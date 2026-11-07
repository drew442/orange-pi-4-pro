"""Dated time launcher fixtures; no full build or physical acceptance claim."""
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
SPEC = importlib.util.spec_from_file_location('dated_time', REPO / 'build/launch-account-dated-timesync.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class DatedTimeTests(unittest.TestCase):
    def core(self):
        return M.module(M.CORE, (REPO / M.CORE).read_bytes())

    def contents(self):
        core = M.configure_timesync(self.core())
        return {name: (REPO / name).read_bytes() for name in core.SOURCE_FILES}

    def manifest(self, contents):
        return {'revision': 'a' * 40, 'files': {name: dict(M.pin(data), git_blob=hashlib.sha1(
            b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()) for name, data in contents.items()}}

    def test_component_pins_and_source_closure(self):
        core = self.core()
        before = {name: getattr(core, name) for name in (
            'base_inputs', 'preflight', 'execute', 'propagation', 'run_phase', 'tree_budget', 'committed_sources')}
        old_sources, old_adapter = core.SOURCE_FILES, core.ADAPTER
        core = M.configure_timesync(core)
        contents = self.contents()
        self.assertEqual(M.pin(contents[M.CORE]), M.CORE_PIN)
        self.assertEqual(M.pin(contents[M.BASE_DRIVER]), M.BASE_DRIVER_PIN)
        self.assertEqual(M.validate_sources(core, contents)['profiles']['time']['members'], 15909)
        self.assertEqual(set(core.SOURCE_FILES), set(old_sources) | {
            M.SELF, M.CORE, M.BASE_DRIVER, M.ADAPTER, M.HELPER, M.CONTRACT})
        self.assertEqual(core.ADAPTER, old_adapter)
        for name, function in before.items():
            self.assertIs(getattr(core, name), function)
        with self.assertRaises(ValueError):
            M.configure_timesync(core)
        for name in (M.CORE, M.BASE_DRIVER, M.HELPER, M.CONTRACT, core.PIPELINE):
            changed = dict(contents, **{name: contents[name] + b'\n'})
            with self.subTest(source=name), self.assertRaises((ValueError, SyntaxError)):
                M.validate_sources(core, changed)

    def checkout(self, project):
        for name, data in self.contents().items():
            path = project / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        for args in (['init', '-q'], ['add', '.'], ['-c', 'user.name=Fixture',
                     '-c', 'user.email=f@example.invalid', 'commit', '-qm', 'Fixture']):
            subprocess.run(['/usr/bin/git', '-C', str(project)] + args, check=True, capture_output=True)

    def test_git_plan_has_new_attribution_and_no_staging(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            self.checkout(project)
            before = sorted(str(p.relative_to(project)) for p in project.rglob('*'))
            args = [sys.executable, '-I', '-B', str(project / M.SELF), '--project', str(project),
                    '--tag', 'dated-time-fixture', '--base-tag', 'dated-base-fixture',
                    '--extra-deb', '/missing/not-read.deb', '--plan']
            result = subprocess.run(args, capture_output=True, text=True, check=True)
            plan = json.loads(result.stdout)
            self.assertEqual(plan['account_date_replay']['driver'], M.pin((project / M.SELF).read_bytes()))
            self.assertEqual(plan['account_date_replay']['time_pipeline_component'], M.CORE_PIN)
            self.assertEqual(plan['account_date_replay']['baseline_driver'], M.BASE_DRIVER_PIN)
            self.assertEqual([p['name'] for p in plan['phases']], ['configure', 'export'])
            self.assertEqual(before, sorted(str(p.relative_to(project)) for p in project.rglob('*')))
            (project / M.HELPER).write_bytes(b'changed')
            failed = subprocess.run(args, capture_output=True, text=True)
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn('committed blob', failed.stderr)

    def test_bootstrap_rejects_modified_or_unpinned_core_before_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            self.checkout(project)
            marker = project / 'executed'
            (project / M.CORE).write_text('open(' + repr(str(marker)) + ',"w").write("bad")')
            with self.assertRaisesRegex(ValueError, 'Git blob'):
                M.authenticated_core(project)
            subprocess.run(['git', '-C', str(project), 'add', M.CORE], check=True)
            subprocess.run(['git', '-C', str(project), '-c', 'user.name=Fixture', '-c',
                            'user.email=f@example.invalid', 'commit', '-qm', 'Changed core'], check=True)
            with self.assertRaisesRegex(ValueError, 'launcher pin'):
                M.authenticated_core(project)
            self.assertFalse(marker.exists())

    def test_configure_delegation_and_export_handshake_staging(self):
        core = self.core()
        original = mock.Mock(return_value=('old-slot', 'old-profile'))
        core.stage_inputs = original
        core = M.configure_timesync(core)
        args = ('shared', 'pipeline', 'phase', 'configure', 'manifest', 'contents', 'bom', 'deb', 'repo')
        self.assertEqual(core.stage_inputs(*args), ('old-slot', 'old-profile'))
        original.assert_called_once_with(*args)
        shared = core.module(core.SHARED, (REPO / core.SHARED).read_bytes())
        pipeline = core.module(core.PIPELINE, (REPO / core.PIPELINE).read_bytes())
        contents = self.contents()
        manifest = self.manifest(contents)
        with tempfile.TemporaryDirectory() as directory:
            phase = Path(directory)
            (phase / 'work').mkdir()
            receipt = phase / 'work/timesync-receipt.json'
            receipt.write_bytes(b'{"success": true}')
            with mock.patch.object(M.os, 'chown'), mock.patch.object(M.os, 'fchown'), \
                    mock.patch.object(shared, 'UID_BASE', os.getuid()):
                slot, profile = core.stage_inputs(shared, pipeline, phase, 'export', manifest, contents,
                                                  b'bom', b'deb', 'c' * 64)
            runner = (slot / 'inputs/run.py').read_text()
            self.assertTrue(runner.startswith("STAGE='export'\n" + core.PROP_RUNNER))
            self.assertIn(M.ADAPTER, runner)
            self.assertIn('profile="time"', runner)
            self.assertIn('export-timesync-result', runner)
            self.assertIn(M.pin(receipt.read_bytes())['sha256'], runner)
            compile(runner, '<time runner>', 'exec')
            self.assertEqual(profile['repo_manifest_sha256'], 'c' * 64)
            self.assertEqual(profile['host_userns_inode'], shared.namespaces()['user'])
            self.assertEqual(json.loads((slot / 'inputs/api-profile.json').read_bytes()), profile)
            self.assertEqual(json.loads((slot / 'inputs/manifest.json').read_bytes()), manifest)
            self.assertEqual((slot / 'inputs/rootfs-bom.json').read_bytes(), b'bom')
            self.assertEqual((slot / 'inputs').stat().st_mode & 0o777, 0o555)
            for name, data in contents.items():
                self.assertEqual((slot / 'inputs/project' / name).read_bytes(), data)

    def base_fixture(self):
        core = M.configure_timesync(self.core())
        contents = self.contents()
        baseline, pipeline = M.dated_pipeline(core, contents)
        names = {pipeline.SELF, pipeline.SHARED, pipeline.HOST_LOCK, pipeline.PROFILE_LOCK}.union(
            *map(set, pipeline.PHASE_FILES.values()))
        manifest = self.manifest({name: contents[name] for name in names})
        base = Path('/fixture/userspace-pipeline-dated/work')
        shared = types.SimpleNamespace(UID_BASE=1048576, ROOT=Path('/builder'), SEED=Path('/seed'),
                                       namespaces=lambda: {'user': 1}, validate_ready=mock.Mock())
        builder = types.SimpleNamespace(nspawn_command=lambda *a: ['nspawn', '--private-users-ownership=chown',
                                        'old', 'tail'], validate_probe=lambda data: {'probe': data.decode()})
        report = {'inputs': manifest, 'account_date_replay': M.baseline_metadata(baseline, contents), 'phases': []}
        files = {base.parent / 'source-manifest.json': json.dumps(manifest).encode()}
        for name, selected in pipeline.PHASE_FILES.items():
            slot = base.parent / name
            files[slot / 'inputs/manifest.json'] = json.dumps({'revision': manifest['revision'],
                'files': {key: manifest['files'][key] for key in selected}}).encode()
            for key in selected:
                files[slot / 'inputs/project' / key] = contents[key]
            files[slot / 'runtime.log'] = b'passed'
            files[slot / 'inputs/run.py'] = M.baseline_runner(name, 'c' * 64)
            report['phases'].append({'name': name, 'command': pipeline.command(shared, builder, base.parent, slot, name),
                'probe': {'probe': 'passed'}, 'helper_returncode': 0, 'api_ready': {}, 'api_profile': {},
                'recipe_receipt_sha256': 'c' * 64})
        pipeline.verify_export = mock.Mock()
        def held(path, **kwargs):
            data = files[path]
            return data, M.pin(data)
        return core, contents, baseline, pipeline, base, shared, builder, report, files, held

    def test_new_base_sources_exact_closure_snapshots_probe_and_provenance(self):
        core, contents, baseline, pipeline, base, shared, builder, report, files, held = self.base_fixture()
        with mock.patch.object(core, 'held', side_effect=held), \
                mock.patch.object(M, 'dated_pipeline', return_value=(baseline, pipeline)):
            core.base_sources(shared, base, contents, report, builder)
            pipeline.verify_export.assert_called_once_with(shared, base.parent, contents)
            shared.validate_ready.assert_called_once_with({}, {}, {'user': 1})
            for key in list(report['account_date_replay']):
                previous = report['account_date_replay'][key]
                report['account_date_replay'][key] = None
                with self.subTest(field=key), self.assertRaisesRegex(ValueError, 'driver provenance'):
                    core.base_sources(shared, base, contents, report, builder)
                report['account_date_replay'][key] = previous
            del report['account_date_replay']
            with self.assertRaisesRegex(ValueError, 'driver provenance'):
                core.base_sources(shared, base, contents, report, builder)

    def test_base_sources_reject_historical_wrong_records_snapshots_and_api(self):
        for mutation in ('historical', 'blob', 'pin', 'report-inputs', 'snapshot', 'source', 'runner', 'command', 'probe', 'helper'):
            core, contents, baseline, pipeline, base, shared, builder, report, files, held = self.base_fixture()
            source_manifest = base.parent / 'source-manifest.json'
            manifest = json.loads(files[source_manifest])
            if mutation == 'historical':
                del manifest['files'][M.BASE_DRIVER]
                report['inputs'] = manifest
            elif mutation in ('blob', 'pin'):
                record = manifest['files'][M.BASE_DRIVER]
                record['git_blob' if mutation == 'blob' else 'sha256'] = '0' * (40 if mutation == 'blob' else 64)
                report['inputs'] = manifest
            elif mutation == 'report-inputs':
                report['inputs'] = {}
            elif mutation == 'snapshot':
                files[base.parent / 'export/inputs/manifest.json'] = b'{}'
            elif mutation == 'source':
                files[base.parent / 'export/inputs/project' / M.BASE_DRIVER] = b'changed'
            elif mutation == 'runner':
                files[base.parent / 'export/inputs/run.py'] = b'changed'
            else:
                report['phases'][1][{'command': 'command', 'probe': 'probe', 'helper': 'helper_returncode'}[mutation]] = None
            files[source_manifest] = json.dumps(manifest).encode()
            with mock.patch.object(core, 'held', side_effect=held), \
                    mock.patch.object(M, 'dated_pipeline', return_value=(baseline, pipeline)), \
                    self.subTest(mutation=mutation), self.assertRaises(ValueError):
                core.base_sources(shared, base, contents, report, builder)
            pipeline.verify_export.assert_not_called()

    def test_old_base_inputs_rejects_failed_chains_before_source_or_deb_reads(self):
        core = M.configure_timesync(self.core())
        contents = self.contents()
        base = Path('/fixture/work')
        shared = types.SimpleNamespace(safe_directory=mock.Mock())
        report = {'phase': str(base.parent), 'work': str(base), 'success': True,
                  'final_archive': json.loads(contents[core.CONTRACT])['base']['archive'],
                  'host_package_inventory_unchanged': True, 'mapped_processes_final': [], 'output_mounts_final': [],
                  'phases': [{'name': name, 'success': True, 'returncode': 0, 'mapped_processes_final': [],
                              'output_mounts_final': []} for name in ('foreign', 'configure', 'export')]}
        mutations = [dict(success=False), dict(host_package_inventory_unchanged=False),
                     dict(mapped_processes_final=[1]), dict(output_mounts_final=['mount'])]
        for changes in mutations:
            bad = dict(report, **changes)
            with mock.patch.object(core, 'base_sources') as source_check, mock.patch.object(core, 'held',
                    return_value=(json.dumps(bad).encode(), {})) as held, self.assertRaisesRegex(ValueError, 'pipeline chain'):
                core.base_inputs(shared, base, contents, Path('/missing.deb'))
            held.assert_called_once_with(base.parent / 'receipt.json', owner=0, limit=1024**2)
            source_check.assert_not_called()

    def test_exact_baseline_runners_match_pinned_staging_code(self):
        core = self.core()
        contents = self.contents()
        _, pipeline = M.dated_pipeline(core, contents)
        manifest = self.manifest(contents)
        foreign = {'success': True, 'dpkg_status_bytes': 0,
                   'repository': {'manifest': {'sha256': 'c' * 64}}, 'adapter_manifest': {'sha256': 'c' * 64}}
        shared = types.SimpleNamespace(UID_BASE=os.getuid(), namespaces=lambda: {'user': 1},
            regular_bytes=lambda *a, **k: json.dumps(foreign).encode(), sha=lambda path: 'c' * 64,
            read_json=lambda *a, **k: {'success': True}, project_snapshot=mock.Mock(),
            atomic_json=lambda path, value: path.write_text(json.dumps(value)))
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(M.os, 'chown'), \
                mock.patch.object(pipeline, 'immutable_file', side_effect=lambda path, data: path.write_bytes(data)):
            phase = Path(directory)
            for name in ('foreign', 'configure', 'export'):
                slot, _ = pipeline.stage_inputs(shared, phase, name, manifest, contents, b'index')
                self.assertEqual((slot / 'inputs/run.py').read_bytes(), M.baseline_runner(name, 'c' * 64))

    def test_retained_base_bom_inventory_repository_and_receipt_chain_gates(self):
        core = M.configure_timesync(self.core())
        contents = self.contents()
        contract = json.loads(contents[core.CONTRACT])
        normalized = json.loads(contents['configs/host-userspace-profiles.lock.json'])['profiles']['normalized-20261106']
        contract['base'].update(archive=M.pin(b'archive'), bom=M.pin(b'bom'))
        contract['addition'].update(M.pin(b'deb'))
        contents[core.CONTRACT] = json.dumps(contract).encode()
        shared = types.SimpleNamespace(safe_directory=mock.Mock(), UID_BASE=os.getuid(), validate_source_tree=mock.Mock())
        deriver = types.SimpleNamespace(status_inventory=mock.Mock(return_value=['220 locked packages']),
                                       parse_lock=lambda data: ['220 locked packages'])
        repo = types.SimpleNamespace(check_repository=mock.Mock())
        configured = json.dumps({'success': True}).encode()
        configured_sha = M.pin(configured)['sha256']
        receipt = {'success': True, 'phase': 'final-userspace-export', 'archive': contract['base']['archive'],
            'inventory': normalized['inventory'], 'raw_tar': normalized['raw_tar'], 'members': normalized['members'],
            'timestamps': {'epoch': normalized['epoch'], 'native_source_metadata_verified': True},
            'exporter_sha256': M.pin(contents['build/export-host-userspace.py'])['sha256'],
            'configure_receipt_sha256': configured_sha}
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / 'base/work'
            (base / 'arm-root').mkdir(parents=True)
            extra = Path(directory) / 'extra.deb'
            receipt_bytes = json.dumps(receipt).encode()
            report = {'phase': str(base.parent), 'work': str(base), 'success': True,
                'final_archive': contract['base']['archive'], 'host_package_inventory_unchanged': True,
                'mapped_processes_final': [], 'output_mounts_final': [],
                'phases': [{'name': name, 'success': True, 'returncode': 0, 'mapped_processes_final': [],
                    'output_mounts_final': [], 'recipe_receipt_sha256': M.pin(
                        receipt_bytes if name == 'export' else configured)['sha256']}
                    for name in ('foreign', 'configure', 'export')]}
            files = {base.parent / 'receipt.json': json.dumps(report).encode(),
                base / 'export-result/receipt.json': receipt_bytes,
                base / 'export-result/rootfs.tar.gz': b'archive', base / 'export-result/rootfs-bom.json': b'bom',
                extra: b'deb', base / 'arm-root/var/lib/dpkg/status': b'status', base / 'repo/manifest.json': b'repo',
                base / 'foreign-receipt.json': configured, base / 'configure-receipt.json': configured}
            def held(path, **kwargs):
                data = files[path]
                return data, M.pin(data)
            with mock.patch.object(core, 'held', side_effect=held), mock.patch.object(core, 'base_sources') as sources, \
                    mock.patch.object(core, 'module', side_effect=lambda name, data: deriver if name == core.DERIVER else repo):
                result = core.base_inputs(shared, base, contents, extra, builder='authenticated')
                self.assertEqual(result[:2], (b'bom', b'deb'))
                sources.assert_called_once_with(shared, base, contents, report, 'authenticated')
                repo.check_repository.assert_called_once_with(M.pin(b'repo')['sha256'], owner=os.getuid())
                for relative, changed, error in (
                    ('export-result/rootfs.tar.gz', b'changed', 'archive pin'),
                    ('export-result/rootfs-bom.json', b'changed', 'BOM pin'),
                    ('foreign-receipt.json', b'{"success":false}', 'phase receipt'),
                    ('configure-receipt.json', b'{"success":false}', 'phase receipt')):
                    path, previous = base / relative, files[base / relative]
                    files[path] = changed
                    with self.subTest(gate=relative), self.assertRaisesRegex(ValueError, error):
                        core.base_inputs(shared, base, contents, extra, builder='authenticated')
                    files[path] = previous
                deriver.status_inventory.return_value = ['wrong packages']
                with self.assertRaisesRegex(ValueError, 'native base inventory'):
                    core.base_inputs(shared, base, contents, extra)
                deriver.status_inventory.return_value = ['220 locked packages']
                repo.check_repository.side_effect = ValueError('repository mismatch')
                with self.assertRaisesRegex(ValueError, 'repository mismatch'):
                    core.base_inputs(shared, base, contents, extra)

    def test_time_verifier_runs_original_and_refuses_replay_pin_provenance_changes(self):
        core = self.core()
        original = mock.Mock(return_value={'original': 'verified'})
        core.verify_export = original
        core = M.configure_timesync(core)
        contents = self.contents()
        expected = M.validate_sources(core, contents)['profiles']['time']
        manifest = self.manifest(contents)
        staged = (json.dumps(manifest, sort_keys=True) + '\n').encode()
        configured_sha = 'c' * 64
        receipt = dict(expected, timestamps={'epoch': expected['epoch']}, account_date_replay={
            'schema': 1, 'profile': 'time', 'adapter': M.pin(contents[M.ADAPTER]),
            'helper': M.pin(contents[M.HELPER]), 'contract': M.pin(contents[M.CONTRACT]),
            'source_manifest': M.pin(staged), 'input_receipt_sha256': configured_sha})
        shared = types.SimpleNamespace(UID_BASE=1048576)
        def held(path, **kwargs):
            data = staged if path.name == 'manifest.json' else json.dumps(receipt).encode()
            return data, M.pin(data)
        with mock.patch.object(core, 'held', side_effect=held):
            self.assertEqual(core.verify_export(shared, Path('/phase'), contents, manifest, configured_sha),
                             {'original': 'verified'})
            for key in ('archive', 'raw_tar', 'members', 'inventory', 'bom', 'timestamps'):
                previous = receipt[key]
                receipt[key] = None if key != 'timestamps' else {}
                with self.subTest(pin=key), self.assertRaises(ValueError):
                    core.verify_export(shared, Path('/phase'), contents, manifest, configured_sha)
                receipt[key] = previous
            for key in receipt['account_date_replay']:
                previous = receipt['account_date_replay'][key]
                receipt['account_date_replay'][key] = None
                with self.subTest(provenance=key), self.assertRaises(ValueError):
                    core.verify_export(shared, Path('/phase'), contents, manifest, configured_sha)
                receipt['account_date_replay'][key] = previous
            original.side_effect = ValueError('old core provenance failure')
            with self.assertRaisesRegex(ValueError, 'old core provenance'):
                core.verify_export(shared, Path('/phase'), contents, manifest, configured_sha)

    def test_real_old_time_verifier_retains_core_provenance_and_artifact_checks(self):
        core = M.configure_timesync(self.core())
        contents = self.contents()
        manifest = self.manifest(contents)
        staged = (json.dumps(manifest, sort_keys=True) + '\n').encode()
        configured = json.dumps({'success': True, 'configured_packages': 221, 'source_manifest': M.pin(staged),
                                 'inputs': json.loads(contents[core.CONTRACT])}).encode()
        configured_sha = M.pin(configured)['sha256']
        old_adapter = core.module(core.ADAPTER, contents[core.ADAPTER])
        expected = {'archive': M.pin(b'archive'), 'bom': M.pin(b'bom'), 'raw_tar': M.pin(b'raw'),
            'members': 10, 'epoch': 1790812800,
            'inventory': {'packages': 221, 'lock_sha256': M.pin(contents['configs/host-timesync-packages.lock.tsv'])['sha256']}}
        receipt = dict(expected, success=True, phase='final-timesync-userspace-export', profile='timesync-20261106',
            derivation_receipt_sha256=configured_sha, configure_receipt_sha256=configured_sha,
            timestamps={'epoch': expected['epoch']}, provenance={'adapter': M.pin(contents[core.ADAPTER]),
                'backend': old_adapter.BACKEND_PIN, 'deriver': old_adapter.DERIVER_PIN,
                'contract': core.CONTRACT_PIN, 'source_manifest': M.pin(staged)},
            account_date_replay={'schema': 1, 'profile': 'time', 'adapter': M.pin(contents[M.ADAPTER]),
                'helper': M.pin(contents[M.HELPER]), 'contract': M.pin(contents[M.CONTRACT]),
                'source_manifest': M.pin(staged), 'input_receipt_sha256': configured_sha})
        phase = Path('/fixture')
        files = {phase / 'work/timesync-receipt.json': configured,
                 phase / 'export/inputs/manifest.json': staged,
                 phase / 'work/export-timesync-result/rootfs.tar.gz': b'archive',
                 phase / 'work/export-timesync-result/rootfs-bom.json': b'bom'}
        def held(path, **kwargs):
            data = json.dumps(receipt).encode() if path.name == 'receipt.json' else files[path]
            return data, M.pin(data)
        shared = types.SimpleNamespace(UID_BASE=1048576)
        with mock.patch.object(core, 'held', side_effect=held), \
                mock.patch.object(M, 'validate_sources', return_value={'profiles': {'time': expected}}):
            self.assertEqual(core.verify_export(shared, phase, contents, manifest, configured_sha),
                             {'archive': expected['archive'], 'bom': expected['bom']})
            for key in receipt['provenance']:
                previous = receipt['provenance'][key]
                receipt['provenance'][key] = None
                with self.subTest(core_provenance=key), self.assertRaisesRegex(ValueError, 'export provenance'):
                    core.verify_export(shared, phase, contents, manifest, configured_sha)
                receipt['provenance'][key] = previous
            for filename in ('rootfs.tar.gz', 'rootfs-bom.json'):
                path = phase / 'work/export-timesync-result' / filename
                previous = files[path]
                files[path] = b'tampered'
                with self.subTest(artifact=filename), self.assertRaisesRegex(ValueError, 'export bytes'):
                    core.verify_export(shared, phase, contents, manifest, configured_sha)
                files[path] = previous


if __name__ == '__main__':
    unittest.main()
