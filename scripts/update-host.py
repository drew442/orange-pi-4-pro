#!/usr/bin/env python3
"""Review or stage a pinned root into the live host's inactive NVMe slot.

Requires this complete trusted repository checkout. Staging never publishes a
selector, activates a root, reboots, changes GPT, or writes the boot stack.
Physical boot, real KVM and recovery acceptance remain separate requirements.
"""
import argparse
import contextlib
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import sys


def adjacent(name, filename):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


PREP = adjacent('stage_host_preparation', 'prepare-host-update.py')
WRITER = PREP.WRITER
require = PREP.require


_MAINTENANCE = None


def maintenance_helper():
    # Lazy: lifecycle activation imports selector/update pure helpers in turn.
    global _MAINTENANCE
    if _MAINTENANCE is None:
        _MAINTENANCE = adjacent('installed_host_maintenance', 'guest-host-maintenance.py')
    return _MAINTENANCE


def snapshot(args):
    policy = adjacent('stage_host_target', 'host-update-target.py')
    return policy.snapshot(args.target, args.expected_model, args.expected_serial,
                           args.expected_bytes, args.slot)


def storage_guard(fd):
    """Detect RAM/local storage, retaining the acquisition policy's MMC ban."""
    info = os.fstat(fd)
    device = f'{os.major(info.st_dev)}:{os.minor(info.st_dev)}'
    matches = [line.split(' - ', 1)[1].split()[0]
               for line in Path('/proc/self/mountinfo').read_text().splitlines()
               if line.split(' - ', 1)[0].split()[2] == device]
    require(matches and len(set(matches)) == 1, 'unknown or ambiguous candidate storage')
    PREP.ACQUIRE.storage_guard(fd, 'ram' if matches[0] in ('tmpfs', 'ramfs') else 'external')


class Source:
    """Authenticate exact held candidate inodes before parsing root metadata."""
    NAMES = ('manifest.json', 'root.ext4', 'selected.conf')

    def __init__(self, candidate, pin, slot, *, parent_opener=PREP.safe_parent,
                 guard=storage_guard, slot_bytes=None, inspect=PREP.compatibility):
        self.stack = contextlib.ExitStack()
        try:
            require(isinstance(pin, str) and re.fullmatch('[0-9a-f]{64}', pin),
                    'explicit lowercase manifest SHA-256 required')
            self.path = Path(candidate)
            self.parent_opener = parent_opener
            self.directory = self.stack.enter_context(parent_opener(self.path))
            info = os.fstat(self.directory)
            require(info.st_uid == 0 and info.st_gid == 0
                    and stat.S_IMODE(info.st_mode) == 0o700,
                    'candidate directory requires root ownership and mode 0700')
            self.directory_identity = (info.st_dev, info.st_ino)
            guard(self.directory)
            self.files, self.identities = {}, {}
            for name in self.NAMES:
                fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
                             dir_fd=self.directory)
                stream = self.stack.enter_context(os.fdopen(fd, 'rb'))
                self.files[name] = stream
                self.identities[name] = self.identity(fd)
                guard(fd)
            manifest = self.files['manifest.json']
            require(0 < self.identities['manifest.json']['bytes'] <= PREP.LIMIT,
                    'manifest exceeds size bound or is empty')
            data = manifest.read(PREP.LIMIT + 1)
            require(len(data) <= PREP.LIMIT and PREP.record(data)['sha256'] == pin,
                    'trusted manifest SHA-256 mismatch')
            self.check()
            value = json.loads(data, object_pairs_hook=WRITER.unique_pairs)
            require(isinstance(value, dict), 'manifest object required')
            self.expected = PREP.validate_manifest(value, slot, slot_bytes)
            self.root = self.files['root.ext4']
            WRITER.hash_stream(self.root, self.expected, progress=WRITER.ConsoleProgress(
                label='Candidate root bytes authenticated'))
            self.check()
            self.root.seek(0)
            selector, self.metadata = inspect(self.root, slot, value)
            self.check()
            supplied = self.files['selected.conf'].read(4097)
            require(len(supplied) <= 4096 and supplied == selector,
                    'candidate selector differs from authenticated root')
            self.check()
            self.review = {'candidate': str(self.path), 'slot': slot, 'root': self.expected,
                           'manifest_sha256': pin, 'selector': PREP.record(selector),
                           'files': self.identities}
        except BaseException:
            self.stack.close()
            raise

    @staticmethod
    def identity(fd):
        info = os.fstat(fd)
        require(info.st_uid == 0 and info.st_gid == 0 and info.st_nlink == 1
                and stat.S_IMODE(info.st_mode) == 0o400,
                'candidate files require root ownership, one link and mode 0400')
        return WRITER.file_identity(fd)

    def check(self):
        info = os.fstat(self.directory)
        require((info.st_dev, info.st_ino) == self.directory_identity
                and info.st_uid == 0 and info.st_gid == 0
                and stat.S_IMODE(info.st_mode) == 0o700,
                'held candidate directory changed')
        with self.parent_opener(self.path) as current:
            now = os.fstat(current)
            require((now.st_dev, now.st_ino) == self.directory_identity,
                    'candidate directory pathname changed')
        for name, stream in self.files.items():
            identity = self.identity(stream.fileno())
            require(identity == self.identities[name], 'held candidate file changed')
            named = os.stat(name, dir_fd=self.directory, follow_symlinks=False)
            require((named.st_dev, named.st_ino) == (identity['device'], identity['inode']),
                    'candidate filename changed')

    def off_target(self, target):
        for stream in self.files.values():
            WRITER.source_off_target(stream.fileno(), target['major_minor'])

    def close(self):
        self.stack.close()


