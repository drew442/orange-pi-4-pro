#!/usr/bin/python3
"""Read-only stopped-guest inspection with retained descriptors and disk flock.

Consumer scanning is a snapshot. Trusted root and cooperative in-place writers
must honor the lock; this does not defend against root replacing or opening a
file after the snapshot. No filesystem cleanliness, backup or KVM claim follows.
"""
import copy
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
import threading

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
BACKUP = module('checkpoint_custody_backup', 'configs/guest_backup.py')


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

    def prepare_managed_publication(self, new_manifest_data, history_data, *, authorization):
        """Retain the stopped disk for one externally authorized exact publication."""
        require(type(self) is Inspection, 'actual stopped inspection required')
        return PreparedPublication._prepare(self, new_manifest_data, history_data, authorization)

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


_PUBLICATION_TOKEN = object()


def _disk_flock(fd, owner):
    """Inspect the existing open description; never acquire or convert a lock."""
    info = os.fstat(fd)
    fields = {}
    data = STORAGE.read_bounded(Path('/proc/self/fdinfo') / str(fd), STORAGE.FDINFO_LIMIT)
    for line in data.splitlines():
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
    require(match is not None, 'missing retained disk flock')
    access, pid, major, minor, inode = match.groups()
    require(access == 'WRITE' and int(pid) == owner and
            (int(major, 16), int(minor, 16), int(inode)) ==
            (os.major(info.st_dev), os.minor(info.st_dev), info.st_ino),
            'retained disk flock identity differs')


