#!/usr/bin/env python3
"""Repackage pinned EL2 diagnostic with the kernel's /sbin/modprobe helper.

Usage: network-diagnostic.py BASE_EL2_ARTIFACTS STATIC_AARCH64_BUSYBOX NEW_OUTPUT
No downloads, compilation, physical device access or hardware acceptance.
"""
import argparse
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import stat
import subprocess
import tarfile
import tempfile

REPO = Path(__file__).resolve().parents[1]
EPOCH = 1793750400
CORE = ('Image', 'uImage', 'kernel.config', 'board.dtb',
        'boot0_sdcard.fex', 'boot_package.fex')
CLASSIFICATION = 'network module autoload candidate; hardware UNTESTED'


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, REPO / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HOST = load('network_host', 'build/assemble-host.py')
STATE = load('network_state', 'build/host-state.py')
PAYLOAD = load('network_payload', 'build/host-payload.py')


def safe_path(value):
    path = Path(value)
    if (not path.is_absolute() or '..' in path.parts
            or path.parts[1:2] in (('dev',), ('proc',), ('sys',))):
        raise ValueError('absolute non-device paths without traversal required')
    return HOST.safe_path(path)


def validate_output(value):
    output = safe_path(value)
    if (output.parent != REPO / 'out' or not output.parent.is_dir()
            or not re.fullmatch(r'network-diagnostic-[A-Za-z0-9][A-Za-z0-9._-]*', output.name)
            or os.path.lexists(output)):
        raise ValueError('output must be a new absolute repo/out/network-diagnostic-NAME')
    return output


def read(path, expected=None, maximum=128 * 1024**2):
    return HOST.read_regular(safe_path(path), maximum, expected)


def authenticate_base(base):
    """Trust the repository evidence, never a caller-selected replacement pin."""
    base = safe_path(base)
    if not base.is_dir():
        raise ValueError('base artifacts directory required')
    evidence = HOST.json_object(read(REPO / 'docs/evidence/el2-diagnostic-20261104.json'))
    raw = read(base / 'artifact-manifest.json', maximum=8 * 1024**2)
    if hashlib.sha256(raw).hexdigest() != evidence['full_manifest_sha256']:
        raise ValueError('untrusted EL2 artifact manifest')
    manifest = HOST.json_object(raw)
    records = manifest['files']
    for name in (*CORE, 'uInitrd'):
        if records[name] != evidence['files'][name]:
            raise ValueError(f'base evidence disagrees: {name}')
        read(base / name, records[name])
    initrd = read(base / 'initramfs.cpio.gz', records['initramfs.cpio.gz'])
    if 'CONFIG_MODPROBE_PATH="/sbin/modprobe"' not in read(base / 'kernel.config').decode():
        raise ValueError('unexpected kernel module helper path')
    # Validate every retained license/source input against the authenticated manifest.
    for directory in ('notices', 'busybox-source'):
        source = safe_path(base / directory)
        if not source.is_dir():
            raise ValueError(f'missing {directory}')
        for item in source.rglob('*'):
            safe_path(item)
            if stat.S_ISDIR(item.lstat().st_mode):
                continue
            relative = item.relative_to(base).as_posix()
            read(item, records[relative])
        expected = {name for name in records if name.startswith(directory + '/')}
        actual = {p.relative_to(base).as_posix() for p in source.rglob('*') if p.is_file()}
        if expected != actual:
            raise ValueError(f'incomplete retained {directory}')
    return base, manifest, raw, initrd


def authenticate_busybox(path):
    lock = HOST.json_object(read(REPO / 'configs/diagnostic-sources.lock.json'))
    data = read(path)
    if hashlib.sha256(data).hexdigest() != lock['busybox']['binary_sha256']:
        raise ValueError('BusyBox binary differs from fixed source lock')
    return data, lock


