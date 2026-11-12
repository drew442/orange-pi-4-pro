"""Native Linux witness regression tests; no A733/hardware acceptance claims."""
import hashlib
import importlib.util
import os
from pathlib import Path
import signal
import socket
import stat
import struct
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('guest_process_evidence', ROOT / 'scripts/guest-process-evidence.py')
P = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(P)
FIXTURE = importlib.util.spec_from_file_location('permit_fixture', ROOT / 'tests/test_guest_run_permit.py')
F = importlib.util.module_from_spec(FIXTURE)
FIXTURE.loader.exec_module(F)


def status():
    return (b'PPid: 100\nUid: 60002 60002 60002 60002\nGid: 60002 60002 60002 60002\n'
            b'Groups: 108\nCapInh: 0\nCapPrm: 0\nCapEff: 0\nCapBnd: 0\nCapAmb: 0\n'
            b'NoNewPrivs: 1\n')


def record(pid, ticks):
    value = F.permit()
    value['parent'] = {'pid': os.getpid(), 'start_ticks': P._identity(os.getpid())['start_ticks']}
    value['child'] = {'pid': pid, 'start_ticks': ticks}
    return value


class ParserTests(unittest.TestCase):
    def test_stat_arbitrary_comm(self):
        for comm in (b'qemu', b'a (b) c)\nmore (x)', b') '):
            fields = [b'S', b'11'] + [b'0'] * 17 + [b'456'] + [b'0'] * 8
            value = P.parse_stat(b'12 (' + comm + b') ' + b' '.join(fields) + b'\n')
            self.assertEqual(value, {'pid': 12, 'ppid': 11, 'state': b'S', 'start_ticks': 456})

    def test_bad_stat(self):
        for data in (True, b'', b'12 (x) S 1', b'12 (x no end', b'x (x) S ' + b'0 ' * 30,
                     b'1 (x) S ' + b'0 ' * 18 + b'true', b'a' * (P.PROC_LIMIT + 1)):
            with self.subTest(data=repr(data)[:60]), self.assertRaises(P.WitnessError):
                P.parse_stat(data)

    def test_status_duplicates_missing_and_bad_types(self):
        self.assertEqual(P.parse_status(status())[b'Uid'], (60002,) * 4)
        for data in (True, b'', status() + b'Uid: 1 1 1 1\n',
                     status().replace(b'CapAmb: 0\n', b''),
                     status().replace(b'NoNewPrivs: 1', b'NoNewPrivs: true'),
                     status().replace(b'Uid: 60002 60002 60002 60002', b'Uid: 60002'),
                     status().replace(b'CapBnd: 0', b'CapBnd: true')):
            with self.subTest(data=repr(data)[:60]), self.assertRaises(P.WitnessError):
                P.parse_status(data)

    def test_proc_path_refusal(self):
        for pid in (True, '123/../../self', 0, -1):
            with self.assertRaises(P.WitnessError):
                P._read(pid, 'stat')
        with self.assertRaises(P.WitnessError):
            P._read(os.getpid(), '../exe')


