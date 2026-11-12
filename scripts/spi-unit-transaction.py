"""API-only, callback-driven A733 SPI transaction preparation; no device access.

A trusted future adapter owns identity, quiescence, recovery, authorization and
receipt durability. Model execution never establishes hardware acceptance.
"""
import hashlib
import json
import struct

SIZE = 16777216
ERASE = 65536
CHUNK = 4096
ORIGINAL_SHA256 = 'dffab0dd410657cb30c7b2fd7f2586a4792e8472e58882b3532581f8111a646d'
CANDIDATE_SHA256 = '83edcd7f448e8353163666016a6bae3e9b70d041a5e4d8454f48931839205e90'
RANGES = ((0, 237568), (262144, 1654784))
METADATA = (260096, 262144)
PRESERVED = ((237568, 260096), (1654784, SIZE))
LAYOUT_ID = 'a733-spi-16m-v1'
PROFILE_ID = 'vendor-normal-a733-v1'
# Immutable reviewed registrations; a caller-provided hash cannot add a backup.
RESTORE_SHA256 = {
    'original': ORIGINAL_SHA256,
    'postboot-20261111': '812038f8291faffc0a404dc06afa2d1f7b52f2404ead566b79b44eef4bca1be8',
    'postwarm-20261111': 'e829cf7b08fa9753866df7de727b2d1b92bb5c525f3bbf4cd94315d528dc275a',
}
HEADER_LAST = list(range(4, 26)) + [1, 2, 3, 0]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode('ascii')


def classify_metadata(data):
    """Validate the source-backed 2 KiB same-board vendor parameter profile.

    U-Boot b791be842935b27268ae3d00e943a9075495f30a:
    include/boot_param.h; board/sunxi/board_common.c:846-880;
    drivers/sunxi_flash/spinor/spinor.c:470-557; drivers/mtd/spi/sf_internal.h.
    DDR bytes are opaque; checksum detects corruption, not authenticity.
    """
    require(type(data) is bytes and len(data) == 2048,
            'metadata must be immutable complete 2048 bytes')
    result = dict(state='erased', profile=None, bytes=2048, sha256=sha(data))
    if data == b'\xff' * 2048:
        return result
    require(data[:8] == b'bootpara', 'metadata header magic mismatch')
    require(data[512:520] == b'NORPARAM', 'metadata NOR magic mismatch')
    words = struct.unpack('<512I', data)
    checksum = (sum(words) - words[3] + 0x5f0a6c39) & 0xffffffff
    require(words[3] == checksum, 'metadata checksum mismatch')
    expected = bytearray(2048)
    expected[:8] = b'bootpara'
    expected[12:16] = data[12:16]
    expected[512:520] = b'NORPARAM'
    # flash_size is encoded in MiB, not bytes. All other unused fields,
    # header values, reserved bytes and ABI padding must remain zero.
    struct.pack_into('<13I', expected, 520, 0x6b, 4, 1, 16, 0, 65536,
                     0, 0, 20000000, 0xaaaaffff, 0, 0x10104, 0x10101)
    expected[572] = 8
    expected[1024:1536] = data[1024:1536]
    require(data == bytes(expected), 'metadata profile or reserved bytes mismatch')
    result.update(state='valid', profile=PROFILE_ID, checksum=f'{checksum:08x}')
    return result


def describe_image(image):
    """Describe code, validated metadata and fixed regions independently."""
    require(type(image) is bytes and len(image) == SIZE,
            'image must be immutable complete bytes')

    def regions(ranges):
        return [dict(offset=start, bytes=end - start, sha256=sha(image[start:end]))
                for start, end in ranges]

    return dict(code=regions(RANGES),
                metadata=classify_metadata(image[METADATA[0]:METADATA[1]]),
                fixed_preserved=regions(PRESERVED))


def authenticate(original, candidate):
    """Accept only immutable, exact pinned complete source images."""
    require(type(original) is bytes and type(candidate) is bytes,
            'source images must be immutable bytes')
    require(len(original) == len(candidate) == SIZE, 'source geometry mismatch')
    require(sha(original) == ORIGINAL_SHA256, 'original source pin mismatch')
    require(sha(candidate) == CANDIDATE_SHA256, 'candidate source pin mismatch')
    for start, end in PRESERVED + (METADATA,):
        require(original[start:end] == candidate[start:end],
                'candidate changed preserved bytes')
    require(classify_metadata(original[METADATA[0]:METADATA[1]])['state'] == 'erased',
            'original source metadata must be erased')


