"""Runtime-to-archive integrity and source attribution regressions."""
import importlib.util
import ast
import stat
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('installer_archive_tests', REPO / 'build/installer-initramfs.py')
ARCHIVE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ARCHIVE)
PROVENANCE = ('build/installer-runtime.py', 'build/host-state.py', 'configs/host_config.py', 'configs/package_state.py',
              'configs/installer-runtime-policy.json', 'docs/evidence/host-userspace-20261104.json',
              'configs/host-userspace-packages.lock.tsv', 'configs/host-userspace-package-hashes.lock.tsv',
              'configs/builder-packages.lock.tsv')


class InstallerArchiveTests(unittest.TestCase):
    def fixture(self, directory):
        path = directory / 'root/usr/lib/python3.11/urllib/__init__.py'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'')
        path.chmod(0o644)
        manifest = {'schema': 1, 'source': json.loads((REPO / 'docs/evidence/host-userspace-20261104.json').read_text())['files']['rootfs.tar.gz'],
                    'policy': ARCHIVE.HOST.file_record(REPO / 'configs/installer-runtime-policy.json'),
                    'provenance': {p: ARCHIVE.HOST.file_record(REPO / p) for p in PROVENANCE},
                    'files': {'usr/lib/python3.11/urllib/__init__.py': {'type': 'file', 'mode': 0o644, **ARCHIVE.HOST.record(b'')}}}
        (directory / 'runtime-manifest.json').write_text(json.dumps(manifest))
        return path, manifest

    def test_authenticated_empty_stdlib_file_is_supported(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.fixture(directory)
            entries, _, _ = ARCHIVE.runtime_entries(directory)
            self.assertEqual(entries['usr/lib/python3.11/urllib/__init__.py'][1], b'')

    def test_mutated_file_namespace_or_mode_is_rejected(self):
        for mutation in ('bytes', 'extra', 'mode'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                path, _ = self.fixture(directory)
                if mutation == 'bytes':
                    path.write_bytes(b'changed')
                elif mutation == 'extra':
                    (path.parent / 'unexpected.py').write_bytes(b'extra')
                else:
                    path.chmod(0o600)
                with self.assertRaises(ValueError):
                    ARCHIVE.runtime_entries(directory)

    def test_stale_or_missing_stager_provenance_is_rejected(self):
        for change in ('missing', 'stale'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                _, manifest = self.fixture(directory)
                if change == 'missing':
                    del manifest['provenance']['build/host-state.py']
                else:
                    manifest['provenance']['build/installer-runtime.py']['sha256'] = '0' * 64
                (directory / 'runtime-manifest.json').write_text(json.dumps(manifest))
                with self.assertRaisesRegex(ValueError, 'provenance'):
                    ARCHIVE.runtime_entries(directory)

    def test_private_probe_stub_contains_real_newlines(self):
        source = ast.parse((REPO / 'tests/verify_installer_runtime_arm.py').read_text())
        program = next(node.value for node in ast.walk(source) if isinstance(node, ast.Constant)
                       and isinstance(node.value, str) and node.value.startswith('import argparse,base64'))
        generated = ast.parse(program)
        texts = [node.value for node in ast.walk(generated) if isinstance(node, ast.Constant) and isinstance(node.value, str)]
        stub = next(value for value in texts if value.startswith('#!/bin/sh'))
        self.assertTrue(stub.startswith('#!/bin/sh' + chr(10)))
        self.assertTrue(stub.endswith('exit 0' + chr(10)))
        self.assertIn(chr(10) + 'printf fixture-call', stub)

    def test_archive_decoder_rejects_unsafe_paths_and_special_objects(self):
        spec = importlib.util.spec_from_file_location('installer_arm_decode', REPO / 'tests/verify_installer_runtime_arm.py')
        verifier = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(verifier)
        valid = ARCHIVE.INIT.newc({'empty': (stat.S_IFREG | 0o644, b'')})
        self.assertEqual(verifier.decode(valid)['empty'][1], b'')
        for damaged in (valid[:-1], valid.replace(b'empty', b'../xx', 1),
                        ARCHIVE.INIT.newc({'device': (stat.S_IFBLK | 0o600, b'')})):
            with self.assertRaises(ValueError):
                verifier.decode(damaged)

    def package_fixture(self, output):
        diagnostic = {name: {'bytes': 1, 'sha256': '0' * 64}
                      for name in ('Image', 'board.dtb', 'kernel.config', 'initramfs.cpio.gz')}
        def read(path, *args):
            if Path(path) == ARCHIVE.HOST.DIAGNOSTIC_RECORD:
                return json.dumps({'files': diagnostic}).encode()
            return b'x'
        return (patch.object(ARCHIVE.STATE, 'validate_workspace_storage'),
                patch.object(ARCHIVE, 'runtime_entries', return_value=({}, {'source': {}}, b'{}')),
                patch.object(ARCHIVE.HOST, 'read_regular', side_effect=read),
                patch.object(ARCHIVE.INIT, 'validate_kernel'),
                patch.object(ARCHIVE.INIT.PAYLOAD, 'module_entries', return_value={}),
                patch.object(ARCHIVE.gzip, 'decompress', return_value=b''))

    def test_archive_includes_closed_tools_and_provenance(self):
        (REPO / 'out').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='installer-initramfs-test-', dir=REPO / 'out') as temporary:
            output = Path(temporary)
            output.rmdir()
            from contextlib import ExitStack
            with ExitStack() as stack:
                for context in self.package_fixture(output):
                    stack.enter_context(context)
                result = ARCHIVE.build('missing', REPO, 'missing', output)
            self.assertEqual(result['entries']['sbin/modprobe'],
                             {'mode': stat.S_IFLNK | 0o777, **ARCHIVE.HOST.record(b'../bin/busybox')})
            tools = ARCHIVE.TOOLS.entries(REPO)
            for name, (mode, data) in tools.items():
                self.assertEqual(result['entries']['usr/lib/a733-tools/' + name],
                                 {'mode': stat.S_IFREG | mode, **ARCHIVE.HOST.record(data)})
            for name in ARCHIVE.TOOLS.SOURCES:
                self.assertEqual(result['recipes'][name], ARCHIVE.HOST.file_record(REPO / name))
            self.assertIn('build/maintenance-tools.py', result['recipes'])
            self.assertEqual(result['tool_package'], {'profile': ARCHIVE.TOOLS.PROFILE,
                             'package_json': ARCHIVE.HOST.record(tools['package.json'][1])})
            for name in ('install-nvme.py', 'acquire-nvme.py', 'plan-nvme.py', 'nvme_layout.py'):
                self.assertIn('usr/lib/a733-installer/' + name, result['entries'])

    def test_runtime_recipe_hashes_use_captured_archive_bytes(self):
        from contextlib import ExitStack
        captured = ARCHIVE.TOOLS.entries(REPO)
        other_sources = {source: (REPO / source).read_bytes()
                         for source, _ in ARCHIVE.SOURCES.values()
                         if source not in captured}
        real_compress = ARCHIVE.gzip.compress
        real_file_record = ARCHIVE.HOST.file_record
        compressed = False
        def compress(data, *args, **kwargs):
            nonlocal compressed
            compressed = True
            return real_compress(data, *args, **kwargs)
        def changed_record(path):
            name = str(Path(path).relative_to(REPO))
            if compressed and (name in ARCHIVE.TOOLS.SOURCES or name in other_sources):
                self.fail('runtime source reread after archive capture: ' + name)
            return real_file_record(path)
        with tempfile.TemporaryDirectory(prefix='installer-initramfs-test-', dir=REPO / 'out') as temporary:
            output = Path(temporary)
            output.rmdir()
            with ExitStack() as stack:
                for context in self.package_fixture(output):
                    stack.enter_context(context)
                stack.enter_context(patch.object(ARCHIVE.gzip, 'compress', side_effect=compress))
                stack.enter_context(patch.object(ARCHIVE.HOST, 'file_record', side_effect=changed_record))
                result = ARCHIVE.build('missing', REPO, 'missing', output)
            self.assertTrue(compressed)
            for name in ARCHIVE.TOOLS.SOURCES:
                self.assertEqual(result['recipes'][name], ARCHIVE.HOST.record(captured[name][1]))
            for name, data in other_sources.items():
                self.assertEqual(result['recipes'][name], ARCHIVE.HOST.record(data))

    def test_legacy_and_new_tool_paths_share_one_captured_snapshot(self):
        from contextlib import ExitStack
        captured = ARCHIVE.TOOLS.entries(REPO)
        metadata = json.loads(captured['package.json'][1])
        duplicate = {source for source, _ in ARCHIVE.SOURCES.values()
                     if source in ARCHIVE.TOOLS.SOURCES}
        for source in duplicate:
            mode, original = captured[source]
            captured[source] = (mode, original + b'\n# captured newer source\n')
            item = {'mode': mode, **ARCHIVE.HOST.record(captured[source][1])}
            metadata['files'][source] = metadata['source_records'][source] = item
        captured['package.json'] = (0o444, ARCHIVE.TOOLS.canonical(metadata))
        with tempfile.TemporaryDirectory(prefix='installer-initramfs-test-', dir=REPO / 'out') as temporary:
            output = Path(temporary)
            output.rmdir()
            with ExitStack() as stack:
                for context in self.package_fixture(output):
                    stack.enter_context(context)
                stack.enter_context(patch.object(ARCHIVE.TOOLS, 'entries', return_value=captured))
                result = ARCHIVE.build('missing', REPO, 'missing', output)
            for legacy, (source, _) in ARCHIVE.SOURCES.items():
                if source in duplicate:
                    expected = ARCHIVE.HOST.record(captured[source][1])
                    for field, value in expected.items():
                        self.assertEqual(result['entries'][legacy][field], value)
                        self.assertEqual(result['entries']['usr/lib/a733-tools/' + source][field], value)
                    self.assertEqual(result['recipes'][source], expected)

    def test_existing_archive_budgets_remain_enforced(self):
        from contextlib import ExitStack
        class Large:
            def __init__(self, size):
                self.size = size
            def __len__(self):
                return self.size
        for expanded in (True, False):
            with self.subTest(expanded=expanded), tempfile.TemporaryDirectory(
                    prefix='installer-initramfs-test-', dir=REPO / 'out') as temporary:
                output = Path(temporary)
                output.rmdir()
                with ExitStack() as stack:
                    for context in self.package_fixture(output):
                        stack.enter_context(context)
                    if expanded:
                        stack.enter_context(patch.object(ARCHIVE.INIT, 'newc', return_value=Large(256 * 1024**2 + 1)))
                    else:
                        stack.enter_context(patch.object(ARCHIVE.gzip, 'compress', return_value=Large(64 * 1024**2 - 64)))
                    with self.assertRaisesRegex(ValueError, 'budget'):
                        ARCHIVE.build('missing', REPO, 'missing', output)
                self.assertFalse(output.exists())

    def test_output_must_be_new_absolute_ignored_workspace(self):
        for output in ('out/installer-initramfs-relative', '/tmp/installer-initramfs-outside'):
            with self.subTest(output=output), self.assertRaises(ValueError):
                ARCHIVE.build('missing', 'missing', 'missing', output)


if __name__ == '__main__':
    unittest.main()
