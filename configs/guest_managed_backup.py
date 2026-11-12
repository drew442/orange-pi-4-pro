"""Pure managed backup framing; validated bytes never confer restore authority.

This distinct six-member profile never accepts legacy five-member archives as
managed authority. Registry enabled flags and unrelated entries are historical.
Root callers must authenticate admission schemas, legal current-run replay,
fixture evidence, actual shutdown/process/storage and the matching disk before
publishing a backup or a fresh destination cohort. This codec supplies none of
those capabilities and does not import the root admission/controller module.
"""
import base64
import importlib.util
from pathlib import Path
import re
import tarfile

_SPEC = importlib.util.spec_from_file_location(
    'a733_managed_backup_records', Path(__file__).with_name('guest_checkpoint_records.py'))
RECORDS = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(RECORDS)
BACKUP, MANIFEST, HEAD, REGISTRY = RECORDS.BACKUP, RECORDS.MANIFEST, RECORDS.HEAD, RECORDS.REGISTRY
require = REGISTRY.require
PROFILE = 'a733-managed-guest-backup-v1'
NAMES = ('enrollment.json', 'checkpoint-history.json', 'lifecycle.json',
         'Image', 'initramfs.cpio.gz', 'disk.raw')
LIFECYCLE_LIMIT = 40 * 1024 * 1024
HISTORY_LIMIT = BACKUP.HISTORY_LIMIT
RECORD_COUNT = 11
TRANSITION_LIMIT = 128 * 1024
TRANSITION_PROFILE = 'a733-guest-head-transition-v1'
TRANSITION_FIELDS = {'schema', 'profile', 'previous_head_sha256', 'new_head_sha256',
                     'previous_head_base64', 'new_head_base64'}
TRANSITION_RE = re.compile(r'transitions/([0-9a-f]{64})-([0-9a-f]{64})\.json')
MEMBER_LIMITS = dict(zip(NAMES, (MANIFEST.LIMIT, HISTORY_LIMIT, LIFECYCLE_LIMIT,
    MANIFEST.CONFIG.ARTIFACT_LIMIT, MANIFEST.CONFIG.ARTIFACT_LIMIT, MANIFEST.CONFIG.DISK_LIMIT)))
sha = BACKUP.sha


def _rawref(value, limit, *, empty=False):
    return RECORDS._reference(value, limit, embedded=True, empty=empty)


def _pin(raw):
    return {'bytes': len(raw), 'sha256': sha(raw)}


def _identity(value, head):
    RECORDS._identity(value, head, head=True)


def _transition(name, raw, head):
    """Check framing/predecessor pins, without authenticating legal head replay."""
    value = RECORDS._parse(raw, TRANSITION_LIMIT)
    RECORDS._fields(value, TRANSITION_FIELDS, 'head transition')
    require(type(value['schema']) is int and value['schema'] == 1 and
            value['profile'] == TRANSITION_PROFILE, 'invalid head transition profile')
    match = TRANSITION_RE.fullmatch(name)
    require(match is not None, 'invalid transition name')
    heads = []
    for index, prefix in enumerate(('previous', 'new')):
        raw_head = BACKUP.decode(value[prefix + '_head_base64'], HEAD.LIMIT)
        RECORDS._parse(raw_head, HEAD.LIMIT)
        parsed = HEAD.validate_head(raw_head)
        require(raw_head == HEAD.canonical(parsed) and
                sha(raw_head) == value[prefix + '_head_sha256'] == match[index + 1],
                'transition name/content/hash mismatch')
        require(all(parsed[key] == head[key] for key in ('storage', 'name', 'uid', 'gid')),
                'transition guest identity mismatch')
        heads.append(parsed)
    previous, new = heads
    require(new['sequence'] == previous['sequence'] + 1 and
            new['previous_head_sha256'] == value['previous_head_sha256'] and
            new['run_id'] == head['run_id'] and new['last_boot_id'] == head['last_boot_id'],
            'transition predecessor/run mismatch')
    require(raw == RECORDS._encode(value), 'noncanonical transition')


