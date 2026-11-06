"""Host-only safety fixtures; these establish no namespace or board acceptance."""
import importlib.util
import json
import os
import select
import signal
import stat
from types import SimpleNamespace
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

SOURCE = Path(__file__).resolve().parents[1] / 'build/launch-userspace-configuration.py'
spec = importlib.util.spec_from_file_location('launch_configuration', SOURCE)
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.host_ns = {name: number + 10 for number, name in enumerate(launcher.NAMES)}
        self.profile = {'nonce': 'a' * 64, 'host_userns_inode': self.host_ns['user'],
                        'host_driver_pid': 9999, 'repo_manifest_sha256': 'b' * 64}
        self.ready = {'nonce': 'a' * 64, 'pid': 3, 'namespace_inodes':
                      {name: number + 100 for number, name in enumerate(launcher.NAMES)},
                      'uid': 0, 'status': {}, 'bootstrap_root': '/a733-export-output/arm-root'}

    def test_import_has_no_runtime_actions(self):
        with mock.patch('subprocess.Popen', side_effect=AssertionError('spawn')), \
             mock.patch('os.geteuid', side_effect=AssertionError('root check')):
            spec.loader.exec_module(launcher)

    def test_safe_tag_fresh_phase_path(self):
        self.assertEqual(launcher.tag_path('run_02-test', self.root),
                         self.root / 'userspace-configure-run_02-test')
        for tag in ('', '../oops', 'a/b', '-bad', 'a.b', 'a\n', 'x' * 65):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                launcher.tag_path(tag, self.root)

    def test_directory_symlink_ancestor_rejected(self):
        (self.root / 'real').mkdir()
        (self.root / 'alias').symlink_to('real')
        with self.assertRaises(ValueError):
            launcher.safe_directory(self.root / 'alias')

    def frozen_metadata(self, uid=launcher.UID_BASE, gid=launcher.UID_BASE,
                        mode=stat.S_IFDIR | 0o700, parent_uid=0, parent_mode=stat.S_IFDIR | 0o700):
        path = Path('/fixture/frozen-root')
        def metadata(current):
            if current == path:
                return SimpleNamespace(st_uid=uid, st_gid=gid, st_mode=mode)
            return SimpleNamespace(st_uid=parent_uid, st_gid=0, st_mode=parent_mode)
        return path, metadata

    def test_frozen_mapped_root_accepts_only_approved_directory_modes(self):
        for mode in (0o700, 0o755):
            path, metadata = self.frozen_metadata(mode=stat.S_IFDIR | mode)
            with self.subTest(mode=mode), mock.patch.object(Path, 'lstat', metadata):
                self.assertEqual(launcher.frozen_root_directory(path), path)

    def test_frozen_root_rejects_wrong_owner_mode_and_symlink(self):
        for changes in ({'uid': 0}, {'gid': 0}, {'uid': launcher.UID_BASE + 1},
                        {'mode': stat.S_IFDIR | 0o777}, {'mode': stat.S_IFDIR | 0o750},
                        {'mode': stat.S_IFDIR | 0o775}, {'mode': stat.S_IFLNK | 0o777},
                        {'parent_uid': launcher.UID_BASE},
                        {'parent_mode': stat.S_IFDIR | 0o777},
                        {'parent_mode': stat.S_IFLNK | 0o777}):
            path, metadata = self.frozen_metadata(**changes)
            with self.subTest(changes=changes), mock.patch.object(Path, 'lstat', metadata):
                with self.assertRaises(ValueError):
                    launcher.frozen_root_directory(path)

    def test_foreign_internal_links_allowed_external_alias_rejected(self):
        tree = self.root / 'foreign'
        tree.mkdir()
        (tree / 'a').write_bytes(b'authentic')
        os.link(tree / 'a', tree / 'b')
        (tree / 'symlink').symlink_to('/usr/bin')
        launcher.validate_source_tree(tree)
        os.link(tree / 'a', self.root / 'outside')
        with self.assertRaisesRegex(ValueError, 'external hardlink'):
            launcher.validate_source_tree(tree)

    def test_special_foreign_file_rejected(self):
        tree = self.root / 'foreign'
        tree.mkdir()
        os.mkfifo(tree / 'fifo')
        with self.assertRaises(ValueError):
            launcher.validate_source_tree(tree)

    def test_protocol_regular_file_owner_mode_and_alias(self):
        path = self.root / 'ready.json'
        path.write_text('{}')
        path.chmod(0o444)
        self.assertEqual(launcher.read_json(path, owner=os.getuid()), {})
        path.chmod(0o644)
        with self.assertRaises(ValueError):
            launcher.read_json(path)
        path.chmod(0o444)
        os.link(path, self.root / 'alias')
        with self.assertRaises(ValueError):
            launcher.read_json(path)

    def test_protocol_symlink_and_size_rejected(self):
        path = self.root / 'value'
        path.write_text('{}')
        (self.root / 'alias').symlink_to(path)
        with self.assertRaises(OSError):
            launcher.regular_bytes(self.root / 'alias')
        with self.assertRaises(ValueError):
            launcher.regular_bytes(path, limit=1)

    @unittest.skipUnless(os.geteuid() == 0, 'root-owned publication fixture')
    def test_atomic_publication_never_replaces(self):
        path = self.root / 'continuation'
        launcher.atomic_json(path, {'nonce': 'a'})
        self.assertEqual(launcher.read_json(path, owner=0), {'nonce': 'a'})
        with self.assertRaises(FileExistsError):
            launcher.atomic_json(path, {'nonce': 'b'})
        self.assertEqual(launcher.read_json(path, owner=0), {'nonce': 'a'})
        self.assertEqual(list(self.root.glob('.api-publish-*')), [])

    def test_ready_requires_exact_namespace_set_and_private_inodes(self):
        launcher.validate_ready(self.ready, self.profile, self.host_ns)
        for name in launcher.NAMES:
            bad = dict(self.ready, namespace_inodes=dict(self.ready['namespace_inodes']))
            bad['namespace_inodes'][name] = self.host_ns[name]
            with self.subTest(name=name), self.assertRaises(ValueError):
                launcher.validate_ready(bad, self.profile, self.host_ns)
        for bad in (dict(self.ready, nonce='c' * 64), dict(self.ready, pid=True),
                    dict(self.ready, arbitrary='value'), dict(self.ready, uid=1),
                    dict(self.ready, bootstrap_root='/etc')):
            with self.assertRaises(ValueError):
                launcher.validate_ready(bad, self.profile, self.host_ns)

    def payload_fixture(self, pid):
        proc = self.root / str(pid)
        proc.mkdir()
        uid = str(launcher.UID_BASE)
        fields = {'Uid': ' '.join([uid] * 4), 'Gid': ' '.join([uid] * 4),
                  'NSpid': str(pid) + ' 3', 'NoNewPrivs': '1'}
        fields.update({key: '0000000000000000' for key in
                       ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb')})
        (proc / 'status').write_text(''.join(f'{key}:\t{value}\n' for key, value in fields.items()))
        for name in ('uid_map', 'gid_map'):
            (proc / name).write_text('0 1048576 65536\n')
        (proc / 'cmdline').write_bytes(b'/usr/bin/python3\0-I\0-B\0/a733-sources/run.py\0')
        return proc

    def test_select_payload_exactly_one_authenticated_candidate(self):
        proc = self.payload_fixture(1234)
        with mock.patch.object(launcher, 'namespaces', return_value=self.ready['namespace_inodes']):
            self.assertEqual(launcher.select_payload(self.ready, self.profile, self.host_ns, self.root), 1234)
            self.payload_fixture(1235)
            with self.assertRaisesRegex(ValueError, 'ambiguous'):
                launcher.select_payload(self.ready, self.profile, self.host_ns, self.root)
        self.assertTrue(proc.exists())

    def test_select_payload_rejects_wrong_command_or_mapping(self):
        proc = self.payload_fixture(1234)
        with mock.patch.object(launcher, 'namespaces', return_value=self.ready['namespace_inodes']):
            (proc / 'cmdline').write_bytes(b'/usr/bin/python3\0evil.py\0')
            with self.assertRaises(ValueError):
                launcher.select_payload(self.ready, self.profile, self.host_ns, self.root)
            (proc / 'cmdline').write_bytes(b'/usr/bin/python3\0-I\0-B\0/a733-sources/run.py\0')
            (proc / 'uid_map').write_text('0 1048576 1\n')
            with self.assertRaises(ValueError):
                launcher.select_payload(self.ready, self.profile, self.host_ns, self.root)

    def test_fixed_helper_enters_no_user_namespace(self):
        command = launcher.helper_command(1234, 'a' * 64)
        self.assertNotIn('--user', command)
        self.assertEqual(command[-3:], ['/a733-sources/project/build/userspace-bootstrap-api.py',
                                       '/a733-export-output/api-setup-request.json', 'a' * 64])
        for name in ('mount', 'pid', 'net', 'uts', 'ipc', 'cgroup'):
            self.assertIn('--' + name, command)
        for pid, digest in ((True, 'a' * 64), (1, 'a' * 64), (42, '../request')):
            with self.assertRaises(ValueError):
                launcher.helper_command(pid, digest)

    def test_payload_command_preserves_isolation_and_changes_ownership_only(self):
        builder = mock.Mock()
        builder.nspawn_command.return_value = ['nspawn', '--private-users-ownership=chown',
                                              '--private-users=1048576:65536', '--private-network',
                                              '--no-new-privileges=yes', '--drop-capability=SEVEN',
                                              '/bin/sh', '/a733-probe.sh']
        args = launcher.payload_command(builder, self.root)
        for arg in ('--private-users-ownership=off', '--private-users=1048576:65536',
                    '--private-network', '--no-new-privileges=yes', '--drop-capability=SEVEN',
                    '--read-only', '--bind-ro=' + str(self.root / 'inputs') + ':/a733-sources'):
            self.assertIn(arg, args)
        self.assertTrue(args[-1].endswith('exec /usr/bin/python3 -I -B /a733-sources/run.py'))

    def test_host_inventory_preserves_multiarch_names_and_rejects_errors(self):
        lock = b'libc6:amd64\t2.35\tamd64\nbase\t1\tall\n'
        inventory = b'base\t1\tall\tinstalled\nlibc6:amd64\t2.35\tamd64\tinstalled\n'
        launcher.validate_host_inventory(inventory, lock)
        for value in (inventory.replace(b':amd64', b''), inventory.replace(b'installed', b'config-files'),
                      inventory + inventory, inventory.replace(b'2.35', b'2.36'), b''):
            with self.assertRaises(ValueError):
                launcher.validate_host_inventory(value, lock)

    def test_inventory_mismatch_fails_before_phase_creation(self):
        contents = {'configs/builder-replay-packages-20261106.lock.tsv': b'pkg\t1\tall\n'}
        with mock.patch.object(launcher, 'check_host', return_value=({}, '')), \
             mock.patch.object(launcher, 'committed_sources', return_value=({}, contents)), \
             mock.patch.object(launcher.subprocess, 'check_output', return_value=b'pkg\t2\tall\tinstalled\n'), \
             mock.patch.object(launcher, 'stage') as stage:
            with self.assertRaisesRegex(ValueError, 'differs from lock'):
                launcher.main(['--tag', 'fixture'])
            stage.assert_not_called()

    def test_committed_snapshot_matches_bytes_and_rejects_dirty_file(self):
        project = self.root / 'project'
        project.mkdir()
        subprocess.run(['git', 'init', '-q', str(project)], check=True)
        for name in launcher.PROJECT_FILES:
            target = project / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('fixture ' + name + '\n')
        subprocess.run(['git', '-C', str(project), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(project), '-c', 'user.name=Fixture', '-c',
                        'user.email=fixture@example.invalid', 'commit', '-qm', 'Fixture'], check=True)
        manifest, contents = launcher.committed_sources(project)
        self.assertEqual(set(manifest['files']), set(launcher.PROJECT_FILES))
        self.assertEqual(set(contents), set(launcher.PROJECT_FILES))
        destination = self.root / 'snapshot'
        # Publication owner changes need root; contents validation is always exercised.
        with mock.patch.object(launcher.os, 'chown'):
            launcher.project_snapshot(destination, contents)
        self.assertEqual((destination / launcher.PROJECT_FILES[0]).read_bytes(),
                         contents[launcher.PROJECT_FILES[0]])
        (project / launcher.PROJECT_FILES[0]).write_text('dirty')
        with self.assertRaisesRegex(ValueError, 'differ from committed'):
            launcher.committed_sources(project)

    def test_bootstrap_helper_failure_never_publishes_continuation(self):
        phase = self.root
        (phase / 'work').mkdir()
        fake = mock.Mock(returncode=1)
        fake.wait.return_value = 1
        fake.pid = 999999999
        with mock.patch.object(launcher, 'select_payload', return_value=1234), \
             mock.patch.object(launcher, 'atomic_json') as publish, \
             mock.patch.object(launcher, 'sha', return_value='d' * 64), \
             mock.patch.object(launcher.subprocess, 'Popen', return_value=fake), \
             mock.patch.object(launcher, 'stop_process') as stop:
            with self.assertRaisesRegex(ValueError, 'helper failed'):
                launcher.bootstrap_api(phase, self.ready, self.profile, self.host_ns, {})
            self.assertEqual(publish.call_count, 1)
            self.assertEqual(publish.call_args.args[0].name, 'api-setup-request.json')
            stop.assert_called_once_with(fake)

    def test_bootstrap_helper_reaped_before_continuation(self):
        phase = self.root
        (phase / 'work').mkdir()
        events = []
        fake = mock.Mock(returncode=0)
        fake.wait.side_effect = lambda timeout: events.append('wait') or 0
        request = {name: self.ready[name] for name in ('nonce', 'namespace_inodes', 'bootstrap_root')}
        request.update({name: self.profile[name] for name in
                        ('host_userns_inode', 'host_driver_pid', 'repo_manifest_sha256')})
        with mock.patch.object(launcher, 'select_payload', return_value=1234), \
             mock.patch.object(launcher, 'atomic_json', side_effect=lambda path, value: events.append(path.name)), \
             mock.patch.object(launcher, 'sha', return_value='d' * 64), \
             mock.patch.object(launcher, 'read_json', return_value={'success': True, 'request': request}), \
             mock.patch.object(launcher.subprocess, 'Popen', return_value=fake), \
             mock.patch.object(launcher, 'stop_process'):
            launcher.bootstrap_api(phase, self.ready, self.profile, self.host_ns, {})
        self.assertEqual(events, ['api-setup-request.json', 'wait', 'api-continue.json'])


class ProcessCleanupTests(unittest.TestCase):
    def child(self, ignore_term=False):
        code = ('import signal,time; '
                + ('signal.signal(signal.SIGTERM,signal.SIG_IGN); ' if ignore_term else '')
                + 'print("ready",flush=True); time.sleep(30)')
        proc = subprocess.Popen(['/usr/bin/python3', '-I', '-B', '-c', code],
                                start_new_session=True, stdout=subprocess.PIPE)
        def finish():
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=5)
            proc.stdout.close()
        self.addCleanup(finish)
        self.assertTrue(select.select([proc.stdout], [], [], 5)[0])
        self.assertEqual(proc.stdout.readline(), b'ready\n')
        return proc

    def test_reaped_leader_is_never_signaled(self):
        proc = subprocess.Popen(['/usr/bin/python3', '-I', '-B', '-c', 'pass'],
                                start_new_session=True)
        proc.wait(timeout=5)
        with mock.patch.object(launcher.os, 'killpg') as kill, \
             mock.patch.object(launcher.os, 'waitid') as wait:
            launcher.stop_process(proc)
        kill.assert_not_called()
        wait.assert_not_called()
        launcher.stop_process(None)

    def stop_and_check_reservation(self, proc, immediate=False):
        original = os.killpg
        signals = []
        def checked(pid, sig):
            self.assertEqual(pid, proc.pid)
            self.assertIsNone(proc.returncode)
            # A successful WNOWAIT lookup proves the leader is still our child.
            os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            signals.append(sig)
            original(pid, sig)
        with mock.patch.object(launcher.os, 'killpg', side_effect=checked):
            if immediate:
                with mock.patch.object(launcher.time, 'monotonic', side_effect=[0, 16]):
                    launcher.stop_process(proc)
            else:
                launcher.stop_process(proc)
        self.assertEqual(signals, [signal.SIGTERM, signal.SIGKILL])
        with self.assertRaises(ChildProcessError):
            os.waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)

    def test_running_child_is_reserved_until_group_signals_finish(self):
        proc = self.child()
        self.stop_and_check_reservation(proc)
        self.assertEqual(proc.returncode, -signal.SIGTERM)

    def test_term_resistant_child_is_killed_and_reaped(self):
        proc = self.child(ignore_term=True)
        self.stop_and_check_reservation(proc, immediate=True)
        self.assertEqual(proc.returncode, -signal.SIGKILL)

    def test_unowned_pid_cannot_trigger_group_signals(self):
        fake = SimpleNamespace(pid=os.getpid(), returncode=None)
        with mock.patch.object(launcher.os, 'killpg') as kill:
            with self.assertRaises(ChildProcessError):
                launcher.stop_process(fake)
        kill.assert_not_called()


if __name__ == '__main__':
    unittest.main()
