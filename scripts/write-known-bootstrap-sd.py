#!/usr/bin/env python3
"""One preauthorized A733 maintenance SD and one authenticated RAM payload."""
import argparse
import errno
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
from types import MappingProxyType

TARGET = '/dev/mmcblk1'
TARGET_DEV = (179, 0)
TARGET_BYTES = 1977614336
SOURCE_BYTES = 268435456
SOURCE_SHA256 = 'ab5e8c2c846cbbd51b61bef6831d7e4d1498c29fefbf47d74301b1f3bb0ad207'
DEFAULT_PROFILE = 'protected-state-20261109'
PROFILES = MappingProxyType({
    DEFAULT_PROFILE: (SOURCE_BYTES, SOURCE_SHA256),
    'nvme-first-trial-20261109': (268435456,
        '8470ff59290016bb2f99c051f371ab20ac431e75fd17dec4e0e0de5c665613d9'),
})
# Pinned vendor include/linux/proc_ns.h, revision
# 2ac08e8c7cdc28abbdc5c9a9dd812f887ae9c79f, lines 41–42.
INITIAL_NAMESPACES = {'pid': 0xEFFFFFFC, 'user': 0xEFFFFFFD}
CID = '035344535530324780716f216e00a8f5'
AUTHORIZATION = 'docs/evidence/storage-write-authorization-20261108.json'
BLKGETSIZE64 = 0x80081272
BLKSSZGET = 0x1268
BLKROGET = 0x125e
BLKFLSBUF = 0x1261
CHUNK = 1024 * 1024


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


