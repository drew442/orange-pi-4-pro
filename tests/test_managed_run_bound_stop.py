"""Run-bound stop race regressions; all signaling and installed I/O are mocked."""
from contextlib import ExitStack
import importlib.util
import copy
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location('bound_stop_test',
    Path(__file__).resolve().parents[1] / 'scripts/manage-guests.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
BOOT = '33333333-3333-4333-8333-333333333333'
RUN = '44444444-4444-4444-8444-444444444444'
EXPECTED = dict(boot_id=BOOT, run_id=RUN, main_pid=123, main_start_ticks=456)
UNIT = 'a733-managed-autotest.service'
TERMINAL = dict(LoadState='loaded', ActiveState='inactive', SubState='dead', MainPID='0',
                Result='success', Job='', ExecMainCode='1', ExecMainStatus='0')


class Resource:
    fd = 88

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        pass

    def check(self):
        pass


class BoundStop(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.result = dict(cleanup_errors=[])
        self.directory = Resource()
        self.directory.publish = Mock(return_value=dict(durable=True, published=True))
        self.metadata = Mock(return_value=dict(status='running'))
        self.process = Mock()
        self.signal = Mock()
        self.close = Mock()
        for obj, name, value in (
            (M.P, 'RamDirectory', Mock(return_value=Resource())),
            (M.P.STORE, 'RootDirectory', Mock(return_value=self.directory)),
            (M, 'bound_metadata', self.metadata), (M, 'bound_process', self.process),
            (M.os, 'pidfd_open', Mock(return_value=91)), (M.os, 'close', self.close),
            (M.signal, 'pidfd_send_signal', self.signal),
            (M, 'stop_properties', Mock(return_value=TERMINAL)), (M.D, 'command', Mock()),
            (M.time, 'sleep', Mock())):
            self.stack.enter_context(patch.object(obj, name, value))

    def run_stop(self):
        return M.bound_stop(object(), 'autotest', Mock(), self.result, EXPECTED,
                            UNIT, {}, [Resource()])

    def test_same_run_ready_completes_and_closes_pidfd(self):
        self.run_stop()
        self.signal.assert_called_once_with(91, M.signal.SIGTERM, None, 0)
        self.close.assert_called_once_with(91)
        self.assertTrue(self.metadata.call_args.kwargs['ready'])
        self.assertTrue(self.metadata.call_args.kwargs['after_signal'])
        self.assertTrue(self.result['stopped'])
        self.assertFalse(self.result['stop_uncertain'])
        self.assertFalse(self.result['pid1_stop_timeout_armed'])
        M.D.command.assert_not_called()
        filename, payload = self.directory.publish.call_args.args
        self.assertEqual(filename, BOOT + '-' + RUN + '.json')
        self.assertIn(RUN.encode(), payload)

    def test_replacement_after_final_recheck_only_signals_pinned_process(self):
        # Model the kernel's pidfd semantics: replacement can change the name,
        # but the already-open descriptor still designates the original task.
        processes, descriptor = {'current': 123}, {}
        def opened(pid, flags):
            descriptor[91] = processes['current']
            return 91
        M.os.pidfd_open.side_effect = opened
        calls = []
        def send(fd, *unused):
            processes['current'] = 789
            calls.append(descriptor[fd])
        self.signal.side_effect = send
        self.metadata.side_effect = [dict(status='running')] * 3 + [ValueError('replacement head')]
        with self.assertRaisesRegex(ValueError, 'replacement'):
            self.run_stop()
        self.assertEqual(calls, [123])
        self.assertEqual(processes['current'], 789)
        self.assertTrue(self.result['stop_uncertain'])
        self.assertNotIn('stopped', self.result)
        M.D.command.assert_not_called()

    def test_pid_reuse_after_pidfd_open_refuses_before_intent(self):
        self.process.side_effect = [None, ValueError('PID reused')]
        with self.assertRaisesRegex(ValueError, 'PID reused'):
            self.run_stop()
        self.signal.assert_not_called()
        self.directory.publish.assert_not_called()
        self.close.assert_called_once_with(91)

    def test_esrch_is_uncertain_and_never_resent(self):
        self.signal.side_effect = ProcessLookupError('gone')
        with self.assertRaises(ProcessLookupError):
            self.run_stop()
        self.signal.assert_called_once()
        self.assertTrue(self.result['signal_uncertain'])
        self.assertTrue(self.result['intent_published'])
        self.close.assert_called_once_with(91)

    def test_terminal_replacement_does_not_complete(self):
        self.metadata.side_effect = [dict(status='running')] * 4 + [ValueError('replacement ready')]
        with self.assertRaisesRegex(ValueError, 'replacement ready'):
            self.run_stop()
        self.assertTrue(self.result['stop_uncertain'])
        self.assertNotIn('stopped', self.result)
        self.signal.assert_called_once()

    def test_duplicate_intent_refuses_without_signal(self):
        self.directory.publish.side_effect = FileExistsError('existing intent')
        with self.assertRaises(FileExistsError):
            self.run_stop()
        self.signal.assert_not_called()
        self.assertTrue(self.result['intent_uncertain'])
        self.close.assert_called_once_with(91)

    def test_unsupported_pidfd_refuses_without_intent_or_signal(self):
        M.os.pidfd_open.side_effect = OSError('unsupported')
        with self.assertRaisesRegex(OSError, 'unsupported'):
            self.run_stop()
        self.directory.publish.assert_not_called()
        self.signal.assert_not_called()
        self.close.assert_not_called()

    def test_missing_pidfd_api_refuses(self):
        with patch.object(M.os, 'pidfd_open', None):
            with self.assertRaisesRegex(M.A.AdmissionError, 'pidfd support'):
                self.run_stop()
        self.signal.assert_not_called()

    def test_pidfd_close_runs_when_resource_cleanup_fails(self):
        self.directory.close = Mock(side_effect=OSError('close failed'))
        with self.assertRaises(Exception):
            self.run_stop()
        self.close.assert_called_once_with(91)

    def test_timeout_does_not_resend(self):
        M.stop_properties.return_value = dict(TERMINAL, ActiveState='active', MainPID='123')
        with patch.object(M.time, 'monotonic', side_effect=[0, 421]):
            with self.assertRaisesRegex(M.A.AdmissionError, 'timeout'):
                self.run_stop()
        self.signal.assert_called_once()
        self.assertTrue(self.result['stop_uncertain'])

    def test_expected_exit_with_pid1_status_lag_waits_without_resignal(self):
        M.stop_properties.side_effect = [dict(TERMINAL, ActiveState='active', MainPID='123'), TERMINAL]
        poller = Mock()
        poller.poll.return_value = [(91, M.select.POLLIN | M.select.POLLHUP)]
        with patch.object(M.select, 'poll', return_value=poller), \
                patch.object(M.A, '_process', side_effect=AssertionError('exited PID must not be read')):
            self.run_stop()
        self.assertTrue(self.result['stopped'])
        self.signal.assert_called_once()
        self.assertEqual(M.stop_properties.call_count, 2)



class ProtectedMetadata(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('bound_checkpoint_fixture',
            Path(__file__).with_name('test_guest_checkpoint_records.py'))
        fixture = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fixture)
        CP = M.P.CP
        _, intent, _ = fixture.cohort(changed=True)
        oldpin = intent['old_manifest']['sha256']
        self.entry = {k: intent[k] for k in ('name', 'uid', 'gid')}
        self.entry.update(manifest_sha256=oldpin, enabled=True, fixture_profile='none',
                          shutdown_profile='qmp-guest-shutdown-v1')
        previous = M.P.HEAD.initial_head(intent['storage'],
                    {k: intent[k] for k in ('name', 'uid', 'gid')}, oldpin)
        self.run = dict(schema=1, profile=M.L.RUN_PROFILE, **CP.common(intent), automatic=False,
                        manifest_sha256=oldpin, previous_head=previous,
                        previous_head_sha256=M.P.digest(M.P.HEAD.canonical(previous)),
                        registry_generation=2, fixture_profile=self.entry['fixture_profile'],
                        shutdown_profile=self.entry['shutdown_profile'])
        self.raw_run = M.L.canonical_run(self.run)
        intent['admission_sha256'] = M.P.digest(self.raw_run)
        self.intent_raw = CP.RECORDS.canonical_intent(intent)
        self.intent = intent
        self.heads = CP._heads(self.run, self.intent_raw, intent)
        self.registry = dict(schema=1, profile=M.P.REGISTRY.PROFILE, generation=2,
                             storage=intent['storage'], guests=[dict(self.entry)])
        self.records = {self.run['run_id'] + '.json': self.raw_run}
        self.raw_head = M.P.HEAD.canonical(self.heads[2])
        self.store, self.runs = Resource(), Resource()
        self.store.read = Mock(side_effect=lambda *args: self.raw_head)
        self.runs.read = Mock(side_effect=self.read_record)
        self.store.child = Mock(return_value=self.runs)
        self.controller = SimpleNamespace(boot_id=intent['boot_id'], _storage=Mock(),
                         _registry=Mock(side_effect=lambda *args: (None,
                             M.P.REGISTRY.canonical(self.registry), copy.deepcopy(self.registry))),
                         _head_store=Mock(return_value=self.store))
        self.expected = dict(EXPECTED, boot_id=intent['boot_id'], run_id=intent['run_id'])
        self.witness = {}
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(M.A, '_boot_id', return_value=intent['boot_id']))
        self.observe(after_signal=False)
        self.assertNotEqual(oldpin, intent['new_manifest']['sha256'])

    def read_record(self, name, limit):
        if name not in self.records:
            raise FileNotFoundError(2, 'missing', name)
        return self.records[name]

    def observe(self, after_signal=True, ready=False):
        with ExitStack() as readers:
            return M.bound_metadata(self.controller, readers, self.entry['name'], self.expected,
                self.entry, Mock(), witness=self.witness, after_signal=after_signal, ready=ready)

    def advance(self, index, *, registry=False, completion=False):
        CP = M.P.CP
        self.records[self.run['run_id'] + '.intent.json'] = self.intent_raw
        self.raw_head = M.P.HEAD.canonical(self.heads[index])
        if registry:
            self.registry['guests'][0]['manifest_sha256'] = self.intent['new_manifest']['sha256']
            self.registry['generation'] = 3
        if completion:
            raw_registry = M.P.REGISTRY.canonical(self.registry)
            completed = dict(schema=1, profile=CP.RECORDS.COMPLETION_PROFILE,
                **CP.common(self.run), intent_sha256=M.P.digest(self.intent_raw),
                outcome_sha256=self.intent['outcome']['sha256'],
                ready_head=CP.reference(self.raw_head, embedded=True),
                manifest_sha256=self.intent['new_manifest']['sha256'],
                registry_generation=self.registry['generation'], registry_sha256=M.P.digest(raw_registry))
            self.records[self.run['run_id'] + '.completed.json'] = CP.RECORDS.canonical_completion(completed)

    def test_actual_old_new_manifest_checkpoint_and_publication_windows(self):
        for index in (3, 4):
            self.advance(index)
            self.assertEqual(self.observe()['status'], self.heads[index]['status'])
        self.advance(5)
        self.assertEqual(self.observe()['manifest_sha256'], self.intent['new_manifest']['sha256'])
        with self.assertRaises(M.BoundObservationPending):
            self.observe(ready=True)  # ready before registry publication
        self.advance(5, registry=True)
        with self.assertRaises(M.BoundObservationPending):
            self.observe(ready=True)  # registry before completion publication
        self.advance(5, registry=True, completion=True)
        head = self.observe(ready=True)
        self.assertEqual(head['manifest_sha256'], self.registry['guests'][0]['manifest_sha256'])
        self.assertEqual(self.witness['run_raw'], self.raw_run)
        self.assertNotEqual(head['manifest_sha256'], self.run['manifest_sha256'])

    def test_before_signal_rejects_changed_head_or_registry(self):
        self.advance(3)
        with self.assertRaisesRegex(M.A.AdmissionError, 'original running'):
            self.observe(after_signal=False)
        self.raw_head = M.P.HEAD.canonical(self.heads[2])
        self.registry['guests'][0]['manifest_sha256'] = self.intent['new_manifest']['sha256']
        with self.assertRaisesRegex(M.A.AdmissionError, 'original running'):
            self.observe(after_signal=False)

    def test_arbitrary_manifest_and_policy_changes_fail(self):
        self.advance(5, registry=True, completion=True)
        self.registry['guests'][0]['manifest_sha256'] = 'e' * 64
        with self.assertRaisesRegex(M.A.AdmissionError, 'manifest/generation'):
            self.observe()
        self.registry['guests'][0] = dict(self.entry, enabled=False)
        with self.assertRaisesRegex(M.A.AdmissionError, 'identity/policy'):
            self.observe()

    def test_replacement_and_mutated_immutable_run_fail(self):
        self.advance(5, registry=True, completion=True)
        changed = dict(self.heads[5], run_id=RUN)
        self.raw_head = M.P.HEAD.canonical(changed)
        with self.assertRaisesRegex(M.A.AdmissionError, 'head identity'):
            self.observe()
        self.raw_head = M.P.HEAD.canonical(self.heads[5])
        changed_run = dict(self.run, registry_generation=1)
        self.records[self.run['run_id'] + '.json'] = M.L.canonical_run(changed_run)
        with self.assertRaisesRegex(M.A.AdmissionError, 'immutable bound run'):
            self.observe()

    def test_legal_concurrent_snapshot_is_pending(self):
        self.advance(4)
        self.store.read.side_effect = [self.raw_head, M.P.HEAD.canonical(self.heads[5])]
        with self.assertRaises(M.BoundObservationPending):
            self.observe()

    def test_intent_published_between_lookup_and_final_head_is_pending(self):
        running = self.raw_head
        self.advance(4)
        checkpoint = self.raw_head
        self.raw_head = running
        self.store.read.side_effect = [running, checkpoint]
        first = True
        original = self.read_record
        def late_intent(name, limit):
            nonlocal first
            if name.endswith('.intent.json') and first:
                first = False
                raise FileNotFoundError(2, 'missing', name)
            return original(name, limit)
        self.runs.read.side_effect = late_intent
        with self.assertRaises(M.BoundObservationPending):
            self.observe()

    def test_one_signal_waits_through_real_ready_registry_completion_windows(self):
        directory = Resource()
        directory.publish = Mock(return_value=dict(durable=True, published=True))
        properties_calls = []
        def properties(*args):
            properties_calls.append(True)
            if len(properties_calls) == 2:
                self.advance(5, registry=True)
            elif len(properties_calls) == 3:
                self.advance(5, registry=True, completion=True)
            return TERMINAL
        with ExitStack() as mocks:
            for obj, name, value in (
                (M.P, 'RamDirectory', Mock(return_value=Resource())),
                (M.P.STORE, 'RootDirectory', Mock(return_value=directory)),
                (M, 'bound_process', Mock()),
                (M.os, 'pidfd_open', Mock(return_value=91)),
                (M.os, 'close', Mock()), (M.time, 'sleep', Mock()),
                (M, 'stop_properties', Mock(side_effect=properties)),
                (M.signal, 'pidfd_send_signal', Mock(side_effect=lambda *args: self.advance(5)))):
                mocks.enter_context(patch.object(obj, name, value))
            receipt = dict(cleanup_errors=[])
            M.bound_stop(self.controller, self.entry['name'], Mock(), receipt, self.expected,
                         UNIT, self.entry, [Resource()])
            self.assertTrue(receipt['stopped'])
            self.assertFalse(receipt['stop_uncertain'])
            M.signal.pidfd_send_signal.assert_called_once_with(91, M.signal.SIGTERM, None, 0)
            self.assertEqual(len(properties_calls), 3)
            self.assertEqual(self.raw_head, M.P.HEAD.canonical(self.heads[5]))

    def test_completion_cross_record_conflict_fails(self):
        self.advance(5, registry=True, completion=True)
        name = self.run['run_id'] + '.completed.json'
        CP = M.P.CP
        completed = CP.RECORDS.validate_completion(self.records[name])
        completed['outcome_sha256'] = 'f' * 64
        self.records[name] = CP.RECORDS.canonical_completion(completed)
        with self.assertRaisesRegex(M.A.AdmissionError, 'completion differs'):
            self.observe(ready=True)


class ProcessBinding(unittest.TestCase):
    def test_exact_native_process_contract_and_reuse(self):
        group = '/a733.slice/a733-guests.slice/' + UNIT
        identity = dict(pid=123, start_ticks=456)
        props = dict(LoadState='loaded', ActiveState='active', SubState='running', MainPID='123',
                     Job='', ControlGroup=group)
        raw = {'cgroup': ('0::' + group + '\n').encode(),
               'cmdline': b'/usr/bin/python3\0-I\0-B\0' + M.U.SUPERVISOR.encode() + b'\0autotest\0'}
        with ExitStack() as stack:
            stack.enter_context(patch.object(M.D, 'properties', return_value=props))
            process = stack.enter_context(patch.object(M.A, '_process', return_value=(identity, 1)))
            stack.enter_context(patch.object(M.A, '_status', return_value=dict(Uid=['0']*4, Gid=['0']*4)))
            stack.enter_context(patch.object(M.A, '_proc_read', side_effect=lambda p: raw[p.rsplit('/', 1)[1]]))
            stack.enter_context(patch.object(M.os.path, 'samefile', return_value=True))
            M.bound_process(UNIT, 'autotest', EXPECTED)
            process.return_value = (dict(identity, start_ticks=457), 1)
            with self.assertRaisesRegex(M.A.AdmissionError, 'identity changed'):
                M.bound_process(UNIT, 'autotest', EXPECTED)
            process.return_value = (identity, 1)
            raw['cmdline'] += b'extra\0'
            with self.assertRaisesRegex(M.A.AdmissionError, 'full command'):
                M.bound_process(UNIT, 'autotest', EXPECTED)

    def test_invalid_identity_and_normal_stop_validation(self):
        M.validate('stop', 'autotest')
        M.validate('stop', 'autotest', expected=EXPECTED)
        for invalid in ({}, dict(EXPECTED, main_pid=0), dict(EXPECTED, main_start_ticks=True),
                        dict(EXPECTED, boot_id='bad'), dict(EXPECTED, run_id=None)):
            with self.subTest(invalid=invalid), self.assertRaises(Exception):
                M.validate('stop', 'autotest', expected=invalid)
        with self.assertRaises(Exception):
            M.validate('start', 'autotest', expected=EXPECTED)

    def test_cli_normal_compatibility_and_bound_arguments(self):
        runtime = SimpleNamespace(json_receipt=lambda r: '{}')
        with patch.object(M, 'manage', return_value=dict(success=True)) as manage, \
                patch.object(M.L.CONTEXT, 'module', return_value=runtime), patch('builtins.print'):
            self.assertEqual(M.main(['stop', 'autotest']), 0)
            self.assertIsNone(manage.call_args.args[-1])
            M.main(['stop', 'autotest', '--expected-boot-id', BOOT, '--expected-run-id', RUN,
                    '--expected-main-pid', '123', '--expected-main-start-ticks', '456'])
            self.assertEqual(manage.call_args.args[-1], EXPECTED)
            with self.assertRaises(SystemExit):
                M.main(['start', 'autotest', '--expected-main-pid', '123'])


if __name__ == '__main__':
    unittest.main()
