"""Native protected admission/flocks; board, PID1 and STATE are substituted.

No KVM, boot or physical deployment acceptance is established here.
"""
import importlib.util
import fcntl
import io
import json
import os
from pathlib import Path
import threading
import unittest
import uuid
from unittest.mock import patch


BASE = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, BASE / path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


R = load('reopen_tests', 'scripts/reopen-guest-admission.py')
M, A = R.M, R.A
F = load('reopen_native_fixture', 'tests/test_guest_host_maintenance.py')
# Reuse the fixture's real producers in exactly the recovery module's family.
F.M, F.A, F.L, F.P = M, A, M.LIFECYCLE, M.LIFECYCLE.POLICY


class Boundary(unittest.TestCase):
    def test_actual_root_precedes_discovery(self):
        with patch.object(M.os, 'getresuid', return_value=(1000, 1000, 1000)), \
                patch.object(M, 'registered_storage') as discover:
            with self.assertRaisesRegex(M.MaintenanceError, 'actual root'):
                R.ReopenMaintenance(str(uuid.uuid4()))
        discover.assert_not_called()

    def test_cli_requires_nonce_and_exposes_no_bypass(self):
        for arguments in ([], ['--expected-nonce', str(uuid.uuid4()), '--ram']):
            with patch('sys.stderr', new_callable=io.StringIO), self.assertRaises(SystemExit):
                R.main(arguments)
        with self.assertRaises(TypeError):
            R.ReopenMaintenance(str(uuid.uuid4()), expected_storage=F.STORAGE)

    def test_cli_reports_receipt(self):
        receipt = dict(success=True, hardware_acceptance=False)
        with patch.object(R, 'reopen', return_value=receipt) as reopen, \
                patch('sys.stdout', new_callable=io.StringIO) as output:
            nonce = str(uuid.uuid4())
            self.assertEqual(R.main(['--expected-nonce', nonce]), 0)
        reopen.assert_called_once_with(nonce)
        self.assertIn('"hardware_acceptance": false', output.getvalue())


