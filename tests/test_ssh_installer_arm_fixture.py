"""Small fixture safety checks; no ARM execution or private artifact access."""
import importlib.util
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ssh_arm_fixture', REPO / 'tests/verify_ssh_installer_arm.py')
V = importlib.util.module_from_spec(spec)
spec.loader.exec_module(V)


class FixtureSafety(unittest.TestCase):
    def test_receipt_pin_precedes_json_and_archive_access(self):
        with patch.object(V.D, 'read_input', return_value=b'not json') as read:
            with patch.object(V.json, 'loads', side_effect=AssertionError('JSON parsed before authentication')):
                with self.assertRaisesRegex(ValueError, 'manifest SHA256 mismatch'):
                    V.authenticated(Path('/archive'), Path('/manifest'), '0' * 64)
        self.assertEqual(read.call_count, 1)

    def test_parse_rejects_symlink_ancestor_before_extraction(self):
        entries = {'init': (stat.S_IFREG | 0o755, b'init'),
                   'alias': (stat.S_IFLNK | 0o777, b'real'),
                   'alias/file': (stat.S_IFREG | 0o600, b'secret')}
        with self.assertRaisesRegex(ValueError, 'non-directory CPIO ancestor'):
            V.D.parse(V.D.INIT.newc(entries))

    def test_extraction_keeps_regular_modes_and_aliases(self):
        entries = {'init': (stat.S_IFREG | 0o755, b'init'),
                   'etc': (stat.S_IFDIR | 0o755, b''),
                   'etc/private': (stat.S_IFREG | 0o600, b'fixture'),
                   'alias': (stat.S_IFLNK | 0o777, b'etc/private')}
        _, validated = V.D.parse(V.D.INIT.newc(entries))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'root'
            V.extract(validated, root)
            self.assertEqual((root / 'etc/private').stat().st_mode & 0o777, 0o600)
            self.assertTrue((root / 'alias').is_symlink())
            self.assertEqual((root / 'alias').read_bytes(), b'fixture')

    def test_policy_does_not_accept_disabled_pam(self):
        with self.assertRaisesRegex(ValueError, 'usepam'):
            V.policy_check(b'usepam no\n')


if __name__ == '__main__':
    unittest.main()
