"""Host-only SPI safety checks: fake sysfs/FDs, never access a real device."""
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    'backup_spi', Path(__file__).resolve().parents[1] / 'scripts/backup-spi.py')
SPI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SPI)


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.node = self.base / 'sys/devices/platform/spi0.0'
        self.mtd = self.node / 'mtd/mtd0'
        self.mtd.mkdir(parents=True)
        for key, value in dict(name='spi0.0', type='nor', size=str(SPI.SIZE),
                               erasesize='65536', writesize='1', dev='90:0').items():
            (self.mtd / key).write_text(value)
        nor = self.node / 'spi-nor'
        nor.mkdir()
        (nor / 'partname').write_text('XM25QU128C')
        (nor / 'jedec_id').write_text('204118')
        for name, target in [('sys/class/mtd/mtd0', self.mtd),
                             ('sys/bus/spi/devices/spi0.0', self.node),
                             ('sys/dev/char/90:0', self.mtd)]:
            link = self.base / name
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(target)
        (self.base / 'proc').mkdir()
        (self.base / 'proc/swaps').write_text('Filename\tType\tSize\tUsed\tPriority\n')
        (self.base / 'proc/mtd').write_text(
            'dev:    size   erasesize  name\nmtd0: 01000000 00010000 "spi0.0"\n')
        self.fake_stat = SimpleNamespace(st_mode=stat.S_IFCHR | 0o600,
                                         st_rdev=os.makedev(90, 0))
        self.start_patch('Path', self.redirect)
        self.start_patch('os.fstat', lambda fd: self.fake_stat)
        self.ioctls = []
        self.start_patch('fcntl.ioctl', self.ioctl)

    def start_patch(self, name, value):
        if '.' in name:
            owner, attribute = name.split('.')
            p = patch.object(getattr(SPI, owner), attribute, value)
        else:
            p = patch.object(SPI, name, value)
        p.start()
        self.addCleanup(p.stop)

    def redirect(self, *parts):
        path = Path(*parts)
        return self.base / str(path).lstrip('/')

    def ioctl(self, fd, operation, data, mutate):
        self.ioctls.append(operation)
        self.assertEqual(operation, SPI.MEMGETINFO)
        data[:] = SPI.MTD_INFO.pack(3, 0xC00, SPI.SIZE, SPI.ERASE, 1, 0, 0)
        return 0

    def inspect(self):
        return SPI.identity(19, '/dev/mtd0')

    def test_complete_identity_and_read_only_ioctl(self):
        result = self.inspect()
        self.assertEqual(result['jedec_id'], '204118')
        self.assertEqual(self.ioctls, [0x80204D01])
        self.assertEqual(SPI.MTD_INFO.size, 32)

    def test_reject_wrong_type_major_or_identity(self):
        self.fake_stat.st_mode = stat.S_IFREG
        with self.assertRaisesRegex(ValueError, 'character'):
            self.inspect()
        self.fake_stat.st_mode = stat.S_IFCHR
        self.fake_stat.st_rdev = os.makedev(90, 2)
        with self.assertRaisesRegex(ValueError, 'major/minor'):
            self.inspect()
        self.fake_stat.st_rdev = os.makedev(90, 0)
        for field, value in [('size', '16777215'), ('name', 'other'), ('type', 'nand')]:
            old = (self.mtd / field).read_text()
            (self.mtd / field).write_text(value)
            with self.assertRaisesRegex(ValueError, 'identity/geometry'):
                self.inspect()
            (self.mtd / field).write_text(old)

    def test_reject_wrong_part_and_jedec(self):
        for field in ('partname', 'jedec_id'):
            path = self.node / 'spi-nor' / field
            old = path.read_text()
            path.write_text('unknown')
            with self.assertRaisesRegex(ValueError, 'part/JEDEC'):
                self.inspect()
            path.write_text(old)

    def test_reject_extra_partition_or_malformed_map(self):
        path = self.base / 'proc/mtd'
        path.write_text(path.read_text() + 'mtd1: 00010000 00010000 "extra"\n')
        with self.assertRaisesRegex(ValueError, 'partition map'):
            self.inspect()

    def test_reject_wrong_spi_ancestry(self):
        link = self.base / 'sys/bus/spi/devices/spi0.0'
        link.unlink()
        other = self.base / 'sys/devices/platform/other-spi'
        other.mkdir()
        link.symlink_to(other)
        with self.assertRaisesRegex(ValueError, 'expected SPI node'):
            self.inspect()

    def test_reject_wrong_dev_char_link(self):
        link = self.base / 'sys/dev/char/90:0'
        link.unlink()
        link.symlink_to(self.node)
        with self.assertRaisesRegex(ValueError, 'expected SPI node'):
            self.inspect()

    def test_reject_extra_sysfs_only_mtd(self):
        (self.base / 'sys/class/mtd/mtd1').mkdir()
        with self.assertRaisesRegex(ValueError, 'additional MTD'):
            self.inspect()

    def test_reject_ioctl_geometry(self):
        def bad_ioctl(fd, command, data, mutate):
            data[:] = SPI.MTD_INFO.pack(4, 0, SPI.SIZE, SPI.ERASE, 1, 0, 0)
        with patch.object(SPI.fcntl, 'ioctl', bad_ioctl):
            with self.assertRaisesRegex(ValueError, 'NOR geometry'):
                self.inspect()


class StreamTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = os.open(self.temp.name, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, self.directory)
        self.snapshot = {'device': '/dev/mtd0'}
        self.calls = 0

    def reader(self, fd, length, offset):
        self.calls += 1
        return b'x' * length if offset < SPI.SIZE else b''

    def run_backup(self, reader=None, guard=None):
        return SPI.backup(123, self.snapshot, self.directory,
                          guard or (lambda: self.snapshot), reader=reader or self.reader)

    def assert_no_success(self):
        self.assertFalse((Path(self.temp.name) / 'manifest.json').exists())

    def test_two_complete_reads_and_output_verification(self):
        result = self.run_backup()
        self.assertEqual(self.calls, 34)
        self.assertEqual(result['bytes'], SPI.SIZE)
        self.assertFalse(result['off_device_retention_verified'])
        root = Path(self.temp.name)
        self.assertEqual(json.loads((root / 'manifest.json').read_text()), result)
        self.assertEqual(stat.S_IMODE((root / 'spi.bin').stat().st_mode), 0o600)

    def test_source_changed(self):
        def changing(fd, length, offset):
            self.calls += 1
            return (b'x' if self.calls <= 17 else b'y') * length if offset < SPI.SIZE else b''
        with self.assertRaisesRegex(ValueError, 'changed between'):
            self.run_backup(changing)
        self.assert_no_success()

    def test_identity_changed_after_read(self):
        guards = iter([self.snapshot, {'changed': True}])
        with self.assertRaisesRegex(ValueError, 'identity changed'):
            self.run_backup(guard=lambda: next(guards))
        self.assert_no_success()

    def test_swap_activated_after_first_read_blocks_success(self):
        snapshots = iter(['Filename Type Size Used Priority\n',
                          'Filename Type Size Used Priority\n/dev/zram0 partition 100 0 -2\n'])
        def guard():
            with patch.object(Path, 'read_text', return_value=next(snapshots)):
                SPI.require_no_swap()
            return self.snapshot
        with self.assertRaisesRegex(ValueError, 'no active swap'):
            self.run_backup(guard=guard)
        self.assert_no_success()

    def test_failed_or_truncated_read(self):
        with self.assertRaisesRegex(ValueError, 'SPI read'):
            self.run_backup(lambda *_: b'')
        self.assert_no_success()
        self.assertTrue((Path(self.temp.name) / 'spi.bin').exists())

    def test_read_error_retains_partial_without_success(self):
        def error(*_):
            raise OSError('simulated read EIO')
        with self.assertRaisesRegex(OSError, 'EIO'):
            self.run_backup(error)
        self.assert_no_success()

    def test_output_corruption_is_rejected(self):
        with patch.object(SPI, 'hash_read', side_effect=['a', 'a', 'bad']):
            # Supply the expected file length to reach hash verification.
            real_fstat = os.fstat
            def sized(fd):
                info = real_fstat(fd)
                return SimpleNamespace(st_mode=info.st_mode, st_nlink=1, st_size=SPI.SIZE)
            with patch.object(SPI.os, 'fstat', sized):
                with self.assertRaisesRegex(ValueError, 'bytes/hash'):
                    self.run_backup()
        self.assert_no_success()