def desired_image(original, candidate, current, direction, restore_id, restore_image):
    if direction == 'install':
        require(restore_id == 'original' and restore_image is None,
                'restore options are invalid for install')
        return (candidate[:METADATA[0]] + current[METADATA[0]:METADATA[1]] +
                candidate[METADATA[1]:])
    require(type(restore_id) is str and restore_id in RESTORE_SHA256,
            'unregistered restore ID')
    if restore_id == 'original':
        desired = original if restore_image is None else restore_image
        expected = ORIGINAL_SHA256
    else:
        require(restore_image is not None, 'registered restore image is required')
        desired = restore_image
        expected = RESTORE_SHA256[restore_id]
    require(type(desired) is bytes and len(desired) == SIZE,
            'restore image must be immutable complete bytes')
    require(sha(desired) == expected, 'registered restore source pin mismatch')
    description = describe_image(desired)
    reference = original if restore_id == 'original' else candidate
    for start, end in RANGES + PRESERVED:
        require(desired[start:end] == reference[start:end],
                'restore source code or fixed preserved bytes mismatch')
    require(description['metadata']['state'] ==
            ('erased' if restore_id == 'original' else 'valid'),
            'restore source metadata state mismatch')
    return desired


def build_plan(original, candidate, current, direction, expected_current_sha256,
               *, restore_id='original', restore_image=None):
    """Bind code, valid/erased metadata and full reconstruction to a live hash.

    Arbitrary exact-pinned interrupted code is admissible; malformed interrupted
    metadata requires a separate reviewed recovery registration (not provided).
    """
    authenticate(original, candidate)
    require(direction in ('install', 'restore'), 'invalid direction')
    require(type(current) is bytes and len(current) == SIZE,
            'current image must be immutable complete bytes')
    require(type(expected_current_sha256) is str and
            len(expected_current_sha256) == 64 and
            all(c in '0123456789abcdef' for c in expected_current_sha256),
            'invalid current hash pin')
    require(sha(current) == expected_current_sha256, 'current hash pin mismatch')
    for start, end in PRESERVED:
        require(current[start:end] == original[start:end],
                'current preserved bytes changed')
    current_description = describe_image(current)
    desired = desired_image(original, candidate, current, direction,
                            restore_id, restore_image)
    units = []
    for index in range(26):
        start, end = index * ERASE, (index + 1) * ERASE
        old, new = current[start:end], desired[start:end]
        units.append(dict(index=index, offset=start, bytes=ERASE,
                          old_sha256=sha(old), new_sha256=sha(new),
                          differing_bytes=sum(a != b for a, b in zip(old, new)),
                          changed=old != new))
    plan = dict(schema=2, operation='spi-unit-transaction', direction=direction,
                layout_id=LAYOUT_ID, supported_metadata_profile=PROFILE_ID,
                metadata_range=list(METADATA),
                metadata_policy='preserve-live' if direction == 'install' else 'exact-backup',
                restore_id=restore_id if direction == 'restore' else None,
                restore_sha256=sha(desired) if direction == 'restore' else None,
                bytes=SIZE, erase_bytes=ERASE, write_chunk_bytes=CHUNK,
                original_sha256=ORIGINAL_SHA256,
                candidate_sha256=CANDIDATE_SHA256,
                current_sha256=expected_current_sha256,
                desired_sha256=sha(desired),
                current_image=current_description, desired_image=describe_image(desired),
                replacement_ranges=[list(r) for r in RANGES],
                preserved_ranges=[list(r) for r in PRESERVED],
                units=units, execution_order=(list(range(26))
                    if direction == 'restore' and restore_id == 'original'
                    else list(HEADER_LAST)),
                untouched_units=list(range(26, 256)), hardware_acceptance=False)
    plan['plan_sha256'] = sha(canonical(plan))
    return plan


class TransactionError(Exception):
    """Failure with a conservative partial receipt; no automatic continuation."""
    def __init__(self, receipt):
        self.receipt = receipt
        super().__init__(receipt['error'])


