"""Explicit fixture evidence selection and refusal checks; no ARM/KVM execution."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, REPO / path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


E = load('fixture_profiles_enrollment', 'tests/verify_guest_enrollment_arm.py')
V = load('fixture_profiles_tools', 'tests/verify_guest_tools_arm.py')
LEGACY = 'docs/evidence/guest-fixture-20261104.json'
FIXED = 'docs/evidence/guest-seed-fixture-20261104.json'


class FixtureProfiles(unittest.TestCase):
    def test_known_pins_and_provenance(self):
        old, old_source = E.load_fixture_profile()
        new, new_source = E.load_fixture_profile('fixed-seed-http')
        self.assertEqual(old, E.load_fixture_profile('legacy')[0])
        self.assertEqual(set(old), set(E.NAMES.values()))
        self.assertEqual(old['Image'], new['Image'])
        self.assertEqual(new['Image'], {'bytes': 5251080, 'sha256':
            '1294959bcaa5a5ad3791913e33cffb8f0893708396aac8be9837c03a0e1f9377'})
        for records, size, initrd, disk in (
            (old, 973641, '62a07e1a5a7c38c5677a4c3750fc479c2636fe9e611aed67c89d3b9623a2c725',
             '0f78f07743e41fec6655b44792284980536f5cd62d08fdcaea0025bb222c6278'),
            (new, 974226, '2f3360a8f6365b84c352cae99949a5ae92a81001efe28dbae221f334805f854b',
             'ff595fac0b9a7d990f099edc0dd0f9d4859fb9942e185d2f2377088ce32bd577')):
            self.assertEqual(records['initramfs.cpio.gz'], {'bytes': size, 'sha256': initrd})
            self.assertEqual(records['guest-disk.raw'], {'bytes': 134217728, 'sha256': disk})
        for source, path, profile in ((old_source, LEGACY, 'legacy'), (new_source, FIXED, 'fixed-seed-http')):
            self.assertEqual(source['profile'], profile)
            self.assertEqual(source['evidence_path'], path)
            self.assertEqual(source['evidence_sha256'], hashlib.sha256((REPO / path).read_bytes()).hexdigest())
        self.assertEqual(new_source['seed_builder']['profile'], 'a733-disposable-guest-seed-v1')

    def test_unknown_profiles_refused_before_any_read(self):
        for profile in ('auto', FIXED, '', None, [], True):
            with self.subTest(profile=profile), patch.object(Path, 'read_bytes') as read:
                with self.assertRaisesRegex(ValueError, 'unsupported guest fixture profile'):
                    E.load_fixture_profile(profile)
                read.assert_not_called()

    def test_malformed_evidence_refused(self):
        documents = {LEGACY: json.loads((REPO / LEGACY).read_bytes()),
                     FIXED: json.loads((REPO / FIXED).read_bytes())}
        mutations = [
            (LEGACY, ('schema',), True),
            (LEGACY, ('files', 'Image'), None),
            (FIXED, ('schema',), 2),
            (FIXED, ('artifact_manifest',), []),
            (FIXED, ('artifact_manifest', 'schema'), True),
            (FIXED, ('artifact_manifest', 'files'), []),
        ]
        for record in (None, [], {}, {'bytes': True, 'sha256': 'a' * 64},
                       {'bytes': 0, 'sha256': 'a' * 64},
                       {'bytes': 64 * 1024**2 + 1, 'sha256': 'a' * 64},
                       {'bytes': 10, 'sha256': 'A' * 64},
                       {'bytes': 10, 'sha256': 'a' * 63},
                       {'bytes': 10, 'sha256': 10},
                       {'bytes': 10, 'sha256': 'a' * 64, 'extra': 1}):
            mutations.append((FIXED, ('artifact_manifest', 'files', 'initramfs.cpio.gz'), record))
        mutations.extend([
            (LEGACY, ('files', 'Image', 'bytes'), 8 * 1024**2 + 1),
            (LEGACY, ('files', 'initramfs.cpio.gz', 'bytes'), 64 * 1024**2 + 1),
            (LEGACY, ('files', 'guest-disk.raw', 'bytes'), 128 * 1024**2 - 512),
            (LEGACY, ('files', 'guest-disk.raw', 'bytes'), 128 * 1024**2 + 512),
            (LEGACY, ('files', 'guest-disk.raw', 'bytes'), 128 * 1024**2 - 1),
            (FIXED, ('artifact_manifest', 'files', 'Image', 'sha256'), '0' * 64),
            (FIXED, ('artifact_manifest', 'files', 'Image', 'bytes'), 8 * 1024**2 + 1),
            (FIXED, ('artifact_manifest', 'files', 'guest-disk.raw', 'bytes'), 513),
            (FIXED, ('artifact_manifest', 'files', 'guest-disk.raw', 'bytes'), 250000000512),
            (FIXED, ('artifact_manifest', 'seed_builder', 'schema'), True),
            (FIXED, ('artifact_manifest', 'seed_builder', 'bytes'), True),
            (FIXED, ('artifact_manifest', 'seed_builder', 'bytes'), 134218240),
            (FIXED, ('artifact_manifest', 'seed_builder', 'sha256'), '0' * 64),
            (FIXED, ('artifact_manifest', 'seed_builder', 'profile'), 'other'),
            (FIXED, ('artifact_manifest', 'seed_builder', 'label'), 'other'),
            (FIXED, ('artifact_manifest', 'seed_builder', 'uuid'), 'other'),
            (FIXED, ('artifact_manifest', 'disk_fixture_id'), 'other'),
            (FIXED, ('artifact_manifest', 'initial_counter'), False),
        ])
        for filename, keys, value in mutations:
            with self.subTest(file=filename, keys=keys, value=value):
                changed = copy.deepcopy(documents)
                parent = changed[filename]
                for key in keys[:-1]:
                    parent = parent[key]
                parent[keys[-1]] = value
                def read(path):
                    return json.dumps(changed[str(path.relative_to(REPO))]).encode()
                with patch.object(Path, 'read_bytes', read), self.assertRaises(ValueError):
                    E.load_fixture_profile('fixed-seed-http')

        missing_label = copy.deepcopy(documents)
        del missing_label[FIXED]['artifact_manifest']['seed_builder']['label']
        def read_missing_label(path):
            return json.dumps(missing_label[str(path.relative_to(REPO))]).encode()
        with patch.object(Path, 'read_bytes', read_missing_label), self.assertRaisesRegex(ValueError, 'identity/profile'):
            E.load_fixture_profile('fixed-seed-http')

    def test_capture_refuses_wrong_profile_hash_and_size(self):
        # Tiny pinned bytes exercise the real descriptor capture without large artifacts.
        data = b'fixture\0bytes'
        good = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            source.write_bytes(data)
            E.capture_seed(source, root / 'good', good)
            self.assertEqual((root / 'good').read_bytes(), data)
            for index, bad in enumerate(({**good, 'sha256': '0' * 64}, {**good, 'bytes': len(data) + 1})):
                with self.assertRaises(ValueError):
                    E.capture_seed(source, root / str(index), bad)
            (root / 'link').symlink_to(source)
            with self.assertRaises(OSError):
                E.capture_seed(root / 'link', root / 'linked-output', good)
            (root / 'hardlink').hardlink_to(source)
            with self.assertRaisesRegex(ValueError, 'metadata'):
                E.capture_seed(source, root / 'hardlink-output', good)

    def test_cli_choices_refuse_unknown_profile(self):
        for filename, args in (
            ('tests/verify_guest_enrollment_arm.py', ['rootfs', 'guest']),
            ('tests/verify_guest_tools_arm.py', ['package', '--package-manifest-sha256', '0' * 64, 'rootfs', 'guest'])):
            result = subprocess.run([sys.executable, str(REPO / filename), *args,
                                     '--fixture-profile', 'auto'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn('invalid choice', result.stderr)

    def test_packaged_verifier_forwards_explicit_and_default_profile(self):
        # Reuse real authenticated package preparation; mock only ARM execution.
        package_tests = load('fixture_profiles_package_tests', 'tests/test_guest_tools_verifier.py')
        case = package_tests.PackageAuthentication()
        case.setUp()
        self.addCleanup(case.doCleanups)
        original = V.module
        enrollment = load('fixture_profiles_mock_stage', 'tests/verify_guest_enrollment_arm.py')
        def module(name, path):
            return enrollment if name == 'installed_arm_enrollment' else original(name, path)
        with patch.object(V, 'module', side_effect=module):
            for options, profile in (({}, 'legacy'), ({'fixture_profile': 'fixed-seed-http'}, 'fixed-seed-http')):
                with self.subTest(profile=profile), patch.object(enrollment, 'verify', return_value={}) as stage:
                    V.verify(case.root, case.pin(), '/unused-rootfs', '/unused-guest',
                             loopback_network=True, **options)
                    self.assertEqual(stage.call_args.kwargs['fixture_profile'], profile)
                    self.assertIn("config['network']", stage.call_args.kwargs['probe'])


if __name__ == '__main__':
    unittest.main()
