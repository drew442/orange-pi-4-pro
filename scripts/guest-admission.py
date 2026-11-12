"""Protected, RAM-only guest admission. No CLI and no storage authentication.

Trusted root callers cooperate through this protocol; a lease does not authenticate
any guest disk. Callers must close contexts explicitly. Lock FDs are close-on-exec
unless a launcher explicitly retains its validated shared lease descriptor.
"""
import contextlib
import ctypes
import fcntl
import json
import math
import os
import stat
import time
import uuid

RUNTIME_PARENT = '/run'
RUNTIME = '/run/a733-admission'
PROFILE = 'a733-admission-v1'
MAX_RECORD = 4096
GATE_TIMEOUT = 5.0
ROOT_UID = 0


class AdmissionError(RuntimeError):
    pass


class PublicationError(AdmissionError):
    """A failed publication; visible state may differ from acknowledged state."""
    def __init__(self, stage, uncertain):
        super().__init__('gate publication failed at ' + stage)
        self.stage = stage
        self.uncertain = uncertain


def _ancestor(info):
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == ROOT_UID
            and not info.st_mode & 0o022, 'unsafe admission ancestor')


def require(condition, message):
    if not condition:
        raise AdmissionError(message)


def _tmpfs(fd):
    result = ctypes.create_string_buffer(256)
    libc = ctypes.CDLL(None, use_errno=True)
    require(libc.fstatfs(fd, result) == 0, 'cannot inspect filesystem')
    require(ctypes.c_long.from_buffer(result).value == 0x01021994,
            'admission must reside on tmpfs')


def _uuid(value):
    require(type(value) is str, 'UUID must be text')
    try:
        require(str(uuid.UUID(value)) == value, 'UUID must be canonical')
    except ValueError as error:
        raise AdmissionError('invalid UUID') from error
    return value


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate gate field')
        result[key] = value
    return result


def encode(record):
    require(type(record) is dict and set(record) == {
        'schema', 'profile', 'state', 'generation', 'boot_id', 'owner_nonce', 'reason'
    }, 'invalid gate fields')
    require(type(record['schema']) is int and record['schema'] == 1,
            'unsupported gate schema')
    require(record['profile'] == PROFILE, 'unsupported gate profile')
    require(record['state'] in ('open', 'closed'), 'invalid gate state')
    require(type(record['generation']) is int and 0 <= record['generation'] < 2**63,
            'invalid gate generation')
    _uuid(record['boot_id'])
    _uuid(record['owner_nonce'])
    require(record['reason'] in ('bootstrap', 'maintenance', 'shutdown'),
            'invalid closure reason')
    require(not (record['state'] == 'open' and record['reason'] == 'shutdown'),
            'shutdown cannot open')
    data = (json.dumps(record, sort_keys=True, separators=(',', ':'),
                       ensure_ascii=True) + '\n').encode('ascii')
    require(len(data) <= MAX_RECORD, 'gate too large')
    return data


def decode(data):
    require(type(data) is bytes and len(data) <= MAX_RECORD, 'gate too large')
    try:
        record = json.loads(data.decode('utf-8'), object_pairs_hook=_pairs)
    except (UnicodeError, ValueError) as error:
        raise AdmissionError('invalid gate JSON') from error
    require(encode(record) == data, 'gate must use canonical JSON')
    return record


