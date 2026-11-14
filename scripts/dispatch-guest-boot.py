#!/usr/bin/python3
"""Validate prepared native units and durably record one automatic queue attempt.

PID1 observations are software gates, never physical deployment acceptance.
An intent survives failed/lost acknowledgements; repeating dispatch never retries.
"""
import argparse
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import re
import select
import subprocess
import sys
import threading
import time

BASE = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('dispatch_prepare', BASE / 'scripts/prepare-guest-boot.py')
PREP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREP)
A, U = PREP.ADMISSION, PREP.UNITS
require, digest, encoded = PREP.require, PREP.digest, PREP.encoded
RECORD = 'dispatched.json'
PROFILE = 'a733-guest-boot-dispatch-v1'
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'}


def command(argv, *, limit=65536, timeout=10):
    """Fixed caller-owned argv, bounded output and wall time, no shell/retry."""
    child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, env=ENV.copy())
    data, deadline = b'', time.monotonic() + timeout
    try:
        while True:
            remaining = deadline - time.monotonic()
            require(remaining > 0 and select.select([child.stdout], [], [], remaining)[0],
                    'PID1 command timeout; acknowledgement uncertain')
            piece = os.read(child.stdout.fileno(), min(4096, limit + 1 - len(data)))
            if not piece:
                break
            data += piece
            require(len(data) <= limit, 'PID1 output exceeds bound')
        require(child.wait(timeout=max(.01, deadline - time.monotonic())) == 0,
                'PID1 command failed')
        return data.decode('ascii')
    finally:
        try:
            if child.poll() is None:
                child.kill()
        finally:
            try:
                child.wait(timeout=1)
            finally:
                child.stdout.close()


def intent_record(data):
    require(type(data) is bytes and 0 < len(data) <= PREP.RECORD_LIMIT, 'invalid dispatch size')
    value = json.loads(data, object_pairs_hook=A._pairs)
    require(type(value) is dict and set(value) == {'schema', 'profile', 'boot_id',
            'policy_sha256', 'registry_sha256', 'selected', 'units'}, 'invalid dispatch fields')
    require(type(value['schema']) is int and value['schema'] == 1 and value['profile'] == PROFILE,
            'invalid dispatch profile')
    A._uuid(value['boot_id'])
    for key in ('policy_sha256', 'registry_sha256'):
        PREP.REGISTRY._digest(value[key])
    names = value['selected']
    require(type(names) is list and len(names) <= PREP.NAME_LIMIT and names == sorted(set(names)),
            'invalid dispatch names')
    for name in names:
        A._guest_name(name)
        require(name != 'firstkvm', 'firstkvm must remain manual')
    require(type(value['units']) is dict and set(value['units']) == {U.SLICE_NAME} |
            {'a733-managed-' + n + '.service' for n in names}, 'invalid dispatch units')
    for pin in value['units'].values():
        PREP.REGISTRY._digest(pin)
    require(encoded(value) == data, 'noncanonical dispatch record')
    return value


def existing(directory, prepared, boot_id, policy_pin):
    try:
        source = PREP.Source(directory.fd, RECORD, 0o400, PREP.RECORD_LIMIT, directory.check)
    except FileNotFoundError as error:
        if error.filename != RECORD:
            raise
        return None
    with source:
        value = intent_record(source.data)
        require(value['boot_id'] == boot_id and value['policy_sha256'] == policy_pin,
                'dispatch boot/policy differs')
        require(set(value['selected']) <= set(prepared['eligible']) and all(
            prepared['units'].get(name) == pin for name, pin in value['units'].items()),
            'dispatch prepared units differ')
        source.check()
        return value


COMMON = ('LoadState', 'FragmentPath', 'DropInPaths', 'MemoryMax', 'MemorySwapMax', 'TasksMax')
SERVICE = ('User', 'Group', 'Type', 'Restart', 'Slice', 'KillMode', 'KillSignal',
           'TimeoutStopUSec', 'SendSIGKILL', 'NoNewPrivileges', 'CapabilityBoundingSet',
           'ExecStartPre', 'ExecStartPost', 'PrivateMounts', 'PrivateTmp', 'PrivateDevices',
           'ProtectSystem', 'RootDirectory', 'RootImage', 'PrivateUsers', 'After', 'Requires',
           'RequiresMountsFor')


