#!/usr/bin/env python3
"""Prepare/apply a pinned, offline, same-board p2/p3/p4 raw backup cohort.

No mount, replay, selector edit, reboot or automatic rollback is performed.
An interrupted apply is nontransactional: remain in RAM and retry the full set.
"""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import struct
import sys
import time
import types
import uuid
import zlib

HELPER_SHA256 = '39adee8bd81186bc1665701843d393c9ceb7f16466fca304b6a3cf43993999d9'
HELPER_BYTES = 28670
STATE_UUID = '360dfb39-f49f-487e-b307-0bb887c854c5'
SIZES = {2: 4294967296, 3: 4294967296, 4: 8589934592}
UUIDS = {2: 'a7330000-0000-4000-8000-00000000000a',
         3: 'a7330000-0000-4000-8000-00000000000b', 4: STATE_UUID}
CHUNK = 1024 * 1024
LOCK = '/run/a733-recovery.lock'


class Refusal(RuntimeError):
    pass


def require(ok, reason):
    if not ok:
        raise Refusal(reason)


def stable_identity(info):
    # Reading a freshly staged helper may update atime; all mutation fields remain pinned.
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def policy():
    """Authenticate the entire stdlib-only policy before executing any of it."""
    path = Path(__file__).absolute().with_name('write-known-nvme-boot.py')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_size == HELPER_BYTES,
                'policy helper size/type differs')
        data = os.read(fd, HELPER_BYTES + 1)
        require(len(data) == HELPER_BYTES and hashlib.sha256(data).hexdigest() == HELPER_SHA256,
                'policy helper SHA-256 differs')
        require(stable_identity(os.fstat(fd)) == stable_identity(before) ==
                stable_identity(os.lstat(path)), 'policy helper changed')
    finally:
        os.close(fd)
    module = types.ModuleType('authenticated_boot_policy')
    module.__file__ = str(path)
    exec(compile(data, str(path), 'exec'), module.__dict__)
    return module


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode('ascii')


def sha_pin(value):
    require(isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value), 'invalid SHA-256 pin')
    return value


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate JSON key')
        result[key] = value
    return result


def parse(data):
    return json.loads(data, object_pairs_hook=unique_object)


def validate_index(index, boot):
    require(isinstance(index, dict) and set(index) == {
        'schema', 'operation', 'serial', 'model', 'namespace_bytes', 'state_uuid',
        'export_boot_id', 'boot_sha256', 'exported_cohort', 'offdevice_verified', 'partitions'},
        'unexpected backup index fields')
    require(type(index['schema']) is int and index['schema'] == 1 and
            index['operation'] == 'known-host-paired-backup' and
            index['serial'] == boot.SERIAL and index['model'] == boot.MODEL and
            type(index['namespace_bytes']) is int and index['namespace_bytes'] == boot.TARGET_BYTES and
            index['state_uuid'] == STATE_UUID and index['boot_sha256'] == boot.NEW_SHA256 and
            index['exported_cohort'] is True and index['offdevice_verified'] is True,
            'backup cohort identity/verification differs')
    require(str(uuid.UUID(index['export_boot_id'])) == index['export_boot_id'], 'invalid export boot ID')
    require(isinstance(index['partitions'], list) and len(index['partitions']) == 3,
            'exactly three paired partitions required')
    for number, entry in zip(SIZES, index['partitions']):
        require(isinstance(entry, dict) and set(entry) == {
            'number', 'archive', 'encoded_bytes', 'encoded_sha256', 'raw_bytes', 'raw_sha256'},
            'unexpected partition fields')
        require(type(entry['number']) is int and entry['number'] == number and
                entry['archive'] == f'p{number}.img.gz' and
                type(entry['raw_bytes']) is int and entry['raw_bytes'] == SIZES[number] and
                type(entry['encoded_bytes']) is int and 0 < entry['encoded_bytes'] <= 2 * 1024**3,
                'partition path/size/order differs')
        sha_pin(entry['encoded_sha256'])
        sha_pin(entry['raw_sha256'])
    require(sum(e['encoded_bytes'] for e in index['partitions']) <= 2 * 1024**3,
            'compressed cohort exceeds 2 GiB RAM budget')
    return index


