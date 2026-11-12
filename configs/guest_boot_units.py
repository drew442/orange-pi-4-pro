"""Pure managed-unit formatting and enabled-configuration budget arithmetic.

Callers supply already selected enabled configurations. This module discovers
nothing, authenticates no storage or enrollment, publishes no files and grants
no launch or enable authority. RequiresMountsFor describes intended dependencies;
actual generated mount dependencies and shutdown ordering need hardware proof.
The caller must authenticate the enabled set and refresh units after changes.
Rendered text proves neither current runtime cgroup limits nor native namespace;
the caller must validate actual units/cgroups before launch.

The supervisor retains the native mount namespace. Its fixed capability bound
is a proposed minimum for credential dropping, protected file/socket ownership,
child signals and /proc inspection; sufficiency still requires hardware proof.
The managed launcher must separately drop every capability from the QEMU child.
"""
import importlib.util
from pathlib import Path
from types import MappingProxyType

_SPEC = importlib.util.spec_from_file_location(
    'a733_boot_enrollment', Path(__file__).with_name('guest_config.py'))
CONFIG = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(CONFIG)
require = CONFIG.require

MIB = 1024 * 1024
HOST_RESERVE_BYTES = 2048 * MIB
MAX_GUESTS = 256
SLICE_NAME = 'a733-guests.slice'
SUPERVISOR = '/usr/lib/a733-tools/scripts/run-managed-guest.py'
CAPABILITIES = ('CAP_CHOWN', 'CAP_DAC_OVERRIDE', 'CAP_KILL', 'CAP_SETGID',
                'CAP_SETUID', 'CAP_SETPCAP', 'CAP_SYS_PTRACE')


def _config(config):
    value = CONFIG.validate_config(CONFIG.canonical(config))
    require(value['name'] != 'firstkvm', 'firstkvm must remain manual')
    return value


def _budget(physical_memory_bytes):
    require(type(physical_memory_bytes) is int and physical_memory_bytes > 0,
            'physical memory must be a positive integer byte count')
    budget = (physical_memory_bytes // MIB) * MIB - HOST_RESERVE_BYTES
    require(budget > 0, 'insufficient physical memory after host reserve')
    return budget


def render_managed(config):
    """Return a733-managed-<name>.service text; never install or enable it."""
    value = _config(config)
    return '\n'.join([
        '[Unit]', 'Description=A733 managed guest ' + value['name'],
        'After=a733-guest-prepare.service a733-guest-storage.service',
        'Requires=a733-guest-prepare.service a733-guest-storage.service',
        'RequiresMountsFor=/run/a733-state /run/a733-guests', '',
        '[Service]', 'Type=exec', 'User=root', 'Group=root',
        'ExecStart=/usr/bin/python3 -I -B ' + SUPERVISOR + ' ' + value['name'],
        'Slice=' + SLICE_NAME, 'NoNewPrivileges=yes', 'UMask=0077',
        'CapabilityBoundingSet=' + ' '.join(CAPABILITIES),
        'Restart=no', 'KillMode=mixed', 'KillSignal=SIGTERM',
        'TimeoutStopSec=420', 'SendSIGKILL=yes',
        'MemoryMax=' + str(value['memory_max_mib']) + 'M',
        'MemorySwapMax=0', 'TasksMax=256', 'LimitCORE=0',
        'StandardInput=null', 'StandardOutput=journal', 'StandardError=journal',
        'LogRateLimitIntervalSec=30s', 'LogRateLimitBurst=1000', '',
    ])


def render_slice(physical_memory_bytes):
    """Return the common slice with a conservative physical-memory ceiling."""
    budget = _budget(physical_memory_bytes)
    return '\n'.join([
        '[Unit]', 'Description=A733 managed guests', '', '[Slice]',
        'MemoryMax=' + str(budget // MIB) + 'M',
        'MemorySwapMax=0', 'TasksMax=4096', '',
    ])


def check_budget(configs, physical_memory_bytes):
    """Validate only caller-selected enabled configs; return immutable numbers.

    This static aggregate check is not runtime MemAvailable admission. There is
    no registry discovery, enabled-flag interpretation or enrollment authority.
    """
    require(type(configs) in (list, tuple) and len(configs) <= MAX_GUESTS,
            'zero to 256 enabled configurations required')
    budget = _budget(physical_memory_bytes)
    names, uids, gids = set(), set(), set()
    requested = 0
    for config in configs:
        value = _config(config)
        require(value['name'] not in names and value['uid'] not in uids and
                value['gid'] not in gids, 'duplicate enabled guest identity')
        names.add(value['name'])
        uids.add(value['uid'])
        gids.add(value['gid'])
        requested += value['memory_max_mib'] * MIB
    require(requested <= budget, 'enabled guest memory exceeds host-reserved budget')
    return MappingProxyType(dict(
        guest_count=len(configs), physical_memory_bytes=physical_memory_bytes,
        host_reserve_bytes=HOST_RESERVE_BYTES, budget_bytes=budget,
        requested_bytes=requested, remaining_bytes=budget - requested))
