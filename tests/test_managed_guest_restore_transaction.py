"""Native protected files/flocks/rename/CAS; explicit platform substitutions.

No fixture result establishes physical boot, KVM, or hardware acceptance.
"""
import copy
import gzip
import importlib.util
import io
import os
import subprocess
import sys
import textwrap
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


T = module('restore_transaction_test', 'scripts/managed-guest-restore-transaction.py')
C = T.CUSTODY
ACTUAL_CONSUMER = C.consumer_check
ACTUAL_SYSTEM_READ = C.CONTEXT.read_system
F = module('restore_transaction_custody_fixture', 'tests/test_managed_guest_restore_custody.py')


@unittest.skipUnless(os.getresuid() == (0, 0, 0), 'native protected root fixture')
class Transaction(unittest.TestCase):
    initial = False
    fixture_profile = 'none'
    patched = F.Custody.patched
    assert_shutdown = F.Custody.assert_shutdown
    add_unrelated = F.Custody.add_unrelated
    def setUp(self):
        self.patched(F, 'C', new=C)
        self.patched(F, 'A', new=C.ADMISSION)
        F.Custody.setUp(self)
        stream = io.BytesIO()
        C.SENDER.send('one', self.pin, stream)
        self.archive = self.fixture.state / 'restore.tar.gz'
        self.archive.write_bytes(stream.getvalue())
        self.archive.chmod(0o400)
        self.archive_pin = T.SCHEMA.digest(stream.getvalue())

    def source(self):
        return T.SOURCE.Source(self.archive, self.archive_pin, self.pin,
                              {self.archive.stat().st_dev})

    def restore(self, transaction_id=None, resume=False):
        custody = C.ShutdownRestoreCustody(self.policy_value, 'one')
        try:
            custody.context.boot_id = custody.boot_id
            with self.source() as source:
                return T.RestoreTransaction(custody, source, transaction_id, resume).execute()
        finally:
            custody.close()

    def test_actual_all_task_consumer_census(self):
        with patch.object(C, 'consumer_check', new=ACTUAL_CONSUMER), \
                patch.object(C.CONTEXT, 'read_system', new=ACTUAL_SYSTEM_READ):
            # Boot UUID reads must use the real ID when the actual /proc reader
            # is active; physical host identity remains explicitly substituted.
            with patch.object(C.ADMISSION, '_boot_id', return_value=self.admission.boot_id), \
                    patch.object(C.CONTEXT, 'read_system', side_effect=lambda path, bound:
                        (self.admission.boot_id + '\n').encode() if str(path) == '/proc/sys/kernel/random/boot_id'
                        else ACTUAL_SYSTEM_READ(path, bound)):
                self.assertTrue(self.restore()['data_committed'])

    def test_readonly_completed_observer_after_later_legitimate_mutation(self):
        receipt = self.restore()
        self.fixture.disk.write_bytes(b'later legitimate bytes')
        self.fixture.head_path.write_bytes(b'later legitimate lifecycle')
        registry = C.REGISTRY.validate_registry(self.fixture.registry_path.read_bytes())
        registry['generation'] += 3
        self.fixture.registry_path.write_bytes(C.REGISTRY.canonical(registry))
        with C.BOOT_POLICY.Policy(self.admission.boot_id) as policy:
            with patch.object(C, 'ShutdownRestoreCustody', side_effect=AssertionError('admission access')), \
                    patch.object(T.SOURCE, 'Source', side_effect=AssertionError('archive access')), \
                    patch.object(T, '_rename', side_effect=AssertionError('write')):
                observed = T.observe_completed(policy, 'one', receipt['transaction_id'], self.archive_pin, self.pin)
        self.assertTrue(observed['historical'])
        self.assertTrue(observed['data_committed'])

    def test_fresh_runtime_without_managed_markers_or_guest_locks(self):
        import shutil
        for name in ('managed', 'guests'):
            shutil.rmtree(Path(C.ADMISSION.RUNTIME) / name)
        self.assertTrue(self.restore()['data_committed'])

    def test_explicit_resume_after_fresh_bootstrap_runtime_without_prepare(self):
        import shutil
        transaction_id = str(T.uuid.uuid4())
        with patch.object(T.RestoreTransaction, '_move', side_effect=OSError('interrupt')):
            with self.assertRaises(T.TransactionError):
                self.restore(transaction_id)
        self.admission.close()
        shutil.rmtree(C.ADMISSION.RUNTIME)
        self.admission = C.ADMISSION.initialize(self.admission.boot_id, 108, lambda: 'running')
        self.addCleanup(self.admission.close)
        # Model a failed initial bootstrap: release its actual EX without
        # prepare_managed, STATE.prepare, reopening, or adopting its nonce.
        self.admission.bootstrap_owner.lease.close()
        self.assertFalse((Path(C.ADMISSION.RUNTIME) / 'guests').exists())
        self.assertTrue(self.restore(transaction_id, True)['data_committed'])
        self.assert_shutdown()

    def test_generation_exhaustion_precedes_canonical_writes(self):
        registry = C.REGISTRY.validate_registry(self.fixture.registry_path.read_bytes())
        registry['generation'] = C.REGISTRY.MAX_GENERATION
        self.fixture.registry_path.write_bytes(C.REGISTRY.canonical(registry))
        before = self.fixture.disk.read_bytes(), self.fixture.head_path.read_bytes()
        with self.assertRaisesRegex(T.TransactionError, 'generation exhausted'):
            self.restore()
        self.assertEqual(before, (self.fixture.disk.read_bytes(), self.fixture.head_path.read_bytes()))

    def test_one_hash_pass_per_retained_file_binding(self):
        original = T.RestoreTransaction.hash
        calls = {}
        def hashing(transaction, fd, size, name):
            identity = (os.fstat(fd).st_dev, os.fstat(fd).st_ino)
            calls[identity] = calls.get(identity, 0) + 1
            return original(transaction, fd, size, name)
        with patch.object(T.RestoreTransaction, 'hash', new=hashing):
            self.assertTrue(self.restore()['data_committed'])
        self.assertGreater(len(calls), 5)
        self.assertEqual(set(calls.values()), {1})

    def test_semantic_read_failure_and_close_error_both_preserved(self):
        leaf = self.fixture.state / 'invalid-restore.json'
        leaf.write_bytes(b'invalid JSON')
        leaf.chmod(0o400)
        parent = os.open(self.fixture.state, T.DIR_FLAGS)
        self.addCleanup(os.close, parent)
        native = os.close
        def failed_close(fd):
            native(fd)
            raise OSError('injected record close error')
        with patch.object(T.os, 'close', side_effect=failed_close):
            with self.assertRaises(T.ReadError) as failed:
                T._read(parent, leaf.name, validate=T.SCHEMA.decode)
        self.assertIsInstance(failed.exception.primary, ValueError)
        self.assertTrue(failed.exception.receipt['cleanup_uncertain'])
        self.assertEqual(len(failed.exception.receipt['close_errors']), 1)

    def test_resume_syncs_visible_intent_and_ack_before_canonical_rename(self):
        rename, fsync = T._rename, os.fsync
        for lost_ack in ('intent.json', 'phase-1.json'):
            transaction_id = str(T.uuid.uuid4())
            def interrupted(*args):
                result = rename(*args)
                if args[3] == lost_ack:
                    raise OSError('injected immutable record parent-fsync acknowledgement loss')
                return result
            with patch.object(T, '_rename', side_effect=interrupted):
                with self.assertRaises(T.TransactionError):
                    self.restore(transaction_id)
            state = self.fixture.state / T.STATE_NAME / transaction_id
            intent_inode = (state / 'intent.json').stat().st_ino
            parent_inode = state.stat().st_ino
            ack_inode = (state / lost_ack).stat().st_ino
            synced, canonical = [], []
            def syncing(fd):
                synced.append(os.fstat(fd).st_ino)
                return fsync(fd)
            def publishing(*args):
                if args[1] in ('one', 'new-payload', 'new-lifecycle'):
                    self.assertIn(intent_inode, synced)
                    self.assertIn(ack_inode, synced)
                    self.assertIn(parent_inode, synced[synced.index(intent_inode) + 1:])
                    self.assertIn(parent_inode, synced[synced.index(ack_inode) + 1:])
                    canonical.append(args)
                return rename(*args)
            with patch.object(T.os, 'fsync', side_effect=syncing), \
                    patch.object(T, '_rename', side_effect=publishing):
                self.assertTrue(self.restore(transaction_id, True)['data_committed'])
            self.assertTrue(canonical)

    def test_preintent_shared_parent_requires_exact_private_mode(self):
        private = self.fixture.root / T.P5_NAME
        private.mkdir(mode=0o700)
        transaction_id = str(T.uuid.uuid4())
        stage = private / transaction_id
        stage.mkdir(mode=0o700)
        payload = stage / 'new-payload'
        payload.mkdir(mode=0o710)
        os.chown(payload, 0, 60001)
        shared = stage / 'new-lifecycle-parent'
        shared.mkdir(mode=0o700)
        custody = C.ShutdownRestoreCustody(self.policy_value, 'one')
        try:
            custody.context.boot_id = custody.boot_id
            self.assertEqual(T.census(custody.controller, custody.recheck)['private_stages'], [transaction_id])
            os.chown(payload, 0, 108)
            with self.assertRaisesRegex(T.TransactionError, 'unsafe private restore directory'):
                T.census(custody.controller, custody.recheck)
            os.chown(payload, 0, 60001)
            shared.chmod(0o710)
            with self.assertRaisesRegex(T.TransactionError, 'unsafe private restore directory'):
                T.census(custody.controller, custody.recheck)
        finally:
            custody.close()

    def test_provenance_read_and_partial_records_refuse_file_mount_mismatch(self):
        name = '.pending-' + str(T.uuid.uuid4())
        path = self.fixture.state / name
        path.write_bytes(T.SCHEMA.canonical(dict(schema=1, profile=T.SCHEMA.PROFILE,
            kind='phase', transaction_id=str(T.uuid.uuid4()), intent_sha256='0' * 64, phase=1)))
        path.chmod(0o400)
        parent = os.open(self.fixture.state, T.DIR_FLAGS)
        self.addCleanup(os.close, parent)
        with patch.object(T, '_mount', side_effect=lambda fd: 1 if fd == parent else 2):
            with self.assertRaisesRegex(ValueError, 'mounted'):
                T._read(parent, name, validate=T.SCHEMA.decode)
            with self.assertRaisesRegex(ValueError, 'mounted'):
                T._pending(parent, name)
        first = [None]
        def rebound(fd):
            if fd == parent:
                return 1
            if first[0] is None:
                first[0] = fd
            return 1 if fd == first[0] else 2
        with patch.object(T, '_mount', side_effect=rebound):
            with self.assertRaisesRegex(ValueError, 'final binding'):
                T._read(parent, name, validate=T.SCHEMA.decode)

    def test_actual_publication_retains_corrupt_old_disk(self):
        self.fixture.disk.write_bytes(b'corrupt old disk')
        old_registry = C.REGISTRY.validate_registry(self.fixture.registry_path.read_bytes())
        receipt = self.restore()
        self.assertTrue(receipt['success'])
        self.assertTrue(receipt['data_committed'])
        self.assertEqual(self.fixture.disk.read_bytes(), (b'A' if self.initial else b'B') * 512)
        retained = self.fixture.guest.parent / T.P5_NAME / receipt['transaction_id'] / 'old-payload' / 'disk.raw'
        self.assertEqual(retained.read_bytes(), b'corrupt old disk')
        current = C.REGISTRY.validate_registry(self.fixture.registry_path.read_bytes())
        self.assertEqual(current['generation'], old_registry['generation'] + 1)
        self.assertEqual(current['guests'], old_registry['guests'])
        self.assert_shutdown()

    def test_publication_completed_census_and_repeat(self):
        receipt = self.restore()
        with C.ShutdownRestoreCustody(self.policy_value, 'one') as custody:
            custody.context.boot_id = custody.boot_id
            census = T.census(custody.controller, custody.recheck)
            self.assertEqual(census['completed'], [receipt['transaction_id']])
        self.fixture.disk.write_bytes(b'later legitimate disk')
        self.fixture.head_path.write_bytes(b'later legitimate head')
        with patch.object(T, '_rename', side_effect=AssertionError('historical repeat wrote')):
            repeated = self.restore(receipt['transaction_id'], True)
        self.assertTrue(repeated['historical'])
        self.assertEqual(self.fixture.disk.read_bytes(), b'later legitimate disk')

    def test_absent_shared_lifecycle_parent(self):
        import shutil
        shutil.rmtree(self.fixture.head_path.parent.parent)
        self.assertTrue(self.restore()['data_committed'])

    def test_absent_payload_and_lifecycle(self):
        import shutil
        shutil.rmtree(self.fixture.guest)
        shutil.rmtree(self.fixture.head_path.parent)
        receipt = self.restore()
        self.assertTrue(receipt['data_committed'])

    def test_each_rename_interruption_explicit_resume(self):
        # Each test method gets one fixture; iterate the boundaries using explicit
        # new cohorts after each completed transaction.
        for boundary in range(1, 12):
            transaction_id = str(T.uuid.uuid4())
            original = T._rename
            count = [0]
            def interrupted(*args):
                count[0] += 1
                result = original(*args)
                if count[0] == boundary:
                    raise OSError('injected after rename')
                return result
            with patch.object(T, '_rename', side_effect=interrupted):
                with self.assertRaises(T.TransactionError):
                    self.restore(transaction_id)
            state = self.fixture.state / T.STATE_NAME / transaction_id
            if not (state / 'intent.json').exists():
                # Harmless pre-intent work is retained; only a NEW request can
                # authorize another transaction.
                continue
            result = self.restore(transaction_id, True)
            self.assertTrue(result['data_committed'])

    def test_incomplete_census_refuses(self):
        transaction_id = str(T.uuid.uuid4())
        with patch.object(T.RestoreTransaction, '_move', side_effect=OSError('interrupt')):
            with self.assertRaises(T.TransactionError):
                self.restore(transaction_id)
        with C.ShutdownRestoreCustody(self.policy_value, 'one') as custody:
            custody.context.boot_id = custody.boot_id
            with self.assertRaisesRegex(T.TransactionError, 'incomplete') as caught:
                T.census(custody.controller, custody.recheck)
            self.assertEqual(caught.exception.receipt['transaction_id'], transaction_id)
        self.assertTrue(self.restore(transaction_id, True)['data_committed'])

    def test_stale_original_bytes_refuse(self):
        transaction_id = str(T.uuid.uuid4())
        with patch.object(T.RestoreTransaction, '_move', side_effect=OSError('interrupt')):
            with self.assertRaises(T.TransactionError):
                self.restore(transaction_id)
        self.fixture.disk.write_bytes(b'new run without registry change')
        with self.assertRaisesRegex(T.TransactionError, 'stale restore'):
            self.restore(transaction_id, True)

    def test_unrelated_and_enabled_entries_preserved(self):
        guest, _pin = self.add_unrelated()
        before = self.fixture.registry()
        before['guests'][0]['enabled'] = True
        self.fixture.registry_path.write_bytes(C.REGISTRY.canonical(before))
        unrelated_bytes = {path.name: path.read_bytes() for path in guest.iterdir() if path.is_file()}
        self.assertTrue(self.restore()['data_committed'])
        self.assertEqual(self.fixture.registry()['guests'], before['guests'])
        self.assertEqual(unrelated_bytes, {path.name: path.read_bytes() for path in guest.iterdir() if path.is_file()})

    def test_cleanup_failure_preserves_durable_data_commit(self):
        original_close = T.RestoreTransaction.close
        native_close = os.close
        failed = [False]
        def close_transaction(transaction):
            def fail_once(fd):
                native_close(fd)
                if not failed[0]:
                    failed[0] = True
                    raise OSError('injected acknowledged close error')
            with patch.object(T.os, 'close', side_effect=fail_once):
                original_close(transaction)
        transaction_id = str(T.uuid.uuid4())
        with patch.object(T.RestoreTransaction, 'close', new=close_transaction):
            with self.assertRaises(T.TransactionError) as failed_result:
                self.restore(transaction_id)
        self.assertTrue(failed_result.exception.receipt['data_committed'])
        self.assertTrue(failed_result.exception.receipt['cleanup_uncertain'])
        self.assertFalse(failed_result.exception.receipt['success'])
        self.assertTrue(self.restore(transaction_id, True)['historical'])

    def test_impossible_lifecycle_publication_before_payload_refuses(self):
        transaction_id = str(T.uuid.uuid4())
        with patch.object(T.RestoreTransaction, '_move', side_effect=OSError('interrupt')):
            with self.assertRaises(T.TransactionError):
                self.restore(transaction_id)
        private = self.fixture.root / T.P5_NAME / transaction_id
        self.fixture.head_path.parent.rename(private / 'old-lifecycle')
        (private / 'new-lifecycle').rename(self.fixture.head_path.parent)
        with self.assertRaisesRegex(T.TransactionError, 'impossible cross-component'):
            self.restore(transaction_id, True)

    def test_new_request_refuses_existing_incomplete_intent(self):
        transaction_id = str(T.uuid.uuid4())
        with patch.object(T.RestoreTransaction, '_move', side_effect=OSError('interrupt')):
            with self.assertRaises(T.TransactionError):
                self.restore(transaction_id)
        with self.assertRaisesRegex(T.TransactionError, 'incomplete'):
            self.restore()
        self.assertTrue(self.restore(transaction_id, True)['data_committed'])

    def test_source_corruption_refuses_before_canonical_changes(self):
        before = self.fixture.disk.read_bytes()
        self.archive.write_bytes(self.archive.read_bytes()[:-1] + b'!')
        with C.ShutdownRestoreCustody(self.policy_value, 'one'):
            with self.assertRaises(ValueError):
                self.source()
        self.assertEqual(self.fixture.disk.read_bytes(), before)


