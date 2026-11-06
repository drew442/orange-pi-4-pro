"""Small authenticated-input fixtures; gpgv is stubbed, dpkg-deb is native."""
import hashlib
import importlib.util
import json
import lzma
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    'offline_userspace_repo', Path(__file__).resolve().parents[1] / 'build/offline-userspace-repo.py')
repo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(repo)


@unittest.skipUnless(shutil.which('dpkg-deb'), 'native dpkg-deb required')
class OfflineRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.cache = self.base / 'cache'
        (self.cache / 'debs').mkdir(parents=True)
        self.output = self.base / 'mirror'
        self.index = self.base / 'Packages.xz'
        self.versions = self.base / 'versions.tsv'
        self.hashes = self.base / 'hashes.tsv'
        package = self.base / 'package'
        (package / 'DEBIAN').mkdir(parents=True)
        (package / 'DEBIAN/control').write_text(
            'Package: sample\nVersion: 1.0-1\nArchitecture: arm64\nMaintainer: Fixture <fixture@example.invalid>\nDescription: fixture\n')
        # Building and inspecting an archive runs no maintainer scripts or ARM code.
        self.deb = self.cache / 'debs/sample_1.0-1_arm64.deb'
        subprocess.run(['dpkg-deb', '--build', str(package), str(self.deb)],
                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.package_size, self.package_sha = repo.digest(self.deb)
        self.versions.write_text('sample\t1.0-1\tarm64\n')
        self.hashes.write_text(f'sample\t1.0-1\t{self.package_sha}  debs/sample_1.0-1_arm64.deb\n')
        (self.cache / 'InRelease').write_bytes(b'pinned signed release fixture')
        (self.cache / 'debian-archive-keyring.gpg').write_bytes(b'pinned keyring fixture')
        # A symlink to an inaccessible/nonregular rootfs proves it is not consumed.
        (self.cache / 'rootfs.tar.gz').symlink_to('/dev/zero')
        self.index_text = (
            'Package: sample\nVersion: 1.0-1\nArchitecture: arm64\n'
            'Filename: pool/main/s/sample/sample_1.0-1_arm64.deb\n'
            f'Size: {self.package_size}\nSHA256: {self.package_sha}\n\n')
        self.pin_index(self.index_text)
        self.patches = [patch.object(repo, 'PINS', self.pins),
                        patch.object(repo, 'PACKAGE_COUNT', 1),
                        patch.object(repo, 'VERSION_LOCK', self.versions),
                        patch.object(repo, 'HASH_LOCK', self.hashes)]
        for mock in self.patches:
            mock.start()
            self.addCleanup(mock.stop)
        self.native_run = subprocess.run
        self.run_patch = patch.object(repo.subprocess, 'run', side_effect=self.command)
        self.run_mock = self.run_patch.start()
        self.addCleanup(self.run_patch.stop)
        self.signature_status = 0
        self.mutate = None

    def pin_index(self, text):
        raw = text.encode()
        self.index.write_bytes(lzma.compress(raw))
        self.pins = {
            'InRelease': repo.digest(self.cache / 'InRelease'),
            'debian-archive-keyring.gpg': repo.digest(self.cache / 'debian-archive-keyring.gpg'),
            'Packages.xz': repo.digest(self.index),
            'Packages': (len(raw), hashlib.sha256(raw).hexdigest()),
            'version-lock': repo.digest(self.versions),
            'hash-lock': repo.digest(self.hashes),
        }
        self.release = 'Codename: bookworm\nSHA256:\n' + ''.join(
            f' {self.pins[k][1]} {self.pins[k][0]} main/binary-arm64/{k}\n'
            for k in ('Packages', 'Packages.xz'))

    def command(self, args, **kwargs):
        if args[0] != 'gpgv':
            return self.native_run(args, **kwargs)
        self.assertEqual(args[args.index('--keyring') + 1], str(self.output / 'verified-keyring.gpg'))
        self.assertEqual(args[-1], str(self.output / 'dists/bookworm/InRelease'))
        self.assertNotIn('--ignore-time-conflict', args)
        if self.signature_status == 0:
            Path(args[args.index('--output') + 1]).write_text(self.release)
        if self.mutate:
            self.mutate()
        return subprocess.CompletedProcess(args, self.signature_status, b'verification stdout', b'signature receipt')

    def construct(self):
        return repo.construct(self.cache, self.index, self.output)

    def repin_index(self, text):
        # Keep the dict patched into the module live across fixture rewrites.
        previous = self.pins
        self.pin_index(text)
        previous.update(self.pins)
        self.pins = previous

    def assert_failed(self, message):
        with self.assertRaisesRegex((ValueError, OSError), message):
            self.construct()
        self.assertFalse((self.output / 'manifest.json').exists())
        if self.output.exists():
            self.assertEqual(json.loads((self.output / 'failure.json').read_text())['status'], 'failed')

    def test_complete_mirror_retains_signed_index_and_native_controls(self):
        result = self.construct()
        self.assertEqual(result['status'], 'verified')
        self.assertEqual(result['package_count'], 1)
        self.assertEqual((self.output / 'dists/bookworm/Packages').exists(), False)
        self.assertEqual((self.output / 'dists/bookworm/main/binary-arm64/Packages').read_text(), self.index_text)
        self.assertEqual((self.output / 'verification/gpgv.stderr').read_bytes(), b'signature receipt')
        self.assertFalse((self.output / 'Release.gpg').exists())
        self.assertEqual(len(list((self.output / 'pool').rglob('*.deb'))), 1)
        for item in result['files']:
            path = self.output / item['path']
            self.assertEqual(repo.digest(path), (item['bytes'], item['sha256']))
            self.assertEqual(path.stat().st_mode & 0o777, 0o444)
        for path in self.output.rglob('*'):
            if path.is_dir():
                self.assertEqual(path.stat().st_mode & 0o777, 0o755)
        self.assertTrue((self.cache / 'rootfs.tar.gz').is_symlink())
        self.assertEqual(repo.digest(self.deb), (self.package_size, self.package_sha))

    def test_signature_failure_retains_receipt_without_success(self):
        self.signature_status = 1
        self.assert_failed('gpgv failed')
        self.assertTrue((self.output / 'verification/gpgv.stderr').exists())

    def test_compressed_pin_rejects_mutation(self):
        self.index.write_bytes(b'changed')
        self.assert_failed('size mismatch')
        self.assertFalse(self.run_mock.called)

    def test_signed_release_must_advertise_both_exact_index_pins(self):
        self.release = self.release.replace('main/binary-arm64/Packages.xz', 'wrong/path')
        self.assert_failed('does not advertise')

    def test_expanded_index_pin_is_independently_checked(self):
        self.pins['Packages'] = (self.pins['Packages'][0], '0' * 64)
        self.release = self.release.replace(hashlib.sha256(self.index_text.encode()).hexdigest(), '0' * 64)
        self.assert_failed('expanded index hash/size mismatch')

    def test_duplicate_tuple_is_rejected(self):
        self.repin_index(self.index_text + self.index_text)
        self.assert_failed('duplicate/conflicting')

    def test_wrong_version_is_not_accepted_by_name(self):
        self.repin_index(self.index_text.replace('Version: 1.0-1', 'Version: 2.0-1'))
        self.assert_failed('missing a locked tuple')

    def test_unsafe_pool_paths_are_rejected(self):
        for filename in ('/pool/x.deb', 'pool/../x.deb', 'pool/x\\y.deb',
                         'other/x.deb', 'pool//x.deb', 'pool/./x.deb', 'pool/x\x7f.deb'):
            with self.subTest(filename=filename):
                with self.assertRaises(ValueError):
                    repo.safe_filename(filename)

    def test_signed_index_hash_must_match_committed_lock(self):
        self.repin_index(self.index_text.replace(self.package_sha, '0' * 64))
        self.assert_failed('index hash/size mismatch')

    def test_changed_cached_package_is_rejected(self):
        content = self.deb.read_bytes()
        self.deb.write_bytes(content[:-1] + bytes([content[-1] ^ 1]))
        self.assert_failed('hash/size mismatch')

    def test_native_deb_control_must_match_tuple(self):
        self.versions.write_text('wrong\t1.0-1\tarm64\n')
        self.hashes.write_text(f'wrong\t1.0-1\t{self.package_sha}  debs/sample_1.0-1_arm64.deb\n')
        self.repin_index(self.index_text.replace('Package: sample', 'Package: wrong'))
        self.assert_failed('control tuple')
        self.assertFalse((self.output / 'pool').exists())

    def test_original_cache_mutation_after_copy_is_detected(self):
        def mutate():
            (self.cache / 'InRelease').write_bytes(b'x' * self.pins['InRelease'][0])
        self.mutate = mutate
        self.assert_failed('original input changed')

    def test_input_leaf_symlink_rejected(self):
        alias = self.base / 'package-copy.deb'
        self.deb.rename(alias)
        self.deb.symlink_to(alias)
        self.assert_failed('symlink path')

    def test_existing_output_and_overlap_are_rejected(self):
        self.output.mkdir()
        with self.assertRaisesRegex(ValueError, 'new'):
            self.construct()
        with self.assertRaisesRegex(ValueError, 'overlaps'):
            repo.construct(self.cache, self.index, self.cache / 'mirror')

    def test_symlink_ancestor_and_pseudo_filesystems_rejected(self):
        alias = self.base / 'alias'
        alias.symlink_to(self.base, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            repo.construct(self.cache, self.index, alias / 'mirror')
        with self.assertRaisesRegex(ValueError, 'pseudo'):
            repo.physical(Path('/dev/shm/mirror'))

    def test_bind_alias_overlap_is_rejected(self):
        native_location = repo.filesystem_location
        def alias_location(path):
            if path == self.output:
                device, location, filesystem = native_location(self.cache)
                return device, location / 'alias-output', filesystem
            return native_location(path)
        with patch.object(repo, 'filesystem_location', side_effect=alias_location):
            self.assert_failed('overlaps or aliases')
        self.assertFalse(self.output.exists())

    def test_unsafe_filename_in_signed_stanza_is_rejected(self):
        self.repin_index(self.index_text.replace(
            'pool/main/s/sample/sample_1.0-1_arm64.deb', 'pool/../escape.deb'))
        self.assert_failed('unsafe pool filename')
        self.assertFalse((self.output / 'pool').exists())

    def test_space_guard_precedes_any_output(self):
        with patch.object(repo.shutil, 'disk_usage', return_value=shutil._ntuple_diskusage(100, 99, 1)):
            self.assert_failed('512 MiB')
        self.assertFalse(self.output.exists())

    def test_staging_bound_stops_unexpected_data(self):
        with patch.object(repo, 'LIMIT', 1):
            self.assert_failed('staging bound')


if __name__ == '__main__':
    unittest.main()
