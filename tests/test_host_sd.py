"""Authenticated regular-file SD packaging checks; no physical boot claims."""
import copy
import gzip
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zlib

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('assemble_host', REPO / 'build/assemble-host.py')
HOST = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HOST)
VERIFY_SPEC = importlib.util.spec_from_file_location('host_sd_independent_verify', REPO / 'tests/verify_host_sd.py')
VERIFY = importlib.util.module_from_spec(VERIFY_SPEC)
VERIFY_SPEC.loader.exec_module(VERIFY)

TOOLS = ('mkimage', 'sfdisk', 'mkfs.ext4', 'e2fsck', 'debugfs')


class HostSDTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='a733-host-sd-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / 'repo'
        for name in HOST.RECIPES:
            target = self.repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO / name, target)
        (self.repo / 'configs/diagnostic-boot.cmd').write_bytes((REPO / 'configs/diagnostic-boot.cmd').read_bytes())
        self.diagnostic = self.root / 'diagnostic'
        self.host = self.root / 'host'
        self.diagnostic.mkdir()
        self.host.mkdir()
        self.output = self.repo / 'out/host-sd-fixture'
        self.pinned = {}
        for name in HOST.BLOBS:
            data = ('nonbootable-fixture-' + name).encode()
            if name == 'boot_package.fex':
                data = data.ljust(1392640, b'\0')
            (self.diagnostic / name).write_bytes(data)
            self.pinned[name] = HOST.record(data)
        self.manifest = {'schema': 1, 'package_state_profile': 'a733-package-state-v1', 'state_uuid': 'bfe5ba29-3175-47a3-a410-04b87e3de1a6',
                         'kernel_release': HOST.KERNEL_RELEASE, 'boot_inputs': self.pinned,
                         'entries': {name: HOST.record((self.repo / path).read_bytes())
                                     for name, path in HOST.ENTRIES.items()},
                         'files': {'initramfs.cpio.gz': HOST.record(gzip.compress(b'fixture', mtime=0))}}
        (self.host / 'initramfs.cpio.gz').write_bytes(gzip.compress(b'fixture', mtime=0))
        self.write_manifest()
        self.pin_path = self.repo / 'docs/evidence/diagnostic-20261104.json'
        self.pin_path.write_text(json.dumps({'schema': 1, 'files': self.pinned}))
        self.el2_package = b'EL2-fixture'.ljust(1392640, b'\0')
        self.el2_pinned = copy.deepcopy(self.pinned)
        self.el2_pinned['boot_package.fex'] = HOST.record(self.el2_package)
        self.el2_evidence = {'schema': 1, 'files': self.el2_pinned,
                             'handoff': {'expected_service': '0x8000ff40', 'expected_spsr': '0x3c9'}}
        self.el2_path = self.repo / 'docs/evidence/el2-diagnostic-20261104.json'
        self.el2_path.write_text(json.dumps(self.el2_evidence))
        self.addCleanup(patch.stopall)
        patch.object(HOST, 'REPO', self.repo).start()
        patch.object(HOST, 'DIAGNOSTIC_RECORD', self.pin_path).start()
        self.real_validate_packages = HOST.validate_builder_packages
        patch.object(HOST, 'validate_builder_packages', return_value={'fixture': {'version': 'fixture', 'architecture': 'fixture'}}).start()

    def write_manifest(self):
        (self.host / 'manifest.json').write_text(json.dumps(self.manifest))

    def build(self, output=None):
        return HOST.build(self.diagnostic, self.host, output or self.output)

    def test_verifier_requires_explicit_profile_and_evidence_provenance(self):
        with patch.object(VERIFY.HOST, 'REPO', self.repo), patch.object(VERIFY.HOST, 'DIAGNOSTIC_RECORD', self.pin_path):
            for name in HOST.BOOT_PROFILES:
                pinned, profile, recipes = HOST.load_boot_profile(name)
                manifest = {'boot_inputs': pinned, 'boot_profile': profile,
                            'recipes': {path: HOST.record(data) for path, data in recipes.items()}}
                self.assertEqual(VERIFY.verify_boot_profile(manifest, name), pinned)
                historical = copy.deepcopy(manifest)
                del historical['boot_profile']
                if name == 'baseline-el1':
                    self.assertEqual(VERIFY.verify_boot_profile(historical, name), pinned)
                    changed = copy.deepcopy(historical)
                    changed['boot_inputs']['boot_package.fex']['sha256'] = '0' * 64
                    with self.assertRaisesRegex(ValueError, 'input provenance'):
                        VERIFY.verify_boot_profile(changed, name)
                    del historical['recipes'][profile['evidence']['path']]
                    with self.assertRaisesRegex(ValueError, 'evidence recipe'):
                        VERIFY.verify_boot_profile(historical, name)
                else:
                    with self.assertRaisesRegex(ValueError, 'explicit trusted selection'):
                        VERIFY.verify_boot_profile(historical, name)
                for invalid in (None, {}, 'baseline-el1'):
                    changed = copy.deepcopy(manifest)
                    changed['boot_profile'] = invalid
                    with self.subTest(profile=name, invalid=invalid), self.assertRaisesRegex(ValueError, 'explicit trusted selection'):
                        VERIFY.verify_boot_profile(changed, name)
                other = 'experimental-el2' if name == 'baseline-el1' else 'baseline-el1'
                with self.assertRaisesRegex(ValueError, 'explicit trusted selection'):
                    VERIFY.verify_boot_profile(manifest, other)
                changed = copy.deepcopy(manifest)
                del changed['recipes'][profile['evidence']['path']]
                with self.assertRaisesRegex(ValueError, 'evidence recipe'):
                    VERIFY.verify_boot_profile(changed, name)
                changed = copy.deepcopy(manifest)
                changed['boot_inputs']['boot0_sdcard.fex']['sha256'] = '0' * 64
                with self.assertRaisesRegex(ValueError, 'input provenance'):
                    VERIFY.verify_boot_profile(changed, name)

    def test_profile_evidence_is_locked_and_validated_before_output(self):
        for profile in ('unknown', '../experimental-el2'):
            with self.subTest(profile=profile), self.assertRaisesRegex(ValueError, 'unknown boot profile'):
                HOST.build(self.diagnostic, self.host, self.output, boot_profile=profile)
            self.assertFalse(self.output.exists())
        for blob in HOST.BLOBS:
            changed = copy.deepcopy(self.el2_evidence)
            changed['files'][blob]['bytes'] += 1
            self.el2_path.write_text(json.dumps(changed))
            with self.subTest(blob=blob), self.assertRaises(ValueError):
                HOST.build(self.diagnostic, self.host, self.output, boot_profile='experimental-el2')
            self.assertFalse(self.output.exists())
        for handoff in ({}, {'expected_service': '0x8000ff04', 'expected_spsr': '0x3c9'},
                        {'expected_service': '0x8000ff40', 'expected_spsr': '0x3c5'}):
            changed = copy.deepcopy(self.el2_evidence)
            changed['handoff'] = handoff
            self.el2_path.write_text(json.dumps(changed))
            with self.assertRaisesRegex(ValueError, 'handoff'):
                HOST.build(self.diagnostic, self.host, self.output, boot_profile='experimental-el2')
            self.assertFalse(self.output.exists())

    @unittest.skipUnless(all(shutil.which(tool) for tool in TOOLS), 'image assembly tools required')
    def test_experimental_package_requires_explicit_selection(self):
        # The old initramfs pins remain valid: Image/DTB/config have not changed.
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            HOST.build(self.diagnostic, self.host, self.output, boot_profile='experimental-el2')
        shutil.rmtree(self.output)
        (self.diagnostic / 'boot_package.fex').write_bytes(self.el2_package)
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            self.build()
        shutil.rmtree(self.output)
        result = HOST.build(self.diagnostic, self.host, self.output, boot_profile='experimental-el2')
        self.assertEqual(result['boot_inputs'], self.el2_pinned)
        self.assertEqual(result['boot_profile']['name'], 'experimental-el2')
        self.assertEqual(result['boot_profile']['target_service'], '0x8000ff40')
        self.assertTrue(result['boot_profile']['physical_acceptance_pending'])
        evidence = HOST.record(self.el2_path.read_bytes())
        self.assertEqual(result['recipes']['docs/evidence/el2-diagnostic-20261104.json'], evidence)
        self.assertEqual(result['boot_profile']['evidence'],
                         {'path': 'docs/evidence/el2-diagnostic-20261104.json', **evidence})

    def test_manifest_rejects_schema_abi_uuid_and_boot_changes(self):
        for field, values in {'schema': (2, True), 'kernel_release': ('other',),
                              'state_uuid': ('invalid', 'BFE5BA29-3175-47A3-A410-04B87E3DE1A6',
                                             '00000000-0000-0000-0000-000000000000')}.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    changed = copy.deepcopy(self.manifest)
                    changed[field] = value
                    with self.assertRaises(ValueError):
                        HOST.validate_manifest(changed, self.pinned)
        for name in ('Image', 'board.dtb', 'kernel.config'):
            changed = copy.deepcopy(self.manifest)
            changed['boot_inputs'][name]['sha256'] = '0' * 64
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'boot input'):
                HOST.validate_manifest(changed, self.pinned)

    def test_missing_and_changed_current_source_entries(self):
        for name in HOST.ENTRIES:
            for mutation in ('missing', 'hash', 'size'):
                changed = copy.deepcopy(self.manifest)
                if mutation == 'missing':
                    del changed['entries'][name]
                elif mutation == 'hash':
                    changed['entries'][name]['sha256'] = '0' * 64
                else:
                    changed['entries'][name]['bytes'] += 1
                with self.subTest(name=name, mutation=mutation), self.assertRaisesRegex(ValueError, 'entry'):
                    HOST.validate_manifest(changed, self.pinned)
        (self.repo / 'configs/host-init').write_bytes(b'changed since initramfs build')
        with self.assertRaisesRegex(ValueError, 'entry'):
            HOST.validate_manifest(self.manifest, self.pinned)

    def test_invalid_initramfs_record(self):
        for record in ({}, {'bytes': HOST.MAX_INITRAMFS + 1, 'sha256': '0' * 64},
                       {'bytes': 1, 'sha256': 'wrong'}, {'bytes': True, 'sha256': '0' * 64}):
            changed = copy.deepcopy(self.manifest)
            changed['files']['initramfs.cpio.gz'] = record
            with self.subTest(record=record), self.assertRaisesRegex(ValueError, 'initramfs'):
                HOST.validate_manifest(changed, self.pinned)

    def test_wrong_pinned_blob_or_initramfs_is_rejected(self):
        for directory, name in [(self.diagnostic, name) for name in HOST.BLOBS] + [(self.host, 'initramfs.cpio.gz')]:
            path = directory / name
            original = path.read_bytes()
            path.write_bytes(bytes([original[0] ^ 1]) + original[1:])
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'mismatch'):
                self.build()
            path.write_bytes(original)
            if self.output.exists():
                shutil.rmtree(self.output)

    def test_refuse_symlink_device_fifo_and_unsafe_ancestors(self):
        original = self.host / 'manifest.json'
        alias = self.root / 'manifest-alias'
        alias.symlink_to(original)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            HOST.read_regular(alias, 1024**2)
        with self.assertRaisesRegex(ValueError, 'device'):
            HOST.read_regular('/dev/zero', 1024)
        fifo = self.root / 'fifo'
        os.mkfifo(fifo)
        with self.assertRaisesRegex(ValueError, 'regular'):
            HOST.read_regular(fifo, 1024)
        alias.unlink()
        alias.symlink_to(self.host, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            HOST.read_regular(alias / 'manifest.json', 1024**2)
        original.unlink()
        original.symlink_to(self.pin_path)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.build()

    def test_existing_and_invalid_outputs(self):
        self.output.mkdir(parents=True)
        sentinel = self.output / 'sentinel'
        sentinel.write_bytes(b'keep')
        with self.assertRaisesRegex(ValueError, 'existing'):
            self.build()
        self.assertEqual(sentinel.read_bytes(), b'keep')
        for output in (self.root / 'host-sd-invalid', Path('out/host-sd-relative'),
                       self.repo / 'out/host-sd-', Path('/dev/host-sd-invalid')):
            with self.subTest(output=output), self.assertRaises(ValueError):
                self.build(output)
        shutil.rmtree(self.output)
        self.output.symlink_to(self.host, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.build()

    def test_manifest_bounded_duplicate_and_nonregular(self):
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            HOST.json_object(b'{"schema":1,"schema":1}')
        with self.assertRaisesRegex(ValueError, 'object'):
            HOST.json_object(b'[]')
        with self.assertRaisesRegex(ValueError, 'size'):
            HOST.read_regular(self.host / 'manifest.json', 4)
        with self.assertRaisesRegex(ValueError, 'regular'):
            HOST.read_regular(self.host, 1024)

    def test_builder_package_version_and_architecture_guard(self):
        names = ('u-boot-tools', 'e2fsprogs', 'util-linux', 'coreutils', 'bash',
                 'python3.10-minimal', 'libpython3.10-stdlib')
        lock = self.root / 'builder.lock'
        locked = ''.join(f'{name}\t1.0\tamd64\n' for name in names)
        lock.write_text(locked)
        with patch.object(HOST.subprocess, 'check_output', return_value=locked):
            self.assertEqual(set(self.real_validate_packages(lock)), set(names))
        for changed in (locked.replace('1.0', '2.0', 1), locked.replace('amd64', 'arm64', 1)):
            with patch.object(HOST.subprocess, 'check_output', return_value=changed):
                with self.assertRaisesRegex(ValueError, 'differ from lock'):
                    self.real_validate_packages(lock)
        lock.write_text('bash\t1.0\tamd64\n')
        with self.assertRaisesRegex(ValueError, 'lacks required'):
            self.real_validate_packages(lock)

    def test_ram_staging_refused(self):
        with patch.object(HOST.subprocess, 'check_output', return_value='tmpfs\n'):
            with self.assertRaisesRegex(ValueError, 'not RAM'):
                self.build()
        self.assertFalse(self.output.exists())

    def test_host_protocol_preserves_diagnostic_commands(self):
        def commands(name):
            return [line for line in (REPO / 'configs' / name).read_text().splitlines()
                    if line and not line.startswith('#') and not line.startswith('echo ')]
        self.assertEqual(commands('host-boot.cmd'), commands('diagnostic-boot.cmd'))

    @unittest.skipUnless(all(shutil.which(tool) for tool in TOOLS), 'image assembly tools required')
    def test_actual_regular_file_packaging(self):
        result = self.build()
        image = self.output / 'a733-host-sd.img'
        self.assertEqual(image.stat().st_size, 256 * 1024**2)
        self.assertEqual(result['trusted_host_manifest'], {
            'path': str(self.host / 'manifest.json'), **HOST.record((self.host / 'manifest.json').read_bytes())})
        self.assertEqual(result['host_initramfs']['sha256'], self.manifest['files']['initramfs.cpio.gz']['sha256'])
        self.assertEqual(result['state_uuid'], self.manifest['state_uuid'])
        for name, expected in result['files'].items():
            self.assertEqual(HOST.file_record(self.output / name), expected)
        with gzip.open(self.output / 'a733-host-sd.img.gz', 'rb') as compressed, image.open('rb') as raw:
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
            subprocess.run(['debugfs', '-R', f'dump {path} {destination}', str(partition)],
                           check=True, capture_output=True)
            return destination.read_bytes()
        self.assertEqual(extracted('/boot/uImage'), (self.diagnostic / 'uImage').read_bytes())
        self.assertEqual(extracted('/boot/dtb/allwinner/sun60i-a733-orangepi-4-pro.dtb'),
                         (self.diagnostic / 'board.dtb').read_bytes())
        command = (self.repo / 'configs/host-boot.cmd').read_bytes()
        self.assertEqual(extracted('/boot/boot.cmd'), command)
        script = extracted('/boot/boot.scr')
        self.assertEqual(script[72:], command)
        self.assertEqual(struct.unpack_from('>I', script, 24)[0], zlib.crc32(script[64:]))
        initrd = extracted('/boot/uInitrd')
        self.assertEqual(initrd, (self.output / 'uInitrd').read_bytes())
        self.assertEqual(initrd[64:], (self.host / 'initramfs.cpio.gz').read_bytes())
        self.assertEqual(struct.unpack_from('>I', initrd, 8)[0], int(HOST.EPOCH))
        self.assertEqual(initrd[28:31], bytes((5, 2, 3)))  # Linux, ARM, ramdisk.
        self.assertEqual(initrd[31], 1)  # gzip legacy convention.
        self.assertIn('no physical boot', result['classification'])

    def test_explicit_boot_command_refuses_symlink(self):
        alias = self.root / 'boot.cmd'
        alias.symlink_to(self.repo / 'configs/host-boot.cmd')
        (self.diagnostic / 'uInitrd').write_bytes(b'fixture')
        result = subprocess.run(['bash', str(self.repo / 'build/assemble-diagnostic.sh'),
                                 str(self.diagnostic), str(self.root / 'bad.img'), str(alias)],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Boot command must', result.stderr)
        self.assertFalse((self.root / 'bad.img').exists())


if __name__ == '__main__':
    unittest.main()
