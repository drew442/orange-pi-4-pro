#!/usr/bin/python3
"""Root-only installed managed guest controls; receipts are not hardware acceptance.

Start queues once after protected request publication. Lost acknowledgements are
never retried. Disable changes future policy and leaves a current run untouched.
"""
import argparse
from contextlib import ExitStack
import importlib.util
import os
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


def validate(operation, name=None, pin=None, fixture='none'):
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


def stop_properties(unit, remaining):
    keys = ('ActiveState', 'SubState', 'Result', 'Job')
    text = D.command(['/usr/bin/systemctl', 'show', '--all', '--no-pager',
                      '--property=' + ','.join(keys), '--', unit], timeout=min(10, remaining))
    values = {}
    for row in text.splitlines():
        key, sep, value = row.partition('=')
        require(sep and key in keys and key not in values, 'invalid stop properties')
        values[key] = value
    require(set(values) == set(keys), 'missing stop properties')
    return values


def stop(controller, name, guard, result):
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


def manage(operation, name=None, pin=None, fixture='none'):
    result = dict(operation=operation, name=name, success=False, stage='validate',
                  hardware_acceptance=False, no_op=False, queue_attempted=False,
                  queue_acknowledged=False, queue_uncertain=False, cleanup_errors=[])
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
        validate(operation, name, pin, fixture)
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
            stop(controller, name, physical, result)
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
        if operation == 'enroll':
            command.add_argument('--fixture-profile', choices=P.REGISTRY.FIXTURE_PROFILES, default='none')
    args = parser.parse_args(argv)
    receipt = manage(args.operation, getattr(args, 'name', None),
                     getattr(args, 'manifest_sha256', None), getattr(args, 'fixture_profile', 'none'))
    # Same bounded receipt format as the supervised runtime, including nested failures.
    runtime = L.CONTEXT.module('manage_receipt', 'scripts/run-managed-guest.py')
    print(runtime.json_receipt(receipt))
    return 0 if receipt['success'] else 1


if __name__ == '__main__':
    sys.exit(main())
