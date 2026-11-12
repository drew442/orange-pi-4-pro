"""Portable profile/model tests plus explicitly reported retained snapshot checks.

Synthetic test pins are injected only into the loaded test module. None of these
callback checks establish physical boot, recovery, or KVM acceptance.
"""
import importlib.util
from pathlib import Path
import struct
import unittest
from unittest.mock import patch

from test_spi_unit_transaction import Nor

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('spi_metadata_engine',
                                             ROOT / 'scripts/spi-unit-transaction.py')
spi = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(spi)


def checked(data):
    result = bytearray(data)
    struct.pack_into('<I', result, 12, 0)
    checksum = (sum(struct.unpack('<512I', result)) + 0x5f0a6c39) & 0xffffffff
    struct.pack_into('<I', result, 12, checksum)
    return bytes(result)


def metadata(seed=1):
    result = bytearray(2048)
    result[:8] = b'bootpara'
    result[512:520] = b'NORPARAM'
    struct.pack_into('<13I', result, 520, 0x6b, 4, 1, 16, 0, 65536,
                     0, 0, 20000000, 0xaaaaffff, 0, 0x10104, 0x10101)
    result[572] = 8
    result[1024:1536] = bytes((index + seed) % 256 for index in range(512))
    return checked(result)


def with_metadata(image, data):
    return image[:spi.METADATA[0]] + data + image[spi.METADATA[1]:]


class MetadataTests(unittest.TestCase):
    def test_erased_and_synthetic_valid_profile(self):
        erased = spi.classify_metadata(b'\xff' * 2048)
        self.assertEqual(erased['state'], 'erased')
        self.assertIsNone(erased['profile'])
        valid = spi.classify_metadata(metadata())
        self.assertEqual(valid['state'], 'valid')
        self.assertEqual(valid['profile'], spi.PROFILE_ID)
        self.assertEqual(valid['sha256'], spi.sha(metadata()))
        # The vendor initializes length and boot0 checksum to zero.
        self.assertEqual(metadata()[16:32], bytes(16))

    def test_wrong_size_and_mutable_metadata_rejected(self):
        for value in (b'', bytes(2049), bytearray(metadata())):
            with self.subTest(value_type=type(value).__name__, size=len(value)):
                with self.assertRaisesRegex(ValueError, 'immutable complete'):
                    spi.classify_metadata(value)

    def test_checksum_and_both_magics_rejected(self):
        for offset, message in ((0, 'header magic'), (512, 'NOR magic'),
                                (12, 'checksum'), (1024, 'checksum')):
            data = bytearray(metadata())
            data[offset] ^= 1
            with self.subTest(offset=offset), self.assertRaisesRegex(ValueError, message):
                spi.classify_metadata(bytes(data))

    def test_unknown_profile_header_padding_and_unused_data_rejected(self):
        # Recompute a correct checksum so every semantic check is exercised.
        for offset in (8, 16, 20, 24, 28, 32, 256, 520, 532, 556,
                       572, 573, 576, 768, 1536, 2047):
            data = bytearray(metadata())
            data[offset] ^= 1
            with self.subTest(offset=offset), self.assertRaisesRegex(ValueError, 'profile'):
                spi.classify_metadata(checked(data))

    def test_all_ddr_bytes_are_opaque(self):
        data = bytearray(metadata())
        data[1024:1536] = b'\xff' * 512
        self.assertEqual(spi.classify_metadata(checked(data))['state'], 'valid')


