"""Host-only runtime-fixes controller fixtures; no builder or hardware acceptance."""
import argparse
import ast
import contextlib
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('/', '_'), REPO / name)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


L = load('build/launch-host-bootstrap-runtime-fixes.py')
S = load('build/launch-host-state-runtime-fixes.py')


def pin(raw):
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def record(raw):
    return dict(pin(raw), git_blob=hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest())


def contents():
    names = subprocess.check_output(['git', '-C', str(REPO), 'ls-files', '-z']).decode().split('\0')
    names += [L.SELF]
    data = {name: (REPO / name).read_bytes() for name in names if name}
    return data


class RuntimeFixesTests(unittest.TestCase):
    def setup_parent(self, stack, data=None, roots=None):
        data = contents() if data is None else data
        held = stack.enter_context(L.Template(REPO / L.TEMPLATE, L.TEMPLATE_SHA))
        parent = L.load_template(held)
        L.adapt(parent, held, stack)
        roots = load('build/launch-host-roots.py') if roots is None else roots
        return parent, held, roots, data

    def options(self, parent):
        return argparse.Namespace(userspace=Path('/missing/rootfs.tar.gz'), diagnostic=Path('/missing/diag'),
            busybox=Path('/missing/busybox'), state_receipt=parent.STATE_PHASE / 'receipt.json',
            state_receipt_sha256=parent.STATE_RECEIPT_SHA, tag='repeat-fixture')

    def test_only_counted_constant_changes_and_original_body_unchanged(self):
        raw = (REPO / L.TEMPLATE).read_bytes()
        tree = ast.parse(raw)
        expected = copy.deepcopy(tree)
        counts = dict.fromkeys(L.SUBSTITUTIONS, 0)
        for node in ast.walk(expected):
            if isinstance(node, ast.Constant) and type(node.value) is str and node.value in L.SUBSTITUTIONS:
                old = node.value
                counts[old] += 1
                node.value = L.SUBSTITUTIONS[old]
        self.assertEqual(counts, L.COUNTS)
        self.assertEqual(ast.dump(expected, include_attributes=True),
                         ast.dump(L.adapted_ast(raw), include_attributes=True))
        self.assertEqual((REPO / L.TEMPLATE).read_bytes(), raw)
        with self.assertRaisesRegex(ValueError, 'SHA256'):
            L.adapted_ast(raw + b'\n')
        for changed in (raw.replace(b'host-state-timesync-02', b'other'),
                        raw + b'\nextra = "host-state-timesync-02"\n'):
            with mock.patch.object(L, 'TEMPLATE_SHA', hashlib.sha256(changed).hexdigest()):
                with self.assertRaisesRegex(ValueError, 'anchors'):
                    L.adapted_ast(changed)

    def test_actual_registered_parent_full_contract_and_plan(self):
        # Every registration and policy parent comes from the repository. No
        # fictional producer fields or registration-pin patches enter this test.
        data = contents()
        raw = (REPO / L.EVIDENCE).read_bytes()
        data[L.EVIDENCE] = raw
        self.assertEqual(pin(raw), L.EVIDENCE_PIN)
        self.assertEqual(record(raw)['git_blob'], L.EVIDENCE_BLOB)
        self.assertEqual(hashlib.sha256(json.dumps(json.loads(raw), sort_keys=True,
            separators=(',', ':')).encode()).hexdigest(), L.CANONICAL_SHA)
        with contextlib.ExitStack() as stack:
            held = stack.enter_context(L.Template(REPO / L.TEMPLATE, L.TEMPLATE_SHA))
            parent = L.load_template(held)
            L.adapt(parent, held, stack)
            roots = load('build/launch-host-roots.py')
            policy = parent.contract(roots, REPO, data)
            manifest = {'revision': 'a' * 40, 'files': {name: record(value) for name, value in data.items()}}
            report = parent.plan(self.options(parent), manifest, policy)
            self.assertEqual(policy['state_producer'], json.loads(raw))
            self.assertEqual(policy['state_policy']['producer'], json.loads(data[S.EVIDENCE]))
            # Canonical policy hash measured from the SHA-authenticated accepted
            # producer receipt (a25aa352…), including the complete nested trees.
            self.assertEqual(hashlib.sha256(json.dumps(policy['state_policy'], sort_keys=True,
                separators=(',', ':')).encode()).hexdigest(),
                '003a0573b0647250e3902c5b93c6ee3c9cb5567696b445bbc532188dadb2d58f')
            self.assertEqual(report['runtime_fixes_controller']['registered_parent_pin'], L.EVIDENCE_PIN)
            self.assertEqual(report['state_receipt_sha256'],
                'a25aa352b42bc49c13218d61587ea68636e83d21b77a34af52c6fd68db12ae88')
            self.assertEqual(report['state_uuid'], '360dfb39-f49f-487e-b307-0bb887c854c5')
            self.assertEqual(report['boot_profile'], 'experimental-el2')
            self.assertFalse(report['hardware_acceptance'])

    def test_actual_parent_proof_keeps_complete_expected_stability_member_bindings(self):
        with contextlib.ExitStack() as stack:
            parent, _, roots, data = self.setup_parent(stack)
            policy = parent.contract(roots, REPO, data)
            producer = policy['state_policy']['producer']
            S.parent_proof_schema(producer)
            for slot in ('A', 'B'):
                expected = producer['expected_trees'][slot]
                actual = producer['readback']['slots'][slot]
                self.assertEqual(set(expected), {'expected', 'stability', 'members', 'independent_image_readback'})
                self.assertEqual(expected['expected'], actual['expected'])
                self.assertEqual(expected['stability'], actual['stability'])
                self.assertEqual(expected['members'], actual['namespace_members'])
                for field in ('expected', 'stability', 'members'):
                    bad = copy.deepcopy(producer)
                    del bad['expected_trees'][slot][field]
                    with self.subTest(slot=slot, field=field), self.assertRaises(ValueError):
                        S.parent_proof_schema(bad)
            bad = copy.deepcopy(producer)
            bad['expected_trees'] = {slot: producer['expected_trees'][slot]['expected'] for slot in ('A', 'B')}
            with self.assertRaises(ValueError):
                S.parent_proof_schema(bad)

    def test_pending_registration_fails_closed(self):
        with contextlib.ExitStack() as stack:
            held = stack.enter_context(L.Template(REPO / L.TEMPLATE, L.TEMPLATE_SHA))
            parent = L.load_template(held)
            L.adapt(parent, held, stack)
            with mock.patch.object(L, 'EVIDENCE_PIN', None):
                with self.assertRaisesRegex(ValueError, 'pins pending'):
                    parent.contract(None, REPO, {})

    def test_full_contract_and_plan_and_immutable_original_defaults(self):
        with contextlib.ExitStack() as stack:
            parent, held, roots, data = self.setup_parent(stack)
            policy = parent.contract(roots, REPO, data)
            manifest = {'revision': 'a' * 40, 'files': {name: record(raw) for name, raw in data.items()}}
            with mock.patch.object(Path, 'mkdir', side_effect=AssertionError('staging')):
                report = parent.plan(self.options(parent), manifest, policy)
            self.assertEqual(parent.__file__, str(REPO / L.SELF))
            self.assertEqual(report['state_uuid'], '360dfb39-f49f-487e-b307-0bb887c854c5')
            self.assertEqual(report['boot_profile'], 'experimental-el2')
            self.assertEqual(report['actual_sd_bytes'], 1977614336)
            self.assertEqual(report['image_bytes'], 256 * 1024**2)
            self.assertFalse(report['hardware_acceptance'])
            self.assertEqual(report['runtime_fixes_controller']['historical_template'], held.pin)
            self.assertEqual(policy['state_policy']['producer']['phase'],
                '/var/lib/a733-clean-replay-20261106/host-roots-timesync-runtime-fixes-01')
            state = roots.module(parent.STATE, data[parent.STATE])
            self.assertIs(state, roots.module(str(REPO / parent.STATE), data[parent.STATE]))
            self.assertTrue(callable(state.held_class))
            self.assertEqual(state.__file__, str(REPO / parent.STATE))
            original = load(L.TEMPLATE)
            old_policy = {'state_uuid': original.STATE_UUID, 'state_producer':
                          json.loads(data[original.PRODUCTION])}
            old_report = original.plan(self.options(original), {}, old_policy)
            self.assertEqual(old_report['state_uuid'], 'fc8bbef6-9385-4ddb-90ba-e41eaccc035b')
            self.assertNotIn('runtime_fixes_controller', old_report)
            for name, value in [('state_receipt', original.STATE_PHASE / 'receipt.json'),
                                ('state_receipt_sha256', 'a' * 64)]:
                options = self.options(parent)
                setattr(options, name, value)
                with self.assertRaises(ValueError):
                    parent.plan(options, manifest, policy)
            bad_manifest = copy.deepcopy(manifest)
            bad_manifest['files'][L.EVIDENCE]['git_blob'] = 'a' * 40
            with self.assertRaisesRegex(ValueError, 'source closure'):
                parent.plan(self.options(parent), bad_manifest, policy)
            with self.assertRaisesRegex(ValueError, 'module data'):
                roots.module(parent.STATE, data[parent.STATE] + b'\n')
            with self.assertRaisesRegex(ValueError, 'module path'):
                roots.module('/different/' + Path(parent.STATE).name, data[parent.STATE])
            self.assertEqual(roots.module('other.py', b'answer = 42').answer, 42)

    def test_registered_evidence_and_state_closure_drift_rejected(self):
        for name, mutate, message in [
            (L.EVIDENCE, lambda raw: raw + b'\n', 'evidence bytes'),
            (L.STATE, lambda raw: raw + b'\n', 'state adapter'),
            (L.TEMPLATE, lambda raw: raw + b'\n', 'outside committed closure'),
            (S.TEMPLATE, lambda raw: raw + b'\n', 'historical state launcher'),
            (S.EVIDENCE, lambda raw: raw + b'\n', 'root evidence bytes')]:
            with self.subTest(name=name), contextlib.ExitStack() as stack:
                parent, _, roots, data = self.setup_parent(stack)
                data[name] = mutate(data[name])
                with self.assertRaisesRegex(ValueError, message):
                    parent.contract(roots, REPO, data)
        for key, value in [('state_uuid', 'fc8bbef6-9385-4ddb-90ba-e41eaccc035b'),
                           ('phase', '/wrong'), ('source_revision', 'a' * 40)]:
            data = contents()
            producer = json.loads(data[L.EVIDENCE]); producer[key] = value
            data[L.EVIDENCE] = json.dumps(producer).encode()
            with self.subTest(key=key), contextlib.ExitStack() as stack:
                parent, _, roots, data = self.setup_parent(stack, data)
                raw = data[L.EVIDENCE]
                stack.enter_context(mock.patch.object(L, 'EVIDENCE_PIN', pin(raw)))
                canonical = hashlib.sha256(json.dumps(json.loads(raw), sort_keys=True, separators=(',', ':')).encode()).hexdigest()
                stack.enter_context(mock.patch.object(L, 'CANONICAL_SHA', canonical))
                with self.assertRaisesRegex(ValueError, 'identity/readback'):
                    parent.contract(roots, REPO, data)

    def test_original_authenticate_state_requires_exact_policy_manifest_and_readback(self):
        with contextlib.ExitStack() as stack:
            parent, _, roots, data = self.setup_parent(stack)
            policy = parent.contract(roots, REPO, data)
            producer = policy['state_producer']; state = policy['state_policy']
            held = {name: types.SimpleNamespace(pin=value) for name, value in (
                ('state-receipt.json', producer['producer_records']['receipt.json']),
                ('state-manifest.json', producer['verification']['manifest']),
                ('state-readback.json', producer['readback']['receipt']))}
            receipt = dict(success=True, state='complete', phase=str(parent.STATE_PHASE), output=producer['output'],
                state_uuid=parent.STATE_UUID, state_bytes=8 * 1024**3,
                sources={'revision': producer['source_revision']}, userspace_profile=producer['profile'],
                metadata_profile=producer['metadata_profile'], policy=state, verification=producer['verification'])
            manifest = dict(schema=1, state_uuid=parent.STATE_UUID, state_bytes=8 * 1024**3,
                boot_selection=producer['boot_selection'], files={'state.ext4': producer['state_image']},
                sources={'rootfs.tar.gz': state['archive'], 'userspace_profile': state['binding'],
                         'timesync_state': state['clock']})
            readback = dict(schema=1, success=True, state='complete',
                full_namespace_metadata_content_readback=True, input_identity_unchanged=True,
                producer_receipt=held['state-receipt.json'].pin, state_manifest=held['state-manifest.json'].pin,
                source_policy=state, parent_root_marker=producer['parent_roots']['readback_expected'],
                filesystem=producer['readback']['filesystem'], image=producer['state_image'])
            records = [receipt, manifest, readback]
            def authenticate(rows):
                verifier = types.SimpleNamespace(json_fd=mock.Mock(side_effect=rows))
                parent.authenticate_state(verifier, held, policy)
            authenticate(records)
            for index, field, value in [(0, 'policy', {}), (0, 'state_uuid', 'wrong'),
                (0, 'sources', {'revision': 'a' * 40}), (1, 'boot_selection', {}),
                (2, 'source_policy', {}), (2, 'producer_receipt', {}),
                (2, 'input_identity_unchanged', False), (2, 'filesystem', {}),
                (2, 'parent_root_marker', {}), (2, 'image', {})]:
                bad = copy.deepcopy(records); bad[index][field] = value
                with self.subTest(index=index, field=field), self.assertRaises(ValueError):
                    authenticate(bad)

    def test_final_check_order_and_all_held_templates_before_success(self):
        events = []
        roots = load('build/launch-host-roots.py')
        roots.execute = lambda *args, **kwargs: (kwargs['final_check'](), events.append('success'))
        with contextlib.ExitStack() as stack:
            parent, held, roots, data = self.setup_parent(stack, roots=roots)
            parent.contract(roots, REPO, data)
            with mock.patch.object(held, 'unchanged', side_effect=lambda: events.append('bootstrap')):
                roots.execute(final_check=lambda: events.append('original'))
            self.assertEqual(events, ['original', 'bootstrap', 'success'])
            with mock.patch.object(held, 'unchanged', side_effect=ValueError('held changed')):
                with self.assertRaisesRegex(ValueError, 'held changed'):
                    roots.execute(final_check=lambda: events.append('original'))
            self.assertEqual(events.count('success'), 1)
            with mock.patch.object(held, 'unchanged', side_effect=AssertionError('must run later')):
                with self.assertRaisesRegex(ValueError, 'original failed'):
                    roots.execute(final_check=lambda: (_ for _ in ()).throw(ValueError('original failed')))

    def test_state_adapter_and_historical_state_hold_survive_until_final_publication(self):
        for target in (L.STATE, S.TEMPLATE):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as directory:
                project = Path(directory)
                data = contents()
                for name, raw in data.items():
                    path = project / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(raw)
                events = []
                roots = load('build/launch-host-roots.py')
                roots.execute = lambda *a, **kw: (kw['final_check'](), events.append('success'))
                stack = contextlib.ExitStack()
                try:
                    parent, _, roots, data = self.setup_parent(stack, data, roots)
                    parent.contract(roots, project, data)
                    path = project / target
                    replacement = project / 'replacement.py'
                    replacement.write_bytes(path.read_bytes())
                    replacement.replace(path)
                    with self.assertRaisesRegex(ValueError, 'template changed'):
                        roots.execute(final_check=lambda: events.append('original'))
                    self.assertEqual(events, ['original'])
                finally:
                    # __exit__ independently detects the same intentional
                    # replacement; it still closes every descriptor.
                    with self.assertRaisesRegex(ValueError, 'template changed'):
                        stack.close()

    def test_template_rejects_unsafe_inputs_and_detects_replacement(self):
        raw = (REPO / L.TEMPLATE).read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); path = root / 'template.py'; path.write_bytes(raw)
            for kind in ('symlink', 'hardlink', 'oversize', 'fifo', 'parent-symlink'):
                bad = root / kind
                if kind == 'symlink': bad.symlink_to(path)
                elif kind == 'hardlink': os.link(path, bad)
                elif kind == 'oversize': bad.write_bytes(b'x' * (1024**2 + 1))
                elif kind == 'fifo': os.mkfifo(bad)
                else:
                    bad.symlink_to(root, target_is_directory=True); bad = bad / path.name
                with self.subTest(kind=kind), self.assertRaises((ValueError, OSError)):
                    with L.Template(bad, L.TEMPLATE_SHA): pass
                if kind == 'hardlink': bad.unlink()
            for mutation in ('replace', 'bytes', 'ancestor'):
                path.write_bytes(raw)
                held = L.Template(path, L.TEMPLATE_SHA).__enter__()
                try:
                    if mutation == 'replace':
                        replacement = root / 'new'; replacement.write_bytes(raw); replacement.replace(path)
                    elif mutation == 'bytes': path.write_bytes(raw + b'\n')
                    else:
                        moved = root.with_name(root.name + '-moved')
                        root.rename(moved); root.mkdir(); (root / path.name).write_bytes(raw)
                    with self.assertRaises(ValueError): held.unchanged()
                finally:
                    held.close()
                    if mutation == 'ancestor':
                        (root / path.name).unlink(); root.rmdir(); moved.rename(root)

    def test_cli_retains_options_and_isolation(self):
        result = subprocess.run([sys.executable, '-I', '-B', str(REPO / L.SELF), '--help'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--state-receipt-sha256', result.stdout)
        result = subprocess.run([sys.executable, '-B', str(REPO / L.SELF), '--help'],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('invoke Python with -I -B', result.stderr)


if __name__ == '__main__':
    unittest.main()
