"""Native readonly locks/files, explicit A733/PID1/physical-placement substitutes.

Producer fixtures simulate completed shutdown; this establishes no hardware,
restore publication or boot acceptance. No real target/device is accessed.
"""
import copy
import fcntl
import importlib.util
import mmap
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


C = module('restore_custody_test', 'scripts/managed-guest-restore-custody.py')
B = module('restore_custody_sender_fixture', 'tests/test_managed_backup_sender.py')
A = C.ADMISSION


class Boundaries(unittest.TestCase):
    def test_nonroot_before_host_reads(self):
        with patch.object(C.os, 'getresuid', return_value=(1000, 1000, 1000)), \
                patch.object(C.CONTEXT, 'read_system') as read:
            with self.assertRaisesRegex(C.CustodyError, 'actual root'):
                C.ShutdownRestoreCustody({}, 'one')
            read.assert_not_called()

    def test_installed_source_is_mandatory(self):
        with self.assertRaisesRegex(A.AdmissionError, 'installed restore source'):
            C.installed_guard('11111111-1111-4111-8111-111111111111')

    def test_no_ordinary_inspection_subclass(self):
        self.assertFalse(issubclass(C.RestoreInspection, C.POLICY.Inspection))


@unittest.skipUnless(os.getresuid() == (0, 0, 0), 'actual root native protected custody fixture')
class Custody(unittest.TestCase):
    initial = False
    fixture_profile = 'none'
    patched = B.Sender.patched

    def setUp(self):
        self.patched(B, 'S', new=C.SENDER)
        B.Sender.setUp(self)
        self.patched(C, 'installed_guard')
        self.patched(A, '_boot_id', return_value=B.F.BOOT)
        p = C.BOOT_POLICY
        self.policy_value = dict(schema=1, profile=p.SCHEMA.PROFILE, target='/dev/nvme0n1',
            expected_model='Fixture SSD', expected_bytes=250000000000, storage=copy.deepcopy(B.F.STORAGE))
        config = self.fixture.state / 'config'
        config.mkdir(mode=0o700)
        leaf = config / p.NAME
        leaf.write_bytes(p.SCHEMA.canonical(self.policy_value))
        leaf.chmod(0o400)
        self.patched(p, 'STATE', new=str(self.fixture.state))
        self.patched(p.CONTEXT, 'host_identity')
        self.patched(p.MANAGEMENT, 'runtime_guard')
        self.patched(p.Policy, '_storage')
        self.patched(p.Policy, '_mount_context', return_value={'fixture': 1})
        self.stop = self.patched(C.MAINT, 'stop_managed')
        self.patched(C.MAINT, 'consumer_check')
        self.events = []
        self.patched(C, 'consumer_check', side_effect=lambda *a, **k: self.events.append('census'))

    def custody(self):
        value = C.ShutdownRestoreCustody(self.policy_value, 'one')
        self.addCleanup(value.close)
        return value

    def assert_shutdown(self):
        self.assertEqual(self.admission._read()['reason'], 'shutdown')
        self.assertEqual(self.admission._read()['state'], 'closed')
        with self.admission.shutdown_lease():
            pass

    def test_completed_cohort_actual_readonly_ex_and_progress(self):
        progress = []
        with C.ShutdownRestoreCustody(self.policy_value, 'one', progress=progress.append) as custody:
            self.assertIs(type(custody.lease), A.Lease)
            self.assertIs(type(custody.policy), C.BOOT_POLICY.Policy)
            A._held_lock(custody.lease.fd, 'WRITE', os.getpid())
            with custody.inspect('one', self.pin) as inspection:
                self.assertEqual(inspection.cohort['manifest_data'], self.fixture.manifest_path.read_bytes())
                for fd in inspection.fds.values():
                    self.assertEqual(fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE, os.O_RDONLY)
                with self.assertRaises(BlockingIOError):
                    with open(self.fixture.disk, 'rb') as disk:
                        fcntl.flock(disk, fcntl.LOCK_SH | fcntl.LOCK_NB)
                self.assertEqual(inspection.cohort['head']['status'], 'ready')
            self.assertTrue(progress)
        self.stop.assert_called_once()
        self.assert_shutdown()

    def test_existing_shutdown_observation_no_owner(self):
        self.admission.close_gate('shutdown')
        before = self.admission._read()
        with self.custody() as custody:
            self.assertFalse(hasattr(custody, 'owner'))
            self.assertEqual(self.admission._read(), before)
        self.assert_shutdown()

    def test_shared_holder_refuses_without_reset(self):
        with self.admission.admit():
            with self.assertRaises(C.CustodyError):
                self.custody()
        self.assert_shutdown()

    def test_missing_runtime_refuses(self):
        original = A.RUNTIME
        self.patched(A, 'RUNTIME', new=original + '-absent')
        with self.assertRaises(C.CustodyError):
            self.custody()
        self.assertFalse(Path(original + '-absent').exists())

    def test_mismatching_policy_refuses_before_gate(self):
        self.policy_value['expected_bytes'] += 512
        with self.assertRaisesRegex(C.CustodyError, 'policy differs'):
            self.custody()
        self.assertEqual(self.admission._read()['state'], 'open')

    def test_missing_accounts_and_marker_still_valid_cohort(self):
        Path(A.RUNTIME, 'managed', 'one.json').unlink()
        validate = module('restore_real_accounts', 'scripts/guest-accounts.py').validate_accounts
        data = dict(passwd=b'root:x:0:0:root:/root:/bin/bash\n', group=b'root:x:0:\n',
                    shadow=b'root:!:1:0:99999:7:::\n', gshadow=b'root:!::\n')
        with patch.object(C.POLICY.ACCOUNTS, 'snapshot', return_value=data), \
                patch.object(C.POLICY.ACCOUNTS, 'validate_accounts', side_effect=validate):
            with self.custody() as custody, custody.inspect('one', self.pin):
                pass

    def test_missing_managed_directory_still_valid(self):
        shutil.rmtree(Path(A.RUNTIME, 'managed'))
        with self.custody() as custody, custody.inspect('one', self.pin):
            pass

    def test_conflicting_present_account_refuses(self):
        validate = module('restore_conflict_accounts', 'scripts/guest-accounts.py').validate_accounts
        data = dict(passwd=b'other:x:60001:0:other:/:/bin/false\n', group=b'root:x:0:\n',
                    shadow=b'root:!:1:0:99999:7:::\n', gshadow=b'root:!::\n')
        with self.custody() as custody:
            with patch.object(C.POLICY.ACCOUNTS, 'snapshot', return_value=data), \
                    patch.object(C.POLICY.ACCOUNTS, 'validate_accounts', side_effect=validate):
                with self.assertRaisesRegex(C.CustodyError, 'UID collision'):
                    custody.inspect('one', self.pin)

    def test_disk_corruption_and_stale_pin_refuse(self):
        with self.custody() as custody:
            with self.assertRaisesRegex(C.CustodyError, 'pin differs'):
                custody.inspect('one', 'a' * 64)
            self.fixture.disk.write_bytes(b'Z' * 512)
            with self.assertRaisesRegex(C.CustodyError, 'hash differs'):
                custody.inspect('one', self.pin)

    def mutate_during_hash(self, change):
        with self.custody() as custody, custody.inspect('one', self.pin) as inspection:
            original = inspection._hash
            mutated = []
            def hashing(fd, size):
                result = original(fd, size)
                if not mutated:
                    mutated.append(True)
                    change(inspection)
                return result
            with patch.object(inspection, '_hash', side_effect=hashing):
                with self.assertRaisesRegex((ValueError, A.AdmissionError), 'changed|differs|replaced'):
                    inspection.recheck()
            self.assertEqual(mutated, [True])

    def test_head_mutation_during_payload_hash_refuses(self):
        def change(_inspection):
            path = self.fixture.head_path
            path.write_bytes(b'X' * path.stat().st_size)
        self.mutate_during_hash(change)

    def test_current_run_record_mutation_during_payload_hash_refuses(self):
        def change(inspection):
            path = self.fixture.head_path
            if inspection.head['sequence'] > 0:
                path = path.parent / 'runs' / (inspection.head['run_id'] + '.completed.json')
            path.write_bytes(b'X' * path.stat().st_size)
        self.mutate_during_hash(change)

    def test_lifecycle_store_replacement_during_payload_hash_refuses(self):
        def change(_inspection):
            directory = self.fixture.head_path.parent
            directory.rename(directory.with_name('retained-one'))
            directory.mkdir(mode=0o700)
        self.mutate_during_hash(change)

    def test_unsafe_file_refuses(self):
        with self.custody() as custody:
            self.fixture.disk.chmod(0o666)
            with self.assertRaisesRegex(C.CustodyError, 'metadata'):
                custody.inspect('one', self.pin)

    def add_unrelated(self):
        guest = self.fixture.root / 'two'
        shutil.copytree(self.fixture.guest, guest)
        guest.chmod(0o710)
        os.chown(guest, 0, 60002)
        manifest = C.POLICY.MANIFEST.validate_manifest(self.fixture.old_raw)
        manifest['config'].update(name='two', uid=60002, gid=60002)
        manifest['files']['disk.raw'].update(uid=60002, gid=60002)
        data = C.POLICY.MANIFEST.canonical(manifest)
        (guest / 'enrollment.json').write_bytes(data)
        (guest / 'disk.raw').write_bytes(b'A' * 512)
        os.chown(guest / 'disk.raw', 60002, 60002)
        if (guest / '.checkpoints').exists():
            shutil.rmtree(guest / '.checkpoints')
        pin = C.CP.digest(data)
        directory = self.fixture.head_path.parent.parent / 'two'
        directory.mkdir(mode=0o700)
        head = C.HEAD.initial_head(B.F.STORAGE, dict(name='two', uid=60002, gid=60002), pin)
        (directory / 'head.json').write_bytes(C.HEAD.canonical(head))
        (directory / 'head.json').chmod(0o400)
        registry = self.fixture.registry()
        entry = copy.deepcopy(registry['guests'][0])
        entry.update(name='two', uid=60002, gid=60002, manifest_sha256=pin)
        registry['guests'].append(entry)
        self.fixture.registry_path.write_bytes(C.REGISTRY.canonical(registry))
        with self.admission.admit() as lease:
            with self.admission.guest_lock('two', lease, create=True):
                pass
        return guest, pin

    def test_unrelated_initial_without_ram_marker_passes(self):
        guest, pin = self.add_unrelated()
        with self.custody() as custody:
            with custody.inspect('two', pin):
                pass
        self.assertTrue((guest / 'disk.raw').exists())

    def test_corrupt_unrelated_refuses_before_target_use(self):
        guest, _ = self.add_unrelated()
        (guest / 'disk.raw').write_bytes(b'Z' * 512)
        before = self.fixture.disk.read_bytes()
        with self.assertRaisesRegex(C.CustodyError, 'hash differs'):
            self.custody()
        self.assertEqual(self.fixture.disk.read_bytes(), before)
        self.assert_shutdown()

    def test_old_corrupt_target_is_observation_only(self):
        self.fixture.disk.write_bytes(b'Z' * 512)
        self.fixture.manifest_path.write_bytes(b'broken')
        self.fixture.head_path.write_bytes(b'broken')
        with self.custody() as custody, custody.snapshot_target() as snapshot:
            self.assertEqual(snapshot.observation['payload']['files']['disk.raw']['sha256'],
                             C.CP.digest(b'Z' * 512))
            self.assertTrue(snapshot.observation['lifecycle'])
            self.assertEqual(self.fixture.manifest_path.read_bytes(), b'broken')

    def test_absent_old_payload_and_head(self):
        shutil.rmtree(self.fixture.guest)
        shutil.rmtree(self.fixture.head_path.parent)
        with self.custody() as custody, custody.snapshot_target() as snapshot:
            self.assertIsNone(snapshot.observation['payload'])
            self.assertIsNone(snapshot.observation['lifecycle'])

    def test_old_symlink_refuses(self):
        self.fixture.disk.unlink()
        self.fixture.disk.symlink_to('/dev/null')
        with self.custody() as custody:
            with self.assertRaisesRegex(C.CustodyError, 'unsafe old target file'):
                custody.snapshot_target()

    def test_old_disk_shared_lock_refuses(self):
        with open(self.fixture.disk, 'rb') as disk:
            fcntl.flock(disk, fcntl.LOCK_SH | fcntl.LOCK_NB)
            with self.custody() as custody:
                with self.assertRaises(C.CustodyError):
                    custody.snapshot_target()

    def test_thread_and_fork_identity_refuse(self):
        custody = self.custody()
        errors = []
        def thread():
            try:
                custody.recheck()
            except BaseException as error:
                errors.append(error)
        worker = threading.Thread(target=thread)
        worker.start()
        worker.join()
        self.assertEqual(len(errors), 1)
        pid = os.fork()
        if pid == 0:
            try:
                custody.recheck()
            except BaseException:
                os._exit(0)
            os._exit(1)
        self.assertEqual(os.waitpid(pid, 0)[1], 0)

    def test_partial_construction_cleanup_and_primary_receipt(self):
        with patch.object(C.LIFECYCLE, 'StateController', side_effect=ValueError('physical failed')):
            with self.assertRaisesRegex(C.CustodyError, 'physical failed') as caught:
                self.custody()
        self.assertFalse(caught.exception.receipt['cleanup_uncertain'])
        self.assert_shutdown()

    def test_primary_and_multiple_cleanup_errors_preserved(self):
        custody = self.custody()
        real = C.os.close
        target = custody.policy._policy_fd
        def fail(fd):
            real(fd)
            if fd == target:
                raise OSError('policy-close')
        self.patched(C.os, 'close', side_effect=fail)
        with self.assertRaises(C.CustodyError) as caught:
            with custody:
                raise ValueError('primary')
        self.assertIn('primary', caught.exception.receipt['primary_error'])
        self.assertTrue(caught.exception.receipt['cleanup_uncertain'])
        self.assertIn('policy-close', str(caught.exception.receipt['close_errors']))
        self.assert_shutdown()

    def test_close_children_before_lease(self):
        custody = self.custody()
        inspection = custody.inspect('one', self.pin)
        custody.close()
        self.assertTrue(inspection._closed)
        self.assert_shutdown()


