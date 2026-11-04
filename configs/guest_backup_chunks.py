"""Canonical externally pinned hashes of encoded guest-backup chunks."""
import hashlib
import json
import re

CHUNK = 1048576
MAX_ARCHIVE = 251 * 1024**3
LIMIT = 32 * 1024**2
PROFILE = 'a733-guest-backup-chunks-v1'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(value):
    require(type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None,
            'invalid SHA-256')
    return value


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate JSON key')
        result[key] = value
    return result


def reject_constant(_value):
    raise ValueError('nonfinite JSON value')


def validate(data, expected_sha256=None):
    require(type(data) is bytes and 0 < len(data) <= LIMIT, 'invalid chunk index size')
    if expected_sha256 is not None:
        digest(expected_sha256)
        require(hashlib.sha256(data).hexdigest() == expected_sha256, 'chunk index digest mismatch')
    try:
        value = json.loads(data.decode('utf-8'), object_pairs_hook=unique_pairs,
                           parse_constant=reject_constant)
    except (UnicodeError, RecursionError) as error:
        raise ValueError('invalid chunk index encoding or nesting') from error
    require(type(value) is dict and set(value) == {
        'schema', 'profile', 'archive', 'manifest_sha256', 'chunk_bytes', 'chunks'},
        'invalid chunk index fields')
    require(type(value['schema']) is int and value['schema'] == 1 and
            value['profile'] == PROFILE and type(value['chunk_bytes']) is int and
            value['chunk_bytes'] == CHUNK, 'invalid chunk index profile/schema')
    archive = value['archive']
    require(type(archive) is dict and set(archive) == {'bytes', 'sha256'} and
            type(archive['bytes']) is int and 1 <= archive['bytes'] <= MAX_ARCHIVE,
            'invalid chunk archive record')
    digest(archive['sha256'])
    digest(value['manifest_sha256'])
    chunks = value['chunks']
    require(type(chunks) is list and len(chunks) == (archive['bytes'] + CHUNK - 1) // CHUNK,
            'invalid chunk count')
    for item in chunks:
        digest(item)
    require(data == canonical(value), 'noncanonical chunk index')
    return value


def make(archive_record, manifest_sha256, chunks):
    data = canonical({'schema': 1, 'profile': PROFILE, 'archive': archive_record,
                      'manifest_sha256': manifest_sha256, 'chunk_bytes': CHUNK,
                      'chunks': chunks})
    validate(data)
    return data
