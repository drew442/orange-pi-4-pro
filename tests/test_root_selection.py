"""Selector syntax, captured-image integrity and atomic file publication tests."""
import hashlib
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('root_selection', Path(__file__).resolve().parents[1] / 'build/root-selection.py')
SELECT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SELECT)


def record(slot='A'):
    return SELECT.serialize({'SCHEMA': '1', 'SLOT': slot, 'ROOT_UUID': SELECT.INIT.PAYLOAD.SLOT_UUIDS[slot],
                             'ROOT_MARKER_SHA256': 'a' * 64, 'KERNEL_RELEASE': SELECT.INIT.PAYLOAD.KERNEL_RELEASE})


class SelectionTests(unittest.TestCase):
    def test_schema(self):
        self.assertEqual(SELECT.parse(record())['SLOT'], 'A')
        self.assertEqual(SELECT.parse(record('B'))['SLOT'], 'B')
        for data in (record()[:-1], record() + b'\n', record() + b'SLOT=A\n',
                     record().replace(b'SLOT=A', b'SLOT=C'), record().replace(b'SCHEMA=1', b'SCHEMA=2'),
                     record().replace(b'SLOT=A', b'SLOT=B'), record().replace(b'6.6.98', b'6.6.99'),
                     record().replace(b'\n', b'\r\n'), record()[:-1] + b'\0', b'x' * 4097):
            with self.subTest(data=data[:50]), self.assertRaises(ValueError):
                SELECT.parse(data)

    def test_successful_atomic_replace(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            SELECT.atomic_store(root, record())
            SELECT.atomic_store(root, record('B'))
            self.assertEqual((root / 'selected.conf').read_bytes(), record('B'))
            self.assertEqual((root / 'selected.conf').stat().st_mode & 0o777, 0o600)
            self.assertEqual([p.name for p in root.iterdir()], ['selected.conf'])

    def test_failure_before_rename_preserves_old_selection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            SELECT.atomic_store(root, record())
            for operation in ('replace', 'fsync'):
                with self.subTest(operation=operation), mock.patch.object(SELECT.os, operation, side_effect=OSError('fixture')):
                    with self.assertRaises(SELECT.PublicationError) as caught:
                        SELECT.atomic_store(root, record('B'))
                    self.assertFalse(caught.exception.published)
                    self.assertEqual((root / 'selected.conf').read_bytes(), record())
                    self.assertEqual([p.name for p in root.iterdir()], ['selected.conf'])

    def test_directory_sync_failure_reports_visible_uncertain_commit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            SELECT.atomic_store(root, record())
            with mock.patch.object(SELECT.os, 'fsync', side_effect=[None, OSError('directory sync')]):
                with self.assertRaises(SELECT.PublicationError) as caught:
                    SELECT.atomic_store(root, record('B'))
            self.assertTrue(caught.exception.published)
            self.assertEqual((root / 'selected.conf').read_bytes(), record('B'))

    def test_unsafe_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            alias = root / 'alias'
            alias.symlink_to(root, target_is_directory=True)
            with self.assertRaises(OSError):
                SELECT.atomic_store(alias, record())
            root.chmod(0o777)
            with self.assertRaises(ValueError):
                SELECT.atomic_store(root, record())

    def test_private_capture_survives_source_modification(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, snapshot = root / 'source', root / 'snapshot'
            data = b'captured' + bytes(1024)
            source.write_bytes(data)
            expected = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
            SELECT.capture_root(source, expected, snapshot)
            source.write_bytes(b'modified' + bytes(1024))
            self.assertEqual(snapshot.read_bytes(), data)
            self.assertEqual(snapshot.stat().st_mode & 0o777, 0o400)
            with self.assertRaisesRegex(ValueError, 'hash/size'):
                SELECT.capture_root(source, expected, root / 'bad')
            alias = root / 'alias'
            alias.symlink_to(source)
            with self.assertRaises(OSError):
                SELECT.capture_root(alias, expected, root / 'bad-link')


if __name__ == '__main__':
    unittest.main()
