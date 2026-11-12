"""Host-only controller fixtures with actual protected-store I/O; no hardware claim."""
import copy
from contextlib import contextmanager
import importlib.util
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location(
    'lifecycle_controller_test', Path(__file__).resolve().parents[1] /
    'scripts/guest-lifecycle.py')
c = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(c)
BOOT = '11111111-1111-4111-8111-111111111111'
STORAGE = dict(state_uuid='22222222-2222-4222-8222-222222222222',
               guest_uuid='33333333-3333-4333-8333-333333333333', nvme_serial='SSD-123')
PIN = 'a' * 64


class AdmissionCleanupTests(unittest.TestCase):
    def test_registry_cleanup_retains_consumption_uncertainty(self):
        primary = c.STORE.PublicationError(dict(stage='directory-fsync',
            consumed=True, uncertain=True, request_id=BOOT), OSError('consume fsync failed'))
        def failed_close():
            raise OSError('registry close failed')
        with self.assertRaises(c.AdmissionCleanupError) as caught:
            with c.AdmissionResources() as resources:
                resources.callback(failed_close)
                raise primary
        detail = caught.exception.receipt
        self.assertTrue(detail['primary_error']['receipt']['consumed'])
        self.assertTrue(detail['primary_error']['receipt']['uncertain'])
        self.assertIn('registry close failed', detail['cleanup_error']['error'])

    def test_inspection_cleanup_retains_nested_registry_failure(self):
        primary = c.AdmissionCleanupError(ValueError('policy refused'), OSError('registry close failed'))
        def failed_close():
            raise OSError('inspection close failed')
        with self.assertRaises(c.AdmissionCleanupError) as caught:
            with c.AdmissionResources() as resources:
                resources.callback(failed_close)
                raise primary
        detail = caught.exception.receipt
        self.assertIn('policy refused', detail['primary_error']['receipt']['primary_error']['error'])
        self.assertIn('inspection close failed', detail['cleanup_error']['error'])


