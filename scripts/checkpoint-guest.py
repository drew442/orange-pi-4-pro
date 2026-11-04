#!/usr/bin/python3
"""Explicit stopped-guest manifest checkpoint; never writes guest payload bytes.

A checkpoint attests the operator's clean shutdown verification. It does not
inspect filesystem cleanliness, create a backup, or establish hardware acceptance.
"""
import argparse
import base64
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import signal
import stat
import sys

BASE = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, BASE / path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


POLICY = module('checkpoint_policy', 'scripts/guest-checkpoint-policy.py')
ENROLL = POLICY.ENROLL
MANIFEST = POLICY.MANIFEST
require = ENROLL.require
canonical = ENROLL.canonical
PROFILE = 'a733-guest-checkpoint-v1'
LOCK = '/run/a733-guest-checkpoint.lock'


def record(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def make_plan(inspection):
    old = inspection.manifest_record
    disk = {key: inspection.disk_record[key] for key in ('bytes', 'sha256')}
    proposed = MANIFEST.proposed_manifest(inspection.manifest, old['sha256'], disk)
    changed = disk != inspection.manifest['config']['disk']
    encoded = MANIFEST.canonical(proposed) if changed else inspection.manifest_data
    result = {'schema': 1, 'operation': PROFILE, 'name': inspection.name,
              'context': inspection.context, 'disk': inspection.disk_record,
              'old_manifest': {**old, 'value': inspection.manifest},
              'proposed_manifest': {**record(encoded), 'value': proposed},
              'requires_update': changed}
    result['plan_sha256'] = hashlib.sha256(canonical(result)).hexdigest()
    return result


def prepare(name, manifest_sha256):
    with POLICY.Inspection(name, manifest_sha256) as inspection:
        result = make_plan(inspection)
        inspection.recheck()
        return result


def authorize(plan):
    require(sys.stdin.isatty() and sys.stderr.isatty(), 'apply requires an interactive TTY')
    phrase = f"CHECKPOINT {plan['name']} {plan['disk']['sha256']} {plan['plan_sha256']}"
    for prompt, expected in (('Confirm independently verified clean guest shutdown',
                              'CLEAN SHUTDOWN VERIFIED'),
                             ('Authorize manifest checkpoint', phrase)):
        sys.stderr.write(prompt + ': ' + expected + '\n> ')
        sys.stderr.flush()
        require(sys.stdin.readline().rstrip('\n') == expected, 'authorization phrase differs')


def same(parent, name, identity):
    return ENROLL.same_entry(parent, name, identity)


def directory_check(parent, fd, identity):
    info = os.fstat(fd)
    require(ENROLL.inode(info) == identity and info.st_uid == info.st_gid == 0 and
            stat.S_IMODE(info.st_mode) == 0o700 and
            info.st_dev == os.fstat(parent).st_dev and same(parent, '.checkpoints', identity),
            'unsafe checkpoint history directory')


def read_archive(fd, expected):
    info = os.fstat(fd)
    ENROLL.regular(info, 0o400)
    require(info.st_size == len(expected), 'checkpoint history collision')
    os.lseek(fd, 0, os.SEEK_SET)
    require(os.read(fd, len(expected) + 1) == expected and
            ENROLL.identity(os.fstat(fd)) == ENROLL.identity(info), 'checkpoint history collision')


def transaction(inspection, plan, lock_check, review, receipt):
    guest = inspection.guest_fd
    history = history_identity = None
    resources, temporaries = [], []
    manifest_inode = archive_inode = None
    archive_name = None

    def recheck():
        inspection.recheck(owned_entries={name: identity for parent, name, identity in temporaries
                                           if parent == guest and same(parent, name, identity)})
        review.recheck()
        lock_check()
        if history is not None:
            directory_check(guest, history, history_identity)

    def create(parent, payload):
        name = '.checkpoint-' + secrets.token_hex(16)
        fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        resources.append(fd)
        identity = ENROLL.inode(os.fstat(fd))
        temporaries.append((parent, name, identity))
        ENROLL.write_all(fd, payload)
        os.fchown(fd, 0, 0)
        os.fchmod(fd, 0o400)
        os.fsync(fd)
        ENROLL.regular(os.fstat(fd), 0o400)
        require(same(parent, name, identity), 'checkpoint temporary replaced')
        return name, fd, identity

    def reconcile():
        if manifest_inode is not None and same(guest, 'enrollment.json', manifest_inode):
            receipt['published'] = True
        if archive_inode is not None and same(history, archive_name, archive_inode):
            receipt['history_published'] = True

    try:
        recheck()
        if not plan['requires_update']:
            receipt['success'] = True
            receipt['unchanged'] = True
            return
        # Emit intent before the first flush or write; a failed call may have acted.
        receipt['disk_flush_attempted'] = True
        receipt['metadata_may_be_modified'] = True
        sys.stderr.write(canonical({'checkpoint_intent': receipt}).decode())
        sys.stderr.flush()
        os.fsync(inspection.disk_fd)
        recheck()
        try:
            os.mkdir('.checkpoints', 0o700, dir_fd=guest)
        except FileExistsError:
            pass
        history = os.open('.checkpoints', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=guest)
        resources.append(history)
        history_identity = ENROLL.inode(os.fstat(history))
        directory_check(guest, history, history_identity)
        # Also covers retry after interruption between mkdir and parent fsync.
        os.fsync(guest)
        old, new = plan['old_manifest'], plan['proposed_manifest']
        archive_name = old['sha256'] + '-' + new['sha256'] + '.json'
        archive = canonical({'schema': 1, 'operation': PROFILE,
                             'old_manifest': old['value'], 'new_manifest': new['value'],
                             'old_manifest_sha256': old['sha256'],
                             'new_manifest_sha256': new['sha256'],
                             'old_manifest_base64': base64.b64encode(inspection.manifest_data).decode('ascii'),
                             'new_manifest_base64': base64.b64encode(MANIFEST.canonical(new['value'])).decode('ascii')})
        try:
            archive_fd = os.open(archive_name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 dir_fd=history)
        except FileNotFoundError:
            temporary, archive_fd, archive_inode = create(history, archive)
            recheck()
            # Retain the known inode even when rename acts and then raises.
            ENROLL.rename_new(history, temporary, history, archive_name)
        else:
            resources.append(archive_fd)
        read_archive(archive_fd, archive)
        require(os.fstat(archive_fd).st_dev == os.fstat(history).st_dev,
                'checkpoint archive on different filesystem')
        archive_inode = ENROLL.inode(os.fstat(archive_fd))
        require(same(history, archive_name, archive_inode), 'checkpoint archive replaced')
        receipt['history_published'] = True
        os.fsync(archive_fd)
        os.fsync(history)
        receipt['history_durable'] = True
        temporary, manifest_fd, manifest_inode = create(guest, MANIFEST.canonical(new['value']))
        recheck()
        previous = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM})
        try:
            recheck()
            require(same(guest, temporary, manifest_inode), 'manifest temporary replaced')
            receipt['manifest_replace_attempted'] = True
            try:
                os.replace(temporary, 'enrollment.json', src_dir_fd=guest, dst_dir_fd=guest)
                receipt['published'] = True
                os.fsync(guest)
                receipt['durable'] = True
            finally:
                reconcile()
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous)
        receipt['success'] = True
    finally:
        # Reconcile before cleanup/unmask/close errors can obscure publication.
        try:
            reconcile()
        finally:
            for parent, name, identity in reversed(temporaries):
                try:
                    if same(parent, name, identity):
                        os.unlink(name, dir_fd=parent)
                except BaseException as error:
                    receipt['cleanup_error'] = type(error).__name__
                    receipt['success'] = False
            for fd in reversed(resources):
                try:
                    os.close(fd)
                except BaseException as error:
                    receipt['cleanup_error'] = type(error).__name__
                    receipt['success'] = False


