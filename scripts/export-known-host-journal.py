#!/usr/bin/env python3
"""Export only this board's installed persistent journal from a quiescent RAM boot.

No hardware acceptance is established. See docs/offline-host-journal.md.
"""
import argparse
import ctypes
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import struct
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
MAX_TOTAL = 192 * 1024**2
MAX_FILES = 64
MAX_FILE = 64 * 1024**2
MAX_ENTRIES = 256
MAX_DEPTH = 4
MOUNT_FLAGS = 1 | 2 | 4 | 8  # MS_RDONLY | MS_NOSUID | MS_NODEV | MS_NOEXEC

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
    owned(Path('/'), directory=True, mode=0o755)
    owned(Path('/run'), directory=True)
    swaps = Path('/proc/swaps').read_text().splitlines()
    require(len(swaps) == 1 and swaps[0].split() ==
            ['Filename', 'Type', 'Size', 'Used', 'Priority'], 'all swap must be disabled')
    require(any(e['path'] == '/proc' and e['type'] == 'proc' for e in entries),
            'proc filesystem required')


def idle(ids, held_fd=None):
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
                if (held_fd is not None and proc.name == str(os.getpid()) and
                        link == proc / 'fd' / str(held_fd)):
                    continue  # Only our exact, validated, read-only STATE descriptor.
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


def inspect_target(expected_state_ro=None, held_fd=None):
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
    idle(ids, held_fd)  # No external device references are permitted.
    for path, size in ((DISK, SIZE), (PART, PART_SIZE)):
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            require(dev_id(os.fstat(fd).st_rdev) == identities[path.name], 'opened device differs')
            for cmd, fmt, expected in ((0x80081272, '=Q', size), (0x1268, '=I', 512),
                                       (0x127b, '=I', 512)):
                require(ioctl_number(fd, cmd, fmt) == expected, 'block ioctl geometry differs')
            ro = ioctl_number(fd, 0x125e, '=I')
            allowed_ro = (0,)
            if path == PART:
                allowed_ro = (0, 1) if expected_state_ro is None else (expected_state_ro,)
            require(ro in allowed_ro, 'block read-only flag differs')
            require(int(node_ro(path).read_text()) == ro, 'sysfs read-only flag differs')
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
    require(not u32(92) & 0x1000 and not u32(100) & 0x10000,
            'unsupported ext4 orphan-file features')
    # Legacy s_last_orphan may be nonzero: production mounts require block RO,
    # which makes the pinned ext4 implementation skip orphan cleanup.
    require(data[208:224] == bytes(16) and u32(224) == 8 and u32(228) == 0,
            'internal journal inode 8 required; external journal refused')


def node_ro(path):
    return Path('/sys/class/block') / path.name / 'ro'


def validate_state_identity(fd, identity):
    """Recheck held node, geometry and internal journal before changing its flag."""
    info, named = os.fstat(fd), PART.lstat()
    require(stat.S_ISBLK(info.st_mode) and stat.S_ISBLK(named.st_mode) and
            info.st_rdev == named.st_rdev and info.st_ino == named.st_ino and
            info.st_dev == named.st_dev and dev_id(info.st_rdev) == identity,
            'held STATE descriptor identity differs')
    for cmd, fmt, expected in ((0x80081272, '=Q', PART_SIZE), (0x1268, '=I', 512),
                               (0x127b, '=I', 512)):
        require(ioctl_number(fd, cmd, fmt) == expected, 'held STATE geometry differs')
    validate_superblock(os.pread(fd, 1024, 1024))


def validate_state_fd(fd, identity, expected_ro=None):
    validate_state_identity(fd, identity)
    ro = ioctl_number(fd, 0x125e, '=I')  # BLKROGET
    require(ro in (0, 1) and (expected_ro is None or ro == expected_ro),
            'STATE block read-only protection differs')
    require(int(node_ro(PART).read_text()) == ro, 'STATE sysfs read-only protection differs')
    return ro


def set_state_ro(fd, identity, value):
    validate_state_fd(fd, identity)
    # BLKROSET changes the kernel flag; this does not write filesystem data.
    fcntl.ioctl(fd, 0x125d, struct.pack('=I', value))
    validate_state_fd(fd, identity, value)


def restore_state_writable(fd, identity):
    """Restore an original zero, re-protecting a validated device on failure."""
    try:
        set_state_ro(fd, identity, 0)
        validate_state_fd(fd, identity, 0)
    except (OSError, ValueError) as restore_error:
        try:
            # Read-only readback may itself have failed. Revalidate device and
            # filesystem identity independently before attempting a protective ioctl.
            validate_state_identity(fd, identity)
            fcntl.ioctl(fd, 0x125d, struct.pack('=I', 1))
            validate_state_fd(fd, identity, 1)
        except (OSError, ValueError) as protection_error:
            raise ValueError('STATE restoration failed; block read-only state uncertain; '
                             'no archive') from protection_error
        raise ValueError('STATE restoration failed; block read-only protection reasserted; '
                         'no archive') from restore_error


