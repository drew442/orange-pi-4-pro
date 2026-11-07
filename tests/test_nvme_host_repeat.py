"""Host-only registered NVMe composition fixtures; no hardware acceptance."""
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


L = load('build/launch-nvme-host-repeat.py')
B = load('build/launch-host-bootstrap-repeat.py')
S = load('build/launch-host-state-repeat.py')


def record(raw):
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
            'git_blob': hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()}


def contents():
    names = subprocess.check_output(['git', '-C', str(REPO), 'ls-files', '-z']).decode().split('\0')
    return {name: (REPO / name).read_bytes() for name in names + [L.SELF] if name}


class RepeatTests(unittest.TestCase):
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
            self.assertEqual(policy['state_uuid'], 'feaaee34-d782-4c18-bd04-1029540ab26a')
            self.assertEqual(report['repeat_controller']['historical_template'], held.pin)
            self.assertEqual(report['repeat_controller']['bootstrap_family']['registered_repeat_controller'],
                json.loads(data[parent.EVIDENCE['bootstrap']])['repeat_controller'])
            self.assertEqual(report['minimum_process_open_file_soft_limit'], 4096)
            self.assertEqual(parent.GUARD, 22 * 1024**3)
            self.assertFalse(report['hardware_acceptance'])
            self.assertFalse(report['physical_write_authorization'])
            self.assertEqual(len(parent.records(policy)), 9)
            old = load(L.TEMPLATE)
            original_policy = old.contract(load(old.ROOTS), REPO, data)
            original_report = old.plan('original', manifest, original_policy)
            self.assertEqual(original_policy['state_uuid'], 'fc8bbef6-9385-4ddb-90ba-e41eaccc035b')
            self.assertNotIn('repeat_controller', original_report)
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
                 *(r['path'] for r in L.REGISTRATIONS.values())]
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

    def test_full_original_authenticate_with_actual_registration_schema(self):
        with contextlib.ExitStack() as stack:
            parent, roots, policy, data, _ = self.setup_parent(stack)
            state = roots.module(parent.STATE, data[parent.STATE])
            bootstrap = roots.module(parent.BOOTSTRAP, data[parent.BOOTSTRAP])
            p = policy['producers']; bp = policy['parent']; sp = bp['state_policy']
            held = {name: types.SimpleNamespace(pin=pin) for name, (_, pin) in parent.records(policy).items()}
            for slot in ('A', 'B'):
                held['roots/root-' + slot + '.ext4'] = types.SimpleNamespace(pin=p['roots']['readback']['slots'][slot]['image'])
            root_receipt = dict(success=True, state='complete', sources={'revision': sp['producer']['producer_revision']},
                userspace_profile='timesync-20261106', metadata_profile='fakeroot-raw-xattrs-v1',
                slot_bytes=4 * 1024**3, policy={k: sp[k] for k in ('archive', 'binding', 'clock', 'diagnostic')},
                verification={'manifest': held['roots-manifest'].pin})
            root_manifest = dict(slot_bytes=4 * 1024**3, slot_uuids=sp['producer']['slot_uuids'],
                files={f'root-{slot}.ext4': held[f'roots/root-{slot}.ext4'].pin for slot in ('A', 'B')},
                sources={'rootfs.tar.gz': sp['archive'], 'userspace_profile': sp['binding'],
                    'timesync_state': sp['clock'], 'rootfs_metadata': {'profile': 'fakeroot-raw-xattrs-v1',
                    'helper': sp['metadata_helper']}, **sp['diagnostic']})
            producer = p['state']
            state_receipt = dict(success=True, state='complete', phase=producer['phase'], output=producer['output'],
                state_uuid=parent.STATE_UUID, state_bytes=8 * 1024**3, sources={'revision': producer['source_revision']},
                userspace_profile=producer['profile'], metadata_profile=producer['metadata_profile'],
                policy=sp, verification=producer['verification'])
            state_manifest = dict(schema=1, state_uuid=parent.STATE_UUID, state_bytes=8 * 1024**3,
                boot_selection=producer['boot_selection'], files={'state.ext4': producer['state_image']},
                sources={'rootfs.tar.gz': sp['archive'], 'userspace_profile': sp['binding'], 'timesync_state': sp['clock']})
            state_readback = dict(schema=1, success=True, state='complete',
                full_namespace_metadata_content_readback=True, input_identity_unchanged=True,
                producer_receipt=held['state-receipt'].pin, state_manifest=held['state-manifest'].pin,
                source_policy=sp, parent_root_marker=producer['parent_roots']['readback_expected'],
                filesystem=producer['readback']['filesystem'], image=producer['state_image'])
            root_readback = dict(success=True, state='complete', full_namespace_metadata_content_readback=True,
                producer_receipt=held['roots-receipt'].pin, root_manifest=held['roots-manifest'].pin,
                slots=p['roots']['readback']['slots'])
            producer = p['bootstrap']
            sd_receipt = dict(success=True, state='complete', phase=producer['phase'], output=producer['output'],
                sources={'revision': producer['source_revision']}, policy=bp,
                verification=producer['verification'], readback_expectations=producer['readback_expectations'])
            sd_manifest = dict(state_uuid=parent.STATE_UUID, boot_profile=bp['boot_profile'], boot_inputs=producer['boot_inputs'])
            rows = [root_receipt, root_manifest, state_receipt, state_manifest, state_readback,
                    root_readback, sd_receipt, sd_manifest, producer['readback_expectations']['checks']]
            def authenticate(values):
                verifier = types.SimpleNamespace(json_fd=mock.Mock(side_effect=values))
                parent.authenticate(verifier, state, bootstrap, held, policy)
            authenticate(rows)
            for index, key, value in [(0, 'policy', {}), (1, 'slot_uuids', {}),
                    (2, 'policy', {}), (3, 'state_uuid', 'old'), (4, 'input_identity_unchanged', False),
                    (4, 'filesystem', {}), (5, 'slots', {}), (5, 'full_namespace_metadata_content_readback', False),
                    (6, 'policy', {}), (6, 'sources', {'revision': 'a' * 40}),
                    (7, 'state_uuid', 'old'), (8, 'unexpected', True)]:
                bad = copy.deepcopy(rows); bad[index][key] = value
                with self.subTest(index=index, key=key), self.assertRaises(ValueError): authenticate(bad)

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

    def test_cli_options_and_isolation(self):
        result = subprocess.run([sys.executable, '-I', '-B', str(REPO / L.SELF), '--help'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        for flag in ('--project', '--tag', '--plan'): self.assertIn(flag, result.stdout)
        result = subprocess.run([sys.executable, '-B', str(REPO / L.SELF), '--help'], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('invoke Python with -I -B', result.stderr)


if __name__ == '__main__':
    unittest.main()
