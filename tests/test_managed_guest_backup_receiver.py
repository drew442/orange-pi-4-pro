"""Artifact receiver/source checks only; no guest or hardware acceptance."""
import base64
import gzip
import hashlib
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


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


R = module('managed_receiver_tested', 'scripts/verify-managed-guest-backup.py')
S, F = R.SOURCE, R.FORMAT
FIX = module('managed_receiver_pure_fixture', 'tests/test_guest_managed_backup.py')
LEGACY = module('managed_receiver_legacy_fixture', 'tests/test_guest_backup_receiver.py')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def ref(data):
    return dict(bytes=len(data), sha256=sha(data), data_base64=base64.b64encode(data).decode())


def fixture():
    manifest, payload = LEGACY.fixture()
    value, _ = FIX.source(initial=True)
    registry = F.REGISTRY.validate_registry(base64.b64decode(value['registry']['data_base64']))
    parsed = F.MANIFEST.validate_manifest(manifest)
    parsed['guest_uuid'] = registry['storage']['guest_uuid']
    manifest = F.MANIFEST.canonical(parsed)
    config = parsed['config']
    identity = {key: config[key] for key in ('name', 'uid', 'gid')}
    head = F.HEAD.initial_head(registry['storage'], identity, sha(manifest))
    registry['guests'][0].update(identity, manifest_sha256=sha(manifest))
    value.update(registry=ref(F.REGISTRY.canonical(registry)), head=ref(F.HEAD.canonical(head)))
    lifecycle = F.canonical(value, manifest)
    return manifest, payload, lifecycle


def bundle(manifest, payload, lifecycle, change=None, tail=bytes(1024), suffix=b''):
    members = {'enrollment.json': manifest, 'checkpoint-history.json':
               F.BACKUP.history_payload({}, manifest), 'lifecycle.json': lifecycle, **payload}
    raw = bytearray()
    for name in F.NAMES:
        data = members[name]
        header = F.make_header(name, len(data))
        if change:
            header, data = change(name, header, data)
        raw.extend(header + data + bytes((-len(data)) % 512))
    raw.extend(tail)
    out = io.BytesIO()
    with gzip.GzipFile(filename='', mode='wb', fileobj=out, mtime=0) as stream:
        stream.write(raw)
    return out.getvalue() + suffix


