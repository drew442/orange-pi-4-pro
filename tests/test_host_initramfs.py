"""File-only initramfs tests; no mounts, board boot or guest execution."""
import contextlib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import struct
import sys
import tempfile
import unittest
import uuid
from unittest import mock

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


class BoundedMetadataTests(unittest.TestCase):
    def command(self, program):
        return [sys.executable, '-c', program]

    @contextlib.contextmanager
    def tracked_children(self):
        original = subprocess.Popen
        children = []
        def spawn(*args, **kwargs):
            child = original(*args, **kwargs)
            children.append(child)
            return child
        with mock.patch.object(INIT.subprocess, 'Popen', side_effect=spawn):
            yield children
        self.assertTrue(children)
        for child in children:
            self.assertIsNotNone(child.returncode)
            self.assertTrue(child.stdout.closed)
            with self.assertRaises(ChildProcessError):
                os.waitpid(child.pid, os.WNOHANG)

    def test_output_at_exact_bound_succeeds_and_reaps(self):
        with self.tracked_children():
            self.assertEqual(INIT.bounded_output(self.command("import os; os.write(1, b'x' * 128)"),
                                                  128, timeout=2), b'x' * 128)

    def test_continuous_overlimit_output_killed_without_accumulating(self):
        with self.tracked_children(), self.assertRaisesRegex(ValueError, 'size bound'):
            INIT.bounded_output(self.command("import os\nwhile True: os.write(1, b'x' * 4096)"),
                                128, timeout=2)

    def test_silent_hang_times_out_and_reaps(self):
        with self.tracked_children(), self.assertRaisesRegex(TimeoutError, 'deadline'):
            INIT.bounded_output(self.command('import time; time.sleep(60)'), 128, timeout=0.1)

    def test_stdout_eof_then_hang_times_out_and_reaps(self):
        with self.tracked_children(), self.assertRaisesRegex(TimeoutError, 'deadline'):
            INIT.bounded_output(self.command('import os,time; os.close(1); time.sleep(60)'),
                                128, timeout=0.1)

    def test_nonzero_exit_rejected_and_reaped(self):
        with self.tracked_children(), self.assertRaises(subprocess.CalledProcessError):
            INIT.bounded_output(self.command('raise SystemExit(3)'), 128, timeout=2)

    def marker_stream(self):
        stream = tempfile.TemporaryFile()
        self.addCleanup(stream.close)
        data = bytearray(2048)
        data[1024 + 56:1024 + 58] = b'\x53\xef'
        struct.pack_into('<I', data, 1024 + 100, 0x1000)
        data[1024 + 104:1024 + 120] = uuid.UUID(INIT.PAYLOAD.SLOT_UUIDS['A']).bytes
        stream.write(data)
        stream.flush()
        return stream

    def test_duplicate_and_nonobject_marker_rejected(self):
        for content in (b'{"slot":"A","slot":"A"}', b'{"nested":{"x":1,"x":2}}', b'[]'):
            with self.subTest(content=content), mock.patch.object(INIT, 'debugfs_content', return_value=content):
                with self.assertRaises(ValueError):
                    INIT.root_marker(self.marker_stream(), 'A')

    def test_marker_limit_enforced_before_json_parse(self):
        original = subprocess.Popen
        children = []
        def overlimit(command, **kwargs):
            child = original(self.command("import os\nwhile True: os.write(1, b'x' * 4096)"), **kwargs)
            children.append(child)
            return child
        with mock.patch.object(INIT.subprocess, 'Popen', side_effect=overlimit), \
                mock.patch.object(INIT.json, 'loads', side_effect=AssertionError('JSON parser reached')):
            with self.assertRaisesRegex(ValueError, 'size bound'):
                INIT.root_marker(self.marker_stream(), 'A')
        self.assertTrue(children)
        self.assertIsNotNone(children[0].returncode)

    def test_all_metadata_reads_receive_fixed_caps(self):
        profile = {'profile': INIT.PAYLOAD.PACKAGE.PROFILE,
                   'manifest': {'bytes': 1, 'sha256': 'a' * 64}}
        metadata = {'slot': 'A', 'filesystem_uuid': INIT.PAYLOAD.SLOT_UUIDS['A'],
                    'kernel_release': INIT.PAYLOAD.KERNEL_RELEASE, 'package_state': profile}
        marker = json.dumps(metadata).encode()
        with mock.patch.object(INIT, 'debugfs_content', side_effect=[marker, INIT.PAYLOAD.PACKAGE.FORMAT, b'x']) as read, \
                mock.patch.object(INIT.PAYLOAD.PACKAGE, 'validate_manifest'):
            actual, value = INIT.root_marker(self.marker_stream(), 'A')
        self.assertEqual(actual, marker)
        self.assertEqual(value, metadata)
        self.assertEqual([call.args[2] for call in read.call_args_list],
                         [64 * 1024, 128, INIT.PAYLOAD.PACKAGE.MANIFEST_LIMIT])


if __name__ == '__main__':
    unittest.main()
