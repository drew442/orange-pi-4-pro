#!/usr/bin/python3
"""Authenticate and verify a guest backup into a new private artifact directory.

This performs no restore, service operation, guest launch or hardware acceptance.
"""
import argparse
import ctypes
import errno
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import signal
import stat
import sys
import zlib

BASE = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, BASE / path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


FORMAT = module('receiver_backup_format', 'configs/guest_backup.py')
STATE = module('receiver_host_state', 'build/host-state.py')
require = FORMAT.require
MAX_ARCHIVE = 251 * 1024**3
RESERVE = 256 * 1024**2
CHUNK = 1024 * 1024


def inode(info):
    return info.st_dev, info.st_ino


def metadata(info):
    return tuple(getattr(info, 'st_' + key) for key in
                 ('dev', 'ino', 'mode', 'uid', 'gid', 'nlink', 'size', 'mtime_ns', 'ctime_ns'))


def same(parent, name, expected):
    try:
        return inode(os.stat(name, dir_fd=parent, follow_symlinks=False)) == expected
    except FileNotFoundError:
        return False


def directory(info):
    require(stat.S_ISDIR(info.st_mode) and info.st_uid in (0, os.geteuid()) and
            not info.st_mode & 0o022, 'unsafe output ancestor')


class Parent:
    def __init__(self, output):
        self.fds = []
        self.names = []
        path = Path(output)
        require(path.is_absolute() and str(path) == str(output) and
                all(x not in ('.', '..') for x in str(output).split('/')[1:]) and path.name,
                'canonical absolute new output required')
        self.name = path.name
        self.path = path.parent
        try:
            self.fds.append(os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
            directory(os.fstat(self.fds[0]))
            for part in path.parent.parts[1:]:
                fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                             dir_fd=self.fds[-1])
                self.names.append(part)
                self.fds.append(fd)
                directory(os.fstat(fd))
            self.fd = self.fds[-1]
            self.recheck()
            absent(self.fd, self.name)
        except BaseException:
            self.close()
            raise

    def recheck(self):
        for fd in self.fds:
            directory(os.fstat(fd))
        for index, name in enumerate(self.names):
            require(same(self.fds[index], name, inode(os.fstat(self.fds[index + 1]))),
                    'output ancestor changed')

    def close(self):
        error = None
        for fd in reversed(self.fds):
            try:
                os.close(fd)
            except BaseException as caught:
                error = caught
        self.fds.clear()
        if error:
            raise error


def absent(parent, name):
    try:
        os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return
    raise ValueError('new output already exists')


def rename_new(parent, old, new):
    library = ctypes.CDLL(None, use_errno=True)
    rename = library.renameat2
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(parent, os.fsencode(old), parent, os.fsencode(new), 1):
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number))


def write_all(fd, data):
    view = memoryview(data)
    while view:
        count = os.write(fd, view)
        require(count > 0, 'short backup write')
        view = view[count:]


def free_space(fd, needed):
    info = os.fstatvfs(fd)
    require(info.f_bavail * info.f_frsize >= needed, 'insufficient output free space')


class Stage:
    def __init__(self, parent):
        self.parent = parent
        self.name = '.guest-backup-' + secrets.token_hex(16)
        self.entries = []
        self.fds = []
        os.mkdir(self.name, 0o700, dir_fd=parent.fd)
        self.fd = os.open(self.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent.fd)
        self.fds.append(self.fd)
        self.identity = inode(os.fstat(self.fd))
        self.recheck()

    def recheck(self):
        self.parent.recheck()
        info = os.fstat(self.fd)
        require(info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700 and
                inode(info) == self.identity and same(self.parent.fd, self.name, self.identity),
                'backup stage replaced')
        for parent, name, identity, _isdir in self.entries:
            require(same(parent, name, identity), 'staged artifact replaced')

    def create(self, name, parent=None):
        parent = self.fd if parent is None else parent
        fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        self.fds.append(fd)
        self.entries.append((parent, name, inode(os.fstat(fd)), False))
        return fd

    def history_directory(self):
        os.mkdir('.checkpoints', 0o700, dir_fd=self.fd)
        fd = os.open('.checkpoints', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.fd)
        self.fds.append(fd)
        self.entries.append((self.fd, '.checkpoints', inode(os.fstat(fd)), True))
        return fd

    def finish(self, fd):
        os.fchmod(fd, 0o400)
        os.fsync(fd)
        info = os.fstat(fd)
        require(info.st_uid == os.geteuid() and info.st_nlink == 1 and
                stat.S_IMODE(info.st_mode) == 0o400, 'unsafe staged file')

    def close(self, published):
        error = None
        if not published:
            for parent, name, identity, isdir in reversed(self.entries):
                try:
                    if same(parent, name, identity):
                        (os.rmdir if isdir else os.unlink)(name, dir_fd=parent)
                except BaseException as caught:
                    error = caught
        for fd in reversed(self.fds):
            try:
                os.close(fd)
            except BaseException as caught:
                error = caught
        if not published:
            try:
                if same(self.parent.fd, self.name, self.identity):
                    os.rmdir(self.name, dir_fd=self.parent.fd)
            except BaseException as caught:
                error = caught
        if error:
            raise error


