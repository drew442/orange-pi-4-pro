"""Pure policy regressions; these establish no protected I/O or hardware proof."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location(
    'guest_storage_policy',
    Path(__file__).resolve().parents[1] / 'configs/guest_storage_policy.py')
POLICY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(POLICY)


def policy():
    return {'schema': 1, 'profile': POLICY.PROFILE, 'target': '/dev/nvme0n1',
            'expected_model': 'SPCC M.2 PCIe SSD', 'expected_bytes': 256060514304,
            'storage': {'state_uuid': 'c566cc4c-9bb5-4a61-91d0-b3aa738fcbcd',
                        'guest_uuid': 'c5472b0b-8306-4b38-802b-94b0fbe3ebbe',
                        'nvme_serial': '134807190FC700149277'}}


class StoragePolicyTests(unittest.TestCase):
    def check_invalid(self, value):
        with self.assertRaises(ValueError):
            POLICY.canonical(value)
        with self.assertRaises(ValueError):
            POLICY.decode((json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n')
                          .encode('utf-8'))

    def test_roundtrip_order_and_input_isolation(self):
        value = policy()
        before = copy.deepcopy(value)
        data = POLICY.canonical(value)
        self.assertEqual(data, (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n')
                         .encode('utf-8'))
        reordered = dict(reversed(list(value.items())))
        reordered['storage'] = dict(reversed(list(value['storage'].items())))
        self.assertEqual(POLICY.canonical(reordered), data)
        decoded = POLICY.decode(data)
        self.assertEqual(decoded, value)
        self.assertEqual(POLICY.canonical(decoded), data)
        decoded['storage']['nvme_serial'] = 'other'
        self.assertEqual(value, before)

    def test_missing_and_unknown_fields(self):
        for section in ('root', 'storage'):
            baseline = policy()
            target = baseline if section == 'root' else baseline['storage']
            for key in tuple(target):
                value = copy.deepcopy(baseline)
                del (value if section == 'root' else value['storage'])[key]
                with self.subTest(section=section, missing=key):
                    self.check_invalid(value)
            for key in ('unknown', 'enabled', 'autostart', 'guests', 'generation', 'path'):
                value = copy.deepcopy(baseline)
                (value if section == 'root' else value['storage'])[key] = None
                with self.subTest(section=section, unknown=key):
                    self.check_invalid(value)

    def test_exact_types_profile_target_model_and_size(self):
        for field, invalids in (
                ('schema', [True, 1.0, '1', 2, None]),
                ('profile', [1, None, 'a733-guest-autostart-v1']),
                ('target', ['/dev/nvme0n1p5', '/dev/nvme1n1', '/dev/mmcblk0',
                            '/dev/../dev/nvme0n1', None]),
                ('expected_model', ['', 'x' * 81, 'é', 'SSD\n', 'SSD\t', '\x7f', 1, None]),
                ('expected_bytes', [True, False, 0, -1, 1.0, '1', 1 << 63, None]),
                ('storage', [None, []])):
            for invalid in invalids:
                value = policy()
                value[field] = invalid
                with self.subTest(field=field, invalid=invalid):
                    self.check_invalid(value)
        for size in (1, POLICY.MAX_BYTES):
            value = policy()
            value.update(expected_bytes=size, expected_model='~' * 80)
            self.assertEqual(POLICY.decode(POLICY.canonical(value)), value)
        for invalid in (None, [], True, 'policy'):
            self.check_invalid(invalid)

    def test_storage_uuid_and_serial(self):
        for field in ('state_uuid', 'guest_uuid'):
            valid = policy()['storage'][field]
            for invalid in ('00000000-0000-0000-0000-000000000000', valid.upper(),
                            valid.replace('-', ''), '{' + valid + '}', 'invalid', 1, None):
                value = policy()
                value['storage'][field] = invalid
                with self.subTest(field=field, invalid=invalid):
                    self.check_invalid(value)
        value = policy()
        value['storage']['guest_uuid'] = value['storage']['state_uuid']
        self.check_invalid(value)
        for invalid in ('', ' spaced', 'a b', 'a/b', 'a\n', 'é', 'a' * 65, None, 12):
            value = policy()
            value['storage']['nvme_serial'] = invalid
            with self.subTest(serial=invalid):
                self.check_invalid(value)
        value = policy()
        value['storage']['nvme_serial'] = 'A' + '._-012abc' * 7
        self.assertEqual(POLICY.decode(POLICY.canonical(value)), value)

    def test_duplicate_keys_at_each_level(self):
        data = POLICY.canonical(policy())
        for old, new in ((b'"schema":1', b'"schema":1,"schema":1'),
                         (b'"nvme_serial":', b'"nvme_serial":"other","nvme_serial":')):
            with self.subTest(old=old), self.assertRaises(ValueError):
                POLICY.decode(data.replace(old, new))

    def test_only_canonical_bytes_decode(self):
        data = POLICY.canonical(policy())
        variants = (data[:-1], data + b'\n', b' ' + data,
                    json.dumps(policy(), indent=2).encode(),
                    data.replace(b'"schema":1', b'"schema":1.0'),
                    data.replace(b'SPCC', b'\\u0053PCC'),
                    json.dumps(policy(), separators=(',', ':')).encode() + b'\n')
        for invalid in variants:
            with self.subTest(invalid=invalid[:80]), self.assertRaises(ValueError):
                POLICY.decode(invalid)

    def test_encoding_size_nesting_and_constants(self):
        for invalid in (b'', b'\xff', b'{}' + b' ' * POLICY.LIMIT, b'\xef\xbb\xbf{}',
                        b'[' * 2000 + b'0' + b']' * 2000, None, 1,
                        POLICY.canonical(policy()).decode(), bytearray(b'{}')):
            with self.subTest(invalid=repr(invalid)[:80]), self.assertRaises(ValueError):
                POLICY.decode(invalid)
        data = POLICY.canonical(policy())
        for constant in (b'NaN', b'Infinity', b'-Infinity', b'1e999'):
            with self.subTest(constant=constant), self.assertRaises(ValueError):
                POLICY.decode(data.replace(b'"expected_bytes":256060514304',
                                           b'"expected_bytes":' + constant))

    def test_same_size_valid_replacement_is_data_not_authority(self):
        original = POLICY.canonical(policy())
        replaced = original.replace(b'134807190FC700149277', b'134807190FC700149278')
        self.assertEqual(len(replaced), len(original))
        self.assertNotEqual(replaced, original)
        self.assertEqual(POLICY.decode(replaced)['storage']['nvme_serial'],
                         '134807190FC700149278')
        self.assertEqual(POLICY.canonical(POLICY.decode(replaced)), replaced)


if __name__ == '__main__':
    unittest.main()
