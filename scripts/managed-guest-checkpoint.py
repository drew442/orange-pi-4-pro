#!/usr/bin/python3
"""Root managed publication and protected-cohort recovery; no CLI or hardware claim.

Construct live objects through FIXTURE.SUPERVISOR. Durable records authenticate
one prepared transition; parsing a record never creates live shutdown authority.
"""
import base64
import copy
from contextlib import ExitStack
import json
import os
import re
import stat
import threading
from pathlib import Path
import importlib.util

BASE = Path(__file__).resolve().parents[1]


def module(name, relative):
    spec = importlib.util.spec_from_file_location(name, BASE / relative)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


FIXTURE = module('checkpoint_managed_fixture', 'scripts/managed-guest-fixture.py')
SUPERVISOR = FIXTURE.SUPERVISOR
LIFECYCLE = SUPERVISOR.LIFECYCLE
POLICY = LIFECYCLE.POLICY
RECORDS = module('checkpoint_managed_records', 'configs/guest_checkpoint_records.py')
TRANSACTION = module('checkpoint_managed_transaction', 'scripts/guest-checkpoint-transaction.py')
HEAD, STORE, REGISTRY = LIFECYCLE.HEAD, LIFECYCLE.STORE, LIFECYCLE.REGISTRY
MANIFEST, BACKUP, ENROLL = POLICY.MANIFEST, POLICY.BACKUP, POLICY.ENROLL
require, digest = POLICY.require, LIFECYCLE.digest
_TOKEN = object()


def reference(raw, *, embedded=False):
    result = dict(bytes=len(raw), sha256=digest(raw))
    if embedded:
        result['data_base64'] = base64.b64encode(raw).decode('ascii')
    return result


def decoded(ref):
    # Call only after the enclosing closed-schema validator has bounded it.
    return base64.b64decode(ref['data_base64'], validate=True)


def common(record):
    return {key: copy.deepcopy(record[key]) for key in
            ('storage', 'name', 'uid', 'gid', 'run_id', 'boot_id')}


def result_for(operation, name):
    result = LIFECYCLE.receipt(operation, name)
    result.update(supervisor_cleanup_required=False, filesystem_cleanliness=False,
                  outcome_sha256=None, intent_sha256=None, completion_sha256=None)
    return result


def exact(store, name, data, limit, result):
    """Publish once or synchronize the same immutable bytes; never repair."""
    require(type(data) is bytes and len(data) <= limit, 'record limit exceeded')
    try:
        existing = store.read(name, limit)
    except FileNotFoundError as error:
        if error.filename != name:
            raise
        existing = None
    if existing is None:
        result['publications'].append(store.publish(name, data))
    else:
        require(existing == data, 'existing immutable record differs')
        result['synchronizations'].append(store.sync(name, pin=digest(data), limit=limit))


def _entry(registry, record, allowed_pins):
    matches = [item for item in registry['guests'] if item['name'] == record['name']]
    require(registry['storage'] == record['storage'] and len(matches) == 1,
            'registry storage/guest differs')
    entry = matches[0]
    require(all(entry[k] == record[k] for k in ('name', 'uid', 'gid')) and
            entry['manifest_sha256'] in allowed_pins and
            entry['shutdown_profile'] == record['shutdown_profile'] and
            entry['fixture_profile'] == record['fixture_profile'], 'registry entry conflicts')
    return entry


def _edge(store, previous, new, result):
    old_raw, new_raw = HEAD.canonical(previous), HEAD.canonical(new)
    name = digest(old_raw) + '-' + digest(new_raw) + '.json'
    data = LIFECYCLE.head_transition(old_raw, new_raw)
    with store.child('transitions') as transitions:
        require(transitions.read(name, LIFECYCLE.TRANSITION_LIMIT) == data,
                'legal immutable head edge missing/differs')
        result['synchronizations'].append(transitions.sync(
            name, pin=digest(data), limit=LIFECYCLE.TRANSITION_LIMIT))


def _heads(record, intent_raw, intent):
    previous = record['previous_head']
    admitted = HEAD.admit(previous, record['manifest_sha256'], record['run_id'],
                          record['boot_id'], expected_head_sha256=record['previous_head_sha256'],
                          automatic=record['automatic'])
    running = HEAD.mark_running(admitted, expected_head_sha256=digest(HEAD.canonical(admitted)))
    stopping = HEAD.mark_stopping(running, expected_head_sha256=digest(HEAD.canonical(running)))
    require(HEAD.canonical(stopping) == decoded(intent['stopping_head']),
            'stopping head does not replay legal admission/run edges')
    checkpoint = HEAD.mark_checkpoint(stopping, digest(intent_raw),
                                     expected_head_sha256=digest(HEAD.canonical(stopping)))
    ready = HEAD.complete(checkpoint, digest(intent_raw), intent['new_manifest']['sha256'],
                          expected_head_sha256=digest(HEAD.canonical(checkpoint)))
    return previous, admitted, running, stopping, checkpoint, ready


