"""Managed sender: native protected records/flocks, explicit platform producers.

The checkpoint fixture simulates live shutdown and physical host placement.
Sender admission and guest/disk exclusion use actual canonical capability types.
These host tests establish no KVM, restoration or hardware acceptance.
"""
import copy
import gzip
import importlib.util
import io
import json
import os
from pathlib import Path
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


S = module('managed_backup_sender_test', 'scripts/backup-managed-guest.py')
F = module('managed_backup_checkpoint_fixture', 'tests/test_managed_guest_checkpoint.py')
A = S.ADMISSION


@unittest.skipUnless(os.getresuid() == (0, 0, 0), 'protected native fixture requires root')
class Sender:
    initial = False
    fixture_profile = 'none'

    def patched(self, target, name, **kwargs):
        mocked = patch.object(target, name, **kwargs)
        value = mocked.start()
        self.addCleanup(mocked.stop)
        return value

    def setUp(self):
        actual_lease, actual_guest = A.Lease, A.GuestLock
        self.patched(F, 'M', new=S.CP)
        self.patched(F, 'P', new=S.POLICY)
        self.patched(F, 'L', new=S.LIFECYCLE)
        self.fixture = F.ManagedCheckpoint(methodName='runTest')
        self.fixture.fixture_profile = self.fixture_profile
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        if self._testMethodName == 'test_zero_length_console_supported':
            outcome = self.fixture.outcome
            original = outcome.recheck
            outcome._console_data = b''
            def empty_console():
                evidence = original()
                evidence.update(console_bytes=0, console_sha256=S.CP.digest(b''))
                return evidence
            outcome.recheck = empty_console
        if self.initial:
            # Reuse the actual protected enrollment, before its simulated run.
            self.fixture.run.close()
            head = S.HEAD.initial_head(F.STORAGE, dict(name='one', uid=60001, gid=60001), self.fixture.pin)
            self.fixture.head_path.write_bytes(S.HEAD.canonical(head))
            self.fixture.disk.write_bytes(b'A' * 512)
        else:
            self.assertTrue(self.fixture.publish()['success'])
            self.fixture.run.close()
        self.pin = S.CP.digest(self.fixture.manifest_path.read_bytes())
        # Restore native same-family types after simulated live production.
        self.patched(A, 'Lease', new=actual_lease)
        self.patched(A, 'GuestLock', new=actual_guest)
        self.runtime = tempfile.TemporaryDirectory(dir='/run', prefix='managed-sender-admission-')
        self.addCleanup(self.runtime.cleanup)
        self.patched(A, 'RUNTIME_PARENT', new=self.runtime.name)
        self.patched(A, 'RUNTIME', new=self.runtime.name + '/a733-admission')
        self.patched(S.CONTEXT, 'read_system', return_value=(F.BOOT + '\n').encode())
        self.patched(S.CONTEXT, 'host_identity')
        self.patched(S.CONTEXT.MANAGEMENT, 'runtime_guard')
        self.patched(S.MAINT, 'registered_storage', return_value=copy.deepcopy(F.STORAGE))
        self.patched(S.MAINT.NORMAL, 'local_kvm_gid', return_value=108)
        self.patched(S.MAINT.NORMAL, 'manager_state', return_value='running')
        self.admission = A.initialize(F.BOOT, 108, lambda: 'running')
        self.addCleanup(self.admission.close)
        owner = self.admission.bootstrap_owner
        self.admission.prepare_managed(owner.lease)
        with self.admission.guest_lock('one', owner.lease, create=True) as lock:
            self.admission.mark_managed('one', owner.lease, lock, guard=lambda: None)
        owner.open()
        owner.lease.close()

    def send(self, writer=None):
        writer = io.BytesIO() if writer is None else writer
        return S.send('one', self.pin, writer), writer

    def members(self, data):
        stream = io.BytesIO(gzip.decompress(data))
        result = {}
        for name in S.FORMAT.NAMES:
            size = S.FORMAT.read_header(stream, name)
            result[name] = S.FORMAT.read_exact(stream, size)
            S.FORMAT.read_padding(stream, size)
        S.FORMAT.read_trailer(stream)
        return result

    def assert_released(self):
        with self.admission.admit() as lease:
            with self.admission.guest_lock('one', lease):
                pass
        # Actual stopped disk flock must also have been released.
        with S.POLICY.Inspection('one', self.pin):
            pass

    def test_archive_exact_bytes_and_native_lifetime(self):
        source_type = S.Source
        opened = []
        def source(name, pin):
            value = source_type(name, pin)
            opened.append(value)
            return value
        fixture = self
        class Writer(io.BytesIO):
            def write(self, data):
                value = opened[0]
                fixture.assertIs(type(value.lease), A.Lease)
                fixture.assertFalse(value.lease.exclusive)
                A._held_lock(value.lease.fd, 'READ', os.getpid())
                A._held_lock(value.lock.fd, 'WRITE', os.getpid())
                # Registry EX is never retained during streaming.
                with fixture.admission.admit() as lease:
                    with fixture.admission.registry_lock(lease):
                        pass
                return super().write(data)
        with patch.object(S, 'Source', side_effect=source):
            receipt, writer = self.send(Writer())
        data = writer.getvalue()
        members = self.members(data)
        self.assertEqual(members['enrollment.json'], self.fixture.manifest_path.read_bytes())
        self.assertEqual(members['disk.raw'], self.fixture.disk.read_bytes())
        lifecycle = S.FORMAT.validate_lifecycle(members['lifecycle.json'], members['enrollment.json'])
        self.assertEqual(len(lifecycle['records']), 0 if self.initial else 10)
        self.assertEqual(receipt['archive'], S.CP.reference(data))
        self.assertFalse(receipt['hardware_acceptance'])
        self.assertTrue(opened[0].closed)
        self.assert_released()

    def test_missing_marker_emits_nothing(self):
        Path(A.RUNTIME, 'managed', 'one.json').unlink()
        writer = io.BytesIO()
        with self.assertRaises((ValueError, A.AdmissionError)):
            self.send(writer)
        self.assertEqual(writer.getvalue(), b'')
        self.assert_released()

    def test_missing_registry_entry_emits_nothing(self):
        registry = self.fixture.registry()
        registry['guests'] = []
        self.fixture.registry_path.write_bytes(S.REGISTRY.canonical(registry))
        writer = io.BytesIO()
        with self.assertRaises(ValueError):
            self.send(writer)
        self.assertEqual(writer.getvalue(), b'')
        self.assert_released()

    def test_wrong_trusted_pin_emits_nothing(self):
        writer = io.BytesIO()
        with self.assertRaises(ValueError):
            S.send('one', 'a' * 64, writer)
        self.assertEqual(writer.getvalue(), b'')
        self.assert_released()

    def test_short_writes_hash_only_accepted_bytes(self):
        class Writer(io.BytesIO):
            def write(self, data):
                return super().write(data[:7])
        receipt, writer = self.send(Writer())
        self.assertEqual(receipt['archive'], S.CP.reference(writer.getvalue()))
        self.members(writer.getvalue())
        self.assert_released()

    def test_zero_write_refuses_and_releases(self):
        class Writer(io.BytesIO):
            def write(self, data):
                return 0
        with self.assertRaises(ValueError):
            self.send(Writer())
        self.assert_released()

    def mutate_during_stream(self, change, *, accepted=False):
        fixture = self
        class Writer(io.BytesIO):
            changed = False
            def write(self, data):
                if not self.changed:
                    self.changed = True
                    change()
                return super().write(data)
        writer = Writer()
        if accepted:
            receipt, _ = self.send(writer)
            self.assertTrue(receipt['success'])
        else:
            with self.assertRaises((ValueError, A.AdmissionError, S.LIFECYCLE.OperationError)):
                self.send(writer)
        self.assertTrue(writer.getvalue())
        return writer

    def test_late_disk_mutation_refuses(self):
        self.mutate_during_stream(lambda: self.fixture.disk.write_bytes(b'Z' * 512))

    def update_registry(self, change):
        with self.admission.admit() as lease:
            with self.admission.registry_lock(lease):
                registry = self.fixture.registry()
                registry['generation'] += 1
                change(registry)
                self.fixture.registry_path.write_bytes(S.REGISTRY.canonical(registry))

    def test_current_disable_preserved_and_snapshot_historical(self):
        old_raw = self.fixture.registry_path.read_bytes()
        writer = self.mutate_during_stream(lambda: self.update_registry(
            lambda r: r['guests'][0].update(enabled=False)), accepted=True)
        self.assertFalse(self.fixture.registry()['guests'][0]['enabled'])
        envelope = self.members(writer.getvalue())['lifecycle.json']
        lifecycle = S.FORMAT.validate_lifecycle(envelope, self.fixture.manifest_path.read_bytes())
        self.assertEqual(S.CP.decoded(lifecycle['registry']), old_raw)
        self.assert_released()

    def test_late_profile_change_refuses(self):
        self.mutate_during_stream(lambda: self.update_registry(
            lambda r: r['guests'][0].update(fixture_profile='managed-power-key-v1')))
        self.assert_released()

    def test_unrelated_entry_update_allowed(self):
        def unrelated(registry):
            entry = copy.deepcopy(registry['guests'][0])
            entry.update(name='two', uid=60002, gid=60002)
            registry['guests'].append(entry)
        self.mutate_during_stream(lambda: self.update_registry(unrelated), accepted=True)
        self.assertEqual(len(self.fixture.registry()['guests']), 2)
        self.assert_released()

    def test_cleanup_error_refuses_success_and_releases(self):
        real_close = S.LEGACY.History.close
        def failing_close(history):
            real_close(history)
            raise OSError('injected close acknowledgement failure')
        with patch.object(S.LEGACY.History, 'close', failing_close):
            with self.assertRaisesRegex(OSError, 'acknowledgement'):
                self.send()
        self.assert_released()


