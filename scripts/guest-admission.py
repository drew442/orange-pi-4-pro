"""Protected, RAM-only guest admission. No CLI and no storage authentication.

Trusted root callers cooperate through this protocol; a lease does not authenticate
any guest disk. Callers must close contexts explicitly. Lock FDs are close-on-exec
unless a launcher explicitly retains its validated shared lease descriptor.
"""
import contextlib
import ctypes
import fcntl
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import stat
import time
import threading
import uuid

RUNTIME_PARENT = '/run'
RUNTIME = '/run/a733-admission'
PROFILE = 'a733-admission-v1'
MAX_RECORD = 4096
GATE_TIMEOUT = 5.0
ROOT_UID = 0
ROOT_GID = 0
PROC_LIMIT = 65536
INITIAL_NAMESPACES = {'pid': 0xEFFFFFFC, 'user': 0xEFFFFFFD}
_SPEC = importlib.util.spec_from_file_location(
    'admission_run_permit', Path(__file__).resolve().parents[1] / 'configs/guest_run_permit.py')
PERMIT = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(PERMIT)


class AdmissionError(RuntimeError):
    pass


class PublicationError(AdmissionError):
    """A failed publication; visible state may differ from acknowledged state."""
    def __init__(self, stage, uncertain):
        super().__init__('admission publication failed at ' + stage)
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
        _readonly(self.fd)
        self.admission._named(self.fd, 'leases.lock', 0o640)
        return self.fd

    def close(self):
        fd, self.fd = self.fd, None
        if fd is not None:
            os.close(fd)  # Linux close errors never authorize retrying this number.

    def __enter__(self):
        self.recheck()
        return self

    def __exit__(self, *unused):
        self.close()


def _readonly(fd):
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    require(flags & os.O_ACCMODE == os.O_RDONLY and not flags & os.O_PATH,
            'lock descriptor must be a readable readonly file')


def _guest_name(name):
    require(type(name) is str and
            re.fullmatch(r'[a-z](?:[a-z0-9-]{0,14}[a-z0-9])?', name) is not None,
            'invalid guest lock name')
    return name + '.lock'


def _sync_directory(fd, gid):
    sync_fd = os.open('.', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                      | os.O_CLOEXEC, dir_fd=fd)
    try:
        require(_same(os.fstat(sync_fd), os.fstat(fd)), 'directory changed before fsync')
        _metadata(sync_fd, 0o710, gid, True)
        os.fsync(sync_fd)
    finally:
        os.close(sync_fd)


class GuestLock:
    """Retained guest exclusion subordinate to a live admission lease.

    Never releases the caller's lease. Closing a gate does not invalidate this
    capability; closing its lease does. FDs remain close-on-exec by default.
    """
    def __init__(self, admission, lease, name, directory, fd):
        self.admission, self.lease, self.name = admission, lease, name
        self.directory, self.fd = directory, fd

    def recheck(self):
        require(self.fd is not None and self.directory is not None, 'guest lock closed')
        self.admission._check_lease(self.lease)
        self.admission._check_guests_directory(self.directory)
        _readonly(self.fd)
        info = _metadata(self.fd, 0o640, self.admission.gid)
        require(_same(info, os.stat(_guest_name(self.name), dir_fd=self.directory,
                                   follow_symlinks=False)), 'guest lock named inode changed')
        return self.fd

    def close(self):
        # Linux closes even when close reports EINTR: never retry a descriptor
        # number that another thread could already have reused.
        fd, directory = self.fd, self.directory
        self.fd = self.directory = None
        try:
            if fd is not None:
                os.close(fd)
        finally:
            if directory is not None:
                os.close(directory)

    def __enter__(self):
        self.recheck()
        return self

    def __exit__(self, *unused):
        self.close()


def _proc_read(path):
    with open(path, 'rb') as stream:
        data = stream.read(PROC_LIMIT + 1)
    require(0 < len(data) <= PROC_LIMIT, 'empty or oversized proc input')
    return data


def _boot_id():
    return _proc_read('/proc/sys/kernel/random/boot_id').decode('ascii').strip()


def _process(pid):
    data = _proc_read('/proc/' + str(pid) + '/stat')
    # comm may contain spaces, newlines and closing parentheses.
    boundary = data.rfind(b') ')
    require(boundary > 0, 'invalid process stat')
    prefix, fields = data[:boundary], data[boundary + 2:].split()
    require(prefix.split(b' (', 1)[0] == str(pid).encode('ascii') and
            len(fields) >= 20 and fields[0] not in (b'Z', b'X', b'x'),
            'invalid or dead process')
    require(re.fullmatch(rb'[0-9]+', fields[1]) is not None and
            re.fullmatch(rb'[0-9]+', fields[19]) is not None, 'invalid process identity')
    return {'pid': pid, 'start_ticks': int(fields[19])}, int(fields[1])


