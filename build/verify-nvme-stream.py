#!/usr/bin/env python3
"""Read one authenticated production gzip NVMe artifact from binary stdin.

Invoke with Python -I -B. Stdlib only; metadata inputs are read-only. No image,
partition, scratch file, device, mount or subprocess is created/opened. The caller
must obtain the successful producer receipt SHA256 through a trusted channel.
"""
import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import sys
import types
import zlib

SIZE = 256060514304
MIB = 1024**2
STATE_UUID = 'fc8bbef6-9385-4ddb-90ba-e41eaccc035b'
REVISION = '6f46e080b9557dd6d720bb0b07d17b19044a7946'
PHASE = '/var/lib/a733-clean-replay-20261106/nvme-timesync-03'
HELPER = {'bytes': 15781, 'sha256': 'd9e9342136af125df2eb9dfe1d832183244f13cd28c7ac3e2a4d7f1ca976b8fb'}
SPI = {'bytes': 1315, 'sha256': 'c9f0db913fb49bbc5c54c5287049b8c56aaf17406fcdccf1b9fe0847ccce6c26'}
PRODUCERS = {
    'roots': '08cb041263fd4d24770cd80615745c46b9136940737eb6acbb07c1db39bcec8c',
    'state': 'fee4c46acb4db8838bbcc4b03f935701032ea093206a53ee39671cd840c6546e',
    'bootstrap': 'c5799bb06995313b6c90f99d4aedcc7b802095a0742cdd19a08d48ba3e3fec98',
}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def pin(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def valid_pin(value):
    require(isinstance(value, dict) and set(value) == {'bytes', 'sha256'} and
            type(value['bytes']) is int and value['bytes'] > 0 and
            isinstance(value['sha256'], str) and re.fullmatch('[0-9a-f]{64}', value['sha256']),
            'invalid artifact pin')
    return value


def signature(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_uid,
            info.st_gid, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class Held:
    """Hold physical ancestry by descriptor; ignore unrelated directory changes."""
    def __init__(self, path, expected=None):
        require('..' not in Path(path).parts, 'parent traversal in metadata path')
        self.path = Path(os.path.abspath(path))
        require(not any(self.path == root or root in self.path.parents
                        for root in map(Path, ('/dev', '/proc', '/sys'))),
                'device and pseudo-filesystem metadata paths are forbidden')
        self.fds = []
        try:
            fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
            self.fds.append(fd)
            self.links = []
            for component in self.path.parts[1:-1]:
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                self.links.append((fd, component, os.fstat(child), child))
                self.fds.append(child)
                fd = child
            self.parent = fd
            path_fd = os.open(self.path.name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            self.fds.append(path_fd)
            named = os.fstat(path_fd)
            require(stat.S_ISREG(named.st_mode) and named.st_nlink == 1 and
                    0 < named.st_size <= 4 * MIB, 'bounded single regular metadata file required')
            # O_PATH performs no device data open. Reopen only the checked inode,
            # even if its directory entry was replaced after the type check.
            self.fd = os.open('/proc/self/fd/' + str(path_fd), os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
            self.fds.append(self.fd)
            self.before = os.fstat(self.fd)
            require(stat.S_ISREG(self.before.st_mode) and self.before.st_nlink == 1 and
                    self.before.st_size <= 4 * MIB, 'bounded single regular metadata file required')
            require(signature(named) == signature(self.before), 'metadata leaf changed before open')
            self.data = os.pread(self.fd, self.before.st_size + 1, 0)
            self.pin = pin(self.data)
            require(len(self.data) == self.before.st_size, 'metadata short read')
            if expected is not None:
                require(self.pin == expected, 'metadata pin mismatch: ' + str(self.path))
            self.unchanged()
        except BaseException:
            self.close()
            raise

    def unchanged(self):
        for parent, name, before, child in self.links:
            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
            require(stat.S_ISDIR(current.st_mode) and
                    (current.st_dev, current.st_ino) == (before.st_dev, before.st_ino) ==
                    (os.fstat(child).st_dev, os.fstat(child).st_ino), 'metadata ancestry changed')
        current = os.stat(self.path.name, dir_fd=self.parent, follow_symlinks=False)
        require(signature(current) == signature(self.before) == signature(os.fstat(self.fd)),
                'metadata identity changed: ' + str(self.path))
        require(pin(os.pread(self.fd, self.before.st_size + 1, 0)) == self.pin,
                'metadata bytes changed: ' + str(self.path))
        require(signature(os.fstat(self.fd)) == signature(self.before), 'metadata changed during hash')

    def close(self):
        for fd in reversed(self.fds):
            os.close(fd)
        self.fds = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def json_bytes(data):
    # Bound parser object overhead as well as input bytes. Scan outside strings
    # before json.loads allocates a tree; the production records are well below
    # these limits. This also bounds pathological deeply nested metadata.
    quoted = escaped = False
    depth = tokens = 0
    for value in data:
        if quoted:
            if escaped:
                escaped = False
            elif value == 92:
                escaped = True
            elif value == 34:
                quoted = False
        elif value == 34:
            quoted = True
        elif value in (123, 91):
            depth += 1
            tokens += 1
        elif value in (125, 93):
            depth -= 1
        elif value in (44, 58):
            tokens += 1
        require(depth <= 64 and tokens <= 32768, 'JSON complexity exceeds bound')
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate JSON key')
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=pairs,
                      parse_constant=lambda _: require(False, 'nonfinite JSON constant'))


def authenticate(receipt, manifest):
    """Closed production contract; pins never come from caller-relaxed options."""
    require(receipt['schema'] == 1 and receipt['success'] is True and
            receipt['state'] == 'complete' and type(receipt['returncode']) is int and
            receipt['returncode'] == 0 and receipt['hardware_acceptance'] is False and
            receipt['physical_storage_written'] is False and
            receipt['host_inventory_unchanged'] is True and receipt['mapped_processes_final'] == [] and
            receipt['phase_mounts_final'] == [], 'producer is incomplete, failed or not file-only')
    require(receipt['sources']['revision'] == REVISION and receipt['phase'] == PHASE and
            receipt['output'] == 'work/project/out/nvme-image-timesync-03', 'wrong producer source/phase')
    policy = receipt['policy']
    producers = policy['producers']
    require(set(producers) == set(PRODUCERS), 'producer namespace differs')
    for role, expected in PRODUCERS.items():
        encoded = json.dumps(producers[role], sort_keys=True, separators=(',', ':')).encode()
        require(hashlib.sha256(encoded).hexdigest() == expected, 'component producer policy differs: ' + role)
    target = {'model': 'SPCC M.2 PCIe SSD', 'serial': '134807190FC700149277', 'bytes': SIZE,
              'logical_sector_bytes': 512, 'physical_sector_bytes': 512}
    expected_profile = producers['bootstrap']['boot_profile']
    require(policy['target_geometry'] == target and policy['state_uuid'] == STATE_UUID and
            policy['boot_profile'] == expected_profile, 'target policy differs')
    require(manifest['schema'] == 1 and manifest['logical_sector_bytes'] == 512 and
            manifest['physical_sector_bytes'] == 512 and manifest['state_uuid'] == STATE_UUID and
            manifest['boot_profile'] == expected_profile and
            manifest['deployment_intent']['for_installation'] is True and
            manifest['boot_selection'] == producers['state']['boot_selection'], 'manifest target/profile differs')
    command = {'name': 'spi-nvme-v1', 'source': {'path': 'configs/spi-nvme-host-boot.cmd', **SPI},
               'payload_context': {'devtype': 'nvme', 'devnum': 0, 'partition': 1, 'prefix': '/boot/'},
               'firmware_search_policy': 'Broader pinned distro scan; inspect GPT attributes and complete boot filesystem separately.',
               'physical_acceptance_pending': True}
    require(manifest['nvme_boot_command_profile'] == policy['nvme_boot_command_profile'] == command and
            policy['nvme_boot_command_source'] == command['source'] and
            manifest['boot_files']['boot/boot.cmd'] == SPI and manifest['recipes'] == policy['recipes'],
            'SPI command policy differs')
    require(manifest['inputs'] == {role + '_manifest': producers[name]['verification']['manifest']
            for role, name in (('roots', 'roots'), ('state', 'state'), ('sd', 'bootstrap'))},
            'component manifest pins differ')
    payloads = {'root-a': producers['roots']['readback']['slots']['A']['image'],
                'root-b': producers['roots']['readback']['slots']['B']['image'],
                'state': producers['state']['state_image']}
    require(manifest['partition_payloads'] == payloads, 'component payload pins differ')
    fsids = manifest['filesystem_uuids']
    require(fsids['root-a'] == producers['roots']['slot_uuids']['A'] and
            fsids['root-b'] == producers['roots']['slot_uuids']['B'] and
            fsids['state'] == STATE_UUID and manifest['kernel_release'] == '6.6.98-a733-diag',
            'root/state filesystem identity or kernel differs')
    require(set(manifest['files']) == {'nvme.img'}, 'unexpected manifest files')
    raw = valid_pin(manifest['files']['nvme.img'])
    require(raw['bytes'] == SIZE and receipt['verification']['image'] == raw and
            receipt['verification']['hardware_acceptance'] is False, 'raw image binding differs')
    compression = receipt['readback_expectations']['compression.json']['value']
    require(compression['schema'] == 1 and compression['raw'] == raw and compression['filename'] == '' and
            compression['mtime'] == 0 and compression['level'] == 9 and
            compression['input_identity_unchanged'] is True and
            compression['gzip_expansion_independent_verification'] == 'pending', 'compression proof differs')
    compressed = valid_pin(compression['gzip'])
    require(compressed['bytes'] <= SIZE, 'compressed artifact exceeds production bound')
    proof = receipt['readback_expectations']['nvme.json']['value']
    require(proof['manifest'] == receipt['verification']['manifest'] and proof['image'] == raw and
            proof['partition_payloads'] == payloads and proof['boot_file_hashes'] == manifest['boot_files'] and
            proof['gpt_primary_backup_and_geometry'] is True and
            proof['root_markers_and_state_selection'] is True and proof['image_metadata_unchanged'] is True and
            proof['spi_script_wrapper'] == {'header_type_crc_body': True, 'helper': policy['spi_wrapper_helper']} and
            set(proof['complete_new_filesystem_readback']) == {'boot', 'guests'}, 'producer inspection binding differs')
    return compressed, raw, proof


class Ranges:
    """Bounded byte windows and independent clipping of full partition hashes."""
    def __init__(self, size, parts):
        require(type(size) is int and size > 0, 'invalid stream size')
        self.size = size
        windows = [(0, min(65536, size)), (max(0, size - 65536), min(65536, size))]
        self.hashes = {}
        for part in parts:
            start, length = part['start_bytes'], part['size_bytes']
            require(type(start) is int and type(length) is int and
                    0 <= start < start + length <= size and length >= 2048, 'partition window outside image')
            windows.append((start + 1024, 1024))
            if part['name'] in ('root-a', 'root-b', 'state'):
                require(part['name'] not in self.hashes, 'duplicate partition hash role')
                self.hashes[part['name']] = (start, length, hashlib.sha256(), [0])
        require(len(windows) <= 7, 'too many capture windows')
        self.windows = [(start, length, bytearray()) for start, length in windows]
        self.offset = 0

    def add(self, data):
        end = self.offset + len(data)
        require(end <= self.size, 'decoded stream exceeds expected bytes')
        for start, length, result in self.windows:
            a, b = max(start, self.offset), min(start + length, end)
            if a < b:
                result.extend(data[a - self.offset:b - self.offset])
        for start, length, digest, count in self.hashes.values():
            a, b = max(start, self.offset), min(start + length, end)
            if a < b:
                digest.update(memoryview(data)[a - self.offset:b - self.offset])
                count[0] += b - a
        self.offset = end

    def read_at(self, _fd, length, offset):
        require(type(length) is int and type(offset) is int and length >= 0 and offset >= 0,
                'invalid memory range')
        for start, size, data in self.windows:
            if start <= offset and offset + length <= start + size:
                require(len(data) == size, 'captured window incomplete')
                return bytes(data[offset - start:offset - start + length])
        raise ValueError('uncaptured memory range requested')

    def payloads(self):
        require(self.offset == self.size, 'decoded stream is short')
        result = {}
        for role, (_, size, digest, count) in self.hashes.items():
            require(count[0] == size, 'partition hash is short')
            result[role] = {'bytes': size, 'sha256': digest.hexdigest()}
        return result


def decode(stream, compressed, raw, capture, fence=lambda: None):
    """Single gzip member; at most 64 KiB wire and 1 MiB expanded per step."""
    valid_pin(compressed); valid_pin(raw)
    decoder = zlib.decompressobj(31)
    wire_hash, raw_hash = hashlib.sha256(), hashlib.sha256()
    wire_size = raw_size = 0
    header = bytearray()
    fence()
    while True:
        wire = stream.read(65536)
        if not wire:
            break
        wire_size += len(wire)
        require(wire_size <= compressed['bytes'], 'wire exceeds expected bytes')
        wire_hash.update(wire)
        header.extend(wire[:max(0, 10 - len(header))])
        if len(header) == 10:
            require(bytes(header) == b'\x1f\x8b\x08\x00\x00\x00\x00\x00\x02\xff',
                    'gzip header must have no filename, mtime zero, level 9 and OS 255')
        require(not decoder.eof, 'trailing gzip bytes or member')
        pending = wire
        while pending:
            data = decoder.decompress(pending, MIB)
            pending = decoder.unconsumed_tail
            raw_size += len(data)
            require(raw_size <= raw['bytes'], 'decoded stream exceeds expected bytes')
            raw_hash.update(data)
            capture.add(data)
            require(not decoder.unused_data, 'trailing gzip bytes or member')
        # The fence costs only bounded metadata hashes once per compressed MiB.
        if wire_size // MIB != (wire_size - len(wire)) // MIB:
            fence()
    require(len(header) == 10 and decoder.eof, 'gzip is truncated')
    wire_pin = {'bytes': wire_size, 'sha256': wire_hash.hexdigest()}
    raw_pin = {'bytes': raw_size, 'sha256': raw_hash.hexdigest()}
    require(wire_pin == compressed, 'compressed wire hash/bytes mismatch')
    require(raw_pin == raw, 'decoded logical image hash/bytes mismatch')
    fence()
    return wire_pin, raw_pin


def inspect(capture, manifest, helper):
    helper.read_at = capture.read_at
    parts = helper.inspect_gpt(None, manifest, SIZE)
    fsids = manifest['filesystem_uuids']
    require(set(fsids) == set(helper.ROLES) and len(set(helper.identity(v) for v in fsids.values())) == 5 and
            fsids['state'] == STATE_UUID, 'filesystem identity policy differs')
    for part in parts:
        helper.superblock(None, part, fsids[part['name']])
        block = capture.read_at(None, 1024, part['start_bytes'] + 1024)
        log_block = struct.unpack_from('<I', block, 24)[0]
        require(log_block <= 6, 'invalid ext4 block size')
        blocks = struct.unpack_from('<I', block, 4)[0]
        if struct.unpack_from('<I', block, 96)[0] & 0x80:
            blocks |= struct.unpack_from('<I', block, 336)[0] << 32
        require(blocks * (1024 << log_block) == part['size_bytes'], 'filesystem block geometry differs')
    payloads = capture.payloads()
    require(payloads == manifest['partition_payloads'], 'stream partition payload hash differs')
    return parts, payloads


def verify(stream, receipt_path, receipt_sha256, manifest_path):
    require(isinstance(receipt_sha256, str) and re.fullmatch('[0-9a-f]{64}', receipt_sha256), 'invalid receipt SHA256')
    helper_path = Path(__file__).absolute().parent.parent / 'tests/verify_nvme_image.py'
    with contextlib.ExitStack() as stack:
        receipt_file = stack.enter_context(Held(receipt_path))
        require(receipt_file.pin['sha256'] == receipt_sha256, 'receipt SHA256 differs from caller pin')
        receipt = json_bytes(receipt_file.data)
        manifest_file = stack.enter_context(Held(manifest_path, valid_pin(receipt['verification']['manifest'])))
        require(len(receipt_file.data) + len(manifest_file.data) <= 4 * MIB,
                'combined metadata exceeds bounded-memory budget')
        manifest = json_bytes(manifest_file.data)
        compressed, raw, proof = authenticate(receipt, manifest)
        helper_file = stack.enter_context(Held(helper_path, HELPER))
        helper = types.ModuleType('authenticated_nvme_inspection')
        helper.__file__ = str(helper_path)
        exec(compile(helper_file.data, str(helper_path), 'exec'), helper.__dict__)
        held = (receipt_file, manifest_file, helper_file)
        def fence():
            for item in held:
                item.unchanged()
        capture = Ranges(SIZE, manifest['layout']['partitions'])
        wire, image = decode(stream, compressed, raw, capture, fence)
        parts, payloads = inspect(capture, manifest, helper)
        fence()
        return {'schema': 1, 'success': True, 'state': 'complete',
                'classification': 'Independent file-artifact gzip wire and decoded NVMe stream verification',
                'producer_receipt': receipt_file.pin, 'manifest': manifest_file.pin,
                'authenticated_inspection_helper': helper_file.pin, 'compressed_wire': wire, 'decoded_image': image,
                'gzip_expansion_independent_verification': True, 'gzip_crc_and_single_member': True,
                'gpt_primary_backup_and_geometry': True, 'partitions': parts, 'partition_payloads': payloads,
                'filesystem_superblocks_uuid_features_and_geometry': True, 'metadata_identity_unchanged': True,
                'producer_attributed_only': {'boot_file_hashes': proof['boot_file_hashes'],
                    'complete_boot_and_guests_filesystem_readback': proof['complete_new_filesystem_readback'],
                    'root_markers_and_state_selection': proof['root_markers_and_state_selection']},
                'hardware_acceptance': False, 'reproducibility_established': False,
                'limitations': ['Filesystem namespaces and boot files are producer-attributed; this reader independently verifies whole wire/raw hashes, GPT, superblocks and root/state partition bytes only.',
                    'No physical device access, installation, boot, SD-write validation or KVM guest execution.']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--receipt', required=True, type=Path)
    parser.add_argument('--receipt-sha256', required=True)
    parser.add_argument('--manifest', required=True, type=Path)
    args = parser.parse_args(argv)
    require(sys.flags.isolated and sys.flags.dont_write_bytecode, 'invoke Python with -I -B')
    print(json.dumps(verify(sys.stdin.buffer, args.receipt, args.receipt_sha256, args.manifest), sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, zlib.error) as error:
        sys.exit('NVMe stream verification failed: ' + str(error))
