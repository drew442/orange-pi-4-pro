"""Regular-file tests only; no mount, chroot or hardware access."""
import importlib.util
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


@unittest.skipUnless(all(shutil.which(tool) for tool in ('mke2fs', 'tune2fs', 'e2fsck', 'debugfs')), 'e2fsprogs required')
class FilesystemTests(unittest.TestCase):
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