class Completed(Sender, unittest.TestCase):
    def test_missing_completion_emits_nothing(self):
        (self.fixture.runs / (F.RUN + '.completed.json')).unlink()
        writer = io.BytesIO()
        with self.assertRaises((OSError, S.LIFECYCLE.OperationError)):
            self.send(writer)
        self.assertEqual(writer.getvalue(), b'')
        self.assert_released()

    def test_tampered_console_emits_nothing(self):
        (self.fixture.runs / (F.RUN + '.console')).write_bytes(b'forged console')
        writer = io.BytesIO()
        with patch.object(S.FORMAT, 'canonical', return_value=b'{}') as inert:
            with self.assertRaises(S.LIFECYCLE.OperationError):
                self.send(writer)
            inert.assert_not_called()
        self.assertEqual(writer.getvalue(), b'')
        self.assert_released()

    def test_late_protected_record_replacement_refuses(self):
        def replace():
            path = self.fixture.runs / (F.RUN + '.console')
            raw = path.read_bytes()
            path.unlink()
            path.write_bytes(raw)
            path.chmod(0o400)
        self.mutate_during_stream(replace)
        self.assert_released()

    def test_zero_length_console_supported(self):
        receipt, writer = self.send()
        members = self.members(writer.getvalue())
        value = S.FORMAT.validate_lifecycle(members['lifecycle.json'], members['enrollment.json'])
        self.assertEqual(S.CP.decoded(value['records']['runs/' + F.RUN + '.console']), b'')
        self.assertTrue(receipt['success'])
        self.assert_released()


