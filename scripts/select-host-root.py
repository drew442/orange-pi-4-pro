#!/usr/bin/env python3
"""Review and publish an explicit live A733 NVMe root selection.

Requires the complete trusted checkout. Changes state metadata only; never stages
root blocks, switches the running root, reboots, or proves root/board health.
Manual rollback after boot requires the old independently trusted manifest.
"""
import argparse
import contextlib
import copy
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import signal
import stat
import sys


def adjacent(name, filename):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


UPDATE = adjacent('selection_update', 'update-host.py')
PREP = UPDATE.PREP
WRITER = PREP.WRITER
SELECT = PREP.SELECT
require = PREP.require


_MAINTENANCE = None


def maintenance_helper():
    # Lazy: lifecycle activation imports selector/update pure helpers in turn.
    global _MAINTENANCE
    if _MAINTENANCE is None:
        _MAINTENANCE = adjacent('installed_host_maintenance', 'guest-host-maintenance.py')
    return _MAINTENANCE


def snapshot(args, published=False):
    policy = adjacent('selection_target', 'host-update-target.py')
    return policy.activation_snapshot(args.target, args.expected_model, args.expected_serial,
                                      args.expected_bytes, args.slot, args.intent, published)


class Manifest:
    """Hold a protected manifest; authenticate bytes before parsing JSON."""
    def __init__(self, path, pin, slot, *, parent_opener=PREP.safe_parent,
                 guard=UPDATE.storage_guard, slot_bytes=None):
        self.stack = contextlib.ExitStack()
        try:
            require(isinstance(pin, str) and re.fullmatch('[0-9a-f]{64}', pin),
                    'explicit lowercase manifest SHA-256 required')
            self.path, self.parent_opener = Path(path), parent_opener
            self.directory = self.stack.enter_context(parent_opener(self.path.parent))
            guard(self.directory)
            info = os.fstat(self.directory)
            self.directory_identity = (info.st_dev, info.st_ino)
            fd = os.open(self.path.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
                         dir_fd=self.directory)
            self.stream = self.stack.enter_context(os.fdopen(fd, 'rb'))
            self.file_identity = self.identity(fd)
            guard(fd)
            require(0 < self.file_identity['bytes'] <= PREP.LIMIT, 'manifest size exceeds bound')
            data = self.stream.read(PREP.LIMIT + 1)
            require(len(data) <= PREP.LIMIT and PREP.record(data)['sha256'] == pin,
                    'trusted manifest SHA-256 mismatch')
            self.check()
            self.value = json.loads(data, object_pairs_hook=WRITER.unique_pairs)
            require(isinstance(self.value, dict), 'manifest object required')
            self.expected = PREP.validate_manifest(self.value, slot, slot_bytes)
            self.review = {'path': str(self.path), 'slot': slot, 'root': self.expected,
                           'manifest_sha256': pin, 'identity': self.file_identity}
        except BaseException:
            self.stack.close()
            raise

    @staticmethod
    def identity(fd):
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_gid == 0
                and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o400,
                'manifest/review requires root:root, mode 0400 and one regular-file link')
        return WRITER.file_identity(fd)

    def check(self):
        info = os.fstat(self.directory)
        require((info.st_dev, info.st_ino) == self.directory_identity,
                'held manifest directory changed')
        with self.parent_opener(self.path.parent) as current:
            info = os.fstat(current)
            require((info.st_dev, info.st_ino) == self.directory_identity,
                    'manifest parent pathname changed')
        require(self.identity(self.stream.fileno()) == self.file_identity,
                'held manifest changed')
        named = os.stat(self.path.name, dir_fd=self.directory, follow_symlinks=False)
        require((named.st_dev, named.st_ino) ==
                (self.file_identity['device'], self.file_identity['inode']),
                'manifest filename changed')

    def close(self):
        self.stack.close()


def candidate_context(args, manifest, published=False):
    return UPDATE.clock_context(manifest.value, snapshot(args, published=published))


def check_context(args, manifest, context):
    manifest.check()
    require(candidate_context(args, manifest) == context, 'live host context changed')


