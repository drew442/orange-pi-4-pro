#!/usr/bin/python3
"""Internal checkpoint publication mechanics; caller supplies retained authority.

No CLI or environment authorization bypass belongs here. Managed callers must
independently authenticate runtime shutdown evidence before entering this core.
"""
import base64
import json
import os
import secrets
import signal
import stat
import sys


def directory_check(parent, fd, identity, *, enroll, require):
    ENROLL = enroll
    info = os.fstat(fd)
    require(ENROLL.inode(info) == identity and info.st_uid == info.st_gid == 0 and
            stat.S_IMODE(info.st_mode) == 0o700 and
            info.st_dev == os.fstat(parent).st_dev and ENROLL.same_entry(parent, '.checkpoints', identity),
            'unsafe checkpoint history directory')


def read_archive(fd, expected, *, enroll, require):
    ENROLL = enroll
    info = os.fstat(fd)
    ENROLL.regular(info, 0o400)
    require(info.st_size == len(expected), 'checkpoint history collision')
    os.lseek(fd, 0, os.SEEK_SET)
    require(os.read(fd, len(expected) + 1) == expected and
            ENROLL.identity(os.fstat(fd)) == ENROLL.identity(info), 'checkpoint history collision')


def transaction(inspection, plan, lock_check, authorization, receipt, *,
                enroll, manifest, require, canonical, history_data, history_schema,
                validate_record):
    """Publish a prepared transition under retained caller authority.

    This internal root transaction neither authenticates shutdown evidence nor
    acquires admission locks. The caller retains the stopped inspection, locks,
    prepared plan and an authorization object with recheck() for its lifetime.
    Schema validation is only structural and never grants that authority.
    Unchanged disks need separate durable completion in a managed caller.
    """
    ENROLL, MANIFEST = enroll, manifest

    def same(parent, name, identity):
        return ENROLL.same_entry(parent, name, identity)

    def check_directory(parent, fd, identity):
        directory_check(parent, fd, identity, enroll=ENROLL, require=require)

    def check_archive(fd, expected):
        read_archive(fd, expected, enroll=ENROLL, require=require)

    guest = inspection.guest_fd
    history = history_identity = None
    resources, temporaries = [], []
    manifest_inode = archive_inode = None
    archive_name = None

    def recheck():
        inspection.recheck(owned_entries={name: identity for parent, name, identity in temporaries
                                           if parent == guest and same(parent, name, identity)})
        authorization.recheck()
        lock_check()
        if history is not None:
            check_directory(guest, history, history_identity)

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
        old, new = plan['old_manifest'], plan['proposed_manifest']
        disk = {key: inspection.disk_record[key] for key in ('bytes', 'sha256')}
        require(plan['name'] == inspection.name and plan['context'] == inspection.context and
                old == {**inspection.manifest_record, 'value': inspection.manifest} and
                plan['disk'] == inspection.disk_record and new['value']['config']['disk'] == disk,
                'checkpoint plan differs from retained inspection')
        require(type(plan['requires_update']) is bool and plan['requires_update'] ==
                (disk != inspection.manifest['config']['disk']),
                'checkpoint change classification differs from inspected disk')
        require(type(history_schema) is int and history_schema in (1, 2),
                'invalid checkpoint history version')
        if not plan['requires_update']:
            require(new == old, 'unchanged checkpoint must preserve exact manifest')
            receipt['success'] = True
            receipt['unchanged'] = True
            return
        archive_name = old['sha256'] + '-' + new['sha256'] + '.json'
        archive = history_data
        parsed_old, parsed_new, old_raw = validate_record(archive_name, archive)
        # The shared validator checks transition structure. Bind the exact bytes
        # to the retained inspection and prepared plan before the first write.
        value = json.loads(archive)
        new_raw = MANIFEST.canonical(new['value'])
        require(value['schema'] == history_schema and canonical(value) == archive and
                old_raw == inspection.manifest_data and parsed_old == old['value'] and
                parsed_new == new['value'] and
                base64.b64decode(value['new_manifest_base64'], validate=True) == new_raw and
                old['bytes'] == len(old_raw) and new['bytes'] == len(new_raw),
                'checkpoint history differs from prepared transition')
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
        check_directory(guest, history, history_identity)
        # Also covers retry after interruption between mkdir and parent fsync.
        os.fsync(guest)
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
        check_archive(archive_fd, archive)
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
