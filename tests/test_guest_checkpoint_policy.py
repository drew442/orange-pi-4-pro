"""Stopped checkpoint policy checks; live provenance is explicitly mocked."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('tested_checkpoint_policy', BASE / 'scripts/guest-checkpoint-policy.py')
POLICY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(POLICY)
MANIFEST = POLICY.MANIFEST


def fixture():
    config = {'schema': 1, 'name': 'fixture', 'uid': 60001, 'gid': 60001,
              'memory_mib': 128, 'memory_max_mib': 256, 'cpus': 1}
    data = {'kernel': b'kernel', 'initrd': b'initrd', 'disk': bytes(512)}
    files = {}
    for key, payload in data.items():
        config[key] = {'bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()}
        uid, gid, mode = (60001, 60001, '0600') if key == 'disk' else (0, 0, '0444')
        files[POLICY.ENROLL.FILENAMES[key]] = {**config[key], 'uid': uid, 'gid': gid, 'mode': mode}
    return {'schema': 1, 'profile': MANIFEST.PROFILE, 'config': config,
            'guest_uuid': '12345678-1234-4234-8234-123456789abc', 'files': files}, data


class ManifestTests(unittest.TestCase):
    def test_roundtrip_and_checkpoint_preserve(self):
        value, _ = fixture()
        encoded = MANIFEST.canonical(value)
        self.assertEqual(value, MANIFEST.validate_manifest(encoded))
        changed = MANIFEST.proposed_manifest(value, 'a' * 64, {'bytes': 512, 'sha256': 'b' * 64})
        self.assertEqual(changed['checkpoint']['number'], 1)
        self.assertEqual(changed['config']['kernel'], value['config']['kernel'])
        self.assertEqual(value['config']['disk']['sha256'], changed['checkpoint']['previous_disk_sha256'])
        self.assertEqual(value, MANIFEST.proposed_manifest(value, 'a' * 64, value['config']['disk']))
        self.assertEqual(value, MANIFEST.validate_manifest(encoded))

    def test_reject_invalid_schema_relations_and_types(self):
        paths = [(('schema',), True), (('profile',), 'wrong'), (('guest_uuid',), '0' * 32),
                 (('files', 'Image', 'mode'), '444'), (('files', 'disk.raw', 'uid'), 0),
                 (('files', 'Image', 'sha256'), 'a' * 64), (('extra',), None)]
        for path, replacement in paths:
            value, _ = fixture()
            target = value
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = replacement
            with self.subTest(path=path), self.assertRaises(ValueError):
                MANIFEST.validate_manifest(json.dumps(value))
        with self.assertRaises(ValueError):
            MANIFEST.validate_manifest(b'{"schema":1,"schema":1}')
        with self.assertRaises(ValueError):
            MANIFEST.validate_manifest(b' ' * 65537)

    def test_checkpoint_range_no_resize(self):
        value, _ = fixture()
        for number in (0, True, 2147483648):
            changed = MANIFEST.proposed_manifest(value, 'a' * 64, {'bytes': 512, 'sha256': 'b' * 64})
            changed['checkpoint']['number'] = number
            with self.assertRaises(ValueError):
                MANIFEST.canonical(changed)
        with self.assertRaises(ValueError):
            MANIFEST.proposed_manifest(value, 'a' * 64, {'bytes': 1024, 'sha256': 'b' * 64})


class ServiceTests(unittest.TestCase):
    def state(self, **kwargs):
        fields = {'LoadState': 'loaded', 'ActiveState': 'inactive', 'SubState': 'dead',
                  'MainPID': '0', 'ControlPID': '0'}
        fields.update(kwargs)
        return ''.join(key + '=' + value + '\n' for key, value in fields.items()).encode()

    def test_stopped_and_absent(self):
        POLICY.parse_service_state(self.state(), 0)
        POLICY.parse_service_state(self.state(LoadState='not-found'), 1)

    def test_fail_closed(self):
        for data, code in [(self.state(), 1), (self.state(MainPID='1'), 0),
                           (self.state(ActiveState='failed'), 0),
                           (self.state() + b'MainPID=0\n', 0), (b'', 0),
                           (self.state(SubState='running'), 0)]:
            with self.subTest(data=data), self.assertRaises(ValueError):
                POLICY.parse_service_state(data, code)
        with self.assertRaises(ValueError):
            POLICY.service_state('other.service')


@unittest.skipUnless(os.geteuid() == 0, 'root metadata fixture required')
class InspectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir='/run')
        self.root = Path(self.temporary.name)
        self.root.chmod(0o711)
        self.guest = self.root / 'fixture'
        self.guest.mkdir(mode=0o710)
        os.chown(self.guest, 0, 60001)
        self.value, self.data = fixture()
        for key, data in self.data.items():
            path = self.guest / POLICY.ENROLL.FILENAMES[key]
            path.write_bytes(data)
            path.chmod(0o600 if key == 'disk' else 0o444)
            os.chown(path, 60001 if key == 'disk' else 0, 60001 if key == 'disk' else 0)
        self.raw = MANIFEST.canonical(self.value)
        self.manifest = self.guest / 'enrollment.json'
        self.manifest.write_bytes(self.raw)
        self.manifest.chmod(0o400)
        self.sha = hashlib.sha256(self.raw).hexdigest()
        self.patches = [patch.object(POLICY.ENROLL, 'ROOT', str(self.root)),
                        patch.object(POLICY.MANAGEMENT, 'runtime_guard'),
                        patch.object(POLICY, 'host_namespace'),
                        patch.object(POLICY.ENROLL, 'probe_uuid'),
                        patch.object(POLICY.ACCOUNTS, 'snapshot', return_value={}),
                        patch.object(POLICY.ACCOUNTS, 'validate_accounts'),
                        patch.object(POLICY.STORAGE, 'verify_guest_directory', return_value={'fixture': True}),
                        patch.object(POLICY.STORAGE, 'verify_guest_storage', return_value={'fixture': True}),
                        patch.object(POLICY, 'service_state', return_value={'stopped': True})]
        for item in self.patches:
            item.start()
        self.addCleanup(self.temporary.cleanup)
        self.addCleanup(lambda: [item.stop() for item in reversed(self.patches)])

    def test_complete_and_changed_disk(self):
        with POLICY.Inspection('fixture', self.sha) as review:
            self.assertEqual(review.disk_record, self.value['config']['disk'])
            with self.assertRaises(BlockingIOError):
                POLICY.Inspection('fixture', self.sha)
            (self.guest / '.checkpoints').mkdir()
            review.recheck()
        disk = self.guest / 'disk.raw'
        disk.write_bytes(b'x' * 512)
        with POLICY.Inspection('fixture', self.sha) as review:
            self.assertNotEqual(review.disk_record, self.value['config']['disk'])
        with self.assertRaises(OSError):
            os.fstat(review.disk_fd)

    def test_metadata_symlink_hash_and_auth(self):
        with self.assertRaises(ValueError):
            POLICY.Inspection('fixture', 'a' * 64)
        for name in ('Image', 'initramfs.cpio.gz', 'disk.raw', 'enrollment.json'):
            path = self.guest / name
            old = path.stat().st_mode & 0o777
            path.chmod(0o666)
            with self.subTest(name=name), self.assertRaises(ValueError):
                POLICY.Inspection('fixture', self.sha)
            path.chmod(old)
        kernel = self.guest / 'Image'
        kernel.write_bytes(b'broken')
        with self.assertRaises(ValueError):
            POLICY.Inspection('fixture', self.sha)
        kernel.unlink()
        kernel.symlink_to('/dev/null')
        with self.assertRaises(OSError):
            POLICY.Inspection('fixture', self.sha)

    def test_mutation_recheck(self):
        with POLICY.Inspection('fixture', self.sha) as review:
            (self.guest / 'disk.raw').write_bytes(b'y' * 512)
            with self.assertRaises(ValueError):
                review.recheck()

    def test_all_descriptors_closed_on_failed_inspection(self):
        before = set(os.listdir('/proc/self/fd'))
        for _ in range(3):
            with self.assertRaises(ValueError):
                POLICY.Inspection('fixture', 'a' * 64)
        self.assertEqual(before, set(os.listdir('/proc/self/fd')))

    def test_payload_never_read_before_storage(self):
        with patch.object(POLICY.STORAGE, 'verify_guest_storage', side_effect=ValueError('placement')), \
                patch.object(POLICY.Inspection, '_hash') as hashing:
            with self.assertRaises(ValueError):
                POLICY.Inspection('fixture', self.sha)
            hashing.assert_not_called()

    def test_owned_manifest_temp(self):
        name = '.checkpoint-' + 'a' * 32
        with POLICY.Inspection('fixture', self.sha) as review:
            temporary = self.guest / name
            temporary.write_bytes(self.raw)
            temporary.chmod(0o400)
            info = temporary.stat()
            with self.assertRaises(ValueError):
                review.recheck()
            review.recheck(owned_entries={name: (info.st_dev, info.st_ino)})
            with self.assertRaises(ValueError):
                review.recheck(owned_entries={name: (info.st_dev, info.st_ino + 1)})
            temporary.chmod(0o600)
            with self.assertRaises(ValueError):
                review.recheck(owned_entries={name: (info.st_dev, info.st_ino)})

    def test_mutation_during_hash_is_refused(self):
        original = POLICY.os.pread
        def read_and_mutate(fd, count, offset):
            data = original(fd, count, offset)
            if os.fstat(fd).st_ino == (self.guest / 'Image').stat().st_ino:
                (self.guest / 'Image').write_bytes(b'kernel')
            return data
        with patch.object(POLICY.os, 'pread', side_effect=read_and_mutate):
            with self.assertRaises(ValueError):
                POLICY.Inspection('fixture', self.sha)

    def test_real_other_process_reader(self):
        process = subprocess.Popen(['/usr/bin/python3', '-c',
            'import sys; f=open(sys.argv[1],"rb"); print("ready",flush=True); sys.stdin.read()',
            str(self.guest / 'disk.raw')], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        try:
            self.assertEqual(process.stdout.readline(), b'ready\n')
            fd = os.open(self.guest / 'disk.raw', os.O_RDONLY)
            try:
                self.assertTrue(POLICY.disk_consumers(fd))
                with self.assertRaises(ValueError):
                    POLICY.Inspection('fixture', self.sha)
            finally:
                os.close(fd)
        finally:
            process.communicate(timeout=5)
            process.stdout.close()


if __name__ == '__main__':
    unittest.main()
