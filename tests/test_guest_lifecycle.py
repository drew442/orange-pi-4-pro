"""Pure lifecycle schema/CAS tests; these make no hardware acceptance claims."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location(
    'guest_lifecycle', Path(__file__).resolve().parents[1] / 'configs/guest_lifecycle.py')
L = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(L)
STORAGE = {'state_uuid': '11111111-1111-4111-8111-111111111111',
           'guest_uuid': '22222222-2222-4222-8222-222222222222',
           'nvme_serial': 'NVME-123'}
IDENTITY = {'name': 'autotest', 'uid': 60002, 'gid': 60002}
PIN = 'a' * 64
NEWPIN = 'b' * 64
INTENT = 'c' * 64
FAILURE = 'd' * 64
BOOT = '33333333-3333-4333-8333-333333333333'
RUN = '44444444-4444-4444-8444-444444444444'
RUN2 = '55555555-5555-4555-8555-555555555555'


def digest(head):
    return hashlib.sha256(L.canonical(head)).hexdigest()


def transition(function, head, *args, **kwargs):
    return function(head, *args, expected_head_sha256=digest(head), **kwargs)


class HeadTests(unittest.TestCase):
    def setUp(self):
        self.head = L.initial_head(STORAGE, IDENTITY, PIN)

    def running(self):
        admitted = transition(L.admit, self.head, PIN, RUN, BOOT, automatic=True)
        return transition(L.mark_running, admitted)

    def completed(self):
        checkpoint = transition(L.mark_checkpoint, self.running(), INTENT)
        return transition(L.complete, checkpoint, INTENT, NEWPIN)

    def test_initial_canonical_ownership_and_roundtrip(self):
        storage, identity = copy.deepcopy(STORAGE), copy.deepcopy(IDENTITY)
        head = L.initial_head(storage, identity, PIN)
        storage['nvme_serial'] = 'changed'
        identity['uid'] = 60003
        self.assertEqual(head, self.head)
        encoded = L.canonical(head)
        self.assertTrue(encoded.endswith(b'\n'))
        self.assertNotIn(b' ', encoded)
        self.assertEqual(L.validate_head(encoded), head)
        self.assertEqual(L.canonical(L.validate_head(encoded)), encoded)
        self.assertEqual(head['sequence'], 0)

    def test_invalid_json_boundaries(self):
        encoded = L.canonical(self.head)
        invalid = [b'', b'\xff', '"\ud800"', encoded + b' ' * L.LIMIT,
                   encoded.replace(b'"schema":1', b'"schema":1,"schema":1'),
                   encoded.replace(b'"schema":1', b'"schema":NaN'),
                   encoded.replace(b'"schema":1', b'"schema":Infinity'),
                   '[' * (L.MAX_DEPTH + 1) + '0' + ']' * (L.MAX_DEPTH + 1)]
        for data in invalid:
            with self.subTest(data=repr(data)[:100]), self.assertRaises(ValueError):
                L.validate_head(data)

    def test_invalid_schema_types_storage_and_identity(self):
        invalid = [('schema', True), ('schema', 2), ('profile', 'future'),
                   ('sequence', True), ('sequence', -1), ('sequence', 1.5),
                   ('sequence', L.MAX_SEQUENCE + 1), ('status', []),
                   ('status', 'unknown'), ('manifest_sha256', 'A' * 64),
                   ('name', 'bad_name'), ('uid', 0), ('uid', True), ('gid', 60003),
                   ('storage', {**STORAGE, 'extra': 1}),
                   ('storage', {**STORAGE, 'nvme_serial': '../disk'}),
                   ('storage', {**STORAGE, 'state_uuid': '0' * 32}),
                   ('storage', {**STORAGE, 'guest_uuid': STORAGE['guest_uuid'].upper() + ' '}),
                   ('run_id', RUN), ('last_boot_id', BOOT),
                   ('previous_head_sha256', 'e' * 64),
                   ('last_completed_intent_sha256', INTENT),
                   ('intent_sha256', INTENT), ('failure_sha256', FAILURE)]
        for key, value in invalid:
            head = copy.deepcopy(self.head)
            head[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                L.canonical(head)
        for operation in ('remove', 'add'):
            head = copy.deepcopy(self.head)
            if operation == 'remove':
                del head['failure_sha256']
            else:
                head['runtime_override'] = True
            with self.assertRaises(ValueError):
                L.canonical(head)
        with self.assertRaises(ValueError):
            L.initial_head(STORAGE, {**IDENTITY, 'extra': 1}, PIN)

    def test_state_shape_requirements(self):
        admitted = transition(L.admit, self.head, PIN, RUN, BOOT)
        for key, value in [('previous_head_sha256', None), ('run_id', None),
                           ('last_boot_id', None),
                           ('run_id', 'AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA'),
                           ('intent_sha256', INTENT), ('failure_sha256', FAILURE)]:
            head = {**admitted, key: value}
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                L.canonical(head)
        checkpoint = transition(L.mark_checkpoint, self.running(), INTENT)
        with self.assertRaises(ValueError):
            L.canonical({**checkpoint, 'intent_sha256': None})
        quarantine = transition(L.quarantine, checkpoint, FAILURE)
        self.assertEqual(quarantine['intent_sha256'], INTENT)
        with self.assertRaises(ValueError):
            L.canonical({**quarantine, 'failure_sha256': None})

    def test_full_transitions_preserve_identity_and_chain(self):
        current = self.head
        steps = [(L.admit, (PIN, RUN, BOOT), 'admitted'),
                 (L.mark_running, (), 'running'),
                 (L.mark_stopping, (), 'stopping'),
                 (L.mark_checkpoint, (INTENT,), 'checkpoint'),
                 (L.complete, (INTENT, NEWPIN), 'ready')]
        for function, args, status in steps:
            original = copy.deepcopy(current)
            following = transition(function, current, *args)
            self.assertEqual(current, original)
            self.assertEqual(following['status'], status)
            self.assertEqual(following['sequence'], current['sequence'] + 1)
            self.assertEqual(following['previous_head_sha256'], digest(current))
            for key in ('storage', 'name', 'uid', 'gid'):
                self.assertEqual(following[key], self.head[key])
            if status != 'ready':
                self.assertIsNone(following['last_completed_intent_sha256'])
            current = following
        self.assertEqual(current['last_boot_id'], BOOT)
        self.assertEqual(current['run_id'], RUN)
        self.assertEqual(current['last_completed_intent_sha256'], INTENT)
        self.assertEqual(current['manifest_sha256'], NEWPIN)
        self.assertIsNone(current['intent_sha256'])

    def assert_malformed_ready_cannot_admit(self, head):
        encoded = (json.dumps(head, sort_keys=True, separators=(',', ':')) + '\n').encode()
        with self.assertRaises(ValueError):
            L.validate_head(encoded)
        with self.assertRaises(ValueError):
            L.admit(head, head['manifest_sha256'], RUN2, BOOT,
                    expected_head_sha256=hashlib.sha256(encoded).hexdigest())

    def test_noninitial_ready_requires_retained_run_and_completion(self):
        head = {**self.head, 'sequence': 1, 'previous_head_sha256': 'e' * 64}
        self.assert_malformed_ready_cannot_admit(head)

    def test_ready_with_run_requires_completed_intent(self):
        head = {**self.completed(), 'last_completed_intent_sha256': None}
        self.assert_malformed_ready_cannot_admit(head)

    def test_ready_with_completed_intent_requires_run(self):
        head = {**self.completed(), 'run_id': None, 'last_boot_id': None}
        self.assert_malformed_ready_cannot_admit(head)

    def test_runless_quarantine_cannot_retain_intents(self):
        head = transition(L.quarantine, self.head, FAILURE)
        self.assertEqual(L.validate_head(L.canonical(head)), head)
        for key in ('intent_sha256', 'last_completed_intent_sha256'):
            malformed = {**head, key: INTENT}
            with self.subTest(key=key), self.assertRaises(ValueError):
                L.validate_head(json.dumps(malformed))

    def test_checkpoint_from_running_and_unchanged_completion(self):
        checkpoint = transition(L.mark_checkpoint, self.running(), INTENT)
        complete = transition(L.complete, checkpoint, INTENT, PIN)
        self.assertEqual(complete['manifest_sha256'], PIN)
        self.assertEqual(complete['sequence'], checkpoint['sequence'] + 1)

    def test_cas_required_even_for_idempotence(self):
        ready = self.completed()
        quarantine = transition(L.quarantine, self.head, FAILURE)
        calls = [(L.admit, self.head, (PIN, RUN, BOOT)),
                 (L.mark_running, self.running(), ()),
                 (L.mark_stopping, self.running(), ()),
                 (L.mark_checkpoint, self.running(), (INTENT,)),
                 (L.complete, ready, (INTENT, NEWPIN)),
                 (L.quarantine, quarantine, (FAILURE,))]
        for function, head, args in calls:
            with self.subTest(function=function.__name__), self.assertRaises(ValueError):
                function(head, *args, expected_head_sha256='f' * 64)

    def test_illegal_transitions_and_pin_conflicts(self):
        for function, args in [(L.mark_running, ()), (L.mark_stopping, ()),
                               (L.mark_checkpoint, (INTENT,)),
                               (L.complete, (INTENT, NEWPIN)),
                               (L.admit, (NEWPIN, RUN, BOOT))]:
            with self.subTest(function=function.__name__), self.assertRaises(ValueError):
                transition(function, self.head, *args)
        running = self.running()
        with self.assertRaises(ValueError):
            transition(L.admit, running, PIN, RUN2, BOOT)
        with self.assertRaises(ValueError):
            transition(L.mark_running, running)
        checkpoint = transition(L.mark_checkpoint, running, INTENT)
        with self.assertRaises(ValueError):
            transition(L.complete, checkpoint, FAILURE, NEWPIN)

    def test_automatic_boot_duplicate_explicit_restart(self):
        ready = self.completed()
        with self.assertRaises(ValueError):
            transition(L.admit, ready, NEWPIN, RUN2, BOOT, automatic=True)
        admitted = transition(L.admit, ready, NEWPIN, RUN2, BOOT)
        self.assertEqual(admitted['status'], 'admitted')
        with self.assertRaises(ValueError):
            transition(L.admit, ready, NEWPIN, RUN, BOOT)
        with self.assertRaises(ValueError):
            transition(L.admit, ready, NEWPIN, RUN2, BOOT, automatic=1)
        next_boot = '66666666-6666-4666-8666-666666666666'
        self.assertEqual(transition(L.admit, ready, NEWPIN, RUN2, next_boot,
                                    automatic=True)['last_boot_id'], next_boot)

    def test_repeated_complete_quarantine_and_completed_intent_retention(self):
        ready = self.completed()
        self.assertEqual(transition(L.complete, ready, INTENT, NEWPIN), ready)
        with self.assertRaises(ValueError):
            transition(L.complete, ready, INTENT, PIN)
        admitted = transition(L.admit, ready, NEWPIN, RUN2, BOOT)
        self.assertEqual(admitted['last_completed_intent_sha256'], INTENT)
        quarantined = transition(L.quarantine, admitted, FAILURE)
        self.assertEqual(quarantined['last_completed_intent_sha256'], INTENT)
        self.assertEqual(transition(L.quarantine, quarantined, FAILURE), quarantined)
        with self.assertRaises(ValueError):
            transition(L.quarantine, quarantined, INTENT)
        with self.assertRaises(ValueError):
            transition(L.admit, quarantined, NEWPIN, RUN, BOOT)
        initial_quarantine = transition(L.quarantine, self.head, FAILURE)
        self.assertIsNone(initial_quarantine['run_id'])

    def test_sequence_exhaustion_and_idempotent_maximum(self):
        head = {**self.completed(), 'sequence': L.MAX_SEQUENCE}
        for function, args in [(L.admit, (NEWPIN, RUN2, BOOT)), (L.quarantine, (FAILURE,))]:
            with self.assertRaises(ValueError):
                transition(function, head, *args)
        ready = {**self.completed(), 'sequence': L.MAX_SEQUENCE}
        self.assertEqual(transition(L.complete, ready, INTENT, NEWPIN), ready)


class MergeTests(unittest.TestCase):
    def setUp(self):
        self.registry = {'schema': 1, 'profile': L.REGISTRY.PROFILE, 'generation': 7,
                         'storage': copy.deepcopy(STORAGE), 'guests': []}
        for name, number in [('alpha', 60002), ('beta', 60003)]:
            self.registry['guests'].append({
                'name': name, 'uid': number, 'gid': number, 'manifest_sha256': PIN,
                'enabled': True, 'shutdown_profile': L.REGISTRY.SHUTDOWN_PROFILE,
                'fixture_profile': 'none',
            })

    def test_interleaved_guest_merges_retain_concurrent_disable(self):
        original = copy.deepcopy(self.registry)
        disabled = L.REGISTRY.update(self.registry, 'alpha', enabled=False,
                                     expected_generation=7)
        beta = L.merge_registry(disabled, 'beta', PIN, INTENT)
        alpha = L.merge_registry(beta, 'alpha', PIN, NEWPIN)
        self.assertEqual(self.registry, original)
        self.assertEqual(alpha['generation'], 10)
        guests = {guest['name']: guest for guest in alpha['guests']}
        self.assertFalse(guests['alpha']['enabled'])
        self.assertTrue(guests['beta']['enabled'])
        self.assertEqual(guests['alpha']['manifest_sha256'], NEWPIN)
        self.assertEqual(guests['beta']['manifest_sha256'], INTENT)
        self.assertEqual(alpha['storage'], STORAGE)
        self.assertEqual(L.merge_registry(alpha, 'alpha', PIN, NEWPIN), alpha)
        self.assertEqual(L.merge_registry(alpha, 'beta', PIN, INTENT), alpha)
        self.assertEqual(L.merge_registry(alpha, 'alpha', NEWPIN, NEWPIN), alpha)

    def test_merge_conflicts_missing_guest_and_exhaustion(self):
        with self.assertRaises(ValueError):
            L.merge_registry(self.registry, 'alpha', FAILURE, NEWPIN)
        with self.assertRaises(ValueError):
            L.merge_registry(self.registry, 'missing', PIN, NEWPIN)
        with self.assertRaises(ValueError):
            L.merge_registry(self.registry, 'alpha', PIN, 'invalid')
        exhausted = {**self.registry, 'generation': L.REGISTRY.MAX_GENERATION}
        with self.assertRaises(ValueError):
            L.merge_registry(exhausted, 'alpha', PIN, NEWPIN)
        self.assertEqual(L.merge_registry(exhausted, 'alpha', PIN, PIN), exhausted)


if __name__ == '__main__':
    unittest.main()
