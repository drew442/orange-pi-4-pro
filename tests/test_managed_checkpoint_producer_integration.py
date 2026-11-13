"""Native composition of actual managed-run, supervisor, stop and checkpoint APIs.

The protected-file fixture explicitly mocks A733 placement, host/account policy
and admission capabilities. This test adds real admission lock flocks and actual
Permit readers. The root Python child is a QMP surrogate: executable/KVM and
credential policy are explicitly mocked. Real fork, barrier, descriptor retention,
UNIX peer identity, QMP messages/EOF, console pipes, raw wait, lifecycle heads,
stopped Inspection/flock and checkpoint transaction execute on this host.
No QEMU, KVM, filesystem-cleanliness or hardware acceptance is claimed.
"""
from contextlib import ExitStack
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import socket
import stat
import struct
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'producer_integration_file_fixture', ROOT / 'tests/test_managed_guest_checkpoint.py')
F = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(F)
M, L, P = F.M, F.L, F.P
S = M.SUPERVISOR
ACTUAL_STOPPED_RUN = S.StoppedRun
GREETING = {'QMP': {'version': {'qemu': {'major': 7, 'minor': 2, 'micro': 22},
                               'package': 'explicit native surrogate'}, 'capabilities': []}}
SHUTDOWN = {'event': 'SHUTDOWN', 'data': {'guest': True, 'reason': 'guest-shutdown'}}


class NativeWitness(S.PROCESS.ChildWitness):
    """Mock executable/credential policy; retain real direct-child/peer/raw wait."""
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
        S.require(peer == (self.pid, os.getuid(), os.getgid()), 'actual surrogate UNIX peer differs')
        return self.validate_child()


class NativeSupervisor(S.Supervisor):
    """Replace only fixed executable/credential preparation with a native child."""
    def __init__(self, run, folder, disk, *, silent=False, exit_status=0):
        self.folder, self.disk = str(folder), str(disk)
        self.silent, self.exit_status = silent, exit_status
        super().__init__(run)

    def _prepare(self):
        self.qmp_directory = self._own_fd(os.open(self.folder, os.O_RDONLY | os.O_DIRECTORY))
        self.payload_pin = 'f' * 64
        self.qemu_pin = dict(path=S.QEMU, bytes=123, sha256='0' * 64)

    def _child_exec(self):
        # _fork has already executed the actual barrier, closed unrelated FDs and
        # marked only the exact borrowed lock descriptions inheritable.
        locks = {self.run.lease.fd, self.run.guest_lock.fd}
        for fd in locks:
            assert os.get_inheritable(fd)
        inherited = set()
        for name in os.listdir('/proc/self/fd'):
            try:
                os.fstat(int(name))
                inherited.add(int(name))
            except OSError:
                pass
        assert inherited == {0, 1, 2} | locks, inherited
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        path = self.folder + '/qmp.sock'
        listener.bind(path)
        # The surrogate stays root; actual guest credentials are explicitly
        # mocked by NativeWitness. Keep the real supervisor's socket metadata
        # validation and path/socket-inode retention checks intact.
        os.chown(path, self.run.config['uid'], self.run.config['gid'])
        listener.listen(1)
        connection, _ = listener.accept()
        connection.sendall(json.dumps(GREETING).encode() + b'\n')
        reader = connection.makefile('rb')
        while True:
            raw = reader.readline()
            if not raw:
                os._exit(91)
            message = json.loads(raw)
            command = message['execute']
            if not self.silent:
                os.write(1, (command + '\n').encode())
            if command == 'cont':
                # Surrogate workload changes only this private test disk.
                fd = os.open(self.disk, os.O_WRONLY)
                try:
                    assert os.write(fd, b'B' * 512) == 512
                    os.fsync(fd)
                finally:
                    os.close(fd)
            connection.sendall(json.dumps({'return': {}, 'id': message['id']}).encode() + b'\n')
            if command == 'system_powerdown':
                connection.sendall(json.dumps(SHUTDOWN).encode() + b'\n')
                reader.close()
                connection.close()
                listener.close()
                os._exit(self.exit_status)


