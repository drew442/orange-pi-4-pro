"""Host-only rejection/orchestration checks; no physical acceptance evidence."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('userspace_pipeline', REPO / 'build/launch-userspace-pipeline.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class PipelineTests(unittest.TestCase):
    def shared(self):
        return M.shared_module({M.SHARED: (REPO / M.SHARED).read_bytes()})

    def test_tag_rejections(self):
        for tag in ('', '../old', 'x/y', '.', 'a b', 'a' * 65, '-x', 'x\n'):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                M.tag_path(tag)
        self.assertEqual(M.tag_path('fresh_02').name, 'userspace-pipeline-fresh_02')

    def test_source_rejects_symlink_and_hardlink(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / 'source'
            source.write_bytes(b'abc')
            (base / 'alias').symlink_to(source)
            with self.assertRaises(OSError):
                M.source_bytes(base / 'alias')
            os.link(source, base / 'hard')
            with self.assertRaises(ValueError):
                M.source_bytes(source)

    def test_git_sources_and_read_only_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            names = {M.SELF, M.SHARED, M.HOST_LOCK, M.PROFILE_LOCK}.union(*map(set, M.PHASE_FILES.values()))
            for name in names:
                path = project / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((REPO / name).read_bytes())
            for args in (['init', '-q'], ['add', '.'], ['-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'Fixture']):
                subprocess.run(['/usr/bin/git', '-C', str(project)] + args, check=True, capture_output=True)
            before = sorted(str(p.relative_to(project)) for p in project.rglob('*'))
            with mock.patch.object(M, '__file__', str(project / M.SELF)), mock.patch.object(M, 'preflight', side_effect=AssertionError('root preflight')), contextlib.redirect_stdout(io.StringIO()) as output:
                M.main(['--project', str(project), '--tag', 'test', '--plan'])
            result = json.loads(output.getvalue())
            self.assertEqual([p['name'] for p in result['phases']], ['foreign', 'configure', 'export'])
            self.assertEqual(before, sorted(str(p.relative_to(project)) for p in project.rglob('*')))
            (project / M.LOCK).write_bytes(b'dirty')
            with self.assertRaisesRegex(ValueError, 'differ from committed'):
                M.committed_sources(project)

    def test_plan_has_no_historical_seed(self):
        result = M.plan('fresh', {'revision': 'a' * 40, 'files': {}})
        self.assertNotIn('userspace-foreign-01', json.dumps(result))
        self.assertEqual(result['work'], str(M.tag_path('fresh') / 'work'))
        self.assertEqual([step['start_free_bytes_exclusive'] for step in result['phases']], [4_000_000_000, 4_000_000_000, 3_500_000_000])

    def test_resource_guards(self):
        good = {'free_bytes': 3_000_000_000, 'available_memory_bytes': 1_000_000_000}
        for name in M.PHASE_FILES:
            M.check_resources(good, name, M.TIMEOUTS[name])
            for sample, elapsed in ((dict(good, free_bytes=2_999_999_999), 0),
                                    (dict(good, available_memory_bytes=999_999_999), 0),
                                    (good, M.TIMEOUTS[name] + 1)):
                with self.assertRaises(ValueError):
                    M.check_resources(sample, name, elapsed)

    def test_commands_use_same_work_and_readonly_inputs(self):
        shared = self.shared()
        builder = types.SimpleNamespace(nspawn_command=lambda *_: ['nspawn', '--private-users-ownership=chown', 'ignored', 'ignored'])
        for name in M.PHASE_FILES:
            args = M.command(shared, builder, Path('/pipeline'), Path('/pipeline') / name, name)
            self.assertIn('--read-only', args)
            self.assertIn('--private-users-ownership=off', args)
            self.assertIn('--bind=/pipeline/work:/a733-export-output', args)
            self.assertIn('--bind-ro=/pipeline/' + name + '/inputs:/a733-sources', args)
            self.assertEqual(any(':/a733-debs' in arg for arg in args), name == 'foreign')
            self.assertNotIn('cp', args)

    def test_work_adapter_preserves_handshake_paths(self):
        adapter = M.WorkSlot(Path('/phase/configure'), Path('/phase/work'))
        self.assertEqual(adapter / 'work', Path('/phase/work'))
        self.assertEqual(adapter / 'work/api-setup-request.json', Path('/phase/work/api-setup-request.json'))
        self.assertEqual(adapter / 'helper.stdout', Path('/phase/configure/helper.stdout'))

    @unittest.skipUnless(os.geteuid() == 0, 'snapshot ownership requires initial-root')
    def test_exclusive_immutable_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'input'
            M.immutable_file(path, b'abc')
            self.assertEqual(path.stat().st_mode & 0o777, 0o444)
            self.assertEqual(path.stat().st_uid, 0)
            with self.assertRaises(FileExistsError):
                M.immutable_file(path, b'replace')
            self.assertEqual(path.read_bytes(), b'abc')

    def test_real_handshake_uses_shared_work(self):
        shared = self.shared()
        with tempfile.TemporaryDirectory() as directory:
            phase = Path(directory)
            slot, work = phase / 'configure', phase / 'work'
            slot.mkdir()
            work.mkdir()
            ready = {'nonce': 'n', 'namespace_inodes': {}, 'bootstrap_root': '/a733-export-output/arm-root'}
            profile = {'nonce': 'n', 'host_userns_inode': 1, 'host_driver_pid': 2, 'repo_manifest_sha256': 'd'}
            def publish(path, value):
                path.write_text(json.dumps(value))
            def helper_wait(timeout):
                request = json.loads((work / 'api-setup-request.json').read_text())
                publish(work / 'api-mount-receipt.json', {'success': True, 'request': request})
                return 0
            helper = mock.Mock(returncode=0)
            helper.wait.side_effect = helper_wait
            with mock.patch.object(shared, 'select_payload', return_value=42), mock.patch.object(shared, 'atomic_json', side_effect=publish), mock.patch.object(shared, 'read_json', side_effect=lambda p, **kw: json.loads(p.read_text())), mock.patch.object(shared, 'sha', return_value='a' * 64), mock.patch.object(shared, 'stop_process'), mock.patch.object(shared.subprocess, 'Popen', return_value=helper):
                shared.bootstrap_api(M.WorkSlot(slot, work), ready, profile, {}, {})
            self.assertTrue((work / 'api-continue.json').exists())
            self.assertTrue((slot / 'helper.stdout').exists())
            self.assertFalse((slot / 'work').exists())

    def test_normalized_archive_validation(self):
        import hashlib
        shared = self.shared()
        shared.UID_BASE = os.getuid()
        data = b'archive bytes'
        expected = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
        contents = {M.PROFILE_LOCK: json.dumps({'profiles': {'normalized-20261106': {'archive': expected}}}).encode()}
        with tempfile.TemporaryDirectory() as directory:
            phase = Path(directory)
            output = phase / 'work/export-result'
            output.mkdir(parents=True)
            receipt = {'success': True, 'archive': expected}
            (output / 'receipt.json').write_text(json.dumps(receipt))
            archive = output / 'rootfs.tar.gz'
            archive.write_bytes(data)
            self.assertEqual(M.verify_export(shared, phase, contents), expected)
            for wrong in (b'short', b'Archive bytes'):
                archive.write_bytes(wrong)
                with self.assertRaisesRegex(ValueError, 'archive differs'):
                    M.verify_export(shared, phase, contents)
            archive.write_bytes(data)
            receipt['archive'] = {'bytes': 1, 'sha256': '0' * 64}
            (output / 'receipt.json').write_text(json.dumps(receipt))
            with self.assertRaisesRegex(ValueError, 'receipt differs'):
                M.verify_export(shared, phase, contents)

    def execute_fixture(self, fail=None, bad_archive=False):
        shared = self.shared()
        shared.mapped_processes = lambda: []
        shared.atomic_json = lambda path, value: path.write_text(json.dumps(value))
        shared.validate_host_inventory = mock.Mock()
        order = []
        report = {}
        with tempfile.TemporaryDirectory() as directory:
            phase = Path(directory) / 'pipeline'
            def stage(_shared, _phase, name, *_):
                order.append(('stage', name))
                return phase / name, {'nonce': 'profile'} if name == 'configure' else None
            def run(_shared, _builder, _phase, _slot, name, _profile, step):
                order.append(('run', name))
                if name == fail:
                    raise RuntimeError('mocked failure')
                step['success'] = True
            with mock.patch.object(M, 'stage_inputs', side_effect=stage), mock.patch.object(M, 'run_phase', side_effect=run), mock.patch.object(M, 'mounts', return_value=[]), mock.patch.object(M, 'verify_export', return_value={'bytes': 1, 'sha256': 'a' * 64}, side_effect=ValueError('archive differs') if bad_archive else None), mock.patch.object(M, 'immutable_file', side_effect=lambda p, b: p.write_bytes(b)), mock.patch.object(M.os, 'chown'), mock.patch.object(M.subprocess, 'check_output', return_value=b'inventory'):
                if bad_archive:
                    with self.assertRaisesRegex(ValueError, 'archive differs'):
                        M.execute(shared, object(), phase, {}, {M.HOST_LOCK: b'lock'}, b'index', b'inventory', report)
                elif fail:
                    with self.assertRaisesRegex(RuntimeError, 'mocked failure'):
                        M.execute(shared, object(), phase, {}, {M.HOST_LOCK: b'lock'}, b'index', b'inventory', report)
                else:
                    M.execute(shared, object(), phase, {}, {M.HOST_LOCK: b'lock'}, b'index', b'inventory', report)
            receipt = json.loads((phase / 'receipt.json').read_text())
            self.assertEqual(receipt['success'], fail is None and not bad_archive)
            self.assertTrue(receipt['host_package_inventory_unchanged'])
        return order

    def test_archive_mismatch_never_publishes_success(self):
        self.execute_fixture(bad_archive=True)

    def test_mocked_sequence(self):
        self.assertEqual(self.execute_fixture(), [(action, name) for name in M.PHASE_FILES for action in ('stage', 'run')])

    def test_failure_stops_before_export_and_records_cleanup(self):
        order = self.execute_fixture('configure')
        self.assertEqual(order[-1], ('run', 'configure'))
        self.assertNotIn(('stage', 'export'), order)

    def test_mocked_process_runs_probe_receipt_and_reaps(self):
        shared = self.shared()
        shared.namespaces = lambda: {}
        shared.resource_sample = lambda: {'free_bytes': 5_000_000_000, 'available_memory_bytes': 2_000_000_000}
        shared.mapped_processes = lambda: []
        shared.stop_process = mock.Mock()
        shared.regular_bytes = mock.Mock(return_value=b'{"success":true}')
        shared.sha = lambda _: 'a' * 64
        process = mock.Mock(returncode=0)
        process.poll.side_effect = [None, 0]
        process.wait.return_value = 0
        builder = types.SimpleNamespace(validate_probe=mock.Mock(return_value={'ok': True}))
        with tempfile.TemporaryDirectory() as directory:
            phase = Path(directory)
            slot = phase / 'foreign'
            slot.mkdir()
            step = {}
            with mock.patch.object(M, 'command', return_value=['nspawn']), mock.patch.object(M, 'mounts', return_value=[]), mock.patch.object(M.subprocess, 'Popen', return_value=process) as launch, mock.patch.object(M.time, 'sleep'):
                M.run_phase(shared, builder, phase, slot, 'foreign', None, step)
            self.assertEqual(launch.call_count, 1)
            self.assertEqual(len(step['resources']), 2)
            self.assertTrue(step['success'])
            shared.stop_process.assert_called_once_with(process)
            builder.validate_probe.assert_called_once()


if __name__ == '__main__':
    unittest.main()
