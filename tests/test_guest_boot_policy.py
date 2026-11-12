"""Native root descriptor fixtures with substituted A733/storage; no hardware claim."""
import importlib.util
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('test_boot_policy',
    Path(__file__).resolve().parents[1] / 'scripts/guest-boot-policy.py')
p = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(p)
BOOT = '11111111-1111-4111-8111-111111111111'
VALUE = {'schema': 1, 'profile': p.SCHEMA.PROFILE, 'target': '/dev/nvme0n1',
         'expected_model': 'Fixture SSD', 'expected_bytes': 250000000000,
         'storage': {'state_uuid': '22222222-2222-4222-8222-222222222222',
                     'guest_uuid': '33333333-3333-4333-8333-333333333333',
                     'nvme_serial': 'SSD-123'}}


@unittest.skipUnless(os.geteuid() == 0, 'actual root-owned private descriptor fixture')
class PolicyTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='boot-policy-', dir='/run')
        self.addCleanup(tmp.cleanup)
        self.state = Path(tmp.name)
        self.config = self.state / 'config'
        self.config.mkdir(mode=0o700)
        self.leaf = self.config / p.NAME
        self.leaf.write_bytes(p.SCHEMA.canonical(VALUE))
        self.leaf.chmod(0o400)
        for patcher in (mock.patch.object(p, 'STATE', str(self.state)),
                        mock.patch.object(p.CONTEXT, 'host_identity'),
                        mock.patch.object(p.MANAGEMENT, 'runtime_guard'),
                        mock.patch.object(p.Policy, '_storage'),
                        mock.patch.object(p.Policy, '_mount_context', return_value={'fixture': 1})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def policy(self):
        result = p.Policy(BOOT)
        self.addCleanup(result.close)
        return result

    def test_detached_value_and_mutable_directory_timestamps(self):
        policy = self.policy()
        value = policy.value
        value['storage']['nvme_serial'] = 'changed'
        self.assertEqual(policy.value, VALUE)
        (self.config / 'unrelated').touch()
        (self.state / 'unrelated').touch()
        policy.check()
        self.assertEqual(p.CONTEXT.host_identity.call_args, mock.call(BOOT))
        self.assertGreaterEqual(p.MANAGEMENT.runtime_guard.call_count, 2)

    def test_native_guard_precedes_any_policy_open(self):
        with mock.patch.object(p.MANAGEMENT, 'runtime_guard', side_effect=ValueError('native')):
            with mock.patch.object(p.Policy, '_open') as held:
                with self.assertRaisesRegex(ValueError, 'native'):
                    p.Policy(BOOT)
                held.assert_not_called()

    def test_missing_native_input_is_not_optional_policy_absence(self):
        with mock.patch.object(p.CONTEXT, 'host_identity', side_effect=FileNotFoundError('board')):
            with self.assertRaisesRegex(ValueError, 'native host/storage input unavailable'):
                p.Policy(BOOT)
        with mock.patch.object(p.Policy, '_storage', side_effect=FileNotFoundError('marker')):
            with self.assertRaisesRegex(ValueError, 'native host/storage input unavailable'):
                p.Policy(BOOT)

    def test_missing_leaf_only(self):
        self.leaf.unlink()
        with self.assertRaises(FileNotFoundError):
            p.Policy(BOOT)
        self.config.rmdir()
        with self.assertRaisesRegex(ValueError, 'config unavailable'):
            p.Policy(BOOT)

    def test_missing_leaf_with_unsafe_parent_refused(self):
        self.leaf.unlink()
        self.config.chmod(0o777)
        with self.assertRaisesRegex(ValueError, 'unsafe'):
            p.Policy(BOOT)

    def test_symlink_and_hardlink_and_mode_refused(self):
        original = self.state / 'original'
        self.leaf.rename(original)
        self.leaf.symlink_to(original)
        with self.assertRaises(OSError):
            p.Policy(BOOT)
        self.leaf.unlink()
        os.link(original, self.leaf)
        with self.assertRaisesRegex(ValueError, 'metadata'):
            p.Policy(BOOT)
        original.unlink()
        self.leaf.chmod(0o600)
        with self.assertRaisesRegex(ValueError, 'metadata'):
            p.Policy(BOOT)

    def test_same_size_byte_change_even_when_metadata_comparison_substituted(self):
        policy = self.policy()
        data = self.leaf.read_bytes().replace(b'Fixture SSD', b'Changed SSD')
        self.assertEqual(len(data), len(policy._bytes))
        self.leaf.write_bytes(data)
        # Pin actual bytes independently of metadata (which also normally fails).
        with mock.patch.object(p.ENROLL, 'identity', return_value=policy._info):
            with self.assertRaisesRegex(ValueError, 'bytes changed'):
                policy.check()

    def test_file_and_directory_replacement_refused(self):
        policy = self.policy()
        self.leaf.rename(self.config / 'old')
        self.leaf.write_bytes(p.SCHEMA.canonical(VALUE))
        self.leaf.chmod(0o400)
        with self.assertRaisesRegex(ValueError, 'named path|metadata'):
            policy.check()
        self.leaf.unlink()
        (self.config / 'old').rename(self.leaf)
        self.config.rename(self.state / 'old-config')
        self.config.mkdir(mode=0o700)
        with self.assertRaisesRegex(ValueError, 'named path'):
            policy.check()

    def test_creator_bound_even_after_close_and_root_drop_cleanup(self):
        policy = self.policy()
        with mock.patch.object(p.os, 'geteuid', return_value=1000):
            policy.close()
        errors = []
        def other_thread():
            try:
                policy.close()
            except ValueError as error:
                errors.append(str(error))
        thread = threading.Thread(target=other_thread)
        thread.start()
        thread.join()
        self.assertEqual(len(errors), 1)
        with mock.patch.object(p.os, 'getpid', return_value=os.getpid() + 1):
            with self.assertRaisesRegex(ValueError, 'creator'):
                policy.close()
        with self.assertRaisesRegex(ValueError, 'closed'):
            policy.check()

    def test_constructor_and_close_attempt_every_descriptor(self):
        baseline = len(os.listdir('/proc/self/fd'))
        with mock.patch.object(p.SCHEMA, 'decode', side_effect=ValueError('bad schema')):
            with self.assertRaisesRegex(ValueError, 'bad schema'):
                p.Policy(BOOT)
        self.assertEqual(len(os.listdir('/proc/self/fd')), baseline)
        policy = self.policy()
        original = os.close
        calls = []
        def close(fd):
            calls.append(fd)
            original(fd)
            raise OSError('cleanup uncertainty')
        count = len(policy._fds)
        with mock.patch.object(p.os, 'close', side_effect=close):
            with self.assertRaises(p.CleanupError) as caught:
                policy.close()
        self.assertEqual(len(calls), count)
        self.assertEqual(len(caught.exception.errors), count)

    def test_primary_retained_when_constructor_cleanup_uncertain(self):
        original = os.close
        def close(fd):
            original(fd)
            raise OSError('uncertain')
        with mock.patch.object(p.SCHEMA, 'decode', side_effect=ValueError('primary')), \
                mock.patch.object(p.os, 'close', side_effect=close):
            with self.assertRaises(p.CleanupError) as caught:
                p.Policy(BOOT)
        self.assertEqual(str(caught.exception.primary), 'primary')
        self.assertGreater(len(caught.exception.errors), 1)

    def test_selected_storage_model_serial_size_sector_and_marker(self):
        policy = self.policy()
        namespace = self.state / 'nvme0n1'
        namespace.mkdir()
        partition = namespace / 'nvme0n1p4'
        partition.mkdir()
        (namespace / 'device').mkdir()
        (namespace / 'queue').mkdir()
        files = {'device/model': b'Fixture SSD\n', 'device/serial': b'SSD-123\n',
                 'queue/logical_block_size': b'512\n',
                 'size': str(VALUE['expected_bytes'] // 512).encode() + b'\n'}
        # Use an exactly sector-aligned fixture size.
        policy._value['expected_bytes'] = (VALUE['expected_bytes'] // 512) * 512
        for name, data in files.items():
            (namespace / name).write_bytes(data)
        marker = self.state / '.a733-state'
        marker.write_text('a733-state-v1\n' + VALUE['storage']['state_uuid'] + '\n')
        marker.chmod(0o600)
        original = Path.resolve
        def resolve(path, **kwargs):
            if str(path) == '/sys/dev/block/259:4':
                return partition
            if str(path) == '/sys/class/block/nvme0n1':
                return namespace
            return original(path, **kwargs)
        row = {'source': '/dev/nvme0n1p4'}
        with mock.patch.object(p.MANAGEMENT, 'validate_mounts', return_value=({}, row)), \
                mock.patch.object(p.MANAGEMENT, 'mount_table'), \
                mock.patch.object(p.MANAGEMENT, 'block_identity', return_value='259:4'), \
                mock.patch.object(Path, 'resolve', resolve):
            check = PolicyTests._real_storage
            check(policy)
            for name, bad in (('device/model', b'Foreign SSD\n'),
                              ('device/serial', b'Foreign-123\n'),
                              ('queue/logical_block_size', b'4096\n'),
                              ('size', b'1\n')):
                with self.subTest(field=name):
                    (namespace / name).write_bytes(bad)
                    with self.assertRaises(ValueError):
                        check(policy)
                    (namespace / name).write_bytes(files[name])
            marker.write_text('a733-state-v1\n' + VALUE['storage']['guest_uuid'] + '\n')
            with self.assertRaisesRegex(ValueError, 'STATE UUID differs'):
                check(policy)
            row['source'] = '/dev/nvme1n1p4'
            with self.assertRaisesRegex(ValueError, 'nvme0n1p4'):
                check(policy)

    def test_mount_id_binding_uses_actual_fdinfo(self):
        policy = self.policy()
        # Retrieve the original implementation behind setUp's substitution.
        original = PolicyTests._real_mount_context
        info = os.fstat(policy._state_fd)
        mount_id = p.STORAGE.fd_mount_id(p.STORAGE.read_bounded(
            '/proc/self/fdinfo/' + str(policy._state_fd), p.STORAGE.FDINFO_LIMIT))
        data = ('1 1 0:1 / / ro - ext4 /dev/nvme0n1p2 ro\n' +
                f'{mount_id} 1 {os.major(info.st_dev)}:{os.minor(info.st_dev)} / '
                f'{self.state} rw,nodev,nosuid - ext4 /dev/nvme0n1p4 rw\n')
        read = p.STORAGE.read_bounded
        def bounded(path, limit):
            return data if str(path) == '/proc/self/mountinfo' else read(path, limit)
        with mock.patch.object(p.STORAGE, 'read_bounded', side_effect=bounded):
            self.assertIn(str(self.state), original(policy))
            data = data.replace(f'{mount_id} 1', f'{mount_id + 1} 1')
            with self.assertRaisesRegex(ValueError, 'mount differs'):
                original(policy)


PolicyTests._real_mount_context = p.Policy._mount_context
PolicyTests._real_storage = p.Policy._storage

if __name__ == '__main__':
    unittest.main()