class CheckpointCustody:
    """Private factory authority; never closes borrowed supervisor/run/lease."""
    def __init__(self, token):
        require(token is _TOKEN, 'checkpoint custody factory required')
        self._pid, self._thread = os.getpid(), threading.get_ident()
        self._stack = ExitStack()
        self._poisoned = self._closed = False
        self._records = {}
        self._expected = None
        self.publication = self.inspection = None

    def physical(self):
        require(os.getpid() == self._pid and threading.get_ident() == self._thread and
                not self._closed and not self._poisoned, 'checkpoint custody unavailable')
        require(type(self.controller.context) is LIFECYCLE.CONTEXT.HostContext and
                type(self.lock) is LIFECYCLE.ADMISSION.GuestLock and
                type(self.controller.lease) is LIFECYCLE.ADMISSION.Lease,
                'actual host context/lease/guest lock required')
        self.controller.context.guard()
        self.controller.lease.recheck()
        self.lock.recheck()
        require(self.controller.admission.is_managed(self.name, self.controller.lease, self.lock),
                'managed marker missing')

    def _open(self):
        self.physical()
        self.store = self.controller._head_store(self._stack, self.name, self.physical)
        self.runs = self._stack.enter_context(self.store.child('runs'))
        # Independent runs readers retain physical-only guards. Publications
        # authenticate the immutable records without recursing into themselves.
        self.store.guard = self.records

    def retain(self, suffix, raw, limit):
        name = self.record['run_id'] + suffix
        exact(self.runs, name, raw, limit, self.result)
        self._records[name] = (raw, limit)

    def records(self):
        # Independent readers use physical guards only: never reenter this guard.
        self.physical()
        for name, (raw, limit) in self._records.items():
            require(self.runs.read(name, limit, pin=digest(raw)) == raw,
                    'retained checkpoint record changed')
    def recheck(self):
        self.records()
        if self._expected is not None:
            require(self.store.read('head.json', HEAD.LIMIT, pin=digest(self._expected)) ==
                    self._expected, 'checkpoint head differs')

    @classmethod
    def from_outcome(cls, outcome, fixture=None):
        require(type(outcome) is SUPERVISOR.StoppedRun, 'actual same-family StoppedRun required')
        outcome.recheck()  # Original PID/thread, retained objects and stopping head.
        run = outcome.run
        require(type(run) is LIFECYCLE.ManagedRun, 'actual same-family ManagedRun required')
        record = LIFECYCLE.validate_run(LIFECYCLE.canonical_run(run._record))
        profile = record['fixture_profile']
        require(record['shutdown_profile'] == REGISTRY.SHUTDOWN_PROFILE and
                profile in ('none', FIXTURE.PROFILE), 'unsupported managed shutdown/fixture profile')
        if profile == 'none':
            require(fixture is None, 'none profile forbids fixture authority')
        else:
            require(type(fixture) is FIXTURE.Fixture and
                    fixture is getattr(outcome._supervisor, '_managed_fixture', None) and
                    fixture.supervisor is outcome._supervisor, 'actual attached fixture required')
        require(not getattr(outcome, '_checkpoint_consumed', False), 'stopped outcome already consumed')
        self = cls(_TOKEN)
        self.controller, self.lock, self.name = run._controller, run.guest_lock, record['name']
        self.record = record
        self.result = result_for('managed-checkpoint', self.name)
        self.result['supervisor_cleanup_required'] = True
        try:
            self._open()
            admission = self.runs.read(record['run_id'] + '.json', HEAD.LIMIT,
                                       pin=run.admission_sha256)
            require(LIFECYCLE.validate_run(admission) == record, 'admission differs')
            self._records[record['run_id'] + '.json'] = (admission, HEAD.LIMIT)
            self._expected = HEAD.canonical(run.recheck())
            self.inspection = self._stack.enter_context(POLICY.Inspection(
                self.name, record['manifest_sha256']))
            require(self.inspection.manifest == run.manifest and
                    self.inspection.manifest_record['sha256'] == record['manifest_sha256'] and
                    POLICY.semantic(self.inspection.context) == POLICY.semantic(
                        POLICY.STORAGE.verify_guest_directory(self.controller.context.guest_fd)),
                    'stopped inspection does not bind run storage/manifest')
            proof = fixture.attest_stopped(outcome, self.inspection) if fixture else None
            shutdown = outcome.recheck()
            console, permit = outcome._console_data, outcome._permit_data
            require(reference(console) == dict(bytes=shutdown['console_bytes'],
                    sha256=shutdown['console_sha256']), 'console evidence differs')
            if proof is not None:
                parsed = _proof(proof)
                require(parsed['outcome'] == shutdown, 'fixture shutdown evidence differs')
            envelope = dict(schema=1, profile=RECORDS.OUTCOME_PROFILE, **common(record),
                            admission_sha256=digest(admission), shutdown_profile=record['shutdown_profile'],
                            fixture_profile=profile, permit=reference(permit, embedded=True),
                            shutdown=shutdown, shutdown_sha256=digest(RECORDS._encode(shutdown)),
                            console=reference(console), fixture=reference(proof) if proof else None)
            raw = RECORDS.canonical_outcome(envelope)
            # All authority consumed while the original run and stopping head remain valid.
            outcome.recheck()
            outcome._checkpoint_consumed = True
            self.retain('.console', console, RECORDS.CONSOLE_LIMIT)
            if proof is not None:
                self.retain('.fixture.json', proof, RECORDS.FIXTURE_LIMIT)
            self.retain('.outcome.json', raw, RECORDS.OUTCOME_LIMIT)
            self.outcome_raw, self.outcome = raw, envelope
            self.result['outcome_sha256'] = digest(raw)
            self.recheck()
            return self
        except BaseException as error:
            self._poisoned = True
            try:
                self.close()
            except BaseException as close_error:
                self.result['close_error'] = repr(close_error)
            raise LIFECYCLE.OperationError(self.result, error) from error

    def transition(self, new):
        self.recheck()
        old_raw, new_raw = self._expected, HEAD.canonical(new)
        with LIFECYCLE.publish_head_transition(self.store, old_raw, new_raw, self.result):
            self.recheck()
            self.result['publications'].append(self.store.publish(
                'head.json', new_raw, expected_sha256=digest(old_raw)))
            # Store guards are physical-only and permit the exact CAS to finish.
            require(self.store.read('head.json', HEAD.LIMIT) == new_raw,
                    'new checkpoint head acknowledgement differs')
        self._expected = new_raw
        self.recheck()

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self._stack.close()
        except BaseException as error:
            # A second idempotent close cannot reconstruct the first failure.
            # Retain uncertainty at the resource owner before propagating it.
            if hasattr(self, 'result'):
                self.result.setdefault('close_error', repr(error))
            raise


