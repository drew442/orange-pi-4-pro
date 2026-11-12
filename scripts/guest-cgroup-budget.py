"""Read-only retained managed cgroup-v2 budget guard; no deployment authority.

ABI: Linux v6.6 Documentation/admin-guide/cgroup-v2.rst (unified 0::$PATH,
CGROUP2_SUPER_MAGIC, domain type, memory.max, memory.swap.max and pids.max).
No controller writes, process migration, unit creation or PID1 queries occur.
The caller authenticates its native namespaces through the protected run permit.
"""
import ctypes
import fcntl
import importlib.util
import os
from pathlib import Path
import re
import stat
import threading

BASE = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location('cgroup_budget_units', BASE / 'configs/guest_boot_units.py')
UNITS = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(UNITS)
require = UNITS.require
ROOT = '/'
MOUNT = '/sys/fs/cgroup'
MAGIC = 0x63677270


def read_native(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        return read_fd(fd, limit)
    finally:
        os.close(fd)


def read_fd(fd, limit):
    os.lseek(fd, 0, os.SEEK_SET)
    chunks, total = [], 0
    while total <= limit:
        data = os.read(fd, min(4096, limit + 1 - total))
        if not data:
            break
        chunks.append(data)
        total += len(data)
    require(total <= limit, 'cgroup native read exceeds bound')
    return b''.join(chunks)


def filesystem(fd):
    # Oversized aligned storage avoids depending on the architecture's statfs tail.
    buffer = (ctypes.c_long * 64)()
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.fstatfs(fd, ctypes.byref(buffer)) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))
    return buffer[0]


def mount_identity(data):
    records = []
    for line in data.decode('ascii').splitlines():
        halves = line.split(' - ')
        require(len(halves) == 2, 'invalid mountinfo record')
        left, right = halves[0].split(), halves[1].split()
        require(len(left) >= 6 and len(right) == 3, 'invalid mountinfo fields')
        require(right[0] != 'cgroup', 'legacy cgroup mount refused')
        point = re.sub(r'\\([0-7]{3})', lambda match: chr(int(match[1], 8)), left[4])
        require(not point.startswith(MOUNT + '/'),
                'nested cgroup mount refused')
        if right[0] == 'cgroup2' or point == MOUNT:
            require(right[0] == 'cgroup2' and left[3] == '/' and point == MOUNT and
                    '\\' not in line and re.fullmatch(r'[0-9]+:[0-9]+', left[2]),
                    'noncanonical cgroup2 mount refused')
            records.append(tuple(left + ['-'] + right))
    require(len(records) == 1, 'unique native cgroup2 mount required')
    return records[0]


class CleanupError(RuntimeError):
    def __init__(self, primary, errors):
        self.primary, self.errors = primary, tuple(errors)
        super().__init__('cgroup descriptor cleanup uncertain')


class CgroupBudget:
    """Retain protected directory and controller FDs for the creating PID/thread."""
    def __init__(self, config):
        self._pid, self._thread = os.getpid(), threading.get_ident()
        self._closed, self._fds, self._nodes = False, [], []
        try:
            config = UNITS._config(config)
            self._path = '/a733.slice/a733-guests.slice/a733-managed-' + config['name'] + '.service'
            self._membership()
            self._mount = mount_identity(read_native('/proc/self/mountinfo', 1024 * 1024))
            data = read_native('/proc/meminfo', 65536).decode('ascii')
            lines = [line for line in data.splitlines() if line.startswith('MemTotal:')]
            require(len(lines) == 1 and re.fullmatch(r'MemTotal:\s+[0-9]{1,20} kB', lines[0]),
                    'invalid MemTotal')
            total = int(lines[0].split()[1]) * 1024
            budget = UNITS.check_budget([config], total)['budget_bytes']
            parent = self._open(ROOT, None, True, False)
            for name in ('sys', 'fs', 'cgroup', 'a733.slice', 'a733-guests.slice',
                         'a733-managed-' + config['name'] + '.service'):
                parent = self._open(name, parent, True, name not in ('sys', 'fs'))
                if name == 'a733-guests.slice':
                    self._slice = parent
            self._unit = parent
            self._limits = []
            for directory, memory, tasks in ((self._slice, budget, 4096),
                                            (self._unit, config['memory_max_mib'] * UNITS.MIB, 256)):
                for name, expected in (('memory.max', str(memory).encode() + b'\n'),
                                       ('memory.swap.max', b'0\n'),
                                       ('pids.max', str(tasks).encode() + b'\n'),
                                       ('cgroup.type', b'domain\n')):
                    self._limits.append((self._open(name, directory, False, True), expected))
            self.check()
        except BaseException as error:
            self._release(error)
            raise

    def _creator(self):
        require((os.getpid(), threading.get_ident()) == (self._pid, self._thread),
                'cgroup creator process/thread differs')

    def _membership(self):
        require(read_native('/proc/self/cgroup', 4096) == b'0::' + self._path.encode() + b'\n',
                'managed unified cgroup membership differs')

    def _open(self, name, parent, directory, cgroup):
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
        if directory:
            flags |= os.O_DIRECTORY
        fd = os.open(name, flags, dir_fd=parent)
        self._fds.append(fd)
        info = os.fstat(fd)
        self._nodes.append((parent, name, fd, self._identity(info), directory, cgroup))
        self._node(self._nodes[-1])
        return fd

    @staticmethod
    def _identity(info):
        return info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode

    def _node(self, node):
        parent, name, fd, identity, directory, cgroup = node
        info = os.fstat(fd)
        named = os.stat(name, dir_fd=parent, follow_symlinks=False)
        require(self._identity(info) == identity == self._identity(named) and
                info.st_uid == info.st_gid == 0 and not info.st_mode & 0o022 and
                (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)),
                'cgroup node metadata/name differs')
        access = fcntl.fcntl(fd, fcntl.F_GETFL)
        require(not os.get_inheritable(fd) and not access & os.O_PATH and
                access & os.O_ACCMODE == os.O_RDONLY,
                'cgroup descriptor access differs')
        if cgroup:
            require(filesystem(fd) == MAGIC and
                    info.st_dev == os.fstat(self._fds[3]).st_dev and
                    self._mount[2] == str(os.major(info.st_dev)) + ':' + str(os.minor(info.st_dev)),
                    'native cgroup2 filesystem differs')

    def check(self):
        self._creator()
        require(not self._closed, 'cgroup budget closed')
        self._membership()
        require(mount_identity(read_native('/proc/self/mountinfo', 1024 * 1024)) == self._mount,
                'cgroup mount changed')
        for node in self._nodes:
            self._node(node)
        for fd, expected in self._limits:
            require(read_fd(fd, 128) == expected, 'managed cgroup limit/type differs')
        self._membership()

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
            if not self._closed:
                self._release(error)
            raise

    def __exit__(self, *unused):
        self.close()
