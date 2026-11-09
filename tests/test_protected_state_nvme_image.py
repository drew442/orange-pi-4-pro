"""Ordinary-file regression checks; no construction or hardware acceptance."""
import gzip
import hashlib
import json
import importlib.util
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
from contextlib import ExitStack
from types import SimpleNamespace
import uuid
import zlib

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('protected_image', REPO / 'build/protected-state-nvme-image.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def record(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


class Monitor:
    def check(self, *args, **kwargs):
        pass


class StreamTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # Force replacement endpoints across decoding chunks and include zeros.
        self.raw = bytes(range(256)) * 100 + bytes(9000)
        self.start, self.length = 1007, 15313
        self.boot = bytes(self.length // 2) + b'B' * (self.length - self.length // 2)
        self.old = self.raw[self.start:self.start + self.length]
        self.new = self.raw[:self.start] + self.boot + self.raw[self.start + self.length:]
        self.ranges = {'prefix': (0, self.start), 'boot': (self.start, self.length),
                       'suffix': (self.start + self.length, len(self.raw) - self.start - self.length)}
        self.source, self.replacement = self.root / 'base.gz', self.root / 'boot'
        self.source.write_bytes(gzip.compress(self.raw, mtime=0))
        self.replacement.write_bytes(self.boot)

    def derive(self, name='new.gz', expected=None, bound=100000):
        with self.source.open('rb') as source, self.replacement.open('rb') as boot, patch.object(M, 'CHUNK', 733):
            return M.derive(source.fileno(), boot.fileno(), self.root / name, expected or record(self.raw),
                            record(self.old), record(self.boot), self.ranges, Monitor(), self.start, self.length, bound)

    def test_exact_replacement_and_independent_output(self):
        raw, before, after, changed = self.derive()
        self.assertEqual(gzip.decompress((self.root / 'new.gz').read_bytes()), self.new)
        self.assertEqual(raw, record(self.new))
        self.assertEqual(changed, sum(a != b for a, b in zip(self.old, self.boot)))
        output = M.verify_output(self.root / 'new.gz', record(self.new), self.ranges, Monitor())
        self.assertEqual(output.records(), after)
        M.unchanged(before, after)
        self.derive('second.gz')
        self.assertEqual((self.root / 'new.gz').read_bytes(), (self.root / 'second.gz').read_bytes())
        self.assertEqual((self.root / 'new.gz').read_bytes()[:10], b'\x1f\x8b\x08\x00\0\0\0\0\x02\xff')

    def test_short_original_and_raw_hash(self):
        self.source.write_bytes(gzip.compress(self.raw[:-1], mtime=0))
        with self.assertRaisesRegex(ValueError, 'size mismatch'):
            self.derive()
        self.source.write_bytes(gzip.compress(b'Q' + self.raw[1:], mtime=0))
        with self.assertRaisesRegex(ValueError, 'raw hash'):
            self.derive('hash.gz')

    def test_overlong_original_and_short_replacement(self):
        with self.assertRaisesRegex(ValueError, 'exceeds raw bound'):
            self.derive(expected=record(self.raw[:-1]))
        self.replacement.write_bytes(self.boot[:-1])
        with self.assertRaisesRegex(ValueError, 'short replacement'):
            self.derive('short.gz')

    def test_members_trailer_and_crc(self):
        original = self.source.read_bytes()
        for index, data in enumerate((original + gzip.compress(b'X'), original + b'X', original[:-1],
                                      original[:-8] + bytes([original[-8] ^ 1]) + original[-7:])):
            self.source.write_bytes(data)
            with self.assertRaises((ValueError, zlib.error)):
                self.derive(f'invalid-{index}.gz')

    def test_scratch_bound_and_existing_output(self):
        with self.assertRaisesRegex(ValueError, 'scratch bound'):
            self.derive(bound=20)
        with self.assertRaises(FileExistsError):
            self.derive()

    def test_output_independent_crc_size_hash_and_outside(self):
        self.derive()
        target = self.root / 'new.gz'
        for expected in (record(self.new[:-1]), record(b'X' + self.new[1:])):
            with self.assertRaises(ValueError):
                M.verify_output(target, expected, self.ranges, Monitor())
        encoded = target.read_bytes()
        target.write_bytes(encoded[:-2])
        with self.assertRaises((EOFError, OSError)):
            M.verify_output(target, record(self.new), self.ranges, Monitor())
        with self.assertRaisesRegex(ValueError, 'outside BOOT'):
            M.unchanged({'suffix': record(b'a')}, {'suffix': record(b'b')})

    def test_build_holds_sources_until_publication(self):
        base = {'state_uuid': M.RUNTIME.STATE_UUID,
                'files': {'nvme.img.gz': record(self.source.read_bytes()), 'nvme.img': record(self.raw)},
                'layout': {'partitions': [{'number': 1, 'name': 'boot', 'start_bytes': self.start,
                                            'size_bytes': self.length}]},
                'gpt_extents': [], 'partition_payloads': {}, 'boot_files': {},
                'build_seconds': 2900.0630514310324, 'allocated_bytes': 1115275264,
                'builder_packages': {'historical-only': 'old-version'},
                'source_date_epoch': 1793750400, 'base_nvme': {'historical': True},
                'future_historical_measurement': 1234}
        authority = self.root / 'base.json'
        authority.write_text(json.dumps(base))
        recipe = self.root / 'recipe.py'
        recipe.write_text('# held source\n')
        (self.root / 'out').mkdir()
        patches = {'REPO': self.root, 'WIRE': record(self.source.read_bytes()), 'RAW': record(self.raw),
                   'MANIFEST': record(authority.read_bytes()), 'OLD_BOOT': record(self.old),
                   'NEW_BOOT': record(self.boot), 'START': self.start, 'LENGTH': self.length,
                   'RECIPES': ('recipe.py',)}
        real_derive, real_extents, real_verify = M.derive, M.extents, M.verify_output
        def derive(*args):
            return real_derive(*args, start=self.start, length=self.length)
        def mutate_after_decode(*args):
            result = real_verify(*args)
            recipe.write_text('# source edit\n')
            return result
        with ExitStack() as stack:
            for key, value in patches.items():
                stack.enter_context(patch.object(M, key, value))
            stack.enter_context(patch.object(M, 'admission', return_value={}))
            monitor = SimpleNamespace(minimum_free=30_000_000_000, minimum_memory=2_000_000_000)
            def check(phase, *args, **kwargs):
                if phase == 'publication':
                    monitor.minimum_free = 29_000_000_000
                    monitor.minimum_memory = 1_500_000_000
            monitor.check = check
            stack.enter_context(patch.object(M.HELP, 'Monitor', return_value=monitor))
            stack.enter_context(patch.object(M, 'derive', side_effect=derive))
            stack.enter_context(patch.object(M, 'extents', side_effect=lambda b: real_extents(
                b, size=len(self.raw), start=self.start, length=self.length)))
            stack.enter_context(patch.object(M, 'validate_gpt'))
            stack.enter_context(patch.object(M, 'verify_output', side_effect=mutate_after_decode))
            output = self.root / 'out/protected-state-nvme-image-mutated'
            with self.assertRaisesRegex(ValueError, 'input changed'):
                M.build(self.source, authority, self.replacement, output)
            self.assertFalse((output / 'manifest.json').exists())
            self.assertFalse((output / 'nvme.img.gz').exists())
            self.assertTrue((output / 'nvme.img.gz.partial').exists())
            with patch.object(M, 'verify_output', side_effect=real_verify):
                complete = self.root / 'out/protected-state-nvme-image-complete'
                result = M.build(self.source, authority, self.replacement, complete)
            self.assertEqual(result['files']['nvme.img'], record(self.new))
            self.assertEqual(gzip.decompress((complete / 'nvme.img.gz').read_bytes()), self.new)
            self.assertTrue((complete / 'manifest.json').exists())
            self.assertFalse(result['hardware_acceptance'])
            self.assertEqual(result['resources']['minimum_free_bytes'], 29_000_000_000)
            self.assertEqual(result['resources']['minimum_available_memory_bytes'], 1_500_000_000)
            # The complete old authority is explicitly historical; no old builder
            # measurement (including unknown future fields) becomes current evidence.
            self.assertEqual(result['source_manifest'], base)
            for name in ('build_seconds', 'allocated_bytes', 'builder_packages', 'source_date_epoch',
                         'base_nvme', 'future_historical_measurement'):
                self.assertNotIn(name, result)
                self.assertEqual(result['source_manifest'][name], base[name])
            self.assertFalse((complete / 'nvme.img').exists())

    def test_runtime_resource_floors(self):
        for free, memory in ((M.HELP.DISK_FLOOR - 1, 2_000_000), (30_000_000_000, 1)):
            with patch.object(M.HELP.shutil, 'disk_usage', return_value=SimpleNamespace(free=free)), \
                    patch.object(Path, 'read_text', return_value=f'MemAvailable: {memory} kB\n'):
                with self.assertRaisesRegex(ValueError, 'runtime disk/memory floor'):
                    M.HELP.Monitor(self.root).check('fixture', force=True)

    def test_ranges_exact_crossing(self):
        capture = M.Ranges({'gpt:fixture': (3, 8), 'tail': (10, 3)})
        for offset in range(0, 13, 2):
            capture.update(bytes(range(13))[offset:offset + 2], offset)
        self.assertEqual(capture.records(), {'gpt:fixture': record(bytes(range(3, 11))),
                                             'tail': record(bytes(range(10, 13)))})
        self.assertEqual(capture.gpt['gpt:fixture'], bytes(range(3, 11)))

    def test_held_fixed_pin_changes_and_unsafe_paths(self):
        with self.assertRaises(ValueError):
            M.RUNTIME.HeldInput(self.source, 100000, record(b'wrong'))
        with M.RUNTIME.HeldInput(self.replacement, len(self.boot), record(self.boot)) as held:
            self.replacement.write_bytes(b'X' * len(self.boot))
            with self.assertRaisesRegex(ValueError, 'changed'):
                held.verify()
        link = self.root / 'link'
        link.symlink_to(self.source)
        for path in (link, Path('relative'), Path('/dev/null')):
            with self.assertRaises((ValueError, OSError)):
                M.RUNTIME.HeldInput(path, 100000)
        alias = self.root / 'hardlink'
        os.link(self.source, alias)
        with self.assertRaises(ValueError):
            M.RUNTIME.HeldInput(alias, 100000)

    def test_admission_and_output_guards_before_mutation(self):
        sample = {'free_bytes': M.ADMISSION - 1, 'available_memory_bytes': 2_000_000_000}
        with patch.object(M.BOOT.SD, 'resources', return_value=sample):
            with self.assertRaisesRegex(ValueError, '23760999827'):
                M.admission(self.root)
        sample['free_bytes'] += 1
        with patch.object(M.BOOT.SD, 'resources', return_value=sample):
            self.assertEqual(M.admission(self.root), sample)
        for path in (self.root / 'protected-state-nvme-image-x', Path('out/protected-state-nvme-image-x'),
                     REPO / 'out' / 'protected-state-nvme-image-../x'):
            with self.assertRaises(ValueError):
                M.output_path(path)
        with patch.object(M, 'REPO', self.root):
            (self.root / 'out').mkdir()
            target = self.root / 'out' / 'protected-state-nvme-image-test'
            target.mkdir()
            with self.assertRaisesRegex(ValueError, 'already exists'):
                M.output_path(target)


class GPTTests(unittest.TestCase):
    def fixture(self):
        size = M.RAW['bytes']
        base = {'logical_sector_bytes': 512, 'physical_sector_bytes': 512,
                'disk_guid': str(uuid.UUID(int=1)),
                'partition_guids': [str(uuid.UUID(int=i)) for i in range(2, 7)]}
        parts, offset = [], 1024**2
        end = (size - 33 * 512) // (1024**2) * (1024**2)
        for index, (name, length) in enumerate(zip(('boot', 'root-a', 'root-b', 'state', 'guests'),
                                                  (512 * 1024**2, 4 * 1024**3, 4 * 1024**3, 8 * 1024**3, None))):
            length = length or end - offset
            parts.append({'number': index + 1, 'name': name, 'start_bytes': offset, 'size_bytes': length,
                          'start_lba': offset // 512, 'end_lba': (offset + length) // 512 - 1})
            offset += length
        base['layout'] = {'table': 'gpt', 'alignment_bytes': 1024**2, 'front_reserved_bytes': 1024**2,
                          'tail_reserved_bytes': size - end, 'partitions': parts}
        sector, last = 512, size // 512 - 1
        entries = bytearray(16384)
        for index, part in enumerate(base['layout']['partitions']):
            struct.pack_into('<16s16sQQQ72s', entries, index * 128,
                             uuid.UUID('0fc63daf-8483-4772-8e79-3d69d8477de4').bytes_le,
                             uuid.UUID(base['partition_guids'][index]).bytes_le,
                             part['start_lba'], part['end_lba'], 0,
                             part['name'].encode('utf-16-le').ljust(72, b'\0'))
        captured = {}
        mbr = bytearray(512)
        struct.pack_into('<B3sB3sII', mbr, 446, 0, b'\0\2\0', 0xee, b'\xff\xff\xff', 1, last)
        mbr[510:] = b'\x55\xaa'
        captured['gpt:protective-mbr'] = mbr
        for prefix, current, alternate, array in (('primary', 1, last, 2), ('backup', last, 1, last - 32)):
            header = bytearray(512)
            struct.pack_into('<8sIIIIQQQQ16sQIII', header, 0, b'EFI PART', 0x10000, 92, 0, 0,
                             current, alternate, 34, last - 33, uuid.UUID(base['disk_guid']).bytes_le,
                             array, 128, 128, zlib.crc32(entries))
            struct.pack_into('<I', header, 16, zlib.crc32(header[:92]))
            captured['gpt:' + prefix + '-header'] = header
            captured['gpt:' + prefix + '-entries'] = bytearray(entries)
        return captured, base, size

    def test_both_gpt_tables_geometry_crc_and_identity(self):
        captured, base, size = self.fixture()
        M.validate_gpt(captured, base, size)
        for name in captured:
            changed = {key: bytearray(value) for key, value in captured.items()}
            changed[name][0] ^= 1
            with self.subTest(name=name), self.assertRaises(ValueError):
                M.validate_gpt(changed, base, size)
        base['layout']['partitions'][1]['size_bytes'] -= 512
        with self.assertRaisesRegex(ValueError, 'layout mismatch'):
            M.validate_gpt(captured, base, size)


if __name__ == '__main__':
    unittest.main()
