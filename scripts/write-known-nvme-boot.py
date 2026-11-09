#!/usr/bin/env python3
"""Guarded offline BOOT-only updater for one preauthorized A733 NVMe."""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import sys
import time
import uuid

DISK = '/dev/nvme0n1'
TARGET = '/dev/nvme0n1p1'
TARGET_BYTES = 256060514304
SOURCE_BYTES = 536870912
OLD_SHA256 = '5860889755d7479e619e86b83bc32237e51bacd3a3fd2e2d2ebe976898f44899'
NEW_SHA256 = 'cc2ad988fdc57579178766acaf09c71d688aaadec94b722593192cb24426c313'
SERIAL = '134807190FC700149277'
MODEL = 'SPCC M.2 PCIe SSD'
KERNEL_RELEASE = '6.6.98-a733-diag'
NS_GET_NSTYPE = 0xb703
# Pinned vendor include/linux/proc_ns.h, revision 2ac08e8c7cdc28abbdc5c9a9dd812f887ae9c79f:
# https://raw.githubusercontent.com/orangepi-xunlong/linux-orangepi/2ac08e8c7cdc28abbdc5c9a9dd812f887ae9c79f/include/linux/proc_ns.h
INITIAL_NAMESPACE_INODES = {'pid': 0xEFFFFFFC, 'user': 0xEFFFFFFD}
NAMESPACE_TYPES = {'pid': 0x20000000, 'user': 0x10000000, 'mnt': 0x00020000}
AUTHORIZATION = 'docs/evidence/storage-write-authorization-20261108.json'
BLKGETSIZE64, BLKSSZGET, BLKPBSZGET, BLKROGET, BLKFLSBUF = 0x80081272, 0x1268, 0x127b, 0x125e, 0x1261
CHUNK = 1024 * 1024
# Sectors from the installed corrected VGIC manifest, including both GPT copies.
GEOMETRY = ((2048, 1048576), (1050624, 8388608), (9439232, 8388608),
            (17827840, 16777216), (34605056, 465512448))
GPT_EXTENTS = (
    (0, 512, 'b6d0b9179af5316d7a9c9db924c0cd01a4976c94b896415ff2bb6f4ff4922c50'),
    (512, 512, 'f6e60c22527739a241b05ea22762c001432b9470383e641b9a83f0988dabf52c'),
    (1024, 16384, '13a4b8d58e00ffa6a3c52f377d0dcd4454df904f34f754092b988413b1c673be'),
    (256060497408, 16384, '13a4b8d58e00ffa6a3c52f377d0dcd4454df904f34f754092b988413b1c673be'),
    (256060513792, 512, 'c796f02d3a1bf7ad2d6e1dcbba1c70e05f67bb322a59c42c30bf72c795c32708'),
)


class Refusal(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise Refusal(message)


def read_text(path):
    # Failed reads are fatal, never converted into an empty successful probe.
    return Path(path).read_text(encoding='ascii').strip()


def device_number(value):
    require(re.fullmatch(r'[0-9]+:[0-9]+', value) is not None,
            'malformed device number')
    return tuple(map(int, value.split(':')))


def unescape_mount(value):
    # mountinfo uses octal escapes for whitespace and backslash.
    require(re.search(r'\\(?![0-7]{3})', value) is None, 'malformed mount escape')
    return re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), value)


def mountinfo(raw=None):
    result = []
    raw = read_text('/proc/self/mountinfo') if raw is None else raw
    require(bool(raw), 'empty mountinfo')
    for line in raw.splitlines():
        fields = line.split()
        require(fields.count('-') == 1, 'malformed mountinfo separator')
        separator = fields.index('-')
        require(separator >= 6 and len(fields) == separator + 4,
                'malformed mountinfo fields')
        require(fields[0].isdigit() and fields[1].isdigit(), 'malformed mount IDs')
        result.append({'dev': device_number(fields[2]),
                       'path': unescape_mount(fields[4]),
                       'options': fields[5].split(',') + fields[-1].split(','),
                       'fstype': fields[separator + 1]})
    return result


