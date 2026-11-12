"""Read-only destination custody for same-device managed restoration.

Shutdown exclusion grants no reopen or launch permission. Durable cohort proof
is distinct from ordinary boot readiness: RAM accounts/markers may be absent.
Trusted root writers must cooperate; this is not protection against hostile root.
No payload, registry, lifecycle record or transaction is published here.
"""
import copy
from contextlib import ExitStack
import errno
import fcntl
import hashlib
import importlib.util
import os
from pathlib import Path
import re
import stat
import threading
import time

BASE = Path(__file__).resolve().parents[1]


def module(name, relative):
    spec = importlib.util.spec_from_file_location(name, BASE / relative)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


SENDER = module('restore_custody_sender', 'scripts/backup-managed-guest.py')
MAINT, CP = SENDER.MAINT, SENDER.CP
LIFECYCLE, POLICY = CP.LIFECYCLE, CP.POLICY
CONTEXT, ADMISSION = LIFECYCLE.CONTEXT, LIFECYCLE.ADMISSION
BOOT_POLICY = module('restore_custody_policy', 'scripts/guest-boot-policy.py')
ARCHIVE = module('restore_custody_decoder', 'scripts/managed-guest-archive.py')
FORMAT, HEAD, REGISTRY, ENROLL = SENDER.FORMAT, CP.HEAD, CP.REGISTRY, CP.ENROLL
require = ADMISSION.require


class CustodyError(RuntimeError):
    def __init__(self, receipt, primary):
        self.receipt, self.primary = copy.deepcopy(receipt), primary
        self.receipt['primary_error'] = repr(primary)
        if hasattr(primary, 'receipt'):
            self.receipt['nested_error'] = copy.deepcopy(primary.receipt)
        super().__init__(f"{self.receipt['phase']}: {type(primary).__name__}: {primary}")


def installed_guard(boot_id):
    require(BASE == Path('/usr/lib/a733-tools'), 'installed restore source required')
    CONTEXT.host_identity(boot_id)
    CONTEXT.MANAGEMENT.runtime_guard()
    rows = CONTEXT.MANAGEMENT.mount_table(CONTEXT.STORAGE.read_bounded(
        '/proc/self/mountinfo', CONTEXT.STORAGE.MOUNTINFO_LIMIT))
    root = rows['/'][0]
    require('ro' in root['flags'] and 'rw' not in root['flags'], 'readonly installed root required')
    for path in (BASE, BASE / 'scripts', BASE / 'configs', Path(__file__)):
        info = os.lstat(path)
        require(info.st_uid == info.st_gid == 0 and not info.st_mode & 0o022 and
                not stat.S_ISLNK(info.st_mode) and info.st_dev == os.stat('/').st_dev,
                'unsafe installed source')


