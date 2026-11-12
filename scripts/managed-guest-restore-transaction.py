#!/usr/bin/python3
"""Same-board managed restoration under actual shutdown EX and physical custody.

Private stages and originals are retained permanently. Failure never retries a
write, rolls back, deletes data, or reopens admission. Explicit resume observes
exact reachable states before advancing. Receipts are data, never capabilities.
Caller constructs canonical CUSTODY first, then canonical SOURCE.Source, and
closes both after execute. Transaction closes every descriptor it owns.
"""
import copy
import ctypes
import fcntl
import importlib.util
import os
from pathlib import Path
import stat
import re
import sys
import uuid

BASE = Path(__file__).resolve().parents[1]


def module(name, relative):
    spec = importlib.util.spec_from_file_location(name, BASE / relative)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


CUSTODY = module('restore_transaction_custody', 'scripts/managed-guest-restore-custody.py')
SOURCE = module('restore_transaction_source', 'scripts/managed-guest-restore-source.py')
SCHEMA = module('restore_transaction_schema', 'configs/guest_managed_restore.py')
CP, ADMISSION = CUSTODY.CP, CUSTODY.ADMISSION
require = SCHEMA.require
STATE_NAME, P5_NAME = 'guest-restores', '.a733-restores'
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


def _inode(info):
    return [info.st_dev, info.st_ino]


def _mount(fd):
    storage = CUSTODY.CONTEXT.STORAGE
    return storage.fd_mount_id(storage.read_bounded('/proc/self/fdinfo/' + str(fd), storage.FDINFO_LIMIT))


def _directory(info, device, *, uid=0, gid=0, mode=0o700):
    require(stat.S_ISDIR(info.st_mode) and info.st_dev == device and
            (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (uid, gid, mode),
            'unsafe restore directory')


def _write(fd, data):
    offset = 0
    while offset < len(data):
        count = os.write(fd, data[offset:])
        require(0 < count <= len(data) - offset, 'invalid restore write')
        offset += count


def _rename(source_parent, source, target_parent, target):
    """Linux atomic no-replace, including directories. No overwrite fallback."""
    libc = ctypes.CDLL(None, use_errno=True)
    call = libc.renameat2
    call.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    call.restype = ctypes.c_int
    if call(source_parent, os.fsencode(source), target_parent, os.fsencode(target), 1) != 0:
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number), target)


def _names(fd):
    result = []
    with os.scandir(fd) as rows:
        for row in rows:
            result.append(row.name)
            require(len(result) <= SCHEMA.COUNT, 'restore namespace exceeds bound')
    return set(result)


def _maybe(parent, name):
    try:
        return os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return None


class ReadError(RuntimeError):
    def __init__(self, primary, errors):
        self.primary = primary
        self.receipt = dict(primary_error=repr(primary) if primary else None,
                            cleanup_uncertain=bool(errors), close_errors=errors)
        super().__init__(f'restore record read/cleanup: {primary!r}; {errors!r}')


def _read(parent, name, limit=SCHEMA.LIMIT, validate=None):
    before = os.stat(name, dir_fd=parent, follow_symlinks=False)
    CP.STORE._regular(before, os.fstat(parent).st_dev, limit)
    fd = os.open(name, FILE_FLAGS, dir_fd=parent)
    fds, primary, errors, raw = [fd], None, [], None
    try:
        require(CP.STORE._metadata(before) == CP.STORE._metadata(os.fstat(fd)) and
                _mount(fd) == _mount(parent), 'restore record replaced or mounted')
        raw = os.pread(fd, limit + 1, 0)
        require(len(raw) == before.st_size and CP.STORE._metadata(before) ==
                CP.STORE._metadata(os.fstat(fd)) == CP.STORE._metadata(os.stat(
                    name, dir_fd=parent, follow_symlinks=False)), 'restore record changed')
        if validate is not None:
            validate(raw)
        named = os.open(name, FILE_FLAGS, dir_fd=parent)
        fds.append(named)
        require(CP.STORE._metadata(os.fstat(named)) == CP.STORE._metadata(before) ==
                CP.STORE._metadata(os.fstat(fd)) and _mount(named) == _mount(fd) == _mount(parent),
                'restore record final binding changed or mounted')
    except BaseException as error:
        primary = error
    for retained in reversed(fds):
        try:
            os.close(retained)
        except BaseException as error:
            errors.append(dict(resource='record:' + name, error=repr(error)))
    if errors:
        raise ReadError(primary, errors) from primary
    if primary is not None:
        raise primary
    return raw


def _pending(parent, name):
    """Metadata-only partial record check; even same-FS bind aliases refuse."""
    require(name.startswith('.pending-'), 'unexpected partial restore record')
    SCHEMA.transaction_id(name[len('.pending-'):])
    info = os.stat(name, dir_fd=parent, follow_symlinks=False)
    require(stat.S_ISREG(info.st_mode) and info.st_uid == info.st_gid == 0 and
            stat.S_IMODE(info.st_mode) in (0o600, 0o400) and info.st_nlink == 1 and
            info.st_dev == os.fstat(parent).st_dev and 0 <= info.st_size <= SCHEMA.LIMIT,
            'unsafe partial restore record')
    fd = os.open(name, FILE_FLAGS, dir_fd=parent)
    primary, errors = None, []
    try:
        require(CP.STORE._metadata(os.fstat(fd)) == CP.STORE._metadata(info) ==
                CP.STORE._metadata(os.stat(name, dir_fd=parent, follow_symlinks=False)) and
                _mount(fd) == _mount(parent), 'partial restore record changed or mounted')
    except BaseException as error:
        primary = error
    try:
        os.close(fd)
    except BaseException as error:
        errors.append(dict(resource='partial:' + name, error=repr(error)))
    if errors:
        raise ReadError(primary, errors) from primary
    if primary is not None:
        raise primary


class TransactionError(RuntimeError):
    def __init__(self, receipt, primary):
        self.receipt, self.primary = copy.deepcopy(receipt), primary
        self.receipt['primary_error'] = repr(primary)
        if hasattr(primary, 'receipt'):
            self.receipt['nested_error'] = copy.deepcopy(primary.receipt)
            self.receipt['cleanup_uncertain'] |= bool(primary.receipt.get('cleanup_uncertain'))
        super().__init__(f"{receipt['phase']}: {primary}")


