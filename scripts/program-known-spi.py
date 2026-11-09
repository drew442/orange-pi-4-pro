#!/usr/bin/env python3
"""Guarded known-A733 SPI plan/programmer preparation.

Writes remain disabled until the coordinator registers a measured, ROM-independent
recovery proof. No CLI or environment option can register that proof. Maintenance
requires trusted quiescent root; census/flock are not kernel exclusion against
hostile root. This tool never supplies hardware acceptance or automatic recovery.
All staged inputs, including this tool and helpers, belong in private /run tmpfs.
"""
import argparse
from contextlib import ExitStack
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import stat
import sys
import time
import types
import uuid

SIZE = 16777216
CHUNK = 1048576
TARGET = '/dev/mtd0'
COMPATIBLE = b'xunlong,orangepi-4-pro\0arm,sun60iw2p1\0'
NVME = ('134807190FC700149277', 'SPCC M.2 PCIe SSD', 256060514304)
ORIGINAL_PIN = 'dffab0dd410657cb30c7b2fd7f2586a4792e8472e58882b3532581f8111a646d'
CANDIDATE_PIN = '83edcd7f448e8353163666016a6bae3e9b70d041a5e4d8454f48931839205e90'
HELPERS = {
    'spi-unit-transaction.py': '6002270dfd2251c16ef8d9ad2fa0a6caa6e279ef346c11c52fa31fcce4c95d0d',
    'spi-mtd-adapter.py': 'f8b88a531827118812e6707de7b5b6e8940e2781e5505896e48d851a16cfbecd',
    'backup-spi.py': 'b160e3fe8e421e18e35aad34aff909e10b4dfca027178d80a4b202c711e2147d',
    'write-known-bootstrap-sd.py': '4769001ab4a4861ff8ffe26ad6aa427f9abe5430b48bb49918d6c1206b96c52a',
}
# Intentionally unregistered. Change only after independent physical demonstration
# and coordinator review of the exact proof bytes. Never accept a runtime override.
RECOVERY_PROOF_PIN = None
MAX_LINE = 65536
MAX_RECORDS = 4096
MAX_TOTAL = 8 * 1048576
ACK_TIMEOUT = 30
READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode('ascii')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def digest(value):
    require(isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value),
            'invalid SHA-256 pin')
    return value


def stamp(info, leaf=True):
    fields = ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid')
    if leaf:
        fields += ('st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
    # Directory mtime/ctime changes from unrelated /run children are immaterial.
    return tuple(getattr(info, key) for key in fields)


class HeldFile:
    """Retain physical nofollow ancestor FDs and a checked immutable byte copy."""
    def __init__(self, path, backup, *, pin=None, exact_size=None, limit=SIZE, source_mode=0o600):
        self.path = path
        self.fds = []
        self.links = []
        self.closed = False
        try:
            parts = Path(path).parts
            require(os.path.normpath(path) == path and parts[:2] == ('/', 'run')
                    and len(parts) >= 4, 'input must be canonical private /run path')
            backup.require_no_swap()
            parent = os.open('/', READ_FLAGS | os.O_DIRECTORY)
            self.fds.append(parent)
            backup.safe_directory(parent)
            self.links.append((None, '/', parent, stamp(os.fstat(parent), False)))
            for part in parts[1:-1]:
                child = os.open(part, READ_FLAGS | os.O_DIRECTORY, dir_fd=parent)
                self.fds.append(child)
                backup.safe_directory(child)
                self.links.append((parent, part, child, stamp(os.fstat(child), False)))
                parent = child
            backup.safe_directory(parent, private=True)
            backup.ram_filesystem(parent)
            self.fd = os.open(parts[-1], READ_FLAGS, dir_fd=parent)
            self.fds.append(self.fd)
            self.parent, self.name = parent, parts[-1]
            info = os.fstat(self.fd)
            backup.ram_filesystem(self.fd)
            require(info.st_dev == os.fstat(parent).st_dev, 'input leaf crossed parent filesystem')
            require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and
                    stat.S_IMODE(info.st_mode) == source_mode and info.st_nlink == 1 and
                    0 < info.st_size <= limit and
                    (exact_size is None or info.st_size == exact_size),
                    'input must be root single-link bounded regular file with required mode')
            self.initial = stamp(info)
            self.data = b''.join(os.pread(self.fd, min(CHUNK, info.st_size - offset), offset)
                                 for offset in range(0, info.st_size, CHUNK))
            require(len(self.data) == info.st_size, 'short staged input read')
            require(pin is None or sha(self.data) == digest(pin), 'input source pin mismatch')
            self.check()
        except BaseException:
            self.close()
            raise

    def check(self):
        require(not self.closed, 'held input closed')
        for parent, name, fd, initial in self.links:
            require(stamp(os.fstat(fd), False) == initial and
                    stamp(os.stat(name, dir_fd=parent, follow_symlinks=False), False) == initial,
                    'input ancestor identity/ownership changed')
        require(stamp(os.fstat(self.fd)) == self.initial and
                stamp(os.stat(self.name, dir_fd=self.parent, follow_symlinks=False)) == self.initial,
                'held input identity/content metadata changed')

    def close(self):
        if not self.closed:
            self.closed = True
            errors = []
            for fd in reversed(self.fds):
                try:
                    os.close(fd)
                except OSError as error:
                    errors.append(error)
            if errors:
                raise errors[0]


