"""Host-only identity parser, refusal, cleanup and mount guards; no devices/mounts."""
import base64
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest import mock
import uuid

SPEC = importlib.util.spec_from_file_location('personalize', Path(__file__).resolve().parents[1] /
                                            'scripts/personalize-known-host-state.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def fixture(extra=None, mode=None):
    blob = b'\0\0\0\x0bssh-ed25519\0\0\0\x20' + b'x' * 32
    files = {'identity/machine-id': b'1' * 32 + b'\n',
             'identity/ssh/ssh_host_ed25519_key.pub': b'ssh-ed25519 ' + base64.b64encode(blob) + b' fixture\n',
             'identity/ssh/ssh_host_ed25519_key': b'-----BEGIN OPENSSH PRIVATE KEY-----\n' +
             base64.b64encode(b'openssh-key-v1\0' + blob) + b'\n-----END OPENSSH PRIVATE KEY-----\n'}
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w', format=tarfile.USTAR_FORMAT) as tar:
        for name, data in files.items():
            member = tarfile.TarInfo(name)
            member.mode = mode if mode is not None else M.FILES[name]
            member.size = len(data)
            tar.addfile(member, io.BytesIO(data))
        if extra:
            tar.addfile(extra, io.BytesIO(b''))
    fp = 'SHA256:' + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip('=')
    return stream.getvalue(), files, fp


class ArchiveTests(unittest.TestCase):
    def parse(self, data, fp):
        with mock.patch.object(M, 'ARCHIVE_HASH', M.digest(data)), mock.patch.object(M, 'FINGERPRINT', fp):
            return M.parse_archive(data)

    def test_exact_three_and_permission_pin(self):
        data, files, fp = fixture()
        self.assertEqual(self.parse(data, fp), files)
        data, _, fp = fixture(mode=0o600)
        with self.assertRaises(ValueError):
            self.parse(data, fp)

    def test_links_duplicates_traversal_and_extras(self):
        for name, kind in [('identity/machine-id', tarfile.REGTYPE), ('../evil', tarfile.REGTYPE),
                           ('identity/link', tarfile.SYMTYPE), ('identity/link', tarfile.LNKTYPE)]:
            member = tarfile.TarInfo(name)
            member.type = kind
            member.linkname = 'identity/machine-id'
            data, _, fp = fixture(extra=member)
            with self.subTest(name=name, kind=kind), self.assertRaises(ValueError):
                self.parse(data, fp)

    def test_hash_and_public_fingerprint_are_independent_guards(self):
        data, _, fp = fixture()
        with self.assertRaises(ValueError):
            M.parse_archive(data)
        with mock.patch.object(M, 'ARCHIVE_HASH', M.digest(data)), self.assertRaises(ValueError):
            M.parse_archive(data)

    def test_superblock_uuid_and_label(self):
        data = bytearray(1024)
        data[56:58] = b'\x53\xef'
        data[104:120] = uuid.UUID(M.STATE_UUID).bytes
        data[120:130] = b'a733-state'
        M.validate_superblock(data)
        data[104] ^= 1
        with self.assertRaises(ValueError):
            M.validate_superblock(data)


@unittest.skipUnless(os.geteuid() == 0, 'real ownership checks require root')
class StateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = fixture()[1]
        (self.root / '.a733-state').write_text(f'a733-state-v1\n{M.STATE_UUID}\n')
        (self.root / '.a733-state').chmod(0o600)
        current = self.root
        for component in ('var', 'lib', 'systemd', 'timesync'):
            current /= component
            current.mkdir(mode=0o755)
        os.chown(current, 996, 996)
        (self.root / 'identity').mkdir(mode=0o700)
        (self.root / 'identity/ssh').mkdir(mode=0o700)

    def test_fresh_id_replace_idempotency_and_modes(self):
        target = self.root / 'identity/machine-id'
        fresh = b'2' * 32 + b'\n'
        target.write_bytes(fresh)
        target.chmod(0o644)
        changed = []
        M.atomic_files(self.root, self.data, M.digest(fresh), changed)
        self.assertEqual(set(changed), set(M.FILES))
        for name, mode in M.FILES.items():
            self.assertEqual((self.root / name).stat().st_mode & 0o777, mode)
        changed = []
        M.atomic_files(self.root, self.data, M.digest(fresh), changed)
        self.assertEqual(changed, [])

    def test_nonempty_conflict_refuses_before_any_writes(self):
        target = self.root / 'identity/ssh/ssh_host_ed25519_key'
        target.write_bytes(b'unexpected')
        target.chmod(0o600)
        with self.assertRaises(ValueError):
            M.atomic_files(self.root, self.data, '0' * 64, [])
        self.assertFalse((self.root / 'identity/machine-id').exists())
        self.assertEqual(target.read_bytes(), b'unexpected')

    def test_symlink_and_hardlink_refuse(self):
        target = self.root / 'identity/machine-id'
        target.symlink_to(self.root / '.a733-state')
        with self.assertRaises(ValueError):
            M.atomic_files(self.root, self.data, '0' * 64, [])
        target.unlink()
        os.link(self.root / '.a733-state', target)
        with self.assertRaises(ValueError):
            M.atomic_files(self.root, self.data, '0' * 64, [])

    def test_partial_failure_reports_completed_renames_and_cleans_temp(self):
        changed = []
        original = M.os.replace
        calls = []
        def replace(src, dst):
            calls.append(dst)
            if len(calls) == 2:
                raise OSError('injected failure')
            return original(src, dst)
        with mock.patch.object(M.os, 'replace', side_effect=replace), self.assertRaises(OSError):
            M.atomic_files(self.root, self.data, '0' * 64, changed)
        self.assertEqual(changed, ['identity/machine-id'])
        self.assertEqual(list(self.root.rglob('.a733-identity-*')), [])

    def test_timesync_owner_and_marker_refusal(self):
        os.chown(self.root / 'var/lib/systemd/timesync', 0, 0)
        with self.assertRaises(ValueError):
            M.check_state(self.root)


class GuardTests(unittest.TestCase):
    def test_idle_refuses_existing_target_mount_before_process_scan(self):
        with mock.patch.object(M, 'mounts', return_value=[{'dev': '259:4'}]), \
                self.assertRaisesRegex(ValueError, 'mounted'):
            M.idle({'259:0', '259:4'})

    def test_runtime_refuses_wrong_boot_before_any_device_action(self):
        with mock.patch.object(M.os, 'geteuid', return_value=0), \
                mock.patch.object(Path, 'read_text', return_value='ffffffff-ffff-ffff-ffff-ffffffffffff'), \
                self.assertRaisesRegex(ValueError, 'boot ID differs'):
            M.runtime_guard('11111111-1111-4111-8111-111111111111')

    def test_ioctl_bytes_decode_exact_integer(self):
        with mock.patch.object(M.fcntl, 'ioctl', return_value=(512).to_bytes(4, sys.byteorder)):
            self.assertEqual(M.ioctl_number(123, 0x1268, '=I'), 512)


class MountTests(unittest.TestCase):
    def test_mounted_device_and_options_must_match(self):
        point = Path('/fixture')
        line = '1 0 259:4 / /fixture rw,nosuid,nodev,noexec - ext4 /dev/nvme0n1p4 rw\n'
        with mock.patch.object(M, 'mounts', return_value=M.mounts(line)), \
                mock.patch.object(Path, 'stat', return_value=mock.Mock(st_dev=os.makedev(259, 4))):
            M.verify_mount(point, '259:4')
            with self.assertRaises(ValueError):
                M.verify_mount(point, '259:3')
        with mock.patch.object(M, 'mounts', return_value=M.mounts(line.replace(',nodev', ''))), \
                self.assertRaises(ValueError):
            M.verify_mount(point, '259:4')

    def test_apply_failure_unmounts_owned_mount_without_reboot(self):
        data = fixture()[1]
        with tempfile.TemporaryDirectory() as temporary:
            point = Path(temporary) / 'owned'
            point.mkdir()
            args = mock.Mock(expected_boot_id=str(uuid.uuid4()), source_archive=Path('/run/source'), apply=True)
            report = {'changed_files': []}
            with mock.patch.object(M, 'runtime_guard'), mock.patch.object(M, 'source_files', return_value=data), \
                    mock.patch.object(M, 'inspect_target', return_value={M.PART.name: '259:4'}), \
                    mock.patch.object(M.tempfile, 'mkdtemp', return_value=str(point)), \
                    mock.patch.object(M.subprocess, 'run') as run, \
                    mock.patch.object(M, 'verify_mount', side_effect=ValueError('wrong mount')):
                with self.assertRaises(ValueError):
                    M.execute(args, report)
                self.assertEqual(run.call_args_list[-1].args[0], ['/bin/umount', str(point)])
                self.assertTrue(report['unmounted'])
                self.assertFalse(point.exists())

    def test_plan_never_mounts(self):
        args = mock.Mock(expected_boot_id=str(uuid.uuid4()), source_archive=Path('/run/source'), apply=False)
        report = {'changed_files': []}
        with mock.patch.object(M, 'runtime_guard'), mock.patch.object(M, 'source_files', return_value=fixture()[1]), \
                mock.patch.object(M, 'inspect_target', return_value={}), \
                mock.patch.object(M.subprocess, 'run') as run:
            M.execute(args, report)
            run.assert_not_called()
            self.assertTrue(report['completed'])


if __name__ == '__main__':
    unittest.main()