def _metadata(fd, mode, gid, directory=False):
    info = os.fstat(fd)
    require((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            and info.st_uid == ROOT_UID and info.st_gid == gid
            and stat.S_IMODE(info.st_mode) == mode,
            'unsafe admission metadata')
    if not directory:
        require(info.st_nlink == 1, 'admission files must have one link')
    _tmpfs(fd)
    return info


def _same(left, right):
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _lock(fd, mode, timeout):
    require(type(timeout) in (int, float) and math.isfinite(timeout)
            and 0 <= timeout <= 300, 'invalid lock timeout')
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(fd, mode | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise AdmissionError('admission lock timeout') from None
            time.sleep(min(0.01, max(0, deadline - time.monotonic())))


def _manager_state():
    """No implicit launch permission: integration supplies trusted PID1 query."""
    raise AdmissionError('trusted PID1 manager-state query required')


class Lease:
    def __init__(self, admission, fd, exclusive=False):
        self.admission, self.fd, self.exclusive = admission, fd, exclusive

    def recheck(self):
        require(self.fd is not None, 'lease closed')
        self.admission.recheck()
        self.admission._named(self.fd, 'leases.lock', 0o640)
        return self.fd

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        self.recheck()
        return self

    def __exit__(self, *unused):
        self.close()


class ClosureOwner:
    def __init__(self, admission, record):
        self.admission = admission
        self.nonce = record['owner_nonce']
        self.reason = record['reason']
        self.lease = None
        self.poisoned = False

    def _matching(self):
        require(not self.poisoned, 'owner failed; recovery required')
        record = self.admission._read()
        require(record['state'] == 'closed' and record['owner_nonce'] == self.nonce
                and record['reason'] == self.reason, 'closure ownership changed')
        return record

    def exclusive(self, timeout=0):
        require(self.lease is None or self.lease.fd is None, 'exclusive lease already held')
        with self.admission._gate():
            self._matching()
        fd = self.admission._open('leases.lock', 0o640)
        try:
            _lock(fd, fcntl.LOCK_EX, timeout)
            with self.admission._gate():
                self._matching()
            self.lease = Lease(self.admission, fd, True)
            return self.lease
        except BaseException:
            os.close(fd)
            raise

    def open(self):
        require(self.reason != 'shutdown', 'shutdown cannot reopen')
        require(self.lease is not None and self.lease.exclusive,
                'exclusive lease required')
        self.lease.recheck()
        with self.admission._gate():
            old = self._matching()
            new = dict(old, state='open', generation=old['generation'] + 1)
            try:
                self.admission._publish(new)
            except BaseException as error:
                self.poisoned = True
                # Rename(open) is the commit boundary: prerequisites must already
                # be complete. A crash after it may leave an unacknowledged open.
                try:
                    self.admission._publish(old)
                except BaseException as rollback_error:
                    failure = PublicationError(getattr(error, 'stage', 'open'), True)
                    failure.rollback_error = rollback_error
                    raise failure from error
                if isinstance(error, PublicationError):
                    error.uncertain = False
                raise


class Admission:
    def __init__(self, boot_id, kvm_gid, manager_state=_manager_state):
        self.boot_id = _uuid(boot_id)
        require(type(kvm_gid) is int and kvm_gid >= 0, 'invalid KVM group')
        self.gid, self.manager_state = kvm_gid, manager_state
        self.chain = []
        self.directory = self.gate_fd = self.leases_fd = None
        self.bootstrap_owner = None

    @classmethod
    def open_existing(cls, boot_id, kvm_gid, manager_state=_manager_state):
        instance = cls(boot_id, kvm_gid, manager_state)
        try:
            instance._directories()
            instance.gate_fd = instance._open('gate.lock', 0o640)
            instance.leases_fd = instance._open('leases.lock', 0o640)
            with instance._gate():
                instance._read()
            return instance
        except BaseException:
            instance.close()
            raise

    def _directories(self):
        fd = os.open('/', os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)
        self.chain.append((None, '', fd))
        components = RUNTIME.strip('/').split('/')
        for component in components:
            parent = fd
            fd = os.open(component, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW
                         | os.O_CLOEXEC, dir_fd=parent)
            self.chain.append((parent, component, fd))
        self.directory = fd
        self.recheck()

    def recheck(self):
        require(self.directory is not None, 'admission closed')
        for parent, name, fd in self.chain:
            info = os.fstat(fd)
            _ancestor(info)
            if parent is not None:
                require(_same(info, os.stat(name, dir_fd=parent, follow_symlinks=False)),
                        'admission ancestor changed')
        _metadata(self.directory, 0o710, self.gid, True)
        for fd, name in ((self.gate_fd, 'gate.lock'), (self.leases_fd, 'leases.lock')):
            if fd is not None:
                self._named(fd, name, 0o640)

    def _named(self, fd, name, mode):
        info = _metadata(fd, mode, self.gid)
        require(_same(info, os.stat(name, dir_fd=self.directory, follow_symlinks=False)),
                'admission named inode changed')

    def _open(self, name, mode):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                     dir_fd=self.directory)
        try:
            self._named(fd, name, mode)
            return fd
        except BaseException:
            os.close(fd)
            raise

    @contextlib.contextmanager
    def _gate(self):
        self.recheck()
        # Separate open descriptions avoid nested/concurrent operations sharing flock.
        fd = self._open('gate.lock', 0o640)
        try:
            _lock(fd, fcntl.LOCK_EX, GATE_TIMEOUT)
            self.recheck()
            self._named(fd, 'gate.lock', 0o640)
            yield
        finally:
            os.close(fd)

    def _read(self):
        self.recheck()
        fd = self._open('gate.json', 0o440)
        try:
            data = os.read(fd, MAX_RECORD + 1)
            self._named(fd, 'gate.json', 0o440)
            record = decode(data)
            require(record['boot_id'] == self.boot_id, 'different host boot')
            return record
        finally:
            os.close(fd)

    def _publish(self, record):
        require(os.geteuid() == 0, 'root controller required')
        self.recheck()
        data = encode(record)
        name = '.gate-' + uuid.uuid4().hex
        fd = None
        stage = 'create'
        renamed = False
        try:
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                         | os.O_CLOEXEC, 0o600, dir_fd=self.directory)
            os.fchown(fd, ROOT_UID, self.gid)
            os.fchmod(fd, 0o440)
            _metadata(fd, 0o440, self.gid)
            with os.fdopen(os.dup(fd), 'wb') as stream:
                stream.write(data)
                stream.flush()
            stage = 'file-fsync'
            os.fsync(fd)
            self.recheck()
            self._named(fd, name, 0o440)
            stage = 'rename'
            os.replace(name, 'gate.json', src_dir_fd=self.directory,
                       dst_dir_fd=self.directory)
            renamed = True
            stage = 'directory-fsync'
            # Nonroot traversal uses O_PATH: 0710 grants search, not listing.
            sync_fd = os.open('.', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                              | os.O_CLOEXEC, dir_fd=self.directory)
            try:
                require(_same(os.fstat(sync_fd), os.fstat(self.directory)),
                        'directory changed before fsync')
                _metadata(sync_fd, 0o710, self.gid, True)
                os.fsync(sync_fd)
            finally:
                os.close(sync_fd)
        except BaseException as error:
            raise PublicationError(stage, renamed) from error
        finally:
            if fd is not None:
                os.close(fd)
            try:
                os.unlink(name, dir_fd=self.directory)
            except FileNotFoundError:
                pass

    def admit(self):
        with self._gate():
            require(self.manager_state() in ('starting', 'running', 'degraded'),
                    'PID1 not accepting admission')
            require(self._read()['state'] == 'open', 'admission closed')
            fd = self._open('leases.lock', 0o640)
            try:
                _lock(fd, fcntl.LOCK_SH, 0)
                self.recheck()
                return Lease(self, fd)
            except BaseException:
                os.close(fd)
                raise

    def close_gate(self, reason='maintenance'):
        require(os.geteuid() == 0, 'root controller required')
        require(reason in ('maintenance', 'shutdown'), 'invalid closure request')
        with self._gate():
            old = self._read()
            if old['state'] == 'closed' and not (
                    reason == 'shutdown' and old['reason'] != 'shutdown'):
                return None
            record = dict(old, state='closed', reason=reason,
                          owner_nonce=str(uuid.uuid4()), generation=old['generation'] + 1)
            self._publish(record)
            return ClosureOwner(self, record)

    def close(self):
        if self.bootstrap_owner and self.bootstrap_owner.lease:
            self.bootstrap_owner.lease.close()
        for field in ('gate_fd', 'leases_fd'):
            fd = getattr(self, field)
            if fd is not None:
                os.close(fd)
                setattr(self, field, None)
        for _, _, fd in reversed(self.chain):
            os.close(fd)
        self.chain = []
        self.directory = None

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.close()


