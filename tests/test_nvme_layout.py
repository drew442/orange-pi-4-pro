"""Independent decoding checks for the pure NVMe GPT encoder."""

import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
import uuid
import zlib

from scripts.nvme_layout import encode_gpt, layout, GIB, MIB


class LayoutTests(unittest.TestCase):
    def test_exact_existing_geometry(self):
        for logical in (512, 4096):
            size = 250_000_000_000 // logical * logical
            result = layout(size, logical, 4096)
            self.assertEqual(result['alignment_bytes'], MIB)
            self.assertEqual(result['front_reserved_bytes'], MIB)
            parts = result['partitions']
            self.assertEqual([p['name'] for p in parts],
                             ['boot', 'root-a', 'root-b', 'state', 'guests'])
            self.assertEqual([p['size_bytes'] for p in parts[:4]],
                             [512 * MIB, 4 * GIB, 4 * GIB, 8 * GIB])
            end = (size - 33 * logical) // MIB * MIB
            self.assertEqual(result['tail_reserved_bytes'], size - end)
            previous_end = MIB
            for part in parts:
                self.assertEqual(part['start_bytes'], previous_end)
                self.assertEqual(part['start_bytes'] % MIB, 0)
                self.assertEqual(part['size_bytes'] % MIB, 0)
                self.assertEqual(part['start_lba'] * logical, part['start_bytes'])
                previous_end += part['size_bytes']
                self.assertEqual((part['end_lba'] + 1) * logical, previous_end)
            self.assertEqual(previous_end, end)

    def test_invalid_geometry(self):
        for args in [(True, 512, 4096), (250_000_000_000, None, 4096),
                     (250_000_000_000, 512, 256), (250_000_000_000, 512, 1536),
                     (250_000_000_001, 512, 4096), (20 * GIB, MIB, MIB),
                     (GIB, 512, 4096)]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                layout(*args)


