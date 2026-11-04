#!/usr/bin/python3
"""Read-only mount/sysfs placement guard for an already held guest disk.

This checks the current mount namespace, not GPT contents or hardware acceptance.
The bootstrap independently validates GPT roles. No block device is opened.
Fixture roots are Python API inputs only; there is no configurable runtime CLI.
"""
import os
from pathlib import Path
import re
import stat

GUEST_MOUNT = '/run/a733-guests'
MOUNTINFO_LIMIT = 1024 * 1024
FDINFO_LIMIT = 16384
NUMBER = r'(?:0|[1-9][0-9]*)'
DEVICE = re.compile(rf'({NUMBER}):({NUMBER})')
NVME = re.compile(r'/dev/(nvme(?:0|[1-9][0-9]*)n[1-9][0-9]*)p([235])')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_bounded(path, limit):
    with Path(path).open('rb') as stream:
        data = stream.read(limit + 1)
    require(0 < len(data) <= limit, f'empty or oversized input: {path}')
    return data.decode('ascii')


def number(value, positive=False):
    require(len(value) <= 20 and re.fullmatch(NUMBER, value) is not None,
            'invalid numeric identity')
    result = int(value)
    require(not positive or result > 0, 'invalid zero identity')
    return result


def device(value):
    match = DEVICE.fullmatch(value)
    require(match is not None, 'invalid device identity')
    return number(match[1]), number(match[2])


def options(value):
    fields = value.split(',')
    require(all(fields) and len(set(fields)) == len(fields), 'ambiguous mount options')
    require(not {'ro', 'rw'} <= set(fields), 'inconsistent mount access flags')
    return set(fields)


def mount_table(data):
    require(isinstance(data, str) and 0 < len(data) <= MOUNTINFO_LIMIT,
            'empty or oversized mountinfo')
    require('\\' not in data and '\x00' not in data, 'escaped mountinfo refused')
    rows = {}
    ids = set()
    for line in data.splitlines():
        fields = line.split()
        require(fields.count('-') == 1, 'malformed mountinfo separator')
        split = fields.index('-')
        require(split >= 6 and len(fields) == split + 4, 'malformed mountinfo fields')
        mount_id = number(fields[0], positive=True)
        number(fields[1], positive=True)
        identity = device(fields[2])
        root, target = fields[3:5]
        require(root.startswith('/') and target.startswith('/') and
                all(part not in ('.', '..') for part in root.split('/') + target.split('/')) and
                '//' not in root and '//' not in target and
                (target == '/' or not target.endswith('/')), 'noncanonical mount path')
        require(mount_id not in ids and target not in rows, 'duplicate mount identity or target')
        ids.add(mount_id)
        rows[target] = {'mount_id': mount_id, 'device': identity, 'root': root,
                        'flags': options(fields[5]), 'type': fields[split + 1],
                        'source': fields[split + 2], 'super': options(fields[split + 3])}
    return rows


def validate_mounts(rows):
    require('/' in rows and GUEST_MOUNT in rows, 'required guest/root mount missing')
    root, guest = rows['/'], rows[GUEST_MOUNT]
    for row, access in ((root, 'ro'), (guest, 'rw')):
        require(row['type'] == 'ext4' and row['root'] == '/', 'wrong filesystem type or mount root')
        require(access in row['flags'] and access in row['super'], 'wrong mount/superblock access')
    require({'nodev', 'nosuid', 'noexec'} <= guest['flags'], 'unsafe guest mount flags')
    require(not any(target.startswith(GUEST_MOUNT + '/') for target in rows),
            'nested guest mount refused')
    match = NVME.fullmatch(root['source'])
    require(match is not None and match[2] in ('2', '3'), 'root must be selected NVMe p2/p3')
    require(guest['source'] == f'/dev/{match[1]}p5', 'guest must be p5 on selected NVMe namespace')
    require(root['device'] != guest['device'], 'root and guest devices must differ')
    return root, guest, match[1]


