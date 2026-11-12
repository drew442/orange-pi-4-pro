"""Regular-file tests only; no mount, chroot or hardware access."""
from contextlib import ExitStack
import gzip
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import sys
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock
import uuid

SPEC = importlib.util.spec_from_file_location('host_payload', Path(__file__).resolve().parents[1] / 'build/host-payload.py')
payload = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(payload)


def tar_member(name, kind=tarfile.REGTYPE, link=''):
    member = tarfile.TarInfo(name)
    member.type = kind
    member.linkname = link
    return member


def newc(entries):
    data = bytearray()
    for name, mode, content, nlink in entries + [('TRAILER!!!', 0, b'', 1)]:
        name = name.encode() + b'\0'
        fields = [1, mode, 0, 0, nlink, 0, len(content), 0, 0, 0, 0, len(name), 0]
        data += b'070701' + ''.join(f'{n:08x}' for n in fields).encode() + name
        data += b'\0' * (-len(data) % 4)
        data += content
        data += b'\0' * (-len(data) % 4)
    return bytes(data)


def modules(extra=None):
    release = 'lib/modules/' + payload.KERNEL_RELEASE
    return [('lib/modules', stat.S_IFDIR | 0o755, b'', 2),
            (release, stat.S_IFDIR | 0o755, b'', 2),
            (release + '/fixture.ko', stat.S_IFREG | 0o644, b'fixture', 1)] + (extra or [])


