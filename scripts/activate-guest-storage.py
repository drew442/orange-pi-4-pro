#!/usr/bin/env python3
"""Explicit host-visible A733 NVMe p5 mount; no guest launch or hardware proof.

Journal replay may write p5 even when mount fails. Manual CLI use requires a
verified off-device backup and interactive authorization. Python boot preparation
uses a typed closed-bootstrap capability. Never formats, partitions or changes SD.
"""
import argparse
import contextlib
import copy
import fcntl
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
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CONTROL = adjacent('guest_activation_control', 'select-host-root.py')
POLICY = adjacent('guest_activation_policy', 'guest-mount-target.py')
CONTEXT = adjacent('guest_activation_context', 'guest-lifecycle-context.py')
ADMISSION = CONTEXT.ADMISSION
PREP, WRITER, UPDATE = CONTROL.PREP, CONTROL.WRITER, CONTROL.UPDATE
require = PREP.require
MOUNTPOINT = Path('/run/a733-guests')
LOCK = Path('/run/a733-guest-mount.lock')


def host_namespace():
    own, init = os.stat('/proc/self/ns/mnt'), os.stat('/proc/1/ns/mnt')
    require((own.st_dev, own.st_ino) == (init.st_dev, init.st_ino),
            'activation requires the PID 1 mount namespace')


def mount_entries():
    return [line.split(' - ', 1)[0].split()
            for line in Path('/proc/self/mountinfo').read_text().splitlines()]


def fixed_entries():
    return [row for row in mount_entries() if row[4] == str(MOUNTPOINT)]


def snapshot(args, mounted):
    host_namespace()
    return POLICY.snapshot(args.target, args.expected_model, args.expected_serial,
                           args.expected_bytes, args.guest_uuid, mounted=mounted)


def make_plan(args):
    require(os.geteuid() == 0, 'guest activation preparation requires root')
    mounted = bool(fixed_entries())
    context = snapshot(args, mounted)
    value = {'schema': 1, 'operation': 'activate-guest-storage', 'context': context,
             'mounted_before': mounted,
             'actions': {'mount': not mounted, 'journal_replay_possible': not mounted,
                         'guest_root_mode': '0711'},
             'warning': 'p5 journal replay and root traversal permissions only; no guest launch, autoboot or physical KVM proof'}
    value['plan_sha256'] = WRITER.digest(WRITER.canonical_json(value))
    return value


def storage_guard(fd, context, ram_only=False):
    # Retain the common policy's explicit MMC-dependency rejection.
    UPDATE.storage_guard(fd)
    device = os.fstat(fd).st_dev
    identity = f'{os.major(device)}:{os.minor(device)}'
    filesystems = {line.split(' - ', 1)[1].split()[0]
                   for line in Path('/proc/self/mountinfo').read_text().splitlines()
                   if line.split(' - ', 1)[0].split()[2] == identity}
    require(len(filesystems) == 1, 'unknown or ambiguous plan storage')
    ram = filesystems <= {'tmpfs', 'ramfs'}
    require(ram or not ram_only and identity == context['state']['major_minor'],
            'storage must be RAM or selected NVMe state')


class Review:
    """Guard protected held review storage before reading a single byte."""
    def __init__(self, path, context):
        self.stack = contextlib.ExitStack()
        self.path, self.context = Path(path), context
        try:
            self.parent = self.stack.enter_context(PREP.safe_parent(self.path.parent))
            storage_guard(self.parent, context)
            fd = os.open(self.path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=self.parent)
            self.stream = self.stack.enter_context(os.fdopen(fd, 'rb'))
            self.identity = CONTROL.Manifest.identity(fd)
            storage_guard(fd, context)
            self.parent_identity = os.fstat(self.parent).st_dev, os.fstat(self.parent).st_ino
            _, self.value = WRITER.read_json(self.stream, limit=PREP.LIMIT)
            self.check()
        except BaseException:
            self.stack.close()
            raise

    def check(self):
        require(CONTROL.Manifest.identity(self.stream.fileno()) == self.identity,
                'held review plan changed')
        for fd in (self.parent, self.stream.fileno()):
            storage_guard(fd, self.context)
        named = os.stat(self.path.name, dir_fd=self.parent, follow_symlinks=False)
        require((named.st_dev, named.st_ino) ==
                (self.identity['device'], self.identity['inode']), 'review filename changed')
        with PREP.safe_parent(self.path.parent) as current:
            info = os.fstat(current)
            require((info.st_dev, info.st_ino) == self.parent_identity,
                    'review parent changed')

    def close(self):
        self.stack.close()


