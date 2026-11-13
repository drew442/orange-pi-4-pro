#!/usr/bin/python3
"""External candidate-B evidence qualification; never grants power authority."""
import argparse
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import threading
import uuid

MODULE = Path('/usr/lib/a733-tools/scripts/manage-guests.py')
MARKER = Path('/usr/lib/orangepi-hypervisor/payload.json')
MODULE_SHA256 = '8a6211b45d6d4b0231b100c71c2bb72a6f4eb475e04de5652104111a7d43520e'
MARKER_SHA256 = '396be626a7919b9c800dd6a00cc5f8c025fa26c2d5c124bdde469e3b8c347ab8'
GUEST = 'autokvm'


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def protected(path, limit, pin, size=None, mode=None):
    """Authenticate source before any installed Python executes."""
    require(path.is_absolute() and '..' not in path.parts, 'unsafe source path')
    device = os.stat('/').st_dev
    for parent in reversed(path.parents):
        info = os.lstat(parent)
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == info.st_gid == 0 and
                not info.st_mode & 0o022 and info.st_dev == device, 'unsafe source directory')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == before.st_gid == 0 and
                not before.st_mode & 0o022 and (mode is None or stat.S_IMODE(before.st_mode) == mode) and
                before.st_dev == device and
                0 < before.st_size <= limit, 'unsafe source file')
        raw = os.read(fd, limit + 1)
        require(os.fstat(fd) == before and os.lstat(path) == before and
                len(raw) == before.st_size and (size is None or len(raw) == size) and
                digest(raw) == pin, 'source bytes differ')
        return raw
    finally:
        os.close(fd)


def closed_runtime(files):
    """Reject every unbound namespace entry, including timestamp-valid bytecode."""
    root = MODULE.parent.parent
    prefix = 'usr/lib/a733-tools/'
    expected = {root / name.removeprefix(prefix) for name in files}
    require(len(files) == 85 and all(name.startswith(prefix) for name in files),
            'exact candidate runtime closure required')
    directories = {root}
    for path in expected:
        directories.update(parent for parent in path.parents if parent == root or root in parent.parents)
    device = os.stat('/').st_dev
    pending, observed = [root], set()
    while pending:
        directory = pending.pop()
        info = os.lstat(directory)
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == info.st_gid == 0 and
                not info.st_mode & 0o022 and info.st_dev == device and
                os.statvfs(directory).f_flag & os.ST_RDONLY, 'unsafe or writable runtime directory')
        with os.scandir(directory) as entries:
            for entry in entries:
                path = directory / entry.name
                require(path not in observed and (path in expected or path in directories),
                        'unexpected runtime entry (including bytecode)')
                observed.add(path)
                info = os.lstat(path)
                require(not stat.S_ISLNK(info.st_mode) and info.st_dev == device and
                        os.statvfs(path).f_flag & os.ST_RDONLY, 'unsafe runtime mount or symlink')
                if path in directories:
                    pending.append(path)
                else:
                    require(stat.S_ISREG(info.st_mode), 'nonregular runtime source')
    require(observed == (expected | directories) - {root}, 'incomplete runtime namespace')


def load_installed():
    require(os.statvfs('/').f_flag & os.ST_RDONLY, 'read-only root required before import')
    raw = protected(MARKER, 2 * 1024 * 1024, MARKER_SHA256, 26273)
    marker = json.loads(raw)
    require(marker['slot'] == 'B', 'candidate B required')
    closed_runtime(marker['maintenance_runtime']['files'])
    # The pinned marker binds the complete executable import closure. Do not
    # trust just the dispatcher while executing its unchecked dependencies.
    for family in ('maintenance_runtime', 'management_runtime'):
        for relative, record in marker[family]['files'].items():
            require(not relative.startswith('/') and '..' not in Path(relative).parts,
                    'invalid bound source path')
            if family == 'maintenance_runtime':
                require(relative.startswith('usr/lib/a733-tools/'), 'unsafe maintenance path')
            protected(Path('/') / relative, 2 * 1024 * 1024,
                      record['sha256'], record['bytes'], record.get('mode'))
    dispatcher = protected(MODULE, 65536, MODULE_SHA256)
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location('qualification_manage', MODULE)
    module = importlib.util.module_from_spec(spec)
    exec(compile(dispatcher, str(MODULE), 'exec'), module.__dict__)
    return module


def terminal(M):
    expected = dict(LoadState='loaded', ActiveState='inactive', Result='success',
                    MainPID='0', Job='', ExecMainCode='1', ExecMainStatus='0')
    observed = M.D.properties('a733-managed-autokvm.service', tuple(expected))
    require(observed == expected, 'managed service is not terminal successful exited')
    return observed


