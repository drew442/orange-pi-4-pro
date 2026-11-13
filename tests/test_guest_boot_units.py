"""Pure formatter regressions; no systemd, launch or hardware acceptance."""
import copy
import importlib.util
from pathlib import Path
import shlex
import unittest
from unittest import mock

BASE = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'guest_boot_units', BASE / 'configs/guest_boot_units.py')
UNITS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(UNITS)
MIB = 1024 * 1024
RAM = 12 * 1024 * MIB
VALID = {'schema': 1, 'name': 'one', 'uid': 60001, 'gid': 60001,
         'memory_mib': 128, 'memory_max_mib': 256, 'cpus': 2,
         'kernel': {'bytes': 100, 'sha256': 'a' * 64},
         'initrd': {'bytes': 200, 'sha256': 'b' * 64},
         'disk': {'bytes': 1024, 'sha256': 'c' * 64}}


def directives(text):
    return dict(line.split('=', 1) for line in text.splitlines() if '=' in line)


class ManagedUnitTests(unittest.TestCase):
    def test_fixed_supervisor_and_shutdown_contract(self):
        unit = UNITS.render_managed(VALID)
        lines = directives(unit)
        self.assertEqual(shlex.split(lines['ExecStart']), [
            '/usr/bin/python3', '-I', '-B',
            '/usr/lib/a733-tools/scripts/run-managed-guest.py', 'one'])
        expected = {
            'Description': 'A733 managed guest one',
            'After': r'a733-guest-prepare.service a733-guest-storage.service run-a733\x2dstate.mount run-a733\x2dguests.mount',
            'Requires': r'a733-guest-prepare.service a733-guest-storage.service run-a733\x2dstate.mount run-a733\x2dguests.mount',
            'RequiresMountsFor': '/run/a733-state /run/a733-guests',
            'Type': 'exec', 'User': 'root', 'Group': 'root',
            'Slice': 'a733-guests.slice', 'NoNewPrivileges': 'yes', 'UMask': '0077',
            'CapabilityBoundingSet': ('CAP_CHOWN CAP_DAC_OVERRIDE CAP_KILL '
                                      'CAP_SETGID CAP_SETUID CAP_SETPCAP CAP_SYS_PTRACE'),
            'Restart': 'no', 'KillMode': 'mixed', 'KillSignal': 'SIGTERM',
            'TimeoutStopSec': '420', 'SendSIGKILL': 'yes', 'MemoryMax': '256M',
            'MemorySwapMax': '0', 'TasksMax': '256', 'LimitCORE': '0',
            'StandardInput': 'null', 'StandardOutput': 'journal',
            'StandardError': 'journal', 'LogRateLimitIntervalSec': '30s',
            'LogRateLimitBurst': '1000',
        }
        self.assertEqual(set(lines), set(expected) | {'ExecStart'})
        for key, value in expected.items():
            self.assertEqual(lines[key], value)
        self.assertNotIn('[Install]', unit)
        self.assertEqual([line for line in unit.splitlines() if line.startswith('[')],
                         ['[Unit]', '[Service]'])
        self.assertTrue(unit.endswith('\n'))

    def test_preserves_native_mount_namespace(self):
        lines = directives(UNITS.render_managed(VALID))
        for key in ('PrivateMounts', 'PrivateTmp', 'PrivateDevices', 'ProtectSystem',
                    'ProtectHome', 'ReadOnlyPaths', 'ReadWritePaths', 'InaccessiblePaths',
                    'BindPaths', 'BindReadOnlyPaths', 'RootDirectory', 'RootImage',
                    'TemporaryFileSystem', 'MountAPIVFS', 'ProtectKernelTunables',
                    'ProtectControlGroups', 'ProtectKernelModules', 'ProtectKernelLogs'):
            self.assertNotIn(key, lines)

    def test_config_is_validated_and_cannot_inject_directives(self):
        bad = [{**VALID, 'name': name} for name in (
            'firstkvm', '../one', 'one%u', 'one\nExecStart=/bad',
            'one;bad', 'one bad', 'one$HOME', 'one\\bad', 'One')]
        bad += [{**VALID, 'memory_max_mib': value}
                for value in (True, '256\nUser=guest', 191, 256.0)]
        bad += [{**VALID, 'cpus': False}, {**VALID, 'ExecStart': '/bad'},
                {**VALID, 'kernel': {'bytes': 1, 'sha256': 'x'}}, None]
        for value in bad:
            for method in (UNITS.render_managed, lambda config: UNITS.check_budget([config], RAM)):
                with self.subTest(value=value, method=method), self.assertRaises(ValueError):
                    method(value)

    def test_format_is_deterministic_and_does_not_mutate_inputs(self):
        config = copy.deepcopy(VALID)
        before = copy.deepcopy(config)
        first = UNITS.render_managed(config)
        self.assertEqual(config, before)
        self.assertEqual(first, UNITS.render_managed(dict(reversed(list(config.items())))))
        network = {**config, 'network': {'mode': 'isolated',
                                       'host_tcp_port': 2222, 'guest_tcp_port': 22}}
        self.assertEqual(UNITS.render_managed(network), first)
        config['memory_max_mib'] = 512
        self.assertEqual(UNITS.render_managed(config), first.replace('MemoryMax=256M', 'MemoryMax=512M'))

    def test_rendering_and_budgeting_perform_no_runtime_io(self):
        # Import-time source loading is separate from pure formatter calls.
        with mock.patch('builtins.open', side_effect=AssertionError('file IO')), \
                mock.patch('os.open', side_effect=AssertionError('descriptor IO')), \
                mock.patch('subprocess.Popen', side_effect=AssertionError('process launch')):
            UNITS.render_managed(VALID)
            UNITS.render_slice(RAM)
            UNITS.check_budget([VALID], RAM)


