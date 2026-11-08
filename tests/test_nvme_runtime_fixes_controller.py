"""Host-only registered runtime-fixes NVMe composition checks; no hardware acceptance."""
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


def load(path):
    spec = importlib.util.spec_from_file_location(path.replace('/', '_'), REPO / path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


L = load('build/launch-nvme-host-runtime-fixes.py')
B = load('build/launch-host-bootstrap-runtime-fixes.py')
S = load('build/launch-host-state-runtime-fixes.py')


def record(raw):
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
            'git_blob': hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()}


def contents():
    names = subprocess.check_output(['git', '-C', str(REPO), 'ls-files', '-z']).decode().split('\0')
    return {name: (REPO / name).read_bytes() for name in names + [L.SELF] if name}


class RuntimeFixesTests(unittest.TestCase):
    def setup_parent(self, stack, data=None, roots=None, project=REPO):
        data = contents() if data is None else data
        held = stack.enter_context(L.Template(REPO / L.TEMPLATE, L.TEMPLATE_SHA))
        parent = L.load_template(held)
        L.adapt(parent, held, stack)
        roots = load('build/launch-host-roots.py') if roots is None else roots
        policy = parent.contract(roots, project, data)
        return parent, roots, policy, data, held

    def manifest(self, data):
        return {'revision': 'a' * 40, 'files': {name: record(raw) for name, raw in data.items()}}

    def test_exact_counted_ast_amendments_only(self):
        raw = (REPO / L.TEMPLATE).read_bytes()
        expected = ast.parse(raw)
        counts = dict.fromkeys(L.SUBSTITUTIONS, 0)
        for node in ast.walk(expected):
            if isinstance(node, ast.Constant) and type(node.value) is str and node.value in L.SUBSTITUTIONS:
                old = node.value; counts[old] += 1; node.value = L.SUBSTITUTIONS[old]
        self.assertEqual(counts, L.COUNTS)
        self.assertEqual(ast.dump(expected, include_attributes=True),
                         ast.dump(L.adapted_ast(raw), include_attributes=True))
        with self.assertRaisesRegex(ValueError, 'SHA256'):
            L.adapted_ast(raw + b'\n')
        for changed in (raw.replace(b'host-roots-timesync-01', b'other'),
                        raw + b'\nextra="host-roots-timesync-01"\n'):
            with mock.patch.object(L, 'TEMPLATE_SHA', hashlib.sha256(changed).hexdigest()):
                with self.assertRaisesRegex(ValueError, 'anchors'):
                    L.adapted_ast(changed)

    def test_actual_registrations_contract_plan_and_old_defaults(self):
        with contextlib.ExitStack() as stack:
            parent, roots, policy, data, held = self.setup_parent(stack)
            manifest = self.manifest(data)
            for registration in L.REGISTRATIONS.values():
                row = record(data[registration['path']])
                self.assertEqual({k: row[k] for k in ('bytes', 'sha256')}, registration['pin'])
                self.assertEqual(row['git_blob'], registration['git_blob'])
                self.assertEqual(hashlib.sha256(json.dumps(json.loads(data[registration['path']]),
                    sort_keys=True, separators=(',', ':')).encode()).hexdigest(), registration['canonical_sha256'])
            report = parent.plan('repeat-fixture', manifest, policy)
            self.assertEqual(parent.__file__, str(REPO / L.SELF))
            self.assertEqual(parent.SELF, L.SELF)
            self.assertEqual(policy['state_uuid'], '360dfb39-f49f-487e-b307-0bb887c854c5')
            self.assertEqual(report['runtime_fixes_controller']['historical_template'], held.pin)
            self.assertEqual(report['runtime_fixes_controller']['bootstrap_family']['registered_runtime_fixes_controller'],
                json.loads(data[parent.EVIDENCE['bootstrap']])['runtime_fixes_controller'])
            self.assertEqual(report['minimum_process_open_file_soft_limit'], 4096)
            self.assertEqual(parent.GUARD, 22 * 1024**3)
            self.assertFalse(report['hardware_acceptance'])
            self.assertFalse(report['physical_write_authorization'])
            self.assertEqual(len(parent.records(policy)), 9)
            old = load(L.TEMPLATE)
            original_policy = old.contract(load(old.ROOTS), REPO, data)
            original_report = old.plan('original', manifest, original_policy)
            self.assertEqual(original_policy['state_uuid'], 'fc8bbef6-9385-4ddb-90ba-e41eaccc035b')
            self.assertNotIn('runtime_fixes_controller', original_report)
            for tag in ('', '../bad', '/bad', 'bad tag', 'a' * 65):
                with self.assertRaises(ValueError): parent.plan(tag, manifest, policy)
            bad = copy.deepcopy(manifest)
            bad['files'][L.REGISTRATIONS['bootstrap']['path']]['git_blob'] = 'a' * 40
            with self.assertRaisesRegex(ValueError, 'source closure'):
                parent.plan('fixture', bad, policy)
            with self.assertRaisesRegex(ValueError, 'root module changed'):
                parent.contract(load(parent.ROOTS), REPO, data)

    def test_all_registered_closure_drift_rejected(self):
        names = [L.TEMPLATE, L.BOOTSTRAP, B.TEMPLATE, B.STATE, S.TEMPLATE,
                 S.EVIDENCE, L.READBACK_REGISTRATION['path'], *(r['path'] for r in L.REGISTRATIONS.values())]
        for name in names:
            data = contents(); data[name] += b'\n'
            with self.subTest(name=name), contextlib.ExitStack() as stack:
                with self.assertRaises(ValueError): self.setup_parent(stack, data)
        for key, value in [('state_uuid', 'fc8bbef6-9385-4ddb-90ba-e41eaccc035b'),
                           ('phase', '/wrong'), ('construction_success', False)]:
            data = contents(); path = L.REGISTRATIONS['roots']['path']; doc = json.loads(data[path])
            doc[key] = value; data[path] = json.dumps(doc).encode()
            with self.subTest(key=key), contextlib.ExitStack() as stack:
                with self.assertRaisesRegex(ValueError, 'evidence bytes'): self.setup_parent(stack, data)
        for field, value in [('bytes', 250000000000), ('serial', 'other'), ('logical_sector_bytes', 4096)]:
            data = contents(); old = load(L.TEMPLATE); doc = json.loads(data[old.TARGET])
            doc['installation']['target'][field] = value; data[old.TARGET] = json.dumps(doc).encode()
            with self.subTest(field=field), contextlib.ExitStack() as stack:
                with self.assertRaisesRegex(ValueError, 'target geometry'): self.setup_parent(stack, data)

    def test_source_pins_and_canonical_forms_are_independent(self):
        for name, registration in L.REGISTRATIONS.items():
            with contextlib.ExitStack() as stack:
                parent, _, policy, data, _ = self.setup_parent(stack)
                manifest = self.manifest(data)
                manifest['files'][registration['path']]['git_blob'] = 'a' * 40
                with self.assertRaisesRegex(ValueError, 'source closure'):
                    parent.plan('fixture', manifest, policy)
            data = contents()
            value = json.loads(data[registration['path']]); value['unexpected'] = True
            data[registration['path']] = json.dumps(value).encode()
            registrations = copy.deepcopy(L.REGISTRATIONS)
            registrations[name]['pin'] = {k: record(data[registration['path']])[k]
                                         for k in ('bytes', 'sha256')}
            with mock.patch.object(L, 'REGISTRATIONS', registrations), contextlib.ExitStack() as stack:
                with self.assertRaisesRegex(ValueError, 'parsed component evidence'):
                    self.setup_parent(stack, data)

    def test_nested_module_factory_exact_path_bytes_and_cache(self):
        with contextlib.ExitStack() as stack:
            parent, roots, _, data, _ = self.setup_parent(stack)
            for name in (parent.BOOTSTRAP, parent.STATE):
                family = roots.module(name, data[name])
                self.assertIs(family, roots.module(str(REPO / name), data[name]))
                self.assertEqual(family.__file__, str(REPO / name))
                self.assertTrue(callable(family.contract))
                with self.assertRaisesRegex(ValueError, 'module data'):
                    roots.module(name, data[name] + b'\n')
                for path in ('/different/' + Path(name).name, 'build/../build/' + Path(name).name):
                    with self.assertRaisesRegex(ValueError, 'module path'): roots.module(path, data[name])
            self.assertEqual(roots.module('unrelated.py', b'answer=42').answer, 42)

    def test_all_held_nested_guards_before_publication(self):
        targets = (L.BOOTSTRAP, B.TEMPLATE, B.STATE, S.TEMPLATE)
        for target in targets:
            with self.subTest(target=target), tempfile.TemporaryDirectory() as temporary:
                project = Path(temporary); data = contents()
                for name, raw in data.items():
                    path = project / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
                events = []; roots = load('build/launch-host-roots.py')
                roots.execute = lambda *a, **kw: (kw['final_check'](), events.append('published'))
                stack = contextlib.ExitStack()
                try:
                    self.setup_parent(stack, data, roots, project)
                    roots.execute(final_check=lambda: events.append('original'))
                    self.assertEqual(events, ['original', 'published'])
                    events.clear()
                    path = project / target; replacement = project / 'replacement'
                    replacement.write_bytes(path.read_bytes()); replacement.replace(path)
                    with self.assertRaisesRegex(ValueError, 'template changed'):
                        roots.execute(final_check=lambda: events.append('original'))
                    self.assertEqual(events, ['original'])
                finally:
                    with self.assertRaisesRegex(ValueError, 'template changed'): stack.close()
        with contextlib.ExitStack() as stack:
            roots = load('build/launch-host-roots.py'); events = []
            roots.execute = lambda *a, **kw: (kw['final_check'](), events.append('published'))
            _, _, _, _, held = self.setup_parent(stack, roots=roots)
            with mock.patch.object(held, 'unchanged', side_effect=ValueError('NVMe held changed')):
                with self.assertRaisesRegex(ValueError, 'NVMe held changed'):
                    roots.execute(final_check=lambda: events.append('original'))
            self.assertEqual(events, ['original'])

    def test_readback_binding_addresses_only_private_copy_of_policy(self):
        with contextlib.ExitStack() as stack:
            parent, _, policy, data, _ = self.setup_parent(stack)
            before = copy.deepcopy(policy)
            registration = L.READBACK_REGISTRATION
            raw = data[registration['path']]
            self.assertEqual(raw, subprocess.check_output(
                ['git', '-C', str(REPO), 'show', 'HEAD:' + registration['path']]))
            self.assertEqual({k: record(raw)[k] for k in ('bytes', 'sha256')}, registration['pin'])
            self.assertEqual(record(raw)['git_blob'], registration['git_blob'])
            binding = json.loads(raw)
            root = policy['producers']['roots']
            self.assertEqual(binding['receipt_contents'], root['readback'])
            self.assertEqual(binding['receipt_contents']['producer_receipt'], root['producer_records']['receipt.json'])
            self.assertEqual(binding['receipt_contents']['root_manifest'], root['verification']['manifest'])
            self.assertEqual(binding['receipt_contents']['slots'], root['readback']['slots'])
            self.assertNotIn('builder_output', root['readback'])
            self.assertNotIn('receipt', root['readback'])
            old = load(L.TEMPLATE)
            with self.assertRaises(KeyError): old.records(policy)
            addressed = copy.deepcopy(policy)
            addressed['producers']['roots']['readback'].update(
                builder_output=binding['builder_output'], receipt=binding['receipt'])
            with L.Template(REPO / L.TEMPLATE, L.TEMPLATE_SHA) as template:
                original = L.load_template(template)
                self.assertEqual(parent.records(policy), original.records(addressed))
            self.assertEqual(parent.records(policy)['roots-readback'],
                             (Path(binding['builder_output']) / 'receipt.json', binding['receipt']))
            self.assertEqual(policy, before)
            manifest = self.manifest(data)
            manifest['files'][registration['path']]['git_blob'] = 'a' * 40
            with self.assertRaisesRegex(ValueError, 'source closure'):
                parent.plan('fixture', manifest, policy)
            changed = copy.deepcopy(policy)
            changed['producers']['roots']['readback']['slots'] = {}
            with self.assertRaisesRegex(ValueError, 'readback policy changed'):
                parent.records(changed)
        for field, value in [('root_phase', '/stale'), ('root_receipt', {}),
                             ('root_manifest', {}), ('receipt_contents', {})]:
            data = contents(); registration = copy.deepcopy(L.READBACK_REGISTRATION)
            binding = json.loads(data[registration['path']]); binding[field] = value
            raw = json.dumps(binding).encode(); data[registration['path']] = raw
            registration['pin'] = {k: record(raw)[k] for k in ('bytes', 'sha256')}
            registration['canonical_sha256'] = hashlib.sha256(json.dumps(
                binding, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            with self.subTest(field=field), mock.patch.object(L, 'READBACK_REGISTRATION', registration), contextlib.ExitStack() as stack:
                with self.assertRaisesRegex(ValueError, 'root readback binding differs'):
                    self.setup_parent(stack, data)
        data = contents(); del data[L.READBACK_REGISTRATION['path']]
        with contextlib.ExitStack() as stack, self.assertRaises(KeyError):
            self.setup_parent(stack, data)

    def test_nested_full_source_policy_and_tree_binding(self):
        with contextlib.ExitStack() as stack:
            parent, _, policy, data, _ = self.setup_parent(stack)
            producer = policy['parent']['state_policy']['producer']
            root = policy['producers']['roots']
            self.assertEqual(producer, json.loads(data[S.EVIDENCE]))
            self.assertEqual(root, json.loads(data[L.REGISTRATIONS['roots']['path']]))
            for slot in ('A', 'B'):
                self.assertEqual(set(root['expected_trees'][slot]), {'bytes', 'sha256'})
                tree = producer['expected_trees'][slot]
                self.assertEqual(set(tree), {'expected', 'stability', 'members', 'independent_image_readback'})
                self.assertEqual(tree['expected'], root['expected_trees'][slot])
                self.assertEqual(tree['expected'], producer['readback']['slots'][slot]['expected'])
                self.assertEqual(tree['stability'], producer['readback']['slots'][slot]['stability'])
                self.assertEqual(tree['members'], producer['readback']['slots'][slot]['namespace_members'])
            manifest = self.manifest(data)
            manifest['files'][S.EVIDENCE]['git_blob'] = 'a' * 40
            with self.assertRaisesRegex(ValueError, 'source closure'):
                parent.plan('fixture', manifest, policy)

    def check_actual_retained_native_metadata_and_reject_drift(self):
        # Explicit --native integration check, outside the source-only suite.
        # Use accepted producer files without fabricated receipt field shapes.
        paths = {
            'roots-receipt': 'out/host-runtime-fixes-20261108/producer/receipt.json',
            'roots-manifest': 'out/host-runtime-fixes-20261108/payload-manifest.json',
            'roots-readback': 'out/host-runtime-fixes-20261108/readback/receipt.json',
            'state-receipt': 'out/state-runtime-fixes-20261108/producer-02/receipt.json',
            'state-manifest': 'out/state-runtime-fixes-20261108/payload-02/manifest.json',
            'state-readback.json': 'out/state-runtime-fixes-20261108/readback-02/receipt.json',
            'sd-receipt': 'out/bootstrap-runtime-fixes-20261108/generated/receipt.json',
            'sd-manifest': 'out/bootstrap-runtime-fixes-20261108/generated/work/project/out/host-sd-timesync-runtime-fixes-01/manifest.json',
            'sd-readback': 'out/bootstrap-runtime-fixes-20261108/generated/work/readback/bootstrap.json',
        }
        missing = [name for name in paths.values() if not (REPO / name).is_file()]
        self.assertFalse(missing, 'actual retained native metadata unavailable: ' + ', '.join(missing))
        with contextlib.ExitStack() as stack:
            parent, roots, policy, data, _ = self.setup_parent(stack)
            state = roots.module(parent.STATE, data[parent.STATE])
            bootstrap = roots.module(parent.BOOTSTRAP, data[parent.BOOTSTRAP])
            verifier = roots.module(parent.VERIFIER, data[parent.VERIFIER])
            Held = state.held_class(verifier)
            held = {}
            values = {}
            for name, (_, expected) in parent.records(policy).items():
                raw = (REPO / paths[name]).read_bytes()
                self.assertEqual({k: record(raw)[k] for k in ('bytes', 'sha256')}, expected, name)
                item = stack.enter_context(Held(REPO / paths[name], expected))
                held[name] = item
                values[id(item)] = verifier.json_fd(item)
            for slot in ('A', 'B'):
                held['roots/root-' + slot + '.ext4'] = types.SimpleNamespace(
                    pin=policy['producers']['roots']['images']['root-' + slot + '.ext4'])
            def authenticate(rows):
                verifier = types.SimpleNamespace(json_fd=lambda item: rows[id(item)])
                parent.authenticate(verifier, state, bootstrap, held, policy)
            parent.authenticate(verifier, state, bootstrap, held, policy)
            self.assertEqual(values[id(held['state-receipt'])]['policy'], policy['parent']['state_policy'])
            self.assertEqual(values[id(held['sd-receipt'])]['policy'], policy['parent'])
            cases = [
                ('roots-receipt', 'policy', {}), ('roots-manifest', 'sources', {}),
                ('roots-readback', 'slots', {}), ('state-receipt', 'policy', {}),
                ('state-manifest', 'state_uuid', 'old'), ('state-readback.json', 'source_policy', {}),
                ('state-readback.json', 'filesystem', {}), ('sd-receipt', 'policy', {}),
                ('sd-receipt', 'sources', {'revision': 'a' * 40}), ('sd-manifest', 'state_uuid', 'old'),
                ('sd-readback', 'unexpected', True)]
            for name, key, value in cases:
                changed = copy.deepcopy(values)
                changed[id(held[name])][key] = value
                with self.subTest(name=name, key=key), self.assertRaises(ValueError):
                    authenticate(changed)

    def test_template_unsafe_input_and_metadata_checks(self):
        raw = (REPO / L.TEMPLATE).read_bytes()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); path = root / 'template'; path.write_bytes(raw)
            for kind in ('symlink', 'hardlink', 'oversize', 'fifo', 'parent-symlink'):
                bad = root / kind
                if kind == 'symlink': bad.symlink_to(path)
                elif kind == 'hardlink': os.link(path, bad)
                elif kind == 'oversize': bad.write_bytes(b'x' * (1024**2 + 1))
                elif kind == 'fifo': os.mkfifo(bad)
                else: bad.symlink_to(root, target_is_directory=True); bad = bad / path.name
                with self.subTest(kind=kind), self.assertRaises((OSError, ValueError)):
                    with L.Template(bad, L.TEMPLATE_SHA): pass
                if kind == 'hardlink': bad.unlink()
            held = L.Template(path, L.TEMPLATE_SHA).__enter__()
            try:
                path.chmod(0o600)
                with self.assertRaisesRegex(ValueError, 'template changed'): held.unchanged()
            finally: held.close()

    def test_resource_guards_and_target_geometry_unchanged(self):
        with contextlib.ExitStack() as stack:
            parent, _, policy, data, _ = self.setup_parent(stack)
            self.assertEqual(policy['target_geometry'], {
                'model': 'SPCC M.2 PCIe SSD', 'serial': '134807190FC700149277',
                'bytes': 256060514304, 'logical_sector_bytes': 512, 'physical_sector_bytes': 512})
            parent.check_resources({'free_bytes': 3_000_000_000, 'available_memory_bytes': 1_000_000_000}, 10800)
            for sample, elapsed in [
                ({'free_bytes': 2_999_999_999, 'available_memory_bytes': 1_000_000_000}, 0),
                ({'free_bytes': 3_000_000_000, 'available_memory_bytes': 999_999_999}, 0),
                ({'free_bytes': 3_000_000_000, 'available_memory_bytes': 1_000_000_000}, 10801)]:
                with self.assertRaisesRegex(ValueError, 'resource floor or timeout'):
                    parent.check_resources(sample, elapsed)
            roots = types.SimpleNamespace(git=lambda *a: b'a blob 100')
            budget = parent.budget(roots, REPO, self.manifest(data), b'payload', b'run')
            self.assertEqual(budget['recipe_bytes'], 22 * 1024**3)
            self.assertGreater(budget['required_free_bytes'], budget['recipe_bytes'])

    def test_cli_options_and_isolation(self):
        result = subprocess.run([sys.executable, '-I', '-B', str(REPO / L.SELF), '--help'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        for flag in ('--project', '--tag', '--plan'): self.assertIn(flag, result.stdout)
        result = subprocess.run([sys.executable, '-B', str(REPO / L.SELF), '--help'], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('invoke Python with -I -B', result.stderr)


if __name__ == '__main__':
    if '--native' in sys.argv:
        sys.argv.remove('--native')
        unittest.main(defaultTest='RuntimeFixesTests.check_actual_retained_native_metadata_and_reject_drift')
    else:
        unittest.main()
