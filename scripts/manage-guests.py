#!/usr/bin/python3
"""Root-only installed managed guest controls; receipts are not hardware acceptance.

Start queues once after protected request publication. Lost acknowledgements are
never retried. Disable changes future policy and leaves a current run untouched.
"""
import argparse
from contextlib import ExitStack
import importlib.util
import os
import signal
import select
from pathlib import Path
import sys
import threading
import time

BASE = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('manage_dispatch', BASE / 'scripts/dispatch-guest-boot.py')
D = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(D)
P = D.PREP
A, L, U = P.ADMISSION, P.LIFECYCLE, P.UNITS
EXPLICIT = L.CONTEXT.module('manage_explicit', 'scripts/guest-explicit-request.py')
require = P.require
OPERATIONS = ('status', 'enroll', 'enable', 'disable', 'start', 'stop')


def validate(operation, name=None, pin=None, fixture='none', expected=None):
    if expected is not None:
        require(operation == 'stop' and type(expected) is dict and
                set(expected) == {'boot_id', 'run_id', 'main_pid', 'main_start_ticks'},
                'all four expected identity arguments are required for stop only')
        for key in ('boot_id', 'run_id'):
            A._uuid(expected[key])
        for key in ('main_pid', 'main_start_ticks'):
            require(type(expected[key]) is int and 0 < expected[key] <= (1 << 63) - 1,
                    'expected process identity must be positive')
    require(operation in OPERATIONS, 'unknown operation')
    require(fixture in P.REGISTRY.FIXTURE_PROFILES, 'unsupported fixture profile')
    if operation == 'status':
        require(name is None and pin is None and fixture == 'none', 'status takes no guest arguments')
        return
    A._guest_name(name)
    require(name != 'firstkvm', 'firstkvm must remain manual')
    if operation in ('enroll', 'enable'):
        P.REGISTRY._digest(pin)
    else:
        require(pin is None, 'manifest pin is only accepted for enroll/enable')
    require(operation == 'enroll' or fixture == 'none', 'fixture is only accepted for enroll')


def entry_for(registry, name):
    require(registry is not None, 'managed registry is unconfigured')
    matches = [entry for entry in registry['guests'] if entry['name'] == name]
    require(len(matches) == 1, 'registered managed guest required')
    return matches[0]


def inactive(unit):
    value = D.properties(unit, ('LoadState', 'ActiveState', 'SubState', 'Job'))
    require(value['LoadState'] in ('loaded', 'not-found') and
            value['ActiveState'] in ('inactive', 'failed') and value['Job'] == '',
            'managed unit active or has a pending job')


def retain_units(stack, texts, guard, *, publish):
    directory = stack.enter_context(P.RamDirectory(P.UNIT_DIRECTORY, 0o755, guard))
    sources = []
    for name, data in sorted(texts.items()):
        if publish:
            # Create-once publication refuses different bytes and never replaces.
            P.publish(directory, name, data, 0o444, P.UNIT_LIMIT)
        source = stack.enter_context(P.Source(directory.fd, name, 0o444,
                                              P.UNIT_LIMIT, directory.check))
        require(source.data == data, 'fixed managed unit bytes differ')
        sources.append(source)
    return sources


def verify_account(config, guard):
    """Observe existing locked RAM identity; never create or change accounts."""
    guard()
    try:
        accounts = P.POLICY.ACCOUNTS
        require(accounts.validate_accounts({'guests': [config]}, accounts.snapshot(), complete=True),
                'locked guest account unavailable')
    except Exception as error:
        raise RuntimeError('locked RAM guest identity unavailable; reboot through guest preparation '
                           'or complete the supported account workflow before enrollment') from error
    guard()


