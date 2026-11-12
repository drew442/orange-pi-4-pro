"""Pure, bounded managed-run permit schema; no runtime authority or I/O.

Parsing is not admission or process authentication. Callers must authenticate
parent/child credentials, executable pins, retained descriptors, live storage
and durable lifecycle state externally. This module opens no files and never
authorizes a checkpoint. Valid bytes alone grant no permission to start a guest.
"""
import importlib.util
import json
from pathlib import Path


def _module(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CONFIG = _module('a733_permit_config', 'guest_config.py')
REGISTRY = _module('a733_permit_registry', 'guest_registry.py')
require = REGISTRY.require
LIMIT = 65536
MAX_DEPTH = 12
MAX_INTEGER = (1 << 63) - 1
MAX_PID = 4194304
MAX_FD = 1048575
PROFILE = 'a733-managed-run-permit-v1'
QEMU_PATH = '/usr/bin/qemu-system-aarch64'
FIELDS = {'schema', 'profile', 'run_id', 'boot_id', 'storage', 'config',
          'manifest_sha256', 'head_sha256', 'host_payload_sha256', 'qemu',
          'kvm_gid', 'parent', 'child', 'locks', 'namespaces', 'automatic'}


def _consume(budget, count):
    budget[0] -= count
    require(budget[0] >= 0, 'permit size limit exceeded')


def _string(value, budget):
    # Count ensure_ascii=False JSON bytes without allocating an encoded copy.
    require(len(value) <= LIMIT, 'permit string limit exceeded')
    _consume(budget, 2)
    for char in value:
        point = ord(char)
        require(not 0xd800 <= point <= 0xdfff, 'invalid Unicode surrogate')
        if char in ('"', '\\', '\b', '\f', '\n', '\r', '\t'):
            size = 2
        elif point < 32:
            size = 6
        else:
            size = 1 if point < 128 else 2 if point < 2048 else 3 if point < 65536 else 4
        _consume(budget, size)


def _tree(value, depth=0, budget=None):
    """Bound nesting and total encoding size before json.dumps allocates bytes."""
    if budget is None:
        budget = [LIMIT - 1]  # Canonical output also includes a final newline.
    require(depth <= MAX_DEPTH, 'permit nesting limit exceeded')
    if type(value) in (dict, list):
        require(depth < MAX_DEPTH, 'permit nesting limit exceeded')
        _consume(budget, 2 + max(0, len(value) - 1))
    if type(value) is dict:
        for key, child in value.items():
            require(type(key) is str, 'permit JSON keys must be strings')
            _string(key, budget)
            _consume(budget, 1)
            _tree(child, depth + 1, budget)
    elif type(value) is list:
        for child in value:
            _tree(child, depth + 1, budget)
    elif type(value) is str:
        _string(value, budget)
    elif type(value) is bool:
        _consume(budget, 4 if value else 5)
    elif value is None:
        _consume(budget, 4)
    elif type(value) is int:
        require(value.bit_length() <= 63, 'permit integer limit exceeded')
        _consume(budget, len(str(value)))
    else:
        raise ValueError('invalid permit JSON value')


def _depth(text):
    depth, quoted, escaped = 0, False, False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in '[{':
            depth += 1
            require(depth <= MAX_DEPTH, 'permit nesting limit exceeded')
        elif char in ']}':
            depth -= 1


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False)


def _integer(value, minimum, maximum, label):
    require(type(value) is int and minimum <= value <= maximum, 'invalid ' + label)


