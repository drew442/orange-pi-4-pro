"""Real root admission around legacy restore; no board or KVM acceptance."""
import fcntl
import os
from pathlib import Path
import unittest
import uuid
from unittest.mock import patch

from test_guest_restore import load

F = load('restore_admission_files', 'tests/test_guest_restore.py')
R = F.R


class ExplicitName(unittest.TestCase):
    def test_missing_and_invalid_name_refuse_before_review(self):
        for name in (None, '', '../one', 'One', True):
            with self.subTest(name=name), patch.object(R, 'Review') as review:
                with self.assertRaises(R.operation_module().ADMISSION.AdmissionError):
                    R.prepare('/absent', 'a' * 64, 'b' * 64, F.UUID, name=name)
                receipt = R.apply('/absent', 'a' * 64, 'b' * 64, F.UUID, '/plan', name=name)
                self.assertFalse(receipt['success'])
                self.assertFalse(receipt['directory_may_be_published'])
                review.assert_not_called()

    def test_cli_requires_explicit_name(self):
        with self.assertRaises(SystemExit) as result:
            R.main(['prepare', '--archive', '/absent', '--archive-sha256', 'a' * 64,
                    '--manifest-sha256', 'b' * 64, '--guest-uuid', F.UUID])
        self.assertEqual(result.exception.code, 2)


