"""Unprivileged trust-boundary regressions for the opt-in ARM verifier."""
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('maintenance_arm_verifier', Path(__file__).with_name('verify_maintenance_arm.py'))
VERIFY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFY)


class TrustTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.root = self.base / 'tree'
        self.root.mkdir()
        (self.root / 'scripts').mkdir()
        (self.root / 'scripts/probe.py').write_bytes(b'trusted bytes\n')
        (self.root / 'scripts/probe.py').chmod(0o444)
        (self.root / 'scripts').chmod(0o555)
        self.root.chmod(0o555)
        self.expected = {'scripts/probe.py': (0o444, b'trusted bytes\n')}

    def tearDown(self):
        for directory, dirs, files in os.walk(self.base, followlinks=False):
            Path(directory).chmod(0o700)
        self.temporary.cleanup()

    def test_authentication_precedes_json_parser(self):
        manifest = self.base / 'manifest.json'
        manifest.write_bytes(b'not json')
        with mock.patch.object(VERIFY.json, 'loads', side_effect=AssertionError('parser reached')):
            with self.assertRaisesRegex(ValueError, 'before parsing'):
                VERIFY.authenticate(manifest, '0' * 64)

    def test_authenticated_duplicate_keys_refused(self):
        path = self.base / 'manifest.json'
        path.write_bytes(b'{"schema":1,"schema":2}')
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            VERIFY.authenticate(path, hashlib.sha256(path.read_bytes()).hexdigest())

    def test_exact_trusted_tree_accepts(self):
        VERIFY.validate_tree(self.root, self.expected)

    def test_authenticated_self_declared_bytes_cannot_replace_checkout(self):
        path = self.root / 'scripts/probe.py'
        path.chmod(0o644)
        path.write_bytes(b'altered bytes\n')
        path.chmod(0o444)
        with self.assertRaisesRegex(ValueError, 'trusted checkout'):
            VERIFY.validate_tree(self.root, self.expected)

    def test_extra_directory_refused(self):
        self.root.chmod(0o755)
        (self.root / 'unexpected').mkdir(mode=0o555)
        self.root.chmod(0o555)
        with self.assertRaisesRegex(ValueError, 'unexpected package directory'):
            VERIFY.validate_tree(self.root, self.expected)

    def test_symlink_parent_refused(self):
        self.root.chmod(0o755)
        (self.root / 'scripts').chmod(0o755)
        (self.root / 'scripts').rename(self.base / 'elsewhere')
        (self.root / 'scripts').symlink_to(self.base / 'elsewhere', target_is_directory=True)
        self.root.chmod(0o555)
        with self.assertRaisesRegex(ValueError, 'unexpected package object'):
            VERIFY.validate_tree(self.root, self.expected)

    def test_hardlinked_expected_file_refused(self):
        os.link(self.root / 'scripts/probe.py', self.base / 'alias')
        with self.assertRaisesRegex(ValueError, 'unexpected package object'):
            VERIFY.validate_tree(self.root, self.expected)

    def test_runtime_alias_cannot_be_staging_parent(self):
        root = self.base / 'stage'
        entries = {'a': (stat.S_IFLNK | 0o777, b'b'),
                   'a/child': (stat.S_IFREG | 0o444, b'data')}
        with self.assertRaisesRegex(ValueError, 'symlink parent'):
            VERIFY.stage_entries(root, entries)
        self.assertFalse((self.base / 'b').exists())

    def test_runtime_special_object_refused(self):
        with self.assertRaisesRegex(ValueError, 'special object'):
            VERIFY.stage_entries(self.base / 'stage', {'block': (stat.S_IFBLK | 0o600, b'')})

    def test_probe_source_compiles(self):
        compile(VERIFY.PROBE, '/probe.py', 'exec')
        self.assertNotIn('guard=lambda', VERIFY.PROBE)


