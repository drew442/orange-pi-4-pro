#!/usr/bin/env python3
"""Finite native SPI boot evidence collection; default is an entirely read-free plan."""
import argparse
import base64
import fcntl
import hashlib
import importlib.util
import inspect
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import threading
import time
from types import SimpleNamespace
import uuid

REPO = Path(__file__).resolve().parents[1]
PINS = {
    'qualify-managed-boot.py': (15424, '91cc5c5cde4c736cb2d777aa459bf8c535678c3815986331b510f4e61c7744c6'),
    'request-spi-power.py': (17795, 'ac5f2e29e06b924180a5432cb9edffe7e2f8d70a2f46aeb836804377ee0ed895'),
}
BOOT0_SHA = '7f0babc242c1e4614c7243bd2c45a980e4385727ffbb544a7c8f2d324cf8dc07'
PACKAGE_SHA = 'bdfe070a2c358de9dd0061b39609f604c0c12e9c25140903505f88799cc343be'
MIB = 1024 * 1024
TRANSPORT_LIMIT = 8 * MIB
UART_LIMIT = 8 * MIB
BUDGET = 192 * MIB
SERIES_SECONDS = 8 * 3600
BOOT_SECONDS = 300
RAM = '/run/a733-spi-collector'
MARKERS = (b'storage type = 3', b'Scanning nvme 0:1',
           b'A733 SPI/NVMe host candidate from nvme 0:1 /boot/')


def require(ok, why):
    if not ok:
        raise ValueError(why)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical_uuid(value):
    require(isinstance(value, str) and str(uuid.UUID(value)) == value and uuid.UUID(value).int,
            'canonical nonzero boot UUID required')
    return value


def tag_value(value):
    require(re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,79}', value or '') is not None,
            'invalid tag')
    return value


def source_bytes(root=REPO):
    captured = {}
    for name, (size, pin) in PINS.items():
        path = root / 'scripts' / name
        require(not any(p.is_symlink() for p in (path, *path.parents)), 'unsafe helper source')
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            before = os.fstat(fd)
            require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and
                    before.st_size == size, 'helper size/type differs')
            raw = os.read(fd, size + 1)
            require(os.fstat(fd) == before and path.lstat() == before and len(raw) == size and
                    digest(raw) == pin, 'helper source pin differs')
            captured[name] = raw
        finally:
            os.close(fd)
    return captured


def module(path, name, raw=None):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    # Transport and UART modules have no import-time operational calls. Do not call
    # their old collectors, warm_module(), guards or preservation procedures.
    exec(compile(path.read_bytes() if raw is None else raw, str(path), 'exec'), result.__dict__)
    return result


def real_ancestry(path):
    for parent in (path, *path.parents):
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode), 'non-directory or symlink ancestry')


def private_directory(path, create=False):
    real_ancestry(path.parent)
    if create:
        os.mkdir(path, 0o700)  # Existing, including partially-created, always refuses.
        directory_sync(path.parent)
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid() and
            stat.S_IMODE(info.st_mode) == 0o700, 'unsafe private evidence directory')
    return info


