import contextlib
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import stat
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'observe-endurance-kvm-exit.py'
BASE = ROOT / 'scripts' / 'observe-first-kvm-exit.py'
SPEC = importlib.util.spec_from_file_location('endurance_exit_observer', SCRIPT)
wrapper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(wrapper)
SOURCE = BASE.read_bytes()


def fake_root_fstat(descriptor):
    """Emulate protected root ancestors on an unprivileged test checkout."""
    values = list(os.stat(descriptor))
    values[4] = 0
    if stat.S_ISDIR(values[0]):
        values[0] &= ~0o022
    return os.stat_result(values)


def authenticated_module():
    with patch.object(wrapper, 'read_protected_source', return_value=SOURCE):
        return wrapper.load_observer()


class EnduranceObserverTests(unittest.TestCase):
    def test_reviewed_base_digest_is_pinned(self):
        self.assertEqual(hashlib.sha256(SOURCE).hexdigest(), wrapper.BASE_SHA256)

    def test_mutated_source_never_executes(self):
        malicious = SOURCE + b'\nraise AssertionError("unauthenticated execution")\n'
        with patch.object(wrapper, 'read_protected_source', return_value=malicious):
            with self.assertRaisesRegex(RuntimeError, 'SHA-256 mismatch'):
                wrapper.load_observer()

    def test_protected_read_and_authentication(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / wrapper.BASE_NAME
            base.write_bytes(SOURCE)
            base.chmod(0o400)
            with patch.object(wrapper, '__file__', str(Path(directory) / SCRIPT.name)), \
                    patch.object(wrapper.os, 'fstat', side_effect=fake_root_fstat):
                module = wrapper.load_observer()
            self.assertEqual(module.UNIT, b'a733-guest-firstkvm.service')
            self.assertFalse((Path(directory) / '__pycache__').exists())

    def test_refuses_writable_file_and_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / wrapper.BASE_NAME
            base.write_bytes(SOURCE)
            link = Path(directory) / 'alias.py'
            link.symlink_to(base)
            with patch.object(wrapper.os, 'fstat', side_effect=fake_root_fstat):
                with self.assertRaisesRegex(RuntimeError, 'read-only regular file'):
                    wrapper.read_protected_source(base)
                with self.assertRaises(OSError):
                    wrapper.read_protected_source(link)

    def test_refuses_unprotected_ancestor(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, 'protected directories'):
                wrapper.read_protected_source(Path(directory) / 'missing.py')

    def test_refuses_nonroot_and_writable_directory(self):
        for uid, mode in [(1000, stat.S_IFDIR | 0o755),
                          (0, stat.S_IFDIR | 0o777)]:
            with self.subTest(uid=uid, mode=mode), \
                    patch.object(wrapper.os, 'fstat', return_value=types.SimpleNamespace(
                        st_uid=uid, st_mode=mode)):
                with self.assertRaisesRegex(RuntimeError, 'protected directories'):
                    wrapper.require_protected_directory(123)

    def test_refuses_special_file_without_blocking(self):
        with tempfile.TemporaryDirectory() as directory:
            fifo = Path(directory) / 'fifo'
            os.mkfifo(fifo, 0o400)
            with patch.object(wrapper.os, 'fstat', side_effect=fake_root_fstat):
                with self.assertRaisesRegex(RuntimeError, 'read-only regular file'):
                    wrapper.read_protected_source(fifo)

    def test_source_size_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / 'oversized.py'
            base.write_bytes(b' ' * (wrapper.MAX_SOURCE_BYTES + 1))
            base.chmod(0o400)
            with patch.object(wrapper.os, 'fstat', side_effect=fake_root_fstat):
                with self.assertRaisesRegex(RuntimeError, 'size bound'):
                    wrapper.read_protected_source(base)

    def test_cli_requires_fixed_mode_before_loading(self):
        for argv in [[], ['--mode', 'custom'], ['--mode', 'smoke', '--unit', 'other'],
                     ['--mode', 'release', '--timeout', '1']]:
            with self.subTest(argv=argv), patch.object(wrapper, 'load_observer') as load, \
                    contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as exit_info:
                    wrapper.main(argv)
                self.assertEqual(exit_info.exception.code, 2)
                load.assert_not_called()

    def test_modes_delegate_fixed_unit_deadline_and_no_arguments(self):
        for mode, timeout in [('smoke', 300.0), ('release', 29100.0)]:
            module = authenticated_module()
            saved = sys.argv
            def run():
                self.assertEqual(module.UNIT, b'a733-guest-endurance.service')
                self.assertEqual(module.TIMEOUT, timeout)
                self.assertEqual(sys.argv, [module.__file__])
                return 7
            with self.subTest(mode=mode), patch.object(wrapper, 'load_observer', return_value=module), \
                    patch.object(module, 'main', side_effect=run):
                self.assertEqual(wrapper.main(['--mode', mode]), 7)
            self.assertIs(sys.argv, saved)

    def test_failed_dependency_emits_only_stderr(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(wrapper, 'load_observer', side_effect=OSError('missing base')), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(wrapper.main(['--mode', 'release']), 1)
        self.assertEqual(stdout.getvalue(), '')
        self.assertIn('missing base', stderr.getvalue())

    def test_argv_restored_when_delegate_raises(self):
        saved = sys.argv
        module = authenticated_module()
        with patch.object(wrapper, 'load_observer', return_value=module), \
                patch.object(module, 'main', side_effect=RuntimeError('dependency missing')), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(wrapper.main(['--mode', 'smoke']), 1)
        self.assertIs(sys.argv, saved)

    def test_release_reference_and_bounded_exit_grace(self):
        for mode, exit_time, expected in [('release', 28800, 0),
                                          ('release', 29099, 0),
                                          ('release', 29100, 1),
                                          ('smoke', 28800, 1)]:
            module = authenticated_module()
            events, now = [], [0.0]
            class Bus:
                initial = True
                def __enter__(self):
                    events.append(('ref', now[0]))
                    return self
                def __exit__(self, *_):
                    events.append(('unref', now[0]))
                def get(self, name):
                    if name == 'SubState':
                        return 'running'
                    if self.initial:
                        self.initial = False
                        return 'inactive'
                    return 'active' if now[0] < exit_time else 'inactive'
                def snapshot(self):
                    return dict(LoadState='loaded', ActiveState='inactive', SubState='dead',
                                Result='success', MainPID=0, ControlPID=0, ExecMainPID=123,
                                ExecMainCode=1, ExecMainStatus=0,
                                ExecMainStartTimestampMonotonic=1,
                                ExecMainExitTimestampMonotonic=exit_time * 1000000 + 1)
            def sleep(_):
                now[0] = min(exit_time, now[0] + 14400)
            original = module.observe
            def observe(bus, emit):
                return original(bus, emit, clock=lambda: now[0], sleep=sleep)
            with self.subTest(mode=mode, exit_time=exit_time), patch.object(wrapper, 'load_observer', return_value=module), \
                    patch.object(module, 'Bus', Bus), patch.object(module, 'observe', side_effect=observe), \
                    patch.object(module, 'emit', side_effect=lambda record: events.append(record)), \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(wrapper.main(['--mode', mode]), expected)
            self.assertEqual(events[0], ('ref', 0.0))
            self.assertEqual(events[1]['event'], 'ready')
            self.assertEqual(events[1]['unit'], 'a733-guest-endurance.service')
            if expected == 0:
                self.assertEqual(events[-2], ('unref', float(exit_time)))
                self.assertEqual(events[-1]['event'], 'final')
            else:
                self.assertEqual(events[-1][0], 'unref')
                self.assertFalse(any(isinstance(e, dict) and e['event'] == 'final'
                                     for e in events))


if __name__ == '__main__':
    unittest.main()
