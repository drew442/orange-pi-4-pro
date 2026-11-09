#!/usr/bin/env python3
"""Restore three pinned identity files to this board's freshly installed STATE.

Default is a read-only, unmounted plan. This is a trusted quiescent RAM recovery
operation, not a defense against concurrent hostile root. No root slot is opened
for writing. See docs/known-host-state-personalization.md before use.
"""
import argparse
import base64
import binascii
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import uuid

DISK = Path('/dev/nvme0n1')
PART = Path('/dev/nvme0n1p4')
SIZE = 256060514304
PART_SIZE = 8 * 1024**3
PART_START = (1 + 512 + 4096 + 4096) * 2048
STATE_UUID = '360dfb39-f49f-487e-b307-0bb887c854c5'
ARCHIVE_HASH = 'ae3930cec481912a164361e11c926ca02d81561a49c0fc49b70514dd19d3d06e'
FINGERPRINT = 'SHA256:Epfegy9GsJ6ng4/0EH3dZmir7beQj5uYTnIbE4JPfEI'
# Protected predecessor STATE: production-host-state-runtime-fixes-20261108.json.
FRESH_MACHINE_ID_HASH = '27e9d2bf78a55ad7cbfc2dfce5511aebb7adcd44b24b474b5123c4ce41c39e2a'
# Public-config STATE: public-config-state-build-20261109.json, raw SHA256
# 2e9d3dcf4dcabfaead65e125e7778c0aed092eeb665e67f3559719d08e3564ec.
# Its factory bytes + newline were authenticated from the 118399-byte full-image
# manifest SHA256 3cbe28d6154d1bdeb8d364bd0f8bf82b61fd40af7be27fbd49695771c4fad08f.
# Both are factory identities for the fixed STATE UUID/SSD, never arbitrary IDs.
FRESH_MACHINE_ID_HASHES = frozenset({
    FRESH_MACHINE_ID_HASH,
    '413b642225f62d2813550eded9cfd923f2a947657f7d00addffd57ef329dc347',
})
FILES = {'identity/machine-id': 0o644, 'identity/ssh/ssh_host_ed25519_key': 0o600,
         'identity/ssh/ssh_host_ed25519_key.pub': 0o644}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def dev_id(value):
    return f'{os.major(value)}:{os.minor(value)}'


def mounts(text=None):
    entries = []
    for line in (Path('/proc/self/mountinfo').read_text() if text is None else text).splitlines():
        before, after = line.split(' - ', 1)
        fields, tail = before.split(), after.split()
        entries.append({'dev': fields[2], 'root': fields[3], 'path': fields[4],
                        'options': fields[5].split(','), 'type': tail[0]})
    return entries


def owned(path, *, directory=False, mode=None, device=None, owner=0):
    info = path.lstat()
    require((stat.S_ISDIR if directory else stat.S_ISREG)(info.st_mode), 'unsafe object type')
    require(info.st_uid == owner and info.st_gid == owner and not info.st_mode & 0o7022,
            'unsafe ownership or permissions')
    require(mode is None or stat.S_IMODE(info.st_mode) == mode, 'incorrect object mode')
    require(device is None or info.st_dev == device, 'unexpected filesystem boundary')
    require(directory or info.st_nlink == 1, 'aliased file')
    return info


def bounded_read(path, limit, *, mode=None, device=None):
    info = owned(path, mode=mode, device=device)
    require(info.st_size <= limit, 'oversize file')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOATIME)
    try:
        require(os.fstat(fd) == info, 'file changed before read')
        data = os.read(fd, limit + 1)
        require(len(data) == info.st_size and os.fstat(fd) == info, 'file changed during read')
        return data
    finally:
        os.close(fd)


def runtime_guard(expected_boot):
    require(os.geteuid() == 0, 'root required')
    require(str(uuid.UUID(expected_boot)) == expected_boot, 'noncanonical boot pin')
    require(Path('/proc/sys/kernel/random/boot_id').read_text().strip() == expected_boot,
            'boot ID differs')
    for name in ('mnt', 'pid', 'user'):
        require(os.readlink('/proc/self/ns/' + name) == os.readlink('/proc/1/ns/' + name),
                'initial host namespaces required')
    require(Path('/proc/self/uid_map').read_text().split() == ['0', '0', '4294967295'],
            'initial user namespace required')
    entries = mounts()
    require(any(e['path'] == '/' and e['type'] in ('rootfs', 'ramfs', 'tmpfs') for e in entries),
            'RAM root required')
    require(any(e['path'] == '/run' and e['type'] == 'tmpfs' for e in entries),
            'dedicated /run tmpfs required')
    owned(Path('/run'), directory=True)