@contextlib.contextmanager
def block_stream(target, exclusive=False):
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    if exclusive:
        flags |= os.O_EXCL
    fd = os.open(target['canonical_path'], flags)
    try:
        WRITER.validate_fd(fd, target)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield fd
        WRITER.validate_fd(fd, target)
    finally:
        os.close(fd)


@contextlib.contextmanager
def activation_lock(context, *, check=lambda: None):
    with PREP.safe_parent(LOCK.parent) as parent:
        storage_guard(parent, context, ram_only=True)
        check()
        fd = os.open(LOCK.name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                     0o600, dir_fd=parent)
        try:
            info = os.fstat(fd)
            require(stat.S_ISREG(info.st_mode) and info.st_uid == info.st_gid == 0
                    and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600
                    and info.st_dev == os.fstat(parent).st_dev, 'unsafe activation lock')
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            named = os.stat(LOCK.name, dir_fd=parent, follow_symlinks=False)
            require((named.st_dev, named.st_ino) == (info.st_dev, info.st_ino),
                    'activation lock pathname changed')
            yield
        finally:
            os.close(fd)


def authorize(plan):
    require(sys.stdin.isatty() and sys.stderr.isatty(), 'apply requires an interactive TTY')
    guest = plan['context']['guest']
    phrase = f"ACTIVATE {guest['serial']} {guest['canonical_path']} {guest['uuid']} {plan['plan_sha256']}"
    for prompt, expected in (('Confirm verified off-device backup', 'BACKUP VERIFIED'),
                             ('Authorize p5 journal replay and root traversal', phrase)):
        sys.stderr.write(prompt + ': ' + expected + '\n> ')
        sys.stderr.flush()
        require(sys.stdin.readline().rstrip('\n') == expected, 'authorization phrase differs')


def checked_command(name):
    path = Path('/usr/bin') / name
    with PREP.safe_parent(path.parent):
        info = path.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_uid == info.st_gid == 0
                and not info.st_mode & 0o022 and info.st_mode & 0o111,
                'unsafe installed mount utility')
    return str(path)


class MountDirectory:
    def __init__(self, context, *, check=lambda: None):
        self.check_authority = check
        self.stack, self.created, self.identity = contextlib.ExitStack(), False, None
        try:
            self.parent = self.stack.enter_context(PREP.safe_parent(MOUNTPOINT.parent))
            storage_guard(self.parent, context, ram_only=True)
            require(not fixed_entries(), 'mountpoint already mounted')
            try:
                self.check_authority()
                os.mkdir(MOUNTPOINT.name, 0o700, dir_fd=self.parent)
                self.created = True
            except FileExistsError:
                pass
            fd = os.open(MOUNTPOINT.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                         dir_fd=self.parent)
            self.stack.callback(os.close, fd)
            info = os.fstat(fd)
            self.identity = info.st_dev, info.st_ino
            require(info.st_uid == info.st_gid == 0 and stat.S_IMODE(info.st_mode) == 0o700
                    and info.st_dev == os.fstat(self.parent).st_dev and not os.listdir(fd),
                    'mountpoint must be empty root:root mode 0700 RAM directory')
        except BaseException:
            self.stack.close()
            raise

    def check(self):
        require(not fixed_entries(), 'mountpoint appeared before activation')
        with PREP.safe_parent(MOUNTPOINT.parent) as parent:
            a, b = os.fstat(parent), os.fstat(self.parent)
            require((a.st_dev, a.st_ino) == (b.st_dev, b.st_ino), 'mount parent changed')
        info = os.stat(MOUNTPOINT.name, dir_fd=self.parent, follow_symlinks=False)
        require((info.st_dev, info.st_ino) == self.identity and stat.S_ISDIR(info.st_mode)
                and info.st_uid == info.st_gid == 0 and stat.S_IMODE(info.st_mode) == 0o700,
                'mountpoint pathname changed')
        fd = os.open(MOUNTPOINT.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                     dir_fd=self.parent)
        try:
            info = os.fstat(fd)
            require((info.st_dev, info.st_ino) == self.identity, 'held mountpoint changed')
            require(not os.listdir(fd), 'mountpoint no longer empty')
        finally:
            os.close(fd)

    def close(self):
        try:
            if self.created and not fixed_entries():
                self.check()
                self.check_authority()
                os.rmdir(MOUNTPOINT.name, dir_fd=self.parent)
        finally:
            self.stack.close()