def block_identity(row, *, dev_root, sys_root):
    match = NVME.fullmatch(row['source'])
    require(match is not None, 'noncanonical NVMe source')
    node = Path(dev_root) / row['source'].removeprefix('/dev/')
    info = node.lstat()
    require(stat.S_ISBLK(info.st_mode) and info.st_uid == 0,
            'not a real root-owned block node')
    identity = (os.major(info.st_rdev), os.minor(info.st_rdev))
    require(identity == row['device'], 'block node differs from mounted device')
    sys_root = Path(sys_root).resolve(strict=True)
    link = sys_root / 'dev/block' / f'{identity[0]}:{identity[1]}'
    leaf = link.resolve(strict=True)
    require(leaf.is_relative_to(sys_root / 'devices') and
            leaf.name == node.name and leaf.parent.name == match[1],
            'unexpected sysfs NVMe partition or namespace')
    require(read_bounded(leaf / 'partition', 32) == match[2] + '\n' and
            read_bounded(leaf / 'dev', 64) == f'{identity[0]}:{identity[1]}\n',
            'unexpected sysfs partition identity')
    parent = leaf.parent
    require(not (parent / 'partition').exists(), 'NVMe namespace is a partition')
    parent_device = device(read_bounded(parent / 'dev', 64).removesuffix('\n'))
    parent_link = sys_root / 'dev/block' / f'{parent_device[0]}:{parent_device[1]}'
    require(parent_link.resolve(strict=True) == parent, 'inconsistent sysfs namespace identity')
    require(read_bounded(parent / 'removable', 16) == '0\n', 'removable NVMe refused')
    if (leaf / 'removable').exists():
        require(read_bounded(leaf / 'removable', 16) == '0\n', 'removable partition refused')
    return identity


def fd_mount_id(data):
    require(isinstance(data, str) and 0 < len(data) <= FDINFO_LIMIT, 'invalid fdinfo size')
    values = []
    for line in data.splitlines():
        require(':' in line, 'malformed fdinfo')
        key, value = line.split(':', 1)
        if key == 'mnt_id':
            values.append(number(value.strip(), positive=True))
    require(len(values) == 1, 'missing or ambiguous fd mount identity')
    return values[0]


def verify_guest_storage(disk_fd, *, boot_fds=(), proc_root=Path('/proc'), sys_root=Path('/sys'),
                         dev_root=Path('/dev')):
    """Return placement evidence or raise ValueError/OSError without writing.

    The caller retains the regular file descriptor through QEMU handoff. These
    checks require no effective-UID privilege and accept no environment config.
    """
    require(type(disk_fd) is int and disk_fd >= 0, 'invalid disk descriptor')
    info = os.fstat(disk_fd)
    require(stat.S_ISREG(info.st_mode), 'guest disk descriptor must be regular')
    table = mount_table(read_bounded(Path(proc_root) / 'self/mountinfo', MOUNTINFO_LIMIT))
    root, guest, namespace = validate_mounts(table)
    for row in (root, guest):
        block_identity(row, dev_root=dev_root, sys_root=sys_root)
    require((os.major(info.st_dev), os.minor(info.st_dev)) == guest['device'],
            'held disk is not on guest filesystem')
    held_mount = fd_mount_id(read_bounded(Path(proc_root) / f'self/fdinfo/{disk_fd}', FDINFO_LIMIT))
    require(held_mount == guest['mount_id'], 'held disk is on an alternate mount')
    for fd in boot_fds:
        require(type(fd) is int and fd >= 0, 'invalid boot descriptor')
        boot = os.fstat(fd)
        require(stat.S_ISREG(boot.st_mode), 'boot descriptor must be regular')
        identity = (os.major(boot.st_dev), os.minor(boot.st_dev))
        mount_id = fd_mount_id(read_bounded(Path(proc_root) / f'self/fdinfo/{fd}', FDINFO_LIMIT))
        require(any(identity == row['device'] and mount_id == row['mount_id']
                    for row in (root, guest)), 'boot input must use selected root or guest NVMe mount')
    return {'root_source': root['source'], 'guest_source': guest['source'],
            'namespace': namespace, 'root_device': list(root['device']),
            'guest_device': list(guest['device']), 'guest_mount_id': guest['mount_id'],
            'boot_inputs_checked': len(boot_fds)}