def _cohort(records, head, entry, manifest_data, registry, registry_raw):
    require(type(records) is dict and len(records) <= RECORD_COUNT, 'invalid lifecycle records')
    if head['sequence'] == 0:
        require(not records, 'initial head cannot have run records')
        return
    prefix = 'runs/' + head['run_id']
    limits = {prefix + '.json': HEAD.LIMIT, prefix + '.console': RECORDS.CONSOLE_LIMIT,
              prefix + '.outcome.json': RECORDS.OUTCOME_LIMIT,
              prefix + '.intent.json': RECORDS.INTENT_LIMIT,
              prefix + '.completed.json': RECORDS.COMPLETION_LIMIT}
    if entry['fixture_profile'] != 'none':
        limits[prefix + '.fixture.json'] = RECORDS.FIXTURE_LIMIT
    transitions = {name for name in records if type(name) is str and
                   TRANSITION_RE.fullmatch(name) is not None}
    require(len(transitions) == 5 and set(records) == set(limits) | transitions,
            'exact completed current-run cohort required')
    raw = {name: _rawref(reference, TRANSITION_LIMIT if name in transitions else limits[name],
                         empty=name == prefix + '.console')
           for name, reference in records.items()}
    for name in transitions:
        _transition(name, raw[name], head)
    # Admission structure/provenance must be checked by the external root codec.
    # Parsing here only bounds JSON resources and rejects ambiguous encoding.
    RECORDS._parse(raw[prefix + '.json'], HEAD.LIMIT)
    admission_pin = sha(raw[prefix + '.json'])
    outcome_raw, intent_raw, completion_raw = (raw[prefix + suffix] for suffix in
        ('.outcome.json', '.intent.json', '.completed.json'))
    outcome = RECORDS.validate_outcome(outcome_raw)
    intent = RECORDS.validate_intent(intent_raw)
    completion = RECORDS.validate_completion(completion_raw)
    for value, data, encoder in ((outcome, outcome_raw, RECORDS.canonical_outcome),
                                (intent, intent_raw, RECORDS.canonical_intent),
                                (completion, completion_raw, RECORDS.canonical_completion)):
        _identity(value, head)
        require(data == encoder(value), 'noncanonical cohort record')
    require(outcome['admission_sha256'] == intent['admission_sha256'] == admission_pin and
            intent['outcome'] == _pin(outcome_raw) and
            completion['intent_sha256'] == sha(intent_raw) == head['last_completed_intent_sha256'] and
            completion['outcome_sha256'] == sha(outcome_raw), 'cohort record pin mismatch')
    require(completion['ready_head'] == records_head_reference(head) and
            completion['manifest_sha256'] == sha(manifest_data) and
            _rawref(intent['new_manifest'], MANIFEST.LIMIT) == manifest_data,
            'cohort current head/manifest mismatch')
    require(outcome['console'] == intent['console'] == _pin(raw[prefix + '.console']) and
            outcome['fixture'] == intent['fixture'] and
            outcome['shutdown_profile'] == entry['shutdown_profile'] and
            outcome['fixture_profile'] == entry['fixture_profile'] and
            outcome['shutdown']['stopping_head_sha256'] == intent['stopping_head']['sha256'],
            'cohort console/fixture/shutdown mismatch')
    if entry['fixture_profile'] != 'none':
        require(intent['fixture'] == _pin(raw[prefix + '.fixture.json']), 'fixture pin mismatch')
        RECORDS._parse(raw[prefix + '.fixture.json'], RECORDS.FIXTURE_LIMIT)
    require(intent['registry']['observed_generation'] <= completion['registry_generation']
            <= registry['generation'], 'completion registry generation is outside source intent bounds')
    if completion['registry_generation'] == registry['generation']:
        require(completion['registry_sha256'] == sha(registry_raw), 'completion registry pin mismatch')
    old = MANIFEST.validate_manifest(_rawref(intent['old_manifest'], MANIFEST.LIMIT))
    permit = RECORDS.PERMIT.validate_permit(_rawref(outcome['permit'], RECORDS.PERMIT.LIMIT))
    require(permit['config'] == old['config'] and
            permit['manifest_sha256'] == intent['old_manifest']['sha256'],
            'permit original manifest mismatch')


def records_head_reference(head):
    """Return a raw reference; this function grants no head custody."""
    raw = HEAD.canonical(head)
    return {**_pin(raw), 'data_base64': base64.b64encode(raw).decode('ascii')}