class MetadataTransactions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Distinct non-FF fixed gaps make neighbor reconstruction observable.
        cls.original = with_metadata(bytes([0xa5]) * spi.SIZE, b'\xff' * 2048)
        candidate = bytearray(cls.original)
        for start, end in spi.RANGES:
            candidate[start:end] = bytes([0x3c]) * (end - start)
        cls.candidate = bytes(candidate)
        cls.cold = with_metadata(cls.candidate, metadata(1))
        cls.warm = with_metadata(cls.candidate, metadata(2))

    def setUp(self):
        pins = patch.multiple(spi, ORIGINAL_SHA256=spi.sha(self.original),
                              CANDIDATE_SHA256=spi.sha(self.candidate),
                              RESTORE_SHA256={'original': spi.sha(self.original),
                                  'postboot-20261111': spi.sha(self.cold),
                                  'postwarm-20261111': spi.sha(self.warm)})
        pins.start()
        self.addCleanup(pins.stop)

    def plan(self, current, direction='install', **options):
        return spi.build_plan(self.original, self.candidate, current, direction,
                              spi.sha(current), **options)

    def execute(self, model, plan, **options):
        return spi.execute(plan, self.original, self.candidate, read=model.read,
                           erase=model.erase, write=model.write,
                           guard=model.guard, emit=model.emit, **options)

    def test_schema_descriptions_and_metadata_only_noop(self):
        for image in (self.cold, self.warm):
            plan = self.plan(image)
            self.assertEqual(plan['schema'], 2)
            self.assertEqual(plan['metadata_policy'], 'preserve-live')
            self.assertEqual(plan['desired_sha256'], spi.sha(image))
            self.assertEqual(plan['current_image'], spi.describe_image(image))
            self.assertEqual(plan['desired_image'], plan['current_image'])
            self.assertEqual(len(plan['current_image']['code']), 2)
            self.assertEqual(len(plan['current_image']['fixed_preserved']), 2)
            model = Nor(image)
            result = self.execute(model, plan)
            self.assertTrue(result['success'])
            self.assertEqual(model.erased, [])

    def test_install_preserves_unit3_calibration_and_neighbors(self):
        current = bytearray(self.cold)
        # Exact-pinned interrupted bytes anywhere in code remain admissible.
        current[0] ^= 1
        current[3 * spi.ERASE:237568] = bytes(40960)
        current[262144:262151] = b'partial'
        current = bytes(current)
        model = Nor(current)
        result = self.execute(model, self.plan(current))
        self.assertTrue(result['success'])
        self.assertEqual(bytes(model.image), self.cold)
        self.assertEqual(model.erased, [4, 3, 0])
        self.assertEqual(bytes(model.image[237568:262144]), current[237568:262144])
        self.assertEqual(bytes(model.image[1654784:]), current[1654784:])

    def test_exact_registered_calibrated_restore_header_last_and_blank_first(self):
        for restore_id, target in (('postboot-20261111', self.cold),
                                   ('postwarm-20261111', self.warm),
                                   ('original', self.original)):
            options = dict(restore_id=restore_id, restore_image=target)
            current = self.candidate if restore_id == 'original' else self.original
            plan = self.plan(current, 'restore', **options)
            self.assertEqual(plan['metadata_policy'], 'exact-backup')
            self.assertEqual(plan['restore_id'], restore_id)
            self.assertEqual(plan['restore_sha256'], spi.sha(target))
            self.assertEqual(plan['execution_order'], list(range(26))
                             if restore_id == 'original' else spi.HEADER_LAST)
            model = Nor(current)
            result = self.execute(model, plan, **options)
            self.assertEqual(model.erased, plan['execution_order'])
            self.assertTrue(result['success'])
            self.assertEqual(bytes(model.image), target)

    def test_calibrated_restore_failures_stop_and_retain_receipts(self):
        options = dict(restore_id='postwarm-20261111', restore_image=self.warm)
        plan = self.plan(self.original, 'restore', **options)
        for setting, value, stage, mutated in (
                ('fail_erase', True, 'erase', True),
                ('zero_write', True, 'write', True),
                ('fail_write', True, 'write', True),
                ('fail_event', 'erase_attempt', 'erase-intent-event', False),
                ('fail_event', 'unit_completed', 'unit-completion-event', True)):
            model = Nor(self.original)
            setattr(model, setting, value)
            with self.subTest(setting=setting, value=value):
                with self.assertRaises(spi.TransactionError) as caught:
                    self.execute(model, plan, **options)
                receipt = caught.exception.receipt
                self.assertFalse(receipt['success'])
                self.assertEqual(receipt['stage'], stage)
                self.assertEqual(receipt['mutation_possible'], mutated)
                self.assertEqual(model.erased, [4] if mutated else [])
                self.assertNotIn(0, model.erased)

    def test_unregistered_missing_wrong_restore_and_install_options_refuse(self):
        for options in (dict(restore_id='unknown', restore_image=self.cold),
                        dict(restore_id='postboot-20261111'),
                        dict(restore_id='postboot-20261111', restore_image=self.warm),
                        dict(restore_id='original', restore_image=self.cold)):
            with self.subTest(options=list(options)), self.assertRaises(ValueError):
                self.plan(self.cold, 'restore', **options)
        for options in (dict(restore_id='postboot-20261111'),
                        dict(restore_image=self.original)):
            with self.assertRaisesRegex(ValueError, 'restore options'):
                self.plan(self.cold, **options)

    def test_registered_hash_still_requires_known_code_fixed_and_metadata(self):
        for offset, message in ((0, 'code or fixed'), (237568, 'code or fixed'),
                                (1654784, 'code or fixed'), (520, 'profile')):
            image = bytearray(self.cold)
            if offset == 520:
                data = bytearray(metadata())
                data[offset] ^= 1
                image = bytearray(with_metadata(bytes(image), checked(data)))
            else:
                image[offset] ^= 1
            image = bytes(image)
            with patch.dict(spi.RESTORE_SHA256, {'postboot-20261111': spi.sha(image)}):
                with self.assertRaisesRegex(ValueError, message):
                    self.plan(self.warm, 'restore', restore_id='postboot-20261111',
                              restore_image=image)

    def test_stale_metadata_schema_and_canonical_policy_refuse_before_mutation(self):
        cases = []
        cases.append((self.plan(self.cold), self.warm))
        for field, value in (('schema', 1), ('metadata_policy', 'exact-backup'),
                             ('restore_id', 'postboot-20261111'),
                             ('layout_id', 'other')):
            plan = self.plan(self.cold)
            plan[field] = value
            cases.append((plan, self.cold))
        plan = self.plan(self.cold)
        plan['current_image']['metadata']['sha256'] = '0' * 64
        cases.append((plan, self.cold))
        for plan, image in cases:
            model = Nor(image)
            with self.assertRaises(spi.TransactionError):
                self.execute(model, plan)
            self.assertEqual(model.erased, [])

    def test_restore_execution_requires_same_selected_identity_and_image(self):
        plan = self.plan(self.cold, 'restore', restore_id='postwarm-20261111',
                         restore_image=self.warm)
        for options in ({}, dict(restore_id='postboot-20261111', restore_image=self.cold),
                        dict(restore_id='postwarm-20261111', restore_image=self.cold)):
            model = Nor(self.cold)
            with self.assertRaises(spi.TransactionError):
                self.execute(model, plan, **options)
            self.assertEqual(model.erased, [])

    def test_partial_metadata_interruption_is_not_normally_admissible(self):
        plan = self.plan(self.cold, 'restore', restore_id='postwarm-20261111',
                         restore_image=self.warm)
        model = Nor(self.cold)
        base_write = model.write

        def interrupted(offset, data):
            if offset == 258048:
                # Only part of the chunk reached NOR; the 2 KiB record is torn.
                base_write(offset, data[:2052])
                raise KeyboardInterrupt('metadata programming interrupted')
            return base_write(offset, data)

        model.write = interrupted
        with self.assertRaises(spi.TransactionError) as caught:
            self.execute(model, plan, restore_id='postwarm-20261111',
                         restore_image=self.warm)
        self.assertTrue(caught.exception.receipt['mutation_possible'])
        self.assertEqual(model.erased, [3])
        with self.assertRaisesRegex(ValueError, 'metadata'):
            self.plan(bytes(model.image), 'restore')

    def test_invalid_metadata_and_fixed_region_refuse_normally(self):
        for offset in (237568, 258048, 260096, 262143, 1654784):
            image = bytearray(self.cold)
            image[offset] ^= 1
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                self.plan(bytes(image))


