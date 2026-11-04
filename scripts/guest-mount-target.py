#!/usr/bin/env python3
"""Read-only installed-host identity policy for explicit A733 p5 activation.

Snapshots neither lock nor authorize a mount. Production evidence comes only
from the fixed live proc/sys paths; fixture injection lives in pure policies.
"""
import os
from pathlib import Path
import struct
import uuid
import importlib.util

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('guest_mount_live', REPO / 'scripts/host-update-target.py')
LIVE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LIVE)
MANAGEMENT, INSTALL, PLANNER = LIVE.MANAGEMENT, LIVE.INSTALL, LIVE.PLANNER
GUEST_MOUNT = '/run/a733-guests'
require = LIVE.require


def guest_superblock(fd, expected_uuid, expected_bytes, reader=None):
    """Check a full-partition ext4 filesystem with the recipe's internal journal."""
    reader = os.pread if reader is None else reader
    require(isinstance(expected_uuid, str), 'canonical nonzero guest UUID required')
    identity = uuid.UUID(expected_uuid)
    require(str(identity) == expected_uuid and identity.int != 0,
            'canonical nonzero guest UUID required')
    require(type(expected_bytes) is int and expected_bytes > 0, 'positive guest capacity required')
    data = reader(fd, 1024, 1024)
    require(len(data) == 1024, 'short guest superblock')
    u32 = lambda offset: struct.unpack_from('<I', data, offset)[0]
    require(struct.unpack_from('<H', data, 0x38)[0] == 0xef53 and u32(0x5c) & 4,
            'journaled ext4 guest filesystem required')
    require(uuid.UUID(bytes=data[0x68:0x78]) == identity, 'guest filesystem UUID differs')
    require(u32(0xe0) == 8 and u32(0xe4) == 0 and data[0xd0:0xe0] == bytes(16)
            and not u32(0x60) & 0x8, 'fixed internal ext4 journal required')
    shift = u32(0x18)
    require(shift <= 6, 'invalid ext4 block size')
    blocks = u32(4)
    if u32(0x60) & 0x80:
        blocks |= u32(0x150) << 32
    else:
        require(u32(0x150) == 0, 'unexpected high block count')
    require(blocks * (1024 << shift) == expected_bytes, 'guest filesystem size differs')
    return str(identity)


def mounted_guest_policy(table, node, mounted, stat_reader=None):
    """Whitelist exactly one owned full-filesystem mount, without any aliases."""
    require(type(mounted) is bool, 'mounted must be boolean')
    rows = [(path, row) for path, items in table.items() for row in items
            if row['id'] == node['maj:min']]
    require(not any(path.startswith(GUEST_MOUNT + '/') for path in table),
            'nested guest mount refused')
    if not mounted:
        require(not rows and GUEST_MOUNT not in table, 'guest mount already present')
        return
    require(len(rows) == 1 and rows[0][0] == GUEST_MOUNT and
            len(table.get(GUEST_MOUNT, [])) == 1, 'guest mount absent, foreign or duplicated')
    row = rows[0][1]
    require(row['source'] == '/dev/' + node['kname'] and row['type'] == 'ext4' and
            row['root'] == '/' and {'rw', 'nodev', 'nosuid', 'noexec'} <= row['flags'] and
            'ro' not in row['flags'] and 'rw' in row['super'] and 'ro' not in row['super'],
            'unsafe guest mount')
    info = (os.stat if stat_reader is None else stat_reader)(GUEST_MOUNT)
    require(f'{os.major(info.st_dev)}:{os.minor(info.st_dev)}' == node['maj:min'],
            'guest mount device differs')


def inventory_policy(inventory, requested, expected_model, expected_serial, expected_bytes,
                     guest_uuid, active, state, table, mounted, resolver, safety_reader,
                     stat_reader=None):
    """Reuse full live-host identity/layout checks and add p5 mount safety."""
    require(type(mounted) is bool, 'mounted must be boolean')
    require(isinstance(guest_uuid, str), 'canonical nonzero guest UUID required')
    identity = uuid.UUID(guest_uuid)
    require(str(identity) == guest_uuid and identity.int != 0,
            'canonical nonzero guest UUID required')
    evidence = {}

    def capture_safety(nodes):
        evidence.update(safety_reader(nodes))
        return evidence

    disk, _, nodes, geometry = LIVE.inventory_policy(
        inventory, requested, expected_model, expected_serial, expected_bytes,
        active['slot'], active, state, resolver, capture_safety, target_kind='running')
    node = nodes[5]
    mounted_guest_policy(table, node, mounted, stat_reader)
    for current in (nodes[0], node):
        require(current.get('ro') is False or type(current.get('ro')) is int and current['ro'] == 0,
                'read-only or unknown guest device state')
        flags = evidence[current['maj:min']]
        require(not any(flags[key] for key in ('holders', 'read_only', 'swap')),
                'guest device busy or read-only')
        allowed = current is node and mounted
        mounts = [value for value in (current.get('mountpoints') or []) if value]
        require(mounts == [GUEST_MOUNT] if allowed else not mounts,
                'unexpected inventory guest mount')
        require(flags['mounted'] is allowed, 'unexpected kernel guest mount evidence')
        actual = [row for items in table.values() for row in items
                  if row['id'] == current['maj:min']]
        require(len(actual) == (1 if allowed else 0), 'unexpected guest device mount')
    path = disk['canonical_path'] + 'p5'
    guest = dict(disk, requested_path=path, canonical_path=path, major_minor=node['maj:min'],
                 size_bytes=geometry['partitions'][4]['size_bytes'], partition_number=5,
                 uuid=guest_uuid, mountpoint=GUEST_MOUNT)
    return disk, guest, nodes, geometry


def snapshot(target, expected_model, expected_serial, expected_bytes, guest_uuid, mounted=False):
    """Validate selected host, entire GPT and held read-only p5 superblock."""
    MANAGEMENT.runtime_guard()
    table = MANAGEMENT.mount_table(Path('/proc/self/mountinfo').read_text())
    root_row, state_row = MANAGEMENT.validate_mounts(table)
    MANAGEMENT.block_identity(root_row)
    MANAGEMENT.block_identity(state_row)
    MANAGEMENT.secure_path(LIVE.BOOT, directory=True)
    require(os.stat(LIVE.BOOT).st_dev == os.stat('/run/a733-state').st_dev,
            'boot directory is not on state')
    active, state = LIVE.running_context(
        table, MANAGEMENT.read_secure(LIVE.MARKER, 1024**2),
        MANAGEMENT.read_secure(LIVE.BOOT / 'selected.conf', 4096),
        MANAGEMENT.read_secure('/run/a733-state/.a733-state', 128),
        os.uname().release, selection_mode='pending')
    disk, guest, nodes, geometry = inventory_policy(
        INSTALL.inventory(), str(target), expected_model, expected_serial, expected_bytes,
        guest_uuid, active, state, table, mounted, PLANNER.identify, PLANNER.read_safety)
    LIVE.sysfs_geometry(nodes, geometry)
    disk_fd = LIVE.open_checked(disk)
    try:
        gpt = LIVE.gpt_context(disk_fd, disk)
        guest['partition_uuid'] = gpt['partition_guids'][4]
        guest_fd = LIVE.open_checked(guest)
        try:
            guest_superblock(guest_fd, guest_uuid, guest['size_bytes'])
        finally:
            os.close(guest_fd)
    finally:
        os.close(disk_fd)
    return {'disk': disk, 'guest': guest, 'active': active, 'state': state, 'gpt': gpt}
