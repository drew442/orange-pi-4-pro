#!/usr/bin/python3
"""Protected bounded records, without lifecycle or storage authority.

The caller MUST authenticate the actual root/STATE/p5 namespace, storage identity,
serialization/admission and per-guest locks in ``guard()``. A supplied FD, its
owner, or a matching st_dev does not prove NVMe placement or freshness. This
module has no paths, environment overrides, CLI, automatic retries or rollback.
CAS is cooperative under caller locks, not kernel CAS against hostile root.

RootDirectory retains physical ancestor descriptors and verifies their named
identities. read's optional pin is a lower-case SHA-256 hex digest. Publication
receipts describe only completed calls; exceptions preserve partial receipts.
"""
import hashlib
import os
import re
import secrets
import stat

MAX_BYTES = 16 * 1024 * 1024


class StoreError(ValueError):
    """Unsafe metadata, name, pin or changed physical namespace."""


class PublicationError(RuntimeError):
    """A failed publication/creation; inspect receipt before any recovery."""

    def __init__(self, receipt, cause):
        self.receipt = dict(receipt)
        self.cause = cause
        super().__init__(f"{receipt['stage']}: {type(cause).__name__}: {cause}")


def _require(ok, message):
    if not ok:
        raise StoreError(message)


def _name(name):
    _require(isinstance(name, str) and name not in ('.', '..') and
             re.fullmatch(r'[A-Za-z0-9_.-]{1,200}', name) is not None,
             'expected safe single-component name')
    return name


def _pin(pin):
    _require(isinstance(pin, str) and re.fullmatch(r'[0-9a-f]{64}', pin) is not None,
             'expected lower-case SHA-256 pin')


def _inode(info):
    return info.st_dev, info.st_ino


def _root_owned(info):
    return info.st_uid == info.st_gid == 0


def _directory(info, private):
    _require(stat.S_ISDIR(info.st_mode) and _root_owned(info), 'unsafe directory owner/type')
    mode = stat.S_IMODE(info.st_mode)
    _require(mode == 0o700 if private else not mode & 0o022,
             'unsafe directory permissions')


def _regular(info, device, limit):
    _require(stat.S_ISREG(info.st_mode) and _root_owned(info) and
             stat.S_IMODE(info.st_mode) == 0o400 and info.st_nlink == 1 and
             info.st_dev == device and 0 <= info.st_size <= limit,
             'unsafe or oversized record')