class RetainedMetadataSnapshots(unittest.TestCase):
    """A missing ignored fixture is reported explicitly, never passed silently."""
    def test_captured_cold_warm_and_erased_snapshots(self):
        paths = {
            'original': ROOT / 'out/spi-backup-20261108-01/spi.bin',
            'factory': ROOT / 'out/spi-nvme-original-20261108/a733-spi-nvme.bin',
            'postboot-20261111': ROOT / 'out/spi-nvme-hardware-20261111-01/postboot-spi.bin',
            'postwarm-20261111': ROOT / 'out/spi-nvme-hardware-20261111-01/postwarm-spi.bin',
        }
        missing = [str(path) for path in paths.values() if not path.is_file()]
        if missing:
            self.skipTest('RETAINED HARDWARE FIXTURE VALIDATION UNAVAILABLE: ' + ', '.join(missing))
        images = {key: path.read_bytes() for key, path in paths.items()}
        spi.authenticate(images['original'], images['factory'])
        for restore_id, expected_checksum in (('postboot-20261111', 'a9392ac4'),
                                               ('postwarm-20261111', 'c93b28e6')):
            image = images[restore_id]
            self.assertEqual(spi.sha(image), spi.RESTORE_SHA256[restore_id])
            description = spi.describe_image(image)
            self.assertEqual(description['metadata']['checksum'], expected_checksum)
            self.assertEqual(description['code'], spi.describe_image(images['factory'])['code'])
            self.assertEqual(description['fixed_preserved'],
                             spi.describe_image(images['original'])['fixed_preserved'])
            plan = spi.build_plan(images['original'], images['factory'], image,
                                  'install', spi.sha(image))
            self.assertFalse(any(unit['changed'] for unit in plan['units']))
            restore = spi.build_plan(images['original'], images['factory'], image,
                                     'restore', spi.sha(image), restore_id=restore_id,
                                     restore_image=image)
            self.assertFalse(any(unit['changed'] for unit in restore['units']))
        self.assertEqual(spi.describe_image(images['original'])['metadata']['state'], 'erased')
        self.assertEqual(spi.describe_image(images['factory'])['metadata']['state'], 'erased')


if __name__ == '__main__':
    unittest.main()
