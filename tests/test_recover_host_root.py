"""Storage-core fixtures bypass admission explicitly and confer no authority.

Small mocked policy/mount fixtures do not establish board recovery.
"""
import contextlib
import copy
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

SPEC = importlib.util.spec_from_file_location('offline_recovery_test', Path(__file__).resolve().parents[1] / 'scripts/recover-host-root.py')
REC = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REC)
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


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.directory = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.real_fstat = os.fstat
        def root_stat(fd):
            value = self.real_fstat(fd)
            return SimpleNamespace(**{name: getattr(value, name) for name in dir(value)
                if name.startswith('st_') and name not in ('st_uid', 'st_gid')}, st_uid=0, st_gid=0)
        self.stack.enter_context(mock.patch.object(REC.os, 'fstat', side_effect=root_stat))
        self.stack.enter_context(mock.patch.object(REC.os, 'geteuid', return_value=0))
        self.stack.enter_context(mock.patch.object(REC.PREP, 'safe_parent', parent))
        self.guard = self.stack.enter_context(mock.patch.object(REC.UPDATE, 'storage_guard'))
        self.off = self.stack.enter_context(mock.patch.object(REC.WRITER, 'source_off_target'))
        self.root = self.directory / 'root'
        self.content = b'r' * SIZE
        self.root.write_bytes(self.content)
        self.state = self.directory / 'state'
        self.state.write_bytes(b's' * SIZE)
        self.disk = self.directory / 'disk'
        self.disk.write_bytes(b'd' * SIZE)
        self.mountpoint = self.directory / 'mounted-state'
        self.mountpoint.mkdir(mode=0o700)
        self.boot = self.mountpoint / 'boot'
        self.boot.mkdir(mode=0o700)
        self.uuid = '12345678-1234-1234-1234-123456789abc'
        (self.mountpoint / '.a733-state').write_text('a733-state-v1\n' + self.uuid + '\n')
        (self.mountpoint / '.a733-state').chmod(0o600)
        dev = self.mountpoint.stat().st_dev
        self.device = f'{os.major(dev)}:{os.minor(dev)}'
        self.stack.enter_context(mock.patch.object(REC, 'MOUNTPOINT', self.mountpoint))
        self.stack.enter_context(mock.patch.object(REC, 'LOCK', self.directory / 'recovery.lock'))
        self.payload = REC.PREP.INIT.PAYLOAD
        self.before, self.after = self.selector('A'), self.selector('B')
        self.write_selector(self.before)
        value = {'schema': 1, 'board': 'Orange Pi 4 Pro A733', 'slot_bytes': SIZE,
                 'slot_uuids': self.payload.SLOT_UUIDS, 'kernel_release': self.payload.KERNEL_RELEASE,
                 'files': {f'root-{s}.ext4': record(self.content) for s in ('A', 'B')}, 'sources': {},
                 'management_runtime': {'profile': 'a733-management-v1', 'files': {
                     v[0]: {'bytes': 1, 'sha256': 'a' * 64} for v in self.payload.MANAGEMENT_FILES.values()}},
                 'package_state': {'profile': self.payload.PACKAGE.PROFILE,
                                   'manifest': {'bytes': 1, 'sha256': 'b' * 64}}}
        self.path = self.directory / 'manifest.json'
        data = json.dumps(value).encode()
        self.path.write_bytes(data)
        self.path.chmod(0o400)
        self.manifest = REC.Manifest(self.path, record(data)['sha256'], 'B',
                                    parent_opener=parent, guard=lambda fd: None, slot_bytes=SIZE)
        self.stack.callback(self.manifest.close)
        self.context = {'disk': {'canonical_path': str(self.disk), 'major_minor': '259:0',
                                'size_bytes': SIZE, 'serial': 'SERIAL'},
                        'target': {'canonical_path': str(self.root), 'major_minor': '259:3',
                                   'size_bytes': SIZE, 'serial': 'SERIAL'},
                        'state': {'canonical_path': str(self.state), 'major_minor': self.device,
                                  'size_bytes': SIZE, 'uuid': self.uuid,
                                  'mountpoint': str(self.mountpoint)}, 'gpt': {'sha256': 'a' * 64}}
        self.args = SimpleNamespace(target='/dev/nvme0n1', expected_model='MODEL', expected_serial='SERIAL',
                                    expected_bytes=250000000000, state_uuid=self.uuid, slot='B')
        self.mounted, self.authorized, self.events = False, False, []
        self.receipts = []
        self.announce = self.stack.enter_context(mock.patch.object(REC, 'announce_state_write',
            side_effect=lambda report: self.receipts.append(copy.deepcopy(report))))
        self.snap = self.stack.enter_context(mock.patch.object(REC, 'snapshot',
            side_effect=lambda args, mounted_state=False: copy.deepcopy(self.context)))
        def validate(fd, target):
            self.assertEqual(os.fstat(fd).st_ino, Path(target['canonical_path']).stat().st_ino)
            import fcntl
            self.assertEqual(fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE, os.O_RDONLY)
        self.stack.enter_context(mock.patch.object(REC.WRITER, 'validate_fd', side_effect=validate))
        self.inspect = self.stack.enter_context(mock.patch.object(REC.PREP, 'compatibility',
            side_effect=lambda stream, slot, value: (self.selector(slot), {})))
        def authorize(plan):
            self.authorized = True
            self.events.append('authorize')
        self.auth = self.stack.enter_context(mock.patch.object(REC, 'authorize', side_effect=authorize))
        self.namespace = self.stack.enter_context(mock.patch.object(REC, 'private_namespace'))
        self.stack.enter_context(mock.patch.object(REC, 'mount_directory', return_value=(
            contextlib.ExitStack(), None, None, False)))
        self.stack.enter_context(mock.patch.object(REC, 'own_state_mounted', side_effect=lambda ctx: self.mounted))
        def run(command, **kw):
            self.assertTrue(self.authorized)
            self.events.append(command[0])
            if command[0] == 'mount':
                self.assertTrue(self.receipts[-1]['state_write_attempted'])
                self.assertTrue(self.receipts[-1]['state_may_be_modified'])
                self.assertFalse(self.receipts[-1]['success'])
                self.mounted = True
            elif command[0] == 'umount':
                self.mounted = False
            else:
                self.fail(command)
        self.run = self.stack.enter_context(mock.patch.object(REC.subprocess, 'run', side_effect=run))

    def selector(self, slot):
        return REC.SELECT.serialize({'SCHEMA': '1', 'SLOT': slot,
            'ROOT_UUID': self.payload.SLOT_UUIDS[slot], 'ROOT_MARKER_SHA256': 'c' * 64,
            'KERNEL_RELEASE': self.payload.KERNEL_RELEASE})

    def write_selector(self, data):
        path = self.boot / 'selected.conf'
        path.write_bytes(data)
        path.chmod(0o600)

    def plan(self):
        self.reviewed = REC.make_plan(self.args, self.manifest)
        return self.reviewed

    def apply(self):
        report = REC._apply_core(self.args, self.manifest, self.reviewed)
        self.assertFalse(report['block_writes_performed'])
        self.assertFalse(report['root_switched'])
        self.assertEqual(self.root.read_bytes(), self.content)
        return report

    def test_prepare_hashes_readonly_never_mounts_or_authorizes(self):
        real_open = os.open
        with mock.patch.object(REC.os, 'open', wraps=real_open) as opened:
            plan = self.plan()
        self.assertEqual(plan['state_image_before'], record(self.state.read_bytes()))
        for path in (self.root, self.state):
            flags = [c.args[1] for c in opened.call_args_list if c.args[0] == str(path)]
            self.assertTrue(flags)
            self.assertTrue(all(f & os.O_EXCL and f & os.O_NOFOLLOW and f & os.O_ACCMODE == os.O_RDONLY for f in flags))
        self.run.assert_not_called()
        self.namespace.assert_not_called()
        self.auth.assert_not_called()
        body = {k: v for k, v in plan.items() if k != 'plan_sha256'}
        self.assertEqual(plan['plan_sha256'], REC.WRITER.digest(REC.WRITER.canonical_json(body)))

    def test_prepare_root_hash_before_metadata(self):
        self.root.write_bytes(b'x' * SIZE)
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            self.plan()
        self.inspect.assert_not_called()

    def test_changed_state_before_and_after_authorization_refused(self):
        self.plan()
        self.state.write_bytes(b'x' * SIZE)
        report = self.apply()
        self.assertFalse(report['success'])
        self.assertFalse(report['state_write_attempted'])
        self.auth.assert_not_called()
        self.state.write_bytes(b's' * SIZE)
        def change(plan):
            self.authorized = True
            self.state.write_bytes(b'x' * SIZE)
        self.auth.side_effect = change
        report = self.apply()
        self.assertFalse(report['state_write_attempted'])
        self.run.assert_not_called()

    def test_success_missing_and_corrupt_selectors(self):
        for before in (None, b'broken\xff\x00', self.before):
            with self.subTest(before=before):
                if before is None:
                    (self.boot / 'selected.conf').unlink()
                else:
                    self.write_selector(before)
                self.plan()
                report = self.apply()
                self.assertTrue(report['success'], report)
                self.assertTrue(report['state_write_attempted'])
                self.assertTrue(report['state_may_be_modified'])
                self.assertTrue(report['state_unmounted'])
                self.assertTrue(report['journal_durable'])
                self.assertTrue(report['selection_published'])
                self.assertTrue(report['publication_durable'])
                self.assertEqual((self.boot / 'selected.conf').read_bytes(), self.after)
                journal = json.loads((self.boot / report['journal_name']).read_bytes())
                self.assertEqual(journal['before_selector_hex'], None if before is None else before.hex())
                self.assertEqual(journal['before_selector_valid'], before == self.before)
                (self.boot / report['journal_name']).unlink()

    def test_unsafe_selector_links_modes_and_size_rejected(self):
        self.plan()
        path = self.boot / 'selected.conf'
        cases = ('symlink', 'hardlink', 'mode', 'oversize')
        for case in cases:
            path.unlink()
            self.write_selector(self.before)
            alias = self.directory / 'alias'
            if case == 'symlink':
                path.rename(alias)
                path.symlink_to(alias)
            elif case == 'hardlink':
                os.link(path, alias)
            elif case == 'mode':
                path.chmod(0o644)
            else:
                self.write_selector(b'x' * 4097)
            report = self.apply()
            self.assertFalse(report['success'], report)
            self.assertFalse(report['journal_visible'])
            self.assertFalse(report['selection_write_attempted'])
            self.assertTrue(report['state_unmounted'])
            if alias.exists():
                alias.unlink()

    def test_bad_marker_rejected_before_boot_creation(self):
        self.plan()
        (self.mountpoint / '.a733-state').write_text('wrong')
        report = self.apply()
        self.assertFalse(report['success'])
        self.assertFalse(report['selection_write_attempted'])
        self.assertTrue(report['state_unmounted'])

    def test_untrusted_selector_owner_and_marker_link_rejected(self):
        self.plan()
        original = REC.os.fstat
        bad_inode = (self.boot / 'selected.conf').stat().st_ino
        def bad_owner(fd):
            value = original(fd)
            if value.st_ino != bad_inode:
                return value
            fields = {name: getattr(value, name) for name in dir(value)
                      if name.startswith('st_') and name != 'st_uid'}
            return SimpleNamespace(**fields, st_uid=123)
        with mock.patch.object(REC.os, 'fstat', side_effect=bad_owner):
            report = self.apply()
        self.assertFalse(report['selection_write_attempted'])
        self.assertIn('unsafe state record', report['error'])
        marker = self.mountpoint / '.a733-state'
        os.link(marker, self.directory / 'marker-alias')
        report = self.apply()
        self.assertFalse(report['selection_write_attempted'])
        self.assertIn('unsafe state marker', report['error'])

    def test_original_and_cleanup_failure_both_visible(self):
        self.plan()
        def fail(command, **kw):
            if command[0] == 'mount':
                self.mounted = True
                raise OSError('journal replay failed')
            raise OSError('unmount failed too')
        self.run.side_effect = fail
        report = self.apply()
        self.assertIn('journal replay failed', report['error'])
        self.assertIn('unmount failed too', report['cleanup_error'])
        self.assertFalse(report['state_unmounted'])

    def test_created_empty_ram_mountpoint_removed_on_failed_mount(self):
        self.plan()
        target = self.directory / 'new-empty-mount'
        target.mkdir(mode=0o700)
        info = target.stat()
        held = self.stack.enter_context(parent(self.directory))
        self.run.side_effect = OSError('mount failed before visible mount')
        with mock.patch.object(REC, 'MOUNTPOINT', target), mock.patch.object(REC, 'mount_directory',
                return_value=(contextlib.ExitStack(), held, (info.st_dev, info.st_ino), True)):
            report = self.apply()
        self.assertFalse(report['success'])
        self.assertFalse(target.exists())
        self.assertFalse(report['state_unmounted'])

    def test_mount_failure_conservatively_marks_state_and_cleans_real_mount(self):
        self.plan()
        original = self.run.side_effect
        def fail(command, **kw):
            original(command, **kw)
            if command[0] == 'mount':
                raise OSError('mount returned failure after replay')
        self.run.side_effect = fail
        report = self.apply()
        self.assertFalse(report['success'])
        self.assertTrue(report['state_may_be_modified'])
        self.assertTrue(report['state_unmounted'])
        self.assertFalse(report['selection_write_attempted'])

    def test_failed_mount_without_actual_mount_does_not_claim_unmount(self):
        self.plan()
        self.run.side_effect = OSError('mount failed')
        report = self.apply()
        self.assertFalse(report['success'])
        self.assertTrue(report['state_may_be_modified'])
        self.assertFalse(report['state_unmounted'])

    def test_unmount_failure_preserves_published_flags(self):
        self.plan()
        original = self.run.side_effect
        def fail(command, **kw):
            if command[0] == 'umount':
                raise OSError('busy recovery mount')
            return original(command, **kw)
        self.run.side_effect = fail
        report = self.apply()
        self.assertFalse(report['success'])
        self.assertTrue(report['selection_published'])
        self.assertTrue(report['publication_durable'])
        self.assertFalse(report['state_unmounted'])
        self.assertEqual(report['state_mountpoint'], str(self.mountpoint))

    def test_journal_precedes_publication_and_root_rehash(self):
        self.plan()
        original = REC.CONTROL.journal_store
        def store(*args):
            original(*args)
            self.events.append('journal')
            self.root.write_bytes(b'x' * SIZE)
        with mock.patch.object(REC.CONTROL, 'journal_store', side_effect=store):
            report = REC._apply_core(self.args, self.manifest, self.reviewed)
        self.assertTrue(report['journal_durable'])
        self.assertFalse(report['selection_write_attempted'])
        self.assertTrue(report['state_unmounted'])
        self.assertEqual(self.events[:3], ['authorize', 'mount', 'journal'])

    def test_journal_failure_prevents_publish(self):
        self.plan()
        with mock.patch.object(REC.CONTROL, 'journal_store', side_effect=OSError('journal')):
            report = self.apply()
        self.assertFalse(report['selection_write_attempted'])
        self.assertTrue(report['state_may_be_modified'])
        self.assertTrue(report['state_unmounted'])

    def test_publication_errors_keep_honest_visibility(self):
        self.plan()
        for visible in (False, True):
            def store(directory, data):
                if visible:
                    self.write_selector(data)
                raise REC.SELECT.PublicationError(OSError('publication'), visible)
            with mock.patch.object(REC.SELECT, 'atomic_store', side_effect=store):
                report = self.apply()
            self.assertFalse(report['success'])
            self.assertTrue(report['journal_durable'])
            self.assertEqual(report['selection_published'], visible)
            self.assertFalse(report['publication_durable'])
            self.assertTrue(report['state_unmounted'])

    def test_sigint_after_publication_preserves_receipt_and_unmounts_state(self):
        self.plan()
        original = REC.CONTROL.publish_selector
        previous = signal.signal(signal.SIGINT, signal.default_int_handler)
        def publish(*args):
            original(*args)
            os.kill(os.getpid(), signal.SIGINT)
        try:
            with mock.patch.object(REC.CONTROL, 'publish_selector', new=publish):
                report = self.apply()
        finally:
            signal.signal(signal.SIGINT, previous)
        self.assertFalse(report['success'])
        self.assertTrue(report['selection_published'])
        self.assertTrue(report['publication_durable'])
        self.assertTrue(report['state_may_be_modified'])
        self.assertTrue(report['state_unmounted'])
        self.assertFalse(self.mounted)
        self.assertEqual((self.boot / 'selected.conf').read_bytes(), self.after)

    def test_sources_and_plan_checked_against_whole_disk(self):
        self.plan()
        checked = []
        report = REC._apply_core(self.args, self.manifest, self.reviewed,
                           plan_check=lambda disk: checked.append(disk))
        self.assertTrue(report['success'], report)
        self.assertTrue(checked)
        self.assertTrue(all(d == self.context['disk'] for d in checked))
        self.assertTrue(self.off.call_args_list)
        self.assertTrue(all(c.args[1] == '259:0' for c in self.off.call_args_list))
        self.assertTrue(any(c.args[0] == self.manifest.directory for c in self.off.call_args_list))
        self.assertTrue(any(c.args[0] == self.manifest.stream.fileno() for c in self.off.call_args_list))

    def test_real_mount_directory_refuses_foreign_and_owns_cleanup_identity(self):
        # Fresh module avoids replacing the mount-directory implementation under test.
        module = REC.adjacent('directory_test', 'recover-host-root.py')
        target = self.directory / 'new-mount'
        with mock.patch.object(module, 'MOUNTPOINT', target), mock.patch.object(module.PREP, 'safe_parent', parent), \
                mock.patch.object(module, 'mount_entries', return_value=[]):
            target.mkdir(mode=0o700)
            (target / 'foreign').write_text('foreign')
            with self.assertRaisesRegex(ValueError, 'empty root-owned'):
                module.mount_directory()
            (target / 'foreign').unlink()
            target.rmdir()
            target.symlink_to(self.mountpoint)
            with self.assertRaises(OSError):
                module.mount_directory()
            target.unlink()
            stack, held, identity, created = module.mount_directory()
            try:
                self.assertTrue(created)
                self.assertEqual(identity, (target.stat().st_dev, target.stat().st_ino))
            finally:
                stack.close()

    def test_authorization_exact_phrase_and_tty(self):
        plan = self.plan()
        module = REC.adjacent('recovery_auth_test', 'recover-host-root.py')
        phrase = f"RECOVER B SERIAL {self.root} {self.uuid} {plan['plan_sha256']}"
        module.authorize(plan, TTY('BACKUP VERIFIED\n' + phrase + '\n'), TTY())
        for stream in (io.StringIO(), TTY('BACKUP VERIFIED\nwrong\n')):
            with self.assertRaises(ValueError):
                module.authorize(plan, stream, TTY())

    def test_no_root_write_or_repair_syscalls(self):
        self.plan()
        with mock.patch.object(REC.os, 'pwrite', side_effect=AssertionError('block write')), \
                mock.patch.object(REC.fcntl, 'ioctl', side_effect=AssertionError('block ioctl')):
            report = self.apply()
        self.assertTrue(report['success'], report)
        commands = [c.args[0] for c in self.run.call_args_list]
        self.assertEqual([c[0] for c in commands], ['mount', 'umount'])


if __name__ == '__main__':
    unittest.main()
