"""External fixture-completion contracts; no hardware acceptance evidence."""
import base64
from contextlib import ExitStack
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location('completion', Path(__file__).resolve().parents[1] /
                                              'scripts/complete-managed-fixture.py')
C = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(C)
BOOT = '01234567-89ab-cdef-0123-456789abcdef'
RUN = 'abcdef01-2345-6789-abcd-ef0123456789'
BASE = dict(boot_id=BOOT, run_id=RUN, supervisor=dict(pid=22, start_ticks=90),
            qemu=dict(pid=32, start_ticks=100))
EXPECTED = dict(boot_id=BOOT, run_id=RUN, main_pid=22, main_start_ticks=90)


def successful_receipt():
    return dict(operation='stop', name='autokvm', success=True, stage='complete', stopped=True,
                expected_identity=dict(EXPECTED), cleanup_errors=[], intent_published=True,
                intent={'durable': True}, signal_attempted=True, signal_acknowledged=True,
                queue_attempted=False, queue_acknowledged=False, queue_uncertain=False,
                intent_uncertain=False, signal_uncertain=False, stop_uncertain=False,
                pid1_stop_timeout_armed=False, metadata_confers_run_trust=False,
                completed_cohort_qualification_required=True,
                unit_status=dict(LoadState='loaded', ActiveState='inactive', SubState='dead',
                                 MainPID='0', Result='success', Job='', ExecMainCode='1', ExecMainStatus='0'))


class Clock:
    def __init__(self):
        self.now = 0

    def time(self):
        return self.now

    def sleep(self, duration):
        self.now += duration


