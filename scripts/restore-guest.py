#!/usr/bin/python3
"""Restore an independently pinned backup into an absent selected-NVMe guest.

No service, account, mount or guest is started. Host file-engine verification is
not physical A733, KVM, boot or SD-write acceptance evidence.
"""
import argparse
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import signal
import stat
import sys
import uuid
import time

import importlib.util

BASE = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, BASE / path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


SOURCE = module('restore_source', 'scripts/guest-restore-source.py')
POLICY = module('restore_policy', 'scripts/guest-checkpoint-policy.py')
ENROLL = SOURCE.ENROLL
MANIFEST = POLICY.MANIFEST
require, canonical = ENROLL.require, ENROLL.canonical
PROFILE = 'a733-guest-restore-v1'


def record(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def status_uids(data):
    require(type(data) is bytes and 0 < len(data) <= 65536, 'invalid process status size')
    rows = [line.split() for line in data.splitlines() if line.startswith(b'Uid:')]
    require(len(rows) == 1 and len(rows[0]) == 5 and
            all(value.isdigit() for value in rows[0][1:]), 'invalid process UID status')
    return tuple(int(value) for value in rows[0][1:])


def uid_consumers(uid, proc_root=Path('/proc')):
    """Refuse every existing process with any guest UID credential."""
    deadline, count = time.monotonic() + 5, 0
    with os.scandir(proc_root) as processes:
        for process in processes:
            require(time.monotonic() <= deadline, 'process UID census timeout')
            if not process.name.isascii() or not process.name.isdigit():
                continue
            count += 1
            require(count <= 4096, 'too many process entries')
            fd = None
            try:
                fd = os.open(Path(proc_root) / process.name / 'status', os.O_RDONLY | os.O_NOFOLLOW)
                data = bytearray()
                while True:
                    require(time.monotonic() <= deadline, 'process UID census timeout')
                    piece = os.read(fd, 65537 - len(data))
                    if not piece:
                        break
                    data.extend(piece)
                    require(len(data) <= 65536, 'oversized process status')
                require(uid not in status_uids(bytes(data)), 'guest UID has an existing process')
            except OSError as error:
                if error.errno not in (errno.ENOENT, errno.ESRCH):
                    raise
            finally:
                if fd is not None:
                    os.close(fd)


class Review:
    """Guard the namespace before authenticating any archive bytes."""
    def __init__(self, archive, archive_sha256, manifest_sha256, guest_uuid, plan_path=None):
        self.held = []
        self.source = None
        try:
            ENROLL.MANAGEMENT.runtime_guard()
            POLICY.host_namespace()
            require(str(uuid.UUID(guest_uuid)) == guest_uuid and uuid.UUID(guest_uuid).int,
                    'canonical nonzero guest UUID required')
            self.root = self.open(ENROLL.ROOT, directory=True)
            info = self.root.info
            require(info.st_uid == info.st_gid == 0 and stat.S_IMODE(info.st_mode) == 0o711,
                    'guest root must be root:root 0711')
            self.context = ENROLL.STORAGE.verify_guest_directory(self.root.fd)
            ENROLL.probe_uuid(self.context, guest_uuid)
            devices = {os.stat('/').st_dev, os.stat(ENROLL.MANAGEMENT.STATE).st_dev, info.st_dev}
            self.source = SOURCE.Source(archive, archive_sha256, manifest_sha256, devices)
            self.config = self.source.manifest['config']
            self.check_accounts_service_budget()
            ENROLL.absent(self.root.fd, self.config['name'])
            self.manifest = MANIFEST.restored_manifest(self.source.manifest, archive_sha256,
                                                       manifest_sha256, guest_uuid)
            self.manifest_data = MANIFEST.canonical(self.manifest)
            self.required = (sum(self.config[key]['bytes'] for key in ENROLL.FILENAMES) +
                             sum(len(data) for data in self.source.history.values()) +
                             len(self.manifest_data) + ENROLL.RESERVE)
            self.space()
            self.plan_file = self.open(plan_path) if plan_path else None
            self.plan = {
                'schema': 1, 'operation': PROFILE, 'config': self.config,
                'source_guest_uuid': self.source.manifest['guest_uuid'], 'guest_uuid': guest_uuid,
                'storage': self.context,
                'archive': {'path': self.source.held.path,
                            'identity': ENROLL.identity(self.source.held.info),
                            **self.source.archive_record},
                'source_manifest': record(self.source.manifest_data),
                'history': {**record(self.source.history_data), 'record_count': len(self.source.history)},
                'manifest': {**record(self.manifest_data), 'value': self.manifest},
                'required_free_bytes': self.required,
            }
            self.plan['plan_sha256'] = hashlib.sha256(canonical(self.plan)).hexdigest()
            if self.plan_file:
                small = {os.stat('/run').st_dev, os.stat(ENROLL.MANAGEMENT.STATE).st_dev}
                supplied = json.loads(ENROLL.read_small(self.plan_file, small),
                                     object_pairs_hook=ENROLL.CONFIG.IDENTITY.unique_pairs,
                                     parse_constant=ENROLL.CONFIG._reject_constant)
                require(canonical(supplied) == canonical(self.plan), 'stale or mismatched restore plan')
            self.recheck()
        except BaseException:
            self.close()
            raise

    def open(self, path, **kwargs):
        held = ENROLL.HeldPath(path, **kwargs)
        self.held.append(held)
        return held

    def check_accounts_service_budget(self):
        config = self.config
        ENROLL.ACCOUNTS.validate_accounts({'schema': 1, 'guests': [
            {key: config[key] for key in ('name', 'uid', 'gid')}]},
            ENROLL.ACCOUNTS.snapshot(), complete=True)
        POLICY.service_state(config['name'])
        uid_consumers(config['uid'])
        ENROLL.budget(config)

    def space(self):
        space = os.fstatvfs(self.root.fd)
        require(space.f_bavail * space.f_frsize >= self.required, 'insufficient guest free space')

    def recheck(self):
        ENROLL.MANAGEMENT.runtime_guard()
        POLICY.host_namespace()
        info = os.fstat(self.root.fd)
        require(ENROLL.inode(info) == ENROLL.inode(self.root.info) and
                info.st_uid == info.st_gid == 0 and stat.S_IMODE(info.st_mode) == 0o711,
                'guest root changed')
        self.root.info = info
        for held in self.held:
            held.recheck()
        self.source.recheck()
        require(ENROLL.STORAGE.verify_guest_directory(self.root.fd) == self.context,
                'storage namespace changed')
        ENROLL.probe_uuid(self.context, self.plan['guest_uuid'])
        self.check_accounts_service_budget()
        ENROLL.absent(self.root.fd, self.config['name'])

    def close(self):
        resources = [self.source] + list(reversed(self.held))
        self.source, self.held = None, []
        error = None
        for resource in resources:
            if resource is not None:
                try:
                    resource.close()
                except BaseException as caught:
                    error = caught
        if error:
            raise error


def prepare(archive, archive_sha256, manifest_sha256, guest_uuid):
    review = Review(archive, archive_sha256, manifest_sha256, guest_uuid)
    try:
        return review.plan
    finally:
        review.close()


def authorize(plan):
    require(sys.stdin.isatty() and sys.stderr.isatty(), 'apply requires an interactive TTY')
    phrase = (f"RESTORE {plan['config']['name']} {plan['archive']['sha256']} "
              f"{plan['guest_uuid']} {plan['plan_sha256']}")
    for prompt, expected in (('Confirm independently verified source backup', 'SOURCE BACKUP VERIFIED'),
                             ('Authorize fresh guest restore', phrase)):
        sys.stderr.write(prompt + ': ' + expected + '\n> ')
        sys.stderr.flush()
        require(sys.stdin.readline().rstrip('\n') == expected, 'authorization phrase differs')


def readback(fd, expected):
    digest, count = hashlib.sha256(), 0
    while True:
        data = os.pread(fd, min(ENROLL.CHUNK, expected['bytes'] - count + 1), count)
        if not data:
            break
        count += len(data)
        require(count <= expected['bytes'], 'staged inode grew')
        digest.update(data)
    require(count == expected['bytes'] and digest.hexdigest() == expected['sha256'],
            'staged readback differs')


def stage_and_publish(review, lock_check, receipt):
    root, config = review.root.fd, review.config
    private = stage = history = None
    private_inode = stage_inode = history_inode = None
    temporary = secrets.token_hex(16)
    # Each entry retains its FD until after publication or conservative cleanup.
    owned = []

    def create(parent, name, expected, uid=0, gid=0, mode=0o400):
        fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        entry = [parent, name, fd, ENROLL.inode(os.fstat(fd)), expected, uid, gid, mode]
        owned.append(entry)
        return fd

    def finish(fd, expected, uid=0, gid=0, mode=0o400):
        os.ftruncate(fd, expected['bytes'])
        readback(fd, expected)
        os.fchown(fd, uid, gid)
        os.fchmod(fd, mode)
        os.fsync(fd)

    def directory(fd, expected, gid, mode):
        info = os.fstat(fd)
        require(ENROLL.inode(info) == expected and info.st_uid == 0 and info.st_gid == gid and
                stat.S_IMODE(info.st_mode) == mode and info.st_dev == os.fstat(root).st_dev,
                'staging directory metadata changed')

    def reconcile():
        if stage_inode is not None and ENROLL.same_entry(root, config['name'], stage_inode):
            receipt['published'] = True
            receipt['staging_retained'] = False

    try:
        review.recheck()
        review.space()
        lock_check()
        try:
            os.mkdir('.enrollment', 0o700, dir_fd=root)
        except FileExistsError:
            pass
        private = os.open('.enrollment', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
        private_inode = ENROLL.inode(os.fstat(private))
        directory(private, private_inode, 0, 0o700)
        require(ENROLL.same_entry(root, '.enrollment', private_inode), 'staging parent changed')
        os.mkdir(temporary, 0o700, dir_fd=private)
        receipt['staging_retained'] = True
        stage = os.open(temporary, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=private)
        stage_inode = ENROLL.inode(os.fstat(stage))
        directory(stage, stage_inode, 0, 0o700)
        payloads = {}
        for key, name in ENROLL.FILENAMES.items():
            uid, gid, mode = (config['uid'], config['gid'], 0o600) if key == 'disk' else (0, 0, 0o444)
            payloads[name] = create(stage, name, config[key], uid, gid, mode)
        counts = dict.fromkeys(payloads, 0)

        def emit(name, data):
            require(name in payloads and type(data) is bytes and 0 < len(data) <= ENROLL.CHUNK,
                    'invalid source callback')
            fd = payloads[name]
            counts[name] += len(data)
            expected = review.manifest['files'][name]
            require(counts[name] <= expected['bytes'], 'source callback exceeds payload')
            receipt['bytes_attempted_staged'] += len(data)
            if data.strip(b'\0'):
                ENROLL.write_all(fd, data)
            else:
                os.lseek(fd, len(data), os.SEEK_CUR)

        review.source.scan(emit=emit)
        for key, name in ENROLL.FILENAMES.items():
            require(counts[name] == config[key]['bytes'], 'incomplete payload callback')
            uid, gid, mode = (config['uid'], config['gid'], 0o600) if key == 'disk' else (0, 0, 0o444)
            finish(payloads[name], config[key], uid, gid, mode)
        if review.source.history:
            os.mkdir('.checkpoints', 0o700, dir_fd=stage)
            history = os.open('.checkpoints', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=stage)
            history_inode = ENROLL.inode(os.fstat(history))
            directory(history, history_inode, 0, 0o700)
            for name, data in review.source.history.items():
                expected = record(data)
                fd = create(history, name, expected)
                ENROLL.write_all(fd, data)
                finish(fd, expected)
                os.close(fd)
                owned[-1][2] = None
            os.fsync(history)
        expected = record(review.manifest_data)
        fd = create(stage, 'enrollment.json', expected)
        ENROLL.write_all(fd, review.manifest_data)
        finish(fd, expected)
        receipt['manifest_sha256'] = expected['sha256']
        os.fchown(stage, 0, config['gid'])
        os.fchmod(stage, 0o710)
        os.fsync(stage)
        review.recheck()
        lock_check()
        directory(private, private_inode, 0, 0o700)
        directory(stage, stage_inode, config['gid'], 0o710)
        require(ENROLL.same_entry(root, '.enrollment', private_inode) and
                ENROLL.same_entry(private, temporary, stage_inode), 'staging namespace changed')
        for parent in (stage, history):
            if parent is None:
                continue
            entries = {entry[1] for entry in owned if entry[0] == parent}
            if parent == stage and history is not None:
                entries.add('.checkpoints')
            require(set(os.listdir(parent)) == entries, 'unexpected staged entry')
        if history is not None:
            directory(history, history_inode, 0, 0o700)
            require(ENROLL.same_entry(stage, '.checkpoints', history_inode), 'history replaced')
        for parent, name, fd, identity, expected, uid, gid, mode in owned:
            reopened = fd is None
            if reopened:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            try:
                info = os.fstat(fd)
                require(ENROLL.same_entry(parent, name, identity) and ENROLL.inode(info) == identity and
                        stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and
                        info.st_uid == uid and info.st_gid == gid and stat.S_IMODE(info.st_mode) == mode and
                        info.st_size == expected['bytes'], 'staged file metadata changed')
                if reopened:
                    readback(fd, expected)
                    require(ENROLL.identity(os.fstat(fd)) == ENROLL.identity(info) and
                            ENROLL.same_entry(parent, name, identity), 'staged history changed')
            finally:
                if reopened:
                    os.close(fd)
        previous = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM})
        try:
            receipt['directory_may_be_published'] = True
            try:
                ENROLL.rename_new(private, temporary, root, config['name'])
                receipt['published'] = True
                receipt['staging_retained'] = False
                os.fsync(root)
                os.fsync(private)
                receipt['durable'] = True
            finally:
                reconcile()
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous)
        receipt['success'] = True
    finally:
        try:
            reconcile()
            if stage is not None and not receipt['published'] and ENROLL.same_entry(private, temporary, stage_inode):
                for parent, name, _fd, identity, *_rest in reversed(owned):
                    if ENROLL.same_entry(parent, name, identity):
                        os.unlink(name, dir_fd=parent)
                if history is not None and ENROLL.same_entry(stage, '.checkpoints', history_inode):
                    try:
                        os.rmdir('.checkpoints', dir_fd=stage)
                    except OSError:
                        pass
                if ENROLL.same_entry(private, temporary, stage_inode):
                    try:
                        os.rmdir(temporary, dir_fd=private)
                        receipt['staging_retained'] = False
                    except OSError:
                        pass
        except BaseException as error:
            receipt['success'] = False
            receipt['cleanup_error'] = type(error).__name__
        finally:
            for fd in [entry[2] for entry in reversed(owned)] + [history, stage, private]:
                if fd is not None:
                    try:
                        os.close(fd)
                    except BaseException as error:
                        receipt['success'] = False
                        receipt['cleanup_error'] = type(error).__name__


