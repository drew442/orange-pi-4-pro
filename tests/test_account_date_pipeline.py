"""New replay contract checks; no physical acceptance or full build claim."""
import copy
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
SPEC = importlib.util.spec_from_file_location('date_pipeline', REPO / 'build/launch-account-dated-userspace.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class PipelineTests(unittest.TestCase):
    def pipeline(self):
        return M.private_module(M.PIPELINE, (REPO / M.PIPELINE).read_bytes())

    def contents(self):
        p = M.configure_pipeline(self.pipeline())
        names = {p.SELF, p.SHARED, p.HOST_LOCK, p.PROFILE_LOCK}.union(*map(set, p.PHASE_FILES.values()))
        return {name: (REPO / name).read_bytes() for name in names}

    def test_historical_component_and_contract_pins(self):
        self.assertEqual(M.pin((REPO / M.PIPELINE).read_bytes()), M.PIPELINE_PIN)
        contract = M.validate_sources(self.contents())
        self.assertEqual(contract['profiles']['baseline']['members'], 15876)
        self.assertEqual(contract['profiles']['time']['members'], 15909)

    def test_export_closure_and_unchanged_other_phases(self):
        p = self.pipeline()
        original = copy.deepcopy(p.PHASE_FILES)
        p = M.configure_pipeline(p)
        self.assertEqual(p.SELF, M.SELF)
        for phase in ('foreign', 'configure'):
            self.assertEqual(p.PHASE_FILES[phase], original[phase])
        for name in (M.SELF, M.PIPELINE, M.ADAPTER, M.HELPER, M.CONTRACT, p.PROFILE_LOCK):
            self.assertIn(name, p.PHASE_FILES['export'])
        self.assertEqual(len(p.PHASE_FILES['export']), len(set(p.PHASE_FILES['export'])))
        with self.assertRaises(ValueError):
            M.configure_pipeline(p)

    def test_plan_authenticates_git_and_creates_no_phase(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            for name, data in self.contents().items():
                path = project / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            for args in (['init', '-q'], ['add', '.'], ['-c', 'user.name=Fixture',
                         '-c', 'user.email=f@example.invalid', 'commit', '-qm', 'Fixture']):
                subprocess.run(['git', '-C', str(project)] + args, check=True, capture_output=True)
            before = sorted(str(p.relative_to(project)) for p in project.rglob('*'))
            args = [sys.executable, '-I', '-B', str(project / M.SELF), '--project', str(project),
                    '--tag', 'date-fixture', '--plan']
            result = subprocess.run(args, capture_output=True, text=True, check=True)
            plan = json.loads(result.stdout)
            self.assertEqual(plan['account_date_replay']['profile'], 'baseline')
            self.assertEqual(plan['account_date_replay']['pipeline_component'], M.PIPELINE_PIN)
            self.assertEqual([p['name'] for p in plan['phases']], ['foreign', 'configure', 'export'])
            self.assertEqual(before, sorted(str(p.relative_to(project)) for p in project.rglob('*')))
            (project / M.HELPER).write_bytes(b'changed')
            rejected = subprocess.run(args, capture_output=True, text=True)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn('differ from committed blob', rejected.stderr)

    def test_wrong_pipeline_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / 'build').mkdir()
            marker = project / 'executed'
            (project / M.PIPELINE).write_text('open(' + repr(str(marker)) + ',"w").write("bad")')
            with self.assertRaisesRegex(ValueError, 'component pin'):
                M.authenticated_pipeline(project)
            self.assertFalse(marker.exists())

    def test_new_stage_delegates_foreign_and_configure(self):
        p = self.pipeline()
        original = mock.Mock(return_value=('old-slot', 'old-profile'))
        p.stage_inputs = original
        p = M.configure_pipeline(p)
        for name in ('foreign', 'configure'):
            self.assertEqual(p.stage_inputs('shared', 'phase', name, 'manifest', 'contents', 'index'),
                             ('old-slot', 'old-profile'))
            original.assert_called_with('shared', 'phase', name, 'manifest', 'contents', 'index')

    def test_export_stage_binds_new_sources_and_runner(self):
        p = M.configure_pipeline(self.pipeline())
        contents = self.contents()
        manifest = {'revision': 'a' * 40, 'files': {name: M.pin(data) for name, data in contents.items()}}
        snapshots = []
        def snapshot(path, selected):
            snapshots.append((path, selected))
        def immutable(path, data):
            path.write_bytes(data)
            path.chmod(0o444)
        shared = types.SimpleNamespace(UID_BASE=1048576,
                    read_json=lambda *a, **k: {'success': True},
                    project_snapshot=snapshot, atomic_json=lambda path, value: path.write_text(json.dumps(value)),
                    sha=lambda path: 'b' * 64)
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(M.os, 'chown') as chown, \
                mock.patch.object(p, 'immutable_file', side_effect=immutable):
            phase = Path(directory)
            slot, profile = p.stage_inputs(shared, phase, 'export', manifest, contents, b'index')
            self.assertIsNone(profile)
            self.assertEqual(slot, phase / 'export')
            selected = p.PHASE_FILES['export']
            self.assertEqual(snapshots, [(slot / 'inputs/project', {n: contents[n] for n in selected})])
            staged = json.loads((slot / 'inputs/manifest.json').read_text())
            self.assertEqual(staged['files'], {n: manifest['files'][n] for n in selected})
            runner = (slot / 'inputs/run.py').read_text()
            self.assertIn(M.ADAPTER, runner)
            self.assertIn('profile="baseline"', runner)
            self.assertIn('b' * 64, runner)
            compile(runner, '<staged runner>', 'exec')
            self.assertEqual((slot / 'inputs').stat().st_mode & 0o777, 0o555)
            chown.assert_called_once_with(slot / 'scratch', 1048576, 1048576)

    def test_outer_verifier_requires_pins_bom_and_transformation(self):
        p = self.pipeline()
        original = mock.Mock(return_value={'archive': 'already-checked'})
        p.verify_export = original
        p = M.configure_pipeline(p)
        contents = {M.ADAPTER: b'adapter', M.HELPER: b'helper', M.CONTRACT: b'contract'}
        expected = {'archive': {'a': 1}, 'raw_tar': {'r': 1}, 'members': 10,
                    'inventory': {'packages': 220}, 'bom': M.pin(b'bom')}
        receipt = dict(expected, account_date_replay={
            'schema': 1, 'profile': 'baseline', 'adapter': M.pin(b'adapter'),
            'helper': M.pin(b'helper'), 'contract': M.pin(b'contract'),
            'input_receipt_sha256': 'input-receipt', 'source_manifest': M.pin(b'manifest')})
        shared = types.SimpleNamespace(UID_BASE=1048576,
                    sha=lambda path: 'input-receipt', regular_bytes=lambda path, **k:
                    b'bom' if path.name == 'rootfs-bom.json' else
                    json.dumps(receipt).encode() if path.name == 'receipt.json' else b'manifest')
        with mock.patch.object(M, 'validate_sources', return_value={'profiles': {'baseline': expected}}):
            self.assertEqual(p.verify_export(shared, Path('/phase'), contents), {'archive': 'already-checked'})
            for key in ('archive', 'raw_tar', 'members', 'inventory', 'bom'):
                previous = receipt[key]
                receipt[key] = None
                with self.subTest(field=key), self.assertRaises(ValueError):
                    p.verify_export(shared, Path('/phase'), contents)
                receipt[key] = previous
            for key in list(receipt['account_date_replay']):
                previous = receipt['account_date_replay'][key]
                receipt['account_date_replay'][key] = None
                with self.subTest(provenance=key), self.assertRaises(ValueError):
                    p.verify_export(shared, Path('/phase'), contents)
                receipt['account_date_replay'][key] = previous
            reader = shared.regular_bytes
            shared.regular_bytes = lambda path, **kw: b'tampered' if path.name == 'rootfs-bom.json' else reader(path, **kw)
            with self.assertRaisesRegex(ValueError, 'BOM bytes'):
                p.verify_export(shared, Path('/phase'), contents)
        self.assertTrue(original.called)

    @unittest.skipUnless(os.geteuid() == 0, 'real mapped-root file ownership requires root')
    def test_outer_verifier_accepts_real_export_receipt_mode(self):
        p = self.pipeline()
        p.verify_export = mock.Mock(return_value={'checked': True})
        p = M.configure_pipeline(p)
        shared = p.shared_module({p.SHARED: (REPO / p.SHARED).read_bytes()})
        contents = {M.ADAPTER: b'adapter', M.HELPER: b'helper', M.CONTRACT: b'contract'}
        expected = {'archive': {'a': 1}, 'raw_tar': {'r': 1}, 'members': 10,
                    'inventory': {'packages': 220}, 'bom': M.pin(b'bom')}
        with tempfile.TemporaryDirectory() as directory:
            phase = Path(directory)
            output = phase / 'work/export-result'
            output.mkdir(parents=True)
            manifest = phase / 'export/inputs/manifest.json'
            manifest.parent.mkdir(parents=True)
            manifest.write_bytes(b'manifest')
            manifest.chmod(0o444)
            configured = phase / 'work/configure-receipt.json'
            configured.write_bytes(b'configuration')
            receipt = dict(expected, account_date_replay={
                'schema': 1, 'profile': 'baseline', 'adapter': M.pin(b'adapter'),
                'helper': M.pin(b'helper'), 'contract': M.pin(b'contract'),
                'source_manifest': M.pin(b'manifest'),
                'input_receipt_sha256': M.pin(b'configuration')['sha256']})
            (output / 'receipt.json').write_text(json.dumps(receipt))
            (output / 'rootfs-bom.json').write_bytes(b'bom')
            for path in output.iterdir():
                path.chmod(0o644)
                os.chown(path, shared.UID_BASE, shared.UID_BASE)
            with mock.patch.object(M, 'validate_sources', return_value={'profiles': {'baseline': expected}}):
                self.assertEqual(p.verify_export(shared, phase, contents), {'checked': True})
                (output / 'rootfs-bom.json').write_bytes(b'tampered')
                with self.assertRaisesRegex(ValueError, 'BOM bytes'):
                    p.verify_export(shared, phase, contents)


if __name__ == '__main__':
    unittest.main()