def prepare_start(controller, name, guard, result):
    """Retain real guest/disk custody and final registry lock through issue()."""
    with P.Resources() as stack:
        guest = stack.enter_context(controller.admission.guest_lock(name, controller.lease, create=False))
        initial = P._registry(controller, guard)
        entry = entry_for(initial, name)
        require(entry['enabled'] is True, 'enabled registered guest required')
        pin = entry['manifest_sha256']
        try:
            inspection = stack.enter_context(P.POLICY.Inspection(name, pin))
        except Exception:
            result['inspection_guidance'] = (
                'Inspect current enrollment/storage; if locked RAM accounts are missing, '
                'reboot through guest preparation or complete the supported pre-enrollment '
                'account workflow')
            raise

        def checked():
            guard()
            guest.recheck()
            controller._inspection(inspection, name, pin)
            require(controller.admission.is_managed(name, controller.lease, guest), 'managed marker missing')

        checked()
        store = controller._head_store(stack, name, checked)
        raw = store.read('head.json', P.HEAD.LIMIT)
        head = P.HEAD.validate_head(raw)
        controller._storage(head)
        require(head['status'] == 'ready' and head['manifest_sha256'] == pin and
                all(head[key] == entry[key] for key in ('name', 'uid', 'gid')), 'ready head differs')
        registry_lock = stack.enter_context(controller.admission.registry_lock(controller.lease))

        def locked():
            checked()
            registry_lock.recheck()

        reg_store, reg_raw, registry = controller._registry(stack, locked)
        require(registry is not None and P.REGISTRY.canonical(registry) == reg_raw and
                entry_for(registry, name) == entry, 'fresh registry differs')
        L._verify_ready(controller, name, guest_lock=guest, inspection=inspection,
                        head_store=store, head_data=raw, registry=(reg_store, reg_raw, registry))
        configs = P._configs(controller, registry['guests'], locked)
        memory = P.physical_memory()
        result['budget'] = dict(U.check_budget(configs, memory))
        config = inspection.manifest['config']
        verify_account(config, locked)
        require([c for c in configs if c['name'] == name] == [config], 'enabled config differs')
        unit = 'a733-managed-' + name + '.service'
        texts = {U.SLICE_NAME: U.render_slice(memory).encode('ascii'),
                 unit: U.render_managed(config).encode('ascii')}
        inactive(unit)
        sources = retain_units(stack, texts, locked, publish=True)
        D.verify_loaded(texts, configs, memory)
        inactive(unit)
        require(P._configs(controller, registry['guests'], locked) == configs,
                'enabled configurations changed')
        require(store.read('head.json', P.HEAD.LIMIT) == raw, 'ready head changed')
        for source in sources:
            source.check()
        locked()
        verify_account(config, locked)
        result['request'] = EXPLICIT.issue(controller, name, pin, raw, P.digest(texts[unit]),
                                            guest, locked, inspection=inspection, registry_entry=entry)
        require(result['request']['success'], 'explicit request publication failed')
        result['no_op'] = result['request']['no_op']


def stop_properties(unit, remaining, keys=('ActiveState', 'SubState', 'Result', 'Job')):
    text = D.command(['/usr/bin/systemctl', 'show', '--all', '--no-pager',
                      '--property=' + ','.join(keys), '--', unit], timeout=min(10, remaining))
    values = {}
    for row in text.splitlines():
        key, sep, value = row.partition('=')
        require(sep and key in keys and key not in values, 'invalid stop properties')
        values[key] = value
    require(set(values) == set(keys), 'missing stop properties')
    return values



def bound_process(unit, name, expected):
    """Authenticate the precise live supervisor before and after pidfd_open."""
    value = D.properties(unit, ('LoadState', 'ActiveState', 'SubState', 'MainPID', 'Job',
                                'ControlGroup'))
    cgroup = '/a733.slice/a733-guests.slice/' + unit
    require(value == dict(LoadState='loaded', ActiveState='active', SubState='running',
                          MainPID=str(expected['main_pid']), Job='', ControlGroup=cgroup),
            'expected managed supervisor is not current')
    pid = expected['main_pid']
    identity, parent = A._process(pid)
    require(identity == dict(pid=pid, start_ticks=expected['main_start_ticks']) and parent == 1,
            'supervisor process identity changed')
    status = A._status(pid)
    require(status.get('Uid') == status.get('Gid') == ['0'] * 4,
            'supervisor root credentials differ')
    require(A._proc_read('/proc/' + str(pid) + '/cgroup') ==
            ('0::' + cgroup + '\n').encode('ascii'), 'supervisor cgroup differs')
    require(A._proc_read('/proc/' + str(pid) + '/cmdline') ==
            b'/usr/bin/python3\0-I\0-B\0' + U.SUPERVISOR.encode('ascii') + b'\0' +
            name.encode('ascii') + b'\0', 'supervisor full command differs')
    # /usr/bin/python3 is normally a symlink to the versioned interpreter.
    require(os.path.samefile('/proc/' + str(pid) + '/exe', '/usr/bin/python3'),
            'supervisor interpreter executable differs')
    require(A._process(pid) == (identity, parent), 'supervisor changed during inspection')
    return value


