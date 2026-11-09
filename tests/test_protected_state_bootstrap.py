"""Small software fixtures; no production build, mount, device or hardware claim."""
import copy
import gzip
import importlib.util
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import tempfile
import unittest
from unittest import mock
import zlib

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('protected_state', ROOT / 'build/protected-state-bootstrap.py')
P = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(P)


def newc(items):
    output = bytearray()
    for index, (name, data, mode) in enumerate(items + [('TRAILER!!!', b'', 0)]):
        name = name.encode() + b'\0'
        values = [index + 1, mode, 42, 43, 1, 123, len(data), 0, 0, 0, 0, len(name), 0]
        output += b'070701' + ''.join(f'{v:08x}' for v in values).encode() + name
        output += bytes(-len(output) % 4)
        output += data
        output += bytes(-len(output) % 4)
    return bytes(output)


def wrapper(archive):
    header = struct.pack('>7I4B32s', 0x27051956, 0, P.EPOCH, 0, 0, 0, 0, 5, 22, 3, 1, b'original ramdisk')
    return P.rewrap(header, archive)


class ProtectedStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='protected-state-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.archive = newc([('init', b'old init', stat.S_IFREG | 0o755),
                             ('etc/env', b'STATE=360d', stat.S_IFREG | 0o644),
                             ('bin/busybox', b'unchanged binary', stat.S_IFREG | 0o755),
                             ('bin/sh', b'busybox', stat.S_IFLNK | 0o777)])

    def test_only_init_bytes_size_change_metadata_and_closure_preserved(self):
        changed, proof = P.replace_init(self.archive, b'new init contents')
        before, _ = P.entries(self.archive)
        after, _ = P.entries(changed)
        self.assertEqual(proof['before_init'], P.record(b'old init'))
        self.assertEqual(proof['after_init'], P.record(b'new init contents'))
        for name in before:
            if name != 'init':
                self.assertEqual(before[name]['raw'], after[name]['raw'])
            else:
                expected = before[name]['fields'][:]
                expected[6] = len(b'new init contents')
                self.assertEqual(expected, after[name]['fields'])
        self.assertEqual(P.replace_init(self.archive, b'new init contents')[0], changed)

    def test_legacy_crc_length_and_header_preservation(self):
        old = wrapper(self.archive)
        header, archive = P.unwrap(old)
        self.assertEqual(archive, self.archive)
        changed, _ = P.replace_init(archive, b'new')
        candidate = P.rewrap(header, changed)
        self.assertEqual(candidate, P.rewrap(header, changed))
        self.assertEqual(P.unwrap(candidate)[1], changed)
        allowed = set(range(4, 8)) | set(range(12, 16)) | set(range(24, 28))
        self.assertTrue(all(old[i] == candidate[i] for i in range(64) if i not in allowed))
        for offset in (4, 12, 24, 63, len(old) - 1):
            bad = bytearray(old)
            bad[offset] ^= 1
            with self.subTest(offset=offset), self.assertRaises((ValueError, zlib.error)):
                P.unwrap(bad)
        with self.assertRaises(ValueError):
            P.unwrap(old[:-1])

    def test_gzip_bomb_trailing_and_multimember_rejected(self):
        def raw_wrap(payload):
            header = bytearray(wrapper(self.archive)[:64])
            struct.pack_into('>I', header, 12, len(payload))
            struct.pack_into('>I', header, 24, zlib.crc32(payload))
            header[4:8] = bytes(4)
            struct.pack_into('>I', header, 4, zlib.crc32(header))
            return bytes(header) + payload
        for payload in (gzip.compress(self.archive) + b'junk',
                        gzip.compress(self.archive) + gzip.compress(b'x'), gzip.compress(self.archive)[:-3]):
            with self.assertRaises((ValueError, zlib.error)):
                P.unwrap(raw_wrap(payload))
        with mock.patch.object(P, 'MAX_ARCHIVE', 32), self.assertRaises(ValueError):
            P.unwrap(raw_wrap(gzip.compress(b'0' * 100000)))

    def test_newc_malformed_unsafe_duplicate_and_init_bounds(self):
        for name in ('../escape', '/absolute', 'a//b', 'a/./b'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                P.entries(newc([(name, b'x', stat.S_IFREG | 0o755)]))
        for data in (self.archive[:-1], self.archive + b'x', b'bad',
                     newc([('init', b'x', stat.S_IFREG), ('init', b'y', stat.S_IFREG)])):
            with self.assertRaises(ValueError):
                P.entries(data)
        for data in (b'', b'x' * (P.MAX_INIT + 1)):
            with self.assertRaises(ValueError):
                P.replace_init(self.archive, data)
        with self.assertRaises(ValueError):
            P.replace_init(newc([('init', b'x', stat.S_IFLNK)]), b'new')
        header = bytearray(self.archive)
        header[54:62] = b'ffffffff'
        with self.assertRaises(ValueError):
            P.entries(header)

    def test_input_paths_hash_size_and_mutation(self):
        path = self.root / 'input'
        path.write_bytes(b'input')
        with self.assertRaisesRegex(ValueError, 'hash/size'):
            P.RUNTIME.HeldInput(path, 10, P.record(b'other'))
        with self.assertRaisesRegex(ValueError, 'size bound'):
            P.RUNTIME.HeldInput(path, 1)
        with P.RUNTIME.HeldInput(path, 10, P.record(b'input')) as held:
            path.write_bytes(b'evil!')
            with self.assertRaises(ValueError):
                held.verify()
        link = self.root / 'link'
        link.symlink_to(path)
        for bad in (link, Path('/dev/null'), Path('/proc/meminfo'), Path('relative'), self.root / '..' / 'input'):
            with self.subTest(path=bad), self.assertRaises((ValueError, OSError)):
                P.RUNTIME.HeldInput(bad, 10)
        os.link(path, self.root / 'hardlink')
        with self.assertRaises(ValueError):
            P.RUNTIME.HeldInput(path, 10)

    def test_output_scope_existing_and_resources(self):
        with mock.patch.object(P, 'REPO', self.root):
            (self.root / 'out').mkdir()
            good = self.root / 'out/protected-state-bootstrap-test'
            self.assertEqual(P.output_path(good), good)
            good.mkdir()
            for bad in (good, self.root / 'out/other', self.root / 'out/../bad', Path('relative')):
                with self.assertRaises(ValueError):
                    P.output_path(bad)
        with mock.patch.object(P.shutil, 'disk_usage', return_value=mock.Mock(free=P.DISK_FLOOR)), \
                mock.patch.object(P.Path, 'read_text', return_value='MemAvailable: 2000000 kB\n'):
            P.resources(self.root)
            with self.assertRaisesRegex(ValueError, 'scratch'):
                P.resources(self.root, admission=True)
        with mock.patch.object(P.shutil, 'disk_usage', return_value=mock.Mock(free=10**10)), \
                mock.patch.object(P.Path, 'read_text', return_value='MemAvailable: 1 kB\n'):
            with self.assertRaisesRegex(ValueError, 'memory'):
                P.resources(self.root)

    def test_namespace_rejects_any_other_data_metadata_or_entry_change(self):
        before = {'boot/uInitrd': {'data': P.record(b'old'), 'metadata': {'size': 3}},
                  'boot/boot.cmd': {'data': P.record(b'cmd'), 'metadata': {'size': 3}}}
        after = copy.deepcopy(before)
        after['boot/uInitrd']['data'] = P.record(b'new-long')
        after['boot/uInitrd']['metadata']['size'] = 8
        P.compare_namespace(before, after, P.record(b'new-long'))
        for kind in ('command', 'metadata', 'extra'):
            bad = copy.deepcopy(after)
            if kind == 'command':
                bad['boot/boot.cmd']['data'] = P.record(b'bad')
            elif kind == 'metadata':
                bad['boot/uInitrd']['metadata']['mode'] = 0o777
            else:
                bad['extra'] = {}
            with self.assertRaises(ValueError):
                P.compare_namespace(before, bad, P.record(b'new-long'))

    @unittest.skipUnless(all(shutil.which(t) for t in ('mke2fs', 'debugfs', 'e2fsck')), 'ext4 tools required')
    def test_small_regular_ext4_patch_preserves_complete_namespace(self):
        tree = self.root / 'tree'
        for name in P.HELP.BOOT_FILES:
            target = tree / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(name.encode())
        image = self.root / 'boot.ext4'
        with image.open('wb') as stream:
            stream.truncate(16 * 1024**2)
        subprocess.run(['mke2fs', '-q', '-t', 'ext4', '-F', '-d', str(tree), str(image)], check=True, capture_output=True)
        before = P.HELP.namespace(image, self.root / 'before')
        payload = self.root / 'uInitrd'
        payload.write_bytes(wrapper(self.archive))
        P.patch_boot(image, payload, before, self.root / 'patch.log')
        after = P.HELP.namespace(image, self.root / 'after')
        P.compare_namespace(before, after, P.record(payload.read_bytes()))


if __name__ == '__main__':
    unittest.main()
