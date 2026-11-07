#!/usr/bin/env python3
"""Small file/stream fixtures only; no hardware or production image claim."""
import copy
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import types
import unittest
from unittest import mock
import uuid
import zlib

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('nvme_stream', ROOT / 'build/verify-nvme-stream.py')
V = importlib.util.module_from_spec(spec)
spec.loader.exec_module(V)


def packed(data):
    output = io.BytesIO()
    with gzip.GzipFile(fileobj=output, filename='', mode='wb', compresslevel=9, mtime=0) as stream:
        stream.write(data)
    return output.getvalue()


def source_fixture():
    producers = {name: json.loads((ROOT / ('docs/evidence/production-host-' + name + '-20261107.json')).read_bytes())
                 for name in ('roots', 'state', 'bootstrap')}
    command = {'name': 'spi-nvme-v1', 'source': {'path': 'configs/spi-nvme-host-boot.cmd', **V.SPI},
               'payload_context': {'devtype': 'nvme', 'devnum': 0, 'partition': 1, 'prefix': '/boot/'},
               'firmware_search_policy': 'Broader pinned distro scan; inspect GPT attributes and complete boot filesystem separately.',
               'physical_acceptance_pending': True}
    raw = {'bytes': V.SIZE, 'sha256': 'a' * 64}
    payloads = {'root-a': producers['roots']['readback']['slots']['A']['image'],
                'root-b': producers['roots']['readback']['slots']['B']['image'], 'state': producers['state']['state_image']}
    manifest = {'schema': 1, 'logical_sector_bytes': 512, 'physical_sector_bytes': 512,
                'state_uuid': V.STATE_UUID, 'boot_profile': copy.deepcopy(producers['bootstrap']['boot_profile']),
                'deployment_intent': {'for_installation': True}, 'boot_selection': producers['state']['boot_selection'],
                'nvme_boot_command_profile': command, 'boot_files': {'boot/boot.cmd': V.SPI}, 'recipes': {},
                'inputs': {k + '_manifest': producers[n]['verification']['manifest']
                           for k, n in (('roots', 'roots'), ('state', 'state'), ('sd', 'bootstrap'))},
                'partition_payloads': payloads, 'files': {'nvme.img': raw}}
    manifest['filesystem_uuids'] = {'root-a': producers['roots']['slot_uuids']['A'],
                                  'root-b': producers['roots']['slot_uuids']['B'], 'state': V.STATE_UUID}
    manifest['kernel_release'] = '6.6.98-a733-diag'
    manifest_bytes = json.dumps(manifest).encode()
    helper = {'path': 'tests/verify_host_sd.py', 'bytes': 1, 'sha256': 'b' * 64}
    proof = {'manifest': V.pin(manifest_bytes), 'image': raw, 'partition_payloads': payloads,
             'boot_file_hashes': manifest['boot_files'], 'gpt_primary_backup_and_geometry': True,
             'root_markers_and_state_selection': True, 'image_metadata_unchanged': True,
             'spi_script_wrapper': {'header_type_crc_body': True, 'helper': helper},
             'complete_new_filesystem_readback': {'boot': {}, 'guests': {}}}
    receipt = {'schema': 1, 'success': True, 'state': 'complete', 'returncode': 0, 'hardware_acceptance': False,
               'physical_storage_written': False, 'host_inventory_unchanged': True,
               'mapped_processes_final': [], 'phase_mounts_final': [], 'sources': {'revision': V.REVISION},
               'phase': V.PHASE, 'output': 'work/project/out/nvme-image-timesync-03',
               'policy': {'producers': producers, 'state_uuid': V.STATE_UUID,
                   'boot_profile': copy.deepcopy(producers['bootstrap']['boot_profile']),
                   'target_geometry': {'model': 'SPCC M.2 PCIe SSD', 'serial': '134807190FC700149277', 'bytes': V.SIZE,
                       'logical_sector_bytes': 512, 'physical_sector_bytes': 512},
                   'nvme_boot_command_profile': command, 'nvme_boot_command_source': command['source'],
                   'recipes': {}, 'spi_wrapper_helper': helper},
               'verification': {'manifest': V.pin(manifest_bytes), 'image': raw, 'hardware_acceptance': False},
               'readback_expectations': {'nvme.json': {'value': proof}, 'compression.json': {'value': {
                   'schema': 1, 'raw': raw, 'gzip': {'bytes': 1234, 'sha256': 'c' * 64},
                   'filename': '', 'mtime': 0, 'level': 9, 'input_identity_unchanged': True,
                   'gzip_expansion_independent_verification': 'pending'}}}}
    return receipt, manifest


