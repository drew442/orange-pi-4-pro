"""Selector syntax, captured-image integrity and atomic file publication tests."""
import hashlib
import importlib.util
import io
import json
import os
import signal
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
    def test_trusted_manifest_requires_managed_profile_and_matching_marker(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'docs/evidence').mkdir(parents=True)
            boot = {name: {'bytes': 1, 'sha256': 'a' * 64} for name in
                    ('Image', 'board.dtb', 'kernel.config', 'initramfs.cpio.gz')}
            (root / 'docs/evidence/diagnostic-20261104.json').write_text(json.dumps({'files': boot}))
            profile = {'profile': 'a733-management-v1', 'files': {
                destination: {'bytes': 1, 'sha256': 'a' * 64}
                for destination, _ in SELECT.INIT.PAYLOAD.MANAGEMENT_FILES.values()}}
            package = {'profile': 'a733-package-state-v1', 'manifest': {'bytes':1, 'sha256':'a'*64}}
            manifest = {'package_state': package, 'slot_bytes': SELECT.INIT.PAYLOAD.SLOT_BYTES,
                        'slot_uuids': SELECT.INIT.PAYLOAD.SLOT_UUIDS,
                        'kernel_release': SELECT.INIT.PAYLOAD.KERNEL_RELEASE,
                        'management_runtime': profile,
                        'files': {'root-A.ext4': {'bytes': SELECT.INIT.PAYLOAD.SLOT_BYTES, 'sha256': 'b' * 64}}}
            path = root / 'manifest.json'
            metadata = {'sources': boot, 'management_runtime': profile, 'package_state': package}
            with mock.patch.object(SELECT, 'REPO', root), \
                    mock.patch.object(SELECT.shutil, 'disk_usage') as space, \
                    mock.patch.object(SELECT, 'capture_root'), \
                    mock.patch.object(SELECT.INIT, 'read_pinned', side_effect=lambda *args: io.BytesIO(b'')), \
                    mock.patch.object(SELECT.INIT, 'root_marker', return_value=(b'marker', metadata)) as marker:
                space.return_value.free = 10 * 1024**3
                for bad in (None, {'profile': 'unmanaged'}, {'profile': 'a733-management-v2'}):
                    path.write_text(json.dumps({**manifest, 'management_runtime': bad}))
                    with self.assertRaises(ValueError):
                        SELECT.prepare(root / 'image', 'A', path)
                    self.assertFalse(marker.called)
                path.write_text(json.dumps(manifest))
                self.assertEqual(SELECT.parse(SELECT.prepare(root / 'image', 'A', path))['SLOT'], 'A')
                marker.return_value = (b'marker', {**metadata, 'package_state': {'profile':'other'}})
                with self.assertRaisesRegex(ValueError, 'package-state'):
                    SELECT.prepare(root / 'image', 'A', path)
                marker.return_value = (b'marker', {**metadata, 'management_runtime': {'profile': 'different'}})
                with self.assertRaisesRegex(ValueError, 'management runtime differs'):
                    SELECT.prepare(root / 'image', 'A', path)
                time_sources = {**boot,
                    'rootfs.tar.gz': {'bytes': 1, 'sha256': 'd' * 64},
                    'userspace_profile': {'profile': 'timesync-20261106'},
                    'timesync_state': {'schema': 1, 'identity': 996, 'directory': 'var/lib/systemd/timesync',
                                      'helper': {'bytes': 1, 'sha256': 'd' * 64}}}
                time_profile = {**profile, 'profile': 'a733-management-v2-timesync'}
                path.write_text(json.dumps({**manifest, 'sources': time_sources, 'management_runtime': time_profile}))
                captured = {'sources': time_sources, 'management_runtime': time_profile, 'package_state': package}
                marker.return_value = (b'marker', captured)
                self.assertEqual(SELECT.parse(SELECT.prepare(root / 'image', 'A', path))['SLOT'], 'A')
                for field in ('rootfs.tar.gz', 'userspace_profile', 'timesync_state'):
                    changed = json.loads(json.dumps(captured))
                    if field == 'rootfs.tar.gz':
                        changed['sources'][field]['bytes'] = True
                    elif field == 'timesync_state':
                        changed['sources'][field]['helper']['sha256'] = 'e' * 64
                    else:
                        changed['sources'][field]['profile'] = 'normalized-20261106'
                    marker.return_value = (b'marker', changed)
                    with self.subTest(captured_field=field), self.assertRaises(ValueError):
                        SELECT.prepare(root / 'image', 'A', path)

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

    def test_held_directory_fd_survives_path_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'boot'
            root.mkdir(mode=0o700)
            descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                moved = root.with_name('original')
                root.rename(moved)
                root.mkdir(mode=0o700)
                SELECT.atomic_store(descriptor, record('B'))
                self.assertEqual((moved / 'selected.conf').read_bytes(), record('B'))
                self.assertFalse((root / 'selected.conf').exists())
                os.fstat(descriptor)  # Caller retains ownership of its descriptor.
            finally:
                os.close(descriptor)

    def test_invalid_directory_fd(self):
        with tempfile.TemporaryFile() as stream:
            with self.assertRaises(OSError):
                SELECT.atomic_store(stream.fileno(), record())
        with self.assertRaises(ValueError):
            SELECT.atomic_store(True, record())

    def test_interrupt_before_and_after_rename_preserves_visibility(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            SELECT.atomic_store(root, record())
            original_replace = os.replace
            def renamed_then_interrupted(*args, **kwargs):
                original_replace(*args, **kwargs)
                raise KeyboardInterrupt('after rename return boundary')
            for replacement, expected in ((mock.Mock(side_effect=KeyboardInterrupt('before rename')), False),
                                          (renamed_then_interrupted, True)):
                with mock.patch.object(SELECT.os, 'replace', side_effect=replacement):
                    with self.assertRaises(SELECT.PublicationError) as caught:
                        SELECT.atomic_store(root, record('B'))
                self.assertEqual(caught.exception.published, expected)
                self.assertEqual((root / 'selected.conf').read_bytes(), record('B') if expected else record())
                self.assertEqual(list(root.glob('.selected-*')), [])

    def test_close_failure_after_publication_keeps_visible_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original_close = os.close
            def close(fd):
                original_close(fd)
                raise OSError('directory close failed')
            with mock.patch.object(SELECT.os, 'close', side_effect=close):
                with self.assertRaises(SELECT.PublicationError) as caught:
                    SELECT.atomic_store(root, record('B'))
            self.assertTrue(caught.exception.published)
            self.assertEqual((root / 'selected.conf').read_bytes(), record('B'))

    def test_pending_sigint_delivered_after_visibility_assignment_and_mask_restored(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            SELECT.atomic_store(root, record())
            previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())
            previous_handler = signal.getsignal(signal.SIGINT)
            delivered = []
            original_replace = os.replace
            def interrupt(signum, frame):
                delivered.append(signum)
                raise KeyboardInterrupt('pending handled SIGINT')
            def rename_and_queue(*args, **kwargs):
                original_replace(*args, **kwargs)
                os.kill(os.getpid(), signal.SIGINT)
                self.assertEqual(delivered, [])  # Still blocked inside publication.
            signal.signal(signal.SIGINT, interrupt)
            try:
                with mock.patch.object(SELECT.os, 'replace', side_effect=rename_and_queue):
                    with self.assertRaises(SELECT.PublicationError) as caught:
                        SELECT.atomic_store(root, record('B'))
                self.assertTrue(caught.exception.published)
                self.assertEqual(delivered, [signal.SIGINT])
                self.assertEqual(signal.pthread_sigmask(signal.SIG_BLOCK, set()), previous_mask)
                self.assertEqual((root / 'selected.conf').read_bytes(), record('B'))
            finally:
                signal.signal(signal.SIGINT, previous_handler)
                signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)

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
