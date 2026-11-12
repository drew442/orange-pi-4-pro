"""Guarded restore private real-file checks; no hardware acceptance evidence."""
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import resource
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, BASE / path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


R = load('guest_restore_test_module', 'scripts/restore-guest.py')
T = load('guest_restore_fixture', 'tests/test_guest_backup_receiver.py')
E = R.ENROLL
UUID = 'ea5d365a-3fb4-421c-a47d-8907ff264f86'


def account_database():
    return {'passwd': b'a733-vm-one:x:60001:60001:A733 guest one:/nonexistent:/usr/sbin/nologin\n',
            'shadow': b'a733-vm-one:!:::::::\n', 'group': b'a733-vm-one:x:60001:\n',
            'gshadow': b'a733-vm-one:!::\n'}


class Authorization(unittest.TestCase):
    def test_uid_status_parser_all_four_credentials(self):
        self.assertEqual(R.status_uids(b'Name: test\nUid:\t0\t60001\t2\t3\n'), (0, 60001, 2, 3))
        for data in (b'', b'Uid: 1 2 3\n', b'Uid: 1 2 3 x\n', b'Uid: 1 2 3 4\n' * 2):
            with self.subTest(data=data), self.assertRaises(ValueError):
                R.status_uids(data)

    def test_uid_census_refuses_saved_uid_and_unreadable_status(self):
        with tempfile.TemporaryDirectory() as temp:
            proc = Path(temp)
            (proc / '123').mkdir()
            status = proc / '123' / 'status'
            status.write_bytes(b'Uid: 0 0 60001 0\n')
            with self.assertRaises(ValueError):
                R.uid_consumers(60001, proc)
            status.write_bytes(b'Name: missing UID\n')
            with self.assertRaises(ValueError):
                R.uid_consumers(60001, proc)
            status.unlink()
            R.uid_consumers(60001, proc)

    def test_non_tty_fails_closed(self):
        with patch.object(R.sys.stdin, 'isatty', return_value=False), self.assertRaises(ValueError):
            R.authorize({})

    def test_storage_before_source_hash(self):
        with patch.object(R.operation_module(), 'ManualGuestOperation'), \
                patch.object(E.MANAGEMENT, 'runtime_guard', side_effect=ValueError('unsupported')), \
                patch.object(R.SOURCE, 'Source') as source, self.assertRaises(ValueError):
            R.prepare('/absent', 'a' * 64, 'b' * 64, UUID, name='one')
        source.assert_not_called()


@unittest.skipUnless(os.geteuid() == 0, 'process credential fixture requires root')
class LiveUidCensus(unittest.TestCase):
    def test_actual_child_guest_uid_refused(self):
        child = subprocess.Popen(['/usr/bin/python3', '-c',
                                  'import os,time; os.setgid(60001); os.setuid(60001); print(1,flush=True); time.sleep(20)'],
                                 stdout=subprocess.PIPE)
        try:
            self.assertEqual(child.stdout.readline(), b'1\n')
            with self.assertRaisesRegex(ValueError, 'existing process'):
                R.uid_consumers(60001)
        finally:
            child.terminate()
            child.wait(timeout=5)
            child.stdout.close()


