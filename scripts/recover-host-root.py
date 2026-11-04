#!/usr/bin/env python3
"""Recover next-boot NVMe root selection from the RAM installer.

Requires the complete trusted checkout on RAM or external storage. State journal
replay is authorized explicitly; root blocks, boot firmware and SD are never
written. This is no boot, health, KVM or physical recovery acceptance proof.
"""
import argparse
import contextlib
import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import uuid


def adjacent(name, filename):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


CONTROL = adjacent('recovery_selection', 'select-host-root.py')
Manifest = CONTROL.Manifest
UPDATE, PREP, WRITER, SELECT = CONTROL.UPDATE, CONTROL.PREP, CONTROL.WRITER, CONTROL.SELECT
require = PREP.require
MOUNTPOINT = Path('/run/a733-recovery-state')
LOCK = Path('/run/a733-recovery.lock')


def snapshot(args, mounted_state=False):
    policy = adjacent('recovery_target', 'host-recovery-target.py')
    return policy.snapshot(args.target, args.expected_model, args.expected_serial,
                           args.expected_bytes, args.state_uuid, args.slot, mounted_state)


def source_check(manifest, disk):
    manifest.check()
    for fd in (manifest.directory, manifest.stream.fileno()):
        UPDATE.storage_guard(fd)
        WRITER.source_off_target(fd, disk['major_minor'])


def check_context(args, manifest, context, mounted_state=False):
    source_check(manifest, context['disk'])
    require(snapshot(args, mounted_state) == context, 'offline recovery context changed')


@contextlib.contextmanager
def block_stream(target, exclusive=True):
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    if exclusive:
        flags |= os.O_EXCL
    fd = os.open(target['canonical_path'], flags)
    with os.fdopen(fd, 'rb') as stream:
        WRITER.validate_fd(fd, target)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield stream
        WRITER.validate_fd(fd, target)


def authenticate_root(stream, args, manifest, context, mounted_state=False):
    require(manifest.expected['bytes'] == context['target']['size_bytes'],
            'root byte budget differs')
    check_context(args, manifest, context, mounted_state)
    WRITER.validate_fd(stream.fileno(), context['target'])
    WRITER.hash_stream(stream, manifest.expected)
    check_context(args, manifest, context, mounted_state)
    WRITER.validate_fd(stream.fileno(), context['target'])
    stream.seek(0)
    proposed, _ = PREP.compatibility(stream, args.slot, manifest.value)
    require(SELECT.parse(proposed)['SLOT'] == args.slot, 'authenticated root slot differs')
    check_context(args, manifest, context, mounted_state)
    return proposed


def state_image(stream, context):
    size = context['state']['size_bytes']
    stream.seek(0)
    result, length = hashlib.sha256(), 0
    while True:
        data = stream.read(min(4 * 1024**2, size - length + 1))
        if not data:
            break
        length += len(data)
        require(length <= size, 'state image exceeds expected size')
        result.update(data)
    require(length == size, 'state image size differs')
    WRITER.validate_fd(stream.fileno(), context['state'])
    return {'bytes': length, 'sha256': result.hexdigest()}


def make_plan(args, manifest):
    context = snapshot(args)
    source_check(manifest, context['disk'])
    with block_stream(context['target']) as root:
        proposed = authenticate_root(root, args, manifest, context)
        with block_stream(context['state']) as state:
            before = state_image(state, context)
            check_context(args, manifest, context)
    result = {'schema': 1, 'operation': 'offline-root-selection-recovery',
              'manifest': manifest.review, 'context': context,
              'expected_root': manifest.expected, 'state_image_before': before,
              'proposed_selector': SELECT.parse(proposed),
              'proposed_selector_sha256': WRITER.digest(proposed),
              'warning': 'State journal replay may change the observed selection, even if mounting fails. Independently verified off-device backup required. Selection metadata only; no boot, health, KVM or physical recovery proof.'}
    result['plan_sha256'] = WRITER.digest(WRITER.canonical_json(result))
    return result


def authorize(plan, input_stream=None, output_stream=None):
    incoming = sys.stdin if input_stream is None else input_stream
    outgoing = sys.stderr if output_stream is None else output_stream
    require(incoming.isatty() and outgoing.isatty(), 'apply requires an interactive TTY')
    target = plan['context']['target']
    phrase = (f"RECOVER {plan['manifest']['slot']} {target['serial']} "
              f"{target['canonical_path']} {plan['context']['state']['uuid']} {plan['plan_sha256']}")
    for prompt, expected in (('Confirm independently verified off-device backup', 'BACKUP VERIFIED'),
                             ('Authorize state journal replay and selection recovery', phrase)):
        outgoing.write(prompt + ': ' + expected + '\n> ')
        outgoing.flush()
        require(incoming.readline().rstrip('\n') == expected, 'authorization phrase does not match')


