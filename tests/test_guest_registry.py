"""Pure registry policy tests; these provide no physical acceptance evidence."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location(
    'guest_registry', Path(__file__).resolve().parents[1] / 'configs/guest_registry.py')
REGISTRY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REGISTRY)


def guest(name='autotest', number=60002):
    return {'name': name, 'uid': number, 'gid': number, 'manifest_sha256': 'a' * 64,
            'enabled': False, 'shutdown_profile': 'qmp-guest-shutdown-v1',
            'fixture_profile': 'none'}


def registry():
    return {'schema': 1, 'profile': 'a733-guest-autostart-v1', 'generation': 0,
            'storage': {'state_uuid': 'c566cc4c-9bb5-4a61-91d0-b3aa738fcbcd',
                        'guest_uuid': 'c5472b0b-8306-4b38-802b-94b0fbe3ebbe',
                        'nvme_serial': '134807190FC700149277'}, 'guests': [guest()]}


class RegistryTests(unittest.TestCase):
    def test_managed_shutdown_fixture_has_explicit_profile(self):
        value = registry()
        value['guests'][0]['fixture_profile'] = 'managed-power-key-v1'
        self.assertEqual(REGISTRY.validate_registry(REGISTRY.canonical(value)), value)
        value['guests'][0]['fixture_profile'] = 'managed-power-key-v2'
        self.check_invalid(value)

    def check_invalid(self, value):
        with self.assertRaises(ValueError):
            REGISTRY.validate_registry(json.dumps(value))

    def test_roundtrip_normalizes_order_and_preserves_input(self):
        value = registry()
        value['guests'].append(guest('another', 60003))
        before = copy.deepcopy(value)
        data = REGISTRY.canonical(value)
        self.assertTrue(data.endswith(b'\n'))
        self.assertEqual(data.count(b'\n'), 1)
        self.assertEqual(REGISTRY.canonical(REGISTRY.validate_registry(data)), data)
        self.assertEqual(REGISTRY.validate_registry(data)['guests'][0]['name'], 'another')
        self.assertEqual(value, before)
        self.assertEqual(REGISTRY.validate_registry(data.decode()), REGISTRY.validate_registry(data))

    def test_empty_and_guest_count_boundaries(self):
        value = registry()
        value['guests'] = []
        self.assertEqual(REGISTRY.validate_registry(REGISTRY.canonical(value)), value)
        value['guests'] = [guest('g' + str(i), 60000 + i) for i in range(32)]
        self.assertEqual(len(REGISTRY.validate_registry(REGISTRY.canonical(value))['guests']), 32)
        value['guests'].append(guest('overflow', 60032))
        self.check_invalid(value)

    def test_each_unknown_and_missing_field(self):
        for section in ('root', 'storage', 'guest'):
            baseline = registry()
            target = (baseline if section == 'root' else baseline['storage'] if section == 'storage'
                      else baseline['guests'][0])
            for key in tuple(target):
                value = copy.deepcopy(baseline)
                changed = (value if section == 'root' else value['storage'] if section == 'storage'
                           else value['guests'][0])
                del changed[key]
                with self.subTest(section=section, missing=key):
                    self.check_invalid(value)
            target['unknown'] = None
            with self.subTest(section=section, unknown=True):
                self.check_invalid(baseline)

    def test_duplicate_json_keys_at_each_level(self):
        data = json.dumps(registry())
        for old, new in (('"schema": 1', '"schema": 1, "schema": 1'),
                         ('"nvme_serial":', '"nvme_serial": "other", "nvme_serial":'),
                         ('"enabled": false', '"enabled": false, "enabled": true')):
            with self.subTest(old=old), self.assertRaises(ValueError):
                REGISTRY.validate_registry(data.replace(old, new))

    def test_bytes_encoding_bounds_and_nesting(self):
        for data in (b'', b'\xff', b'{}' + b' ' * REGISTRY.LIMIT, '\ud800',
                     '[' * 2000 + '0' + ']' * 2000, b'\xef\xbb\xbf{}', None, 1):
            with self.subTest(data=repr(data)[:80]), self.assertRaises(ValueError):
                REGISTRY.validate_registry(data)
        data = REGISTRY.canonical(registry())
        padded = data + b' ' * (REGISTRY.LIMIT - len(data))
        self.assertEqual(REGISTRY.validate_registry(padded)['generation'], 0)
        for constant in ('NaN', 'Infinity', '-Infinity', '1e999'):
            with self.subTest(constant=constant), self.assertRaises(ValueError):
                REGISTRY.validate_registry(data.decode().replace('"generation":0',
                                                                 '"generation":' + constant))

    def test_schema_generation_and_profile_types(self):
        for field, values in (('schema', [True, '1', 0, 2, None]),
                              ('profile', ['other', 1, None]),
                              ('generation', [True, -1, 1.0, '1', None, 1 << 63]),
                              ('storage', [[], None]), ('guests', [{}, None])):
            for invalid in values:
                value = registry()
                value[field] = invalid
                with self.subTest(field=field, invalid=invalid):
                    self.check_invalid(value)
        value = registry()
        value['generation'] = REGISTRY.MAX_GENERATION
        self.assertEqual(REGISTRY.validate_registry(REGISTRY.canonical(value)), value)

    def test_storage_uuid_and_serial(self):
        for key in ('state_uuid', 'guest_uuid'):
            for invalid in ('00000000-0000-0000-0000-000000000000',
                            registry()['storage'][key].upper(), 'not-uuid', 1, None,
                            '{' + registry()['storage'][key] + '}',
                            registry()['storage'][key].replace('-', '')):
                value = registry()
                value['storage'][key] = invalid
                with self.subTest(key=key, invalid=invalid):
                    self.check_invalid(value)
        for invalid in ('', ' spaced', 'a b', 'a/b', 'a\n', 'é', 'a' * 65, None, 12):
            value = registry()
            value['storage']['nvme_serial'] = invalid
            with self.subTest(serial=invalid):
                self.check_invalid(value)
        value = registry()
        value['storage']['nvme_serial'] = 'A' + '._-012abc' * 7
        REGISTRY.canonical(value)

    def test_identity_and_duplicates(self):
        for field, invalids in (('name', ['Bad', '-bad', 'a' * 17, '', None]),
                                ('uid', [True, 59999, 61000, 60003, '60002']),
                                ('gid', [False, 59999, 61000, 60003, '60002'])):
            for invalid in invalids:
                value = registry()
                value['guests'][0][field] = invalid
                with self.subTest(field=field, invalid=invalid):
                    self.check_invalid(value)
        for duplicate in (guest('autotest', 60003), guest('other', 60002)):
            value = registry()
            value['guests'].append(duplicate)
            self.check_invalid(value)

    def test_digest_enable_and_lifecycle_profiles(self):
        for field, invalids in (('manifest_sha256', ['a' * 63, 'A' * 64, 'g' * 64, 1, None]),
                                ('enabled', [0, 1, 'true', None]),
                                ('shutdown_profile', ['sigterm', None, True]),
                                ('fixture_profile', ['firstkvm', None, False])):
            for invalid in invalids:
                value = registry()
                value['guests'][0][field] = invalid
                with self.subTest(field=field, invalid=invalid):
                    self.check_invalid(value)
        value = registry()
        value['guests'][0].update(enabled=True, fixture_profile='first-kvm-http-v1')
        REGISTRY.canonical(value)
        value['guests'][0]['name'] = 'firstkvm'
        self.check_invalid(value)
        value['guests'][0]['enabled'] = False
        REGISTRY.canonical(value)

    def test_update_cas_pin_disable_and_input_isolation(self):
        value = registry()
        before = copy.deepcopy(value)
        updated = REGISTRY.update(value, 'autotest', enabled=True, manifest_sha256='b' * 64,
                                  expected_generation=0)
        self.assertEqual(value, before)
        self.assertEqual(updated['generation'], 1)
        self.assertEqual(updated['guests'][0]['manifest_sha256'], 'b' * 64)
        self.assertIs(updated['guests'][0]['enabled'], True)
        disabled = REGISTRY.update(updated, 'autotest', enabled=False, expected_generation=1)
        self.assertIs(disabled['guests'][0]['enabled'], False)
        self.assertEqual(disabled['generation'], 2)
        self.assertIs(updated['guests'][0]['enabled'], True)
        for expected in (True, '0', -1, 1, None):
            with self.subTest(expected=expected), self.assertRaises(ValueError):
                REGISTRY.update(value, 'autotest', enabled=True, expected_generation=expected)
        with self.assertRaises(ValueError):
            REGISTRY.update(value, 'missing', enabled=True, expected_generation=0)

    def test_update_overflow_and_policy_and_explicit_types(self):
        value = registry()
        value['generation'] = REGISTRY.MAX_GENERATION
        with self.assertRaises(ValueError):
            REGISTRY.update(value, 'autotest', enabled=True,
                            expected_generation=REGISTRY.MAX_GENERATION)
        value = registry()
        value['guests'][0]['name'] = 'firstkvm'
        with self.assertRaises(ValueError):
            REGISTRY.update(value, 'firstkvm', enabled=True, expected_generation=0)
        for kwargs in ({'enabled': 1}, {'manifest_sha256': 'A' * 64}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                REGISTRY.update(registry(), 'autotest', expected_generation=0, **kwargs)
        self.assertEqual(REGISTRY.update(registry(), 'autotest', expected_generation=0)['generation'], 1)

    def test_canonical_rejects_python_values_not_representable_in_json(self):
        for invalid in (float('nan'), float('inf'), object()):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                REGISTRY.canonical(invalid)
        recursive = []
        recursive.append(recursive)
        with self.assertRaises(ValueError):
            REGISTRY.canonical(recursive)


if __name__ == '__main__':
    unittest.main()
