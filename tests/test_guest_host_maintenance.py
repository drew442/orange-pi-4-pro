"""Native root tmpfs/flocks with explicit board, PID1 and physical STATE fixtures.

Real lifecycle/manifest/stopped disk and protected registry checks are retained.
These fixtures establish no physical boot, guest cleanliness or KVM acceptance.
"""
import copy
import errno
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import shutil
import tempfile
import threading
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('maintenance_tests', BASE / 'scripts/guest-host-maintenance.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
A, L, P = M.ADMISSION, M.LIFECYCLE, M.LIFECYCLE.POLICY
STORAGE = dict(state_uuid='22222222-2222-4222-8222-222222222222',
               guest_uuid='33333333-3333-4333-8333-333333333333', nvme_serial='SSD-123')


class Boundaries(unittest.TestCase):
    def test_nonroot_before_host_reads(self):
        with patch.object(M.os, 'getresuid', return_value=(1000, 1000, 1000)), \
                patch.object(M.CONTEXT, 'read_system') as read:
            with self.assertRaisesRegex(M.MaintenanceError, 'actual root'):
                M.InstalledMaintenance(STORAGE)
            read.assert_not_called()

    def test_no_storage_or_bypass_api(self):
        with self.assertRaises(TypeError):
            M.InstalledMaintenance()
        with self.assertRaises(TypeError):
            M.InstalledMaintenance(STORAGE, ram=True)
        with self.assertRaises(M.MaintenanceError):
            M.InstalledMaintenance({**STORAGE, 'bypass': True})

    def test_fixed_bounded_managed_only_stop(self):
        with patch.object(M.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            M.stop_managed()
        args, kwargs = run.call_args
        self.assertEqual(args[0], ['/usr/bin/systemctl', 'stop', 'a733-managed-*.service'])
        self.assertEqual(kwargs, dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, env=M.ENVIRONMENT, timeout=420, check=False))
        with patch.object(M.subprocess, 'run', return_value=SimpleNamespace(returncode=1)):
            with self.assertRaises(A.AdmissionError):
                M.stop_managed()


@unittest.skipUnless(os.getresuid() == (0, 0, 0), 'protected native tmpfs fixture requires root')
class Installed(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir='/run', prefix='a733-maintenance-tests-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root, self.state = self.base / 'guests', self.base / 'state'
        self.root.mkdir(mode=0o711)
        self.state.mkdir(mode=0o700)
        self.guest = self.root / 'one'
        self.guest.mkdir(mode=0o710)
        os.chown(self.guest, 0, 60001)
        self.boot = str(uuid.uuid4())
        self.events = []
        self.bad_context = False
        fixture = self

        class PhysicalStateFixture:
            def __init__(self, storage, lease, *, boot_id):
                fixture.assertEqual(storage, STORAGE)
                fixture.assertEqual(boot_id, fixture.boot)
                self.storage, self.lease = copy.deepcopy(storage), lease
                self.state_fd = os.open(fixture.state, os.O_RDONLY | os.O_DIRECTORY)
                self.guest_fd = os.open(fixture.root, os.O_RDONLY | os.O_DIRECTORY)
                self.closed = False
            def guard(self):
                M.require(not self.closed and not fixture.bad_context, 'fixture physical placement changed')
                self.lease.recheck()
            def close(self):
                if not self.closed:
                    self.closed = True
                    os.close(self.guest_fd)
                    os.close(self.state_fd)
            def __enter__(self):
                self.guard()
                return self
            def __exit__(self, *args):
                self.close()

        for substitute in (
                patch.object(A, 'RUNTIME_PARENT', self.tmp.name),
                patch.object(A, 'RUNTIME', str(self.base / 'a733-admission')),
                patch.object(M.CONTEXT, 'read_system', return_value=(self.boot + '\n').encode()),
                patch.object(M.CONTEXT, 'host_identity'),
                patch.object(M.NORMAL.CONTEXT.MANAGEMENT, 'runtime_guard'),
                patch.object(M.NORMAL, 'manager_state', return_value='running'),
                patch.object(M.NORMAL.ACCOUNTS, 'snapshot', return_value={'group': b'kvm:x:108:\n'}),
                patch.object(M.CONTEXT, 'HostContext', PhysicalStateFixture),
                patch.object(P.ENROLL, 'ROOT', str(self.root)),
                patch.object(P.MANAGEMENT, 'runtime_guard'), patch.object(P, 'host_namespace'),
                patch.object(P.ENROLL, 'probe_uuid'), patch.object(P, 'service_state', return_value={}),
                patch.object(P, 'disk_consumers', return_value=[]),
                patch.object(P.ACCOUNTS, 'validate_accounts'), patch.object(P.ACCOUNTS, 'snapshot', return_value={}),
                patch.object(P.STORAGE, 'verify_guest_directory', return_value={'namespace': 'physical-fixture'}),
                patch.object(P.STORAGE, 'verify_guest_storage', return_value={'namespace': 'physical-fixture'}),
                patch.object(M, 'stop_managed', side_effect=lambda: self.events.append('stop')),
                patch.object(M, 'consumer_check', side_effect=lambda: self.events.append('census'))):
            substitute.start()
            self.addCleanup(substitute.stop)
        self.admission = A.initialize(self.boot, 108, lambda: 'running')
        self.addCleanup(self.admission.close)
        owner = self.admission.bootstrap_owner
        self.config = dict(schema=1, name='one', uid=60001, gid=60001,
                           memory_mib=64, memory_max_mib=128, cpus=1)
        files = {}
        for key, filename, data in (('kernel', 'Image', b'kernel'), ('initrd', 'initramfs.cpio.gz', b'initrd'),
                                    ('disk', 'disk.raw', b'A' * 512)):
            path = self.guest / filename
            path.write_bytes(data)
            disk = key == 'disk'
            path.chmod(0o600 if disk else 0o444)
            if disk:
                os.chown(path, 60001, 60001)
            self.config[key] = M.CP.reference(data)
            files[filename] = dict(**self.config[key], uid=60001 if disk else 0,
                                   gid=60001 if disk else 0, mode='0600' if disk else '0444')
        manifest = dict(schema=1, profile=M.CP.MANIFEST.PROFILE, guest_uuid=STORAGE['guest_uuid'],
                        config=self.config, files=files)
        self.manifest = self.guest / 'enrollment.json'
        raw = json.dumps(manifest).encode() + b'\n'
        self.manifest.write_bytes(raw)
        self.manifest.chmod(0o400)
        self.pin = L.digest(raw)
        with L.StateController(STORAGE, owner.lease, boot_id=self.boot) as controller:
            controller.enroll('one', self.pin, enabled=True)
        self.head_path = self.root / '.a733-lifecycle/one/head.json'
        owner.open()
        owner.lease.close()

    def operation(self):
        operation = M.InstalledMaintenance(STORAGE)
        self.addCleanup(operation.close)
        return operation

    def gate(self):
        with self.admission._gate():
            return self.admission._read()

    def test_entry_census_failure_releases_ex_but_never_opens(self):
        with patch.object(M, 'consumer_check', side_effect=PermissionError('census denied')):
            with self.assertRaises(M.MaintenanceError) as caught:
                self.operation()
        self.assertEqual(caught.exception.receipt['phase'], 'entry-consumers')
        self.assertEqual(self.gate()['state'], 'closed')
        owner = A.ClosureOwner(self.admission, self.gate())
        with owner.exclusive(timeout=0):
            pass

    def test_callback_rechecks_do_not_rescan_or_hash_guest(self):
        operation = self.operation()
        with patch.object(M, 'consumer_check') as census, \
                patch.object(P, 'Inspection', side_effect=AssertionError('unexpected disk scan')), \
                patch.object(operation.admission, '_gate', side_effect=AssertionError('gate lock inversion')):
            operation.recheck()
            operation.recheck()
        census.assert_not_called()

    def test_context_entry_failure_closes_owned_resources(self):
        operation = self.operation()
        self.bad_context = True
        with self.assertRaises(M.MaintenanceError):
            operation.__enter__()
        self.assertTrue(operation._closed)
        self.assertIsNone(operation.lease)
        self.assertEqual(self.gate()['state'], 'closed')

    def test_foreign_pid_cannot_close_or_finish(self):
        operation = self.operation()
        read_fd, write_fd = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.close(read_fd)
            try:
                for method in (operation.close, operation.finish_success):
                    try:
                        method()
                    except A.AdmissionError:
                        os.write(write_fd, b'x')
            finally:
                os._exit(0)
        os.close(write_fd)
        try:
            received = b''
            while True:
                piece = os.read(read_fd, 10)
                if not piece:
                    break
                received += piece
            self.assertEqual(received, b'xx')
        finally:
            os.close(read_fd)
            os.waitpid(pid, 0)
        operation.recheck()

    def test_quarantined_or_malformed_heads_never_reconcile(self):
        original = self.head_path.read_bytes()
        head = L.HEAD.validate_head(original)
        quarantine = L.HEAD.quarantine(head, 'f' * 64, expected_head_sha256=L.digest(original))
        self.head_path.write_bytes(L.HEAD.canonical(quarantine))
        with patch.object(M.CP, 'reconcile') as reconcile:
            with self.assertRaises(M.MaintenanceError):
                self.operation()
        reconcile.assert_not_called()
        self.assertEqual(self.head_path.read_bytes(), L.HEAD.canonical(quarantine))
        self.assertEqual(self.gate()['state'], 'closed')

    def test_native_lease_initial_proof_and_explicit_finish(self):
        operation = self.operation()
        self.assertEqual(self.events, ['stop', 'census'])
        self.assertIs(type(operation.controller), L.StateController)
        self.assertIs(operation.context, operation.controller.context)
        A._held_lock(operation.lease.fd, 'WRITE', os.getpid())
        self.assertEqual(self.gate()['state'], 'closed')
        with self.assertRaises(A.AdmissionError):
            self.admission.admit()
        result = operation.finish_success()
        self.assertTrue(result['success'])
        self.assertEqual(self.events, ['stop', 'census', 'census'])
        self.assertEqual(self.gate()['state'], 'open')
        with self.assertRaises(A.AdmissionError):
            operation.recheck()
        operation.close()
        with self.admission.admit():
            pass

    def test_repeated_finish_preserves_ack_and_creator_owned_locks(self):
        operation = self.operation()
        acknowledged = operation.finish_success()
        with patch.object(operation.owner, 'open', side_effect=AssertionError('repeated open')), \
                patch.object(operation, 'close', side_effect=AssertionError('unexpected cleanup')):
            with self.assertRaisesRegex(M.MaintenanceError, 'already finished') as caught:
                operation.finish_success()
        self.assertEqual(caught.exception.receipt, acknowledged)
        self.assertEqual(operation.receipt, acknowledged)
        self.assertEqual(self.gate()['state'], 'open')
        A._held_lock(operation.lease.fd, 'WRITE', os.getpid())
        operation.close()
        with self.admission.admit():
            pass

    def test_default_close_leaves_closed_and_cannot_adopt(self):
        operation = self.operation()
        nonce = operation.owner.nonce
        operation.close()
        operation.close()
        self.assertEqual(self.gate()['owner_nonce'], nonce)
        with self.assertRaisesRegex(M.MaintenanceError, 'already closed'):
            self.operation()
        self.assertEqual(self.events, ['stop', 'census'])

    def test_manual_lease_conflict_after_stop_without_kill(self):
        with self.admission.admit():
            with self.assertRaises(M.MaintenanceError) as caught:
                self.operation()
        self.assertEqual(caught.exception.receipt['phase'], 'exclusive-lease')
        self.assertEqual(self.events, ['stop'])
        self.assertEqual(self.gate()['state'], 'closed')

    def test_stop_failure_never_acquires_exclusive_or_disk(self):
        with patch.object(M, 'stop_managed', side_effect=RuntimeError('stop failed')), \
                patch.object(A.ClosureOwner, 'exclusive') as exclusive:
            with self.assertRaises(M.MaintenanceError):
                self.operation()
        exclusive.assert_not_called()
        self.assertEqual(self.gate()['state'], 'closed')

    def test_native_identity_and_pid1_refuse_before_closure(self):
        with patch.object(M.CONTEXT, 'host_identity', side_effect=RuntimeError('foreign namespace')):
            with self.assertRaises(M.MaintenanceError):
                self.operation()
        for state in ('stopping', 'maintenance', 'unknown'):
            with patch.object(M.NORMAL, 'manager_state', return_value=state):
                with self.assertRaises(M.MaintenanceError):
                    self.operation()
        self.assertEqual(self.gate()['state'], 'open')
        self.assertEqual(self.events, [])

    def test_nonce_and_actual_exclusive_flock_rechecked(self):
        operation = self.operation()
        import fcntl
        fcntl.flock(operation.lease.fd, fcntl.LOCK_SH)
        with self.assertRaises(A.AdmissionError):
            operation.recheck()
        fcntl.flock(operation.lease.fd, fcntl.LOCK_EX)
        with self.admission._gate():
            self.admission._publish(dict(self.admission._read(), owner_nonce=str(uuid.uuid4())))
        with self.assertRaisesRegex(A.AdmissionError, 'ownership changed'):
            operation.recheck()

    def test_creator_thread_including_double_close(self):
        operation = self.operation()
        errors = []
        def foreign():
            for method in (operation.recheck, operation.close):
                try:
                    method()
                except A.AdmissionError as error:
                    errors.append(str(error))
        thread = threading.Thread(target=foreign)
        thread.start()
        thread.join()
        self.assertEqual(len(errors), 2)
        operation.recheck()
        operation.close()
        thread = threading.Thread(target=foreign)
        thread.start()
        thread.join()
        self.assertEqual(len(errors), 4)

    def test_recheck_placement_and_final_disk_proof(self):
        operation = self.operation()
        self.bad_context = True
        with self.assertRaises(A.AdmissionError):
            operation.recheck()
        self.bad_context = False
        (self.guest / 'disk.raw').write_bytes(b'changed')
        with self.assertRaises(M.MaintenanceError):
            operation.finish_success()
        self.assertEqual(self.gate()['state'], 'closed')

    def test_initial_manifest_tamper_refuses(self):
        self.manifest.write_bytes(self.manifest.read_bytes() + b' ')
        with self.assertRaises(M.MaintenanceError):
            self.operation()
        self.assertEqual(self.gate()['state'], 'closed')

    def test_unresolved_heads_reconcile_fresh_then_refuse_on_failure(self):
        with self.admission.admit() as lease, L.StateController(STORAGE, lease, boot_id=self.boot) as controller:
            run = controller.admit_run('one', self.pin, str(uuid.uuid4()))
            run.close()
        with patch.object(M.CP, 'reconcile', wraps=M.CP.reconcile) as reconcile:
            with self.assertRaises(M.MaintenanceError):
                self.operation()
        self.assertEqual(reconcile.call_count, 1)
        self.assertEqual(self.gate()['state'], 'closed')
        self.assertEqual(L.HEAD.validate_head(self.head_path.read_bytes())['status'], 'quarantined')

    def test_completed_ready_requires_checkpoint_authenticator(self):
        head = L.HEAD.initial_head(STORAGE, dict(name='one', uid=60001, gid=60001), self.pin)
        # Actual schema edges provide a syntactically valid completed ready head;
        # fabricated missing cohort must still fail the real checkpoint verifier.
        head = L.HEAD.admit(head, self.pin, str(uuid.uuid4()), self.boot,
                             expected_head_sha256=L.digest(L.HEAD.canonical(head)))
        for function in (L.HEAD.mark_running, L.HEAD.mark_stopping):
            head = function(head, expected_head_sha256=L.digest(L.HEAD.canonical(head)))
        head = L.HEAD.mark_checkpoint(head, 'a' * 64,
                                expected_head_sha256=L.digest(L.HEAD.canonical(head)))
        head = L.HEAD.complete(head, 'a' * 64, self.pin, expected_head_sha256=L.digest(L.HEAD.canonical(head)))
        self.head_path.write_bytes(L.HEAD.canonical(head))
        with patch.object(M.CP, 'authenticate_ready_cohort', wraps=M.CP.authenticate_ready_cohort) as verify:
            with self.assertRaises(M.MaintenanceError):
                self.operation()
        verify.assert_called_once()
        self.assertEqual(self.gate()['state'], 'closed')

    def checkpoint_fixture(self, interrupted=False):
        # Reuse data construction, but execute producers/verifiers in exactly our
        # CP family. The helper's board/admission substitutes end before entry.
        spec = importlib.util.spec_from_file_location(
            'maintenance_cohort_fixture', BASE / 'tests/test_managed_guest_checkpoint.py')
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        helper.M, helper.L, helper.P = M.CP, L, P
        case = helper.ManagedCheckpoint()
        try:
            case.setUp()
            if interrupted:
                native = M.CP.STORE.RootDirectory.publish
                def stop_before_checkpoint(store, name, raw, **kwargs):
                    if name == 'head.json' and L.HEAD.validate_head(raw)['status'] == 'checkpoint':
                        raise OSError('interrupted before checkpoint head')
                    return native(store, name, raw, **kwargs)
                with patch.object(M.CP.STORE.RootDirectory, 'publish', new=stop_before_checkpoint):
                    with self.assertRaises(L.OperationError):
                        case.publish()
            else:
                self.assertTrue(case.publish()['success'])
            case.run.close()
            shutil.copytree(case.root, self.root, dirs_exist_ok=True)
            shutil.copytree(case.state, self.state, dirs_exist_ok=True)
        finally:
            case.doCleanups()

    def test_real_completed_checkpoint_cohort_success(self):
        self.checkpoint_fixture()
        with patch.object(M.CP, 'authenticate_ready_cohort', wraps=M.CP.authenticate_ready_cohort) as verify:
            operation = self.operation()
            operation.finish_success()
        self.assertEqual(verify.call_count, 2)
        self.assertEqual(self.gate()['state'], 'open')

    def test_real_stopping_cohort_reconciles_before_maintenance(self):
        self.checkpoint_fixture(interrupted=True)
        self.assertEqual(L.HEAD.validate_head(self.head_path.read_bytes())['status'], 'stopping')
        with patch.object(M.CP, 'reconcile', wraps=M.CP.reconcile) as reconcile:
            operation = self.operation()
        reconcile.assert_called_once()
        self.assertEqual(L.HEAD.validate_head(self.head_path.read_bytes())['status'], 'ready')
        operation.finish_success()
        self.assertEqual(self.gate()['state'], 'open')

    def test_final_consumer_refusal_and_open_ack_uncertainty(self):
        operation = self.operation()
        with patch.object(M, 'consumer_check', side_effect=RuntimeError('VM remains')):
            with self.assertRaises(M.MaintenanceError):
                operation.finish_success()
        self.assertEqual(self.gate()['state'], 'closed')

    def test_open_ack_failure_conservative_receipt(self):
        operation = self.operation()
        native = operation.owner.open
        def after_effect():
            native()
            raise OSError('lost open acknowledgement')
        with patch.object(operation.owner, 'open', side_effect=after_effect):
            with self.assertRaises(M.MaintenanceError) as caught:
                operation.finish_success()
        self.assertEqual(caught.exception.receipt['gate_state'], 'uncertain')
        self.assertFalse(caught.exception.receipt['success'])
        self.assertEqual(self.gate()['state'], 'open')

    def test_close_all_owned_resources_once_on_errors(self):
        operation = self.operation()
        called = []
        for field in ('controller', 'lease', 'admission'):
            resource = getattr(operation, field)
            native = resource.close
            def fail(field=field, native=native):
                called.append(field)
                native()
                raise OSError(field + ' close acknowledgement')
            substitute = patch.object(resource, 'close', side_effect=fail)
            substitute.start()
            self.addCleanup(substitute.stop)
        with self.assertRaises(M.MaintenanceError) as caught:
            operation.close()
        self.assertEqual(called, ['controller', 'lease', 'admission'])
        self.assertEqual(len(caught.exception.receipt['close_errors']), 3)
        operation.close()
        self.assertEqual(len(called), 3)


class Census(unittest.TestCase):
    def scan(self, target=None, error=None, device=False):
        entries = {'/proc': [SimpleNamespace(name='123', path='/proc/123')],
                   '/proc/123/task': [SimpleNamespace(name='124', path='/proc/123/task/124')],
                   '/proc/123/task/124/fd': [SimpleNamespace(name='4', path='/proc/123/task/124/fd/4')]}
        class Scan:
            def __init__(self, path):
                self.items = entries[path]
            def __enter__(self):
                return iter(self.items)
            def __exit__(self, *args):
                pass
        with patch.object(M.os, 'scandir', side_effect=Scan), \
                patch.object(M.os, 'readlink', side_effect=error, return_value=target or 'socket:[123]'), \
                patch.object(M.os, 'stat', return_value=SimpleNamespace(
                    st_mode=0o020600 if device else 0o100600, st_rdev=os.makedev(10, 232))):
            M.consumer_check()

    def test_kvm_vm_vcpu_vgic_and_character_alias_refuse(self):
        for target in ('/dev/kvm', '/dev/kvm (deleted)', 'anon_inode:kvm-vm',
                       'anon_inode:[kvm-vm]', 'anon_inode:kvm-vcpu:0',
                       'anon_inode:kvm-arm-vgic-v2', 'anon_inode:kvm-arm-vgic-v3'):
            with self.subTest(target=target), self.assertRaises(A.AdmissionError):
                self.scan(target)
        with self.assertRaises(A.AdmissionError):
            self.scan('/renamed/device', device=True)
        self.scan()

    def test_only_gone_races_allow(self):
        for number in (errno.ENOENT, errno.ESRCH):
            self.scan(error=OSError(number, 'gone'))
        for number in (errno.EPERM, errno.EACCES, errno.EIO, errno.EMFILE):
            with self.subTest(number=number), self.assertRaises(OSError):
                self.scan(error=OSError(number, 'unsafe'))

    def test_scan_limits_and_deadline(self):
        for constant in ('PID_LIMIT', 'FD_LIMIT'):
            with patch.object(M, constant, 0), self.assertRaises(A.AdmissionError):
                self.scan()
        with patch.object(M.time, 'monotonic', side_effect=[0, 11]), self.assertRaises(A.AdmissionError):
            self.scan()


if __name__ == '__main__':
    unittest.main()