class Tree:
    """Rename-aware actual readonly descriptors, exact bytes and disk EX.

    Only the root named edge can move; child names/inodes remain fixed. Metadata
    snapshots for directory timestamps are intentionally refreshed after rename;
    file metadata and bytes must remain exact throughout the transaction.
    """
    def __init__(self, owner, parent, name, payload):
        self.owner, self.parent, self.name, self.payload = owner, parent, name, payload
        self.fds, self.dirs, self.files, self.observation, self.edges, self.hashes = [], {}, {}, None, [], {}
        if _maybe(parent, name) is None:
            return
        self.fd = self._open(parent, name, True)
        info = os.fstat(self.fd)
        _directory(info, os.fstat(owner.custody.context.guest_fd).st_dev,
                   gid=owner.entry['gid'] if payload else 0, mode=0o710 if payload else 0o700)
        self._walk(self.fd, '', 0)
        self.observation = dict(inode=_inode(info), directories={}, files={})
        self.refresh()

    def _open(self, parent, name, directory=False):
        fd = os.open(name, DIR_FLAGS if directory else FILE_FLAGS, dir_fd=parent)
        self.owner.fd(fd, 'tree:' + name)
        self.fds.append(fd)
        self.edges.append([fd, parent, name, _inode(os.fstat(fd))])
        require(_mount(fd) == _mount(parent), 'restore tree crosses mount boundary')
        return fd

    def _walk(self, fd, prefix, depth):
        require(depth <= 1, 'restore tree too deep')
        names = _names(fd)
        self.dirs[prefix[:-1]] = (fd, names)
        for name in sorted(names):
            require(CP.STORE._name(name) == name, 'unsafe restore leaf')
            path = prefix + name
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                require(depth == 0 and name in (('.checkpoints',) if self.payload else ('runs', 'transitions')),
                        'unexpected restore subdirectory')
                _directory(info, os.fstat(fd).st_dev)
                self._walk(self._open(fd, name, True), path + '/', depth + 1)
                continue
            disk = self.payload and path == 'disk.raw'
            boot = self.payload and path in ('Image', 'initramfs.cpio.gz')
            if self.payload:
                require((depth == 0 and name in set(CUSTODY.ENROLL.FILENAMES.values()) | {'enrollment.json'}) or
                        (depth == 1 and CUSTODY.SENDER.LEGACY.BACKUP.NAME_RE.fullmatch(name)),
                        'unexpected restore payload')
            else:
                require((depth == 0 and name == 'head.json') or
                        (depth == 1 and CUSTODY.FORMAT.TRANSITION_RE.fullmatch(path)) or
                        (depth == 1 and path.startswith('runs/') and
                         re.fullmatch(r'[a-zA-Z0-9.-]+', name)), 'unexpected lifecycle path')
            limit = (CUSTODY.POLICY.MANIFEST.CONFIG.DISK_LIMIT if disk else
                     CUSTODY.POLICY.MANIFEST.CONFIG.ARTIFACT_LIMIT if boot else SCHEMA.FILE_LIMIT)
            mode, uid, gid = ((0o600, self.owner.entry['uid'], self.owner.entry['gid']) if disk else
                              (0o444 if boot else 0o400, 0, 0))
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_dev == os.fstat(fd).st_dev and
                    (stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid) == (mode, uid, gid) and
                    0 <= info.st_size <= limit, 'unsafe restore file')
            opened = self._open(fd, name)
            require(CP.STORE._metadata(info) == CP.STORE._metadata(os.fstat(opened)), 'restore file replaced')
            if disk:
                fcntl.flock(opened, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.disk_fd = opened
            self.files[path] = (opened, fd, name, info)
            require(len(self.files) <= SCHEMA.COUNT, 'restore tree file bound exceeded')
        require(sum(info.st_size for path, (_, _, _, info) in self.files.items()
                    if path != 'disk.raw') <= CUSTODY.FORMAT.LIFECYCLE_LIMIT +
                CUSTODY.FORMAT.HISTORY_LIMIT + 2 * CUSTODY.POLICY.MANIFEST.CONFIG.ARTIFACT_LIMIT,
                'restore tree aggregate bound exceeded')

    @staticmethod
    def metadata(info):
        return dict(inode=_inode(info), uid=info.st_uid, gid=info.st_gid, mode=stat.S_IMODE(info.st_mode))

    def refresh(self):
        if self.observation is None:
            require(_maybe(self.parent, self.name) is None, 'absent restore object appeared')
            return None
        require(_inode(os.stat(self.name, dir_fd=self.parent, follow_symlinks=False)) ==
                self.observation['inode'] == _inode(os.fstat(self.fd)), 'restore tree moved unexpectedly')
        for fd, parent, name, identity in self.edges:
            require(_inode(os.stat(name, dir_fd=parent, follow_symlinks=False)) == identity ==
                    _inode(os.fstat(fd)), 'restore child binding changed')
        result = dict(inode=self.observation['inode'], directories={}, files={})
        for path, (fd, names) in self.dirs.items():
            require(_names(fd) == names, 'restore tree names changed')
            info = os.fstat(fd)
            _directory(info, os.fstat(self.owner.custody.context.guest_fd).st_dev,
                       gid=self.owner.entry['gid'] if self.payload and path == '' else 0,
                       mode=0o710 if self.payload and path == '' else 0o700)
            result['directories'][path] = self.metadata(info)
        for path, (fd, parent, name, before) in self.files.items():
            require(CP.STORE._metadata(before) == CP.STORE._metadata(os.fstat(fd)) ==
                    CP.STORE._metadata(os.stat(name, dir_fd=parent, follow_symlinks=False)),
                    'restore file metadata changed')
            if path == 'disk.raw':
                CUSTODY.POLICY._disk_flock(fd, os.getpid())
            # A retained readonly FD and exact named metadata bind the actual
            # bytes already hashed in this process. Directory rename changes
            # directory ctime, never child file metadata. Every explicit resume
            # constructs fresh Trees and hashes all observed files again.
            if path not in self.hashes:
                self.hashes[path] = self.owner.hash(fd, before.st_size, path)
            require(CP.STORE._metadata(before) == CP.STORE._metadata(os.fstat(fd)) ==
                    CP.STORE._metadata(os.stat(name, dir_fd=parent, follow_symlinks=False)),
                    'restore file changed during byte authentication')
            result['files'][path] = dict(**self.metadata(before), **self.hashes[path])
        if self.observation['files']:
            require(result == self.observation, 'restore tree bytes changed')
        self.observation = result
        return copy.deepcopy(result)

    def sync(self):
        self.refresh()
        for fd, _, _, _ in self.files.values():
            os.fsync(fd)
        for fd, _ in reversed(list(self.dirs.values())):
            os.fsync(fd)
        os.fsync(self.parent)
        self.refresh()

    def relocate(self, parent, name):
        require(self.observation is not None, 'cannot relocate absent tree')
        self.parent, self.name = parent, name
        self.edges[0][1:3] = [parent, name]
        self.refresh()


class RestoreTransaction(CUSTODY.Resources):
    def __init__(self, custody, source, transaction_id=None, resume=False):
        super().__init__()
        require(type(custody) is CUSTODY.ShutdownRestoreCustody, 'actual canonical restore custody required')
        custody.recheck()
        require(type(source) is SOURCE.Source, 'actual canonical authenticated Source required')
        source.recheck()
        require(type(resume) is bool, 'explicit resume boolean required')
        require(not resume or transaction_id is not None, 'explicit resume UUID required')
        self.custody, self.source, self.resume = custody, source, resume
        self.transaction_id = SCHEMA.transaction_id(transaction_id or str(uuid.uuid4()))
        self.entry = copy.deepcopy(custody.entry)
        self.receipt.update(transaction_id=self.transaction_id, data_committed=False,
                            fresh_boot_required=True, phase='validate-source')
        self.trees, self.intent, self.fixed_edges = {}, None, {}

    def recheck(self):
        self.creator()
        require(not self._closed, 'restore transaction closed')
        self.custody.recheck()
        self.source.recheck()
        allowed = {os.fstat(self.custody.context.state_fd).st_dev, os.fstat(self.custody.context.guest_fd).st_dev}
        require(os.fstat(self.source.held.fd).st_dev in allowed, 'archive must reside on actual STATE or p5')
        for fd, (parent, name, metadata) in self.fixed_edges.items():
            require(Tree.metadata(os.fstat(fd)) == metadata == Tree.metadata(
                os.stat(name, dir_fd=parent, follow_symlinks=False)), 'restore fixed directory binding changed')
        if hasattr(self, 'lifecycle_parent') and hasattr(self, 'parent_observation'):
            parent = self.parent_observation
            canonical = _maybe(self.custody.context.guest_fd, '.a733-lifecycle')
            staged = _maybe(self.p5, 'new-lifecycle-parent')
            if parent['old_inode'] is not None:
                require(canonical is not None and _inode(canonical) == parent['old_inode'] and staged is None,
                        'shared lifecycle parent binding changed')
            else:
                require((canonical is None) != (staged is None) and
                        _inode(canonical if canonical is not None else staged) == parent['new_inode'],
                        'new shared lifecycle parent binding changed')
            require(_inode(os.fstat(self.lifecycle_parent)) == (parent['old_inode'] or parent['new_inode']),
                    'retained shared lifecycle parent changed')
        if hasattr(self, 'lock'):

            require(type(self.lock) is ADMISSION.GuestLock and self.lock.lease is self.custody.lease and
                    self.lock.name == self.custody.name, 'actual target guest exclusion required')
            self.lock.recheck()
            ADMISSION._held_lock(self.lock.fd, 'WRITE', os.getpid())

    def hash(self, fd, size, name):
        import hashlib
        digest, offset = hashlib.sha256(), 0
        progress = self.custody.progress
        if progress:
            progress(dict(name=self.custody.name, file=name, bytes=0, total=size))
        while offset < size:
            data = os.pread(fd, min(SOURCE.CHUNK, size - offset), offset)
            require(data, 'restore input truncated')
            digest.update(data)
            offset += len(data)
            if progress:
                progress(dict(name=self.custody.name, file=name, bytes=offset, total=size))
        require(not os.pread(fd, 1, offset), 'restore input grew')
        return dict(bytes=offset, sha256=digest.hexdigest())

    def directory(self, parent, name, create=False, gid=0, mode=0o700):
        self.recheck()
        before = _maybe(parent, name)
        if before is None:
            require(create, 'missing restore directory: ' + name)
            os.mkdir(name, 0o700, dir_fd=parent)
            fd = self.fd(os.open(name, DIR_FLAGS, dir_fd=parent), 'directory:' + name)
            os.fchown(fd, 0, gid)
            os.fchmod(fd, mode)
            os.fsync(fd)
            os.fsync(parent)
        else:
            _directory(before, os.fstat(parent).st_dev, gid=gid, mode=mode)
            fd = self.fd(os.open(name, DIR_FLAGS, dir_fd=parent), 'directory:' + name)
            require(_inode(os.fstat(fd)) == _inode(before), 'restore directory replaced')
        _directory(os.fstat(fd), os.fstat(parent).st_dev, gid=gid, mode=mode)
        require(_mount(fd) == _mount(parent), 'restore directory mount boundary')
        self.fixed_edges[fd] = (parent, name, Tree.metadata(os.fstat(fd)))
        return fd

    def record(self, name, raw):
        """Publish one immutable record, never overwrite or retry uncertain IO."""
        self.recheck()
        require(SCHEMA.decode(raw)['transaction_id'] == self.transaction_id, 'record transaction differs')
        if _maybe(self.state, name) is not None:
            def matching(data):
                SCHEMA.decode(data)
                require(data == raw, 'conflicting immutable restore record')
            _read(self.state, name, validate=matching)
            fd = self.fd(os.open(name, FILE_FLAGS, dir_fd=self.state), 'record:' + name)
            os.fsync(fd)
            os.fsync(self.state)
            _read(self.state, name, validate=matching)
            return
        temporary = '.pending-' + str(uuid.uuid4())
        fd = self.fd(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW |
                             os.O_CLOEXEC, 0o600, dir_fd=self.state), 'record-write:' + name)
        _write(fd, raw)
        os.fchmod(fd, 0o400)
        os.fsync(fd)
        _rename(self.state, temporary, self.state, name)
        os.fsync(self.state)

    def _source_identity(self):
        source, target = self.source, self.entry
        config, cohort = source.manifest['config'], source.cohort
        require(all(config[key] == target[key] == cohort['entry'][key]
                    for key in ('name', 'uid', 'gid')) and config['name'] == self.custody.name,
                'same existing destination identity required')
        require(all(target[key] == cohort['entry'][key] for key in ('fixture_profile', 'shutdown_profile')),
                'restore profiles differ')
        require(cohort['registry']['storage'] == self.custody.expected_policy['storage'] and
                source.manifest['guest_uuid'] == self.custody.expected_policy['storage']['guest_uuid'],
                'same physical storage identity required; rebasing unsupported')
        require(len(source.history_data) <= 16 * 1024 * 1024 and
                len(source.lifecycle_data) <= 40 * 1024 * 1024 and
                all(len(raw) <= SCHEMA.FILE_LIMIT for raw in cohort['records'].values()),
                'restore historical metadata bound exceeded')
        return dict(archive=copy.deepcopy(source.archive_record), manifest_sha256=source.manifest_sha256,
                    registry_generation=source.cohort['registry']['generation'],
                    history=SCHEMA.reference(source.history_data), lifecycle=SCHEMA.reference(source.lifecycle_data))

    def _file(self, parent, name, raw, uid=0, gid=0, mode=0o400):
        fd = self.fd(os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                             0o600, dir_fd=parent), 'stage-write:' + name)
        _write(fd, raw)
        os.fchown(fd, uid, gid)
        os.fchmod(fd, mode)
        os.fsync(fd)
        # Close writing descriptions before consumer census and disk exclusion.
        self._close_fd(fd)

    def _close_fd(self, fd):
        self.fixed_edges.pop(fd, None)
        # Remove before close: never retry a descriptor after an uncertain close.
        for index, (_name, callback) in enumerate(self._resources):
            if getattr(callback, '_restore_fd', None) == fd:
                self._resources.pop(index)
                break
        try:
            os.close(fd)
        except BaseException as error:
            self.receipt['close_errors'].append(dict(resource='stage-writer', error=repr(error)))
            self.receipt['cleanup_uncertain'] = True
            raise

    def fd(self, fd, name):
        def close():
            os.close(fd)
        close._restore_fd = fd
        self._resources.append((name, close))
        return fd

    def _stage(self):
        payload = self.directory(self.p5, 'new-payload', create=True, gid=self.entry['gid'], mode=0o710)
        life = self.directory(self.p5, 'new-lifecycle', create=True)
        require(not _names(payload) and not _names(life), 'new stage already populated')
        self._file(payload, 'enrollment.json', self.source.manifest_data)
        history = self.directory(payload, '.checkpoints', create=True)
        for name, raw in self.source.history.items():
            self._file(history, name, raw)
        self._file(life, 'head.json', self.source.cohort['head_data'])
        subdirs = {name: self.directory(life, name, create=True) for name in ('runs', 'transitions')}
        for path, raw in self.source.cohort['records'].items():
            parent, name = path.split('/')
            self._file(subdirs[parent], name, raw)
        streams, totals = {}, {}
        for name in SOURCE.FORMAT.NAMES[3:]:
            streams[name] = self.fd(os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW |
                                           os.O_CLOEXEC, 0o600, dir_fd=payload), 'payload-write:' + name)
            totals[name] = 0
        def emit(name, data):
            _write(streams[name], data)
            totals[name] += len(data)
            if self.custody.progress:
                self.custody.progress(dict(name=self.custody.name, file=name, bytes=totals[name],
                    total=self.source.manifest['files'][name]['bytes']))
        self.source.scan(emit)
        for name, fd in streams.items():
            disk = name == 'disk.raw'
            os.fchown(fd, self.entry['uid'] if disk else 0, self.entry['gid'] if disk else 0)
            os.fchmod(fd, 0o600 if disk else 0o444)
            os.fsync(fd)
            self._close_fd(fd)
        for fd in (history, *subdirs.values(), payload, life, self.p5):
            os.fsync(fd)
        for fd in (history, *subdirs.values(), payload, life):
            self._close_fd(fd)
        return dict(payload=Tree(self, self.p5, 'new-payload', True),
                    lifecycle=Tree(self, self.p5, 'new-lifecycle', False))

    def _census(self):
        fds = [fd for tree in self.trees.values() for fd in tree.fds]
        # The staged source may itself live inside a retained old directory.
        fds.append(self.source.held.fd)
        CUSTODY.consumer_check(self.entry['uid'], [tuple(_inode(os.fstat(fd))) for fd in fds], retained_fds=fds)

    def _registry(self):
        return self.custody.registry()

    def _new_registry(self, old, old_raw):
        value = copy.deepcopy(old)
        value['generation'] = max(old['generation'], self.source.cohort['registry']['generation']) + 1
        require(value['generation'] <= CUSTODY.REGISTRY.MAX_GENERATION, 'registry generation exhausted')
        target = self.custody._entry(value, self.custody.name)
        require(target == self.entry, 'target registry changed')
        for entry in value['guests']:
            if entry['name'] == self.custody.name:
                entry['manifest_sha256'] = self.source.manifest_sha256
        raw = CUSTODY.REGISTRY.canonical(value)
        CUSTODY.REGISTRY.validate_registry(raw)
        return raw

    def _verify_new(self):
        """Actual bytes/metadata under retained guest/disk locks, pure replay last."""
        self.recheck()
        payload, life = self.trees['new-payload'], self.trees['new-lifecycle']
        for tree in (payload, life):
            tree.refresh()
        expected = {name: raw for name, raw in self.source.history.items()}
        raw_expected = {'enrollment.json': self.source.manifest_data,
                        **{'.checkpoints/' + name: raw for name, raw in expected.items()}}
        actual = payload.observation['files']
        require(set(actual) == set(raw_expected) | set(SOURCE.FORMAT.NAMES[3:]), 'new payload members differ')
        for name, raw in raw_expected.items():
            require({k: actual[name][k] for k in ('bytes', 'sha256')} == SCHEMA.reference(raw),
                    'new historical payload differs')
        for name in SOURCE.FORMAT.NAMES[3:]:
            require({k: actual[name][k] for k in ('bytes', 'sha256')} == {k: self.source.manifest['files'][name][k] for k in ('bytes', 'sha256')}, 'new payload bytes differ')
        expected_life = {'head.json': self.source.cohort['head_data'], **self.source.cohort['records']}
        require(set(life.observation['files']) == set(expected_life), 'new lifecycle members differ')
        for name, raw in expected_life.items():
            require({k: life.observation['files'][name][k] for k in ('bytes', 'sha256')} == SCHEMA.reference(raw),
                    'new historical lifecycle differs')
        # Source was independently authenticated; exact actual historical bytes
        # above preserve completion semantics without altering old proof records.
        CUSTODY.ARCHIVE.validate_cohort(self.source.manifest_data, self.source.lifecycle_data)
        storage = CUSTODY.POLICY.STORAGE.verify_guest_storage(payload.disk_fd,
            boot_fds=tuple(payload.files[name][0] for name in ('Image', 'initramfs.cpio.gz')))
        require(CUSTODY.POLICY.semantic(storage) == CUSTODY.POLICY.semantic(
            CUSTODY.POLICY.STORAGE.verify_guest_directory(self.custody.context.guest_fd)),
            'restored payload physical placement differs')
        CUSTODY.ENROLL.probe_uuid(storage, self.source.manifest['guest_uuid'])
        CUSTODY.POLICY.service_state(self.custody.name)
        config = self.source.manifest['config']
        CUSTODY.POLICY.ACCOUNTS.validate_accounts({'schema': 1, 'guests': [
            {key: config[key] for key in ('name', 'uid', 'gid')}]}, CUSTODY.POLICY.ACCOUNTS.snapshot(), complete=False)
        try:
            self.custody.admission.is_managed(self.custody.name, self.custody.lease, self.lock)
        except FileNotFoundError as error:
            require(error.filename == 'managed', 'unexpected missing managed metadata')
        self._census()
        self.recheck()

    def _phase_record(self, phase):
        return dict(schema=1, profile=SCHEMA.PROFILE, transaction_id=self.transaction_id,
                    kind='data_commit' if phase == 6 else 'phase',
                    intent_sha256=SCHEMA.digest(self.intent_raw), phase=phase)

    def _ack(self, phase):
        self.record('data-commit.json' if phase == 6 else f'phase-{phase}.json',
                    SCHEMA.canonical(self._phase_record(phase)))
        self.receipt['phase'] = 'data-commit' if phase == 6 else f'phase-{phase}'
        if phase == 6:
            self.receipt['data_committed'] = True

    def _move(self, tree, parent, name):
        self.recheck()
        tree.refresh()
        self._census()
        oldparent, oldname = tree.parent, tree.name
        _rename(oldparent, oldname, parent, name)
        tree.relocate(parent, name)
        # File/dir/parents durability is re-established on explicit resume too.
        tree.sync()
        os.fsync(oldparent)
        os.fsync(parent)

    def _publish_registry(self):
        self.recheck()
        # Every disk EX is already held; registry is always last.
        with self.custody.admission.registry_lock(self.custody.lease) as lock:
            def guard():
                self.recheck()
                lock.recheck()
                ADMISSION._held_lock(lock.fd, 'WRITE', os.getpid())
            with CP.STORE.RootDirectory(self.custody.context.state_fd, 'guests', guard=guard) as store:
                actual = store.read('autostart.json', CUSTODY.REGISTRY.LIMIT)
                old, new = (SCHEMA.registry_bytes(self.intent[key]) for key in ('old_registry', 'new_registry'))
                require(actual in (old, new), 'restore registry CAS conflict')
                if actual == old:
                    store.publish('autostart.json', new, expected_sha256=SCHEMA.digest(old))
                else:
                    store.sync('autostart.json', pin=SCHEMA.digest(new), limit=CUSTODY.REGISTRY.LIMIT)
                require(store.read('autostart.json', CUSTODY.REGISTRY.LIMIT) == new, 'final registry differs')

    def _prepare(self):
        self.recheck()
        pins = self._source_identity()
        if not self.resume:
            census(self.custody.controller, self.custody.recheck)
        raw, registry = self._registry()
        # Exhaustion and identity refusals precede even private stage creation.
        self.lock = self.retain(self.custody.admission.guest_lock(
            self.custody.name, self.custody.lease, create=True), 'target-guest-lock')
        self.state_parent = self.directory(self.custody.context.state_fd, STATE_NAME, create=not self.resume)
        self.p5_parent = self.directory(self.custody.context.guest_fd, P5_NAME, create=not self.resume)
        if not self.resume:
            require(_maybe(self.state_parent, self.transaction_id) is None and
                    _maybe(self.p5_parent, self.transaction_id) is None, 'transaction UUID already exists')
        self.state = self.directory(self.state_parent, self.transaction_id, create=not self.resume)
        self.p5 = self.directory(self.p5_parent, self.transaction_id, create=not self.resume)
        if self.resume:
            self.intent_raw = _read(self.state, 'intent.json', validate=SCHEMA.decode)
            self.intent = SCHEMA.decode(self.intent_raw)
            require(self.intent['kind'] == 'intent' and self.intent['transaction_id'] == self.transaction_id and
                    self.intent['source'] == pins and self.intent['storage'] == self.custody.expected_policy['storage'] and
                    self.intent['name'] == self.custody.name and
                    _inode(os.fstat(self.state)) == self.intent['state_inode'] and
                    _inode(os.fstat(self.p5)) == self.intent['p5_inode'], 'restore intent identity/source differs')
            # Completed repeats inspect provenance only; later guest bytes and
            # registry mutations are legitimate and must never be replayed.
            if _maybe(self.state, 'data-commit.json') is not None:
                _provenance(self.state, self.intent_raw)
                self.receipt.update(data_committed=True, phase='historical-data-commit', historical=True)
                return 6
            require(self.entry == self.intent['entry'] or
                    self.entry == dict(self.intent['entry'], manifest_sha256=self.source.manifest_sha256),
                    'resume target identity differs')
            self.entry = copy.deepcopy(self.intent['entry'])
            old_registry_raw = SCHEMA.registry_bytes(self.intent['old_registry'])
            old_registry = CUSTODY.REGISTRY.validate_registry(old_registry_raw)
            require(CUSTODY.REGISTRY.canonical(old_registry) == old_registry_raw and
                    self.custody._entry(old_registry, self.custody.name) == self.entry and
                    self._new_registry(old_registry, old_registry_raw) ==
                    SCHEMA.registry_bytes(self.intent['new_registry']), 'resume registry derivation differs')
            require(raw in (SCHEMA.registry_bytes(self.intent['old_registry']),
                            SCHEMA.registry_bytes(self.intent['new_registry'])), 'resume registry changed')
            return self._resume_trees(raw)
        new_raw = self._new_registry(registry, raw)
        parent_info = _maybe(self.custody.context.guest_fd, '.a733-lifecycle')
        if parent_info is None:
            self.lifecycle_parent = self.directory(self.p5, 'new-lifecycle-parent', create=True)
            parent_observation = dict(old_inode=None, new_inode=_inode(os.fstat(self.lifecycle_parent)))
        else:
            self.lifecycle_parent = self.directory(self.custody.context.guest_fd, '.a733-lifecycle')
            parent_observation = dict(old_inode=_inode(os.fstat(self.lifecycle_parent)), new_inode=None)
        self.parent_observation = parent_observation
        old = dict(payload=Tree(self, self.custody.context.guest_fd, self.custody.name, True),
                   lifecycle=Tree(self, self.lifecycle_parent, self.custody.name, False))
        self.trees.update({'old-' + key: tree for key, tree in old.items()})
        new = self._stage()
        self.trees.update({'new-' + key: tree for key, tree in new.items()})
        self._verify_new()
        for tree in self.trees.values():
            if tree.observation is not None:
                tree.sync()
        observed_raw, observed_registry = self._registry()
        require(observed_raw == raw and observed_registry == registry, 'registry changed while staging')
        self.intent = dict(schema=1, profile=SCHEMA.PROFILE, kind='intent', transaction_id=self.transaction_id,
            storage=copy.deepcopy(self.custody.expected_policy['storage']), name=self.custody.name,
            entry=copy.deepcopy(self.entry), source=pins, old_registry=SCHEMA.registry_reference(raw),
            new_registry=SCHEMA.registry_reference(new_raw), old={key: tree.refresh() for key, tree in old.items()},
            new={key: tree.refresh() for key, tree in new.items()}, p5_inode=_inode(os.fstat(self.p5)),
            state_inode=_inode(os.fstat(self.state)), lifecycle_parent=parent_observation)
        self.intent_raw = SCHEMA.canonical(self.intent)
        SCHEMA.decode(self.intent_raw)
        require((_maybe(self.custody.context.guest_fd, '.a733-lifecycle') is None) ==
                (parent_observation['old_inode'] is None), 'shared lifecycle parent changed')
        self.record('intent.json', self.intent_raw)
        self.receipt['phase'] = 'intent-durable'
        return 0

    def _resume_trees(self, registry_raw):
        expected_parent = self.intent['lifecycle_parent']
        canonical = _maybe(self.custody.context.guest_fd, '.a733-lifecycle')
        staged = _maybe(self.p5, 'new-lifecycle-parent')
        parent_published = False
        if expected_parent['old_inode'] is not None:
            require(canonical is not None and _inode(canonical) == expected_parent['old_inode'] and staged is None,
                    'shared lifecycle parent changed')
            self.lifecycle_parent = self.directory(self.custody.context.guest_fd, '.a733-lifecycle')
        else:
            require((canonical is None) != (staged is None), 'missing/duplicate shared lifecycle parent')
            if canonical is None:
                require(_inode(staged) == expected_parent['new_inode'], 'staged shared parent changed')
                self.lifecycle_parent = self.directory(self.p5, 'new-lifecycle-parent')
                require(not _names(self.lifecycle_parent), 'staged shared parent is not empty')
            else:
                require(_inode(canonical) == expected_parent['new_inode'], 'foreign shared lifecycle parent')
                self.lifecycle_parent = self.directory(self.custody.context.guest_fd, '.a733-lifecycle')
                require(_names(self.lifecycle_parent) <= {self.custody.name}, 'unexpected shared parent guest')
                parent_published = True
        self.parent_observation = expected_parent
        state = {}
        for key, parent, payload in (('payload', self.custody.context.guest_fd, True),
                                     ('lifecycle', self.lifecycle_parent, False)):
            expected_old, expected_new = self.intent['old'][key], self.intent['new'][key]
            locations = {'canonical': (parent, self.custody.name),
                         'old': (self.p5, 'old-' + key), 'new': (self.p5, 'new-' + key)}
            found = {label: _maybe(fd, name) for label, (fd, name) in locations.items()}
            expected_inodes = {tuple(item['inode']) for item in (expected_old, expected_new) if item is not None}
            require(all(item is None or tuple(_inode(item)) in expected_inodes for item in found.values()),
                    'unknown resume object inode')
            old_at, new_at = [], []
            for label, info in found.items():
                if info is None:
                    continue
                if expected_old is not None and _inode(info) == expected_old['inode']:
                    old_at.append(label)
                if _inode(info) == expected_new['inode']:
                    new_at.append(label)
            require(len(new_at) == 1 and (len(old_at) == 1 if expected_old is not None else not old_at),
                    'missing/duplicate resume objects')
            require(new_at[0] in ('canonical', 'new') and (not old_at or old_at[0] in ('canonical', 'old')),
                    'impossible resume object locations')
            require(not (old_at == ['canonical'] and new_at == ['canonical']), 'duplicate canonical restore objects')
            state[key] = 0 if old_at == ['canonical'] or (expected_old is None and new_at == ['new']) else (
                1 if new_at == ['new'] else 2)
            for side, at, expected in (('old', old_at, expected_old), ('new', new_at, expected_new)):
                if at:
                    fd, name = locations[at[0]]
                    tree = Tree(self, fd, name, payload)
                    require(tree.refresh() == expected, 'stale restore bytes or metadata')
                else:
                    # Old absence is data; no disk descriptor or lock is forged.
                    tree = Tree(self, self.p5, 'old-' + key, payload)
                    require(tree.observation is None, 'unexpected retained original')
                self.trees[side + '-' + key] = tree
        oldraw, newraw = (SCHEMA.registry_bytes(self.intent[key]) for key in ('old_registry', 'new_registry'))
        require(registry_raw in (oldraw, newraw), 'resume registry differs')
        candidates = []
        for phase, p, l, r in ((0, 0, 0, oldraw), (1, 1, 0, oldraw), (2, 2, 0, oldraw),
                               (3, 2, 1, oldraw), (4, 2, 2, oldraw), (5, 2, 2, newraw)):
            def matches(key, observed, expected):
                return observed == expected or (self.intent['old'][key] is None and observed == 0 and expected == 1)
            if matches('payload', state['payload'], p) and matches('lifecycle', state['lifecycle'], l) and registry_raw == r:
                candidates.append(phase)
        ack = _provenance(self.state, self.intent_raw, incomplete=True)
        candidates = [phase for phase in candidates if ack <= phase <= ack + 1 and
                      (not parent_published or phase >= 3) and
                      (self.intent['lifecycle_parent']['new_inode'] is None or parent_published or phase <= 2)]
        require(candidates, 'impossible cross-component restore phase')
        phase = max(candidates)
        # A visible intent/ack can survive a failed publication syscall before
        # its parent fsync. Re-authenticate and establish STATE durability before
        # any further canonical mutation or acknowledgement. Completed repeats
        # never enter this path.
        self.record('intent.json', self.intent_raw)
        for durable_phase in range(1, ack + 1):
            self.record(f'phase-{durable_phase}.json', SCHEMA.canonical(self._phase_record(durable_phase)))
        # Sync every exact observed object before acknowledging an operation
        # that might have reached rename but failed before directory fsync.
        for tree in self.trees.values():
            if tree.observation is not None:
                tree.sync()
        self._census()
        self._verify_new()
        if parent_published:
            os.fsync(self.lifecycle_parent)
            os.fsync(self.p5)
            os.fsync(self.custody.context.guest_fd)
        if phase > ack:
            self._ack(phase)
        return phase

    def _publish_parent(self):
        observation = self.intent['lifecycle_parent']
        if observation['new_inode'] is None:
            return
        require(_maybe(self.custody.context.guest_fd, '.a733-lifecycle') is None and
                _inode(os.fstat(self.lifecycle_parent)) == observation['new_inode'] and
                _inode(os.stat('new-lifecycle-parent', dir_fd=self.p5, follow_symlinks=False)) ==
                observation['new_inode'] and not _names(self.lifecycle_parent), 'shared parent publication conflict')
        _rename(self.p5, 'new-lifecycle-parent', self.custody.context.guest_fd, '.a733-lifecycle')
        self.fixed_edges[self.lifecycle_parent] = (self.custody.context.guest_fd, '.a733-lifecycle',
                                                  Tree.metadata(os.fstat(self.lifecycle_parent)))
        os.fsync(self.lifecycle_parent)
        os.fsync(self.p5)
        os.fsync(self.custody.context.guest_fd)

    def execute(self):
        primary = None
        try:
            phase = self._prepare()
            if phase == 6:
                pass
            else:
                for step in range(phase + 1, 7):
                    self.recheck()
                    if step in (1, 3):
                        if step == 3:
                            self._publish_parent()
                        key = 'payload' if step == 1 else 'lifecycle'
                        tree = self.trees['old-' + key]
                        if tree.observation is not None:
                            self._move(tree, self.p5, 'old-' + key)
                    elif step in (2, 4):
                        key = 'payload' if step == 2 else 'lifecycle'
                        parent = self.custody.context.guest_fd if step == 2 else self.lifecycle_parent
                        self._move(self.trees['new-' + key], parent, self.custody.name)
                    elif step == 5:
                        self._verify_new()
                        self._publish_registry()
                    else:
                        self._verify_new()
                        require(self._registry()[0] == SCHEMA.registry_bytes(self.intent['new_registry']),
                                'registry changed before data commit')
                        for tree in self.trees.values():
                            if tree.observation is not None:
                                tree.sync()
                    self._ack(step)
        except BaseException as error:
            primary = error
        try:
            self.close()
        except BaseException as error:
            primary = primary or error
        if primary is not None:
            raise TransactionError(self.receipt, primary) from primary
        self.receipt['success'] = True
        return copy.deepcopy(self.receipt)


