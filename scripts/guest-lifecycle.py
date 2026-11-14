#!/usr/bin/python3
"""Bounded opt-in registry/head I/O; no VM supervision or checkpoint authority.

Only explicit enrollment creates persistent directories and the initial head.
Failures retain partial publications and RAM exclusion; recovery is explicit.
This API has no CLI, launch, checkpoint completion, rollback or hardware acceptance.
"""
import base64
import copy
from contextlib import ExitStack, contextmanager
import hashlib
import importlib.util
import json
import re
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]


def module(name, relative):
    spec = importlib.util.spec_from_file_location(name, BASE / relative)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


CONTEXT = module('lifecycle_controller_context', 'scripts/guest-lifecycle-context.py')
ADMISSION = CONTEXT.ADMISSION
POLICY = CONTEXT.POLICY
REGISTRY = CONTEXT.REGISTRY
HEAD = module('lifecycle_controller_head', 'configs/guest_lifecycle.py')
STORE = module('lifecycle_controller_store', 'scripts/guest-lifecycle-store.py')
require = POLICY.require
# Only the managed checkpoint module installs its verifier in its own family.
_READY_VERIFIER = None


def _verify_ready(controller, name, *, guest_lock, inspection, head_store, head_data, registry=None):
    head = HEAD.validate_head(head_data)
    require(head['status'] == 'ready', 'ready trusted head required')
    if head['sequence'] == 0:
        return  # Explicit initial enrollment is the sole cohort exemption.
    require(callable(_READY_VERIFIER), 'completed ready cohort verifier unavailable')
    return _READY_VERIFIER(controller, name, guest_lock=guest_lock, inspection=inspection,
                           head_store=head_store, head_data=head_data, registry=registry)


def digest(data):
    return hashlib.sha256(data).hexdigest()


class OperationError(RuntimeError):
    """Conservative operation receipt, including nested publication uncertainty."""
    def __init__(self, receipt, cause):
        self.receipt = copy.deepcopy(receipt)
        self.cause = cause
        if hasattr(cause, 'receipt'):
            self.receipt['publication_error'] = copy.deepcopy(cause.receipt)
        elif isinstance(cause, ADMISSION.PublicationError):
            self.receipt['marker_error'] = dict(stage=cause.stage,
                                                uncertain=cause.uncertain)
        super().__init__(f"{receipt['stage']}: {type(cause).__name__}: {cause}")


def admission_failure(error):
    detail = dict(type=type(error).__name__, error=str(error)[:1024])
    if hasattr(error, 'receipt'):
        detail['receipt'] = copy.deepcopy(error.receipt)
    return detail


class AdmissionCleanupError(RuntimeError):
    def __init__(self, primary, cleanup):
        self.receipt = dict(primary_error=admission_failure(primary),
                            cleanup_error=admission_failure(cleanup))
        super().__init__('run admission cleanup failed after primary failure')


class AdmissionResources(ExitStack):
    """Keep policy consumption/publication evidence when lock cleanup fails."""
    def __exit__(self, error_type, error, traceback):
        try:
            return super().__exit__(error_type, error, traceback)
        except BaseException as cleanup:
            if error is None or cleanup is error:
                raise
            raise AdmissionCleanupError(error, cleanup) from error


def receipt(operation, name):
    return dict(operation=operation, name=name, stage='validate', success=False,
                hardware_acceptance=False, managed_prepare_attempted=False, marker_attempted=False,
                marker_completed=False, directory_attempted=False,
                head_attempted=False, registry_attempted=False,
                head_sync_attempted=False, registry_sync_attempted=False,
                head_sha256=None, registry_sha256=None, registry_previous_sha256=None,
                manifest_sha256=None, publications=[], synchronizations=[])