class ControllerTests(unittest.TestCase):
    def test_managed_fixture_enrollment_records_explicit_profile(self):
        with self.controller as controller:
            result = controller.enroll('autotest', PIN, fixture_profile='managed-power-key-v1')
            self.assertTrue(result['success'])
            self.assertEqual(controller.registry()['registry']['guests'][0]['fixture_profile'],
                             'managed-power-key-v1')

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.state, self.guests = self.base / 'state', self.base / 'guests'
        self.state.mkdir(mode=0o700)
        self.guests.mkdir(mode=0o711)
        self.events = []
        self.markers = set()
        self.allow = True
        self.disk = dict(bytes=512, sha256='d' * 64)
        self.config = dict(name='autotest', uid=60002, gid=60002, disk=self.disk.copy())
        self.manifest_uuid = STORAGE['guest_uuid']
        self.placement = dict(namespace='nvme0n1', guest_device=[259, 5])
        self.on_registry_lock = None
        self.fail_guest_lock = False
        fixture = self

        class Context:
            def __init__(self, expected_storage, lease, *, boot_id):
                fixture.assertEqual(expected_storage, STORAGE)
                fixture.assertEqual(boot_id, BOOT)
                self.storage = copy.deepcopy(expected_storage)
                self.state_fd = os.open(fixture.state, os.O_RDONLY | os.O_DIRECTORY)
                self.guest_fd = os.open(fixture.guests, os.O_RDONLY | os.O_DIRECTORY)
                self.closed = False

            def guard(self):
                c.require(not self.closed and fixture.allow, 'context storage changed')

            def __enter__(self):
                self.guard()
                return self

            def close(self):
                if not self.closed:
                    self.closed = True
                    os.close(self.state_fd)
                    os.close(self.guest_fd)

            def __exit__(self, *unused):
                self.close()

        class Inspection:
            def __init__(self, name, pin):
                fixture.events.append('inspect')
                fixture.assertEqual(pin, PIN)
                self.manifest = dict(config=copy.deepcopy(fixture.config),
                                     guest_uuid=fixture.manifest_uuid)
                self.manifest_record = dict(sha256=pin)
                self.disk_record = copy.deepcopy(fixture.disk)
                self.context = fixture.placement.copy()

            def recheck(self):
                fixture.events.append('inspect-check')

            def __enter__(self):
                return self

            def __exit__(self, *unused):
                fixture.events.append('inspect-close')

        class Admission:
            @contextmanager
            def guest_lock(self, name, lease, *, create=False):
                fixture.events.append('guest-lock')
                c.require(not fixture.fail_guest_lock, 'guest lock busy')
                lock = SimpleNamespace(recheck=lambda: fixture.events.append('guest-check'))
                yield lock
                fixture.events.append('guest-unlock')

            @contextmanager
            def registry_lock(self, lease):
                fixture.events.append('registry-lock')
                if fixture.on_registry_lock:
                    callback, fixture.on_registry_lock = fixture.on_registry_lock, None
                    callback()
                yield SimpleNamespace(recheck=lambda: fixture.events.append('registry-check'))
                fixture.events.append('registry-unlock')

            def prepare_managed(self, lease):
                fixture.events.append('prepare-marker')

            def mark_managed(self, name, lease, lock, *, guard):
                guard()
                fixture.events.append('marker')
                fixture.markers.add(name)
                guard()

            def is_managed(self, name, lease, lock):
                return name in fixture.markers

        self.admission = Admission()
        self.lease = SimpleNamespace(admission=self.admission)
        patches = [mock.patch.object(c.CONTEXT, 'HostContext', Context),
                   mock.patch.object(c.POLICY, 'Inspection', Inspection),
                   mock.patch.object(c.POLICY.STORAGE, 'verify_guest_directory',
                                     side_effect=lambda fd: self.placement.copy()),
                   mock.patch.object(c.STORE, '_root_owned', lambda info:
                                     info.st_uid == os.getuid() and info.st_gid == os.getgid())]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.controller = c.StateController(STORAGE, self.lease, boot_id=BOOT)
        self.addCleanup(self.controller.close)
        self.head = self.guests / '.a733-lifecycle/autotest/head.json'
        self.registry = self.state / 'guests/autostart.json'

    def enroll(self, **kwargs):
        return self.controller.enroll('autotest', PIN, **kwargs)

    def rewrite_registry(self, update):
        value = c.REGISTRY.validate_registry(self.registry.read_bytes())
        update(value)
        self.registry.chmod(0o600)
        self.registry.write_bytes(c.REGISTRY.canonical(value))
        self.registry.chmod(0o400)

    def failure(self, callable_, message=None):
        with self.assertRaises(c.OperationError) as caught:
            callable_()
        result = caught.exception.receipt
        self.assertFalse(result['success'])
        self.assertFalse(result['hardware_acceptance'])
        if message:
            self.assertIn(message, str(caught.exception))
        return result

    def test_missing_status_does_not_mutate_and_context_borrows_lease(self):
        before = list(self.state.iterdir()), list(self.guests.iterdir())
        with self.controller as controller:
            self.assertEqual(controller.registry()['configured'], False)
            self.assertEqual(controller.status()['heads'], {})
        self.assertEqual(before, (list(self.state.iterdir()), list(self.guests.iterdir())))
        self.assertIs(self.lease.admission, self.admission)
        self.assertIs(c.ADMISSION, c.CONTEXT.ADMISSION)

    def test_success_default_optin_modes_and_order(self):
        result = self.enroll()
        self.assertTrue(result['success'])
        self.assertTrue(result['marker_completed'])
        self.assertFalse(result['hardware_acceptance'])
        value = self.controller.registry()['registry']
        self.assertEqual(value['generation'], 1)
        self.assertFalse(value['guests'][0]['enabled'])
        self.assertEqual(c.HEAD.validate_head(self.head.read_bytes())['sequence'], 0)
        for path in (self.head, self.registry):
            self.assertEqual(path.stat().st_mode & 0o777, 0o400)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
        self.assertLess(self.events.index('marker'), self.events.index('registry-lock'))
        self.assertLess(self.events.index('registry-unlock'), self.events.index('guest-unlock'))
        self.assertEqual(self.controller.status()['heads']['autotest']['head']['status'], 'ready')

    def test_enable_disable_idempotent(self):
        self.enroll()
        self.controller.enable('autotest', PIN)
        self.assertTrue(self.controller.registry()['registry']['guests'][0]['enabled'])
        previous = self.registry.read_bytes()
        result = self.controller.enable('autotest', PIN)
        self.assertFalse(result['registry_attempted'])
        self.assertEqual(previous, self.registry.read_bytes())
        self.controller.disable('autotest')
        previous = self.registry.read_bytes()
        self.controller.disable('autotest')
        self.assertEqual(previous, self.registry.read_bytes())
        self.assertIn('autotest', self.markers)

    def test_refuse_firstkvm_types_pin_before_marker_or_directories(self):
        for run in (lambda: self.controller.enroll('firstkvm', PIN),
                    lambda: self.enroll(enabled=1), lambda: self.enroll(fixture_profile='bad'),
                    lambda: self.controller.enroll('autotest', 'B' * 64)):
            self.failure(run)
            self.assertFalse(self.markers)
            self.assertFalse(list(self.state.iterdir()))
            self.assertFalse(list(self.guests.iterdir()))

    def test_stale_disk_and_wrong_uuid_no_marker(self):
        self.disk['sha256'] = 'e' * 64
        self.failure(self.enroll, 'disk differs')
        self.assertFalse(self.markers)
        self.disk = self.config['disk'].copy()
        self.manifest_uuid = STORAGE['state_uuid']
        self.failure(self.enroll, 'UUID differs')
        self.assertFalse(self.markers)

    def test_lock_failure_and_wrong_storage_no_marker(self):
        self.fail_guest_lock = True
        self.failure(self.enroll, 'busy')
        self.fail_guest_lock = False
        self.allow = False
        self.failure(self.enroll, 'storage changed')
        self.assertFalse(self.markers)
        self.assertFalse(self.head.exists())

    def test_same_head_registry_idempotency_preserves_disabled(self):
        self.enroll(enabled=True)
        self.controller.disable('autotest')
        old_head, old_registry = self.head.read_bytes(), self.registry.read_bytes()
        result = self.enroll(enabled=True)
        self.assertEqual(old_head, self.head.read_bytes())
        self.assertEqual(old_registry, self.registry.read_bytes())
        self.assertFalse(result['registry_attempted'])
        self.assertFalse(result['head_attempted'])

    def test_concurrent_disable_at_final_registry_lock_is_preserved(self):
        self.enroll(enabled=True)
        self.on_registry_lock = lambda: self.rewrite_registry(
            lambda value: value['guests'][0].update(enabled=False))
        self.enroll(enabled=True)
        self.assertFalse(self.controller.registry()['registry']['guests'][0]['enabled'])

    def test_uid_conflict_and_noninitial_head_refuse(self):
        self.enroll()
        self.config['uid'] = 60003
        self.failure(self.enroll, 'fixed equal UID/GID')
        self.config['gid'] = 60003
        self.failure(self.enroll, 'head differs')
        self.failure(lambda: self.controller.enable('autotest', PIN), 'ready trusted head')
        self.config.update(uid=60002, gid=60002)
        self.rewrite_registry(lambda v: v['guests'][0].update(uid=60003, gid=60003))
        self.failure(self.enroll, 'entry conflicts')
        self.failure(lambda: self.controller.enable('autotest', PIN), 'entry conflicts')

    def test_disable_needs_no_head_disk_marker_or_guest_lock(self):
        self.enroll(enabled=True)
        head = self.head.read_bytes()
        self.head.chmod(0o600)
        self.head.write_bytes(b'quarantined or invalid')
        self.head.chmod(0o400)
        self.markers.clear()
        self.events.clear()
        with mock.patch.object(c.POLICY, 'Inspection', side_effect=AssertionError('disk inspected')):
            self.controller.disable('autotest')
            status = self.controller.status()
        self.assertTrue(status['heads']['autotest']['unavailable'])
        self.assertNotIn('guest-lock', self.events)
        self.assertEqual(self.head.read_bytes(), b'quarantined or invalid')
        self.assertNotEqual(head, self.head.read_bytes())

    def test_enable_missing_marker_and_changed_disk_refuse_without_registry_write(self):
        self.enroll()
        old = self.registry.read_bytes()
        self.markers.clear()
        self.failure(lambda: self.controller.enable('autotest', PIN), 'marker missing')
        self.assertEqual(old, self.registry.read_bytes())
        self.markers.add('autotest')
        self.disk['sha256'] = 'e' * 64
        self.failure(lambda: self.controller.enable('autotest', PIN), 'disk differs')
        self.assertEqual(old, self.registry.read_bytes())

    def test_registry_publication_failure_retains_head_marker_and_partial_receipt(self):
        publish = c.STORE.RootDirectory.publish

        def fail(store, name, data, **kwargs):
            if name == 'autostart.json':
                raise c.STORE.PublicationError(dict(stage='publish', published=False,
                                                    uncertain=True), OSError('injected'))
            return publish(store, name, data, **kwargs)

        with mock.patch.object(c.STORE.RootDirectory, 'publish', fail):
            result = self.failure(self.enroll)
        self.assertTrue(self.head.exists())
        self.assertIn('autotest', self.markers)
        self.assertTrue(result['head_attempted'])
        self.assertTrue(result['registry_attempted'])
        self.assertTrue(result['publication_error']['uncertain'])
        self.assertFalse(self.registry.exists())
        self.enroll()  # Exact initial head permits explicit completion.

    def test_head_postpublication_failure_does_not_delete_head_or_marker(self):
        publish = c.STORE.RootDirectory.publish

        def fail(store, name, data, **kwargs):
            result = publish(store, name, data, **kwargs)
            if name == 'head.json':
                result.update(stage='after-publication', success=False, uncertain=True)
                raise c.STORE.PublicationError(result, OSError('injected'))
            return result

        with mock.patch.object(c.STORE.RootDirectory, 'publish', fail):
            result = self.failure(self.enroll)
        self.assertTrue(result['publication_error']['published'])
        self.assertTrue(self.head.exists())
        self.assertFalse(self.registry.exists())
        self.assertIn('autotest', self.markers)

    def test_cas_race_keeps_other_publisher_and_failure_evidence(self):
        self.enroll()
        publish = c.STORE.RootDirectory.publish

        def race(store, name, data, **kwargs):
            if name == 'autostart.json':
                self.rewrite_registry(lambda value: value.update(generation=99))
            return publish(store, name, data, **kwargs)

        with mock.patch.object(c.STORE.RootDirectory, 'publish', race):
            result = self.failure(lambda: self.controller.enable('autotest', PIN))
        self.assertTrue(result['registry_attempted'])
        self.assertFalse(result['publication_error']['published'])
        value = self.controller.registry()['registry']
        self.assertEqual(value['generation'], 99)
        self.assertFalse(value['guests'][0]['enabled'])

    def test_wrong_storage_registry_preflight_does_not_publish_new_marker(self):
        self.enroll()
        self.markers.clear()
        self.rewrite_registry(lambda value: value['storage'].update(nvme_serial='other'))
        result = self.failure(self.enroll, 'storage identity')
        self.assertFalse(result['marker_attempted'])
        self.assertFalse(self.markers)

    def test_invalid_registry_storage_blocks_disable(self):
        self.enroll(enabled=True)
        self.rewrite_registry(lambda value: value['storage'].update(nvme_serial='other'))
        before = self.registry.read_bytes()
        self.failure(lambda: self.controller.disable('autotest'), 'storage identity')
        self.assertEqual(before, self.registry.read_bytes())

    def test_quarantined_head_never_becomes_ready_by_enrollment_or_enable(self):
        self.enroll()
        previous = self.head.read_bytes()
        head = c.HEAD.quarantine(c.HEAD.validate_head(previous), 'f' * 64,
                                 expected_head_sha256=c.digest(previous))
        self.head.chmod(0o600)
        self.head.write_bytes(c.HEAD.canonical(head))
        self.head.chmod(0o400)
        self.failure(self.enroll, 'head differs')
        self.failure(lambda: self.controller.enable('autotest', PIN), 'ready trusted head')
        self.assertEqual(self.head.read_bytes(), c.HEAD.canonical(head))

    def test_disable_preserves_unrelated_guests(self):
        self.enroll(enabled=True)

        def add(value):
            entry = {**value['guests'][0], 'name': 'other', 'uid': 60003, 'gid': 60003}
            value['guests'].append(entry)

        self.rewrite_registry(add)
        other = self.controller.registry()['registry']['guests'][1]
        self.controller.disable('autotest')
        current = self.controller.registry()['registry']
        self.assertEqual(current['guests'][1], other)
        self.assertEqual(current['generation'], 2)

    def test_marker_failure_receipt_prevents_persistent_publication(self):
        def fail(name, lease, lock, *, guard):
            guard()
            self.markers.add(name)
            raise c.ADMISSION.PublicationError('guard', True)

        with mock.patch.object(self.admission, 'mark_managed', side_effect=fail):
            result = self.failure(self.enroll)
        self.assertTrue(result['managed_prepare_attempted'])
        self.assertTrue(result['marker_attempted'])
        self.assertFalse(result['marker_completed'])
        self.assertEqual(result['marker_error'], dict(stage='guard', uncertain=True))
        self.assertFalse(self.head.exists())
        self.assertFalse(self.registry.exists())
        self.assertIn('autotest', self.markers)

    def test_partial_directory_creation_is_retained_and_reported(self):
        mkdir = os.mkdir

        def fail(name, *args, **kwargs):
            mkdir(name, *args, **kwargs)
            if name == '.a733-lifecycle':
                raise OSError('injected after mkdir')

        with mock.patch.object(c.STORE.os, 'mkdir', side_effect=fail):
            result = self.failure(self.enroll)
        self.assertTrue(result['directory_attempted'])
        self.assertTrue(result['publication_error']['mutation_possible'])
        self.assertTrue((self.guests / '.a733-lifecycle').exists())
        self.assertFalse(self.registry.exists())
        self.assertIn('autotest', self.markers)

    def test_authority_missing_file_is_not_reported_as_unconfigured(self):
        with mock.patch.object(self.controller.context, 'guard',
                               side_effect=FileNotFoundError(2, 'missing state marker', '.a733-state')):
            with self.assertRaises(FileNotFoundError):
                self.controller.registry()

    def test_registry_missing_file_in_existing_private_directory_can_enroll(self):
        (self.state / 'guests').mkdir(mode=0o700)
        self.assertFalse(self.controller.registry()['configured'])
        self.enroll()
        self.assertEqual(self.controller.registry()['registry']['generation'], 1)

    def test_missing_registry_disable_never_creates_persistent_directory(self):
        self.failure(lambda: self.controller.disable('autotest'), 'unconfigured')
        self.assertFalse(list(self.state.iterdir()))
        self.assertFalse(list(self.guests.iterdir()))

    def test_explicit_enabled_and_fixture_receipt_and_idempotent_desired_policy(self):
        first = self.enroll(enabled=True, fixture_profile='first-kvm-http-v1')
        self.assertTrue(first['enabled_current'])
        self.controller.disable('autotest')
        again = self.enroll(enabled=True, fixture_profile='first-kvm-http-v1')
        self.assertTrue(again['enabled_requested'])
        self.assertFalse(again['enabled_current'])
        self.assertEqual(again['manifest_sha256'], PIN)
        self.assertEqual(again['registry_sha256'], c.digest(self.registry.read_bytes()))
        self.failure(lambda: self.enroll(fixture_profile='none'), 'entry conflicts')

    def test_head_identity_and_storage_are_metadata_only_status_failures(self):
        self.enroll()
        head = c.HEAD.validate_head(self.head.read_bytes())
        head['storage']['nvme_serial'] = 'other'
        self.head.chmod(0o600)
        self.head.write_bytes(c.HEAD.canonical(head))
        self.head.chmod(0o400)
        self.assertTrue(self.controller.status()['heads']['autotest']['unavailable'])
        old = self.head.read_bytes()
        self.controller.disable('autotest')
        self.assertEqual(old, self.head.read_bytes())

    def _registry_postrename_repeat(self, operation):
        if operation != 'enroll':
            self.enroll(enabled=operation == 'disable')
        before = self.registry.read_bytes() if self.registry.exists() else None
        command = {'enroll': lambda: self.enroll(enabled=True),
                   'enable': lambda: self.controller.enable('autotest', PIN),
                   'disable': lambda: self.controller.disable('autotest')}[operation]
        fsync = os.fsync

        def fail_after_rename(fd):
            target = os.readlink('/proc/self/fd/' + str(fd))
            if (target == str(self.registry.parent) and self.registry.exists() and
                    self.registry.read_bytes() != before):
                raise OSError('postrename registry parent fsync')
            return fsync(fd)

        with mock.patch.object(c.STORE.os, 'fsync', side_effect=fail_after_rename):
            failed = self.failure(command)
        self.assertTrue(failed['publication_error']['published'])
        self.assertFalse(failed['publication_error']['durable'])
        stable_head, stable_registry = self.head.read_bytes(), self.registry.read_bytes()
        for failing_target in (self.registry, self.registry.parent):
            def fail_sync(fd):
                if os.readlink('/proc/self/fd/' + str(fd)) == str(failing_target):
                    raise OSError('explicit repeat synchronization failed')
                return fsync(fd)

            with (mock.patch.object(c.STORE.os, 'fsync', side_effect=fail_sync),
                  mock.patch.object(c.STORE.RootDirectory, 'publish',
                                    side_effect=AssertionError('repeat must not publish'))):
                failed = self.failure(command)
            self.assertTrue(failed['registry_sync_attempted'])
            self.assertFalse(failed['registry_attempted'])
            self.assertFalse(failed['publication_error']['durable'])
            self.assertEqual(stable_registry, self.registry.read_bytes())
            self.assertEqual(stable_head, self.head.read_bytes())
        barriers = []

        def record(fd):
            barriers.append(os.readlink('/proc/self/fd/' + str(fd)))
            return fsync(fd)

        with (mock.patch.object(c.STORE.os, 'fsync', side_effect=record),
              mock.patch.object(c.STORE.RootDirectory, 'publish',
                                side_effect=AssertionError('repeat must not publish'))):
            result = command()
        self.assertTrue(result['success'])
        self.assertTrue(result['registry_sync_attempted'])
        self.assertFalse(result['registry_attempted'])
        self.assertIn(str(self.registry), barriers)
        self.assertIn(str(self.registry.parent), barriers)
        synced = result['synchronizations'][-1]
        self.assertTrue(synced['file_fsynced'])
        self.assertTrue(synced['directory_fsynced'])
        self.assertTrue(synced['durable'])
        self.assertEqual(stable_registry, self.registry.read_bytes())
        self.assertEqual(stable_head, self.head.read_bytes())
        return result

    def test_explicit_disable_repeat_establishes_durability_without_republication(self):
        self._registry_postrename_repeat('disable')

    def test_explicit_enable_repeat_establishes_durability_without_republication(self):
        self._registry_postrename_repeat('enable')

    def test_explicit_enroll_repeat_establishes_head_and_registry_durability(self):
        result = self._registry_postrename_repeat('enroll')
        self.assertTrue(result['head_sync_attempted'])
        self.assertEqual(len(result['synchronizations']), 2)
        self.assertEqual(result['synchronizations'][0]['sha256'], c.digest(self.head.read_bytes()))

    def test_existing_head_sync_failure_blocks_registry_commit(self):
        fsync = os.fsync

        def fail_initial_head_parent(fd):
            if (self.head.exists() and
                    os.readlink('/proc/self/fd/' + str(fd)) == str(self.head.parent)):
                raise OSError('head parent fsync after publication')
            return fsync(fd)

        with mock.patch.object(c.STORE.os, 'fsync', side_effect=fail_initial_head_parent):
            failed = self.failure(self.enroll)
        self.assertTrue(failed['publication_error']['published'])
        self.assertFalse(failed['publication_error']['durable'])
        self.assertTrue(self.head.exists())
        self.assertFalse(self.registry.exists())
        sync = c.STORE.RootDirectory.sync
        in_sync = False

        def syncing(store, *args, **kwargs):
            nonlocal in_sync
            in_sync = True
            try:
                return sync(store, *args, **kwargs)
            finally:
                in_sync = False

        for target in (self.head, self.head.parent):
            def fail_existing(fd):
                if in_sync and os.readlink('/proc/self/fd/' + str(fd)) == str(target):
                    raise OSError('head repeat sync failed')
                return fsync(fd)

            with (mock.patch.object(c.STORE.RootDirectory, 'sync', syncing),
                  mock.patch.object(c.STORE.os, 'fsync', side_effect=fail_existing)):
                failed = self.failure(self.enroll)
            self.assertTrue(failed['head_sync_attempted'])
            self.assertFalse(failed['registry_attempted'])
            self.assertFalse(self.registry.exists())
            self.assertTrue(self.head.exists())
        result = self.enroll()
        self.assertTrue(result['success'])
        self.assertTrue(result['head_sync_attempted'])
        self.assertFalse(result['head_attempted'])
        self.assertTrue(result['registry_attempted'])
        self.assertTrue(result['synchronizations'][0]['durable'])

    def test_existing_head_and_registry_syncs_keep_required_lock_order(self):
        self.enroll()
        self.events.clear()
        sync = c.STORE.RootDirectory.sync

        def observe(store, name, **kwargs):
            self.events.append('sync-' + name)
            return sync(store, name, **kwargs)

        with mock.patch.object(c.STORE.RootDirectory, 'sync', observe):
            self.enroll()
        self.assertLess(self.events.index('guest-lock'), self.events.index('sync-head.json'))
        self.assertLess(self.events.index('sync-head.json'), self.events.index('registry-lock'))
        self.assertLess(self.events.index('registry-lock'), self.events.index('sync-autostart.json'))
        self.assertLess(self.events.index('sync-autostart.json'), self.events.index('registry-unlock'))
        self.assertLess(self.events.index('registry-unlock'), self.events.index('guest-unlock'))

    def test_existing_head_authority_failure_cannot_complete_enrollment(self):
        self.enroll()
        before = self.registry.read_bytes()
        self.allow = False
        failed = self.failure(self.enroll, 'context storage changed')
        self.assertFalse(failed['registry_sync_attempted'])
        self.assertEqual(before, self.registry.read_bytes())

    def test_status_does_not_hash_disk_or_publish(self):
        self.enroll()
        before = self.head.read_bytes(), self.registry.read_bytes()
        with (mock.patch.object(c.POLICY, 'Inspection', side_effect=AssertionError('inspection')),
              mock.patch.object(c.STORE.RootDirectory, 'publish', side_effect=AssertionError('publish'))):
            self.controller.status()
        self.assertEqual(before, (self.head.read_bytes(), self.registry.read_bytes()))


if __name__ == '__main__':
    unittest.main()
