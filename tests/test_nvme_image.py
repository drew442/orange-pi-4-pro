"""Integration contracts and regular-file-only sparse placement regressions."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('nvme_image_tests', REPO / 'build/nvme-image.py')
IMAGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(IMAGE)


class NVMeImageTests(unittest.TestCase):
    def setUp(self):
        self.roots = json.loads((REPO / 'docs/evidence/managed-payload-20261104.json').read_text())
        self.state = json.loads((REPO / 'docs/evidence/managed-state-fixture-20261104.json').read_text())['manifest']
        self.sd = json.loads((REPO / 'docs/evidence/host-sd-20261104.json').read_text())
        self.diag = json.loads((REPO / 'docs/evidence/diagnostic-20261104.json').read_text())['files']

    def test_pinned_payload_state_bootstrap_pairing(self):
        self.assertEqual(IMAGE.validate_pairing(self.roots, self.state, self.sd, self.diag), self.state['state_uuid'])

    def test_reject_mismatched_state_bootstrap_and_root_profile(self):
        for where, field, value in (('sd', 'state_uuid', 'd' * 36), ('sd', 'kernel_release', 'other'),
                                    ('roots', 'management_runtime', {}), ('roots', 'slot_bytes', 1)):
            values = {'roots': copy.deepcopy(self.roots), 'state': copy.deepcopy(self.state), 'sd': copy.deepcopy(self.sd)}
            values[where][field] = value
            with self.subTest(where=where, field=field), self.assertRaises(ValueError):
                IMAGE.validate_pairing(values['roots'], values['state'], values['sd'], self.diag)
        changed = copy.deepcopy(self.sd)
        changed['boot_inputs']['board.dtb']['sha256'] = 'f' * 64
        with self.assertRaises(ValueError):
            IMAGE.validate_pairing(self.roots, self.state, changed, self.diag)

    def test_sparse_placement_preserves_source_and_zero_padding(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, target = root / 'source', root / 'target'
            data = b'header' + bytes(2 * 1024**2) + b'tail'
            source.write_bytes(data)
            fd = os.open(target, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.ftruncate(fd, len(data) + 8192)
                IMAGE.place_sparse(source, fd, 4096, len(data))
            finally:
                os.close(fd)
            self.assertEqual(target.read_bytes(), bytes(4096) + data + bytes(4096))
            self.assertEqual(source.read_bytes(), data)

    def test_sparse_placement_rejects_devices_symlinks_and_bounds(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source'
            source.write_bytes(b'test')
            alias = root / 'alias'
            alias.symlink_to(source)
            fd = os.open(root / 'target', os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.ftruncate(fd, 8)
                for path, offset, size in ((alias, 0, 4), (source, 7, 4), (source, 0, 5), (source, -1, 4)):
                    with self.subTest(path=path, offset=offset, size=size), self.assertRaises(ValueError):
                        IMAGE.place_sparse(path, fd, offset, size)
            finally:
                os.close(fd)
            fd = os.open('/dev/null', os.O_RDONLY)
            try:
                with self.assertRaises(ValueError):
                    IMAGE.place_sparse(source, fd, 0, 4)
            finally:
                os.close(fd)

    def test_output_storage_rejection_precedes_staging(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directories = []
            for name, manifest in (('roots', self.roots), ('state', self.state), ('sd', self.sd)):
                directory = root / name
                directory.mkdir()
                (directory / 'manifest.json').write_text(json.dumps(manifest))
                directories.append(directory)
            (root / 'configs').mkdir()
            shutil.copyfile(REPO / 'configs/host-boot.cmd', root / 'configs/host-boot.cmd')
            output = root / 'out/nvme-image-fixture'
            with patch.object(IMAGE.os, 'geteuid', return_value=0), patch.object(IMAGE, 'REPO', root), patch.object(IMAGE.HOST, 'validate_builder_packages', return_value={}), patch.object(IMAGE.STATE, 'validate_workspace_storage', side_effect=ValueError('SD/RAM refused')) as guard:
                with self.assertRaisesRegex(ValueError, 'SD/RAM refused'):
                    IMAGE.build(*directories, root, output, 250_000_000_000)
                guard.assert_called_once_with(root)
            self.assertFalse(output.parent.exists())

    def test_write_all_rejects_no_progress(self):
        with patch.object(IMAGE.os, 'pwrite', return_value=0):
            with self.assertRaises(OSError):
                IMAGE.write_all(99, b'content', 0)

    @unittest.skipUnless(all(shutil.which(tool) for tool in ('mke2fs', 'e2fsck', 'debugfs')), 'filesystem tools required')
    def test_guests_filesystem_journal_identity_and_private_root(self):
        import struct
        import uuid
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            seed = root / 'seed'
            seed.mkdir(mode=0o700)
            image = root / 'guests.ext4'
            identity = str(uuid.uuid4())
            IMAGE.make_filesystem(image, 32 * 1024**2, 'A733GUESTS', identity, seed)
            with image.open('rb') as source:
                source.seek(1024)
                sb = source.read(1024)
            self.assertEqual(str(uuid.UUID(bytes=sb[104:120])), identity)
            self.assertTrue(struct.unpack_from('<I', sb, 92)[0] & 4)
            info = IMAGE.debugfs(image, 'stat /').decode()
            self.assertRegex(info, r'Mode:\s+0700')


if __name__ == '__main__':
    unittest.main()
