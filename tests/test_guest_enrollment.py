"""Enrollment policy and private real-file engine checks; no hardware evidence."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('guest_enrollment', BASE / 'scripts/enroll-guest.py')
E = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(E)
UUID = '7811c43d-01e4-4635-83b4-25d276172ac0'


def account_database():
    name = 'a733-vm-one'
    return {'passwd': f'{name}:x:60001:60001:A733 guest one:/nonexistent:/usr/sbin/nologin\n'.encode(),
            'shadow': f'{name}:!::::::::\n'.encode(),
            'group': f'{name}:x:60001:\n'.encode(), 'gshadow': f'{name}:!::\n'.encode()}


class PolicyTests(unittest.TestCase):
    def info(self, **kwargs):
        value = dict(st_mode=stat.S_IFREG | 0o400, st_uid=0, st_gid=0, st_nlink=1)
        value.update(kwargs)
        return SimpleNamespace(**value)

    def test_root_regular_single_link_nonwritable_contract(self):
        E.regular(self.info(), 0o400)
        for kwargs in ({'st_uid': 60001}, {'st_gid': 60001}, {'st_nlink': 2},
                       {'st_mode': stat.S_IFREG | 0o420}, {'st_mode': stat.S_IFLNK | 0o400},
                       {'st_mode': stat.S_IFREG | 0o440}, {'st_mode': stat.S_IFREG | 0o4400}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                E.regular(self.info(**kwargs), 0o400)

    def test_memory_measurement_and_cpu_refuse(self):
        config = {'cpus': 1, 'memory_max_mib': 192}
        from unittest.mock import mock_open
        for data in (b'MemFree: 900000 kB\n', b'MemAvailable: 1 kB\n',
                     b'MemAvailable: 900000 MB\n', b'MemAvailable: 999999 kB\n' * 2):
            with patch('builtins.open', mock_open(read_data=data)), patch.object(E.os, 'cpu_count', return_value=4):
                with self.assertRaises(ValueError):
                    E.budget(config)
        with patch('builtins.open', mock_open(read_data=b'MemAvailable: 900000 kB\n')), \
                patch.object(E.os, 'cpu_count', return_value=4):
            E.budget(config)
        with patch.object(E.os, 'cpu_count', return_value=None), self.assertRaises(ValueError):
            E.budget(config)

    def test_actual_host_not_assumed_supported(self):
        with self.assertRaises((ValueError, OSError)):
            E.prepare('/not-present', '/not-present', '/not-present', '/not-present', UUID)


@unittest.skipUnless(os.geteuid() == 0, 'real ownership/file engine needs root; run privately with sudo')
class FileEngineTests(unittest.TestCase):
    """Only live provenance is mocked; FD walking, hashing, copy and rename are real."""
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='enrollment-test-', dir='/run')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / 'guests'
        self.root.mkdir(mode=0o711)
        self.state = self.base / 'state'
        self.state.mkdir(mode=0o700)
        self.sources = {}
        self.config = {'schema': 1, 'name': 'one', 'uid': 60001, 'gid': 60001,
                       'memory_mib': 64, 'memory_max_mib': 128, 'cpus': 1}
        for key, data in (('kernel', b'kernel-data'), ('initrd', b'initrd-data'),
                          ('disk', b'\0' * 1024 + b'D' * 512)):
            path = self.base / key
            path.write_bytes(data)
            path.chmod(0o400)
            self.sources[key] = path
            self.config[key] = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
        self.config_path = self.base / 'config.json'
        self.save_config()
        self.plan_path = self.base / 'plan.json'
        self.context = {'root_source': '/dev/nvme0n1p2', 'guest_source': '/dev/nvme0n1p5',
                        'namespace': 'nvme0n1', 'root_device': [259, 2], 'guest_device': [259, 5],
                        'guest_mount_id': 100, 'boot_inputs_checked': 0}
        for mocker in (patch.object(E, 'ROOT', str(self.root)), patch.object(E, 'LOCK', str(self.base / 'lock')),
                       patch.object(E.MANAGEMENT, 'STATE', self.state),
                       patch.object(E.MANAGEMENT, 'runtime_guard'), patch.object(E, 'probe_uuid'),
                       patch.object(E.STORAGE, 'verify_guest_directory', return_value=self.context),
                       patch.object(E.ACCOUNTS, 'snapshot', return_value=account_database()),
                       patch.object(E, 'budget')):
            mocker.start()
            self.addCleanup(mocker.stop)
        # Correct shadow row is exactly nine fields.
        E.ACCOUNTS.snapshot.return_value['shadow'] = b'a733-vm-one:!:::::::\n'

    def save_config(self):
        self.config_path.write_bytes(E.CONFIG.canonical(self.config))
        self.config_path.chmod(0o400)

    def args(self):
        return (str(self.config_path), *(str(self.sources[key]) for key in E.FILENAMES), UUID)

    def plan(self):
        plan = E.prepare(*self.args())
        self.plan_path.write_bytes(E.canonical(plan))
        self.plan_path.chmod(0o400)
        return plan

    def apply(self):
        return E.apply(*self.args(), str(self.plan_path))

    def test_prepare_creates_nothing_and_repeat_is_exact(self):
        before = sorted(self.base.rglob('*'))
        plan = E.prepare(*self.args())
        self.assertEqual(before, sorted(self.base.rglob('*')))
        self.assertEqual(plan, E.prepare(*self.args()))
        self.assertNotIn('MemAvailable', json.dumps(plan))
        self.assertFalse((self.root / '.enrollment').exists())

    def test_publish_permissions_content_and_manifest(self):
        self.plan()
        result = self.apply()
        self.assertTrue(result['success'], result)
        self.assertTrue(result['published'])
        self.assertTrue(result['durable'])
        self.assertEqual(result['bytes_attempted_staged'], sum(self.config[key]['bytes'] for key in E.FILENAMES))
        final = self.root / 'one'
        self.assertEqual((final.stat().st_uid, final.stat().st_gid, stat.S_IMODE(final.stat().st_mode)), (0, 60001, 0o710))
        for key, name in E.FILENAMES.items():
            path = final / name
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), self.config[key]['sha256'])
            expected = (60001, 60001, 0o600) if key == 'disk' else (0, 0, 0o444)
            self.assertEqual((path.stat().st_uid, path.stat().st_gid, stat.S_IMODE(path.stat().st_mode)), expected)
        manifest = final / 'enrollment.json'
        self.assertEqual((manifest.stat().st_uid, manifest.stat().st_gid, stat.S_IMODE(manifest.stat().st_mode)), (0, 0, 0o400))
        self.assertEqual(result['manifest_sha256'], hashlib.sha256(manifest.read_bytes()).hexdigest())
        value = json.loads(manifest.read_bytes())
        self.assertEqual(value['profile'], E.PROFILE)
        self.assertEqual(set(value['files']), set(E.FILENAMES.values()))
        self.assertNotIn('enrollment.json', value['files'])
        self.assertEqual(value['config'], self.config)

    def test_all_source_metadata_checked_before_hash(self):
        self.sources['disk'].chmod(0o620)
        with patch.object(E, 'hash_source') as hashing, self.assertRaises(ValueError):
            E.prepare(*self.args())
        hashing.assert_not_called()

    def test_storage_checked_before_any_input_read(self):
        with patch.object(E.STORAGE, 'verify_guest_directory', side_effect=ValueError('not p5')), \
                patch.object(E, 'read_small') as reading, self.assertRaises(ValueError):
            E.prepare(*self.args())
        reading.assert_not_called()
        with patch.object(E, 'probe_uuid', side_effect=ValueError('UUID mismatch')), \
                patch.object(E, 'read_small') as reading, self.assertRaises(ValueError):
            E.prepare(*self.args())
        reading.assert_not_called()

    def test_source_device_checked_before_hash(self):
        original = E.HeldPath
        def wrong_device(path, **kwargs):
            held = original(path, **kwargs)
            if str(path) == str(self.sources['disk']):
                fields = dict(vars(SimpleNamespace(**{key: getattr(held.info, key) for key in dir(held.info) if key.startswith('st_')})))
                fields['st_dev'] = 987654321
                held.info = SimpleNamespace(**fields)
            return held
        with patch.object(E, 'HeldPath', side_effect=wrong_device), patch.object(E, 'hash_source') as hashing:
            with self.assertRaisesRegex(ValueError, 'selected NVMe'):
                E.prepare(*self.args())
            hashing.assert_not_called()

    def test_guest_owned_source_refused(self):
        os.chown(self.sources['disk'], 60001, 60001)
        self.sources['disk'].chmod(0o600)
        with self.assertRaisesRegex(ValueError, 'metadata'):
            E.prepare(*self.args())

    def test_config_mode_hardlink_symlink_and_unsafe_ancestor(self):
        self.config_path.chmod(0o600)
        with self.assertRaises(ValueError):
            E.prepare(*self.args())
        self.config_path.chmod(0o400)
        linked = self.base / 'linked'
        os.link(self.config_path, linked)
        with self.assertRaises(ValueError):
            E.prepare(*self.args())
        linked.unlink()
        old = self.sources['kernel']
        linked.symlink_to(old)
        self.sources['kernel'] = linked
        with self.assertRaises(OSError):
            E.prepare(*self.args())
        self.sources['kernel'] = old
        self.base.chmod(0o770)
        with self.assertRaises(ValueError):
            E.prepare(*self.args())

    def test_invalid_uuid_and_missing_account_refuse(self):
        for value in (UUID.upper(), '00000000-0000-0000-0000-000000000000', 'bad'):
            with self.assertRaises(ValueError):
                E.prepare(*self.args()[:-1], value)
        E.ACCOUNTS.snapshot.return_value = {'passwd': b'root:x:0:0::/:/bin/bash\n',
            'shadow': b'root:!:::::::\n', 'group': b'root:x:0:\n', 'gshadow': b'root:!::\n'}
        with self.assertRaisesRegex(ValueError, 'missing'):
            E.prepare(*self.args())

    def test_no_replace_existing_any_object(self):
        self.plan()
        target = self.root / 'one'
        for kind in ('regular', 'directory', 'symlink'):
            if kind == 'regular':
                target.write_bytes(b'sentinel')
            elif kind == 'directory':
                target.mkdir()
            else:
                target.symlink_to('/unavailable')
            self.assertFalse(self.apply()['success'])
            if kind == 'directory':
                target.rmdir()
            else:
                target.unlink()

    def test_low_space_precedes_hash_and_creation(self):
        with patch.object(E.os, 'fstatvfs', return_value=SimpleNamespace(f_bavail=1, f_frsize=4096)), \
                patch.object(E, 'hash_source') as hashing, self.assertRaisesRegex(ValueError, 'space'):
            E.prepare(*self.args())
        hashing.assert_not_called()
        self.assertFalse((self.root / '.enrollment').exists())

    def test_stale_plan_and_source_replacement_refuse(self):
        self.plan()
        path = self.sources['kernel']
        content = path.read_bytes()
        path.unlink()
        path.write_bytes(content)
        path.chmod(0o400)
        result = self.apply()
        self.assertFalse(result['success'])
        self.assertFalse((self.root / 'one').exists())

    def test_source_growth_during_copy_and_readback_hash_failure(self):
        self.plan()
        original = E.copy_source
        def grow(source, fd, expected, receipt):
            if source.path == str(self.sources['disk']):
                with open(source.path, 'ab') as stream:
                    stream.write(b'extra')
            return original(source, fd, expected, receipt)
        with patch.object(E, 'copy_source', side_effect=grow):
            self.assertFalse(self.apply()['success'])
        self.assertFalse((self.root / 'one').exists())
        self.assertEqual(list((self.root / '.enrollment').iterdir()), [])

    def test_copy_independent_readback_failure(self):
        self.plan()
        original = E.write_all
        def corrupt(fd, data):
            original(fd, b'X' * len(data))
        with patch.object(E, 'write_all', side_effect=corrupt):
            result = self.apply()
        self.assertFalse(result['success'])
        self.assertFalse(result['published'])
        self.assertEqual(list((self.root / '.enrollment').iterdir()), [])

    def test_rename_completed_then_interrupt_preserves_published(self):
        self.plan()
        original = E.rename_new
        def interrupted(*args):
            original(*args)
            raise KeyboardInterrupt
        with patch.object(E, 'rename_new', side_effect=interrupted):
            result = self.apply()
        self.assertFalse(result['success'])
        self.assertTrue(result['published'])
        self.assertFalse(result['durable'])
        self.assertTrue((self.root / 'one' / 'disk.raw').exists())

    def test_parent_fsync_failure_preserves_published(self):
        self.plan()
        original = E.os.fsync
        def fail(fd):
            if E.inode(os.fstat(fd)) == E.inode(self.root.stat()):
                raise OSError('parent fsync failure')
            original(fd)
        with patch.object(E.os, 'fsync', side_effect=fail):
            result = self.apply()
        self.assertFalse(result['success'])
        self.assertTrue(result['published'])
        self.assertFalse(result['durable'])
        self.assertTrue((self.root / 'one' / 'disk.raw').exists())

    def test_queued_sigterm_after_publish_preserves_flags(self):
        self.plan()
        original = E.rename_new
        previous = signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
        try:
            def queued(*args):
                original(*args)
                os.kill(os.getpid(), signal.SIGTERM)
            with patch.object(E, 'rename_new', side_effect=queued):
                result = self.apply()
        finally:
            signal.signal(signal.SIGTERM, previous)
        self.assertFalse(result['success'])
        self.assertTrue(result['published'])
        self.assertTrue(result['durable'])
        self.assertTrue((self.root / 'one' / 'disk.raw').exists())

    def test_racing_target_never_overwritten(self):
        self.plan()
        original = E.rename_new
        def race(*args):
            (self.root / 'one').write_bytes(b'foreign')
            original(*args)
        with patch.object(E, 'rename_new', side_effect=race):
            result = self.apply()
        self.assertFalse(result['success'])
        self.assertFalse(result['published'])
        self.assertEqual((self.root / 'one').read_bytes(), b'foreign')

    def test_foreign_staged_entries_retained_during_cleanup(self):
        self.plan()
        def foreign(*args):
            stage = next((self.root / '.enrollment').iterdir())
            (stage / 'foreign').write_bytes(b'keep')
            raise ValueError('stop before publication')
        with patch.object(E.Review, 'recheck', side_effect=foreign):
            result = self.apply()
        self.assertFalse(result['success'])
        self.assertTrue(result['staging_retained'])
        stages = list((self.root / '.enrollment').iterdir())
        self.assertEqual(len(stages), 1)
        self.assertEqual([path.name for path in stages[0].iterdir()], ['foreign'])

    def test_parent_mode_and_storage_changes_before_publish(self):
        self.plan()
        original = E.Review.recheck
        def changed(review):
            self.root.chmod(0o777)
            original(review)
        with patch.object(E.Review, 'recheck', changed):
            self.assertFalse(self.apply()['success'])
        self.assertFalse((self.root / 'one').exists())


if __name__ == '__main__':
    unittest.main()
