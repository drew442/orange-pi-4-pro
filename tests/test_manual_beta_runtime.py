"""Manual beta boundaries; no real mounts, services, devices or KVM required."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


M = load('manual_beta_management', 'configs/host-management.py')
P = load('manual_beta_payload', 'build/host-payload.py')
D = load('manual_beta_dispatch', 'scripts/dispatch-guest-boot.py')
C = load('manual_beta_controls', 'scripts/manage-guests.py')
L = load('manual_beta_lifecycle', 'scripts/guest-lifecycle.py')
CONFIG = dict(schema=1, hostname='beta', management_mac='02:73:30:00:00:01',
              management_cidr='192.0.2.0/24', dhcp=True)


class ManualBeta(unittest.TestCase):
    def test_closed_default_and_explicit_profile(self):
        self.assertEqual(M.CONFIG.guest_management_profile({}), 'manual-beta')
        for value in M.CONFIG.GUEST_MANAGEMENT_PROFILES:
            self.assertEqual(M.CONFIG.guest_management_profile({'guest_management_profile': value}), value)
        for value in (None, True, [], {}, 'managed', 'MANUAL-BETA'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                M.CONFIG.guest_management_profile({'guest_management_profile': value})

    def test_profile_uses_secure_fixed_payload_and_rejects_duplicate_authority(self):
        with patch.object(M, 'read_secure', return_value=b'{}') as read:
            self.assertEqual(M.guest_management_profile(), 'manual-beta')
            read.assert_called_once_with('/usr/lib/orangepi-hypervisor/payload.json', 1024 * 1024)
        with patch.object(M, 'read_secure', return_value=(
                b'{"guest_management_profile":"manual-beta",'
                b'"guest_management_profile":"managed-experimental"}')):
            with self.assertRaisesRegex(ValueError, 'duplicate'):
                M.guest_management_profile()

    def test_root_reader_binds_profile_but_accepts_legacy_manual_descriptor(self):
        P.validate_guest_boot_bindings({}, {'guest_management_profile': 'manual-beta'})
        with self.assertRaisesRegex(ValueError, 'guest management profile differs'):
            P.validate_guest_boot_bindings({}, {'guest_management_profile': 'managed-experimental'})
        with self.assertRaisesRegex(ValueError, 'unknown guest management profile'):
            P.validate_guest_boot_runtime({'guest_management_profile': 'typo'})

    def test_host_prepares_shared_storage_but_masks_only_dispatch_by_default(self):
        for profile in M.CONFIG.GUEST_MANAGEMENT_PROFILES:
            with self.subTest(profile=profile), \
                    patch.object(M, 'runtime_guard', return_value=CONFIG), \
                    patch.object(M, 'guest_management_profile', return_value=profile), \
                    patch.object(M, 'prepare_clock_state'), patch.object(M.os, 'chmod'), \
                    patch.object(M, 'ram_directory'), patch.object(M, 'atomic_write'), \
                    patch.object(M, 'ram_link') as link:
                M.prepare()
                links = dict(call.args for call in link.call_args_list)
                wants = '/etc/systemd/system/multi-user.target.wants/'
                self.assertEqual(links[wants+'a733-guest-storage.service'],
                                 '/lib/systemd/system/a733-guest-storage.service')
                dispatch = 'a733-guest-prepare.service'
                if profile == 'manual-beta':
                    self.assertEqual(links['/etc/systemd/system/'+dispatch], '/dev/null')
                    self.assertEqual(links[wants+dispatch], '/dev/null')
                else:
                    self.assertEqual(links[wants+dispatch], '/lib/systemd/system/'+dispatch)
                    self.assertNotIn('/etc/systemd/system/'+dispatch, links)
                self.assertNotIn('/etc/systemd/system/a733-guest-storage.service', links)

    def test_manual_rejects_dispatch_before_admission_or_publication(self):
        with patch.object(D.PREP.CONTEXT.MANAGEMENT, 'guest_management_profile', return_value='manual-beta'), \
                patch.object(D.A, '_boot_id') as boot, patch.object(D, 'command') as command:
            result = D.dispatch()
            self.assertFalse(result['success'])
            self.assertIn('parked in manual-beta', result['error'])
            self.assertFalse(result['intent_published'])
            self.assertFalse(result['queue_attempted'])
            boot.assert_not_called()
            command.assert_not_called()

    def test_manual_rejects_managed_controls_before_storage_or_admission(self):
        for op in ('enroll', 'enable', 'start'):
            with self.subTest(operation=op), \
                    patch.object(C.P.CONTEXT.MANAGEMENT, 'guest_management_profile', return_value='manual-beta'), \
                    patch.object(C.A, '_boot_id') as boot:
                result = C.manage(op, 'testguest', 'a'*64 if op != 'start' else None)
                self.assertFalse(result['success'])
                self.assertIn('parked in manual-beta', result['error'])
                self.assertFalse(result['queue_attempted'])
                boot.assert_not_called()

    def test_direct_lifecycle_api_cannot_bypass_manual_boundary(self):
        controller = object.__new__(L.StateController)
        controller.boot_id = '11111111-1111-4111-8111-111111111111'
        with patch.object(L.CONTEXT.MANAGEMENT, 'guest_management_profile', return_value='manual-beta'):
            for call in (lambda: controller.enroll('testguest', 'a'*64),
                         lambda: controller.enable('testguest', 'a'*64),
                         lambda: controller.admit_run('testguest', 'a'*64, controller.boot_id)):
                with self.assertRaisesRegex(L.OperationError, 'parked in manual-beta'):
                    call()

    def test_experimental_opt_in_remains_available(self):
        with patch.object(M, 'guest_management_profile', return_value='managed-experimental'):
            M.require_managed_runtime()
        with patch.object(M, 'guest_management_profile', return_value='manual-beta'):
            with self.assertRaisesRegex(ValueError, 'parked in manual-beta'):
                M.require_managed_runtime()


if __name__ == '__main__':
    unittest.main()