def parse_archive(data):
    require(len(data) == 10240 and digest(data) == ARCHIVE_HASH, 'archive byte pin differs')
    result = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:') as archive:
        count = 0
        for member in archive:
            count += 1
            require(count <= 5 and member.name not in result, 'duplicate or excess archive members')
            require(set(member.pax_headers) <= {'mtime', 'atime', 'ctime'} and
                    all(re.fullmatch(r'-?[0-9]+(?:\.[0-9]+)?', value)
                        for value in member.pax_headers.values()),
                    'unselected or invalid archive extension')
            if member.isdir() and member.name in ('identity', 'identity/ssh'):
                require(member.uid == member.gid == 0 and member.mode == 0o700,
                        'unsafe archive directory')
                result[member.name] = None
                continue
            require(member.name in FILES and member.isfile(),
                    'unselected or unsafe archive member')
            require(member.uid == member.gid == 0 and member.mode == FILES[member.name],
                    'archive member metadata differs')
            require(0 < member.size <= (33 if member.name.endswith('machine-id') else 4096),
                    'archive member size differs')
            result[member.name] = archive.extractfile(member).read(member.size + 1)
    require(set(result) == set(FILES) | {'identity', 'identity/ssh'},
            'missing identity file or directory')
    result = {k: v for k, v in result.items() if v is not None}
    require(set(result) == set(FILES), 'missing identity file')
    mid = result['identity/machine-id']
    require(re.fullmatch(rb'[0-9a-f]{32}\n', mid) and mid != b'0' * 32 + b'\n',
            'invalid machine-id')
    public = result['identity/ssh/ssh_host_ed25519_key.pub'].split()
    require(len(public) in (2, 3) and public[0] == b'ssh-ed25519', 'invalid public key')
    try:
        blob = base64.b64decode(public[1], validate=True)
        private = result['identity/ssh/ssh_host_ed25519_key']
        require(private.startswith(b'-----BEGIN OPENSSH PRIVATE KEY-----\n') and
                private.endswith(b'-----END OPENSSH PRIVATE KEY-----\n'), 'invalid private key envelope')
        decoded = base64.b64decode(b''.join(private.splitlines()[1:-1]), validate=True)
    except binascii.Error as error:
        raise ValueError('invalid key encoding') from error
    require(blob[:19] == b'\0\0\0\x0bssh-ed25519\0\0\0\x20' and len(blob) == 51,
            'invalid Ed25519 wire key')
    require(decoded.startswith(b'openssh-key-v1\0') and blob in decoded, 'invalid pinned private key')
    fp = 'SHA256:' + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip('=')
    require(fp == FINGERPRINT, 'public key fingerprint differs')
    return result


def source_files(path):
    require(path.is_absolute() and path == path.resolve(strict=True) and path.is_relative_to('/run'),
            'source must be canonical under /run')
    current = path.parent
    require(current != Path('/run'), 'source requires a private staging directory')
    while current != Path('/run'):
        owned(current, directory=True, mode=0o700)
        current = current.parent
    info = owned(path, mode=0o600)
    require(any(e['type'] == 'tmpfs' and e['dev'] == dev_id(info.st_dev) for e in mounts()),
            'source must be in tmpfs')
    return parse_archive(bounded_read(path, 10240, mode=0o600))


def idle(ids):
    require(not any(e['dev'] in ids for e in mounts()), 'NVMe is mounted')
    swaps = Path('/proc/swaps').read_text().splitlines()
    require(swaps and swaps[0].split() == ['Filename', 'Type', 'Size', 'Used', 'Priority'],
            'unrecognized swap inventory')
    for line in swaps[1:]:
        info = os.stat(line.split()[0])
        require(dev_id(info.st_rdev if stat.S_ISBLK(info.st_mode) else info.st_dev) not in ids,
                'NVMe has active swap')
    for proc in Path('/proc').iterdir():
        if not proc.name.isdecimal():
            continue
        try:
            require(not any(e['dev'] in ids for e in mounts((proc / 'mountinfo').read_text())),
                    'NVMe mounted in another namespace')
            for link in [proc / 'cwd', proc / 'root', proc / 'exe', *list((proc / 'fd').iterdir())]:
                try:
                    info = link.stat()
                except FileNotFoundError:
                    continue
                require(dev_id(info.st_dev) not in ids and
                        (not stat.S_ISBLK(info.st_mode) or dev_id(info.st_rdev) not in ids),
                        'process uses NVMe')
            for line in (proc / 'maps').read_text().splitlines():
                fields = line.split()
                major, minor = fields[3].split(':')
                require(f'{int(major, 16)}:{int(minor, 16)}' not in ids, 'process maps NVMe')
        except (FileNotFoundError, ProcessLookupError):
            continue


