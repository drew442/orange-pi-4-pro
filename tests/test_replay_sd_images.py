"""Bounded SD replay guards; these tests do not compile or establish hardware acceptance."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('replay_sd_images', Path(__file__).resolve().parents[1] / 'build/replay-sd-images.py')
replay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(replay)


def record(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / 'repo'
        (self.repo / 'out').mkdir(parents=True)
        self.inputs, self.kernel, self.vendor = [self.root / name for name in ('inputs', 'kernel', 'vendor')]
        for path in (self.inputs, self.vendor, self.kernel / 'modules/lib/modules'):
            path.mkdir(parents=True)
        self.repo_patch = patch.object(replay, 'REPO', self.repo)
        self.repo_patch.start()
        self.addCleanup(self.repo_patch.stop)

    def test_tag_paths_collisions_and_overlap(self):
        outputs = replay.preflight(self.inputs, self.kernel, self.vendor, 'safe-2026.11_06')
        self.assertEqual(outputs['host'], self.repo / 'out/host-sd-safe-2026.11_06')
        for tag in ('', '-bad', '../bad', 'a/b', 'a' * 65, '$(id)', 'space here'):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                replay.output_paths(tag)
        for key in outputs:
            with self.subTest(key=key):
                outputs[key].mkdir()
                with self.assertRaisesRegex(ValueError, 'Existing output'):
                    replay.preflight(self.inputs, self.kernel, self.vendor, 'safe-2026.11_06')
                outputs[key].rmdir()
        with self.assertRaisesRegex(ValueError, 'overlap'):
            replay.preflight(self.repo, self.kernel, self.vendor, 'test')
        for path in (Path('relative'), self.root / '..' / 'input', Path('/dev'), Path('/proc'), Path('/sys')):
            with self.subTest(path=path), self.assertRaises(ValueError):
                replay.preflight(path, self.kernel, self.vendor, 'test')
        link = self.root / 'linked-input'
        link.symlink_to(self.inputs)
        with self.assertRaises(ValueError):
            replay.preflight(link, self.kernel, self.vendor, 'test')
        outputs['host'].symlink_to(self.root / 'absent')
        with self.assertRaises(ValueError):
            replay.preflight(self.inputs, self.kernel, self.vendor, 'safe-2026.11_06')

    def test_module_symlinks_fail_before_output_reservation(self):
        release = self.kernel / 'modules/lib/modules/6.6.98'
        release.mkdir()
        module = release / 'example.ko'
        module.write_bytes(b'module')
        for name, target in (('linked.ko', module), ('build', self.inputs)):
            link = release / name
            link.symlink_to(target)
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'module'):
                replay.preflight(self.inputs, self.kernel, self.vendor, 'module-link')
            self.assertFalse(any(path.exists() for path in replay.output_paths('module-link').values()))
            link.unlink()

    def test_authenticated_inputs_and_committed_anchors(self):
        path = self.inputs / 'file'
        path.write_bytes(b'correct')
        self.assertEqual(replay.authenticate(path, record(b'correct')), record(b'correct'))
        with self.assertRaisesRegex(ValueError, 'Untrusted input'):
            replay.authenticate(path, record(b'wrong'))
        original_repo = Path(__file__).resolve().parents[1]
        with patch.object(replay, 'REPO', original_repo):
            pins = replay.anchors()
        self.assertEqual(pins['rootfs']['bytes'], 169852145)
        self.assertEqual(pins['busybox']['sha256'], '0a41864f87a0737804f0f4a68a1cfa1ed22374f4f75a1be3eb376e2659ce1316')
        self.assertEqual(pins['state_uuid'], '00fd936c-49fb-4fea-87c8-928b9c1599e0')
        self.assertEqual(pins['expected']['host']['sha256'], '2d22d84ada37a5b5d3696cc913caddcb31c0e681e65185005ea56ca0b8396329')
        self.assertEqual(pins['expected']['installer']['sha256'], '2e16dea5e2941823bf8b40f1687ac417a67c91cecd87173b109fed23ca932499')

    def archive(self, extras=(), source=b'legacy', modes=None):
        path = self.inputs / 'recipe.tar.gz'
        with tarfile.open(path, 'w:gz') as archive:
            for name in sorted(replay.LEGACY_FILES):
                item = tarfile.TarInfo(name)
                item.size = len(source)
                item.mode = (modes or {}).get(name, 0o7777)
                archive.addfile(item, io.BytesIO(source))
            for name, kind in extras:
                item = tarfile.TarInfo(name)
                item.type = kind
                item.linkname = '/tmp/unsafe'
                archive.addfile(item, io.BytesIO(b'') if kind == tarfile.REGTYPE else None)
        return path

    def test_archive_upfront_validation(self):
        cases = [('a/../escape', tarfile.REGTYPE), ('/absolute', tarfile.REGTYPE),
                 ('./build/diagnostic-initramfs.sh', tarfile.REGTYPE),
                 ('build//diagnostic-initramfs.sh', tarfile.REGTYPE),
                 ('link', tarfile.SYMTYPE), ('hardlink', tarfile.LNKTYPE),
                 ('fifo', tarfile.FIFOTYPE), ('device', tarfile.CHRTYPE)]
        for index, extra in enumerate(cases):
            destination = self.root / f'extract-{index}'
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                replay.extract_legacy(self.archive([extra]), destination)
            self.assertFalse(destination.exists())
        with self.assertRaisesRegex(ValueError, 'checksum'):
            replay.extract_legacy(self.archive(), self.root / 'bad-content')
        self.assertFalse((self.root / 'bad-content').exists())
        hashes = {name: record(b'legacy')['sha256'] for name in replay.LEGACY_HASHES}
        with patch.object(replay, 'LEGACY_HASHES', hashes):
            result = replay.extract_legacy(self.archive(), self.root / 'good')
        self.assertEqual(set(result), replay.LEGACY_FILES)
        for name in replay.LEGACY_FILES:
            self.assertEqual((self.root / 'good' / name).stat().st_mode & 0o7777, 0o777)

    def test_legacy_archive_modes_preserve_authenticated_cpio_metadata(self):
        modes = {'licenses/GPL-2.0.txt': 0o664,
                 'licenses/busybox-copyright.txt': 0o644,
                 'configs/diagnostic-init': 0o775,
                 'build/diagnostic-initramfs.sh': 0o775}
        hashes = {name: record(b'legacy')['sha256'] for name in replay.LEGACY_HASHES}
        with patch.object(replay, 'LEGACY_HASHES', hashes):
            replay.extract_legacy(self.archive(modes=modes), self.root / 'modes')
        for name, expected in modes.items():
            with self.subTest(name=name):
                self.assertEqual((self.root / 'modes' / name).stat().st_mode & 0o7777, expected)

    def test_private_environment_and_umask_restore_on_failure(self):
        original = dict(os.environ)
        before = os.umask(0o077)
        try:
            with patch.dict(os.environ, {'MAKEFLAGS': '-j99', 'GIT_DIR': '/evil', 'PYTHONPATH': '/evil', 'CC': '/evil'}):
                with self.assertRaisesRegex(RuntimeError, 'failure'):
                    with replay.private_environment():
                        self.assertEqual(dict(os.environ), replay.ENVIRONMENT)
                        self.assertEqual(os.umask(0o022), 0o022)
                        with replay.umask(0o002):
                            self.assertEqual(os.umask(0o002), 0o002)
                        self.assertEqual(os.umask(0o022), 0o022)
                        raise RuntimeError('failure')
                self.assertEqual(os.environ['MAKEFLAGS'], '-j99')
                self.assertEqual(os.umask(0o077), 0o077)
        finally:
            os.umask(before)
        self.assertEqual(dict(os.environ), original)

    def test_command_failure_preserves_output_and_environment(self):
        logs = self.root / 'logs'
        logs.mkdir()
        commands = replay.Commands(logs)
        with patch.dict(os.environ, {'MAKEFLAGS': '-j99'}):
            with self.assertRaises(subprocess.CalledProcessError):
                commands.run('/usr/bin/python3', '-I', '-c',
                             'import os,sys; print(os.environ.get("MAKEFLAGS", "clean")); sys.exit(7)')
        self.assertEqual((logs / '01.log').read_text(), 'clean\n')
        self.assertEqual(json.loads((logs / 'commands.json').read_text())[0]['returncode'], 7)
        self.assertTrue(logs.exists())

    def test_project_recipe_must_be_committed_and_actual_bytes_match(self):
        project = {'commit': 'a' * 40}
        tree = b'100644 blob ' + b'b' * 40 + b'\tbuild/replay-sd-images.py\0'
        recipe = self.repo / 'build/replay-sd-images.py'
        recipe.parent.mkdir()
        recipe.write_bytes(b'committed')
        with patch.object(replay.HELPER, 'project_source', return_value=dict(project)), \
                patch.object(replay.subprocess, 'check_output', side_effect=[tree, b'committed']):
            result = replay.project_source()
        self.assertEqual(result['inputs']['build/replay-sd-images.py'], record(b'committed'))
        for listing, blob in ((b'', b''), (tree, b'different')):
            with self.subTest(listing=listing), patch.object(replay.HELPER, 'project_source', return_value=dict(project)), \
                    patch.object(replay.subprocess, 'check_output', side_effect=[listing, blob]), self.assertRaises(ValueError):
                replay.project_source()

    def test_inspection_disk_probe_and_resource_guards(self):
        outputs = replay.output_paths('case')
        with patch.object(replay.STATE, 'validate_workspace_storage', return_value={'fstype': 'ext4'}) as storage, \
                patch.object(replay.shutil, 'disk_usage', return_value=type('Usage', (), {'free': 0})()), \
                patch.object(replay.tempfile, 'TemporaryDirectory', return_value=self.temporary):
            with self.assertRaisesRegex(ValueError, '4 GiB'):
                replay.resources(outputs)
        self.assertEqual(storage.call_args_list[1].args, (Path('/var/tmp'),))

    def test_replay_success_and_mismatch_results_preserve_outputs(self):
        tiny = record(b'data')
        diag = {name: tiny for name in ('Image', 'board.dtb', 'kernel.config', 'boot0_sdcard.fex', 'initramfs.cpio.gz', 'uImage')}
        pins = {'diagnostic': diag, 'el2': {'boot_package.fex': tiny}, 'state_uuid': 'uuid',
                'expected': {'host': tiny, 'installer': tiny}}
        for name in ('Image', 'board.dtb', 'kernel.config'):
            (self.kernel / name).write_bytes(b'data')
        boot0 = self.vendor / 'external/packages/pack-uboot/sun60iw2/bin/boot0_sdcard_a733.fex'
        boot0.parent.mkdir(parents=True)
        boot0.write_bytes(b'data')
        (self.inputs / 'boot_package.fex').write_bytes(b'data')

        def command(commands, *args):
            path = commands.directory / f'{len(commands.records):02d}.log'
            commands.records.append({'command': list(map(str, args)), 'returncode': 0})
            path.write_bytes(b'{}\n')
            if args[0] == 'bash':
                Path(args[3]).write_bytes(b'data')
            elif args[0] == 'mkimage':
                Path(args[-1]).write_bytes(b'data')
            elif 'assemble-' in str(args[3]):
                output = Path(args[6])
                output.mkdir()
                name = 'a733-host-sd.img' if 'assemble-host' in str(args[3]) else 'a733-installer-prep.img'
                (output / name).write_bytes(b'data')
                (output / (name + '.gz')).write_bytes(b'compressed')
            return path

        def execute(tag):
            with patch.object(replay.os, 'geteuid', return_value=0), \
                    patch.object(replay, 'project_source', return_value={'commit': 'a' * 40}), \
                    patch.object(replay.HELPER, 'check_builder', return_value={'name': replay.PROFILE}), \
                    patch.object(replay, 'resources', return_value={}), \
                    patch.object(replay, 'anchors', return_value=pins), \
                    patch.object(replay, 'input_records', return_value={'input': tiny}), \
                    patch.object(replay, 'extract_legacy', return_value={}), \
                    patch.object(replay.Commands, 'run', command):
                return replay.replay(self.inputs, self.kernel, self.vendor, tag)

        result = execute('success')
        self.assertEqual(result['status'], 'passed')
        self.assertTrue(all(result['comparisons'].values()))
        self.assertEqual(result['products']['host']['gzip'], record(b'compressed'))
        self.assertFalse(result['hardware_acceptance'])
        self.assertTrue(result['builder_profile_before_after_equal'])
        pins['expected']['host'] = record(b'wrong')
        with self.assertRaisesRegex(ValueError, 'differ from committed targets'):
            execute('mismatch')
        failure = json.loads((self.repo / 'out/sd-replay-mismatch/result.json').read_text())
        self.assertEqual(failure['status'], 'failed')
        self.assertIn('ValueError', failure['error'])
        self.assertTrue((self.repo / 'out/host-sd-mismatch/a733-host-sd.img').exists())


if __name__ == '__main__':
    unittest.main()