@contextlib.contextmanager
def recovery_lock():
    with PREP.safe_parent(LOCK.parent) as parent:
        fd = os.open(LOCK.name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                     0o600, dir_fd=parent)
        try:
            info = os.fstat(fd)
            require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_gid == 0
                    and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600
                    and info.st_dev == os.fstat(parent).st_dev, 'unsafe recovery lock')
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            named = os.stat(LOCK.name, dir_fd=parent, follow_symlinks=False)
            require((named.st_dev, named.st_ino) == (info.st_dev, info.st_ino),
                    'recovery lock pathname changed')
            yield fd
        finally:
            os.close(fd)


def private_namespace():
    library = ctypes.CDLL(None, use_errno=True)
    if library.unshare(0x00020000) != 0:  # CLONE_NEWNS
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    subprocess.run(['mount', '--make-rprivate', '/'], check=True)


def mount_entries():
    return [line.split(' - ', 1)[0].split()
            for line in Path('/proc/self/mountinfo').read_text().splitlines()]


def own_state_mounted(context):
    entries = [entry for entry in mount_entries() if entry[4] == str(MOUNTPOINT)]
    require(len(entries) <= 1, 'ambiguous recovery mountpoint')
    if not entries:
        return False
    require(entries[0][2] == context['state']['major_minor'],
            'foreign filesystem at recovery mountpoint; inspect manually')
    return True


def mount_directory():
    """Hold the RAM parent and optionally create one empty, owned directory."""
    stack = contextlib.ExitStack()
    try:
        parent = stack.enter_context(PREP.safe_parent(MOUNTPOINT.parent))
        require(not any(entry[4] == str(MOUNTPOINT) for entry in mount_entries()),
                'recovery mountpoint already mounted')
        created = False
        try:
            os.mkdir(MOUNTPOINT.name, 0o700, dir_fd=parent)
            created = True
        except FileExistsError:
            pass
        fd = os.open(MOUNTPOINT.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                     dir_fd=parent)
        stack.callback(os.close, fd)
        info = os.fstat(fd)
        require(info.st_uid == 0 and info.st_gid == 0
                and stat.S_IMODE(info.st_mode) == 0o700 and not os.listdir(fd)
                and info.st_dev == os.fstat(parent).st_dev,
                'recovery mountpoint requires an empty root-owned mode 0700 RAM directory')
        return stack, parent, (info.st_dev, info.st_ino), created
    except BaseException:
        stack.close()
        raise


def checked_record(directory, name, limit):
    return CONTROL.secure_read(directory, name, limit)


def selector_before(boot):
    try:
        before = checked_record(boot, 'selected.conf', 4096)
    except FileNotFoundError:
        return None, False
    try:
        SELECT.parse(before)
        valid = True
    except (ValueError, UnicodeError, KeyError, TypeError):
        valid = False
    return before, valid


def validate_marker(state, expected_uuid):
    fd = os.open('.a733-state', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=state)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_gid == 0
                and info.st_nlink == 1 and not info.st_mode & 0o022
                and info.st_dev == os.fstat(state).st_dev, 'unsafe state marker')
        identity = WRITER.file_identity(fd)
        data = stream.read(257)
        require(data == f'a733-state-v1\n{expected_uuid}\n'.encode()
                and WRITER.file_identity(fd) == identity, 'state marker differs')
        named = os.stat('.a733-state', dir_fd=state, follow_symlinks=False)
        require((named.st_dev, named.st_ino) == (info.st_dev, info.st_ino),
                'state marker pathname changed')


@contextlib.contextmanager
def boot_directory(context):
    with PREP.safe_parent(MOUNTPOINT) as state:
        info = os.fstat(state)
        require(f'{os.major(info.st_dev)}:{os.minor(info.st_dev)}' ==
                context['state']['major_minor'], 'mounted state device differs')
        validate_marker(state, context['state']['uuid'])
        try:
            os.mkdir('boot', 0o700, dir_fd=state)
            os.fsync(state)
        except FileExistsError:
            pass
        boot = os.open('boot', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=state)
        try:
            info = os.fstat(boot)
            require(info.st_uid == 0 and info.st_gid == 0 and not info.st_mode & 0o022
                    and info.st_dev == os.fstat(state).st_dev, 'unsafe state boot directory')
            yield boot, (info.st_dev, info.st_ino)
        finally:
            os.close(boot)