def properties(unit, keys):
    text = command(['/usr/bin/systemctl', 'show', '--all', '--no-pager',
                    '--property=' + ','.join(keys), '--', unit])
    values = {}
    for line in text.splitlines():
        key, separator, value = line.partition('=')
        require(separator and key in keys and key not in values, 'invalid unit properties')
        values[key] = value
    # systemctl's human display omits empty command arrays, even with --all.
    # Only these two optional arrays may be absent. verify_loaded separately
    # requires the exact renderer fragment, reload and no drop-ins, so this
    # cannot authenticate an unobserved command override.
    for key in ('ExecStartPre', 'ExecStartPost'):
        if key in keys:
            values.setdefault(key, '')
    require(set(values) == set(keys), 'missing unit properties')
    return values


def unit_dependencies(text):
    """Decode systemctl's bounded ASCII unit-name list, never shell syntax.

    systemd 252 quotes names containing backslashes and doubles those
    backslashes. Only that C escape can occur in a legal serialized unit name.
    Retain literal unit-name \\xHH escapes, including in unquoted fixtures.
    """
    require(type(text) is str and len(text) <= 65536 and text.isascii(),
            'invalid dependency display size/encoding')
    names, offset = set(), 0
    while offset < len(text):
        if text[offset] == ' ':
            offset += 1
            continue
        quoted = text[offset] == '"'
        if quoted:
            offset += 1
        token = []
        while offset < len(text):
            char = text[offset]
            if quoted and char == '"':
                offset += 1
                break
            if not quoted and char == ' ':
                break
            if quoted and char == '\\':
                require(text[offset:offset + 2] == '\\\\',
                        'invalid dependency display escape')
                offset += 1
            token.append(char)
            require(len(token) <= 255, 'dependency unit name exceeds bound')
            offset += 1
        else:
            require(not quoted, 'unterminated dependency display quote')
        require(offset == len(text) or text[offset] == ' ',
                'invalid dependency display separator')
        name = ''.join(token)
        require(re.fullmatch(r'(?:[A-Za-z0-9:_.@-]|\\x[0-9A-Fa-f]{2})+\.'
                             r'(?:service|mount|slice|target|socket|device|swap|path|timer|scope|automount)',
                             name) is not None, 'invalid dependency unit name')
        require(name not in names and len(names) < 4096, 'duplicate/excess dependency units')
        names.add(name)
    return names


