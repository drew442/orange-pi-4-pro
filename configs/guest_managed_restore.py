"""Bounded restore provenance data. No JSON record conveys live authority."""
import base64
import hashlib
import importlib.util
from pathlib import Path
import json
import re
import uuid

PROFILE = 'a733-managed-restore-v1'
LIMIT = 4 * 1024 * 1024
FILE_LIMIT = 10 * 1024 * 1024
COUNT = 4096
_SPEC = importlib.util.spec_from_file_location('restore_record_registry', Path(__file__).with_name('guest_registry.py'))
REGISTRY = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(REGISTRY)


def require(value, message):
    if not value:
        raise ValueError(message)


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True) + '\n').encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def reference(data):
    return dict(bytes=len(data), sha256=digest(data))


def pin(value):
    require(type(value) is str and re.fullmatch('[0-9a-f]{64}', value), 'invalid restore hash')


def transaction_id(value):
    require(type(value) is str and str(uuid.UUID(value)) == value and
            uuid.UUID(value).version == 4, 'canonical restore UUID4 required')
    return value


def fields(value, expected):
    require(type(value) is dict and set(value) == set(expected), 'restore fields differ')


def ref(value):
    fields(value, ('bytes', 'sha256'))
    require(type(value['bytes']) is int and 0 <= value['bytes'] <= 2**63 - 1, 'invalid restore bytes')
    pin(value['sha256'])


def inode(value):
    require(type(value) is list and len(value) == 2 and
            all(type(n) is int and 0 <= n < 2**64 for n in value), 'invalid restore inode')


def observation(value):
    if value is None:
        return
    fields(value, ('inode', 'directories', 'files'))
    inode(value['inode'])
    require(type(value['directories']) is dict and type(value['files']) is dict and
            1 <= len(value['directories']) <= 4 and len(value['files']) <= COUNT,
            'invalid restore tree bounds')
    require('' in value['directories'], 'missing restore tree root')
    for name, item in list(value['directories'].items()) + list(value['files'].items()):
        require(type(name) is str and len(name) <= 220 and
                (name == '' or re.fullmatch('[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?', name)) and
                all(part not in ('.', '..') for part in name.split('/')), 'invalid restore tree path')
        isdir = name in value['directories']
        fields(item, ('inode', 'uid', 'gid', 'mode') if isdir else
               ('inode', 'uid', 'gid', 'mode', 'bytes', 'sha256'))
        inode(item['inode'])
        for key in ('uid', 'gid', 'mode'):
            require(type(item[key]) is int and 0 <= item[key] < 2**32, 'invalid restore metadata')
        require(item['mode'] <= 0o777, 'invalid restore permissions')
        if not isdir:
            ref({k: item[k] for k in ('bytes', 'sha256')})
    require(value['directories']['']['inode'] == value['inode'], 'restore root inode differs')


def decode(data):
    require(type(data) is bytes and 0 < len(data) <= LIMIT, 'restore record size differs')
    def pairs(rows):
        result = {}
        for key, item in rows:
            require(key not in result, 'duplicate restore JSON key')
            result[key] = item
        return result
    value = json.loads(data, object_pairs_hook=pairs)
    require(canonical(value) == data, 'noncanonical restore record')
    require(type(value) is dict, 'restore record must be object')
    require(value.get('schema') == 1 and type(value.get('schema')) is int and
            value.get('profile') == PROFILE, 'restore profile differs')
    transaction_id(value.get('transaction_id'))
    kind = value.get('kind')
    common = ('schema', 'profile', 'transaction_id', 'kind')
    if kind == 'intent':
        fields(value, (*common, 'storage', 'name', 'entry', 'source', 'old_registry', 'new_registry',
                       'old', 'new', 'p5_inode', 'state_inode', 'lifecycle_parent'))
        require(type(value['name']) is str and re.fullmatch('[a-z][a-z0-9-]{0,30}', value['name']) and
                value['name'] != 'firstkvm', 'invalid restore guest')
        fields(value['source'], ('archive', 'manifest_sha256', 'history', 'lifecycle', 'registry_generation'))
        for key in ('archive', 'history', 'lifecycle'):
            ref(value['source'][key])
        pin(value['source']['manifest_sha256'])
        require(type(value['source']['registry_generation']) is int and
                0 <= value['source']['registry_generation'] <= REGISTRY.MAX_GENERATION,
                'invalid archived registry generation')
        for key in ('old_registry', 'new_registry'):
            fields(value[key], ('bytes', 'sha256', 'base64'))
            raw = base64.b64decode(value[key]['base64'], validate=True)
            require(len(raw) <= 65536 and base64.b64encode(raw).decode() == value[key]['base64'] and
                    reference(raw) == {k: value[key][k] for k in ('bytes', 'sha256')},
                    'registry reference differs')
        for side in ('old', 'new'):
            fields(value[side], ('payload', 'lifecycle'))
            for item in value[side].values():
                observation(item)
        require(all(value['new'][key] is not None for key in ('payload', 'lifecycle')),
                'missing staged restore object')
        inode(value['p5_inode'])
        inode(value['state_inode'])
        fields(value['lifecycle_parent'], ('old_inode', 'new_inode'))
        parent = value['lifecycle_parent']
        require((parent['old_inode'] is None) != (parent['new_inode'] is None),
                'restore shared parent observation differs')
        inode(parent['new_inode'] if parent['old_inode'] is None else parent['old_inode'])
        if parent['old_inode'] is None:
            require(value['old']['lifecycle'] is None, 'absent shared parent has old lifecycle')
        require(type(value['storage']) is dict and type(value['entry']) is dict, 'restore identity differs')
        oldraw, newraw = (registry_bytes(value[key]) for key in ('old_registry', 'new_registry'))
        old, new = REGISTRY.validate_registry(oldraw), REGISTRY.validate_registry(newraw)
        require(REGISTRY.canonical(old) == oldraw and REGISTRY.canonical(new) == newraw and
                old['storage'] == new['storage'] == value['storage'], 'restore registry storage differs')
        targets = [entry for entry in old['guests'] if entry['name'] == value['name']]
        require(targets == [value['entry']], 'restore registered identity differs')
        expected = dict(old, generation=max(old['generation'], value['source']['registry_generation']) + 1,
                        guests=[dict(entry, manifest_sha256=value['source']['manifest_sha256'])
                                if entry['name'] == value['name'] else entry for entry in old['guests']])
        require(expected == new, 'restore registry derivation differs')
    elif kind in ('phase', 'data_commit'):
        fields(value, (*common, 'intent_sha256', 'phase'))
        pin(value['intent_sha256'])
        require(type(value['phase']) is int and 1 <= value['phase'] <= 6 and
                (value['phase'] == 6) == (kind == 'data_commit'), 'invalid restore phase')
    else:
        raise ValueError('invalid restore kind')
    return value


def registry_bytes(value):
    return base64.b64decode(value['base64'], validate=True)


def registry_reference(raw):
    return dict(**reference(raw), base64=base64.b64encode(raw).decode('ascii'))
