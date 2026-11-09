#!/usr/bin/env python3
"""Package the fixed protected BOOT in an authenticated full NVMe gzip stream."""
import argparse
from contextlib import ExitStack
import copy
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import struct
import sys
import uuid
import zlib

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location('protected_image_boot', REPO / 'build/protected-state-nvme-boot.py')
BOOT = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(BOOT)
RUNTIME, HELP = BOOT.RUNTIME, BOOT.HELP
require = BOOT.require
WIRE, RAW, MANIFEST, OLD_BOOT = BOOT.WIRE, BOOT.RAW, BOOT.MANIFEST, BOOT.BASE_BOOT
NEW_BOOT = {'bytes': 536870912, 'sha256': 'cc2ad988fdc57579178766acaf09c71d688aaadec94b722593192cb24426c313'}
START, LENGTH = BOOT.BOOT_OFFSET, BOOT.BOOT_BYTES
CHUNK = 1024**2
ADMISSION = 23760999827
OUTPUT_BOUND = 2 * 1024**3
SCRATCH_BUDGET = OUTPUT_BOUND + 16 * CHUNK
RECIPES = ('build/protected-state-nvme-image.py',) + BOOT.RECIPES
# Inherit only unchanged payload identity/policy, never construction measurements.
PAYLOAD_METADATA = ('schema', 'state_uuid', 'kernel_release', 'deployment_intent',
                    'logical_sector_bytes', 'physical_sector_bytes', 'disk_guid',
                    'partition_guids', 'filesystem_uuids', 'layout', 'gpt_extents',
                    'boot_selection', 'partition_payloads', 'board', 'boot_profile',
                    'runtime_profile', 'nvme_boot_command_profile', 'boot_command_policy',
                    'boot_files')


def output_path(path):
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts, 'absolute output path required')
    require(path.parent == REPO / 'out' and re.fullmatch(
        r'protected-state-nvme-image-[A-Za-z0-9][A-Za-z0-9_-]{0,63}', path.name),
        'output must be fresh out/protected-state-nvme-image-TAG')
    RUNTIME.safe_regular_path(path)
    require(not path.exists() and not path.is_symlink(), 'output already exists; failed tags cannot be reused')
    return path


def admission(directory):
    sample = BOOT.SD.resources(directory)
    require(sample['free_bytes'] >= max(ADMISSION, BOOT.SD.DISK_FLOOR + SCRATCH_BUDGET),
            'initial admission requires 23760999827 free bytes and output scratch budget')
    return sample


class BoundedWriter:
    def __init__(self, stream, maximum):
        self.stream, self.maximum, self.total = stream, maximum, 0

    def write(self, data):
        require(self.total + len(data) <= self.maximum, 'compressed output exceeds scratch bound')
        count = self.stream.write(data)
        require(count == len(data), 'short compressed output write')
        self.total += count
        return count

    def flush(self):
        self.stream.flush()


def decoded_blocks(fd, size, monitor):
    """Decode exactly one member, checking CRC/EOF with a hard expansion ceiling."""
    os.lseek(fd, 0, os.SEEK_SET)
    decoder, total = zlib.decompressobj(31), 0
    while encoded := os.read(fd, CHUNK):
        require(not decoder.eof, 'trailing gzip member/data')
        while encoded:
            data = decoder.decompress(encoded, min(CHUNK, size - total + 1))
            encoded = decoder.unconsumed_tail
            require(not decoder.unused_data, 'trailing gzip member/data')
            total += len(data)
            require(total <= size, 'expanded stream exceeds raw bound')
            monitor.check('decode', total)
            if data:
                yield data
    require(decoder.eof, 'truncated gzip/CRC trailer')
    require(total == size, 'raw size mismatch')