@unittest.skipUnless(hasattr(os, 'fork') and Path('/proc/self/stat').exists(), 'Linux procfs required')
class NativeWaitTests(unittest.TestCase):
    def child(self):
        read_fd, write_fd = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.close(write_fd)
            os.read(read_fd, 1)
            os._exit(0)
        os.close(read_fd)
        self.addCleanup(self.cleanup_child, pid, write_fd)
        ticks = P._identity(pid)['start_ticks']
        return pid, write_fd, P.ChildWitness(pid, ticks, record(pid, ticks))

    @staticmethod
    def cleanup_child(pid, fd):
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            waited, _ = os.waitpid(pid, os.WNOHANG)
            if waited == 0:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
        except ProcessLookupError:
            pass
        except ChildProcessError:
            pass

    def test_direct_child_zero_and_terminal(self):
        pid, release, witness = self.child()
        self.assertIsNone(witness.reap())
        self.assertFalse(witness.normal_zero)
        os.write(release, b'x')
        self.assertEqual(witness.reap(nonblocking=False), 0)
        self.assertTrue(witness.normal_zero)
        for action in (witness.reap, witness.validate_child):
            with self.assertRaises(P.WitnessError):
                action()
        connection, other = socket.socketpair()
        with connection, other:
            with self.assertRaises(P.WitnessError):
                witness.verify_qmp_peer(connection)

    def test_signal_is_never_clean(self):
        pid, release, witness = self.child()
        os.kill(pid, signal.SIGTERM)
        raw = witness.reap(nonblocking=False)
        self.assertTrue(os.WIFSIGNALED(raw))
        self.assertEqual(os.WTERMSIG(raw), signal.SIGTERM)
        self.assertFalse(witness.normal_zero)

    def test_nonchild_echild_is_failure(self):
        pid = os.getppid()
        ticks = P._identity(pid)['start_ticks']
        witness = P.ChildWitness(pid, ticks, record(pid, ticks))
        with self.assertRaisesRegex(P.WitnessError, 'wait evidence'):
            witness.reap()
        self.assertFalse(witness.normal_zero)
        self.assertIsNone(witness.raw_status)
        with self.assertRaises(P.WitnessError):
            witness.validate_child()

    def test_actual_ordinary_child_credentials_refused(self):
        pid, release, witness = self.child()
        with self.assertRaisesRegex(P.WitnessError, 'credentials'):
            witness.validate_child()

    def test_constructor_identity_and_boolean_refusal(self):
        pid, release, witness = self.child()
        for field in ('parent', 'child'):
            value = record(pid, witness.start_ticks)
            value[field]['start_ticks'] += 1
            with self.assertRaises(P.WitnessError):
                P.ChildWitness(pid, witness.start_ticks, value)
        with self.assertRaises(P.WitnessError):
            P.ChildWitness(True, 1, record(pid, witness.start_ticks))


