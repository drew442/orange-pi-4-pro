"""Software-only actual-root/tmpfs/locks tests; A733/native entry is mocked.

Run with sudo for the root cases. No block device or physical KVM is accessed.
"""
import errno
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

BASE = Path(__file__).resolve().parents[1]
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, BASE / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

W = load('offline_writer', 'scripts/write-known-nvme-boot.py')
A = load('offline_canonical_admission', 'scripts/guest-admission.py')


@unittest.skipUnless(os.getresuid() == (0, 0, 0), 'requires actual root and /run tmpfs')
class OfflineTests(unittest.TestCase):
    def setUp(self):
        self.parent = tempfile.mkdtemp(prefix='a733-offline-test-', dir='/run')
        self.addCleanup(shutil.rmtree, self.parent)
        self.boot = W.read_text('/proc/sys/kernel/random/boot_id')
        self.path = Path(self.parent) / 'a733-admission'
        self.mounts = W.mountinfo()
        self.redirect_run = False
        real_open, real_stat = os.open, os.stat
        def opened(path, flags, mode=0o777, *, dir_fd=None):
            # Redirect only the test's retained /run descriptor to a private
            # root-owned directory on actual /run tmpfs. No metadata is mocked.
            if path == 'run' and dir_fd is not None and self.redirect_run:
                path, dir_fd = self.parent, None
            return real_open(path, flags, mode, dir_fd=dir_fd)
        def stated(path, *, dir_fd=None, follow_symlinks=True):
            if path == '/run':
                path = self.parent
            return real_stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)
        native = os.uname()
        uname = os.uname_result((native.sysname, native.nodename, W.KERNEL_RELEASE,
                                native.version, 'aarch64'))
        for p in (patch.object(W, 'host_context', return_value=self.mounts),
                  patch.object(W, 'namespace_guard'),
                  patch.object(W.os, 'uname', return_value=uname),
                  patch.object(W.os, 'open', side_effect=opened),
                  patch.object(W.os, 'stat', side_effect=stated),
                  patch.object(A, 'RUNTIME_PARENT', self.parent),
                  patch.object(A, 'RUNTIME', str(self.path))):
            p.start()
            self.addCleanup(p.stop)

    def initialize(self):
        a = A.initialize(self.boot, os.getgid(), lambda: 'running')
        self.addCleanup(a.close)
        return a

    def guard(self):
        self.redirect_run = True
        try:
            guard = W.OfflineAdmission.acquire(self.boot)
        finally:
            self.redirect_run = False
        self.addCleanup(guard.close)
        return guard

    def test_absent_interlock_blocks_canonical_initialize_and_pins_absence(self):
        guard = self.guard()
        with self.assertRaises(A.AdmissionError):
            self.initialize()
        self.path.mkdir(mode=0o710)
        with self.assertRaisesRegex(W.Refusal, 'appeared'):
            guard.check()

    def test_closed_dead_owner_borrows_without_opening_or_writing(self):
        a = self.initialize()
        original = (self.path / 'gate.json').read_bytes()
        a.bootstrap_owner.lease.close()
        with self.guard() as guard:
            guard.check_consumers()
            self.assertEqual((self.path / 'gate.json').read_bytes(), original)
            with self.assertRaises(A.AdmissionError):
                a.bootstrap_owner.exclusive()
        self.assertEqual(a._read()['state'], 'closed')

    def test_live_exclusive_and_manual_shared_lease_refuse(self):
        a = self.initialize()
        with self.assertRaises(BlockingIOError):
            self.guard()
        a.bootstrap_owner.lease.close()
        fd = os.open(self.path / 'leases.lock', os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                self.guard()
        finally:
            os.close(fd)

    def test_open_record_refuses(self):
        a = self.initialize()
        a.bootstrap_owner.open()
        a.bootstrap_owner.lease.close()
        with self.assertRaisesRegex(W.Refusal, 'closed'):
            self.guard()

    def test_shutdown_and_bootstrap_allowed_but_replacement_never_adopted(self):
        a = self.initialize()
        a.bootstrap_owner.lease.close()
        guard = self.guard()
        record = a._read()
        record['reason'] = 'shutdown'
        record['owner_nonce'] = str(uuid.uuid4())
        a._publish(record)
        with self.assertRaisesRegex(W.Refusal, 'metadata|inode changed'):
            guard.check()
        guard.close()
        with self.guard():
            self.assertEqual(a._read()['reason'], 'shutdown')

    def test_bad_metadata_nofollow_hardlink_and_record_bytes(self):
        a = self.initialize()
        a.bootstrap_owner.lease.close()
        gate = self.path / 'gate.json'
        original = gate.read_bytes()
        for payload in (b'{}\n', original.rstrip(), original.replace(b'"schema":1', b'"schema":true'),
                        original.replace(b'"schema":1', b'"schema":1,"schema":1')):
            gate.chmod(0o640)
            gate.write_bytes(payload)
            gate.chmod(0o440)
            with self.assertRaises(W.Refusal):
                self.guard()
        gate.chmod(0o640)
        gate.write_bytes(original)
        gate.chmod(0o440)
        os.link(gate, self.path / 'extra')
        with self.assertRaises(W.Refusal):
            self.guard()
        (self.path / 'extra').unlink()
        gate.rename(self.path / 'original')
        gate.symlink_to('original')
        with self.assertRaises(OSError):
            self.guard()
        gate.unlink()
        (self.path / 'original').rename(gate)
        self.path.chmod(0o770)
        with self.assertRaises(W.Refusal):
            self.guard()

    def test_actual_pid_thread_and_closed_creator_binding(self):
        guard = self.guard()
        results = []
        def foreign():
            for method in (guard.check, guard.close):
                try:
                    method()
                except W.Refusal:
                    results.append(True)
        thread = threading.Thread(target=foreign)
        thread.start()
        thread.join()
        self.assertEqual(results, [True, True])
        child = os.fork()
        if child == 0:
            try:
                guard.close()
            except W.Refusal:
                os._exit(0)
            os._exit(1)
        self.assertEqual(os.waitpid(child, 0)[1], 0)
        guard.close()
        thread = threading.Thread(target=foreign)
        thread.start()
        thread.join()
        self.assertEqual(results, [True] * 4)
        with self.assertRaises(W.Refusal):
            guard.check()

    def test_stolen_lock_and_inode_replacement_refuse(self):
        guard = self.guard()
        fcntl.flock(guard.init_fd, fcntl.LOCK_UN)
        with self.assertRaisesRegex(W.Refusal, 'flock'):
            guard.check()
        guard.close()
        guard = self.guard()
        lock = Path(self.parent) / guard.INIT
        lock.unlink()
        lock.touch(mode=0o600)
        with self.assertRaisesRegex(W.Refusal, 'metadata|inode'):
            guard.check()

    def test_private_mount_addition_allowed_run_replacement_refuses(self):
        guard = self.guard()
        with patch.object(W, 'mountinfo', return_value=self.mounts + [
                dict(path='/private-state', fstype='ext4', dev=(7, 1), options=['rw'])]):
            guard.check()
        original = self.parent + '-old'
        os.rename(self.parent, original)
        self.addCleanup(shutil.rmtree, original)
        os.mkdir(self.parent, 0o700)
        with self.assertRaisesRegex(W.Refusal, 'identity changed'):
            guard.check()

    def test_close_closes_all_descriptors_once_even_on_error(self):
        guard = self.guard()
        held = list(reversed(guard.fds))
        closed = []
        real_close = os.close
        def close(fd):
            closed.append(fd)
            real_close(fd)
            if fd == held[0]:
                raise OSError(errno.EIO, 'injected close acknowledgement error')
        with patch.object(W.os, 'close', side_effect=close):
            with self.assertRaises(OSError):
                guard.close()
            guard.close()
        self.assertEqual(closed, held)


    def test_root_loss_refuses_work_but_creator_can_close(self):
        guard = self.guard()
        with patch.object(W.os, 'getresuid', return_value=(1000, 1000, 1000)):
            with self.assertRaisesRegex(W.Refusal, 'actual root'):
                guard.check()
            guard.close()


    def test_failed_acquisition_has_no_fd_leak(self):
        a = self.initialize()
        before = len(os.listdir('/proc/self/fd'))
        for unused in range(10):
            with self.assertRaises(BlockingIOError):
                self.guard()
        self.assertEqual(len(os.listdir('/proc/self/fd')), before)
        a.bootstrap_owner.lease.close()
        guard = self.guard()
        held = list(guard.fds)
        guard.close()
        for fd in held:
            with self.assertRaises(OSError):
                os.fstat(fd)


class CensusTests(unittest.TestCase):
    def test_actual_census_or_permission_refusal(self):
        # The privileged suite must successfully enumerate every visible task.
        if os.getresuid() == (0, 0, 0):
            W.offline_consumer_check()

    def test_kvm_names_alias_permissions_and_bounds(self):
        class Entries:
            def __init__(self, entries):
                self.entries = entries
            def __enter__(self):
                return iter(self.entries)
            def __exit__(self, *args):
                pass
        from types import SimpleNamespace as Entry
        entries = {'/proc': [Entry(name='1', path='/proc/1')],
                   '/proc/1/task': [Entry(name='1', path='/proc/1/task/1')],
                   '/proc/1/task/1/fd': [Entry(name='3', path='/proc/1/task/1/fd/3')]}
        with patch.object(W.os, 'scandir', side_effect=lambda p: Entries(entries[p])):
            for target in ('/dev/kvm', 'anon_inode:kvm-vm', 'anon_inode:[kvm-vcpu:0]'):
                with patch.object(W.os, 'readlink', return_value=target), self.assertRaises(W.Refusal):
                    W.offline_consumer_check()
            with patch.object(W.os, 'readlink', return_value='/alias'), patch.object(
                    W.os, 'stat', return_value=Entry(st_mode=0o20600, st_rdev=os.makedev(10, 232))), self.assertRaises(W.Refusal):
                W.offline_consumer_check()
            for code in (errno.EACCES, errno.EIO):
                with patch.object(W.os, 'readlink', side_effect=OSError(code, 'denied')), self.assertRaises(OSError):
                    W.offline_consumer_check()
            for code in (errno.ENOENT, errno.ESRCH):
                with patch.object(W.os, 'readlink', side_effect=OSError(code, 'gone')):
                    W.offline_consumer_check()
            with patch.object(W.OfflineAdmission, 'FD_LIMIT', 0), self.assertRaises(W.Refusal):
                W.offline_consumer_check()
            with patch.object(W.time, 'monotonic', side_effect=[0, 11]), self.assertRaises(W.Refusal):
                W.offline_consumer_check()


if __name__ == '__main__':
    unittest.main()
