"""Pure permit-schema regressions; no process or hardware acceptance claims."""
import importlib.util
import json
from pathlib import Path
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location(
    'guest_run_permit', Path(__file__).resolve().parents[1] / 'configs/guest_run_permit.py')
P = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(P)


def permit():
    return {
        'schema': 1, 'profile': P.PROFILE,
        'run_id': 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
        'boot_id': 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
        'storage': {'state_uuid': '11111111-1111-4111-8111-111111111111',
                    'guest_uuid': '22222222-2222-4222-8222-222222222222',
                    'nvme_serial': 'NVME-123'},
        'config': {'schema': 1, 'name': 'autotest', 'uid': 60002, 'gid': 60002,
                   'memory_mib': 256, 'memory_max_mib': 320, 'cpus': 1,
                   'kernel': {'bytes': 1024, 'sha256': 'a' * 64},
                   'initrd': {'bytes': 1024, 'sha256': 'b' * 64},
                   'disk': {'bytes': 4096, 'sha256': 'c' * 64},
                   'network': {'mode': 'isolated', 'host_tcp_port': 8080,
                               'guest_tcp_port': 80}},
        'manifest_sha256': 'd' * 64, 'head_sha256': 'e' * 64,
        'host_payload_sha256': 'f' * 64,
        'qemu': {'path': P.QEMU_PATH, 'bytes': 1024, 'sha256': '0' * 64},
        'namespaces': {'pid': {'dev': 4, 'ino': 0xEFFFFFFC},
                       'user': {'dev': 4, 'ino': 0xEFFFFFFD},
                       'mnt': {'dev': 4, 'ino': 4026531841}},
        'kvm_gid': 108, 'parent': {'pid': 100, 'start_ticks': 200},
        'child': {'pid': 101, 'start_ticks': 201},
        'locks': {'lease': {'fd': 3, 'dev': 0, 'ino': 1000},
                  'guest': {'fd': 4, 'dev': 0, 'ino': 1001}},
        'automatic': True,
    }