class MockedWitnessTests(unittest.TestCase):
    def namespace_stat(self, path):
        pin = self.witness._permit['namespaces'][str(path).rsplit('/', 1)[-1]]
        return SimpleNamespace(st_dev=pin['dev'], st_ino=pin['ino'])
    def setUp(self):
        self.parent = os.getpid()
        self.identity_patch = mock.patch.object(P, '_identity', side_effect=self.identity)
        self.identity_patch.start()
        self.addCleanup(self.identity_patch.stop)
        self.value = F.permit()
        self.value['parent'] = {'pid': self.parent, 'start_ticks': 200}
        self.witness = P.ChildWitness(101, 201, self.value)

    def identity(self, pid):
        return {'pid': pid, 'ppid': self.parent, 'start_ticks': 200 if pid == self.parent else 201,
                'state': b'S'}

    def test_replacement_or_zombie(self):
        original = self.identity
        for changes in ({'ppid': 1}, {'start_ticks': 202}, {'state': b'Z'}):
            def altered(pid):
                value = original(pid)
                if pid == 101:
                    value.update(changes)
                return value
            with mock.patch.object(P, '_identity', side_effect=altered):
                with self.assertRaises(P.WitnessError):
                    self.witness.validate_child()

    def test_credential_variants(self):
        cases = [(b'Uid: 60002 60002 60002 60002', b'Uid: 60002 0 60002 60002'),
                 (b'Gid: 60002 60002 60002 60002', b'Gid: 60002 60002 0 60002'),
                 (b'Groups: 108', b'Groups: 108 60002'),
                 (b'Groups: 108', b'Groups: 108 0'),
                 (b'Groups: 108', b'Groups: 108 108'),
                 (b'NoNewPrivs: 1', b'NoNewPrivs: 0'),
                 (b'PPid: 100', b'PPid: 1')]
        cases.extend((key + b': 0', key + b': 1') for key in
                     (b'CapInh', b'CapPrm', b'CapEff', b'CapBnd', b'CapAmb'))
        valid = status().replace(b'PPid: 100', f'PPid: {self.parent}'.encode())
        with mock.patch.object(P, '_read', return_value=valid), mock.patch.object(
                P.os, 'stat', side_effect=self.namespace_stat):
            self.witness._credentials()
        for old, new in cases:
            data = status().replace(old, new).replace(b'PPid: 100', f'PPid: {self.parent}'.encode())
            with mock.patch.object(P, '_read', return_value=data):
                with self.assertRaises(P.WitnessError):
                    self.witness._credentials()

    def test_namespace_mismatch(self):
        valid = status().replace(b'PPid: 100', f'PPid: {self.parent}'.encode())
        with mock.patch.object(P, '_read', return_value=valid), mock.patch.object(
                P.os, 'stat', side_effect=[mock.Mock(st_dev=1, st_ino=1), mock.Mock(st_dev=1, st_ino=2)]):
            with self.assertRaisesRegex(P.WitnessError, 'namespace'):
                self.witness._credentials()

    def test_matching_parent_and_child_still_require_permit_namespace_pin(self):
        valid = status().replace(b'PPid: 100', f'PPid: {self.parent}'.encode())
        with mock.patch.object(P, '_read', return_value=valid), mock.patch.object(
                P.os, 'stat', return_value=SimpleNamespace(st_dev=123, st_ino=456)):
            with self.assertRaisesRegex(P.WitnessError, 'namespace'):
                self.witness._credentials()

    def test_binary_pin_and_replacement(self):
        with tempfile.TemporaryFile() as binary:
            binary.write(b'pinned-qemu')
            binary.flush()
            self.witness._permit['qemu'].update(bytes=11, sha256=hashlib.sha256(b'pinned-qemu').hexdigest())
            info = os.fstat(binary.fileno())
            # Native temporary file is unlinked; trusted metadata is injected only
            # for this isolated hashing test, never used as deployment evidence.
            metadata = (info.st_dev, info.st_ino, 11, stat.S_IFREG | 0o755, 0, 0, 1, 1, 1)
            def opening(path, flags):
                fd = os.dup(binary.fileno())
                os.lseek(fd, 0, os.SEEK_SET)
                return fd
            with mock.patch.object(self.witness, '_credentials'), mock.patch.object(
                    P.os, 'open', side_effect=opening), mock.patch.object(P, '_metadata', return_value=metadata):
                with mock.patch.object(P.os, 'stat', return_value=info):
                    self.assertEqual(self.witness.validate_child()['qemu_bytes'], 11)
                    self.witness._permit['qemu']['sha256'] = '0' * 64
                    with self.assertRaisesRegex(P.WitnessError, 'digest'):
                        self.witness.validate_child()
                    self.witness._permit['qemu']['sha256'] = hashlib.sha256(b'pinned-qemu').hexdigest()
                    with mock.patch.object(P, '_metadata', side_effect=[metadata, (2,) + metadata[1:]]):
                        with self.assertRaisesRegex(P.WitnessError, 'inode'):
                            self.witness.validate_child()
                    with mock.patch.object(P, '_metadata', side_effect=[metadata] * 3 + [(2,) + metadata[1:]]):
                        with self.assertRaisesRegex(P.WitnessError, 'changed'):
                            self.witness.validate_child()

    def test_unsafe_binary_metadata(self):
        for changes in ({'st_nlink': 2}, {'st_uid': 1}, {'st_mode': stat.S_IFREG | 0o777},
                        {'st_mode': stat.S_IFDIR | 0o755}):
            values = dict(st_nlink=1, st_uid=0, st_mode=stat.S_IFREG | 0o755)
            values.update(changes)
            with self.assertRaises(P.WitnessError):
                P._metadata(mock.Mock(**values))

    def test_live_unconnected_socket_refused(self):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            with self.assertRaises(OSError):
                self.witness.verify_qmp_peer(connection)

    def test_peer_checks(self):
        peer = mock.Mock(spec=socket.socket)
        peer.family = socket.AF_UNIX
        peer.getsockopt.side_effect = [socket.SOCK_STREAM, struct.pack('3i', 101, 60002, 60002)]
        with mock.patch.object(self.witness, 'validate_child', return_value={'ok': True}) as validate:
            self.assertEqual(self.witness.verify_qmp_peer(peer), {'ok': True})
            validate.assert_called_once_with()
        for values in ((102, 60002, 60002), (101, 0, 60002), (101, 60002, 0)):
            peer.getsockopt.side_effect = [socket.SOCK_STREAM, struct.pack('3i', *values)]
            with self.assertRaisesRegex(P.WitnessError, 'peer mismatch'):
                self.witness.verify_qmp_peer(peer)
        peer.getsockopt.side_effect = [socket.SOCK_DGRAM]
        with self.assertRaises(P.WitnessError):
            self.witness.verify_qmp_peer(peer)
        peer.family = socket.AF_INET
        with self.assertRaises(P.WitnessError):
            self.witness.verify_qmp_peer(peer)


if __name__ == '__main__':
    unittest.main()