def verify_loaded(texts, configs, memory):
    """Exact protected fragment + reload + no drop-ins authenticates ExecStart.

    Do not parse systemd's escaped ExecStart display as shell syntax. Its source
    is the retained exact renderer bytes, with a single fixed ExecStart line.
    """
    command(['/usr/bin/systemctl', 'daemon-reload'], limit=1024)
    mounts = []
    for path in ('/run/a733-state', '/run/a733-guests'):
        result = command(['/usr/bin/systemd-escape', '--path', '--suffix=mount', path], limit=256)
        require(result.endswith('\n') and result.count('\n') == 1, 'invalid mount unit escape')
        mounts.append(result[:-1])
    by_name = {c['name']: c for c in configs}
    for unit in sorted(texts):
        is_slice = unit == U.SLICE_NAME
        value = properties(unit, COMMON if is_slice else COMMON + SERVICE)
        expected = {'LoadState': 'loaded', 'FragmentPath': PREP.UNIT_DIRECTORY + '/' + unit,
                    'DropInPaths': '', 'MemorySwapMax': '0', 'TasksMax': '4096' if is_slice else '256'}
        if is_slice:
            expected['MemoryMax'] = str((memory // U.MIB) * U.MIB - U.HOST_RESERVE_BYTES)
        else:
            name = unit[len('a733-managed-'):-len('.service')]
            expected.update(MemoryMax=str(by_name[name]['memory_max_mib'] * U.MIB), User='root',
                Group='root', Type='exec', Restart='no', Slice=U.SLICE_NAME, KillMode='mixed',
                KillSignal='15', TimeoutStopUSec='7min', SendSIGKILL='yes', NoNewPrivileges='yes',
                ExecStartPre='', ExecStartPost='', PrivateMounts='no', PrivateTmp='no',
                PrivateDevices='no', ProtectSystem='no', RootDirectory='', RootImage='', PrivateUsers='no')
            caps = value['CapabilityBoundingSet'].upper().split()
            require(len(caps) == len(set(caps)) and set(caps) == set(U.CAPABILITIES),
                    'loaded capability bound differs')
            deps = {'a733-guest-prepare.service', 'a733-guest-storage.service'} | set(mounts)
            after = unit_dependencies(value['After'])
            required = unit_dependencies(value['Requires'])
            require(deps <= after and deps <= required,
                    'loaded storage dependencies differ')
            require(set(value['RequiresMountsFor'].split()) == {'/run/a733-state', '/run/a733-guests'},
                    'loaded required mount paths differ')
        require(all(value[key] == wanted for key, wanted in expected.items()),
                'loaded unit contract differs: ' + unit)


def dispatch():
    receipt = dict(operation='dispatch-guest-boot', stage='validate', success=False,
                   hardware_acceptance=False, noop=False, selected=[], skipped=[],
                   intent_published=False, intent_uncertain=False, queue_attempted=False,
                   queue_acknowledged=False, queue_uncertain=False, cleanup_errors=[])
    resources, admission, lease = ExitStack(), None, None
    creator = os.getpid(), threading.get_ident()
    policy = None
    boot = None
    def guard():
        require((os.getpid(), threading.get_ident()) == creator and
                threading.current_thread() is threading.main_thread() and os.getresuid() == (0, 0, 0),
                'dispatch requires original root main thread')
        require(A._boot_id() == boot, 'boot changed')
        PREP.installed_guard(boot)
        if policy is not None:
            policy.check()
    def live():
        guard()
        admission.recheck()
        lease.recheck()
        require(type(lease) is A.Lease and not lease.exclusive and lease.admission is admission,
                'canonical shared lease required')
        A._held_lock(lease.fd, 'READ', creator[0])
    try:
        PREP.CONTEXT.MANAGEMENT.require_managed_runtime()
        boot = A._boot_id()
        guard()
        receipt['stage'] = 'storage-policy'
        policy = resources.enter_context(PREP.BOOT_POLICY.Policy(boot))
        pin = digest(PREP.BOOT_POLICY.SCHEMA.canonical(policy.value))
        storage = policy.value['storage']
        guard()
        receipt.update(boot_id=boot, policy_sha256=pin)
        receipt['stage'] = 'admission'
        admission = A.Admission.open_existing(boot, PREP.NORMAL.local_kvm_gid(), PREP.NORMAL.manager_state)
        require(type(admission) is A.Admission and admission.bootstrap_owner is None,
                'canonical existing admission required')
        prepared = PREP._noop(admission, policy, boot, pin, guard)
        directory = resources.enter_context(PREP.RamDirectory(PREP.PREPARED_DIRECTORY, 0o700, guard))
        prepared_source = resources.enter_context(PREP.Source(
            directory.fd, PREP.RECORD, 0o400, PREP.RECORD_LIMIT, directory.check))
        require(PREP.prepared_record(prepared_source.data) == prepared, 'prepared record changed')
        with admission._gate():
            require(admission._read()['state'] == 'open' and admission.manager_state() in
                    ('starting', 'running', 'degraded'), 'PID1/admission stopping')
            previous = existing(directory, prepared, boot, pin)
        if previous is not None:
            receipt.update(success=True, noop=True, selected=previous['selected'], intent_published=True,
                           stage='complete', queue_uncertain=True)
        else:
            lease = admission.admit()
            live()
            controller = resources.enter_context(PREP.LIFECYCLE.StateController(storage, lease, boot_id=boot))
            def physical():
                live()
                controller.context.guard()
            receipt['stage'] = 'fresh-registry-configs'
            registry = PREP._registry(controller, physical)
            configs = PREP._configs(controller, registry['guests'], physical)
            memory = PREP.physical_memory()
            receipt['budget'] = dict(U.check_budget(configs, memory))
            enabled = {c['name'] for c in configs}
            selected = sorted(enabled & set(prepared['eligible']))
            receipt.update(selected=selected, skipped=sorted(enabled - set(selected)))
            texts = {U.SLICE_NAME: U.render_slice(memory).encode('ascii')}
            for config in configs:
                if config['name'] in selected:
                    texts['a733-managed-' + config['name'] + '.service'] = U.render_managed(config).encode('ascii')
            units = resources.enter_context(PREP.RamDirectory(PREP.UNIT_DIRECTORY, 0o755, live))
            sources = []
            for name, data in sorted(texts.items()):
                require(prepared['units'].get(name) == digest(data), 'fresh unit configuration differs')
                source = resources.enter_context(PREP.Source(units.fd, name, 0o444, PREP.UNIT_LIMIT, units.check))
                require(source.data == data, 'prepared unit bytes differ')
                sources.append(source)
            receipt['stage'] = 'loaded-unit-contract'
            verify_loaded(texts, configs, memory)
            PREP.MAINTENANCE.consumer_check()
            attestation = PREP.MANUAL.Attestation(admission)
            try:
                attestation.recheck()
            finally:
                attestation.close()
            require(PREP._registry(controller, physical) == registry, 'registry changed during dispatch')
            require(PREP._configs(controller, registry['guests'], physical) == configs,
                    'enabled configurations changed during dispatch')
            prepared_source.check()
            for source in sources:
                source.check()
            physical()
            intent = dict(schema=1, profile=PROFILE, boot_id=boot, policy_sha256=pin,
                          registry_sha256=digest(PREP.REGISTRY.canonical(registry)),
                          selected=selected, units={name: digest(data) for name, data in texts.items()})
            data = encoded(intent)
            intent_record(data)
            receipt['stage'] = 'publish-intent'
            # The gate serializes competing dispatchers' check/create boundary.
            # Publishing an existing identical record is never queue permission.
            with admission._gate():
                live()
                require(admission._read()['state'] == 'open' and admission.manager_state() in
                        ('starting', 'running', 'degraded'), 'PID1/admission stopping')
                previous = existing(directory, prepared, boot, pin)
                if previous is not None:
                    receipt.update(noop=True, selected=previous['selected'], queue_uncertain=True)
                else:
                    try:
                        PREP.publish(directory, RECORD, data, 0o400, PREP.RECORD_LIMIT)
                    except BaseException as error:
                        detail = getattr(error, 'receipt', {})
                        receipt.update(intent_published=bool(detail.get('published')),
                                       intent_uncertain=bool(detail.get('uncertain')))
                        raise
                receipt['intent_published'] = True
            # Read-only source/physical/policy FDs must close before queueing.
            receipt['stage'] = 'prequeue-cleanup'
            try:
                resources.close()
            except BaseException as error:
                receipt['cleanup_errors'].append(dict(resource='readonly-resources', error=str(error)[:512]))
                raise
            policy = None
            live()
            if not receipt['noop'] and selected:
                with admission._gate():
                    live()
                    require(admission._read()['state'] == 'open' and admission.manager_state() in
                            ('starting', 'running', 'degraded'), 'PID1/admission stopping')
                    receipt.update(stage='queue', queue_attempted=True, queue_uncertain=True)
                    command(['/usr/bin/systemctl', '--no-block', 'start', '--'] +
                            ['a733-managed-' + name + '.service' for name in selected], limit=1024)
                    receipt.update(queue_acknowledged=True, queue_uncertain=False)
            receipt.update(success=True, stage='complete')
    except BaseException as error:
        receipt.update(error=type(error).__name__ + ': ' + str(error)[:1024],
                       failure=PREP.failure_detail(error))
    finally:
        for name, item in (('readonly-resources', resources), ('lease', lease), ('admission', admission)):
            if item is not None:
                try:
                    item.close()
                except BaseException as error:
                    receipt['cleanup_errors'].append(dict(resource=name, error=str(error)[:512]))
        if receipt['cleanup_errors']:
            receipt.update(success=False, stage='cleanup')
    require(len(encoded(receipt)) <= PREP.RECORD_LIMIT, 'dispatch receipt bound exceeded')
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    receipt = dispatch()
    print(encoded(receipt).decode('ascii'), end='')
    return 0 if receipt['success'] else 1


if __name__ == '__main__':
    sys.exit(main())