def apply(name, manifest_sha256, plan_path):
    receipt = {'schema': 1, 'operation': PROFILE, 'success': False,
               'published': False, 'durable': False, 'history_published': False,
               'history_durable': False, 'disk_flush_attempted': False,
               'metadata_may_be_modified': False, 'manifest_replace_attempted': False}
    lock = inspection = review = None
    try:
        ENROLL.MANAGEMENT.runtime_guard()
        lock = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        info = os.fstat(lock)
        ENROLL.regular(info, 0o600)
        require(info.st_dev == os.stat('/run').st_dev, 'checkpoint lock must reside on RAM')
        def lock_check():
            require(ENROLL.identity(os.fstat(lock)) == ENROLL.identity(info) and
                    ENROLL.identity(os.lstat(LOCK)) == ENROLL.identity(info), 'checkpoint lock changed')
        lock_check()
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        inspection = POLICY.Inspection(name, manifest_sha256)
        plan = make_plan(inspection)
        review = ENROLL.HeldPath(plan_path)
        devices = {os.stat('/run').st_dev, os.stat(ENROLL.MANAGEMENT.STATE).st_dev}
        supplied = json.loads(ENROLL.read_small(review, devices),
                              object_pairs_hook=ENROLL.CONFIG.IDENTITY.unique_pairs,
                              parse_constant=ENROLL.CONFIG._reject_constant)
        require(canonical(supplied) == canonical(plan), 'stale or mismatched checkpoint plan')
        receipt.update({'name': name, 'plan_sha256': plan['plan_sha256'],
                        'old_manifest_sha256': plan['old_manifest']['sha256'],
                        'new_manifest_sha256': plan['proposed_manifest']['sha256'],
                        'new_disk_sha256': plan['disk']['sha256']})
        authorize(plan)
        transaction(inspection, plan, lock_check, review, receipt)
    except BaseException as error:
        receipt['success'] = False
        receipt['error'] = type(error).__name__
    finally:
        for resource in (review, inspection, lock):
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
        command.add_argument('--name', required=True)
        command.add_argument('--manifest-sha256', required=True)
        if action == 'apply':
            command.add_argument('--plan', required=True)
    args = parser.parse_args(argv)
    os.umask(0o077)
    def interrupted(_signum, _frame):
        raise KeyboardInterrupt
    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        result = (apply(args.name, args.manifest_sha256, args.plan) if args.command == 'apply'
                  else prepare(args.name, args.manifest_sha256))
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
