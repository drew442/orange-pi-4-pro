"""Pinned regular-file repackaging checks; no physical boot claims."""
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('network_diagnostic', REPO / 'build/network-diagnostic.py')
NETWORK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(NETWORK)


def newc(entries):
    data = bytearray()
    for name, mode, content in entries + [('TRAILER!!!', 0, b'')]:
        name = name.encode() + b'\0'
        fields = [1, mode, 0, 0, 1, 0, len(content), 0, 0, 0, 0, len(name), 0]
        data += b'070701' + ''.join(f'{field:08x}' for field in fields).encode() + name
        data += b'\0' * (-len(data) % 4)
        data += content
        data += b'\0' * (-len(data) % 4)
    return bytes(data)


def module_archive(extra=None):
    root = 'lib/modules/' + NETWORK.PAYLOAD.KERNEL_RELEASE
    return gzip.compress(newc([
        ('lib/modules', stat.S_IFDIR | 0o755, b''),
        (root, stat.S_IFDIR | 0o755, b''),
        (root + '/fixture.ko', stat.S_IFREG | 0o640, b'kernel module'),
        (root + '/modules.alias', stat.S_IFREG | 0o644, b'alias fixture fixture\n'),
        ('init', stat.S_IFREG | 0o755, b'untrusted nonmodule payload'),
    ] + (extra or [])), mtime=0)


class RecipeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / 'repo'
        (self.repo / 'out').mkdir(parents=True)
        (self.repo / 'docs/evidence').mkdir(parents=True)
        (self.repo / 'configs').mkdir()
        self.base = self.root / 'base'
        self.base.mkdir()
        for name in NETWORK.CORE + ('uInitrd',):
            (self.base / name).write_bytes(name.encode())
        (self.base / 'kernel.config').write_text('CONFIG_MODPROBE_PATH="/sbin/modprobe"\n')
        (self.base / 'initramfs.cpio.gz').write_bytes(module_archive())
        for directory in ('notices', 'busybox-source'):
            (self.base / directory).mkdir()
            (self.base / directory / 'fixture').write_bytes(directory.encode())
        self.records = {p.relative_to(self.base).as_posix(): NETWORK.HOST.file_record(p)
                        for p in self.base.rglob('*') if p.is_file()}
        self.write_manifest()
        self.busybox = self.root / 'busybox'
        self.busybox.write_bytes(b'locked BusyBox')
        self.lock = {'busybox': {'binary_sha256': hashlib.sha256(self.busybox.read_bytes()).hexdigest()}}
        (self.repo / 'configs/diagnostic-sources.lock.json').write_text(json.dumps(self.lock))
        self.patch = mock.patch.object(NETWORK, 'REPO', self.repo)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def write_manifest(self):
        raw = json.dumps({'schema': 1, 'files': self.records}).encode()
        (self.base / 'artifact-manifest.json').write_bytes(raw)
        evidence = {'full_manifest_sha256': hashlib.sha256(raw).hexdigest(),
                    'files': {name: self.records[name] for name in NETWORK.CORE + ('uInitrd',)}}
        (self.repo / 'docs/evidence/el2-diagnostic-20261104.json').write_text(json.dumps(evidence))

    def test_authenticates_fixed_manifest_and_retained_inputs(self):
        base, manifest, raw, initrd = NETWORK.authenticate_base(self.base)
        self.assertEqual(base, self.base)
        self.assertEqual(manifest['files'], self.records)
        self.assertEqual(raw, (base / 'artifact-manifest.json').read_bytes())
        self.assertEqual(initrd, module_archive())

    def test_rejects_rewritten_manifest_even_if_self_consistent(self):
        (self.base / 'Image').write_bytes(b'replacement kernel')
        self.records['Image'] = NETWORK.HOST.file_record(self.base / 'Image')
        (self.base / 'artifact-manifest.json').write_text(json.dumps({'files': self.records}))
        with self.assertRaisesRegex(ValueError, 'untrusted EL2 artifact manifest'):
            NETWORK.authenticate_base(self.base)

    def test_rejects_changed_core_old_initrd_and_source(self):
        for name in ('Image', 'boot_package.fex', 'initramfs.cpio.gz', 'busybox-source/fixture'):
            with self.subTest(name=name):
                path = self.base / name
                original = path.read_bytes()
                path.write_bytes(b'modified')
                with self.assertRaisesRegex(ValueError, 'hash/size mismatch'):
                    NETWORK.authenticate_base(self.base)
                path.write_bytes(original)

    def test_rejects_evidence_core_disagreement(self):
        evidence_path = self.repo / 'docs/evidence/el2-diagnostic-20261104.json'
        evidence = json.loads(evidence_path.read_text())
        evidence['files']['Image']['sha256'] = '0' * 64
        evidence_path.write_text(json.dumps(evidence))
        with self.assertRaisesRegex(ValueError, 'evidence disagrees'):
            NETWORK.authenticate_base(self.base)

    def test_rejects_unsafe_source_or_missing_retained_source(self):
        source = self.base / 'notices/fixture'
        source.unlink()
        source.symlink_to(self.busybox)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            NETWORK.authenticate_base(self.base)
        source.unlink()
        with self.assertRaisesRegex(ValueError, 'incomplete retained notices'):
            NETWORK.authenticate_base(self.base)

    def test_busybox_uses_fixed_repository_lock(self):
        self.assertEqual(NETWORK.authenticate_busybox(self.busybox), (b'locked BusyBox', self.lock))
        self.busybox.write_bytes(b'other BusyBox')
        with self.assertRaisesRegex(ValueError, 'fixed source lock'):
            NETWORK.authenticate_busybox(self.busybox)

    def test_rejects_symlink_fifo_and_device_inputs_without_opening_them(self):
        link = self.root / 'link'
        link.symlink_to(self.busybox)
        fifo = self.root / 'fifo'
        os.mkfifo(fifo)
        for path in (link, fifo, Path('/dev/null')):
            with self.subTest(path=path), self.assertRaises(ValueError):
                NETWORK.read(path)

    def test_output_name_location_and_new_directory_are_required(self):
        output = self.repo / 'out/network-diagnostic-fixture'
        self.assertEqual(NETWORK.validate_output(output), output)
        for bad in (self.root / output.name, self.repo / 'out/network-diagnostic-',
                    self.repo / 'out/other', Path('out/network-diagnostic-relative'),
                    self.repo / 'out/../out/network-diagnostic-traversal'):
            with self.subTest(path=bad), self.assertRaises(ValueError):
                NETWORK.validate_output(bad)
        output.mkdir()
        with self.assertRaises(ValueError):
            NETWORK.validate_output(output)
        output.rmdir()
        output.symlink_to(self.root / 'absent')
        with self.assertRaises(ValueError):
            NETWORK.validate_output(output)

    def test_output_symlink_ancestor_is_refused(self):
        (self.repo / 'out').rmdir()
        (self.repo / 'out').symlink_to(self.root)
        with self.assertRaises(ValueError):
            NETWORK.validate_output(self.repo / 'out/network-diagnostic-fixture')

    def test_private_module_extraction_preserves_content_modes_and_excludes_userspace(self):
        destination = self.root / 'modules'
        entries = NETWORK.stage_modules(module_archive(), destination)
        self.assertFalse((destination / 'init').exists())
        module = destination / NETWORK.PAYLOAD.KERNEL_RELEASE / 'fixture.ko'
        self.assertEqual(module.read_bytes(), b'kernel module')
        self.assertEqual(stat.S_IMODE(module.stat().st_mode), 0o640)
        self.assertEqual(len(entries), 4)

    def test_private_module_extraction_refuses_links_and_traversal_before_staging(self):
        for name, mode in [('lib/modules/escape', stat.S_IFLNK | 0o777),
                           ('lib/modules/../escape', stat.S_IFREG | 0o644)]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                NETWORK.stage_modules(module_archive([(name, mode, b'outside')]), self.root / 'modules')
            self.assertFalse((self.root / 'modules').exists())

    def test_repacked_initrd_requires_helper_target_and_pinned_busybox(self):
        original = module_archive()
        modules = NETWORK.PAYLOAD.module_entries(gzip.decompress(original))
        binary = ('bin/busybox', stat.S_IFREG | 0o755, b'locked BusyBox')
        helper = ('sbin/modprobe', stat.S_IFLNK | 0o777, b'../bin/busybox')
        repacked = module_archive([binary, helper])
        NETWORK.validate_repacked_initrd(repacked, modules, b'locked BusyBox')
        for bad in (module_archive([binary]),
                    module_archive([binary, ('sbin/modprobe', stat.S_IFREG | 0o755, b'../bin/busybox')]),
                    module_archive([binary, ('sbin/modprobe', stat.S_IFLNK | 0o777, b'wrong-target')])):
            with self.assertRaisesRegex(ValueError, 'BusyBox helper'):
                NETWORK.validate_repacked_initrd(bad, modules, b'locked BusyBox')
        with self.assertRaisesRegex(ValueError, 'BusyBox helper'):
            NETWORK.validate_repacked_initrd(repacked, modules, b'replacement BusyBox')

    def test_archive_retains_recipe_directories_with_normalized_metadata(self):
        for name in ('build', 'licenses', 'scripts'):
            (self.repo / name).mkdir()
            (self.repo / name / 'fixture').write_text('recipe')
        (self.repo / 'scripts/verify-diagnostic.py').write_bytes((REPO / 'scripts/verify-diagnostic.py').read_bytes())
        (self.repo / 'configs/diagnostic-boot.cmd').write_bytes((REPO / 'configs/diagnostic-boot.cmd').read_bytes())
        first, second = self.root / 'one.tar.gz', self.root / 'two.tar.gz'
        NETWORK.recipe_archive(first)
        NETWORK.recipe_archive(second)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        with tarfile.open(first) as archive:
            self.assertEqual(archive.extractfile('configs/diagnostic-boot.cmd').read(),
                             (REPO / 'configs/diagnostic-boot.cmd').read_bytes())
            self.assertEqual(archive.extractfile('scripts/verify-diagnostic.py').read(),
                             (REPO / 'scripts/verify-diagnostic.py').read_bytes())
            self.assertEqual(archive.extractfile('docs/evidence/el2-diagnostic-20261104.json').read(),
                             (self.repo / 'docs/evidence/el2-diagnostic-20261104.json').read_bytes())
            for member in archive:
                self.assertEqual((member.uid, member.gid, member.mtime), (0, 0, NETWORK.EPOCH))

    def test_all_build_subprocesses_force_validated_output_for_temporary_files(self):
        output = self.repo / 'out/network-diagnostic-env'
        for name in ('build', 'licenses', 'scripts'):
            (self.repo / name).mkdir()
        original_run = subprocess.run
        calls = []

        def run_command(command, *, env, check, stdout=None):
            self.assertTrue(check)
            self.assertEqual(env['TMPDIR'], str(output))
            calls.append(command)
            # A real fresh Python process must choose validated storage even
            # when the caller inherited an unsuitable TMPDIR.
            probe = original_run(
                [sys.executable, '-c',
                 'import tempfile; from pathlib import Path; '
                 't=tempfile.TemporaryDirectory(); print(Path(t.name).parent); t.cleanup()'],
                env=env, check=True, capture_output=True, text=True)
            self.assertEqual(probe.stdout.strip(), str(output))
            if command[0] == 'bash' and command[1].endswith('diagnostic-initramfs.sh'):
                Path(command[3]).write_bytes(module_archive([
                    ('bin/busybox', stat.S_IFREG | 0o755, b'locked BusyBox'),
                    ('sbin/modprobe', stat.S_IFLNK | 0o777, b'../bin/busybox')]))
            elif command[0] == 'mkimage':
                Path(command[-1]).write_bytes(b'wrapped initrd')
            elif command[0] == 'bash':
                Path(command[-1]).write_bytes(b'image fixture')
            elif command[0] == 'gzip':
                stdout.write(gzip.compress(Path(command[-1]).read_bytes(), mtime=0))
            elif command[0] != 'python3':
                self.fail(f'unexpected command: {command}')
            return subprocess.CompletedProcess(command, 0)

        with mock.patch.dict(os.environ, {'TMPDIR': str(self.root / 'unsuitable-storage')}), \
                mock.patch.object(NETWORK, 'validate_builder', return_value={}), \
                mock.patch.object(NETWORK.STATE, 'validate_workspace_storage', return_value={}), \
                mock.patch.object(NETWORK.subprocess, 'run', side_effect=run_command):
            result = NETWORK.build(self.base, self.busybox, output)
        self.assertTrue(result['preserved_core_hashes'])
        self.assertEqual(len(calls), 5)
        self.assertTrue(calls[-1][1].endswith('scripts/verify-diagnostic.py'))


if __name__ == '__main__':
    unittest.main()