def checked_mount(args, context):
    require(snapshot(args, True) == context, 'mounted guest context changed')
    entries = fixed_entries()
    require(len(entries) == 1 and entries[0][2] == context['guest']['major_minor'],
            'guest mount identity differs')
    return tuple(entries[0])


def grant_traversal(args, context, identity, report, *, check=lambda: None):
    require(checked_mount(args, context) == identity, 'guest mount replaced')
    with PREP.safe_parent(MOUNTPOINT.parent) as parent:
        fd = os.open(MOUNTPOINT.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                     dir_fd=parent)
        try:
            info = os.fstat(fd)
            require(f'{os.major(info.st_dev)}:{os.minor(info.st_dev)}' ==
                    context['guest']['major_minor'], 'guest root device differs')
            require(info.st_uid == info.st_gid == 0 and stat.S_IMODE(info.st_mode) in (0o700, 0o711),
                    'guest root must be root:root mode 0700 or 0711')
            require(checked_mount(args, context) == identity, 'guest mount replaced before chmod')
            if stat.S_IMODE(info.st_mode) != 0o711:
                check()
                report['permissions_may_be_modified'] = report['guest_may_be_modified'] = True
                os.fchmod(fd, 0o711)
                os.fsync(fd)
            require(stat.S_IMODE(os.fstat(fd).st_mode) == 0o711, 'guest traversal readback differs')
            named = os.stat(MOUNTPOINT.name, dir_fd=parent, follow_symlinks=False)
            require((named.st_dev, named.st_ino) == (info.st_dev, info.st_ino),
                    'guest root pathname changed')
            require(checked_mount(args, context) == identity, 'guest mount changed after chmod')
        finally:
            os.close(fd)


def new_report():
    return dict(success=False, mounted_before=False, mount_attempted=False,
                mount_may_be_visible=False, guest_may_be_modified=False,
                permissions_may_be_modified=False, mount_retained=False, cleanup_errors=[],
                classification='explicit p5 activation only; no physical boot or KVM proof')


def failure(report, error):
    report['success'] = False
    report.setdefault('errors', []).append(f'{type(error).__name__}: {error}')