def module_from_bytes(data, name, pin):
    require(sha(data) == pin, 'helper source pin mismatch')
    module = types.ModuleType(name)
    # Compile the authenticated held bytes, never reopen/import a mutable path.
    exec(compile(data, '<authenticated-' + name + '>', 'exec'), module.__dict__)
    return module


def bootstrap_backup(path):
    """Bootstrap HeldFile's tmpfs checks without trusting an import path.

    This small bootstrap uses the same physical walker, with local check methods;
    only after authentication may backup-spi supply the equivalent helpers.
    """
    import ctypes

    class Checks:
        @staticmethod
        def require_no_swap():
            require([line.split() for line in Path('/proc/swaps').read_text().splitlines()] ==
                    [['Filename', 'Type', 'Size', 'Used', 'Priority']], 'active or invalid swap')

        @staticmethod
        def safe_directory(fd, private=False):
            info = os.fstat(fd)
            require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and
                    not info.st_mode & 0o022 and
                    (not private or stat.S_IMODE(info.st_mode) == 0o700), 'unsafe input ancestor')

        @staticmethod
        def ram_filesystem(fd):
            result = (ctypes.c_long * 32)()
            require(ctypes.CDLL(None, use_errno=True).fstatfs(fd, ctypes.byref(result)) == 0
                    and result[0] == 0x01021994, 'staged input is not tmpfs')

    held = HeldFile(path, Checks, pin=HELPERS['backup-spi.py'], limit=1048576)
    try:
        return held, module_from_bytes(held.data, 'spi_backup', HELPERS['backup-spi.py'])
    except BaseException:
        held.close()
        raise


def board_guard(expected_boot, host):
    host.host_context(expected_boot)
    require(Path('/proc/device-tree/compatible').read_bytes() == COMPATIBLE,
            'board compatible pin mismatch')
    root = Path('/sys/class/block/nvme0n1')
    observed = ((root / 'device/serial').read_text().strip(),
                (root / 'device/model').read_text().strip(),
                int((root / 'size').read_text().strip()) * 512)
    require(observed == NVME, 'known NVMe identity mismatch')


def recovery_gate(proof=None):
    require(RECOVERY_PROOF_PIN is not None,
            'ROM-independent recovery proof is unregistered; SPI writes disabled')
    require(proof is not None and sha(proof.data) == digest(RECOVERY_PROOF_PIN),
            'registered recovery proof pin mismatch')
    proof.check()
    value = json.loads(proof.data)
    require(value.get('schema') == 1 and
            value.get('operation') == 'known-a733-rom-independent-recovery' and
            value.get('compatible_hex') == COMPATIBLE.hex() and
            value.get('nvme_serial') == NVME[0] and
            value.get('spi') == dict(device=TARGET, dev='90:0', partname='XM25QU128C',
                                     jedec_id='204118', bytes=SIZE, erase_bytes=65536) and
            value.get('method') == 'rom-usb-fel-compatible-ram' and
            value.get('physical_demonstration') is True and
            value.get('independent_of_spi_contents') is True,
            'registered recovery proof identity/demonstration mismatch')


def reject_consumer(info, link=''):
    number = (os.major(info.st_rdev), os.minor(info.st_rdev))
    require(not (stat.S_ISCHR(info.st_mode) and number in ((90, 0), (90, 1))) and
            not (stat.S_ISBLK(info.st_mode) and number == (31, 0)),
            'concurrent SPI consumer/alias')
    require(link not in ('anon_inode:kvm-vm', 'anon_inode:kvm-vcpu') and
            not re.search(r'kvm[-_](?:vm|vcpu)', link) and
            link != '/dev/kvm', 'active KVM consumer')


