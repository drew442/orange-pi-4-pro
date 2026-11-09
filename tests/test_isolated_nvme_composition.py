"""File-only tests for the isolated composer boundary; no hardware acceptance."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import py_compile
import subprocess
import tempfile
import types
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, REPO / relative)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


launch = load('isolated_composer', 'build/launch-isolated-nvme-composition.py')
builder = load('isolation_builder', 'build/clean-builder.py')
shared = load('composition_shared', 'build/launch-userspace-configuration.py')


class CompositionBoundary(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)

    def fixture(self, name='input', data=b'component'):
        path = self.root / name
        path.write_bytes(data)
        return path, launch.pin(data)

    def test_plan_is_read_only_and_does_not_connect_or_launch(self):
        with mock.patch.object(launch, 'execute', side_effect=AssertionError('execution forbidden')):
            with mock.patch.object(launch.subprocess, 'check_output', side_effect=AssertionError('process forbidden')):
                with mock.patch('builtins.print'):
                    launch.main(['--project', str(self.root), '--tag', 'isolated-20261109-01', '--plan'])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_untrusted_source_reader_is_rejected_before_read_or_execution(self):
        guard = types.SimpleNamespace(root_owned_path=mock.Mock(side_effect=ValueError('untrusted ownership')))
        with mock.patch.object(launch, 'module') as execute, mock.patch.object(launch, 'trusted_bytes') as read:
            with self.assertRaisesRegex(ValueError, 'untrusted ownership'):
                launch.project_apis(self.root, guard)
        guard.root_owned_path.assert_called_once_with(self.root / 'build/launch-host-roots.py')
        read.assert_not_called()
        execute.assert_not_called()

    def test_authenticated_source_ignores_existing_source_and_malicious_pyc(self):
        source = self.root / 'api.py'
        marker = self.root / 'executed-malicious-code'
        source.write_text('from pathlib import Path\nPath(' + repr(str(marker)) + ').touch()\n')
        pyc = Path(py_compile.compile(str(source), doraise=True))
        cached_bytes = pyc.read_bytes()
        api = launch.module(source, b'value = "authenticated held bytes"\n')
        self.assertEqual(api.value, 'authenticated held bytes')
        self.assertEqual(api.__file__, str(source))
        self.assertFalse(marker.exists())
        self.assertEqual(pyc.read_bytes(), cached_bytes)

    def test_ignored_bytecode_cache_blocks_legacy_nested_imports(self):
        for name in ('build', 'configs'):
            (self.root / name).mkdir()
        guard = types.SimpleNamespace(root_owned_path=lambda _: None)
        launch.bytecode_free(self.root, guard)
        (self.root / 'build/__pycache__').mkdir()
        with self.assertRaisesRegex(ValueError, 'bytecode-free'):
            launch.bytecode_free(self.root, guard)
        (self.root / 'build/__pycache__').rmdir()
        (self.root / 'configs/hidden.pyc').write_bytes(b'cached executable')
        with self.assertRaisesRegex(ValueError, 'bytecode-free'):
            launch.bytecode_free(self.root, guard)

    def test_changed_legacy_api_is_rejected_before_shared_or_freeze_execution(self):
        guard = types.SimpleNamespace(root_owned_path=lambda _: None)
        source_reader = types.SimpleNamespace(read_source=lambda _: b'')
        roots_path = self.root / 'build/launch-host-roots.py'

        def read(path):
            return b'changed API' if path.name == 'freeze-builder.py' else b'authenticated API'

        with mock.patch.object(launch, 'bytecode_free'), \
             mock.patch.object(launch, 'trusted_bytes', side_effect=read), \
             mock.patch.object(launch, 'git', return_value=b'authenticated API'), \
             mock.patch.object(launch, 'sources', return_value={'files': {}}), \
             mock.patch.object(launch, 'module', return_value=source_reader) as execute:
            with self.assertRaisesRegex(ValueError, 'API/lock changed'):
                launch.project_apis(self.root, guard)
        execute.assert_called_once_with(roots_path, b'authenticated API')

    def test_tag_cannot_escape_or_exceed_composer_name_limit(self):
        for tag in ('../old', '', 'a;touch', 'a' * 56):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                launch.phase_path(tag)

    def test_admission_includes_logical_sparse_inputs_and_margin(self):
        required = launch.ADMISSION + sum(v['bytes'] for v in launch.INPUTS.values()) + launch.MARGIN
        sample = {'free_bytes': required, 'available_memory_bytes': 3 * 1024**3}
        with self.assertRaises(ValueError):
            launch.resource_guard(sample, initial=True)
        launch.resource_guard(dict(sample, free_bytes=required + 1), initial=True)
        launch.resource_guard(dict(sample, free_bytes=launch.ADMISSION + 1))
        with self.assertRaises(ValueError):
            launch.resource_guard(dict(sample, available_memory_bytes=1024**3 - 1))

    def test_held_rejects_aliases_and_nonregular_inputs(self):
        source, pin = self.fixture()
        alias = self.root / 'alias'
        alias.symlink_to(source)
        with self.assertRaises(OSError):
            launch.Held(alias, pin)
        alias.unlink()
        os.link(source, alias)
        with self.assertRaises(ValueError):
            launch.Held(source, pin)
        pipe = self.root / 'pipe'
        os.mkfifo(pipe)
        with self.assertRaises(ValueError):
            launch.Held(pipe, {'bytes': 0, 'sha256': hashlib.sha256(b'').hexdigest()})

    def test_sparse_copy_has_identical_logical_bytes_without_source_changes(self):
        data = b'a' * launch.CHUNK + bytes(4 * launch.CHUNK) + b'z'
        source, pin = self.fixture(data=data)
        before = launch.identity(source.stat())
        destination = self.root / 'copied'
        with launch.Held(source, pin) as held:
            held.authenticate(destination)
            held.authenticate()
        self.assertEqual(destination.read_bytes(), data)
        self.assertLess(destination.stat().st_blocks * 512, len(data))
        self.assertEqual(launch.identity(source.stat()), before)

    def test_held_detects_replacement_even_with_identical_bytes(self):
        source, pin = self.fixture()
        with launch.Held(source, pin) as held:
            source.unlink()
            source.write_bytes(b'component')
            with self.assertRaises(ValueError):
                held.authenticate()

    def test_wrong_hash_retains_failed_copy(self):
        source, pin = self.fixture()
        destination = self.root / 'partial'
        with launch.Held(source, dict(pin, sha256='0' * 64)) as held:
            with self.assertRaises(ValueError):
                held.authenticate(destination)
        self.assertTrue(destination.exists())

    def test_command_has_one_writable_alias_and_reuses_restricted_namespace(self):
        phase = launch.phase_path('isolated-20261109-01')
        args = launch.command(builder, phase, 'isolated-20261109-01')
        for expected in ('--read-only', '--private-users-ownership=off', '--private-users=1048576:65536',
                         '--private-network', '--no-new-privileges=yes'):
            self.assertIn(expected, args)
        writable = [v for v in args if v.startswith('--bind=')]
        self.assertEqual(writable, ['--bind=' + str(phase / 'output') + ':/a733-sources/out'])
        project = '--bind-ro=' + str(phase / 'project') + ':/a733-sources'
        inputs = '--bind-ro=' + str(phase / 'inputs') + ':/a733-sources/out/.inputs'
        self.assertLess(args.index(project), args.index(writable[0]))
        self.assertLess(args.index(writable[0]), args.index(inputs))
        self.assertIn('firmware-empty', ' '.join(args))
        self.assertIn('--root-profile deterministic-20261109', args[-1])
        self.assertNotIn('--private-users-ownership=chown', args)
        self.assertNotIn('--bind=/dev', ' '.join(args))

    def make_repository(self):
        project = self.root / 'project'
        project.mkdir()
        env = dict(launch.ENV, GIT_AUTHOR_NAME='Fixture', GIT_AUTHOR_EMAIL='fixture@example.invalid',
                   GIT_COMMITTER_NAME='Fixture', GIT_COMMITTER_EMAIL='fixture@example.invalid')
        subprocess.run(['git', 'init', '-q', str(project)], env=env, check=True)
        (project / 'recipe').write_text('unchanged composer fixture\n')
        (project / '.gitignore').write_text('/out/\n')
        subprocess.run(['git', '-C', str(project), 'add', '.'], env=env, check=True)
        subprocess.run(['git', '-C', str(project), 'commit', '-qm', 'Fixture'], env=env, check=True)
        revision = launch.git(project, 'rev-parse', 'HEAD').decode().strip()
        return project, revision

    def test_source_snapshot_binds_outer_commit_and_refuses_dirty_source(self):
        project, revision = self.make_repository()
        with mock.patch.object(launch, 'SOURCE', revision):
            manifest = launch.sources(project, Path.read_bytes)
            self.assertEqual(manifest['revision'], revision)
            (project / 'recipe').write_text('changed')
            with self.assertRaises(ValueError):
                launch.sources(project, Path.read_bytes)

    def test_stage_clones_without_hardlinks_and_keeps_inputs_outside_git(self):
        project, revision = self.make_repository()
        source, pin = self.fixture()
        phase = self.root / 'phase'
        phase.mkdir()
        with mock.patch.object(launch, 'SOURCE', revision), mock.patch.object(launch, 'UID', os.getuid()):
            with mock.patch.object(launch, 'STAGED', {'component': 'roots/component'}):
                manifest = launch.sources(project, Path.read_bytes)
                with launch.Held(source, pin) as held:
                    producer = launch.stage(phase, project, {'component': held}, Path.read_bytes)
                    self.assertEqual(producer['revision'], revision)
                self.assertEqual(launch.sources(phase / 'project', Path.read_bytes), manifest)
        copied = phase / 'inputs/roots/component'
        self.assertEqual(copied.read_bytes(), source.read_bytes())
        self.assertEqual(copied.stat().st_mode & 0o777, 0o400)
        self.assertEqual(copied.stat().st_nlink, 1)
        self.assertNotEqual(copied.stat().st_ino, source.stat().st_ino)
        self.assertEqual((phase / 'project/recipe').stat().st_mode & 0o022, 0)
        self.assertTrue((phase / 'output/.inputs').is_dir())

    def test_wrong_producer_commit_is_refused_before_input_staging(self):
        project, _revision = self.make_repository()
        phase = self.root / 'phase'
        phase.mkdir()
        with self.assertRaises(subprocess.CalledProcessError):
            launch.stage(phase, project, {}, Path.read_bytes)
        self.assertFalse((phase / 'inputs').exists())

    def test_target_pin_mismatch_fails_without_publishing_success(self):
        path = self.root / 'output'
        path.mkdir()
        (path / 'manifest.json').write_text(json.dumps({'files': dict(launch.TARGET, **{'nvme.img': {'bytes': 1}}),
            'root_profile': launch.PROFILE, 'hardware_acceptance': False, 'physical_storage_written': False}))
        with mock.patch.object(launch, 'UID', os.getuid()), self.assertRaises(ValueError):
            launch.verify_target(path, shared, {'files': {}})

    def test_provenance_and_full_verification_required_even_for_matching_target(self):
        path = self.root / 'output'
        path.mkdir()
        result = {'files': launch.TARGET, 'root_profile': launch.PROFILE,
                  'hardware_acceptance': False, 'physical_storage_written': False, 'sources': {}}
        (path / 'manifest.json').write_text(json.dumps(result))
        with mock.patch.object(launch, 'UID', os.getuid()), self.assertRaises(ValueError):
            launch.verify_target(path, shared, {'files': {}})

    def test_matching_manifest_does_not_waive_encoded_file_hash(self):
        path = self.root / 'output'
        path.mkdir()
        (path / 'nvme.img.gz').write_bytes(b'wrong')
        target = {'nvme.img': launch.pin(b'raw fixture'), 'nvme.img.gz': launch.pin(b'right')}
        recipe = launch.pin(b'recipe')
        result = {'files': target, 'root_profile': launch.PROFILE,
                  'hardware_acceptance': False, 'physical_storage_written': False,
                  'sources': {'build/composer': recipe},
                  'derivation': {key: True for key in ('base_crc_eof_raw_hash_verified', 'both_gpt_tables_verified',
                       'output_gzip_full_expansion_verified', 'generated_single_member_wire_verified',
                       'all_inputs_held_reauthenticated')}, 'tool_versions': {}}
        (path / 'manifest.json').write_text(json.dumps(result))
        with mock.patch.object(launch, 'UID', os.getuid()), mock.patch.object(launch, 'TARGET', target):
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                launch.verify_target(path, shared, {'files': {'build/composer': recipe}})

    def test_failure_retains_phase_checks_inputs_and_does_not_clean_unknown_resources(self):
        source, pin = self.fixture()
        fixed_inputs = {'component': dict(pin, path=str(source))}
        historical = b'{}'
        lock = {'source_namespace_manifest': launch.pin(historical)}
        manifest = {'revision': launch.SOURCE, 'files': {}}
        root_api = types.SimpleNamespace(read_source=lambda path: json.dumps(lock).encode() if path.name.endswith('.json') else b'lock')
        shared_api = types.SimpleNamespace(INVENTORY=['fixture-inventory'],
            validate_host_inventory=lambda *_: None, regular_bytes=lambda *_args, **_kw: historical,
            interrupted=shared.interrupted, stop_process=mock.Mock(), mapped_processes=lambda: [999999],
            phase_mounts=lambda _: ['unknown-existing-mount'],
            resource_sample=lambda: {'free_bytes': 4000000000, 'available_memory_bytes': 2000000000},
            atomic_json=lambda path, value: path.write_text(json.dumps(value)))
        freeze = types.SimpleNamespace(snapshot=lambda _: ([], b'{}'),
            validate_namespace_source=lambda *_: None, producer=types.SimpleNamespace(host_state=lambda: {}))

        def failing_stage(phase, _project, held, *_args):
            destination = phase / 'inputs/component'
            destination.parent.mkdir()
            held['component'].authenticate(destination)
            raise RuntimeError('deliberate staging failure')

        with mock.patch.object(launch, 'BASE', self.root), mock.patch.object(launch, 'INPUTS', fixed_inputs), \
             mock.patch.object(launch, 'STAGED', {'component': 'component'}), \
             mock.patch.object(launch, 'preflight', return_value=(None, root_api, shared_api, freeze, manifest)), \
             mock.patch.object(launch, 'runtime_anchors', return_value={}), \
             mock.patch.object(launch, 'sources', return_value=manifest), \
             mock.patch.object(launch, 'stage', side_effect=failing_stage), \
             mock.patch.object(launch.subprocess, 'check_output', return_value=b'inventory'):
            with self.assertRaisesRegex(RuntimeError, 'deliberate staging failure'):
                launch.execute(self.root, 'failed')
        receipt = json.loads((self.root / 'isolated-nvme-failed/receipt.json').read_text())
        self.assertFalse(receipt['success'])
        self.assertTrue(receipt['held_and_staged_inputs_reauthenticated'])
        self.assertEqual(receipt['mapped_processes_final'], [999999])
        self.assertEqual(receipt['phase_mounts_final'], ['unknown-existing-mount'])
        self.assertTrue((self.root / 'isolated-nvme-failed/inputs/component').exists())
        self.assertEqual(source.read_bytes(), b'component')
        shared_api.stop_process.assert_called_with(None)


if __name__ == '__main__':
    unittest.main()