def consumer_check(uid, identities, *, retained_fds=(), proc_root=Path('/proc')):
    """Bound every task's UID, FD table, mappings and cwd/root/exe references.

    Ignore our own retained descriptors only. A surviving target UID task refuses
    even without an observed reference, preventing nonroot reopen races. Vanished
    tasks/FDs are permissible; permissions and malformed/unbounded input refuse.
    """
    identities = set(identities)
    require(type(retained_fds) in (tuple, list), 'exact retained descriptor list required')
    retained = {}
    for fd in retained_fds:
        require(type(fd) is int and fd >= 0 and fd not in retained, 'invalid retained descriptor')
        ADMISSION._readonly(fd)
        retained[fd] = ENROLL.identity(os.fstat(fd))
    deadline, tasks, descriptors, mappings, processes = time.monotonic() + 10, 0, 0, 0, 0
    def check():
        require(time.monotonic() <= deadline, 'restore consumer census timeout')
    def gone(error):
        if error.errno not in (errno.ENOENT, errno.ESRCH):
            raise error
    def reference(path, *, own_fd=None, task=None):
        try:
            info = os.stat(path)
            if ENROLL.inode(info) not in identities:
                return
            require(own_fd is not None and own_fd in retained and
                    ENROLL.identity(info) == retained[own_fd] == ENROLL.identity(os.fstat(own_fd)),
                    'restore target has a task consumer')
            ADMISSION._readonly(own_fd)
            data = CONTEXT.read_system(task / 'fdinfo' / str(own_fd), CONTEXT.STORAGE.FDINFO_LIMIT)
            flags = [line.split()[1:] for line in data.decode('ascii').splitlines()
                     if line.startswith('flags:')]
            require(len(flags) == 1 and len(flags[0]) == 1 and
                    re.fullmatch(r'[0-7]+', flags[0][0]), 'invalid retained task descriptor flags')
            value = int(flags[0][0], 8)
            require(value & os.O_ACCMODE == os.O_RDONLY and not value & os.O_PATH,
                    'retained task descriptor must be readonly')
        except OSError as error:
            gone(error)
    with os.scandir(proc_root) as entries:
        for process in entries:
            check()
            if not process.name.isdecimal():
                continue
            processes += 1
            require(processes <= MAINT.PID_LIMIT, 'restore process census limit exceeded')
            own_process = int(process.name) == os.getpid()
            try:
                with os.scandir(Path(process.path) / 'task') as threads:
                    for thread in threads:
                        check()
                        tasks += 1
                        require(thread.name.isdecimal() and tasks <= MAINT.PID_LIMIT,
                                'restore task census limit exceeded')
                        try:
                            status = CONTEXT.read_system(Path(thread.path) / 'status', 65536)
                            rows = [line.split()[1:] for line in status.decode('ascii').splitlines()
                                    if line.startswith('Uid:')]
                            require(len(rows) == 1 and len(rows[0]) == 4 and
                                    all(v.isdecimal() for v in rows[0]), 'invalid task UID observation')
                            require(str(uid) not in rows[0], 'target UID task survives shutdown')
                            for name in ('cwd', 'root', 'exe'):
                                reference(Path(thread.path) / name)
                            with os.scandir(Path(thread.path) / 'fd') as files:
                                for entry in files:
                                    check()
                                    descriptors += 1
                                    require(entry.name.isdecimal() and descriptors <= MAINT.FD_LIMIT,
                                            'restore descriptor census limit exceeded')
                                    reference(entry.path, own_fd=int(entry.name) if own_process else None,
                                              task=Path(thread.path))
                            with open(Path(thread.path) / 'maps', 'rb') as stream:
                                while True:
                                    check()
                                    line = stream.readline(65537)
                                    if not line:
                                        break
                                    mappings += 1
                                    require(len(line) <= 65536 and mappings <= MAINT.FD_LIMIT,
                                            'restore mapping census limit exceeded')
                                    fields = line.split(None, 5)
                                    require(len(fields) >= 5 and re.fullmatch(
                                        rb'[0-9a-f]+:[0-9a-f]+', fields[3]) and
                                        fields[4].isdigit(), 'invalid task mapping')
                                    major, minor = (int(v, 16) for v in fields[3].split(b':'))
                                    require((os.makedev(major, minor), int(fields[4])) not in identities,
                                            'restore target has a mapped consumer')
                        except OSError as error:
                            gone(error)
            except OSError as error:
                gone(error)


class Resources:
    """Exhaustive cleanup with a preserved primary failure and all close errors."""
    def __init__(self):
        self._pid, self._thread = os.getpid(), threading.get_ident()
        self._closed, self._resources = False, []
        self.receipt = dict(phase='validate', success=False, hardware_acceptance=False,
                            cleanup_uncertain=False, close_errors=[])

    def creator(self):
        require((os.getpid(), threading.get_ident()) == (self._pid, self._thread),
                'restore custody creator process/thread differs')
        require(threading.current_thread() is threading.main_thread(), 'restore requires main thread')

    def retain(self, item, name):
        self._resources.append((name, item.close))
        return item

    def fd(self, fd, name):
        self._resources.append((name, lambda: os.close(fd)))
        return fd

    def close(self, primary=None):
        self.creator()
        if self._closed:
            if primary is not None:
                raise CustodyError(self.receipt, primary) from primary
            return
        self._closed = True
        resources, self._resources = self._resources, []
        for name, callback in reversed(resources):
            try:
                callback()
            except BaseException as error:
                record = dict(resource=name, error=repr(error))
                if hasattr(error, 'errors'):
                    record['errors'] = [repr(item) for item in error.errors]
                if hasattr(error, 'receipt'):
                    record['receipt'] = copy.deepcopy(error.receipt)
                self.receipt['close_errors'].append(record)
        self.receipt['cleanup_uncertain'] = bool(self.receipt['close_errors'])
        if primary is not None or self.receipt['cleanup_uncertain']:
            cause = primary if primary is not None else RuntimeError('restore cleanup uncertain')
            raise CustodyError(self.receipt, cause) from cause

    def __enter__(self):
        try:
            self.recheck()
            return self
        except BaseException as error:
            self.close(error)

    def __exit__(self, _kind, error, _traceback):
        self.close(error)


