"""Private backup file tests; no restore or hardware acceptance evidence."""
import base64
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import tempfile
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('backup_receiver_test', BASE / 'scripts/verify-guest-backup.py')
R = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(R)
F = R.FORMAT


def sha(data):
    return hashlib.sha256(data).hexdigest()


def fixture():
    payload = {'Image': b'kernel', 'initramfs.cpio.gz': b'initrd', 'disk.raw': bytes(512)}
    config = {'schema': 1, 'name': 'one', 'uid': 60001, 'gid': 60001,
              'memory_mib': 64, 'memory_max_mib': 128, 'cpus': 1}
    files = {}
    for key, name in F.MANIFEST.FILENAMES.items():
        config[key] = R.record(payload[name])
        files[name] = {**config[key], 'uid': 60001 if key == 'disk' else 0,
                       'gid': 60001 if key == 'disk' else 0,
                       'mode': '0600' if key == 'disk' else '0444'}
    value = {'schema': 1, 'profile': F.MANIFEST.PROFILE, 'config': config,
             'guest_uuid': '7811c43d-01e4-4635-83b4-25d276172ac0', 'files': files}
    return F.MANIFEST.canonical(value), payload


def bundle(manifest, payload, history=None, change=None, tail=bytes(1024), suffix=b''):
    members = {'enrollment.json': manifest, 'checkpoint-history.json':
               F.history_payload(history or {}, manifest), **payload}
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


class BackupFormat(unittest.TestCase):
    def test_large_gnu_header(self):
        size = 20 * 1024**3
        header = F.make_header('disk.raw', size)
        self.assertEqual(F.read_header(io.BytesIO(header), 'disk.raw', size), size)
        self.assertEqual(len(header), 512)

    def test_headers_reject_noncanonical_metadata_and_checksum(self):
        header = bytearray(F.make_header('disk.raw', 512))
        header[0] = ord('/')
        with self.assertRaises(ValueError):
            F.read_header(io.BytesIO(header), 'disk.raw', 512)
        with self.assertRaises(ValueError):
            F.read_header(io.BytesIO(F.make_header('Image', 2)), 'Image', 1)

    def test_complete_noncanonical_history_and_orphan_preservation(self):
        raw, _ = fixture()
        old = F.MANIFEST.validate_manifest(raw)
        raw = json.dumps(old, indent=2).encode()
        new = F.MANIFEST.proposed_manifest(old, sha(raw), {'bytes': 512, 'sha256': 'a' * 64})
        new_raw = F.MANIFEST.canonical(new)
        def archive(old_raw, new_value):
            old_value = F.MANIFEST.validate_manifest(old_raw)
            new_bytes = F.MANIFEST.canonical(new_value)
            data = F.canonical({'schema': 1, 'operation': 'a733-guest-checkpoint-v1',
                                'old_manifest': old_value, 'new_manifest': new_value,
                                'old_manifest_sha256': sha(old_raw), 'new_manifest_sha256': sha(new_bytes),
                                'old_manifest_base64': base64.b64encode(old_raw).decode(),
                                'new_manifest_base64': base64.b64encode(new_bytes).decode()})
            return sha(old_raw) + '-' + sha(new_bytes) + '.json', data
        name, data = archive(raw, new)
        orphan = F.MANIFEST.proposed_manifest(old, sha(raw), {'bytes': 512, 'sha256': 'b' * 64})
        orphan_name, orphan_data = archive(raw, orphan)
        entries = {name: data, orphan_name: orphan_data}
        envelope = F.history_payload(entries, new_raw)
        self.assertEqual(F.validate_history(envelope, json.dumps(new).encode()), entries)
        with self.assertRaises(ValueError):
            F.history_payload({orphan_name: orphan_data}, new_raw)
        malformed = json.loads(data)
        malformed['new_manifest']['config']['cpus'] = 2
        with self.assertRaises(ValueError):
            F.history_payload({name: F.canonical(malformed)}, new_raw)

    def test_history_caps_and_order(self):
        raw, _ = fixture()
        with self.assertRaises(ValueError):
            F.validate_history(b'x' * (F.HISTORY_LIMIT + 1), raw)
        with self.assertRaises(ValueError):
            F.history_payload({'a' * 64 + '-' + 'b' * 64 + '.json': b'x' * (F.ENTRY_LIMIT + 1)}, raw)
        value = {'schema': 1, 'profile': F.HISTORY_PROFILE, 'entries': [
            {'name': 'b' * 64 + '-' + 'a' * 64 + '.json', 'data_base64': ''},
            {'name': 'a' * 64 + '-' + 'b' * 64 + '.json', 'data_base64': ''}]}
        with self.assertRaises(ValueError):
            F.validate_history(F.canonical(value), raw)


