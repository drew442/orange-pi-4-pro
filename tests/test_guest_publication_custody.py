"""Retained real-file disk custody checks; no A733 or clean-shutdown evidence."""
import base64
import copy
import errno
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import threading
import unittest
from unittest.mock import Mock, patch

BASE = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('custody_fixture', BASE / 'tests/test_guest_checkpoint.py')
FIXTURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIXTURE)
C = FIXTURE.C
P = C.POLICY


@unittest.skipUnless(os.geteuid() == 0, 'root ownership checks require private sudo fixture')
class PublicationCustody(unittest.TestCase):
    setUp = FIXTURE.CheckpointFiles.setUp
    save_plan = FIXTURE.CheckpointFiles.save_plan

    def prepare(self, *, noop=False, noncanonical=False):
        if noop:
            self.disk.write_bytes(b'A' * 512)
        if noncanonical:
            self.old_data = json.dumps(self.manifest, indent=2).encode()
            self.manifest_path.write_bytes(self.old_data)
            self.pin = C.record(self.old_data)['sha256']
        self.inspection = P.Inspection('one', self.pin)
        self.addCleanup(self.inspection.close)
        provenance = {'profile': 'a733-managed-shutdown-v1',
                      'run_id': '7811c43d-01e4-4635-83b4-25d276172ac1',
                      'boot_id': '7811c43d-01e4-4635-83b4-25d276172ac2',
                      'shutdown_profile': 'qmp-guest-shutdown-v1',
                      'fixture_profile': 'none', 'evidence_sha256': 'e' * 64}
        self.new = C.MANIFEST.proposed_managed_manifest(
            self.inspection.manifest, self.pin, self.inspection.disk_record, provenance)
        self.new_data = self.old_data if noop else C.MANIFEST.canonical(self.new)
        self.history_value = {
            'schema': 2, 'operation': 'a733-managed-guest-checkpoint-v2',
            'old_manifest': self.inspection.manifest, 'new_manifest': self.new,
            'old_manifest_sha256': self.pin,
            'new_manifest_sha256': C.record(self.new_data)['sha256'],
            'old_manifest_base64': base64.b64encode(self.old_data).decode(),
            'new_manifest_base64': base64.b64encode(self.new_data).decode()}
        self.history_data = None if noop else C.canonical(self.history_value)
        self.authorization = Mock()
        self.custody = self.inspection.prepare_managed_publication(
            self.new_data, self.history_data, authorization=self.authorization)
        self.addCleanup(self.custody.close)
        return self.custody

    def publish(self, *, history=True):
        if history:
            directory = self.guest / '.checkpoints'
            directory.mkdir(mode=0o700)
            path = directory / self.custody.archive_name
            path.write_bytes(self.history_data)
            path.chmod(0o400)
        temporary = self.guest / 'prepared'
        temporary.write_bytes(self.new_data)
        temporary.chmod(0o400)
        os.replace(temporary, self.manifest_path)

    def assert_locked(self):
        fd = os.open(self.disk, os.O_RDONLY)
        try:
            with self.assertRaises(BlockingIOError):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)

    def test_old_inspection_stale_custody_rebinds_exact_pair(self):
        self.prepare(noncanonical=True)
        self.custody.before_recheck()
        self.publish()
        with self.assertRaises(ValueError):
            self.inspection.recheck()
        self.assertIs(self.custody.bind_published(), self.custody)
        self.custody.recheck()
        self.inspection.close()
        self.custody.recheck()
        self.assert_locked()
        self.custody.close()
        fd = os.open(self.disk, os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)

    def test_original_close_keeps_flock_before_binding(self):
        self.prepare()
        self.inspection.close()
        self.assert_locked()
        self.custody.before_recheck()
        self.publish()
        self.custody.bind_published().recheck()

    def test_noop_keeps_exact_noncanonical_bytes_and_inode(self):
        self.prepare(noop=True, noncanonical=True)
        inode = self.manifest_path.stat().st_ino
        self.custody.bind_published().recheck()
        self.assertEqual(self.manifest_path.read_bytes(), self.old_data)
        self.assertEqual(self.manifest_path.stat().st_ino, inode)
        self.assertFalse((self.guest / '.checkpoints').exists())

    def test_noop_replacement_refused(self):
        self.prepare(noop=True)
        self.publish(history=False)
        with self.assertRaises(ValueError):
            self.custody.bind_published()

    def test_exact_history_required(self):
        for kind in ('missing', 'bytes', 'mode', 'directory', 'link'):
            with self.subTest(kind=kind):
                # A fresh instance prevents poisoned custody from masking errors.
                case = PublicationCustody()
                case.setUp()
                try:
                    case.prepare()
                    case.publish(history=kind != 'missing')
                    if kind != 'missing':
                        directory = case.guest / '.checkpoints'
                        path = directory / case.custody.archive_name
                        if kind == 'bytes':
                            path.write_bytes(case.history_data + b' ')
                        elif kind == 'mode':
                            path.chmod(0o600)
                        elif kind == 'directory':
                            directory.chmod(0o755)
                        else:
                            os.link(path, case.base / 'linked-history')
                    with self.assertRaises((ValueError, OSError)):
                        case.custody.bind_published()
                finally:
                    case.doCleanups()

    def test_prepared_pair_validation(self):
        self.prepare()
        malformed = [b'{}', b'invalid', self.history_data + b' ']
        for key, value in [('schema', True), ('schema', 1), ('operation', 'other'),
                           ('new_manifest_sha256', '0' * 64),
                           ('old_manifest_base64', base64.b64encode(b'{}').decode())]:
            bad = {**self.history_value, key: value}
            malformed.append(C.canonical(bad))
        for data in malformed:
            with self.subTest(data=data[:60]):
                with self.assertRaises((ValueError, KeyError)):
                    self.inspection.prepare_managed_publication(
                        self.new_data, data, authorization=self.authorization)
        changed = copy.deepcopy(self.new)
        changed['config']['cpus'] = 2
        with self.assertRaises(ValueError):
            self.inspection.prepare_managed_publication(
                C.MANIFEST.canonical(changed), self.history_data, authorization=self.authorization)
        with self.assertRaises(ValueError):
            self.inspection.prepare_managed_publication(self.new_data, self.history_data,
                                                       authorization=True)

    def test_noop_cannot_change_raw_or_add_history(self):
        self.prepare(noop=True, noncanonical=True)
        for raw, history in [(C.MANIFEST.canonical(self.manifest), None),
                             (self.old_data, b'{}')]:
            with self.assertRaises(ValueError):
                self.inspection.prepare_managed_publication(raw, history,
                                                           authorization=self.authorization)

    def test_initial_authorization_failure_refuses_factory(self):
        self.prepare()
        guard = Mock()
        guard.recheck.side_effect = ValueError('durable intent invalid')
        with self.assertRaises(ValueError):
            self.inspection.prepare_managed_publication(
                self.new_data, self.history_data, authorization=guard)
        self.inspection.recheck()
        self.assert_locked()

    def test_current_namespace_storage_accounts_service_and_consumers_rechecked(self):
        for target, attribute, kwargs in [
                (P, 'host_namespace', {'side_effect': ValueError('namespace changed')}),
                (P.STORAGE, 'verify_guest_directory', {'return_value': {'other': True}}),
                (P.STORAGE, 'verify_guest_storage', {'return_value': {'other': True}}),
                (P.ACCOUNTS, 'validate_accounts', {'side_effect': ValueError('accounts changed')}),
                (P, 'service_state', {'side_effect': ValueError('service running')}),
                (P, 'disk_consumers', {'return_value': [{'pid': 123, 'fd': 4}]})]:
            with self.subTest(attribute=attribute):
                case = PublicationCustody()
                case.setUp()
                try:
                    case.prepare()
                    case.publish()
                    case.custody.bind_published()
                    with patch.object(target, attribute, **kwargs):
                        with self.assertRaises(ValueError):
                            case.custody.recheck()
                finally:
                    case.doCleanups()

    def test_authorization_failure_poisoned(self):
        self.prepare()
        self.publish()
        self.authorization.recheck.side_effect = ValueError('checkpoint head changed')
        with self.assertRaises(ValueError):
            self.custody.bind_published()
        self.authorization.recheck.side_effect = None
        with self.assertRaises(ValueError):
            self.custody.bind_published()
        self.assert_locked()

    def test_changed_published_manifest_refused_and_poisoned(self):
        self.prepare()
        self.publish()
        self.manifest_path.write_bytes(self.old_data)
        with self.assertRaises(ValueError):
            self.custody.bind_published()
        self.manifest_path.write_bytes(self.new_data)
        with self.assertRaises(ValueError):
            self.custody.bind_published()

    def test_postbind_disk_inode_metadata_hash_and_boot_inputs_refused(self):
        for kind in ('inode', 'mode', 'hash', 'kernel', 'history', 'history-inode'):
            with self.subTest(kind=kind):
                case = PublicationCustody()
                case.setUp()
                try:
                    case.prepare()
                    case.publish()
                    case.custody.bind_published()
                    if kind == 'inode':
                        path = case.guest / 'disk-copy'
                        path.write_bytes(case.disk.read_bytes())
                        path.chmod(0o600)
                        os.chown(path, 60001, 60001)
                        os.replace(path, case.disk)
                    elif kind == 'mode':
                        case.disk.chmod(0o644)
                    elif kind == 'hash':
                        case.disk.write_bytes(b'C' * 512)
                    elif kind == 'kernel':
                        (case.guest / 'Image').write_bytes(b'KERNEL')
                    else:
                        path = case.guest / '.checkpoints' / case.custody.archive_name
                        if kind == 'history':
                            path.write_bytes(case.history_data)
                        else:
                            temporary = case.base / 'new-history'
                            temporary.write_bytes(case.history_data)
                            temporary.chmod(0o400)
                            os.replace(temporary, path)
                    with self.assertRaises(ValueError):
                        case.custody.recheck()
                finally:
                    case.doCleanups()

    def test_disk_flock_not_reacquired_or_converted(self):
        self.prepare()
        fcntl.flock(self.inspection.disk_fd, fcntl.LOCK_UN)
        with patch.object(P.fcntl, 'flock', side_effect=AssertionError('must not reacquire')):
            with self.assertRaises(ValueError):
                self.custody.before_recheck()

    def test_thread_bound(self):
        self.prepare()
        errors = []
        def other_thread():
            try:
                self.custody.before_recheck()
            except ValueError as error:
                errors.append(error)
        thread = threading.Thread(target=other_thread)
        thread.start()
        thread.join()
        self.assertEqual(len(errors), 1)

    def test_close_detaches_all_and_does_not_retry_recycled_descriptor(self):
        self.prepare()
        original_close = os.close
        target = self.custody._owned[-1]
        calls, recycled = [], []
        def interrupted(fd):
            calls.append(fd)
            original_close(fd)
            if fd == target:
                replacement = os.open('/dev/null', os.O_RDONLY)
                self.assertEqual(replacement, target)
                recycled.append(replacement)
                raise OSError(errno.EINTR, 'ambiguous close')
        with patch.object(P.os, 'close', side_effect=interrupted):
            with self.assertRaises(OSError):
                self.custody.close()
            self.custody.close()
        self.assertEqual(calls.count(target), 1)
        self.assertEqual(self.custody._owned, [])
        os.fstat(recycled[0])
        original_close(recycled[0])
        self.inspection.recheck()


if __name__ == '__main__':
    unittest.main()
