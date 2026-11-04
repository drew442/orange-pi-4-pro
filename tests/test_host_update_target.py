"""Read-only policy fixtures; these are not hardware acceptance evidence."""
import copy
from contextlib import ExitStack
import importlib.util
import json
import os
import stat
from types import SimpleNamespace
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import uuid

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('host_update_target', REPO / 'scripts/host-update-target.py')
TARGET = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TARGET)


class TargetPolicyTests(unittest.TestCase):
    def setUp(self):
        self.size = 250_000_000_000
        self.logical = 512
        self.disk = {'path': '/dev/nvme0n1', 'kname': 'nvme0n1', 'type': 'disk',
                     'tran': 'nvme', 'model': 'fixture', 'serial': 'unique', 'size': self.size,
                     'log-sec': 512, 'phy-sec': 4096, 'ro': False, 'mountpoints': [],
                     'maj:min': '259:0', 'children': []}
        geometry = TARGET.LAYOUT.layout(self.size, 512, 4096)
        for part in geometry['partitions']:
            i = part['number']
            self.disk['children'].append(dict(self.disk, path=f'/dev/nvme0n1p{i}',
                kname=f'nvme0n1p{i}', type='part', size=part['size_bytes'],
                **{'maj:min': f'259:{i}', 'children': []}))
        self.active = {'slot': 'A', 'root_source': '/dev/nvme0n1p2', 'root_major_minor': '259:2'}
        self.state = {'source': '/dev/nvme0n1p4', 'major_minor': '259:4'}
        # Both expected mounts and an unrelated guest partition mount are allowed.
        for i in (2, 4, 5):
            self.disk['children'][i - 1]['mountpoints'] = ['/fixture']

    def resolve(self, path):
        for node in TARGET.PLANNER.flatten([self.disk]):
            if node['path'] == path:
                return path, node['maj:min']
        raise ValueError('unknown fixture node')

    def safety(self, nodes):
        return {n['maj:min']: {'holders': [], 'read_only': False,
                'mounted': bool(n['mountpoints']), 'swap': False} for n in nodes}

    def policy(self):
        return TARGET.inventory_policy({'blockdevices': [self.disk]}, '/dev/nvme0n1',
            'fixture', 'unique', self.size, 'B', self.active, self.state, self.resolve, self.safety)

    def test_explicit_identity_and_slot_rejection_matrix(self):
        valid = {'model': 'fixture', 'serial': 'unique', 'bytes': self.size, 'slot': 'B'}
        cases = []
        for field in ('model', 'serial'):
            for value in ('wrong', '', ' ', valid[field] + ' ', ' ' + valid[field]):
                cases.append((field, value))
        cases.extend([('bytes', True), ('bytes', self.size - 512),
                      ('slot', 'A'), ('slot', 'C'), ('slot', '')])
        for field, value in cases:
            with self.subTest(field=field, value=value):
                args = dict(valid, **{field: value})
                with self.assertRaises(ValueError):
                    TARGET.inventory_policy({'blockdevices': [self.disk]}, '/dev/nvme0n1',
                        args['model'], args['serial'], args['bytes'], args['slot'],
                        self.active, self.state, self.resolve, self.safety)

    def test_whole_disk_class_and_namespace_rejection_matrix(self):
        for field, value in (('type', 'part'), ('type', 'crypt'), ('tran', 'sata'),
                             ('kname', 'sda'), ('kname', 'nvme0n1p1'), ('kname', 'nvme1n1')):
            with self.subTest(field=field, value=value):
                with mock.patch.dict(self.disk, {field: value}):
                    with self.assertRaisesRegex(ValueError, 'whole canonical'):
                        self.policy()

    def test_successful_snapshot_returns_exact_stable_context(self):
        release = TARGET.SELECT.INIT.PAYLOAD.KERNEL_RELEASE
        root_uuid = TARGET.SELECT.INIT.PAYLOAD.SLOT_UUIDS['A']
        marker = json.dumps({'schema': 1, 'slot': 'A', 'filesystem_uuid': root_uuid,
                             'kernel_release': release}).encode()
        selector = {'SCHEMA': '1', 'SLOT': 'A', 'ROOT_UUID': root_uuid,
                    'ROOT_MARKER_SHA256': TARGET.digest(marker), 'KERNEL_RELEASE': release}
        selector_data = TARGET.SELECT.serialize(selector)
        state_uuid = '89d3d789-f8c5-4cb7-908b-d0b1538c789a'
        state_marker = f'a733-state-v1\n{state_uuid}\n'.encode()
        secure_files = {str(TARGET.MARKER): marker,
                        str(TARGET.BOOT / 'selected.conf'): selector_data,
                        '/run/a733-state/.a733-state': state_marker}
        # Exercise actual mountinfo parsing and validation, including all package binds.
        rows = []
        def mount(target, identity, source, root='/', filesystem='ext4', flags='rw,nodev,nosuid', extra=None):
            rows.append(f'{len(rows) + 1} 0 {identity} {root} {target} {flags} - '
                        f'{filesystem} {source} {extra or flags}')
        mount('/', '259:2', '/dev/nvme0n1p2', flags='ro')
        mount('/run/a733-state', '259:4', '/dev/nvme0n1p4')
        mount('/run', '0:1', 'tmpfs', filesystem='tmpfs')
        mount('/etc', '0:2', 'overlay', filesystem='overlay', flags='rw',
              extra='rw,upperdir=/run/etc-upper,workdir=/run/etc-work')
        for name in ('var', 'root', 'home'):
            mount('/' + name, '259:4', '/dev/nvme0n1p4', root='/' + name)
        for name, (target, _) in TARGET.MANAGEMENT.PACKAGE.COMPONENTS.items():
            mount('/' + target, '259:2', '/dev/nvme0n1p2',
                  root='/' + TARGET.MANAGEMENT.PACKAGE.ROOT + '/' + name, flags='ro,nodev,nosuid')
        mount('/etc/machine-id', '259:4', '/dev/nvme0n1p4', root='/identity/machine-id')
        mountinfo = '\n'.join(rows) + '\n'
        disk, target, nodes, geometry = self.policy()
        gpt = {'disk_guid': '12345678-1234-4321-8765-123456789abc',
               'partition_guids': [str(uuid.UUID(int=i)) for i in range(1, 6)], 'sha256': 'a' * 64}
        target['partition_uuid'] = gpt['partition_guids'][2]
        expected = {'disk': disk, 'target': target,
                    'active': dict(self.active, marker_sha256=TARGET.digest(marker)),
                    'state': dict(self.state, uuid=state_uuid, boot_directory=str(TARGET.BOOT),
                                  selector=selector, selector_sha256=TARGET.digest(selector_data)),
                    'gpt': gpt}
        with mock.patch.object(TARGET.MANAGEMENT, 'runtime_guard') as guard, \
                mock.patch.object(Path, 'read_text', return_value=mountinfo), \
                mock.patch.object(TARGET.MANAGEMENT, 'block_identity') as block_identity, \
                mock.patch.object(TARGET.MANAGEMENT, 'secure_path') as secure_path, \
                mock.patch.object(TARGET.MANAGEMENT, 'read_secure',
                                  side_effect=lambda path, _limit: secure_files[str(path)]) as read_secure, \
                mock.patch.object(TARGET.os, 'stat', return_value=SimpleNamespace(st_dev=123)), \
                mock.patch.object(TARGET.os, 'uname', return_value=SimpleNamespace(release=release)), \
                mock.patch.object(TARGET.INSTALL, 'inventory', return_value={'blockdevices': [self.disk]}), \
                mock.patch.object(TARGET.PLANNER, 'identify', side_effect=self.resolve), \
                mock.patch.object(TARGET.PLANNER, 'read_safety', side_effect=self.safety), \
                mock.patch.object(TARGET, 'sysfs_geometry') as sysfs_geometry, \
                mock.patch.object(TARGET, 'open_checked', side_effect=[70, 71, 72, 73]) as opened, \
                mock.patch.object(TARGET, 'gpt_context', return_value=gpt) as gpt_context, \
                mock.patch.object(TARGET.os, 'close') as closed:
            first = TARGET.snapshot('/dev/nvme0n1', 'fixture', 'unique', self.size, 'B')
            second = TARGET.snapshot('/dev/nvme0n1', 'fixture', 'unique', self.size, 'B')
            self.assertEqual(first, expected)
            self.assertEqual(second, expected)
            self.assertEqual(set(first), {'disk', 'target', 'active', 'state', 'gpt'})
            self.assertEqual(guard.call_args_list, [mock.call(), mock.call()])
            self.assertEqual(opened.call_args_list, [mock.call(disk), mock.call(target)] * 2)
            self.assertEqual(closed.call_args_list, [mock.call(71), mock.call(70),
                                                     mock.call(73), mock.call(72)])
            self.assertEqual(gpt_context.call_args_list, [mock.call(70, disk), mock.call(72, disk)])
            self.assertEqual(sysfs_geometry.call_args_list, [mock.call(nodes, geometry)] * 2)
            self.assertEqual(block_identity.call_count, 4)
            self.assertEqual(secure_path.call_args_list,
                             [mock.call(TARGET.BOOT, directory=True)] * 2)
            self.assertEqual(read_secure.call_count, 6)

    def test_only_inactive_slot_is_selected_with_busy_active_and_guests(self):
        disk, target, _, _ = self.policy()
        self.assertEqual(target['partition_number'], 3)
        self.assertEqual(target['size_bytes'], 4 * 1024**3)
        self.assertEqual(disk['serial'], 'unique')

    def test_active_parent_mismatch(self):
        self.active['root_source'] = '/dev/nvme1n1p2'
        with self.assertRaisesRegex(ValueError, 'derive'):
            self.policy()

    def test_mount_identity_mismatch(self):
        self.state['major_minor'] = '259:99'
        with self.assertRaisesRegex(ValueError, 'derive'):
            self.policy()

    def test_busy_inactive_mount_swap_holder_readonly(self):
        for key, value in [('mounted', True), ('swap', True), ('holders', ['dm-0']), ('read_only', True)]:
            original = self.safety
            def safety(nodes):
                result = original(nodes)
                result['259:3'][key] = value
                return result
            with mock.patch.object(self, 'safety', safety), self.assertRaisesRegex(ValueError, 'busy'):
                self.policy()

    def test_mapped_descendants(self):
        self.disk['children'][4]['children'].append({'type': 'crypt', 'maj:min': '253:0'})
        with self.assertRaisesRegex(ValueError, 'mapped'):
            self.policy()

    def test_duplicate_alias_inventory(self):
        inventory = {'blockdevices': [self.disk, copy.deepcopy(self.disk['children'][0])]}
        with self.assertRaisesRegex(ValueError, 'alias'):
            TARGET.inventory_policy(inventory, '/dev/nvme0n1', 'fixture', 'unique', self.size,
                                    'B', self.active, self.state, self.resolve, self.safety)

    def test_running_selector_and_immutable_marker(self):
        marker = json.dumps({'schema': 1, 'slot': 'A',
            'filesystem_uuid': TARGET.SELECT.INIT.PAYLOAD.SLOT_UUIDS['A'],
            'kernel_release': TARGET.SELECT.INIT.PAYLOAD.KERNEL_RELEASE}).encode()
        selector = {'SCHEMA': '1', 'SLOT': 'A',
            'ROOT_UUID': TARGET.SELECT.INIT.PAYLOAD.SLOT_UUIDS['A'],
            'ROOT_MARKER_SHA256': TARGET.digest(marker),
            'KERNEL_RELEASE': TARGET.SELECT.INIT.PAYLOAD.KERNEL_RELEASE}
        root = {'source': '/dev/nvme0n1p2', 'id': '259:2'}
        state = {'source': '/dev/nvme0n1p4', 'id': '259:4'}
        state_marker = f'a733-state-v1\n{uuid.uuid4()}\n'.encode()
        def run():
            return TARGET.running_context({}, marker, TARGET.SELECT.serialize(selector),
                                          state_marker, selector['KERNEL_RELEASE'])
        with mock.patch.object(TARGET.MANAGEMENT, 'validate_mounts', return_value=(root, state)):
            self.assertEqual(run()[0]['slot'], 'A')
            selector['ROOT_MARKER_SHA256'] = '0' * 64
            with self.assertRaisesRegex(ValueError, 'hash'):
                run()
            selector['SLOT'] = 'B'
            selector['ROOT_UUID'] = TARGET.SELECT.INIT.PAYLOAD.SLOT_UUIDS['B']
            with self.assertRaisesRegex(ValueError, 'reboot'):
                run()

    def test_4kn_kernel_geometry_uses_512_byte_units(self):
        # Divisible by 4096, unlike the nominal decimal capacity fixture.
        size = 250_000_003_072
        geometry = TARGET.LAYOUT.layout(size, 4096, 4096)
        nodes = [dict(self.disk)] + [dict(n) for n in self.disk['children']]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            parent = root / 'nvme0n1'
            parent.mkdir()
            for i, node in enumerate(nodes):
                base = parent if i == 0 else parent / node['kname']
                base.mkdir(exist_ok=True)
                (base / 'dev').write_text(node['maj:min'])
                (root / node['maj:min']).symlink_to(base)
                if i:
                    part = geometry['partitions'][i - 1]
                    (base / 'partition').write_text(str(i))
                    (base / 'start').write_text(str(part['start_bytes'] // 512))
                    (base / 'size').write_text(str(part['size_bytes'] // 512))
            TARGET.sysfs_geometry(nodes, geometry, root)
            (parent / nodes[3]['kname'] / 'start').write_text(str(geometry['partitions'][2]['start_lba']))
            with self.assertRaisesRegex(ValueError, 'geometry'):
                TARGET.sysfs_geometry(nodes, geometry, root)

    def test_actual_gpt_primary_backup_and_extra_entries(self):
        for logical, size in ((512, self.size), (4096, 250_000_003_072)):
            encoded = TARGET.LAYOUT.encode_gpt(size, logical, 4096)
            disk = {'size_bytes': size, 'logical_sector_bytes': logical, 'physical_sector_bytes': 4096}
            extents = {e['offset_bytes']: e['data'] for e in encoded['extents']}
            def reader(_fd, length, offset):
                return extents[offset][:length]
            context = TARGET.gpt_context(None, disk, reader)
            self.assertEqual(context['partition_guids'], encoded['partition_guids'])
            self.assertEqual(context, TARGET.gpt_context(None, disk, reader))
            for extent in encoded['extents']:
                original = extents[extent['offset_bytes']]
                damaged = bytearray(original)
                damaged[-1] ^= 1
                extents[extent['offset_bytes']] = bytes(damaged)
                with self.assertRaises(ValueError):
                    TARGET.gpt_context(None, disk, reader)
                extents[extent['offset_bytes']] = original
            # A valid GPT from a different capacity is still stale geometry.
            stale = TARGET.LAYOUT.encode_gpt(size - logical, logical, 4096,
                encoded['disk_guid'], encoded['partition_guids'])
            extents[logical] = stale['extents'][1]['data']
            with self.assertRaisesRegex(ValueError, 'GPT'):
                TARGET.gpt_context(None, disk, reader)

    def test_checked_open_is_readonly_nonexclusive_and_closes_on_ioctl_failure(self):
        info = SimpleNamespace(st_mode=stat.S_IFBLK | 0o600, st_uid=0,
                               st_dev=1, st_ino=2, st_rdev=os.makedev(259, 0))
        record = {'canonical_path': '/dev/nvme0n1'}
        with mock.patch.object(Path, 'lstat', return_value=info), \
                mock.patch.object(TARGET.os, 'open', return_value=99) as opened, \
                mock.patch.object(TARGET.os, 'fstat', return_value=info), \
                mock.patch.object(TARGET.os, 'close') as closed, \
                mock.patch.object(TARGET.INSTALL, 'validate_fd', side_effect=ValueError('ioctl')):
            with self.assertRaisesRegex(ValueError, 'ioctl'):
                TARGET.open_checked(record)
            flags = opened.call_args.args[1]
            self.assertEqual(flags & os.O_ACCMODE, os.O_RDONLY)
            self.assertTrue(flags & os.O_NOFOLLOW)
            self.assertTrue(flags & os.O_NONBLOCK)
            self.assertFalse(flags & os.O_EXCL)
            closed.assert_called_once_with(99)

    def test_snapshot_always_starts_with_live_runtime_guard(self):
        with mock.patch.object(TARGET.MANAGEMENT, 'runtime_guard', side_effect=ValueError('live guard')) as guard:
            with self.assertRaisesRegex(ValueError, 'live guard'):
                TARGET.snapshot('/dev/nvme0n1', 'fixture', 'unique', self.size, 'B')
            guard.assert_called_once_with()

    def selection_fixture(self, running, selected, marker_hash=None):
        """Mock external reads, retaining actual context and inventory policies."""
        number = 2 if running == 'A' else 3
        self.active = {'slot': running, 'root_source': f'/dev/nvme0n1p{number}',
                       'root_major_minor': f'259:{number}'}
        for i in (2, 3):
            self.disk['children'][i - 1]['mountpoints'] = ['/'] if i == number else []
        release = TARGET.SELECT.INIT.PAYLOAD.KERNEL_RELEASE
        marker = json.dumps({'schema': 1, 'slot': running,
            'filesystem_uuid': TARGET.SELECT.INIT.PAYLOAD.SLOT_UUIDS[running],
            'kernel_release': release}).encode()
        selector = {'SCHEMA': '1', 'SLOT': selected,
            'ROOT_UUID': TARGET.SELECT.INIT.PAYLOAD.SLOT_UUIDS[selected],
            'ROOT_MARKER_SHA256': marker_hash or (TARGET.digest(marker) if selected == running else 'f' * 64),
            'KERNEL_RELEASE': release}
        files = {str(TARGET.MARKER): marker,
                 str(TARGET.BOOT / 'selected.conf'): TARGET.SELECT.serialize(selector),
                 '/run/a733-state/.a733-state': b'a733-state-v1\n89d3d789-f8c5-4cb7-908b-d0b1538c789a\n'}
        stack = ExitStack()
        self.addCleanup(stack.close)
        guard = stack.enter_context(mock.patch.object(TARGET.MANAGEMENT, 'runtime_guard'))
        stack.enter_context(mock.patch.object(Path, 'read_text', return_value=''))
        stack.enter_context(mock.patch.object(TARGET.MANAGEMENT, 'mount_table', return_value={}))
        stack.enter_context(mock.patch.object(TARGET.MANAGEMENT, 'validate_mounts', return_value=(
            {'source': self.active['root_source'], 'id': self.active['root_major_minor']},
            {'source': self.state['source'], 'id': self.state['major_minor']})))
        stack.enter_context(mock.patch.object(TARGET.MANAGEMENT, 'block_identity'))
        stack.enter_context(mock.patch.object(TARGET.MANAGEMENT, 'secure_path'))
        reads = stack.enter_context(mock.patch.object(TARGET.MANAGEMENT, 'read_secure',
            side_effect=lambda path, _limit: files[str(path)]))
        stack.enter_context(mock.patch.object(TARGET.os, 'stat', return_value=SimpleNamespace(st_dev=123)))
        stack.enter_context(mock.patch.object(TARGET.os, 'uname', return_value=SimpleNamespace(release=release)))
        stack.enter_context(mock.patch.object(TARGET.INSTALL, 'inventory', return_value={'blockdevices': [self.disk]}))
        stack.enter_context(mock.patch.object(TARGET.PLANNER, 'identify', side_effect=self.resolve))
        stack.enter_context(mock.patch.object(TARGET.PLANNER, 'read_safety', side_effect=self.safety))
        stack.enter_context(mock.patch.object(TARGET, 'sysfs_geometry'))
        opened = stack.enter_context(mock.patch.object(TARGET, 'open_checked', return_value=70))
        stack.enter_context(mock.patch.object(TARGET, 'gpt_context', return_value={
            'disk_guid': str(uuid.UUID(int=6)),
            'partition_guids': [str(uuid.UUID(int=i)) for i in range(1, 6)], 'sha256': 'a' * 64}))
        stack.enter_context(mock.patch.object(TARGET.os, 'close'))
        return stack, guard, reads, opened, selector

    def test_activation_and_cancellation_complete_slot_matrix(self):
        for running in ('A', 'B'):
            for selected in ('A', 'B'):
                for slot in ('A', 'B'):
                    for intent in ('activate', 'cancel-pending'):
                        for published in (False, True):
                            with self.subTest(running=running, selected=selected, slot=slot,
                                              intent=intent, published=published):
                                stack, guard, reads, opened, selector = self.selection_fixture(running, selected)
                                valid = (slot != running and selected == (slot if published else running)
                                         if intent == 'activate' else
                                         slot == running and (selected == running if published else selected != running))
                                with stack:
                                    def snapshot():
                                        return TARGET.activation_snapshot('/dev/nvme0n1', 'fixture',
                                            'unique', self.size, slot, intent, published)
                                    if not valid:
                                        with self.assertRaises(ValueError):
                                            snapshot()
                                        opened.assert_not_called()
                                    else:
                                        result = snapshot()
                                        self.assertEqual(set(result), {'disk', 'target', 'active', 'state', 'gpt'})
                                        self.assertEqual(result['state']['selector'], selector)
                                        self.assertEqual(result['state']['selector_sha256'],
                                            TARGET.digest(TARGET.SELECT.serialize(selector)))
                                        self.assertEqual(result['active']['slot'], running)
                                        self.assertEqual(result['target']['partition_number'], 2 if slot == 'A' else 3)
                                        self.assertEqual(result['target']['size_bytes'], 4 * 1024**3)
                                        self.assertEqual(reads.call_count, 3)
                                    guard.assert_called_once_with()

    def test_activation_arguments_are_strict(self):
        for intent, published in (('stage', False), ('', False), (None, False),
                                  ('activate', 0), ('activate', 1), ('activate', None),
                                  ('cancel-pending', 'false')):
            with self.subTest(intent=intent, published=published), \
                    mock.patch.object(TARGET.MANAGEMENT, 'runtime_guard') as guard:
                with self.assertRaises(ValueError):
                    TARGET.activation_snapshot('/dev/nvme0n1', 'fixture', 'unique',
                                               self.size, 'B', intent, published)
                guard.assert_not_called()

    def test_activation_keeps_current_marker_binding(self):
        for intent, published, slot in (('activate', False, 'B'), ('cancel-pending', True, 'A')):
            with self.subTest(intent=intent), self.selection_fixture('A', 'A', '0' * 64)[0]:
                with self.assertRaisesRegex(ValueError, 'marker hash'):
                    TARGET.activation_snapshot('/dev/nvme0n1', 'fixture', 'unique',
                                               self.size, slot, intent, published)

    def test_pending_selector_still_requires_full_parse(self):
        marker = json.dumps({'schema': 1, 'slot': 'A',
            'filesystem_uuid': TARGET.SELECT.INIT.PAYLOAD.SLOT_UUIDS['A'],
            'kernel_release': TARGET.SELECT.INIT.PAYLOAD.KERNEL_RELEASE}).encode()
        with mock.patch.object(TARGET.MANAGEMENT, 'validate_mounts', return_value=(
                {'source': '/dev/nvme0n1p2', 'id': '259:2'},
                {'source': '/dev/nvme0n1p4', 'id': '259:4'})):
            with self.assertRaises(ValueError):
                TARGET.running_context({}, marker, b'SLOT=B\n', b'',
                                       TARGET.SELECT.INIT.PAYLOAD.KERNEL_RELEASE, 'pending')

    def test_running_target_allows_mounts_but_refuses_other_busy_flags(self):
        def policy(kind='running', slot='A'):
            return TARGET.inventory_policy({'blockdevices': [self.disk]}, '/dev/nvme0n1',
                'fixture', 'unique', self.size, slot, self.active, self.state,
                self.resolve, self.safety, target_kind=kind)
        self.assertEqual(policy()[1]['partition_number'], 2)
        with self.assertRaisesRegex(ValueError, 'inactive'):
            policy('inactive')
        with self.assertRaisesRegex(ValueError, 'running'):
            policy('running', 'B')
        for identity in ('259:0', '259:2'):
            for key, value in (('holders', ['dm-0']), ('read_only', True), ('swap', True)):
                with self.subTest(identity=identity, key=key):
                    original = self.safety
                    def safety(nodes):
                        flags = original(nodes)
                        flags[identity][key] = value
                        return flags
                    with mock.patch.object(self, 'safety', safety), self.assertRaisesRegex(ValueError, 'busy'):
                        policy()
        for value in (True, None, '0'):
            with mock.patch.dict(self.disk['children'][1], ro=value), self.assertRaisesRegex(ValueError, 'unknown'):
                policy()
        self.disk['mountpoints'] = ['/unexpected']
        with self.assertRaisesRegex(ValueError, 'mounted'):
            policy()

    def test_activation_runtime_guard_cannot_be_bypassed_with_pending_selector(self):
        for intent, published in (('activate', False), ('activate', True),
                                  ('cancel-pending', False), ('cancel-pending', True)):
            with mock.patch.object(TARGET.MANAGEMENT, 'runtime_guard', side_effect=ValueError('live guard')):
                with self.assertRaisesRegex(ValueError, 'live guard'):
                    TARGET.activation_snapshot('/dev/nvme0n1', 'fixture', 'unique',
                                               self.size, 'B', intent, published)


if __name__ == '__main__':
    unittest.main()
