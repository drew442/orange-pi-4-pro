"""Native tmpfs/nonroot admission fixtures; no A733, KVM or deployment claim."""
import argparse
import ctypes
import fcntl
import hashlib
import importlib.util
import json
import os
import signal
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('launch_admission_tests', BASE / 'scripts/guest-launch-admission.py')
L = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(L)
A = L.ADMISSION
SPEC2 = importlib.util.spec_from_file_location('manual_launcher_tests', BASE / 'scripts/launch-kvm-guest.py')
LAUNCH = importlib.util.module_from_spec(SPEC2)
SPEC2.loader.exec_module(LAUNCH)


class Shape(unittest.TestCase):
    def test_fixed_names_and_paths(self):
        args = argparse.Namespace(**{key: Path('/run/a733-guests/one') / leaf
                                     for key, leaf in L.FILES.items()})
        self.assertEqual(L.enrolled_name(args), 'one')
        for key in L.FILES:
            with patch.object(args, key, Path('/tmp/elsewhere')):
                with self.assertRaises(A.AdmissionError):
                    L.enrolled_name(args)
        for name in ('Bad', '../one', 'one/../two', 'unknown/name'):
            args.kernel = Path('/run/a733-guests') / name / 'Image'
            with self.assertRaises(A.AdmissionError):
                L.enrolled_name(args)

    def test_foreign_owner_refused(self):
        with self.assertRaisesRegex(A.AdmissionError, 'canonical bootstrap owner'):
            L.prepare_runtime(object())

    def test_launcher_admission_failure_precedes_input_and_access(self):
        args = ['--memory-mib', '128', '--cpus', '1']
        for key, leaf in L.FILES.items():
            args += ['--' + key, '/run/a733-guests/one/' + leaf,
                     '--' + key + '-sha256', '0' * 64]
        with patch.object(LAUNCH, 'launch_admission', side_effect=A.AdmissionError('refused')), \
                patch.object(LAUNCH, 'prepare') as prepare, \
                patch.object(LAUNCH, 'require_launch_access') as access, \
                patch.object(LAUNCH.sys, 'stderr'):
            self.assertEqual(LAUNCH.main(args), 1)
        prepare.assert_not_called()
        access.assert_not_called()

    def test_canonical_attestation_closed_shape(self):
        value = dict(schema=1, profile=L.PROFILE, boot_id=A._boot_id(), kvm_gid=108,
                     namespaces={key: dict(dev=1, ino=2) for key in ('pid', 'user', 'mnt')})
        L.canonical(value)
        for changed in ({**value, 'bypass': True}, {**value, 'schema': True},
                        {**value, 'kvm_gid': True}, {**value, 'namespaces': {}},
                        {**value, 'namespaces': {key: dict(dev=True, ino=2)
                                               for key in ('pid', 'user', 'mnt')}}):
            with self.assertRaises(A.AdmissionError):
                L.canonical(changed)