def source_identity(boot, fd, path, size, before=None):
    info = os.fstat(fd)
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_gid == 0 and
            stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1 and info.st_size == size,
            'source must be exact-size root:root 0600 single-link regular file')
    identity = boot.identity(info)
    require(boot.identity(os.lstat(path)) == identity and
            (before is None or before == identity), 'source identity changed')
    return identity


@contextlib.contextmanager
def source(boot, mounts, path, size=None):
    boot.protected_ram_path(path, mounts)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        if size is None:
            size = os.fstat(fd).st_size
            require(0 < size <= 16384, 'JSON input exceeds bound')
        before = source_identity(boot, fd, path, size)
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        yield fd, before, size
        source_identity(boot, fd, path, size, before)
    finally:
        os.close(fd)


class Progress:
    def __init__(self, label, total):
        self.label, self.total, self.next = label, total, 0

    def __call__(self, count, final=False):
        now = time.monotonic()
        if final or now >= self.next:
            print(f'{self.label}: {count}/{self.total} bytes', file=sys.stderr, flush=True)
            self.next = now + 1


def hash_fd(fd, size, label):
    digest, offset = hashlib.sha256(), 0
    progress = Progress(label, size)
    while offset < size:
        data = os.pread(fd, min(CHUNK, size - offset), offset)
        require(bool(data), 'unexpected input EOF')
        digest.update(data)
        offset += len(data)
        progress(offset)
    progress(offset, True)
    return digest.hexdigest()


def pinned_json(fd, size, pin):
    data = os.pread(fd, size + 1, 0)
    require(len(data) == size and hashlib.sha256(data).hexdigest() == sha_pin(pin),
            'JSON input SHA-256 differs')
    return parse(data)


def archive_prefix(fd):
    decoder, offset, result = zlib.decompressobj(31), 0, bytearray()
    while len(result) < 2048:
        encoded = os.pread(fd, CHUNK, offset)
        require(bool(encoded), 'short archived filesystem prefix')
        offset += len(encoded)
        result.extend(decoder.decompress(encoded, 2048 - len(result)))
        require(not decoder.eof or len(result) == 2048, 'short archived filesystem')
    return bytes(result[1024:2048])


def validate_superblock(data, number):
    u32 = lambda offset: struct.unpack_from('<I', data, offset)[0]
    require(len(data) == 1024 and struct.unpack_from('<H', data, 0x38)[0] == 0xef53 and
            str(uuid.UUID(bytes=data[0x68:0x78])) == UUIDS[number], 'archived filesystem type/UUID differs')
    shift = u32(0x18)
    require(shift <= 6, 'invalid archived ext4 block size')
    blocks = u32(4) | ((u32(0x150) << 32) if u32(0x60) & 0x80 else 0)
    require((u32(0x60) & 0x80 or not u32(0x150)) and
            blocks * (1024 << shift) == SIZES[number], 'archived filesystem size differs')
    if number == 4:
        require(u32(0x5c) & 4 and u32(0xe0) == 8 and u32(0xe4) == 0 and
                data[0xd0:0xe0] == bytes(16) and not u32(0x60) & 8,
                'archived STATE requires fixed internal ext4 journal')


def context(boot, expected):
    mounts = boot.host_context(expected)
    require(os.uname().machine == 'aarch64', 'AArch64 required')
    swaps = boot.read_text('/proc/swaps').splitlines()
    require(len(swaps) == 1 and swaps[0].split() ==
            ['Filename', 'Type', 'Size', 'Used', 'Priority'], 'all active swap forbidden')
    fields = dict(line.split(':', 1) for line in boot.read_text('/proc/meminfo').splitlines())
    available = fields.get('MemAvailable', '').split()
    require(len(available) == 2 and available[1] == 'kB' and available[0].isdigit() and
            int(available[0]) * 1024 >= 3 * 1024**3, 'at least 3 GiB MemAvailable required')
    return mounts