class Initial(Custody):
    initial = True


class Census(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.proc = Path(temp.name)
        self.task = self.proc / '34567' / 'task' / '34568'
        self.task.mkdir(parents=True)
        (self.task / 'fd').mkdir()
        (self.task / 'status').write_text('Uid:\t0\t0\t0\t0\n')
        (self.task / 'maps').write_text('')
        self.target = self.proc / 'target'
        self.target.write_bytes(b'target')
        self.identity = C.ENROLL.inode(self.target.stat())

    def scan(self):
        return C.consumer_check(60001, [self.identity], proc_root=self.proc)

    def test_all_task_fd(self):
        (self.task / 'fd' / '5').symlink_to(self.target)
        with self.assertRaisesRegex(A.AdmissionError, 'task consumer'):
            self.scan()

    def test_all_task_mapping(self):
        device, inode = self.identity
        (self.task / 'maps').write_text(f'1000-2000 rw-s 0000 {os.major(device):02x}:{os.minor(device):02x} {inode} target\n')
        with self.assertRaisesRegex(A.AdmissionError, 'mapped consumer'):
            self.scan()

    def test_directory_cwd_root_exe(self):
        for name in ('cwd', 'root', 'exe'):
            link = self.task / name
            link.symlink_to(self.target)
            with self.assertRaisesRegex(A.AdmissionError, 'task consumer'):
                self.scan()
            link.unlink()

    def test_remaining_uid_without_reference(self):
        (self.task / 'status').write_text('Uid:\t60001\t60001\t60001\t60001\n')
        with self.assertRaisesRegex(A.AdmissionError, 'UID task'):
            self.scan()

    @unittest.skipUnless(os.getresuid() == (0, 0, 0), 'native root proc reference check')
    def test_actual_same_uid_fd_and_directory_consumers(self):
        # Kernel task directories are exposed through a bounded fixture census
        # root. No status, FD, maps or cwd observation is substituted.
        for directory in (False, True):
            target = self.proc if directory else self.target
            fd = os.open(target, os.O_RDONLY | (os.O_DIRECTORY if directory else 0))
            ready_r, ready_w = os.pipe()
            hold_r, hold_w = os.pipe()
            code = ('import os; os.setuid(60001); '
                    f'os.write({ready_w}, b"r"); os.read({hold_r}, 1)')
            process = subprocess.Popen(['/usr/bin/python3', '-c', code],
                pass_fds=(fd, ready_w, hold_r), stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                os.close(ready_w)
                os.close(hold_r)
                self.assertEqual(os.read(ready_r, 1), b'r')
                census = self.proc / 'native'
                census.mkdir()
                (census / str(process.pid)).symlink_to('/proc/' + str(process.pid))
                with self.assertRaisesRegex(A.AdmissionError, 'target UID task'):
                    C.consumer_check(60001, [C.ENROLL.inode(os.fstat(fd))], proc_root=census)
                shutil.rmtree(census)
            finally:
                os.close(hold_w)
                os.close(ready_r)
                os.close(fd)
                process.wait(timeout=5)

    def self_census(self):
        census = self.proc / 'self-census'
        census.mkdir()
        (census / str(os.getpid())).symlink_to('/proc/' + str(os.getpid()))
        return census

    def test_exact_retained_readonly_self_descriptor_passes(self):
        census = self.self_census()
        fd = os.open(self.target, os.O_RDONLY)
        try:
            C.consumer_check(60001, [self.identity], retained_fds=[fd], proc_root=census)
        finally:
            os.close(fd)

    def test_unowned_self_writable_descriptor_refuses(self):
        census = self.self_census()
        fd = os.open(self.target, os.O_RDONLY)
        extra = os.open(self.target, os.O_RDWR)
        try:
            with self.assertRaisesRegex(A.AdmissionError, 'task consumer'):
                C.consumer_check(60001, [self.identity], retained_fds=[fd], proc_root=census)
            with self.assertRaisesRegex(A.AdmissionError, 'readonly'):
                C.consumer_check(60001, [self.identity], retained_fds=[extra], proc_root=census)
        finally:
            os.close(extra)
            os.close(fd)

    def test_unowned_self_directory_duplicate_refuses(self):
        census = self.self_census()
        fd = os.open(self.proc, os.O_RDONLY | os.O_DIRECTORY)
        extra = os.dup(fd)
        identity = C.ENROLL.inode(os.fstat(fd))
        try:
            with self.assertRaisesRegex(A.AdmissionError, 'task consumer'):
                C.consumer_check(60001, [identity], retained_fds=[fd], proc_root=census)
        finally:
            os.close(extra)
            os.close(fd)

    def test_self_mapping_refuses_even_with_exact_readonly_descriptors(self):
        census = self.self_census()
        fd = os.open(self.target, os.O_RDONLY)
        mapping = mmap.mmap(fd, 0, access=mmap.ACCESS_READ)
        try:
            # Python mmap retains an internal readonly duplicate. Explicitly
            # exempt both FD numbers to isolate rejection of the actual mapping.
            retained = []
            for leaf in Path('/proc/self/fd').iterdir():
                try:
                    if C.ENROLL.inode(leaf.stat()) == self.identity:
                        retained.append(int(leaf.name))
                except FileNotFoundError:
                    pass
            with self.assertRaisesRegex(A.AdmissionError, 'mapped consumer'):
                C.consumer_check(60001, [self.identity], retained_fds=retained, proc_root=census)
        finally:
            mapping.close()
            os.close(fd)

    def test_bounded_malformed_task(self):
        (self.task / 'status').write_text('Uid:\t0\t0\n')
        with self.assertRaisesRegex(A.AdmissionError, 'UID observation'):
            self.scan()
