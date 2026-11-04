"""Run with: python3 -m unittest discover -s tests -p 'test_plan_nvme.py' -v."""

import copy
import importlib.util
import contextlib
import io
import json
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('plan_nvme', Path(__file__).parents[1] / 'scripts/plan-nvme.py')
planner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(planner)


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.disk = {'path': '/dev/nvme0n1', 'kname': 'nvme0n1', 'type': 'disk',
                     'tran': 'nvme', 'model': 'Test SSD', 'serial': 'TEST123',
                     'size': 250_000_000_000, 'log-sec': 512, 'phy-sec': 4096,
                     'ro': False, 'mountpoints': [None], 'maj:min': '259:0',
                     'children': [{'path': '/dev/nvme0n1p1', 'kname': 'nvme0n1p1',
                                   'type': 'part', 'ro': False, 'mountpoints': [None],
                                   'maj:min': '259:1'}]}
        for node in planner.flatten([self.disk]):
            base = self.root / 'sys/class/block' / node['kname']
            base.joinpath('holders').mkdir(parents=True)
            base.joinpath('dev').write_text(node['maj:min'])
            base.joinpath('ro').write_text('0')
        partition = self.root / 'sys/class/block/nvme0n1/nvme0n1p1'
        partition.mkdir()
        partition.joinpath('partition').write_text('1')
        self.root.joinpath('proc/self').mkdir(parents=True)
        self.root.joinpath('proc/self/mountinfo').write_text('')
        self.root.joinpath('proc/swaps').write_text('Filename\tType\tSize\tUsed\tPriority\n')
        self.identities = {'/dev/disk/by-id/nvme-test': ('/dev/nvme0n1', '259:0'),
                           '/dev/nvme0n1': ('/dev/nvme0n1', '259:0'),
                           '/dev/nvme0n1p1': ('/dev/nvme0n1p1', '259:1')}

    def run_plan(self, target='/dev/disk/by-id/nvme-test', **kwargs):
        with patch.object(planner, 'Path', lambda p: self.root / str(p).lstrip('/')):
            return planner.plan({'blockdevices': [self.disk]}, target,
                                resolver=self.identities.__getitem__, **kwargs)

    def test_actual_decimal_capacity_and_output_invariants(self):
        result = self.run_plan(expected_serial='TEST123', expected_bytes=250_000_000_000)
        self.assertEqual(result['target']['size_bytes'], 250_000_000_000)
        self.assertFalse(result['writes_performed'])
        self.assertEqual(result['operation'], 'plan-only')
        parts = result['layout']['partitions']
        self.assertEqual([p['size_bytes'] for p in parts[:4]],
                         [512 * planner.MIB, 4 * planner.GIB, 4 * planner.GIB, 8 * planner.GIB])
        previous_end = planner.MIB
        for part in parts:
            self.assertEqual(part['start_bytes'], previous_end)
            self.assertEqual(part['start_bytes'] % planner.MIB, 0)
            self.assertEqual(part['size_bytes'] % planner.MIB, 0)
            self.assertEqual((part['end_lba'] + 1) * 512,
                             part['start_bytes'] + part['size_bytes'])
            previous_end += part['size_bytes']
        self.assertEqual(previous_end + result['layout']['tail_reserved_bytes'], 250_000_000_000)
        self.assertGreaterEqual(result['layout']['tail_reserved_bytes'], 33 * 512)
        self.assertEqual(set(result['authorization_evidence']['devices_checked']), {'259:0', '259:1'})

    def test_4k_sectors_preserve_byte_layout(self):
        self.disk['log-sec'] = 4096
        self.disk['size'] = 250_000_000_000 // 4096 * 4096
        result = self.run_plan()
        for part in result['layout']['partitions']:
            self.assertEqual(part['start_lba'] * 4096, part['start_bytes'])
            self.assertEqual((part['end_lba'] + 1) * 4096,
                             part['start_bytes'] + part['size_bytes'])
        self.assertGreaterEqual(result['layout']['tail_reserved_bytes'], 33 * 4096)

    def test_reject_large_logical_sectors_overlapping_primary_gpt(self):
        for logical in (512 * 1024, planner.MIB):
            with self.subTest(logical=logical):
                self.disk['log-sec'] = logical
                self.disk['phy-sec'] = logical
                self.disk['size'] = 250_000_000_000 // logical * logical
                with self.assertRaisesRegex(planner.UnsafeTarget, 'sector geometry'):
                    self.run_plan()

    def test_cli_requests_tree_and_rejects_mounted_descendant(self):
        self.disk['children'][0]['mountpoints'] = ['/state']
        output = subprocess.CompletedProcess([], 0, json.dumps({'blockdevices': [self.disk]}), '')
        real_plan = planner.plan

        def fixture_plan(inventory, target, **kwargs):
            return real_plan(inventory, target, resolver=self.identities.__getitem__, **kwargs)

        errors = io.StringIO()
        with patch.object(planner.subprocess, 'run', return_value=output) as run, \
                patch.object(planner, 'plan', side_effect=fixture_plan), \
                patch.object(planner, 'Path', lambda p: self.root / str(p).lstrip('/')), \
                patch.object(planner.sys, 'argv', ['plan-nvme.py', '--target', '/dev/disk/by-id/nvme-test']), \
                contextlib.redirect_stderr(errors):
            self.assertEqual(planner.main(), 1)
        self.assertIn('--tree', run.call_args.args[0])
        self.assertIn('mounted', errors.getvalue())

    def test_reject_kernel_read_only(self):
        self.root.joinpath('sys/class/block/nvme0n1p1/ro').write_text('1')
        with self.assertRaisesRegex(planner.UnsafeTarget, 'read-only'):
            self.run_plan()

    def test_reject_hidden_partition(self):
        hidden = self.root.joinpath('sys/class/block/nvme0n1/nvme0n1p2')
        hidden.mkdir()
        hidden.joinpath('partition').write_text('2')
        with self.assertRaisesRegex(planner.UnsafeTarget, 'partitions'):
            self.run_plan()

    def test_reject_regular_file(self):
        with self.assertRaisesRegex(planner.UnsafeTarget, 'Not a block device'):
            planner.identify(self.root / 'proc/swaps')

    def test_reject_mounted_descendant_in_lsblk(self):
        self.disk['children'][0]['mountpoints'] = ['/state']
        with self.assertRaisesRegex(planner.UnsafeTarget, 'mounted'):
            self.run_plan()

    def test_reject_mounted_descendant_in_proc(self):
        self.root.joinpath('proc/self/mountinfo').write_text('1 0 259:1 / /state rw - ext4 /dev/nvme0n1p1 rw\n')
        with self.assertRaisesRegex(planner.UnsafeTarget, 'mounted'):
            self.run_plan()

    def test_reject_holder(self):
        self.root.joinpath('sys/class/block/nvme0n1p1/holders/dm-0').touch()
        with self.assertRaisesRegex(planner.UnsafeTarget, 'holders'):
            self.run_plan()

    def test_reject_swap_alias(self):
        self.root.joinpath('proc/swaps').write_text('Filename Type Size Used Priority\n/dev/alias partition 1 0 -2\n')
        fake_stat = type('Stat', (), {'st_mode': 0o060000, 'st_rdev': __import__('os').makedev(259, 1)})()
        real_stat = planner.os.stat
        with patch.object(planner.os, 'stat', side_effect=lambda p, *a, **kw: fake_stat if str(p) == '/dev/alias' else real_stat(p, *a, **kw)):
            with self.assertRaisesRegex(planner.UnsafeTarget, 'swap'):
                self.run_plan()

    def test_reject_partition_target(self):
        with self.assertRaisesRegex(planner.UnsafeTarget, 'whole NVMe'):
            self.run_plan('/dev/nvme0n1p1')

    def test_reject_expected_identifier_mismatch(self):
        for kwargs in ({'expected_serial': 'OTHER'}, {'expected_bytes': 250 * planner.GIB}):
            with self.subTest(kwargs=kwargs), self.assertRaises(planner.UnsafeTarget):
                self.run_plan(**kwargs)

    def test_reject_path_identity_mismatch(self):
        self.identities['/dev/nvme0n1'] = ('/dev/nvme1n1', '259:9')
        with self.assertRaisesRegex(planner.UnsafeTarget, 'identit'):
            self.run_plan()

    def test_reject_undersized_and_accept_minimum(self):
        fixed = (512 + 4 * 1024 + 4 * 1024 + 8 * 1024) * planner.MIB
        minimum = fixed + 2 * planner.MIB + 33 * 512
        self.disk['size'] = minimum - 512
        with self.assertRaisesRegex(planner.UnsafeTarget, 'too small'):
            self.run_plan()
        self.disk['size'] = minimum
        self.assertEqual(self.run_plan()['layout']['partitions'][-1]['size_bytes'], planner.MIB)

    def test_reject_read_only_missing_identity_and_wrong_transport(self):
        original = copy.deepcopy(self.disk)
        for changes in ({'ro': True}, {'serial': ''}, {'model': None}, {'tran': 'sata'}, {'log-sec': None}):
            self.disk = {**copy.deepcopy(original), **changes}
            with self.subTest(changes=changes), self.assertRaises(planner.UnsafeTarget):
                self.run_plan()

    def test_reject_inconsistent_kernel_inventory(self):
        self.root.joinpath('sys/class/block/nvme0n1p1/dev').write_text('259:99')
        with self.assertRaisesRegex(planner.UnsafeTarget, 'identit'):
            self.run_plan()

    def test_missing_safety_evidence_fails_closed(self):
        self.root.joinpath('sys/class/block/nvme0n1p1/ro').unlink()
        with self.assertRaises(OSError):
            self.run_plan()


if __name__ == '__main__':
    unittest.main()
