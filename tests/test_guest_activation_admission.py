"""Installed activation composition: native protected registry/admission/RO EX.

Board, PID1, physical STATE/p5 placement and mount operations are explicit
fixtures. These host tests supply no boot, actual mount or KVM acceptance.
"""
import contextlib
import fcntl
import importlib.util
import os
from pathlib import Path
import unittest
import uuid
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, BASE / relative)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


T = load('activation_core_fixture', 'tests/test_guest_mount.py')
F = load('activation_storage_fixture', 'tests/test_guest_storage_maintenance.py')
M, S, A, STORAGE = T.M, F.S, F.A, F.STORAGE


@unittest.skipUnless(os.getresuid() == (0, 0, 0), 'native protected RAM fixture requires root')
class Installed(unittest.TestCase):
    command = T.Transaction.command
    gate = F.Installed.gate

    def setUp(self):
        T.Transaction.setUp(self)
        F.Installed.setUp(self)
        self.context['disk']['serial'] = STORAGE['nvme_serial']
        self.context['state']['uuid'] = STORAGE['state_uuid']
        self.context['guest']['uuid'] = STORAGE['guest_uuid']
        self.operations = []
        self.target_depth = 0
        native_helper, native_held, native_stat = M.adjacent, S.CONTEXT.ENROLL.HeldPath, os.stat
        native_storage = S.StorageMaintenance
        self.maintenance_type = native_storage

        def helper(name, filename):
            if filename == 'guest-storage-maintenance.py':
                return S
            return native_helper(name, filename)

        def held(path, **kwargs):
            if str(path) == '/run/a733-state/guests/autostart.json':
                path = self.state / 'guests/autostart.json'
            return native_held(path, **kwargs)

        def placed_stat(path, *args, **kwargs):
            if str(path) == '/run/a733-state':
                path = self.state
            return native_stat(path, *args, **kwargs)

        def operation(storage):
            result = native_storage(storage)
            self.operations.append(result)
            self.addCleanup(result.close)
            return result

        def authorize(plan):
            self.assertEqual(self.gate()['state'], 'open')
            self.events.append('authorize')

        @contextlib.contextmanager
        def target_lock(*args, **kwargs):
            operation = self.operations[-1]
            self.assertEqual(self.gate()['state'], 'closed')
            self.assertEqual(fcntl.fcntl(operation.lease.fd, fcntl.F_GETFL) & os.O_ACCMODE, os.O_RDONLY)
            A._held_lock(operation.lease.fd, 'WRITE', os.getpid())
            self.events.append('target-lock')
            self.target_depth += 1
            try:
                yield 42
            finally:
                self.events.append('target-close')
                self.target_depth -= 1

        for substitute in (patch.object(M, 'adjacent', side_effect=helper),
                patch.object(S.CONTEXT.ENROLL, 'HeldPath', side_effect=held),
                patch.object(S.os, 'stat', side_effect=placed_stat),
                patch.object(S, 'StorageMaintenance', side_effect=operation),
                patch.object(M, 'authorize', side_effect=authorize),
                patch.object(M, 'activation_lock', side_effect=target_lock),
                patch.object(M, 'block_stream', side_effect=target_lock)):
            self.patches.enter_context(substitute)
        self.directory.close.side_effect = lambda: self.events.append('directory-close')

    def apply(self, **kwargs):
        return M.apply(self.args, self.plan, **kwargs)

    def test_native_success_order_and_existing_mount(self):
        # Binding must follow every target FD/activation-lock/directory cleanup.
        native_bind = self.maintenance_type.bind_storage
        def bind(operation):
            self.assertEqual(self.target_depth, 0)
            self.assertIn('directory-close', self.events)
            self.assertEqual(self.events[-1], 'target-close')
            return native_bind(operation)
        with patch.object(self.maintenance_type, 'bind_storage', new=bind):
            report = self.apply()
        self.assertTrue(report['success'], report)
        self.assertTrue(report['maintenance']['storage_bound'])
        self.assertTrue(report['maintenance']['open_acknowledged'])
        self.assertEqual(self.gate()['state'], 'open')
        self.assertTrue(self.operations[-1]._closed)
        self.assertEqual(self.events[:3], ['authorize', 'stop', 'census'])
        self.assertLess(self.events.index('directory-close'), len(self.events) - 2)
        self.assertEqual(self.events[-3:], ['target-close', 'census', 'census'])
        self.plan['mounted_before'] = self.visible = True
        self.run.reset_mock()
        report = self.apply()
        self.assertTrue(report['success'], report)
        self.assertFalse(report['mount_attempted'])
        self.run.assert_not_called()

    def test_authorization_failure_precedes_helper_or_gate_close(self):
        with patch.object(M, 'authorize', side_effect=ValueError('TTY denied')), \
                patch.object(S.M, 'registered_storage') as registry:
            report = self.apply()
        self.assertFalse(report['success'])
        registry.assert_not_called()
        self.assertEqual(self.gate()['state'], 'open')
        self.assertEqual(self.events, [])
        self.run.assert_not_called()

    def test_each_reviewed_tuple_mismatch_precedes_gate_and_mount(self):
        for field in ('state_uuid', 'guest_uuid', 'nvme_serial'):
            with self.subTest(field=field), patch.object(S.M, 'registered_storage',
                    return_value={**STORAGE, field: 'wrong'}):
                report = self.apply()
                self.assertFalse(report['success'])
                self.assertIn('registered storage differs', report['errors'][0])
                self.assertEqual(self.gate()['state'], 'open')
                self.run.assert_not_called()
        self.assertEqual(self.operations, [])

    def test_wrong_reviewed_plan_refuses_before_authorization(self):
        self.make.return_value = dict(self.plan, plan_sha256='different')
        report = self.apply()
        self.assertFalse(report['success'])
        M.authorize.assert_not_called()
        self.assertEqual(self.gate()['state'], 'open')
        self.run.assert_not_called()

    def test_protected_registry_malformed_refuses_before_gate(self):
        (self.state / 'guests/autostart.json').write_bytes(b'{}\n')
        report = self.apply()
        self.assertFalse(report['success'])
        self.assertEqual(self.gate()['state'], 'open')
        self.run.assert_not_called()

    def test_platform_and_pid1_refuse_before_gate_close(self):
        for substitute in (
                patch.object(S.CONTEXT, 'host_identity', side_effect=ValueError('foreign namespace')),
                patch.object(S.M.NORMAL, 'manager_state', return_value='stopping')):
            with substitute:
                report = self.apply()
            self.assertFalse(report['success'])
            self.assertEqual(self.gate()['state'], 'open')
            self.run.assert_not_called()
            self.assertNotIn('target-lock', self.events)

    def test_malformed_existing_gate_refuses_without_reinitialization(self):
        path = Path(A.RUNTIME) / 'gate.json'
        # Admission uses a protected canonical gate; malformed bytes grant no owner.
        path.write_bytes(b'{}\n')
        with patch.object(M, 'authorize'):
            report = self.apply()
        self.assertFalse(report['success'])
        self.run.assert_not_called()
        self.assertNotIn('target-lock', self.events)
        self.assertEqual(path.read_bytes(), b'{}\n')

    def test_shared_manual_lease_refuses_before_target_lock(self):
        with self.admission.admit():
            report = self.apply()
        self.assertFalse(report['success'])
        self.assertEqual(report['maintenance']['phase'], 'exclusive-lease')
        self.assertEqual(self.gate()['state'], 'closed')
        self.assertNotIn('target-lock', self.events)
        self.run.assert_not_called()

    def test_existing_bootstrap_shutdown_maintenance_closures_not_adopted(self):
        for reason in ('bootstrap', 'shutdown', 'maintenance'):
            with self.subTest(reason=reason):
                self.admission.close_gate('shutdown')
                with self.admission._gate():
                    self.admission._publish(dict(self.admission._read(), reason=reason))
                nonce = self.gate()['owner_nonce']
                # Authorization remains an operator prompt; allow closed fixture observation.
                with patch.object(M, 'authorize'):
                    report = self.apply()
                self.assertFalse(report['success'])
                self.assertEqual(self.gate()['owner_nonce'], nonce)
                self.assertEqual(self.gate()['reason'], reason)
                self.assertNotIn('target-lock', self.events)
                self.run.assert_not_called()

    def test_missing_runtime_not_initialized(self):
        with patch.object(A, 'RUNTIME', str(self.base / 'absent')), \
                patch.object(A, 'initialize', side_effect=AssertionError('no initialization')):
            report = self.apply()
        self.assertFalse(report['success'])
        self.assertEqual(self.gate()['state'], 'open')
        self.run.assert_not_called()

    def test_failure_mount_receipts_and_closed_gate_preserved(self):
        self.grant.side_effect = ValueError('permissions rejected')
        report = self.apply()
        self.assertFalse(report['success'])
        self.assertTrue(report['mount_attempted'])
        self.assertTrue(report['guest_may_be_modified'])
        self.assertFalse(report['mount_retained'])
        self.assertFalse(report['maintenance']['storage_bound'])
        self.assertEqual(self.gate()['state'], 'closed')
        # No retry/adoption when the failed activation left p5 absent.
        with patch.object(M, 'authorize'):
            retry = self.apply()
        self.assertFalse(retry['success'])
        self.assertEqual(self.run.call_count, 2)

    def test_original_plan_guard_rechecked_after_ex_before_targets(self):
        observations = []
        def guard():
            observations.append(self.gate()['state'])
            if observations[-1] == 'closed':
                raise ValueError('held reviewed plan changed')
        report = self.apply(plan_check=guard)
        self.assertFalse(report['success'])
        self.assertEqual(observations, ['open', 'closed'])
        self.assertEqual(self.gate()['state'], 'closed')
        self.assertNotIn('target-lock', self.events)
        self.run.assert_not_called()

    def test_partial_permission_and_failed_unmount_receipts_preserved(self):
        def grant(args, context, identity, report, **kwargs):
            report['permissions_may_be_modified'] = True
            report['guest_may_be_modified'] = True
            raise OSError('chmod readback failed')
        def command(argv, **kwargs):
            if argv[0].endswith('/umount'):
                raise OSError('busy')
            self.visible = True
        self.grant.side_effect, self.run.side_effect = grant, command
        report = self.apply()
        self.assertFalse(report['success'])
        self.assertTrue(report['permissions_may_be_modified'])
        self.assertTrue(report['mount_retained'])
        self.assertIn('busy', report['cleanup_errors'][0])
        self.assertFalse(report['maintenance']['storage_bound'])
        self.assertEqual(self.gate()['state'], 'closed')

    def test_directory_cleanup_failure_never_binds_or_finishes(self):
        self.directory.close.side_effect = OSError('directory cleanup failed')
        report = self.apply()
        self.assertFalse(report['success'])
        self.assertTrue(report['mount_retained'])
        self.assertIn('directory cleanup failed', report['cleanup_errors'][0])
        self.assertFalse(report['maintenance']['storage_bound'])
        self.assertFalse(report['maintenance']['open_attempted'])
        self.assertEqual(self.gate()['state'], 'closed')

    def test_target_lock_cleanup_failure_never_finishes(self):
        @contextlib.contextmanager
        def bad_lock(*args, **kwargs):
            yield
            raise OSError('activation lock cleanup failed')
        with patch.object(M, 'activation_lock', side_effect=bad_lock):
            report = self.apply()
        self.assertFalse(report['success'])
        self.assertFalse(report['maintenance']['storage_bound'])
        self.assertFalse(report['maintenance']['open_attempted'])
        self.assertEqual(self.gate()['state'], 'closed')

    def test_nonce_race_after_mount_retains_actual_mount_and_no_finish(self):
        def command(argv, **kwargs):
            self.visible = True
            with self.admission._gate():
                self.admission._publish(dict(self.admission._read(), owner_nonce=str(uuid.uuid4())))
        self.run.side_effect = command
        report = self.apply()
        self.assertFalse(report['success'])
        self.assertTrue(report['mount_attempted'])
        self.assertTrue(report['mount_retained'])
        self.assertTrue(report['cleanup_errors'])
        self.assertFalse(report['maintenance']['open_attempted'])
        self.run.assert_called_once()
        self.assertEqual(self.gate()['state'], 'closed')

    def test_replaced_target_after_ex_refuses_before_mount(self):
        self.make.side_effect = [self.plan, dict(self.plan, plan_sha256='replacement')]
        report = self.apply()
        self.assertFalse(report['success'])
        self.assertIn('target-lock', self.events)
        self.assertEqual(self.gate()['state'], 'closed')
        self.run.assert_not_called()

    def test_bind_failure_preserves_mount_receipts_and_closed_gate(self):
        self.bad_context = True
        report = self.apply()
        self.assertFalse(report['success'])
        self.assertTrue(report['mount_attempted'])
        self.assertTrue(report['mount_retained'])
        self.assertEqual(report['maintenance']['phase'], 'bind-storage')
        self.assertFalse(report['maintenance']['open_attempted'])
        self.assertEqual(self.gate()['state'], 'closed')

    def test_open_publication_uncertainty_preserved(self):
        native = A.ClosureOwner.open
        def uncertain(owner):
            native(owner)
            raise OSError('open acknowledgment lost')
        with patch.object(A.ClosureOwner, 'open', new=uncertain):
            report = self.apply()
        self.assertFalse(report['success'])
        self.assertTrue(report['mount_retained'])
        self.assertTrue(report['maintenance']['open_attempted'])
        self.assertFalse(report['maintenance']['open_acknowledged'])
        self.assertEqual(report['maintenance']['gate_state'], 'uncertain')
        self.assertEqual(self.gate()['state'], 'open')

    def test_close_failure_prevents_overall_success_after_acknowledged_open(self):
        native = A.Admission.close
        def bad_close(admission):
            native(admission)
            if admission is not self.admission:
                raise OSError('admission close failed')
        with patch.object(A.Admission, 'close', new=bad_close):
            report = self.apply()
        self.assertFalse(report['success'])
        self.assertTrue(report['maintenance']['open_acknowledged'])
        self.assertTrue(report['maintenance']['close_errors'])
        self.assertTrue(report['cleanup_errors'])
        self.assertEqual(self.gate()['state'], 'open')


if __name__ == '__main__':
    unittest.main()