def apply(args, plan, report=None, *, plan_check=lambda: None):
    """Activate installed storage under fresh pre-p5 maintenance custody.

    Interactive authorization precedes admission closure. The original target
    transaction retains custody through its complete cleanup before binding p5
    and explicitly reopening admission. No runtime is initialized here.
    """
    report = new_report() if report is None else report
    maintenance = None
    maintenance_failure = None

    def authorization(current):
        nonlocal maintenance
        authorize(current)
        # Lazy loading keeps the bootstrap API independent of installed custody.
        helper = adjacent('guest_activation_maintenance', 'guest-storage-maintenance.py')
        storage = helper.M.registered_storage()
        context = current['context']
        require(storage == dict(state_uuid=context['state']['uuid'],
                                guest_uuid=context['guest']['uuid'],
                                nvme_serial=context['disk']['serial']),
                'registered storage differs from reviewed activation plan')
        maintenance = helper.StorageMaintenance(storage)

    def check():
        plan_check()
        if maintenance is not None:
            maintenance.recheck()

    try:
        _apply(args, plan, report, plan_check=check, authorization=authorization)
        core_success = report['success'] and not report['cleanup_errors']
        report['success'] = False
        if core_success:
            require(maintenance is not None, 'maintenance custody missing')
            maintenance.bind_storage()
            maintenance.finish_success()
    except (Exception, KeyboardInterrupt) as error:
        failure(report, error)
        if hasattr(error, 'receipt'):
            maintenance_failure = copy.deepcopy(error.receipt)
    finally:
        # _apply records authorization/acquisition errors itself.
        if maintenance is not None:
            try:
                maintenance.close()
            except (Exception, KeyboardInterrupt) as error:
                failure(report, error)
                report['cleanup_errors'].append(f'{type(error).__name__}: {error}')
            report['maintenance'] = copy.deepcopy(maintenance.receipt)
            report['success'] = (not report.get('errors') and not report['cleanup_errors']
                                 and report['maintenance']['success'])
        elif maintenance_failure is not None:
            report['maintenance'] = maintenance_failure
    return report


def bootstrap_check(owner):
    """Validate a borrowed closed-bootstrap capability; never opens admission."""
    require(os.geteuid() == 0, 'bootstrap activation requires root')
    require(type(owner) is ADMISSION.ClosureOwner, 'typed bootstrap closure owner required')
    require(type(owner.admission) is ADMISSION.Admission, 'typed admission required')
    require(owner.reason == 'bootstrap', 'bootstrap closure required')
    lease = owner.lease
    require(type(lease) is ADMISSION.Lease and lease.admission is owner.admission
            and lease.exclusive is True, 'matching exclusive bootstrap lease required')
    lease.recheck()
    # A mutable Python flag alone does not establish a currently held EX flock.
    data = CONTEXT.read_system('/proc/self/fdinfo/' + str(lease.fd), 65536).decode('ascii')
    require(any(row.split()[2:6] == ['FLOCK', 'ADVISORY', 'WRITE', str(os.getpid())]
                for row in data.splitlines() if row.startswith('lock:')),
            'live exclusive bootstrap flock required')
    CONTEXT.host_identity(owner.admission.boot_id)
    with owner.admission._gate():
        record = owner._matching()
        require(record['boot_id'] == owner.admission.boot_id, 'bootstrap boot differs')


def apply_bootstrap(args, plan, owner, report=None, *, plan_check=lambda: None):
    """Python-only recurring activation under authenticated closed bootstrap.

    The opt-in boot preparer supplies registered exact target arguments and its
    current plan guard. This borrows owner/lease without closing or reopening it.
    All existing device, filesystem and mount transaction checks still apply.
    """
    def check():
        bootstrap_check(owner)
        plan_check()
    return _apply(args, plan, report, plan_check=check,
                  authorization=lambda current: check())


