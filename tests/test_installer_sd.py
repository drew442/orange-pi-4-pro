"""Installer preparation SD packaging fixtures; no hardware acceptance claims."""
import copy
import gzip
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zlib

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('assemble_installer', REPO / 'build/assemble-installer.py')
SD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SD)
TOOLS = ('mkimage', 'sfdisk', 'mkfs.ext4', 'e2fsck', 'debugfs')


class InstallerSDTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='a733-installer-sd-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / 'repo'
        for name in set(SD.RECIPES) | SD.INITRAMFS_RECIPES:
            target = self.repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO / name, target)
        self.diagnostic = self.root / 'diagnostic'
        self.installer = self.root / 'installer'
        self.diagnostic.mkdir()
        self.installer.mkdir()
        self.output = self.repo / 'out/installer-sd-fixture'
        self.pinned = {}
        for name in SD.BLOBS:
            data = ('nonbootable-fixture-' + name).encode()
            if name == 'boot_package.fex':
                data = data.ljust(1392640, b'\0')
            (self.diagnostic / name).write_bytes(data)
            self.pinned[name] = SD.HOST.record(data)
        archive = gzip.compress(b'nonbootable fixture', mtime=0)
        (self.installer / 'initramfs.cpio.gz').write_bytes(archive)
        self.pin_path = self.repo / 'docs/evidence/diagnostic-20261104.json'
        self.pin_path.write_text(json.dumps({'schema': 1, 'files': self.pinned}))
        self.el2_package = b'EL2-fixture'.ljust(1392640, b'\0')
        self.el2_pinned = copy.deepcopy(self.pinned)
        self.el2_pinned['boot_package.fex'] = SD.HOST.record(self.el2_package)
        self.el2_evidence = {'schema': 1, 'files': self.el2_pinned,
                             'handoff': {'expected_service': '0x8000ff40', 'expected_spsr': '0x3c9'}}
        self.el2_path = self.repo / 'docs/evidence/el2-diagnostic-20261104.json'
        self.el2_path.write_text(json.dumps(self.el2_evidence))
        self.manifest = {'schema': 1, 'kernel_release': SD.KERNEL_RELEASE,
                         'boot_inputs': self.pinned,
                         'entries': {name: {'mode': mode, **SD.HOST.record((self.repo / source).read_bytes())}
                                     for name, (source, mode) in SD.ENTRIES.items()},
                         'recipes': {name: SD.HOST.file_record(self.repo / name) for name in SD.INITRAMFS_RECIPES},
                         'files': {'initramfs.cpio.gz': SD.HOST.record(archive)}}
        self.write_manifest()
        self.addCleanup(patch.stopall)
        patch.object(SD, 'REPO', self.repo).start()
        patch.object(SD.HOST, 'REPO', self.repo).start()
        patch.object(SD.HOST, 'DIAGNOSTIC_RECORD', self.pin_path).start()
        self.packages = patch.object(SD.HOST, 'validate_builder_packages', return_value={'fixture': {}}).start()
        self.storage = patch.object(SD.STATE, 'validate_workspace_storage', return_value={'fstype': 'ext4', 'maj:min': '1:1'}).start()

    def write_manifest(self):
        (self.installer / 'manifest.json').write_text(json.dumps(self.manifest))

    def build(self, output=None):
        return SD.build(self.diagnostic, self.installer, output or self.output)

    def test_profile_evidence_is_locked_and_validated_before_output(self):
        for profile in ('unknown', '../experimental-el2'):
            with self.subTest(profile=profile), self.assertRaisesRegex(ValueError, 'unknown boot profile'):
                SD.build(self.diagnostic, self.installer, self.output, boot_profile=profile)
            self.assertFalse(self.output.exists())
        for blob in SD.HOST.BLOBS:
            changed = copy.deepcopy(self.el2_evidence)
            changed['files'][blob]['bytes'] += 1
            self.el2_path.write_text(json.dumps(changed))
            with self.subTest(blob=blob), self.assertRaises(ValueError):
                SD.build(self.diagnostic, self.installer, self.output, boot_profile='experimental-el2')
            self.assertFalse(self.output.exists())
        for handoff in ({}, {'expected_service': '0x8000ff04', 'expected_spsr': '0x3c9'},
                        {'expected_service': '0x8000ff40', 'expected_spsr': '0x3c5'}):
            changed = copy.deepcopy(self.el2_evidence)
            changed['handoff'] = handoff
            self.el2_path.write_text(json.dumps(changed))
            with self.assertRaisesRegex(ValueError, 'handoff'):
                SD.build(self.diagnostic, self.installer, self.output, boot_profile='experimental-el2')
            self.assertFalse(self.output.exists())

    @unittest.skipUnless(all(shutil.which(tool) for tool in TOOLS), 'image assembly tools required')
    def test_experimental_package_requires_explicit_selection(self):
        # The old initramfs pins remain valid: Image/DTB/config have not changed.
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            SD.build(self.diagnostic, self.installer, self.output, boot_profile='experimental-el2')
        shutil.rmtree(self.output)
        (self.diagnostic / 'boot_package.fex').write_bytes(self.el2_package)
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            self.build()
        shutil.rmtree(self.output)
        result = SD.build(self.diagnostic, self.installer, self.output, boot_profile='experimental-el2')
        self.assertEqual(result['boot_inputs'], self.el2_pinned)
        self.assertEqual(result['boot_profile']['name'], 'experimental-el2')
        self.assertEqual(result['boot_profile']['target_service'], '0x8000ff40')
        self.assertTrue(result['boot_profile']['physical_acceptance_pending'])
        evidence = SD.HOST.record(self.el2_path.read_bytes())
        self.assertEqual(result['recipes']['docs/evidence/el2-diagnostic-20261104.json'], evidence)
        self.assertEqual(result['boot_profile']['evidence'],
                         {'path': 'docs/evidence/el2-diagnostic-20261104.json', **evidence})

    def test_manifest_schema_kernel_boot_and_entries(self):
        for field, value in (('schema', True), ('schema', 2), ('kernel_release', 'other')):
            changed = copy.deepcopy(self.manifest)
            changed[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                SD.validate_manifest(changed, self.pinned)
        for name in ('Image', 'board.dtb', 'kernel.config'):
            changed = copy.deepcopy(self.manifest)
            changed['boot_inputs'][name]['bytes'] += 1
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'boot input'):
                SD.validate_manifest(changed, self.pinned)
        for name in SD.ENTRIES:
            for field in ('mode', 'bytes', 'sha256'):
                changed = copy.deepcopy(self.manifest)
                changed['entries'][name][field] = 0
                with self.subTest(name=name, field=field), self.assertRaisesRegex(ValueError, 'entry'):
                    SD.validate_manifest(changed, self.pinned)
        (self.repo / 'configs/installer-init').write_bytes(b'changed current source')
        with self.assertRaisesRegex(ValueError, 'entry'):
            self.build()

    def test_recipe_provenance_required_current_and_canonical(self):
        for mutation in ('missing', 'stale', 'unsafe', 'noncanonical'):
            changed = copy.deepcopy(self.manifest)
            if mutation == 'missing':
                del changed['recipes']['build/installer-initramfs.py']
            elif mutation == 'stale':
                changed['recipes']['build/installer-initramfs.py']['bytes'] += 1
            else:
                changed['recipes']['../escape' if mutation == 'unsafe' else 'build//extra.py'] = {}
            with self.subTest(mutation=mutation), self.assertRaisesRegex(ValueError, 'provenance|recipe path'):
                SD.validate_manifest(changed, self.pinned)
        (self.repo / 'build/installer-initramfs.py').write_bytes(b'new generator')
        with self.assertRaisesRegex(ValueError, 'provenance differs'):
            self.build()

    def test_archive_record_budget(self):
        for item in ({}, {'bytes': True, 'sha256': '0' * 64},
                     {'bytes': SD.MAX_INITRAMFS + 1, 'sha256': '0' * 64},
                     {'bytes': 1, 'sha256': 'bad'}):
            changed = copy.deepcopy(self.manifest)
            changed['files']['initramfs.cpio.gz'] = item
            with self.subTest(item=item), self.assertRaisesRegex(ValueError, 'initramfs'):
                SD.validate_manifest(changed, self.pinned)

    def test_wrong_captured_inputs(self):
        for directory, name in [(self.diagnostic, name) for name in SD.BLOBS] + [(self.installer, 'initramfs.cpio.gz')]:
            source = directory / name
            original = source.read_bytes()
            source.write_bytes(bytes([original[0] ^ 1]) + original[1:])
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'mismatch'):
                self.build()
            source.write_bytes(original)
            if self.output.exists():
                shutil.rmtree(self.output)

    def test_output_storage_and_package_guards(self):
        for output in (Path('out/installer-sd-relative'), self.root / 'installer-sd-outside',
                       self.repo / 'out/installer-sd-', Path('/dev/installer-sd-no')):
            with self.subTest(output=output), self.assertRaises(ValueError):
                self.build(output)
        for reason in ('RAM', 'MMC', 'removable'):
            self.storage.side_effect = ValueError(reason)
            with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, reason):
                self.build()
            self.assertFalse(self.output.exists())
        self.storage.side_effect = None
        self.packages.side_effect = ValueError('package pin mismatch')
        with self.assertRaisesRegex(ValueError, 'package pin'):
            self.build()
        self.assertFalse(self.output.exists())
        self.output.mkdir(parents=True)
        (self.output / 'keep').write_bytes(b'keep')
        with self.assertRaisesRegex(ValueError, 'existing'):
            self.build()
        self.assertEqual((self.output / 'keep').read_bytes(), b'keep')
        shutil.rmtree(self.output)
        self.output.symlink_to(self.installer, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.build()

    def test_nonregular_and_symlink_inputs(self):
        source = self.installer / 'initramfs.cpio.gz'
        source.unlink()
        os.mkfifo(source)
        with self.assertRaisesRegex(ValueError, 'regular'):
            self.build()
        source.unlink()
        source.symlink_to(self.diagnostic / 'Image')
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.build(self.repo / 'out/installer-sd-alias')

    def test_boot_protocol_identical_to_diagnostic(self):
        def commands(name):
            return [line for line in (REPO / 'configs' / name).read_text().splitlines()
                    if line and not line.startswith(('#', 'echo '))]
        self.assertEqual(commands('installer-boot.cmd'), commands('diagnostic-boot.cmd'))

    @unittest.skipUnless(all(shutil.which(tool) for tool in TOOLS), 'image tools required')
    def test_actual_regular_file_packaging(self):
        result = self.build()
        image = self.output / 'a733-installer-prep.img'
        self.assertEqual(image.stat().st_size, 256 * 1024**2)
        self.assertEqual(result['classification'], SD.CLASSIFICATION)
        self.assertEqual(result['trusted_installer_manifest'], {
            'path': str(self.installer / 'manifest.json'),
            **SD.HOST.record((self.installer / 'manifest.json').read_bytes())})
        self.assertEqual(result['installer_initramfs']['sha256'], self.manifest['files']['initramfs.cpio.gz']['sha256'])
        self.assertEqual(result['initramfs_recipes'], self.manifest['recipes'])
        for name, expected in result['files'].items():
            self.assertEqual(SD.HOST.file_record(self.output / name), expected)
        with gzip.open(self.output / 'a733-installer-prep.img.gz', 'rb') as compressed, image.open('rb') as raw:
            while chunk := raw.read(1024**2):
                self.assertEqual(compressed.read(len(chunk)), chunk)
            self.assertEqual(compressed.read(1), b'')
        partition = self.root / 'partition.ext4'
        with image.open('rb') as raw:
            mbr = raw.read(512)
            self.assertEqual(mbr[510:], b'\x55\xaa')
            self.assertEqual(struct.unpack_from('<II', mbr, 454), (65536, 458752))
            for name, offset in (('boot0_sdcard.fex', 8192), ('boot_package.fex', 16793600)):
                raw.seek(offset)
                self.assertEqual(raw.read(self.pinned[name]['bytes']), (self.diagnostic / name).read_bytes())
            raw.seek(32 * 1024**2)
            with partition.open('wb') as destination:
                shutil.copyfileobj(raw, destination)
        def extracted(path):
            destination = self.root / 'extracted'
            destination.unlink(missing_ok=True)
            subprocess.run(['debugfs', '-R', f'dump {path} {destination}', str(partition)], check=True, capture_output=True)
            return destination.read_bytes()
        self.assertEqual(extracted('/boot/uImage'), (self.diagnostic / 'uImage').read_bytes())
        self.assertEqual(extracted('/boot/dtb/allwinner/sun60i-a733-orangepi-4-pro.dtb'), (self.diagnostic / 'board.dtb').read_bytes())
        command = (self.repo / 'configs/installer-boot.cmd').read_bytes()
        self.assertEqual(extracted('/boot/boot.cmd'), command)
        self.assertEqual(extracted('/boot/boot.scr')[72:], command)
        initrd = extracted('/boot/uInitrd')
        self.assertEqual(initrd, (self.output / 'uInitrd').read_bytes())
        self.assertEqual(initrd[64:], (self.installer / 'initramfs.cpio.gz').read_bytes())
        self.assertEqual(struct.unpack_from('>I', initrd, 8)[0], int(SD.HOST.EPOCH))
        self.assertEqual(struct.unpack_from('>I', initrd, 24)[0], zlib.crc32(initrd[64:]))
        self.assertEqual(initrd[28:32], bytes((5, 2, 3, 1)))
        self.assertEqual(list(self.output.glob('.staging-*')), [])
        self.storage.assert_called_once_with(self.repo)
        self.packages.assert_called_once_with(self.repo / 'configs/builder-packages.lock.tsv')


if __name__ == '__main__':
    unittest.main()