class CompletionTests(unittest.TestCase):
    def test_plan_has_no_installed_import_probe_or_mutation(self):
        with patch.object(C, 'load_installed', side_effect=AssertionError('import')), \
                patch.object(C, 'observe', side_effect=AssertionError('observe')), \
                patch.object(C, 'publish', side_effect=AssertionError('mutation')), \
                patch.object(C, 'stop_once', side_effect=AssertionError('stop')), \
                patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(C.main([]), 0)
        plan = json.loads(output.getvalue())
        self.assertEqual(plan['operation'], 'plan')
        self.assertFalse(plan['hardware_acceptance'])

    def test_interval_starts_after_http_and_identity_observation(self):
        clock = Clock()
        snapshots = []
        def snapshot():
            snapshots.append(clock.now)
            return BASE
        def probe():
            clock.now += 27
            return {'status': 200}
        baseline, evidence = C.stable_interval(snapshot, probe, clock=clock.time, sleep=clock.sleep)
        self.assertEqual(baseline, BASE)
        self.assertEqual(clock.now, 162)
        self.assertEqual(evidence['stable_seconds'], 135)
        self.assertEqual(snapshots[1], 27)
        self.assertEqual(snapshots[-1], 162)

    def test_changed_pid_start_ticks_boot_or_run_refuses(self):
        for field, value in [('qemu', dict(pid=32, start_ticks=101)), ('run_id', 'different'),
                             ('boot_id', 'different')]:
            with self.subTest(field=field):
                clock = Clock()
                changed = dict(BASE, **{field: value})
                snapshot = Mock(side_effect=[BASE, BASE, changed])
                with self.assertRaisesRegex(ValueError, 'identity changed'):
                    C.stable_interval(snapshot, lambda: {}, clock=clock.time, sleep=clock.sleep)
                self.assertEqual(clock.now, 15)

    def test_http_failure_never_starts_interval(self):
        clock = Clock()
        with self.assertRaisesRegex(RuntimeError, 'probe'):
            C.stable_interval(lambda: BASE, Mock(side_effect=RuntimeError('probe')),
                              clock=clock.time, sleep=clock.sleep)
        self.assertEqual(clock.now, 0)

    def complete_mocks(self, directory, stop=None):
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(C, '__file__', str(directory / 'complete-managed-fixture.py')))
        stack.enter_context(patch.object(C, 'private_ram'))
        stack.enter_context(patch.object(C, 'load_installed', return_value=NS()))
        stack.enter_context(patch.object(C, 'stable_interval', return_value=(BASE, {'stable_seconds': 135})))
        observe = stack.enter_context(patch.object(C, 'observe', return_value=BASE))
        stop_mock = stack.enter_context(patch.object(C, 'stop_once', return_value=stop or {'uncertain': False}))
        return observe, stop_mock

    def test_one_durable_intent_for_boot_run_and_retry_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            _, stop = self.complete_mocks(directory)
            real_fsync = os.fsync
            with patch.object(C.os, 'fsync', wraps=real_fsync) as fsync:
                first = C.complete(BOOT, 2, RUN)
                self.assertEqual(fsync.call_count, 4)
            self.assertTrue(first['success'])
            self.assertFalse(first['completed_cohort_authenticated'])
            self.assertFalse(first['disk_or_run_trust'])
            second = C.complete(BOOT, 2, RUN)
            self.assertFalse(second['success'])
            self.assertIn('FileExistsError', second['error'])
            self.assertEqual(stop.call_count, 1)
            intent = directory / ('complete-' + BOOT + '-' + RUN + '.intent.json')
            self.assertEqual(json.loads(intent.read_text())['command'], C.stop_command(EXPECTED))
            stop.assert_called_once_with(EXPECTED)
            self.assertEqual(intent.stat().st_mode & 0o777, 0o600)

    def test_uncertain_stop_is_retained_and_never_resent(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            _, stop = self.complete_mocks(directory, {'uncertain': True, 'error': 'timeout'})
            first = C.complete(BOOT, 2)
            self.assertFalse(first['success'])
            receipt = directory / ('complete-' + BOOT + '-' + RUN + '.receipt.json')
            self.assertEqual(json.loads(receipt.read_text())['error'], 'timeout')
            self.assertFalse(C.complete(BOOT, 2)['success'])
            self.assertEqual(stop.call_count, 1)

    def test_changed_final_identity_prevents_intent_and_stop(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            observe, stop = self.complete_mocks(directory)
            observe.return_value = dict(BASE, run_id='changed')
            result = C.complete(BOOT, 2)
            self.assertFalse(result['success'])
            self.assertEqual(list(directory.iterdir()), [])
            stop.assert_not_called()

    def test_source_pin_refuses_before_executing_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            raw = b'raise AssertionError("untrusted code executed")\n'
            (directory / 'qualify-managed-boot.py').write_bytes(raw)
            (directory / 'qualify-managed-boot.py').chmod(0o644)
            actual_fstat = os.fstat
            def root_owned(fd):
                info = list(actual_fstat(fd))
                info[4:6] = [0, 0]
                return os.stat_result(info)
            with patch.object(C, '__file__', str(directory / 'complete-managed-fixture.py')), \
                    patch.object(C, 'private_ram'), patch.object(C, 'QUALIFIER_BYTES', len(raw)), \
                    patch.object(C.os, 'fstat', side_effect=root_owned):
                with self.assertRaisesRegex(ValueError, 'staged qualifier differs'):
                    C.load_installed()

    def run_fake_stop(self, code, **kwargs):
        with patch.object(C, 'COMMAND', [sys.executable, '-I', '-B', '-c', code]), \
                patch.object(C.subprocess, 'Popen', wraps=subprocess.Popen) as popen:
            with ExitStack() as stack:
                for key, value in kwargs.items():
                    stack.enter_context(patch.object(C, key, value))
                result = C.stop_once(EXPECTED)
            self.assertEqual(popen.call_count, 1)
            return result

    def test_stop_preserves_full_stdout_stderr_rc_and_native_receipt(self):
        receipt = successful_receipt()
        output = json.dumps(receipt) + '\n'
        code = 'import sys; sys.stdout.write(' + repr(output) + '); sys.stderr.write("progress\\n")'
        result = self.run_fake_stop(code)
        self.assertFalse(result['uncertain'])
        self.assertEqual(result['returncode'], 0)
        self.assertEqual(base64.b64decode(result['stdout']['data_base64']), output.encode())
        self.assertEqual(base64.b64decode(result['stderr']['data_base64']), b'progress\n')
        self.assertEqual(result['receipt'], receipt)

    def test_stop_timeout_is_uncertain_and_not_retried(self):
        result = self.run_fake_stop('import time; time.sleep(10)', STOP_TIMEOUT=0.03)
        self.assertTrue(result['uncertain'])
        self.assertIn('timeout', result['error'])
        self.assertLess(result['returncode'], 0)

    def test_stop_output_overflow_is_explicit_and_bounded(self):
        result = self.run_fake_stop('import sys; sys.stdout.write("x"*8192)', OUTPUT_LIMIT=100)
        self.assertTrue(result['uncertain'])
        self.assertTrue(result['output_incomplete'])
        self.assertLessEqual(result['stdout']['bytes'] + result['stderr']['bytes'], 100)

    def test_native_refusal_remains_uncertain(self):
        result = self.run_fake_stop('print("{\\"success\\":false}")')
        self.assertTrue(result['uncertain'])
        self.assertIn('native bound stop refused', result['error'])

    def test_subprocess_always_receives_all_four_stable_identity_arguments(self):
        receipt = successful_receipt()
        suffix = C.stop_command(EXPECTED)[len(C.COMMAND):]
        code = ('import sys,json; assert sys.argv[1:] == ' + repr(suffix) +
                '; print(' + repr(json.dumps(receipt)) + ')')
        result = self.run_fake_stop(code)
        self.assertFalse(result['uncertain'])
        self.assertEqual(result['command'][-8:], suffix)
        self.assertEqual(result['expected_identity'], EXPECTED)

    def test_replacement_after_final_observation_keeps_original_bound_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            _, stop = self.complete_mocks(directory)
            replacement = dict(EXPECTED, run_id='fedcba98-7654-3210-fedc-ba9876543210', main_pid=77)
            current = dict(EXPECTED)
            actual_publish = C.publish
            def replace_after_intent(path, name, value):
                actual_publish(path, name, value)
                if name.endswith('.intent.json'):
                    current.update(replacement)
            def bound_stop(expected):
                # Model native refusal when the original run was replaced.
                self.assertEqual(expected, EXPECTED)
                self.assertNotEqual(expected, current)
                return {'uncertain': True, 'error': 'expected supervisor replaced'}
            stop.side_effect = bound_stop
            with patch.object(C, 'publish', side_effect=replace_after_intent):
                result = C.complete(BOOT, 2, RUN)
            self.assertFalse(result['success'])
            stop.assert_called_once_with(EXPECTED)
            self.assertEqual(current, replacement)
            self.assertFalse(C.complete(BOOT, 2, RUN)['success'])
            self.assertEqual(stop.call_count, 1)

    def test_inherited_pipe_writer_timeout_closes_both_readers(self):
        actual_popen = subprocess.Popen
        managers = []
        def launch(*args, **kwargs):
            manager = actual_popen(*args, **kwargs)
            managers.append(manager)
            return manager
        # The descendant outlives the killed manager and still owns both writers.
        code = ('import subprocess,sys,time; '
                'subprocess.Popen([sys.executable,"-c","import time; time.sleep(0.4)"]); '
                'time.sleep(10)')
        with patch.object(C, 'COMMAND', [sys.executable, '-I', '-B', '-c', code]), \
                patch.object(C, 'STOP_TIMEOUT', 0.1), \
                patch.object(C.subprocess, 'Popen', side_effect=launch):
            result = C.stop_once(EXPECTED)
        self.assertEqual(len(managers), 1)
        self.assertTrue(result['uncertain'])
        self.assertTrue(result['output_incomplete'])
        self.assertIn('timeout', result['error'])
        self.assertTrue(managers[0].stdout.closed)
        self.assertTrue(managers[0].stderr.closed)
        self.assertNotIn('cleanup_errors', result)

    def test_native_identity_or_checkpoint_closure_mismatch_is_refused(self):
        for field, value in [('expected_identity', dict(EXPECTED, main_pid=77)),
                             ('unit_status', dict(successful_receipt()['unit_status'], ExecMainStatus='1')),
                             ('intent_published', False), ('signal_acknowledged', False),
                             ('stop_uncertain', True), ('cleanup_errors', [{'error': 'uncertain'}])]:
            with self.subTest(field=field):
                receipt = successful_receipt()
                receipt[field] = value
                code = 'print(' + repr(json.dumps(receipt)) + ')'
                self.assertTrue(self.run_fake_stop(code)['uncertain'])

    def test_fake_api_refuses_nonrunning_head_without_guest_or_disk_lock(self):
        class Resource:
            def __enter__(self):
                return self
            def __exit__(self, *unused):
                return None
            def check(self):
                pass
            def recheck(self):
                pass
            def close(self):
                pass
        class Lease(Resource):
            exclusive = False
        class Admission(Resource):
            bootstrap_owner = None
            @classmethod
            def open_existing(cls, *unused):
                return admission
            def admit(self):
                return lease
            def guest_lock(self, *unused, **kwargs):
                raise AssertionError('live guest lock forbidden')
        admission, lease = Admission(), Lease()
        lease.admission = admission
        controller = Resource()
        controller.context = NS(guard=lambda: None)
        controller.status = lambda: {'heads': {'autokvm': {'head': {'status': 'ready'}}}, 'registry': {}}
        P = NS(BOOT_POLICY=NS(Policy=lambda boot: Resource(),),
               NORMAL=NS(local_kvm_gid=lambda: 108, manager_state='state'))
        # The read-only policy exposes selected storage; no live disk Inspection.
        policy = Resource()
        policy.value = {'storage': {}}
        P.BOOT_POLICY.Policy = lambda boot: policy
        M = NS(P=P, A=NS(Admission=Admission, Lease=Lease),
               L=NS(StateController=lambda *args, **kwargs: controller), entry_for=lambda *args: {})
        with patch.object(C, 'native'):
            with self.assertRaisesRegex(ValueError, 'current fixture head differs'):
                C.observe(M, BOOT)


if __name__ == '__main__':
    unittest.main()
