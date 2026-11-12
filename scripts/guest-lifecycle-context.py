"""Read-only live placement authority for lifecycle storage, with no runtime CLI.

Callers still need the correct per-guest transaction lock, trusted manifest pins
and authenticated outcomes. This does not establish physical acceptance or disk
cleanliness. Guards retain MANAGEMENT.runtime_guard's STATE blkid cost; only the
additional guest UUID probe is limited to initial, explicit and final verification.
"""
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

BASE = Path(__file__).resolve().parents[1]


def module(name, relative):
    import importlib.util
    path = (BASE / relative).resolve()
    shared = relative == 'scripts/guest-admission.py'
    if shared:
        # The root image is trusted read-only source. Authenticate exactly the
        # bytes executed, never a second pathname read or a stale bytecode file.
        name = 'a733_runtime_guest_admission'
        source = path.read_bytes()
        source_hash = hashlib.sha256(source).hexdigest()
        cached = sys.modules.get(name)
        if cached is not None:
            if (Path(getattr(cached, '__file__', '')).resolve() != path or
                    getattr(cached, '_a733_source_sha256', None) != source_hash):
                raise RuntimeError('admission module origin or source revision differs')
            return cached
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    if not shared:
        spec.loader.exec_module(result)
        return result
    result._a733_source_sha256 = source_hash
    sys.modules[name] = result
    try:
        exec(compile(source, str(path), 'exec'), result.__dict__)
    except BaseException:
        if sys.modules.get(name) is result:
            del sys.modules[name]
        raise
    return result


POLICY = module('lifecycle_context_policy', 'scripts/guest-checkpoint-policy.py')
ENROLL, MANAGEMENT, STORAGE = POLICY.ENROLL, POLICY.MANAGEMENT, POLICY.STORAGE
# Context consumers share this authenticated admission module. Direct foreign
# module loads deliberately retain a different capability class identity.
ADMISSION = module('lifecycle_context_admission', 'scripts/guest-admission.py')
REGISTRY = module('lifecycle_context_registry', 'configs/guest_registry.py')
require = POLICY.require
STATE = '/run/a733-state'
GUESTS = '/run/a733-guests'
COMPATIBLE = b'xunlong,orangepi-4-pro\0arm,sun60iw2p1\0'
INITIAL_NAMESPACES = {'pid': 0xEFFFFFFC, 'user': 0xEFFFFFFD}


def read_system(path, limit):
    with Path(path).open('rb') as stream:
        data = stream.read(limit + 1)
    require(0 < len(data) <= limit, 'empty or oversized system input')
    return data


def host_identity(boot_id):
    require(os.geteuid() == 0, 'root required')
    require(read_system('/proc/sys/kernel/random/boot_id', 64) ==
            (boot_id + '\n').encode('ascii'), 'host boot differs')
    for namespace in ('pid', 'mnt', 'user'):
        mine = os.stat('/proc/self/ns/' + namespace)
        init = os.stat('/proc/1/ns/' + namespace)
        require(ENROLL.inode(mine) == ENROLL.inode(init), 'native host namespace required')
        if namespace in INITIAL_NAMESPACES:
            require(mine.st_ino == INITIAL_NAMESPACES[namespace],
                    'initial PID and user namespaces required')
    status = read_system('/proc/self/status', 65536).decode('ascii')
    pids = [line.split()[1:] for line in status.splitlines() if line.startswith('NSpid:')]
    require(pids == [[str(os.getpid())]], 'nested or unavailable host PID namespace')
    require(read_system('/proc/device-tree/compatible', 128) == COMPATIBLE,
            'board compatible differs')


def validate_storage(expected):
    # Use the exact registry schema, including rejection of extra fields/types.
    require(type(expected) is dict, 'storage dictionary required')
    record = {'schema': 1, 'profile': REGISTRY.PROFILE, 'generation': 0,
              'storage': expected, 'guests': []}
    return REGISTRY.validate_registry(json.dumps(record, allow_nan=False))['storage']


def physical_identity(info):
    # Directory timestamps/size/link count may change when unrelated guests write.
    return tuple(getattr(info, 'st_' + key) for key in ('dev', 'ino', 'mode', 'uid', 'gid'))


