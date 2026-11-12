"""Native bootstrap EX/protected discovery; explicit physical/sysusers fixtures.

No real /etc account reads/writes or hardware acceptance are performed.
"""
import copy
import fcntl
import importlib.util
import os
from pathlib import Path
import shlex
import tempfile
import threading
import unittest
import uuid
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('boot_accounts_tests', BASE / 'scripts/guest-boot-accounts.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
A = M.ADMISSION
STORAGE = dict(state_uuid='22222222-2222-4222-8222-222222222222',
               guest_uuid='33333333-3333-4333-8333-333333333333', nvme_serial='SSD-123')


class Boundary(unittest.TestCase):
    def test_fake_owner(self):
        with self.assertRaisesRegex(M.PreparationError, 'canonical bootstrap owner'):
            M.prepare(object(), STORAGE)


@unittest.skipUnless(os.getresuid() == (0, 0, 0), 'native protected fixture requires root')
class Native(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir='/run', prefix='a733-boot-accounts-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.state, self.guests = self.base / 'state', self.base / 'guests'
        self.state.mkdir(mode=0o700)
        self.guests.mkdir(mode=0o711)
        (self.state / 'guests').mkdir(mode=0o700)
        self.registry_path = self.state / 'guests/autostart.json'
        self.boot = str(uuid.uuid4())
        self.closed = self.executions = 0
        self.bad_context = self.bad_cleanup = False
        fixture = self

        class PhysicalStateFixture:
            def __init__(self, expected_storage, lease, *, boot_id):
                fixture.assertEqual(expected_storage, STORAGE)
                fixture.assertEqual(boot_id, fixture.boot)
                self.storage = copy.deepcopy(expected_storage)
                self.lease = lease
                self.state_fd = os.open(fixture.state, os.O_RDONLY | os.O_DIRECTORY)
                self.guest_fd = os.open(fixture.guests, os.O_RDONLY | os.O_DIRECTORY)
            def guard(self):
                M.require(not fixture.bad_context, 'physical placement changed')
                self.lease.recheck()
            def close(self):
                fixture.closed += 1
                os.close(self.state_fd)
                os.close(self.guest_fd)
                if fixture.bad_cleanup:
                    raise OSError('cleanup failed')

        self.databases = dict(passwd=b'root:x:0:0:root:/root:/bin/sh\n',
                              shadow=b'root:!:0:0:99999:7:::\n',
                              group=b'root:x:0:\n', gshadow=b'root:!::\n')
        for substitute in (patch.object(A, 'RUNTIME_PARENT', self.tmp.name),
                           patch.object(A, 'RUNTIME', str(self.base / 'a733-admission')),
                           patch.object(M.CONTEXT, 'HostContext', PhysicalStateFixture),
                           patch.object(M.CONTEXT, 'host_identity'),
                           patch.object(M.ACCOUNTS, '_BOOTSTRAP_CONTEXT', M.CONTEXT),
                           patch.object(M.ACCOUNTS.MANAGEMENT, 'runtime_guard'),
                           patch.object(M.ACCOUNTS, 'LOCK', str(self.base / 'accounts.lock')),
                           patch.object(M.ACCOUNTS, 'snapshot', side_effect=lambda: copy.deepcopy(self.databases)),
                           patch.object(M.ACCOUNTS.subprocess, 'run', side_effect=self.sysusers)):
            substitute.start()
            self.addCleanup(substitute.stop)
        self.admission = A.initialize(self.boot, 108, lambda: 'running')
        self.addCleanup(self.admission.close)
        self.owner = self.admission.bootstrap_owner
        self.registry()

    def sysusers(self, command, **kwargs):
        self.assertEqual(command[:2], ['/usr/bin/systemd-sysusers', '--inline'])
        self.executions += 1
        for line in command[2:]:
            fields = shlex.split(line)
            name = fields[1]
            if fields[0] == 'g':
                self.databases['group'] += f'{name}:x:{fields[2]}:\n'.encode()
                self.databases['gshadow'] += f'{name}:!::\n'.encode()
            else:
                uid, gid = fields[2].split(':')
                self.databases['passwd'] += f'{name}:x:{uid}:{gid}:{fields[3]}:{fields[4]}:{fields[5]}\n'.encode()
                self.databases['shadow'] += f'{name}:!:0:0:99999:7:::\n'.encode()

    def registry(self, entries=(), storage=None):
        value = dict(schema=1, profile=M.REGISTRY.PROFILE, generation=9,
                     storage=storage or STORAGE, guests=list(entries))
        raw = M.REGISTRY.canonical(value)
        self.registry_path.chmod(0o600) if self.registry_path.exists() else None
        self.registry_path.write_bytes(raw)
        self.registry_path.chmod(0o400)
        return raw

    def enrollment(self, name='firstkvm', uid=60001, managed=False):
        config = dict(schema=1, name=name, uid=uid, gid=uid, memory_mib=128,
                      memory_max_mib=256, cpus=1, kernel=dict(bytes=1, sha256='a'*64),
                      initrd=dict(bytes=1, sha256='b'*64), disk=dict(bytes=512, sha256='c'*64))
        files = {filename: dict(**config[key], uid=uid if key == 'disk' else 0,
                               gid=uid if key == 'disk' else 0,
                               mode='0600' if key == 'disk' else '0444')
                 for key, filename in M.MANIFEST.FILENAMES.items()}
        raw = M.MANIFEST.canonical(dict(schema=1, profile=M.MANIFEST.PROFILE,
                                      config=config, guest_uuid=STORAGE['guest_uuid'], files=files))
        directory = self.guests / name
        directory.mkdir(mode=0o710)
        os.chown(directory, 0, uid)
        (directory / 'enrollment.json').write_bytes(raw)
        (directory / 'enrollment.json').chmod(0o400)
        return dict(name=name, uid=uid, gid=uid, manifest_sha256=M.digest(raw),
                    enabled=managed, shutdown_profile=M.REGISTRY.SHUTDOWN_PROFILE,
                    fixture_profile='none')

    def prepare(self):
        result = M.prepare(self.owner, STORAGE)
        self.assertEqual(self.admission._read()['state'], 'closed')
        self.owner.lease.recheck()
        self.assertIs(self.admission.bootstrap_owner, self.owner)
        return result

    def failure(self, message):
        with self.assertRaisesRegex(M.PreparationError, message) as error:
            self.prepare()
        self.assertFalse(error.exception.receipt['success'])
        self.assertEqual(self.admission._read()['state'], 'closed')
        return error.exception

    def test_manual_and_disabled_managed_no_launch_authority(self):
        self.enrollment()
        managed = self.enrollment('managed', 60002)
        raw = self.registry([managed])
        for name in ('.a733-lifecycle', 'lost+found', 'partial-work'):
            (self.guests / name).mkdir(mode=0o700)
        result = self.prepare()
        self.assertEqual([x['name'] for x in result['identities']], ['firstkvm', 'managed'])
        self.assertEqual(self.executions, 1)
        self.assertFalse(result['run_permission'])
        self.assertFalse(result['hardware_acceptance'])
        self.assertEqual(self.registry_path.read_bytes(), raw)
        self.assertFalse((self.base / 'a733-admission/managed').exists())
        self.prepare()
        self.assertEqual(self.executions, 1)
        self.assertEqual(self.closed, 2)

    def test_32_fresh_batches_preserve_prior_rows(self):
        for i in range(32):
            self.enrollment(f'guest{i:02}', 60000+i)
        original = copy.deepcopy(self.databases)
        result = self.prepare()
        self.assertEqual([len(b['names']) for b in result['batches']], [16, 16])
        self.assertEqual(self.executions, 2)
        for name in self.databases:
            self.assertTrue(self.databases[name].startswith(original[name]))
        self.assertIn(b'a733-vm-guest00:', self.databases['passwd'])
        self.assertIn(b'a733-vm-guest31:', self.databases['passwd'])

    def test_cross_batch_duplicate_refused_before_any_writes(self):
        for i in range(17):
            self.enrollment(f'guest{i:02}', 60000+i if i < 16 else 60000)
        self.failure('duplicate guest UID/GID')
        self.assertEqual(self.executions, 0)

    def test_missing_registry_refused_without_creation(self):
        self.registry_path.unlink()
        self.failure('prepared registry required')
        self.assertFalse(self.registry_path.exists())

    def test_empty_registry_and_no_enrollments(self):
        self.assertEqual(self.prepare()['batches'], [])
        self.assertEqual(self.executions, 0)

    def test_reserved_manual_identity_cannot_be_registered(self):
        self.registry([self.enrollment()])
        self.failure('manual firstkvm cannot be managed')
        self.assertEqual(self.executions, 0)

    def test_unsafe_root_objects(self):
        for kind in ('symlink', 'file', 'exposed-hidden', 'bad-mode'):
            with self.subTest(kind=kind):
                path = self.guests / ('.hidden' if kind == 'exposed-hidden' else 'bad')
                if kind == 'symlink':
                    path.symlink_to(self.state, target_is_directory=True)
                elif kind == 'file':
                    path.write_bytes(b'bad')
                else:
                    path.mkdir(mode=0o710 if kind == 'exposed-hidden' else 0o755)
                self.failure('unsafe guest root child|hidden exposed|unsafe exposed')
                path.rmdir() if path.is_dir() and not path.is_symlink() else path.unlink()
        self.assertEqual(self.executions, 0)

    def test_manifest_unsafe_and_gid_mismatch(self):
        self.enrollment()
        path = self.guests / 'firstkvm/enrollment.json'
        path.chmod(0o444)
        self.failure('unsafe or oversized record')
        path.chmod(0o400)
        os.chown(path.parent, 0, 60002)
        self.failure('enrollment directory identity differs')
        self.assertEqual(self.executions, 0)

    def test_manifest_symlink_hardlink_and_foreign_uuid(self):
        self.enrollment()
        path = self.guests / 'firstkvm/enrollment.json'
        raw = path.read_bytes()
        other = self.base / 'record'
        path.rename(other)
        path.symlink_to(other)
        self.failure('unsafe or oversized record')
        path.unlink()
        os.link(other, path)
        self.failure('unsafe or oversized record')
        path.unlink()
        other.unlink()
        value = M.MANIFEST.validate_manifest(raw)
        value['guest_uuid'] = str(uuid.uuid4())
        path.write_bytes(M.MANIFEST.canonical(value))
        path.chmod(0o400)
        self.failure('enrollment guest UUID differs')
        self.assertEqual(self.executions, 0)

    def test_database_collision_refused_by_real_planner(self):
        self.enrollment()
        self.databases['passwd'] += b'other:x:60001:100:Other:/nonexistent:/bin/false\n'
        self.failure('UID collision')
        self.assertEqual(self.executions, 0)

    def test_registry_pin_missing_and_storage(self):
        entry = self.enrollment('managed')
        entry['manifest_sha256'] = 'f'*64
        self.registry([entry])
        self.failure('registry/enrollment identity or pin differs')
        self.registry([dict(entry, name='absent')])
        self.failure('registered enrollment missing')
        self.registry(storage={**STORAGE, 'nvme_serial': 'other'})
        self.failure('storage identity differs')
        self.assertEqual(self.executions, 0)

    def test_registry_same_bytes_replacement_detected(self):
        self.enrollment()
        make_plan = M.ACCOUNTS.make_plan
        def replace(*args):
            plan = make_plan(*args)
            raw = self.registry_path.read_bytes()
            self.registry_path.unlink()
            self.registry_path.write_bytes(raw)
            self.registry_path.chmod(0o400)
            return plan
        with patch.object(M.ACCOUNTS, 'make_plan', side_effect=replace):
            self.failure('protected metadata changed')
        self.assertEqual(self.executions, 0)

    def test_manifest_content_mutation_after_plan_refused(self):
        self.enrollment()
        path = self.guests / 'firstkvm/enrollment.json'
        make_plan = M.ACCOUNTS.make_plan
        def mutate(*args):
            plan = make_plan(*args)
            path.chmod(0o600)
            path.write_bytes(path.read_bytes().replace(b'"cpus":1', b'"cpus":2'))
            path.chmod(0o400)
            return plan
        with patch.object(M.ACCOUNTS, 'make_plan', side_effect=mutate):
            self.failure('protected metadata changed|guest root namespace changed')
        self.assertEqual(self.executions, 0)

    def test_budget_and_name_bound(self):
        self.enrollment()
        with patch.object(M, 'METADATA_LIMIT', 10):
            self.failure('metadata limit exceeded')
        with patch.object(M, 'NAME_LIMIT', 0):
            self.failure('entry limit exceeded')
        self.assertEqual(self.executions, 0)

    def test_batch_failure_preserves_progress_and_primary_receipt(self):
        for i in range(17):
            self.enrollment(f'guest{i:02}', 60000+i)
        apply = M.ACCOUNTS.apply_bootstrap
        calls = []
        def failing(data, plan, owner):
            calls.append(data)
            if len(calls) == 2:
                self.bad_cleanup = True
                return dict(success=False, accounts_may_be_modified=True, error='injected')
            return apply(data, plan, owner)
        with patch.object(M.ACCOUNTS, 'apply_bootstrap', side_effect=failing):
            error = self.failure('account batch failed')
        self.assertEqual(self.executions, 1)
        self.assertTrue(error.receipt['batches'][0]['receipt']['success'])
        self.assertTrue(error.receipt['batches'][1]['receipt']['accounts_may_be_modified'])
        self.assertEqual(len(error.receipt['cleanup_errors']), 1)
        self.assertEqual(self.closed, 1)

    def test_foreign_owner_and_bad_lease_refused(self):
        foreign = A.ClosureOwner(self.admission, self.admission._read())
        with self.assertRaisesRegex(M.PreparationError, 'original bootstrap owner'):
            M.prepare(foreign, STORAGE)
        self.owner.reason = 'maintenance'
        self.failure('bootstrap ownership differs')
        self.owner.reason = 'bootstrap'
        fcntl.flock(self.owner.lease.fd, fcntl.LOCK_UN)
        self.failure('missing or invalid inherited flock')
        fcntl.flock(self.owner.lease.fd, fcntl.LOCK_EX)
        self.owner.lease.exclusive = False
        self.failure('original exclusive bootstrap lease')
        self.owner.lease.exclusive = True
        self.assertEqual(self.closed, 0)

    def test_fork_cannot_borrow_parent_owned_flock(self):
        read_fd, write_fd = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.close(read_fd)
            try:
                M.prepare(self.owner, STORAGE)
            except M.PreparationError as error:
                os.write(write_fd, str(error).encode()[:2048])
            finally:
                os._exit(0)
        os.close(write_fd)
        try:
            message = os.read(read_fd, 2048).decode()
            _, status = os.waitpid(pid, 0)
        finally:
            os.close(read_fd)
        self.assertEqual(status, 0)
        self.assertRegex(message, 'flock identity or mode differs')
        self.owner.lease.recheck()
        self.assertEqual(self.closed, 0)

    def test_changed_actual_pid_and_source_refused(self):
        actual_pid = os.getpid()
        with patch.object(M.os, 'getpid', return_value=actual_pid+10000):
            self.failure('flock identity or mode differs')
        with patch.object(A, '_a733_source_sha256', '0'*64):
            self.failure('admission module origin or source revision differs')
        self.assertEqual(self.executions, 0)

    def test_noncanonical_registry_and_registered_private_refused(self):
        self.registry_path.chmod(0o600)
        self.registry_path.write_bytes(self.registry_path.read_bytes() + b'\n')
        self.registry_path.chmod(0o400)
        self.failure('noncanonical registry')
        entry = self.enrollment('managed')
        self.registry([entry])
        directory = self.guests / 'managed'
        os.chown(directory, 0, 0)
        directory.chmod(0o700)
        self.failure('registered guest directory is private')
        self.assertEqual(self.executions, 0)

    def test_nested_receipt_and_descriptor_cleanup(self):
        self.enrollment()
        before = len(os.listdir('/proc/self/fd'))
        class LostAck(RuntimeError):
            receipt = dict(uncertain=True, stage='injected')
        with patch.object(M.ACCOUNTS, 'apply_bootstrap', side_effect=LostAck('lost ack')):
            error = self.failure('lost ack')
        self.assertEqual(error.receipt['nested_error'], LostAck.receipt)
        self.assertEqual(len(error.receipt['batches']), 1)
        self.assertTrue(error.receipt['batches'][0]['attempted'])
        self.assertEqual(len(os.listdir('/proc/self/fd')), before)
        self.assertEqual(self.closed, 1)

    def test_pid_thread_and_physical_guard_refuse(self):
        self.enrollment()
        errors = []
        make_plan = M.ACCOUNTS.make_plan
        def callback(*args):
            def run():
                try:
                    # An existing retained record's guard is exercised by the
                    # physical controller's store check in another thread.
                    guard = captured[0]
                    guard()
                except BaseException as error:
                    errors.append(error)
            thread = threading.Thread(target=run)
            thread.start()
            thread.join()
            return make_plan(*args)
        captured = []
        original = M.STORE.RootDirectory.__init__
        def capture(store, *args, **kwargs):
            captured.append(kwargs['guard'])
            original(store, *args, **kwargs)
        with patch.object(M.STORE.RootDirectory, '__init__', capture), \
                patch.object(M.ACCOUNTS, 'make_plan', side_effect=callback):
            self.prepare()
        self.assertTrue(errors)
        self.assertTrue(all('another process or thread' in str(e) for e in errors))
        self.bad_context = True
        self.failure('physical placement changed')

    def test_open_owner_and_unacceptable_pid1_refuse(self):
        self.admission.manager_state = lambda: 'stopping'
        self.failure('PID1 not accepting bootstrap')
        self.admission.manager_state = lambda: 'running'
        self.owner.open()
        with self.assertRaisesRegex(M.PreparationError, 'closure ownership changed'):
            M.prepare(self.owner, STORAGE)
        self.assertEqual(self.closed, 0)


if __name__ == '__main__':
    unittest.main()
