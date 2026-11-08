"""Host-only validation of the bounded VGIC candidate recipe; no hardware claims."""
import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('vgic_host_sd', REPO / 'build/vgic-host-sd.py')
VGIC = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VGIC)


def fdt(properties=(), extra_node=False, reserve=0):
    """Small independent binary fixture, independent of dtc/fdtput availability."""
    strings = b'compatible\0interrupts\0status\0'
    block = bytearray()

    def word(*values):
        block.extend(struct.pack('>' + 'I' * len(values), *values))

    def begin(name):
        word(1)
        block.extend(name.encode() + b'\0')
        block.extend(bytes((-len(block)) % 4))

    def prop(name, value):
        word(3, len(value), strings.index(name.encode() + b'\0'))
        block.extend(value)
        block.extend(bytes((-len(block)) % 4))

    begin('')
    prop('compatible', b'test,a733\0')
    begin('interrupt-controller@3400000')
    prop('compatible', b'arm,gic-v3\0')
    for name, value in properties:
        prop(name, value)
    word(2)
    if extra_node:
        begin('unrelated')
        word(2)
    word(2, 9)
    reservations = struct.pack('>QQQQ', reserve, reserve, 0, 0) if reserve else bytes(16)
    off_struct = 40 + len(reservations)
    off_strings = off_struct + len(block)
    total = off_strings + len(strings)
    header = struct.pack('>10I', 0xd00dfeed, total, off_struct, off_strings,
                         40, 17, 16, 0, len(strings), len(block))
    return header + reservations + block + strings


