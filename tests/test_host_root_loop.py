"""Non-root guards for the opt-in file-loop verifier; no mounts are performed."""
import errno
import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import os

SPEC = importlib.util.spec_from_file_location('loop_verifier', Path(__file__).with_name('verify_host_root_loop.py'))
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


@unittest.skipUnless(shutil.which('mke2fs') and shutil.which('tune2fs'), 'requires e2fsprogs')
class LoopVerifierGuards(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'root.img'
        with self.path.open('wb') as stream:
            stream.truncate(32 * 1024**2)
        subprocess.run(['mke2fs', '-q', '-t', 'ext4', '-F', '-O', '^has_journal', str(self.path)],
                       check=True, capture_output=True)

    def test_writable_filesystem_rejected_before_mount(self):
        with patch.object(verifier.os, 'geteuid', return_value=0), \
                patch.object(verifier, 'verify_open_image') as mount_test:
            with self.assertRaisesRegex(ValueError, 'READONLY'):
                verifier.verify(self.path)
            mount_test.assert_not_called()

    def test_validated_descriptor_survives_path_replacement(self):
        subprocess.run(['tune2fs', '-O', 'read-only', str(self.path)], check=True, capture_output=True)
        def inspect_descriptor(path, descriptor, info):
            before = verifier.sha256_fd(descriptor)
            path.rename(path.with_suffix('.saved'))
            path.symlink_to('/dev/null')
            self.assertEqual(verifier.sha256_fd(descriptor), before)
            self.assertEqual(os.fstat(descriptor).st_ino, info.st_ino)
            staged = path.with_suffix('.staged')
            verifier.copy_sparse_image(descriptor, staged, info.st_size)
            with staged.open('rb') as source:
                self.assertEqual(verifier.sha256_fd(source.fileno()), before)
        with patch.object(verifier.os, 'geteuid', return_value=0), \
                patch.object(verifier, 'verify_open_image', side_effect=inspect_descriptor):
            verifier.verify(self.path)

    def test_replacement_before_open_cannot_redirect_to_device(self):
        real_open = os.open
        def replace(path, flags):
            self.path.unlink()
            self.path.symlink_to('/dev/null')
            return real_open(path, flags)
        with patch.object(verifier.os, 'geteuid', return_value=0), \
                patch.object(verifier.os, 'open', side_effect=replace), \
                patch.object(verifier, 'verify_open_image') as mount_test:
            with self.assertRaises(OSError) as error:
                verifier.verify(self.path)
            self.assertEqual(error.exception.errno, errno.ELOOP)
            mount_test.assert_not_called()


if __name__ == '__main__':
    unittest.main()
