#!/usr/bin/env python3
"""Small real ext4 fixture checks only; does not open the full diagnostic image."""
import base64
import copy
import json
import shutil
import struct
import hashlib
import importlib.util
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('readback', Path(__file__).resolve().parents[1] / 'build/verify-host-roots.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class ReadbackTests(unittest.TestCase):
    def test_fail_closed_parsers(self):
        for text in ('garbage', '/2/040755/0/0/.//\n', '/0/040755/0/0/.//\n/2/040755/0/0/..//\n'):
            with self.assertRaises(ValueError):
                m.parse_listing(text)
        with self.assertRaises(ValueError):
            m.parse_xattrs('Extended attributes:\n  unsupported format\n')
        with tempfile.TemporaryDirectory(prefix='a733-reader-') as directory:
            bad = subprocess.CompletedProcess([], 0, b'debugfs: stat <2>\n', b'debugfs 1.47.0\nstat: failure\n')
            with patch.object(m.subprocess, 'run', return_value=bad):
                with self.assertRaisesRegex(ValueError, 'diagnostic/error'):
                    m.Reader(999, directory).batch(['stat <2>'])

    def test_unsafe_names_and_symlink_inputs(self):
        with tempfile.TemporaryDirectory(prefix='a733-reader-') as directory:
            regular = Path(directory) / 'file'
            regular.write_bytes(b'a')
            link = Path(directory) / 'link'
            link.symlink_to(regular)
            with self.assertRaises(OSError):
                m.open_regular(link)
        self.assertTrue(m.valid_name_text('Főtanúsítvány.pem'))
        for invalid in ('a\nb', 'a\rb', 'a\tb', 'a\x7fb', '\udcff'):
            self.assertFalse(m.valid_name_text(invalid))
        with self.assertRaises(ValueError):
            m.validate_members({'.': {'type': 'dir', 'mode': 493, 'uid': 0, 'gid': 0, 'xattrs': {}}, 'a\nb': {'type': 'file'}})

    def test_held_inputs_pin_identity_parent_and_special_rejection(self):
        with tempfile.TemporaryDirectory(prefix='a733-held-') as directory:
            root = Path(directory)
            source = root / 'source'
            source.write_bytes(b'authentic')
            expected = {'bytes': 9, 'sha256': hashlib.sha256(b'authentic').hexdigest()}
            with m.Held(source, expected) as held:
                self.assertEqual(held.pin, expected)
            with self.assertRaisesRegex(ValueError, 'hash pin'):
                m.Held(source, dict(expected, sha256='0' * 64))
            with self.assertRaisesRegex(ValueError, 'exceeds bound'):
                m.Held(source, max_bytes=1)
            held = m.Held(source, expected)
            replacement = root / 'replacement'
            replacement.write_bytes(b'authentic')
            replacement.replace(source)
            try:
                with self.assertRaisesRegex(ValueError, 'identity changed'):
                    held.unchanged()
            finally:
                held.close()
            alias = root / 'alias'
            alias.hardlink_to(source)
            with self.assertRaisesRegex(ValueError, 'single-link regular'):
                m.Held(source)
            fifo = root / 'fifo'
            os.mkfifo(fifo)
            with self.assertRaisesRegex(ValueError, 'single-link regular'):
                m.Held(fifo)
            linked_parent = root / 'linked-parent'
            linked_parent.symlink_to(root, target_is_directory=True)
            with self.assertRaises(OSError):
                m.Held(linked_parent / 'source')
        for source in ('/proc/version', '/sys/kernel/notes', '/dev/null'):
            with self.assertRaisesRegex(ValueError, 'pseudo'):
                m.Held(Path(source))

    def test_json_duplicates_bounds_and_output_creation_guards(self):
        with tempfile.TemporaryDirectory(prefix='a733-json-') as directory:
            root = Path(directory)
            source = root / 'json'
            for data in (b'{"a":1,"a":2}', b'{"a":NaN}', b'[]'):
                source.write_bytes(data)
                with m.Held(source) as held:
                    with self.assertRaises(ValueError):
                        m.json_fd(held)
            source.write_bytes(b'{"long":"value"}')
            with m.Held(source) as held, patch.object(m, 'MAX_JSON', 4):
                with self.assertRaisesRegex(ValueError, 'exceeds bound'):
                    m.json_fd(held)
            fresh = root / 'fresh'
            self.assertEqual(m.new_output(fresh), fresh)
            self.assertEqual(stat.S_IMODE(fresh.stat().st_mode), 0o700)
            with self.assertRaises(ValueError):
                m.new_output(fresh)
            for path in (root / 'unsafe space', Path('/dev/output'), root / '..' / 'unsafe'):
                with self.assertRaises(ValueError):
                    m.new_output(path)
            link = root / 'link'
            link.symlink_to(fresh, target_is_directory=True)
            with self.assertRaises(ValueError):
                m.new_output(link / 'out')

    def test_invalid_pinned_receipt_retains_failure_and_never_overwrites(self):
        with tempfile.TemporaryDirectory(prefix='a733-receipt-') as directory:
            root = Path(directory)
            (root / 'producer').mkdir()
            producer = root / 'producer/receipt.json'
            producer.write_text('{"schema":1,"success":false}')
            for name, expected_hash, error in (
                ('badpin', '0' * 64, 'receipt hash pin'),
                ('badreceipt', hashlib.sha256(producer.read_bytes()).hexdigest(), 'closed root build')):
                output = root / name
                with self.assertRaisesRegex(ValueError, error):
                    m.readback(producer, expected_hash, output)
                receipt = json.loads((output / 'receipt.json').read_text())
                self.assertFalse(receipt['success'])
                self.assertEqual(receipt['state'], 'failed')
                self.assertFalse(receipt['hardware_acceptance'])
                self.assertTrue((output / 'controller.py').is_file())
                with self.assertRaisesRegex(ValueError, 'output must be new'):
                    m.readback(producer, expected_hash, output)

    def test_output_inside_producer_is_rejected_before_any_creation(self):
        with tempfile.TemporaryDirectory(prefix='a733-readonly-') as directory:
            phase = Path(directory)
            receipt = phase / 'receipt.json'
            receipt.write_text('{}')
            output = phase / 'forbidden-review'
            with self.assertRaisesRegex(ValueError, 'outside producer phase'):
                m.readback(receipt, hashlib.sha256(receipt.read_bytes()).hexdigest(), output)
            self.assertFalse(output.exists())

    def test_character_metadata_parser(self):
        def device_stat(device):
            return ('Inode: 99   Type: character special    Mode:  0600   Flags: 0x0\n'
                    'User: 0   Group: 5   Project: 0   Size: 0\n'
                    ' mtime: 0x6553f100:00000000 -- Tue Nov 14 22:13:20 2023\n'
                    'Links: 1   Blockcount: 0\n'
                    'Device major/minor number: ' + device + '\n')
        actual = m.parse_stat(device_stat('1:3'), 99)
        self.assertEqual(actual['device'], [1, 3])
        self.assertEqual(actual['mode'], 0o600)
        self.assertEqual(actual['gid'], 5)
        self.assertNotEqual(actual['device'], [1, 5])
        with self.assertRaises(ValueError):
            m.parse_stat(device_stat('invalid'), 99)

    def test_source_policy_is_closed_and_binds_source_helpers(self):
        repo = Path(__file__).resolve().parents[1]
        source_names = ('build/host-payload.py', 'build/launch-host-roots.py', 'build/userspace-profiles.py',
                        'build/timesync-state.py', 'build/rootfs-metadata.py',
                        'configs/host-userspace-profiles.lock.json', 'docs/evidence/diagnostic-20261104.json')
        files = {name: {'bytes': len((repo / name).read_bytes()),
                        'sha256': hashlib.sha256((repo / name).read_bytes()).hexdigest(),
                        'git_blob': 'a' * 40} for name in source_names}
        pin = lambda name: {key: files[name][key] for key in ('bytes', 'sha256')}
        policy = {'archive': m.ARCHIVE, 'binding': {'profile': m.PROFILE, 'archive': m.ARCHIVE,
                   'resolver': pin('build/userspace-profiles.py'),
                   'profile_lock': pin('configs/host-userspace-profiles.lock.json')},
                  'diagnostic': json.loads((repo / 'docs/evidence/diagnostic-20261104.json').read_text())['files'],
                  'clock': {'schema': 1, 'identity': 996, 'directory': 'var/lib/systemd/timesync',
                            'helper': pin('build/timesync-state.py')}}
        policy['diagnostic'] = {name: policy['diagnostic'][name] for name in
                                ('Image', 'board.dtb', 'kernel.config', 'initramfs.cpio.gz')}
        receipt = {'schema': 1, 'success': True, 'state': 'complete', 'hardware_acceptance': False,
                   'slot_bytes': m.SLOT_BYTES, 'userspace_profile': m.PROFILE, 'metadata_profile': m.METADATA,
                   'host_inventory_unchanged': True, 'mapped_processes_final': [], 'phase_mounts_final': [],
                   'sources': {'revision': 'b' * 40, 'files': files}, 'policy': policy}
        self.assertEqual(m.source_policy(receipt)['metadata_helper'], pin('build/rootfs-metadata.py'))
        for key, value in (('schema', True), ('success', False), ('metadata_profile', 'native'),
                           ('slot_bytes', 1024**3), ('userspace_profile', 'arbitrary')):
            bad = copy.deepcopy(receipt)
            bad[key] = value
            with self.assertRaises(ValueError):
                m.source_policy(bad)
        bad = copy.deepcopy(receipt)
        bad['policy']['binding']['resolver']['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'source binding'):
            m.source_policy(bad)

    def test_real_ext4_readback_and_discrepancies(self):
        with tempfile.TemporaryDirectory(prefix='a733-reader-') as directory:
            work = Path(directory)
            root = work / 'root'
            root.mkdir()
            root.chmod(0o755)
            (root / 'dir').mkdir()
            (root / 'dir/file').write_bytes(b'full contents\0\xff\n')
            os.link(root / 'dir/file', root / 'hard')
            (root / 'Főtanúsítvány.pem').write_bytes('Hungarian certificate fixture ő'.encode('utf-8'))
            (root / 'short').symlink_to('dir/file')
            (root / 'long').symlink_to('dir/' + 'a' * 96)
            os.setxattr(root / 'dir/file', 'user.binary', b'\0\xffbinary\n')
            for p in [root / 'dir/file', root / 'hard', root / 'short', root / 'long', root / 'dir', root / 'Főtanúsítvány.pem', root]:
                os.utime(p, ns=(1700000000123456789, 1700000000123456789), follow_symlinks=False)
            members = {}
            for name in ('.', 'dir', 'dir/file', 'hard', 'short', 'long', 'Főtanúsítvány.pem'):
                path = root / name
                meta = path.lstat()
                kind = 'dir' if stat.S_ISDIR(meta.st_mode) else 'symlink' if stat.S_ISLNK(meta.st_mode) else 'hardlink' if name == 'hard' else 'file'
                item = dict(type=kind, mode=stat.S_IMODE(meta.st_mode), uid=meta.st_uid, gid=meta.st_gid, mtime_ns=meta.st_mtime_ns, xattrs={key: base64.b64encode(os.getxattr(path, key, follow_symlinks=False)).decode() for key in os.listxattr(path, follow_symlinks=False)})
                if kind in ('file', 'hardlink'):
                    data = path.read_bytes()
                    item['data'] = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                if kind == 'hardlink':
                    item['link'] = 'dir/file'
                elif kind == 'symlink':
                    item['link'] = os.readlink(path)
                members[name] = item
            # mke2fs creates its root inode as root:root, independent of -d root owner.
            members['.'].update(uid=0, gid=0, mode=0o755)
            image = work / 'fixture.ext4'
            with image.open('wb') as stream:
                stream.truncate(32 * 1024**2)
            subprocess.run(['mke2fs', '-q', '-F', '-t', 'ext4', '-O', '^has_journal', '-E', 'lazy_itable_init=0', '-U', m.SLOT_UUIDS['A'], '-d', str(root), str(image)], check=True)
            subprocess.run(['tune2fs', '-O', 'read-only', str(image)], check=True, stdout=subprocess.PIPE)
            fd = m.open_regular(image)
            initial = m.digest_fd(fd)
            try:
                def check(expected):
                    out = work / ('read-' + str(len(list(work.glob('read-*')))))
                    out.mkdir()
                    return m.verify(fd, expected, out, root_time=m.superblock(fd, 'A'), progress=False)
                receipt = check(members)
                self.assertEqual(receipt['namespace_members'], 7)
                self.assertEqual(receipt['root_creation_time_seconds'], m.superblock(fd, 'A'))
                self.assertIn('actual superblock', receipt['image_mtime_policy'])
                self.assertEqual(receipt['unique_regular_inodes'], 2)
                self.assertEqual(receipt['fast_symlinks'], 1)
                self.assertEqual(receipt['long_symlinks_dumped'], 1)
                self.assertEqual(receipt['raw_xattrs'], 1)  # one raw attribute on a shared inode
                self.assertEqual(receipt['hardlink_groups'], [['dir/file', 'hard']])
                bad = __import__('copy').deepcopy(members)
                bad['dir/file']['data']['sha256'] = bad['hard']['data']['sha256'] = '0' * 64
                with self.assertRaisesRegex(ValueError, 'regular contents differ'):
                    check(bad)
                bad = __import__('copy').deepcopy(members)
                bad['short']['link'] = 'dir/fail'
                with self.assertRaisesRegex(ValueError, 'fast symlink target differs'):
                    check(bad)
                bad = __import__('copy').deepcopy(members)
                bad['dir']['mtime_ns'] += 10**9
                with self.assertRaisesRegex(ValueError, 'mtime differs'):
                    check(bad)
                bad = __import__('copy').deepcopy(members)
                del bad['long']
                with self.assertRaisesRegex(ValueError, 'namespace mismatch'):
                    check(bad)
                bad = __import__('copy').deepcopy(members)
                bad['unexpected'] = dict(bad['Főtanúsítvány.pem'])
                with self.assertRaisesRegex(ValueError, 'namespace mismatch'):
                    check(bad)
                bad = __import__('copy').deepcopy(members)
                bad['dir/file']['xattrs']['user.binary'] = base64.b64encode(b'wrong value').decode()
                bad['hard']['xattrs']['user.binary'] = bad['dir/file']['xattrs']['user.binary']
                with self.assertRaisesRegex(ValueError, 'xattr namespace/size'):
                    check(bad)
                with patch.object(m.shutil, 'disk_usage', return_value=type('Usage', (), {'free': 0})()):
                    with self.assertRaisesRegex(ValueError, 'scratch budget'):
                        check(members)
                bad = __import__('copy').deepcopy(members)
                bad['hard']['type'] = 'file'
                del bad['hard']['link']
                with self.assertRaisesRegex(ValueError, 'hardlink topology'):
                    check(bad)
                self.assertEqual(m.digest_fd(fd), initial)
                block = bytearray(os.pread(fd, 1024, 1024))
                struct.pack_into('<I', block, 100, struct.unpack_from('<I', block, 100)[0] & ~0x1000)
                with patch.object(m.os, 'pread', return_value=bytes(block)):
                    with self.assertRaisesRegex(ValueError, 'read-only without journal'):
                        m.superblock(fd, 'A')
                with self.assertRaisesRegex(ValueError, 'UUID'):
                    m.superblock(fd, 'B')
            finally:
                os.close(fd)


if __name__ == '__main__':
    unittest.main()