@contextlib.contextmanager
def checked_root(args, manifest, context):
    """Authenticate the same held readonly block FD subsequently parsed."""
    target = context['target']
    require(manifest.expected['bytes'] == target['size_bytes'], 'root byte budget differs')
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    if args.intent == 'activate':
        flags |= os.O_EXCL
    fd = os.open(target['canonical_path'], flags)
    with os.fdopen(fd, 'rb') as stream:
        WRITER.validate_fd(fd, target)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        check_context(args, manifest, context)
        WRITER.hash_stream(stream, manifest.expected, progress=WRITER.ConsoleProgress(
            label='Selected root bytes authenticated'))
        check_context(args, manifest, context)
        WRITER.validate_fd(fd, target)
        stream.seek(0)
        selector, _ = PREP.compatibility(stream, args.slot, manifest.value)
        values = SELECT.parse(selector)
        require(values['SLOT'] == args.slot, 'authenticated selector slot differs')
        if args.intent == 'cancel-pending':
            require(args.slot == context['active']['slot'] and
                    values['ROOT_MARKER_SHA256'] == context['active']['marker_sha256'],
                    'cancellation root marker differs from actual running root')
        check_context(args, manifest, context)
        WRITER.validate_fd(fd, target)
        yield stream, selector
        WRITER.validate_fd(fd, target)


def make_plan(args, manifest):
    manifest.check()
    context = candidate_context(args, manifest)
    with checked_root(args, manifest, context) as (_, selector):
        check_context(args, manifest, context)
    result = {'schema': 1, 'operation': 'explicit-root-selection', 'intent': args.intent,
              'manifest': manifest.review, 'context': context,
              'proposed_selector': SELECT.parse(selector),
              'proposed_selector_sha256': WRITER.digest(selector),
              'expected_root': manifest.expected,
              'warning': 'Selection only: no running root switch, health proof, reboot, automatic rollback or failed-boot recovery. Independently verified off-device backup required.'}
    result['plan_sha256'] = WRITER.digest(WRITER.canonical_json(result))
    return result


def authorize(plan, input_stream=None, output_stream=None):
    incoming = sys.stdin if input_stream is None else input_stream
    outgoing = sys.stderr if output_stream is None else output_stream
    require(incoming.isatty() and outgoing.isatty(), 'apply requires an interactive TTY')
    target = plan['context']['target']
    outgoing.write(
        f"Select root slot {plan['manifest']['slot']}; intent: {plan['intent']}\n"
        f"Target: {target['canonical_path']}\n"
        f"Model: {target.get('model', plan['context'].get('disk', {}).get('model', 'unavailable'))}\n"
        f"Serial: {target['serial']}\nSize: {target['size_bytes']} bytes\n"
        f"Root SHA-256: {plan['expected_root']['sha256']}\n"
        f"Plan SHA-256: {plan['plan_sha256']}\n"
        "An independently verified off-device backup is a required precondition; "
        "this confirmation is not backup evidence.\n"
        "Proceed with explicit root selection? [yes/no] ")
    outgoing.flush()
    require(incoming.readline().strip().casefold() == 'yes', 'authorization declined')


def secure_read(directory, name, limit, *, sync=False):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_gid == 0
                and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600,
                'unsafe state record')
        identity = WRITER.file_identity(fd)
        data = stream.read(limit + 1)
        require(len(data) <= limit and WRITER.file_identity(fd) == identity,
                'state record grew or changed')
        if sync:
            os.fsync(fd)
            require(WRITER.file_identity(fd) == identity, 'state record changed during sync')
        named = os.stat(name, dir_fd=directory, follow_symlinks=False)
        require((named.st_dev, named.st_ino) == (info.st_dev, info.st_ino),
                'state record filename changed')
        return data