class GPTTests(unittest.TestCase):
    def test_decode_both_sector_sizes(self):
        # Literal formats/offsets here independently decode the on-disk structures.
        linux_type = uuid.UUID('0fc63daf-8483-4772-8e79-3d69d8477de4')
        for logical in (512, 4096):
            with self.subTest(logical=logical):
                size = 250_000_000_000 // logical * logical
                result = encode_gpt(size, logical, 4096)
                extents = {e['name']: e for e in result['extents']}
                previous_end = 0
                for extent in result['extents']:
                    self.assertGreaterEqual(extent['offset_bytes'], previous_end)
                    self.assertEqual(extent['offset_bytes'] % logical, 0)
                    self.assertEqual(len(extent['data']) % logical, 0)
                    previous_end = extent['offset_bytes'] + len(extent['data'])
                    self.assertLessEqual(previous_end, size)
                self.assertEqual(previous_end, size)
                mbr = extents['protective-mbr']['data']
                self.assertEqual(mbr[:446], bytes(446))
                self.assertEqual(mbr[450], 0xee)
                self.assertEqual(struct.unpack_from('<II', mbr, 454),
                                 (1, size // logical - 1))
                self.assertEqual(mbr[462:510], bytes(48))
                self.assertEqual(mbr[510:512], b'\x55\xaa')
                self.assertEqual(mbr[512:], bytes(logical - 512))
                primary = extents['primary-entries']['data']
                self.assertEqual(primary, extents['backup-entries']['data'])
                self.assertEqual(len(primary), 128 * 128)
                self.assertEqual(primary[5 * 128:], bytes(123 * 128))
                identities = [uuid.UUID(result['disk_guid'])]
                for index, part in enumerate(result['layout']['partitions']):
                    entry = primary[index * 128:(index + 1) * 128]
                    self.assertEqual(uuid.UUID(bytes_le=entry[:16]), linux_type)
                    identity = uuid.UUID(bytes_le=entry[16:32])
                    self.assertEqual(str(identity), result['partition_guids'][index])
                    identities.append(identity)
                    start, end, attributes = struct.unpack_from('<QQQ', entry, 32)
                    self.assertEqual((start, end), (part['start_lba'], part['end_lba']))
                    self.assertEqual(attributes, 0)
                    self.assertEqual(entry[56:].decode('utf-16-le').rstrip('\0'), part['name'])
                self.assertEqual(len(set(identities)), 6)
                self.assertTrue(all(i.version == 4 and i.int for i in identities))
                last_lba = size // logical - 1
                array_sectors = 16384 // logical
                for name, current, alternate, entries_lba in [
                        ('primary-header', 1, last_lba, 2),
                        ('backup-header', last_lba, 1, last_lba - array_sectors)]:
                    data = extents[name]['data']
                    self.assertEqual(extents[name]['offset_bytes'], current * logical)
                    self.assertEqual(data[:8], b'EFI PART')
                    revision, header_size, crc, reserved = struct.unpack_from('<IIII', data, 8)
                    self.assertEqual((revision, header_size, reserved), (0x10000, 92, 0))
                    copy = bytearray(data[:92])
                    copy[16:20] = bytes(4)
                    self.assertEqual(zlib.crc32(copy), crc)
                    self.assertEqual(data[92:], bytes(logical - 92))
                    self.assertEqual(struct.unpack_from('<QQQQ', data, 24),
                                     (current, alternate, 2 + array_sectors,
                                      last_lba - array_sectors - 1))
                    self.assertEqual(uuid.UUID(bytes_le=data[56:72]), identities[0])
                    self.assertEqual(struct.unpack_from('<QIII', data, 72),
                                     (entries_lba, 128, 128, zlib.crc32(primary)))
                parts = result['layout']['partitions']
                self.assertGreaterEqual(parts[0]['start_lba'], result['first_usable_lba'])
                self.assertLessEqual(parts[-1]['end_lba'], result['last_usable_lba'])
                self.assertLessEqual(extents['primary-entries']['offset_bytes'] + len(primary),
                                     parts[0]['start_bytes'])
                self.assertLessEqual((parts[-1]['end_lba'] + 1) * logical,
                                     extents['backup-entries']['offset_bytes'])

    def test_fresh_and_supplied_identities(self):
        one = encode_gpt(250_000_000_000, 512, 4096)
        two = encode_gpt(250_000_000_000, 512, 4096)
        self.assertTrue(set([one['disk_guid']] + one['partition_guids']).isdisjoint(
            [two['disk_guid']] + two['partition_guids']))
        repeated = encode_gpt(250_000_000_000, 512, 4096,
                              one['disk_guid'], one['partition_guids'])
        self.assertEqual(one, repeated)

    def test_invalid_identities_and_capacity(self):
        identities = [uuid.uuid4() for _ in range(6)]
        for disk, parts in [(uuid.UUID(int=0), identities[1:]),
                            ('invalid', identities[1:]),
                            (identities[0], [uuid.UUID(int=0)] + identities[2:]),
                            (identities[0], [identities[0]] + identities[2:]),
                            (identities[0], [identities[1]] * 5),
                            (identities[0], identities[1:4]),
                            (identities[0], 'abcde')]:
            with self.subTest(disk=disk, parts=parts), self.assertRaises(ValueError):
                encode_gpt(250_000_000_000, 512, 4096, disk, parts)
        with self.assertRaisesRegex(ValueError, 'LBA range'):
            encode_gpt((2**64 + 1) * 512, 512, 4096)

    def test_protective_mbr_saturates_large_capacity(self):
        result = encode_gpt(3 * 1024**4, 512, 4096)
        mbr = result['extents'][0]['data']
        self.assertEqual(struct.unpack_from('<I', mbr, 458)[0], 0xffffffff)

    @unittest.skipUnless(shutil.which('sfdisk') and shutil.which('blkid'),
                         'requires util-linux sfdisk and blkid')
    def test_regular_file_util_linux_readback(self):
        size = 250_000_000_000
        result = encode_gpt(size, 512, 4096)
        with tempfile.TemporaryDirectory() as temporary:
            image = Path(temporary) / 'nvme.img'
            with image.open('xb') as stream:
                stream.truncate(size)
                for extent in result['extents']:
                    stream.seek(extent['offset_bytes'])
                    stream.write(extent['data'])
            readback = subprocess.run(['sfdisk', '--json', str(image)], check=True,
                                      capture_output=True, text=True)
            table = json.loads(readback.stdout)['partitiontable']
            self.assertEqual(table['label'], 'gpt')
            self.assertEqual(table['id'].lower(), result['disk_guid'])
            self.assertEqual(table['firstlba'], result['first_usable_lba'])
            self.assertEqual(table['lastlba'], result['last_usable_lba'])
            self.assertEqual(len(table['partitions']), 5)
            for observed, expected, identity in zip(table['partitions'],
                    result['layout']['partitions'], result['partition_guids']):
                self.assertEqual(observed['start'], expected['start_lba'])
                self.assertEqual(observed['size'], expected['size_bytes'] // 512)
                self.assertEqual(observed['name'], expected['name'])
                self.assertEqual(observed['uuid'].lower(), identity)
                self.assertEqual(observed['type'].lower(),
                                 '0fc63daf-8483-4772-8e79-3d69d8477de4')
            probe = subprocess.run(['blkid', '-p', '-o', 'export', str(image)],
                                   check=True, capture_output=True, text=True)
            fields = dict(line.split('=', 1) for line in probe.stdout.splitlines())
            self.assertEqual(fields['PTTYPE'], 'gpt')
            self.assertEqual(fields['PTUUID'], result['disk_guid'])


if __name__ == '__main__':
    unittest.main()
