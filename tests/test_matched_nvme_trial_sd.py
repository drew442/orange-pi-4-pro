"""Synthetic regular-file construction tests; no devices or hardware acceptance."""
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).absolute().parents[1]
SPEC = importlib.util.spec_from_file_location('matched_trial', ROOT / 'build/matched-nvme-trial-sd.py')
P = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(P)


def pin(data):
    return len(data), hashlib.sha256(data).hexdigest()


def fixture_parent():
    parent = ROOT / 'tmp'
    parent.mkdir(mode=0o700, exist_ok=True)
    with P.HeldPath(parent, directory=True):
        pass
    return parent


class MatchedTrialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='matched-trial-test-', dir=fixture_parent())
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'out').mkdir()
        self.base_bytes = bytes(range(256)) * 512
        self.spi_bytes = b'SPI boot0 MUST NEVER COPY' * 128 + b'P' * 8192
        self.base = self.root / 'base.img'
        self.spi = self.root / 'spi.bin'
        self.base.write_bytes(self.base_bytes)
        self.spi.write_bytes(self.spi_bytes)
        self.start, self.sd_start, self.length = 4096, 16384, 4096
        self.package = self.spi_bytes[self.start:self.start + self.length]
        registry = {'targets': {P.TARGET: {'required_artifacts': {'artifacts/boot_package.fex': P.record(*pin(self.package))}}}}
        reg = self.root / 'registry.json'
        reg.write_text(json.dumps(registry))
        self.patches = {
            'REPO': self.root, 'BASE': pin(self.base_bytes), 'SPI': pin(self.spi_bytes),
            'DISK_FLOOR': 1024, 'SCRATCH': 1024, 'MEMORY_FLOOR': 1024, 'CHUNK': 1024,
            'PACKAGE': pin(self.package), 'SPI_START': self.start, 'SD_START': self.sd_start,
            'BOOT_START': 32768, 'CLOSURE': {'registry.json': pin(reg.read_bytes())},
        }
        patcher = mock.patch.multiple(P, **self.patches)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.output = self.root / 'out/matched-nvme-trial-sd-one'

    def run_build(self, output=None):
        return P.produce(self.base, self.spi, output or self.output)

    def test_fixture_parent_in_fresh_checkout_preserves_existing_permissions(self):
        checkout = self.root / 'fresh-checkout'
        checkout.mkdir()
        with mock.patch(__name__ + '.ROOT', checkout):
            parent = fixture_parent()
            self.assertEqual(parent, checkout / 'tmp')
            self.assertEqual(parent.stat().st_mode & 0o777, 0o700)
            parent.chmod(0o750)
            self.assertEqual(fixture_parent().stat().st_mode & 0o777, 0o750)
            parent.rmdir()
            nested = MatchedTrialTests('test_complete_exact_preservation_deterministic_gzip_and_receipt')
            result = unittest.TestResult()
            nested.run(result)
            self.assertEqual(result.testsRun, 1)
            self.assertEqual(result.errors, [])
            self.assertEqual(result.failures, [])
            self.assertEqual(parent.stat().st_mode & 0o777, 0o700)

    def test_complete_exact_preservation_deterministic_gzip_and_receipt(self):
        result = self.run_build()
        raw = (self.output / 'a733-host-sd.img').read_bytes()
        expected = self.base_bytes[:self.sd_start] + self.package + self.base_bytes[self.sd_start + self.length:]
        self.assertEqual(raw, expected)
        self.assertEqual(raw[:4096], self.base_bytes[:4096])
        self.assertEqual(raw[32768:], self.base_bytes[32768:])
        compressed = (self.output / 'a733-host-sd.img.gz').read_bytes()
        self.assertEqual(gzip.decompress(compressed), expected)
        self.assertEqual(compressed[3:8], b'\0' * 5)
        second = self.root / 'out/matched-nvme-trial-sd-two'
        self.run_build(second)
        self.assertEqual(compressed, (second / 'a733-host-sd.img.gz').read_bytes())
        self.assertFalse(result['hardware_acceptance'])
        self.assertEqual(result['status'], 'UNBOOTED')
        self.assertEqual(result['outputs']['a733-host-sd.img'], P.record(*pin(expected)))
        self.assertEqual(json.loads((self.output / 'manifest.json').read_bytes()), result)
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.output / 'manifest.json').stat().st_mode & 0o777, 0o600)
        outside = expected[:self.sd_start] + expected[self.sd_start + self.length:]
        self.assertEqual(result['preservation']['concatenated_sha256'], hashlib.sha256(outside).hexdigest())

    def test_wrong_input_size_and_hash_before_output_creation(self):
        for content in (b'bad', b'X' + self.base_bytes[1:]):
            self.base.write_bytes(content)
            with self.assertRaisesRegex(ValueError, 'pin mismatch'):
                self.run_build()
            self.assertFalse(self.output.exists())

    def test_wrong_package_pin_and_registry_binding(self):
        with mock.patch.object(P, 'PACKAGE', (self.length, '0' * 64)):
            with self.assertRaisesRegex(ValueError, 'package pin'):
                self.run_build()
        registry = self.root / 'registry.json'
        bad = json.loads(registry.read_text())
        bad['targets'][P.TARGET]['required_artifacts']['artifacts/boot_package.fex']['sha256'] = '0' * 64
        registry.write_text(json.dumps(bad))
        with mock.patch.object(P, 'CLOSURE', {'registry.json': pin(registry.read_bytes())}):
            with self.assertRaisesRegex(ValueError, 'registered package'):
                self.run_build()

    def test_pinned_source_closure_rejects_changed_evidence(self):
        (self.root / 'registry.json').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'pin mismatch'):
            self.run_build()
        self.assertFalse(self.output.exists())

    def test_symlink_hardlink_fifo_and_device_paths_refused(self):
        link = self.root / 'link'
        link.symlink_to(self.base)
        with self.assertRaises(OSError):
            P.HeldPath(link)
        link.unlink()
        os.link(self.base, link)
        with self.assertRaisesRegex(ValueError, 'hardlinked'):
            P.HeldPath(self.base)
        fifo = self.root / 'fifo'
        os.mkfifo(fifo)
        with self.assertRaisesRegex(ValueError, 'regular file'):
            P.HeldPath(fifo)
        for path in ('/dev/null', '/proc/version', '/sys/kernel', 'relative', '/home/../etc/passwd'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                P.HeldPath(path)

    def test_ancestor_symlink_world_writable_and_owner_refused(self):
        parent = self.root / 'parent'
        parent.mkdir()
        (parent / 'file').write_bytes(b'x')
        link = self.root / 'parent-link'
        link.symlink_to(parent, target_is_directory=True)
        with self.assertRaises(OSError):
            P.HeldPath(link / 'file')
        parent.chmod(0o777)
        with self.assertRaisesRegex(ValueError, 'world-writable'):
            P.HeldPath(parent / 'file')
        parent.chmod(0o700)
        with mock.patch.object(P, 'TRUSTED_UIDS', set()):
            with self.assertRaisesRegex(ValueError, 'owner'):
                P.HeldPath(self.base)

    def test_source_mutation_and_ancestor_drift(self):
        with P.HeldPath(self.base) as held:
            self.base.write_bytes(b'Z' + self.base_bytes[1:])
            with self.assertRaisesRegex(ValueError, 'mutation'):
                held.check()
        parent = self.root / 'parent'
        parent.mkdir()
        file = parent / 'file'
        file.write_bytes(b'x')
        with P.HeldPath(file) as held:
            parent.rename(self.root / 'moved')
            parent.mkdir()
            (parent / 'file').write_bytes(b'x')
            with self.assertRaisesRegex(ValueError, 'drift'):
                held.check()

    def test_mid_build_source_mutation_leaves_no_receipt(self):
        original = P.resources
        def mutate(fd, phase, admission=False):
            result = original(fd, phase, admission)
            if phase == 'gzip':
                self.base.write_bytes(b'Z' + self.base_bytes[1:])
            return result
        with mock.patch.object(P, 'resources', side_effect=mutate):
            with self.assertRaisesRegex(ValueError, 'mutation'):
                self.run_build()
        self.assertFalse((self.output / 'manifest.json').exists())

    def test_existing_output_and_output_scope_refused(self):
        self.output.mkdir()
        with self.assertRaises(FileExistsError):
            self.run_build()
        for output in (self.root / 'elsewhere', self.root / 'out/wrong-prefix', Path('/tmp/matched-nvme-trial-sd-x')):
            with self.subTest(output=output), self.assertRaisesRegex(ValueError, 'output must'):
                self.run_build(output)

    def test_disk_memory_admission_and_runtime_floors(self):
        with P.HeldPath(self.root, True) as held:
            fs = os.fstatvfs(held.fd)
            free = fs.f_bavail * fs.f_frsize
            with mock.patch.object(P, 'DISK_FLOOR', free + 1):
                with self.assertRaisesRegex(ValueError, 'disk'):
                    P.resources(held.fd, 'runtime')
            with mock.patch.object(P, 'SCRATCH', free + 1):
                with self.assertRaisesRegex(ValueError, 'disk'):
                    P.resources(held.fd, 'admission', True)
            with mock.patch.object(P, 'MEMORY_FLOOR', 10**30):
                with self.assertRaisesRegex(ValueError, 'MemAvailable'):
                    P.resources(held.fd, 'memory')

    def test_gzip_crc_and_expansion_limits(self):
        file = self.root / 'gzip'
        file.write_bytes(gzip.compress(b'x' * 10000))
        with P.HeldPath(file) as held:
            with self.assertRaisesRegex(ValueError, 'expansion bound'):
                P.verify_gzip(held.fd, pin(b'x'))
        damaged = bytearray(gzip.compress(b'x' * 10000))
        damaged[-8] ^= 1
        file.write_bytes(damaged)
        with P.HeldPath(file) as held:
            with self.assertRaises((OSError, ValueError)):
                P.verify_gzip(held.fd, pin(b'x' * 10000))

    def test_final_receipt_failure_removes_receipt(self):
        original = P.resources
        def fail(fd, phase, admission=False):
            if phase == 'publication':
                raise ValueError('final floor failure')
            return original(fd, phase, admission)
        with mock.patch.object(P, 'resources', side_effect=fail):
            with self.assertRaisesRegex(ValueError, 'final floor'):
                self.run_build()
        self.assertTrue((self.output / 'a733-host-sd.img').exists())
        self.assertFalse((self.output / 'manifest.json').exists())

    def test_output_ancestor_drift_stops_without_receipt(self):
        original = P.resources
        def drift(fd, phase, admission=False):
            result = original(fd, phase, admission)
            if phase == 'gzip':
                self.output.rename(self.output.with_name('moved-output'))
                self.output.mkdir()
            return result
        with mock.patch.object(P, 'resources', side_effect=drift):
            with self.assertRaisesRegex(ValueError, 'drift'):
                self.run_build()
        self.assertFalse((self.output / 'manifest.json').exists())
        self.assertFalse((self.output.with_name('moved-output') / 'manifest.json').exists())

    def test_final_output_mutation_stops_receipt(self):
        original = P.resources
        def mutate(fd, phase, admission=False):
            result = original(fd, phase, admission)
            if phase == 'final':
                with (self.output / 'a733-host-sd.img.gz').open('r+b') as stream:
                    stream.write(b'bad')
            return result
        with mock.patch.object(P, 'resources', side_effect=mutate):
            with self.assertRaisesRegex(ValueError, 'output mutation'):
                self.run_build()
        self.assertFalse((self.output / 'manifest.json').exists())

    def test_manifest_fsync_failure_removes_receipt(self):
        original = os.fsync
        def fail(fd):
            # Identify the newly written regular receipt through its JSON bytes.
            try:
                if os.pread(fd, 1, 0) == b'{':
                    raise OSError('receipt fsync failure')
            except IsADirectoryError:
                pass
            return original(fd)
        with mock.patch.object(P.os, 'fsync', side_effect=fail):
            with self.assertRaisesRegex(OSError, 'receipt fsync'):
                self.run_build()
        self.assertFalse((self.output / 'manifest.json').exists())

    def test_preservation_checker_catches_every_region(self):
        raw = self.root / 'raw'
        expected = self.base_bytes[:self.sd_start] + self.package + self.base_bytes[self.sd_start + self.length:]
        for index in (0, 8192, self.sd_start, self.sd_start + self.length, 32768, len(expected) - 1):
            bad = bytearray(expected)
            bad[index] ^= 1
            raw.write_bytes(bad)
            with P.HeldPath(self.base) as base, P.HeldPath(self.spi) as spi, P.HeldPath(raw) as output:
                with self.subTest(index=index), self.assertRaisesRegex(ValueError, 'byte mismatch'):
                    P.compare(base.fd, output.fd, spi.fd)


if __name__ == '__main__':
    unittest.main()