class BoundObservationPending(Exception):
    """A legal producer publication window needs another bounded observation."""


def bound_checkpoint_intent(raw, run, witness):
    """Validate an observational intent and replay its exact typed head edges."""
    CP = P.CP
    intent = CP.RECORDS.validate_intent(raw)
    require(CP.RECORDS.canonical_intent(intent) == raw and
            CP.common(intent) == CP.common(run) and
            intent['admission_sha256'] == witness['admission_sha256'] and
            intent['old_manifest']['sha256'] == run['manifest_sha256'] and
            intent['registry']['observed_generation'] >= run['registry_generation'],
            'bound checkpoint intent/admission differs')
    if 'checkpoint_intent_raw' in witness:
        require(raw == witness['checkpoint_intent_raw'], 'immutable checkpoint intent changed')
    witness['checkpoint_intent_raw'] = raw
    return intent, [P.HEAD.canonical(h) for h in CP._heads(run, raw, intent)[2:]]


def bound_metadata(controller, stack, name, expected, entry, guard, *, ready=False,
                   witness=None, after_signal=False):
    """Observe legal protected run edges; never authenticate disk/cohort custody."""
    witness = {} if witness is None else witness
    CP = P.CP
    guard()
    require(A._boot_id() == expected['boot_id'] == controller.boot_id, 'expected boot differs')
    _, raw_registry, registry = controller._registry(stack, guard)
    require(registry is not None and P.REGISTRY.canonical(registry) == raw_registry,
            'noncanonical bound registry')
    current_entry = entry_for(registry, name)
    stable = set(entry) - {'manifest_sha256'}
    require(set(current_entry) == set(entry) and all(current_entry[k] == entry[k] for k in stable),
            'bound registry identity/policy differs')
    controller._storage(registry)
    store = controller._head_store(stack, name, guard)
    raw = store.read('head.json', P.HEAD.LIMIT)
    head = P.HEAD.validate_head(raw)
    require(P.HEAD.canonical(head) == raw, 'noncanonical protected head')
    controller._storage(head)
    require(head['last_boot_id'] == expected['boot_id'] and head['run_id'] == expected['run_id'] and
            all(head[k] == entry[k] for k in ('name', 'uid', 'gid')),
            'bound head identity differs')
    runs = stack.enter_context(store.child('runs'))
    raw_run = runs.read(expected['run_id'] + '.json', P.HEAD.LIMIT)
    run = L.validate_run(raw_run)
    require(L.canonical_run(run) == raw_run and
            run['boot_id'] == expected['boot_id'] and run['run_id'] == expected['run_id'] and
            all(run[k] == entry[k] for k in ('name', 'uid', 'gid', 'manifest_sha256',
                                            'fixture_profile', 'shutdown_profile')),
            'bound protected run differs')
    controller._storage(run)
    if 'run_raw' in witness:
        require(raw_run == witness['run_raw'], 'immutable bound run changed')
    else:
        require(not after_signal, 'original run witness required')
        witness['run_raw'] = raw_run
        witness['admission_sha256'] = P.digest(raw_run)
    admitted = P.HEAD.admit(run['previous_head'], run['manifest_sha256'], run['run_id'],
                            run['boot_id'], expected_head_sha256=run['previous_head_sha256'],
                            automatic=run['automatic'])
    running = P.HEAD.mark_running(admitted, expected_head_sha256=P.digest(P.HEAD.canonical(admitted)))
    stopping = P.HEAD.mark_stopping(running, expected_head_sha256=P.digest(P.HEAD.canonical(running)))
    legal = [P.HEAD.canonical(running)]
    pending = False
    if not after_signal:
        require(current_entry == entry and raw == legal[0], 'original running binding changed')
    else:
        legal.append(P.HEAD.canonical(stopping))
        intent_name = expected['run_id'] + '.intent.json'
        try:
            intent_raw = runs.read(intent_name, CP.RECORDS.INTENT_LIMIT)
        except FileNotFoundError as error:
            if error.filename != intent_name:
                raise
            intent_raw = None
        if intent_raw is None:
            require('checkpoint_intent_raw' not in witness, 'retained checkpoint intent disappeared')
            require(current_entry == entry, 'registry changed without checkpoint intent')
        else:
            intent, legal = bound_checkpoint_intent(intent_raw, run, witness)
            newpin = intent['new_manifest']['sha256']
            require(current_entry['manifest_sha256'] in (entry['manifest_sha256'], newpin) and
                    registry['generation'] >= intent['registry']['observed_generation'],
                    'bound registry manifest/generation conflicts')
            # Registry is read first. An old registry with a new ready head is
            # expected while the producer publishes ready before merging registry.
            if raw != legal[-1]:
                require(current_entry['manifest_sha256'] == entry['manifest_sha256'],
                        'registry advanced before legal ready head')
            else:
                pending = current_entry['manifest_sha256'] != newpin
                completed_name = expected['run_id'] + '.completed.json'
                try:
                    completed_raw = runs.read(completed_name, CP.RECORDS.COMPLETION_LIMIT)
                except FileNotFoundError as error:
                    if error.filename != completed_name:
                        raise
                    pending = True
                else:
                    completed = CP.RECORDS.validate_completion(completed_raw)
                    require(CP.RECORDS.canonical_completion(completed) == completed_raw and
                            CP.common(completed) == CP.common(run) and
                            completed['intent_sha256'] == P.digest(intent_raw) and
                            completed['outcome_sha256'] == intent['outcome']['sha256'] and
                            CP.decoded(completed['ready_head']) == raw and
                            completed['manifest_sha256'] == newpin,
                            'bound completion differs')
                    # A completion newer than the first registry read is another
                    # legal publication window; validate it on the next poll.
                    if completed['registry_generation'] > registry['generation']:
                        pending = True
                    else:
                        require(completed['registry_generation'] >= intent['registry']['observed_generation'] and
                                (completed['registry_generation'] != registry['generation'] or
                                 completed['registry_sha256'] == P.digest(raw_registry)),
                                'bound completion registry differs')
        require(raw in legal, 'bound head is not a legal checkpoint transition')
    # A concurrent legal head publication is pending, never an accepted snapshot.
    fresh = store.read('head.json', P.HEAD.LIMIT)
    fresh_head = P.HEAD.validate_head(fresh)
    require(P.HEAD.canonical(fresh_head) == fresh, 'noncanonical fresh protected head')
    if after_signal and fresh != raw and fresh not in legal:
        # Intent/checkpoint may have published after our first intent lookup.
        # Authenticate that newly visible transition before treating it as pending.
        fresh_intent = runs.read(expected['run_id'] + '.intent.json', CP.RECORDS.INTENT_LIMIT)
        _, fresh_legal = bound_checkpoint_intent(fresh_intent, run, witness)
        require(fresh in fresh_legal, 'bound metadata changed to conflicting head')
    else:
        require(fresh in legal, 'bound metadata changed to conflicting head')
    require(runs.read(expected['run_id'] + '.json', P.HEAD.LIMIT) == raw_run,
            'immutable bound run changed during inspection')
    guard()
    if fresh != raw:
        require(after_signal, 'bound metadata changed before signal')
        raise BoundObservationPending('legal checkpoint head publication in progress')
    if ready and (head['status'] != 'ready' or pending):
        raise BoundObservationPending('ready/registry/completion publication pending')
    return head