class PermitTests(unittest.TestCase):
    def changed(self, path, value):
        record = permit()
        target = record
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        return record

    def invalid(self, path, values):
        for value in values:
            record = self.changed(path, value)
            with self.subTest(path=path, value=repr(value)[:80]):
                with self.assertRaises(ValueError):
                    P.canonical(record)
                with self.assertRaises(ValueError):
                    P.validate_permit(json.dumps(record))

    def test_roundtrip_stable_and_independent_ownership(self):
        record = permit()
        encoded = P.canonical(record)
        self.assertTrue(encoded.endswith(b'\n'))
        self.assertNotIn(b' ', encoded)
        normalized = P.validate_permit(encoded)
        self.assertEqual(normalized, record)
        self.assertEqual(P.canonical(normalized), encoded)
        normalized['storage']['nvme_serial'] = 'changed'
        normalized['config']['disk']['bytes'] = 8192
        normalized['locks']['lease']['ino'] = 2000
        normalized['child']['pid'] = 200
        self.assertEqual(record, permit())
        self.assertEqual(P.validate_permit(encoded), permit())
        record['automatic'] = False
        record['config'].pop('network')
        self.assertEqual(P.validate_permit(P.canonical(record)), record)

    def test_exact_field_sets(self):
        for path in ((), ('storage',), ('config',), ('qemu',), ('parent',),
                     ('child',), ('locks',), ('locks', 'lease'), ('locks', 'guest'),
                     ('namespaces',), ('namespaces', 'pid'),
                     ('namespaces', 'user'), ('namespaces', 'mnt')):
            for operation in ('extra', 'missing'):
                record = permit()
                target = record
                for key in path:
                    target = target[key]
                if operation == 'extra':
                    target['extra'] = 1
                else:
                    del target[next(iter(target))]
                with self.subTest(path=path, operation=operation), self.assertRaises(ValueError):
                    P.canonical(record)

    def test_profiles_uuids_and_pins(self):
        self.invalid(('schema',), [True, 0, 2, '1'])
        self.invalid(('profile',), ['a733-guest-head-v1', 'future', None])
        for key in ('run_id', 'boot_id', 'storage'):
            if key == 'storage':
                for subkey in ('state_uuid', 'guest_uuid'):
                    self.invalid((key, subkey), ['0' * 32, 'bad', None,
                                                '00000000-0000-0000-0000-000000000000'])
            else:
                original = permit()[key]
                self.invalid((key,), [original.upper(), original.replace('-', ''), None,
                                      '00000000-0000-0000-0000-000000000000'])
        self.invalid(('storage', 'nvme_serial'), ['', '../disk', 'a' * 65])
        for key in ('manifest_sha256', 'head_sha256', 'host_payload_sha256'):
            self.invalid((key,), ['A' * 64, 'a' * 63, None])
        self.invalid(('qemu', 'sha256'), ['A' * 64, 'x' * 64])

    def test_reuses_full_config_validation(self):
        self.invalid(('config', 'name'), ['firstkvm', 'bad_name'])
        self.invalid(('config', 'schema'), [True, 2])
        self.invalid(('config', 'uid'), [True, 0, 60003])
        self.invalid(('config', 'memory_max_mib'), [319, True])
        self.invalid(('config', 'disk', 'bytes'), [4097, True])
        self.invalid(('config', 'network', 'mode'), ['tap', 'user'])
        self.invalid(('config', 'network', 'host_tcp_port'), [80, True, 65536])

    def test_integer_bounds_and_bool_rejection(self):
        bounds = [(('qemu', 'bytes'), 1, P.MAX_INTEGER),
                  (('kvm_gid',), 1, 2147483647)]
        for role in ('parent', 'child'):
            bounds.extend([((role, 'pid'), 1, P.MAX_PID),
                           ((role, 'start_ticks'), 1, P.MAX_INTEGER)])
        for role in ('lease', 'guest'):
            bounds.extend([(('locks', role, 'fd'), 3, P.MAX_FD),
                           (('locks', role, 'dev'), 0, P.MAX_INTEGER),
                           (('locks', role, 'ino'), 1, P.MAX_INTEGER)])
        for namespace in ('pid', 'user', 'mnt'):
            bounds.extend([(('namespaces', namespace, 'dev'), 0, P.MAX_INTEGER),
                           (('namespaces', namespace, 'ino'), 1, P.MAX_INTEGER)])
        for path, low, high in bounds:
            self.invalid(path, [True, False, low - 1, high + 1, 1.5, '1'])
            for value in (low, high):
                record = self.changed(path, value)
                if path == ('locks', 'guest', 'fd') and value == 3:
                    record['locks']['lease']['fd'] = 4
                with self.subTest(path=path, value=value):
                    self.assertEqual(P.validate_permit(P.canonical(record)), record)
        self.invalid(('automatic',), [0, 1, None, 'true'])

    def test_namespace_identity_objects_have_exact_typed_fields(self):
        self.invalid(('namespaces',), [None, [], 'pid'])
        for namespace in ('pid', 'user', 'mnt'):
            self.invalid(('namespaces', namespace), [None, [], 1, True])

    def test_fixed_binary_and_identity_collisions(self):
        self.invalid(('qemu', 'path'), ['/tmp/qemu-system-aarch64',
                                      '/usr/bin/../bin/qemu-system-aarch64',
                                      P.QEMU_PATH + ' ', None])
        self.invalid(('kvm_gid',), [60002])
        self.invalid(('child', 'pid'), [100])
        self.invalid(('locks', 'guest', 'fd'), [3])
        self.invalid(('locks', 'guest', 'ino'), [1000])
        # Equal inode numbers on distinct devices are different lock objects.
        record = permit()
        record['locks']['guest'].update(dev=1, ino=1000)
        self.assertEqual(P.validate_permit(P.canonical(record)), record)
        # Start ticks can coincide for distinct PIDs.
        record['child']['start_ticks'] = record['parent']['start_ticks']
        self.assertEqual(P.validate_permit(P.canonical(record)), record)

    def test_json_parser_bounds_duplicates_nonfinite_and_depth(self):
        encoded = P.canonical(permit())
        invalid = [b'', b'\xff', '"\ud800"', b' ' * (P.LIMIT + 1),
                   encoded.replace(b'"schema":1', b'"schema":1,"schema":1', 1),
                   encoded.replace(b'"ino":1000', b'"ino":1000,"ino":1000'),
                   encoded.replace(b'"automatic":true', b'"automatic":NaN'),
                   encoded.replace(b'"automatic":true', b'"automatic":Infinity'),
                   '[' * (P.MAX_DEPTH + 1) + '0' + ']' * (P.MAX_DEPTH + 1),
                   encoded.replace(b'"NVME-123"', b'"\\ud800"')]
        for data in invalid:
            with self.subTest(data=repr(data)[:100]), self.assertRaises(ValueError):
                P.validate_permit(data)
        self.assertEqual(P.validate_permit(encoded + b' ' * (P.LIMIT - len(encoded))), permit())
        self.assertEqual(P.validate_permit(encoded.decode()), permit())

    def test_preflight_rejects_before_encoding(self):
        deep = []
        for _ in range(P.MAX_DEPTH):
            deep = [deep]
        cyclic = []
        cyclic.append(cyclic)
        invalid = [{'unexpected': 'a' * (P.LIMIT + 1)},
                   {str(index): 'abcd' * 100 for index in range(200)},
                   ['a'] * P.LIMIT, deep, cyclic, {'bad': 1 << 100000},
                   {'bad': '\ud800'}, {1: 'bad'}, {'bad': float('nan')},
                   {'bad': float('inf')}, {'bad': object()}]
        # No json.dumps call is permitted for any oversized/deep/unsupported tree.
        with mock.patch.object(P.json, 'dumps', side_effect=AssertionError('encoding called')):
            for value in invalid:
                with self.subTest(value=type(value).__name__), self.assertRaises(ValueError):
                    P.canonical(value)


if __name__ == '__main__':
    unittest.main()
