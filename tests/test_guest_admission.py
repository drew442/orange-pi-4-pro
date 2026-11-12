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

    def test_lease_close_error_detaches_fd_and_does_not_retry_reused_number(self):
        self.opened()
        lease = self.admission.admit()
        lock = self.admission.guest_lock('probe', lease, create=True)
        fd = lease.fd
        original = os.close
        def close_then_error(number):
            original(number)
            raise InterruptedError('close completed but reported an error')
        with mock.patch.object(a.os, 'close', side_effect=close_then_error):
            with self.assertRaises(InterruptedError):
                lease.close()
        self.assertIsNone(lease.fd)
        reused = os.open('/dev/null', os.O_RDONLY)
        try:
            self.assertEqual(reused, fd)
            lease.close()
            os.fstat(reused)
            with self.assertRaises(a.AdmissionError):
                lock.recheck()
        finally:
            original(reused)
            lock.close()

    def test_admission_close_error_releases_other_fds_without_retry(self):
        owner = self.admission.bootstrap_owner
        failed_fd = owner.lease.fd
        descriptors = [self.admission.gate_fd, self.admission.leases_fd] + [
            fd for _, _, fd in self.admission.chain]
        original = os.close
        reused = []
        def close_then_error(number):
            original(number)
            if number == failed_fd and not reused:
                replacement = os.open('/dev/null', os.O_RDONLY)
                if replacement != number:
                    os.dup2(replacement, number, inheritable=False)
                    original(replacement)
                reused.append(number)
                raise InterruptedError('closed bootstrap lease')
        with mock.patch.object(a.os, 'close', side_effect=close_then_error):
            with self.assertRaises(InterruptedError):
                self.admission.close()
        try:
            self.assertEqual(reused, [failed_fd])
            self.assertIsNone(owner.lease.fd)
            for fd in descriptors:
                with self.assertRaises(OSError):
                    os.fstat(fd)
            with mock.patch.object(a.os, 'close', side_effect=AssertionError('retried close')):
                self.admission.close()
            os.fstat(reused[0])
        finally:
            original(reused[0])

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

    def test_guest_lock_bootstrap_and_readonly_existing(self):
        lease = self.admission.bootstrap_owner.lease
        with self.admission.guest_lock('alpha', lease, create=True) as guest:
            self.assertEqual(fcntl.fcntl(guest.fd, fcntl.F_GETFL) & os.O_ACCMODE,
                             os.O_RDONLY)
            self.assertTrue(fcntl.fcntl(guest.directory, fcntl.F_GETFL) & os.O_PATH)
            self.assertFalse(os.get_inheritable(guest.fd))
            identity = os.fstat(guest.fd).st_ino
        with mock.patch.object(a.os, 'geteuid', return_value=1000):
            with self.admission.guest_lock('alpha', lease) as guest:
                self.assertEqual(os.fstat(guest.fd).st_ino, identity)
            with self.assertRaises(a.AdmissionError):
                self.admission.guest_lock('beta', lease, create=True)
        with self.admission.guest_lock('alpha', lease, create=True) as guest:
            self.assertEqual(os.fstat(guest.fd).st_ino, identity)

    def test_guest_lock_refuses_unknown_and_invalid_names(self):
        lease = self.admission.bootstrap_owner.lease
        for name in ('../x', '', 'a/b', 'UPPER', 'x'*17, 'x.lock', None):
            with self.assertRaises(a.AdmissionError):
                self.admission.guest_lock(name, lease, create=True)
        with self.assertRaises(FileNotFoundError):
            self.admission.guest_lock('unknown', lease)
        self.assertFalse((self.path / 'guests').exists())

    def test_guest_locks_exclude_same_name_and_keep_names_independent(self):
        self.opened()
        with self.admission.admit() as lease:
            with self.admission.guest_lock('alpha', lease, create=True) as alpha:
                with self.assertRaises(a.AdmissionError):
                    self.admission.guest_lock('alpha', lease, timeout=0.01)
                with self.admission.guest_lock('beta', lease, create=True):
                    alpha.recheck()
                # Already admitted transactions finish after global closure.
                self.admission.close_gate()
                alpha.recheck()
                with self.admission.guest_lock('beta', lease):
                    pass

    def test_guest_lock_requires_same_live_lease(self):
        self.opened()
        with self.admission.admit() as lease:
            with a.Admission.open_existing(self.boot, self.gid) as other:
                with self.assertRaises(a.AdmissionError):
                    other.guest_lock('alpha', lease, create=True)
            with self.assertRaises(a.AdmissionError):
                self.admission.guest_lock('alpha', object(), create=True)
            guest = self.admission.guest_lock('alpha', lease, create=True)
            try:
                lease.close()
                with self.assertRaises(a.AdmissionError):
                    guest.recheck()
                with self.assertRaises(a.AdmissionError):
                    self.admission.guest_lock('beta', lease, create=True)
            finally:
                guest.close()

    def test_guest_lock_recheck_refuses_writable_descriptor(self):
        lease = self.admission.bootstrap_owner.lease
        with self.admission.guest_lock('alpha', lease, create=True) as guest:
            os.close(guest.fd)
            guest.fd = os.open(self.path / 'guests/alpha.lock', os.O_RDWR)
            with self.assertRaises(a.AdmissionError):
                guest.recheck()

    def test_guest_lock_subprocess_exclusion(self):
        lease = self.admission.bootstrap_owner.lease
        with self.admission.guest_lock('alpha', lease, create=True):
            pass
        code = ('import os,fcntl,sys; f=os.open(sys.argv[1],os.O_RDONLY); '
                'fcntl.flock(f,fcntl.LOCK_EX); print("ready",flush=True); sys.stdin.read()')
        child = subprocess.Popen([sys.executable, '-c', code,
                                  str(self.path / 'guests/alpha.lock')],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), 'ready')
            before = len(os.listdir('/proc/self/fd'))
            for _ in range(3):
                with self.assertRaises(a.AdmissionError):
                    self.admission.guest_lock('alpha', lease, timeout=0.01)
            self.assertEqual(before, len(os.listdir('/proc/self/fd')))
            with self.admission.guest_lock('beta', lease, create=True):
                pass
        finally:
            child.communicate('done', timeout=5)
        with self.admission.guest_lock('alpha', lease):
            pass

    def test_guest_lock_unsafe_fifo_symlink_and_hardlink(self):
        lease = self.admission.bootstrap_owner.lease
        with self.admission.guest_lock('alpha', lease, create=True):
            pass
        guests = self.path / 'guests'
        os.mkfifo(guests / 'fifo.lock', 0o640)
        (guests / 'link.lock').symlink_to('alpha.lock')
        os.link(guests / 'alpha.lock', guests / 'hard.lock')
        before = len(os.listdir('/proc/self/fd'))
        for name in ('fifo', 'link', 'hard', 'alpha'):
            for create in (True, False):
                with self.assertRaises((a.AdmissionError, OSError)):
                    self.admission.guest_lock(name, lease, create=create)
        self.assertEqual(before, len(os.listdir('/proc/self/fd')))

    def test_guest_lock_unsafe_directory_and_inode_substitution(self):
        lease = self.admission.bootstrap_owner.lease
        with self.admission.guest_lock('alpha', lease, create=True) as guest:
            os.rename(self.path / 'guests/alpha.lock', self.path / 'guests/old.lock')
            (self.path / 'guests/alpha.lock').touch(mode=0o640)
            with self.assertRaises(a.AdmissionError):
                guest.recheck()
        with self.admission.guest_lock('beta', lease, create=True) as guest:
            os.rename(self.path / 'guests', self.path / 'old-guests')
            (self.path / 'guests').symlink_to('old-guests', target_is_directory=True)
            with self.assertRaises(a.AdmissionError):
                guest.recheck()
            with self.assertRaises(OSError):
                self.admission.guest_lock('beta', lease)

    def test_guest_lock_rejects_bad_directory_metadata_without_repair(self):
        lease = self.admission.bootstrap_owner.lease
        with self.admission.guest_lock('alpha', lease, create=True):
            pass
        guests = self.path / 'guests'
        os.chmod(guests, 0o750)
        with self.assertRaises(a.AdmissionError):
            self.admission.guest_lock('alpha', lease, create=True)
        self.assertEqual(stat.S_IMODE(guests.stat().st_mode), 0o750)

    def test_guest_creation_fsync_failures_leave_no_fd_leaks(self):
        # A fresh tree has six boundaries: guests fsync, runtime fsync,
        # created lock fsync, readonly lock fsync, guests fsync, runtime fsync.
        for fail_at in range(1, 7):
            with self.subTest(fail_at=fail_at):
                with tempfile.TemporaryDirectory(dir=self.tmp.name) as parent:
                    runtime = str(Path(parent) / 'a733-admission')
                    with mock.patch.object(a, 'RUNTIME', runtime), \
                            mock.patch.object(a, 'RUNTIME_PARENT', parent):
                        with a.initialize(self.boot, self.gid) as admission:
                            lease = admission.bootstrap_owner.lease
                            before = len(os.listdir('/proc/self/fd'))
                            original = os.fsync
                            count = 0
                            def fail_once(fd):
                                nonlocal count
                                count += 1
                                if count == fail_at:
                                    raise OSError('injected creation fsync failure')
                                return original(fd)
                            with mock.patch.object(a.os, 'fsync', side_effect=fail_once):
                                with self.assertRaises(OSError):
                                    admission.guest_lock('alpha', lease, create=True)
                            self.assertEqual(count, fail_at)
                            self.assertEqual(before, len(os.listdir('/proc/self/fd')))
                            # Safe partial creation retains its inode and can be
                            # explicitly resynced, without replacing or repairing.
                            path = Path(runtime) / 'guests/alpha.lock'
                            identity = path.stat().st_ino if path.exists() else None
                            with admission.guest_lock('alpha', lease, create=True) as guest:
                                if identity is not None:
                                    self.assertEqual(os.fstat(guest.fd).st_ino, identity)

    def test_guest_creation_close_failure_does_not_leak_directory(self):
        lease = self.admission.bootstrap_owner.lease
        # Prepare the directory so the failure reaches the created writable lock.
        with self.admission.guest_lock('alpha', lease, create=True):
            pass
        original = os.close
        failed = False
        def close_created(number):
            nonlocal failed
            writable = (fcntl.fcntl(number, fcntl.F_GETFL) & os.O_ACCMODE == os.O_WRONLY)
            original(number)
            if writable and not failed:
                failed = True
                raise OSError('injected created-file close error')
        before = len(os.listdir('/proc/self/fd'))
        with mock.patch.object(a.os, 'close', side_effect=close_created):
            with self.assertRaises(OSError):
                self.admission.guest_lock('beta', lease, create=True)
        self.assertTrue(failed)
        self.assertEqual(before, len(os.listdir('/proc/self/fd')))
        identity = (self.path / 'guests/beta.lock').stat().st_ino
        with self.admission.guest_lock('beta', lease, create=True) as guest:
            self.assertEqual(os.fstat(guest.fd).st_ino, identity)

    def test_guest_close_error_still_closes_directory(self):
        lease = self.admission.bootstrap_owner.lease
        guest = self.admission.guest_lock('alpha', lease, create=True)
        fd, directory = guest.fd, guest.directory
        original = os.close
        def close_then_fail(number):
            original(number)
            if number == fd:
                raise OSError('injected close error after actual close')
        with mock.patch.object(a.os, 'close', side_effect=close_then_fail):
            with self.assertRaises(OSError):
                guest.close()
        for number in (fd, directory):
            with self.assertRaises(OSError):
                os.fstat(number)
        guest.close()

    def test_guest_timeout_bounds(self):
        lease = self.admission.bootstrap_owner.lease
        for timeout in (-1, 301, float('inf'), float('nan'), True, '0'):
            with self.assertRaises(a.AdmissionError):
                self.admission.guest_lock('alpha', lease, create=True, timeout=timeout)
        self.assertFalse((self.path / 'guests').exists())

    def managed_setup(self):
        lease = self.admission.bootstrap_owner.lease
        self.admission.prepare_managed(lease)
        guest = self.admission.guest_lock('alpha', lease, create=True)
        self.addCleanup(guest.close)
        return lease, guest

    def test_registry_lock_exclusion_independence_and_readonly(self):
        lease = self.admission.bootstrap_owner.lease
        with self.admission.registry_lock(lease) as registry:
            inode = os.fstat(registry.fd).st_ino
            self.assertEqual(stat.S_IMODE(os.fstat(registry.fd).st_mode), 0o600)
            self.assertEqual(fcntl.fcntl(registry.fd, fcntl.F_GETFL) & os.O_ACCMODE,
                             os.O_RDONLY)
            self.assertFalse(os.get_inheritable(registry.fd))
            with self.assertRaises(a.AdmissionError):
                self.admission.registry_lock(lease, timeout=0.01)
            with self.admission.guest_lock('alpha', lease, create=True):
                pass
            self.admission.close_gate('shutdown')
            registry.recheck()
        with self.admission.registry_lock(lease) as registry:
            self.assertEqual(os.fstat(registry.fd).st_ino, inode)

    def test_registry_subprocess_exclusion_and_stale_named_inode(self):
        lease = self.admission.bootstrap_owner.lease
        with self.admission.registry_lock(lease):
            pass
        child = subprocess.Popen([sys.executable, '-c',
            'import os,fcntl,sys; f=os.open(sys.argv[1],os.O_RDONLY); '
            'fcntl.flock(f,fcntl.LOCK_EX); print("ready",flush=True); sys.stdin.read()',
            str(self.path / 'registry.lock')], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), 'ready')
            with self.assertRaises(a.AdmissionError):
                self.admission.registry_lock(lease, timeout=0.02)
            with self.admission.guest_lock('independent', lease, create=True):
                pass
        finally:
            child.communicate('done', timeout=5)
        with self.admission.registry_lock(lease) as registry:
            (self.path / 'registry.lock').rename(self.path / 'old-registry')
            (self.path / 'registry.lock').write_bytes(b'')
            (self.path / 'registry.lock').chmod(0o600)
            with self.assertRaises(a.AdmissionError):
                registry.recheck()

    def test_registry_requires_root_same_live_lease_and_safe_inode(self):
        lease = self.admission.bootstrap_owner.lease
        with mock.patch.object(a.os, 'geteuid', return_value=1000):
            with self.assertRaises(a.AdmissionError):
                self.admission.registry_lock(lease)
        for timeout in (-1, 301, True, float('nan')):
            with self.assertRaises(a.AdmissionError):
                self.admission.registry_lock(lease, timeout)
        with self.assertRaises(a.AdmissionError):
            self.admission.registry_lock(object())
        registry = self.admission.registry_lock(lease)
        registry.close()
        target = self.path / 'registry.lock'
        os.chmod(target, 0o640)
        with self.assertRaises(a.AdmissionError):
            self.admission.registry_lock(lease)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o640)
        os.chmod(target, 0o600)
        os.link(target, self.path / 'alias-registry')
        with self.assertRaises(a.AdmissionError):
            self.admission.registry_lock(lease)
        (self.path / 'alias-registry').unlink()
        registry = self.admission.registry_lock(lease)
        lease.close()
        with self.assertRaises(a.AdmissionError):
            registry.recheck()
        registry.close()

    def test_registry_close_failure_never_retries_fd_number(self):
        registry = self.admission.registry_lock(self.admission.bootstrap_owner.lease)
        number = registry.fd
        original = os.close
        def close_fail(fd):
            original(fd)
            raise InterruptedError('closed')
        with mock.patch.object(a.os, 'close', side_effect=close_fail):
            with self.assertRaises(InterruptedError):
                registry.close()
        reused = os.open('/dev/null', os.O_RDONLY)
        try:
            self.assertEqual(reused, number)
            registry.close()
            os.fstat(reused)
        finally:
            original(reused)

    def test_registry_fsync_failures_do_not_replace_partial_inode(self):
        lease = self.admission.bootstrap_owner.lease
        for failed_stage in (1, 2):
            target = self.path / 'registry.lock'
            if target.exists():
                target.unlink()
            before = len(os.listdir('/proc/self/fd'))
            calls = 0
            original = os.fsync
            def fail(fd):
                nonlocal calls
                calls += 1
                if calls == failed_stage:
                    raise OSError('fsync')
                return original(fd)
            with mock.patch.object(a.os, 'fsync', side_effect=fail):
                with self.assertRaises(OSError):
                    self.admission.registry_lock(lease)
            inode = target.stat().st_ino
            self.assertEqual(before, len(os.listdir('/proc/self/fd')))
            with self.admission.registry_lock(lease) as lock:
                self.assertEqual(os.fstat(lock.fd).st_ino, inode)

    def test_new_helpers_do_not_reacquire_gate(self):
        lease = self.admission.bootstrap_owner.lease
        with mock.patch.object(self.admission, '_gate',
                               side_effect=AssertionError('nested gate')):
            self.admission.prepare_managed(lease)
            with self.admission.guest_lock('alpha', lease, create=True) as guest:
                with self.admission.registry_lock(lease):
                    pass
                self.admission.mark_managed('alpha', lease, guest, guard=lambda: None)
                self.assertTrue(self.admission.is_managed('alpha', lease, guest))

    def test_managed_preparation_fsync_failures_keep_existing_directory(self):
        lease = self.admission.bootstrap_owner.lease
        original = os.fsync
        for fail_at in (1, 2):
            calls = 0
            def fail(fd):
                nonlocal calls
                calls += 1
                if calls == fail_at:
                    raise OSError('preparation fsync failure')
                return original(fd)
            before = len(os.listdir('/proc/self/fd'))
            with mock.patch.object(a.os, 'fsync', side_effect=fail):
                with self.assertRaises(OSError):
                    self.admission.prepare_managed(lease)
            directory = self.path / 'managed'
            inode = directory.stat().st_ino
            self.assertEqual(before, len(os.listdir('/proc/self/fd')))
            self.admission.prepare_managed(lease)
            self.assertEqual(directory.stat().st_ino, inode)

    def test_managed_missing_preparation_refuses_missing_marker_is_false(self):
        lease = self.admission.bootstrap_owner.lease
        with self.admission.guest_lock('alpha', lease, create=True) as guest:
            with self.assertRaises(FileNotFoundError):
                self.admission.is_managed('alpha', lease, guest)
            self.admission.prepare_managed(lease)
            self.assertFalse(self.admission.is_managed('alpha', lease, guest))
            self.assertTrue(stat.S_ISDIR((self.path / 'managed').stat().st_mode))
            self.assertEqual(stat.S_IMODE((self.path / 'managed').stat().st_mode), 0o710)
            with mock.patch.object(a.os, 'geteuid', return_value=1000):
                with self.assertRaises(a.AdmissionError):
                    self.admission.prepare_managed(lease)

    def test_marker_canonical_idempotent_retained_and_nonroot_read(self):
        lease, guest = self.managed_setup()
        guard = mock.Mock()
        self.admission.mark_managed('alpha', lease, guest, guard=guard)
        self.assertEqual(guard.call_count, 2)
        path = self.path / 'managed/alpha.json'
        inode = path.stat().st_ino
        self.assertEqual(path.read_bytes(), self.admission._marker_bytes('alpha'))
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o440)
        with mock.patch.object(a.os, 'link', side_effect=AssertionError('republish')):
            with mock.patch.object(a.os, 'fsync', wraps=os.fsync) as synced:
                self.admission.mark_managed('alpha', lease, guest, guard=guard)
            self.assertEqual(synced.call_count, 3)
        self.assertEqual(path.stat().st_ino, inode)
        with mock.patch.object(a.os, 'geteuid', return_value=1000):
            self.assertTrue(self.admission.is_managed('alpha', lease, guest))
            with self.assertRaises(a.AdmissionError):
                self.admission.mark_managed('alpha', lease, guest, guard=guard)

    def test_marker_guard_before_mutation_and_after_publication_failure(self):
        lease, guest = self.managed_setup()
        path = self.path / 'managed/alpha.json'
        with self.assertRaises(RuntimeError):
            self.admission.mark_managed('alpha', lease, guest,
                                        guard=mock.Mock(side_effect=RuntimeError('trust')))
        self.assertFalse(path.exists())
        calls = 0
        def guard():
            nonlocal calls
            calls += 1
            if calls == 2:
                self.assertTrue(path.exists())
                raise RuntimeError('head enrollment must not proceed')
        with self.assertRaises(a.PublicationError) as caught:
            self.admission.mark_managed('alpha', lease, guest, guard=guard)
        self.assertEqual(caught.exception.stage, 'guard')
        self.assertTrue(caught.exception.uncertain)
        self.assertTrue(self.admission.is_managed('alpha', lease, guest))
        # Controller first-head failure/disable leaves the exclusion established.
        self.assertTrue(path.exists())

    def test_marker_requires_matching_guest_and_live_lease(self):
        lease, guest = self.managed_setup()
        with self.admission.guest_lock('beta', lease, create=True) as beta:
            for function in (self.admission.is_managed,
                             lambda n, l, g: self.admission.mark_managed(n, l, g, guard=lambda: None)):
                with self.assertRaises(a.AdmissionError):
                    function('alpha', lease, beta)
                with self.assertRaises(a.AdmissionError):
                    function('alpha', object(), guest)
        guest.close()
        with self.assertRaises(a.AdmissionError):
            self.admission.is_managed('alpha', lease, guest)
        guest = self.admission.guest_lock('alpha', lease)
        try:
            lease.close()
            with self.assertRaises(a.AdmissionError):
                self.admission.mark_managed('alpha', lease, guest, guard=lambda: None)
        finally:
            guest.close()

    def test_marker_corrupt_unknown_duplicate_foreign_and_unsafe_never_repair(self):
        lease, guest = self.managed_setup()
        path = self.path / 'managed/alpha.json'
        good = self.admission._marker_bytes('alpha')
        bad_records = (b'{}', b'x'*4097, good + b' ',
                       good.replace(b'"schema":1', b'"schema":true'),
                       good.replace(b'"schema":1', b'"schema":1,"schema":1'),
                       good.replace(b'"schema":1', b'"schema":1,"other":1'),
                       good.replace(self.boot.encode(), str(uuid.uuid4()).encode()))
        for data in bad_records:
            path.write_bytes(data)
            path.chmod(0o440)
            inode = path.stat().st_ino
            for function in (self.admission.is_managed,
                             lambda n, l, g: self.admission.mark_managed(n, l, g, guard=lambda: None)):
                with self.assertRaises(a.AdmissionError):
                    function('alpha', lease, guest)
            self.assertEqual(path.stat().st_ino, inode)
            self.assertEqual(path.read_bytes(), data)
            path.unlink()
        for kind in ('fifo', 'symlink', 'hardlink', 'directory', 'mode'):
            with self.subTest(kind=kind):
                if kind == 'fifo':
                    os.mkfifo(path, 0o440)
                elif kind == 'symlink':
                    path.symlink_to('../gate.json')
                elif kind == 'directory':
                    path.mkdir(mode=0o440)
                else:
                    path.write_bytes(good)
                    path.chmod(0o440 if kind == 'hardlink' else 0o640)
                    if kind == 'hardlink':
                        os.link(path, path.with_name('alias'))
                for function in (self.admission.is_managed,
                                 lambda n, l, g: self.admission.mark_managed(n, l, g, guard=lambda: None)):
                    with self.assertRaises((a.AdmissionError, OSError)):
                        function('alpha', lease, guest)
                if kind == 'directory':
                    path.rmdir()
                else:
                    path.unlink()
                if kind == 'hardlink':
                    path.with_name('alias').unlink()

    def test_managed_directory_metadata_is_not_repaired(self):
        lease, guest = self.managed_setup()
        directory = self.path / 'managed'
        directory.chmod(0o750)
        for function in (lambda: self.admission.prepare_managed(lease),
                         lambda: self.admission.is_managed('alpha', lease, guest)):
            with self.assertRaises(a.AdmissionError):
                function()
        self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o750)

    def test_marker_publication_fsync_boundaries_and_no_false_completion(self):
        lease, guest = self.managed_setup()
        path = self.path / 'managed/alpha.json'
        for fail_at in (1, 2, 3):
            calls = 0
            original = os.fsync
            def fail(fd):
                nonlocal calls
                calls += 1
                if calls == fail_at:
                    raise OSError('injected fsync')
                return original(fd)
            before = len(os.listdir('/proc/self/fd'))
            with mock.patch.object(a.os, 'fsync', side_effect=fail):
                with self.assertRaises(a.PublicationError) as caught:
                    self.admission.mark_managed('alpha', lease, guest, guard=lambda: None)
            self.assertEqual(caught.exception.uncertain, fail_at > 1)
            self.assertEqual(before, len(os.listdir('/proc/self/fd')))
            self.assertFalse(list((self.path / 'managed').glob('.managed-*')))
            self.assertEqual(path.exists(), fail_at > 1)
            if path.exists():
                path.unlink()

    def test_marker_link_and_unlink_failure_conserve_exclusion(self):
        lease, guest = self.managed_setup()
        path = self.path / 'managed/alpha.json'
        original_link, original_unlink = os.link, os.unlink
        def link_then_fail(*args, **kwargs):
            original_link(*args, **kwargs)
            raise OSError('link happened without acknowledgement')
        with mock.patch.object(a.os, 'link', side_effect=link_then_fail):
            with self.assertRaises(a.PublicationError) as caught:
                self.admission.mark_managed('alpha', lease, guest, guard=lambda: None)
        self.assertTrue(caught.exception.uncertain)
        self.assertTrue(self.admission.is_managed('alpha', lease, guest))
        path.unlink()
        def refuse_unlink(*args, **kwargs):
            raise OSError('unlink failure')
        with mock.patch.object(a.os, 'unlink', side_effect=refuse_unlink):
            with self.assertRaises(a.PublicationError) as caught:
                self.admission.mark_managed('alpha', lease, guest, guard=lambda: None)
        self.assertEqual(caught.exception.stage, 'temporary-unlink')
        self.assertTrue(caught.exception.uncertain)
        self.assertEqual(path.stat().st_nlink, 2)
        # An incomplete no-replace publication is unsafe and never repaired.
        with self.assertRaises(a.AdmissionError):
            self.admission.mark_managed('alpha', lease, guest, guard=lambda: None)
        for temporary in (self.path / 'managed').glob('.managed-*'):
            original_unlink(temporary)

    def test_marker_noreplace_race_and_owned_cleanup_only(self):
        lease, guest = self.managed_setup()
        path = self.path / 'managed/alpha.json'
        original_link = os.link
        raced_inode = []
        def concurrent_marker(*args, **kwargs):
            path.write_bytes(b'foreign record')
            path.chmod(0o440)
            raced_inode.append(path.stat().st_ino)
            return original_link(*args, **kwargs)
        with mock.patch.object(a.os, 'link', side_effect=concurrent_marker):
            with self.assertRaises(a.PublicationError) as caught:
                self.admission.mark_managed('alpha', lease, guest, guard=lambda: None)
        self.assertEqual(caught.exception.stage, 'link')
        self.assertEqual(path.stat().st_ino, raced_inode[0])
        self.assertEqual(path.read_bytes(), b'foreign record')
        path.unlink()
        # A privileged substitution of the temporary name must not be cleaned.
        original_write = os.write
        replaced = []
        def substitute(fd, data):
            result = original_write(fd, data)
            temporary = next((self.path / 'managed').glob('.managed-*'))
            temporary.rename(temporary.with_name('retained-original'))
            temporary.write_bytes(b'do not remove')
            replaced.append(temporary)
            return result
        with mock.patch.object(a.os, 'write', side_effect=substitute):
            with self.assertRaises(a.PublicationError):
                self.admission.mark_managed('alpha', lease, guest, guard=lambda: None)
        self.assertEqual(replaced[0].read_bytes(), b'do not remove')
        self.assertFalse(path.exists())

    def test_marker_close_failure_detaches_and_does_not_retry(self):
        lease, guest = self.managed_setup()
        original = os.close
        closed = []
        def fail_marker_close(fd):
            info = os.fstat(fd)
            original(fd)
            if stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o440:
                closed.append(fd)
                raise InterruptedError('closed marker')
        before = len(os.listdir('/proc/self/fd'))
        with mock.patch.object(a.os, 'close', side_effect=fail_marker_close):
            with self.assertRaises(a.PublicationError) as caught:
                self.admission.mark_managed('alpha', lease, guest, guard=lambda: None)
        self.assertEqual(caught.exception.stage, 'close')
        self.assertTrue(caught.exception.uncertain)
        self.assertEqual(len(closed), 1)
        self.assertEqual(before, len(os.listdir('/proc/self/fd')))
        self.assertTrue(self.admission.is_managed('alpha', lease, guest))

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
