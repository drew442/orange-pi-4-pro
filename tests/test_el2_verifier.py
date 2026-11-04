"""Manifest input boundaries for the file-only EL2 artifact inspector."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('el2_verify', ROOT / 'scripts/verify-el2-diagnostic.py')
VERIFY = importlib.util.module_from_spec(spec)
spec.loader.exec_module(VERIFY)


class El2VerifierManifestTests(unittest.TestCase):
    def test_regular_manifest_parses(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'manifest.json'
            path.write_text(json.dumps({'schema': 1}))
            self.assertEqual(VERIFY.read_manifest(path), {'schema': 1})

    def test_symlink_manifest_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'target').write_text('{}')
            path = root / 'manifest.json'
            path.symlink_to(root / 'target')
            with self.assertRaisesRegex(ValueError, 'regular file'):
                VERIFY.read_manifest(path)

    def test_fifo_and_directory_are_refused_without_blocking(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'manifest.json'
            os.mkfifo(path)
            with self.assertRaisesRegex(ValueError, 'regular file'):
                VERIFY.read_manifest(path)
            path.unlink()
            path.mkdir()
            with self.assertRaisesRegex(ValueError, 'regular file'):
                VERIFY.read_manifest(path)


if __name__ == '__main__':
    unittest.main()
