"""Registered NVMe-first firmware on compatible SD inputs; no hardware claims."""
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, REPO / path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


HOST = module('nvme_test_sd_host', 'build/assemble-host.py')
VERIFY = module('nvme_test_sd_verify', 'tests/verify_host_sd.py')
PROFILE = 'nvme-first-el2-test'
EVIDENCE = 'docs/evidence/uboot-nvme-candidate-20261107.json'
EL2 = 'docs/evidence/el2-diagnostic-20261104.json'
CONFIG = 'configs/uboot-nvme-candidate-v1.json'
BASELINE = 'docs/evidence/diagnostic-20261104.json'


class NvmeTestSDProfileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='a733-nvme-test-profile-')
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name)
        for path in (BASELINE, EL2, EVIDENCE, CONFIG, HOST.NVME_REGISTRY):
            destination = self.repo / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO / path, destination)
        self.addCleanup(patch.stopall)
        for host in (HOST, VERIFY.HOST):
            patch.object(host, 'REPO', self.repo).start()
            patch.object(host, 'DIAGNOSTIC_RECORD', self.repo / BASELINE).start()

    def test_registered_package_preserves_compatible_sd_stack_and_provenance(self):
        baseline, _, _ = HOST.load_boot_profile()
        el2, _, _ = HOST.load_boot_profile('experimental-el2')
        pinned, metadata, recipes = HOST.load_boot_profile(PROFILE)
        self.assertIn(PROFILE, HOST.BOOT_PROFILES)
        self.assertEqual(set(pinned), set(baseline))  # Keep original initramfs provenance too.
        for name in baseline:
            if name != 'boot_package.fex':
                self.assertEqual(pinned[name], baseline[name])
                self.assertEqual(pinned[name], el2[name])
        self.assertEqual(pinned['boot_package.fex'], {
            'bytes': 1392640,
            'sha256': 'bdfe070a2c358de9dd0061b39609f604c0c12e9c25140903505f88799cc343be'})
        self.assertNotEqual(pinned['boot_package.fex'], el2['boot_package.fex'])
        self.assertEqual(metadata['registered_target'], 'nvme-candidate-20261107-ram02')
        self.assertEqual(metadata['target_service'], '0x8000ff40')
        self.assertEqual(metadata['expected_spsr'], '0x3c9')
        self.assertTrue(metadata['physical_acceptance_pending'])
        self.assertEqual(metadata['environment'], 'CONFIG_ENV_IS_NOWHERE=y')
        self.assertEqual(metadata['bootcommand'], 'setenv boot_targets nvme; run distro_bootcmd')
        self.assertEqual(set(recipes), {BASELINE, EL2, EVIDENCE, CONFIG, HOST.NVME_REGISTRY})
        for key in ('registry', 'evidence'):
            provenance = metadata[key]
            self.assertEqual(provenance, {'path': provenance['path'],
                             **HOST.record(recipes[provenance['path']])})
        # No new state is selected by a firmware profile.
        self.assertNotIn('state_uuid', metadata)
        self.assertNotIn('configs/spi-nvme-host-boot.cmd', recipes)

    def test_registry_evidence_config_and_el2_tampering_is_rejected(self):
        for path in (HOST.NVME_REGISTRY, EVIDENCE, CONFIG, EL2):
            original = (self.repo / path).read_bytes()
            (self.repo / path).write_bytes(original + b' ')
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'hash/size mismatch'):
                HOST.load_boot_profile(PROFILE)
            (self.repo / path).write_bytes(original)

    def test_wrong_schema_target_and_package_cannot_be_registered_by_caller(self):
        path = self.repo / HOST.NVME_REGISTRY
        original = path.read_bytes()
        registry = json.loads(original)
        changes = []
        changed = copy.deepcopy(registry)
        changed['schema'] = 2
        changes.append(changed)
        changed = copy.deepcopy(registry)
        changed['targets']['other'] = changed['targets'].pop(HOST.NVME_TARGET)
        changes.append(changed)
        changed = copy.deepcopy(registry)
        changed['targets'][HOST.NVME_TARGET]['required_artifacts']['artifacts/boot_package.fex']['sha256'] = '0' * 64
        changes.append(changed)
        for changed in changes:
            path.write_text(json.dumps(changed))
            with self.subTest(change=changed), self.assertRaisesRegex(ValueError, 'hash/size mismatch'):
                HOST.load_boot_profile(PROFILE)
        path.write_bytes(original)

    def test_baseline_and_experimental_metadata_and_recipes_are_unchanged(self):
        baseline = json.loads((self.repo / BASELINE).read_bytes())['files']
        for name, path, service, spsr in (
                ('baseline-el1', BASELINE, '0x8000ff04', '0x3c5'),
                ('experimental-el2', EL2, '0x8000ff40', '0x3c9')):
            pinned, metadata, recipes = HOST.load_boot_profile(name)
            data = (self.repo / path).read_bytes()
            expected = dict(baseline)
            if name == 'experimental-el2':
                expected['boot_package.fex'] = json.loads(data)['files']['boot_package.fex']
            self.assertEqual(pinned, expected)
            self.assertEqual(metadata, {'name': name, 'target_service': service,
                             'expected_spsr': spsr, 'physical_acceptance_pending': True,
                             'evidence': {'path': path, **HOST.record(data)}})
            self.assertEqual(recipes, {BASELINE: (self.repo / BASELINE).read_bytes(), path: data})
        self.assertEqual(HOST.load_boot_profile(), HOST.load_boot_profile('baseline-el1'))

    def test_independent_verifier_requires_complete_metadata_and_all_recipes(self):
        pinned, metadata, recipes = HOST.load_boot_profile(PROFILE)
        manifest = {'boot_inputs': {name: pinned[name] for name in HOST.BLOBS},
                    'boot_profile': metadata,
                    'recipes': {name: HOST.record(data) for name, data in recipes.items()}}
        self.assertEqual(VERIFY.verify_boot_profile(manifest, PROFILE), pinned)
        for field in metadata:
            changed = copy.deepcopy(manifest)
            del changed['boot_profile'][field]
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'explicit trusted selection'):
                VERIFY.verify_boot_profile(changed, PROFILE)
        for path in recipes:
            changed = copy.deepcopy(manifest)
            del changed['recipes'][path]
            with self.subTest(recipe=path), self.assertRaisesRegex(ValueError, 'evidence recipe'):
                VERIFY.verify_boot_profile(changed, PROFILE)
        changed = copy.deepcopy(manifest)
        changed['boot_inputs']['boot_package.fex'] = HOST.load_boot_profile('experimental-el2')[0]['boot_package.fex']
        with self.assertRaisesRegex(ValueError, 'input provenance'):
            VERIFY.verify_boot_profile(changed, PROFILE)


if __name__ == '__main__':
    unittest.main()