def _status(pid):
    result = {}
    for line in _proc_read('/proc/' + str(pid) + '/status').decode('ascii').splitlines():
        if ':' in line:
            key, value = line.split(':', 1)
            require(key not in result, 'duplicate process status field')
            result[key] = value.split()
    return result


def _held_lock(fd, mode, owner):
    """Inspect the actual open description; never acquire or convert its flock."""
    _readonly(fd)
    info = os.fstat(fd)
    fields = {}
    for line in _proc_read('/proc/self/fdinfo/' + str(fd)).decode('ascii').splitlines():
        key, separator, value = line.partition(':')
        require(separator and key not in fields, 'invalid descriptor information')
        fields[key] = value.strip()
    require(re.fullmatch(r'[0-7]+', fields.get('flags', '')) is not None and
            re.fullmatch(r'[0-9]+', fields.get('ino', '')) is not None,
            'invalid descriptor flags/inode')
    flags = int(fields['flags'], 8)
    require(flags & os.O_ACCMODE == os.O_RDONLY and not flags & os.O_PATH and
            int(fields['ino']) == info.st_ino, 'descriptor information differs')
    match = re.fullmatch(r'[0-9]+: FLOCK\s+ADVISORY\s+(READ|WRITE)\s+'
                         r'([0-9]+) ([0-9a-fA-F]+):([0-9a-fA-F]+):([0-9]+) 0 EOF',
                         fields.get('lock', ''))
    require(match is not None, 'missing or invalid inherited flock')
    access, pid, major, minor, inode = match.groups()
    require(access == mode and int(pid) == owner and
            (int(major, 16), int(minor, 16), int(inode)) ==
            (os.major(info.st_dev), os.minor(info.st_dev), info.st_ino),
            'inherited flock identity or mode differs')
    return info


class InitializationLock:
    """Root-only, nonblocking exclusion for runtime creation and offline recovery.

    The portable ABI is RUNTIME_PARENT + '/a733-admission-init.lock': root:root,
    regular, 0600, one link, empty, on the validated parent tmpfs. This grants
    exclusion only, never admission or ownership of an existing closure. An
    offline caller must additionally obtain leases.lock EX when runtime exists.
    Call check() before protected work and close() explicitly in the creator
    process/thread. The held open description is readonly and close-on-exec.
    """
    NAME = 'a733-admission-init.lock'

    def __init__(self):
        self.pid = os.getpid()
        self.thread = threading.current_thread()
        self.chain = []
        self.parent = self.fd = None

    def _creator(self):
        require(os.getpid() == self.pid and threading.current_thread() is self.thread,
                'initialization lock belongs to another process or thread')

    def _parent(self):
        require(self.parent is not None, 'initialization lock closed')
        for parent, name, fd in self.chain:
            info = os.fstat(fd)
            _ancestor(info)
            if parent is not None:
                require(_same(info, os.stat(name, dir_fd=parent, follow_symlinks=False)),
                        'initialization ancestor changed')
        _tmpfs(self.parent)

    def _named(self, fd):
        self._parent()
        info = _metadata(fd, 0o600, ROOT_GID)
        require(info.st_size == 0, 'initialization lock must be empty')
        require(_same(info, os.stat(self.NAME, dir_fd=self.parent,
                                    follow_symlinks=False)),
                'initialization lock named inode changed')
        return info

    @classmethod
    def acquire(cls):
        require(os.geteuid() == 0, 'root initialization controller required')
        require(type(RUNTIME_PARENT) is str and RUNTIME_PARENT.startswith('/')
                and RUNTIME_PARENT != '/' and
                all(part not in ('', '.', '..') for part in RUNTIME_PARENT[1:].split('/')),
                'invalid fixed runtime parent')
        result = cls()
        creation = None
        try:
            fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
            result.chain.append((None, '', fd))
            for name in RUNTIME_PARENT[1:].split('/'):
                parent = fd
                fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                             | os.O_CLOEXEC, dir_fd=parent)
                result.chain.append((parent, name, fd))
            result.parent = fd
            result._parent()
            try:
                creation = os.open(cls.NAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                                   | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=fd)
            except FileExistsError:
                pass  # Existing metadata and bytes are never repaired or reset.
            else:
                result._parent()
                os.fchown(creation, ROOT_UID, ROOT_GID)
                os.fchmod(creation, 0o600)
                result._named(creation)
                os.fsync(creation)
                number, creation = creation, None
                os.close(number)
                os.fsync(fd)
            result.fd = os.open(cls.NAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                                | os.O_CLOEXEC, dir_fd=fd)
            result._named(result.fd)
            _lock(result.fd, fcntl.LOCK_EX, 0)
            result.check()
            return result
        except BaseException:
            try:
                if creation is not None:
                    number, creation = creation, None
                    os.close(number)
            finally:
                result.close()
            raise

    def check(self):
        self._creator()
        require(os.geteuid() == 0, 'root initialization controller required')
        require(self.fd is not None, 'initialization lock closed')
        self._named(self.fd)
        _held_lock(self.fd, 'WRITE', self.pid)
        return self.fd

    recheck = check

    def close(self):
        self._creator()  # Even a closed guard cannot be used by another creator.
        fd, chain = self.fd, self.chain
        self.fd = self.parent = None
        self.chain = []
        errors = []
        for number in ([fd] if fd is not None else []) + [item[2] for item in reversed(chain)]:
            try:
                os.close(number)
            except BaseException as error:
                errors.append(error)
        if errors:
            raise errors[0]

    def __enter__(self):
        self.check()
        return self

    def __exit__(self, *unused):
        self.close()


