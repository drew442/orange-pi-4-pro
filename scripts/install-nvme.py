#!/usr/bin/env python3
"""Prepare a review plan or interactively install an authenticated whole NVMe image.

Preparation is read-only and grants no authorization or lock. No firmware, SD,
mount, automatic backup, activation or rollback operations are implemented.
"""
import argparse
import base64
import contextlib
import fcntl
import gzip
import hashlib
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import signal
import stat
import struct
import subprocess
import sys
import time


def adjacent(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PLANNER = adjacent('install_nvme_planner', 'plan-nvme.py')
LAYOUT = adjacent('install_nvme_layout', 'nvme_layout.py')
MIB = 1024**2
# Linux include/uapi/linux/fs.h; _IOR size encodes the 64-bit Linux ABI.
BLKGETSIZE64, BLKSSZGET, BLKPBSZGET, BLKROGET = 0x80081272, 0x1268, 0x127b, 0x125e
BLKFLSBUF, BLKRRPART = 0x1261, 0x125f


def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate JSON field')
        result[key] = value
    return result


def record_valid(value):
    require(isinstance(value, dict) and type(value.get('bytes')) is int
            and value['bytes'] > 0 and isinstance(value.get('sha256'), str)
            and re.fullmatch('[0-9a-f]{64}', value['sha256']), 'invalid image record')
    return {key: value[key] for key in ('bytes', 'sha256')}


def file_identity(fd):
    info = os.fstat(fd)
    require(stat.S_ISREG(info.st_mode), 'source must be a regular file')
    return {'device': info.st_dev, 'inode': info.st_ino, 'bytes': info.st_size,
            'mtime_ns': info.st_mtime_ns, 'ctime_ns': info.st_ctime_ns}


def open_regular(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        file_identity(fd)
        return os.fdopen(fd, 'rb')
    except BaseException:
        os.close(fd)
        raise


def read_json(source, limit=4*MIB):
    source.seek(0)
    data = source.read(limit + 1)
    require(len(data) <= limit, 'JSON exceeds size limit')
    result = json.loads(data, object_pairs_hook=unique_pairs)
    require(isinstance(result, dict), 'JSON object required')
    return data, result


def validate_manifest(value):
    require(type(value.get('schema')) is int and value['schema'] == 1,
            'unsupported manifest schema')
    require(value.get('kernel_release') == '6.6.98-a733-diag', 'unsupported kernel profile')
    record = record_valid(value['files']['nvme.img'])
    geometry = LAYOUT.layout(record['bytes'], value['logical_sector_bytes'], value['physical_sector_bytes'])
    require(value['layout'] == geometry, 'manifest differs from fixed host layout')
    intent = value.get('deployment_intent')
    require(isinstance(intent, dict) and intent.get('for_installation') is True,
            'artifact is a fixture, not intended for installation')
    mac, cidr, key = (intent.get(name) for name in
                      ('management_mac', 'management_cidr', 'administration_public_key_fingerprint'))
    require(isinstance(mac, str) and re.fullmatch(r'(?:[0-9a-f]{2}:){5}[0-9a-f]{2}', mac)
            and not int(mac[:2], 16) & 1 and mac != '00:00:00:00:00:00'
            and mac not in ('02:73:30:00:00:01', '02:11:22:33:44:55'), 'invalid or fixture management MAC')
    require(isinstance(cidr, str), 'management CIDR required')
    network = ipaddress.IPv4Network(cidr, strict=True)
    require(str(network) == cidr and network.prefixlen > 0
            and not any(network.overlaps(ipaddress.IPv4Network(n)) for n in
                        ('192.0.2.0/24', '198.51.100.0/24', '203.0.113.0/24', '127.0.0.0/8', '224.0.0.0/3')),
            'invalid or fixture management CIDR')
    require(isinstance(key, str) and re.fullmatch(r'SHA256:[A-Za-z0-9+/]{43}', key)
            and key != 'SHA256:zJDXpqimTuhgMi8BhG83V7SeYsDurn/2Dmt9g/R3Fzc',
            'invalid or fixture administration fingerprint')
    require(len(base64.b64decode(key[7:] + '=', validate=True)) == 32, 'invalid fingerprint')
    return record


def hash_stream(source, expected):
    source.seek(0)
    result, length = hashlib.sha256(), 0
    while True:
        data = source.read(min(4*MIB, expected['bytes'] - length + 1))
        if not data:
            break
        length += len(data)
        require(length <= expected['bytes'], 'source exceeds authenticated size')
        result.update(data)
    require(length == expected['bytes'] and result.hexdigest() == expected['sha256'],
            'source size or SHA-256 mismatch')


class Source:
    """Hold authenticated manifest and encoded image descriptors through installation."""
    def __init__(self, image, manifest, manifest_sha256):
        self.stack = contextlib.ExitStack()
        try:
            self.manifest_file = self.stack.enter_context(open_regular(manifest))
            self.manifest_identity = file_identity(self.manifest_file.fileno())
            data, self.manifest = read_json(self.manifest_file)
            require(re.fullmatch('[0-9a-f]{64}', manifest_sha256 or '')
                    and digest(data) == manifest_sha256, 'trusted manifest SHA-256 mismatch')
            self.manifest_record = {'bytes': len(data), 'sha256': digest(data)}
            self.decoded_expected = validate_manifest(self.manifest)
            self.image = self.stack.enter_context(open_regular(image))
            self.identity = file_identity(self.image.fileno())
            name = Path(image).name
            require(name in ('nvme.img', 'nvme.img.gz'), 'image basename must be nvme.img or nvme.img.gz')
            self.compressed = name.endswith('.gz')
            self.encoded_expected = record_valid(self.manifest['files'][name])
            hash_stream(self.image, self.encoded_expected)
            with self.decoded() as decoded:
                hash_stream(decoded, self.decoded_expected)
            self.check()
            self.review = {'path': os.path.abspath(image), 'identity': self.identity,
                           'encoded_record': self.encoded_expected, 'decoded_record': self.decoded_expected,
                           'manifest_path': os.path.abspath(manifest), 'manifest_identity': self.manifest_identity,
                           'manifest_record': self.manifest_record}
        except BaseException:
            self.stack.close()
            raise

    @contextlib.contextmanager
    def decoded(self):
        self.image.seek(0)
        if self.compressed:
            with gzip.GzipFile(fileobj=self.image, mode='rb') as stream:
                yield stream
        else:
            yield self.image

    def check(self):
        require(file_identity(self.image.fileno()) == self.identity, 'image changed while held')
        require(file_identity(self.manifest_file.fileno()) == self.manifest_identity,
                'manifest changed while held')

    def close(self):
        self.stack.close()


def block_dependencies(major_minor, sysroot=Path('/sys/dev/block'), seen=None):
    """Resolve partitions and recursive mapper slaves, rejecting unknown topology."""
    seen = set() if seen is None else seen
    require(major_minor not in seen, 'cyclic backing-device topology')
    seen.add(major_minor)
    node = (sysroot / major_minor).resolve(strict=True)
    require(node.joinpath('dev').read_text().strip() == major_minor, 'backing identity differs')
    result = {major_minor}
    if node.joinpath('partition').exists():
        parent = node.parent.joinpath('dev').read_text().strip()
        result |= block_dependencies(parent, sysroot, seen)
    else:
        slaves = list(node.joinpath('slaves').iterdir())
        if slaves:
            for slave in slaves:
                result |= block_dependencies(slave.joinpath('dev').read_text().strip(), sysroot, seen.copy())
        else:
            require(re.fullmatch(r'(?:nvme[0-9]+n[0-9]+|sd[a-z]+|vd[a-z]+|mmcblk[0-9]+)', node.name),
                    'unsupported backing device')
    return result


def source_off_target(fd, target_major_minor, mountinfo=None, dependencies=block_dependencies):
    info = os.fstat(fd)
    major_minor = f'{os.major(info.st_dev)}:{os.minor(info.st_dev)}'
    text = Path('/proc/self/mountinfo').read_text() if mountinfo is None else mountinfo
    entries = [line.split(' - ', 1) for line in text.splitlines()]
    matches = [after.split()[0] for before, after in entries if before.split()[2] == major_minor]
    require(matches and len(set(matches)) == 1, 'ambiguous or unknown source filesystem')
    filesystem = matches[0]
    if filesystem in ('tmpfs', 'ramfs'):
        return
    require(filesystem in ('ext2', 'ext3', 'ext4', 'xfs', 'vfat', 'exfat', 'ntfs3', 'iso9660'),
            'unsupported source filesystem backing')
    require(target_major_minor not in dependencies(major_minor), 'source derives from target NVMe')


def inventory():
    result = subprocess.run(['lsblk', '--json', '--bytes', '--paths', '--tree', '--output',
                             'PATH,KNAME,TYPE,TRAN,MODEL,SERIAL,SIZE,LOG-SEC,PHY-SEC,RO,MOUNTPOINTS,MAJ:MIN'],
                            check=True, capture_output=True, text=True)
    value = json.loads(result.stdout)
    for node in PLANNER.flatten(value['blockdevices']):
        node['kname'] = os.path.basename(node['kname'])
    return value


def inspect_target(args, source):
    result = PLANNER.plan(inventory(), args.target, expected_serial=args.expected_serial,
                          expected_bytes=args.expected_bytes)
    target = result['target']
    require(target['model'] == args.expected_model, 'model does not match')
    manifest = source.manifest
    require(target['size_bytes'] == source.decoded_expected['bytes']
            and target['logical_sector_bytes'] == manifest['logical_sector_bytes']
            and target['physical_sector_bytes'] == manifest['physical_sector_bytes']
            and result['layout'] == manifest['layout'], 'target and image geometry differ')
    for file in (source.image, source.manifest_file):
        source_off_target(file.fileno(), target['major_minor'])
    source.check()
    return target


def ioctl_number(fd, command, fmt):
    return struct.unpack(fmt, fcntl.ioctl(fd, command, bytes(struct.calcsize(fmt))))[0]


def validate_fd(fd, target):
    info = os.fstat(fd)
    require(stat.S_ISBLK(info.st_mode) and f'{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}' == target['major_minor'],
            'opened target identity differs')
    for command, fmt, expected in ((BLKGETSIZE64, '=Q', target['size_bytes']),
                                   (BLKSSZGET, '=I', target['logical_sector_bytes']),
                                   (BLKPBSZGET, '=I', target['physical_sector_bytes']), (BLKROGET, '=I', 0)):
        require(ioctl_number(fd, command, fmt) == expected, 'opened target geometry or read-only state differs')


def metadata_hashes(fd, size):
    values = {}
    for name, offset in (('first_mib', 0), ('last_mib', size-MIB)):
        data = os.pread(fd, MIB, offset)
        require(len(data) == MIB, 'short target metadata read')
        values[name] = digest(data)
    return values


def make_plan(args, source):
    target = inspect_target(args, source)
    fd = os.open(target['canonical_path'], os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    try:
        validate_fd(fd, target)
        old = metadata_hashes(fd, target['size_bytes'])
        require(inspect_target(args, source) == target, 'target changed during preparation')
    finally:
        os.close(fd)
    result = {'schema': 1, 'operation': 'full-disk-nvme-install', 'source': source.review,
              'target': target, 'layout': source.manifest['layout'], 'old_metadata': old,
              'deployment_intent': source.manifest['deployment_intent'],
              'warning': 'This plan is neither authorization nor a lock. Verify an off-device backup before erasure.'}
    result['plan_sha256'] = digest(canonical_json(result))
    return result


def authorize(plan, input_stream=None, output_stream=None):
    input_stream = sys.stdin if input_stream is None else input_stream
    output_stream = sys.stderr if output_stream is None else output_stream
    require(input_stream.isatty() and output_stream.isatty(), 'apply requires an interactive TTY')
    target = plan['target']
    for prompt, expected in (
            ('Confirm an independently verified off-device backup: ', 'BACKUP VERIFIED'),
            ('Confirm complete, irreversible target erasure: ',
             f"ERASE {target['serial']} {target['size_bytes']} {plan['plan_sha256']}")):
        output_stream.write(prompt)
        output_stream.flush()
        require(input_stream.readline().rstrip('\n') == expected, 'authorization phrase does not match')


def stream_copy_verify(source_stream, target_fd, decoded_expected, *, flush_callback,
                       refresh_callback, source_check=lambda: None,
                       progress=lambda done, total: None, chunk_bytes=4*MIB, report=None):
    """Bounded byte engine; caller owns device validation and exclusive descriptor.

    Tests may supply a regular target descriptor. Production never does. Every
    logical byte is written, including zeros. Failure never claims an untouched
    target after a write was attempted, even if that syscall raised immediately.
    """
    if report is None:
        report = {'success': False, 'target_modified': False, 'bytes_attempted': 0,
                  'bytes_written': 0, 'bytes_verified': 0}
    report['success'] = False
    try:
        expected = record_valid(decoded_expected)
        require(type(chunk_bytes) is int and chunk_bytes > 0, 'invalid copy chunk size')
        source_check()
        source_stream.seek(0)
        incoming = hashlib.sha256()
        while report['bytes_written'] < expected['bytes']:
            data = source_stream.read(min(chunk_bytes, expected['bytes']-report['bytes_written']))
            require(data, 'decoded image ended early')
            require(len(data) <= expected['bytes']-report['bytes_written'], 'decoded image exceeds size')
            incoming.update(data)
            cursor = 0
            report['bytes_attempted'] += len(data)
            while cursor < len(data):
                report['target_modified'] = True
                count = os.pwrite(target_fd, data[cursor:], report['bytes_written'])
                require(0 < count <= len(data)-cursor, 'target write made invalid progress')
                cursor += count
                report['bytes_written'] += count
            progress(report['bytes_written'], expected['bytes'])
        require(not source_stream.read(1), 'decoded image exceeds size')
        require(incoming.hexdigest() == expected['sha256'], 'image changed during writing')
        source_check()
        os.fsync(target_fd)
        flush_callback(target_fd)
        readback = hashlib.sha256()
        while report['bytes_verified'] < expected['bytes']:
            data = os.pread(target_fd, min(chunk_bytes, expected['bytes']-report['bytes_verified']), report['bytes_verified'])
            require(data, 'target readback ended early')
            readback.update(data)
            report['bytes_verified'] += len(data)
        require(readback.hexdigest() == expected['sha256'], 'target readback SHA-256 mismatch')
        source_check()
        refresh_callback(target_fd)
        report['success'] = True
        report['readback_sha256'] = readback.hexdigest()
    except (Exception, KeyboardInterrupt) as error:
        report['success'] = False
        report['error'] = f'{type(error).__name__}: {error}'
    return report


class ConsoleProgress:
    """Bound serial output by bytes or elapsed time, plus final completion."""
    def __init__(self, output=None, clock=None):
        self.output = sys.stderr if output is None else output
        self.clock = time.monotonic if clock is None else clock
        self.last_time = self.clock()
        self.last_bytes = 0

    def start(self):
        print('Starting full-disk NVMe write; interruption may leave a partial target.',
              file=self.output, flush=True)

    def __call__(self, done, total):
        now = self.clock()
        if done == total or done - self.last_bytes >= 1024**3 or now - self.last_time >= 10:
            print(f'NVMe bytes written: {done}/{total}', file=self.output, flush=True)
            self.last_time, self.last_bytes = now, done


def interrupted(signum, frame):
    """Best-effort report on SIGTERM; SIGKILL and power loss cannot be caught."""
    raise InterruptedError(f'received signal {signum}')


def apply(args, source, plan, report=None):
    require(os.geteuid() == 0, 'apply requires root for device writes and cache invalidation')
    if report is None:
        report = {'success': False, 'target_modified': False, 'bytes_attempted': 0,
                  'bytes_written': 0, 'bytes_verified': 0}
    authorize(plan)
    target = plan['target']
    require(inspect_target(args, source) == target, 'target changed after authorization')
    probe = os.open(target['canonical_path'], os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    try:
        validate_fd(probe, target)
        require(metadata_hashes(probe, target['size_bytes']) == plan['old_metadata'],
                'target metadata changed after authorization')
    finally:
        os.close(probe)
    fd = os.open(target['canonical_path'], os.O_RDWR | os.O_EXCL | os.O_NONBLOCK | os.O_NOFOLLOW)
    try:
        validate_fd(fd, target)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        def check():
            source.check()
            require(inspect_target(args, source) == target, 'target identity or busy state changed')
            validate_fd(fd, target)
        check()
        require(metadata_hashes(fd, target['size_bytes']) == plan['old_metadata'], 'target metadata changed')
        # Reauthenticate encoded bytes after the interactive pause and before any write.
        hash_stream(source.image, source.encoded_expected)
        check()
        require(metadata_hashes(fd, target['size_bytes']) == plan['old_metadata'], 'target metadata changed')
        with source.decoded() as stream:
            progress = ConsoleProgress()
            progress.start()
            stream_copy_verify(
                stream, fd, source.decoded_expected,
                flush_callback=lambda held: fcntl.ioctl(held, BLKFLSBUF),
                refresh_callback=lambda held: fcntl.ioctl(held, BLKRRPART), source_check=check,
                progress=progress,
                report=report)
    except (Exception, KeyboardInterrupt) as error:
        report['success'] = False
        report['error'] = f'{type(error).__name__}: {error}'
    finally:
        try:
            os.close(fd)
        except (Exception, KeyboardInterrupt) as error:
            report['success'] = False
            report['error'] = f'{type(error).__name__}: {error}'
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('prepare', 'apply'))
    parser.add_argument('--image', required=True)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--manifest-sha256', required=True, help='Digest obtained through a trusted independent channel')
    parser.add_argument('--target', required=True)
    parser.add_argument('--expected-model', required=True)
    parser.add_argument('--expected-serial', required=True)
    parser.add_argument('--expected-bytes', type=int, required=True)
    parser.add_argument('--plan', help='Previously reviewed JSON plan; required for apply')
    args = parser.parse_args(argv)
    source = None
    result = {'success': False, 'target_modified': False, 'bytes_attempted': 0,
              'bytes_written': 0, 'bytes_verified': 0}
    previous_sigterm = signal.signal(signal.SIGTERM, interrupted)
    try:
        require(args.expected_model.strip() and args.expected_serial.strip() and args.expected_bytes > 0,
                'nonempty expected identity required')
        require(args.operation != 'apply' or os.geteuid() == 0,
                'apply requires root for device writes and cache invalidation')
        require(args.operation != 'apply' or args.plan, 'apply requires --plan')
        source = Source(args.image, args.manifest, args.manifest_sha256)
        plan = make_plan(args, source)
        if args.operation == 'prepare':
            source.close()
            source = None
            print(json.dumps(plan, indent=2, sort_keys=True))
            return 0
        with open_regular(args.plan) as saved:
            _, reviewed = read_json(saved)
            source_off_target(saved.fileno(), plan['target']['major_minor'])
        require(reviewed == plan, 'current preflight differs from reviewed plan')
        print(json.dumps(plan, indent=2, sort_keys=True), file=sys.stderr)
        apply(args, source, plan, report=result)
    except (Exception, KeyboardInterrupt) as error:
        result['success'] = False
        result['error'] = f'{type(error).__name__}: {error}'
    finally:
        if source is not None:
            try:
                source.close()
            except (Exception, KeyboardInterrupt) as error:
                # Preserve a prior write report if closing a held source fails.
                result['success'] = False
                result['error'] = f'{type(error).__name__}: {error}'
        signal.signal(signal.SIGTERM, previous_sigterm)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result['success'] else 1


if __name__ == '__main__':
    sys.exit(main())