def journal_store(directory, data, name, report):
    """Durable no-replace journal. Exact existing records support prepublish retry."""
    temporary = '.selection-' + secrets.token_hex(16)
    owned = None
    renamed = False
    try:
        try:
            existing = secure_read(directory, name, PREP.LIMIT)
        except FileNotFoundError:
            pass
        else:
            require(existing == data, 'existing selection journal differs')
            report['journal_visible'] = True
            require(secure_read(directory, name, PREP.LIMIT, sync=True) == data,
                    'existing selection journal changed before sync')
            os.fsync(directory)
            report['journal_durable'] = True
            return
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory)
        info = os.fstat(fd)
        owned = (info.st_dev, info.st_ino)
        with os.fdopen(fd, 'wb') as stream:
            require(info.st_uid == 0 and info.st_gid == 0 and
                    stat.S_IMODE(info.st_mode) == 0o600, 'unsafe private journal inode')
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        named = os.stat(temporary, dir_fd=directory, follow_symlinks=False)
        require((named.st_dev, named.st_ino) == owned, 'private journal filename changed')
        with SELECT.publication_signals():
            PREP.rename_new(directory, temporary, name)
            renamed = True
            report['journal_visible'] = True
        os.fsync(directory)
        report['journal_durable'] = True
    except BaseException as error:
        renamed = renamed or SELECT.named_inode(directory, name, owned)
        report['journal_visible'] = report['journal_visible'] or renamed
        report['journal_error'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        if owned is not None and not renamed:
            info = os.stat(temporary, dir_fd=directory, follow_symlinks=False)
            if (info.st_dev, info.st_ino) == owned:
                os.unlink(temporary, dir_fd=directory)


def new_report():
    return {'success': False, 'selection_write_attempted': False,
            'selection_published': False, 'publication_durable': False,
            'journal_visible': False, 'journal_durable': False,
            'root_switched': False, 'block_writes_performed': False,
            'classification': 'selection receipt only; no health, boot, KVM or recovery proof'}


def failure(report, error):
    report['success'] = False
    report['error'] = f'{type(error).__name__}: {error}'
    if hasattr(error, 'receipt'):
        report['maintenance'] = copy.deepcopy(error.receipt)


def publish_selector(directory, data, report):
    """Publish through a held directory while preserving interruption accounting.

    Caller owns authorization, durable history, target verification and locks.
    State files are written; no root block device is written or switched.
    """
    report['selection_write_attempted'] = True
    with SELECT.publication_signals():
        try:
            SELECT.atomic_store(directory, data)
        except SELECT.PublicationError as error:
            report['selection_published'] = error.published
            raise
        report['selection_published'] = True
        report['publication_durable'] = True


def apply(args, manifest, plan, report=None, *, plan_check=lambda target: None):
    report = new_report() if report is None else report
    maintenance = None
    maintenance_failure = None
    try:
        require(os.geteuid() == 0, 'apply requires root')
        require(report == new_report(), 'apply requires a fresh conservative report')
        context = plan['context']
        target = context['target']
        proposed = SELECT.serialize(plan['proposed_selector'])
        report.update(operation='explicit-root-selection', intent=args.intent,
                      plan_sha256=plan['plan_sha256'], target=target,
                      manifest=plan['manifest'], expected_root=plan['expected_root'],
                      selected_before=context['state']['selector'],
                      proposed_selector=plan['proposed_selector'])
        require(make_plan(args, manifest) == plan, 'preflight differs from reviewed plan')
        plan_check(target)
        authorize(plan)
        helper = maintenance_helper()
        maintenance = helper.InstalledMaintenance(helper.registered_storage())
        reviewed_plan_check = plan_check
        def plan_check(target):
            maintenance.recheck()
            reviewed_plan_check(target)
        with maintenance:
            with UPDATE.update_lock(context):
                require(make_plan(args, manifest) == plan, 'plan changed after authorization/lock')
                plan_check(target)
                with checked_root(args, manifest, context) as (root, selector):
                    require(selector == proposed, 'authenticated selector differs from plan')
                    with PREP.safe_parent(Path(context['state']['boot_directory'])) as boot:
                        identity = os.fstat(boot)
                        require(identity.st_uid == 0 and not identity.st_mode & 0o022 and
                                f'{os.major(identity.st_dev)}:{os.minor(identity.st_dev)}' ==
                                context['state']['major_minor'], 'boot directory differs from state')
                        def check():
                            check_context(args, manifest, context)
                            WRITER.validate_fd(root.fileno(), target)
                            plan_check(target)
                            with PREP.safe_parent(Path(context['state']['boot_directory'])) as named:
                                info = os.fstat(named)
                                require((info.st_dev, info.st_ino) == (identity.st_dev, identity.st_ino),
                                        'state boot directory pathname changed')
                        check()
                        before = secure_read(boot, 'selected.conf', 4096)
                        require(WRITER.digest(before) == context['state']['selector_sha256'] and
                                SELECT.parse(before) == context['state']['selector'],
                                'installed selector differs from plan')
                        journal = {'schema': 1, 'operation': 'explicit-root-selection',
                                   'intent': args.intent, 'plan_sha256': plan['plan_sha256'],
                                   'manifest_sha256': manifest.review['manifest_sha256'],
                                   'root': manifest.expected, 'context': context,
                                   'before_selector_hex': before.hex(),
                                   'after_selector_hex': proposed.hex()}
                        name = 'selection-' + plan['plan_sha256'] + '.json'
                        report['journal_name'] = name
                        journal_store(boot, WRITER.canonical_json(journal), name, report)
                        check()
                        # Reauthenticate after journal I/O while locks and FD remain held.
                        WRITER.hash_stream(root, manifest.expected, progress=WRITER.ConsoleProgress(
                            label='Prepublication root bytes authenticated'))
                        check()
                        require(secure_read(boot, 'selected.conf', 4096) == before,
                                'selector changed before publication')
                        publish_selector(boot, proposed, report)
                        actual = secure_read(boot, 'selected.conf', 4096)
                        report['selected_after'] = SELECT.parse(actual)
                        require(actual == proposed, 'published selector readback differs')
                        expected = copy.deepcopy(context)
                        expected['state']['selector'] = plan['proposed_selector']
                        expected['state']['selector_sha256'] = WRITER.digest(proposed)
                        manifest.check()
                        plan_check(target)
                        require(candidate_context(args, manifest, published=True) == expected,
                                'postpublication context differs; inspect installed selection')
                        WRITER.validate_fd(root.fileno(), target)
                        with PREP.safe_parent(Path(context['state']['boot_directory'])) as named:
                            info = os.fstat(named)
                            require((info.st_dev, info.st_ino) == (identity.st_dev, identity.st_ino),
                                    'postpublication boot directory pathname changed')
            maintenance.finish_success()
        report['success'] = True
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
    parser.add_argument('--intent', choices=('activate', 'cancel-pending'), required=True)
    parser.add_argument('--slot', choices=('A', 'B'), required=True)
    parser.add_argument('--target', required=True)
    parser.add_argument('--expected-model', required=True)
    parser.add_argument('--expected-serial', required=True)
    parser.add_argument('--expected-bytes', type=int, required=True)
    parser.add_argument('--payload-manifest', type=Path, required=True)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--plan', type=Path)
    args = parser.parse_args(argv)
    report = new_report()
    stack = contextlib.ExitStack()
    previous = signal.signal(signal.SIGTERM, WRITER.interrupted)
    try:
        require(args.expected_model.strip() and args.expected_serial.strip() and
                args.expected_bytes > 0, 'explicit device identity required')
        require(args.operation != 'apply' or (args.plan and os.geteuid() == 0),
                'apply requires root and --plan')
        manifest = Manifest(args.payload_manifest, args.manifest_sha256, args.slot)
        stack.callback(manifest.close)
        plan = make_plan(args, manifest)
        if args.operation == 'prepare':
            stack.close()
            print(json.dumps(plan, sort_keys=True, indent=2))
            return 0
        # Reuse protected-file holding/identity policy, without parsing as a manifest.
        parent = stack.enter_context(PREP.safe_parent(args.plan.parent))
        UPDATE.storage_guard(parent)
        saved = stack.enter_context(WRITER.open_regular(args.plan))
        identity = Manifest.identity(saved.fileno())
        UPDATE.storage_guard(saved.fileno())
        _, reviewed = WRITER.read_json(saved, limit=PREP.LIMIT)
        def plan_check(target):
            require(Manifest.identity(saved.fileno()) == identity, 'held review plan changed')
            named = os.stat(args.plan.name, dir_fd=parent, follow_symlinks=False)
            require((named.st_dev, named.st_ino) == (identity['device'], identity['inode']),
                    'review plan filename changed')
            with PREP.safe_parent(args.plan.parent) as current:
                a, b = os.fstat(parent), os.fstat(current)
                require((a.st_dev, a.st_ino) == (b.st_dev, b.st_ino),
                        'review plan parent pathname changed')
        plan_check(plan['context']['target'])
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
