"""Native readonly tmpfs shutdown flocks; no board or deployment evidence."""
from contextlib import ExitStack, contextmanager
import fcntl
import importlib.util
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('shutdown_admission_test',
    Path(__file__).resolve().parents[1] / 'scripts/guest-admission.py')
A = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(A)


@unittest.skipUnless(os.getresuid() == (0, 0, 0), 'native protected RAM requires root')
class ShutdownLease(unittest.TestCase):
    def test_gate_exit_failure_does_not_leak_unreturned_exclusive_lease(self):
        self.admission.close_gate('shutdown')
        original = self.admission._gate
        @contextmanager
        def failed_exit():
            with original():
                yield
            raise OSError('gate close acknowledgement lost')
        with patch.object(self.admission, '_gate', failed_exit):
            with self.assertRaisesRegex(OSError, 'gate close acknowledgement'):
                self.admission.shutdown_lease()
        # A leaked readonly EX descriptor would refuse this independent claim.
        with self.admission.shutdown_lease() as lease:
            A._held_lock(lease.fd, 'WRITE', os.getpid())

    def test_gate_exit_and_lease_close_failures_retain_both_receipts(self):
        self.admission.close_gate('shutdown')
        original_gate, original_close = self.admission._gate, A.os.close
        closes = []
        @contextmanager
        def failed_exit():
            with original_gate():
                yield
            raise OSError('gate close acknowledgement lost')
        def failed_lease_close(fd):
            path = os.readlink('/proc/self/fd/' + str(fd))
            original_close(fd)
            if path.endswith('/leases.lock'):
                closes.append(fd)
                raise OSError('lease close acknowledgement lost')
        with patch.object(self.admission, '_gate', failed_exit), \
             patch.object(A.os, 'close', failed_lease_close):
            with self.assertRaises(A.AdmissionError) as caught:
                self.admission.shutdown_lease()
        self.assertIn('gate close acknowledgement', caught.exception.receipt['primary_error'])
        self.assertIn('lease close acknowledgement', caught.exception.receipt['close_error'])
        self.assertTrue(caught.exception.receipt['lease_cleanup_uncertain'])
        self.assertEqual(len(closes), 1)
        with self.admission.shutdown_lease():
            pass

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        parent = self.stack.enter_context(tempfile.TemporaryDirectory(dir='/run'))
        self.path = Path(parent) / 'a733-admission'
        self.stack.enter_context(patch.object(A, 'RUNTIME_PARENT', parent))
        self.stack.enter_context(patch.object(A, 'RUNTIME', str(self.path)))
        self.admission = A.initialize(A._boot_id(), 0, lambda: 'running')
        self.stack.callback(self.admission.close)
        self.admission.bootstrap_owner.open()
        self.admission.bootstrap_owner.lease.close()

    def test_shutdown_returns_actual_readonly_ex_not_owner(self):
        self.admission.close_gate('shutdown')
        record = self.admission._read()
        with self.admission.shutdown_lease() as lease:
            self.assertIs(type(lease), A.Lease)
            self.assertTrue(lease.exclusive)
            self.assertFalse(os.get_inheritable(lease.fd))
            self.assertEqual(fcntl.fcntl(lease.fd, fcntl.F_GETFL) & os.O_ACCMODE, os.O_RDONLY)
            A._held_lock(lease.fd, 'WRITE', os.getpid())
            with self.assertRaises(A.AdmissionError):
                A._held_lock(lease.fd, 'WRITE', os.getpid() + 1)
            self.assertEqual(self.admission._read(), record)
        self.assertEqual(self.admission._read(), record)
        self.assertIsNone(self.admission.close_gate('shutdown'))
        with self.admission.shutdown_lease():
            pass

    def test_open_refuses(self):
        with self.assertRaisesRegex(A.AdmissionError, 'closed shutdown'):
            self.admission.shutdown_lease()

    def test_maintenance_refuses(self):
        self.admission.close_gate()
        with self.assertRaisesRegex(A.AdmissionError, 'closed shutdown'):
            self.admission.shutdown_lease()

    def test_shared_holder_refuses_immediately_without_changing_closure(self):
        with self.admission.admit():
            self.admission.close_gate('shutdown')
            record = self.admission._read()
            with self.assertRaisesRegex(A.AdmissionError, 'lock timeout'):
                self.admission.shutdown_lease()
            self.assertEqual(self.admission._read(), record)

    def test_existing_ex_refuses(self):
        self.admission.close_gate('shutdown')
        with self.admission.shutdown_lease():
            with self.assertRaisesRegex(A.AdmissionError, 'lock timeout'):
                self.admission.shutdown_lease()

    def test_nonroot_refuses(self):
        self.admission.close_gate('shutdown')
        with patch.object(A.os, 'getresuid', return_value=(1000, 1000, 1000)):
            with self.assertRaisesRegex(A.AdmissionError, 'actual root'):
                self.admission.shutdown_lease()

    def test_worker_thread_refuses(self):
        errors = []
        def invoke():
            try:
                self.admission.shutdown_lease()
            except A.AdmissionError as error:
                errors.append(str(error))
        thread = threading.Thread(target=invoke)
        thread.start()
        thread.join()
        self.assertIn('main thread', errors[0])

    def test_foreign_boot_refuses(self):
        with patch.object(A, '_boot_id', return_value='11111111-1111-4111-8111-111111111111'):
            with self.assertRaisesRegex(A.AdmissionError, 'different host boot'):
                self.admission.shutdown_lease()

    def test_renamed_runtime_refuses(self):
        self.admission.close_gate('shutdown')
        self.path.rename(self.path.with_name('moved'))
        with self.assertRaises(OSError):
            self.admission.shutdown_lease()

    def test_wrong_lock_metadata_refuses(self):
        self.admission.close_gate('shutdown')
        (self.path / 'leases.lock').chmod(0o600)
        with self.assertRaisesRegex(A.AdmissionError, 'unsafe admission metadata'):
            self.admission.shutdown_lease()


if __name__ == '__main__':
    unittest.main()