def directory_sync(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def publish(path, raw):
    """Exclusive immutable publication; failure leaves a blocking partial artifact."""
    private_directory(path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
    try:
        require(os.fstat(fd).st_nlink == 1, 'linked publication')
        offset = 0
        while offset < len(raw):
            amount = os.write(fd, raw[offset:])
            require(amount > 0, 'short evidence write')
            offset += amount
        os.fsync(fd)
    finally:
        os.close(fd)
    directory_sync(path.parent)


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()


def read_private(path, limit):
    private_directory(path.parent)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and
                before.st_uid == os.geteuid() and stat.S_IMODE(before.st_mode) == 0o400 and
                0 < before.st_size <= limit, 'unsafe evidence file')
        raw = os.read(fd, limit + 1)
        require(os.fstat(fd) == before and path.lstat() == before and len(raw) == before.st_size,
                'evidence changed')
        return raw
    finally:
        os.close(fd)


class Evidence:
    def __init__(self, out, tag, observe=False):
        real_ancestry(out)
        info = out.lstat()
        require(info.st_uid == os.geteuid() and not info.st_mode & 0o022, 'unsafe out directory')
        require(shutil.disk_usage(out).free >= 256 * MIB + BUDGET, 'insufficient evidence reserve')
        tag_value(tag)
        self.original = out / ('spi-boots-' + tag)
        if observe:
            private_directory(self.original)
            self.path = out / ('spi-observation-' + tag + '-' + str(uuid.uuid4()))
        else:
            self.path = self.original
        private_directory(self.path, create=True)
        self.used = self.serial = 0

    def reserve(self, amount):
        require(self.used + amount <= BUDGET, 'insufficient capture budget before power')
        require(shutil.disk_usage(self.path).free >= 256 * MIB + amount, 'capture reserve unavailable')

    def save(self, event, **fields):
        self.serial += 1
        value = dict(event=event, hardware_acceptance=False,
                     review='coordinator-review-required', **fields)
        raw = encoded(value)
        require(self.used + len(raw) <= BUDGET, 'evidence budget exhausted')
        require(shutil.disk_usage(self.path).free >= 256 * MIB + len(raw), 'evidence free floor')
        publish(self.path / f'{self.serial:05d}-{event}.json', raw)
        self.used += len(raw)
        return value


class ExecutionLock:
    """One native executing series across tags, held independently of intents."""
    def __init__(self, out):
        real_ancestry(out)
        self.path = out / 'spi-collector-execute.lock'
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            info = os.fstat(self.fd)
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and
                    info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o600 and
                    info.st_size == 0 and self.path.lstat() == info, 'unsafe execution lock')
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.fsync(self.fd)
            directory_sync(out)
        except BaseException:
            os.close(self.fd)
            raise

    def close(self):
        os.close(self.fd)


class Intents:
    def __init__(self, out):
        self.path = out / 'spi-power-intents'
        # Do not create the namespace during observations or baseline checks.

    def check(self, boot):
        canonical_uuid(boot)
        if not os.path.lexists(self.path):
            real_ancestry(self.path.parent)
            return
        private_directory(self.path)
        # Unknown/partial contents refuse rather than imply absence.
        for entry in self.path.iterdir():
            require(re.fullmatch(r'[0-9a-f-]{36}\.json', entry.name) is not None,
                    'unknown global intent entry')
            canonical_uuid(entry.stem)
            record = json.loads(read_private(entry, 256 * 1024))
            require(record['old_boot_id'] == entry.stem, 'global intent name/binding mismatch')
        require(not os.path.lexists(self.path / (boot + '.json')),
                'existing global intent for current boot: read-only observation required')

    def create(self, value):
        boot = canonical_uuid(value['old_boot_id'])
        self.check(boot)
        if not os.path.lexists(self.path):
            private_directory(self.path, create=True)
        publish(self.path / (boot + '.json'), encoded(value))


# The following functions are also compiled into the read-only remote RPC. They
# use captured sources under honest synthetic filenames, never claim disk custody.
def captured_modules(sources):
    result = []
    for name in ('qualify-managed-boot.py', 'request-spi-power.py'):
        raw = base64.b64decode(sources[name], validate=True)
        size, pin = PINS[name]
        require(len(raw) == size and digest(raw) == pin, 'captured source pin differs')
        namespace = dict(__name__='captured_' + name, __file__='<captured-' + name + '>')
        exec(compile(raw, namespace['__file__'], 'exec'), namespace)
        result.append(SimpleNamespace(**namespace))
    return result


def native_context(sources):
    Q, R = captured_modules(sources)
    M = Q.load_installed()
    P, A = M.P, M.A
    boot = canonical_uuid(A._boot_id())
    P.installed_guard(boot)
    manager = P.NORMAL.manager_state()
    require(manager in ('starting', 'running', 'degraded'), 'PID1 not normally running')
    policy = P.BOOT_POLICY.Policy(boot)
    try:
        policy.check()
        snapshot = R.storage_snapshot(M, policy)
        R.sd_absent()
        require(os.uname().release == '6.6.98-a733-diag', 'wrong kernel')
        require(Path('/sys/devices/system/cpu/online').read_bytes() == b'0-7\n', 'CPUs0-7 required')
        log = M.D.command(['/usr/bin/dmesg', '--color=never'], limit=2 * MIB, timeout=10)
        require('All CPU(s) started at EL2' in log and 'VHE mode initialized successfully' in log
                and 'HYP mode not available' not in log, 'EL2/VHE initialization differs')
        ntp = M.D.command(['/usr/bin/timedatectl', 'show', '-p', 'NTPSynchronized', '--value'],
                          limit=256, timeout=10).strip()
        expected = dict(LoadState='loaded', ActiveState='inactive', Result='success',
                        MainPID='0', Job='', ExecMainCode='1', ExecMainStatus='0')
        unit = M.D.properties('a733-managed-autokvm.service', tuple(expected))
        require(unit.get('ActiveState') != 'failed' and unit.get('LoadState') not in ('error', 'bad-setting')
                and unit.get('Result') in ('success', ''), 'managed unit failed/refused')
        ready = unit == expected and ntp == 'yes' and manager in ('running', 'degraded')
        require(A._boot_id() == boot, 'boot changed during observation')
        P.installed_guard(boot)
        policy.check()
        return dict(state='ready' if ready else 'busy', boot_id=boot, storage=snapshot,
                    unit=unit, ntp=ntp, cpus='0-7', kernel=os.uname().release,
                    el2=True, hardware_acceptance=False)
    finally:
        policy.close()


