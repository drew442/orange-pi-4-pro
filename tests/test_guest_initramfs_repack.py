"""Host-only archive mutation and path guards; no guest execution."""
import gzip
import importlib.util
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('guest_repack', REPO / 'build/repack-guest-initramfs.py')
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)


def entry(name, data=b'', mode=stat.S_IFREG | 0o755):
    name = name.encode() + b'\0'
    fields = [0, mode, 0, 0, 1, 1793750400, len(data), 0, 0, 0, 0, len(name), 0]
    result = b'070701' + b''.join(f'{v:08x}'.encode() for v in fields) + name
    result += b'\0' * (-len(result) % 4)
    result += data + b'\0' * (-len(data) % 4)
    return result


def archive(*items):
    return b''.join(items) + entry('TRAILER!!!', mode=0) + b'\0' * 20


class RepackTests(unittest.TestCase):
    def setUp(self):
        self.original = archive(entry('.', mode=stat.S_IFDIR | 0o755), entry('./init', b'old-init\n'),
                                entry('bin/busybox', b'unchanged-busybox'))

    def test_replace_only_init_preserves_remaining_raw_entries_and_metadata(self):
        updated, preserved = R.replace_init(self.original, b'longer replacement\n')
        old = R.entries(self.original)
        new = R.entries(updated)
        self.assertEqual(next(e['data'] for e in new if e['name'] == 'init'), b'longer replacement\n')
        for before, after in zip(old, new):
            if before['name'] != 'init':
                self.assertEqual(self.original[before['start']:before['end']], updated[after['start']:after['end']])
        self.assertEqual(len(preserved), len(old) - 1)
        self.assertEqual(R.replace_init(self.original, b'longer replacement\n')[0], updated)

    def test_malformed_and_truncated_archive(self):
        for raw in (b'', b'bad', b'070701' + b'z' * 104, self.original[:109], self.original[:-25],
                    self.original + b'X', b'X' * (R.MAX_RAW + 1)):
            with self.subTest(size=len(raw)), self.assertRaises(ValueError):
                R.entries(raw)

    def test_require_single_regular_0755_init(self):
        for raw in (archive(entry('other')), archive(entry('init'), entry('./init')),
                    archive(entry('init', mode=stat.S_IFLNK | 0o755)),
                    archive(entry('init', mode=stat.S_IFREG | 0o644))):
            with self.assertRaises(ValueError):
                R.entries(raw)

    def test_refuse_unsafe_names(self):
        for name in ('/init', '../init', 'dir/../init', 'dir//init'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                R.entries(archive(entry(name), entry('init')))

    def test_duplicate_non_init_and_truncated_names_or_data(self):
        for raw in (archive(entry('init'), entry('bin/tool'), entry('./bin/tool')),
                    entry('init')[:112], entry('init', b'contents')[:-6],
                    archive(entry('init')).replace(b'init\0', b'initX', 1)):
            with self.assertRaises(ValueError):
                R.entries(raw)

    def test_authenticated_original_and_unsafe_output(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / 'source.gz'
            source.write_bytes(gzip.compress(self.original, mtime=0))
            with self.assertRaises(ValueError):
                R.build(source, base / 'result')
            with patch.object(R, 'ORIGINAL', R.HOST.record(source.read_bytes())):
                with self.assertRaises(ValueError):
                    R.build(source, base / 'result')
                (base / 'link.gz').symlink_to(source)
                with self.assertRaises(ValueError):
                    R.build(base / 'link.gz', base / 'result')
                with self.assertRaises(ValueError):
                    R.build(source, Path('/dev/result'))
                output_link = R.REPO / 'out' / (base.name + '-symlink')
                output_link.symlink_to(base, target_is_directory=True)
                try:
                    with self.assertRaises(ValueError):
                        R.build(source, output_link)
                    with self.assertRaises(ValueError):
                        R.build(source, output_link / 'guest-initramfs-child')
                finally:
                    output_link.unlink()

    def test_build_deterministic_no_overwrite_and_manifest(self):
        with tempfile.TemporaryDirectory(dir=REPO / 'out', prefix='guest-initramfs-test-') as directory:
            existing = Path(directory)
            source = existing / 'source.gz'
            source.write_bytes(gzip.compress(self.original, mtime=0))
            first = existing.with_name(existing.name + '-a')
            second = existing.with_name(existing.name + '-b')
            try:
                with patch.object(R, 'ORIGINAL', R.HOST.record(source.read_bytes())):
                    one = R.build(source, first)
                    two = R.build(source, second)
                    self.assertEqual((first / 'initramfs.cpio.gz').read_bytes(), (second / 'initramfs.cpio.gz').read_bytes())
                    self.assertEqual(one, two)
                    self.assertTrue(one['all_other_entries_preserved'])
                    self.assertEqual(one['init'], R.HOST.record((REPO / 'configs/guest-init').read_bytes()))
                    with self.assertRaises(ValueError):
                        R.build(source, first)
                    with self.assertRaises(ValueError):
                        R.build(source, existing)
                    source.write_bytes(source.read_bytes() + b'X')
                    with self.assertRaises(ValueError):
                        R.build(source, second)
            finally:
                for output in (first, second):
                    if output.exists():
                        for path in output.iterdir():
                            path.unlink()
                        output.rmdir()

    def test_decompression_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'large.gz'
            source.write_bytes(gzip.compress(b'\0' * (R.MAX_RAW + 1), mtime=0))
            with patch.object(R, 'ORIGINAL', R.HOST.record(source.read_bytes())):
                with self.assertRaisesRegex(ValueError, 'decompressed'):
                    R.build(source, REPO / 'out/guest-initramfs-oversize-test')


if __name__ == '__main__':
    unittest.main()