class DecoderTests(unittest.TestCase):
    def decode(self, data, wire=None, compressed=None, raw=None, fence=lambda: None):
        wire = packed(data) if wire is None else wire
        capture = V.Ranges(len(data), [])
        result = V.decode(io.BytesIO(wire), compressed or V.pin(wire), raw or V.pin(data), capture, fence)
        return result, capture

    def test_real_small_gzip_and_multiple_output_chunks(self):
        data = b'bounded expansion\n' * 200000
        result, capture = self.decode(data)
        self.assertEqual(result[1], V.pin(data))
        self.assertEqual(capture.offset, len(data))
        self.assertEqual(capture.read_at(None, 16, 0), data[:16])
        self.assertEqual(capture.read_at(None, 16, len(data) - 16), data[-16:])

    def test_all_header_fields_closed(self):
        data = b'small'
        original = packed(data)
        for offset in range(10):
            wire = bytearray(original)
            wire[offset] ^= 1
            with self.subTest(offset=offset), self.assertRaises((ValueError, zlib.error)):
                self.decode(data, wire=bytes(wire))

    def test_crc_truncated_append_and_second_member(self):
        data = b'CRC fixture' * 1000
        original = packed(data)
        corrupt = bytearray(original)
        corrupt[-8] ^= 1
        for wire in (bytes(corrupt), original[:-1], original[:8], original + b'x', original + packed(b'x')):
            with self.subTest(length=len(wire)), self.assertRaises((ValueError, zlib.error)):
                self.decode(data, wire=wire)

    def test_wire_raw_overflow_short_and_hashes(self):
        data = b'hello' * 100
        wire = packed(data)
        for compressed, raw in (({**V.pin(wire), 'bytes': len(wire) - 1}, V.pin(data)),
                                (V.pin(wire), {**V.pin(data), 'bytes': len(data) - 1}),
                                ({**V.pin(wire), 'bytes': len(wire) + 1}, V.pin(data)),
                                ({**V.pin(wire), 'sha256': '0' * 64}, V.pin(data)),
                                (V.pin(wire), {**V.pin(data), 'sha256': '0' * 64})):
            with self.subTest(compressed=compressed, raw=raw), self.assertRaises(ValueError):
                self.decode(data, compressed=compressed, raw=raw)

    def test_ranges_partition_hash_clips_boundaries(self):
        data = bytes(range(256)) * 64
        parts = [{'name': 'root-a', 'start_bytes': 100, 'size_bytes': 2048},
                 {'name': 'state', 'start_bytes': 3000, 'size_bytes': 4096}]
        ranges = V.Ranges(len(data), parts)
        for start in range(0, len(data), 997):
            ranges.add(data[start:start + 997])
        self.assertEqual(ranges.payloads(), {p['name']: V.pin(data[p['start_bytes']:p['start_bytes'] + p['size_bytes']]) for p in parts})
        self.assertEqual(ranges.read_at(None, 1024, 1124), data[1124:2148])
        with self.assertRaises(ValueError):
            ranges.read_at(None, len(data) + 1, 0)
        with self.assertRaises(ValueError):
            ranges.add(b'x')

    def test_uncaptured_and_incomplete_memory_ranges(self):
        ranges = V.Ranges(2 * V.MIB, [])
        with self.assertRaises(ValueError):
            ranges.read_at(None, 1, 0)
        with self.assertRaises(ValueError):
            ranges.read_at(None, 1, V.MIB)
        with self.assertRaises(ValueError):
            V.Ranges(10, [{'name': 'state', 'start_bytes': 0, 'size_bytes': 3000}])

    def test_decoder_calls_mutation_fence(self):
        calls = []
        self.decode(b'x' * 4096, fence=lambda: calls.append(True))
        self.assertEqual(len(calls), 2)
        with self.assertRaisesRegex(ValueError, 'mutation'):
            self.decode(b'x', fence=lambda: V.require(False, 'mutation'))

    def test_decode_never_opens_files(self):
        data = b'no filesystem output' * 100
        wire = packed(data)
        with mock.patch('builtins.open', side_effect=AssertionError('file open')), \
                mock.patch.object(os, 'open', side_effect=AssertionError('descriptor open')):
            self.decode(data, wire=wire)

    def test_metadata_mutation_during_stream_fails_final_fence(self):
        with tempfile.TemporaryDirectory() as tmp:
            leaf = Path(tmp) / 'receipt'; leaf.write_bytes(b'{}')
            data = b'actual streamed mutation test'
            class MutatingStream(io.BytesIO):
                def read(self, count):
                    result = super().read(count)
                    if result:
                        leaf.write_bytes(b'[]')
                    return result
            with V.Held(leaf) as held, self.assertRaises(ValueError):
                wire = packed(data)
                V.decode(MutatingStream(wire), V.pin(wire), V.pin(data),
                         V.Ranges(len(data), []), held.unchanged)