def bound_stop(controller, name, guard, result, expected, unit, entry, sources):
    """One SIGTERM to a pinned process, never a name-based stop or escalation.

    Direct signaling does not arm PID1 TimeoutStopSec. The terminal wait remains
    bounded; timeout or any ambiguous observation requires operator inspection.
    """
    result.update(expected_identity=dict(expected), stop_uncertain=True,
                  intent_published=False, intent_uncertain=False, signal_attempted=False,
                  signal_acknowledged=False, signal_uncertain=False,
                  pid1_stop_timeout_armed=False, metadata_confers_run_trust=False,
                  completed_cohort_qualification_required=True)
    pidfd = None
    try:
        require(callable(getattr(os, 'pidfd_open', None)) and
                callable(getattr(signal, 'pidfd_send_signal', None)), 'native pidfd support required')
        with P.Resources() as stack:
            witness = {}

            def metadata(*, ready=False):
                # Each poll owns and closes its readers; never accumulate FDs for 420s.
                with P.Resources() as readers:
                    return bound_metadata(controller, readers, name, expected, entry, guard, ready=ready,
                                          witness=witness, after_signal=result['signal_acknowledged'])

            require(metadata()['status'] == 'running', 'running expected head required')
            bound_process(unit, name, expected)
            pidfd = os.pidfd_open(expected['main_pid'], 0)
            bound_process(unit, name, expected)
            require(metadata()['status'] == 'running', 'expected run changed before intent')
            ram = stack.enter_context(P.RamDirectory(P.PREPARED_DIRECTORY, 0o700, guard))
            directory = stack.enter_context(P.STORE.RootDirectory(
                ram.fd, 'bound-stop', guard=ram.check, create=True))
            intent = dict(schema=1, profile='a733-bound-stop-v1', name=name, **expected)
            result.update(stage='bound-stop-intent', intent_uncertain=True)
            result['intent'] = directory.publish(expected['boot_id'] + '-' + expected['run_id'] + '.json',
                                                 P.encoded(intent))
            require(result['intent']['durable'], 'bound stop intent durability uncertain')
            result.update(intent_published=True, intent_uncertain=False)
            for source in sources:
                source.check()
            directory.check()
            require(metadata()['status'] == 'running', 'expected run changed before signal')
            bound_process(unit, name, expected)
            guard()
            deadline, progress = time.monotonic() + 420, 0
            result.update(stage='bound-stop-signal', signal_attempted=True, signal_uncertain=True)
            signal.pidfd_send_signal(pidfd, signal.SIGTERM, None, 0)
            result.update(signal_acknowledged=True, signal_uncertain=False, stage='wait-bound-stop')
            while True:
                guard()
                remaining = deadline - time.monotonic()
                require(remaining > 0, 'bound stop timeout; uncertain; do not retry')
                try:
                    metadata()
                except BoundObservationPending:
                    time.sleep(min(1, remaining))
                    continue
                value = stop_properties(unit, remaining, ('LoadState', 'ActiveState', 'SubState',
                                        'MainPID', 'Result', 'Job', 'ExecMainCode', 'ExecMainStatus'))
                result['unit_status'] = value
                if value['ActiveState'] in ('inactive', 'failed'):
                    require(value == dict(LoadState='loaded', ActiveState='inactive', SubState='dead',
                            MainPID='0', Result='success', Job='', ExecMainCode='1', ExecMainStatus='0'),
                            'bound service not terminal successful')
                    try:
                        metadata(ready=True)
                    except BoundObservationPending:
                        time.sleep(min(1, remaining))
                        continue
                    result.update(stopped=True, stop_uncertain=False)
                    return
                require(value['MainPID'] == str(expected['main_pid']) and value['Job'] == '',
                        'supervisor replaced during bound stop')
                # A readable pinned pidfd witnesses exit even while PID1 still
                # displays the old MainPID. Keep polling metadata/terminal state;
                # this does not authorize any further signal.
                poller = select.poll()
                poller.register(pidfd, select.POLLIN)
                events = poller.poll(0)
                require(all(fd == pidfd and mask & (select.POLLIN | select.POLLHUP) and
                            not mask & ~(select.POLLIN | select.POLLHUP) for fd, mask in events),
                        'uncertain supervisor pidfd observation')
                if not events:
                    require(A._process(expected['main_pid'])[0] == dict(pid=expected['main_pid'],
                            start_ticks=expected['main_start_ticks']), 'supervisor PID reused')
                if time.monotonic() >= progress:
                    print('Waiting for bound managed stop/checkpoint (up to 420 seconds)',
                          file=sys.stderr, flush=True)
                    progress = time.monotonic() + 15
                time.sleep(min(1, remaining))
    except BaseException as error:
        result['stop_uncertain'] = True
        if hasattr(error, 'receipt') and result.get('intent_uncertain'):
            result['intent'] = error.receipt
            result['intent_published'] = bool(error.receipt.get('published'))
        raise
    finally:
        if pidfd is not None:
            try:
                os.close(pidfd)
            except BaseException as error:
                result['stop_uncertain'] = True
                result['cleanup_errors'].append(dict(resource='stop-pidfd', error=str(error)[:512]))