class OutputTests(unittest.TestCase):
    def setUp(self):
        swap = patch.object(SPI, 'require_no_swap')
        swap.start()
        self.addCleanup(swap.stop)

    def test_canonical_source_and_read_only_open(self):
        for path in ('/dev/mtd0ro', '/dev/mtd00', '/dev/../dev/mtd0', '/tmp/spi', '/dev/mtdblock0'):
            with self.assertRaises(ValueError):
                SPI.open_source(path)
        with patch.object(SPI.os, 'open', return_value=123) as opened, \
                patch.object(SPI, 'identity', return_value={}), \
                patch.object(SPI.os, 'close'):
            SPI.open_source('/dev/mtd0')
        flags = opened.call_args.args[1]
        self.assertEqual(flags & os.O_ACCMODE, os.O_RDONLY)
        self.assertTrue(flags & os.O_NOFOLLOW and flags & os.O_NONBLOCK)

    def test_real_source_symlink_open_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / 'image'
            image.write_bytes(b'fixture')
            link = Path(directory) / 'symlink'
            link.symlink_to(image)
            real_open = os.open
            def source_open(path, flags):
                self.assertEqual(path, '/dev/mtd0')
                return real_open(link, flags)
            with patch.object(SPI.os, 'open', source_open), \
                    patch.object(SPI, 'identity') as inspect:
                with self.assertRaises(OSError):
                    SPI.open_source('/dev/mtd0')
                inspect.assert_not_called()

    def test_successful_safe_output_fd_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'run').mkdir(mode=0o755)
            (root / 'run/parent').mkdir(mode=0o755)
            real_open, real_stat = os.open, os.fstat
            opened = []
            def rooted_open(path, flags, **kwargs):
                opened.append((path, flags, kwargs))
                return real_open(root if path == '/' else path, flags, **kwargs)
            def root_owned(fd):
                info = real_stat(fd)
                return SimpleNamespace(st_mode=info.st_mode, st_uid=0)
            def tmpfs(fd):
                self.assertTrue(stat.S_ISDIR(real_stat(fd).st_mode))
            with patch.object(SPI.os, 'open', rooted_open), \
                    patch.object(SPI.os, 'fstat', root_owned), \
                    patch.object(SPI, 'ram_filesystem', side_effect=tmpfs) as ram, \
                    patch.object(SPI.os, 'fstatvfs', return_value=SimpleNamespace(
                        f_bavail=SPI.SIZE + SPI.RESERVE, f_frsize=1)):
                fd = SPI.create_output('/run/parent/backup')
                try:
                    self.assertTrue(stat.S_ISDIR(real_stat(fd).st_mode))
                    self.assertEqual(stat.S_IMODE(real_stat(fd).st_mode), 0o700)
                finally:
                    os.close(fd)
            self.assertEqual([item[0] for item in opened], ['/', 'run', 'parent', 'backup'])
            self.assertEqual(ram.call_count, 3)
            for _, flags, _ in opened:
                self.assertTrue(flags & os.O_DIRECTORY and flags & os.O_NOFOLLOW)
                self.assertEqual(flags & os.O_ACCMODE, os.O_RDONLY)
            for _, _, kwargs in opened[1:]:
                self.assertIn('dir_fd', kwargs)

    def test_unsafe_output_paths(self):
        for path in ('/tmp/spi', '/run', '/run/../tmp/spi', '/run//spi', '/run/./spi', 'run/spi'):
            with self.assertRaises(ValueError):
                SPI.create_output(path)

    def test_unsafe_ancestor_owner_mode_and_type(self):
        for mode, uid in [(stat.S_IFDIR | 0o777, 0), (stat.S_IFDIR | 0o755, 1000),
                          (stat.S_IFREG | 0o700, 0)]:
            with patch.object(SPI.os, 'fstat', return_value=SimpleNamespace(st_mode=mode, st_uid=uid)):
                with self.assertRaises(ValueError):
                    SPI.safe_directory(123)

    def test_capacity_refusal_precedes_directory_creation(self):
        with patch.object(SPI.os, 'open', side_effect=[50, 51]), \
                patch.object(SPI.os, 'close'), \
                patch.object(SPI, 'safe_directory'), \
                patch.object(SPI, 'ram_filesystem'), \
                patch.object(SPI.os, 'fstatvfs', return_value=SimpleNamespace(f_bavail=1, f_frsize=4096)), \
                patch.object(SPI.os, 'mkdir') as mkdir:
            with self.assertRaisesRegex(ValueError, 'capacity'):
                SPI.create_output('/run/spi-test')
            mkdir.assert_not_called()

    def test_symlink_output_ancestor_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            link = Path(directory) / 'link'
            link.symlink_to(directory)
            real_open = os.open
            def symlink_open(name, flags, **kwargs):
                if name == 'run':
                    return real_open(link, flags)
                return real_open(name, flags, **kwargs)
            with patch.object(SPI.os, 'open', symlink_open):
                with self.assertRaises(OSError):
                    SPI.create_output('/run/spi-test')

    def test_kernel_filesystem_magic_required(self):
        def filesystem(magic):
            def fstatfs(fd, result):
                result._obj[0] = magic
                return 0
            return SimpleNamespace(fstatfs=fstatfs)
        for magic in (0xEF53, 0x858458F6):
            with patch.object(SPI.ctypes, 'CDLL', return_value=filesystem(magic)):
                with self.assertRaisesRegex(ValueError, 'not tmpfs'):
                    SPI.ram_filesystem(123)
        with patch.object(SPI.ctypes, 'CDLL', return_value=filesystem(0x01021994)):
            SPI.ram_filesystem(123)