def mountinfo():
    result = []
    raw = read_text('/proc/self/mountinfo')
    require(bool(raw), 'empty mountinfo')
    for line in raw.splitlines():
        fields = line.split()
        require(fields.count('-') == 1, 'malformed mountinfo separator')
        separator = fields.index('-')
        require(separator >= 6 and len(fields) == separator + 4,
                'malformed mountinfo fields')
        require(fields[0].isdigit() and fields[1].isdigit(), 'malformed mount IDs')
        result.append({'dev': device_number(fields[2]),
                       'root': unescape_mount(fields[3]),
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


def host_context(expected_boot_id):
    require(os.geteuid() == 0, 'must run as root')
    require(str(uuid.UUID(expected_boot_id)) == expected_boot_id, 'invalid expected boot ID')
    require(read_text('/proc/sys/kernel/random/boot_id') == expected_boot_id,
            'boot ID differs from caller pin')
    for namespace in ('pid', 'user', 'mnt'):
        mine = os.stat('/proc/self/ns/' + namespace)
        init = os.stat('/proc/1/ns/' + namespace)
        require((mine.st_dev, mine.st_ino) == (init.st_dev, init.st_ino),
                'must share PID 1 host PID and mount namespaces')
        if namespace in INITIAL_NAMESPACES:
            require(mine.st_ino == INITIAL_NAMESPACES[namespace],
                    'must use initial PID and user namespaces')
    # Corroborate the pinned namespace identities; NSpid alone is insufficient
    # when a nested PID namespace has a privately remounted proc filesystem.
    status = read_text('/proc/self/status')
    nspid = [line.split()[1:] for line in status.splitlines() if line.startswith('NSpid:')]
    require(len(nspid) == 1 and len(nspid[0]) == 1 and nspid[0][0] == str(os.getpid()),
            'nested or unavailable host PID namespace')
    mounts = mountinfo()
    proc = containing_mount('/proc', mounts)
    proc_stat = os.stat('/proc')
    require(proc['path'] == '/proc' and proc['root'] == '/' and
            proc['fstype'] == 'proc' and
            proc['dev'] == (os.major(proc_stat.st_dev), os.minor(proc_stat.st_dev)),
            'invalid or partial proc mount')
    require(not any(x.startswith('hidepid=') and x != 'hidepid=0' for x in proc['options']),
            'proc hides processes')
    require(not any(x.startswith('subset=') and x != 'subset=all' for x in proc['options']),
            'proc restricts process visibility')
    for mount in mounts:
        if mount['path'].startswith('/proc/'):
            require(mount['path'] == '/proc/sys/fs/binfmt_misc' and
                    mount['fstype'] == 'binfmt_misc' and mount['root'] == '/',
                    'unexpected proc submount')
    require_single_thread()
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


def validate_source(fd, path, initial=None, profile=DEFAULT_PROFILE):
    length, _ = PROFILES[profile]
    info = os.fstat(fd)
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and
            stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1 and
            info.st_size == length, 'source must be root 0600, single-link, exact-size regular file')
    require(identity(os.lstat(path)) == identity(info), 'source path/held descriptor changed')
    if initial is not None:
        require(identity(info) == initial, 'source identity changed')
    return identity(info)


def open_source(path, mounts, profile=DEFAULT_PROFILE):
    _, expected_hash = PROFILES[profile]
    protected_ram_path(path, mounts)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        initial = validate_source(fd, path, profile=profile)
        digest = hashlib.sha256()
        while True:
            chunk = os.read(fd, CHUNK)
            if not chunk:
                break
            digest.update(chunk)
        require(digest.hexdigest() == expected_hash, 'source SHA-256 mismatch')
        validate_source(fd, path, initial, profile=profile)
        os.lseek(fd, 0, os.SEEK_SET)
        return fd, initial
    except BaseException:
        os.close(fd)
        raise


def target_identity():
    info = os.lstat(TARGET)
    require(stat.S_ISBLK(info.st_mode) and
            (os.major(info.st_rdev), os.minor(info.st_rdev)) == TARGET_DEV,
            'target path is not exact non-symlink block device')
    base = Path('/sys/class/block/mmcblk1')
    require(device_number(read_text(base / 'dev')) == TARGET_DEV, 'sysfs target device mismatch')
    require(read_text(base / 'device/cid') == CID, 'SD CID mismatch')
    require(read_text(base / 'device/name') == 'SU02G', 'SD model mismatch')
    require(read_text(base / 'ro') == '0', 'SD is read-only')
    require(read_text(base / 'size') == str(TARGET_BYTES // 512), 'SD size mismatch')
    require(read_text(base / 'queue/logical_block_size') == '512', 'SD logical sector mismatch')
    devices = {TARGET_DEV}
    for child in base.iterdir():
        partition_name = re.fullmatch(r'mmcblk1p([1-9][0-9]*)', child.name)
        try:
            os.stat(child / 'partition')
        except (FileNotFoundError, NotADirectoryError):
            require(partition_name is None, 'SD partition probe disappeared or is missing')
            continue
        require(partition_name is not None, 'unexpected SD partition name')
        partition = read_text(child / 'partition')
        require(partition == partition_name[1], 'malformed partition number')
        number = device_number(read_text(child / 'dev'))
        require(number[0] == TARGET_DEV[0] and number not in devices,
                'unexpected or duplicate SD partition device')
        devices.add(number)
    return (info.st_dev, info.st_ino, info.st_rdev), devices


def ordinary_exit(error):
    return error.errno in (errno.ENOENT, errno.ESRCH)


def confirmed_gone(path):
    try:
        os.stat(path)
    except OSError as error:
        if ordinary_exit(error):
            return True
        raise
    return False


def require_single_thread():
    tasks = list(Path('/proc/self/task').iterdir())
    require(len(tasks) == 1 and tasks[0].name == str(os.getpid()),
            'writer must remain single-threaded')


def process_consumers(devices, own_fd=None):
    # This census assumes a trusted, quiescent root environment. It is not an
    # atomic exclusion mechanism against new consumers or hostile root actors.
    require_single_thread()
    pids = [p for p in Path('/proc').iterdir() if p.name.isdigit()]
    require(bool(pids) and any(p.name == '1' for p in pids), 'incomplete proc process visibility')
    for process in pids:
        try:
            tasks = list((process / 'task').iterdir())
            require(bool(tasks), 'empty process task table')
            for task in tasks:
                require(task.name.isdigit(), 'malformed process task')
                try:
                    descriptors = list((task / 'fd').iterdir())
                    for descriptor in descriptors:
                        require(descriptor.name.isdigit(), 'malformed process descriptor')
                        if (process.name == task.name == str(os.getpid()) and
                                int(descriptor.name) == own_fd):
                            continue
                        try:
                            reject_block_consumer(os.stat(descriptor), devices, 'descriptor')
                        except OSError as error:
                            if not ordinary_exit(error):
                                raise
                    # A block-device mapping can survive close(fd). The maps
                    # device column describes the node's filesystem st_dev,
                    # never the underlying block device's st_rdev.
                    maps = read_text(task / 'maps')
                    for line in maps.splitlines():
                        fields = line.split(maxsplit=5)
                        require(len(fields) >= 5 and
                                re.fullmatch(r'[0-9a-f]+-[0-9a-f]+', fields[0]) and
                                re.fullmatch(r'[r-][w-][x-][ps]', fields[1]) and
                                re.fullmatch(r'[0-9a-f]+', fields[2]) and
                                re.fullmatch(r'[0-9a-f]+:[0-9a-f]+', fields[3]) and
                                fields[4].isdigit(), 'malformed process maps')
                        start, end = (int(x, 16) for x in fields[0].split('-'))
                        require(start < end, 'malformed process map range')
                        if int(fields[4]) == 0:
                            continue
                        try:
                            # Linux exposes map_files through direct /proc/TID,
                            # but omits it from /proc/PID/task/TID.
                            # maps pads low addresses; map_files names do not.
                            map_name = f'{start:x}-{end:x}'
                            mapped = os.stat(Path('/proc') / task.name / 'map_files' / map_name)
                        except OSError as error:
                            if ordinary_exit(error):
                                if confirmed_gone(task):
                                    continue
                                remaining = read_text(task / 'maps').splitlines()
                                require(not any(row.split()[0] == fields[0] for row in remaining
                                                if row.split()),
                                        'surviving process mapping is unscannable')
                                continue
                            raise
                        reject_block_consumer(mapped, devices, 'mapping')
                except OSError as error:
                    if not ordinary_exit(error):
                        raise
                    require(confirmed_gone(task), 'surviving process task is unscannable')
        except OSError as error:
            if not ordinary_exit(error):
                raise
            require(confirmed_gone(process), 'surviving process is unscannable')


def reject_block_consumer(info, devices, kind):
    if stat.S_ISBLK(info.st_mode):
        require((os.major(info.st_rdev), os.minor(info.st_rdev)) not in devices,
                'SD block device has an open process ' + kind)


def idle_target(devices, own_fd=None):
    mounts = mountinfo()
    require(not any(m['dev'] in devices for m in mounts), 'SD or partition is mounted')
    swaps = read_text('/proc/swaps').splitlines()
    require(bool(swaps) and swaps[0].split() == ['Filename', 'Type', 'Size', 'Used', 'Priority'],
            'malformed swaps header')
    for line in swaps[1:]:
        fields = line.split()
        require(len(fields) == 5 and fields[1] in ('partition', 'file') and
                all(re.fullmatch(r'-?[0-9]+', n) for n in fields[2:]), 'malformed swap entry')
        info = os.stat(unescape_mount(fields[0]))
        number = info.st_rdev if stat.S_ISBLK(info.st_mode) else info.st_dev
        require((os.major(number), os.minor(number)) not in devices, 'SD is used for swap')
    for major, minor in devices:
        require(not list(Path(f'/sys/dev/block/{major}:{minor}/holders').iterdir()),
                'SD or partition has holders')
    process_consumers(devices, own_fd)


def block_integer(fd, request, fmt):
    value = bytearray(struct.calcsize(fmt))
    fcntl.ioctl(fd, request, value, True)
    return struct.unpack(fmt, value)[0]


def validate_open_target(fd):
    info = os.fstat(fd)
    require(stat.S_ISBLK(info.st_mode) and
            (os.major(info.st_rdev), os.minor(info.st_rdev)) == TARGET_DEV,
            'opened target descriptor identity mismatch')
    require(block_integer(fd, BLKGETSIZE64, '=Q') == TARGET_BYTES, 'opened SD size mismatch')
    require(block_integer(fd, BLKSSZGET, '=I') == 512, 'opened SD sector mismatch')
    require(block_integer(fd, BLKROGET, '=I') == 0, 'opened SD is read-only')


def stream_write(source_fd, target_fd, length, progress=None):
    """Write exactly length bytes, including zeros; never read beyond the bound."""
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


def write_payload(source_fd, source_path, source_identity, expected_boot_id, before,
                  profile=DEFAULT_PROFILE):
    length, _ = PROFILES[profile]
    host_context(expected_boot_id)
    validate_source(source_fd, source_path, source_identity, profile=profile)
    current, devices = target_identity()
    require(current == before, 'target identity changed before opening')
    idle_target(devices)
    # O_EXCL asks Linux for a block-device claim and rejects mounted devices.
    fd = os.open(TARGET, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_EXCL)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        validate_open_target(fd)
        current, devices = target_identity()
        require(current == before, 'target identity changed after opening')
        opened = os.fstat(fd)
        require((opened.st_dev, opened.st_ino, opened.st_rdev) == current,
                'target path no longer names opened block device')
        idle_target(devices, own_fd=fd)
        validate_source(source_fd, source_path, source_identity, profile=profile)
        os.lseek(source_fd, 0, os.SEEK_SET)
        count = stream_write(source_fd, fd, length,
                             lambda n, total: print(f'Written {n}/{total} bytes', file=sys.stderr, flush=True))
        validate_source(source_fd, source_path, source_identity, profile=profile)
        print('Flushing SD writes (fsync and BLKFLSBUF)...', file=sys.stderr, flush=True)
        os.fsync(fd)
        fcntl.ioctl(fd, BLKFLSBUF)
        print('SD flush complete.', file=sys.stderr, flush=True)
        return count
    finally:
        os.close(fd)


def emit_report(report, report_fd=None):
    encoded = (json.dumps(report, sort_keys=True) + '\n').encode('ascii')
    if report_fd is not None:
        offset = 0
        while offset < len(encoded):
            count = os.write(report_fd, encoded[offset:])
            require(count > 0, 'short report write')
            offset += count
        os.fsync(report_fd)
    print(encoded.decode('ascii'), end='', flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', help='authenticated raw payload in a root 0700 directory on /run tmpfs')
    parser.add_argument('--expected-boot-id', required=True)
    parser.add_argument('--profile', choices=tuple(PROFILES), default=DEFAULT_PROFILE)
    parser.add_argument('--write', action='store_true', help='execute this preauthorized maintenance write')
    parser.add_argument('--report', help='fresh JSON report path in a root 0700 directory on /run tmpfs')
    args = parser.parse_args(argv)
    length, expected_hash = PROFILES[args.profile]
    source_fd = report_fd = None
    report = {'operation': 'known-bootstrap-sd-maintenance', 'status': 'refused',
              'profile': args.profile, 'hardware_acceptance': False, 'bytes_written': 0,
              'authorization': AUTHORIZATION, 'expected_boot_id': args.expected_boot_id,
              'target': {'path': TARGET, 'major_minor': '179:0', 'model': 'SU02G',
                         'cid': CID, 'bytes': TARGET_BYTES, 'write_offset': 0,
                         'write_bytes': length},
              'source': {'path': args.image, 'bytes': length, 'sha256': expected_hash}}
    try:
        mounts = host_context(args.expected_boot_id)
        source_fd, source_identity = open_source(args.image, mounts, profile=args.profile)
        before, devices = target_identity()
        idle_target(devices)
        if args.report:
            protected_ram_path(args.report, mounts)
            report_fd = os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                                os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        report['status'] = 'plan'
        if args.write:
            print(json.dumps(report, sort_keys=True), file=sys.stderr, flush=True)
            report['bytes_written'] = write_payload(source_fd, args.image, source_identity,
                                                    args.expected_boot_id, before, profile=args.profile)
            report['status'] = 'write-complete'
        emit_report(report, report_fd)
        return 0
    except (OSError, ValueError, Refusal) as error:
        report['status'] = 'failed-or-refused'
        report['error'] = str(error)
        # A failed write may have partially changed the SD. Do not claim zero bytes.
        if args.write:
            report['bytes_written'] = None
        try:
            emit_report(report, report_fd)
        except OSError as report_error:
            print(json.dumps(report, sort_keys=True), flush=True)
            print(f'Report write also failed: {report_error}', file=sys.stderr, flush=True)
        print(f'Refused/failed: {error}', file=sys.stderr, flush=True)
        return 1
    finally:
        if source_fd is not None:
            os.close(source_fd)
        if report_fd is not None:
            os.close(report_fd)


if __name__ == '__main__':
    sys.exit(main())