def validate_builder():
    release = platform.freedesktop_os_release()
    if (release.get('ID') != 'ubuntu' or release.get('VERSION_ID') != '22.04'
            or platform.machine() != 'x86_64'):
        raise ValueError('requires Ubuntu 22.04 amd64 builder')
    lock = REPO / 'configs/builder-packages.lock.tsv'
    packages = HOST.validate_builder_packages(lock)
    selected = {'cpio', 'gzip', 'findutils', 'binutils', 'binutils-x86-64-linux-gnu', 'tar'}
    expected = {}
    for line in read(lock).decode().splitlines():
        name, version, arch = line.split('\t')
        if name in selected:
            expected[name] = {'version': version, 'architecture': arch}
    if set(expected) != selected:
        raise ValueError('incomplete native packaging tool lock')
    output = subprocess.check_output(
        ['dpkg-query', '-W', '-f=${binary:Package}\t${Version}\t${Architecture}\n',
         *sorted(selected)], text=True)
    actual = {}
    for line in output.splitlines():
        name, version, arch = line.split('\t')
        actual[name] = {'version': version, 'architecture': arch}
    if actual != expected:
        raise ValueError('native packaging tools differ from lock')
    return dict(packages, **actual)


def stage_modules(initrd, destination):
    entries = PAYLOAD.module_entries(gzip.decompress(initrd))
    destination.mkdir(mode=0o700)
    for path, (mode, content) in sorted(entries.items(), key=lambda item: len(item[0].parts)):
        target = destination.joinpath(*path.parts[2:])
        if stat.S_ISDIR(mode):
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as stream:
                stream.write(content)
        target.chmod(stat.S_IMODE(mode))
    return entries


def validate_repacked_initrd(data, old_modules, busybox):
    """Check the complete newc grammar before inspecting userspace entries."""
    decoded = gzip.decompress(data)
    if PAYLOAD.module_entries(decoded) != old_modules:
        raise ValueError('kernel modules changed during repackaging')
    selected = {}
    offset = 0
    while offset + 110 <= len(decoded):
        fields = [int(decoded[n:n + 8], 16) for n in range(offset + 6, offset + 110, 8)]
        mode, size, namesize = fields[1], fields[6], fields[11]
        offset += 110
        name = decoded[offset:offset + namesize - 1].decode()
        offset = (offset + namesize + 3) & ~3
        content = decoded[offset:offset + size]
        offset = (offset + size + 3) & ~3
        if name == 'TRAILER!!!':
            break
        selected[PurePosixPath(name)] = (mode, content)
    helper = selected.get(PurePosixPath('sbin/modprobe'))
    binary = selected.get(PurePosixPath('bin/busybox'))
    if (helper is None or not stat.S_ISLNK(helper[0]) or helper[1] != b'../bin/busybox'
            or binary is None or not stat.S_ISREG(binary[0]) or binary[1] != busybox
            or not binary[0] & 0o111):
        raise ValueError('repacked initramfs lacks the pinned /sbin/modprobe BusyBox helper')


def recipe_archive(destination):
    # Retain runtime helpers and repository evidence pins as well as recipes.
    with destination.open('xb') as stream, gzip.GzipFile(fileobj=stream, mode='wb', mtime=0, filename='') as compressed:
        with tarfile.open(fileobj=compressed, mode='w|') as archive:
            for root in ('build', 'configs', 'licenses', 'scripts', 'docs/evidence'):
                for path in sorted((REPO / root).rglob('*')):
                    if '__pycache__' in path.parts or path.suffix == '.pyc':
                        continue
                    safe_path(path)
                    info = archive.gettarinfo(str(path), arcname=path.relative_to(REPO).as_posix())
                    if not (info.isfile() or info.isdir()):
                        raise ValueError(f'nonregular recipe source: {path}')
                    info.uid = info.gid = 0
                    info.uname = info.gname = ''
                    info.mtime = EPOCH
                    if info.isfile():
                        archive.addfile(info, io.BytesIO(read(path)))
                    else:
                        archive.addfile(info)


