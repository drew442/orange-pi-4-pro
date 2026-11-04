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
NETWORK = {'mode': 'isolated', 'host_tcp_port': 2222, 'guest_tcp_port': 22}


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
        self.assertEqual(data, (json.dumps(VALID, sort_keys=True, separators=(',', ':')) + '\n').encode())

    def test_network_roundtrip_boundaries_and_copy(self):
        self.assertEqual(CONFIG.FIELDS, set(VALID))
        for host, guest in ((1024, 1), (2222, 22), (65535, 65535)):
            network = {**NETWORK, 'host_tcp_port': host, 'guest_tcp_port': guest}
            value = {**VALID, 'network': network}
            result = self.validate(value)
            self.assertEqual(result, value)
            self.assertEqual(CONFIG.canonical(result),
                             (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode())
            result['network']['host_tcp_port'] = 9999
            self.assertEqual(network['host_tcp_port'], host)

    def test_network_rejects_unsupported_profiles_fields_and_port_types(self):
        bad = [None, False, [], 'isolated', {}]
        bad.extend({**NETWORK, 'mode': mode} for mode in ('off', 'nat', 'bridge', True, None))
        for key in NETWORK:
            missing = dict(NETWORK)
            del missing[key]
            bad.append(missing)
        for key, value in (('host_ip', '0.0.0.0'), ('guest_ip', '10.0.2.16'),
                           ('protocol', 'udp'), ('forwards', []), ('mac', '00:00:00:00:00:00')):
            bad.append({**NETWORK, key: value})
        for key, values in {'host_tcp_port': (True, False, None, 0, 1, 1023, 65536, 2222.0, '2222'),
                            'guest_tcp_port': (True, False, None, 0, -1, 65536, 22.0, '22')}.items():
            bad.extend({**NETWORK, key: value} for value in values)
        for network in bad:
            with self.subTest(network=network), self.assertRaises(ValueError):
                self.validate({**VALID, 'network': network})
        data = json.dumps({**VALID, 'network': NETWORK})
        with self.assertRaises(ValueError):
            CONFIG.validate_config(data.replace('"host_tcp_port": 2222',
                                                '"host_tcp_port": 2222, "host_tcp_port": 3333'))

    def test_network_survives_manifest_checkpoint_and_restore(self):
        spec = importlib.util.spec_from_file_location('network_manifest', BASE / 'configs/guest_manifest.py')
        manifest_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(manifest_module)
        config = {**VALID, 'network': NETWORK}
        manifest = {'schema': 1, 'profile': manifest_module.PROFILE, 'config': config,
                    'guest_uuid': '11111111-1111-4111-8111-111111111111', 'files': {}}
        for key, filename in manifest_module.FILENAMES.items():
            uid, gid, mode = (config['uid'], config['gid'], '0600') if key == 'disk' else (0, 0, '0444')
            manifest['files'][filename] = {**config[key], 'uid': uid, 'gid': gid, 'mode': mode}
        normalized = manifest_module.validate_manifest(manifest_module.canonical(manifest))
        checkpoint = manifest_module.proposed_manifest(normalized, 'd' * 64,
                                                       {**VALID['disk'], 'sha256': 'e' * 64})
        restored = manifest_module.restored_manifest(checkpoint, 'f' * 64, 'a' * 64,
                                                     '22222222-2222-4222-8222-222222222222')
        for value in (normalized, checkpoint, restored):
            self.assertEqual(value['config']['network'], NETWORK)

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
    def test_network_adds_only_paired_forward_arguments(self):
        legacy = CONFIG.render_unit(VALID, '/opt/a733/launch-kvm-guest.py', 108)
        forwarded = CONFIG.render_unit({**VALID, 'network': NETWORK},
                                       '/opt/a733/launch-kvm-guest.py', 108)
        self.assertEqual(forwarded, legacy.replace('--cpus 2\n',
                         '--cpus 2 --host-tcp-port 2222 --guest-tcp-port 22\n'))

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