def protected_journal(point, identities):
    """Mount behind block RO; restore eligibility only after proved unmount."""
    identity = identities[PART.name]
    claim = held = None
    try:
        claim = os.open(PART, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_EXCL)
        original_ro = validate_state_fd(claim, identity)
        if original_ro == 0:
            set_state_ro(claim, identity, 1)
        validate_state_fd(claim, identity, 1)
        held = os.open(PART, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        validate_state_fd(held, identity, 1)
        os.close(claim)
        claim = None  # Kernel mount must not run with an exclusive block claim.
        require(inspect_target(expected_state_ro=1, held_fd=held) == identities,
                'target identity changed before protected mount')
        validate_state_fd(held, identity, 1)
        cleanup = {'unmounted': False}
        try:
            return mounted_journal(point, identity, cleanup), original_ro
        finally:
            # idle() proves no mount/reference remains across visible namespaces.
            # Failed unmount/pre-restore checks cannot clear the verified RO flag.
            require(cleanup['unmounted'],
                    'normal unmount not proved; STATE remains block read-only')
            validate_state_fd(held, identity, 1)
            require(inspect_target(expected_state_ro=1, held_fd=held) == identities,
                    'target identity changed after protected unmount')
            validate_state_fd(held, identity, 1)
            if original_ro == 0:
                restore_state_writable(held, identity)
            else:
                validate_state_fd(held, identity, 1)
    finally:
        if claim is not None:
            os.close(claim)
        if held is not None:
            os.close(held)


def write_counters():
    result = {}
    # Check the known SD identity too: never silently omit a missing SD counter.
    sd = Path('/sys/class/block/mmcblk1')
    require(sd.joinpath('device/cid').read_text().strip() ==
            '035344535530324780716f216e00a8f5', 'SD identity differs')
    for name in ('nvme0n1', 'mmcblk1'):
        fields = Path('/sys/class/block', name, 'stat').read_text().split()
        require(len(fields) >= 11 and all(x.isdecimal() for x in fields),
                'invalid block counters')
        # Writes completed/merged/sectors/time plus discard and flush statistics.
        result[name] = [int(x) for x in fields[4:8] + fields[11:]]
    return result


def mount_readonly(point):
    libc = ctypes.CDLL(None, use_errno=True)
    libc.mount.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
                           ctypes.c_ulong, ctypes.c_char_p]
    libc.mount.restype = ctypes.c_int
    if libc.mount(os.fsencode(PART), os.fsencode(point), b'ext4', MOUNT_FLAGS,
                  b'noload') != 0:
        raise OSError(ctypes.get_errno(), 'read-only noload mount failed')


def unmount(point):
    libc = ctypes.CDLL(None, use_errno=True)
    libc.umount2.argtypes = [ctypes.c_char_p, ctypes.c_int]
    libc.umount2.restype = ctypes.c_int
    if libc.umount2(os.fsencode(point), 0) != 0:
        raise OSError(ctypes.get_errno(), 'unmount failed; retain mountpoint')


def verify_mount(point, identity):
    matches = [e for e in mounts() if e['path'] == str(point)]
    require(len(matches) == 1 and matches[0]['dev'] == identity and
            matches[0]['root'] == '/' and matches[0]['type'] == 'ext4' and
            {'ro', 'nosuid', 'nodev', 'noexec'} <= set(matches[0]['options']) and
            'rw' not in matches[0]['options'], 'mounted identity or options differ')
    # Kernel reports noload as norecovery in ext4 superblock mount options.
    lines = Path('/proc/self/mountinfo').read_text().splitlines()
    require(any(line.split(' - ', 1)[0].split()[4] == str(point) and
                {'noload', 'norecovery'} & set(line.split(' - ', 1)[1].split()[2].split(','))
                for line in lines), 'journal replay suppression missing')
    require(dev_id(point.stat().st_dev) == identity, 'mounted device differs')


def directory_info(fd, device):
    info = os.fstat(fd)
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and
            not info.st_mode & 0o0022 and info.st_dev == device,
            'unsafe journal directory')
    return info