def _provenance(state, intent_raw, incomplete=False):
    intent = SCHEMA.decode(intent_raw)
    require(intent['kind'] == 'intent', 'restore intent required')
    names = _names(state)
    phases = []
    for name in names:
        if name.startswith('.pending-'):
            # Temporary records never authorize a phase. Their metadata is still
            # bounded and safe; retain for manual inspection, never repair.
            _pending(state, name)
            continue
        if name == 'intent.json':
            continue
        require(name in {f'phase-{n}.json' for n in range(1, 6)} | {'data-commit.json'},
                'unexpected restore provenance file')
        raw = _read(state, name, validate=SCHEMA.decode)
        value = SCHEMA.decode(raw)
        phase = 6 if name == 'data-commit.json' else int(name[6])
        require(value == dict(schema=1, profile=SCHEMA.PROFILE, transaction_id=intent['transaction_id'],
                              kind='data_commit' if phase == 6 else 'phase',
                              intent_sha256=SCHEMA.digest(intent_raw), phase=phase),
                'restore provenance binding differs')
        phases.append(phase)
    require(sorted(phases) == list(range(1, max(phases, default=0) + 1)), 'restore phase records have a gap')
    if not incomplete:
        require(6 in phases, 'incomplete managed restore requires explicit resume')
    return max(phases, default=0)