class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.home(), prefix='managed-receiver-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manifest, self.payload, self.lifecycle = fixture()
        self.encoded = bundle(self.manifest, self.payload, self.lifecycle)
        self.archive = self.root / 'input.gz'
        self.archive.write_bytes(self.encoded)
        self.output = self.root / 'verified'
        mock = patch.object(R.STATE, 'validate_workspace_storage', return_value={'fixture': True})
        mock.start()
        self.addCleanup(mock.stop)

    def verify(self, encoded=None, archive_sha=None, manifest_sha=None):
        if encoded is not None:
            self.encoded = encoded
            self.archive.write_bytes(encoded)
        return R.verify(str(self.archive), archive_sha or sha(self.encoded),
                        manifest_sha or sha(self.manifest), str(self.output))

    def assert_clean(self):
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.root.glob('.guest-backup-*')), [])

    def fd_snapshot(self):
        result = {}
        for name in os.listdir('/proc/self/fd'):
            try:
                result[int(name)] = os.readlink('/proc/self/fd/' + name)
            except FileNotFoundError:
                pass
        return result

    def test_stage_constructor_recheck_and_open_failures_close_every_fd(self):
        real_open = os.open
        def opened(path, *args, **kwargs):
            if str(path).startswith('.guest-backup-'):
                raise OSError('constructor open')
            return real_open(path, *args, **kwargs)
        for fault in ('recheck', 'open'):
            with self.subTest(fault=fault):
                before = self.fd_snapshot()
                target = patch.object(R.Stage, 'recheck', side_effect=OSError('recheck')) \
                    if fault == 'recheck' else patch.object(R.os, 'open', side_effect=opened)
                with target:
                    result = self.verify()
                self.assertFalse(result['success'])
                self.assertEqual(result['error'], 'OSError')
                self.assertEqual(self.fd_snapshot(), before)
                self.assert_clean()

    def test_mkdir_lost_ack_retains_unknown_identity_without_fd_leak(self):
        real_mkdir = os.mkdir
        def created(path, *args, **kwargs):
            result = real_mkdir(path, *args, **kwargs)
            if str(path).startswith('.guest-backup-'):
                raise OSError('lost mkdir acknowledgment')
            return result
        before = self.fd_snapshot()
        with patch.object(R.os, 'mkdir', side_effect=created):
            result = self.verify()
        self.assertFalse(result['success'])
        self.assertEqual(result['error'], 'OSError')
        self.assertTrue(Path(result['retained_unknown_paths'][0]).is_dir())
        self.assertEqual(self.fd_snapshot(), before)

    def test_history_open_failure_cleans_tracked_created_directory(self):
        real_open = os.open
        def opened(path, *args, **kwargs):
            if path == '.checkpoints':
                raise OSError('history open')
            return real_open(path, *args, **kwargs)
        before = self.fd_snapshot()
        with patch.object(R.os, 'open', side_effect=opened):
            result = self.verify()
        self.assertFalse(result['success'])
        self.assertEqual(self.fd_snapshot(), before)
        self.assert_clean()

    def test_unknown_created_identity_retains_path_without_fd_leak(self):
        real_stat = os.stat
        def observed(path, *args, **kwargs):
            if str(path).startswith('.guest-backup-'):
                raise OSError('cannot identify created inode')
            return real_stat(path, *args, **kwargs)
        before = self.fd_snapshot()
        with patch.object(R.os, 'stat', side_effect=observed):
            result = self.verify()
        self.assertFalse(result['success'])
        self.assertEqual(result['error'], 'OSError')
        retained = result['retained_unknown_paths']
        self.assertEqual(len(retained), 1)
        self.assertTrue(Path(retained[0]).is_dir())
        self.assertEqual(self.fd_snapshot(), before)

    def test_history_unknown_identity_retained_and_cleanup_error_reported(self):
        real_stat = os.stat
        def observed(path, *args, **kwargs):
            if path == '.checkpoints':
                raise OSError('cannot identify history inode')
            return real_stat(path, *args, **kwargs)
        before = self.fd_snapshot()
        with patch.object(R.os, 'stat', side_effect=observed):
            result = self.verify()
        self.assertFalse(result['success'])
        self.assertEqual(result['error'], 'OSError')
        self.assertIn('cleanup_errors', result)
        self.assertTrue(Path(result['retained_unknown_paths'][0]).is_dir())
        self.assertEqual(self.fd_snapshot(), before)

    def test_constructor_cleanup_preserves_foreign_replacement(self):
        real_open = os.open
        foreign = []
        def opened(path, *args, **kwargs):
            if str(path).startswith('.guest-backup-'):
                parent = kwargs['dir_fd']
                os.rename(path, str(path) + '-owned', src_dir_fd=parent, dst_dir_fd=parent)
                os.mkdir(path, 0o700, dir_fd=parent)
                foreign.append(self.root / path)
                raise OSError('replacement before open')
            return real_open(path, *args, **kwargs)
        before = self.fd_snapshot()
        with patch.object(R.os, 'open', side_effect=opened):
            result = self.verify()
        self.assertFalse(result['success'])
        self.assertTrue(foreign[0].is_dir())
        self.assertEqual(self.fd_snapshot(), before)

    def test_cleanup_ack_failure_reports_retained_identity_without_fd_leak(self):
        real_close = R.Parent.close
        def closed(parent):
            real_close(parent)
            raise OSError('lost parent close acknowledgment')
        before = self.fd_snapshot()
        with patch.object(R.Parent, 'close', closed):
            result = self.verify()
        self.assertFalse(result['success'])
        self.assertTrue(result['published'] and result['durable'])
        self.assertEqual(result['cleanup_errors'], ['OSError'])
        self.assertEqual(tuple(result['retained_artifacts']['expected_identity']),
                         R.GENERIC.inode(self.output.stat()))
        self.assertEqual(self.fd_snapshot(), before)

    def test_final_stat_failure_retains_capture_and_primary_error_closes_fds(self):
        before = self.fd_snapshot()
        with patch.object(R, 'extract', side_effect=ValueError('primary extraction fault')), \
                patch.object(R, 'same', side_effect=OSError('final stat')):
            result = self.verify()
        self.assertFalse(result['success'])
        self.assertFalse(result['published'])
        self.assertTrue(result['publication_uncertain'])
        self.assertEqual(result['error'], 'ValueError')
        self.assertEqual(result['publication_observation_errors'], ['OSError'])
        retained = result['retained_artifacts']
        self.assertEqual(retained['output_path'], str(self.output))
        self.assertEqual(tuple(retained['expected_identity']),
                         R.GENERIC.inode(Path(retained['stage_path']).stat()))
        self.assertTrue((Path(retained['stage_path']) / 'archive.tar.gz').exists())
        self.assertEqual(self.fd_snapshot(), before)

    def test_final_stat_failure_preserves_acknowledged_publication_durability(self):
        real_same = R.same
        calls = []
        def observed(*args):
            calls.append(True)
            if len(calls) == 2:
                raise OSError('final observation after fsync')
            return real_same(*args)
        before = self.fd_snapshot()
        with patch.object(R, 'same', side_effect=observed):
            result = self.verify()
        self.assertFalse(result['success'])
        self.assertTrue(result['published'] and result['durable'])
        self.assertTrue(result['publication_uncertain'])
        self.assertTrue((self.output / 'disk.raw').exists())
        self.assertEqual(self.fd_snapshot(), before)

    def test_lost_rename_ack_preserves_primary_error_under_stat_failure(self):
        real_rename = R.rename_new
        def renamed(*args):
            real_rename(*args)
            raise RuntimeError('lost rename acknowledgment')
        before = self.fd_snapshot()
        with patch.object(R, 'rename_new', side_effect=renamed), \
                patch.object(R, 'same', side_effect=OSError('cannot observe rename')):
            result = self.verify()
        self.assertFalse(result['success'])
        self.assertFalse(result['published'] or result['durable'])
        self.assertEqual(result['error'], 'RuntimeError')
        self.assertTrue(result['publication_uncertain'])
        self.assertTrue((self.output / 'disk.raw').exists())
        self.assertEqual(self.fd_snapshot(), before)

    def test_full_profile_materialized_readonly(self):
        result = self.verify()
        self.assertTrue(result['success'], result)
        self.assertTrue(result['published'] and result['durable'])
        self.assertEqual(result['profile'], F.PROFILE)
        self.assertEqual(result['classification'], 'artifact-only')
        self.assertEqual((self.output / 'archive.tar.gz').read_bytes(), self.encoded)
        for name, data in {**self.payload, 'enrollment.json': self.manifest,
                           'lifecycle.json': self.lifecycle}.items():
            path = self.output / name
            self.assertEqual(path.read_bytes(), data)
            self.assertEqual(path.stat().st_mode & 0o777, 0o400)
        receipt = json.loads((self.output / 'backup-verification.json').read_bytes())
        self.assertTrue(receipt['artifact_checks_passed'])
        self.assertNotIn('published', receipt)

    def test_archive_authentication_before_any_decoder(self):
        with patch.object(S, 'scan_fd', side_effect=AssertionError('decoder called')) as scan:
            self.assertFalse(self.verify(archive_sha='0' * 64)['success'])
            scan.assert_not_called()
        self.assert_clean()

    def test_manifest_pin_and_payload_fault_cleanup(self):
        self.assertFalse(self.verify(manifest_sha='0' * 64)['success'])
        self.assert_clean()
        payload = dict(self.payload, Image=b'broken')
        self.assertFalse(self.verify(bundle(self.manifest, payload, self.lifecycle))['success'])
        self.assert_clean()

    def test_legal_cohort_decoder_before_payload_callback(self):
        value, manifest = FIX.source()  # deliberately invalid inert admission
        lifecycle = F.canonical(value, manifest)
        encoded = bundle(manifest, self.payload, lifecycle)
        fdpath = self.root / 'invalid.gz'
        fdpath.write_bytes(encoded)
        with fdpath.open('rb') as stream:
            emitted = []
            with self.assertRaises(ValueError):
                S.scan_fd(stream.fileno(), sha(manifest), lambda *item: emitted.append(item))
            self.assertEqual(emitted, [])

    def test_truncated_encoded_extra_member_suffix_and_tar_tail(self):
        for encoded in (self.encoded[:-4], self.encoded + self.encoded,
                        self.encoded + b'x',
                        bundle(self.manifest, self.payload, self.lifecycle, tail=bytes(1536)),
                        bundle(self.manifest, self.payload, self.lifecycle, tail=b'x' * 1024)):
            with self.subTest(encoded_size=len(encoded)):
                self.assertFalse(self.verify(encoded)['success'])
                self.assert_clean()

    def test_legacy_and_noncanonical_gzip_profile_refuse(self):
        legacy = LEGACY.bundle(self.manifest, self.payload)
        self.assertFalse(self.verify(legacy)['success'])
        changed = bytearray(self.encoded)
        changed[4] = 1
        self.assertFalse(self.verify(bytes(changed))['success'])
        self.assert_clean()

    def test_nonzero_padding_and_extra_tar_member_refuse(self):
        raw = bytearray(gzip.decompress(self.encoded))
        raw[512 + len(self.manifest)] = 1
        out = io.BytesIO()
        with gzip.GzipFile(filename='', mode='wb', fileobj=out, mtime=0) as stream:
            stream.write(raw)
        self.assertFalse(self.verify(out.getvalue())['success'])
        self.assert_clean()
        extra = F.make_header('disk.raw', 0) + bytes(1024)
        self.assertFalse(self.verify(bundle(self.manifest, self.payload, self.lifecycle,
                                           tail=extra))['success'])
        self.assert_clean()

    @unittest.skipUnless(os.geteuid() == 0, 'real protected root source metadata required')
    def test_source_replacement_and_payload_consumer_fault(self):
        self.archive.chmod(0o400)
        with S.Source(str(self.archive), sha(self.encoded), sha(self.manifest),
                      {self.archive.stat().st_dev}) as source:
            def fail(_name, _chunk):
                raise RuntimeError('discard unpublished bytes')
            with self.assertRaises(RuntimeError):
                source.scan(fail)
            source.recheck()
            replacement = self.root / 'replacement'
            replacement.write_bytes(self.encoded)
            replacement.chmod(0o400)
            replacement.replace(self.archive)
            with self.assertRaises(ValueError):
                source.scan()

    def test_no_replace_publication_and_race(self):
        self.output.mkdir()
        self.assertFalse(self.verify()['success'])
        self.output.rmdir()
        def raced(parent, _old, new):
            os.mkdir(new, dir_fd=parent)
            raise FileExistsError
        with patch.object(R, 'rename_new', side_effect=raced):
            self.assertFalse(self.verify()['success'])
        self.assertTrue(self.output.is_dir())
        self.assertEqual(list(self.root.glob('.guest-backup-*')), [])

    def test_partial_publication_is_preserved(self):
        real = R.rename_new
        def acted(*args):
            real(*args)
            raise OSError('post-rename fault')
        with patch.object(R, 'rename_new', side_effect=acted):
            result = self.verify()
        self.assertFalse(result['success'])
        self.assertTrue(result['published'])
        self.assertFalse(result['durable'])
        self.assertTrue((self.output / 'disk.raw').exists())

    @unittest.skipUnless(os.geteuid() == 0, 'real protected root source metadata required')
    def test_source_matching_archive_lock_lifetime_and_readonly_fds(self):
        import fcntl
        self.archive.chmod(0o400)
        device = self.archive.stat().st_dev
        with S.Source(str(self.archive), sha(self.encoded), sha(self.manifest), {device}) as source:
            heldfd = source.held.fd
            self.assertEqual(fcntl.fcntl(heldfd, fcntl.F_GETFL) & os.O_ACCMODE, os.O_RDONLY)
            collected = {name: bytearray() for name in self.payload}
            source.scan(lambda name, data: collected[name].extend(data))
            self.assertEqual(collected, self.payload)
            self.assertEqual(source.description['sha256'], sha(self.encoded))
            with self.archive.open('rb') as other:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(other.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            errors = []
            def alien():
                for action in (source.recheck, source.scan, source.close):
                    try:
                        action()
                    except ValueError:
                        errors.append(True)
            thread = threading.Thread(target=alien)
            thread.start()
            thread.join()
            self.assertEqual(len(errors), 3)
            with patch.object(S.os, 'getpid', return_value=os.getpid() + 1):
                with self.assertRaises(ValueError):
                    source.close()
            source.recheck()
        with self.assertRaises(OSError):
            os.fstat(heldfd)
        with self.archive.open('rb') as other:
            fcntl.flock(other.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @unittest.skipUnless(os.geteuid() == 0, 'real protected root source metadata required')
    def test_source_pins_before_decoder_and_device_mode_guards(self):
        self.archive.chmod(0o400)
        device = self.archive.stat().st_dev
        with patch.object(S, 'scan_fd', side_effect=AssertionError) as scan:
            with self.assertRaises(ValueError):
                S.Source(str(self.archive), '0' * 64, sha(self.manifest), {device})
            scan.assert_not_called()
        with self.assertRaises(ValueError):
            S.Source(str(self.archive), sha(self.encoded), sha(self.manifest), {device + 1})
        self.archive.chmod(0o600)
        with self.assertRaises(ValueError):
            S.Source(str(self.archive), sha(self.encoded), sha(self.manifest), {device})

    def test_root_guard_precedes_path_access(self):
        with patch.object(S.os, 'geteuid', return_value=60001), \
                patch.object(S.ENROLL, 'HeldPath', side_effect=AssertionError) as held:
            with self.assertRaises(ValueError):
                S.Source('/never/open', sha(self.encoded), sha(self.manifest), {1})
            held.assert_not_called()

    def test_exact_metadata_across_scans_and_callback_exception(self):
        with self.archive.open('rb') as stream:
            initial = S.scan_fd(stream.fileno(), sha(self.manifest))[0]
            with self.assertRaises(ValueError):
                S.scan_fd(stream.fileno(), sha(self.manifest), initial=(b'wrong', *initial[1:]))
            def fail(_name, _data):
                raise RuntimeError('unpublished consumer failure')
            with self.assertRaises(RuntimeError):
                S.scan_fd(stream.fileno(), sha(self.manifest), fail, initial)


SENDER_FIX = module('managed_receiver_sender_fixture', 'tests/test_managed_backup_sender.py')


@unittest.skipUnless(os.getresuid() == (0, 0, 0), 'protected producer requires root')
class CompletedProducer(unittest.TestCase):
    """Actual sender/cohort publication; platform observations explicitly mocked."""
    initial = False
    fixture_profile = 'none'
    patched = SENDER_FIX.Sender.patched
    setUp = SENDER_FIX.Sender.setUp
    send = SENDER_FIX.Sender.send

    def test_completed_sender_receiver_and_source(self):
        sent, writer = self.send()
        encoded = writer.getvalue()
        self.patched(R.STATE, 'validate_workspace_storage', return_value={'fixture': True})
        with tempfile.TemporaryDirectory(dir='/root', prefix='managed-completed-') as temp:
            archive, output = Path(temp) / 'input.gz', Path(temp) / 'verified'
            archive.write_bytes(encoded)
            result = R.verify(str(archive), sent['archive']['sha256'], self.pin, str(output))
            self.assertTrue(result['success'], result)
            archive.chmod(0o400)
            with S.Source(str(archive), sha(encoded), self.pin, {archive.stat().st_dev}) as source:
                self.assertIsNotNone(source.cohort['run'])
                received = {name: bytearray() for name in F.NAMES[3:]}
                source.scan(lambda name, chunk: received[name].extend(chunk))
                for name, data in received.items():
                    self.assertEqual(bytes(data), (output / name).read_bytes())
                self.assertEqual(source.lifecycle_data, (output / 'lifecycle.json').read_bytes())
                self.assertEqual(source.history, {path.name: path.read_bytes()
                                 for path in (output / '.checkpoints').iterdir()})


if __name__ == '__main__':
    unittest.main()
