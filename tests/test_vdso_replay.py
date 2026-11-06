"""Offline vDSO experiment checks; no kernel compilation or hardware actions."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('vdso_replay', Path(__file__).resolve().parents[1] / 'build/vdso-replay.py')
replay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(replay)


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo, self.workspace = self.root / 'repo', self.root / 'cache'
        (self.repo / 'out').mkdir(parents=True)
        self.workspace.mkdir()
        self.output = self.repo / 'out/vdso-replay-test'

    def test_output_guards(self):
        replay.validate_output(self.repo, self.workspace, self.output)
        for path in (self.repo / 'out/vdso-replay-$(id)', self.repo / 'out/vdso-replay-',
                     self.repo / 'out/../vdso-replay-test', self.root / 'vdso-replay-test'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                replay.validate_output(self.repo, self.workspace, path)
        self.output.symlink_to(self.workspace)
        with self.assertRaises(ValueError):
            replay.validate_output(self.repo, self.workspace, self.output)

    def test_complete_embedding_authentication_rejects_missing_duplicate_or_tampered_targets(self):
        image, vdso = self.root / 'Image', self.root / 'vdso.so'
        vdso.write_bytes(b'ELF-complete-body-and-build-id')
        for contents, offset, accepted in ((b'prefix' + vdso.read_bytes() + b'end', 6, True),
                                            (vdso.read_bytes() * 2, 0, False), (b'ELF-build-id', 0, False)):
            image.write_bytes(contents)
            expected = replay.facts(image)
            if accepted:
                result = replay.authenticate_embedding(image, vdso, expected, replay.facts(vdso), offset)
                self.assertEqual(result['offset'], 6)
            else:
                with self.assertRaisesRegex(ValueError, 'exactly once'):
                    replay.authenticate_embedding(image, vdso, expected, replay.facts(vdso), offset)
        with self.assertRaises(ValueError):
            replay.authenticate_embedding(image, vdso, expected, {'bytes': 1, 'sha256': '0' * 64}, 0)

    def test_vdso_only_resource_guard_does_not_relax_full_kernel_guard(self):
        sample = {'free_disk_bytes': {'out': 4_000_000_000}, 'available_ram_bytes': 3_000_000_000}
        with patch.object(replay.kernel, 'resources', return_value=sample):
            self.assertEqual(replay.initial_resources((self.root,)), sample)
            with self.assertRaises(ValueError):
                replay.kernel.initial_resources((self.root,))
        for bad in ({**sample, 'available_ram_bytes': 2_999_999_999},
                    {**sample, 'free_disk_bytes': {'out': 3_999_999_999}}):
            with patch.object(replay.kernel, 'resources', return_value=bad), self.assertRaises(ValueError):
                replay.initial_resources((self.root,))

    def test_actual_command_capture_requires_compiler_and_assembler_flags(self):
        tree = self.output / 'kernel-source'
        root = tree / replay.VDSO
        root.mkdir(parents=True)
        make = replay.make_command(tree, self.workspace)
        self.assertIn('KCFLAGS=-fdebug-prefix-map=' + str(tree) + '=' + str(self.workspace / 'kernel'), make)
        self.assertIn('KAFLAGS=-Wa,--debug-prefix-map=' + str(tree) + '=' + str(self.workspace / 'kernel'), make)
        artifacts = self.root / 'artifacts'
        artifacts.mkdir()
        flags = {'vgettimeofday': make[-2].split('=', 1)[1],
                 'note': make[-1].split('=', 1)[1],
                 'sigreturn': make[-1].split('=', 1)[1]}
        for missing in flags:
            for name, flag in flags.items():
                (root / f'.{name}.o.cmd').write_text('compiler missing map' if name == missing else flag)
            with self.subTest(missing=missing), self.assertRaisesRegex(ValueError, 'Actual ' + missing):
                replay.capture_generated(tree, artifacts, make)
        for name, flag in flags.items():
            (root / f'.{name}.o.cmd').write_text(flag)
        self.assertEqual(set(replay.capture_generated(tree, artifacts, make)), set(flags))

    def simulate(self, mismatch=False, changed=False, interrupted=False):
        config, target = b'CONFIG_KVM=y\n', b'ELF-header-build-id-complete-body'
        source = self.workspace / 'kernel' / replay.VDSO
        source.mkdir(parents=True)
        (source / 'vdso.so').write_bytes(target)
        (source / 'vdso.so.dbg').write_bytes(b'debug-original')
        before = {'project': {'commit': 'a' * 40, 'inputs': {}}, 'kernel': {'shallow': True}, 'vendor': {},
                  'vdso_embedding': {'vdso': replay.facts(source / 'vdso.so')},
                  'optional_debug_unanchored': replay.facts(source / 'vdso.so.dbg'),
                  'targets': {'kernel.config': {'bytes': len(config), 'sha256': __import__('hashlib').sha256(config).hexdigest()}}}
        commands = []

        def command(_monitor, args, cwd, log):
            commands.append([str(arg) for arg in args])
            if 'clone' in args:
                Path(args[-1]).mkdir()
            elif 'prepare' in args:
                if interrupted:
                    raise InterruptedError('test cancellation')
                root = cwd / replay.VDSO
                root.mkdir(parents=True)
                (root / 'vdso.so').write_bytes(target[:-1] + b'X' if mismatch else target)
                # Debug equality is deliberately not required for acceptance.
                (root / 'vdso.so.dbg').write_bytes(b'debug-different')
                for name, flag in (('vgettimeofday', args[-3]), ('note', args[-2]), ('sigreturn', args[-2])):
                    (root / f'.{name}.o.cmd').write_text(flag.split('=', 1)[1])
                generated = cwd / 'include/config'
                generated.mkdir(parents=True)
                (generated / 'kernel.release').write_text('6.6.98-a733-diag\n')
        old = os.umask(0o077)
        try:
            after = {**before, 'changed': True} if changed else before
            with patch.object(replay.kernel.Monitor, 'command', command), \
                    patch.object(replay.kernel, 'source_state', return_value=before['kernel']), \
                    patch.object(replay, 'snapshot', return_value=(after, config)):
                if mismatch or changed or interrupted:
                    with self.assertRaises((ValueError, InterruptedError)):
                        replay.build(self.repo, self.workspace, self.output, before, config)
                else:
                    replay.build(self.repo, self.workspace, self.output, before, config)
            self.assertEqual(os.umask(0o077), 0o077)
        finally:
            os.umask(old)
        return commands, json.loads((self.output / 'artifacts/replay-result.json').read_text())

    def test_fresh_clone_prepare_only_full_byte_match_and_debug_unanchored(self):
        commands, result = self.simulate()
        self.assertTrue(result['success'])
        self.assertFalse(result['debug_comparison_unanchored']['equal'])
        self.assertEqual(result['umask'], '0002')
        self.assertIn('--shared', commands[0])
        self.assertEqual([cmd[-1] for cmd in commands if cmd[0] == 'make'], ['olddefconfig', 'prepare'])
        self.assertTrue((self.output / 'artifacts/commands/.note.o.cmd').is_file())
        self.assertTrue((self.output / 'replay-manifest.json').is_file())

    def test_equal_build_id_but_different_complete_body_fails_and_retains_bytes(self):
        _, result = self.simulate(mismatch=True)
        self.assertFalse(result['success'])
        self.assertFalse(result['comparison']['equal'])
        self.assertTrue((self.output / 'artifacts/vdso.so').read_bytes().endswith(b'X'))

    def test_postcheck_mutation_fails(self):
        _, result = self.simulate(changed=True)
        self.assertFalse(result['success'])
        self.assertIn('Shared inputs changed', result['postcheck_error'])

    def test_interruption_preserves_failure_and_restores_umask(self):
        _, result = self.simulate(interrupted=True)
        self.assertFalse(result['success'])
        self.assertIn('InterruptedError', result['error'])
        self.assertTrue((self.output / 'kernel-source').is_dir())
        self.assertTrue((self.output / 'replay-manifest.json').exists())

    def test_recipe_head_binding_detects_index_hidden_change(self):
        subprocess.run(['git', 'init', '-q', self.repo], check=True)
        for relative in replay.OWN_INPUTS:
            path = self.repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('committed recipe input')
        subprocess.run(['git', '-C', self.repo, 'add', 'build', 'tests'], check=True)
        subprocess.run(['git', '-C', self.repo, '-c', 'user.name=Test', '-c',
                        'user.email=test@example.invalid', 'commit', '-qm', 'Fixture'], check=True)
        head = replay.kernel.git(self.repo, 'rev-parse', 'HEAD').decode().strip()
        before = {'project': {'commit': head, 'inputs': {}}, 'kernel': {'shallow': True},
                  'targets': {'Image': {}}}
        with patch.object(replay.kernel, 'snapshot', return_value=(before, b'config')), \
                patch.object(replay, 'authenticate_embedding', return_value={'offset': 1}):
            record, _ = replay.snapshot(self.repo, self.workspace)
        self.assertIn('build/vdso-replay.py', record['project']['inputs'])
        subprocess.run(['git', '-C', self.repo, 'update-index', '--assume-unchanged',
                        'build/vdso-replay.py'], check=True)
        (self.repo / 'build/vdso-replay.py').write_text('hidden mutation')
        with patch.object(replay.kernel, 'snapshot', return_value=(before, b'config')), \
                self.assertRaisesRegex(ValueError, 'differs from HEAD'):
            replay.snapshot(self.repo, self.workspace)

    def test_cli_requires_registered_profile(self):
        command = ['python3', replay.__file__, '--target', replay.TARGET, '--output', self.output]
        result = subprocess.run(command, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'--builder-profile', result.stderr)


if __name__ == '__main__':
    unittest.main()