def _release(opened, fd, errors):
    primary = sys.exc_info()[1]
    opened.remove(fd)
    try:
        os.close(fd)
    except BaseException as error:
        item = dict(resource='census-local', error=repr(error))
        errors.append(item)
        raise ReadError(primary, [item]) from primary


def _private_tree(fd, opened, depth=0, budget=None, close_errors=None):
    """Metadata-only bounded validation, never historical current-byte replay."""
    budget = [0] if budget is None else budget
    close_errors = [] if close_errors is None else close_errors
    require(depth <= 2, 'private restore tree too deep')
    for name in _names(fd):
        CP.STORE._name(name)
        budget[0] += 1
        require(budget[0] <= SCHEMA.COUNT * 4, 'private restore metadata bound exceeded')
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        require(info.st_dev == os.fstat(fd).st_dev, 'private restore device differs')
        if stat.S_ISDIR(info.st_mode):
            payload = depth == 0 and name in ('old-payload', 'new-payload')
            require(depth < 2 and info.st_uid == 0 and
                    stat.S_IMODE(info.st_mode) == (0o710 if payload else 0o700) and
                    (60000 <= info.st_gid <= 60999 if payload else info.st_gid == 0),
                    'unsafe private restore directory')
            child = os.open(name, DIR_FLAGS, dir_fd=fd)
            opened.append(child)
            require(_inode(os.fstat(child)) == _inode(info) and _mount(child) == _mount(fd),
                    'private restore directory changed/mounted')
            try:
                _private_tree(child, opened, depth + 1, budget, close_errors)
            finally:
                _release(opened, child, close_errors)
        else:
            disk = name == 'disk.raw' and depth == 1
            boot = name in ('Image', 'initramfs.cpio.gz') and depth == 1
            limit = (CUSTODY.POLICY.MANIFEST.CONFIG.DISK_LIMIT if disk else
                     CUSTODY.POLICY.MANIFEST.CONFIG.ARTIFACT_LIMIT if boot else SCHEMA.FILE_LIMIT)
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and
                    0 <= info.st_size <= limit and
                    (stat.S_IMODE(info.st_mode) == 0o600 and info.st_uid == info.st_gid and
                     (info.st_uid == 0 or 60000 <= info.st_uid <= 60999) if disk else
                     info.st_uid == info.st_gid == 0 and stat.S_IMODE(info.st_mode) in (0o400, 0o444, 0o600)),
                    'unsafe private restore file')
            child = os.open(name, FILE_FLAGS, dir_fd=fd)
            opened.append(child)
            require(CP.STORE._metadata(os.fstat(child)) == CP.STORE._metadata(info) and
                    _mount(child) == _mount(fd), 'private restore file changed/mounted')
            _release(opened, child, close_errors)


