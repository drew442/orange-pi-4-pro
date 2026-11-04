"""Regular-file packaging checks, without ARM execution or hardware claims."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('storage_tools', Path(__file__).resolve().parents[1] / 'build/host-storage-tools.py')
TOOLS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TOOLS)


class StorageToolsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.binary = b'\x7fELF\x02\x01' + bytes(12) + b'\xb7\x00' + bytes(64)
        self.lock = {'source': 'evidence.json', 'packages': {'fixture': '1'},
                     'files': {'sbin/blkid': {'source': 'usr/sbin/blkid', 'bytes': len(self.binary),
                                             'sha256': hashlib.sha256(self.binary).hexdigest()}},
                     'notices': {'usr/share/licenses/copyright': 'usr/share/doc/copyright'}}
        (self.root / 'configs').mkdir()
        (self.root / 'configs/host-storage-tools.lock.json').write_text(json.dumps(self.lock))
        self.source = self.root / 'rootfs.tar.gz'
        self.archive()
        self.repo_patch = patch.object(TOOLS, 'REPO', self.root)
        self.repo_patch.start()
        self.addCleanup(self.repo_patch.stop)

    def archive(self, kind='regular'):
        with tarfile.open(self.source, 'w:gz') as archive:
            for name, data in [('usr/sbin/blkid', self.binary), ('usr/share/doc/copyright', b'notice\n')]:
                item = tarfile.TarInfo('./' + name)
                item.size = len(data)
                if kind == 'symlink' and name.endswith('blkid'):
                    item.type, item.linkname, item.size = tarfile.SYMTYPE, '/etc/passwd', 0
                    archive.addfile(item)
                else:
                    archive.addfile(item, io.BytesIO(data))
                    if kind == 'duplicate' and name.endswith('blkid'):
                        archive.addfile(item, io.BytesIO(data))
        with self.source.open('rb') as stream:
            (self.root / 'evidence.json').write_text(json.dumps({'files': {'rootfs.tar.gz': TOOLS.digest(stream)}}))

    def test_complete_tree_and_no_overwrite(self):
        output = self.root / 'out/runtime'
        manifest = TOOLS.package(self.source, output)
        self.assertEqual((output / 'sbin/blkid').read_bytes(), self.binary)
        self.assertEqual((output / 'sbin/blkid').stat().st_mode & 0o777, 0o755)
        self.assertEqual((output / 'usr/share/licenses/copyright').read_bytes(), b'notice\n')
        self.assertEqual(manifest['files']['sbin/blkid']['bytes'], len(self.binary))
        with self.assertRaises(FileExistsError):
            TOOLS.package(self.source, output)

    def test_changed_source_rejected_before_output(self):
        with self.source.open('ab') as stream:
            stream.write(b'changed')
        with self.assertRaisesRegex(ValueError, 'hash/size'):
            TOOLS.package(self.source, self.root / 'out/runtime')
        self.assertFalse((self.root / 'out').exists())

    def test_symlink_source_rejected(self):
        alias = self.root / 'alias'
        alias.symlink_to(self.source)
        with self.assertRaises(OSError):
            TOOLS.package(alias, self.root / 'out/runtime')

    def test_selected_links_and_duplicates_rejected(self):
        for kind in ('symlink', 'duplicate'):
            with self.subTest(kind=kind):
                self.archive(kind)
                with self.assertRaisesRegex(ValueError, 'nonregular'):
                    TOOLS.package(self.source, self.root / 'out/runtime')
                self.assertFalse((self.root / 'out').exists())

    def test_pinned_binary_mismatch_rejected(self):
        self.binary += b'wrong'
        self.archive()
        with self.assertRaisesRegex(ValueError, 'pinned binary mismatch'):
            TOOLS.package(self.source, self.root / 'out/runtime')

    def test_output_scope_and_symlink_ancestor(self):
        for target in (self.root / 'elsewhere', self.root / 'out'):
            with self.assertRaisesRegex(ValueError, 'below repository out'):
                TOOLS.package(self.source, target)
        (self.root / 'out').symlink_to(self.root / 'configs')
        with self.assertRaisesRegex(ValueError, 'symlink output ancestor'):
            TOOLS.package(self.source, self.root / 'out/runtime')


if __name__ == '__main__':
    unittest.main()