def ioctl_number(fd, command, fmt):
    return struct.unpack(fmt, fcntl.ioctl(fd, command, bytes(struct.calcsize(fmt))))[0]


def inspect_target():
    nodes = [DISK, *sorted(Path('/dev').glob('nvme0n1p*'))]
    ids = set()
    identities = {}
    for path in nodes:
        info = path.lstat()
        require(stat.S_ISBLK(info.st_mode), 'target is not a real block device')
        identity = dev_id(info.st_rdev)
        node = (Path('/sys/dev/block') / identity).resolve(strict=True)
        require(node.name == path.name and node.joinpath('dev').read_text().strip() == identity,
                'sysfs identity differs')
        require(not list(node.joinpath('holders').iterdir()), 'NVMe has holders')
        identities[path.name] = identity
        ids.add(identity)
    require(set(identities) == {'nvme0n1', *('nvme0n1p' + str(i) for i in range(1, 6))},
            'unexpected NVMe partition set')
    base = Path('/sys/class/block/nvme0n1')
    require(base.joinpath('device/serial').read_text().strip() == '134807190FC700149277' and
            base.joinpath('device/model').read_text().strip() == 'SPCC M.2 PCIe SSD',
            'NVMe serial or model differs')
    require(int(base.joinpath('size').read_text()) * 512 == SIZE and
            int(base.joinpath('queue/logical_block_size').read_text()) == 512 and
            int(base.joinpath('queue/physical_block_size').read_text()) == 512,
            'NVMe geometry differs')
    p4 = Path('/sys/class/block/nvme0n1p4')
    require(p4.resolve().parent == base.resolve() and int(p4.joinpath('partition').read_text()) == 4 and
            int(p4.joinpath('start').read_text()) == PART_START and
            int(p4.joinpath('size').read_text()) * 512 == PART_SIZE, 'STATE geometry differs')
    idle(ids)  # Before opening either device ourselves.
    for path, size in ((DISK, SIZE), (PART, PART_SIZE)):
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            require(dev_id(os.fstat(fd).st_rdev) == identities[path.name], 'opened device differs')
            for cmd, fmt, expected in ((0x80081272, '=Q', size), (0x1268, '=I', 512),
                                       (0x127b, '=I', 512), (0x125e, '=I', 0)):
                require(ioctl_number(fd, cmd, fmt) == expected, 'block ioctl geometry differs')
            if path == PART:
                validate_superblock(os.pread(fd, 1024, 1024))
        finally:
            os.close(fd)
    return identities


def validate_superblock(data):
    require(len(data) == 1024 and data[56:58] == b'\x53\xef' and
            str(uuid.UUID(bytes=bytes(data[104:120]))) == STATE_UUID and
            data[120:136].rstrip(b'\0') == b'a733-state', 'STATE superblock differs')
    u32 = lambda offset: struct.unpack_from('<I', data, offset)[0]
    require(u32(24) == 2 and u32(4) == PART_SIZE // 4096 and u32(336) == 0,
            'STATE block geometry differs')
    require(u32(92) & 4 and not u32(96) & ~0x2c6,
            'unsupported ext4 or journal features')
    require(data[208:224] == bytes(16) and u32(224) == 8 and u32(228) == 0,
            'internal journal inode 8 required; external journal refused')
    # Apply explicitly authorizes writable recovery of this STATE. Dirty journal
    # and orphan metadata are permitted; external journal devices are never used.


def verify_mount(path, identity):
    matches = [e for e in mounts() if e['path'] == str(path)]
    require(len(matches) == 1 and matches[0]['dev'] == identity and matches[0]['root'] == '/' and
            matches[0]['type'] == 'ext4' and
            {'rw', 'nosuid', 'nodev', 'noexec'} <= set(matches[0]['options']),
            'mounted filesystem identity or options differ')
    require(dev_id(path.stat().st_dev) == identity, 'mount stat identity differs')


def check_state(root):
    device = owned(root, directory=True).st_dev
    require(bounded_read(root / '.a733-state', 128, mode=0o600, device=device) ==
            f'a733-state-v1\n{STATE_UUID}\n'.encode(), 'STATE marker differs')
    # Fresh runtime-fixed timesync profile must retain its special daemon owner.
    current = root
    for component in ('var', 'lib', 'systemd', 'timesync'):
        current /= component
        owned(current, directory=True, mode=0o755, device=device,
              owner=996 if component == 'timesync' else 0)
    for relative in ('identity', 'identity/ssh'):
        path = root / relative
        if path.exists() or path.is_symlink():
            owned(path, directory=True, mode=0o700, device=device)
    return device


