"""Native readonly flock composition; no physical storage or host mount writes.

A733/kernel/RAM entry and storage/mount/subsystem identity are substituted.
Actual tmpfs metadata, canonical records, process census and flock execute.
Storage-core fixtures alone provide no offline authority or board acceptance.
"""
import contextlib
import fcntl
import importlib.util
import io
import json
import os
from pathlib import Path
import unittest
from unittest import mock
import uuid

BASE = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, BASE / 'tests' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


STORAGE = load('ram_recovery_storage_fixture', 'test_recover_host_root.py')
NATIVE = load('ram_recovery_native_fixture', 'test_offline_guest_admission.py')
REC, W = STORAGE.REC, NATIVE.W


@unittest.skipUnless(os.getresuid() == (0, 0, 0), 'requires actual root and /run tmpfs')
class RAMAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.storage = STORAGE.RecoveryTests('runTest')
        self.addCleanup(self.storage.doCleanups)
        self.storage.setUp()
        native_namespace_guard = W.namespace_guard
        self.native = NATIVE.OfflineTests('runTest')
        self.addCleanup(self.native.doCleanups)
        self.native.setUp()
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.object(W, 'namespace_guard', native_namespace_guard))
        self.stack.enter_context(mock.patch.object(REC, 'offline_policy', return_value=W))
        self.plan = self.storage.plan()
        self.events = []
        self.guard = None
        self.real_acquire = W.OfflineAdmission.acquire
        def acquire(boot_id):
            self.events.append('acquire')
            self.native.redirect_run = True
            try:
                self.guard = self.real_acquire(boot_id)
            finally:
                self.native.redirect_run = False
            self.addCleanup(lambda guard=self.guard: W.OfflineAdmission.close(guard))
            return self.guard
        self.stack.enter_context(mock.patch.object(W.OfflineAdmission, 'acquire', side_effect=acquire))
        original_auth = self.storage.auth.side_effect
        def authorize(plan):
            self.events.append('authorize')
            original_auth(plan)
        self.storage.auth.side_effect = authorize
        original_lock = REC.recovery_lock
        @contextlib.contextmanager
        def target_lock():
            self.assertIsNotNone(self.guard)
            self.guard.check()
            self.events.append('target-lock')
            with original_lock() as fd:
                yield fd
            self.events.append('target-closed')
        self.stack.enter_context(mock.patch.object(REC, 'recovery_lock', target_lock))

    def apply(self, cleanup=lambda: None, plan_check=lambda disk: None):
        return REC.apply(self.storage.args, self.storage.manifest, self.plan,
                         source_cleanup=cleanup, plan_check=plan_check)

    def closed_runtime(self):
        admission = self.native.initialize()
        admission.bootstrap_owner.lease.close()
        return admission

    def assert_no_mutation(self, report):
        self.assertFalse(report['success'], report)
        self.assertFalse(report['state_write_attempted'], report)
        self.assertFalse(report['selection_write_attempted'], report)
        self.storage.run.assert_not_called()
        self.assertNotIn('target-lock', self.events)

    def test_authorization_before_actual_exclusion_and_all_cleanup_before_final_census(self):
        admission = self.closed_runtime()
        original = (self.native.path / 'gate.json').read_bytes()
        real_check, real_census, real_close = W.OfflineAdmission.check, W.OfflineAdmission.check_consumers, W.OfflineAdmission.close
        def check(guard):
            self.events.append('check')
            return real_check(guard)
        def census(guard):
            self.events.append('census')
            return real_census(guard)
        def close(guard):
            self.events.append('close')
            return real_close(guard)
        def source_cleanup():
            self.events.append('source-cleanup')
            self.assertFalse(self.storage.mounted)
            self.guard.check()
            with self.assertRaises(NATIVE.A.AdmissionError):
                admission.bootstrap_owner.exclusive()
        def private():
            self.events.append('private-mnt')
        self.storage.namespace.side_effect = private
        with mock.patch.object(W.OfflineAdmission, 'check', check), \
                mock.patch.object(W.OfflineAdmission, 'check_consumers', census), \
                mock.patch.object(W.OfflineAdmission, 'close', close):
            report = self.apply(source_cleanup)
        self.assertTrue(report['success'], report)
        self.assertEqual(report['offline_admission'], dict(acquired=True, final_consumers_checked=True, closed=True))
        self.assertLess(self.events.index('authorize'), self.events.index('acquire'))
        self.assertLess(self.events.index('acquire'), self.events.index('target-lock'))
        self.assertLess(self.events.index('target-closed'), self.events.index('source-cleanup'))
        final_census = len(self.events) - 1 - self.events[::-1].index('census')
        self.assertLess(self.events.index('source-cleanup'), final_census)
        self.assertEqual(self.events[-1], 'close')
        after_private = self.events.index('private-mnt') + 1
        self.assertIn('check', self.events[after_private:])
        for fd in (self.guard.init_fd, self.guard.leases):
            with self.assertRaises(OSError):
                fcntl.fcntl(fd, fcntl.F_GETFD)
        self.assertEqual((self.native.path / 'gate.json').read_bytes(), original)
        self.assertTrue(report['publication_durable'])

    def test_absent_runtime_init_exclusion_is_readonly_and_retained_through_source_cleanup(self):
        def cleanup():
            self.assertEqual(fcntl.fcntl(self.guard.init_fd, fcntl.F_GETFL) & os.O_ACCMODE,
                             os.O_RDONLY)
            self.guard._held(self.guard.init_fd)
            with self.assertRaises(NATIVE.A.AdmissionError):
                self.native.initialize()
            self.assertFalse(self.native.path.exists())
        report = self.apply(cleanup)
        self.assertTrue(report['success'], report)
        self.assertFalse(self.native.path.exists())
        # Closed guard releases, but does not initialize or reopen anything.
        admission = self.native.initialize()
        self.assertEqual(admission._read()['state'], 'closed')

    def test_failed_unmount_retains_durable_storage_receipt_and_guard_through_cleanup(self):
        original = self.storage.run.side_effect
        def run(command, **kwargs):
            if command[0] == 'umount':
                raise OSError('retained busy STATE mount')
            return original(command, **kwargs)
        self.storage.run.side_effect = run
        def cleanup():
            self.assertTrue(self.storage.mounted)
            self.guard.check()
        report = self.apply(cleanup)
        self.assertFalse(report['success'], report)
        self.assertTrue(report['selection_published'])
        self.assertTrue(report['publication_durable'])
        self.assertTrue(report['state_may_be_modified'])
        self.assertFalse(report['state_unmounted'])
        self.assertIn('retained busy STATE mount', report['cleanup_error'])
        self.assertTrue(report['offline_admission']['closed'])

    def test_open_live_exclusive_and_shared_runtime_refuse_before_target_lock(self):
        admission = self.native.initialize()
        self.assert_no_mutation(self.apply())  # Actual live bootstrap EX.
        admission.bootstrap_owner.lease.close()
        fd = os.open(self.native.path / 'leases.lock', os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            self.assert_no_mutation(self.apply())
        finally:
            os.close(fd)
        with admission.bootstrap_owner.exclusive():
            admission.bootstrap_owner.open()
        self.assert_no_mutation(self.apply())

    def test_nonce_change_after_private_mount_refuses_before_replay(self):
        admission = self.closed_runtime()
        def change():
            record = admission._read()
            record['owner_nonce'] = str(uuid.uuid4())
            admission._publish(record)
        self.storage.namespace.side_effect = change
        report = self.apply()
        self.assertFalse(report['state_write_attempted'], report)
        self.assertFalse(report['success'])
        self.assertTrue(report['offline_admission']['closed'])
        self.assertFalse(report['offline_admission']['final_consumers_checked'])
        self.storage.run.assert_not_called()

    def test_auth_and_plan_races_refuse_before_exclusion(self):
        self.storage.auth.side_effect = OSError('TTY refused')
        self.assert_no_mutation(self.apply())
        self.assertNotIn('acquire', self.events)
        self.storage.auth.side_effect = lambda plan: None
        self.storage.state.write_bytes(b'changed')
        self.assert_no_mutation(self.apply())
        self.assertNotIn('acquire', self.events)

    def test_source_cleanup_final_query_and_close_failures_preserve_published_receipt(self):
        for fault in ('cleanup', 'census', 'close'):
            with self.subTest(fault=fault):
                def cleanup():
                    if fault == 'cleanup':
                        raise OSError('source cleanup failed')
                real_census, real_close = W.OfflineAdmission.check_consumers, W.OfflineAdmission.close
                def census(guard):
                    if fault == 'census' and self.storage.mounted is False and 'target-closed' in self.events:
                        raise OSError('final census failed')
                    return real_census(guard)
                def close(guard):
                    real_close(guard)
                    if fault == 'close':
                        raise OSError('guard close failed')
                self.events.clear()
                with mock.patch.object(W.OfflineAdmission, 'check_consumers', census), \
                        mock.patch.object(W.OfflineAdmission, 'close', close):
                    report = self.apply(cleanup)
                self.assertFalse(report['success'], report)
                self.assertTrue(report['selection_published'], report)
                self.assertTrue(report['publication_durable'], report)
                self.assertTrue(report['state_unmounted'], report)
                self.assertIn('failed', report['error'])
                (self.storage.boot / report['journal_name']).unlink()

    def test_cli_source_close_failure_is_under_guard_and_final_receipt_fails(self):
        plan_path = self.storage.directory / 'reviewed-plan.json'
        plan_path.write_text(json.dumps(self.plan))
        plan_path.chmod(0o400)
        original_close = self.storage.manifest.close
        def close_source():
            self.assertIsNotNone(self.guard)
            self.guard.check()
            self.assertFalse(self.storage.mounted)
            self.events.append('cli-source-close')
            original_close()
            raise OSError('CLI source close failed')
        factory = mock.Mock(return_value=self.storage.manifest)
        factory.identity = REC.Manifest.identity
        output = io.StringIO()
        argv = ['recover-host-root.py', 'apply', '--target', self.storage.args.target,
                '--expected-model', self.storage.args.expected_model,
                '--expected-serial', self.storage.args.expected_serial,
                '--expected-bytes', str(self.storage.args.expected_bytes),
                '--state-uuid', self.storage.uuid, '--slot', 'B',
                '--payload-manifest', str(self.storage.path),
                '--manifest-sha256', 'a' * 64, '--plan', str(plan_path)]
        with mock.patch.object(REC, 'Manifest', factory), \
                mock.patch.object(self.storage.manifest, 'close', side_effect=close_source), \
                mock.patch.object(REC.sys, 'argv', argv), \
                mock.patch.object(REC.sys, 'stdout', output), \
                mock.patch.object(REC.sys, 'stderr', io.StringIO()):
            code = REC.main()
        report = json.loads(output.getvalue())
        self.assertEqual(code, 1, report)
        self.assertFalse(report['success'])
        self.assertTrue(report['selection_published'], report)
        self.assertTrue(report['state_unmounted'], report)
        self.assertTrue(report['offline_admission']['closed'], report)
        self.assertIn('CLI source close failed', report['error'])
        self.assertIn('cli-source-close', self.events)

    def test_source_plan_callback_composes_with_guard_after_namespace_and_publication(self):
        checks = []
        def checked(disk):
            checks.append((self.storage.namespace.called, self.storage.mounted))
            if self.guard is not None:
                self.guard.check()
        report = self.apply(plan_check=checked)
        self.assertTrue(report['success'], report)
        self.assertIn((True, False), checks)
        self.assertIn((True, True), checks)


class HelperAuthenticationTests(unittest.TestCase):
    def test_exact_adjacent_helper_authenticated_without_project_imports(self):
        policy = REC.offline_policy()
        self.assertTrue(hasattr(policy, 'OfflineAdmission'))
        with mock.patch.object(REC, 'OFFLINE_HELPER_SHA256', '0' * 64):
            with self.assertRaisesRegex(ValueError, 'SHA-256'):
                REC.offline_policy()


if __name__ == '__main__':
    unittest.main()