class StoppedDiskCustody:
    """Process/thread-bound aliases, retaining one disk flock across publication.

    Authorization is borrowed: its recheck must independently authenticate the
    protected outcome, intent and exact checkpoint head. Parsing is not authority.
    No publication or payload mutation is performed here.
    """
    def __init__(self, token):
        require(token is _PUBLICATION_TOKEN, 'publication factory required')
        self._pid, self._thread = os.getpid(), threading.get_ident()
        self._owned = []
        self._closed = self._poisoned = self._bound = False
        self._history_fd = self._history_directory = None

    @classmethod
    def _prepare(cls, inspection, new_data, history_data, authorization):
        require(type(inspection) is Inspection, 'actual stopped inspection required')
        require(callable(getattr(authorization, 'recheck', None)),
                'retained checkpoint authorization required')
        result = cls(_PUBLICATION_TOKEN)
        result._authorization = authorization
        try:
            authorization.recheck()
            inspection.recheck()
            require(type(new_data) is bytes and 0 < len(new_data) <= MANIFEST.LIMIT,
                    'invalid prepared manifest bytes')
            old_data = inspection.manifest_data
            old_sha = hashlib.sha256(old_data).hexdigest()
            require(inspection.manifest_record == {'bytes': len(old_data), 'sha256': old_sha},
                    'old manifest pin differs')
            old = MANIFEST.validate_manifest(old_data)
            observed = inspection._hash(inspection.disk_fd, old['config']['disk']['bytes'])
            require(observed == inspection.disk_record, 'stopped disk changed')
            changed = observed != old['config']['disk']
            proposed = MANIFEST.validate_manifest(new_data)
            if changed:
                require(proposed['schema'] == 2 and 'checkpoint' in proposed,
                        'managed checkpoint manifest required')
                expected = MANIFEST.proposed_managed_manifest(
                    old, old_sha, observed, proposed['checkpoint']['shutdown_provenance'])
                require(new_data == MANIFEST.canonical(expected), 'prepared manifest differs')
                new_sha = hashlib.sha256(new_data).hexdigest()
                archive_name = old_sha + '-' + new_sha + '.json'
                parsed_old, parsed_new, raw_old = BACKUP.validate_record(archive_name, history_data)
                value = BACKUP.parse_json(history_data)
                require(value['schema'] == 2 and
                        value['operation'] == 'a733-managed-guest-checkpoint-v2' and
                        ENROLL.canonical(value) == history_data and
                        raw_old == old_data and parsed_old == old and parsed_new == proposed and
                        BACKUP.decode(value['new_manifest_base64'], MANIFEST.LIMIT) == new_data,
                        'prepared history differs')
            else:
                require(new_data == old_data and history_data is None,
                        'unchanged publication must preserve exact manifest without history')
                archive_name = None
            result._old_data, result._new_data = old_data, new_data
            result._history_data, result._archive_name = history_data, archive_name
            result._disk_record = copy.deepcopy(observed)
            result._inspection = result._retain(inspection)
            result._check(bound=False)
            authorization.recheck()
            return result
        except BaseException:
            result._poisoned = True
            result.close()
            raise

    def _dup(self, fd):
        alias = fcntl.fcntl(fd, fcntl.F_DUPFD_CLOEXEC, 0)
        self._owned.append(alias)
        return alias

    def _retain(self, source):
        retained = object.__new__(Inspection)
        retained.name = source.name
        retained.context = copy.deepcopy(source.context)
        retained.manifest = MANIFEST.validate_manifest(source.manifest_data)
        retained.manifest_data = source.manifest_data
        retained.manifest_record = copy.deepcopy(source.manifest_record)
        retained.disk_record = copy.deepcopy(source.disk_record)
        root = object.__new__(ENROLL.HeldPath)
        root.path, root.names = source.root.path, list(source.root.names)
        root.fds = [self._dup(fd) for fd in source.root.fds]
        root.fd, root.info = root.fds[-1], source.root.info
        retained.root = root
        mapping = dict(zip(source.root.fds, root.fds))
        for fd in source._opened:
            mapping[fd] = self._dup(fd)
        retained._opened = [mapping[fd] for fd in source._opened]
        retained._infos = {mapping[fd]: (mapping[parent], name, info)
                           for fd, (parent, name, info) in source._infos.items()}
        retained.guest_fd, retained.manifest_fd = mapping[source.guest_fd], mapping[source.manifest_fd]
        retained._guest_info = source._guest_info
        retained.fds = {key: mapping[fd] for key, fd in source.fds.items()}
        retained.disk_fd = retained.fds['disk']
        return retained

    @property
    def guest_fd(self):
        return self._inspection.guest_fd

    @property
    def disk_fd(self):
        return self._inspection.disk_fd

    @property
    def old_manifest_data(self):
        return self._old_data

    @property
    def new_manifest_data(self):
        return self._new_data

    @property
    def history_data(self):
        return self._history_data

    @property
    def archive_name(self):
        return self._archive_name

    @property
    def disk_record(self):
        return copy.deepcopy(self._disk_record)

    def _owner(self):
        require(os.getpid() == self._pid and threading.get_ident() == self._thread,
                'publication custody creator required')
        require(not self._closed and not self._poisoned, 'publication custody unavailable')

    def _check(self, *, bound, owned_entries=None):
        self._owner()
        self._authorization.recheck()
        held = self._inspection
        _disk_flock(held.disk_fd, self._pid)
        held.recheck(owned_entries=owned_entries)
        require(held._read(held.manifest_fd, len(held.manifest_data)) ==
                (self._new_data if bound else self._old_data), 'retained manifest bytes differ')
        for key in ('kernel', 'initrd'):
            expected = held.manifest['config'][key]
            require(held._hash(held.fds[key], expected['bytes']) == expected,
                    'retained boot input differs')
        require(held._hash(held.disk_fd, self._disk_record['bytes']) == self._disk_record,
                'retained stopped disk differs')
        if bound and self._history_data is not None:
            self._check_history()
        self._authorization.recheck()

    def before_recheck(self, *, owned_entries=None):
        try:
            require(not self._bound, 'publication already bound')
            self._check(bound=False, owned_entries=owned_entries)
        except BaseException:
            self._poisoned = True
            raise

    def _check_history(self):
        held = self._inspection
        directory, fd = self._history_directory, self._history_fd
        info = os.fstat(directory)
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == info.st_gid == 0 and
                stat.S_IMODE(info.st_mode) == 0o700 and info.st_dev == held._guest_info.st_dev and
                ENROLL.inode(info) == self._history_directory_inode and
                ENROLL.inode(os.stat('.checkpoints', dir_fd=held.guest_fd,
                                     follow_symlinks=False)) == ENROLL.inode(info),
                'unsafe retained checkpoint history directory')
        info = os.fstat(fd)
        held._metadata(info, 0, 0, 0o400, len(self._history_data))
        require(ENROLL.identity(info) == self._history_info and
                ENROLL.inode(os.stat(self._archive_name, dir_fd=directory,
                                     follow_symlinks=False)) == ENROLL.inode(info) and
                os.pread(fd, len(self._history_data) + 1, 0) == self._history_data and
                ENROLL.identity(os.fstat(fd)) == self._history_info,
                'retained checkpoint history differs')

    def bind_published(self):
        """Accept only the exact prepared pair under the retained guest directory."""
        try:
            self._owner()
            require(not self._bound, 'publication already bound')
            self._authorization.recheck()
            held = self._inspection
            if self._history_data is None:
                self._check(bound=False)
                self._bound = True
                return self
            # Keep the old manifest descriptor owned until final close, but remove
            # its deliberately stale named check only for this exact handoff.
            fd = os.open('enrollment.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=held.guest_fd)
            self._owned.append(fd)
            held._metadata(os.fstat(fd), 0, 0, 0o400, len(self._new_data))
            info = os.fstat(fd)
            require(os.pread(fd, len(self._new_data) + 1, 0) == self._new_data and
                    ENROLL.identity(os.fstat(fd)) == ENROLL.identity(info),
                    'published manifest differs from prepared bytes')
            old_fd = held.manifest_fd
            held._opened.remove(old_fd)
            held._opened.append(fd)
            held._infos[fd] = (held.guest_fd, 'enrollment.json', info)
            held.manifest_fd = fd
            held.manifest_data = self._new_data
            held.manifest = MANIFEST.validate_manifest(self._new_data)
            held.manifest_record = {'bytes': len(self._new_data),
                                    'sha256': hashlib.sha256(self._new_data).hexdigest()}
            if self._history_data is not None:
                directory = os.open('.checkpoints', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                    dir_fd=held.guest_fd)
                self._owned.append(directory)
                self._history_directory = directory
                self._history_directory_inode = ENROLL.inode(os.fstat(directory))
                archive = os.open(self._archive_name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                  dir_fd=directory)
                self._owned.append(archive)
                self._history_fd = archive
                self._history_info = ENROLL.identity(os.fstat(archive))
            self._check(bound=True)
            self._bound = True
            return self
        except BaseException:
            self._poisoned = True
            raise

    def recheck(self):
        try:
            require(self._bound, 'published manifest has not been bound')
            self._check(bound=True)
        except BaseException:
            self._poisoned = True
            raise

    def close(self):
        require(os.getpid() == self._pid and threading.get_ident() == self._thread,
                'publication custody creator required')
        # Detach before attempting close: EINTR can mean an FD was already closed
        # and reused. Never retry a numeric descriptor after uncertain close.
        owned, self._owned = self._owned, []
        self._closed = True
        error = None
        for fd in reversed(owned):
            try:
                os.close(fd)
            except BaseException as caught:
                error = caught
        if error is not None:
            raise error

    def __enter__(self):
        self._owner()
        return self

    def __exit__(self, *_):
        self.close()


class PreparedPublication(StoppedDiskCustody):
    """An exact validated old/new pair with retained stopped-disk custody."""
