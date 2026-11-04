"""Read-only guest activation policy fixtures; no physical acceptance evidence."""
import copy
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import struct
from types import SimpleNamespace
import unittest
from unittest import mock
import uuid

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('guest_mount_target', REPO / 'scripts/guest-mount-target.py')
TARGET = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TARGET)
LIVE = TARGET.LIVE
UUID = 'fbd7bf7b-8b18-4bfb-b04f-8305c191f190'


class GuestMountPolicyTests(unittest.TestCase):
    def setUp(self):
        self.size = 250_000_000_000
        geometry = LIVE.LAYOUT.layout(self.size, 512, 4096)
        self.disk = {'path': '/dev/nvme0n1', 'kname': 'nvme0n1', 'type': 'disk',
                     'tran': 'nvme', 'model': 'fixture', 'serial': 'unique', 'size': self.size,
                     'log-sec': 512, 'phy-sec': 4096, 'ro': False, 'mountpoints': [],
                     'maj:min': '259:0', 'children': []}
        for part in geometry['partitions']:
            i = part['number']
            self.disk['children'].append(dict(self.disk, path=f'/dev/nvme0n1p{i}',
                kname=f'nvme0n1p{i}', type='part', size=part['size_bytes'],
                **{'maj:min': f'259:{i}', 'children': []}))
        self.active = {'slot': 'B', 'root_source': '/dev/nvme0n1p3', 'root_major_minor': '259:3'}
        self.state = {'source': '/dev/nvme0n1p4', 'major_minor': '259:4'}
        self.disk['children'][2]['mountpoints'] = ['/']
        self.disk['children'][3]['mountpoints'] = ['/run/a733-state']
        self.table = {}
        self.mounted = False
        self.safety_overrides = {}

    def resolve(self, path):
        for node in TARGET.PLANNER.flatten([self.disk]):
            if node['path'] == path:
                return path, node['maj:min']
        raise ValueError('unknown fixture node')

    def safety(self, nodes):
        result = {n['maj:min']: dict(holders=[], read_only=False,
                  mounted=bool(n['mountpoints']), swap=False) for n in nodes}
        for identity, values in self.safety_overrides.items():
            result[identity].update(values)
        return result

    def policy(self, requested='/dev/nvme0n1', model='fixture', serial='unique', size=None, identity=UUID):
        return TARGET.inventory_policy({'blockdevices': [self.disk]}, requested, model,
            serial, self.size if size is None else size, identity, self.active, self.state,
            self.table, self.mounted, self.resolve, self.safety,
            lambda _: SimpleNamespace(st_dev=os.makedev(259, 5)))

    def mount(self):
        self.mounted = True
        self.disk['children'][4]['mountpoints'] = [TARGET.GUEST_MOUNT]
        self.table[TARGET.GUEST_MOUNT] = [dict(id='259:5', source='/dev/nvme0n1p5',
            type='ext4', root='/', flags={'rw', 'nodev', 'nosuid', 'noexec'}, super={'rw'})]

    def test_root_b_and_guest_identity_with_busy_unused_root(self):
        # Other roots are not mount targets and need no idle check.
        self.disk['children'][1]['mountpoints'] = ['/unused']
        self.safety_overrides['259:2'] = dict(holders=['fixture'], swap=True)
        disk, guest, nodes, geometry = self.policy()
        self.assertEqual(guest['canonical_path'], '/dev/nvme0n1p5')
        self.assertEqual(guest['partition_number'], 5)
        self.assertEqual(guest['uuid'], UUID)
        self.assertEqual(guest['size_bytes'], geometry['partitions'][4]['size_bytes'])
        self.assertEqual(len(nodes), 6)
        self.assertEqual(disk['serial'], 'unique')

    def test_identity_and_whole_device_rejections(self):
        for kwargs in ({'requested': '/dev/nvme0n1p4'}, {'model': 'other'},
                       {'serial': 'other'}, {'size': self.size - 512}, {'size': True},
                       {'identity': UUID.upper()}, {'identity': str(uuid.UUID(int=0))}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.policy(**kwargs)
        for record, field, value in ((self.active, 'root_source', '/dev/nvme1n1p3'),
                                     (self.state, 'source', '/dev/nvme1n1p4'),
                                     (self.state, 'major_minor', '259:99')):
            with mock.patch.dict(record, {field: value}), self.assertRaises(ValueError):
                self.policy()

    def test_whole_and_p5_busy_or_unknown_flags(self):
        for number in (0, 5):
            for key, value in (('holders', ['dm-0']), ('swap', True),
                               ('read_only', True), ('mounted', True)):
                self.safety_overrides = {f'259:{number}': {key: value}}
                with self.subTest(number=number, key=key), self.assertRaises(ValueError):
                    self.policy()
            self.safety_overrides = {}
            node = self.disk if number == 0 else self.disk['children'][4]
            for value in (True, None, '0'):
                with mock.patch.dict(node, ro=value), self.assertRaises(ValueError):
                    self.policy()

    def test_mounted_owned_and_inventory_kernel_consistency(self):
        self.mount()
        self.assertEqual(self.policy()[1]['mountpoint'], TARGET.GUEST_MOUNT)
        for mountpoints in ([], ['/alias'], [TARGET.GUEST_MOUNT, '/alias']):
            with mock.patch.dict(self.disk['children'][4], mountpoints=mountpoints), self.assertRaises(ValueError):
                self.policy()
        self.safety_overrides = {'259:5': {'mounted': False}}
        with self.assertRaises(ValueError):
            self.policy()

    def test_foreign_alias_nested_and_unsafe_mounts(self):
        self.mount()
        row = self.table[TARGET.GUEST_MOUNT][0]
        for field, value in (('id', '259:4'), ('source', '/dev/nvme0n1p4'),
                             ('source', '/dev/disk/by-uuid/alias'), ('root', '/bind'),
                             ('type', 'xfs'), ('super', {'ro'})):
            with mock.patch.dict(row, {field: value}), self.assertRaises(ValueError):
                self.policy()
        for flag in ('rw', 'nodev', 'nosuid', 'noexec'):
            with mock.patch.dict(row, flags=row['flags'] - {flag}), self.assertRaises(ValueError):
                self.policy()
        for path, added in (('/alias', row), (TARGET.GUEST_MOUNT + '/nested', dict(row, id='0:1'))):
            with mock.patch.dict(self.table, {path: [added]}), self.assertRaises(ValueError):
                self.policy()
        self.mounted = False
        with self.assertRaises(ValueError):
            self.policy()

    def test_mounted_directory_device_must_match(self):
        self.mount()
        with self.assertRaisesRegex(ValueError, 'device differs'):
            TARGET.mounted_guest_policy(self.table, self.disk['children'][4], True,
                lambda _: SimpleNamespace(st_dev=os.makedev(259, 4)))

    def test_duplicate_inventory_and_wrong_geometry_refused(self):
        self.disk['children'][4]['size'] -= 512
        with self.assertRaises(ValueError):
            self.policy()
        self.disk['children'][4]['size'] += 512
        self.disk['children'][4]['children'] = [copy.deepcopy(self.disk['children'][0])]
        with self.assertRaises(ValueError):
            self.policy()

    def superblock(self):
        data = bytearray(1024)
        struct.pack_into('<H', data, 0x38, 0xef53)
        for offset, value in ((0x5c, 4), (0xe0, 8), (0x18, 2), (4, 16384)):
            struct.pack_into('<I', data, offset, value)
        data[0x68:0x78] = uuid.UUID(UUID).bytes
        return data

    def test_superblock_complete_size_uuid_internal_journal(self):
        data = self.superblock()
        def check():
            return TARGET.guest_superblock(7, UUID, 64 * 1024**2, lambda *args: bytes(data))
        self.assertEqual(check(), UUID)
        for offset, value in ((0x38, 0), (0x5c, 0), (0xe0, 0), (0xe4, 1),
                              (0xd0, 1), (0x60, 8), (0x18, 7),
                              (4, 16383), (0x150, 1)):
            saved = data[:]
            struct.pack_into('<I', data, offset, value)
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                check()
            data[:] = saved
        for identity in (UUID.upper(), str(uuid.UUID(int=0)), str(uuid.uuid4())):
            with self.assertRaises(ValueError):
                TARGET.guest_superblock(7, identity, 64 * 1024**2, lambda *args: bytes(data))
        for capacity in (True, 0, 64 * 1024**2 - 4096):
            with self.assertRaises(ValueError):
                TARGET.guest_superblock(7, UUID, capacity, lambda *args: bytes(data))
        with self.assertRaises(ValueError):
            TARGET.guest_superblock(7, UUID, 64 * 1024**2, lambda *args: bytes(data[:-1]))
        struct.pack_into('<I', data, 0x60, 0x80)
        struct.pack_into('<I', data, 0x150, 1)
        self.assertEqual(TARGET.guest_superblock(7, UUID, (2**32 + 16384) * 4096,
                                               lambda *args: bytes(data)), UUID)

    def test_snapshot_runtime_guard_first(self):
        with mock.patch.object(TARGET.MANAGEMENT, 'runtime_guard', side_effect=ValueError('live guard')):
            with self.assertRaisesRegex(ValueError, 'live guard'):
                TARGET.snapshot('/dev/nvme0n1', 'fixture', 'unique', self.size, UUID)

    def test_snapshot_reads_markers_pending_selection_and_closes_held_fds(self):
        release = LIVE.SELECT.INIT.PAYLOAD.KERNEL_RELEASE
        marker = json.dumps(dict(schema=1, slot='B',
            filesystem_uuid=LIVE.SELECT.INIT.PAYLOAD.SLOT_UUIDS['B'], kernel_release=release)).encode()
        selector = LIVE.SELECT.serialize(dict(SCHEMA='1', SLOT='A',
            ROOT_UUID=LIVE.SELECT.INIT.PAYLOAD.SLOT_UUIDS['A'], ROOT_MARKER_SHA256='f' * 64,
            KERNEL_RELEASE=release))
        files = {str(LIVE.MARKER): marker, str(LIVE.BOOT / 'selected.conf'): selector,
                 '/run/a733-state/.a733-state': f'a733-state-v1\n{UUID}\n'.encode()}
        with ExitStack() as stack:
            def patch(obj, name, **kwargs):
                return stack.enter_context(mock.patch.object(obj, name, **kwargs))
            patch(TARGET.MANAGEMENT, 'runtime_guard')
            patch(Path, 'read_text', return_value='')
            patch(TARGET.MANAGEMENT, 'mount_table', return_value={})
            patch(TARGET.MANAGEMENT, 'validate_mounts', return_value=(
                dict(source=self.active['root_source'], id=self.active['root_major_minor']),
                dict(source=self.state['source'], id=self.state['major_minor'])))
            patch(TARGET.MANAGEMENT, 'block_identity')
            patch(TARGET.MANAGEMENT, 'secure_path')
            patch(TARGET.MANAGEMENT, 'read_secure', side_effect=lambda path, _: files[str(path)])
            patch(TARGET.os, 'stat', return_value=SimpleNamespace(st_dev=123))
            patch(TARGET.os, 'uname', return_value=SimpleNamespace(release=release))
            patch(TARGET.INSTALL, 'inventory', return_value={'blockdevices': [self.disk]})
            patch(TARGET.PLANNER, 'identify', side_effect=self.resolve)
            patch(TARGET.PLANNER, 'read_safety', side_effect=self.safety)
            patch(LIVE, 'sysfs_geometry')
            opened = patch(LIVE, 'open_checked', side_effect=[70, 71, 72, 73, 74])
            patch(LIVE, 'gpt_context', return_value={'partition_guids': [str(uuid.UUID(int=i)) for i in range(1, 6)]})
            checked = patch(TARGET, 'guest_superblock', return_value=UUID)
            closed = patch(TARGET.os, 'close')
            result = TARGET.snapshot('/dev/nvme0n1', 'fixture', 'unique', self.size, UUID)
            self.assertEqual(set(result), {'disk', 'guest', 'active', 'state', 'gpt'})
            self.assertEqual(result['active']['slot'], 'B')
            self.assertEqual(result['state']['selector']['SLOT'], 'A')
            self.assertEqual(result['guest']['partition_uuid'], str(uuid.UUID(int=5)))
            self.assertEqual(opened.call_count, 2)
            checked.assert_called_once_with(71, UUID, result['guest']['size_bytes'])
            self.assertEqual(closed.call_args_list, [mock.call(71), mock.call(70)])
            checked.side_effect = ValueError('superblock refused')
            with self.assertRaisesRegex(ValueError, 'superblock refused'):
                TARGET.snapshot('/dev/nvme0n1', 'fixture', 'unique', self.size, UUID)
            self.assertEqual(closed.call_args_list[-2:], [mock.call(73), mock.call(72)])
            opened.side_effect = [74, ValueError('partition open refused')]
            with self.assertRaisesRegex(ValueError, 'partition open refused'):
                TARGET.snapshot('/dev/nvme0n1', 'fixture', 'unique', self.size, UUID)
            self.assertEqual(closed.call_args_list[-1], mock.call(74))


if __name__ == '__main__':
    unittest.main()
