"""Pure guest enrollment policy; these checks do not execute KVM or systemd."""
import copy
import importlib.util
import json
from pathlib import Path
import shlex
import unittest

BASE = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('guest_config', BASE / 'configs/guest_config.py')
CONFIG = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONFIG)
VALID = {'schema': 1, 'name': 'one', 'uid': 60001, 'gid': 60001,
         'memory_mib': 128, 'memory_max_mib': 256, 'cpus': 2,
         'kernel': {'bytes': 100, 'sha256': 'a' * 64},
         'initrd': {'bytes': 200, 'sha256': 'b' * 64},
         'disk': {'bytes': 1024, 'sha256': 'c' * 64}}


class SchemaTests(unittest.TestCase):
    def validate(self, value):
        return CONFIG.validate_config(json.dumps(value))

    def test_roundtrip_and_stable_canonical(self):
        self.assertEqual(self.validate(VALID), VALID)
        data = CONFIG.canonical(dict(reversed(list(VALID.items()))))
        self.assertEqual(data, CONFIG.canonical(VALID))
        self.assertTrue(data.endswith(b'\n'))
        self.assertEqual(CONFIG.validate_config(data), VALID)
        result = self.validate(VALID)
        result['disk']['bytes'] = 512
        self.assertEqual(VALID['disk']['bytes'], 1024)

    def test_exact_fields_and_identity(self):
        bad = [{**VALID, 'unexpected': 1}, {**VALID, 'schema': True}]
        for key in VALID:
            value = copy.deepcopy(VALID)
            del value[key]
            bad.append(value)
        for key, values in {'name': ('', 'A', '../one', 'a-', 'a' * 17, 'a\nb'),
                            'uid': (True, 59999, 61000, 60001.0),
                            'gid': (0, 60002)}.items():
            bad.extend({**VALID, key: value} for value in values)
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.validate(value)
        for name in ('a', 'a' * 16, 'a-1'):
            self.validate({**VALID, 'name': name})

    def test_malformed_bounded_and_duplicate_json(self):
        text = json.dumps(VALID)
        for data in (b'', {}, b'\xff', '{', '[]', text + 'x',
                     text.replace('"schema": 1', '"schema": 1, "schema": 1'),
                     text.replace('"bytes": 100', '"bytes": 100, "bytes": 100'),
                     text.replace('100', 'NaN'), '\ud800', '[' * 2000):
            with self.subTest(data=repr(data)[:100]), self.assertRaises(ValueError):
                CONFIG.validate_config(data)
        CONFIG.validate_config(text + ' ' * (65536 - len(text)))
        with self.assertRaises(ValueError):
            CONFIG.validate_config(text + ' ' * (65537 - len(text)))
        # Text limits count bytes, including multibyte JSON whitespace substitutes.
        with self.assertRaises(ValueError):
            CONFIG.validate_config('é' * 32769)

    def test_resource_limits_and_types(self):
        for key, values in {'memory_mib': (True, 63, 1048577, 128.0),
                            'memory_max_mib': (False, 127, 191, 1048577, '256'),
                            'cpus': (True, 0, 257, 1.0)}.items():
            for number in values:
                with self.subTest(key=key, number=number), self.assertRaises(ValueError):
                    self.validate({**VALID, key: number})
        for memory, limit, cpus in ((64, 128, 1), (1048512, 1048576, 256)):
            self.validate({**VALID, 'memory_mib': memory, 'memory_max_mib': limit, 'cpus': cpus})
        with self.assertRaises(ValueError):
            self.validate({**VALID, 'memory_mib': 1048513, 'memory_max_mib': 1048576})

    def test_artifact_policy(self):
        for key in ('kernel', 'initrd', 'disk'):
            maximum = CONFIG.DISK_LIMIT if key == 'disk' else CONFIG.ARTIFACT_LIMIT
            for artifact in ({'bytes': True, 'sha256': 'a' * 64},
                             {'bytes': 0, 'sha256': 'a' * 64},
                             {'bytes': maximum + 1, 'sha256': 'a' * 64},
                             {'bytes': 512.0, 'sha256': 'a' * 64},
                             {'bytes': 512, 'sha256': 'A' * 64},
                             {'bytes': 512, 'sha256': 'a' * 63},
                             {'bytes': 512, 'sha256': 'a' * 63 + '\n'},
                             {'bytes': 512, 'sha256': 1},
                             {'bytes': 512}, {**VALID[key], 'path': '/tmp/other'}):
                with self.subTest(key=key, artifact=artifact), self.assertRaises(ValueError):
                    self.validate({**VALID, key: artifact})
            self.validate({**VALID, key: {'bytes': maximum, 'sha256': '0' * 64}})
        with self.assertRaises(ValueError):
            self.validate({**VALID, 'disk': {'bytes': 513, 'sha256': 'a' * 64}})


