"""Runtime-to-archive integrity and source attribution regressions."""
import importlib.util
import ast
import stat
import json
from pathlib import Path
import tempfile
import unittest

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

    def test_output_must_be_new_absolute_ignored_workspace(self):
        for output in ('out/installer-initramfs-relative', '/tmp/installer-initramfs-outside'):
            with self.subTest(output=output), self.assertRaises(ValueError):
                ARCHIVE.build('missing', 'missing', 'missing', output)


if __name__ == '__main__':
    unittest.main()