@unittest.skipUnless(os.getresuid() == (0, 0, 0), 'real protected RAM locks require root')
class RestoreAdmission(unittest.TestCase):
    def setUp(self):
        self.files = F.RestoreFiles()
        self.files.setUp()
        self.addCleanup(self.files.doCleanups)
        # Use a separate real helper after the file-engine fixture mocks its own
        # outer boundary. Canonical admission still supplies the actual locks.
        self.operation = load('restore_real_operation', 'scripts/guest-operation-admission.py')
        self.admission_module = self.operation.ADMISSION
        self.boot = str(uuid.uuid4())
        self.runtime = self.files.base / 'a733-admission'
        for item in (patch.object(R, 'OPERATION', self.operation),
                     patch.object(self.admission_module, 'RUNTIME_PARENT', str(self.files.base)),
                     patch.object(self.admission_module, 'RUNTIME', str(self.runtime)),
                     patch.object(self.operation.CONTEXT, 'read_system',
                                  return_value=(self.boot + '\n').encode()),
                     patch.object(self.operation.CONTEXT, 'host_identity'),
                     patch.object(self.operation.CONTEXT.MANAGEMENT, 'runtime_guard'),
                     patch.object(self.operation.ACCOUNTS, 'snapshot',
                                  return_value={'group': b'root:x:0:\nkvm:x:108:\n'}),
                     patch.object(self.operation, 'manager_state', return_value='running')):
            item.start()
            self.addCleanup(item.stop)
        self.admission = self.admission_module.initialize(self.boot, 108, lambda: 'running')
        self.addCleanup(self.admission.close)
        owner = self.admission.bootstrap_owner
        self.admission.prepare_managed(owner.lease)
        owner.open()
        owner.lease.close()

    def assert_held(self):
        active = self.operation._ACTIVE.operation
        self.assertIsNotNone(active)
        self.assertEqual(active.name, 'one')
        active.recheck()
        with self.admission.admit() as lease:
            with self.assertRaises(self.admission_module.AdmissionError):
                self.admission.guest_lock('one', lease)
        return active

    def assert_released(self):
        self.assertIsNone(getattr(self.operation._ACTIVE, 'operation', None))
        with self.admission.admit() as lease, self.admission.guest_lock('one', lease, create=True):
            pass
        if Path(R.ENROLL.LOCK).exists():
            with open(R.ENROLL.LOCK, 'rb') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_prepare_admits_before_source_and_releases_after_source_cleanup(self):
        events = []
        source_init, source_close = R.SOURCE.Source.__init__, R.SOURCE.Source.close
        def opening(source, *args):
            self.assert_held()
            events.append('source')
            source_init(source, *args)
        def closing(source):
            self.assert_held()
            events.append('cleanup')
            source_close(source)
        with patch.object(R.SOURCE.Source, '__init__', opening), \
                patch.object(R.SOURCE.Source, 'close', closing):
            self.files.plan()
        self.assertEqual(events, ['source', 'cleanup'])
        self.assert_released()

    def test_apply_holds_admission_before_subsystem_and_through_cleanup(self):
        self.files.plan()
        events = []
        native_open, source_close = os.open, R.SOURCE.Source.close
        def opening(path, *args, **kwargs):
            if path == R.ENROLL.LOCK:
                self.assert_held()
                events.append('subsystem')
            return native_open(path, *args, **kwargs)
        def closing(source):
            self.assert_held()
            with open(R.ENROLL.LOCK, 'rb') as lock:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            events.append('cleanup')
            source_close(source)
        with patch.object(R.os, 'open', opening), patch.object(R.SOURCE.Source, 'close', closing):
            result = self.files.apply()
        self.assertTrue(result['success'], result)
        self.assertEqual(events, ['subsystem', 'cleanup'])
        self.assert_released()

    def test_managed_name_refused_before_source_or_subsystem(self):
        with self.admission.admit() as lease, \
                self.admission.guest_lock('one', lease, create=True) as guest:
            self.admission.mark_managed('one', lease, guest, guard=lambda: None)
        with patch.object(R.SOURCE, 'Source') as source:
            with self.assertRaisesRegex(self.admission_module.AdmissionError, 'managed guest'):
                self.files.plan()
            result = self.files.apply()
        source.assert_not_called()
        self.assertFalse(result['success'])
        self.assertFalse(Path(R.ENROLL.LOCK).exists())
        self.assert_released()

    def test_managed_remote_name_refused_before_review_or_network(self):
        with self.admission.admit() as lease, \
                self.admission.guest_lock('one', lease, create=True) as guest:
            self.admission.mark_managed('one', lease, guest, guard=lambda: None)
        with patch.object(R, 'Review') as review:
            with self.assertRaises(self.admission_module.AdmissionError):
                R.prepare(None, 'a' * 64, 'b' * 64, F.UUID, name='one',
                          archive_url='https://example.test/archive.gz')
            result = R.apply(None, 'a' * 64, 'b' * 64, F.UUID, '/plan', name='one',
                             archive_url='https://example.test/archive.gz')
        review.assert_not_called()
        self.assertFalse(result['success'])
        self.assertFalse(Path(R.ENROLL.LOCK).exists())
        self.assert_released()

    def test_marker_after_authorization_refused_before_stage(self):
        self.files.plan()
        def authorize(plan):
            active = self.assert_held()
            active.admission.mark_managed('one', active.lease, active.guest_lock,
                                          guard=lambda: None)
        with patch.object(R, 'authorize', authorize):
            result = self.files.apply()
        self.assertFalse(result['success'])
        self.assertFalse((self.files.root / '.enrollment').exists())
        self.assert_released()

    def test_wrong_archive_name_refused_without_staging(self):
        with self.assertRaisesRegex(ValueError, 'source name differs'):
            R.prepare(*self.files.args(), name='two')
        result = R.apply(*self.files.args(), str(self.files.plan_path), name='two')
        self.assertFalse(result['success'])
        self.assertFalse((self.files.root / '.enrollment').exists())
        self.assertFalse((self.files.root / 'one').exists())
        self.assert_released()

    def test_name_changed_after_authorization_refused_before_stage(self):
        self.files.plan()
        def authorize(plan):
            plan['config']['name'] = 'two'
        with patch.object(R, 'authorize', authorize):
            result = self.files.apply()
        self.assertFalse(result['success'])
        self.assertFalse((self.files.root / '.enrollment').exists())
        self.assert_released()

    def test_conflicting_guest_fails_before_source_other_guest_independent(self):
        with self.admission.admit() as lease, \
                self.admission.guest_lock('one', lease, create=True):
            with patch.object(R.SOURCE, 'Source') as source:
                with self.assertRaises(self.admission_module.AdmissionError):
                    self.files.plan()
                result = self.files.apply()
            source.assert_not_called()
            self.assertFalse(result['success'])
            self.assertFalse(Path(R.ENROLL.LOCK).exists())
        with self.admission.admit() as lease, \
                self.admission.guest_lock('two', lease, create=True):
            self.files.plan()
        self.assert_released()

    def test_admitted_restore_finishes_after_gate_closes(self):
        self.files.plan()
        def authorize(plan):
            self.assert_held()
            self.closure = self.admission.close_gate('shutdown')
        with patch.object(R, 'authorize', authorize):
            result = self.files.apply()
        self.assertTrue(result['success'], result)
        self.assertIsNone(getattr(self.operation._ACTIVE, 'operation', None))
        with self.closure.exclusive():
            pass
        with patch.object(R.SOURCE, 'Source') as source:
            with self.assertRaisesRegex(self.admission_module.AdmissionError, 'admission closed'):
                self.files.plan()
        source.assert_not_called()

    def test_copy_failure_cleans_stage_before_admission_release(self):
        self.files.plan()
        scan = R.SOURCE.Source.scan
        def failing(source, emit=None):
            if emit is None:
                return scan(source, emit)
            self.assert_held()
            emit('Image', b'bad')
            raise ValueError('copy interrupted')
        close = R.SOURCE.Source.close
        def closing(source):
            self.assert_held()
            self.assertEqual(list((self.files.root / '.enrollment').iterdir()), [])
            close(source)
        with patch.object(R.SOURCE.Source, 'scan', failing), \
                patch.object(R.SOURCE.Source, 'close', closing):
            result = self.files.apply()
        self.assertFalse(result['success'])
        self.assertFalse(result['staging_retained'])
        self.assertFalse(result['published'])
        self.assert_released()

    def test_source_close_failure_still_releases_admission_and_subsystem(self):
        self.files.plan()
        close = R.SOURCE.Source.close
        def failing(source):
            self.assert_held()
            close(source)
            raise OSError('source close acknowledgement failed')
        with patch.object(R.SOURCE.Source, 'close', failing):
            result = self.files.apply()
        self.assertFalse(result['success'])
        self.assertEqual(result['cleanup_error'], 'OSError')
        self.assertTrue(result['published'])
        self.assert_released()

    def test_owner_close_failure_is_conservative_after_all_other_cleanup(self):
        self.files.plan()
        close = self.operation.ManualGuestOperation.close
        def failing(owner):
            with open(R.ENROLL.LOCK, 'rb') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            close(owner)
            raise OSError('operation close acknowledgement failed')
        with patch.object(self.operation.ManualGuestOperation, 'close', failing):
            result = self.files.apply()
        self.assertFalse(result['success'])
        self.assertTrue(result['published'])
        self.assertEqual(result['cleanup_error'], 'OSError')
        self.assert_released()


if __name__ == '__main__':
    unittest.main()
