"""File-only SPI assembly fixtures; these do not establish physical backup/boot."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('assemble_spi_nvme', REPO / 'build/assemble-spi-nvme.py')
spi = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(spi)


class AssemblyTests(unittest.TestCase):
    def setUp(self):
        (REPO / 'out').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='spi-nvme-tests-', dir=REPO / 'out')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.backup = self.root / 'synthetic-backup'
        self.components = self.root / 'synthetic-components'
        self.backup.mkdir()
        (self.components / 'artifacts').mkdir(parents=True)
        self.output = REPO / 'out' / ('spi-nvme-synthetic-' + self.root.name)
        self.addCleanup(lambda: __import__('shutil').rmtree(self.output, ignore_errors=True))
        self.baseline = bytes(range(256)) * (spi.SIZE // 256)
        (self.backup / 'spi.bin').write_bytes(self.baseline)
        self.manifest = dict(status='verified-local-ram-backup', **spi.pin(self.baseline),
                             source_reads=2, image='spi.bin', off_device_retention_verified=False,
                             recovery_proven=False, source=dict(device='/dev/mtd0', dev='90:0',
                             sysfs='/sys/devices/platform/spi/spi0/spi0.0/mtd/mtd0',
                             spi='/sys/devices/platform/spi/spi0/spi0.0', partname='XM25QU128C',
                             jedec_id='204118', sysfs_geometry=dict(name='spi0.0', type='nor',
                             size=str(spi.SIZE), erasesize=str(spi.ERASE), writesize='1'),
                             proc_mtd='dev:    size   erasesize  name\nmtd0: 01000000 00010000 "spi0.0"\n',
                             ioctl=dict(type=3, flags=1024, size=spi.SIZE, erasesize=spi.ERASE,
                                        writesize=1, oobsize=0)))
        self.write_backup_manifest()
        files = {}
        self.blobs = []
        for index, (name, _, length) in enumerate(spi.LAYOUT):
            blob = bytes([17 + index]) * length
            self.blobs.append(blob)
            (self.components / name).write_bytes(blob)
            files[name] = spi.pin(blob)
        raw = json.dumps(dict(schema=1, hardware_acceptance=False, files=files)).encode()
        (self.components / 'candidate-manifest.json').write_bytes(raw)
        self.target = dict(manifest=spi.pin(raw), required_artifacts=files)
        # Only source authentication is replaced: no fixtures depend on ignored artifacts.
        self.source_patch = patch.object(spi, 'authenticate_sources', return_value=self.target)
        self.source_patch.start()
        self.addCleanup(self.source_patch.stop)

    def write_backup_manifest(self):
        (self.backup / 'manifest.json').write_text(json.dumps(self.manifest))

    def run_assembly(self, plan=False):
        return spi.assemble(self.backup, self.components, self.output, plan)

    def test_preserve_every_unreplaced_byte_and_erase_unit(self):
        result = self.run_assembly()
        data = (self.output / 'a733-spi-nvme.bin').read_bytes()
        self.assertEqual(len(data), spi.SIZE)
        self.assertEqual(data[:237568], self.blobs[0])
        self.assertEqual(data[262144:1654784], self.blobs[1])
        self.assertEqual(data[237568:262144], self.baseline[237568:262144])
        self.assertEqual(data[1654784:], self.baseline[1654784:])
        self.assertEqual(result['candidate'], spi.pin(data))
        self.assertEqual(result['affected_erase_units'], list(range(26)))
        self.assertEqual(result['affected_erase_end'], 1703936)
        self.assertEqual(len(result['erase_units']), 256)
        self.assertTrue(all(unit['changed'] == (unit['index'] < 26) for unit in result['erase_units']))
        for unit in result['erase_units']:
            self.assertEqual(unit['original_sha256'], spi.pin(self.baseline[unit['offset']:unit['end']])['sha256'])
            self.assertEqual(unit['candidate_sha256'], spi.pin(data[unit['offset']:unit['end']])['sha256'])
        for name in ('deployment_authorized', 'hardware_acceptance', 'physical_writes', 'recovery_proven'):
            self.assertIs(result[name], False)
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o700)
        for name in ('manifest.json', 'a733-spi-nvme.bin'):
            self.assertEqual((self.output / name).stat().st_mode & 0o777, 0o600)

    def test_plan_creates_nothing(self):
        self.assertEqual(self.run_assembly(True)['stage'], 'validated-file-only-plan')
        self.assertFalse(self.output.exists())

    def test_existing_output_never_clobbered(self):
        self.output.mkdir()
        sentinel = self.output / 'sentinel'
        sentinel.write_text('keep')
        with self.assertRaises(ValueError):
            self.run_assembly()
        self.assertEqual(sentinel.read_text(), 'keep')

    def test_baseline_size_and_hash(self):
        for data in (self.baseline[:-1], b'X' + self.baseline[1:]):
            with self.subTest(length=len(data)):
                (self.backup / 'spi.bin').write_bytes(data)
                with self.assertRaises(ValueError):
                    self.run_assembly()
        self.assertFalse(self.output.exists())

    def test_identity_and_geometry_fail_closed(self):
        for field, value in (('jedec_id', '000000'), ('partname', 'other'),
                             ('proc_mtd', self.manifest['source']['proc_mtd'] + 'mtd1: other\n'),
                             ('sysfs', '/sys/devices/other/mtd/mtd0')):
            with self.subTest(field=field):
                original = self.manifest['source'][field]
                self.manifest['source'][field] = value
                self.write_backup_manifest()
                with self.assertRaises(ValueError):
                    self.run_assembly()
                self.manifest['source'][field] = original
        self.assertFalse(self.output.exists())

    def test_untrusted_component_parent_or_blob(self):
        parent = self.components / 'candidate-manifest.json'
        original = parent.read_bytes()
        parent.write_bytes(original + b' ')
        with self.assertRaises(ValueError):
            self.run_assembly()
        parent.write_bytes(original)
        path = self.components / spi.LAYOUT[0][0]
        path.write_bytes(b'Z' * spi.LAYOUT[0][2])
        with self.assertRaises(ValueError):
            self.run_assembly()

    def test_link_fifo_and_symlink_ancestor_refused(self):
        image = self.backup / 'spi.bin'
        saved = self.backup / 'saved'
        image.rename(saved)
        for kind in ('symlink', 'hardlink', 'fifo'):
            with self.subTest(kind=kind):
                if kind == 'symlink':
                    image.symlink_to(saved)
                elif kind == 'hardlink':
                    os.link(saved, image)
                else:
                    os.mkfifo(image)
                with self.assertRaises((ValueError, OSError)):
                    self.run_assembly()
                image.unlink()
        saved.rename(image)
        link = self.root / 'linked-backup'
        link.symlink_to(self.backup, target_is_directory=True)
        with self.assertRaises(OSError):
            spi.assemble(link, self.components, self.output, True)

    def test_pseudo_and_noncanonical_paths(self):
        for value in ('/dev/null', '/proc/self/fd/0', '/sys/test', '/tmp/../etc/x', '/tmp//file', 'relative'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                spi.canonical(value)

    def test_duplicate_keys_and_extra_backup_keys(self):
        (self.backup / 'manifest.json').write_bytes(b'{"status":1,"status":2}')
        with self.assertRaises(ValueError):
            self.run_assembly()
        self.manifest['extra'] = True
        self.write_backup_manifest()
        with self.assertRaises(ValueError):
            self.run_assembly()

    def test_input_mutation_before_publication(self):
        original = spi.publish
        def mutate(inputs, parent, output, candidate, result):
            (self.backup / 'spi.bin').write_bytes(b'X' * spi.SIZE)
            original(inputs, parent, output, candidate, result)
        with patch.object(spi, 'publish', side_effect=mutate), self.assertRaises(ValueError):
            self.run_assembly()
        self.assertFalse((self.output / 'manifest.json').exists())

    def test_failed_publication_has_no_success_manifest(self):
        original = spi.write_file
        def fail(directory, name, data):
            if name == 'manifest.json.partial':
                raise OSError('fixture publication failure')
            original(directory, name, data)
        with patch.object(spi, 'write_file', side_effect=fail), self.assertRaises(OSError):
            self.run_assembly()
        self.assertFalse((self.output / 'manifest.json').exists())

    def test_written_image_corruption_prevents_success_manifest(self):
        original = spi.write_file
        def corrupt(directory, name, data):
            original(directory, name, data)
            if name == 'a733-spi-nvme.bin':
                fd = os.open(name, os.O_WRONLY, dir_fd=directory)
                try:
                    os.pwrite(fd, b'corrupted', 0)
                finally:
                    os.close(fd)
        with patch.object(spi, 'write_file', side_effect=corrupt), self.assertRaises(ValueError):
            self.run_assembly()
        self.assertFalse((self.output / 'manifest.json').exists())

    def test_late_image_mutation_prevents_success_manifest(self):
        original = spi.write_file
        def corrupt(directory, name, data):
            original(directory, name, data)
            if name == 'manifest.json.partial':
                fd = os.open('a733-spi-nvme.bin', os.O_WRONLY, dir_fd=directory)
                try:
                    os.pwrite(fd, b'late corruption', 0)
                finally:
                    os.close(fd)
        with patch.object(spi, 'write_file', side_effect=corrupt), self.assertRaises(ValueError):
            self.run_assembly()
        self.assertFalse((self.output / 'manifest.json').exists())

    def test_directory_replacement_detected(self):
        inputs = spi.Inputs()
        try:
            inputs.read(self.backup / 'manifest.json', 1024 * 1024)
            self.backup.rename(self.root / 'old-backup')
            self.backup.mkdir()
            with self.assertRaises(ValueError):
                inputs.recheck()
        finally:
            inputs.close()


class SourceAuthenticationTests(unittest.TestCase):
    def test_committed_registry_and_evidence(self):
        inputs = spi.Inputs()
        try:
            target = spi.authenticate_sources(inputs)
            self.assertEqual(target['manifest']['bytes'], 11439)
            inputs.recheck()
        finally:
            inputs.close()

    def test_wrong_registry_pin_refused(self):
        inputs = spi.Inputs()
        try:
            with patch.object(spi, 'REGISTRY_PIN', dict(bytes=4035, sha256='0' * 64)):
                with self.assertRaises(ValueError):
                    spi.authenticate_sources(inputs)
        finally:
            inputs.close()

    def test_registry_must_equal_committed_git(self):
        inputs = spi.Inputs()
        try:
            with patch.object(spi, 'git', return_value=b'not the registry'):
                with self.assertRaisesRegex(ValueError, 'registry differs'):
                    spi.authenticate_sources(inputs)
        finally:
            inputs.close()

    def test_evidence_must_equal_committed_git(self):
        inputs = spi.Inputs()
        real_git = spi.git
        def wrong_evidence(*args):
            if args[-1].endswith('uboot-nvme-candidate-20261107.json'):
                return b'changed evidence'
            return real_git(*args)
        try:
            with patch.object(spi, 'git', side_effect=wrong_evidence):
                with self.assertRaisesRegex(ValueError, 'evidence differs'):
                    spi.authenticate_sources(inputs)
        finally:
            inputs.close()


if __name__ == '__main__':
    unittest.main()
