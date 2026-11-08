"""Registered runtime-fixes adapters: no device access, builders or hardware acceptance."""
import argparse
import ast
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
FRESH_UUID = 'eadc8eb3-cba7-486a-aa6d-8c0aff8ccf75'


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('/', '_'), REPO / name)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


L = load('build/launch-host-state-runtime-fixes.py')
V = load('build/verify-host-state-runtime-fixes.py')
ROOTS = load('build/launch-host-roots.py')


def pin(raw):
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def record(raw):
    return dict(pin(raw), git_blob=hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest())


def contents():
    names = subprocess.check_output(['git', '-C', str(REPO), 'ls-files', '-z']).decode().split('\0')
    names += [L.SELF, V.SELF]
    return {name: (REPO / name).read_bytes() for name in names if name}


def options(parent):
    return argparse.Namespace(userspace=Path('/missing/rootfs.tar.gz'), config=Path('/missing/config'),
        admin_keys=Path('/missing/key'), roots_receipt=parent.PRODUCER_PHASE / 'receipt.json',
        roots_receipt_sha256='12a0bb104f29935f75be49e936d120e410a335ee032a3dd486e0f3cf112b6ba2',
        state_uuid=FRESH_UUID, tag='repeat-test')


class RuntimeFixesTests(unittest.TestCase):
    def parent(self, adapter, stack):
        held = stack.enter_context(adapter.Template(REPO / adapter.TEMPLATE, adapter.TEMPLATE_SHA))
        parent = adapter.load_template(held)
        adapter.adapt(parent, held)
        return parent, held

    def receipt(self):
        data = contents()
        producer = json.loads(data[L.EVIDENCE])
        policy = dict(producer['policy'], producer=producer,
            operator=json.loads(data['docs/evidence/operator-inputs-20261105.json']),
            metadata_helper=pin(data['build/rootfs-metadata.py']))
        return dict(schema=1, success=True, state='complete', hardware_acceptance=False,
            state_bytes=8 * 1024**3, state_uuid=FRESH_UUID, userspace_profile=ROOTS.PROFILE,
            metadata_profile=ROOTS.METADATA, host_inventory_unchanged=True, mapped_processes_final=[],
            phase_mounts_final=[], sources={'revision': 'a' * 40,
                'files': {name: record(raw) for name, raw in data.items()}}, policy=policy,
            runtime_fixes_controller=L.chain_binding(pin(data[L.TEMPLATE])))

    def test_complete_ast_changes_only_registered_constants_and_originals_unchanged(self):
        for adapter in (L, V):
            raw = (REPO / adapter.TEMPLATE).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), adapter.TEMPLATE_SHA)
            original = ast.parse(raw)
            expected = copy.deepcopy(original)
            changes = []
            for before, after in zip(ast.walk(original), ast.walk(expected)):
                if isinstance(before, ast.Constant) and type(before.value) is str and before.value in adapter.SUBSTITUTIONS:
                    after.value = adapter.SUBSTITUTIONS[before.value]
                    changes.append(before.value)
            self.assertCountEqual(changes, adapter.SUBSTITUTIONS)
            self.assertEqual(ast.dump(adapter.adapted_ast(raw), include_attributes=True),
                             ast.dump(expected, include_attributes=True))
            self.assertEqual(raw, (REPO / adapter.TEMPLATE).read_bytes())
            with self.assertRaisesRegex(ValueError, 'SHA256'):
                adapter.adapted_ast(raw + b'\n')
            # Even a separately authenticated future template cannot silently
            # change the anchor count; registration must be reviewed again.
            for mutation in (raw.replace(next(iter(adapter.SUBSTITUTIONS)).encode(), b'unknown-anchor'),
                             raw + ('\nextra = ' + repr(next(iter(adapter.SUBSTITUTIONS))) + '\n').encode()):
                with mock.patch.object(adapter, 'TEMPLATE_SHA', hashlib.sha256(mutation).hexdigest()):
                    with self.assertRaisesRegex(ValueError, 'anchors'):
                        adapter.adapted_ast(mutation)

    def test_registered_launch_contract_and_plan(self):
        import contextlib
        with contextlib.ExitStack() as stack:
            parent, held = self.parent(L, stack)
            data = contents()
            policy = parent.contract(ROOTS, REPO, data)
            manifest = {'revision': 'a' * 40, 'files': {name: record(raw) for name, raw in data.items()}}
            with mock.patch.object(Path, 'lstat', side_effect=AssertionError('input inspection')), \
                 mock.patch.object(Path, 'mkdir', side_effect=AssertionError('staging')):
                report = parent.plan(options(parent), manifest, policy)
            self.assertEqual(parent.__file__, str(REPO / L.SELF))
            self.assertEqual(parent.SELF, L.SELF)
            self.assertEqual(str(parent.PRODUCER_OUTPUT),
                '/var/lib/a733-clean-replay-20261106/host-roots-timesync-runtime-fixes-01/work/project/out/host-payload-timesync-runtime-fixes-01')
            self.assertEqual(report['runtime_fixes_controller'], L.chain_binding(held.pin))
            self.assertFalse(report['hardware_acceptance'])
            self.assertEqual(report['minimum_initial_available_memory_bytes'], 2 * 1024**3)
            self.assertEqual(report['runtime_minimum_free_bytes'], 3_000_000_000)
            self.assertEqual(report['runtime_minimum_available_memory_bytes'], 1_000_000_000)
            self.assertEqual(report['state_uuid'], FRESH_UUID)
            for field, value in [('roots_receipt', Path('/var/lib/a733-clean-replay-20261106/host-roots-timesync-01/receipt.json')),
                                 ('roots_receipt_sha256', 'a' * 64)]:
                opts = options(parent)
                setattr(opts, field, value)
                with self.assertRaises(ValueError):
                    parent.plan(opts, manifest, policy)
            for raw in (data['docs/evidence/production-host-roots-20261107.json'], data[L.EVIDENCE] + b'\n'):
                bad = dict(data, **{L.EVIDENCE: raw})
                with self.assertRaisesRegex(ValueError, 'evidence bytes'):
                    parent.contract(ROOTS, REPO, bad)
            bad = copy.deepcopy(manifest)
            bad['files'][L.EVIDENCE]['sha256'] = 'a' * 64
            with self.assertRaisesRegex(ValueError, 'evidence source closure'):
                parent.plan(options(parent), bad, policy)

    def test_both_uuid_guards_and_original_plans(self):
        import contextlib
        for adapter in (L, V):
            with contextlib.ExitStack() as stack:
                parent, _ = self.parent(adapter, stack)
                self.assertEqual(parent.state_uuid(FRESH_UUID), FRESH_UUID)
                self.assertIn('a55f9ea1-d255-4556-b578-9aa9e9d72726', adapter.FORBIDDEN_UUIDS)
                with self.assertRaises(ValueError):
                    parent.state_uuid('a55f9ea1-d255-4556-b578-9aa9e9d72726')
                for value in (*L.FORBIDDEN_UUIDS, *ROOTS.SLOT_UUIDS.values(), '0' * 32, FRESH_UUID.upper()):
                    with self.subTest(adapter=adapter.SELF, value=value), self.assertRaises(ValueError):
                        parent.state_uuid(value)
        original = load(L.TEMPLATE)
        old_producer = json.loads((REPO / original.PRODUCTION).read_bytes())
        old_policy = dict(old_producer['policy'], producer=old_producer)
        opts = options(original)
        opts.state_uuid = L.FORBIDDEN_UUIDS[1]
        opts.roots_receipt_sha256 = old_producer['producer_records']['receipt.json']['sha256']
        self.assertEqual(original.plan(opts, {}, old_policy)['state_uuid'], L.FORBIDDEN_UUIDS[1])
        self.assertEqual(load(V.TEMPLATE).state_uuid(L.FORBIDDEN_UUIDS[1]), L.FORBIDDEN_UUIDS[1])

    def test_reader_preserves_closed_policy_and_adds_exact_repeat_bindings(self):
        import contextlib
        with contextlib.ExitStack() as stack:
            parent, held = self.parent(V, stack)
            receipt = self.receipt()
            self.assertEqual(parent.__file__, str(REPO / V.SELF))
            self.assertEqual(parent.source_policy(receipt), receipt['policy'])
            mutations = []
            def mutation(path, value):
                bad = copy.deepcopy(receipt)
                target = bad
                for name in path[:-1]:
                    target = target[name]
                target[path[-1]] = value
                mutations.append(bad)
            mutation(['policy', 'producer'], json.loads((REPO / 'docs/evidence/production-host-roots-20261107.json').read_bytes()))
            mutation(['policy', 'producer', 'phase'], '/unexpected')
            mutation(['sources', 'files', L.EVIDENCE, 'sha256'], 'a' * 64)
            mutation(['sources', 'files', L.EVIDENCE, 'git_blob'], 'a' * 40)
            mutation(['sources', 'files', V.TEMPLATE, 'sha256'], 'a' * 64)
            mutation(['sources', 'files', L.TEMPLATE, 'sha256'], 'a' * 64)
            mutation(['runtime_fixes_controller', 'registered_parent'], 'unknown.json')
            mutation(['success'], False)
            mutation(['state_bytes'], 123)
            mutation(['phase_mounts_final'], ['/unexpected'])
            mutation(['policy', 'operator', 'management', 'dhcp'], False)
            mutation(['policy', 'clock', 'identity'], 0)
            for index, bad in enumerate(mutations):
                with self.subTest(index=index), self.assertRaises(ValueError):
                    parent.source_policy(bad)
            bad = copy.deepcopy(receipt)
            del bad['sources']['files'][L.EVIDENCE]
            with self.assertRaises(KeyError):
                parent.source_policy(bad)
            # Test publication interception without running the filesystem reader.
            reports = []
            # adapt() has already captured the real publish, so install a fresh
            # template with a fake sink before registering the hooks.
            parent = V.load_template(held)
            parent.R.publish = lambda path, report: reports.append(copy.deepcopy(report))
            V.adapt(parent, held)
            parent.R.publish(Path('/unused/receipt.json'), {'success': True, 'state': 'complete'})
            self.assertEqual(reports[-1]['runtime_fixes_controller'], V.chain_binding(held.pin))
            with mock.patch.object(held, 'unchanged', side_effect=ValueError('template replaced')):
                with self.assertRaisesRegex(ValueError, 'template replaced'):
                    parent.R.publish(Path('/unused/receipt.json'), {'success': True, 'state': 'complete'})
            self.assertFalse(reports[-1]['success'])
            self.assertEqual(reports[-1]['state'], 'failed')

    def test_launch_final_template_check_is_inside_original_execution_publication(self):
        import contextlib
        with contextlib.ExitStack() as stack:
            parent, held = self.parent(L, stack)
            root_parent = parent.module(str(REPO / parent.ROOTS), (REPO / parent.ROOTS).read_bytes())
            original_final = mock.Mock()
            # The wrapper calls the unchanged execute body. Intercept at the
            # original body boundary with a module fixture for an explicit order.
            original_parent = L.load_template(held)
            events = []
            fake_roots = types.SimpleNamespace(execute=lambda *args, **kw: kw['final_check']())
            original_parent.module = lambda *args: fake_roots
            L.adapt(original_parent, held)
            wrapped = original_parent.module(parent.ROOTS, b'fixture')
            with mock.patch.object(held, 'unchanged', side_effect=lambda: events.append('template')):
                wrapped.execute(final_check=lambda: events.append('original'))
            self.assertEqual(events, ['original', 'template'])
            with mock.patch.object(held, 'unchanged', side_effect=ValueError('changed')):
                with self.assertRaisesRegex(ValueError, 'changed'):
                    wrapped.execute(final_check=original_final)
            original_final.assert_called_once()
            self.assertTrue(callable(root_parent.execute))

    def test_template_refuses_unsafe_files_and_detects_replacement_or_mutation(self):
        for adapter in (L, V):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                raw = (REPO / adapter.TEMPLATE).read_bytes()
                path = root / 'template.py'
                path.write_bytes(raw)
                for kind in ('symlink', 'hardlink', 'oversize', 'fifo', 'parent-symlink'):
                    bad = root / kind
                    if kind == 'symlink': bad.symlink_to(path)
                    elif kind == 'hardlink': os.link(path, bad)
                    elif kind == 'oversize': bad.write_bytes(b'x' * (1024**2 + 1))
                    elif kind == 'fifo': os.mkfifo(bad)
                    else:
                        bad.symlink_to(root, target_is_directory=True)
                        bad = bad / path.name
                    with self.subTest(kind=kind), self.assertRaises((ValueError, OSError)):
                        with adapter.Template(bad, adapter.TEMPLATE_SHA): pass
                    if kind == 'hardlink': bad.unlink()
                # Identical-byte replacement still fails inode identity checks.
                for mutate in (lambda: path.write_bytes(raw + b'\n'),
                               lambda: ((root / 'replacement').write_bytes(raw), os.replace(root / 'replacement', path))):
                    path.write_bytes(raw)
                    held = adapter.Template(path, adapter.TEMPLATE_SHA).__enter__()
                    try:
                        mutate()
                        with self.assertRaises(ValueError): held.unchanged()
                    finally:
                        held.close()

    def test_registration_matches_committed_evidence_and_historical_adapters(self):
        raw = subprocess.check_output(['git', '-C', str(REPO), 'show', '3fcfd75:' + L.EVIDENCE])
        for adapter in (L, V):
            self.assertEqual(pin(raw), adapter.EVIDENCE_PIN)
            self.assertEqual(record(raw)['git_blob'], adapter.EVIDENCE_BLOB)
            self.assertEqual(hashlib.sha256(json.dumps(json.loads(raw), sort_keys=True,
                separators=(',', ':')).encode()).hexdigest(), adapter.CANONICAL_SHA)
        for name, sha in (
                ('build/launch-host-state-repeat.py', '5e2998921932607614e51b5f0177917afed24b7a821be5ebad44c4475eeedace'),
                ('build/verify-host-state-repeat.py', '3a31b48f463ce5fa0f8e0c343f6f939dbb3ead30a2870bbff1ac29ffbcbff95d')):
            self.assertEqual(hashlib.sha256((REPO / name).read_bytes()).hexdigest(), sha)

    def test_committed_parent_proof_schema_and_actual_reader_member_binding(self):
        import contextlib
        raw = subprocess.check_output(['git', '-C', str(REPO), 'show', '3fcfd75:' + L.EVIDENCE])
        producer = json.loads(raw)
        for adapter in (L, V):
            adapter.parent_proof_schema(producer)
        # Exercise the precise historical lookup against the real committed
        # policy, including its actual count and pins. Only transport of the
        # separately held proof JSON is replaced by a deterministic tree.
        with contextlib.ExitStack() as stack:
            parent, _ = self.parent(V, stack)
            expected = producer['expected_trees']['A']
            directory = dict(type='dir', mode=0o755, uid=0, gid=0, mtime_ns=0, xattrs={})
            marker = dict(type='file', mode=0o444, uid=0, gid=0, mtime_ns=0,
                          xattrs={}, data=pin(b'actual-shape marker fixture'))
            members = {name: dict(directory) for name in
                ('.', 'usr', 'usr/lib', 'usr/lib/orangepi-hypervisor')}
            members['usr/lib/orangepi-hypervisor/payload.json'] = marker
            for index in range(expected['members'] - len(members)):
                members['fixture-%05d' % index] = dict(marker)
            proof = dict(schema=1, slot='A', members=members)
            held = types.SimpleNamespace(pin=expected['expected'])
            selector = {'ROOT_MARKER_SHA256': marker['data']['sha256']}
            with mock.patch.object(parent.R, 'json_fd', return_value=proof):
                self.assertEqual(parent.parent_marker_binding(held, {'producer': producer}, selector), marker['data'])
                with self.assertRaisesRegex(ValueError, 'selected marker'):
                    parent.parent_marker_binding(held, {'producer': producer}, {'ROOT_MARKER_SHA256': '0' * 64})
                bad = copy.deepcopy(proof)
                bad['members'].pop('fixture-00000')
                with mock.patch.object(parent.R, 'json_fd', return_value=bad):
                    with self.assertRaisesRegex(ValueError, 'proof identity'):
                        parent.parent_marker_binding(held, {'producer': producer}, selector)

    def test_parent_proof_schema_rejects_flat_missing_and_corrupt_records(self):
        producer = json.loads(contents()[L.EVIDENCE])
        for adapter in (L, V):
            for slot in ('A', 'B'):
                mutations = []
                bad = copy.deepcopy(producer)
                bad['expected_trees'][slot] = bad['expected_trees'][slot]['expected']
                mutations.append(bad)
                for field in ('expected', 'stability', 'members', 'independent_image_readback'):
                    bad = copy.deepcopy(producer)
                    del bad['expected_trees'][slot][field]
                    mutations.append(bad)
                for field, value in (('expected', {'bytes': 1, 'sha256': '0' * 64}),
                                     ('stability', {'bytes': 57, 'sha256': 'f' * 64}),
                                     ('members', 1), ('members', True), ('members', '16368'),
                                     ('independent_image_readback', True),
                                     ('independent_image_readback', 0)):
                    bad = copy.deepcopy(producer)
                    bad['expected_trees'][slot][field] = value
                    mutations.append(bad)
                for field in ('expected', 'stability', 'namespace_members'):
                    bad = copy.deepcopy(producer)
                    del bad['readback']['slots'][slot][field]
                    mutations.append(bad)
                for index, bad in enumerate(mutations):
                    with self.subTest(adapter=adapter.SELF, slot=slot, index=index):
                        with self.assertRaisesRegex(ValueError, 'registered parent proof'):
                            adapter.parent_proof_schema(bad)

    def test_parent_proof_preflight_runs_after_authentication_in_both_hooks(self):
        import contextlib
        data = contents()
        with contextlib.ExitStack() as stack:
            launcher, _ = self.parent(L, stack)
            reader, _ = self.parent(V, stack)
            with mock.patch.object(L, 'parent_proof_schema', wraps=L.parent_proof_schema) as guard:
                launcher.contract(ROOTS, REPO, data)
                guard.assert_called_once_with(json.loads(data[L.EVIDENCE]))
                guard.reset_mock()
                with self.assertRaisesRegex(ValueError, 'evidence bytes'):
                    launcher.contract(ROOTS, REPO, dict(data, **{L.EVIDENCE: data[L.EVIDENCE] + b'\n'}))
                guard.assert_not_called()
            with mock.patch.object(V, 'parent_proof_schema', wraps=V.parent_proof_schema) as guard:
                receipt = self.receipt()
                reader.source_policy(receipt)
                guard.assert_called_once_with(receipt['policy']['producer'])
                guard.reset_mock()
                bad = copy.deepcopy(receipt)
                bad['policy']['producer']['expected_trees']['A'] = bad['policy']['producer']['expected_trees']['A']['expected']
                with self.assertRaisesRegex(ValueError, 'committed evidence'):
                    reader.source_policy(bad)
                guard.assert_not_called()
            # An authenticated but incompatible future registration must still
            # refuse the launcher contract, before any phase can be created.
            bad_producer = json.loads(data[L.EVIDENCE])
            bad_producer['expected_trees']['A'] = bad_producer['expected_trees']['A']['expected']
            raw = json.dumps(bad_producer, sort_keys=True, separators=(',', ':')).encode()
            with mock.patch.object(L, 'EVIDENCE_PIN', pin(raw)), \
                 mock.patch.object(L, 'CANONICAL_SHA', hashlib.sha256(raw).hexdigest()), \
                 mock.patch.object(Path, 'mkdir', side_effect=AssertionError('staging')):
                with self.assertRaisesRegex(ValueError, 'registered parent proof'):
                    launcher.contract(ROOTS, REPO, dict(data, **{L.EVIDENCE: raw}))

    def test_adapted_reader_keeps_full_semantic_and_regular_ext4_checks(self):
        # Reuse existing non-production fixtures against the adapted module.
        # These cover content, links, xattrs, UUID, writable journal policy,
        # selector proof, private metadata and failure publication.
        import contextlib
        fixtures = load('tests/test_verify_host_state.py')
        with contextlib.ExitStack() as stack:
            parent, _ = self.parent(V, stack)
            with mock.patch.object(fixtures, 'm', parent), mock.patch.object(fixtures, 'UUID', FRESH_UUID):
                for name in ('test_semantic_overlay_and_staging_binding',
                             'test_independent_parent_marker_rejects_agreed_wrong_selector',
                             'test_output_rejected_before_creation_and_failure_journal',
                             'test_actual_journal_writable_filesystem_full_readback'):
                    case = fixtures.StateVerifierTests(name)
                    result = unittest.TestResult()
                    case.run(result)
                    self.assertEqual(result.skipped, [], name)
                    self.assertTrue(result.wasSuccessful(), (name, result.errors, result.failures))

    def test_capacity_formula_preserved_without_staging(self):
        import contextlib
        with contextlib.ExitStack() as stack:
            parent, _ = self.parent(L, stack)
            data = contents()
            policy = parent.contract(ROOTS, REPO, data)
            manifest = {'files': {name: pin(raw) for name, raw in data.items()}}
            objects = 123456
            roots = types.SimpleNamespace(git=lambda *a: ('a blob %d\n' % objects).encode())
            candidate = types.SimpleNamespace(authenticated_file=mock.Mock())
            userspace = mock.MagicMock()
            archive = mock.MagicMock()
            archive.__enter__.return_value = [types.SimpleNamespace(size=440908294, isfile=lambda: True)]
            with mock.patch.object(parent.tarfile, 'open', return_value=archive):
                result = parent.budget(roots, candidate, userspace, policy, manifest, REPO)
            state_peak = 2 * 8589934592 + 440908294 + 138093095 + 1024**3
            capsule = 138093095 + 663 + 136
            git_bound = 3 * objects + sum(p['bytes'] + 4096 for p in manifest['files'].values()) + 64 * 1024**2
            self.assertEqual(result['required_free_bytes'], state_peak + capsule + git_bound)
            self.assertEqual(result['selector_peak_bytes'], 5 * 1024**3)
            self.assertEqual(candidate.authenticated_file.call_count, 2)

    def test_cli_help_preserves_options_and_requires_isolation(self):
        for adapter, option in ((L, '--roots-receipt'), (V, '--receipt-sha256')):
            result = subprocess.run([sys.executable, '-I', '-B', str(REPO / adapter.SELF), '--help'],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(option, result.stdout)
            self.assertIn('runtime-fixes', result.stdout)
            result = subprocess.run([sys.executable, '-B', str(REPO / adapter.SELF), '--help'],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('invoke Python with -I -B', result.stderr)


if __name__ == '__main__':
    unittest.main()
