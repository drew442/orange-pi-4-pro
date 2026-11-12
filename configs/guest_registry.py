"""Pure desired-state schema; storage authentication and publication are external.

A manifest pin is supplied by the caller, never inferred from a current file.
Valid registry bytes alone do not authorize automatic checkpointing or launch.
"""
import importlib.util
import json
from pathlib import Path
import re
import uuid

_SPEC = importlib.util.spec_from_file_location(
    'a733_registry_identity', Path(__file__).with_name('guest_identity.py'))
IDENTITY = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(IDENTITY)
require = IDENTITY.require
LIMIT = 65536
MAX_DEPTH = 8
MAX_GUESTS = 32
MAX_GENERATION = (1 << 63) - 1
PROFILE = 'a733-guest-autostart-v1'
SHUTDOWN_PROFILE = 'qmp-guest-shutdown-v1'
FIELDS = {'schema', 'profile', 'generation', 'storage', 'guests'}
GUEST_FIELDS = {'name', 'uid', 'gid', 'manifest_sha256', 'enabled',
                'shutdown_profile', 'fixture_profile'}


def _reject_constant(value):
    raise ValueError('nonfinite JSON value')


def _check_depth(text):
    """Bound parser nesting before invoking the JSON decoder."""
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
            require(depth <= MAX_DEPTH, 'registry nesting limit exceeded')
        elif char in ']}':
            depth -= 1


def _uuid(value):
    require(type(value) is str, 'invalid storage UUID')
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as error:
        raise ValueError('invalid storage UUID') from error
    require(str(parsed) == value and parsed.int != 0, 'canonical nonzero storage UUID required')


def _digest(value):
    require(type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None,
            'invalid manifest SHA-256')


def validate_registry(data):
    """Parse bounded UTF-8 JSON and return normalized, independently owned data."""
    require(isinstance(data, (bytes, str)), 'registry must be bytes or text')
    try:
        encoded = data.encode('utf-8') if isinstance(data, str) else data
        require(0 < len(encoded) <= LIMIT, 'invalid registry size')
        text = encoded.decode('utf-8')
        _check_depth(text)
        value = json.loads(text, object_pairs_hook=IDENTITY.unique_pairs,
                           parse_constant=_reject_constant)
    except (UnicodeError, RecursionError) as error:
        raise ValueError('invalid registry encoding or nesting') from error
    require(type(value) is dict and set(value) == FIELDS, 'invalid registry fields')
    require(type(value['schema']) is int and value['schema'] == 1 and
            type(value['profile']) is str and value['profile'] == PROFILE,
            'invalid registry schema/profile')
    generation = value['generation']
    require(type(generation) is int and 0 <= generation <= MAX_GENERATION,
            'invalid registry generation')
    storage = value['storage']
    require(type(storage) is dict and set(storage) == {'state_uuid', 'guest_uuid', 'nvme_serial'},
            'invalid registry storage fields')
    _uuid(storage['state_uuid'])
    _uuid(storage['guest_uuid'])
    require(type(storage['nvme_serial']) is str and
            re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', storage['nvme_serial']) is not None,
            'invalid NVMe serial')
    guests = value['guests']
    require(type(guests) is list and len(guests) <= MAX_GUESTS, 'invalid registry guest count')
    names, uids, gids = set(), set(), set()
    for guest in guests:
        require(type(guest) is dict and set(guest) == GUEST_FIELDS, 'invalid registry guest fields')
        identity = IDENTITY.validate_config(json.dumps({
            'schema': 1, 'guests': [{key: guest[key] for key in ('name', 'uid', 'gid')}],
        }, allow_nan=False))['guests'][0]
        name, uid, gid = identity['name'], identity['uid'], identity['gid']
        require(name not in names and uid not in uids and gid not in gids,
                'duplicate registry identity')
        names.add(name)
        uids.add(uid)
        gids.add(gid)
        _digest(guest['manifest_sha256'])
        require(type(guest['enabled']) is bool, 'enabled must be boolean')
        require(guest['shutdown_profile'] == SHUTDOWN_PROFILE, 'invalid shutdown profile')
        require(type(guest['fixture_profile']) is str and
                guest['fixture_profile'] in ('none', 'first-kvm-http-v1'), 'invalid fixture profile')
        require(name != 'firstkvm' or not guest['enabled'], 'firstkvm must remain manual')
    value['guests'] = sorted(guests, key=lambda guest: guest['name'])
    return value


def canonical(value):
    """Return validated stable UTF-8 JSON with a final newline."""
    try:
        data = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (TypeError, OverflowError, RecursionError) as error:
        raise ValueError('invalid registry value') from error
    normalized = validate_registry(data)
    return (json.dumps(normalized, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')


def update(value, name, *, manifest_sha256=None, enabled=None, expected_generation):
    """Apply a caller-authenticated pin/enable change with a generation CAS.

    Every successful call advances the generation, including an unchanged value.
    This function does not enroll new guests or inspect storage.
    """
    result = validate_registry(canonical(value))
    require(type(expected_generation) is int and
            expected_generation == result['generation'], 'registry generation differs')
    require(result['generation'] < MAX_GENERATION, 'registry generation exhausted')
    require(type(name) is str, 'invalid guest name')
    matches = [guest for guest in result['guests'] if guest['name'] == name]
    require(len(matches) == 1, 'guest is not registered')
    if manifest_sha256 is not None:
        _digest(manifest_sha256)
        matches[0]['manifest_sha256'] = manifest_sha256
    if enabled is not None:
        require(type(enabled) is bool, 'enabled must be boolean')
        matches[0]['enabled'] = enabled
    result['generation'] += 1
    return validate_registry(canonical(result))