def mtd_components():
    path = Path('/dev/mtd0')
    sysroot = Path('/sys/class/mtd/mtd0')

    def inventory(fd):
        held, named = os.fstat(fd), path.lstat()
        require(stat.S_ISCHR(held.st_mode) and stat.S_ISCHR(named.st_mode) and
                held.st_rdev == named.st_rdev == os.makedev(90, 0) and
                (held.st_dev, held.st_ino) == (named.st_dev, named.st_ino), 'MTD descriptor identity differs')
        require((sysroot / 'dev').read_bytes() == b'90:0\n' and
                (sysroot / 'size').read_bytes() == b'16777216\n' and
                (sysroot / 'erasesize').read_bytes() == b'65536\n', 'MTD geometry missing/differs')
        return (held.st_dev, held.st_ino, held.st_rdev, held.st_mode,
                (sysroot / 'name').read_bytes())

    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        before = inventory(fd)
        result = {}
        for name, offset, size, pin in (
                ('boot0', 0, 237568, BOOT0_SHA),
                ('package', 262144, 1654784 - 262144, PACKAGE_SHA),
                ('calibration', 260096, 2048, None)):
            raw = bytearray()
            while len(raw) < size:
                chunk = os.pread(fd, min(65536, size - len(raw)), offset + len(raw))
                require(bool(chunk), 'short MTD read')
                raw.extend(chunk)
            value = dict(offset=offset, bytes=size, sha256=digest(raw))
            if pin:
                require(value['sha256'] == pin, 'SPI ' + name + ' hash differs')
            else:
                value['data_base64'] = base64.b64encode(raw).decode('ascii')
            result[name] = value
        require(inventory(fd) == before, 'MTD named/held identity changed')
        result['inventory'] = dict(device='/dev/mtd0', rdev='90:0', bytes=16777216,
                                   erasesize=65536, name_base64=base64.b64encode(before[4]).decode('ascii'))
        result['scope'] = 'code components and calibration only; full boundary snapshots required'
        return result
    finally:
        os.close(fd)


def stage_sources(sources, boot):
    """Boot/source guard precedes every RAM write; no admission or sync here."""
    Q, R = captured_modules(sources)
    M = Q.load_installed()

    def guard():
        require(M.A._boot_id() == boot, 'staging boot changed')
        M.P.installed_guard(boot)

    guard()
    R.ram_directory(Path('/run'), private=False)
    root = Path(RAM)
    if not os.path.lexists(root):
        guard()
        os.mkdir(root, 0o700)
        directory_sync(root.parent)
    R.ram_directory(root)
    require(set(os.listdir(root)) <= set(PINS), 'unexpected RAM staging entry')
    for name in PINS:
        path = root / name
        raw = base64.b64decode(sources[name], validate=True)
        guard()
        if os.path.lexists(path):
            require(R.read_private(path, len(raw)) == raw, 'RAM source differs')
        else:
            # publish needs local uid=0 private checks; remote always native root.
            publish(path, raw)
        guard()
        require(R.read_private(path, len(raw)) == raw, 'RAM staging readback differs')
    require(set(os.listdir(root)) == set(PINS), 'RAM closure differs')
    directory_sync(root)
    guard()
    return dict(state='staged', boot_id=boot, source_pins=PINS)