class ProtectedRecords:
    """Retained safe reads; semantic failures remain primary during cleanup."""
    def __init__(self, owner):
        self.owner, self.files = owner, []

    def read(self, store, name, limit):
        store.check()
        initial = os.stat(name, dir_fd=store.fd, follow_symlinks=False)
        CP.STORE._regular(initial, os.fstat(store.fd).st_dev, limit)
        fd = self.owner.fd(os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK |
                                 os.O_CLOEXEC, dir_fd=store.fd), name)
        raw = store._read_retained(fd, name, initial, limit, None)
        self.files.append((store, name, fd, initial, limit, raw))
        return raw

    def recheck(self):
        for store, name, fd, initial, limit, raw in self.files:
            require(store._read_retained(fd, name, initial, limit, CP.digest(raw)) == raw,
                    'retained restore lifecycle changed')

    def recheck_bindings(self):
        """Reject changed records/ancestors after long reads without rereading IO.

        Exact inode, size, ownership, mode, link count and mtime/ctime bind the
        bytes already authenticated above. Hostile root metadata tampering is
        outside the cooperative-writer threat model.
        """
        for store, name, fd, initial, limit, _raw in self.files:
            store.check()
            current = os.fstat(fd)
            CP.STORE._regular(current, os.fstat(store.fd).st_dev, limit)
            named = os.stat(name, dir_fd=store.fd, follow_symlinks=False)
            require(CP.STORE._metadata(current) == CP.STORE._metadata(initial) ==
                    CP.STORE._metadata(named), 'retained restore lifecycle binding changed')