def initialize(boot_id, kvm_gid, manager_state=_manager_state):
    """Create fresh closed bootstrap state; existing state is validated, never reset.

    Fresh .bootstrap_owner retains lease EX. Open it only after preparation;
    release .bootstrap_owner.lease explicitly before admitting any run.
    """
    require(os.geteuid() == 0, 'root controller required')
    _uuid(boot_id)
    created = False
    # Only /run's direct child is supported; physical parent validation precedes mkdir.
    require(os.path.dirname(RUNTIME) == RUNTIME_PARENT
            and os.path.basename(RUNTIME) == 'a733-admission',
            'invalid fixed runtime path')
    parent = os.open(RUNTIME_PARENT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(parent)
        _ancestor(info)
        _tmpfs(parent)
        try:
            os.mkdir(RUNTIME.rsplit('/', 1)[1], 0o710, dir_fd=parent)
            created = True
            directory = os.open(RUNTIME.rsplit('/', 1)[1], os.O_RDONLY | os.O_DIRECTORY
                                | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
            try:
                os.fchown(directory, ROOT_UID, kvm_gid)
                os.fchmod(directory, 0o710)
                for name in ('gate.lock', 'leases.lock'):
                    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                                 | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=directory)
                    try:
                        os.fchown(fd, ROOT_UID, kvm_gid)
                        os.fchmod(fd, 0o640)
                        os.fsync(fd)
                    finally:
                        os.close(fd)
                os.fsync(directory)
            finally:
                os.close(directory)
        except FileExistsError:
            pass
    finally:
        os.close(parent)
    if not created:
        return Admission.open_existing(boot_id, kvm_gid, manager_state)
    admission = Admission(boot_id, kvm_gid, manager_state)
    try:
        admission._directories()
        admission.gate_fd = admission._open('gate.lock', 0o640)
        admission.leases_fd = admission._open('leases.lock', 0o640)
        with admission._gate():
            lease_fd = admission._open('leases.lock', 0o640)
            try:
                _lock(lease_fd, fcntl.LOCK_EX, 0)
                record = dict(schema=1, profile=PROFILE, state='closed', generation=0,
                              boot_id=boot_id, owner_nonce=str(uuid.uuid4()), reason='bootstrap')
                admission._publish(record)
                owner = ClosureOwner(admission, record)
                owner.lease = Lease(admission, lease_fd, True)
                admission.bootstrap_owner = owner
            except BaseException:
                os.close(lease_fd)
                raise
        return admission
    except BaseException:
        admission.close()
        raise