def remote_source(sources, operation, boot=None):
    functions = (require, digest, canonical_uuid, directory_sync, real_ancestry,
                 private_directory, publish, captured_modules, native_context,
                 mtd_components, stage_sources)
    header = ('import base64, hashlib, json, os, stat, uuid\nfrom pathlib import Path\n'
              'from types import SimpleNamespace\n')
    header += f'PINS={PINS!r}\nMIB={MIB!r}\nRAM={RAM!r}\nBOOT0_SHA={BOOT0_SHA!r}\nPACKAGE_SHA={PACKAGE_SHA!r}\n'
    header += 'SOURCES=' + repr({name: base64.b64encode(raw).decode('ascii')
                                for name, raw in sources.items()}) + '\n'
    header += '\n'.join(inspect.getsource(fn) for fn in functions)
    header += '\nresult={}\ntry:\n'
    # Keep a boot identity even when later readiness checks return busy/refused.
    header += " result['boot_id']=canonical_uuid(Path('/proc/sys/kernel/random/boot_id').read_text().strip())\n"
    if operation == 'observe':
        header += ' result.update(native_context(SOURCES))\n'
    elif operation == 'components':
        header += f' require(native_context(SOURCES)["boot_id"] == {boot!r}, "component boot differs")\n'
        header += " result.update(state='components', components=mtd_components())\n"
        header += f' require(native_context(SOURCES)["boot_id"] == {boot!r}, "component boot changed")\n'
    elif operation == 'stage':
        header += f' result.update(stage_sources(SOURCES, {boot!r}))\n'
    else:
        raise ValueError('unknown RPC operation')
    header += "except BaseException as error:\n result.update(state='refused', error=type(error).__name__+': '+str(error)[:2048])\nprint(json.dumps(result, sort_keys=True), flush=True)\n"
    return header.encode()


class Native:
    def __init__(self, captured, evidence, deadline, clock=time.monotonic):
        self.sources, self.evidence, self.deadline, self.clock = captured, evidence, deadline, clock
        self.phase_deadline = deadline
        self.rpc_templates = {operation: remote_source(captured, operation, '__EXPECTED_SPI_BOOT_UUID__')
                              for operation in ('observe', 'components', 'stage')}
        dependencies = {}
        for name in ('collect-spi-boots.py', 'preserve-known-endurance-outcome.py', 'collect-known-cold-boots.py'):
            path = REPO / 'scripts' / name
            require(not any(p.is_symlink() for p in (path, *path.parents)), 'unsafe collector dependency')
            dependencies[name] = path.read_bytes()
        evidence.save('collector-sources', source_pins={name: dict(bytes=len(raw), sha256=digest(raw))
                                                       for name, raw in dependencies.items()})
        self.transport = module(REPO / 'scripts/preserve-known-endurance-outcome.py', 'spi_transport',
                                dependencies['preserve-known-endurance-outcome.py'])
        self.cold = module(REPO / 'scripts/collect-known-cold-boots.py', 'spi_uart',
                           dependencies['collect-known-cold-boots.py'])

    def call(self, label, source, timeout=30):
        remaining = min(self.deadline, self.phase_deadline) - self.clock()
        require(remaining > 0, 'series deadline')
        self.evidence.reserve(12 * MIB)
        outcome = []
        def bounded_transaction():
            try:
                outcome.append(self.transport.bounded_command(
                    self.transport.ssh_argv(), TRANSPORT_LIMIT,
                    timeout=min(timeout, remaining), source=source))
            except BaseException as error:
                outcome.append(error)
        worker = threading.Thread(target=bounded_transaction)
        worker.start()
        while worker.is_alive():
            worker.join(30)
            if worker.is_alive():
                print('SPI collector: bounded ' + label + ' transport remains pending.', flush=True)
        result = outcome[0]
        if isinstance(result, BaseException):
            raise result
        self.evidence.save('transport', operation=label, raw=result)
        return result

    def rpc(self, operation, boot=None):
        if boot is not None:
            canonical_uuid(boot)
        source = self.rpc_templates[operation].replace(b'__EXPECTED_SPI_BOOT_UUID__', (boot or '').encode('ascii'))
        raw = self.call(operation, source)
        try:
            value = json.loads(base64.b64decode(raw['stdout_b64'], validate=True))
            require(isinstance(value, dict), 'RPC object required')
        except (ValueError, KeyError):
            return dict(state='transport-unknown')
        if value.get('state') == 'refused' and not raw.get('truncated'):
            return value
        if raw.get('returncode') != 0 or raw.get('timed_out') or raw.get('truncated'):
            return dict(state='transport-unknown')
        return value

    def observe(self):
        return self.rpc('observe')

    def components(self, boot):
        value = self.rpc('components', boot)
        require(value.get('state') == 'components' and value.get('boot_id') == boot,
                'SPI component observation failed')
        return value

    def stage(self, boot):
        result = self.rpc('stage', boot)
        require(result.get('state') == 'staged' and result.get('boot_id') == boot, 'RAM staging failed')

    def cli(self, name, args, timeout):
        # Authenticated staged bytes execute with real __file__, without shell interpolation.
        source = ('import os\nos.execv("/usr/bin/python3", ' +
                  repr(['/usr/bin/python3', '-I', '-B', RAM + '/' + name, *args]) + ')\n').encode()
        return self.call(name, source, timeout)

    def qualify(self, boot):
        raw = self.cli('qualify-managed-boot.py', ['--qualify', '--expected-boot-id', boot], 120)
        require(raw.get('returncode') == 0 and not raw.get('timed_out') and
                not raw.get('truncated'), 'qualification transport failed')
        value = json.loads(base64.b64decode(raw['stdout_b64'], validate=True))
        require(value.get('success') is True and value.get('stage') == 'complete' and
                value.get('expected_boot_id') == boot and not value.get('cleanup_errors') and
                len(value.get('records', {})) == 13, 'incomplete qualified cohort')
        for record in value['records'].values():
            data = base64.b64decode(record['data_base64'], validate=True)
            require(len(data) == record['bytes'] and digest(data) == record['sha256'],
                    'retained qualifier record differs')
        return dict(head=value['records']['head.json']['sha256'],
                    registry=value['records']['autostart.json']['sha256'])

    def request(self, intent):
        args = ['--execute', '--action', intent['action'], '--expected-boot-id', intent['old_boot_id'],
                '--request-id', intent['request_id'], '--expected-head-sha256', intent['head'],
                '--expected-registry-sha256', intent['registry']]
        # Exactly ONE call. A timeout/close is retained and never retried.
        return self.cli('request-spi-power.py', args, 45)

    def uart(self):
        return self.cold.UART(self.transport.UART)

    def attest(self, deadline):
        return self.cold.confirm(deadline, clock=self.clock)


