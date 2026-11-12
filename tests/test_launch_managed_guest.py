"""Managed child handoff fixtures; no real NVMe, KVM or supervisor authority.

Protected-file fixtures run as root. Native placement/platform/permit validation
are substituted explicitly; actual inherited-lock and peer proofs are separate.
"""
from contextlib import ExitStack
import grp
import hashlib
import importlib.util
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

BASE = Path(__file__).resolve().parents[1]
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, BASE / path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value

M = load('managed_launch_test', 'scripts/launch-managed-guest.py')
F = load('managed_launch_test_permit', 'tests/test_guest_run_permit.py')
RUN = F.permit()['run_id']


class HandoffTests(unittest.TestCase):
    def test_invalid_run_refuses_before_runtime_access(self):
        for value in ('other', '0' * 36, True, '../' + RUN):
            with self.subTest(value=value), self.assertRaises(ValueError):
                M.ManagedLaunch(value)

    def test_cli_has_only_run_selector_and_never_exposes_artifact_or_dry_run(self):
        result = subprocess.run([sys.executable, '-I', '-B', str(BASE / 'scripts/launch-managed-guest.py'),
                                 '--help'], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--run-id', result.stdout)
        self.assertNotIn('--dry-run', result.stdout)
        self.assertNotIn('--kernel', result.stdout)
        self.assertNotIn('--disk', result.stdout)

    def test_close_detaches_all_inputs_before_any_reported_close_failure(self):
        guest = M.Inputs(SimpleNamespace(host_tcp_port=None, guest_tcp_port=None))
        guest.fds = {'kernel': 100, 'initrd': 101, 'disk': 102}
        with mock.patch.object(M.os, 'close', side_effect=[OSError('close acted'), None, None]) as close:
            with self.assertRaises(OSError):
                guest.close()
            self.assertEqual(guest.fds, {})
            guest.close()
            self.assertEqual([call.args[0] for call in close.call_args_list], [100, 101, 102])

    def test_inherit_exact_inputs_and_two_borrowed_locks(self):
        fds = [os.open('/dev/null', os.O_RDONLY | os.O_CLOEXEC) for _ in range(6)]
        try:
            guest = SimpleNamespace(fds=dict(zip(('kernel', 'initrd', 'disk'), fds[:3])))
            locks = SimpleNamespace(fdtuple=tuple(fds[3:5]))
            os.set_inheritable(fds[5], True)
            with mock.patch.object(M.os, 'listdir', return_value=[str(fd) for fd in fds]):
                M.inherit(guest, locks)
            self.assertTrue(all(os.get_inheritable(fd) for fd in fds[:5]))
            self.assertFalse(os.get_inheritable(fds[5]))
            with self.assertRaises(ValueError):
                M.inherit(guest, SimpleNamespace(fdtuple=(fds[0], fds[4])))
        finally:
            for fd in fds:
                os.close(fd)

    def test_paused_argv_and_descriptor_exec_are_fixed_and_one_shot(self):
        value = M.ManagedLaunch(RUN)
        value.guest = SimpleNamespace(argv=lambda **kw: ['/old/qemu', '-accel', 'kvm'])
        value.qmp_path = '/run/a733-admission/qmp/' + RUN + '/qmp.sock'
        self.assertEqual(value.argv(), [M.QEMU_PATH, '-accel', 'kvm', '-S', '-qmp',
                                       'unix:' + value.qmp_path + ',server=on,wait=off'])
        value.qemu = SimpleNamespace(fd=123)
        value.locks = SimpleNamespace(fdtuple=(10, 11))
        value.recheck = mock.Mock()
        with mock.patch.object(M, 'inherit') as inherit, mock.patch.object(M.os, 'execve') as execute:
            with self.assertRaisesRegex(RuntimeError, 'unexpectedly returned'):
                value.execute()
            execute.assert_called_once_with(123, value.argv(),
                                            {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'TZ': 'UTC'})
            inherit.assert_called_once_with(value.guest, value.locks)
            with self.assertRaisesRegex(ValueError, 'already attempted'):
                value.execute()
            self.assertEqual(execute.call_count, 1)

    def test_runtime_memory_reserves_one_gib(self):
        config = F.permit()['config']
        for kib, valid in (((config['memory_max_mib'] + 1024) * 1024, True),
                           ((config['memory_max_mib'] + 1024) * 1024 - 1, False)):
            with mock.patch.object(M.CONTEXT, 'read_system', return_value=f'MemAvailable: {kib} kB\n'.encode()), \
                    mock.patch.object(M.os, 'cpu_count', return_value=8):
                if valid:
                    M.memory_budget(config)
                else:
                    with self.assertRaises(ValueError):
                        M.memory_budget(config)


@unittest.skipUnless(os.geteuid() == 0, 'root-owned retained-file fixture')
class ProtectedInputs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='managed-launch-', dir='/run')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.value = F.permit()
        self.paths = {}
        for key, name, data in (('kernel', 'Image', b'kernel'), ('initrd', 'initramfs.cpio.gz', b'initrd'),
                                ('disk', 'disk.raw', b'A' * 512)):
            p = self.root / name
            p.write_bytes(data)
            os.chown(p, 60002 if key == 'disk' else 0, 60002 if key == 'disk' else 0)
            p.chmod(0o600 if key == 'disk' else 0o444)
            self.paths[key] = str(p)
            self.value['config'][key] = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
        self.payload = self.root / 'payload.json'
        self.payload.write_bytes(b'{"fixture":true}\n')
        self.payload.chmod(0o444)
        self.value['host_payload_sha256'] = hashlib.sha256(self.payload.read_bytes()).hexdigest()
        self.binary = self.root / 'qemu-fixture'
        self.binary.write_bytes(b'\x7fELF' + b'not an executable fixture')
        self.binary.chmod(0o555)
        self.value['qemu'] = {'path': str(self.binary), 'bytes': self.binary.stat().st_size,
                              'sha256': hashlib.sha256(self.binary.read_bytes()).hexdigest()}
        self.admission_root = self.root / 'admission'
        self.admission_root.mkdir(mode=0o710)
        os.chown(self.admission_root, 0, 108)
        qmp = self.admission_root / 'qmp'
        qmp.mkdir(mode=0o710)
        os.chown(qmp, 0, 108)
        self.qmp = qmp / RUN
        self.qmp.mkdir(mode=0o730)
        self.qmp.chmod(0o730)
        os.chown(self.qmp, 0, 60002)
        self.events = []
        self.permit = SimpleNamespace(value=self.value, close=mock.Mock(), recheck=mock.Mock())
        self.locks = SimpleNamespace(fdtuple=(100, 101), recheck=mock.Mock())
        admission_fd = os.open(self.admission_root, os.O_PATH | os.O_DIRECTORY)
        self.admission = SimpleNamespace(directory=admission_fd, open_permit=mock.Mock(return_value=self.permit),
                                         validate_inherited=mock.Mock(return_value=self.locks),
                                         close=lambda: os.close(admission_fd))
        context = {'namespace': 'nvme0n1', 'root_source': '/dev/nvme0n1p2',
                   'guest_source': '/dev/nvme0n1p5'}
        def storage(*args, **kwargs):
            self.events.append('placement')
            return context
        def read_system(path, limit):
            if path.endswith('/device/serial'):
                return b'NVME-123\n'
            if path.endswith('/boot_id'):
                return (self.value['boot_id'] + '\n').encode()
            raise AssertionError(path)
        patches = [mock.patch.object(M, 'child_platform', return_value=108),
                   mock.patch.object(M, 'memory_budget'),
                   mock.patch.object(M, 'PAYLOAD', str(self.payload)),
                   mock.patch.object(M, 'QEMU_PATH', str(self.binary)),
                   mock.patch.object(M.CONFIG, 'paths', return_value=self.paths),
                   mock.patch.object(M.ManagedLaunch, '_input_directories'),
                   mock.patch.object(M.CONTEXT, 'read_system', side_effect=read_system),
                   mock.patch.object(M.ADMISSION.Admission, 'open_runtime', return_value=self.admission),
                   mock.patch.object(M.LAUNCH.STORAGE, 'verify_guest_storage', side_effect=storage)]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.prepared = M.ManagedLaunch(RUN)
        self.addCleanup(self.prepared.close)

    def test_prepare_binds_all_artifacts_and_starts_only_paused(self):
        with self.prepared as launch:
            self.assertEqual(launch.guest.inputs['disk']['sha256'], self.value['config']['disk']['sha256'])
            self.assertIn('-S', launch.argv())
            self.assertEqual(launch.qemu.info.st_size, self.binary.stat().st_size)
            self.assertGreaterEqual(len(self.events), 2)
        self.permit.close.assert_called_once()
        self.locks.recheck.assert_called()

    def test_disk_owner_mismatch_refuses_before_placement_or_payload_reads(self):
        os.chown(self.paths['disk'], 0, 0)
        with self.assertRaisesRegex(ValueError, 'metadata differs'):
            self.prepared.__enter__()
        self.assertEqual(self.events, [])
        self.permit.close.assert_called_once()

    def test_storage_refusal_precedes_any_hash_read(self):
        with mock.patch.object(M.LAUNCH.STORAGE, 'verify_guest_storage', side_effect=ValueError('bad placement')), \
                mock.patch.object(M, 'hash_root_file', side_effect=AssertionError('payload read before placement')):
            with self.assertRaisesRegex(ValueError, 'bad placement'):
                self.prepared.__enter__()

    def test_named_input_replacement_is_refused(self):
        with self.prepared:
            path = Path(self.paths['kernel'])
            other = path.with_name('other')
            other.write_bytes(path.read_bytes())
            other.chmod(0o444)
            other.replace(path)
            with self.assertRaisesRegex(ValueError, 'path replaced|Input changed'):
                self.prepared.recheck()

    def test_qmp_destination_must_be_absent_even_if_symlink(self):
        (self.qmp / 'qmp.sock').symlink_to(self.root / 'missing')
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.prepared.__enter__()

    def test_payload_hash_and_qemu_bytes_must_match_permit(self):
        self.value['host_payload_sha256'] = 'f' * 64
        with self.assertRaisesRegex(ValueError, 'SHA-256 differs'):
            self.prepared.__enter__()


if __name__ == '__main__':
    unittest.main()