def collect_journal(root):
    """Use held directory FDs, fail unsafe entries, and retain bounded bytes in RAM."""
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY | os.O_NOATIME
    rootfd = os.open(root, flags)
    result = []
    total = 0
    entries = 0
    device = os.fstat(rootfd).st_dev

    def walk(fd, relative, depth):
        nonlocal total, entries
        before = directory_info(fd, device)
        require(depth <= MAX_DEPTH, 'journal directory bounds exceeded')
        names = []
        with os.scandir(fd) as directory:
            for entry in directory:
                entries += 1
                require(entries <= MAX_ENTRIES, 'journal directory bounds exceeded')
                names.append(entry.name)
        for name in sorted(names):
            require(re.fullmatch(r'[A-Za-z0-9_.@~-]{1,255}', name) is not None and
                    name not in ('.', '..'), 'unsafe journal entry name')
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            path = relative + '/' + name
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name, flags, dir_fd=fd)
                try:
                    require(os.fstat(child) == info, 'directory changed before traversal')
                    walk(child, path, depth + 1)
                finally:
                    os.close(child)
                continue
            require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and
                    info.st_nlink == 1 and not info.st_mode & 0o7022 and
                    info.st_dev == device, 'unsafe journal entry')
            require(name.endswith(('.journal', '.journal~')),
                    'unselected file in journal subtree')
            require(0 <= info.st_size <= MAX_FILE and len(result) < MAX_FILES and
                    total + info.st_size <= MAX_TOTAL, 'journal file bounds exceeded')
            source = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME |
                             os.O_NONBLOCK, dir_fd=fd)
            try:
                require(os.fstat(source) == info, 'journal changed before read')
                chunks = []
                remaining = info.st_size
                while remaining:
                    chunk = os.read(source, min(1024 * 1024, remaining))
                    require(bool(chunk), 'short journal read')
                    chunks.append(chunk)
                    remaining -= len(chunk)
                require(os.read(source, 1) == b'' and os.fstat(source) == info,
                        'journal changed during read')
                data = b''.join(chunks)
            finally:
                os.close(source)
            result.append((path, info, data))
            total += len(data)
        require(os.fstat(fd) == before, 'directory changed during traversal')

    held = [rootfd]
    try:
        directory_info(rootfd, device)
        for component in ('var', 'log', 'journal'):
            before = os.stat(component, dir_fd=held[-1], follow_symlinks=False)
            child = os.open(component, flags, dir_fd=held[-1])
            held.append(child)
            require(os.fstat(child) == before, 'journal ancestor changed')
            directory_info(child, device)
        ancestor_stats = [os.fstat(fd) for fd in held]
        walk(held[-1], 'var/log/journal', 0)
        require(all(os.fstat(fd) == info for fd, info in zip(held, ancestor_stats)),
                'journal ancestor changed during export')
        require(bool(result), 'no persistent journal files found')
    finally:
        for fd in reversed(held):
            os.close(fd)
    return result


def mounted_journal(point, identity, cleanup_state=None):
    mounted = False
    try:
        mount_readonly(point)
        mounted = True
        verify_mount(point, identity)
        return collect_journal(point)
    finally:
        if mounted:
            unmount(point)
            if cleanup_state is not None:
                cleanup_state['unmounted'] = True
        # Do not remove a mountpoint after a failed unmount.
        point.rmdir()


def emit_archive(files, receipt, output):
    with tarfile.open(fileobj=output, mode='w|gz', format=tarfile.PAX_FORMAT) as archive:
        for name, info, data in files:
            member = tarfile.TarInfo(name)
            member.size = len(data)
            member.mode = stat.S_IMODE(info.st_mode)
            member.uid, member.gid = info.st_uid, info.st_gid
            member.mtime = info.st_mtime_ns // 10**9
            archive.addfile(member, io.BytesIO(data))
        data = (json.dumps(receipt, sort_keys=True, indent=2) + '\n').encode()
        member = tarfile.TarInfo('export-receipt.json')
        member.mode, member.size = 0o600, len(data)
        archive.addfile(member, io.BytesIO(data))


def execute(expected_boot, output):
    runtime_guard(expected_boot)
    before = write_counters()
    identity = inspect_target()
    print('Validated RAM boot and fixed STATE identity; protecting block device before mount.', file=sys.stderr)
    point = Path(tempfile.mkdtemp(prefix='a733-journal-', dir='/run'))
    files, original_ro = protected_journal(point, identity)
    runtime_guard(expected_boot)
    require(inspect_target(expected_state_ro=original_ro) == identity,
            'target identity changed after export')
    after = write_counters()
    require(before == after, 'SD or NVMe write counters changed; refusing archive')
    receipt = {'schema': 1, 'operation': 'offline-persistent-journal-export',
               'boot_id': expected_boot, 'hardware_acceptance': False,
               'source': str(PART), 'state_uuid': STATE_UUID,
               'mount_flags': ['ro', 'nosuid', 'nodev', 'noexec', 'noload'],
               'unmounted': True, 'block_ro_during_mount': 1,
               'block_ro_original': original_ro, 'block_ro_restored': original_ro,
               'write_counters_before': before,
               'write_counters_after': after,
               'members': [{'path': name, 'size': len(data),
                            'sha256': hashlib.sha256(data).hexdigest(),
                            'uid': info.st_uid, 'gid': info.st_gid,
                            'mode': format(stat.S_IMODE(info.st_mode), '04o'),
                            'mtime_ns': info.st_mtime_ns}
                           for name, info, data in files]}
    print(f'Unmounted; counters unchanged; exporting {len(files)} journal files '
          f'({sum(len(data) for _, _, data in files)} bytes).', file=sys.stderr)
    emit_archive(files, receipt, output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-boot-id', required=True)
    args = parser.parse_args()
    try:
        execute(args.expected_boot_id, sys.stdout.buffer)
    except (OSError, ValueError, tarfile.TarError) as error:
        print('Journal export refused or failed: ' +
              (str(error) if isinstance(error, ValueError) else type(error).__name__) +
              '; discard any partial archive and inspect mounts before reboot.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
