"""Offline cohort replay from actual CP producer fixtures; no hardware evidence.

Physical placement, accounts, shutdown and KVM observations are explicitly mocked
by the existing producer fixture. Actual CP publication and pure decoder remain.
"""
import copy
import importlib.util
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


D = module('managed_archive_under_test', 'scripts/managed-guest-archive.py')
F = module('managed_archive_producer_fixture', 'tests/test_managed_guest_checkpoint.py')
S = module('managed_archive_sender_fixture', 'tests/test_managed_backup_sender.py')
PURE = module('managed_archive_inert_fixture', 'tests/test_guest_managed_backup.py')
CP, R = D.CP, D.R
PREFIX = 'runs/' + F.RUN


def ref(raw):
    return CP.reference(raw, embedded=True)


def rebuild(value, manifest, *, run_change=None, outcome_change=None, intent_change=None,
            proof_change=None, admission_encoder=R._encode):
    """Repin attacker-controlled JSON, so semantic rejection cannot rely on hashes."""
    value = copy.deepcopy(value)
    records = value['records']
    run = json.loads(CP.decoded(records[PREFIX + '.json']))
    outcome = json.loads(CP.decoded(records[PREFIX + '.outcome.json']))
    intent = json.loads(CP.decoded(records[PREFIX + '.intent.json']))
    completion = json.loads(CP.decoded(records[PREFIX + '.completed.json']))
    if run_change:
        run_change(run)
    admission_raw = admission_encoder(run)
    records[PREFIX + '.json'] = ref(admission_raw)
    outcome['admission_sha256'] = intent['admission_sha256'] = CP.digest(admission_raw)
    if proof_change:
        proof = json.loads(CP.decoded(records[PREFIX + '.fixture.json']))
        proof_change(proof)
        proof_raw = CP.FIXTURE.canonical(proof)
        records[PREFIX + '.fixture.json'] = ref(proof_raw)
        outcome['fixture'] = intent['fixture'] = CP.reference(proof_raw)
    if outcome_change:
        outcome_change(outcome)
    outcome_raw = R._encode(outcome)
    records[PREFIX + '.outcome.json'] = ref(outcome_raw)
    intent['outcome'] = CP.reference(outcome_raw)
    if intent['requires_update']:
        new = json.loads(manifest)
        new['checkpoint']['shutdown_provenance']['evidence_sha256'] = CP.digest(outcome_raw)
        manifest = CP.MANIFEST.canonical(new)
        intent['new_manifest'] = ref(manifest)
        intent['registry']['new_manifest_sha256'] = CP.digest(manifest)
        history = json.loads(CP.decoded(intent['history']))
        history.update(new_manifest=new, new_manifest_sha256=CP.digest(manifest),
                       new_manifest_base64=ref(manifest)['data_base64'])
        intent['history'] = ref(CP.BACKUP.canonical(history))
    if intent_change:
        intent_change(intent)
    intent_raw = R._encode(intent)
    records[PREFIX + '.intent.json'] = ref(intent_raw)
    heads = CP._heads(run, intent_raw, intent)
    head_raw = CP.HEAD.canonical(heads[-1])
    value['head'] = ref(head_raw)
    for name in list(records):
        if name.startswith('transitions/'):
            del records[name]
    for previous, new in zip(heads, heads[1:]):
        old_raw, new_raw = CP.HEAD.canonical(previous), CP.HEAD.canonical(new)
        name = 'transitions/' + CP.digest(old_raw) + '-' + CP.digest(new_raw) + '.json'
        records[name] = ref(CP.LIFECYCLE.head_transition(old_raw, new_raw))
    registry = json.loads(CP.decoded(value['registry']))
    registry['guests'][0]['manifest_sha256'] = CP.digest(manifest)
    registry_raw = CP.REGISTRY.canonical(registry)
    value['registry'] = ref(registry_raw)
    completion.update(intent_sha256=CP.digest(intent_raw), outcome_sha256=CP.digest(outcome_raw),
                      ready_head=ref(head_raw), manifest_sha256=CP.digest(manifest),
                      registry_sha256=CP.digest(registry_raw))
    records[PREFIX + '.completed.json'] = ref(R._encode(completion))
    return value, manifest