class ShutdownRestoreCustody(Resources):
    """Own retained installed policy, shutdown EX and physical HostContext.

    expected_policy is exact decoded policy data, not authority. Only existing
    admission runtime is used. Closing never reopens, resets or initializes it.
    """
    def __init__(self, expected_policy, name, *, progress=None):
        super().__init__()
        self.name, self.progress = name, progress
        self.policy = self.admission = self.lease = self.controller = None
        self._children = set()
        try:
            self.creator()
            require(os.getresuid() == (0, 0, 0), 'actual root required for restore')
            ADMISSION._guest_name(name)
            require(name != 'firstkvm', 'firstkvm cannot be restored')
            expected = BOOT_POLICY.SCHEMA.decode(BOOT_POLICY.SCHEMA.canonical(expected_policy))
            raw = CONTEXT.read_system('/proc/sys/kernel/random/boot_id', 64)
            require(raw.endswith(b'\n'), 'invalid boot UUID')
            self.boot_id = ADMISSION._uuid(raw[:-1].decode('ascii'))
            installed_guard(self.boot_id)
            self.policy = self.retain(BOOT_POLICY.Policy(self.boot_id), 'policy')
            require(type(self.policy) is BOOT_POLICY.Policy and self.policy.value == expected,
                    'installed storage policy differs')
            self.expected_policy = expected
            require(MAINT.NORMAL.manager_state() in ('starting', 'running', 'degraded'),
                    'PID1 does not permit restore')
            self.receipt['phase'] = 'open-existing'
            self.admission = self.retain(ADMISSION.Admission.open_existing(
                self.boot_id, MAINT.NORMAL.local_kvm_gid(), MAINT.NORMAL.manager_state), 'admission')
            self.receipt['phase'] = 'close-shutdown'
            # Deliberately discard ownership. Observation of an existing shutdown
            # closure is equally valid and never adopts a nonce.
            self.admission.close_gate('shutdown')
            self.receipt['phase'] = 'stop-managed'
            MAINT.stop_managed()
            self.receipt['phase'] = 'shutdown-exclusive'
            self.lease = self.retain(self.admission.shutdown_lease(), 'shutdown-lease')
            self.receipt['phase'] = 'physical-context'
            self.controller = self.retain(LIFECYCLE.StateController(
                expected['storage'], self.lease, boot_id=self.boot_id), 'controller')
            self.recheck()
            MAINT.consumer_check()
            _, registry = self.registry()
            self.entry = self._entry(registry, name)
            for entry in registry['guests']:
                if entry['name'] != name:
                    with self.inspect(entry['name'], entry['manifest_sha256']):
                        pass
            self.receipt['phase'] = 'shutdown-custody'
        except BaseException as error:
            self.close(error)

    @property
    def context(self):
        self.recheck()
        return self.controller.context

    def recheck(self):
        self.creator()
        require(not self._closed and os.getresuid() == (0, 0, 0), 'restore custody unavailable')
        installed_guard(self.boot_id)
        require(type(self.policy) is BOOT_POLICY.Policy, 'actual installed policy required')
        self.policy.check()
        require(self.policy.value == self.expected_policy, 'storage policy changed')
        require(CONTEXT.module('restore_canonical_admission', 'scripts/guest-admission.py') is ADMISSION,
                'canonical admission source required')
        require(type(self.admission) is ADMISSION.Admission and type(self.lease) is ADMISSION.Lease and
                self.lease.admission is self.admission and self.lease.exclusive,
                'actual shutdown EX lease required')
        self.lease.recheck()
        ADMISSION._readonly(self.lease.fd)
        ADMISSION._held_lock(self.lease.fd, 'WRITE', self._pid)
        gate = self.admission._read()
        require(gate['state'] == 'closed' and gate['reason'] == 'shutdown', 'shutdown gate differs')
        require(type(self.controller) is LIFECYCLE.StateController and
                type(self.controller.context) is CONTEXT.HostContext and
                self.controller.context.lease is self.lease and
                self.controller.context.storage == self.expected_policy['storage'],
                'actual same-family physical context required')
        self.controller.context.guard()

    def _entry(self, registry, name, pin=None):
        require(registry['storage'] == self.expected_policy['storage'], 'registry storage differs')
        entries = [e for e in registry['guests'] if e['name'] == name]
        require(len(entries) == 1 and name != 'firstkvm', 'existing managed destination required')
        entry = entries[0]
        require(entry['shutdown_profile'] == 'qmp-guest-shutdown-v1' and
                entry['fixture_profile'] in ('none', CP.FIXTURE.PROFILE), 'unsupported profiles')
        if pin is not None:
            require(entry['manifest_sha256'] == pin, 'current registry pin differs')
        return copy.deepcopy(entry)

    def registry(self):
        self.recheck()
        resources = Resources()
        resources.recheck = self.recheck
        with resources:
            lock = resources.retain(self.admission.registry_lock(self.lease), 'registry-lock')
            def guard():
                self.recheck()
                lock.recheck()
                ADMISSION._held_lock(lock.fd, 'WRITE', self._pid)
            store = resources.retain(CP.STORE.RootDirectory(
                self.controller.context.state_fd, 'guests', guard=guard), 'registry-directory')
            records = ProtectedRecords(resources)
            raw = records.read(store, 'autostart.json', REGISTRY.LIMIT)
            value = REGISTRY.validate_registry(raw)
            require(value['storage'] == self.expected_policy['storage'] and
                    REGISTRY.canonical(value) == raw, 'canonical registry/storage required')
            records.recheck()
            guard()
            return raw, copy.deepcopy(value)

    def inspect(self, name, pin):
        self.recheck()
        return RestoreInspection(self, name, pin)

    def snapshot_target(self):
        self.recheck()
        return TargetSnapshot(self)

    def close(self, primary=None):
        self.creator()
        # Children own locks subordinate to this lease. Close them first, even
        # when a caller omitted their own context manager during failure.
        for child in list(self._children):
            try:
                child.close()
            except BaseException as error:
                self.receipt['close_errors'].append(dict(resource='child', error=repr(error)))
        super().close(primary)


