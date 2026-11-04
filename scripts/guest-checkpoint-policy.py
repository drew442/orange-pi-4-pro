#!/usr/bin/python3
"""Read-only stopped-guest inspection with retained descriptors and disk flock.

Consumer scanning is a snapshot. Trusted root and cooperative in-place writers
must honor the lock; this does not defend against root replacing or opening a
file after the snapshot. No filesystem cleanliness, backup or KVM claim follows.
"""
import errno
import fcntl
import hashlib
import importlib.util
import os
from pathlib import Path
import re
import select
import stat
import subprocess
import time

BASE = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, BASE / path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


ENROLL = module('checkpoint_enrollment', 'scripts/enroll-guest.py')
MANIFEST = module('checkpoint_manifest', 'configs/guest_manifest.py')
MANAGEMENT, ACCOUNTS, CONFIG, STORAGE = ENROLL.MANAGEMENT, ENROLL.ACCOUNTS, ENROLL.CONFIG, ENROLL.STORAGE
require = CONFIG.require


def host_namespace():
    for namespace in ('mnt', 'pid'):
        require(ENROLL.inode(os.stat('/proc/self/ns/' + namespace)) ==
                ENROLL.inode(os.stat('/proc/1/ns/' + namespace)), 'host namespace required')


def service_state(name):
    require(type(name) is str and re.fullmatch(r'[a-z](?:[a-z0-9-]{0,14}[a-z0-9])?', name),
            'invalid guest name')
    process = subprocess.Popen([
        '/usr/bin/systemctl', 'show', '--no-pager',
        '--property=LoadState,ActiveState,SubState,MainPID,ControlPID', '--',
        'a733-guest-' + name + '.service'], stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
        env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'})
    data, deadline = b'', time.monotonic() + 10
    try:
        while True:
            remaining = deadline - time.monotonic()
            require(remaining > 0 and select.select([process.stdout], [], [], remaining)[0],
                    'systemctl timeout')
            piece = os.read(process.stdout.fileno(), 16385 - len(data))
            if not piece:
                break
            data += piece
            require(len(data) <= 16384, 'oversized systemctl output')
        code = process.wait(timeout=max(0.01, deadline - time.monotonic()))
    finally:
        if process.poll() is None:
            process.kill()
        try:
            process.wait(timeout=1)
        finally:
            process.stdout.close()
    return parse_service_state(data, code)


def parse_service_state(data, code):
    require(type(data) is bytes and 0 < len(data) <= 16384, 'invalid systemctl output')
    try:
        fields = dict(CONFIG.IDENTITY.unique_pairs(line.split('=', 1)
                      for line in data.decode('ascii').splitlines()))
    except (UnicodeError, ValueError) as error:
        raise ValueError('invalid systemctl fields') from error
    require(set(fields) == {'LoadState', 'ActiveState', 'SubState', 'MainPID', 'ControlPID'},
            'incomplete systemctl state')
    require(fields['LoadState'] in ('loaded', 'not-found') and
            fields['ActiveState'] == 'inactive' and fields['SubState'] == 'dead' and
            fields['MainPID'] == fields['ControlPID'] == '0', 'guest service must be stopped')
    require(code == 0 or (code == 1 and fields['LoadState'] == 'not-found'), 'systemctl failed')
    return fields


def disk_consumers(fd, proc_root=Path('/proc')):
    """List other processes holding the same inode, including read-only readers."""
    expected = ENROLL.inode(os.fstat(fd))
    deadline, count, pids, result = time.monotonic() + 5, 0, 0, []
    def check():
        require(time.monotonic() <= deadline, 'consumer scan timeout')
    def raced(error):
        return error.errno in (errno.ENOENT, errno.ESRCH)
    with os.scandir(proc_root) as processes:
        for process in processes:
            check()
            if not re.fullmatch('[0-9]+', process.name):
                continue
            pids += 1
            require(pids <= 4096, 'too many process entries')
            if int(process.name) == os.getpid():
                continue
            try:
                with os.scandir(Path(proc_root) / process.name / 'fd') as entries:
                    for entry in entries:
                        check()
                        count += 1
                        require(count <= 65536, 'too many process descriptors')
                        require(re.fullmatch('[0-9]+', entry.name), 'invalid process descriptor')
                        try:
                            info = entry.stat(follow_symlinks=True)
                        except OSError as error:
                            if raced(error):
                                continue
                            raise
                        if ENROLL.inode(info) == expected:
                            result.append({'pid': int(process.name), 'fd': int(entry.name)})
            except OSError as error:
                if not raced(error):
                    raise
    return result


def semantic(context):
    return {key: value for key, value in context.items() if key != 'boot_inputs_checked'}


class Inspection:
    def __init__(self, name, manifest_sha256):
        self.root = None
        self._opened = []
        self._infos = {}
        self.fds = {}
        try:
            MANAGEMENT.runtime_guard()
            host_namespace()
            require(type(name) is str and re.fullmatch(r'[a-z](?:[a-z0-9-]{0,14}[a-z0-9])?', name),
                    'invalid guest name')
            MANIFEST.digest(manifest_sha256)
            self.name = name
            self.root = ENROLL.HeldPath(ENROLL.ROOT, directory=True)
            root = self.root.info
            require(root.st_uid == root.st_gid == 0 and stat.S_IMODE(root.st_mode) == 0o711,
                    'guest root must be root:root 0711')
            self.context = STORAGE.verify_guest_directory(self.root.fd)
            self.guest_fd = self._open(name, self.root.fd, directory=True)
            info = os.fstat(self.guest_fd)
            require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and
                    stat.S_IMODE(info.st_mode) == 0o710 and info.st_dev == root.st_dev,
                    'unsafe guest directory')
            self._guest_info = info
            self.manifest_fd = self._open('enrollment.json', self.guest_fd)
            manifest_info = os.fstat(self.manifest_fd)
            self._metadata(manifest_info, 0, 0, 0o400)
            require(0 < manifest_info.st_size <= MANIFEST.LIMIT, 'invalid manifest size')
            self.manifest_data = self._read(self.manifest_fd, manifest_info.st_size)
            require(hashlib.sha256(self.manifest_data).hexdigest() == manifest_sha256,
                    'manifest SHA-256 differs')
            self.manifest = MANIFEST.validate_manifest(self.manifest_data)
            self.manifest_record = {'bytes': len(self.manifest_data), 'sha256': manifest_sha256}
            config = self.manifest['config']
            require(config['name'] == name and info.st_gid == config['gid'], 'guest identity differs')
            ENROLL.probe_uuid(self.context, self.manifest['guest_uuid'])
            self._accounts()
            for key, filename in ENROLL.FILENAMES.items():
                fd = self._open(filename, self.guest_fd)
                self.fds[key] = fd
                record = self.manifest['files'][filename]
                self._metadata(os.fstat(fd), record['uid'], record['gid'], int(record['mode'], 8), record['bytes'])
            self.disk_fd = self.fds['disk']
            self._storage()
            fcntl.flock(self.disk_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.recheck()
            for key in ('kernel', 'initrd', 'disk'):
                fd, expected = self.fds[key], config[key]
                observed = self._hash(fd, expected['bytes'])
                if key == 'disk':
                    self.disk_record = observed
                else:
                    require(observed == expected, key + ' SHA-256 differs')
            self.recheck()
        except BaseException:
            self.close()
            raise

    def _open(self, name, parent, directory=False):
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        if directory:
            flags |= os.O_DIRECTORY
        fd = os.open(name, flags, dir_fd=parent)
        self._opened.append(fd)
        self._infos[fd] = (parent, name, os.fstat(fd))
        return fd

    def _metadata(self, info, uid, gid, mode, size=None):
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and
                info.st_uid == uid and info.st_gid == gid and stat.S_IMODE(info.st_mode) == mode and
                info.st_dev == self._guest_info.st_dev and (size is None or info.st_size == size),
                'unsafe guest file metadata')

    def _held(self, fd):
        parent, name, before = self._infos[fd]
        current = os.fstat(fd)
        keys = ('dev', 'ino', 'mode', 'uid', 'gid') if fd == self.guest_fd else tuple(ENROLL.identity(before))
        require(all(getattr(current, 'st_' + key) == getattr(before, 'st_' + key) for key in keys) and
                ENROLL.inode(os.stat(name, dir_fd=parent, follow_symlinks=False)) == ENROLL.inode(before),
                'held guest input changed')

    def _read(self, fd, size):
        self._held(fd)
        data = os.pread(fd, size + 1, 0)
        self._held(fd)
        require(len(data) == size, 'input size changed')
        return data

    def _hash(self, fd, size):
        self._held(fd)
        digest, count = hashlib.sha256(), 0
        while True:
            data = os.pread(fd, min(ENROLL.CHUNK, size - count + 1), count)
            if not data:
                break
            count += len(data)
            require(count <= size, 'guest input grew')
            digest.update(data)
        self._held(fd)
        require(count == size, 'guest input size changed')
        return {'bytes': count, 'sha256': digest.hexdigest()}

    def _accounts(self):
        config = self.manifest['config']
        ACCOUNTS.validate_accounts({'schema': 1, 'guests': [
            {key: config[key] for key in ('name', 'uid', 'gid')}]}, ACCOUNTS.snapshot(), complete=True)

    def _storage(self):
        require(semantic(STORAGE.verify_guest_directory(self.root.fd)) == semantic(self.context),
                'guest directory storage changed')
        require(semantic(STORAGE.verify_guest_storage(self.disk_fd,
                boot_fds=(self.fds['kernel'], self.fds['initrd']))) == semantic(self.context),
                'guest file storage changed')

    def recheck(self, *, owned_entries=None):
        MANAGEMENT.runtime_guard()
        host_namespace()
        # Do not pin root timestamps: another guest may be enrolled concurrently.
        self.root.info = os.fstat(self.root.fd)
        require(self.root.info.st_uid == self.root.info.st_gid == 0 and
                stat.S_IMODE(self.root.info.st_mode) == 0o711, 'unsafe guest root')
        self.root.recheck()
        for fd in self._opened:
            self._held(fd)
        owned_entries = {} if owned_entries is None else owned_entries
        require(type(owned_entries) is dict, 'invalid owned entries')
        for name, expected in owned_entries.items():
            require(type(name) is str and re.fullmatch(r'\.checkpoint-[0-9a-f]{32}', name),
                    'invalid owned manifest name')
            require(type(expected) is tuple and len(expected) == 2 and
                    all(type(number) is int and number >= 0 for number in expected),
                    'invalid owned manifest identity')
            info = os.stat(name, dir_fd=self.guest_fd, follow_symlinks=False)
            self._metadata(info, 0, 0, 0o400)
            require(ENROLL.inode(info) == expected, 'owned manifest replaced')
        base = {'Image', 'initramfs.cpio.gz', 'disk.raw', 'enrollment.json'} | set(owned_entries)
        require(set(os.listdir(self.guest_fd)) in (base, base | {'.checkpoints'}),
                'unexpected guest directory entry')
        self._storage()
        ENROLL.probe_uuid(self.context, self.manifest['guest_uuid'])
        self._accounts()
        self.service = service_state(self.name)
        require(not disk_consumers(self.disk_fd), 'guest disk has another consumer')

    def close(self):
        error = None
        for fd in reversed(self._opened):
            try:
                os.close(fd)
            except BaseException as caught:
                error = caught
        self._opened.clear()
        if self.root is not None:
            try:
                self.root.close()
            except BaseException as caught:
                error = caught
            self.root = None
        if error:
            raise error

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
