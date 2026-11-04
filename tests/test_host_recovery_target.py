"""Offline policy fixtures only: no hardware probing, mounts or acceptance."""
import copy
from contextlib import ExitStack
import importlib.util
import os
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import uuid

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('recovery_target', REPO / 'scripts/host-recovery-target.py')
T = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(T)
IDENTITY = '89d3d789-f8c5-4cb7-908b-d0b1538c789a'


def row(identity='0:1', kind='tmpfs', source='tmpfs', flags=None):
    return {'id': identity, 'root': '/', 'type': kind, 'source': source,
            'flags': set(flags or ('rw', 'nodev', 'nosuid')), 'super': {'rw'}}


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.size = 250_000_000_000
        self.geometry = T.LAYOUT.layout(self.size, 512, 4096)
        self.disk = {'path': '/dev/nvme0n1', 'kname': 'nvme0n1', 'type': 'disk',
                     'tran': 'nvme', 'model': 'fixture', 'serial': 'unique', 'size': self.size,
                     'log-sec': 512, 'phy-sec': 4096, 'ro': False, 'mountpoints': [],
                     'maj:min': '259:0', 'children': []}
        for p in self.geometry['partitions']:
            i = p['number']
            self.disk['children'].append(dict(self.disk, path=f'/dev/nvme0n1p{i}',
                kname=f'nvme0n1p{i}', type='part', size=p['size_bytes'],
                **{'maj:min': f'259:{i}', 'children': []}))
        self.table = {'/': [row()], '/run': [row()]}

    def resolve(self, path):
        nodes = list(T.PLANNER.flatten([self.disk]))
        node = next(n for n in nodes if n['path'] == path)
        return path, node['maj:min']

    def safety(self, nodes):
        return {n['maj:min']: {'holders': [], 'read_only': False, 'swap': False,
                              'mounted': bool(n['mountpoints'])} for n in nodes}

    def policy(self, mounted=False, **kwargs):
        args = dict(inventory={'blockdevices': [self.disk]}, requested='/dev/nvme0n1',
                    expected_model='fixture', expected_serial='unique', expected_bytes=self.size,
                    slot='B', state_uuid=IDENTITY, table=self.table, mounted_state=mounted,
                    resolver=self.resolve, safety_reader=self.safety)
        args.update(kwargs)
        return T.inventory_policy(**args)

    def own_mount(self):
        self.table[T.STATE_MOUNT] = [row('259:4', 'ext4', '/dev/nvme0n1p4')]
        self.disk['children'][3]['mountpoints'] = [T.STATE_MOUNT]

    def test_identity_and_fixed_geometry(self):
        disk, target, state, nodes, geometry = self.policy()
        self.assertEqual(len(disk), 8)
        self.assertEqual(target['partition_number'], 3)
        self.assertEqual(target['size_bytes'], 4 * T.LAYOUT.GIB)
        self.assertEqual(state['size_bytes'], 8 * T.LAYOUT.GIB)
        self.assertEqual(state['uuid'], IDENTITY)
        for kwargs in ({'expected_model': 'other'}, {'expected_serial': 'other'},
                       {'expected_bytes': self.size - 512}, {'expected_bytes': True},
                       {'slot': 'C'}, {'state_uuid': str(uuid.UUID(int=0))},
                       {'state_uuid': IDENTITY.upper()}, {'mounted_state': 1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.policy(**kwargs)

    def test_all_six_nodes_must_be_idle_and_writable(self):
        for node in [self.disk] + self.disk['children']:
            for key, value in (('ro', True), ('mountpoints', ['/unexpected']), ('size', 1)):
                with self.subTest(node=node['kname'], key=key):
                    with mock.patch.dict(node, {key: value}), self.assertRaises(ValueError):
                        self.policy()
            for flag, value in (('holders', ['dm-0']), ('read_only', True),
                                ('swap', True), ('mounted', True)):
                evidence = self.safety([self.disk] + self.disk['children'])
                evidence[node['maj:min']][flag] = value
                with self.subTest(node=node['kname'], flag=flag), self.assertRaises(ValueError):
                    self.policy(safety_reader=lambda nodes: evidence)

    def test_aliases_mappings_and_geometry_rejected(self):
        original = copy.deepcopy(self.disk)
        changes = [lambda: self.disk['children'].append(copy.deepcopy(self.disk['children'][0])),
                   lambda: self.disk['children'][0]['children'].append({'type': 'crypt'}),
                   lambda: self.disk['children'][0].update({'log-sec': 4096}),
                   lambda: self.disk['children'][0].update({'kname': 'nvme0n1p9'}),
                   lambda: self.disk.update({'tran': 'sata'})]
        for change in changes:
            self.disk = copy.deepcopy(original)
            change()
            with self.assertRaises(ValueError):
                self.policy()
        self.disk = original
        with self.assertRaises(ValueError):
            self.policy(inventory={'blockdevices': [self.disk, copy.deepcopy(self.disk['children'][0])]})

    def test_owned_state_mount_only(self):
        self.own_mount()
        with mock.patch.object(T.os, 'stat', return_value=SimpleNamespace(st_dev=os.makedev(259, 4))):
            self.policy(True)
            with self.assertRaises(ValueError):
                self.policy(False)
            original = copy.deepcopy(self.table)
            mutations = [lambda r: r.update(source='/dev/wrong'),
                         lambda r: r.update(root='/subtree'),
                         lambda r: r.update(type='xfs'),
                         lambda r: r.update(flags={'ro', 'nodev', 'nosuid'}),
                         lambda r: r.update(flags={'rw', 'nodev'}),
                         lambda r: r.update(super={'ro'})]
            for mutation in mutations:
                self.table = copy.deepcopy(original)
                mutation(self.table[T.STATE_MOUNT][0])
                with self.assertRaises(ValueError):
                    self.policy(True)
            self.table = copy.deepcopy(original)
            self.table['/bind'] = copy.deepcopy(self.table[T.STATE_MOUNT])
            with self.assertRaises(ValueError):
                self.policy(True)
            self.table = copy.deepcopy(original)
            self.table[T.STATE_MOUNT].append(row('0:2'))
            with self.assertRaises(ValueError):
                self.policy(True)
        self.table = original
        with mock.patch.object(T.os, 'stat', return_value=SimpleNamespace(st_dev=os.makedev(259, 5))):
            with self.assertRaises(ValueError):
                self.policy(True)

    def superblock(self):
        data = bytearray(1024)
        struct.pack_into('<I', data, 4, 8 * T.LAYOUT.GIB // 4096)
        struct.pack_into('<I', data, 0x18, 2)
        struct.pack_into('<H', data, 0x38, 0xef53)
        struct.pack_into('<I', data, 0x5c, 4)
        struct.pack_into('<I', data, 0xe0, 8)
        data[0x68:0x78] = uuid.UUID(IDENTITY).bytes
        return data

    def test_external_or_unknown_journal_rejected_before_mount(self):
        for offset, value in ((0xe0, 0), (0xe0, 9), (0xe4, 179), (0x60, 0x8)):
            data = self.superblock()
            struct.pack_into('<I', data, offset, value)
            with self.subTest(offset=offset, value=value), self.assertRaisesRegex(ValueError, 'internal'):
                T.state_superblock(7, IDENTITY, lambda *args: bytes(data))
        data = self.superblock()
        data[0xd0] = 1
        with self.assertRaisesRegex(ValueError, 'internal'):
            T.state_superblock(7, IDENTITY, lambda *args: bytes(data))

    def test_state_superblock_identity_size_and_journal(self):
        data = self.superblock()
        reader = lambda fd, size, offset: bytes(data)
        self.assertEqual(T.state_superblock(7, IDENTITY, reader), IDENTITY)
        struct.pack_into('<I', data, 0x60, 0x84)  # 64bit + journal needs_recovery accepted
        self.assertEqual(T.state_superblock(7, IDENTITY, reader), IDENTITY)
        for offset, fmt, value in ((4, '<I', 1), (0x18, '<I', 7), (0x38, '<H', 0),
                                   (0x5c, '<I', 0), (0x150, '<I', 1)):
            data = self.superblock()
            struct.pack_into(fmt, data, offset, value)
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                T.state_superblock(7, IDENTITY, reader)
        data = self.superblock()
        data[0x68] ^= 1
        with self.assertRaises(ValueError):
            T.state_superblock(7, IDENTITY, reader)
        with self.assertRaises(ValueError):
            T.state_superblock(7, IDENTITY, lambda *args: b'')

    def test_ram_environment_rejects_disk_overlay_kernel_swap_and_bind(self):
        mountinfo = '1 0 0:1 / / rw - rootfs rootfs rw\n2 1 0:1 / /run rw,nodev,nosuid - tmpfs tmpfs rw\n'
        def guard(text=mountinfo, swaps='Filename Type Size Used Priority\n', machine='aarch64',
                  release='6.6.98-a733-diag', uid=0):
            def read(path):
                return text if str(path).endswith('mountinfo') else swaps
            with mock.patch.object(T.os, 'geteuid', return_value=uid), \
                 mock.patch.object(T.os, 'uname', return_value=SimpleNamespace(machine=machine, release=release)), \
                 mock.patch.object(T.Path, 'read_text', read), \
                 mock.patch.object(T.os, 'stat', return_value=SimpleNamespace(st_dev=os.makedev(0, 1))):
                return T.environment_guard()
        self.assertIn('/run', guard())
        for kwargs in ({'uid': 1}, {'machine': 'x86_64'}, {'release': 'other'},
                       {'swaps': 'Filename Type Size Used Priority\n/dev/zram0 partition 10 1 -2\n'},
                       {'text': mountinfo.replace('rootfs rootfs', 'ext4 /dev/nvme0n1p2')},
                       {'text': mountinfo.replace('rootfs rootfs', 'overlay overlay')},
                       {'text': mountinfo.replace('0:1 / /run', '0:1 /sub /run')},
                       {'text': mountinfo.replace('/run rw,', '/run ro,')}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                guard(**kwargs)

    def test_snapshot_checks_every_node_and_has_no_active_context(self):
        gpt = {'partition_guids': [str(uuid.uuid4()) for _ in range(5)]}
        opened = []
        def opening(record):
            opened.append(record)
            return 99
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(T, 'environment_guard', return_value=self.table))
            stack.enter_context(mock.patch.object(T.INSTALL, 'inventory', return_value={'blockdevices': [self.disk]}))
            stack.enter_context(mock.patch.object(T.PLANNER, 'identify', self.resolve))
            stack.enter_context(mock.patch.object(T.PLANNER, 'read_safety', self.safety))
            geometry = stack.enter_context(mock.patch.object(T.LIVE, 'sysfs_geometry'))
            stack.enter_context(mock.patch.object(T.LIVE, 'open_checked', opening))
            stack.enter_context(mock.patch.object(T.LIVE, 'gpt_context', return_value=gpt))
            probe = stack.enter_context(mock.patch.object(T, 'state_superblock'))
            stack.enter_context(mock.patch.object(T.os, 'close'))
            context = T.snapshot('/dev/nvme0n1', 'fixture', 'unique', self.size, IDENTITY, 'B')
        self.assertEqual(set(context), {'disk', 'target', 'state', 'gpt'})
        self.assertEqual(len(opened), 6)
        self.assertEqual(context['target']['partition_uuid'], gpt['partition_guids'][2])
        self.assertEqual(context['state']['partition_uuid'], gpt['partition_guids'][3])
        geometry.assert_called_once()
        probe.assert_called_once_with(99, IDENTITY)

    def test_actual_gpt_tamper_and_4kn(self):
        for logical in (512, 4096):
            size = self.size // logical * logical
            encoded = T.LAYOUT.encode_gpt(size, logical, 4096)
            record = {'size_bytes': size, 'logical_sector_bytes': logical,
                      'physical_sector_bytes': 4096}
            extents = encoded['extents']
            def reader(fd, count, offset):
                for extent in extents:
                    start = extent['offset_bytes']
                    if start <= offset and offset + count <= start + len(extent['data']):
                        return extent['data'][offset-start:offset-start+count]
                return b''
            valid = T.LIVE.gpt_context(1, record, reader)
            self.assertEqual(len(valid['partition_guids']), 5)
            extents[-1]['data'] = extents[-1]['data'][:-1] + b'X'
            with self.assertRaises(ValueError):
                T.LIVE.gpt_context(1, record, reader)

    def test_sysfs_4kn_start_and_size_use_512_byte_units(self):
        geometry = T.LAYOUT.layout(self.size // 4096 * 4096, 4096, 4096)
        nodes = [self.disk] + self.disk['children']
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            parent = root / 'devices' / 'nvme0n1'
            parent.mkdir(parents=True)
            for i, node in enumerate(nodes):
                base = parent if not i else parent / node['kname']
                base.mkdir(exist_ok=True)
                (base / 'dev').write_text(node['maj:min'])
                (root / node['maj:min']).symlink_to(base)
                if i:
                    p = geometry['partitions'][i-1]
                    (base / 'partition').write_text(str(i))
                    (base / 'start').write_text(str(p['start_bytes']//512))
                    (base / 'size').write_text(str(p['size_bytes']//512))
            T.LIVE.sysfs_geometry(nodes, geometry, root)
            (parent / nodes[1]['kname'] / 'start').write_text(str(geometry['partitions'][0]['start_bytes']//4096))
            with self.assertRaises(ValueError):
                T.LIVE.sysfs_geometry(nodes, geometry, root)


if __name__ == '__main__':
    unittest.main()
