"""Pure bounded managed checkpoint records; parsing never grants authority.

Valid JSON does not authenticate a process, storage, shutdown, fixture, or cohort
and never authorizes execution or checkpoint publication. Root custody validates
those facts and actual cross-record resources separately. No runtime I/O occurs.
"""
import base64
import hashlib
import json
import math

# Use the same pure dependencies and import convention as managed-run permits.
import importlib.util
from pathlib import Path


def _module(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PERMIT = _module('a733_checkpoint_permit', 'guest_run_permit.py')
MANIFEST = _module('a733_checkpoint_manifest', 'guest_manifest.py')
BACKUP = _module('a733_checkpoint_backup', 'guest_backup.py')
HEAD = _module('a733_checkpoint_head', 'guest_lifecycle.py')
QMP = _module('a733_checkpoint_qmp', 'guest_qmp.py')
REGISTRY = PERMIT.REGISTRY
require = REGISTRY.require
MAX_DEPTH = 40
MAX_INTEGER = (1 << 63) - 1
OUTCOME_LIMIT = 10 * 1024 * 1024
INTENT_LIMIT = 2 * 1024 * 1024
COMPLETION_LIMIT = 64 * 1024
CONSOLE_LIMIT = 8 * 1024 * 1024
FIXTURE_LIMIT = 256 * 1024
OUTCOME_PROFILE = 'a733-managed-outcome-v1'
INTENT_PROFILE = 'a733-managed-checkpoint-intent-v1'
COMPLETION_PROFILE = 'a733-managed-checkpoint-completed-v1'
IDENTITY_FIELDS = {'storage', 'name', 'uid', 'gid', 'run_id', 'boot_id'}
COMMON_FIELDS = {'schema', 'profile'} | IDENTITY_FIELDS
OUTCOME_FIELDS = COMMON_FIELDS | {'admission_sha256', 'shutdown_profile',
    'fixture_profile', 'permit', 'shutdown', 'shutdown_sha256', 'console', 'fixture'}
INTENT_FIELDS = COMMON_FIELDS | {'admission_sha256', 'stopping_head', 'outcome',
    'console', 'fixture', 'old_manifest', 'new_manifest', 'disk', 'requires_update',
    'history', 'registry'}
COMPLETION_FIELDS = COMMON_FIELDS | {'intent_sha256', 'outcome_sha256', 'ready_head',
    'manifest_sha256', 'registry_generation', 'registry_sha256'}
SHUTDOWN_FIELDS = {'schema', 'profile', 'run_id', 'boot_id', 'permit_sha256',
    'stopping_head_sha256', 'raw_wait_status', 'qmp', 'console_bytes',
    'console_sha256', 'hardware_acceptance', 'checkpoint_authorized'}


def _consume(budget, size):
    budget[0] -= size
    require(budget[0] >= 0, 'record size limit exceeded')


def _string(value, budget):
    require(len(value) <= budget[0], 'record string size limit exceeded')
    _consume(budget, 2)
    for char in value:
        point = ord(char)
        require(not 0xd800 <= point <= 0xdfff, 'invalid Unicode surrogate')
        size = (2 if char in ('"', '\\', '\b', '\f', '\n', '\r', '\t') else
                6 if point < 32 else 1 if point < 128 else 2 if point < 2048 else
                3 if point < 65536 else 4)
        _consume(budget, size)


def _tree(value, depth=0, budget=None):
    """Preflight exact JSON encoding size before dumps; reject cycles by depth."""
    if budget is None:
        budget = [OUTCOME_LIMIT - 1]
    require(depth <= MAX_DEPTH, 'record nesting limit exceeded')
    if type(value) in (dict, list):
        require(depth < MAX_DEPTH, 'record nesting limit exceeded')
        _consume(budget, 2 + max(0, len(value) - 1))
    if type(value) is dict:
        for key, child in value.items():
            require(type(key) is str, 'JSON keys must be strings')
            _string(key, budget)
            _consume(budget, 1)
            _tree(child, depth + 1, budget)
    elif type(value) is list:
        for child in value:
            _tree(child, depth + 1, budget)
    elif type(value) is str:
        _string(value, budget)
    elif type(value) is bool:
        _consume(budget, 4 if value else 5)
    elif value is None:
        _consume(budget, 4)
    elif type(value) is int:
        require(value.bit_length() <= 63, 'JSON integer too large')
        _consume(budget, len(str(value)))
    elif type(value) is float:
        require(math.isfinite(value), 'nonfinite JSON number')
        _consume(budget, len(repr(value)))
    else:
        raise ValueError('invalid JSON value')


def _encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')


def _parse(data, limit):
    require(type(data) in (str, bytes) and 0 < len(data) <= limit, 'invalid record size/type')
    try:
        raw = data.encode('utf-8') if type(data) is str else data
        require(len(raw) <= limit, 'record size limit exceeded')
        text = raw.decode('utf-8')
        # Scan before the decoder allocates a nested tree.
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
                require(depth <= MAX_DEPTH, 'record nesting limit exceeded')
            elif char in ']}':
                depth -= 1
        value = json.loads(text, object_pairs_hook=REGISTRY.IDENTITY.unique_pairs,
                           parse_constant=REGISTRY._reject_constant)
        _tree(value, budget=[limit - 1])
        return value
    except (UnicodeError, RecursionError) as error:
        raise ValueError('invalid record encoding/nesting') from error


def _fields(value, fields, label):
    require(type(value) is dict and set(value) == fields, 'invalid ' + label + ' fields')


def _integer(value, lower, upper, label):
    require(type(value) is int and lower <= value <= upper, 'invalid ' + label)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _reference(value, limit, *, embedded=False, empty=True):
    fields = {'bytes', 'sha256'} | ({'data_base64'} if embedded else set())
    _fields(value, fields, 'reference')
    _integer(value['bytes'], 0 if empty else 1, limit, 'reference size')
    REGISTRY._digest(value['sha256'])
    if embedded:
        encoded = value['data_base64']
        require(type(encoded) is str and len(encoded) <= 4 * ((limit + 2) // 3),
                'base64 size limit exceeded')
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, UnicodeError) as error:
            raise ValueError('invalid base64') from error
        require(len(raw) == value['bytes'] and len(raw) <= limit and
                base64.b64encode(raw).decode('ascii') == encoded and
                _sha(raw) == value['sha256'], 'reference content mismatch')
        return raw


def _common(value, fields, profile):
    _fields(value, fields, 'record')
    require(type(value['schema']) is int and value['schema'] == 1 and
            type(value['profile']) is str and value['profile'] == profile,
            'invalid record schema/profile')
    REGISTRY.validate_registry(_encode({'schema': 1, 'profile': REGISTRY.PROFILE,
        'generation': 0, 'storage': value['storage'], 'guests': []}))
    REGISTRY.IDENTITY.validate_config(_encode({'schema': 1,
        'guests': [{key: value[key] for key in ('name', 'uid', 'gid')}]}))
    for key in ('run_id', 'boot_id'):
        REGISTRY._uuid(value[key])


def _identity(value, other, *, head=False):
    for key in IDENTITY_FIELDS:
        other_key = 'last_boot_id' if head and key == 'boot_id' else key
        require(value[key] == other[other_key], 'record identity mismatch: ' + key)


def _head(value, reference, status):
    raw = _reference(reference, HEAD.LIMIT, embedded=True, empty=False)
    head = HEAD.validate_head(raw)
    require(raw == HEAD.canonical(head) and head['status'] == status,
            'invalid canonical head/status')
    _identity(value, head, head=True)
    return head


def validate_outcome(data):
    value = _parse(data, OUTCOME_LIMIT)
    _common(value, OUTCOME_FIELDS, OUTCOME_PROFILE)
    REGISTRY._digest(value['admission_sha256'])
    require(value['shutdown_profile'] == 'qmp-guest-shutdown-v1' and
            type(value['shutdown_profile']) is str, 'unsupported shutdown profile')
    require(type(value['fixture_profile']) is str and value['fixture_profile'] in
            ('none', 'managed-power-key-v1'), 'unsupported fixture profile')
    raw = _reference(value['permit'], PERMIT.LIMIT, embedded=True, empty=False)
    permit = PERMIT.validate_permit(raw)
    require(raw == PERMIT.canonical(permit), 'noncanonical permit')
    _identity(value, {**permit, **{k: permit['config'][k] for k in ('name', 'uid', 'gid')}})
    _reference(value['console'], CONSOLE_LIMIT)
    if value['fixture_profile'] == 'none':
        require(value['fixture'] is None, 'unexpected fixture reference')
    else:
        _reference(value['fixture'], FIXTURE_LIMIT, empty=False)
    shutdown = value['shutdown']
    _fields(shutdown, SHUTDOWN_FIELDS, 'shutdown')
    require(type(shutdown['schema']) is int and shutdown['schema'] == 1 and
            shutdown['profile'] == 'a733-supervised-shutdown-v1', 'invalid shutdown schema/profile')
    for key in ('run_id', 'boot_id'):
        require(shutdown[key] == value[key], 'shutdown run/boot mismatch')
    require(shutdown['permit_sha256'] == value['permit']['sha256'], 'shutdown permit mismatch')
    REGISTRY._digest(shutdown['stopping_head_sha256'])
    _integer(shutdown['raw_wait_status'], 0, 0, 'raw wait status')
    _integer(shutdown['console_bytes'], 0, CONSOLE_LIMIT, 'console size')
    require(shutdown['console_bytes'] == value['console']['bytes'] and
            shutdown['console_sha256'] == value['console']['sha256'], 'shutdown console mismatch')
    require(shutdown['hardware_acceptance'] is False and
            shutdown['checkpoint_authorized'] is False, 'JSON cannot grant authority')
    REGISTRY._digest(value['shutdown_sha256'])
    require(_sha(_encode(shutdown)) == value['shutdown_sha256'], 'shutdown hash mismatch')
    require(type(shutdown['qmp']) is list and len(shutdown['qmp']) <= QMP.MAX_MESSAGES,
            'invalid QMP transcript')
    session = QMP.Session(value['run_id'])
    for item in shutdown['qmp']:
        _fields(item, {'direction', 'message'}, 'QMP transcript item')
        require(type(item['message']) is dict, 'invalid QMP message')
        if item['direction'] == 'send':
            require(session.command(item['message'].get('execute')) == item['message'],
                    'QMP command mismatch')
        else:
            require(item['direction'] == 'receive', 'invalid QMP direction')
            session.receive(item['message'])
        require(item['message'].get('event') not in ('RESET', 'GUEST_PANICKED'),
                'disqualifying QMP event')
    require(session.clean_shutdown_observed and session._pending is None and
            session._capabilities and 'cont' in session._sent,
            'complete acknowledged clean QMP shutdown required')
    return value


def validate_intent(data):
    value = _parse(data, INTENT_LIMIT)
    _common(value, INTENT_FIELDS, INTENT_PROFILE)
    REGISTRY._digest(value['admission_sha256'])
    head = _head(value, value['stopping_head'], 'stopping')
    _reference(value['outcome'], OUTCOME_LIMIT, empty=False)
    _reference(value['console'], CONSOLE_LIMIT)
    if value['fixture'] is not None:
        _reference(value['fixture'], FIXTURE_LIMIT, empty=False)
    old_raw = _reference(value['old_manifest'], MANIFEST.LIMIT, embedded=True, empty=False)
    new_raw = _reference(value['new_manifest'], MANIFEST.LIMIT, embedded=True, empty=False)
    old, new = MANIFEST.validate_manifest(old_raw), MANIFEST.validate_manifest(new_raw)
    require(old['guest_uuid'] == value['storage']['guest_uuid'], 'manifest storage mismatch')
    for key in ('name', 'uid', 'gid'):
        require(old['config'][key] == value[key], 'manifest identity mismatch')
    require(head['manifest_sha256'] == value['old_manifest']['sha256'], 'stopping manifest mismatch')
    _reference(value['disk'], MANIFEST.CONFIG.DISK_LIMIT, empty=False)
    require(value['disk']['bytes'] == old['config']['disk']['bytes'], 'disk resizing refused')
    require(type(value['requires_update']) is bool and value['requires_update'] ==
            (value['disk'] != old['config']['disk']), 'incorrect update classification')
    if value['requires_update']:
        require(new['schema'] == 2 and 'checkpoint' in new, 'managed manifest required')
        provenance = new['checkpoint']['shutdown_provenance']
        require(all(provenance[k] == value[k] for k in ('run_id', 'boot_id')) and
                provenance['evidence_sha256'] == value['outcome']['sha256'] and
                (provenance['fixture_profile'] == 'none') == (value['fixture'] is None),
                'managed provenance mismatch')
        proposed = MANIFEST.proposed_managed_manifest(
            old, value['old_manifest']['sha256'], value['disk'], provenance)
        require(new == proposed and new_raw == MANIFEST.canonical(new), 'proposed manifest mismatch')
        history_raw = _reference(value['history'], BACKUP.ENTRY_LIMIT, embedded=True, empty=False)
        history = _parse(history_raw, BACKUP.ENTRY_LIMIT)
        require(type(history) is dict and type(history.get('schema')) is int and
                history.get('schema') == 2 and history.get('operation') ==
                'a733-managed-guest-checkpoint-v2' and history_raw == BACKUP.canonical(history),
                'invalid managed history')
        name = value['old_manifest']['sha256'] + '-' + value['new_manifest']['sha256'] + '.json'
        h_old, h_new, h_raw = BACKUP.validate_record(name, history_raw)
        require(h_old == old and h_new == new and h_raw == old_raw and
                BACKUP.decode(history['new_manifest_base64'], MANIFEST.LIMIT) == new_raw,
                'history manifest mismatch')
    else:
        require(value['new_manifest'] == value['old_manifest'] and value['history'] is None,
                'no-op must preserve exact old manifest and omit history')
    registry = value['registry']
    _fields(registry, {'observed_generation', 'old_manifest_sha256', 'new_manifest_sha256'}, 'registry')
    _integer(registry['observed_generation'], 0, MAX_INTEGER, 'registry generation')
    require(registry['old_manifest_sha256'] == value['old_manifest']['sha256'] and
            registry['new_manifest_sha256'] == value['new_manifest']['sha256'], 'registry pin mismatch')
    return value


def validate_completion(data):
    value = _parse(data, COMPLETION_LIMIT)
    _common(value, COMPLETION_FIELDS, COMPLETION_PROFILE)
    for key in ('intent_sha256', 'outcome_sha256', 'manifest_sha256', 'registry_sha256'):
        REGISTRY._digest(value[key])
    _integer(value['registry_generation'], 0, MAX_INTEGER, 'registry generation')
    head = _head(value, value['ready_head'], 'ready')
    require(head['last_completed_intent_sha256'] == value['intent_sha256'] and
            head['manifest_sha256'] == value['manifest_sha256'], 'completion head mismatch')
    return value


def _canonical(value, validator, limit):
    _tree(value, budget=[limit - 1])
    normalized = validator(_encode(value))
    return _encode(normalized)


def canonical_outcome(value):
    return _canonical(value, validate_outcome, OUTCOME_LIMIT)


def canonical_intent(value):
    return _canonical(value, validate_intent, INTENT_LIMIT)


def canonical_completion(value):
    return _canonical(value, validate_completion, COMPLETION_LIMIT)
