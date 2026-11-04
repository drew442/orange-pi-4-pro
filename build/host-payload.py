#!/usr/bin/env python3
"""Package authenticated interim userspace into immutable regular-file A/B payloads.

No installer, mounts, network access, chroot execution or hardware acceptance.
Run as root: host-payload.py ROOTFS_TAR DIAGNOSTIC_ARTIFACT_DIR NEW_OUTPUT_DIR
"""
import argparse
import gzip
import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath
import shutil
import stat
import struct
import subprocess
import tarfile
import tempfile

REPO = Path(__file__).resolve().parent.parent
SLOT_BYTES = 4 * 1024**3
KERNEL_RELEASE = '6.6.98-a733-diag'
SLOT_UUIDS = {'A': 'a7330000-0000-4000-8000-00000000000a',
              'B': 'a7330000-0000-4000-8000-00000000000b'}
CLASSIFICATION = 'managed host root prototype; installation and hardware acceptance pending'
BOOT_FILES = ('Image', 'board.dtb', 'kernel.config', 'initramfs.cpio.gz')
MANAGEMENT_FILES = {
    'configs/host-management.py': ('usr/lib/orangepi-hypervisor/host-management.py', 0o755),
    'configs/host_config.py': ('usr/lib/orangepi-hypervisor/host_config.py', 0o644),
    'configs/a733-management-ssh.service': ('usr/lib/systemd/system/a733-management-ssh.service', 0o644),
}