def stop(controller, name, guard, result, expected=None):
    if expected is not None:
        result['stop_uncertain'] = True
    registry = P._registry(controller, guard)
    entry = entry_for(registry, name)
    # Disabled current runs still have a fixed supervisor contract.
    configs = P._configs(controller, [dict(entry, enabled=True)], guard)
    memory = P.physical_memory()
    unit = 'a733-managed-' + name + '.service'
    texts = {U.SLICE_NAME: U.render_slice(memory).encode('ascii'),
             unit: U.render_managed(configs[0]).encode('ascii')}
    with P.Resources() as stack:
        sources = retain_units(stack, texts, guard, publish=False)
        D.verify_loaded(texts, configs, memory)
        for source in sources:
            source.check()
        guard()
        if expected is not None:
            return bound_stop(controller, name, guard, result, expected, unit, entry, sources)
        deadline, progress = time.monotonic() + 420, 0
        result.update(stage='stop-request', queue_attempted=True, queue_uncertain=True, stop_uncertain=True)
        D.command(['/usr/bin/systemctl', '--no-block', 'stop', '--', unit], limit=1024)
        result.update(queue_acknowledged=True, queue_uncertain=False, stage='wait-stop')
        while True:
            guard()
            remaining = deadline - time.monotonic()
            require(remaining > 0, 'stop timeout; outcome uncertain; do not automatically retry')
            value = stop_properties(unit, remaining)
            result['unit_status'] = value
            if value['ActiveState'] in ('inactive', 'failed') and not value['Job']:
                require(value['ActiveState'] == 'inactive' and value['Result'] == 'success',
                        'managed service failed; inspect protected lifecycle evidence')
                result.update(stopped=True, stop_uncertain=False)
                return
            if time.monotonic() >= progress:
                print('Waiting for managed stop/checkpoint (up to 420 seconds)', file=sys.stderr, flush=True)
                progress = time.monotonic() + 15
            time.sleep(min(1, remaining))


