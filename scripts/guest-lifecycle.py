#!/usr/bin/python3
"""Bounded opt-in registry/head I/O; no VM supervision or checkpoint authority.

Only explicit enrollment creates persistent directories and the initial head.
Failures retain partial publications and RAM exclusion; recovery is explicit.
This API has no CLI, launch, head transition, rollback or hardware acceptance.
"""
import copy
from contextlib import ExitStack
import hashlib
import importlib.util
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

    def enroll(self, name, trusted_manifest_sha256, *, enabled=False, fixture_profile='none'):
        result = receipt('enroll', name)
        result.update(manifest_sha256=trusted_manifest_sha256, enabled_requested=enabled)
        try:
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
                result['head_sha256'] = digest(data)
                self._toggle(stack, name, True, guard, result, identity, trusted_manifest_sha256)
            result.update(stage='complete', success=True)
            return result
        except BaseException as error:
            raise OperationError(result, error) from error
