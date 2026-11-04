"""File-only initramfs tests; no mounts, board boot or guest execution."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('host_initramfs', Path(__file__).resolve().parents[1] / 'build/host-initramfs.py')
INIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INIT)


class InitramfsTests(unittest.TestCase):
    def test_kernel_requires_overlay_xattrs_and_storage_built_in(self):
        names = ('BLK_DEV_NVME', 'EXT4_FS', 'OVERLAY_FS', 'TMPFS', 'TMPFS_XATTR', 'DEVTMPFS')
        data = '\n'.join('CONFIG_' + name + '=y' for name in names).encode()
        INIT.validate_kernel(data)
        for name in names:
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, name):
                INIT.validate_kernel(data.replace(('CONFIG_' + name + '=y').encode(), ('CONFIG_' + name + '=m').encode()))

    def test_newc_deterministic_and_module_roundtrip(self):
        entries = {}
        release = 'lib/modules/' + INIT.PAYLOAD.KERNEL_RELEASE
        INIT.add(entries, release + '/fixture.ko', stat.S_IFREG | 0o644, b'module')
        INIT.add(entries, 'bin/sh', stat.S_IFLNK | 0o777, b'busybox')
        first = INIT.newc(entries)
        self.assertEqual(first, INIT.newc(dict(reversed(list(entries.items())))))
        self.assertEqual(INIT.PAYLOAD.module_entries(first)[Path(release + '/fixture.ko')][1], b'module')
        self.assertIn(b'bin/sh\0', first)

    def test_duplicate_and_unsafe_archive_paths(self):
        entries = {}
        INIT.add(entries, 'init', stat.S_IFREG | 0o755, b'init')
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            INIT.add(entries, 'init', stat.S_IFREG | 0o755)
        for name in ('/escape', '../escape', 'dir/../../escape'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                INIT.newc({name: (stat.S_IFREG | 0o644, b'')})

    def test_authenticated_descriptor_and_symlink_refusal(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / 'source'
            source.write_bytes(b'expected')
            expected = {'bytes': 8, 'sha256': hashlib.sha256(b'expected').hexdigest()}
            with INIT.read_pinned(source, expected) as stream:
                self.assertEqual(stream.tell(), 0)
                self.assertEqual(stream.read(), b'expected')
            source.write_bytes(b'modified')
            with self.assertRaisesRegex(ValueError, 'mismatch'):
                INIT.read_pinned(source, expected)
            alias = source.with_name('alias')
            alias.symlink_to(source)
            with self.assertRaises(OSError):
                INIT.read_pinned(alias, expected)

    def test_state_uuid_and_output_guards(self):
        arguments = ('missing', 'missing', 'missing')
        for value in ('invalid', '00000000-0000-0000-0000-000000000000',
                      INIT.PAYLOAD.SLOT_UUIDS['A'], 'A7330000-0000-4000-8000-000000000001'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                INIT.build(*arguments, value, '/tmp/invalid')
        with self.assertRaisesRegex(ValueError, 'new repo/out'):
            INIT.build(*arguments, 'e7330000-0000-4000-8000-000000000001', '/tmp/invalid')

    @unittest.skipUnless(all(shutil.which(tool) for tool in ('mke2fs', 'tune2fs', 'debugfs')), 'ext4 tools required')
    def test_marker_from_real_regular_ext4_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / 'root'
            target = root / 'usr/lib/orangepi-hypervisor/payload.json'
            target.parent.mkdir(parents=True)
            for relative, kind in INIT.PAYLOAD.PACKAGE.COMPONENTS.values():
                component = root / relative
                component.parent.mkdir(parents=True, exist_ok=True)
                if kind == 'directory': component.mkdir(exist_ok=True)
                else: component.write_bytes(b'fixture')
            for path in (root, *root.rglob('*')):
                path.chmod(path.stat().st_mode & ~0o022)
            package = INIT.PAYLOAD.PACKAGE.snapshot(root)
            value = {'package_state': package, 'slot': 'A', 'filesystem_uuid': INIT.PAYLOAD.SLOT_UUIDS['A'],
                     'kernel_release': INIT.PAYLOAD.KERNEL_RELEASE}
            data = (json.dumps(value) + '\n').encode()
            target.write_bytes(data)
            image = base / 'root.ext4'
            with image.open('xb') as stream:
                stream.truncate(32 * 1024**2)
            subprocess.run(['mke2fs', '-q', '-F', '-t', 'ext4', '-O', '^has_journal',
                            '-U', INIT.PAYLOAD.SLOT_UUIDS['A'], '-d', str(root), str(image)], check=True)
            with image.open('rb') as stream, self.assertRaisesRegex(ValueError, 'read-only'):
                INIT.root_marker(stream, 'A')
            subprocess.run(['tune2fs', '-O', 'read-only', str(image)], check=True, stdout=subprocess.DEVNULL)
            with image.open('rb') as stream:
                marker, actual = INIT.root_marker(stream, 'A')
            self.assertEqual(marker, data)
            self.assertEqual(actual, value)
            with image.open('rb') as stream, self.assertRaisesRegex(ValueError, 'UUID'):
                INIT.root_marker(stream, 'B')
            subprocess.run(['debugfs', '-w', '-R',
                'set_inode_field /usr/lib/orangepi-hypervisor/package-state/manifest.json size 1', str(image)],
                check=True, capture_output=True)
            with image.open('rb') as stream, self.assertRaisesRegex(ValueError, 'manifest'):
                INIT.root_marker(stream, 'A')
            subprocess.run(['debugfs', '-w', '-R',
                'set_inode_field /usr/lib/orangepi-hypervisor/package-state/FORMAT size 0', str(image)],
                check=True, capture_output=True)
            with image.open('rb') as stream, self.assertRaisesRegex(ValueError, 'format'):
                INIT.root_marker(stream, 'A')


if __name__ == '__main__':
    unittest.main()
