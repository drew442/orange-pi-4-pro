"""File-only seed determinism/safety; no board, guest CPU or device writes."""
import importlib.util
import os
from pathlib import Path
import shutil
import stat
import struct
import uuid
import tempfile
import time
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('guest_seed', REPO / 'build/guest-seed.py')
SEED = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SEED)


class GuestSeedTests(unittest.TestCase):
    def setUp(self):
        # /tmp has shared writes and must be rejected by the production API.
        self.temporary = tempfile.TemporaryDirectory(prefix='guest-seed-test-', dir=Path.home())
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)
        try:
            fd = SEED.TRUST.open_trusted_directory(self.work)
        except ValueError as error:
            self.skipTest(f'test requires trusted home ancestors: {error}')
        else:
            os.close(fd)

    def build_local(self, output):
        # Local host tool versions/storage are NOT the pinned builder. Only tests
        # may override these policies; neither the CLI nor API accepts overrides.
        with mock.patch.object(SEED, 'check_versions'), mock.patch.object(
                SEED.STATE, 'validate_workspace_storage') as storage:
            record = SEED.build(output)
        held = storage.call_args.args[0]
        self.assertEqual(held.parent.parent, Path('/proc') / str(os.getpid()))
        return record

    @unittest.skipUnless(all(shutil.which(tool) for tool in ('mke2fs', 'debugfs', 'e2fsck', 'dumpe2fs')),
                         'requires filesystem file tools')
    def test_reproducible_across_time_umask_cwd_timezone(self):
        old_cwd = Path.cwd()
        old_tz = os.environ.get('TZ')
        old_mask = os.umask(0o077)
        try:
            first_dir = self.work / 'first'
            first_dir.mkdir(mode=0o700)
            os.chdir(first_dir)
            os.environ['TZ'] = 'Pacific/Honolulu'
            first = self.build_local(first_dir / 'one.raw')
            time.sleep(1.05)  # Different actual source ctime, not only mtime.
            os.umask(0o002)
            second_dir = self.work / 'second'
            second_dir.mkdir(mode=0o700)
            os.chdir(second_dir)
            os.environ['TZ'] = 'Asia/Tokyo'
            second = self.build_local(second_dir / 'two.raw')
            self.assertEqual(first, second)
            a, b = first_dir / 'one.raw', second_dir / 'two.raw'
            with a.open('rb') as left, b.open('rb') as right:
                while block := left.read(1024**2):
                    self.assertEqual(block, right.read(1024**2))
                self.assertEqual(right.read(1), b'')
            self.assertEqual(a.stat().st_size, SEED.SIZE)
            self.assertEqual(stat.S_IMODE(a.stat().st_mode), 0o644)
            self.assertEqual(a.stat().st_uid, os.geteuid())
            with a.open('rb') as stream:
                stream.seek(1024)
                superblock = stream.read(1024)
            self.assertEqual(superblock[104:120], uuid.UUID(SEED.UUID).bytes)
            self.assertEqual(superblock[120:136].rstrip(b'\0'), b'kvm-fixture')
            self.assertEqual(struct.unpack_from('<I', superblock, 24)[0], 2)
            self.assertEqual(struct.unpack_from('<I', superblock, 48)[0], SEED.EPOCH)
            self.assertEqual(struct.unpack_from('<I', superblock, 92)[0] & 4, 0)
            # Independent tool invocations verify bytes and metadata, including
            # both synthetic files and filesystem-created directory inodes.
            for name, expected in SEED.CONTENTS.items():
                result = SEED.run(['debugfs', '-R', f'cat /{name}', str(a)])
                self.assertEqual(result.split(b'\n', 1)[1], expected)
            for name in ('/', '/lost+found', '/fixture-id', '/counter'):
                result = SEED.run(['debugfs', '-R', f'stat {name}', str(a)]).decode('ascii')
                self.assertIn('User:     0   Group:     0', result)
                for field in ('atime', 'mtime', 'ctime', 'crtime'):
                    self.assertIn(f'{field}: 0x{SEED.EPOCH:08x}:00000000', result)
            self.assertEqual(set(first_dir.iterdir()), {a})
            self.assertEqual(set(second_dir.iterdir()), {b})
        finally:
            os.chdir(old_cwd)
            os.umask(old_mask)
            if old_tz is None:
                os.environ.pop('TZ', None)
            else:
                os.environ['TZ'] = old_tz

    def test_existing_objects_refused_before_tools_or_storage(self):
        target = self.work / 'existing'
        target.write_bytes(b'keep')
        (self.work / 'symlink').symlink_to(target)
        (self.work / 'directory').mkdir()
        os.mkfifo(self.work / 'fifo')
        with mock.patch.object(SEED, 'run') as run, mock.patch.object(
                SEED.STATE, 'validate_workspace_storage') as storage:
            for path in self.work.iterdir():
                with self.subTest(path=path.name), self.assertRaises(ValueError):
                    SEED.build(path)
            run.assert_not_called()
            storage.assert_not_called()
        self.assertEqual(target.read_bytes(), b'keep')
        self.assertTrue((self.work / 'symlink').is_symlink())

    def test_device_and_relative_output_refused(self):
        with mock.patch.object(SEED, 'run') as run:
            for path in (Path('/dev/null'), Path('relative.raw')):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    SEED.build(path)
            run.assert_not_called()

    def test_storage_guard_before_output_creation(self):
        output = self.work / 'seed.raw'
        def refuse(path):
            self.assertFalse(output.exists())
            self.assertEqual(path.parent.parent, Path('/proc') / str(os.getpid()))
            self.assertEqual(set(self.work.iterdir()), set())
            raise ValueError('storage refused')
        with mock.patch.object(SEED.STATE, 'validate_workspace_storage', side_effect=refuse), \
                mock.patch.object(SEED, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'storage refused'):
                SEED.build(output)
            run.assert_not_called()
        self.assertFalse(output.exists())

    def test_shared_or_symlink_parent_refused(self):
        shared = self.work / 'shared'
        shared.mkdir()
        shared.chmod(0o777)
        link = self.work / 'link'
        link.symlink_to(self.work, target_is_directory=True)
        with mock.patch.object(SEED, 'run') as run:
            for parent in (shared, link):
                with self.subTest(parent=parent), self.assertRaises((ValueError, OSError)):
                    SEED.build(parent / 'new.raw')
            run.assert_not_called()

    def test_failure_removes_only_own_inode(self):
        output = self.work / 'seed.raw'
        with mock.patch.object(SEED.STATE, 'validate_workspace_storage'), \
                mock.patch.object(SEED, 'check_versions'), \
                mock.patch.object(SEED, 'format_seed', side_effect=ValueError('failed')):
            with self.assertRaisesRegex(ValueError, 'failed'):
                SEED.build(output)
        self.assertEqual(set(self.work.iterdir()), set())
        def replace(fd, stage):
            output.unlink()
            output.write_bytes(b'foreign')
            raise ValueError('replaced')
        with mock.patch.object(SEED.STATE, 'validate_workspace_storage'), \
                mock.patch.object(SEED, 'check_versions'), \
                mock.patch.object(SEED, 'format_seed', side_effect=replace):
            with self.assertRaisesRegex(ValueError, 'replaced'):
                SEED.build(output)
        self.assertEqual(output.read_bytes(), b'foreign')
        self.assertEqual(set(self.work.iterdir()), {output})

    def test_formatters_receive_only_held_regular_file_descriptors(self):
        output = self.work / 'seed.raw'
        invoked = []
        def fail_on_formatter(args, **kwargs):
            invoked.append(args)
            if args[0] == 'mke2fs':
                fd = kwargs['fd']
                self.assertTrue(stat.S_ISREG(os.fstat(fd).st_mode))
                self.assertEqual(os.fstat(fd).st_size, SEED.SIZE)
                self.assertEqual(args[-2], f'/proc/self/fd/{fd}')
                self.assertNotIn(str(output), args)
                raise ValueError('formatter intercepted')
            self.fail('unexpected tool before formatter')
        with mock.patch.object(SEED.STATE, 'validate_workspace_storage'), \
                mock.patch.object(SEED, 'check_versions'), \
                mock.patch.object(SEED, 'run', side_effect=fail_on_formatter):
            with self.assertRaisesRegex(ValueError, 'formatter intercepted'):
                SEED.build(output)
        self.assertEqual(len(invoked), 1)
        self.assertEqual(set(self.work.iterdir()), set())

    def test_version_policy_cannot_be_changed_by_environment(self):
        with mock.patch.dict(os.environ, {'E2FSPROGS_FAKE_TIME': '1', 'MKE2FS_CONFIG': '/foreign'}), \
                mock.patch.object(SEED, 'run', return_value=b'1.47.0'):
            with self.assertRaisesRegex(ValueError, 'pinned Jammy'):
                SEED.check_versions()


if __name__ == '__main__':
    unittest.main()