def quiescence(host, own_fd=None):
    """Every task's descriptors, maps and mount namespace; fail closed on gaps."""
    host.require_single_thread()
    processes = [p for p in Path('/proc').iterdir() if p.name.isdigit()]
    require(any(p.name == '1' for p in processes), 'incomplete process census')
    block = Path('/sys/class/block/mtdblock0')
    if block.exists():
        require((block / 'dev').read_text().strip() == '31:0', 'unexpected MTD block alias')
        require(not list((block / 'holders').iterdir()), 'MTD block alias has holders')
    for process in processes:
        try:
            tasks = list((process / 'task').iterdir())
            require(bool(tasks), 'empty task census')
            for task in tasks:
                try:
                    mounts = host.read_text(task / 'mountinfo').splitlines()
                    require(bool(mounts), 'empty task mountinfo')
                    for line in mounts:
                        fields = line.split()
                        require(len(fields) >= 10 and fields.count('-') == 1 and
                                fields.index('-') >= 6 and len(fields) == fields.index('-') + 4 and
                                re.fullmatch('[0-9]+:[0-9]+', fields[2]), 'invalid task mountinfo')
                        require(fields[2] != '31:0', 'MTD block alias is mounted')
                    descriptors = list((task / 'fd').iterdir())
                    for descriptor in descriptors:
                        require(descriptor.name.isdigit(), 'invalid descriptor census')
                        if process.name == task.name == str(os.getpid()) and int(descriptor.name) == own_fd:
                            continue
                        try:
                            reject_consumer(os.stat(descriptor), os.readlink(descriptor))
                        except OSError as error:
                            if not host.ordinary_exit(error):
                                raise
                    for line in host.read_text(task / 'maps').splitlines():
                        fields = line.split(maxsplit=5)
                        require(len(fields) >= 5 and re.fullmatch('[0-9a-f]+-[0-9a-f]+', fields[0])
                                and fields[4].isdigit(), 'invalid task mapping')
                        if fields[4] == '0':
                            continue
                        start, end = (int(v, 16) for v in fields[0].split('-'))
                        require(start < end, 'invalid mapping range')
                        try:
                            reject_consumer(os.stat(Path('/proc') / task.name / 'map_files' /
                                                    f'{start:x}-{end:x}'))
                        except OSError as error:
                            if not host.ordinary_exit(error):
                                raise
                            if not host.confirmed_gone(task):
                                require(not any(row.split()[0] == fields[0] for row in
                                                host.read_text(task / 'maps').splitlines() if row.split()),
                                        'live mapping is unscannable')
                except OSError as error:
                    if not host.ordinary_exit(error):
                        raise
                    require(host.confirmed_gone(task), 'live task is unscannable')
        except OSError as error:
            if not host.ordinary_exit(error):
                raise
            require(host.confirmed_gone(process), 'live process is unscannable')


def target_identity(fd, backup):
    info = os.fstat(fd)
    require(stat.S_ISCHR(info.st_mode) and
            (os.major(info.st_rdev), os.minor(info.st_rdev)) == (90, 0),
            'target is not exact character 90:0')
    return backup.identity(fd, TARGET)


class BoundedLines:
    """Raw FD input: select deadlines do not depend on buffered stream state."""
    def __init__(self, stream):
        self.fd = stream.fileno()
        self.buffer = b''

    def __call__(self, limit, timeout):
        deadline = time.monotonic() + timeout
        with selectors.DefaultSelector() as selector:
            selector.register(self.fd, selectors.EVENT_READ)
            while b'\n' not in self.buffer:
                require(len(self.buffer) < limit, 'protocol line exceeds bound')
                remaining = deadline - time.monotonic()
                require(remaining > 0 and selector.select(remaining), 'protocol input timeout')
                data = os.read(self.fd, min(4096, limit + 1 - len(self.buffer)))
                require(bool(data), 'protocol EOF/truncated line')
                self.buffer += data
            line, self.buffer = self.buffer.split(b'\n', 1)
            require(len(line) + 1 <= limit, 'protocol line exceeds bound')
            return line + b'\n'


class AcknowledgedEvents:
    def __init__(self, output, read_ack, nonce=None):
        self.output, self.read_ack = output, read_ack
        self.nonce = nonce or uuid.uuid4().hex
        self.sequence, self.total = 0, 0

    def __call__(self, event):
        require(event.get('hardware_acceptance') is False, 'invalid event acceptance')
        self.sequence += 1
        envelope = dict(schema=1, nonce=self.nonce, sequence=self.sequence,
                        event_sha256=sha(canonical(event)), event=event)
        raw = canonical(envelope) + b'\n'
        self.total += len(raw)
        require(self.sequence <= MAX_RECORDS and len(raw) <= MAX_LINE and self.total <= MAX_TOTAL,
                'event capture bounds exceeded')
        self.output.write(raw)
        self.output.flush()
        expected = dict(nonce=self.nonce, sequence=self.sequence,
                        event_sha256=envelope['event_sha256'])
        require(self.read_ack(1024, ACK_TIMEOUT) == canonical(expected) + b'\n',
                'durable capture acknowledgement mismatch')


