"""Host-only identity parser, refusal, cleanup and mount guards; no devices/mounts."""
import base64
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
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


# Public factory bytes, never the original private identity archive. Copied only
# after authenticating the producer manifests: predecessor 15468 bytes/SHA256
# b8789e3da623866fa8a9d67cad75ef10a6143731ca7d882503e69d3ea67d5fac;
# normalized full image 118399 bytes/SHA256
# 3cbe28d6154d1bdeb8d364bd0f8bf82b61fd40af7be27fbd49695771c4fad08f.
FACTORY_MACHINE_IDS = (
    b'896a9617df698dcfc4774bb60efbfb06\n',
    b'6256ad797317ee26ed1f2e3c3c0fb33b\n',
)
NORMALIZED_SELECTION = {
    'KERNEL_RELEASE': '6.6.98-a733-diag',
    'ROOT_MARKER_SHA256': '286494073ce3a0b69443f96389dbbce11ab015705f7e9f05c7a7cf06503da866',
    'ROOT_UUID': 'a7330000-0000-4000-8000-00000000000a',
    'SCHEMA': '1',
    'SLOT': 'A',
}


def fixture(extra=None, mode=None, pax=None, include_directories=True):
    blob = b'\0\0\0\x0bssh-ed25519\0\0\0\x20' + b'x' * 32
    files = {'identity/machine-id': b'1' * 32 + b'\n',
             'identity/ssh/ssh_host_ed25519_key.pub': b'ssh-ed25519 ' + base64.b64encode(blob) + b' fixture\n',
             'identity/ssh/ssh_host_ed25519_key': b'-----BEGIN OPENSSH PRIVATE KEY-----\n' +
             base64.b64encode(b'openssh-key-v1\0' + blob) + b'\n-----END OPENSSH PRIVATE KEY-----\n'}
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w', format=tarfile.PAX_FORMAT) as tar:
        if include_directories:
            for name in ('identity', 'identity/ssh'):
                member = tarfile.TarInfo(name)
                member.type = tarfile.DIRTYPE
                member.mode = 0o700
                member.pax_headers = dict(pax or {})
                tar.addfile(member)
        for name, data in files.items():
            member = tarfile.TarInfo(name)
            member.mode = mode if mode is not None else M.FILES[name]
            member.size = len(data)
            member.pax_headers = dict(pax or {})
            tar.addfile(member, io.BytesIO(data))
        if extra:
            tar.addfile(extra, io.BytesIO(b''))
    fp = 'SHA256:' + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip('=')
    return stream.getvalue(), files, fp


