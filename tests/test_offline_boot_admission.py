"""Actual root/tmpfs/flock BOOT integration; A733/block classification mocked.

Ordinary files receive test writes. Native RAM/kernel/entry checks are explicitly
mocked by the admission fixture. These tests confer no storage or hardware proof.
"""
import fcntl
import hashlib
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import test_offline_guest_admission as OA
W, A = OA.W, OA.A


@unittest.skipUnless(os.getresuid() == (0, 0, 0), 'requires actual root and /run tmpfs')
class BootAdmissionTests(unittest.TestCase):
    initialize = OA.OfflineTests.initialize

    def setUp(self):
        OA.OfflineTests.setUp(self)
        self.source = Path(self.parent) / 'source'
        self.part = Path(self.parent) / 'boot-substitute'
        self.disk = Path(self.parent) / 'disk-substitute'
        self.source.write_bytes(b'new')
        self.source.chmod(0o600)
        self.part.write_bytes(b'old')
        self.disk.write_bytes(b'GPT')
        self.guard = None
        self.tagged = {}
        self.closed = []
        self.census_calls = 0
        original_acquire = W.OfflineAdmission.acquire
        original_open, original_close = W.os.open, W.os.close
        original_census = W.offline_consumer_check
        def acquire(boot):
            self.redirect_run = True
            try:
                self.guard = original_acquire(boot)
            finally:
                self.redirect_run = False
            return self.guard
        def opened(path, flags, mode=0o777, *, dir_fd=None):
            tag = None
            if path == W.TARGET:
                path, tag = self.part, 'part'
            elif path == W.DISK:
                path, tag = self.disk, 'disk'
            elif str(path) == str(self.source):
                tag = 'source'
            if tag is not None:
                self.assertIsNotNone(self.guard)
                self.guard.check()
                self.assertEqual(fcntl.fcntl(self.guard.init_fd, fcntl.F_GETFL) & os.O_ACCMODE, os.O_RDONLY)
                if self.guard.leases is not None:
                    self.guard._held(self.guard.leases)
            fd = original_open(path, flags, mode, dir_fd=dir_fd)
            if tag is not None:
                self.tagged[fd] = tag
            return fd
        def closed(fd):
            tag = self.tagged.pop(fd, None)
            if tag is not None:
                self.guard._held(self.guard.init_fd)
                if self.guard.leases is not None:
                    self.guard._held(self.guard.leases)
                self.closed.append(tag)
            return original_close(fd)
        def census():
            self.census_calls += 1
            return original_census()
        for p in (patch.object(W.OfflineAdmission, 'acquire', side_effect=acquire),
                  patch.object(W.os, 'open', side_effect=opened),
                  patch.object(W.os, 'close', side_effect=closed),
                  patch.object(W, 'offline_consumer_check', side_effect=census),
                  patch.object(W, 'SOURCE_BYTES', 3),
                  patch.object(W, 'NEW_SHA256', hashlib.sha256(b'new').hexdigest()),
                  patch.object(W, 'OLD_SHA256', hashlib.sha256(b'old').hexdigest()),
                  patch.object(W, 'validate_open_target'), patch.object(W, 'idle_target'),
                  patch.object(W, 'target_identity', return_value=({}, set())),
                  patch.object(W, 'verify_gpt'), patch.object(W.fcntl, 'ioctl')):
            p.start()
            self.addCleanup(p.stop)

    def apply(self, rollback=False):
        return W.apply_payload(str(self.source), self.boot, self.mounts, rollback=rollback, readback=True)

    def test_absent_guard_held_before_claims_through_cleanup_and_two_censuses(self):
        result = self.apply()
        self.assertEqual(result['status'], 'write-complete')
        self.assertEqual(self.part.read_bytes(), b'new')
        self.assertEqual(self.closed, ['part', 'disk', 'source'])
        self.assertEqual(self.census_calls, 2)
        with self.assertRaisesRegex(W.Refusal, 'closed'):
            self.guard.check()

    def test_closed_actual_lease_allows_rollback_without_opening_gate(self):
        a = self.initialize()
        a.bootstrap_owner.lease.close()
        original = (self.path / 'gate.json').read_bytes()
        self.part.write_bytes(b'new')
        self.source.write_bytes(b'old')
        result = self.apply(rollback=True)
        self.assertEqual(result['status'], 'rollback-complete')
        self.assertEqual(self.part.read_bytes(), b'old')
        self.assertEqual((self.path / 'gate.json').read_bytes(), original)
        self.assertEqual(self.census_calls, 2)

    def test_open_or_live_actual_ex_and_manual_sh_refuse_before_source_claim(self):
        a = self.initialize()
        with self.assertRaises(BlockingIOError):
            self.apply()
        self.assertFalse(self.tagged)
        a.bootstrap_owner.open()
        a.bootstrap_owner.lease.close()
        with self.assertRaisesRegex(W.Refusal, 'closed'):
            self.apply()
        owner = a.close_gate()
        fd = os.open(self.path / 'leases.lock', os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                self.apply()
        finally:
            os.close(fd)
        self.assertIsNotNone(owner)
        self.assertEqual(self.part.read_bytes(), b'old')
        self.assertFalse(self.closed)

    def test_bad_native_entry_or_actual_kvm_census_refuses_before_claims(self):
        with patch.object(W, 'host_context', side_effect=W.Refusal('bad native context')):
            with self.assertRaisesRegex(W.Refusal, 'native context'):
                self.apply()
        with patch.object(W, 'offline_consumer_check', side_effect=W.Refusal('actual KVM consumer remains')):
            with self.assertRaisesRegex(W.Refusal, 'KVM consumer'):
                self.apply()
        self.assertFalse(self.closed)
        self.assertEqual(self.part.read_bytes(), b'old')

    def test_replaced_gate_after_source_authentication_refuses_before_target_write(self):
        a = self.initialize()
        a.bootstrap_owner.lease.close()
        original = W.open_source
        def source(*args):
            result = original(*args)
            # Root may supersede a closure despite EX custody. Its new nonce is
            # never adopted by the already retained offline guard.
            record = a._read()
            record['reason'] = 'shutdown'
            a._publish(record)
            return result
        # Cleanup checks in the instrumentation should record ownership retained,
        # rather than require the now deliberately invalid gate to remain valid.
        with patch.object(W, 'open_source', side_effect=source), self.assertRaises(W.Refusal):
            self.apply()
        self.assertEqual(self.part.read_bytes(), b'old')
        self.assertEqual(self.census_calls, 1)

    def test_chunk_callback_stops_later_writes_without_full_census(self):
        a = self.initialize()
        a.bootstrap_owner.lease.close()
        original_write = os.write
        writes = []
        def written(fd, payload):
            count = original_write(fd, payload)
            if self.tagged.get(fd) == 'part':
                writes.append(count)
                record = a._read()
                record['reason'] = 'shutdown'
                a._publish(record)
            return count
        with patch.object(W, 'CHUNK', 1), patch.object(W.os, 'write', side_effect=written), self.assertRaises(W.Refusal):
            self.apply()
        self.assertEqual(writes, [1])
        self.assertEqual(self.part.read_bytes(), b'nld')
        self.assertEqual(self.census_calls, 1)

    def test_guard_close_lost_ack_prevents_success_after_complete_write(self):
        real_close = W.OfflineAdmission.close
        def closed(guard):
            real_close(guard)
            raise OSError('guard close acknowledgement lost')
        with patch.object(W.OfflineAdmission, 'close', new=closed), self.assertRaisesRegex(OSError, 'acknowledgement'):
            self.apply()
        self.assertEqual(self.part.read_bytes(), b'new')
        self.assertEqual(self.closed, ['part', 'disk', 'source'])
        self.assertEqual(self.census_calls, 2)
        self.assertFalse(self.guard.fds)

    def test_final_kvm_census_failure_prevents_success_after_owned_cleanup(self):
        original = W.offline_consumer_check.side_effect
        def census():
            original()
            if self.census_calls == 2:
                self.assertEqual(self.closed, ['part', 'disk', 'source'])
                raise W.Refusal('final actual KVM consumer remains')
        with patch.object(W, 'offline_consumer_check', side_effect=census), self.assertRaisesRegex(W.Refusal, 'final actual KVM'):
            self.apply()
        self.assertEqual(self.part.read_bytes(), b'new')
        self.assertFalse(self.guard.fds)

    def test_main_keeps_write_uncertainty_on_lost_guard_close_ack(self):
        import io
        import json
        from contextlib import redirect_stdout
        report = Path(self.parent) / 'report.json'
        real_close = W.OfflineAdmission.close
        def closed(guard):
            real_close(guard)
            raise OSError('guard close acknowledgement lost')
        output = io.StringIO()
        with patch.object(W.OfflineAdmission, 'close', new=closed), patch.object(
                W, 'read_receipt', return_value={'remote_host': 'software-fixture'}), redirect_stdout(output):
            result = W.main(['apply', str(self.source), '--expected-boot-id', self.boot,
                             '--backup-receipt', str(Path(self.parent) / 'receipt'),
                             '--backup-receipt-sha256', 'a' * 64, '--report', str(report)])
        self.assertEqual(result, 1)
        receipt = json.loads(report.read_bytes())
        self.assertEqual(receipt['status'], 'failed-or-refused')
        self.assertIsNone(receipt['bytes_written'])
        self.assertIn('close acknowledgement lost', receipt['error'])
        self.assertEqual(self.part.read_bytes(), b'new')


    def test_target_close_lost_ack_still_closes_disk_source_and_guard(self):
        real_close = os.close
        def closed(fd):
            tag = self.tagged.get(fd)
            real_close(fd)
            if tag == 'part':
                raise OSError('partition close acknowledgement lost')
        with patch.object(W.os, 'close', side_effect=closed), self.assertRaisesRegex(OSError, 'partition close'):
            self.apply()
        self.assertEqual(self.part.read_bytes(), b'new')
        self.assertFalse(self.guard.fds)
        self.assertEqual(self.census_calls, 1)
        # Every tagged FD was attempted exactly once, even after the first close
        # reported failure. The OS confirms all source/target descriptors closed.
        self.assertEqual(self.closed, ['part', 'disk', 'source'])


if __name__ == '__main__':
    unittest.main()
