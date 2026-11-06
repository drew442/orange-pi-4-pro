"""Host-only replay guards; these tests execute no vendor tools or builds."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('uboot_replay', Path(__file__).resolve().parents[1] / 'build/uboot-replay.py')
replay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(replay)


def challenge():
    rows = ['  ' + ', '.join(f'0x{byte:02x}' for byte in range(row * 8, row * 8 + 8)) +
            (',' if row < 15 else '') for row in range(16)]
    return ('__attribute__((__used__))\nunsigned char sunxi_challenge[] = {\n' + '\n'.join(rows) +
            '\n};\n__attribute__((__used__))\nunsigned int sunxi_challenge_len = 128;\n').encode()


class ChallengeTests(unittest.TestCase):
    def test_valid_exact_data(self):
        self.assertEqual(replay.validate_challenge(challenge()), bytes(range(128)))

    def test_reject_bad_c(self):
        good = challenge()
        for bad in (good[:-1], good + b' ', good + b'#include <stdio.h>\n',
                    good.replace(b' = 128;', b' = 127;'),
                    good.replace(b'unsigned char', b'volatile char'),
                    good.replace(b'0x00', b'0xgg'), good.replace(b'0x00', b'0X00'),
                    good.replace(b'0x00', b'1234'), good.replace(b'[]', b'[0]')):
            with self.subTest(bad=bad[:80]), self.assertRaises((ValueError, UnicodeError)):
                replay.validate_challenge(bad)

    def test_generator_default_branch_preserved(self):
        original = 'before\n' + replay.GENERATOR + 'after\n'
        modified = replay.patch_generator(original)
        self.assertEqual(modified.split('else\n', 1)[1].split('endif\n', 1)[0],
                         replay.GENERATOR[replay.GENERATOR.index('\t@dd if='):replay.GENERATOR.index('prepare:')])
        self.assertIn('ifneq ($(A733_REPLAY_CHALLENGE),)', modified)
        self.assertEqual(modified, 'before\n' + replay.REPLAY_GENERATOR + 'after\n')

    def test_fail_unexpected_generator_context(self):
        for source in ('', replay.GENERATOR * 2, replay.GENERATOR.replace('bs=128', 'bs=64'),
                       replay.GENERATOR + 'A733_REPLAY_CHALLENGE'):
            with self.subTest(source=source), self.assertRaises(ValueError):
                replay.patch_generator(source)


class FileGuards(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / 'repo'
        (self.repo / 'out').mkdir(parents=True)
        self.workspace = self.root / 'workspace'
        self.workspace.mkdir()
        self.provenance = self.root / 'provenance'
        self.provenance.mkdir()
        self.output = self.repo / 'out/uboot-replay-test'

    def test_valid_output(self):
        replay.validate_output(self.repo, self.workspace, self.provenance, self.output)

    def test_unsafe_paths(self):
        link = self.root / 'alias'
        link.symlink_to(self.workspace, target_is_directory=True)
        for path in (Path('relative'), self.root / '..' / 'escape', Path('/dev/null'),
                     Path('/proc/self/status'), Path('/sys'), link / 'file'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                replay.safe_path(path)

    def test_nonregular_files_rejected(self):
        import os
        fifo = self.root / 'fifo'
        os.mkfifo(fifo)
        with self.assertRaises(ValueError):
            replay.read_regular(fifo)
        with self.assertRaises(ValueError):
            replay.facts(fifo)

    def test_output_rules(self):
        for output in (self.root / 'uboot-replay-test', self.repo / 'out/wrong-name',
                       self.repo / 'out/uboot-replay-$(id)', self.repo / 'out/uboot-replay-'):
            with self.subTest(output=output), self.assertRaises(ValueError):
                replay.validate_output(self.repo, self.workspace, self.provenance, output)
        self.output.mkdir()
        with self.assertRaises(ValueError):
            replay.validate_output(self.repo, self.workspace, self.provenance, self.output)

    def test_overlap(self):
        with self.assertRaises(ValueError):
            replay.validate_output(self.repo, self.repo, self.provenance, self.output)

    def test_authenticate_detects_changes(self):
        path = self.root / 'input'
        path.write_bytes(b'authenticated')
        expected = replay.facts(path)
        replay.authenticate(path, expected)
        path.write_bytes(b'mutated')
        with self.assertRaises(ValueError):
            replay.authenticate(path, expected)

    def test_mutation_during_hash_rejected(self):
        path = self.root / 'input'
        path.write_bytes(b'input bytes')
        original = path.stat()
        before = SimpleNamespace(st_mode=original.st_mode, st_size=original.st_size,
                                 st_mtime_ns=original.st_mtime_ns, st_ctime_ns=original.st_ctime_ns)
        after = SimpleNamespace(st_mode=original.st_mode, st_size=original.st_size,
                                st_mtime_ns=original.st_mtime_ns + 1, st_ctime_ns=original.st_ctime_ns)
        with patch.object(replay.os, 'fstat', side_effect=[before, after]):
            with self.assertRaisesRegex(ValueError, 'mutated while reading'):
                replay.facts(path)

    def test_manifest_anchor_before_parsing(self):
        (self.repo / 'docs/evidence').mkdir(parents=True)
        evidence = self.repo / 'docs/evidence/el2-diagnostic-20261104.json'
        evidence.write_text(json.dumps({'full_manifest_sha256': replay.MANIFEST_SHA}))
        (self.provenance / 'artifact-manifest.json').write_text('{invalid JSON')
        with self.assertRaisesRegex(ValueError, 'Unauthenticated provenance'):
            replay.trusted_provenance(self.repo, self.provenance)

    def test_manifest_authenticated_valid_and_bad_input(self):
        (self.repo / 'docs/evidence').mkdir(parents=True)
        entries = {}
        for name in replay.RETAINED:
            content = challenge() if name.endswith('challenge.c') else b'retained\n'
            (self.provenance / name).write_bytes(content)
            entries[name] = replay.facts(self.provenance / name)
        (self.repo / 'configs').mkdir()
        original_lock = self.repo / 'configs/builder-packages.lock.tsv'
        original_lock.write_bytes(b'original inventory\n')
        entries['builder-packages.lock.tsv'] = replay.facts(original_lock)
        manifest = json.dumps({'files': entries}).encode()
        anchor = hashlib.sha256(manifest).hexdigest()
        (self.provenance / 'artifact-manifest.json').write_bytes(manifest)
        (self.repo / 'docs/evidence/el2-diagnostic-20261104.json').write_text(
            json.dumps({'full_manifest_sha256': anchor, 'files': entries}))
        with patch.object(replay, 'MANIFEST_SHA', anchor):
            self.assertEqual(replay.trusted_provenance(self.repo, self.provenance)['files'], entries)
            (self.provenance / 'uboot-unpacked.bin').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'Untrusted input'):
                replay.trusted_provenance(self.repo, self.provenance)
            (self.provenance / 'uboot-unpacked.bin').write_bytes(b'retained\n')
            original_lock.write_bytes(b'changed original inventory\n')
            with self.assertRaisesRegex(ValueError, 'Untrusted input'):
                replay.trusted_provenance(self.repo, self.provenance)

    def committed_project(self):
        subprocess.run(['git', 'init', '-q', self.repo], check=True, capture_output=True)
        (self.repo / '.gitignore').write_text('out/\n')
        for relative in replay.PROJECT_INPUTS:
            path = self.repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('committed fixture: ' + relative + '\n')
        subprocess.run(['git', '-C', self.repo, 'add', '.'], check=True, capture_output=True)
        subprocess.run(['git', '-C', self.repo, '-c', 'user.name=Fixture', '-c',
                        'user.email=fixture@example.invalid', 'commit', '-qm', 'Fixture'],
                       check=True, capture_output=True)

    def test_project_requires_git_metadata(self):
        with self.assertRaisesRegex(ValueError, '.git metadata'):
            replay.project_source(self.repo)

    def test_project_rejects_dirty_and_untracked(self):
        self.committed_project()
        recipe = self.repo / 'build/uboot-replay.py'
        original = recipe.read_bytes()
        recipe.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'clean and committed'):
            replay.project_source(self.repo)
        recipe.write_bytes(original)
        (self.repo / 'untracked-source').write_text('untracked')
        with self.assertRaisesRegex(ValueError, 'clean and committed'):
            replay.project_source(self.repo)

    def test_project_compares_actual_recipe_with_head(self):
        self.committed_project()
        subprocess.run(['git', '-C', self.repo, 'update-index', '--assume-unchanged',
                        'build/uboot-replay.py'], check=True, capture_output=True)
        (self.repo / 'build/uboot-replay.py').write_text('hidden change')
        with self.assertRaisesRegex(ValueError, 'differs from commit'):
            replay.project_source(self.repo)

    def test_source_provenance_captures_actual_commit_and_patch_state(self):
        self.committed_project()
        # Ignored generated output does not dirty the project source checkout.
        (self.repo / 'out/build-log').write_text('generated')
        project = replay.project_source(self.repo)
        actual_head = subprocess.check_output(['git', '-C', self.repo, 'rev-parse', 'HEAD']).decode().strip()
        self.assertEqual(project['commit'], actual_head)
        self.assertEqual(project['git_status_porcelain'], '')
        self.assertEqual(project['inputs']['build/uboot-replay.py'], replay.facts(self.repo / 'build/uboot-replay.py'))
        artifacts = self.root / 'artifacts'
        artifacts.mkdir()
        for name in ('uboot-env-nowhere-size.patch', 'uboot-a733-el2-handoff.patch',
                     'uboot-challenge-replay.patch'):
            (artifacts / name).write_text('actual applied patch: ' + name)
        (artifacts / 'original-uboot-sunxi-challenge.c').write_bytes(challenge())
        record = replay.replay_source_provenance(project, artifacts, {'name': 'original-20261104'})
        self.assertEqual(record['builder_profile'], {'name': 'original-20261104'})
        self.assertEqual(record['project']['commit'], actual_head)
        self.assertEqual(record['recipe'], project['inputs']['build/uboot-replay.py'])
        self.assertEqual(record['registered_target'], replay.TARGET)
        self.assertEqual(record['authenticated_full_manifest_sha256'], replay.MANIFEST_SHA)
        self.assertEqual(record['original_uboot_head'], replay.UBOOT_HEAD)
        self.assertEqual(record['source_date_epoch'], 1772630733)
        self.assertEqual(record['scm_suffix'], '-gb791be8-dirty')
        self.assertEqual(record['replay_challenge_c'], replay.facts(artifacts / 'original-uboot-sunxi-challenge.c'))
        self.assertEqual(record['applied_patches']['uboot-challenge-replay.patch'],
                         replay.facts(artifacts / 'uboot-challenge-replay.patch'))
        (self.repo / 'build/uboot-replay.py').write_text('changed after capture')
        with self.assertRaises(ValueError):
            replay.project_source(self.repo)

    def test_unknown_target_and_nonregistered_path(self):
        script = Path(replay.__file__)
        unknown = subprocess.run(['python3', script, '--target', 'other', '--output', self.output], capture_output=True)
        self.assertNotEqual(unknown.returncode, 0)
        self.assertIn(b'invalid choice', unknown.stderr)
        bad_path = subprocess.run(['python3', script, '--target', replay.TARGET, '--builder-profile', 'original-20261104',
                                   '--workspace', self.workspace, '--output', self.output], capture_output=True)
        self.assertNotEqual(bad_path.returncode, 0)
        self.assertIn(b'Only registered', bad_path.stderr)


class BuilderProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        (self.repo / 'configs').mkdir()
        real_repo = Path(replay.__file__).resolve().parents[1]
        for filename in replay.BUILDER_PROFILES.values():
            (self.repo / 'configs' / filename).write_bytes((real_repo / 'configs' / filename).read_bytes())

    def inventory(self, profile):
        return (self.repo / 'configs' / replay.BUILDER_PROFILES[profile]).read_bytes()

    def test_explicit_profiles_and_mechanical_delta(self):
        original = replay.validate_inventory(self.repo, 'original-20261104', self.inventory('original-20261104'))
        self.assertEqual(original['package_delta_from_original'], [])
        current = replay.validate_inventory(self.repo, 'replay-20261106', self.inventory('replay-20261106'))
        self.assertEqual(current['lock'], replay.facts(self.repo / current['lock_file']))
        self.assertEqual(len(current['package_delta_from_original']), 5)
        self.assertEqual({row['package'] for row in current['package_delta_from_original']}, set(replay.REPLAY_PACKAGE_CHANGES))
        self.assertEqual(current['original_lock'], original['lock'])

    def test_unknown_profile_and_inventory_changes_refused(self):
        with self.assertRaisesRegex(ValueError, 'Unknown builder profile'):
            replay.builder_metadata(self.repo, '/tmp/arbitrary-lock')
        data = self.inventory('replay-20261106')
        for modified in (data + b'extra-package\t1.0\tall\n',
                         data.replace(b'apt\t2.4.15ubuntu0.1\t', b'apt\t9.9\t'),
                         self.inventory('original-20261104')):
            with self.subTest(modified=modified[:80]), self.assertRaisesRegex(ValueError, 'inventory differs'):
                replay.validate_inventory(self.repo, 'replay-20261106', modified)

    def test_registered_profile_lock_mutation_refused(self):
        path = self.repo / 'configs' / replay.BUILDER_PROFILES['replay-20261106']
        for changed in (path.read_bytes() + b'extra-package\t1.0\tall\n',
                        path.read_bytes().replace(b'apt\t2.4.15ubuntu0.1\t', b'apt\t9.9\t')):
            path.write_bytes(changed)
            with self.assertRaisesRegex(ValueError, 'exact registered package delta'):
                replay.builder_metadata(self.repo, 'replay-20261106')

    def test_profile_required_and_unknown_cli_refused(self):
        base = ['python3', replay.__file__, '--target', replay.TARGET, '--output', self.repo / 'out/uboot-replay-test']
        for suffix in ([], ['--builder-profile', 'arbitrary']):
            result = subprocess.run(base + suffix, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b'--builder-profile', result.stderr)


if __name__ == '__main__':
    unittest.main()