class Framing(unittest.TestCase):
    def test_inert_admission_accepted_by_format_but_not_decoder(self):
        value, manifest = PURE.source()
        raw = D.FORMAT.canonical(value, manifest)
        with self.assertRaises(ValueError):
            D.validate_cohort(manifest, raw)

    def test_exact_initial_exemption_and_detached_data(self):
        value, manifest = PURE.source(initial=True)
        raw = D.FORMAT.canonical(value, manifest)
        result = D.validate_cohort(manifest, raw)
        self.assertEqual(result['head']['sequence'], 0)
        self.assertIsNone(result['run'])
        result['registry']['guests'].clear()
        self.assertEqual(len(D.validate_cohort(manifest, raw)['registry']['guests']), 1)
        value['records']['runs/extra.json'] = ref(b'{}')
        with self.assertRaises(ValueError):
            D.validate_cohort(manifest, R._encode(value))

    def test_packaged_catalogue_preserves_exact_shipped_bytes(self):
        raw = (ROOT / 'configs/managed-guest-profile.lock.json').read_bytes()
        self.assertEqual(D.CATALOGUE_DATA, raw)
        self.assertEqual(D.CATALOGUE_SHA256, CP.digest(raw))
        self.assertNotEqual(D.CATALOGUE_SHA256, CP.digest(CP.FIXTURE.canonical(CP.FIXTURE.LOCK)))


