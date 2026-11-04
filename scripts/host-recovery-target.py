#!/usr/bin/env python3
"""Read-only offline recovery policy, restricted to the fixed A733 RAM runtime.

Snapshots are identity evidence, never authorization or exclusive device claims.
The trusted source checkout is required; installed management is not consulted.
"""
import os
from pathlib import Path
import re
import struct
import uuid
import importlib.util

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('recovery_live_policy', REPO / 'scripts/host-update-target.py')
LIVE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LIVE)
INSTALL, PLANNER, LAYOUT, MANAGEMENT = LIVE.INSTALL, LIVE.PLANNER, LIVE.LAYOUT, LIVE.MANAGEMENT
STATE_MOUNT = '/run/a733-recovery-state'
require = LIVE.require


def environment_guard():
    """Reject installed/disk hosts; return current mount evidence for source checks."""
    require(os.geteuid() == 0, 'root required')
    running = os.uname()
    require(running.machine == 'aarch64' and running.release == '6.6.98-a733-diag',
            'fixed A733 preparation kernel required')
    table = MANAGEMENT.mount_table(Path('/proc/self/mountinfo').read_text())
    for path, kinds in (('/', ('rootfs', 'tmpfs', 'ramfs')), ('/run', ('tmpfs',))):
        rows = table.get(path, [])
        require(len(rows) == 1, 'missing/ambiguous RAM mount')
        row = rows[0]
        require(row['type'] in kinds and row['root'] == '/' and
                'rw' in row['flags'] and 'ro' not in row['flags'] and
                'rw' in row['super'] and 'ro' not in row['super'], 'RAM preparation mount required')
        device = os.stat(path).st_dev
        require(f'{os.major(device)}:{os.minor(device)}' == row['id'],
                'RAM mount identity differs')
    swaps = Path('/proc/swaps').read_text().splitlines()
    require(len(swaps) == 1 and swaps[0].startswith('Filename'), 'active or unknown swap refused')
    return table


def state_superblock(fd, expected_uuid, reader=None):
    """Read exactly the ext4 superblock through a held read-only device FD."""
    reader = os.pread if reader is None else reader
    identity = uuid.UUID(expected_uuid)
    require(str(identity) == expected_uuid and identity.int != 0, 'canonical nonzero state UUID required')
    data = reader(fd, 1024, 1024)
    require(len(data) == 1024, 'short state superblock')
    u32 = lambda offset: struct.unpack_from('<I', data, offset)[0]
    require(struct.unpack_from('<H', data, 0x38)[0] == 0xef53 and u32(0x5c) & 4,
            'journaled ext4 state required')
    require(uuid.UUID(bytes=data[0x68:0x78]) == identity, 'state filesystem UUID differs')
    # The pinned state recipe creates inode 8 on this filesystem. Never let a
    # recovery mount discover an external journal on another physical device.
    require(u32(0xe0) == 8 and u32(0xe4) == 0 and data[0xd0:0xe0] == bytes(16)
            and not u32(0x60) & 0x8, 'fixed internal ext4 journal required')
    shift = u32(0x18)
    require(shift <= 6, 'invalid ext4 block size')
    blocks = u32(4)
    if u32(0x60) & 0x80:
        blocks |= u32(0x150) << 32
    else:
        require(u32(0x150) == 0, 'unexpected high block count')
    require(blocks * (1024 << shift) == 8 * LAYOUT.GIB, 'state filesystem size differs')
    return str(identity)


def mounted_state_policy(table, node, mounted_state):
    """Validate the sole owned state mount before whitelisting its busy evidence."""
    rows = [(path, row) for path, items in table.items() for row in items
            if row['id'] == node['maj:min']]
    if not mounted_state:
        require(not rows, 'state is mounted')
        return
    require(len(rows) == 1 and rows[0][0] == STATE_MOUNT and
            len(table.get(STATE_MOUNT, [])) == 1, 'state mount is absent or duplicated')
    row = rows[0][1]
    require(row['source'] == '/dev/' + node['kname'] and row['type'] == 'ext4' and
            row['root'] == '/' and {'rw', 'nodev', 'nosuid'} <= row['flags'] and
            'ro' not in row['flags'] and 'rw' in row['super'] and 'ro' not in row['super'],
            'unsafe recovery state mount')
    info = os.stat(STATE_MOUNT)
    require(f'{os.major(info.st_dev)}:{os.minor(info.st_dev)}' == node['maj:min'],
            'state mount device differs')