def prepare_destinations(root, files, fresh_hash):
    # Retain the internal single-hash interface used by legacy fixtures; production
    # passes only the fixed two-entry registry, with no CLI or environment override.
    fresh_hashes = frozenset({fresh_hash}) if type(fresh_hash) is str else fresh_hash
    require(type(fresh_hashes) is frozenset and 1 <= len(fresh_hashes) <= 2 and
            all(type(value) is str and re.fullmatch(r'[0-9a-f]{64}', value)
                for value in fresh_hashes), 'invalid factory machine-id hash allowance')
    device = check_state(root)
    existing = {}
    for name, data in files.items():
        path = root / name
        if path.exists() or path.is_symlink():
            old = bounded_read(path, 4096, mode=FILES[name], device=device)
            permitted = old in (b'', data)
            if name == 'identity/machine-id' and re.fullmatch(rb'[0-9a-f]{32}\n', old):
                permitted |= digest(old) in fresh_hashes
            require(permitted, 'unexpected nonempty destination; refusing overwrite')
            existing[name] = old
    return existing


def atomic_files(root, files, fresh_hash, changed):
    existing = prepare_destinations(root, files, fresh_hash)  # Refuse all conflicts before changes.
    device = root.stat().st_dev
    for relative in ('identity', 'identity/ssh'):
        path = root / relative
        if not path.exists():
            path.mkdir(mode=0o700)
            sync_dir(path.parent)
        owned(path, directory=True, mode=0o700, device=device)
    for name, data in files.items():
        if existing.get(name) == data:
            continue
        path = root / name
        fd, temporary = tempfile.mkstemp(prefix='.a733-identity-', dir=path.parent)
        try:
            os.fchmod(fd, FILES[name])
            os.fchown(fd, 0, 0)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            changed.append(name)  # Rename can survive even if subsequent directory sync fails.
            sync_dir(path.parent)
        finally:
            if os.path.lexists(temporary):
                os.unlink(temporary)
    for name, data in files.items():
        require(bounded_read(root / name, 4096, mode=FILES[name], device=device) == data,
                'identity readback differs')


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def execute(args, report):
    runtime_guard(args.expected_boot_id)
    files = source_files(args.source_archive)
    identity = inspect_target()
    report.update(target=str(PART), disk=str(DISK), serial='134807190FC700149277',
                  state_uuid=STATE_UUID, archive_sha256=ARCHIVE_HASH, public_fingerprint=FINGERPRINT,
                  proposed_files=[{'path': n, 'sha256': digest(d), 'mode': format(FILES[n], '04o')}
                                  for n, d in files.items()])
    if not args.apply:
        report.update(completed=True, durability='not applicable; unmounted read-only plan',
                      destination_inspection='deferred until apply mount')
        return
    runtime_guard(args.expected_boot_id)
    require(source_files(args.source_archive) == files and inspect_target() == identity,
            'preflight changed')
    point = Path(tempfile.mkdtemp(prefix='a733-identity-', dir='/run'))
    mounted = False
    try:
        subprocess.run(['/bin/mount', '-t', 'ext4', '-o', 'rw,nosuid,nodev,noexec', str(PART), str(point)],
                       check=True, capture_output=True)
        mounted = True
        verify_mount(point, identity[PART.name])
        atomic_files(point, files, FRESH_MACHINE_ID_HASHES, report['changed_files'])
        check_state(point)
        report['files_fsynced'] = True
    finally:
        if mounted:
            subprocess.run(['/bin/umount', str(point)], check=True, capture_output=True)
            report['unmounted'] = True
        point.rmdir()
    report.update(completed=True, durability='files and containing directories fsynced; STATE unmounted')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-boot-id', required=True)
    parser.add_argument('--source-archive', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    report = {'schema': 1, 'operation': 'same-physical-board-identity-preservation',
              'apply': args.apply, 'hardware_acceptance': False, 'completed': False,
              'changed_files': [], 'files_fsynced': False, 'unmounted': False}
    try:
        execute(args, report)
    except (OSError, ValueError, subprocess.SubprocessError, tarfile.TarError) as error:
        # Never expose source contents, machine-id, or subprocess stderr.
        report.update(error=(str(error) if isinstance(error, ValueError) else
                             'identity operation refused or failed; do not reboot'),
                      partial=bool(report['changed_files']), durability='not established')
        print(json.dumps(report, sort_keys=True))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