def publish_mock_fixture(case):
    """Simulate the reviewed fixture producer, retaining actual CP publication.

    Catalogue, HTTP/KVM observations and StoppedRun are explicit host fixtures.
    Sender/cohort authentication and native export locks remain unchanged.
    """
    checkpoint = S.CP
    catalogue = {**checkpoint.FIXTURE.LOCK, 'kernel': case.config['kernel'],
                 'initrd': case.config['initrd'], 'disk_bytes': 512}
    lock_path = case.base / 'sender-fixture-catalogue.json'
    lock_path.write_bytes(checkpoint.FIXTURE.canonical(catalogue))
    lock_path.chmod(0o400)
    body = b'a733-kvm-guest-network-v1 counter=1\n'

    class MockFixture:
        def __init__(self):
            self.supervisor = case.outcome._supervisor

        def attest_stopped(self, outcome, inspection):
            evidence = outcome.recheck()
            proof = dict(schema=1, profile='a733-managed-fixture-acceptance-v1',
                fixture_profile=checkpoint.FIXTURE.PROFILE, storage=F.STORAGE,
                name='one', run_id=F.RUN, boot_id=F.BOOT,
                admission_sha256=case.run.admission_sha256, old_manifest_sha256=case.pin,
                profile_sha256=checkpoint.digest(lock_path.read_bytes()), previous_counter=0,
                counter=1, hardware_acceptance=False, checkpoint_authorized=False,
                permit_sha256=checkpoint.digest(case.permit_raw), outcome=evidence,
                stopped_disk=inspection.disk_record, console_bytes=evidence['console_bytes'],
                console_sha256=evidence['console_sha256'], kernel=case.config['kernel'],
                initrd=case.config['initrd'], live=dict(port=18080, listen_inode='1234', kvm_fds=[
                    dict(fd=3, target='/dev/kvm'), dict(fd=4, target='anon_inode:kvm-vm'),
                    dict(fd=5, target='anon_inode:kvm-vcpu:0'),
                    dict(fd=6, target='anon_inode:kvm-arm-vgic-v3')]))
            for key in ('http_probe', 'http_confirmation', 'http_post_confirmation'):
                proof[key] = dict(status=200, body_sha256=checkpoint.digest(body), bytes=128)
            return checkpoint.FIXTURE.canonical(proof)

    fixture = MockFixture()
    case.outcome._supervisor._managed_fixture = fixture
    for name, value in [('Fixture', MockFixture), ('KERNEL', case.config['kernel']),
                        ('INITRD', case.config['initrd']), ('LOCK', catalogue),
                        ('LOCK_PATH', lock_path)]:
        mocked = patch.object(checkpoint.FIXTURE, name, value)
        mocked.start()
        case.addCleanup(mocked.stop)
    return checkpoint.checkpoint(case.outcome, fixture)


