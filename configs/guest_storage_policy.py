"""Pure storage policy data; decoding grants no storage or launch authority.

The future caller must authenticate protected policy placement and validate live
storage independently. Canonical bytes and their length are not trusted hashes:
an equally sized, syntactically valid replacement still decodes successfully.
This module performs no discovery, device access, publication or mutation.
"""
import importlib.util
import json
from pathlib import Path
import re
import uuid

_SPEC = importlib.util.spec_from_file_location(
    'a733_storage_policy_identity', Path(__file__).with_name('guest_identity.py'))
IDENTITY = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(IDENTITY)
require = IDENTITY.require
LIMIT = 4096
MAX_DEPTH = 4
MAX_BYTES = (1 << 63) - 1
PROFILE = 'a733-guest-storage-policy-v1'
TARGET = '/dev/nvme0n1'
FIELDS = {'schema', 'profile', 'target', 'expected_model', 'expected_bytes', 'storage'}
STORAGE_FIELDS = {'state_uuid', 'guest_uuid', 'nvme_serial'}


def _uuid(value):
    require(type(value) is str, 'invalid storage UUID')
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as error:
        raise ValueError('invalid storage UUID') from error
    require(str(parsed) == value and parsed.int != 0,
            'canonical nonzero storage UUID required')


def _validate(value):
    require(type(value) is dict and set(value) == FIELDS, 'invalid storage policy fields')
    require(type(value['schema']) is int and value['schema'] == 1 and
            type(value['profile']) is str and value['profile'] == PROFILE,
            'invalid storage policy schema/profile')
    require(type(value['target']) is str and value['target'] == TARGET,
            'invalid storage policy target')
    require(type(value['expected_model']) is str and
            re.fullmatch(r'[\x20-\x7e]{1,80}', value['expected_model']) is not None,
            'invalid expected NVMe model')
    require(type(value['expected_bytes']) is int and
            0 < value['expected_bytes'] <= MAX_BYTES, 'invalid expected NVMe size')
    storage = value['storage']
    require(type(storage) is dict and set(storage) == STORAGE_FIELDS,
            'invalid storage policy storage fields')
    _uuid(storage['state_uuid'])
    _uuid(storage['guest_uuid'])
    require(storage['state_uuid'] != storage['guest_uuid'], 'storage UUIDs must differ')
    require(type(storage['nvme_serial']) is str and
            re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', storage['nvme_serial']) is not None,
            'invalid NVMe serial')
    return {**value, 'storage': dict(storage)}


def canonical(value):
    """Validate policy data and emit deterministic UTF-8 bytes with a newline."""
    normalized = _validate(value)
    encoded = (json.dumps(normalized, sort_keys=True, separators=(',', ':'),
                          allow_nan=False) + '\n').encode('utf-8')
    require(len(encoded) <= LIMIT, 'invalid storage policy size')
    return encoded


def _reject_constant(value):
    raise ValueError('nonfinite JSON value')


def _check_depth(text):
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
            require(depth <= MAX_DEPTH, 'storage policy nesting limit exceeded')
        elif char in ']}':
            depth -= 1


def decode(data):
    """Decode only bounded canonical bytes; successful decoding is not authority."""
    require(type(data) is bytes, 'storage policy must be bytes')
    require(0 < len(data) <= LIMIT, 'invalid storage policy size')
    try:
        text = data.decode('utf-8')
        _check_depth(text)
        value = json.loads(text, object_pairs_hook=IDENTITY.unique_pairs,
                           parse_constant=_reject_constant)
    except (UnicodeError, RecursionError) as error:
        raise ValueError('invalid storage policy encoding or nesting') from error
    normalized = _validate(value)
    require(canonical(normalized) == data, 'storage policy bytes must be canonical')
    return normalized