def validate_permit(data):
    """Parse bounded UTF-8 JSON into independently owned normalized data.

    Process identities and descriptor numbers are claims, authenticated externally.
    """
    require(type(data) in (bytes, str), 'permit must be bytes or text')
    require(0 < len(data) <= LIMIT, 'invalid permit size')
    try:
        encoded = data.encode('utf-8') if type(data) is str else data
        require(len(encoded) <= LIMIT, 'invalid permit size')
        text = encoded.decode('utf-8')
        _depth(text)
        value = json.loads(text, object_pairs_hook=REGISTRY.IDENTITY.unique_pairs,
                           parse_constant=REGISTRY._reject_constant)
    except (UnicodeError, RecursionError) as error:
        raise ValueError('invalid permit encoding or nesting') from error
    _tree(value)
    require(type(value) is dict and set(value) == FIELDS, 'invalid permit fields')
    require(type(value['schema']) is int and value['schema'] == 1 and
            type(value['profile']) is str and value['profile'] == PROFILE,
            'invalid permit schema/profile')
    for key in ('run_id', 'boot_id'):
        REGISTRY._uuid(value[key])
    storage = REGISTRY.validate_registry(_encode({
        'schema': 1, 'profile': REGISTRY.PROFILE, 'generation': 0,
        'storage': value['storage'], 'guests': [],
    }))['storage']
    config = CONFIG.validate_config(_encode(value['config']))
    require(config['name'] != 'firstkvm', 'firstkvm must remain manual')
    value['storage'], value['config'] = storage, config
    for key in ('manifest_sha256', 'head_sha256', 'host_payload_sha256'):
        REGISTRY._digest(value[key])
    qemu = value['qemu']
    require(type(qemu) is dict and set(qemu) == {'path', 'bytes', 'sha256'},
            'invalid QEMU fields')
    require(type(qemu['path']) is str and qemu['path'] == QEMU_PATH, 'invalid QEMU path')
    _integer(qemu['bytes'], 1, MAX_INTEGER, 'QEMU size')
    REGISTRY._digest(qemu['sha256'])
    _integer(value['kvm_gid'], 1, 2147483647, 'KVM group')
    require(value['kvm_gid'] not in (config['uid'], config['gid']), 'KVM group collides with guest')
    for key in ('parent', 'child'):
        identity = value[key]
        require(type(identity) is dict and set(identity) == {'pid', 'start_ticks'},
                'invalid ' + key + ' fields')
        _integer(identity['pid'], 1, MAX_PID, key + ' PID')
        _integer(identity['start_ticks'], 1, MAX_INTEGER, key + ' start ticks')
    require(value['parent']['pid'] != value['child']['pid'], 'parent and child PID collide')
    namespaces = value['namespaces']
    require(type(namespaces) is dict and set(namespaces) == {'pid', 'user', 'mnt'},
            'invalid namespace fields')
    for name in ('pid', 'user', 'mnt'):
        identity = namespaces[name]
        require(type(identity) is dict and set(identity) == {'dev', 'ino'},
                'invalid ' + name + ' namespace identity')
        _integer(identity['dev'], 0, MAX_INTEGER, name + ' namespace device')
        _integer(identity['ino'], 1, MAX_INTEGER, name + ' namespace inode')
    locks = value['locks']
    require(type(locks) is dict and set(locks) == {'lease', 'guest'}, 'invalid lock fields')
    for key in ('lease', 'guest'):
        lock = locks[key]
        require(type(lock) is dict and set(lock) == {'fd', 'dev', 'ino'},
                'invalid ' + key + ' lock fields')
        _integer(lock['fd'], 3, MAX_FD, key + ' lock FD')
        _integer(lock['dev'], 0, MAX_INTEGER, key + ' lock device')
        _integer(lock['ino'], 1, MAX_INTEGER, key + ' lock inode')
    lease, guest = locks['lease'], locks['guest']
    require(lease['fd'] != guest['fd'], 'lock descriptors collide')
    require((lease['dev'], lease['ino']) != (guest['dev'], guest['ino']), 'lock inodes collide')
    require(type(value['automatic']) is bool, 'automatic must be boolean')
    return value


def canonical(value):
    """Return validated stable UTF-8 JSON with a final newline, without I/O."""
    _tree(value)
    normalized = validate_permit(_encode(value))
    return (_encode(normalized) + '\n').encode('utf-8')