@unittest.skipUnless(os.getresuid() == (0, 0, 0), 'protected native tmpfs fixture requires root')
class Native(unittest.TestCase):
    setUp = F.Installed.setUp
    gate = F.Installed.gate
    checkpoint_fixture = F.Installed.checkpoint_fixture

    def close_gate(self):
        owner = self.admission.close_gate('maintenance')
        self.assertIs(type(owner), A.ClosureOwner)
        return owner

    def operation(self, nonce):
        with patch.object(M, 'registered_storage', return_value=F.STORAGE):
            operation = R.ReopenMaintenance(nonce)
        self.addCleanup(operation.close)
        return operation

    def test_ready_reopens_after_entry_and_final_proofs(self):
        old = self.close_gate()
        operation = self.operation(old.nonce)
        A._held_lock(operation.lease.fd, 'WRITE', os.getpid())
        self.assertEqual(fcntl.fcntl(operation.lease.fd, fcntl.F_GETFL) & os.O_ACCMODE,
                         os.O_RDONLY)
        self.assertEqual(self.events, ['stop', 'census'])
        self.assertIs(type(operation.controller), M.LIFECYCLE.StateController)
        self.assertTrue(operation.finish_success()['open_acknowledged'])
        self.assertEqual(self.events, ['stop', 'census', 'census'])
        self.assertEqual(self.gate()['state'], 'open')
        with self.assertRaisesRegex(A.AdmissionError, 'ownership changed'):
            old.exclusive(timeout=0)

    def test_wrong_nonce_open_bootstrap_shutdown_refuse_before_stop(self):
        with self.assertRaises(M.MaintenanceError):
            self.operation(str(uuid.uuid4()))
        owner = self.close_gate()
        with self.assertRaises(M.MaintenanceError):
            self.operation(str(uuid.uuid4()))
        for reason in ('bootstrap', 'shutdown'):
            with self.admission._gate():
                self.admission._publish(dict(self.admission._read(), reason=reason))
            with self.assertRaises(M.MaintenanceError):
                self.operation(owner.nonce)
        self.assertEqual(self.events, [])

    def test_missing_runtime_never_initializes(self):
        with patch.object(A, 'RUNTIME', str(self.base / 'missing')), \
                patch.object(A, 'initialize', side_effect=AssertionError('must not initialize')):
            with self.assertRaises(M.MaintenanceError):
                self.operation(str(uuid.uuid4()))
        self.assertEqual(self.events, [])

    def test_installed_native_identity_and_pid1_required_before_stop(self):
        owner = self.close_gate()
        with patch.object(M.CONTEXT, 'host_identity', side_effect=OSError('foreign namespace')):
            with self.assertRaises(M.MaintenanceError):
                self.operation(owner.nonce)
        with patch.object(M.NORMAL, 'manager_state', return_value='stopping'):
            with self.assertRaises(M.MaintenanceError):
                self.operation(owner.nonce)
        self.assertEqual(self.events, [])
        self.assertEqual(self.gate()['state'], 'closed')

    def test_actual_shared_manual_lease_refuses_after_managed_stop(self):
        with self.admission.admit():
            owner = self.close_gate()
            with self.assertRaises(M.MaintenanceError) as caught:
                self.operation(owner.nonce)
        self.assertEqual(caught.exception.receipt['phase'], 'exclusive-lease')
        self.assertEqual(self.events, ['stop'])
        self.assertEqual(self.gate()['state'], 'closed')

    def test_live_exclusive_owner_cannot_be_taken_over(self):
        owner = self.close_gate()
        with owner.exclusive(timeout=0):
            with self.assertRaises(M.MaintenanceError) as caught:
                self.operation(owner.nonce)
        self.assertEqual(caught.exception.receipt['phase'], 'exclusive-lease')
        self.assertEqual(self.gate()['owner_nonce'], owner.nonce)

    def test_nonce_rechecked_after_stop_before_state_controller(self):
        owner = self.close_gate()
        def replace():
            with self.admission._gate():
                self.admission._publish(dict(self.admission._read(), owner_nonce=str(uuid.uuid4())))
        with patch.object(M, 'stop_managed', side_effect=replace), \
                patch.object(M.LIFECYCLE, 'StateController') as controller:
            with self.assertRaisesRegex(M.MaintenanceError, 'ownership changed'):
                self.operation(owner.nonce)
        controller.assert_not_called()

    def test_nonce_changed_during_exclusive_acquisition_refuses(self):
        owner = self.close_gate()
        native = A._lock
        lease_inode = os.fstat(self.admission.leases_fd).st_ino
        changed = []
        def race(fd, mode, timeout):
            native(fd, mode, timeout)
            if (not changed and mode == fcntl.LOCK_EX
                    and os.fstat(fd).st_ino == lease_inode):
                changed.append(True)
                with self.admission._gate():
                    self.admission._publish(dict(self.admission._read(), owner_nonce=str(uuid.uuid4())))
        with patch.object(A, '_lock', side_effect=race), \
                patch.object(M.LIFECYCLE, 'StateController') as controller:
            with self.assertRaisesRegex(M.MaintenanceError, 'ownership changed'):
                self.operation(owner.nonce)
        self.assertEqual(changed, [True])
        controller.assert_not_called()

    def test_stop_and_census_failures_leave_closed_release_fds(self):
        owner = self.close_gate()
        for method in ('stop_managed', 'consumer_check'):
            before = set(os.listdir('/proc/self/fd'))
            with patch.object(M, method, side_effect=OSError('proof failed')):
                with self.assertRaises(M.MaintenanceError):
                    self.operation(owner.nonce)
            self.assertEqual(set(os.listdir('/proc/self/fd')), before)
            self.assertEqual(self.gate()['state'], 'closed')
            with owner.exclusive(timeout=0):
                pass

    def test_unresolved_and_quarantine_refuse(self):
        with self.admission.admit() as lease, M.LIFECYCLE.StateController(
                F.STORAGE, lease, boot_id=self.boot) as controller:
            run = controller.admit_run('one', self.pin, str(uuid.uuid4()))
            run.close()
        owner = self.close_gate()
        with self.assertRaises(M.MaintenanceError):
            self.operation(owner.nonce)
        self.assertEqual(M.LIFECYCLE.HEAD.validate_head(self.head_path.read_bytes())['status'], 'quarantined')
        with patch.object(M.CP, 'reconcile') as reconcile:
            with self.assertRaises(M.MaintenanceError):
                self.operation(owner.nonce)
        reconcile.assert_not_called()
        self.assertEqual(self.gate()['state'], 'closed')

    def test_known_interrupted_checkpoint_reconciles_and_authenticates(self):
        self.checkpoint_fixture(interrupted=True)
        owner = self.close_gate()
        with patch.object(M.CP, 'reconcile', wraps=M.CP.reconcile) as reconcile, \
                patch.object(M.CP, 'authenticate_ready_cohort', wraps=M.CP.authenticate_ready_cohort) as ready:
            operation = self.operation(owner.nonce)
            operation.finish_success()
        reconcile.assert_called_once()
        self.assertEqual(ready.call_count, 2)
        self.assertEqual(self.gate()['state'], 'open')

    def test_final_proof_and_lost_ack_fail_conservatively(self):
        owner = self.close_gate()
        operation = self.operation(owner.nonce)
        with patch.object(M, 'consumer_check', side_effect=OSError('consumer remains')):
            with self.assertRaises(M.MaintenanceError):
                operation.finish_success()
        self.assertEqual(self.gate()['state'], 'closed')
        operation = self.operation(owner.nonce)
        native = operation.owner.open
        def lost_ack():
            native()
            raise OSError('lost acknowledgement')
        with patch.object(operation.owner, 'open', side_effect=lost_ack):
            with self.assertRaises(M.MaintenanceError) as caught:
                operation.finish_success()
        self.assertEqual(caught.exception.receipt['gate_state'], 'uncertain')
        self.assertFalse(caught.exception.receipt['success'])
        self.assertEqual(self.gate()['state'], 'open')

    def test_cleanup_errors_attempt_all_fds_once(self):
        operation = self.operation(self.close_gate().nonce)
        called = []
        for field in ('controller', 'lease', 'admission'):
            native = getattr(operation, field).close
            def fail(native=native, field=field):
                called.append(field)
                native()
                raise OSError('lost close acknowledgement')
            substitute = patch.object(getattr(operation, field), 'close', side_effect=fail)
            substitute.start()
            self.addCleanup(substitute.stop)
        with self.assertRaises(M.MaintenanceError) as caught:
            operation.close()
        self.assertEqual(called, ['controller', 'lease', 'admission'])
        self.assertEqual(len(caught.exception.receipt['close_errors']), 3)
        operation.close()
        self.assertEqual(len(called), 3)

    def test_cli_cleanup_failure_after_open_reports_failed_command(self):
        owner = self.close_gate()
        native = M.LIFECYCLE.StateController.close
        closed = []
        def fail(controller):
            native(controller)
            closed.append(True)
            raise OSError('lost close acknowledgement')
        with patch.object(M, 'registered_storage', return_value=F.STORAGE), \
                patch.object(M.LIFECYCLE.StateController, 'close', new=fail), \
                patch('sys.stderr', new_callable=io.StringIO) as output, \
                patch('sys.stdout', new_callable=io.StringIO) as stdout:
            status = R.main(['--expected-nonce', owner.nonce])
        receipt = json.loads(output.getvalue())
        self.assertEqual(status, 1)
        self.assertFalse(receipt['success'])
        self.assertTrue(receipt['open_acknowledged'])
        self.assertEqual(receipt['gate_state'], 'open')
        self.assertTrue(receipt['maintenance']['success'])
        self.assertTrue(receipt['maintenance']['open_acknowledged'])
        self.assertEqual(receipt['maintenance']['close_errors'][0]['resource'], 'controller')
        self.assertEqual(self.gate()['state'], 'open')
        self.assertEqual(closed, [True])
        self.assertEqual(stdout.getvalue(), '')
        with self.admission.admit():
            pass

    def test_cli_cleanup_failure_before_finish_reports_failed_closed_command(self):
        owner = self.close_gate()
        native = M.LIFECYCLE.StateController.close
        def fail(controller):
            native(controller)
            raise OSError('lost close acknowledgement')
        with patch.object(M, 'registered_storage', return_value=F.STORAGE), \
                patch.object(M.LIFECYCLE.StateController, 'close', new=fail), \
                patch.object(M, 'consumer_check', side_effect=OSError('census refused')), \
                patch('sys.stderr', new_callable=io.StringIO) as output:
            status = R.main(['--expected-nonce', owner.nonce])
        receipt = json.loads(output.getvalue())
        self.assertEqual(status, 1)
        self.assertFalse(receipt['success'])
        self.assertFalse(receipt['maintenance']['success'])
        self.assertFalse(receipt['open_acknowledged'])
        self.assertEqual(receipt['gate_state'], 'closed')
        self.assertEqual(receipt['maintenance']['close_errors'][0]['resource'], 'controller')
        self.assertEqual(self.gate()['state'], 'closed')
        with owner.exclusive(timeout=0):
            pass

    def test_cli_success_still_reports_completed_open(self):
        owner = self.close_gate()
        with patch.object(M, 'registered_storage', return_value=F.STORAGE), \
                patch('sys.stdout', new_callable=io.StringIO) as output, \
                patch('sys.stderr', new_callable=io.StringIO) as stderr:
            status = R.main(['--expected-nonce', owner.nonce])
        receipt = json.loads(output.getvalue())
        self.assertEqual(status, 0)
        self.assertTrue(receipt['success'])
        self.assertTrue(receipt['open_acknowledged'])
        self.assertEqual(receipt['gate_state'], 'open')
        self.assertEqual(self.gate()['state'], 'open')
        self.assertEqual(stderr.getvalue(), '')

    def test_closed_object_still_requires_creator_thread_and_pid(self):
        operation = self.operation(self.close_gate().nonce)
        operation.close()
        errors = []
        def foreign():
            for method in (operation.close, operation.finish_success):
                try:
                    method()
                except A.AdmissionError:
                    errors.append(True)
        thread = threading.Thread(target=foreign)
        thread.start()
        thread.join()
        self.assertEqual(errors, [True, True])
        with patch.object(M.os, 'getpid', return_value=os.getpid() + 1):
            with self.assertRaises(A.AdmissionError):
                operation.close()


if __name__ == '__main__':
    unittest.main()