def superblock_fixture():
    data = bytearray(1024)
    data[56:58] = b'\x53\xef'
    data[104:120] = uuid.UUID(M.STATE_UUID).bytes
    data[120:130] = b'a733-state'
    for offset, value in ((4, M.PART_SIZE // 4096), (24, 2),
                          (92, 0x3c), (96, 0x2c2), (100, 0x46b), (224, 8)):
        struct.pack_into('<I', data, offset, value)
    return data


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

    def test_timestamp_extensions_are_allowed_but_other_pax_fields_are_refused(self):
        data, files, fp = fixture(pax={'mtime': '1777319348.692000006',
                                     'atime': '1793952102', 'ctime': '48.336000022'})
        self.assertEqual(self.parse(data, fp), files)
        for pax in ({'SCHILY.xattr.test': 'value'}, {'linkpath': '../other'}, {'ctime': 'invalid'}):
            data, _, fp = fixture(pax=pax)
            with self.subTest(pax=pax), self.assertRaises((ValueError, tarfile.TarError)):
                self.parse(data, fp)

    def test_both_pinned_archive_directories_are_required(self):
        data, _, fp = fixture(include_directories=False)
        with self.assertRaisesRegex(ValueError, 'missing identity file or directory'):
            self.parse(data, fp)


class SuperblockTests(unittest.TestCase):
    def test_known_profile_with_internal_journal_is_allowed(self):
        data = superblock_fixture()
        M.validate_superblock(data)

    def test_dirty_journal_and_orphan_metadata_remain_allowed_for_rw_recovery(self):
        data = superblock_fixture()
        for offset, value in ((92, 0x103c), (96, 0x2c6), (100, 0x1046b), (232, 1)):
            struct.pack_into('<I', data, offset, value)
        M.validate_superblock(data)

    def test_identity_and_block_geometry_are_required(self):
        for offset in (4, 24, 56, 104, 120, 336):
            data = superblock_fixture()
            data[offset] ^= 1
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                M.validate_superblock(data)
        with self.assertRaises(ValueError):
            M.validate_superblock(superblock_fixture()[:-1])

    def test_unsafe_journal_is_refused_before_either_apply_preflight_mount(self):
        # Exercise the production raw validator through both execute preflights.
        # No target node, mountpoint or subprocess is opened by these fixtures.
        cases = [('external UUID', 208, 1), ('external device', 228, 1),
                 ('missing inode', 224, 0), ('other inode', 224, 9),
                 ('journal device feature', 96, 0x2ca),
                 ('unsupported incompat', 96, 0x102c2),
                 ('missing has_journal', 92, 0x38)]
        for name, offset, value in cases:
            data = superblock_fixture()
            struct.pack_into('<I', data, offset, value)
            with self.subTest(name=name), self.assertRaises(ValueError):
                M.validate_superblock(data)
            for refused_preflight in (1, 2):
                checks = []
                def inspect():
                    checks.append(None)
                    M.validate_superblock(data if len(checks) == refused_preflight
                                          else superblock_fixture())
                    return {M.PART.name: '259:4'}
                args = mock.Mock(expected_boot_id=str(uuid.uuid4()),
                                 source_archive=Path('/run/source'), apply=True)
                with self.subTest(name=name, preflight=refused_preflight), \
                        mock.patch.object(M, 'runtime_guard'), \
                        mock.patch.object(M, 'source_files', return_value=fixture()[1]), \
                        mock.patch.object(M, 'inspect_target', side_effect=inspect), \
                        mock.patch.object(M.tempfile, 'mkdtemp') as mkdtemp, \
                        mock.patch.object(M.subprocess, 'run') as run:
                    with self.assertRaises(ValueError):
                        M.execute(args, {'changed_files': []})
                    self.assertEqual(len(checks), refused_preflight)
                    mkdtemp.assert_not_called()
                    run.assert_not_called()


class FactoryHashTests(unittest.TestCase):
    def test_registry_is_exactly_the_two_authenticated_factory_ids(self):
        self.assertEqual(M.FRESH_MACHINE_ID_HASHES, frozenset(M.digest(mid) for mid in FACTORY_MACHINE_IDS))
        self.assertIn(M.FRESH_MACHINE_ID_HASH, M.FRESH_MACHINE_ID_HASHES)

    def test_internal_hash_argument_retains_string_support_and_rejects_unbounded_types(self):
        with mock.patch.object(M, 'check_state') as check:
            for allowance in (M.FRESH_MACHINE_ID_HASH, M.FRESH_MACHINE_ID_HASHES):
                self.assertEqual(M.prepare_destinations(Path('/unused'), {}, allowance), {})
            check.reset_mock()
            for allowance in (None, True, [], {}, {M.FRESH_MACHINE_ID_HASH},
                              frozenset(), frozenset({'a' * 64, 'b' * 64, 'c' * 64}),
                              frozenset({1}), frozenset({'A' * 64}), 'a' * 63, 'a' * 65):
                with self.subTest(allowance_type=type(allowance).__name__), \
                        self.assertRaisesRegex(ValueError, 'invalid factory machine-id hash allowance'):
                    M.prepare_destinations(Path('/unused'), {}, allowance)
            check.assert_not_called()


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

    def seed_factory_and_sentinels(self, factory, slot='A'):
        target = self.root / 'identity/machine-id'
        target.write_bytes(factory)
        target.chmod(0o644)
        selection = dict(NORMALIZED_SELECTION, SLOT=slot,
                         ROOT_UUID='a7330000-0000-4000-8000-00000000000' + slot.lower())
        sentinels = {
            'boot/selected.conf': ''.join(f'{key}={selection[key]}\n' for key in
                                         ('SCHEMA', 'SLOT', 'ROOT_UUID', 'ROOT_MARKER_SHA256',
                                          'KERNEL_RELEASE')).encode(),
            'config/management.json': b'{"ssh_port":22,"permit_any_subnet":true}\n',
            'root/.ssh/authorized_keys': b'synthetic original admin key one\nsynthetic original admin key two\n',
            'var/lib/systemd/timesync/clock': b'preserve clock state',
        }
        if slot == 'A':
            self.assertEqual(M.digest(sentinels['boot/selected.conf']),
                             '3c76b96c328b4508ff407c8594352e04389664e97ced6f0a09284cade5bd027b')
        for name, data in sentinels.items():
            path = self.root / name
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            path.write_bytes(data)
            path.chmod(0o600)
            if name.endswith('/clock'):
                os.chown(path, 996, 996)
        return self.unchanged_state_snapshot()

    def unchanged_state_snapshot(self):
        snapshot = {}
        for path in [self.root, *self.root.rglob('*')]:
            relative = str(path.relative_to(self.root))
            if relative in M.FILES:
                continue
            info = path.lstat()
            metadata = (info.st_mode, info.st_uid, info.st_gid, info.st_ino)
            # Atomic replacement changes only these containing directories' times.
            if relative not in ('.', 'identity', 'identity/ssh'):
                metadata += (info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            snapshot[relative] = (metadata, path.read_bytes() if path.is_file() else None,
                                  {key: os.getxattr(path, key) for key in os.listxattr(path)})
        return snapshot

    def test_both_authenticated_factory_ids_replace_only_three_identity_files(self):
        self.assertEqual({M.digest(mid) for mid in FACTORY_MACHINE_IDS}, M.FRESH_MACHINE_ID_HASHES)
        for factory in FACTORY_MACHINE_IDS:
            for slot in ('B', 'A'):
                with self.subTest(factory_sha256=M.digest(factory), slot=slot):
                    # Return to a fresh factory state between cases.
                    for name in M.FILES:
                        (self.root / name).unlink(missing_ok=True)
                    before = self.seed_factory_and_sentinels(factory, slot)
                    self.assertEqual(M.prepare_destinations(self.root, self.data, M.FRESH_MACHINE_ID_HASHES),
                                     {'identity/machine-id': factory})
                    changed = []
                    M.atomic_files(self.root, self.data, M.FRESH_MACHINE_ID_HASHES, changed)
                    self.assertEqual(set(changed), set(M.FILES))
                    self.assertEqual(before, self.unchanged_state_snapshot())
                    for name, data in self.data.items():
                        self.assertEqual((self.root / name).read_bytes(), data)
                    changed = []
                    M.atomic_files(self.root, self.data, M.FRESH_MACHINE_ID_HASHES, changed)
                    self.assertEqual(changed, [])
                    self.assertEqual(before, self.unchanged_state_snapshot())

    def test_other_nonempty_ids_and_factory_byte_variants_refuse_before_writes(self):
        target = self.root / 'identity/machine-id'
        variants = [b'3' * 32 + b'\n']
        for factory in FACTORY_MACHINE_IDS:
            variants.extend((factory[:-1], factory[:-2] + b'\n', factory + b'\n',
                             factory[:-1] + b'\r\n', factory.upper()))
        for data in variants:
            with self.subTest(sha256=M.digest(data)):
                target.write_bytes(data)
                target.chmod(0o644)
                changed = []
                with self.assertRaisesRegex(ValueError, 'unexpected nonempty destination'):
                    M.atomic_files(self.root, self.data, M.FRESH_MACHINE_ID_HASHES, changed)
                self.assertEqual(changed, [])
                self.assertEqual(target.read_bytes(), data)
                self.assertFalse((self.root / 'identity/ssh/ssh_host_ed25519_key').exists())

    def test_new_factory_does_not_allow_foreign_key_or_unsafe_identity_directory(self):
        self.seed_factory_and_sentinels(FACTORY_MACHINE_IDS[1])
        key = self.root / 'identity/ssh/ssh_host_ed25519_key'
        key.write_bytes(b'foreign key material')
        key.chmod(0o600)
        with self.assertRaisesRegex(ValueError, 'unexpected nonempty destination'):
            M.atomic_files(self.root, self.data, M.FRESH_MACHINE_ID_HASHES, [])
        self.assertEqual((self.root / 'identity/machine-id').read_bytes(), FACTORY_MACHINE_IDS[1])
        key.unlink()
        (self.root / 'identity/ssh').chmod(0o755)
        with self.assertRaisesRegex(ValueError, 'incorrect object mode'):
            M.atomic_files(self.root, self.data, M.FRESH_MACHINE_ID_HASHES, [])
        (self.root / 'identity/ssh').chmod(0o700)
        (self.root / '.a733-state').write_text('a733-state-v1\n' + str(uuid.uuid4()) + '\n')
        with self.assertRaisesRegex(ValueError, 'STATE marker differs'):
            M.atomic_files(self.root, self.data, M.FRESH_MACHINE_ID_HASHES, [])

    def test_apply_routes_new_factory_through_real_atomic_files_and_preserves_state(self):
        before = self.seed_factory_and_sentinels(FACTORY_MACHINE_IDS[1], 'B')
        args = mock.Mock(expected_boot_id=str(uuid.uuid4()), source_archive=Path('/run/source'), apply=True)
        report = {'changed_files': []}
        # Fake mount/unmount only; the actual temporary filesystem and all STATE
        # ownership, destination preflight, atomic writes, fsync and readback are real.
        with mock.patch.object(M, 'runtime_guard'), \
                mock.patch.object(M, 'source_files', return_value=self.data), \
                mock.patch.object(M, 'inspect_target', return_value={M.PART.name: '259:4'}), \
                mock.patch.object(M.tempfile, 'mkdtemp', return_value=str(self.root)), \
                mock.patch.object(M.subprocess, 'run') as run, \
                mock.patch.object(M, 'verify_mount'), \
                mock.patch.object(Path, 'rmdir') as rmdir:
            M.execute(args, report)
        self.assertEqual(set(report['changed_files']), set(M.FILES))
        self.assertTrue(report['completed'] and report['files_fsynced'] and report['unmounted'])
        self.assertEqual(before, self.unchanged_state_snapshot())
        self.assertEqual(run.call_args_list[0].args[0],
                         ['/bin/mount', '-t', 'ext4', '-o', 'rw,nosuid,nodev,noexec', str(M.PART), str(self.root)])
        self.assertEqual(run.call_args_list[1].args[0], ['/bin/umount', str(self.root)])
        rmdir.assert_called_once_with()
        public_report = json.dumps(report)
        for secret in (FACTORY_MACHINE_IDS[1], self.data['identity/machine-id'],
                       self.data['identity/ssh/ssh_host_ed25519_key']):
            self.assertNotIn(secret.decode(), public_report)

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

    def test_apply_preflight_drift_still_refuses_before_mount(self):
        data = fixture()[1]
        different = dict(data, **{'identity/machine-id': b'4' * 32 + b'\n'})
        args = mock.Mock(expected_boot_id=str(uuid.uuid4()), source_archive=Path('/run/source'), apply=True)
        for sources, targets in (([data, different], [{M.PART.name: '259:4'}]),
                                 ([data, data], [{M.PART.name: '259:4'}, {M.PART.name: '259:9'}])):
            with self.subTest(source_changed=sources[0] != sources[1]), \
                    mock.patch.object(M, 'runtime_guard'), \
                    mock.patch.object(M, 'source_files', side_effect=sources), \
                    mock.patch.object(M, 'inspect_target', side_effect=targets), \
                    mock.patch.object(M.tempfile, 'mkdtemp') as mkdtemp, \
                    mock.patch.object(M.subprocess, 'run') as run:
                with self.assertRaisesRegex(ValueError, 'preflight changed'):
                    M.execute(args, {'changed_files': []})
                mkdtemp.assert_not_called()
                run.assert_not_called()

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
