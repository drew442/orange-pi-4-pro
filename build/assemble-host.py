#!/usr/bin/env python3
"""Package a regular-file A733 NVMe host SD bootstrap prototype, never a device."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile

REPO = Path(__file__).resolve().parents[1]
DIAGNOSTIC_RECORD = REPO / 'docs/evidence/diagnostic-20261104.json'
KERNEL_RELEASE = '6.6.98-a733-diag'
EPOCH = '1793750400'
MAX_INITRAMFS = 64 * 1024**2 - 65  # Include the legacy 64-byte wrapper in budget.
BLOBS = ('boot0_sdcard.fex', 'boot_package.fex', 'uImage', 'Image', 'board.dtb', 'kernel.config')
ENTRIES = {'init': 'configs/host-init',
           'etc/host-storage-resolver.sh': 'configs/host-storage-resolver.sh'}
RECIPES = ('build/assemble-host.py', 'build/assemble-diagnostic.sh', 'configs/host-boot.cmd',
           'build/host-initramfs.py', 'configs/host-init', 'configs/host-storage-resolver.sh',
           'configs/diagnostic-sources.lock.json', 'configs/builder-packages.lock.tsv',
           'docs/evidence/diagnostic-20261104.json', 'configs/package_state.py')


def safe_path(path):
    """Reject symlink ancestors and special-file ancestors before opening inputs."""
    path = Path(os.path.abspath(path))
    for part in reversed((path, *path.parents)):
        try:
            mode = part.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise ValueError(f'symlink path refused: {part}')
        if part != path and not stat.S_ISDIR(mode):
            raise ValueError(f'non-directory ancestor: {part}')
    if path == Path('/dev') or Path('/dev') in path.parents:
        raise ValueError('device paths refused')
    return path


def record(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def read_regular(path, maximum, expected=None):
    path = safe_path(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f'not a regular file: {path}')
        if not 0 < info.st_size <= maximum:
            raise ValueError(f'input size outside budget: {path}')
    except BaseException:
        os.close(descriptor)
        raise
    with os.fdopen(descriptor, 'rb') as stream:
        data = stream.read(maximum + 1)
    if not 0 < len(data) <= maximum or (expected is not None and record(data) != expected):
        raise ValueError(f'input hash/size mismatch: {path}')
    return data


def json_object(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f'duplicate manifest field: {key}')
            result[key] = value
        return result
    value = json.loads(data, object_pairs_hook=pairs)
    if not isinstance(value, dict):
        raise ValueError('manifest must be an object')
    return value


def validate_manifest(manifest, diagnostic):
    if type(manifest.get('schema')) is not int or manifest['schema'] != 1:
        raise ValueError('host manifest must use schema 1')
    state_uuid = manifest.get('state_uuid', '')
    if (not isinstance(state_uuid, str) or
            not re.fullmatch(r'[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}', state_uuid) or
            state_uuid == '00000000-0000-0000-0000-000000000000'):
        raise ValueError('state UUID must be canonical lowercase and nonzero')
    if manifest.get('package_state_profile') != 'a733-package-state-v1':
        raise ValueError('host bootstrap lacks required package-state ABI')
    if manifest.get('kernel_release') != KERNEL_RELEASE:
        raise ValueError('incompatible host kernel ABI')
    for name in ('Image', 'board.dtb', 'kernel.config'):
        if manifest.get('boot_inputs', {}).get(name) != diagnostic[name]:
            raise ValueError(f'incompatible host boot input: {name}')
    for name, source in ENTRIES.items():
        expected = record(read_regular(REPO / source, 1024**2))
        actual = manifest.get('entries', {}).get(name, {})
        if any(actual.get(field) != value for field, value in expected.items()):
            raise ValueError(f'missing or changed current repository initramfs entry: {name}')
    item = manifest.get('files', {}).get('initramfs.cpio.gz', {})
    if (type(item.get('bytes')) is not int or not 0 < item['bytes'] <= MAX_INITRAMFS or
            not isinstance(item.get('sha256'), str) or
            not re.fullmatch('[0-9a-f]{64}', item['sha256'])):
        raise ValueError('invalid initramfs hash/size record')
    return item


def validate_builder_packages(lock_path):
    """Require the selected Jammy packaging tool versions and architectures."""
    selected = {'u-boot-tools', 'e2fsprogs', 'util-linux', 'coreutils', 'bash',
                'python3.10-minimal'}
    optional = {'libpython3.10-stdlib'}
    expected = {}
    for line in read_regular(lock_path, 1024**2).decode().splitlines():
        name, version, architecture = line.split('\t')
        package = name.split(':', 1)[0]
        if package in selected | optional:
            if package in expected:
                raise ValueError(f'ambiguous builder package lock: {package}')
            expected[package] = (version, architecture)
    if not selected <= set(expected):
        raise ValueError('builder package lock lacks required tool packages')
    queries = [f'{name}:{expected[name][1]}' for name in sorted(expected)]
    output = subprocess.check_output(
        ['dpkg-query', '-W', '-f=${binary:Package}\t${Version}\t${Architecture}\n',
         *queries], text=True)
    actual = {}
    for line in output.splitlines():
        name, version, architecture = line.split('\t')
        package = name.split(':', 1)[0]
        if package in actual:
            raise ValueError(f'ambiguous installed builder package: {package}')
        actual[package] = (version, architecture)
    if actual != expected:
        raise ValueError(f'builder tool package versions/architectures differ from lock: {actual!r}')
    return {name: {'version': version, 'architecture': architecture}
            for name, (version, architecture) in sorted(actual.items())}


def file_record(path):
    checksum = hashlib.sha256()
    size = 0
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024**2), b''):
            size += len(chunk)
            checksum.update(chunk)
    return {'bytes': size, 'sha256': checksum.hexdigest()}


def build(diagnostic_directory, host_directory, output_directory):
    raw_output = Path(output_directory)
    output = safe_path(raw_output)
    if (not raw_output.is_absolute() or raw_output != output or
            output.parent != REPO / 'out' or not output.name.startswith('host-sd-') or
            output.name == 'host-sd-'):
        raise ValueError('output must be a new absolute repo/out/host-sd-NAME directory')
    if output.exists():
        raise ValueError('existing output refused')
    diagnostic_directory = safe_path(diagnostic_directory)
    host_directory = safe_path(host_directory)
    for directory in (diagnostic_directory, host_directory):
        if not directory.is_dir():
            raise ValueError(f'input must be a directory: {directory}')
    pinned = json_object(read_regular(DIAGNOSTIC_RECORD, 1024**2))['files']
    manifest_data = read_regular(host_directory / 'manifest.json', 4 * 1024**2)
    host = json_object(manifest_data)
    initramfs_record = validate_manifest(host, pinned)
    recipe_data = {name: read_regular(REPO / name, 4 * 1024**2) for name in RECIPES}
    recipes = {name: record(data) for name, data in recipe_data.items()}
    builder_packages = validate_builder_packages(REPO / 'configs/builder-packages.lock.tsv')
    output.parent.mkdir(exist_ok=True)
    filesystem = subprocess.check_output(['stat', '-f', '-c', '%T', str(output.parent)], text=True).strip()
    if filesystem in ('tmpfs', 'ramfs', 'devtmpfs'):
        raise ValueError('staging requires a disk-backed filesystem, not RAM')
    output.mkdir(mode=0o700)  # Atomic reservation; never replace an existing output.
    with tempfile.TemporaryDirectory(prefix='.staging-', dir=output) as temporary:
        stage = Path(temporary)
        inputs = stage / 'inputs'
        inputs.mkdir(mode=0o700)
        sources = {}
        for name in BLOBS:
            data = read_regular(diagnostic_directory / name, pinned[name]['bytes'], pinned[name])
            (inputs / name).write_bytes(data)
            sources[name] = {'path': str(diagnostic_directory / name), **record(data)}
        data = read_regular(host_directory / 'initramfs.cpio.gz', MAX_INITRAMFS, initramfs_record)
        (inputs / 'initramfs.cpio.gz').write_bytes(data)
        command = recipe_data['configs/host-boot.cmd']
        (inputs / 'boot.cmd').write_bytes(command)
        environment = dict(os.environ, SOURCE_DATE_EPOCH=EPOCH, TMPDIR=str(stage))
        with (output / 'assembly.log').open('xb') as log:
            subprocess.run(['mkimage', '-A', 'arm', '-O', 'linux', '-T', 'ramdisk',
                            '-C', 'gzip', '-d', str(inputs / 'initramfs.cpio.gz'),
                            str(inputs / 'uInitrd')], env=environment, stdout=log,
                           stderr=subprocess.STDOUT, check=True)
            subprocess.run(['bash', str(REPO / 'build/assemble-diagnostic.sh'), str(inputs),
                            str(output / 'a733-host-sd.img'), str(inputs / 'boot.cmd')],
                           env=environment, stdout=log, stderr=subprocess.STDOUT, check=True)
        shutil.copyfile(inputs / 'uInitrd', output / 'uInitrd')
        image = output / 'a733-host-sd.img'
        if image.stat().st_size != 256 * 1024**2:
            raise ValueError('assembler returned incorrect image size')
        with image.open('rb') as source, (output / 'a733-host-sd.img.gz').open('xb') as destination:
            with gzip.GzipFile(filename='', fileobj=destination, mode='wb', compresslevel=9, mtime=0) as compressed:
                shutil.copyfileobj(source, compressed)
        result = {'schema': 1, 'board': 'Orange Pi 4 Pro A733',
                  'classification': 'NVMe host SD bootstrap prototype; no physical boot or KVM acceptance',
                  'state_uuid': host['state_uuid'], 'kernel_release': KERNEL_RELEASE,
                  'package_state_profile': host['package_state_profile'],
                  'root_selection': 'NVMe state boot/selected.conf; root A/B not embedded',
                  'source_date_epoch': int(EPOCH), 'boot_inputs': {name: pinned[name] for name in BLOBS},
                  'sources': sources,
                  'host_initramfs': {'path': str(host_directory / 'initramfs.cpio.gz'), **record(data)},
                  'trusted_host_manifest': {'path': str(host_directory / 'manifest.json'), **record(manifest_data)},
                  'source_bom': json_object(recipe_data['configs/diagnostic-sources.lock.json']),
                  'builder_packages': builder_packages,
                  'recipes': recipes,
                  'boot_command': record(command),
                  'compatible_immutable_boot_stack': 'Pinned diagnostic boot0/package/uImage/DTB; legacy arm wrappers and vendor load protocol',
                  'files': {name: file_record(output / name) for name in
                            ('a733-host-sd.img', 'a733-host-sd.img.gz', 'uInitrd', 'assembly.log')}}
        (output / 'manifest.json').write_text(json.dumps(result, indent=2) + '\n')
        return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('diagnostic_directory', 'host_initramfs_directory', 'new_output_directory'):
        parser.add_argument(name)
    args = parser.parse_args()
    try:
        result = build(args.diagnostic_directory, args.host_initramfs_directory, args.new_output_directory)
    except (ValueError, OSError, KeyError, TypeError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'host SD packaging: {error}\n')
    print(json.dumps(result, indent=2))