def uart_evidence(raw, kind, attested_offset=None):
    require(len(raw) <= UART_LIMIT, 'UART limit exceeded')
    require(not any(word in raw for word in
                    (b'Kernel panic', b'Internal error:', b'unclean shutdown')), 'UART panic/unclean shutdown')
    boundary = b'reboot: Restarting system' if kind == 'warm' else b'reboot: Power down'
    start = raw.find(boundary)
    if start < 0:
        return False
    start += len(boundary)
    require(b'reboot: Restarting system' not in raw[start:] and b'reboot: Power down' not in raw[start:],
            'multiple UART boot boundaries')
    if kind == 'cold':
        if attested_offset is None:
            return False
        require(start <= attested_offset, 'cold powerdown must precede attestation')
        start = attested_offset
    # The native boot0 banner anchors one firmware boot, preventing matches
    # assembled across separate firmware attempts without a Linux restart.
    banner = b'HELLO! BOOT0 is starting!'
    if banner not in raw[start:]:
        return False
    start += raw[start:].find(banner) + len(banner)
    require(banner not in raw[start:], 'multiple UART firmware boots')
    after = raw[start:]
    position = 0
    for marker in MARKERS:
        found = after.find(marker, position)
        if found < 0:
            return False
        position = found + len(marker)
    return True


