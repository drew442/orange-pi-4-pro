"""Pure enrollment schema and manual supervision preparation, with no I/O.

Enrollment publishes the validated config in its root-owned manifest. The guest
runtime directory must be root:guest-gid 0710; kernel/initrd root:root 0444,
mutable disk guest:guest 0600, and enrollment.json root:root 0400. These are
publication requirements, not effects of this module. RAM/KVM and physical
acceptance checks belong to the operator and launcher.

The unit deliberately retains the original mount namespace for storage guards.
Stopping it sends SIGTERM; no guest graceful-shutdown protocol is provided.
"""
import importlib.util
import json
from pathlib import Path
import re

_SPEC = importlib.util.spec_from_file_location(
    'a733_enrollment_identity', Path(__file__).with_name('guest_identity.py'))
IDENTITY = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(IDENTITY)
require = IDENTITY.require
LIMIT = 65536
ARTIFACT_LIMIT = 256 * 1024 * 1024
DISK_LIMIT = 250000000000
FIELDS = {'schema', 'name', 'uid', 'gid', 'memory_mib', 'memory_max_mib',
          'cpus', 'kernel', 'initrd', 'disk'}


def _reject_constant(value):
    raise ValueError('nonfinite JSON value')


def validate_config(data):
    """Parse bounded UTF-8 JSON, rejecting unknown fields and duplicate keys."""
    require(isinstance(data, (bytes, str)), 'config must be bytes or text')
    try:
        encoded = data.encode('utf-8') if isinstance(data, str) else data
        require(0 < len(encoded) <= LIMIT, 'invalid config size')
        value = json.loads(encoded.decode('utf-8'), object_pairs_hook=IDENTITY.unique_pairs,
                           parse_constant=_reject_constant)
    except (UnicodeError, RecursionError) as error:
        raise ValueError('invalid config encoding or nesting') from error
    require(type(value) is dict and set(value) == FIELDS, 'invalid enrollment fields')
    identity = IDENTITY.validate_config(json.dumps({
        'schema': value['schema'],
        'guests': [{key: value[key] for key in ('name', 'uid', 'gid')}],
    }))
    result = {'schema': 1, **identity['guests'][0]}
    for key, minimum, maximum in (('memory_mib', 64, 1048576),
                                  ('memory_max_mib', 128, 1048576), ('cpus', 1, 256)):
        number = value[key]
        require(type(number) is int and minimum <= number <= maximum, 'invalid ' + key)
        result[key] = number
    require(result['memory_max_mib'] >= result['memory_mib'] + 64,
            'memory_max_mib must reserve at least 64 MiB beyond guest memory')
    for key in ('kernel', 'initrd', 'disk'):
        artifact = value[key]
        require(type(artifact) is dict and set(artifact) == {'bytes', 'sha256'},
                'invalid ' + key + ' fields')
        size, digest = artifact['bytes'], artifact['sha256']
        maximum = DISK_LIMIT if key == 'disk' else ARTIFACT_LIMIT
        require(type(size) is int and 0 < size <= maximum, 'invalid ' + key + ' size')
        require(key != 'disk' or size % 512 == 0, 'disk must be sector aligned')
        require(type(digest) is str and re.fullmatch('[0-9a-f]{64}', digest) is not None,
                'invalid ' + key + ' sha256')
        result[key] = {'bytes': size, 'sha256': digest}
    return result


def _validate_value(config):
    try:
        data = json.dumps(config, allow_nan=False)
    except (TypeError, OverflowError, RecursionError) as error:
        raise ValueError('invalid config value') from error
    return validate_config(data)


def canonical(config):
    """Return the normalized config as stable UTF-8 JSON with a final newline."""
    value = _validate_value(config)
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def paths(config):
    """Return fixed absolute artifact/manifest paths, without touching storage."""
    value = _validate_value(config)
    base = '/run/a733-guests/' + value['name'] + '/'
    return {key: base + filename for key, filename in (
        ('kernel', 'Image'), ('initrd', 'initramfs.cpio.gz'),
        ('disk', 'disk.raw'), ('enrollment', 'enrollment.json'))}


def render_unit(config, launcher_path, kvm_gid):
    """Render a manually started unit; never installs or enables a service."""
    value = _validate_value(config)
    require(type(launcher_path) is str and launcher_path.startswith('/') and
            re.fullmatch(r'/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*', launcher_path) is not None and
            all(part not in ('.', '..') for part in launcher_path.split('/')),
            'launcher must be a conservative absolute path')
    require(type(kvm_gid) is int and 0 < kvm_gid <= 4294967294 and
            kvm_gid != value['gid'], 'invalid distinct KVM group')
    artifact_paths = paths(value)
    args = ['/usr/bin/python3', '-I', '-B', launcher_path]
    for key in ('kernel', 'initrd', 'disk'):
        args.extend(['--' + key, artifact_paths[key], '--' + key + '-sha256', value[key]['sha256']])
    args.extend(['--memory-mib', str(value['memory_mib']), '--cpus', str(value['cpus'])])
    account = 'a733-vm-' + value['name']
    return '\n'.join([
        '[Unit]', 'Description=A733 guest ' + value['name'], 'After=local-fs.target', '',
        '[Service]', 'Type=exec', 'User=' + account, 'Group=' + account,
        'SupplementaryGroups=' + str(kvm_gid), 'ExecStart=' + ' '.join(args),
        'Restart=no', 'UMask=0077', 'NoNewPrivileges=yes', 'CapabilityBoundingSet=',
        'RestrictSUIDSGID=yes', 'LimitCORE=0', 'MemoryMax=' + str(value['memory_max_mib']) + 'M',
        'MemorySwapMax=0', 'TasksMax=256', 'StandardInput=null', 'StandardOutput=journal', 'StandardError=journal',
        'KillMode=control-group', 'KillSignal=SIGTERM', 'TimeoutStopSec=30', 'SendSIGKILL=no', '',
    ])
