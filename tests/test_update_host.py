"""Private regular-file transaction fixtures; never NVMe/hardware acceptance."""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('stage_host_test', Path(__file__).resolve().parents[1] / 'scripts/update-host.py')
UPDATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(UPDATE)
SIZE = 8192


def record(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


@contextlib.contextmanager
def fixture_parent(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        yield fd
    finally:
        os.close(fd)


class TTY(io.StringIO):
    def isatty(self):
        return True


class StagingTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.directory = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.candidate = self.directory / 'candidate'
        self.candidate.mkdir(mode=0o700)
        self.content = b'new-root' + bytes(SIZE - 8)
        payload = UPDATE.PREP.INIT.PAYLOAD
        self.manifest = {'schema': 1, 'board': 'Orange Pi 4 Pro A733',
                         'slot_bytes': SIZE, 'slot_uuids': payload.SLOT_UUIDS.copy(),
                         'kernel_release': payload.KERNEL_RELEASE,
                         'files': {'root-B.ext4': record(self.content)},
                         'sources': {},
                         'management_runtime': {'profile': 'a733-management-v1',
                             'files': {v[0]: {'bytes': 1, 'sha256': 'a' * 64}
                                       for v in payload.MANAGEMENT_FILES.values()}},
                         'package_state': {'profile': payload.PACKAGE.PROFILE,
                                           'manifest': {'bytes': 1, 'sha256': 'b' * 64}}}
        self.selector = UPDATE.PREP.SELECT.serialize(
            {'SCHEMA': '1', 'SLOT': 'B', 'ROOT_UUID': payload.SLOT_UUIDS['B'],
             'ROOT_MARKER_SHA256': 'c' * 64, 'KERNEL_RELEASE': payload.KERNEL_RELEASE})
        self.write('manifest.json', json.dumps(self.manifest).encode())
        self.pin = record((self.candidate / 'manifest.json').read_bytes())['sha256']
        self.write('root.ext4', self.content)
        self.write('selected.conf', self.selector)
        # Model root-owned fixture files for unprivileged host tests. No CLI
        # option can inject ownership, target validation or source storage policy.
        self.real_fstat = os.fstat
        def root_stat(fd):
            value = self.real_fstat(fd)
            return SimpleNamespace(**{name: getattr(value, name) for name in dir(value)
                                     if name.startswith('st_') and name not in ('st_uid', 'st_gid')},
                                   st_uid=0, st_gid=0)
        self.stack.enter_context(mock.patch.object(UPDATE.os, 'fstat', side_effect=root_stat))
        self.source = None

    def write(self, name, data):
        path = self.candidate / name
        if path.exists():
            path.chmod(0o600)
        path.write_bytes(data)
        path.chmod(0o400)

    def source_open(self, **options):
        defaults = {'parent_opener': fixture_parent, 'guard': lambda fd: None,
                    'slot_bytes': SIZE,
                    'inspect': lambda root, slot, manifest: (self.selector, {})}
        defaults.update(options)
        source = UPDATE.Source(self.candidate, self.pin, 'B', **defaults)
        self.stack.callback(source.close)
        return source

    def transaction(self):
        self.source = self.source_open()
        sequence = getattr(self, 'sequence', 0) + 1
        self.sequence = sequence
        self.target_path = self.directory / f'inactive-{sequence}'
        self.target_path.write_bytes(b'x' * SIZE)
        self.boot = self.directory / f'boot-{sequence}'
        self.boot.mkdir(mode=0o700)
        (self.boot / 'selected.conf').write_bytes(b'active selector sentinel')
        with fixture_parent(self.boot) as fd:
            device = self.real_fstat(fd)
        self.context = {'disk': {'serial': 'SERIAL'},
                        'target': {'canonical_path': str(self.target_path),
                                   'serial': 'SERIAL', 'size_bytes': SIZE,
                                   'major_minor': '259:3'},
                        'active': {'slot': 'A', 'root_major_minor': '259:2'},
                        'state': {'boot_directory': str(self.boot),
                                  'major_minor': f'{os.major(device.st_dev)}:{os.minor(device.st_dev)}',
                                  'selector_sha256': 'a' * 64, 'selector': {'SLOT': 'A'}},
                        'gpt': {'sha256': 'b' * 64}}
        self.args = SimpleNamespace(target='/dev/nvme0n1', expected_model='MODEL',
                                    expected_serial='SERIAL', expected_bytes=250000000000,
                                    slot='B')
        self.snap = self.stack.enter_context(mock.patch.object(UPDATE, 'snapshot', side_effect=lambda args: copy.deepcopy(self.context)))
        self.stack.enter_context(mock.patch.object(UPDATE.os, 'geteuid', return_value=0))
        self.stack.enter_context(mock.patch.object(UPDATE.PREP, 'safe_parent', fixture_parent))
        self.off_target = self.stack.enter_context(mock.patch.object(UPDATE.WRITER, 'source_off_target'))
        def validate(fd, target):
            self.assertEqual(os.fstat(fd).st_ino, self.target_path.stat().st_ino)
            self.assertEqual(target['major_minor'], '259:3')
        self.stack.enter_context(mock.patch.object(UPDATE.WRITER, 'validate_fd', side_effect=validate))
        self.stack.enter_context(mock.patch.object(UPDATE.WRITER, 'metadata_hashes',
            side_effect=lambda fd, size: {'head': record(os.pread(fd, 64, 0))['sha256'],
                                         'tail': record(os.pread(fd, 64, size-64))['sha256']}))
        self.ioctl = self.stack.enter_context(mock.patch.object(UPDATE.fcntl, 'ioctl'))
        self.stack.enter_context(mock.patch.object(UPDATE.sys, 'stderr', io.StringIO()))
        self.auth = self.stack.enter_context(mock.patch.object(UPDATE, 'authorize'))
        self.plan = UPDATE.make_plan(self.args, self.source)
        return self.plan

    def run_apply(self):
        return UPDATE.apply(self.args, self.source, self.plan)

    def assert_untouched(self, report):
        self.assertFalse(report['success'])
        self.assertFalse(report['activation_ready'])
        self.assertFalse(report['selector_writes_performed'])
        self.assertFalse(report['target_modified'])
        self.assertEqual(report['bytes_written'], 0)
        self.assertEqual(self.target_path.read_bytes(), b'x' * SIZE)

    def test_source_authenticates_then_derives_selector_ignoring_receipt(self):
        self.write('candidate.json', b'forged and invalid receipt ignored')
        source = self.source_open()
        self.assertEqual(source.review['root'], record(self.content))
        self.assertEqual(source.review['manifest_sha256'], self.pin)
        self.assertEqual(source.review['selector'], record(self.selector))
        self.assertEqual(set(source.review['files']), set(UPDATE.Source.NAMES))

    def test_manifest_pin_checked_before_json_parser(self):
        self.pin = '0' * 64
        with mock.patch.object(UPDATE.json, 'loads', side_effect=AssertionError('parser reached')):
            with self.assertRaisesRegex(ValueError, 'SHA-256'):
                self.source_open()

    def test_duplicate_manifest_and_size_bounds(self):
        for data in (b'{"schema":1,"schema":1}', b'{"x":{"y":1,"y":2}}', b'', b'x' * (UPDATE.PREP.LIMIT + 1)):
            self.write('manifest.json', data)
            self.pin = record(data)['sha256']
            with self.assertRaises(ValueError):
                self.source_open()

    def test_full_root_hash_precedes_metadata_inspection(self):
        self.write('root.ext4', self.content[:-1] + b'1')
        inspector = mock.Mock(side_effect=AssertionError('metadata reached'))
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            self.source_open(inspect=inspector)
        inspector.assert_not_called()

    def test_forged_selector_rejected(self):
        self.write('selected.conf', self.selector.replace(b'SLOT=B', b'SLOT=A'))
        with self.assertRaisesRegex(ValueError, 'selector'):
            self.source_open()

    def test_mutation_during_root_inspection_rejected(self):
        def inspect(root, slot, manifest):
            self.write('selected.conf', b'forged')
            return self.selector, {}
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.source_open(inspect=inspect)

    def test_held_source_mutation_replacement_and_hardlink_rejected(self):
        source = self.source_open()
        self.write('root.ext4', self.content)
        with self.assertRaisesRegex(ValueError, 'changed'):
            source.check()
        source.close()
        source = self.source_open()
        original = self.candidate / 'selected.conf'
        original.rename(self.candidate / 'old-selected')
        self.write('selected.conf', self.selector)
        with self.assertRaisesRegex(ValueError, 'changed'):
            source.check()
        source.close()
        os.link(self.candidate / 'manifest.json', self.directory / 'hardlink')
        with self.assertRaisesRegex(ValueError, 'one link'):
            self.source_open()

    def test_directory_path_replacement_rejected(self):
        source = self.source_open()
        self.candidate.rename(self.directory / 'old-candidate')
        self.candidate.mkdir(mode=0o700)
        with self.assertRaisesRegex(ValueError, 'pathname'):
            source.check()

    def test_symlink_fifo_and_writable_files_rejected(self):
        path = self.candidate / 'selected.conf'
        path.unlink()
        path.symlink_to(self.candidate / 'manifest.json')
        with self.assertRaises(OSError):
            self.source_open()
        path.unlink()
        os.mkfifo(path, 0o400)
        with self.assertRaisesRegex(ValueError, 'regular'):
            self.source_open()
        path.unlink()
        self.write('selected.conf', self.selector)
        path.chmod(0o600)
        with self.assertRaisesRegex(ValueError, '0400'):
            self.source_open()

    def test_manifest_fixed_production_slot_size_and_wrong_slot(self):
        with self.assertRaisesRegex(ValueError, 'slot size'):
            self.source_open(slot_bytes=None)
        self.manifest['files'] = {}
        self.write('manifest.json', json.dumps(self.manifest).encode())
        self.pin = record((self.candidate / 'manifest.json').read_bytes())['sha256']
        with self.assertRaises(KeyError):
            self.source_open()

    def test_plan_is_readonly_and_binds_inactive_context_all_sources(self):
        plan = self.transaction()
        self.auth.assert_not_called()
        self.assertFalse((self.boot / 'update.lock').exists())
        self.assertEqual(self.target_path.read_bytes(), b'x' * SIZE)
        self.assertEqual(plan['operation'], 'inactive-slot-host-update')
        self.assertEqual(plan['context']['active']['slot'], 'A')
        self.assertEqual(plan['source']['slot'], 'B')
        self.assertEqual({call.args[1] for call in self.off_target.call_args_list}, {'259:3'})
        self.assertEqual({call.args[0] for call in self.off_target.call_args_list},
                         {stream.fileno() for stream in self.source.files.values()})
        unsigned = dict(plan)
        del unsigned['plan_sha256']
        self.assertEqual(plan['plan_sha256'], UPDATE.WRITER.digest(UPDATE.WRITER.canonical_json(unsigned)))

    def test_clock_directory_replacement_refuses_before_target_write(self):
        proof = {'state_uuid': 'fixture', 'directory': {'inode': 1}}
        with mock.patch.object(UPDATE, 'clock_context',
                               side_effect=lambda metadata, context: {**context, 'timesync_state': copy.deepcopy(proof)}):
            self.transaction()
            self.assertEqual(self.plan['context']['timesync_state'], proof)
            proof['directory']['inode'] = 2
            report = self.run_apply()
        self.assert_untouched(report)
        self.auth.assert_not_called()

    def test_success_full_write_zeroes_readback_no_activation_or_partition_refresh(self):
        self.transaction()
        result = self.run_apply()
        self.assertTrue(result['success'], result)
        self.assertTrue(result['activation_ready'])
        self.assertTrue(result['target_modified'])
        self.assertEqual(result['bytes_written'], SIZE)
        self.assertEqual(result['bytes_verified'], SIZE)
        self.assertEqual(self.target_path.read_bytes(), self.content)
        self.assertEqual((self.boot / 'selected.conf').read_bytes(), b'active selector sentinel')
        self.assertFalse(result['selector_writes_performed'])
        self.assertEqual([c.args[1] for c in self.ioctl.call_args_list], [UPDATE.WRITER.BLKFLSBUF])

    def test_changed_plan_before_tty_never_authorized(self):
        self.transaction()
        self.context['state']['selector_sha256'] = 'd' * 64
        result = self.run_apply()
        self.assert_untouched(result)
        self.auth.assert_not_called()
        self.assertFalse((self.boot / 'update.lock').exists())

    def test_changed_plan_after_tty_or_after_open_never_written(self):
        for phase in ('tty', 'open'):
            with self.subTest(phase=phase):
                self.transaction()
                if phase == 'tty':
                    self.auth.side_effect = lambda plan: self.context['active'].update(slot='B')
                else:
                    real_open = os.open
                    def opening(path, flags, *args, **kwargs):
                        fd = real_open(path, flags, *args, **kwargs)
                        if str(path) == str(self.target_path) and flags & os.O_RDWR:
                            self.context['state']['selector_sha256'] = 'e' * 64
                        return fd
                    self.stack.enter_context(mock.patch.object(UPDATE.os, 'open', side_effect=opening))
                result = self.run_apply()
                self.assert_untouched(result)

    def test_same_inode_content_mutation_after_tty_cannot_reach_write(self):
        self.transaction()
        self.auth.side_effect = lambda plan: self.write('root.ext4', self.content[:-1] + b'1')
        self.assert_untouched(self.run_apply())

    def test_metadata_change_after_open_stops_before_write(self):
        self.transaction()
        real_open = os.open
        def opening(path, flags, *args, **kwargs):
            fd = real_open(path, flags, *args, **kwargs)
            if str(path) == str(self.target_path) and flags & os.O_RDWR:
                os.pwrite(fd, b'changed', 0)
            return fd
        with mock.patch.object(UPDATE.os, 'open', side_effect=opening):
            result = self.run_apply()
        self.assertFalse(result['target_modified'])
        self.assertFalse(result['success'])
        self.assertIn('metadata changed', result['error'])

    def test_lock_contention_rejects_without_writing(self):
        self.transaction()
        with UPDATE.update_lock(self.context):
            result = self.run_apply()
        self.assert_untouched(result)
        self.assertIn('BlockingIOError', result['error'])

    def test_unsafe_lock_inode_rejected(self):
        self.transaction()
        (self.boot / 'update.lock').symlink_to(self.candidate / 'root.ext4')
        result = self.run_apply()
        self.assert_untouched(result)

    def test_partial_interrupted_write_conservative_report(self):
        self.transaction()
        actual = os.pwrite
        calls = 0
        def write(fd, data, offset):
            nonlocal calls
            calls += 1
            if calls == 1:
                return actual(fd, data[:1024], offset)
            raise KeyboardInterrupt()
        with mock.patch.object(UPDATE.os, 'pwrite', side_effect=write):
            report = self.run_apply()
        self.assertFalse(report['success'])
        self.assertFalse(report['activation_ready'])
        self.assertTrue(report['target_modified'])
        self.assertEqual(report['bytes_attempted'], SIZE)
        self.assertEqual(report['bytes_written'], 1024)
        self.assertEqual(report['bytes_verified'], 0)
        self.assertIn('KeyboardInterrupt', report['error'])

    def test_first_write_error_still_means_possibly_modified(self):
        self.transaction()
        with mock.patch.object(UPDATE.os, 'pwrite', side_effect=OSError('write failed')):
            report = self.run_apply()
        self.assertTrue(report['target_modified'])
        self.assertEqual(report['bytes_written'], 0)
        self.assertEqual(report['bytes_attempted'], SIZE)
        self.assertFalse(report['success'])

    def test_flush_and_readback_failures_conservative(self):
        for operation in ('flush', 'fsync', 'readback'):
            with self.subTest(operation=operation):
                self.transaction()
                if operation == 'flush':
                    patch = mock.patch.object(UPDATE.fcntl, 'ioctl', side_effect=OSError('flush failed'))
                elif operation == 'fsync':
                    patch = mock.patch.object(UPDATE.os, 'fsync', side_effect=OSError('sync failed'))
                else:
                    actual = os.pread
                    def read(fd, size, offset):
                        return b'corrupted' if size == SIZE else actual(fd, size, offset)
                    patch = mock.patch.object(UPDATE.os, 'pread', side_effect=read)
                with patch:
                    result = self.run_apply()
                self.assertTrue(result['target_modified'])
                self.assertEqual(result['bytes_written'], SIZE)
                self.assertFalse(result['success'])
                self.assertFalse(result['activation_ready'])
                self.assertFalse(result['selector_writes_performed'])

    def test_context_changed_after_write_cannot_claim_ready(self):
        self.transaction()
        actual = os.pwrite
        def write(fd, data, offset):
            result = actual(fd, data, offset)
            self.context['gpt']['sha256'] = 'f' * 64
            return result
        with mock.patch.object(UPDATE.os, 'pwrite', side_effect=write):
            result = self.run_apply()
        self.assertTrue(result['target_modified'])
        self.assertFalse(result['success'])
        self.assertFalse(result['activation_ready'])
        self.assertEqual(result['bytes_verified'], 0)

    def test_target_close_error_preserves_written_counts(self):
        self.transaction()
        actual = os.close
        target_inode = self.target_path.stat().st_ino
        def close(fd):
            info = self.real_fstat(fd)
            actual(fd)
            if info.st_ino == target_inode and self.target_path.read_bytes() == self.content:
                raise OSError('target close failed')
        with mock.patch.object(UPDATE.os, 'close', side_effect=close):
            report = self.run_apply()
        self.assertTrue(report['target_modified'])
        self.assertEqual(report['bytes_verified'], SIZE)
        self.assertFalse(report['success'])
        self.assertFalse(report['activation_ready'])

    def test_authorize_requires_tty_backup_and_exact_slot_target_phrase(self):
        plan = self.transaction()
        target = plan['context']['target']
        phrase = f"STAGE B SERIAL {target['canonical_path']} {SIZE} {plan['plan_sha256']}"
        # Resolve the original function from the module file without patched imports.
        spec = importlib.util.spec_from_file_location('stage_host_auth', SPEC.origin)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for incoming, outgoing, ok in (
                (TTY('BACKUP VERIFIED\n' + phrase + '\n'), TTY(), True),
                (io.StringIO('BACKUP VERIFIED\n' + phrase + '\n'), TTY(), False),
                (TTY('BACKUP VERIFIED\n' + phrase + '\n'), io.StringIO(), False),
                (TTY('BACKUP VERIFIED\n' + phrase.replace('STAGE B', 'STAGE A') + '\n'), TTY(), False),
                (TTY('BACKUP VERIFIED\n' + phrase.replace('SERIAL', 'OTHER') + '\n'), TTY(), False),
                (TTY('YES\n' + phrase + '\n'), TTY(), False)):
            if ok:
                module.authorize(plan, incoming, outgoing)
            else:
                with self.assertRaises(ValueError):
                    module.authorize(plan, incoming, outgoing)


    def test_storage_guard_applies_to_every_held_candidate_file(self):
        guard = mock.Mock()
        source = self.source_open(guard=guard)
        self.assertEqual(len(guard.call_args_list), 4)
        self.assertEqual({call.args[0] for call in guard.call_args_list},
                         {source.directory, *(f.fileno() for f in source.files.values())})

    def test_storage_detection_retains_acquisition_mmc_rejection(self):
        with fixture_parent(self.candidate) as fd:
            info = os.fstat(fd)
            device = f'{os.major(info.st_dev)}:{os.minor(info.st_dev)}'
            for filesystem, kind in (('tmpfs', 'ram'), ('ext4', 'external')):
                text = f'12 1 {device} / /fixture rw - {filesystem} none rw\n'
                with mock.patch.object(UPDATE.Path, 'read_text', return_value=text), \
                     mock.patch.object(UPDATE.PREP.ACQUIRE, 'storage_guard') as acquire:
                    UPDATE.storage_guard(fd)
                acquire.assert_called_once_with(fd, kind)
            with mock.patch.object(UPDATE.Path, 'read_text', return_value=''):
                with self.assertRaisesRegex(ValueError, 'unknown'):
                    UPDATE.storage_guard(fd)

    def test_reused_write_report_never_skips_a_write(self):
        self.transaction()
        report = UPDATE.new_report()
        report.update(target_modified=True, bytes_written=SIZE)
        result = UPDATE.apply(self.args, self.source, self.plan, report)
        self.assertIs(result, report)
        self.auth.assert_not_called()
        self.assertFalse(result['success'])
        self.assertEqual(result['bytes_written'], SIZE)
        self.assertIn('fresh report', result['error'])
        self.assertEqual(self.target_path.read_bytes(), b'x' * SIZE)

    def test_report_binds_partial_or_complete_write_to_reviewed_inputs(self):
        self.transaction()
        result = self.run_apply()
        self.assertEqual(result['plan_sha256'], self.plan['plan_sha256'])
        self.assertEqual(result['operation'], self.plan['operation'])
        self.assertEqual(result['staged_slot'], 'B')
        self.assertEqual(result['target'], self.context['target'])
        self.assertEqual(result['source']['root'], record(self.content))
        self.assertEqual(result['source']['manifest_sha256'], self.pin)
        self.assertEqual(result['selected_before'], {'SLOT': 'A'})

    def test_production_cli_has_no_bypass_and_prepare_ignores_plan(self):
        # Help itself must work without runtime hardware or elevated privileges.
        with contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(SystemExit) as result:
                UPDATE.main(['--help'])
        self.assertEqual(result.exception.code, 0)
        self.assertNotIn('--force', output.getvalue())
        self.assertNotIn('--yes', output.getvalue())
        self.assertNotIn('--slot-bytes', output.getvalue())


class ClockContextTests(unittest.TestCase):
    def test_legacy_baseline_needs_no_new_dependency(self):
        context = {'state': {}}
        with mock.patch.object(UPDATE, 'adjacent') as load:
            self.assertIs(UPDATE.clock_context({}, context), context)
        load.assert_not_called()

    def test_known_nonclock_profile_needs_no_clock_state(self):
        context = {'state': {}}
        policy = SimpleNamespace(MANAGEMENT=SimpleNamespace(
            timesync_policy=mock.Mock(return_value=None), timesync_guard=mock.Mock()))
        metadata = {'sources': {'userspace_profile': {'profile': 'normalized-20261106'}}}
        with mock.patch.object(UPDATE, 'adjacent', return_value=policy):
            self.assertIs(UPDATE.clock_context(metadata, context), context)
        policy.MANAGEMENT.timesync_guard.assert_not_called()

    def test_clock_binding_must_match_selected_state(self):
        context = {'state': {'uuid': 'fixture', 'major_minor': '0:1'}}
        proof = {'state_uuid': 'fixture', 'directory': {'inode': 1, 'device': os.makedev(0, 1)}}
        guard = mock.Mock(return_value=proof)
        policy = SimpleNamespace(MANAGEMENT=SimpleNamespace(
            timesync_policy=mock.Mock(return_value={'schema': 1}), timesync_guard=guard))
        metadata = {'sources': {'timesync_state': {'schema': 1}}}
        with mock.patch.object(UPDATE, 'adjacent', return_value=policy):
            self.assertEqual(UPDATE.clock_context(metadata, context), {**context, 'timesync_state': proof})
            self.assertNotIn('timesync_state', context)
            guard.return_value = {**proof, 'state_uuid': 'other'}
            with self.assertRaisesRegex(ValueError, 'selected state'):
                UPDATE.clock_context(metadata, context)
            guard.return_value = {**proof, 'directory': {'inode': 1, 'device': os.makedev(0, 2)}}
            with self.assertRaisesRegex(ValueError, 'selected state'):
                UPDATE.clock_context(metadata, context)


if __name__ == '__main__':
    unittest.main()
