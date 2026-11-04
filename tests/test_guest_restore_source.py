"""Protected read-only restore source fixtures; no hardware acceptance evidence."""
import base64
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, BASE / path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


S = load('restore_source_test', 'scripts/guest-restore-source.py')
T = load('restore_source_fixtures', 'tests/test_guest_backup_receiver.py')
F = S.FORMAT


@unittest.skipUnless(os.geteuid() == 0, 'protected /run source fixtures require root')
class RestoreSource(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/run', prefix='restore-source-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manifest, self.payload = T.fixture()
        self.archive = self.root / 'archive.tar.gz'
        self.set_archive(T.bundle(self.manifest, self.payload))

    def set_archive(self, encoded):
        self.encoded = encoded
        self.archive.write_bytes(encoded)
        self.archive.chmod(0o400)

    def source(self, **kwargs):
        return S.Source(str(kwargs.get('path', self.archive)),
                        kwargs.get('archive_sha', T.sha(self.encoded)),
                        kwargs.get('manifest_sha', T.sha(self.manifest)),
                        kwargs.get('devices', {self.archive.stat().st_dev}))

    def test_repeat_exact_payloads_and_no_source_changes(self):
        before = S.ENROLL.identity(self.archive.stat())
        with self.source() as source:
            self.assertEqual(source.archive_record, T.R.record(self.encoded))
            self.assertEqual(source.manifest_data, self.manifest)
            self.assertEqual(source.manifest, F.MANIFEST.validate_manifest(self.manifest))
            self.assertEqual(source.history, {})
            self.assertEqual(source.history_data, F.history_payload({}, self.manifest))
            for _ in range(2):
                received = {name: bytearray() for name in self.payload}
                def emit(name, chunk):
                    self.assertIn(name, self.payload)
                    self.assertLessEqual(len(chunk), S.CHUNK)
                    received[name].extend(chunk)
                source.scan(emit)
                self.assertEqual(received, self.payload)
            source.recheck()
        self.assertEqual(S.ENROLL.identity(self.archive.stat()), before)
        self.assertEqual(self.archive.read_bytes(), self.encoded)
        self.assertEqual(list(self.root.iterdir()), [self.archive])
        source.close()
        with self.assertRaises(ValueError):
            source.recheck()

    def test_preserves_complete_history_bytes(self):
        old_raw = json.dumps(F.MANIFEST.validate_manifest(self.manifest), indent=2).encode()
        old = F.MANIFEST.validate_manifest(old_raw)
        new = F.MANIFEST.proposed_manifest(old, T.sha(old_raw),
                                         {'bytes': 512, 'sha256': T.sha(b'A' * 512)})
        new_raw = F.MANIFEST.canonical(new)
        entry = F.canonical({'schema': 1, 'operation': 'a733-guest-checkpoint-v1',
                             'old_manifest': old, 'new_manifest': new,
                             'old_manifest_sha256': T.sha(old_raw),
                             'new_manifest_sha256': T.sha(new_raw),
                             'old_manifest_base64': base64.b64encode(old_raw).decode(),
                             'new_manifest_base64': base64.b64encode(new_raw).decode()})
        history = {T.sha(old_raw) + '-' + T.sha(new_raw) + '.json': entry}
        self.manifest = new_raw
        self.payload['disk.raw'] = b'A' * 512
        self.set_archive(T.bundle(new_raw, self.payload, history))
        with self.source() as source:
            self.assertEqual(source.history, history)
            self.assertEqual(source.history_data, F.history_payload(history, new_raw))
            source.scan(lambda _name, _chunk: None)

    def test_payload_emission_is_bounded_to_one_mib(self):
        self.payload['disk.raw'] = bytes(2 * S.CHUNK + 512)
        manifest = F.MANIFEST.validate_manifest(self.manifest)
        record = T.R.record(self.payload['disk.raw'])
        manifest['config']['disk'] = record
        manifest['files']['disk.raw'].update(record)
        self.manifest = F.MANIFEST.canonical(manifest)
        self.set_archive(T.bundle(self.manifest, self.payload))
        with self.source() as source:
            sizes = []
            def emit(name, chunk):
                self.assertLessEqual(len(chunk), S.CHUNK)
                if name == 'disk.raw':
                    sizes.append(len(chunk))
            source.scan(emit)
            self.assertEqual(sizes, [S.CHUNK, S.CHUNK, 512])

    def test_encoded_authentication_before_any_parser(self):
        with patch.object(S.RECEIVER, 'GzipReader', side_effect=AssertionError('gzip')), \
                patch.object(F, 'read_header', side_effect=AssertionError('tar')), \
                patch.object(F.MANIFEST, 'validate_manifest', side_effect=AssertionError('JSON')):
            with self.assertRaisesRegex(ValueError, 'SHA-256'):
                self.source(archive_sha='0' * 64)

    def test_manifest_pin_before_manifest_json(self):
        with patch.object(F.MANIFEST, 'validate_manifest', side_effect=AssertionError('JSON')):
            with self.assertRaisesRegex(ValueError, 'manifest digest'):
                self.source(manifest_sha='0' * 64)

    def test_authentication_metadata_change_is_detected_before_decoding(self):
        original = S.ENROLL.hash_source
        def changed(held, expected):
            original(held, expected)
            self.archive.chmod(0o600)
        with patch.object(S.ENROLL, 'hash_source', side_effect=changed), \
                patch.object(S.RECEIVER, 'GzipReader', side_effect=AssertionError('gzip')):
            with self.assertRaisesRegex(ValueError, 'held input changed'):
                self.source()

    def test_bad_payload_truncation_header_suffix_and_footer(self):
        bad_payload = {**self.payload, 'disk.raw': b'A' * 512}
        def bad_header(name, header, data):
            return (b'x' + header[1:] if name == 'Image' else header), data
        def bad_padding(name, header, data):
            # A declared short member leaves nonzero bytes in its padding.
            return (F.make_header(name, 1) if name == 'Image' else header), data
        bad = [T.bundle(self.manifest, bad_payload), self.encoded[:-8],
               T.bundle(self.manifest, self.payload, change=bad_header),
               T.bundle(self.manifest, self.payload, change=bad_padding),
               T.bundle(self.manifest, self.payload, suffix=b'x'),
               T.bundle(self.manifest, self.payload, suffix=self.encoded),
               T.bundle(self.manifest, self.payload, tail=bytes(512)),
               T.bundle(self.manifest, self.payload, tail=bytes(10752)),
               T.bundle(self.manifest, self.payload, tail=bytes(1024) + b'x' * 512)]
        gzip_header = bytearray(self.encoded)
        gzip_header[9] = 3
        bad.append(bytes(gzip_header))
        for encoded in bad:
            with self.subTest(encoded=T.sha(encoded)):
                self.set_archive(encoded)
                with self.assertRaises((ValueError, S.RECEIVER.zlib.error)):
                    self.source()

    def test_callback_failure_never_returns_success(self):
        with self.source() as source:
            def emit(_name, _chunk):
                raise RuntimeError('caller failed')
            with self.assertRaisesRegex(RuntimeError, 'caller failed'):
                source.scan(emit)
            source.scan()

    def test_short_reads_are_handled_but_early_eof_fails(self):
        read = os.read
        with self.source() as source:
            with patch.object(S.os, 'read', side_effect=lambda fd, size: read(fd, min(size, 7))):
                source.scan()
            with patch.object(S.os, 'read', return_value=b''):
                with self.assertRaisesRegex(ValueError, 'truncated'):
                    source.scan()

    def test_mutation_during_emit_is_detected(self):
        with self.source() as source:
            def emit(_name, _chunk):
                self.archive.chmod(0o600)
            with self.assertRaisesRegex(ValueError, 'held input changed'):
                source.scan(emit)

    def test_named_path_replacement_is_detected(self):
        with self.source() as source:
            self.archive.rename(self.root / 'original')
            self.archive.write_bytes(self.encoded)
            self.archive.chmod(0o400)
            with self.assertRaisesRegex(ValueError, 'named path changed'):
                source.scan()

    def test_shared_lock_retained_until_close_and_exclusive_refused(self):
        fd = os.open(self.archive, os.O_RDONLY)
        self.addCleanup(os.close, fd)
        with self.source():
            with self.assertRaises(BlockingIOError):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with self.assertRaises(BlockingIOError):
            self.source()
        fcntl.flock(fd, fcntl.LOCK_UN)

    def test_unsafe_sources_refused_before_hashing(self):
        link = self.root / 'link'
        link.symlink_to(self.archive)
        with patch.object(S.ENROLL, 'hash_source', side_effect=AssertionError('hash called')):
            with self.assertRaises(OSError):
                self.source(path=link)
            link.unlink()
            os.link(self.archive, link)
            with self.assertRaisesRegex(ValueError, 'unsafe source'):
                self.source()
            link.unlink()
            self.archive.chmod(0o444)
            with self.assertRaisesRegex(ValueError, 'unsafe source'):
                self.source()
            self.archive.chmod(0o400)
            with self.assertRaisesRegex(ValueError, 'selected NVMe'):
                self.source(devices=set())
            with patch.object(S, 'MAX_ARCHIVE', len(self.encoded) - 1):
                with self.assertRaisesRegex(ValueError, 'archive size'):
                    self.source()

    def test_constructor_failure_releases_all_descriptors(self):
        before = set(os.listdir('/proc/self/fd'))
        with self.assertRaises(ValueError):
            self.source(archive_sha='0' * 64)
        self.assertEqual(set(os.listdir('/proc/self/fd')), before)


if __name__ == '__main__':
    unittest.main()
