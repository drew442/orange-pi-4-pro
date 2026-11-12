"""Enrollment policy and private real-file engine checks; no hardware evidence."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import uuid
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('guest_enrollment', BASE / 'scripts/enroll-guest.py')
E = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(E)
O = E.operation_module()
A = O.ADMISSION
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

    def test_actual_nonroot_refuses_before_config_or_runtime_reads(self):
        code = """
import importlib.util, os, sys
spec = importlib.util.spec_from_file_location('enroll', sys.argv[1])
e = importlib.util.module_from_spec(spec)
spec.loader.exec_module(e)
if os.geteuid() == 0:
    os.setgroups([])
    os.setgid(65534)
    os.setuid(65534)
def forbidden(*args):
    raise AssertionError('runtime read before root check')
e.MANAGEMENT.runtime_guard = forbidden
try:
    e.prepare('/absent', '/absent', '/absent', '/absent', sys.argv[2])
except ValueError as error:
    assert 'actual root' in str(error), str(error)
else:
    raise AssertionError('nonroot prepare admitted')
result = e.apply('/absent', '/absent', '/absent', '/absent', sys.argv[2], '/absent')
assert result['success'] is False and result['bytes_attempted_staged'] == 0, result
"""
        result = subprocess.run([sys.executable, '-c', code, SPEC.origin, UUID],
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr.decode())

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
        self.boot = str(uuid.uuid4())
        self.admission_path = self.base / 'a733-admission'
        for mocker in (patch.object(A, 'RUNTIME_PARENT', str(self.base)),
                       patch.object(A, 'RUNTIME', str(self.admission_path)),
                       patch.object(O.CONTEXT, 'read_system', return_value=(self.boot + '\n').encode()),
                       patch.object(O.CONTEXT, 'host_identity'),
                       patch.object(O.CONTEXT.MANAGEMENT, 'runtime_guard'),
                       patch.object(O.ACCOUNTS, 'snapshot',
                                    return_value={'group': b'root:x:0:\nkvm:x:108:\n'}),
                       patch.object(O, 'manager_state', return_value='running')):
            mocker.start()
            self.addCleanup(mocker.stop)
        self.admission = A.initialize(self.boot, 108, lambda: 'running')
        self.addCleanup(self.admission.close)
        owner = self.admission.bootstrap_owner
        self.admission.prepare_managed(owner.lease)
        with self.admission.guest_lock('one', owner.lease, create=True):
            pass
        owner.open()
        owner.lease.close()
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

    def assert_operation_held(self):
        operation = O._ACTIVE.operation
        self.assertEqual(operation.name, 'one')
        operation.recheck()
        with self.admission.admit() as lease:
            with self.assertRaises(A.AdmissionError):
                self.admission.guest_lock('one', lease)
        return operation

    def test_prepare_creates_stable_lock_and_hashes_payloads_once_under_admission(self):
        (self.admission_path / 'guests' / 'one.lock').unlink()
        original = E.hash_source
        calls = []
        def hashing(*args):
            self.assert_operation_held()
            self.assertFalse(Path(E.LOCK).exists())
            calls.append(args[0].path)
            return original(*args)
        with patch.object(E, 'hash_source', side_effect=hashing):
            E.prepare(*self.args())
        self.assertCountEqual(calls, [str(path) for path in self.sources.values()])
        self.assertTrue((self.admission_path / 'guests' / 'one.lock').exists())
        self.assertIsNone(O._ACTIVE.operation)

    def test_closed_gate_and_managed_name_refuse_before_review_or_subsystem_lock(self):
        self.plan()
        with self.admission.admit() as lease, self.admission.guest_lock('one', lease) as guest:
            self.admission.mark_managed('one', lease, guest, guard=lambda: None)
        for closed in (False, True):
            if closed:
                self.admission.close_gate('shutdown')
            with self.subTest(closed=closed), patch.object(E, 'Review') as review:
                with self.assertRaises(A.AdmissionError):
                    E.prepare(*self.args())
                result = self.apply()
                self.assertFalse(result['success'])
                self.assertEqual(result['bytes_attempted_staged'], 0)
                review.assert_not_called()
                self.assertFalse(Path(E.LOCK).exists())

    def test_conflicting_guest_flock_precedes_global_lock_and_payload_reads(self):
        self.plan()
        with self.admission.admit() as lease, self.admission.guest_lock('one', lease), \
                patch.object(E, 'Review') as review:
            with self.assertRaises(A.AdmissionError):
                E.prepare(*self.args())
            result = self.apply()
            self.assertFalse(result['success'])
            self.assertEqual(result['bytes_attempted_staged'], 0)
            self.assertFalse(Path(E.LOCK).exists())
            review.assert_not_called()
        # Stable exclusion is per guest; another guest's lock does not block one.
        with self.admission.admit() as lease, self.admission.guest_lock('two', lease, create=True):
            self.assertTrue(self.apply()['success'])

    def test_apply_lock_order_and_admission_lifetime_through_cleanup(self):
        self.plan()
        native_open, native_copy = E.os.open, E.copy_source
        native_close, native_operation_close = E.HeldPath.close, O.ManualGuestOperation.close
        events = []
        def opening(path, *args, **kwargs):
            if str(path) == E.LOCK:
                self.assert_operation_held()
                events.append('subsystem')
            return native_open(path, *args, **kwargs)
        def copying(*args):
            self.assert_operation_held()
            events.append('copy')
            return native_copy(*args)
        def closing(held):
            self.assert_operation_held()
            events.append('input-close')
            return native_close(held)
        def operation_close(operation):
            events.append('operation-close')
            return native_operation_close(operation)
        with patch.object(E.os, 'open', side_effect=opening), \
                patch.object(E, 'copy_source', side_effect=copying), \
                patch.object(E.HeldPath, 'close', closing), \
                patch.object(O.ManualGuestOperation, 'close', operation_close):
            result = self.apply()
        self.assertTrue(result['success'], result)
        self.assertEqual(events[0], 'subsystem')
        self.assertEqual(events.count('copy'), 3)
        self.assertEqual(events[-1], 'operation-close')
        self.assertIsNone(O._ACTIVE.operation)

    def test_existing_apply_finishes_after_shutdown_gate_closure(self):
        self.plan()
        native = E.copy_source
        closed = []
        def copying(*args):
            if not closed:
                closed.append(self.admission.close_gate('shutdown'))
                with self.assertRaises(A.AdmissionError):
                    closed[0].exclusive()
            return native(*args)
        with patch.object(E, 'copy_source', side_effect=copying):
            result = self.apply()
        self.assertTrue(result['success'], result)
        with closed[0].exclusive():
            pass
        self.assertFalse(self.apply()['success'])

    def test_config_replacement_between_discovery_and_review_never_stages(self):
        self.plan()
        native = E.Review.__init__
        def raced(review, *args, **kwargs):
            data = self.config_path.read_bytes()
            self.config_path.unlink()
            self.config_path.write_bytes(data)
            self.config_path.chmod(0o400)
            native(review, *args, **kwargs)
        with patch.object(E.Review, '__init__', raced), \
                patch.object(E, 'stage_and_publish') as stage:
            result = self.apply()
        self.assertFalse(result['success'])
        self.assertEqual(result['bytes_attempted_staged'], 0)
        stage.assert_not_called()
        self.assertFalse((self.root / '.enrollment').exists())

    def test_different_config_cannot_publish_under_discovered_name_lock(self):
        native = E.Review.__init__
        def raced(review, *args, **kwargs):
            self.config['name'] = 'two'
            self.save_config()
            E.ACCOUNTS.snapshot.return_value = {
                key: value.replace(b'one', b'two')
                for key, value in E.ACCOUNTS.snapshot.return_value.items()}
            native(review, *args, **kwargs)
        with patch.object(E.Review, '__init__', raced):
            with self.assertRaises(ValueError):
                E.prepare(*self.args())
        self.assertFalse((self.root / '.enrollment').exists())
        self.assertIsNone(O._ACTIVE.operation)

    def test_prepare_close_error_still_releases_discovery_and_operation(self):
        native = E.HeldPath.close
        closed = []
        def closing(held):
            self.assertTrue(getattr(O._ACTIVE, 'operation', None))
            closed.append(held.path)
            native(held)
            if held.path == str(self.config_path):
                raise OSError('input close failed')
        with patch.object(E.HeldPath, 'close', closing):
            with self.assertRaises(OSError):
                E.prepare(*self.args())
        self.assertEqual(closed.count(str(self.config_path)), 2)
        self.assertIsNone(O._ACTIVE.operation)
        with self.admission.admit() as lease, self.admission.guest_lock('one', lease):
            pass

    def test_prepare_preserves_factored_review_plan_bytes(self):
        review = E.Review(*self.args())
        try:
            expected = E.canonical(review.plan)
        finally:
            review.close()
        self.assertEqual(E.canonical(E.prepare(*self.args())), expected)

    def test_guard_failure_before_staging_and_before_publication_reports_failure(self):
        self.plan()
        original = E.bind_config
        calls = []
        def binding(*args):
            original(*args)
            calls.append(True)
            if len(calls) == 2:
                raise ValueError('final admission binding failure')
        with patch.object(E, 'bind_config', side_effect=ValueError('initial guard failure')):
            result = self.apply()
        self.assertFalse(result['success'])
        self.assertEqual(result['bytes_attempted_staged'], 0)
        self.assertFalse((self.root / '.enrollment').exists())
        with patch.object(E, 'bind_config', side_effect=binding):
            result = self.apply()
        self.assertFalse(result['success'])
        self.assertFalse(result['published'])
        self.assertEqual(list((self.root / '.enrollment').iterdir()), [])
        self.assertIsNone(O._ACTIVE.operation)

    def test_input_close_failure_closes_each_owner_once_and_operation_last(self):
        self.plan()
        native_close = E.HeldPath.close
        native_operation_close = O.ManualGuestOperation.close
        closed = []
        events = []
        def closing(held):
            self.assertTrue(getattr(O._ACTIVE, 'operation', None))
            self.assertNotIn(id(held), closed)
            closed.append(id(held))
            native_close(held)
            events.append('input')
            if held.path == str(self.config_path):
                raise OSError('close acknowledgement failed')
        def operation_close(operation):
            events.append('operation')
            return native_operation_close(operation)
        with patch.object(E.HeldPath, 'close', closing), \
                patch.object(O.ManualGuestOperation, 'close', operation_close):
            result = self.apply()
        self.assertFalse(result['success'])
        self.assertTrue(result['published'])
        self.assertEqual(result['cleanup_error'], 'OSError')
        self.assertEqual(len(closed), 7)  # Review root/config/plan/3 payloads + discovery.
        self.assertEqual(events[-1], 'operation')
        self.assertIsNone(O._ACTIVE.operation)
        with self.admission.admit() as lease, self.admission.guest_lock('one', lease):
            pass

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

    def test_storage_checked_before_payload_hash(self):
        with patch.object(E.STORAGE, 'verify_guest_directory', side_effect=ValueError('not p5')), \
                patch.object(E, 'hash_source') as reading, self.assertRaises(ValueError):
            E.prepare(*self.args())
        reading.assert_not_called()
        with patch.object(E, 'probe_uuid', side_effect=ValueError('UUID mismatch')), \
                patch.object(E, 'hash_source') as reading, self.assertRaises(ValueError):
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
