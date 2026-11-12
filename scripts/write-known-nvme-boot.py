#!/usr/bin/env python3
"""Guarded offline BOOT-only updater for one preauthorized A733 NVMe."""
import argparse
import ctypes
import errno
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import select
import signal
import stat
import struct
import sys
import time
import threading
import tempfile
import uuid

DISK = '/dev/nvme0n1'
TARGET = '/dev/nvme0n1p1'
TARGET_BYTES = 256060514304
SOURCE_BYTES = 536870912
OLD_SHA256 = '5860889755d7479e619e86b83bc32237e51bacd3a3fd2e2d2ebe976898f44899'
NEW_SHA256 = 'cc2ad988fdc57579178766acaf09c71d688aaadec94b722593192cb24426c313'
SERIAL = '134807190FC700149277'
MODEL = 'SPCC M.2 PCIe SSD'
KERNEL_RELEASE = '6.6.98-a733-diag'
NS_GET_NSTYPE = 0xb703
# Pinned vendor include/linux/proc_ns.h, revision 2ac08e8c7cdc28abbdc5c9a9dd812f887ae9c79f:
# https://raw.githubusercontent.com/orangepi-xunlong/linux-orangepi/2ac08e8c7cdc28abbdc5c9a9dd812f887ae9c79f/include/linux/proc_ns.h
INITIAL_NAMESPACE_INODES = {'pid': 0xEFFFFFFC, 'user': 0xEFFFFFFD}
NAMESPACE_TYPES = {'pid': 0x20000000, 'user': 0x10000000, 'mnt': 0x00020000}
AUTHORIZATION = 'docs/evidence/storage-write-authorization-20261108.json'
BLKGETSIZE64, BLKSSZGET, BLKPBSZGET, BLKROGET, BLKFLSBUF = 0x80081272, 0x1268, 0x127b, 0x125e, 0x1261
CHUNK = 1024 * 1024
# Sectors from the installed corrected VGIC manifest, including both GPT copies.
GEOMETRY = ((2048, 1048576), (1050624, 8388608), (9439232, 8388608),
            (17827840, 16777216), (34605056, 465512448))
GPT_EXTENTS = (
    (0, 512, 'b6d0b9179af5316d7a9c9db924c0cd01a4976c94b896415ff2bb6f4ff4922c50'),
    (512, 512, 'f6e60c22527739a241b05ea22762c001432b9470383e641b9a83f0988dabf52c'),
    (1024, 16384, '13a4b8d58e00ffa6a3c52f377d0dcd4454df904f34f754092b988413b1c673be'),
    (256060497408, 16384, '13a4b8d58e00ffa6a3c52f377d0dcd4454df904f34f754092b988413b1c673be'),
    (256060513792, 512, 'c796f02d3a1bf7ad2d6e1dcbba1c70e05f67bb322a59c42c30bf72c795c32708'),
)


class Refusal(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise Refusal(message)


def read_text(path):
    # Failed reads are fatal, never converted into an empty successful probe.
    return Path(path).read_text(encoding='ascii').strip()


def device_number(value):
    require(re.fullmatch(r'[0-9]+:[0-9]+', value) is not None,
            'malformed device number')
    return tuple(map(int, value.split(':')))


def unescape_mount(value):
    # mountinfo uses octal escapes for whitespace and backslash.
    require(re.search(r'\\(?![0-7]{3})', value) is None, 'malformed mount escape')
    return re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), value)


def mountinfo(raw=None):
    result = []
    raw = read_text('/proc/self/mountinfo') if raw is None else raw
    require(bool(raw), 'empty mountinfo')
    for line in raw.splitlines():
        fields = line.split()
        require(fields.count('-') == 1, 'malformed mountinfo separator')
        separator = fields.index('-')
        require(separator >= 6 and len(fields) == separator + 4,
                'malformed mountinfo fields')
        require(fields[0].isdigit() and fields[1].isdigit(), 'malformed mount IDs')
        result.append({'dev': device_number(fields[2]),
                       'path': unescape_mount(fields[4]),
                       'options': fields[5].split(',') + fields[-1].split(','),
                       'fstype': fields[separator + 1]})
    return result


def containing_mount(path, mounts):
    matches = [m for m in mounts if path == m['path'] or
               path.startswith(m['path'].rstrip('/') + '/')]
    require(bool(matches), 'path has no visible mount')
    # Equal-length stacked mountpoints are ambiguous; refuse rather than guess.
    longest = max(len(m['path']) for m in matches)
    matches = [m for m in matches if len(m['path']) == longest]
    require(len(matches) == 1, 'ambiguous stacked mountpoint')
    return matches[0]


def namespace_guard(namespace):
    path = '/proc/self/ns/' + namespace
    # proc namespace entries intentionally are kernel symlinks to nsfs objects.
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
    try:
        opened = os.fstat(fd)
        mine = os.stat(path)
        init = os.stat('/proc/1/ns/' + namespace)
        require((opened.st_dev, opened.st_ino) == (mine.st_dev, mine.st_ino) ==
                (init.st_dev, init.st_ino), 'namespace descriptor/path/PID 1 identity differs')
        require(fcntl.ioctl(fd, NS_GET_NSTYPE) == NAMESPACE_TYPES[namespace],
                'namespace type differs')
        if namespace in INITIAL_NAMESPACE_INODES:
            require(opened.st_ino == INITIAL_NAMESPACE_INODES[namespace],
                    'must use pinned kernel initial PID and user namespaces')
    finally:
        os.close(fd)


def host_context(expected_boot_id):
    require(os.geteuid() == 0, 'must run as root')
    require(str(uuid.UUID(expected_boot_id)) == expected_boot_id, 'invalid expected boot ID')
    require(read_text('/proc/sys/kernel/random/boot_id') == expected_boot_id,
            'boot ID differs from caller pin')
    require(os.uname().release == KERNEL_RELEASE, 'only pinned A733 maintenance kernel supported')
    for namespace in ('pid', 'mnt', 'user'):
        namespace_guard(namespace)
    # Secondary consistency check: a private proc mount can hide ancestor NSpid
    # fields, so the pinned initial namespace inode check above is essential.
    status = read_text('/proc/self/status')
    nspid = [line.split()[1:] for line in status.splitlines() if line.startswith('NSpid:')]
    require(len(nspid) == 1 and len(nspid[0]) == 1 and nspid[0][0].isdigit(),
            'nested or unavailable host PID namespace')
    require(read_text('/proc/self/uid_map').split() == ['0', '0', '4294967295'],
            'initial user namespace required')
    mounts = mountinfo()
    require(containing_mount('/', mounts)['fstype'] in ('rootfs', 'ramfs', 'tmpfs'),
            'offline RAM root required')
    run = containing_mount('/run', mounts)
    require(run['path'] == '/run' and run['fstype'] == 'tmpfs', 'dedicated /run tmpfs required')
    proc = containing_mount('/proc', mounts)
    require(proc['path'] == '/proc' and proc['fstype'] == 'proc', 'invalid proc mount')
    require(not any(x.startswith('hidepid=') and x != 'hidepid=0' for x in proc['options']),
            'proc hides processes')
    return mounts


