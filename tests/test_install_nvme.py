"""Writer policy and fault tests; never open or write a physical block device."""
import copy
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location('install_nvme_tests', Path(__file__).resolve().parents[1] / 'scripts/install-nvme.py')
INSTALL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSTALL)


def record(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def manifest():
    size = 250_000_000_000
    return {'schema': 1, 'kernel_release': '6.6.98-a733-diag',
            'logical_sector_bytes': 512, 'physical_sector_bytes': 4096,
            'layout': INSTALL.LAYOUT.layout(size, 512, 4096),
            'files': {'nvme.img': {'bytes': size, 'sha256': 'a'*64}},
            'deployment_intent': {'for_installation': True,
                'administration_public_key_fingerprint': 'SHA256:' + 'A'*43,
                'management_mac': 'a0:11:22:33:44:55', 'management_cidr': '10.1.2.0/24'}}


class TTY(io.StringIO):
    def isatty(self):
        return True


class EngineTests(unittest.TestCase):
    def run_engine(self, data, **kwargs):
        with tempfile.TemporaryFile() as target:
            target.write(b'x' * len(data))
            target.flush()
            result = INSTALL.stream_copy_verify(io.BytesIO(data), target.fileno(), record(data),
                flush_callback=kwargs.pop('flush_callback', lambda fd: None),
                refresh_callback=kwargs.pop('refresh_callback', lambda fd: None), **kwargs)
            target.seek(0)
            return result, target.read()

    def test_all_bytes_including_zeros_and_short_writes(self):
        data = b'prefix' + bytes(8192) + b'end'
        actual = os.pwrite
        with patch.object(INSTALL.os, 'pwrite', side_effect=lambda fd, value, offset: actual(fd, value[:7], offset)):
            result, written = self.run_engine(data, chunk_bytes=511)
        self.assertTrue(result['success'])
        self.assertEqual(written, data)
        self.assertEqual(result['bytes_verified'], len(data))
        self.assertEqual(result['bytes_written'], len(data))

    def test_first_write_failure_is_conservatively_modified(self):
        for error in (OSError('write failed'), KeyboardInterrupt()):
            with self.subTest(error=error), patch.object(INSTALL.os, 'pwrite', side_effect=error):
                result, _ = self.run_engine(b'payload')
            self.assertFalse(result['success'])
            self.assertTrue(result['target_modified'])
            self.assertEqual(result['bytes_attempted'], 7)
            self.assertEqual(result['bytes_written'], 0)

    def test_partial_failure_tracks_written_bytes(self):
        actual = os.pwrite
        calls = 0
        def fail(fd, data, offset):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('partial')
            return actual(fd, data[:3], offset)
        with patch.object(INSTALL.os, 'pwrite', side_effect=fail):
            result, _ = self.run_engine(b'payload')
        self.assertEqual(result['bytes_written'], 3)
        self.assertFalse(result['success'])
        self.assertTrue(result['target_modified'])

    def test_bad_readback_and_flush_and_refresh_are_failure(self):
        for failure in ('readback', 'flush', 'refresh'):
            kwargs = {}
            if failure != 'readback':
                kwargs[f'{failure}_callback'] = Mock(side_effect=OSError(failure))
            with self.subTest(failure=failure), patch.object(INSTALL.os, 'pread', wraps=os.pread) as read:
                if failure == 'readback':
                    read.side_effect = lambda fd, size, offset: b'!'*size
                result, _ = self.run_engine(b'payload', **kwargs)
            self.assertFalse(result['success'])
            self.assertTrue(result['target_modified'])

    def test_hash_mismatch_and_size_bounds(self):
        for data in (b'payloa', b'payload!', b'changed'):
            with self.subTest(data=data), tempfile.TemporaryFile() as target:
                refresh = Mock()
                result = INSTALL.stream_copy_verify(io.BytesIO(data), target.fileno(), record(b'payload'),
                    flush_callback=lambda fd: None, refresh_callback=refresh)
                self.assertFalse(result['success'])
                refresh.assert_not_called()

    def test_prewrite_check_failure_is_unmodified(self):
        result, data = self.run_engine(b'payload', source_check=Mock(side_effect=ValueError('changed')))
        self.assertFalse(result['target_modified'])
        self.assertEqual(data, b'x'*7)

    def test_postwrite_source_change_prevents_refresh(self):
        check = Mock(side_effect=[None, ValueError('source changed')])
        refresh = Mock()
        result, _ = self.run_engine(b'payload', source_check=check, refresh_callback=refresh)
        self.assertFalse(result['success'])
        self.assertTrue(result['target_modified'])
        refresh.assert_not_called()


class PolicyTests(unittest.TestCase):
    def test_isolated_cli_and_no_approval_bypass(self):
        script = str(Path(INSTALL.__file__).resolve())
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(['python3', '-I', '-B', script, '--help'], cwd=directory, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--manifest-sha256', result.stdout)
        for flag in ('--yes', '--force', '--loop', '--test'):
            result = subprocess.run(['python3', '-I', '-B', script, 'apply', flag], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)

    def test_fixture_and_incompatible_manifest_refused(self):
        for field, value in (('for_installation', False), ('for_installation', 1),
                             ('management_mac', '02:73:30:00:00:01'),
                             ('management_cidr', '192.0.2.0/24'),
                             ('administration_public_key_fingerprint', 'SHA256:zJDXpqimTuhgMi8BhG83V7SeYsDurn/2Dmt9g/R3Fzc')):
            changed = manifest()
            changed['deployment_intent'][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                INSTALL.validate_manifest(changed)
        for field, value in (('schema', True), ('kernel_release', 'other'), ('physical_sector_bytes', 513), ('layout', {}), ('deployment_intent', None)):
            changed = manifest()
            changed[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                INSTALL.validate_manifest(changed)
        self.assertEqual(INSTALL.validate_manifest(manifest()), manifest()['files']['nvme.img'])

    def test_tty_and_two_exact_phrases_required(self):
        plan = {'target': {'serial': 'SERIAL', 'size_bytes': 123}, 'plan_sha256': 'a'*64}
        exact = 'BACKUP VERIFIED\nERASE SERIAL 123 ' + 'a'*64 + '\n'
        INSTALL.authorize(plan, TTY(exact), TTY())
        for stream in (io.StringIO(exact), TTY('yes\n'), TTY(exact.replace('SERIAL', 'OTHER')), TTY(exact.replace('BACKUP VERIFIED', 'BACKUP VERIFIED '))):
            with self.subTest(stream=stream), self.assertRaises(ValueError):
                INSTALL.authorize(plan, stream, TTY())
        with self.assertRaises(ValueError):
            INSTALL.authorize(plan, TTY(exact), io.StringIO())

    def test_source_authentication_raw_gzip_and_mutation(self):
        data = b'payload' + bytes(65536)
        for compressed in (False, True):
            with self.subTest(compressed=compressed), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                image = root / ('nvme.img.gz' if compressed else 'nvme.img')
                encoded = gzip.compress(data) if compressed else data
                image.write_bytes(encoded)
                value = manifest()
                value['files']['nvme.img'] = record(data)
                value['files'][image.name] = record(encoded)
                path = root / 'manifest.json'
                path.write_text(json.dumps(value))
                trusted = hashlib.sha256(path.read_bytes()).hexdigest()
                # Keep realistic production geometry validation separate from tiny stream fixtures.
                with patch.object(INSTALL, 'validate_manifest', return_value=record(data)):
                    source = INSTALL.Source(image, path, trusted)
                    try:
                        image.write_bytes(encoded + b'!')
                        with self.assertRaisesRegex(ValueError, 'changed'):
                            source.check()
                    finally:
                        source.close()
                    with self.assertRaises(ValueError):
                        INSTALL.Source(image, path, trusted)
                    with self.assertRaisesRegex(ValueError, 'manifest SHA'):
                        INSTALL.Source(image, path, '0'*64)

    def test_decoded_source_hash_size_and_gzip_truncation_fail_prewrite(self):
        for data, expected in ((b'payload!', record(b'payload')), (b'changed', record(b'payload')), (b'payloa', record(b'payload'))):
            with self.subTest(data=data), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                encoded = gzip.compress(data)
                image = root/'nvme.img.gz'
                image.write_bytes(encoded)
                value = manifest()
                value['files']['nvme.img.gz'] = record(encoded)
                value['files']['nvme.img'] = expected
                path = root/'manifest.json'
                path.write_text(json.dumps(value))
                with patch.object(INSTALL, 'validate_manifest', return_value=expected), self.assertRaises(ValueError):
                    INSTALL.Source(image, path, hashlib.sha256(path.read_bytes()).hexdigest())

    def test_plan_digest_stable_and_covers_target_metadata(self):
        target = {'canonical_path':'/dev/nvme0n1','size_bytes':250_000_000_000}
        source = SimpleNamespace(review={'manifest_record':{'sha256':'a'*64}}, manifest=manifest())
        with patch.object(INSTALL, 'inspect_target', return_value=target), \
                patch.object(INSTALL.os, 'open', return_value=99), patch.object(INSTALL.os, 'close'), \
                patch.object(INSTALL, 'validate_fd'), \
                patch.object(INSTALL, 'metadata_hashes', return_value={'first_mib':'old', 'last_mib':'old'}) as metadata:
            first = INSTALL.make_plan(Mock(), source)
            self.assertEqual(first, INSTALL.make_plan(Mock(), source))
            metadata.return_value = {'first_mib':'changed', 'last_mib':'old'}
            changed = INSTALL.make_plan(Mock(), source)
        self.assertNotEqual(first['plan_sha256'], changed['plan_sha256'])
        unsigned = dict(first)
        unsigned.pop('plan_sha256')
        self.assertEqual(first['plan_sha256'], INSTALL.digest(INSTALL.canonical_json(unsigned)))

    def test_symlink_source_and_duplicate_manifest_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'file').write_bytes(b'content')
            (root/'alias').symlink_to(root/'file')
            with self.assertRaises(OSError):
                INSTALL.open_regular(root/'alias')
        with self.assertRaises(ValueError):
            INSTALL.read_json(io.BytesIO(b'{"schema":1,"schema":1}'))

    def test_source_backing_overlap_unknown_and_ram(self):
        with tempfile.TemporaryFile() as file:
            info = os.fstat(file.fileno())
            identity = f'{os.major(info.st_dev)}:{os.minor(info.st_dev)}'
            for filesystem, dependencies, allowed in (
                    ('tmpfs', set(), True), ('ramfs', set(), True),
                    ('ext4', {'8:0'}, True), ('ext4', {'8:0', '259:0'}, False),
                    ('overlay', set(), False), ('btrfs', {'8:0'}, False)):
                line = f'1 0 {identity} / /source rw - {filesystem} source rw\n'
                with self.subTest(filesystem=filesystem, dependencies=dependencies):
                    if allowed:
                        INSTALL.source_off_target(file.fileno(), '259:0', line, lambda dev: dependencies)
                    else:
                        with self.assertRaises(ValueError):
                            INSTALL.source_off_target(file.fileno(), '259:0', line, lambda dev: dependencies)
            with self.assertRaises(ValueError):
                INSTALL.source_off_target(file.fileno(), '259:0', '')

    def test_partition_and_recursive_mapper_dependencies(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sysroot = root/'dev'
            sysroot.mkdir()
            devices = root/'devices'
            devices.mkdir()
            def node(name, identity, parent=devices):
                directory = parent/name
                directory.mkdir()
                (directory/'dev').write_text(identity)
                (directory/'slaves').mkdir()
                (sysroot/identity).symlink_to(directory)
                return directory
            disk = node('nvme0n1', '259:0')
            part = node('nvme0n1p1', '259:1', disk)
            (part/'partition').write_text('1')
            dm = node('dm-0', '253:0')
            (dm/'slaves'/'nvme0n1p1').symlink_to(part)
            self.assertEqual(INSTALL.block_dependencies('253:0', sysroot), {'253:0','259:1','259:0'})
            loop = node('loop0', '7:0')
            with self.assertRaises(ValueError):
                INSTALL.block_dependencies('7:0', sysroot)

    def test_fd_geometry_identity_and_regular_target_refused(self):
        target = {'major_minor': '259:0', 'size_bytes': 100, 'logical_sector_bytes': 512, 'physical_sector_bytes': 4096}
        fake = SimpleNamespace(st_mode=stat.S_IFBLK, st_rdev=os.makedev(259, 0))
        with patch.object(INSTALL.os, 'fstat', return_value=fake), patch.object(INSTALL, 'ioctl_number', side_effect=[100,512,4096,0]):
            INSTALL.validate_fd(99, target)
        for values in ([101,512,4096,0], [100,4096,4096,0], [100,512,512,0], [100,512,4096,1]):
            with patch.object(INSTALL.os, 'fstat', return_value=fake), patch.object(INSTALL, 'ioctl_number', side_effect=values), self.assertRaises(ValueError):
                INSTALL.validate_fd(99, target)
        with tempfile.TemporaryFile() as file, self.assertRaises(ValueError):
            INSTALL.validate_fd(file.fileno(), target)

    def test_inspect_target_checks_model_geometry_and_source(self):
        value = manifest()
        target = {'model': 'SSD', 'major_minor': '259:0', 'size_bytes': 250_000_000_000,
                  'logical_sector_bytes': 512, 'physical_sector_bytes': 4096}
        source = SimpleNamespace(manifest=value, decoded_expected=value['files']['nvme.img'], image=Mock(), manifest_file=Mock(), check=Mock())
        args = SimpleNamespace(target='/dev/nvme0n1', expected_serial='SERIAL', expected_bytes=250_000_000_000, expected_model='SSD')
        with patch.object(INSTALL, 'inventory', return_value={}), patch.object(INSTALL.PLANNER, 'plan', return_value={'target':target, 'layout':value['layout']}), patch.object(INSTALL, 'source_off_target'):
            self.assertEqual(INSTALL.inspect_target(args, source), target)
            for field, wrong in (('model','OTHER'), ('size_bytes',123), ('logical_sector_bytes',4096), ('physical_sector_bytes',512)):
                with patch.dict(target, {field:wrong}), self.subTest(field=field), self.assertRaises(ValueError):
                    INSTALL.inspect_target(args, source)

    def test_busy_and_changed_identity_are_rejected_by_used_planner(self):
        disk = {'path':'/dev/nvme0n1', 'kname':'nvme0n1', 'type':'disk', 'tran':'nvme',
                'model':'SSD', 'serial':'SERIAL', 'size':250_000_000_000,
                'log-sec':512, 'phy-sec':4096, 'ro':False, 'mountpoints':[None], 'maj:min':'259:0'}
        idle = {'holders':[], 'read_only':False, 'mounted':False, 'swap':False}
        resolver = lambda path: ('/dev/nvme0n1','259:0')
        for field, value in (('holders',['dm-0']), ('mounted',True), ('swap',True), ('read_only',True)):
            evidence = dict(idle, **{field:value})
            with self.subTest(field=field), self.assertRaises(ValueError):
                INSTALL.PLANNER.plan({'blockdevices':[disk]}, disk['path'], resolver=resolver,
                    safety_reader=lambda nodes: {'259:0':evidence}, expected_serial='SERIAL', expected_bytes=250_000_000_000)
        with self.assertRaises(ValueError):
            INSTALL.PLANNER.plan({'blockdevices':[disk]}, disk['path'], resolver=resolver,
                safety_reader=lambda nodes: {'259:0':idle}, expected_serial='CHANGED')

    def test_nonroot_apply_refused_before_authorization_or_target_open(self):
        with patch.object(INSTALL.os, 'geteuid', return_value=1000), \
                patch.object(INSTALL, 'authorize') as authorization, \
                patch.object(INSTALL.os, 'open') as opened:
            with self.assertRaisesRegex(ValueError, 'apply requires root'):
                INSTALL.apply(Mock(), Mock(), Mock())
            authorization.assert_not_called()
            opened.assert_not_called()

    @patch.object(INSTALL.os, 'geteuid', return_value=0)
    def test_outer_main_interruption_preserves_shared_write_report(self, uid):
        plan = {'target':{'major_minor':'259:0'}}
        def escaped(args, source, plan, report):
            report.update(target_modified=True, bytes_attempted=4096, bytes_written=1024,
                          bytes_verified=512, success=True)
            raise KeyboardInterrupt('at apply return boundary')
        output = io.StringIO()
        saved_plan = io.BytesIO(json.dumps(plan).encode())
        saved_plan.fileno = lambda: 99
        argv = ['apply', '--image','nvme.img', '--manifest','manifest.json',
                '--manifest-sha256','a'*64, '--target','/dev/nvme0n1',
                '--expected-model','SSD', '--expected-serial','SERIAL',
                '--expected-bytes','250000000000', '--plan','plan.json']
        with patch.object(INSTALL, 'Source'), patch.object(INSTALL, 'make_plan', return_value=plan), \
                patch.object(INSTALL, 'open_regular', return_value=saved_plan), \
                patch.object(INSTALL, 'source_off_target'), patch.object(INSTALL, 'apply', side_effect=escaped), \
                patch.object(INSTALL.sys, 'stdout', output), patch.object(INSTALL.sys, 'stderr', io.StringIO()), \
                patch.object(INSTALL.signal, 'signal') as handler:
            self.assertEqual(INSTALL.main(argv), 1)
        result = json.loads(output.getvalue())
        self.assertFalse(result['success'])
        self.assertTrue(result['target_modified'])
        self.assertEqual(result['bytes_attempted'], 4096)
        self.assertEqual(result['bytes_written'], 1024)
        self.assertEqual(result['bytes_verified'], 512)
        self.assertIs(handler.call_args_list[0].args[1], INSTALL.interrupted)
        with self.assertRaises(InterruptedError):
            INSTALL.interrupted(INSTALL.signal.SIGTERM, None)

    def test_console_progress_start_byte_time_and_completion_throttling(self):
        output = io.StringIO()
        now = [0]
        progress = INSTALL.ConsoleProgress(output=output, clock=lambda:now[0])
        progress.start()
        total = 3*1024**3
        for done in range(4*1024**2, 1024**3, 4*1024**2):
            progress(done, total)
        self.assertEqual(len(output.getvalue().splitlines()), 1)
        progress(1024**3, total)
        self.assertEqual(len(output.getvalue().splitlines()), 2)
        now[0] = 10
        progress(1024**3+4*1024**2, total)
        self.assertEqual(len(output.getvalue().splitlines()), 3)
        progress(total, total)
        self.assertEqual(len(output.getvalue().splitlines()), 4)

    @patch.object(INSTALL.os, 'geteuid', return_value=0)
    def test_apply_full_path_and_failure_reports_hold_exclusive_fd(self, uid):
        payload = b'payload' + bytes(4096)
        for fault in (None, 'refresh', 'close', 'interrupt', 'source'):
            with self.subTest(fault=fault), tempfile.TemporaryFile() as file:
                file.write(b'x'*len(payload))
                file.flush()
                source = SimpleNamespace(image=io.BytesIO(payload), encoded_expected=record(payload),
                    decoded_expected=record(payload), check=Mock(), decoded=lambda:io.BytesIO(payload))
                target = {'canonical_path':'/dev/nvme0n1','size_bytes':len(payload)}
                plan = {'target':target, 'old_metadata':{'first_mib':'old', 'last_mib':'old'}}
                events = []
                def authorize(value):
                    events.append('authorize')
                def open_device(path, flags):
                    events.append('exclusive-open' if flags & os.O_EXCL else 'probe-open')
                    return file.fileno()
                def close(fd):
                    events.append('close')
                    if fault == 'close' and events.count('close') == 2:
                        raise OSError('close failed')
                def ioctl(fd, command):
                    events.append('flush' if command == INSTALL.BLKFLSBUF else 'refresh')
                    if fault == 'refresh' and command == INSTALL.BLKRRPART:
                        raise OSError('refresh failed')
                if fault == 'source':
                    source.check.side_effect = [None, None, None, ValueError('source changed')]
                write = os.pwrite
                with patch.object(INSTALL, 'authorize', side_effect=authorize), \
                        patch.object(INSTALL, 'inspect_target', return_value=target), \
                        patch.object(INSTALL.os, 'open', side_effect=open_device) as opened, \
                        patch.object(INSTALL.os, 'close', side_effect=close), \
                        patch.object(INSTALL.os, 'pwrite', side_effect=KeyboardInterrupt() if fault == 'interrupt' else write), \
                        patch.object(INSTALL, 'validate_fd', side_effect=lambda fd, value:events.append('validate')), \
                        patch.object(INSTALL, 'metadata_hashes', side_effect=lambda fd,size:(events.append('metadata') or plan['old_metadata'])), \
                        patch.object(INSTALL.fcntl, 'flock', side_effect=lambda fd,flags:events.append('lock')), \
                        patch.object(INSTALL.fcntl, 'ioctl', side_effect=ioctl), \
                        patch.object(INSTALL.sys, 'stderr', io.StringIO()):
                    result = INSTALL.apply(Mock(), source, plan)
                self.assertEqual(result['success'], fault is None)
                self.assertTrue(result['target_modified'])
                self.assertEqual(events[:5], ['authorize','probe-open','validate','metadata','close'])
                self.assertLess(events.index('exclusive-open'), events.index('lock'))
                self.assertEqual(opened.call_args_list[-1].args[1], os.O_RDWR|os.O_EXCL|os.O_NONBLOCK|os.O_NOFOLLOW)
                self.assertEqual(events[-1], 'close')
                if fault is None:
                    self.assertLess(events.index('flush'), events.index('refresh'))
                    self.assertEqual(result['bytes_verified'], len(payload))
                if fault == 'interrupt':
                    self.assertEqual(result['bytes_written'], 0)

    @patch.object(INSTALL.os, 'geteuid', return_value=0)
    def test_apply_lock_metadata_and_postlock_revalidation_prevent_writes(self, uid):
        target = {'canonical_path':'/dev/nvme0n1', 'size_bytes':250_000_000_000}
        plan = {'target':target, 'old_metadata':{'first_mib':'old', 'last_mib':'old'}}
        for fault in ('metadata', 'lock', 'busy'):
            with self.subTest(fault=fault), patch.object(INSTALL, 'authorize'), \
                    patch.object(INSTALL, 'inspect_target', side_effect=[target, ValueError('busy')] if fault == 'busy' else None, return_value=target), \
                    patch.object(INSTALL.os, 'open', return_value=99) as opened, \
                    patch.object(INSTALL.os, 'close'), patch.object(INSTALL, 'validate_fd'), \
                    patch.object(INSTALL, 'metadata_hashes', return_value={} if fault == 'metadata' else plan['old_metadata']), \
                    patch.object(INSTALL.fcntl, 'flock', side_effect=BlockingIOError('locked') if fault == 'lock' else None), \
                    patch.object(INSTALL, 'stream_copy_verify') as engine:
                if fault == 'metadata':
                    with self.assertRaisesRegex(ValueError, 'metadata changed'):
                        INSTALL.apply(Mock(), Mock(), plan)
                    self.assertEqual(opened.call_count, 1)
                else:
                    result = INSTALL.apply(Mock(), Mock(), plan)
                    self.assertFalse(result['success'])
                    self.assertFalse(result['target_modified'])
                engine.assert_not_called()
                for call in opened.call_args_list:
                    self.assertTrue(call.args[1] & os.O_NOFOLLOW)
                if opened.call_count == 2:
                    self.assertTrue(opened.call_args_list[-1].args[1] & os.O_EXCL)

    @patch.object(INSTALL.os, 'geteuid', return_value=0)
    def test_apply_revalidates_identity_before_open(self, uid):
        target = {'serial':'SERIAL'}
        with patch.object(INSTALL, 'authorize'), patch.object(INSTALL, 'inspect_target', return_value={'serial':'CHANGED'}), patch.object(INSTALL.os, 'open') as opened:
            with self.assertRaises(ValueError):
                INSTALL.apply(Mock(), Mock(), {'target':target})
            opened.assert_not_called()



class SharedReportRegression(unittest.TestCase):
    def test_input_failure_cannot_retain_prior_success(self):
        report = {'success': True, 'target_modified': True, 'bytes_attempted': 4,
                  'bytes_written': 4, 'bytes_verified': 0}
        with patch.object(INSTALL.os, 'pwrite') as write:
            result = INSTALL.stream_copy_verify(io.BytesIO(b''), 99, {'bytes': 0, 'sha256': '0'*64},
                       flush_callback=lambda fd:None, refresh_callback=lambda fd:None, report=report)
        self.assertFalse(result['success'])
        self.assertTrue(result['target_modified'])
        self.assertEqual(result['bytes_written'], 4)
        write.assert_not_called()

if __name__ == '__main__':
    unittest.main()