class Initial(Transaction):
    initial = True

    def test_missing_parent_rename_and_fsync_lost_ack_resume(self):
        import shutil
        rename, fsync = T._rename, os.fsync
        for boundary in range(4):
            shutil.rmtree(self.fixture.head_path.parent.parent)
            transaction_id = str(T.uuid.uuid4())
            armed, count = [False], [0]
            def renamed(*args):
                result = rename(*args)
                if args[1] == 'new-lifecycle-parent':
                    armed[0] = True
                    if boundary == 0:
                        raise OSError('injected parent rename acknowledgement loss')
                return result
            def synced(fd):
                result = fsync(fd)
                if armed[0]:
                    count[0] += 1
                    if count[0] == boundary:
                        raise OSError('injected parent fsync acknowledgement loss')
                return result
            with patch.object(T, '_rename', side_effect=renamed), \
                    patch.object(T.os, 'fsync', side_effect=synced):
                with self.assertRaises(T.TransactionError):
                    self.restore(transaction_id)
            self.assertTrue(self.restore(transaction_id, True)['data_committed'])

    def test_every_owned_descriptor_close_interruption(self):
        stage_close, transaction_close, native_close = (T.RestoreTransaction._close_fd,
                                                       T.RestoreTransaction.close, os.close)
        count, fail_at = [0], [None]
        def close_fd(fd):
            native_close(fd)
            count[0] += 1
            if count[0] == fail_at[0]:
                raise OSError('injected owned descriptor close acknowledgement loss')
        def stage(transaction, fd):
            with patch.object(T.os, 'close', side_effect=close_fd):
                return stage_close(transaction, fd)
        def cleanup(transaction, primary=None):
            with patch.object(T.os, 'close', side_effect=close_fd):
                return transaction_close(transaction, primary)
        self.restore()
        with patch.object(T.RestoreTransaction, '_close_fd', new=stage), \
                patch.object(T.RestoreTransaction, 'close', new=cleanup):
            self.restore()
        boundaries = count[0]
        self.assertGreater(boundaries, 20)
        for boundary in range(1, boundaries + 1):
            count[0], fail_at[0] = 0, boundary
            transaction_id = str(T.uuid.uuid4())
            before = self.fixture.disk.read_bytes(), self.fixture.head_path.read_bytes()
            with patch.object(T.RestoreTransaction, '_close_fd', new=stage), \
                    patch.object(T.RestoreTransaction, 'close', new=cleanup):
                with self.assertRaises(T.TransactionError) as failed:
                    self.restore(transaction_id)
            self.assertTrue(failed.exception.receipt['cleanup_uncertain'])
            self.assertFalse(failed.exception.receipt['success'])
            state = self.fixture.state / T.STATE_NAME / transaction_id
            if (state / 'data-commit.json').exists():
                self.assertTrue(failed.exception.receipt['data_committed'])
                self.assertTrue(self.restore(transaction_id, True)['historical'])
            else:
                self.assertFalse((state / 'intent.json').exists())
                self.assertEqual(before, (self.fixture.disk.read_bytes(), self.fixture.head_path.read_bytes()))

    def test_every_postintent_fsync_interruption_and_explicit_resume(self):
        original_record, fsync = T.RestoreTransaction.record, os.fsync
        armed, count, fail_at = [False], [0], [None]
        def record(transaction, name, raw):
            result = original_record(transaction, name, raw)
            if name == 'intent.json':
                armed[0] = True
            return result
        def interrupted(fd):
            result = fsync(fd)
            if armed[0]:
                count[0] += 1
                if count[0] == fail_at[0]:
                    raise OSError('injected after fsync')
            return result
        # Establish the exact source cohort first; the producer fixture may
        # retain extra historical records outside the archived current cohort.
        self.restore()
        with patch.object(T.RestoreTransaction, 'record', new=record), \
                patch.object(T.os, 'fsync', side_effect=interrupted):
            self.restore()
        boundaries = count[0]
        self.assertGreater(boundaries, 20)
        for boundary in range(1, boundaries + 1):
            transaction_id = str(T.uuid.uuid4())
            armed[0], count[0], fail_at[0] = False, 0, boundary
            with patch.object(T.RestoreTransaction, 'record', new=record), \
                    patch.object(T.os, 'fsync', side_effect=interrupted):
                with self.assertRaises(T.TransactionError) as failure:
                    self.restore(transaction_id)
            self.assertTrue((self.fixture.state / T.STATE_NAME / transaction_id / 'intent.json').exists(),
                            f'boundary {boundary}: {failure.exception.receipt}')
            self.assertTrue(self.restore(transaction_id, True)['data_committed'])


