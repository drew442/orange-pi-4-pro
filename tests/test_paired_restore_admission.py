"""Native readonly-flock composition; physical/p5 boundaries are explicit mocks.

Requires root for the actual tmpfs admission fixture. No physical devices, real
KVM or storage authority are involved. The core archive fixture uses tiny files.
"""
import contextlib
import os
from pathlib import Path
import unittest
from unittest import mock

import test_known_host_state_restore as R
import test_offline_guest_admission as OA

M, W = R.M, OA.W


@unittest.skipUnless(os.getresuid() == (0, 0, 0), 'requires actual root and /run tmpfs')
class PairedAdmissionTests(unittest.TestCase):
    # Reuse only fixture helpers; primitive tests run in their own module.
    initialize = OA.OfflineTests.initialize

    def setUp(self):
        OA.OfflineTests.setUp(self)
        self.r = R.RestoreTests()
        self.r.setUp()
        self.addCleanup(self.r.doCleanups)
        self.r.args.expected_boot_id = self.boot
        for name in ('protected_ram_path', 'fresh_output'):
            p = mock.patch.object(W, name, getattr(self.r.boot, name))
            p.start()
            self.addCleanup(p.stop)
        self.r.boot = W
        self.r.admission_context = contextlib.nullcontext
        self.events = []
        self.active = None
        original_acquire = W.OfflineAdmission.acquire
        def acquire(expected):
            self.redirect_run = True
            try:
                guard = original_acquire(expected)
            finally:
                self.redirect_run = False
            self.active = guard
            self.events.append('exclusion')
            return guard
        p = mock.patch.object(W.OfflineAdmission, 'acquire', side_effect=acquire)
        p.start()
        self.addCleanup(p.stop)
        self.probe = mock.patch.object(W, 'refuse_managed_p5', side_effect=self.clean_probe)
        self.probe_mock = self.probe.start()
        self.addCleanup(self.probe.stop)
        original_claims = self.r.claims
        @contextlib.contextmanager
        def claims(*args):
            if self.r.args.operation == 'apply':
                self.active.check()
                self.events.append('targets')
            with original_claims(*args) as value:
                try:
                    yield value
                finally:
                    if self.r.args.operation == 'apply':
                        self.active.check()
                        self.events.append('target-cleanup')
        self.r.claims = claims

    def clean_probe(self, guest_uuid, guard):
        self.assertEqual(guest_uuid, M.GUEST_UUID)
        self.assertIs(type(guard), W.OfflineAdmission)
        guard.check()
        self.events.append('p5-probe')
        return {'status': 'clean-marker-absence', 'hardware_acceptance': False,
                'storage_authority': False}

    def closed(self):
        admission = self.initialize()
        admission.bootstrap_owner.lease.close()
        return admission

    def test_authenticated_cohort_precedes_exclusion_probe_and_writes(self):
        self.closed()
        self.r.prepare_apply()
        self.assertIsNone(self.active)  # prepare never initializes/opens admission
        original_hash, original_write = M.hash_fd, os.write
        def hashed(fd, size, label):
            result = original_hash(fd, size, label)
            self.events.append('authenticated-' + label)
            return result
        def write(fd, data):
            if fd in self.r.parts.values():
                self.active.check()
                self.events.append('write')
            return original_write(fd, data)
        with mock.patch.object(M, 'hash_fd', side_effect=hashed), \
             mock.patch.object(M.os, 'write', side_effect=write):
            self.r.run_restore()
        self.assertEqual(self.events[:3], ['authenticated-Authenticate p2.img.gz',
            'authenticated-Authenticate p3.img.gz', 'authenticated-Authenticate p4.img.gz'])
        self.assertLess(self.events.index('exclusion'), self.events.index('p5-probe'))
        self.assertLess(self.events.index('p5-probe'), self.events.index('targets'))
        self.assertLess(self.events.index('targets'), self.events.index('write'))
        self.assertFalse(self.active.fds)
        self.assertEqual(self.r.report['status'], 'operations-complete-awaiting-terminal-receipt')

    def test_invalid_archive_never_acquires_exclusion(self):
        self.r.prepare_apply()
        path = Path(self.r.args.backup_index).parent / 'p4.img.gz'
        path.write_bytes(path.read_bytes() + b'x')
        with self.assertRaises(M.Refusal):
            self.r.run_restore()
        self.assertIsNone(self.active)
        self.probe_mock.assert_not_called()
        self.r.assert_untouched()

    def test_p5_failure_receipt_retained_before_target_claims(self):
        self.r.prepare_apply()
        for reason in ('missing p5', 'dirty ext4', 'unknown feature', 'lifecycle marker'):
            with self.subTest(reason=reason):
                error = W.Refusal(reason)
                error.receipt = {'status': 'refused', 'block_ro': 'protected', 'error': reason}
                self.probe_mock.side_effect = error
                with self.assertRaises(W.Refusal):
                    self.r.run_restore()
                self.assertEqual(self.r.report['p5_legacy_restore_probe'], error.receipt)
                self.assertNotIn('targets', self.events)
                self.assertFalse(self.active.fds)
                self.r.assert_untouched()

    def test_open_gate_and_live_lease_refuse_before_probe(self):
        admission = self.initialize()
        self.r.prepare_apply()
        with self.assertRaises(BlockingIOError):
            self.r.run_restore()
        admission.bootstrap_owner.lease.close()
        data = (self.path / 'gate.json').read_bytes().replace(b'"closed"', b'"open"')
        (self.path / 'gate.json').write_bytes(data)
        with self.assertRaises(W.Refusal):
            self.r.run_restore()
        self.probe_mock.assert_not_called()
        self.r.assert_untouched()

    def test_non_native_entry_refuses_before_probe(self):
        self.r.prepare_apply()
        with mock.patch.object(W, 'host_context', side_effect=W.Refusal('native entry required')), \
             self.assertRaises(W.Refusal):
            self.r.run_restore()
        self.probe_mock.assert_not_called()
        self.r.assert_untouched()

    def test_stale_gate_stops_actual_short_write_stream(self):
        self.closed()
        self.r.prepare_apply()
        original_write = os.write
        def write(fd, data):
            if fd == self.r.parts[2]:
                count = original_write(fd, data[:7])
                path = self.path / 'gate.json'
                path.write_bytes(path.read_bytes().replace(b'"generation":0', b'"generation":1'))
                # Same-byte writes also alter the complete retained stat identity.
                return count
            return original_write(fd, data)
        with mock.patch.object(M.os, 'write', side_effect=write), self.assertRaises(W.Refusal):
            self.r.run_restore()
        state = self.r.report['partitions']['2']
        self.assertEqual(state['accepted_bytes'], 7)
        self.assertTrue(state['may_have_modified'])
        self.assertFalse(state['stream_complete'])
        self.assertFalse(self.r.report['partitions']['3'].get('attempted', False))
        self.assertFalse(self.active.fds)

    def test_exclusion_survives_target_and_source_cleanup(self):
        self.closed()
        self.r.prepare_apply()
        original_close = os.close
        observed = []
        def close(fd):
            path = os.readlink(f'/proc/self/fd/{fd}')
            if path.startswith(str(self.r.base)) and (path.endswith('.img.gz') or
                                                     path.endswith('index.json') or
                                                     path.endswith('plan.json')):
                self.active.check()
                observed.append(path)
            return original_close(fd)
        with mock.patch.object(M.os, 'close', side_effect=close):
            self.r.run_restore()
        self.assertEqual(len(observed), 5)
        self.assertFalse(self.active.fds)

    def test_guard_close_failure_preserves_written_fields_without_success(self):
        self.closed()
        self.r.prepare_apply()
        original_close = W.OfflineAdmission.close
        def close(guard):
            original_close(guard)
            raise OSError('injected admission close acknowledgement failure')
        with mock.patch.object(W.OfflineAdmission, 'close', close), self.assertRaises(OSError):
            self.r.run_restore()
        self.assertEqual(self.r.report['status'], 'writing-nontransactional')
        for n in self.r.sizes:
            state = self.r.report['partitions'][str(n)]
            self.assertTrue(state['may_have_modified'] and state['flush_complete'])
            self.assertEqual(state['accepted_bytes'], self.r.sizes[n])
            self.assertFalse(state['readback_complete'])

    def test_prepare_absent_runtime_does_not_create_init_or_probe(self):
        self.r.prepare_apply()
        self.assertFalse((Path(self.parent) / W.OfflineAdmission.INIT).exists())
        self.assertFalse(self.path.exists())
        self.probe_mock.assert_not_called()
        self.r.assert_untouched()

    def test_only_entry_and_final_census_in_restore_loop(self):
        self.closed()
        self.r.prepare_apply()
        with mock.patch.object(W, 'offline_consumer_check') as census:
            self.r.run_restore()
        # Probe is explicitly substituted, so its own boundary scans are excluded.
        self.assertEqual(census.call_count, 2)

    def test_source_close_failure_retains_exclusion_and_prevents_completion(self):
        self.closed()
        self.r.prepare_apply()
        original_close = os.close
        def close(fd):
            path = os.readlink(f'/proc/self/fd/{fd}')
            if path == str(self.r.base / 'p4.img.gz'):
                self.active.check()
                original_close(fd)
                raise OSError('source close acknowledgement failed')
            return original_close(fd)
        with mock.patch.object(M.os, 'close', side_effect=close), self.assertRaises(OSError):
            self.r.run_restore()
        self.assertEqual(self.r.report['status'], 'writing-nontransactional')
        self.assertTrue(self.r.report['partitions']['4']['flush_complete'])
        self.assertFalse(self.active.fds)

    def test_actual_file_target_opens_and_closes_inside_exclusion(self):
        self.closed()
        self.r.prepare_apply()
        disk_path = self.r.private('namespace', b'disk')
        for number, size in self.r.sizes.items():
            self.r.private(f'namespacep{number}', b'X' * size)
        real_read, real_open, real_close = W.read_text, os.open, os.close
        observed = []
        def opened(path, flags, *args, **kwargs):
            if str(path).startswith(str(disk_path)):
                self.assertIn('p5-probe', self.events)
                self.active.check()
                observed.append(('open', str(path)))
            return real_open(path, flags, *args, **kwargs)
        def closed(fd):
            path = os.readlink(f'/proc/self/fd/{fd}')
            if path.startswith(str(disk_path)):
                self.active.check()
                observed.append(('close', path))
            return real_close(fd)
        self.r.claims = M.targets
        before = {'namespace': (0, 0, 0)}
        with mock.patch.object(W, 'DISK', str(disk_path)), \
             mock.patch.object(W, 'target_identity', return_value=(before, set())), \
             mock.patch.object(W, 'validate_open_target'), \
             mock.patch.object(W, 'idle_target'), \
             mock.patch.object(W, 'verify_gpt'), \
             mock.patch.object(W, 'read_text', side_effect=lambda p:
                 '0 0' if p.startswith('/sys/class/block/') else real_read(p)), \
             mock.patch.object(M, 'context', return_value=[]), \
             mock.patch.object(M.os, 'open', side_effect=opened), \
             mock.patch.object(M.os, 'close', side_effect=closed):
            self.r.run_restore()
        self.assertEqual(sum(kind == 'open' for kind, _ in observed), 4)
        self.assertEqual(sum(kind == 'close' for kind, _ in observed), 4)
        for number in self.r.sizes:
            self.assertEqual(Path(f'{disk_path}p{number}').read_bytes(), self.r.raw[number])
        self.assertFalse(self.active.fds)

    def test_post_acquire_check_failure_closes_retained_exclusion(self):
        self.closed()
        self.r.prepare_apply()
        acquire = W.OfflineAdmission.acquire.side_effect
        def changed(expected):
            guard = acquire(expected)
            path = self.path / 'gate.json'
            path.write_bytes(path.read_bytes())
            return guard
        with mock.patch.object(W.OfflineAdmission, 'acquire', side_effect=changed), \
             self.assertRaises(W.Refusal):
            self.r.run_restore()
        self.assertFalse(self.active.fds)
        self.probe_mock.assert_not_called()
        self.r.assert_untouched()


if __name__ == '__main__':
    unittest.main()
