"""API-only, callback-driven A733 SPI transaction preparation; no device access.

A trusted future adapter owns identity, quiescence, recovery, authorization and
receipt durability. Model execution never establishes hardware acceptance.
"""
import hashlib
import json

SIZE = 16777216
ERASE = 65536
CHUNK = 4096
ORIGINAL_SHA256 = 'dffab0dd410657cb30c7b2fd7f2586a4792e8472e58882b3532581f8111a646d'
CANDIDATE_SHA256 = '83edcd7f448e8353163666016a6bae3e9b70d041a5e4d8454f48931839205e90'
RANGES = ((0, 237568), (262144, 1654784))
PRESERVED = ((237568, 262144), (1654784, SIZE))


def sha(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode('ascii')


def authenticate(original, candidate):
    """Accept only immutable, exact pinned complete source images."""
    require(type(original) is bytes and type(candidate) is bytes,
            'source images must be immutable bytes')
    require(len(original) == len(candidate) == SIZE, 'source geometry mismatch')
    require(sha(original) == ORIGINAL_SHA256, 'original source pin mismatch')
    require(sha(candidate) == CANDIDATE_SHA256, 'candidate source pin mismatch')
    for start, end in PRESERVED:
        require(original[start:end] == candidate[start:end],
                'candidate changed preserved bytes')


def build_plan(original, candidate, current, direction, expected_current_sha256):
    """Prepare a JSON-compatible review plan; never invoke any callbacks.

    An interrupted image is admissible only under the caller's exact full-image
    hash and unchanged preservation bytes. No assumption about atomic writes is
    made inside the replacement ranges.
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
    desired = candidate if direction == 'install' else original
    units = []
    for index in range(26):
        start, end = index * ERASE, (index + 1) * ERASE
        old, new = current[start:end], desired[start:end]
        units.append(dict(index=index, offset=start, bytes=ERASE,
                          old_sha256=sha(old), new_sha256=sha(new),
                          differing_bytes=sum(a != b for a, b in zip(old, new)),
                          changed=old != new))
    plan = dict(schema=1, operation='spi-unit-transaction', direction=direction,
                bytes=SIZE, erase_bytes=ERASE, write_chunk_bytes=CHUNK,
                original_sha256=ORIGINAL_SHA256,
                candidate_sha256=CANDIDATE_SHA256,
                current_sha256=expected_current_sha256,
                desired_sha256=sha(desired),
                replacement_ranges=[list(r) for r in RANGES],
                preserved_ranges=[list(r) for r in PRESERVED],
                units=units, execution_order=(list(range(4, 26)) + [1, 2, 3, 0]
                                             if direction == 'install' else list(range(26))),
                untouched_units=list(range(26, 256)),
                hardware_acceptance=False)
    plan['plan_sha256'] = sha(canonical(plan))
    return plan


class TransactionError(Exception):
    """Failure with a conservative partial receipt; no automatic continuation."""
    def __init__(self, receipt):
        self.receipt = receipt
        super().__init__(receipt['error'])


def execute(plan, original, candidate, *, read, erase, write, guard, emit):
    """Execute a reviewed plan through explicit trusted callbacks only.

    read(offset, size) returns exact bytes; erase(offset, ERASE) either completes
    or raises; write(offset, bytes) returns a strictly positive accepted count.
    guard() and emit(event) return normally only on success, otherwise raise.
    emit must durably capture each event before returning. Callback exceptions,
    including interruptions, stop execution and raise TransactionError.
    """
    receipt = dict(schema=1, success=False, hardware_acceptance=False,
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
        receipt['plan_sha256'] = reviewed['plan_sha256']
        authenticate(original, candidate)
        receipt['stage'] = 'initial-guard'
        guard()
        receipt['stage'] = 'fresh-current-read'
        current = full_read()
        fresh = build_plan(original, candidate, current, reviewed['direction'],
                           reviewed['current_sha256'])
        require(canonical(fresh) == canonical(reviewed), 'reviewed plan is stale or changed')
        guard()
        desired = candidate if reviewed['direction'] == 'install' else original
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