class MetadataTests(unittest.TestCase):
    def test_readonly_held_inputs_and_sibling_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'receipt.json'
            p.write_bytes(b'{}')
            before = p.stat()
            with V.Held(p, V.pin(b'{}')) as held:
                (Path(tmp) / 'new-log').write_bytes(b'log')
                held.unchanged()
                self.assertEqual(held.data, b'{}')
            self.assertEqual(before.st_mtime_ns, p.stat().st_mtime_ns)
            self.assertEqual({x.name for x in Path(tmp).iterdir()}, {'receipt.json', 'new-log'})

    def test_leaf_parent_symlinks_hardlinks_and_directory_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            real = base / 'real'; real.mkdir()
            leaf = real / 'metadata'; leaf.write_bytes(b'{}')
            (base / 'link').symlink_to(real, target_is_directory=True)
            (real / 'leaf-link').symlink_to(leaf)
            for path in (base / 'link/metadata', real / 'leaf-link', real):
                with self.subTest(path=path), self.assertRaises((OSError, ValueError)):
                    V.Held(path)
            os.link(leaf, real / 'hard')
            with self.assertRaises(ValueError):
                V.Held(leaf)

    def test_device_and_pseudo_paths_refuse_before_any_open(self):
        for path in ('/dev/null', '/dev/nvme0n1', '/proc/self/status', '/sys/kernel/uevent_seqnum'):
            with self.subTest(path=path), \
                    mock.patch.object(V.os, 'open', side_effect=AssertionError('path opened')):
                with self.assertRaisesRegex(ValueError, 'pseudo-filesystem'):
                    V.Held(path)

    def test_nonregular_leaf_refuses_before_leaf_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            leaf = Path(tmp) / 'fifo'
            os.mkfifo(leaf)
            original = os.open
            def guarded(path, *args, **kwargs):
                if path == leaf.name and not args[0] & os.O_PATH:
                    raise AssertionError('nonregular leaf opened')
                return original(path, *args, **kwargs)
            with mock.patch.object(V.os, 'open', side_effect=guarded):
                with self.assertRaisesRegex(ValueError, 'single regular metadata'):
                    V.Held(leaf)

    def test_leaf_replacement_cannot_redirect_checked_descriptor_to_fifo(self):
        with tempfile.TemporaryDirectory() as tmp:
            leaf = Path(tmp) / 'metadata'
            leaf.write_bytes(b'{}')
            original = os.open
            data_opens = []
            def replace_after_pin(path, flags, *args, **kwargs):
                fd = original(path, flags, *args, **kwargs)
                if path == leaf.name and flags & os.O_PATH:
                    leaf.rename(leaf.with_name('original'))
                    os.mkfifo(leaf)
                if str(path).startswith('/proc/self/fd/'):
                    data_opens.append(os.fstat(fd).st_mode)
                return fd
            with mock.patch.object(V.os, 'open', side_effect=replace_after_pin):
                with self.assertRaisesRegex(ValueError, 'metadata identity changed'):
                    V.Held(leaf)
            self.assertEqual(len(data_opens), 1)
            self.assertTrue(V.stat.S_ISREG(data_opens[0]))

    def test_substitution_and_same_length_mutation_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            leaf = Path(tmp) / 'metadata'
            leaf.write_bytes(b'{}')
            with V.Held(leaf) as held:
                leaf.write_bytes(b'[]')
                with self.assertRaises(ValueError):
                    held.unchanged()
            leaf.write_bytes(b'{}')
            with V.Held(leaf) as held:
                leaf.rename(leaf.with_name('old'))
                leaf.write_bytes(b'{}')
                with self.assertRaises(ValueError):
                    held.unchanged()

    def test_ancestor_replacement_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp) / 'parent'; parent.mkdir()
            leaf = parent / 'metadata'; leaf.write_bytes(b'{}')
            with V.Held(leaf) as held:
                parent.rename(parent.with_name('old'))
                parent.mkdir(); leaf.write_bytes(b'{}')
                with self.assertRaises(ValueError):
                    held.unchanged()

    def test_bounded_json_duplicate_keys_and_pin(self):
        with tempfile.TemporaryDirectory() as tmp:
            leaf = Path(tmp) / 'metadata'; leaf.write_bytes(b'{}')
            with self.assertRaises(ValueError):
                V.Held(leaf, V.pin(b'[]'))
            with leaf.open('wb') as f:
                f.truncate(4 * V.MIB + 1)
            with self.assertRaises(ValueError):
                V.Held(leaf)
        for data in (b'{"x":1,"x":2}', b'{"x":NaN}', b'[' * 65 + b']' * 65,
                     b'[' + b'0,' * 32769 + b'0]'):
            with self.assertRaises(ValueError):
                V.json_bytes(data)