class NamedPayload(Resources):
    def __init__(self, custody, name):
        super().__init__()
        require(type(custody) is ShutdownRestoreCustody, 'actual restore custody required')
        custody.recheck()
        self.custody, self.name = custody, name
        self._infos, self._opened, self.fds = {}, [], {}
        self.guest_fd = None
        self.lock = self.retain(custody.admission.guest_lock(name, custody.lease, create=False), 'guest-lock')
        custody._children.add(self)

    def physical(self):
        self.creator()
        require(not self._closed, 'restore payload custody closed')
        self.custody.recheck()
        require(type(self.lock) is ADMISSION.GuestLock and self.lock.lease is self.custody.lease and
                self.lock.name == self.name, 'actual matching guest lock required')
        self.lock.recheck()
        ADMISSION._held_lock(self.lock.fd, 'WRITE', self._pid)
        for fd in self._opened:
            self._held(fd)

    def _open(self, name, parent, directory=False):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC |
                     (os.O_DIRECTORY if directory else 0), dir_fd=parent)
        self.fd(fd, name)
        self._opened.append(fd)
        self._infos[fd] = (parent, name, os.fstat(fd))
        storage = CONTEXT.STORAGE
        mount = lambda number: storage.fd_mount_id(storage.read_bounded(
            '/proc/self/fdinfo/' + str(number), storage.FDINFO_LIMIT))
        require(mount(fd) == mount(parent), 'unexpected target mount boundary')
        return fd

    _held = POLICY.Inspection._held
    _read = POLICY.Inspection._read
    _metadata = POLICY.Inspection._metadata

    def _hash(self, fd, size):
        self._held(fd)
        digest, count = hashlib.sha256(), 0
        progress = self.custody.progress
        if progress:
            progress(dict(name=self.name, file=self._infos[fd][1], bytes=0, total=size))
        while count < size:
            data = os.pread(fd, min(ENROLL.CHUNK, size - count), count)
            require(bool(data), 'restore input truncated')
            count += len(data)
            digest.update(data)
            if progress:
                progress(dict(name=self.name, file=self._infos[fd][1], bytes=count, total=size))
        require(not os.pread(fd, 1, count), 'restore input grew')
        self._held(fd)
        return dict(bytes=count, sha256=digest.hexdigest())

    def close(self, primary=None):
        self.creator()
        self.custody._children.discard(self)
        super().close(primary)


