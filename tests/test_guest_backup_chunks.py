"""Encoded chunk index tests; these establish no hardware restore acceptance."""
import copy
import fcntl
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
SPEC = importlib.util.spec_from_file_location('test_backup_indexer', BASE / 'scripts/index-guest-backup.py')
I = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(I)
C = I.CHUNKS
R = I.RECEIVER
F = I.FORMAT


def sha(data):
    return hashlib.sha256(data).hexdigest()


def manifest_fixture():
    payload = {'Image': b'kernel', 'initramfs.cpio.gz': b'initrd', 'disk.raw': bytes(512)}
    config = {'schema': 1, 'name': 'one', 'uid': 60001, 'gid': 60001,
              'memory_mib': 64, 'memory_max_mib': 128, 'cpus': 1}
    files = {}
    for key, name in F.MANIFEST.FILENAMES.items():
        config[key] = R.record(payload[name])
        files[name] = {**config[key], 'uid': 60001 if key == 'disk' else 0,
                       'gid': 60001 if key == 'disk' else 0,
                       'mode': '0600' if key == 'disk' else '0444'}
    return F.MANIFEST.canonical({'schema': 1, 'profile': F.MANIFEST.PROFILE, 'config': config,
                                 'guest_uuid': '7811c43d-01e4-4635-83b4-25d276172ac0',
                                 'files': files})


def encoded_fixture(manifest):
    # The generator intentionally authenticates only the first member after the
    # encoded hash: it requires prior full receiver verification of the backup.
    raw = F.make_header('enrollment.json', len(manifest)) + manifest + bytes(1024)
    output = io.BytesIO()
    with gzip.GzipFile(filename='', mode='wb', fileobj=output, mtime=0) as stream:
        stream.write(raw)
    return output.getvalue()


class ChunkFormat(unittest.TestCase):
    def setUp(self):
        self.data = C.make({'bytes': C.CHUNK + 1, 'sha256': 'a' * 64}, 'b' * 64,
                           ['c' * 64, 'd' * 64])
        self.value = C.validate(self.data, sha(self.data))

    def test_roundtrip_and_final_short_chunk(self):
        self.assertEqual(C.canonical(self.value), self.data)
        self.assertEqual(self.value['archive']['bytes'] % C.CHUNK, 1)

    def test_encoded_pin_checked_before_json(self):
        with patch.object(C.json, 'loads', side_effect=AssertionError('JSON parsed')):
            with self.assertRaises(ValueError):
                C.validate(b'not json', '0' * 64)

    def test_exact_schema_types_and_bounds(self):
        cases = []
        for key, value in [('schema', True), ('chunk_bytes', True), ('chunk_bytes', C.CHUNK - 1),
                           ('profile', 'other'), ('chunks', ['a' * 64]),
                           ('chunks', ['A' * 64, 'd' * 64]), ('manifest_sha256', 'g' * 64)]:
            item = copy.deepcopy(self.value)
            item[key] = value
            cases.append(item)
        for size in (True, 0, -1, C.MAX_ARCHIVE + 1, 1.0):
            item = copy.deepcopy(self.value)
            item['archive']['bytes'] = size
            cases.append(item)
        item = copy.deepcopy(self.value)
        item['extra'] = 1
        cases.append(item)
        item = copy.deepcopy(self.value)
        item['archive']['extra'] = 1
        cases.append(item)
        for value in cases:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    C.validate(C.canonical(value))

    def test_noncanonical_duplicate_nonfinite_and_oversized(self):
        cases = [self.data[:-1], b' ' + self.data,
                 self.data.replace(b'"schema":1', b'"schema":1,"schema":1'),
                 self.data.replace(b'"schema":1', b'"schema":NaN'),
                 b'x' * (C.LIMIT + 1), 'text', b'\xff']
        for data in cases:
            with self.assertRaises(ValueError):
                C.validate(data)

    def test_maximum_count_fits_limit_and_wrong_count_refused(self):
        count = (C.MAX_ARCHIVE + C.CHUNK - 1) // C.CHUNK
        data = C.make({'bytes': C.MAX_ARCHIVE, 'sha256': 'a' * 64}, 'b' * 64,
                      ['c' * 64] * count)
        self.assertLess(len(data), C.LIMIT)
        self.assertEqual(len(C.validate(data)['chunks']), 257024)
        value = C.validate(self.data)
        value['chunks'].append('e' * 64)
        with self.assertRaises(ValueError):
            C.validate(C.canonical(value))


