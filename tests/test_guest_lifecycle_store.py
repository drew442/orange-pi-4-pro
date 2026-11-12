"""Actual filesystem/FD tests; ownership patch is test-only for nonroot CI."""
import hashlib
import importlib.util
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    'lifecycle_store', Path(__file__).resolve().parents[1] / 'scripts/guest-lifecycle-store.py')
STORE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STORE)


def digest(data):
    return hashlib.sha256(data).hexdigest()


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.parent = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, self.parent)
        # Production always requires root:root. Only metadata ownership is
        # substituted; real directories, syscalls, modes, links and FDs remain.
        self.owner_patch = patch.object(STORE, '_root_owned', lambda info:
                                        info.st_uid == os.getuid() and info.st_gid == os.getgid())
        self.owner_patch.start()
        self.addCleanup(self.owner_patch.stop)
        self.calls = 0
        self.allowed = True
        self.store = STORE.RootDirectory(self.parent, 'records', guard=self.guard, create=True)
        self.addCleanup(self.store.close)
        self.records = self.path / 'records'

    def guard(self):
        self.calls += 1
        if not self.allowed:
            raise STORE.StoreError('caller authority failed')

    def publish_error(self, name='record', data=b'new', **kwargs):
        with self.assertRaises(STORE.PublicationError) as caught:
            self.store.publish(name, data, **kwargs)
        self.assertFalse(caught.exception.receipt['success'])
        return caught.exception.receipt

    def test_create_read_pin_and_replace(self):
        result = self.store.publish('record', b'old')
        self.assertTrue(all(result[key] for key in
                            ('success', 'published', 'durable', 'file_fsynced',
                             'directory_fsynced', 'file_closed', 'temporary_removed')))
        self.assertEqual(result['bytes'], 3)
        self.assertEqual(result['sha256'], digest(b'old'))
        self.assertEqual(self.store.read('record', 3, digest(b'old')), b'old')
        self.assertEqual(stat.S_IMODE((self.records / 'record').stat().st_mode), 0o400)
        self.assertEqual((self.records / 'record').stat().st_nlink, 1)
        result = self.store.publish('record', b'new', expected_sha256=digest(b'old'))
        self.assertTrue(result['durable'])
        self.assertEqual(self.store.read('record', 3), b'new')

    def test_empty_record_and_missing(self):
        self.store.publish('empty', b'')
        self.assertEqual(self.store.read('empty', 0), b'')
        with self.assertRaises(FileNotFoundError):
            self.store.read('missing', 10)

    def test_immutable_collision_and_wrong_cas_preserve_old(self):
        self.store.publish('record', b'old')
        for kwargs in ({}, {'expected_sha256': digest(b'wrong')}):
            result = self.publish_error(**kwargs)
            self.assertFalse(result['mutation_possible'])
            self.assertFalse(result['published'])
        self.assertEqual(self.store.read('record', 3), b'old')
        self.assertEqual(list(self.records.iterdir()), [self.records / 'record'])

    def test_missing_cas_fails_before_mutation(self):
        receipt = self.publish_error(expected_sha256=digest(b'old'))
        self.assertFalse(receipt['mutation_possible'])
        self.assertFalse(list(self.records.iterdir()))

    def test_input_bounds_and_names(self):
        for name in ('', '.', '..', '../evil', '/absolute', 'x/y', 'x\x00', 'a' * 201):
            with self.assertRaises(STORE.StoreError):
                self.store.publish(name, b'x')
        for data in (bytearray(b'x'), memoryview(b'x'), 'x', b'x' * (STORE.MAX_BYTES + 1)):
            with self.assertRaises(STORE.StoreError):
                self.store.publish('record', data)
        for limit in (-1, True, STORE.MAX_BYTES + 1):
            with self.assertRaises(STORE.StoreError):
                self.store.read('record', limit)
        for pin in ('x', 'A' * 64, b'a' * 64):
            with self.assertRaises(STORE.StoreError):
                self.store.read('record', 10, pin)

    def test_unsafe_file_types_rejected_before_open(self):
        target = self.records / 'record'
        for kind in ('symlink', 'fifo', 'directory'):
            with self.subTest(kind=kind):
                if kind == 'symlink':
                    target.symlink_to('/dev/zero')
                elif kind == 'fifo':
                    os.mkfifo(target, 0o400)
                else:
                    target.mkdir(mode=0o700)
                with patch.object(STORE.os, 'open', side_effect=AssertionError('must not open')):
                    with self.assertRaises(STORE.StoreError):
                        self.store.read('record', 10)
                target.rmdir() if kind == 'directory' else target.unlink()

    def test_hardlinks_modes_size_and_pin_rejected(self):
        self.store.publish('record', b'old')
        os.link(self.records / 'record', self.records / 'alias')
        with self.assertRaises(STORE.StoreError):
            self.store.read('record', 10)
        (self.records / 'alias').unlink()
        for mode in (0o600, 0o440, 0o000):
            os.chmod(self.records / 'record', mode)
            with self.assertRaises(STORE.StoreError):
                self.store.read('record', 10)
        os.chmod(self.records / 'record', 0o400)
        for limit, pin in ((2, None), (10, digest(b'other'))):
            with self.assertRaises(STORE.StoreError):
                self.store.read('record', limit, pin)

    def test_guard_is_external_authority(self):
        self.allowed = False
        result = self.publish_error()
        self.assertFalse(result['mutation_possible'])
        self.assertFalse(list(self.records.iterdir()))
        with self.assertRaises(STORE.StoreError):
            self.store.read('missing', 10)

    def test_parent_permissions_refused(self):
        os.chmod(self.path, 0o777)
        with self.assertRaises(STORE.StoreError):
            self.store.read('missing', 10)
        with self.assertRaises(STORE.StoreError):
            STORE.RootDirectory(self.parent, 'other', guard=self.guard, create=True)
        self.assertFalse((self.path / 'other').exists())
        os.chmod(self.path, 0o700)

    def test_existing_directory_metadata_never_repaired(self):
        for kind in ('directory', 'symlink', 'fifo'):
            target = self.path / 'other'
            if kind == 'directory':
                target.mkdir(mode=0o755)
            elif kind == 'symlink':
                target.symlink_to(self.records)
            else:
                os.mkfifo(target, 0o700)
            with self.assertRaises(STORE.StoreError):
                STORE.RootDirectory(self.parent, 'other', guard=self.guard, create=True)
            target.rmdir() if kind == 'directory' else target.unlink()

    def test_child_independent_lifetime_and_full_ancestor_chain(self):
        child = self.store.child('child', create=True)
        self.addCleanup(child.close)
        self.store.close()
        child.publish('record', b'x')
        self.records.rename(self.path / 'old')
        self.records.mkdir(mode=0o700)
        result = self.publish_error_on(child)
        self.assertFalse(result['mutation_possible'])
        self.assertFalse((self.path / 'old' / 'child' / 'other').exists())

    def publish_error_on(self, store):
        with self.assertRaises(STORE.PublicationError) as caught:
            store.publish('other', b'x')
        return caught.exception.receipt

    def test_named_record_substitution_while_reading(self):
        self.store.publish('record', b'old')
        original = os.read
        def changed(fd, count):
            data = original(fd, count)
            target = self.records / 'record'
            if target.exists():
                target.unlink()
                target.write_bytes(b'evil')
                target.chmod(0o400)
            return data
        with patch.object(STORE.os, 'read', side_effect=changed):
            with self.assertRaises(STORE.StoreError):
                self.store.read('record', 10)

    def test_cas_rechecked_after_temporary_fsync(self):
        self.store.publish('record', b'old')
        original = os.fsync
        def changed(fd):
            original(fd)
            if stat.S_ISREG(os.fstat(fd).st_mode):
                target = self.records / 'record'
                target.chmod(0o600)
                target.write_bytes(b'stale')
                target.chmod(0o400)
        with patch.object(STORE.os, 'fsync', side_effect=changed):
            result = self.publish_error(expected_sha256=digest(b'old'))
        self.assertFalse(result['published'])
        self.assertEqual(self.store.read('record', 10), b'stale')

    def test_short_writes_completed_and_zero_write_refused(self):
        original = os.write
        with patch.object(STORE.os, 'write', side_effect=lambda fd, data: original(fd, data[:1])):
            self.store.publish('record', b'abcdef')
        self.assertEqual(self.store.read('record', 10), b'abcdef')
        with patch.object(STORE.os, 'write', return_value=0):
            result = self.publish_error('other')
        self.assertFalse(result['published'])
        self.assertFalse((self.records / 'other').exists())

    def test_file_and_directory_fsync_failures(self):
        original = os.fsync
        for directory in (False, True):
            def failed(fd):
                if stat.S_ISDIR(os.fstat(fd).st_mode) == directory:
                    raise OSError('injected fsync')
                return original(fd)
            with patch.object(STORE.os, 'fsync', side_effect=failed):
                result = self.publish_error('dir' if directory else 'file')
            self.assertEqual(result['published'], directory)
            self.assertEqual(result['file_fsynced'], directory)
            self.assertFalse(result['durable'])
            self.assertFalse(result['directory_fsynced'])
            self.assertTrue(result['uncertain'])

    def test_link_failure_before_and_after_action(self):
        original = os.link
        for acted in (False, True):
            def failed(*args, **kwargs):
                if acted:
                    original(*args, **kwargs)
                raise OSError('injected link')
            with patch.object(STORE.os, 'link', side_effect=failed):
                result = self.publish_error('yes' if acted else 'no')
            self.assertEqual(result['published'], acted)
            self.assertFalse(result['durable'])
            if acted:
                self.assertEqual(self.store.read('yes', 10), b'new')

    def test_rename_failure_before_and_after_action(self):
        original = os.replace
        for acted in (False, True):
            name = 'yes' if acted else 'no'
            self.store.publish(name, b'old')
            def failed(*args, **kwargs):
                if acted:
                    original(*args, **kwargs)
                raise OSError('injected replace')
            with patch.object(STORE.os, 'replace', side_effect=failed):
                result = self.publish_error(name, expected_sha256=digest(b'old'))
            self.assertEqual(result['published'], acted)
            self.assertFalse(result['durable'])
            self.assertEqual(self.store.read(name, 10), b'new' if acted else b'old')

    def test_link_collision_at_publication_preserves_competitor(self):
        original = os.link
        def collision(src, dst, **kwargs):
            target = self.records / dst
            target.write_bytes(b'other')
            target.chmod(0o400)
            return original(src, dst, **kwargs)
        with patch.object(STORE.os, 'link', side_effect=collision):
            result = self.publish_error()
        self.assertFalse(result['published'])
        self.assertFalse(result['durable'])
        self.assertEqual(self.store.read('record', 10), b'other')

    def test_close_failure_retains_publication_and_durability(self):
        original = os.close
        def failed(fd):
            original(fd)
            raise OSError('injected close after action')
        with patch.object(STORE.os, 'close', side_effect=failed):
            result = self.publish_error()
        self.assertTrue(result['published'])
        self.assertTrue(result['durable'])
        self.assertFalse(result['file_closed'])
        self.assertTrue(result['uncertain'])
        self.assertEqual(self.store.read('record', 10), b'new')

    def test_mkdir_failure_after_action_and_fsync_failure(self):
        original = os.mkdir
        def failed(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError('injected mkdir after action')
        with patch.object(STORE.os, 'mkdir', side_effect=failed):
            with self.assertRaises(STORE.PublicationError) as caught:
                self.store.child('partial', create=True)
        result = caught.exception.receipt
        self.assertTrue(result['mutation_possible'])
        self.assertFalse(result['durable'])
        self.assertTrue(result['uncertain'])
        self.assertTrue((self.records / 'partial').is_dir())
        with patch.object(STORE.os, 'fsync', side_effect=OSError('injected fsync')):
            with self.assertRaises(STORE.PublicationError) as caught:
                self.store.child('unflushed', create=True)
        self.assertTrue(caught.exception.receipt['published'])
        self.assertFalse(caught.exception.receipt['durable'])

    def test_temporary_substitution_not_deleted(self):
        original = os.fsync
        def changed(fd):
            original(fd)
            if stat.S_ISREG(os.fstat(fd).st_mode):
                for target in self.records.glob('.lifecycle-*'):
                    target.unlink()
                    target.write_bytes(b'foreign')
                    target.chmod(0o400)
        with patch.object(STORE.os, 'fsync', side_effect=changed):
            result = self.publish_error()
        self.assertFalse(result['published'])
        self.assertFalse(result['temporary_removed'])
        self.assertEqual(next(self.records.glob('.lifecycle-*')).read_bytes(), b'foreign')

    def test_temporary_cleanup_failure_is_not_retried(self):
        original = os.unlink
        calls = []
        def failed(name, **kwargs):
            if name.startswith('.lifecycle-'):
                calls.append(name)
                raise OSError('injected unlink')
            return original(name, **kwargs)
        with patch.object(STORE.os, 'unlink', side_effect=failed):
            result = self.publish_error()
        self.assertEqual(len(calls), 1)
        self.assertTrue(result['published'])
        self.assertFalse(result['durable'])
        self.assertFalse(result['temporary_removed'])
        self.assertEqual((self.records / 'record').read_bytes(), b'new')
        self.assertEqual((self.records / 'record').stat().st_nlink, 2)
        # Retain the partial destination and temporary for explicit recovery.

    def test_temporary_content_metadata_change_refused(self):
        original = os.fsync
        def changed(fd):
            original(fd)
            if stat.S_ISREG(os.fstat(fd).st_mode):
                target = next(self.records.glob('.lifecycle-*'))
                target.chmod(0o600)
                target.write_bytes(b'bad')
                target.chmod(0o400)
        with patch.object(STORE.os, 'fsync', side_effect=changed):
            result = self.publish_error()
        self.assertFalse(result['published'])
        self.assertFalse((self.records / 'record').exists())

    def test_replaced_directory_during_publication_preserves_partial_files(self):
        original = os.fsync
        def changed(fd):
            original(fd)
            if stat.S_ISREG(os.fstat(fd).st_mode):
                self.records.rename(self.path / 'detached')
                self.records.mkdir(mode=0o700)
        with patch.object(STORE.os, 'fsync', side_effect=changed):
            result = self.publish_error()
        self.assertFalse(result['published'])
        self.assertFalse(result['durable'])
        self.assertIn('cleanup_error', result)
        self.assertFalse(list(self.records.iterdir()))
        self.assertEqual(len(list((self.path / 'detached').iterdir())), 1)

    def test_creation_guard_failure_retains_partial_receipt(self):
        original = os.mkdir
        def changed(*args, **kwargs):
            original(*args, **kwargs)
            self.allowed = False
        with patch.object(STORE.os, 'mkdir', side_effect=changed):
            with self.assertRaises(STORE.PublicationError) as caught:
                self.store.child('partial', create=True)
        self.assertTrue(caught.exception.receipt['published'])
        self.assertFalse(caught.exception.receipt['durable'])
        self.assertTrue(caught.exception.receipt['uncertain'])
        self.allowed = True

    def test_sync_exact_record_establishes_durability_without_replacement(self):
        self.store.publish('record', b'old')
        before = (self.records / 'record').stat()
        entries = list(self.records.iterdir())
        original_fsync, original_close = os.fsync, os.close
        calls, closes = [], []
        def fsync(fd):
            calls.append('directory' if stat.S_ISDIR(os.fstat(fd).st_mode) else 'file')
            return original_fsync(fd)
        def close(fd):
            closes.append(fd)
            return original_close(fd)
        with patch.object(STORE.os, 'fsync', side_effect=fsync), \
                patch.object(STORE.os, 'close', side_effect=close), \
                patch.object(STORE.os, 'write', side_effect=AssertionError('no write')), \
                patch.object(STORE.os, 'replace', side_effect=AssertionError('no replace')), \
                patch.object(STORE.os, 'link', side_effect=AssertionError('no link')):
            result = self.store.sync('record', pin=digest(b'old'), limit=3)
        self.assertEqual(calls, ['file', 'directory'])
        self.assertEqual(len(closes), 1)
        with self.assertRaises(OSError):
            os.fstat(closes[0])
        self.assertEqual(result['operation'], 'sync-existing-record')
        self.assertEqual(result['sha256'], digest(b'old'))
        self.assertEqual(result['bytes'], 3)
        self.assertTrue(all(result[key] for key in
                            ('success', 'durable', 'file_fsynced', 'directory_fsynced', 'file_closed')))
        self.assertFalse(result['published'])
        self.assertFalse(result['uncertain'])
        self.assertEqual(STORE._metadata(before), STORE._metadata((self.records / 'record').stat()))
        self.assertEqual(entries, list(self.records.iterdir()))

    def test_sync_failed_publication_retry_adds_only_durability_barrier(self):
        original = os.fsync
        def failed(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError('directory flush failed')
            return original(fd)
        with patch.object(STORE.os, 'fsync', side_effect=failed):
            receipt = self.publish_error(data=b'old')
        self.assertTrue(receipt['published'])
        self.assertFalse(receipt['durable'])
        before = (self.records / 'record').stat()
        receipt = self.store.sync('record', pin=digest(b'old'))
        self.assertTrue(receipt['durable'])
        self.assertEqual(STORE._metadata(before), STORE._metadata((self.records / 'record').stat()))

    def test_sync_pin_bounds_missing_and_guard_fail_closed(self):
        self.store.publish('record', b'old')
        original_close = os.close
        for name, pin, limit, expected_close in (
                ('record', digest(b'wrong'), 10, 1),
                ('record', 'stale-pin', 10, 0),
                ('record', digest(b'old'), 2, 0),
                ('missing', digest(b'old'), 10, 0),
                ('record', digest(b'old'), -1, 0),
                ('record', digest(b'old'), True, 0),
                ('record', digest(b'old'), STORE.MAX_BYTES + 1, 0)):
            closes = []
            def close(fd):
                closes.append(fd)
                return original_close(fd)
            with patch.object(STORE.os, 'close', side_effect=close), \
                    patch.object(STORE.os, 'fsync', side_effect=AssertionError('no fsync')):
                with self.assertRaises(STORE.PublicationError) as caught:
                    self.store.sync(name, pin=pin, limit=limit)
            result = caught.exception.receipt
            self.assertFalse(result['success'])
            self.assertFalse(result['durable'])
            self.assertTrue(result['uncertain'])
            self.assertEqual(len(closes), expected_close)
            self.assertEqual(result['file_closed'], bool(expected_close))
        self.allowed = False
        with patch.object(STORE.os, 'open', side_effect=AssertionError('no open')):
            with self.assertRaises(STORE.PublicationError) as caught:
                self.store.sync('record', pin=digest(b'old'))
        self.assertFalse(caught.exception.receipt['file_fsynced'])
        self.allowed = True

    def test_sync_refuses_nonregular_hardlinked_or_unsafe_metadata_before_open(self):
        target = self.records / 'record'
        for kind in ('fifo', 'symlink', 'directory', 'hardlink', 'writable'):
            if kind == 'fifo':
                os.mkfifo(target, 0o400)
            elif kind == 'symlink':
                target.symlink_to('/dev/zero')
            elif kind == 'directory':
                target.mkdir(mode=0o700)
            else:
                target.write_bytes(b'old')
                target.chmod(0o600 if kind == 'writable' else 0o400)
                if kind == 'hardlink':
                    os.link(target, self.records / 'alias')
            with patch.object(STORE.os, 'open', side_effect=AssertionError('must not open')):
                with self.assertRaises(STORE.PublicationError) as caught:
                    self.store.sync('record', pin=digest(b'old'))
            self.assertIsInstance(caught.exception.cause, STORE.StoreError)
            self.assertFalse(caught.exception.receipt['durable'])
            target.rmdir() if kind == 'directory' else target.unlink()
            if kind == 'hardlink':
                (self.records / 'alias').unlink()

    def test_sync_fsync_failures_before_and_after_action_have_no_false_facts(self):
        self.store.publish('record', b'old')
        original_fsync, original_close = os.fsync, os.close
        for directory in (False, True):
            for acted in (False, True):
                calls, closes = [], []
                def fsync(fd):
                    isdir = stat.S_ISDIR(os.fstat(fd).st_mode)
                    calls.append(isdir)
                    if isdir == directory:
                        if acted:
                            original_fsync(fd)
                        raise OSError('injected fsync')
                    return original_fsync(fd)
                def close(fd):
                    closes.append(fd)
                    return original_close(fd)
                with patch.object(STORE.os, 'fsync', side_effect=fsync), \
                        patch.object(STORE.os, 'close', side_effect=close):
                    with self.assertRaises(STORE.PublicationError) as caught:
                        self.store.sync('record', pin=digest(b'old'))
                result = caught.exception.receipt
                self.assertEqual(calls, [False, True] if directory else [False])
                self.assertEqual(len(closes), 1)
                self.assertTrue(result['file_closed'])
                self.assertEqual(result['file_fsynced'], directory)
                self.assertFalse(result['directory_fsynced'])
                self.assertFalse(result['durable'])
                self.assertFalse(result['success'])
                self.assertTrue(result['uncertain'])
                with self.assertRaises(OSError):
                    os.fstat(closes[0])

    def test_sync_close_after_action_failure_is_not_retried_or_claimed_closed(self):
        self.store.publish('record', b'old')
        original = os.close
        closes = []
        def close(fd):
            closes.append(fd)
            original(fd)
            raise OSError('injected close after action')
        with patch.object(STORE.os, 'close', side_effect=close):
            with self.assertRaises(STORE.PublicationError) as caught:
                self.store.sync('record', pin=digest(b'old'))
        result = caught.exception.receipt
        self.assertEqual(len(closes), 1)
        self.assertFalse(result['file_closed'])
        self.assertTrue(result['file_fsynced'])
        self.assertTrue(result['directory_fsynced'])
        self.assertTrue(result['durable'])
        self.assertFalse(result['success'])
        self.assertTrue(result['uncertain'])
        self.assertEqual(result['stage'], 'close')

    def test_sync_named_inode_substitution_after_each_fsync_rejected(self):
        original = os.fsync
        for directory in (False, True):
            name = 'dir' if directory else 'file'
            self.store.publish(name, b'old')
            def changed(fd):
                original(fd)
                if stat.S_ISDIR(os.fstat(fd).st_mode) == directory:
                    target = self.records / name
                    target.unlink()
                    target.write_bytes(b'old')
                    target.chmod(0o400)
            with patch.object(STORE.os, 'fsync', side_effect=changed):
                with self.assertRaises(STORE.PublicationError) as caught:
                    self.store.sync(name, pin=digest(b'old'))
            result = caught.exception.receipt
            self.assertFalse(result['success'])
            self.assertFalse(result['durable'])
            self.assertTrue(result['file_closed'])
            self.assertTrue(result['uncertain'])
            self.assertEqual(self.store.read(name, 10), b'old')

    def test_sync_guard_invalid_after_each_fsync_rejected(self):
        self.store.publish('record', b'old')
        original = os.fsync
        for directory in (False, True):
            def changed(fd):
                original(fd)
                if stat.S_ISDIR(os.fstat(fd).st_mode) == directory:
                    self.allowed = False
            with patch.object(STORE.os, 'fsync', side_effect=changed):
                with self.assertRaises(STORE.PublicationError) as caught:
                    self.store.sync('record', pin=digest(b'old'))
            result = caught.exception.receipt
            self.assertFalse(result['success'])
            self.assertFalse(result['durable'])
            self.assertTrue(result['file_closed'])
            self.assertTrue(result['uncertain'])
            self.allowed = True

    def test_root_owner_enforced_without_test_patch(self):
        self.owner_patch.stop()
        try:
            self.assertFalse(STORE._root_owned(SimpleNamespace(st_uid=1, st_gid=0)))
            self.assertFalse(STORE._root_owned(SimpleNamespace(st_uid=0, st_gid=1)))
            if os.getuid() != 0 or os.getgid() != 0:
                with self.assertRaises(STORE.StoreError):
                    STORE.RootDirectory(self.parent, 'other', guard=self.guard, create=True)
            else:
                other = STORE.RootDirectory(self.parent, 'other', guard=self.guard, create=True)
                other.close()
        finally:
            self.owner_patch.start()


if __name__ == '__main__':
    unittest.main()
