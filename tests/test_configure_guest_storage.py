"""Actual root-owned RAM descriptors/flocks; substituted installed/physical probes.

These tests neither mount storage nor initialize admission nor launch a VM.
"""
import copy
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import fcntl
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('configure_storage_test', BASE / 'scripts/configure-guest-storage.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
A = M.ADMISSION
VALUE = dict(schema=1, profile=M.SCHEMA.PROFILE, target='/dev/nvme0n1',
             expected_model='Test NVMe', expected_bytes=250000000000,
             storage=dict(state_uuid='22222222-2222-4222-8222-222222222222',
                          guest_uuid='33333333-3333-4333-8333-333333333333',
                          nvme_serial='SSD-123'))


class Boundary(unittest.TestCase):
    def test_nonroot_refuses_before_discovery(self):
        with patch.object(M.os, 'getresuid', return_value=(1000, 1000, 1000)), \
                patch.object(M.ACTIVATE, 'snapshot') as snapshot:
            result = M.configure(VALUE)
        self.assertFalse(result['success'])
        self.assertIn('actual root', result['error'])
        snapshot.assert_not_called()

    def test_main_thread_required(self):
        values = []
        with patch.object(M.os, 'getresuid', return_value=(0, 0, 0)):
            thread = threading.Thread(target=lambda: values.append(M.configure(VALUE)))
            thread.start()
            thread.join()
        self.assertIn('main thread', values[0]['error'])

    def test_cli_requires_all_explicit_fields_and_rejects_force(self):
        for args in ([], ['--target', '/dev/sda'], ['--force']):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                M.main(args)

    def test_invalid_policy_never_observes_host(self):
        with patch.object(M.os, 'getresuid', return_value=(0, 0, 0)), \
                patch.object(M, 'installed_source') as installed:
            for key, value in [('target', '/dev/sda'), ('expected_bytes', True),
                               ('expected_model', '')]:
                invalid = copy.deepcopy(VALUE)
                invalid[key] = value
                self.assertFalse(M.configure(invalid)['success'])
            installed.assert_not_called()


@unittest.skipUnless(os.geteuid() == 0, 'actual root RAM/protected-file fixture')
class Native(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.base = Path(self.stack.enter_context(tempfile.TemporaryDirectory(dir='/run', prefix='configure-storage-')))
        self.state = self.base / 'state'
        self.state.mkdir(mode=0o700)
        self.config = self.state / 'config'
        self.config.mkdir(mode=0o700, exist_ok=True)
        self.leaf = self.config / M.POLICY.NAME
        self.runtime = self.base / 'a733-admission'
        self.snapshot = dict(state={'uuid': VALUE['storage']['state_uuid']}, disk={'target': VALUE['target']}, gpt={'fixture': True})
        self.observed = []
        for item in (
            patch.object(A, 'RUNTIME_PARENT', str(self.base)),
            patch.object(A, 'RUNTIME', str(self.runtime)),
            patch.object(M.POLICY, 'STATE', str(self.state)),
            patch.object(M, 'installed_source', return_value=('installed-fixture',)),
            patch.object(M.ACTIVATE, 'fixed_entries', return_value=[]),
            patch.object(M.ACTIVATE, 'snapshot', side_effect=self.observe),
            patch.object(M.PREP.MAINTENANCE, 'consumer_check'),
            patch.object(M.StateRoot, '_mount', return_value={'fixture': 1}),
            patch.object(M.POLICY.Policy, '_native'),
            patch.object(M.POLICY.Policy, '_storage'),
            patch.object(M.POLICY.Policy, '_mount_context', return_value={'fixture': 1}),
            patch.object(A, 'initialize', side_effect=AssertionError('must not initialize')),
            patch.object(M.ACTIVATE, 'apply_bootstrap', side_effect=AssertionError('must not mount')),
            patch.object(M, 'progress'),
        ):
            self.stack.enter_context(item)

    def observe(self, args, mounted):
        self.assertEqual(vars(args), dict(target=VALUE['target'], expected_model=VALUE['expected_model'],
            expected_serial=VALUE['storage']['nvme_serial'], expected_bytes=VALUE['expected_bytes'],
            guest_uuid=VALUE['storage']['guest_uuid']))
        self.assertFalse(mounted)
        self.observed.append(copy.deepcopy(vars(args)))
        return copy.deepcopy(self.snapshot)

    def run_configure(self, value=None):
        result = M.configure(VALUE if value is None else value)
        self.assertLess(len(json.dumps(result)), 16384)
        self.assertFalse(self.runtime.exists())
        return result

    def write_policy(self, data=None, mode=0o400):
        self.config.mkdir(mode=0o700, exist_ok=True)
        self.leaf.write_bytes(M.SCHEMA.canonical(VALUE) if data is None else data)
        self.leaf.chmod(mode)

    def assert_locked(self):
        fd = os.open(self.base / A.InitializationLock.NAME, os.O_RDONLY | os.O_CLOEXEC)
        try:
            self.assertEqual(fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE, os.O_RDONLY)
            with self.assertRaises(BlockingIOError):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)

    def test_publication_actual_policy_reader_and_idempotence(self):
        result = self.run_configure()
        self.assertTrue(result['success'], result)
        self.assertTrue(result['published'])
        self.assertTrue(result['durable'])
        self.assertEqual(self.leaf.read_bytes(), M.SCHEMA.canonical(VALUE))
        info = self.leaf.stat()
        self.assertEqual((info.st_mode & 0o777, info.st_uid, info.st_gid, info.st_nlink), (0o400, 0, 0, 1))
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o700)
        self.assertEqual(sorted(p.name for p in self.config.iterdir()), [M.POLICY.NAME])
        inode = info.st_ino
        again = self.run_configure()
        self.assertTrue(again['success'], again)
        self.assertTrue(again['idempotent'])
        self.assertFalse(again['published'])
        self.assertTrue(again['durable'])
        self.assertTrue(again['synchronization']['file_fsynced'])
        self.assertTrue(again['synchronization']['directory_fsynced'])
        self.assertEqual(self.leaf.stat().st_ino, inode)
        self.assertEqual(M.PREP.MAINTENANCE.consumer_check.call_count, 4)

    def test_existing_runtime_any_entry_refuses_even_identical_policy(self):
        self.write_policy()
        for kind in ('directory', 'file', 'symlink'):
            if kind == 'directory':
                self.runtime.mkdir()
            elif kind == 'file':
                self.runtime.touch()
            else:
                self.runtime.symlink_to('/missing')
            result = M.configure(VALUE)
            self.assertFalse(result['success'])
            self.assertIn('existing admission runtime', result['error'])
            self.assertEqual(self.observed, [])
            self.runtime.rmdir() if kind == 'directory' else self.runtime.unlink()

    def test_concurrent_initialization_exclusion(self):
        lock = A.InitializationLock.acquire()
        try:
            self.assertEqual(fcntl.fcntl(lock.fd, fcntl.F_GETFL) & os.O_ACCMODE, os.O_RDONLY)
            result = self.run_configure()
            self.assertFalse(result['success'])
            self.assertFalse(self.leaf.exists())
        finally:
            lock.close()

    def test_other_existing_policy_never_replaced(self):
        altered = copy.deepcopy(VALUE)
        altered['storage']['nvme_serial'] = 'different'
        self.write_policy(M.SCHEMA.canonical(altered))
        inode, data = self.leaf.stat().st_ino, self.leaf.read_bytes()
        result = self.run_configure()
        self.assertFalse(result['success'])
        self.assertIn('existing policy differs', result['error'])
        self.assertEqual((self.leaf.stat().st_ino, self.leaf.read_bytes()), (inode, data))

    def test_unsafe_or_malformed_existing_policy(self):
        for data, mode in ((b'{}\n', 0o400), (M.SCHEMA.canonical(VALUE), 0o600)):
            self.write_policy(data, mode)
            result = self.run_configure()
            self.assertFalse(result['success'])
            self.assertEqual(self.leaf.read_bytes(), data)

    def test_policy_symlink_hardlink_and_unsafe_config_refuse(self):
        self.config.mkdir(mode=0o700, exist_ok=True)
        other = self.state / 'other'
        other.write_bytes(M.SCHEMA.canonical(VALUE))
        other.chmod(0o400)
        self.leaf.symlink_to(other)
        self.assertFalse(self.run_configure()['success'])
        self.leaf.unlink()
        os.link(other, self.leaf)
        self.assertFalse(self.run_configure()['success'])
        self.leaf.unlink()
        self.config.chmod(0o755)
        self.assertFalse(self.run_configure()['success'])

    def test_state_uuid_and_changed_context_refuse(self):
        self.snapshot['state']['uuid'] = '44444444-4444-4444-8444-444444444444'
        result = self.run_configure()
        self.assertIn('STATE UUID differs', result['error'])
        self.assertFalse(self.leaf.exists())
        self.snapshot['state']['uuid'] = VALUE['storage']['state_uuid']
        self.observed.clear()
        original = self.observe
        def changing(args, mounted):
            result = original(args, mounted)
            if len(self.observed) > 1:
                result['gpt']['fixture'] = False
            return result
        M.ACTIVATE.snapshot.side_effect = changing
        result = self.run_configure()
        self.assertIn('storage context changed', result['error'])
        self.assertFalse(self.leaf.exists())

    def test_caller_change_is_not_permission(self):
        value = copy.deepcopy(VALUE)
        original = self.observe
        def mutate(args, mounted):
            result = original(args, mounted)
            value['expected_model'] = 'changed'
            return result
        M.ACTIVATE.snapshot.side_effect = mutate
        result = self.run_configure(value)
        self.assertIn('caller policy changed', result['error'])
        self.assertFalse(self.leaf.exists())

    def test_registry_missing_leaf_and_protected_storage_mismatch(self):
        guests = self.state / 'guests'
        guests.mkdir(mode=0o700)
        self.assertTrue(self.run_configure()['success'])
        self.leaf.unlink()
        registry = dict(schema=1, profile=M.REGISTRY.PROFILE, generation=0,
                        storage=copy.deepcopy(VALUE['storage']), guests=[])
        registry['storage']['nvme_serial'] = 'other'
        leaf = guests / 'autostart.json'
        leaf.write_bytes(M.REGISTRY.canonical(registry))
        leaf.chmod(0o400)
        result = self.run_configure()
        self.assertIn('registry storage differs', result['error'])
        self.assertFalse(self.leaf.exists())
        leaf.write_bytes(b'{}\n')
        self.assertFalse(self.run_configure()['success'])

    def test_link_acknowledgement_loss_keeps_committed_policy_receipt(self):
        self.config.mkdir(mode=0o700, exist_ok=True)
        real_link = os.link
        def lost(*args, **kwargs):
            real_link(*args, **kwargs)
            raise OSError('link acknowledgement lost')
        with patch.object(M.STORE.os, 'link', side_effect=lost):
            result = self.run_configure()
        self.assertFalse(result['success'])
        self.assertTrue(result['published'])
        self.assertTrue(result['uncertain'])
        self.assertFalse(result['durable'])
        self.assertTrue(result['publication']['destination_matches'])
        self.assertEqual(self.leaf.stat().st_nlink, 1)
        self.assertEqual(self.leaf.read_bytes(), M.SCHEMA.canonical(VALUE))

    def test_publication_fsync_failures_preserve_exact_receipts(self):
        self.config.mkdir(mode=0o700, exist_ok=True)
        original = os.fsync
        for failure_at in ('file-fsync', 'directory-fsync'):
            def fail(fd):
                link_exists = self.leaf.exists()
                is_file = not os.path.isdir('/proc/self/fd/' + str(fd))
                temporary = '.lifecycle-' in os.readlink('/proc/self/fd/' + str(fd))
                if ((failure_at == 'file-fsync' and is_file and temporary) or
                        (failure_at == 'directory-fsync' and link_exists and not is_file)):
                    raise OSError('injected fsync failure')
                return original(fd)
            with patch.object(M.STORE.os, 'fsync', side_effect=fail):
                result = self.run_configure()
            self.assertFalse(result['success'])
            self.assertEqual(result['publication']['stage'], failure_at)
            self.assertEqual(result['published'], failure_at == 'directory-fsync')
            self.assertFalse(result['durable'])
            self.assertTrue(result['uncertain'])
            self.assertEqual(sorted(path.name for path in self.config.iterdir()),
                             [M.POLICY.NAME] if result['published'] else [])
            if self.leaf.exists():
                self.leaf.unlink()

    def test_idempotent_fsync_failures_keep_inode_bytes_and_metadata(self):
        self.write_policy()
        original = os.fsync
        initial = self.leaf.stat()
        metadata = (initial.st_dev, initial.st_ino, initial.st_mode, initial.st_uid,
                    initial.st_gid, initial.st_nlink, initial.st_size,
                    initial.st_mtime_ns, initial.st_ctime_ns)
        data = self.leaf.read_bytes()
        for failure_at in ('file-fsync', 'directory-fsync'):
            def fail(fd):
                self.assert_locked()
                path = os.readlink('/proc/self/fd/' + str(fd))
                if ((failure_at == 'file-fsync' and path == str(self.leaf)) or
                        (failure_at == 'directory-fsync' and path == str(self.config))):
                    raise OSError('idempotent fsync uncertain')
                return original(fd)
            # config RootDirectory's create=True also syncs its existing directory;
            # inject only once the intended policy sync begins.
            real_sync = M.STORE.RootDirectory.sync
            def sync(store, *args, **kwargs):
                with patch.object(M.STORE.os, 'fsync', side_effect=fail):
                    return real_sync(store, *args, **kwargs)
            with patch.object(M.STORE.RootDirectory, 'sync', sync):
                result = self.run_configure()
            self.assertFalse(result['success'])
            self.assertTrue(result['idempotent'])
            self.assertFalse(result['published'])
            self.assertFalse(result['durable'])
            self.assertTrue(result['uncertain'])
            self.assertEqual(result['synchronization']['stage'], failure_at)
            self.assertTrue(result['synchronization']['file_closed'])
            self.assertEqual(self.leaf.read_bytes(), data)
            after = self.leaf.stat()
            self.assertEqual((after.st_dev, after.st_ino, after.st_mode, after.st_uid,
                after.st_gid, after.st_nlink, after.st_size, after.st_mtime_ns,
                after.st_ctime_ns), metadata)
            self.assertEqual(sorted(path.name for path in self.config.iterdir()), [M.POLICY.NAME])
        retry = self.run_configure()
        self.assertTrue(retry['success'], retry)
        self.assertTrue(retry['durable'])
        self.assertEqual(self.leaf.stat().st_ino, initial.st_ino)

    def test_idempotent_sync_close_failure_keeps_barrier_receipt(self):
        self.write_policy()
        inode, data = self.leaf.stat().st_ino, self.leaf.read_bytes()
        original_sync = M.STORE.RootDirectory.sync
        original_close = os.close
        def sync(store, *args, **kwargs):
            def close(fd):
                path = os.readlink('/proc/self/fd/' + str(fd))
                original_close(fd)
                if path == str(self.leaf):
                    self.assert_locked()
                    raise OSError('sync close acknowledgement lost')
            with patch.object(M.STORE.os, 'close', side_effect=close):
                return original_sync(store, *args, **kwargs)
        with patch.object(M.STORE.RootDirectory, 'sync', sync):
            result = self.run_configure()
        self.assertFalse(result['success'])
        self.assertTrue(result['idempotent'])
        self.assertTrue(result['cleanup_uncertain'])
        self.assertFalse(result['durable'])
        self.assertTrue(result['synchronization']['durable'])
        self.assertFalse(result['synchronization']['file_closed'])
        self.assertEqual((self.leaf.stat().st_ino, self.leaf.read_bytes()), (inode, data))

    def test_publication_temporary_cleanup_failure_retains_linked_policy(self):
        self.config.mkdir(mode=0o700, exist_ok=True)
        original = os.unlink
        def fail(path, *args, **kwargs):
            if str(path).startswith('.lifecycle-'):
                self.assert_locked()
                raise OSError('temporary unlink uncertain')
            return original(path, *args, **kwargs)
        with patch.object(M.STORE.os, 'unlink', side_effect=fail):
            result = self.run_configure()
        self.assertFalse(result['success'])
        self.assertTrue(result['published'])
        self.assertTrue(result['uncertain'])
        self.assertEqual(self.leaf.stat().st_nlink, 2)
        self.assertIn('temporary unlink uncertain', result['error'])
        self.assertTrue(result['cleanup_uncertain'])
        # A later invocation must refuse this unsafe committed inode.
        result = self.run_configure()
        self.assertFalse(result['success'])
        self.assertEqual(self.leaf.stat().st_nlink, 2)

    def test_runtime_appearing_before_publication_prohibits_mutation(self):
        original = M.STORE.RootDirectory.publish
        def runtime_appears(store, *args, **kwargs):
            self.runtime.symlink_to('/absent')
            return original(store, *args, **kwargs)
        with patch.object(M.STORE.RootDirectory, 'publish', runtime_appears):
            result = M.configure(VALUE)
        self.assertFalse(result['success'])
        self.assertIn('existing admission runtime', result['error'])
        self.assertFalse(self.leaf.exists())
        self.runtime.unlink()

    def test_actual_policy_reader_failure_after_commit_keeps_policy(self):
        with patch.object(M.POLICY.Policy, '_storage', side_effect=ValueError('physical UUID changed')):
            result = self.run_configure()
        self.assertFalse(result['success'])
        self.assertTrue(result['published'])
        self.assertTrue(result['durable'])
        self.assertEqual(self.leaf.read_bytes(), M.SCHEMA.canonical(VALUE))

    def test_cleanup_of_policy_source_config_and_state_under_exclusion(self):
        events = []
        originals = [(M.POLICY.Policy, 'close'), (M.PREP.Source, 'close'),
                     (M.STORE.RootDirectory, 'close'), (M.StateRoot, 'close')]
        for cls, name in originals:
            original = getattr(cls, name)
            label = cls.__name__
            def close(obj, original=original, label=label):
                self.assert_locked()
                events.append(label)
                return original(obj)
            self.stack.enter_context(patch.object(cls, name, close))
        result = self.run_configure()
        self.assertTrue(result['success'], result)
        self.assertEqual(events[-4:], ['Policy', 'Source', 'RootDirectory', 'StateRoot'])
        lock = A.InitializationLock.acquire()
        lock.close()

    def test_cleanup_failure_keeps_committed_policy_and_exclusion_until_cleanup(self):
        original = M.POLICY.Policy.close
        def close(obj):
            self.assert_locked()
            original(obj)
            raise OSError('policy close uncertain')
        with patch.object(M.POLICY.Policy, 'close', close):
            result = self.run_configure()
            again = self.run_configure()
        self.assertFalse(result['success'])
        self.assertTrue(result['published'])
        self.assertTrue(result['cleanup_uncertain'])
        self.assertFalse(result['durable'])
        self.assertFalse(again['success'])
        self.assertTrue(again['idempotent'])
        self.assertTrue(again['synchronization']['durable'])
        self.assertFalse(again['durable'])
        self.assertTrue(again['cleanup_uncertain'])
        self.assertEqual(self.leaf.read_bytes(), M.SCHEMA.canonical(VALUE))
        lock = A.InitializationLock.acquire()
        lock.close()

    def test_no_vm_consumers_before_mutation_or_success(self):
        M.PREP.MAINTENANCE.consumer_check.side_effect = ValueError('KVM consumer')
        result = self.run_configure()
        self.assertFalse(result['success'])
        self.assertFalse(self.leaf.exists())
        M.PREP.MAINTENANCE.consumer_check.side_effect = [None, ValueError('late KVM consumer')]
        result = self.run_configure()
        self.assertFalse(result['success'])
        self.assertTrue(result['published'])
        self.assertTrue(self.leaf.exists())

    def test_state_named_substitution_refuses(self):
        original = M.STORE.RootDirectory.publish
        def replace(store, *args, **kwargs):
            self.state.rename(self.base / 'old-state')
            self.state.mkdir(mode=0o700)
            return original(store, *args, **kwargs)
        with patch.object(M.STORE.RootDirectory, 'publish', replace):
            result = self.run_configure()
        self.assertFalse(result['success'])
        self.assertFalse(self.leaf.exists())

    def test_postpublication_source_constructor_read_and_close_failures_retained(self):
        original_read, original_close = M.PREP.Source.read, M.PREP.Source.close
        def read(source):
            if source.name == M.POLICY.NAME and self.leaf.exists():
                raise ValueError('postpublication primary read failure')
            return original_read(source)
        def close(source):
            fail = source.fd is not None and source.name == M.POLICY.NAME
            if fail:
                self.assert_locked()
            original_close(source)
            if fail:
                raise OSError('source constructor close acknowledgement lost')
        with patch.object(M.PREP.Source, 'read', read), patch.object(M.PREP.Source, 'close', close):
            result = self.run_configure()
        self.assertFalse(result['success'])
        self.assertTrue(result['published'])
        self.assertFalse(result['durable'])
        self.assertTrue(result['uncertain'])
        self.assertTrue(result['cleanup_uncertain'])
        self.assertTrue(result['publication']['durable'])
        detail = json.dumps(result['failure'])
        self.assertIn('postpublication primary read failure', detail)
        self.assertIn('source constructor close acknowledgement lost', detail)
        self.assertEqual(self.leaf.read_bytes(), M.SCHEMA.canonical(VALUE))

    def test_final_registry_source_close_uncertainty_retained(self):
        guests = self.state / 'guests'
        guests.mkdir(mode=0o700)
        registry = dict(schema=1, profile=M.REGISTRY.PROFILE, generation=0,
                        storage=VALUE['storage'], guests=[])
        registry_leaf = guests / 'autostart.json'
        raw = M.REGISTRY.canonical(registry)
        registry_leaf.write_bytes(raw)
        registry_leaf.chmod(0o400)
        original = os.close
        def close(fd):
            path = os.readlink('/proc/self/fd/' + str(fd))
            fail = path == str(registry_leaf) and self.leaf.exists()
            if fail:
                self.assert_locked()
            original(fd)
            if fail:
                raise OSError('final registry close acknowledgement lost')
        with patch.object(M.STORE.os, 'close', side_effect=close):
            result = self.run_configure()
        self.assertFalse(result['success'])
        self.assertTrue(result['published'])
        self.assertFalse(result['durable'])
        self.assertTrue(result['uncertain'])
        self.assertTrue(result['cleanup_uncertain'])
        self.assertTrue(result['publication']['durable'])
        self.assertIn('final registry close acknowledgement lost', json.dumps(result['failure']))
        self.assertEqual(self.leaf.read_bytes(), M.SCHEMA.canonical(VALUE))
        self.assertEqual(registry_leaf.read_bytes(), raw)

    def test_readonly_directory_constructor_keeps_primary_and_close_failure(self):
        original_check, original_close = M.MetadataDirectory.check, M.STORE.RootDirectory.close
        def check(directory):
            if directory._edges[-1][1] == 'config':
                raise ValueError('directory constructor primary failure')
            return original_check(directory)
        def close(directory):
            fail = bool(directory._edges) and directory._edges[-1][1] == 'config'
            if fail:
                self.assert_locked()
            original_close(directory)
            if fail:
                raise OSError('directory constructor close acknowledgement lost')
        with patch.object(M.MetadataDirectory, 'check', check), patch.object(M.STORE.RootDirectory, 'close', close):
            result = self.run_configure()
        self.assertFalse(result['success'])
        self.assertTrue(result['cleanup_uncertain'])
        detail = json.dumps(result['failure'])
        self.assertIn('directory constructor primary failure', detail)
        self.assertIn('directory constructor close acknowledgement lost', detail)
        self.assertFalse(self.leaf.exists())

    def test_state_constructor_keeps_primary_and_close_failure(self):
        original = os.close
        def close(fd):
            path = os.readlink('/proc/self/fd/' + str(fd))
            fail = path == str(self.state)
            if fail:
                self.assert_locked()
            original(fd)
            if fail:
                raise OSError('STATE constructor close acknowledgement lost')
        with patch.object(M.StateRoot, 'check', side_effect=ValueError('STATE constructor primary failure')), \
                patch.object(M.os, 'close', side_effect=close):
            result = self.run_configure()
        self.assertFalse(result['success'])
        self.assertTrue(result['cleanup_uncertain'])
        detail = json.dumps(result['failure'])
        self.assertIn('STATE constructor primary failure', detail)
        self.assertIn('STATE constructor close acknowledgement lost', detail)
        self.assertFalse(self.leaf.exists())

    def test_missing_config_is_not_created_and_cleanup_failure_is_not_absence(self):
        self.config.rmdir()
        result = self.run_configure()
        self.assertFalse(result['success'])
        self.assertFalse(self.config.exists())
        original = M.STORE.RootDirectory.close
        def close(directory):
            self.assert_locked()
            original(directory)
            raise OSError('missing directory descriptor close uncertain')
        with patch.object(M.STORE.RootDirectory, 'close', close):
            result = self.run_configure()
        self.assertFalse(result['success'])
        self.assertTrue(result['cleanup_uncertain'])
        self.assertFalse(result['durable'])
        self.assertFalse(self.config.exists())

    def test_cli_json_and_phase_output(self):
        args = ['--target', VALUE['target'], '--expected-model', VALUE['expected_model'],
                '--expected-serial', VALUE['storage']['nvme_serial'],
                '--expected-bytes', str(VALUE['expected_bytes']),
                '--state-uuid', VALUE['storage']['state_uuid'],
                '--guest-uuid', VALUE['storage']['guest_uuid']]
        out = io.StringIO()
        with redirect_stdout(out):
            code = M.main(args)
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out.getvalue())['success'])
        self.assertGreaterEqual(M.progress.call_count, 5)


if __name__ == '__main__':
    unittest.main()
