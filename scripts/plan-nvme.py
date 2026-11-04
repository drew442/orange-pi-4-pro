#!/usr/bin/env python3
"""Report a proposed NVMe GPT layout. This program never writes block devices."""

import argparse
import json
import importlib.util
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

# Also support the installer's isolated Python invocation (-I), which omits
# the script directory from sys.path. Import only the adjacent trusted module.
_spec = importlib.util.spec_from_file_location('a733_nvme_layout', Path(__file__).with_name('nvme_layout.py'))
_layout = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_layout)
GIB, MIB, layout = _layout.GIB, _layout.MIB, _layout.layout


class UnsafeTarget(ValueError):
    """The target cannot be safely identified as an idle NVMe namespace."""


def require(condition, message):
    if not condition:
        raise UnsafeTarget(message)


def identify(path):
    resolved = os.path.realpath(path)
    info = os.stat(resolved)
    require(stat.S_ISBLK(info.st_mode), f"Not a block device: {path}")
    return resolved, f"{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}"


def flatten(nodes):
    for node in nodes:
        yield node
        yield from flatten(node.get("children", []))


def read_safety(nodes):
    """Read kernel evidence; missing or unreadable evidence fails closed."""
    result = {}
    for node in nodes:
        base = Path('/sys/class/block') / node['kname']
        require(base.joinpath('dev').read_text().strip() == node['maj:min'],
                'sysfs and lsblk device identities differ')
        result[node['maj:min']] = {
            'holders': sorted(p.name for p in base.joinpath('holders').iterdir()),
            'read_only': base.joinpath('ro').read_text().strip() != '0',
        }
    root = Path('/sys/class/block') / nodes[0]['kname']
    partitions = {p.name for p in root.iterdir() if p.joinpath('partition').exists()}
    require(partitions == {n['kname'] for n in nodes[1:]},
            'sysfs partitions and lsblk descendants differ')
    mounts = {line.split()[2] for line in Path('/proc/self/mountinfo').read_text().splitlines()}
    swaps = set()
    lines = Path('/proc/swaps').read_text().splitlines()
    require(lines and lines[0].startswith('Filename'), 'Cannot parse swap inventory')
    for line in lines[1:]:
        name = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), line.split()[0])
        info = os.stat(name)
        if stat.S_ISBLK(info.st_mode):
            swaps.add(f'{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}')
        else:
            # A swap file on this disk also makes it busy.
            swaps.add(f'{os.major(info.st_dev)}:{os.minor(info.st_dev)}')
    for major_minor, evidence in result.items():
        evidence['mounted'] = major_minor in mounts
        evidence['swap'] = major_minor in swaps
    return result


def plan(inventory, target, resolver=identify, safety_reader=read_safety,
         expected_serial=None, expected_bytes=None):
    canonical, major_minor = resolver(target)
    matches = [n for n in flatten(inventory['blockdevices'])
               if n.get('maj:min') == major_minor]
    require(len(matches) == 1, 'Target identity is absent or ambiguous in lsblk')
    disk = matches[0]
    require(disk.get('type') == 'disk' and disk.get('tran') == 'nvme'
            and re.fullmatch(r'nvme[0-9]+n[0-9]+', disk.get('kname', '')),
            'Target must be a whole NVMe namespace')
    require(resolver(disk['path']) == (canonical, major_minor),
            'Target path and lsblk identity differ')
    model = str(disk.get('model') or '').strip()
    serial = str(disk.get('serial') or '').strip()
    require(model and serial, 'Target model and serial are required')
    require(expected_serial is None or serial == expected_serial, 'Serial does not match')
    size, logical, physical = (disk.get(k) for k in ('size', 'log-sec', 'phy-sec'))
    require(all(type(v) is int and v > 0 for v in (size, logical, physical)),
            'Invalid byte size or sector geometry')
    require(logical in (512, 4096)
            and physical >= logical and physical % logical == 0
            and MIB % physical == 0 and size % logical == 0,
            'Unsupported sector geometry')
    require(expected_bytes is None or size == expected_bytes, 'Byte capacity does not match')
    nodes = list(flatten([disk]))
    evidence = safety_reader(nodes)
    for node in nodes:
        require(node.get('type') in ('disk', 'part'), 'Target has mapped descendants')
        require(node.get('ro') is False or type(node.get('ro')) is int and node['ro'] == 0,
                'Target or descendant is read-only or has unknown read-only state')
        require(not any(node.get('mountpoints') or []), 'Target or descendant is mounted')
        state = evidence[node['maj:min']]
        require(not state['holders'], 'Target or descendant has active holders')
        require(not state['read_only'], 'Kernel reports read-only device')
        require(not state['mounted'], 'Target or descendant is mounted')
        require(not state['swap'], 'Target or descendant has active swap')
    try:
        geometry = layout(size, logical, physical)
    except ValueError as error:
        raise UnsafeTarget(str(error)) from error
    return {
        'schema_version': 1, 'operation': 'plan-only', 'writes_performed': False,
        'target': {'requested_path': target, 'canonical_path': canonical,
                   'major_minor': major_minor, 'model': model, 'serial': serial,
                   'size_bytes': size, 'logical_sector_bytes': logical,
                   'physical_sector_bytes': physical,
                   'stable_path_recommended': '/dev/disk/by-id/nvme-…'},
        'authorization_evidence': {'explicit_target': True, 'whole_nvme_namespace': True,
                                   'checked_busy_indicators_absent': True, 'devices_checked': evidence,
                                   'expected_serial': expected_serial,
                                   'expected_bytes': expected_bytes},
        'layout': geometry,
        'limitations': ['Observation is not a lock; revalidate before any future installer action.',
                        'Mount checks cover the current mount namespace.',
                        'Open raw-device users are not detected.',
                        'These observations do not authorize installation.',
                        'Proposed layout requires bootloader filesystem validation.',
                        'No installation authorization, firmware validation or hardware acceptance.'],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', required=True, help='Whole NVMe path; prefer /dev/disk/by-id/')
    parser.add_argument('--expected-serial')
    parser.add_argument('--expected-bytes', type=int)
    args = parser.parse_args()
    try:
        output = subprocess.run([
            'lsblk', '--json', '--bytes', '--paths', '--tree', '--output',
            'PATH,KNAME,TYPE,TRAN,MODEL,SERIAL,SIZE,LOG-SEC,PHY-SEC,RO,MOUNTPOINTS,MAJ:MIN'
        ], check=True, text=True, capture_output=True)
        inventory = json.loads(output.stdout)
        # --paths also expands KNAME on util-linux; sysfs requires the basename.
        for node in flatten(inventory['blockdevices']):
            node['kname'] = os.path.basename(node['kname'])
        result = plan(inventory, args.target, expected_serial=args.expected_serial,
                      expected_bytes=args.expected_bytes)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f'Refusing target: {error}', file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
