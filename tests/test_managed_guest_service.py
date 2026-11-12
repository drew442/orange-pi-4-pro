"""Service composition with real fork/QMP/raw wait, Run and checkpoint I/O.

Reuse the native surrogate's explicit physical placement, credential/executable,
account and admission-capability mocks for ordering tests. One named native
admission test restores canonical Admission/Lease/GuestLock and native flocks.
Boot/physical host and QEMU credential/executable checks remain explicitly mocked.
This is software orchestration evidence; no QEMU, KVM or hardware acceptance.
"""
import importlib.util
import json
import os
from pathlib import Path
import signal
import threading
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


SERVICE = module('tested_managed_service', 'scripts/run-managed-guest.py')
ACTUAL_CONSUME = SERVICE.EXPLICIT.consume
NATIVE = module('service_native_fixture', 'tests/test_managed_checkpoint_producer_integration.py')
M, L, S = NATIVE.M, NATIVE.L, NATIVE.S


@unittest.skipUnless(os.geteuid() == 0 and hasattr(os, 'fork'), 'root Linux native fixture required')
class ServiceComposition(unittest.TestCase):
    def setUp(self):
        self.actual_lease, self.actual_guest = L.ADMISSION.Lease, L.ADMISSION.GuestLock
        self.actual_open = L.ADMISSION.Admission.open_existing
        self.case = NATIVE.NativeProducerComposition()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        c = self.case
        previous = M.HEAD.canonical(c.run._record['previous_head'])
        c.run.close()
        c.head_path.write_bytes(previous)
        self.events = []
        admission = c.lease.admission
        admission.admit = lambda: c.lease
        admission.close_gate = lambda reason: self.events.append('gate:' + reason)
        admission.close = lambda: self.events.append('admission-close')
        c.lease.close = lambda: self.events.append('lease-close')
        original_init = S.Supervisor.__init__
        fixture = self

        def initialize(supervisor, run):
            supervisor.folder, supervisor.disk = str(c.qmp), str(c.disk)
            supervisor.silent, supervisor.exit_status = False, 0
            original_init(supervisor, run)
            c.supervisor = supervisor
            fixture.events.append('supervisor-new')

        patches = [mock.patch.object(SERVICE.EXPLICIT, 'consume', return_value=True),
                   mock.patch.object(SERVICE, 'CP', M),
                   mock.patch.object(SERVICE, 'LIFECYCLE', L),
                   mock.patch.object(SERVICE, 'SUPERVISOR', S),
                   mock.patch.object(SERVICE, 'ADMISSION', L.ADMISSION),
                   mock.patch.object(SERVICE, 'CONTEXT', L.CONTEXT),
                   mock.patch.object(SERVICE.MAINTENANCE, 'registered_storage', return_value=NATIVE.F.STORAGE),
                   mock.patch.object(L.CONTEXT, 'read_system', return_value=(NATIVE.F.BOOT + '\n').encode()),
                   mock.patch.object(L.CONTEXT, 'host_identity'),
                   mock.patch.object(SERVICE.NORMAL, 'local_kvm_gid', return_value=1),
                   mock.patch.object(SERVICE.NORMAL, 'manager_state', return_value='running'),
                   mock.patch.object(L.ADMISSION.Admission, 'open_existing', return_value=admission),
                   mock.patch.object(S.Supervisor, '__init__', initialize),
                   mock.patch.object(S.Supervisor, '_prepare', NATIVE.NativeSupervisor._prepare),
                   mock.patch.object(S.Supervisor, '_child_exec', NATIVE.NativeSupervisor._child_exec)]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.stop_calls = 0

    def stop(self):
        self.stop_calls += 1
        return self.stop_calls >= 2  # Before launch false, running loop true.

    def execute(self):
        return SERVICE.run_service('one', stop_requested=self.stop)

    def test_actual_producer_checkpoint_and_cleanup_before_success(self):
        result = self.execute()
        self.assertTrue(result['success'])
        self.assertFalse(result['hardware_acceptance'])
        self.assertEqual(result['cleanup'], ['supervisor', 'run', 'controller', 'lease', 'admission'])
        self.assertEqual(self.events[-2:], ['lease-close', 'admission-close'])
        self.assertEqual(result['supervisor']['errors'], [])
        self.assertTrue(self.case.supervisor._closed)
        self.assertTrue(self.case.supervisor.run._closed)
        self.assertEqual(NATIVE.F.ManagedCheckpoint.head(self.case)['status'], 'ready')
        self.assertTrue(self.case.supervisor.run._record['automatic'])
        self.assertNotEqual(result['run_id'], NATIVE.F.RUN)
        self.assertNotIn('gate:shutdown', self.events)
        self.assertTrue(M.authenticate_ready_cohort(self.case.controller, 'one')['success'])

    def test_service_composes_explicit_policy_and_records_exact_flag(self):
        def consume(controller, name, pin, head, lock, inspection, entry, guard):
            self.assertIs(controller.lease, self.case.lease)
            self.assertEqual(name, 'one')
            self.assertEqual(entry['manifest_sha256'], pin)
            guard()
            return False
        with mock.patch.object(SERVICE.EXPLICIT, 'consume', side_effect=consume) as policy:
            self.assertTrue(self.execute()['success'])
        policy.assert_called_once()
        self.assertFalse(self.case.supervisor.run._record['automatic'])

    def test_policy_rejects_non_boolean_without_admission_publication(self):
        previous = self.case.head_path.read_bytes()
        with mock.patch.object(SERVICE.EXPLICIT, 'consume', return_value=1):
            with self.assertRaises(SERVICE.ServiceError):
                self.execute()
        self.assertEqual(self.case.head_path.read_bytes(), previous)
        self.assertNotIn('supervisor-new', self.events)

    def test_default_automatic_still_refuses_second_run_in_same_boot(self):
        self.assertTrue(self.execute()['success'])
        previous = self.case.head_path.read_bytes()
        with self.assertRaises(SERVICE.ServiceError):
            self.execute()
        self.assertEqual(self.case.head_path.read_bytes(), previous)
        self.assertEqual(self.events.count('supervisor-new'), 1)

    def test_native_admission_shared_guest_ex_and_checkpoint_publication(self):
        self.native_explicit_case()

    def test_native_policy_failure_before_consume_preserves_pending_and_head(self):
        self.native_explicit_case('before')

    def test_native_policy_failure_after_consume_blocks_lost_ack_retry(self):
        self.native_explicit_case('after')

    def test_native_consumption_and_registry_cleanup_failures_both_survive(self):
        original_close, original_sync = L.ADMISSION.RegistryLock.close, os.fsync
        previous = self.case.head_path.read_bytes()
        def failed_close(lock):
            original_close(lock)
            raise OSError('final registry close failed')
        def failed_sync(fd):
            if os.readlink('/proc/self/fd/' + str(fd)).endswith('/boot/explicit') and any(
                    name.startswith('consumed-') for name in os.listdir(fd)):
                raise OSError('consumption directory fsync failed')
            return original_sync(fd)
        with mock.patch.object(L.ADMISSION.RegistryLock, 'close', failed_close), \
                mock.patch.object(os, 'fsync', failed_sync):
            with self.assertRaises(SERVICE.ServiceError) as caught:
                self.native_explicit_case()
        detail = caught.exception.receipt['nested_error']['publication_error']
        self.assertTrue(detail['primary_error']['receipt']['consumed'])
        self.assertTrue(detail['primary_error']['receipt']['uncertain'])
        self.assertIn('final registry close failed', detail['cleanup_error']['error'])
        self.assertEqual(self.case.head_path.read_bytes(), previous)
        self.assertNotIn('supervisor-new', self.events)

    def native_explicit_case(self, failure_mode=None):
        # Restore canonical capability types after the inherited synthetic setup.
        # The actual Admission creates protected files and native kernel flocks.
        admission_module = L.ADMISSION
        with tempfile.TemporaryDirectory(dir='/run', prefix='service-admission-') as folder:
            Path(folder).chmod(0o755)
            units, prepared = Path(folder) / 'units', Path(folder) / 'boot'
            units.mkdir(mode=0o755)
            prepared.mkdir(mode=0o700)
            with mock.patch.object(admission_module, 'Lease', self.actual_lease), \
                 mock.patch.object(admission_module, 'GuestLock', self.actual_guest), \
                 mock.patch.object(admission_module, 'RUNTIME_PARENT', folder), \
                 mock.patch.object(admission_module, 'RUNTIME', folder + '/a733-admission'), \
                 mock.patch.object(admission_module, '_boot_id', return_value=NATIVE.F.BOOT), \
                 mock.patch.object(admission_module.Admission, 'open_existing', side_effect=self.actual_open):
                admission = admission_module.initialize(NATIVE.F.BOOT, 1, lambda: 'running')
                try:
                    owner = admission.bootstrap_owner
                    admission.prepare_managed(owner.lease)
                    with admission.guest_lock('one', owner.lease, create=True) as lock:
                        admission.mark_managed('one', owner.lease, lock, guard=lambda: None)
                    owner.open()
                    owner.lease.close()
                    original = S.Supervisor.start
                    def start(supervisor):
                        self.assertIs(type(supervisor.run.lease), self.actual_lease)
                        self.assertIs(type(supervisor.run.guest_lock), self.actual_guest)
                        self.assertFalse(supervisor.run.lease.exclusive)
                        admission_module._held_lock(supervisor.run.lease.fd, 'READ', os.getpid())
                        admission_module._held_lock(supervisor.run.guest_lock.fd, 'WRITE', os.getpid())
                        return original(supervisor)
                    helper = SERVICE.EXPLICIT
                    receipts = []
                    def explicit(controller, name, pin, head, lock, inspection, entry, guard):
                        # CLI issue and service consume compose under the actual
                        # retained SH/EX/inspection/final-registry custody.
                        raw = helper.UNITS.render_managed(inspection.manifest['config']).encode()
                        path = units / ('a733-managed-' + name + '.service')
                        path.write_bytes(raw)
                        path.chmod(0o444)
                        receipts.append(helper.issue(controller, name, pin, head,
                            helper.digest(raw), lock, guard, inspection=inspection, registry_entry=entry))
                        if failure_mode == 'before':
                            path.write_bytes(b'unit changed before consume')
                        automatic = ACTUAL_CONSUME(controller, name, pin, head, lock,
                                                   inspection, entry, guard)
                        if failure_mode == 'after':
                            raise OSError('lost acknowledgement after consume')
                        return automatic
                    with mock.patch.object(S.Supervisor, 'start', start), \
                         mock.patch.object(helper.PREP, 'installed_guard'), \
                         mock.patch.object(helper.PREP, 'UNIT_DIRECTORY', str(units)), \
                         mock.patch.object(helper.PREP, 'PREPARED_DIRECTORY', str(prepared)), \
                         mock.patch.object(helper, 'consume', side_effect=explicit):
                        if failure_mode is not None:
                            previous = self.case.head_path.read_bytes()
                            with self.assertRaises(SERVICE.ServiceError):
                                self.execute()
                            self.assertEqual(self.case.head_path.read_bytes(), previous)
                            self.assertNotIn('supervisor-new', self.events)
                            self.assertEqual((prepared / 'explicit/pending-one.json').exists(),
                                             failure_mode == 'before')
                            with mock.patch.object(helper, 'consume', side_effect=ACTUAL_CONSUME):
                                with self.assertRaises(SERVICE.ServiceError):
                                    self.execute()
                            self.assertEqual(self.case.head_path.read_bytes(), previous)
                            return
                        result = self.execute()
                    self.assertEqual(len(receipts), 1)
                    self.assertFalse(self.case.supervisor.run._record['automatic'])
                    self.assertFalse((prepared / 'explicit/pending-one.json').exists())
                    self.assertTrue((prepared / 'explicit' / (
                        'consumed-' + receipts[0]['request_id'] + '.json')).exists())
                    self.assertTrue(result['success'])
                    self.assertEqual(NATIVE.F.ManagedCheckpoint.head(self.case)['status'], 'ready')
                    with admission.admit() as lease:
                        with admission.guest_lock('one', lease):
                            pass
                finally:
                    admission.close()

    def test_pid1_shutdown_closes_gate_before_stop(self):
        original = S.Supervisor.stop
        def stop(supervisor):
            self.events.append('stop')
            return original(supervisor)
        with mock.patch.object(SERVICE.NORMAL, 'manager_state', side_effect=['running', 'stopping']), \
             mock.patch.object(S.Supervisor, 'stop', stop):
            self.assertTrue(self.execute()['success'])
        self.assertLess(self.events.index('gate:shutdown'), self.events.index('stop'))

    def test_healthy_polls_do_not_query_pid1_until_flag_and_final_stop(self):
        request = SERVICE.StopRequest()
        original_poll, original_stop = S.Supervisor.poll, S.Supervisor.stop
        polls = []
        def poll(supervisor):
            if supervisor.receipt['stage'] == 'running':
                self.assertEqual(manager.call_count, 1)
                polls.append(True)
                if len(polls) == 5:
                    request.signal(signal.SIGTERM, None)
            return original_poll(supervisor)
        def stop(supervisor):
            self.assertEqual(manager.call_count, 2)
            self.assertEqual(len(polls), 5)
            self.events.append('qmp-stop')
            self.assertIn('gate:shutdown', self.events)
            return original_stop(supervisor)
        with mock.patch.object(SERVICE.NORMAL, 'manager_state',
                               side_effect=['running', 'stopping']) as manager, \
             mock.patch.object(S.Supervisor, 'poll', poll), \
             mock.patch.object(S.Supervisor, 'stop', stop), \
             mock.patch.object(SERVICE.time, 'sleep'):
            result = SERVICE.run_service('one', stop_requested=request)
        self.assertTrue(result['success'])
        self.assertEqual(manager.call_count, 2)
        self.assertLess(self.events.index('gate:shutdown'), self.events.index('qmp-stop'))

    def test_disabled_guest_never_creates_supervisor(self):
        registry = NATIVE.F.ManagedCheckpoint.registry(self.case)
        registry['guests'][0]['enabled'] = False
        self.case.registry_path.write_bytes(M.REGISTRY.canonical(registry))
        with self.assertRaises(SERVICE.ServiceError) as caught:
            self.execute()
        self.assertEqual(caught.exception.receipt['stage'], 'registry')
        self.assertNotIn('supervisor-new', self.events)
        self.assertEqual(caught.exception.receipt['cleanup'], ['controller', 'lease', 'admission'])

    def test_missing_head_refuses_before_launch(self):
        self.case.head_path.unlink()
        with self.assertRaises(SERVICE.ServiceError):
            self.execute()
        self.assertNotIn('supervisor-new', self.events)

    def test_missing_required_fixture_proof_refuses_checkpoint(self):
        # Admission schema is real; only input/profile fixture preparation is
        # mocked. Checkpoint still requires actual same-family attached proof.
        registry = NATIVE.F.ManagedCheckpoint.registry(self.case)
        registry['guests'][0]['fixture_profile'] = M.FIXTURE.PROFILE
        self.case.registry_path.write_bytes(M.REGISTRY.canonical(registry))
        with mock.patch.object(M.FIXTURE, 'Fixture', side_effect=ValueError('missing fixture inputs')):
            with self.assertRaises(SERVICE.ServiceError) as caught:
                self.execute()
        self.assertFalse(caught.exception.receipt['success'])
        self.assertNotIn('checkpoint', caught.exception.receipt)

    def test_fixture_called_once_and_stop_during_verification_refuses(self):
        registry = NATIVE.F.ManagedCheckpoint.registry(self.case)
        registry['guests'][0]['fixture_profile'] = M.FIXTURE.PROFILE
        self.case.registry_path.write_bytes(M.REGISTRY.canonical(registry))
        fixture = mock.Mock()
        fixture.verify_and_confirm.return_value = {'hardware_acceptance': False}
        with mock.patch.object(M.FIXTURE, 'Fixture', return_value=fixture), \
             mock.patch.object(M, 'checkpoint') as checkpoint:
            with self.assertRaises(SERVICE.ServiceError) as caught:
                self.execute()
        fixture.verify_and_confirm.assert_called_once_with()
        checkpoint.assert_not_called()
        self.assertIn('proof ambiguous', str(caught.exception))
        self.assertEqual(NATIVE.F.ManagedCheckpoint.head(self.case)['status'], 'quarantined')

    def test_checkpoint_failure_keeps_partial_receipt_and_closes_all(self):
        error = L.OperationError({'stage': 'manifest-publication', 'success': False}, ValueError('injected'))
        with mock.patch.object(M, 'checkpoint', side_effect=error):
            with self.assertRaises(SERVICE.ServiceError) as caught:
                self.execute()
        result = caught.exception.receipt
        self.assertEqual(result['nested_error']['stage'], 'manifest-publication')
        self.assertFalse(result['success'])
        self.assertEqual(result['cleanup'], ['supervisor', 'run', 'controller', 'lease', 'admission'])

    def test_post_checkpoint_supervisor_cleanup_error_cannot_report_success(self):
        original = S.Supervisor.close
        def close(supervisor):
            original(supervisor)
            supervisor.receipt['errors'].append('injected cleanup failure')
        with mock.patch.object(S.Supervisor, 'close', close):
            with self.assertRaises(SERVICE.ServiceError) as caught:
                self.execute()
        self.assertTrue(caught.exception.receipt['checkpoint']['success'])
        self.assertFalse(caught.exception.receipt['success'])
        self.assertIn('admission', caught.exception.receipt['cleanup'])
        self.assertTrue(caught.exception.receipt['supervisor']['errors'])

    def test_run_close_failure_still_closes_controller_lease_admission(self):
        original = L.ManagedRun.close
        def close(run):
            original(run)
            raise OSError('run close injected')
        with mock.patch.object(L.ManagedRun, 'close', close):
            with self.assertRaises(SERVICE.ServiceError) as caught:
                self.execute()
        self.assertFalse(caught.exception.receipt['success'])
        self.assertEqual(caught.exception.receipt['cleanup'][-3:], ['controller', 'lease', 'admission'])

    def test_raw_zero_without_guest_shutdown_never_checkpoints(self):
        # Surrogate replies to powerdown, then emits RESET and exits normally 0.
        # Actual supervisor must require the missing guest-owned SHUTDOWN.
        with mock.patch.object(NATIVE, 'SHUTDOWN', {'event': 'RESET', 'data': {}}), \
             mock.patch.object(M, 'checkpoint') as checkpoint:
            with self.assertRaises(SERVICE.ServiceError):
                self.execute()
        checkpoint.assert_not_called()
        self.assertEqual(self.case.supervisor.receipt['raw_wait_status'], 0)