def _profile_sha256():
    with ExitStack() as stack:
        held = ENROLL.HeldPath(str(FIXTURE.LOCK_PATH))
        stack.callback(held.close)
        ENROLL.regular(held.info)
        require(not held.info.st_mode & 0o222 and 0 < held.info.st_size <= 4096,
                'unsafe fixture catalogue')
        raw = os.pread(held.fd, 4097, 0)
        FIXTURE.validate_lock(raw)
        held.recheck()
        return digest(raw)


def _proof(raw):
    require(type(raw) is bytes and 0 < len(raw) <= RECORDS.FIXTURE_LIMIT,
            'fixture proof size differs')
    value = RECORDS._parse(raw, RECORDS.FIXTURE_LIMIT)
    fields = {'schema', 'profile', 'fixture_profile', 'storage', 'name', 'run_id', 'boot_id',
              'admission_sha256', 'old_manifest_sha256', 'profile_sha256', 'previous_counter',
              'counter', 'hardware_acceptance', 'checkpoint_authorized', 'live', 'http_probe',
              'http_confirmation', 'http_post_confirmation', 'permit_sha256', 'outcome',
              'stopped_disk', 'console_bytes', 'console_sha256', 'kernel', 'initrd'}
    require(type(value) is dict and set(value) == fields and
            type(value['schema']) is int and value['schema'] == 1 and FIXTURE.canonical(value) == raw and
            value.get('profile') == 'a733-managed-fixture-acceptance-v1' and
            value.get('fixture_profile') == FIXTURE.PROFILE and
            value.get('hardware_acceptance') is False and
            value.get('checkpoint_authorized') is False, 'fixture proof differs')
    return value


def _fixture_evidence(proof, old, console):
    config = old['config']
    require(config['kernel'] == FIXTURE.KERNEL and config['initrd'] == FIXTURE.INITRD and
            config['disk']['bytes'] == FIXTURE.LOCK['disk_bytes'], 'fixture reviewed inputs differ')
    previous, counter = proof['previous_counter'], proof['counter']
    expected = [('PASS cpu', 'PASS cpu sha256=' + FIXTURE.CPU),
                ('PASS disk', f'PASS disk previous={previous} counter={counter}'),
                ('READY network', f'READY network tcp=8080 counter={counter}; host must verify and confirm HTTP response'),
                ('PASS network', f'PASS network loopback-http counter={counter}'),
                ('PASS fixture', f'PASS fixture counter={counter}; READY managed-power-key-v1'),
                ('PASS managed-power-key-sync', 'PASS managed-power-key-sync')]
    lines = [line.removesuffix(b'\r') for line in console.split(b'\n')[:-1]]
    require(not any(marker in console for marker in (b'FAIL', b'Kernel panic', b'not syncing')),
            'fixture console failure')
    positions = []
    for prefix, line in expected:
        matches = [index for index, actual in enumerate(lines) if actual.startswith(prefix.encode())]
        require(len(matches) == 1 and lines[matches[0]] == line.encode(), 'fixture console proof differs')
        positions.extend(matches)
    require(positions == sorted(positions), 'fixture console proof order differs')
    body = f'a733-kvm-guest-network-v1 counter={counter}\n'.encode()
    for key in ('http_probe', 'http_confirmation', 'http_post_confirmation'):
        value = proof[key]
        require(type(value) is dict and set(value) == {'status', 'body_sha256', 'bytes'} and
                type(value['status']) is int and value['status'] == 200 and
                value['body_sha256'] == digest(body) and type(value['bytes']) is int and
                len(body) <= value['bytes'] <= 4096, 'fixture HTTP proof differs')
    live = proof['live']
    require(type(live) is dict and set(live) == {'kvm_fds', 'listen_inode', 'port'} and
            type(live['listen_inode']) is str and re.fullmatch('[1-9][0-9]{0,19}', live['listen_inode']) and
            type(live['port']) is int and live['port'] == config.get('network', {}).get('host_tcp_port') and
            type(live['kvm_fds']) is list and len(live['kvm_fds']) <= 4096,
            'fixture live descriptor/listener proof differs')
    targets, numbers = [], set()
    for item in live['kvm_fds']:
        require(type(item) is dict and set(item) == {'fd', 'target'} and
                type(item['fd']) is int and 0 <= item['fd'] <= 1048576 and
                item['fd'] not in numbers and type(item['target']) is str, 'fixture descriptor proof differs')
        numbers.add(item['fd'])
        targets.append(item['target'])
    require(targets.count('/dev/kvm') == targets.count('anon_inode:kvm-vm') == 1 and
            sum(target in ('anon_inode:kvm-arm-vgic-v2', 'anon_inode:kvm-arm-vgic-v3')
                for target in targets) >= 1 and
            sorted(target for target in targets if target.startswith('anon_inode:kvm-vcpu:')) ==
            sorted('anon_inode:kvm-vcpu:' + str(index) for index in range(config['cpus'])),
            'fixture VM/vCPU/VGIC proof differs')