@unittest.skipUnless(os.getresuid() == (0, 0, 0), 'actual protected producer fixture needs root')
class Completed(unittest.TestCase):
    fixture_profile = 'none'

    def setUp(self):
        for target, name, new in [(F, 'M', CP), (F, 'P', CP.POLICY), (F, 'L', CP.LIFECYCLE),
                                  (S.S, 'CP', CP)]:
            mocked = patch.object(target, name, new)
            mocked.start()
            self.addCleanup(mocked.stop)
        self.case = F.ManagedCheckpoint(methodName='runTest')
        self.case.fixture_profile = self.fixture_profile
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        if self._testMethodName == 'test_actual_producer_noop_preserves_exact_manifest':
            self.case.disk.write_bytes(b'A' * 512)
        if self.fixture_profile == 'none':
            receipt = self.case.publish()
        else:
            receipt = S.publish_mock_fixture(self.case)
        self.assertTrue(receipt['success'])
        records = {}
        for directory in (self.case.runs, self.case.head_path.parent / 'transitions'):
            for path in directory.iterdir():
                records[directory.name + '/' + path.name] = ref(path.read_bytes())
        self.manifest = self.case.manifest_path.read_bytes()
        self.value = dict(schema=1, profile=D.FORMAT.PROFILE,
            registry=ref(self.case.registry_path.read_bytes()),
            head=ref(self.case.head_path.read_bytes()), records=records)
        if self.fixture_profile != 'none':
            proof = json.loads(CP.decoded(records[PREFIX + '.fixture.json']))
            mocked = patch.object(D, 'CATALOGUE_SHA256', proof['profile_sha256'])
            mocked.start()
            self.addCleanup(mocked.stop)

    def decode(self, value=None, manifest=None):
        return D.validate_cohort(self.manifest if manifest is None else manifest,
                                 R._encode(self.value if value is None else value))

    def reject(self, value, manifest=None):
        with self.assertRaises(ValueError):
            self.decode(value, manifest)

    def test_actual_producer_historical_cohort(self):
        # No destination identity supplied or asserted; source identity is retained.
        result = self.decode()
        self.assertEqual(result['run']['storage'], F.STORAGE)
        self.assertEqual(len(result['heads']), 6)
        self.assertEqual(result['manifest_data'], self.manifest)
        self.assertEqual(len(result['records']), 11 if self.fixture_profile != 'none' else 10)
        result['run']['name'] = 'changed'
        self.assertEqual(self.decode()['run']['name'], 'one')

    def test_actual_producer_noop_preserves_exact_manifest(self):
        result = self.decode()
        self.assertFalse(result['intent']['requires_update'])
        self.assertEqual(result['manifest_data'], self.case.old_raw)
        self.assertIsNone(result['intent']['history'])

    def test_actual_enrollment_initial_history(self):
        run = json.loads(CP.decoded(self.value['records'][PREFIX + '.json']))
        registry = json.loads(CP.decoded(self.value['registry']))
        registry['guests'][0]['manifest_sha256'] = self.case.pin
        value = dict(schema=1, profile=D.FORMAT.PROFILE,
            head=ref(CP.HEAD.canonical(run['previous_head'])),
            registry=ref(CP.REGISTRY.canonical(registry)), records={})
        result = self.decode(value, self.case.old_raw)
        self.assertEqual(result['head']['sequence'], 0)
        self.assertEqual(result['records'], {})

    def test_later_source_registry_preserves_historical_completion(self):
        value = copy.deepcopy(self.value)
        registry = json.loads(CP.decoded(value['registry']))
        registry['generation'] += 1
        registry['guests'][0]['enabled'] = False
        value['registry'] = ref(CP.REGISTRY.canonical(registry))
        result = self.decode(value)
        self.assertFalse(result['entry']['enabled'])
        self.assertLess(result['completion']['registry_generation'], result['registry']['generation'])

    def test_noncanonical_admission_even_after_hash_repin(self):
        value, manifest = rebuild(self.value, self.manifest,
            admission_encoder=lambda run: json.dumps(run, indent=2).encode())
        D.FORMAT.validate_lifecycle(R._encode(value), manifest)
        self.reject(value, manifest)

    def test_invalid_admission_profile_after_complete_repin(self):
        value, manifest = rebuild(self.value, self.manifest,
            run_change=lambda run: run.update(profile='inert-admission-v1'))
        D.FORMAT.validate_lifecycle(R._encode(value), manifest)
        self.reject(value, manifest)

    def test_run_registry_generation_exceeds_intent_after_repin(self):
        value, manifest = rebuild(self.value, self.manifest,
            run_change=lambda run: run.update(registry_generation=100))
        D.FORMAT.validate_lifecycle(R._encode(value), manifest)
        self.reject(value, manifest)

    def test_exact_members_missing_extra_and_edge(self):
        for name in self.value['records']:
            with self.subTest(missing=name):
                value = copy.deepcopy(self.value)
                del value['records'][name]
                self.reject(value)
        value = copy.deepcopy(self.value)
        value['records']['runs/unrelated.json'] = ref(b'{}')
        self.reject(value)
        value = copy.deepcopy(self.value)
        name = next(n for n in value['records'] if n.startswith('transitions/'))
        edge = json.loads(CP.decoded(value['records'][name]))
        edge['extra'] = True
        value['records'][name] = ref(R._encode(edge))
        self.reject(value)

    def test_framing_valid_illegal_transition_is_refused(self):
        value = copy.deepcopy(self.value)
        for name, reference in list(value['records'].items()):
            if not name.startswith('transitions/'):
                continue
            edge = json.loads(CP.decoded(reference))
            previous = json.loads(CP.BACKUP.decode(edge['previous_head_base64'], CP.HEAD.LIMIT))
            if previous['status'] != 'admitted':
                continue
            new = json.loads(CP.BACKUP.decode(edge['new_head_base64'], CP.HEAD.LIMIT))
            previous['status'] = 'stopping'
            previous_raw = CP.HEAD.canonical(previous)
            new['previous_head_sha256'] = CP.digest(previous_raw)
            new_raw = CP.HEAD.canonical(new)
            raw = CP.LIFECYCLE.head_transition(previous_raw, new_raw)
            del value['records'][name]
            forged_name = 'transitions/' + CP.digest(previous_raw) + '-' + CP.digest(new_raw) + '.json'
            value['records'][forged_name] = ref(raw)
            break
        else:
            self.fail('producer admitted edge missing')
        D.FORMAT.validate_lifecycle(R._encode(value), self.manifest)
        self.reject(value)

    def test_no_live_or_root_runtime_authentication(self):
        with patch.object(CP, '_cohort_records', side_effect=AssertionError('runtime custody')), \
                patch.object(CP, '_check_completion', side_effect=AssertionError('runtime custody')), \
                patch.object(CP, '_profile_sha256', side_effect=AssertionError('live catalogue')), \
                patch.object(CP.LIFECYCLE, 'StateController', side_effect=AssertionError('controller')), \
                patch.object(CP.os, 'getresuid', side_effect=AssertionError('root identity')):
            self.assertEqual(self.decode()['head']['status'], 'ready')

    def test_permit_admitted_head_pin_after_complete_repin(self):
        def change(outcome):
            permit = json.loads(CP.decoded(outcome['permit']))
            permit['head_sha256'] = 'f' * 64
            outcome['permit'] = ref(R.PERMIT.canonical(permit))
            outcome['shutdown']['permit_sha256'] = outcome['permit']['sha256']
            outcome['shutdown_sha256'] = CP.digest(R._encode(outcome['shutdown']))
        value, manifest = rebuild(self.value, self.manifest, outcome_change=change)
        D.FORMAT.validate_lifecycle(R._encode(value), manifest)
        self.reject(value, manifest)

    def test_completion_generation_and_registry_pins(self):
        for change in (dict(registry_generation=0), dict(registry_generation=100),
                       dict(registry_sha256='f' * 64), dict(outcome_sha256='f' * 64)):
            with self.subTest(change=change):
                value = copy.deepcopy(self.value)
                completion = json.loads(CP.decoded(value['records'][PREFIX + '.completed.json']))
                completion.update(change)
                value['records'][PREFIX + '.completed.json'] = ref(R._encode(completion))
                self.reject(value)

    def test_console_and_intent_outcome_pins(self):
        for suffix in ('.console', '.intent.json', '.outcome.json'):
            value = copy.deepcopy(self.value)
            raw = CP.decoded(value['records'][PREFIX + suffix])
            value['records'][PREFIX + suffix] = ref(raw + b' ')
            self.reject(value)


