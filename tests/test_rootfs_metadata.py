"""Small regular-file fixtures only; no mounts, network or hardware acceptance."""
import base64
from contextlib import ExitStack
import copy
import gzip
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, REPO / 'build' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


META = load('test_rootfs_metadata_backend', 'rootfs-metadata.py')
PAYLOAD = load('test_rootfs_metadata_payload', 'host-payload.py')
STATE = load('test_rootfs_metadata_state', 'host-state.py')


def entry(name, kind=tarfile.REGTYPE, data=b'fixture', link='', attrs=None):
    member = tarfile.TarInfo(name)
    member.type, member.linkname = kind, link
    member.uid, member.gid = os.getuid(), os.getgid()
    member.mode = 0o755 if kind == tarfile.DIRTYPE else 0o777 if kind == tarfile.SYMTYPE else 0o644
    member.mtime = 1700000000
    member.pax_headers = {'mtime': '1700000000.123456789', **(attrs or {})}
    member.size = len(data) if kind == tarfile.REGTYPE else 0
    return member, data


class Fixtures(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)
        self.archive = self.work / 'input.tar'
        self.root = self.work / 'root'
        self.root.mkdir()
        self.profile = mock.patch.object(META, 'validate_profile', return_value={'fixture': {}})
        self.profile.start()
        self.addCleanup(self.profile.stop)

    def write_archive(self, entries):
        with tarfile.open(self.archive, 'w', format=tarfile.PAX_FORMAT) as archive:
            for member, data in [entry('.', tarfile.DIRTYPE), *entries]:
                archive.addfile(member, io.BytesIO(data) if member.isfile() else None)
        with self.archive.open('rb') as stream:
            return META.digest(stream)

    def extract(self, entries, **kwargs):
        expected = self.write_archive(entries)
        return META.extract(self.archive, self.root, expected, PAYLOAD.validate_tar, **kwargs)

    def rejected(self, entries, pattern=None):
        expected = self.write_archive(entries)
        with mock.patch.object(META.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, pattern or '.'):
                META.extract(self.archive, self.root, expected, PAYLOAD.validate_tar)
            run.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_complete_small_tree_with_links_and_fractional_mtime(self):
        receipt = self.extract([entry('dir', tarfile.DIRTYPE), entry('dir/file'),
                                entry('hard', tarfile.LNKTYPE, link='dir/file'),
                                entry('sym', tarfile.SYMTYPE, link='dir/file')])
        self.assertEqual(receipt['profile'], META.PROFILE)
        self.assertEqual(receipt['audit']['members'], 5)
        self.assertTrue(receipt['validation']['passed'])
        self.assertEqual((self.root / 'hard').stat().st_ino, (self.root / 'dir/file').stat().st_ino)
        self.assertEqual((self.root / 'dir/file').stat().st_mtime_ns, 1700000000123456789)
        self.assertEqual(len(receipt['expected_metadata_sha256']), 64)
        self.assertEqual(receipt['helper'], PAYLOAD.file_record(REPO / 'build/rootfs-metadata.py'))

    def test_raw_xattr_applied_after_tar_and_never_by_tar(self):
        original = subprocess.run
        events = []
        original_set = os.setxattr
        def run(command, **kwargs):
            events.append('tar')
            self.assertIn('--no-acls', command)
            self.assertIn('--no-xattrs', command)
            self.assertEqual(len(kwargs['pass_fds']), 2)
            self.assertTrue(command[command.index('--file') + 1].startswith('/proc/self/fd/'))
            return original(command, **kwargs)
        def setxattr(path, key, value, **kwargs):
            events.append('xattr')
            self.assertTrue(Path(path).is_file())
            self.assertEqual(value, b'\xff\x00binary')
            self.assertFalse(kwargs['follow_symlinks'])
            return original_set(path, key, value, **kwargs)
        with mock.patch.object(META.subprocess, 'run', side_effect=run), \
                mock.patch.object(META.os, 'setxattr', side_effect=setxattr):
            self.extract([entry('file', attrs={'SCHILY.xattr.user.fixture': '\udcff\x00binary'})])
        self.assertEqual(events, ['tar', 'xattr'])

    def test_traversal_and_absolute_names_rejected(self):
        for name in ('../escape', '/escape', 'dir/../../escape'):
            with self.subTest(name=name):
                self.rejected([entry(name)])

    def test_late_symlink_ancestor_rejected(self):
        self.rejected([entry('dir/file'), entry('dir', tarfile.SYMTYPE, link='/tmp')])

    def test_late_hardlink_target_must_be_regular(self):
        self.rejected([entry('hard', tarfile.LNKTYPE, link='late'), entry('late', tarfile.SYMTYPE, link='target')])

    def test_fifo_and_block_rejected(self):
        for kind in (tarfile.FIFOTYPE, tarfile.BLKTYPE):
            with self.subTest(kind=kind):
                self.rejected([entry('special', kind)])

    def test_char_whitelist_and_exact_metadata(self):
        for name, device, mode in [('dev/unknown', (1, 3), 0o666), ('dev/null', (1, 4), 0o666),
                                   ('dev/null', (1, 3), 0o600)]:
            member, data = entry(name, tarfile.CHRTYPE)
            member.uid = member.gid = 0
            member.devmajor, member.devminor = device
            member.mode = mode
            self.rejected([entry('dev', tarfile.DIRTYPE), (member, data)])
        entries = [entry('dev', tarfile.DIRTYPE)]
        for name, device in META.DEVICES.items():
            member, data = entry(name, tarfile.CHRTYPE)
            member.uid = member.gid = 0
            member.devmajor, member.devminor = device
            member.mode = 0o666
            entries.append((member, data))
        self.write_archive(entries)
        with self.archive.open('rb') as stream:
            records, audit = META.source_records(stream, PAYLOAD.validate_tar)
        self.assertEqual(audit['character_nodes'], 8)
        self.assertEqual(records['dev/null']['device'], [1, 3])

    def test_acl_without_raw_counterpart_rejected(self):
        for name in ('SCHILY.acl.access', 'SCHILY.acl.default', 'SCHILY.acl.unknown'):
            self.rejected([entry('file', attrs={name: 'user::rw-'})], 'raw counterpart')

    def test_invalid_xattr_names_and_ipc_limits_rejected(self):
        for attrs in ({'SCHILY.xattr.': 'x'}, {'SCHILY.xattr.user.a\0bad': 'x'},
                      {'SCHILY.xattr.user.a': 'x' * 1017},
                      {'SCHILY.xattr.user.' + str(i): '' for i in range(150)}):
            with self.subTest(keys=list(attrs)[:2]):
                self.rejected([entry('file', attrs=attrs)])

    def test_missing_expected_and_wrong_hash_rejected(self):
        expected = self.write_archive([entry('file')])
        for record in (None, {}, {**expected, 'sha256': '0' * 64}):
            with mock.patch.object(META.subprocess, 'run') as run, self.assertRaises(ValueError):
                META.extract(self.archive, self.root, record, PAYLOAD.validate_tar)
            run.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_source_symlink_and_destination_symlink_ancestors_rejected(self):
        expected = self.write_archive([entry('file')])
        link = self.work / 'link'
        link.symlink_to(self.archive)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            META.extract(link, self.root, expected, PAYLOAD.validate_tar)
        link.unlink()
        link.symlink_to(self.work, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            META.extract(self.archive, link / 'root', expected, PAYLOAD.validate_tar)

    def test_nonempty_and_overlapping_root_rejected(self):
        expected = self.write_archive([entry('file')])
        (self.root / 'existing').write_text('preserve')
        with self.assertRaises(ValueError):
            META.extract(self.archive, self.root, expected, PAYLOAD.validate_tar)
        with self.assertRaisesRegex(ValueError, 'overlap'):
            META.extract(self.archive, self.work, expected, PAYLOAD.validate_tar)
        self.assertEqual((self.root / 'existing').read_text(), 'preserve')

    def test_source_mutation_after_preflight_fails_before_extraction(self):
        expected = self.write_archive([entry('file')])
        original = META.source_records
        def mutate(*args):
            result = original(*args)
            with self.archive.open('ab') as changed:
                changed.write(b'changed')
            return result
        with mock.patch.object(META, 'source_records', side_effect=mutate), \
                mock.patch.object(META.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'archive changed'):
                META.extract(self.archive, self.root, expected, PAYLOAD.validate_tar)
            run.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_source_mutation_during_tar_fails_before_raw_attributes(self):
        expected = self.write_archive([entry('file', attrs={'SCHILY.xattr.user.fixture': 'x'})])
        original = subprocess.run
        def mutate(*args, **kwargs):
            result = original(*args, **kwargs)
            with self.archive.open('ab') as changed:
                changed.write(b'changed')
            return result
        with mock.patch.object(META.subprocess, 'run', side_effect=mutate), \
                mock.patch.object(META.os, 'setxattr') as setxattr:
            with self.assertRaisesRegex(ValueError, 'archive changed'):
                META.extract(self.archive, self.root, expected, PAYLOAD.validate_tar)
            setxattr.assert_not_called()

    def test_complete_comparison_detects_content_mode_type_namespace_and_links(self):
        expected = self.write_archive([entry('file'), entry('other', data=b'fixture'),
                                       entry('hard', tarfile.LNKTYPE, link='file')])
        with self.archive.open('rb') as stream:
            records, _ = META.source_records(stream, PAYLOAD.validate_tar)
        META.extract(self.archive, self.root, expected, PAYLOAD.validate_tar)
        for field, value in [('mode', 0o600), ('type', 'dir'), ('data', {'bytes': 7, 'sha256': '0' * 64}),
                             ('xattrs', {'user.extra': base64.b64encode(b'x').decode()})]:
            changed = copy.deepcopy(records)
            changed['file'][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                META.compare_tree(self.root, changed)
        changed = copy.deepcopy(records)
        changed['hard']['link'] = 'other'
        with self.assertRaisesRegex(ValueError, 'topology'):
            META.compare_tree(self.root, changed)
        changed.pop('other')
        with self.assertRaisesRegex(ValueError, 'namespace'):
            META.compare_tree(self.root, changed)


class ProfileTests(unittest.TestCase):
    def profile_context(self):
        stack = ExitStack()
        stack.enter_context(mock.patch.dict(os.environ, {'FAKEROOTKEY': '123', 'LD_PRELOAD': 'libfakeroot-sysv.so'}, clear=True))
        stack.enter_context(mock.patch.object(META.os, 'getuid', return_value=0))
        stack.enter_context(mock.patch.object(META.os, 'geteuid', return_value=0))
        stack.enter_context(mock.patch.object(META.platform, 'freedesktop_os_release', return_value={'ID': 'ubuntu', 'VERSION_ID': '22.04'}))
        stack.enter_context(mock.patch.object(META.platform, 'machine', return_value='x86_64'))
        output = 'fakeroot\t1.28-1ubuntu1\tamd64\tinstalled\nlibfakeroot:amd64\t1.28-1ubuntu1\tamd64\tinstalled\n'
        stack.enter_context(mock.patch.object(META.subprocess, 'run', return_value=mock.Mock(stdout=output)))
        return stack

    def test_pinned_profile(self):
        with self.profile_context():
            self.assertEqual(META.validate_profile()['fakeroot']['version'], '1.28-1ubuntu1')

    def test_environment_failures(self):
        for env in ({'FAKEROOTKEY': ''}, {'LD_PRELOAD': ''}):
            with self.profile_context(), mock.patch.dict(os.environ, env), self.assertRaises(ValueError):
                META.validate_profile()
        with self.profile_context(), mock.patch.object(META.os, 'getuid', return_value=1000), self.assertRaises(ValueError):
            META.validate_profile()

    def test_platform_and_package_mismatch(self):
        with self.profile_context(), mock.patch.object(META.platform, 'machine', return_value='aarch64'), self.assertRaises(ValueError):
            META.validate_profile()
        with self.profile_context(), mock.patch.object(META.platform, 'freedesktop_os_release', return_value={'ID': 'debian', 'VERSION_ID': '12'}), self.assertRaises(ValueError):
            META.validate_profile()
        for output in ('fakeroot\t1.29\tamd64\tinstalled\n',
                       'fakeroot\t1.28-1ubuntu1\tarm64\tinstalled\n',
                       'fakeroot\t1.28-1ubuntu1\tamd64\tconfig-files\n'):
            with self.profile_context(), mock.patch.object(META.subprocess, 'run', return_value=mock.Mock(stdout=output)), self.assertRaises(ValueError):
                META.validate_profile()


class WiringTests(unittest.TestCase):
    def test_native_extraction_keeps_original_tar_command_and_none_return(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive, root = Path(temporary) / 'input.tar', Path(temporary) / 'root'
            with tarfile.open(archive, 'w') as tar:
                tar.addfile(entry('.', tarfile.DIRTYPE)[0])
            with mock.patch.object(PAYLOAD.subprocess, 'run') as run, \
                    mock.patch.object(PAYLOAD.importlib.util, 'spec_from_file_location') as load_spec:
                self.assertIsNone(PAYLOAD.extract_rootfs(archive, root))
                load_spec.assert_not_called()
                run.assert_called_once_with(['tar', '--extract', '--file', str(archive), '--directory', str(root),
                    '--numeric-owner', '--same-owner', '--same-permissions', '--xattrs', '--xattrs-include=*', '--acls'], check=True)

    def test_optional_extract_forwards_pin_and_existing_namespace_validator(self):
        backend = mock.Mock()
        backend.extract.return_value = {'passed': True}
        with mock.patch.object(PAYLOAD, 'metadata_backend', return_value=backend):
            self.assertEqual(PAYLOAD.extract_rootfs('archive', 'root', META.PROFILE, {'pin': 1}), {'passed': True})
        backend.extract.assert_called_once_with('archive', 'root', {'pin': 1}, PAYLOAD.validate_tar)

    def test_payload_build_optional_receipt_provenance_and_native_compatibility(self):
        for profile in ('native', META.PROFILE):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
                work = Path(temporary)
                archive = work / 'input.tar'
                with tarfile.open(archive, 'w') as tar:
                    tar.addfile(entry('.', tarfile.DIRTYPE)[0])
                diagnostic = work / 'diag'
                diagnostic.mkdir()
                for name in PAYLOAD.BOOT_FILES:
                    (diagnostic / name).write_bytes(gzip.compress(b'fixture') if name == 'initramfs.cpio.gz' else b'fixture')
                output = work / 'output'
                backend = mock.Mock(LOCK=META.LOCK) if profile != 'native' else None
                receipt = {'profile': profile, 'helper': PAYLOAD.file_record(REPO / 'build/rootfs-metadata.py')}
                patches = {'validate_metadata_profile': mock.Mock(return_value=backend),
                           'validate_output': mock.Mock(return_value=output),
                           'validate_builder_packages': mock.Mock(return_value={}),
                           'authenticate': mock.Mock(), 'validate_kernel': mock.Mock(),
                           'module_entries': mock.Mock(return_value={}),
                           'extract_rootfs': mock.Mock(return_value=receipt),
                           'import_modules': mock.Mock(return_value={}),
                           'install_management': mock.Mock(return_value={}),
                           'maintenance_entries': mock.Mock(return_value={}),
                           'install_maintenance': mock.Mock(return_value={}),
                           'tool_version': mock.Mock(return_value='fixture')}
                markers = []
                def image(root, destination, slot):
                    markers.append(json.loads((root / 'usr/lib/orangepi-hypervisor/payload.json').read_text()))
                    destination.write_bytes(b'image')
                patches['make_image'] = mock.Mock(side_effect=image)
                for name, value in patches.items():
                    stack.enter_context(mock.patch.object(PAYLOAD, name, value))
                stack.enter_context(mock.patch.object(PAYLOAD.os, 'geteuid', return_value=0))
                stack.enter_context(mock.patch.object(PAYLOAD.shutil, 'which', return_value='/tool'))
                stack.enter_context(mock.patch.object(PAYLOAD.shutil, 'disk_usage', return_value=mock.Mock(free=10**15)))
                stack.enter_context(mock.patch.object(PAYLOAD.PACKAGE, 'snapshot', return_value={}))
                stack.enter_context(mock.patch.object(PAYLOAD.PACKAGE, 'prune_caches', return_value={}))
                PAYLOAD.build(archive, diagnostic, output, metadata_profile=profile)
                manifest = json.loads((output / 'manifest.json').read_text())
                args = patches['extract_rootfs'].call_args.args
                self.assertEqual(args[0], archive)
                if backend is None:
                    self.assertEqual(len(args), 2)
                    self.assertNotIn('metadata_extraction', manifest)
                    self.assertNotIn('rootfs_metadata', markers[0]['sources'])
                    self.assertFalse((output / 'provenance/rootfs-metadata.py').exists())
                else:
                    self.assertEqual(args[2], profile)
                    self.assertEqual(args[3], manifest['sources']['rootfs.tar.gz'])
                    self.assertEqual(manifest['metadata_extraction'], receipt)
                    self.assertEqual(markers[0]['sources']['rootfs_metadata']['helper'], receipt['helper'])
                    self.assertEqual(manifest['files']['provenance/rootfs-metadata.py'], receipt['helper'])
                    self.assertIn('provenance/' + META.LOCK.name, manifest['files'])

    def test_state_build_optional_receipt_provenance_and_native_compatibility(self):
        for profile in ('native', META.PROFILE):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
                work = Path(temporary)
                archive = work / 'input.tar'
                with tarfile.open(archive, 'w') as tar:
                    tar.addfile(entry('.', tarfile.DIRTYPE)[0])
                expected = PAYLOAD.file_record(archive)
                for name in ('config', 'key', 'selection'):
                    (work / name).write_bytes(b'fixture')
                output = work / 'output'
                backend = mock.Mock(LOCK=META.LOCK) if profile != 'native' else None
                receipt = {'profile': profile, 'helper': PAYLOAD.file_record(REPO / 'build/rootfs-metadata.py')}
                bounded = STATE.bounded_input
                def read(path, limit):
                    if Path(path).name == 'host-userspace-20261104.json':
                        return json.dumps({'files': {'rootfs.tar.gz': expected}}).encode()
                    return bounded(path, limit)
                def seed(extracted, state, *args):
                    state.mkdir()
                    return 'fixture-machine-id'
                def image(root, destination, uuid):
                    destination.write_bytes(b'image')
                    return []
                patches = {'validate_output': mock.Mock(return_value=output),
                           'bounded_input': mock.Mock(side_effect=read),
                           'validate_config': mock.Mock(return_value={}),
                           'validate_public_keys': mock.Mock(return_value=(b'key', ['fingerprint'])),
                           'validate_workspace_storage': mock.Mock(return_value={}),
                           'seed_state': mock.Mock(side_effect=seed), 'contents': mock.Mock(return_value=[]),
                           'make_image': mock.Mock(side_effect=image),
                           'compress_image': mock.Mock(side_effect=lambda source, dest: dest.write_bytes(b'gzip'))}
                for name, value in patches.items():
                    stack.enter_context(mock.patch.object(STATE, name, value))
                stack.enter_context(mock.patch.object(STATE.SELECTION, 'parse', return_value={}))
                stack.enter_context(mock.patch.object(STATE.os, 'geteuid', return_value=0))
                stack.enter_context(mock.patch.object(STATE.shutil, 'which', return_value='/tool'))
                stack.enter_context(mock.patch.object(STATE.shutil, 'disk_usage', return_value=mock.Mock(free=10**15)))
                stack.enter_context(mock.patch.object(STATE.PAYLOAD, 'validate_metadata_profile', return_value=backend))
                stack.enter_context(mock.patch.object(STATE.PAYLOAD, 'validate_builder_packages', return_value={}))
                stack.enter_context(mock.patch.object(STATE.PAYLOAD, 'tool_version', return_value='fixture'))
                stack.enter_context(mock.patch.object(STATE.PAYLOAD.PACKAGE, 'prune_caches', return_value={}))
                extract = stack.enter_context(mock.patch.object(STATE.PAYLOAD, 'extract_rootfs', return_value=receipt))
                STATE.build(archive, work / 'config', work / 'key', output, work / 'selection', metadata_profile=profile)
                manifest = json.loads((output / 'manifest.json').read_text())
                args = extract.call_args.args
                if backend is None:
                    self.assertEqual(len(args), 2)
                    self.assertNotIn('metadata_extraction', manifest)
                    self.assertNotIn('rootfs_metadata', manifest['sources'])
                else:
                    self.assertEqual(args[2:], (profile, expected))
                    self.assertEqual(manifest['metadata_extraction'], receipt)
                    self.assertEqual(manifest['sources']['rootfs_metadata']['helper'], receipt['helper'])
                    self.assertEqual(manifest['files']['provenance/rootfs-metadata.py'], receipt['helper'])
                    self.assertIn('provenance/' + META.LOCK.name, manifest['files'])

    def test_unknown_profile_rejected(self):
        with self.assertRaises(ValueError):
            PAYLOAD.metadata_backend('auto')

    def test_builds_validate_profile_before_output_or_large_processing(self):
        for module, arguments in ((PAYLOAD, ['rootfs', 'diagnostic', 'output']),
                                  (STATE, ['rootfs', 'config', 'key', 'output', 'selection'])):
            owner = PAYLOAD if module is PAYLOAD else STATE.PAYLOAD
            with mock.patch.object(owner, 'validate_metadata_profile', side_effect=ValueError('profile rejected')) as validate, \
                    mock.patch.object(module, 'validate_output') as output:
                with self.assertRaisesRegex(ValueError, 'profile rejected'):
                    module.build(*arguments, metadata_profile=META.PROFILE)
                validate.assert_called_once_with(META.PROFILE)
                output.assert_not_called()


if __name__ == '__main__':
    unittest.main()
