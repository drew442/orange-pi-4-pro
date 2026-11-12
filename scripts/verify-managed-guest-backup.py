#!/usr/bin/python3
"""Authenticate managed backups into NEW private artifact directories only.

No guest storage, registry mutation, root restore publication or admission is
provided. The caller supplies trusted archive and enrollment SHA-256 pins.
"""
import argparse
import importlib.util
import os
from pathlib import Path
import signal
import secrets
import stat
import sys

BASE = Path(__file__).resolve().parents[1]


def source_module():
    # Lazy import keeps source/scanner independent of the artifact receiver.
    spec = importlib.util.spec_from_file_location(
        'managed_receiver_source', BASE / 'scripts/managed-guest-restore-source.py')
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


SOURCE = source_module()
FORMAT, GENERIC = SOURCE.FORMAT, SOURCE.GENERIC
STATE = GENERIC.STATE
require = FORMAT.require
Parent = GENERIC.Parent
capture, readback, record = GENERIC.capture, GENERIC.readback, GENERIC.record
write_all, free_space = GENERIC.write_all, GENERIC.free_space
same, absent, rename_new = GENERIC.same, GENERIC.absent, GENERIC.rename_new
RESERVE = GENERIC.RESERVE


def cleanup_error(receipt, error):
    receipt['success'] = False
    receipt.setdefault('cleanup_error', type(error).__name__)
    receipt.setdefault('cleanup_errors', []).append(type(error).__name__)


class Stage(GENERIC.Stage):
    """Managed staging with exception-safe construction and exact-inode cleanup."""
    def __init__(self, parent, receipt=None):
        self.parent = parent
        self.receipt = {} if receipt is None else receipt
        self.name = '.guest-backup-' + secrets.token_hex(16)
        self.entries, self.fds, self.unknown_paths = [], [], []
        self.fd = self.identity = None
        try:
            os.mkdir(self.name, 0o700, dir_fd=parent.fd)
            # Capture identity before opening: failed open must not orphan mkdir.
            info = os.stat(self.name, dir_fd=parent.fd, follow_symlinks=False)
            self.identity = GENERIC.inode(info)
            require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid() and
                    stat.S_IMODE(info.st_mode) == 0o700, 'unsafe created stage')
            self.fd = os.open(self.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                              dir_fd=parent.fd)
            self.fds.append(self.fd)
            require(GENERIC.inode(os.fstat(self.fd)) == self.identity, 'created stage replaced')
            self.recheck()
        except BaseException:
            if self.identity is None:
                self.unknown_paths.append(str(parent.path / self.name))
            try:
                self.close(False)
            except BaseException as error:
                cleanup_error(self.receipt, error)
                self.retain()
            raise

    def history_directory(self):
        try:
            os.mkdir('.checkpoints', 0o700, dir_fd=self.fd)
            info = os.stat('.checkpoints', dir_fd=self.fd, follow_symlinks=False)
        except BaseException:
            self.unknown_paths.append(str(self.parent.path / self.name / '.checkpoints'))
            raise
        identity = GENERIC.inode(info)
        self.entries.append((self.fd, '.checkpoints', identity, True))
        fd = os.open('.checkpoints', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                     dir_fd=self.fd)
        self.fds.append(fd)
        require(GENERIC.inode(os.fstat(fd)) == identity, 'created history replaced')
        return fd

    def retain(self):
        # These are candidate locations with an expected inode, not new custody
        # observations. A failed stat must not manufacture a publication result.
        self.receipt['retained_artifacts'] = {
            'stage_path': str(self.parent.path / self.name),
            'output_path': str(self.parent.path / self.parent.name),
            'expected_identity': list(self.identity) if self.identity is not None else None}

    def close(self, published):
        if published:
            if self.receipt.get('publication_uncertain') or not self.receipt.get('success'):
                self.retain()
        else:
            for parent, name, identity, isdir in reversed(self.entries):
                try:
                    if GENERIC.same(parent, name, identity):
                        (os.rmdir if isdir else os.unlink)(name, dir_fd=parent)
                    else:
                        self.retain()
                except BaseException as error:
                    cleanup_error(self.receipt, error)
                    self.retain()
            if self.identity is not None:
                try:
                    if GENERIC.same(self.parent.fd, self.name, self.identity):
                        os.rmdir(self.name, dir_fd=self.parent.fd)
                    else:
                        self.retain()
                except BaseException as error:
                    cleanup_error(self.receipt, error)
                    self.retain()
        if self.unknown_paths:
            self.receipt['retained_unknown_paths'] = list(self.unknown_paths)
        # Never let a path observation failure skip descriptor closure.
        for fd in reversed(self.fds):
            try:
                os.close(fd)
            except BaseException as error:
                cleanup_error(self.receipt, error)
        self.fds.clear()


def observe_publication(parent, stage, receipt):
    try:
        observed = same(parent.fd, parent.name, stage.identity)
        if receipt['published'] and not observed:
            receipt['success'] = False
            receipt['publication_uncertain'] = True
            receipt['publication_identity_changed'] = True
            stage.retain()
        receipt['published'] = receipt['published'] or observed
    except BaseException as error:
        receipt['success'] = False
        receipt['publication_uncertain'] = True
        receipt.setdefault('publication_observation_errors', []).append(type(error).__name__)
        stage.retain()


def extract(stage, archive_fd, encoded_size, manifest_sha):
    """Extract only after capture authenticated every encoded byte."""
    files, payloads = {}, {}
    def metadata_ready(exact, manifest, history, _cohort):
        free_space(stage.fd, sum(item['bytes'] for item in manifest['files'].values()) +
                   sum(map(len, exact)) + sum(map(len, history.values())) + RESERVE)
        for name, data in zip(FORMAT.NAMES[:3], exact):
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
        for name in FORMAT.NAMES[3:]:
            fd = stage.create(name)
            expected = {key: manifest['files'][name][key] for key in ('bytes', 'sha256')}
            payloads[name] = fd
            files[name] = (fd, expected)

    def emit(name, chunk):
        fd = payloads[name]
        if chunk == bytes(len(chunk)):
            os.lseek(fd, len(chunk), os.SEEK_CUR)
        else:
            write_all(fd, chunk)

    _exact, _manifest, history, _cohort = SOURCE.scan_fd(
        archive_fd, manifest_sha, emit, metadata_ready=metadata_ready)
    for name, fd in payloads.items():
        os.ftruncate(fd, files[name][1]['bytes'])
        stage.finish(fd)
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
        stage = Stage(parent, receipt)
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
        data = FORMAT.BACKUP.canonical(verification)
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
            except BaseException as error:
                receipt.setdefault('error', type(error).__name__)
                raise
            finally:
                observe_publication(parent, stage, receipt)
            require(receipt['published'], 'backup publication missing')
            os.fsync(parent.fd)
            receipt['durable'] = True
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous)
        receipt['success'] = not receipt.get('publication_uncertain', False)
    except BaseException as error:
        receipt.setdefault('error', type(error).__name__)
    finally:
        if parent is not None and stage is not None:
            observe_publication(parent, stage, receipt)
        for resource in (stage, parent):
            if resource is not None:
                try:
                    resource.close(receipt['published'] or receipt.get('publication_uncertain', False)) \
                        if resource is stage else resource.close()
                except BaseException as error:
                    cleanup_error(receipt, error)
                    if stage is not None:
                        stage.retain()
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
        print(FORMAT.BACKUP.canonical(result).decode(), end='')
        return 0 if result['success'] else 1
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == '__main__':
    sys.exit(main())