def execute(plan, original, candidate, *, read, erase, write, guard, emit,
            restore_id='original', restore_image=None):
    """Execute a reviewed plan through explicit trusted callbacks only.

    read(offset, size) returns exact bytes; erase(offset, ERASE) either completes
    or raises; write(offset, bytes) returns a strictly positive accepted count.
    guard() and emit(event) return normally only on success, otherwise raise.
    emit must durably capture each event before returning. Callback exceptions,
    including interruptions, stop execution and raise TransactionError.
    """
    receipt = dict(schema=2, success=False, hardware_acceptance=False,
                   plan_sha256=None, attempted_units=[], completed_units=[],
                   skipped_units=[], erase_attempted_bytes=0,
                   write_attempted_bytes=0, write_confirmed_bytes=0,
                   mutation_possible=False, active_unit=None,
                   stage='validate', final_verified=False)

    def event(kind, **fields):
        emit(dict(event=kind, plan_sha256=receipt['plan_sha256'],
                  hardware_acceptance=False, **fields))

    def exact_read(offset, size):
        data = read(offset, size)
        require(type(data) is bytes and len(data) == size,
                f'invalid read at {offset} for {size} bytes')
        return data

    def full_read():
        return b''.join(exact_read(offset, ERASE)
                        for offset in range(0, SIZE, ERASE))

    try:
        # Freeze caller-owned dictionaries before any callback can mutate them.
        reviewed = json.loads(canonical(plan))
        require(reviewed.get('schema') == 2, 'unsupported reviewed plan schema')
        receipt['plan_sha256'] = reviewed['plan_sha256']
        authenticate(original, candidate)
        receipt['stage'] = 'initial-guard'
        guard()
        receipt['stage'] = 'fresh-current-read'
        current = full_read()
        fresh = build_plan(original, candidate, current, reviewed['direction'],
                           reviewed['current_sha256'], restore_id=restore_id,
                           restore_image=restore_image)
        require(canonical(fresh) == canonical(reviewed), 'reviewed plan is stale or changed')
        guard()
        desired = desired_image(original, candidate, current, reviewed['direction'],
                                restore_id, restore_image)
        receipt['stage'] = 'plan-event'
        event('plan_revalidated')
        for index in fresh['execution_order']:
            unit = fresh['units'][index]
            offset = unit['offset']
            receipt['active_unit'] = index
            receipt['stage'] = 'unit-guard'
            guard()
            receipt['stage'] = 'unit-current-read'
            require(sha(exact_read(offset, ERASE)) == unit['old_sha256'],
                    f'unit {index} changed since fresh plan')
            if not unit['changed']:
                receipt['stage'] = 'skip-event'
                event('unit_skipped', unit=index)
                receipt['skipped_units'].append(index)
                continue
            # Recheck after the read, immediately before the durable intent.
            receipt['stage'] = 'erase-guard'
            guard()
            receipt['stage'] = 'erase-intent-event'
            event('erase_attempt', unit=index, offset=offset, bytes=ERASE)
            receipt['attempted_units'].append(index)
            receipt['erase_attempted_bytes'] += ERASE
            receipt['mutation_possible'] = True
            receipt['stage'] = 'erase'
            erase(offset, ERASE)
            receipt['stage'] = 'erase-readback'
            require(exact_read(offset, ERASE) == b'\xff' * ERASE,
                    f'unit {index} erase verification failed')
            image = desired[offset:offset + ERASE]
            for chunk_start in range(0, ERASE, CHUNK):
                position = chunk_start
                end = chunk_start + CHUNK
                while position < end:
                    receipt['stage'] = 'write-guard'
                    guard()
                    data = image[position:end]
                    receipt['stage'] = 'write'
                    receipt['write_attempted_bytes'] += len(data)
                    count = write(offset + position, data)
                    require(type(count) is int and 0 < count <= len(data),
                            f'invalid write count at {offset + position}')
                    receipt['write_confirmed_bytes'] += count
                    position += count
            receipt['stage'] = 'unit-readback'
            require(exact_read(offset, ERASE) == image,
                    f'unit {index} complete verification failed')
            receipt['stage'] = 'unit-completion-guard'
            guard()
            receipt['stage'] = 'unit-completion-event'
            event('unit_completed', unit=index, sha256=unit['new_sha256'])
            receipt['completed_units'].append(index)
        receipt['active_unit'] = None
        receipt['stage'] = 'final-guard'
        guard()
        receipt['stage'] = 'final-readback'
        require(full_read() == desired, 'full image final verification failed')
        receipt['stage'] = 'final-completion-guard'
        guard()
        receipt['stage'] = 'completion-event'
        event('transaction_verified', sha256=sha(desired))
        receipt.update(success=True, final_verified=True, stage='complete',
                       final_sha256=sha(desired))
        return receipt
    except BaseException as error:
        # Do not call emit or perform recovery: its failure may be the cause.
        receipt['error'] = f'{type(error).__name__}: {error}'
        raise TransactionError(receipt) from error
