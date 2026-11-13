#!/usr/bin/python3
"""External, single-attempt SPI power request; default is a read-free plan."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import threading
from types import SimpleNamespace
import uuid

SOURCE = Path('/run/a733-spi-collector/request-spi-power.py')
QUALIFIER = SOURCE.with_name('qualify-managed-boot.py')
QUALIFIER_SHA256 = '938ef0355df0a764160c63e5d1c0532818888ec8135fba962830472f41025ae0'
QUALIFIER_BYTES = 15424
INTENT = Path('/run/a733-spi-power/request.json')
SELECTOR_SHA256 = '8755d4566ef2037b61251ededb242f3a41871c059ee97b990e0f396bd024f24b'
MARKER_SHA256 = 'ce1e1841cea22852f122f2c3de0c15fc146ded7a1f090c05d8e3ebe8be430b65'
STORAGE = dict(nvme_serial='134807190FC700149277',
               state_uuid='360dfb39-f49f-487e-b307-0bb887c854c5',
               guest_uuid='298b281a-d10e-44eb-bdb9-285e27eaafad')
ENVIRONMENT = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'}
CREATOR = os.getpid(), threading.get_ident()


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def original():
    require((os.getpid(), threading.get_ident()) == CREATOR and
            threading.current_thread() is threading.main_thread() and
            os.getresuid() == (0, 0, 0), 'original root main thread required')


def ram_directory(path, private=True):
    """Fixed /run placement, with no symlink or writable ancestor traversal."""
    require(path.is_absolute() and path.parts[:2] == ('/', 'run') and
            '..' not in path.parts, 'fixed RAM path required')
    device = os.stat('/run').st_dev
    rows = [line.split(' - ', 1) for line in Path('/proc/self/mountinfo').read_text().splitlines()]
    mounts = [(a.split(), b.split()) for a, b in rows]
    require(any(a[4] == '/run' and b[0] == 'tmpfs' and
                a[2] == f'{os.major(device)}:{os.minor(device)}' for a, b in mounts),
            '/run must be native tmpfs')
    for parent in reversed((path,) + tuple(path.parents)):
        info = os.lstat(parent)
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == info.st_gid == 0 and
                not info.st_mode & 0o022, 'unsafe RAM ancestor')
        if parent != Path('/'):
            require(info.st_dev == device, 'RAM placement changed')
    if private:
        require(stat.S_IMODE(os.lstat(path).st_mode) == 0o700, 'private RAM directory required')


def read_private(path, limit):
    ram_directory(path.parent)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == before.st_gid == 0 and
                stat.S_IMODE(before.st_mode) == 0o400 and before.st_nlink == 1 and
                before.st_dev == os.stat(path.parent).st_dev and 0 < before.st_size <= limit,
                'unsafe private source/intent')
        raw = os.read(fd, limit + 1)
        require(os.fstat(fd) == before and os.lstat(path) == before and
                len(raw) == before.st_size, 'private bytes changed')
        return raw
    finally:
        os.close(fd)


def load_qualifier():
    original()
    require(Path(__file__) == SOURCE, 'fixed external helper path required')
    require(set(os.listdir(SOURCE.parent)) == {SOURCE.name, QUALIFIER.name},
            'unexpected collector entry (including bytecode)')
    helper = read_private(SOURCE, 65536)
    raw = read_private(QUALIFIER, QUALIFIER_BYTES)
    require(len(raw) == QUALIFIER_BYTES and digest(raw) == QUALIFIER_SHA256,
            'qualifier source pin differs')
    namespace = {'__name__': 'spi_power_qualifier', '__file__': str(QUALIFIER)}
    exec(compile(raw, str(QUALIFIER), 'exec'), namespace)
    return SimpleNamespace(**namespace), digest(helper)


def sd_absent():
    with os.scandir('/sys/class/block') as entries:
        names = [entry.name for entry in entries]
    require('mmcblk1' not in names and not any(n.startswith('mmcblk1p') for n in names),
            'known SD namespace present')
    for name in names:
        if re.fullmatch(r'mmcblk[0-9]+', name):
            kind = (Path('/sys/class/block') / name / 'device/type').read_bytes()
            require(kind == b'MMC\n', 'SD or unknown MMC type present')


def storage_snapshot(M, policy):
    value = policy.value
    require(value['storage'] == STORAGE and value['target'] == '/dev/nvme0n1' and
            value['expected_model'] == 'SPCC M.2 PCIe SSD' and
            value['expected_bytes'] == 256060514304, 'fixed root storage policy differs')
    args = SimpleNamespace(target=value['target'], expected_model=value['expected_model'],
                           expected_serial=STORAGE['nvme_serial'],
                           expected_bytes=value['expected_bytes'], guest_uuid=STORAGE['guest_uuid'])
    snap = M.P.ACTIVATE.snapshot(args, True)
    require(snap['active']['slot'] == snap['state']['selector']['SLOT'] == 'B' and
            snap['active']['marker_sha256'] == MARKER_SHA256 and
            snap['state']['selector_sha256'] == SELECTOR_SHA256 and
            snap['state']['uuid'] == STORAGE['state_uuid'] and
            snap['guest']['uuid'] == STORAGE['guest_uuid'], 'running/selected B storage differs')
    return snap


def closed_record(admission, expected=None):
    admission.recheck()
    with admission._gate():
        record = admission._read()
        require(record['state'] == 'closed' and record['reason'] == 'shutdown' and
                (expected is None or record == expected), 'shutdown closure changed')
        return record


def authenticate(M, Q, policy, admission, lease, args, guard):
    P, L, CP = M.P, M.L, M.P.CP
    records = {}
    with P.Resources() as stack:
        controller = stack.enter_context(L.StateController(policy.value['storage'], lease,
                                                          boot_id=args.expected_boot_id))
        lock = stack.enter_context(admission.guest_lock('autokvm', lease, create=False))
        def physical():
            guard()
            controller.context.guard()
            lock.recheck()
        store = controller._head_store(stack, 'autokvm', physical)
        runs = stack.enter_context(store.child('runs'))
        head_raw = store.read('head.json', P.HEAD.LIMIT)
        head = P.HEAD.validate_head(head_raw)
        require(P.HEAD.canonical(head) == head_raw and head['status'] == 'ready' and
                head['sequence'] > 0 and head['name'] == 'autokvm' and
                head['last_boot_id'] == args.expected_boot_id and
                digest(head_raw) == args.expected_head_sha256, 'current expected ready head required')
        run_raw = runs.read(head['run_id'] + '.json', P.HEAD.LIMIT)
        run = L.validate_run(run_raw)
        require(L.canonical_run(run) == run_raw and run['run_id'] == head['run_id'] and
                run['name'] == 'autokvm' and run['boot_id'] == args.expected_boot_id and
                run['automatic'] is True and run['fixture_profile'] == 'managed-power-key-v1' and
                run['shutdown_profile'] == P.REGISTRY.SHUTDOWN_PROFILE,
                'current automatic completed fixture required')
        physical()
        auth = CP.authenticate_ready_cohort(controller, 'autokvm', guest_lock=lock, head_data=head_raw)
        require(auth.get('success') is True and auth.get('stage') == 'complete' and
                auth.get('publications') == [] and not auth.get('close_error') and
                not auth.get('cleanup_errors') and auth.get('registry_sha256') == args.expected_registry_sha256,
                'completed cohort authentication failed')
        retained = {'head.json': head_raw, 'runs/' + head['run_id'] + '.json': run_raw}
        for suffix, field, limit in (
                ('.intent.json', 'intent_sha256', CP.RECORDS.INTENT_LIMIT),
                ('.outcome.json', 'outcome_sha256', CP.RECORDS.OUTCOME_LIMIT),
                ('.completed.json', 'completion_sha256', CP.RECORDS.COMPLETION_LIMIT)):
            name = head['run_id'] + suffix
            raw = runs.read(name, limit)
            require(digest(raw) == auth.get(field), 'authenticated cohort reference changed')
            retained['runs/' + name] = raw
        # CP has released disk/registry resources. Only now take registry custody.
        with P.Resources() as registry_stack:
            registry_lock = registry_stack.enter_context(admission.registry_lock(lease))
            def registry_guard():
                physical()
                registry_lock.recheck()
            _, registry_raw, registry = controller._registry(registry_stack, registry_guard)
            require(registry is not None and digest(registry_raw) == args.expected_registry_sha256,
                    'authenticated registry changed')
            entry = M.entry_for(registry, 'autokvm')
            require(all(entry[key] == head[key] for key in ('name', 'uid', 'gid', 'manifest_sha256')),
                    'registry/head binding changed')
            retained['autostart.json'] = registry_raw
            require(store.read('head.json', P.HEAD.LIMIT) == head_raw and
                    runs.read(head['run_id'] + '.json', P.HEAD.LIMIT) == run_raw, 'head/run changed')
            for name, raw in retained.items():
                if name.startswith('runs/'):
                    require(runs.read(name[5:], len(raw), pin=digest(raw)) == raw, 'cohort reference changed')
            registry_guard()
        records = {name: Q.retain(raw) for name, raw in retained.items()}
        physical()
    return records


def publish_intent(value):
    raw = (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()
    require(len(raw) <= 256 * 1024, 'intent exceeds bound')
    ram_directory(INTENT.parent.parent, private=False)
    os.mkdir(INTENT.parent, 0o700)  # Any previous/partial directory permanently refuses this boot.
    ram_directory(INTENT.parent)
    directory = os.open(INTENT.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        fd = os.open(INTENT.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW |
                     os.O_CLOEXEC, 0o400, dir_fd=directory)
        try:
            require(os.write(fd, raw) == len(raw), 'partial intent publication')
            os.fsync(fd)
        finally:
            os.close(fd)
        os.fsync(directory)
    finally:
        os.close(directory)
    require(read_private(INTENT, 256 * 1024) == raw, 'intent readback changed')
    return raw


def emit(value):
    print(json.dumps(value, sort_keys=True), flush=True)


def execute(args):
    result = dict(operation='request-spi-power', success=False, hardware_acceptance=False,
                  request_id=args.request_id, expected_boot_id=args.expected_boot_id,
                  action=args.action, command_attempted=False, command_acknowledged=False,
                  stage='source', shutdown_close_attempted=False, shutdown_closed=None,
                  exclusive_acquired=False, exclusive_released=False,
                  intent_publication_attempted=False, intent_durable=False, cleanup_errors=[])
    policy = admission = lease = M = None
    try:
        Q, helper_pin = load_qualifier()
        M = Q.load_installed()
        P, A = M.P, M.A
        require(M.L is P.CP.LIFECYCLE and A is M.L.ADMISSION, 'same installed module family required')
        def guard():
            original()
            require(A._boot_id() == args.expected_boot_id, 'current boot differs')
            P.installed_guard(args.expected_boot_id)
            if policy is not None:
                policy.check()
        def boundary():
            guard()
            require(P.NORMAL.manager_state() in ('running', 'degraded'), 'PID1 is not normally running')
            storage_snapshot(M, policy)
            sd_absent()
            Q.terminal(M)
            P.MAINTENANCE.consumer_check()
        guard()
        result['stage'] = 'preconditions'
        policy = P.BOOT_POLICY.Policy(args.expected_boot_id)
        boundary()
        require(not os.path.lexists(INTENT.parent), 'existing or partial power intent blocks request')
        admission = A.Admission.open_existing(args.expected_boot_id, P.NORMAL.local_kvm_gid(),
                                               P.NORMAL.manager_state)
        require(type(admission) is A.Admission and admission.bootstrap_owner is None,
                'canonical existing admission required')
        admission.recheck()
        with admission._gate():
            require(admission._read()['state'] == 'open', 'admission must be open')
        result.update(stage='close-admission', shutdown_close_attempted=True)
        admission.close_gate('shutdown')
        record = closed_record(admission)
        result.update(stage='acquire-ex', shutdown_closed=True)
        lease = admission.shutdown_lease()  # Immediate EX, never SH upgrade.
        result['exclusive_acquired'] = True
        def authority():
            guard()
            require(type(lease) is A.Lease and lease.exclusive and lease.admission is admission,
                    'canonical shutdown EX required')
            lease.recheck()
            A._held_lock(lease.fd, 'WRITE', CREATOR[0])
            require(not os.get_inheritable(lease.fd), 'inheritable EX refused')
            closed_record(admission, record)
        authority()
        boundary()
        result['stage'] = 'authenticate'
        records = authenticate(M, Q, policy, admission, lease, args, authority)
        authority()
        boundary()
        result.update(stage='publish-intent', intent_publication_attempted=True)
        intent = publish_intent(dict(schema=1, boot_id=args.expected_boot_id,
            request_id=args.request_id, action=args.action, hardware_acceptance=False,
            helper_sha256=helper_pin, qualifier_sha256=QUALIFIER_SHA256,
            marker_sha256=MARKER_SHA256, selector_sha256=SELECTOR_SHA256,
            storage=policy.value, closure=record, records=records))
        result.update(intent_durable=True, stage='release-ex')
        authority()
        # All controller/guest/disk resources already closed by authenticate().
        releasing, lease = lease, None  # A close error is never retried.
        releasing.close()
        result.update(exclusive_released=True, stage='final-checks')
        guard()
        closed_record(admission, record)
        require(lease is None, 'EX remains before power command')
        require(load_qualifier()[1] == helper_pin and
                read_private(INTENT, 256 * 1024) == intent, 'source or intent changed')
        boundary()
        closed_record(admission, record)
        result['intent_sha256'] = digest(intent)
        emit(dict(result, stage='precommand', shutdown_closed=True, exclusive_released=True))
        result['stage'] = 'command'
        result['command_attempted'] = True  # Before invocation, including timeout/ack loss.
        completed = subprocess.run(['/usr/bin/systemctl', args.action], stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=ENVIRONMENT.copy(), timeout=10, check=False)
        result['command_acknowledged'] = completed.returncode == 0
        require(completed.returncode == 0, 'power command failed; do not retry')
        result.update(success=True, stage='complete')
    except BaseException as error:
        result['error'] = type(error).__name__ + ': ' + str(error)[:1024]
        if M is not None:
            try:
                result['failure'] = M.P.failure_detail(error)
            except BaseException as detail_error:
                result['failure_detail_error'] = str(detail_error)[:512]
    finally:
        for name, resource in (('lease', lease), ('admission', admission), ('policy', policy)):
            if resource is not None:
                try:
                    resource.close()
                except BaseException as error:
                    result['cleanup_errors'].append(dict(resource=name, error=str(error)[:512]))
        if result['cleanup_errors']:
            result['success'] = False
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--action', choices=('reboot', 'poweroff'))
    for flag in ('expected-boot-id', 'request-id', 'expected-head-sha256', 'expected-registry-sha256'):
        parser.add_argument('--' + flag)
    args = parser.parse_args(argv)
    if not args.execute:
        result = dict(operation='plan', hardware_acceptance=False, power_requested=False)
    else:
        try:
            require(args.action is not None, '--execute requires --action')
            for name in ('expected_boot_id', 'request_id'):
                value = getattr(args, name)
                require(value is not None and str(uuid.UUID(value)) == value and uuid.UUID(value).int != 0,
                        'canonical nonzero ' + name + ' required')
            for name in ('expected_head_sha256', 'expected_registry_sha256'):
                require(re.fullmatch('[0-9a-f]{64}', getattr(args, name) or '') is not None,
                        'lowercase SHA256 ' + name + ' required')
            require(sys.flags.isolated == sys.flags.dont_write_bytecode == 1,
                    'execution requires python3 -I -B')
        except (ValueError, AttributeError) as error:
            parser.error(str(error))
        result = execute(args)
    emit(result)
    return 0 if result.get('success', result.get('operation') == 'plan') else 1


if __name__ == '__main__':
    sys.exit(main())