def clock_context(metadata, context):
    """Bind additive time-state compatibility without reading a live clock."""
    require(isinstance(metadata, dict), 'root metadata must be an object')
    sources = metadata.get('sources', {})
    require(isinstance(sources, dict), 'root sources must be an object')
    if 'userspace_profile' not in sources and 'timesync_state' not in sources:
        return context
    policy = adjacent('clock_state_target', 'host-update-target.py')
    if policy.MANAGEMENT.timesync_policy(metadata) is None:
        return context
    clock = policy.MANAGEMENT.timesync_guard(metadata, context['state']['uuid'])
    require(clock is not None and clock['state_uuid'] == context['state']['uuid'],
            'clock state differs from selected state filesystem')
    major, minor = context['state']['major_minor'].split(':')
    require(clock['directory']['device'] == os.makedev(int(major), int(minor)),
            'clock directory is not on the selected state filesystem')
    return {**context, 'timesync_state': clock}


def inspect(args, source):
    context = snapshot(args)
    source.off_target(context['target'])
    source.check()
    return clock_context(source.metadata, context)


def make_plan(args, source):
    verify_readback = getattr(args, 'verify_readback', False)
    require(type(verify_readback) is bool, 'readback choice must be boolean')
    context = inspect(args, source)
    target = context['target']
    fd = os.open(target['canonical_path'], os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    try:
        WRITER.validate_fd(fd, target)
        old = WRITER.metadata_hashes(fd, target['size_bytes'])
        require(inspect(args, source) == context, 'host context changed during preparation')
        WRITER.validate_fd(fd, target)
    finally:
        os.close(fd)
    result = {'schema': 1, 'operation': 'inactive-slot-host-update',
              'source': source.review, 'context': context, 'old_metadata': old,
              'verify_readback': verify_readback,
              'warning': 'This plan is neither authorization nor a lock. An independently verified off-device backup is required. Staging does not activate the slot.'}
    result['plan_sha256'] = WRITER.digest(WRITER.canonical_json(result))
    return result


def authorize(plan, input_stream=None, output_stream=None):
    incoming = sys.stdin if input_stream is None else input_stream
    outgoing = sys.stderr if output_stream is None else output_stream
    require(incoming.isatty() and outgoing.isatty(), 'apply requires an interactive TTY')
    target = plan['context']['target']
    outgoing.write(
        f"Stage inactive root slot {plan['source']['slot']}\n"
        f"Target: {target['canonical_path']}\n"
        f"Model: {target.get('model', plan['context'].get('disk', {}).get('model', 'unavailable'))}\n"
        f"Serial: {target['serial']}\nSize: {target['size_bytes']} bytes\n"
        f"Root SHA-256: {plan['source']['root']['sha256']}\n"
        f"Plan SHA-256: {plan['plan_sha256']}\n"
        f"Full target readback: {plan['verify_readback']}\n"
        "An independently verified off-device backup is a required precondition; "
        "this confirmation is not backup evidence.\n"
        "Proceed with inactive-slot overwrite? [yes/no] ")
    outgoing.flush()
    require(incoming.readline().strip().casefold() == 'yes', 'authorization declined')


@contextlib.contextmanager
def update_lock(context):
    state = context['state']
    with PREP.safe_parent(Path(state['boot_directory'])) as directory:
        info = os.fstat(directory)
        require(info.st_uid == 0 and not info.st_mode & 0o022
                and f'{os.major(info.st_dev)}:{os.minor(info.st_dev)}' == state['major_minor'],
                'update lock directory differs from trusted state')
        fd = os.open('update.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                     0o600, dir_fd=directory)
        try:
            info = os.fstat(fd)
            require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_gid == 0
                    and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600
                    and info.st_dev == os.fstat(directory).st_dev,
                    'unsafe update lock inode')
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            named = os.stat('update.lock', dir_fd=directory, follow_symlinks=False)
            require((named.st_dev, named.st_ino) == (info.st_dev, info.st_ino),
                    'update lock name changed')
            yield fd
        finally:
            os.close(fd)


def new_report():
    return {'success': False, 'target_modified': False, 'bytes_attempted': 0,
            'bytes_written': 0, 'bytes_verified': 0, 'selector_writes_performed': False,
            'activation_ready': False, 'readback_requested': False, 'verification_performed': False,
            'classification': 'staging receipt only; no activation or health/hardware proof'}


def failure(report, error):
    report['success'] = report['activation_ready'] = False
    report['error'] = f'{type(error).__name__}: {error}'
    if hasattr(error, 'receipt'):
        report['maintenance'] = copy.deepcopy(error.receipt)


def apply(args, source, plan, report=None, *, plan_check=lambda target: None):
    """Transaction core. Tests replace policy/FD guards, never the production CLI."""
    report = new_report() if report is None else report
    maintenance = None
    maintenance_failure = None
    try:
        require(os.geteuid() == 0, 'apply requires root')
        require(not report.get('success') and not report.get('target_modified')
                and all(report.get(name) == 0 for name in
                        ('bytes_attempted', 'bytes_written', 'bytes_verified')),
                'apply requires a fresh report with zero write counters')
        report.update(operation='inactive-slot-host-update', plan_sha256=plan['plan_sha256'],
                      staged_slot=plan['source']['slot'], target=plan['context']['target'],
                      source=plan['source'], selected_before=plan['context']['state']['selector'],
                      readback_requested=getattr(args, 'verify_readback', False))
        require(type(plan.get('verify_readback')) is bool
                and plan['verify_readback'] is getattr(args, 'verify_readback', False),
                'readback choice differs from reviewed plan')
        require(make_plan(args, source) == plan, 'current preflight differs from reviewed plan')
        plan_check(plan['context']['target'])
        authorize(plan)
        helper = maintenance_helper()
        maintenance = helper.InstalledMaintenance(helper.registered_storage())
        reviewed_plan_check = plan_check
        def plan_check(target):
            maintenance.recheck()
            reviewed_plan_check(target)
        with maintenance:
            with update_lock(plan['context']):
                require(make_plan(args, source) == plan, 'host plan changed after authorization/lock')
                target = plan['context']['target']
                plan_check(target)
                fd = os.open(target['canonical_path'], os.O_RDWR | os.O_EXCL | os.O_NONBLOCK | os.O_NOFOLLOW)
                try:
                    WRITER.validate_fd(fd, target)
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

                    def check():
                        require(inspect(args, source) == plan['context'], 'live host context changed')
                        WRITER.validate_fd(fd, target)
                        plan_check(target)

                    check()
                    require(WRITER.metadata_hashes(fd, target['size_bytes']) == plan['old_metadata'],
                            'inactive slot metadata changed after open')
                    WRITER.hash_stream(source.root, source.expected, progress=WRITER.ConsoleProgress(
                        label='Prewrite candidate root bytes authenticated'))
                    check()
                    require(WRITER.metadata_hashes(fd, target['size_bytes']) == plan['old_metadata'],
                            'inactive slot metadata changed before write')
                    progress = WRITER.ConsoleProgress()
                    print('Staging inactive root; interruption may leave this slot incomplete.',
                          file=sys.stderr, flush=True)
                    WRITER.stream_copy_verify(
                        source.root, fd, source.expected,
                        flush_callback=lambda held: fcntl.ioctl(held, WRITER.BLKFLSBUF),
                        refresh_callback=lambda held: None, source_check=check,
                        progress=progress, report=report, verify_readback=plan['verify_readback'],
                        readback_progress=WRITER.ConsoleProgress(label='Root slot bytes read back'),
                        status=lambda message: print(message, file=sys.stderr, flush=True))
                    report['verification_performed'] = (
                        report['success'] and plan['verify_readback'])
                    report['activation_ready'] = report['success']
                finally:
                    os.close(fd)
            if report['success']:
                maintenance.finish_success()
    except (Exception, KeyboardInterrupt) as error:
        failure(report, error)
        if hasattr(error, 'receipt'):
            maintenance_failure = copy.deepcopy(error.receipt)
    finally:
        if maintenance is not None:
            report['maintenance'] = (maintenance_failure if maintenance_failure is not None
                                     else copy.deepcopy(maintenance.receipt))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('prepare', 'apply'))
    parser.add_argument('--candidate', required=True, type=Path)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--slot', required=True, choices=('A', 'B'))
    parser.add_argument('--target', required=True)
    parser.add_argument('--expected-model', required=True)
    parser.add_argument('--expected-serial', required=True)
    parser.add_argument('--expected-bytes', required=True, type=int)
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--verify-readback', action='store_true',
                        help='verify the full staged target after flushing (binds the plan)')
    args = parser.parse_args(argv)
    report = new_report()
    stack = contextlib.ExitStack()
    previous = signal.signal(signal.SIGTERM, WRITER.interrupted)
    try:
        require(args.expected_model.strip() and args.expected_serial.strip()
                and args.expected_bytes > 0, 'nonempty expected device identity required')
        require(args.operation != 'apply' or (args.plan and os.geteuid() == 0),
                'apply requires root and --plan')
        source = Source(args.candidate, args.manifest_sha256, args.slot)
        stack.callback(source.close)
        plan = make_plan(args, source)
        if args.operation == 'prepare':
            stack.close()
            print(json.dumps(plan, indent=2, sort_keys=True))
            return 0
        saved = stack.enter_context(WRITER.open_regular(args.plan))
        identity = WRITER.file_identity(saved.fileno())
        storage_guard(saved.fileno())
        _, reviewed = WRITER.read_json(saved, limit=PREP.LIMIT)
        require(WRITER.file_identity(saved.fileno()) == identity, 'review plan changed while read')

        def plan_check(target):
            require(WRITER.file_identity(saved.fileno()) == identity, 'review plan changed while held')
            WRITER.source_off_target(saved.fileno(), target['major_minor'])

        plan_check(plan['context']['target'])
        require(type(reviewed.get('verify_readback')) is bool
                and reviewed['verify_readback'] is args.verify_readback,
                'readback choice differs from reviewed plan')
        require(reviewed == plan, 'current preflight differs from reviewed plan')
        print(json.dumps(plan, indent=2, sort_keys=True), file=sys.stderr)
        apply(args, source, plan, report, plan_check=plan_check)
    except (Exception, KeyboardInterrupt) as error:
        failure(report, error)
    finally:
        try:
            stack.close()
        except (Exception, KeyboardInterrupt) as error:
            failure(report, error)
        signal.signal(signal.SIGTERM, previous)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report['success'] else 1


if __name__ == '__main__':
    sys.exit(main())
