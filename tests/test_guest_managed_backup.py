"""Pure format checks; no root authority, devices, guest execution or hardware claims."""
import base64
import copy
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


B = module('managed_backup_tested', ROOT / 'configs/guest_managed_backup.py')
F = module('managed_backup_source_fixtures', ROOT / 'tests/test_guest_checkpoint_records.py')
R = B.RECORDS


def ref(raw):
    return {**B._pin(raw), 'data_base64': base64.b64encode(raw).decode('ascii')}


def decoded(value):
    return base64.b64decode(value['data_base64'])


def source(changed=False, fixture=False, initial=False):
    """Reuse pure record fixtures and repin them around an inert admission blob.

    This intentionally is NOT a root-valid admission; acceptance by the artifact
    codec demonstrates that wrappers still must authenticate admissions/cohorts.
    """
    outcome, intent, completion = F.cohort(changed, fixture)
    old_raw = decoded(intent['old_manifest'])
    old = B.MANIFEST.validate_manifest(old_raw)
    identity = {key: intent[key] for key in ('name', 'uid', 'gid')}
    head = B.HEAD.initial_head(intent['storage'], identity, B.sha(old_raw))
    heads = [head]
    head = F.transition(B.HEAD.admit, head, B.sha(old_raw), intent['run_id'], intent['boot_id'])
    heads.append(head)
    head = F.transition(B.HEAD.mark_running, head)
    heads.append(head)
    head = F.transition(B.HEAD.mark_stopping, head)
    heads.append(head)
    admission = b'{"artifact_only":true}\n'
    outcome['admission_sha256'] = intent['admission_sha256'] = B.sha(admission)
    proof = b'{"external_fixture_validator_required":true}\n'
    if fixture:
        outcome['fixture'] = intent['fixture'] = B._pin(proof)
    outcome_raw = R.canonical_outcome(outcome)
    intent['outcome'] = B._pin(outcome_raw)
    if changed:
        new = B.MANIFEST.validate_manifest(decoded(intent['new_manifest']))
        new['checkpoint']['shutdown_provenance']['evidence_sha256'] = B.sha(outcome_raw)
        new_raw = B.MANIFEST.canonical(new)
        intent['new_manifest'] = ref(new_raw)
        history = json.loads(decoded(intent['history']))
        history.update(new_manifest=new, new_manifest_sha256=B.sha(new_raw),
                       new_manifest_base64=ref(new_raw)['data_base64'])
        intent['history'] = ref(B.BACKUP.canonical(history))
        intent['registry']['new_manifest_sha256'] = B.sha(new_raw)
    else:
        new_raw = old_raw
    intent_raw = R.canonical_intent(intent)
    head = F.transition(B.HEAD.mark_checkpoint, head, B.sha(intent_raw))
    heads.append(head)
    head = F.transition(B.HEAD.complete, head, B.sha(intent_raw), B.sha(new_raw))
    heads.append(head)
    if initial:
        head, new_raw = heads[0], old_raw
    registry = {'schema': 1, 'profile': B.REGISTRY.PROFILE, 'generation': 5,
                'storage': intent['storage'], 'guests': [{**identity,
                    'manifest_sha256': B.sha(new_raw), 'enabled': True,
                    'shutdown_profile': outcome['shutdown_profile'],
                    'fixture_profile': outcome['fixture_profile']}]}
    registry_raw = B.REGISTRY.canonical(registry)
    completion.update(intent_sha256=B.sha(intent_raw), outcome_sha256=B.sha(outcome_raw),
                      ready_head=ref(B.HEAD.canonical(head)), manifest_sha256=B.sha(new_raw),
                      registry_sha256=B.sha(registry_raw))
    records = {}
    if not initial:
        prefix = 'runs/' + head['run_id']
        records = {prefix + '.json': ref(admission),
                   prefix + '.console': ref(b'guest synced\n'),
                   prefix + '.outcome.json': ref(outcome_raw),
                   prefix + '.intent.json': ref(intent_raw),
                   prefix + '.completed.json': ref(R.canonical_completion(completion))}
        if fixture:
            records[prefix + '.fixture.json'] = ref(proof)
        for previous, current in zip(heads, heads[1:]):
            raw_previous, raw_current = B.HEAD.canonical(previous), B.HEAD.canonical(current)
            edge = dict(schema=1, profile=B.TRANSITION_PROFILE,
                        previous_head_sha256=B.sha(raw_previous), new_head_sha256=B.sha(raw_current),
                        previous_head_base64=ref(raw_previous)['data_base64'],
                        new_head_base64=ref(raw_current)['data_base64'])
            name = 'transitions/' + B.sha(raw_previous) + '-' + B.sha(raw_current) + '.json'
            records[name] = ref(R._encode(edge))
    return dict(schema=1, profile=B.PROFILE, registry=ref(registry_raw),
                head=ref(B.HEAD.canonical(head)), records=records), new_raw


