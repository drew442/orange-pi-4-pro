"""Native root RAM/flock tests with explicit board/runtime/account substitutes.

No storage, guest boot, KVM or deployment acceptance follows from these tests.
"""
import importlib.util
import io
import fcntl
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'operation_admission_tests', BASE / 'scripts/guest-operation-admission.py')
O = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(O)
A = O.ADMISSION


class RootBoundary(unittest.TestCase):
    def test_nonroot_refused_before_native_runtime_reads(self):
        with patch.object(O.os, 'getresuid', return_value=(1000, 1000, 1000)), \
                patch.object(O.CONTEXT, 'read_system') as read:
            with self.assertRaisesRegex(A.AdmissionError, 'actual root'):
                O.ManualGuestOperation('one')
            read.assert_not_called()

    def test_actual_nonroot_process_refused(self):
        code = """
import importlib.util, os, sys
spec = importlib.util.spec_from_file_location('op', sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
if os.geteuid() == 0:
    os.setgroups([])
    os.setgid(65534)
    os.setuid(65534)
try:
    m.ManualGuestOperation('one')
except m.ADMISSION.AdmissionError as error:
    assert 'actual root' in str(error), str(error)
else:
    raise AssertionError('nonroot admitted')
"""
        result = subprocess.run([sys.executable, '-c', code, SPEC.origin],
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_local_gid_uses_bounded_local_parser(self):
        with patch.object(O.ACCOUNTS, 'snapshot', return_value={
                'group': b'root:x:0:\nkvm:x:108:\n'}):
            self.assertEqual(O.local_kvm_gid(), 108)
        for data in (b'root:x:0:\n', b'kvm:x:0:\n', b'kvm:x:10:\nkvm:x:11:\n',
                     b'kvm:x:01:\n', b'kvm:x:10:'):
            with self.subTest(data=data), patch.object(O.ACCOUNTS, 'snapshot',
                                                       return_value={'group': data}):
                with self.assertRaises((ValueError, A.AdmissionError)):
                    O.local_kvm_gid()

    def test_manager_fixed_query_and_parser_with_native_pipe(self):
        native = subprocess.Popen
        for response, code, accepted in ((b'running\n', 0, True),
                                          (b'running\nrunning\n', 0, False),
                                          (b'running\n', 1, False),
                                          (b'x' * 65, 0, False)):
            def spawn(argv, **kwargs):
                self.assertEqual(argv, ['/usr/bin/systemctl', 'show',
                                        '--property=SystemState', '--value'])
                self.assertEqual(kwargs['env'], {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin',
                                                  'LC_ALL': 'C'})
                return native([sys.executable, '-c',
                               'import os; os.write(1, ' + repr(response) +
                               '); raise SystemExit(' + str(code) + ')'], **kwargs)
            with self.subTest(response=response, code=code), \
                    patch.object(O.subprocess, 'Popen', side_effect=spawn):
                if accepted:
                    self.assertEqual(O.manager_state(), 'running')
                else:
                    with self.assertRaises(A.AdmissionError):
                        O.manager_state()

    def test_manager_kill_failure_still_attempts_wait_and_closes_pipe(self):
        native = subprocess.Popen
        for after_effect in (False, True):
            with self.subTest(after_effect=after_effect):
                process = native([sys.executable, '-c', 'import time; time.sleep(20)'],
                                 stdout=subprocess.PIPE, stdin=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
                kill, wait = process.kill, process.wait
                waited = []
                def failing_kill():
                    if after_effect:
                        kill()
                    raise OSError('kill acknowledgement failed')
                def checked_wait(*args, **kwargs):
                    waited.append(True)
                    return wait(*args, **kwargs)
                try:
                    with patch.object(O.subprocess, 'Popen', return_value=process), \
                            patch.object(O.select, 'select', return_value=([], [], [])), \
                            patch.object(process, 'kill', side_effect=failing_kill), \
                            patch.object(process, 'wait', side_effect=checked_wait):
                        with self.assertRaises((OSError, subprocess.TimeoutExpired)):
                            O.manager_state()
                    self.assertTrue(waited)
                    self.assertTrue(process.stdout.closed)
                finally:
                    if process.poll() is None:
                        kill()
                    wait(timeout=5)
                    process.stdout.close()

    def test_manager_deadline_refusal_kills_and_reaps_query(self):
        native = subprocess.Popen
        processes = []
        def spawn(*args, **kwargs):
            process = native([sys.executable, '-c', 'import time; time.sleep(20)'], **kwargs)
            processes.append(process)
            return process
        with patch.object(O.subprocess, 'Popen', side_effect=spawn), \
                patch.object(O.select, 'select', return_value=([], [], [])):
            with self.assertRaisesRegex(A.AdmissionError, 'timeout'):
                O.manager_state()
        self.assertIsNotNone(processes[0].returncode)
        self.assertTrue(processes[0].stdout.closed)


@unittest.skipUnless(os.getresuid() == (0, 0, 0), 'native protected /run fixture requires root')
class Operation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='a733-operation-test-', dir='/run')
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'a733-admission'
        self.boot = str(uuid.uuid4())
        for substitute in (patch.object(A, 'RUNTIME_PARENT', self.tmp.name),
                           patch.object(A, 'RUNTIME', str(self.path)),
                           patch.object(O.CONTEXT, 'read_system',
                                        return_value=(self.boot + '\n').encode()),
                           patch.object(O.CONTEXT, 'host_identity'),
                           patch.object(O.CONTEXT.MANAGEMENT, 'runtime_guard'),
                           patch.object(O.ACCOUNTS, 'snapshot',
                                        return_value={'group': b'root:x:0:\nkvm:x:108:\n'}),
                           patch.object(O, 'manager_state', return_value='running')):
            substitute.start()
            self.addCleanup(substitute.stop)
        self.admission = A.initialize(self.boot, 108, lambda: 'running')
        self.addCleanup(self.admission.close)
        owner = self.admission.bootstrap_owner
        self.admission.prepare_managed(owner.lease)
        with self.admission.guest_lock('one', owner.lease, create=True):
            pass
        owner.open()
        owner.lease.close()

    def operation(self, **kwargs):
        item = O.ManualGuestOperation('one', **kwargs)
        self.addCleanup(item.close)
        return item

    def test_canonical_module_readonly_locks_and_lifetime(self):
        another = O.CONTEXT.module('unused', 'scripts/guest-admission.py')
        self.assertIs(A, another)
        operation = self.operation()
        self.assertIs(type(operation.lease), A.Lease)
        lease, guest = operation.lease, operation.guest_lock
        self.assertFalse(os.get_inheritable(lease.fd))
        A._held_lock(lease.fd, 'READ', os.getpid())
        A._held_lock(guest.fd, 'WRITE', os.getpid())
        with self.admission.admit() as other:
            with self.assertRaises(A.AdmissionError):
                self.admission.guest_lock('one', other)
        operation.close()
        self.assertIsNone(lease.fd)
        self.assertIsNone(guest.fd)
        operation.close()
        with self.assertRaises(A.AdmissionError):
            operation.recheck()
        with self.admission.admit() as other, self.admission.guest_lock('one', other):
            pass

    def test_existing_operation_finishes_after_closure_new_call_refused(self):
        operation = self.operation()
        owner = self.admission.close_gate('shutdown')
        operation.recheck()
        with self.assertRaises(A.AdmissionError):
            owner.exclusive()
        operation.close()
        with owner.exclusive():
            pass
        with self.assertRaisesRegex(A.AdmissionError, 'admission closed'):
            O.ManualGuestOperation('one')

    def test_unknown_name_unprepared_runtime_and_create(self):
        with self.assertRaises(FileNotFoundError):
            O.ManualGuestOperation('two')
        with O.ManualGuestOperation('two', create=True):
            pass
        with patch.object(A, 'RUNTIME', str(self.path / 'missing')):
            with self.assertRaises(FileNotFoundError):
                O.ManualGuestOperation('one')

    def test_pid1_stopping_refuses_before_guest_lock(self):
        for state in ('stopping', 'offline', 'unknown'):
            with self.subTest(state=state), patch.object(O, 'manager_state', return_value=state):
                with self.assertRaisesRegex(A.AdmissionError, 'PID1'):
                    O.ManualGuestOperation('one')
        for state in ('starting', 'running', 'degraded'):
            with self.subTest(state=state), patch.object(O, 'manager_state', return_value=state):
                with O.ManualGuestOperation('one'):
                    pass

    def test_managed_marker_refuses_acquisition_and_recheck(self):
        operation = self.operation()
        operation.admission.mark_managed('one', operation.lease, operation.guest_lock,
                                    guard=lambda: None)
        with self.assertRaisesRegex(A.AdmissionError, 'managed guest'):
            operation.recheck()
        operation.close()
        with self.assertRaisesRegex(A.AdmissionError, 'managed guest'):
            O.ManualGuestOperation('one')

    def test_missing_marker_directory_refuses(self):
        (self.path / 'managed').rmdir()
        with self.assertRaises(FileNotFoundError):
            O.ManualGuestOperation('one')
        # Failed partial construction must release its guest and lease.
        with self.admission.admit() as lease, self.admission.guest_lock('one', lease):
            pass

    def test_actual_unlock_and_wrong_mode_refused(self):
        for target, mode in (('_lease', None), ('_guest_lock', None), ('_lease', fcntl.LOCK_EX)):
            with self.subTest(target=target, mode=mode):
                operation = self.operation()
                fcntl.flock(getattr(operation, target).fd,
                            fcntl.LOCK_UN if mode is None else mode)
                with self.assertRaises(A.AdmissionError):
                    operation.recheck()
                operation.close()

    def test_named_replacement_refused(self):
        operation = self.operation()
        path = self.path / 'guests/one.lock'
        path.rename(path.with_suffix('.old'))
        path.touch(mode=0o640)
        os.chown(path, 0, 108)
        with self.assertRaises(A.AdmissionError):
            operation.recheck()

    def test_nested_and_foreign_thread_refused_without_releasing_locks(self):
        operation = self.operation()
        with self.assertRaisesRegex(A.AdmissionError, 'nested'):
            O.ManualGuestOperation('two', create=True)
        errors = []
        def foreign():
            for action in (operation.recheck, operation.close):
                try:
                    action()
                except A.AdmissionError as error:
                    errors.append(error)
        thread = threading.Thread(target=foreign)
        thread.start()
        thread.join(5)
        self.assertEqual(len(errors), 2)
        operation.recheck()

    def test_distinct_helper_loads_share_nested_operation_boundary(self):
        operation = self.operation()
        spec = importlib.util.spec_from_file_location(
            'distinct_operation_import', BASE / 'scripts/guest-operation-admission.py')
        duplicate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(duplicate)
        self.assertIs(duplicate.ADMISSION, O.ADMISSION)
        self.assertIs(duplicate._ACTIVE, O._ACTIVE)
        with self.assertRaisesRegex(A.AdmissionError, 'nested'):
            duplicate.ManualGuestOperation('two', create=True)
        operation.recheck()
        self.assertIs(O._ACTIVE.operation, operation)

    def test_native_boot_and_runtime_rechecked(self):
        operation = self.operation()
        for obj, attribute in ((O.CONTEXT, 'host_identity'),
                               (O.CONTEXT.MANAGEMENT, 'runtime_guard')):
            with patch.object(obj, attribute, side_effect=ValueError('changed')):
                with self.assertRaisesRegex(ValueError, 'changed'):
                    operation.recheck()

    def test_close_error_visible_all_owned_resources_closed_once(self):
        operation = self.operation()
        guest, lease = operation._guest_lock, operation._lease
        native = os.close
        failed = guest.fd
        calls = []
        def close(fd):
            calls.append(fd)
            native(fd)
            if fd == failed:
                raise OSError('close failed after release')
        with patch.object(A.os, 'close', side_effect=close):
            with self.assertRaisesRegex(OSError, 'close failed'):
                operation.close()
            operation.close()
        self.assertEqual(calls.count(failed), 1)
        self.assertIsNone(guest.fd)
        self.assertIsNone(lease.fd)
        self.assertIsNone(operation.admission.directory)

    def test_forked_process_cannot_use_or_close_operation(self):
        operation = self.operation()
        pid = os.fork()
        if pid == 0:
            try:
                for action in (operation.recheck, operation.close):
                    try:
                        action()
                    except A.AdmissionError:
                        pass
                    else:
                        os._exit(2)
                os._exit(0)
            except BaseException:
                os._exit(3)
        _, status = os.waitpid(pid, 0)
        self.assertEqual(status, 0)
        operation.recheck()

    def test_backup_rechecks_actual_marker_after_inspection_before_gzip(self):
        spec = importlib.util.spec_from_file_location('operation_sender_late', BASE / 'scripts/backup-guest.py')
        sender = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sender)
        class Inspection:
            manifest = {'config': {'disk': {}}}
            disk_record = {}
            def __init__(self, *unused):
                operation = O._ACTIVE.operation
                operation.admission.mark_managed(
                    'one', operation._lease, operation._guest_lock, guard=lambda: None)
            def __enter__(self):
                return self
            def __exit__(self, *unused):
                pass
            def recheck(self):
                pass
        class History:
            def __init__(self, inspection):
                pass
            def recheck(self):
                pass
            def close(self):
                pass
        writer = io.BytesIO()
        with patch.object(sender, 'OPERATION', O), \
                patch.object(sender.POLICY, 'Inspection', Inspection), \
                patch.object(sender, 'History', History):
            with self.assertRaisesRegex(A.AdmissionError, 'managed guest'):
                sender.send('one', '0' * 64, writer)
        self.assertEqual(writer.getvalue(), b'')

    def test_backup_managed_refuses_before_inspection_or_bytes(self):
        spec = importlib.util.spec_from_file_location('operation_sender', BASE / 'scripts/backup-guest.py')
        sender = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sender)
        with self.admission.admit() as lease, self.admission.guest_lock('one', lease) as guest:
            self.admission.mark_managed('one', lease, guest, guard=lambda: None)
        writer = io.BytesIO()
        with patch.object(sender, 'OPERATION', O), patch.object(sender.POLICY, 'Inspection') as inspect:
            with self.assertRaisesRegex(A.AdmissionError, 'managed guest'):
                sender.send('one', '0' * 64, writer)
            inspect.assert_not_called()
        self.assertEqual(writer.getvalue(), b'')


if __name__ == '__main__':
    unittest.main()