def _prepare(custody):
    record, inspection = custody.record, custody.inspection
    old_raw = inspection.manifest_data
    old = inspection.manifest
    _history(custody, manifest_data=old_raw)
    disk = inspection.disk_record
    require(inspection._hash(inspection.disk_fd, disk['bytes']) == disk, 'stopped disk changed')
    changed = disk != old['config']['disk']
    provenance = dict(profile='a733-managed-shutdown-v1', run_id=record['run_id'],
                      boot_id=record['boot_id'], shutdown_profile=record['shutdown_profile'],
                      fixture_profile=record['fixture_profile'], evidence_sha256=digest(custody.outcome_raw))
    new = MANIFEST.proposed_managed_manifest(old, digest(old_raw), disk, provenance)
    new_raw = MANIFEST.canonical(new) if changed else old_raw
    history = None
    if changed:
        history = BACKUP.canonical(dict(schema=2, operation='a733-managed-guest-checkpoint-v2',
            old_manifest=old, new_manifest=new, old_manifest_sha256=digest(old_raw),
            new_manifest_sha256=digest(new_raw),
            old_manifest_base64=reference(old_raw, embedded=True)['data_base64'],
            new_manifest_base64=reference(new_raw, embedded=True)['data_base64']))
        BACKUP.validate_record(digest(old_raw) + '-' + digest(new_raw) + '.json', history)
    with ExitStack() as stack:
        lock = stack.enter_context(custody.controller.admission.registry_lock(custody.controller.lease))
        def guard():
            custody.recheck()
            lock.recheck()
        _, _, registry = custody.controller._registry(stack, guard)
        require(registry is not None, 'registry unavailable')
        _entry(registry, record, (digest(old_raw),))
        require(registry['generation'] >= record['registry_generation'], 'registry generation regressed')
        observed = registry['generation']
    intent = dict(schema=1, profile=RECORDS.INTENT_PROFILE, **common(record),
                  admission_sha256=custody.outcome['admission_sha256'],
                  stopping_head=reference(custody._expected, embedded=True),
                  outcome=reference(custody.outcome_raw), console=custody.outcome['console'],
                  fixture=custody.outcome['fixture'], old_manifest=reference(old_raw, embedded=True),
                  new_manifest=reference(new_raw, embedded=True), disk=disk,
                  requires_update=changed, history=reference(history, embedded=True) if history else None,
                  registry=dict(observed_generation=observed, old_manifest_sha256=digest(old_raw),
                                new_manifest_sha256=digest(new_raw)))
    raw = RECORDS.canonical_intent(intent)
    custody.retain('.intent.json', raw, RECORDS.INTENT_LIMIT)
    custody.intent, custody.intent_raw = intent, raw
    custody.result['intent_sha256'] = digest(raw)
    return _heads(record, raw, intent)


def _plan(inspection, intent):
    old_raw, new_raw = decoded(intent['old_manifest']), decoded(intent['new_manifest'])
    return dict(name=inspection.name, context=inspection.context, disk=intent['disk'],
                old_manifest={**reference(old_raw), 'value': MANIFEST.validate_manifest(old_raw)},
                proposed_manifest={**reference(new_raw), 'value': MANIFEST.validate_manifest(new_raw)},
                requires_update=intent['requires_update'])


class _BeforePublication:
    """Concrete adapter for the shared transaction's inspection recheck callback."""
    def __init__(self, inspection, publication):
        self._inspection, self._publication = inspection, publication

    def __getattr__(self, name):
        return getattr(self._inspection, name)

    def recheck(self, *, owned_entries=None):
        self._publication.before_recheck(owned_entries=owned_entries)


def _publish_manifest(custody):
    intent, inspection = custody.intent, custody.inspection
    history = decoded(intent['history']) if intent['history'] else None
    publication = inspection.prepare_managed_publication(
        decoded(intent['new_manifest']), history, authorization=custody)
    custody._stack.callback(publication.close)
    custody.publication = publication
    tx = dict(success=False, published=False, durable=False, history_published=False,
              history_durable=False, disk_flush_attempted=False,
              metadata_may_be_modified=False, manifest_replace_attempted=False)
    custody.result['transaction'] = tx
    TRANSACTION.transaction(_BeforePublication(inspection, publication), _plan(inspection, intent),
                            custody.physical, custody, tx, enroll=ENROLL, manifest=MANIFEST,
                            require=require, canonical=BACKUP.canonical, history_data=history,
                            history_schema=2, validate_record=BACKUP.validate_record)
    require(tx['success'] and 'cleanup_error' not in tx, 'checkpoint transaction cleanup failed')
    publication.bind_published()
    publication.recheck()