class NamespaceTests(unittest.TestCase):
    def test_probe_repairs_inherited_ignored_and_blocked_sigint(self):
        # Execute the exact fixture helper with a real publication operation;
        # signal changes stay in a subprocess and never affect this test runner.
        helper = next(node for node in ast.parse(VERIFY.PROBE).body
                      if isinstance(node, ast.FunctionDef) and
                      node.name == 'configure_probe_sigint')
        code = '''
import importlib.util,os,pathlib,signal,tempfile
assert signal.getsignal(signal.SIGINT)==signal.SIG_IGN
assert signal.SIGINT in signal.pthread_sigmask(signal.SIG_BLOCK,set())
'''+ast.unparse(helper)+'''
configure_probe_sigint()
spec=importlib.util.spec_from_file_location('selection',%r)
S=importlib.util.module_from_spec(spec);spec.loader.exec_module(S)
data=S.serialize({'SCHEMA':'1','SLOT':'A','ROOT_UUID':S.INIT.PAYLOAD.SLOT_UUIDS['A'],
                  'ROOT_MARKER_SHA256':'a'*64,'KERNEL_RELEASE':S.INIT.PAYLOAD.KERNEL_RELEASE})
with tempfile.TemporaryDirectory() as tmp:
    directory=pathlib.Path(tmp)
    replace=S.os.replace
    def interrupted(*args,**kwargs):
        replace(*args,**kwargs);os.kill(os.getpid(),signal.SIGINT)
    S.os.replace=interrupted
    try:
        try:S.atomic_store(directory,data)
        except S.PublicationError as error:
            assert error.published
            assert isinstance(error.__cause__,KeyboardInterrupt)
        else:raise AssertionError('queued SIGINT not delivered')
    finally:S.os.replace=replace
    assert (directory/'selected.conf').read_bytes()==data
''' % str(VERIFY.REPO / 'build/root-selection.py')

        def inherited_signal_state():
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT})

        result = subprocess.run([sys.executable, '-I', '-B', '-c', code],
                                preexec_fn=inherited_signal_state,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_private_root_mount_precedes_restricted_mounts_and_chroot(self):
        root = Path('/var/lib/a733-maintenance-arm-test')
        pin = hashlib.sha256(Path(VERIFY.__file__).read_bytes()).hexdigest()
        calls = mock.Mock()
        with mock.patch.object(VERIFY.os, 'getpid', return_value=1), \
                mock.patch.object(VERIFY.subprocess, 'run', calls.run), \
                mock.patch.object(VERIFY.os, 'chroot', calls.chroot), \
                mock.patch.object(VERIFY.os, 'chdir', calls.chdir), \
                mock.patch.object(VERIFY.os, 'execve', calls.execve):
            VERIFY.worker(root, pin)
        options = {'check': True, 'env': VERIFY.ENV,
                   'capture_output': True, 'timeout': 20}
        self.assertEqual(calls.mock_calls, [
            mock.call.run(['/usr/bin/mount', '--make-rprivate', '/'], **options),
            mock.call.run(['/usr/bin/mount', '--bind', str(root), str(root)], **options),
            mock.call.run(['/usr/bin/mount', '-t', 'proc', '-o',
                           'ro,nosuid,nodev,noexec', 'proc', str(root / 'proc')], **options),
            mock.call.run(['/usr/bin/mount', '--bind', '/sys', str(root / 'sys')], **options),
            mock.call.run(['/usr/bin/mount', '-o', 'remount,bind,ro,nosuid,nodev,noexec',
                           str(root / 'sys')], **options),
            mock.call.chroot(root), mock.call.chdir('/'),
            mock.call.execve('/usr/bin/python3',
                             ['/usr/bin/python3', '-I', '-B', '/probe.py'], VERIFY.ENV),
        ])

    def test_root_bind_failure_stops_before_chroot(self):
        root = Path('/var/lib/a733-maintenance-arm-test')
        pin = hashlib.sha256(Path(VERIFY.__file__).read_bytes()).hexdigest()
        with mock.patch.object(VERIFY.os, 'getpid', return_value=1), \
                mock.patch.object(VERIFY.subprocess, 'run',
                                  side_effect=[None, OSError('bind failed')]) as run, \
                mock.patch.object(VERIFY.os, 'chroot') as chroot, \
                mock.patch.object(VERIFY.os, 'execve') as execve:
            with self.assertRaisesRegex(OSError, 'bind failed'):
                VERIFY.worker(root, pin)
        self.assertEqual(run.call_count, 2)
        chroot.assert_not_called()
        execve.assert_not_called()


if __name__ == '__main__':
    unittest.main()