def manage(operation, name=None, pin=None, fixture='none', expected=None):
    result = dict(operation=operation, name=name, success=False, stage='validate',
                  hardware_acceptance=False, no_op=False, queue_attempted=False,
                  queue_acknowledged=False, queue_uncertain=False, cleanup_errors=[])
    if operation == 'stop' and expected is not None:
        result.update(expected_identity=expected, stop_uncertain=True, intent_published=False,
                      intent_uncertain=False, signal_attempted=False, signal_acknowledged=False,
                      signal_uncertain=False, pid1_stop_timeout_armed=False,
                      metadata_confers_run_trust=False, completed_cohort_qualification_required=True)
    stack, admission, lease = P.Resources(), None, None
    creator, boot, policy = (os.getpid(), threading.get_ident()), None, None

    def guard():
        require((os.getpid(), threading.get_ident()) == creator and
                threading.current_thread() is threading.main_thread() and os.getresuid() == (0, 0, 0),
                'original root main thread required')
        require(A._boot_id() == boot, 'current boot changed')
        P.installed_guard(boot)
        if policy is not None:
            policy.check()

    def live():
        guard()
        require(type(admission) is A.Admission and admission.bootstrap_owner is None and
                type(lease) is A.Lease and not lease.exclusive and lease.admission is admission,
                'canonical existing shared admission required')
        admission.recheck()
        lease.recheck()
        A._held_lock(lease.fd, 'READ', creator[0])

    try:
        validate(operation, name, pin, fixture, expected)
        boot = A._boot_id()
        guard()
        result['stage'] = 'storage-policy'
        policy = stack.enter_context(P.BOOT_POLICY.Policy(boot))
        result['stage'] = 'admission'
        admission = A.Admission.open_existing(boot, P.NORMAL.local_kvm_gid(), P.NORMAL.manager_state)
        require(type(admission) is A.Admission and admission.bootstrap_owner is None,
                'canonical existing admission required')
        lease = admission.admit()
        live()
        controller = stack.enter_context(L.StateController(policy.value['storage'], lease, boot_id=boot))

        def physical():
            live()
            controller.context.guard()

        physical()
        result['stage'] = operation
        if operation == 'status':
            result['status'] = controller.status()
        elif operation == 'enroll':
            result['transaction'] = controller.enroll(name, pin, enabled=False, fixture_profile=fixture)
        elif operation == 'enable':
            result['transaction'] = controller.enable(name, pin)
        elif operation == 'disable':
            result['transaction'] = controller.disable(name)
        elif operation == 'stop':
            stop(controller, name, physical, result, expected)
        else:
            prepare_start(controller, name, physical, result)
        result['stage'] = 'prequeue-cleanup'
        stack.close()
        policy = None
        live()
        if operation == 'start' and not result['no_op']:
            with admission._gate():
                live()
                require(admission._read()['state'] == 'open' and admission.manager_state() in
                        ('starting', 'running', 'degraded'), 'PID1/admission stopping')
                result.update(stage='queue', queue_attempted=True, queue_uncertain=True)
                D.command(['/usr/bin/systemctl', '--no-block', 'start', '--',
                           'a733-managed-' + name + '.service'], limit=1024)
                result.update(queue_acknowledged=True, queue_uncertain=False)
        result.update(success=True, stage='complete')
    except BaseException as error:
        result.update(error=type(error).__name__ + ': ' + str(error)[:1024], failure=P.failure_detail(error))
        if operation == 'status':
            result['diagnostic'] = ('Status unavailable: inspect admission gate and storage preparation; '
                                    'this does not establish a guest failure')
    finally:
        for label, resource in (('resources', stack), ('lease', lease), ('admission', admission)):
            if resource is not None:
                try:
                    resource.close()
                except BaseException as error:
                    result['cleanup_errors'].append(dict(resource=label, error=str(error)[:512]))
        if result['cleanup_errors']:
            result.update(success=False, stage='cleanup')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    sub = parser.add_subparsers(dest='operation', required=True)
    sub.add_parser('status', allow_abbrev=False)
    for operation in OPERATIONS[1:]:
        command = sub.add_parser(operation, allow_abbrev=False)
        command.add_argument('name')
        if operation in ('enroll', 'enable'):
            command.add_argument('--manifest-sha256', required=True)
        if operation == 'stop':
            for key in ('boot-id', 'run-id', 'main-pid', 'main-start-ticks'):
                command.add_argument('--expected-' + key, type=int if key.startswith('main-') else str)
        if operation == 'enroll':
            command.add_argument('--fixture-profile', choices=P.REGISTRY.FIXTURE_PROFILES, default='none')
    args = parser.parse_args(argv)
    identity = {key: getattr(args, 'expected_' + key, None) for key in
                ('boot_id', 'run_id', 'main_pid', 'main_start_ticks')}
    expected = identity if any(value is not None for value in identity.values()) else None
    receipt = manage(args.operation, getattr(args, 'name', None),
                     getattr(args, 'manifest_sha256', None), getattr(args, 'fixture_profile', 'none'), expected)
    # Same bounded receipt format as the supervised runtime, including nested failures.
    runtime = L.CONTEXT.module('manage_receipt', 'scripts/run-managed-guest.py')
    print(runtime.json_receipt(receipt))
    return 0 if receipt['success'] else 1


if __name__ == '__main__':
    sys.exit(main())
