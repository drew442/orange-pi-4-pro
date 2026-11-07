"""Small regular-file transaction fixtures; never hardware acceptance."""
import contextlib
import copy
import fcntl
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('select_live_test', Path(__file__).resolve().parents[1] / 'scripts/select-host-root.py')
LIVE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LIVE)
SIZE = 8192


def record(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


@contextlib.contextmanager
def parent(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        yield fd
    finally:
        os.close(fd)


class TTY(io.StringIO):
    def isatty(self):
        return True


class SelectionTransactionTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.directory = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.real_fstat = os.fstat
        def root_stat(fd):
            value = self.real_fstat(fd)
            return SimpleNamespace(**{name: getattr(value, name) for name in dir(value)
                                     if name.startswith('st_') and name not in ('st_uid', 'st_gid')},
                                   st_uid=0, st_gid=0)
        self.stack.enter_context(mock.patch.object(LIVE.os, 'fstat', side_effect=root_stat))
        self.stack.enter_context(mock.patch.object(LIVE.os, 'geteuid', return_value=0))
        self.stack.enter_context(mock.patch.object(LIVE.PREP, 'safe_parent', parent))
        self.content = b'authenticated-root' + bytes(SIZE - 18)
        self.payload = LIVE.PREP.INIT.PAYLOAD
        self.value = {'schema': 1, 'board': 'Orange Pi 4 Pro A733',
                      'slot_bytes': SIZE, 'slot_uuids': self.payload.SLOT_UUIDS,
                      'kernel_release': self.payload.KERNEL_RELEASE,
                      'files': {f'root-{slot}.ext4': record(self.content) for slot in ('A', 'B')},
                      'sources': {},
                      'management_runtime': {'profile': 'a733-management-v1',
                          'files': {v[0]: {'bytes': 1, 'sha256': 'a' * 64}
                                    for v in self.payload.MANAGEMENT_FILES.values()}},
                      'package_state': {'profile': self.payload.PACKAGE.PROFILE,
                                        'manifest': {'bytes': 1, 'sha256': 'b' * 64}}}
        self.manifest_path = self.directory / 'manifest.json'
        self.write_manifest(json.dumps(self.value).encode())
        self.target = self.directory / 'root-block-fixture'
        self.target.write_bytes(self.content)
        self.boot = self.directory / 'boot'
        self.boot.mkdir(mode=0o700)
        self.before = self.selector('A')
        self.after = self.selector('B')
        (self.boot / 'selected.conf').write_bytes(self.before)
        (self.boot / 'selected.conf').chmod(0o600)
        dev = self.boot.stat().st_dev
        self.context = {'disk': {'serial': 'SERIAL', 'major_minor': '259:0'},
                        'target': {'canonical_path': str(self.target), 'serial': 'SERIAL',
                                   'major_minor': '259:3', 'size_bytes': SIZE},
                        'active': {'slot': 'A', 'marker_sha256': 'c' * 64},
                        'state': {'boot_directory': str(self.boot),
                                  'major_minor': f'{os.major(dev)}:{os.minor(dev)}',
                                  'selector': LIVE.SELECT.parse(self.before),
                                  'selector_sha256': record(self.before)['sha256']},
                        'gpt': {'sha256': 'd' * 64}}
        self.args = SimpleNamespace(target='/dev/nvme0n1', expected_model='MODEL',
                                    expected_serial='SERIAL', expected_bytes=250000000000,
                                    slot='B', intent='activate')
        def snapshot(args, published=False):
            result = copy.deepcopy(self.context)
            if published:
                data = (self.boot / 'selected.conf').read_bytes()
                result['state'].update(selector=LIVE.SELECT.parse(data),
                                       selector_sha256=record(data)['sha256'])
            return result
        self.snap = self.stack.enter_context(mock.patch.object(LIVE, 'snapshot', side_effect=snapshot))
        def validate(fd, target):
            self.assertEqual(os.fstat(fd).st_ino, self.target.stat().st_ino)
            self.assertEqual(target['size_bytes'], SIZE)
            self.assertEqual(fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE, os.O_RDONLY)
        self.validate = self.stack.enter_context(mock.patch.object(LIVE.WRITER, 'validate_fd', side_effect=validate))
        self.inspect = self.stack.enter_context(mock.patch.object(LIVE.PREP, 'compatibility',
            side_effect=lambda stream, slot, value: (self.selector(slot), {})))
        self.auth = self.stack.enter_context(mock.patch.object(LIVE, 'authorize'))

    def selector(self, slot):
        return LIVE.SELECT.serialize({'SCHEMA': '1', 'SLOT': slot,
            'ROOT_UUID': self.payload.SLOT_UUIDS[slot], 'ROOT_MARKER_SHA256': 'c' * 64,
            'KERNEL_RELEASE': self.payload.KERNEL_RELEASE})

    def write_manifest(self, data):
        if self.manifest_path.exists():
            self.manifest_path.chmod(0o600)
        self.manifest_path.write_bytes(data)
        self.manifest_path.chmod(0o400)
        self.pin = record(data)['sha256']

    def manifest(self, **kw):
        source = LIVE.Manifest(self.manifest_path, self.pin, self.args.slot,
                               parent_opener=parent, guard=lambda fd: None,
                               slot_bytes=SIZE, **kw)
        self.stack.callback(source.close)
        return source

    def transaction(self):
        self.source = self.manifest()
        self.plan = LIVE.make_plan(self.args, self.source)
        return self.plan

    def run_apply(self):
        return LIVE.apply(self.args, self.source, self.plan)

    def assert_unpublished(self, report):
        self.assertFalse(report['success'], report)
        self.assertFalse(report['selection_published'], report)
        self.assertEqual((self.boot / 'selected.conf').read_bytes(), self.before)
        self.assertEqual(self.target.read_bytes(), self.content)
        self.assertFalse(report['block_writes_performed'])
        self.assertFalse(report['root_switched'])

    def test_hash_before_json_and_duplicate_json_rejected(self):
        with mock.patch.object(LIVE.json, 'loads', side_effect=AssertionError('parser reached')):
            self.pin = '0' * 64
            with self.assertRaisesRegex(ValueError, 'SHA-256'):
                self.manifest()
        for data in (b'{"schema":1,"schema":1}', b'', b'x' * (LIVE.PREP.LIMIT + 1)):
            self.write_manifest(data)
            with self.assertRaises(ValueError):
                self.manifest()

    def test_manifest_permission_link_and_filename_guards(self):
        for mode in (0o600, 0o444):
            self.manifest_path.chmod(mode)
            with self.assertRaisesRegex(ValueError, '0400'):
                self.manifest()
        self.manifest_path.chmod(0o400)
        os.link(self.manifest_path, self.directory / 'alias')
        with self.assertRaisesRegex(ValueError, 'one regular-file link'):
            self.manifest()
        (self.directory / 'alias').unlink()
        source = self.manifest()
        self.manifest_path.rename(self.directory / 'old')
        self.write_manifest(json.dumps(self.value).encode())
        with self.assertRaisesRegex(ValueError, 'changed'):
            source.check()

    def test_manifest_storage_checks_directory_and_file(self):
        guard = mock.Mock()
        source = LIVE.Manifest(self.manifest_path, self.pin, 'B', parent_opener=parent,
                               guard=guard, slot_bytes=SIZE)
        self.stack.callback(source.close)
        self.assertEqual(guard.call_count, 2)
        self.assertTrue(stat.S_ISDIR(os.fstat(guard.call_args_list[0].args[0]).st_mode))
        self.assertTrue(stat.S_ISREG(os.fstat(guard.call_args_list[1].args[0]).st_mode))

    def test_target_full_hash_precedes_metadata(self):
        source = self.manifest()
        self.target.write_bytes(self.content[:-1] + b'x')
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            LIVE.make_plan(self.args, source)
        self.inspect.assert_not_called()

    def test_readonly_exclusive_activation_nonexclusive_cancellation(self):
        real_open = os.open
        for intent in ('activate', 'cancel-pending'):
            self.args.intent = intent
            if intent == 'cancel-pending':
                self.args.slot = 'A'
                self.context['state'].update(selector=LIVE.SELECT.parse(self.after),
                    selector_sha256=record(self.after)['sha256'])
                (self.boot / 'selected.conf').write_bytes(self.after)
            source = self.manifest()
            with mock.patch.object(LIVE.os, 'open', wraps=real_open) as opened:
                LIVE.make_plan(self.args, source)
            flags = [call.args[1] for call in opened.call_args_list if call.args[0] == str(self.target)]
            self.assertEqual(len(flags), 1)
            self.assertEqual(bool(flags[0] & os.O_EXCL), intent == 'activate')
            self.assertEqual(flags[0] & os.O_ACCMODE, os.O_RDONLY)
            self.assertTrue(flags[0] & os.O_NOFOLLOW)

    def test_cancel_running_marker_mismatch_rejected(self):
        self.args.intent, self.args.slot = 'cancel-pending', 'A'
        self.context['active']['marker_sha256'] = '0' * 64
        source = self.manifest()
        with self.assertRaisesRegex(ValueError, 'actual running root'):
            LIVE.make_plan(self.args, source)

    def test_success_journal_full_before_after_and_no_root_write(self):
        self.transaction()
        with mock.patch.object(LIVE.os, 'pwrite', side_effect=AssertionError('block write')), \
                mock.patch.object(LIVE.fcntl, 'ioctl', side_effect=AssertionError('block mutation')):
            report = self.run_apply()
        self.assertTrue(report['success'], report)
        self.assertTrue(report['journal_durable'])
        self.assertTrue(report['selection_published'])
        self.assertTrue(report['publication_durable'])
        self.assertEqual((self.boot / 'selected.conf').read_bytes(), self.after)
        journal = json.loads((self.boot / report['journal_name']).read_bytes())
        self.assertEqual(bytes.fromhex(journal['before_selector_hex']), self.before)
        self.assertEqual(bytes.fromhex(journal['after_selector_hex']), self.after)
        self.assertEqual(journal['manifest_sha256'], self.pin)
        self.assertEqual(self.target.read_bytes(), self.content)
        self.assertFalse(report['block_writes_performed'])
        self.assertFalse(report['root_switched'])

    def test_clock_directory_replacement_prevents_selector_publication(self):
        proof = {'state_uuid': 'fixture', 'directory': {'inode': 1}}
        with mock.patch.object(LIVE.UPDATE, 'clock_context',
                               side_effect=lambda metadata, context: {**context, 'timesync_state': copy.deepcopy(proof)}):
            self.transaction()
            self.assertEqual(self.plan['context']['timesync_state'], proof)
            proof['directory']['inode'] = 2
            report = self.run_apply()
        self.assert_unpublished(report)
        self.auth.assert_not_called()

    def test_cancel_pending_publishes_authenticated_running_root(self):
        self.args.intent, self.args.slot = 'cancel-pending', 'A'
        self.context['state'].update(selector=LIVE.SELECT.parse(self.after),
                                    selector_sha256=record(self.after)['sha256'])
        (self.boot / 'selected.conf').write_bytes(self.after)
        self.transaction()
        report = self.run_apply()
        self.assertTrue(report['success'], report)
        self.assertEqual(report['selected_before']['SLOT'], 'B')
        self.assertEqual(report['selected_after']['SLOT'], 'A')
        self.assertEqual((self.boot / 'selected.conf').read_bytes(), self.before)
        self.assertFalse(report['root_switched'])
        self.assertFalse(report['block_writes_performed'])

    def test_source_changes_during_hash_are_rejected_before_metadata(self):
        source = self.manifest()
        original = LIVE.WRITER.hash_stream
        def changed(stream, expected):
            original(stream, expected)
            self.write_manifest(json.dumps(self.value).encode())
        with mock.patch.object(LIVE.WRITER, 'hash_stream', side_effect=changed):
            with self.assertRaisesRegex(ValueError, 'manifest changed'):
                LIVE.make_plan(self.args, source)
        self.inspect.assert_not_called()

    def test_pretty_plan_bound_by_hash(self):
        plan = self.transaction()
        body = {k: v for k, v in plan.items() if k != 'plan_sha256'}
        self.assertEqual(plan['plan_sha256'], LIVE.WRITER.digest(LIVE.WRITER.canonical_json(body)))
        self.assertEqual(plan['expected_root'], record(self.content))
        self.assertEqual(plan['proposed_selector'], LIVE.SELECT.parse(self.after))

    def test_plan_changes_before_or_after_tty(self):
        self.transaction()
        self.context['gpt']['sha256'] = 'e' * 64
        self.assert_unpublished(self.run_apply())
        self.auth.assert_not_called()
        self.context['gpt']['sha256'] = 'd' * 64
        self.auth.side_effect = lambda plan: self.context['gpt'].update(sha256='e' * 64)
        self.assert_unpublished(self.run_apply())
        self.assertFalse(list(self.boot.glob('selection-*.json')))

    def test_state_lock_contention(self):
        self.transaction()
        with LIVE.UPDATE.update_lock(self.context):
            report = self.run_apply()
        self.assert_unpublished(report)
        self.assertIn('BlockingIOError', report['error'])

    def test_target_changes_after_authorization(self):
        self.transaction()
        self.auth.side_effect = lambda plan: self.target.write_bytes(b'x' * SIZE)
        report = self.run_apply()
        self.assertFalse(report['selection_published'])
        self.assertIn('SHA-256', report['error'])

    def test_manifest_changes_after_authorization(self):
        self.transaction()
        self.auth.side_effect = lambda plan: self.write_manifest(json.dumps(self.value).encode())
        self.assert_unpublished(self.run_apply())

    def test_target_changes_after_journal_rejected(self):
        self.transaction()
        original = LIVE.journal_store
        def store(*args):
            original(*args)
            self.target.write_bytes(b'x' * SIZE)
        with mock.patch.object(LIVE, 'journal_store', side_effect=store):
            report = self.run_apply()
        self.assertFalse(report['selection_published'])
        self.assertTrue(report['journal_durable'])
        self.assertIn('SHA-256', report['error'])

    def test_selector_pre_and_post_rename_errors_honest_receipts(self):
        self.transaction()
        for published in (False, True):
            def publish(directory, data):
                if published:
                    (self.boot / 'selected.conf').write_bytes(data)
                raise LIVE.SELECT.PublicationError(OSError('fixture'), published)
            with mock.patch.object(LIVE.SELECT, 'atomic_store', side_effect=publish):
                report = self.run_apply()
            self.assertFalse(report['success'])
            self.assertTrue(report['selection_write_attempted'])
            self.assertEqual(report['selection_published'], published)
            self.assertFalse(report['publication_durable'])
            self.assertTrue(report['journal_durable'])

    def test_apply_interrupt_after_real_selector_rename_reports_visible_uncertain(self):
        self.transaction()
        original_sync = os.fsync
        def sync(fd):
            if stat.S_ISDIR(self.real_fstat(fd).st_mode) and (self.boot / 'selected.conf').read_bytes() == self.after:
                raise KeyboardInterrupt('after selector rename')
            return original_sync(fd)
        with mock.patch.object(LIVE.os, 'fsync', side_effect=sync):
            report = self.run_apply()
        self.assertFalse(report['success'])
        self.assertTrue(report['selection_published'])
        self.assertFalse(report['publication_durable'])
        self.assertEqual((self.boot / 'selected.conf').read_bytes(), self.after)
        self.assertEqual(list(self.boot.glob('.selected-*')), [])

    def test_apply_interrupt_before_selector_rename_reports_unpublished(self):
        self.transaction()
        with mock.patch.object(LIVE.SELECT.os, 'replace', side_effect=KeyboardInterrupt('before rename')):
            report = self.run_apply()
        self.assert_unpublished(report)
        self.assertTrue(report['journal_durable'])
        self.assertFalse(report['publication_durable'])

    def test_handled_signal_after_publisher_return_preserves_caller_receipt(self):
        original_store = LIVE.SELECT.atomic_store
        previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, [])
        for signum, handler in ((signal.SIGINT, signal.default_int_handler),
                                (signal.SIGTERM, LIVE.WRITER.interrupted)):
            with self.subTest(signal=signum):
                (self.boot / 'selected.conf').write_bytes(self.before)
                self.transaction()
                previous_handler = signal.signal(signum, handler)
                def store(*args, **kwargs):
                    original_store(*args, **kwargs)
                    os.kill(os.getpid(), signum)
                try:
                    with mock.patch.object(LIVE.SELECT, 'atomic_store', new=store):
                        report = self.run_apply()
                finally:
                    signal.signal(signum, previous_handler)
                self.assertFalse(report['success'])
                self.assertTrue(report['selection_published'])
                self.assertTrue(report['publication_durable'])
                self.assertTrue(report['journal_durable'])
                self.assertEqual((self.boot / 'selected.conf').read_bytes(), self.after)
                self.assertEqual(signal.pthread_sigmask(signal.SIG_BLOCK, []), previous_mask)

    def test_apply_selector_rename_then_interrupt_reconciles_created_inode(self):
        self.transaction()
        original_replace = os.replace
        def replace(*args, **kwargs):
            original_replace(*args, **kwargs)
            raise KeyboardInterrupt('rename returned via interruption')
        with mock.patch.object(LIVE.SELECT.os, 'replace', side_effect=replace):
            report = self.run_apply()
        self.assertFalse(report['success'])
        self.assertTrue(report['selection_published'])
        self.assertFalse(report['publication_durable'])
        self.assertEqual((self.boot / 'selected.conf').read_bytes(), self.after)
        self.assertEqual(list(self.boot.glob('.selected-*')), [])

    def test_journal_rename_then_interrupt_reconciles_created_inode(self):
        self.transaction()
        original = LIVE.PREP.rename_new
        def rename(*args):
            original(*args)
            raise KeyboardInterrupt('journal rename returned via interruption')
        with mock.patch.object(LIVE.PREP, 'rename_new', side_effect=rename):
            report = self.run_apply()
        self.assert_unpublished(report)
        self.assertTrue(report['journal_visible'])
        self.assertFalse(report['journal_durable'])
        self.assertFalse(report['selection_write_attempted'])
        self.assertTrue((self.boot / report['journal_name']).is_file())
        self.assertEqual(list(self.boot.glob('.selection-*')), [])

    def test_postpublication_context_failure_keeps_honest_new_selection(self):
        self.transaction()
        original = self.snap.side_effect
        def changed(args, published=False):
            result = original(args, published)
            if published:
                result['gpt']['sha256'] = 'f' * 64
            return result
        self.snap.side_effect = changed
        report = self.run_apply()
        self.assertFalse(report['success'])
        self.assertTrue(report['selection_published'])
        self.assertTrue(report['publication_durable'])
        self.assertEqual(report['selected_after']['SLOT'], 'B')
        self.assertEqual((self.boot / 'selected.conf').read_bytes(), self.after)

    def test_authorization_exact_phrase_and_tty_required(self):
        plan = self.transaction()
        phrase = f"SELECT activate B SERIAL {self.target} {SIZE} {plan['plan_sha256']}"
        # Use the unpatched function from a fresh module to check its real prompts.
        spec = importlib.util.spec_from_file_location('select_auth_test', SPEC.origin)
        value = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(value)
        value.authorize(plan, TTY('BACKUP VERIFIED\n' + phrase + '\n'), TTY())
        for incoming in (io.StringIO(), TTY('BACKUP VERIFIED\nwrong\n')):
            with self.assertRaises(ValueError):
                value.authorize(plan, incoming, TTY())

    def test_journal_reuse_different_foreign_and_fsync_faults(self):
        with parent(self.boot) as directory:
            data, name = b'{"journal":"fixture"}', 'selection-fixture.json'
            first = LIVE.new_report()
            LIVE.journal_store(directory, data, name, first)
            self.assertTrue(first['journal_durable'])
            inode = (self.boot / name).stat().st_ino
            second = LIVE.new_report()
            LIVE.journal_store(directory, data, name, second)
            self.assertTrue(second['journal_durable'])
            self.assertEqual((self.boot / name).stat().st_ino, inode)
            with self.assertRaisesRegex(ValueError, 'differs'):
                LIVE.journal_store(directory, b'different', name, LIVE.new_report())
            (self.boot / name).chmod(0o644)
            with self.assertRaisesRegex(ValueError, 'unsafe'):
                LIVE.journal_store(directory, data, name, LIVE.new_report())
            (self.boot / name).unlink()
            for effects, visible in (([OSError('file fsync')], False),
                                      ([None, OSError('directory fsync')], True)):
                report = LIVE.new_report()
                with mock.patch.object(LIVE.os, 'fsync', side_effect=effects):
                    with self.assertRaises(OSError):
                        LIVE.journal_store(directory, data, name, report)
                self.assertEqual(report['journal_visible'], visible)
                self.assertFalse(report['journal_durable'])
                self.assertEqual(list(self.boot.glob('.selection-*')), [])
                if visible:
                    self.assertEqual((self.boot / name).read_bytes(), data)
                    # Exact reuse re-fsyncs the directory before declaring durable.
                    LIVE.journal_store(directory, data, name, report)
                    self.assertTrue(report['journal_durable'])
                    (self.boot / name).unlink()

    def test_reused_journal_file_sync_precedes_directory_sync(self):
        with parent(self.boot) as directory:
            data, name = b'existing exact journal', 'selection-reuse.json'
            LIVE.journal_store(directory, data, name, LIVE.new_report())
            calls = []
            original = os.fsync
            def sync(fd):
                calls.append(stat.S_ISDIR(self.real_fstat(fd).st_mode))
                return original(fd)
            with mock.patch.object(LIVE.os, 'fsync', side_effect=sync):
                LIVE.journal_store(directory, data, name, LIVE.new_report())
            self.assertEqual(calls, [False, True])
            report = LIVE.new_report()
            with mock.patch.object(LIVE.os, 'fsync', side_effect=OSError('reuse file sync')):
                with self.assertRaises(OSError):
                    LIVE.journal_store(directory, data, name, report)
            self.assertTrue(report['journal_visible'])
            self.assertFalse(report['journal_durable'])
            self.assertFalse(report['selection_write_attempted'])

    def test_journal_foreign_temporary_never_cleaned(self):
        with parent(self.boot) as directory:
            foreign = self.boot / '.selection-fixed'
            foreign.write_bytes(b'foreign')
            with mock.patch.object(LIVE.secrets, 'token_hex', return_value='fixed'):
                with self.assertRaises(FileExistsError):
                    LIVE.journal_store(directory, b'data', 'selection-final.json', LIVE.new_report())
            self.assertEqual(foreign.read_bytes(), b'foreign')

    def test_journal_failure_never_attempts_selection(self):
        self.transaction()
        original_fsync = os.fsync
        def directory_failure(fd):
            if stat.S_ISDIR(self.real_fstat(fd).st_mode):
                raise OSError('journal directory fsync')
            original_fsync(fd)
        with mock.patch.object(LIVE.os, 'fsync', side_effect=directory_failure):
            report = self.run_apply()
        self.assert_unpublished(report)
        self.assertTrue(report['journal_visible'])
        self.assertFalse(report['journal_durable'])
        self.assertFalse(report['selection_write_attempted'])


if __name__ == '__main__':
    unittest.main()