def _apply(args, plan, report=None, *, plan_check, authorization):
    report = new_report() if report is None else report
    directory, owned = None, None
    locks = contextlib.ExitStack()
    context = plan['context']
    try:
        require(report == new_report(), 'fresh conservative report required')
        report.update(mounted_before=plan['mounted_before'], plan_sha256=plan['plan_sha256'],
                      mount_may_be_visible=plan['mounted_before'], mount_retained=plan['mounted_before'])
        require(make_plan(args) == plan, 'current preflight differs from reviewed plan')
        plan_check()
        authorization(plan)
        plan_check()
        locks.enter_context(activation_lock(context, check=plan_check))
        locks.enter_context(block_stream(context['disk']))
        require(make_plan(args) == plan, 'plan changed after authorization/locks')
        plan_check()
        if not plan['mounted_before']:
            with block_stream(context['guest'], exclusive=True) as fd:
                POLICY.guest_superblock(fd, context['guest']['uuid'], context['guest']['size_bytes'])
                require(snapshot(args, False) == context, 'guest context changed while claimed')
                plan_check()
                directory = MountDirectory(context, check=plan_check)
                directory.check()
            # Kernel filesystem claim follows release of the read-only exclusive claim.
            require(snapshot(args, False) == context, 'context changed before mount')
            plan_check()
            directory.check()
            command = checked_command('mount')
            plan_check()
            report.update(mount_attempted=True, mount_may_be_visible=True,
                          guest_may_be_modified=True)
            print(json.dumps(report, sort_keys=True), file=sys.stderr, flush=True)
            try:
                subprocess.run([command, '-t', 'ext4', '-o', 'rw,nodev,nosuid,noexec',
                                context['guest']['canonical_path'], str(MOUNTPOINT)], check=True)
            finally:
                # Reconcile even if SIGTERM/KeyboardInterrupt arrives after kernel mount
                # but before subprocess returns. Never infer absence from exit status.
                if fixed_entries():
                    owned = checked_mount(args, context)
                    report['mount_retained'] = True
            require(owned is not None, 'guest mount did not appear')
            identity = owned
        else:
            identity = checked_mount(args, context)
            report.update(mount_may_be_visible=True, mount_retained=True)
        plan_check()
        grant_traversal(args, context, identity, report, check=plan_check)
        require(checked_mount(args, context) == identity, 'guest mount changed before success')
        plan_check()
        report['success'] = True
    except (Exception, KeyboardInterrupt) as error:
        failure(report, error)
        if hasattr(error, 'receipt'):
            report['maintenance'] = copy.deepcopy(error.receipt)
    finally:
        if not report['success'] and owned is not None:
            try:
                require(checked_mount(args, context) == owned,
                        'owned mount replaced; retained for manual inspection')
                plan_check()
                subprocess.run([checked_command('umount'), str(MOUNTPOINT)], check=True)
                require(not fixed_entries(), 'mount remains visible after unmount')
                report['mount_retained'] = report['mount_may_be_visible'] = False
            except (Exception, KeyboardInterrupt) as error:
                report['cleanup_errors'].append(f'{type(error).__name__}: {error}')
        if report['mount_attempted'] and owned is None:
            try:
                report['mount_retained'] = bool(fixed_entries())
                report['mount_may_be_visible'] = report['mount_retained']
            except (Exception, KeyboardInterrupt) as error:
                report['mount_retained'] = True
                report['cleanup_errors'].append(f'{type(error).__name__}: {error}')
        if directory is not None:
            try:
                directory.close()
            except (Exception, KeyboardInterrupt) as error:
                failure(report, error)
                report['cleanup_errors'].append(f'{type(error).__name__}: {error}')
        try:
            locks.close()
        except (Exception, KeyboardInterrupt) as error:
            failure(report, error)
            report['cleanup_errors'].append(f'{type(error).__name__}: {error}')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('prepare', 'apply'))
    for name in ('target', 'expected-model', 'expected-serial', 'guest-uuid'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--expected-bytes', type=int, required=True)
    parser.add_argument('--plan', type=Path)
    args = parser.parse_args(argv)
    report, review = new_report(), None
    previous = signal.signal(signal.SIGTERM, WRITER.interrupted)
    try:
        require(args.expected_model.strip() and args.expected_serial.strip() and args.expected_bytes > 0,
                'explicit device identity required')
        identity = uuid.UUID(args.guest_uuid)
        require(str(identity) == args.guest_uuid and identity.int, 'canonical nonzero guest UUID required')
        require(args.operation != 'apply' or args.plan, 'apply requires --plan')
        plan = make_plan(args)
        if args.operation == 'prepare':
            print(json.dumps(plan, sort_keys=True, indent=2))
            return 0
        review = Review(args.plan, plan['context'])
        require(review.value == plan, 'current preflight differs from saved plan')
        apply(args, plan, report, plan_check=review.check)
    except (Exception, KeyboardInterrupt) as error:
        failure(report, error)
    finally:
        if review is not None:
            try:
                review.close()
            except (Exception, KeyboardInterrupt) as error:
                failure(report, error)
        signal.signal(signal.SIGTERM, previous)
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0 if report['success'] else 1


if __name__ == '__main__':
    sys.exit(main())
