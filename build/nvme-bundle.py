#!/usr/bin/env python3
"""Compress a trusted builder NVMe image into a private, verified gzip bundle.

Run with /usr/bin/python3.10 -I -B build/nvme-bundle.py SOURCE_DIRECTORY OUTPUT.
No device writes, mounts, signatures or hardware acceptance. The source manifest
is trusted builder input; its deployment intent and all existing fields survive.
"""
import argparse
import contextlib
import copy
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import sys
import zlib

REPO = Path(__file__).resolve().parents[1]
MIB = 1024**2
CHUNK = 4 * MIB
COMPRESSION_LEVEL = 6
RECIPES = ('build/nvme-bundle.py', 'build/host-state.py', 'build/host-payload.py',
           'build/root-selection.py', 'build/host-initramfs.py', 'build/host-storage-tools.py',
           'configs/host_config.py',
           'scripts/install-nvme.py', 'scripts/plan-nvme.py', 'scripts/nvme_layout.py',
           'configs/builder-packages.lock.tsv')


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, REPO / path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


STATE = module('bundle_state', 'build/host-state.py')
WRITER = module('bundle_writer', 'scripts/install-nvme.py')
require = WRITER.require


def safe_directory(path):
    path = Path(os.path.abspath(path))
    require(path.resolve() == path and path.is_dir(), 'input requires a real directory without symlink ancestors')
    return path


def validate_output(path):
    path = Path(path)
    parent = REPO / 'out'
    require(path.is_absolute() and path.parent == parent and path.name.startswith('nvme-bundle-')
            and path.name != 'nvme-bundle-' and not path.exists() and not path.is_symlink(),
            'output must be a new absolute repo/out/nvme-bundle-NAME')
    require(parent.is_dir() and parent.resolve() == parent, 'output parent must exist without symlink ancestors')
    return path


def validate_manifest(value):
    require(type(value.get('schema')) is int and value['schema'] == 1, 'unsupported manifest schema')
    require(value.get('kernel_release') == '6.6.98-a733-diag', 'unsupported kernel profile')
    record = WRITER.record_valid(value['files']['nvme.img'])
    geometry = WRITER.LAYOUT.layout(record['bytes'], value['logical_sector_bytes'], value['physical_sector_bytes'])
    require(value['layout'] == geometry, 'manifest differs from fixed host layout')
    if 'deployment_intent' in value:
        intent = value['deployment_intent']
        require(isinstance(intent, dict) and type(intent.get('for_installation')) is bool,
                'malformed deployment intent')
        if intent['for_installation']:
            WRITER.validate_manifest(value)
    require('nvme.img.gz' not in value['files'] and 'distribution' not in value,
            'source already contains bundle records')
    return record


def builder_packages():
    selected = {'python3.10-minimal', 'libpython3.10-minimal', 'libpython3.10-stdlib', 'zlib1g'}
    expected = {}
    for line in (REPO / 'configs/builder-packages.lock.tsv').read_text().splitlines():
        name, version, architecture = line.split('\t')
        name = name.split(':')[0]
        if name in selected:
            require(name not in expected, 'ambiguous builder package lock')
            expected[name] = (version, architecture)
    require(set(expected) == selected, 'builder package lock lacks compression dependencies')
    output = subprocess.check_output(
        ['dpkg-query', '-W', '-f=${binary:Package}\t${Version}\t${Architecture}\n',
         *[f'{name}:{expected[name][1]}' for name in sorted(expected)]], text=True)
    actual = {}
    for line in output.splitlines():
        name, version, architecture = line.split('\t')
        name = name.split(':')[0]
        require(name not in actual, 'ambiguous installed builder package')
        actual[name] = (version, architecture)
    require(actual == expected, 'builder compression packages differ from pinned lock')
    return {name: {'version': version, 'architecture': architecture}
            for name, (version, architecture) in sorted(actual.items())}



def loaded_zlib():
    """Identify the actual mapped zlib, rather than resolving a library search."""
    with WRITER.open_regular('/proc/self/maps') as stream:
        data = stream.read(4*MIB + 1)
    require(len(data) <= 4*MIB, 'runtime memory map exceeds bound')
    paths = set()
    for line in data.decode().splitlines():
        fields = line.split(maxsplit=5)
        if len(fields) == 6 and Path(fields[5]).name.startswith('libz.so.'):
            paths.add(Path(fields[5]))
    require(len(paths) == 1, 'ambiguous or absent mapped zlib library')
    path = paths.pop()
    require(path.name == 'libz.so.1.2.11' and path.parent in
            (Path('/usr/lib/x86_64-linux-gnu'), Path('/lib/x86_64-linux-gnu')),
            'mapped zlib library differs from pinned amd64 runtime')
    require(path.resolve() == path, 'mapped zlib library must use canonical path')
    return path