class StateController:
    """Own a read-only live HostContext; borrow the caller's admission lease."""
    def __init__(self, expected_storage, lease, *, boot_id):
        self.context = CONTEXT.HostContext(expected_storage, lease, boot_id=boot_id)
        self.lease = lease
        self.admission = lease.admission
        self.boot_id = boot_id

    def __enter__(self):
        self.context.__enter__()
        return self

    def close(self):
        self.context.close()

    def __exit__(self, *args):
        self.context.__exit__(*args)

    def _storage(self, value):
        require(value['storage'] == self.context.storage, 'storage identity differs')

    def _registry(self, stack, guard):
        """Return exact bytes/pin and parsed record, without creating anything."""
        try:
            store = stack.enter_context(STORE.RootDirectory(
                self.context.state_fd, 'guests', guard=guard))
        except FileNotFoundError as error:
            if error.filename != 'guests':
                raise
            guard()
            return None, None, None
        try:
            data = store.read('autostart.json', REGISTRY.LIMIT)
        except FileNotFoundError as error:
            if error.filename != 'autostart.json':
                raise
            store.check()
            return store, None, None
        value = REGISTRY.validate_registry(data)
        self._storage(value)
        return store, data, value

    def registry(self):
        """Observe validated registry metadata; a missing file is unconfigured."""
        with ExitStack() as stack:
            _, data, value = self._registry(stack, self.context.guard)
            return dict(configured=value is not None, registry=copy.deepcopy(value),
                        registry_sha256=digest(data) if data is not None else None,
                        hardware_acceptance=False)

    def _head_store(self, stack, name, guard, create=False, result=None):
        if create:
            result['directory_attempted'] = True
        root = stack.enter_context(STORE.RootDirectory(
            self.context.guest_fd, '.a733-lifecycle', guard=guard, create=create))
        if create:
            result['publications'].append(copy.deepcopy(root.creation_receipt))
        store = stack.enter_context(root.child(name, create=create))
        if create:
            result['publications'].append(copy.deepcopy(store.creation_receipt))
        return store

    def status(self):
        """Read metadata only; head observations confer no disk/run trust."""
        result = self.registry()
        result['heads'] = {}
        if not result['configured']:
            return result
        for entry in result['registry']['guests']:
            name = entry['name']
            try:
                with ExitStack() as stack:
                    store = self._head_store(stack, name, self.context.guard)
                    data = store.read('head.json', HEAD.LIMIT)
                    head = HEAD.validate_head(data)
                    self._storage(head)
                    require(all(head[key] == entry[key] for key in
                                ('name', 'uid', 'gid', 'manifest_sha256')),
                            'registry/head identity differs')
                    result['heads'][name] = dict(head=head, head_sha256=digest(data))
            except (ValueError, OSError) as error:
                result['heads'][name] = dict(unavailable=True, error=str(error))
        return result

    def _inspection(self, inspection, name, pin):
        inspection.recheck()
        config = inspection.manifest['config']
        require(config['name'] == name, 'guest identity differs')
        require(inspection.manifest_record['sha256'] == pin, 'manifest pin differs')
        require(inspection.manifest['guest_uuid'] == self.context.storage['guest_uuid'],
                'guest UUID differs')
        require(POLICY.semantic(inspection.context) == POLICY.semantic(
            POLICY.STORAGE.verify_guest_directory(self.context.guest_fd)),
            'guest storage placement differs')
        require(inspection.disk_record == config['disk'],
                'disk differs from trusted manifest; recovery required')
        return {key: config[key] for key in ('name', 'uid', 'gid')}

    def admit_run(self, name, trusted_manifest_sha256, run_id, *, automatic=False,
                  admission_policy=None):
        """Persist admission and retain exclusion; this grants no checkpoint authority.

        A trusted optional policy runs after ready/registry checks under all
        retained locks and returns the exact automatic flag before publication.
        """
        result = receipt('admit-run', name)
        result.update(run_id=run_id, boot_id=self.boot_id,
                      manifest_sha256=trusted_manifest_sha256)
        stack = ExitStack()
        inspection = None
        try:
            CONTEXT.MANAGEMENT.require_managed_runtime()
            REGISTRY._digest(trusted_manifest_sha256)
            REGISTRY._uuid(run_id)
            require(type(automatic) is bool, 'automatic must be boolean')
            require(admission_policy is None or callable(admission_policy),
                    'admission policy must be callable')
            require(name != 'firstkvm', 'firstkvm must remain manual')
            result['stage'] = 'guest-lock'
            lock = stack.enter_context(self.admission.guest_lock(name, self.lease, create=False))

            def guard():
                self.context.guard()
                lock.recheck()
                require(self.admission.is_managed(name, self.lease, lock),
                        'managed marker missing; preparation/recovery required')
                if inspection is not None:
                    self._inspection(inspection, name, trusted_manifest_sha256)

            result['stage'] = 'inspection'
            with AdmissionResources() as inspection_stack:
                inspected = inspection_stack.enter_context(
                    POLICY.Inspection(name, trusted_manifest_sha256))
                inspection = inspected
                guard()
                identity = self._inspection(inspection, name, trusted_manifest_sha256)
                store = self._head_store(stack, name, guard)
                previous_data = store.read('head.json', HEAD.LIMIT)
                previous = HEAD.validate_head(previous_data)
                self._storage(previous)
                require(all(previous[k] == v for k, v in identity.items()),
                        'head identity differs')
                previous_pin = digest(previous_data)
                # Preserve the ordinary API's early ready/once-per-boot refusal.
                # Explicit policy must consume its request only after the full
                # ready cohort and current registry have been authenticated.
                admitted = None
                if admission_policy is None:
                    admitted = HEAD.admit(
                        previous, trusted_manifest_sha256, run_id, self.boot_id,
                        expected_head_sha256=previous_pin, automatic=automatic)
                # Registry lock is acquired last, never while waiting for a guest/disk.
                with AdmissionResources() as registry_stack:
                    registry_lock = registry_stack.enter_context(
                        self.admission.registry_lock(self.lease))

                    def registry_guard():
                        guard()
                        registry_lock.recheck()

                    registry_store, registry_data, registry = self._registry(registry_stack, registry_guard)
                    require(registry is not None, 'registry is unconfigured')
                    entries = [entry for entry in registry['guests'] if entry['name'] == name]
                    require(len(entries) == 1, 'guest is not registered')
                    entry = entries[0]
                    require(all(entry[k] == v for k, v in identity.items()) and
                            entry['manifest_sha256'] == trusted_manifest_sha256,
                            'registry entry conflicts')
                    require(not automatic or entry['enabled'], 'automatic guest disabled')
                    require(entry['shutdown_profile'] == REGISTRY.SHUTDOWN_PROFILE and
                            entry['fixture_profile'] in ('none', 'managed-power-key-v1'),
                            'unsupported managed shutdown/fixture profile')
                    _verify_ready(self, name, guest_lock=lock, inspection=inspection,
                                  head_store=store, head_data=previous_data, registry=(registry_store, registry_data, registry))
                    if admission_policy is not None:
                        result['stage'] = 'admission-policy'
                        automatic = admission_policy(
                            self, name, trusted_manifest_sha256, previous_data,
                            lock, inspection, entry, registry_guard)
                        require(type(automatic) is bool,
                                'admission policy must return exact boolean')
                        registry_guard()
                    require(not automatic or entry['enabled'], 'automatic guest disabled')
                    if admission_policy is not None:
                        admitted = HEAD.admit(
                            previous, trusted_manifest_sha256, run_id, self.boot_id,
                            expected_head_sha256=previous_pin, automatic=automatic)
                    record = dict(schema=1, profile=RUN_PROFILE,
                                  storage=self.context.storage, **identity,
                                  run_id=run_id, boot_id=self.boot_id, automatic=automatic,
                                  manifest_sha256=trusted_manifest_sha256,
                                  previous_head_sha256=previous_pin, previous_head=previous,
                                  registry_generation=registry['generation'],
                                  fixture_profile=entry['fixture_profile'],
                                  shutdown_profile=entry['shutdown_profile'])
                    data = canonical_run(record)
                    result['stage'] = 'run-directory'
                    runs = stack.enter_context(store.child('runs', create=True))
                    result['publications'].append(copy.deepcopy(runs.creation_receipt))
                    result['stage'] = 'run-publication'
                    result['admission_sha256'] = digest(data)
                    result['publications'].append(runs.publish(run_id + '.json', data))
                    result['stage'] = 'head-transition'
                    new_data = HEAD.canonical(admitted)
                    with publish_head_transition(store, previous_data, new_data, result):
                        result.update(stage='head-publication', head_attempted=True)
                        result['publications'].append(store.publish(
                            'head.json', new_data, expected_sha256=previous_pin))
                    result['head_sha256'] = digest(new_data)
                    manifest = copy.deepcopy(inspection.manifest)
                inspection = None
            # Disk flock/input descriptors have closed before a child can launch.
            session = ManagedRun(self, stack, lock, store, runs, record, manifest,
                                 admitted, result)
            session.recheck()
            result.update(stage='complete', success=True)
            return session
        except BaseException as error:
            try:
                stack.close()
            except BaseException as close_error:
                result['close_error'] = admission_failure(close_error)
            raise OperationError(result, error) from error

    def enroll(self, name, trusted_manifest_sha256, *, enabled=False, fixture_profile='none'):
        result = receipt('enroll', name)
        result.update(manifest_sha256=trusted_manifest_sha256, enabled_requested=enabled)
        try:
            CONTEXT.MANAGEMENT.require_managed_runtime()
            REGISTRY._digest(trusted_manifest_sha256)
            require(type(enabled) is bool, 'enabled must be boolean')
            require(name != 'firstkvm', 'firstkvm must remain manual')
            require(type(fixture_profile) is str and fixture_profile in
                    REGISTRY.FIXTURE_PROFILES, 'invalid fixture profile')
            result['stage'] = 'guest-lock'
            with ExitStack() as stack:
                lock = stack.enter_context(self.admission.guest_lock(
                    name, self.lease, create=True))
                result['stage'] = 'inspection'
                inspection = stack.enter_context(POLICY.Inspection(name, trusted_manifest_sha256))

                def guard():
                    self.context.guard()
                    lock.recheck()
                    self._inspection(inspection, name, trusted_manifest_sha256)

                guard()
                identity = self._inspection(inspection, name, trusted_manifest_sha256)
                entry = dict(**identity, manifest_sha256=trusted_manifest_sha256,
                             enabled=enabled, shutdown_profile=REGISTRY.SHUTDOWN_PROFILE,
                             fixture_profile=fixture_profile)
                head_data = HEAD.canonical(HEAD.initial_head(
                    self.context.storage, identity, trusted_manifest_sha256))
                result['head_sha256'] = digest(head_data)
                # Reject a presently invalid/wrong-storage registry before any
                # exclusion/head mutation; final authority is still the fresh
                # read under registry.lock, acquired last below.
                result['stage'] = 'registry-preflight'
                with ExitStack() as preflight:
                    self._registry(preflight, guard)
                result['stage'] = 'managed-preparation'
                result['managed_prepare_attempted'] = True
                self.admission.prepare_managed(self.lease)
                result.update(stage='managed-marker', marker_attempted=True)
                self.admission.mark_managed(name, self.lease, lock, guard=guard)
                result['marker_completed'] = True
                result['stage'] = 'head-directory'
                store = self._head_store(stack, name, guard, True, result)
                try:
                    current = store.read('head.json', HEAD.LIMIT)
                except FileNotFoundError:
                    current = None
                require(current is None or current == head_data,
                        'existing head differs; use enable/disable or recovery')
                if current is None:
                    result.update(stage='head-publication', head_attempted=True)
                    result['publications'].append(store.publish('head.json', head_data))
                else:
                    # An earlier publication may have renamed successfully but
                    # failed its durability barrier. Visibility is insufficient.
                    self._sync_exact(store, 'head.json', digest(current),
                                     HEAD.LIMIT, result, 'head')
                result['stage'] = 'registry-lock'
                registry_lock = stack.enter_context(self.admission.registry_lock(self.lease))

                def registry_guard():
                    guard()
                    registry_lock.recheck()

                self._enroll_registry(stack, registry_guard, entry, result)
            result.update(stage='complete', success=True)
            return result
        except BaseException as error:
            raise OperationError(result, error) from error

    def _enroll_registry(self, stack, guard, entry, result):
        result['stage'] = 'registry-inspection'
        store, data, value = self._registry(stack, guard)
        if value is None:
            value = dict(schema=1, profile=REGISTRY.PROFILE, generation=0,
                         storage=self.context.storage, guests=[])
        matches = [item for item in value['guests'] if item['name'] == entry['name']]
        if matches:
            require({k: v for k, v in matches[0].items() if k != 'enabled'} ==
                    {k: v for k, v in entry.items() if k != 'enabled'},
                    'registry entry conflicts')
            result.update(registry_sha256=digest(data), registry_previous_sha256=digest(data),
                          enabled_current=matches[0]['enabled'])
            self._sync_exact(store, 'autostart.json', digest(data),
                             REGISTRY.LIMIT, result, 'registry')
            return  # Preserve latest enabled, including a concurrent disable.
        require(value['generation'] < REGISTRY.MAX_GENERATION, 'generation exhausted')
        value['generation'] += 1
        value['guests'].append(entry)
        new_data = REGISTRY.canonical(value)
        if store is None:
            result.update(stage='registry-directory', directory_attempted=True)
            store = stack.enter_context(STORE.RootDirectory(
                self.context.state_fd, 'guests', guard=guard, create=True))
            result['publications'].append(copy.deepcopy(store.creation_receipt))
        self._publish_registry(store, data, new_data, result)
        result['enabled_current'] = entry['enabled']

    def _sync_exact(self, store, name, pin, limit, result, kind):
        result.update(stage=kind + '-synchronization',
                      **{kind + '_sync_attempted': True})
        result['synchronizations'].append(store.sync(name, pin=pin, limit=limit))

    def _publish_registry(self, store, old_data, new_data, result):
        result.update(stage='registry-publication', registry_attempted=True,
                      registry_sha256=digest(new_data),
                      registry_previous_sha256=digest(old_data) if old_data is not None else None)
        result['publications'].append(store.publish(
            'autostart.json', new_data,
            expected_sha256=digest(old_data) if old_data is not None else None))

    def _toggle(self, stack, name, enabled, guard, result, identity=None, pin=None):
        result['stage'] = 'registry-lock'
        lock = stack.enter_context(self.admission.registry_lock(self.lease))

        def locked_guard():
            guard()
            lock.recheck()

        result['stage'] = 'registry-inspection'
        store, data, value = self._registry(stack, locked_guard)
        require(value is not None, 'registry is unconfigured')
        entries = [item for item in value['guests'] if item['name'] == name]
        require(len(entries) == 1, 'guest is not registered')
        entry = entries[0]
        if identity is not None:
            require(all(entry[key] == identity[key] for key in identity) and
                    entry['manifest_sha256'] == pin, 'registry entry conflicts')
        result.update(registry_sha256=digest(data), registry_previous_sha256=digest(data),
                      enabled_current=entry['enabled'])
        if entry['enabled'] == enabled:
            self._sync_exact(store, 'autostart.json', digest(data),
                             REGISTRY.LIMIT, result, 'registry')
            return
        changed = REGISTRY.update(value, name, enabled=enabled,
                                  expected_generation=value['generation'])
        self._publish_registry(store, data, REGISTRY.canonical(changed), result)
        result['enabled_current'] = enabled

    def disable(self, name):
        """Change future desired state, leaving current runs/heads/markers intact."""
        result = receipt('disable', name)
        try:
            with ExitStack() as stack:
                self._toggle(stack, name, False, self.context.guard, result)
            result.update(stage='complete', success=True)
            return result
        except BaseException as error:
            raise OperationError(result, error) from error

    def enable(self, name, trusted_manifest_sha256):
        result = receipt('enable', name)
        result['manifest_sha256'] = trusted_manifest_sha256
        try:
            CONTEXT.MANAGEMENT.require_managed_runtime()
            REGISTRY._digest(trusted_manifest_sha256)
            require(name != 'firstkvm', 'firstkvm must remain manual')
            result['stage'] = 'guest-lock'
            with ExitStack() as stack:
                lock = stack.enter_context(self.admission.guest_lock(name, self.lease, create=False))
                result['stage'] = 'inspection'
                inspection = stack.enter_context(POLICY.Inspection(name, trusted_manifest_sha256))

                def guard():
                    self.context.guard()
                    lock.recheck()
                    self._inspection(inspection, name, trusted_manifest_sha256)

                guard()
                require(self.admission.is_managed(name, self.lease, lock),
                        'managed marker missing; preparation/recovery required')
                identity = self._inspection(inspection, name, trusted_manifest_sha256)
                store = self._head_store(stack, name, guard)
                data = store.read('head.json', HEAD.LIMIT)
                head = HEAD.validate_head(data)
                self._storage(head)
                require(head['status'] == 'ready' and all(head[k] == v for k, v in identity.items())
                        and head['manifest_sha256'] == trusted_manifest_sha256,
                        'ready trusted head required')
                _verify_ready(self, name, guest_lock=lock, inspection=inspection,
                              head_store=store, head_data=data)
                result['head_sha256'] = digest(data)
                self._toggle(stack, name, True, guard, result, identity, trusted_manifest_sha256)
            result.update(stage='complete', success=True)
            return result
        except BaseException as error:
            raise OperationError(result, error) from error