class Permit:
    """Retained protected bytes, not launch or storage authority by themselves."""
    def __init__(self, admission, run_id, directory, fd, data):
        self.admission, self.run_id = admission, run_id
        self.directory, self.fd, self.data = directory, fd, data

    @property
    def value(self):
        return PERMIT.validate_permit(self.data)

    def recheck(self):
        require(self.fd is not None and self.directory is not None, 'permit closed')
        self.admission._check_permits_directory(self.directory)
        _readonly(self.fd)
        self.admission._permit_named(self.fd, self.directory, self.run_id)
        data = os.pread(self.fd, PERMIT.LIMIT + 1, 0)
        require(data == self.data and PERMIT.canonical(PERMIT.validate_permit(data)) == data,
                'permit bytes changed or noncanonical')
        self.admission._permit_named(self.fd, self.directory, self.run_id)
        require(self.value['run_id'] == self.run_id, 'permit run differs')
        return self.value

    def close(self):
        fd, directory = self.fd, self.directory
        self.fd = self.directory = None
        try:
            if fd is not None:
                os.close(fd)
        finally:
            if directory is not None:
                os.close(directory)

    def __enter__(self):
        self.recheck()
        return self

    def __exit__(self, *unused):
        self.close()


class ManagedLocks:
    """Borrow exact inherited lock descriptions. Never dup, flock or close them.

    The caller owns the permit and admission lifetimes. A gate closure is valid
    during a run and does not invalidate this retained capability.
    """
    def __init__(self, admission, permit):
        self.admission, self.permit = admission, permit
        value = permit.recheck()
        self.fds = self.fdtuple = (value['locks']['lease']['fd'], value['locks']['guest']['fd'])

    def recheck(self):
        value = self.permit.recheck()
        admission = self.admission
        require(value['boot_id'] == admission.boot_id == _boot_id() and
                value['kvm_gid'] == admission.gid, 'managed host identity differs')
        parent, child = value['parent'], value['child']
        require(child['pid'] == os.getpid() and parent['pid'] == os.getppid(),
                'permit is not for this direct child')
        require(_process(child['pid']) == (child, parent['pid']) and
                _process(parent['pid'])[0] == parent, 'stale managed process identity')
        parent_status, child_status = _status(parent['pid']), _status(child['pid'])
        require(parent_status.get('Uid') == [str(ROOT_UID)] * 4, 'managed parent is not root')
        config = value['config']
        require(child_status.get('Uid') == [str(config['uid'])] * 4 and
                child_status.get('Gid') == [str(config['gid'])] * 4 and
                child_status.get('Groups') == [str(admission.gid)] and
                child_status.get('NoNewPrivs') == ['1'], 'managed child credentials differ')
        for key in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb'):
            entry = child_status.get(key, [])
            require(len(entry) == 1 and re.fullmatch(r'[0-9a-fA-F]+', entry[0]) is not None
                    and int(entry[0], 16) == 0, 'managed child capabilities remain')
        for pid, status in ((parent['pid'], parent_status), (child['pid'], child_status)):
            require(status.get('NSpid') == [str(pid)], 'nested or unavailable PID namespace')
        for namespace in ('pid', 'user', 'mnt'):
            own = os.stat('/proc/self/ns/' + namespace)
            # Cross-UID namespace links are ptrace protected on Linux. Root
            # attested these exact identities before publishing this protected
            # record; the nonroot child authenticates its own accessible links.
            expected = value['namespaces'][namespace]
            require((own.st_dev, own.st_ino) == (expected['dev'], expected['ino']),
                    'managed attested namespace differs')
            if namespace in INITIAL_NAMESPACES:
                require(own.st_ino == INITIAL_NAMESPACES[namespace],
                        'initial PID and user namespaces required')
        lease = Lease(admission, self.fds[0])
        # These temporary typed wrappers borrow; their close methods are never called.
        admission._check_lease(lease)
        directory = os.open('guests', os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW
                            | os.O_CLOEXEC, dir_fd=admission.directory)
        try:
            guest = GuestLock(admission, lease, config['name'], directory, self.fds[1])
            guest.recheck()
            for key, fd, mode in (('lease', self.fds[0], 'READ'),
                                  ('guest', self.fds[1], 'WRITE')):
                expected = value['locks'][key]
                info = _held_lock(fd, mode, parent['pid'])
                require((fd, info.st_dev, info.st_ino) ==
                        (expected['fd'], expected['dev'], expected['ino']),
                        'inherited descriptor binding differs')
            require(admission.is_managed(config['name'], lease, guest),
                    'managed name marker missing')
        finally:
            os.close(directory)
        # Detect parent death/reparenting or PID reuse across all checks.
        require(_process(parent['pid'])[0] == parent and
                _process(child['pid']) == (child, parent['pid']) and
                os.getppid() == parent['pid'], 'managed identity changed during verification')
        self.permit.recheck()
        return self.fds