def apply(archive, archive_sha256, manifest_sha256, guest_uuid, plan_path):
    receipt = {'schema': 1, 'operation': PROFILE, 'success': False, 'published': False,
               'durable': False, 'staging_retained': False, 'directory_may_be_published': False,
               'bytes_attempted_staged': 0, 'archive_sha256': archive_sha256,
               'source_manifest_sha256': manifest_sha256}
    lock = review = None
    try:
        ENROLL.MANAGEMENT.runtime_guard()
        lock = os.open(ENROLL.LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        info = os.fstat(lock)
        ENROLL.regular(info, 0o600)
        require(info.st_dev == os.stat('/run').st_dev, 'enrollment lock must reside on RAM')
        def lock_check():
            require(ENROLL.identity(os.fstat(lock)) == ENROLL.identity(info) and
                    ENROLL.identity(os.lstat(ENROLL.LOCK)) == ENROLL.identity(info), 'enrollment lock changed')
        lock_check()
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        review = Review(archive, archive_sha256, manifest_sha256, guest_uuid, plan_path)
        receipt['plan_sha256'] = review.plan['plan_sha256']
        receipt['manifest_sha256'] = review.plan['manifest']['sha256']
        authorize(review.plan)
        stage_and_publish(review, lock_check, receipt)
    except BaseException as error:
        receipt['success'] = False
        receipt['error'] = type(error).__name__
    finally:
        for resource in (review, lock):
            if resource is not None:
                try:
                    os.close(resource) if isinstance(resource, int) else resource.close()
                except BaseException as error:
                    receipt['success'] = False
                    receipt['cleanup_error'] = type(error).__name__
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for action in ('prepare', 'apply'):
        command = commands.add_parser(action)
        for name in ('archive', 'archive-sha256', 'manifest-sha256', 'guest-uuid'):
            command.add_argument('--' + name, required=True)
        if action == 'apply':
            command.add_argument('--plan', required=True)
    args = parser.parse_args(argv)
    os.umask(0o077)
    def interrupted(_signum, _frame):
        raise KeyboardInterrupt
    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        arguments = (args.archive, args.archive_sha256, args.manifest_sha256, args.guest_uuid)
        result = apply(*arguments, args.plan) if args.command == 'apply' else prepare(*arguments)
        print(canonical(result).decode(), end='')
        return 0 if result.get('success', True) else 1
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, UnicodeError, KeyboardInterrupt) as error:
        print(json.dumps({'success': False, 'error': type(error).__name__}), file=sys.stderr)
        sys.exit(1)