def readback(fd, expected):
    info = os.fstat(fd)
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.geteuid() and
            stat.S_IMODE(info.st_mode) == 0o400 and info.st_size == expected['bytes'],
            'staged metadata mismatch')
    os.lseek(fd, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    while True:
        chunk = os.read(fd, CHUNK)
        if not chunk:
            break
        digest.update(chunk)
    require(digest.hexdigest() == expected['sha256'] and metadata(info) == metadata(os.fstat(fd)),
            'staged readback differs')


def record(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def capture(stage, archive, archive_sha):
    fd = os.open(archive, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and
                0 < info.st_size <= MAX_ARCHIVE, 'invalid encoded archive metadata/size')
        free_space(stage.fd, info.st_size + RESERVE)
        target = stage.create('archive.tar.gz')
        digest = hashlib.sha256()
        remaining = info.st_size
        while remaining:
            chunk = os.read(fd, min(CHUNK, remaining))
            require(bool(chunk), 'archive changed during capture')
            digest.update(chunk)
            write_all(target, chunk)
            remaining -= len(chunk)
        require(not os.read(fd, 1) and metadata(info) == metadata(os.fstat(fd)),
                'archive changed during capture')
        require(digest.hexdigest() == archive_sha, 'encoded archive digest mismatch')
        stage.finish(target)
        # Authenticate the private capture independently before any decoding.
        readback(target, {'bytes': info.st_size, 'sha256': archive_sha})
        os.lseek(target, 0, os.SEEK_SET)
        return target, info.st_size
    finally:
        os.close(fd)


def padding(stream, size):
    require(FORMAT.read_exact(stream, (-size) % 512) == bytes((-size) % 512), 'nonzero tar padding')


class GzipReader:
    """Single member decoder with bounded output and no encoded suffix."""
    def __init__(self, fd):
        self.fd = fd
        self.decoder = zlib.decompressobj(31)
        self.pending = b''
        self.done = False

    def read(self, size):
        result = bytearray()
        while len(result) < size and not self.done:
            if not self.pending:
                self.pending = os.read(self.fd, CHUNK)
                require(bool(self.pending), 'truncated gzip stream')
            chunk = self.decoder.decompress(self.pending, size - len(result))
            result.extend(chunk)
            self.pending = self.decoder.unconsumed_tail
            if self.decoder.eof:
                require(not self.decoder.unused_data and not os.read(self.fd, 1),
                        'extra encoded gzip suffix/member')
                self.done = True
        return bytes(result)


def extract(stage, archive_fd, encoded_size, manifest_sha):
    files = {}
    stream = GzipReader(archive_fd)
    # Profile fixes the gzip header too; the digest authenticates all encoded bytes.
    require(os.pread(archive_fd, 10, 0) == b'\x1f\x8b\x08\x00\x00\x00\x00\x00\x02\xff',
            'noncanonical gzip header')
    size = FORMAT.read_header(stream, 'enrollment.json', FORMAT.MANIFEST.LIMIT)
    manifest_data = FORMAT.read_exact(stream, size)
    require(hashlib.sha256(manifest_data).hexdigest() == manifest_sha, 'manifest digest mismatch')
    manifest = FORMAT.MANIFEST.validate_manifest(manifest_data)
    padding(stream, size)
    size = FORMAT.read_header(stream, 'checkpoint-history.json', FORMAT.HISTORY_LIMIT)
    # Reserve full logical output even though zero runs will be sparse.
    free_space(stage.fd, sum(x['bytes'] for x in manifest['files'].values()) +
               len(manifest_data) + 2 * size + RESERVE)
    history_data = FORMAT.read_exact(stream, size)
    history = FORMAT.validate_history(history_data, manifest_data)
    padding(stream, size)
    for name, data in [('enrollment.json', manifest_data), ('checkpoint-history.json', history_data)]:
        fd = stage.create(name)
        write_all(fd, data)
        stage.finish(fd)
        files[name] = (fd, record(data))
    history_fd = stage.history_directory()
    for name, data in history.items():
        fd = stage.create(name, history_fd)
        write_all(fd, data)
        stage.finish(fd)
        files['.checkpoints/' + name] = (fd, record(data))
    os.fsync(history_fd)
    for name in FORMAT.NAMES[2:]:
        expected = manifest['files'][name]
        size = FORMAT.read_header(stream, name, expected['bytes'])
        require(size == expected['bytes'], 'backup payload size mismatch')
        fd = stage.create(name)
        remaining = size
        digest = hashlib.sha256()
        while remaining:
            chunk = FORMAT.read_exact(stream, min(CHUNK, remaining))
            digest.update(chunk)
            if chunk == bytes(len(chunk)):
                os.lseek(fd, len(chunk), os.SEEK_CUR)
            else:
                write_all(fd, chunk)
            remaining -= len(chunk)
        os.ftruncate(fd, size)
        require(digest.hexdigest() == expected['sha256'], 'backup payload digest mismatch')
        padding(stream, size)
        stage.finish(fd)
        files[name] = (fd, {key: expected[key] for key in ('bytes', 'sha256')})
    tail = stream.read(10241)
    require(1024 <= len(tail) <= 10240 and len(tail) % 512 == 0 and tail == bytes(len(tail)),
            'invalid tar end or extra decoded data')
    return files, len(history)


def verify(archive, archive_sha256, manifest_sha256, new_output):
    receipt = {'schema': 1, 'profile': FORMAT.PROFILE, 'success': False, 'published': False,
               'durable': False, 'classification': 'artifact-only',
               'archive_sha256': archive_sha256, 'manifest_sha256': manifest_sha256,
               'guarantees': 'No off-device storage, guest boot or restore guarantee.'}
    parent = stage = None
    try:
        FORMAT.MANIFEST.digest(archive_sha256)
        FORMAT.MANIFEST.digest(manifest_sha256)
        parent = Parent(new_output)
        storage = STATE.validate_workspace_storage(Path('/proc') / str(os.getpid()) / 'fd' / str(parent.fd))
        stage = Stage(parent)
        archive_fd, encoded_size = capture(stage, archive, archive_sha256)
        files, count = extract(stage, archive_fd, encoded_size, manifest_sha256)
        files['archive.tar.gz'] = (archive_fd, {'bytes': encoded_size, 'sha256': archive_sha256})
        for fd, expected in files.values():
            readback(fd, expected)
        receipt.update({'source_history_records': count, 'storage': storage,
                        'files': {name: item[1] for name, item in files.items()}})
        verification = {key: value for key, value in receipt.items()
                        if key not in ('success', 'published', 'durable')}
        verification['artifact_checks_passed'] = True
        data = FORMAT.canonical(verification)
        fd = stage.create('backup-verification.json')
        write_all(fd, data)
        stage.finish(fd)
        readback(fd, record(data))
        stage.recheck()
        os.fsync(stage.fd)
        previous = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM})
        try:
            stage.recheck()
            absent(parent.fd, parent.name)
            try:
                rename_new(parent.fd, stage.name, parent.name)
            finally:
                receipt['published'] = same(parent.fd, parent.name, stage.identity)
            require(receipt['published'], 'backup publication missing')
            os.fsync(parent.fd)
            receipt['durable'] = True
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous)
        receipt['success'] = True
    except BaseException as error:
        receipt['error'] = type(error).__name__
    finally:
        if parent is not None and stage is not None:
            receipt['published'] = same(parent.fd, parent.name, stage.identity)
        for resource in (stage, parent):
            if resource is not None:
                try:
                    resource.close(receipt['published']) if resource is stage else resource.close()
                except BaseException as error:
                    receipt['success'] = False
                    receipt['cleanup_error'] = type(error).__name__
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive')
    parser.add_argument('--archive-sha256', required=True)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--new-output', required=True)
    args = parser.parse_args(argv)
    os.umask(0o077)
    def interrupted(_signum, _frame):
        raise KeyboardInterrupt
    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        result = verify(args.archive, args.archive_sha256, args.manifest_sha256, args.new_output)
        print(FORMAT.canonical(result).decode(), end='')
        return 0 if result['success'] else 1
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == '__main__':
    sys.exit(main())