class ManagedBackupTests(unittest.TestCase):
    def reject(self, value, manifest):
        with self.assertRaises(ValueError):
            B.canonical(value, manifest)
        with self.assertRaises(ValueError):
            B.validate_lifecycle(R._encode(value), manifest)

    def test_complete_sources_and_exact_historical_bytes(self):
        for changed in (False, True):
            for fixture in (False, True):
                with self.subTest(changed=changed, fixture=fixture):
                    value, manifest = source(changed, fixture)
                    raw = B.canonical(value, manifest)
                    parsed = B.validate_lifecycle(raw, manifest)
                    self.assertEqual(value, parsed)
                    self.assertEqual(len(parsed['records']), 11 if fixture else 10)
                    if not changed:
                        self.assertTrue(manifest.endswith(b'\n\n'))
                    prefix = 'runs/' + json.loads(decoded(value['head']))['run_id']
                    intent = json.loads(decoded(parsed['records'][prefix + '.intent.json']))
                    self.assertTrue(decoded(intent['old_manifest']).endswith(b'\n\n'))
                    parsed['registry']['sha256'] = '0' * 64
                    self.assertNotEqual(value, parsed)

    def test_initial_requires_ready_empty_cohort(self):
        value, manifest = source(initial=True)
        self.assertEqual(B.validate_lifecycle(B.canonical(value, manifest), manifest)['records'], {})
        value['records']['runs/unrelated.json'] = ref(b'{}')
        self.reject(value, manifest)
        value, manifest = source(initial=True)
        head = json.loads(decoded(value['head']))
        head['status'] = 'running'
        value['head'] = ref(R._encode(head))
        self.reject(value, manifest)

    def test_profiles_closed_fields_and_duplicate_json(self):
        value, manifest = source()
        for change in ({'profile': B.BACKUP.PROFILE}, {'schema': True}, {'extra': None}):
            self.reject({**value, **change}, manifest)
        for key in value:
            broken = dict(value)
            del broken[key]
            self.reject(broken, manifest)
        raw = B.canonical(value, manifest)
        for invalid_manifest in (None, manifest.decode(), b'', b' ' * (B.MANIFEST.LIMIT + 1)):
            with self.assertRaises(ValueError):
                B.validate_lifecycle(raw, invalid_manifest)
        for broken in (raw.replace(b'"schema":1', b'"schema":1,"schema":1', 1),
                       raw.replace(b'"schema":1', b'"schema":NaN', 1), b'\xff',
                       b'[' * 41 + b'0' + b']' * 41, b'"\\ud800"'):
            with self.assertRaises(ValueError):
                B.validate_lifecycle(broken, manifest)
        name = next(iter(value['records']))
        token = json.dumps(name).encode() + b':'
        duplicate = raw.replace(token, token + R._encode(value['records'][name]).strip() + b',' + token, 1)
        with self.assertRaises(ValueError):
            B.validate_lifecycle(duplicate, manifest)
        self.assertEqual(B.BACKUP.NAMES, ('enrollment.json', 'checkpoint-history.json',
                                         'Image', 'initramfs.cpio.gz', 'disk.raw'))

    def test_rawref_hash_size_encoding_and_guest_binding(self):
        original, manifest = source()
        for key, replacement in [('bytes', True), ('bytes', B.HEAD.LIMIT + 1),
                ('sha256', '0' * 64), ('data_base64', '!'),
                ('data_base64', original['head']['data_base64'] + '\n')]:
            value = copy.deepcopy(original)
            value['head'][key] = replacement
            self.reject(value, manifest)
        for which, key, replacement in [('head', 'manifest_sha256', '0' * 64),
                ('head', 'uid', 60003), ('head', 'storage', {**json.loads(decoded(original['head']))['storage'],
                                                         'nvme_serial': 'OTHER'})]:
            value = copy.deepcopy(original)
            parsed = json.loads(decoded(value[which]))
            parsed[key] = replacement
            value[which] = ref(R._encode(parsed))
            self.reject(value, manifest)
        value = copy.deepcopy(original)
        registry = json.loads(decoded(value['registry']))
        registry['guests'][0]['manifest_sha256'] = '0' * 64
        value['registry'] = ref(B.REGISTRY.canonical(registry))
        self.reject(value, manifest)
        # Equivalent canonicalized enrollment differs from the exact source pin.
        self.reject(original, B.MANIFEST.canonical(B.MANIFEST.validate_manifest(manifest)))

    def test_record_members_crosspins_and_transitions(self):
        original, manifest = source()
        for name in original['records']:
            value = copy.deepcopy(original)
            del value['records'][name]
            self.reject(value, manifest)
        value = copy.deepcopy(original)
        value['records']['runs/unrelated.json'] = ref(b'{}\n')
        self.reject(value, manifest)
        for suffix in ('.console', '.json', '.outcome.json', '.intent.json', '.completed.json'):
            value = copy.deepcopy(original)
            name = next(key for key in value['records'] if key.startswith('runs/') and
                        (key.endswith(suffix) if suffix != '.json' else key.count('.') == 1))
            value['records'][name] = ref(b'{}\n')
            self.reject(value, manifest)
        edge_name = next(name for name in original['records'] if name.startswith('transitions/'))
        value = copy.deepcopy(original)
        edge = json.loads(decoded(value['records'][edge_name]))
        edge['profile'] = 'legacy-transition'
        value['records'][edge_name] = ref(R._encode(edge))
        self.reject(value, manifest)
        value = copy.deepcopy(original)
        moved = 'transitions/' + '0' * 64 + '-' + '1' * 64 + '.json'
        value['records'][moved] = value['records'].pop(edge_name)
        self.reject(value, manifest)

    def test_embedded_duplicate_keys_noncanonical_base64_and_fixture_limit(self):
        value, manifest = source()
        admission_name = next(name for name in value['records'] if name.startswith('runs/') and name.count('.') == 1)
        value['records'][admission_name] = ref(b'{"x":1,"x":1}')
        self.reject(value, manifest)
        value, manifest = source()
        value['registry'] = {'bytes': 1, 'sha256': B.sha(b'f'), 'data_base64': 'Zh=='}
        self.reject(value, manifest)
        value, manifest = source(fixture=True)
        fixture_name = next(name for name in value['records'] if name.endswith('.fixture.json'))
        value['records'][fixture_name]['bytes'] = R.FIXTURE_LIMIT + 1
        self.reject(value, manifest)
        value, manifest = source()
        edge_name = next(name for name in value['records'] if name.startswith('transitions/'))
        value['records'][edge_name]['bytes'] = B.TRANSITION_LIMIT + 1
        self.reject(value, manifest)

    def test_whole_registry_snapshot_is_historical(self):
        value, manifest = source()
        registry = B.REGISTRY.validate_registry(decoded(value['registry']))
        registry['generation'] += 1
        registry['guests'][0]['enabled'] = False
        registry['guests'].append({**registry['guests'][0], 'name': 'other', 'uid': 60004, 'gid': 60004})
        value['registry'] = ref(B.REGISTRY.canonical(registry))
        parsed = B.validate_lifecycle(B.canonical(value, manifest), manifest)
        self.assertEqual(len(json.loads(decoded(parsed['registry']))['guests']), 2)
        self.assertFalse(json.loads(decoded(parsed['registry']))['guests'][0]['enabled'])

    def test_completion_generation_stays_between_intent_and_source(self):
        for generation in (0, 1, 2, 5, 6):
            with self.subTest(generation=generation):
                value, manifest = source()
                name = next(name for name in value['records'] if name.endswith('.completed.json'))
                completion = R.validate_completion(decoded(value['records'][name]))
                completion['registry_generation'] = generation
                value['records'][name] = ref(R.canonical_completion(completion))
                if generation in (2, 5):
                    self.assertEqual(B.validate_lifecycle(B.canonical(value, manifest), manifest), value)
                else:
                    self.reject(value, manifest)

    def test_preallocation_and_record_budgets(self):
        value, manifest = source()
        for broken in ({'huge': 'x' * B.LIFECYCLE_LIMIT}, {'huge': 1 << 64},
                       {'bad': '\ud800'}, {'bad': float('nan')}, {1: 'invalid key'}):
            with mock.patch.object(R.json, 'dumps', side_effect=AssertionError('allocated early')):
                with self.assertRaises(ValueError):
                    B.canonical(broken, manifest)
        with mock.patch.object(R.json, 'loads', side_effect=AssertionError('parsed early')):
            with self.assertRaises(ValueError):
                B.validate_lifecycle(b' ' * (B.LIFECYCLE_LIMIT + 1), manifest)
        value['registry']['data_base64'] = 'A' * (4 * ((B.REGISTRY.LIMIT + 2) // 3) + 4)
        with mock.patch.object(R.base64, 'b64decode', side_effect=AssertionError('decoded early')):
            with self.assertRaises(ValueError):
                B.canonical(value, manifest)
        for suffix, limit in (('.console', R.CONSOLE_LIMIT), ('.intent.json', R.INTENT_LIMIT),
                              ('.completed.json', R.COMPLETION_LIMIT),
                              ('.outcome.json', R.OUTCOME_LIMIT), ('.json', B.HEAD.LIMIT)):
            value, manifest = source()
            name = next(key for key in value['records'] if key.startswith('runs/') and
                        (key.endswith(suffix) if suffix != '.json' else key.count('.') == 1))
            value['records'][name]['bytes'] = limit + 1
            self.reject(value, manifest)

    def test_large_disk_base256_headers_and_strict_framing(self):
        maximum = B.MANIFEST.CONFIG.DISK_LIMIT
        for size in (0, 8 * 1024**3, maximum):
            raw = B.make_header('disk.raw', size)
            self.assertEqual(B.read_header(io.BytesIO(raw), 'disk.raw'), size)
        self.assertTrue(B.make_header('disk.raw', maximum)[124] & 0x80)
        for name, size in [('disk.raw', maximum + 1), ('../disk.raw', 1),
                           ('lifecycle.json', B.LIFECYCLE_LIMIT + 1), ('Image', True)]:
            with self.assertRaises(ValueError):
                B.make_header(name, size)
        for name in B.NAMES:
            self.assertEqual(B.read_header(io.BytesIO(B.make_header(name, 0)), name), 0)
        with self.assertRaises(ValueError):
            B.read_header(io.BytesIO(B.BACKUP.make_header('Image', 0)), 'lifecycle.json')
        raw = B.make_header('disk.raw', 0)
        with self.assertRaises(ValueError):
            B.read_header(io.BytesIO(raw[:-1] + b'x'), 'disk.raw')
        with self.assertRaises(ValueError):
            B.read_header(io.BytesIO(raw[:511]), 'disk.raw')
        B.read_padding(io.BytesIO(bytes(511)), 1)
        B.read_padding(io.BytesIO(b''), 512)
        for padding in (bytes(510), bytes(510) + b'x'):
            with self.assertRaises(ValueError):
                B.read_padding(io.BytesIO(padding), 1)
        B.read_trailer(io.BytesIO(bytes(1024)))
        for trailer in (bytes(512), bytes(1023) + b'x', bytes(1024) + b'x',
                        bytes(1024) + B.make_header('disk.raw', 0)):
            with self.assertRaises(ValueError):
                B.read_trailer(io.BytesIO(trailer))


if __name__ == '__main__':
    unittest.main()
