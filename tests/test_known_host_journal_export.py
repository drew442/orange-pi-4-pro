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
        # Recovery and legacy orphan metadata remain useful for protected reads.
        dirty = bytearray(data)
        struct.pack_into('<I', dirty, 96, 0x2c6)
        struct.pack_into('<I', dirty, 232, 1)
        helper.validate_superblock(dirty)
        for offset, value in [(224, 0), (228, 1), (208, 1), (96, 0x102c2), (92, 0), (92, 0x1004), (100, 0x10000)]:
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
                patch.object(helper, 'protected_journal', return_value=([], 0)), \
                patch.object(helper, 'emit_archive') as emit:
            with self.assertRaisesRegex(ValueError, 'write counters changed'):
                helper.execute('fixture', io.BytesIO())
            emit.assert_not_called()


class BlockProtectionTests(unittest.TestCase):
    """Model kernel RO flags, not physical I/O; exercise production lifecycle."""
    def run_protected(self, original=0, failure=None):
        with tempfile.TemporaryDirectory() as directory:
            point = Path(directory) / 'mount'
            point.mkdir()
            state = {'ro': original, 'closed': [], 'changes': [], 'mounts': 0}
            identities = {helper.PART.name: '259:4'}

            def validate(fd, identity, expected_ro=None):
                self.assertIn(fd, (10, 11))
                self.assertNotIn(fd, state['closed'])
                self.assertEqual(identity, '259:4')
                if failure == 'wrongfd':
                    raise ValueError('held STATE descriptor identity differs')
                if expected_ro is not None:
                    helper.require(state['ro'] == expected_ro, 'protection differs')
                return state['ro']

            def ioctl(fd, command, data):
                self.assertEqual(command, 0x125d)
                value = struct.unpack('=I', data)[0]
                state['changes'].append(value)
                if failure == 'set' and value == 1:
                    raise OSError('RO set refused')
                if failure == 'restore' and value == 0:
                    raise OSError('RO restore refused')
                if failure != 'ignored-set':
                    state['ro'] = value

            def mount(_point):
                self.assertEqual(state['ro'], 1, 'mount requires block RO')
                self.assertIn(10, state['closed'], 'exclusive claim must be released')
                self.assertNotIn(11, state['closed'], 'held descriptor required')
                state['mounts'] += 1

            def inspect(**kwargs):
                self.assertEqual(kwargs, {'expected_state_ro': 1, 'held_fd': 11})
                self.assertEqual(state['ro'], 1)
                if failure == 'post-identity' and state['mounts']:
                    raise ValueError('target identity changed')
                return identities

            def unmount(_point):
                if failure == 'unmount':
                    raise OSError('unmount failed')

            with patch.object(helper.os, 'open', side_effect=[10, 11]), \
                    patch.object(helper.os, 'close', side_effect=state['closed'].append), \
                    patch.object(helper, 'validate_state_fd', side_effect=validate), \
                    patch.object(helper, 'validate_state_identity'), \
                    patch.object(helper.fcntl, 'ioctl', side_effect=ioctl), \
                    patch.object(helper, 'inspect_target', side_effect=inspect), \
                    patch.object(helper, 'mount_readonly', side_effect=mount), \
                    patch.object(helper, 'verify_mount'), \
                    patch.object(helper, 'collect_journal', return_value=['fixture']), \
                    patch.object(helper, 'unmount', side_effect=unmount):
                if failure:
                    with self.assertRaises((OSError, ValueError)):
                        helper.protected_journal(point, identities)
                else:
                    self.assertEqual(helper.protected_journal(point, identities),
                                     (['fixture'], original))
            return state

    def test_block_ro_enforced_before_mount_then_restored(self):
        state = self.run_protected()
        self.assertEqual(state['changes'], [1, 0])
        self.assertEqual(state['ro'], 0)
        self.assertEqual(state['closed'], [10, 11])

    def test_preserve_preexisting_block_ro(self):
        state = self.run_protected(original=1)
        self.assertEqual(state['changes'], [])
        self.assertEqual(state['ro'], 1)

    def test_ro_refusal_ignored_set_and_wrong_fd_never_mount(self):
        for failure in ('set', 'ignored-set', 'wrongfd'):
            with self.subTest(failure=failure):
                state = self.run_protected(failure=failure)
                self.assertEqual(state['mounts'], 0)
                self.assertNotIn(0, state['changes'])

    def test_failed_unmount_or_revalidation_never_restores_rw(self):
        for failure in ('unmount', 'post-identity'):
            with self.subTest(failure=failure):
                state = self.run_protected(failure=failure)
                self.assertEqual(state['changes'], [1])
                self.assertEqual(state['ro'], 1)

    def test_failed_restore_reports_failure_and_retains_ro(self):
        state = self.run_protected(failure='restore')
        self.assertEqual(state['changes'], [1, 0, 1])
        self.assertEqual(state['ro'], 1)

    def test_post_clear_sysfs_failure_reprotects_only_revalidated_device(self):
        info = SimpleNamespace(st_mode=stat.S_IFBLK | 0o600, st_rdev=os.makedev(259, 4),
                               st_ino=1, st_dev=1)
        for failure in ('readback', 'reprotect-ioctl', 'reprotect-identity', 'reprotect-readback'):
            with self.subTest(failure=failure):
                state = {'ro': 1, 'changes': [], 'identity_checks': 0}

                def fstat(_fd):
                    state['identity_checks'] += 1
                    if failure == 'reprotect-identity' and 0 in state['changes']:
                        raise ValueError('identity lost')
                    return info

                def ioctl_number(_fd, command, _fmt):
                    return {0x80081272: helper.PART_SIZE, 0x1268: 512,
                            0x127b: 512, 0x125e: state['ro']}[command]

                def ioctl(_fd, _command, data):
                    value = struct.unpack('=I', data)[0]
                    # Protection must have freshly passed identity validation.
                    if value == 1:
                        self.assertGreaterEqual(state['identity_checks'], 3)
                    state['changes'].append(value)
                    if value == 1 and failure == 'reprotect-ioctl':
                        raise OSError('cannot reprotect')
                    state['ro'] = value

                def read_sysfs():
                    if state['ro'] == 0 or (failure == 'reprotect-readback' and
                                            state['changes'] == [0, 1]):
                        raise OSError('sysfs read failed after flag change')
                    return str(state['ro'])

                with patch.object(helper.os, 'fstat', side_effect=fstat), \
                        patch.object(Path, 'lstat', return_value=info), \
                        patch.object(helper.os, 'pread', return_value=b'fixture'), \
                        patch.object(helper, 'validate_superblock'), \
                        patch.object(helper, 'ioctl_number', side_effect=ioctl_number), \
                        patch.object(helper.fcntl, 'ioctl', side_effect=ioctl), \
                        patch.object(Path, 'read_text', side_effect=read_sysfs):
                    message = 'protection reasserted' if failure == 'readback' else 'state uncertain'
                    with self.assertRaisesRegex(ValueError, message):
                        helper.restore_state_writable(10, '259:4')
                if failure == 'reprotect-identity':
                    self.assertEqual(state['changes'], [0], 'never ioctl unvalidated device')
                else:
                    self.assertEqual(state['changes'], [0, 1])
                self.assertEqual(state['ro'], 0 if failure in
                                 ('reprotect-ioctl', 'reprotect-identity') else 1)

    def test_actual_fd_validator_requires_ioctl_and_sysfs_ro_agreement(self):
        info = SimpleNamespace(st_mode=stat.S_IFBLK | 0o600, st_rdev=os.makedev(259, 4),
                               st_ino=1, st_dev=1)
        for ioctl_ro, sysfs_ro in ((0, '0'), (1, '0'), (0, '1')):
            with self.subTest(ioctl_ro=ioctl_ro, sysfs_ro=sysfs_ro), \
                    patch.object(helper.os, 'fstat', return_value=info), \
                    patch.object(Path, 'lstat', return_value=info), \
                    patch.object(helper.os, 'pread', return_value=b'fixture'), \
                    patch.object(helper, 'validate_superblock'), \
                    patch.object(helper, 'ioctl_number',
                                 side_effect=[helper.PART_SIZE, 512, 512, ioctl_ro]), \
                    patch.object(Path, 'read_text', return_value=sysfs_ro):
                with self.assertRaisesRegex(ValueError, 'protection differs'):
                    helper.validate_state_fd(10, '259:4', 1)

    def test_actual_fd_validator_rejects_wrong_device_before_ioctl(self):
        info = SimpleNamespace(st_mode=stat.S_IFBLK | 0o600, st_rdev=os.makedev(259, 4),
                               st_ino=1, st_dev=1)
        wrong = SimpleNamespace(**vars(info))
        wrong.st_rdev = os.makedev(259, 5)
        with patch.object(helper.os, 'fstat', return_value=wrong), \
                patch.object(Path, 'lstat', return_value=info), \
                patch.object(helper, 'ioctl_number') as ioctl:
            with self.assertRaisesRegex(ValueError, 'descriptor identity differs'):
                helper.validate_state_fd(10, '259:4')
            ioctl.assert_not_called()


if __name__ == '__main__':
    unittest.main()