class ArchiveTests(unittest.TestCase):
    def test_machine_id_commit_mask_preserves_vendor_unit_and_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            vendor = root / 'usr/lib/systemd/system/systemd-machine-id-commit.service'
            vendor.parent.mkdir(parents=True)
            vendor.write_bytes(b'[Service]\nExecStart=/lib/systemd/systemd-machine-id-setup --commit\n')
            identity = root / 'etc/machine-id'
            identity.parent.mkdir()
            identity.write_bytes(b'0123456789abcdef0123456789abcdef\n')
            identity.chmod(0o644)
            originals = {path: (path.read_bytes(), path.stat()) for path in (vendor, identity)}
            records = payload.install_machine_id_commit_mask(root)
            relative = 'etc/systemd/system/systemd-machine-id-commit.service'
            mask = root / relative
            self.assertTrue(stat.S_ISLNK(mask.lstat().st_mode))
            self.assertEqual(os.readlink(mask), '/dev/null')
            self.assertEqual(records, {relative: {'type': 'symlink', 'target': '/dev/null', 'mode': 0o777}})
            self.assertEqual(list((root / 'etc/systemd/system').iterdir()), [mask])
            for path, (data, original) in originals.items():
                self.assertEqual(path.read_bytes(), data)
                self.assertEqual(path.stat(), original)
            self.assertEqual(identity.stat().st_mode & 0o777, 0o644)
            with self.assertRaisesRegex(ValueError, 'already exists'):
                payload.install_machine_id_commit_mask(root)

    def test_machine_id_commit_mask_refuses_existing_destination_and_unsafe_ancestors(self):
        relative = 'etc/systemd/system/systemd-machine-id-commit.service'
        for kind in ('file', 'directory', 'symlink', 'ancestor'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                target = root / relative
                if kind == 'ancestor':
                    (root / 'etc').symlink_to('/etc')
                else:
                    target.parent.mkdir(parents=True)
                    if kind == 'file':
                        target.write_text('keep')
                    elif kind == 'directory':
                        target.mkdir()
                    else:
                        target.symlink_to('/dev/null')
                with self.assertRaises(ValueError):
                    payload.install_machine_id_commit_mask(root)

    def test_maintenance_tree_is_complete_read_only_and_matches_portable_package(self):
        entries = payload.maintenance_entries()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installed = payload.install_maintenance(root, entries)
            self.assertEqual(installed['profile'], 'a733-maintenance-tools-v1')
            for name, (mode, data) in entries.items():
                relative = 'usr/lib/a733-tools/' + name
                path = root / relative
                self.assertEqual(path.read_bytes(), data)
                self.assertEqual(path.stat().st_mode & 0o7777, mode)
                self.assertEqual(installed['files'][relative], {**payload.file_record(path), 'mode': mode})
            for path in (root / 'usr/lib/a733-tools').rglob('*'):
                if path.is_dir():
                    self.assertEqual(path.stat().st_mode & 0o7777, 0o555)
            with self.assertRaisesRegex(ValueError, 'already exists'):
                payload.install_maintenance(root, entries)
            # Private test cleanup must restore directory write permission.
            for path in sorted((root / 'usr/lib/a733-tools').rglob('*'), reverse=True):
                if path.is_dir():
                    path.chmod(0o755)
            (root / 'usr/lib/a733-tools').chmod(0o755)

    def test_maintenance_staging_refuses_existing_objects_and_symlink_ancestors(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'usr/lib').mkdir(parents=True)
            (root / 'usr/lib/a733-tools').symlink_to(root / 'elsewhere')
            with self.assertRaises(ValueError):
                payload.install_maintenance(root)
            (root / 'usr/lib/a733-tools').unlink()
            (root / 'usr/lib').rmdir()
            (root / 'usr/lib').symlink_to(root.parent)
            with self.assertRaises(ValueError):
                payload.install_maintenance(root)

    def test_management_runtime_is_installed_and_bound_to_content_hashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            record = payload.install_management(root)
            self.assertEqual(record['profile'], 'a733-management-v1')
            self.assertEqual(len(record['files']), 4)
            for name, expected in record['files'].items():
                self.assertEqual(payload.file_record(root / name), expected)
            self.assertEqual((root / 'usr/lib/orangepi-hypervisor/host-management.py').stat().st_mode & 0o777, 0o755)
            with self.assertRaisesRegex(ValueError, 'already exists'):
                payload.install_management(root)

    def test_tar_traversal_and_absolute(self):
        for name in ('../escape', '/escape', 'usr/../../escape'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                payload.validate_tar([tar_member(name)])

    def test_tar_symlink_ancestors_and_escape(self):
        for entries in ([tar_member('escape', tarfile.SYMTYPE, '/tmp'), tar_member('escape/file')],
                        [tar_member('escape/file'), tar_member('escape', tarfile.SYMTYPE, '/tmp')],
                        [tar_member('escape', tarfile.SYMTYPE, '../outside')]):
            with self.assertRaises(ValueError):
                payload.validate_tar(entries)

    def test_tar_hardlinks_must_target_regular_internal_member(self):
        for target in ('../escape', '/escape', 'missing', 'link'):
            with self.subTest(target=target), self.assertRaises(ValueError):
                payload.validate_tar([tar_member('link', tarfile.SYMTYPE, '/tmp'),
                                      tar_member('hard', tarfile.LNKTYPE, target)])
        payload.validate_tar([tar_member('file'), tar_member('hard', tarfile.LNKTYPE, 'file')])

    def test_tar_safe_merged_usr_and_absolute_leaf(self):
        payload.validate_tar([tar_member('.', tarfile.DIRTYPE),
                              tar_member('usr', tarfile.DIRTYPE),
                              tar_member('usr/lib', tarfile.DIRTYPE),
                              tar_member('lib', tarfile.SYMTYPE, 'usr/lib'),
                              tar_member('leaf', tarfile.SYMTYPE, '/usr/bin/true')])

    def test_newc_only_modules_imported(self):
        entries = payload.module_entries(newc(modules([('init', stat.S_IFREG | 0o755, b'evil', 1)])))
        self.assertEqual(len(entries), 3)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'usr/lib').mkdir(parents=True)
            (root / 'lib').symlink_to('usr/lib')
            bom = payload.import_modules(root, entries)
            self.assertEqual(len(bom), 1)
            self.assertEqual((root / 'usr/lib/modules' / payload.KERNEL_RELEASE / 'fixture.ko').read_bytes(), b'fixture')
            self.assertFalse((root / 'init').exists())

    def test_newc_bad_paths_types_abi_truncation(self):
        for extra in ([('../escape', stat.S_IFREG | 0o644, b'', 1)],
                      [('/escape', stat.S_IFREG | 0o644, b'', 1)],
                      [('lib/modules/link', stat.S_IFLNK | 0o777, b'/tmp', 1)],
                      [('lib/modules/node', stat.S_IFCHR | 0o600, b'', 1)],
                      [('lib/modules/hard.ko', stat.S_IFREG | 0o644, b'', 2)],
                      [('lib/modules/wrong/file.ko', stat.S_IFREG | 0o644, b'', 1)]):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                payload.module_entries(newc(modules(extra)))
        with self.assertRaises(ValueError):
            payload.module_entries(newc(modules())[:-10])

    def test_tar_extraction_retains_xattrs(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / 'source'
            source.mkdir()
            (source / 'fixture').write_bytes(b'fixture')
            try:
                os.setxattr(source / 'fixture', 'user.payload-test', b'kept')
            except OSError as error:
                self.skipTest(str(error))
            archive = base / 'root.tar'
            subprocess.run(['tar', '--create', '--xattrs', '--acls', '--file', str(archive),
                            '--directory', str(source), '.'], check=True)
            target = base / 'target'
            target.mkdir()
            payload.extract_rootfs(archive, target)
            self.assertEqual(os.getxattr(target / 'fixture', 'user.payload-test'), b'kept')

    def test_bad_hash_and_nonregular_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'file'
            path.write_bytes(b'fixture')
            payload.authenticate(path, payload.file_record(path))
            with self.assertRaises(ValueError):
                payload.authenticate(path, {'bytes': 7, 'sha256': '0' * 64})
            link = Path(directory) / 'link'
            link.symlink_to(path)
            with self.assertRaises(ValueError):
                payload.require_regular(link)
            with self.assertRaises(ValueError):
                payload.require_regular(Path(directory))

    def test_builder_package_versions_and_multiarch_names(self):
        lock = payload.REPO / 'configs/builder-packages.lock.tsv'
        lines = [line for line in lock.read_text().splitlines()
                 if line.split('\t')[0].split(':')[0] in
                 ('e2fsprogs', 'libext2fs2', 'tar', 'gzip')]
        installed = '\n'.join(lines) + '\n'
        with mock.patch.object(payload.subprocess, 'check_output', return_value=installed) as query:
            records = payload.validate_builder_packages(lock)
            self.assertEqual(set(records), {'e2fsprogs', 'libext2fs2', 'tar', 'gzip'})
            self.assertIn('libext2fs2:amd64', query.call_args.args[0])
        for bad in (installed.replace('1.46.5-2ubuntu1.2', '1.47.0', 1),
                    installed.replace('\tamd64', '\tarm64', 1)):
            with mock.patch.object(payload.subprocess, 'check_output', return_value=bad):
                with self.assertRaisesRegex(ValueError, 'versions/architectures differ'):
                    payload.validate_builder_packages(lock)

    def test_kernel_config(self):
        good = '# Linux/arm64 6.6.98 Kernel Configuration\nCONFIG_EXT4_FS=y\n'
        payload.validate_kernel(good)
        for bad in (good.replace('=y', '=m'), good.replace('6.6.98', '6.6.99')):
            with self.assertRaises(ValueError):
                payload.validate_kernel(bad)

    def test_output_and_staging_escape(self):
        for path in (Path('/tmp/host-payload-test'), payload.REPO / 'out/host-payload-',
                     Path('out/host-payload-test')):
            with self.assertRaises(ValueError):
                payload.validate_output(path)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'escape').symlink_to('/tmp')
            with self.assertRaises(ValueError):
                payload.within_root(root, 'escape/file')


class UserspaceProfiles(unittest.TestCase):
    def test_clock_profile_binds_actual_packaged_checker(self):
        for profile in (payload.DEFAULT_USERSPACE_PROFILE, 'normalized-20261106'):
            self.assertIsNone(payload.clock_state_policy(profile))
        policy = payload.clock_state_policy('timesync-20261106')
        self.assertEqual(policy['identity'], 996)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = payload.install_maintenance(root)
            self.assertEqual(len(runtime['files']), 56)
            helper = root / 'usr/lib/a733-tools/build/timesync-state.py'
            payload.authenticate(helper, policy['helper'])
            helper.chmod(0o644)
            helper.write_bytes(b'changed')
            with self.assertRaises(ValueError):
                payload.authenticate(helper, policy['helper'])

    def test_source_parity_is_typed_and_presence_sensitive(self):
        expected = {'sources': {'rootfs.tar.gz': {'bytes': 1, 'sha256': 'a' * 64}}}
        payload.validate_source_bindings(expected, expected)
        for actual in ({'sources': {}}, {'sources': {'rootfs.tar.gz': None}},
                       {'sources': {'rootfs.tar.gz': {'bytes': True, 'sha256': 'a' * 64}}}):
            with self.subTest(actual=actual), self.assertRaises(ValueError):
                payload.validate_source_bindings(actual, expected)

    def test_resolver_delegation_and_repository_override(self):
        backend = mock.Mock()
        backend.resolve.return_value = {'selected': True}
        with mock.patch.object(payload, 'userspace_backend', return_value=backend):
            resolve = backend.resolve
            self.assertEqual(payload.userspace_binding(), {'selected': True})
            resolve.assert_called_once_with(payload.DEFAULT_USERSPACE_PROFILE, repo=payload.REPO)
            resolve.reset_mock()
            payload.userspace_binding('normalized-20261106', repo=Path('/fixture'))
            resolve.assert_called_once_with('normalized-20261106', repo=Path('/fixture'))

    def test_shared_runtime_import_does_not_load_source_only_profile_helper(self):
        with mock.patch.object(payload.importlib.util, 'spec_from_file_location', wraps=importlib.util.spec_from_file_location) as load:
            module = importlib.util.module_from_spec(SPEC)
            SPEC.loader.exec_module(module)
            self.assertFalse(any(Path(call.args[1]).name == 'userspace-profiles.py' for call in load.call_args_list))
        backend = payload.userspace_backend()
        self.assertEqual(backend.DEFAULT, payload.DEFAULT_USERSPACE_PROFILE)
        self.assertEqual(backend.PROFILES, payload.USERSPACE_PROFILES)

    def test_cli_default_and_explicit_profiles(self):
        for options, profile, metadata in (([], payload.DEFAULT_USERSPACE_PROFILE, 'native'),
                (['--userspace-profile', 'normalized-20261106', '--metadata-profile', 'fakeroot-raw-xattrs-v1'],
                 'normalized-20261106', 'fakeroot-raw-xattrs-v1')):
            with mock.patch.object(sys, 'argv', ['host-payload.py', 'rootfs', 'diag', 'output', *options]), mock.patch.object(payload, 'build') as build:
                payload.main()
                self.assertEqual(build.call_args.args, (Path('rootfs'), Path('diag'), Path('output'), metadata, profile))

    def test_profiles_rejected_before_output_validation(self):
        with mock.patch.object(payload, 'userspace_binding', side_effect=ValueError('profile rejected')), mock.patch.object(payload, 'validate_output') as output:
            with self.assertRaisesRegex(ValueError, 'profile rejected'):
                payload.build('missing', 'missing', 'missing', userspace_profile='unknown')
            output.assert_not_called()

    def test_wrong_profile_archive_fails_before_output_mutation(self):
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            work = Path(temporary)
            archive = work / 'wrong.tar'
            archive.write_bytes(b'wrong profile')
            diagnostic = work / 'diag'
            diagnostic.mkdir()
            output = work / 'absent/host-payload-wrong'
            stack.enter_context(mock.patch.object(payload, 'userspace_binding', return_value={
                'evidence': payload.REPO / 'docs/evidence/host-userspace-20261104.json',
                'archive': {'bytes': 1, 'sha256': '0' * 64}}))
            stack.enter_context(mock.patch.object(payload, 'validate_output', return_value=output))
            stack.enter_context(mock.patch.object(payload, 'validate_builder_packages', return_value={}))
            stack.enter_context(mock.patch.object(payload.os, 'geteuid', return_value=0))
            stack.enter_context(mock.patch.object(payload.shutil, 'which', return_value='/tool'))
            with self.assertRaisesRegex(ValueError, 'hash or size mismatch'):
                payload.build(archive, diagnostic, output, userspace_profile='normalized-20261106')
            self.assertFalse(output.parent.exists())

    def test_flat_provenance_rejects_collisions(self):
        self.assertEqual(payload.provenance_paths((Path('/a/proof'), Path('/a/proof'))), (Path('/a/proof'),))
        with self.assertRaisesRegex(ValueError, 'basename collision'):
            payload.provenance_paths((Path('/a/proof'), Path('/b/proof')))

    def test_profile_markers_manifest_provenance_and_metadata_pin(self):
        cases = [(metadata, 'normalized-20261106', False, False) for metadata in payload.METADATA_PROFILES] + [
            ('native', payload.DEFAULT_USERSPACE_PROFILE, False, False),
            ('native', 'timesync-20261106', False, False),
            ('native', 'normalized-20261106', True, False), ('native', 'normalized-20261106', False, True)]
        for metadata, userspace_profile, changed_provenance, no_space in cases:
            with self.subTest(metadata=metadata, changed_provenance=changed_provenance, no_space=no_space), tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
                work = Path(temporary)
                archive = work / 'rootfs.tar'
                with tarfile.open(archive, 'w') as target:
                    target.addfile(tar_member('.', tarfile.DIRTYPE))
                expected = payload.file_record(archive)
                proof = work / 'normalized-proof.json'
                proof.write_text('{}')
                binding = {'profile': userspace_profile, 'source_pins': {'fixture': expected}}
                helper = payload.REPO / 'build/userspace-profiles.py'
                lock = payload.REPO / 'configs/host-userspace-profiles.lock.json'
                selected = {'archive': expected, 'evidence': proof, 'provenance': (proof, helper, lock), 'binding': binding}
                selected['provenance_records'] = {str(path): payload.file_record(path) for path in selected['provenance']}
                if changed_provenance:
                    proof.write_text('{"changed": true}')
                diagnostic = work / 'diag'
                diagnostic.mkdir()
                for name in payload.BOOT_FILES:
                    (diagnostic / name).write_bytes(gzip.compress(b'fixture') if name == 'initramfs.cpio.gz' else b'fixture')
                output = work / 'output'
                backend = mock.Mock(LOCK=payload.REPO / 'configs/rootfs-metadata.lock.json') if metadata != 'native' else None
                # Use the actual metadata lock path, independently of userspace choice.
                if backend is not None:
                    backend.LOCK = payload.metadata_backend(metadata).LOCK
                receipt = {'helper': payload.file_record(payload.REPO / 'build/rootfs-metadata.py')}
                extract = mock.Mock(return_value=receipt)
                markers = []
                def image(root, destination, slot):
                    markers.append(json.loads((root / 'usr/lib/orangepi-hypervisor/payload.json').read_text()))
                    mask = root / 'etc/systemd/system/systemd-machine-id-commit.service'
                    self.assertTrue(mask.is_symlink())
                    self.assertEqual(os.readlink(mask), '/dev/null')
                    destination.write_bytes(b'image')
                patches = {'userspace_binding': mock.Mock(return_value=selected),
                    'validate_metadata_profile': mock.Mock(return_value=backend),
                    'validate_output': mock.Mock(return_value=output),
                    'validate_builder_packages': mock.Mock(return_value={}),
                    'validate_kernel': mock.Mock(), 'module_entries': mock.Mock(return_value={}),
                    'extract_rootfs': extract, 'import_modules': mock.Mock(return_value={}),
                    'install_management': mock.Mock(return_value={}), 'maintenance_entries': mock.Mock(return_value={}),
                    'install_maintenance': mock.Mock(return_value={}), 'make_image': mock.Mock(side_effect=image),
                    'tool_version': mock.Mock(return_value='fixture')}
                # Archive authentication is real; diagnostic blobs are independent mocked fixtures.
                authenticate = payload.authenticate
                patches['authenticate'] = mock.Mock(side_effect=lambda path, pin: authenticate(path, pin) if Path(path) == archive or Path(path).parent.name == 'provenance' else None)
                for name, value in patches.items():
                    stack.enter_context(mock.patch.object(payload, name, value))
                stack.enter_context(mock.patch.object(payload.os, 'geteuid', return_value=0))
                stack.enter_context(mock.patch.object(payload.shutil, 'which', return_value='/tool'))
                stack.enter_context(mock.patch.object(payload.shutil, 'disk_usage', return_value=mock.Mock(free=0 if no_space else 10**15)))
                stack.enter_context(mock.patch.object(payload.PACKAGE, 'snapshot', return_value={}))
                stack.enter_context(mock.patch.object(payload.PACKAGE, 'prune_caches', return_value=[]))
                if changed_provenance or no_space:
                    with self.assertRaisesRegex(ValueError, 'hash or size mismatch' if changed_provenance else 'insufficient free space'):
                        payload.build(archive, diagnostic, output, metadata, userspace_profile)
                    extract.assert_not_called()
                    patches['make_image'].assert_not_called()
                    if no_space:
                        self.assertFalse(output.exists())
                    continue
                payload.build(archive, diagnostic, output, metadata, userspace_profile)
                manifest = json.loads((output / 'manifest.json').read_text())
                if userspace_profile == payload.DEFAULT_USERSPACE_PROFILE:
                    self.assertNotIn('userspace_profile', manifest['sources'])
                else:
                    self.assertEqual(manifest['sources']['userspace_profile'], binding)
                self.assertEqual([marker['sources'] for marker in markers], [manifest['sources']] * 2)
                self.assertEqual(manifest['systemd_masks'], {
                    'etc/systemd/system/systemd-machine-id-commit.service': {
                        'type': 'symlink', 'target': '/dev/null', 'mode': 0o777}})
                self.assertEqual([marker['systemd_masks'] for marker in markers], [manifest['systemd_masks']] * 2)
                if userspace_profile == 'timesync-20261106':
                    self.assertEqual(manifest['management_runtime']['profile'], 'a733-management-v2-timesync')
                    self.assertIn('timesync_state', manifest['sources'])
                else:
                    self.assertNotIn('timesync_state', manifest['sources'])
                for source in selected['provenance']:
                    self.assertEqual((output / 'provenance' / source.name).read_bytes(), source.read_bytes())
                if backend is not None:
                    self.assertEqual(extract.call_args.args[2:], (metadata, expected))
                else:
                    self.assertEqual(len(extract.call_args.args), 2)


@unittest.skipUnless(all(shutil.which(tool) for tool in ('mke2fs', 'tune2fs', 'e2fsck', 'debugfs')), 'e2fsprogs required')
class FilesystemTests(unittest.TestCase):
    def test_machine_id_commit_mask_survives_compressed_root_image(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / 'root'
            root.mkdir()
            payload.install_machine_id_commit_mask(root)
            image = base / 'root-A.ext4'
            payload.make_image(root, image, 'A', size=32 * 1024**2)
            archive = base / 'root-A.ext4.gz'
            with image.open('rb') as source, gzip.open(archive, 'wb') as target:
                shutil.copyfileobj(source, target)
            image.unlink()
            with gzip.open(archive, 'rb') as source, image.open('xb') as target:
                shutil.copyfileobj(source, target)
            info = subprocess.check_output(['debugfs', '-R',
                'stat /etc/systemd/system/systemd-machine-id-commit.service', str(image)],
                stderr=subprocess.DEVNULL)
            self.assertIn(b'Type: symlink', info)
            self.assertRegex(info, rb'Mode:\s+0777')
            self.assertIn(b'Fast link dest: "/dev/null"', info)

    def test_ext4_readonly_without_journal_uuid_and_content(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / 'root'
            root.mkdir()
            (root / 'fixture').write_text('immutable fixture\n')
            os.setxattr(root / 'fixture', 'user.payload-test', b'kept')
            image = base / 'root-A.ext4'
            payload.make_image(root, image, 'A', size=32 * 1024**2)
            self.assertEqual(image.stat().st_size, 32 * 1024**2)
            with image.open('rb') as stream:
                stream.seek(1024)
                sb = stream.read(1024)
            self.assertFalse(struct.unpack_from('<I', sb, 92)[0] & 4)
            self.assertTrue(struct.unpack_from('<I', sb, 100)[0] & 0x1000)
            self.assertEqual(str(uuid.UUID(bytes=sb[104:120])), payload.SLOT_UUIDS['A'])
            self.assertNotEqual(payload.SLOT_UUIDS['A'], payload.SLOT_UUIDS['B'])
            content = subprocess.check_output(['debugfs', '-R', 'cat /fixture', str(image)], stderr=subprocess.DEVNULL)
            self.assertEqual(content, b'immutable fixture\n')
            attrs = subprocess.check_output(['debugfs', '-R', 'ea_list /fixture', str(image)],
                                            stderr=subprocess.DEVNULL)
            self.assertIn(b'user.payload-test', attrs)
            with self.assertRaises(FileExistsError):
                payload.make_image(root, image, 'A', size=32 * 1024**2)


if __name__ == '__main__':
    unittest.main()