class Collector:
    def __init__(self, native, evidence, intents, *, clock=time.monotonic, sleep=time.sleep):
        self.native, self.evidence, self.intents = native, evidence, intents
        self.clock, self.sleep = clock, sleep
        self.deadline = native.deadline
        self.seen = set()
        self.completed = []
        self.progress = 0

    def remaining(self, deadline=None):
        result = min(self.deadline, deadline or self.deadline) - self.clock()
        require(result > 0, 'boot/series deadline exceeded')
        return result

    def pause(self, deadline):
        self.remaining(deadline)
        if self.clock() >= self.progress:
            print('SPI boot collection: read-only readiness polling; coordinator review required.', flush=True)
            self.progress = self.clock() + 30
        self.sleep(min(5, self.remaining(deadline)))

    def observation(self):
        value = self.native.observe()
        self.evidence.save('observation', observed=value)
        if value.get('boot_id') is not None:
            canonical_uuid(value['boot_id'])
        require(value.get('state') != 'refused', 'native authenticated guard refused: ' + str(value.get('error')))
        return value

    def qualify(self, boot):
        # Crucially precedes BOTH baseline RAM staging and NVMe synchronization.
        self.intents.check(boot)
        self.remaining(self.native.phase_deadline)
        component = self.native.components(boot)
        self.evidence.save('components', observed=component)
        self.remaining(self.native.phase_deadline)
        self.intents.check(boot)
        self.native.stage(boot)
        self.remaining(self.native.phase_deadline)
        self.intents.check(boot)
        qualified = self.native.qualify(boot)
        self.evidence.save('qualified', boot_id=boot, references=qualified)
        return qualified

    def baseline(self, expected):
        deadline = min(self.deadline, self.clock() + BOOT_SECONDS)
        self.native.phase_deadline = deadline
        while True:
            self.remaining(deadline)
            observed = self.observation()
            if observed.get('boot_id'):
                require(observed['boot_id'] == expected, 'baseline boot differs')
                self.intents.check(expected)
            if observed.get('state') == 'ready':
                self.remaining(deadline)
                self.seen.add(expected)
                qualified = self.qualify(expected)
                self.remaining(deadline)
                return qualified
            self.pause(deadline)

    def drain_uart(self, uart, raw, boot):
        while True:
            self.remaining()
            piece = uart.read()
            if not piece:
                return
            require(len(raw) + len(piece) <= UART_LIMIT, 'UART capture limit exceeded')
            self.evidence.save('uart', old_boot_id=boot, offset=len(raw),
                               data_base64=base64.b64encode(piece).decode('ascii'))
            raw.extend(piece)

    def transition(self, kind, boot, qualified, cycle):
        self.remaining()
        self.intents.check(boot)
        uart = self.native.uart()
        raw, attested_offset = bytearray(), None
        try:
            intent = dict(schema=1, old_boot_id=boot, request_id=str(uuid.uuid4()),
                          action='reboot' if kind == 'warm' else 'poweroff',
                          source_pins=PINS, uart_start=uart.mark(), cycle=cycle, **qualified)
            # Reserve bounded partial transport, UART and qualification captures.
            self.evidence.reserve(40 * MIB)
            # Both publications must be durable before opening the sole R transaction.
            self.intents.create(intent)
            self.evidence.save('requested', **intent)
            deadline = min(self.deadline, self.clock() + BOOT_SECONDS)
            powerdown_deadline = min(deadline, self.clock() + 60)
            self.native.phase_deadline = deadline
            response = self.native.request(intent)
            self.evidence.save('request-result', old_boot_id=boot, raw=response,
                               acknowledgement='unknown unless retained helper receipt proves it')
            new_boot = None
            while True:
                self.remaining(deadline)
                self.drain_uart(uart, raw, boot)
                if kind == 'cold' and attested_offset is None:
                    if b'reboot: Power down' not in raw:
                        self.remaining(powerdown_deadline)
                        self.pause(powerdown_deadline)
                        continue
                    require(not any(marker in raw[raw.find(b'reboot: Power down'):] for marker in MARKERS),
                            'cold boot appeared before physical attestation')
                    attested_offset = len(raw)
                    require(self.native.attest(self.deadline) is True, 'physical attestation refused')
                    self.evidence.save('physical-attestation', old_boot_id=boot,
                                       uart_offset=attested_offset, minimum_disconnect_seconds=10)
                    # Operator may take time, but the series cap is never extended.
                    deadline = min(self.deadline, self.clock() + BOOT_SECONDS)
                    self.native.phase_deadline = deadline
                observed = self.observation()
                current = observed.get('boot_id')
                if current and current != boot:
                    require(current not in self.seen, 'reused boot UUID')
                    require(new_boot in (None, current), 'boot changed twice before qualification')
                    new_boot = current
                if observed.get('state') == 'ready' and current != boot and uart_evidence(
                        raw, kind, attested_offset):
                    self.remaining(deadline)
                    self.drain_uart(uart, raw, boot)
                    require(uart_evidence(raw, kind, attested_offset), 'UART interval changed before qualification')
                    # A distinct authenticated boot and ordered UART resolve outcome,
                    # never alter the unknown acknowledgement of the old request.
                    qualified = self.qualify(current)
                    self.drain_uart(uart, raw, boot)
                    require(uart_evidence(raw, kind, attested_offset), 'UART interval changed during qualification')
                    self.remaining(deadline)
                    self.seen.add(current)
                    self.completed.append(current)
                    self.evidence.save('completed-transition', old_boot_id=boot, boot_id=current,
                                       proposed_count=len(self.completed), request_id=intent['request_id'])
                    return current, qualified
                # The old boot is observed only: no stage/qualify/admission reopen.
                self.pause(deadline)
        finally:
            uart.close()

    def run(self, kind, count, expected):
        boot = expected
        qualified = self.baseline(expected)
        for cycle in range(1, count + 1):
            boot, qualified = self.transition(kind, boot, qualified, cycle)
        return self.evidence.save('series-complete', completed_count=len(self.completed),
                    distinct_boot_ids=sorted(self.seen), proposed_transitions=self.completed,
                    source_pins=PINS, custody='off-device evidence and full boundary SPI snapshots required')


