"""Pure transaction fixtures; no real device, mount or hardware acceptance."""
import contextlib
import copy
import importlib.util
import io
import os
from pathlib import Path
import stat
import tempfile
import uuid
import types
import unittest
from unittest.mock import patch, Mock

SPEC = importlib.util.spec_from_file_location('guest_transaction',
    Path(__file__).resolve().parents[1] / 'scripts/activate-guest-storage.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class Transaction(unittest.TestCase):
    def setUp(self):
        self.context = {'disk': {'canonical_path': '/dev/nvme0n1'},
                        'guest': {'canonical_path': '/dev/nvme0n1p5', 'uuid': 'guest',
                                  'serial': 'serial', 'size_bytes': 123,
                                  'major_minor': '259:5'}, 'state': {'major_minor': '259:4'}}
        self.args = types.SimpleNamespace()
        self.plan = {'context': self.context, 'mounted_before': False, 'plan_sha256': 'hash'}
        self.visible = False
        self.row = ('123', '1', '259:5', '/', '/run/a733-guests', 'rw')
        self.directory = Mock()
        self.patches = contextlib.ExitStack()
        self.addCleanup(self.patches.close)
        def use(name, **kwargs):
            return self.patches.enter_context(patch.object(M, name, **kwargs))
        self.make = use('make_plan', return_value=self.plan)
        use('snapshot', return_value=self.context)
        use('authorize')
        use('activation_lock', side_effect=lambda c, **k: contextlib.nullcontext())
        use('block_stream', side_effect=lambda *a, **k: contextlib.nullcontext(42))
        use('MountDirectory', return_value=self.directory)
        use('checked_command', side_effect=lambda name: '/usr/bin/' + name)
        use('fixed_entries', side_effect=lambda: [self.row] if self.visible else [])
        use('checked_mount', side_effect=lambda *a: self.row if self.visible else
            (_ for _ in ()).throw(ValueError('no mount')))
        self.grant = use('grant_traversal')
        self.run = self.patches.enter_context(patch.object(M.subprocess, 'run', side_effect=self.command))
        self.patches.enter_context(patch.object(M.POLICY, 'guest_superblock'))
        self.stderr = self.patches.enter_context(contextlib.redirect_stderr(io.StringIO()))

    def apply(self):
        # Explicit transaction-core fixture: installed custody is composed in
        # test_guest_activation_admission.py, never bypassed by a runtime flag.
        return M._apply(self.args, self.plan, plan_check=lambda: None,
                        authorization=M.authorize)

    def command(self, command, **kwargs):
        self.visible = command[0].endswith('/mount')

    def test_success_receipt_precedes_kernel_and_no_other_commands(self):
        def command(argv, **kwargs):
            self.assertIn('"guest_may_be_modified": true', self.stderr.getvalue())
            self.assertIn('"mount_attempted": true', self.stderr.getvalue())
            self.visible = True
        self.run.side_effect = command
        report = self.apply()
        self.assertTrue(report['success'])
        self.assertTrue(report['mount_retained'])
        self.assertTrue(report['guest_may_be_modified'])
        self.assertEqual(self.run.call_args.args[0], ['/usr/bin/mount', '-t', 'ext4', '-o',
            'rw,nodev,nosuid,noexec', '/dev/nvme0n1p5', '/run/a733-guests'])

    def test_failed_mount_still_reports_possible_journal_replay(self):
        self.run.side_effect = OSError('mount failed')
        report = self.apply()
        self.assertFalse(report['success'])
        self.assertTrue(report['guest_may_be_modified'])
        self.assertTrue(report['mount_attempted'])
        self.assertFalse(report['mount_retained'])
        self.assertFalse(report['mount_may_be_visible'])

    def test_locks_held_during_failure_cleanup(self):
        held = []
        @contextlib.contextmanager
        def lock(context, **kwargs):
            held.append(True)
            try:
                yield
            finally:
                held.pop()
        self.grant.side_effect = ValueError('fail after mount')
        def command(argv, **kwargs):
            self.assertEqual(held, [True])
            self.visible = argv[0].endswith('/mount')
        self.run.side_effect = command
        with patch.object(M, 'activation_lock', side_effect=lock):
            report = self.apply()
        self.assertFalse(report['success'])
        self.assertFalse(report['mount_retained'])
        self.assertEqual(held, [])

    def test_plan_change_blocks_mount(self):
        self.make.side_effect = [self.plan, dict(self.plan, plan_sha256='changed')]
        report = self.apply()
        self.assertFalse(report['success'])
        self.assertFalse(report['guest_may_be_modified'])
        self.run.assert_not_called()

    def test_signal_after_kernel_mount_reconciles_and_unmounts(self):
        def command(argv, **kwargs):
            if argv[0].endswith('/mount'):
                self.visible = True
                raise KeyboardInterrupt('SIGTERM after kernel mount')
            self.visible = False
        self.run.side_effect = command
        report = self.apply()
        self.assertFalse(report['success'])
        self.assertTrue(report['guest_may_be_modified'])
        self.assertFalse(report['mount_retained'])
        self.assertEqual(self.run.call_count, 2)

    def test_cleanup_error_retains_mount_and_original_error(self):
        self.grant.side_effect = ValueError('permission failure')
        def command(argv, **kwargs):
            if argv[0].endswith('/umount'):
                raise OSError('busy')
            self.visible = True
        self.run.side_effect = command
        report = self.apply()
        self.assertFalse(report['success'])
        self.assertTrue(report['mount_retained'])
        self.assertIn('permission failure', report['errors'][0])
        self.assertIn('busy', report['cleanup_errors'][0])

    def test_foreign_appearance_never_unmounted(self):
        def command(argv, **kwargs):
            self.visible = True
        self.run.side_effect = command
        with patch.object(M, 'checked_mount', side_effect=ValueError('foreign')):
            report = self.apply()
        self.assertFalse(report['success'])
        self.assertTrue(report['mount_retained'])
        self.assertEqual(self.run.call_count, 1)
        self.grant.assert_not_called()

    def test_replaced_owned_mount_never_unmounted(self):
        self.grant.side_effect = ValueError('failure')
        with patch.object(M, 'checked_mount', side_effect=[self.row, ('999',)]):
            report = self.apply()
        self.assertFalse(report['success'])
        self.assertTrue(report['mount_retained'])
        self.assertEqual(self.run.call_count, 1)
        self.assertTrue(report['cleanup_errors'])

    def test_preexisting_mount_failure_never_unmounts(self):
        self.plan['mounted_before'], self.visible = True, True
        self.grant.side_effect = ValueError('mode mismatch')
        report = self.apply()
        self.assertFalse(report['success'])
        self.assertFalse(report['mount_attempted'])
        self.assertFalse(report['guest_may_be_modified'])
        self.assertTrue(report['mount_retained'])
        self.run.assert_not_called()
        self.directory.check.assert_not_called()

    def test_already_mounted_is_idempotent(self):
        self.plan['mounted_before'], self.visible = True, True
        report = self.apply()
        self.assertTrue(report['success'])
        self.assertFalse(report['guest_may_be_modified'])
        self.run.assert_not_called()

    def test_foreign_directory_rejected_before_mount(self):
        self.directory.check.side_effect = ValueError('foreign path')
        report = self.apply()
        self.assertFalse(report['success'])
        self.assertFalse(report['guest_may_be_modified'])
        self.run.assert_not_called()


class BootstrapTransaction(Transaction):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        a = M.ADMISSION
        for change in (patch.object(a, 'RUNTIME', str(Path(self.tmp.name) / 'a733-admission')),
                       patch.object(a, 'RUNTIME_PARENT', self.tmp.name),
                       patch.object(a, 'ROOT_UID', os.getuid()),
                       patch.object(a, 'ROOT_GID', os.getgid()), patch.object(a, '_tmpfs'),
                       patch.object(a, '_ancestor'), patch.object(a.os, 'geteuid', return_value=0)):
            self.patches.enter_context(change)
        self.admission = a.initialize(str(uuid.uuid4()), os.getgid(), lambda: 'running')
        # Close before patches and temporary directory are released.
        self.patches.callback(self.admission.close)
        self.owner = self.admission.bootstrap_owner
        self.host = self.patches.enter_context(patch.object(M.CONTEXT, 'host_identity'))

    def test_bootstrap_mount_without_manual_authorization_or_reopen(self):
        guard = Mock()
        report = M.apply_bootstrap(self.args, self.plan, self.owner, plan_check=guard)
        self.assertTrue(report['success'])
        M.authorize.assert_not_called()
        self.assertGreater(guard.call_count, 4)
        self.assertEqual(self.admission._read()['state'], 'closed')
        self.assertIsNotNone(self.owner.lease.fd)
        self.host.assert_called_with(self.admission.boot_id)

    def test_invalid_capabilities_refuse_before_mount_or_permissions(self):
        for kind in ('foreign', 'shared', 'closed', 'nonce', 'shutdown', 'maintenance',
                     'boot', 'host', 'unlocked'):
            with self.subTest(kind=kind):
                owner = self.owner
                old_nonce, old_reason = owner.nonce, owner.reason
                lease_fd = owner.lease.fd
                old_read = self.admission._read
                extra = contextlib.ExitStack()
                try:
                    if kind == 'foreign':
                        owner = types.SimpleNamespace(**vars(owner))
                    elif kind == 'shared':
                        owner.lease.exclusive = False
                    elif kind == 'closed':
                        owner.lease.fd = None
                    elif kind == 'nonce':
                        owner.nonce = str(uuid.uuid4())
                    elif kind in ('shutdown', 'maintenance'):
                        owner.reason = kind
                    elif kind == 'boot':
                        extra.enter_context(patch.object(self.admission, '_read',
                            side_effect=lambda: dict(old_read(), boot_id=str(uuid.uuid4()))))
                    elif kind == 'host':
                        self.host.side_effect = ValueError('wrong boot/native host')
                    elif kind == 'unlocked':
                        import fcntl
                        fcntl.flock(lease_fd, fcntl.LOCK_UN)
                    report = M.apply_bootstrap(self.args, self.plan, owner)
                    self.assertFalse(report['success'])
                    self.run.assert_not_called()
                    self.grant.assert_not_called()
                    M.MountDirectory.assert_not_called()
                finally:
                    extra.close()
                    owner = self.owner
                    owner.nonce, owner.reason = old_nonce, old_reason
                    owner.lease.fd, owner.lease.exclusive = lease_fd, True
                    self.host.side_effect = None
                    if kind == 'unlocked':
                        fcntl.flock(lease_fd, fcntl.LOCK_EX)

    def test_guard_failure_after_mount_retains_conservative_cleanup(self):
        def guard():
            if self.visible:
                raise ValueError('registered plan guard changed')
        report = M.apply_bootstrap(self.args, self.plan, self.owner, plan_check=guard)
        self.assertFalse(report['success'])
        self.assertTrue(report['guest_may_be_modified'])
        self.assertTrue(report['mount_retained'])
        self.assertTrue(report['cleanup_errors'])
        self.grant.assert_not_called()
        self.assertEqual(self.run.call_count, 1)

    def test_manual_entrypoint_still_calls_authorize(self):
        M.authorize.side_effect = ValueError('TTY required')
        report = M.apply(self.args, self.plan)
        self.assertFalse(report['success'])
        self.run.assert_not_called()
        M.authorize.assert_called_once_with(self.plan)


class DescriptorPolicy(unittest.TestCase):
    def test_manual_authorization_requires_tty_and_exact_backup_phrase(self):
        plan = {'context': {'guest': {'serial': 'S', 'canonical_path': '/dev/p5',
                                     'uuid': 'U'}}, 'plan_sha256': 'H'}
        for terminal, text in ((False, ''), (True, 'wrong\n'),
                               (True, 'BACKUP VERIFIED\nwrong\n')):
            with self.subTest(terminal=terminal, text=text):
                stdin, stderr = io.StringIO(text), io.StringIO()
                stdin.isatty = stderr.isatty = lambda: terminal
                with patch.object(M.sys, 'stdin', stdin), patch.object(M.sys, 'stderr', stderr):
                    with self.assertRaises(ValueError):
                        M.authorize(plan)
        stdin = io.StringIO('BACKUP VERIFIED\nACTIVATE S /dev/p5 U H\n')
        stderr = io.StringIO()
        stdin.isatty = stderr.isatty = lambda: True
        with patch.object(M.sys, 'stdin', stdin), patch.object(M.sys, 'stderr', stderr):
            M.authorize(plan)

    def test_bootstrap_has_no_cli_option(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(SystemExit) as exit_status:
                M.main(['--help'])
        self.assertEqual(exit_status.exception.code, 0)
        self.assertNotIn('--bootstrap', output.getvalue())
        arguments = ['prepare', '--target', '/dev/nvme0n1', '--expected-model', 'M',
                     '--expected-serial', 'S', '--guest-uuid',
                     '298b281a-d10e-44eb-bdb9-285e27eaafad',
                     '--expected-bytes', '256060514304']
        for options in (arguments + ['--bootstrap'], arguments + ['--authorized'],
                        arguments + ['--no-authorize'], ['bootstrap'] + arguments[1:]):
            with self.subTest(options=options), \
                    contextlib.redirect_stderr(io.StringIO()), \
                    patch.object(M, 'make_plan', side_effect=AssertionError('runtime reached')):
                with self.assertRaises(SystemExit) as refused:
                    M.main(options)
                self.assertEqual(refused.exception.code, 2)

    def test_wrong_device_never_chmod(self):
        info = types.SimpleNamespace(st_dev=os.makedev(179, 1), st_uid=0, st_gid=0,
                                     st_mode=stat.S_IFDIR | 0o700)
        context = {'guest': {'major_minor': '259:5'}}
        with patch.object(M, 'checked_mount', return_value=('id',)), \
             patch.object(M.PREP, 'safe_parent', side_effect=lambda p: contextlib.nullcontext(55)), \
             patch.object(M.os, 'open', return_value=77), \
             patch.object(M.os, 'fstat', return_value=info), \
             patch.object(M.os, 'close'), patch.object(M.os, 'fchmod') as chmod:
            with self.assertRaisesRegex(ValueError, 'device differs'):
                M.grant_traversal(None, context, ('id',), M.new_report())
            chmod.assert_not_called()

    def test_review_parent_storage_guard_precedes_file_open_or_read(self):
        with patch.object(M.PREP, 'safe_parent', side_effect=lambda p: contextlib.nullcontext(55)), \
             patch.object(M, 'storage_guard', side_effect=ValueError('SD forbidden')), \
             patch.object(M.os, 'open') as opening, \
             patch.object(M.WRITER, 'read_json') as reading:
            with self.assertRaisesRegex(ValueError, 'SD forbidden'):
                M.Review(Path('/trusted/plan'), {})
            opening.assert_not_called()
            reading.assert_not_called()

    def test_traversal_uses_held_guest_root_and_idempotent_mode(self):
        for initial_mode in (0o700, 0o711):
            with self.subTest(initial_mode=initial_mode):
                info = types.SimpleNamespace(st_dev=os.makedev(259, 5), st_uid=0, st_gid=0,
                    st_ino=234, st_mode=stat.S_IFDIR | initial_mode)
                after = types.SimpleNamespace(**vars(info))
                after.st_mode = stat.S_IFDIR | 0o711
                def chmod(fd, mode):
                    self.assertEqual((fd, mode), (77, 0o711))
                report = M.new_report()
                with patch.object(M, 'checked_mount', return_value=('id',)), \
                     patch.object(M.PREP, 'safe_parent', side_effect=lambda p: contextlib.nullcontext(55)), \
                     patch.object(M.os, 'open', return_value=77), \
                     patch.object(M.os, 'fstat', side_effect=[info, after]), \
                     patch.object(M.os, 'stat', return_value=after), \
                     patch.object(M.os, 'close'), patch.object(M.os, 'fsync') as sync, \
                     patch.object(M.os, 'fchmod', side_effect=chmod) as changing:
                    M.grant_traversal(None, {'guest': {'major_minor': '259:5'}}, ('id',), report)
                    self.assertEqual(changing.call_count, int(initial_mode == 0o700))
                    self.assertEqual(sync.call_count, int(initial_mode == 0o700))
                    self.assertEqual(report['permissions_may_be_modified'], initial_mode == 0o700)

    def test_namespace_requires_host_visibility(self):
        with patch.object(M.os, 'stat', side_effect=[types.SimpleNamespace(st_dev=1, st_ino=1),
                                                     types.SimpleNamespace(st_dev=1, st_ino=2)]):
            with self.assertRaisesRegex(ValueError, 'PID 1'):
                M.host_namespace()


if __name__ == '__main__':
    unittest.main()