def retain(raw):
    return dict(bytes=len(raw), sha256=digest(raw),
                data_base64=base64.b64encode(raw).decode('ascii'))


def qualify(expected_boot):
    result = dict(success=False, stage='source', hardware_acceptance=False,
                  power_authorized=False, nvme_synchronization=True,
                  expected_boot_id=expected_boot, guest=GUEST, cleanup_errors=[])
    stack = lease = admission = policy = None
    M = None
    creator = (os.getpid(), threading.get_ident())
    try:
        require(os.getresuid() == (0, 0, 0) and
                threading.current_thread() is threading.main_thread(), 'root main thread required')
        M = load_installed()
        P, L, A = M.P, M.L, M.A
        CP = P.CP
        require(L is CP.LIFECYCLE and A is L.ADMISSION, 'same module family required')
        stack = P.Resources()

        def guard():
            require((os.getpid(), threading.get_ident()) == creator and
                    threading.current_thread() is threading.main_thread() and
                    os.getresuid() == (0, 0, 0), 'original root main thread required')
            require(A._boot_id() == expected_boot, 'current boot changed or stale boot requested')
            P.installed_guard(expected_boot)
            if policy is not None:
                policy.check()

        def live():
            guard()
            require(type(admission) is A.Admission and admission.bootstrap_owner is None and
                    type(lease) is A.Lease and not lease.exclusive and
                    lease.admission is admission, 'canonical existing shared admission required')
            admission.recheck()
            lease.recheck()
            A._held_lock(lease.fd, 'READ', creator[0])

        guard()
        result['stage'] = 'admission'
        policy = stack.enter_context(P.BOOT_POLICY.Policy(expected_boot))
        admission = A.Admission.open_existing(expected_boot, P.NORMAL.local_kvm_gid(),
                                               P.NORMAL.manager_state)
        require(type(admission) is A.Admission and admission.bootstrap_owner is None,
                'canonical existing admission required')
        lease = admission.admit()
        live()
        controller = stack.enter_context(L.StateController(policy.value['storage'], lease,
                                                            boot_id=expected_boot))
        lock = stack.enter_context(admission.guest_lock(GUEST, lease, create=False))

        def physical():
            live()
            controller.context.guard()
            lock.recheck()

        store = controller._head_store(stack, GUEST, physical)
        runs = stack.enter_context(store.child('runs'))
        head_raw = store.read('head.json', P.HEAD.LIMIT)
        head = P.HEAD.validate_head(head_raw)
        require(P.HEAD.canonical(head) == head_raw and head['status'] == 'ready' and
                head['sequence'] > 0 and head['name'] == GUEST and
                head['last_boot_id'] == expected_boot, 'current completed canonical ready head required')
        run_raw = runs.read(head['run_id'] + '.json', P.HEAD.LIMIT)
        run = L.validate_run(run_raw)
        require(L.canonical_run(run) == run_raw and run['run_id'] == head['run_id'] and
                run['name'] == GUEST and run['boot_id'] == expected_boot and
                run['automatic'] is True and run['fixture_profile'] == 'managed-power-key-v1' and
                run['shutdown_profile'] == P.REGISTRY.SHUTDOWN_PROFILE,
                'current automatic managed fixture required')
        result['stage'] = 'quiescence-before'
        result['unit_before'] = terminal(M)
        P.MAINTENANCE.consumer_check()
        result['census_before'] = 'no KVM consumers'
        physical()
        result['stage'] = 'authenticate'
        authentication = CP.authenticate_ready_cohort(controller, GUEST, guest_lock=lock,
                                                       head_data=head_raw)
        result['authentication'] = authentication
        require(authentication.get('success') is True and authentication.get('stage') == 'complete' and
                authentication.get('publications') == [] and not authentication.get('close_error') and
                not authentication.get('cleanup_errors'), 'authentication incomplete or published records')
        result['stage'] = 'retain'
        records = result['records'] = {'head.json': retain(head_raw)}
        limits = {'.json': P.HEAD.LIMIT, '.intent.json': CP.RECORDS.INTENT_LIMIT,
                  '.outcome.json': CP.RECORDS.OUTCOME_LIMIT, '.fixture.json': CP.RECORDS.FIXTURE_LIMIT,
                  '.completed.json': CP.RECORDS.COMPLETION_LIMIT, '.console': CP.RECORDS.CONSOLE_LIMIT}
        retained = {}
        for suffix, limit in limits.items():
            name = head['run_id'] + suffix
            raw = runs.read(name, limit)
            if suffix in ('.intent.json', '.outcome.json', '.completed.json'):
                key = {'.intent.json': 'intent_sha256', '.outcome.json': 'outcome_sha256',
                       '.completed.json': 'completion_sha256'}[suffix]
                require(digest(raw) == authentication.get(key), 'authenticated record changed')
            retained[name] = (raw, limit)
            records['runs/' + name] = retain(raw)
        require(retained[head['run_id'] + '.json'][0] == run_raw, 'run changed')
        # Reuse authenticated typed intent/edge constructors, retaining all
        # immutable transition references as well as the complete run cohort.
        intent_raw = retained[head['run_id'] + '.intent.json'][0]
        intent = CP.RECORDS.validate_intent(intent_raw)
        for suffix, field in (('.fixture.json', 'fixture'), ('.console', 'console')):
            raw = retained[head['run_id'] + suffix][0]
            require(dict(bytes=len(raw), sha256=digest(raw)) == intent[field],
                    'authenticated fixture/console changed')
        heads = CP._heads(run, intent_raw, intent)
        transitions = stack.enter_context(store.child('transitions'))
        for previous, new in zip(heads, heads[1:]):
            old_raw, new_raw = P.HEAD.canonical(previous), P.HEAD.canonical(new)
            name = digest(old_raw) + '-' + digest(new_raw) + '.json'
            raw = transitions.read(name, L.TRANSITION_LIMIT)
            require(raw == L.head_transition(old_raw, new_raw), 'transition changed')
            records['transitions/' + name] = retain(raw)
        for name, (raw, limit) in retained.items():
            require(runs.read(name, limit, pin=digest(raw)) == raw, 'retained run cohort changed')
        require(store.read('head.json', P.HEAD.LIMIT) == head_raw, 'head changed')
        # Authentication has released its disk/registry locks. Acquire registry
        # only now; never call authentication or disk fsync while holding it.
        with P.Resources() as registry_stack:
            registry_lock = registry_stack.enter_context(admission.registry_lock(lease))
            def registry_guard():
                physical()
                registry_lock.recheck()
            _, registry_raw, registry = controller._registry(registry_stack, registry_guard)
            require(registry is not None and digest(registry_raw) == authentication.get('registry_sha256'),
                    'authenticated registry changed')
            entry = M.entry_for(registry, GUEST)
            require(all(entry[key] == head[key] for key in ('name', 'uid', 'gid', 'manifest_sha256')),
                    'registry/head guest binding changed')
            records['autostart.json'] = retain(registry_raw)
            for name, (raw, limit) in retained.items():
                require(runs.read(name, limit, pin=digest(raw)) == raw, 'final run cohort changed')
            require(store.read('head.json', P.HEAD.LIMIT) == head_raw, 'final head changed')
            registry_guard()
        result['unit_after'] = terminal(M)
        P.MAINTENANCE.consumer_check()
        result['census_after'] = 'no KVM consumers'
        physical()
        result['stage'] = 'cleanup'
    except BaseException as error:
        result['error'] = type(error).__name__ + ': ' + str(error)[:1024]
        if M is not None:
            result['failure'] = M.P.failure_detail(error)
    finally:
        for label, resource in (('resources', stack), ('lease', lease), ('admission', admission)):
            if resource is not None:
                try:
                    resource.close()
                except BaseException as error:
                    result['cleanup_errors'].append(dict(resource=label, error=str(error)[:512]))
        if not result.get('error') and not result['cleanup_errors']:
            result.update(success=True, stage='complete')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--qualify', action='store_true', help='authorize NVMe fsync qualification')
    parser.add_argument('--expected-boot-id')
    args = parser.parse_args(argv)
    if not args.qualify:
        result = dict(operation='plan', qualification_requested=False, guest=GUEST,
                      hardware_acceptance=False, power_authorized=False, nvme_synchronization=False)
    else:
        try:
            require(args.expected_boot_id is not None and
                    str(uuid.UUID(args.expected_boot_id)) == args.expected_boot_id,
                    '--qualify requires canonical --expected-boot-id UUID')
        except (ValueError, AttributeError) as error:
            parser.error(str(error))
        if sys.flags.isolated != 1 or sys.flags.dont_write_bytecode != 1:
            parser.error('qualification requires python3 -I -B')
        result = qualify(args.expected_boot_id)
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get('success', result.get('operation') == 'plan') else 1


if __name__ == '__main__':
    sys.exit(main())
