"""Bounded canonical guest-backup framing and exact checkpoint history."""
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import tarfile

_SPEC = importlib.util.spec_from_file_location('backup_manifest', Path(__file__).with_name('guest_manifest.py'))
MANIFEST = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(MANIFEST)
require = MANIFEST.require
PROFILE = 'a733-guest-backup-v1'
HISTORY_PROFILE = 'a733-guest-checkpoint-history-v1'
NAMES = ('enrollment.json', 'checkpoint-history.json', 'Image', 'initramfs.cpio.gz', 'disk.raw')
HISTORY_LIMIT = 16 * 1024 * 1024
ENTRY_LIMIT = 512 * 1024
ENTRY_COUNT = 1024
NAME_RE = re.compile(r'[0-9a-f]{64}-[0-9a-f]{64}\.json')


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def make_header(name, size):
    require(name in NAMES and type(size) is int and 0 <= size <= 251 * 1024**3,
            'invalid backup header name/size')
    header = tarfile.TarInfo(name)
    header.size = size
    header.mode = 0o400
    header.uid = header.gid = header.mtime = 0
    header.uname = header.gname = ''
    return header.tobuf(format=tarfile.GNU_FORMAT, encoding='ascii', errors='strict')


def read_exact(stream, size):
    parts = []
    while size:
        chunk = stream.read(size)
        require(bool(chunk), 'truncated backup')
        parts.append(chunk)
        size -= len(chunk)
    return b''.join(parts)


def read_header(stream, expected_name, max_size):
    block = read_exact(stream, 512)
    try:
        header = tarfile.TarInfo.frombuf(block, 'ascii', 'strict')
    except (tarfile.TarError, UnicodeError, ValueError) as error:
        raise ValueError('invalid backup tar header') from error
    require(header.name == expected_name and header.type == tarfile.REGTYPE and
            0 <= header.size <= max_size, 'unexpected backup member')
    require(block == make_header(expected_name, header.size), 'noncanonical backup header')
    return header.size


def parse_json(data):
    try:
        return json.loads(data.decode('utf-8'), object_pairs_hook=MANIFEST.CONFIG.IDENTITY.unique_pairs,
                          parse_constant=MANIFEST.CONFIG._reject_constant)
    except (UnicodeError, RecursionError) as error:
        raise ValueError('invalid backup JSON') from error


def decode(encoded, limit):
    require(type(encoded) is str and len(encoded) <= ((limit + 2) // 3) * 4,
            'oversized base64 value')
    try:
        value = base64.b64decode(encoded, validate=True)
    except (ValueError, UnicodeError) as error:
        raise ValueError('invalid backup base64') from error
    require(len(value) <= limit and base64.b64encode(value).decode('ascii') == encoded,
            'noncanonical or oversized base64')
    return value


def validate_record(name, data):
    """Check exact supplied history bytes and structure, without shutdown authority."""
    require(type(name) is str and NAME_RE.fullmatch(name) is not None and
            type(data) is bytes and 0 < len(data) <= ENTRY_LIMIT, 'invalid checkpoint record')
    value = parse_json(data)
    fields = {'schema', 'operation', 'old_manifest', 'new_manifest', 'old_manifest_sha256',
              'new_manifest_sha256', 'old_manifest_base64', 'new_manifest_base64'}
    require(type(value) is dict and set(value) == fields and type(value['schema']) is int and
            type(value['operation']) is str and (value['schema'], value['operation']) in (
                (1, 'a733-guest-checkpoint-v1'), (2, 'a733-managed-guest-checkpoint-v2')),
            'invalid checkpoint record fields')
    old_raw = decode(value['old_manifest_base64'], MANIFEST.LIMIT)
    new_raw = decode(value['new_manifest_base64'], MANIFEST.LIMIT)
    old, new = MANIFEST.validate_manifest(old_raw), MANIFEST.validate_manifest(new_raw)
    MANIFEST.validate_manifest(canonical(value['old_manifest']))
    MANIFEST.validate_manifest(canonical(value['new_manifest']))
    old_sha, new_sha = sha(old_raw), sha(new_raw)
    require(value['old_manifest'] == old and value['new_manifest'] == new and
            value['old_manifest_sha256'] == old_sha and value['new_manifest_sha256'] == new_sha and
            name == old_sha + '-' + new_sha + '.json', 'checkpoint content/hash mismatch')
    if value['schema'] == 1:
        proposed = MANIFEST.proposed_manifest(old, old_sha, new['config']['disk'])
    else:
        require(new['schema'] == 2 and 'checkpoint' in new, 'managed checkpoint required')
        proposed = MANIFEST.proposed_managed_manifest(
            old, old_sha, new['config']['disk'], new['checkpoint']['shutdown_provenance'])
    require(new_raw == MANIFEST.canonical(new) and old != new and proposed == new,
            'invalid checkpoint transition')
    return old, new, old_raw


def validate_entries(entries, manifest_data):
    require(type(entries) is dict and len(entries) <= ENTRY_COUNT, 'invalid history entries')
    parsed = {name: validate_record(name, data) for name, data in entries.items()}
    current = MANIFEST.validate_manifest(manifest_data)
    seen = set()
    while 'checkpoint' in current:
        name = current['checkpoint']['previous_manifest_sha256'] + '-' + sha(MANIFEST.canonical(current)) + '.json'
        require(name not in seen and name in parsed and len(seen) < ENTRY_COUNT,
                'missing or cyclic checkpoint chain')
        seen.add(name)
        old, new, _raw = parsed[name]
        require(new == current, 'checkpoint chain content differs')
        current = old
    return entries


def history_payload(entries, manifest_data):
    validate_entries(entries, manifest_data)
    value = {'schema': 1, 'profile': HISTORY_PROFILE,
             'entries': [{'name': name, 'data_base64': base64.b64encode(entries[name]).decode('ascii')}
                         for name in sorted(entries)]}
    encoded = canonical(value)
    require(len(encoded) <= HISTORY_LIMIT, 'checkpoint history exceeds limit')
    return encoded


def validate_history(data, manifest_data):
    require(type(data) is bytes and 0 < len(data) <= HISTORY_LIMIT, 'invalid history size')
    value = parse_json(data)
    require(type(value) is dict and set(value) == {'schema', 'profile', 'entries'} and
            type(value['schema']) is int and value['schema'] == 1 and
            value['profile'] == HISTORY_PROFILE and type(value['entries']) is list and
            len(value['entries']) <= ENTRY_COUNT, 'invalid checkpoint history envelope')
    entries = {}
    previous = ''
    for entry in value['entries']:
        require(type(entry) is dict and set(entry) == {'name', 'data_base64'}, 'invalid history entry')
        name = entry['name']
        require(type(name) is str and name > previous and NAME_RE.fullmatch(name) is not None,
                'history names must be sorted and unique')
        previous = name
        entries[name] = decode(entry['data_base64'], ENTRY_LIMIT)
    validate_entries(entries, manifest_data)
    require(data == history_payload(entries, manifest_data), 'noncanonical history envelope')
    return entries
