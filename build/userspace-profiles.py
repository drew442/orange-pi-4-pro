"""Closed userspace inputs for production builders; no hardware acceptance claim.

The source pin and tracked lock must change together to authorize another proof.
resolve() performs read-only validation before callers create output directories.
"""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat

REPO = Path(__file__).resolve().parents[1]
DEFAULT = 'baseline-20261104'
PROFILES = (DEFAULT, 'normalized-20261106')
LOCK = 'configs/host-userspace-profiles.lock.json'
LOCK_SHA256 = 'ffa155f51cca41d4c791594f53829b79cc6c255cde06880137ed0c52cab40d58'
MAX_BYTES = 4 * 1024 * 1024
EVIDENCE = {
    DEFAULT: ('docs/evidence/host-userspace-20261104.json',),
    PROFILES[1]: ('docs/evidence/userspace-fresh-pipeline-20261106.json',
                  'docs/evidence/userspace-canonical-export-20261106.json'),
}
SOURCES = ('build/prepare-host-userspace.py', 'build/configure-host-userspace.py',
           'build/export-host-userspace.py', 'build/debootstrap-adapter.py',
           'build/offline-userspace-repo.py',
           'configs/host-userspace-packages.lock.tsv',
           'configs/host-userspace-package-hashes.lock.tsv',
           'configs/host-userspace-source-archives.lock.json')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_regular(repo, relative):
    """Walk every physical parent with dirfds; never follow a symlink or FIFO."""
    require(type(relative) is str and str(PurePosixPath(relative)) == relative
            and not relative.startswith('/') and '..' not in relative.split('/'),
            'invalid userspace input path')
    root = Path(repo)
    require(root.is_absolute() and '..' not in root.parts, 'repository must be absolute')
    components = root.parts[1:] + tuple(relative.split('/'))
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in components[:-1]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        source = os.open(components[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            before = os.fstat(source)
            require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                    and 0 < before.st_size <= MAX_BYTES, 'userspace input must be a bounded regular file')
            with os.fdopen(source, 'rb', closefd=False) as stream:
                data = stream.read(MAX_BYTES + 1)
            after = os.fstat(source)
            require(len(data) == before.st_size and len(data) <= MAX_BYTES
                    and (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                    == (after.st_size, after.st_mtime_ns, after.st_ctime_ns),
                    'userspace input changed during read')
            return data
        finally:
            os.close(source)
    finally:
        os.close(fd)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate userspace JSON key')
        result[key] = value
    return result


def parse(data):
    try:
        value = json.loads(data, object_pairs_hook=unique_object,
                           parse_constant=lambda value: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError('invalid userspace JSON') from error
    require(type(value) is dict, 'userspace JSON must be an object')
    return value


def fingerprint(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def pin(value):
    require(type(value) is dict and set(value) == {'bytes', 'sha256'}
            and type(value['bytes']) is int and value['bytes'] > 0
            and type(value['sha256']) is str
            and re.fullmatch('[0-9a-f]{64}', value['sha256']) is not None,
            'invalid userspace size/hash pin')


def agrees(actual, expected, label):
    """Typed subset comparison: JSON booleans cannot stand in for integers."""
    require(type(actual) is type(expected), label + ' type differs')
    if type(expected) is dict:
        for key, value in expected.items():
            require(key in actual, label + ' missing ' + key)
            agrees(actual[key], value, label + '.' + key)
    else:
        require(actual == expected, label + ' differs')


def validate_proof(profile, selected, proofs):
    archive = selected['archive']
    if profile == DEFAULT:
        agrees(proofs[0], {'schema': 1, 'packages_installed': 220,
                          'files': {'rootfs.tar.gz': archive}}, 'baseline evidence')
        return
    expected = {'schema': 1, 'success': True, 'archive': archive,
                'raw_tar': selected['raw_tar'], 'members': selected['members'],
                'inventory': selected['inventory'],
                'timestamps': {'epoch': selected['epoch'], 'native_source_metadata_verified': True}}
    fresh, canonical = proofs
    agrees(fresh, {'schema': 1, 'success': True,
                   'configuration': {'packages': 220, 'dpkg_audit_clean': True, 'apt_check_passed': True},
                   'export': expected,
                   'comparison': {'compressed_bytes_identical': True, 'bom_bytes_identical': True,
                                  'raw_tar_hash_identical': True},
                   'independent_review': {'success': True, 'archive': {
                       'archive': archive, 'raw_tar': selected['raw_tar'],
                       'members': selected['members'], 'inventory_exact': 220}}}, 'fresh evidence')
    agrees(canonical, {'schema': 1, 'success': True, 'archive_bytes_identical': True,
                       'raw_tar_hashes_identical': True, 'bom_bytes_identical': True,
                       'independent_review': {'success': True}}, 'canonical evidence')
    require(type(canonical.get('runs')) is list and len(canonical['runs']) == 2,
            'canonical evidence must contain two runs')
    for run in canonical['runs']:
        agrees(run, {'export': expected}, 'canonical run')


def resolve(profile=DEFAULT, repo=REPO):
    """Return archive pin, primary evidence, provenance paths and JSON binding."""
    require(type(profile) is str and profile in PROFILES, 'unknown userspace profile')
    repo = Path(repo)
    lock_data = read_regular(repo, LOCK)
    require(hashlib.sha256(lock_data).hexdigest() == LOCK_SHA256, 'userspace profile lock hash differs')
    lock = parse(lock_data)
    require(set(lock) == {'schema', 'profiles'} and type(lock['schema']) is int and lock['schema'] == 1
            and type(lock['profiles']) is dict and set(lock['profiles']) == set(PROFILES),
            'invalid closed userspace profile lock')
    selected = lock['profiles'][profile]
    keys = {'archive', 'evidence', 'sources'}
    if profile != DEFAULT:
        keys |= {'raw_tar', 'members', 'epoch', 'inventory'}
    require(type(selected) is dict and set(selected) == keys, 'invalid userspace profile fields')
    pin(selected['archive'])
    require(type(selected['evidence']) is dict and tuple(selected['evidence']) == EVIDENCE[profile],
            'invalid userspace evidence paths')
    expected_sources = () if profile == DEFAULT else SOURCES
    require(type(selected['sources']) is dict and tuple(selected['sources']) == expected_sources,
            'invalid userspace source paths')
    if profile != DEFAULT:
        pin(selected['raw_tar'])
        require(type(selected['members']) is int and selected['members'] == 15876
                and type(selected['epoch']) is int and selected['epoch'] == 1790812800,
                'invalid normalized members/epoch')
        agrees(selected['inventory'], {'packages': 220, 'lock_sha256':
               'f2abf03d0d6e7fd506560689093220514cbf1acc678f7560562026d6ca6564a6'}, 'inventory')
    proofs = []
    records = {str(repo / LOCK): fingerprint(lock_data)}
    for relative, expected in selected['evidence'].items():
        pin(expected)
        data = read_regular(repo, relative)
        require(fingerprint(data) == expected, 'userspace evidence size/hash differs: ' + relative)
        records[str(repo / relative)] = fingerprint(data)
        proofs.append(parse(data))
    for relative, expected in selected['sources'].items():
        pin(expected)
        actual = fingerprint(read_regular(repo, relative))
        require(actual == expected, 'userspace source size/hash differs: ' + relative)
        records[str(repo / relative)] = actual
    validate_proof(profile, selected, proofs)
    helper = 'build/userspace-profiles.py'
    helper_pin = fingerprint(read_regular(repo, helper))
    records[str(repo / helper)] = helper_pin
    paths = (helper, LOCK) + EVIDENCE[profile] + expected_sources
    return {'archive': dict(selected['archive']), 'evidence': repo / EVIDENCE[profile][0],
            'provenance': tuple(repo / path for path in paths), 'provenance_records': records,
            'binding': {'profile': profile, **selected, 'profile_lock': fingerprint(lock_data),
                        'resolver': helper_pin}}
