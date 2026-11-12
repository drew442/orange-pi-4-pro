"""Real readonly tmpfs records/flocks with explicit physical/account substitutions.

No board, account database, VM, systemd or storage-activation evidence is claimed.
"""
import copy
import io
from contextlib import ExitStack, redirect_stderr
import fcntl
import importlib.util
import os
from pathlib import Path
import tempfile
import threading
import unittest
import uuid
from unittest.mock import patch
from types import SimpleNamespace

BASE = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('prepare_boot_test', BASE / 'scripts/prepare-guest-boot.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
A = M.ADMISSION
REAL_CONFIGS = M._configs
REAL_READY = M._ready
REAL_RESTORE_CENSUS = M.restore_census
STORAGE = dict(state_uuid='22222222-2222-4222-8222-222222222222',
               guest_uuid='33333333-3333-4333-8333-333333333333', nvme_serial='SSD-123')
VALUE = dict(schema=1, profile=M.BOOT_POLICY.SCHEMA.PROFILE, target='/dev/nvme0n1',
             expected_model='Test NVMe', expected_bytes=250000000000, storage=STORAGE)
CONFIG = dict(schema=1, name='one', uid=60001, gid=60001, memory_mib=128,
              memory_max_mib=256, cpus=2, kernel=dict(bytes=100, sha256='a'*64),
              initrd=dict(bytes=200, sha256='b'*64), disk=dict(bytes=1024, sha256='c'*64))
RAM = 12 * 1024**3


class Boundary(unittest.TestCase):
    def test_partial_constructors_preserve_primary_and_cleanup_failures(self):
        for constructor, arguments in (
                (M.RamDirectory, (M.UNIT_DIRECTORY, 0o755, lambda: None)),
                (M.Source, (0, 'record.json', 0o400, 100, lambda: None))):
            with self.subTest(constructor=constructor.__name__):
                with patch.object(M.os, 'open', side_effect=OSError('primary open failure')), \
                        patch.object(constructor, 'close', side_effect=OSError('cleanup failure')):
                    with self.assertRaises(M.CleanupError) as caught:
                        constructor(*arguments)
                detail = M.failure_detail(caught.exception)['receipt']
                self.assertIn('primary open failure', detail['primary_error']['error'])
                self.assertIn('cleanup failure', detail['cleanup_error']['error'])

    def test_resource_failure_retains_primary_publication_and_cleanup(self):
        primary = M.STORE.PublicationError(dict(stage='directory-fsync',
            mutation_possible=True, published=True, durable=False, uncertain=True),
            OSError('publication acknowledgement lost'))
        def failed_close():
            raise OSError('source close uncertain')
        with self.assertRaises(M.CleanupError) as caught:
            with M.Resources() as resources:
                resources.callback(failed_close)
                raise primary
        detail = M.failure_detail(caught.exception)['receipt']
        receipt = detail['primary_error']['receipt']
        self.assertTrue(receipt['published'])
        self.assertTrue(receipt['uncertain'])
        self.assertFalse(receipt['durable'])
        self.assertEqual(receipt['stage'], 'directory-fsync')
        self.assertIn('source close uncertain', detail['cleanup_error']['error'])

    def test_unprivileged_is_bounded_failure(self):
        with patch.object(M.os, 'getresuid', return_value=(1000, 1000, 1000)):
            result = M.prepare()
        self.assertFalse(result['success'])
        self.assertFalse(result['admission_open_acknowledged'])
        self.assertIn('actual root', result['error'])
        self.assertLess(len(M.encoded(result)), M.RECORD_LIMIT)

    def test_wrong_thread(self):
        output = []
        thread = threading.Thread(target=lambda: output.append(M.prepare()))
        thread.start()
        thread.join()
        self.assertIn('original main thread', output[0]['error'])

    def test_publication_path_closed(self):
        with self.assertRaisesRegex(A.AdmissionError, 'invalid publication path'):
            M.RamDirectory('/tmp/elsewhere', 0o700, lambda: None)

    def test_cli_rejects_scope_flags(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            M.main(['--target', '/dev/sda'])


@unittest.skipUnless(os.getresuid() == (0, 0, 0), 'protected native RAM fixtures require root')
class Native(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        tmp = self.stack.enter_context(tempfile.TemporaryDirectory(dir='/run', prefix='a733-prepare-test-'))
        self.base = Path(tmp)
        self.base.chmod(0o755)
        self.boot = str(uuid.uuid4())
        self.events = []
        self.policy_bad = self.policy_close_bad = self.context_bad = False
        self.fail_at = None
        self.registry = dict(schema=1, profile=M.REGISTRY.PROFILE, generation=0, storage=STORAGE, guests=[])
        self.heads = {}
        self.ready_bad = set()
        self.accounts_calls = 0
        self.runtime = self.base / 'a733-admission'
        self.unit_dir = self.base / 'systemd/system'
        self.prepared_dir = self.base / 'a733-guest-boot'
        fixture = self

        class ProtectedPolicyFixture:
            def __init__(self, boot):
                fixture.assertEqual(boot, fixture.boot)
                fixture.events.append('policy-open')
            @property
            def value(self):
                return copy.deepcopy(VALUE)
            def check(self):
                M.require(not fixture.policy_bad, 'policy changed')
            def close(self):
                fixture.events.append('policy-close')
                if fixture.policy_close_bad:
                    raise OSError('policy close failed')

        class PhysicalContextFixture:
            def __init__(self, storage, lease, *, boot_id):
                fixture.assertEqual(storage, STORAGE)
                fixture.assertEqual(boot_id, fixture.boot)
                self.lease = lease
                self.context = self
                self.storage = copy.deepcopy(storage)
                self.admission = lease.admission
                self.boot_id = boot_id
            def guard(self):
                M.require(not fixture.context_bad, 'physical context changed')
                self.lease.recheck()
            def close(self):
                fixture.events.append('context-close')
            def _storage(self, value):
                M.require(value['storage'] == STORAGE, 'storage changed')

        patches = [patch.object(M, 'installed_guard', lambda boot: None),
            patch.object(A, '_boot_id', lambda: self.boot),
            patch.object(A, 'RUNTIME_PARENT', str(self.base)), patch.object(A, 'RUNTIME', str(self.runtime)),
            patch.object(M, 'UNIT_DIRECTORY', str(self.unit_dir)),
            patch.object(M, 'PREPARED_DIRECTORY', str(self.prepared_dir)),
            patch.object(M.BOOT_POLICY, 'Policy', ProtectedPolicyFixture),
            patch.object(M.NORMAL, 'local_kvm_gid', lambda: 108),
            patch.object(M.NORMAL, 'manager_state', lambda: 'running'),
            patch.object(M.MANUAL.CONTEXT, 'host_identity', lambda boot: None),
            patch.object(M, 'physical_memory', lambda: RAM),
            patch.object(M.MAINTENANCE, 'consumer_check', self.census),
            patch.object(M.ACTIVATE, 'make_plan', self.plan),
            patch.object(M.ACTIVATE, 'apply_bootstrap', self.activate),
            patch.object(M, 'restore_census', self.restore_census),
            patch.object(M.STATE, 'prepare', self.state),
            patch.object(M.ACCOUNTS, 'prepare', self.accounts),
            patch.object(M.LIFECYCLE, 'StateController', PhysicalContextFixture),
            patch.object(M, '_registry', self.read_registry),
            patch.object(M, '_observe_head', self.observe),
            patch.object(M, '_ready', self.ready),
            patch.object(M, '_configs', self.configs),
            patch.object(M.CP, 'reconcile', self.reconcile)]
        for change in patches:
            self.stack.enter_context(change)
        self.original_open = A.ClosureOwner.open
        def commit(owner):
            self.events.append('open')
            return self.original_open(owner)
        self.stack.enter_context(patch.object(A.ClosureOwner, 'open', commit))

    def entry(self, name, enabled=True):
        index = len(self.registry['guests'])
        entry = dict(name=name, uid=60001+index, gid=60001+index, manifest_sha256='a'*64,
                     enabled=enabled, shutdown_profile=M.REGISTRY.SHUTDOWN_PROFILE, fixture_profile='none')
        self.registry['guests'].append(entry)
        self.heads[name] = dict(status='ready', sequence=0)
        return entry

    def plan(self, args):
        self.assertEqual(vars(args), dict(target=VALUE['target'], expected_model=VALUE['expected_model'],
            expected_bytes=VALUE['expected_bytes'], expected_serial=STORAGE['nvme_serial'],
            guest_uuid=STORAGE['guest_uuid']))
        return dict(context=dict(state=dict(uuid=STORAGE['state_uuid'])))

    def authority(self, owner):
        self.assertIs(type(owner), A.ClosureOwner)
        self.assertIs(owner.admission.bootstrap_owner, owner)
        self.assertTrue(owner.lease.exclusive)
        owner.lease.recheck()
        A._held_lock(owner.lease.fd, 'WRITE', os.getpid())
        owner._matching()
        self.assertEqual(fcntl.fcntl(owner.lease.fd, fcntl.F_GETFL) & os.O_ACCMODE, os.O_RDONLY)
        if self.fail_at in self.events:
            raise ValueError('injected preparation failure')

    def activate(self, args, plan, owner, *, plan_check):
        self.events.append('activate')
        self.authority(owner)
        plan_check()
        return dict(success=True, cleanup_errors=[])

    def state(self, owner, storage):
        self.events.append('state')
        self.authority(owner)
        return dict(success=True, cleanup_errors=[])

    def restore_census(self, storage, lease, boot_id, guard):
        self.assertEqual((storage, boot_id), (STORAGE, self.boot))
        self.assertIs(lease, lease.admission.bootstrap_owner.lease)
        self.events.append('restore-census')
        self.authority(lease.admission.bootstrap_owner)
        guard()
        return dict(success=True, hardware_acceptance=False, transactions=[], completed=0, private_stages=0)

    def accounts(self, owner, storage):
        self.accounts_calls += 1
        self.events.append('accounts')
        self.authority(owner)
        return dict(success=True, cleanup_errors=[])

    def read_registry(self, controller, guard):
        guard()
        return copy.deepcopy(self.registry)

    def observe(self, controller, entry, guard):
        guard()
        return copy.deepcopy(self.heads[entry['name']])

    def ready(self, controller, entry, guard):
        guard()
        self.events.append('ready-' + entry['name'])
        M.require(entry['name'] not in self.ready_bad, 'bad disk cohort')

    def configs(self, controller, entries, guard):
        guard()
        configs = []
        for entry in entries:
            if entry['enabled']:
                configs.append({**copy.deepcopy(CONFIG), **{k: entry[k] for k in ('name', 'uid', 'gid')}})
        return configs

    def reconcile(self, controller, name):
        self.events.append('reconcile-' + name)
        self.heads[name] = dict(status='ready', sequence=1)
        return dict(success=True)

    def census(self):
        self.events.append('census')
        if self.fail_at == 'census':
            raise ValueError('KVM consumer remains')

    def gate(self):
        with A.Admission.open_existing(self.boot, 108, lambda: 'running') as admission:
            return admission._read()['state']

    def assert_closed(self):
        self.assertEqual(self.gate(), 'closed')
        self.assertNotIn('open', self.events)

    def test_success_real_ex_and_immutable_ram_publication(self):
        self.entry('one')
        result = M.prepare()
        self.assertTrue(result['success'], result)
        self.assertTrue(result['admission_open_acknowledged'])
        self.assertEqual(self.gate(), 'open')
        self.assertEqual(result['eligible'], ['one'])
        self.assertEqual(self.events.count('open'), 1)
        self.assertLess(self.events.index('context-close'), self.events.index('open'))
        self.assertLess(self.events.index('policy-close'), self.events.index('open'))
        self.assertLess(self.events.index('census'), self.events.index('open'))
        self.assertLess(self.events.index('activate'), self.events.index('restore-census'))
        self.assertLess(self.events.index('restore-census'), self.events.index('state'))
        self.assertLess(self.events.index('restore-census'), self.events.index('accounts'))
        receipt = self.prepared_dir / M.RECORD
        self.assertEqual(receipt.stat().st_mode & 0o777, 0o400)
        record = M.prepared_record(receipt.read_bytes())
        self.assertEqual(record['eligible'], ['one'])
        for name, pin in result['units'].items():
            path = self.unit_dir / name
            self.assertEqual(path.stat().st_mode & 0o777, 0o444)
            self.assertEqual(path.stat().st_nlink, 1)
            self.assertEqual(M.digest(path.read_bytes()), pin)
        self.assertFalse(any(self.unit_dir.glob('*.wants')))

    def test_incomplete_restore_refuses_before_state_markers_accounts_and_open(self):
        self.fail_at = 'restore-census'
        result = M.prepare()
        self.assertFalse(result['success'])
        self.assertEqual(result['stage'], 'restore-census')
        self.assertNotIn('state', self.events)
        self.assertNotIn('accounts', self.events)
        self.assertEqual(self.accounts_calls, 0)
        self.assertFalse((self.runtime / 'managed').exists())
        self.assert_closed()

    def test_restore_census_preserves_primary_and_controller_cleanup_failure(self):
        fixture = self
        class Controller:
            def __init__(self, storage, lease, *, boot_id):
                fixture.assertEqual((storage, boot_id), (STORAGE, fixture.boot))
                fixture.assertTrue(lease.exclusive)
                A._held_lock(lease.fd, 'WRITE', os.getpid())
                self.context = SimpleNamespace(guard=lease.recheck)
            def __enter__(self):
                return self
            def __exit__(self, *args):
                raise OSError('restore census controller close failed')
        with A.initialize(self.boot, 108, lambda: 'running') as admission:
            owner = admission.bootstrap_owner
            with patch.object(M.RESTORE.CUSTODY.LIFECYCLE, 'StateController', Controller), \
                    patch.object(M.RESTORE, 'census', side_effect=ValueError('incomplete restore intent')):
                with self.assertRaises(M.CleanupError) as caught:
                    REAL_RESTORE_CENSUS(STORAGE, owner.lease, self.boot, owner._matching)
            detail = caught.exception.receipt
            self.assertIn('incomplete restore intent', detail['primary_error']['error'])
            self.assertIn('controller close failed', detail['cleanup_error']['error'])
            self.assertEqual(admission._read()['state'], 'closed')

    def test_repeat_with_active_lease_is_readonly_noop(self):
        self.entry('one')
        self.assertTrue(M.prepare()['success'])
        before = {p: (p.stat().st_ino, p.stat().st_mtime_ns, p.read_bytes())
                  for p in [self.prepared_dir / M.RECORD, *self.unit_dir.iterdir()]}
        with A.Admission.open_existing(self.boot, 108, lambda: 'running') as admission:
            with admission.admit():
                self.events.clear()
                result = M.prepare()
        self.assertTrue(result['success'], result)
        self.assertTrue(result['noop'])
        self.assertEqual(self.accounts_calls, 1)
        self.assertNotIn('activate', self.events)
        self.assertNotIn('census', self.events)
        self.assertNotIn('open', self.events)
        for path, pin in before.items():
            self.assertEqual((path.stat().st_ino, path.stat().st_mtime_ns, path.read_bytes()), pin)

    def test_existing_closed_nonce_is_never_adopted(self):
        with A.initialize(self.boot, 108, lambda: 'running') as existing:
            nonce = existing.bootstrap_owner.nonce
            result = M.prepare()
            self.assertFalse(result['success'])
            self.assertEqual(existing._read()['owner_nonce'], nonce)
        self.assert_closed()
        self.assertNotIn('activate', self.events)

    def test_missing_receipt_refuses_open_noop(self):
        self.assertTrue(M.prepare()['success'])
        (self.prepared_dir / M.RECORD).unlink()
        self.events.clear()
        result = M.prepare()
        self.assertFalse(result['success'])
        self.assertFalse(result['noop'])
        self.assertNotIn('activate', self.events)
        self.assertEqual(self.gate(), 'open')

    def test_policy_absence_never_initializes(self):
        missing = FileNotFoundError(2, 'absent', M.BOOT_POLICY.NAME)
        with patch.object(M.BOOT_POLICY, 'Policy', side_effect=missing):
            result = M.prepare()
        self.assertFalse(result['success'])
        self.assertEqual(result['stage'], 'unconfigured')
        self.assertFalse(self.runtime.exists())

    def test_unsafe_policy_parent_is_not_optional(self):
        with patch.object(M.BOOT_POLICY, 'Policy', side_effect=FileNotFoundError(2, 'absent', 'config')):
            result = M.prepare()
        self.assertIn('config', result['error'])
        self.assertFalse(self.runtime.exists())

    def test_enabled_unavailable_guest_counts_in_budget(self):
        self.entry('one')
        self.entry('two')
        self.ready_bad.add('one')
        result = M.prepare()
        self.assertTrue(result['success'], result)
        self.assertEqual(result['eligible'], ['two'])
        self.assertIn('one', result['unavailable'])
        self.assertEqual(result['budget']['guest_count'], 2)
        self.assertEqual(result['budget']['requested_bytes'], 512 * 1024**2)
        self.assertNotIn('a733-managed-one.service', result['units'])

    def test_nonready_and_completed_ready_reconcile_before_verify(self):
        self.entry('one')
        self.entry('two')
        self.heads['one'] = dict(status='checkpoint', sequence=4)
        self.heads['two'] = dict(status='ready', sequence=8)
        result = M.prepare()
        self.assertTrue(result['success'], result)
        for name in ('one', 'two'):
            self.assertLess(self.events.index('reconcile-' + name), self.events.index('ready-' + name))

    def test_quarantine_is_unavailable_without_repair(self):
        self.entry('one')
        self.heads['one'] = dict(status='quarantined', sequence=3)
        result = M.prepare()
        self.assertTrue(result['success'], result)
        self.assertEqual(result['eligible'], [])
        self.assertNotIn('reconcile-one', self.events)
        self.assertNotIn('ready-one', self.events)

    def test_context_fault_is_global(self):
        self.entry('one')
        self.context_bad = True
        result = M.prepare()
        self.assertFalse(result['success'])
        self.assert_closed()

    def test_cleanup_policy_failure_prevents_open(self):
        self.policy_close_bad = True
        result = M.prepare()
        self.assertFalse(result['success'])
        self.assertIn('policy close failed', result['error'])
        self.assert_closed()
        self.assertEqual(self.events.count('policy-close'), 1)

    def test_census_failure_prevents_open(self):
        self.fail_at = 'census'
        result = M.prepare()
        self.assertFalse(result['success'])
        self.assert_closed()

    def test_activation_wrong_state_refuses_before_apply(self):
        with patch.object(M.ACTIVATE, 'make_plan', return_value=dict(context=dict(state=dict(uuid=str(uuid.uuid4()))))):
            result = M.prepare()
        self.assertFalse(result['success'])
        self.assertNotIn('activate', self.events)
        self.assert_closed()

    def test_acknowledged_open_with_close_error_is_preserved(self):
        original = A.Admission.close
        def failed_close(admission):
            initialized = admission.bootstrap_owner is not None
            original(admission)
            if initialized:
                raise OSError('owned fd cleanup uncertain')
        with patch.object(A.Admission, 'close', failed_close):
            result = M.prepare()
        self.assertFalse(result['success'])
        self.assertTrue(result['admission_open_acknowledged'])
        self.assertFalse(result['admission_open_uncertain'])
        self.assertEqual(result['stage'], 'cleanup-after-open')
        self.assertEqual(self.gate(), 'open')

    def test_uncertain_open_never_claims_closed(self):
        def uncertain(owner):
            self.original_open(owner)
            raise A.PublicationError('directory-fsync', True)
        with patch.object(A.ClosureOwner, 'open', uncertain):
            result = M.prepare()
        self.assertFalse(result['success'])
        self.assertFalse(result['admission_open_acknowledged'])
        self.assertTrue(result['admission_open_uncertain'])
        self.assertEqual(self.gate(), 'open')

    def test_unit_substitution_during_census_refuses_open(self):
        def replace():
            path = self.unit_dir / M.UNITS.SLICE_NAME
            data = path.read_bytes()
            path.unlink()
            path.write_bytes(data)
            path.chmod(0o444)
        with patch.object(M.MAINTENANCE, 'consumer_check', replace):
            result = M.prepare()
        self.assertFalse(result['success'])
        self.assertIn('metadata', result['error'])
        self.assert_closed()

    def test_existing_unit_mismatch_never_overwritten(self):
        self.unit_dir.mkdir(parents=True, mode=0o755)
        path = self.unit_dir / M.UNITS.SLICE_NAME
        path.write_bytes(b'foreign\n')
        path.chmod(0o444)
        result = M.prepare()
        self.assertFalse(result['success'])
        self.assertEqual(path.read_bytes(), b'foreign\n')
        self.assert_closed()

    def test_publication_rename_failure_preserves_primary_receipt(self):
        original = M.CONTEXT.ENROLL.rename_new
        def fail(source, old, destination, name):
            if name == M.UNITS.SLICE_NAME:
                raise OSError('injected rename failure')
            return original(source, old, destination, name)
        with patch.object(M.CONTEXT.ENROLL, 'rename_new', fail):
            result = M.prepare()
        self.assertFalse(result['success'])
        self.assertFalse(result['admission_open_attempted'])
        self.assertIn('injected rename failure', result['failure']['error'])
        self.assert_closed()
        self.assertEqual(list(self.unit_dir.iterdir()), [])

    def test_simultaneous_publication_and_directory_cleanup_failure(self):
        primary = M.STORE.PublicationError(dict(stage='directory-fsync',
            mutation_possible=True, published=True, durable=False, uncertain=True),
            OSError('publication acknowledgement lost'))
        original = M.RamDirectory.close
        def failed_close(directory):
            original(directory)
            raise OSError('directory close uncertain')
        with patch.object(M, 'publish', side_effect=primary), \
             patch.object(M.RamDirectory, 'close', failed_close):
            result = M.prepare()
        self.assertFalse(result['success'])
        self.assertFalse(result['admission_open_attempted'])
        receipt = result['failure']['receipt']['primary_error']['receipt']
        self.assertTrue(receipt['published'])
        self.assertTrue(receipt['uncertain'])
        self.assertFalse(receipt['durable'])
        self.assertEqual(receipt['stage'], 'directory-fsync')
        self.assertIn('directory close uncertain', result['cleanup_errors'][0]['error'])
        self.assert_closed()

    def test_postrename_fsync_failure_keeps_gate_closed(self):
        original = M.os.fsync
        def fail(fd):
            path = os.readlink('/proc/self/fd/' + str(fd))
            if path == str(self.unit_dir) and (self.unit_dir / M.UNITS.SLICE_NAME).exists():
                raise OSError('injected parent fsync failure')
            return original(fd)
        with patch.object(M.os, 'fsync', fail):
            result = M.prepare()
        self.assertFalse(result['success'])
        self.assertTrue((self.unit_dir / M.UNITS.SLICE_NAME).exists())
        self.assert_closed()

    def test_state_publication_error_preserves_primary_boundaries(self):
        primary = M.STORE.PublicationError(dict(operation='publish', stage='directory-fsync',
            success=False, mutation_possible=True, published=True, durable=False,
            uncertain=True, file_fsynced=True, directory_fsynced=False), OSError('fsync failed'))
        failure = M.STATE.PreparationError(dict(stage='registry-publication', success=False,
            cleanup_errors=[], publications=[], registry_raw=b'x'*500000), primary)
        with patch.object(M.STATE, 'prepare', side_effect=failure):
            result = M.prepare()
        receipt = result['failure']['receipt']['nested_error']
        self.assertTrue(receipt['published'])
        self.assertTrue(receipt['uncertain'])
        self.assertFalse(receipt['directory_fsynced'])
        self.assertLess(len(M.encoded(result)), M.RECORD_LIMIT)
        self.assert_closed()

    def test_actual_enabled_metadata_reader_hashes_no_payloads(self):
        root = self.base / 'guests'
        root.mkdir(mode=0o711)
        entry = self.entry('one')
        guest = root / 'one'
        guest.mkdir(mode=0o710)
        os.chown(guest, 0, entry['gid'])
        manifest = M.POLICY.MANIFEST
        files = {name: {**CONFIG[key], 'uid': CONFIG['uid'] if key == 'disk' else 0,
                 'gid': CONFIG['gid'] if key == 'disk' else 0,
                 'mode': '0600' if key == 'disk' else '0444'}
                 for key, name in manifest.FILENAMES.items()}
        raw = manifest.canonical(dict(schema=1, profile=manifest.PROFILE, config=CONFIG,
            guest_uuid=STORAGE['guest_uuid'], files=files))
        path = guest / 'enrollment.json'
        path.write_bytes(raw)
        path.chmod(0o400)
        entry['manifest_sha256'] = M.digest(raw)
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            controller = SimpleNamespace(context=SimpleNamespace(guest_fd=fd, storage=STORAGE))
            self.assertEqual(REAL_CONFIGS(controller, [entry], lambda: None), [CONFIG])
            self.assertEqual(list(guest.iterdir()), [path])
            entry['manifest_sha256'] = 'f'*64
            with self.assertRaisesRegex(A.AdmissionError, 'enrollment differs'):
                REAL_CONFIGS(controller, [entry], lambda: None)
        finally:
            os.close(fd)

    def test_actual_ready_helper_retains_guest_disk_registry_order(self):
        with A.initialize(self.boot, 108, lambda: 'running') as admission:
            owner = admission.bootstrap_owner
            admission.prepare_managed(owner.lease)
            entry = self.entry('one')
            with admission.guest_lock('one', owner.lease, create=True) as lock:
                admission.mark_managed('one', owner.lease, lock, guard=owner._matching)
            head_raw = M.HEAD.canonical(M.HEAD.initial_head(STORAGE,
                {key: entry[key] for key in ('name', 'uid', 'gid')}, entry['manifest_sha256']))
            events = []
            class InspectionFixture:
                def __init__(self, name, pin):
                    self.name, self.pin = name, pin
                def __enter__(self):
                    events.append('disk-open')
                    return self
                def __exit__(self, *unused):
                    events.append('disk-close')
            class HeadStore:
                def read(self, name, limit):
                    return head_raw
            def inspect(inspection, name, pin):
                self.assertEqual((inspection.name, inspection.pin), (name, pin))
                return {key: entry[key] for key in ('name', 'uid', 'gid')}
            controller = SimpleNamespace(admission=admission, lease=owner.lease,
                _inspection=inspect, _storage=lambda value: self.assertEqual(value['storage'], STORAGE),
                _head_store=lambda *unused: HeadStore(),
                _registry=lambda *unused: (object(), M.REGISTRY.canonical(self.registry), copy.deepcopy(self.registry)))
            def verify(controller, name, **arguments):
                events.append('verify')
                arguments['guest_lock'].recheck()
                self.assertIs(arguments['inspection'].__class__, InspectionFixture)
                self.assertEqual(arguments['head_data'], head_raw)
                self.assertEqual(arguments['registry'][2], self.registry)
            with patch.object(M.POLICY, 'Inspection', InspectionFixture), patch.object(M.LIFECYCLE, '_verify_ready', verify):
                REAL_READY(controller, entry, owner._matching)
            self.assertEqual(events, ['disk-open', 'verify', 'disk-close'])

    def test_hardlink_and_symlink_sources_refused(self):
        directory = self.base / 'private'
        directory.mkdir(mode=0o700)
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, fd)
        path = directory / 'data'
        path.write_bytes(b'one\n')
        path.chmod(0o400)
        os.link(path, directory / 'alias')
        with self.assertRaisesRegex(A.AdmissionError, 'unsafe protected'):
            M.Source(fd, 'data', 0o400, 100, lambda: None)
        (directory / 'alias').unlink()
        path.unlink()
        path.symlink_to('/etc/passwd')
        with self.assertRaises(OSError):
            M.Source(fd, 'data', 0o400, 100, lambda: None)


if __name__ == '__main__':
    unittest.main()
