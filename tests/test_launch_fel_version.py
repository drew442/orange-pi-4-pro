"""FEL probe orchestration without namespace, USB or hardware access."""
import ast
import hashlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('test_fel_launcher', ROOT / 'build/launch-fel-version.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class LauncherTests(unittest.TestCase):
    def test_phase_names_are_closed(self):
        self.assertEqual(M.phase_path('compile-01').name, 'fel-version-compile-01')
        for tag in ('', '../bad', 'a/b', 'a\n', '-x', 'x' * 65):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                M.phase_path(tag)

    def test_generated_payload_is_valid_and_build_only(self):
        source = M.payload_program('compile-01').decode()
        ast.parse(source)
        self.assertIn(M.COMMIT, source)
        self.assertIn(M.TREE, source)
        self.assertIn('assert r.COMMIT==', source)
        self.assertIn('out/fel-version-compile-01', source)
        self.assertIn('r.build(u,p/', source)
        self.assertNotIn('feldev_open', source)
        self.assertNotIn('--dev', source)
        self.assertNotIn('usb_access', source)

    def test_input_guard_allows_siblings_but_refuses_leaf_or_parent_replacement(self):
        from unittest.mock import Mock
        import tempfile
        import os
        import types
        def module(name, data):
            value = types.ModuleType(name); value.__file__ = name
            exec(compile(data, name, 'exec'), value.__dict__)
            return value
        contents = {name: (ROOT / name).read_bytes() for name in (M.VERIFIER, M.STATE)}
        Held = M.input_held_class(SimpleNamespace(module=module), ROOT, contents)
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp) / 'inputs'; parent.mkdir()
            leaf = parent / 'bundle'; leaf.write_bytes(b'original')
            held = Held(leaf)
            try:
                (parent / 'receipt.json').write_bytes(b'sibling')
                held.unchanged()
                before = leaf.stat(); leaf.write_bytes(b'mutated!')
                os.utime(leaf, ns=(before.st_atime_ns, before.st_mtime_ns + 1000000000))
                with self.assertRaises(ValueError): held.unchanged()
            finally: held.close()
            held = Held(leaf)
            try:
                parent.rename(parent.with_name('old-inputs')); parent.mkdir()
                with self.assertRaises(ValueError): held.unchanged()
            finally: held.close()

    def test_existing_execute_accepts_prefix_without_changing_lifecycle(self):
        spec = importlib.util.spec_from_file_location('fel_roots', ROOT / M.ROOTS)
        roots = importlib.util.module_from_spec(spec); spec.loader.exec_module(roots)
        import tempfile
        from unittest.mock import Mock, patch
        with tempfile.TemporaryDirectory() as tmp:
            shared = SimpleNamespace(safe_directory=Mock(), stop_process=Mock(), mapped_processes=lambda: [],
                                     phase_mounts=lambda p: [], INVENTORY=['inventory'],
                                     resource_sample=lambda: {'free_bytes': 4000000000, 'available_memory_bytes': 2000000000},
                                     atomic_json=Mock(), interrupted=Mock(), UID_BASE=1048576)
            for label, admitted in [('fel-version-compile-01', True), ('fel-version-../escape', False), ('unknown-test', False)]:
                phase = Path(tmp) / str(admitted) / label.replace('/', '_')
                phase.parent.mkdir(exist_ok=True)
                stage = Mock(side_effect=ValueError('expected staging stop'))
                with patch.object(roots.os, 'chown'), patch.object(roots.subprocess, 'check_output', return_value=b'x\n' * 842):
                    with self.assertRaisesRegex(ValueError, 'expected staging stop' if admitted else 'unsafe producer'):
                        roots.execute(shared, None, None, Path(tmp), phase, {}, {}, b'x\n' * 842,
                                      {}, Path('/unused'), Path('/unused'), 'compile-01', output_name=label, stage=stage)
                self.assertEqual(stage.called, admitted)


if __name__ == '__main__':
    unittest.main()
