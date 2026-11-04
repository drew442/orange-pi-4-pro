#!/usr/bin/env python3
"""Read-only live installed-host policy for A733 root operations.

No CLI, configurable runtime paths, filesystem probing of the inactive slot,
mounting, or authorization. A snapshot is evidence, never a device lock.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import uuid

REPO = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


MANAGEMENT = module('update_target_management', REPO / 'configs/host-management.py')
INSTALL = module('update_target_install', REPO / 'scripts/install-nvme.py')
SELECT = module('update_target_select', REPO / 'build/root-selection.py')
LAYOUT = INSTALL.LAYOUT
PLANNER = INSTALL.PLANNER
BOOT = Path('/run/a733-state/boot')
MARKER = Path('/usr/lib/orangepi-hypervisor/payload.json')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def running_context(table, marker, selector_data, state_marker, release, selection_mode='current'):
    """Validate current root, state, marker and selection as one policy unit."""
    require(selection_mode in ('current', 'pending'), 'invalid selection mode')
    root, state = MANAGEMENT.validate_mounts(table)
    slot = 'A' if root['source'].endswith('p2') else 'B'
    payload = json.loads(marker, object_pairs_hook=INSTALL.unique_pairs)
    selector = SELECT.parse(selector_data)
    require(payload.get('schema') == 1 and payload.get('slot') == slot and
            payload.get('filesystem_uuid') == SELECT.INIT.PAYLOAD.SLOT_UUIDS[slot] and
            payload.get('kernel_release') == release == SELECT.INIT.PAYLOAD.KERNEL_RELEASE,
            'mounted root payload or current kernel differs')
    if selection_mode == 'current':
        require(selector['SLOT'] == slot, 'selected slot differs from running root; reboot required')
    if selector['SLOT'] == slot:
        require(selector['ROOT_MARKER_SHA256'] == digest(marker), 'selected root marker hash differs')
    lines = state_marker.decode('ascii').splitlines()
    require(len(lines) == 2 and lines[0] == 'a733-state-v1', 'invalid state marker')
    identity = str(uuid.UUID(lines[1]))
    require(uuid.UUID(identity).int != 0 and state_marker == f'a733-state-v1\n{identity}\n'.encode(),
            'invalid state UUID')
    return ({'slot': slot, 'root_source': root['source'], 'root_major_minor': root['id'],
             'marker_sha256': digest(marker)},
            {'source': state['source'], 'major_minor': state['id'], 'uuid': identity,
             'boot_directory': str(BOOT), 'selector': selector,
             'selector_sha256': digest(selector_data)})


def inventory_policy(inventory, requested, expected_model, expected_serial, expected_bytes,
                     slot, active, state, resolver, safety_reader, target_kind='inactive'):
    require(target_kind in ('inactive', 'running'), 'invalid target kind')
    require(slot in ('A', 'B') and (slot != active['slot'] if target_kind == 'inactive'
                                  else slot == active['slot']),
            'requested slot must be ' + target_kind)
    require(isinstance(expected_model, str) and expected_model.strip() == expected_model and expected_model,
            'explicit nonempty model required')
    require(isinstance(expected_serial, str) and expected_serial.strip() == expected_serial and expected_serial,
            'explicit nonempty serial required')
    require(type(expected_bytes) is int and expected_bytes > 0, 'explicit byte capacity required')
    canonical, major_minor = resolver(requested)
    all_nodes = list(PLANNER.flatten(inventory['blockdevices']))
    matches = [n for n in all_nodes if n.get('maj:min') == major_minor]
    require(len(matches) == 1, 'target absent or aliased in inventory')
    disk = matches[0]
    name = disk.get('kname', '')
    require(disk.get('type') == 'disk' and disk.get('tran') == 'nvme' and
            re.fullmatch(r'nvme[0-9]+n[0-9]+', name) and canonical == '/dev/' + name,
            'whole canonical NVMe namespace required')
    require(resolver(disk['path']) == (canonical, major_minor), 'disk inventory identity differs')
    model, serial = (str(disk.get(key) or '').strip() for key in ('model', 'serial'))
    require((model, serial, disk.get('size')) == (expected_model, expected_serial, expected_bytes),
            'explicit NVMe identity differs')
    logical, physical = disk.get('log-sec'), disk.get('phy-sec')
    geometry = LAYOUT.layout(expected_bytes, logical, physical)
    children = disk.get('children', [])
    require(len(children) == 5 and len(list(PLANNER.flatten([disk]))) == 6,
            'exact five direct partitions required; mapped descendants refused')
    require({n.get('kname') for n in children} == {name + 'p' + str(i) for i in range(1, 6)},
            'unexpected partition names')
    nodes = [disk] + sorted(children, key=lambda n: n['kname'])
    require(len({n.get('maj:min') for n in nodes}) == 6, 'duplicate partition identity')
    for node in nodes:
        require(sum(n.get('maj:min') == node['maj:min'] for n in all_nodes) == 1,
                'duplicate inventory alias')
        require(resolver(node['path']) == ('/dev/' + node['kname'], node['maj:min']),
                'partition path identity differs')
    for node, part in zip(nodes[1:], geometry['partitions']):
        require(node.get('type') == 'part' and node.get('size') == part['size_bytes'] and
                node.get('log-sec') == logical and node.get('phy-sec') == physical,
                'partition geometry differs')
    active_number = 2 if active['slot'] == 'A' else 3
    require(active['root_source'] == f'{canonical}p{active_number}' and
            active['root_major_minor'] == nodes[active_number]['maj:min'] and
            state['source'] == canonical + 'p4' and state['major_minor'] == nodes[4]['maj:min'],
            'mounted root/state do not derive from explicit target')
    target_number = 2 if slot == 'A' else 3
    evidence = safety_reader(nodes)
    for node in (disk, nodes[target_number]):
        mounted_running = target_kind == 'running' and node is nodes[target_number]
        require(node.get('ro') is False or type(node.get('ro')) is int and node['ro'] == 0,
                'read-only or unknown target state')
        require(mounted_running or not any(node.get('mountpoints') or []), 'target is mounted')
        flags = evidence[node['maj:min']]
        keys = ('holders', 'read_only', 'swap') if mounted_running else (
            'holders', 'read_only', 'mounted', 'swap')
        require(not any(flags[key] for key in keys),
                'target is busy or read-only')
    record = {'requested_path': str(requested), 'canonical_path': canonical,
              'major_minor': major_minor, 'model': model, 'serial': serial,
              'size_bytes': expected_bytes, 'logical_sector_bytes': logical,
              'physical_sector_bytes': physical}
    target = dict(record, requested_path=f'{canonical}p{target_number}',
                  canonical_path=f'{canonical}p{target_number}',
                  major_minor=nodes[target_number]['maj:min'],
                  size_bytes=geometry['partitions'][target_number - 1]['size_bytes'],
                  partition_number=target_number)
    return record, target, nodes, geometry


def sysfs_geometry(nodes, geometry, sysroot=Path('/sys/dev/block')):
    """Kernel partition start/size are in 512-byte units, even on 4Kn."""
    parent = (sysroot / nodes[0]['maj:min']).resolve(strict=True)
    require(parent.name == nodes[0]['kname'] and not (parent / 'partition').exists(),
            'unexpected whole-device sysfs node')
    for index, node in enumerate(nodes):
        base = (sysroot / node['maj:min']).resolve(strict=True)
        require(base.name == node['kname'] and (base / 'dev').read_text().strip() == node['maj:min'],
                'sysfs identity differs')
        require(not (base / 'removable').exists() or (base / 'removable').read_text().strip() == '0',
                'removable device refused')
        if index:
            part = geometry['partitions'][index - 1]
            require(base.parent == parent and (base / 'partition').read_text().strip() == str(index),
                    'partition parent/number differs')
            require(int((base / 'start').read_text()) * 512 == part['start_bytes'] and
                    int((base / 'size').read_text()) * 512 == part['size_bytes'],
                    'kernel partition geometry differs')


def gpt_context(fd, disk, reader=os.pread):
    logical = disk['logical_sector_bytes']
    header = reader(fd, logical, logical)
    entries = reader(fd, 5 * 128, 2 * logical)
    require(len(header) == logical and len(entries) == 5 * 128, 'short GPT identity read')
    disk_guid = str(uuid.UUID(bytes_le=header[56:72]))
    partition_guids = [str(uuid.UUID(bytes_le=entries[i * 128 + 16:i * 128 + 32])) for i in range(5)]
    encoded = LAYOUT.encode_gpt(disk['size_bytes'], logical, disk['physical_sector_bytes'],
                                disk_guid, partition_guids)
    records = []
    for extent in encoded['extents']:
        data = reader(fd, len(extent['data']), extent['offset_bytes'])
        require(data == extent['data'], 'actual primary/backup GPT differs from fixed layout')
        records.append({'name': extent['name'], 'offset_bytes': extent['offset_bytes'],
                        'bytes': len(data), 'sha256': digest(data)})
    return {'disk_guid': disk_guid, 'partition_guids': partition_guids,
            'sha256': digest(INSTALL.canonical_json(records))}


def open_checked(record):
    path = Path(record['canonical_path'])
    before = path.lstat()
    require(stat.S_ISBLK(before.st_mode) and before.st_uid == 0, 'root-owned real block node required')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        after = os.fstat(fd)
        require((before.st_dev, before.st_ino, before.st_rdev, before.st_uid) ==
                (after.st_dev, after.st_ino, after.st_rdev, after.st_uid), 'node changed during open')
        INSTALL.validate_fd(fd, record)
        return fd
    except BaseException:
        os.close(fd)
        raise


def snapshot(target_path, expected_model, expected_serial, expected_bytes, slot):
    """Staging requires the selected running root and an idle inactive target."""
    return _snapshot(target_path, expected_model, expected_serial, expected_bytes, slot)


def activation_snapshot(target_path, expected_model, expected_serial, expected_bytes,
                        slot, intent, published=False):
    """Read live evidence before or after explicit selector publication.

    ``published`` is an internal postcondition check, never staging authority.
    A pending selector is parsed, but does not establish the other root's health.
    """
    require(intent in ('activate', 'cancel-pending'), 'invalid selection intent')
    require(type(published) is bool, 'published must be a boolean')
    return _snapshot(target_path, expected_model, expected_serial, expected_bytes, slot,
                     intent, published)


def _snapshot(target_path, expected_model, expected_serial, expected_bytes, slot,
              intent=None, published=False):
    MANAGEMENT.runtime_guard()
    table = MANAGEMENT.mount_table(Path('/proc/self/mountinfo').read_text())
    root_row, state_row = MANAGEMENT.validate_mounts(table)
    MANAGEMENT.block_identity(root_row)
    MANAGEMENT.block_identity(state_row)
    MANAGEMENT.secure_path(BOOT, directory=True)
    require(os.stat(BOOT).st_dev == os.stat('/run/a733-state').st_dev, 'boot directory is not on state')
    active, state = running_context(table, MANAGEMENT.read_secure(MARKER, 1024**2),
                                   MANAGEMENT.read_secure(BOOT / 'selected.conf', 4096),
                                   MANAGEMENT.read_secure('/run/a733-state/.a733-state', 128),
                                   os.uname().release,
                                   selection_mode='current' if intent is None else 'pending')
    if intent is not None:
        selected = state['selector']['SLOT']
        running = active['slot']
        if intent == 'activate':
            require(slot != running and selected == (slot if published else running),
                    'activation requires inactive target and expected selector')
        else:
            require(slot == running and (selected == running if published else selected != running),
                    'cancellation requires running target and expected selector')
    disk, target, nodes, geometry = inventory_policy(
        INSTALL.inventory(), str(target_path), expected_model, expected_serial, expected_bytes,
        slot, active, state, PLANNER.identify, PLANNER.read_safety,
        target_kind='running' if intent == 'cancel-pending' else 'inactive')
    sysfs_geometry(nodes, geometry)
    disk_fd = open_checked(disk)
    try:
        gpt = gpt_context(disk_fd, disk)
        target['partition_uuid'] = gpt['partition_guids'][target['partition_number'] - 1]
        target_fd = open_checked(target)
        os.close(target_fd)
    finally:
        os.close(disk_fd)
    return {'disk': disk, 'target': target, 'active': active, 'state': state, 'gpt': gpt}
