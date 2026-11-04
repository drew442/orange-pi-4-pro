"""Small independent fixtures for the opt-in NVMe regular-file inspector."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
import uuid
import zlib

SPEC = importlib.util.spec_from_file_location('nvme_verifier', Path(__file__).with_name('verify_nvme_image.py'))
VERIFY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFY)


class NVMeVerifierTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def fixture(self, sector=512):
        # Independent test encoding: no production layout/codec imports.
        size = 20 * 1024**3
        last = size // sector - 1
        array_sectors = 16384 // sector
        disk = str(uuid.uuid4())
        ids = [str(uuid.uuid4()) for _ in range(5)]
        entries = bytearray(16384)
        parts = []
        offset = 1024**2
        end = (size - 33 * sector) // 1024**2 * 1024**2
        for number, (role, budget) in enumerate(zip(('boot', 'root-a', 'root-b', 'state', 'guests'),
                                                   (512 * 1024**2, 4 * 1024**3, 4 * 1024**3, 8 * 1024**3, None)), 1):
            length = budget if budget is not None else end - offset
            first, final = offset // sector, (offset + length) // sector - 1
            struct.pack_into('<16s16sQQQ72s', entries, (number - 1) * 128,
                             uuid.UUID('0fc63daf-8483-4772-8e79-3d69d8477de4').bytes_le,
                             uuid.UUID(ids[number - 1]).bytes_le, first, final, 0, role.encode('utf-16-le'))
            parts.append({'number': number, 'name': role, 'start_bytes': offset, 'size_bytes': length,
                          'start_lba': first, 'end_lba': final})
            offset += length
        path = self.root / ('disk-' + str(sector))
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        self.addCleanup(os.close, fd)
        os.ftruncate(fd, size)
        mbr = bytearray(sector)
        struct.pack_into('<B3sB3sII', mbr, 446, 0, b'\0\2\0', 0xee, b'\xff'*3, 1, min(last, 0xffffffff))
        mbr[510:512] = b'\x55\xaa'
        os.pwrite(fd, mbr, 0)
        for current, alternate, array_lba in ((1, last, 2), (last, 1, last - array_sectors)):
            header = bytearray(sector)
            struct.pack_into('<8sIIIIQQQQ16sQIII', header, 0, b'EFI PART', 0x10000, 92, 0, 0,
                             current, alternate, 2 + array_sectors, last - array_sectors - 1,
                             uuid.UUID(disk).bytes_le, array_lba, 128, 128, zlib.crc32(entries))
            struct.pack_into('<I', header, 16, zlib.crc32(header[:92]))
            os.pwrite(fd, header, current * sector)
            os.pwrite(fd, entries, array_lba * sector)
        manifest = {'logical_sector_bytes': sector, 'physical_sector_bytes': 4096,
                    'disk_guid': disk, 'partition_guids': ids,
                    'layout': {'table': 'gpt', 'alignment_bytes': 1024**2, 'front_reserved_bytes': 1024**2,
                               'tail_reserved_bytes': size - end, 'partitions': parts}}
        return fd, manifest, size

    def test_independent_gpt_both_sector_sizes(self):
        for sector in (512, 4096):
            with self.subTest(sector=sector):
                fd, manifest, size = self.fixture(sector)
                self.assertEqual(VERIFY.inspect_gpt(fd, manifest, size), manifest['layout']['partitions'])

    def repair_headers(self, fd, sector, size):
        last = size // sector - 1
        for current, array_lba in ((1, 2), (last, last - 16384 // sector)):
            block = bytearray(os.pread(fd, sector, current * sector))
            struct.pack_into('<I', block, 88, zlib.crc32(os.pread(fd, 16384, array_lba * sector)))
            block[16:20] = bytes(4)
            struct.pack_into('<I', block, 16, zlib.crc32(block[:92]))
            os.pwrite(fd, block, current * sector)

    def test_gpt_header_mbr_and_array_tampering(self):
        fd, manifest, size = self.fixture()
        locations = [0, 510, 512, 512 + 16, 512 + 92, 1024, size - 512, size - 1024]
        for offset in locations:
            original = os.pread(fd, 1, offset)
            os.pwrite(fd, bytes([original[0] ^ 1]), offset)
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                VERIFY.inspect_gpt(fd, manifest, size)
            os.pwrite(fd, original, offset)

    def test_crc_correct_structural_tampering(self):
        fd, manifest, size = self.fixture()
        # Repaired header CRC cannot hide wrong usable LBA, count or reserved.
        original = os.pread(fd, 512, 512)
        for offset in (20, 24, 32, 40, 48, 72, 80, 84):
            header = bytearray(original)
            header[offset] ^= 1
            os.pwrite(fd, header, 512)
            self.repair_headers(fd, 512, size)
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                VERIFY.inspect_gpt(fd, manifest, size)
            os.pwrite(fd, original, 512)
        # Repaired array and header CRCs cannot hide role/type/extent/unused tampering.
        array = os.pread(fd, 16384, 1024)
        backup = size - 512 - 16384
        for offset in (0, 16, 32, 40, 48, 56, 5 * 128):
            changed = bytearray(array)
            changed[offset] ^= 1
            for position in (1024, backup):
                os.pwrite(fd, changed, position)
            self.repair_headers(fd, 512, size)
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                VERIFY.inspect_gpt(fd, manifest, size)
            for position in (1024, backup):
                os.pwrite(fd, array, position)
            self.repair_headers(fd, 512, size)

    def test_manifest_geometry_and_identity_tampering(self):
        fd, manifest, size = self.fixture()
        for field in ('disk_guid', 'partition_guids', 'layout', 'logical_sector_bytes'):
            changed = copy.deepcopy(manifest)
            if field == 'disk_guid':
                changed[field] = str(uuid.uuid4())
            elif field == 'partition_guids':
                changed[field][0] = changed[field][1]
            elif field == 'layout':
                changed[field]['partitions'][0]['size_bytes'] += 512
            else:
                changed[field] = 1024
            with self.subTest(field=field), self.assertRaises(ValueError):
                VERIFY.inspect_gpt(fd, changed, size)

    def test_sparse_capture_clips_bounds_and_preserves_holes(self):
        source = self.root / 'source'
        with source.open('wb') as stream:
            stream.truncate(32 * 1024**2)
        fd = os.open(source, os.O_RDONLY)
        self.addCleanup(os.close, fd)
        with source.open('r+b') as stream:
            stream.write(b'outside-left')
            stream.seek(1024**2 - 3)
            stream.write(b'abcINSIDE')
            stream.seek(7 * 1024**2 - 4)
            stream.write(b'TAILoutside-right')
        target = self.root / 'capture'
        VERIFY.sparse_capture(fd, 1024**2, 6 * 1024**2, target)
        with target.open('rb') as stream:
            self.assertEqual(stream.read(6), b'INSIDE')
            stream.seek(-4, os.SEEK_END)
            self.assertEqual(stream.read(), b'TAIL')
        self.assertEqual(target.stat().st_size, 6 * 1024**2)
        self.assertLess(target.stat().st_blocks * 512, target.stat().st_size // 4)
        output = os.open(target, os.O_RDONLY)
        try:
            self.assertEqual(VERIFY.digest(fd, 1024**2, 6 * 1024**2), VERIFY.digest(output, 0, 6 * 1024**2))
        finally:
            os.close(output)
        with self.assertRaises(FileExistsError):
            VERIFY.sparse_capture(fd, 1024**2, 6 * 1024**2, target)
        with self.assertRaises(ValueError):
            VERIFY.sparse_capture(fd, -1, 512, self.root / 'bad')

    def test_nonregular_and_symlink_paths_refused(self):
        ordinary = self.root / 'ordinary'
        ordinary.write_bytes(b'ok')
        link = self.root / 'link'
        link.symlink_to(ordinary)
        fifo = self.root / 'fifo'
        os.mkfifo(fifo)
        for path in (link, fifo, self.root, Path('/dev/null')):
            with self.subTest(path=path), self.assertRaises((OSError, ValueError)):
                VERIFY.open_regular(path)
        (self.root / 'manifest.json').symlink_to(ordinary)
        with self.assertRaises(OSError):
            VERIFY.verify(self.root)

    def test_manifest_bound(self):
        with (self.root / 'manifest.json').open('wb') as stream:
            stream.truncate(4 * 1024**2 + 1)
        with self.assertRaisesRegex(ValueError, 'bound'):
            VERIFY.verify(self.root)

    def test_ext4_features_and_uuid(self):
        image = self.root / 'sb'
        image.write_bytes(bytes(2048))
        fd = os.open(image, os.O_RDWR)
        self.addCleanup(os.close, fd)
        identity = str(uuid.uuid4())
        block = bytearray(1024)
        block[56:58] = b'\x53\xef'
        block[104:120] = uuid.UUID(identity).bytes
        for role, compat, incompat, ro in (('boot', 0, 0, 0), ('root-a', 0, 0, 0x1000),
                                          ('root-b', 0, 0, 0x1000), ('state', 4, 0, 0), ('guests', 4, 0, 0)):
            struct.pack_into('<III', block, 92, compat, incompat, ro)
            os.pwrite(fd, block, 1024)
            part = {'name': role, 'start_bytes': 0}
            VERIFY.superblock(fd, part, identity)
            with self.assertRaises(ValueError):
                VERIFY.superblock(fd, part, str(uuid.uuid4()))
            struct.pack_into('<III', block, 92, compat ^ 4, incompat, ro)
            os.pwrite(fd, block, 1024)
            with self.subTest(role=role), self.assertRaises(ValueError):
                VERIFY.superblock(fd, part, identity)
        for incompat, ro in ((0x80, 0), (0, 0x400), (0, 0x1000)):
            struct.pack_into('<III', block, 92, 0, incompat, ro)
            os.pwrite(fd, block, 1024)
            with self.assertRaises(ValueError):
                VERIFY.superblock(fd, {'name': 'boot', 'start_bytes': 0}, identity)

    @unittest.skipUnless(shutil.which('mke2fs') and shutil.which('debugfs'), 'e2fsprogs required')
    def test_debugfs_missing_file_and_output_bound(self):
        image = self.root / 'small.ext4'
        with image.open('wb') as stream:
            stream.truncate(16 * 1024**2)
        subprocess.run(['mke2fs', '-q', '-F', '-t', 'ext4', str(image)], check=True, capture_output=True)
        VERIFY.metadata(image, '/', 'directory', 0o755)
        with self.assertRaises(ValueError):
            VERIFY.metadata(image, '/missing', 'regular')
        with self.assertRaises(ValueError):
            VERIFY.debugfs(image, 'cat /missing')
        with self.assertRaises(ValueError):
            VERIFY.debugfs(image, 'stat /', limit=8)


if __name__ == '__main__':
    unittest.main()
