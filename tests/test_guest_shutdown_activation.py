"""Actual canonical readonly shutdown EX; physical placement/mounts substituted.

Protected runtime bytes and flocks are native on /run tmpfs. These tests run no
guest, physical NVMe operation or kernel mount; see the opt-in loop companion.
"""
import copy
import fcntl
import importlib.util
import os
from pathlib import Path
import tempfile
import threading
import types
import unittest
from unittest.mock import Mock, patch
import uuid

SPEC = importlib.util.spec_from_file_location('shutdown_mount_fixture',
    Path(__file__).with_name('test_guest_mount.py'))
T = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(T)
M, A = T.M, T.M.ADMISSION


@unittest.skipUnless(os.getresuid() == (0, 0, 0), 'native protected runtime requires root')
class ShutdownActivation(unittest.TestCase):
    command = T.Transaction.command

    def setUp(self):
        T.Transaction.setUp(self)
        self.context['disk'].update(serial='reviewed-disk', size_bytes=250_000_000_000)
        self.context['state']['uuid'] = str(uuid.uuid4())
        self.tmp = tempfile.TemporaryDirectory(prefix='a733-shutdown-test-', dir='/run')
        self.addCleanup(self.tmp.cleanup)
        self.patches.enter_context(patch.object(A, 'RUNTIME_PARENT', self.tmp.name))
        self.patches.enter_context(patch.object(A, 'RUNTIME', self.tmp.name + '/a733-admission'))
        self.admission = A.initialize(A._boot_id(), os.getgid(), lambda: 'running')
        self.addCleanup(self.admission.close)
        self.admission.bootstrap_owner.open()
        self.admission.bootstrap_owner.lease.close()
        self.admission.close_gate('shutdown')
        self.lease = self.admission.shutdown_lease()
        self.addCleanup(self.lease.close)
        self.host = self.patches.enter_context(patch.object(M.CONTEXT, 'host_identity'))

    def apply(self, **kwargs):
        return M.apply_shutdown(self.args, self.plan, self.lease, **kwargs)

    def unchanged_authority(self):
        self.assertEqual(self.admission._read()['reason'], 'shutdown')
        self.assertEqual(self.admission._read()['state'], 'closed')
        self.assertEqual(fcntl.fcntl(self.lease.fd, fcntl.F_GETFL) & os.O_ACCMODE, os.O_RDONLY)
        A._held_lock(self.lease.fd, 'WRITE', os.getpid())
        self.lease.recheck()

    def rejected(self, lease=None):
        # Initial authorization deliberately raises before constructing a report.
        with self.assertRaises((ValueError, A.AdmissionError, OSError)):
            M.apply_shutdown(self.args, self.plan, self.lease if lease is None else lease)
        self.run.assert_not_called()
        self.grant.assert_not_called()
        M.MountDirectory.assert_not_called()

    def test_success_borrows_live_readonly_ex_and_never_takes_gate(self):
        record = self.admission._read()
        with patch.object(self.admission, '_gate', side_effect=AssertionError('deep guard must not take gate')), \
                patch.object(A, 'initialize', side_effect=AssertionError('must not initialize')), \
                patch.object(A.ClosureOwner, 'open', side_effect=AssertionError('must not reopen')):
            report = self.apply()
        self.assertTrue(report['success'], report)
        self.assertTrue(report['mount_attempted'])
        self.assertTrue(report['guest_may_be_modified'])
        self.assertTrue(report['mount_retained'])
        # The substituted traversal does not mutate anything.
        self.assertFalse(report['permissions_may_be_modified'])
        self.assertFalse(report['cleanup_errors'])
        self.assertEqual(self.admission._read(), record)
        self.unchanged_authority()
        M.authorize.assert_not_called()
        self.host.assert_called_with(self.admission.boot_id)

    def test_already_mounted_retains_borrow_without_mutation(self):
        self.plan['mounted_before'] = self.visible = True
        report = self.apply()
        self.assertTrue(report['success'], report)
        self.assertTrue(report['mounted_before'])
        self.assertTrue(report['mount_retained'])
        for name in ('mount_attempted', 'guest_may_be_modified', 'permissions_may_be_modified'):
            self.assertFalse(report[name], name)
        self.run.assert_not_called()
        self.unchanged_authority()

    def test_foreign_and_noncanonical_capabilities_fail_before_mount(self):
        for lease in (object(), types.SimpleNamespace(**vars(self.lease)),
                      self.admission.bootstrap_owner):
            with self.subTest(lease=type(lease).__name__):
                self.rejected(lease)
        foreign = A.Lease(types.SimpleNamespace(), self.lease.fd, True)
        self.rejected(foreign)

    def test_released_shared_unlocked_or_writable_fail_before_mount(self):
        fd = self.lease.fd
        try:
            self.lease.fd = None
            self.rejected()
            self.lease.fd = fd
            self.lease.exclusive = False
            self.rejected()
            self.lease.exclusive = True
            fcntl.flock(fd, fcntl.LOCK_UN)
            self.rejected()
            fcntl.flock(fd, fcntl.LOCK_SH)
            self.rejected()
            fcntl.flock(fd, fcntl.LOCK_UN)
            writable = os.open(Path(A.RUNTIME) / 'leases.lock', os.O_RDWR | os.O_CLOEXEC)
            try:
                fcntl.flock(writable, fcntl.LOCK_EX)
                self.rejected(A.Lease(self.admission, writable, True))
            finally:
                os.close(writable)
        finally:
            self.lease.fd, self.lease.exclusive = fd, True
            fcntl.flock(fd, fcntl.LOCK_EX)

    def test_actual_root_main_thread_and_fork_owner_required(self):
        with patch.object(M.os, 'getresuid', return_value=(1000, 0, 0)):
            self.rejected()
        errors = []
        def thread():
            try:
                M.shutdown_check(self.lease)
            except Exception as error:
                errors.append(str(error))
        worker = threading.Thread(target=thread)
        worker.start()
        worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(errors), 1)
        pid = os.fork()
        if pid == 0:
            try:
                M.shutdown_check(self.lease)
            except Exception:
                os._exit(0)
            os._exit(1)
        self.assertEqual(os.waitpid(pid, 0)[1], 0)
        self.run.assert_not_called()

    def test_malformed_bootstrap_maintenance_open_and_wrong_boot_records_refuse(self):
        path = Path(A.RUNTIME) / 'gate.json'
        original = path.read_bytes()
        record = self.admission._read()
        try:
            for value in (b'{}\n', A.encode(dict(record, reason='bootstrap')),
                          A.encode(dict(record, reason='maintenance')),
                          A.encode(dict(record, state='open', reason='maintenance')),
                          A.encode(dict(record, boot_id=str(uuid.uuid4())))):
                with self.subTest(value=value):
                    path.write_bytes(value)
                    self.rejected()
                    self.assertEqual(path.read_bytes(), value)
        finally:
            path.write_bytes(original)

    def test_current_boot_host_and_missing_runtime_refuse(self):
        for substitute in (patch.object(A, '_boot_id', return_value=str(uuid.uuid4())),
                patch.object(M.CONTEXT, 'host_identity', side_effect=ValueError('foreign host'))):
            with substitute:
                self.rejected()
        runtime = Path(A.RUNTIME)
        moved = runtime.with_name('absent-runtime')
        runtime.rename(moved)
        try:
            self.rejected()
        finally:
            moved.rename(runtime)

    def test_current_reviewed_tuple_and_context_guard_reused(self):
        for section, field in (('disk', 'serial'), ('disk', 'size_bytes'),
                               ('state', 'uuid'), ('guest', 'uuid')):
            changed = copy.deepcopy(self.plan)
            changed['context'][section][field] = 'changed'
            with self.subTest(section=section, field=field), \
                    patch.object(M, 'make_plan', return_value=changed):
                report = self.apply()
                self.assertFalse(report['success'])
                self.assertFalse(report['mount_attempted'])
                self.assertFalse(report['guest_may_be_modified'])
                self.run.assert_not_called()
        with patch.object(M, 'snapshot', return_value={'changed': 'STATE or disk context'}):
            report = self.apply()
        self.assertFalse(report['success'])
        self.run.assert_not_called()
        self.unchanged_authority()

    def test_initial_and_deep_guard_failure_precede_mount(self):
        with self.assertRaisesRegex(ValueError, 'initial guard'):
            self.apply(plan_check=Mock(side_effect=ValueError('initial guard')))
        calls = []
        def guard():
            calls.append(True)
            if len(calls) == 3:
                raise ValueError('deep guard')
        report = self.apply(plan_check=guard)
        self.assertFalse(report['success'])
        self.assertFalse(report['mount_attempted'])
        self.run.assert_not_called()
        self.unchanged_authority()

    def test_post_mount_failure_unmounts_with_borrowed_lease_alive(self):
        self.grant.side_effect = ValueError('permission rejected')
        report = self.apply()
        self.assertFalse(report['success'])
        self.assertTrue(report['mount_attempted'])
        self.assertTrue(report['guest_may_be_modified'])
        self.assertFalse(report['permissions_may_be_modified'])
        self.assertFalse(report['mount_retained'])
        self.assertFalse(report['mount_may_be_visible'])
        self.assertFalse(report['cleanup_errors'])
        self.assertEqual(self.run.call_count, 2)
        self.unchanged_authority()

    def test_permission_change_receipt_survives_failure_and_unmount(self):
        def permissions(args, context, identity, report, **kwargs):
            report['permissions_may_be_modified'] = True
            raise ValueError('chmod completion uncertain')
        self.grant.side_effect = permissions
        report = self.apply()
        self.assertFalse(report['success'])
        self.assertTrue(report['permissions_may_be_modified'])
        self.assertTrue(report['guest_may_be_modified'])
        self.assertFalse(report['mount_retained'])
        self.assertFalse(report['mount_may_be_visible'])
        self.assertFalse(report['cleanup_errors'])
        self.unchanged_authority()

    def test_post_mount_guard_change_retains_conservative_mount(self):
        def guard():
            if self.visible:
                raise ValueError('policy changed')
        report = self.apply(plan_check=guard)
        self.assertFalse(report['success'])
        self.assertTrue(report['mount_retained'])
        self.assertTrue(report['mount_may_be_visible'])
        self.assertTrue(report['guest_may_be_modified'])
        self.assertTrue(report['cleanup_errors'])
        self.grant.assert_not_called()
        self.assertEqual(self.run.call_count, 1)
        self.unchanged_authority()


if __name__ == '__main__':
    unittest.main()