class ServiceBoundary(unittest.TestCase):
    def test_manual_name_refused_before_storage_discovery(self):
        with mock.patch.object(SERVICE.MAINTENANCE, 'registered_storage') as storage:
            with self.assertRaises(SERVICE.ServiceError):
                SERVICE.run_service('firstkvm')
        storage.assert_not_called()

    def test_nonmain_thread_refused(self):
        errors = []
        def work():
            try:
                SERVICE.run_service('one')
            except SERVICE.ServiceError as error:
                errors.append(error)
        thread = threading.Thread(target=work)
        thread.start()
        thread.join()
        self.assertEqual(len(errors), 1)
        self.assertIn('main thread', str(errors[0]))

    def test_signal_handler_only_sets_flag(self):
        request = SERVICE.StopRequest()
        self.assertFalse(request())
        request.signal(signal.SIGTERM, None)
        self.assertTrue(request())

    def test_cli_restores_handlers_and_accepts_only_name(self):
        handlers = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
        with mock.patch.object(SERVICE, 'run_service', return_value={'success': True}), \
             mock.patch('sys.stdout'):
            self.assertEqual(SERVICE.main(['one']), 0)
        for number, handler in handlers.items():
            self.assertEqual(signal.getsignal(number), handler)
        with self.assertRaises(SystemExit), mock.patch('sys.stderr'):
            SERVICE.main(['one', '--qemu-args', 'unsafe'])

    def test_receipt_bytes_are_hashed_and_output_bounded(self):
        raw = SERVICE.json_receipt({'success': False, 'console': b'x' * 1000000,
                                    'errors': ['failure'], 'nested': ['a' * 100000] * 10000})
        value = json.loads(raw)
        self.assertLessEqual(len(raw.encode()), 262144)
        self.assertFalse(value['success'])


if __name__ == '__main__':
    unittest.main()
