#!/usr/bin/env python3
"""Exercise file-backed diagnostic packaging; fixtures cannot boot a board."""

import gzip
import hashlib
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
import zlib


REPO = Path(__file__).resolve().parent.parent
ASSEMBLER = REPO / "build/assemble-diagnostic.sh"
IMAGE_BYTES = 256 * 1024 * 1024
PARTITION_OFFSET = 32 * 1024 * 1024
DTB_NAME = "sun60i-a733-orangepi-4-pro.dtb"
TOOLS = ("mkimage", "sfdisk", "mkfs.ext4", "e2fsck", "debugfs", "dumpe2fs")


@unittest.skipUnless(all(shutil.which(tool) for tool in TOOLS),
                     "requires U-Boot tools, util-linux and e2fsprogs")
class AssembleDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="a733-assembly-test-")
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)
        self.inputs = self.work / "inputs"
        self.inputs.mkdir()
        self.output = self.work / "diagnostic.img"
        self.environment = dict(os.environ, LC_ALL="C", TZ="UTC",
                                SOURCE_DATE_EPOCH="1793750400")
        (self.inputs / "boot0_sdcard.fex").write_bytes(b"BOOT0-FIXTURE\n" * 17)
        (self.inputs / "boot_package.fex").write_bytes(b"PACKAGE-FIXTURE\n" * 31)
        kernel = self.work / "kernel-payload"
        kernel.write_bytes(b"NONBOOTABLE-KERNEL-FIXTURE\n")
        ramdisk = self.work / "ramdisk-payload.gz"
        ramdisk.write_bytes(gzip.compress(b"NONBOOTABLE-INITRAMFS-FIXTURE\n", mtime=0))
        self.run_checked("mkimage", "-A", "arm", "-O", "linux", "-T", "kernel",
                         "-C", "none", "-a", "0x41000000", "-e", "0x41000000",
                         "-d", kernel, self.inputs / "uImage")
        self.run_checked("mkimage", "-A", "arm", "-O", "linux", "-T", "ramdisk",
                         "-C", "gzip", "-d", ramdisk, self.inputs / "uInitrd")
        # An empty, valid flattened device tree, not a hardware description.
        tree = struct.pack(">IIII", 1, 0, 2, 9)
        header = struct.pack(">10I", 0xD00DFEED, 72, 56, 72, 40, 17, 16, 0, 0, 16)
        (self.inputs / "board.dtb").write_bytes(header + bytes(16) + tree)

    def run_checked(self, *arguments):
        result = subprocess.run([str(argument) for argument in arguments],
                                env=self.environment, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def assemble(self):
        return self.run_checked("bash", ASSEMBLER, self.inputs, self.output)

    def extract_file(self, partition, name):
        destination = self.work / ("extracted-" + Path(name).name)
        self.run_checked("debugfs", "-R", f"dump {name} {destination}", partition)
        self.assertTrue(destination.is_file(), f"debugfs did not extract {name}")
        return destination.read_bytes()

    def test_image_layout_and_boot_files(self):
        self.assemble()
        self.assertEqual(self.output.stat().st_size, IMAGE_BYTES)
        with self.output.open("rb") as image:
            mbr = image.read(512)
            self.assertEqual(mbr[510:512], b"\x55\xaa")
            self.assertEqual(struct.unpack_from("<I", mbr, 440)[0], 0xA7330001)
            entry = mbr[446:462]
            self.assertEqual((entry[0], entry[4]), (0x80, 0x83))
            start, count = struct.unpack_from("<II", entry, 8)
            self.assertEqual((start, count), (65536, 458752))
            self.assertEqual((start + count) * 512, IMAGE_BYTES)
            self.assertEqual(mbr[462:510], bytes(48))
            for name, offset in (("boot0_sdcard.fex", 8192),
                                 ("boot_package.fex", 16793600)):
                with self.subTest(blob=name):
                    expected = (self.inputs / name).read_bytes()
                    image.seek(offset)
                    self.assertEqual(image.read(len(expected)), expected)
                    self.assertEqual(image.read(512), bytes(512))
            partition = self.work / "partition.ext4"
            image.seek(PARTITION_OFFSET)
            with partition.open("wb") as destination:
                shutil.copyfileobj(image, destination)
        self.assertEqual(partition.stat().st_size, 224 * 1024 * 1024)
        self.run_checked("e2fsck", "-fn", partition)
        details = self.run_checked("dumpe2fs", "-h", partition)
        feature_line = next(line for line in details.splitlines()
                            if line.startswith("Filesystem features:"))
        features = set(feature_line.split(":", 1)[1].split())
        self.assertTrue(features.isdisjoint(
            {"has_journal", "64bit", "metadata_csum", "metadata_csum_seed", "gdt_csum"}))
        for name in ("uImage", "uInitrd"):
            with self.subTest(boot_file=name):
                self.assertEqual(self.extract_file(partition, "/boot/" + name),
                                 (self.inputs / name).read_bytes())
        self.assertEqual(self.extract_file(partition, "/boot/dtb/allwinner/" + DTB_NAME),
                         (self.inputs / "board.dtb").read_bytes())
        command = (REPO / "configs/diagnostic-boot.cmd").read_bytes()
        self.assertEqual(self.extract_file(partition, "/boot/boot.cmd"), command)
        script = self.extract_file(partition, "/boot/boot.scr")
        self.assertEqual(struct.unpack_from(">I", script)[0], 0x27051956)
        # Legacy script payload has its size and terminating zero before text.
        self.assertEqual(struct.unpack_from(">II", script, 64), (len(command), 0))
        self.assertEqual(script[72:], command)
        self.assertEqual(struct.unpack_from(">I", script, 12)[0], len(script) - 64)
        self.assertEqual(struct.unpack_from(">I", script, 24)[0],
                         zlib.crc32(script[64:]))

    def test_existing_output_is_preserved(self):
        sentinel = b"EXISTING-OUTPUT-MUST-SURVIVE\n"
        self.output.write_bytes(sentinel)
        result = subprocess.run(["bash", str(ASSEMBLER), str(self.inputs),
                                 str(self.output)], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Output must not exist", result.stderr)
        self.assertEqual(self.output.read_bytes(), sentinel)

    def test_sparse_regular_output_preserves_logical_size_and_zero_tail(self):
        self.assemble()
        info = self.output.stat()
        self.assertEqual(info.st_size, IMAGE_BYTES)
        self.assertLess(info.st_blocks * 512, IMAGE_BYTES // 2)
        with self.output.open('rb') as image:
            image.seek(-1024 * 1024, os.SEEK_END)
            self.assertEqual(image.read(), bytes(1024 * 1024))

    def test_identical_inputs_produce_identical_image(self):
        def digest():
            checksum = hashlib.sha256()
            with self.output.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    checksum.update(chunk)
            return checksum.hexdigest()

        self.assemble()
        first = digest()
        self.output.unlink()
        self.assemble()
        self.assertEqual(digest(), first)

    def test_oversized_inputs_are_rejected_without_output(self):
        # Exact first rejected sizes; sparse files keep these checks lightweight.
        for name, size in (("boot0_sdcard.fex", 16793600 - 8192 + 1),
                           ("boot_package.fex", PARTITION_OFFSET - 16793600 + 1),
                           ("uImage", 33554432),
                           ("board.dtb", 1048576 - 65536),
                           ("uInitrd", 67108864)):
            with self.subTest(input=name):
                path = self.inputs / name
                original = path.read_bytes()
                try:
                    with path.open("wb") as stream:
                        stream.truncate(size)
                    result = subprocess.run(
                        ["bash", str(ASSEMBLER), str(self.inputs), str(self.output)],
                        capture_output=True, text=True)
                    self.assertNotEqual(result.returncode, 0,
                                        result.stdout + result.stderr)
                    self.assertFalse(self.output.exists())
                finally:
                    path.write_bytes(original)


if __name__ == "__main__":
    unittest.main(verbosity=2)