def containing_mount(path, mounts):
    matches = [m for m in mounts if path == m['path'] or
               path.startswith(m['path'].rstrip('/') + '/')]
    require(bool(matches), 'path has no visible mount')
    # Equal-length stacked mountpoints are ambiguous; refuse rather than guess.
    longest = max(len(m['path']) for m in matches)
    matches = [m for m in matches if len(m['path']) == longest]
    require(len(matches) == 1, 'ambiguous stacked mountpoint')
    return matches[0]


def namespace_guard(namespace):
    path = '/proc/self/ns/' + namespace
    # proc namespace entries intentionally are kernel symlinks to nsfs objects.
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
    try:
        opened = os.fstat(fd)
        mine = os.stat(path)
        init = os.stat('/proc/1/ns/' + namespace)
        require((opened.st_dev, opened.st_ino) == (mine.st_dev, mine.st_ino) ==
                (init.st_dev, init.st_ino), 'namespace descriptor/path/PID 1 identity differs')
        require(fcntl.ioctl(fd, NS_GET_NSTYPE) == NAMESPACE_TYPES[namespace],
                'namespace type differs')
        if namespace in INITIAL_NAMESPACE_INODES:
            require(opened.st_ino == INITIAL_NAMESPACE_INODES[namespace],
                    'must use pinned kernel initial PID and user namespaces')
    finally:
        os.close(fd)


def host_context(expected_boot_id):
    require(os.geteuid() == 0, 'must run as root')
    require(str(uuid.UUID(expected_boot_id)) == expected_boot_id, 'invalid expected boot ID')
    require(read_text('/proc/sys/kernel/random/boot_id') == expected_boot_id,
            'boot ID differs from caller pin')
    require(os.uname().release == KERNEL_RELEASE, 'only pinned A733 maintenance kernel supported')
    for namespace in ('pid', 'mnt', 'user'):
        namespace_guard(namespace)
    # Secondary consistency check: a private proc mount can hide ancestor NSpid
    # fields, so the pinned initial namespace inode check above is essential.
    status = read_text('/proc/self/status')
    nspid = [line.split()[1:] for line in status.splitlines() if line.startswith('NSpid:')]
    require(len(nspid) == 1 and len(nspid[0]) == 1 and nspid[0][0].isdigit(),
            'nested or unavailable host PID namespace')
    require(read_text('/proc/self/uid_map').split() == ['0', '0', '4294967295'],
            'initial user namespace required')
    mounts = mountinfo()
    require(containing_mount('/', mounts)['fstype'] in ('rootfs', 'ramfs', 'tmpfs'),
            'offline RAM root required')
    run = containing_mount('/run', mounts)
    require(run['path'] == '/run' and run['fstype'] == 'tmpfs', 'dedicated /run tmpfs required')
    proc = containing_mount('/proc', mounts)
    require(proc['path'] == '/proc' and proc['fstype'] == 'proc', 'invalid proc mount')
    require(not any(x.startswith('hidepid=') and x != 'hidepid=0' for x in proc['options']),
            'proc hides processes')
    return mounts


def protected_ram_path(path, mounts):
    require(os.path.isabs(path) and os.path.normpath(path) == path,
            'payload/report path must be canonical absolute')
    require(path.startswith('/run/'), 'payload/report must be under /run')
    components = Path(path).parts
    current = Path('/')
    root = os.lstat(current)
    require(stat.S_ISDIR(root.st_mode) and root.st_uid == 0 and
            not root.st_mode & 0o022, 'unsafe root directory')
    for part in components[1:-1]:
        current /= part
        info = os.lstat(current)
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and
                not info.st_mode & 0o022, 'unsafe or symlinked path ancestor')
    parent = os.lstat(os.path.dirname(path))
    require(stat.S_IMODE(parent.st_mode) == 0o700, 'immediate parent must be root mode 0700')
    mount = containing_mount(path, mounts)
    require(mount['fstype'] == 'tmpfs', 'payload/report must be on proven tmpfs')
    require((os.major(parent.st_dev), os.minor(parent.st_dev)) == mount['dev'],
            'parent mount identity mismatch')


def identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def validate_source(fd, path, initial=None):
    info = os.fstat(fd)
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and
            stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1 and
            info.st_size == SOURCE_BYTES, 'source must be root 0600, single-link, exact-size regular file')
    require(identity(os.lstat(path)) == identity(info), 'source path/held descriptor changed')
    if initial is not None:
        require(identity(info) == initial, 'source identity changed')
    return identity(info)


def open_source(path, mounts, expected_sha):
    protected_ram_path(path, mounts)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        initial = validate_source(fd, path)
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        print(f'Authenticating BOOT source: {path}', file=sys.stderr, flush=True)
        checked = 0
        next_progress = time.monotonic() + 1
        digest = hashlib.sha256()
        while True:
            chunk = os.read(fd, CHUNK)
            if not chunk:
                break
            digest.update(chunk)
            checked += len(chunk)
            if time.monotonic() >= next_progress:
                print(f'Authenticated {checked}/{SOURCE_BYTES} source bytes', file=sys.stderr, flush=True)
                next_progress = time.monotonic() + 1
        require(digest.hexdigest() == expected_sha, 'source SHA-256 mismatch')
        validate_source(fd, path, initial)
        print(f'Authenticated {checked}/{SOURCE_BYTES} source bytes; SHA-256 matches.', file=sys.stderr, flush=True)
        os.lseek(fd, 0, os.SEEK_SET)
        return fd, initial
    except BaseException:
        os.close(fd)
        raise


