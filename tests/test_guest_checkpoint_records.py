"""Pure record regressions; no execution, checkpoint authority or hardware claims."""
import base64
import copy
import hashlib
import importlib.util
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


R = module('checkpoint_records_tested', ROOT / 'configs/guest_checkpoint_records.py')
P = module('checkpoint_permit_fixture', ROOT / 'tests/test_guest_run_permit.py')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def ref(raw, embedded=True):
    result = {'bytes': len(raw), 'sha256': sha(raw)}
    if embedded:
        result['data_base64'] = base64.b64encode(raw).decode('ascii')
    return result


def transition(function, head, *args):
    return function(head, *args, expected_head_sha256=sha(R.HEAD.canonical(head)))


def cohort(changed=True, fixture=False):
    permit = P.permit()
    config = copy.deepcopy(permit['config'])
    old = {'schema': 1, 'profile': R.MANIFEST.PROFILE, 'config': config,
           'guest_uuid': permit['storage']['guest_uuid'], 'files': {}}
    for key, name in R.MANIFEST.FILENAMES.items():
        old['files'][name] = {**config[key], 'uid': config['uid'] if key == 'disk' else 0,
                             'gid': config['gid'] if key == 'disk' else 0,
                             'mode': '0600' if key == 'disk' else '0444'}
    # Preserve deliberately noncanonical historical enrollment bytes.
    old_raw = json.dumps(old, indent=2).encode() + b'\n\n'
    identity = {k: config[k] for k in ('name', 'uid', 'gid')}
    head = R.HEAD.initial_head(permit['storage'], identity, sha(old_raw))
    head = transition(R.HEAD.admit, head, sha(old_raw), permit['run_id'], permit['boot_id'])
    permit['manifest_sha256'] = sha(old_raw)
    permit['head_sha256'] = sha(R.HEAD.canonical(head))
    head = transition(R.HEAD.mark_running, head)
    head = transition(R.HEAD.mark_stopping, head)
    common = dict(schema=1, storage=copy.deepcopy(permit['storage']), **identity,
                  run_id=permit['run_id'], boot_id=permit['boot_id'])
    session = R.QMP.Session(permit['run_id'])
    session.receive({'QMP': {'version': {'qemu': {'major': 7, 'minor': 2, 'micro': 22},
                                      'package': ''}, 'capabilities': []}})
    for command in ('qmp_capabilities', 'cont', 'system_powerdown'):
        sent = session.command(command)
        session.receive({'return': {}, 'id': sent['id']})
    session.receive({'event': 'SHUTDOWN', 'data': {'guest': True, 'reason': 'guest-shutdown'}})
    console = ref(b'guest synced\n', False)
    permit_ref = ref(R.PERMIT.canonical(permit))
    shutdown = dict(schema=1, profile='a733-supervised-shutdown-v1',
                    run_id=permit['run_id'], boot_id=permit['boot_id'],
                    permit_sha256=permit_ref['sha256'],
                    stopping_head_sha256=sha(R.HEAD.canonical(head)), raw_wait_status=0,
                    qmp=list(session.transcript), console_bytes=console['bytes'],
                    console_sha256=console['sha256'], hardware_acceptance=False,
                    checkpoint_authorized=False)
    proof = ref(b'fixture proof externally authenticated', False) if fixture else None
    outcome = dict(common, profile=R.OUTCOME_PROFILE, admission_sha256='9' * 64,
                   shutdown_profile='qmp-guest-shutdown-v1', fixture_profile=
                   'managed-power-key-v1' if fixture else 'none', permit=permit_ref,
                   shutdown=shutdown, shutdown_sha256=sha(R._encode(shutdown)),
                   console=console, fixture=proof)
    disk = {**config['disk'], 'sha256': '1' * 64 if changed else config['disk']['sha256']}
    provenance = dict(profile='a733-managed-shutdown-v1', run_id=permit['run_id'],
                      boot_id=permit['boot_id'], shutdown_profile=outcome['shutdown_profile'],
                      fixture_profile=outcome['fixture_profile'],
                      evidence_sha256=sha(R.canonical_outcome(outcome)))
    new = R.MANIFEST.proposed_managed_manifest(old, sha(old_raw), disk, provenance)
    new_raw = R.MANIFEST.canonical(new) if changed else old_raw
    history = dict(schema=2, operation='a733-managed-guest-checkpoint-v2',
                   old_manifest=old, new_manifest=new, old_manifest_sha256=sha(old_raw),
                   new_manifest_sha256=sha(new_raw),
                   old_manifest_base64=ref(old_raw)['data_base64'],
                   new_manifest_base64=ref(new_raw)['data_base64'])
    intent = dict(common, profile=R.INTENT_PROFILE, admission_sha256=outcome['admission_sha256'],
                  stopping_head=ref(R.HEAD.canonical(head)),
                  outcome=ref(R.canonical_outcome(outcome), False), console=console, fixture=proof,
                  old_manifest=ref(old_raw), new_manifest=ref(new_raw), disk=disk,
                  requires_update=changed, history=ref(R.BACKUP.canonical(history)) if changed else None,
                  registry={'observed_generation': 2, 'old_manifest_sha256': sha(old_raw),
                            'new_manifest_sha256': sha(new_raw)})
    intent_sha = sha(R.canonical_intent(intent))
    head = transition(R.HEAD.mark_checkpoint, head, intent_sha)
    head = transition(R.HEAD.complete, head, intent_sha, sha(new_raw))
    completion = dict(common, profile=R.COMPLETION_PROFILE, intent_sha256=intent_sha,
                      outcome_sha256=intent['outcome']['sha256'], ready_head=ref(R.HEAD.canonical(head)),
                      manifest_sha256=sha(new_raw), registry_generation=5, registry_sha256='8' * 64)
    return outcome, intent, completion


class RecordTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = cohort()

    def canonical(self, index, value):
        return (R.canonical_outcome, R.canonical_intent, R.canonical_completion)[index](value)

    def validate(self, index, data):
        return (R.validate_outcome, R.validate_intent, R.validate_completion)[index](data)

    def reject(self, index, path, replacement, rehash=False):
        value = copy.deepcopy(self.records[index])
        target = value
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = replacement
        if rehash:
            value['shutdown_sha256'] = sha(R._encode(value['shutdown']))
        with self.subTest(index=index, path=path, value=repr(replacement)[:60]):
            with self.assertRaises(ValueError):
                self.canonical(index, value)
            with self.assertRaises(ValueError):
                self.validate(index, json.dumps(value))

    def test_realistic_changed_and_noop_exact_old_bytes(self):
        for changed in (False, True):
            for fixture in (False, True):
                records = cohort(changed, fixture)
                for index, value in enumerate(records):
                    encoded = self.canonical(index, value)
                    parsed = self.validate(index, encoded)
                    self.assertEqual(value, parsed)
                    self.assertEqual(encoded, self.canonical(index, parsed))
                    parsed['storage']['nvme_serial'] = 'mutated'
                    self.assertEqual(value['storage']['nvme_serial'], 'NVME-123')
                intent = records[1]
                self.assertTrue(base64.b64decode(intent['old_manifest']['data_base64']).endswith(b'\n\n'))
                if not changed:
                    self.assertEqual(intent['old_manifest'], intent['new_manifest'])
                    self.assertIsNone(intent['history'])

    def test_schema_identity_profiles_and_closed_fields(self):
        for index, original in enumerate(self.records):
            for path, replacements in [(['schema'], [True, 2, '1']),
                    (['profile'], ['legacy', None]), (['uid'], [True, 0, 60003]),
                    (['gid'], [False, 60003]), (['run_id'], ['bad', P.permit()['boot_id']]),
                    (['storage', 'guest_uuid'], [P.permit()['boot_id']]),
                    (['storage', 'nvme_serial'], ['../nvme', 'other'])]:
                for replacement in replacements:
                    self.reject(index, path, replacement)
            for key in original:
                value = copy.deepcopy(original)
                del value[key]
                with self.assertRaises(ValueError):
                    self.canonical(index, value)
            value = dict(original, extra=True)
            with self.assertRaises(ValueError):
                self.canonical(index, value)

    def test_hash_references_base64_and_limits(self):
        paths = [(0, 'permit', R.PERMIT.LIMIT), (1, 'stopping_head', R.HEAD.LIMIT),
                 (1, 'old_manifest', R.MANIFEST.LIMIT), (1, 'new_manifest', R.MANIFEST.LIMIT),
                 (1, 'history', R.BACKUP.ENTRY_LIMIT), (2, 'ready_head', R.HEAD.LIMIT)]
        for index, field, limit in paths:
            for key, replacements in [('bytes', [True, 0, limit + 1]),
                    ('sha256', ['A' * 64, '0' * 64]),
                    ('data_base64', ['!', 'A' * (4 * ((limit + 2) // 3) + 4),
                                     self.records[index][field]['data_base64'] + '\n'])]:
                for replacement in replacements:
                    self.reject(index, [field, key], replacement)
        self.reject(0, ['console', 'bytes'], R.CONSOLE_LIMIT + 1)
        self.reject(1, ['outcome', 'bytes'], R.OUTCOME_LIMIT + 1)
        self.reject(0, ['fixture_profile'], 'first-kvm-http-v1')
        self.reject(0, ['fixture'], ref(b'x', False))
        self.reject(0, ['shutdown_profile'], 'legacy')
        # Reject nonzero unused pad bits even when the decoded bytes/hash agree.
        with self.assertRaises(ValueError):
            R._reference({'bytes': 1, 'sha256': sha(b'f'), 'data_base64': 'Zh=='}, 2, embedded=True)

    def test_shutdown_and_transcript_integrity(self):
        for key, values in [('raw_wait_status', [True, 1, 256]),
                ('run_id', [P.permit()['boot_id']]), ('permit_sha256', ['0' * 64]),
                ('console_sha256', ['0' * 64]), ('console_bytes', [True, 0]),
                ('hardware_acceptance', [True, 0]), ('checkpoint_authorized', [True, 0])]:
            for value in values:
                self.reject(0, ['shutdown', key], value, True)
        transcript = self.records[0]['shutdown']['qmp']
        variants = [[], transcript[:-1], transcript + [transcript[-1]],
                    [transcript[1], *transcript[0:1], *transcript[2:]],
                    [*transcript[:2], transcript[1], *transcript[2:]],
                    [*transcript[:-2], transcript[-1]],  # powerdown ACK missing
                    [*transcript, {'direction': 'receive', 'message': {'event': 'RESET'}}],
                    [*transcript, {'direction': 'receive', 'message': {'event': 'GUEST_PANICKED'}}]]
        for value in variants:
            self.reject(0, ['shutdown', 'qmp'], value, True)
        bad = copy.deepcopy(transcript)
        bad[1]['message']['id'] = 'wrong'
        self.reject(0, ['shutdown', 'qmp'], bad, True)
        self.reject(0, ['shutdown_sha256'], '0' * 64)

    def test_manifest_history_registry_and_completion_bindings(self):
        for path, replacement in [(['disk', 'bytes'], 8192), (['disk', 'bytes'], True),
                (['requires_update'], False), (['requires_update'], 1),
                (['registry', 'observed_generation'], True),
                (['registry', 'old_manifest_sha256'], '0' * 64),
                (['registry', 'new_manifest_sha256'], '0' * 64),
                (['outcome', 'sha256'], '0' * 64), (['history'], None),
                (['fixture'], ref(b'proof', False))]:
            self.reject(1, path, replacement)
        for path, replacement in [(['intent_sha256'], '0' * 64),
                (['manifest_sha256'], '0' * 64), (['registry_generation'], True),
                (['registry_generation'], 1 << 63), (['registry_sha256'], 'A' * 64)]:
            self.reject(2, path, replacement)
        # Canonicalizing an unchanged historical manifest is forbidden.
        value = cohort(False)[1]
        old = R.MANIFEST.validate_manifest(base64.b64decode(value['old_manifest']['data_base64']))
        value['new_manifest'] = ref(R.MANIFEST.canonical(old))
        with self.assertRaises(ValueError):
            R.canonical_intent(value)
        # Any unrelated registry updates may advance generation independently.
        value = copy.deepcopy(self.records[1])
        value['registry']['observed_generation'] = R.MAX_INTEGER
        R.canonical_intent(value)

    def test_nested_qmp_and_shutdown_before_final_ack(self):
        value = copy.deepcopy(self.records[0])
        transcript = value['shutdown']['qmp']
        # The supervisor can observe guest SHUTDOWN before its powerdown ACK.
        transcript[-2], transcript[-1] = transcript[-1], transcript[-2]
        nested = 1
        for _ in range(29):
            nested = {'nested': nested}
        transcript.append({'direction': 'receive', 'message': {
            'event': 'OTHER', 'data': {'payload': nested}}})
        value['shutdown_sha256'] = sha(R._encode(value['shutdown']))
        R.canonical_outcome(value)
        too_deep = 1
        for _ in range(40):
            too_deep = {'nested': too_deep}
        transcript[-1]['message']['data']['payload'] = too_deep
        with self.assertRaises(ValueError):
            R.canonical_outcome(value)

    def test_embedded_objects_are_exact_and_semantically_bound(self):
        # A valid permit with different resource identity cannot become an outcome.
        for key, replacement in [('run_id', P.permit()['boot_id']),
                                 ('storage', {**P.permit()['storage'], 'nvme_serial': 'OTHER'})]:
            value = copy.deepcopy(self.records[0])
            permit = R.PERMIT.validate_permit(base64.b64decode(value['permit']['data_base64']))
            permit[key] = replacement
            value['permit'] = ref(R.PERMIT.canonical(permit))
            value['shutdown']['permit_sha256'] = value['permit']['sha256']
            value['shutdown_sha256'] = sha(R._encode(value['shutdown']))
            with self.assertRaises(ValueError):
                R.canonical_outcome(value)
        value = copy.deepcopy(self.records[0])
        raw = base64.b64decode(value['permit']['data_base64'])
        value['permit'] = ref(json.dumps(json.loads(raw), indent=2).encode())
        value['shutdown']['permit_sha256'] = value['permit']['sha256']
        value['shutdown_sha256'] = sha(R._encode(value['shutdown']))
        with self.assertRaises(ValueError):
            R.canonical_outcome(value)
        # Rehashing a valid but wrong stopping head does not fix the manifest pin.
        value = copy.deepcopy(self.records[1])
        head = R.HEAD.validate_head(base64.b64decode(value['stopping_head']['data_base64']))
        head['manifest_sha256'] = '0' * 64
        value['stopping_head'] = ref(R.HEAD.canonical(head))
        with self.assertRaises(ValueError):
            R.canonical_intent(value)
        # A manual-history operation remains forbidden even with a correct hash.
        value = copy.deepcopy(self.records[1])
        history = json.loads(base64.b64decode(value['history']['data_base64']))
        history['schema'], history['operation'] = 1, 'a733-guest-checkpoint-v1'
        value['history'] = ref(R.BACKUP.canonical(history))
        with self.assertRaises(ValueError):
            R.canonical_intent(value)
        with mock.patch.object(R.base64, 'b64decode', side_effect=AssertionError('decoded early')):
            with self.assertRaises(ValueError):
                R._reference({'bytes': 1, 'sha256': '0' * 64,
                              'data_base64': 'x' * 9}, 1, embedded=True)

    def test_json_parser_resources_and_preallocation(self):
        for index, value in enumerate(self.records):
            encoded = self.canonical(index, value)
            for raw in [b'', b'\xff', '"\ud800"', encoded.replace(b'"schema":1', b'"schema":NaN', 1),
                        encoded.replace(b'"schema":1', b'"schema":Infinity', 1),
                        encoded.replace(b'"schema":1', b'"schema":1,"schema":1', 1),
                        '[' * 41 + '0' + ']' * 41]:
                with self.assertRaises(ValueError):
                    self.validate(index, raw)
        cycle = []
        cycle.append(cycle)
        for value in [cycle, {'huge': 1 << 64}, {'huge': 'x' * (R.OUTCOME_LIMIT + 1)},
                      {'bad': float('nan')}, {'bad': object()}, {1: 'bad'}]:
            with mock.patch.object(R.json, 'dumps', side_effect=AssertionError('allocated early')):
                with self.assertRaises(ValueError):
                    R.canonical_outcome(value)
        for function, limit in [(R.canonical_intent, R.INTENT_LIMIT),
                                (R.canonical_completion, R.COMPLETION_LIMIT)]:
            with mock.patch.object(R.json, 'dumps', side_effect=AssertionError('allocated early')):
                with self.assertRaises(ValueError):
                    function({'large': 'x' * limit})


if __name__ == '__main__':
    unittest.main()