def new_report():
    report = CONTROL.new_report()
    report.update(state_write_attempted=False, state_may_be_modified=False,
                  state_unmounted=False,
                  classification='offline selection metadata recovery only; no boot, health, KVM or physical recovery proof')
    return report


def announce_state_write(report):
    # Emit the conservative receipt before any journal replay can begin.
    print(json.dumps(report, sort_keys=True, indent=2), file=sys.stderr, flush=True)


def failure(report, error):
    if 'error' in report:
        report['success'] = False
        report.setdefault('additional_errors', []).append(f'{type(error).__name__}: {error}')
    else:
        CONTROL.failure(report, error)


def apply(args, manifest, plan, report=None, *, plan_check=lambda disk: None):
    report = new_report() if report is None else report
    mount_stack, mount_parent, mount_identity, created = None, None, None, False
    try:
        require(os.geteuid() == 0, 'apply requires root')
        require(report == new_report(), 'apply requires a fresh conservative report')
        context, proposed = plan['context'], SELECT.serialize(plan['proposed_selector'])
        disk, target = context['disk'], context['target']
        report.update(operation=plan['operation'], plan_sha256=plan['plan_sha256'],
                      target=target, manifest=plan['manifest'], expected_root=plan['expected_root'],
                      proposed_selector=plan['proposed_selector'], state_mountpoint=str(MOUNTPOINT))
        require(make_plan(args, manifest) == plan, 'preflight differs from reviewed plan')
        plan_check(disk)
        authorize(plan)
        with recovery_lock(), block_stream(disk, exclusive=False):
            require(make_plan(args, manifest) == plan, 'plan changed after authorization/lock')
            plan_check(disk)
            with block_stream(target) as root:
                require(authenticate_root(root, args, manifest, context) == proposed,
                        'authenticated selector differs from plan')
                with block_stream(context['state']) as state:
                    require(state_image(state, context) == plan['state_image_before'],
                            'state image changed before journal recovery')
                    check_context(args, manifest, context)
                    plan_check(disk)
                # Release state O_EXCL before the kernel claims its filesystem.
                private_namespace()
                check_context(args, manifest, context)
                plan_check(disk)
                mount_stack, mount_parent, mount_identity, created = mount_directory()
                report['state_write_attempted'] = report['state_may_be_modified'] = True
                announce_state_write(report)
                try:
                    subprocess.run(['mount', '-t', 'ext4', '-o', 'rw,nodev,nosuid',
                                    context['state']['canonical_path'], str(MOUNTPOINT)], check=True)
                    require(own_state_mounted(context), 'state mount did not appear')
                    check_context(args, manifest, context, True)
                    plan_check(disk)
                    with boot_directory(context) as (boot, identity):
                        lock_context = {'state': {'boot_directory': str(MOUNTPOINT / 'boot'),
                                                 'major_minor': context['state']['major_minor']}}
                        with UPDATE.update_lock(lock_context):
                            def check():
                                check_context(args, manifest, context, True)
                                WRITER.validate_fd(root.fileno(), target)
                                plan_check(disk)
                                with PREP.safe_parent(MOUNTPOINT / 'boot') as named:
                                    info = os.fstat(named)
                                    require((info.st_dev, info.st_ino) == identity,
                                            'state boot directory pathname changed')
                            check()
                            before, valid = selector_before(boot)
                            report.update(before_selector_hex=None if before is None else before.hex(),
                                          before_selector_valid=valid)
                            journal = {'schema': 1, 'operation': plan['operation'],
                                       'plan_sha256': plan['plan_sha256'],
                                       'manifest_sha256': manifest.review['manifest_sha256'],
                                       'expected_root': manifest.expected, 'context': context,
                                       'state_image_before': plan['state_image_before'],
                                       'before_selector_hex': report['before_selector_hex'],
                                       'before_selector_valid': valid,
                                       'after_selector_hex': proposed.hex()}
                            name = 'recovery-' + plan['plan_sha256'] + '.json'
                            report['journal_name'] = name
                            CONTROL.journal_store(boot, WRITER.canonical_json(journal), name, report)
                            check()
                            require(authenticate_root(root, args, manifest, context, True) == proposed,
                                    'root selector changed after journal')
                            check()
                            require(selector_before(boot) == (before, valid),
                                    'selector changed before publication')
                            CONTROL.publish_selector(boot, proposed, report)
                            actual = checked_record(boot, 'selected.conf', 4096)
                            report['selected_after'] = SELECT.parse(actual)
                            require(actual == proposed, 'published selector readback differs')
                            check()
                finally:
                    # A failed mount command can still have replayed the journal.
                    original_error = sys.exc_info()[1]
                    try:
                        if own_state_mounted(context):
                            subprocess.run(['umount', str(MOUNTPOINT)], check=True)
                            require(not own_state_mounted(context), 'state remains mounted after umount')
                            report['state_unmounted'] = True
                    except (Exception, KeyboardInterrupt) as error:
                        if original_error is not None:
                            failure(report, original_error)
                        report['cleanup_error'] = f'{type(error).__name__}: {error}'
                        raise
        report['success'] = True
    except (Exception, KeyboardInterrupt) as error:
        failure(report, error)
    finally:
        if mount_stack is not None:
            try:
                if created and not own_state_mounted(context):
                    named = os.stat(MOUNTPOINT.name, dir_fd=mount_parent, follow_symlinks=False)
                    require((named.st_dev, named.st_ino) == mount_identity,
                            'recovery mountpoint pathname changed during cleanup')
                    os.rmdir(MOUNTPOINT.name, dir_fd=mount_parent)
            except (Exception, KeyboardInterrupt) as error:
                failure(report, error)
            finally:
                try:
                    mount_stack.close()
                except (Exception, KeyboardInterrupt) as error:
                    failure(report, error)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('prepare', 'apply'))
    parser.add_argument('--target', required=True)
    parser.add_argument('--expected-model', required=True)
    parser.add_argument('--expected-serial', required=True)
    parser.add_argument('--expected-bytes', required=True, type=int)
    parser.add_argument('--state-uuid', required=True)
    parser.add_argument('--slot', choices=('A', 'B'), required=True)
    parser.add_argument('--payload-manifest', required=True, type=Path)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--plan', type=Path)
    args = parser.parse_args(argv)
    report, stack = new_report(), contextlib.ExitStack()
    previous = signal.signal(signal.SIGTERM, WRITER.interrupted)
    try:
        require(args.expected_model.strip() and args.expected_serial.strip() and
                args.expected_bytes > 0, 'explicit device identity required')
        require(str(uuid.UUID(args.state_uuid)) == args.state_uuid and uuid.UUID(args.state_uuid).int,
                'canonical nonzero state UUID required')
        require(args.operation != 'apply' or (args.plan and os.geteuid() == 0),
                'apply requires root and --plan')
        manifest = Manifest(args.payload_manifest, args.manifest_sha256, args.slot)
        stack.callback(manifest.close)
        plan = make_plan(args, manifest)
        if args.operation == 'prepare':
            stack.close()
            print(json.dumps(plan, sort_keys=True, indent=2))
            return 0
        parent = stack.enter_context(PREP.safe_parent(args.plan.parent))
        saved = stack.enter_context(WRITER.open_regular(args.plan))
        identity = Manifest.identity(saved.fileno())
        UPDATE.storage_guard(parent)
        UPDATE.storage_guard(saved.fileno())
        _, reviewed = WRITER.read_json(saved, limit=PREP.LIMIT)
        def plan_check(disk):
            require(Manifest.identity(saved.fileno()) == identity, 'held review plan changed')
            for fd in (parent, saved.fileno()):
                UPDATE.storage_guard(fd)
                WRITER.source_off_target(fd, disk['major_minor'])
            named = os.stat(args.plan.name, dir_fd=parent, follow_symlinks=False)
            require((named.st_dev, named.st_ino) == (identity['device'], identity['inode']),
                    'review plan filename changed')
            with PREP.safe_parent(args.plan.parent) as current:
                a, b = os.fstat(parent), os.fstat(current)
                require((a.st_dev, a.st_ino) == (b.st_dev, b.st_ino),
                        'review plan parent pathname changed')
        plan_check(plan['context']['disk'])
        require(reviewed == plan, 'current preflight differs from reviewed plan')
        print(json.dumps(plan, sort_keys=True, indent=2), file=sys.stderr)
        apply(args, manifest, plan, report, plan_check=plan_check)
    except (Exception, KeyboardInterrupt) as error:
        failure(report, error)
    finally:
        try:
            stack.close()
        except (Exception, KeyboardInterrupt) as error:
            failure(report, error)
        signal.signal(signal.SIGTERM, previous)
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0 if report['success'] else 1


if __name__ == '__main__':
    sys.exit(main())
