#!/usr/bin/env python3
"""Independent, opt-in inspection of a trusted integrated NVMe regular-file fixture.

Never opens a physical target, mounts, or repairs. Manifest integrity is supplied
by the caller; hashes are comparisons with trusted metadata, not signatures.
"""
import errno
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import resource
import stat
import struct
import subprocess
import sys
import tempfile
import uuid
import zlib

MIB = 1024**2
GIB = 1024**3
ROLES = ('boot', 'root-a', 'root-b', 'state', 'guests')
LINUX_GUID = '0fc63daf-8483-4772-8e79-3d69d8477de4'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def record(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def open_regular(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        require(stat.S_ISREG(os.fstat(fd).st_mode), 'input must be a regular non-symlink file')
        return fd
    except BaseException:
        os.close(fd)
        raise


def read_at(fd, length, offset):
    data = os.pread(fd, length, offset)
    require(len(data) == length, 'short image read')
    return data


def digest(fd, start, size):
    value = hashlib.sha256()
    offset = 0
    while offset < size:
        data = read_at(fd, min(4 * MIB, size - offset), start + offset)
        value.update(data)
        offset += len(data)
    return {'bytes': size, 'sha256': value.hexdigest()}


def identity(value):
    parsed = uuid.UUID(value)
    require(parsed.int != 0 and str(parsed) == value, 'nonzero canonical UUID required')
    return value


def inspect_gpt(fd, manifest, size):
    """Parse both tables without importing the production geometry or codec."""
    sector = manifest['logical_sector_bytes']
    physical = manifest['physical_sector_bytes']
    require(type(sector) is int and sector in (512, 4096), 'unsupported logical sector')
    require(type(physical) is int and physical >= sector and physical % sector == 0
            and MIB % physical == 0 and size % sector == 0, 'unsupported physical geometry')
    last = size // sector - 1
    array_sectors = 16384 // sector
    first_usable, last_usable = 2 + array_sectors, last - array_sectors - 1
    mbr = read_at(fd, sector, 0)
    expected_mbr = bytearray(sector)
    struct.pack_into('<B3sB3sII', expected_mbr, 446, 0, b'\0\2\0', 0xee,
                     b'\xff\xff\xff', 1, min(last, 0xffffffff))
    expected_mbr[510:512] = b'\x55\xaa'
    require(mbr == expected_mbr, 'protective MBR mismatch')
    disk = identity(manifest['disk_guid'])
    tables = []
    for current, alternate, entries_lba in ((1, last, 2), (last, 1, last - array_sectors)):
        block = read_at(fd, sector, current * sector)
        values = struct.unpack_from('<8sIIIIQQQQ16sQIII', block)
        signature, revision, length, crc, reserved, this, other, first, end, guid, array, count, width, array_crc = values
        require((signature, revision, length, reserved) == (b'EFI PART', 0x10000, 92, 0), 'GPT header format mismatch')
        require(block[92:] == bytes(sector - 92), 'GPT header trailing reserved bytes nonzero')
        header = bytearray(block[:92])
        header[16:20] = bytes(4)
        require(zlib.crc32(header) == crc, 'GPT header CRC mismatch')
        require((this, other, first, end, array, count, width) ==
                (current, alternate, first_usable, last_usable, entries_lba, 128, 128), 'GPT header geometry mismatch')
        require(str(uuid.UUID(bytes_le=guid)) == disk, 'GPT disk GUID mismatch')
        entries = read_at(fd, 16384, entries_lba * sector)
        require(zlib.crc32(entries) == array_crc, 'GPT array CRC mismatch')
        tables.append(entries)
    require(tables[0] == tables[1], 'GPT primary/backup arrays differ')
    end_bytes = (size - 33 * sector) // MIB * MIB
    offset = MIB
    partitions = []
    ids = manifest['partition_guids']
    require(isinstance(ids, list) and len(ids) == 5, 'expected five partition GUIDs')
    require(len(set([disk] + [identity(v) for v in ids])) == 6, 'duplicate GPT identities')
    for number, (role, budget) in enumerate(zip(ROLES, (512 * MIB, 4 * GIB, 4 * GIB, 8 * GIB, None)), 1):
        length = budget if budget is not None else end_bytes - offset
        require(length >= MIB, 'insufficient partition budget')
        start, end = offset // sector, (offset + length) // sector - 1
        require(first_usable <= start <= end <= last_usable, 'partition outside usable LBAs')
        kind, guid, actual_start, actual_end, attributes, name = struct.unpack_from('<16s16sQQQ72s', tables[0], (number - 1) * 128)
        require(str(uuid.UUID(bytes_le=kind)) == LINUX_GUID and str(uuid.UUID(bytes_le=guid)) == ids[number - 1], 'partition GUID mismatch')
        require((actual_start, actual_end, attributes) == (start, end, 0), 'partition geometry/attributes mismatch')
        require(name == role.encode('utf-16-le').ljust(72, b'\0'), 'partition UTF-16 role mismatch')
        partitions.append({'number': number, 'name': role, 'start_bytes': offset, 'size_bytes': length,
                           'start_lba': start, 'end_lba': end})
        offset += length
    require(not any(tables[0][5 * 128:]), 'unused GPT entries must be zero')
    expected = {'table': 'gpt', 'alignment_bytes': MIB, 'front_reserved_bytes': MIB,
                'tail_reserved_bytes': size - end_bytes, 'partitions': partitions}
    require(manifest['layout'] == expected, 'manifest layout differs from independent geometry')
    return partitions


def sparse_capture(fd, start, size, target):
    """Capture a partition from a pinned descriptor, clipping allocated extents."""
    require(stat.S_ISREG(os.fstat(fd).st_mode), 'capture source must be regular')
    require(0 <= start and 0 < size and start + size <= os.fstat(fd).st_size, 'capture bounds invalid')
    output = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.ftruncate(output, size)
        cursor, limit = start, start + size
        while cursor < limit:
            try:
                data_start = os.lseek(fd, cursor, os.SEEK_DATA)
            except OSError as error:
                if error.errno == errno.ENXIO:
                    break
                raise ValueError('SEEK_DATA/HOLE support required for sparse capture') from error
            if data_start >= limit:
                break
            data_end = min(os.lseek(fd, data_start, os.SEEK_HOLE), limit)
            require(cursor <= data_start < data_end <= limit, 'invalid sparse extent')
            while data_start < data_end:
                data = read_at(fd, min(MIB, data_end - data_start), data_start)
                relative = data_start - start
                while data:
                    written = os.pwrite(output, data, relative)
                    require(written > 0, 'short capture write')
                    relative += written
                    data = data[written:]
                data_start = min(data_start + MIB, data_end)
            cursor = data_end
    finally:
        os.close(output)


def debugfs(image, command, limit=MIB):
    # debugfs sometimes exits zero for missing files; callers must parse positive
    # stat evidence. Limit child output on disk as well as the eventual read.
    def bounded_output():
        resource.setrlimit(resource.RLIMIT_FSIZE, (limit + 1, limit + 1))
    with tempfile.TemporaryFile(dir=image.parent) as output, tempfile.TemporaryFile(dir=image.parent) as errors:
        result = subprocess.run(['debugfs', '-R', command, str(image)], stdout=output,
                                stderr=errors, timeout=120, preexec_fn=bounded_output)
        require(result.returncode == 0 and os.fstat(output.fileno()).st_size <= limit
                and os.fstat(errors.fileno()).st_size <= limit, 'debugfs failed or output exceeded bound')
        errors.seek(0)
        diagnostic = errors.read()
        require(not any(token in diagnostic for token in (b'not found', b'File not found', b'Filesystem not open', b'Command not found', b'EXT2 directory corrupted')), 'debugfs lookup failed')
        output.seek(0)
        return output.read(limit + 1)


def metadata(image, path, kind, mode=None):
    details = debugfs(image, 'stat ' + path).decode('utf-8')
    attributes = re.search(r'Type:\s+(\S+)\s+Mode:\s+0?([0-7]{3,4})\b', details)
    owner = re.search(r'User:\s+(\d+)\s+Group:\s+(\d+)\b', details)
    require(attributes is not None and owner is not None and attributes.group(1) == kind,
            'missing or unexpected inode type: ' + path)
    if mode is not None:
        require(int(attributes.group(2), 8) == mode and owner.groups() == ('0', '0'), 'inode ownership/mode mismatch: ' + path)


def superblock(fd, part, expected_uuid):
    block = read_at(fd, 1024, part['start_bytes'] + 1024)
    compat, incompat, ro = struct.unpack_from('<III', block, 92)
    role = part['name']
    require(block[56:58] == b'\x53\xef' and str(uuid.UUID(bytes=block[104:120])) == identity(expected_uuid), 'ext4 magic/UUID mismatch: ' + role)
    if role in ('root-a', 'root-b'):
        require(ro & 0x1000 and not compat & 4, 'root requires read-only feature and no journal')
    elif role in ('state', 'guests'):
        require(compat & 4 and not ro & 0x1000, 'mutable partition requires journal without read-only feature')
    else:
        require(not compat & 4 and not incompat & 0x80 and not ro & 0x400
                and not ro & 0x1000, 'boot ext4 compatibility features differ')


def snapshot(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def verify(directory):
    directory = Path(directory)
    manifest_fd = open_regular(directory / 'manifest.json')
    try:
        require(os.fstat(manifest_fd).st_size <= 4 * MIB, 'manifest exceeds bound')
        manifest_bytes = read_at(manifest_fd, os.fstat(manifest_fd).st_size, 0)
        manifest = json.loads(manifest_bytes)
    finally:
        os.close(manifest_fd)
    require(manifest['schema'] == 1, 'unsupported manifest schema')
    fd = open_regular(directory / 'nvme.img')
    try:
        initial = os.fstat(fd)
        size = initial.st_size
        require(size >= 17 * GIB and size == manifest['files']['nvme.img']['bytes'], 'image size differs or too small')
        whole = digest(fd, 0, size)
        require(whole == manifest['files']['nvme.img'], 'whole logical image hash/bytes mismatch')
        parts = inspect_gpt(fd, manifest, size)
        fsids = manifest['filesystem_uuids']
        require(set(fsids) == set(ROLES) and len(set(identity(v) for v in fsids.values())) == 5, 'filesystem identities must be distinct')
        require(fsids['state'] == manifest['state_uuid'], 'state UUID differs')
        payloads = {}
        with tempfile.TemporaryDirectory(prefix='nvme-inspect-', dir='/var/tmp') as temporary:
            private = Path(temporary)
            captures = {}
            for part in parts:
                role = part['name']
                superblock(fd, part, fsids[role])
                if role in ('root-a', 'root-b', 'state'):
                    payloads[role] = digest(fd, part['start_bytes'], part['size_bytes'])
                    require(payloads[role] == manifest['partition_payloads'][role], 'partition payload hash/bytes mismatch: ' + role)
                capture = private / (role + '.ext4')
                sparse_capture(fd, part['start_bytes'], part['size_bytes'], capture)
                captures[role] = capture
            metadata(captures['boot'], '/', 'directory', 0o755)
            metadata(captures['guests'], '/', 'directory', 0o700)
            expected_paths = {'boot/uImage', 'boot/uInitrd', 'boot/boot.cmd', 'boot/boot.scr',
                              'boot/dtb/allwinner/sun60i-a733-orangepi-4-pro.dtb'}
            require(set(manifest['boot_files']) == expected_paths, 'boot file set differs')
            for path, expected in manifest['boot_files'].items():
                require(not PurePosixPath(path).is_absolute() and '..' not in PurePosixPath(path).parts, 'unsafe boot path')
                require(type(expected['bytes']) is int and 0 < expected['bytes'] <= 128 * MIB, 'boot file size outside bound')
                metadata(captures['boot'], '/' + path, 'regular')
                data = debugfs(captures['boot'], 'cat /' + path, expected['bytes'])
                require(record(data) == expected, 'boot file hash/bytes mismatch: ' + path)
            selection = manifest['boot_selection']
            fields = ('SCHEMA', 'SLOT', 'ROOT_UUID', 'ROOT_MARKER_SHA256', 'KERNEL_RELEASE')
            require(set(selection) == set(fields) and selection['SCHEMA'] == '1'
                    and selection['SLOT'] in ('A', 'B') and selection['KERNEL_RELEASE'] == manifest['kernel_release'], 'selector schema/ABI mismatch')
            require(re.fullmatch('[0-9a-f]{64}', selection['ROOT_MARKER_SHA256']) is not None, 'selector hash invalid')
            state = captures['state']
            metadata(state, '/boot', 'directory', 0o700)
            for path in ('/boot/selected.conf', '/.a733-state'):
                metadata(state, path, 'regular', 0o600)
            serialized = ''.join(f'{key}={selection[key]}\n' for key in fields).encode()
            require(debugfs(state, 'cat /boot/selected.conf') == serialized, 'state selector differs')
            require(debugfs(state, 'cat /.a733-state') == f'a733-state-v1\n{fsids["state"]}\n'.encode(), 'state marker differs')
            for slot in ('A', 'B'):
                role = 'root-' + slot.lower()
                marker_path = '/usr/lib/orangepi-hypervisor/payload.json'
                metadata(captures[role], marker_path, 'regular')
                marker = debugfs(captures[role], 'cat ' + marker_path)
                value = json.loads(marker)
                require(value['schema'] == 1 and value['slot'] == slot and value['filesystem_uuid'] == fsids[role]
                        and value['kernel_release'] == manifest['kernel_release'], 'root marker slot/UUID/ABI mismatch')
                if slot == selection['SLOT']:
                    require(selection['ROOT_UUID'] == fsids[role] and hashlib.sha256(marker).hexdigest() == selection['ROOT_MARKER_SHA256'], 'selected root marker hash/UUID mismatch')
        require(snapshot(os.fstat(fd)) == snapshot(initial), 'image metadata changed during inspection')
        return {'classification': 'independent regular-file NVMe inspection; no hardware acceptance',
                'manifest': record(manifest_bytes), 'image': whole, 'logical_sector_bytes': manifest['logical_sector_bytes'],
                'gpt_primary_backup_and_geometry': True, 'filesystem_uuids_and_features': fsids,
                'partition_payloads': payloads, 'boot_file_hashes': manifest['boot_files'],
                'root_markers_and_state_selection': True, 'boot_and_guests_root_metadata': True,
                'image_metadata_unchanged': True,
                'limitations': ['Trusted manifest required; no signature authentication.',
                                'Whole logical image hashed once; final dev/inode/size/mtime/ctime compared on a quiescent builder.',
                                'No physical installation, SPI/NVMe boot, zero SD writes, or KVM guest demonstrated.']}
    finally:
        os.close(fd)


if __name__ == '__main__':
    if len(sys.argv) != 2:
        sys.exit(f'Usage: {sys.argv[0]} TRUSTED_IMAGE_DIRECTORY')
    try:
        print(json.dumps(verify(sys.argv[1]), indent=2))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        sys.exit(f'NVMe inspection: {error}')
