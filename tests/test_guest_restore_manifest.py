"""Restore provenance and checkpoint chains remain distinct and authenticated."""
import copy
import hashlib
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('restore_manifest_test', ROOT / 'configs/guest_manifest.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def initial():
    config = {'schema': 1, 'name': 'one', 'uid': 60001, 'gid': 60001,
              'memory_mib': 64, 'memory_max_mib': 128, 'cpus': 1}
    files = {}
    for key, name in M.FILENAMES.items():
        config[key] = {'bytes': 512, 'sha256': 'a' * 64}
        files[name] = {**config[key], 'uid': 60001 if key == 'disk' else 0,
                       'gid': 60001 if key == 'disk' else 0,
                       'mode': '0600' if key == 'disk' else '0444'}
    return {'schema': 1, 'profile': M.PROFILE, 'config': config, 'files': files,
            'guest_uuid': '7811c43d-01e4-4635-83b4-25d276172ac0'}


class RestoreManifest(unittest.TestCase):
    def setUp(self):
        self.source = initial()
        self.target_uuid = 'c9f44972-20fb-4b84-b9ce-23c24ca66daf'

    def restore(self, source=None):
        return M.restored_manifest(source or self.source, 'b' * 64, 'c' * 64, self.target_uuid)

    def test_rebase_preserves_config_and_payloads_and_exact_external_pins(self):
        before = copy.deepcopy(self.source)
        result = self.restore()
        self.assertEqual(self.source, before)
        self.assertEqual(result['config'], before['config'])
        self.assertEqual(result['files'], before['files'])
        self.assertEqual(result['guest_uuid'], self.target_uuid)
        self.assertEqual(result['restoration'], {'archive_sha256': 'b' * 64,
            'manifest_sha256': 'c' * 64, 'guest_uuid': before['guest_uuid']})
        self.assertEqual(M.validate_manifest(M.canonical(result)), result)

    def test_restore_starts_new_chain_and_next_checkpoint_preserves_provenance(self):
        current = M.proposed_manifest(self.source, 'd' * 64, {'bytes': 512, 'sha256': 'e' * 64})
        restored = self.restore(current)
        self.assertNotIn('checkpoint', restored)
        pin = hashlib.sha256(M.canonical(restored)).hexdigest()
        checkpoint = M.proposed_manifest(restored, pin, {'bytes': 512, 'sha256': 'f' * 64})
        self.assertEqual(checkpoint['checkpoint']['number'], 1)
        self.assertEqual(checkpoint['checkpoint']['previous_manifest_sha256'], pin)
        self.assertEqual(checkpoint['restoration'], restored['restoration'])

    def test_repeated_restore_links_to_immediate_backup_source(self):
        once = self.restore()
        twice = M.restored_manifest(once, '1' * 64, '2' * 64, self.source['guest_uuid'])
        self.assertEqual(twice['restoration'], {'archive_sha256': '1' * 64,
            'manifest_sha256': '2' * 64, 'guest_uuid': self.target_uuid})

    def test_invalid_provenance_and_destination_refused(self):
        for key, value in [('archive_sha256', 'A' * 64), ('manifest_sha256', False),
                           ('guest_uuid', '00000000-0000-0000-0000-000000000000'),
                           ('guest_uuid', self.target_uuid.upper()), ('unexpected', True)]:
            with self.subTest(key=key, value=value):
                result = self.restore()
                result['restoration'][key] = value
                with self.assertRaises(ValueError):
                    M.canonical(result)
        with self.assertRaises(ValueError):
            M.restored_manifest(self.source, 'b' * 64, 'c' * 64, 'bad')
        with self.assertRaises(ValueError):
            M.restored_manifest(self.source, 'bad', 'c' * 64, self.target_uuid)
        result = self.restore()
        del result['restoration']['manifest_sha256']
        with self.assertRaises(ValueError):
            M.canonical(result)


if __name__ == '__main__':
    unittest.main()