@contextlib.contextmanager
def recovery_lock():
    parent = os.open('/run', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    fd = None
    try:
        info = os.fstat(parent)
        require(info.st_uid == 0 and not info.st_mode & 0o022, 'unsafe /run')
        fd = os.open('a733-recovery.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW |
                     os.O_CLOEXEC | os.O_NONBLOCK, 0o600, dir_fd=parent)
        initial = os.fstat(fd)
        require(stat.S_ISREG(initial.st_mode) and initial.st_uid == initial.st_gid == 0 and
                initial.st_nlink == 1 and stat.S_IMODE(initial.st_mode) == 0o600 and
                initial.st_dev == info.st_dev, 'unsafe recovery lock')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(os.stat('a733-recovery.lock', dir_fd=parent, follow_symlinks=False) == initial,
                'recovery lock changed')
        yield fd
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent)


@contextlib.contextmanager
def targets(boot, expected, writable):
    context(boot, expected)
    before, devices = boot.target_identity()
    boot.idle_target(devices)
    with contextlib.ExitStack() as stack:
        disk = os.open(boot.DISK, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        stack.callback(os.close, disk)
        # Advisory namespace claim only: whole-device O_EXCL conflicts with partitions.
        fcntl.flock(disk, fcntl.LOCK_EX | fcntl.LOCK_NB)
        boot.validate_open_target(disk, boot.DISK, before, boot.TARGET_BYTES)
        parts = {}
        for number, size in SIZES.items():
            path = f'{boot.DISK}p{number}'
            fd = os.open(path, (os.O_RDWR if writable else os.O_RDONLY) |
                         os.O_NOFOLLOW | os.O_CLOEXEC | os.O_EXCL)
            stack.callback(os.close, fd)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            boot.validate_open_target(fd, path, before, size)
            parts[number] = fd
        def check():
            context(boot, expected)
            require(boot.target_identity() == (before, devices), 'target identity changed')
            boot.validate_open_target(disk, boot.DISK, before, boot.TARGET_BYTES)
            for n, fd in parts.items():
                boot.validate_open_target(fd, f'{boot.DISK}p{n}', before, SIZES[n])
            boot.idle_target(devices, (disk, *parts.values()))
            boot.verify_gpt(disk)
            for name in before:
                fields = boot.read_text('/sys/class/block/' + name + '/inflight').split()
                require(len(fields) == 2 and fields == ['0', '0'], 'NVMe I/O in flight')
        check()
        yield disk, parts, check
        check()


def store_report(fd, report):
    data = canonical(report)
    os.lseek(fd, 0, os.SEEK_SET)
    os.ftruncate(fd, 0)
    write_all(fd, data)
    os.fsync(fd)


def write_all(fd, data, accepted=None):
    offset = 0
    while offset < len(data):
        count = os.write(fd, memoryview(data)[offset:])
        require(0 < count <= len(data) - offset, 'invalid or zero write')
        offset += count
        if accepted:
            accepted(count)


def restore_stream(source_fd, target_fd, entry, state):
    """One gzip member, exact encoded EOF, bounded expansion and raw pin."""
    decoder, digest, raw, encoded = zlib.decompressobj(31), hashlib.sha256(), 0, 0
    progress = Progress(f'p{entry["number"]} restore', entry['raw_bytes'])
    os.lseek(target_fd, 0, os.SEEK_SET)
    def accepted(count):
        state['accepted_bytes'] += count
        progress(state['accepted_bytes'])
    while encoded < entry['encoded_bytes']:
        chunk = os.pread(source_fd, min(CHUNK, entry['encoded_bytes'] - encoded), encoded)
        require(bool(chunk), 'truncated gzip input')
        encoded += len(chunk)
        while chunk:
            output = decoder.decompress(chunk, min(CHUNK, entry['raw_bytes'] - raw + 1))
            chunk = decoder.unconsumed_tail
            raw += len(output)
            require(raw <= entry['raw_bytes'], 'gzip expansion exceeds partition bound')
            digest.update(output)
            write_all(target_fd, output, accepted)
            require(not decoder.unused_data, 'trailing gzip data/member forbidden')
            if decoder.eof:
                require(encoded == entry['encoded_bytes'] and not chunk, 'gzip ended before encoded EOF')
                break
    require(decoder.eof and raw == entry['raw_bytes'] and
            digest.hexdigest() == entry['raw_sha256'], 'gzip EOF/length/raw SHA-256 differs')
    state['stream_complete'] = True
    progress(raw, True)


def plan_for(index, args):
    return {'schema': 1, 'operation': 'known-host-paired-restore',
            'expected_boot_id': args.expected_boot_id, 'backup_index_sha256': args.index_sha256,
            'backup_index': index, 'write_order': [2, 3, 4],
            'targets': [f'/dev/nvme0n1p{n}' for n in SIZES],
            'preserved': ['GPT', 'BOOT', 'p5', 'SD', 'SPI'],
            'interruption': 'nontransactional; remain in RAM and retry full cohort',
            'readback': args.readback, 'hardware_acceptance': False}


def run(args, boot, report, report_fd, mounts):
    require(len({args.backup_index, args.plan, args.report}) == 3, 'input/output paths overlap')
    with contextlib.ExitStack() as stack:
        fd, before, size = stack.enter_context(source(boot, mounts, args.backup_index))
        index = validate_index(pinned_json(fd, size, args.index_sha256), boot)
        source_identity(boot, fd, args.backup_index, size, before)
        report['expected_archives'] = index['partitions']
        report['export_boot_id'] = index['export_boot_id']
        plan = plan_for(index, args)
        if args.operation == 'apply':
            require(args.plan_sha256 is not None, 'apply requires independently pinned plan SHA-256')
            pfd, pbefore, psize = stack.enter_context(source(boot, mounts, args.plan))
            require(pinned_json(pfd, psize, args.plan_sha256) == plan, 'reviewed plan differs')
            source_identity(boot, pfd, args.plan, psize, pbefore)
        else:
            require(args.plan_sha256 is None, 'prepare takes no plan pin')
        pinned_inputs = [(fd, args.backup_index, size, before)]
        if args.operation == 'apply':
            pinned_inputs.append((pfd, args.plan, psize, pbefore))
        held = []
        for entry in index['partitions']:
            path = str(Path(args.backup_index).parent / entry['archive'])
            require(path not in (args.plan, args.report), 'archive/output overlap')
            afd, identity, length = stack.enter_context(source(boot, mounts, path, entry['encoded_bytes']))
            require(hash_fd(afd, length, f'Authenticate {entry["archive"]}') == entry['encoded_sha256'],
                    'archive encoded SHA-256 differs')
            source_identity(boot, afd, path, length, identity)
            validate_superblock(archive_prefix(afd), entry['number'])
            source_identity(boot, afd, path, length, identity)
            held.append((entry, afd, path, identity))
            pinned_inputs.append((afd, path, length, identity))
        def check_sources():
            for sfd, path, length, identity in pinned_inputs:
                source_identity(boot, sfd, path, length, identity)
        with recovery_lock(), targets(boot, args.expected_boot_id, args.operation == 'apply') as (disk, parts, check):
            # p1 is never opened writable, and GPT/BOOT remain byte-for-byte pinned.
            require(hash_region(disk, 1048576, boot.SOURCE_BYTES) == boot.NEW_SHA256,
                    'compatible current BOOT SHA-256 differs')
            check()
            check_sources()
            if args.operation == 'prepare':
                out = boot.fresh_output(args.plan, mounts)
                try:
                    write_all(out, canonical(plan))
                    os.fsync(out)
                finally:
                    os.close(out)
                report['plan_sha256'] = hashlib.sha256(canonical(plan)).hexdigest()
            else:
                for entry, afd, path, identity in held:
                    source_identity(boot, afd, path, entry['encoded_bytes'], identity)
                report['status'] = 'writing-nontransactional'
                print('Restoring fixed p2 -> p3 -> p4; interruption requires full retry in RAM.',
                      file=sys.stderr, flush=True)
                sys.stderr.flush()
                store_report(report_fd, report)  # durable intent before first device write
                for entry, afd, path, identity in held:
                    number = entry['number']
                    check()
                    check_sources()
                    source_identity(boot, afd, path, entry['encoded_bytes'], identity)
                    state = report['partitions'][str(number)]
                    # A failed write syscall may have modified media without returning a count.
                    # Persist uncertainty before entering any partition write operation.
                    report.update(attempted=True, may_have_modified=True)
                    state.update(attempted=True, may_have_modified=True)
                    store_report(report_fd, report)
                    restore_stream(afd, parts[number], entry, state)
                    source_identity(boot, afd, path, entry['encoded_bytes'], identity)
                    print(f'Flushing p{number}...', file=sys.stderr, flush=True)
                    os.fsync(parts[number])
                    fcntl.ioctl(parts[number], boot.BLKFLSBUF)
                    state['flush_complete'] = True
                    if args.readback:
                        require(hash_fd(parts[number], entry['raw_bytes'], f'p{number} readback') ==
                                entry['raw_sha256'], 'partition readback SHA-256 differs')
                        state['readback_complete'] = True
                    source_identity(boot, afd, path, entry['encoded_bytes'], identity)
                    check()
                    check_sources()
                    store_report(report_fd, report)
                # ExitStack closes all claims and all held sources before success is assigned.
    report['status'] = 'operations-complete-awaiting-terminal-receipt'


def hash_region(fd, start, length):
    digest, offset = hashlib.sha256(), 0
    progress = Progress('Authenticate compatible BOOT', length)
    while offset < length:
        data = os.pread(fd, min(CHUNK, length - offset), start + offset)
        require(bool(data), 'short BOOT read')
        digest.update(data)
        offset += len(data)
        progress(offset)
    progress(offset, True)
    return digest.hexdigest()


def interrupted(signum, _frame):
    raise InterruptedError(f'interrupted by signal {signum}; remain in RAM and retry full cohort')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('prepare', 'apply'))
    for name in ('backup-index', 'index-sha256', 'expected-boot-id', 'plan', 'report'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--plan-sha256')
    parser.add_argument('--readback', action='store_true')
    args = parser.parse_args(argv)
    report = {'schema': 1, 'operation': 'known-host-paired-' + args.operation,
              'status': 'failed-or-refused', 'hardware_acceptance': False,
              'expected_boot_id': args.expected_boot_id, 'backup_index_sha256': args.index_sha256,
              'plan_sha256': args.plan_sha256, 'readback_requested': args.readback,
              'attempted': False, 'may_have_modified': False,
              'partitions': {str(n): {'attempted': False, 'may_have_modified': False,
                  'accepted_bytes': 0, 'stream_complete': False,
                  'flush_complete': False, 'readback_complete': False} for n in SIZES}}
    report_fd, result = None, 1
    old_handlers = {}
    try:
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            old_handlers[sig] = signal.signal(sig, interrupted)
        boot = policy()
        report['fixed_target'] = {'disk': boot.DISK, 'serial': boot.SERIAL, 'model': boot.MODEL,
            'namespace_bytes': boot.TARGET_BYTES, 'state_uuid': STATE_UUID,
            'compatible_boot_sha256': boot.NEW_SHA256,
            'partitions': [{'path': f'{boot.DISK}p{n}', 'raw_bytes': size} for n, size in SIZES.items()]}
        mounts = context(boot, args.expected_boot_id)
        report['observed_boot_id'] = args.expected_boot_id
        report['authenticated_policy_sha256'] = HELPER_SHA256
        report_fd = boot.fresh_output(args.report, mounts)
        store_report(report_fd, report)
        run(args, boot, report, report_fd, mounts)
        result = 0
    except BaseException as error:
        report.update(status='failed-or-refused', error=f'{type(error).__name__}: {error}')
        print(report['error'], file=sys.stderr, flush=True)
    finally:
        if report_fd is not None:
            try:
                # This file is deliberately conservative: it can never attest its own
                # successful fsync/close. Only the subsequent terminal receipt can.
                store_report(report_fd, report)
            except BaseException as error:
                report.update(status='failed-or-refused', report_error=str(error))
                result = 1
            try:
                os.close(report_fd)
            except BaseException as error:
                report.update(status='failed-or-refused', report_close_error=str(error))
                result = 1
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)
    if result == 0:
        report['progress_report_sha256'] = hashlib.sha256(canonical(report)).hexdigest()
        report['status'] = 'prepared' if args.operation == 'prepare' else 'restore-complete'
        report['receipt_requires'] = 'exit 0 plus terminal receipt plus matching conservative progress report'
    print(canonical(report).decode('ascii'), end='', flush=True)
    return result


if __name__ == '__main__':
    sys.exit(main())