def _history(custody, *, synchronize=True, manifest_data=None):
    intent = getattr(custody, 'intent', None)
    held = custody.publication._inspection if custody.publication is not None else custody.inspection
    entries = {}
    try:
        directory = os.open('.checkpoints', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=held.guest_fd)
    except FileNotFoundError as error:
        if error.filename != '.checkpoints':
            raise
    else:
        with ExitStack() as stack:
            stack.callback(os.close, directory)
            inode = ENROLL.inode(os.fstat(directory))
            def guard():
                custody.physical()
                TRANSACTION.directory_check(held.guest_fd, directory, inode,
                                            enroll=ENROLL, require=require)
            guard()
            names = os.listdir(directory)
            require(len(names) <= BACKUP.ENTRY_COUNT, 'checkpoint history entry limit exceeded')
            for name in names:
                # Validate the single component before open; history names must
                # also pass BACKUP's exact digest-pair grammar below.
                STORE._name(name)
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                with ExitStack() as record_stack:
                    record_stack.callback(os.close, fd)
                    info = os.fstat(fd)
                    ENROLL.regular(info, 0o400)
                    require(info.st_dev == os.fstat(directory).st_dev and
                            0 < info.st_size <= BACKUP.ENTRY_LIMIT, 'unsafe checkpoint history size/device')
                    raw = os.pread(fd, info.st_size + 1, 0)
                    BACKUP.validate_record(name, raw)
                    require(len(raw) == info.st_size, 'history read size differs')
                    if synchronize:
                        os.fsync(fd)
                        os.fsync(directory)
                    guard()
                    require(ENROLL.identity(info) == ENROLL.identity(os.fstat(fd)) ==
                            ENROLL.identity(os.stat(name, dir_fd=directory, follow_symlinks=False)) and
                            os.pread(fd, info.st_size + 1, 0) == raw, 'checkpoint history changed')
                    entries[name] = raw
                    custody.result['synchronizations'].append(dict(
                        operation='history-sync', name=name, sha256=digest(raw), durable=synchronize))
    BACKUP.validate_entries(entries, manifest_data if manifest_data is not None else
                            decoded(intent['new_manifest']))
    if manifest_data is None and intent['history'] is not None:
        name = intent['old_manifest']['sha256'] + '-' + intent['new_manifest']['sha256'] + '.json'
        require(entries.get(name) == decoded(intent['history']), 'published history differs')


def _disk(custody):
    inspection = custody.inspection
    if custody.publication is not None:
        custody.publication.recheck()
        held = custody.publication._inspection
    else:
        inspection.recheck()
        held = inspection
    os.fsync(held.disk_fd)
    os.fsync(held.manifest_fd)
    os.fsync(held.guest_fd)
    custody.physical()
    require(held._hash(held.disk_fd, custody.intent['disk']['bytes']) == custody.intent['disk'],
            'disk differs from recorded stopped intent')
    require(held.manifest_data == decoded(custody.intent['new_manifest']),
            'published manifest differs from recorded intent')
    _history(custody)


def _completion(custody, registry, registry_raw, ready_raw):
    intent, record = custody.intent, custody.record
    value = dict(schema=1, profile=RECORDS.COMPLETION_PROFILE, **common(record),
                 intent_sha256=digest(custody.intent_raw), outcome_sha256=intent['outcome']['sha256'],
                 ready_head=reference(ready_raw, embedded=True),
                 manifest_sha256=intent['new_manifest']['sha256'],
                 registry_generation=registry['generation'], registry_sha256=digest(registry_raw))
    return RECORDS.canonical_completion(value)


def _check_completion(raw, custody, registry, registry_raw):
    value = RECORDS.validate_completion(raw)
    require(RECORDS.canonical_completion(value) == raw and common(value) == common(custody.record) and
            value['intent_sha256'] == digest(custody.intent_raw) and
            value['outcome_sha256'] == digest(custody.outcome_raw) and
            decoded(value['ready_head']) == custody._expected and
            value['manifest_sha256'] == custody.intent['new_manifest']['sha256'] and
            custody.intent['registry']['observed_generation'] <= value['registry_generation'] <= registry['generation'],
            'historical completion cohort differs')
    if value['registry_generation'] == registry['generation']:
        require(value['registry_sha256'] == digest(registry_raw), 'same-generation registry differs')
    return value


def _finish_registry(custody, *, require_existing=False):
    custody.recheck()
    _disk(custody)
    intent = custody.intent
    name = custody.record['run_id'] + '.completed.json'
    with ExitStack() as stack:
        lock = stack.enter_context(custody.controller.admission.registry_lock(custody.controller.lease))
        def guard():
            custody.recheck()
            lock.recheck()
        store, raw, registry = custody.controller._registry(stack, guard)
        require(registry is not None, 'registry unavailable')
        try:
            completed = custody.runs.read(name, RECORDS.COMPLETION_LIMIT)
        except FileNotFoundError as error:
            if error.filename != name:
                raise
            completed = None
        if completed is not None:
            _check_completion(completed, custody, registry, raw)
        oldpin, newpin = intent['old_manifest']['sha256'], intent['new_manifest']['sha256']
        _entry(registry, custody.record, (newpin,) if require_existing else (oldpin, newpin))
        require(registry['generation'] >= intent['registry']['observed_generation'], 'registry generation regressed')
        merged = HEAD.merge_registry(registry, custody.name, oldpin, newpin)
        new_raw = REGISTRY.canonical(merged)
        if merged != registry:
            require(not require_existing, 'ready registry requires reconciliation')
            custody.result['publications'].append(store.publish('autostart.json', new_raw,
                                                               expected_sha256=digest(raw)))
            raw, registry = new_raw, merged
        else:
            custody.result['synchronizations'].append(store.sync('autostart.json', pin=digest(raw), limit=REGISTRY.LIMIT))
        if completed is None:
            require(not require_existing, 'completed ready cohort acknowledgement missing')
            completed = _completion(custody, registry, raw, custody._expected)
        _check_completion(completed, custody, registry, raw)
        exact(custody.runs, name, completed, RECORDS.COMPLETION_LIMIT, custody.result)
        custody.result.update(completion_sha256=digest(completed), registry_sha256=digest(raw),
                              head_sha256=digest(custody._expected), manifest_sha256=newpin)