class OfflineAdmission:
    """Cooperative RAM recovery exclusion, never closure or storage authority.

    Entry proves the native initial mount namespace. Later check() permits the
    caller's intentional mount unshare, retaining root/run/proc and PID/user
    identity instead. The bounded KVM census runs on entry; callers must invoke
    check_consumers() again at their final quiescence boundary. No gate is opened,
    record adopted, or installed project module imported.
    """
    NAME = 'a733-admission'
    INIT = 'a733-admission-init.lock'
    PID_LIMIT = 65536
    FD_LIMIT = 262144

    def __init__(self, boot_id):
        self.boot_id = boot_id
        self.pid = os.getpid()
        self.thread = threading.current_thread()
        self.fds = []
        self.paths = {}
        self.directory = self.init_fd = self.gate = self.leases = self.record = None
        self.data = None

    def _creator(self):
        require(os.getpid() == self.pid and threading.current_thread() is self.thread,
                'offline admission belongs to another process or thread')

    def _open(self, name, parent=None, directory=False, flags=0):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC |
                     os.O_NONBLOCK | (os.O_DIRECTORY if directory else 0) | flags,
                     dir_fd=parent)
        self.fds.append(fd)
        return fd

    @staticmethod
    def _same(a, b):
        return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)

    @staticmethod
    def _tmpfs(fd):
        buffer = ctypes.create_string_buffer(256)
        require(ctypes.CDLL(None, use_errno=True).fstatfs(fd, buffer) == 0 and
                ctypes.c_long.from_buffer(buffer).value == 0x01021994,
                'offline admission requires tmpfs')

    def _named(self, fd, name, parent, mode, gid, directory=False, empty=False):
        info = os.fstat(fd)
        require((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
                and info.st_uid == 0 and info.st_gid == gid and
                stat.S_IMODE(info.st_mode) == mode and
                (directory or info.st_nlink == 1) and (not empty or info.st_size == 0),
                'unsafe offline admission metadata')
        require(self._same(info, os.stat(name, dir_fd=parent, follow_symlinks=False)),
                'offline admission named inode changed')
        require(info.st_dev == os.fstat(self.run).st_dev,
                'offline admission filesystem differs')
        return info

    def _held(self, fd):
        require(fcntl.fcntl(fd, fcntl.F_GETFL) & (os.O_ACCMODE | os.O_PATH) == os.O_RDONLY,
                'offline lock must be readonly')
        require(fcntl.fcntl(fd, fcntl.F_GETFD) & fcntl.FD_CLOEXEC,
                'offline lock must be close-on-exec')
        raw = read_text('/proc/self/fdinfo/' + str(fd))
        fields = {}
        for line in raw.splitlines():
            key, sep, value = line.partition(':')
            require(sep and key not in fields, 'invalid offline lock fdinfo')
            fields[key] = value.strip()
        match = re.fullmatch(r'[0-9]+: FLOCK\s+ADVISORY\s+WRITE\s+'
                             r'([0-9]+) ([0-9a-fA-F]+):([0-9a-fA-F]+):([0-9]+) 0 EOF',
                             fields.get('lock', ''))
        info = os.fstat(fd)
        require(match is not None and tuple(int(x, base) for x, base in
                zip(match.groups(), (10, 16, 16, 10))) ==
                (self.pid, os.major(info.st_dev), os.minor(info.st_dev), info.st_ino),
                'offline exclusion flock differs')
        require(re.fullmatch('[0-7]+', fields.get('flags', '')) is not None and
                int(fields['flags'], 8) & (os.O_ACCMODE | os.O_PATH) == os.O_RDONLY and
                fields.get('ino') == str(info.st_ino), 'offline lock descriptor differs')

    def _environment(self):
        self._creator()
        require(os.getresuid() == (0, 0, 0), 'actual root required for offline admission')
        require(bool(self.fds), 'offline admission closed')
        require(os.uname().machine == 'aarch64' and os.uname().release == KERNEL_RELEASE,
                'only native pinned A733 kernel supported')
        require(read_text('/proc/sys/kernel/random/boot_id') == self.boot_id,
                'offline boot ID changed')
        for namespace in ('pid', 'user'):
            namespace_guard(namespace)
        require(read_text('/proc/self/uid_map').split() == ['0', '0', '4294967295'],
                'initial user namespace required')
        nspid = [line.split()[1:] for line in read_text('/proc/self/status').splitlines()
                 if line.startswith('NSpid:')]
        require(nspid == [[str(self.pid)]], 'native process visibility differs')
        mounts = mountinfo()
        for path, (fd, saved_mount) in self.paths.items():
            info = os.fstat(fd)
            require(self._same(info, os.stat(path, follow_symlinks=False)) and
                    stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and
                    not info.st_mode & 0o022, 'offline root/run/proc identity changed')
            require(containing_mount(path, mounts) == saved_mount,
                    'offline root/run/proc mount changed')
        self._tmpfs(self.run)

    @staticmethod
    def _decode(data):
        def pairs(items):
            value = {}
            for key, item in items:
                require(key not in value, 'duplicate offline gate field')
                value[key] = item
            return value
        try:
            record = json.loads(data.decode('ascii'), object_pairs_hook=pairs)
            require(type(record) is dict and set(record) == {
                'schema', 'profile', 'state', 'generation', 'boot_id', 'owner_nonce', 'reason'},
                'invalid offline gate fields')
            require(type(record['schema']) is int and record['schema'] == 1 and
                    record['profile'] == 'a733-admission-v1' and record['state'] == 'closed' and
                    type(record['generation']) is int and 0 <= record['generation'] < 2**63 and
                    record['reason'] in ('maintenance', 'bootstrap', 'shutdown'),
                    'offline gate must be valid and closed')
            for name in ('boot_id', 'owner_nonce'):
                require(type(record[name]) is str and str(uuid.UUID(record[name])) == record[name],
                        'offline gate UUID must be canonical')
            require((json.dumps(record, sort_keys=True, separators=(',', ':'),
                                ensure_ascii=True) + '\n').encode('ascii') == data,
                    'offline gate must be canonical')
        except (ValueError, UnicodeError, TypeError) as error:
            raise Refusal('invalid offline gate record') from error
        return record

    def _observe(self):
        """Never wait holding gate; retain exact original bytes and named inode."""
        self._named(self.directory, self.NAME, self.run, 0o710, self.gid, True)
        for fd, name in ((self.gate, 'gate.lock'), (self.leases, 'leases.lock')):
            self._named(fd, name, self.directory, 0o640, self.gid, empty=True)
        fcntl.flock(self.gate, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            info = self._named(self.record, 'gate.json', self.directory, 0o440, self.gid)
            require(info.st_size <= 4096, 'offline gate too large')
            data = os.pread(self.record, 4097, 0)
            record = self._decode(data)
            require(record['boot_id'] == self.boot_id, 'offline gate boot differs')
            require(identity(info) == identity(os.fstat(self.record)), 'offline record changed')
            if self.data is None:
                self.data, self.record_identity = data, identity(info)
            require(data == self.data and identity(info) == self.record_identity,
                    'offline closure changed')
            self._named(self.record, 'gate.json', self.directory, 0o440, self.gid)
        finally:
            fcntl.flock(self.gate, fcntl.LOCK_UN)

    @classmethod
    def acquire(cls, expected_boot_id):
        mounts = host_context(expected_boot_id)  # Strict initial MNT check, once.
        result = cls(expected_boot_id)
        creation = None
        try:
            result._creator()
            require(os.getresuid() == (0, 0, 0), 'actual root required for offline admission')
            require(os.uname().machine == 'aarch64', 'native A733 machine required')
            result.root = result._open('/', directory=True)
            result.run = result._open('run', result.root, True)
            result.proc = result._open('proc', result.root, True)
            for path, fd in (('/', result.root), ('/run', result.run), ('/proc', result.proc)):
                result.paths[path] = (fd, containing_mount(path, mounts))
            result._environment()
            try:
                creation = os.open(cls.INIT, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                                   os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=result.run)
            except FileExistsError:
                pass
            else:
                os.fchown(creation, 0, 0)
                os.fchmod(creation, 0o600)
                result._named(creation, cls.INIT, result.run, 0o600, 0, empty=True)
                os.fsync(creation)
                fd, creation = creation, None
                os.close(fd)
                os.fsync(result.run)
            result.init_fd = result._open(cls.INIT, result.run)
            result._named(result.init_fd, cls.INIT, result.run, 0o600, 0, empty=True)
            fcntl.flock(result.init_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                result.directory = result._open(cls.NAME, result.run, True)
            except FileNotFoundError:
                pass
            if result.directory is not None:
                result.gid = os.fstat(result.directory).st_gid
                result._named(result.directory, cls.NAME, result.run, 0o710, result.gid, True)
                result.gate = result._open('gate.lock', result.directory)
                result.leases = result._open('leases.lock', result.directory)
                result.record = result._open('gate.json', result.directory)
                result._observe()
                fcntl.flock(result.leases, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result.check_consumers()
            return result
        except BaseException:
            try:
                if creation is not None:
                    fd, creation = creation, None
                    os.close(fd)
            finally:
                result.close()
            raise

    def check(self):
        self._environment()
        self._named(self.init_fd, self.INIT, self.run, 0o600, 0, empty=True)
        self._held(self.init_fd)
        if self.directory is None:
            try:
                os.stat(self.NAME, dir_fd=self.run, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise Refusal('offline absent admission runtime appeared')
        else:
            self._held(self.leases)
            self._observe()
        return self

    def check_consumers(self):
        self.check()
        offline_consumer_check()
        self.check()
        return self

    def close(self):
        self._creator()  # Also rejects foreign use after an earlier close.
        fds, self.fds = self.fds, []
        errors = []
        for fd in reversed(fds):
            try:
                os.close(fd)
            except BaseException as error:
                errors.append(error)
        if errors:
            raise errors[0]

    def __enter__(self):
        return self.check()

    def __exit__(self, *unused):
        self.close()


def offline_consumer_check():
    """Bounded initial-namespace task/FD census; permission errors refuse.

    Task tables include threads with distinct FD tables. KVM names follow the
    pinned managed fixture census; character-device aliases also refuse. Only a
    disappearing process/descriptor (ENOENT/ESRCH) is a permissible scan race.
    """
    tasks = descriptors = 0
    deadline = time.monotonic() + 10
    def bounded():
        require(time.monotonic() <= deadline, "KVM consumer census timeout")
    def gone(error):
        if error.errno not in (errno.ENOENT, errno.ESRCH):
            raise error
    with os.scandir('/proc') as processes:
        count = 0
        for process in processes:
            bounded()
            if not process.name.isdecimal():
                continue
            count += 1
            require(count <= OfflineAdmission.PID_LIMIT, 'process census limit exceeded')
            try:
                with os.scandir(process.path + '/task') as threads:
                    for thread in threads:
                        bounded()
                        require(thread.name.isdecimal(), 'invalid task census entry')
                        tasks += 1
                        require(tasks <= OfflineAdmission.PID_LIMIT, 'task census limit exceeded')
                        try:
                            with os.scandir(thread.path + '/fd') as files:
                                for entry in files:
                                    bounded()
                                    descriptors += 1
                                    require(descriptors <= OfflineAdmission.FD_LIMIT, 'descriptor census limit exceeded')
                                    require(entry.name.isdecimal(), 'invalid descriptor census entry')
                                    try:
                                        target = os.readlink(entry.path)
                                        require(not (target.startswith('/dev/kvm') or
                                                (target.startswith('anon_inode:') and 'kvm' in target)),
                                                'actual KVM consumer remains')
                                        info = os.stat(entry.path)
                                        require(not (stat.S_ISCHR(info.st_mode) and
                                                info.st_rdev == os.makedev(10, 232)),
                                                'actual KVM device consumer remains')
                                    except OSError as error:
                                        gone(error)
                        except OSError as error:
                            gone(error)
            except OSError as error:
                gone(error)



def protected_ram_path(path, mounts):
    require(os.path.isabs(path) and os.path.normpath(path) == path,
            'payload/report path must be canonical absolute')
    require(path.startswith('/run/'), 'payload/report must be under /run')
    components = Path(path).parts
    current = Path('/')
    root = os.lstat(current)
    require(stat.S_ISDIR(root.st_mode) and root.st_uid == 0 and
            not root.st_mode & 0o022, 'unsafe root directory')
    for part in components[1:-1]:
        current /= part
        info = os.lstat(current)
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and
                not info.st_mode & 0o022, 'unsafe or symlinked path ancestor')
    parent = os.lstat(os.path.dirname(path))
    require(stat.S_IMODE(parent.st_mode) == 0o700, 'immediate parent must be root mode 0700')
    mount = containing_mount(path, mounts)
    require(mount['fstype'] == 'tmpfs', 'payload/report must be on proven tmpfs')
    require((os.major(parent.st_dev), os.minor(parent.st_dev)) == mount['dev'],
            'parent mount identity mismatch')


def identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def validate_source(fd, path, initial=None):
    info = os.fstat(fd)
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and
            stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1 and
            info.st_size == SOURCE_BYTES, 'source must be root 0600, single-link, exact-size regular file')
    require(identity(os.lstat(path)) == identity(info), 'source path/held descriptor changed')
    if initial is not None:
        require(identity(info) == initial, 'source identity changed')
    return identity(info)


def open_source(path, mounts, expected_sha):
    protected_ram_path(path, mounts)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        initial = validate_source(fd, path)
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        print(f'Authenticating BOOT source: {path}', file=sys.stderr, flush=True)
        checked = 0
        next_progress = time.monotonic() + 1
        digest = hashlib.sha256()
        while True:
            chunk = os.read(fd, CHUNK)
            if not chunk:
                break
            digest.update(chunk)
            checked += len(chunk)
            if time.monotonic() >= next_progress:
                print(f'Authenticated {checked}/{SOURCE_BYTES} source bytes', file=sys.stderr, flush=True)
                next_progress = time.monotonic() + 1
        require(digest.hexdigest() == expected_sha, 'source SHA-256 mismatch')
        validate_source(fd, path, initial)
        print(f'Authenticated {checked}/{SOURCE_BYTES} source bytes; SHA-256 matches.', file=sys.stderr, flush=True)
        os.lseek(fd, 0, os.SEEK_SET)
        return fd, initial
    except BaseException:
        os.close(fd)
        raise


def target_identity():
    return _offline_inventory(0)


def _offline_inventory(p5_ro):
    base = Path('/sys/class/block/nvme0n1')
    require(read_text(base / 'device/serial') == SERIAL and
            read_text(base / 'device/model') == MODEL, 'NVMe identity differs')
    require(read_text(base / 'size') == str(TARGET_BYTES // 512), 'NVMe size differs')
    for field in ('logical_block_size', 'physical_block_size'):
        require(read_text(base / 'queue' / field) == '512', 'NVMe sector size differs')
    expected = {'nvme0n1', *('nvme0n1p' + str(i) for i in range(1, 6))}
    require({p.name for p in Path('/dev').glob('nvme0n1p*')} == expected - {'nvme0n1'},
            'unexpected device partition set')
    require({p.name for p in base.iterdir() if (p / 'partition').exists()} ==
            expected - {'nvme0n1'}, 'unexpected sysfs partition set')
    result = {}
    devices = set()
    for name in sorted(expected):
        path = Path('/dev') / name
        info = os.lstat(path)
        require(stat.S_ISBLK(info.st_mode), 'NVMe node must be a non-symlink block device')
        dev = (os.major(info.st_rdev), os.minor(info.st_rdev))
        node = Path('/sys/class/block') / name
        require(device_number(read_text(node / 'dev')) == dev and dev not in devices and
                Path(f'/sys/dev/block/{dev[0]}:{dev[1]}').resolve(strict=True) == node.resolve(strict=True),
                'NVMe sysfs device differs')
        require(read_text(node / 'ro') == str(p5_ro if name == 'nvme0n1p5' else 0),
                'NVMe node read-only flag differs')
        if name != 'nvme0n1':
            number = int(name.removeprefix('nvme0n1p'))
            start, size = GEOMETRY[number - 1]
            require(node.resolve().parent == base.resolve() and
                    read_text(node / 'partition') == str(number) and
                    read_text(node / 'start') == str(start) and
                    read_text(node / 'size') == str(size), 'NVMe partition geometry differs')
        result[name] = (info.st_dev, info.st_ino, info.st_rdev)
        devices.add(dev)
    return result, devices


def process_consumers(devices, own_fds=()):
    processes = [p for p in Path('/proc').iterdir() if p.name.isdigit()]
    require(bool(processes) and any(p.name == '1' for p in processes), 'incomplete proc census')
    for process in processes:
        try:
            threads = list((process / 'task').iterdir())
            require(bool(threads), 'empty task census')
        except (FileNotFoundError, ProcessLookupError):
            continue
        for thread in threads:
            require(thread.name.isdigit(), 'malformed task census')
            try:
                require(not any(m['dev'] in devices for m in mountinfo(read_text(thread / 'mountinfo'))),
                        'NVMe mounted in another process namespace')
                for link in [thread / 'cwd', thread / 'root', thread / 'exe',
                             *list((thread / 'fd').iterdir())]:
                    if (process.name == str(os.getpid()) and link.parent.name == 'fd' and
                            link.name.isdigit() and int(link.name) in own_fds):
                        continue
                    try:
                        info = os.stat(link)
                    except (FileNotFoundError, ProcessLookupError):
                        continue  # Closed descriptor or absent kernel-thread exe.
                    number = info.st_rdev if stat.S_ISBLK(info.st_mode) else info.st_dev
                    require((os.major(number), os.minor(number)) not in devices,
                            'process uses NVMe')
                for line in read_text(thread / 'maps').splitlines():
                    fields = line.split()
                    require(len(fields) >= 5 and re.fullmatch(r'[0-9a-f]+:[0-9a-f]+', fields[3]),
                            'malformed process maps')
                    dev = tuple(int(n, 16) for n in fields[3].split(':'))
                    require(dev not in devices, 'process maps NVMe')
            except (FileNotFoundError, ProcessLookupError):
                # An exited task may disappear between enumeration and inspection.
                require(not thread.exists(), 'incomplete census of a still-live task')


def idle_target(devices, own_fds=()):
    require(not any(m['dev'] in devices for m in mountinfo()), 'NVMe is mounted')
    swaps = read_text('/proc/swaps').splitlines()
    require(bool(swaps) and swaps[0].split() == ['Filename', 'Type', 'Size', 'Used', 'Priority'],
            'malformed swaps header')
    for line in swaps[1:]:
        fields = line.split()
        require(len(fields) == 5 and fields[1] in ('partition', 'file') and
                all(re.fullmatch(r'-?[0-9]+', n) for n in fields[2:]), 'malformed swap entry')
        info = os.stat(unescape_mount(fields[0]))
        number = info.st_rdev if stat.S_ISBLK(info.st_mode) else info.st_dev
        require((os.major(number), os.minor(number)) not in devices, 'NVMe is used for swap')
    for major, minor in devices:
        require(not list(Path(f'/sys/dev/block/{major}:{minor}/holders').iterdir()),
                'NVMe has holders')
    process_consumers(devices, own_fds)


def block_integer(fd, request, fmt):
    value = bytearray(struct.calcsize(fmt))
    fcntl.ioctl(fd, request, value, True)
    return struct.unpack(fmt, value)[0]


def validate_open_target(fd, path, before, size):
    info = os.fstat(fd)
    require(stat.S_ISBLK(info.st_mode) and
            (info.st_dev, info.st_ino, info.st_rdev) == before[Path(path).name],
            'opened target differs')
    for request, fmt, expected in ((BLKGETSIZE64, '=Q', size), (BLKSSZGET, '=I', 512),
                                   (BLKPBSZGET, '=I', 512), (BLKROGET, '=I', 0)):
        require(block_integer(fd, request, fmt) == expected, 'opened device geometry/RO differs')


def verify_gpt(fd):
    for offset, length, expected in GPT_EXTENTS:
        data = os.pread(fd, length, offset)
        require(len(data) == length and hashlib.sha256(data).hexdigest() == expected,
                'installed GPT differs from exact pin')


@contextmanager
def opened_target(expected_boot_id, writable=False):
    host_context(expected_boot_id)
    before, devices = target_identity()
    idle_target(devices)
    disk_fd = part_fd = None
    try:
        # Only p1 receives write capability. The namespace descriptor stays read-only.
        disk_fd = os.open(DISK, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        part_fd = os.open(TARGET, (os.O_RDWR if writable else os.O_RDONLY) |
                          os.O_NOFOLLOW | os.O_CLOEXEC | os.O_EXCL)
        fcntl.flock(part_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        validate_open_target(disk_fd, DISK, before, TARGET_BYTES)
        validate_open_target(part_fd, TARGET, before, SOURCE_BYTES)
        require(target_identity() == (before, devices), 'target identity changed after opening')
        idle_target(devices, (disk_fd, part_fd))
        verify_gpt(disk_fd)
        yield part_fd, disk_fd
        host_context(expected_boot_id)
        require(target_identity() == (before, devices), 'target identity changed after operation')
        idle_target(devices, (disk_fd, part_fd))
        verify_gpt(disk_fd)
    finally:
        errors = []
        for number in (part_fd, disk_fd):
            if number is not None:
                try:
                    os.close(number)
                except BaseException as error:
                    errors.append(error)
        if errors:
            raise errors[0]


# Pinned A733 guest-partition geometry; this is not an arbitrary mount API.
P5 = '/dev/nvme0n1p5'
P5_BYTES = GEOMETRY[4][1] * 512
BLKROSET = 0x125d
P5_PROBE_TIMEOUT = 30.0


def _p5_superblock(data, expected_uuid):
    """Narrow clean known-recipe ext4 check, never a directory/journal parser.

    Offsets and orphan masks follow pinned vendor 2ac08e8c7cdc28abbdc5c9a9dd812f887ae9c79f
    fs/ext4/ext4.h. Supported masks cover the journaled recipe in build/nvme-image.py and
    build/guest-seed.py's named ext4 features; exact defaults are not assumed.
    Absence on any dirty, orphan-bearing or unknown filesystem is not proof.
    """
    require(type(expected_uuid) is str and str(uuid.UUID(expected_uuid)) == expected_uuid
            and uuid.UUID(expected_uuid).int != 0, 'canonical nonzero p5 UUID required')
    require(len(data) == 1024, 'short p5 superblock')
    u32 = lambda offset: struct.unpack_from('<I', data, offset)[0]
    require(data[0x38:0x3a] == b'\x53\xef' and
            data[0x68:0x78] == uuid.UUID(expected_uuid).bytes and
            data[0x78:0x88].rstrip(b'\0') == b'A733GUESTS', 'p5 ext4 identity differs')
    require(u32(0x18) == 2 and u32(4) == P5_BYTES // 4096 and u32(0x150) == 0,
            'p5 full-filesystem geometry differs')
    require(struct.unpack_from('<H', data, 0x3a)[0] == 1 and
            not u32(0x60) & 4 and u32(0xe8) == 0 and
            not u32(0x5c) & 0x1000 and not u32(0x64) & 0x10000,
            'p5 is dirty, needs recovery or has orphan metadata')
    require(u32(0x5c) & 4 and not u32(0x5c) & ~0x3c and
            not u32(0x60) & ~0x2c2 and not u32(0x64) & ~0x46b,
            'unknown p5 ext4 feature profile')
    require(data[0xd0:0xe0] == bytes(16) and u32(0xe0) == 8 and u32(0xe4) == 0,
            'p5 fixed internal journal required')


def _p5_fd(fd, before, expected_uuid, expected_ro):
    info, named = os.fstat(fd), os.lstat(P5)
    require(stat.S_ISBLK(info.st_mode) and stat.S_ISBLK(named.st_mode) and
            (info.st_dev, info.st_ino, info.st_rdev) == before['nvme0n1p5'] ==
            (named.st_dev, named.st_ino, named.st_rdev), 'held p5 node identity differs')
    for command, fmt, expected in ((BLKGETSIZE64, '=Q', P5_BYTES),
                                   (BLKSSZGET, '=I', 512), (BLKPBSZGET, '=I', 512),
                                   (BLKROGET, '=I', expected_ro)):
        require(block_integer(fd, command, fmt) == expected, 'p5 geometry or block RO differs')
    require(read_text('/sys/class/block/nvme0n1p5/ro') == str(expected_ro),
            'p5 sysfs block RO differs')
    _p5_superblock(os.pread(fd, 1024, 1024), expected_uuid)


def _p5_mount(point):
    libc = ctypes.CDLL(None, use_errno=True)
    libc.mount.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
                           ctypes.c_ulong, ctypes.c_char_p]
    libc.mount.restype = ctypes.c_int
    if libc.mount(os.fsencode(P5), os.fsencode(point), b'ext4', 1 | 2 | 4 | 8,
                  b'noload') != 0:
        raise OSError(ctypes.get_errno(), 'p5 read-only noload mount failed')


def _p5_unmount(point):
    libc = ctypes.CDLL(None, use_errno=True)
    libc.umount2.argtypes = [ctypes.c_char_p, ctypes.c_int]
    libc.umount2.restype = ctypes.c_int
    if libc.umount2(os.fsencode(point), 0) != 0:
        raise OSError(ctypes.get_errno(), 'p5 owned normal unmount failed')


def _p5_mount_identity(point, device):
    rows = [row for row in mountinfo() if row['path'] == point]
    require(len(rows) == 1 and rows[0]['dev'] == device and rows[0]['fstype'] == 'ext4'
            and {'ro', 'nosuid', 'nodev', 'noexec'} <= set(rows[0]['options']) and
            'rw' not in rows[0]['options'] and
            {'noload', 'norecovery'} & set(rows[0]['options']), 'p5 mounted identity/options differ')
    # mountinfo() intentionally exposes only the fields used by the old writer.
    # Check full-filesystem root separately without changing that stable API.
    lines = [line.split() for line in read_text('/proc/self/mountinfo').splitlines()]
    rows = [row for row in lines if len(row) >= 6 and unescape_mount(row[4]) == point]
    require(len(rows) == 1 and rows[0][3] == '/', 'p5 full-filesystem mount required')
    info = os.lstat(point)
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and info.st_gid == 0 and
            stat.S_IMODE(info.st_mode) == 0o700 and
            (os.major(info.st_dev), os.minor(info.st_dev)) == device,
            'p5 mounted root metadata/device differs')


def _p5_private_namespace():
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.unshare(0x00020000) != 0:  # CLONE_NEWNS only; PID/user remain native.
        raise OSError(ctypes.get_errno(), 'p5 private mount namespace failed')
    libc.mount.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
                           ctypes.c_ulong, ctypes.c_char_p]
    if libc.mount(None, b'/', None, (1 << 14) | (1 << 18), None) != 0:
        raise OSError(ctypes.get_errno(), 'p5 recursive private propagation failed')


def _p5_child_probe(fd, before, expected_uuid, point, point_identity):
    """Child borrows inherited exclusion; it never manufactures a parent guard.

    A mount syscall can succeed before an exception/lost acknowledgement. Reconcile
    the exact owned point, and report normal unmount only after explicit verification.
    The parent treats missing child evidence as uncertainty and retains block RO.
    """
    result = {'marker_present': None, 'unmounted': False, 'error': None}
    root = None
    private = attempted = False
    try:
        _p5_private_namespace()
        private = True
        info = os.lstat(point)
        require(identity(info) == point_identity, 'p5 probe point changed')
        require(not any(row['path'] == point or row['path'].startswith(point + '/')
                        for row in mountinfo()), 'p5 probe point has a foreign mount')
        _p5_fd(fd, before, expected_uuid, 1)
        device = (os.major(before['nvme0n1p5'][2]), os.minor(before['nvme0n1p5'][2]))
        attempted = True
        _p5_mount(point)
        _p5_mount_identity(point, device)
        _p5_fd(fd, before, expected_uuid, 1)
        root = os.open(point, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        held, named = os.fstat(root), os.lstat(point)
        require(OfflineAdmission._same(held, named), 'p5 probe root descriptor changed')
        try:
            os.stat('.a733-lifecycle', dir_fd=root, follow_symlinks=False)
        except FileNotFoundError:
            result['marker_present'] = False
        else:
            result['marker_present'] = True
        _p5_mount_identity(point, device)
        _p5_fd(fd, before, expected_uuid, 1)
    except BaseException as error:
        result['error'] = (type(error).__name__ + ': ' + str(error))[:512]
    finally:
        if root is not None:
            try:
                os.close(root)
            except BaseException as error:
                result['error'] = 'p5 root close failed: ' + str(error)[:256]
        try:
            if private and attempted:
                rows = [row for row in mountinfo()
                        if row['path'] == point or row['path'].startswith(point + '/')]
                require(bool(rows), 'p5 mount not observed; retain protection')
                if rows:
                    _p5_mount_identity(point, device)
                    _p5_fd(fd, before, expected_uuid, 1)
                    _p5_unmount(point)
                require(not any(row['path'] == point or row['path'].startswith(point + '/')
                                for row in mountinfo()), 'p5 owned unmount not proved')
                require(identity(os.lstat(point)) == point_identity,
                        'p5 probe underlying point changed')
                result['unmounted'] = True
        except BaseException as error:
            result['unmounted'] = False
            result['error'] = 'p5 cleanup uncertain: ' + str(error)[:384]
    return result


def _p5_wait_child(pid, pipe):
    """Bound receipt and wall time, reap only our exact unreaped fork child."""
    deadline = time.monotonic() + P5_PROBE_TIMEOUT
    data = bytearray()
    status = None
    eof = False
    while status is None or not eof:
        require(time.monotonic() <= deadline, 'p5 probe child timeout')
        if status is None:
            observed, code = os.waitpid(pid, os.WNOHANG)
            if observed == pid:
                status = code
        ready, _, _ = select.select([pipe] if not eof else [], [], [], 0.02)
        if ready:
            chunk = os.read(pipe, 2049 - len(data))
            data.extend(chunk)
            require(len(data) <= 2048, 'p5 probe child receipt too large')
            eof = not chunk
    require(status == 0 and data, 'p5 probe child did not acknowledge success')
    receipt = json.loads(bytes(data).decode('ascii'))
    require(type(receipt) is dict and set(receipt) == {'marker_present', 'unmounted', 'error'}
            and type(receipt['unmounted']) is bool and
            (receipt['marker_present'] is None or type(receipt['marker_present']) is bool) and
            (receipt['error'] is None or type(receipt['error']) is str),
            'invalid p5 probe child receipt')
    return receipt


def _p5_run_child(fd, before, expected_uuid, point, point_identity):
    reader = writer = None
    pid = None
    try:
        reader, writer = os.pipe2(os.O_CLOEXEC | os.O_NONBLOCK)
        pid = os.fork()
        if pid == 0:
            try:
                os.close(reader)
                receipt = _p5_child_probe(fd, before, expected_uuid, point, point_identity)
                data = json.dumps(receipt, sort_keys=True, separators=(',', ':')).encode('ascii')
                require(len(data) <= 2048 and os.write(writer, data) == len(data),
                        'p5 child receipt write failed')
                os._exit(0)
            except BaseException:
                os._exit(1)
        number, writer = writer, None
        os.close(number)
        return _p5_wait_child(pid, reader)
    except BaseException:
        if pid is not None and pid > 0:
            # An unreaped child cannot have its PID reused. Never signal after a
            # successful reap, and never infer a PID from a process name.
            try:
                observed, unused = os.waitpid(pid, os.WNOHANG)
                if observed == 0:
                    os.kill(pid, signal.SIGKILL)
                    deadline = time.monotonic() + 2
                    while time.monotonic() < deadline:
                        observed, unused = os.waitpid(pid, os.WNOHANG)
                        if observed == pid:
                            break
                        time.sleep(0.01)
            except ChildProcessError:
                pass
        raise
    finally:
        for number in (writer, reader):
            if number is not None:
                os.close(number)


def refuse_managed_p5(expected_guest_uuid, admission):
    """Prove clean marker absence for legacy raw STATE restore; no write authority.

    The parent retains actual offline admission throughout. Only a private child
    mounts kernel ext4, after verified block RO. Failure receipts conservatively
    retain RO whenever owned unmount, child acknowledgement or identity is unknown.
    This does not authorize storage writes or establish physical hardware acceptance.
    """
    require(type(admission) is OfflineAdmission, 'actual offline admission required')
    admission.check_consumers()
    host_context(admission.boot_id)
    receipt = {'schema': 1, 'operation': 'legacy-restore-p5-clean-absence',
               'target': P5, 'point': None, 'hardware_acceptance': False,
               'storage_authority': False, 'block_ro': 'unchanged',
               'unmounted': False, 'marker_present': None, 'status': 'refused'}
    disk = claim = held = point_fd = None
    point = None
    point_identity = None
    failure = None
    try:
        before, devices = target_identity()
        idle_target(devices)
        disk = os.open(DISK, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        claim = os.open(P5, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_EXCL)
        validate_open_target(disk, DISK, before, TARGET_BYTES)
        _p5_fd(claim, before, expected_guest_uuid, 0)
        verify_gpt(disk)
        require(target_identity() == (before, devices), 'p5 inventory changed before protection')
        idle_target(devices, (disk, claim))
        admission.check_consumers()
        # Set uncertainty BEFORE the ioctl: an exception can follow a kernel change.
        receipt['block_ro'] = 'uncertain'
        fcntl.ioctl(claim, BLKROSET, struct.pack('=I', 1))
        _p5_fd(claim, before, expected_guest_uuid, 1)
        receipt['block_ro'] = 'protected'
        held = os.open(P5, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        _p5_fd(held, before, expected_guest_uuid, 1)
        number, claim = claim, None
        os.close(number)  # An exclusive block claim must not cross kernel mount.
        require(_offline_inventory(1) == (before, devices), 'p5 protected inventory changed')
        idle_target(devices, (disk, held))
        verify_gpt(disk)
        admission.check_consumers()
        point = tempfile.mkdtemp(prefix='a733-p5-probe-', dir='/run')
        receipt['point'] = point
        point_fd = os.open(point, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        info = os.fstat(point_fd)
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == info.st_gid == 0 and
                stat.S_IMODE(info.st_mode) == 0o700 and
                info.st_dev == os.fstat(admission.run).st_dev and
                OfflineAdmission._same(info, os.lstat(point)), 'unsafe p5 probe point')
        OfflineAdmission._tmpfs(point_fd)
        point_identity = identity(info)
        admission.check()
        child = _p5_run_child(held, before, expected_guest_uuid, point, point_identity)
        receipt.update(child)
        require(child['unmounted'], 'p5 normal owned unmount not acknowledged')
        admission.check_consumers()
        require(_offline_inventory(1) == (before, devices), 'p5 identity changed after unmount')
        _p5_fd(held, before, expected_guest_uuid, 1)
        verify_gpt(disk)
        idle_target(devices, (disk, held))
        require(identity(os.fstat(point_fd)) == point_identity == identity(os.lstat(point)),
                'p5 probe point changed after child')
        # Only the normal-unmount acknowledgement and independent target/idle
        # proof allow returning this originally-writable device to flag zero.
        try:
            fcntl.ioctl(held, BLKROSET, struct.pack('=I', 0))
            _p5_fd(held, before, expected_guest_uuid, 0)
            require(target_identity() == (before, devices), 'p5 restored inventory changed')
            receipt['block_ro'] = 'restored-original-zero'
        except BaseException:
            receipt['block_ro'] = 'uncertain'
            # Reprotect only the still-exact device. A failed readback never
            # licenses a blind ioctl against a substituted target.
            require(_offline_inventory_for_reprotect(before, devices),
                    'p5 identity uncertain after RO restoration failure')
            fcntl.ioctl(held, BLKROSET, struct.pack('=I', 1))
            _p5_fd(held, before, expected_guest_uuid, 1)
            receipt['block_ro'] = 'protected-after-restore-failure'
            raise
        require(child['error'] is None, 'p5 child refused: ' + str(child['error']))
        require(child['marker_present'] is False, 'managed p5 marker present; legacy STATE restore refused')
        admission.check_consumers()
        receipt['status'] = 'clean-marker-absence'
    except BaseException as error:
        failure = error
    finally:
        errors = []
        if point is not None and point_identity is not None and receipt['unmounted']:
            try:
                require(identity(os.lstat(point)) == point_identity,
                        'p5 point replaced; retain it')
                os.rmdir(point)
                receipt['point'] = None
            except BaseException as error:
                errors.append(error)
        for number in (point_fd, held, claim, disk):
            if number is not None:
                try:
                    os.close(number)
                except BaseException as error:
                    errors.append(error)
        if errors:
            failure = failure or errors[0]
            receipt['cleanup_errors'] = [str(error)[:256] for error in errors]
    if failure is not None:
        refusal = Refusal('p5 clean-absence probe refused: ' + str(failure))
        refusal.receipt = receipt
        raise refusal from failure
    return json.loads(json.dumps(receipt))


def _offline_inventory_for_reprotect(before, devices):
    # Either flag value is acceptable for identity-only re-protection; each full
    # inventory still checks every node, sysfs geometry and pinned serial/model.
    for value in (0, 1):
        try:
            return _offline_inventory(value) == (before, devices)
        except (Refusal, OSError):
            continue
    return False


def hash_extent(fd, length=SOURCE_BYTES):
    digest = hashlib.sha256()
    offset = 0
    while offset < length:
        data = os.pread(fd, min(CHUNK, length - offset), offset)
        require(bool(data), 'unexpected extent EOF')
        digest.update(data)
        offset += len(data)
    return digest.hexdigest()


def stream_write(source_fd, target_fd, length, progress=None, before_write=None):
    """Copy exactly length including zeros, never touch bytes outside the bound."""
    written = 0
    next_progress = time.monotonic() + 1
    while written < length:
        chunk = os.read(source_fd, min(CHUNK, length - written))
        require(bool(chunk), 'unexpected source EOF')
        offset = 0
        while offset < len(chunk):
            if before_write is not None:
                before_write()
            count = os.write(target_fd, memoryview(chunk)[offset:])
            require(0 < count <= len(chunk) - offset, 'invalid or zero target write')
            offset += count
            written += count
            if progress and time.monotonic() >= next_progress:
                progress(written, length)
                next_progress = time.monotonic() + 1
    if progress:
        progress(written, length)
    return written


def progress(count, total):
    print(f'NVMe BOOT: {count}/{total} bytes', file=sys.stderr, flush=True)


def fresh_output(path, mounts):
    protected_ram_path(path, mounts)
    return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)


def read_receipt(path, mounts, expected_sha, expected_boot_id):
    require(re.fullmatch(r'[0-9a-f]{64}', expected_sha) is not None, 'invalid receipt SHA-256 pin')
    protected_ram_path(path, mounts)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1 and
                stat.S_IMODE(info.st_mode) == 0o600 and 0 < info.st_size <= 16384,
                'receipt must be bounded root 0600 single-link regular file')
        before = identity(info)
        data = os.read(fd, 16385)
        require(hashlib.sha256(data).hexdigest() == expected_sha, 'receipt SHA-256 differs')
        require(identity(os.fstat(fd)) == before and identity(os.lstat(path)) == before,
                'receipt changed while reading')
        receipt = json.loads(data)
    finally:
        os.close(fd)
    require(isinstance(receipt, dict) and receipt.get('schema') == 1 and
            receipt.get('operation') == 'known-nvme-boot-offdevice-backup' and
            receipt.get('serial') == SERIAL and receipt.get('bytes') == SOURCE_BYTES and
            receipt.get('sha256') == OLD_SHA256 and
            receipt.get('export_boot_id') == expected_boot_id and
            receipt.get('authenticated_transfer_verified') is True and
            isinstance(receipt.get('remote_host'), str) and bool(receipt['remote_host'].strip()) and
            isinstance(receipt.get('remote_path'), str) and receipt['remote_path'].startswith('/'),
            'off-device backup receipt fields differ')
    return receipt


def export_backup(path, expected_boot_id, mounts):
    output_fd = fresh_output(path, mounts)
    try:
        with opened_target(expected_boot_id) as (part_fd, _):
            count = stream_write(part_fd, output_fd, SOURCE_BYTES, progress)
            os.fsync(output_fd)
            require(count == SOURCE_BYTES, 'incomplete backup')
        os.close(output_fd)
        output_fd = None
        source_fd, _ = open_source(path, mounts, OLD_SHA256)
        os.close(source_fd)
    finally:
        if output_fd is not None:
            os.close(output_fd)
    return {'backup_path': path, 'bytes': SOURCE_BYTES, 'sha256': OLD_SHA256,
            'export_boot_id': expected_boot_id, 'status': 'export-complete'}


def inspect_target(expected_boot_id):
    """Observe the fixed BOOT target under the same exclusive read-only claim."""
    with opened_target(expected_boot_id) as (part_fd, _):
        before, _ = target_identity()
        print('Inspecting live BOOT SHA-256...', file=sys.stderr, flush=True)
        current_sha = hash_extent(part_fd, SOURCE_BYTES)
    return {'status': 'inspect-complete', 'current_sha256': current_sha,
            'observed_boot_id': expected_boot_id, 'bytes_written': 0, 'write_bytes': 0,
            'read_only': True,
            'model': MODEL, 'namespace_bytes': TARGET_BYTES, 'sector_bytes': 512,
            'target_identity': before, 'partition_geometry_sectors': GEOMETRY,
            'gpt_extents': [{'offset': offset, 'bytes': length, 'sha256': digest}
                            for offset, length, digest in GPT_EXTENTS],
            'gpt_verified': True}


def apply_payload(path, expected_boot_id, mounts, rollback=False, readback=False,
                  recovery_current_sha=None):
    if recovery_current_sha is not None:
        require(not rollback and re.fullmatch(r'[0-9a-f]{64}', recovery_current_sha) is not None,
                'recovery requires a lowercase SHA-256 current-target pin')
        current_sha, source_sha = recovery_current_sha, OLD_SHA256
    else:
        current_sha, source_sha = ((NEW_SHA256, OLD_SHA256) if rollback else (OLD_SHA256, NEW_SHA256))
    with OfflineAdmission.acquire(expected_boot_id) as admission:
        admission.check()
        source_fd, source_identity = open_source(path, mounts, source_sha)
        try:
            admission.check()
            with opened_target(expected_boot_id, writable=True) as (part_fd, disk_fd):
                print('Checking live predecessor BOOT SHA-256...', file=sys.stderr, flush=True)
                require(hash_extent(part_fd, SOURCE_BYTES) == current_sha, 'live BOOT hash differs from required predecessor')
                host_context(expected_boot_id)
                before, devices = target_identity()
                validate_open_target(part_fd, TARGET, before, SOURCE_BYTES)
                validate_open_target(disk_fd, DISK, before, TARGET_BYTES)
                idle_target(devices, (disk_fd, part_fd))
                verify_gpt(disk_fd)
                validate_source(source_fd, path, source_identity)
                if recovery_current_sha is not None:
                    print('Rechecking caller-pinned BOOT immediately before recovery...',
                          file=sys.stderr, flush=True)
                    require(hash_extent(part_fd, SOURCE_BYTES) == current_sha,
                            'live BOOT changed since caller inspection or recovery admission')
                    validate_source(source_fd, path, source_identity)
                os.lseek(source_fd, 0, os.SEEK_SET)
                os.lseek(part_fd, 0, os.SEEK_SET)
                count = stream_write(source_fd, part_fd, SOURCE_BYTES, progress,
                                     before_write=admission.check)
                admission.check()
                validate_source(source_fd, path, source_identity)
                print('Flushing NVMe BOOT (fsync and BLKFLSBUF)...', file=sys.stderr, flush=True)
                os.fsync(part_fd)
                admission.check()
                fcntl.ioctl(part_fd, BLKFLSBUF)
                if readback:
                    require(hash_extent(part_fd, SOURCE_BYTES) == source_sha, 'explicit BOOT readback failed')
            result = {'status': ('recovery-complete' if recovery_current_sha is not None else
                               'rollback-complete' if rollback else 'write-complete'),
                    'predecessor_sha256': current_sha,
                    'bytes_written': count, 'source_sha256': source_sha, 'readback': readback}
        finally:
            os.close(source_fd)
        admission.check_consumers()
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('export-backup', 'apply', 'rollback', 'inspect', 'recover-backup'))
    parser.add_argument('payload', nargs='?', help='fresh backup output or raw input in root0700 /run tmpfs directory')
    parser.add_argument('--expected-boot-id', required=True)
    parser.add_argument('--backup-receipt')
    parser.add_argument('--backup-receipt-sha256')
    parser.add_argument('--expected-current-sha256', help='recover-backup only: fresh inspect current_sha256')
    parser.add_argument('--readback', action='store_true', help='optional complete p1 readback after write')
    parser.add_argument('--report', required=True, help='fresh protected /run JSON result')
    args = parser.parse_args(argv)
    report_fd = None
    report = {'operation': 'known-nvme-boot-' + args.operation, 'status': 'failed-or-refused',
              'target': TARGET, 'serial': SERIAL, 'write_offset': 0, 'namespace_offset': 1048576,
              'write_bytes': 0 if args.operation == 'inspect' else SOURCE_BYTES,
              'expected_boot_id': args.expected_boot_id,
              'authorization': AUTHORIZATION, 'hardware_acceptance': False, 'bytes_written': 0}
    try:
        mounts = host_context(args.expected_boot_id)
        require(args.report != args.payload and args.report != args.backup_receipt,
                'report must be distinct from inputs')
        report_fd = fresh_output(args.report, mounts)
        print(json.dumps(report, sort_keys=True), file=sys.stderr, flush=True)
        if args.operation == 'inspect':
            require(args.payload is None and not args.readback and not args.backup_receipt and
                    not args.backup_receipt_sha256 and args.expected_current_sha256 is None,
                    'inspect takes no payload, receipt, readback or current-target pin')
            report.update(inspect_target(args.expected_boot_id))
        elif args.operation == 'export-backup':
            require(args.payload is not None, 'backup output payload path required')
            require(args.expected_current_sha256 is None, 'current-target pin is recovery-only')
            require(not args.readback and not args.backup_receipt and not args.backup_receipt_sha256,
                    'export takes no receipt or readback')
            report.update(export_backup(args.payload, args.expected_boot_id, mounts))
        else:
            require(args.payload is not None, 'input payload path required')
            if args.operation == 'recover-backup':
                require(args.expected_current_sha256 is not None and
                        re.fullmatch(r'[0-9a-f]{64}', args.expected_current_sha256) is not None,
                        'recover-backup requires --expected-current-sha256 from fresh inspect')
                report['expected_current_sha256'] = args.expected_current_sha256
            else:
                require(args.expected_current_sha256 is None, 'current-target pin is recovery-only')
            require(args.backup_receipt and args.backup_receipt_sha256, 'pinned off-device backup receipt required')
            # Rollback may occur in a later offline boot: its caller must create a new
            # authenticated receipt for the retained old backup bound to that boot ID.
            receipt = read_receipt(args.backup_receipt, mounts, args.backup_receipt_sha256,
                                   args.expected_boot_id)
            report['backup_receipt_sha256'] = args.backup_receipt_sha256
            report['backup_remote_host'] = receipt['remote_host']
            report['bytes_written'] = None  # Any subsequent failure may leave a partial p1.
            report.update(apply_payload(args.payload, args.expected_boot_id, mounts,
                                        args.operation == 'rollback', args.readback,
                                        args.expected_current_sha256))
        result = 0
    except (OSError, ValueError, Refusal) as error:
        report['error'] = str(error)
        print(f'Refused/failed: {error}', file=sys.stderr, flush=True)
        result = 1
    try:
        data = (json.dumps(report, sort_keys=True) + '\n').encode('ascii')
        if report_fd is not None:
            offset = 0
            while offset < len(data):
                count = os.write(report_fd, data[offset:])
                require(count > 0, 'zero report write')
                offset += count
            os.fsync(report_fd)
        print(data.decode('ascii'), end='', flush=True)
    finally:
        if report_fd is not None:
            os.close(report_fd)
    return result


if __name__ == '__main__':
    sys.exit(main())
