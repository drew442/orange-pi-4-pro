"""Offline guards and simulated replay; no compilation or hardware actions."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import sys
from io import StringIO
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('kernel_replay', Path(__file__).resolve().parents[1] / 'build/kernel-replay.py')
replay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(replay)


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo, self.workspace = self.root / 'repo', self.root / 'cache'
        (self.repo / 'out').mkdir(parents=True)
        (self.workspace / 'artifacts').mkdir(parents=True)
        self.output = self.repo / 'out/kernel-replay-case'

    def test_output_existing_symlink_traversal_overlap_and_make_expansion(self):
        replay.validate_output(self.repo, self.workspace, self.output)
        for output in (self.repo / 'out/other', self.repo / 'out/kernel-replay-$(id)',
                       self.repo / 'out/../kernel-replay-case', self.root / 'kernel-replay-case'):
            with self.subTest(output=output), self.assertRaises(ValueError):
                replay.validate_output(self.repo, self.workspace, output)
        link = self.repo / 'out/kernel-replay-link'
        link.symlink_to(self.workspace)
        with self.assertRaises(ValueError):
            replay.validate_output(self.repo, self.workspace, link)
        with self.assertRaises(ValueError):
            replay.validate_output(self.repo, self.repo, self.output)
        self.output.mkdir()
        with self.assertRaises(ValueError):
            replay.validate_output(self.repo, self.workspace, self.output)

    def test_environment_ignores_ambient_build_overrides(self):
        with patch.dict(os.environ, {'MAKEFLAGS': '-j99', 'CC': '/evil', 'GIT_DIR': '/evil',
                                     'CPATH': '/evil', 'PATH': '/evil', 'DPKG_ADMINDIR': '/evil', 'KBUILD_BUILD_USER': 'other'}):
            env = replay.environment()
        self.assertEqual(env['KBUILD_BUILD_USER'], 'builder')
        self.assertEqual(env['SOURCE_DATE_EPOCH'], '1793750400')
        self.assertEqual(env['PATH'], '/usr/bin:/bin')
        for name in ('MAKEFLAGS', 'CC', 'GIT_DIR', 'CPATH', 'DPKG_ADMINDIR'):
            self.assertNotIn(name, env)

    def test_git_trust_is_invocation_local_exact_and_path_validated(self):
        with patch.object(replay, 'run', return_value=b'ok') as execute:
            self.assertEqual(replay.git(self.workspace, 'status', '--porcelain'), b'ok')
        command = execute.call_args.args[0]
        self.assertEqual(command.count(f'safe.directory={self.workspace}'), 1)
        self.assertNotIn('safe.directory=*', command)
        self.assertEqual(command[command.index('-C') + 1], self.workspace)
        alias = self.root / 'cache-alias'
        alias.symlink_to(self.workspace)
        with patch.object(replay, 'run') as execute, self.assertRaisesRegex(ValueError, 'Symlink'):
            replay.git(alias, 'status')
        execute.assert_not_called()

    def test_start_resources_thresholds(self):
        good = {'free_disk_bytes': {'output': 15_000_000_001}, 'available_ram_bytes': 3_000_000_000}
        for sample, accepted in ((good, True), ({**good, 'free_disk_bytes': {'out': 15_000_000_000}}, False),
                                 ({**good, 'available_ram_bytes': 2_999_999_999}, False)):
            with self.subTest(sample=sample), patch.object(replay, 'resources', return_value=sample):
                if accepted:
                    self.assertEqual(replay.initial_resources((self.root,)), good)
                else:
                    with self.assertRaises(ValueError):
                        replay.initial_resources((self.root,))

    def test_monitor_low_disk_refuses_launch_and_logs_final_sample(self):
        from io import StringIO
        log = StringIO()
        monitor = replay.Monitor((self.root,), log)
        with patch.object(replay, 'resources', return_value={'free_disk_bytes': {'out': 2_000_000_000}}):
            monitor.sample('end')
        with patch.object(replay.subprocess, 'Popen') as launch, self.assertRaisesRegex(ValueError, 'below 3 GB'):
            monitor.command(['make'], self.root, None)
        launch.assert_not_called()
        self.assertEqual(json.loads(log.getvalue())['phase'], 'end')

    def test_modules_sorted_bytes_modes_and_only_generated_links_removed(self):
        modules = self.root / 'modules'
        release = modules / 'lib/modules/version'
        release.mkdir(parents=True)
        (release / 'z.ko').write_bytes(b'z')
        (release / 'a.ko').write_bytes(b'a')
        (release / 'a.ko').chmod(0o640)
        for name in ('source', 'build'):
            (release / name).symlink_to('/nonexistent')
        replay.remove_generated_links(modules)
        inventory = replay.module_inventory(modules)
        self.assertEqual([entry['path'] for entry in inventory], ['lib/modules/version/a.ko', 'lib/modules/version/z.ko'])
        self.assertEqual(inventory[0]['mode'], 0o640)
        (release / 'other').symlink_to('/nonexistent')
        with self.assertRaisesRegex(ValueError, 'symlink'):
            replay.module_inventory(modules)
        (release / 'source').write_text('retain this unexpected input')
        with self.assertRaisesRegex(ValueError, 'Unexpected generated link type'):
            replay.remove_generated_links(modules)
        self.assertTrue((release / 'source').exists())

    def project(self):
        subprocess.run(['git', 'init', '-q', self.repo], check=True)
        (self.repo / '.gitignore').write_text('out/\n')
        for relative in replay.PROJECT_INPUTS:
            path = self.repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('committed ' + relative)
        subprocess.run(['git', '-C', self.repo, 'add', '.'], check=True)
        subprocess.run(['git', '-C', self.repo, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                        'commit', '-qm', 'Fixture'], check=True)

    def test_project_head_binds_actual_helper_even_when_index_hides_change(self):
        self.project()
        record = replay.project_source(self.repo)
        self.assertIn('build/uboot-replay.py', record['inputs'])
        subprocess.run(['git', '-C', self.repo, 'update-index', '--assume-unchanged', 'build/uboot-replay.py'], check=True)
        (self.repo / 'build/uboot-replay.py').write_text('hidden alteration')
        with self.assertRaisesRegex(ValueError, 'differs from HEAD'):
            replay.project_source(self.repo)

    def test_exact_git_trust_handles_other_owner_without_trusting_unrelated_tree(self):
        self.project()
        env = dict(replay.environment(), GIT_TEST_ASSUME_DIFFERENT_OWNER='1')
        private = self.root / 'private-owner-test'
        with patch.object(replay, 'environment', return_value=env):
            with self.assertRaises(subprocess.CalledProcessError):
                replay.run(['git', '-C', self.repo, 'status', '--porcelain'])
            self.assertEqual(replay.git(self.repo, 'status', '--porcelain'), b'')
            replay.run(replay.git_command(self.repo, 'clone', '--shared', '--no-checkout', self.repo, private))
            # Invocation-local trust must not persist for another repository.
            with self.assertRaises(subprocess.CalledProcessError):
                replay.run(['git', '-C', private, 'status', '--porcelain'])
            replay.git(private, 'checkout', '--detach', 'HEAD')
            self.assertEqual(replay.git(private, 'status', '--porcelain'), b'')

    def test_project_dirty_and_untracked_refused(self):
        self.project()
        (self.repo / 'untracked').write_text('not committed')
        with self.assertRaisesRegex(ValueError, 'clean and committed'):
            replay.project_source(self.repo)

    def test_shallow_cached_source_and_private_shared_clone_preserve_state(self):
        original = self.root / 'source'
        subprocess.run(['git', 'init', '-q', original], check=True)
        for content in ('first', 'second'):
            (original / 'input').write_text(content)
            subprocess.run(['git', '-C', original, 'add', '.'], check=True)
            subprocess.run(['git', '-C', original, '-c', 'user.name=Test', '-c',
                            'user.email=test@example.invalid', 'commit', '-qm', content], check=True)
        head = replay.git(original, 'rev-parse', 'HEAD').decode().strip()
        cache, private = self.root / 'shallow', self.root / 'private'
        replay.run(['git', 'clone', '-q', '--depth=1', original.as_uri(), cache])
        before = replay.source_state(cache, head)
        self.assertTrue(before['shallow'])
        replay.run(replay.git_command(cache, 'clone', '-q', '--shared', '--no-checkout', cache, private))
        replay.git(private, 'checkout', '-q', '--detach', head)
        self.assertEqual(replay.source_state(private, head), before)
        self.assertEqual(replay.source_state(cache, head), before)
        (cache / 'input').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'must be clean'):
            replay.source_state(cache, head)
        with self.assertRaisesRegex(ValueError, 'Unpinned'):
            replay.source_state(private, '0' * 40)

    def test_running_command_cancels_term_ignoring_descendants(self):
        monitor = replay.Monitor((self.root,), StringIO())
        child_pid_file = self.root / 'descendant.pid'

        def cancel_when_child_ready():
            deadline = time.monotonic() + 5
            while not child_pid_file.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            monitor.reason = 'test low disk'
            monitor.failed.set()

        canceller = threading.Thread(target=cancel_when_child_ready)
        canceller.start()
        self.addCleanup(canceller.join)
        child = ('import signal, time, os; from pathlib import Path; '
                 'signal.signal(signal.SIGTERM, signal.SIG_IGN); '
                 f'Path({str(child_pid_file)!r}).write_text(str(os.getpid())); time.sleep(60)')
        parent = f'import subprocess, sys, time; subprocess.Popen([sys.executable, "-c", {child!r}]); time.sleep(60)'
        with (self.root / 'child.log').open('wb') as log, self.assertRaisesRegex(ValueError, 'test low disk'):
            monitor.command([sys.executable, '-c', parent], self.root, log)
        descendant = int(child_pid_file.read_text())
        process_stat = Path(f'/proc/{descendant}/stat')
        deadline = time.monotonic() + 1
        while process_stat.exists() and process_stat.read_text().split()[2] != 'Z' and time.monotonic() < deadline:
            time.sleep(0.01)
        if process_stat.exists():
            # An orphan zombie can await init reaping; no descendant may run.
            self.assertEqual(process_stat.read_text().split()[2], 'Z')
        with self.assertRaisesRegex(InterruptedError, 'signal'):
            replay.interrupted(15, None)

    def simulated_build(self, mismatch=False, changed_after=False, interrupted=False,
                        debug_path_profile='native', missing_map=False):
        config = b'CONFIG_KVM=y\n'
        products = {'Image': b'kernel', 'board.dtb': b'dtb', 'kernel.config': config}
        for name, content in products.items():
            (self.workspace / 'artifacts' / name).write_bytes(content)
        for relative in replay.PROJECT_INPUTS:
            path = self.repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(relative)
        before = {'kernel': {'commit': replay.KERNEL_HEAD}, 'vendor': {'commit': replay.VENDOR_HEAD}, 'targets': {name: replay.facts(self.workspace / 'artifacts' / name) for name in products},
                  'original_modules_inventory': None,
                  'project': {'commit': 'a' * 40, 'inputs': {name: replay.facts(self.repo / name) for name in replay.PROJECT_INPUTS}}}
        commands = []

        def command(_monitor, args, cwd, log):
            commands.append([str(arg) for arg in args])
            if 'clone' in args:
                Path(args[-1]).mkdir()
            elif 'olddefconfig' in args:
                (cwd / '.config').write_bytes(config)
            elif 'Image' in args:
                if interrupted:
                    replay.interrupted(15, None)
                for name, relative in replay.PRODUCTS.items():
                    path = cwd / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b'mismatch' if mismatch and name == 'Image' else products[name])
                for relative in ('include/generated', 'include/config', 'arch/arm64/include/generated'):
                    (cwd / relative).mkdir(parents=True, exist_ok=True)
                    (cwd / relative / 'fixture.h').write_text('generated header')
                (cwd / 'include/config/kernel.release').write_text('6.6.98-a733-diag\n')
                flags = dict(arg.split('=', 1) for arg in args if '=' in str(arg))
                for relative, kind in (('arch/arm64/kernel/vdso/.vgettimeofday.o.cmd', 'KCFLAGS'),
                                       ('arch/arm64/kernel/vdso/.note.o.cmd', 'KAFLAGS'),
                                       ('arch/arm64/kernel/vdso/.sigreturn.o.cmd', 'KAFLAGS'),
                                       ('init/.main.o.cmd', 'KCFLAGS')):
                    path = cwd / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    observed = '' if missing_map == kind or (missing_map is True and kind == 'KAFLAGS') else flags.get(kind, '')
                    path.write_text(f'cmd_fixture := aarch64-linux-gnu-gcc -g {observed} -c source\n')
            elif 'modules_install' in args:
                root = Path(next(arg.split('=', 1)[1] for arg in args if str(arg).startswith('INSTALL_MOD_PATH=')))
                release = root / 'lib/modules/6.6.98-a733-diag'
                release.mkdir(parents=True)
                (release / 'test.ko').write_bytes(b'module')
                (release / 'build').symlink_to(cwd)
                (release / 'source').symlink_to(cwd)

        after = {**before, 'changed': True} if changed_after else before
        with patch.object(replay.Monitor, 'command', command), \
                patch.object(replay, 'source_state', return_value={'commit': replay.KERNEL_HEAD}), \
                patch.object(replay, 'snapshot', return_value=(after, config)):
            if mismatch or changed_after or interrupted or missing_map:
                with self.assertRaises((ValueError, InterruptedError)):
                    replay.build(self.repo, self.workspace, self.output, before, config, debug_path_profile)
            else:
                replay.build(self.repo, self.workspace, self.output, before, config, debug_path_profile)
        return commands, json.loads((self.output / 'artifacts/replay-result.json').read_text())

    def test_simulated_replay_matches_registered_products_and_preserves_inputs(self):
        commands, result = self.simulated_build()
        self.assertTrue(result['success'])
        self.assertTrue(result['shared_inputs_unchanged'])
        self.assertIsNone(result['modules_match_unanchored_retained_baseline'])
        self.assertIn('--shared', commands[0])
        self.assertIn(f'safe.directory={self.workspace / "kernel"}', commands[0])
        self.assertIn(f'safe.directory={self.output / "kernel-source"}', commands[1])
        self.assertTrue(all('LOCALVERSION=-a733-diag' in command for command in commands if 'Image' in command or 'modules_install' in command))
        self.assertEqual((self.workspace / 'artifacts/Image').read_bytes(), b'kernel')
        samples = [json.loads(line) for line in (self.output / 'logs/resources.jsonl').read_text().splitlines()]
        self.assertEqual((samples[0]['phase'], samples[-1]['phase']), ('start', 'end'))
        self.assertTrue((self.output / 'replay-manifest.json').exists())

    def test_mismatch_retains_actual_bytes_comparison_and_failure(self):
        _, result = self.simulated_build(mismatch=True)
        self.assertFalse(result['success'])
        self.assertFalse(result['comparisons']['Image']['equal'])
        self.assertEqual((self.output / 'artifacts/Image').read_bytes(), b'mismatch')
        self.assertTrue((self.output / 'kernel-source').exists())

    def test_postbuild_shared_input_mutation_prevents_success(self):
        _, result = self.simulated_build(changed_after=True)
        self.assertFalse(result['success'])
        self.assertIn('changed during replay', result['postcheck_error'])

    def test_signal_interruption_retains_failure_result_and_manifest(self):
        _, result = self.simulated_build(interrupted=True)
        self.assertFalse(result['success'])
        self.assertIn('InterruptedError', result['error'])
        self.assertTrue((self.output / 'replay-manifest.json').exists())
        samples = [json.loads(line) for line in (self.output / 'logs/resources.jsonl').read_text().splitlines()]
        self.assertEqual(samples[-1]['phase'], 'end')

    def test_native_profile_has_no_mapping_and_preserves_caller_umask(self):
        caller = os.umask(0o027)
        try:
            commands, result = self.simulated_build()
            observed = os.umask(0o027)
            self.assertEqual(observed, 0o027)
        finally:
            os.umask(caller)
        self.assertEqual(result['build_settings']['debug_path_profile'], 'native')
        self.assertEqual(result['build_settings']['caller_umask'], '0027')
        self.assertEqual((result['umask_before'], result['umask_after']), ('0022', '0022'))
        self.assertTrue(all(not any(arg.startswith(('KCFLAGS=', 'KAFLAGS=')) for arg in command)
                            for command in commands))
        self.assertEqual(len(result['actual_commands']), 4)
        self.assertEqual((self.output / 'artifacts/modules/lib/modules/6.6.98-a733-diag/test.ko').stat().st_mode & 0o777, 0o644)

    def test_mapped_profile_flags_apply_to_all_make_stages_and_actual_commands(self):
        commands, result = self.simulated_build(debug_path_profile='original-20261104')
        settings = result['build_settings']
        mapping = f'{self.output / "kernel-source"}={self.workspace / "kernel"}'
        self.assertEqual(settings['kcflags'], '-fdebug-prefix-map=' + mapping)
        self.assertEqual(settings['kaflags'], '-Wa,--debug-prefix-map=' + mapping)
        stages = [command for command in commands if command[0] == 'make']
        self.assertEqual(len(stages), 3)
        for command in stages:
            self.assertIn('KCFLAGS=' + settings['kcflags'], command)
            self.assertIn('KAFLAGS=' + settings['kaflags'], command)
            self.assertIn('-j2', command)
        self.assertEqual((result['umask_before'], result['umask_after']), ('0002', '0002'))
        self.assertEqual((self.output / 'artifacts/modules/lib/modules/6.6.98-a733-diag/test.ko').stat().st_mode & 0o777, 0o664)
        self.assertEqual(result['build_environment_before'], result['build_environment_after'])
        self.assertEqual(len(result['actual_commands']), 4)
        self.assertTrue((self.output / 'artifacts/generated/include/generated/fixture.h').is_file())
        self.assertTrue((self.output / 'artifacts/generated/arch/arm64/include/generated/fixture.h').is_file())

    def test_missing_actual_assembler_map_fails_with_metadata_and_restores_umask(self):
        caller = os.umask(0o077)
        try:
            _, result = self.simulated_build(debug_path_profile='original-20261104', missing_map=True)
            observed = os.umask(0o077)
            self.assertEqual(observed, 0o077)
        finally:
            os.umask(caller)
        self.assertFalse(result['success'])
        self.assertIn('lacks explicit debug-path mapping', result['error'])
        self.assertEqual(result['build_settings']['debug_path_profile'], 'original-20261104')
        self.assertEqual(result['umask_after'], '0002')
        self.assertTrue((self.output / 'artifacts/commands/arch/arm64/kernel/vdso/.note.o.cmd').exists())

    def test_missing_actual_compiler_map_also_fails(self):
        _, result = self.simulated_build(debug_path_profile='original-20261104', missing_map='KCFLAGS')
        self.assertFalse(result['success'])
        self.assertIn('.vgettimeofday.o.cmd', result['error'])

    def test_cli_mapping_is_explicit_opt_in(self):
        for options, profile in (([], 'native'), (['--debug-path-profile', 'original-20261104'], 'original-20261104')):
            argv = ['kernel-replay.py', '--target', replay.TARGET, '--builder-profile', replay.PROFILE,
                    '--output', str(self.output), *options]
            with patch.object(sys, 'argv', argv), patch.object(replay, 'validate_output'), \
                    patch.object(replay, 'initial_resources'), patch.object(replay, 'snapshot', return_value=({}, b'')), \
                    patch.object(replay.signal, 'signal'), patch.object(replay, 'build') as build, \
                    patch('builtins.print'):
                self.assertEqual(replay.main(), 0)
            self.assertEqual(build.call_args.args[-1], profile)

    def test_umask_restored_if_output_initialization_fails(self):
        self.output.mkdir()
        caller = os.umask(0o027)
        try:
            with self.assertRaises(FileExistsError):
                replay.build(self.repo, self.workspace, self.output, {}, b'', 'original-20261104')
            self.assertEqual(os.umask(0o027), 0o027)
        finally:
            os.umask(caller)

    def test_cli_explicit_profile_required(self):
        base = ['python3', replay.__file__, '--target', replay.TARGET, '--output', self.output]
        for options in ([], ['--builder-profile', 'original-20261104']):
            result = subprocess.run(base + options, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b'--builder-profile', result.stderr)


if __name__ == '__main__':
    unittest.main()
