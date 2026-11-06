"""Foreign DATA recipe guard tests; no namespace, extraction or ARM execution."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('prepare_userspace', BASE / 'build/prepare-host-userspace.py')
p = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(p)


class PrepareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.work = self.base / 'work'
        self.work.mkdir()
        self.project = self.base / 'project'
        self.project.mkdir()
        self.root = self.work / 'arm-root'
        for name, value in [('WORK', self.work), ('ROOT', self.root), ('PROJECT', self.project),
                            ('SOURCE_MANIFEST', self.base / 'manifest.json'), ('NAMESPACE_OWNER', os.getuid())]:
            patcher = patch.object(p, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def put(self, name, content=b'', parent=None):
        path = (parent or self.root) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def manifest(self):
        files = {}
        for name in p.SOURCE_FILES:
            path = self.put(name, b'source', self.project)
            files[name] = dict(p.record(path), git_blob='a' * 40)
        value = {'revision': 'b' * 40, 'files': files}
        p.SOURCE_MANIFEST.write_text(json.dumps(value))
        return value

    def output(self):
        self.root.mkdir(mode=0o700)
        self.put('debootstrap/functions', b'pinned')
        self.put('functions', b'pinned', self.work / 'adapter')
        self.put('debootstrap/arch', b'arm64\n')
        self.put('debootstrap/suite', b'bookworm\n')
        self.put('debootstrap/required', b'base')
        self.put('debootstrap/base', b'base extra')
        self.put('var/lib/dpkg/status')
        self.put('configs/host-userspace-packages.lock.tsv', b'base\t1\tarm64\nextra\t1\tall\n', self.project)
        self.put('var/cache/apt/archives/base.deb', b'binary')
        for name in ('null', 'zero', 'full', 'random', 'urandom', 'tty', 'console', 'ptmx'):
            self.put('dev/' + name).chmod(0o666)
        for name in ('proc', 'sys', 'dev/pts', 'dev/shm'):
            (self.root / name).mkdir()
        for name, target in {'fd': '/proc/self/fd', 'stdin': '/proc/self/fd/0',
                             'stdout': '/proc/self/fd/1', 'stderr': '/proc/self/fd/2'}.items():
            (self.root / 'dev' / name).symlink_to(target)
        adapter = types.SimpleNamespace(DEVICES={name: (1, 3) for name in ('null', 'zero', 'full', 'random', 'urandom', 'tty', 'console', 'ptmx')})
        manifest = {'packages': [{'SHA256': hashlib.sha256(b'binary').hexdigest(), 'Size': 6}]}
        return adapter, manifest

    def test_import_has_no_actions(self):
        with patch('subprocess.run', side_effect=AssertionError('execution')), patch('os.open', side_effect=AssertionError('open')):
            spec = importlib.util.spec_from_file_location('passive_prepare', BASE / 'build/prepare-host-userspace.py')
            spec.loader.exec_module(importlib.util.module_from_spec(spec))

    def test_cli_rejects_path_overrides(self):
        for option in ('--root', '--work', '--cache', '--project'):
            result = subprocess.run(['python3', '-I', '-B', str(BASE / 'build/prepare-host-userspace.py'), option, '/tmp'], capture_output=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn(b'unrecognized arguments', result.stderr)

    def test_optimized_python_keeps_guards(self):
        code = "import runpy; runpy.run_path(%r)['require'](False,'rejected')" % str(BASE / 'build/prepare-host-userspace.py')
        result = subprocess.run(['python3', '-O', '-c', code], capture_output=True)
        self.assertIn(b'ValueError: rejected', result.stderr)

    def restricted(self, env=None, status_changes=None, map_value='0 1048576 65536'):
        fields = {'Uid': '0 0 0 0', 'Gid': '0 0 0 0', 'NoNewPrivs': '1', 'NSpid': '2'}
        fields.update({key: '0000000000000000' for key in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb')})
        fields.update(status_changes or {})
        def read(path):
            if path.name in ('uid_map', 'gid_map'):
                return map_value
            if path.name == 'status':
                return '\n'.join(key + ':\t' + value for key, value in fields.items())
            raise AssertionError(str(path))
        with patch.object(p.platform, 'freedesktop_os_release', return_value={'ID': 'ubuntu', 'VERSION_ID': '22.04'}), \
             patch.object(p.platform, 'machine', return_value='x86_64'), \
             patch.object(p.os, 'getuid', return_value=0), patch.object(p.os, 'geteuid', return_value=0), \
             patch.object(p.os, 'getgid', return_value=0), patch.object(p.os, 'getegid', return_value=0), \
             patch.object(Path, 'read_text', read), patch.dict(os.environ, env or {}, clear=True):
            return p.restrictions()

    def test_namespace_restrictions_and_benign_environment(self):
        result = self.restricted({'PWD': '/', 'container': 'systemd-nspawn', 'SHLVL': '1'})
        self.assertEqual(result['NoNewPrivs'], '1')
        with self.assertRaisesRegex(ValueError, 'private UID/GID'):
            self.restricted(map_value='0 0 4294967295')
        with self.assertRaisesRegex(ValueError, 'NoNewPrivs'):
            self.restricted(status_changes={'NoNewPrivs': '0'})
        for field in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb'):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'dangerous capabilities'):
                self.restricted(status_changes={field: '0000000002000000'})

    def test_loader_fakeroot_and_bootstrap_environment_rejected(self):
        for key in ('LD_PRELOAD', 'LD_LIBRARY_PATH', 'PYTHONPATH', 'PYTHONHOME',
                    'FAKEROOTKEY', 'FAKECHROOT', 'DEBOOTSTRAP_DIR'):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'environment'):
                self.restricted({key: ''})

    def test_record_rejects_symlink_hardlink_and_ancestor(self):
        path = self.put('file', b'data')
        link = self.root / 'link'
        link.symlink_to(path)
        with self.assertRaises(ValueError):
            p.record(link)
        os.link(path, self.root / 'hard')
        with self.assertRaises(ValueError):
            p.record(path)
        alias = self.work / 'alias'
        alias.symlink_to(self.root)
        with self.assertRaises(ValueError):
            p.record(alias / 'file')

    def test_read_bound(self):
        path = self.put('large', b'12345')
        with self.assertRaisesRegex(ValueError, 'bound'):
            p.read_checked(path, 4)

    def test_manifest_authenticates_all_entries(self):
        value = self.manifest()
        extra = self.put('build/extra.py', b'extra', self.project)
        value['files']['build/extra.py'] = dict(p.record(extra), git_blob='c' * 40)
        p.SOURCE_MANIFEST.write_text(json.dumps(value))
        with patch.object(p, 'readonly'):
            self.assertEqual(len(p.sources()), 6)
            extra.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'authentication'):
                p.sources()

    def test_hidden_bytecode_rejected_before_import_or_output(self):
        self.manifest()
        self.put('build/__pycache__/offline-userspace-repo.cpython-310.pyc',
                 b'untrusted bytecode', self.project)
        # Keep real source authentication and no-write preflight orchestration.
        def preflight():
            return {}, p.sources()
        with patch.object(p, 'readonly'), patch.object(p, 'preflight', side_effect=preflight), \
             patch.object(p, 'load', side_effect=AssertionError('must reject before import')) as load:
            with self.assertRaisesRegex(ValueError, 'undeclared project paths'):
                p.prepare()
        load.assert_not_called()
        self.assertEqual(list(self.work.iterdir()), [])

    def test_undeclared_empty_directory_and_symlink_directory_rejected(self):
        self.manifest()
        hidden = self.project / '.hidden'
        hidden.mkdir()
        with patch.object(p, 'readonly'), self.assertRaisesRegex(ValueError, 'undeclared'):
            p.sources()
        hidden.rmdir()
        hidden.symlink_to(self.work, target_is_directory=True)
        with patch.object(p, 'readonly'), self.assertRaisesRegex(ValueError, 'ancestor'):
            p.sources()

    def test_manifest_rejects_malformed_entries(self):
        for name in ('../escape', '/absolute', 'a//b', 'a/./b', 'a\\b'):
            with self.subTest(name=name):
                value = self.manifest()
                value['files'][name] = next(iter(value['files'].values()))
                p.SOURCE_MANIFEST.write_text(json.dumps(value))
                with patch.object(p, 'readonly'), self.assertRaisesRegex(ValueError, 'unsafe'):
                    p.sources()
        for mutate in (lambda v: v.update(revision='bad'),
                       lambda v: v['files'].pop(p.SOURCE_FILES[0]),
                       lambda v: v['files'][p.SOURCE_FILES[0]].update(bytes=True),
                       lambda v: v['files'][p.SOURCE_FILES[0]].update(git_blob='bad')):
            value = self.manifest()
            mutate(value)
            p.SOURCE_MANIFEST.write_text(json.dumps(value))
            with patch.object(p, 'readonly'), self.assertRaises((ValueError, KeyError)):
                p.sources()

    def test_existing_outputs_survive(self):
        path = self.work / 'receipt'
        path.write_text('old')
        with self.assertRaises(OSError):
            p.write_new(path, {'success': True})
        self.assertEqual(path.read_text(), 'old')
        target = self.work / 'target'
        target.write_text('target')
        link = self.work / 'link'
        link.symlink_to(target)
        with self.assertRaises(OSError):
            p.write_new(link, {})
        self.assertEqual(target.read_text(), 'target')
        self.assertFalse(list(self.work.glob('.foreign-*')))

    def test_preflight_failure_writes_nothing(self):
        with patch.object(p, 'preflight', side_effect=ValueError('source rejected')):
            with self.assertRaises(ValueError):
                p.prepare()
        self.assertEqual(list(self.work.iterdir()), [])

    def test_work_requires_empty_directory(self):
        (self.work / 'old').write_text('retained')
        with patch.object(p, 'restrictions', return_value={}), self.assertRaisesRegex(ValueError, 'empty'):
            p.preflight()
        self.assertEqual((self.work / 'old').read_text(), 'retained')

    def test_mount_alias_coordinates(self):
        topology = [(Path('/'), '8:1', Path('/'), 'ext4'),
                    (Path('/work'), '8:1', Path('/inputs/subtree'), 'ext4')]
        self.assertEqual(p.location(Path('/work/out'), topology), ('8:1', Path('/inputs/subtree/out'), 'ext4'))

    def test_foreign_command_controlled_and_failure_logs_published(self):
        observed = {}
        def run(argv, **kwargs):
            observed.update(argv=argv, **kwargs)
            kwargs['stdout'].write(b'data')
            return types.SimpleNamespace(returncode=3)
        report = {'commands': []}
        with patch.object(p.subprocess, 'run', side_effect=run):
            with self.assertRaisesRegex(ValueError, 'failed'):
                p.foreign(report)
        self.assertEqual((self.work / 'foreign.stdout').read_bytes(), b'data')
        self.assertEqual(observed['timeout'], 600)
        self.assertIn('--foreign', observed['argv'])
        self.assertIn('--force-check-gpg', observed['argv'])
        self.assertIn('--include=ca-certificates', observed['argv'])
        self.assertNotIn('LD_PRELOAD', observed['env'])
        self.assertEqual(report['commands'][0]['status'], 3)

    def test_timeout_publishes_logs(self):
        with patch.object(p.subprocess, 'run', side_effect=subprocess.TimeoutExpired('foreign', 600)):
            with self.assertRaises(subprocess.TimeoutExpired):
                p.foreign({'commands': []})
        self.assertTrue((self.work / 'foreign.stderr').is_file())

    def test_output_success(self):
        adapter, manifest = self.output()
        with patch.object(p, 'mounts', return_value=[]):
            devices, packages, count = p.validate_output(adapter, manifest)
        self.assertEqual(len(devices), 8)
        self.assertEqual(count, 1)
        self.assertEqual(packages['base'], ['base', 'extra'])

    def test_output_rejects_configured_status_and_wrong_arch(self):
        adapter, manifest = self.output()
        for name, content, message in [('var/lib/dpkg/status', b'Package: base', 'configured'),
                                       ('debootstrap/arch', b'amd64', 'architecture')]:
            path = self.root / name
            original = path.read_bytes()
            path.write_bytes(content)
            with self.assertRaisesRegex(ValueError, message):
                p.validate_output(adapter, manifest)
            path.write_bytes(original)

    def test_placeholder_mutations_rejected(self):
        adapter, manifest = self.output()
        path = self.root / 'dev/null'
        for mutation in ('mode', 'content', 'symlink'):
            path.unlink()
            path.write_bytes(b'')
            path.chmod(0o666)
            if mutation == 'mode':
                path.chmod(0o644)
            elif mutation == 'content':
                path.write_bytes(b'x')
            else:
                path.unlink()
                path.symlink_to(self.root / 'dev/zero')
            with self.assertRaises(ValueError):
                p.validate_output(adapter, manifest)

    def test_output_rejects_mount_package_and_cache_mutations(self):
        adapter, manifest = self.output()
        with patch.object(p, 'mounts', return_value=[(self.root / 'proc', '', Path('/'), 'proc')]):
            with self.assertRaisesRegex(ValueError, 'mounts'):
                p.validate_output(adapter, manifest)
        with patch.object(p, 'mounts', return_value=[]):
            self.put('debootstrap/base', b'unlocked')
            with self.assertRaisesRegex(ValueError, 'package sets'):
                p.validate_output(adapter, manifest)
            self.put('debootstrap/base', b'base')
            self.put('var/cache/apt/archives/base.deb', b'changed')
            with self.assertRaisesRegex(ValueError, 'cached deb'):
                p.validate_output(adapter, manifest)

    def test_mocked_orchestration_receipt_contract(self):
        repo = self.work / 'repo'
        tree = self.work / 'adapter'
        manifest = {'schema': 1, 'status': 'verified', 'package_count': 220,
                    'packages': [{}] * 220, 'bom_sha256': 'a' * 64}
        def construct(*args):
            self.put('manifest.json', b'repository', repo)
            return manifest
        def adapter_prepare(*args):
            self.put('manifest.json', b'adapter', tree)
        modules = [types.SimpleNamespace(construct=construct), types.SimpleNamespace(prepare=adapter_prepare)]
        with patch.object(p, 'preflight', return_value=({}, {})), patch.object(p, 'load', side_effect=modules), \
             patch.object(p, 'check_repository'), patch.object(p, 'foreign'), \
             patch.object(p, 'validate_output', return_value=({}, {'required': ['base']}, 99)), \
             patch.object(p, 'sources', return_value={}), patch.object(p, 'restrictions', return_value={}):
            result = p.prepare()
        receipt = json.loads((self.work / 'foreign-receipt.json').read_bytes())
        self.assertEqual(receipt, result)
        self.assertTrue(receipt['success'])
        self.assertEqual(receipt['dpkg_status_bytes'], 0)
        self.assertEqual(receipt['repository']['manifest'], p.record(repo / 'manifest.json'))
        self.assertEqual(receipt['adapter_manifest'], p.record(tree / 'manifest.json'))
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)

    def test_post_preflight_failure_receipt(self):
        with patch.object(p, 'preflight', return_value=({}, {})), patch.object(p, 'load', side_effect=ValueError('failed import')):
            with self.assertRaisesRegex(ValueError, 'failed import'):
                p.prepare()
        receipt = json.loads((self.work / 'foreign-receipt.json').read_bytes())
        self.assertFalse(receipt['success'])
        self.assertIn('failed import', receipt['error'])


if __name__ == '__main__':
    unittest.main()