class BudgetTests(unittest.TestCase):
    def test_slice_floors_physical_memory_and_reserves_host(self):
        text = UNITS.render_slice(RAM + MIB - 1)
        self.assertEqual(directives(text), {'Description': 'A733 managed guests',
                         'MemoryMax': '10240M', 'MemorySwapMax': '0', 'TasksMax': '4096'})
        self.assertNotIn('[Install]', text)
        self.assertEqual(UNITS.render_slice(RAM), text)
        self.assertEqual(directives(UNITS.render_slice(2049 * MIB))['MemoryMax'], '1M')

    def test_rejects_invalid_or_insufficient_physical_memory(self):
        for value in (True, False, None, '12345', 1.0, -1, 0, 2048 * MIB,
                      2048 * MIB + MIB - 1):
            for method in (UNITS.render_slice, lambda size: UNITS.check_budget([], size)):
                with self.subTest(value=value, method=method), self.assertRaises(ValueError):
                    method(value)

    def test_uses_memory_max_not_guest_ram_and_rejects_one_mib_over(self):
        exact = 2304 * MIB
        summary = UNITS.check_budget([VALID], exact + MIB - 1)
        self.assertEqual(dict(summary), {
            'guest_count': 1, 'physical_memory_bytes': exact + MIB - 1,
            'host_reserve_bytes': 2048 * MIB, 'budget_bytes': 256 * MIB,
            'requested_bytes': 256 * MIB, 'remaining_bytes': 0})
        with self.assertRaises(ValueError):
            UNITS.check_budget([VALID], exact - 1)
        with self.assertRaises(ValueError):
            UNITS.check_budget([{**VALID, 'memory_max_mib': 257}], exact)

    def test_identity_uniqueness_is_global_beyond_account_batch_size(self):
        configs = [{**VALID, 'name': 'vm' + str(i), 'uid': 60000 + i,
                    'gid': 60000 + i} for i in range(17)]
        self.assertEqual(UNITS.check_budget(configs, RAM)['guest_count'], 17)
        for duplicate in ({**VALID, 'name': configs[0]['name'], 'uid': 60999, 'gid': 60999},
                          {**VALID, 'name': 'other', 'uid': 60000, 'gid': 60000}):
            with self.subTest(duplicate=duplicate), self.assertRaises(ValueError):
                UNITS.check_budget(configs + [duplicate], RAM)

    def test_guest_slots_are_bounded_and_inputs_are_explicit(self):
        configs = [{**VALID, 'name': 'vm' + str(i), 'uid': 60000 + i,
                    'gid': 60000 + i} for i in range(256)]
        ram = (2048 + 256 * 256) * MIB
        self.assertEqual(UNITS.check_budget(tuple(configs), ram)['guest_count'], 256)
        with self.assertRaises(ValueError):
            UNITS.check_budget(configs + [{**VALID, 'name': 'last', 'uid': 60999, 'gid': 60999}], ram)
        for value in (None, {}, set(), iter(configs), 'one'):
            with self.subTest(value=type(value)), self.assertRaises(ValueError):
                UNITS.check_budget(value, RAM)
        with self.assertRaises(ValueError):
            UNITS.check_budget([{**VALID, 'enabled': False}], RAM)

    def test_summary_is_immutable_detached_and_order_independent(self):
        one = copy.deepcopy(VALID)
        two = {**VALID, 'name': 'two', 'uid': 60002, 'gid': 60002}
        configs = [one, two]
        before = copy.deepcopy(configs)
        summary = UNITS.check_budget(configs, RAM)
        self.assertEqual(summary, UNITS.check_budget(list(reversed(configs)), RAM))
        self.assertEqual(configs, before)
        with self.assertRaises(TypeError):
            summary['requested_bytes'] = 0
        one['memory_max_mib'] = 512
        configs.clear()
        self.assertEqual(summary['requested_bytes'], 512 * MIB)
        empty = UNITS.check_budget([], RAM)
        self.assertEqual(empty['guest_count'], 0)
        self.assertEqual(empty['requested_bytes'], 0)
        self.assertEqual(empty['remaining_bytes'], empty['budget_bytes'])


if __name__ == '__main__':
    unittest.main()