def validate_lifecycle(data, manifest_data):
    """Return a plain validated envelope, preserving every exact embedded byte.

    No returned object is authenticated or authorizes restore, enable or launch.
    The root wrapper must validate admission and replay the complete legal cohort.
    """
    require(type(data) is bytes, 'lifecycle must be bytes')
    require(type(manifest_data) is bytes and 0 < len(manifest_data) <= MANIFEST.LIMIT,
            'enrollment must be bounded exact bytes')
    value = RECORDS._parse(data, LIFECYCLE_LIMIT)
    RECORDS._fields(value, {'schema', 'profile', 'registry', 'head', 'records'}, 'lifecycle')
    require(type(value['schema']) is int and value['schema'] == 1 and
            value['profile'] == PROFILE, 'invalid lifecycle schema/profile')
    registry_raw = _rawref(value['registry'], REGISTRY.LIMIT)
    head_raw = _rawref(value['head'], HEAD.LIMIT)
    RECORDS._parse(registry_raw, REGISTRY.LIMIT)
    RECORDS._parse(head_raw, HEAD.LIMIT)
    RECORDS._parse(manifest_data, MANIFEST.LIMIT)
    registry, head = REGISTRY.validate_registry(registry_raw), HEAD.validate_head(head_raw)
    manifest = MANIFEST.validate_manifest(manifest_data)
    require(registry_raw == REGISTRY.canonical(registry) and head_raw == HEAD.canonical(head),
            'noncanonical source registry/head')
    entries = [guest for guest in registry['guests'] if guest['name'] == manifest['config']['name']]
    require(len(entries) == 1, 'source guest not registered')
    entry = entries[0]
    require(head['status'] == 'ready' and head['storage'] == registry['storage'] and
            manifest['guest_uuid'] == registry['storage']['guest_uuid'] and
            all(entry[key] == head[key] == manifest['config'][key] for key in ('name', 'uid', 'gid')) and
            entry['manifest_sha256'] == head['manifest_sha256'] == sha(manifest_data),
            'source registry/head/enrollment mismatch')
    _cohort(value['records'], head, entry, manifest_data, registry, registry_raw)
    require(data == RECORDS._encode(value), 'noncanonical lifecycle envelope')
    return value


def canonical(value, manifest_data):
    """Preflight size/depth/Unicode before encoding, then validate the envelope."""
    RECORDS._tree(value, budget=[LIFECYCLE_LIMIT - 1])
    raw = RECORDS._encode(value)
    validate_lifecycle(raw, manifest_data)
    return raw


def make_header(name, size):
    require(type(name) is str and name in MEMBER_LIMITS and type(size) is int and
            0 <= size <= MEMBER_LIMITS[name], 'invalid managed backup header name/size')
    header = tarfile.TarInfo(name)
    header.size, header.mode = size, 0o400
    header.uid = header.gid = header.mtime = 0
    header.uname = header.gname = ''
    return header.tobuf(format=tarfile.GNU_FORMAT, encoding='ascii', errors='strict')


read_exact = BACKUP.read_exact


def read_header(stream, expected_name, max_size=None):
    require(type(expected_name) is str and expected_name in MEMBER_LIMITS,
            'invalid managed backup member name')
    limit = MEMBER_LIMITS[expected_name]
    if max_size is not None:
        require(type(max_size) is int and 0 <= max_size <= limit, 'invalid managed member limit')
        limit = max_size
    block = read_exact(stream, 512)
    try:
        header = tarfile.TarInfo.frombuf(block, 'ascii', 'strict')
    except (tarfile.TarError, UnicodeError, ValueError) as error:
        raise ValueError('invalid managed backup tar header') from error
    require(header.name == expected_name and header.type == tarfile.REGTYPE and
            0 <= header.size <= limit, 'unexpected managed backup member')
    require(block == make_header(expected_name, header.size), 'noncanonical managed backup header')
    return header.size


def read_padding(stream, size):
    require(type(size) is int and 0 <= size <= MANIFEST.CONFIG.DISK_LIMIT,
            'invalid managed member size')
    padding = (-size) % 512
    require(read_exact(stream, padding) == bytes(padding), 'nonzero managed backup padding')


def read_trailer(stream):
    """Require exactly two zero blocks then EOF; extra members/records refuse."""
    require(read_exact(stream, 1024) == bytes(1024), 'invalid managed backup trailer')
    require(stream.read(1) == b'', 'trailing managed backup data')