def checkpoint(actualStoppedRun, fixture=None):
    """Consume an actual retained stop and publish one complete protected cohort."""
    custody = None
    result = result_for('managed-checkpoint', None)
    try:
        custody = CheckpointCustody.from_outcome(actualStoppedRun, fixture)
        result = custody.result
        result['stage'] = 'intent-preparation'
        heads = _prepare(custody)
        result['stage'] = 'checkpoint-head'
        custody.transition(heads[-2])
        result['stage'] = 'manifest-publication'
        _publish_manifest(custody)
        _disk(custody)
        result['stage'] = 'ready-head'
        custody.transition(heads[-1])
        result['stage'] = 'registry-completion'
        _finish_registry(custody)
        custody.recheck()
        result['stage'] = 'close'
        custody.close()
        # The borrowed Supervisor must be closed by its outer owner. Its close()
        # can return normally while recording errors: explicitly expose the duty.
        require(not actualStoppedRun._supervisor.receipt['errors'], 'supervisor cleanup errors')
        result.update(stage='complete', success=True)
        return copy.deepcopy(result)
    except BaseException as error:
        if custody is None and isinstance(error, LIFECYCLE.OperationError):
            raise
        if custody is not None:
            custody._poisoned = True
            try:
                custody.close()
            except BaseException as close_error:
                result['close_error'] = repr(close_error)
        # Uncertain publication retains records and current head for fresh recovery.
        raise LIFECYCLE.OperationError(result, error) from error


def _cohort_records(custody, head_raw):
    """Authenticate complete exact durable references and legal current-run edges."""
    head = HEAD.validate_head(head_raw)
    require(HEAD.canonical(head) == head_raw and head['status'] != 'quarantined',
            'canonical nonquarantined head required')
    run_id = head['run_id']
    require(run_id is not None, 'initial enrollment has no checkpoint cohort')
    admission = custody.runs.read(run_id + '.json', HEAD.LIMIT)
    record = LIFECYCLE.validate_run(admission)
    require(LIFECYCLE.canonical_run(record) == admission and record['run_id'] == run_id and
            record['storage'] == custody.controller.context.storage and
            record['name'] == custody.name and all(record[k] == head[k] for k in ('uid', 'gid')) and
            record['boot_id'] == head['last_boot_id'], 'admission/current head identity differs')
    custody.record = record
    custody._records[run_id + '.json'] = (admission, HEAD.LIMIT)
    raw = custody.runs.read(run_id + '.intent.json', RECORDS.INTENT_LIMIT)
    intent = RECORDS.validate_intent(raw)
    require(RECORDS.canonical_intent(intent) == raw and common(intent) == common(record) and
            intent['admission_sha256'] == digest(admission) and
            intent['old_manifest']['sha256'] == record['manifest_sha256'] and
            intent['registry']['observed_generation'] >= record['registry_generation'],
            'intent/admission differs')
    custody.intent_raw, custody.intent = raw, intent
    custody._records[run_id + '.intent.json'] = (raw, RECORDS.INTENT_LIMIT)
    outcome_raw = custody.runs.read(run_id + '.outcome.json', RECORDS.OUTCOME_LIMIT,
                                   pin=intent['outcome']['sha256'])
    outcome = RECORDS.validate_outcome(outcome_raw)
    require(RECORDS.canonical_outcome(outcome) == outcome_raw and
            reference(outcome_raw) == intent['outcome'] and
            common(outcome) == common(record) and outcome['admission_sha256'] == digest(admission) and
            all(outcome[k] == record[k] for k in ('shutdown_profile', 'fixture_profile')) and
            outcome['console'] == intent['console'] and outcome['fixture'] == intent['fixture'] and
            outcome['shutdown']['stopping_head_sha256'] == intent['stopping_head']['sha256'],
            'outcome/admission/intent differs')
    custody.outcome_raw, custody.outcome = outcome_raw, outcome
    custody._records[run_id + '.outcome.json'] = (outcome_raw, RECORDS.OUTCOME_LIMIT)
    console = custody.runs.read(run_id + '.console', RECORDS.CONSOLE_LIMIT,
                               pin=intent['console']['sha256'])
    require(reference(console) == intent['console'], 'console reference differs')
    custody._records[run_id + '.console'] = (console, RECORDS.CONSOLE_LIMIT)
    old = MANIFEST.validate_manifest(decoded(intent['old_manifest']))
    permit = RECORDS.PERMIT.validate_permit(decoded(outcome['permit']))
    require(permit['config'] == old['config'] and permit['manifest_sha256'] == record['manifest_sha256'],
            'permit does not bind original admitted manifest/config')
    heads = _heads(record, raw, intent)
    require(permit['head_sha256'] == digest(HEAD.canonical(heads[1])), 'permit admitted head differs')
    if intent['fixture'] is not None:
        proof_raw = custody.runs.read(run_id + '.fixture.json', RECORDS.FIXTURE_LIMIT,
                                     pin=intent['fixture']['sha256'])
        proof = _proof(proof_raw)
        require(reference(proof_raw) == intent['fixture'] and
                all(proof.get(k) == record[k] for k in ('storage', 'name', 'run_id', 'boot_id')) and
                proof.get('admission_sha256') == digest(admission) and
                proof.get('old_manifest_sha256') == record['manifest_sha256'] and
                proof.get('permit_sha256') == outcome['permit']['sha256'] and
                proof.get('outcome') == outcome['shutdown'] and
                proof.get('stopped_disk') == intent['disk'] and
                dict(bytes=proof.get('console_bytes'), sha256=proof.get('console_sha256')) == intent['console'] and
                proof.get('kernel') == FIXTURE.KERNEL and proof.get('initrd') == FIXTURE.INITRD and
                proof.get('profile_sha256') == _profile_sha256() and
                type(proof.get('previous_counter')) is int and
                type(proof.get('counter')) is int and
                0 <= proof['previous_counter'] < 99999999 and
                proof['counter'] == proof['previous_counter'] + 1,
                'managed fixture proof cross-record binding differs')
        _fixture_evidence(proof, old, console)
        custody._records[run_id + '.fixture.json'] = (proof_raw, RECORDS.FIXTURE_LIMIT)
    statuses = {'stopping': 3, 'checkpoint': 4, 'ready': 5}
    require(head['status'] in statuses and head == heads[statuses[head['status']]],
            'current head is not the exact intended legal cohort phase')
    for index in range(1, statuses[head['status']] + 1):
        _edge(custody.store, heads[index - 1], heads[index], custody.result)
    custody._expected = head_raw
    custody.result.update(outcome_sha256=digest(outcome_raw), intent_sha256=digest(raw))
    custody.recheck()
    return heads