def inventory_policy(inventory, requested, expected_model, expected_serial, expected_bytes,
                     slot, state_uuid, table, mounted_state, resolver, safety_reader):
    require(slot in ('A', 'B'), 'invalid recovery slot')
    require(type(mounted_state) is bool, 'mounted_state must be boolean')
    for value in (expected_model, expected_serial):
        require(isinstance(value, str) and value and value.strip() == value,
                'explicit nonempty model and serial required')
    require(type(expected_bytes) is int and expected_bytes > 0, 'explicit byte capacity required')
    identity = uuid.UUID(state_uuid)
    require(str(identity) == state_uuid and identity.int != 0, 'canonical nonzero state UUID required')
    canonical, major_minor = resolver(requested)
    all_nodes = list(PLANNER.flatten(inventory['blockdevices']))
    matches = [n for n in all_nodes if n.get('maj:min') == major_minor]
    require(len(matches) == 1, 'target absent or aliased in inventory')
    disk = matches[0]
    name = disk.get('kname', '')
    require(disk.get('type') == 'disk' and disk.get('tran') == 'nvme' and
            re.fullmatch(r'nvme[0-9]+n[0-9]+', name) and canonical == '/dev/' + name,
            'whole canonical NVMe namespace required')
    model, serial = (str(disk.get(key) or '').strip() for key in ('model', 'serial'))
    require((model, serial, disk.get('size')) == (expected_model, expected_serial, expected_bytes),
            'explicit NVMe identity differs')
    logical, physical = disk.get('log-sec'), disk.get('phy-sec')
    geometry = LAYOUT.layout(expected_bytes, logical, physical)
    children = disk.get('children', [])
    require(len(children) == 5 and len(list(PLANNER.flatten([disk]))) == 6 and
            {n.get('kname') for n in children} == {name + 'p' + str(i) for i in range(1, 6)},
            'exact five direct partitions required')
    nodes = [disk] + sorted(children, key=lambda n: n['kname'])
    require(len({n.get('maj:min') for n in nodes}) == 6, 'duplicate partition identity')
    for node in nodes:
        require(sum(n.get('maj:min') == node['maj:min'] for n in all_nodes) == 1,
                'duplicate inventory alias')
        require(resolver(node['path']) == ('/dev/' + node['kname'], node['maj:min']),
                'device path identity differs')
    for node, part in zip(nodes[1:], geometry['partitions']):
        require(node.get('type') == 'part' and node.get('size') == part['size_bytes'] and
                node.get('log-sec') == logical and node.get('phy-sec') == physical,
                'partition geometry differs')
    mounted_state_policy(table, nodes[4], mounted_state)
    evidence = safety_reader(nodes)
    for index, node in enumerate(nodes):
        allowed = index == 4 and mounted_state
        require(node.get('ro') is False or type(node.get('ro')) is int and node['ro'] == 0,
                'read-only or unknown device state')
        mounts = [m for m in (node.get('mountpoints') or []) if m]
        require(mounts == [STATE_MOUNT] if allowed else not mounts, 'unexpected inventory mount')
        flags = evidence[node['maj:min']]
        require(not any(flags[key] for key in ('holders', 'read_only', 'swap')),
                'device is busy or read-only')
        require(flags['mounted'] is True if allowed else not flags['mounted'],
                'unexpected kernel mount evidence')
        actual = [row for items in table.values() for row in items if row['id'] == node['maj:min']]
        require(len(actual) == (1 if allowed else 0), 'unexpected device mount')
    record = {'requested_path': str(requested), 'canonical_path': canonical,
              'major_minor': major_minor, 'model': model, 'serial': serial,
              'size_bytes': expected_bytes, 'logical_sector_bytes': logical,
              'physical_sector_bytes': physical}
    def partition(number):
        path = f'{canonical}p{number}'
        return dict(record, requested_path=path, canonical_path=path,
                    major_minor=nodes[number]['maj:min'], partition_number=number,
                    size_bytes=geometry['partitions'][number - 1]['size_bytes'])
    return record, partition(2 if slot == 'A' else 3), dict(
        partition(4), uuid=state_uuid, mountpoint=STATE_MOUNT), nodes, geometry


def snapshot(target_path, expected_model, expected_serial, expected_bytes, state_uuid, slot,
             mounted_state=False):
    table = environment_guard()
    disk, target, state, nodes, geometry = inventory_policy(
        INSTALL.inventory(), str(target_path), expected_model, expected_serial, expected_bytes,
        slot, state_uuid, table, mounted_state, PLANNER.identify, PLANNER.read_safety)
    LIVE.sysfs_geometry(nodes, geometry)
    disk_fd = LIVE.open_checked(disk)
    try:
        gpt = LIVE.gpt_context(disk_fd, disk)
        for number, node in enumerate(nodes[1:], 1):
            record = dict(disk, requested_path=node['path'], canonical_path=node['path'],
                          major_minor=node['maj:min'], size_bytes=node['size'])
            fd = LIVE.open_checked(record)
            try:
                if number == 4:
                    state_superblock(fd, state_uuid)
            finally:
                os.close(fd)
        for record in (target, state):
            record['partition_uuid'] = gpt['partition_guids'][record['partition_number'] - 1]
    finally:
        os.close(disk_fd)
    return {'disk': disk, 'target': target, 'state': state, 'gpt': gpt}
