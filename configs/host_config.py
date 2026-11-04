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
    if str(network) != cidr or network.prefixlen == 0:
        raise ValueError('management_cidr must be canonical IPv4 CIDR with prefix 1..32')
    return value


def validate_public_key(data):
    try:
        text = data.decode('ascii')
    except UnicodeDecodeError as error:
        raise ValueError('public key must be ASCII') from error
    if '\r' in text or '\x00' in text or len(text.splitlines()) != 1:
        raise ValueError('public key must be one line')
    fields = text.strip().split()
    if len(fields) < 2 or fields[0] != 'ssh-ed25519':
        raise ValueError('a plain ssh-ed25519 public key without options is required')
    try:
        blob = base64.b64decode(fields[1], validate=True)
    except ValueError as error:
        raise ValueError('invalid public key base64') from error
    algorithm = b'ssh-ed25519'
    expected = struct.pack('>I', len(algorithm)) + algorithm + struct.pack('>I', 32)
    if (len(blob) != len(expected) + 32 or not blob.startswith(expected)
            or base64.b64encode(blob).decode() != fields[1]):
        raise ValueError('invalid RFC4253 ed25519 public key')
    canonical = f'ssh-ed25519 {fields[1]}\n'.encode()
    fingerprint = 'SHA256:' + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip('=')
    return canonical, fingerprint
