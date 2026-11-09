"""Fixed public-directory root registration; regular-file planning only."""
import argparse
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
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


L = load('build/launch-host-state-public-dirs.py')
ROOTS = load('build/launch-host-roots.py')


def pin(raw):
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def record(raw):
    return dict(pin(raw), git_blob=hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest())


def contents():
    names = subprocess.check_output(['git', '-C', str(REPO), 'ls-files', '-z']).decode().split('\0')
    return {name: (REPO / name).read_bytes() for name in set(names + [L.SELF, L.EVIDENCE]) if name}


def options(parent, producer):
    return argparse.Namespace(userspace=Path('/missing/rootfs.tar.gz'), config=Path('/missing/config'),
        admin_keys=Path('/missing/key'), roots_receipt=parent.PRODUCER_PHASE / 'receipt.json',
        roots_receipt_sha256=producer['producer_records']['receipt.json']['sha256'],
        state_uuid=STATE_UUID, tag='public-dirs-test')


class PublicConfigStateAdapterTests(unittest.TestCase):
    def parent(self, stack):
        held = stack.enter_context(L.Template(REPO / L.TEMPLATE, L.TEMPLATE_SHA))
        parent = L.load_template(held)
        L.adapt(parent, held)
        return parent, held

    def test_historical_body_changes_only_five_registered_literals(self):
        raw = (REPO / L.TEMPLATE).read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), L.TEMPLATE_SHA)
        original = ast.parse(raw)
        expected = copy.deepcopy(original)
        changes = []
        for before, after in zip(ast.walk(original), ast.walk(expected)):
            if isinstance(before, ast.Constant) and type(before.value) is str and before.value in L.SUBSTITUTIONS:
                changes.append(before.value)
                after.value = L.SUBSTITUTIONS[before.value]
        self.assertEqual(len(changes), 5)
        self.assertCountEqual(changes, L.SUBSTITUTIONS)
        self.assertEqual(ast.dump(L.adapted_ast(raw), include_attributes=True),
                         ast.dump(expected, include_attributes=True))
        with self.assertRaisesRegex(ValueError, 'SHA256'):
            L.adapted_ast(raw + b'\n')
        for anchor in L.SUBSTITUTIONS:
            for mutation in (raw.replace(anchor.encode(), b'unknown-anchor'),
                             raw + ('\nextra = ' + repr(anchor) + '\n').encode()):
                with self.subTest(anchor=anchor), mock.patch.object(L, 'TEMPLATE_SHA', pin(mutation)['sha256']):
                    with self.assertRaisesRegex(ValueError, 'anchors'):
                        L.adapted_ast(mutation)

    def test_registered_bytes_parsed_hash_and_git_blob_are_independent_guards(self):
        data = contents()
        raw = data[L.EVIDENCE]
        self.assertEqual(pin(raw), L.EVIDENCE_PIN)
        self.assertEqual(record(raw)['git_blob'], L.EVIDENCE_BLOB)
        canonical = json.dumps(json.loads(raw), sort_keys=True, separators=(',', ':')).encode()
        self.assertEqual(pin(canonical)['sha256'], L.CANONICAL_SHA)
        L.evidence_record(record(raw))
        for field, value in (('bytes', len(raw) + 1), ('sha256', 'a' * 64), ('git_blob', 'a' * 40)):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'source closure'):
                L.evidence_record(dict(record(raw), **{field: value}))
        with contextlib.ExitStack() as stack:
            parent, _ = self.parent(stack)
            with self.assertRaisesRegex(ValueError, 'evidence bytes'):
                parent.contract(ROOTS, REPO, dict(data, **{L.EVIDENCE: raw + b'\n'}))
            with mock.patch.object(L, 'CANONICAL_SHA', 'a' * 64):
                with self.assertRaisesRegex(ValueError, 'parsed root evidence'):
                    parent.contract(ROOTS, REPO, data)
            with mock.patch.object(L, 'parent_proof_schema', wraps=L.parent_proof_schema) as guard:
                parent.contract(ROOTS, REPO, data)
                guard.assert_called_once_with(json.loads(raw))
                guard.reset_mock()
                with self.assertRaises(ValueError):
                    parent.contract(ROOTS, REPO, dict(data, **{L.EVIDENCE: raw + b'\n'}))
                guard.assert_not_called()

    def test_new_parent_plan_preserves_guards_and_reports_exact_provenance(self):
        data = contents()
        with contextlib.ExitStack() as stack:
            parent, held = self.parent(stack)
            policy = parent.contract(ROOTS, REPO, data)
            manifest = {'revision': 'a' * 40, 'files': {name: record(raw) for name, raw in data.items()}}
            opts = options(parent, policy['producer'])
            with mock.patch.object(Path, 'lstat', side_effect=AssertionError('input inspection')), \
                 mock.patch.object(Path, 'mkdir', side_effect=AssertionError('staging')):
                report = parent.plan(opts, manifest, policy)
            self.assertEqual(parent.__file__, str(REPO / L.SELF))
            self.assertEqual(parent.SELF, L.SELF)
            self.assertEqual(str(parent.PRODUCER_OUTPUT),
                '/var/lib/a733-clean-replay-20261106/host-roots-timesync-public-dirs-01/work/project/out/host-payload-timesync-public-dirs-01')
            self.assertEqual(policy['producer']['producer_revision'], '4f40843365566ae4ada9b7377b75bba3f40c3360')
            self.assertEqual(report['public_dirs_controller'], L.chain_binding(held.pin))
            self.assertNotIn('runtime_fixes_controller', report)
            self.assertEqual(report['public_dirs_controller']['registered_parent'], L.EVIDENCE)
            self.assertEqual(report['state_uuid'], STATE_UUID)
            self.assertEqual(report['state_bytes'], 8 * 1024**3)
            self.assertFalse(report['hardware_acceptance'])
            self.assertFalse(report['full_filesystem_readback'])
            self.assertIn('fresh machine identity intentionally differs', report['reproducibility'])
            self.assertEqual(report['minimum_initial_available_memory_bytes'], 2 * 1024**3)
            self.assertEqual(report['runtime_minimum_available_memory_bytes'], 1_000_000_000)
            self.assertEqual(report['runtime_minimum_free_bytes'], 3_000_000_000)
            for field, value in (('roots_receipt', Path('/var/lib/a733-clean-replay-20261106/host-roots-timesync-runtime-fixes-01/receipt.json')),
                                 ('roots_receipt_sha256', 'a' * 64)):
                bad = copy.copy(opts)
                setattr(bad, field, value)
                with self.assertRaises(ValueError):
                    parent.plan(bad, manifest, policy)
            bad = copy.deepcopy(manifest)
            bad['files'][L.EVIDENCE]['git_blob'] = 'a' * 40
            with self.assertRaisesRegex(ValueError, 'source closure'):
                parent.plan(opts, bad, policy)
            for path in (L.TEMPLATE, 'build/verify-host-roots.py'):
                bad = dict(data, **{path: data[path] + b'\n'})
                with self.assertRaises(ValueError):
                    parent.contract(ROOTS, REPO, bad)

    def test_parent_proof_links_each_slot_and_refuses_cross_slot_or_incompatible_records(self):
        producer = json.loads((REPO / L.EVIDENCE).read_bytes())
        L.parent_proof_schema(producer)
        for slot in ('A', 'B'):
            mutations = []
            bad = copy.deepcopy(producer)
            bad['readback']['slots'][slot] = bad['readback']['slots']['B' if slot == 'A' else 'A']
            mutations.append(bad)
            for field, value in (('expected', {'bytes': 1, 'sha256': '0' * 64}),
                                 ('stability', {'bytes': 57, 'sha256': 'f' * 64}),
                                 ('members', True), ('members', 1), ('independent_image_readback', True)):
                bad = copy.deepcopy(producer)
                bad['expected_trees'][slot][field] = value
                mutations.append(bad)
            for field in ('expected', 'stability', 'namespace_members'):
                bad = copy.deepcopy(producer)
                del bad['readback']['slots'][slot][field]
                mutations.append(bad)
            for bad in mutations:
                with self.subTest(slot=slot), self.assertRaisesRegex(ValueError, 'registered parent proof'):
                    L.parent_proof_schema(bad)
        # Authentication alone never bypasses the parent-proof schema preflight.
        data = contents()
        bad = copy.deepcopy(producer)
        bad['expected_trees']['A'] = bad['expected_trees']['A']['expected']
        raw = json.dumps(bad, sort_keys=True, separators=(',', ':')).encode()
        with contextlib.ExitStack() as stack:
            parent, _ = self.parent(stack)
            with mock.patch.object(L, 'EVIDENCE_PIN', pin(raw)), \
                 mock.patch.object(L, 'CANONICAL_SHA', pin(raw)['sha256']), \
                 mock.patch.object(Path, 'mkdir', side_effect=AssertionError('staging')):
                with self.assertRaisesRegex(ValueError, 'registered parent proof'):
                    parent.contract(ROOTS, REPO, dict(data, **{L.EVIDENCE: raw}))

    def test_original_uuid_refusals_and_intended_protected_boot_uuid(self):
        historical = load('build/launch-host-state-runtime-fixes.py')
        self.assertEqual(L.FORBIDDEN_UUIDS, historical.FORBIDDEN_UUIDS)
        with contextlib.ExitStack() as stack:
            parent, _ = self.parent(stack)
            self.assertEqual(parent.state_uuid(STATE_UUID), STATE_UUID)
            for value in (*L.FORBIDDEN_UUIDS, *ROOTS.SLOT_UUIDS.values(),
                          '00000000-0000-0000-0000-000000000000', STATE_UUID.upper(), '/dev/nvme0n1'):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    parent.state_uuid(value)

    def test_final_template_check_still_follows_original_execution_checks(self):
        with contextlib.ExitStack() as stack:
            held = stack.enter_context(L.Template(REPO / L.TEMPLATE, L.TEMPLATE_SHA))
            parent = L.load_template(held)
            events = []
            fake_roots = types.SimpleNamespace(execute=lambda *args, **kw: kw['final_check']())
            parent.module = lambda *args: fake_roots
            L.adapt(parent, held)
            wrapped = parent.module(parent.ROOTS, b'fixture')
            with mock.patch.object(held, 'unchanged', side_effect=lambda: events.append('template')):
                wrapped.execute(final_check=lambda: events.append('original'))
            self.assertEqual(events, ['original', 'template'])
            with mock.patch.object(held, 'unchanged', side_effect=ValueError('changed')):
                with self.assertRaisesRegex(ValueError, 'changed'):
                    wrapped.execute(final_check=lambda: None)

    def test_historical_registrations_unchanged_and_original_cli_preserved(self):
        for path, expected in (
            (L.TEMPLATE, L.TEMPLATE_SHA),
            ('build/launch-host-state-runtime-fixes.py', 'f137beb5512c4f370806191bb1a2646a367bce8514154f5b64a369de04e4629c'),
            ('build/verify-host-state-runtime-fixes.py', '48e9052c599b4b40f542a3332e03b3745c50da4293a5cb56d1b4422300efb1ee'),
            ('build/launch-host-state-repeat.py', '5e2998921932607614e51b5f0177917afed24b7a821be5ebad44c4475eeedace')):
            self.assertEqual(pin((REPO / path).read_bytes())['sha256'], expected, path)
        original = subprocess.run([sys.executable, '-I', '-B', str(REPO / L.TEMPLATE), '--help'], capture_output=True, text=True)
        adapted = subprocess.run([sys.executable, '-I', '-B', str(REPO / L.SELF), '--help'], capture_output=True, text=True)
        self.assertEqual(original.returncode, 0, original.stderr)
        self.assertEqual(adapted.returncode, 0, adapted.stderr)
        self.assertEqual(adapted.stdout.replace(Path(L.SELF).name, Path(L.TEMPLATE).name).split(),
                         original.stdout.split())
        result = subprocess.run([sys.executable, '-B', str(REPO / L.SELF), '--help'], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('invoke Python with -I -B', result.stderr)


if __name__ == '__main__':
    unittest.main()
