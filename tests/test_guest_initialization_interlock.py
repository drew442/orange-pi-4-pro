"""Native root/tmpfs initialization exclusion; no hardware acceptance claim."""
import fcntl
import importlib.util
import os
from pathlib import Path
import tempfile
import threading
import unittest
import uuid
from unittest import mock

SPEC = importlib.util.spec_from_file_location(
    'interlock_admission', Path(__file__).resolve().parents[1] / 'scripts/guest-admission.py')
a = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(a)


@unittest.skipUnless(os.geteuid() == 0, 'native root metadata checks require root')
class InitializationInterlockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='a733-interlock-test-', dir='/run')
        self.addCleanup(self.tmp.cleanup)
        self.parent = Path(self.tmp.name)
        self.runtime = self.parent / 'a733-admission'
        self.lockpath = self.parent / a.InitializationLock.NAME
        for patch in (mock.patch.object(a, 'RUNTIME_PARENT', str(self.parent)),
                      mock.patch.object(a, 'RUNTIME', str(self.runtime))):
            patch.start()
            self.addCleanup(patch.stop)
        self.boot = str(uuid.uuid4())

    def acquire(self):
        lock = a.InitializationLock.acquire()
        self.addCleanup(lock.close)
        return lock

    def initialize(self):
        admission = a.initialize(self.boot, 0)
        self.addCleanup(admission.close)
        return admission

    def test_native_metadata_readonly_and_actual_self_ex(self):
        lock = self.acquire()
        info = os.fstat(lock.check())
        self.assertEqual((info.st_uid, info.st_gid, info.st_mode & 0o7777,
                          info.st_size, info.st_nlink), (0, 0, 0o600, 0, 1))
        self.assertEqual(fcntl.fcntl(lock.fd, fcntl.F_GETFL) & os.O_ACCMODE, os.O_RDONLY)
        self.assertFalse(os.get_inheritable(lock.fd))
        with self.assertRaises(OSError):
            os.write(lock.fd, b'x')
        a._held_lock(lock.fd, 'WRITE', os.getpid())

    def test_held_lock_blocks_initialize_before_runtime_creation(self):
        lock = self.acquire()
        with self.assertRaises(a.AdmissionError):
            self.initialize()
        self.assertFalse(self.runtime.exists())
        lock.close()
        admission = self.initialize()
        self.assertEqual(admission._read()['reason'], 'bootstrap')

    def test_competing_open_description_and_fork_refuse(self):
        lock = self.acquire()
        with self.assertRaises(a.AdmissionError):
            a.InitializationLock.acquire()
        pid = os.fork()
        if pid == 0:
            try:
                for operation in (lock.check, lock.close, a.InitializationLock.acquire,
                                  lambda: a.initialize(self.boot, 0)):
                    try:
                        operation()
                    except a.AdmissionError:
                        continue
                    os._exit(2)
                os._exit(0)
            except BaseException:
                os._exit(3)
        self.assertEqual(os.waitpid(pid, 0)[1], 0)
        lock.check()
        self.assertFalse(self.runtime.exists())

    def test_competing_writable_description_cannot_claim_exclusion(self):
        self.acquire()
        writer = os.open(self.lockpath, os.O_RDWR | os.O_CLOEXEC)
        try:
            with self.assertRaises(BlockingIOError):
                fcntl.flock(writer, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(writer)

    def test_guard_loss_after_mkdir_refuses_runtime_file_creation(self):
        mkdir = os.mkdir
        def create_then_corrupt(*args, **kwargs):
            mkdir(*args, **kwargs)
            self.lockpath.chmod(0o640)
        with mock.patch.object(a.os, 'mkdir', side_effect=create_then_corrupt):
            with self.assertRaises(a.AdmissionError):
                self.initialize()
        self.assertTrue(self.runtime.is_dir())
        self.assertEqual(list(self.runtime.iterdir()), [])

    def test_thread_binding_even_after_close(self):
        lock = self.acquire()
        for closed in (False, True):
            if closed:
                lock.close()
            errors = []
            def attempt():
                for operation in (lock.check, lock.close):
                    try:
                        operation()
                    except a.AdmissionError:
                        errors.append(True)
            thread = threading.Thread(target=attempt)
            thread.start()
            thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [True, True])

    def test_fresh_and_existing_keep_bootstrap_lease_and_do_not_reset(self):
        admission = self.initialize()
        before = (self.runtime / 'gate.json').read_bytes()
        self.acquire().close()  # initialize released the initialization lock.
        writer = admission._open('leases.lock', 0o640)
        try:
            with self.assertRaises(a.AdmissionError):
                a._lock(writer, fcntl.LOCK_EX, 0)
        finally:
            os.close(writer)
        repeat = self.initialize()
        self.assertIsNone(repeat.bootstrap_owner)
        self.assertEqual((self.runtime / 'gate.json').read_bytes(), before)
        self.assertEqual(admission._read()['state'], 'closed')

    def test_shutdown_closure_is_never_stolen(self):
        admission = self.initialize()
        owner = admission.close_gate('shutdown')
        before = (self.runtime / 'gate.json').read_bytes()
        self.assertIsNotNone(owner)
        self.assertIsNone(self.initialize().bootstrap_owner)
        self.assertEqual((self.runtime / 'gate.json').read_bytes(), before)

    def test_interlock_retained_through_publish_and_existing_open(self):
        publish = a.Admission._publish
        def checked_publish(admission, record):
            with self.assertRaises(a.AdmissionError):
                a.InitializationLock.acquire()
            return publish(admission, record)
        with mock.patch.object(a.Admission, '_publish', checked_publish):
            self.initialize()
        existing = a.Admission.open_existing
        def checked_existing(*args):
            with self.assertRaises(a.AdmissionError):
                a.InitializationLock.acquire()
            return existing(*args)
        with mock.patch.object(a.Admission, 'open_existing', side_effect=checked_existing):
            self.initialize()

    def test_initial_validation_precedes_interlock_creation(self):
        for boot, gid in (('bad', 0), (self.boot, -1), (self.boot, True)):
            with self.assertRaises(a.AdmissionError):
                a.initialize(boot, gid)
            self.assertFalse(self.lockpath.exists())
        with mock.patch.object(a, 'RUNTIME', str(self.parent / 'wrong')):
            with self.assertRaises(a.AdmissionError):
                self.initialize()
        self.assertFalse(self.lockpath.exists())
        with mock.patch.object(a.os, 'geteuid', return_value=1000):
            with self.assertRaises(a.AdmissionError):
                self.acquire()
        self.assertFalse(self.lockpath.exists())

    def test_invalid_gid_refuses_before_paths_or_descriptors_are_created(self):
        for gid in (True, False, -1, -2**64, 2**32 - 1, 2**32, 2**64, 10**100):
            with self.subTest(gid=gid):
                before = len(os.listdir('/proc/self/fd'))
                with mock.patch.object(a.InitializationLock, 'acquire') as acquire:
                    with self.assertRaises(a.AdmissionError):
                        a.initialize(self.boot, gid)
                    acquire.assert_not_called()
                with self.assertRaises(a.AdmissionError):
                    a.Admission(self.boot, gid)
                self.assertEqual(len(os.listdir('/proc/self/fd')), before)
                self.assertFalse(self.lockpath.exists())
                self.assertFalse(self.runtime.exists())

    def test_valid_gid_boundaries_pass_validation_without_os_group_changes(self):
        # Validation-only: do not ask this host to create an arbitrary OS GID.
        for gid in (0, 2**32 - 2):
            with self.subTest(gid=gid):
                admission = a.Admission(self.boot, gid)
                self.assertEqual(admission.gid, gid)
                admission.close()
                marker = RuntimeError('validated before initialization interlock')
                with mock.patch.object(a.InitializationLock, 'acquire',
                                       side_effect=marker) as acquire:
                    with self.assertRaises(RuntimeError) as caught:
                        a.initialize(self.boot, gid)
                    self.assertIs(caught.exception, marker)
                    acquire.assert_called_once_with()
                self.assertFalse(self.lockpath.exists())
                self.assertFalse(self.runtime.exists())

    def test_unsafe_existing_objects_refuse_without_repair(self):
        for case in ('symlink', 'hardlink', 'mode', 'owner', 'group', 'bytes', 'fifo', 'directory'):
            with self.subTest(case=case):
                if case == 'symlink':
                    self.lockpath.symlink_to(self.parent / 'absent')
                elif case == 'fifo':
                    os.mkfifo(self.lockpath, 0o600)
                elif case == 'directory':
                    self.lockpath.mkdir(mode=0o600)
                else:
                    self.lockpath.touch(mode=0o600)
                    if case == 'hardlink':
                        os.link(self.lockpath, self.parent / 'alias')
                    elif case == 'mode':
                        self.lockpath.chmod(0o640)
                    elif case == 'owner':
                        os.chown(self.lockpath, 1, 0)
                    elif case == 'group':
                        os.chown(self.lockpath, 0, 1)
                    elif case == 'bytes':
                        self.lockpath.write_bytes(b'unchanged')
                before = self.lockpath.lstat()
                with self.assertRaises((a.AdmissionError, OSError)):
                    self.initialize()
                self.assertEqual(self.lockpath.lstat(), before)
                self.assertFalse(self.runtime.exists())
                if case == 'directory':
                    self.lockpath.rmdir()
                else:
                    self.lockpath.unlink()
                if case == 'hardlink':
                    (self.parent / 'alias').unlink()

    def test_check_refuses_named_replacement(self):
        lock = self.acquire()
        self.lockpath.rename(self.parent / 'old')
        self.lockpath.touch(mode=0o600)
        with self.assertRaises(a.AdmissionError):
            lock.check()

    def test_check_refuses_parent_replacement(self):
        lock = self.acquire()
        saved = self.parent.with_name(self.parent.name + '-saved')
        self.parent.rename(saved)
        try:
            self.parent.mkdir(mode=0o700)
            with self.assertRaises(a.AdmissionError):
                lock.check()
        finally:
            self.parent.rmdir()
            saved.rename(self.parent)

    def test_check_refuses_metadata_changes(self):
        lock = self.acquire()
        self.lockpath.chmod(0o640)
        with self.assertRaises(a.AdmissionError):
            lock.check()

    def test_check_refuses_unlocked_or_shared_description(self):
        lock = self.acquire()
        for mode in (fcntl.LOCK_UN, fcntl.LOCK_SH):
            fcntl.flock(lock.fd, mode)
            with self.assertRaises(a.AdmissionError):
                lock.check()

    def test_check_refuses_writable_replacement_fd(self):
        lock = self.acquire()
        writer = os.open(self.lockpath, os.O_RDWR | os.O_CLOEXEC)
        try:
            os.dup2(writer, lock.fd)
            fcntl.flock(lock.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(a.AdmissionError):
                lock.check()
        finally:
            os.close(writer)

    def test_close_failure_detaches_all_owned_fds_and_never_retries(self):
        lock = self.acquire()
        numbers = [lock.fd] + [entry[2] for entry in lock.chain]
        close = os.close
        calls = []
        def close_then_error(number):
            calls.append(number)
            close(number)
            raise InterruptedError('close completed')
        with mock.patch.object(a.os, 'close', side_effect=close_then_error):
            with self.assertRaises(InterruptedError):
                lock.close()
            lock.close()
        self.assertCountEqual(calls, numbers)
        self.assertIsNone(lock.fd)
        self.assertIsNone(lock.parent)
        self.assertEqual(lock.chain, [])
        for number in numbers:
            with self.assertRaises(OSError):
                os.fstat(number)

    def test_acquire_failure_closes_creation_and_parent_descriptors(self):
        before = len(os.listdir('/proc/self/fd'))
        with mock.patch.object(a.os, 'fsync', side_effect=OSError('sync failed')):
            with self.assertRaises(OSError):
                a.InitializationLock.acquire()
        self.assertEqual(len(os.listdir('/proc/self/fd')), before)
        self.assertFalse(self.runtime.exists())
        self.acquire().check()  # Root metadata was established; no reset needed.

    def test_close_failure_on_initialize_does_not_leak_bootstrap(self):
        close = a.InitializationLock.close
        def close_then_error(lock):
            close(lock)
            raise InterruptedError('interlock close failed')
        before = len(os.listdir('/proc/self/fd'))
        with mock.patch.object(a.InitializationLock, 'close', close_then_error):
            with self.assertRaises(InterruptedError):
                self.initialize()
        self.assertEqual(len(os.listdir('/proc/self/fd')), before)
        self.assertEqual(self.initialize()._read()['reason'], 'bootstrap')


if __name__ == '__main__':
    unittest.main()
