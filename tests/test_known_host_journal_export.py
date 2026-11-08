"""Host-only regression checks; never open or mount physical storage."""
from contextlib import contextmanager
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import struct
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

SPEC = importlib.util.spec_from_file_location(
    'journal_export', Path(__file__).resolve().parents[1] / 'scripts/export-known-host-journal.py')
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)


class JournalExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.directory = self.root / 'var/log/journal' / ('a' * 32)
        self.directory.mkdir(parents=True)
        for directory in (self.root, self.root / "var", self.root / "var/log",
                          self.root / "var/log/journal", self.directory):
            directory.chmod(0o755)
        self.source = self.directory / 'system.journal'
        self.source.write_bytes(b'journal fixture\0\xff')
        self.source.chmod(0o640)
        # Files remain on host tmpfs/disk; normalize only the fixture owner's UID
        # so source validation exercises the same branch without requiring sudo.
        actual_stat, actual_fstat = os.stat, os.fstat
        class RootInfo:
            def __init__(self, info):
                self.info = info
            def __getattr__(self, name):
                return 0 if name == 'st_uid' else getattr(self.info, name)
            def __eq__(self, other):
                return isinstance(other, RootInfo) and self.info == other.info
        self.stat_patch = patch.object(helper.os, 'stat',
                                       side_effect=lambda *a, **k: RootInfo(actual_stat(*a, **k)))
        self.fstat_patch = patch.object(helper.os, 'fstat',
                                        side_effect=lambda *a, **k: RootInfo(actual_fstat(*a, **k)))
        self.stat_patch.start()
        self.fstat_patch.start()

    def tearDown(self):
        self.fstat_patch.stop()
        self.stat_patch.stop()
        self.temp.cleanup()

    def test_archive_contains_only_selected_journal_and_receipt(self):
        files = helper.collect_journal(self.root)
        stream = io.BytesIO()
        helper.emit_archive(files, {'members': ['fixture']}, stream)
        with tarfile.open(fileobj=io.BytesIO(stream.getvalue()), mode='r:gz') as archive:
            self.assertEqual(archive.getnames(),
                             ['var/log/journal/' + 'a' * 32 + '/system.journal',
                              'export-receipt.json'])
            member = archive.getmembers()[0]
            self.assertEqual(member.mode, 0o640)
            self.assertEqual(archive.extractfile(member).read(), self.source.read_bytes())
            self.assertEqual(json.load(archive.extractfile('export-receipt.json')),
                             {'members': ['fixture']})

    def test_bounds(self):
        for limit, value in [('MAX_TOTAL', 1), ('MAX_FILE', 1), ('MAX_FILES', 0),
                             ('MAX_ENTRIES', 1), ('MAX_DEPTH', 0)]:
            with self.subTest(limit=limit), patch.object(helper, limit, value):
                with self.assertRaisesRegex(ValueError, 'bounds'):
                    helper.collect_journal(self.root)

    def test_lazy_directory_enumeration_stops_at_global_entry_limit(self):
        actual_scandir = os.scandir
        child_inode = self.directory.stat().st_ino
        for nested in (False, True):
            with self.subTest(nested=nested):
                consumed = []
                closed = []
                @contextmanager
                def bounded_fixture(fd):
                    def names():
                        for number in range(4096):
                            consumed.append(number)
                            yield SimpleNamespace(name=f'{number:04d}.journal')
                    try:
                        if not nested or os.fstat(fd).st_ino == child_inode:
                            yield names()
                        else:
                            with actual_scandir(fd) as directory:
                                def counted_names():
                                    for entry in directory:
                                        consumed.append(entry.name)
                                        yield entry
                                yield counted_names()
                    finally:
                        closed.append(fd)
                with patch.object(helper.os, 'scandir', side_effect=bounded_fixture):
                    with self.assertRaisesRegex(ValueError, 'directory bounds exceeded'):
                        helper.collect_journal(self.root)
                # Includes the ancestor's child entry: the allowance is shared
                # across all directories, with one extra entry to detect excess.
                self.assertEqual(len(consumed), helper.MAX_ENTRIES + 1)
                self.assertEqual(len(closed), 2 if nested else 1)

    def test_symlink_hardlink_and_nonjournal_refused(self):
        extra = self.directory / 'extra.journal'
        for kind in ('symlink', 'hardlink', 'unselected'):
            with self.subTest(kind=kind):
                if kind == 'symlink':
                    extra.symlink_to(self.source)
                elif kind == 'hardlink':
                    os.link(self.source, extra)
                else:
                    extra = self.directory / 'secret.key'
                    extra.write_bytes(b'do not export')
                with self.assertRaises(ValueError):
                    helper.collect_journal(self.root)
                extra.unlink()

    def test_writable_file_and_directory_refused(self):
        self.source.chmod(0o660)
        with self.assertRaisesRegex(ValueError, 'unsafe'):
            helper.collect_journal(self.root)
        self.source.chmod(0o640)
        self.directory.chmod(0o777)
        with self.assertRaisesRegex(ValueError, 'unsafe'):
            helper.collect_journal(self.root)

    def test_superblock_rejects_external_and_unknown_journals(self):
        data = bytearray(1024)
        data[56:58] = b'\x53\xef'
        data[104:120] = uuid.UUID(helper.STATE_UUID).bytes
        data[120:130] = b'a733-state'
        for offset, value in [(4, helper.PART_SIZE // 4096), (24, 2), (92, 4),
                              (96, 0x2c2), (224, 8)]:
            struct.pack_into('<I', data, offset, value)
        helper.validate_superblock(data)
        for offset, value in [(224, 0), (228, 1), (208, 1), (96, 0x102c2), (92, 0)]:
            with self.subTest(offset=offset):
                bad = bytearray(data)
                struct.pack_into('<I', bad, offset, value)
                with self.assertRaises(ValueError):
                    helper.validate_superblock(bad)

    def test_directory_setgid_permitted_but_nonroot_owner_refused(self):
        self.directory.chmod(0o2755)
        self.assertEqual(len(helper.collect_journal(self.root)), 1)
        with patch.object(helper.os, 'fstat', return_value=os.stat_result(
                (stat.S_IFDIR | 0o755, 1, 1, 1, 1000, 0, 0, 0, 0, 0))):
            with self.assertRaisesRegex(ValueError, 'unsafe journal directory'):
                helper.directory_info(0, 1)

    def test_changed_source_refused(self):
        actual_read = os.read
        def changing_read(fd, size):
            data = actual_read(fd, size)
            if data:
                self.source.write_bytes(b'changed')
            return data
        with patch.object(helper.os, 'read', side_effect=changing_read):
            with self.assertRaisesRegex(ValueError, 'changed during read'):
                helper.collect_journal(self.root)

    def test_mount_calls_libc_with_readonly_noload(self):
        from unittest.mock import Mock
        libc = Mock()
        libc.mount.return_value = 0
        with patch.object(helper.ctypes, 'CDLL', return_value=libc):
            helper.mount_readonly(Path('/run/private-mount'))
        libc.mount.assert_called_once_with(b'/dev/nvme0n1p4', b'/run/private-mount',
                                           b'ext4', 15, b'noload')

    def test_cleanup_on_collection_failure(self):
        point = self.root / 'point'
        point.mkdir()
        with patch.object(helper, 'mount_readonly') as mount, \
                patch.object(helper, 'verify_mount'), \
                patch.object(helper, 'collect_journal', side_effect=ValueError('fixture')), \
                patch.object(helper, 'unmount') as unmount:
            with self.assertRaises(ValueError):
                helper.mounted_journal(point, '0:1')
            mount.assert_called_once_with(point)
            unmount.assert_called_once_with(point)
        self.assertFalse(point.exists())

    def test_failed_unmount_retains_mountpoint(self):
        point = self.root / 'point'
        point.mkdir()
        with patch.object(helper, 'mount_readonly'), patch.object(helper, 'verify_mount'), \
                patch.object(helper, 'collect_journal', return_value=[]), \
                patch.object(helper, 'unmount', side_effect=OSError('fixture')):
            with self.assertRaises(OSError):
                helper.mounted_journal(point, '0:1')
        self.assertTrue(point.is_dir())

    def test_failed_mount_does_not_unmount(self):
        point = self.root / 'point'
        point.mkdir()
        with patch.object(helper, 'mount_readonly', side_effect=OSError('fixture')), \
                patch.object(helper, 'unmount') as unmount:
            with self.assertRaises(OSError):
                helper.mounted_journal(point, '0:1')
            unmount.assert_not_called()
        self.assertFalse(point.exists())

    def test_write_counter_change_prevents_archive(self):
        with patch.object(helper, 'runtime_guard'), \
                patch.object(helper, 'write_counters', side_effect=[{'x': [0]}, {'x': [1]}]), \
                patch.object(helper, 'inspect_target', return_value={helper.PART.name: '0:1'}), \
                patch.object(helper.tempfile, 'mkdtemp', return_value=str(self.root)), \
                patch.object(helper, 'mounted_journal', return_value=[]), \
                patch.object(helper, 'emit_archive') as emit:
            with self.assertRaisesRegex(ValueError, 'write counters changed'):
                helper.execute('fixture', io.BytesIO())
            emit.assert_not_called()


if __name__ == '__main__':
    unittest.main()