def target_identity():
    base = Path('/sys/class/block/nvme0n1')
    require(read_text(base / 'device/serial') == SERIAL and
            read_text(base / 'device/model') == MODEL, 'NVMe identity differs')
    require(read_text(base / 'size') == str(TARGET_BYTES // 512), 'NVMe size differs')
    for field in ('logical_block_size', 'physical_block_size'):
        require(read_text(base / 'queue' / field) == '512', 'NVMe sector size differs')
    expected = {'nvme0n1', *('nvme0n1p' + str(i) for i in range(1, 6))}
    require({p.name for p in Path('/dev').glob('nvme0n1p*')} == expected - {'nvme0n1'},
            'unexpected device partition set')
    require({p.name for p in base.iterdir() if (p / 'partition').exists()} ==
            expected - {'nvme0n1'}, 'unexpected sysfs partition set')
    result = {}
    devices = set()
    for name in sorted(expected):
        path = Path('/dev') / name
        info = os.lstat(path)
        require(stat.S_ISBLK(info.st_mode), 'NVMe node must be a non-symlink block device')
        dev = (os.major(info.st_rdev), os.minor(info.st_rdev))
        node = Path('/sys/class/block') / name
        require(device_number(read_text(node / 'dev')) == dev and dev not in devices and
                Path(f'/sys/dev/block/{dev[0]}:{dev[1]}').resolve(strict=True) == node.resolve(strict=True),
                'NVMe sysfs device differs')
        require(read_text(node / 'ro') == '0', 'NVMe node is read-only')
        if name != 'nvme0n1':
            number = int(name.removeprefix('nvme0n1p'))
            start, size = GEOMETRY[number - 1]
            require(node.resolve().parent == base.resolve() and
                    read_text(node / 'partition') == str(number) and
                    read_text(node / 'start') == str(start) and
                    read_text(node / 'size') == str(size), 'NVMe partition geometry differs')
        result[name] = (info.st_dev, info.st_ino, info.st_rdev)
        devices.add(dev)
    return result, devices


def process_consumers(devices, own_fds=()):
    processes = [p for p in Path('/proc').iterdir() if p.name.isdigit()]
    require(bool(processes) and any(p.name == '1' for p in processes), 'incomplete proc census')
    for process in processes:
        try:
            threads = list((process / 'task').iterdir())
            require(bool(threads), 'empty task census')
        except (FileNotFoundError, ProcessLookupError):
            continue
        for thread in threads:
            require(thread.name.isdigit(), 'malformed task census')
            try:
                require(not any(m['dev'] in devices for m in mountinfo(read_text(thread / 'mountinfo'))),
                        'NVMe mounted in another process namespace')
                for link in [thread / 'cwd', thread / 'root', thread / 'exe',
                             *list((thread / 'fd').iterdir())]:
                    if (process.name == str(os.getpid()) and link.parent.name == 'fd' and
                            link.name.isdigit() and int(link.name) in own_fds):
                        continue
                    try:
                        info = os.stat(link)
                    except (FileNotFoundError, ProcessLookupError):
                        continue  # Closed descriptor or absent kernel-thread exe.
                    number = info.st_rdev if stat.S_ISBLK(info.st_mode) else info.st_dev
                    require((os.major(number), os.minor(number)) not in devices,
                            'process uses NVMe')
                for line in read_text(thread / 'maps').splitlines():
                    fields = line.split()
                    require(len(fields) >= 5 and re.fullmatch(r'[0-9a-f]+:[0-9a-f]+', fields[3]),
                            'malformed process maps')
                    dev = tuple(int(n, 16) for n in fields[3].split(':'))
                    require(dev not in devices, 'process maps NVMe')
            except (FileNotFoundError, ProcessLookupError):
                # An exited task may disappear between enumeration and inspection.
                require(not thread.exists(), 'incomplete census of a still-live task')


def idle_target(devices, own_fds=()):
    require(not any(m['dev'] in devices for m in mountinfo()), 'NVMe is mounted')
    swaps = read_text('/proc/swaps').splitlines()
    require(bool(swaps) and swaps[0].split() == ['Filename', 'Type', 'Size', 'Used', 'Priority'],
            'malformed swaps header')
    for line in swaps[1:]:
        fields = line.split()
        require(len(fields) == 5 and fields[1] in ('partition', 'file') and
                all(re.fullmatch(r'-?[0-9]+', n) for n in fields[2:]), 'malformed swap entry')
        info = os.stat(unescape_mount(fields[0]))
        number = info.st_rdev if stat.S_ISBLK(info.st_mode) else info.st_dev
        require((os.major(number), os.minor(number)) not in devices, 'NVMe is used for swap')
    for major, minor in devices:
        require(not list(Path(f'/sys/dev/block/{major}:{minor}/holders').iterdir()),
                'NVMe has holders')
    process_consumers(devices, own_fds)


def block_integer(fd, request, fmt):
    value = bytearray(struct.calcsize(fmt))
    fcntl.ioctl(fd, request, value, True)
    return struct.unpack(fmt, value)[0]


def validate_open_target(fd, path, before, size):
    info = os.fstat(fd)
    require(stat.S_ISBLK(info.st_mode) and
            (info.st_dev, info.st_ino, info.st_rdev) == before[Path(path).name],
            'opened target differs')
    for request, fmt, expected in ((BLKGETSIZE64, '=Q', size), (BLKSSZGET, '=I', 512),
                                   (BLKPBSZGET, '=I', 512), (BLKROGET, '=I', 0)):
        require(block_integer(fd, request, fmt) == expected, 'opened device geometry/RO differs')


def verify_gpt(fd):
    for offset, length, expected in GPT_EXTENTS:
        data = os.pread(fd, length, offset)
        require(len(data) == length and hashlib.sha256(data).hexdigest() == expected,
                'installed GPT differs from exact pin')


@contextmanager
def opened_target(expected_boot_id, writable=False):
    host_context(expected_boot_id)
    before, devices = target_identity()
    idle_target(devices)
    disk_fd = part_fd = None
    try:
        # Only p1 receives write capability. The namespace descriptor stays read-only.
        disk_fd = os.open(DISK, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        part_fd = os.open(TARGET, (os.O_RDWR if writable else os.O_RDONLY) |
                          os.O_NOFOLLOW | os.O_CLOEXEC | os.O_EXCL)
        fcntl.flock(part_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        validate_open_target(disk_fd, DISK, before, TARGET_BYTES)
        validate_open_target(part_fd, TARGET, before, SOURCE_BYTES)
        require(target_identity() == (before, devices), 'target identity changed after opening')
        idle_target(devices, (disk_fd, part_fd))
        verify_gpt(disk_fd)
        yield part_fd, disk_fd
        host_context(expected_boot_id)
        require(target_identity() == (before, devices), 'target identity changed after operation')
        idle_target(devices, (disk_fd, part_fd))
        verify_gpt(disk_fd)
    finally:
        if part_fd is not None:
            os.close(part_fd)
        if disk_fd is not None:
            os.close(disk_fd)


def hash_extent(fd, length=SOURCE_BYTES):
    digest = hashlib.sha256()
    offset = 0
    while offset < length:
        data = os.pread(fd, min(CHUNK, length - offset), offset)
        require(bool(data), 'unexpected extent EOF')
        digest.update(data)
        offset += len(data)
    return digest.hexdigest()


def stream_write(source_fd, target_fd, length, progress=None):
    """Copy exactly length including zeros, never touch bytes outside the bound."""
    written = 0
    next_progress = time.monotonic() + 1
    while written < length:
        chunk = os.read(source_fd, min(CHUNK, length - written))
        require(bool(chunk), 'unexpected source EOF')
        offset = 0
        while offset < len(chunk):
            count = os.write(target_fd, memoryview(chunk)[offset:])
            require(0 < count <= len(chunk) - offset, 'invalid or zero target write')
            offset += count
            written += count
            if progress and time.monotonic() >= next_progress:
                progress(written, length)
                next_progress = time.monotonic() + 1
    if progress:
        progress(written, length)
    return written


def progress(count, total):
    print(f'NVMe BOOT: {count}/{total} bytes', file=sys.stderr, flush=True)


def fresh_output(path, mounts):
    protected_ram_path(path, mounts)
    return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)


def read_receipt(path, mounts, expected_sha, expected_boot_id):
    require(re.fullmatch(r'[0-9a-f]{64}', expected_sha) is not None, 'invalid receipt SHA-256 pin')
    protected_ram_path(path, mounts)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1 and
                stat.S_IMODE(info.st_mode) == 0o600 and 0 < info.st_size <= 16384,
                'receipt must be bounded root 0600 single-link regular file')
        before = identity(info)
        data = os.read(fd, 16385)
        require(hashlib.sha256(data).hexdigest() == expected_sha, 'receipt SHA-256 differs')
        require(identity(os.fstat(fd)) == before and identity(os.lstat(path)) == before,
                'receipt changed while reading')
        receipt = json.loads(data)
    finally:
        os.close(fd)
    require(isinstance(receipt, dict) and receipt.get('schema') == 1 and
            receipt.get('operation') == 'known-nvme-boot-offdevice-backup' and
            receipt.get('serial') == SERIAL and receipt.get('bytes') == SOURCE_BYTES and
            receipt.get('sha256') == OLD_SHA256 and
            receipt.get('export_boot_id') == expected_boot_id and
            receipt.get('authenticated_transfer_verified') is True and
            isinstance(receipt.get('remote_host'), str) and bool(receipt['remote_host'].strip()) and
            isinstance(receipt.get('remote_path'), str) and receipt['remote_path'].startswith('/'),
            'off-device backup receipt fields differ')
    return receipt


def export_backup(path, expected_boot_id, mounts):
    output_fd = fresh_output(path, mounts)
    try:
        with opened_target(expected_boot_id) as (part_fd, _):
            count = stream_write(part_fd, output_fd, SOURCE_BYTES, progress)
            os.fsync(output_fd)
            require(count == SOURCE_BYTES, 'incomplete backup')
        os.close(output_fd)
        output_fd = None
        source_fd, _ = open_source(path, mounts, OLD_SHA256)
        os.close(source_fd)
    finally:
        if output_fd is not None:
            os.close(output_fd)
    return {'backup_path': path, 'bytes': SOURCE_BYTES, 'sha256': OLD_SHA256,
            'export_boot_id': expected_boot_id, 'status': 'export-complete'}


def inspect_target(expected_boot_id):
    """Observe the fixed BOOT target under the same exclusive read-only claim."""
    with opened_target(expected_boot_id) as (part_fd, _):
        before, _ = target_identity()
        print('Inspecting live BOOT SHA-256...', file=sys.stderr, flush=True)
        current_sha = hash_extent(part_fd, SOURCE_BYTES)
    return {'status': 'inspect-complete', 'current_sha256': current_sha,
            'observed_boot_id': expected_boot_id, 'bytes_written': 0, 'write_bytes': 0,
            'read_only': True,
            'model': MODEL, 'namespace_bytes': TARGET_BYTES, 'sector_bytes': 512,
            'target_identity': before, 'partition_geometry_sectors': GEOMETRY,
            'gpt_extents': [{'offset': offset, 'bytes': length, 'sha256': digest}
                            for offset, length, digest in GPT_EXTENTS],
            'gpt_verified': True}


def apply_payload(path, expected_boot_id, mounts, rollback=False, readback=False,
                  recovery_current_sha=None):
    if recovery_current_sha is not None:
        require(not rollback and re.fullmatch(r'[0-9a-f]{64}', recovery_current_sha) is not None,
                'recovery requires a lowercase SHA-256 current-target pin')
        current_sha, source_sha = recovery_current_sha, OLD_SHA256
    else:
        current_sha, source_sha = ((NEW_SHA256, OLD_SHA256) if rollback else (OLD_SHA256, NEW_SHA256))
    source_fd, source_identity = open_source(path, mounts, source_sha)
    try:
        with opened_target(expected_boot_id, writable=True) as (part_fd, disk_fd):
            print('Checking live predecessor BOOT SHA-256...', file=sys.stderr, flush=True)
            require(hash_extent(part_fd, SOURCE_BYTES) == current_sha, 'live BOOT hash differs from required predecessor')
            host_context(expected_boot_id)
            before, devices = target_identity()
            validate_open_target(part_fd, TARGET, before, SOURCE_BYTES)
            validate_open_target(disk_fd, DISK, before, TARGET_BYTES)
            idle_target(devices, (disk_fd, part_fd))
            verify_gpt(disk_fd)
            validate_source(source_fd, path, source_identity)
            if recovery_current_sha is not None:
                print('Rechecking caller-pinned BOOT immediately before recovery...',
                      file=sys.stderr, flush=True)
                require(hash_extent(part_fd, SOURCE_BYTES) == current_sha,
                        'live BOOT changed since caller inspection or recovery admission')
                validate_source(source_fd, path, source_identity)
            os.lseek(source_fd, 0, os.SEEK_SET)
            os.lseek(part_fd, 0, os.SEEK_SET)
            count = stream_write(source_fd, part_fd, SOURCE_BYTES, progress)
            validate_source(source_fd, path, source_identity)
            print('Flushing NVMe BOOT (fsync and BLKFLSBUF)...', file=sys.stderr, flush=True)
            os.fsync(part_fd)
            fcntl.ioctl(part_fd, BLKFLSBUF)
            if readback:
                require(hash_extent(part_fd, SOURCE_BYTES) == source_sha, 'explicit BOOT readback failed')
        return {'status': ('recovery-complete' if recovery_current_sha is not None else
                           'rollback-complete' if rollback else 'write-complete'),
                'predecessor_sha256': current_sha,
                'bytes_written': count, 'source_sha256': source_sha, 'readback': readback}
    finally:
        os.close(source_fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('export-backup', 'apply', 'rollback', 'inspect', 'recover-backup'))
    parser.add_argument('payload', nargs='?', help='fresh backup output or raw input in root0700 /run tmpfs directory')
    parser.add_argument('--expected-boot-id', required=True)
    parser.add_argument('--backup-receipt')
    parser.add_argument('--backup-receipt-sha256')
    parser.add_argument('--expected-current-sha256', help='recover-backup only: fresh inspect current_sha256')
    parser.add_argument('--readback', action='store_true', help='optional complete p1 readback after write')
    parser.add_argument('--report', required=True, help='fresh protected /run JSON result')
    args = parser.parse_args(argv)
    report_fd = None
    report = {'operation': 'known-nvme-boot-' + args.operation, 'status': 'failed-or-refused',
              'target': TARGET, 'serial': SERIAL, 'write_offset': 0, 'namespace_offset': 1048576,
              'write_bytes': 0 if args.operation == 'inspect' else SOURCE_BYTES,
              'expected_boot_id': args.expected_boot_id,
              'authorization': AUTHORIZATION, 'hardware_acceptance': False, 'bytes_written': 0}
    try:
        mounts = host_context(args.expected_boot_id)
        require(args.report != args.payload and args.report != args.backup_receipt,
                'report must be distinct from inputs')
        report_fd = fresh_output(args.report, mounts)
        print(json.dumps(report, sort_keys=True), file=sys.stderr, flush=True)
        if args.operation == 'inspect':
            require(args.payload is None and not args.readback and not args.backup_receipt and
                    not args.backup_receipt_sha256 and args.expected_current_sha256 is None,
                    'inspect takes no payload, receipt, readback or current-target pin')
            report.update(inspect_target(args.expected_boot_id))
        elif args.operation == 'export-backup':
            require(args.payload is not None, 'backup output payload path required')
            require(args.expected_current_sha256 is None, 'current-target pin is recovery-only')
            require(not args.readback and not args.backup_receipt and not args.backup_receipt_sha256,
                    'export takes no receipt or readback')
            report.update(export_backup(args.payload, args.expected_boot_id, mounts))
        else:
            require(args.payload is not None, 'input payload path required')
            if args.operation == 'recover-backup':
                require(args.expected_current_sha256 is not None and
                        re.fullmatch(r'[0-9a-f]{64}', args.expected_current_sha256) is not None,
                        'recover-backup requires --expected-current-sha256 from fresh inspect')
                report['expected_current_sha256'] = args.expected_current_sha256
            else:
                require(args.expected_current_sha256 is None, 'current-target pin is recovery-only')
            require(args.backup_receipt and args.backup_receipt_sha256, 'pinned off-device backup receipt required')
            # Rollback may occur in a later offline boot: its caller must create a new
            # authenticated receipt for the retained old backup bound to that boot ID.
            receipt = read_receipt(args.backup_receipt, mounts, args.backup_receipt_sha256,
                                   args.expected_boot_id)
            report['backup_receipt_sha256'] = args.backup_receipt_sha256
            report['backup_remote_host'] = receipt['remote_host']
            report['bytes_written'] = None  # Any subsequent failure may leave a partial p1.
            report.update(apply_payload(args.payload, args.expected_boot_id, mounts,
                                        args.operation == 'rollback', args.readback,
                                        args.expected_current_sha256))
        result = 0
    except (OSError, ValueError, Refusal) as error:
        report['error'] = str(error)
        print(f'Refused/failed: {error}', file=sys.stderr, flush=True)
        result = 1
    try:
        data = (json.dumps(report, sort_keys=True) + '\n').encode('ascii')
        if report_fd is not None:
            offset = 0
            while offset < len(data):
                count = os.write(report_fd, data[offset:])
                require(count > 0, 'zero report write')
                offset += count
            os.fsync(report_fd)
        print(data.decode('ascii'), end='', flush=True)
    finally:
        if report_fd is not None:
            os.close(report_fd)
    return result


if __name__ == '__main__':
    sys.exit(main())
