"""Shared data-only management configuration validation (schema 1)."""
import base64
import hashlib
import ipaddress
import json
import re
import struct

def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'duplicate JSON field: {key}')
        result[key] = value
    return result


def validate_config(data):
    value = json.loads(data, object_pairs_hook=unique_pairs)
    required = {'schema', 'hostname', 'management_mac', 'management_cidr', 'dhcp'}
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError('management config must contain exactly schema, hostname, management_mac, management_cidr, dhcp')
    if type(value['schema']) is not int or value['schema'] != 1 or value['dhcp'] is not True:
        raise ValueError('schema 1 and dhcp=true required')
    hostname = value['hostname']
    if not isinstance(hostname, str) or not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', hostname):
        raise ValueError('hostname must be one lowercase DNS label')
    mac = value['management_mac']
    if (not isinstance(mac, str) or not re.fullmatch(r'(?:[0-9a-f]{2}:){5}[0-9a-f]{2}', mac)
            or int(mac[:2], 16) & 1 or mac == '00:00:00:00:00:00'):
        raise ValueError('management_mac must be canonical lowercase nonzero unicast MAC')
    cidr = value['management_cidr']
    if not isinstance(cidr, str):
        raise ValueError('management_cidr must be canonical IPv4 CIDR')
    network = ipaddress.IPv4Network(cidr, strict=True)
    if str(network) != cidr:
        raise ValueError('management_cidr must be canonical IPv4 CIDR with prefix 0..32')
    return value


MAX_PUBLIC_KEYS = 16
MAX_PUBLIC_KEYS_BYTES = 16384


def validate_public_key(data):
    """Parse exactly one plain key, stripping only its optional ASCII comment."""
    if len(data) > MAX_PUBLIC_KEYS_BYTES:
        raise ValueError('public key exceeds byte bound')
    try:
        text = data.decode('ascii')
    except UnicodeDecodeError as error:
        raise ValueError('public key must be ASCII') from error
    line = text[:-1] if text.endswith('\n') else text
    if not line or any(ord(char) < 32 or ord(char) == 127 for char in line):
        raise ValueError('public key must be one line without controls')
    fields = line.split()
    if len(fields) < 2 or fields[0] not in ('ssh-ed25519', 'ssh-rsa'):
        raise ValueError('a plain ssh-ed25519 or ssh-rsa public key without options is required')
    try:
        blob = base64.b64decode(fields[1], validate=True)
    except ValueError as error:
        raise ValueError('invalid public key base64') from error
    if base64.b64encode(blob).decode() != fields[1]:
        raise ValueError('noncanonical public key base64')
    offset = 0
    def field():
        nonlocal offset
        if offset + 4 > len(blob):
            raise ValueError('truncated RFC4253 field')
        size = struct.unpack_from('>I', blob, offset)[0]
        offset += 4
        if size > len(blob) - offset:
            raise ValueError('truncated RFC4253 field')
        value = blob[offset:offset + size]
        offset += size
        return value
    if field() != fields[0].encode():
        raise ValueError('public key algorithm mismatch')
    if fields[0] == 'ssh-ed25519':
        if len(field()) != 32:
            raise ValueError('invalid RFC4253 ed25519 public key')
    else:
        def positive_mpint():
            value = field()
            if (not value or value[0] & 128 or
                    (value[0] == 0 and (len(value) == 1 or not value[1] & 128))):
                raise ValueError('invalid canonical positive RSA mpint')
            return int.from_bytes(value, 'big')
        exponent, modulus = positive_mpint(), positive_mpint()
        if (not 2048 <= modulus.bit_length() <= 16384 or not modulus & 1 or
                not 3 <= exponent <= 0xffffffff or not exponent & 1 or exponent >= modulus):
            raise ValueError('invalid RSA modulus or exponent')
    if offset != len(blob):
        raise ValueError('trailing public key binary data')
    canonical = f'{fields[0]} {fields[1]}\n'.encode()
    fingerprint = 'SHA256:' + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip('=')
    return canonical, fingerprint


def validate_public_keys(data):
    """Return canonical authorized_keys bytes and ordered unique fingerprints."""
    if not data or len(data) > MAX_PUBLIC_KEYS_BYTES:
        raise ValueError('authorized keys must contain 1..16384 bytes')
    lines = data[:-1].split(b'\n') if data.endswith(b'\n') else data.split(b'\n')
    if not 1 <= len(lines) <= MAX_PUBLIC_KEYS:
        raise ValueError('authorized keys must contain 1..16 keys')
    canonical, fingerprints = [], []
    for line in lines:
        key, fingerprint = validate_public_key(line)
        if fingerprint in fingerprints:
            raise ValueError('duplicate administration public key')
        canonical.append(key)
        fingerprints.append(fingerprint)
    return b''.join(canonical), fingerprints


def timesync_policy(metadata):
    """Recognize the closed profile feature set, including legacy baseline."""
    def require(condition, message):
        if not condition:
            raise ValueError(message)

    require(type(metadata) is dict, 'invalid payload metadata')
    sources = metadata.get('sources', {})
    require(type(sources) is dict, 'invalid payload sources')
    binding = sources.get('userspace_profile')
    if 'userspace_profile' not in sources:
        profile = 'baseline-20261104'
    else:
        require(type(binding) is dict and type(binding.get('profile')) is str,
                'invalid userspace profile binding')
        profile = binding['profile']
    if profile in ('baseline-20261104', 'normalized-20261106'):
        require('timesync_state' not in sources, 'unexpected timesync policy')
        return None
    require(profile == 'timesync-20261106', 'unknown userspace profile')
    policy = sources.get('timesync_state')
    require(type(policy) is dict and set(policy) == {'schema', 'identity', 'directory', 'helper'},
            'invalid timesync policy fields')
    require(type(policy['schema']) is int and policy['schema'] == 1 and
            type(policy['identity']) is int and policy['identity'] == 996 and
            policy['directory'] == 'var/lib/systemd/timesync', 'invalid timesync policy')
    helper = policy['helper']
    require(type(helper) is dict and set(helper) == {'bytes', 'sha256'} and
            type(helper['bytes']) is int and helper['bytes'] > 0 and
            type(helper['sha256']) is str and re.fullmatch('[0-9a-f]{64}', helper['sha256']) is not None,
            'invalid timesync helper pin')
    return policy
