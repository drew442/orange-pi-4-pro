"""Tiny regular-file fixtures; never decode/build a production NVMe image."""
import copy
import gzip
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('vgic_nvme', ROOT / 'build/vgic-nvme-image.py')
V = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(V)
F = V.load('vgic_fixtures', 'tests/test_vgic_runtime_bootstrap.py')


class Quiet:
    def check(self, *args, **kwargs):
        pass


class NvmeDerivationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='vgic-nvme-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def file(self, name, data):
        path = self.root / name
        path.write_bytes(data)
        return path

    def decode(self, encoded, data, bound=None):
        path = self.file('stream.gz', encoded)
        target = self.root / 'new.raw'
        target.unlink(missing_ok=True)
        fd = V.new_regular(target)
        try:
            with open(path, 'rb') as stream:
                outside = V.decode(stream.fileno(), fd, bound or V.VGIC.HOST.record(data), Quiet())
            return target.read_bytes(), outside
        finally:
            os.close(fd)

    def test_bounded_decode_sparse_crc_and_full_hash(self):
        data = b'front' + bytes(100000) + b'last'
        with mock.patch.multiple(V, BOOT_START=8, BOOT_BYTES=32, CHUNK=1024):
            raw, outside = self.decode(gzip.compress(data, mtime=0), data)
            self.assertEqual(raw, data)
            self.assertEqual(outside, V.VGIC.HOST.record(data[:8] + data[40:]))
            for bad in (gzip.compress(data)[:-2], gzip.compress(data) + b'junk',
                        gzip.compress(data) + gzip.compress(b'extra')):
                with self.subTest(bad=len(bad)), self.assertRaises((ValueError, V.zlib.error)):
                    self.decode(bad, data)
            broken = bytearray(gzip.compress(data)); broken[-8] ^= 1
            with self.assertRaises(V.zlib.error):
                self.decode(broken, data)
            with self.assertRaisesRegex(ValueError, 'hash/size'):
                self.decode(gzip.compress(data), data, {'bytes': len(data), 'sha256': 'bad'})
            with self.assertRaisesRegex(ValueError, 'exceeds raw bound'):
                self.decode(gzip.compress(data), data, {'bytes': 3, 'sha256': 'unused'})

    def test_compression_outside_proof_determinism_and_mutation(self):
        data = b'front___BOOT____tail'
        source = self.file('raw', data)
        with mock.patch.multiple(V, BOOT_START=8, BOOT_BYTES=8, CHUNK=3):
            expected = V.VGIC.HOST.record(data[:8] + data[16:])
            one, two = self.root / 'one.gz', self.root / 'two.gz'
            self.assertEqual(V.compress(source, one, Quiet(), expected), V.VGIC.HOST.record(data))
            V.compress(source, two, Quiet(), expected)
            self.assertEqual(one.read_bytes(), two.read_bytes())
            changed = bytearray(data); changed[9] ^= 1
            source.write_bytes(changed)
            V.compress(source, self.root / 'boot-change.gz', Quiet(), expected)
            changed[-1] ^= 1; source.write_bytes(changed)
            with self.assertRaisesRegex(ValueError, 'outside BOOT'):
                V.compress(source, self.root / 'bad.gz', Quiet(), expected)

    def test_input_safety_and_stability(self):
        file = self.file('input', b'base')
        link = self.root / 'alias'; link.symlink_to(file)
        for path in (link, Path('/proc/meminfo'), Path('/dev/null'), Path('relative'), self.root / '..' / 'input'):
            with self.subTest(path=path), self.assertRaises((ValueError, OSError)):
                V.RUNTIME.HeldInput(path, 10)
        with V.RUNTIME.HeldInput(file, 10) as held:
            file.write_bytes(b'evil')
            with self.assertRaisesRegex(ValueError, 'changed'):
                held.verify()
        hard = self.root / 'hard'; os.link(file, hard)
        with self.assertRaisesRegex(ValueError, 'hardlinks'):
            V.RUNTIME.HeldInput(file, 10)

    def test_new_raw_exclusive_and_writeback_punch_bounds(self):
        raw = self.file('raw', b'A' * 16384)
        with self.assertRaises(FileExistsError):
            V.new_regular(raw)
        boot = self.file('boot', bytes(4096))
        fd = os.open(raw, os.O_RDWR)
        try:
            with mock.patch.multiple(V, BOOT_START=4096, BOOT_BYTES=4096):
                V.replace_boot(fd, boot)
                self.assertEqual(raw.read_bytes(), b'A' * 4096 + bytes(4096) + b'A' * 8192)
            with mock.patch.multiple(V, BOOT_START=15000, BOOT_BYTES=4096):
                with self.assertRaisesRegex(ValueError, 'bounds'):
                    V.replace_boot(fd, boot)
        finally:
            os.close(fd)

    def test_resource_admission_and_runtime(self):
        self.assertGreaterEqual(V.ADMISSION, 23760999827)
        self.assertEqual(V.NATIVE_FLOOR, 268435456)
        with mock.patch.object(V.shutil, 'disk_usage', return_value=mock.Mock(free=V.ADMISSION - 1)):
            with self.assertRaisesRegex(ValueError, 'initial admission'):
                V.build('/missing', '/missing', '/missing', '/missing', 'fresh')
        with mock.patch.object(V.shutil, 'disk_usage', return_value=mock.Mock(free=V.DISK_FLOOR - 1)):
            with self.assertRaisesRegex(ValueError, 'runtime disk/memory'):
                V.Monitor(self.root).check('test', force=True)
        monitor = V.Monitor(self.root)
        monitor.started -= V.TIMEOUT + 1
        with self.assertRaisesRegex(ValueError, 'timeout'):
            monitor.check('test')
        with mock.patch.object(V.shutil, 'disk_usage', return_value=mock.Mock(free=V.ADMISSION)), \
                mock.patch.object(V.Path, 'read_text', return_value='MemAvailable: 1 kB\n'):
            with self.assertRaisesRegex(ValueError, 'runtime disk/memory'):
                V.Monitor(self.root).check('test', force=True)

    def test_semantic_change_rejects_extra_property(self):
        V.VGIC.verify_delta(F.fdt(), F.fdt(patched=True))
        with self.assertRaisesRegex(ValueError, 'semantic delta'):
            V.VGIC.verify_delta(F.fdt(), F.fdt(patched=True, extra=True))

    def test_base_and_paired_manifest_policy_preserves_original_labels(self):
        base = {'state_uuid': V.RUNTIME.STATE_UUID, 'files': {'nvme.img': V.RAW},
                'boot_files': V.BOOT_FILES, 'inputs': {'sd_manifest': V.RUNTIME.MANIFEST_RECORD},
                'boot_profile': {'name': 'experimental-el2'}}
        paired = {'state_uuid': V.RUNTIME.STATE_UUID,
                  'base_bootstrap': {'manifest': V.RUNTIME.MANIFEST_RECORD},
                  'files': {'a733-host-sd.img': V.SD},
                  'boot_inputs': {'board.dtb': V.RUNTIME.PATCHED_DTB_RECORD},
                  'boot_profile': {'name': V.RUNTIME.PROFILE}}
        original = copy.deepcopy(base)
        V.validate_manifests(base, paired)
        self.assertEqual(base, original)
        for key, value in (('state_uuid', 'old-state'),
                           ('inputs', {'sd_manifest': V.SD_MANIFEST}),
                           ('boot_profile', {'name': V.RUNTIME.PROFILE})):
            broken = copy.deepcopy(base); broken[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                V.validate_manifests(broken, paired)

    def test_derived_metadata_retains_installer_identity_without_aliasing(self):
        # Consumer validation needs a real layout and nonfixture identity, but no image bytes.
        installer = V.load('vgic_installer_consumer', 'scripts/install-nvme.py')
        layout = installer.LAYOUT.layout(V.RAW['bytes'], 512, 512)
        base = {key: {} for key in ('disk_guid', 'partition_guids', 'filesystem_uuids',
                                    'gpt_extents', 'boot_selection', 'partition_payloads')}
        base.update(schema=1, state_uuid=V.RUNTIME.STATE_UUID, kernel_release='6.6.98-a733-diag',
                    logical_sector_bytes=512, physical_sector_bytes=512, layout=layout,
                    deployment_intent={'for_installation': True, 'management_mac': '10:f4:ae:90:5e:57',
                                       'management_cidr': '0.0.0.0/0',
                                       'administration_public_key_fingerprints': ['SHA256:' + 'A' * 43]})
        result = V.preserved_metadata(base)
        result['files'] = {'nvme.img': V.RAW}
        self.assertEqual(installer.validate_manifest(result), V.RAW)
        result['deployment_intent']['management_mac'] = 'changed'
        self.assertEqual(base['deployment_intent']['management_mac'], '10:f4:ae:90:5e:57')

    def test_sd_extraction_uses_bounded_deep_dtb_reader(self):
        destination = self.root / 'sd'
        with mock.patch.object(V.VGIC, 'copy_region') as copy_region, \
                mock.patch.object(V.INSPECT, 'debugfs', return_value=b'dtb') as debugfs:
            # The real copy creates a scratch file, which the helper removes afterward.
            copy_region.side_effect = lambda source, target, offset, length: target.touch()
            result = V.extract_sd_dtb(self.root / 'sd.img', destination)
        self.assertEqual(result.read_bytes(), b'dtb')
        self.assertEqual(debugfs.call_args.args[1], 'cat /' + V.DTB_PATH)
        self.assertEqual(debugfs.call_args.args[2], V.RUNTIME.PATCHED_DTB_RECORD['bytes'])

    def make_boot(self):
        seed = self.root / 'seed'; seed.mkdir()
        for directory in sorted(V.DIRECTORIES - {'.'}, key=len):
            (seed / directory).mkdir()
        for path in V.BOOT_FILES:
            (seed / path).write_bytes(F.fdt() if path == V.DTB_PATH else path.encode())
        image = self.root / 'boot.ext4'
        with open(image, 'xb') as stream:
            stream.truncate(16 * 1024**2)
        subprocess.run(['mke2fs', '-q', '-t', 'ext4', '-F', '-O', '^has_journal,^64bit,^metadata_csum',
                        '-d', str(seed), str(image)], check=True, capture_output=True)
        subprocess.run(['debugfs', '-w', '-R', 'rmdir /lost+found', str(image)], check=True, capture_output=True)
        return image

    @unittest.skipUnless(shutil.which('debugfs') and shutil.which('mke2fs'), 'e2fsprogs required')
    def test_complete_boot_namespace_patch_metadata_and_extra_file_rejection(self):
        image = self.make_boot()
        before = V.namespace(image, self.root / 'before')
        dtb = self.file('new.dtb', F.fdt(patched=True))
        with mock.patch.object(V.RUNTIME, 'PATCHED_DTB_RECORD', V.VGIC.HOST.record(dtb.read_bytes())):
            V.patch_boot(image, dtb, before, 1793750400, self.root / 'patch.log')
            after = V.namespace(image, self.root / 'after')
            V.compare_namespace(before, after)
            bad = copy.deepcopy(after); bad['boot/uImage']['data'] = {'bytes': 1, 'sha256': 'bad'}
            with self.assertRaisesRegex(ValueError, 'exceeds DTB'):
                V.compare_namespace(before, bad)
        subprocess.run(['debugfs', '-w', '-R', f'write {dtb} /unexpected', str(image)], check=True, capture_output=True)
        with self.assertRaisesRegex(ValueError, 'extra BOOT entry'):
            V.namespace(image, self.root / 'extra')


if __name__ == '__main__':
    unittest.main()
