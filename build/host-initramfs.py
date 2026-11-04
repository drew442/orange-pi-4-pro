#!/usr/bin/env python3
"""Package an authenticated NVMe host initramfs; never write a storage device."""
import argparse
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import struct
import subprocess
import tarfile

REPO = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


PAYLOAD = module('host_payload', REPO / 'build/host-payload.py')
STORAGE = module('host_storage_tools', REPO / 'build/host-storage-tools.py')


def read_pinned(path, expected):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    stream = os.fdopen(descriptor, 'rb')
    try:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError(f'not a regular file: {path}')
        if STORAGE.digest(stream) != expected:
            raise ValueError(f'input hash/size mismatch: {path}')
        stream.seek(0)
        return stream
    except BaseException:
        stream.close()
        raise


def root_marker(stream, slot):
    stream.seek(1024)
    block = stream.read(1024)
    if (len(block) != 1024 or block[56:58] != b'\x53\xef' or
            struct.unpack_from('<I', block, 92)[0] & 4 or
            not struct.unpack_from('<I', block, 100)[0] & 0x1000):
        raise ValueError('root must be ext4 read-only feature without journal')
    import uuid
    if str(uuid.UUID(bytes=block[104:120])) != PAYLOAD.SLOT_UUIDS[slot]:
        raise ValueError('root UUID differs from slot')
    descriptor = stream.fileno()
    result = subprocess.run(['debugfs', '-R', 'cat /usr/lib/orangepi-hypervisor/payload.json',
                             f'/proc/self/fd/{descriptor}'], pass_fds=(descriptor,),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    value = json.loads(result.stdout)
    if (value['slot'] != slot or value['filesystem_uuid'] != PAYLOAD.SLOT_UUIDS[slot] or
            value['kernel_release'] != PAYLOAD.KERNEL_RELEASE):
        raise ValueError('root payload marker incompatible with selected slot/kernel')
    return result.stdout, value


def newc(entries):
    """Deterministic root-owned newc, with fixed names/modes and no hardlinks."""
    result = bytearray()
    items = sorted(entries.items()) + [('TRAILER!!!', (0, b''))]
    for inode, (name, (mode, data)) in enumerate(items, 1):
        if name.startswith('/') or '..' in Path(name).parts or '\0' in name:
            raise ValueError('unsafe initramfs entry')
        encoded = name.encode() + b'\0'
        fields = [inode, mode, 0, 0, 1, 0, len(data), 0, 0, 0, 0, len(encoded), 0]
        result.extend(b'070701' + ''.join(f'{value:08x}' for value in fields).encode() + encoded)
        result.extend(b'\0' * (-len(result) % 4))
        result.extend(data)
        result.extend(b'\0' * (-len(result) % 4))
    return bytes(result)


def add(entries, name, mode, data=b''):
    if name in entries:
        raise ValueError(f'duplicate initramfs entry: {name}')
    for parent in reversed(Path(name).parents):
        if str(parent) != '.':
            entries.setdefault(str(parent), (stat.S_IFDIR | 0o755, b''))
    entries[name] = (mode, data)


def validate_kernel(data):
    settings = dict(line.split('=', 1) for line in data.decode().splitlines()
                    if line.startswith('CONFIG_') and '=' in line)
    required = ('BLK_DEV_NVME', 'EXT4_FS', 'OVERLAY_FS', 'TMPFS', 'TMPFS_XATTR', 'DEVTMPFS')
    missing = [name for name in required if settings.get('CONFIG_' + name) != 'y']
    if missing:
        raise ValueError(f'kernel must build in: {missing}')


def build(rootfs, diagnostic, busybox, root_image, slot, state_uuid, output):
    if not re.fullmatch(r'[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}', state_uuid):
        raise ValueError('state UUID must be canonical lowercase')
    if state_uuid in PAYLOAD.SLOT_UUIDS.values():
        raise ValueError('state UUID must differ from root slots')
    output = Path(os.path.abspath(output))
    if output.parent != REPO / 'out' or not output.name.startswith('host-initramfs-'):
        raise ValueError('output must be a new repo/out/host-initramfs-NAME directory')
    if output.parent.is_symlink() or output.exists() or output.is_symlink():
        raise ValueError('existing/symlink output refused')
    diag = json.loads((REPO / 'docs/evidence/diagnostic-20261104.json').read_text())['files']
    user = json.loads((REPO / 'docs/evidence/host-userspace-20261104.json').read_text())['files']['rootfs.tar.gz']
    roots = json.loads((REPO / 'docs/evidence/host-payload-20261104.json').read_text())
    lock = json.loads((REPO / 'configs/host-storage-tools.lock.json').read_text())
    busybox_hash = json.loads((REPO / 'configs/diagnostic-sources.lock.json').read_text())['busybox']['binary_sha256']
    descriptor = os.open(busybox, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('BusyBox must be regular')
        busybox_data = stream.read(8 * 1024**2 + 1)
    if hashlib.sha256(busybox_data).hexdigest() != busybox_hash:
        raise ValueError('BusyBox hash mismatch')
    with read_pinned(root_image, roots['files'][f'root-{slot}.ext4']) as stream:
        marker, metadata = root_marker(stream, slot)
    for name in ('Image', 'board.dtb', 'kernel.config'):
        with read_pinned(Path(diagnostic) / name, diag[name]) as stream:
            if name == 'kernel.config':
                validate_kernel(stream.read())
    with read_pinned(Path(diagnostic) / 'initramfs.cpio.gz', diag['initramfs.cpio.gz']) as stream:
        modules = PAYLOAD.module_entries(gzip.decompress(stream.read()))
    with read_pinned(rootfs, user) as stream:
        with tarfile.open(fileobj=stream, mode='r:gz') as archive:
            runtime = STORAGE.selected_files(archive, lock)
    for name in ('Image', 'board.dtb', 'kernel.config', 'initramfs.cpio.gz'):
        if metadata['sources'][name] != diag[name]:
            raise ValueError('root marker and boot stack source mismatch')
    entries = {}
    for name in ('bin', 'sbin', 'dev', 'proc', 'sys', 'run', 'tmp', 'etc', 'newroot', 'lib/modules'):
        add(entries, name, stat.S_IFDIR | (0o1777 if name == 'tmp' else 0o755))
    add(entries, 'bin/busybox', stat.S_IFREG | 0o755, busybox_data)
    add(entries, 'bin/sh', stat.S_IFLNK | 0o777, b'busybox')
    for name, source in {'init': 'configs/host-init',
                         'etc/host-storage-resolver.sh': 'configs/host-storage-resolver.sh'}.items():
        add(entries, name, stat.S_IFREG | 0o755, (REPO / source).read_bytes())
    configuration = (f'ROOT_UUID={PAYLOAD.SLOT_UUIDS[slot]}\nSTATE_UUID={state_uuid}\n'
                     f'ROOT_MARKER_SHA256={hashlib.sha256(marker).hexdigest()}\n'
                     f'KERNEL_RELEASE={PAYLOAD.KERNEL_RELEASE}\n').encode()
    add(entries, 'etc/a733-boot.conf', stat.S_IFREG | 0o644, configuration)
    for name, item in lock['files'].items():
        add(entries, name, stat.S_IFREG | 0o755, runtime[item['source']])
    for name, source in lock['notices'].items():
        add(entries, name, stat.S_IFREG | 0o644, runtime[source])
    for name in ('GPL-2.0.txt', 'busybox-copyright.txt'):
        add(entries, 'usr/share/licenses/' + name, stat.S_IFREG | 0o644, (REPO / 'licenses' / name).read_bytes())
    for name, (mode, data) in modules.items():
        path = str(name)
        if stat.S_ISDIR(mode) and path in entries:
            continue
        add(entries, path, mode, data)
    data = gzip.compress(newc(entries), compresslevel=9, mtime=0)
    if len(data) >= 64 * 1024**2:
        raise ValueError('initramfs exceeds existing boot address budget')
    output.parent.mkdir(exist_ok=True)
    output.mkdir(mode=0o700)
    (output / 'initramfs.cpio.gz').write_bytes(data)
    manifest = {'schema': 1, 'classification': 'NVMe host handoff prototype; no physical boot or KVM acceptance',
                'slot': slot, 'root_uuid': PAYLOAD.SLOT_UUIDS[slot], 'state_uuid': state_uuid,
                'root_marker_sha256': hashlib.sha256(marker).hexdigest(),
                'root_image': roots['files'][f'root-{slot}.ext4'], 'boot_inputs': diag,
                'recipes': {name: PAYLOAD.file_record(REPO / name) for name in
                            ('build/host-initramfs.py', 'build/host-payload.py',
                             'build/host-storage-tools.py', 'configs/host-storage-tools.lock.json')},
                'files': {'initramfs.cpio.gz': {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}},
                'entries': {name: {'mode': mode, 'bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest()}
                            for name, (mode, content) in sorted(entries.items()) if stat.S_ISREG(mode)}}
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for argument in ('userspace_tar', 'diagnostic_dir', 'busybox', 'root_image', 'state_uuid', 'new_output'):
        parser.add_argument(argument)
    parser.add_argument('--slot', choices=('A', 'B'), required=True)
    args = parser.parse_args()
    try:
        result = build(args.userspace_tar, args.diagnostic_dir, args.busybox, args.root_image,
                       args.slot, args.state_uuid, args.new_output)
    except (ValueError, OSError, subprocess.CalledProcessError, tarfile.TarError) as error:
        parser.exit(1, f'host initramfs: {error}\n')
    print(json.dumps({key: value for key, value in result.items() if key != 'entries'}, indent=2))