TRANSITION_LIMIT = 256 * 1024
TRANSITION_PROFILE = 'a733-guest-head-transition-v1'
TRANSITION_FIELDS = {'schema', 'profile', 'previous_head_sha256', 'new_head_sha256',
                     'previous_head_base64', 'new_head_base64'}


def validate_head_transition(data):
    """Validate exact canonical before/after head bytes without adopting either."""
    require(type(data) is bytes and 0 < len(data) <= TRANSITION_LIMIT,
            'invalid head transition size')
    text = data.decode('utf-8')
    REGISTRY._check_depth(text)
    value = json.loads(text, object_pairs_hook=REGISTRY.IDENTITY.unique_pairs,
                       parse_constant=REGISTRY._reject_constant)
    require(type(value) is dict and set(value) == TRANSITION_FIELDS,
            'invalid head transition fields')
    require(type(value['schema']) is int and value['schema'] == 1 and
            value['profile'] == TRANSITION_PROFILE, 'invalid head transition schema/profile')
    heads = []
    for prefix in ('previous', 'new'):
        pin, encoded = value[prefix + '_head_sha256'], value[prefix + '_head_base64']
        REGISTRY._digest(pin)
        require(type(encoded) is str and len(encoded) <= 4 * ((HEAD.LIMIT + 2) // 3),
                'invalid encoded head size')
        raw = base64.b64decode(encoded, validate=True)
        head = HEAD.validate_head(raw)
        require(HEAD.canonical(head) == raw and digest(raw) == pin and
                base64.b64encode(raw).decode('ascii') == encoded,
                'head transition bytes/hash differ')
        heads.append(head)
    previous, new = heads
    require(new['sequence'] == previous['sequence'] + 1 and
            new['previous_head_sha256'] == value['previous_head_sha256'] and
            all(previous[key] == new[key] for key in ('storage', 'name', 'uid', 'gid')),
            'head transition predecessor differs')
    return value


def head_transition(previous_data, new_data):
    value = dict(schema=1, profile=TRANSITION_PROFILE,
                 previous_head_sha256=digest(previous_data), new_head_sha256=digest(new_data),
                 previous_head_base64=base64.b64encode(previous_data).decode('ascii'),
                 new_head_base64=base64.b64encode(new_data).decode('ascii'))
    data = (json.dumps(value, sort_keys=True, separators=(',', ':'),
                       allow_nan=False) + '\n').encode('utf-8')
    validate_head_transition(data)
    return data


@contextmanager
def publish_head_transition(store, previous_data, new_data, result):
    """Durably retain exact head edge before its CAS; exact retries sync, never adopt."""
    data = head_transition(previous_data, new_data)
    name = digest(previous_data) + '-' + digest(new_data) + '.json'
    result['stage'] = 'head-transition-directory'
    with store.child('transitions', create=True) as transitions:
        result['publications'].append(copy.deepcopy(transitions.creation_receipt))
        result['stage'] = 'head-transition-inspection'
        try:
            existing = transitions.read(name, TRANSITION_LIMIT)
        except FileNotFoundError as error:
            if error.filename != name:
                raise
            existing = None
        if existing is None:
            result['stage'] = 'head-transition-publication'
            result['publications'].append(transitions.publish(name, data))
        else:
            validate_head_transition(existing)
            require(existing == data, 'existing head transition differs')
            result['stage'] = 'head-transition-synchronization'
            result['synchronizations'].append(transitions.sync(
                name, pin=digest(data), limit=TRANSITION_LIMIT))
        previous_guard = store.guard

        def chain_guard():
            previous_guard()
            transitions.read(name, TRANSITION_LIMIT, pin=digest(data))

        store.guard = chain_guard
        try:
            chain_guard()
            yield
            chain_guard()
        finally:
            store.guard = previous_guard


RUN_PROFILE = 'a733-guest-run-admission-v1'
RUN_FIELDS = {'schema', 'profile', 'storage', 'name', 'uid', 'gid', 'run_id',
              'boot_id', 'automatic', 'manifest_sha256', 'previous_head_sha256',
              'previous_head', 'registry_generation', 'fixture_profile', 'shutdown_profile'}


def _canonical_record(value):
    data = (json.dumps(value, sort_keys=True, separators=(',', ':'),
                       allow_nan=False) + '\n').encode('utf-8')
    require(0 < len(data) <= HEAD.LIMIT, 'invalid run record size')
    return data


def validate_run(data):
    """Validate bounded, unambiguous admission bytes, returning detached data."""
    require(type(data) is bytes and 0 < len(data) <= HEAD.LIMIT, 'invalid run record size')
    text = data.decode('utf-8')
    REGISTRY._check_depth(text)
    value = json.loads(text, object_pairs_hook=REGISTRY.IDENTITY.unique_pairs,
                       parse_constant=REGISTRY._reject_constant)
    require(type(value) is dict and set(value) == RUN_FIELDS, 'invalid run record fields')
    require(type(value['schema']) is int and value['schema'] == 1 and
            value['profile'] == RUN_PROFILE, 'invalid run schema/profile')
    for key in ('run_id', 'boot_id'):
        REGISTRY._uuid(value[key])
    require(type(value['automatic']) is bool, 'automatic must be boolean')
    generation = value['registry_generation']
    require(type(generation) is int and 0 <= generation <= REGISTRY.MAX_GENERATION,
            'invalid registry generation')
    require(type(value['fixture_profile']) is str and
            value['fixture_profile'] in REGISTRY.FIXTURE_PROFILES, 'invalid fixture profile')
    require(value['shutdown_profile'] == REGISTRY.SHUTDOWN_PROFILE and
            type(value['shutdown_profile']) is str, 'unsupported shutdown profile')
    require(value['fixture_profile'] in ('none', 'managed-power-key-v1'),
            'unsupported managed fixture profile')
    previous = HEAD.validate_head(HEAD.canonical(value['previous_head']))
    require(previous['storage'] == value['storage'] and
            all(previous[key] == value[key] for key in
                ('name', 'uid', 'gid', 'manifest_sha256')), 'run/prior head binding differs')
    HEAD.admit(previous, value['manifest_sha256'], value['run_id'], value['boot_id'],
               expected_head_sha256=value['previous_head_sha256'],
               automatic=value['automatic'])
    require(value['name'] != 'firstkvm', 'firstkvm must remain manual')
    return value


def canonical_run(value):
    return _canonical_record(validate_run(_canonical_record(value)))


class ManagedRun:
    """Own retained stores/guest exclusion, borrowing the controller and lease.

    mark_running requires the supervisor's authenticated direct child and paused
    QMP readiness. No method here establishes that evidence or grants checkpoint
    authority. A failed transition poisons this object even if rename succeeded.
    """
    def __init__(self, controller, stack, lock, store, runs, record, manifest, head, result):
        self._controller, self._stack = controller, stack
        self._lock, self._store, self._runs = lock, store, runs
        self._record = validate_run(canonical_run(record))
        self._manifest = copy.deepcopy(manifest)
        self._head = HEAD.validate_head(HEAD.canonical(head))
        self._head_pin = digest(HEAD.canonical(head))
        self._admission_pin = digest(canonical_run(record))
        self._closed = self._poisoned = False
        self._receipt = result
        self._placement_guard = store.guard
        store.guard = self._record_guard

    def _record_guard(self):
        self._placement_guard()
        data = self._runs.read(self.run_id + '.json', HEAD.LIMIT, pin=self._admission_pin)
        require(validate_run(data) == self._record, 'admission record differs')

    @property
    def lease(self):
        return self._controller.lease

    @property
    def guest_lock(self):
        return self._lock

    @property
    def manifest(self):
        return copy.deepcopy(self._manifest)

    @property
    def config(self):
        return copy.deepcopy(self._manifest['config'])

    @property
    def run_id(self):
        return self._record['run_id']

    @property
    def boot_id(self):
        return self._record['boot_id']

    @property
    def head_sha256(self):
        return self._head_pin

    @property
    def admission_sha256(self):
        return self._admission_pin

    @property
    def receipt(self):
        return copy.deepcopy(self._receipt)

    def recheck(self):
        require(not self._closed and not self._poisoned, 'run session closed or poisoned')
        self._controller.context.guard()
        self._lock.recheck()
        require(self._controller.admission.is_managed(
            self._record['name'], self.lease, self._lock), 'managed marker missing')
        data = self._runs.read(self.run_id + '.json', HEAD.LIMIT, pin=self._admission_pin)
        require(validate_run(data) == self._record, 'admission record differs')
        require(self._record['storage'] == self._controller.context.storage,
                'run storage identity differs')
        data = self._store.read('head.json', HEAD.LIMIT, pin=self._head_pin)
        head = HEAD.validate_head(data)
        require(head == self._head and all(head[key] == self._record[key] for key in
                ('storage', 'name', 'uid', 'gid', 'manifest_sha256', 'run_id')) and
                head['last_boot_id'] == self.boot_id, 'current run head differs')
        return copy.deepcopy(head)

    def _transition(self, operation, builder):
        result = receipt(operation, self._record['name'])
        result.update(run_id=self.run_id, boot_id=self.boot_id,
                      head_sha256=self._head_pin, admission_sha256=self._admission_pin)
        self._pending_failure_receipt = None
        try:
            result['stage'] = 'run-recheck'
            head = self.recheck()
            new = builder(head)
            if self._pending_failure_receipt is not None:
                result['publications'].append(self._pending_failure_receipt)
            data = HEAD.canonical(new)
            result['stage'] = 'head-transition'
            with publish_head_transition(self._store, HEAD.canonical(head), data, result):
                result.update(stage='head-publication', head_attempted=True)
                result['publications'].append(self._store.publish(
                    'head.json', data, expected_sha256=self._head_pin))
            # Never adopt a merely visible publication following an exception.
            self._head, self._head_pin = new, digest(data)
            result.update(stage='complete', success=True, head_sha256=self._head_pin)
            self._receipt = result
            return copy.deepcopy(result)
        except BaseException as error:
            if (self._pending_failure_receipt is not None and
                    self._pending_failure_receipt not in result['publications']):
                result['publications'].append(self._pending_failure_receipt)
            self._poisoned = True
            self._receipt = result
            raise OperationError(result, error) from error

    def mark_running(self):
        """Persist externally authenticated direct-child/paused-QMP readiness."""
        return self._transition('mark-running', lambda head: HEAD.mark_running(
            head, expected_head_sha256=self._head_pin))

    def mark_stopping(self):
        """Persist externally authenticated stop request or observed self-shutdown."""
        return self._transition('mark-stopping', lambda head: HEAD.mark_stopping(
            head, expected_head_sha256=self._head_pin))

    def quarantine(self, reasoncode):
        """Retain exact failure authority; no force, adoption or reset is provided."""
        def build(head):
            require(type(reasoncode) is str and
                    re.fullmatch(r'[a-z][a-z0-9-]{0,63}', reasoncode) is not None,
                    'invalid quarantine reason code')
            record = dict(schema=1, profile='a733-guest-run-failure-v1',
                          **{key: copy.deepcopy(self._record[key]) for key in
                             ('storage', 'name', 'uid', 'gid', 'run_id', 'boot_id',
                              'manifest_sha256')},
                          head_sha256=self._head_pin, head=head, code=reasoncode)
            data = _canonical_record(record)
            self._pending_failure_receipt = self._runs.publish(self.run_id + '.failure.json', data)
            return HEAD.quarantine(head, digest(data), expected_head_sha256=self._head_pin)

        return self._transition('quarantine', build)

    def close(self):
        if self._closed:
            return
        self._closed = True
        stack, self._stack = self._stack, None
        stack.close()  # ExitStack attempts every callback, without recycling FDs.

    def __enter__(self):
        self.recheck()
        return self

    def __exit__(self, *_args):
        self.close()
