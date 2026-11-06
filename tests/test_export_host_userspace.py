"""Exporter guards and full native PAX readback, with ordinary-file fixtures only."""
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import tarfile
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('userspace_export', Path(__file__).resolve().parents[1] / 'build/export-host-userspace.py')
exporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(exporter)


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.work = Path(self.temporary.name)
        self.root = self.work / 'arm-root'
        self.root.mkdir()
        self.root.chmod(0o755)
        self.patches = [patch.object(exporter, 'WORK', self.work), patch.object(exporter, 'ROOT', self.root),
                        patch.object(exporter, 'RECEIPT', self.work / 'configure-receipt.json'),
                        patch.object(exporter, 'MIN_FREE', 0)]
        for item in self.patches:
            item.start()
        for name in ('proc', 'sys', 'dev/pts', 'dev/shm', 'etc/ssh', 'usr/sbin', 'usr/bin',
                     'etc/apt/apt.conf.d', 'etc/systemd/system-preset', 'usr/share/keyrings',
                     'var/lib/dpkg', 'var/lib/apt/lists', 'var/cache/apt/archives', 'var/log',
                     'var/lib/dbus', 'var/lib/systemd', 'var/lib/urandom', 'run', 'tmp'):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        for name in exporter.DEVICES:
            self.put(name, b'', 0o666)
        self.put('usr/share/keyrings/debian-archive-keyring.gpg', b'public fixture')
        self.put('etc/systemd/system-preset/00-prototype.preset', b'disable *\n')
        self.put('etc/apt/apt.conf.d/99-prototype', b'Acquire::Check-Valid-Until "false";\nAPT::Install-Recommends "false";\nAPT::Install-Suggests "false";\n')
        self.put('usr/sbin/policy-rc.d', b'#!/bin/sh\nexit 101\n', 0o755)
        self.put('etc/apt/sources.list', b'deb file:/a733-repo bookworm main\n')
        self.put('etc/shadow', b'root::19000:0:99999:7:::\n', 0o640)
        self.put('etc/machine-id', b'generated-machine')
        self.put('etc/ssh/ssh_host_ed25519_key', b'secret', 0o600)
        self.put('etc/ssh/ssh_host_ed25519_key.pub', b'public')
        self.put('etc/ssh/sshd_config', b'keep this configuration')
        self.put('var/lib/dbus/machine-id', b'generated')
        self.put('var/lib/systemd/random-seed', b'secret')
        self.put('var/lib/urandom/random-seed', b'secret')
        self.put('var/log/install.log', b'log')
        self.put('tmp/deep/cache', b'cache')
        self.put('usr/share/doc/fixture/copyright', b'preserve copyright')
        self.put('usr/share/data', b'payload\0' * 30)
        os.link(self.root / 'usr/share/data', self.root / 'usr/share/data-hard')
        (self.root / 'bin').symlink_to('usr/bin')
        self.lock = self.work / 'packages.lock'
        self.lock.write_bytes(b'fixture\t1.0\tarm64\n')
        self.put('var/lib/dpkg/status', b'Package: fixture\nStatus: install ok installed\nVersion: 1.0\nArchitecture: arm64\nDescription: fixture\n continuation\n\n')
        self.patches.extend([patch.object(exporter, 'LOCK', self.lock),
                             patch.object(exporter, 'LOCK_SHA256', hashlib.sha256(self.lock.read_bytes()).hexdigest()),
                             patch.object(exporter, 'LOCK_BYTES', self.lock.stat().st_size),
                             patch.object(exporter, 'PACKAGE_COUNT', 1),
                             patch.object(exporter, 'ROOT_OWNER', (os.getuid(), os.getgid()))])
        for item in self.patches[4:]:
            item.start()
        receipt = {'success': True, 'configured_packages': 1, 'inventory_match': True,
                   'dpkg_audit_clean': True, 'apt_check_passed': True}
        data = json.dumps(receipt).encode()
        exporter.RECEIPT.write_bytes(data)
        self.receipt_hash = hashlib.sha256(data).hexdigest()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temporary.cleanup()

    def put(self, name, data, mode=0o644):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(mode)
        return path

    def run_export(self, output=None, receipt_hash=None):
        with patch.object(exporter, 'environment'), patch.object(exporter, 'disk_backed'):
            return exporter.export(output or self.work / 'export-test', receipt_hash or self.receipt_hash)

    def test_export_full_readback_and_cleanup(self):
        data = self.root / 'usr/share/data'
        os.utime(data, ns=(1234567890123456789, 1234567890123456789))
        try:
            os.setxattr(data, 'user.fixture', b'\0\xffbinary')
        except OSError:
            pass
        report = self.run_export()
        output = self.work / 'export-test'
        self.assertTrue(report['success'])
        self.assertEqual(report['character_headers'], 8)
        bom = json.loads((output / 'rootfs-bom.json').read_text())
        self.assertEqual(exporter.verify_archive(output / 'rootfs.tar.gz', bom), report['raw_tar'])
        self.assertEqual(bom['usr/share/data']['mtime_ns'], 1234567890123456789)
        self.assertEqual(bom['usr/share/data-hard']['type'], 'hardlink')
        self.assertEqual((self.root / 'etc/machine-id').read_bytes(), b'')
        self.assertEqual((self.root / 'etc/apt/sources.list').read_bytes(), exporter.SOURCES)
        self.assertTrue((self.root / 'etc/shadow').read_bytes().startswith(b'root:!:'))
        self.assertTrue((self.root / 'etc/ssh/sshd_config').exists())
        self.assertTrue((self.root / 'usr/share/doc/fixture/copyright').exists())
        self.assertFalse((self.root / 'etc/ssh/ssh_host_ed25519_key').exists())
        self.assertFalse(list((self.root / 'tmp').iterdir()))
        self.assertNotIn('secret', json.dumps(report))
        with tarfile.open(output / 'rootfs.tar.gz') as archive:
            self.assertEqual(archive.getmembers()[0].name, '.')
            chars = [m for m in archive if m.ischr()]
            self.assertEqual(len(chars), 8)
            self.assertTrue(all(m.mode == 0o666 and m.uid == m.gid == m.size == 0 for m in chars))
        for name in exporter.DEVICES:
            self.assertTrue(stat.S_ISREG((self.root / name).lstat().st_mode))

    def test_root_mode_normalized(self):
        self.root.chmod(0o700)
        report = self.run_export()
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o755)
        self.assertEqual(report['cleanup']['root_mode'], {'before': 0o700, 'after': 0o755})

    def test_wrong_receipt_hash_prevents_cleanup(self):
        with self.assertRaisesRegex(ValueError, 'authentication'):
            self.run_export(receipt_hash='0' * 64)
        self.assertTrue((self.root / 'etc/ssh/ssh_host_ed25519_key').exists())
        self.assertFalse((self.work / 'export-test').exists())

    def test_failed_receipt(self):
        data = exporter.RECEIPT.read_bytes().replace(b'true', b'false', 1)
        exporter.RECEIPT.write_bytes(data)
        with self.assertRaisesRegex(ValueError, 'did not pass'):
            self.run_export(receipt_hash=hashlib.sha256(data).hexdigest())

    def test_receipt_symlink_and_hardlink(self):
        source = self.work / 'receipt-copy'
        exporter.RECEIPT.rename(source)
        exporter.RECEIPT.symlink_to(source)
        with self.assertRaisesRegex(ValueError, 'regular'):
            self.run_export()
        exporter.RECEIPT.unlink()
        os.link(source, exporter.RECEIPT)
        with self.assertRaisesRegex(ValueError, 'hardlink'):
            self.run_export()

    def test_exact_package_status(self):
        for status in (b'install ok unpacked', b'deinstall ok installed'):
            self.put('var/lib/dpkg/status', b'Package: fixture\nStatus: ' + status + b'\nVersion: 1.0\nArchitecture: arm64\n')
            with self.assertRaisesRegex(ValueError, 'fully installed'):
                self.run_export()
        self.put('var/lib/dpkg/status', b'Package: fixture\nStatus: install ok installed\nVersion: wrong\nArchitecture: arm64\n')
        with self.assertRaisesRegex(ValueError, 'inventory differs'):
            self.run_export()

    def test_lock_is_pinned(self):
        self.lock.write_bytes(b'forged')
        with self.assertRaisesRegex(ValueError, 'lock pin'):
            self.run_export()

    def test_mount_at_source_or_below_rejected(self):
        for path in (self.root, self.root / 'dev', self.root / 'var/log'):
            with patch.object(exporter, 'mounts', return_value=[(path, 'ext4')]):
                with self.assertRaisesRegex(ValueError, 'active mount'):
                    self.run_export()

    def test_output_namespace_alias_and_existing(self):
        for path in (self.root, self.root / 'export-bad', self.work / 'repo', Path('/tmp/export-bad')):
            with self.assertRaises(ValueError):
                self.run_export(output=path)
        (self.work / 'export-test').mkdir()
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.run_export()

    def test_symlink_output_ancestor(self):
        alias = self.work / 'alias'
        alias.symlink_to(self.work, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink ancestor'):
            self.run_export(output=alias / 'export-test')

    def test_devices_are_only_approved_regular_placeholders(self):
        path = self.root / 'dev/null'
        path.chmod(0o600)
        with self.assertRaisesRegex(ValueError, 'placeholder'):
            self.run_export()
        path.chmod(0o666)
        path.write_bytes(b'not empty')
        with self.assertRaisesRegex(ValueError, 'placeholder'):
            self.run_export()

    def test_fifo_and_external_hardlink_rejected(self):
        fifo = self.root / 'tmp/fifo'
        os.mkfifo(fifo)
        with self.assertRaisesRegex(ValueError, 'special source'):
            self.run_export()
        fifo.unlink()
        os.link(self.root / 'usr/share/data', self.work / 'external')
        with self.assertRaisesRegex(ValueError, 'hardlink escapes'):
            self.run_export()

    def test_generated_key_symlink_cannot_escape(self):
        key = self.root / 'etc/ssh/ssh_host_ed25519_key'
        key.unlink()
        outside = self.work / 'outside'
        outside.write_bytes(b'keep')
        key.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'generated secret'):
            self.run_export()
        self.assertEqual(outside.read_bytes(), b'keep')

    def test_policy_and_qemu_require_exact_bytes(self):
        self.put('usr/sbin/policy-rc.d', b'#!/bin/sh\nexit 0\n')
        with self.assertRaisesRegex(ValueError, 'policy-rc'):
            self.run_export()
        self.put('usr/sbin/policy-rc.d', b'#!/bin/sh\nexit 101\n')
        self.put('usr/bin/qemu-aarch64-static', b'wrong emulator')
        with self.assertRaisesRegex(ValueError, 'qemu pin'):
            self.run_export()
        self.assertTrue((self.root / 'etc/ssh/ssh_host_ed25519_key').exists())

    def test_build_only_directories_fail_closed(self):
        self.put('a733-repo/user-file', b'keep')
        with self.assertRaisesRegex(ValueError, 'mirror directory'):
            self.run_export()
        (self.root / 'a733-repo/user-file').unlink()
        self.put('debootstrap/user-data', b'keep')
        with self.assertRaisesRegex(ValueError, 'debootstrap state'):
            self.run_export()

    def test_pseudo_dirs_must_be_empty_physical_dirs(self):
        self.put('proc/unexpected', b'x')
        with self.assertRaisesRegex(ValueError, 'not empty'):
            self.run_export()

    def test_tree_change_fails_without_publication(self):
        original = exporter.write_archive
        def change(*args):
            result = original(*args)
            self.put('usr/share/data', b'changed')
            return result
        with patch.object(exporter, 'write_archive', side_effect=change):
            with self.assertRaisesRegex(ValueError, 'tree changed'):
                self.run_export()
        self.assertFalse((self.work / 'export-test').exists())
        self.assertFalse(list(self.work.glob('.export-staging-*')))

    def test_archive_corruption_and_bom_mismatch(self):
        self.run_export()
        archive = self.work / 'export-test/rootfs.tar.gz'
        bom = json.loads((archive.parent / 'rootfs-bom.json').read_text())
        bom['usr/share/data']['data']['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'BOM'):
            exporter.verify_archive(archive, bom)
        content = bytearray(archive.read_bytes())
        content[-8] ^= 255
        archive.write_bytes(content)
        with self.assertRaises((gzip.BadGzipFile, EOFError, tarfile.ReadError, ValueError)):
            exporter.verify_archive(archive, bom)

    def test_archive_unsafe_namespace_rejected(self):
        archive = self.work / 'bad.tar.gz'
        with tarfile.open(archive, 'w:gz') as output:
            root = tarfile.TarInfo('.')
            root.type = tarfile.DIRTYPE
            output.addfile(root)
            output.addfile(tarfile.TarInfo('../escape'))
        with self.assertRaisesRegex(ValueError, 'namespace'):
            exporter.verify_archive(archive, {})

    def test_namespace_mapping_and_capability_guards(self):
        original = Path.read_text
        status = ('NoNewPrivs: 1\nCapInh: 00000000f58cafff\n'
                  'CapPrm: 00000000f58cafff\nCapEff: 00000000f58cafff\n'
                  'CapBnd: 00000000f58cafff\nCapAmb: 0\n')
        mapping = ['0 1048576 65536\n']
        status_value = [status]
        def read(path, *args, **kwargs):
            if str(path) in ('/proc/self/uid_map', '/proc/self/gid_map'):
                return mapping[0]
            if str(path) == '/proc/self/status':
                return status_value[0]
            return original(path, *args, **kwargs)
        with patch.object(exporter.platform, 'freedesktop_os_release', return_value={'ID': 'ubuntu', 'VERSION_ID': '22.04'}), \
                patch.object(exporter.platform, 'machine', return_value='x86_64'), \
                patch.object(exporter.os, 'getuid', return_value=0), \
                patch.object(exporter.os, 'geteuid', return_value=0), \
                patch.object(exporter.os, 'getgid', return_value=0), \
                patch.object(exporter.os, 'getegid', return_value=0), \
                patch.object(Path, 'read_text', read), patch.dict(os.environ, {}, clear=True):
            exporter.environment()
            mapping[0] = '0 0 4294967295\n'
            with self.assertRaisesRegex(ValueError, 'exact private'):
                exporter.environment()
            mapping[0] = '0 1048576 65536\n'
            status_value[0] = status.replace('NoNewPrivs: 1', 'NoNewPrivs: 0')
            with self.assertRaisesRegex(ValueError, 'NoNewPrivs'):
                exporter.environment()
            status_value[0] = status.replace('CapEff: 00000000f58cafff', 'CapEff: ffffffffffffffff')
            with self.assertRaisesRegex(ValueError, 'capabilities'):
                exporter.environment()

    def test_failed_archive_validation_has_no_success_publication(self):
        with patch.object(exporter, 'verify_archive', side_effect=ValueError('corrupt bytes')):
            with self.assertRaisesRegex(ValueError, 'corrupt bytes'):
                self.run_export()
        self.assertFalse((self.work / 'export-test').exists())
        self.assertFalse(list(self.work.glob('.export-staging-*')))

    def test_physical_disk_and_environment_guards(self):
        with patch.object(exporter, 'mounts', return_value=[(Path('/'), 'tmpfs')]):
            with self.assertRaisesRegex(ValueError, 'physical disk'):
                exporter.disk_backed(self.work)
        with patch.object(exporter.platform, 'freedesktop_os_release', return_value={'ID': 'debian'}):
            with self.assertRaisesRegex(ValueError, 'Ubuntu'):
                exporter.environment()


if __name__ == '__main__':
    unittest.main()