@unittest.skipUnless(os.getresuid() == (0, 0, 0), 'native mount namespace requires root')
class MountedProvenance(unittest.TestCase):
    def test_same_filesystem_bind_record_and_partial_refuse(self):
        script = textwrap.dedent("""
            import importlib.util, os, pathlib, subprocess, tempfile, uuid
            spec = importlib.util.spec_from_file_location('mounted_restore',
                pathlib.Path.cwd() / 'scripts/managed-guest-restore-transaction.py')
            T = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(T)
            with tempfile.TemporaryDirectory() as location:
                root = pathlib.Path(location)
                source, target = root / 'source', root / ('.pending-' + str(uuid.uuid4()))
                raw = T.SCHEMA.canonical(dict(schema=1, profile=T.SCHEMA.PROFILE, kind='phase',
                    transaction_id=str(uuid.uuid4()), intent_sha256='0' * 64, phase=1))
                for path in (source, target):
                    path.write_bytes(raw)
                    path.chmod(0o400)
                subprocess.run(['mount', '--bind', str(source), str(target)], check=True)
                parent = os.open(root, T.DIR_FLAGS)
                try:
                    for operation in (lambda: T._read(parent, target.name, validate=T.SCHEMA.decode),
                                      lambda: T._pending(parent, target.name)):
                        try:
                            operation()
                        except ValueError as error:
                            assert 'mounted' in str(error), repr(error)
                        else:
                            raise AssertionError('same-FS mounted record accepted')
                finally:
                    os.close(parent)
                    subprocess.run(['umount', str(target)], check=True)
        """)
        result = subprocess.run(['unshare', '--mount', '--propagation', 'private',
                                 sys.executable, '-c', script], cwd=ROOT,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0 and 'Operation not permitted' in result.stderr and 'unshare:' in result.stderr:
            self.skipTest('CAP_SYS_ADMIN unavailable for isolated native mount namespace')
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
