"""Frozen component launcher guards; no actual compiler/device acceptance."""
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import types
import unittest
import zlib

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('launch_nvme', REPO / 'build/launch-uboot-nvme-candidate.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def image(source):
    payload = struct.pack('>2I', len(source), 0) + source
    header = struct.pack('>7I4B32s', 0x27051956, 0, 1793750400, len(payload), 0, 0,
                         zlib.crc32(payload), 5, 2, 6, 0, bytes(32))
    return header[:4] + struct.pack('>I', zlib.crc32(header)) + header[8:] + payload


class LauncherTests(unittest.TestCase):
    def test_tag_and_resource_bounds(self):
        for tag in ('', '../old', '/old', 'a b', '-old', 'a' * 65):
            with self.assertRaises(ValueError): M.phase_path(tag)
        self.assertEqual(M.phase_path('first_01').name, 'uboot-nvme-component-first_01')
        good = {'free_bytes': 3_000_000_000, 'available_memory_bytes': 1_000_000_000}
        M.check_resources(good, M.TIMEOUT)
        for bad, elapsed in [(dict(good, free_bytes=2_999_999_999), 0),
                             (dict(good, available_memory_bytes=999_999_999), 0), (good, M.TIMEOUT + 1)]:
            with self.assertRaises(ValueError): M.check_resources(bad, elapsed)

    def test_plan_git_closure_and_no_staging(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            for name in M.SOURCE_FILES:
                path = project / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((REPO / name).read_bytes())
            for args in (['init', '-q'], ['add', '.'], ['-c', 'user.name=Fixture', '-c',
                         'user.email=f@example.invalid', 'commit', '-qm', 'Fixture']):
                subprocess.run(['git', '-C', str(project)] + args, check=True, capture_output=True)
            before = sorted(str(p.relative_to(project)) for p in project.rglob('*'))
            args = [sys.executable, '-I', '-B', str(project / M.SELF), '--project', str(project), '--tag', 'fixture', '--plan']
            result = subprocess.run(args, check=True, capture_output=True, text=True)
            plan = json.loads(result.stdout)
            self.assertEqual(plan['compilation_jobs'], 2)
            self.assertFalse(plan['spi_image_or_programmer'])
            self.assertEqual(plan['minimum_initial_free_bytes_exclusive'], 4_000_000_000)
            self.assertEqual(before, sorted(str(p.relative_to(project)) for p in project.rglob('*')))
            (project / M.SCRIPT).write_bytes(b'changed')
            rejected = subprocess.run(args, capture_output=True, text=True)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn('source differs from Git blob', rejected.stderr)

    def test_namespace_readonly_inputs_and_fixed_inner_recipe(self):
        shared = types.SimpleNamespace(ROOT=Path('/rootfs'), SEED=Path('/seed'))
        builder = types.SimpleNamespace(nspawn_command=lambda *a: ['nspawn', '--private-users-ownership=chown', 'old', 'tail'])
        args = M.command(shared, builder, Path('/phase'))
        self.assertIn('--read-only', args)
        self.assertIn('--private-users-ownership=off', args)
        self.assertIn('--bind-ro=' + str(M.SOURCE_HOME) + ':/home', args)
        self.assertIn('--bind-ro=' + str(M.PROVENANCE) + ':' + str(M.PROVENANCE), args)
        self.assertIn('--bind=/phase/work:/a733-export-output', args)
        runner = M.inner_program('a' * 40).decode()
        compile(runner, '<inner>', 'exec')
        self.assertIn("'--builder-profile','replay-20261106'", runner)
        self.assertIn("'--output',str(output)", runner)
        self.assertNotIn('flashcp', runner)
        self.assertNotIn('/dev/mtd', runner)
        with self.assertRaises(ValueError): M.inner_program('not a revision')

    def test_bundle_head_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / 'source'; source.mkdir()
            (source / 'file').write_text('source')
            for args in (['init', '-q'], ['add', '.'], ['-c', 'user.name=Fixture', '-c',
                         'user.email=f@example.invalid', 'commit', '-qm', 'Fixture']):
                subprocess.run(['git', '-C', str(source)] + args, check=True, capture_output=True)
            revision = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD']).decode().strip()
            bundle = root / 'source.bundle'; clone = root / 'clone'
            subprocess.run(['git', '-C', str(source), 'bundle', 'create', str(bundle), 'HEAD'], check=True, capture_output=True)
            subprocess.run(['git', 'clone', '--no-checkout', str(bundle), str(clone)], check=True, capture_output=True)
            subprocess.run(['git', '-C', str(clone), 'checkout', '--detach', revision], check=True, capture_output=True)
            self.assertEqual((clone / 'file').read_bytes(), b'source')
            self.assertEqual(subprocess.check_output(['git', '-C', str(clone), 'status', '--porcelain']), b'')

    def test_independent_script_crc_payload_epoch_and_arch_guards(self):
        source = b'echo candidate\nfalse\n'
        good = image(source)
        checked = M.verify_host_script(good, source)
        self.assertTrue(checked['crc_verified'])
        self.assertEqual(checked['script'], M.pin(good))
        for offset in (0, 4, 8, 12, 16, 20, 24, 28, 29, 30, 31, 32, 64, 68, 72):
            bad = bytearray(good); bad[offset] ^= 1
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                M.verify_host_script(bytes(bad), source)
        for bad in (good[:60], good[:-1], good + b'\0'):
            with self.assertRaises(ValueError): M.verify_host_script(bad, source)
        with self.assertRaises(ValueError): M.verify_host_script(good, b'changed')


if __name__ == '__main__':
    unittest.main()
