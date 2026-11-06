"""Launcher host fixtures; no ARM execution, physical boot or KVM acceptance."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

PROJECT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT / 'build/launch-timesync-userspace.py'
spec = importlib.util.spec_from_file_location('launch_timesync', SOURCE)
l = importlib.util.module_from_spec(spec)
spec.loader.exec_module(l)
shared = l.module(l.SHARED, (PROJECT / l.SHARED).read_bytes())
pipeline = l.module(l.PIPELINE, (PROJECT / l.PIPELINE).read_bytes())


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def project(self):
        checkout = self.root / 'project'
        checkout.mkdir()
        for name in l.SOURCE_FILES:
            path = checkout / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((PROJECT / name).read_bytes())
        env = dict(l.ENV, GIT_AUTHOR_NAME='Test', GIT_AUTHOR_EMAIL='test@example.invalid',
                   GIT_COMMITTER_NAME='Test', GIT_COMMITTER_EMAIL='test@example.invalid')
        subprocess.run(['/usr/bin/git', 'init', '-q', str(checkout)], check=True, env=env)
        subprocess.run(['/usr/bin/git', '-C', str(checkout), 'add', '.'], check=True, env=env)
        subprocess.run(['/usr/bin/git', '-C', str(checkout), 'commit', '-qm', 'Fixture'], check=True, env=env)
        return checkout

    def test_real_git_source_manifest_and_contract(self):
        manifest, contents = l.committed_sources(self.project())
        self.assertEqual(set(manifest['files']), set(l.SOURCE_FILES))
        self.assertEqual(l.pin(contents[l.CONTRACT]), l.CONTRACT_PIN)
        self.assertIn(l.SELF, contents)

    def test_modified_committed_source_refused(self):
        checkout = self.project()
        with (checkout / l.SHARED).open('ab') as stream:
            stream.write(b'\n# changed\n')
        with self.assertRaisesRegex(ValueError, 'committed blob'):
            l.committed_sources(checkout)

    def test_untracked_launcher_refused(self):
        checkout = self.project()
        subprocess.run(['/usr/bin/git', '-C', str(checkout), 'rm', '--cached', '-q', l.SELF], check=True)
        # Commit the removal to make HEAD itself lack the source.
        env = dict(os.environ, GIT_AUTHOR_NAME='Test', GIT_AUTHOR_EMAIL='t@example.invalid',
                   GIT_COMMITTER_NAME='Test', GIT_COMMITTER_EMAIL='t@example.invalid')
        subprocess.run(['/usr/bin/git', '-C', str(checkout), 'commit', '-qm', 'Remove'], check=True, env=env)
        with self.assertRaisesRegex(ValueError, 'uncommitted source'):
            l.committed_sources(checkout)

    def test_plan_never_reads_external_inputs(self):
        manifest, _ = l.committed_sources(self.project())
        with mock.patch.object(l, 'held', side_effect=AssertionError('host read')):
            report = l.plan('new', 'tracked', Path('/missing/expected.deb'), manifest)
        self.assertEqual(report['base'], str(l.BASE / 'userspace-pipeline-tracked/work'))
        self.assertEqual(report['clone'], ['arm-root', 'repo'])
        self.assertEqual(report['phases'][0]['start_free_bytes_minimum'], 4 * 1024**3)
        self.assertNotIn('final_archive', report)

    def test_tag_and_absolute_path_refusals(self):
        for value in ('', '../old', '/old', 'a.b', 'x' * 65, 'x\n'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                l.tag_path(value)
        for value in ('relative', '/absolute/../escape'):
            with self.assertRaises(ValueError):
                l.absolute(Path(value))

    def test_held_rejects_symlink_and_external_hardlink(self):
        source = self.root / 'input'
        source.write_bytes(b'ok')
        alias = self.root / 'alias'
        alias.symlink_to(source)
        with self.assertRaises(OSError):
            l.held(alias)
        alias.unlink()
        os.link(source, alias)
        with self.assertRaisesRegex(ValueError, 'single regular'):
            l.held(source)

    def test_held_rejects_oversized_input(self):
        source = self.root / 'input'
        source.write_bytes(b'oversized')
        with self.assertRaisesRegex(ValueError, 'oversized'):
            l.held(source, limit=1)

    def test_held_streamed_fact(self):
        source = self.root / 'input'
        source.write_bytes(b'archive unknown until built')
        data, fact = l.held(source, data=False)
        self.assertEqual(data, b'')
        self.assertEqual(fact, l.pin(source.read_bytes()))

    def test_capacity_refusal_before_base_lookup_and_artifacts(self):
        fake = SimpleNamespace(safe_directory=mock.Mock(), resource_sample=lambda: {'free_bytes': 3_246_000_000})
        with mock.patch.multiple(l.os, getuid=lambda: 0, geteuid=lambda: 0, getgid=lambda: 0, getegid=lambda: 0):
            with self.assertRaisesRegex(ValueError, '5 GiB'):
                l.preflight(fake, pipeline, self.root / 'new', self.root / 'missing/work', {})
        self.assertEqual(list(self.root.iterdir()), [])

    def test_clone_budget_counts_full_bytes_and_nodes(self):
        base = self.root / 'base'
        for name in ('arm-root', 'repo'):
            (base / name).mkdir(parents=True)
            (base / name / 'file').write_bytes(b'a' * 9000)
        budget, records = l.tree_budget(shared, base)
        self.assertEqual(budget, 18000 + 4 * 4096)
        self.assertEqual(len(records), 4)
        os.link(base / 'repo/file', self.root / 'external')
        with self.assertRaisesRegex(ValueError, 'external hardlink'):
            l.tree_budget(shared, base)

    def test_full_capacity_failure_never_creates_phase(self):
        base = self.root / 'base'
        phase = self.root / 'phase'
        fake = SimpleNamespace(resource_sample=lambda: {'free_bytes': l.MIN_INITIAL})
        inputs = (b'bom', b'deb', 'x', {})
        with mock.patch.object(l, 'tree_budget', return_value=(10 * 1024**3, {})):
            with self.assertRaisesRegex(ValueError, 'full-clone'):
                l.execute(fake, pipeline, None, phase, base, {}, {}, inputs, b'', {})
        self.assertFalse(phase.exists())

    def test_stages_have_byte_identical_project_manifests_and_private_scratch(self):
        phase = self.root / 'phase'
        (phase / 'work').mkdir(parents=True)
        (phase / 'work/timesync-receipt.json').write_bytes(b'{"success": true}')
        contents = {l.CONTRACT: (PROJECT / l.CONTRACT).read_bytes(), l.DERIVER: b'# derive', l.ADAPTER: b'# adapter'}
        manifest = {'revision': 'a' * 40, 'files': {k: dict(l.pin(v), git_blob='b' * 40) for k, v in contents.items()}}
        # Root ownership operations are mocked; namespaces/ARM are never entered.
        with mock.patch('os.chown'), mock.patch('os.fchown'), mock.patch.object(shared, 'UID_BASE', os.getuid()):
            slots = [l.stage_inputs(shared, pipeline, phase, name, manifest, contents, b'bom', b'deb', 'c' * 64)
                     for name in ('configure', 'export')]
        first, second = [slot / 'inputs' for slot, _ in slots]
        self.assertEqual((first / 'manifest.json').read_bytes(), (second / 'manifest.json').read_bytes())
        self.assertEqual((first / 'manifest.json').read_bytes(), (json.dumps(manifest, sort_keys=True) + '\n').encode())
        self.assertIn('m.main()', (first / 'run.py').read_text())
        self.assertIn('export-timesync-result', (second / 'run.py').read_text())
        self.assertEqual(slots[1][1]['repo_manifest_sha256'], 'c' * 64)
        self.assertNotEqual(slots[0][1]['nonce'], slots[1][1]['nonce'])
        for source in (first, second):
            runner = (source / 'run.py').read_text()
            self.assertLess(runner.index('propagation-'), runner.index('import importlib.util'))
            compile(runner, str(source / 'run.py'), 'exec')
        self.assertNotEqual(slots[0][0] / 'scratch', slots[1][0] / 'scratch')

    def test_existing_stage_refused(self):
        phase = self.root / 'phase'
        (phase / 'configure').mkdir(parents=True)
        with self.assertRaises(FileExistsError):
            l.stage_inputs(shared, pipeline, phase, 'configure', {}, {}, b'', b'', '')

    def test_base_chain_failure_precedes_output_or_deb_read(self):
        base = self.root / 'pipeline/work'
        base.mkdir(parents=True)
        (base.parent / 'receipt.json').write_text('{"success":false}')
        contents = {l.CONTRACT: (PROJECT / l.CONTRACT).read_bytes()}
        fake = SimpleNamespace(safe_directory=mock.Mock(), UID_BASE=os.getuid())
        real_held = l.held
        with mock.patch.object(l, 'held', side_effect=lambda path, **kwargs: real_held(path, limit=kwargs.get('limit'))) as held:
            with self.assertRaisesRegex(ValueError, 'pipeline chain'):
                l.base_inputs(fake, base, contents, self.root / 'absent.deb')
        self.assertEqual(held.call_count, 1)

    def test_failed_configuration_never_stages_or_runs_export(self):
        phase = self.root / 'new'
        fake = SimpleNamespace(resource_sample=lambda: {'free_bytes': 100 * 1024**3},
                               UID_BASE=os.getuid(), interrupted=shared.interrupted,
                               atomic_json=shared.atomic_json, validate_source_tree=mock.Mock(),
                               mapped_processes=lambda: [], phase_mounts=lambda path: [],
                               INVENTORY=['inventory'], validate_host_inventory=mock.Mock())
        report = {}
        with mock.patch.object(l, 'tree_budget', return_value=(1, {})), \
             mock.patch.object(l, 'stage_inputs', return_value=(phase / 'configure', {})) as stage, \
             mock.patch.object(l, 'run_phase', side_effect=ValueError('configuration rejected')) as run, \
             mock.patch.object(l.subprocess, 'run'), \
             mock.patch.object(l.subprocess, 'check_output', return_value=b'initial'), \
             mock.patch('os.chown'), mock.patch('os.fchown'):
            with self.assertRaisesRegex(ValueError, 'configuration rejected'):
                l.execute(fake, pipeline, None, phase, self.root / 'base', {},
                          {l.HOST_LOCK: b'lock'}, (b'bom', b'deb', 'repo', {}), b'initial', report)
        self.assertEqual(stage.call_count, 1)
        self.assertEqual(stage.call_args.args[3], 'configure')
        self.assertEqual(run.call_count, 1)
        self.assertFalse(json.loads((phase / 'receipt.json').read_text())['success'])
        self.assertEqual([p['name'] for p in report['phases']], ['configure'])

    def test_source_dependency_closed_set_contains_both_phase_helpers(self):
        contents = {name: (PROJECT / name).read_bytes() for name in l.SOURCE_FILES}
        d = l.module(l.DERIVER, contents[l.DERIVER])
        self.assertLessEqual(set(d.SOURCE_FILES), set(l.SOURCE_FILES))
        for name in (l.SELF, l.ADAPTER, l.PIPELINE, l.SHARED, l.HOST_LOCK):
            self.assertIn(name, l.SOURCE_FILES)

    def helper(self, mismatch=None):
        ns = {name: number + 100 for number, name in enumerate(shared.NAMES)}
        expected = {'nonce': 'a' * 64, 'namespaces': ns, 'host_mount_namespace': 1,
                    'root': {'dev': 3, 'ino': 4}}
        mount = mock.Mock(return_value=0)
        actual_ns = dict(ns)
        if mismatch == 'namespace':
            actual_ns['mnt'] += 1
        with mock.patch('sys.argv', ['helper', json.dumps(expected)]), \
             mock.patch.multiple(l.os, getuid=lambda: 0, geteuid=lambda: 0,
                                 getgid=lambda: 0, getegid=lambda: 0), \
             mock.patch('os.stat', return_value=SimpleNamespace(st_dev=3, st_ino=5 if mismatch == 'root' else 4)), \
             mock.patch('pathlib.Path.stat', autospec=True, side_effect=lambda path: SimpleNamespace(st_ino=actual_ns[path.name])), \
             mock.patch('pathlib.Path.read_text', autospec=True, side_effect=lambda path: '1 1 1' if mismatch == 'uid' else
                        ('0 0 4294967295' if path.name in ('uid_map', 'gid_map') else '1 0 1:1 / / rw - ext4 /dev/x rw')), \
             mock.patch('ctypes.CDLL', return_value=SimpleNamespace(mount=mount)), mock.patch('builtins.print'):
            if mismatch:
                with self.assertRaises(ValueError):
                    exec(compile(l.PROP_HELPER, '<fixed helper>', 'exec'), {})
            else:
                exec(compile(l.PROP_HELPER, '<fixed helper>', 'exec'), {})
        return mount

    def test_fixed_helper_only_mounts_after_identity_validation(self):
        for mismatch in ('namespace', 'root', 'uid'):
            with self.subTest(mismatch=mismatch):
                self.helper(mismatch).assert_not_called()
        self.helper().assert_called_once_with(None, b'/', None, (1 << 18) | (1 << 14), None)

    def test_failed_or_invalid_propagation_never_continues(self):
        slot = self.root / 'slot'
        work = self.root / 'work'
        slot.mkdir()
        work.mkdir()
        fake = SimpleNamespace(select_payload=mock.Mock(return_value=9999),
                               ROOT=SimpleNamespace(stat=lambda: SimpleNamespace(st_dev=3, st_ino=4)),
                               status_fields=lambda path: {key: '0' for key in ('Uid', 'Gid', 'NoNewPrivs', 'CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb')},
                               stop_process=mock.Mock(), atomic_json=mock.Mock())
        ns = {name: number + 100 for number, name in enumerate(shared.NAMES)}
        for code in (1, 0):
            for path in slot.iterdir():
                path.unlink()
            proc = mock.Mock()
            proc.wait.return_value = code
            with mock.patch.object(l.subprocess, 'Popen', return_value=proc), \
                 mock.patch.object(l, 'held', return_value=(b'{"success":false}', {})):
                with self.assertRaisesRegex(ValueError, 'helper (failed|proof differs)'):
                    l.propagation(fake, pipeline, slot, work, 'export', {'namespace_inodes': ns},
                                  {'nonce': 'a' * 64}, dict(ns, user=10, mnt=11), {})
            fake.atomic_json.assert_not_called()
            self.assertEqual(fake.stop_process.call_args.args, (proc,))

    def test_changed_payload_or_capabilities_never_continues(self):
        keys = ('Uid', 'Gid', 'NoNewPrivs', 'CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb')
        before = {key: '0' for key in keys}
        ns = {name: number + 100 for number, name in enumerate(shared.NAMES)}
        host = dict(ns, user=10, mnt=11)
        proof = {'success': True, 'nonce': 'a' * 64, 'namespace_inodes': dict(ns, user=10), 'root': {'dev': 3, 'ino': 4}}
        for changed in ('pid', 'caps'):
            slot = self.root / changed
            slot.mkdir()
            fake = SimpleNamespace(select_payload=mock.Mock(side_effect=[9999, 10000 if changed == 'pid' else 9999]),
                                   ROOT=SimpleNamespace(stat=lambda: SimpleNamespace(st_dev=3, st_ino=4)),
                                   status_fields=mock.Mock(side_effect=[before, dict(before, CapEff='1')]),
                                   atomic_json=mock.Mock(), stop_process=mock.Mock())
            proc = mock.Mock()
            proc.wait.return_value = 0
            with mock.patch.object(l.subprocess, 'Popen', return_value=proc), \
                 mock.patch.object(l, 'held', return_value=(json.dumps(proof).encode(), {})):
                with self.assertRaisesRegex(ValueError, 'payload (changed|credentials/capabilities changed)'):
                    l.propagation(fake, pipeline, slot, self.root / 'work', 'configure',
                                  {'namespace_inodes': ns}, {'nonce': 'a' * 64}, host, {})
            fake.atomic_json.assert_not_called()

    def test_ready_mode_overrides_restrictive_umask(self):
        fragment = l.PROP_RUNNER.split("path=W/('propagation-", 1)[1].split('continuation=', 1)[0]
        fragment = "path=W/('propagation-" + fragment
        previous = os.umask(0o077)
        try:
            exec(compile(fragment, '<ready publication>', 'exec'),
                 {'W': self.root, 'STAGE': 'configure', 'ready': {}, 'os': os, 'json': json})
        finally:
            os.umask(previous)
        self.assertEqual((self.root / 'propagation-configure-ready.json').stat().st_mode & 0o777, 0o444)

    def test_base_sources_authenticates_snapshots_and_probe_logs(self):
        contents = {name: (PROJECT / name).read_bytes() for name in l.SOURCE_FILES}
        names = {l.PIPELINE, l.SHARED, l.HOST_LOCK, pipeline.PROFILE_LOCK}.union(*map(set, pipeline.PHASE_FILES.values()))
        records = {name: dict(l.pin(contents[name]), git_blob=l.hashlib.sha1(
                   b'blob ' + str(len(contents[name])).encode() + b'\0' + contents[name]).hexdigest()) for name in names}
        manifest = {'revision': 'a' * 40, 'files': records}
        base = self.root / 'tracked/work'
        base.mkdir(parents=True)
        (base.parent / 'source-manifest.json').write_text(json.dumps(manifest))
        fake_shared = SimpleNamespace(ROOT=shared.ROOT, SEED=shared.SEED,
                                      validate_ready=mock.Mock(), namespaces=lambda: {})
        builder = SimpleNamespace(nspawn_command=lambda *args: ['nspawn', '--private-users-ownership=chown', 'old', 'old'],
                                  validate_probe=mock.Mock(return_value={'verified': True}))
        report = {'inputs': manifest, 'phases': []}
        for name, selected in pipeline.PHASE_FILES.items():
            slot = base.parent / name
            (slot / 'inputs').mkdir(parents=True)
            (slot / 'inputs/manifest.json').write_text(json.dumps({'revision': manifest['revision'], 'files': {key: records[key] for key in selected}}))
            for key in selected:
                path = slot / 'inputs/project' / key
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(contents[key])
            (slot / 'runtime.log').write_bytes(b'probe fixture')
            report['phases'].append({'name': name, 'command': pipeline.command(fake_shared, builder, base.parent, slot, name),
                                     'probe': {'verified': True}, 'helper_returncode': 0, 'api_ready': {}, 'api_profile': {}})
        real_held = l.held
        with mock.patch.object(l, 'held', side_effect=lambda path, **kwargs: real_held(path, limit=kwargs.get('limit'))):
            l.base_sources(fake_shared, base, contents, report, builder)
            self.assertEqual(builder.validate_probe.call_count, 3)
            original = report['phases'][0]['command']
            report['phases'][0]['command'] = ['unreviewed']
            with self.assertRaisesRegex(ValueError, 'phase command differs'):
                l.base_sources(fake_shared, base, contents, report, builder)
            report['phases'][0]['command'] = original
            builder.validate_probe.return_value = {'verified': False}
            with self.assertRaisesRegex(ValueError, 'phase probe differs'):
                l.base_sources(fake_shared, base, contents, report, builder)
            builder.validate_probe.return_value = {'verified': True}
            original_inputs = report['inputs']
            report['inputs'] = {}
            with self.assertRaisesRegex(ValueError, 'source manifest differs'):
                l.base_sources(fake_shared, base, contents, report, builder)
            report['inputs'] = original_inputs
            target = base.parent / 'export/inputs/project/build/export-host-userspace.py'
            target.write_bytes(target.read_bytes() + b'\n# corrupted')
            with self.assertRaisesRegex(ValueError, 'phase source differs'):
                l.base_sources(fake_shared, base, contents, report, builder)

    def test_workslot_routes_full_relative_work_path(self):
        slot = pipeline.WorkSlot(Path('/stage/configure'), Path('/stage/work'))
        self.assertEqual(slot / 'work/api-ready.json', Path('/stage/work/api-ready.json'))
        self.assertEqual(slot / 'helper.stdout', Path('/stage/configure/helper.stdout'))

    def test_runtime_limits_and_timeouts(self):
        for name, timeout in l.TIMEOUTS.items():
            l.check_resources({'free_bytes': l.RUNTIME_FLOOR, 'available_memory_bytes': 1_000_000_000}, name, timeout)
            for sample in ({'free_bytes': l.RUNTIME_FLOOR - 1, 'available_memory_bytes': 1_000_000_000},
                           {'free_bytes': l.RUNTIME_FLOOR, 'available_memory_bytes': 999_999_999}):
                with self.assertRaises(ValueError):
                    l.check_resources(sample, name, 0)
            with self.assertRaises(ValueError):
                l.check_resources({'free_bytes': l.RUNTIME_FLOOR, 'available_memory_bytes': 1_000_000_000}, name, timeout + 1)


if __name__ == '__main__':
    unittest.main()