class SemanticDeltaTests(unittest.TestCase):
    def test_accept_exact_gic_ppi9_level_high(self):
        VGIC.verify_delta(fdt(), fdt([('interrupts', VGIC.VALUE)]))

    def test_reject_other_property_or_node_or_reservation(self):
        for candidate in (
                fdt([('interrupts', VGIC.VALUE), ('status', b'okay\0')]),
                fdt([('interrupts', VGIC.VALUE)], extra_node=True),
                fdt([('interrupts', VGIC.VALUE)], reserve=4096),
                fdt([('interrupts', struct.pack('>III', 1, 10, 4))])):
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                VGIC.verify_delta(fdt(), candidate)

    def test_reject_existing_baseline_interrupts_even_if_same(self):
        data = fdt([('interrupts', VGIC.VALUE)])
        with self.assertRaisesRegex(ValueError, 'already present'):
            VGIC.verify_delta(data, data)

    def test_reject_duplicate_property_and_invalid_blob(self):
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            VGIC.fdt_semantics(fdt([('interrupts', VGIC.VALUE), ('interrupts', VGIC.VALUE)]))
        for data in (b'', fdt()[:-1], b'x' + fdt()[1:]):
            with self.assertRaises(ValueError):
                VGIC.fdt_semantics(data)


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_reject_device_symlink_and_fifo_inputs_without_reading(self):
        import os
        for path in (Path('/dev/null'), self.root / 'link', self.root / 'fifo'):
            if path.name == 'link':
                path.symlink_to('/etc/passwd')
            elif path.name == 'fifo':
                os.mkfifo(path)
            with self.subTest(path=path), self.assertRaises(ValueError):
                VGIC.file_record(path)

    def test_reject_symlink_ancestor(self):
        (self.root / 'link').symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            VGIC.file_record(self.root / 'link' / 'file')

    def test_hash_tamper(self):
        path = self.root / 'file'
        path.write_bytes(b'baseline')
        pin = VGIC.file_record(path)
        path.write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'hash/size'):
            VGIC.require_record(path, pin)

    def test_output_rejects_traversal_existing_and_symlink(self):
        with mock.patch.object(VGIC, 'REPO', self.root):
            for name in ('../x', '/dev/sda', 'x/y', '', '.', 'x' * 65):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    VGIC.output_path(name)
            (self.root / 'out').mkdir()
            target = VGIC.output_path('review-01')
            self.assertEqual(target, self.root / 'out/vgic-host-sd-review-01')
            target.mkdir()
            with self.assertRaisesRegex(ValueError, 'must not exist'):
                VGIC.output_path('review-01')
            target.rmdir()
            target.symlink_to(self.root / 'missing')
            with self.assertRaises(ValueError):
                VGIC.output_path('review-01')

    def test_sparse_copy_keeps_source_and_exact_region(self):
        source, output = self.root / 'source', self.root / 'output'
        data = b'prefix' + bytes(1024**2) + b'suffix'
        source.write_bytes(data)
        original = VGIC.file_record(source)
        VGIC.copy_region(source, output, 6, len(data) - 6)
        self.assertEqual(output.read_bytes(), data[6:])
        self.assertEqual(VGIC.file_record(source), original)
        with self.assertRaises(FileExistsError):
            VGIC.copy_region(source, output, 0, 1)
        with self.assertRaisesRegex(ValueError, 'short image'):
            VGIC.copy_region(source, self.root / 'short', len(data), 1)

    def test_mbr_layout_rejects_changed_target_and_extra_partition(self):
        image = self.root / 'image'
        mbr = bytearray(512)
        mbr[440:444] = struct.pack('<I', 0xa7330001)
        mbr[446], mbr[450] = 0x80, 0x83
        struct.pack_into('<II', mbr, 454, 65536, 458752)
        mbr[510:512] = b'\x55\xaa'
        with mock.patch.object(VGIC, 'IMAGE_RECORD', {'bytes': 512}):
            image.write_bytes(mbr)
            VGIC.verify_layout(image)
            for position in (440, 446, 450, 454, 458, 462, 510):
                broken = bytearray(mbr)
                broken[position] ^= 1
                image.write_bytes(broken)
                with self.subTest(position=position), self.assertRaises(ValueError):
                    VGIC.verify_layout(image)

    def test_rebuilt_boot_files_must_preserve_every_original_byte(self):
        original, rebuilt = self.root / 'original', self.root / 'rebuilt'
        original.mkdir()
        rebuilt.mkdir()
        for blob in VGIC.FILES:
            (original / blob).write_bytes(fdt() if blob == 'board.dtb' else blob.encode())
            (rebuilt / blob).write_bytes(
                fdt([('interrupts', VGIC.VALUE)]) if blob == 'board.dtb' else blob.encode())
        VGIC.verify_boot_files(original, rebuilt)
        for blob in VGIC.FILES:
            if blob == 'board.dtb':
                continue
            before = (rebuilt / blob).read_bytes()
            (rebuilt / blob).write_bytes(before + b'tamper')
            with self.subTest(blob=blob), self.assertRaisesRegex(ValueError, 'preserved boot file'):
                VGIC.verify_boot_files(original, rebuilt)
            (rebuilt / blob).write_bytes(before)

    def test_original_image_pin_matches_tracked_deployment_evidence(self):
        guide = (REPO / 'docs/deploy-personalized-20261106.md').read_text()
        self.assertIn(VGIC.IMAGE_RECORD['sha256'], guide)
        self.assertIn(VGIC.STATE_UUID, guide)
        self.assertEqual(VGIC.IMAGE_RECORD['bytes'], 256 * 1024**2)

    def test_toolchain_dtc_version_must_match(self):
        with mock.patch.object(VGIC.HOST, 'validate_builder_packages', return_value={}), \
                mock.patch.object(VGIC.subprocess, 'check_output', return_value='1.7.0\tamd64'):
            with self.assertRaisesRegex(ValueError, 'version/architecture'):
                VGIC.builder_packages()

    def test_extract_detects_debugfs_silent_failure(self):
        destination = self.root / 'dump'
        with mock.patch.object(VGIC, 'copy_region'), \
                mock.patch.object(VGIC.subprocess, 'run', return_value=None):
            with self.assertRaisesRegex(ValueError, 'debugfs failed'):
                VGIC.extract(self.root / 'unused', destination)


if __name__ == '__main__':
    unittest.main()
