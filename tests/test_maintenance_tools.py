"""Closed maintenance package, safe reads and deterministic publication checks."""
import gzip
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('maintenance_tests', REPO / 'build/maintenance-tools.py')
TOOLS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TOOLS)


class MaintenanceToolsTests(unittest.TestCase):
    def setUp(self):
        # Tests run in /tmp; production still checks every absolute ancestor.
        self.strict_check = TOOLS.check_directory
        temporary_identity = TOOLS.inode(Path('/tmp').stat())
        def test_check(info):
            if TOOLS.inode(info) != temporary_identity:
                self.strict_check(info)
        self.check_patch = patch.object(TOOLS, 'check_directory', side_effect=test_check)
        self.check_patch.start()
        self.addCleanup(self.check_patch.stop)

    def fixture(self, root):
        for name in TOOLS.SOURCES:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((name + '\n').encode())
        (root / 'build/maintenance-tools.py').write_bytes(b'# builder\n')
        (root / 'build/host-state.py').write_text('def validate_workspace_storage(path):\n    pass\n')

    def test_real_sources_are_exact_and_allowlist_is_closed(self):
        values = TOOLS.entries()
        self.assertEqual(len(TOOLS.SOURCES), 57)
        self.assertEqual(len(TOOLS.LEGACY_SOURCES), 39)
        self.assertEqual(set(TOOLS.SOURCES) - set(TOOLS.LEGACY_SOURCES), {
            'configs/guest_registry.py', 'configs/guest_lifecycle.py', 'configs/guest_qmp.py',
            'scripts/guest-admission.py', 'scripts/guest-lifecycle-context.py',
            'scripts/guest-lifecycle-store.py', 'scripts/guest-lifecycle.py',
            'configs/guest_run_permit.py', 'scripts/guest-process-evidence.py',
            'scripts/guest-qmp-channel.py', 'scripts/guest-checkpoint-transaction.py',
            'scripts/launch-managed-guest.py', 'scripts/managed-guest-supervisor.py',
            'scripts/managed-guest-fixture.py', 'configs/managed-guest-profile.lock.json',
            'configs/guest_checkpoint_records.py', 'scripts/managed-guest-checkpoint.py',
            'scripts/guest-operation-admission.py'})
        self.assertIn('build/timesync-state.py', TOOLS.SOURCES)
        self.assertEqual(set(values), set(TOOLS.SOURCES) | {'package.json'})
        for name in TOOLS.SOURCES:
            self.assertEqual(values[name], (0o444, (REPO / name).read_bytes()))
        metadata = json.loads(values['package.json'][1])
        self.assertEqual(metadata['profile'], TOOLS.PROFILE)
        self.assertEqual(metadata['entrypoints'], TOOLS.ENTRYPOINTS)
        self.assertEqual(set(metadata['source_records']), set(TOOLS.SOURCES))
        self.assertNotIn('package.json', metadata['files'])
        self.assertEqual(TOOLS.canonical(metadata), values['package.json'][1])
        self.assertEqual(len(metadata['entrypoints']), 16)

    def test_extracted_package_imports_and_cli_help_without_checkout(self):
        # Use only archive bytes in a private relocated tree. Import every helper,
        # including the HTTPS source otherwise reached only by restore apply.
        probe = r"""
import importlib.util, json, pathlib, sys
assert sys.flags.isolated and sys.dont_write_bytecode
root = pathlib.Path(sys.argv[1]).resolve()
package = json.loads((root / 'package.json').read_bytes())
original = importlib.util.spec_from_file_location
loaded = set()
def local_spec(name, location, *args, **kwargs):
    path = pathlib.Path(location).resolve()
    assert path.is_relative_to(root), ('dependency escaped package', str(path))
    loaded.add(str(path.relative_to(root)))
    return original(name, location, *args, **kwargs)
importlib.util.spec_from_file_location = local_spec
def load(name, path):
    spec = local_spec(name, root / path)
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value
for index, path in enumerate(sorted(package['source_records'])):
    if path.endswith('.py'):
        load('relocated_' + str(index), path)
restore = load('relocated_restore', 'scripts/restore-guest.py')
restore.module('relocated_https', 'scripts/guest-restore-https.py')
activation = load('relocated_activation', 'scripts/activate-guest-storage.py')
context = load('relocated_context', 'scripts/guest-lifecycle-context.py')
controller = load('relocated_controller', 'scripts/guest-lifecycle.py')
accounts = load('relocated_accounts', 'scripts/guest-accounts.py')
checkpoint = load('relocated_checkpoint', 'scripts/managed-guest-checkpoint.py')
assert activation.ADMISSION is context.ADMISSION is controller.ADMISSION
assert accounts._bootstrap_context().ADMISSION is context.ADMISSION
assert checkpoint.LIFECYCLE.ADMISSION is context.ADMISSION
assert checkpoint.LIFECYCLE._READY_VERIFIER is checkpoint.authenticate_ready_cohort
operation = load('relocated_operation', 'scripts/guest-operation-admission.py')
assert operation.ADMISSION is context.ADMISSION
assert loaded <= set(package['source_records'])
print(json.dumps(sorted(loaded)))
"""
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            library = directory / 'usr/lib'
            library.mkdir(parents=True)
            with tarfile.open(fileobj=io.BytesIO(TOOLS.archive(TOOLS.entries())), mode='r:gz') as archive:
                archive.extractall(library, filter='data')
            root = library / 'a733-tools'
            before = sorted(path.relative_to(root).as_posix() for path in root.rglob('*'))
            environment = {'PATH': os.defpath, 'LC_ALL': 'C', 'PYTHONPATH': str(REPO)}
            result = subprocess.run([sys.executable, '-I', '-B', '-c', probe, str(root)],
                                    cwd=directory, env=environment, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(set(json.loads(result.stdout)), {name for name in TOOLS.SOURCES if name.endswith('.py')})
            metadata = json.loads((root / 'package.json').read_bytes())
            for name, path in metadata['entrypoints'].items():
                with self.subTest(entrypoint=name):
                    result = subprocess.run([sys.executable, '-I', '-B', str(root / path), '--help'],
                                            cwd=directory, env=environment, capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn('usage:', result.stdout)
            self.assertEqual(sorted(path.relative_to(root).as_posix() for path in root.rglob('*')), before)

    def test_deterministic_archive_and_root_ownership(self):
        values = TOOLS.entries()
        first = TOOLS.archive(values)
        self.assertEqual(first, TOOLS.archive(values))
        self.assertEqual(first[4:8], b'\0' * 4)
        with tarfile.open(fileobj=io.BytesIO(gzip.decompress(first)), mode='r:') as tar:
            names = tar.getnames()
            self.assertEqual(names, sorted(names))
            self.assertEqual({m.name.removeprefix('a733-tools/') for m in tar if m.isfile()}, set(values))
            for member in tar:
                self.assertEqual((member.uid, member.gid, member.mtime, member.uname, member.gname), (0, 0, 0, '', ''))
                self.assertEqual(member.mode, 0o555 if member.isdir() else 0o444)
                self.assertTrue(member.isdir() or member.isfile())
                if member.isfile():
                    self.assertEqual(tar.extractfile(member).read(), values[member.name.removeprefix('a733-tools/')][1])

    def test_missing_symlink_special_and_ancestor_symlink_refused(self):
        for kind in ('missing', 'symlink', 'fifo', 'ancestor', 'hardlink'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self.fixture(root)
                path = root / TOOLS.SOURCES[0]
                if kind == 'ancestor':
                    path.parent.rename(root / 'elsewhere')
                    path.parent.symlink_to(root / 'elsewhere', target_is_directory=True)
                else:
                    path.unlink()
                    if kind == 'symlink':
                        path.symlink_to(root / TOOLS.SOURCES[1])
                    elif kind == 'fifo':
                        os.mkfifo(path)
                    elif kind == 'hardlink':
                        os.link(root / TOOLS.SOURCES[1], path)
                with self.assertRaises((ValueError, OSError)):
                    TOOLS.entries(root)

    def test_source_mutation_and_budget_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            path = root / TOOLS.SOURCES[0]
            original = os.fstat
            count = 0
            def changed(fd):
                nonlocal count
                count += 1
                if count == 2:
                    path.write_bytes(b'changed during read\n')
                return original(fd)
            with patch.object(TOOLS.os, 'fstat', side_effect=changed), self.assertRaisesRegex(ValueError, 'changed'):
                TOOLS.read_source(path)
            with patch.object(TOOLS, 'MAX_SOURCE', 1), self.assertRaises(ValueError):
                TOOLS.entries(root)
            with patch.object(TOOLS, 'MAX_PAYLOAD', 1), self.assertRaisesRegex(ValueError, 'budget'):
                TOOLS.entries(root)

    def test_build_publication_refuses_existing_and_cleans_private_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            output = root / 'out/maintenance-tools-test'
            with patch.object(TOOLS, 'REPO', root):
                manifest = TOOLS.build(output)
                self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE((output / 'tree').stat().st_mode), 0o555)
                self.assertEqual(json.loads((output / 'manifest.json').read_bytes()), manifest)
                self.assertEqual(manifest['artifacts']['tools.tar.gz'], TOOLS.record((output / 'tools.tar.gz').read_bytes()))
                self.assertEqual(set(manifest['recipes']), set(TOOLS.SOURCES) | {'build/maintenance-tools.py'})
                self.assertIn('package.json', manifest['files'])
                with self.assertRaises(ValueError):
                    TOOLS.build(output)
                with patch.object(TOOLS, 'publish', side_effect=OSError('fixture failure')):
                    with self.assertRaises(OSError):
                        TOOLS.build(root / 'out/maintenance-tools-failed')
                self.assertEqual(list((root / 'out').iterdir()), [output])

    def test_publication_race_never_replaces_existing_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, target = root / 'source', root / 'target'
            source.mkdir()
            target.mkdir()
            (target / 'sentinel').write_bytes(b'preserve')
            with self.assertRaises(FileExistsError):
                fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    TOOLS.publish(fd, source.name, target.name)
                finally:
                    os.close(fd)
            self.assertEqual((target / 'sentinel').read_bytes(), b'preserve')
            self.assertTrue(source.is_dir())

    def test_unsafe_backing_storage_is_checked_before_any_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            (root / 'build/host-state.py').write_text(
                "def validate_workspace_storage(path):\n    raise ValueError('unsafe backing storage')\n")
            with patch.object(TOOLS, 'REPO', root), self.assertRaisesRegex(ValueError, 'unsafe backing'):
                TOOLS.build(root / 'out/maintenance-tools-refused')
            self.assertFalse((root / 'out').exists())

    def test_all_absolute_ancestors_must_be_trusted(self):
        with patch.object(TOOLS, 'check_directory', side_effect=self.strict_check):
            with self.assertRaisesRegex(ValueError, 'ancestors'):
                TOOLS.open_trusted_directory(Path('/tmp'))
        with tempfile.TemporaryDirectory() as temporary:
            ancestor = Path(temporary)
            root = ancestor / 'repo'
            root.mkdir()
            self.fixture(root)
            ancestor.chmod(0o777)
            with patch.object(TOOLS, 'REPO', root), self.assertRaisesRegex(ValueError, 'ancestors'):
                TOOLS.build(root / 'out/maintenance-tools-refused')
            ancestor.chmod(0o700)
            self.assertFalse((root / 'out').exists())

    def test_parent_is_held_during_storage_checks_and_publication(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            held = []
            def storage(fd):
                self.assertTrue(stat.S_ISDIR(os.fstat(fd).st_mode))
                held.append(TOOLS.inode(os.fstat(fd)))
            real_publish = TOOLS.publish
            def publication(fd, source, destination):
                self.assertEqual(TOOLS.inode(os.fstat(fd)), held[-1])
                return real_publish(fd, source, destination)
            with patch.object(TOOLS, 'REPO', root), patch.object(TOOLS, 'validate_storage', side_effect=storage), \
                    patch.object(TOOLS, 'publish', side_effect=publication):
                TOOLS.build(root / 'out/maintenance-tools-pinned')
            self.assertEqual(len(held), 2)

    def test_parent_replacement_never_redirects_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            (root / 'out').mkdir(mode=0o700)
            foreign = root / 'foreign'
            foreign.mkdir()
            real_archive = TOOLS.archive
            def replace(values):
                (root / 'out').rename(root / 'old-out')
                (root / 'out').symlink_to(foreign, target_is_directory=True)
                return real_archive(values)
            with patch.object(TOOLS, 'REPO', root), patch.object(TOOLS, 'archive', side_effect=replace):
                with self.assertRaises((ValueError, OSError)):
                    TOOLS.build(root / 'out/maintenance-tools-pinned')
            self.assertEqual(list(foreign.iterdir()), [])
            self.assertEqual(list((root / 'old-out').iterdir()), [])

    def test_cleanup_preserves_replaced_symlinks_and_foreign_objects(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            stage = TOOLS.Stage(fd)
            try:
                stage.write('tree/owned', b'owned', 0o444)
                tree = root / stage.name / 'tree'
                tree.rename(root / stage.name / 'moved')
                foreign = root / 'foreign'
                foreign.mkdir(mode=0o700)
                (foreign / 'sentinel').write_bytes(b'preserve')
                foreign.chmod(0o555)
                tree.symlink_to(foreign, target_is_directory=True)
                # Untracked child of an owned directory is preserved as well.
                (root / stage.name / 'untracked').write_bytes(b'foreign')
                stage.cleanup()
                self.assertTrue(tree.is_symlink())
                self.assertEqual((foreign / 'sentinel').read_bytes(), b'preserve')
                self.assertEqual(stat.S_IMODE(foreign.stat().st_mode), 0o555)
                self.assertEqual((root / stage.name / 'untracked').read_bytes(), b'foreign')
            finally:
                stage.close()
                os.close(fd)

    def test_output_restrictions(self):
        for value in ('out/maintenance-tools-relative', '/tmp/maintenance-tools-outside', str(REPO / 'out/maintenance-tools-')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                TOOLS.validate_output(value)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            with patch.object(TOOLS, 'REPO', root):
                (root / 'out').symlink_to(root / 'build', target_is_directory=True)
                with self.assertRaises((ValueError, OSError)):
                    TOOLS.open_output_parent(root / 'out/maintenance-tools-new')
                (root / 'out').unlink()
                (root / 'out').mkdir(mode=0o777)
                (root / 'out').chmod(0o777)
                with self.assertRaisesRegex(ValueError, 'trusted'):
                    TOOLS.open_output_parent(root / 'out/maintenance-tools-new')


if __name__ == '__main__':
    unittest.main()
