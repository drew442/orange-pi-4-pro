"""Native fork/UNIX/QMP plumbing with mocked host authority, never QEMU/KVM.

Fixtures deliberately replace A733 storage/root/credentials and executable checks.
The real fork barrier, FD inheritance, UNIX peer PID, QMP parser/commands, console
collector, raw wait, timeouts and forced-child cleanup execute on the host.
"""
import copy
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import struct
import tempfile
import threading
import time
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location(
    'managed_supervisor_test', Path(__file__).resolve().parents[1] / 'scripts/managed-guest-supervisor.py')
S = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(S)
RUN = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
BOOT = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
GREETING = {'QMP': {'version': {'qemu': {'major': 7, 'minor': 2, 'micro': 22},
                               'package': 'explicit native surrogate'}, 'capabilities': []}}
SHUTDOWN = {'event': 'SHUTDOWN', 'data': {'guest': True, 'reason': 'guest-shutdown'}}


class Lock:
    def __init__(self, fd, admission=None):
        self.fd, self.admission = fd, admission
        self.closed = False

    def recheck(self):
        if self.closed:
            raise ValueError('fixture borrowed lock closed')
        os.fstat(self.fd)
        return self.fd


class Permit(S.ADMISSION.Permit):
    def __init__(self, value):
        self._value = copy.deepcopy(value)
        self.data = json.dumps(value, sort_keys=True).encode()
        self.closed = False

    def recheck(self):
        if self.closed:
            raise ValueError('fixture permit closed')

    def close(self):
        self.closed = True


class Admission:
    def __init__(self):
        self.gid = os.getgid()
        self.published = False
        self.fail_publication = False

    def recheck(self):
        pass

    def publish_permit(self, value, lease, guest_lock, *, guard, deadline=None):
        guard()
        if self.fail_publication:
            raise ValueError('fixture publication failure')
        # Actual child must still be blocked in the same fork, before socket creation.
        identity = S.PROCESS._identity(value['child']['pid'])
        assert identity['ppid'] == os.getpid()
        assert identity['start_ticks'] == value['child']['start_ticks']
        for key, lock in (('lease', lease), ('guest', guest_lock)):
            pin = value['locks'][key]
            info = os.fstat(lock.fd)
            assert pin == dict(fd=lock.fd, dev=info.st_dev, ino=info.st_ino)
        self.published = True
        self.value = copy.deepcopy(value)
        return Permit(value)


class Run(S.LIFECYCLE.ManagedRun):
    """Explicit mocked persistent authority; no durable-run acceptance claim."""
    def __init__(self, lease, guest):
        self._lease, self._guest = lease, guest
        self.status = 'admitted'
        self.events = []
        self._record = dict(storage={'native': 'surrogate'}, manifest_sha256='a' * 64,
                            automatic=False)
        self.closed = False

    @property
    def config(self):
        return dict(name='surrogate', uid=os.getresuid()[0], gid=os.getresgid()[0])

    @property
    def lease(self):
        return self._lease

    @property
    def guest_lock(self):
        return self._guest

    @property
    def run_id(self):
        return RUN

    @property
    def boot_id(self):
        return BOOT

    @property
    def head_sha256(self):
        return {'admitted': 'b', 'running': 'c', 'stopping': 'd', 'quarantined': 'e'}[self.status] * 64

    def recheck(self):
        self.lease.recheck()
        self.guest_lock.recheck()
        if self.closed:
            raise ValueError('fixture run closed')
        return dict(status=self.status)

    def mark_running(self):
        self.events.append('running')
        self.status = 'running'
        return dict(success=True)

    def mark_stopping(self):
        self.events.append('stopping')
        self.status = 'stopping'
        return dict(success=True)

    def quarantine(self, reason):
        self.events.append('quarantined')
        self.status = 'quarantined'
        return dict(success=True, code=reason)


class Witness(S.PROCESS.ChildWitness):
    """Mock executable/credential policy, real direct identity/peer/raw wait."""
    def __init__(self, pid, ticks, permit):
        self.pid, self.start_ticks = pid, ticks
        self._parent = os.getpid()
        self._parent_ticks = S.PROCESS._identity(self._parent)['start_ticks']
        self._status = None
        self._wait_failed = False

    def validate_child(self):
        return self._child()

    def verify_qmp_peer(self, connection):
        peer = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        assert peer == (self.pid, os.getresuid()[0], os.getresgid()[0])
        return self.validate_child()