def build(base_value, busybox_value, output_value):
    output = validate_output(output_value)
    base, old_manifest, old_raw, initrd = authenticate_base(base_value)
    busybox, lock = authenticate_busybox(safe_path(busybox_value))
    if base == output or base in output.parents or output in base.parents:
        raise ValueError('output overlaps base artifacts')
    packages = validate_builder()
    storage = STATE.validate_workspace_storage(output.parent)
    output.mkdir(mode=0o700)
    env = dict(os.environ, LC_ALL='C', TZ='UTC', SOURCE_DATE_EPOCH=str(EPOCH),
               TMPDIR=str(output))
    for name in CORE:
        (output / name).write_bytes(read(base / name, old_manifest['files'][name]))
    (output / 'base-el2-artifact-manifest.json').write_bytes(old_raw)
    (output / 'base-el2-evidence.json').write_bytes(read(REPO / 'docs/evidence/el2-diagnostic-20261104.json'))
    for directory in ('notices', 'busybox-source'):
        (output / directory).mkdir()
        for source in sorted((base / directory).rglob('*')):
            destination = output / source.relative_to(base)
            safe_path(source)
            if stat.S_ISDIR(source.lstat().st_mode):
                destination.mkdir(parents=True, exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(read(source, old_manifest['files'][source.relative_to(base).as_posix()]))
    with tempfile.TemporaryDirectory(prefix='.network-', dir=output) as temporary:
        work = Path(temporary)
        modules = work / 'modules'
        old_modules = stage_modules(initrd, modules)
        (work / 'busybox').write_bytes(busybox)
        (work / 'busybox').chmod(0o755)
        subprocess.run(['bash', str(REPO / 'build/diagnostic-initramfs.sh'),
                        str(work / 'busybox'), str(output / 'initramfs.cpio.gz'), str(modules)],
                       check=True, env=env)
        validate_repacked_initrd(read(output / 'initramfs.cpio.gz'), old_modules, busybox)
    subprocess.run(['mkimage', '-A', 'arm', '-O', 'linux', '-T', 'ramdisk', '-C', 'gzip',
                    '-n', 'uInitrd', '-d', str(output / 'initramfs.cpio.gz'), str(output / 'uInitrd')],
                   check=True, env=env)
    recipe_archive(output / 'build-recipe.tar.gz')
    subprocess.run(['bash', str(REPO / 'build/assemble-diagnostic.sh'), str(output),
                    str(output / 'a733-diagnostic.img')], check=True, env=env)
    with (output / 'a733-diagnostic.img.gz').open('xb') as stream:
        subprocess.run(['gzip', '-n', '-c', str(output / 'a733-diagnostic.img')],
                       stdout=stream, check=True, env=env)
    retained = {str(p.relative_to(output)): HOST.file_record(p)
                for directory in ('notices', 'busybox-source')
                for p in sorted((output / directory).rglob('*')) if p.is_file()}
    (output / 'retained-source-manifest.json').write_text(json.dumps(retained, indent=2) + '\n')
    files = {p.name: HOST.file_record(p) for p in sorted(output.iterdir()) if p.is_file()}
    preserved = all(files[name] == old_manifest['files'][name] for name in CORE)
    if not preserved:
        raise ValueError('pinned boot inputs changed')
    manifest = {'schema': 1, 'board': 'Orange Pi 4 Pro A733',
                'classification': CLASSIFICATION, 'hardware_acceptance': 'UNTESTED; actual KVM guest required',
                'change': '/sbin/modprobe -> ../bin/busybox enables the configured kernel helper; PHY driver and DHCP remain untested',
                'source_date_epoch': EPOCH, 'source': lock, 'builder_packages': packages,
                'workspace_storage': storage, 'base_initramfs': old_manifest['files']['initramfs.cpio.gz'],
                'preserved_core_files': list(CORE), 'preserved_core_hashes': preserved,
                'files': files}
    (output / 'artifact-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    sums = dict(files, **{'artifact-manifest.json': HOST.file_record(output / 'artifact-manifest.json')})
    (output / 'SHA256SUMS').write_text(''.join(f"{record['sha256']}  {name}\n" for name, record in sorted(sums.items())))
    subprocess.run(['python3', str(REPO / 'scripts/verify-diagnostic.py'), str(output)], check=True, env=env)
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('base_el2_artifacts')
    parser.add_argument('static_aarch64_busybox')
    parser.add_argument('new_output')
    args = parser.parse_args()
    try:
        build(args.base_el2_artifacts, args.static_aarch64_busybox, args.new_output)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, tarfile.TarError) as error:
        parser.exit(1, f'Network diagnostic build refused: {error}\n')