class HostContext:
    """Retain authenticated STATE/p5 roots; never create directories or write.

    The lease is borrowed and must outlive this context. state_fd/guest_fd are
    read-only directory descriptors and must not be closed or repurposed by callers.
    storage returns an independent copy of the caller-pinned storage tuple.
    """
    def __init__(self, expected_storage, lease, *, boot_id):
        self._held = []
        self._closed = False
        try:
            self._storage = validate_storage(expected_storage)
            REGISTRY._uuid(boot_id)
            require(type(lease) is ADMISSION.Lease, 'typed admission lease required')
            self.lease, self.boot_id = lease, boot_id
            self._host()
            MANAGEMENT.runtime_guard()
            self._state = self._hold(STATE)
            self._guests = self._hold(GUESTS)
            self.state_fd, self.guest_fd = self._state.fd, self._guests.fd
            self._identities = [physical_identity(os.fstat(item.fd)) for item in self._held]
            self._roots()
            self._placement = STORAGE.verify_guest_directory(self.guest_fd)
            self._mounts = self._mount_context()
            self.verify()
        except BaseException:
            self._release()
            raise

    @property
    def storage(self):
        return copy.deepcopy(self._storage)

    def _host(self):
        self.lease.recheck()
        require(self.boot_id == self.lease.admission.boot_id, 'lease boot differs')
        host_identity(self.boot_id)

    def _hold(self, path):
        item = ENROLL.HeldPath(path, directory=True)
        self._held.append(item)
        return item

    def _roots(self):
        for item, expected in zip(self._held, self._identities):
            info = os.fstat(item.fd)
            flags = fcntl.fcntl(item.fd, fcntl.F_GETFL)
            require(flags & os.O_ACCMODE == os.O_RDONLY and not flags & os.O_PATH,
                    'read-only root descriptor required')
            require(physical_identity(info) == expected, 'held root identity changed')
            require(stat.S_ISDIR(info.st_mode) and info.st_uid == info.st_gid == 0 and
                    not info.st_mode & 0o7022, 'unsafe storage root')
            # HeldPath pins full metadata for immutable inputs. These roots are
            # mutable directories, so refresh timestamps only after stable identity.
            item.info = info
            item.recheck()
        require(stat.S_IMODE(os.fstat(self.guest_fd).st_mode) == 0o711,
                'guest root must be root:root 0711')

    def _mount_context(self):
        rows = STORAGE.mount_table(STORAGE.read_bounded(
            '/proc/self/mountinfo', STORAGE.MOUNTINFO_LIMIT))
        require('/' in rows and STATE in rows, 'STATE/root mount missing')
        for fd, path in ((self.state_fd, STATE), (self.guest_fd, GUESTS)):
            require(path in rows, 'storage mount missing')
            row = rows[path]
            info = os.fstat(fd)
            require((os.major(info.st_dev), os.minor(info.st_dev)) == row['device'] and
                    STORAGE.fd_mount_id(STORAGE.read_bounded(
                        '/proc/self/fdinfo/' + str(fd), STORAGE.FDINFO_LIMIT)) == row['mount_id'],
                    'held root mount differs')
        return copy.deepcopy({path: rows[path] for path in ('/', STATE, GUESTS)})

    def _marker(self):
        fd = os.open('.a733-state', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK |
                     os.O_CLOEXEC, dir_fd=self.state_fd)
        try:
            before = os.fstat(fd)
            ENROLL.regular(before)
            require(before.st_dev == os.fstat(self.state_fd).st_dev and
                    0 < before.st_size <= 128, 'invalid state marker placement/size')
            data = os.read(fd, 129)
            require(ENROLL.identity(before) == ENROLL.identity(os.fstat(fd)) and
                    ENROLL.inode(os.stat('.a733-state', dir_fd=self.state_fd,
                                         follow_symlinks=False)) == ENROLL.inode(before),
                    'state marker changed')
            require(data == ('a733-state-v1\n' + self._storage['state_uuid'] + '\n').encode(),
                    'STATE UUID differs')
        finally:
            os.close(fd)

    def _serial(self):
        namespace = self._placement['namespace']
        serial = read_system('/sys/class/block/' + namespace + '/device/serial', 128)
        require(serial.decode('ascii').strip() == self._storage['nvme_serial'],
                'NVMe serial differs')

    def guard(self):
        """Recheck live placement before mutation; do not rescan guest contents."""
        require(not self._closed, 'host context closed')
        self._host()
        MANAGEMENT.runtime_guard()
        self._roots()
        require(self._mount_context() == self._mounts, 'storage mount context changed')
        require(POLICY.semantic(STORAGE.verify_guest_directory(self.guest_fd)) ==
                POLICY.semantic(self._placement), 'guest placement changed')
        self._marker()
        self._serial()

    def verify(self):
        """Also probe the actual p5 UUID read-only; no guest disk hashing."""
        self.guard()
        ENROLL.probe_uuid(self._placement, self._storage['guest_uuid'])

    def _release(self):
        self._closed = True
        failure = None
        while self._held:
            try:
                self._held.pop().close()
            except BaseException as error:
                failure = error
        if failure is not None:
            raise failure

    def close(self):
        if not self._closed:
            try:
                self.verify()
            finally:
                self._release()

    def __enter__(self):
        try:
            self.guard()
            return self
        except BaseException:
            self._release()
            raise

    def __exit__(self, *unused):
        self.close()