class AuthenticationTests(unittest.TestCase):
    def test_full_source_boot_profile_contract(self):
        receipt, manifest = source_fixture()
        expected = {
            'name': 'experimental-el2', 'target_service': '0x8000ff40',
            'expected_spsr': '0x3c9', 'physical_acceptance_pending': True,
            'evidence': {'path': 'docs/evidence/el2-diagnostic-20261104.json', 'bytes': 6582,
                         'sha256': '9c30d5d873fa8ad154b8068cdebfc7f40f6487ce6a79f76a52da4dd44ba1b5f8'},
        }
        self.assertEqual(receipt['policy']['boot_profile'], expected)
        self.assertEqual(manifest['boot_profile'], expected)
        V.authenticate(receipt, manifest)
        for target in ('policy', 'manifest'):
            for replacement in ('experimental-el2', {'name': 'experimental-el2'},
                                {**expected, 'evidence': {**expected['evidence'], 'sha256': '0' * 64}},
                                {**expected, 'target_service': '0x8000ff41'},
                                {**expected, 'expected_spsr': '0x3c5'}):
                changed_receipt, changed_manifest = source_fixture()
                container = changed_receipt['policy'] if target == 'policy' else changed_manifest
                container['boot_profile'] = replacement
                with self.subTest(target=target, replacement=replacement), self.assertRaises(ValueError):
                    V.authenticate(changed_receipt, changed_manifest)

    def test_closed_production_contract(self):
        receipt, manifest = source_fixture()
        compressed, raw, proof = V.authenticate(receipt, manifest)
        self.assertEqual(raw['bytes'], V.SIZE)
        self.assertEqual(compressed['bytes'], 1234)
        self.assertTrue(proof['gpt_primary_backup_and_geometry'])

    def test_failed_partial_stale_and_substituted_policy(self):
        mutations = [lambda r, m: r.update(success=False), lambda r, m: r.update(state='running'),
                     lambda r, m: r.update(returncode=1), lambda r, m: r.update(mapped_processes_final=[123]),
                     lambda r, m: r.update(phase_mounts_final=['/mount']), lambda r, m: r.update(host_inventory_unchanged=False),
                     lambda r, m: r['sources'].update(revision='0' * 40), lambda r, m: r.update(phase=V.PHASE + '-old'),
                     lambda r, m: r['policy']['target_geometry'].update(bytes=V.SIZE - 512),
                     lambda r, m: r['policy']['producers']['state'].update(state_uuid=str(uuid.uuid4())),
                     lambda r, m: m.update(state_uuid=str(uuid.uuid4())),
                     lambda r, m: m['partition_payloads']['state'].update(sha256='0' * 64),
                     lambda r, m: m['inputs']['sd_manifest'].update(sha256='0' * 64),
                     lambda r, m: m['boot_files']['boot/boot.cmd'].update(bytes=1314),
                     lambda r, m: r['readback_expectations']['compression.json']['value'].update(filename='nvme.img'),
                     lambda r, m: r['readback_expectations']['compression.json']['value'].update(gzip_expansion_independent_verification=True)]
        for number, mutation in enumerate(mutations):
            receipt, manifest = copy.deepcopy(source_fixture())
            mutation(receipt, manifest)
            with self.subTest(number=number), self.assertRaises(ValueError):
                V.authenticate(receipt, manifest)

    def test_receipt_manifest_and_helper_pins_before_stream_read(self):
        class Unreadable:
            def read(self, _):
                raise AssertionError('stream read before authentication')
        with tempfile.TemporaryDirectory() as tmp:
            receipt, manifest = source_fixture()
            p, m = Path(tmp) / 'receipt', Path(tmp) / 'manifest'
            p.write_bytes(json.dumps(receipt).encode()); m.write_bytes(json.dumps(manifest).encode())
            with self.assertRaisesRegex(ValueError, 'receipt SHA256'):
                V.verify(Unreadable(), p, '0' * 64, m)
            m.write_bytes(b'{}')
            with self.assertRaisesRegex(ValueError, 'metadata pin mismatch'):
                V.verify(Unreadable(), p, V.pin(p.read_bytes())['sha256'], m)
            m.write_bytes(json.dumps(manifest).encode())
            with mock.patch.object(V, 'HELPER', {'bytes': 15781, 'sha256': '0' * 64}):
                with self.assertRaisesRegex(ValueError, 'metadata pin mismatch'):
                    V.verify(Unreadable(), p, V.pin(p.read_bytes())['sha256'], m)