def _fresh(controller, name, *, guest_lock=None, inspection=None, head_data=None):
    require(type(controller) is LIFECYCLE.StateController, 'actual same-family controller required')
    custody = CheckpointCustody(_TOKEN)
    custody.name, custody.result = name, result_for('reconcile-managed-checkpoint', name)
    try:
        if guest_lock is None:
            # Re-authenticate placement with a fresh context, borrowing the lease.
            custody.controller = custody._stack.enter_context(LIFECYCLE.StateController(
                controller.context.storage, controller.lease, boot_id=controller.boot_id))
            custody.lock = custody._stack.enter_context(controller.admission.guest_lock(
                name, controller.lease, create=False))
        else:
            custody.controller, custody.lock = controller, guest_lock
        custody._open()
        observed = custody.store.read('head.json', HEAD.LIMIT)
        require(head_data is None or observed == head_data, 'borrowed ready head changed')
        custody._expected = observed
        if inspection is not None:
            require(type(inspection) is POLICY.Inspection, 'actual same-family Inspection required')
            custody.inspection = inspection  # Borrowed disk lock; never reopen it.
        return custody
    except BaseException as error:
        try:
            custody.close()
        except BaseException:
            # The caller never received custody; preserve its cleanup receipt
            # together with the original construction/authentication failure.
            raise LIFECYCLE.OperationError(custody.result, error) from error
        raise