@unittest.skipUnless(os.geteuid() == 0 and hasattr(os, 'fork'), 'root Linux native fixture required')
class NativeProducerComposition(unittest.TestCase):
    def setUp(self):
        # Reuse the actual protected-file/controller construction. Suppress only
        # the helper's fabricated producer setup transitions; all transitions
        # exercised below use the actual ManagedRun methods and publication I/O.
        with mock.patch.object(L.ManagedRun, 'mark_running'), \
             mock.patch.object(L.ManagedRun, 'mark_stopping'):
            F.ManagedCheckpoint.setUp(self)
        self.assertIs(type(self.run), L.ManagedRun)
        self.assertEqual(self.run.recheck()['status'], 'admitted')
        self.disk.write_bytes(b'A' * 512)
        self.supervisor = None
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        # The shared helper patches its synthetic StoppedRun producer; restore
        # the actual class for this complete composition, without altering it.
        self.stack.enter_context(mock.patch.object(S, 'StoppedRun', ACTUAL_STOPPED_RUN))
        self.stack.enter_context(mock.patch.object(S.PROCESS, 'ChildWitness', NativeWitness))
        for key, value in dict(CONNECT_TIMEOUT=2, GRACEFUL_TIMEOUT=2,
                               EXIT_TIMEOUT=2, TERM_TIMEOUT=.2, KILL_TIMEOUT=.2).items():
            self.stack.enter_context(mock.patch.object(S, key, value))
        self.qmp = self.base / 'qmp'
        self.qmp.mkdir(mode=0o700)
        self.permits = self.base / 'permits'
        self.permits.mkdir(mode=0o700)
        self.lease_path, self.guest_lock_path = self.base / 'lease.lock', self.base / 'guest.lock'
        self.lease_path.write_bytes(b'')
        self.guest_lock_path.write_bytes(b'')
        for path in (self.lease_path, self.guest_lock_path):
            path.chmod(0o400)
        lease_fd = os.open(self.lease_path, os.O_RDONLY | os.O_CLOEXEC)
        fcntl.flock(lease_fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        self.lease.fd = lease_fd
        self.addCleanup(os.close, lease_fd)
        fixture = self
        original_lease_check = type(self.lease).recheck
        original_guest_check = type(self.run.guest_lock).recheck
        original_guest_close = type(self.run.guest_lock).close

        def lease_check(lease):
            original_lease_check(lease)
            os.fstat(lease.fd)
            return lease.fd

        def close_guest(lock):
            original_guest_close(lock)
            fd = getattr(lock, 'fd', None)
            lock.fd = None
            if fd is not None:
                os.close(fd)

        def guest_check(lock):
            original_guest_check(lock)
            if getattr(lock, 'fd', None) is None:
                lock.fd = os.open(fixture.guest_lock_path, os.O_RDONLY | os.O_CLOEXEC)
                try:
                    fcntl.flock(lock.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BaseException:
                    os.close(lock.fd)
                    lock.fd = None
                    raise
                fixture.addCleanup(close_guest, lock)
            os.fstat(lock.fd)
            return lock.fd

        self.stack.enter_context(mock.patch.object(type(self.lease), 'recheck', lease_check))
        self.stack.enter_context(mock.patch.object(type(self.run.guest_lock), 'recheck', guest_check))
        self.stack.enter_context(mock.patch.object(type(self.run.guest_lock), 'close', close_guest))
        self.lease.recheck()
        self.run.guest_lock.recheck()
        admission = self.lease.admission
        admission.gid = 1  # Explicit native surrogate's mocked KVM group policy.
        admission.recheck = self.lease.recheck

        def check_permits(directory):
            info = os.fstat(directory)
            fixture.assertEqual((info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)), (0, 0, 0o700))
            fixture.assertEqual((info.st_dev, info.st_ino),
                                (fixture.permits.stat().st_dev, fixture.permits.stat().st_ino))

        def permit_named(fd, directory, run_id):
            info = os.fstat(fd)
            S.ENROLL.regular(info, 0o400)
            named = os.stat(run_id + '.json', dir_fd=directory, follow_symlinks=False)
            fixture.assertEqual(S.ENROLL.identity(info), S.ENROLL.identity(named))

        def publish_permit(value, lease, lock, *, guard, deadline=None):
            guard()
            for key, capability in (('lease', lease), ('guest', lock)):
                fd = capability.recheck()
                info = os.fstat(fd)
                fixture.assertEqual(value['locks'][key], dict(fd=fd, dev=info.st_dev, ino=info.st_ino))
            child = S.PROCESS._identity(value['child']['pid'])
            fixture.assertEqual(child['ppid'], os.getpid())
            fixture.assertEqual(child['start_ticks'], value['child']['start_ticks'])
            fixture.assertFalse((fixture.qmp / 'qmp.sock').exists())  # Child still at fork barrier.
            raw = S.PERMIT.canonical(value)
            directory = os.open(fixture.permits, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
            try:
                with M.STORE.RootDirectory(fixture.base_fd, 'permits', guard=guard) as store:
                    store.publish(value['run_id'] + '.json', raw)
                fd = os.open(value['run_id'] + '.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                             dir_fd=directory)
            except BaseException:
                os.close(directory)
                raise
            return S.ADMISSION.Permit(admission, value['run_id'], directory, fd, raw)

        admission._check_permits_directory = check_permits
        admission._permit_named = permit_named
        admission.publish_permit = publish_permit
        self.base_fd = os.open(self.base, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, self.base_fd)
        self.addCleanup(self.cleanup_native)

    def cleanup_native(self):
        if self.supervisor is not None and not self.supervisor._closed:
            try:
                self.supervisor.close()
            except S.SupervisionError:
                pass
        self.run.close()

    def stop_actual(self, *, silent=False, exit_status=0):
        self.supervisor = NativeSupervisor(self.run, self.qmp, self.disk,
                                           silent=silent, exit_status=exit_status)
        self.supervisor.start()
        outcome = self.supervisor.stop()
        self.assertIs(type(outcome), ACTUAL_STOPPED_RUN)
        self.assertIs(type(outcome.run), L.ManagedRun)
        self.assertEqual(outcome.recheck()['raw_wait_status'], 0)
        self.assertTrue(outcome.channel.clean_shutdown)
        self.assertTrue(outcome.console.eof)
        self.assertFalse(outcome.console.thread.is_alive())
        self.assertTrue(os.WIFEXITED(self.supervisor._raw_status))
        self.assertEqual(os.WEXITSTATUS(self.supervisor._raw_status), 0)
        return outcome

    def test_actual_producers_checkpoint_verify_and_admit_next_run(self):
        for silent in (False, True):
            with self.subTest(silent=silent):
                # Fresh fixture per run preserves real immutable run UUID exclusion.
                case = NativeProducerComposition()
                case.setUp()
                try:
                    outcome = case.stop_actual(silent=silent)
                    result = M.checkpoint(outcome)
                    self.assertTrue(result['success'])
                    self.assertTrue(result['supervisor_cleanup_required'])
                    envelope_raw = (case.runs / (F.RUN + '.outcome.json')).read_bytes()
                    envelope = M.RECORDS.validate_outcome(envelope_raw)
                    self.assertEqual(envelope['shutdown'], outcome.evidence)
                    self.assertEqual(envelope['permit'], M.reference(outcome._permit_data, embedded=True))
                    commands = [item['message']['execute'] for item in envelope['shutdown']['qmp']
                                if item['direction'] == 'send']
                    self.assertEqual(commands, ['qmp_capabilities', 'cont', 'system_powerdown'])
                    if silent:
                        self.assertEqual(envelope['console'], M.reference(b''))
                        self.assertEqual((case.runs / (F.RUN + '.console')).read_bytes(), b'')
                    case.supervisor.close()
                    self.assertEqual(case.supervisor.receipt['errors'], [])
                    case.run.close()
                    self.assertTrue(M.authenticate_ready_cohort(case.controller, 'one')['success'])
                    pin = M.digest(case.manifest_path.read_bytes())
                    later = case.controller.admit_run('one', pin, '55555555-5555-4555-8555-555555555555')
                    try:
                        self.assertIs(type(later), L.ManagedRun)
                        self.assertEqual(later.recheck()['status'], 'admitted')
                    finally:
                        later.close()
                finally:
                    case.doCleanups()

    def test_actual_nonzero_child_exit_never_creates_checkpoint_outcome(self):
        self.supervisor = NativeSupervisor(self.run, self.qmp, self.disk, exit_status=7)
        self.supervisor.start()
        with self.assertRaises(S.SupervisionError) as caught:
            self.supervisor.stop()
        self.assertEqual(caught.exception.receipt['raw_wait_status'], 7 << 8)
        self.assertIsNone(self.supervisor._outcome)
        self.assertEqual(M.HEAD.validate_head(self.head_path.read_bytes())['status'], 'quarantined')
        self.assertFalse((self.runs / (F.RUN + '.outcome.json')).exists())

    def test_outer_supervisor_close_receipt_errors_require_checked_cleanup(self):
        outcome = self.stop_actual(silent=True)
        result = M.checkpoint(outcome)
        self.assertTrue(result['supervisor_cleanup_required'])
        # Supervisor.close() deliberately records owned close errors and returns.
        original_close = self.supervisor._close_fd
        fired = []
        def close_after_effect(fd):
            original_close(fd)
            if not fired:
                fired.append(True)
                raise OSError('native owned close acknowledgement lost')
        with mock.patch.object(self.supervisor, '_close_fd', close_after_effect):
            self.supervisor.close()
        self.assertTrue(fired)
        self.assertTrue(self.supervisor.receipt['errors'])
        self.run.close()
        # Durable publication remains coherent; cleanup uncertainty requires this
        # explicit fresh verification, never fabricated shutdown or republishing.
        self.assertTrue(M.authenticate_ready_cohort(self.controller, 'one')['success'])


if __name__ == '__main__':
    unittest.main()