@unittest.skipUnless(os.getresuid() == (0, 0, 0), 'requires root for actual tmpfs/credential fixture')
class Native(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='a733-launch-test-', dir='/run')
        self.addCleanup(self.tmp.cleanup)
        os.chmod(self.tmp.name, 0o711)
        self.runtime = Path(self.tmp.name) / 'a733-admission'
        self.guests = tempfile.TemporaryDirectory(prefix='a733-enrolled-test-', dir='/run')
        self.addCleanup(self.guests.cleanup)
        os.chmod(self.guests.name, 0o711)
        self.home = Path(self.guests.name) / 'one'
        self.home.mkdir(mode=0o710)
        os.chown(self.home, 0, 60000)
        self.args = argparse.Namespace(memory_mib=128, cpus=1, dry_run=False)
        for key, leaf in L.FILES.items():
            path = self.home / leaf
            data = (key + ' fixture').encode()
            path.write_bytes(data)
            os.chmod(path, 0o600 if key == 'disk' else 0o444)
            os.chown(path, 60000 if key == 'disk' else 0, 60000 if key == 'disk' else 0)
            setattr(self.args, key, path)
            setattr(self.args, key + '_sha256', hashlib.sha256(data).hexdigest())
        # Only board-compatible provenance is substituted. Root and child use
        # actual boot ID, PID1/self namespace links, tmpfs metadata and flocks.
        native_read = L.CONTEXT.read_system
        def read(path, limit):
            if path == '/proc/device-tree/compatible':
                return L.CONTEXT.COMPATIBLE
            return native_read(path, limit)
        for fixture in (patch.object(A, 'RUNTIME_PARENT', self.tmp.name),
                        patch.object(A, 'RUNTIME', str(self.runtime)),
                        patch.object(L, 'GUESTS', self.guests.name),
                        patch.object(L.CONTEXT, 'read_system', side_effect=read),
                        patch.object(L.NORMAL, 'manager_state', return_value='running'),
                        patch.object(LAUNCH.STORAGE, 'verify_guest_storage', return_value={'fixture': True})):
            fixture.start()
            self.addCleanup(fixture.stop)
        self.admission = A.initialize(A._boot_id(), 108, lambda: 'running')
        self.addCleanup(self.admission.close)
        self.owner = self.admission.bootstrap_owner
        self.admission.prepare_managed(self.owner.lease)
        with self.admission.guest_lock('one', self.owner.lease, create=True):
            pass
        L.prepare_runtime(self.owner)

    def open(self):
        self.owner.open()
        self.owner.lease.close()

    def child(self, action, *, groups=(108,), nnp=True, on_ready=None):
        read, write = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.close(read)
            try:
                signal.alarm(10)
                libc = ctypes.CDLL(None, use_errno=True)
                for cap in range(int(Path('/proc/sys/kernel/cap_last_cap').read_text()) + 1):
                    if libc.prctl(24, cap, 0, 0, 0):
                        raise OSError(ctypes.get_errno(), 'drop bounding capability')
                os.setgroups(list(groups))
                os.setgid(60000)
                os.setuid(60000)
                if nnp and libc.prctl(38, 1, 0, 0, 0):
                    raise OSError(ctypes.get_errno(), 'set no new privileges')
                action(write)
                os.write(write, b'OK')
                os._exit(0)
            except BaseException as error:
                os.write(write, (type(error).__name__ + ': ' + str(error)).encode())
                os._exit(1)
        os.close(write)
        data = b''
        while True:
            piece = os.read(read, 65536)
            if not piece:
                break
            data += piece
            if on_ready is not None and data == b'READY':
                on_ready()
                on_ready = None
        os.close(read)
        _, status = os.waitpid(pid, 0)
        return os.waitstatus_to_exitcode(status), data.decode()

    def launch(self, write):
        with L.ManualLaunch(self.args) as operation:
            with LAUNCH.prepare(self.args, operation) as guest:
                operation.validate_inputs(guest)
                self.assertEqual(len(operation.recheck()), 2)

    def test_root_publish_retry_and_nonroot_own_namespace_only(self):
        before = (self.runtime / L.RECORD).stat()
        L.prepare_runtime(self.owner)
        self.assertEqual(before.st_ino, (self.runtime / L.RECORD).stat().st_ino)
        self.assertEqual((before.st_uid, before.st_gid, before.st_mode & 0o777), (0, 108, 0o440))
        self.open()
        def action(write):
            with self.assertRaises(PermissionError):
                os.stat('/proc/1/ns/mnt')
            self.launch(write)
        self.assertEqual(self.child(action), (0, 'OK'))

    def test_missing_attestation_unknown_guest_and_managed_refused(self):
        self.open()
        path = self.runtime / L.RECORD
        data = path.read_bytes()
        path.unlink()
        code, error = self.child(self.launch)
        self.assertNotEqual(code, 0)
        self.assertIn('FileNotFoundError', error)
        path.write_bytes(data); os.chown(path, 0, 108); os.chmod(path, 0o440)
        (self.runtime / 'guests/one.lock').unlink()
        self.assertIn('FileNotFoundError', self.child(self.launch)[1])

    def test_managed_marker_refuses(self):
        with self.admission.guest_lock('one', self.owner.lease) as lock:
            self.admission.mark_managed('one', self.owner.lease, lock, guard=lambda: None)
        self.open()
        self.assertIn('managed guest', self.child(self.launch)[1])

    def test_credential_groups_and_nnp_refused(self):
        self.open()
        for kwargs in ({'groups': (108, 60001)}, {'groups': ()}, {'nnp': False}):
            self.assertNotEqual(self.child(self.launch, **kwargs)[0], 0)
        self.assertEqual(self.child(self.launch, groups=(108, 60000)), (0, 'OK'))

    def test_namespace_boot_canonical_and_metadata_tamper(self):
        path = self.runtime / L.RECORD
        original = path.read_bytes()
        self.open()
        for mutation in ('namespace', 'boot', 'canonical', 'mode'):
            value = json.loads(original)
            if mutation == 'namespace':
                value['namespaces']['mnt']['ino'] += 1
            if mutation == 'boot':
                value['boot_id'] = '00000000-0000-0000-0000-000000000000'
            path.write_bytes(L.canonical(value) + (b' ' if mutation == 'canonical' else b''))
            os.chmod(path, 0o640 if mutation == 'mode' else 0o440)
            self.assertNotEqual(self.child(self.launch)[0], 0)

    def test_nonce_open_owner_and_lost_exclusive_refused(self):
        nonce = self.owner.nonce
        self.owner.nonce = '00000000-0000-0000-0000-000000000000'
        with self.assertRaises(A.AdmissionError):
            L.prepare_runtime(self.owner)
        self.owner.nonce = nonce
        fcntl.flock(self.owner.lease.fd, fcntl.LOCK_UN)
        with self.assertRaises(A.AdmissionError):
            L.prepare_runtime(self.owner)

    def test_existing_invalid_attestation_never_repaired(self):
        path = self.runtime / L.RECORD
        path.write_bytes(b'{}\n')
        inode = path.stat().st_ino
        with self.assertRaises(A.PublicationError):
            L.prepare_runtime(self.owner)
        self.assertEqual(path.read_bytes(), b'{}\n')
        self.assertEqual(path.stat().st_ino, inode)

    def test_lock_loss_and_unsafe_input_modes_recheck(self):
        self.open()
        def action(write):
            with L.ManualLaunch(self.args) as operation:
                fcntl.flock(operation.lease.fd, fcntl.LOCK_UN)
                with self.assertRaises(A.AdmissionError):
                    operation.recheck()
        self.assertEqual(self.child(action), (0, 'OK'))
        os.chmod(self.args.kernel, 0o666)
        self.assertIn('ownership or mode', self.child(self.launch)[1])

    def test_existing_holder_finishes_after_gate_closure(self):
        self.open()
        ack_read, ack_write = os.pipe()
        self.addCleanup(os.close, ack_read)
        self.addCleanup(os.close, ack_write)
        def action(write):
            with L.ManualLaunch(self.args) as operation:
                os.write(write, b'READY')
                self.assertEqual(os.read(ack_read, 1), b'x')
                with LAUNCH.prepare(self.args, operation) as guest:
                    operation.validate_inputs(guest)
                    operation.recheck()
            with self.assertRaisesRegex(A.AdmissionError, 'admission closed'):
                L.ManualLaunch(self.args)
        def close_gate():
            self.admission.close_gate('shutdown')
            os.write(ack_write, b'x')
        self.assertEqual(self.child(action, on_ready=close_gate), (0, 'READYOK'))

    def test_same_guest_lock_conflict_and_unsafe_input_precede_hash(self):
        self.open()
        with self.admission.admit() as lease:
            with self.admission.guest_lock('one', lease):
                self.assertIn('lock timeout', self.child(self.launch)[1])
        os.chmod(self.args.kernel, 0o666)
        def action(write):
            with L.ManualLaunch(self.args) as operation:
                with patch.object(LAUNCH.PreparedGuest, 'verify_file') as hashing:
                    with self.assertRaises(A.AdmissionError):
                        LAUNCH.prepare(self.args, operation)
                    hashing.assert_not_called()
            # A failed prepare releases the disk flock and both operation locks.
            with L.ManualLaunch(self.args):
                pass
        self.assertEqual(self.child(action), (0, 'OK'))

    def test_foreign_thread_and_fork_cannot_close_creator_locks(self):
        self.open()
        def action(write):
            operation = L.ManualLaunch(self.args)
            for closed in (False, True):
                if closed:
                    operation.close()
                failures = []
                def foreign():
                    try:
                        operation.close()
                    except A.AdmissionError:
                        failures.append(True)
                thread = threading.Thread(target=foreign)
                thread.start()
                thread.join(2)
                self.assertFalse(thread.is_alive())
                self.assertEqual(failures, [True])
                pid = os.fork()
                if pid == 0:
                    try:
                        operation.close()
                    except A.AdmissionError:
                        os._exit(0)
                    os._exit(2)
                self.assertEqual(os.waitpid(pid, 0)[1], 0)
                if not closed:
                    operation.recheck()
            operation.close()
        self.assertEqual(self.child(action), (0, 'OK'))

    def test_lost_rename_ack_preserves_visible_record_and_reports_uncertainty(self):
        (self.runtime / L.RECORD).unlink()
        rename = L.CONTEXT.ENROLL.rename_new
        def lost_ack(*args):
            rename(*args)
            raise OSError('rename completed; acknowledgement lost')
        with patch.object(L.CONTEXT.ENROLL, 'rename_new', side_effect=lost_ack):
            with self.assertRaises(A.PublicationError) as raised:
                L.prepare_runtime(self.owner)
        self.assertEqual(raised.exception.stage, 'rename')
        self.assertTrue(raised.exception.uncertain)
        self.assertEqual(self.admission._read()['state'], 'closed')
        A._held_lock(self.owner.lease.fd, 'WRITE', os.getpid())
        before = (self.runtime / L.RECORD).stat().st_ino
        L.prepare_runtime(self.owner)
        self.assertEqual((self.runtime / L.RECORD).stat().st_ino, before)

    def test_failed_publication_cleans_temp_and_keeps_gate_closed(self):
        (self.runtime / L.RECORD).unlink()
        count = len(os.listdir('/proc/self/fd'))
        with patch.object(L.CONTEXT.ENROLL, 'rename_new', side_effect=OSError('rename failed')):
            with self.assertRaises(A.PublicationError) as raised:
                L.prepare_runtime(self.owner)
        self.assertEqual(raised.exception.stage, 'rename')
        self.assertTrue(raised.exception.uncertain)
        self.assertEqual(self.admission._read()['state'], 'closed')
        self.assertFalse(list(self.runtime.glob('.manual-launch-*')))
        self.assertEqual(len(os.listdir('/proc/self/fd')), count)
        L.prepare_runtime(self.owner)

    def test_producer_close_failure_is_visible_and_cleanup_once(self):
        (self.runtime / L.RECORD).unlink()
        original_close = os.close
        closes = []
        def close(fd):
            try:
                target = os.readlink('/proc/self/fd/' + str(fd))
            except OSError:
                target = ''
            writable = fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE == os.O_WRONLY
            original_close(fd)
            if target.endswith('/' + L.RECORD) and writable:
                closes.append(fd)
                raise OSError('close acknowledgement failed')
        with patch.object(L.os, 'close', side_effect=close):
            with self.assertRaises(A.PublicationError) as raised:
                L.prepare_runtime(self.owner)
        self.assertEqual(raised.exception.stage, 'cleanup')
        self.assertTrue(raised.exception.uncertain)
        self.assertEqual(len(closes), 1)
        self.assertEqual(self.admission._read()['state'], 'closed')
        L.prepare_runtime(self.owner)

    def test_open_owner_refused_and_admission_closed_refuses(self):
        self.open()
        with self.assertRaises(A.AdmissionError):
            L.prepare_runtime(self.owner)
        self.admission.close_gate('shutdown')
        self.assertIn('admission closed', self.child(self.launch)[1])

    def test_unsafe_extra_fds_are_refused(self):
        self.open()
        def action(write):
            with L.ManualLaunch(self.args) as operation:
                with LAUNCH.prepare(self.args, operation) as guest:
                    LAUNCH.admission_module = lambda: L
                    for unsafe in ((operation.lease.fd, operation.lease.fd),
                                   (guest.fds['disk'], operation.guest_lock.fd),
                                   (self.admission.gate_fd, operation.guest_lock.fd)):
                        with self.assertRaises((ValueError, A.AdmissionError)):
                            guest.inherit_inputs(optional_extra_fds=unsafe)
        self.assertEqual(self.child(action), (0, 'OK'))

    def test_actual_exec_retains_only_inputs_and_two_locks(self):
        self.open()
        def action(write):
            with L.ManualLaunch(self.args) as operation:
                with LAUNCH.prepare(self.args, operation) as guest:
                    locks = operation.recheck()
                    os.dup2(write, 1)
                    LAUNCH.admission_module = lambda: L
                    guest.inherit_inputs(optional_extra_fds=locks)
                    operation.recheck()
                    expected = sorted([*guest.fds.values(), *locks])
                    code = '''import fcntl,json,os,sys
held=[]
for entry in os.listdir('/proc/self/fd'):
 fd=int(entry)
 if fd>2:
  try: os.fstat(fd)
  except OSError: continue
  held.append(fd)
assert sorted(held)==json.loads(sys.argv[1]),(held,sys.argv)
for fd,mode in zip(json.loads(sys.argv[2]),('READ','WRITE')):
 assert mode in open('/proc/self/fdinfo/'+str(fd)).read()
print('exec locks retained')
'''
                    os.execv('/usr/bin/python3', ['/usr/bin/python3', '-c', code,
                                                 json.dumps(expected), json.dumps(locks)])
        self.assertEqual(self.child(action), (0, 'exec locks retained\n'))


if __name__ == '__main__':
    unittest.main()
