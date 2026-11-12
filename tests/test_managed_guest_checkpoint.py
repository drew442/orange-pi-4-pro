"""Root protected-file regressions with explicitly mocked live producers.

These tests retain actual stopped Inspection/flock and publication syscalls.
Host placement, accounts, admission capabilities and StoppedRun observations are
simulated. They establish no KVM, A733, clean-filesystem or hardware acceptance.
"""
import copy
from contextlib import contextmanager
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


M = module('managed_checkpoint_under_test', 'scripts/managed-guest-checkpoint.py')
P, L = M.POLICY, M.LIFECYCLE
PERMIT_FIXTURE = module('managed_checkpoint_permit_fixture', 'tests/test_guest_run_permit.py')
RUN = '44444444-4444-4444-8444-444444444444'
BOOT = '11111111-1111-4111-8111-111111111111'
STORAGE = dict(state_uuid='22222222-2222-4222-8222-222222222222',
               guest_uuid='33333333-3333-4333-8333-333333333333', nvme_serial='SSD-123')


@unittest.skipUnless(os.geteuid() == 0, 'protected ownership tests require root')
class ManagedCheckpoint(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir='/run', prefix='managed-checkpoint-tests-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root, self.state = self.base / 'guests', self.base / 'state'
        self.root.mkdir(mode=0o711)
        self.state.mkdir(mode=0o700)
        self.guest = self.root / 'one'
        self.guest.mkdir(mode=0o710)
        os.chown(self.guest, 0, 60001)
        self.config = dict(schema=1, name='one', uid=60001, gid=60001,
                           memory_mib=64, memory_max_mib=128, cpus=1)
        if getattr(self, 'fixture_profile', 'none') == M.FIXTURE.PROFILE:
            self.config['network'] = dict(mode='isolated', host_tcp_port=18080, guest_tcp_port=8080)
        files = {}
        for key, name, data in [('kernel', 'Image', b'kernel'), ('initrd', 'initramfs.cpio.gz', b'initrd'),
                                ('disk', 'disk.raw', b'A' * 512)]:
            path = self.guest / name
            path.write_bytes(data)
            path.chmod(0o600 if key == 'disk' else 0o444)
            if key == 'disk':
                os.chown(path, 60001, 60001)
            self.config[key] = M.reference(data)
            files[name] = dict(**self.config[key], uid=60001 if key == 'disk' else 0,
                               gid=60001 if key == 'disk' else 0,
                               mode='0600' if key == 'disk' else '0444')
        manifest = dict(schema=1, profile=M.MANIFEST.PROFILE, guest_uuid=STORAGE['guest_uuid'],
                        config=self.config, files=files)
        self.old_raw = json.dumps(manifest, indent=2).encode() + b'\n\n'
        self.manifest_path = self.guest / 'enrollment.json'
        self.manifest_path.write_bytes(self.old_raw)
        self.manifest_path.chmod(0o400)
        self.pin = M.digest(self.old_raw)
        self.disk = self.guest / 'disk.raw'
        self.placement = dict(namespace='fixture-only')
        self.markers, self.lock_events = set(), []
        fixture = self

        class HostContext:
            def __init__(self, storage, lease, *, boot_id):
                fixture.assertEqual(storage, STORAGE)
                self.storage, self.lease = copy.deepcopy(storage), lease
                self.state_fd = os.open(fixture.state, os.O_RDONLY | os.O_DIRECTORY)
                self.guest_fd = os.open(fixture.root, os.O_RDONLY | os.O_DIRECTORY)
                self.closed = False
            def guard(self):
                M.require(not self.closed, 'context closed')
                self.lease.recheck()
            def close(self):
                if not self.closed:
                    self.closed = True
                    os.close(self.guest_fd)
                    os.close(self.state_fd)
            def __enter__(self):
                self.guard()
                return self
            def __exit__(self, *args):
                self.close()

        class Lease:
            def __init__(self, admission):
                self.admission, self.closed = admission, False
            def recheck(self):
                M.require(not self.closed, 'lease closed')

        class GuestLock:
            def __init__(self, name):
                self.name, self.closed = name, False
            def recheck(self):
                M.require(not self.closed, 'guest lock closed')
            def close(self):
                self.closed = True

        class Admission:
            @contextmanager
            def guest_lock(self, name, lease, *, create=False):
                fixture.lock_events.append('guest-lock')
                lock = GuestLock(name)
                try:
                    yield lock
                finally:
                    lock.close()
                    fixture.lock_events.append('guest-unlock')
            @contextmanager
            def registry_lock(self, lease):
                fixture.lock_events.append('registry-lock')
                yield SimpleNamespace(recheck=lease.recheck)
                fixture.lock_events.append('registry-unlock')
            def is_managed(self, name, lease, lock):
                return name in fixture.markers
            def prepare_managed(self, lease):
                lease.recheck()
            def mark_managed(self, name, lease, lock, *, guard):
                guard()
                fixture.markers.add(name)

        patches = [patch.object(L.CONTEXT, 'HostContext', HostContext),
                   patch.object(L.ADMISSION, 'Lease', Lease), patch.object(L.ADMISSION, 'GuestLock', GuestLock),
                   patch.object(P.ENROLL, 'ROOT', str(self.root)),
                   patch.object(P.MANAGEMENT, 'runtime_guard'), patch.object(P, 'host_namespace'),
                   patch.object(P.ENROLL, 'probe_uuid'), patch.object(P, 'service_state', return_value={}),
                   patch.object(P, 'disk_consumers', return_value=[]),
                   patch.object(P.ACCOUNTS, 'validate_accounts'), patch.object(P.ACCOUNTS, 'snapshot', return_value={}),
                   patch.object(P.STORAGE, 'verify_guest_directory', return_value=self.placement),
                   patch.object(P.STORAGE, 'verify_guest_storage', return_value=self.placement)]
        for mocked in patches:
            mocked.start()
            self.addCleanup(mocked.stop)
        self.lease = Lease(Admission())
        self.controller = L.StateController(STORAGE, self.lease, boot_id=BOOT)
        self.addCleanup(self.controller.close)
        self.controller.enroll('one', self.pin, enabled=True,
                               fixture_profile=getattr(self, 'fixture_profile', 'none'))
        self.run = self.controller.admit_run('one', self.pin, RUN)
        self.addCleanup(self.run.close)
        admitted_pin = self.run.head_sha256
        self.run.mark_running()
        self.run.mark_stopping()
        self.disk.write_bytes(b'B' * 512)
        permit = PERMIT_FIXTURE.permit()
        permit.update(run_id=RUN, boot_id=BOOT, storage=copy.deepcopy(STORAGE), config=copy.deepcopy(self.config),
                      manifest_sha256=self.pin, head_sha256=admitted_pin, automatic=False)
        self.permit_raw = M.RECORDS.PERMIT.canonical(permit)
        session = M.RECORDS.QMP.Session(RUN)
        session.receive({'QMP': {'version': {'qemu': {'major': 7, 'minor': 2, 'micro': 22},
                                                     'package': ''}, 'capabilities': []}})
        for command in ('qmp_capabilities', 'cont', 'system_powerdown'):
            sent = session.command(command)
            session.receive({'return': {}, 'id': sent['id']})
        session.receive({'event': 'SHUTDOWN', 'data': {'guest': True, 'reason': 'guest-shutdown'}})
        console = b'guest synced\n'
        if getattr(self, 'fixture_profile', 'none') == M.FIXTURE.PROFILE:
            console = (f'PASS cpu sha256={M.FIXTURE.CPU}\nPASS disk previous=0 counter=1\n'
                       'READY network tcp=8080 counter=1; host must verify and confirm HTTP response\n'
                       'PASS network loopback-http counter=1\n'
                       'PASS fixture counter=1; READY managed-power-key-v1\n'
                       'PASS managed-power-key-sync\n').encode()
        evidence = dict(schema=1, profile='a733-supervised-shutdown-v1', run_id=RUN, boot_id=BOOT,
                        permit_sha256=M.digest(self.permit_raw), stopping_head_sha256=self.run.head_sha256,
                        raw_wait_status=0, qmp=list(session.transcript), console_bytes=len(console),
                        console_sha256=M.digest(console), hardware_acceptance=False, checkpoint_authorized=False)

        class MockStoppedRun:
            def __init__(self):
                self.run = fixture.run
                self._supervisor = SimpleNamespace(receipt={'errors': []})
                self._permit_data, self._console_data = fixture.permit_raw, console
            def recheck(self):
                M.require(self.run.recheck()['status'] == 'stopping', 'original stopping head required')
                return copy.deepcopy(evidence)
        mocked = patch.object(M.SUPERVISOR, 'StoppedRun', MockStoppedRun)
        mocked.start()
        self.addCleanup(mocked.stop)
        self.outcome = MockStoppedRun()
        self.head_path = self.root / '.a733-lifecycle/one/head.json'
        self.runs = self.head_path.parent / 'runs'
        self.registry_path = self.state / 'guests/autostart.json'

    def publish(self):
        return M.checkpoint(self.outcome)

    def head(self):
        return M.HEAD.validate_head(self.head_path.read_bytes())

    def registry(self):
        return M.REGISTRY.validate_registry(self.registry_path.read_bytes())

    def recover(self):
        self.run.close()  # Fresh reconciliation owns a new guest/disk lock.
        return M.reconcile(self.controller, 'one')

    def test_publication_cohort_and_repeated_fresh_verification(self):
        result = self.publish()
        self.assertTrue(result['success'])
        self.assertTrue(result['supervisor_cleanup_required'])
        self.assertFalse(result['hardware_acceptance'])
        self.assertEqual(self.head()['sequence'], 5)
        self.assertEqual(self.disk.read_bytes(), b'B' * 512)
        self.assertFalse(self.lease.closed)
        self.assertFalse(self.run._closed)
        with self.assertRaises(ValueError):
            self.run.recheck()  # Old live capability deliberately remains stale.
        for path in self.runs.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o400)
        before = self.head_path.read_bytes(), self.registry_path.read_bytes()
        completed = (self.runs / (RUN + '.completed.json')).read_bytes()
        self.assertTrue(self.recover()['success'])
        self.assertTrue(M.authenticate_ready_cohort(self.controller, 'one')['success'])
        self.assertEqual(before, (self.head_path.read_bytes(), self.registry_path.read_bytes()))
        self.assertEqual(completed, (self.runs / (RUN + '.completed.json')).read_bytes())

    def test_noop_preserves_exact_noncanonical_old_bytes(self):
        self.disk.write_bytes(b'A' * 512)
        inode = self.manifest_path.stat().st_ino
        result = self.publish()
        self.assertTrue(result['success'])
        self.assertTrue(result['transaction']['unchanged'])
        self.assertEqual(self.old_raw, self.manifest_path.read_bytes())
        self.assertEqual(inode, self.manifest_path.stat().st_ino)
        self.assertFalse((self.guest / '.checkpoints').exists())
        self.assertTrue(self.recover()['success'])

    def test_missing_completed_cohort_blocks_admission_and_enable(self):
        self.publish()
        self.run.close()
        (self.runs / (RUN + '.completed.json')).unlink()
        stable = self.head_path.read_bytes()
        pin = M.digest(self.manifest_path.read_bytes())
        for function in (lambda: self.controller.admit_run('one', pin,
                          '55555555-5555-4555-8555-555555555555'),
                         lambda: self.controller.enable('one', pin)):
            with self.assertRaises(L.OperationError):
                function()
        self.assertEqual(stable, self.head_path.read_bytes())
        self.assertTrue(self.recover()['success'])

    def test_unchanged_completion_survives_disable_and_other_guest_update(self):
        self.publish()
        completed = (self.runs / (RUN + '.completed.json')).read_bytes()
        self.controller.disable('one')
        registry = self.registry()
        registry['generation'] += 1
        registry['guests'].append(dict(name='two', uid=60002, gid=60002,
            manifest_sha256='a' * 64, enabled=True, shutdown_profile=M.REGISTRY.SHUTDOWN_PROFILE,
            fixture_profile='none'))
        self.registry_path.write_bytes(M.REGISTRY.canonical(registry))
        self.assertTrue(self.recover()['success'])
        self.assertFalse(self.registry()['guests'][0]['enabled'])
        self.assertEqual(len(self.registry()['guests']), 2)
        self.assertEqual(completed, (self.runs / (RUN + '.completed.json')).read_bytes())

    def test_actual_producer_required_and_no_public_authorization_bypass(self):
        with self.assertRaises(ValueError):
            M.CheckpointCustody(True)
        for value in ({}, object(), SimpleNamespace(run=self.run, authorized=True)):
            with self.assertRaises(L.OperationError):
                M.checkpoint(value)
        self.assertFalse((self.runs / (RUN + '.outcome.json')).exists())

    def test_publication_boundary_failures_recover_only_complete_intent(self):
        # A syscall may succeed and its caller then lose the acknowledgement.
        for target in (RUN + '.console', RUN + '.outcome.json', RUN + '.intent.json',
                       'checkpoint-head', 'ready-head', 'autostart.json', RUN + '.completed.json'):
            with self.subTest(target=target):
                case = ManagedCheckpoint()
                case.setUp()
                try:
                    original = M.STORE.RootDirectory.publish
                    fired = []
                    def fail_after(store, name, data, **kwargs):
                        receipt = original(store, name, data, **kwargs)
                        kind = name
                        if name == 'head.json':
                            status = M.HEAD.validate_head(data)['status']
                            kind = status + '-head'
                        if kind == target and not fired:
                            fired.append(True)
                            raise M.STORE.PublicationError(receipt, OSError('ack lost after effect'))
                        return receipt
                    with patch.object(M.STORE.RootDirectory, 'publish', fail_after):
                        with self.assertRaises(L.OperationError):
                            case.publish()
                    self.assertTrue(fired)
                    self.assertNotEqual(case.head()['status'], 'quarantined')
                    if target in (RUN + '.console', RUN + '.outcome.json'):
                        with self.assertRaises(L.OperationError):
                            case.recover()
                        self.assertEqual(case.head()['status'], 'quarantined')
                    else:
                        self.assertTrue(case.recover()['success'])
                        self.assertEqual(case.head()['status'], 'ready')
                finally:
                    case.doCleanups()

    def test_managed_fixture_exact_proof_bound_before_outcome(self):
        case = ManagedCheckpoint()
        case.fixture_profile = M.FIXTURE.PROFILE
        case.setUp()
        try:
            catalogue = {**M.FIXTURE.LOCK, 'kernel': case.config['kernel'],
                         'initrd': case.config['initrd'], 'disk_bytes': 512}
            lock_path = case.base / 'catalogue.json'
            lock_path.write_bytes(M.FIXTURE.canonical(catalogue))
            lock_path.chmod(0o400)
            body = b'a733-kvm-guest-network-v1 counter=1\n'
            class MockFixture:
                def __init__(self):
                    self.supervisor = case.outcome._supervisor
                def attest_stopped(self, outcome, inspection):
                    evidence = outcome.recheck()
                    proof = dict(schema=1, profile='a733-managed-fixture-acceptance-v1',
                        fixture_profile=M.FIXTURE.PROFILE, storage=STORAGE, name='one', run_id=RUN,
                        boot_id=BOOT, admission_sha256=case.run.admission_sha256,
                        old_manifest_sha256=case.pin, profile_sha256=M.digest(lock_path.read_bytes()),
                        previous_counter=0, counter=1, hardware_acceptance=False,
                        checkpoint_authorized=False, permit_sha256=M.digest(case.permit_raw),
                        outcome=evidence, stopped_disk=inspection.disk_record,
                        console_bytes=evidence['console_bytes'], console_sha256=evidence['console_sha256'],
                        kernel=case.config['kernel'], initrd=case.config['initrd'],
                        live=dict(port=18080, listen_inode='1234', kvm_fds=[
                            dict(fd=3, target='/dev/kvm'), dict(fd=4, target='anon_inode:kvm-vm'),
                            dict(fd=5, target='anon_inode:kvm-vcpu:0'),
                            dict(fd=6, target='anon_inode:kvm-arm-vgic-v3')]))
                    for key in ('http_probe', 'http_confirmation', 'http_post_confirmation'):
                        proof[key] = dict(status=200, body_sha256=M.digest(body), bytes=128)
                    return M.FIXTURE.canonical(proof)
            actual_fixture = MockFixture()
            case.outcome._supervisor._managed_fixture = actual_fixture
            with patch.object(M.FIXTURE, 'Fixture', MockFixture), \
                 patch.object(M.FIXTURE, 'KERNEL', case.config['kernel']), \
                 patch.object(M.FIXTURE, 'INITRD', case.config['initrd']), \
                 patch.object(M.FIXTURE, 'LOCK', catalogue), patch.object(M.FIXTURE, 'LOCK_PATH', lock_path):
                result = M.checkpoint(case.outcome, actual_fixture)
                self.assertTrue(result['success'])
                outcome = M.RECORDS.validate_outcome((case.runs / (RUN + '.outcome.json')).read_bytes())
                proof_raw = (case.runs / (RUN + '.fixture.json')).read_bytes()
                self.assertEqual(outcome['fixture'], M.reference(proof_raw))
                self.assertEqual(json.loads(proof_raw)['outcome'], outcome['shutdown'])
                self.assertTrue(case.recover()['success'])
        finally:
            case.doCleanups()

    def test_history_manifest_and_disk_fsync_failures_after_effect(self):
        for target in ('disk.raw', 'history-temporary', 'manifest-temporary', 'guest-directory'):
            with self.subTest(target=target):
                case = ManagedCheckpoint()
                case.setUp()
                try:
                    original = os.fsync
                    fired = []
                    def fail_after(fd):
                        name = os.readlink('/proc/self/fd/' + str(fd))
                        kind = Path(name).name
                        if '.checkpoint-' in kind:
                            value = json.loads(Path('/proc/self/fd/' + str(fd)).read_bytes())
                            kind = 'history-temporary' if value.get('operation') == 'a733-managed-guest-checkpoint-v2' else 'manifest-temporary'
                        if name == str(case.guest):
                            kind = 'guest-directory'
                        original(fd)
                        if kind == target and not fired:
                            fired.append(True)
                            raise OSError('fsync acknowledgement lost after effect')
                    with patch.object(os, 'fsync', fail_after):
                        with self.assertRaises(L.OperationError):
                            case.publish()
                    self.assertTrue(fired)
                    self.assertNotEqual(case.head()['status'], 'quarantined')
                    self.assertTrue(case.recover()['success'])
                finally:
                    case.doCleanups()

    def test_record_fsync_failures_preserve_publication_uncertainty(self):
        for target in ('console', 'outcome', 'intent', 'checkpoint', 'ready', 'registry', 'completion'):
            with self.subTest(target=target):
                case = ManagedCheckpoint()
                case.setUp()
                try:
                    original = os.fsync
                    fired = []
                    def fail_after(fd):
                        name = os.readlink('/proc/self/fd/' + str(fd))
                        kind = None
                        if '.lifecycle-' in name:
                            raw = Path('/proc/self/fd/' + str(fd)).read_bytes()
                            if raw == case.outcome._console_data:
                                kind = 'console'
                            else:
                                value = json.loads(raw)
                                profile = value.get('profile')
                                kind = {M.RECORDS.OUTCOME_PROFILE: 'outcome',
                                        M.RECORDS.INTENT_PROFILE: 'intent',
                                        M.RECORDS.COMPLETION_PROFILE: 'completion',
                                        M.REGISTRY.PROFILE: 'registry'}.get(profile)
                                if profile == M.HEAD.PROFILE:
                                    kind = value['status']
                        original(fd)
                        if kind == target and not fired:
                            fired.append(True)
                            raise OSError('record fsync acknowledgement lost')
                    with patch.object(os, 'fsync', fail_after):
                        with self.assertRaises(L.OperationError):
                            case.publish()
                    self.assertTrue(fired)
                    self.assertNotEqual(case.head()['status'], 'quarantined')
                    if target in ('console', 'outcome', 'intent'):
                        with self.assertRaises(L.OperationError):
                            case.recover()
                        self.assertEqual(case.head()['status'], 'quarantined')
                    else:
                        self.assertTrue(case.recover()['success'])
                finally:
                    case.doCleanups()

    def test_concurrent_disable_and_unrelated_guest_preserved_at_final_merge(self):
        original = P.PreparedPublication.bind_published
        def change_registry(publication):
            result = original(publication)
            self.controller.disable('one')
            registry = self.registry()
            registry['generation'] += 1
            registry['guests'].append(dict(name='two', uid=60002, gid=60002,
                manifest_sha256='a' * 64, enabled=True, shutdown_profile=M.REGISTRY.SHUTDOWN_PROFILE,
                fixture_profile='none'))
            self.registry_path.write_bytes(M.REGISTRY.canonical(registry))
            return result
        with patch.object(P.PreparedPublication, 'bind_published', change_registry):
            self.publish()
        registry = self.registry()
        self.assertFalse(registry['guests'][0]['enabled'])
        self.assertEqual(registry['guests'][0]['manifest_sha256'], M.digest(self.manifest_path.read_bytes()))
        self.assertEqual(registry['guests'][1]['name'], 'two')
        self.assertTrue(registry['guests'][1]['enabled'])

    def test_manifest_replace_lost_ack_recovers_exact_new_manifest(self):
        original = os.replace
        def fail_after(src, dst, **kwargs):
            original(src, dst, **kwargs)
            if dst == 'enrollment.json':
                raise OSError('manifest rename acknowledgement lost')
        with patch.object(os, 'replace', fail_after):
            with self.assertRaises(L.OperationError):
                self.publish()
        self.assertEqual(self.head()['status'], 'checkpoint')
        self.assertNotEqual(self.manifest_path.read_bytes(), self.old_raw)
        stable = self.manifest_path.read_bytes()
        self.assertTrue(self.recover()['success'])
        self.assertEqual(stable, self.manifest_path.read_bytes())

    def test_owned_close_after_effect_prevents_success_without_requarantining(self):
        original = os.close
        fired = []
        def fail_after(fd):
            name = os.readlink('/proc/self/fd/' + str(fd))
            original(fd)
            # The custody's runs directory closes after completion is durable.
            if name == str(self.runs) and (self.runs / (RUN + '.completed.json')).exists() and not fired:
                fired.append(True)
                raise OSError('close acknowledgement lost after effect')
        with patch.object(os, 'close', fail_after):
            with self.assertRaises(L.OperationError):
                self.publish()
        self.assertTrue(fired)
        self.assertEqual(self.head()['status'], 'ready')
        self.assertTrue(self.recover()['success'])

    def test_disk_flock_retained_across_manifest_replacement_and_binding(self):
        original = P.PreparedPublication.bind_published
        def assert_retained(publication):
            fd = os.open(self.disk, os.O_RDONLY)
            try:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(fd)
            return original(publication)
        with patch.object(P.PreparedPublication, 'bind_published', assert_retained):
            self.publish()
        fd = os.open(self.disk, os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)

    def test_tamper_each_current_cohort_record_and_legal_edge_refused(self):
        for target in ('admission', 'intent', 'outcome', 'console', 'completion', 'history', 'edge', 'disk'):
            with self.subTest(target=target):
                case = ManagedCheckpoint()
                case.setUp()
                try:
                    case.publish()
                    case.run.close()
                    paths = dict(admission=case.runs / (RUN + '.json'),
                                 intent=case.runs / (RUN + '.intent.json'),
                                 outcome=case.runs / (RUN + '.outcome.json'),
                                 console=case.runs / (RUN + '.console'),
                                 completion=case.runs / (RUN + '.completed.json'),
                                 history=next((case.guest / '.checkpoints').iterdir()),
                                 edge=next((case.head_path.parent / 'transitions').iterdir()), disk=case.disk)
                    path = paths[target]
                    path.write_bytes(path.read_bytes() + b' ')
                    with self.assertRaises(L.OperationError):
                        M.authenticate_ready_cohort(case.controller, 'one')
                    self.assertEqual(case.head()['status'], 'ready')
                finally:
                    case.doCleanups()

    def test_unexplained_disk_conflict_quarantines_and_never_clears(self):
        self.publish()
        self.disk.write_bytes(b'C' * 512)
        with self.assertRaises(L.OperationError):
            self.recover()
        self.assertEqual(self.head()['status'], 'quarantined')
        stable = self.head_path.read_bytes()
        self.disk.write_bytes(b'B' * 512)
        with self.assertRaises(L.OperationError):
            self.recover()
        self.assertEqual(stable, self.head_path.read_bytes())

    def test_later_run_cannot_replay_old_intent(self):
        self.publish()
        self.run.close()
        pin = M.digest(self.manifest_path.read_bytes())
        later = self.controller.admit_run('one', pin, '55555555-5555-4555-8555-555555555555')
        later.close()
        with self.assertRaises(L.OperationError):
            self.recover()
        self.assertEqual(self.head()['status'], 'quarantined')



if __name__ == '__main__':
    unittest.main()