def observe_only(native, evidence):
    # Existing context is read, never modified. Exactly one new artifact directory.
    contexts = sorted(evidence.original.glob('*-requested.json'))
    require(contexts, 'existing request context required')
    records = [json.loads(read_private(path, 256 * 1024)) for path in contexts]
    old = canonical_uuid(records[-1]['old_boot_id'])
    observed = native.observe()
    current = observed.get('boot_id') if observed.get('state') in ('ready', 'busy') else None
    if current is not None:
        canonical_uuid(current)
    return evidence.save('observe-only', old_boot_id=old, observed=observed,
                    outcome=('distinct boot observed' if current and current != old else
                             'old boot' if current == old else 'uncertain'),
                    source_pins=PINS, credit=False)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--execute', action='store_true')
    mode.add_argument('--observe-only', action='store_true')
    parser.add_argument('--kind', choices=('warm', 'cold'))
    parser.add_argument('--count', type=int)
    parser.add_argument('--expected-start-boot-id')
    parser.add_argument('--tag')
    args = parser.parse_args(argv)
    try:
        if args.execute:
            require(args.kind is not None and args.count is not None and 1 <= args.count <= 10,
                    '--execute requires --kind and --count 1..10')
            canonical_uuid(args.expected_start_boot_id)
            tag_value(args.tag)
            if args.kind == 'cold':
                require(sys.stdin.isatty() and sys.stdout.isatty(), 'cold execution requires local interactive TTY')
        elif args.observe_only:
            tag_value(args.tag)
            require(args.kind is args.count is args.expected_start_boot_id is None,
                    '--observe-only accepts only --tag')
    except (ValueError, AttributeError, TypeError) as error:
        parser.error(str(error))
    return args


def main(argv=None):
    args = parse_args(argv)
    if not args.execute and not args.observe_only:
        print(json.dumps(dict(operation='plan', hardware_acceptance=False, power_requested=False,
                    max_seconds=SERIES_SECONDS, new_boot_seconds=BOOT_SECONDS,
                    requires='explicit remaining count, authoritative boot UUID, unique tag; coordinator review')))
        return 0
    evidence = lock = None
    try:
        started = time.monotonic()
        captured = source_bytes()
        evidence = Evidence(REPO / 'out', args.tag, observe=args.observe_only)
        evidence.save('source-pins', source_pins=PINS)
        if args.execute:
            lock = ExecutionLock(REPO / 'out')
        native = Native(captured, evidence, started + SERIES_SECONDS)
        if args.observe_only:
            result = observe_only(native, evidence)
        else:
            result = Collector(native, evidence, Intents(REPO / 'out')).run(
                                args.kind, args.count, args.expected_start_boot_id)
        print(json.dumps(result, sort_keys=True))
        return 0
    except BaseException as error:
        if evidence is not None:
            evidence.save('incomplete', error=type(error).__name__ + ': ' + str(error)[:2048])
        print('SPI collector stopped: ' + str(error)[:2048], file=sys.stderr)
        return 1
    finally:
        if lock is not None:
            lock.close()


if __name__ == '__main__':
    sys.exit(main())
