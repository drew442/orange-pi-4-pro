"""Host-only NOR callback model; never opens a device."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    'spi_transaction', Path(__file__).resolve().parents[1] /
    'scripts/spi-unit-transaction.py')
spi = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(spi)


class Nor:
    def __init__(self, image):
        self.image = bytearray(image)
        self.erased = []
        self.written = []
        self.events = []
        self.limit = spi.CHUNK
        self.fail_event = None
        self.fail_erase = False
        self.fail_write = False
        self.zero_write = False
        self.bad_erase = False
        self.bad_write = False
        self.fail_guard_after_erase = False
        self.corrupt_final = False
        self.reads = 0

    def read(self, offset, size):
        self.reads += 1
        result = bytes(self.image[offset:offset + size])
        if self.corrupt_final and self.erased and size == spi.ERASE and offset == 26 * spi.ERASE:
            return bytes([result[0] ^ 1]) + result[1:]
        return result

    def erase(self, offset, size):
        assert size == spi.ERASE and offset % size == 0
        assert 0 <= offset < 26 * spi.ERASE
        assert self.events[-1]['event'] == 'erase_attempt'
        self.erased.append(offset // size)
        self.image[offset:offset + size] = b'\xff' * size
        if self.fail_erase:
            raise OSError('erase may have completed')
        if self.bad_erase:
            self.image[offset] = 0

    def write(self, offset, data):
        assert offset // spi.ERASE in self.erased
        assert len(data) <= spi.CHUNK
        assert offset // spi.CHUNK == (offset + len(data) - 1) // spi.CHUNK
        if self.zero_write:
            return 0
        count = min(self.limit, len(data))
        assert all((old & new) == new for old, new in
                   zip(self.image[offset:offset + count], data[:count]))
        self.image[offset:offset + count] = data[:count]
        self.written.append((offset, count))
        if self.fail_write:
            raise KeyboardInterrupt('interrupted after programming')
        if self.bad_write:
            self.image[offset] ^= 1
        return count

    def guard(self):
        if self.fail_guard_after_erase and self.erased:
            raise RuntimeError('guard lost')

    def emit(self, event):
        assert event['hardware_acceptance'] is False
        if event['event'] == self.fail_event:
            raise OSError('event capture unavailable')
        self.events.append(event)

    def execute(self, plan, original, candidate):
        return spi.execute(plan, original, candidate, read=self.read,
                           erase=self.erase, write=self.write,
                           guard=self.guard, emit=self.emit)


class TransactionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Non-FF preservation bytes prove full partial units are reconstructed.
        original = bytearray([0xA5]) * spi.SIZE
        original[spi.METADATA[0]:spi.METADATA[1]] = b'\xff' * 2048
        cls.original = bytes(original)
        candidate = bytearray(cls.original)
        for start, end in spi.RANGES:
            candidate[start:end] = bytes([0x3C]) * (end - start)
        cls.candidate = bytes(candidate)

    def setUp(self):
        self.pins = patch.multiple(spi, ORIGINAL_SHA256=spi.sha(self.original),
                                   CANDIDATE_SHA256=spi.sha(self.candidate))
        self.pins.start()
        self.addCleanup(self.pins.stop)
        self.plan = self.plan_for(self.original)
        self.nor = Nor(self.original)

    def plan_for(self, current, direction='install'):
        return spi.build_plan(self.original, self.candidate, current, direction,
                              spi.sha(current))

    def run_nor(self, plan=None):
        return self.nor.execute(self.plan if plan is None else plan,
                                self.original, self.candidate)

    def failure(self):
        with self.assertRaises(spi.TransactionError) as caught:
            self.run_nor()
        result = caught.exception.receipt
        self.assertFalse(result['success'])
        self.assertFalse(result['hardware_acceptance'])
        return result

    def test_complete_install_preserves_exact_full_units(self):
        receipt = self.run_nor()
        self.assertEqual(bytes(self.nor.image), self.candidate)
        self.assertEqual(self.nor.erased, self.plan['execution_order'])
        self.assertEqual(receipt['completed_units'], self.plan['execution_order'])
        self.assertEqual(receipt['write_confirmed_bytes'], 26 * spi.ERASE)
        self.assertTrue(receipt['final_verified'])
        self.assertFalse(receipt['hardware_acceptance'])
        self.assertEqual(self.nor.reads, 256 + 26 * 3 + 256)

    def test_install_and_restore_callbacks_follow_reviewed_order(self):
        install_order = list(range(4, 26)) + [1, 2, 3, 0]
        self.assertEqual(self.plan['execution_order'], install_order)
        self.assertEqual([unit['index'] for unit in self.plan['units']], list(range(26)))
        self.run_nor()
        self.assertEqual([event['unit'] for event in self.nor.events
                          if event['event'] == 'erase_attempt'], install_order)
        restore = self.plan_for(self.candidate, 'restore')
        self.assertEqual(restore['execution_order'], list(range(26)))
        self.nor.erased.clear()
        self.nor.events.clear()
        self.run_nor(restore)
        self.assertEqual(self.nor.erased, list(range(26)))
        self.assertEqual(bytes(self.nor.image), self.original)

    def test_changed_reviewed_execution_order_refuses(self):
        self.plan['execution_order'] = list(range(26))
        self.failure()
        self.assertEqual(self.nor.erased, [])

    def test_short_writes_complete_chunks(self):
        self.nor.limit = 997
        self.run_nor()
        self.assertEqual(bytes(self.nor.image), self.candidate)
        self.assertGreater(len(self.nor.written), 26 * 16)

    def test_already_desired_skips_all(self):
        self.nor = Nor(self.candidate)
        receipt = self.run_nor(self.plan_for(self.candidate))
        self.assertEqual(receipt['skipped_units'], self.plan['execution_order'])
        self.assertEqual(self.nor.erased, [])
        self.assertFalse(receipt['mutation_possible'])

    def test_erase_failure_conservatively_records_attempt(self):
        self.nor.fail_erase = True
        receipt = self.failure()
        self.assertEqual(receipt['attempted_units'], [4])
        self.assertEqual(receipt['completed_units'], [])
        self.assertEqual(receipt['erase_attempted_bytes'], spi.ERASE)
        self.assertTrue(receipt['mutation_possible'])
        self.assertEqual(self.nor.written, [])

    def test_zero_write_stops(self):
        self.nor.zero_write = True
        receipt = self.failure()
        self.assertEqual(self.nor.erased, [4])
        self.assertEqual(receipt['write_confirmed_bytes'], 0)
        self.assertEqual(receipt['write_attempted_bytes'], spi.CHUNK)

    def test_interruption_then_fresh_restore(self):
        self.nor.fail_write = True
        receipt = self.failure()
        self.assertIn('KeyboardInterrupt', receipt['error'])
        self.assertEqual(self.nor.erased, [4])
        interrupted = bytes(self.nor.image)
        restore = self.plan_for(interrupted, 'restore')
        self.nor.fail_write = False
        result = self.run_nor(restore)
        self.assertTrue(result['success'])
        self.assertEqual(bytes(self.nor.image), self.original)
        self.assertEqual(self.nor.erased, [4, 4])

    def test_corrupt_erase_readback_stops(self):
        self.nor.bad_erase = True
        self.assertEqual(self.failure()['stage'], 'erase-readback')
        self.assertEqual(self.nor.written, [])
        self.assertEqual(self.nor.erased, [4])

    def test_corrupt_write_readback_stops(self):
        self.nor.bad_write = True
        self.assertEqual(self.failure()['stage'], 'unit-readback')
        self.assertEqual(self.nor.erased, [4])

    def test_guard_failure_stops_further_writes(self):
        self.nor.fail_guard_after_erase = True
        self.assertEqual(self.failure()['stage'], 'write-guard')
        self.assertEqual(self.nor.written, [])
        self.assertEqual(self.nor.erased, [4])

    def test_intent_event_failure_prevents_erase(self):
        self.nor.fail_event = 'erase_attempt'
        receipt = self.failure()
        self.assertEqual(self.nor.erased, [])
        self.assertEqual(receipt['attempted_units'], [])
        self.assertFalse(receipt['mutation_possible'])

    def test_completion_event_failure_stops_next_unit(self):
        self.nor.fail_event = 'unit_completed'
        receipt = self.failure()
        self.assertEqual(self.nor.erased, [4])
        self.assertEqual(receipt['completed_units'], [])
        self.assertEqual(receipt['attempted_units'], [4])

    def test_final_full_image_read_detects_untouched_corruption(self):
        self.nor.corrupt_final = True
        self.assertEqual(self.failure()['stage'], 'final-readback')

    def test_final_event_failure_is_not_success(self):
        self.nor.fail_event = 'transaction_verified'
        self.assertFalse(self.failure()['final_verified'])

    def test_stale_current_plan_refuses_without_erase(self):
        self.nor.image[0] ^= 1
        self.failure()
        self.assertEqual(self.nor.erased, [])

    def test_modified_reviewed_plan_refuses(self):
        self.plan['units'][0]['new_sha256'] = '0' * 64
        self.failure()
        self.assertEqual(self.nor.erased, [])

    def test_current_hash_pin_and_preservation_are_required(self):
        with self.assertRaisesRegex(ValueError, 'hash pin mismatch'):
            spi.build_plan(self.original, self.candidate, self.original,
                           'install', '0' * 64)
        for offset in (237568, 1654784, 26 * spi.ERASE, spi.SIZE - 1):
            current = bytearray(self.original)
            current[offset] ^= 1
            with self.assertRaisesRegex(ValueError, 'preserved'):
                self.plan_for(bytes(current))

    def test_source_pins_geometry_and_immutable_inputs(self):
        with self.assertRaisesRegex(ValueError, 'candidate source pin'):
            spi.build_plan(self.original, self.original, self.original,
                           'install', spi.sha(self.original))
        with self.assertRaisesRegex(ValueError, 'geometry'):
            spi.authenticate(self.original[:-1], self.candidate)
        with self.assertRaisesRegex(ValueError, 'immutable'):
            self.plan_for(bytearray(self.original))

    def test_plan_is_canonical_and_has_unit_differences(self):
        plan = self.plan_for(self.original)
        digest = plan.pop('plan_sha256')
        self.assertEqual(digest, spi.sha(spi.canonical(plan)))
        self.assertEqual(plan['units'][3]['differing_bytes'], 40960)
        self.assertEqual(plan['untouched_units'], list(range(26, 256)))
        self.assertFalse(plan['hardware_acceptance'])

    def test_unit_change_after_fresh_plan_prevents_erase(self):
        base_read = self.nor.read

        def drift(offset, size):
            if self.nor.reads == 256:
                self.nor.image[4 * spi.ERASE] ^= 1
            return base_read(offset, size)

        self.nor.read = drift
        self.assertEqual(self.failure()['stage'], 'unit-current-read')
        self.assertEqual(self.nor.erased, [])

    def test_readback_exception_stops_after_attempted_erase(self):
        base_read = self.nor.read

        def broken(offset, size):
            if self.nor.erased:
                raise OSError('readback lost')
            return base_read(offset, size)

        self.nor.read = broken
        self.assertEqual(self.failure()['stage'], 'erase-readback')
        self.assertEqual(self.nor.erased, [4])
        self.assertEqual(self.nor.written, [])

    def test_excess_write_count_stops(self):
        self.nor.write = lambda offset, data: len(data) + 1
        receipt = self.failure()
        self.assertEqual(receipt['write_confirmed_bytes'], 0)
        self.assertEqual(self.nor.erased, [4])

    def test_plan_capture_failure_prevents_erase(self):
        self.nor.fail_event = 'plan_revalidated'
        self.failure()
        self.assertEqual(self.nor.erased, [])

    def test_short_read_stops_before_mutation(self):
        self.nor.read = lambda offset, size: b''
        self.failure()
        self.assertEqual(self.nor.erased, [])


if __name__ == '__main__':
    unittest.main()
