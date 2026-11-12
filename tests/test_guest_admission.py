"""Host-only admission safety/concurrency checks; no KVM acceptance claim."""
import importlib.util
import fcntl
import os
from pathlib import Path
import subprocess
import stat
import sys
import tempfile
import time
import unittest
import uuid
from unittest import mock

SPEC = importlib.util.spec_from_file_location(
    'guest_admission', Path(__file__).resolve().parents[1] / 'scripts/guest-admission.py')
a = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(a)
PRODUCTION_ANCESTOR = a._ancestor


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'a733-admission'
        self.boot = str(uuid.uuid4())
        self.gid = os.getgid()
        # Production has no relaxation flags. Patch only filesystem ownership,
        # tmpfs and /tmp ancestor constraints in these unprivileged host tests.
        for patch in (mock.patch.object(a, 'RUNTIME', str(self.path)),
                      mock.patch.object(a, 'RUNTIME_PARENT', self.tmp.name),
                      mock.patch.object(a, 'ROOT_UID', os.getuid()),
                      mock.patch.object(a, '_tmpfs'),
                      mock.patch.object(a, '_ancestor'),
                      mock.patch.object(a.os, 'geteuid', return_value=0)):
            patch.start()
            self.addCleanup(patch.stop)
        self.admission = a.initialize(self.boot, self.gid, lambda: 'running')
        self.addCleanup(self.admission.close)

    def opened(self):
        owner = self.admission.bootstrap_owner
        owner.open()
        owner.lease.close()
        return self.admission

    def test_bootstrap_closed_then_open(self):
        with self.assertRaises(a.AdmissionError):
            self.admission.admit()
        self.assertEqual(self.admission._read()['reason'], 'bootstrap')
        self.opened()
        with self.admission.admit() as lease:
            self.assertFalse(os.get_inheritable(lease.recheck()))
        self.assertEqual(self.admission._read()['generation'], 1)

    def test_repeat_initialize_does_not_steal_closure(self):
        self.opened()
        owner = self.admission.close_gate()
        with a.initialize(self.boot, self.gid, lambda: 'running') as repeat:
            self.assertIsNone(repeat.bootstrap_owner)
            self.assertIsNone(repeat.close_gate())
        self.assertEqual(self.admission._read()['owner_nonce'], owner.nonce)

    def test_gate_closure_while_shared_lease_retained(self):
        self.opened()
        with self.admission.admit() as running:
            owner = self.admission.close_gate()
            running.recheck()  # Closing gate does not invalidate existing runs.
            with self.assertRaises(a.AdmissionError):
                self.admission.admit()
            with self.assertRaises(a.AdmissionError):
                owner.exclusive(0.02)
        with owner.exclusive(0.1):
            owner.open()
        with self.admission.admit():
            pass

    def test_shutdown_replaces_maintenance_owner_and_never_reopens(self):
        self.opened()
        maintenance = self.admission.close_gate()
        shutdown = self.admission.close_gate('shutdown')
        self.assertIsNotNone(shutdown)
        with self.assertRaises(a.AdmissionError):
            maintenance.exclusive()
        with shutdown.exclusive():
            with self.assertRaises(a.AdmissionError):
                shutdown.open()
        self.assertIsNone(self.admission.close_gate())
        self.assertIsNone(self.admission.close_gate('shutdown'))

    def test_shutdown_supersedes_live_bootstrap_owner(self):
        bootstrap = self.admission.bootstrap_owner
        shutdown = self.admission.close_gate('shutdown')
        self.assertIsNotNone(shutdown)
        self.assertEqual(self.admission._read()['reason'], 'shutdown')
        with self.assertRaises(a.AdmissionError):
            bootstrap.open()
        bootstrap.lease.close()
        with shutdown.exclusive():
            with self.assertRaises(a.AdmissionError):
                shutdown.open()
        with self.assertRaises(a.AdmissionError):
            self.admission.admit()

    def test_fifo_candidates_refuse_without_blocking_open(self):
        code = """
import importlib.util, os, sys
spec = importlib.util.spec_from_file_location('admission', sys.argv[1])
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)
a.RUNTIME = sys.argv[2]
a.ROOT_UID = os.getuid()
a._tmpfs = lambda fd: None
a._ancestor = lambda info: None
try:
    a.Admission.open_existing(sys.argv[3], os.getgid())
except a.AdmissionError:
    print('refused', flush=True)
else:
    raise AssertionError('FIFO admitted')
"""
        for name in ('gate.lock', 'leases.lock', 'gate.json'):
            with self.subTest(name=name):
                path = self.path / name
                saved = self.path / ('saved-' + name)
                path.rename(saved)
                try:
                    os.mkfifo(path, 0o640)
                    result = subprocess.run([sys.executable, '-c', code, SPEC.origin,
                                             str(self.path), self.boot], capture_output=True,
                                            text=True, timeout=2)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout.strip(), 'refused')
                finally:
                    path.unlink()
                    saved.rename(path)

    def test_nonce_and_exclusive_lease_required(self):
        self.opened()
        owner = self.admission.close_gate()
        with self.assertRaises(a.AdmissionError):
            owner.open()
        with owner.exclusive():
            owner.nonce = str(uuid.uuid4())
            with self.assertRaises(a.AdmissionError):
                owner.open()

    def test_pid1_refuses_unknown_and_stopping(self):
        self.opened()
        for value in ('stopping', 'offline', 'unknown', None):
            self.admission.manager_state = lambda: value
            with self.assertRaises(a.AdmissionError):
                self.admission.admit()
        for value in ('starting', 'running', 'degraded'):
            self.admission.manager_state = lambda: value
            with self.admission.admit():
                pass
        self.admission.manager_state = a._manager_state
        with self.assertRaises(a.AdmissionError):
            self.admission.admit()

    def test_boot_mismatch(self):
        with self.assertRaises(a.AdmissionError):
            a.Admission.open_existing(str(uuid.uuid4()), self.gid)

    def test_lock_descriptors_readonly_and_flock_works(self):
        self.opened()
        self.assertTrue(fcntl.fcntl(self.admission.directory, fcntl.F_GETFL) & os.O_PATH)
        with self.admission.admit() as lease:
            for fd in (self.admission.gate_fd, self.admission.leases_fd, lease.fd):
                self.assertEqual(fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE,
                                 os.O_RDONLY)
            with self.assertRaises(OSError):
                os.write(lease.fd, b'x')

    def test_actual_subprocess_shared_lease_blocks_exclusive(self):
        self.opened()
        child = subprocess.Popen([sys.executable, '-c',
            'import fcntl,os,sys; f=os.open(sys.argv[1],os.O_RDONLY); '
            'fcntl.flock(f,fcntl.LOCK_SH); print("ready",flush=True); sys.stdin.read()',
            str(self.path / 'leases.lock')], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), 'ready')
            owner = self.admission.close_gate()
            start = time.monotonic()
            with self.assertRaises(a.AdmissionError):
                owner.exclusive(0.04)
            self.assertLess(time.monotonic() - start, 0.5)
        finally:
            child.communicate('done', timeout=5)
        with owner.exclusive():
            pass

    def test_gate_timeout_and_no_fd_leak(self):
        fd = os.open(self.path / 'gate.lock', os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            before = len(os.listdir('/proc/self/fd'))
            with mock.patch.object(a, 'GATE_TIMEOUT', 0.02):
                for _ in range(5):
                    with self.assertRaises(a.AdmissionError):
                        self.admission.admit()
            self.assertEqual(before, len(os.listdir('/proc/self/fd')))
        finally:
            os.close(fd)

    def test_lock_substitution_stale_inode_and_symlink(self):
        self.opened()
        with self.admission.admit() as lease:
            os.rename(self.path / 'leases.lock', self.path / 'old-lock')
            (self.path / 'leases.lock').symlink_to('old-lock')
            with self.assertRaises(a.AdmissionError):
                lease.recheck()
            with self.assertRaises(a.AdmissionError):
                self.admission.admit()

    def test_metadata_hardlink_and_bad_record_refuse(self):
        target = self.path / 'gate.json'
        os.link(target, self.path / 'alias')
        with self.assertRaises(a.AdmissionError):
            self.admission._read()
        os.unlink(self.path / 'alias')
        os.chmod(target, 0o640)
        with self.assertRaises(a.AdmissionError):
            self.admission._read()
        os.chmod(target, 0o440)
        # Trusted root corruption must not be silently rewritten by initialize.
        os.chmod(target, 0o600)
        target.write_bytes(b'{}\n')
        os.chmod(target, 0o440)
        with self.assertRaises(a.AdmissionError):
            a.initialize(self.boot, self.gid)
        self.assertEqual(target.read_bytes(), b'{}\n')

    def test_directory_substitution(self):
        original = self.path.with_name('old')
        os.rename(self.path, original)
        self.path.symlink_to(original, target_is_directory=True)
        with self.assertRaises(a.AdmissionError):
            self.admission.recheck()

    def test_strict_bounded_json(self):
        good = self.admission._read()
        for bad in (b'{}', a.encode(good) + b' ', b'\xff', b' '*4097,
                    a.encode(good).replace(b'"schema":1', b'"schema":true'),
                    a.encode(good).replace(b'"schema":1', b'"schema":1,"schema":1'),
                    a.encode(good).replace(b'"schema":1', b'"extra":1,"schema":1')):
            with self.assertRaises(a.AdmissionError):
                a.decode(bad)

    def test_open_failure_before_rename_stays_closed_and_poisoned(self):
        owner = self.admission.bootstrap_owner
        original = os.replace
        calls = 0
        def fail_once(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError('injected rename failure')
            return original(*args, **kwargs)
        with mock.patch.object(a.os, 'replace', side_effect=fail_once):
            with self.assertRaises(a.PublicationError) as caught:
                owner.open()
        self.assertEqual(caught.exception.stage, 'rename')
        self.assertFalse(caught.exception.uncertain)
        self.assertEqual(self.admission._read()['state'], 'closed')
        with self.assertRaises(a.AdmissionError):
            owner.open()
        self.assertFalse(list(self.path.glob('.gate-*')))

    def test_open_directory_fsync_failure_recloses(self):
        owner = self.admission.bootstrap_owner
        original = os.fsync
        fail = True
        def fail_once(fd):
            nonlocal fail
            if stat.S_ISDIR(os.fstat(fd).st_mode) and fail:
                fail = False
                raise OSError('injected dir fsync failure')
            return original(fd)
        with mock.patch.object(a.os, 'fsync', side_effect=fail_once):
            with self.assertRaises(a.PublicationError) as caught:
                owner.open()
        self.assertEqual(caught.exception.stage, 'directory-fsync')
        self.assertFalse(caught.exception.uncertain)
        self.assertEqual(self.admission._read()['state'], 'closed')

    def test_failed_rollback_reports_uncertain(self):
        owner = self.admission.bootstrap_owner
        original = os.fsync
        def fail_directory(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError('injected persistent failure')
            return original(fd)
        with mock.patch.object(a.os, 'fsync', side_effect=fail_directory):
            with self.assertRaises(a.PublicationError) as caught:
                owner.open()
        self.assertTrue(caught.exception.uncertain)
        self.assertTrue(owner.poisoned)

    def test_file_fsync_failure_preserves_closed_gate(self):
        owner = self.admission.bootstrap_owner
        original = os.fsync
        fail = True
        def fail_once(fd):
            nonlocal fail
            if not stat.S_ISDIR(os.fstat(fd).st_mode) and fail:
                fail = False
                raise OSError('injected file fsync failure')
            return original(fd)
        with mock.patch.object(a.os, 'fsync', side_effect=fail_once):
            with self.assertRaises(a.PublicationError) as caught:
                owner.open()
        self.assertEqual(caught.exception.stage, 'file-fsync')
        self.assertFalse(caught.exception.uncertain)
        self.assertEqual(self.admission._read()['state'], 'closed')

    def test_repeat_bootstrap_with_active_lease_is_verified_noop(self):
        self.opened()
        before = (self.path / 'gate.json').read_bytes()
        with self.admission.admit():
            with a.initialize(self.boot, self.gid, lambda: 'running') as repeat:
                self.assertIsNone(repeat.bootstrap_owner)
                self.assertEqual(repeat._read()['state'], 'open')
        self.assertEqual((self.path / 'gate.json').read_bytes(), before)

    def test_concurrent_process_closures_have_only_one_owner(self):
        self.opened()
        code = """
import importlib.util, os, sys
spec = importlib.util.spec_from_file_location('admission', sys.argv[1])
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)
a.RUNTIME = sys.argv[2]
a.ROOT_UID = os.getuid()
a._tmpfs = lambda fd: None
a._ancestor = lambda info: None
a.os.geteuid = lambda: 0
with a.Admission.open_existing(sys.argv[3], os.getgid()) as client:
    result = client.close_gate()
    print(result.nonce if result else 'none', flush=True)
"""
        children = [subprocess.Popen([sys.executable, '-c', code, SPEC.origin,
                    str(self.path), self.boot], stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True) for _ in range(2)]
        results = []
        for child in children:
            stdout, stderr = child.communicate(timeout=5)
            self.assertEqual(child.returncode, 0, stderr)
            results.append(stdout.strip())
        self.assertEqual(results.count('none'), 1)
        nonce = next(item for item in results if item != 'none')
        self.assertEqual(self.admission._read()['owner_nonce'], nonce)

    def test_nonroot_cannot_create_or_close_gate(self):
        self.opened()
        with mock.patch.object(a.os, 'geteuid', return_value=1000):
            with self.assertRaises(a.AdmissionError):
                a.initialize(self.boot, self.gid)
            with self.assertRaises(a.AdmissionError):
                self.admission.close_gate()

    def test_real_ancestor_policy_rejects_writable_and_wrong_owner(self):
        # The production checker itself is covered independently of fixture patch.
        info = os.stat(self.tmp.name)
        fields = list(info)
        fields[0] |= 0o002
        with self.assertRaises(a.AdmissionError):
            PRODUCTION_ANCESTOR(os.stat_result(fields))
        fields = list(info)
        fields[4] = os.getuid() + 1
        with self.assertRaises(a.AdmissionError):
            PRODUCTION_ANCESTOR(os.stat_result(fields))



if __name__ == '__main__':
    unittest.main()