class HistoryProof:
    """Pure history validation over actual owner-retained protected readers."""
    def __init__(self, owner):
        self.owner, self.entries, self.directory = owner, {}, None
        backup = SENDER.LEGACY.BACKUP
        try:
            self.directory = owner._open('.checkpoints', owner.guest_fd, directory=True)
        except FileNotFoundError:
            self.payload = backup.history_payload({}, owner.manifest_data)
            return
        info = os.fstat(self.directory)
        require(info.st_uid == info.st_gid == 0 and stat.S_IMODE(info.st_mode) == 0o700 and
                info.st_dev == owner._guest_info.st_dev, 'unsafe checkpoint history directory')
        names = []
        with os.scandir(self.directory) as rows:
            for row in rows:
                names.append(row.name)
                require(len(names) <= backup.ENTRY_COUNT, 'too many checkpoint records')
        encoded = 0
        for name in sorted(names):
            require(backup.NAME_RE.fullmatch(name), 'invalid checkpoint filename')
            fd = owner._open(name, self.directory)
            info = os.fstat(fd)
            owner._metadata(info, 0, 0, 0o400)
            require(0 < info.st_size <= backup.ENTRY_LIMIT, 'checkpoint record size differs')
            encoded += ((info.st_size + 2) // 3) * 4 + len(name)
            require(encoded <= backup.HISTORY_LIMIT, 'checkpoint history exceeds limit')
            owner._hash(fd, info.st_size)  # bounded byte progress for bulk history reads
            self.entries[name] = owner._read(fd, info.st_size)
        self.payload = backup.history_payload(self.entries, owner.manifest_data)
        self.recheck()

    def recheck(self):
        if self.directory is None:
            try:
                os.stat('.checkpoints', dir_fd=self.owner.guest_fd, follow_symlinks=False)
            except FileNotFoundError:
                return
            raise ValueError('checkpoint history appeared')
        require(set(os.listdir(self.directory)) == set(self.entries), 'checkpoint history names changed')
        self.owner._held(self.directory)


class RestoreInspection(NamedPayload):
    """Actual retained stopped destination; never ordinary CP/launch custody."""
    def __init__(self, custody, name, pin):
        super().__init__(custody, name)
        try:
            POLICY.MANIFEST.digest(pin)
            self.pin = pin
            _, initial_registry = custody.registry()
            expected_entry = custody._entry(initial_registry, name, pin)
            consumer_check(expected_entry['uid'], [])
            self.guest_fd = self._open(name, custody.context.guest_fd, directory=True)
            self._guest_info = os.fstat(self.guest_fd)
            info = self._guest_info
            require(info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o710 and
                    info.st_dev == os.fstat(custody.context.guest_fd).st_dev, 'unsafe guest directory')
            self.manifest_fd = self._open('enrollment.json', self.guest_fd)
            self._metadata(os.fstat(self.manifest_fd), 0, 0, 0o400)
            size = os.fstat(self.manifest_fd).st_size
            require(0 < size <= POLICY.MANIFEST.LIMIT, 'invalid manifest size')
            self.manifest_data = self._read(self.manifest_fd, size)
            require(CP.digest(self.manifest_data) == pin, 'manifest pin differs')
            self.manifest = POLICY.MANIFEST.validate_manifest(self.manifest_data)
            config = self.manifest['config']
            require(all(config[k] == expected_entry[k] for k in ('name', 'uid', 'gid')) and
                    info.st_gid == config['gid'] and
                    self.manifest['guest_uuid'] == custody.expected_policy['storage']['guest_uuid'],
                    'destination manifest identity differs')
            self.context = POLICY.STORAGE.verify_guest_directory(custody.context.guest_fd)
            for key, filename in ENROLL.FILENAMES.items():
                fd = self._open(filename, self.guest_fd)
                self.fds[key] = fd
                record = self.manifest['files'][filename]
                self._metadata(os.fstat(fd), record['uid'], record['gid'], int(record['mode'], 8), record['bytes'])
            self.disk_fd = self.fds['disk']
            fcntl.flock(self.disk_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            consumer_check(expected_entry['uid'], [ENROLL.inode(os.fstat(fd)) for fd in self._opened],
                           retained_fds=self._opened)
            self.history = HistoryProof(self)
            self.stack = self.retain(ExitStack(), 'lifecycle-readers')
            self.store = custody.controller._head_store(self.stack, name, self.physical)
            self.records = ProtectedRecords(self)
            self.head_raw = self.records.read(self.store, 'head.json', HEAD.LIMIT)
            self.head = HEAD.validate_head(self.head_raw)
            self.payload_records = {}
            # Read exact required cohort records via retained protected FDs.
            if self.head['sequence'] > 0:
                runs = self.stack.enter_context(self.store.child('runs'))
                prefix = self.head['run_id']
                limits = {'.json': HEAD.LIMIT, '.console': CP.RECORDS.CONSOLE_LIMIT,
                          '.outcome.json': CP.RECORDS.OUTCOME_LIMIT, '.intent.json': CP.RECORDS.INTENT_LIMIT,
                          '.completed.json': CP.RECORDS.COMPLETION_LIMIT}
                _, registry = custody.registry()
                entry = custody._entry(registry, name, pin)
                if entry['fixture_profile'] != 'none':
                    limits['.fixture.json'] = CP.RECORDS.FIXTURE_LIMIT
                raw = {suffix: self.records.read(runs, prefix + suffix, limit)
                       for suffix, limit in limits.items()}
                for suffix, data in raw.items():
                    self.payload_records['runs/' + prefix + suffix] = CP.reference(data, embedded=True)
                admission = LIFECYCLE.validate_run(raw['.json'])
                intent = CP.RECORDS.validate_intent(raw['.intent.json'])
                heads = CP._heads(admission, raw['.intent.json'], intent)
                transitions = self.stack.enter_context(self.store.child('transitions'))
                for old, new in zip(heads, heads[1:]):
                    filename = CP.digest(HEAD.canonical(old)) + '-' + CP.digest(HEAD.canonical(new)) + '.json'
                    data = self.records.read(transitions, filename, LIFECYCLE.TRANSITION_LIMIT)
                    self.payload_records['transitions/' + filename] = CP.reference(data, embedded=True)
            self.recheck()
            self.receipt['phase'] = 'verified-durable-cohort'
        except BaseException as error:
            self.close(error)

    def recheck(self):
        self.physical()
        POLICY._disk_flock(self.disk_fd, self._pid)
        require(set(os.listdir(self.guest_fd)) in (
            set(ENROLL.FILENAMES.values()) | {'enrollment.json'},
            set(ENROLL.FILENAMES.values()) | {'enrollment.json', '.checkpoints'}),
            'unexpected guest payload entry')
        require(POLICY.semantic(POLICY.STORAGE.verify_guest_storage(self.disk_fd,
            boot_fds=(self.fds['kernel'], self.fds['initrd']))) == POLICY.semantic(self.context),
            'destination file placement differs')
        ENROLL.probe_uuid(self.context, self.manifest['guest_uuid'])
        POLICY.service_state(self.name)
        config = self.manifest['config']
        POLICY.ACCOUNTS.validate_accounts({'schema': 1, 'guests': [
            {k: config[k] for k in ('name', 'uid', 'gid')}]}, POLICY.ACCOUNTS.snapshot(), complete=False)
        try:
            self.custody.admission.is_managed(self.name, self.custody.lease, self.lock)
        except FileNotFoundError as error:
            require(error.filename == 'managed', 'unexpected missing managed metadata')
        consumer_check(config['uid'], [ENROLL.inode(os.fstat(fd)) for fd in self._opened],
                       retained_fds=self._opened)
        self.history.recheck()
        self.records.recheck()
        for key, fd in self.fds.items():
            require(self._hash(fd, config[key]['bytes']) == config[key], 'destination input hash differs')
        raw, registry = self.custody.registry()  # disk EX precedes this registry EX
        self.entry = self.custody._entry(registry, self.name, self.pin)
        require(all(self.entry[k] == config[k] for k in ('name', 'uid', 'gid')), 'registry identity differs')
        lifecycle = FORMAT.canonical(dict(schema=1, profile=FORMAT.PROFILE,
            registry=CP.reference(raw, embedded=True), head=CP.reference(self.head_raw, embedded=True),
            records=self.payload_records), self.manifest_data)
        cohort = ARCHIVE.validate_cohort(self.manifest_data, lifecycle)
        # Bulk input hashing and pure replay can take time. Revalidate every
        # protected named record and its retained ancestor chain at the proof
        # boundary, rather than returning a stale head/cohort observation.
        self.records.recheck_bindings()
        self.history.recheck()
        self.physical()
        self.cohort = cohort
        self.lifecycle, self.registry_raw = lifecycle, raw


class TargetSnapshot(NamedPayload):
    """Retain old target bytes without asserting old integrity or ready status.

    Explicit absence is preserved. No rename-aware publication capability is
    implied; changing any named binding invalidates this read-only observation.
    """
    def __init__(self, custody):
        super().__init__(custody, custody.name)
        self.observation = dict(payload=None, lifecycle=None)
        self._absent = []
        self.lifecycle_parent = None
        try:
            _, registry = custody.registry()
            self.entry = custody._entry(registry, self.name)
            require(self.entry == custody.entry, 'target registry identity changed')
            self._trees = []
            consumer_check(self.entry['uid'], [])
            self._snapshot('payload', custody.context.guest_fd, self.name, payload=True)
            # RootDirectory validates the protected lifecycle parent, but target
            # may be absent or contain damaged bounded bytes.
            try:
                parent = self.retain(CP.STORE.RootDirectory(custody.context.guest_fd,
                    '.a733-lifecycle', guard=custody.recheck), 'lifecycle-parent')
            except FileNotFoundError as error:
                require(error.filename == '.a733-lifecycle', 'unexpected lifecycle absence')
                self._absent.append((custody.context.guest_fd, '.a733-lifecycle'))
            else:
                self.lifecycle_parent = parent
                self._snapshot('lifecycle', parent.fd, self.name, payload=False)
            self.recheck()
            self.receipt['phase'] = 'old-target-observed'
        except BaseException as error:
            self.close(error)

    def _snapshot(self, key, parent, name, *, payload):
        try:
            directory = self._open(name, parent, directory=True)
        except FileNotFoundError:
            self._absent.append((parent, name))
            return
        info = os.fstat(directory)
        require(info.st_dev == os.fstat(self.custody.context.guest_fd).st_dev and info.st_uid == 0 and
                info.st_gid == (self.entry['gid'] if payload else 0) and
                stat.S_IMODE(info.st_mode) == (0o710 if payload else 0o700), 'unsafe old target directory')
        if payload:
            self.guest_fd, self._guest_info = directory, info
        result, budget = {}, [0, 0]
        def walk(fd, prefix, depth):
            require(depth <= 1, 'old target tree too deep')
            names = []
            with os.scandir(fd) as entries:
                for entry in entries:
                    budget[0] += 1
                    require(budget[0] <= 4096, 'old target entry limit exceeded')
                    names.append(entry.name)
            for leaf in sorted(names):
                require('/' not in leaf and leaf not in ('.', '..'), 'unsafe old target name')
                before = os.stat(leaf, dir_fd=fd, follow_symlinks=False)
                path = prefix + leaf
                if stat.S_ISDIR(before.st_mode):
                    require((payload and depth == 0 and leaf == '.checkpoints') or
                            (not payload and depth == 0 and leaf in ('runs', 'transitions')),
                            'unexpected old target directory')
                    child = self._open(leaf, fd, directory=True)
                    meta = os.fstat(child)
                    require(meta.st_uid == meta.st_gid == 0 and stat.S_IMODE(meta.st_mode) == 0o700 and
                            meta.st_dev == info.st_dev, 'unsafe old target child')
                    walk(child, path + '/', depth + 1)
                    continue
                require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and
                        before.st_dev == info.st_dev, 'unsafe old target file')
                disk = payload and path == 'disk.raw'
                if payload:
                    require((depth == 0 and leaf in set(ENROLL.FILENAMES.values()) | {'enrollment.json'}) or
                            (depth == 1 and SENDER.LEGACY.BACKUP.NAME_RE.fullmatch(leaf)),
                            'unexpected old payload name')
                else:
                    require((depth == 0 and leaf == 'head.json') or
                            (depth == 1 and re.fullmatch(r'[a-zA-Z0-9.-]+', leaf)),
                            'unexpected old lifecycle name')
                limit = POLICY.MANIFEST.CONFIG.DISK_LIMIT if disk else (
                    POLICY.MANIFEST.CONFIG.ARTIFACT_LIMIT if payload and leaf in ('Image', 'initramfs.cpio.gz')
                    else FORMAT.LIFECYCLE_LIMIT)
                require(0 <= before.st_size <= limit, 'old target file too large')
                expected_mode = 0o600 if disk else (0o444 if payload and leaf in ('Image', 'initramfs.cpio.gz') else 0o400)
                require(before.st_uid == (self.entry['uid'] if disk else 0) and
                        before.st_gid == (self.entry['gid'] if disk else 0) and
                        stat.S_IMODE(before.st_mode) == expected_mode, 'unsafe old target metadata')
                opened = self._open(leaf, fd)
                if disk:
                    fcntl.flock(opened, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self.disk_fd = opened
                budget[1] += before.st_size if not disk else 0
                require(budget[1] <= FORMAT.LIFECYCLE_LIMIT + 2 * POLICY.MANIFEST.CONFIG.ARTIFACT_LIMIT,
                        'old target aggregate size exceeded')
                consumer_check(self.entry['uid'], [ENROLL.inode(os.fstat(item)) for item in self._opened],
                               retained_fds=self._opened)
                result[path] = dict(inode=list(ENROLL.inode(os.fstat(opened))),
                                    **self._hash(opened, before.st_size))
            self._trees.append((fd, set(names)))
        walk(directory, '', 0)
        self.observation[key] = dict(inode=list(ENROLL.inode(info)), files=result)
        self.observation[key]['disk' if payload else 'head'] = copy.deepcopy(
            result.get('disk.raw' if payload else 'head.json'))

    def recheck(self):
        self.physical()
        if self.lifecycle_parent is not None:
            self.lifecycle_parent.check()
        for parent, name in self._absent:
            try:
                os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                continue
            raise ValueError('absent old target appeared')
        for fd, names in self._trees:
            require(set(os.listdir(fd)) == names, 'old target names changed')
        if hasattr(self, 'disk_fd'):
            POLICY._disk_flock(self.disk_fd, self._pid)
        consumer_check(self.entry['uid'], [ENROLL.inode(os.fstat(fd)) for fd in self._opened],
                       retained_fds=self._opened)
        _, current = self.custody.registry()
        require(self.custody._entry(current, self.name) == self.entry, 'old target registry changed')
        self.physical()
