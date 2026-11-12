"""Retained installed STATE storage-policy reader; no activation authority.

This authenticates placement and pins bytes for one creator and boot. It neither
initializes admission nor mounts, formats, publishes or launches anything. Native
storage observations are software guards, not hardware acceptance evidence.
"""
import copy
import fcntl
import os
from pathlib import Path
import stat
import threading

import importlib.util

BASE = Path(__file__).resolve().parents[1]


def module(name, relative):
    spec = importlib.util.spec_from_file_location(name, BASE / relative)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


CONTEXT = module('boot_policy_context', 'scripts/guest-lifecycle-context.py')
SCHEMA = module('boot_policy_schema', 'configs/guest_storage_policy.py')
ENROLL, MANAGEMENT, STORAGE = CONTEXT.ENROLL, CONTEXT.MANAGEMENT, CONTEXT.STORAGE
require = CONTEXT.require
STATE = '/run/a733-state'
NAME = 'guest-storage.json'


class CleanupError(RuntimeError):
    """Retain the primary failure and every uncertain descriptor cleanup."""
    def __init__(self, primary, errors):
        self.primary, self.errors = primary, tuple(errors)
        super().__init__('policy descriptor cleanup uncertain')


class Policy:
    """Pin root-owned 0400 canonical policy on the authenticated selected STATE.

    value returns a detached data copy. check reauthenticates native host/storage
    and every retained name, descriptor, byte and mount pin. close only needs the
    creating process/thread, allowing cleanup after a privilege drop.
    """
    def __init__(self, boot_id):
        self._pid, self._thread = os.getpid(), threading.get_ident()
        self._closed, self._fds = False, []
        missing_leaf = False
        try:
            SCHEMA._uuid(boot_id)
            self.boot_id = boot_id
            self._native()
            try:
                parts = Path(STATE).parts
                require(parts and parts[0] == '/' and str(Path(STATE)) == STATE and
                        all(part not in ('.', '..') for part in parts),
                        'canonical absolute STATE required')
                self._chain = [self._open('/', None, directory=True)]
                self._names = []
                for name in parts[1:]:
                    self._chain.append(self._open(name, self._chain[-1], directory=True))
                    self._names.append(name)
                self._state_fd = self._chain[-1]
                config = self._open('config', self._chain[-1], directory=True)
            except FileNotFoundError as error:
                raise ValueError('selected STATE/config unavailable') from error
            self._chain.append(config)
            self._names.append('config')
            require(stat.S_IMODE(os.fstat(config).st_mode) == 0o700,
                    'policy config must be root:root 0700')
            self._dirs = [CONTEXT.physical_identity(os.fstat(fd)) for fd in self._chain]
            self._directories()
            # Recheck all parents even on absence: only an exact leaf miss is optional.
            try:
                self._policy_fd = self._open(NAME, config)
            except FileNotFoundError:
                self._directories()
                missing_leaf = True
                raise
            self._chain.append(self._policy_fd)
            self._names.append(NAME)
            self._info = ENROLL.identity(os.fstat(self._policy_fd))
            self._roots()
            self._bytes = self._read()
            self._value = SCHEMA.decode(self._bytes)
            self._mounts = self._mount_context()
            self.check()
        except BaseException as error:
            if isinstance(error, FileNotFoundError) and not missing_leaf:
                primary = ValueError('required native host/storage input unavailable')
                self._release(primary)
                raise primary from error
            self._release(error)
            raise

    def _creator(self):
        require(os.getpid() == self._pid and threading.get_ident() == self._thread,
                'policy creator process/thread differs')

    def _native(self):
        CONTEXT.host_identity(self.boot_id)
        MANAGEMENT.runtime_guard()

    def _open(self, name, parent, *, directory=False):
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
        if directory:
            flags |= os.O_DIRECTORY
        fd = os.open(name, flags, dir_fd=parent)
        self._fds.append(fd)
        info = os.fstat(fd)
        if directory:
            ENROLL.trusted_directory(info)
        return fd

    def _directories(self):
        for index, expected in enumerate(self._dirs):
            fd = self._chain[index]
            info = os.fstat(fd)
            ENROLL.trusted_directory(info)
            require(CONTEXT.physical_identity(info) == expected,
                    'policy directory identity changed')
            if index:
                require(ENROLL.inode(os.stat(self._names[index - 1],
                        dir_fd=self._chain[index - 1], follow_symlinks=False)) ==
                        ENROLL.inode(info), 'policy named parent changed')

    def _roots(self):
        for index, fd in enumerate(self._chain):
            info = os.fstat(fd)
            flags = fcntl.fcntl(fd, fcntl.F_GETFL)
            require(flags & os.O_ACCMODE == os.O_RDONLY and not flags & os.O_PATH,
                    'read-only policy descriptor required')
            if index < len(self._chain) - 1:
                ENROLL.trusted_directory(info)
                require(CONTEXT.physical_identity(info) == self._dirs[index],
                        'policy directory identity changed')
            else:
                ENROLL.regular(info, 0o400)
                require(0 < info.st_size <= SCHEMA.LIMIT and
                        ENROLL.identity(info) == self._info, 'policy metadata changed')
            if index:
                named = os.stat(self._names[index - 1], dir_fd=self._chain[index - 1],
                                follow_symlinks=False)
                require(ENROLL.inode(named) == ENROLL.inode(info), 'policy named path changed')
        state = os.fstat(self._state_fd)
        require(all(os.fstat(fd).st_dev == state.st_dev for fd in self._chain[-3:]),
                'policy/config must reside on selected STATE')
        require(stat.S_IMODE(os.fstat(self._chain[-2]).st_mode) == 0o700,
                'policy config must be root:root 0700')

    def _read(self):
        data = os.pread(self._policy_fd, SCHEMA.LIMIT + 1, 0)
        self._roots()
        require(len(data) == self._info['size'], 'policy size changed')
        return data

    def _mount_context(self):
        data = STORAGE.read_bounded('/proc/self/mountinfo', STORAGE.MOUNTINFO_LIMIT)
        rows = STORAGE.mount_table(data)
        require('/' in rows and STATE in rows, 'selected root/STATE mount missing')
        state = rows[STATE]
        info = os.fstat(self._state_fd)
        require((os.major(info.st_dev), os.minor(info.st_dev)) == state['device'] and
                STORAGE.fd_mount_id(STORAGE.read_bounded(
                    '/proc/self/fdinfo/' + str(self._state_fd), STORAGE.FDINFO_LIMIT)) ==
                state['mount_id'], 'held STATE mount differs')
        return copy.deepcopy({path: rows[path] for path in ('/', STATE)})

    def _storage(self):
        _, state = MANAGEMENT.validate_mounts(MANAGEMENT.mount_table(
            STORAGE.read_bounded('/proc/self/mountinfo', STORAGE.MOUNTINFO_LIMIT)))
        require(state['source'] == '/dev/nvme0n1p4', 'selected STATE must be nvme0n1p4')
        identity = MANAGEMENT.block_identity(state)
        node = (Path('/sys/dev/block') / identity).resolve(strict=True)
        require(node.name == 'nvme0n1p4' and node.parent.name == 'nvme0n1',
                'selected STATE namespace differs')
        namespace = node.parent
        require(not (namespace / 'partition').exists(), 'namespace is a partition')
        require((Path('/sys/class/block/nvme0n1').resolve(strict=True)) == namespace,
                'selected namespace link differs')
        read = CONTEXT.read_system
        for field, expected in (('model', self._value['expected_model']),
                                ('serial', self._value['storage']['nvme_serial'])):
            require(read(namespace / 'device' / field, 128).decode('ascii').strip() == expected,
                    'selected NVMe ' + field + ' differs')
        require(read(namespace / 'queue/logical_block_size', 32) == b'512\n',
                'NVMe logical sector must be 512 bytes')
        sectors = STORAGE.number(read(namespace / 'size', 32).decode('ascii').removesuffix('\n'),
                                 positive=True)
        require(sectors * 512 == self._value['expected_bytes'], 'NVMe size differs')
        # Read through the retained STATE directory, never a fresh absolute path.
        fd = os.open('.a733-state', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK |
                     os.O_CLOEXEC, dir_fd=self._state_fd)
        primary = None
        try:
            before = os.fstat(fd)
            ENROLL.regular(before)
            require(before.st_dev == os.fstat(self._state_fd).st_dev and
                    0 < before.st_size <= 128, 'unsafe STATE marker placement/size')
            marker = os.read(fd, 129)
            require(ENROLL.identity(before) == ENROLL.identity(os.fstat(fd)) and
                    ENROLL.inode(os.stat('.a733-state', dir_fd=self._state_fd,
                                         follow_symlinks=False)) == ENROLL.inode(before),
                    'STATE marker changed')
            require(marker == ('a733-state-v1\n' + self._value['storage']['state_uuid'] +
                               '\n').encode('ascii'), 'selected STATE UUID differs')
        except BaseException as error:
            primary = error
            raise
        finally:
            try:
                os.close(fd)
            except BaseException as error:
                raise CleanupError(primary, [error]) from primary

    @property
    def value(self):
        self._creator()
        require(not self._closed, 'policy closed')
        return copy.deepcopy(self._value)

    def check(self):
        self._creator()
        require(not self._closed, 'policy closed')
        self._native()
        self._roots()
        require(self._read() == self._bytes, 'policy bytes changed')
        require(self._mount_context() == self._mounts, 'selected root/STATE mount changed')
        self._storage()
        self._roots()
        require(self._read() == self._bytes, 'policy bytes changed')

    def _release(self, primary=None):
        self._closed = True
        errors = []
        while self._fds:
            try:
                os.close(self._fds.pop())
            except BaseException as error:
                errors.append(error)
        if errors:
            raise CleanupError(primary, errors) from primary

    def close(self):
        self._creator()
        if not self._closed:
            self._release()

    def __enter__(self):
        self._creator()
        try:
            self.check()
            return self
        except BaseException as error:
            self._release(error)
            raise

    def __exit__(self, *unused):
        self.close()
