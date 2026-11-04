#!/usr/bin/python3
"""Stream a pinned stopped guest to a remote receiver through stdout.

File verification cannot establish clean shutdown, filesystem consistency,
restorability, guest boot or KVM hardware acceptance. Discard partial streams.
Trusted root and cooperative writers must honor the retained disk lock.
"""
import argparse
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import stat
import sys

BASE = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, BASE / path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


POLICY = module('backup_checkpoint_policy', 'scripts/guest-checkpoint-policy.py')
BACKUP = module('sender_guest_backup', 'configs/guest_backup.py')
ENROLL = POLICY.ENROLL
require = POLICY.require
CHUNK = 1024 * 1024


class EncodedWriter:
    """Count/hash only bytes accepted by the output, including short writes."""
    def __init__(self, writer):
        self.writer = writer
        self.count = 0
        self.digest = hashlib.sha256()

    def write(self, data):
        view = memoryview(data)
        total = len(view)
        while view:
            count = self.writer.write(view)
            require(type(count) is int and 0 < count <= len(view), 'invalid output write')
            self.digest.update(view[:count])
            self.count += count
            view = view[count:]
        return total

    def flush(self):
        self.writer.flush()


class History:
    """Retain every history descriptor and pin names/metadata across streaming."""
    def __init__(self, inspection):
        self.inspection = inspection
        self.fd = None
        self.files = []
        self.entries = {}
        try:
            try:
                self.fd = os.open('.checkpoints', os.O_RDONLY | os.O_DIRECTORY |
                                  os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=inspection.guest_fd)
            except FileNotFoundError:
                self.payload = BACKUP.history_payload({}, inspection.manifest_data)
                return
            self.info = os.fstat(self.fd)
            require(stat.S_ISDIR(self.info.st_mode) and self.info.st_uid == self.info.st_gid == 0 and
                    stat.S_IMODE(self.info.st_mode) == 0o700 and
                    self.info.st_dev == os.fstat(inspection.guest_fd).st_dev,
                    'unsafe checkpoint history directory')
            names = os.listdir(self.fd)
            require(len(names) <= BACKUP.ENTRY_COUNT, 'too many checkpoint records')
            # Exact envelope overhead is checked by history_payload below. Bound
            # retained content before allocating a potentially huge history.
            encoded_content = 0
            for name in sorted(names):
                require(BACKUP.NAME_RE.fullmatch(name) is not None, 'invalid checkpoint filename')
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
                info = os.fstat(fd)
                self.files.append((name, fd, info))
                require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and
                        info.st_uid == info.st_gid == 0 and stat.S_IMODE(info.st_mode) == 0o400 and
                        info.st_dev == self.info.st_dev and 0 < info.st_size <= BACKUP.ENTRY_LIMIT,
                        'unsafe checkpoint record')
                encoded_content += ((info.st_size + 2) // 3) * 4 + len(name)
                require(encoded_content <= BACKUP.HISTORY_LIMIT, 'checkpoint history exceeds limit')
                self.entries[name] = read_fd(fd, info.st_size)
            self.payload = BACKUP.history_payload(self.entries, inspection.manifest_data)
            self.recheck()
        except BaseException:
            self.close()
            raise

    def recheck(self):
        guest = self.inspection.guest_fd
        if self.fd is None:
            try:
                os.stat('.checkpoints', dir_fd=guest, follow_symlinks=False)
            except FileNotFoundError:
                return
            raise ValueError('checkpoint history appeared')
        require(ENROLL.identity(os.fstat(self.fd)) == ENROLL.identity(self.info) and
                ENROLL.identity(os.stat('.checkpoints', dir_fd=guest, follow_symlinks=False)) ==
                ENROLL.identity(self.info), 'checkpoint history directory changed')
        require(set(os.listdir(self.fd)) == set(self.entries), 'checkpoint history names changed')
        for name, fd, info in self.files:
            require(ENROLL.identity(os.fstat(fd)) == ENROLL.identity(info) and
                    ENROLL.identity(os.stat(name, dir_fd=self.fd, follow_symlinks=False)) ==
                    ENROLL.identity(info), 'checkpoint record changed')

    def close(self):
        # Close all retained descriptors even when one close raises.
        error = None
        for fd in [item[1] for item in self.files] + ([self.fd] if self.fd is not None else []):
            try:
                os.close(fd)
            except BaseException as caught:
                error = caught
        self.files = []
        self.fd = None
        if error:
            raise error


def read_fd(fd, size):
    os.lseek(fd, 0, os.SEEK_SET)
    parts, remaining = [], size
    while remaining:
        piece = os.read(fd, min(CHUNK, remaining))
        require(bool(piece), 'checkpoint record truncated')
        parts.append(piece)
        remaining -= len(piece)
    require(not os.read(fd, 1), 'checkpoint record grew')
    return b''.join(parts)


def write_entry(stream, name, data):
    stream.write(BACKUP.make_header(name, len(data)))
    stream.write(data)
    stream.write(b'\0' * (-len(data) % 512))


def stream_file(stream, inspection, key, name):
    fd = inspection.fds[key]
    expected = inspection.manifest['config'][key]
    before = os.fstat(fd)
    inspection._held(fd)
    stream.write(BACKUP.make_header(name, expected['bytes']))
    os.lseek(fd, 0, os.SEEK_SET)
    remaining, digest = expected['bytes'], hashlib.sha256()
    while remaining:
        data = os.read(fd, min(CHUNK, remaining))
        require(bool(data), 'guest input truncated')
        remaining -= len(data)
        digest.update(data)
        stream.write(data)
    require(not os.read(fd, 1), 'guest input grew')
    require(digest.hexdigest() == expected['sha256'], name + ' SHA-256 differs during backup')
    require(ENROLL.identity(before) == ENROLL.identity(os.fstat(fd)), 'guest input metadata changed')
    inspection._held(fd)
    stream.write(b'\0' * (-expected['bytes'] % 512))


def send(name, pin, writer):
    encoded = EncodedWriter(writer)
    with POLICY.Inspection(name, pin) as inspection:
        require(inspection.disk_record == inspection.manifest['config']['disk'],
                'disk changed: explicit checkpoint required before backup')
        history = History(inspection)
        try:
            inspection.recheck()
            history.recheck()
            with gzip.GzipFile(filename='', mode='wb', compresslevel=9, mtime=0,
                               fileobj=encoded) as archive:
                write_entry(archive, 'enrollment.json', inspection.manifest_data)
                write_entry(archive, 'checkpoint-history.json', history.payload)
                for key, filename in ENROLL.FILENAMES.items():
                    stream_file(archive, inspection, key, filename)
                archive.write(b'\0' * 1024)
                history.recheck()
                inspection.recheck()
            encoded.flush()
            history.recheck()
            inspection.recheck()
            count = len(history.entries)
        finally:
            history.close()
    return {'schema': 1, 'operation': BACKUP.PROFILE, 'success': True, 'name': name,
            'archive': {'bytes': encoded.count, 'sha256': encoded.digest.hexdigest()},
            'manifest_sha256': pin, 'history_count': count,
            'verification': 'file-only', 'clean_shutdown_verified': False,
            'filesystem_consistency_verified': False, 'restore_verified': False,
            'guest_boot_verified': False, 'hardware_acceptance': False}


def stdout_guard(stream):
    mode = os.fstat(stream.fileno()).st_mode
    require(stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode),
            'stdout must be a pipe or socket to a remote receiver')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('name')
    parser.add_argument('--manifest-sha256', required=True)
    args = parser.parse_args(argv)
    stdout_guard(sys.stdout.buffer)
    def interrupted(_signum, _frame):
        raise KeyboardInterrupt
    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        result = send(args.name, args.manifest_sha256, sys.stdout.buffer)
        sys.stderr.write(json.dumps(result, sort_keys=True) + '\n')
        sys.stderr.flush()
        return 0
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, UnicodeError, KeyboardInterrupt) as error:
        print(json.dumps({'success': False, 'error': type(error).__name__,
                          'partial_stream_must_be_discarded': True}), file=sys.stderr)
        sys.exit(1)
