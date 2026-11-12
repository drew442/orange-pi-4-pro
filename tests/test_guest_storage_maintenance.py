"""Native admission/RO EX custody, explicit board/STATE/p5 placement substitutes.

No mount, KVM guest or hardware acceptance is exercised by these fixtures.
"""
import fcntl
import importlib.util
import os
from pathlib import Path
import threading
import unittest
import uuid
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, BASE / relative)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


S = load('storage_maintenance_tests', 'scripts/guest-storage-maintenance.py')
F = load('storage_maintenance_fixture', 'tests/test_guest_host_maintenance.py')
# The fixture must compose actual producers in this helper's CP family.
F.M, F.A, F.L, F.P = S.M, S.ADMISSION, S.LIFECYCLE, S.LIFECYCLE.POLICY
A, M, L, STORAGE = S.ADMISSION, S.M, S.LIFECYCLE, F.STORAGE


class Boundaries(unittest.TestCase):
    def test_nonroot_and_invalid_storage_before_host_reads(self):
        with patch.object(S.os, 'getresuid', return_value=(1000, 1000, 1000)), \
                patch.object(S.CONTEXT, 'read_system') as read:
            with self.assertRaisesRegex(S.MaintenanceError, 'actual root'):
                S.StorageMaintenance(STORAGE)
            read.assert_not_called()
        with patch.object(S.CONTEXT, 'read_system') as read:
            with self.assertRaises(S.MaintenanceError):
                S.StorageMaintenance({**STORAGE, 'bypass': True})
            read.assert_not_called()
        with self.assertRaises(TypeError):
            S.StorageMaintenance(STORAGE, skip_p5=True)


