"""Reject contradictory provenance before inspecting any image."""
import copy
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest
import zlib

SPEC = importlib.util.spec_from_file_location('host_sd_verifier', Path(__file__).with_name('verify_host_sd.py'))
VERIFY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFY)


class HostSDVerifierTests(unittest.TestCase):
    def test_contradictory_provenance_rejected_before_image_open(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output, host = root / 'output', root / 'host'
            output.mkdir()
            host.mkdir()
            archive_record = {'bytes': 1, 'sha256': 'a' * 64}
            trusted = {'state_uuid': 'bfe5ba29-3175-47a3-a410-04b87e3de1a6',
                       'kernel_release': '6.6.98-a733-diag',
                       'files': {'initramfs.cpio.gz': archive_record}}
            data = json.dumps(trusted).encode()
            (host / 'manifest.json').write_bytes(data)
            manifest = {'state_uuid': trusted['state_uuid'], 'kernel_release': trusted['kernel_release'],
                        'trusted_host_manifest': VERIFY.record(data), 'host_initramfs': archive_record}
            for field in ('state_uuid', 'kernel_release', 'trusted_host_manifest', 'host_initramfs'):
                changed = copy.deepcopy(manifest)
                if field in ('state_uuid', 'kernel_release'):
                    changed[field] = 'contradiction'
                else:
                    changed[field]['sha256'] = 'b' * 64
                (output / 'manifest.json').write_text(json.dumps(changed))
                with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'differ'):
                    VERIFY.verify(output, host)

    def test_legacy_wrapper_integrity_and_type(self):
        payload = b'fixture payload'
        header = bytearray(64)
        struct.pack_into('>7I', header, 0, 0x27051956, 0, 1793750400, len(payload), 0, 0, zlib.crc32(payload))
        header[28:32] = bytes((5, 2, 3, 1))
        struct.pack_into('>I', header, 4, zlib.crc32(header))
        wrapped = bytes(header) + payload
        self.assertEqual(VERIFY.wrapper(wrapped, 3, 1), payload)
        for damaged in (wrapped[:-1], wrapped[:64] + b'X' + payload[1:]):
            with self.subTest(damaged=damaged), self.assertRaises(ValueError):
                VERIFY.wrapper(damaged, 3, 1)
        with self.assertRaises(ValueError):
            VERIFY.wrapper(wrapped, 6, 0)


if __name__ == '__main__':
    unittest.main()
