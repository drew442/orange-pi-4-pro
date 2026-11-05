"""Host-only state payload checks; no mounts, devices or hardware claims."""
import base64
import gzip
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock
import uuid

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('host_state', REPO / 'build/host-state.py')
STATE = importlib.util.module_from_spec(spec)
spec.loader.exec_module(STATE)
SELECTION = STATE.SELECTION.serialize({'SCHEMA': '1', 'SLOT': 'A',
    'ROOT_UUID': STATE.PAYLOAD.SLOT_UUIDS['A'], 'ROOT_MARKER_SHA256': 'a' * 64,
    'KERNEL_RELEASE': STATE.PAYLOAD.KERNEL_RELEASE})
BLOB = struct.pack('>I', 11) + b'ssh-ed25519' + struct.pack('>I', 32) + bytes(range(32))
KEY = b'ssh-ed25519 ' + base64.b64encode(BLOB) + b' operator comment\n'
CONFIG = {'schema': 1, 'hostname': 'a733-test', 'management_mac': '02:11:22:33:44:55',
          'management_cidr': '192.0.2.0/24', 'dhcp': True}


class Validators(unittest.TestCase):
    def test_config(self):
        self.assertEqual(STATE.validate_config(json.dumps(CONFIG)), CONFIG)
        mutations = [('schema', True), ('dhcp', False), ('dhcp', 1), ('hostname', '-bad'),
                     ('hostname', 'two.labels'), ('hostname', 'a' * 64), ('hostname', 1),
                     ('management_mac', '01:11:22:33:44:55'),
                     ('management_mac', '00:00:00:00:00:00'),
                     ('management_mac', '02:AA:22:33:44:55'),
                     ('management_cidr', '192.0.2.1/24'),
                     ('management_cidr', '::/64'), ('management_cidr', '192.0.2.0/255.255.255.0')]
        for field, value in mutations:
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                STATE.validate_config(json.dumps({**CONFIG, field: value}))
        for data in ('{}', '[]', json.dumps({**CONFIG, 'password': 'bad'}),
                     '{"schema":1,"schema":1}'):
            with self.subTest(data=data), self.assertRaises(ValueError):
                STATE.validate_config(data)

    def test_public_key(self):
        canonical, fingerprint = STATE.validate_public_key(KEY)
        self.assertEqual(canonical, b'ssh-ed25519 ' + base64.b64encode(BLOB) + b'\n')
        self.assertTrue(fingerprint.startswith('SHA256:'))
        for key in (KEY + KEY, b'command="id" ' + KEY, b'-----BEGIN PRIVATE KEY-----\n',
                    b'ssh-rsa AAAA', b'ssh-ed25519 %%%', b'ssh-ed25519 ' + base64.b64encode(BLOB + b'x'),
                    b'ssh-ed25519 ' + base64.b64encode(BLOB[:-1]), KEY.replace(b'\n', b'\r\n'),
                    KEY.replace(b'ssh-ed25519', b'ssh-ed25519\0')):
            with self.subTest(key=key), self.assertRaises(ValueError):
                STATE.validate_public_key(key)

    def test_state_bundle_interface_preserves_multiple_keys(self):
        other = b'ssh-ed25519 ' + base64.b64encode(BLOB[:-1] + b'z') + b'\n'
        canonical, fingerprints = STATE.validate_public_keys(KEY + other)
        self.assertEqual(len(fingerprints), 2)
        self.assertEqual(canonical, STATE.validate_public_key(KEY)[0] + other)
        self.assertEqual(STATE.validate_config(json.dumps({**CONFIG, 'management_cidr': '0.0.0.0/0'}))['management_cidr'], '0.0.0.0/0')

    def test_uuid(self):
        value = str(uuid.uuid4())
        self.assertEqual(STATE.validate_uuid(value), value)
        for value in (value.upper(), '0' * 32, str(uuid.UUID(int=0)), *STATE.PAYLOAD.SLOT_UUIDS.values()):
            with self.assertRaises(ValueError):
                STATE.validate_uuid(value)

    def test_input_guards(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'data').write_bytes(b'abc')
            (root / 'link').symlink_to(root / 'data')
            os.mkfifo(root / 'fifo')
            self.assertEqual(STATE.bounded_input(root / 'data', 3), b'abc')
            for path in (root / 'link', root / 'fifo', root):
                with self.assertRaises((ValueError, OSError)):
                    STATE.bounded_input(path, 10)
            with self.assertRaises(ValueError):
                STATE.bounded_input(root / 'data', 2)

    def test_output_guards(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(STATE, 'REPO', Path(temporary)):
            root = Path(temporary)
            (root / 'out').mkdir()
            valid = root / 'out/host-state-fixture'
            self.assertEqual(STATE.validate_output(valid), valid)
            for path in (Path('out/host-state-x'), root / 'host-state-x', root / 'out/host-state-', root / 'out/other'):
                with self.assertRaises(ValueError):
                    STATE.validate_output(path)
            valid.symlink_to(root / 'missing')
            with self.assertRaises(ValueError):
                STATE.validate_output(valid)
            valid.unlink()
            valid.mkdir()
            with self.assertRaises(ValueError):
                STATE.validate_output(valid)
            shutil.rmtree(root / 'out')
            (root / 'real').mkdir()
            (root / 'out').symlink_to(root / 'real')
            with self.assertRaises(ValueError):
                STATE.validate_output(valid)

    def test_storage_rejects_ram(self):
        with mock.patch.object(STATE.subprocess, 'check_output', return_value=json.dumps(
                {'filesystems': [{'fstype': 'tmpfs', 'maj:min': '0:1'}]})):
            with self.assertRaises(ValueError):
                STATE.validate_workspace_storage(REPO)

    def test_storage_backing_devices(self):
        # Synthetic sysfs nodes plus mocked findmnt cover recursive DM decisions.
        cases = [('mmcblk0', False, False, False), ('loop0', False, False, False),
                 ('sdb', True, False, False), ('mmcblk0', False, True, False),
                 ('sdb', True, True, False), ('nvme0n1', False, False, True),
                 ('sda', False, True, True)]
        for name, removable, mapped, accepted in cases:
            with self.subTest(name=name, removable=removable, mapped=mapped):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    sysblock = root / 'block'
                    sysblock.mkdir()
                    disk = root / 'devices' / name
                    disk.mkdir(parents=True)
                    (disk / 'removable').write_text('1' if removable else '0')
                    selected = disk
                    if mapped:
                        selected = root / 'devices/dm-0'
                        (selected / 'slaves').mkdir(parents=True)
                        (selected / 'slaves' / name).symlink_to(disk)
                    (sysblock / '8:0').symlink_to(selected)
                    result = {'filesystems': [{'fstype': 'ext4', 'maj:min': '8:0'}]}
                    with mock.patch.object(STATE, 'SYS_DEV_BLOCK', sysblock), mock.patch.object(
                            STATE.subprocess, 'check_output', return_value=json.dumps(result)):
                        if accepted:
                            self.assertEqual(STATE.validate_workspace_storage(root), result['filesystems'][0])
                        else:
                            with self.assertRaises(ValueError):
                                STATE.validate_workspace_storage(root)

    def test_filesystem_directory_metadata_rejection(self):
        for text in ('', 'Inode: 2 Type: regular Mode: 0755\nUser: 0 Group: 0',
                     'Inode: 2 Type: directory Mode: 0777\nUser: 0 Group: 0',
                     'Inode: 2 Type: directory Mode: 0755\nUser: 123 Group: 0'):
            with self.subTest(text=text), mock.patch.object(STATE.subprocess, 'run',
                    return_value=mock.Mock(stdout=text)), self.assertRaises(ValueError):
                STATE.filesystem_directories(Path('/unused'))

    def test_archive_namespace_rejection(self):
        for name in ('../var/log', '/var/log'):
            with self.assertRaises(ValueError):
                STATE.PAYLOAD.validate_tar([tarfile.TarInfo(name)])
        parent = tarfile.TarInfo('var')
        parent.type = tarfile.SYMTYPE
        parent.linkname = '/tmp'
        with self.assertRaises(ValueError):
            STATE.PAYLOAD.validate_tar([parent, tarfile.TarInfo('var/data')])

    def test_cache_pruning_precedes_state_seed_and_keeps_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            extracted = root / 'extracted'
            (extracted / 'var/cache/apt').mkdir(parents=True)
            (extracted / 'var/lib/dpkg').mkdir(parents=True)
            (extracted / 'var/lib/dpkg/status').write_bytes(b'retained physical state copy')
            extracted.chmod(0o755)
            cache = extracted / 'var/cache/apt/pkgcache.bin'
            cache.write_bytes(b'unused cache')
            for path in extracted.rglob('*'):
                if path.is_dir():
                    path.chmod(0o755)
            records = STATE.PAYLOAD.PACKAGE.prune_caches(extracted)
            self.assertEqual(records[0]['path'], 'var/cache/apt/pkgcache.bin')
            if os.geteuid() != 0:
                return  # seed_state's root-owned-var contract is checked separately.
            state = root / 'state'
            STATE.seed_state(extracted, state, str(uuid.uuid4()), CONFIG, KEY, SELECTION)
            self.assertFalse((state / 'var/cache/apt/pkgcache.bin').exists())
            self.assertEqual((state / 'var/lib/dpkg/status').read_bytes(), b'retained physical state copy')

    def test_seed_rejects_symlink_var(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'input').mkdir()
            (root / 'input/var').symlink_to(root)
            with self.assertRaises(ValueError):
                STATE.seed_state(root / 'input', root / 'state', str(uuid.uuid4()), CONFIG, KEY, SELECTION)
            self.assertFalse((root / 'state').exists())

    def test_streamed_image_gzip(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = root / 'state.ext4'
            data = bytes(range(256)) * 8192
            image.write_bytes(data)
            first, second = root / 'first.gz', root / 'second.gz'
            STATE.compress_image(image, first)
            STATE.compress_image(image, second)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(first.read_bytes()[4:8], b'\0' * 4)
            self.assertEqual(gzip.decompress(first.read_bytes()), data)
            with self.assertRaises(FileExistsError):
                STATE.compress_image(image, first)

    def test_image_exclusive_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'existing').write_bytes(b'keep')
            (root / 'link').symlink_to(root / 'existing')
            for path in (root / 'existing', root / 'link'):
                with self.assertRaises(OSError):
                    STATE.make_image(root, path, str(uuid.uuid4()), size=32 * 1024**2)
            self.assertEqual((root / 'existing').read_bytes(), b'keep')


@unittest.skipUnless(os.geteuid() == 0, 'root required for ownership-preserving state fixture')
class RootFixtures(unittest.TestCase):
    def test_seed_preserves_var_and_unique_identities(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            identifiers = []
            for i in range(2):
                extracted = root / f'input{i}'
                (extracted / 'var/lib').mkdir(parents=True)
                (extracted / 'var/lib/data').write_bytes(b'preserved')
                os.chown(extracted / 'var/lib/data', 123, 456)
                os.setxattr(extracted / 'var/lib/data', 'user.fixture', b'preserved')
                state = root / f'state{i}'
                state_uuid = str(uuid.uuid4())
                machine_id = STATE.seed_state(extracted, state, state_uuid, CONFIG, STATE.validate_public_key(KEY)[0], SELECTION)
                identifiers.append(machine_id)
                self.assertRegex(machine_id, r'^[0-9a-f]{32}$')
                self.assertEqual((state / '.a733-state').read_text(), f'a733-state-v1\n{state_uuid}\n')
                self.assertEqual((state / 'identity/machine-id').read_bytes(), machine_id.encode() + b'\n')
                self.assertEqual((state / 'var/lib/data').stat().st_uid, 123)
                self.assertEqual(os.getxattr(state / 'var/lib/data', 'user.fixture'), b'preserved')
                for name, mode in [('root', 0o700), ('home', 0o755), ('identity/ssh', 0o700),
                                   ('config/management.json', 0o600), ('root/.ssh/authorized_keys', 0o600),
                                   ('boot', 0o700), ('boot/selected.conf', 0o600)]:
                    self.assertEqual(stat.S_IMODE((state / name).stat().st_mode), mode)
                self.assertEqual(list((state / 'identity/ssh').iterdir()), [])
                self.assertEqual((state / 'boot/selected.conf').read_bytes(), SELECTION)
            self.assertNotEqual(*identifiers)

    @unittest.skipUnless(all(shutil.which(tool) for tool in ('mke2fs', 'e2fsck', 'debugfs')), 'ext4 fixture tools unavailable')
    def test_small_regular_journaled_image(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'userspace/var').mkdir(parents=True)
            state_uuid = str(uuid.uuid4())
            STATE.seed_state(root / 'userspace', root / 'state', state_uuid, CONFIG, STATE.validate_public_key(KEY)[0], SELECTION)
            image = root / 'state.ext4'
            metadata = STATE.make_image(root / 'state', image, state_uuid, size=32 * 1024**2)
            self.assertEqual(metadata['/'], {'type': 'directory', 'mode': '0755', 'uid': 0, 'gid': 0})
            self.assertEqual(metadata['/lost+found'], {'type': 'directory', 'mode': '0700', 'uid': 0, 'gid': 0})
            self.assertEqual(STATE.contents(root / 'state')['.']['mode'], '0700')
            marker = subprocess.check_output(['debugfs', '-R', 'cat /.a733-state', str(image)], stderr=subprocess.DEVNULL)
            self.assertEqual(marker, f'a733-state-v1\n{state_uuid}\n'.encode())
            identity = subprocess.check_output(['debugfs', '-R', 'cat /identity/machine-id', str(image)], stderr=subprocess.DEVNULL)
            self.assertRegex(identity, rb'^[0-9a-f]{32}\n$')


if __name__ == '__main__':
    unittest.main()