class GptTests(unittest.TestCase):
    def test_superblock_features_uuids_and_block_geometry(self):
        # Real authenticated superblock parser, with GPT stubbed because this
        # fixture deliberately has tiny partitions and is not production GPT.
        helper_bytes = (ROOT / 'tests/verify_nvme_image.py').read_bytes()
        self.assertEqual(V.pin(helper_bytes), V.HELPER)
        helper = types.ModuleType('trusted_superblock')
        exec(compile(helper_bytes, 'authenticated helper', 'exec'), helper.__dict__)
        parts = [{'name': role, 'start_bytes': 4096 * i, 'size_bytes': 4096}
                 for i, role in enumerate(helper.ROLES)]
        fsids = {role: str(uuid.UUID(int=i + 300)) for i, role in enumerate(helper.ROLES)}
        fsids['state'] = V.STATE_UUID
        original = bytearray(4096 * 5)
        for part in parts:
            offset, role = part['start_bytes'] + 1024, part['name']
            struct.pack_into('<I', original, offset + 4, 4)
            original[offset + 56:offset + 58] = b'\x53\xef'
            original[offset + 104:offset + 120] = uuid.UUID(fsids[role]).bytes
            if role in ('root-a', 'root-b'):
                struct.pack_into('<I', original, offset + 100, 0x1000)
            elif role in ('state', 'guests'):
                struct.pack_into('<I', original, offset + 92, 4)
        helper.inspect_gpt = lambda _fd, _manifest, _size: parts
        def inspect(data):
            capture = V.Ranges(len(data), parts); capture.add(data)
            manifest = {'filesystem_uuids': fsids, 'partition_payloads': capture.payloads()}
            return V.inspect(capture, manifest, helper)
        self.assertEqual(inspect(original)[0], parts)
        for index, field, value in ((1, 104, b'\0' * 16), (1, 100, struct.pack('<I', 0)),
                                    (3, 92, struct.pack('<I', 0)), (0, 92, struct.pack('<I', 4)),
                                    (2, 4, struct.pack('<I', 5)), (4, 24, struct.pack('<I', 7))):
            data = bytearray(original)
            offset = parts[index]['start_bytes'] + 1024 + field
            data[offset:offset + len(value)] = value
            with self.subTest(index=index, field=field), self.assertRaises(ValueError):
                inspect(data)

    def test_actual_geometry_gpt_from_bounded_memory(self):
        helper_bytes = (ROOT / 'tests/verify_nvme_image.py').read_bytes()
        self.assertEqual(V.pin(helper_bytes), V.HELPER)
        helper = types.ModuleType('trusted_gpt')
        exec(compile(helper_bytes, 'authenticated helper', 'exec'), helper.__dict__)
        sector, size = 512, V.SIZE
        last = size // sector - 1
        end_bytes = (size - 33 * sector) // V.MIB * V.MIB
        disk = 'a7330000-0000-4000-8000-000000000001'
        ids = [str(uuid.UUID(int=i + 100)) for i in range(5)]
        array = bytearray(16384)
        offset = V.MIB
        parts = []
        for number, (role, budget) in enumerate(zip(helper.ROLES, (512 * V.MIB, 4 * 1024**3, 4 * 1024**3, 8 * 1024**3, None)), 1):
            length = end_bytes - offset if budget is None else budget
            start, end = offset // sector, (offset + length) // sector - 1
            struct.pack_into('<16s16sQQQ72s', array, (number - 1) * 128, uuid.UUID(helper.LINUX_GUID).bytes_le,
                             uuid.UUID(ids[number - 1]).bytes_le, start, end, 0, role.encode('utf-16-le').ljust(72, b'\0'))
            parts.append({'number': number, 'name': role, 'start_bytes': offset, 'size_bytes': length, 'start_lba': start, 'end_lba': end})
            offset += length
        manifest = {'logical_sector_bytes': sector, 'physical_sector_bytes': sector, 'disk_guid': disk,
                    'partition_guids': ids, 'layout': {'table': 'gpt', 'alignment_bytes': V.MIB,
                        'front_reserved_bytes': V.MIB, 'tail_reserved_bytes': size - end_bytes, 'partitions': parts}}
        front, tail = bytearray(65536), bytearray(65536)
        struct.pack_into('<B3sB3sII', front, 446, 0, b'\0\2\0', 0xee, b'\xff\xff\xff', 1, min(last, 0xffffffff))
        front[510:512] = b'\x55\xaa'
        front[1024:1024 + len(array)] = array
        tail[-512 - len(array):-512] = array
        for current, other, entries, target, at in ((1, last, 2, front, 512), (last, 1, last - 32, tail, 65536 - 512)):
            block = bytearray(512)
            struct.pack_into('<8sIIIIQQQQ16sQIII', block, 0, b'EFI PART', 0x10000, 92, 0, 0, current, other,
                             34, last - 33, uuid.UUID(disk).bytes_le, entries, 128, 128, zlib.crc32(array))
            struct.pack_into('<I', block, 16, zlib.crc32(block[:92]))
            target[at:at + 512] = block
        capture = V.Ranges(size, parts)
        capture.windows[0][2].extend(front)
        capture.windows[1][2].extend(tail)
        helper.read_at = capture.read_at
        self.assertEqual(helper.inspect_gpt(None, manifest, size), parts)
        capture.windows[1][2][-500] ^= 1
        with self.assertRaises(ValueError):
            helper.inspect_gpt(None, manifest, size)


if __name__ == '__main__':
    unittest.main()
