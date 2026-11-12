"""Host-only read-only authority fixtures; no physical storage/KVM claim."""
import fcntl
import importlib.util
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location(
    'test_lifecycle_context', Path(__file__).resolve().parents[1] /
    'scripts/guest-lifecycle-context.py')
c = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(c)
BOOT = '11111111-1111-4111-8111-111111111111'
STORAGE = {'state_uuid': '22222222-2222-4222-8222-222222222222',
           'guest_uuid': '33333333-3333-4333-8333-333333333333', 'nvme_serial': 'SSD-123'}
FSTAT, STAT = os.fstat, os.stat


def root_info(info):
    values = {name: getattr(info, name) for name in dir(info) if name.startswith('st_')}
    values.update(st_uid=0, st_gid=0)
    return SimpleNamespace(**values)


class AdmissionModuleTests(unittest.TestCase):
    def test_context_and_activation_share_exact_capability_classes(self):
        second = c.module('second_context', 'scripts/guest-lifecycle-context.py')
        activation = c.module('second_activation', 'scripts/activate-guest-storage.py')
        foreign = c.module('foreign_admission', 'scripts/guest-checkpoint-policy.py')
        self.assertIs(second.ADMISSION, c.ADMISSION)
        self.assertIs(activation.ADMISSION, c.ADMISSION)
        self.assertIs(second.ADMISSION.Lease, c.ADMISSION.Lease)
        self.assertIsNot(foreign, c.POLICY)
        spec = importlib.util.spec_from_file_location('direct_foreign',
            c.BASE / 'scripts/guest-admission.py')
        direct = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(direct)
        with self.assertRaisesRegex(ValueError, 'typed'):
            c.HostContext(STORAGE, direct.Lease(None, None), boot_id=BOOT)

    def test_cache_origin_revision_and_failed_execution(self):
        key = 'a733_runtime_guest_admission'
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(sys.modules), \
                mock.patch.object(c, 'BASE', Path(tmp)):
            path = Path(tmp) / 'scripts/guest-admission.py'
            path.parent.mkdir()
            path.write_text('class Lease: pass\n')
            # A different root tree cannot consume an already cached authority.
            with self.assertRaisesRegex(RuntimeError, 'origin or source'):
                c.module('ignored', 'scripts/guest-admission.py')
            del sys.modules[key]
            first = c.module('ignored', 'scripts/guest-admission.py')
            self.assertIs(c.module('other', 'scripts/guest-admission.py'), first)
            path.write_text('class Lease: pass\n# changed\n')
            with self.assertRaisesRegex(RuntimeError, 'origin or source'):
                c.module('ignored', 'scripts/guest-admission.py')
            del sys.modules[key]
            path.write_text('raise RuntimeError("broken source")\n')
            with self.assertRaisesRegex(RuntimeError, 'broken source'):
                c.module('ignored', 'scripts/guest-admission.py')
            self.assertNotIn(key, sys.modules)


class ContextTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.state, self.guests = self.base / 'state', self.base / 'guests'
        self.state.mkdir(mode=0o700)
        self.guests.mkdir(mode=0o711)
        self.marker = self.state / '.a733-state'
        self.marker.write_text('a733-state-v1\n' + STORAGE['state_uuid'] + '\n')
        self.marker.chmod(0o644)
        self.held = []
        fixture = self

        class Held:
            def __init__(self, path, *, directory):
                fixture.assertTrue(directory)
                self.path = path
                self.fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                self.info = os.fstat(self.fd)
                self.original = c.ENROLL.inode(self.info)
                fixture.held.append(self)

            def recheck(self):
                c.require(c.ENROLL.inode(os.stat(self.path, follow_symlinks=False)) ==
                          self.original, 'named path changed')

            def close(self):
                os.close(self.fd)

        self.namespace = {name: value for name, value in c.INITIAL_NAMESPACES.items()}
        self.namespace['mnt'] = 500
        self.init_namespace = self.namespace.copy()
        self.system = {'/proc/sys/kernel/random/boot_id': (BOOT + '\n').encode(),
                       '/proc/device-tree/compatible': c.COMPATIBLE,
                       '/proc/self/status': ('NSpid:\t' + str(os.getpid()) + '\n').encode(),
                       '/sys/class/block/nvme0n1/device/serial': b'SSD-123\n'}
        self.placement = {'namespace': 'nvme0n1', 'guest_source': '/dev/nvme0n1p5',
                          'guest_device': [259, 5], 'guest_mount_id': 30}
        self.mount_generation = 0
        self.lease = c.ADMISSION.Lease(SimpleNamespace(boot_id=BOOT), 999)
        self.lease_check = mock.Mock(side_effect=lambda: c.require(
            self.lease.fd is not None, 'lease closed'))
        self.probe = mock.Mock()
        self.runtime_guard = mock.Mock()
        patches = [mock.patch.object(c, 'STATE', str(self.state)),
                   mock.patch.object(c, 'GUESTS', str(self.guests)),
                   mock.patch.object(c.ENROLL, 'HeldPath', Held),
                   mock.patch.object(c.os, 'geteuid', return_value=0),
                   mock.patch.object(c.os, 'fstat', side_effect=lambda fd: root_info(FSTAT(fd))),
                   mock.patch.object(c.os, 'stat', side_effect=self.stat),
                   mock.patch.object(c, 'read_system', side_effect=lambda p, n: self.system[p]),
                   mock.patch.object(c.MANAGEMENT, 'runtime_guard', self.runtime_guard),
                   mock.patch.object(c.STORAGE, 'verify_guest_directory',
                                     side_effect=lambda fd: self.placement.copy()),
                   mock.patch.object(c.STORAGE, 'read_bounded', side_effect=self.mount_input),
                   mock.patch.object(self.lease, 'recheck', self.lease_check),
                   mock.patch.object(c.ENROLL, 'probe_uuid', self.probe)]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def stat(self, path, *args, **kwargs):
        path = str(path)
        for prefix, table in (('/proc/self/ns/', self.namespace),
                              ('/proc/1/ns/', self.init_namespace)):
            if path.startswith(prefix):
                return SimpleNamespace(st_dev=4, st_ino=table[path.removeprefix(prefix)])
        return root_info(STAT(path, *args, **kwargs))

    def mount_input(self, path, limit):
        if '/fdinfo/' in str(path):
            fd = int(str(path).rsplit('/', 1)[1])
            target = os.readlink('/proc/self/fd/' + str(fd))
            return 'mnt_id:\t' + ('20' if target == str(self.state) else '30') + '\n'
        dev = FSTAT(self.held[0].fd).st_dev
        identity = str(os.major(dev)) + ':' + str(os.minor(dev))
        return ('10 1 259:2 / / ro - ext4 /dev/nvme0n1p2 ro\n' +
                f'20 10 {identity} / {self.state} rw - ext4 /dev/nvme0n1p4 rw\n' +
                f'30 10 {identity} / {self.guests} rw,nodev,nosuid,noexec - ext4 '
                f'/dev/nvme0n1p5 rw{",sync" if self.mount_generation else ""}\n')

    def context(self):
        return c.HostContext(STORAGE, self.lease, boot_id=BOOT)

    def assert_released(self):
        for item in self.held:
            with self.assertRaises(OSError):
                FSTAT(item.fd)

    def test_readonly_borrowed_lease_independent_storage_and_probe_budget(self):
        with self.context() as context:
            for fd in (context.state_fd, context.guest_fd):
                self.assertEqual(fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE, os.O_RDONLY)
            context.storage['nvme_serial'] = 'other'
            self.assertEqual(context.storage, STORAGE)
            context.guard()
            self.assertEqual(self.probe.call_count, 1)
            context.verify()
            self.assertEqual(self.probe.call_count, 2)
        self.assertEqual(self.probe.call_count, 3)
        self.assertEqual(self.lease.fd, 999)
        self.assert_released()
        context.close()
        with self.assertRaisesRegex(ValueError, 'closed'):
            context.guard()

    def test_strict_storage_and_exact_capability_class(self):
        for storage in (None, {**STORAGE, 'extra': 1}, {**STORAGE, 'nvme_serial': 'x y'},
                        {**STORAGE, 'guest_uuid': '0' * 36}):
            with self.assertRaises((ValueError, TypeError)):
                c.HostContext(storage, self.lease, boot_id=BOOT)
        with self.assertRaisesRegex(ValueError, 'typed'):
            c.HostContext(STORAGE, SimpleNamespace(recheck=lambda: None), boot_id=BOOT)
        self.assertFalse(self.held)

    def test_live_identity_refusals(self):
        cases = [('boot', lambda: self.system.update({'/proc/sys/kernel/random/boot_id': b'wrong\n'})),
                 ('lease boot', lambda: setattr(self.lease.admission, 'boot_id', STORAGE['state_uuid'])),
                 ('namespace', lambda: self.init_namespace.update(mnt=600)),
                 ('initial', lambda: (self.namespace.update(pid=600), self.init_namespace.update(pid=600))),
                 ('nested', lambda: self.system.update({'/proc/self/status': b'NSpid:\t1\t2\n'})),
                 ('board', lambda: self.system.update({'/proc/device-tree/compatible': b'other\0'}))]
        for name, mutate in cases:
            with self.subTest(name=name):
                original_system = self.system.copy()
                original_ns, original_init = self.namespace.copy(), self.init_namespace.copy()
                mutate()
                with self.assertRaises(ValueError):
                    self.context()
                self.system = original_system
                self.namespace, self.init_namespace = original_ns, original_init
                self.lease.admission.boot_id = BOOT
        self.assertFalse(self.held)

    def test_root_privilege_required(self):
        with mock.patch.object(c.os, 'geteuid', return_value=1000):
            with self.assertRaisesRegex(ValueError, 'root'):
                self.context()

    def test_constructor_state_serial_metadata_probe_failures_clean_up(self):
        for kind in ('marker', 'serial', 'mode', 'probe', 'runtime'):
            with self.subTest(kind=kind):
                self.held.clear()
                if kind == 'marker':
                    self.marker.write_bytes(b'wrong\n')
                elif kind == 'serial':
                    self.system['/sys/class/block/nvme0n1/device/serial'] = b'other\n'
                elif kind == 'mode':
                    self.guests.chmod(0o777)
                elif kind == 'probe':
                    self.probe.side_effect = ValueError('UUID differs')
                else:
                    self.runtime_guard.side_effect = ValueError('pairing differs')
                with self.assertRaises(ValueError):
                    self.context()
                self.assert_released()
                self.marker.write_text('a733-state-v1\n' + STORAGE['state_uuid'] + '\n')
                self.system['/sys/class/block/nvme0n1/device/serial'] = b'SSD-123\n'
                self.guests.chmod(0o711)
                self.probe.side_effect = self.runtime_guard.side_effect = None

    def test_guard_refuses_mount_and_placement_change(self):
        context = self.context()
        self.mount_generation = 1
        with self.assertRaisesRegex(ValueError, 'mount context'):
            context.guard()
        self.mount_generation = 0
        self.placement['guest_source'] = '/dev/nvme1n1p5'
        with self.assertRaisesRegex(ValueError, 'placement'):
            context.close()
        self.assert_released()

    def test_closed_lease_and_final_verification_failure_release_descriptors(self):
        context = self.context()
        self.lease.fd = None
        with self.assertRaisesRegex(ValueError, 'lease closed'):
            context.close()
        self.assert_released()
        self.held.clear()
        self.lease.fd = 999
        context = self.context()
        self.probe.side_effect = ValueError('final UUID differs')
        with self.assertRaisesRegex(ValueError, 'final UUID'):
            context.close()
        self.assert_released()

    def test_named_root_replacement_is_refused_even_with_same_metadata(self):
        context = self.context()
        self.guests.rename(self.base / 'old-guests')
        self.guests.mkdir(mode=0o711)
        with self.assertRaisesRegex(ValueError, 'named path'):
            context.close()
        self.assert_released()

    def test_enter_failure_releases_and_closed_context_cannot_reenter(self):
        context = self.context()
        self.lease.fd = None
        with self.assertRaisesRegex(ValueError, 'lease closed'):
            context.__enter__()
        self.assert_released()
        with self.assertRaisesRegex(ValueError, 'closed'):
            context.__enter__()

    def test_marker_symlink_and_unsafe_metadata_refused(self):
        for kind in ('symlink', 'mode', 'hardlink'):
            with self.subTest(kind=kind):
                if kind == 'symlink':
                    self.marker.rename(self.base / 'marker-target')
                    self.marker.symlink_to(self.base / 'marker-target')
                elif kind == 'mode':
                    self.marker.chmod(0o666)
                else:
                    os.link(self.marker, self.base / 'marker-hardlink')
                with self.assertRaises((OSError, ValueError)):
                    self.context()
                self.assert_released()
                self.held.clear()
                self.marker.unlink()
                self.marker.write_text('a733-state-v1\n' + STORAGE['state_uuid'] + '\n')
                self.marker.chmod(0o644)

    def test_unrelated_directory_timestamp_change_allowed(self):
        with self.context() as context:
            (self.guests / 'unrelated').mkdir()
            context.guard()


if __name__ == '__main__':
    unittest.main()