def census(controller, guard):
    """Readonly pre-bootstrap census. Actual canonical EX/context required.

    Completed provenance remains historical across later legitimate guest runs.
    Harmless pre-intent stages are ignored after namespace metadata validation.
    No account, marker, RAM lock, STATE or p5 write occurs here.
    """
    require(type(controller) is CUSTODY.LIFECYCLE.StateController and
            type(controller.context) is CUSTODY.CONTEXT.HostContext, 'actual restore controller required')
    context = controller.context
    lease = context.lease
    require(type(lease) is ADMISSION.Lease and lease.exclusive, 'actual canonical EX lease required')
    require(callable(guard), 'physical caller guard required')
    def check():
        guard()
        context.guard()
        lease.recheck()
        ADMISSION._readonly(lease.fd)
        ADMISSION._held_lock(lease.fd, 'WRITE', os.getpid())
        require(os.getresuid() == (0, 0, 0), 'native root census required')
        CUSTODY.CONTEXT.host_identity(context.boot_id)
    opened, edges, observed, release_errors = [], [], [], []
    def directory(parent, name):
        info = _maybe(parent, name)
        if info is None:
            return None
        _directory(info, os.fstat(parent).st_dev)
        fd = os.open(name, DIR_FLAGS, dir_fd=parent)
        opened.append(fd)
        require(_inode(os.fstat(fd)) == _inode(info) and _mount(fd) == _mount(parent),
                'restore census directory replaced/mounted')
        edges.append((parent, name, fd, _inode(info)))
        observed.append((parent, name, Tree.metadata(info)))
        return fd
    result = dict(schema=1, success=True, completed=[], private_stages=[], transactions=[], hardware_acceptance=False)
    primary = None
    current_transaction = None
    try:
        check()
        state = directory(context.state_fd, STATE_NAME)
        p5 = directory(context.guest_fd, P5_NAME)
        state_names = set() if state is None else _names(state)
        p5_names = set() if p5 is None else _names(p5)
        previous, census_budget = [], [0]
        for name in sorted(state_names | p5_names):
            current_transaction = name
            for prior in previous:
                edges[:] = [edge for edge in edges if edge[2] != prior]
                _release(opened, prior, release_errors)
            previous = []
            check()
            SCHEMA.transaction_id(name)
            state_tx = directory(state, name) if name in state_names else None
            p5_tx = directory(p5, name) if name in p5_names else None
            previous = [fd for fd in (state_tx, p5_tx) if fd is not None]
            if p5_tx is not None:
                require(_names(p5_tx) <= {'old-payload', 'new-payload', 'old-lifecycle', 'new-lifecycle', 'new-lifecycle-parent'},
                        'unexpected private restore stage')
                _private_tree(p5_tx, opened, budget=census_budget, close_errors=release_errors)
                for leaf in _names(p5_tx):
                    # Payload root legitimately has root:guest 0710 metadata;
                    # its root-only parent excludes discovery and guest access.
                    info = os.stat(leaf, dir_fd=p5_tx, follow_symlinks=False)
                    payload_root = leaf in ('old-payload', 'new-payload')
                    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and info.st_dev == os.fstat(p5_tx).st_dev and
                            stat.S_IMODE(info.st_mode) == (0o710 if payload_root else 0o700) and
                            (60000 <= info.st_gid <= 60999 if payload_root else info.st_gid == 0), 'unsafe private restore object')
                    fd = os.open(leaf, DIR_FLAGS, dir_fd=p5_tx)
                    opened.append(fd)
                    require(_mount(fd) == _mount(p5_tx), 'private restore object mounted')
                    _release(opened, fd, release_errors)
            if state_tx is None or _maybe(state_tx, 'intent.json') is None:
                if state_tx is not None:
                    require(all(leaf.startswith('.pending-') for leaf in _names(state_tx)),
                            'pre-intent records cannot confer restore authority')
                    for leaf in _names(state_tx):
                        _pending(state_tx, leaf)
                result['private_stages'].append(name)
                result['transactions'].append(dict(transaction_id=name, state='private-preintent'))
                continue
            raw = _read(state_tx, 'intent.json', validate=SCHEMA.decode)
            intent = SCHEMA.decode(raw)
            require(intent['kind'] == 'intent' and intent['transaction_id'] == name and
                    intent['storage'] == context.storage and p5_tx is not None and
                    intent['state_inode'] == _inode(os.fstat(state_tx)) and
                    intent['p5_inode'] == _inode(os.fstat(p5_tx)), 'census restore identity differs')
            _provenance(state_tx, raw)
            expected_retained = {'old-' + key for key in ('payload', 'lifecycle') if intent['old'][key] is not None}
            require(_names(p5_tx) == expected_retained, 'completed restore retention names differ')
            for key in ('payload', 'lifecycle'):
                expected = intent['old'][key]
                if expected is not None:
                    require(_inode(os.stat('old-' + key, dir_fd=p5_tx, follow_symlinks=False)) == expected['inode'],
                            'completed restore retained inode differs')
            result['completed'].append(name)
            result['transactions'].append(dict(transaction_id=name, state='data-committed'))
        check()
        for parent, name, metadata in observed:
            require(Tree.metadata(os.stat(name, dir_fd=parent, follow_symlinks=False)) == metadata,
                    'census observed namespace changed')
        for parent, name, fd, identity in edges:
            require(_inode(os.stat(name, dir_fd=parent, follow_symlinks=False)) == identity ==
                    _inode(os.fstat(fd)), 'census namespace changed')
    except BaseException as error:
        primary = error
    errors = list(release_errors)
    for fd in reversed(opened):
        try:
            os.close(fd)
        except BaseException as error:
            errors.append(repr(error))
    if primary is not None or errors:
        raise TransactionError(dict(phase='bootstrap-census', success=False, data_committed=False,
                                    transaction_id=current_transaction,
                                    cleanup_uncertain=bool(errors), close_errors=errors),
                               primary or RuntimeError('census cleanup uncertain')) from primary
    return result


