"""Teardown orchestration with explicit physical/PID1 substitutions.

Real protected RAM flocks remain; no systemctl stop, umount, VM or hardware work.
"""
from contextlib import ExitStack, contextmanager, nullcontext, redirect_stderr
import copy
import errno
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('stop_storage_test',
    Path(__file__).resolve().parents[1] / 'scripts/stop-guest-storage.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
A = M.ADMISSION
VALUE = dict(target='/dev/nvme0n1', expected_model='Test SSD', expected_bytes=250000000000,
    storage=dict(state_uuid='22222222-2222-4222-8222-222222222222',
                 guest_uuid='33333333-3333-4333-8333-333333333333', nvme_serial='SSD123'))


class Boundary(unittest.TestCase):
    def test_nonroot_refuses(self):
        with patch.object(M.os, 'getresuid', return_value=(1000, 1000, 1000)):
            receipt = M.stop_storage()
        self.assertFalse(receipt['success'])
        self.assertFalse(receipt['umount_attempted'])
        self.assertIn('actual root', receipt['error']['error'])

    def test_cli_rejects_flags(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            M.main(['--target', '/dev/sda'])

    @unittest.skipUnless(os.getresuid() == (0, 0, 0), 'complete native process census requires root')
    def test_actual_reference_census_refuses_held_regular_fd(self):
        # /run is a separate tmpfs; the held FD, rather than our overlay cwd,
        # creates the reference. All stat/readlink/maps operations stay native.
        with tempfile.TemporaryFile(dir='/run') as stream:
            with self.assertRaisesRegex(A.AdmissionError, 'process reference'):
                M.target_consumers(os.fstat(stream.fileno()).st_dev)


    def test_reference_census_permission_error_refuses(self):
        with patch.object(M.os, 'scandir', side_effect=PermissionError(errno.EACCES, 'denied')):
            with self.assertRaises(PermissionError):
                M.target_consumers(os.makedev(259, 5))

    def test_reference_census_disappearing_task_is_allowed(self):
        real_scandir = os.scandir
        process = SimpleNamespace(name='99999999', path='/proc/99999999')
        def scan(path):
            if path == '/proc':
                return nullcontext([process])
            return real_scandir(path)
        with patch.object(M.os, 'scandir', scan):
            result = M.target_consumers(os.makedev(259, 5))
        self.assertEqual(result, dict(processes=1, tasks=0, descriptors=0))

    def test_reference_census_count_and_time_are_bounded(self):
        process = SimpleNamespace(name='1', path='/proc/1')
        with patch.object(M.os, 'scandir', return_value=nullcontext([process])):
            with patch.object(M, 'PID_LIMIT', 0):
                with self.assertRaisesRegex(A.AdmissionError, 'process census limit'):
                    M.target_consumers(os.makedev(259, 5))
        with patch.object(M.os, 'scandir', return_value=nullcontext([process])):
            with patch.object(M.time, 'monotonic', side_effect=[0, 11]):
                with self.assertRaisesRegex(A.AdmissionError, 'census timeout'):
                    M.target_consumers(os.makedev(259, 5))


@unittest.skipUnless(os.getresuid() == (0, 0, 0), 'native protected RAM fixtures require root')
class Native(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        parent = self.stack.enter_context(tempfile.TemporaryDirectory(dir='/run'))
        self.runtime = Path(parent) / 'a733-admission'
        self.stack.enter_context(patch.object(A, 'RUNTIME_PARENT', parent))
        self.stack.enter_context(patch.object(A, 'RUNTIME', str(self.runtime)))
        self.boot = A._boot_id()
        self.admission = A.initialize(self.boot, 0, lambda: 'running')
        self.stack.callback(self.admission.close)
        self.admission.bootstrap_owner.open()
        self.admission.bootstrap_owner.lease.close()
        self.present = True
        self.events = []
        self.policy_close_bad = self.lock_close_bad = False
        self.snapshot_bad = self.pin_bad = False
        self.umount_result = 0
        self.umount_error = None
        self.unmount_removes = True
        fixture = self
        class Policy:
            value = copy.deepcopy(VALUE)
            def __init__(self, boot):
                fixture.events.append('policy')
                self.check()
            def check(self):
                fixture.events.append('policy-check')
            def close(self):
                fixture.events.append('policy-close')
                if fixture.policy_close_bad:
                    raise OSError('policy cleanup failed')
        self.snapshot = dict(disk={'serial': 'SSD123'}, guest=dict(canonical_path='/dev/nvme0n1p5',
            uuid=VALUE['storage']['guest_uuid'], major_minor='259:5'),
            state=dict(uuid=VALUE['storage']['state_uuid']), active={}, gpt={})
        self.pin = ([['exact-mount-row']], (os.makedev(259, 5), 123, 0o40711, 0, 0))
        def snapshot(args, mounted):
            self.events.append('snapshot-' + str(mounted))
            if self.snapshot_bad:
                raise ValueError('physical target mismatch')
            result = copy.deepcopy(self.snapshot)
            if not mounted and self.after_bad:
                result['gpt'] = {'changed': True}
            return result
        self.after_bad = False
        @contextmanager
        def lock(context, *, check):
            check()
            self.events.append('lock')
            try:
                yield
            finally:
                self.events.append('unlock')
                if self.lock_close_bad:
                    raise OSError('activation lock cleanup failed')
        def stop():
            self.assertEqual(self.admission._read()['reason'], 'shutdown')
            self.events.append('stop')
        def run(argv, **kwargs):
            self.events.append('umount')
            self.assertEqual(argv, ['/usr/bin/umount', '/run/a733-guests'])
            self.assertEqual(kwargs['timeout'], 30)
            self.assertFalse(kwargs['check'])
            A._held_lock(self.active_lease(), 'WRITE', os.getpid())
            if self.unmount_removes:
                self.present = False
            if self.umount_error:
                raise self.umount_error
            return SimpleNamespace(returncode=self.umount_result)
        self.leases = []
        real_shutdown = A.Admission.shutdown_lease
        def shutdown(admission):
            self.events.append('lease')
            lease = real_shutdown(admission)
            self.leases.append(lease)
            return lease
        self.active_lease = lambda: self.leases[-1].fd
        def census(device):
            self.events.append('census')
            self.assertEqual(device, os.makedev(259, 5))
            return dict(tasks=1)
        for target, name, replacement in (
            (M.BOOT, 'installed_guard', lambda boot: None),
            (M.POLICY, 'Policy', Policy), (M.ACTIVATE, 'host_namespace', lambda: None),
            (M.ACTIVATE, 'fixed_entries', lambda: self.pin[0] if self.present else []),
            (M.ACTIVATE, 'snapshot', snapshot), (M, 'mount_pin', lambda: self.pin),
            (M.ACTIVATE, 'activation_lock', lock),
            (M.ACTIVATE, 'checked_command', lambda name: '/usr/bin/' + name),
            (M.M, 'stop_managed', stop), (M.M, 'consumer_check', lambda: self.events.append('kvm')),
            (M.NORMAL, 'local_kvm_gid', lambda: 0), (M.NORMAL, 'manager_state', lambda: 'running'),
            (M, 'target_consumers', census), (M.subprocess, 'run', run),
            (A.Admission, 'shutdown_lease', shutdown)):
            self.stack.enter_context(patch.object(target, name, replacement))

    def closed(self):
        self.assertEqual(self.admission._read()['reason'], 'shutdown')
        self.assertEqual(self.admission._read()['state'], 'closed')
        self.assertTrue(all(lease.fd is None for lease in self.leases))

    def test_success_closes_then_stops_then_leases_and_unmounts_once(self):
        receipt = M.stop_storage()
        self.assertTrue(receipt['success'], receipt)
        self.assertTrue(receipt['umount_acknowledged'])
        self.assertEqual(receipt['mount_after'], 'absent')
        self.assertLess(self.events.index('stop'), self.events.index('lease'))
        self.assertLess(self.events.index('census'), self.events.index('umount'))
        self.assertEqual(self.events.count('umount'), 1)
        self.assertEqual(self.events[-1], 'policy-close')
        self.closed()

    def test_absent_mount_noop_needs_policy_without_runtime(self):
        self.present = False
        with patch.object(A.Admission, 'open_existing', side_effect=AssertionError('must not open')):
            receipt = M.stop_storage()
        self.assertTrue(receipt['success'], receipt)
        self.assertTrue(receipt['noop'])
        self.assertIn('policy', self.events)
        self.assertNotIn('stop', self.events)
        self.assertFalse(receipt['umount_attempted'])
        self.assertEqual(self.admission._read()['state'], 'open')

    def test_missing_policy_refuses_even_absent(self):
        self.present = False
        with patch.object(M.POLICY, 'Policy', side_effect=FileNotFoundError('policy')):
            receipt = M.stop_storage()
        self.assertFalse(receipt['success'])
        self.assertNotIn('stop', self.events)

    def test_existing_supervisor_shutdown_closure_works(self):
        self.admission.close_gate('shutdown')
        record = self.admission._read()
        receipt = M.stop_storage()
        self.assertTrue(receipt['success'], receipt)
        self.assertEqual(self.admission._read(), record)

    def test_live_shared_holder_refuses_after_stop(self):
        with self.admission.admit():
            receipt = M.stop_storage()
        self.assertFalse(receipt['success'])
        self.assertIn('stop', self.events)
        self.assertNotIn('umount', self.events)
        self.closed()

    def test_busy_unmount_no_retry_actual_present(self):
        self.umount_result = 32
        self.unmount_removes = False
        receipt = M.stop_storage()
        self.assertFalse(receipt['success'])
        self.assertFalse(receipt['umount_acknowledged'])
        self.assertEqual(receipt['mount_after'], 'present')
        self.assertEqual(self.events.count('umount'), 1)
        self.closed()

    def test_timeout_after_removal_reports_actual_absent_unacknowledged(self):
        self.umount_error = subprocess.TimeoutExpired('umount', 30)
        receipt = M.stop_storage()
        self.assertFalse(receipt['success'])
        self.assertEqual(receipt['mount_after'], 'absent')
        self.assertFalse(receipt['umount_acknowledged'])
        self.assertEqual(self.events.count('umount'), 1)
        self.closed()

    def test_positive_ack_with_mount_remaining_refuses(self):
        self.unmount_removes = False
        receipt = M.stop_storage()
        self.assertFalse(receipt['success'])
        self.assertTrue(receipt['umount_acknowledged'])
        self.closed()

    def test_physical_mismatch_refuses_before_unmount(self):
        self.snapshot_bad = True
        receipt = M.stop_storage()
        self.assertFalse(receipt['success'])
        self.assertFalse(receipt['umount_attempted'])
        self.closed()

    def test_changed_gpt_after_unmount_refuses(self):
        self.after_bad = True
        receipt = M.stop_storage()
        self.assertFalse(receipt['success'])
        self.assertTrue(receipt['umount_acknowledged'])
        self.closed()

    def test_kvm_consumer_refuses(self):
        with patch.object(M.M, 'consumer_check', side_effect=ValueError('actual KVM consumer')):
            receipt = M.stop_storage()
        self.assertFalse(receipt['success'])
        self.assertNotIn('umount', self.events)
        self.closed()

    def test_process_reference_refuses(self):
        with patch.object(M, 'target_consumers', side_effect=ValueError('actual p5 reference')):
            receipt = M.stop_storage()
        self.assertFalse(receipt['success'])
        self.assertNotIn('umount', self.events)
        self.closed()

    def test_cleanup_error_prevents_overall_success(self):
        self.policy_close_bad = True
        receipt = M.stop_storage()
        self.assertFalse(receipt['success'])
        self.assertEqual(receipt['cleanup_errors'][0]['resource'], 'policy')
        self.closed()

    def test_primary_and_lock_cleanup_receipts_survive(self):
        self.umount_result = 32
        self.lock_close_bad = True
        receipt = M.stop_storage()
        self.assertFalse(receipt['success'])
        nested = receipt['error']['receipt']
        self.assertIn('normal guest unmount failed', nested['primary_error']['error'])
        self.assertIn('activation lock cleanup failed', nested['cleanup_error']['error'])
        self.closed()

    def test_foreign_native_namespace_refuses_before_policy(self):
        with patch.object(M.ACTIVATE, 'host_namespace', side_effect=ValueError('foreign namespace')):
            receipt = M.stop_storage()
        self.assertFalse(receipt['success'])
        self.assertNotIn('policy', self.events)

    def test_umount_observation_unknown_never_claims_success(self):
        with patch.object(M, 'observe_mount', return_value='unknown'):
            receipt = M.stop_storage()
        self.assertFalse(receipt['success'])
        self.assertTrue(receipt['umount_acknowledged'])
        self.assertEqual(receipt['mount_after'], 'unknown')
        self.closed()

    def test_mountpoint_inode_changes_before_unmount_refuses(self):
        pins = [self.pin, self.pin, (self.pin[0], (self.pin[1][0], 456, 0o40711, 0, 0))]
        with patch.object(M, 'mount_pin', side_effect=pins):
            receipt = M.stop_storage()
        self.assertFalse(receipt['success'])
        self.assertFalse(receipt['umount_attempted'])
        self.closed()

    def test_shutdown_publish_failure_retains_publication_receipt(self):
        real_close = A.Admission.close_gate
        def uncertain(admission, reason):
            real_close(admission, reason)
            raise A.PublicationError('directory-fsync', True)
        with patch.object(A.Admission, 'close_gate', uncertain):
            receipt = M.stop_storage()
        self.assertFalse(receipt['success'])
        self.assertEqual(receipt['error']['stage'], 'directory-fsync')
        self.assertTrue(receipt['error']['uncertain'])
        self.assertTrue(receipt['shutdown_closed'])
        self.assertFalse(receipt['umount_attempted'])
        self.closed()

    def test_stop_failure_leaves_shutdown_closed(self):
        with patch.object(M.M, 'stop_managed', side_effect=ValueError('stop failed')):
            receipt = M.stop_storage()
        self.assertFalse(receipt['success'])
        self.assertNotIn('lease', self.events)
        self.closed()


if __name__ == '__main__':
    unittest.main()
