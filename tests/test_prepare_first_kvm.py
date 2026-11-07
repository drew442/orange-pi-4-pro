"""Closed-profile metadata preparation; no devices, guest or image decoding."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('prepare_first_kvm', Path(__file__).resolve().parents[1] / 'scripts/prepare-first-kvm.py')
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


class ProfileTests(unittest.TestCase):
    def records(self, profile):
        p = m.PROFILES[profile]
        root = {'package_state': {'manifest': {'sha256': p['package']}},
                'maintenance_runtime': {'files': {f'usr/lib/a733-tools/configs/tool-{n}.py': {'bytes': 10, 'sha256': 'a' * 64} for n in range(p['count'])}}}
        if profile == 'installed-20261106':
            root = {'host-payload-deployment-20261106-02': {'manifest_sha256': p['root_sha'], 'manifest': root}}
        manifest = {'state_uuid': p['state'], 'filesystem_uuids': {'state': p['state'], 'guests': p['guest']},
                    'inputs': {'roots_manifest': {'sha256': p['root_sha']}},
                    'files': {'nvme.img': m.record(p['raw']), 'nvme.img.gz': m.record(p['wire'])}}
        records = {p['root'][0]: root, p['bundle'] + '/manifest.json': manifest}
        if 'proof' in p:
            manifest['distribution'] = {'verification': {'stream_report': m.record(p['proof'][1:])}}
            records[p['proof'][0]] = {'success': True, 'state': 'complete', 'gzip_expansion_independent_verification': True,
                                     'compressed_wire': m.record(p['wire']), 'decoded_image': m.record(p['raw'])}
        else:
            manifest['distribution'] = {'verification': 'all logical source bytes hashed; fsynced encoded bytes hashed on readback; full bounded gzip expansion hashed', 'source_image': {'record': m.record(p['raw'])}}
        return records

    def generate(self, profile, records):
        with mock.patch.object(m, 'read_record', side_effect=lambda path, size, sha: records[path]):
            return m.prepare_data(profile)

    def test_both_closed_profiles(self):
        self.assertEqual(set(m.PROFILES), {'installed-20261106', 'candidate-repeat-20261107'})
        for profile, count, state, guest in [
            ('installed-20261106', 39, '00fd936c-49fb-4fea-87c8-928b9c1599e0', '3dd7373a-c4b2-42e2-867a-bc5f39039285'),
            ('candidate-repeat-20261107', 40, 'feaaee34-d782-4c18-bd04-1029540ab26a', '5f980211-35f5-4436-98c6-6a750eb1fc3c'),
        ]:
            data = self.generate(profile, self.records(profile))
            values = json.loads(data['profile.json'])['values']
            self.assertEqual(values['STATE_UUID'], state)
            self.assertEqual(values['GUEST_UUID'], guest)
            self.assertEqual(int(values['TOOL_COUNT']), count)
            self.assertEqual(len(data['installed-tools.sha256'].splitlines()), count)
            self.assertIn('readonly PROFILE=' + profile, data['profile.env'])
            self.assertFalse(json.loads(data['profile.json'])['recovery_bytes_verified_now'])

    def test_unknown_profile(self):
        with self.assertRaises(ValueError):
            m.prepare_data('arbitrary')

    def test_mixed_profile_metadata(self):
        profile = 'candidate-repeat-20261107'
        p = m.PROFILES[profile]
        for mutate in [
            lambda r: r[p['bundle'] + '/manifest.json'].__setitem__('state_uuid', m.PROFILES['installed-20261106']['state']),
            lambda r: r[p['root'][0]]['package_state']['manifest'].__setitem__('sha256', 'a' * 64),
            lambda r: r[p['bundle'] + '/manifest.json']['filesystem_uuids'].__setitem__('guests', m.PROFILES['installed-20261106']['guest']),
            lambda r: r[p['proof'][0]].__setitem__('success', False),
            lambda r: r[p['proof'][0]]['decoded_image'].__setitem__('sha256', 'b' * 64),
        ]:
            records = self.records(profile)
            mutate(records)
            with self.assertRaises(ValueError):
                self.generate(profile, records)

    def test_tool_paths_types_counts(self):
        for name in ['usr/lib/a733-tools/../bad', 'usr/lib/a733-tools//bad', 'usr/lib/a733-tools/a\nb', '/usr/lib/a733-tools/a']:
            with self.assertRaises(ValueError):
                m.validate_tools({name: {'bytes': 1, 'sha256': 'a' * 64}}, 1)
        for record in [{'bytes': True, 'sha256': 'a' * 64}, {'bytes': 1, 'sha256': 'bad'}]:
            with self.assertRaises(ValueError):
                m.validate_tools({'usr/lib/a733-tools/a': record}, 1)
        with self.assertRaises(ValueError):
            m.validate_tools({}, 39)

    def test_record_authentication_and_special_files(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(m, 'REPO', Path(directory)):
            path = Path(directory) / 'record'
            raw = b'{"a":1}'
            path.write_bytes(raw)
            digest = hashlib.sha256(raw).hexdigest()
            self.assertEqual(m.read_record('record', len(raw), digest), {'a': 1})
            with self.assertRaises(ValueError):
                m.read_record('record', len(raw), 'a' * 64)
            duplicate = b'{"a":1,"a":2}'
            path.write_bytes(duplicate)
            with self.assertRaises(ValueError):
                m.read_record('record', len(duplicate), hashlib.sha256(duplicate).hexdigest())
            path.unlink()
            path.symlink_to('/dev/null')
            with self.assertRaises(ValueError):
                m.read_record('record', 0, hashlib.sha256(b'').hexdigest())
            path.unlink()
            m.os.mkfifo(path)
            with self.assertRaises(ValueError):
                m.read_record('record', 0, hashlib.sha256(b'').hexdigest())

    def test_output_new_private_checksums_and_guards(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(m, 'REPO', Path(directory)), mock.patch.object(m.subprocess, 'check_output', return_value='ext4 /dev/mapper/ubuntu--vg-ubuntu--lv\n'):
            base = Path(directory)
            (base / 'out').mkdir()
            target = base / 'out' / 'new'
            m.write_output(target, {'profile.env': 'readonly PROFILE=test\n'})
            self.assertEqual(target.stat().st_mode & 0o777, 0o700)
            self.assertEqual((target / 'profile.env').stat().st_mode & 0o777, 0o400)
            self.assertEqual((target / 'SHA256SUMS').read_text(), hashlib.sha256(b'readonly PROFILE=test\n').hexdigest() + '  profile.env\n')
            with self.assertRaises(FileExistsError):
                m.write_output(target, {})
            for unsafe in [Path('relative'), base / 'elsewhere', base / 'out' / '..' / 'escape']:
                with self.assertRaises(ValueError):
                    m.write_output(unsafe, {})
            (base / 'out' / 'link').symlink_to(target)
            with self.assertRaises(FileExistsError):
                m.write_output(base / 'out' / 'link', {})
            with mock.patch.object(m.subprocess, 'check_output', return_value='tmpfs tmpfs\n'):
                with self.assertRaises(ValueError):
                    m.write_output(base / 'out' / 'ram', {})
            with mock.patch.object(m.os, 'statvfs', return_value=mock.Mock(f_bavail=1, f_frsize=4096)):
                with self.assertRaises(ValueError):
                    m.write_output(base / 'out' / 'full', {})


if __name__ == '__main__':
    unittest.main()
