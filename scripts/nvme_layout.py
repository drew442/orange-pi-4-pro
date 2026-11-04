"""Pure host NVMe geometry and GPT encoding; never opens files or devices.

On-disk fields and validation follow Linux v6.6 block/partitions/efi.h and
efi.c (https://github.com/torvalds/linux/tree/v6.6/block/partitions), the
kernel implementation of the UEFI GPT format. Geometry intentionally retains
the planner's conservative 33-logical-sector tail even on 4 KiB media.
"""

import struct
import uuid
import zlib

MIB = 1024**2
GIB = 1024**3
LINUX_FILESYSTEM_GUID = uuid.UUID('0fc63daf-8483-4772-8e79-3d69d8477de4')
ENTRY_COUNT = 128
ENTRY_BYTES = 128


def layout(size_bytes, logical_sector_bytes, physical_sector_bytes):
    """Return the plan-only geometry, raising ValueError for invalid inputs."""
    size, logical, physical = size_bytes, logical_sector_bytes, physical_sector_bytes
    if not all(type(v) is int and v > 0 for v in (size, logical, physical)):
        raise ValueError('Invalid byte size or sector geometry')
    if not (logical in (512, 4096) and physical >= logical
            and physical % logical == 0 and MIB % physical == 0
            and size % logical == 0):
        raise ValueError('Unsupported sector geometry')
    end = (size - 33 * logical) // MIB * MIB
    offset = MIB
    partitions = []
    for number, (name, budget) in enumerate([
            ('boot', 512 * MIB), ('root-a', 4 * GIB), ('root-b', 4 * GIB),
            ('state', 8 * GIB), ('guests', None)], 1):
        length = budget if budget is not None else end - offset
        if length < MIB:
            raise ValueError('Target is too small for fixed partitions and guest space')
        partitions.append({'number': number, 'name': name, 'start_bytes': offset,
                           'size_bytes': length, 'start_lba': offset // logical,
                           'end_lba': (offset + length) // logical - 1})
        offset += length
    return {'table': 'gpt', 'alignment_bytes': MIB, 'front_reserved_bytes': MIB,
            'tail_reserved_bytes': size - end, 'partitions': partitions}


def _guid(value):
    try:
        result = value if isinstance(value, uuid.UUID) else uuid.UUID(value)
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError('Invalid GUID') from error
    if result.int == 0:
        raise ValueError('Zero GUID is not permitted')
    return result


def encode_gpt(size_bytes, logical_sector_bytes, physical_sector_bytes,
               disk_guid=None, partition_guids=None):
    """Return GPT byte extents and identity metadata, with no I/O.

    ``extents`` is an ordered list of {name, offset_bytes, data}; every extent
    covers whole logical sectors. ``partition_guids`` is an optional sequence
    of exactly five UUID strings/objects in boot/root-a/root-b/state/guests order.
    All six identities must be nonzero and distinct. Defaults are fresh UUID4s.
    """
    geometry = layout(size_bytes, logical_sector_bytes, physical_sector_bytes)
    logical = logical_sector_bytes
    last_lba = size_bytes // logical - 1
    if last_lba > 0xffffffffffffffff:
        raise ValueError('Capacity exceeds GPT LBA range')
    disk_id = _guid(uuid.uuid4() if disk_guid is None else disk_guid)
    if partition_guids is None:
        partition_guids = [uuid.uuid4() for _ in geometry['partitions']]
    if not isinstance(partition_guids, (list, tuple)) or len(partition_guids) != 5:
        raise ValueError('Expected five partition GUIDs')
    partition_ids = [_guid(value) for value in partition_guids]
    if len(set([disk_id] + partition_ids)) != 6:
        raise ValueError('Duplicate GUID is not permitted')

    array_sectors = (ENTRY_COUNT * ENTRY_BYTES + logical - 1) // logical
    backup_entries_lba = last_lba - array_sectors
    first_usable = 2 + array_sectors
    last_usable = backup_entries_lba - 1
    entries = bytearray(ENTRY_COUNT * ENTRY_BYTES)
    for part, identity in zip(geometry['partitions'], partition_ids):
        if not first_usable <= part['start_lba'] <= part['end_lba'] <= last_usable:
            raise ValueError('Partition outside GPT usable bounds')
        struct.pack_into('<16s16sQQQ72s', entries, (part['number'] - 1) * ENTRY_BYTES,
                         LINUX_FILESYSTEM_GUID.bytes_le, identity.bytes_le,
                         part['start_lba'], part['end_lba'], 0,
                         part['name'].encode('utf-16-le'))
    entries_crc = zlib.crc32(entries)
    array = bytes(entries).ljust(array_sectors * logical, b'\0')

    def header(current_lba, alternate_lba, entries_lba):
        data = bytearray(logical)
        struct.pack_into('<8sIIIIQQQQ16sQIII', data, 0, b'EFI PART', 0x00010000,
                         92, 0, 0, current_lba, alternate_lba, first_usable,
                         last_usable, disk_id.bytes_le, entries_lba,
                         ENTRY_COUNT, ENTRY_BYTES, entries_crc)
        struct.pack_into('<I', data, 16, zlib.crc32(data[:92]))
        return bytes(data)

    mbr = bytearray(logical)
    struct.pack_into('<B3sB3sII', mbr, 446, 0, b'\0\2\0', 0xee,
                     b'\xff\xff\xff', 1, min(last_lba, 0xffffffff))
    mbr[510:512] = b'\x55\xaa'
    extents = [
        {'name': 'protective-mbr', 'offset_bytes': 0, 'data': bytes(mbr)},
        {'name': 'primary-header', 'offset_bytes': logical,
         'data': header(1, last_lba, 2)},
        {'name': 'primary-entries', 'offset_bytes': 2 * logical, 'data': array},
        {'name': 'backup-entries', 'offset_bytes': backup_entries_lba * logical,
         'data': array},
        {'name': 'backup-header', 'offset_bytes': last_lba * logical,
         'data': header(last_lba, 1, backup_entries_lba)},
    ]
    previous_end = 0
    for extent in extents:
        start = extent['offset_bytes']
        end = start + len(extent['data'])
        if not previous_end <= start < end <= size_bytes:
            raise ValueError('GPT extents overlap or exceed capacity')
        previous_end = end
    return {'layout': geometry, 'disk_guid': str(disk_id),
            'partition_guids': [str(identity) for identity in partition_ids],
            'extents': extents, 'first_usable_lba': first_usable,
            'last_usable_lba': last_usable}
