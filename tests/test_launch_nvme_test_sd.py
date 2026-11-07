"""Registered firmware transport adapter contracts; no hardware access."""
import copy
import hashlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ADAPTER = load('test_nvme_sd_adapter', 'build/launch-nvme-test-sd.py')


class AdapterTests(unittest.TestCase):
    def test_historical_controller_pin_is_exact(self):
        self.assertEqual(hashlib.sha256((ROOT / ADAPTER.PARENT).read_bytes()).hexdigest(),
                         ADAPTER.PARENT_SHA)

    def test_real_payload_changes_only_two_explicit_profile_selections(self):
        parent = load('test_nvme_sd_parent', ADAPTER.PARENT)
        before = parent.payload_program('firmware-test', parent.STATE_UUID)
        ADAPTER.adapt(parent)
        after = parent.payload_program('firmware-test', parent.STATE_UUID)
        self.assertEqual(before.count(b"boot_profile='experimental-el2'"), 2)
        self.assertEqual(after, before.replace(b"boot_profile='experimental-el2'",
                                              b"boot_profile='nvme-first-el2-test'"))
        for tag, identity in [('../unsafe', parent.STATE_UUID), ('test', 'wrong-uuid')]:
            with self.assertRaises(ValueError):
                parent.payload_program(tag, identity)

    def test_changed_payload_anchor_refuses(self):
        parent = SimpleNamespace(contract=lambda *a: {}, payload_program=lambda *a: b'unknown',
                                 plan=lambda *a: {})
        ADAPTER.adapt(parent)
        with self.assertRaisesRegex(ValueError, 'anchors'):
            parent.payload_program('test', 'uuid')

    def fixture(self):
        metadata = dict(name=ADAPTER.PROFILE, target_service='0x8000ff40',
                        expected_spsr='0x3c9', physical_acceptance_pending=True)
        pinned = {'kernel': {'sha256': 'kernel'}, 'boot_package.fex': {'sha256': 'new'}}
        contents = {'build/assemble-host.py': b'assembly', 'recipe': b'original'}
        assembly = SimpleNamespace(RECIPES=('recipe',),
                                  load_boot_profile=lambda name: (pinned, metadata, {'new-proof': b'proof'}))
        roots = SimpleNamespace(module=lambda *a: assembly, pin=lambda data: {'sha256': hashlib.sha256(data).hexdigest()})
        baseline = {'diagnostic': {'kernel': {'sha256': 'kernel'},
                                   'boot_package.fex': {'sha256': 'old'}},
                    'boot_profile': {'name': 'experimental-el2'}, 'sd_recipes': {'old-proof': {}}}
        parent = SimpleNamespace(BOOT_FILES=('kernel', 'boot_package.fex'),
                                 contract=lambda *a: copy.deepcopy(baseline),
                                 payload_program=lambda *a: b'',
                                 plan=lambda *a: {'boot_profile': 'experimental-el2', 'unchanged': True})
        ADAPTER.adapt(parent)
        return parent, roots, contents, metadata, pinned, baseline

    def test_contract_replaces_only_package_and_exact_profile_recipes(self):
        parent, roots, contents, metadata, pinned, baseline = self.fixture()
        result = parent.contract(roots, Path('/project'), contents)
        self.assertEqual(result['diagnostic'], pinned)
        self.assertEqual(result['boot_profile'], metadata)
        self.assertEqual(set(result['sd_recipes']), {'recipe', 'new-proof'})
        self.assertEqual(baseline['diagnostic']['boot_package.fex'], {'sha256': 'old'})

    def test_contract_refuses_changed_kernel_or_el2_handoff(self):
        for field, value in [('target_service', '0x8000ff04'), ('expected_spsr', '0x3c5'),
                             ('physical_acceptance_pending', False), ('name', 'unregistered')]:
            parent, roots, contents, metadata, _, _ = self.fixture()
            metadata[field] = value
            with self.assertRaisesRegex(ValueError, 'handoff'):
                parent.contract(roots, Path('/project'), contents)
        parent, roots, contents, _, pinned, _ = self.fixture()
        pinned['kernel'] = {'sha256': 'changed'}
        with self.assertRaisesRegex(ValueError, 'immutable'):
            parent.contract(roots, Path('/project'), contents)

    def test_plan_reports_test_profile_and_retains_parent_fields(self):
        parent, _, _, _, _, _ = self.fixture()
        result = parent.plan(None, None, None)
        self.assertEqual(result['boot_profile'], ADAPTER.PROFILE)
        self.assertTrue(result['unchanged'])
        self.assertIn('pending', result['firmware_test_scope'])


if __name__ == '__main__':
    unittest.main()
