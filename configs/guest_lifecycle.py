"""Pure lifecycle head schema and compare-and-swap transitions.

No function authenticates storage, manifests, clean shutdown, checkpoint history
or durable evidence. Callers must authenticate the complete durable cohort before
invoking a transition, and separately serialize and publish its result. Local
hashes and valid JSON alone cannot establish freshness or authorize a guest run.
"""
import hashlib
import importlib.util
import json
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    'a733_lifecycle_registry', Path(__file__).with_name('guest_registry.py'))
REGISTRY = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(REGISTRY)
require = REGISTRY.require
LIMIT = 65536
MAX_DEPTH = 8
MAX_SEQUENCE = (1 << 63) - 1
PROFILE = 'a733-guest-head-v1'
FIELDS = {'schema', 'profile', 'storage', 'name', 'uid', 'gid', 'sequence',
          'manifest_sha256', 'status', 'last_boot_id', 'run_id', 'intent_sha256',
          'last_completed_intent_sha256', 'previous_head_sha256', 'failure_sha256'}
STATUSES = {'ready', 'admitted', 'running', 'stopping', 'checkpoint', 'quarantined'}


def validate_head(data):
    """Parse bounded UTF-8 JSON, rejecting ambiguous or unsupported records."""
    require(isinstance(data, (bytes, str)), 'head must be bytes or text')
    try:
        encoded = data.encode('utf-8') if isinstance(data, str) else data
        require(0 < len(encoded) <= LIMIT, 'invalid head size')
        text = encoded.decode('utf-8')
        REGISTRY._check_depth(text)
        value = json.loads(text, object_pairs_hook=REGISTRY.IDENTITY.unique_pairs,
                           parse_constant=REGISTRY._reject_constant)
    except (UnicodeError, RecursionError) as error:
        raise ValueError('invalid head encoding or nesting') from error
    require(type(value) is dict and set(value) == FIELDS, 'invalid head fields')
    require(type(value['schema']) is int and value['schema'] == 1 and
            type(value['profile']) is str and value['profile'] == PROFILE,
            'invalid head schema/profile')
    REGISTRY.validate_registry(json.dumps({
        'schema': 1, 'profile': REGISTRY.PROFILE, 'generation': 0,
        'storage': value['storage'], 'guests': [],
    }, allow_nan=False))
    REGISTRY.IDENTITY.validate_config(json.dumps({
        'schema': 1, 'guests': [{key: value[key] for key in ('name', 'uid', 'gid')}],
    }, allow_nan=False))
    sequence = value['sequence']
    require(type(sequence) is int and 0 <= sequence <= MAX_SEQUENCE,
            'invalid head sequence')
    require(type(value['status']) is str and value['status'] in STATUSES,
            'invalid head status')
    REGISTRY._digest(value['manifest_sha256'])
    for key in ('intent_sha256', 'last_completed_intent_sha256',
                'previous_head_sha256', 'failure_sha256'):
        if value[key] is not None:
            REGISTRY._digest(value[key])
    for key in ('last_boot_id', 'run_id'):
        if value[key] is not None:
            REGISTRY._uuid(value[key])
    require((value['last_boot_id'] is None) == (value['run_id'] is None),
            'run and boot IDs must be present together')
    if sequence == 0:
        require(value['status'] == 'ready' and all(value[key] is None for key in (
            'previous_head_sha256', 'last_boot_id', 'run_id', 'intent_sha256',
            'last_completed_intent_sha256', 'failure_sha256')), 'invalid initial head')
    else:
        require(value['previous_head_sha256'] is not None, 'previous head hash required')
        if value['status'] == 'ready':
            require(value['run_id'] is not None and
                    value['last_completed_intent_sha256'] is not None,
                    'noninitial ready head requires retained run and completed intent')
    if (value['intent_sha256'] is not None or
            value['last_completed_intent_sha256'] is not None):
        require(value['run_id'] is not None,
                'intent requires retained run and boot IDs')
    if value['status'] in ('admitted', 'running', 'stopping', 'checkpoint'):
        require(value['run_id'] is not None, 'active head requires run and boot IDs')
    if value['status'] == 'checkpoint':
        require(value['intent_sha256'] is not None, 'checkpoint intent required')
    elif value['status'] != 'quarantined':
        require(value['intent_sha256'] is None, 'unexpected checkpoint intent')
    require((value['failure_sha256'] is not None) == (value['status'] == 'quarantined'),
            'failure hash is required only in quarantine')
    return value


def canonical(value):
    """Return validated canonical UTF-8 JSON with a final newline."""
    try:
        data = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (TypeError, OverflowError, RecursionError) as error:
        raise ValueError('invalid head value') from error
    normalized = validate_head(data)
    return (json.dumps(normalized, sort_keys=True, separators=(',', ':'),
                       allow_nan=False) + '\n').encode('utf-8')


