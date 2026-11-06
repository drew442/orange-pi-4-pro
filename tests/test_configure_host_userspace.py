"""Ordinary-file guard tests; never run ARM setup, mounts or a host launcher."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

BASE = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


c = load('userspace_configure', BASE / 'build/configure-host-userspace.py')
h = load('configure_api', BASE / 'build/userspace-bootstrap-api.py')


class ConfigureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.work = Path(self.temp.name)
        self.root = self.work / 'arm-root'
        self.root.mkdir(mode=0o700)
        self.repo = self.work / 'repo'
        self.repo.mkdir()
        self.patches = [patch.object(c, 'WORK', self.work), patch.object(c, 'ROOT', self.root),
                        patch.object(c, 'REPO', self.repo), patch.object(c, 'CLONE_OWNER', os.getuid())]
        for item in self.patches:
            item.start()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(lambda: [item.stop() for item in reversed(self.patches)])

    def put(self, name, data=b''):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def test_import_has_no_actions(self):
        with patch('subprocess.run', side_effect=AssertionError('execution')), patch('os.chmod', side_effect=AssertionError('chmod')):
            imported = load('configure_no_action', BASE / 'build/configure-host-userspace.py')
        self.assertTrue(callable(imported.main))

    def test_cli_rejects_root_override(self):
        result = subprocess.run(['python3', '-I', '-B', str(BASE / 'build/configure-host-userspace.py'),
                                 '--root', str(self.root)], capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn(b'unrecognized arguments', result.stderr)

    def test_guards_remain_under_optimized_python(self):
        code = f"import runpy; m=runpy.run_path({str(BASE / 'build/configure-host-userspace.py')!r}); m['require'](False,'guard')"
        result = subprocess.run(['python3', '-O', '-c', code], capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'ValueError: guard', result.stderr)

    def test_real_pinned_locks(self):
        with patch.object(c, 'LOCK', BASE / 'configs/host-userspace-packages.lock.tsv'), \
             patch.object(c, 'HASH_LOCK', BASE / 'configs/host-userspace-package-hashes.lock.tsv'), \
             patch.object(c, 'readonly_input'):
            expected, hashes = c.locks()
        self.assertEqual(len(expected), 220)
        self.assertEqual(set(expected), set(hashes))

    def test_lock_mutation_rejected(self):
        lock = self.work / 'lock'
        lock.write_bytes(b'altered')
        with patch.object(c, 'LOCK', lock), patch.object(c, 'readonly_input'):
            with self.assertRaisesRegex(ValueError, 'lock pin'):
                c.locks()

    def test_read_rejects_symlink_hardlink_and_parent_alias(self):
        path = self.put('file', b'original')
        symlink = self.root / 'symlink'
        symlink.symlink_to(path)
        with self.assertRaisesRegex(ValueError, 'single regular'):
            c.read_checked(symlink)
        os.link(path, self.root / 'hardlink')
        with self.assertRaisesRegex(ValueError, 'single regular'):
            c.read_checked(path)
        (self.root / 'alias').symlink_to(self.repo, target_is_directory=True)
        (self.repo / 'file').write_bytes(b'outside')
        with self.assertRaisesRegex(ValueError, 'ancestor'):
            c.read_checked(self.root / 'alias/file')

    def test_atomic_clone_write_does_not_modify_hardlinks_or_symlinks(self):
        path = self.put('etc/file', b'original')
        hardlink = self.root / 'etc/alias'
        os.link(path, hardlink)
        with self.assertRaises(ValueError):
            c.write_regular(path, b'replacement')
        self.assertEqual(hardlink.read_bytes(), b'original')
        path.unlink()
        path.symlink_to(hardlink)
        with self.assertRaises(ValueError):
            c.write_regular(path, b'replacement')
        self.assertEqual(hardlink.read_bytes(), b'original')

    def test_write_creates_only_clone_directories(self):
        target = self.root / 'etc/systemd/system-preset/preset'
        c.write_regular(target, b'disable *\n', 0o644)
        self.assertEqual(target.read_bytes(), b'disable *\n')
        with self.assertRaisesRegex(ValueError, 'outside fixed clone'):
            c.write_regular(self.work / 'elsewhere', b'bad')
        outside = self.work / 'outside'
        outside.mkdir()
        (self.root / 'alias').symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'ancestor'):
            c.write_regular(self.root / 'alias/new/file', b'bad')
        self.assertFalse((outside / 'new').exists())

    def test_restrictions_reject_release_uidmap_nnp_and_each_drop(self):
        good = {'NoNewPrivs': '1', **{key: '0' for key in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb')}}
        api = types.SimpleNamespace(status=lambda: dict(good))
        with patch.object(c.platform, 'freedesktop_os_release', return_value={'ID': 'ubuntu', 'VERSION_ID': '22.04'}), \
             patch.object(c.platform, 'machine', return_value='x86_64'), \
             patch.object(c.Path, 'read_text', return_value='0 1048576 65536\n'), \
             patch.dict(os.environ, {}, clear=True), \
             patch.object(c.os, 'getuid', return_value=0), patch.object(c.os, 'geteuid', return_value=0), \
             patch.object(c.os, 'getgid', return_value=0), patch.object(c.os, 'getegid', return_value=0):
            self.assertEqual(c.restrictions(api), good)
            for key in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb'):
                with self.subTest(key=key), patch.object(api, 'status', return_value={**good, key: hex(c.DROP_MASK)}):
                    with self.assertRaisesRegex(ValueError, 'capabilities'):
                        c.restrictions(api)
            with patch.object(api, 'status', return_value={**good, 'NoNewPrivs': '0'}):
                with self.assertRaisesRegex(ValueError, 'NoNewPrivs'):
                    c.restrictions(api)
            with patch.object(c.Path, 'read_text', return_value='0 0 4294967295'):
                with self.assertRaisesRegex(ValueError, 'map'):
                    c.restrictions(api)
            with patch.object(c.platform, 'machine', return_value='aarch64'):
                with self.assertRaisesRegex(ValueError, 'amd64'):
                    c.restrictions(api)

    def test_profile_rejects_nonce_and_initial_user_namespace(self):
        api = types.SimpleNamespace(namespaces=lambda: {'user': 2})
        profile = dict(nonce='a'*64, repo_manifest_sha256='b'*64, host_userns_inode=1, host_driver_pid=42)
        self.assertEqual(c.validate_profile(profile, api), {'user': 2})
        for changed in ({'nonce': 'x'}, {'host_userns_inode': 2}, {'host_driver_pid': True}, {'extra': 1}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                c.validate_profile({**profile, **changed}, api)

    def test_preexisting_ready_rejected_without_mutation(self):
        (self.work / 'api-ready.json').symlink_to('/nonexistent')
        api = types.SimpleNamespace(under=lambda records, path: [], mounts=lambda: [])
        with patch.object(c, 'restrictions', return_value={}), patch.object(c, 'readonly_input') as authenticated:
            with self.assertRaisesRegex(ValueError, 'output already exists'):
                c.preflight(api)
            authenticated.assert_not_called()
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
        self.assertFalse((self.work / 'configure-receipt.json').exists())

    def test_foreign_receipt_authentication_and_failure_precede_mutation(self):
        profile_path = self.work / 'profile'
        profile = dict(nonce='a'*64, repo_manifest_sha256='b'*64, host_userns_inode=1, host_driver_pid=42)
        inputs = {'foreign_receipt_sha256': '', 'adapter_manifest_sha256': 'c'*64}
        def authenticated(path, expected=None, owner=None):
            if path == profile_path:
                return profile, 'profile-digest'
            if path == c.INPUTS:
                return inputs, 'input-digest'
            return h.authenticated(path, expected, owner)
        api = types.SimpleNamespace(PROFILE=profile_path, authenticated=authenticated,
                                    namespaces=lambda: {'user': 2}, under=lambda records, path: [], mounts=lambda: [])
        for changes in ({'success': False}, {'dpkg_status_bytes': 1}, {'schema': 2}, {'sha': '0'*64}):
            foreign = {'schema': 1, 'success': True, 'dpkg_status_bytes': 0}
            foreign.update({k: v for k, v in changes.items() if k != 'sha'})
            data = json.dumps(foreign).encode()
            (self.work / 'foreign-receipt.json').write_bytes(data)
            inputs['foreign_receipt_sha256'] = changes.get('sha', hashlib.sha256(data).hexdigest())
            with self.subTest(changes=changes), patch.object(c, 'restrictions', return_value={}), \
                 patch.object(c, 'readonly_input'), patch.object(c, 'locks') as locks:
                with self.assertRaises(ValueError):
                    c.preflight(api)
                locks.assert_not_called()
            self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
            self.assertFalse((self.work / 'configure-receipt.json').exists())

    def test_inventory_rejects_duplicate_and_partial_configuration(self):
        self.assertEqual(c.inventory('pkg\t1\tarm64\tinstalled\n'), {'pkg': ('1', 'arm64')})
        for text in ('pkg\t1\tarm64\tunpacked\n', 'pkg\t1\tarm64\tinstalled\n'*2):
            with self.assertRaises(ValueError):
                c.inventory(text)

    def test_arm_failure_and_timeout_are_never_success(self):
        report = {'commands': []}
        api = types.SimpleNamespace(write_new=h.write_new)
        with patch.object(c.subprocess, 'run', return_value=types.SimpleNamespace(returncode=3)):
            with self.assertRaisesRegex(ValueError, 'ARM command failed'):
                c.arm(api, report, ['/bin/false'], 'apt-check')
        self.assertEqual(report['commands'][0]['status'], 3)
        with patch.object(c.subprocess, 'run', side_effect=subprocess.TimeoutExpired('fixture', 1)):
            with self.assertRaises(subprocess.TimeoutExpired):
                c.arm(api, report, ['/bin/false'], 'apt-update', timeout=1)
        self.assertNotIn('status', report['commands'][1])
        with self.assertRaisesRegex(ValueError, 'unreviewed'):
            c.arm(api, report, [], '../escape')

    def test_handshake_rejects_forgery_before_arm(self):
        profile = dict(nonce='a'*64, host_userns_inode=1, host_driver_pid=42, repo_manifest_sha256='b'*64)
        (self.work / 'api-continue.json').write_text('{}')
        api = Mock()
        api.authenticated.return_value = ({'nonce': 'wrong', 'helper_status': 0, 'receipt_sha256': 'c'*64}, 'digest')
        with self.assertRaisesRegex(ValueError, 'continuation'):
            c.handshake(api, profile, {}, {'user': 2})
        api.authenticated.assert_called_once_with(self.work / 'api-continue.json', owner=65534)
        api.check_api.assert_not_called()

    def test_helper_timeout(self):
        api = types.SimpleNamespace(write_new=h.write_new)
        with self.assertRaisesRegex(ValueError, 'helper timeout'):
            c.handshake(api, {'nonce': 'a'*64}, {}, {}, timeout=0)

    def test_failure_receipt_after_validated_clone(self):
        api = types.SimpleNamespace(write_new=h.write_new)
        state = ({'nonce': 'a'*64}, {}, {}, {}, b'fixture')
        with patch.object(c, 'load_helper', return_value=api), patch.object(c, 'preflight', return_value=state), \
             patch.object(c, 'write_regular', side_effect=ValueError('injection failure')):
            with self.assertRaisesRegex(ValueError, 'injection failure'):
                c.configure()
        receipt = json.loads((self.work / 'configure-receipt.json').read_text())
        self.assertFalse(receipt['success'])
        self.assertIn('injection failure', receipt['error'])
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o755)

    def test_preflight_failure_leaves_clone_and_receipt_absent(self):
        with patch.object(c, 'load_helper', return_value=object()), patch.object(c, 'preflight', side_effect=ValueError('bad source')):
            with self.assertRaisesRegex(ValueError, 'bad source'):
                c.configure()
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
        self.assertFalse((self.work / 'configure-receipt.json').exists())

    def adapter_fixture(self):
        adapter = self.work / 'adapter'
        (adapter / 'scripts').mkdir(parents=True)
        (adapter / 'scripts/bookworm').symlink_to('sid')
        sources = {}
        for source, output, copied in (
                ('usr/sbin/debootstrap', 'debootstrap', 'debootstrap'),
                ('usr/share/debootstrap/functions', 'functions', 'functions'),
                ('usr/share/debootstrap/scripts/sid', 'scripts/sid', 'suite-script'),
                ('usr/share/debootstrap/scripts/debian-common', 'scripts/debian-common', 'debian-common')):
            data = output.encode()
            (adapter / output).write_bytes(data)
            self.put('debootstrap/' + copied, data)
            sources[source] = dict(output=output, output_bytes=len(data), output_sha256=hashlib.sha256(data).hexdigest())
        manifest = dict(version='1.0.126+nmu1ubuntu0.9', sources=sources, bookworm_symlink='sid')
        path = adapter / 'manifest.json'
        path.write_text(json.dumps(manifest))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return adapter, digest, {'adapter_manifest': {'sha256': digest}}

    def test_adapter_authenticates_all_code_and_rejects_extras(self):
        adapter, digest, foreign = self.adapter_fixture()
        c.validate_adapter(h, digest, foreign)
        self.put('debootstrap/suite-script', b'changed')
        with self.assertRaisesRegex(ValueError, 'cloned bootstrap'):
            c.validate_adapter(h, digest, foreign)
        self.put('debootstrap/suite-script', b'scripts/sid')
        (adapter / 'extra').write_bytes(b'unknown')
        with self.assertRaisesRegex(ValueError, 'file set'):
            c.validate_adapter(h, digest, foreign)

    def test_adapter_receipt_mismatch_rejected(self):
        _, digest, foreign = self.adapter_fixture()
        with self.assertRaisesRegex(ValueError, 'authentication'):
            c.validate_adapter(h, '0'*64, foreign)
        with self.assertRaisesRegex(ValueError, 'foreign adapter'):
            c.validate_adapter(h, digest, {'adapter_manifest': {'sha256': '0'*64}})

    def run_configuration_fixture(self, daemon=False, bad_inventory=False, bad_audit=False):
        expected = {f'pkg{i:03}': ('1', 'arm64') for i in range(220)}
        ns, status = {'user': 2}, {'NoNewPrivs': '1'}
        profile = {'nonce': 'a'*64, 'repo_manifest_sha256': 'b'*64}
        api = types.SimpleNamespace(write_new=h.write_new, namespaces=lambda: ns, check_repository=Mock())
        (self.repo / 'verified-keyring.gpg').write_bytes(b'keyring')
        (self.root / 'etc/apt/sources.list.d').mkdir(parents=True)
        calls = []
        def fake_arm(helper, report, args, name, timeout=1200, env_extra=None):
            calls.append((name, args))
            self.assertEqual(self.root.stat().st_mode & 0o777, 0o755)
            if name in ('apt-update', 'apt-install'):
                self.assertEqual((self.root / 'usr/sbin/policy-rc.d').read_bytes(), c.POLICY)
                self.assertEqual((self.root / 'etc/systemd/system-preset/00-prototype.preset').read_bytes(), c.PRESET)
                self.assertEqual((self.root / 'etc/apt/sources.list').read_bytes(), c.SOURCES)
            if name == 'second-stage':
                (self.root / 'usr/sbin/policy-rc.d').unlink()
                return ''
            if name in ('base-inventory', 'full-inventory'):
                count = 99 if name == 'base-inventory' else 219 if bad_inventory else 220
                return ''.join(f'pkg{i:03}\t1\tarm64\tinstalled\n' for i in range(count))
            if name == 'dpkg-audit' and bad_audit:
                return 'unconfigured package'
            return ''
        original_iterdir = c.Path.iterdir
        def iterdir(path):
            if path == Path('/proc'):
                return iter([Path('/proc/1'), Path('/proc/' + str(os.getpid()))] + ([Path('/proc/99999999')] if daemon else []))
            return original_iterdir(path)
        with patch.object(c, 'load_helper', return_value=api), \
             patch.object(c, 'preflight', return_value=(profile, status, ns, expected, b'qemu')), \
             patch.object(c, 'sha', return_value=c.QEMU_SHA256), \
             patch.object(c, 'handshake', return_value=({}, 'c'*64)), \
             patch.object(c, 'restrictions', return_value=status), \
             patch.object(c, 'arm', side_effect=fake_arm), patch.object(c.Path, 'iterdir', iterdir):
            result = c.configure()
        return result, calls

    def test_orchestration_preserves_commands_offline_policy_and_receipt(self):
        result, calls = self.run_configuration_fixture()
        self.assertTrue(result['success'])
        self.assertEqual(result['configured_packages'], 220)
        self.assertEqual([name for name, _ in calls], list(c.COMMAND_NAMES))
        install = dict(calls)['apt-install']
        self.assertEqual(install[:4], ['/usr/bin/apt-get', '--yes', '--no-install-recommends', 'install'])
        self.assertEqual(len(install[4:]), 220)
        self.assertTrue(all(spec.endswith('=1') for spec in install[4:]))
        receipt = json.loads((self.work / 'configure-receipt.json').read_text())
        for field in ('inventory_match', 'dpkg_audit_clean', 'apt_check_passed'):
            self.assertIs(receipt[field], True)

    def test_live_daemon_cannot_publish_success(self):
        with self.assertRaisesRegex(ValueError, 'daemon/helper'):
            self.run_configuration_fixture(daemon=True)
        self.assertFalse(json.loads((self.work / 'configure-receipt.json').read_text())['success'])

    def test_incomplete_inventory_cannot_publish_success(self):
        with self.assertRaisesRegex(ValueError, 'full inventory'):
            self.run_configuration_fixture(bad_inventory=True)
        self.assertFalse(json.loads((self.work / 'configure-receipt.json').read_text())['success'])

    def test_dirty_audit_cannot_publish_success(self):
        with self.assertRaisesRegex(ValueError, 'audit not empty'):
            self.run_configuration_fixture(bad_audit=True)
        self.assertFalse(json.loads((self.work / 'configure-receipt.json').read_text())['success'])


if __name__ == '__main__':
    unittest.main()