def read_flash(fd, reader=os.pread, progress=None):
    parts = []
    for offset in range(0, SIZE, CHUNK):
        part = reader(fd, CHUNK, offset)
        require(type(part) is bytes and len(part) == CHUNK, 'short full-flash read')
        parts.append(part)
        if progress:
            progress(offset + CHUNK)
    return b''.join(parts)


def reviewed_plan(engine, raw, pin, original, candidate, current, direction):
    plan = json.loads(raw)
    frozen = dict(plan)
    recorded = frozen.pop('plan_sha256')
    require(recorded == digest(pin) == sha(engine.canonical(frozen)), 'reviewed plan pin mismatch')
    require(plan['direction'] == direction, 'reviewed plan direction mismatch')
    fresh = engine.build_plan(original, candidate, current, direction, plan['current_sha256'])
    require(engine.canonical(fresh) == engine.canonical(plan), 'reviewed plan/current changed')
    return plan


def transact(engine, backend, plan, original, candidate, *, guard, identity,
             emit, open_rw, close=os.close):
    """Only called after RO plan revalidation; close exactly once, even on failure."""
    guard()
    emit(dict(event='capture_ready', hardware_acceptance=False,
              plan_sha256=plan['plan_sha256']))
    guard()
    fd = open_rw()
    adapter, receipt, failure = None, None, None
    close_ok = False
    try:
        adapter = backend.MtdAdapter(fd, guard=guard, identity_check=identity, emit=emit)
        # Progress is visible for all full scans, without adding expensive scans
        # to guards. Unit reads do not print thousands of progress messages.
        scanned = 0
        def read(offset, size):
            nonlocal scanned
            data = adapter.read(offset, size)
            scanned += size
            if scanned % CHUNK == 0:
                print(f'SPI transaction reads: {scanned} bytes', file=sys.stderr, flush=True)
            return data
        receipt = engine.execute(plan, original, candidate, read=read,
                                 erase=adapter.erase, write=adapter.write,
                                 guard=adapter.guard, emit=adapter.emit)
    except BaseException as error:
        failure = error
        receipt = getattr(error, 'receipt', dict(success=False, hardware_acceptance=False,
                                                mutation_possible=False, stage='adapter-setup'))
    finally:
        if adapter is not None:
            adapter.invalidate()
        try:
            close(fd)
            close_ok = True
        except BaseException as error:
            failure = failure or error
    outcome = dict(event='programmer_outcome', hardware_acceptance=False,
                   success=failure is None and close_ok and receipt['success'],
                   close_success=close_ok, receipt=receipt)
    # Failure to capture this outcome prevents reporting completion. Never retry
    # any mutation or failed event, even after a transport/close error.
    emit(outcome)
    require(outcome['success'], 'SPI attempt failed or close uncertain; fresh review required')
    return outcome


def authenticated_binding(required=False):
    """Only the fixed native controller bootstrap supplies this namespace value.

    This authenticates the intended entry point under trusted maintenance; it is
    not a security boundary against a privileged actor injecting Python objects.
    There is deliberately no command-line/environment source-pin override.
    """
    binding = globals().get('_AUTHENTICATED_BOOTSTRAP')
    require(not required or binding is not None, 'write requires authenticated capture bootstrap')
    if binding is not None:
        require(type(binding) is dict and set(binding) == {'pin', 'path', 'fd', 'check'} and
                binding['path'] == __file__ and type(binding['fd']) is int and
                callable(binding['check']), 'invalid authenticated bootstrap binding')
        digest(binding['pin'])
        binding['check']()
    return binding