class BackupReceiver(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.home(), prefix='backup-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manifest, self.payload = fixture()
        self.encoded = bundle(self.manifest, self.payload)
        self.archive = self.root / 'input.gz'
        self.archive.write_bytes(self.encoded)
        self.output = self.root / 'verified'
        mock = patch.object(R.STATE, 'validate_workspace_storage', return_value={'fixture': True})
        mock.start()
        self.addCleanup(mock.stop)

    def verify(self, encoded=None, **kwargs):
        if encoded is not None:
            self.encoded = encoded
            self.archive.write_bytes(encoded)
        return R.verify(str(self.archive), kwargs.get('archive_sha', sha(self.encoded)),
                        kwargs.get('manifest_sha', sha(self.manifest)), str(self.output))

    def test_full_small_backup_and_independent_files(self):
        result = self.verify()
        self.assertTrue(result['success'], result)
        self.assertTrue(result['published'] and result['durable'])
        self.assertEqual(result['classification'], 'artifact-only')
        self.assertEqual((self.output / 'archive.tar.gz').read_bytes(), self.encoded)
        self.assertEqual((self.output / 'enrollment.json').read_bytes(), self.manifest)
        for name, data in self.payload.items():
            self.assertEqual((self.output / name).read_bytes(), data)
            self.assertEqual((self.output / name).stat().st_mode & 0o777, 0o400)
        self.assertEqual(list((self.output / '.checkpoints').iterdir()), [])

    def test_storage_guard_uses_receiver_pid_held_parent(self):
        seen = []
        def storage(path):
            seen.append(path)
            self.assertEqual(path.parts[:4], ('/', 'proc', str(os.getpid()), 'fd'))
            self.assertEqual(path.resolve(), self.root)
            return {'fixture': True}
        with patch.object(R.STATE, 'validate_workspace_storage', side_effect=storage):
            self.assertTrue(self.verify()['success'])
        self.assertEqual(len(seen), 1)

    def test_verification_file_does_not_claim_publication(self):
        self.assertTrue(self.verify()['success'])
        value = json.loads((self.output / 'backup-verification.json').read_bytes())
        self.assertTrue(value['artifact_checks_passed'])
        self.assertFalse({'success', 'published', 'durable'} & set(value))

    def test_authentication_precedes_decoder_and_manifest_parser(self):
        with patch.object(R, 'extract', side_effect=AssertionError('parser invoked')):
            result = self.verify(archive_sha='0' * 64)
        self.assertFalse(result['success'])
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.root.iterdir()), [self.archive])

    def test_manifest_external_pin_and_disk_digest(self):
        self.assertFalse(self.verify(manifest_sha='0' * 64)['success'])
        altered = {**self.payload, 'disk.raw': b'A' * 512}
        self.assertFalse(self.verify(bundle(self.manifest, altered))['success'])
        self.assertFalse(self.output.exists())

    def test_tar_path_pax_and_tail(self):
        import tarfile
        for kind in ('path', 'pax', 'symlink'):
            def change(name, header, data):
                if name == 'Image':
                    item = tarfile.TarInfo('../Image' if kind == 'path' else name)
                    item.size = len(data)
                    item.type = {'path': tarfile.REGTYPE, 'pax': tarfile.XHDTYPE,
                                 'symlink': tarfile.SYMTYPE}[kind]
                    header = item.tobuf(format=tarfile.GNU_FORMAT)
                return header, data
            self.assertFalse(self.verify(bundle(self.manifest, self.payload, change=change))['success'])
        for tail in (bytes(512), bytes(10752), bytes(1024) + b'x' * 512):
            self.assertFalse(self.verify(bundle(self.manifest, self.payload, tail=tail))['success'])
        for suffix in (b'\x00', gzip.compress(b''), b'trailer'):
            self.assertFalse(self.verify(bundle(self.manifest, self.payload, suffix=suffix))['success'])

    def test_existing_symlink_and_unsafe_parent(self):
        self.output.symlink_to(self.archive)
        self.assertFalse(self.verify()['success'])
        self.assertTrue(self.output.is_symlink())
        self.output.unlink()
        self.root.chmod(0o777)
        self.assertFalse(self.verify()['success'])
        self.root.chmod(0o700)

    def test_atomic_unavailable_and_publication_race(self):
        with patch.object(R, 'rename_new', side_effect=OSError('unsupported')):
            self.assertFalse(self.verify()['success'])
        def raced(parent, old, new):
            fd = os.open(new, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent)
            os.write(fd, b'foreign')
            os.close(fd)
            raise FileExistsError
        with patch.object(R, 'rename_new', side_effect=raced):
            result = self.verify()
        self.assertFalse(result['published'])
        self.assertEqual(self.output.read_bytes(), b'foreign')

    def test_post_rename_error_preserves_output(self):
        rename = R.rename_new
        def acted(parent, old, new):
            rename(parent, old, new)
            raise OSError('after rename')
        with patch.object(R, 'rename_new', side_effect=acted):
            result = self.verify()
        self.assertFalse(result['success'])
        self.assertTrue(result['published'])
        self.assertTrue((self.output / 'disk.raw').exists())

    def test_parent_fsync_failure_reports_visible_without_durability(self):
        original = R.os.fsync
        def fsync(fd):
            # Parent is the trusted fixture directory, not a staging directory.
            if os.path.samefile('/proc/self/fd/' + str(fd), self.root):
                raise OSError('parent fsync failed')
            return original(fd)
        with patch.object(R.os, 'fsync', side_effect=fsync):
            result = self.verify()
        self.assertFalse(result['success'])
        self.assertTrue(result['published'])
        self.assertFalse(result['durable'])
        self.assertTrue((self.output / 'disk.raw').exists())

    def test_signal_after_real_rename_retains_publication(self):
        rename = R.rename_new
        def acted(parent, old, new):
            rename(parent, old, new)
            os.kill(os.getpid(), signal.SIGINT)
        with patch.object(R, 'rename_new', side_effect=acted):
            result = self.verify()
        self.assertFalse(result['success'])
        self.assertTrue(result['published'])
        self.assertTrue(result['durable'])
        self.assertTrue((self.output / 'disk.raw').exists())

    def test_capture_refuses_hardlink_and_encoded_size_cap(self):
        linked = self.root / 'linked'
        os.link(self.archive, linked)
        self.assertFalse(self.verify()['success'])
        linked.unlink()
        with patch.object(R, 'MAX_ARCHIVE', len(self.encoded) - 1):
            self.assertFalse(self.verify()['success'])
        self.assertFalse(self.output.exists())

    def test_no_foreign_cleanup(self):
        original = R.extract
        foreign = []
        def replaced(stage, *args):
            files, count = original(stage, *args)
            os.unlink('Image', dir_fd=stage.fd)
            fd = os.open('Image', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=stage.fd)
            os.write(fd, b'foreign')
            os.close(fd)
            foreign.append(stage.name)
            return files, count
        with patch.object(R, 'extract', side_effect=replaced):
            result = self.verify()
        self.assertFalse(result['success'])
        self.assertEqual((self.root / foreign[0] / 'Image').read_bytes(), b'foreign')

    def test_free_space_checked_before_capture_and_logical_extract(self):
        with patch.object(R, 'free_space', side_effect=ValueError('space')):
            self.assertFalse(self.verify()['success'])
        calls = []
        def space(fd, needed):
            calls.append(needed)
            if len(calls) == 2:
                raise ValueError('logical space')
        with patch.object(R, 'free_space', side_effect=space):
            self.assertFalse(self.verify()['success'])
        self.assertEqual(len(calls), 2)


if __name__ == '__main__':
    unittest.main()