@unittest.skipUnless(os.getresuid() == (0, 0, 0), 'protected native fixture requires root')
class ManagedFixtureProfile(unittest.TestCase):
    def setUp(self):
        mocked = patch.object(F.ManagedCheckpoint, 'publish', publish_mock_fixture)
        mocked.start()
        self.addCleanup(mocked.stop)
        self.case = Completed(methodName='runTest')
        self.case.fixture_profile = S.CP.FIXTURE.PROFILE
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()

    def test_managed_power_key_fixture_exports_exact_proof(self):
        case = self.case
        receipt, writer = case.send()
        members = case.members(writer.getvalue())
        lifecycle = S.FORMAT.validate_lifecycle(members['lifecycle.json'], members['enrollment.json'])
        self.assertEqual(len(lifecycle['records']), 11)
        proof = (case.fixture.runs / (F.RUN + '.fixture.json')).read_bytes()
        self.assertEqual(S.CP.decoded(lifecycle['records']['runs/' + F.RUN + '.fixture.json']), proof)
        self.assertEqual(receipt['archive'], S.CP.reference(writer.getvalue()))
        self.assertTrue(receipt['success'])
        self.assertFalse(receipt['hardware_acceptance'])
        case.assert_released()

    def test_tampered_managed_power_key_fixture_refuses_before_output(self):
        case = self.case
        path = case.fixture.runs / (F.RUN + '.fixture.json')
        proof = json.loads(path.read_bytes())
        proof['counter'] = 2
        path.write_bytes(S.CP.FIXTURE.canonical(proof))
        writer = io.BytesIO()
        with self.assertRaises(S.LIFECYCLE.OperationError):
            case.send(writer)
        self.assertEqual(writer.getvalue(), b'')
        case.assert_released()


class Initial(Sender, unittest.TestCase):
    initial = True

    def test_foreign_thread_and_fork_cannot_close_owned_custody(self):
        source = S.Source('one', self.pin)
        self.addCleanup(source.close)
        def foreign_thread():
            errors = []
            def close():
                try:
                    source.close()
                except ValueError as error:
                    errors.append(str(error))
            with patch.object(source.stack, 'close', side_effect=AssertionError('foreign cleanup')) as cleanup:
                thread = threading.Thread(target=close)
                thread.start()
                thread.join(5)
                self.assertFalse(thread.is_alive())
                self.assertEqual(len(errors), 1)
                self.assertIn('another process or thread', errors[0])
                cleanup.assert_not_called()
        foreign_thread()
        pid = os.fork()
        if pid == 0:
            try:
                with patch.object(source.stack, 'close', side_effect=AssertionError('foreign cleanup')):
                    source.close()
            except ValueError:
                os._exit(0)
            except BaseException:
                os._exit(2)
            os._exit(1)
        waited, status = os.waitpid(pid, 0)
        self.assertEqual(waited, pid)
        self.assertEqual(status, 0)
        for fd in [source.lease.fd, source.lock.fd, *source.inspection.fds.values()]:
            os.fstat(fd)
        source.recheck()
        source.close()
        foreign_thread()  # Even an already closed object remains creator-bound.
        self.assert_released()


class Boundary(unittest.TestCase):
    def test_nonroot_refused_before_platform_reads(self):
        with patch.object(S.os, 'getresuid', return_value=(1000, 1000, 1000)), \
                patch.object(S.CONTEXT, 'read_system') as read:
            with self.assertRaisesRegex(ValueError, 'actual root'):
                S.send('one', 'a' * 64, io.BytesIO())
            read.assert_not_called()

    def test_console_stdout_refused(self):
        with tempfile.TemporaryFile() as stream:
            with self.assertRaisesRegex(ValueError, 'pipe or socket'):
                S.LEGACY.stdout_guard(stream)


if __name__ == '__main__':
    unittest.main()