def initial_head(storage, identity, manifest_sha256):
    """Build an initial head from externally authenticated enrollment inputs."""
    require(type(identity) is dict and set(identity) == {'name', 'uid', 'gid'},
            'invalid identity fields')
    return validate_head(canonical({
        'schema': 1, 'profile': PROFILE, 'storage': storage, **identity,
        'sequence': 0, 'manifest_sha256': manifest_sha256, 'status': 'ready',
        'last_boot_id': None, 'run_id': None, 'intent_sha256': None,
        'last_completed_intent_sha256': None, 'previous_head_sha256': None,
        'failure_sha256': None,
    }))


def _current(head, expected_head_sha256):
    REGISTRY._digest(expected_head_sha256)
    data = canonical(head)
    require(hashlib.sha256(data).hexdigest() == expected_head_sha256,
            'head hash differs')
    return validate_head(data)


def _advance(current, **changes):
    require(current['sequence'] < MAX_SEQUENCE, 'head sequence exhausted')
    previous = hashlib.sha256(canonical(current)).hexdigest()
    current.update(changes)
    current['sequence'] += 1
    current['previous_head_sha256'] = previous
    return validate_head(canonical(current))


def admit(head, manifest_sha256, run_id, boot_id, *, expected_head_sha256, automatic=False):
    """Admit a caller-authorized run; automatic admission is once per boot.

    Reject reuse of the retained run ID. Historical UUID uniqueness requires the
    durable I/O layer to create the protected per-run record exclusively (O_EXCL).
    """
    current = _current(head, expected_head_sha256)
    require(type(automatic) is bool, 'automatic must be boolean')
    require(current['status'] == 'ready', 'admission requires ready head')
    REGISTRY._digest(manifest_sha256)
    require(current['manifest_sha256'] == manifest_sha256, 'manifest pin differs')
    REGISTRY._uuid(run_id)
    REGISTRY._uuid(boot_id)
    require(not automatic or boot_id != current['last_boot_id'], 'boot already admitted')
    require(run_id != current['run_id'], 'new run ID required')
    return _advance(current, status='admitted', run_id=run_id, last_boot_id=boot_id)


def mark_running(head, *, expected_head_sha256):
    """Record externally proven bound child/QMP readiness before guest execution."""
    current = _current(head, expected_head_sha256)
    require(current['status'] == 'admitted', 'running requires admitted head')
    return _advance(current, status='running')


def mark_stopping(head, *, expected_head_sha256):
    """Record a caller-authenticated stop request or observed self-shutdown."""
    current = _current(head, expected_head_sha256)
    require(current['status'] == 'running', 'stopping requires running head')
    return _advance(current, status='stopping')


def mark_checkpoint(head, intent_sha256, *, expected_head_sha256):
    """Record an externally authenticated durable checkpoint intent."""
    current = _current(head, expected_head_sha256)
    require(current['status'] in ('running', 'stopping'), 'checkpoint requires active head')
    REGISTRY._digest(intent_sha256)
    return _advance(current, status='checkpoint', intent_sha256=intent_sha256)


def complete(head, intent_sha256, manifest_sha256, *, expected_head_sha256):
    """Consume an authenticated completed cohort, retaining the admitted boot ID."""
    current = _current(head, expected_head_sha256)
    REGISTRY._digest(intent_sha256)
    REGISTRY._digest(manifest_sha256)
    if (current['status'] == 'ready' and
            current['last_completed_intent_sha256'] == intent_sha256 and
            current['manifest_sha256'] == manifest_sha256):
        return current
    require(current['status'] == 'checkpoint' and current['intent_sha256'] == intent_sha256,
            'matching checkpoint intent required')
    return _advance(current, status='ready', manifest_sha256=manifest_sha256,
                    intent_sha256=None, last_completed_intent_sha256=intent_sha256)


def quarantine(head, failure_sha256, *, expected_head_sha256):
    """Retain a caller-authenticated failure record; there is no reset operation."""
    current = _current(head, expected_head_sha256)
    REGISTRY._digest(failure_sha256)
    if current['status'] == 'quarantined':
        require(current['failure_sha256'] == failure_sha256, 'quarantine failure differs')
        return current
    return _advance(current, status='quarantined', failure_sha256=failure_sha256)


def merge_registry(current, name, oldpin, newpin):
    """Merge one authenticated intent into the latest registry under caller's lock.

    Preserve all enabled flags and unrelated entries. This does not authenticate
    the intent or publish the registry; callers must reread it under their lock.
    """
    result = REGISTRY.validate_registry(REGISTRY.canonical(current))
    require(type(name) is str, 'invalid guest name')
    REGISTRY._digest(oldpin)
    REGISTRY._digest(newpin)
    matches = [guest for guest in result['guests'] if guest['name'] == name]
    require(len(matches) == 1, 'guest is not registered')
    pin = matches[0]['manifest_sha256']
    if pin == newpin:
        return result
    require(pin == oldpin, 'registry manifest pin conflicts')
    return REGISTRY.update(result, name, manifest_sha256=newpin,
                           expected_generation=result['generation'])
