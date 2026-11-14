"""Managed control composition tests; no hardware acceptance claims."""
from contextlib import ExitStack
import importlib.util
import hashlib
import stat
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location('manage_guests_test',
    Path(__file__).resolve().parents[1] / 'scripts/manage-guests.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
BOOT = '33333333-3333-4333-8333-333333333333'
PIN = 'a' * 64


class Resource:
    def __init__(self):
        self.closed = False
        self.checks = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        self.closed = True

    def recheck(self):
        if self.closed:
            raise ValueError('resource closed')
        self.checks += 1


class Controls(unittest.TestCase):
    def setUp(self):
        profile = patch.object(M.P.CONTEXT.MANAGEMENT, 'guest_management_profile', return_value='managed-experimental')
        profile.start()
        self.addCleanup(profile.stop)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.policy = Resource()
        self.policy.value = {'storage': {'expected': True}}
        self.policy.check = self.policy.recheck
        self.context = SimpleNamespace(guard=Mock())
        self.controller = Resource()
        self.controller.context = self.context
        self.controller.status = Mock(return_value={'configured': True})
        self.controller.enroll = Mock(return_value={'success': True})
        self.controller.enable = Mock(return_value={'success': True})
        self.controller.disable = Mock(return_value={'success': True})
        owner = self

        class Lease(Resource):
            exclusive = False
            fd = 22

            def __init__(self, admission):
                super().__init__()
                self.admission = admission

        class Admission(Resource):
            bootstrap_owner = None

            @classmethod
            def open_existing(cls, *args):
                return owner.admission

            def admit(self):
                return owner.lease

            def _gate(self):
                return Resource()

            def _read(self):
                return {'state': 'open'}

            def manager_state(self):
                return 'running'

        self.admission = Admission()
        self.lease = Lease(self.admission)
        for target, name, value in [
            (M.A, 'Admission', Admission), (M.A, 'Lease', Lease),
            (M.A, '_boot_id', Mock(return_value=BOOT)), (M.A, '_held_lock', Mock()),
            (M.P, 'installed_guard', Mock()), (M.os, 'getresuid', Mock(return_value=(0, 0, 0))),
            (M.P.BOOT_POLICY, 'Policy', Mock(return_value=self.policy)),
            (M.P.NORMAL, 'local_kvm_gid', Mock(return_value=108)),
            (M.L, 'StateController', Mock(return_value=self.controller))]:
            self.stack.enter_context(patch.object(target, name, value))
        self.command = self.stack.enter_context(patch.object(M.D, 'command', return_value=''))

    def assert_closed(self):
        self.assertTrue(self.admission.closed)
        self.assertTrue(self.lease.closed)
        self.assertTrue(self.controller.closed)
        self.assertTrue(self.policy.closed)

    def start(self, noop=False, cleanup_error=False):
        def prepare(controller, name, guard, receipt):
            guard()
            receipt.update(no_op=noop, request={'success': True, 'no_op': noop})
            if cleanup_error:
                self.controller.close = Mock(side_effect=OSError('close failure'))
        with patch.object(M, 'prepare_start', side_effect=prepare):
            return M.manage('start', 'autotest')

    def test_start_queues_once_after_readonly_custody_closed(self):
        def command(argv, **kwargs):
            self.assertTrue(self.controller.closed)
            self.assertTrue(self.policy.closed)
            self.assertFalse(self.lease.closed)
            self.assertEqual(argv, ['/usr/bin/systemctl', '--no-block', 'start', '--',
                                    'a733-managed-autotest.service'])
        self.command.side_effect = command
        result = self.start()
        self.assertTrue(result['success'])
        self.assertTrue(result['queue_acknowledged'])
        self.command.assert_called_once()
        self.assert_closed()

    def test_pending_noop_never_requeues(self):
        result = self.start(noop=True)
        self.assertTrue(result['success'])
        self.assertTrue(result['no_op'])
        self.assertFalse(result['queue_attempted'])
        self.command.assert_not_called()
        self.assert_closed()

    def test_queue_failure_records_uncertainty_without_retry(self):
        self.command.side_effect = OSError('lost ack')
        result = self.start()
        self.assertFalse(result['success'])
        self.assertTrue(result['queue_attempted'])
        self.assertTrue(result['queue_uncertain'])
        self.command.assert_called_once()
        self.assert_closed()

    def test_prequeue_cleanup_failure_preserves_request_without_queue(self):
        result = self.start(cleanup_error=True)
        self.assertFalse(result['success'])
        self.assertTrue(result['request']['success'])
        self.command.assert_not_called()
        self.assertTrue(self.lease.closed)
        self.assertTrue(self.admission.closed)

    def test_foreign_admission_refuses_before_mutation(self):
        self.admission = Resource()
        result = M.manage('disable', 'autotest')
        self.assertFalse(result['success'])
        self.controller.disable.assert_not_called()
        self.command.assert_not_called()

    def test_exclusive_lease_refuses_before_controller(self):
        self.lease.exclusive = True
        result = M.manage('disable', 'autotest')
        self.assertFalse(result['success'])
        M.L.StateController.assert_not_called()
        self.assertTrue(self.lease.closed)

    def test_stale_boot_refuses_before_policy(self):
        M.A._boot_id.side_effect = [BOOT, '44444444-4444-4444-8444-444444444444']
        result = M.manage('disable', 'autotest')
        self.assertFalse(result['success'])
        M.P.BOOT_POLICY.Policy.assert_not_called()
        self.controller.disable.assert_not_called()

    def test_nonroot_refuses_before_policy(self):
        M.os.getresuid.return_value = (1000, 1000, 1000)
        self.assertFalse(M.manage('status')['success'])
        M.P.BOOT_POLICY.Policy.assert_not_called()

    def test_disable_only_updates_desired_policy(self):
        result = M.manage('disable', 'autotest')
        self.assertTrue(result['success'])
        self.controller.disable.assert_called_once_with('autotest')
        self.command.assert_not_called()
        self.assert_closed()

    def test_enroll_defaults_disabled_and_passes_independent_pin(self):
        self.assertTrue(M.manage('enroll', 'autotest', PIN)['success'])
        self.controller.enroll.assert_called_once_with('autotest', PIN,
                                                     enabled=False, fixture_profile='none')

    def test_enable_uses_controller_authority(self):
        self.assertTrue(M.manage('enable', 'autotest', PIN)['success'])
        self.controller.enable.assert_called_once_with('autotest', PIN)

    def test_status_observational(self):
        result = M.manage('status')
        self.assertTrue(result['success'])
        self.assertFalse(result['hardware_acceptance'])
        self.controller.status.assert_called_once_with()
        self.command.assert_not_called()


class StartCustody(unittest.TestCase):
    def setUp(self):
        profile = patch.object(M.P.CONTEXT.MANAGEMENT, 'guest_management_profile', return_value='managed-experimental')
        profile.start()
        self.addCleanup(profile.stop)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.guest, self.registry_lock, self.inspection, self.store = [Resource() for _ in range(4)]
        self.entry = dict(name='autotest', uid=60002, gid=60002, enabled=True, manifest_sha256=PIN)
        self.config = dict(name='autotest', uid=60002, gid=60002)
        self.other_config = dict(name='another')
        self.registry = {'guests': [self.entry]}
        self.inspection.manifest = {'config': self.config}
        self.store.read = Mock(return_value=b'head')
        self.controller = SimpleNamespace(
            admission=SimpleNamespace(guest_lock=Mock(return_value=self.guest),
                                      registry_lock=Mock(return_value=self.registry_lock),
                                      is_managed=Mock(return_value=True)),
            lease=object(), _inspection=Mock(), _storage=Mock(),
            _registry=Mock(return_value=(self.store, b'registry', self.registry)))
        self.controller._head_store = lambda stack, *args: stack.enter_context(self.store)
        self.guard = Mock()
        self.sources = [SimpleNamespace(check=Mock())]
        self.result = {}
        self.patches = [
            (M.P, '_registry', Mock(return_value=self.registry)),
            (M.P.POLICY, 'Inspection', Mock(return_value=self.inspection)),
            (M.P.HEAD, 'validate_head', Mock(return_value=dict(self.entry, status='ready'))),
            (M.P.REGISTRY, 'canonical', Mock(return_value=b'registry')),
            (M.L, '_verify_ready', Mock()),
            (M.P, '_configs', Mock(return_value=[self.config, self.other_config])),
            (M.P, 'physical_memory', Mock(return_value=8 * 1024**3)),
            (M.U, 'check_budget', Mock(return_value={'guest_count': 2})),
            (M.U, 'render_slice', Mock(return_value='slice')),
            (M.U, 'render_managed', Mock(return_value='service')),
            (M, 'inactive', Mock()),
            (M, 'verify_account', Mock()),
            (M, 'retain_units', Mock(return_value=self.sources)),
            (M.D, 'verify_loaded', Mock()),
            (M.EXPLICIT, 'issue', Mock(side_effect=self.issue))]
        for target, name, value in self.patches:
            self.stack.enter_context(patch.object(target, name, value))

    def issue(self, controller, name, pin, raw, unit_pin, guest, guard, **kwargs):
        self.assertFalse(self.guest.closed)
        self.assertFalse(self.inspection.closed)
        self.assertFalse(self.registry_lock.closed)
        guard()
        self.assertGreater(self.registry_lock.checks, 0)
        self.assertIs(kwargs['inspection'], self.inspection)
        self.assertIs(kwargs['registry_entry'], self.entry)
        self.assertEqual(pin, PIN)
        self.assertEqual(raw, b'head')
        self.assertEqual(unit_pin, M.P.digest(b'service'))
        self.sources[0].check.assert_called_once()
        M.D.verify_loaded.assert_called_once()
        self.assertEqual(M.inactive.call_count, 2)
        return {'success': True, 'no_op': False}

    def test_actual_inspection_and_final_registry_custody_through_issue(self):
        M.prepare_start(self.controller, 'autotest', self.guard, self.result)
        self.assertTrue(all(r.closed for r in (self.guest, self.inspection, self.registry_lock, self.store)))
        M.U.check_budget.assert_called_once_with([self.config, self.other_config], 8 * 1024**3)
        self.assertFalse(self.result['no_op'])

    def test_final_registry_disagreement_refuses_request_and_closes(self):
        self.controller._registry.return_value = (self.store, b'registry', {'guests': []})
        with self.assertRaises(M.A.AdmissionError):
            M.prepare_start(self.controller, 'autotest', self.guard, self.result)
        M.EXPLICIT.issue.assert_not_called()
        M.retain_units.assert_not_called()
        self.assertTrue(self.guest.closed and self.inspection.closed and self.registry_lock.closed)

    def test_enabled_config_budget_failure_never_publishes_request(self):
        M.U.check_budget.side_effect = ValueError('budget exceeded')
        with self.assertRaisesRegex(ValueError, 'budget exceeded'):
            M.prepare_start(self.controller, 'autotest', self.guard, self.result)
        M.EXPLICIT.issue.assert_not_called()
        M.retain_units.assert_not_called()


class ConstructorGuidance(unittest.TestCase):
    def test_actual_inspection_constructor_missing_account_preserves_error_and_closes(self):
        policy = M.P.POLICY
        raw = b'constructor-manifest'
        pin = hashlib.sha256(raw).hexdigest()
        guest, root = Resource(), Resource()
        root.fd = 11
        root.info = SimpleNamespace(st_uid=0, st_gid=0, st_mode=stat.S_IFDIR | 0o711, st_dev=1)
        guest_info = SimpleNamespace(st_uid=0, st_gid=60002,
                                    st_mode=stat.S_IFDIR | 0o710, st_dev=1)
        config = dict(name='autotest', uid=60002, gid=60002)
        manifest = {'config': config, 'guest_uuid': '22222222-2222-4222-8222-222222222222'}
        entry = dict(config, enabled=True, manifest_sha256=pin)
        controller = SimpleNamespace(admission=SimpleNamespace(guest_lock=Mock(return_value=guest)),
                                     lease=object())
        # Real account parser rejects a complete root database lacking this guest.
        databases = dict(passwd=b'root:x:0:0:root:/root:/bin/bash\n',
                         shadow=b'root:!:::::::\n', group=b'root:x:0:\n', gshadow=b'root:!::\n')
        result = {}
        with ExitStack() as stack:
            for target, name, value in [
                (M.P, '_registry', Mock(return_value={'guests': [entry]})),
                (policy.MANAGEMENT, 'runtime_guard', Mock()),
                (policy, 'host_namespace', Mock()),
                (policy.ENROLL, 'HeldPath', Mock(return_value=root)),
                (policy.ENROLL, 'probe_uuid', Mock()),
                (policy.STORAGE, 'verify_guest_directory', Mock(return_value={})),
                (policy.os, 'fstat', Mock(side_effect=[guest_info, SimpleNamespace(st_size=len(raw))])),
                (policy.Inspection, '_open', Mock(side_effect=[12, 13])),
                (policy.Inspection, '_metadata', Mock()),
                (policy.Inspection, '_read', Mock(return_value=raw)),
                (policy.MANIFEST, 'validate_manifest', Mock(return_value=manifest)),
                (policy.ACCOUNTS, 'snapshot', Mock(return_value=databases)),
                (M, 'verify_account', Mock()),
                (M.EXPLICIT, 'issue', Mock())]:
                stack.enter_context(patch.object(target, name, value))
            with self.assertRaisesRegex(ValueError, 'guest identity missing after sysusers') as caught:
                M.prepare_start(controller, 'autotest', Mock(), result)
            self.assertIn('reboot through guest preparation', result['inspection_guidance'])
            self.assertEqual(str(caught.exception), 'guest identity missing after sysusers')
            M.verify_account.assert_not_called()
            M.EXPLICIT.issue.assert_not_called()
        self.assertTrue(root.closed)
        self.assertTrue(guest.closed)

    def test_constructor_error_receipt_is_preserved_without_wrapping(self):
        error = RuntimeError('constructor cleanup failed')
        error.receipt = {'cleanup_errors': ['retained descriptor close failure']}
        guest = Resource()
        controller = SimpleNamespace(admission=SimpleNamespace(guest_lock=Mock(return_value=guest)),
                                     lease=object())
        entry = dict(name='autotest', enabled=True, manifest_sha256=PIN)
        result = {}
        with patch.object(M.P, '_registry', return_value={'guests': [entry]}), \
                patch.object(M.P.POLICY, 'Inspection', side_effect=error):
            with self.assertRaises(RuntimeError) as caught:
                M.prepare_start(controller, 'autotest', Mock(), result)
        self.assertIs(caught.exception, error)
        self.assertEqual(caught.exception.receipt,
                         {'cleanup_errors': ['retained descriptor close failure']})
        self.assertIn('Inspect current enrollment/storage', result['inspection_guidance'])
        self.assertTrue(guest.closed)


class StopControls(unittest.TestCase):
    def setUp(self):
        profile = patch.object(M.P.CONTEXT.MANAGEMENT, 'guest_management_profile', return_value='managed-experimental')
        profile.start()
        self.addCleanup(profile.stop)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.entry = dict(name='autotest', enabled=False)
        self.config = dict(name='autotest')
        self.controller = object()
        self.guard = Mock()
        self.result = {}
        for target, name, value in [
            (M.P, '_registry', Mock(return_value={'guests': [self.entry]})),
            (M.P, '_configs', Mock(return_value=[self.config])),
            (M.P, 'physical_memory', Mock(return_value=8 * 1024**3)),
            (M.U, 'render_slice', Mock(return_value='slice')),
            (M.U, 'render_managed', Mock(return_value='service')),
            (M, 'retain_units', Mock(return_value=[])),
            (M.D, 'verify_loaded', Mock()),
            (M.D, 'command', Mock(return_value='')),
            (M, 'stop_properties', Mock(return_value=dict(ActiveState='inactive',
                                                        SubState='dead', Result='success', Job=''))),
            (M.time, 'sleep', Mock())]:
            self.stack.enter_context(patch.object(target, name, value))

    def test_disabled_current_run_still_stops_once(self):
        M.stop(self.controller, 'autotest', self.guard, self.result)
        M.D.command.assert_called_once_with(['/usr/bin/systemctl', '--no-block', 'stop', '--',
                                             'a733-managed-autotest.service'], limit=1024)
        self.assertTrue(self.result['stopped'])
        self.assertFalse(self.result['stop_uncertain'])
        M.P._configs.assert_called_once_with(self.controller,
                                             [dict(self.entry, enabled=True)], self.guard)

    def test_stop_ack_failure_never_retries(self):
        M.D.command.side_effect = OSError('lost acknowledgement')
        with self.assertRaises(OSError):
            M.stop(self.controller, 'autotest', self.guard, self.result)
        self.assertTrue(self.result['queue_uncertain'])
        self.assertTrue(self.result['stop_uncertain'])
        M.D.command.assert_called_once()
        M.stop_properties.assert_not_called()

    def test_stop_timeout_never_claims_clean_or_retries(self):
        with patch.object(M.time, 'monotonic', side_effect=[0, 421]):
            with self.assertRaisesRegex(M.A.AdmissionError, 'stop timeout'):
                M.stop(self.controller, 'autotest', self.guard, self.result)
        self.assertTrue(self.result['stop_uncertain'])
        self.assertNotIn('stopped', self.result)
        M.D.command.assert_called_once()
        M.stop_properties.assert_not_called()

    def test_failed_service_never_claims_clean(self):
        M.stop_properties.return_value = dict(ActiveState='failed', SubState='failed',
                                              Result='exit-code', Job='')
        with self.assertRaisesRegex(M.A.AdmissionError, 'managed service failed'):
            M.stop(self.controller, 'autotest', self.guard, self.result)
        self.assertNotIn('stopped', self.result)
        M.D.command.assert_called_once()


class Validation(unittest.TestCase):
    def test_strict_allowlists(self):
        invalid = [('start', 'firstkvm', None, 'none'), ('start', '--help', None, 'none'),
                   ('start', '../disk', None, 'none'), ('start', 'autotest', PIN, 'none'),
                   ('enable', 'autotest', None, 'none'), ('enable', 'autotest', PIN.upper(), 'none'),
                   ('status', 'autotest', None, 'none'), ('disable', 'autotest', None, 'other'),
                   ('start', 'autotest', None, 'managed-power-key-v1')]
        for args in invalid:
            with self.subTest(args=args), self.assertRaises((ValueError, TypeError, M.A.AdmissionError)):
                M.validate(*args)

    def test_current_registry_must_have_exact_guest(self):
        for registry in (None, {'guests': []}, {'guests': [{'name': 'autotest'}] * 2}):
            with self.assertRaises((ValueError, M.A.AdmissionError)):
                M.entry_for(registry, 'autotest')

    def test_active_loaded_unit_or_job_refuses(self):
        for active, job in [('active', ''), ('activating', ''), ('inactive', '12')]:
            value = dict(LoadState='loaded', ActiveState=active, SubState='dead', Job=job)
            with patch.object(M.D, 'properties', return_value=value), self.assertRaises((ValueError, M.A.AdmissionError)):
                M.inactive('a733-managed-autotest.service')

    def test_changed_existing_unit_never_overwritten(self):
        directory = Resource()
        directory.fd, directory.check = 9, Mock()
        with ExitStack() as stack, patch.object(M.P, 'RamDirectory', return_value=directory), \
                patch.object(M.P, 'publish', side_effect=ValueError('existing publication differs')) as publish, \
                patch.object(M.P, 'Source') as source:
            with self.assertRaisesRegex(ValueError, 'differs'):
                M.retain_units(stack, {'a733-managed-autotest.service': b'fixed'}, Mock(), publish=True)
            publish.assert_called_once_with(directory, 'a733-managed-autotest.service', b'fixed',
                                            0o444, M.P.UNIT_LIMIT)
            source.assert_not_called()

    def test_missing_locked_account_refuses_with_preparation_guidance(self):
        accounts = M.P.POLICY.ACCOUNTS
        with patch.object(accounts, 'snapshot', return_value={}), \
                patch.object(accounts, 'validate_accounts', side_effect=ValueError('missing')):
            with self.assertRaisesRegex(RuntimeError, 'reboot through guest preparation'):
                M.verify_account({'name': 'autotest'}, Mock())

    def test_stop_observation_is_bounded_and_strict(self):
        rows = 'ActiveState=inactive\nSubState=dead\nResult=success\nJob=\n'
        with patch.object(M.D, 'command', return_value=rows) as command:
            self.assertEqual(M.stop_properties('unit', .3)['ActiveState'], 'inactive')
            self.assertEqual(command.call_args.kwargs['timeout'], .3)
        for rows in ('ActiveState=inactive\n', 'Job=\nJob=\n', 'Other=unexpected\n'):
            with patch.object(M.D, 'command', return_value=rows), self.assertRaises((ValueError, M.A.AdmissionError)):
                M.stop_properties('unit', 1)


if __name__ == '__main__':
    unittest.main()