def hold_programmer(backup, binding):
    source = HeldFile(__file__, backup, pin=binding['pin'] if binding else None,
                      limit=1048576, source_mode=0o400)
    try:
        if binding is not None:
            require(stamp(os.fstat(binding['fd'])) == source.initial,
                    'bootstrap/programmer held source identity changed')
            binding['check']()
        return source
    except BaseException:
        source.close()
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', nargs='?', choices=('plan', 'write'), default='plan')
    parser.add_argument('--direction', choices=('install', 'restore'), default='install')
    parser.add_argument('--expected-boot-id', required=True)
    parser.add_argument('--original', required=True)
    parser.add_argument('--candidate', required=True)
    parser.add_argument('--helpers', required=True, help='private /run directory with pinned helpers')
    parser.add_argument('--plan', help='held engine JSON plan (write only)')
    parser.add_argument('--plan-sha256', help='canonical engine plan digest (write only)')
    parser.add_argument('--recovery-proof', help='registered held physical proof (write only)')
    args = parser.parse_args(argv)
    require(str(uuid.UUID(args.expected_boot_id)) == args.expected_boot_id, 'invalid boot pin')
    if args.mode == 'write':
        # Crucially, fail before ANY device open while proof is unregistered.
        require(RECOVERY_PROOF_PIN is not None,
                'ROM-independent recovery proof is unregistered; SPI writes disabled')
        authenticated_binding(required=True)
        require(args.plan and args.plan_sha256 and args.recovery_proof,
                'write requires reviewed plan, digest and registered proof')
    else:
        require(not any((args.plan, args.plan_sha256, args.recovery_proof)),
                'write prerequisites are invalid in plan mode')
    binding = authenticated_binding(required=args.mode == 'write')
    with ExitStack() as stack:
        held = []
        backup_file, backup = bootstrap_backup(str(Path(args.helpers) / 'backup-spi.py'))
        held.append(backup_file)
        stack.callback(backup_file.close)
        own_source = hold_programmer(backup, binding)
        held.append(own_source)
        stack.callback(own_source.close)
        modules = {'backup-spi.py': backup}
        for name, pin in HELPERS.items():
            if name == 'backup-spi.py':
                continue
            source = HeldFile(str(Path(args.helpers) / name), backup, pin=pin, limit=1048576)
            held.append(source)
            stack.callback(source.close)
            modules[name] = module_from_bytes(source.data, name.replace('-', '_'), pin)
        host, engine = modules['write-known-bootstrap-sd.py'], modules['spi-unit-transaction.py']
        board_guard(args.expected_boot_id, host)
        def hold(path, **kwargs):
            value = HeldFile(path, backup, **kwargs)
            held.append(value)
            stack.callback(value.close)
            return value
        original = hold(args.original, pin=ORIGINAL_PIN, exact_size=SIZE).data
        candidate = hold(args.candidate, pin=CANDIDATE_PIN, exact_size=SIZE).data
        engine.authenticate(original, candidate)
        plan_file = hold(args.plan, limit=MAX_LINE) if args.mode == 'write' else None
        proof = hold(args.recovery_proof, pin=RECOVERY_PROOF_PIN, limit=MAX_LINE) if args.mode == 'write' else None
        active = [None]
        def guard():
            if binding is not None:
                binding['check']()
            board_guard(args.expected_boot_id, host)
            backup.require_no_swap()
            for value in held:
                value.check()
            if args.mode == 'write':
                recovery_gate(proof)
            quiescence(host, active[0])
        guard()
        ro = os.open(TARGET, READ_FLAGS | os.O_NONBLOCK)
        active[0] = ro
        try:
            snapshot = target_identity(ro, backup)
            current = read_flash(ro, progress=lambda count:
                                 print(f'SPI read: {count}/{SIZE}', file=sys.stderr, flush=True))
            guard()
            require(target_identity(ro, backup) == snapshot, 'SPI identity changed')
            if args.mode == 'plan':
                plan = engine.build_plan(original, candidate, current, args.direction, sha(current))
            else:
                plan = reviewed_plan(engine, plan_file.data, args.plan_sha256,
                                     original, candidate, current, args.direction)
        finally:
            active[0] = None
            os.close(ro)
        if args.mode == 'plan':
            print(engine.canonical(plan).decode('ascii'), flush=True)
            return
        emit = AcknowledgedEvents(sys.stdout.buffer, BoundedLines(sys.stdin.buffer))
        def open_rw():
            guard()
            fd = os.open(TARGET, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
            active[0] = fd
            try:
                # Advisory only; census/trusted maintenance remain necessary.
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                require(target_identity(fd, backup) == snapshot, 'fresh writable target changed')
                guard()
                return fd
            except BaseException:
                active[0] = None
                os.close(fd)
                raise
        def close(fd):
            active[0] = None
            os.close(fd)  # One call only: retry could close a recycled descriptor.
        transact(engine, modules['spi-mtd-adapter.py'], plan, original, candidate,
                 guard=guard, identity=lambda fd: require(target_identity(fd, backup) == snapshot,
                                                         'bound target identity changed'),
                 emit=emit, open_rw=open_rw, close=close)


if __name__ == '__main__':
    try:
        main()
    except (Exception, KeyboardInterrupt) as error:
        print(f'SPI programmer refused/failed: {type(error).__name__}: {error}',
              file=sys.stderr, flush=True)
        sys.exit(1)
