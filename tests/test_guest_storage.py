"""Mount/sysfs fixtures exercise policy; they do not establish hardware acceptance."""
import contextlib
import importlib.util
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location(
    'guest_storage', Path(__file__).resolve().parents[1] / 'scripts/guest-storage.py')
GUARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GUARD)


class GuestStorageTests(unittest.TestCase):
    def setUp(self):
        self.context = contextlib.ExitStack()
        self.addCleanup(self.context.close)
        self.work = Path(self.context.enter_context(tempfile.TemporaryDirectory()))
        self.proc = self.work / 'proc'
        self.sys = self.work / 'sys'
        self.dev = self.work / 'dev'
        (self.proc / 'self/fdinfo').mkdir(parents=True)
        (self.sys / 'dev/block').mkdir(parents=True)
        self.dev.mkdir()
        self.namespace = self.sys / 'devices/pci/nvme/nvme0/nvme0n1'
        self.namespace.mkdir(parents=True)
        (self.namespace / 'dev').write_text('259:0\n')
        (self.namespace / 'removable').write_text('0\n')
        (self.sys / 'dev/block/259:0').symlink_to(self.namespace)
        self.nodes = {}
        for part in (2, 3, 5):
            leaf = self.namespace / f'nvme0n1p{part}'
            leaf.mkdir()
            (leaf / 'dev').write_text(f'259:{part}\n')
            (leaf / 'partition').write_text(f'{part}\n')
            (self.sys / f'dev/block/259:{part}').symlink_to(leaf)
            self.nodes[self.dev / leaf.name] = SimpleNamespace(
                st_mode=stat.S_IFBLK | 0o660, st_uid=0, st_rdev=os.makedev(259, part))
        self.root_row = '20 1 259:2 / / ro,relatime - ext4 /dev/nvme0n1p2 ro'
        self.guest_row = ('30 20 259:5 / /run/a733-guests rw,nosuid,nodev,noexec,relatime'
                          ' - ext4 /dev/nvme0n1p5 rw')
        self.write_mounts()
        disk = self.work / 'disk.raw'
        disk.touch()
        self.fd = os.open(disk, os.O_RDONLY)
        self.addCleanup(os.close, self.fd)
        (self.proc / f'self/fdinfo/{self.fd}').write_text('pos:\t0\nflags:\t0100000\nmnt_id:\t30\nino:\t1\n')
        self.disk_info = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_dev=os.makedev(259, 5))
        self.context.enter_context(mock.patch.object(GUARD.os, 'fstat', side_effect=lambda fd: self.disk_info))
        original_lstat = Path.lstat
        nodes = self.nodes

        def fixture_lstat(path):
            if path in nodes:
                return nodes[path]
            return original_lstat(path)

        self.context.enter_context(mock.patch.object(Path, 'lstat', fixture_lstat))

    def write_mounts(self, extra=''):
        (self.proc / 'self/mountinfo').write_text(self.root_row + '\n' + self.guest_row + '\n' + extra)

    def verify(self):
        return GUARD.verify_guest_storage(self.fd, proc_root=self.proc, sys_root=self.sys, dev_root=self.dev)

    def test_valid_selected_root_a_and_root_b(self):
        evidence = self.verify()
        self.assertEqual(evidence['namespace'], 'nvme0n1')
        self.assertEqual(evidence['guest_device'], [259, 5])
        self.assertEqual(evidence['guest_mount_id'], 30)
        self.root_row = self.root_row.replace('259:2', '259:3').replace('n1p2', 'n1p3')
        self.write_mounts()
        self.assertEqual(self.verify()['root_source'], '/dev/nvme0n1p3')

    def test_no_root_privilege_required_and_no_block_open(self):
        original_open = Path.open
        opened = []

        def checked_open(path, *args, **kwargs):
            opened.append(path)
            self.assertFalse(path.is_relative_to(self.dev))
            self.assertIn(args[0], ('rb',))
            return original_open(path, *args, **kwargs)

        with mock.patch.object(Path, 'open', checked_open), mock.patch.object(os, 'geteuid', return_value=1000):
            self.verify()
        self.assertTrue(opened)

    def test_wrong_sources_refused(self):
        for source in ('/dev/mmcblk0p5', '/dev/sda5', '/dev/dm-0', '/dev/nvme1n1p5',
                       '/dev/nvme0n1p4', '/dev/disk/by-id/nvme', '/dev/nvme00n1p5'):
            with self.subTest(source=source):
                self.write_mounts()
                path = self.proc / 'self/mountinfo'
                path.write_text(path.read_text().replace('/dev/nvme0n1p5', source))
                with self.assertRaises(ValueError):
                    self.verify()
        for source in ('/dev/mmcblk0p2', '/dev/sda2', '/dev/dm-0', '/dev/nvme0n1p4'):
            with self.subTest(root=source):
                self.write_mounts()
                path = self.proc / 'self/mountinfo'
                path.write_text(path.read_text().replace('/dev/nvme0n1p2', source))
                with self.assertRaises(ValueError):
                    self.verify()

    def test_duplicate_ambiguous_and_nested_mounts(self):
        for extra in (self.guest_row + '\n', self.root_row + '\n',
                      '31 30 259:5 /other /run/a733-guests/vm rw - ext4 /dev/nvme0n1p5 rw\n',
                      '30 1 0:1 / /unrelated rw - tmpfs tmpfs rw\n'):
            with self.subTest(extra=extra):
                self.write_mounts(extra)
                with self.assertRaises(ValueError):
                    self.verify()

    def test_access_flags_mount_roots_and_type(self):
        original = self.guest_row
        for row in (original.replace('rw,nosuid', 'ro,nosuid'),
                    original.replace('p5 rw', 'p5 ro'),
                    original.replace('rw,nosuid', 'rw,ro,nosuid'),
                    original.replace(',noexec', ''), original.replace(',nodev', ''),
                    original.replace(',nosuid', ''), original.replace('ext4', 'xfs'),
                    original.replace('259:5 / ', '259:5 /bind ')):
            with self.subTest(row=row):
                self.guest_row = row
                self.write_mounts()
                with self.assertRaises(ValueError):
                    self.verify()
        self.guest_row = original
        self.root_row = self.root_row.replace('p2 ro', 'p2 rw')
        self.write_mounts()
        with self.assertRaises(ValueError):
            self.verify()

    def test_held_descriptor_device_type_and_mount_id(self):
        self.disk_info.st_dev = os.makedev(259, 4)
        with self.assertRaisesRegex(ValueError, 'guest filesystem'):
            self.verify()
        self.disk_info.st_dev = os.makedev(259, 5)
        self.disk_info.st_mode = stat.S_IFBLK | 0o600
        with self.assertRaisesRegex(ValueError, 'must be regular'):
            self.verify()
        self.disk_info.st_mode = stat.S_IFREG | 0o600
        for data in ('mnt_id:\t31\n', 'pos:\t0\n', 'mnt_id:\t30\nmnt_id:\t30\n',
                     'mnt_id:\t030\n', 'mnt_id:\t+30\n', 'mnt_id:\t-1\n'):
            with self.subTest(fdinfo=data):
                (self.proc / f'self/fdinfo/{self.fd}').write_text(data)
                with self.assertRaises(ValueError):
                    self.verify()

    def test_unsafe_node_metadata(self):
        for part in (2, 5):
            node = self.nodes[self.dev / f'nvme0n1p{part}']
            original_mode = node.st_mode
            for mode in (stat.S_IFLNK | 0o777, stat.S_IFREG | 0o600):
                node.st_mode = mode
                with self.assertRaisesRegex(ValueError, 'block node'):
                    self.verify()
            node.st_mode = original_mode
            node.st_uid = 1000
            with self.assertRaisesRegex(ValueError, 'root-owned'):
                self.verify()
            node.st_uid = 0
            node.st_rdev = os.makedev(259, 99)
            with self.assertRaisesRegex(ValueError, 'mounted device'):
                self.verify()
            node.st_rdev = os.makedev(259, part)

    def test_sysfs_partition_dev_parent_and_removable(self):
        for path, bad in ((self.namespace / 'nvme0n1p5/partition', '4\n'),
                          (self.namespace / 'nvme0n1p2/dev', '259:9\n'),
                          (self.namespace / 'removable', '1\n'),
                          (self.namespace / 'dev', '259:9\n')):
            with self.subTest(path=path):
                previous = path.read_text()
                path.write_text(bad)
                with self.assertRaises((ValueError, OSError)):
                    self.verify()
                path.write_text(previous)
        (self.namespace / 'nvme0n1p5/removable').write_text('1\n')
        with self.assertRaisesRegex(ValueError, 'removable'):
            self.verify()

    def test_sysfs_alias_cannot_resolve_outside_devices_or_wrong_parent(self):
        link = self.sys / 'dev/block/259:5'
        for destination in (self.work / 'nvme0n1/nvme0n1p5',
                            self.sys / 'devices/nvme1n1/nvme0n1p5'):
            destination.mkdir(parents=True)
            link.unlink()
            link.symlink_to(destination)
            with self.assertRaisesRegex(ValueError, 'sysfs NVMe'):
                self.verify()

    def test_strict_bounded_mountinfo_and_fdinfo(self):
        for bad in ('', self.root_row + '\n',
                    self.root_row.replace('259:2', '0259:2') + '\n' + self.guest_row,
                    self.root_row + '\n' + self.guest_row.replace('/run/', '/run/../run/'),
                    self.root_row + '\n' + self.guest_row.replace('/run/', '/run/\\040/'),
                    self.root_row + '\n' + self.guest_row.replace(' - ', ' '),
                    'x' * (GUARD.MOUNTINFO_LIMIT + 1)):
            with self.subTest(length=len(bad)):
                (self.proc / 'self/mountinfo').write_text(bad)
                with self.assertRaises(ValueError):
                    self.verify()
        self.write_mounts()
        (self.proc / f'self/fdinfo/{self.fd}').write_text('x' * (GUARD.FDINFO_LIMIT + 1))
        with self.assertRaises(ValueError):
            self.verify()

    def test_boot_inputs_require_selected_root_or_guest_mount(self):
        boot_fd = self.fd + 100
        fdinfo = self.proc / f'self/fdinfo/{boot_fd}'
        boot = SimpleNamespace(st_mode=stat.S_IFREG | 0o444, st_dev=os.makedev(259, 2))
        fdinfo.write_text('mnt_id:\t20\n')
        with mock.patch.object(GUARD.os, 'fstat',
                               side_effect=lambda fd: self.disk_info if fd == self.fd else boot):
            result = GUARD.verify_guest_storage(
                self.fd, boot_fds=[boot_fd], proc_root=self.proc, sys_root=self.sys, dev_root=self.dev)
            self.assertEqual(result['boot_inputs_checked'], 1)
            for identity, mount in ((os.makedev(179, 1), 20),
                                    (os.makedev(259, 2), 31),
                                    (os.makedev(259, 4), 40)):
                boot.st_dev = identity
                fdinfo.write_text(f'mnt_id:\t{mount}\n')
                with self.assertRaisesRegex(ValueError, 'boot input'):
                    GUARD.verify_guest_storage(self.fd, boot_fds=[boot_fd],
                        proc_root=self.proc, sys_root=self.sys, dev_root=self.dev)

    def test_directory_entrypoint_retains_device_and_mount_identity_checks(self):
        self.disk_info.st_mode = stat.S_IFDIR | 0o711
        kwargs = dict(proc_root=self.proc, sys_root=self.sys, dev_root=self.dev)
        with self.assertRaisesRegex(ValueError, 'regular'):
            GUARD.verify_guest_storage(self.fd, **kwargs)
        result = GUARD.verify_guest_directory(self.fd, **kwargs)
        self.assertEqual(result['guest_device'], [259, 5])
        self.disk_info.st_dev = os.makedev(179, 1)
        with self.assertRaisesRegex(ValueError, 'guest filesystem'):
            GUARD.verify_guest_directory(self.fd, **kwargs)
        self.disk_info.st_dev = os.makedev(259, 5)
        (self.proc / f'self/fdinfo/{self.fd}').write_text('mnt_id:\t31\n')
        with self.assertRaisesRegex(ValueError, 'alternate mount'):
            GUARD.verify_guest_directory(self.fd, **kwargs)

    def test_actual_host_is_not_assumed_to_be_supported(self):
        # Current host is a builder, not the A733 managed read-only NVMe layout.
        with self.assertRaises((ValueError, OSError)):
            GUARD.verify_guest_storage(self.fd)


if __name__ == '__main__':
    unittest.main()