class UnitTests(unittest.TestCase):
    def test_fixed_paths_and_argument_boundaries(self):
        paths = CONFIG.paths(VALID)
        self.assertEqual(paths, {'kernel': '/run/a733-guests/one/Image',
                                'initrd': '/run/a733-guests/one/initramfs.cpio.gz',
                                'disk': '/run/a733-guests/one/disk.raw',
                                'enrollment': '/run/a733-guests/one/enrollment.json'})
        unit = CONFIG.render_unit(VALID, '/opt/a733/launch-kvm-guest.py', 108)
        lines = dict(line.split('=', 1) for line in unit.splitlines() if '=' in line)
        args = shlex.split(lines['ExecStart'])
        self.assertEqual(args[:4], ['/usr/bin/python3', '-I', '-B', '/opt/a733/launch-kvm-guest.py'])
        expected = []
        for key in ('kernel', 'initrd', 'disk'):
            expected += ['--' + key, paths[key], '--' + key + '-sha256', VALID[key]['sha256']]
        self.assertEqual(args[4:], expected + ['--memory-mib', '128', '--cpus', '2'])
        for key, value in {'Type': 'exec', 'User': 'a733-vm-one', 'Group': 'a733-vm-one',
                           'SupplementaryGroups': '108', 'Restart': 'no', 'UMask': '0077',
                           'NoNewPrivileges': 'yes', 'CapabilityBoundingSet': '',
                           'RestrictSUIDSGID': 'yes', 'LimitCORE': '0', 'MemoryMax': '256M',
                           'MemorySwapMax': '0', 'TasksMax': '256', 'StandardInput': 'null', 'StandardOutput': 'journal',
                           'StandardError': 'journal', 'KillMode': 'control-group',
                           'KillSignal': 'SIGTERM', 'TimeoutStopSec': '30', 'SendSIGKILL': 'no'}.items():
            self.assertEqual(lines[key], value)
        self.assertEqual(set(lines), {'Description', 'After', 'ExecStart', 'Type', 'User', 'Group',
                         'SupplementaryGroups', 'Restart', 'UMask', 'NoNewPrivileges',
                         'CapabilityBoundingSet', 'RestrictSUIDSGID', 'LimitCORE', 'MemoryMax',
                         'MemorySwapMax', 'TasksMax', 'StandardInput', 'StandardOutput', 'StandardError', 'KillMode',
                         'KillSignal', 'TimeoutStopSec', 'SendSIGKILL'})
        self.assertNotIn('[Install]', unit)

    def test_launcher_injection_and_specifiers(self):
        for path in ('relative', '/a b', '/a\nb', '/a%u', '/a$HOME', '/a"b', "/a'b", '/a;b',
                     '/a`id`', '/a$(id)', '/a\\b', '/a/../b', '/a/./b', '//a', '/a/', '/',
                     '/ä/b', '/a|b', '/a&b', Path('/a')):
            with self.subTest(path=path), self.assertRaises(ValueError):
                CONFIG.render_unit(VALID, path, 108)

    def test_kvm_group_and_config_refusals(self):
        for gid in (True, 0, -1, 60001, 4294967295, 108.0, '108'):
            with self.subTest(gid=gid), self.assertRaises(ValueError):
                CONFIG.render_unit(VALID, '/a', gid)
        CONFIG.render_unit(VALID, '/a', 4294967294)
        for method in (CONFIG.canonical, CONFIG.paths):
            with self.assertRaises(ValueError):
                method({**VALID, 'name': 'one%u'})
        with self.assertRaises(ValueError):
            CONFIG.render_unit({**VALID, 'name': 'one\nExecStart=/bad'}, '/a', 108)


if __name__ == '__main__':
    unittest.main()
