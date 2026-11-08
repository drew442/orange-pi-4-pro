"""Tiny host-only safety checks; no images, target programs or hardware."""
import gzip
import importlib.util
import json
from pathlib import Path
import stat
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('assemble_ssh_sd_test', REPO / 'build/assemble-ssh-installer.py')
SD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SD)


def wrapper(payload, kind=3):
    header = bytearray(struct.pack('>7I4B32s', 0x27051956, 0, SD.EPOCH, len(payload),
                                  0, 0, zlib.crc32(payload), 5, 2, kind,
                                  1 if kind == 3 else 0, bytes(32)))
    struct.pack_into('>I', header, 4, zlib.crc32(header))
    return bytes(header) + payload


class Safety(unittest.TestCase):
    def test_held_file_rejects_links_and_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'input'
            path.write_bytes(b'pinned')
            link = Path(directory) / 'alias'
            link.symlink_to(path)
            with self.assertRaises(ValueError):
                SD.BASE.HeldInput(link, 20)
            link.unlink()
            import os
            os.link(path, link)
            with self.assertRaises(ValueError):
                SD.BASE.HeldInput(path, 20)
            link.unlink()
            with SD.BASE.HeldInput(path, 20) as held:
                path.write_bytes(b'change')
                with self.assertRaises(ValueError):
                    held.verify()

    def test_forbidden_inputs_and_output_tags(self):
        for path in ('relative', '/dev/null', '/proc/meminfo', '/sys/kernel'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                SD.BASE.safe_regular_path(path)
        for tag in ('', '../other', 'a/b', 'a' * 65):
            with self.assertRaises(ValueError):
                SD.output_path(tag)

    def test_wrappers_require_exact_header_crc_and_payload(self):
        payload = gzip.compress(b'fixture', mtime=0)
        good = wrapper(payload)
        SD.validate_wrapper(good, payload, 3)
        script = struct.pack('>II', 3, 0) + b'cmd'
        SD.validate_wrapper(wrapper(script, 6), script, 6)
        for position in (0, 4, 8, 16, 24, 28, 29, 30, 31, 32, 64):
            bad = bytearray(good)
            bad[position] ^= 1
            with self.subTest(position=position), self.assertRaises(ValueError):
                SD.validate_wrapper(bytes(bad), payload, 3)
        with self.assertRaises(ValueError):
            SD.validate_wrapper(good + b'x', payload, 3)

    def archive(self):
        regular = stat.S_IFREG | 0o644
        entries = {'init': (stat.S_IFREG | 0o755, b'#!/bin/sh\n')}
        maintenance = sorted('usr/lib/a733-tools/' + name for name in (*SD.SSH.TOOLS.SOURCES, 'package.json'))
        entries.update({name: (regular, name.encode()) for name in maintenance})
        original = {'entries': {name: {'mode': entries[name][0], **SD.HOST.record(entries[name][1])}
                                for name in maintenance}}
        receipt = json.dumps(original).encode()
        base_path = SD.SSH.PROVENANCE + 'base-manifest.json'
        entries[base_path] = (regular, receipt)
        provenance = b'captured producer'
        entries[SD.SSH.PROVENANCE + 'build/fixture.py'] = (regular, provenance)
        raw = SD.SSH.INIT.newc(entries)
        packed = gzip.compress(raw, mtime=0)
        manifest = {'schema': 1, 'profile': 'a733-private-ssh-ram-installer-v1',
                    'files': {'initramfs.cpio.gz': SD.HOST.record(packed)},
                    'entries': {name: {'mode': mode, **SD.HOST.record(data)} for name, (mode, data) in entries.items()},
                    'expanded_bytes': len(raw), 'maintenance_preserved': maintenance,
                    'base_manifest_path': base_path, 'base_manifest': SD.HOST.record(receipt),
                    'recipes': {'build/fixture.py': SD.HOST.record(provenance)}}
        return packed, manifest

    def test_archive_matches_historical_maintenance_and_provenance(self):
        packed, manifest = self.archive()
        with patch.object(SD, 'ARCHIVE', SD.HOST.record(packed)):
            SD.validate_archive(packed, manifest)
            manifest['maintenance_preserved'].pop()
            with self.assertRaises(ValueError):
                SD.validate_archive(packed, manifest)
        packed, manifest = self.archive()
        manifest['recipes']['build/fixture.py']['sha256'] = '0' * 64
        with patch.object(SD, 'ARCHIVE', SD.HOST.record(packed)), self.assertRaises(ValueError):
            SD.validate_archive(packed, manifest)

    def test_archive_authentication_precedes_decompression(self):
        with patch.object(SD.SSH, 'unpack') as unpack:
            with self.assertRaises(ValueError):
                SD.validate_archive(b'unauthenticated', {})
            unpack.assert_not_called()

    def test_gzip_expansion_is_bounded(self):
        packed, manifest = self.archive()
        with patch.object(SD, 'ARCHIVE', SD.HOST.record(packed)), patch.object(SD, 'MAX_EXPANDED', 8):
            with self.assertRaises(ValueError):
                SD.validate_archive(packed, manifest)

    def test_resources_preserve_floor_and_memory(self):
        from collections import namedtuple
        usage = namedtuple('usage', 'total used free')
        with patch.object(SD.shutil, 'disk_usage', return_value=usage(1, 1, SD.FREE_FLOOR - 1)):
            with self.assertRaises(ValueError):
                SD.resources(REPO)
        with patch.object(SD.shutil, 'disk_usage', return_value=usage(1, 1, SD.FREE_FLOOR + 1)), \
                patch.object(SD.Path, 'read_text', return_value='MemAvailable: 999999 kB\n'):
            SD.resources(REPO)
            with self.assertRaises(ValueError):
                SD.resources(REPO, admission=True)

    def test_prefix_checks_every_byte(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = (Path(directory) / name for name in ('source', 'target'))
            source.write_bytes(b'complete prefix')
            target.write_bytes(source.read_bytes())
            with SD.BASE.HeldInput(source, 100) as held, patch.object(SD, 'PREFIX', SD.HOST.record(source.read_bytes())):
                SD.verify_prefix(held, target)
                target.write_bytes(b'complete prefiX')
                with self.assertRaises(ValueError):
                    SD.verify_prefix(held, target)


if __name__ == '__main__':
    unittest.main()
