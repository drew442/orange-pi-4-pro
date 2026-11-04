#!/usr/bin/python3
"""Index a previously verified backup on known protected fixed local storage.

Only the encoded archive and first manifest are authenticated here. A complete
receiver verification is a prerequisite; this adds no VM restore acceptance.
"""
import argparse
import fcntl
import hashlib
import importlib.util
import os
from pathlib import Path
import secrets
import signal
import stat
import sys

BASE = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, BASE / path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


RECEIVER = module('index_backup_receiver', 'scripts/verify-guest-backup.py')
FORMAT = RECEIVER.FORMAT
CHUNKS = module('index_backup_chunks', 'configs/guest_backup_chunks.py')
require = CHUNKS.require


class Source:
    """Retain every protected directory and the exact read-only archive inode."""
    def __init__(self, archive):
        self.fds = []
        self.names = []
        path = Path(archive)
        require(path.is_absolute() and str(path) == str(archive) and path.name and
                all(x not in ('.', '..') for x in str(archive).split('/')[1:]),
                'canonical absolute archive required')
        self.name = path.name
        try:
            self.fds.append(os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
            RECEIVER.directory(os.fstat(self.fds[0]))
            for part in path.parent.parts[1:]:
                self.fds.append(os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                        dir_fd=self.fds[-1]))
                self.names.append(part)
                RECEIVER.directory(os.fstat(self.fds[-1]))
            self.fd = os.open(self.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                              dir_fd=self.fds[-1])
            self.fds.append(self.fd)
            self.info = os.fstat(self.fd)
            fcntl.flock(self.fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            self.recheck()
        except BaseException:
            self.close()
            raise

    def recheck(self):
        for fd in self.fds[:-1]:
            RECEIVER.directory(os.fstat(fd))
        for i, name in enumerate(self.names):
            require(RECEIVER.same(self.fds[i], name, RECEIVER.inode(os.fstat(self.fds[i + 1]))),
                    'source ancestor changed')
        info = os.fstat(self.fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid in (0, os.geteuid()) and
                info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o400 and
                0 < info.st_size <= CHUNKS.MAX_ARCHIVE and
                RECEIVER.metadata(info) == RECEIVER.metadata(self.info) and
                RECEIVER.same(self.fds[-2], self.name, RECEIVER.inode(info)),
                'unsafe or changed source archive')
        fcntl.flock(self.fd, fcntl.LOCK_SH | fcntl.LOCK_NB)

    def close(self):
        for fd in reversed(self.fds):
            os.close(fd)
        self.fds.clear()


def fdpath(fd):
    return Path('/proc') / str(os.getpid()) / 'fd' / str(fd)


def scan(source, expected):
    digest = hashlib.sha256()
    hashes = []
    remaining = source.info.st_size
    while remaining:
        needed = min(CHUNKS.CHUNK, remaining)
        chunk = bytearray()
        while len(chunk) < needed:
            data = os.read(source.fd, needed - len(chunk))
            require(bool(data), 'truncated source archive')
            chunk.extend(data)
        digest.update(chunk)
        hashes.append(hashlib.sha256(chunk).hexdigest())
        remaining -= needed
    require(not os.read(source.fd, 1), 'source archive grew')
    source.recheck()
    require(digest.hexdigest() == expected, 'encoded archive digest mismatch')
    return hashes


def authenticate_manifest(source, expected):
    source.recheck()
    os.lseek(source.fd, 0, os.SEEK_SET)
    require(os.pread(source.fd, 10, 0) == b'\x1f\x8b\x08\x00\x00\x00\x00\x00\x02\xff',
            'noncanonical gzip header')
    stream = RECEIVER.GzipReader(source.fd)
    size = FORMAT.read_header(stream, 'enrollment.json', FORMAT.MANIFEST.LIMIT)
    raw = FORMAT.read_exact(stream, size)
    require(hashlib.sha256(raw).hexdigest() == expected, 'manifest digest mismatch')
    FORMAT.MANIFEST.validate_manifest(raw)
    source.recheck()


def index(archive, archive_sha256, manifest_sha256, new_output):
    receipt = {'schema': 1, 'profile': CHUNKS.PROFILE, 'success': False, 'published': False,
               'durable': False, 'classification': 'artifact-only',
               'manifest_sha256': manifest_sha256,
               'guarantees': 'Requires prior full backup verification; no VM restore acceptance.'}
    parent = source = None
    fd = identity = temporary = None
    try:
        CHUNKS.digest(archive_sha256)
        CHUNKS.digest(manifest_sha256)
        parent = RECEIVER.Parent(new_output)
        storage = RECEIVER.STATE.validate_workspace_storage(fdpath(parent.fd))
        source = Source(archive)
        source_storage = RECEIVER.STATE.validate_workspace_storage(fdpath(source.fd))
        hashes = scan(source, archive_sha256)
        authenticate_manifest(source, manifest_sha256)
        record = {'bytes': source.info.st_size, 'sha256': archive_sha256}
        data = CHUNKS.make(record, manifest_sha256, hashes)
        receipt.update({'archive': record, 'index': RECEIVER.record(data),
                        'chunk_count': len(hashes), 'storage': storage,
                        'source_storage': source_storage})
        parent.recheck()
        temporary = '.guest-backup-index-' + secrets.token_hex(16)
        fd = os.open(temporary, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=parent.fd)
        identity = RECEIVER.inode(os.fstat(fd))
        RECEIVER.write_all(fd, data)
        os.fchmod(fd, 0o400)
        os.fsync(fd)
        RECEIVER.readback(fd, receipt['index'])
        staged_info = RECEIVER.metadata(os.fstat(fd))
        previous = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM})
        try:
            source.recheck()
            parent.recheck()
            require(RECEIVER.same(parent.fd, temporary, identity), 'index temporary replaced')
            require(RECEIVER.metadata(os.fstat(fd)) == staged_info,
                    'staged index metadata changed')
            RECEIVER.absent(parent.fd, parent.name)
            try:
                RECEIVER.rename_new(parent.fd, temporary, parent.name)
            finally:
                receipt['published'] = RECEIVER.same(parent.fd, parent.name, identity)
            require(receipt['published'], 'index publication missing')
            os.fsync(parent.fd)
            receipt['durable'] = True
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous)
        receipt['success'] = True
    except BaseException as error:
        receipt['error'] = type(error).__name__
    finally:
        if parent is not None and identity is not None:
            receipt['published'] = RECEIVER.same(parent.fd, parent.name, identity)
        try:
            if identity is not None and not receipt['published'] and RECEIVER.same(parent.fd, temporary, identity):
                os.unlink(temporary, dir_fd=parent.fd)
        except BaseException as error:
            receipt['success'] = False
            receipt['cleanup_error'] = type(error).__name__
        for resource in (fd, source, parent):
            if resource is not None:
                try:
                    os.close(resource) if type(resource) is int else resource.close()
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
        result = index(args.archive, args.archive_sha256, args.manifest_sha256, args.new_output)
        print(CHUNKS.canonical(result).decode(), end='')
        return 0 if result['success'] else 1
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == '__main__':
    sys.exit(main())