class RegistryLock:
    """Root-only final lock in lease/guest/disk/registry acquisition order."""
    def __init__(self, admission, lease, fd):
        self.admission, self.lease, self.fd = admission, lease, fd

    def recheck(self):
        require(os.geteuid() == 0, 'root registry controller required')
        require(self.fd is not None, 'registry lock closed')
        self.admission._check_lease(self.lease)
        _readonly(self.fd)
        self.admission._named(self.fd, 'registry.lock', 0o600)
        return self.fd

    def close(self):
        fd, self.fd = self.fd, None
        if fd is not None:
            os.close(fd)

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
        require(type(kvm_gid) is int and 0 <= kvm_gid < 2**32 - 1,
                'invalid KVM group')
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

    @classmethod
    def open_runtime(cls, boot_id, kvm_gid):
        """Open only protected runtime directories, without taking the gate."""
        instance = cls(boot_id, kvm_gid)
        try:
            instance._directories()
            return instance
        except BaseException:
            instance.close()
            raise

    def _check_permits_directory(self, fd):
        self.recheck()
        info = _metadata(fd, 0o710, self.gid, True)
        require(_same(info, os.stat('permits', dir_fd=self.directory,
                                   follow_symlinks=False)), 'permit directory changed')

    def _permit_named(self, fd, directory, run_id):
        self._check_permits_directory(directory)
        info = _metadata(fd, 0o440, self.gid)
        require(_same(info, os.stat(_uuid(run_id) + '.json', dir_fd=directory,
                                   follow_symlinks=False)), 'permit named inode changed')

    def open_permit(self, run_id):
        """Read protected canonical bytes; this alone grants no launch authority."""
        _uuid(run_id)
        self.recheck()
        directory = os.open('permits', os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW
                            | os.O_CLOEXEC, dir_fd=self.directory)
        fd = None
        try:
            self._check_permits_directory(directory)
            fd = os.open(run_id + '.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                         | os.O_CLOEXEC, dir_fd=directory)
            self._permit_named(fd, directory, run_id)
            data = os.pread(fd, PERMIT.LIMIT + 1, 0)
            result = Permit(self, run_id, directory, fd, data)
            result.recheck()
            return result
        except BaseException:
            try:
                if fd is not None:
                    os.close(fd)
            finally:
                os.close(directory)
            raise

    def validate_inherited(self, permit):
        require(isinstance(permit, Permit) and permit.admission is self,
                'retained permit from this admission required')
        result = ManagedLocks(self, permit)
        result.recheck()
        return result

    def _check_permit_publication(self, value, lease, guest_lock):
        require(os.geteuid() == 0, 'root permit controller required')
        require(value['boot_id'] == self.boot_id == _boot_id() and
                value['kvm_gid'] == self.gid, 'permit host binding differs')
        require(value['parent']['pid'] == os.getpid() and
                _process(os.getpid())[0] == value['parent'] and
                _status(os.getpid()).get('Uid') == [str(ROOT_UID)] * 4,
                'permit parent identity differs')
        require(_process(value['child']['pid']) == (value['child'], os.getpid()),
                'permit child is not the current direct child')
        for namespace in ('pid', 'user', 'mnt'):
            own = os.stat('/proc/self/ns/' + namespace)
            init = os.stat('/proc/1/ns/' + namespace)
            expected = value['namespaces'][namespace]
            require(_same(own, init) and
                    (own.st_dev, own.st_ino) == (expected['dev'], expected['ino']),
                    'permit native namespace binding differs')
            if namespace in INITIAL_NAMESPACES:
                require(own.st_ino == INITIAL_NAMESPACES[namespace],
                        'initial PID and user namespaces required')
        require(_status(os.getpid()).get('NSpid') == [str(os.getpid())],
                'nested or unavailable parent PID namespace')
        name = value['config']['name']
        self._check_guest(name, lease, guest_lock)
        require(not lease.exclusive, 'shared managed lease required')
        for key, capability, mode in (('lease', lease, 'READ'),
                                      ('guest', guest_lock, 'WRITE')):
            expected = value['locks'][key]
            info = _held_lock(capability.fd, mode, os.getpid())
            require((capability.fd, info.st_dev, info.st_ino) ==
                    (expected['fd'], expected['dev'], expected['ino']),
                    'permit lock binding differs')
        require(self.is_managed(name, lease, guest_lock), 'managed name marker missing')

    def publish_permit(self, value, lease, guest_lock, *, guard):
        """Publish a per-run pre-exec child binding with no overwrite or repair.

        guard authenticates HostContext and the durable admitted head externally.
        It runs around each mutation, as well as entry and final verification.
        Partial protected files are retained on failure and cannot be adopted.
        """
        require(os.geteuid() == 0, 'root permit controller required')
        require(callable(guard), 'trusted admitted-head guard required')
        data = PERMIT.canonical(value)
        value = PERMIT.validate_permit(data)
        def check():
            self._check_permit_publication(value, lease, guest_lock)
            guard()
            self._check_permit_publication(value, lease, guest_lock)
        check()
        directory = fd = result = None
        stage, uncertain = 'directory', False
        try:
            try:
                os.mkdir('permits', 0o710, dir_fd=self.directory)
            except FileExistsError:
                pass
            else:
                # O_PATH is retained separately; mutation descriptors are never
                # returned to children or retained by a Permit.
                check()
                directory = os.open('permits', os.O_RDONLY | os.O_DIRECTORY
                                    | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=self.directory)
                check()
                os.fchown(directory, ROOT_UID, self.gid)
                check()
                os.fchmod(directory, 0o710)
                check()
                self._check_permits_directory(directory)
                number, directory = directory, None
                os.close(number)
            check()
            directory = os.open('permits', os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW
                                | os.O_CLOEXEC, dir_fd=self.directory)
            self._check_permits_directory(directory)
            filename = value['run_id'] + '.json'
            stage = 'create'
            check()
            uncertain = True
            try:
                fd = os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                             | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                             0o600, dir_fd=directory)
            except FileExistsError:
                with self.open_permit(value['run_id']) as existing:
                    require(existing.data == data, 'existing permit differs')
                    check()
                    os.fsync(existing.fd)
                    check()
            else:
                check()
                os.fchown(fd, ROOT_UID, self.gid)
                check()
                stage = 'write'
                remaining = data
                while remaining:
                    count = os.write(fd, remaining)
                    require(count > 0, 'short permit write')
                    remaining = remaining[count:]
                    check()
                # Keep partially written bytes root-only until complete.
                stage = 'file-fsync'
                os.fsync(fd)
                check()
                os.fchmod(fd, 0o440)
                check()
                self._permit_named(fd, directory, value['run_id'])
                os.fsync(fd)
                check()
            stage = 'directory-fsync'
            self._check_permits_directory(directory)
            _sync_directory(directory, self.gid)
            check()
            _sync_directory(self.directory, self.gid)
            check()
            stage = 'verify'
            result = self.open_permit(value['run_id'])
            try:
                require(result.data == data, 'published permit differs')
                check()
                result.recheck()
            except BaseException:
                result.close()
                raise
        except BaseException as error:
            raise PublicationError(stage, uncertain) from error
        finally:
            cleanup_error = None
            descriptors = (fd, directory)
            fd = directory = None
            for number in descriptors:
                if number is not None:
                    try:
                        os.close(number)
                    except BaseException as error:
                        if cleanup_error is None:
                            cleanup_error = error
            if cleanup_error is not None:
                try:
                    if result is not None:
                        result.close()
                finally:
                    raise PublicationError('close', uncertain) from cleanup_error
        return result

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

    def _check_lease(self, lease):
        require(isinstance(lease, Lease) and lease.admission is self,
                'guest lock requires this admission lease')
        lease.recheck()

    def _check_guests_directory(self, fd):
        self.recheck()
        info = _metadata(fd, 0o710, self.gid, True)
        require(_same(info, os.stat('guests', dir_fd=self.directory,
                                   follow_symlinks=False)), 'guest lock directory changed')

    def guest_lock(self, name, lease, *, create=False, timeout=0):
        """Take guest EX after a lease, without reacquiring the gate.

        Root preparation creates each stable inode once. A creation failure may
        leave a partial protected object; it is never replaced or repaired here.
        Caller must keep the admission lease open until after GuestLock.close().
        """
        filename = _guest_name(name)
        require(type(create) is bool, 'create must be boolean')
        require(type(timeout) in (int, float) and math.isfinite(timeout)
                and 0 <= timeout <= 300, 'invalid lock timeout')
        self._check_lease(lease)
        if create:
            require(os.geteuid() == 0, 'root guest-lock preparation required')
            try:
                os.mkdir('guests', 0o710, dir_fd=self.directory)
            except FileExistsError:
                pass
            else:
                directory = os.open('guests', os.O_RDONLY | os.O_DIRECTORY
                                    | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=self.directory)
                try:
                    os.fchown(directory, ROOT_UID, self.gid)
                    os.fchmod(directory, 0o710)
                    self._check_guests_directory(directory)
                    _sync_directory(directory, self.gid)
                    _sync_directory(self.directory, self.gid)
                finally:
                    os.close(directory)
        directory = os.open('guests', os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW
                            | os.O_CLOEXEC, dir_fd=self.directory)
        fd = None
        try:
            self._check_lease(lease)
            self._check_guests_directory(directory)
            if create:
                try:
                    created_fd = os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                                         | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                                         0o600, dir_fd=directory)
                except FileExistsError:
                    pass
                else:
                    try:
                        os.fchown(created_fd, ROOT_UID, self.gid)
                        os.fchmod(created_fd, 0o640)
                        _metadata(created_fd, 0o640, self.gid)
                        self._check_lease(lease)
                        self._check_guests_directory(directory)
                        require(_same(os.fstat(created_fd), os.stat(
                            filename, dir_fd=directory, follow_symlinks=False)),
                            'created guest lock changed')
                        os.fsync(created_fd)
                    finally:
                        os.close(created_fd)
            fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                         | os.O_CLOEXEC, dir_fd=directory)
            result = GuestLock(self, lease, name, directory, fd)
            result.recheck()
            if create:
                # Also sync safe existing files: an earlier creator may have
                # failed after creation but before acknowledging fsync.
                os.fsync(fd)
                _sync_directory(directory, self.gid)
                _sync_directory(self.directory, self.gid)
            _lock(fd, fcntl.LOCK_EX, timeout)
            result.recheck()
            return result
        except BaseException:
            try:
                if fd is not None:
                    os.close(fd)
            finally:
                os.close(directory)
            raise

    def registry_lock(self, lease, timeout=0):
        """Take registry EX last; never takes gate, guest or disk locks."""
        require(os.geteuid() == 0, 'root registry controller required')
        require(type(timeout) in (int, float) and math.isfinite(timeout)
                and 0 <= timeout <= 300, 'invalid lock timeout')
        self._check_lease(lease)
        try:
            created = os.open('registry.lock', os.O_WRONLY | os.O_CREAT | os.O_EXCL
                              | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                              0o600, dir_fd=self.directory)
        except FileExistsError:
            pass
        else:
            try:
                os.fchown(created, ROOT_UID, self.gid)
                os.fchmod(created, 0o600)
                self._named(created, 'registry.lock', 0o600)
                os.fsync(created)
            finally:
                os.close(created)
        fd = self._open('registry.lock', 0o600)
        try:
            result = RegistryLock(self, lease, fd)
            result.recheck()
            os.fsync(fd)
            _sync_directory(self.directory, self.gid)
            _lock(fd, fcntl.LOCK_EX, timeout)
            result.recheck()
            return result
        except BaseException:
            os.close(fd)
            raise

    def _check_managed_directory(self, fd):
        self.recheck()
        info = _metadata(fd, 0o710, self.gid, True)
        require(_same(info, os.stat('managed', dir_fd=self.directory,
                                   follow_symlinks=False)), 'managed directory changed')

    def _managed_directory(self):
        fd = os.open('managed', os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW
                     | os.O_CLOEXEC, dir_fd=self.directory)
        try:
            self._check_managed_directory(fd)
            return fd
        except BaseException:
            os.close(fd)
            raise

    def prepare_managed(self, lease):
        """Prepare protected marker directory before bootstrap opens admission."""
        require(os.geteuid() == 0, 'root managed preparation required')
        self._check_lease(lease)
        try:
            os.mkdir('managed', 0o710, dir_fd=self.directory)
        except FileExistsError:
            pass
        else:
            fd = os.open('managed', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                         | os.O_CLOEXEC, dir_fd=self.directory)
            try:
                os.fchown(fd, ROOT_UID, self.gid)
                os.fchmod(fd, 0o710)
                self._check_managed_directory(fd)
            finally:
                os.close(fd)
        fd = self._managed_directory()
        try:
            self._check_lease(lease)
            _sync_directory(fd, self.gid)
            _sync_directory(self.directory, self.gid)
        finally:
            os.close(fd)

    def _check_guest(self, name, lease, guest_lock):
        _guest_name(name)
        self._check_lease(lease)
        require(isinstance(guest_lock, GuestLock) and guest_lock.admission is self
                and guest_lock.lease is lease and guest_lock.name == name,
                'matching current guest lock required')
        guest_lock.recheck()

    def _marker_bytes(self, name):
        _guest_name(name)
        return (json.dumps(dict(schema=1, profile='a733-managed-name-v1',
                                boot_id=self.boot_id, name=name), sort_keys=True,
                           separators=(',', ':')) + '\n').encode('ascii')

    def _marker_named(self, fd, directory, filename):
        self._check_managed_directory(directory)
        info = _metadata(fd, 0o440, self.gid)
        require(_same(info, os.stat(filename, dir_fd=directory, follow_symlinks=False)),
                'managed marker named inode changed')

    def _read_marker(self, directory, name):
        filename = name + '.json'
        try:
            fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                         | os.O_CLOEXEC, dir_fd=directory)
        except FileNotFoundError:
            self._check_managed_directory(directory)
            return None
        try:
            # Prove tmpfs and regular single-link inode before any read/atime.
            self._marker_named(fd, directory, filename)
            data = os.read(fd, MAX_RECORD + 1)
            require(len(data) <= MAX_RECORD, 'managed marker too large')
            # Equality to the sole canonical document also rejects duplicate,
            # unknown, malformed, foreign-boot and incorrectly typed fields.
            require(data == self._marker_bytes(name), 'invalid managed marker')
            self._marker_named(fd, directory, filename)
            return fd
        except BaseException:
            os.close(fd)
            raise

    def is_managed(self, name, lease, guest_lock):
        """A legacy exclusion marker confers no managed launch permission."""
        self._check_guest(name, lease, guest_lock)
        directory = self._managed_directory()  # Missing preparation refuses.
        try:
            fd = self._read_marker(directory, name)
            if fd is None:
                self._check_guest(name, lease, guest_lock)
                return False
            try:
                self._check_guest(name, lease, guest_lock)
                return True
            finally:
                os.close(fd)
        finally:
            os.close(directory)

    def mark_managed(self, name, lease, guest_lock, *, guard):
        """Publish exclusion BEFORE persistent first-head creation.

        guard() must authenticate current storage and trusted enrollment intent;
        this module authenticates neither. Call it before mutation and after
        durability. Never remove the marker on disable or ordinary failure.
        PublicationError uncertainty forbids blind retry of a failed transaction.
        """
        require(os.geteuid() == 0, 'root managed controller required')
        require(callable(guard), 'trusted enrollment guard required')
        self._check_guest(name, lease, guest_lock)
        guard()
        self._check_guest(name, lease, guest_lock)
        directory = self._managed_directory()
        fd = None
        temporary = None
        stage, uncertain = 'inspect', False
        failure = None
        try:
            fd = self._read_marker(directory, name)
            if fd is None:
                stage = 'create'
                temporary = '.managed-' + uuid.uuid4().hex
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                             | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                             0o600, dir_fd=directory)
                os.fchown(fd, ROOT_UID, self.gid)
                os.fchmod(fd, 0o440)
                self._marker_named(fd, directory, temporary)
                data = self._marker_bytes(name)
                stage = 'write'
                while data:
                    written = os.write(fd, data)
                    require(written > 0, 'short managed marker write')
                    data = data[written:]
                stage = 'file-fsync'
                os.fsync(fd)
                self._check_guest(name, lease, guest_lock)
                self._marker_named(fd, directory, temporary)
                stage = 'link'
                # Set uncertainty before the syscall: injected/asynchronous
                # failure may occur after the namespace operation succeeded.
                uncertain = True
                os.link(temporary, name + '.json', src_dir_fd=directory,
                        dst_dir_fd=directory, follow_symlinks=False)
                stage = 'temporary-unlink'
                require(_same(os.fstat(fd), os.stat(temporary, dir_fd=directory,
                                                   follow_symlinks=False)),
                        'temporary marker changed')
                os.unlink(temporary, dir_fd=directory)
                temporary = None
                self._marker_named(fd, directory, name + '.json')
            else:
                uncertain = True  # Existing exclusion remains on any failure.
                stage = 'file-fsync'
                os.fsync(fd)
            stage = 'directory-fsync'
            _sync_directory(directory, self.gid)
            _sync_directory(self.directory, self.gid)
            stage = 'guard'
            self._check_guest(name, lease, guest_lock)
            self._marker_named(fd, directory, name + '.json')
            guard()
            self._check_guest(name, lease, guest_lock)
            self._marker_named(fd, directory, name + '.json')
        except BaseException as error:
            failure = PublicationError(stage, uncertain)
            failure.__cause__ = error
        finally:
            # Clean only our retained temporary inode. Never touch the final
            # name; even failed publication may have established exclusion.
            if temporary is not None and fd is not None:
                try:
                    info = os.stat(temporary, dir_fd=directory, follow_symlinks=False)
                    require(_same(info, os.fstat(fd)), 'temporary marker changed')
                    os.unlink(temporary, dir_fd=directory)
                except FileNotFoundError:
                    pass
                except BaseException as error:
                    if failure is None:
                        failure = PublicationError('cleanup', uncertain)
                        failure.__cause__ = error
            for number in (fd, directory):
                if number is not None:
                    try:
                        os.close(number)  # Never retry a failed close.
                    except BaseException as error:
                        if failure is None:
                            failure = PublicationError('close', uncertain)
                            failure.__cause__ = error
        if failure is not None:
            raise failure

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
        errors = []
        owner, self.bootstrap_owner = self.bootstrap_owner, None
        if owner is not None and owner.lease is not None:
            try:
                owner.lease.close()
            except BaseException as error:
                errors.append(error)
        descriptors = []
        for field in ('gate_fd', 'leases_fd'):
            fd = getattr(self, field)
            setattr(self, field, None)
            if fd is not None:
                descriptors.append(fd)
        chain, self.chain = self.chain, []
        self.directory = None
        descriptors.extend(fd for _, _, fd in reversed(chain))
        for fd in descriptors:
            try:
                os.close(fd)
            except BaseException as error:
                errors.append(error)
        if errors:
            raise errors[0]

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
    # Only /run's direct child is supported; physical parent validation precedes mkdir.
    require(os.path.dirname(RUNTIME) == RUNTIME_PARENT
            and os.path.basename(RUNTIME) == 'a733-admission',
            'invalid fixed runtime path')
    require(type(kvm_gid) is int and 0 <= kvm_gid < 2**32 - 1,
            'invalid KVM group')
    interlock = InitializationLock.acquire()
    admission = None
    try:
        admission = _initialize_locked(boot_id, kvm_gid, manager_state, interlock)
        interlock.check()
    except BaseException:
        try:
            if admission is not None:
                admission.close()
        finally:
            interlock.close()
        raise
    try:
        interlock.close()
    except BaseException:
        admission.close()
        raise
    return admission


def _initialize_locked(boot_id, kvm_gid, manager_state, interlock):
    created = False
    interlock.check()
    parent = os.open(RUNTIME_PARENT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(parent)
        _ancestor(info)
        _tmpfs(parent)
        try:
            interlock.check()
            os.mkdir(RUNTIME.rsplit('/', 1)[1], 0o710, dir_fd=parent)
            created = True
            interlock.check()
            directory = os.open(RUNTIME.rsplit('/', 1)[1], os.O_RDONLY | os.O_DIRECTORY
                                | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
            try:
                interlock.check()
                os.fchown(directory, ROOT_UID, kvm_gid)
                interlock.check()
                os.fchmod(directory, 0o710)
                for name in ('gate.lock', 'leases.lock'):
                    interlock.check()
                    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                                 | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=directory)
                    try:
                        interlock.check()
                        os.fchown(fd, ROOT_UID, kvm_gid)
                        interlock.check()
                        os.fchmod(fd, 0o640)
                        interlock.check()
                        os.fsync(fd)
                    finally:
                        os.close(fd)
                interlock.check()
                os.fsync(directory)
            finally:
                os.close(directory)
        except FileExistsError:
            pass
    finally:
        os.close(parent)
    interlock.check()
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
                interlock.check()
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