def observe_completed(policy, name, transaction_id, archive_sha256, manifest_sha256):
    """Historical readonly lookup, before admission closure or archive access.

    Actual installed Policy pins physical STATE. This returns provenance data,
    never live authority. Missing/incomplete exact transactions return None;
    malformed or unsafe records refuse. No current guest bytes are inspected.
    """
    require(type(policy) is CUSTODY.BOOT_POLICY.Policy, 'actual canonical installed Policy required')
    policy.check()
    CUSTODY.installed_guard(policy.boot_id)
    ADMISSION._guest_name(name)
    require(name != 'firstkvm', 'firstkvm cannot be restored')
    SCHEMA.transaction_id(transaction_id)
    SCHEMA.pin(archive_sha256)
    SCHEMA.pin(manifest_sha256)
    fds, edges, primary, result = [], [], None, None
    def check():
        policy.check()
        CUSTODY.installed_guard(policy.boot_id)
        for parent, leaf, fd, identity in edges:
            _directory(os.fstat(fd), os.fstat(parent).st_dev)
            require(_inode(os.stat(leaf, dir_fd=parent, follow_symlinks=False)) == identity ==
                    _inode(os.fstat(fd)), 'completed restore directory replaced')
    try:
        parent = policy._state_fd
        for leaf in (STATE_NAME, transaction_id):
            info = _maybe(parent, leaf)
            if info is None:
                break
            _directory(info, os.fstat(parent).st_dev)
            fd = os.open(leaf, DIR_FLAGS, dir_fd=parent)
            fds.append(fd)
            require(_inode(os.fstat(fd)) == _inode(info) and _mount(fd) == _mount(parent),
                    'completed restore namespace replaced/mounted')
            edges.append((parent, leaf, fd, _inode(info)))
            parent = fd
        else:
            if _maybe(parent, 'intent.json') is not None:
                raw = _read(parent, 'intent.json', validate=SCHEMA.decode)
                intent = SCHEMA.decode(raw)
                require(intent['kind'] == 'intent' and intent['transaction_id'] == transaction_id and
                        intent['name'] == name and intent['storage'] == policy.value['storage'] and
                        intent['source']['archive']['sha256'] == archive_sha256 and
                        intent['source']['manifest_sha256'] == manifest_sha256 and
                        intent['state_inode'] == _inode(os.fstat(parent)),
                        'completed restore identity/source differs')
                if _maybe(parent, 'data-commit.json') is not None:
                    _provenance(parent, raw)
                    result = dict(schema=1, transaction_id=transaction_id, phase='historical-data-commit',
                                  historical=True, data_committed=True, success=True,
                                  cleanup_uncertain=False, close_errors=[], hardware_acceptance=False,
                                  fresh_boot_required=True)
                else:
                    _provenance(parent, raw, incomplete=True)
            else:
                require(all(leaf.startswith('.pending-') for leaf in _names(parent)),
                        'provenance without restore intent')
                for leaf in _names(parent):
                    _pending(parent, leaf)
        check()
    except BaseException as error:
        primary = error
    errors = []
    for fd in reversed(fds):
        try:
            os.close(fd)
        except BaseException as error:
            errors.append(repr(error))
    if primary is not None or errors:
        receipt = result or dict(phase='observe-completed', data_committed=False)
        receipt.update(success=False, cleanup_uncertain=bool(errors), close_errors=errors)
        raise TransactionError(receipt, primary or RuntimeError('completed lookup cleanup uncertain')) from primary
    return result