def runtime_evidence(packages):
    """Bind installed package checks to the interpreter and libraries in use."""
    require(sys.flags.isolated and sys.flags.ignore_environment and sys.flags.no_user_site,
            'compression requires isolated Python (-I)')
    require(sys.version_info[:3] == (3, 10, 12), 'compression requires pinned Python 3.10.12')
    executable = Path(sys.executable)
    require(executable.is_absolute() and executable.resolve() == Path('/usr/bin/python3.10'),
            'compression requires /usr/bin/python3.10')
    require(os.access(executable, os.X_OK), 'compression interpreter is not executable')
    require(zlib.ZLIB_VERSION == '1.2.11' and zlib.ZLIB_RUNTIME_VERSION == '1.2.11',
            'compression requires pinned zlib compile/runtime 1.2.11')
    require(zlib.__spec__.origin == 'built-in' and 'zlib' in sys.builtin_module_names
            and not hasattr(zlib, '__file__'), 'zlib must be built into pinned interpreter')
    library = loaded_zlib()
    # Jammy's merged /usr maps the real /usr/lib path; dpkg owns its /lib alias.
    library_package_path = Path('/lib/x86_64-linux-gnu/libz.so.1.2.11')
    require(library_package_path.resolve() == library, 'mapped zlib differs from packaged library')
    files = {
        'executable': (executable.resolve(), executable.resolve(), 'python3.10-minimal'),
        'gzip_module': (Path(gzip.__file__), Path(gzip.__file__), 'libpython3.10-stdlib'),
        'zlib_library': (library, library_package_path, 'zlib1g')}
    require(files['gzip_module'][0] == Path('/usr/lib/python3.10/gzip.py'),
            'gzip module must come from pinned Python standard library')
    result = {'zlib_module': {'origin': 'built-in', 'covered_by': 'executable'}}
    for name, (path, package_path, package) in files.items():
        require(path.resolve() == path, 'runtime file symlink refused')
        ownership = subprocess.check_output(['dpkg-query', '-S', str(package_path)], text=True).splitlines()
        require(len(ownership) == 1 and ': ' in ownership[0], 'ambiguous runtime package ownership')
        owner, owned_path = ownership[0].split(': ', 1)
        require(owner in (package, f"{package}:{packages[package]['architecture']}")
                and owned_path == str(package_path), 'active runtime file has unexpected package owner')
        result[name] = {'path': str(path), 'package_path': str(package_path),
                        'package': owner, **file_record(path)}
    return result


def record_stream(stream, limit):
    """Read all bytes, refusing expansion beyond the caller's explicit bound."""
    digest, size = hashlib.sha256(), 0
    while True:
        data = stream.read(min(CHUNK, limit - size + 1))
        if not data:
            break
        size += len(data)
        require(size <= limit, 'stream exceeds expected size bound')
        digest.update(data)
    return {'bytes': size, 'sha256': digest.hexdigest()}


def file_record(path):
    with WRITER.open_regular(path) as stream:
        return record_stream(stream, os.fstat(stream.fileno()).st_size)


class BoundedOutput:
    def __init__(self, stream, limit):
        self.stream, self.limit, self.size = stream, limit, 0

    def write(self, data):
        require(self.size + len(data) <= self.limit, 'encoded output exceeds free-space budget')
        written = self.stream.write(data)
        self.size += written
        return written

    def flush(self):
        self.stream.flush()


def compress(source, output, expected, encoded_limit):
    source.seek(0)
    with gzip.GzipFile(filename='', mode='wb', compresslevel=COMPRESSION_LEVEL,
                       fileobj=BoundedOutput(output, encoded_limit), mtime=0) as encoded:
        digest, size = hashlib.sha256(), 0
        while True:
            data = source.read(min(CHUNK, expected['bytes'] - size + 1))
            if not data:
                break
            size += len(data)
            require(size <= expected['bytes'], 'source exceeds expected size')
            digest.update(data)
            encoded.write(data)
    require({'bytes': size, 'sha256': digest.hexdigest()} == expected, 'source size or SHA-256 mismatch')
    output.flush()
    os.fsync(output.fileno())


