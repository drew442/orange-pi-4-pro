"""Packaged service boundaries; no installed PID1 or physical mount proof."""
import configparser
import hashlib
import importlib.util
from pathlib import Path
import shlex
import tempfile
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
def module(name, relative):
    spec = importlib.util.spec_from_file_location(name, BASE / relative)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result

PAYLOAD = module('guest_service_payload', 'build/host-payload.py')
MANAGEMENT = module('guest_service_management', 'configs/host-management.py')
NAMES = ('a733-guest-storage.service', 'a733-guest-prepare.service')
CONFIG = {'schema': 1, 'hostname': 'a733-host', 'management_mac': '02:73:30:00:00:01',
          'management_cidr': '192.0.2.0/24', 'dhcp': True}


def unit(name):
    value = configparser.ConfigParser(interpolation=None)
    value.optionxform = str
    value.read_string((BASE / 'configs' / name).read_text())
    return value


class GuestServicePackagingTests(unittest.TestCase):
    def test_new_root_installs_exact_services_and_records_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            management = PAYLOAD.install_management(root)
            records = PAYLOAD.install_guest_boot(root)['files']
            self.assertEqual(len(management['files']), 4)
            for name in NAMES:
                relative = 'usr/lib/systemd/system/' + name
                expected = (BASE / 'configs' / name).read_bytes()
                self.assertEqual((root / relative).read_bytes(), expected)
                self.assertEqual((root / relative).stat().st_mode & 0o777, 0o644)
                self.assertEqual(records[relative]['sha256'], hashlib.sha256(expected).hexdigest())
            with self.assertRaisesRegex(ValueError, 'already exists'):
                PAYLOAD.install_guest_boot(root)

    def test_ram_wants_keep_management_independent_and_run_no_commands(self):
        with patch.object(MANAGEMENT, 'runtime_guard', return_value=CONFIG), \
             patch.object(MANAGEMENT, 'guest_management_profile', return_value='manual-beta'), \
             patch.object(MANAGEMENT, 'prepare_clock_state'), \
             patch.object(MANAGEMENT, 'ram_directory'), patch.object(MANAGEMENT, 'atomic_write'), \
             patch.object(MANAGEMENT.os, 'chmod'), patch.object(MANAGEMENT, 'ram_link') as link, \
             patch.object(MANAGEMENT.subprocess, 'run') as command:
            MANAGEMENT.prepare()
        links = dict(call.args for call in link.call_args_list)
        for name in ('a733-guest-storage.service', 'a733-management-ssh.service', 'systemd-networkd.service'):
            self.assertEqual(links['/etc/systemd/system/multi-user.target.wants/' + name],
                             '/lib/systemd/system/' + name)
        self.assertEqual(links['/etc/systemd/system/a733-guest-prepare.service'], '/dev/null')
        self.assertEqual(links['/etc/systemd/system/multi-user.target.wants/a733-guest-prepare.service'], '/dev/null')
        self.assertFalse(any('a733-managed-' in target for target in links))
        command.assert_not_called()
        for name in NAMES:
            value = unit(name)
            for key in ('After', 'Before', 'Requires', 'Wants'):
                self.assertNotIn('a733-management-ssh.service', value['Unit'].get(key, ''))
            self.assertEqual(value['Unit']['ConditionPathExists'],
                             '/run/a733-state/config/guest-storage.json')

    def test_storage_prepares_before_dispatch_and_stops_before_mount_teardown(self):
        value = unit(NAMES[0])
        self.assertEqual(value['Service']['Type'], 'oneshot')
        self.assertEqual(value['Service']['RemainAfterExit'], 'yes')
        self.assertEqual(shlex.split(value['Service']['ExecStart']), ['/usr/bin/python3', '-I', '-B',
                         '/usr/lib/a733-tools/scripts/prepare-guest-boot.py'])
        self.assertNotIn('ExecStop', value['Service'])
        self.assertEqual(shlex.split(value['Service']['ExecStopPost']), ['/usr/bin/python3', '-I', '-B',
                         '/usr/lib/a733-tools/scripts/stop-guest-storage.py'])
        self.assertEqual(value['Unit']['RequiresMountsFor'], '/run/a733-state')
        self.assertIn(r'run-a733\x2dguests.mount', value['Unit']['After'].split())
        self.assertIn('umount.target', value['Unit']['Before'].split())
        self.assertGreaterEqual(int(value['Service']['TimeoutStopSec']), 420 + 30 + 10)

    def test_dispatch_waits_for_successful_storage_without_starting_guests_directly(self):
        value = unit(NAMES[1])
        self.assertEqual(value['Unit']['After'], NAMES[0])
        self.assertEqual(value['Unit']['Requires'], NAMES[0])
        self.assertEqual(value['Unit']['RequiresMountsFor'], '/run/a733-state /run/a733-guests')
        self.assertEqual(shlex.split(value['Service']['ExecStart']), ['/usr/bin/python3', '-I', '-B',
                         '/usr/lib/a733-tools/scripts/dispatch-guest-boot.py'])
        self.assertNotIn('ExecStop', value['Service'])

    def test_services_retain_native_namespaces_and_finite_resources(self):
        for name in NAMES:
            value = unit(name)
            self.assertEqual(set(value.sections()), {'Unit', 'Service'})
            service = value['Service']
            self.assertEqual(service['User'], 'root')
            self.assertEqual(service['Group'], 'root')
            self.assertEqual(service['NoNewPrivileges'], 'yes')
            self.assertEqual(service['Restart'], 'no')
            self.assertEqual(service['MemorySwapMax'], '0')
            self.assertEqual(service['MemoryMax'], '512M')
            self.assertEqual(service['TasksMax'], '512')
            self.assertLessEqual(int(service['TimeoutStartSec']), 300)
            for key in ('PrivateMounts', 'PrivateTmp', 'PrivateDevices', 'PrivateUsers',
                        'ProtectSystem', 'RootDirectory', 'RootImage'):
                self.assertNotIn(key, service)


if __name__ == '__main__':
    unittest.main()