class SwapTests(unittest.TestCase):
    def check(self, content):
        with patch.object(Path, 'read_text', return_value=content):
            SPI.require_no_swap()

    def test_header_only_passes(self):
        self.check('Filename\t\tType\tSize\tUsed\tPriority\n')

    def test_active_block_file_and_zram_swap_rejected(self):
        header = 'Filename\tType\tSize\tUsed\tPriority\n'
        for target, kind in [('/dev/sda2', 'partition'), ('/swapfile', 'file'),
                             ('/dev/zram0', 'partition')]:
            with self.subTest(target=target):
                with self.assertRaisesRegex(ValueError, 'no active swap'):
                    self.check(header + f'{target} {kind} 1024 0 -2\n')

    def test_empty_malformed_header_and_read_error_rejected(self):
        for content in ('', 'Filename Type Size Used\n', 'unexpected\n',
                        'Filename Type Size Used Priority\n\n'):
            with self.subTest(content=content):
                with self.assertRaises(ValueError):
                    self.check(content)
        with patch.object(Path, 'read_text', side_effect=OSError('cannot read swaps')):
            with self.assertRaisesRegex(OSError, 'cannot read swaps'):
                SPI.require_no_swap()

    def test_active_swap_refuses_before_output_creation(self):
        with patch.object(Path, 'read_text', return_value=
                          'Filename Type Size Used Priority\n/dev/zram0 partition 100 0 -2\n'), \
                patch.object(SPI.os, 'open') as opened, \
                patch.object(SPI.os, 'mkdir') as mkdir:
            with self.assertRaises(ValueError):
                SPI.create_output('/run/spi-test')
            opened.assert_not_called()
            mkdir.assert_not_called()


if __name__ == '__main__':
    unittest.main()