class Fixture(Completed):
    fixture_profile = 'managed-power-key-v1'

    def test_proof_crossbindings_and_evidence_after_complete_repin(self):
        for key, replacement in [('counter', 2), ('previous_counter', True),
                ('storage', {**F.STORAGE, 'nvme_serial': 'OTHER'}), ('admission_sha256', 'f' * 64),
                ('old_manifest_sha256', 'f' * 64), ('permit_sha256', 'f' * 64),
                ('console_sha256', 'f' * 64), ('profile_sha256', 'f' * 64),
                ('live', dict(port=18080, listen_inode='1234', kvm_fds=[])),
                ('stopped_disk', dict(bytes=512, sha256='f' * 64)),
                ('hardware_acceptance', True), ('kernel', dict(bytes=1, sha256='f' * 64)),
                ('http_confirmation', dict(status=200, bytes=128, body_sha256='f' * 64))]:
            with self.subTest(key=key):
                value, manifest = rebuild(self.value, self.manifest,
                    proof_change=lambda proof: proof.update({key: replacement}))
                D.FORMAT.validate_lifecycle(R._encode(value), manifest)
                self.reject(value, manifest)

    def test_exact_shipped_catalogue_pin_and_reformatted_pin(self):
        shipped = CP.digest(D.CATALOGUE_DATA)
        with patch.object(D, 'CATALOGUE_SHA256', shipped):
            value, manifest = rebuild(self.value, self.manifest,
                proof_change=lambda proof: proof.update(profile_sha256=shipped))
            self.assertIsNotNone(self.decode(value, manifest)['fixture'])
            value, manifest = rebuild(self.value, self.manifest,
                proof_change=lambda proof: proof.update(
                    profile_sha256=CP.digest(CP.FIXTURE.canonical(CP.FIXTURE.LOCK))))
            self.reject(value, manifest)


if __name__ == '__main__':
    unittest.main()