def verify_encoded(stream, expected, encoded_limit):
    stream.seek(0)
    record = record_stream(stream, encoded_limit)
    stream.seek(0)
    with gzip.GzipFile(fileobj=stream, mode='rb') as decoded:
        require(record_stream(decoded, expected['bytes']) == expected, 'decoded size or SHA-256 mismatch')
    return record


def write_private(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def build(source_directory, new_output_directory):
    source_directory = safe_directory(source_directory)
    output = validate_output(new_output_directory)
    require(source_directory != output, 'source and output must differ')
    with contextlib.ExitStack() as stack:
        # Hold both original descriptors throughout; never reopen the raw image.
        manifest_stream = stack.enter_context(WRITER.open_regular(source_directory / 'manifest.json'))
        manifest_identity = WRITER.file_identity(manifest_stream.fileno())
        data, source_manifest = WRITER.read_json(manifest_stream)
        expected = validate_manifest(source_manifest)
        source = stack.enter_context(WRITER.open_regular(source_directory / 'nvme.img'))
        source_identity = WRITER.file_identity(source.fileno())
        require(source_identity['bytes'] == expected['bytes'], 'source size differs from manifest')
        packages = builder_packages()
        runtime = runtime_evidence(packages)
        recipes = {name: file_record(REPO / name) for name in RECIPES}
        storage = STATE.validate_workspace_storage(output.parent)
        # Tiny fixtures may need less; normal images require at least 2 GiB free.
        free = shutil.disk_usage(output.parent).free
        maximum_encoded = expected['bytes'] + expected['bytes'] // 1000 + MIB
        require(free >= min(2 * 1024**3, maximum_encoded + 64*MIB), 'insufficient output free space')
        encoded_limit = min(maximum_encoded, free - 64*MIB)
        output.mkdir(mode=0o700)
        try:
            fd = os.open(output / 'nvme.img.gz', os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'w+b') as encoded:
                compress(source, encoded, expected, encoded_limit)
                require(WRITER.file_identity(source.fileno()) == source_identity, 'source image changed while held')
                encoded_record = verify_encoded(encoded, expected, encoded_limit)
            require(WRITER.file_identity(source.fileno()) == source_identity, 'source image changed while held')
            require(WRITER.file_identity(manifest_stream.fileno()) == manifest_identity, 'source manifest changed while held')
            require(recipes == {name: file_record(REPO / name) for name in RECIPES}, 'recipe changed during bundle build')
            require(runtime == runtime_evidence(packages), 'compression runtime changed during bundle build')
            result = copy.deepcopy(source_manifest)
            result['files']['nvme.img.gz'] = encoded_record
            result['distribution'] = {
                'format': 'gzip', 'source_manifest': {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()},
                'source_image': {'path': str(source_directory / 'nvme.img'), 'identity': source_identity, 'record': expected},
                'compression': {'level': COMPRESSION_LEVEL, 'mtime': 0, 'filename': '',
                                'implementation': 'Python gzip.GzipFile', 'python_implementation': platform.python_implementation(),
                                'python_version': platform.python_version(), 'zlib_compile_version': zlib.ZLIB_VERSION,
                                'zlib_runtime_version': zlib.ZLIB_RUNTIME_VERSION},
                'builder_packages': packages, 'runtime_files': runtime, 'recipes': recipes, 'workspace_storage': storage,
                'encoded_size_limit': encoded_limit,
                'verification': 'all logical source bytes hashed; fsynced encoded bytes hashed on readback; full bounded gzip expansion hashed',
                'trust': 'source manifest is trusted builder input; no signature or hardware acceptance implied',
                'staging_storage_guard_limit': 'kernel sdX/removable=0 cannot identify every USB SD reader; operator must use known fixed builder storage'}
            final = (json.dumps(result, indent=2, ensure_ascii=True, allow_nan=False) + '\n').encode()
            write_private(output / '.manifest.partial', final)
            write_private(output / 'manifest.sha256', (hashlib.sha256(final).hexdigest() + '  manifest.json\n').encode())
            os.rename(output / '.manifest.partial', output / 'manifest.json')
            directory_fd = os.open(output, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except BaseException:
            # Retain private partial output for diagnosis, without a success manifest.
            (output / 'manifest.json').unlink(missing_ok=True)
            raise
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source_image_directory')
    parser.add_argument('new_output_directory')
    args = parser.parse_args()
    try:
        result = build(args.source_image_directory, args.new_output_directory)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, EOFError) as error:
        parser.exit(1, f'NVMe bundle: {error}\n')
    print(json.dumps(result['files'], sort_keys=True))
