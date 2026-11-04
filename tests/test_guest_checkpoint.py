"""Private real-file checkpoint transaction tests; no physical acceptance evidence."""
import io
import base64
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import signal
import stat
import tempfile
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('guest_checkpoint', BASE / 'scripts/checkpoint-guest.py')
C = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(C)


@unittest.skipUnless(os.geteuid() == 0, 'root ownership checks require private sudo fixture')
class CheckpointFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir='/run', prefix='checkpoint-tests-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / 'guests'
        self.root.mkdir(mode=0o711)
        self.guest = self.root / 'one'
        self.guest.mkdir(mode=0o710)
        os.chown(self.guest, 0, 60001)
        self.state = self.base / 'state'
        self.state.mkdir(mode=0o700)
        config = {'schema': 1, 'name': 'one', 'uid': 60001, 'gid': 60001,
                  'memory_mib': 64, 'memory_max_mib': 128, 'cpus': 1}
        files = {}
        for key, name, data in [('kernel', 'Image', b'kernel'),
                                ('initrd', 'initramfs.cpio.gz', b'initrd'),
                                ('disk', 'disk.raw', b'A' * 512)]:
            disk = key == 'disk'
            path = self.guest / name
            path.write_bytes(data)
            path.chmod(0o600 if disk else 0o444)
            if disk:
                os.chown(path, 60001, 60001)
            config[key] = C.record(data)
            files[name] = {**config[key], 'uid': 60001 if disk else 0,
                           'gid': 60001 if disk else 0, 'mode': '0600' if disk else '0444'}
        self.manifest = {'schema': 1, 'profile': 'a733-guest-enrollment-v1', 'config': config,
                         'guest_uuid': '7811c43d-01e4-4635-83b4-25d276172ac0', 'files': files}
        self.old_data = C.MANIFEST.canonical(self.manifest)
        self.manifest_path = self.guest / 'enrollment.json'
        self.manifest_path.write_bytes(self.old_data)
        self.manifest_path.chmod(0o400)
        self.pin = C.record(self.old_data)['sha256']
        self.disk = self.guest / 'disk.raw'
        self.disk.write_bytes(b'B' * 512)
        self.disk_data = self.disk.read_bytes()
        self.plan_path = self.base / 'plan.json'
        self.context = {'namespace': 'fixture-only'}
        mocks = [patch.object(C, 'LOCK', str(self.base / 'lock')),
                 patch.object(C.ENROLL, 'ROOT', str(self.root)),
                 patch.object(C.ENROLL.MANAGEMENT, 'STATE', self.state),
                 patch.object(C.ENROLL.MANAGEMENT, 'runtime_guard'),
                 patch.object(C.ENROLL, 'probe_uuid'),
                 patch.object(C.POLICY, 'host_namespace'),
                 patch.object(C.POLICY, 'service_state', return_value={'active': False}),
                 patch.object(C.POLICY, 'disk_consumers', return_value=[]),
                 patch.object(C.POLICY.ACCOUNTS, 'validate_accounts'),
                 patch.object(C.POLICY.ACCOUNTS, 'snapshot', return_value={}),
                 patch.object(C.POLICY.STORAGE, 'verify_guest_directory', return_value=self.context),
                 patch.object(C.POLICY.STORAGE, 'verify_guest_storage', return_value=self.context),
                 patch.object(C, 'authorize'), patch.object(C.sys, 'stderr', io.StringIO())]
        for mock in mocks:
            mock.start()
            self.addCleanup(mock.stop)
        self.save_plan()

    def save_plan(self):
        self.plan = C.prepare('one', self.pin)
        self.plan_path.write_bytes(C.canonical(self.plan))
        self.plan_path.chmod(0o400)

    def apply(self):
        return C.apply('one', self.pin, str(self.plan_path))

    def assert_disk_unchanged(self):
        self.assertEqual(self.disk.read_bytes(), self.disk_data)
        self.assertEqual(stat.S_IMODE(self.disk.stat().st_mode), 0o600)
        self.assertEqual(self.disk.stat().st_uid, 60001)

    def test_publish_archive_permissions_and_disk_unchanged(self):
        receipt = self.apply()
        self.assertTrue(receipt['success'], receipt)
        self.assertTrue(receipt['published'])
        self.assertTrue(receipt['durable'])
        self.assertTrue(receipt['history_durable'])
        self.assertEqual(self.manifest_path.read_bytes(),
                         C.MANIFEST.canonical(self.plan['proposed_manifest']['value']))
        history = self.guest / '.checkpoints'
        self.assertEqual(stat.S_IMODE(history.stat().st_mode), 0o700)
        archives = list(history.iterdir())
        self.assertEqual(len(archives), 1)
        archive = json.loads(archives[0].read_bytes())
        self.assertEqual(archive['old_manifest'], self.manifest)
        self.assertEqual(archive['new_manifest'], self.plan['proposed_manifest']['value'])
        for path in (archives[0], self.manifest_path):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o400)
            self.assertEqual((path.stat().st_uid, path.stat().st_gid, path.stat().st_nlink), (0, 0, 1))
        self.assert_disk_unchanged()

    def test_history_retains_exact_noncanonical_before_and_after_bytes(self):
        self.old_data = json.dumps(self.manifest, indent=2).encode()
        self.manifest_path.write_bytes(self.old_data)
        self.pin = C.record(self.old_data)['sha256']
        self.save_plan()
        result = self.apply()
        self.assertTrue(result['success'], result)
        archive = json.loads(next((self.guest / '.checkpoints').iterdir()).read_bytes())
        before = base64.b64decode(archive['old_manifest_base64'], validate=True)
        after = base64.b64decode(archive['new_manifest_base64'], validate=True)
        self.assertEqual(before, self.old_data)
        self.assertEqual(C.record(before)['sha256'], archive['old_manifest_sha256'])
        self.assertEqual(after, self.manifest_path.read_bytes())
        self.assertEqual(C.record(after)['sha256'], archive['new_manifest_sha256'])

    def test_stale_plan_and_external_pin_refuse_before_write(self):
        self.disk.write_bytes(b'C' * 512)
        receipt = self.apply()
        self.assertFalse(receipt['success'])
        self.assertFalse(receipt['metadata_may_be_modified'])
        self.assertFalse((self.guest / '.checkpoints').exists())
        with self.assertRaises(ValueError):
            C.prepare('one', '0' * 64)

    def test_noop_has_no_archive_or_flush(self):
        self.disk.write_bytes(b'A' * 512)
        self.disk_data = self.disk.read_bytes()
        self.save_plan()
        with patch.object(C.os, 'fsync', side_effect=AssertionError('no flush needed')):
            receipt = self.apply()
        self.assertTrue(receipt['success'], receipt)
        self.assertTrue(receipt['unchanged'])
        self.assertFalse(receipt['published'])
        self.assertFalse(receipt['disk_flush_attempted'])
        self.assertFalse((self.guest / '.checkpoints').exists())
        self.assertEqual(self.manifest_path.read_bytes(), self.old_data)

    def test_noop_preserves_noncanonical_old_manifest_pin(self):
        self.disk.write_bytes(b'A' * 512)
        self.old_data = json.dumps(self.manifest, indent=2).encode()
        self.manifest_path.write_bytes(self.old_data)
        self.pin = C.record(self.old_data)['sha256']
        self.save_plan()
        self.assertEqual(self.plan['proposed_manifest']['sha256'], self.pin)
        receipt = self.apply()
        self.assertTrue(receipt['success'], receipt)
        self.assertEqual(receipt['new_manifest_sha256'], self.pin)
        self.assertEqual(self.manifest_path.read_bytes(), self.old_data)

    def test_lock_busy_refused(self):
        lock = os.open(C.LOCK, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            receipt = self.apply()
            self.assertFalse(receipt['success'])
            self.assertFalse(receipt['disk_flush_attempted'])
        finally:
            os.close(lock)

    def test_disk_flush_failure_retains_old_manifest(self):
        with patch.object(C.os, 'fsync', side_effect=OSError('flush failed')):
            receipt = self.apply()
        self.assertFalse(receipt['success'])
        self.assertTrue(receipt['disk_flush_attempted'])
        self.assertFalse(receipt['published'])
        self.assertEqual(self.manifest_path.read_bytes(), self.old_data)
        self.assert_disk_unchanged()

    def test_archive_collision_never_overwritten(self):
        history = self.guest / '.checkpoints'
        history.mkdir(mode=0o700)
        archive = history / (self.pin + '-' + self.plan['proposed_manifest']['sha256'] + '.json')
        archive.write_bytes(b'foreign')
        archive.chmod(0o400)
        receipt = self.apply()
        self.assertFalse(receipt['success'])
        self.assertFalse(receipt['history_published'])
        self.assertEqual(archive.read_bytes(), b'foreign')
        self.assertEqual(self.manifest_path.read_bytes(), self.old_data)

    def test_manifest_replace_failure_preserves_archive_and_retry_reuses(self):
        with patch.object(C.os, 'replace', side_effect=OSError('publication failed')):
            receipt = self.apply()
        self.assertFalse(receipt['published'])
        self.assertTrue(receipt['history_durable'])
        archives = list((self.guest / '.checkpoints').iterdir())
        self.assertEqual(len(archives), 1)
        archived_inode = archives[0].stat().st_ino
        self.assertEqual(self.manifest_path.read_bytes(), self.old_data)
        receipt = self.apply()
        self.assertTrue(receipt['success'], receipt)
        self.assertEqual(archives[0].stat().st_ino, archived_inode)
        self.assert_disk_unchanged()

    def test_replace_then_raise_reconciles_publication(self):
        real = C.os.replace
        def interrupted(*args, **kwargs):
            real(*args, **kwargs)
            raise KeyboardInterrupt
        with patch.object(C.os, 'replace', side_effect=interrupted):
            receipt = self.apply()
        self.assertFalse(receipt['success'])
        self.assertTrue(receipt['published'])
        self.assertFalse(receipt['durable'])
        self.assertEqual(C.record(self.manifest_path.read_bytes())['sha256'],
                         receipt['new_manifest_sha256'])
        self.assert_disk_unchanged()

    def test_real_signals_queued_after_replace_preserve_publication(self):
        real = C.os.replace
        for signum in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signum=signum):
                self.manifest_path.write_bytes(self.old_data)
                self.save_plan()
                def interrupted(*args, **kwargs):
                    real(*args, **kwargs)
                    os.kill(os.getpid(), signum)
                def handler(_signum, _frame):
                    raise KeyboardInterrupt
                previous = signal.signal(signum, handler)
                try:
                    with patch.object(C.os, 'replace', side_effect=interrupted):
                        receipt = self.apply()
                finally:
                    signal.signal(signum, previous)
                self.assertTrue(receipt['published'], receipt)
                self.assertTrue(receipt['durable'], receipt)
                self.assertFalse(receipt['success'])
                self.assert_disk_unchanged()

    def test_source_mutation_after_authorization_refused(self):
        def changed(_plan):
            self.disk.write_bytes(b'C' * 512)
        with patch.object(C, 'authorize', side_effect=changed):
            receipt = self.apply()
        self.assertFalse(receipt['success'])
        self.assertFalse(receipt['disk_flush_attempted'])
        self.assertEqual(self.manifest_path.read_bytes(), self.old_data)

    def test_review_plan_mutation_after_authorization_refused(self):
        def changed(_plan):
            self.plan_path.write_bytes(b'{}')
        with patch.object(C, 'authorize', side_effect=changed):
            receipt = self.apply()
        self.assertFalse(receipt['success'])
        self.assertFalse(receipt['disk_flush_attempted'])
        self.assertEqual(self.manifest_path.read_bytes(), self.old_data)

    def test_postpublication_directory_flush_failure_preserves_new_manifest(self):
        real = C.os.fsync
        def failed(fd):
            if (os.fstat(fd).st_ino == self.guest.stat().st_ino and
                    self.manifest_path.read_bytes() != self.old_data):
                raise OSError('new manifest visible; parent flush failed')
            real(fd)
        with patch.object(C.os, 'fsync', side_effect=failed):
            receipt = self.apply()
        self.assertTrue(receipt['published'], receipt)
        self.assertFalse(receipt['durable'])
        self.assertFalse(receipt['success'])
        self.assertTrue(receipt['history_durable'])
        self.assertEqual(C.record(self.manifest_path.read_bytes())['sha256'],
                         receipt['new_manifest_sha256'])
        self.assert_disk_unchanged()

    def test_history_publication_then_raise_retains_archive(self):
        real = C.ENROLL.rename_new
        def failed(*args):
            real(*args)
            raise KeyboardInterrupt
        with patch.object(C.ENROLL, 'rename_new', side_effect=failed):
            receipt = self.apply()
        self.assertTrue(receipt['history_published'], receipt)
        self.assertFalse(receipt['published'])
        self.assertFalse(receipt['success'])
        self.assertEqual(len(list((self.guest / '.checkpoints').iterdir())), 1)
        self.assertEqual(self.manifest_path.read_bytes(), self.old_data)

    def test_cleanup_close_error_does_not_hide_publication(self):
        real = C.os.close
        failed_once = []
        def failed(fd):
            info = os.fstat(fd)
            real(fd)
            if stat.S_ISREG(info.st_mode) and not failed_once:
                failed_once.append(fd)
                raise OSError('close acted then failed')
        with patch.object(C.os, 'close', side_effect=failed):
            receipt = self.apply()
        self.assertTrue(receipt['published'], receipt)
        self.assertTrue(receipt['durable'])
        self.assertFalse(receipt['success'])
        self.assertEqual(receipt['cleanup_error'], 'OSError')
        self.assert_disk_unchanged()

    def test_unsafe_history_directory_refused(self):
        outside = self.base / 'outside'
        outside.mkdir()
        (self.guest / '.checkpoints').symlink_to(outside)
        receipt = self.apply()
        self.assertFalse(receipt['success'])
        self.assertFalse(receipt['published'])
        self.assertFalse(list(outside.iterdir()))
        self.assertEqual(self.manifest_path.read_bytes(), self.old_data)

    def test_foreign_temporary_replacement_not_deleted(self):
        foreign = []
        def replaced(source, destination, **kwargs):
            parent = kwargs['src_dir_fd']
            os.unlink(source, dir_fd=parent)
            fd = os.open(source, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o400, dir_fd=parent)
            os.write(fd, b'foreign-sentinel')
            os.close(fd)
            foreign.append(self.guest / source)
            raise OSError('injected foreign replacement')
        with patch.object(C.os, 'replace', side_effect=replaced):
            receipt = self.apply()
        self.assertFalse(receipt['published'])
        self.assertEqual(foreign[0].read_bytes(), b'foreign-sentinel')
        self.assertEqual(self.manifest_path.read_bytes(), self.old_data)


if __name__ == '__main__':
    unittest.main()