@unittest.skipUnless(os.geteuid() == 0, 'private real ownership engine requires root')
class RestoreFiles(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='restore-test-', dir='/run')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'guests'
        self.root.mkdir(mode=0o711)
        self.state = self.base / 'state'
        self.state.mkdir(mode=0o700)
        self.archive = self.base / 'backup.gz'
        self.plan_path = self.base / 'plan.json'
        self.manifest, self.payload = T.fixture()
        self.history = {}
        self.write_archive()
        self.context = {'guest_source': '/dev/nvme0n1p5', 'guest_device': [259, 5], 'fixture': True}
        for mock in (patch.object(R.operation_module(), 'ManualGuestOperation'),
                     patch.object(E, 'ROOT', str(self.root)),
                     patch.object(E, 'LOCK', str(self.base / 'lock')),
                     patch.object(E.MANAGEMENT, 'STATE', str(self.state)),
                     patch.object(E.MANAGEMENT, 'runtime_guard'),
                     patch.object(R.POLICY, 'host_namespace'),
                     patch.object(R.POLICY, 'service_state', return_value={'fixture': 'stopped'}),
                     patch.object(E.STORAGE, 'verify_guest_directory', return_value=self.context),
                     patch.object(E, 'probe_uuid'), patch.object(E, 'budget'),
                     patch.object(E.ACCOUNTS, 'snapshot', return_value=account_database()),
                     patch.object(R, 'uid_consumers'), patch.object(R, 'authorize')):
            mock.start()
            self.addCleanup(mock.stop)

    def write_archive(self):
        self.encoded = T.bundle(self.manifest, self.payload, self.history)
        self.archive.write_bytes(self.encoded)
        self.archive.chmod(0o400)

    def args(self):
        return str(self.archive), T.sha(self.encoded), T.sha(self.manifest), UUID

    def plan(self):
        value = R.prepare(*self.args(), name='one')
        self.plan_path.write_bytes(R.canonical(value))
        self.plan_path.chmod(0o400)
        return value

    def apply(self):
        return R.apply(*self.args(), str(self.plan_path), name='one')

    def add_history(self):
        old = T.F.MANIFEST.validate_manifest(self.manifest)
        self.payload['disk.raw'] = b'X' * 512
        new = T.F.MANIFEST.proposed_manifest(old, T.sha(self.manifest), R.record(self.payload['disk.raw']))
        data = T.F.MANIFEST.canonical(new)
        entry = T.F.canonical({'schema': 1, 'operation': 'a733-guest-checkpoint-v1',
                              'old_manifest': old, 'new_manifest': new,
                              'old_manifest_sha256': T.sha(self.manifest), 'new_manifest_sha256': T.sha(data),
                              'old_manifest_base64': base64.b64encode(self.manifest).decode(),
                              'new_manifest_base64': base64.b64encode(data).decode()})
        name = T.sha(self.manifest) + '-' + T.sha(data) + '.json'
        self.history = {name: entry}
        self.manifest = data
        self.write_archive()

    def test_prepare_has_no_writes_and_repeats(self):
        before = list(self.base.rglob('*'))
        plan = R.prepare(*self.args(), name='one')
        self.assertEqual(before, list(self.base.rglob('*')))
        self.assertEqual(plan, R.prepare(*self.args(), name='one'))
        self.assertFalse((self.root / '.enrollment').exists())

    def test_restores_current_payload_history_uuid_and_provenance(self):
        self.add_history()
        plan = self.plan()
        result = self.apply()
        self.assertTrue(result['success'], result)
        self.assertTrue(result['published'])
        self.assertTrue(result['durable'])
        self.assertFalse(result['staging_retained'])
        final = self.root / 'one'
        self.assertEqual((final.stat().st_uid, final.stat().st_gid, stat.S_IMODE(final.stat().st_mode)),
                         (0, 60001, 0o710))
        for name, data in self.payload.items():
            path = final / name
            self.assertEqual(path.read_bytes(), data)
            expected = (60001, 60001, 0o600) if name == 'disk.raw' else (0, 0, 0o444)
            info = path.stat()
            self.assertEqual((info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)), expected)
        raw = (final / 'enrollment.json').read_bytes()
        self.assertEqual(T.sha(raw), result['manifest_sha256'])
        value = T.F.MANIFEST.validate_manifest(raw)
        self.assertNotIn('checkpoint', value)
        self.assertEqual(value['guest_uuid'], UUID)
        self.assertEqual(value['restoration'], {'archive_sha256': T.sha(self.encoded),
                          'manifest_sha256': T.sha(self.manifest),
                          'guest_uuid': json.loads(self.manifest)['guest_uuid']})
        self.assertEqual(plan['manifest']['value'], value)
        for name, data in self.history.items():
            path = final / '.checkpoints' / name
            self.assertEqual(path.read_bytes(), data)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o400)
        self.assertEqual(result['bytes_attempted_staged'], sum(map(len, self.payload.values())))

    def test_many_history_records_have_compact_plan_and_bounded_fds(self):
        old = T.F.MANIFEST.validate_manifest(self.manifest)
        for index in range(300):
            new = T.F.MANIFEST.proposed_manifest(old, T.sha(self.manifest),
                       {'bytes': 512, 'sha256': hashlib.sha256(str(index).encode()).hexdigest()})
            data = T.F.MANIFEST.canonical(new)
            entry = T.F.canonical({'schema': 1, 'operation': 'a733-guest-checkpoint-v1',
                      'old_manifest': old, 'new_manifest': new,
                      'old_manifest_sha256': T.sha(self.manifest), 'new_manifest_sha256': T.sha(data),
                      'old_manifest_base64': base64.b64encode(self.manifest).decode(),
                      'new_manifest_base64': base64.b64encode(data).decode()})
            self.history[T.sha(self.manifest) + '-' + T.sha(data) + '.json'] = entry
        self.write_archive()
        plan = self.plan()
        self.assertLess(len(R.canonical(plan)), E.CONFIG.LIMIT)
        self.assertEqual(plan['history']['record_count'], 300)
        # A subprocess inherits the fixture policy mocks but its resource limit
        # cannot affect the test runner. The restore opens history one at a time.
        reader, writer = os.pipe()
        child = os.fork()
        if child == 0:
            os.close(reader)
            try:
                resource.setrlimit(resource.RLIMIT_NOFILE, (96, 96))
                result = self.apply()
                os.write(writer, R.canonical(result))
                os._exit(0)
            except BaseException:
                os._exit(1)
        os.close(writer)
        with os.fdopen(reader, 'rb') as stream:
            result = json.loads(stream.read())
        _, status = os.waitpid(child, 0)
        self.assertEqual(status, 0)
        self.assertTrue(result['success'], result)
        self.assertEqual(len(list((self.root / 'one' / '.checkpoints').iterdir())), 300)

    def test_stale_plan_and_changed_source_fail_before_staging(self):
        plan = self.plan()
        plan['guest_uuid'] = json.loads(self.manifest)['guest_uuid']
        self.plan_path.write_bytes(R.canonical(plan))
        result = self.apply()
        self.assertFalse(result['success'])
        self.assertFalse((self.root / '.enrollment').exists())
        self.plan()
        self.archive.chmod(0o600)
        self.archive.write_bytes(b'bad archive')
        self.archive.chmod(0o400)
        result = self.apply()
        self.assertFalse(result['success'])
        self.assertFalse((self.root / '.enrollment').exists())

    def test_every_existing_target_refused(self):
        for kind in ('file', 'directory', 'symlink'):
            with self.subTest(kind=kind):
                self.plan()
                target = self.root / 'one'
                if kind == 'file':
                    target.write_bytes(b'sentinel')
                elif kind == 'directory':
                    target.mkdir()
                else:
                    target.symlink_to(self.archive)
                self.assertFalse(self.apply()['success'])
                self.assertTrue(os.path.lexists(target))
                target.rmdir() if kind == 'directory' else target.unlink()

    def test_authorization_abort_has_no_staging(self):
        self.plan()
        with patch.object(R, 'authorize', side_effect=ValueError('abort')):
            result = self.apply()
        self.assertFalse(result['published'])
        self.assertFalse((self.root / '.enrollment').exists())

    def test_callback_and_readback_failure_clean_owned_only(self):
        for failing in ('scan', 'readback'):
            with self.subTest(failing=failing):
                self.plan()
                if failing == 'scan':
                    original = R.SOURCE.Source.scan
                    def scan(source, emit=None):
                        if emit is not None:
                            emit('Image', b'bad')
                            raise ValueError('callback interrupted')
                        return original(source, emit)
                    mock = patch.object(R.SOURCE.Source, 'scan', scan)
                else:
                    mock = patch.object(R, 'readback', side_effect=ValueError('readback differs'))
                with mock:
                    result = self.apply()
                self.assertFalse(result['success'])
                self.assertFalse(result['published'])
                self.assertFalse(result['staging_retained'])
                self.assertFalse((self.root / 'one').exists())

    def test_foreign_staging_entry_preserved(self):
        self.plan()
        original = R.readback
        injected = False
        def readback(fd, expected):
            nonlocal injected
            if not injected:
                injected = True
                stage = next((self.root / '.enrollment').iterdir())
                (stage / 'foreign').write_bytes(b'sentinel')
            return original(fd, expected)
        with patch.object(R, 'readback', readback):
            result = self.apply()
        self.assertFalse(result['success'])
        self.assertTrue(result['staging_retained'])
        stage = next((self.root / '.enrollment').iterdir())
        self.assertEqual((stage / 'foreign').read_bytes(), b'sentinel')
        self.assertEqual(list(stage.iterdir()), [stage / 'foreign'])

    def test_queued_sigterm_after_rename_preserves_published_inode(self):
        self.plan()
        original = E.rename_new
        def rename(*args):
            original(*args)
            os.kill(os.getpid(), signal.SIGTERM)
        def interrupted(_signum, _frame):
            raise KeyboardInterrupt
        previous = signal.signal(signal.SIGTERM, interrupted)
        try:
            with patch.object(E, 'rename_new', rename):
                result = self.apply()
        finally:
            signal.signal(signal.SIGTERM, previous)
        self.assertFalse(result['success'])
        self.assertTrue(result['published'])
        self.assertTrue(result['durable'])
        self.assertFalse(result['staging_retained'])
        self.assertEqual((self.root / 'one' / 'disk.raw').read_bytes(), self.payload['disk.raw'])

    def test_parent_fsync_failure_preserves_visible_restore(self):
        self.plan()
        original = os.fsync
        def fsync(fd):
            if E.inode(os.fstat(fd)) == E.inode(self.root.stat()):
                raise OSError('parent flush failed')
            return original(fd)
        with patch.object(R.os, 'fsync', fsync):
            result = self.apply()
        self.assertFalse(result['success'])
        self.assertTrue(result['published'])
        self.assertFalse(result['durable'])
        self.assertFalse(result['staging_retained'])
        self.assertTrue((self.root / 'one' / 'enrollment.json').exists())

    def test_service_account_budget_and_namespace_fail_closed(self):
        for target, name in ((R.POLICY, 'service_state'), (E.ACCOUNTS, 'snapshot'),
                             (E, 'budget'), (R.POLICY, 'host_namespace'), (R, 'uid_consumers')):
            self.plan()
            with self.subTest(name=name), patch.object(target, name, side_effect=ValueError('unavailable')):
                result = self.apply()
            self.assertFalse(result['success'])
            self.assertFalse((self.root / '.enrollment').exists())

    def test_changed_destination_uuid_refuses_before_archive(self):
        with patch.object(E, 'probe_uuid', side_effect=ValueError('wrong destination')), \
                patch.object(R.SOURCE, 'Source') as source, self.assertRaises(ValueError):
            R.prepare(*self.args(), name='one')
        source.assert_not_called()


if __name__ == '__main__':
    unittest.main()