class BackupIndexer(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.home(), prefix='backup-index-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manifest = manifest_fixture()
        self.encoded = encoded_fixture(self.manifest)
        self.archive = self.root / 'archive.gz'
        self.archive.write_bytes(self.encoded)
        self.archive.chmod(0o400)
        self.output = self.root / 'chunks.json'
        mock = patch.object(R.STATE, 'validate_workspace_storage', return_value={'fixture': True})
        mock.start()
        self.addCleanup(mock.stop)

    def run_index(self, **kwargs):
        return I.index(str(kwargs.get('archive', self.archive)),
                       kwargs.get('archive_sha256', sha(self.encoded)),
                       kwargs.get('manifest_sha256', sha(self.manifest)), str(self.output))

    def test_published_index_actual_files_and_two_held_storage_checks(self):
        paths = []
        def storage(path):
            self.assertEqual(path.parts[:4], ('/', 'proc', str(os.getpid()), 'fd'))
            paths.append(path.resolve())
            return {'fixture': True}
        with patch.object(R.STATE, 'validate_workspace_storage', side_effect=storage):
            result = self.run_index()
        self.assertTrue(result['success'], result)
        self.assertTrue(result['published'] and result['durable'])
        self.assertEqual(paths, [self.root, self.archive])
        data = self.output.read_bytes()
        parsed = C.validate(data, result['index']['sha256'])
        self.assertEqual(parsed['chunks'], [sha(self.encoded)])
        self.assertEqual(parsed['archive'], R.record(self.encoded))
        self.assertEqual(parsed['manifest_sha256'], sha(self.manifest))
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o400)

    def test_archive_pin_checked_before_decoder(self):
        with patch.object(R, 'GzipReader', side_effect=AssertionError('decoder invoked')):
            result = self.run_index(archive_sha256='0' * 64)
        self.assertFalse(result['success'])
        self.assertEqual(result['error'], 'ValueError')
        self.assertEqual(list(self.root.iterdir()), [self.archive])

    def test_manifest_pin_checked_before_json(self):
        with patch.object(F.MANIFEST, 'validate_manifest', side_effect=AssertionError('JSON parsed')):
            result = self.run_index(manifest_sha256='0' * 64)
        self.assertFalse(result['success'])
        self.assertEqual(result['error'], 'ValueError')

    def test_existing_output_file_directory_and_symlink(self):
        self.output.write_bytes(b'foreign')
        self.assertFalse(self.run_index()['success'])
        self.assertEqual(self.output.read_bytes(), b'foreign')
        self.output.unlink()
        self.output.mkdir()
        self.assertFalse(self.run_index()['success'])
        self.output.rmdir()
        self.output.symlink_to(self.archive)
        self.assertFalse(self.run_index()['success'])
        self.assertTrue(self.output.is_symlink())

    def test_source_symlink_hardlink_permissions_and_unsafe_parent(self):
        linked = self.root / 'linked'
        linked.symlink_to(self.archive)
        self.assertFalse(self.run_index(archive=linked)['success'])
        linked.unlink()
        os.link(self.archive, linked)
        self.assertFalse(self.run_index()['success'])
        linked.unlink()
        self.archive.chmod(0o600)
        self.assertFalse(self.run_index()['success'])
        self.archive.chmod(0o400)
        self.root.chmod(0o777)
        self.assertFalse(self.run_index()['success'])
        self.root.chmod(0o700)

    def test_partial_reads_assemble_full_chunks(self):
        # Test the scanner without decoding a much larger archive: SHA boundaries
        # must follow the format, regardless of short reads from the descriptor.
        self.archive.chmod(0o600)
        raw = b'x' * (C.CHUNK + 173)
        self.archive.write_bytes(raw)
        self.archive.chmod(0o400)
        original = I.os.read
        source = I.Source(str(self.archive))
        try:
            with patch.object(I.os, 'read', side_effect=lambda fd, size: original(fd, min(size, 117))):
                hashes = I.scan(source, sha(raw))
        finally:
            source.close()
        self.assertEqual(hashes, [sha(raw[:C.CHUNK]), sha(raw[C.CHUNK:])])

    def test_changed_source_during_scan_is_refused(self):
        # Make the observed metadata change deterministic even when the builder's
        # filesystem timestamps share one clock tick with an identical rewrite.
        os.utime(self.archive, ns=(1000000000, 1000000000))
        original = I.os.read
        changed = []
        def read(fd, size):
            data = original(fd, size)
            if not changed and data:
                changed.append(True)
                self.archive.chmod(0o600)
                self.archive.write_bytes(self.encoded)
                self.archive.chmod(0o400)
            return data
        with patch.object(I.os, 'read', side_effect=read):
            result = self.run_index()
        self.assertFalse(result['success'])
        self.assertFalse(self.output.exists())

    def test_truncation_size_bound_and_cooperative_lock(self):
        with patch.object(I.os, 'read', return_value=b''):
            self.assertFalse(self.run_index()['success'])
        with patch.object(C, 'MAX_ARCHIVE', len(self.encoded) - 1):
            self.assertFalse(self.run_index()['success'])
        locked = os.open(self.archive, os.O_RDONLY)
        try:
            fcntl.flock(locked, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertFalse(self.run_index()['success'])
        finally:
            os.close(locked)
        self.assertFalse(self.output.exists())

    def test_source_ancestor_symlink_and_foreign_owner(self):
        link = self.root / 'directory-link'
        link.symlink_to(self.root, target_is_directory=True)
        self.assertFalse(self.run_index(archive=link / self.archive.name)['success'])
        if os.geteuid() == 0:
            os.chown(self.archive, 60002, 60002)
            try:
                self.assertFalse(self.run_index()['success'])
            finally:
                os.chown(self.archive, 0, 0)

    def test_fixed_storage_guard_rejects_source_before_hash(self):
        calls = []
        def storage(path):
            calls.append(path.resolve())
            if len(calls) == 2:
                raise ValueError('source storage refused')
            return {'fixture': True}
        with patch.object(R.STATE, 'validate_workspace_storage', side_effect=storage), \
                patch.object(I, 'scan', side_effect=AssertionError('hash started')):
            result = self.run_index()
        self.assertEqual(result.get('error'), 'ValueError')
        self.assertEqual(calls, [self.root, self.archive])

    def test_first_member_name_and_canonical_gzip_header(self):
        raw = F.make_header('Image', len(self.manifest)) + self.manifest
        for encoded in (gzip.compress(raw, mtime=0), b'bad gzip header'):
            self.archive.chmod(0o600)
            self.archive.write_bytes(encoded)
            self.archive.chmod(0o400)
            result = self.run_index(archive_sha256=sha(encoded))
            self.assertFalse(result['success'])
            self.assertFalse(self.output.exists())

    def test_queued_interrupt_after_rename_keeps_durable_output(self):
        rename = R.rename_new
        def acted(parent, old, new):
            rename(parent, old, new)
            os.kill(os.getpid(), signal.SIGINT)
        with patch.object(R, 'rename_new', side_effect=acted):
            result = self.run_index()
        self.assertFalse(result['success'])
        self.assertTrue(result['published'] and result['durable'])
        self.assertTrue(self.output.exists())

    def test_rename_acted_then_error_reconciles_publication(self):
        rename = R.rename_new
        def acted(parent, old, new):
            rename(parent, old, new)
            raise OSError('after rename')
        with patch.object(R, 'rename_new', side_effect=acted):
            result = self.run_index()
        self.assertFalse(result['success'])
        self.assertTrue(result['published'])
        self.assertFalse(result['durable'])

    def test_parent_fsync_failure_reports_visible_without_durability(self):
        original = I.os.fsync
        def fsync(fd):
            if os.path.samefile('/proc/self/fd/' + str(fd), self.root):
                raise OSError('directory sync failed')
            return original(fd)
        with patch.object(I.os, 'fsync', side_effect=fsync):
            result = self.run_index()
        self.assertFalse(result['success'])
        self.assertTrue(result['published'])
        self.assertFalse(result['durable'])

    def test_foreign_publication_is_never_removed(self):
        def raced(parent, old, new):
            fd = os.open(new, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent)
            os.write(fd, b'foreign')
            os.close(fd)
            raise FileExistsError
        with patch.object(R, 'rename_new', side_effect=raced):
            result = self.run_index()
        self.assertFalse(result['published'])
        self.assertEqual(self.output.read_bytes(), b'foreign')
        self.assertEqual(set(self.root.iterdir()), {self.output, self.archive})

    def test_foreign_temporary_is_never_removed(self):
        original = R.write_all
        foreign = []
        def replaced(fd, data):
            original(fd, data)
            path = next(self.root.glob('.guest-backup-index-*'))
            path.unlink()
            path.write_bytes(b'foreign')
            foreign.append(path)
        with patch.object(R, 'write_all', side_effect=replaced):
            result = self.run_index()
        self.assertFalse(result['success'])
        self.assertFalse(result['published'])
        self.assertEqual(foreign[0].read_bytes(), b'foreign')

    def test_atomic_rename_unavailable_cleans_owned_temporary(self):
        with patch.object(R, 'rename_new', side_effect=OSError('unsupported')):
            self.assertFalse(self.run_index()['success'])
        self.assertEqual(list(self.root.iterdir()), [self.archive])

    def test_storage_refusal_and_file_sync_failure_cleanup(self):
        with patch.object(R.STATE, 'validate_workspace_storage', side_effect=ValueError('storage')):
            self.assertFalse(self.run_index()['success'])
        with patch.object(I.os, 'fsync', side_effect=OSError('sync failed')):
            self.assertFalse(self.run_index()['success'])
        self.assertEqual(list(self.root.iterdir()), [self.archive])


if __name__ == '__main__':
    unittest.main()
