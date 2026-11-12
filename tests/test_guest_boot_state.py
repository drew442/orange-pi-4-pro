"""Native readonly flocks/protected stores; physical placement/PID1 are fixtures.

No fixture establishes physical boot, disk cleanliness or actual KVM acceptance.
"""
import copy
import fcntl
import importlib.util
import os
from pathlib import Path
import tempfile
import threading
import unittest
import uuid
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('boot_state_tests', BASE / 'scripts/guest-boot-state.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
A, L = M.ADMISSION, M.LIFECYCLE
STORAGE = dict(state_uuid='22222222-2222-4222-8222-222222222222',
               guest_uuid='33333333-3333-4333-8333-333333333333', nvme_serial='SSD-123')


class Boundary(unittest.TestCase):
    def test_fake_owner(self):
        with self.assertRaisesRegex(M.PreparationError, 'canonical bootstrap owner'):
            M.prepare(object(), STORAGE)


@unittest.skipUnless(os.getresuid() == (0, 0, 0), 'native protected tmpfs fixture requires root')
class Native(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir='/run', prefix='a733-boot-state-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.state, self.guests = self.base / 'state', self.base / 'guests'
        self.state.mkdir(mode=0o700)
        self.guests.mkdir(mode=0o711)
        self.boot = str(uuid.uuid4())
        self.closed = 0
        self.bad_context = self.bad_cleanup = False
        fixture = self

        class PhysicalStateFixture:
            def __init__(self, expected_storage, lease, *, boot_id):
                fixture.assertEqual(expected_storage, STORAGE)
                fixture.assertEqual(boot_id, fixture.boot)
                self.storage = copy.deepcopy(expected_storage)
                self.lease = lease
                self.state_fd = os.open(fixture.state, os.O_RDONLY | os.O_DIRECTORY)
                self.guest_fd = os.open(fixture.guests, os.O_RDONLY | os.O_DIRECTORY)
            def guard(self):
                M.require(not fixture.bad_context, 'physical placement changed')
                self.lease.recheck()
            def close(self):
                fixture.closed += 1
                os.close(self.state_fd)
                os.close(self.guest_fd)
                if fixture.bad_cleanup:
                    raise OSError('cleanup failed')

        for substitute in (patch.object(A, 'RUNTIME_PARENT', self.tmp.name),
                           patch.object(A, 'RUNTIME', str(self.base / 'a733-admission')),
                           patch.object(M.CONTEXT, 'HostContext', PhysicalStateFixture)):
            substitute.start()
            self.addCleanup(substitute.stop)
        self.admission = A.initialize(self.boot, 108, lambda: 'running')
        self.addCleanup(self.admission.close)
        self.owner = self.admission.bootstrap_owner
        self.registry_path = self.state / 'guests/autostart.json'
        self.lifecycle = self.guests / '.a733-lifecycle'

    def gate_closed(self):
        self.assertEqual(self.admission._read()['state'], 'closed')
        self.assertIs(self.admission.bootstrap_owner, self.owner)
        self.owner.lease.recheck()

    def prepare(self):
        value = M.prepare(self.owner, STORAGE)
        self.gate_closed()
        return value

    def registry(self, names=(), generation=0, storage=None):
        parent = self.registry_path.parent
        parent.mkdir(mode=0o700, exist_ok=True)
        guests = [dict(name=name, uid=60001+i, gid=60001+i, manifest_sha256='a'*64,
                       enabled=i % 2 == 0, shutdown_profile=M.REGISTRY.SHUTDOWN_PROFILE,
                       fixture_profile='none') for i, name in enumerate(names)]
        raw = M.REGISTRY.canonical(dict(schema=1, profile=M.REGISTRY.PROFILE,
            generation=generation, storage=storage or STORAGE, guests=guests))
        self.registry_path.write_bytes(raw)
        self.registry_path.chmod(0o400)
        return raw

    def head(self, name='one', valid=True):
        self.lifecycle.mkdir(mode=0o700, exist_ok=True)
        child = self.lifecycle / name
        child.mkdir(mode=0o700)
        raw = M.HEAD.canonical(M.HEAD.initial_head(STORAGE,
            dict(name=name, uid=60001, gid=60001), 'a'*64)) if valid else b'invalid\n'
        (child / 'head.json').write_bytes(raw)
        (child / 'head.json').chmod(0o400)
        return raw

    def marked(self, name):
        with self.admission.guest_lock(name, self.owner.lease) as lock:
            return self.admission.is_managed(name, self.owner.lease, lock)

    def assert_failure(self, message):
        with self.assertRaisesRegex(M.PreparationError, message) as error:
            self.prepare()
        self.gate_closed()
        self.assertFalse(error.exception.receipt['success'])
        return error.exception

    def test_empty_durable_idempotent_detached(self):
        first = self.prepare()
        raw = self.registry_path.read_bytes()
        inode = self.registry_path.stat().st_ino
        self.assertEqual(first['registry']['generation'], 0)
        self.assertEqual(first['marked_names'], [])
        self.assertFalse(first['hardware_acceptance'])
        self.assertEqual(self.registry_path.stat().st_mode & 0o777, 0o400)
        first['registry']['storage']['nvme_serial'] = 'changed'
        second = self.prepare()
        self.assertEqual(second['registry_raw'], raw)
        self.assertEqual(self.registry_path.stat().st_ino, inode)
        self.assertTrue(second['synchronizations'][0]['durable'])
        self.assertEqual(self.closed, 2)
        self.assertFalse(self.lifecycle.exists())

    def test_existing_registry_head_preserved(self):
        raw = self.registry(['one', 'two'], 23)
        head = self.head()
        result = self.prepare()
        self.assertEqual(result['registry_raw'], raw)
        self.assertEqual(self.registry_path.read_bytes(), raw)
        self.assertEqual((self.lifecycle / 'one/head.json').read_bytes(), head)
        self.assertTrue(result['registry']['guests'][0]['enabled'])
        self.assertFalse(result['registry']['guests'][1]['enabled'])
        self.assertTrue(result['heads']['two']['unavailable'])
        self.assertFalse(result['heads']['one']['run_permission'])
        self.assertTrue(self.marked('one'))
        self.assertTrue(self.marked('two'))
        self.assertFalse((self.lifecycle / 'two').exists())

    def test_orphan_invalid_and_symlink_marked_without_repair(self):
        bad = self.head('bad', valid=False)
        self.head('orphan')
        outside = self.base / 'outside'
        outside.mkdir(mode=0o700)
        (self.lifecycle / 'link').symlink_to(outside, target_is_directory=True)
        result = self.prepare()
        self.assertEqual(result['orphan_names'], ['bad', 'link', 'orphan'])
        for name in result['orphan_names']:
            self.assertTrue(self.marked(name))
        self.assertTrue(result['heads']['bad']['unavailable'])
        self.assertTrue(result['heads']['link']['unavailable'])
        self.assertEqual((self.lifecycle / 'bad/head.json').read_bytes(), bad)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(result['registry']['guests'], [])

    def test_malformed_child_marked_without_following(self):
        self.lifecycle.mkdir(mode=0o700)
        child = self.lifecycle / 'one'
        child.write_bytes(b'not a directory')
        child.chmod(0o400)
        result = self.prepare()
        self.assertTrue(self.marked('one'))
        self.assertTrue(result['heads']['one']['unavailable'])
        self.assertEqual(child.read_bytes(), b'not a directory')

    def test_no_nested_lease_and_registry_lock_last(self):
        self.registry(['one'])
        self.head()
        events = []
        guest_lock = self.admission.guest_lock
        registry_lock = self.admission.registry_lock
        def guest(*args, **kwargs):
            events.append('guest')
            self.assertNotIn('registry', events)
            return guest_lock(*args, **kwargs)
        def registry(*args, **kwargs):
            events.append('registry')
            return registry_lock(*args, **kwargs)
        with patch.object(self.admission, 'guest_lock', guest), \
                patch.object(self.admission, 'registry_lock', registry), \
                patch.object(self.admission, 'admit', side_effect=AssertionError('nested SH')), \
                patch.object(self.owner, 'exclusive', side_effect=AssertionError('nested EX')):
            self.prepare()
        self.assertEqual(events, ['guest', 'registry'])

    def test_storage_mismatch_refused_before_markers(self):
        raw = self.registry(storage={**STORAGE, 'nvme_serial': 'different'})
        self.assert_failure('storage identity differs')
        self.assertEqual(self.registry_path.read_bytes(), raw)
        self.assertFalse((self.base / 'a733-admission/managed').exists())
        self.assertEqual(self.closed, 1)

    def test_noncanonical_registry_refused(self):
        self.registry()
        self.registry_path.chmod(0o600)
        self.registry_path.write_bytes(self.registry_path.read_bytes() + b'\n')
        self.registry_path.chmod(0o400)
        self.assert_failure('noncanonical registry')

    def test_missing_heads_marked(self):
        self.registry(['one'])
        result = self.prepare()
        self.assertTrue(self.marked('one'))
        self.assertTrue(result['heads']['one']['unavailable'])
        self.assertFalse(self.lifecycle.exists())

    def test_unsafe_parent_and_name_refused(self):
        self.lifecycle.mkdir(mode=0o755)
        self.assert_failure('unsafe directory permissions')
        self.assertFalse(self.registry_path.exists())
        self.lifecycle.chmod(0o700)
        (self.lifecycle / 'Invalid').mkdir(mode=0o700)
        self.assert_failure('invalid guest lock name')
        self.assertFalse(self.registry_path.exists())

    def test_symlink_parent_refused(self):
        self.lifecycle.symlink_to(self.state, target_is_directory=True)
        self.assert_failure('unsafe directory owner/type')

    def test_orphan_reserved_firstkvm_refused_without_marker(self):
        self.head('firstkvm')
        self.assert_failure('unexpected manual firstkvm')
        self.assertFalse((self.base / 'a733-admission/managed').exists())
        self.assertFalse(self.registry_path.exists())

    def test_disabled_registered_firstkvm_refused_before_all_markers(self):
        raw = self.registry(['another', 'firstkvm'], generation=19)
        value = M.REGISTRY.validate_registry(raw)
        self.assertFalse(value['guests'][1]['enabled'])
        self.assertTrue(value['guests'][0]['enabled'])
        guest = self.guests / 'firstkvm'
        guest.mkdir(mode=0o700)
        disk = guest / 'disk.raw'
        disk.write_bytes(b'manual guest disk must be preserved')
        original_disk = disk.read_bytes()
        original_owner, original_lease = self.owner, self.owner.lease
        with patch.object(self.admission, 'prepare_managed',
                          wraps=self.admission.prepare_managed) as prepare_markers, \
                patch.object(self.admission, 'mark_managed',
                             wraps=self.admission.mark_managed) as publish_marker:
            error = self.assert_failure('unexpected manual firstkvm')
        prepare_markers.assert_not_called()
        publish_marker.assert_not_called()
        self.assertEqual(error.receipt['marked_names'], [])
        self.assertFalse((self.base / 'a733-admission/managed').exists())
        self.assertEqual(self.registry_path.read_bytes(), raw)
        self.assertEqual(disk.read_bytes(), original_disk)
        self.assertIs(self.admission.bootstrap_owner, original_owner)
        self.assertIs(self.owner.lease, original_lease)
        A._held_lock(original_lease.fd, 'WRITE', os.getpid())
        self.gate_closed()
        self.assertEqual(self.closed, 1)

    def test_name_bound(self):
        self.head('one')
        with patch.object(M, 'NAME_LIMIT', 0):
            self.assert_failure('name limit')

    def test_lost_ack_retry_syncs_exact_published_bytes(self):
        original = M.STORE.RootDirectory.publish
        def lost_ack(store, name, raw, **kwargs):
            receipt = original(store, name, raw, **kwargs)
            raise M.STORE.PublicationError(receipt, OSError('lost ack'))
        with patch.object(M.STORE.RootDirectory, 'publish', lost_ack):
            error = self.assert_failure('lost ack')
        self.assertTrue(error.receipt['nested_error']['durable'])
        inode = self.registry_path.stat().st_ino
        raw = self.registry_path.read_bytes()
        result = self.prepare()
        self.assertEqual(result['registry_raw'], raw)
        self.assertEqual(self.registry_path.stat().st_ino, inode)
        self.assertTrue(result['synchronizations'][0]['durable'])

    def test_publication_receipt_survives_store_cleanup_failure(self):
        publish = M.STORE.RootDirectory.publish
        close = M.STORE.RootDirectory.close
        def lost_ack(store, *args, **kwargs):
            receipt = publish(store, *args, **kwargs)
            raise M.STORE.PublicationError(receipt, OSError('primary lost ack'))
        def failed_close(store):
            close(store)
            raise OSError('store cleanup failed')
        with patch.object(M.STORE.RootDirectory, 'publish', lost_ack), \
                patch.object(M.STORE.RootDirectory, 'close', failed_close):
            error = self.assert_failure('primary lost ack')
        self.assertTrue(error.receipt['nested_error']['durable'])
        self.assertTrue(error.receipt['cleanup_errors'])
        self.assertEqual(self.closed, 1)

    def test_marker_failure_retains_receipt_and_closed_gate(self):
        self.registry(['one'])
        with patch.object(self.admission, 'mark_managed',
                          side_effect=A.PublicationError('directory-fsync', True)):
            error = self.assert_failure('directory-fsync')
        self.assertTrue(error.receipt['marker_error']['uncertain'])
        self.assertEqual(self.closed, 1)

    def test_cleanup_failure_is_failure_and_closed_once(self):
        self.bad_cleanup = True
        error = self.assert_failure('cleanup failed')
        self.assertEqual(len(error.receipt['cleanup_errors']), 1)
        self.assertEqual(self.closed, 1)

    def test_primary_failure_survives_cleanup_failure(self):
        self.registry(storage={**STORAGE, 'nvme_serial': 'different'})
        self.bad_cleanup = True
        error = self.assert_failure('storage identity differs')
        self.assertEqual(len(error.receipt['cleanup_errors']), 1)
        self.assertEqual(self.closed, 1)

    def test_open_gate_refuses(self):
        self.owner.open()
        with self.assertRaisesRegex(M.PreparationError, 'closure ownership changed'):
            M.prepare(self.owner, STORAGE)
        self.assertEqual(self.closed, 0)

    def test_maintenance_or_fake_owner_refuses(self):
        foreign = A.ClosureOwner(self.admission, self.admission._read())
        with self.assertRaisesRegex(M.PreparationError, 'original bootstrap owner'):
            M.prepare(foreign, STORAGE)
        self.owner.reason = 'maintenance'
        self.assert_failure('bootstrap ownership differs')

    def test_shutdown_and_shared_lease_refuse(self):
        self.owner.reason = 'shutdown'
        self.assert_failure('bootstrap ownership differs')
        self.owner.reason = 'bootstrap'
        self.owner.lease.exclusive = False
        self.assert_failure('original exclusive bootstrap lease')
        self.owner.lease.exclusive = True

    def test_unacceptable_pid1_and_changed_nonce_refuse(self):
        self.admission.manager_state = lambda: 'stopping'
        self.assert_failure('PID1 not accepting bootstrap')
        self.admission.manager_state = lambda: 'running'
        self.owner.nonce = str(uuid.uuid4())
        self.assert_failure('closure ownership changed')

    def test_current_source_identity_rechecked(self):
        with patch.object(A, '_a733_source_sha256', '0' * 64):
            self.assert_failure('admission module origin or source revision differs')
        self.assertEqual(self.closed, 0)

    def test_lost_lease_and_wrong_pid_refuse(self):
        fcntl.flock(self.owner.lease.fd, fcntl.LOCK_UN)
        self.assert_failure('missing or invalid inherited flock')
        fcntl.flock(self.owner.lease.fd, fcntl.LOCK_EX)
        with patch.object(M.os, 'getpid', return_value=os.getpid() + 10000):
            self.assert_failure('flock identity or mode differs')

    def test_cross_thread_callback_refuses(self):
        errors = []
        # Trigger the retained source guard from another thread via a store.
        publish = M.STORE.RootDirectory.publish
        def cross_thread(store, *args, **kwargs):
            def invoke():
                try:
                    store.guard()
                except BaseException as error:
                    errors.append(error)
            thread = threading.Thread(target=invoke)
            thread.start()
            thread.join()
            return publish(store, *args, **kwargs)
        with patch.object(M.STORE.RootDirectory, 'publish', cross_thread):
            self.prepare()
        self.assertEqual(len(errors), 1)
        self.assertIn('another process or thread', str(errors[0]))

    def test_guard_lost_physical_placement_refuses(self):
        original = self.admission.prepare_managed
        def invalidate(lease):
            original(lease)
            self.bad_context = True
        with patch.object(self.admission, 'prepare_managed', invalidate):
            self.assert_failure('physical placement changed')
        self.assertFalse(self.registry_path.exists())


if __name__ == '__main__':
    unittest.main()
