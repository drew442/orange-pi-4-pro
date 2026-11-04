"""Private file/stream sender checks; no board, boot or clean shutdown evidence."""
import base64
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('guest_backup_sender', BASE / 'scripts/backup-guest.py')
S = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(S)


def record(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


class Writer:
    def __init__(self, limit=None, hook=None):
        self.data = bytearray()
        self.limit = limit
        self.hook = hook
        self.flushed = False

    def write(self, data):
        if self.hook:
            self.hook()
        count = min(len(data), self.limit) if self.limit else len(data)
        self.data.extend(data[:count])
        return count

    def flush(self):
        self.flushed = True


class Sender(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.guest = Path(self.tmp.name)
        self.payloads = {'Image': b'kernel', 'initramfs.cpio.gz': b'initrd', 'disk.raw': b'A' * 512}
        config = {'schema': 1, 'name': 'one', 'uid': 60001, 'gid': 60001,
                  'memory_mib': 64, 'memory_max_mib': 128, 'cpus': 1}
        files = {}
        for key, name in S.ENROLL.FILENAMES.items():
            data = self.payloads[name]
            (self.guest / name).write_bytes(data)
            config[key] = record(data)
            files[name] = {**record(data), 'uid': 60001 if key == 'disk' else 0,
                           'gid': 60001 if key == 'disk' else 0,
                           'mode': '0600' if key == 'disk' else '0444'}
        self.manifest = {'schema': 1, 'profile': 'a733-guest-enrollment-v1', 'config': config,
                         'guest_uuid': '7811c43d-01e4-4635-83b4-25d276172ac0', 'files': files}
        # Preserve noncanonical externally pinned manifest bytes.
        self.raw = json.dumps(self.manifest, indent=2).encode()
        self.pin = record(self.raw)['sha256']
        self.checks = 0
        outer = self
        class Inspection:
            def __init__(self, name, pin):
                S.require(name == 'one' and pin == outer.pin, 'bad pin')
                self.name = name
                self.manifest = outer.manifest
                self.manifest_data = outer.raw
                self.disk_record = record((outer.guest / 'disk.raw').read_bytes())
                self.guest_fd = os.open(outer.guest, os.O_RDONLY | os.O_DIRECTORY)
                self.fds = {key: os.open(outer.guest / name, os.O_RDONLY)
                            for key, name in S.ENROLL.FILENAMES.items()}
                self.infos = {fd: S.ENROLL.identity(os.fstat(fd)) for fd in self.fds.values()}
            def _held(self, fd):
                S.require(S.ENROLL.identity(os.fstat(fd)) == self.infos[fd], 'input changed')
            def recheck(self):
                outer.checks += 1
                for fd in self.fds.values():
                    self._held(fd)
            def __enter__(self):
                return self
            def __exit__(self, *_):
                for fd in [self.guest_fd] + list(self.fds.values()):
                    os.close(fd)
        mock = patch.object(S.POLICY, 'Inspection', Inspection)
        mock.start()
        self.addCleanup(mock.stop)

    def send(self, writer=None):
        writer = writer or Writer()
        receipt = S.send('one', self.pin, writer)
        return writer, receipt

    def unpack(self, data):
        raw = gzip.decompress(data)
        archive = tarfile.open(fileobj=io.BytesIO(raw), mode='r:')
        self.assertEqual(archive.getnames(), list(S.BACKUP.NAMES))
        offset = 0
        result = {}
        for item in archive:
            self.assertEqual(raw[offset:offset + 512], S.BACKUP.make_header(item.name, item.size))
            result[item.name] = archive.extractfile(item).read()
            offset += 512 + ((item.size + 511) // 512) * 512
        self.assertEqual(raw[offset:], b'\0' * 1024)
        return result

    def test_roundtrip_deterministic_headers_exact_content_and_receipt(self):
        writer, receipt = self.send()
        again, second = self.send()
        self.assertEqual(writer.data, again.data)
        self.assertEqual(receipt, second)
        self.assertEqual(bytes(writer.data[:10]), b'\x1f\x8b\x08\x00\x00\x00\x00\x00\x02\xff')
        contents = self.unpack(writer.data)
        self.assertEqual(contents['enrollment.json'], self.raw)
        self.assertEqual(S.BACKUP.validate_history(contents['checkpoint-history.json'], self.raw), {})
        for name, data in self.payloads.items():
            self.assertEqual(contents[name], data)
        self.assertEqual(receipt['archive'], record(writer.data))
        self.assertTrue(writer.flushed)
        self.assertFalse(receipt['hardware_acceptance'])

    def test_stale_disk_refused_before_output(self):
        (self.guest / 'disk.raw').write_bytes(b'B' * 512)
        writer = Writer()
        with self.assertRaisesRegex(ValueError, 'explicit checkpoint'):
            self.send(writer)
        self.assertEqual(writer.data, b'')

    def test_bad_external_pin_refused_before_output(self):
        writer = Writer()
        with self.assertRaises(ValueError):
            S.send('one', '0' * 64, writer)
        self.assertEqual(writer.data, b'')

    def test_short_writes_preserve_every_byte_and_encoded_hash(self):
        expected, _ = self.send()
        writer, receipt = self.send(Writer(limit=3))
        self.assertEqual(writer.data, expected.data)
        self.assertEqual(receipt['archive'], record(writer.data))

    def test_zero_write_and_output_error_raise(self):
        for result in (0, None, -1, 999999):
            with self.subTest(result=result), self.assertRaises(ValueError):
                S.EncodedWriter(type('Bad', (), {'write': lambda self, data: result})()).write(b'abc')
        with self.assertRaises(OSError):
            self.send(Writer(hook=lambda: (_ for _ in ()).throw(OSError('closed pipe'))))

    def test_mutation_during_gzip_header_fails(self):
        def mutate():
            (self.guest / 'disk.raw').write_bytes(b'B' * 512)
        with self.assertRaises(ValueError):
            self.send(Writer(hook=mutate))

    def test_flush_failure_has_no_receipt(self):
        writer = Writer()
        writer.flush = lambda: (_ for _ in ()).throw(OSError('flush failed'))
        with self.assertRaises(OSError):
            self.send(writer)

    def test_runtime_guard_failure_before_archive(self):
        writer = Writer()
        with patch.object(S.POLICY, 'Inspection', side_effect=ValueError('unsupported builder')):
            with self.assertRaises(ValueError):
                self.send(writer)
        self.assertEqual(writer.data, b'')

    def test_stdout_guard_regular_and_tty_refusal_pipe_acceptance(self):
        with tempfile.TemporaryFile() as stream, self.assertRaises(ValueError):
            S.stdout_guard(stream)
        read, write = os.pipe()
        try:
            with os.fdopen(write, 'wb') as stream:
                S.stdout_guard(stream)
        finally:
            os.close(read)
        with patch.object(S.os, 'fstat', return_value=type('Info', (), {'st_mode': 0o020000})()):
            with self.assertRaises(ValueError):
                S.stdout_guard(type('TTY', (), {'fileno': lambda self: 1})())

    @unittest.skipUnless(os.geteuid() == 0, 'root history metadata fixture required')
    def test_orphan_intent_retained_exactly(self):
        new = S.POLICY.MANIFEST.proposed_manifest(self.manifest, self.pin, record(b'B' * 512))
        new_raw = S.POLICY.MANIFEST.canonical(new)
        new_sha = record(new_raw)['sha256']
        name = self.pin + '-' + new_sha + '.json'
        entry = S.BACKUP.canonical({'schema': 1, 'operation': 'a733-guest-checkpoint-v1',
              'old_manifest': self.manifest, 'new_manifest': new,
              'old_manifest_sha256': self.pin, 'new_manifest_sha256': new_sha,
              'old_manifest_base64': base64.b64encode(self.raw).decode(),
              'new_manifest_base64': base64.b64encode(new_raw).decode()})
        history = self.guest / '.checkpoints'
        history.mkdir(mode=0o700)
        path = history / name
        path.write_bytes(entry)
        path.chmod(0o400)
        writer, receipt = self.send()
        contents = self.unpack(writer.data)
        self.assertEqual(S.BACKUP.validate_history(contents['checkpoint-history.json'], self.raw), {name: entry})
        self.assertEqual(receipt['history_count'], 1)
        path.unlink()
        path.symlink_to(self.guest / 'Image')
        writer = Writer()
        with self.assertRaises(OSError):
            self.send(writer)
        self.assertEqual(writer.data, b'')

    @unittest.skipUnless(os.geteuid() == 0, 'root history metadata fixture required')
    def test_history_appearing_during_stream_refused(self):
        fired = False
        def appear():
            nonlocal fired
            if not fired:
                fired = True
                (self.guest / '.checkpoints').mkdir(mode=0o700)
        with self.assertRaisesRegex(ValueError, 'history appeared'):
            self.send(Writer(hook=appear))

    @unittest.skipUnless(os.geteuid() == 0, 'root history metadata fixture required')
    def test_empty_history_directory_names_changed_during_stream_refused(self):
        history = self.guest / '.checkpoints'
        history.mkdir(mode=0o700)
        fired = False
        def replace():
            nonlocal fired
            if not fired:
                fired = True
                (history / 'unexpected').write_bytes(b'new')
        with self.assertRaises(ValueError):
            self.send(Writer(hook=replace))

    @unittest.skipUnless(os.geteuid() == 0, 'root history metadata fixture required')
    def test_history_metadata_and_entry_bounds_rejected_before_output(self):
        history = self.guest / '.checkpoints'
        history.mkdir(mode=0o700)
        name = '0' * 64 + '-' + '1' * 64 + '.json'
        path = history / name
        path.write_bytes(b'bad')
        writer = Writer()
        with self.assertRaisesRegex(ValueError, 'unsafe checkpoint record'):
            self.send(writer)
        self.assertEqual(writer.data, b'')
        path.chmod(0o400)
        with path.open('wb') as stream:
            stream.truncate(S.BACKUP.ENTRY_LIMIT + 1)
        with self.assertRaisesRegex(ValueError, 'unsafe checkpoint record'):
            self.send(writer)
        self.assertEqual(writer.data, b'')
        path.unlink()
        with patch.object(S.BACKUP, 'ENTRY_COUNT', -1):
            with self.assertRaisesRegex(ValueError, 'too many checkpoint records'):
                self.send(writer)
        self.assertEqual(writer.data, b'')

    def test_checkpoint_manifest_without_history_refused_before_output(self):
        self.manifest = S.POLICY.MANIFEST.proposed_manifest(
            self.manifest, self.pin, record(b'B' * 512))
        self.raw = S.POLICY.MANIFEST.canonical(self.manifest)
        self.pin = record(self.raw)['sha256']
        (self.guest / 'disk.raw').write_bytes(b'B' * 512)
        writer = Writer()
        with self.assertRaisesRegex(ValueError, 'missing or cyclic checkpoint chain'):
            self.send(writer)
        self.assertEqual(writer.data, b'')

    def test_history_symlink_directory_and_bad_name_refused(self):
        history = self.guest / '.checkpoints'
        history.symlink_to(self.guest, target_is_directory=True)
        with self.assertRaises(OSError):
            self.send()
        history.unlink()
        history.mkdir(mode=0o700)
        (history / 'unexpected').write_bytes(b'bad')
        with self.assertRaises(ValueError):
            self.send()


if __name__ == '__main__':
    unittest.main()
