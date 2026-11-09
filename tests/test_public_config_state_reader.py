"""Fixed public-directory STATE reader registration; no hardware acceptance."""
import ast
import contextlib
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import types
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
STATE_UUID = '360dfb39-f49f-487e-b307-0bb887c854c5'


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('/', '_'), REPO / name)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


V = load('build/verify-host-state-public-dirs.py')
L = load(V.LAUNCHER)
ROOTS = load('build/launch-host-roots.py')


def pin(raw):
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def record(raw):
    return dict(pin(raw), git_blob=hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest())


def contents():
    names = subprocess.check_output(['git', '-C', str(REPO), 'ls-files', '-z']).decode().split('\0')
    return {name: (REPO / name).read_bytes() for name in set(names + [V.SELF, L.SELF, V.EVIDENCE]) if name}


def receipt():
    data = contents()
    producer = json.loads(data[V.EVIDENCE])
    policy = dict(producer['policy'], producer=producer,
        operator=json.loads(data['docs/evidence/operator-inputs-20261105.json']),
        metadata_helper=pin(data['build/rootfs-metadata.py']))
    return dict(schema=1, success=True, state='complete', hardware_acceptance=False,
        state_bytes=8 * 1024**3, state_uuid=STATE_UUID, userspace_profile=ROOTS.PROFILE,
        metadata_profile=ROOTS.METADATA, host_inventory_unchanged=True, mapped_processes_final=[],
        phase_mounts_final=[], sources={'revision': 'a' * 40,
            'files': {name: record(raw) for name, raw in data.items()}}, policy=policy,
        public_dirs_controller=L.chain_binding(pin(data[L.TEMPLATE])))