def file_record(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(data)
    return {'bytes': Path(path).stat().st_size, 'sha256': digest.hexdigest()}


def require_regular(path):
    path = Path(path)
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError(f'not a regular non-symlink input: {path}')
    return path


def authenticate(path, expected):
    require_regular(path)
    if file_record(path) != expected:
        raise ValueError(f'input hash or size mismatch: {path}')


def archive_path(name):
    if not name or '\x00' in name or name.startswith('/'):
        raise ValueError(f'unsafe archive path: {name!r}')
    path = PurePosixPath(name)
    if '..' in path.parts:
        raise ValueError(f'archive traversal: {name!r}')
    return path


def validate_tar(members):
    """Check complete namespace before extracting anything (including late links)."""
    entries = {}
    for member in members:
        path = archive_path(member.name)
        if path in entries:
            raise ValueError(f'duplicate tar path: {path}')
        entries[path] = member
        if path == PurePosixPath('.') and not member.isdir():
            raise ValueError('archive root must be a directory')
        if not (member.isdir() or member.isfile() or member.issym() or
                member.islnk() or member.ischr() or member.isfifo()):
            raise ValueError(f'unsupported tar entry: {path}')
    for path, member in entries.items():
        for ancestor in path.parents:
            if ancestor in entries and not entries[ancestor].isdir():
                raise ValueError(f'non-directory tar ancestor: {path}')
        if member.issym():
            # Absolute links are rooted at the future filesystem root. Relative
            # links may not walk above it, even when they are leaf entries.
            depth = len(path.parent.parts) if not member.linkname.startswith('/') else 0
            for component in PurePosixPath(member.linkname).parts:
                if component == '..':
                    depth -= 1
                    if depth < 0:
                        raise ValueError(f'escaping tar symlink: {path}')
                elif component not in ('/', '.'):
                    depth += 1
        if member.islnk():
            target = archive_path(member.linkname)
            if target not in entries or not entries[target].isfile():
                raise ValueError(f'unsafe tar hardlink: {path}')
    return entries


def extract_rootfs(archive, root):
    with tarfile.open(archive, 'r:*') as source:
        members = source.getmembers()
        validate_tar(members)
    # GNU tar preserves the --xattrs/--acls metadata emitted by the userspace
    # recipe; Python tarfile does not restore these PAX fields. Namespace was
    # checked in full above, including links declared after their descendants.
    subprocess.run(['tar', '--extract', '--file', str(archive), '--directory', str(root),
                    '--numeric-owner', '--same-owner', '--same-permissions',
                    '--xattrs', '--xattrs-include=*', '--acls'], check=True)


def module_entries(data):
    """Parse gzip-decoded newc; return ONLY regular modules and directories."""
    offset = 0
    modules = {}
    seen = set()
    trailer = False
    while offset + 110 <= len(data):
        header = data[offset:offset + 110]
        if header[:6] != b'070701':
            raise ValueError('expected newc archive')
        try:
            values = [int(header[n:n + 8], 16) for n in range(6, 110, 8)]
        except ValueError as error:
            raise ValueError('invalid newc header') from error
        mode, nlink, size, namesize = values[1], values[4], values[6], values[11]
        offset += 110
        if namesize < 1 or offset + namesize > len(data):
            raise ValueError('truncated newc name')
        raw_name = data[offset:offset + namesize]
        if raw_name[-1:] != b'\0' or b'\0' in raw_name[:-1]:
            raise ValueError('invalid newc name')
        name = raw_name[:-1].decode('utf-8')
        offset = (offset + namesize + 3) & ~3
        if offset + size > len(data):
            raise ValueError('truncated newc content')
        content = data[offset:offset + size]
        offset = (offset + size + 3) & ~3
        if name == 'TRAILER!!!':
            if size or any(data[offset:]):
                raise ValueError('unexpected newc trailer data')
            trailer = True
            break
        path = archive_path(name)
        if path in seen:
            raise ValueError(f'duplicate newc entry: {path}')
        seen.add(path)
        if path.parts[:2] == ('lib', 'modules'):
            if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                raise ValueError(f'non-regular module archive entry: {path}')
            if stat.S_ISREG(mode) and nlink != 1:
                raise ValueError(f'hardlinked module: {path}')
            if stat.S_ISDIR(mode) and size:
                raise ValueError(f'directory module content: {path}')
            modules[path] = (mode, content)
    if not trailer:
        raise ValueError('missing newc trailer')
    releases = {p.parts[2] for p in modules if len(p.parts) > 2}
    if releases != {KERNEL_RELEASE} or not any(
            p.name.endswith('.ko') and stat.S_ISREG(v[0]) for p, v in modules.items()):
        raise ValueError('missing modules or incompatible kernel module ABI')
    for path in modules:
        for parent in path.parents:
            if parent in modules and not stat.S_ISDIR(modules[parent][0]):
                raise ValueError('non-directory module ancestor')
    return modules


def within_root(root, relative):
    target = root.joinpath(*PurePosixPath(relative).parts).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError(f'staging symlink escapes root: {relative}')
    return target


def import_modules(root, entries):
    destination = within_root(root, 'lib/modules')
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('rootfs already contains modules')
    records = {}
    for path, (mode, content) in sorted(entries.items(), key=lambda item: len(item[0].parts)):
        target = within_root(root, str(path))
        if stat.S_ISDIR(mode):
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as stream:
                stream.write(content)
            records[str(path)] = {'bytes': len(content),
                                  'sha256': hashlib.sha256(content).hexdigest()}
        target.chmod(stat.S_IMODE(mode))
    return records


def validate_kernel(config):
    settings = dict(line.split('=', 1) for line in config.splitlines()
                    if line.startswith('CONFIG_') and '=' in line)
    if settings.get('CONFIG_EXT4_FS') != 'y':
        raise ValueError('CONFIG_EXT4_FS=y required')
    if not re.search(r'^# Linux/arm64 6\.6\.98(?:[- ]).*Kernel Configuration$', config, re.M):
        raise ValueError('incompatible kernel version')


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def install_management(root):
    records = {}
    for source, (destination, mode) in MANAGEMENT_FILES.items():
        target = within_root(root, destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            raise ValueError(f'management destination already exists: {destination}')
        shutil.copyfile(REPO / source, target)
        target.chmod(mode)
        records[destination] = file_record(target)
    return {'profile': 'a733-management-v1', 'files': records}


def make_image(root, output, slot, size=SLOT_BYTES):
    if output.is_symlink():
        raise ValueError('symlink image output refused')
    # Exclusive regular file creation; mke2fs never receives a device argument.
    with output.open('xb') as stream:
        stream.truncate(size)
    subprocess.run(['mke2fs', '-q', '-t', 'ext4', '-F', '-b', '4096',
                    '-O', '^has_journal', '-E', 'lazy_itable_init=0',
                    '-m', '0', '-U', SLOT_UUIDS[slot], '-L', f'a733-root-{slot}',
                    '-d', str(root), str(output)], check=True)
    # mke2fs's supported creation features exclude READONLY (e2fsprogs
    # v1.47.0 misc/mke2fs.c:1115); tune2fs sets it with valid checksums.
    subprocess.run(['tune2fs', '-O', 'read-only', str(output)], check=True)
    # Inspect disk superblock directly, independently of human-readable tool text.
    with output.open('rb') as stream:
        stream.seek(1024)
        superblock = stream.read(1024)
    compat, ro_compat = struct.unpack_from('<I', superblock, 92)[0], struct.unpack_from('<I', superblock, 100)[0]
    if compat & 4 or not ro_compat & 0x1000 or output.stat().st_size != size:
        raise ValueError('filesystem lacks immutable no-journal format or size differs')
    subprocess.run(['e2fsck', '-fn', str(output)], check=True)


def validate_output(output):
    output = Path(output)
    parent = REPO / 'out'
    if (not output.is_absolute() or output.parent != parent or
            not output.name.startswith('host-payload-') or
            output.name == 'host-payload-' or output.exists() or output.is_symlink()):
        raise ValueError('output must be a new absolute repo/out/host-payload-NAME')
    if parent.is_symlink() or parent.resolve() != parent:
        raise ValueError('symlink output parent refused')
    return output


def tool_version(tool):
    result = subprocess.run([tool, '-V'], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, check=False)
    # tune2fs prints its version followed by usage and exits nonzero for -V.
    return result.stdout.strip()


def validate_builder_packages(lock_path):
    """Require the pinned package versions for archive and filesystem tools."""
    selected = {'e2fsprogs', 'libext2fs2', 'tar', 'gzip'}
    expected = {}
    for line in Path(lock_path).read_text().splitlines():
        name, version, architecture = line.split('\t')
        package = name.split(':', 1)[0]
        if package in selected:
            if package in expected:
                raise ValueError(f'ambiguous builder package lock: {package}')
            expected[package] = (version, architecture)
    if set(expected) != selected:
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


def build(rootfs, diagnostic, output):
    if os.geteuid() != 0:
        raise ValueError('root required to preserve rootfs ownership and device nodes')
    for tool in ('mke2fs', 'tune2fs', 'e2fsck', 'tar'):
        if not shutil.which(tool):
            raise ValueError(f'required tool missing: {tool}')
    builder_packages = validate_builder_packages(REPO / 'configs/builder-packages.lock.tsv')
    output = validate_output(output)
    rootfs = require_regular(rootfs)
    diagnostic = Path(diagnostic)
    if not diagnostic.is_dir() or diagnostic.is_symlink():
        raise ValueError('diagnostic input must be a real directory')
    user_evidence = REPO / 'docs/evidence/host-userspace-20261104.json'
    diag_evidence = REPO / 'docs/evidence/diagnostic-20261104.json'
    user_lock = json.loads(user_evidence.read_text())['files']
    diag_lock = json.loads(diag_evidence.read_text())['files']
    authenticate(rootfs, user_lock['rootfs.tar.gz'])
    for name in BOOT_FILES:
        authenticate(diagnostic / name, diag_lock[name])
    validate_kernel((diagnostic / 'kernel.config').read_text())
    with gzip.open(diagnostic / 'initramfs.cpio.gz', 'rb') as stream:
        modules = module_entries(stream.read())
    with tarfile.open(rootfs) as source:
        members = source.getmembers()
        validate_tar(members)
        unpacked = sum(m.size for m in members if m.isfile())
    if unpacked > SLOT_BYTES * 3 // 4:
        raise ValueError('rootfs exceeds conservative slot budget')
    # Two full images, worst-case compressed copies, staging and safety reserve.
    required = 4 * SLOT_BYTES + unpacked + 1024**3
    output.parent.mkdir(exist_ok=True)
    if shutil.disk_usage(output.parent).free < required:
        raise ValueError(f'insufficient free space: require {required} bytes')
    output.mkdir(mode=0o700)
    sources = {'rootfs.tar.gz': user_lock['rootfs.tar.gz'],
               **{name: diag_lock[name] for name in BOOT_FILES}}
    provenance = output / 'provenance'
    provenance.mkdir()
    for path in (user_evidence, diag_evidence, Path(__file__).resolve(),
                 REPO / 'configs/diagnostic-sources.lock.json',
                 REPO / 'configs/builder-packages.lock.tsv',
                 REPO / 'build/host-userspace.sh',
                 REPO / 'configs/host-userspace-packages.txt',
                 REPO / 'configs/host-userspace-packages.lock.tsv',
                 REPO / 'configs/host-userspace-package-hashes.lock.tsv',
                 *(REPO / source for source in MANAGEMENT_FILES)):
        shutil.copyfile(path, provenance / path.name)
    for name in BOOT_FILES:
        shutil.copyfile(diagnostic / name, output / name)
    with tempfile.TemporaryDirectory(prefix='.staging-', dir=output) as stage:
        root = Path(stage)
        extract_rootfs(rootfs, root)
        module_bom = import_modules(root, modules)
        management = install_management(root)
        write_json(output / 'modules.json', module_bom)
        metadata_dir = within_root(root, 'usr/lib/orangepi-hypervisor')
        metadata_dir.mkdir(parents=True, exist_ok=True)
        for slot in SLOT_UUIDS:
            write_json(metadata_dir / 'payload.json', {
                'schema': 1, 'classification': CLASSIFICATION, 'slot': slot,
                'filesystem_uuid': SLOT_UUIDS[slot], 'kernel_release': KERNEL_RELEASE,
                'sources': sources, 'modules_bom': file_record(output / 'modules.json'),
                'management_runtime': management})
            image = output / f'root-{slot}.ext4'
            make_image(root, image, slot)
            with image.open('rb') as source, (output / (image.name + '.gz')).open('xb') as target:
                with gzip.GzipFile(filename='', mode='wb', fileobj=target, mtime=0) as compressed:
                    shutil.copyfileobj(source, compressed)
    files = {str(p.relative_to(output)): file_record(p)
             for p in sorted(output.rglob('*')) if p.is_file()}
    write_json(output / 'manifest.json', {
        'schema': 1, 'board': 'Orange Pi 4 Pro A733', 'classification': CLASSIFICATION,
        'kernel_release': KERNEL_RELEASE, 'slot_bytes': SLOT_BYTES,
        'slot_uuids': SLOT_UUIDS, 'sources': sources, 'files': files,
        'management_runtime': management,
        'builder_packages': builder_packages,
        'reproducibility': 'not established; filesystem/package-generated metadata may vary',
        'tools': {tool: tool_version(tool) for tool in ('mke2fs', 'tune2fs', 'e2fsck')}})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('rootfs_tar', type=Path)
    parser.add_argument('diagnostic_artifact_dir', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    try:
        build(args.rootfs_tar, args.diagnostic_artifact_dir, args.output)
    except (ValueError, OSError, tarfile.TarError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'host payload: {error}\n')


if __name__ == '__main__':
    main()