def _manifest_bytes(custody):
    custody.recheck()
    with ExitStack() as stack:
        directory = os.open(custody.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=custody.controller.context.guest_fd)
        stack.callback(os.close, directory)
        info = os.fstat(directory)
        require(info.st_uid == 0 and info.st_gid == custody.record['gid'] and
                stat.S_IMODE(info.st_mode) == 0o710 and
                info.st_dev == os.fstat(custody.controller.context.guest_fd).st_dev,
                'unsafe guest manifest directory')
        fd = os.open('enrollment.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=directory)
        stack.callback(os.close, fd)
        before = os.fstat(fd)
        ENROLL.regular(before, 0o400)
        require(before.st_dev == info.st_dev and 0 < before.st_size <= MANIFEST.LIMIT,
                'unsafe manifest size/device')
        raw = os.pread(fd, before.st_size + 1, 0)
        require(len(raw) == before.st_size and
                ENROLL.identity(before) == ENROLL.identity(os.fstat(fd)) and
                ENROLL.identity(before) == ENROLL.identity(os.stat(
                    'enrollment.json', dir_fd=directory, follow_symlinks=False)) and
                ENROLL.inode(info) == ENROLL.inode(os.stat(custody.name,
                    dir_fd=custody.controller.context.guest_fd, follow_symlinks=False)),
                'manifest replaced/changed')
        custody.recheck()
        return raw


def _open_intended_disk(custody):
    raw = _manifest_bytes(custody)
    intent = custody.intent
    require(raw in (decoded(intent['old_manifest']), decoded(intent['new_manifest'])),
            'manifest conflicts with protected intent')
    custody.inspection = custody._stack.enter_context(POLICY.Inspection(custody.name, digest(raw)))
    require(custody.inspection.manifest_data == raw and
            POLICY.semantic(custody.inspection.context) == POLICY.semantic(
                POLICY.STORAGE.verify_guest_directory(custody.controller.context.guest_fd)) and
            custody.inspection.disk_record == intent['disk'], 'stopped disk/placement conflicts')
    return raw == decoded(intent['new_manifest'])


def _quarantine(custody, code):
    # Only freshly authenticated durable conflicts use quarantine. A publication
    # acknowledgement failure preserves its possibly valid checkpoint/ready head.
    custody.recheck()
    head = HEAD.validate_head(custody._expected)
    require(head['status'] != 'quarantined', 'quarantine cannot be cleared')
    failure = dict(schema=1, profile='a733-managed-checkpoint-failure-v1',
                   storage=custody.controller.context.storage, name=custody.name,
                   run_id=head['run_id'], head_sha256=digest(custody._expected),
                   head=head, code=code)
    raw = RECORDS._encode(failure)
    name = head['run_id'] + '.checkpoint-failure.json'
    exact(custody.runs, name, raw, HEAD.LIMIT, custody.result)
    new = HEAD.quarantine(head, digest(raw), expected_head_sha256=digest(custody._expected))
    custody.transition(new)
    custody.result['quarantined'] = True


def reconcile(controller, name):
    """Fresh protected-cohort recovery; no inferred clean shutdown or force path."""
    custody = None
    result = result_for('reconcile-managed-checkpoint', name)
    try:
        custody = _fresh(controller, name)
        result = custody.result
        head = HEAD.validate_head(custody._expected)
        require(head['status'] != 'quarantined', 'quarantine requires separately reviewed recovery')
        # From here a malformed/missing cohort is an authenticated durable conflict.
        try:
            heads = _cohort_records(custody, custody._expected)
            new_present = _open_intended_disk(custody)
            if new_present:
                _disk(custody)
            else:
                require(head['status'] in ('stopping', 'checkpoint'),
                        'ready head has old manifest')
        except (ValueError, FileNotFoundError) as error:
            # Corrupt retained records cannot be used by recheck to authorize
            # quarantine. Authenticate the exact current head physically afresh.
            custody._records = {}
            custody.recheck()
            _quarantine(custody, 'cohort-conflict')
            raise error
        if head['status'] == 'stopping':
            custody.transition(heads[-2])
        if not new_present:
            _publish_manifest(custody)
        _disk(custody)
        current = HEAD.validate_head(custody._expected)
        if current['status'] == 'checkpoint':
            custody.transition(heads[-1])
        elif current['status'] == 'ready':
            result['synchronizations'].append(custody.store.sync(
                'head.json', pin=digest(custody._expected), limit=HEAD.LIMIT))
        try:
            _finish_registry(custody)
        except (ValueError, FileNotFoundError):
            _quarantine(custody, 'registry-completion-conflict')
            raise
        custody.recheck()
        custody.close()
        result.update(stage='complete', success=True)
        return copy.deepcopy(result)
    except BaseException as error:
        if custody is None and isinstance(error, LIFECYCLE.OperationError):
            raise
        if custody is not None:
            custody._poisoned = True
            try:
                custody.close()
            except BaseException as close_error:
                result['close_error'] = repr(close_error)
        raise LIFECYCLE.OperationError(result, error) from error


def authenticate_ready_cohort(controller, name, *, guest_lock=None, inspection=None,
                              head_store=None, head_data=None, registry=None):
    """Verify and sync current completed cohort before enable/admit/boot preparation.

    Borrow existing guest/disk/registry locks during admission. Public standalone
    calls acquire fresh context/guest/disk authority in the usual lock order.
    """
    custody = None
    result = result_for('authenticate-ready-cohort', name)
    try:
        custody = _fresh(controller, name, guest_lock=guest_lock, inspection=inspection,
                         head_data=head_data)
        result = custody.result
        head = HEAD.validate_head(custody._expected)
        require(head['status'] == 'ready' and head['sequence'] > 0,
                'completed ready head required')
        _cohort_records(custody, custody._expected)
        if custody.inspection is None:
            require(_open_intended_disk(custody), 'ready manifest differs')
        else:
            require(custody.inspection.manifest_data == decoded(custody.intent['new_manifest']) and
                    custody.inspection.disk_record == custody.intent['disk'], 'borrowed ready disk differs')
        _disk(custody)
        result['synchronizations'].append(custody.store.sync(
            'head.json', pin=digest(custody._expected), limit=HEAD.LIMIT))
        if registry is None:
            _finish_registry(custody, require_existing=True)
        else:
            registry_store, registry_raw, current_registry = registry
            result['synchronizations'].append(registry_store.sync(
                'autostart.json', pin=digest(registry_raw), limit=REGISTRY.LIMIT))
            _entry(current_registry, custody.record, (custody.intent['new_manifest']['sha256'],))
            name_completed = custody.record['run_id'] + '.completed.json'
            raw = custody.runs.read(name_completed, RECORDS.COMPLETION_LIMIT)
            _check_completion(raw, custody, current_registry, registry_raw)
            exact(custody.runs, name_completed, raw, RECORDS.COMPLETION_LIMIT, result)
        custody.recheck()
        custody.close()
        result.update(stage='complete', success=True)
        return result
    except BaseException as error:
        if custody is None and isinstance(error, LIFECYCLE.OperationError):
            raise
        if custody is not None:
            custody._poisoned = True
            try:
                custody.close()
            except BaseException as close_error:
                result['close_error'] = repr(close_error)
        raise LIFECYCLE.OperationError(result, error) from error


LIFECYCLE._READY_VERIFIER = authenticate_ready_cohort
