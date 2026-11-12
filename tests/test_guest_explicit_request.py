"""Actual root RAM request publication/flocks; hardware/PID1/inspection substituted."""
import copy
from contextlib import ExitStack
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('tested_explicit', ROOT / 'scripts/guest-explicit-request.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
A = M.ADMISSION
BOOT = '33333333-3333-4333-8333-333333333333'
STORAGE = dict(state_uuid='11111111-1111-4111-8111-111111111111',
               guest_uuid='22222222-2222-4222-8222-222222222222', nvme_serial='NVME-123')
CONFIG = dict(schema=1, name='one', uid=60001, gid=60001, memory_mib=128,
              memory_max_mib=256, cpus=2, kernel=dict(bytes=100, sha256='a'*64),
              initrd=dict(bytes=200, sha256='b'*64), disk=dict(bytes=1024, sha256='c'*64))


@unittest.skipUnless(os.geteuid() == 0, 'actual root RAM metadata required')
class NativeRequests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.base = Path(self.stack.enter_context(tempfile.TemporaryDirectory(dir='/run', prefix='explicit-test-')))
        self.base.chmod(0o755)
        self.prepared, self.units = self.base / 'boot', self.base / 'systemd'
        self.prepared.mkdir(mode=0o700)
        self.units.mkdir(mode=0o755)
        self.unit = self.units / 'a733-managed-one.service'
        self.unit.write_text(M.UNITS.render_managed(CONFIG))
        self.unit.chmod(0o444)
        for item in (patch.object(A, 'RUNTIME_PARENT', str(self.base)),
                     patch.object(A, 'RUNTIME', str(self.base / 'a733-admission')),
                     patch.object(A, '_boot_id', return_value=BOOT),
                     patch.object(M.PREP, 'UNIT_DIRECTORY', str(self.units)),
                     patch.object(M.PREP, 'PREPARED_DIRECTORY', str(self.prepared)),
                     patch.object(M.PREP, 'installed_guard')):
            self.stack.enter_context(item)
        self.admission = A.initialize(BOOT, 1, lambda: 'running')
        self.stack.callback(self.admission.close)
        owner = self.admission.bootstrap_owner
        self.admission.prepare_managed(owner.lease)
        with self.admission.guest_lock('one', owner.lease, create=True) as lock:
            self.admission.mark_managed('one', owner.lease, lock, guard=lambda: None)
        owner.open()
        owner.lease.close()
        self.lease = self.stack.enter_context(self.admission.admit())
        self.lock = self.stack.enter_context(self.admission.guest_lock('one', self.lease))
        self.entry = dict(name='one', uid=60001, gid=60001, manifest_sha256=M.digest(b'manifest'), enabled=True)
        self.head = M.HEAD.canonical(M.HEAD.initial_head(STORAGE,
            {key: CONFIG[key] for key in ('name', 'uid', 'gid')}, self.entry['manifest_sha256']))
        self.inspection = SimpleNamespace(recheck=lambda: None, manifest_data=b'manifest',
            manifest_record=dict(sha256=self.entry['manifest_sha256']), disk_record=CONFIG['disk'],
            manifest=dict(config=copy.deepcopy(CONFIG), guest_uuid=STORAGE['guest_uuid']))
        self.context = SimpleNamespace(lease=self.lease, storage=STORAGE, guard=self.lease.recheck)
        self.head_dir = self.base / 'head'
        self.head_dir.mkdir(mode=0o700)
        (self.head_dir / 'head.json').write_bytes(self.head)
        (self.head_dir / 'head.json').chmod(0o400)
        parent = os.open(self.base, os.O_RDONLY | os.O_DIRECTORY)
        self.stack.callback(os.close, parent)
        self.controller = SimpleNamespace(context=self.context, lease=self.lease,
            admission=self.admission, boot_id=BOOT,
            _head_store=lambda stack, name, guard: stack.enter_context(
                M.STORE.RootDirectory(parent, 'head', guard=guard)),
            _registry=lambda stack, guard: (None, None, dict(guests=[self.entry])))

    @property
    def pending(self):
        return self.prepared / 'explicit/pending-one.json'

    def issue(self):
        return M.issue(self.controller, 'one', self.entry['manifest_sha256'], self.head,
            M.digest(self.unit.read_bytes()), self.lock, self.lease.recheck,
            inspection=self.inspection, registry_entry=self.entry)

    def consume(self):
        return M.consume(self.controller, 'one', self.entry['manifest_sha256'], self.head,
                         self.lock, self.inspection, self.entry, self.lease.recheck)

    def test_absence_readonly_then_once_only_consume_and_no_requeue(self):
        self.assertIs(self.consume(), True)
        self.assertFalse((self.prepared / 'explicit').exists())
        issued = self.issue()
        self.assertTrue(issued['success'])
        self.assertEqual(self.pending.stat().st_mode & 0o777, 0o400)
        self.assertEqual(self.pending.parent.stat().st_mode & 0o777, 0o700)
        again = self.issue()
        self.assertTrue(again['no_op'])
        self.assertEqual(issued['request_id'], again['request_id'])
        self.assertIs(self.consume(), False)
        self.assertFalse(self.pending.exists())
        self.assertTrue((self.pending.parent / ('consumed-' + issued['request_id'] + '.json')).exists())
        with self.assertRaises(M.STORE.PublicationError):
            self.consume()
        with self.assertRaises(M.STORE.PublicationError):
            self.issue()

    def test_pending_binding_mismatches_and_malformed_bytes_never_fallback(self):
        self.issue()
        original = self.pending.read_bytes()
        changes = dict(boot_id='44444444-4444-4444-8444-444444444444',
            unit_sha256='f'*64, ready_head_sha256='e'*64, manifest_sha256='d'*64)
        for key, change in changes.items():
            value = json.loads(original)
            value[key] = change
            self.pending.write_bytes(M.encoded(value))
            with self.subTest(key=key), self.assertRaises(M.STORE.PublicationError):
                self.consume()
            self.assertTrue(self.pending.exists())
        for raw in (b'{}', original + b' ', original.replace(b'"schema":1', b'"schema":true')):
            self.pending.write_bytes(raw)
            with self.assertRaises(M.STORE.PublicationError):
                self.consume()

    def assert_malformed_and_close_errors(self, path):
        path.write_bytes(b'{}')
        original_close = M.PREP.Source.close
        def failed_close(source):
            original_close(source)
            if source.name == path.name:
                raise OSError('request source close failed')
        with patch.object(M.PREP.Source, 'close', failed_close):
            with self.assertRaises(M.STORE.PublicationError) as caught:
                self.issue()
        detail = caught.exception.receipt['nested_error']
        self.assertIn('invalid explicit request fields', detail['primary_error']['error'])
        self.assertIn('request source close failed', detail['cleanup_error']['error'])

    def test_pending_validation_and_close_failures_both_survive(self):
        self.issue()
        self.assert_malformed_and_close_errors(self.pending)

    def test_consumed_validation_and_close_failures_both_survive(self):
        issued = self.issue()
        self.consume()
        self.assert_malformed_and_close_errors(
            self.pending.parent / ('consumed-' + issued['request_id'] + '.json'))

    def test_unit_replacement_and_symlink_directory_refused(self):
        self.issue()
        self.unit.write_bytes(b'changed')
        with self.assertRaises(M.STORE.PublicationError):
            self.consume()
        self.unit.write_text(M.UNITS.render_managed(CONFIG))
        self.pending.unlink()
        self.pending.parent.rmdir()
        self.pending.parent.symlink_to(self.units, target_is_directory=True)
        with self.assertRaises(M.STORE.PublicationError):
            self.consume()

    def test_requires_actual_shared_and_exclusive_readonly_flocks(self):
        fcntl.flock(self.lock.fd, fcntl.LOCK_UN)
        with self.assertRaises(M.STORE.PublicationError):
            self.issue()

    def test_stale_head_and_pending_duplicate_mismatch_refused(self):
        self.issue()
        value = json.loads(self.pending.read_bytes())
        value['unit_sha256'] = 'f'*64
        self.pending.write_bytes(M.encoded(value))
        with self.assertRaises(M.STORE.PublicationError):
            self.issue()
        (self.head_dir / 'head.json').write_bytes(self.head + b' ')
        with self.assertRaises(M.STORE.PublicationError):
            self.issue()

    def test_rename_uncertainty_retains_pending_and_consumed_fsync_failure(self):
        self.issue()
        with patch.object(M.CONTEXT.ENROLL, 'rename_new', side_effect=OSError('rename')):
            with self.assertRaises(M.STORE.PublicationError) as caught:
                self.consume()
        self.assertTrue(caught.exception.receipt['uncertain'])
        self.assertTrue(self.pending.exists())
        original = os.fsync
        def fail(fd):
            if not self.pending.exists():
                raise OSError('parent fsync')
            original(fd)
        with patch.object(M.os, 'fsync', side_effect=fail):
            with self.assertRaises(M.STORE.PublicationError) as caught:
                self.consume()
        self.assertTrue(caught.exception.receipt['consumed'])
        self.assertTrue(caught.exception.receipt['uncertain'])
        self.assertFalse(self.pending.exists())
        with self.assertRaises(M.STORE.PublicationError):
            self.issue()

    def test_invalid_metadata_and_pending_symlink_fail_closed(self):
        self.issue()
        self.pending.chmod(0o600)
        with self.assertRaises(M.STORE.PublicationError):
            self.consume()
        self.pending.chmod(0o400)
        data = self.pending.read_bytes()
        self.pending.unlink()
        target = self.base / 'untrusted.json'
        target.write_bytes(data)
        target.chmod(0o400)
        self.pending.symlink_to(target)
        with self.assertRaises(M.STORE.PublicationError):
            self.consume()
        self.pending.unlink()
        self.pending.parent.chmod(0o755)
        with self.assertRaises(M.STORE.PublicationError):
            self.consume()

    def test_consumed_target_collision_preserves_both_records(self):
        issued = self.issue()
        target = self.pending.parent / ('consumed-' + issued['request_id'] + '.json')
        target.write_bytes(b'previous')
        target.chmod(0o400)
        with self.assertRaises(M.STORE.PublicationError) as caught:
            self.consume()
        self.assertTrue(caught.exception.receipt['uncertain'])
        self.assertTrue(self.pending.exists())
        self.assertEqual(target.read_bytes(), b'previous')

    def test_consumed_source_cleanup_failure_retains_uncertainty_and_no_requeue(self):
        self.issue()
        original = M.PREP.Source.close
        def fail(source):
            original(source)
            if source.name.startswith('consumed-'):
                raise OSError('consumed source close failed')
        with patch.object(M.PREP.Source, 'close', fail):
            with self.assertRaises(M.STORE.PublicationError) as caught:
                self.consume()
        self.assertTrue(caught.exception.receipt['consumed'])
        self.assertTrue(caught.exception.receipt['uncertain'])
        self.assertIn('nested_error', caught.exception.receipt)
        self.assertFalse(self.pending.exists())
        with self.assertRaises(M.STORE.PublicationError):
            self.issue()

    def test_guard_failure_after_cleanup_refuses_live_admission(self):
        self.issue()
        original = M.PREP.RamDirectory.close
        invalid = [False]
        def close(directory):
            original(directory)
            if directory.guard is not None:
                invalid[0] = True
        def guard():
            self.lease.recheck()
            M.require(not invalid[0], 'context changed during cleanup')
        # bindings closes its unit directory before request rename: no consume
        # occurs when that cleanup invalidates placement.
        with patch.object(M.PREP.RamDirectory, 'close', close):
            with self.assertRaises(M.STORE.PublicationError):
                M.consume(self.controller, 'one', self.entry['manifest_sha256'], self.head,
                          self.lock, self.inspection, self.entry, guard)
        self.assertTrue(self.pending.exists())

    def test_creator_thread_and_inherited_flock_owner_refused(self):
        import threading
        errors = []
        def issue_elsewhere():
            try:
                self.issue()
            except BaseException as error:
                errors.append(error)
        thread = threading.Thread(target=issue_elsewhere)
        thread.start()
        thread.join()
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], M.STORE.PublicationError)
        pid = os.fork()
        if pid == 0:
            try:
                self.issue()
            except BaseException:
                os._exit(0)
            os._exit(1)
        _, status = os.waitpid(pid, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 0)
        self.assertFalse(self.pending.exists())

    def test_wrong_unit_pin_registry_and_storage_are_readonly_rejections(self):
        with self.assertRaises(M.STORE.PublicationError):
            M.issue(self.controller, 'one', self.entry['manifest_sha256'], self.head,
                'a'*64, self.lock, self.lease.recheck,
                inspection=self.inspection, registry_entry=self.entry)
        self.entry['enabled'] = False
        with self.assertRaises(M.STORE.PublicationError):
            self.issue()
        self.entry['enabled'] = True
        self.context.storage = dict(STORAGE, nvme_serial='CHANGED')
        with self.assertRaises(M.STORE.PublicationError):
            self.issue()
        self.assertFalse((self.prepared / 'explicit').exists())

    def test_consumed_initial_head_blocks_automatic_fallback_after_lost_ack(self):
        self.issue()
        self.assertIs(self.consume(), False)
        self.assertIsNone(M.HEAD.validate_head(self.head)['last_boot_id'])
        with self.assertRaises(M.STORE.PublicationError):
            self.consume()
        # Once the durable ready head has advanced, the consumed receipt does
        # not invent a global automatic prohibition; HEAD.admit retains its
        # separate once-per-boot policy.
        value = M.HEAD.validate_head(self.head)
        value.update(sequence=5, previous_head_sha256=M.digest(self.head),
                     run_id='44444444-4444-4444-8444-444444444444',
                     last_boot_id=BOOT, last_completed_intent_sha256='f'*64)
        self.head = M.HEAD.canonical(value)
        self.assertIs(self.consume(), True)

    def test_absent_request_cleanup_failure_still_refuses_automatic(self):
        (self.prepared / 'explicit').mkdir(mode=0o700)
        original = M.STORE.RootDirectory.close
        def close(directory):
            original(directory)
            raise OSError('absent request directory close failed')
        with patch.object(M.STORE.RootDirectory, 'close', close):
            with self.assertRaises(M.STORE.PublicationError):
                self.consume()
