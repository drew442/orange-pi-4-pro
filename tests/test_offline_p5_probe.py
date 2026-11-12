"""Software probe checks: physical NVMe, block ioctls and kernel mounts substituted.

Root cases use actual /run tmpfs, root-owned descriptors and native admission
flocks. ARM/kernel/native entry remains explicitly mocked by the admission fixture.
No physical disk, hardware KVM or deployment acceptance is exercised.
"""
import errno
import os
from pathlib import Path
import struct
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

import test_offline_guest_admission as OA
W = OA.W
GUEST_UUID = '298b281a-d10e-44eb-bdb9-285e27eaafad'


def superblock():
    data = bytearray(1024)
    for offset, value in ((4, W.P5_BYTES // 4096), (0x18, 2), (0x5c, 0x3c),
                          (0x60, 0x2c2), (0x64, 0x46b), (0xe0, 8)):
        struct.pack_into('<I', data, offset, value)
    struct.pack_into('<H', data, 0x38, 0xef53)
    struct.pack_into('<H', data, 0x3a, 1)
    data[0x68:0x78] = uuid.UUID(GUEST_UUID).bytes
    data[0x78:0x82] = b'A733GUESTS'
    return data


class SuperblockTests(unittest.TestCase):
    def test_known_clean_profile_and_supported_feature_subsets(self):
        W._p5_superblock(superblock(), GUEST_UUID)
        data = superblock()
        for offset, value in ((0x5c, 4), (0x60, 2), (0x64, 3)):
            struct.pack_into('<I', data, offset, value)
        W._p5_superblock(data, GUEST_UUID)

    def test_dirty_orphan_unknown_geometry_journal_and_short_refuse(self):
        for offset, value, fmt in ((0x3a, 0, '<H'), (0x3a, 2, '<H'), (0x3a, 5, '<H'),
                                   (0x3a, 0x8001, '<H'), (0x60, 0x2c6, '<I'),
                                   (0xe8, 42, '<I'), (0x5c, 0x103c, '<I'),
                                   (0x64, 0x1046b, '<I'), (0x60, 0x800002c2, '<I'),
                                   (0x5c, 0x8000003c, '<I'), (0x64, 0x8000046b, '<I'),
                                   (0x5c, 0x38, '<I'), (0x18, 1, '<I'), (4, 1, '<I'),
                                   (0x150, 1, '<I'), (0xe0, 9, '<I'), (0xe4, 1, '<I'),
                                   (0xd0, 1, '<I')):
            with self.subTest(offset=offset, value=value):
                data = superblock()
                struct.pack_into(fmt, data, offset, value)
                with self.assertRaises(W.Refusal):
                    W._p5_superblock(data, GUEST_UUID)
        with self.assertRaises(W.Refusal):
            W._p5_superblock(superblock()[:-1], GUEST_UUID)
        with self.assertRaises(W.Refusal):
            W._p5_superblock(superblock(), str(uuid.uuid4()))

    def test_no_caller_constructed_or_subclass_guard(self):
        for fake in (None, object(), type('Fake', (), {'check_consumers': lambda s: None})()):
            with self.assertRaisesRegex(W.Refusal, 'actual offline admission'):
                W.refuse_managed_p5(GUEST_UUID, fake)


class HeldDescriptorTests(unittest.TestCase):
    def test_real_fd_short_reads_node_alias_and_dual_ro_readback(self):
        from types import SimpleNamespace as Info
        with tempfile.TemporaryFile() as image:
            image.write(bytes(1024) + superblock())
            image.flush()
            node = Info(st_mode=0o60600, st_dev=1, st_ino=2, st_rdev=os.makedev(7, 250))
            before = {'nvme0n1p5': (node.st_dev, node.st_ino, node.st_rdev)}
            values = {W.BLKGETSIZE64: W.P5_BYTES, W.BLKSSZGET: 512,
                      W.BLKPBSZGET: 512, W.BLKROGET: 1}
            with patch.object(W.os, 'fstat', return_value=node), patch.object(
                    W.os, 'lstat', return_value=node), patch.object(
                    W, 'block_integer', side_effect=lambda fd, cmd, fmt: values[cmd]), patch.object(
                    W, 'read_text', return_value='1'):
                W._p5_fd(image.fileno(), before, GUEST_UUID, 1)
                values[W.BLKROGET] = 0
                with self.assertRaises(W.Refusal):
                    W._p5_fd(image.fileno(), before, GUEST_UUID, 1)
                values[W.BLKROGET] = 1
                with patch.object(W, 'read_text', return_value='0'), self.assertRaises(W.Refusal):
                    W._p5_fd(image.fileno(), before, GUEST_UUID, 1)
                alias = Info(st_mode=0o120777, st_dev=1, st_ino=2, st_rdev=node.st_rdev)
                with patch.object(W.os, 'lstat', return_value=alias), self.assertRaises(W.Refusal):
                    W._p5_fd(image.fileno(), before, GUEST_UUID, 1)
                replacement = Info(st_mode=node.st_mode, st_dev=1, st_ino=99, st_rdev=node.st_rdev)
                with patch.object(W.os, 'lstat', return_value=replacement), self.assertRaises(W.Refusal):
                    W._p5_fd(image.fileno(), before, GUEST_UUID, 1)
                image.truncate(1100)
                with self.assertRaisesRegex(W.Refusal, 'short p5'):
                    W._p5_fd(image.fileno(), before, GUEST_UUID, 1)

    def test_mount_full_root_options_and_metadata_are_required(self):
        from types import SimpleNamespace as Info
        point = '/run/owned-p5-fixture'
        device = (7, 250)
        row = dict(path=point, dev=device, fstype='ext4',
                   options=['ro', 'nosuid', 'nodev', 'noexec', 'norecovery'])
        root = Info(st_mode=0o40700, st_uid=0, st_gid=0, st_dev=os.makedev(*device))
        raw = '1 2 7:250 / ' + point + ' ro,nosuid,nodev,noexec - ext4 /dev/loop0 ro,norecovery'
        with patch.object(W, 'mountinfo', return_value=[row]), patch.object(
                W, 'read_text', return_value=raw), patch.object(W.os, 'lstat', return_value=root):
            W._p5_mount_identity(point, device)
            for bad in (dict(row, options=['ro']), dict(row, dev=(7, 251)),
                        dict(row, options=row['options'] + ['rw']), dict(row, fstype='tmpfs')):
                with patch.object(W, 'mountinfo', return_value=[bad]), self.assertRaises(W.Refusal):
                    W._p5_mount_identity(point, device)
            with patch.object(W, 'read_text', return_value=raw.replace('7:250 / ', '7:250 /subdir ')), self.assertRaises(W.Refusal):
                W._p5_mount_identity(point, device)
            root.st_mode = 0o40755
            with self.assertRaises(W.Refusal):
                W._p5_mount_identity(point, device)


@unittest.skipUnless(os.getresuid() == (0, 0, 0), 'requires actual root and /run tmpfs')
class ProbeTests(unittest.TestCase):
    guard = OA.OfflineTests.guard

    def setUp(self):
        OA.OfflineTests.setUp(self)
        self.admission = self.guard()
        self.disk_path = Path(self.parent) / 'disk-substitute'
        self.p5_path = Path(self.parent) / 'p5-substitute'
        self.disk_path.touch(mode=0o600)
        self.p5_path.write_bytes(bytes(1024) + superblock())
        self.events = []
        self.ro = 0
        self.before = {'nvme0n1p5': (1, 2, os.makedev(7, 250)), 'nvme0n1': (1, 3, os.makedev(7, 249))}
        self.devices = {(7, 249), (7, 250)}
        real_open = W.os.open
        def opened(path, flags, mode=0o777, *, dir_fd=None):
            if path == W.DISK:
                path = self.disk_path
            elif path == W.P5:
                path = self.p5_path
            return real_open(path, flags & ~os.O_EXCL, mode, dir_fd=dir_fd)
        def inventory(expected):
            self.events.append(('inventory', expected))
            W.require(self.ro == expected, 'fixture RO differs')
            return self.before, self.devices
        def validate(fd, before, guest_uuid, expected):
            self.events.append(('validate', expected))
            W.require(self.ro == expected, 'fixture block RO differs')
            W._p5_superblock(os.pread(fd, 1024, 1024), guest_uuid)
        def ioctl(fd, command, value=0, *unused):
            self.assertEqual(command, W.BLKROSET)
            self.ro = struct.unpack('=I', value)[0]
            self.events.append(('set-ro', self.ro))
        def child(*args):
            self.events.append(('child-mount', self.ro))
            self.assertEqual(self.ro, 1)
            return dict(marker_present=False, unmounted=True, error=None)
        for p in (patch.object(W.os, 'open', side_effect=opened),
                  patch.object(W, '_offline_inventory', side_effect=inventory),
                  patch.object(W, '_p5_fd', side_effect=validate),
                  patch.object(W, 'validate_open_target'),
                  patch.object(W, 'verify_gpt'),
                  patch.object(W, 'idle_target', side_effect=lambda *args: self.events.append(('idle', self.ro))),
                  patch.object(W.fcntl, 'ioctl', side_effect=ioctl),
                  patch.object(W, '_p5_run_child', side_effect=child)):
            p.start()
            self.addCleanup(p.stop)

    def test_clean_absence_block_ro_order_and_detached_proof(self):
        before_fds = len(os.listdir('/proc/self/fd'))
        result = W.refuse_managed_p5(GUEST_UUID, self.admission)
        self.assertEqual(result['status'], 'clean-marker-absence')
        self.assertFalse(result['storage_authority'])
        self.assertFalse(result['hardware_acceptance'])
        self.assertIsNone(result['point'])
        self.assertEqual(self.ro, 0)
        set_one = self.events.index(('set-ro', 1))
        mounted = self.events.index(('child-mount', 1))
        set_zero = self.events.index(('set-ro', 0))
        self.assertLess(set_one, mounted)
        self.assertIn(('validate', 1), self.events[set_one:mounted])
        self.assertLess(mounted, set_zero)
        self.assertIn(('idle', 1), self.events[mounted:set_zero])
        self.assertEqual(len(os.listdir('/proc/self/fd')), before_fds)
        self.admission.check()

    def test_dirty_refuses_before_protection_mount_or_writes(self):
        with self.p5_path.open('r+b') as target:
            target.seek(1024 + 0x3a)
            target.write(b'\0\0')
        with self.assertRaises(W.Refusal) as caught:
            W.refuse_managed_p5(GUEST_UUID, self.admission)
        self.assertEqual(caught.exception.receipt['block_ro'], 'unchanged')
        self.assertNotIn(('set-ro', 1), self.events)
        self.assertFalse(any(event[0] == 'child-mount' for event in self.events))

    def test_present_marker_refuses_after_safe_original_flag_restore(self):
        with patch.object(W, '_p5_run_child', return_value=dict(marker_present=True, unmounted=True, error=None)):
            with self.assertRaisesRegex(W.Refusal, 'managed p5 marker') as caught:
                W.refuse_managed_p5(GUEST_UUID, self.admission)
        self.assertEqual(self.ro, 0)
        self.assertEqual(caught.exception.receipt['block_ro'], 'restored-original-zero')
        self.assertIsNone(caught.exception.receipt['point'])

    def test_unmount_uncertainty_or_missing_receipt_never_clears_ro(self):
        cases = [dict(marker_present=False, unmounted=False, error='unmount failed'),
                 W.Refusal('lost child receipt')]
        for case in cases:
            self.ro = 0
            self.events.clear()
            with self.subTest(case=case), patch.object(W, '_p5_run_child', side_effect=
                    case if isinstance(case, Exception) else lambda *a: case):
                with self.assertRaises(W.Refusal) as caught:
                    W.refuse_managed_p5(GUEST_UUID, self.admission)
                self.assertEqual(self.ro, 1)
                self.assertNotIn(('set-ro', 0), self.events)
                point = caught.exception.receipt['point']
                self.assertTrue(Path(point).exists())
                self.addCleanup(os.rmdir, point)

    def test_after_unmount_target_or_point_replacement_keeps_ro(self):
        def child(*args):
            self.before = dict(self.before, nvme0n1p5=(1, 99, os.makedev(7, 250)))
            return dict(marker_present=False, unmounted=True, error=None)
        with patch.object(W, '_p5_run_child', side_effect=child):
            with self.assertRaises(W.Refusal):
                W.refuse_managed_p5(GUEST_UUID, self.admission)
        self.assertEqual(self.ro, 1)
        self.assertNotIn(('set-ro', 0), self.events)

    def test_foreign_replaced_point_is_retained_without_deletion(self):
        def child(fd, before, guest_uuid, point, original):
            saved = point + '-saved'
            os.rename(point, saved)
            os.symlink(saved, point)
            self.addCleanup(os.rmdir, saved)
            self.addCleanup(os.unlink, point)
            return dict(marker_present=False, unmounted=True, error=None)
        with patch.object(W, '_p5_run_child', side_effect=child), self.assertRaises(W.Refusal) as caught:
            W.refuse_managed_p5(GUEST_UUID, self.admission)
        self.assertEqual(self.ro, 1)
        self.assertTrue(Path(caught.exception.receipt['point']).is_symlink())
        self.assertTrue(caught.exception.receipt['cleanup_errors'])


    def test_restore_failure_reasserts_protection_and_reports_refusal(self):
        real_validate = W._p5_fd.side_effect
        def validate(fd, before, guest_uuid, expected):
            if expected == 0 and ('set-ro', 0) in self.events:
                raise OSError(errno.EIO, 'restore readback lost')
            return real_validate(fd, before, guest_uuid, expected)
        with patch.object(W, '_p5_fd', side_effect=validate), self.assertRaises(W.Refusal) as caught:
            W.refuse_managed_p5(GUEST_UUID, self.admission)
        self.assertEqual(self.ro, 1)
        self.assertEqual(caught.exception.receipt['block_ro'], 'protected-after-restore-failure')
        self.assertEqual([event for event in self.events if event[0] == 'set-ro'],
                         [('set-ro', 1), ('set-ro', 0), ('set-ro', 1)])

    def test_set_ro_lost_ack_retains_uncertain_protection(self):
        def ioctl(fd, request, value):
            self.ro = struct.unpack('=I', value)[0]
            raise OSError(errno.EIO, 'protection acknowledgement lost')
        with patch.object(W.fcntl, 'ioctl', side_effect=ioctl), self.assertRaises(W.Refusal) as caught:
            W.refuse_managed_p5(GUEST_UUID, self.admission)
        self.assertEqual(self.ro, 1)
        self.assertEqual(caught.exception.receipt['block_ro'], 'uncertain')
        self.assertFalse(any(event[0] == 'child-mount' for event in self.events))


@unittest.skipUnless(os.getresuid() == (0, 0, 0), 'requires actual root and /run tmpfs')
class ChildProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='a733-p5-child-', dir='/run')
        self.addCleanup(self.tmp.cleanup)
        self.point = self.tmp.name
        self.device = (7, 250)
        self.before = {'nvme0n1p5': (1, 2, os.makedev(*self.device))}
        self.mounted = False
        self.events = []
        def mount(point):
            self.mounted = True
            self.events.append('mount')
        def unmount(point):
            self.mounted = False
            self.events.append('unmount')
        def rows():
            return [dict(path=self.point, dev=self.device)] if self.mounted else []
        for p in (patch.object(W, '_p5_private_namespace'),
                  patch.object(W, '_p5_fd'), patch.object(W, '_p5_mount_identity'),
                  patch.object(W, '_p5_mount', side_effect=mount),
                  patch.object(W, '_p5_unmount', side_effect=unmount),
                  patch.object(W, 'mountinfo', side_effect=rows)):
            p.start()
            self.addCleanup(p.stop)

    def probe(self):
        return W._p5_child_probe(-1, self.before, GUEST_UUID, self.point, W.identity(os.lstat(self.point)))

    def test_absence_and_any_type_marker_including_dangling_symlink(self):
        self.assertEqual(self.probe(), dict(marker_present=False, unmounted=True, error=None))
        marker = Path(self.point) / '.a733-lifecycle'
        for kind in ('dir', 'file', 'symlink'):
            if kind == 'dir':
                marker.mkdir()
            elif kind == 'file':
                marker.touch()
            else:
                marker.symlink_to('missing')
            result = self.probe()
            self.assertTrue(result['marker_present'])
            self.assertTrue(result['unmounted'])
            if kind == 'dir':
                marker.rmdir()
            else:
                marker.unlink()

    def test_mount_succeeded_but_ack_lost_reconciles_and_unmounts(self):
        def mount(point):
            self.mounted = True
            raise OSError(errno.EIO, 'lost mount acknowledgement')
        with patch.object(W, '_p5_mount', side_effect=mount):
            result = self.probe()
        self.assertTrue(result['unmounted'])
        self.assertIsNone(result['marker_present'])
        self.assertIn('lost mount acknowledgement', result['error'])
        self.assertIn('unmount', self.events)

    def test_mount_never_observed_or_unmount_failure_remains_uncertain(self):
        with patch.object(W, '_p5_mount', side_effect=OSError(errno.EIO, 'mount failed')):
            self.assertFalse(self.probe()['unmounted'])
        with patch.object(W, '_p5_unmount', side_effect=OSError(errno.EBUSY, 'busy')):
            self.assertFalse(self.probe()['unmounted'])
        self.mounted = False

    def test_unmount_lost_ack_never_claims_proved_cleanup(self):
        def unmount(point):
            self.mounted = False
            raise OSError(errno.EIO, 'unmount acknowledgement lost')
        with patch.object(W, '_p5_unmount', side_effect=unmount):
            result = self.probe()
        self.assertFalse(result['unmounted'])
        self.assertIn('cleanup uncertain', result['error'])

    def test_invalid_or_oversized_child_receipts_refuse_without_fd_leak(self):
        before = len(os.listdir('/proc/self/fd'))
        for result in ({'bogus': True}, dict(marker_present=False, unmounted=True, error='x' * 3000)):
            with patch.object(W, '_p5_child_probe', return_value=result), self.assertRaises(W.Refusal):
                W._p5_run_child(-1, self.before, GUEST_UUID, self.point, ())
        self.assertEqual(len(os.listdir('/proc/self/fd')), before)


    def test_private_child_cannot_change_parent_mount_namespace(self):
        before = os.stat('/proc/self/ns/mnt')
        result = dict(marker_present=False, unmounted=True, error=None)
        # Native fork/pipe/wait executes; kernel mount operations remain mocked.
        with patch.object(W, '_p5_child_probe', return_value=result):
            self.assertEqual(W._p5_run_child(-1, self.before, GUEST_UUID, self.point, ()), result)
        after = os.stat('/proc/self/ns/mnt')
        self.assertEqual((before.st_dev, before.st_ino), (after.st_dev, after.st_ino))

    def test_native_child_timeout_is_bounded_and_known_child_reaped(self):
        def stalled(*args):
            time.sleep(2)
        with patch.object(W, '_p5_child_probe', side_effect=stalled), patch.object(W, 'P5_PROBE_TIMEOUT', 0.05):
            start = time.monotonic()
            with self.assertRaisesRegex(W.Refusal, 'timeout'):
                W._p5_run_child(-1, self.before, GUEST_UUID, self.point, ())
            self.assertLess(time.monotonic() - start, 1)
        with self.assertRaises(ChildProcessError):
            os.waitpid(-1, os.WNOHANG)


if __name__ == '__main__':
    unittest.main()