class Ranges:
    """Hash named extents and optionally retain only small GPT extents."""
    def __init__(self, extents):
        self.extents = extents
        self.hashes = {name: hashlib.sha256() for name in extents}
        self.counts = dict.fromkeys(extents, 0)
        self.gpt = {name: bytearray() for name in extents if name.startswith('gpt:')}

    def update(self, data, offset):
        for name, (start, size) in self.extents.items():
            left, right = max(offset, start), min(offset + len(data), start + size)
            if left < right:
                part = data[left - offset:right - offset]
                self.hashes[name].update(part)
                self.counts[name] += len(part)
                if name in self.gpt:
                    self.gpt[name].extend(part)

    def records(self):
        require(all(self.counts[n] == size for n, (_, size) in self.extents.items()), 'short range capture')
        return {n: {'bytes': self.counts[n], 'sha256': h.hexdigest()} for n, h in self.hashes.items()}


def extents(base, size=RAW['bytes'], start=START, length=LENGTH):
    result = {'prefix': (0, start), 'suffix': (start + length, size - start - length),
              'boot': (start, length)}
    for part in base['layout']['partitions'][1:]:
        result['partition:' + part['name']] = (part['start_bytes'], part['size_bytes'])
    for item in base['gpt_extents']:
        require(0 < item['bytes'] <= 16384 and 0 <= item['offset_bytes'] <= size - item['bytes'], 'GPT capture bounds')
        result['gpt:' + item['name']] = (item['offset_bytes'], item['bytes'])
    return result


