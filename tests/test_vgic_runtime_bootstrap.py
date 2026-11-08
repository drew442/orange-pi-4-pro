"""Small host fixtures only: no raw 256 MiB image construction or hardware claims."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('vgic_runtime_bootstrap', REPO / 'build/vgic-runtime-bootstrap.py')
V = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(V)


def fdt(extra=False, patched=False):
    strings = b'interrupts\0status\0'
    block = struct.pack('>I', 1) + bytes(4)
    name = b'interrupt-controller@3400000\0'
    block += struct.pack('>I', 1) + name + bytes(-len(name) % 4)
    if patched:
        block += struct.pack('>III', 3, 12, 0) + V.VGIC.VALUE
    if extra:
        block += struct.pack('>III', 3, 4, 11) + b'bad\0'
    block += struct.pack('>III', 2, 2, 9)
    header = struct.pack('>10I', 0xd00dfeed, 56 + len(block) + len(strings),
                         56, 56 + len(block), 40, 17, 16, 0, len(strings), len(block))
    return header + bytes(16) + block + strings


class RuntimeBootstrapTests(unittest.TestCase):
    def setUp(self):
        # Only tiny fixtures; production paths intentionally reject /dev/shm too.
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def file(self, name='input', data=b'original'):
        path = self.root / name
        path.write_bytes(data)
        return path

    def base(self):
        return {'schema': 1, 'board': 'Orange Pi 4 Pro A733', 'state_uuid': V.STATE_UUID,
                'kernel_release': '6.6.98-a733-diag', 'package_state_profile': 'a733-package-state-v1',
                'root_selection': 'NVMe state', 'source_date_epoch': 1793750400,
                'boot_command': {'bytes': 1, 'sha256': 'command'},
                'boot_profile': {'name': 'experimental-el2', 'evidence': 'old evidence'},
                'boot_inputs': {'board.dtb': V.DTB_RECORD, 'uImage': {'bytes': 1},
                                'boot0_sdcard.fex': {'bytes': 2}, 'boot_package.fex': {'bytes': 3}},
                'files': {'a733-host-sd.img': V.IMAGE_RECORD},
                'recipes': {'historical-producer': 'old'}, 'sources': {'historical-source': 'old'},
                'trusted_host_manifest': {'historical-initramfs': 'old'}}

    def test_pins_match_corrected_evidence_and_not_old_vgic_base(self):
        evidence = json.loads((REPO / 'docs/evidence/production-host-bootstrap-runtime-fixes-20261108.json').read_text())
        self.assertEqual(V.IMAGE_RECORD, evidence['image'])
        self.assertEqual(V.MANIFEST_RECORD, evidence['payload_records']['sd_manifest'])
        self.assertEqual(V.DTB_RECORD, evidence['boot_inputs']['board.dtb'])
        self.assertEqual(V.STATE_UUID, evidence['state_uuid'])
        self.assertNotEqual(V.IMAGE_RECORD, V.VGIC.IMAGE_RECORD)
        self.assertEqual(V.IMAGE_RECORD['bytes'], 256 * 1024**2)
        self.assertLess(V.IMAGE_RECORD['bytes'], V.SD_BYTES)
        self.assertEqual(V.PROFILE, 'experimental-el2-vgic-v1')
        self.assertEqual(V.PATCHED_DTB_RECORD['bytes'], 209344)

    def test_original_manifest_authenticated_without_mutation(self):
        base = self.base()
        V.validate_base(base)
        for field, value in (('state_uuid', V.VGIC.STATE_UUID),
                             ('boot_profile', {'name': V.PROFILE}),
                             ('files', {'a733-host-sd.img': V.VGIC.IMAGE_RECORD}),
                             ('boot_inputs', {'board.dtb': V.PATCHED_DTB_RECORD})):
            broken = copy.deepcopy(base)
            broken[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                V.validate_base(broken)

    def test_derivation_provenance_separates_old_producer_and_new_payload(self):
        base = self.base()
        before = copy.deepcopy(base)
        original = json.dumps(base).encode()
        outputs = {'a733-host-sd.img': V.IMAGE_RECORD, 'uInitrd': {'sha256': 'retained'}}
        result = V.result_manifest(base, original, {'new-recipe': 'actual'}, {'dtc': 'pinned'},
                                   outputs, {'uInitrd': 'identical'}, {'bytes': 33554432})
        self.assertEqual(base, before)
        self.assertEqual(result['base_bootstrap']['original_manifest'], before)
        self.assertEqual(result['base_bootstrap']['original_manifest_file']['sha256'], V.HOST.record(original)['sha256'])
        self.assertEqual(result['base_bootstrap']['boot_profile']['name'], 'experimental-el2')
        self.assertEqual(result['boot_profile']['name'], V.PROFILE)
        self.assertEqual(result['boot_inputs']['board.dtb'], V.PATCHED_DTB_RECORD)
        self.assertEqual(result['base_bootstrap']['original_manifest']['boot_inputs']['board.dtb'], V.DTB_RECORD)
        self.assertEqual(result['recipes'], {'new-recipe': 'actual'})
        self.assertNotIn('sources', result)
        self.assertNotIn('trusted_host_manifest', result)
        self.assertFalse(result['hardware_acceptance'])
        self.assertIn('UNBOOTED', result['classification'])

    def test_semantics_reject_extra_mutation(self):
        V.VGIC.verify_delta(fdt(), fdt(patched=True))
        with self.assertRaisesRegex(ValueError, 'semantic delta'):
            V.VGIC.verify_delta(fdt(), fdt(extra=True, patched=True))

    def test_held_inputs_refuse_links_special_paths_and_unbounded_files(self):
        source = self.file()
        link = self.root / 'symlink'
        link.symlink_to(source)
        hardlink = self.root / 'hardlink'
        os.link(source, hardlink)
        fifo = self.root / 'fifo'
        os.mkfifo(fifo)
        for path in (source, hardlink, link, fifo, Path('/dev/null'), Path('/proc/version'),
                     Path('/sys/kernel/uevent_seqnum'), Path('relative')):
            with self.subTest(path=path), self.assertRaises(ValueError):
                V.HeldInput(path, 1024)
        hardlink.unlink()
        with self.assertRaisesRegex(ValueError, 'size bound'):
            V.HeldInput(source, 1)
        with self.assertRaisesRegex(ValueError, 'hash/size'):
            V.HeldInput(source, 1024, {'bytes': 8, 'sha256': 'bad'})

    def test_reject_symlink_ancestor_and_parent_alias(self):
        source = self.file()
        (self.root / 'dirlink').symlink_to(self.root, target_is_directory=True)
        for path in (self.root / 'dirlink/input', self.root / 'child/../input'):
            with self.assertRaises(ValueError):
                V.HeldInput(path, 1024)

    def test_hold_rechecks_hash_identity_and_path_replacement(self):
        source = self.file()
        with V.HeldInput(source, 1024) as held:
            self.assertEqual(held.read(), b'original')
            held.verify()
            source.write_bytes(b'changed!')
            with self.assertRaisesRegex(ValueError, 'changed'):
                held.verify()
        source.write_bytes(b'original')
        with V.HeldInput(source, 1024) as held:
            source.rename(self.root / 'renamed')
            source.write_bytes(b'original')
            with self.assertRaisesRegex(ValueError, 'changed'):
                held.verify()

    def test_detect_hash_change_even_when_identity_comparison_mocked(self):
        source = self.file()
        with V.HeldInput(source, 1024) as held:
            with mock.patch.object(V, 'identity', return_value=held.initial_identity):
                source.write_bytes(b'changed!')
                with self.assertRaisesRegex(ValueError, 'changed'):
                    held.verify()

    def test_output_names_and_exclusive_failed_tags(self):
        with mock.patch.object(V, 'REPO', self.root):
            (self.root / 'out').mkdir()
            for name in ('', '../x', 'x/y', '/x', 'x' * 65, 'x y'):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    V.output_path(name)
            output = V.output_path('run-01')
            self.assertEqual(output.name, 'host-sd-vgic-runtime-run-01')
            output.mkdir()
            with self.assertRaisesRegex(ValueError, 'failed tags'):
                V.output_path('run-01')
            output.rmdir()
            output.symlink_to(self.root / 'missing')
            with self.assertRaises(ValueError):
                V.output_path('run-01')

    def test_entire_prefix_compared_and_truncation_refused(self):
        source = self.file(data=b'a' * 4096)
        target = self.file('target', b'a' * 4096 + b'new filesystem')
        self.assertEqual(V.compare_prefix(source, target, 4096), V.HOST.record(b'a' * 4096))
        for data in (b'a' * 4095, b'a' * 4095 + b'b'):
            target.write_bytes(data)
            with self.assertRaisesRegex(ValueError, '32 MiB'):
                V.compare_prefix(source, target, 4096)

    def test_every_preserved_boot_file_checked(self):
        original, rebuilt = self.root / 'before', self.root / 'after'
        original.mkdir()
        rebuilt.mkdir()
        for blob in V.VGIC.FILES:
            (original / blob).write_bytes(fdt() if blob == 'board.dtb' else blob.encode())
            (rebuilt / blob).write_bytes(fdt(patched=True) if blob == 'board.dtb' else blob.encode())
        V.VGIC.verify_boot_files(original, rebuilt)
        for blob in ('uImage', 'uInitrd', 'boot.cmd', 'boot.scr'):
            with self.subTest(blob=blob):
                path = rebuilt / blob
                path.write_bytes(blob.encode() + b'changed')
                with self.assertRaisesRegex(ValueError, 'preserved boot file'):
                    V.VGIC.verify_boot_files(original, rebuilt)
                path.write_bytes(blob.encode())

    def test_gzip_determinism_header_expansion_and_exclusive_output(self):
        import gzip
        source = self.file(data=b'payload' * 4096)
        first, second = self.root / 'first.gz', self.root / 'second.gz'
        V.deterministic_gzip(source, first)
        V.deterministic_gzip(source, second)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(first.read_bytes()[3:8], bytes(5))  # No filename and zero mtime.
        self.assertEqual(gzip.decompress(first.read_bytes()), source.read_bytes())
        with self.assertRaises(FileExistsError):
            V.deterministic_gzip(source, first)

    def fixture_build(self, mutate_input=False):
        from contextlib import ExitStack
        base = self.base()
        payloads = {'uImage': b'kernel', 'uInitrd': b'initrd', 'boot.cmd': b'cmd',
                    'boot.scr': b'script', 'board.dtb': fdt()}
        firmware = {'boot0_sdcard.fex': b'boot0', 'boot_package.fex': b'el2'}
        for blob in ('uImage', 'board.dtb'):
            base['boot_inputs'][blob] = V.HOST.record(payloads[blob])
        for blob in firmware:
            base['boot_inputs'][blob] = V.HOST.record(firmware[blob])
        base['files']['uInitrd'] = V.HOST.record(payloads['uInitrd'])
        base['boot_command'] = V.HOST.record(payloads['boot.cmd'])
        source = self.file('base.img', b'prefixbasefs')
        image_pin = V.HOST.record(source.read_bytes())
        base['files']['a733-host-sd.img'] = image_pin
        for recipe in V.RECIPES:
            path = self.root / recipe
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(recipe.encode())
        base['recipes']['configs/builder-packages.lock.tsv'] = V.HOST.record(
            b'configs/builder-packages.lock.tsv')
        original_data = json.dumps(base).encode()
        original_manifest = self.file('base-manifest.json', original_data)

        def extract(image, destination):
            destination.mkdir()
            for blob, data in payloads.items():
                if blob == 'board.dtb' and image != source:
                    data = fdt(patched=True)
                (destination / blob).write_bytes(data)

        def run(command, **kwargs):
            if command[0] == 'fdtput':
                Path(command[3]).write_bytes(fdt(patched=True))
            else:
                Path(command[3]).write_bytes(b'prefixnewfs')
                if mutate_input:
                    source.write_bytes(b'prefixbadfs!')

        compare = V.compare_prefix
        with ExitStack() as stack:
            patches = (
                mock.patch.object(V, 'REPO', self.root),
                mock.patch.object(V, 'IMAGE_RECORD', image_pin),
                mock.patch.object(V, 'MANIFEST_RECORD', V.HOST.record(original_data)),
                mock.patch.object(V, 'DTB_RECORD', V.HOST.record(fdt())),
                mock.patch.object(V, 'PATCHED_DTB_RECORD', V.HOST.record(fdt(patched=True))),
                mock.patch.object(V.VGIC, 'verify_layout'),
                mock.patch.object(V.VGIC, 'extract', side_effect=extract),
                mock.patch.object(V.VGIC, 'copy_region', side_effect=lambda src, dst, off, length: dst.write_bytes(firmware[dst.name])),
                mock.patch.object(V.VGIC, 'builder_packages', return_value={'dtc': 'pinned'}),
                mock.patch.object(V, 'tool_versions', return_value={'fdtput': '1.6.1'}),
                mock.patch.object(V.subprocess, 'run', side_effect=run),
                mock.patch.object(V, 'compare_prefix', side_effect=lambda a, b: compare(a, b, 6)),
                mock.patch.object(V.shutil, 'disk_usage', return_value=mock.Mock(free=2 * 1024**3)),
            )
            for patch in patches:
                stack.enter_context(patch)
            result = V.build(source, original_manifest, 'fixture')
        return result, original_data

    def test_small_complete_pipeline_keeps_original_manifest_and_payloads(self):
        result, original_data = self.fixture_build()
        output = self.root / 'out/host-sd-vgic-runtime-fixture'
        self.assertEqual((output / 'base-bootstrap-manifest.json').read_bytes(), original_data)
        self.assertEqual((output / 'uInitrd').read_bytes(), b'initrd')
        self.assertEqual(result['base_bootstrap']['original_manifest'], json.loads(original_data))
        self.assertEqual(result['tool_versions']['fdtput'], '1.6.1')
        self.assertEqual(result, json.loads((output / 'manifest.json').read_text()))
        for blob, pin in result['files'].items():
            self.assertEqual(V.VGIC.file_record(output / blob), pin)
        self.assertFalse(any(output.glob('scratch-*')))

    def test_small_pipeline_rejects_changed_input_and_consumes_failed_tag(self):
        with self.assertRaisesRegex(ValueError, 'input changed'):
            self.fixture_build(mutate_input=True)
        output = self.root / 'out/host-sd-vgic-runtime-fixture'
        self.assertTrue(output.is_dir())
        self.assertFalse((output / 'manifest.json').exists())
        with mock.patch.object(V, 'REPO', self.root):
            with self.assertRaisesRegex(ValueError, 'failed tags'):
                V.output_path('fixture')

    def test_insufficient_native_space_refused_before_output_reservation(self):
        base = self.base()
        base['recipes']['configs/builder-packages.lock.tsv'] = {'bytes': 1, 'sha256': 'lock'}
        fake = mock.MagicMock()
        fake.record = {'bytes': 1, 'sha256': 'lock'}
        fake.read.return_value = b'{}'
        fake.__enter__.return_value = fake
        with mock.patch.object(V, 'REPO', self.root), \
                mock.patch.object(V, 'HeldInput', return_value=fake), \
                mock.patch.object(V.HOST, 'json_object', return_value=base), \
                mock.patch.object(V, 'validate_base'), \
                mock.patch.object(V.VGIC, 'verify_layout'), \
                mock.patch.object(V.VGIC, 'builder_packages', return_value={}), \
                mock.patch.object(V, 'tool_versions', return_value={}), \
                mock.patch.object(V.shutil, 'disk_usage', return_value=mock.Mock(free=311 * 1024**2)):
            with self.assertRaisesRegex(ValueError, 'scratch'):
                V.build(self.root / 'image', self.root / 'manifest', 'low-space')
            self.assertFalse((self.root / 'out/host-sd-vgic-runtime-low-space').exists())


if __name__ == '__main__':
    unittest.main()
