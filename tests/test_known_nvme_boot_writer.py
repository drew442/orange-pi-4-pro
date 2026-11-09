"""No hardware: exercise refusal gates and bounded writes on ordinary files."""
from contextlib import contextmanager, redirect_stdout
import io
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('nvme_boot_writer', Path(__file__).resolve().parents[1] / 'scripts/write-known-nvme-boot.py')
W = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(W)


class WriterTests(unittest.TestCase):
    def test_pins(self):
        manifest = json.loads((Path(__file__).resolve().parents[1] / 'docs/evidence/storage-write-authorization-20261108.json').read_text())
        self.assertIsInstance(manifest, dict)
        self.assertEqual(W.SOURCE_BYTES, W.GEOMETRY[0][1] * 512)
        self.assertEqual(W.GEOMETRY[0][0], 2048)
        self.assertEqual(W.TARGET, '/dev/nvme0n1p1')
        self.assertEqual(W.OLD_SHA256, '5860889755d7479e619e86b83bc32237e51bacd3a3fd2e2d2ebe976898f44899')
        self.assertEqual(W.NEW_SHA256, 'cc2ad988fdc57579178766acaf09c71d688aaadec94b722593192cb24426c313')
        for offset, length, _ in W.GPT_EXTENTS:
            self.assertTrue(offset + length <= 1048576 or offset >= 1048576 + W.SOURCE_BYTES)

    def test_bounded_copy_handles_short_writes_zeros_and_preserves_surroundings(self):
        payload = b'\x00' * 37 + bytes(range(256)) * 7
        with tempfile.TemporaryFile() as source, tempfile.TemporaryFile() as target:
            source.write(payload + b'NOT-COPIED')
            source.seek(0)
            target.write(b'PREFIX' + b'x' * len(payload) + b'SUFFIX')
            target.seek(6)
            original_write = os.write
            def short_write(fd, data):
                return original_write(fd, data[:11])
            progress = []
            with patch.object(W.os, 'write', side_effect=short_write):
                self.assertEqual(W.stream_write(source.fileno(), target.fileno(), len(payload),
                                                lambda n, total: progress.append((n, total))), len(payload))
            target.seek(0)
            self.assertEqual(target.read(), b'PREFIX' + payload + b'SUFFIX')
            self.assertEqual(source.tell(), len(payload))
            self.assertEqual(progress[-1], (len(payload), len(payload)))

    def test_zero_write_refused(self):
        with tempfile.TemporaryFile() as source, tempfile.TemporaryFile() as target:
            source.write(b'abc')
            source.seek(0)
            with patch.object(W.os, 'write', return_value=0), self.assertRaises(W.Refusal):
                W.stream_write(source.fileno(), target.fileno(), 3)

    def test_short_source_refused(self):
        with tempfile.TemporaryFile() as source, tempfile.TemporaryFile() as target:
            with self.assertRaisesRegex(W.Refusal, 'EOF'):
                W.stream_write(source.fileno(), target.fileno(), 3)

    def test_hash_extent_is_bounded(self):
        with tempfile.TemporaryFile() as source:
            source.write(b'abcTRAILING')
            source.flush()
            self.assertEqual(W.hash_extent(source.fileno(), 3), hashlib.sha256(b'abc').hexdigest())
            self.assertEqual(source.tell(), 11)

    def test_hash_extent_refuses_eof(self):
        with tempfile.TemporaryFile() as source, self.assertRaises(W.Refusal):
            W.hash_extent(source.fileno(), 1)

    def test_gpt_corruption_refused_without_write(self):
        with tempfile.TemporaryFile() as disk:
            disk.write(b'correctGPT')
            disk.flush()
            with patch.object(W, 'GPT_EXTENTS', ((0, 10, hashlib.sha256(b'correctGPT').hexdigest()),)):
                W.verify_gpt(disk.fileno())
                os.pwrite(disk.fileno(), b'X', 1)
                with self.assertRaises(W.Refusal):
                    W.verify_gpt(disk.fileno())

    def test_gpt_short_read_refused(self):
        with tempfile.TemporaryFile() as disk, self.assertRaises(W.Refusal):
            W.verify_gpt(disk.fileno())

    def test_mount_parser_rejects_malformed(self):
        for line in ('', '1 2 x / / rw - tmpfs tmpfs rw', '1 2 0:1 / / rw tmpfs tmpfs rw'):
            with self.subTest(line=line), self.assertRaises(W.Refusal):
                W.mountinfo(line)

    def test_mount_parser_decodes_paths(self):
        self.assertEqual(W.mountinfo('1 2 0:1 / /hello\\040world rw - tmpfs tmpfs rw')[0]['path'], '/hello world')

    def test_stacked_mount_refused(self):
        mounts = [{'path': '/run'}, {'path': '/run'}]
        with self.assertRaises(W.Refusal):
            W.containing_mount('/run/source', mounts)

    def test_ram_path_rejects_noncanonical_and_nonrun(self):
        for path in ('relative', '/tmp/file', '/run/a/../b'):
            with self.subTest(path=path), self.assertRaises(W.Refusal):
                W.protected_ram_path(path, [])

    def test_regular_file_target_refused(self):
        with tempfile.TemporaryFile() as target, self.assertRaises(W.Refusal):
            W.validate_open_target(target.fileno(), W.TARGET, {'nvme0n1p1': (0, 0, 0)}, W.SOURCE_BYTES)

    def test_source_metadata_and_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'raw'
            source.write_bytes(b'raw')
            source.chmod(0o600)
            with patch.object(W, 'SOURCE_BYTES', 3), patch.object(W, 'protected_ram_path'), patch.object(W.os, 'geteuid', return_value=0):
                if source.stat().st_uid != 0:
                    self.skipTest('root-owned source fixture requires root')
                fd, identity = W.open_source(str(source), [], hashlib.sha256(b'raw').hexdigest())
                try:
                    self.assertEqual(os.read(fd, 3), b'raw')
                    source.chmod(0o644)
                    with self.assertRaises(W.Refusal):
                        W.validate_source(fd, str(source), identity)
                finally:
                    os.close(fd)

    def test_wrong_source_digest_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'raw'
            source.write_bytes(b'raw')
            source.chmod(0o600)
            with patch.object(W, 'SOURCE_BYTES', 3), patch.object(W, 'protected_ram_path'), self.assertRaises(W.Refusal):
                W.open_source(str(source), [], '0' * 64)

    def test_linked_source_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'raw'
            source.write_bytes(b'raw')
            source.chmod(0o600)
            os.link(source, Path(directory) / 'hardlink')
            with patch.object(W, 'SOURCE_BYTES', 3), patch.object(W, 'protected_ram_path'), self.assertRaises(W.Refusal):
                W.open_source(str(source), [], hashlib.sha256(b'raw').hexdigest())

    def receipt(self):
        return {'schema': 1, 'operation': 'known-nvme-boot-offdevice-backup',
                'serial': W.SERIAL, 'bytes': W.SOURCE_BYTES, 'sha256': W.OLD_SHA256,
                'export_boot_id': 'caller-boot', 'authenticated_transfer_verified': True,
                'remote_host': 'trusted-control', 'remote_path': '/retained/boot.ext4'}

    def check_receipt(self, receipt, expected_sha=None):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'receipt'
            data = json.dumps(receipt).encode()
            path.write_bytes(data)
            path.chmod(0o600)
            with patch.object(W, 'protected_ram_path'):
                return W.read_receipt(str(path), [], expected_sha or hashlib.sha256(data).hexdigest(), 'caller-boot')

    @unittest.skipUnless(os.geteuid() == 0, 'root-owned fixture requires privileged suite')
    def test_receipt_validated(self):
        self.assertEqual(self.check_receipt(self.receipt())['sha256'], W.OLD_SHA256)

    def test_receipt_wrong_pin_refused(self):
        with self.assertRaises(W.Refusal):
            self.check_receipt(self.receipt(), '0' * 64)

    def test_receipt_bad_fields_refused(self):
        for key, value in [('serial', 'different'), ('bytes', 1), ('sha256', W.NEW_SHA256),
                           ('export_boot_id', 'stale'), ('authenticated_transfer_verified', False),
                           ('remote_host', ''), ('remote_path', 'relative')]:
            with self.subTest(key=key), self.assertRaises(W.Refusal):
                self.check_receipt({**self.receipt(), key: value})

    def test_opened_target_uses_only_partition_write_capability(self):
        before = {'nvme0n1': (1, 2, 3), 'nvme0n1p1': (4, 5, 6)}
        devices = {(259, 0), (259, 1)}
        with patch.object(W, 'host_context'), \
                patch.object(W, 'target_identity', return_value=(before, devices)), \
                patch.object(W, 'idle_target') as idle, \
                patch.object(W.os, 'open', side_effect=[101, 102]) as opened, \
                patch.object(W.os, 'close') as closed, \
                patch.object(W.fcntl, 'flock') as lock, \
                patch.object(W, 'validate_open_target'), patch.object(W, 'verify_gpt') as gpt:
            with W.opened_target('boot', writable=True) as descriptors:
                self.assertEqual(descriptors, (102, 101))
            self.assertEqual(opened.call_args_list[0].args[0], W.DISK)
            self.assertEqual(opened.call_args_list[0].args[1] & os.O_ACCMODE, os.O_RDONLY)
            self.assertEqual(opened.call_args_list[1].args[0], W.TARGET)
            self.assertEqual(opened.call_args_list[1].args[1] & os.O_ACCMODE, os.O_RDWR)
            self.assertTrue(opened.call_args_list[1].args[1] & os.O_EXCL)
            lock.assert_called_once_with(102, W.fcntl.LOCK_EX | W.fcntl.LOCK_NB)
            self.assertEqual(gpt.call_count, 2)
            self.assertEqual(idle.call_count, 3)
            self.assertEqual({c.args[0] for c in closed.call_args_list}, {101, 102})

    def test_changed_target_after_open_refused(self):
        with patch.object(W, 'host_context'), \
                patch.object(W, 'target_identity', side_effect=[({'original': 1}, set()), ({'changed': 1}, set())]), \
                patch.object(W, 'idle_target'), patch.object(W.os, 'open', side_effect=[101, 102]), \
                patch.object(W.os, 'close'), patch.object(W.fcntl, 'flock'), \
                patch.object(W, 'validate_open_target'), self.assertRaises(W.Refusal):
            with W.opened_target('boot', writable=True):
                self.fail('changed target yielded')

    def test_boot_pin_mismatch_refused(self):
        with patch.object(W.os, 'geteuid', return_value=0), \
                patch.object(W, 'read_text', return_value='another-boot'), self.assertRaises(W.Refusal):
            W.host_context('00000000-0000-4000-8000-000000000001')

    def test_nested_pid_private_proc_single_nspid_and_initial_uidmap_refused(self):
        boot = '00000000-0000-4000-8000-000000000001'
        text = {'/proc/sys/kernel/random/boot_id': boot,
                '/proc/self/status': 'NSpid: 1',
                '/proc/self/uid_map': '0 0 4294967295'}
        nested = SimpleNamespace(st_dev=7, st_ino=123456789)
        with patch.object(W.os, 'geteuid', return_value=0), \
                patch.object(W.os, 'uname', return_value=SimpleNamespace(release=W.KERNEL_RELEASE)), \
                patch.object(W, 'read_text', side_effect=lambda path: text[str(path)]), \
                patch.object(W.os, 'open', return_value=101), patch.object(W.os, 'close'), \
                patch.object(W.os, 'fstat', return_value=nested), \
                patch.object(W.os, 'stat', return_value=nested), \
                patch.object(W.fcntl, 'ioctl', return_value=W.NAMESPACE_TYPES['pid']), \
                self.assertRaisesRegex(W.Refusal, 'initial PID'):
            W.host_context(boot)

    def test_initial_namespace_descriptor_type_and_path_checked(self):
        initial = SimpleNamespace(st_dev=7, st_ino=W.INITIAL_NAMESPACE_INODES['pid'])
        changed = SimpleNamespace(st_dev=7, st_ino=99)
        for path_race, wrong_type in ((False, False), (True, False), (False, True)):
            with self.subTest(path_race=path_race, wrong_type=wrong_type), \
                    patch.object(W.os, 'open', return_value=101), patch.object(W.os, 'close') as closed, \
                    patch.object(W.os, 'fstat', return_value=initial), \
                    patch.object(W.os, 'stat', return_value=changed if path_race else initial), \
                    patch.object(W.fcntl, 'ioctl', return_value=0 if wrong_type else W.NAMESPACE_TYPES['pid']):
                if path_race or wrong_type:
                    with self.assertRaises(W.Refusal):
                        W.namespace_guard('pid')
                else:
                    W.namespace_guard('pid')
                closed.assert_called_once_with(101)

    def test_wrong_kernel_refused(self):
        boot = '00000000-0000-4000-8000-000000000001'
        with patch.object(W.os, 'geteuid', return_value=0), \
                patch.object(W, 'read_text', return_value=boot), \
                patch.object(W.os, 'uname', return_value=SimpleNamespace(release='other')), \
                self.assertRaisesRegex(W.Refusal, 'pinned A733'):
            W.host_context(boot)

    def test_hidden_proc_refused(self):
        boot = '00000000-0000-4000-8000-000000000001'
        text = {'/proc/sys/kernel/random/boot_id': boot,
                '/proc/self/status': 'NSpid: 1', '/proc/self/uid_map': '0 0 4294967295'}
        mounts = [{'dev': (0, 1), 'path': '/', 'fstype': 'rootfs', 'options': []},
                  {'dev': (0, 2), 'path': '/run', 'fstype': 'tmpfs', 'options': []},
                  {'dev': (0, 3), 'path': '/proc', 'fstype': 'proc', 'options': ['hidepid=2']}]
        with patch.object(W.os, 'geteuid', return_value=0), \
                patch.object(W.os, 'uname', return_value=SimpleNamespace(release=W.KERNEL_RELEASE)), \
                patch.object(W, 'namespace_guard'), patch.object(W, 'mountinfo', return_value=mounts), \
                patch.object(W, 'read_text', side_effect=lambda path: text[str(path)]), \
                self.assertRaisesRegex(W.Refusal, 'hides processes'):
            W.host_context(boot)

    @contextmanager
    def proc_fixture(self, mount_device='0:1', with_fd=False):
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)
            for tid in ('1', '2'):
                task = proc / '1' / 'task' / tid
                (task / 'fd').mkdir(parents=True)
                (task / 'mountinfo').write_text(f'1 2 {mount_device} / / rw - tmpfs tmpfs rw')
                (task / 'maps').write_text('')
                if with_fd and tid == '2':
                    (task / 'fd' / '5').write_text('descriptor')
            def redirected_path(path):
                return proc if path == '/proc' else Path(path)
            with patch.object(W, 'Path', side_effect=redirected_path):
                yield proc

    def test_consumer_census_all_tasks_without_consumers(self):
        with self.proc_fixture():
            W.process_consumers({(259, 1)})

    def test_consumer_census_other_namespace_mount_refused(self):
        with self.proc_fixture(mount_device='259:1'), self.assertRaisesRegex(W.Refusal, 'another process namespace'):
            W.process_consumers({(259, 1)})

    def test_consumer_census_unshared_thread_fd_refused(self):
        original_stat = os.stat
        def fixture_stat(path, *args, **kwargs):
            if str(path).endswith('/task/2/fd/5'):
                return SimpleNamespace(st_mode=0o060000, st_rdev=os.makedev(259, 1))
            return original_stat(path, *args, **kwargs)
        with self.proc_fixture(with_fd=True), patch.object(W.os, 'stat', side_effect=fixture_stat), \
                self.assertRaisesRegex(W.Refusal, 'process uses NVMe'):
            W.process_consumers({(259, 1)})

    def test_consumer_census_permission_failure_is_fatal(self):
        with self.proc_fixture(), patch.object(W, 'read_text', side_effect=PermissionError('hidden')), \
                self.assertRaises(PermissionError):
            W.process_consumers({(259, 1)})

    def test_partial_write_failure_report_never_claims_zero(self):
        with tempfile.TemporaryFile() as report_file, tempfile.TemporaryFile() as target:
            def partial(*args):
                os.write(target.fileno(), b'partial')
                raise OSError('simulated write failure')
            output = io.StringIO()
            with patch.object(W, 'host_context', return_value=[]), \
                    patch.object(W, 'fresh_output', return_value=os.dup(report_file.fileno())), \
                    patch.object(W, 'read_receipt', return_value={'remote_host': 'trusted'}), \
                    patch.object(W, 'apply_payload', side_effect=partial), redirect_stdout(output):
                result = W.main(['apply', '/run/private/raw', '--expected-boot-id', 'boot',
                                 '--backup-receipt', '/run/private/receipt', '--backup-receipt-sha256', 'a' * 64,
                                 '--report', '/run/private/report'])
            self.assertEqual(result, 1)
            report = json.loads(output.getvalue())
            self.assertIsNone(report['bytes_written'])
            self.assertEqual(report['status'], 'failed-or-refused')
            self.assertEqual(os.pread(target.fileno(), 7, 0), b'partial')
            report_file.seek(0)
            self.assertEqual(json.loads(report_file.read())['bytes_written'], None)

    def exercise_apply(self, rollback=False, predecessor=True, readback=False):
        source_sha = W.OLD_SHA256 if rollback else W.NEW_SHA256
        current_sha = W.NEW_SHA256 if rollback else W.OLD_SHA256
        with tempfile.TemporaryFile() as source, tempfile.TemporaryFile() as target:
            source.write(b'abc')
            source.seek(0)
            target.write(b'old')
            target.seek(0)
            @contextmanager
            def opened(*args, **kwargs):
                self.assertTrue(kwargs['writable'])
                yield target.fileno(), 9999
            hashes = [current_sha if predecessor else 'bad'] + ([source_sha] if readback else [])
            with patch.object(W, 'open_source', return_value=(os.dup(source.fileno()), 'identity')) as authenticated, \
                    patch.object(W, 'opened_target', opened), patch.object(W, 'hash_extent', side_effect=hashes) as hashed, \
                    patch.object(W, 'validate_source'), patch.object(W, 'host_context'), \
                    patch.object(W, 'target_identity', return_value=({}, set())), \
                    patch.object(W, 'validate_open_target'), patch.object(W, 'idle_target'), \
                    patch.object(W, 'verify_gpt'), patch.object(W, 'SOURCE_BYTES', 3), \
                    patch.object(W.fcntl, 'ioctl') as ioctl, patch.object(W, 'progress'):
                if not predecessor:
                    with self.assertRaises(W.Refusal):
                        W.apply_payload('raw', 'boot', [], rollback, readback)
                    target.seek(0)
                    self.assertEqual(target.read(), b'old')
                    ioctl.assert_not_called()
                    return
                result = W.apply_payload('raw', 'boot', [], rollback, readback)
                authenticated.assert_called_once_with('raw', [], source_sha)
                self.assertEqual(hashed.call_count, 2 if readback else 1)
                ioctl.assert_called_once_with(target.fileno(), W.BLKFLSBUF)
                target.seek(0)
                self.assertEqual(target.read(), b'abc')
                self.assertEqual(result['bytes_written'], 3)
                self.assertEqual(result['source_sha256'], source_sha)
                self.assertEqual(result['status'], 'rollback-complete' if rollback else 'write-complete')

    def test_forward_write(self):
        self.exercise_apply()

    def test_rollback_reverse_pins(self):
        self.exercise_apply(rollback=True)

    def test_wrong_predecessor_never_written(self):
        self.exercise_apply(predecessor=False)

    def test_rollback_wrong_predecessor_never_written(self):
        self.exercise_apply(rollback=True, predecessor=False)

    def test_explicit_readback(self):
        self.exercise_apply(readback=True)

    @unittest.skipUnless(os.geteuid() == 0, 'root-owned fixture requires privileged suite')
    def test_export_only_reads_target(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryFile() as target:
            path = str(Path(directory) / 'backup')
            target.write(b'old')
            target.seek(0)
            @contextmanager
            def opened(*args, **kwargs):
                self.assertFalse(kwargs.get('writable', False))
                yield target.fileno(), 9999
            with patch.object(W, 'protected_ram_path'), patch.object(W, 'opened_target', opened), \
                    patch.object(W, 'SOURCE_BYTES', 3), patch.object(W, 'OLD_SHA256', hashlib.sha256(b'old').hexdigest()), \
                    patch.object(W, 'progress'):
                report = W.export_backup(path, 'boot', [])
                self.assertEqual(report['status'], 'export-complete')
                self.assertEqual(Path(path).read_bytes(), b'old')
                self.assertEqual(Path(path).stat().st_mode & 0o777, 0o600)
                self.assertEqual(os.pread(target.fileno(), 3, 0), b'old')


if __name__ == '__main__':
    unittest.main()
