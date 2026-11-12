"""Host-only hostile fixtures; mocked root/platform authority never establishes KVM."""
import copy
from contextlib import ExitStack
import subprocess
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import stat
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('fixture_acceptance', Path(__file__).resolve().parents[1] / 'scripts/managed-guest-fixture.py')
F = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(F)


class FixtureAcceptance(unittest.TestCase):
    def test_debugfs_cleanup_attempts_both_pipe_closes_after_wait_or_kill_error(self):
        for failing in ('kill', 'wait', 'stdout'):
            process = mock.Mock()
            process.poll.return_value = None
            if failing == 'kill':
                process.kill.side_effect = PermissionError('kill failed')
            elif failing == 'wait':
                process.wait.side_effect = subprocess.TimeoutExpired('debugfs', 1)
            else:
                process.stdout.close.side_effect = OSError('close acted then failed')
            with self.subTest(failing=failing), mock.patch.object(F, 'superblock'), \
                    mock.patch.object(F.subprocess, 'Popen', return_value=process), \
                    mock.patch.object(F.select, 'select', side_effect=TimeoutError('capture failed')):
                with self.assertRaises(Exception):
                    F.debugfs(99, '/counter', 60002, 60002)
            process.kill.assert_called_once()
            process.wait.assert_called_once_with(timeout=1)
            process.stdout.close.assert_called_once()
            process.stderr.close.assert_called_once()

    def test_reviewed_lock_and_mutations(self):
        data = Path(F.LOCK_PATH).read_bytes()
        self.assertEqual(F.validate_lock(data), F.LOCK)
        for key in F.LOCK:
            changed = copy.deepcopy(F.LOCK)
            changed[key] = None
            with self.subTest(key=key), self.assertRaises(ValueError):
                F.validate_lock(F.canonical(changed))
        for data in (b'{"schema":1,"schema":1}', b' '*4097, b'{}', b'{"schema":true}'):
            with self.assertRaises(ValueError):
                F.validate_lock(data)

    def test_native_private_profile_readonly_and_replacement(self):
        # Explicit platform/root-authority mocks only: real private files and
        # HeldPath descriptor/no-follow/identity checks still execute.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'profile.json'
            path.write_bytes(F.canonical(F.LOCK))
            path.chmod(0o444)
            with mock.patch.object(F.ENROLL, 'trusted_directory'), \
                 mock.patch.object(F.ENROLL, 'regular'), \
                 mock.patch.object(F.os, 'getuid', return_value=0), \
                 mock.patch.object(F.os, 'geteuid', return_value=0), \
                 mock.patch.object(F, 'LOCK_PATH', path):
                for mode in (0o644, 0o444):
                    path.chmod(mode)
                    owner = object.__new__(F.SUPERVISOR.Supervisor)
                    owner._owner = lambda: None
                    owner._started = False
                    owner._stack = ExitStack()
                    config = dict(name='managedtest', uid=1234, gid=1234,
                                  kernel=F.KERNEL, initrd=F.INITRD,
                                  disk={'bytes': F.LOCK['disk_bytes']},
                                  network=dict(mode='isolated', guest_tcp_port=8080, host_tcp_port=2222))
                    owner.run = SimpleNamespace(config=config,
                        _record={'fixture_profile': F.PROFILE, 'manifest_sha256':'0'*64},
                        recheck=lambda: {'status':'admitted'})
                    try:
                        if mode == 0o644:
                            with self.assertRaisesRegex(ValueError, 'read-only'):
                                F.Fixture(owner)
                        else:
                            with mock.patch.object(F.POLICY, 'Inspection', side_effect=RuntimeError('past lock')):
                                with self.assertRaisesRegex(RuntimeError, 'past lock'):
                                    F.Fixture(owner)
                            held = owner._managed_fixture._lock
                            replacement = path.with_suffix('.new')
                            replacement.write_bytes(path.read_bytes())
                            replacement.chmod(0o444)
                            replacement.replace(path)
                            with self.assertRaises(ValueError):
                                held.recheck()
                        with self.assertRaisesRegex(ValueError, 'already attached'):
                            F.Fixture(owner)
                    finally:
                        owner._stack.close()

    def test_native_debugfs_capture_bounds_and_fixed_privilege_command(self):
        real_popen = subprocess.Popen
        for length in (2, 4097):
            observed = []
            def surrogate(args, **kwargs):
                observed.append((args, kwargs.copy()))
                # Only the root credential transition/parser is mocked here.
                # Native child pipes, output bounds, wait and cleanup execute.
                return real_popen(['/usr/bin/python3', '-c',
                    'import os; os.write(1, b"x" * '+str(length)+')'], **kwargs)
            with tempfile.TemporaryFile() as disk, \
                 mock.patch.object(F, 'superblock'), \
                 mock.patch.object(F.subprocess, 'Popen', side_effect=surrogate):
                if length == 2:
                    self.assertEqual(F.debugfs(disk.fileno(), '/counter', 1234, 1234), b'xx')
                else:
                    with self.assertRaisesRegex(ValueError, 'limit'):
                        F.debugfs(disk.fileno(), '/counter', 1234, 1234)
                args, kwargs = observed[0]
                self.assertEqual(args[0], '/usr/bin/setpriv')
                self.assertIn('--clear-groups', args)
                self.assertIn('--no-new-privs', args)
                self.assertIn('--bounding-set=-all', args)
                self.assertEqual(args[-4:], ['/usr/sbin/debugfs', '-R', 'cat /counter',
                    '/proc/self/fd/'+str(disk.fileno())])
                self.assertNotIn('-w', args)
                self.assertEqual(kwargs['pass_fds'], (disk.fileno(),))

    def test_counter_canonical_bounds(self):
        for data in (b'0\n', b'1\n', b'99999999\n'):
            self.assertEqual(F.counter(data), int(data))
        for data in (b'0', b'00\n', b'01\n', b'+1\n', b'-1\n', b' 1\n',
                     b'1\r\n', b'100000000\n', b'1\n\n', b'1\x00\n'):
            with self.subTest(data=data), self.assertRaises(ValueError):
                F.counter(data)

    def test_real_sparse_file_fixed_superblock(self):
        with tempfile.TemporaryFile() as disk:
            disk.truncate(F.LOCK['disk_bytes'])
            for offset, data in F.FIELDS:
                os.pwrite(disk.fileno(), data, offset)
            F.superblock(disk.fileno())
            for offset, data in F.FIELDS:
                os.pwrite(disk.fileno(), b'\x00'*len(data), offset)
                with self.assertRaises(ValueError):
                    F.superblock(disk.fileno())
                os.pwrite(disk.fileno(), data, offset)
            disk.truncate(1024)
            with self.assertRaises(ValueError):
                F.superblock(disk.fileno())

    def serve(self, response, expected=7, confirm=False):
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        port = listener.getsockname()[1]
        requests = []
        errors = []
        def worker():
            try:
                listener.settimeout(2)
                with listener.accept()[0] as connection:
                    connection.settimeout(2)
                    requests.append(connection.recv(4096))
                    connection.sendall(response)
            except OSError as error:
                errors.append(error)
            finally:
                listener.close()
        thread = threading.Thread(target=worker)
        thread.start()
        attempts = []
        try:
            result = F.http(port, expected, confirm=confirm,
                            before_send=lambda: attempts.append(True), timeout=2)
            return result, requests, attempts
        finally:
            thread.join(3)
            self.assertFalse(thread.is_alive())

    def test_native_http_exact_and_confirmation(self):
        body = b'a733-kvm-guest-network-v1 counter=7\n'
        result, requests, attempts = self.serve(b'HTTP/1.0 200 OK\r\nContent-Length: '+str(len(body)).encode()+b'\r\n\r\n'+body, confirm=True)
        self.assertEqual(result['status'], 200)
        self.assertTrue(requests[0].startswith(b'GET /cgi-bin/probe?confirm=7 HTTP/1.0\r\n'))
        self.assertEqual(attempts, [True])

    def test_native_http_rejects_redirect_duplicates_and_bounds(self):
        body = b'a733-kvm-guest-network-v1 counter=7\n'
        bad = [b'HTTP/1.0 302 Found\r\n\r\n'+body,
               b'HTTP/1.0 200 OK\r\nLocation: /evil\r\n\r\n'+body,
               b'HTTP/1.0 200 OK\r\nX: a\r\nx: b\r\n\r\n'+body,
               b'HTTP/1.0 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n'+body,
               b'HTTP/1.0 200 OK\r\nContent-Length: 2\r\n\r\n'+body,
               b'HTTP/1.0 200 OK\r\n\r\n'+body+b'extra',
               b'HTTP/1.0 200 OK\r\nX: '+b'a'*4096+b'\r\n\r\n'+body]
        for response in bad:
            with self.subTest(response=response[:60]), self.assertRaises(ValueError):
                self.serve(response)

    def console_fixture(self, data, events=()):
        obj = object.__new__(F.Fixture)
        obj.old, obj.expected = 6, 7
        obj.supervisor = SimpleNamespace(console_snapshot=lambda: data,
                                        channel=SimpleNamespace(transcript=events, shutdown_message=None))
        return obj

    def test_exact_console_lines_and_duplicates(self):
        cpu = ('PASS cpu sha256='+F.CPU+'\n').encode()
        disk = b'PASS disk previous=6 counter=7\n'
        data, found = self.console_fixture(cpu+disk)._console()
        self.assertEqual(found, {b'PASS cpu', b'PASS disk'})
        for bad in (cpu+cpu, disk+disk, cpu.replace(b'PASS cpu ', b'PASS cpu fake '),
                    b'FAIL disk\n', b'Kernel panic - not syncing\n',
                    b'PASS disk previous=5 counter=7\n', b'PASS managed-power-key-sync\n'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.console_fixture(bad)._console()
        for name in ('RESET', 'GUEST_PANICKED'):
            with self.assertRaises(ValueError):
                self.console_fixture(cpu, ({'direction':'receive', 'message':{'event':name}},))._console()

    def console_lines(self):
        return [('PASS cpu sha256='+F.CPU+'\n').encode(),
                b'PASS disk previous=6 counter=7\n',
                b'READY network tcp=8080 counter=7; host must verify and confirm HTTP response\n',
                b'PASS network loopback-http counter=7\n',
                b'PASS fixture counter=7; READY managed-power-key-v1\n',
                b'PASS managed-power-key-sync\n']

    def test_all_byte_splits_preserve_live_partial_evidence(self):
        expected = {b'PASS cpu', b'PASS disk', b'READY network', b'PASS network', b'PASS fixture'}
        data = b''.join(self.console_lines()[:-1])
        for split in range(len(data)+1):
            with self.subTest(split=split):
                _, found = self.console_fixture(data[:split])._console()
                if split == len(data):
                    self.assertEqual(found, expected)
                elif data[:split].rpartition(b'\n')[2]:
                    self.assertNotEqual(found, expected)
        # Incomplete unstructured kernel output does not require a newline.
        _, found = self.console_fixture(b''.join(self.console_lines())+b'Power down')._console(stopped=True)
        self.assertEqual(found, expected | {b'PASS managed-power-key-sync'})

    def test_stopped_unterminated_structured_tails_and_duplicates_refuse(self):
        complete = b''.join(self.console_lines())
        tails = [line.rstrip(b'\n') for line in self.console_lines()]
        tails += [b'FAIL', b'FAIL disk', b'P', b'PASS c', b'READY net', b'FA']
        for tail in tails:
            with self.subTest(tail=tail), self.assertRaises(ValueError):
                self.console_fixture(complete+tail)._console(stopped=True)
        for index in (0, 1, 2):
            prefix = b''.join(self.console_lines()[:3])
            with self.assertRaises(ValueError):
                self.console_fixture(prefix+self.console_lines()[index].rstrip(b'\n'))._console()
        with self.assertRaisesRegex(ValueError, 'failure'):
            self.console_fixture(complete+b'FAIL disk')._console()

    def test_active_verification_waits_for_every_byte_then_succeeds(self):
        fixture = self.console_fixture(b'')
        fixture._owner = lambda: None
        fixture._attempted = fixture._verified = False
        fixture.evidence = {}
        fixture.config = {'network': {'host_tcp_port':2222}}
        fixture.supervisor.permit = SimpleNamespace(data=b'host-test-permit')
        initial = b''.join(self.console_lines()[:3])
        all_active = b''.join(self.console_lines()[:-1])
        stream = {'data': b'', 'target': initial}
        fixture.supervisor.console_snapshot = lambda: stream['data']
        def live():
            stream['data'] = stream['target'][:len(stream['data'])+1]
            return {'host_only_mock': True}
        fixture._live = live
        requests = []
        def http(port, expected, **kwargs):
            requests.append(kwargs['confirm'])
            if kwargs['confirm']:
                kwargs['before_send']()
                stream['target'] = all_active
            return {'status':200}
        with mock.patch.object(F, 'http', side_effect=http), mock.patch.object(F.time, 'sleep'):
            fixture.verify_and_confirm(timeout=3)
        self.assertTrue(fixture._verified)
        self.assertEqual(stream['data'], all_active)
        self.assertEqual(requests, [False, True, False])

    def test_confirmation_retry_is_forbidden(self):
        fixture = object.__new__(F.Fixture)
        fixture._attempted = False
        fixture._owner = lambda: None
        fixture._mark_attempted()
        with self.assertRaises(ValueError):
            fixture._mark_attempted()
        fixture._verified = False
        with self.assertRaises(ValueError):
            fixture.verify_and_confirm()

    def test_preconfirmation_failure_also_consumes_validator(self):
        fixture = object.__new__(F.Fixture)
        fixture._owner = lambda: None
        fixture._attempted = fixture._verified = False
        fixture._live = mock.Mock(side_effect=ValueError('hostile live identity'))
        with self.assertRaisesRegex(ValueError, 'identity'):
            fixture.verify_and_confirm()
        with self.assertRaisesRegex(ValueError, 'one-shot'):
            fixture.verify_and_confirm()
        fixture._live.assert_called_once()

    def test_failed_send_consumes_attempt(self):
        attempts = []
        connection = mock.MagicMock()
        connection.__enter__.return_value = connection
        connection.sendall.side_effect = OSError('uncertain send')
        with mock.patch.object(F.socket, 'socket', return_value=connection):
            with self.assertRaises(OSError):
                F.http(2222, 7, confirm=True, before_send=lambda: attempts.append(True))
        self.assertEqual(attempts, [True])
        connection.connect.assert_called_once()
        connection.sendall.assert_called_once()

    def run_census(self, targets, address='0100007F:08AE', inode='99'):
        entries = [SimpleNamespace(name=str(i), path=str(i)) for i in range(len(targets))]
        line = (' 0: '+address+' 00000000:0000 0A 0:0 0:0 0 0 0 '+inode+'\n').encode()
        with mock.patch.object(F.os, 'scandir', return_value=entries), \
             mock.patch.object(F.os, 'readlink', side_effect=targets), \
             mock.patch.object(F.os, 'stat', return_value=SimpleNamespace(st_mode=stat.S_IFCHR, st_rdev=os.makedev(10,232))), \
             mock.patch('builtins.open', return_value=io.BytesIO(b'header\n'+line)):
            return F.census(123, 2, 2222)

    def test_proc_exact_anonymous_descriptors_and_listener(self):
        targets = ['anon_inode:kvm-vm', 'anon_inode:kvm-vcpu:0', 'anon_inode:kvm-vcpu:1',
                   'anon_inode:kvm-arm-vgic-v3', 'socket:[99]', '/dev/kvm']
        self.assertEqual(self.run_census(targets)['listen_inode'], '99')
        for i in (0, 1, 2, 3, 5):
            bad = targets.copy()
            bad[i] = '/tmp/'+bad[i]
            with self.subTest(i=i), self.assertRaises(ValueError):
                self.run_census(bad)
        for address, inode in [('00000000:08AE','99'), ('0100007F:08AF','99'), ('0100007F:08AE','98')]:
            with self.subTest(address=address, inode=inode), self.assertRaises(ValueError):
                self.run_census(targets, address, inode)

    def test_census_exact_vcpu_set_rejects_missing_extra_and_duplicates(self):
        base = ['anon_inode:kvm-vm', 'anon_inode:kvm-arm-vgic-v3', 'socket:[99]', '/dev/kvm']
        for indices in ((0,), (0, 2), (0, 1, 2), (0, 1, 3), (0, 1, 1), (0, 0)):
            with self.subTest(indices=indices), self.assertRaises(ValueError):
                self.run_census(base+['anon_inode:kvm-vcpu:'+str(i) for i in indices])
        for malformed in ('00', '-1', '01'):
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                self.run_census(base+['anon_inode:kvm-vcpu:0', 'anon_inode:kvm-vcpu:1',
                                      'anon_inode:kvm-vcpu:'+malformed])
        self.assertEqual(len(self.run_census(base+['anon_inode:kvm-vcpu:0', 'anon_inode:kvm-vcpu:1'])['kvm_fds']), 5)

    def test_actual_capabilities_required(self):
        with self.assertRaises(ValueError):
            F.Fixture(SimpleNamespace())
        fixture = object.__new__(F.Fixture)
        fixture._owner = lambda: None
        fixture._verified, fixture._attested = True, False
        with self.assertRaises(ValueError):
            fixture.attest_stopped(SimpleNamespace(), SimpleNamespace())

    def test_stopped_persisted_counter_mismatch(self):
        fixture = object.__new__(F.Fixture)
        fixture._owner = lambda: None
        fixture._verified, fixture._attested = True, False
        fixture._inspection = lambda inspection: None
        fixture._disk_inode = (1, 2)
        fixture._console = lambda **kw: (b'console', {b'PASS cpu', b'PASS disk', b'READY network',
            b'PASS network', b'PASS fixture', b'PASS managed-power-key-sync'})
        fixture._counter = lambda inspection: 8
        fixture.expected = 7
        owner = SimpleNamespace()
        outcome = object.__new__(F.SUPERVISOR.StoppedRun)
        outcome._supervisor = owner
        outcome.recheck = lambda: {'profile':'test-only-root-authority-mock'}
        owner._outcome = outcome
        fixture.supervisor = owner
        inspection = SimpleNamespace(disk_fd=0)
        with mock.patch.object(F.ENROLL, 'inode', return_value=(1, 2)), \
             mock.patch.object(F.os, 'fstat', return_value=None), self.assertRaisesRegex(ValueError, 'persisted'):
            fixture.attest_stopped(outcome, inspection)

    def test_stopped_proof_bound_refuses_without_truncation(self):
        self.assertEqual(F.EVIDENCE_LIMIT, 256*1024)
        fixture = object.__new__(F.Fixture)
        fixture._owner = lambda: None
        fixture._verified, fixture._attested = True, False
        fixture._inspection = lambda inspection: None
        fixture._disk_inode = (1, 2)
        fixture._console = lambda **kw: (b'console', {b'PASS cpu', b'PASS disk', b'READY network',
            b'PASS network', b'PASS fixture', b'PASS managed-power-key-sync'})
        fixture._counter = lambda inspection: 7
        fixture.expected = 7
        fixture.evidence = {'test_only_padding': 'x'*F.EVIDENCE_LIMIT}
        owner = SimpleNamespace()
        outcome = object.__new__(F.SUPERVISOR.StoppedRun)
        outcome._supervisor = owner
        outcome.recheck = lambda: {'profile':'test-only-root-authority-mock'}
        owner._outcome = outcome
        fixture.supervisor = owner
        inspection = SimpleNamespace(disk_fd=0, disk_record={'sha256':'0'*64, 'bytes':134217728})
        with mock.patch.object(F.ENROLL, 'inode', return_value=(1, 2)), \
             mock.patch.object(F.os, 'fstat', return_value=None):
            with self.assertRaisesRegex(ValueError, 'limit'):
                fixture.attest_stopped(outcome, inspection)
            self.assertFalse(fixture._attested)
            self.assertEqual(len(fixture.evidence['test_only_padding']), F.EVIDENCE_LIMIT)
            fixture.evidence.pop('test_only_padding')
            proof = fixture.attest_stopped(outcome, inspection)
            self.assertLess(len(proof), F.EVIDENCE_LIMIT)
            self.assertEqual(json.loads(proof)['stopped_disk'], inspection.disk_record)


if __name__ == '__main__':
    unittest.main()
