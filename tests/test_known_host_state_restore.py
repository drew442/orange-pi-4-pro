"""Host-only fixtures: mocked physical boundaries never establish hardware acceptance."""
import argparse
import contextlib
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import struct
import tempfile
import types
import unittest
from unittest import mock
import uuid

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('paired_restore', ROOT / 'scripts/restore-known-host-state.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        # Fixture imports source directly; production still authenticates its exact pin.
        spec = importlib.util.spec_from_file_location('restore_fixture_policy',
            ROOT / 'scripts/write-known-nvme-boot.py')
        self.boot = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.boot)
        # Model only root ownership; modes, links, sizes and inode changes stay real.
        def root_owned(probe):
            def inspect(*args, **kwargs):
                info = probe(*args, **kwargs)
                values = {name: getattr(info, name) for name in dir(info) if name.startswith('st_')}
                values.update(st_uid=0, st_gid=0)
                return types.SimpleNamespace(**values)
            return inspect
        for name in ('fstat', 'lstat'):
            patch = mock.patch.object(M.os, name, side_effect=root_owned(getattr(os, name)))
            patch.start()
            self.addCleanup(patch.stop)
        self.boot.protected_ram_path = lambda *args: None
        self.boot.fresh_output = lambda path, mounts: os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        self.sizes = {2: 4096, 3: 4096, 4: 8192}
        self.patch = mock.patch.object(M, 'SIZES', self.sizes)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.index = dict(schema=1, operation='known-host-paired-backup', serial=self.boot.SERIAL,
                          model=self.boot.MODEL, namespace_bytes=self.boot.TARGET_BYTES,
                          state_uuid=M.STATE_UUID, export_boot_id=str(uuid.uuid4()),
                          boot_sha256=self.boot.NEW_SHA256, exported_cohort=True,
                          offdevice_verified=True, partitions=[])
        self.raw = {}
        for n, size in self.sizes.items():
            data = bytearray(size)
            struct.pack_into('<I', data, 1024 + 4, size // 1024)
            struct.pack_into('<H', data, 1024 + 0x38, 0xef53)
            data[1024 + 0x68:1024 + 0x78] = uuid.UUID(M.UUIDS[n]).bytes
            if n == 4:
                struct.pack_into('<I', data, 1024 + 0x5c, 4)
                struct.pack_into('<I', data, 1024 + 0xe0, 8)
            data = bytes(data)
            self.raw[n] = data
            archive = gzip.compress(data, mtime=0)
            self.private(f'p{n}.img.gz', archive)
            self.index['partitions'].append(dict(number=n, archive=f'p{n}.img.gz',
                encoded_bytes=len(archive), encoded_sha256=hashlib.sha256(archive).hexdigest(),
                raw_bytes=size, raw_sha256=hashlib.sha256(data).hexdigest()))
        self.private('index.json', M.canonical(self.index))
        self.args = argparse.Namespace(operation='prepare', backup_index=str(self.base / 'index.json'),
            index_sha256=hashlib.sha256(M.canonical(self.index)).hexdigest(),
            expected_boot_id=str(uuid.uuid4()), plan=str(self.base / 'plan.json'),
            report=str(self.base / 'report.json'), plan_sha256=None, readback=False)
        self.parts = {}
        for n, size in self.sizes.items():
            path = self.private(f'target{n}', b'X' * size)
            self.parts[n] = os.open(path, os.O_RDWR)
            self.addCleanup(os.close, self.parts[n])
        self.disk = os.open(self.private('disk', b'disk'), os.O_RDONLY)
        self.addCleanup(os.close, self.disk)
        self.report_fd = os.open(self.args.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        self.addCleanup(os.close, self.report_fd)
        self.report = {'status': 'failed-or-refused', 'partitions': {str(n): {
            'accepted_bytes': 0, 'stream_complete': False, 'flush_complete': False,
            'readback_complete': False} for n in self.sizes}}

    def private(self, name, data):
        path = self.base / name
        path.write_bytes(data)
        path.chmod(0o600)
        return path

    @contextlib.contextmanager
    def claims(self, *_args):
        yield self.disk, self.parts, lambda: None

    @contextlib.contextmanager
    def admission_context(self):
        # Core format/write tests deliberately substitute exclusion and p5 proof.
        # This fake grants no production authority; native composition is tested separately.
        guard = mock.MagicMock()
        with mock.patch.object(self.boot.OfflineAdmission, 'acquire', return_value=guard), \
             mock.patch.object(self.boot, 'refuse_managed_p5', return_value={
                 'status': 'fixture-only-no-authority', 'hardware_acceptance': False}):
            yield

    def run_restore(self):
        with self.admission_context(), mock.patch.object(M, 'targets', self.claims), \
             mock.patch.object(M, 'recovery_lock', contextlib.nullcontext), \
             mock.patch.object(M, 'hash_region', return_value=self.boot.NEW_SHA256), \
             mock.patch.object(M.fcntl, 'ioctl', return_value=0):
            M.run(self.args, self.boot, self.report, self.report_fd, [])

    def prepare_apply(self):
        self.run_restore()
        self.args.operation = 'apply'
        self.args.plan_sha256 = hashlib.sha256(Path(self.args.plan).read_bytes()).hexdigest()

    def assert_untouched(self):
        for n, fd in self.parts.items():
            self.assertEqual(os.pread(fd, self.sizes[n], 0), b'X' * self.sizes[n])

    def test_prepare_only_and_restore_damaged_state(self):
        # All current partitions, including STATE, contain invalid filesystem bytes.
        self.prepare_apply()
        self.assert_untouched()
        self.run_restore()
        self.assertEqual(self.report['status'], 'operations-complete-awaiting-terminal-receipt')
        for n, fd in self.parts.items():
            self.assertEqual(os.pread(fd, self.sizes[n], 0), self.raw[n])
            state = self.report['partitions'][str(n)]
            self.assertTrue(state['stream_complete'] and state['flush_complete'])
            self.assertFalse(state['readback_complete'])

    def test_index_and_plan_pin_refusals_never_write(self):
        with self.assertRaises(M.Refusal):
            self.args.index_sha256 = '0' * 64
            self.run_restore()
        self.assert_untouched()
        self.args.index_sha256 = hashlib.sha256(M.canonical(self.index)).hexdigest()
        self.prepare_apply()
        self.args.plan_sha256 = '0' * 64
        with self.assertRaises(M.Refusal):
            self.run_restore()
        self.assert_untouched()

    def test_index_strict_cohort_and_fixed_paths(self):
        for key, value in [('serial', 'other'), ('offdevice_verified', False),
                           ('state_uuid', str(uuid.uuid4())), ('exported_cohort', False)]:
            with self.subTest(key=key):
                bad = dict(self.index, **{key: value})
                with self.assertRaises(M.Refusal):
                    M.validate_index(bad, self.boot)
        for entry_key, value in [('archive', '../p2.img.gz'), ('number', 4),
                                 ('raw_bytes', 1), ('encoded_sha256', 'bad')]:
            bad = json.loads(json.dumps(self.index))
            bad['partitions'][0][entry_key] = value
            with self.assertRaises(M.Refusal):
                M.validate_index(bad, self.boot)
        with self.assertRaises(M.Refusal):
            M.parse(b'{"schema":1,"schema":1}')

    def test_changed_or_hardlinked_archive_never_writes(self):
        self.prepare_apply()
        path = self.base / 'p3.img.gz'
        data = bytearray(path.read_bytes())
        data[-1] ^= 1
        path.write_bytes(data)
        with self.assertRaises(M.Refusal):
            self.run_restore()
        self.assert_untouched()
        os.link(self.base / 'p2.img.gz', self.base / 'alias')
        with self.assertRaises(M.Refusal):
            self.run_restore()
        self.assert_untouched()

    def test_source_identity_detects_replacement(self):
        path = str(self.base / 'p2.img.gz')
        with self.assertRaises(M.Refusal):
            with M.source(self.boot, [], path) as (_fd, _identity, _size):
                os.rename(path, path + '.old')
                self.private('p2.img.gz', Path(path + '.old').read_bytes())

    def stream_fixture(self, encoded, raw_bytes=None, raw_sha=None):
        entry = dict(self.index['partitions'][0])
        entry.update(encoded_bytes=len(encoded))
        if raw_bytes is not None:
            entry['raw_bytes'] = raw_bytes
        if raw_sha is not None:
            entry['raw_sha256'] = raw_sha
        fd = os.open(self.private('stream.gz', encoded), os.O_RDONLY)
        try:
            return M.restore_stream(fd, self.parts[2], entry, self.report['partitions']['2'])
        finally:
            os.close(fd)

    def test_stream_corruption_truncation_trailing_and_overexpansion(self):
        good = (self.base / 'p2.img.gz').read_bytes()
        corrupt = bytearray(good)
        corrupt[-8] ^= 1
        for name, encoded in [('crc', bytes(corrupt)), ('truncated', good[:-1]),
                              ('trailing', good + b'x'), ('second-member', good + good),
                              ('expansion', gzip.compress(self.raw[2] + b'x'))]:
            self.report['partitions']['2'].update(accepted_bytes=0, stream_complete=False)
            with self.subTest(name=name), self.assertRaises((M.Refusal, M.zlib.error)):
                self.stream_fixture(encoded)
            self.assertFalse(self.report['partitions']['2']['stream_complete'])
            self.assertLessEqual(os.lseek(self.parts[2], 0, os.SEEK_CUR), self.sizes[2])

    def test_short_writes_are_counted(self):
        real_write = os.write
        with mock.patch.object(M.os, 'write', side_effect=lambda fd, data: real_write(fd, data[:7])):
            self.stream_fixture((self.base / 'p2.img.gz').read_bytes())
        self.assertEqual(self.report['partitions']['2']['accepted_bytes'], self.sizes[2])
        self.assertEqual(os.pread(self.parts[2], self.sizes[2], 0), self.raw[2])

    def test_failure_and_interrupt_at_each_partition_no_success(self):
        self.prepare_apply()
        for failed_n in self.sizes:
            self.report['status'] = 'failed-or-refused'
            actual = M.restore_stream
            def stream(fd, target, entry, state, check=None):
                if entry['number'] == failed_n:
                    raise InterruptedError('injected signal')
                return actual(fd, target, entry, state, check=check)
            with mock.patch.object(M, 'restore_stream', side_effect=stream), self.assertRaises(InterruptedError):
                self.run_restore()
            self.assertNotEqual(self.report['status'], 'restore-complete')

    def test_flush_and_readback_failures_do_not_succeed(self):
        self.prepare_apply()
        actual_fsync = os.fsync
        def flush(fd):
            if fd == self.parts[2]:
                raise OSError('injected flush error')
            return actual_fsync(fd)
        with mock.patch.object(M.os, 'fsync', side_effect=flush), self.assertRaises(OSError):
            self.run_restore()
        self.assertFalse(self.report['partitions']['2']['flush_complete'])
        self.assertNotEqual(self.report['status'], 'restore-complete')
        # readback is part of the reviewed plan, so make a new prepared plan.
        Path(self.args.plan).unlink()
        self.args.operation, self.args.plan_sha256, self.args.readback = 'prepare', None, True
        self.prepare_apply()
        actual_hash = M.hash_fd
        def bad_hash(fd, size, label):
            return '0' * 64 if 'readback' in label else actual_hash(fd, size, label)
        with mock.patch.object(M, 'hash_fd', side_effect=bad_hash), self.assertRaises(M.Refusal):
            self.run_restore()
        self.assertFalse(self.report['partitions']['2']['readback_complete'])
        self.assertNotEqual(self.report['status'], 'restore-complete')

    def test_durable_intent_failure_and_zero_write_stop(self):
        self.prepare_apply()
        with mock.patch.object(M, 'store_report', side_effect=OSError('intent fsync failed')), \
             self.assertRaises(OSError):
            self.run_restore()
        self.assert_untouched()
        with mock.patch.object(M.os, 'write', return_value=0), self.assertRaises(M.Refusal):
            self.stream_fixture((self.base / 'p2.img.gz').read_bytes())
        self.assertEqual(self.report['partitions']['2']['accepted_bytes'], 0)
        self.assertFalse(self.report['partitions']['2']['stream_complete'])

    def test_claim_close_failure_prevents_success(self):
        self.prepare_apply()
        @contextlib.contextmanager
        def failed_close(*_args):
            yield self.disk, self.parts, lambda: None
            raise OSError('claim close failed')
        with mock.patch.object(self, 'claims', failed_close), self.assertRaises(OSError):
            self.run_restore()
        self.assertNotEqual(self.report['status'], 'restore-complete')

    def call_main(self):
        report_path = self.base / 'main-report.json'
        argv = [self.args.operation]
        for field in ('backup_index', 'index_sha256', 'expected_boot_id', 'plan'):
            argv += ['--' + field.replace('_', '-'), getattr(self.args, field)]
        argv += ['--report', str(report_path)]
        if self.args.plan_sha256:
            argv += ['--plan-sha256', self.args.plan_sha256]
        if self.args.readback:
            argv.append('--readback')
        output = io.StringIO()
        with self.admission_context(), mock.patch.object(M, 'policy', return_value=self.boot), \
             mock.patch.object(M, 'context', return_value=[]), \
             mock.patch.object(M, 'targets', self.claims), \
             mock.patch.object(M, 'recovery_lock', contextlib.nullcontext), \
             mock.patch.object(M, 'hash_region', return_value=self.boot.NEW_SHA256), \
             mock.patch.object(M.fcntl, 'ioctl', return_value=0), \
             contextlib.redirect_stdout(output):
            result = M.main(argv)
        terminal = json.loads(output.getvalue())
        return result, terminal, json.loads(report_path.read_bytes()), report_path

    def test_main_first_write_error_records_uncertainty_and_binds_receipt(self):
        self.prepare_apply()
        actual_write = os.write
        def write(fd, data):
            if fd == self.parts[2]:
                raise OSError('write failed before returning a count')
            return actual_write(fd, data)
        with mock.patch.object(M.os, 'write', side_effect=write):
            result, terminal, stored, _ = self.call_main()
        self.assertEqual(result, 1)
        for report in (terminal, stored):
            self.assertTrue(report['attempted'] and report['may_have_modified'])
            self.assertTrue(report['partitions']['2']['attempted'])
            self.assertTrue(report['partitions']['2']['may_have_modified'])
            self.assertEqual(report['partitions']['2']['accepted_bytes'], 0)
            self.assertFalse(report['partitions']['3']['attempted'])
            self.assertEqual(report['expected_boot_id'], self.args.expected_boot_id)
            self.assertEqual(report['observed_boot_id'], self.args.expected_boot_id)
            self.assertEqual(report['backup_index_sha256'], self.args.index_sha256)
            self.assertEqual(report['plan_sha256'], self.args.plan_sha256)
            self.assertEqual(report['fixed_target']['serial'], self.boot.SERIAL)
            self.assertEqual(report['fixed_target']['disk'], '/dev/nvme0n1')
            self.assertEqual([p['path'] for p in report['fixed_target']['partitions']],
                             ['/dev/nvme0n1p2', '/dev/nvme0n1p3', '/dev/nvme0n1p4'])
            self.assertEqual(report['expected_archives'], self.index['partitions'])
            self.assertFalse(report['readback_requested'])
            self.assertNotEqual(report['status'], 'restore-complete')

    def test_main_terminal_success_requires_matching_conservative_file(self):
        self.prepare_apply()
        result, terminal, stored, path = self.call_main()
        self.assertEqual(result, 0)
        self.assertEqual(terminal['status'], 'restore-complete')
        self.assertEqual(stored['status'], 'operations-complete-awaiting-terminal-receipt')
        self.assertEqual(terminal['progress_report_sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertTrue(stored['partitions']['4']['flush_complete'])

    def test_main_final_report_fsync_failure_cannot_store_success(self):
        self.prepare_apply()
        actual_store, actual_fsync = M.store_report, os.fsync
        def store(fd, report):
            if report['status'] == 'operations-complete-awaiting-terminal-receipt':
                def fsync(current):
                    if current == fd:
                        raise OSError('terminal report fsync failed')
                    return actual_fsync(current)
                with mock.patch.object(M.os, 'fsync', side_effect=fsync):
                    return actual_store(fd, report)
            return actual_store(fd, report)
        with mock.patch.object(M, 'store_report', side_effect=store):
            result, terminal, stored, _ = self.call_main()
        self.assertEqual(result, 1)
        self.assertEqual(terminal['status'], 'failed-or-refused')
        self.assertNotEqual(stored['status'], 'restore-complete')
        self.assertTrue(terminal['may_have_modified'])

    def test_main_report_close_failure_cannot_store_success(self):
        self.prepare_apply()
        actual_close = os.close
        def close(fd):
            path = os.readlink(f'/proc/self/fd/{fd}')
            actual_close(fd)
            if path == str(self.base / 'main-report.json'):
                raise OSError('report close failed')
        with mock.patch.object(M.os, 'close', side_effect=close):
            result, terminal, stored, _ = self.call_main()
        self.assertEqual(result, 1)
        self.assertEqual(terminal['status'], 'failed-or-refused')
        self.assertNotEqual(stored['status'], 'restore-complete')
        self.assertTrue(terminal['may_have_modified'])

    def test_main_source_close_failure_prevents_terminal_success(self):
        self.prepare_apply()
        actual_close = os.close
        def close(fd):
            path = os.readlink(f'/proc/self/fd/{fd}')
            actual_close(fd)
            if path == str(self.base / 'p4.img.gz'):
                raise OSError('source close failed')
        with mock.patch.object(M.os, 'close', side_effect=close):
            result, terminal, stored, _ = self.call_main()
        self.assertEqual(result, 1)
        self.assertEqual(terminal['status'], 'failed-or-refused')
        self.assertNotEqual(stored['status'], 'restore-complete')
        self.assertTrue(terminal['may_have_modified'])

    def test_policy_read_atime_change_is_admitted(self):
        directory = '/dev/shm' if os.access('/dev/shm', os.W_OK) else None
        with tempfile.TemporaryDirectory(dir=directory) as temp:
            target = Path(temp) / 'write-known-nvme-boot.py'
            helper_data = (ROOT / 'scripts/write-known-nvme-boot.py').read_bytes()
            target.write_bytes(helper_data)
            os.utime(target, (1, 1))
            with mock.patch.object(M, '__file__', str(Path(temp) / 'restore-known-host-state.py')), \
                 mock.patch.object(M, 'HELPER_BYTES', target.stat().st_size), \
                 mock.patch.object(M, 'HELPER_SHA256', hashlib.sha256(helper_data).hexdigest()):
                loaded = M.policy()
            self.assertEqual(loaded.SERIAL, self.boot.SERIAL)
            self.assertGreater(os.stat(target).st_atime, 1)

    def test_third_claim_failure_closes_claims_without_writes(self):
        # Exercise real claim orchestration while replacing only physical probes.
        disk = self.base / 'namespace'
        disk.write_bytes(b'disk')
        for n in self.sizes:
            (self.base / f'namespacep{n}').write_bytes(b'X' * self.sizes[n])
        boot = types.SimpleNamespace(DISK=str(disk), TARGET_BYTES=4,
            target_identity=lambda: ({'namespace': (0, 0, 0)}, set()), idle_target=lambda *a: None,
            validate_open_target=lambda *a: None, verify_gpt=lambda *a: None,
            read_text=lambda *a: '0 0')
        real_open = os.open
        opened = []
        def open_claim(path, flags, *a, **kw):
            if str(path).endswith('p4'):
                raise OSError('third exclusive claim refused')
            fd = real_open(path, flags, *a, **kw)
            opened.append(fd)
            return fd
        with mock.patch.object(M, 'context', return_value=[]), \
             mock.patch.object(M.os, 'open', side_effect=open_claim), \
             mock.patch.object(M.os, 'write') as writes:
            with self.assertRaises(OSError):
                with M.targets(boot, self.args.expected_boot_id, True):
                    self.fail('must not yield partial claims')
            writes.assert_not_called()
        for fd in opened:
            with self.assertRaises(OSError):
                os.fstat(fd)

    def test_context_rejects_arch_swap_and_memory(self):
        fake = types.SimpleNamespace(host_context=lambda _: [], read_text=lambda p:
            'Filename Type Size Used Priority\n' if p == '/proc/swaps' else 'MemAvailable: 4194304 kB')
        with mock.patch.object(M.os, 'uname', return_value=types.SimpleNamespace(machine='x86_64')):
            with self.assertRaises(M.Refusal):
                M.context(fake, self.args.expected_boot_id)
        with mock.patch.object(M.os, 'uname', return_value=types.SimpleNamespace(machine='aarch64')):
            self.assertEqual(M.context(fake, self.args.expected_boot_id), [])
            fake.read_text = lambda p: 'Filename Type Size Used Priority\n/dev/sda partition 1 0 1' if p == '/proc/swaps' else 'MemAvailable: 1 kB'
            with self.assertRaises(M.Refusal):
                M.context(fake, self.args.expected_boot_id)
            fake.read_text = lambda p: 'Filename Type Size Used Priority' if p == '/proc/swaps' else 'MemAvailable: 1 kB'
            with self.assertRaises(M.Refusal):
                M.context(fake, self.args.expected_boot_id)

    def test_policy_pin_and_signal_are_fail_closed(self):
        with mock.patch.object(M, 'HELPER_SHA256', '0' * 64), self.assertRaises(M.Refusal):
            M.policy()
        with self.assertRaises(InterruptedError):
            M.interrupted(signal.SIGTERM, None)


if __name__ == '__main__':
    unittest.main()