def _metadata(info):
    return (_inode(info), info.st_mode, info.st_uid, info.st_gid, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _receipt(operation, name):
    return dict(operation=operation, name=name, stage='validate', success=False,
                mutation_possible=False, published=False, durable=False,
                uncertain=False, file_fsynced=False, directory_fsynced=False,
                file_closed=False, temporary_removed=False)


class RootDirectory:
    """Retained protected child of an externally authenticated parent FD.

    ``guard`` is called with no arguments and must raise on failed authority.
    child() duplicates the entire retained chain and survives parent.close().
    Existing unsafe metadata is always refused, never repaired.
    """

    def __init__(self, parent_fd, name, *, guard, create=False):
        self._initialize(parent_fd, name, guard, create, ())

    def _initialize(self, parent_fd, name, guard, create, ancestors):
        _name(name)
        _require(callable(guard), 'guard must authenticate placement and locks')
        self.guard = guard
        self._fds = []
        self._edges = []
        self._closed = False
        receipt = _receipt('mkdir', name)
        try:
            # Each edge owns its descriptors; no dependence on another object's life.
            for old_parent, old_name, old_child, identity in ancestors:
                parent, child = os.dup(old_parent), None
                self._fds.append(parent)
                child = os.dup(old_child)
                self._fds.append(child)
                self._edges.append((parent, old_name, child, identity))
            parent = os.dup(parent_fd)
            self._fds.append(parent)
            self._base_identity = _inode(os.fstat(self._fds[0]))
            _directory(os.fstat(parent), bool(ancestors))
            self._check_ancestors()
            guard()
            try:
                before = os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                if not create:
                    raise
                receipt.update(stage='mkdir', mutation_possible=True)
                os.mkdir(name, 0o700, dir_fd=parent)
                receipt['published'] = True
                before = os.stat(name, dir_fd=parent, follow_symlinks=False)
            _directory(before, True)  # Refuse special objects before opening them.
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW |
                            os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
            self._fds.append(child)
            identity = _inode(before)
            self._edges.append((parent, name, child, identity))
            self.fd = child
            self.check()
            if create:
                # Also establish durability for a preexisting directory after a
                # previous interrupted creation; no success from mkdir alone.
                receipt['stage'] = 'directory-fsync'
                os.fsync(child)
                receipt['child_directory_fsynced'] = True
                self.check()
                os.fsync(parent)
                receipt.update(directory_fsynced=True, durable=True)
                self.check()
            receipt.update(stage='complete', success=True)
            self.creation_receipt = receipt
        except BaseException as error:
            receipt['uncertain'] = (receipt['mutation_possible'] or
                                    receipt['stage'] == 'directory-fsync')
            for fd in reversed(self._fds):
                try:
                    os.close(fd)
                except BaseException as close_error:
                    receipt['close_error'] = type(close_error).__name__
                    receipt['uncertain'] = True
            self._closed = True
            if receipt['mutation_possible'] or receipt['stage'] == 'directory-fsync':
                raise PublicationError(receipt, error) from error
            raise

    def _check_ancestors(self):
        _require(not self._closed, 'directory is closed')
        if self._fds:
            _require(_inode(os.fstat(self._fds[0])) == self._base_identity,
                     'authenticated parent descriptor changed')
            _directory(os.fstat(self._fds[0]), False)
        for parent, name, child, identity in self._edges:
            parent_info, info = os.fstat(parent), os.fstat(child)
            _directory(parent_info, False)
            _directory(info, True)
            named = os.stat(name, dir_fd=parent, follow_symlinks=False)
            _require(_inode(info) == identity == _inode(named) and
                     info.st_dev == parent_info.st_dev and
                     _metadata(info) == _metadata(named), 'physical directory replaced')

    def check(self):
        self.guard()
        self._check_ancestors()
        self.guard()

    def child(self, name, create=False):
        self.check()
        result = object.__new__(RootDirectory)
        result._initialize(self.fd, name, self.guard, create, self._edges)
        return result

    def read(self, name, limit, pin=None):
        _name(name)
        _require(type(limit) is int and 0 <= limit <= MAX_BYTES, 'invalid read bound')
        if pin is not None:
            _pin(pin)
        self.check()
        fd = None
        try:
            initial = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
            _regular(initial, os.fstat(self.fd).st_dev, limit)
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK |
                         os.O_CLOEXEC, dir_fd=self.fd)
            return self._read_retained(fd, name, initial, limit, pin)
        except FileNotFoundError:
            self.check()
            raise
        finally:
            if fd is not None:
                os.close(fd)

    def _read_retained(self, fd, name, initial, limit, pin):
        """Read/recheck a held regular FD without closing or replacing it."""
        self.check()
        before = os.fstat(fd)
        _regular(before, os.fstat(self.fd).st_dev, limit)
        _require(_metadata(initial) == _metadata(before), 'record changed before read')
        os.lseek(fd, 0, os.SEEK_SET)
        chunks, remaining = [], before.st_size + 1
        while remaining:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b''.join(chunks)
        _require(len(data) == before.st_size and _metadata(before) ==
                 _metadata(os.fstat(fd)) == _metadata(os.stat(
                     name, dir_fd=self.fd, follow_symlinks=False)), 'record changed during read')
        if pin is not None:
            _require(hashlib.sha256(data).hexdigest() == pin, 'record pin differs')
        self.check()
        return data

    def sync(self, name, *, pin, limit=MAX_BYTES):
        """Establish durability of the exact existing pinned record, without writes.

        Used after a previous ambiguous publication: visibility/hash agreement
        alone is not a completed durability barrier. The caller retains its
        authenticated locks throughout this operation. No inode is replaced and
        no metadata is repaired. Successful receipt facts require completed
        file/parent fsync, final authentication and a successful single close.
        """
        receipt = _receipt('sync-existing-record', name)
        receipt['sha256'] = pin
        fd = None
        failure = None
        try:
            _name(name)
            _pin(pin)
            _require(type(limit) is int and 0 <= limit <= MAX_BYTES, 'invalid read bound')
            self.check()
            initial = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
            _regular(initial, os.fstat(self.fd).st_dev, limit)
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK |
                         os.O_CLOEXEC, dir_fd=self.fd)
            data = self._read_retained(fd, name, initial, limit, pin)
            receipt.update(bytes=len(data), stage='file-fsync', mutation_possible=True)
            os.fsync(fd)
            receipt['file_fsynced'] = True
            self._read_retained(fd, name, initial, limit, pin)
            receipt['stage'] = 'directory-fsync'
            os.fsync(self.fd)
            receipt['directory_fsynced'] = True
            self._read_retained(fd, name, initial, limit, pin)
            receipt['durable'] = True
        except BaseException as error:
            failure = error
            receipt['uncertain'] = True
        finally:
            if fd is not None:
                try:
                    # A failed close may already have released the descriptor.
                    # Never retry it, even when the numeric FD remains visible.
                    os.close(fd)
                    receipt['file_closed'] = True
                except BaseException as error:
                    receipt['close_error'] = type(error).__name__
                    if failure is None:
                        receipt['stage'] = 'close'
                    receipt['uncertain'] = True
                    failure = failure or error
        if failure is not None:
            raise PublicationError(receipt, failure) from failure
        receipt.update(stage='complete', success=True)
        return receipt

    def _expected(self, name, expected):
        if expected is None:
            try:
                os.stat(name, dir_fd=self.fd, follow_symlinks=False)
            except FileNotFoundError:
                return
            raise StoreError('immutable destination already exists')
        self.read(name, MAX_BYTES, pin=expected)

    def publish(self, name, data, *, expected_sha256=None):
        _name(name)
        _require(type(data) is bytes and len(data) <= MAX_BYTES,
                 'payload must be immutable bounded bytes')
        if expected_sha256 is not None:
            _pin(expected_sha256)
        receipt = _receipt('publish', name)
        receipt.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                       expected_sha256=expected_sha256)
        temporary = '.lifecycle-' + secrets.token_hex(16)
        fd = identity = None
        cleanup_attempted = False
        failure = None
        try:
            self.check()
            self._expected(name, expected_sha256)
            receipt.update(stage='temporary-create', mutation_possible=True)
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                         os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                         0o600, dir_fd=self.fd)
            identity = _inode(os.fstat(fd))
            info = os.fstat(fd)
            _require(stat.S_ISREG(info.st_mode) and _root_owned(info) and
                     info.st_nlink == 1 and info.st_dev == os.fstat(self.fd).st_dev,
                     'unsafe new temporary')
            self.check()
            receipt['stage'] = 'write'
            offset = 0
            while offset < len(data):
                count = os.write(fd, data[offset:])
                _require(0 < count <= len(data) - offset, 'short/invalid write')
                offset += count
            os.fchmod(fd, 0o400)
            _regular(os.fstat(fd), os.fstat(self.fd).st_dev, MAX_BYTES)
            _require(os.fstat(fd).st_size == len(data), 'temporary size differs')
            temporary_metadata = _metadata(os.fstat(fd))
            receipt['stage'] = 'file-fsync'
            os.fsync(fd)
            receipt['file_fsynced'] = True
            self.check()
            _require(_inode(os.stat(temporary, dir_fd=self.fd, follow_symlinks=False)) ==
                     identity and _metadata(os.fstat(fd)) == temporary_metadata and
                     _metadata(os.stat(temporary, dir_fd=self.fd, follow_symlinks=False)) ==
                     temporary_metadata, 'temporary replaced or changed')
            # Re-read the exact old content immediately before publication.
            self._expected(name, expected_sha256)
            self.check()
            receipt['stage'] = 'publish'
            if expected_sha256 is None:
                # link is atomic no-replace. The extra link is removed before
                # successful return; no existing destination is ever overwritten.
                os.link(temporary, name, src_dir_fd=self.fd, dst_dir_fd=self.fd,
                        follow_symlinks=False)
            else:
                os.replace(temporary, name, src_dir_fd=self.fd, dst_dir_fd=self.fd)
            receipt['published'] = True
            cleanup_attempted = True
            self._remove_temporary(temporary, identity, receipt)
            _regular(os.fstat(fd), os.fstat(self.fd).st_dev, MAX_BYTES)
            self.check()
            receipt['stage'] = 'directory-fsync'
            os.fsync(self.fd)
            receipt.update(directory_fsynced=True, durable=True)
            self.check()
        except BaseException as error:
            failure = error
            receipt['uncertain'] = receipt['mutation_possible']
        finally:
            # An interrupted syscall may have acted. Reconcile identity only;
            # this never infers durability from a visible destination.
            if identity is not None:
                try:
                    receipt['destination_matches'] = (_inode(os.stat(
                        name, dir_fd=self.fd, follow_symlinks=False)) == identity)
                    receipt['published'] |= receipt['destination_matches']
                except FileNotFoundError:
                    pass
                except BaseException as error:
                    receipt['reconcile_error'] = type(error).__name__
                    receipt['uncertain'] = True
                    failure = failure or error
                try:
                    if not cleanup_attempted:
                        cleanup_attempted = True
                        self._remove_temporary(temporary, identity, receipt)
                except BaseException as error:
                    receipt['cleanup_error'] = type(error).__name__
                    receipt['uncertain'] = True
                    failure = failure or error
            if fd is not None:
                try:
                    # Do not retry close: the kernel may already have released FD.
                    os.close(fd)
                    receipt['file_closed'] = True
                except BaseException as error:
                    receipt['close_error'] = type(error).__name__
                    if failure is None:
                        receipt['stage'] = 'close'
                    receipt['uncertain'] = True
                    failure = failure or error
        if failure is not None:
            raise PublicationError(receipt, failure) from failure
        receipt.update(stage='complete', success=True)
        return receipt

    def _remove_temporary(self, name, identity, receipt):
        try:
            info = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        if _inode(info) == identity:
            # Do not clean up through a replaced ancestor or failed authority.
            self.check()
            os.unlink(name, dir_fd=self.fd)
            receipt['temporary_removed'] = True

    def close(self):
        if self._closed:
            return
        self._closed = True
        failure = None
        for fd in reversed(self._fds):
            try:
                os.close(fd)
            except BaseException as error:
                failure = failure or error
        if failure is not None:
            raise failure

    def __enter__(self):
        self.check()
        return self

    def __exit__(self, *_args):
        self.close()