class NativeSupervisor(S.Supervisor):
    """Surrogate only: bypass board/root inputs and fixed credential-drop exec."""
    def __init__(self, run, folder, mode='normal'):
        self.folder, self.mode = folder, mode
        with mock.patch.object(S.os, 'getuid', return_value=0), mock.patch.object(S.os, 'geteuid', return_value=0):
            super().__init__(run)

    def _prepare(self):
        self.qmp_directory = self._own_fd(os.open(self.folder, os.O_RDONLY | os.O_DIRECTORY))
        self.payload_pin = 'f' * 64
        self.qemu_pin = dict(path=S.QEMU, bytes=123, sha256='0' * 64)

    def _check_paths(self):
        self.run.recheck()

    def _child_exec(self):
        # Every non-stdio descriptor other than the two inherited locks is closed.
        locks = {self.run.lease.fd, self.run.guest_lock.fd}
        for fd in locks:
            assert os.get_inheritable(fd)
        existing = set()
        for name in os.listdir('/proc/self/fd'):
            try:
                os.fstat(int(name))
                existing.add(int(name))
            except OSError:
                pass
        assert existing == {0, 1, 2} | locks, existing
        if self.mode == 'before-qmp':
            os._exit(7)
        if self.mode == 'ignore-term':
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(self.folder + '/qmp.sock')
        listener.listen(1)
        connection, _ = listener.accept()
        connection.sendall(json.dumps(GREETING).encode() + b'\n')
        reader = connection.makefile('rb')
        while True:
            message = json.loads(reader.readline())
            name = message['execute']
            # A parent-only status transition is verified independently in tests;
            # wire order records exact command issuance into console bytes.
            os.write(1, (name + '\n').encode())
            if name == 'cont' and self.mode == 'overflow':
                for _ in range(100):
                    os.write(1, b'x' * 65536)
            if name == 'system_powerdown' and self.mode == 'hang':
                time.sleep(30)
            if name == 'system_powerdown' and self.mode == 'malformed':
                connection.sendall(b'{bad}\n')
                time.sleep(30)
            reply = {'return': {}, 'id': message['id']}
            connection.sendall(json.dumps(reply).encode() + b'\n')
            if name == 'system_powerdown' or name == 'cont' and self.mode == 'self-shutdown':
                event = copy.deepcopy(SHUTDOWN)
                if self.mode == 'host-shutdown':
                    event['data']['guest'] = False
                connection.sendall(json.dumps(event).encode() + b'\n')
                connection.close()
                os._exit(9 if self.mode == 'nonzero' else 0)