@unittest.skipUnless(os.getresuid() == (0, 0, 0), 'native protected RAM fixture requires root')
class Installed(unittest.TestCase):
    gate = F.Installed.gate

    def setUp(self):
        F.Installed.setUp(self)
        self.marker = ('a733-state-v1\n' + STORAGE['state_uuid'] + '\n').encode()
        self.serial = (STORAGE['nvme_serial'] + '\n').encode()
        management = S.CONTEXT.MANAGEMENT
        native_resolve = Path.resolve
        def resolve(path, *args, **kwargs):
            if str(path).startswith('/sys/dev/block/'):
                return Path('/sys/devices/fixture/nvme0n1/nvme0n1p4')
            return native_resolve(path, *args, **kwargs)
        for substitute in (
                patch.object(management, 'validate_mounts', return_value=({}, {'source': '/dev/nvme0n1p4'})),
                patch.object(management, 'block_identity', return_value='259:4'),
                patch.object(management, 'read_secure', side_effect=lambda *args: self.marker),
                patch.object(Path, 'resolve', new=resolve),
                patch.object(S.CONTEXT, 'read_system', side_effect=lambda path, limit:
                    self.serial if path.startswith('/sys/') else (self.boot + '\n').encode())):
            substitute.start()
            self.addCleanup(substitute.stop)

    def operation(self):
        operation = S.StorageMaintenance(STORAGE)
        self.addCleanup(operation.close)
        return operation

    def test_p5_absent_acquires_without_context_and_binds_once(self):
        native = S.CONTEXT.HostContext
        with patch.object(S.CONTEXT, 'HostContext', side_effect=FileNotFoundError('p5 absent')) as context:
            operation = self.operation()
            operation.recheck()
            context.assert_not_called()
        self.assertEqual(self.events, ['stop', 'census'])
        self.assertIsNone(operation.controller)
        self.assertFalse(operation.receipt['storage_bound'])
        with self.assertRaisesRegex(A.AdmissionError, 'not bound'):
            _ = operation.context
        flags = fcntl.fcntl(operation.lease.fd, fcntl.F_GETFL)
        self.assertEqual(flags & os.O_ACCMODE, os.O_RDONLY)
        A._held_lock(operation.lease.fd, 'WRITE', os.getpid())
        with patch.object(S.CONTEXT, 'HostContext', wraps=native) as context:
            operation.bind_storage()
            context.assert_called_once()
        self.assertIs(type(operation.controller), L.StateController)
        self.assertTrue(operation.receipt['storage_bound'])
        self.assertIs(operation.context, operation.controller.context)
        result = operation.finish_success()
        self.assertTrue(result['open_acknowledged'])
        self.assertEqual(self.gate()['state'], 'open')
        self.assertEqual(self.events, ['stop', 'census', 'census', 'census'])
        with self.assertRaises(A.AdmissionError):
            operation.recheck()
        with patch.object(operation.owner, 'open') as reopen:
            with self.assertRaisesRegex(S.MaintenanceError, 'already finished') as caught:
                operation.finish_success()
            self.assertEqual(caught.exception.receipt, result)
            reopen.assert_not_called()
        with self.assertRaisesRegex(S.MaintenanceError, 'already finished') as caught:
            operation.bind_storage()
        self.assertEqual(caught.exception.receipt, result)

    def test_unbound_finish_refuses_and_cleans(self):
        operation = self.operation()
        with self.assertRaisesRegex(S.MaintenanceError, 'not bound'):
            operation.finish_success()
        self.assertTrue(operation._closed)
        self.assertIsNone(operation.lease)
        self.assertEqual(self.gate()['state'], 'closed')

    def test_binding_failure_closes_all_and_never_opens(self):
        operation = self.operation()
        with patch.object(S.CONTEXT, 'HostContext', side_effect=FileNotFoundError('p5 absent')):
            with self.assertRaisesRegex(S.MaintenanceError, 'p5 absent') as caught:
                operation.bind_storage()
        self.assertEqual(caught.exception.receipt['phase'], 'bind-storage')
        self.assertIsNone(operation.admission)
        self.assertIsNone(operation.lease)
        self.assertEqual(self.gate()['state'], 'closed')

    def test_second_binding_refuses_and_closes(self):
        operation = self.operation()
        operation.bind_storage()
        with self.assertRaisesRegex(S.MaintenanceError, 'already attempted'):
            operation.bind_storage()
        self.assertTrue(operation._closed)
        self.assertEqual(self.gate()['state'], 'closed')

    def test_wrong_selected_state_or_serial_refuses_before_gate_change(self):
        for field, bad in (('marker', b'a733-state-v1\n' + str(uuid.uuid4()).encode() + b'\n'),
                           ('serial', b'WRONG\n')):
            original = getattr(self, field)
            setattr(self, field, bad)
            with self.assertRaises(S.MaintenanceError):
                self.operation()
            setattr(self, field, original)
            self.assertEqual(self.gate()['state'], 'open')
        self.assertEqual(self.events, [])

    def test_native_and_pid1_refuse_before_gate_change(self):
        with patch.object(M.CONTEXT, 'host_identity', side_effect=RuntimeError('foreign namespace')):
            with self.assertRaises(S.MaintenanceError):
                self.operation()
        with patch.object(M.NORMAL, 'manager_state', return_value='stopping'):
            with self.assertRaises(S.MaintenanceError):
                self.operation()
        self.assertEqual(self.gate()['state'], 'open')

    def test_manual_shared_or_live_exclusive_refuse_after_stop(self):
        with self.admission.admit():
            with self.assertRaises(S.MaintenanceError) as caught:
                self.operation()
        self.assertEqual(caught.exception.receipt['phase'], 'exclusive-lease')
        self.assertEqual(self.events, ['stop'])
        self.assertEqual(self.gate()['state'], 'closed')
        # A second requester cannot adopt even a closed maintenance owner.
        with A.ClosureOwner(self.admission, self.gate()).exclusive(timeout=0):
            with self.assertRaisesRegex(S.MaintenanceError, 'already closed'):
                self.operation()

    def test_bootstrap_and_shutdown_closed_records_not_adopted(self):
        for reason in ('bootstrap', 'shutdown'):
            self.admission.close_gate('shutdown')
            if reason == 'bootstrap':
                with self.admission._gate():
                    self.admission._publish(dict(self.admission._read(), reason='bootstrap'))
            with self.assertRaisesRegex(S.MaintenanceError, 'already closed'):
                self.operation()
            self.assertEqual(self.gate()['reason'], reason)
            # Fixture-only reset, never a production authority.
            with self.admission._gate():
                record = self.admission._read()
                self.admission._publish(dict(record, state='open', reason='maintenance'))
        self.assertEqual(self.events, [])

    def test_actual_nonce_shutdown_and_lost_ex_are_rechecked(self):
        operation = self.operation()
        fcntl.flock(operation.lease.fd, fcntl.LOCK_SH)
        with self.assertRaises(A.AdmissionError):
            operation.recheck()
        fcntl.flock(operation.lease.fd, fcntl.LOCK_EX)
        with self.admission._gate():
            self.admission._publish(dict(self.admission._read(), owner_nonce=str(uuid.uuid4())))
        with self.assertRaisesRegex(A.AdmissionError, 'ownership changed'):
            operation.recheck()
        with self.assertRaises(S.MaintenanceError):
            operation.bind_storage()
        self.assertEqual(self.gate()['state'], 'closed')

    def test_shutdown_supersedes_owner_and_prevents_finish(self):
        operation = self.operation()
        operation.bind_storage()
        self.admission.close_gate('shutdown')
        with self.assertRaises(S.MaintenanceError):
            operation.finish_success()
        self.assertEqual(self.gate()['reason'], 'shutdown')

    def test_bound_context_changed_unknown_head_or_consumer_refuses(self):
        operation = self.operation()
        self.bad_context = True
        with self.assertRaises(S.MaintenanceError):
            operation.bind_storage()
        self.assertEqual(self.gate()['state'], 'closed')

    def test_bound_context_and_selected_state_rechecked(self):
        operation = self.operation()
        operation.bind_storage()
        self.bad_context = True
        with self.assertRaises(A.AdmissionError):
            operation.recheck()
        self.bad_context = False
        self.marker = b'wrong selected STATE'
        with self.assertRaises(S.MaintenanceError):
            operation.finish_success()
        self.assertEqual(self.gate()['state'], 'closed')

    def test_foreign_controller_not_accepted(self):
        operation = self.operation()
        operation.bind_storage()
        controller = operation.controller
        operation.controller = object()
        with self.assertRaisesRegex(A.AdmissionError, 'same-family'):
            operation.recheck()
        operation.controller = controller
        operation.recheck()

    def test_quiescence_precedes_reopen_with_no_postopen_proof(self):
        operation = self.operation()
        native_quiescent = operation._quiescent
        native_open = operation.owner.open
        events = []
        def quiescent():
            self.assertEqual(self.gate()['state'], 'closed')
            events.append('quiescence')
            native_quiescent()
        def reopen():
            events.append('open')
            native_open()
        with patch.object(operation, '_quiescent', side_effect=quiescent), \
                patch.object(operation.owner, 'open', side_effect=reopen):
            operation.bind_storage()
            operation.finish_success()
        self.assertEqual(events, ['quiescence', 'quiescence', 'open'])

    def test_open_ack_failure_reports_uncertainty(self):
        operation = self.operation()
        operation.bind_storage()
        native_open = operation.owner.open
        def reopen():
            native_open()
            raise OSError('lost acknowledgement')
        with patch.object(operation.owner, 'open', side_effect=reopen):
            with self.assertRaises(S.MaintenanceError) as caught:
                operation.finish_success()
        self.assertEqual(caught.exception.receipt['gate_state'], 'uncertain')
        self.assertFalse(caught.exception.receipt['open_acknowledged'])
        self.assertEqual(self.gate()['state'], 'open')

    def test_unknown_head_refuses_at_binding(self):
        operation = self.operation()
        self.head_path.write_bytes(b'{}\n')
        with self.assertRaises(S.MaintenanceError):
            operation.bind_storage()
        self.assertEqual(self.gate()['state'], 'closed')

    def test_consumer_refuses_at_entry_binding_and_finish(self):
        with patch.object(M, 'consumer_check', side_effect=RuntimeError('KVM remains')):
            with self.assertRaises(S.MaintenanceError):
                self.operation()
        self.assertEqual(self.gate()['state'], 'closed')

    def test_binding_consumer_refusal(self):
        operation = self.operation()
        with patch.object(M, 'consumer_check', side_effect=RuntimeError('KVM remains')):
            with self.assertRaises(S.MaintenanceError):
                operation.bind_storage()
        self.assertEqual(self.gate()['state'], 'closed')

    def test_final_consumer_refusal(self):
        operation = self.operation()
        operation.bind_storage()
        with patch.object(M, 'consumer_check', side_effect=RuntimeError('KVM remains')):
            with self.assertRaises(S.MaintenanceError):
                operation.finish_success()
        self.assertEqual(self.gate()['state'], 'closed')

    def test_foreign_thread_including_closed_cannot_use(self):
        operation = self.operation()
        errors = []
        def foreign():
            for action in (operation.recheck, operation.close, operation.bind_storage, operation.finish_success):
                try:
                    action()
                except A.AdmissionError:
                    errors.append(True)
        for closed in (False, True):
            if closed:
                operation.close()
            thread = threading.Thread(target=foreign)
            thread.start()
            thread.join()
        self.assertEqual(len(errors), 8)

    def test_child_cannot_close_bind_or_finish(self):
        operation = self.operation()
        read, write = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.close(read)
            try:
                for action in (operation.close, operation.bind_storage, operation.finish_success):
                    try:
                        action()
                    except A.AdmissionError:
                        os.write(write, b'x')
            finally:
                os._exit(0)
        os.close(write)
        try:
            received = b''
            while True:
                piece = os.read(read, 10)
                if not piece:
                    break
                received += piece
            self.assertEqual(received, b'xxx')
        finally:
            os.close(read)
            os.waitpid(pid, 0)
        operation.recheck()

    def test_cleanup_failure_reports_all_owned_resources_once(self):
        operation = self.operation()
        operation.bind_storage()
        calls = []
        for field in ('controller', 'lease', 'admission'):
            resource = getattr(operation, field)
            native = resource.close
            def close(field=field, native=native):
                calls.append(field)
                native()
                raise OSError('cleanup acknowledgement')
            substitute = patch.object(resource, 'close', side_effect=close)
            substitute.start()
            self.addCleanup(substitute.stop)
        with patch.object(M, 'consumer_check', side_effect=RuntimeError('consumer')):
            with self.assertRaises(S.MaintenanceError) as caught:
                operation.finish_success()
        self.assertEqual(calls, ['controller', 'lease', 'admission'])
        self.assertEqual(len(caught.exception.receipt['close_errors']), 3)
        self.assertIn('cleanup_error', caught.exception.receipt)
        operation.close()
        self.assertEqual(len(calls), 3)
        self.assertEqual(self.gate()['state'], 'closed')


if __name__ == '__main__':
    unittest.main()