class PublicConfigStateReaderTests(unittest.TestCase):
    def parent(self, stack):
        held = stack.enter_context(V.Template(REPO / V.TEMPLATE, V.TEMPLATE_SHA))
        parent = V.load_template(held)
        V.adapt(parent, held)
        return parent, held

    def test_historical_body_changes_only_four_registered_literals(self):
        raw = (REPO / V.TEMPLATE).read_bytes()
        self.assertEqual(pin(raw)['sha256'], V.TEMPLATE_SHA)
        original = ast.parse(raw)
        expected = copy.deepcopy(original)
        changes = []
        for before, after in zip(ast.walk(original), ast.walk(expected)):
            if isinstance(before, ast.Constant) and type(before.value) is str and before.value in V.SUBSTITUTIONS:
                changes.append(before.value)
                after.value = V.SUBSTITUTIONS[before.value]
        self.assertEqual(len(changes), 4)
        self.assertCountEqual(changes, V.SUBSTITUTIONS)
        self.assertEqual(ast.dump(V.adapted_ast(raw), include_attributes=True),
                         ast.dump(expected, include_attributes=True))
        with self.assertRaisesRegex(ValueError, 'SHA256'):
            V.adapted_ast(raw + b'\n')
        for anchor in V.SUBSTITUTIONS:
            for mutation in (raw.replace(anchor.encode(), b'unknown-anchor'),
                             raw + ('\nextra = ' + repr(anchor) + '\n').encode()):
                with self.subTest(anchor=anchor), mock.patch.object(V, 'TEMPLATE_SHA', pin(mutation)['sha256']):
                    with self.assertRaisesRegex(ValueError, 'anchors'):
                        V.adapted_ast(mutation)

    def test_registration_and_source_policy_require_exact_new_chain(self):
        raw = (REPO / V.EVIDENCE).read_bytes()
        self.assertEqual(pin(raw), V.EVIDENCE_PIN)
        self.assertEqual(record(raw)['git_blob'], V.EVIDENCE_BLOB)
        self.assertEqual(pin(json.dumps(json.loads(raw), sort_keys=True,
            separators=(',', ':')).encode())['sha256'], V.CANONICAL_SHA)
        self.assertEqual(V.EVIDENCE_PIN, L.EVIDENCE_PIN)
        self.assertEqual(V.CANONICAL_SHA, L.CANONICAL_SHA)
        self.assertEqual(V.EVIDENCE_BLOB, L.EVIDENCE_BLOB)
        for field, value in (('bytes', len(raw) + 1), ('sha256', 'a' * 64), ('git_blob', 'a' * 40)):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'source closure'):
                V.evidence_record(dict(record(raw), **{field: value}))
        with contextlib.ExitStack() as stack:
            parent, _ = self.parent(stack)
            good = receipt()
            self.assertEqual(parent.__file__, str(REPO / V.SELF))
            self.assertEqual(parent.source_policy(good), good['policy'])
            mutations = []
            def mutation(path, value):
                bad = copy.deepcopy(good)
                target = bad
                for name in path[:-1]:
                    target = target[name]
                target[path[-1]] = value
                mutations.append(bad)
            mutation(['public_dirs_controller', 'registered_parent'], 'unknown.json')
            mutation(['public_dirs_controller', 'registered_parent_pin', 'sha256'], 'a' * 64)
            mutation(['public_dirs_controller', 'registered_parent_canonical_sha256'], 'a' * 64)
            mutation(['sources', 'files', V.EVIDENCE, 'sha256'], 'a' * 64)
            mutation(['sources', 'files', V.EVIDENCE, 'git_blob'], 'a' * 40)
            mutation(['sources', 'files', V.TEMPLATE, 'sha256'], 'a' * 64)
            mutation(['sources', 'files', L.TEMPLATE, 'sha256'], 'a' * 64)
            mutation(['policy', 'producer', 'phase'], '/unexpected')
            mutation(['success'], False)
            mutation(['phase_mounts_final'], ['/unexpected'])
            bad = copy.deepcopy(good)
            bad['runtime_fixes_controller'] = bad.pop('public_dirs_controller')
            mutations.append(bad)
            bad = copy.deepcopy(good)
            del bad['sources']['files'][L.SELF]
            mutations.append(bad)
            for index, bad in enumerate(mutations):
                with self.subTest(index=index), self.assertRaises((ValueError, KeyError)):
                    parent.source_policy(bad)
            with mock.patch.object(V, 'parent_proof_schema', wraps=V.parent_proof_schema) as guard:
                parent.source_policy(good)
                guard.assert_called_once_with(good['policy']['producer'])
                guard.reset_mock()
                bad = copy.deepcopy(good)
                bad['policy']['producer']['expected_trees']['A']['members'] = 1
                with self.assertRaisesRegex(ValueError, 'committed evidence'):
                    parent.source_policy(bad)
                guard.assert_not_called()

    def test_parent_proof_schema_rejects_cross_slot_and_missing_bindings(self):
        producer = receipt()['policy']['producer']
        V.parent_proof_schema(producer)
        for slot in ('A', 'B'):
            bad = copy.deepcopy(producer)
            bad['readback']['slots'][slot] = bad['readback']['slots']['B' if slot == 'A' else 'A']
            with self.assertRaisesRegex(ValueError, 'registered parent proof'):
                V.parent_proof_schema(bad)
            for field in ('expected', 'stability', 'namespace_members'):
                bad = copy.deepcopy(producer)
                del bad['readback']['slots'][slot][field]
                with self.subTest(slot=slot, field=field), self.assertRaisesRegex(ValueError, 'registered parent proof'):
                    V.parent_proof_schema(bad)
            bad = copy.deepcopy(producer)
            bad['expected_trees'][slot]['members'] = True
            with self.assertRaisesRegex(ValueError, 'registered parent proof'):
                V.parent_proof_schema(bad)
        # Isolate the registration hook to prove its schema check remains active
        # after an otherwise successful historical policy authentication.
        with contextlib.ExitStack() as stack:
            held = stack.enter_context(V.Template(REPO / V.TEMPLATE, V.TEMPLATE_SHA))
            parent = V.load_template(held)
            parent.source_policy = lambda rec: rec['policy']
            V.adapt(parent, held)
            bad = receipt()
            bad['policy']['producer']['expected_trees']['A']['members'] = 1
            with self.assertRaisesRegex(ValueError, 'registered parent proof'):
                parent.source_policy(bad)

    def test_independent_parent_marker_rejects_wrong_slot_count_pin_and_selector(self):
        producer = receipt()['policy']['producer']
        expected = producer['expected_trees']['A']
        directory = dict(type='dir', mode=0o755, uid=0, gid=0, mtime_ns=0, xattrs={})
        marker = dict(type='file', mode=0o444, uid=0, gid=0, mtime_ns=0,
                      xattrs={}, data=pin(b'public-directory root marker fixture'))
        members = {name: dict(directory) for name in ('.', 'usr', 'usr/lib', 'usr/lib/orangepi-hypervisor')}
        members['usr/lib/orangepi-hypervisor/payload.json'] = marker
        for index in range(expected['members'] - len(members)):
            members['fixture-%05d' % index] = dict(marker)
        proof = dict(schema=1, slot='A', members=members)
        selected = {'ROOT_MARKER_SHA256': marker['data']['sha256']}
        held = types.SimpleNamespace(pin=expected['expected'])
        with contextlib.ExitStack() as stack:
            parent, _ = self.parent(stack)
            with mock.patch.object(parent.R, 'json_fd', return_value=proof):
                self.assertEqual(parent.parent_marker_binding(held, {'producer': producer}, selected), marker['data'])
                with self.assertRaisesRegex(ValueError, 'selected marker'):
                    parent.parent_marker_binding(held, {'producer': producer}, {'ROOT_MARKER_SHA256': '0' * 64})
                wrong = types.SimpleNamespace(pin=producer['expected_trees']['B']['expected'])
                with self.assertRaisesRegex(ValueError, 'proof pin'):
                    parent.parent_marker_binding(wrong, {'producer': producer}, selected)
            for bad in (dict(proof, slot='B'), dict(proof, members={})):
                with mock.patch.object(parent.R, 'json_fd', return_value=bad):
                    with self.assertRaisesRegex(ValueError, 'proof identity'):
                        parent.parent_marker_binding(held, {'producer': producer}, selected)

    def test_publication_checks_template_before_success_and_reports_failure(self):
        with contextlib.ExitStack() as stack:
            held = stack.enter_context(V.Template(REPO / V.TEMPLATE, V.TEMPLATE_SHA))
            parent = V.load_template(held)
            events, reports = [], []
            def sink(path, report):
                events.append('publish')
                reports.append(copy.deepcopy(report))
            parent.R.publish = sink
            V.adapt(parent, held)
            with mock.patch.object(held, 'unchanged', side_effect=lambda: events.append('check')):
                parent.R.publish(Path('/unused/receipt.json'), {'success': True, 'state': 'complete'})
            self.assertEqual(events, ['check', 'publish'])
            self.assertEqual(reports[-1]['public_dirs_controller'], V.chain_binding(held.pin))
            with mock.patch.object(held, 'unchanged', side_effect=ValueError('template replaced')):
                with self.assertRaisesRegex(ValueError, 'template replaced'):
                    parent.R.publish(Path('/unused/receipt.json'), {'success': True, 'state': 'complete'})
            self.assertFalse(reports[-1]['success'])
            self.assertEqual(reports[-1]['state'], 'failed')
            self.assertIn('template replaced', reports[-1]['error'])

    def test_uuid_guards_semantic_and_actual_ext4_checks_preserved(self):
        fixtures = load('tests/test_verify_host_state.py')
        with contextlib.ExitStack() as stack:
            parent, _ = self.parent(stack)
            self.assertEqual(parent.state_uuid(STATE_UUID), STATE_UUID)
            for value in (*V.FORBIDDEN_UUIDS, *ROOTS.SLOT_UUIDS.values(), '0' * 32, STATE_UUID.upper()):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    parent.state_uuid(value)
            with mock.patch.object(fixtures, 'm', parent), mock.patch.object(fixtures, 'UUID', STATE_UUID):
                for name in ('test_semantic_overlay_and_staging_binding',
                             'test_independent_parent_marker_rejects_agreed_wrong_selector',
                             'test_output_rejected_before_creation_and_failure_journal',
                             'test_actual_journal_writable_filesystem_full_readback'):
                    result = unittest.TestResult()
                    fixtures.StateVerifierTests(name).run(result)
                    self.assertEqual(result.skipped, [], name)
                    self.assertTrue(result.wasSuccessful(), (name, result.errors, result.failures))

    def test_historical_registration_and_cli_unchanged(self):
        self.assertEqual(pin((REPO / 'build/verify-host-state-runtime-fixes.py').read_bytes())['sha256'],
                         '48e9052c599b4b40f542a3332e03b3745c50da4293a5cb56d1b4422300efb1ee')
        original = subprocess.run([sys.executable, '-I', '-B', str(REPO / V.TEMPLATE), '--help'], capture_output=True, text=True)
        adapted = subprocess.run([sys.executable, '-I', '-B', str(REPO / V.SELF), '--help'], capture_output=True, text=True)
        self.assertEqual(original.returncode, 0, original.stderr)
        self.assertEqual(adapted.returncode, 0, adapted.stderr)
        self.assertEqual(adapted.stdout.replace(Path(V.SELF).name, Path(V.TEMPLATE).name).split(), original.stdout.split())
        result = subprocess.run([sys.executable, '-B', str(REPO / V.SELF), '--help'], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('invoke Python with -I -B', result.stderr)


if __name__ == '__main__':
    unittest.main()