@unittest.skipUnless(hasattr(os, 'fork'), 'Linux fork required')
class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='a733-supervisor-')
        self.addCleanup(self.folder.cleanup)
        self.admission = Admission()
        self.fds = []
        for name, mode in (('lease', fcntl.LOCK_SH), ('guest', fcntl.LOCK_EX)):
            path = Path(self.folder.name, name)
            path.write_bytes(b'')
            fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
            self.fds.append(fd)
            fcntl.flock(fd, mode | fcntl.LOCK_NB)
            self.addCleanup(os.close, fd)
        self.run = Run(Lock(self.fds[0], self.admission), Lock(self.fds[1]))
        self.patch = mock.patch.object(S.PROCESS, 'ChildWitness', Witness)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        # All actual protocol deadlines remain positive and bounded, but failures
        # finish quickly in the host suite.
        for key, value in dict(CONNECT_TIMEOUT=.4, GRACEFUL_TIMEOUT=.4,
                               EXIT_TIMEOUT=.4, TERM_TIMEOUT=.2, KILL_TIMEOUT=.2).items():
            patcher = mock.patch.object(S, key, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.supervisor = None
        self.addCleanup(self.cleanup_supervisor)

    def cleanup_supervisor(self):
        if self.supervisor is not None and not self.supervisor._closed:
            try:
                self.supervisor.close()
            except S.SupervisionError:
                pass

    def start(self, mode='normal'):
        self.supervisor = NativeSupervisor(self.run, self.folder.name, mode)
        return self.supervisor.start()

    def assert_borrowed_open(self):
        self.assertFalse(self.run.closed)
        self.run.lease.recheck()
        self.run.guest_lock.recheck()

    def test_real_fork_peer_commands_raw_zero_and_typed_outcome(self):
        supervisor = self.start()
        self.assertTrue(self.admission.published)
        self.assertEqual(self.run.events, ['running'])
        outcome = supervisor.stop()
        self.assertIsInstance(outcome, S.StoppedRun)
        self.assertEqual(self.run.events, ['running', 'stopping'])
        evidence = outcome.recheck()
        self.assertEqual(evidence['raw_wait_status'], 0)
        self.assertFalse(evidence['checkpoint_authorized'])
        self.assertFalse(evidence['hardware_acceptance'])
        sent = [m['message']['execute'] for m in evidence['qmp'] if m['direction'] == 'send']
        self.assertEqual(sent, ['qmp_capabilities', 'cont', 'system_powerdown'])
        self.assertIn(b'system_powerdown', supervisor.console_snapshot())
        self.assertIs(supervisor.stop(), outcome)
        self.assert_borrowed_open()
        supervisor.close()
        with self.assertRaises(ValueError):
            outcome.recheck()
        self.assert_borrowed_open()

    def test_durable_running_precedes_cont_and_stopping_precedes_stop(self):
        original = S.TRANSPORT.Channel.command
        seen = []
        def command(channel, name, *args, **kwargs):
            seen.append((name, self.run.status))
            return original(channel, name, *args, **kwargs)
        with mock.patch.object(S.TRANSPORT.Channel, 'command', command):
            self.start().stop()
        self.assertEqual(seen, [('qmp_capabilities', 'admitted'), ('cont', 'running'),
                                ('system_powerdown', 'stopping')])

    def test_actual_self_shutdown_sends_no_stop_command(self):
        supervisor = self.start('self-shutdown')
        deadline = time.monotonic() + .3
        while supervisor.channel.shutdown_message is None and time.monotonic() < deadline:
            supervisor.poll()
            time.sleep(.005)
        outcome = supervisor.stop()
        self.assertNotIn(b'system_powerdown', supervisor.console_snapshot())
        self.assertTrue(outcome.channel.clean_shutdown)

    def test_slow_stop_authority_and_publication_leave_full_response_budget(self):
        supervisor = self.start()
        clock = mock.Mock(wraps=time)
        offset = [0]
        clock.monotonic.side_effect = lambda: time.monotonic() + offset[0]
        original_paths = supervisor._check_paths
        original_stopping = self.run.mark_stopping

        def paths():
            original_paths()
            offset[0] += 61

        def stopping():
            result = original_stopping()
            offset[0] += 61
            return result

        with mock.patch.object(S, 'time', clock), \
                mock.patch.object(S, 'GRACEFUL_TIMEOUT', 60), \
                mock.patch.object(supervisor, '_check_paths', side_effect=paths), \
                mock.patch.object(self.run, 'mark_stopping', side_effect=stopping), \
                mock.patch.object(supervisor.channel, 'command', wraps=supervisor.channel.command) as command, \
                mock.patch.object(supervisor.channel, 'pump', wraps=supervisor.channel.pump) as pump:
            outcome = supervisor.stop()
        self.assertEqual(command.call_args, mock.call('system_powerdown', timeout=10))
        self.assertGreater(pump.call_args.kwargs['timeout'], 59)
        self.assertEqual(outcome.evidence['raw_wait_status'], 0)
        self.assertFalse(supervisor.receipt['forced'])

    def test_stop_command_and_event_share_response_budget(self):
        supervisor = self.start()
        clock = mock.Mock(wraps=time)
        offset = [0]
        clock.monotonic.side_effect = lambda: time.monotonic() + offset[0]
        original_command = supervisor.channel.command

        def command(*args, **kwargs):
            result = original_command(*args, **kwargs)
            offset[0] += 4
            return result

        with mock.patch.object(S, 'time', clock), \
                mock.patch.object(S, 'GRACEFUL_TIMEOUT', 60), \
                mock.patch.object(supervisor.channel, 'command', side_effect=command) as sent, \
                mock.patch.object(supervisor.channel, 'pump', wraps=supervisor.channel.pump) as pump:
            supervisor.stop()
        self.assertEqual(sent.call_args, mock.call('system_powerdown', timeout=10))
        self.assertGreater(pump.call_args.kwargs['timeout'], 55)
        self.assertLessEqual(pump.call_args.kwargs['timeout'], 56)

    def test_exhausted_stop_response_budget_quarantines_without_retry(self):
        supervisor = self.start()
        clock = mock.Mock(wraps=time)
        offset = [0]
        clock.monotonic.side_effect = lambda: time.monotonic() + offset[0]
        original_command = supervisor.channel.command

        def command(*args, **kwargs):
            result = original_command(*args, **kwargs)
            offset[0] += 61
            return result

        with mock.patch.object(S, 'time', clock), \
                mock.patch.object(S, 'GRACEFUL_TIMEOUT', 60), \
                mock.patch.object(supervisor.channel, 'command', side_effect=command) as sent, \
                mock.patch.object(supervisor.channel, 'pump') as pump:
            with self.assertRaisesRegex(S.SupervisionError, 'graceful shutdown deadline exceeded'):
                supervisor.stop()
            with self.assertRaisesRegex(ValueError, 'supervisor closed'):
                supervisor.stop()
        sent.assert_called_once_with('system_powerdown', timeout=10)
        pump.assert_not_called()
        self.assertEqual(self.run.status, 'quarantined')
        self.assertIsNone(supervisor._outcome)

    def test_failed_stop_publication_sends_no_powerdown(self):
        supervisor = self.start()
        with mock.patch.object(self.run, 'mark_stopping', side_effect=ValueError('stop publication failed')), \
                mock.patch.object(supervisor.channel, 'command', wraps=supervisor.channel.command) as command:
            with self.assertRaisesRegex(S.SupervisionError, 'stop publication failed'):
                supervisor.stop()
        command.assert_not_called()
        self.assertEqual(self.run.status, 'quarantined')
        self.assertIsNone(supervisor._outcome)

    def test_publication_failure_does_not_release_barrier_and_reaps_child(self):
        self.admission.fail_publication = True
        with self.assertRaises(S.SupervisionError) as caught:
            self.start()
        self.assertFalse(Path(self.folder.name, 'qmp.sock').exists())
        self.assertEqual(self.run.status, 'quarantined')
        self.assertIsNotNone(caught.exception.receipt['raw_wait_status'])
        self.assert_borrowed_open()

    def test_publication_guard_waits_beyond_old_ten_second_barrier(self):
        original = self.admission.publish_permit
        def delayed(value, lease, guest_lock, *, guard, deadline=None):
            # One controlled native delay exercises the actual child select, not
            # a mocked timeout or QMP surrogate's running lifetime.
            time.sleep(11)
            return original(value, lease, guest_lock, guard=guard, deadline=deadline)
        with mock.patch.object(self.admission, 'publish_permit', delayed):
            supervisor = self.start()
        self.assertTrue(self.admission.published)
        self.assertEqual(supervisor.stop().evidence['raw_wait_status'], 0)

    def assert_no_launch_after_failure(self, receipt):
        self.assertFalse(Path(self.folder.name, 'qmp.sock').exists())
        self.assertEqual(self.run.status, 'quarantined')
        self.assertIsNotNone(receipt['raw_wait_status'])
        self.assertFalse(self.supervisor._owned_fds)
        self.assertFalse(self.supervisor.console.thread.is_alive())
        self.assert_borrowed_open()

    def test_expired_budget_refuses_guard_before_publication_mutation(self):
        original = self.admission.publish_permit
        def delayed(value, lease, guest_lock, *, guard, deadline=None):
            time.sleep(.15)
            return original(value, lease, guest_lock, guard=guard, deadline=deadline)
        with mock.patch.object(S, 'PERMIT_PUBLICATION_TIMEOUT', .1), \
                mock.patch.object(self.admission, 'publish_permit', delayed):
            with self.assertRaises(S.SupervisionError) as caught:
                self.start()
        self.assertFalse(self.admission.published)
        self.assertIsNone(self.supervisor.permit)
        self.assertIsInstance(caught.exception.cause, TimeoutError)
        self.assert_no_launch_after_failure(caught.exception.receipt)

    def test_guard_crossing_deadline_refuses_mutation(self):
        original = self.run.recheck
        def delayed_recheck():
            time.sleep(.15)
            return original()
        original_publish = self.admission.publish_permit
        def publish(value, lease, guest_lock, *, guard, deadline=None):
            with mock.patch.object(self.run, 'recheck', delayed_recheck):
                return original_publish(value, lease, guest_lock, guard=guard, deadline=deadline)
        with mock.patch.object(S, 'PERMIT_PUBLICATION_TIMEOUT', .1), \
                mock.patch.object(self.admission, 'publish_permit', publish):
            with self.assertRaises(S.SupervisionError) as caught:
                self.start()
        self.assertFalse(self.admission.published)
        self.assert_no_launch_after_failure(caught.exception.receipt)

    def test_publication_return_after_deadline_retains_closed_permit_without_release(self):
        original = self.admission.publish_permit
        def delayed(value, lease, guest_lock, *, guard, deadline=None):
            permit = original(value, lease, guest_lock, guard=guard, deadline=deadline)
            time.sleep(.15)
            return permit
        with mock.patch.object(S, 'PERMIT_PUBLICATION_TIMEOUT', .1), \
                mock.patch.object(self.admission, 'publish_permit', delayed):
            with self.assertRaises(S.SupervisionError) as caught:
                self.start()
        self.assertTrue(self.admission.published)
        self.assertTrue(self.supervisor.permit.closed)
        self.assert_no_launch_after_failure(caught.exception.receipt)

    def test_final_path_check_crossing_deadline_never_releases_child(self):
        self.supervisor = NativeSupervisor(self.run, self.folder.name)
        original = self.supervisor._check_paths
        def delayed():
            original()
            if self.admission.published:
                time.sleep(.15)
        with mock.patch.object(S, 'PERMIT_PUBLICATION_TIMEOUT', .1), \
                mock.patch.object(self.supervisor, '_check_paths', delayed):
            with self.assertRaises(S.SupervisionError) as caught:
                self.supervisor.start()
        self.assertIsInstance(caught.exception.cause, TimeoutError)
        self.assertTrue(self.supervisor.permit.closed)
        self.assert_no_launch_after_failure(caught.exception.receipt)

    def test_child_barrier_timeout_is_detected_and_never_released(self):
        original = self.admission.publish_permit
        def delayed(value, lease, guest_lock, *, guard, deadline=None):
            time.sleep(.1)
            return original(value, lease, guest_lock, guard=guard, deadline=deadline)
        with mock.patch.object(S, 'BARRIER_TIMEOUT', .02), \
                mock.patch.object(self.admission, 'publish_permit', delayed):
            with self.assertRaises(S.SupervisionError) as caught:
                self.start()
        self.assertEqual(caught.exception.receipt['raw_wait_status'], 125 << 8)
        self.assert_no_launch_after_failure(caught.exception.receipt)

    def test_parent_barrier_eof_exits_child_without_launch(self):
        def publish(*args, **kwargs):
            # Closing the sole parent writer models a parent that dies before
            # release; all other child copies were closed by the real fork.
            for fd in list(self.supervisor._owned_fds):
                if fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE == os.O_WRONLY:
                    self.supervisor._close_fd(fd)
            deadline = time.monotonic() + 1
            while S.PROCESS._identity(self.supervisor.pid)['state'] != b'Z':
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.005)
            raise ValueError('parent barrier writer closed')
        with mock.patch.object(self.admission, 'publish_permit', publish):
            with self.assertRaises(S.SupervisionError) as caught:
                self.start()
        self.assertEqual(caught.exception.receipt['raw_wait_status'], 125 << 8)
        self.assertFalse(self.admission.published)
        self.assert_no_launch_after_failure(caught.exception.receipt)

    def test_invalid_barrier_token_exits_child_without_launch(self):
        write = os.write
        def invalid(fd, data):
            return write(fd, b'X' if data == b'G' else data)
        with mock.patch.object(S.os, 'write', invalid):
            with self.assertRaises(S.SupervisionError) as caught:
                self.start()
        self.assertEqual(caught.exception.receipt['raw_wait_status'], 125 << 8)
        self.assertTrue(self.supervisor.permit.closed)
        self.assert_no_launch_after_failure(caught.exception.receipt)

    def test_child_rejects_parent_identity_change_after_release_token(self):
        identity = S.PROCESS._identity
        def changed(pid):
            value = identity(pid)
            # Change only the fork child's view of its parent's pinned birth.
            if os.getpid() != self.supervisor._parent and pid == self.supervisor._parent:
                value = dict(value, start_ticks=value['start_ticks'] + 1)
            return value
        with mock.patch.object(S.PROCESS, '_identity', changed):
            with self.assertRaises(S.SupervisionError) as caught:
                self.start()
        self.assertEqual(caught.exception.receipt['raw_wait_status'], 125 << 8)
        self.assert_no_launch_after_failure(caught.exception.receipt)

    def test_publication_error_retains_bounded_underlying_cause(self):
        def publish(*args, **kwargs):
            try:
                raise ValueError('barrier child is a zombie ' + 'x' * 3000)
            except ValueError as error:
                raise S.ADMISSION.PublicationError('directory', False) from error
        with mock.patch.object(self.admission, 'publish_permit', publish):
            with self.assertRaises(S.SupervisionError) as caught:
                self.start()
        error = caught.exception.receipt['permit_error']
        self.assertEqual((error['stage'], error['uncertain']), ('directory', False))
        self.assertEqual(error['cause']['type'], 'ValueError')
        self.assertTrue(error['cause']['message'].startswith('barrier child is a zombie'))
        self.assertEqual(len(error['cause']['message']), 2048)
        self.assert_no_launch_after_failure(caught.exception.receipt)

    def test_exit_before_socket_is_not_clean(self):
        with self.assertRaises(S.SupervisionError) as caught:
            self.start('before-qmp')
        self.assertEqual(caught.exception.receipt['raw_wait_status'], 7 << 8)
        self.assertEqual(self.run.status, 'quarantined')

    def test_nonzero_host_event_protocol_error_and_timeout_quarantine(self):
        # Each subtest needs a fresh socket destination and run authority.
        for mode in ('nonzero', 'host-shutdown', 'malformed', 'hang'):
            with self.subTest(mode=mode):
                self.run.status = 'admitted'
                path = Path(self.folder.name, 'qmp.sock')
                path.unlink(missing_ok=True)
                supervisor = self.start(mode)
                with self.assertRaises(S.SupervisionError) as caught:
                    supervisor.stop()
                self.assertEqual(self.run.status, 'quarantined')
                self.assertIsNotNone(caught.exception.receipt['raw_wait_status'])
                self.assert_borrowed_open()

    def test_console_overflow_quarantines_and_joins_before_fd_close(self):
        with mock.patch.object(S, 'CONSOLE_LIMIT', 1024):
            try:
                supervisor = self.start('overflow')
                time.sleep(.05)
                supervisor.poll()
            except S.SupervisionError:
                pass
            else:
                self.fail('overflow must fail')
        self.assertEqual(self.run.status, 'quarantined')
        self.assertFalse(self.supervisor.console.thread.is_alive())
        self.assertLessEqual(len(self.supervisor.console.snapshot()), 1024)
        self.assert_borrowed_open()

    def test_fork_refuses_other_python_threads(self):
        gate = threading.Event()
        thread = threading.Thread(target=gate.wait)
        thread.start()
        try:
            with self.assertRaises(S.SupervisionError):
                self.start()
        finally:
            gate.set()
            thread.join()
        self.assertIsNone(self.supervisor.pid)
        self.assertEqual(self.run.status, 'quarantined')

    def test_echild_forbids_signals_and_cannot_approve_shutdown(self):
        supervisor = self.start()
        with mock.patch.object(supervisor.witness, 'reap', side_effect=ChildProcessError()), \
                mock.patch.object(S.os, 'kill') as kill:
            with self.assertRaises(S.SupervisionError):
                supervisor.poll()
            kill.assert_not_called()
        # Test fixture owns the genuinely still-live native surrogate separately.
        os.kill(supervisor.pid, signal.SIGKILL)
        os.waitpid(supervisor.pid, 0)
        self.assertTrue(supervisor._wait_lost)
        self.assertEqual(self.run.status, 'quarantined')

    def test_fixed_exec_arguments_environment_and_only_run_selector(self):
        supervisor = NativeSupervisor(self.run, self.folder.name)
        with mock.patch.object(S.os, 'execve', side_effect=RuntimeError('intercept')) as execute:
            with self.assertRaises(RuntimeError):
                supervisor._child_exec = S.Supervisor._child_exec.__get__(supervisor)
                supervisor._child_exec()
        executable, args, environment = execute.call_args.args
        self.assertEqual(executable, '/usr/bin/setpriv')
        self.assertEqual(args[-6:], [S.PYTHON, '-I', '-B', S.LAUNCHER, '--run-id', RUN])
        self.assertIn('--inh-caps=-all', args)
        self.assertIn('--ambient-caps=-all', args)
        self.assertIn('--bounding-set=-all', args)
        self.assertIn('--no-new-privs', args)
        self.assertEqual(environment, {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'TZ': 'UTC'})
        supervisor.close()

    def test_partial_pipe_allocation_closes_owned_descriptors(self):
        supervisor = NativeSupervisor(self.run, self.folder.name)
        first = os.pipe2(os.O_CLOEXEC)
        with mock.patch.object(S.os, 'pipe2', side_effect=[first, OSError('pipe failure')]):
            with self.assertRaises(OSError):
                supervisor._fork()
        self.assertEqual(supervisor._owned_fds, set(first))
        supervisor.close()
        for fd in first:
            with self.assertRaises(OSError):
                os.fstat(fd)

    def test_existing_qmp_directory_is_never_repaired(self):
        supervisor = NativeSupervisor(self.run, self.folder.name)
        parent = os.open(self.folder.name, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, parent)
        path = Path(self.folder.name, 'qmp')
        path.mkdir(mode=0o777)
        path.chmod(0o777)
        with mock.patch.object(S.os, 'fchown') as chown, mock.patch.object(S.os, 'fchmod') as chmod:
            with self.assertRaisesRegex(ValueError, 'unsafe QMP'):
                supervisor._prepare_directory(parent, 'qmp', os.getgid(), 0o710)
            chown.assert_not_called()
            chmod.assert_not_called()
        self.assertEqual(path.stat().st_mode & 0o777, 0o777)
        supervisor.close()

    def test_existing_run_directory_refuses_even_if_metadata_would_match(self):
        supervisor = NativeSupervisor(self.run, self.folder.name)
        parent = os.open(self.folder.name, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, parent)
        Path(self.folder.name, RUN).mkdir()
        with self.assertRaisesRegex(ValueError, 'already exists'):
            supervisor._prepare_directory(parent, RUN, os.getgid(), 0o730, fresh=True)
        supervisor.close()

    def test_forced_cleanup_keeps_raw_signal(self):
        supervisor = self.start('hang')
        with mock.patch.object(supervisor, '_signal', wraps=supervisor._signal) as signals:
            with self.assertRaises(S.SupervisionError):
                supervisor.close()
        self.assertEqual(signals.call_args_list[0].args, (signal.SIGTERM,))
        self.assertTrue(supervisor.receipt['forced'])
        self.assertTrue(os.WIFSIGNALED(supervisor._raw_status))
        self.assert_borrowed_open()

    def test_ignored_term_escalates_to_kill_without_clean_outcome(self):
        supervisor = self.start('ignore-term')
        with mock.patch.object(supervisor, '_signal', wraps=supervisor._signal) as signals:
            with self.assertRaises(S.SupervisionError):
                supervisor.close()
        self.assertEqual([call.args[0] for call in signals.call_args_list],
                         [signal.SIGTERM, signal.SIGKILL])
        self.assertEqual(os.WTERMSIG(supervisor._raw_status), signal.SIGKILL)
        self.assertEqual(self.run.status, 'quarantined')
        self.assertFalse(supervisor.channel.clean_shutdown)

    def test_foreign_run_and_nonroot_refused(self):
        with self.assertRaisesRegex(ValueError, 'actual ManagedRun'):
            S.Supervisor(object())
        with mock.patch.object(S.os, 'getuid', return_value=1000):
            with self.assertRaisesRegex(ValueError, 'root supervisor'):
                S.Supervisor(self.run)

    def test_outcome_rejects_changed_head_and_borrowed_permit(self):
        supervisor = self.start()
        outcome = supervisor.stop()
        self.run.status = 'running'
        with self.assertRaises(ValueError):
            outcome.recheck()
        self.run.status = 'stopping'
        original = outcome.permit
        outcome.permit = Permit(self.admission.value)
        with self.assertRaises(ValueError):
            outcome.recheck()
        outcome.permit = original

    def test_outcome_stays_bound_to_the_raw_wait_owner_process_and_thread(self):
        supervisor = self.start()
        outcome = supervisor.stop()
        pid = os.fork()
        if pid == 0:
            try:
                outcome.recheck()
            except ValueError:
                os._exit(0)
            os._exit(1)
        self.assertEqual(os.waitpid(pid, 0), (pid, 0))
        refused = []
        def foreign_thread():
            try:
                outcome.recheck()
            except ValueError:
                refused.append(True)
        thread = threading.Thread(target=foreign_thread)
        thread.start()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(refused, [True])
        self.assertEqual(outcome.recheck()['raw_wait_status'], 0)


if __name__ == '__main__':
    unittest.main()
