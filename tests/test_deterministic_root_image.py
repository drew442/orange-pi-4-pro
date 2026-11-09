"""Real regular-file ext4 fixtures; no device access or hardware claims."""
import hashlib
import importlib.util
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import time
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('deterministic_root_image', REPO / 'build/deterministic-root-image.py')
IMAGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(IMAGE)
SIZE = 32 * 1024**2
TOOLS = ('mke2fs', 'tune2fs', 'debugfs', 'e2fsck')


class DeterministicRootImageTests(unittest.TestCase):
    def setUp(self):
        # /tmp is deliberately refused by the production ancestor policy.
        self.temp = tempfile.TemporaryDirectory(prefix='.a733-ext4-test-',
                                               dir='/run' if os.geteuid() == 0 else Path.home())
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)

    def tree(self, name='root', reverse=False):
        root = self.work / name
        root.mkdir(mode=0o700)
        entries = ['private', 'directory', 'symlink', 'hardlink', 'named pipe']
        if reverse:
            entries.reverse()
        # Create the hardlink's target under either namespace name first.
        for entry in entries:
            path = root / entry
            if entry in ('private', 'hardlink'):
                other = root / ('hardlink' if entry == 'private' else 'private')
                if other.exists():
                    os.link(other, path)
                else:
                    path.write_bytes(b'private\x00payload\xff\n')
                    path.chmod(0o640)
                    os.setxattr(path, 'user.fixture', b'opaque\x00xattr\xff')
            elif entry == 'directory':
                path.mkdir(mode=0o750)
                (path / 'space " quote').write_bytes(b'nested\n')
                (path / 'space " quote').chmod(0o600)
            elif entry == 'symlink':
                path.symlink_to('directory/space " quote')
            else:
                os.mkfifo(path, 0o600)
        os.setxattr(root, 'user.fixture', b'root metadata')
        for index, path in enumerate(IMAGE.tree_entries(root)):
            os.utime(path, ns=((index + 10) * 10**9 + 123, (index + 20) * 10**9 + 456), follow_symlinks=False)
        return root

    def require_tools(self):
        missing = [tool for tool in TOOLS if not shutil.which(tool)]
        if missing:
            self.skipTest('native e2fsprogs unavailable: ' + ', '.join(missing))

    def read_debugfs(self, image, command):
        result = subprocess.run(['debugfs', '-R', command, str(image)], capture_output=True, check=True)
        self.assertRegex(result.stderr, rb'^debugfs [^\r\n]+\n$')
        return result.stdout

    def snapshot(self, root):
        result = {}
        for path in IMAGE.tree_entries(root):
            info = path.lstat()
            item = [info.st_mode, info.st_uid, info.st_gid, info.st_rdev, info.st_nlink,
                    {key: os.getxattr(path, key, follow_symlinks=False)
                     for key in os.listxattr(path, follow_symlinks=False)}]
            if stat.S_ISREG(info.st_mode):
                item.append(path.read_bytes())
            if stat.S_ISLNK(info.st_mode):
                item.append(os.readlink(path))
            result[str(path.relative_to(root))] = item
        return result

    def test_prepare_preserves_non_time_metadata_and_links(self):
        root = self.tree()
        external = self.work / 'external'
        external.write_bytes(b'untouched')
        os.utime(external, ns=(123456789, 987654321))
        (root / 'external-link').symlink_to(external)
        before = self.snapshot(root)
        paths = IMAGE.tree_entries(root)
        IMAGE.prepare_tree(root)
        # Check times before content reads can change access times.
        for path in paths:
            info = path.lstat()
            self.assertEqual((info.st_atime_ns, info.st_mtime_ns), (IMAGE.EPOCH * 10**9,) * 2)
        self.assertEqual(before, self.snapshot(root))
        self.assertEqual((root / 'private').stat().st_ino, (root / 'hardlink').stat().st_ino)
        self.assertEqual(external.stat().st_mtime_ns, 987654321)

    def test_real_images_repeat_full_bytes_and_preserve_metadata(self):
        self.require_tools()
        roots = []
        paths = [self.work / name for name in ('first.ext4', 'second.ext4', 'slot-b.ext4')]
        records = []
        for index, output in enumerate(paths[:2]):
            root = self.tree('tree-' + str(index), reverse=bool(index))
            roots.append(root)
            IMAGE.prepare_tree(root)
            before = self.snapshot(root)
            records.append(IMAGE.make_image(root, output, 'A', SIZE))
            self.assertEqual(before, self.snapshot(root))
            time.sleep(1.05)  # Different creator wall clock and original inode ctimes.
        first, second = (path.read_bytes() for path in paths[:2])
        self.assertEqual(hashlib.sha256(first).digest(), hashlib.sha256(second).digest())
        self.assertEqual(first, second)
        self.assertEqual(records[0]['inode_count'], 7)
        self.assertEqual(records[0]['epoch'], IMAGE.EPOCH)
        self.assertEqual(set(records[0]['tools']), set(TOOLS))
        IMAGE.make_image(roots[0], paths[2], 'B', SIZE)
        self.assertNotEqual(hashlib.sha256(first).digest(), hashlib.sha256(paths[2].read_bytes()).digest())
        root_stat = self.read_debugfs(paths[2], 'stat <2>').decode()
        self.assertIn('Mode:  0700', root_stat)
        self.assertRegex(root_stat, rf'User:\s+{os.getuid()}\s+Group:\s+{os.getgid()}\s+')
        self.assertIn(b'user.fixture (13) = "root metadata"', self.read_debugfs(paths[2], 'ea_list <2>'))
        private = self.read_debugfs(paths[2], 'stat /private').decode()
        hardlink = self.read_debugfs(paths[2], 'stat /hardlink').decode()
        self.assertEqual(re.search(r'Inode: (\d+)', private)[1], re.search(r'Inode: (\d+)', hardlink)[1])
        self.assertIn('Mode:  0640', private)
        self.assertRegex(private, r'Links: 2\s+')
        self.assertEqual(self.read_debugfs(paths[2], 'cat /private'), b'private\x00payload\xff\n')
        self.assertIn(b'user.fixture (13)', self.read_debugfs(paths[2], 'ea_list /private'))
        extracted = self.work / 'xattr-readback'
        self.read_debugfs(paths[2], f'ea_get -f {extracted} /private user.fixture')
        self.assertEqual(extracted.read_bytes(), b'opaque\x00xattr\xff')
        self.assertIn(b'Fast link dest: "directory/space " quote"', self.read_debugfs(paths[2], 'stat /symlink'))
        # All allocated namespace inodes, including the generated lost+found.
        with paths[2].open('rb') as stream:
            inodes = IMAGE.namespace(stream.fileno(), IMAGE.EPOCH, self.work)
            outputs = IMAGE.batch(stream.fileno(), [f'stat <{ino}>' for ino in inodes], IMAGE.EPOCH, self.work)
        for output in outputs:
            for field in ('atime', 'mtime', 'ctime', 'crtime'):
                self.assertRegex(output, rf'(?m)^ ?{field}: 0x{IMAGE.EPOCH:08x}:00000000 -- ')

    def test_unsafe_sources_targets_and_unprepared_tree_are_refused(self):
        root = self.tree()
        output = self.work / 'output'
        with self.assertRaisesRegex(ValueError, 'explicitly prepared'):
            IMAGE.make_image(root, output, 'A', SIZE)
        self.assertFalse(output.exists())
        IMAGE.prepare_tree(root)
        link = self.work / 'source-link'
        link.symlink_to(root, target_is_directory=True)
        for source in (link, Path('/dev'), Path('/proc'), Path('/sys')):
            with self.assertRaises((ValueError, OSError)):
                IMAGE.make_image(source, output, 'A', SIZE)
        with self.assertRaises(OSError):
            IMAGE.make_image(root / 'private', output, 'A', SIZE)
        parent_link = self.work / 'parent-link'
        parent_link.symlink_to(self.work, target_is_directory=True)
        with self.assertRaises(OSError):
            IMAGE.make_image(root, parent_link / 'output', 'A', SIZE)
        shared = self.work / 'shared'
        shared.mkdir(mode=0o777)
        shared.chmod(0o777)
        with self.assertRaisesRegex(ValueError, 'writable directory ancestor'):
            IMAGE.make_image(root, shared / 'output', 'A', SIZE)
        for target in (root / 'output', Path('/dev/a733-refuse'), Path('/proc/a733-refuse'), Path('/sys/a733-refuse')):
            with self.assertRaises(ValueError):
                IMAGE.make_image(root, target, 'A', SIZE)
        output.write_bytes(b'unchanged')
        with self.assertRaises(FileExistsError):
            IMAGE.make_image(root, output, 'A', SIZE)
        self.assertEqual(output.read_bytes(), b'unchanged')
        output.unlink()
        output.symlink_to(self.work / 'absent')
        with self.assertRaises(FileExistsError):
            IMAGE.make_image(root, output, 'A', SIZE)
        output.unlink()
        os.mkfifo(output)
        with self.assertRaises(FileExistsError):
            IMAGE.make_image(root, output, 'A', SIZE)
        for size in (0, SIZE - 1, IMAGE.SLOT_BYTES + 4096):
            with self.assertRaises(ValueError):
                IMAGE.make_image(root, self.work / 'bad-size', 'A', size)

    def test_tool_failures_and_debugfs_false_success_are_refused(self):
        root = self.tree()
        IMAGE.prepare_tree(root)
        with mock.patch.object(IMAGE, 'run', side_effect=ValueError('injected command failure')):
            with self.assertRaisesRegex(ValueError, 'injected command failure'):
                IMAGE.make_image(root, self.work / 'failure.ext4', 'A', SIZE)
        with (self.work / 'failure.ext4').open('rb') as stream:
            bad_results = [
                (b'debugfs: stat <2>\n', b'debugfs 1.47.0\nset_inode_field: invalid field\n'),
                (b'debugfs: wrong command\n', b'debugfs 1.47.0\n'),
                (b'debugfs: set_inode_field <2> ctime @1\nunknown field\n', b'debugfs 1.47.0\n'),
            ]
            for result in bad_results:
                with mock.patch.object(IMAGE, 'run', return_value=result):
                    with self.assertRaises(ValueError):
                        IMAGE.batch(stream.fileno(), ['set_inode_field <2> ctime @1'], IMAGE.EPOCH, self.work, write=True)

    def test_real_final_checker_failure_prevents_success(self):
        self.require_tools()
        root = self.tree()
        IMAGE.prepare_tree(root)
        original = IMAGE.run
        observed = []

        def checked(args, **kwargs):
            # Every filesystem subprocess receives a held regular output FD.
            image_arg = [arg for arg in args if arg.startswith('/proc/self/fd/')][-1]
            fd = int(image_arg.rsplit('/', 1)[1])
            self.assertIn(fd, kwargs['fds'])
            self.assertTrue(stat.S_ISREG(os.fstat(fd).st_mode))
            observed.append(args[0])
            if args[0] == 'e2fsck':
                raise ValueError('injected final checker failure')
            return original(args, **kwargs)

        with mock.patch.object(IMAGE, 'run', side_effect=checked):
            with self.assertRaisesRegex(ValueError, 'injected final checker failure'):
                IMAGE.make_image(root, self.work / 'checker-failure.ext4', 'A', SIZE)
        self.assertIn('mke2fs', observed)
        self.assertIn('tune2fs', observed)


if __name__ == '__main__':
    unittest.main()