def validate_gpt(captured, base, size):
    """Independently validate protective MBR, both CRCs/tables and manifest geometry."""
    sector = base['logical_sector_bytes']
    require(sector == base['physical_sector_bytes'] == 512 and size % sector == 0, 'unsupported sector geometry')
    last, array_sectors = size // sector - 1, 32
    mbr = bytearray(sector)
    struct.pack_into('<B3sB3sII', mbr, 446, 0, b'\0\2\0', 0xee, b'\xff\xff\xff', 1, min(last, 0xffffffff))
    mbr[510:512] = b'\x55\xaa'
    require(captured['gpt:protective-mbr'] == mbr, 'protective MBR mismatch')
    arrays = []
    for prefix, current, alternate, lba in (('primary', 1, last, 2), ('backup', last, 1, last - array_sectors)):
        block = captured['gpt:' + prefix + '-header']
        require(len(block) == sector, 'GPT header size')
        values = struct.unpack_from('<8sIIIIQQQQ16sQIII', block)
        signature, revision, length, crc, reserved, this, other, first, end, guid, array, count, width, array_crc = values
        require((signature, revision, length, reserved, this, other, first, end, array, count, width) ==
                (b'EFI PART', 0x10000, 92, 0, current, alternate, 34, last - 33, lba, 128, 128), 'GPT header geometry/format')
        require(not any(block[92:]) and str(uuid.UUID(bytes_le=bytes(guid))) == base['disk_guid'], 'GPT disk identity/reserved bytes')
        header = bytearray(block[:92])
        header[16:20] = bytes(4)
        require(zlib.crc32(header) == crc, 'GPT header CRC mismatch')
        entries = captured['gpt:' + prefix + '-entries']
        require(len(entries) == 16384 and zlib.crc32(entries) == array_crc, 'GPT array CRC mismatch')
        arrays.append(entries)
    require(arrays[0] == arrays[1], 'GPT tables differ')
    expected = bytearray(16384)
    # Enforce the established five-partition geometry independently of supplied LBAs.
    offset, end_bytes = CHUNK, (size - 33 * sector) // CHUNK * CHUNK
    require(len(base['layout']['partitions']) == len(base['partition_guids']) == 5, 'GPT partition count')
    identities = [base['disk_guid']] + base['partition_guids']
    require(len(set(identities)) == 6 and all(uuid.UUID(v).int for v in identities), 'GPT duplicate/zero identity')
    for index, (role, budget) in enumerate(zip(('boot', 'root-a', 'root-b', 'state', 'guests'),
                                              (LENGTH, 4 * 1024**3, 4 * 1024**3, 8 * 1024**3, None))):
        length = budget if budget else end_bytes - offset
        part = {'number': index + 1, 'name': role, 'start_bytes': offset, 'size_bytes': length,
                'start_lba': offset // sector, 'end_lba': (offset + length) // sector - 1}
        require(length >= CHUNK and base['layout']['partitions'][index] == part, 'GPT layout mismatch')
        struct.pack_into('<16s16sQQQ72s', expected, index * 128,
                         uuid.UUID('0fc63daf-8483-4772-8e79-3d69d8477de4').bytes_le,
                         uuid.UUID(base['partition_guids'][index]).bytes_le,
                         part['start_lba'], part['end_lba'], 0, role.encode('utf-16-le').ljust(72, b'\0'))
        offset += length
    require(arrays[0] == expected, 'GPT partition entries mismatch')
    require(base['layout'] == {'table': 'gpt', 'alignment_bytes': CHUNK, 'front_reserved_bytes': CHUNK,
                              'tail_reserved_bytes': size - end_bytes, 'partitions': base['layout']['partitions']}, 'GPT layout metadata mismatch')


def unchanged(before, after):
    require({k: v for k, v in before.items() if k != 'boot'} ==
            {k: v for k, v in after.items() if k != 'boot'}, 'bytes outside BOOT changed')


def derive(source_fd, boot_fd, target, expected, old_boot, new_boot, ranges, monitor,
           start=START, length=LENGTH, output_bound=OUTPUT_BOUND):
    original, candidate = hashlib.sha256(), hashlib.sha256()
    before, after = Ranges(ranges), Ranges(ranges)
    total = changed = 0
    with target.open('xb') as wire:
        bounded = BoundedWriter(wire, output_bound)
        with gzip.GzipFile(filename='', fileobj=bounded, mode='wb', compresslevel=9, mtime=0) as encoded:
            for data in decoded_blocks(source_fd, expected['bytes'], monitor):
                original.update(data)
                before.update(data, total)
                left, right = max(total, start), min(total + len(data), start + length)
                patched = data
                if left < right:
                    replacement = os.pread(boot_fd, right - left, left - start)
                    require(len(replacement) == right - left, 'short replacement BOOT')
                    old = data[left - total:right - total]
                    changed += sum(a != b for a, b in zip(old, replacement))
                    patched = data[:left - total] + replacement + data[right - total:]
                candidate.update(patched)
                after.update(patched, total)
                encoded.write(patched)
                total += len(data)
    require({'bytes': total, 'sha256': original.hexdigest()} == expected, 'original raw hash/size mismatch')
    before, after = before.records(), after.records()
    require(before['boot'] == old_boot and after['boot'] == new_boot, 'BOOT extent pin mismatch')
    require(changed > 0, 'replacement changed no BOOT bytes')
    unchanged(before, after)
    return {'bytes': total, 'sha256': candidate.hexdigest()}, before, after, changed


def verify_output(path, expected, ranges, monitor):
    """Separate gzip reader and complete hash pass, including CRC/trailer and EOF."""
    digest, capture, total = hashlib.sha256(), Ranges(ranges), 0
    with path.open('rb') as wire, gzip.GzipFile(fileobj=wire, mode='rb') as decoded:
        while data := decoded.read(min(CHUNK, expected['bytes'] - total + 1)):
            total += len(data)
            require(total <= expected['bytes'], 'output expansion exceeds bound')
            digest.update(data)
            capture.update(data, total - len(data))
            monitor.check('independent output decode', total)
    require({'bytes': total, 'sha256': digest.hexdigest()} == expected, 'output raw hash/size mismatch')
    return capture


def build(base_gzip, base_manifest, new_boot, output):
    output = output_path(output)
    require(output.parent.is_dir(), 'repository out directory required')
    admitted = admission(output.parent)
    monitor = HELP.Monitor(output.parent)
    monitor.check('admission', force=True)
    with ExitStack() as stack:
        held = []
        def hold(path, maximum, pin=None):
            item = stack.enter_context(RUNTIME.HeldInput(path, maximum, pin))
            require(all(item.initial_identity[:2] != prior.initial_identity[:2] for prior in held), 'aliased inputs')
            held.append(item)
            return item
        source = hold(base_gzip, WIRE['bytes'], WIRE)
        authority = hold(base_manifest, MANIFEST['bytes'], MANIFEST)
        replacement = hold(new_boot, NEW_BOOT['bytes'], NEW_BOOT)
        recipes = {name: hold(REPO / name, CHUNK).record for name in RECIPES}
        # All three fixed inputs are authenticated before manifest/GPT interpretation.
        base = json.loads(authority.read())
        require(base['files']['nvme.img.gz'] == WIRE and base['files']['nvme.img'] == RAW, 'base authority mismatch')
        require(base['state_uuid'] == RUNTIME.STATE_UUID, 'STATE UUID mismatch')
        require(base['layout']['partitions'][0]['start_bytes'] == START and
                base['layout']['partitions'][0]['size_bytes'] == LENGTH, 'BOOT bounds mismatch')
        ranges = extents(base)
        output.mkdir(mode=0o700)
        partial = output / 'nvme.img.gz.partial'
        new_raw, before, after, changed = derive(source.fd, replacement.fd, partial, RAW, OLD_BOOT, NEW_BOOT, ranges, monitor)
        proof = verify_output(partial, new_raw, ranges, monitor)
        require(proof.records() == after, 'independent output ranges differ')
        validate_gpt(proof.gpt, base, RAW['bytes'])
        for item in base['gpt_extents']:
            require(before['gpt:' + item['name']] == {'bytes': item['bytes'], 'sha256': item['sha256']}, 'GPT authority extent mismatch')
        for name, pin in base['partition_payloads'].items():
            require(before['partition:' + name] == pin, 'unchanged partition authority mismatch')
        # Unchanged prefix/suffix and matching GPT extent pins prove both source tables too.
        unchanged(before, after)
        result = {name: copy.deepcopy(base[name]) for name in PAYLOAD_METADATA if name in base}
        result.update(classification='protected STATE full NVMe distribution; UNBOOTED',
                      hardware_acceptance=False, physical_storage_written=False,
                      files={'nvme.img': new_raw, 'nvme.img.gz': BOOT.VGIC.file_record(partial)},
                      sources=recipes, base_gzip=WIRE, base_manifest=MANIFEST, protected_boot=NEW_BOOT,
                      source_manifest=copy.deepcopy(base),
                      deployment='unpersonalized distribution only; never overwrite the personalized running board',
                      derivation={'only_changed_extent': {'offset_bytes': START, 'bytes': LENGTH},
                                  'changed_bytes': changed, 'before_ranges': before, 'after_ranges': after,
                                  'base_crc_eof_raw_hash_verified': True, 'both_gpt_tables_verified': True,
                                  'output_gzip_full_expansion_verified': True, 'all_inputs_held_reauthenticated': True,
                                  'gzip': {'level': 9, 'mtime': 0, 'filename': ''}},
                      resources={'admission': admitted, 'admission_guard': ADMISSION,
                                 'output_bound': OUTPUT_BOUND, 'scratch_budget': SCRATCH_BUDGET,
                                 'disk_floor': HELP.DISK_FLOOR, 'memory_floor': HELP.MEMORY_FLOOR,
                                 'minimum_free_bytes': monitor.minimum_free,
                                 'minimum_available_memory_bytes': monitor.minimum_memory},
                      tool_versions={'python': sys.version, 'zlib': zlib.ZLIB_RUNTIME_VERSION})
        # Replace obsolete inherited BOOT and derivation metadata with the fixed new payload.
        result['boot_files']['boot/uInitrd'] = BOOT.NEW_INITRD
        for item in held:
            monitor.check('reauthenticate', force=True)
            item.verify()
        monitor.check('publication', force=True)
        result['resources']['minimum_free_bytes'] = monitor.minimum_free
        result['resources']['minimum_available_memory_bytes'] = monitor.minimum_memory
        partial.rename(output / 'nvme.img.gz')
        with (output / 'manifest.json').open('x') as stream:
            json.dump(result, stream, indent=2)
            stream.write('\n')
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('base-gzip', 'base-manifest', 'new-boot', 'output'):
        parser.add_argument('--' + name, required=True, type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(build(args.base_gzip, args.base_manifest, args.new_boot, args.output), indent=2))
    except (ValueError, OSError, KeyError, TypeError, UnicodeError, EOFError, zlib.error, struct.error) as error:
        parser.exit(1, f'protected STATE full NVMe packaging failed: {error}\n')


if __name__ == '__main__':
    main()
