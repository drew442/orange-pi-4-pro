"""Pure authenticated enrollment/checkpoint manifest schema (authentication is external)."""
import copy
import importlib.util
import json
from pathlib import Path
import re
import uuid

_SPEC = importlib.util.spec_from_file_location('manifest_guest_config', Path(__file__).with_name('guest_config.py'))
CONFIG = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(CONFIG)
require = CONFIG.require
LIMIT = 65536
PROFILE = 'a733-guest-enrollment-v1'
FILENAMES = {'kernel': 'Image', 'initrd': 'initramfs.cpio.gz', 'disk': 'disk.raw'}


def digest(value):
    require(type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None,
            'invalid SHA-256')
    return value


def validate_manifest(data):
    require(isinstance(data, (str, bytes)), 'manifest must be bytes or text')
    try:
        encoded = data.encode('utf-8') if isinstance(data, str) else data
        require(0 < len(encoded) <= LIMIT, 'invalid manifest size')
        value = json.loads(encoded.decode('utf-8'), object_pairs_hook=CONFIG.IDENTITY.unique_pairs,
                           parse_constant=CONFIG._reject_constant)
    except (UnicodeError, RecursionError) as error:
        raise ValueError('invalid manifest encoding or nesting') from error
    fields = {'schema', 'profile', 'config', 'guest_uuid', 'files'}
    require(type(value) is dict and fields <= set(value) and
            set(value) <= fields | {'checkpoint', 'restoration'}, 'invalid manifest fields')
    require(type(value['schema']) is int and value['schema'] == 1 and value['profile'] == PROFILE,
            'invalid manifest profile/schema')
    config = CONFIG.validate_config(json.dumps(value['config'], allow_nan=False))
    identifier = value['guest_uuid']
    require(type(identifier) is str, 'invalid guest UUID')
    try:
        parsed = uuid.UUID(identifier)
    except (ValueError, AttributeError) as error:
        raise ValueError('invalid guest UUID') from error
    require(str(parsed) == identifier and parsed.int != 0, 'canonical nonzero UUID required')
    files = value['files']
    require(type(files) is dict and set(files) == set(FILENAMES.values()), 'invalid manifest files')
    for key, filename in FILENAMES.items():
        record = files[filename]
        uid, gid, mode = (config['uid'], config['gid'], '0600') if key == 'disk' else (0, 0, '0444')
        expected = {**config[key], 'uid': uid, 'gid': gid, 'mode': mode}
        require(type(record) is dict and set(record) == set(expected) and
                all(type(record[k]) is type(v) and record[k] == v for k, v in expected.items()),
                'manifest file/config metadata differs')
    if 'checkpoint' in value:
        checkpoint = value['checkpoint']
        require(type(checkpoint) is dict and set(checkpoint) == {
            'number', 'previous_manifest_sha256', 'previous_disk_sha256', 'clean_shutdown_attested'},
            'invalid checkpoint fields')
        require(type(checkpoint['number']) is int and 1 <= checkpoint['number'] <= 2147483647,
                'invalid checkpoint number')
        digest(checkpoint['previous_manifest_sha256'])
        digest(checkpoint['previous_disk_sha256'])
        require(checkpoint['clean_shutdown_attested'] is True, 'clean shutdown attestation required')
    if 'restoration' in value:
        restoration = value['restoration']
        require(type(restoration) is dict and set(restoration) == {
            'archive_sha256', 'manifest_sha256', 'guest_uuid'}, 'invalid restoration fields')
        digest(restoration['archive_sha256'])
        digest(restoration['manifest_sha256'])
        source_uuid = restoration['guest_uuid']
        require(type(source_uuid) is str, 'invalid restoration UUID')
        try:
            parsed_source = uuid.UUID(source_uuid)
        except (ValueError, AttributeError) as error:
            raise ValueError('invalid restoration UUID') from error
        require(str(parsed_source) == source_uuid and parsed_source.int != 0,
                'canonical nonzero restoration UUID required')
    value['config'] = config
    return value


def canonical(value):
    data = (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()
    normalized = validate_manifest(data)
    return (json.dumps(normalized, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def proposed_manifest(current_manifest, old_manifest_sha, new_disk_record):
    current = validate_manifest(canonical(current_manifest))
    digest(old_manifest_sha)
    require(type(new_disk_record) is dict and set(new_disk_record) == {'bytes', 'sha256'},
            'invalid new disk record')
    require(type(new_disk_record['bytes']) is int and
            new_disk_record['bytes'] == current['config']['disk']['bytes'], 'disk resizing refused')
    digest(new_disk_record['sha256'])
    if new_disk_record == current['config']['disk']:
        return current
    proposed = copy.deepcopy(current)
    proposed['config']['disk']['sha256'] = new_disk_record['sha256']
    proposed['files']['disk.raw']['sha256'] = new_disk_record['sha256']
    proposed['checkpoint'] = {
        'number': current.get('checkpoint', {}).get('number', 0) + 1,
        'previous_manifest_sha256': old_manifest_sha,
        'previous_disk_sha256': current['config']['disk']['sha256'],
        'clean_shutdown_attested': True,
    }
    return validate_manifest(canonical(proposed))


def restored_manifest(source_manifest, archive_sha256, manifest_sha256, destination_guest_uuid):
    """Rebase an authenticated backup onto a fresh destination installation.

    The caller authenticates the encoded archive and exact source manifest first.
    Old history is retained separately; the restored base starts a new checkpoint
    chain, linking its exact predecessor through external backup provenance.
    """
    current = validate_manifest(canonical(source_manifest))
    digest(archive_sha256)
    digest(manifest_sha256)
    restored = copy.deepcopy(current)
    restored.pop('checkpoint', None)
    restored['guest_uuid'] = destination_guest_uuid
    restored['restoration'] = {'archive_sha256': archive_sha256,
                               'manifest_sha256': manifest_sha256,
                               'guest_uuid': current['guest_uuid']}
    return validate_manifest(canonical(restored))
