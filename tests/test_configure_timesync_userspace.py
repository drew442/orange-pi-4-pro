"""Host-only safety regressions. ARM commands and namespace setup are mocked."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('configure_timesync', PROJECT / 'build/configure-timesync-userspace.py')
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)
PREFLIGHT = m.preflight
ENVIRONMENT = m.environment
VALIDATE_SERVICE = m.validate_service
ENABLE_SERVICE_LINKS = m.enable_service_links


class Locks(unittest.TestCase):
    def test_real_pinned_relation(self):
        contract = json.loads((PROJECT / m.CONTRACT).read_text())
        with patch.object(m, 'PROJECT', PROJECT), patch.object(m, 'readonly'):
            old, new, hashes = m.validate_locks(contract)
        self.assertEqual((len(old), len(new), len(hashes)), (220, 221, 220))
        self.assertEqual(set(new) - set(old), {'systemd-timesyncd'})

    def test_contract_pin(self):
        data = (PROJECT / m.CONTRACT).read_bytes()
        self.assertEqual({'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}, m.CONTRACT_PIN)

    def test_bad_lock_records(self):
        for data, hashes in [(b'a\t1\tamd64\n', False), (b'a\t1\tall\na\t2\tall\n', False),
                             (b'a\t1\t' + b'0' * 64 + b'  debs/../a.deb\n', True),
                             (b'a\t1\t' + b'0' * 64 + b'  /tmp/a.deb\n', True),
                             (b'a\tbad version\tall\n', False)]:
            with self.subTest(data=data), self.assertRaises(ValueError):
                m.parse_lock(data, hashes)

    def test_relation_rejects_old_changes(self):
        contract = json.loads((PROJECT / m.CONTRACT).read_text())
        original = m.pinned
        def read(path, pin):
            data = original(path, pin)
            return data.replace(b'adduser\t3.134', b'adduser\t3.135') if path.name == 'host-timesync-packages.lock.tsv' else data
        with patch.object(m, 'PROJECT', PROJECT), patch.object(m, 'readonly'), patch.object(m, 'pinned', side_effect=read):
            with self.assertRaises(ValueError):
                m.validate_locks(contract)

    def test_status_rejects_malformed_and_duplicate(self):
        valid = b'Package: p\nVersion: 1\nArchitecture: arm64\nStatus: install ok installed\n'
        self.assertEqual(m.status_inventory(valid), {'p': ('1', 'arm64')})
        for data in (valid + b'\n' + valid, valid.replace(b'ok installed', b'ok unpacked'),
                     valid + b'Version: 2\n', b' malformed\n'):
            with self.subTest(data=data), self.assertRaises((ValueError, KeyError)):
                m.status_inventory(data)


class Sources(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.project = self.base / 'project'
        self.project.mkdir()
        self.manifest = self.base / 'manifest.json'
        self.patches = [patch.object(m, 'PROJECT', self.project), patch.object(m, 'SOURCE_MANIFEST', self.manifest),
                        patch.object(m, 'SOURCE_FILES', ('a.py',)), patch.object(m, 'readonly')]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        (self.project / 'a.py').write_bytes(b'pass\n')
        data = b'pass\n'
        self.value = {'revision': 'a' * 40, 'files': {'a.py': {'git_blob': 'b' * 40, 'bytes': len(data),
                                                           'sha256': hashlib.sha256(data).hexdigest()}}}
        self.publish()

    def publish(self):
        self.manifest.write_text(json.dumps(self.value))

    def test_closed_tree(self):
        self.assertEqual(set(m.sources()), {'a.py'})
        (self.project / '__pycache__').mkdir()
        with self.assertRaises(ValueError):
            m.sources()

    def test_tampering(self):
        (self.project / 'a.py').write_text('raise RuntimeError()\n')
        with self.assertRaises(ValueError):
            m.sources()

    def test_alias_manifest_paths(self):
        for name in ('../a.py', './a.py', 'dir//a.py', '/a.py', 'dir\\a.py'):
            self.value['files'] = {name: next(iter(self.value['files'].values()))}
            self.publish()
            with self.subTest(name=name), self.assertRaises(ValueError):
                m.sources()

    def test_symlink_rejected(self):
        (self.project / 'a.py').unlink()
        (self.project / 'a.py').symlink_to(self.manifest)
        with self.assertRaises(ValueError):
            m.sources()


class ServiceEnablement(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'arm-root'
        self.root.mkdir(mode=0o755)
        self.contract = json.loads((PROJECT / m.CONTRACT).read_text())
        self.link = self.root / next(iter(self.contract['service']['links']))
        self.target = self.contract['service']['links'][str(self.link.relative_to(self.root))]
        # Model mapped-root ownership without privileged chown. Preserve real
        # types, permissions and link counts for descriptor-based traversal.
        original_stat, original_fstat = os.stat, os.fstat
        original_lstat = Path.lstat
        def mapped(info):
            values = list(info)
            values[4] = values[5] = 0
            return os.stat_result(values)
        for p in (patch.object(m, 'ROOT', self.root),
                  patch.object(Path, 'lstat', lambda path: mapped(original_lstat(path))),
                  patch.object(m.os, 'stat', side_effect=lambda *a, **kw: mapped(original_stat(*a, **kw))),
                  patch.object(m.os, 'fstat', side_effect=lambda *a: mapped(original_fstat(*a)))):
            p.start()
            self.addCleanup(p.stop)

    def test_missing_parents_and_restrictive_umask(self):
        previous = os.umask(0o077)
        try:
            ENABLE_SERVICE_LINKS(self.contract)
        finally:
            os.umask(previous)
        for name, target in self.contract['service']['links'].items():
            path = self.root / name
            self.assertEqual(os.readlink(path), target)
            for parent in path.parents:
                if parent == self.root:
                    break
                self.assertEqual(parent.stat().st_mode & 0o7777, 0o755)
        self.assertEqual(set(p.name for p in self.link.parent.iterdir()), {'systemd-timesyncd.service'})

    def test_identical_links_preserve_identity(self):
        ENABLE_SERVICE_LINKS(self.contract)
        before = os.lstat(self.link)
        ENABLE_SERVICE_LINKS(self.contract)
        self.assertEqual(os.lstat(self.link), before)

    def test_conflicting_file_or_alias_is_unchanged(self):
        self.link.parent.mkdir(parents=True)
        for parent in self.link.parents:
            if parent == self.root:
                break
            parent.chmod(0o755)
        for target in (None, '/dev/null', '../other.service'):
            with self.subTest(target=target):
                if target is None:
                    self.link.write_bytes(b'keep')
                else:
                    self.link.symlink_to(target)
                before = os.lstat(self.link)
                with self.assertRaisesRegex(ValueError, 'conflicting'):
                    ENABLE_SERVICE_LINKS(self.contract)
                self.assertEqual(os.lstat(self.link), before)
                self.link.unlink()

    def test_symlink_parent_is_not_followed(self):
        outside = self.root.parent / 'outside'
        outside.mkdir()
        (self.root / 'etc').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(OSError):
            ENABLE_SERVICE_LINKS(self.contract)
        self.assertEqual(list(outside.iterdir()), [])

    def test_unsafe_parent_modes_and_owners_are_rejected(self):
        for parent in (self.root, self.link.parent):
            parent.mkdir(parents=True, exist_ok=True)
            for ancestor in parent.parents:
                if ancestor == self.root:
                    break
                if self.root in ancestor.parents:
                    ancestor.chmod(0o755)
            parent.chmod(0o777)
            with self.subTest(parent=parent), self.assertRaisesRegex(ValueError, 'owner/mode'):
                ENABLE_SERVICE_LINKS(self.contract)
            parent.chmod(0o755)
        original = os.fstat
        def nonroot(fd):
            values = list(original(fd))
            values[4] = values[5] = 1000
            return os.stat_result(values)
        with patch.object(m.os, 'fstat', side_effect=nonroot), self.assertRaisesRegex(ValueError, 'owner/mode'):
            ENABLE_SERVICE_LINKS(self.contract)

    def test_existing_link_metadata_is_required(self):
        ENABLE_SERVICE_LINKS(self.contract)
        original = os.stat
        for index, value in ((4, 1000), (5, 1000), (3, 2)):
            def changed(*args, **kwargs):
                values = list(original(*args, **kwargs))
                values[index] = value
                return os.stat_result(values)
            with self.subTest(index=index), patch.object(m.os, 'stat', side_effect=changed):
                with self.assertRaisesRegex(ValueError, 'conflicting'):
                    ENABLE_SERVICE_LINKS(self.contract)


class Derivation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.root = self.work / 'arm-root'
        (self.root / 'etc').mkdir(parents=True)
        for name in ('passwd', 'group'):
            (self.root / 'etc' / name).write_bytes(b'original\n')
        self.expected = {f'p{i}': ('1', 'arm64') for i in range(221)}
        self.status = '\n\n'.join(f'Package: {p}\nVersion: 1\nArchitecture: arm64\nStatus: install ok installed' for p in self.expected).encode()
        (self.root / 'var/lib/dpkg').mkdir(parents=True)
        (self.root / 'var/lib/dpkg/status').write_bytes(self.status)
        self.events = []
        def write(path, data, mode=0o644):
            self.events.append(str(path.relative_to(self.root)))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        def suppress():
            write(self.root / 'usr/sbin/policy-rc.d', b'exit101')
            write(self.root / 'etc/systemd/system-preset/00-prototype.preset', b'disable *')
        self.c = types.SimpleNamespace(write_regular=write, suppress_daemons=suppress, POLICY=b'exit101', PRESET=b'disable *',
                                      read_checked=lambda p, *a: p.read_bytes(), inventory=lambda text: self.expected,
                                      handshake=lambda *a: (self.events.append('api'), 'helper'))
        self.h = types.SimpleNamespace(write_new=lambda path, obj: path.write_text(json.dumps(obj)),
                                      namespaces=lambda: {'pid': 1}, check_repository=Mock())
        self.identity = types.SimpleNamespace(audit=Mock(return_value={'phase': 'after'}), compare_accounts=Mock(return_value=True))
        self.facts = dict(contract={}, profile={'repo_manifest_sha256': 'x'}, initial={}, ns={'pid': 1},
                          expected=self.expected, qemu=b'qemu', policy=b'fixed policy', deb=b'deb', identity_before={'phase': 'after'})
        original_iterdir = Path.iterdir
        def iterdir(path):
            return iter([Path('/proc/1')]) if path == Path('/proc') else original_iterdir(path)
        for p in (patch.object(m, 'WORK', self.work), patch.object(m, 'ROOT', self.root),
                  patch.object(m, 'SOURCE_MANIFEST', self.root / 'etc/passwd'),
                  patch.object(m, 'preflight', return_value=self.facts), patch.object(m, 'validate_service', return_value={'enabled': True}),
                  patch.object(m, 'enable_service_links', side_effect=lambda contract: self.events.append('enable-service')),
                  patch.object(m, 'environment', return_value={}),
                  patch.object(Path, 'iterdir', iterdir)):
            p.start()
            self.addCleanup(p.stop)

    def run_arm(self, h, report, args, name):
        self.events.append(name)
        report['commands'].append({'name': name, 'status': 0})
        return ''

    def test_policy_and_helper_before_package_execution(self):
        with patch.object(m, 'arm', side_effect=self.run_arm):
            result = m.derive(self.c, None, self.h, self.identity)
        self.assertTrue(result['success'])
        self.assertLess(self.events.index('etc/sysusers.d/systemd-timesync.conf'), self.events.index('timesync-install'))
        self.assertLess(self.events.index('api'), self.events.index('timesync-install'))
        self.assertEqual([x for x in self.events if x.startswith('timesync-')], list(m.COMMAND_NAMES))
        self.assertLess(self.events.index('timesync-check'), self.events.index('enable-service'))
        self.assertTrue((self.work / 'timesync-package-state.json').exists())
        self.assertFalse((self.work / 'configure-receipt.json').exists())
        self.identity.compare_accounts.assert_called_once_with(self.work / 'identity-before', self.root)

    def test_unsafe_preflight_has_no_failure_receipt(self):
        with patch.object(m, 'preflight', side_effect=ValueError('bad BOM')), self.assertRaises(ValueError):
            m.derive(self.c, None, self.h, self.identity)
        self.assertFalse((self.work / 'timesync-receipt.json').exists())
        self.assertEqual(self.events, [])

    def test_command_failure_records_no_success(self):
        def fail(h, report, args, name):
            report['commands'].append({'name': name, 'status': 1})
            raise ValueError('ARM failed')
        with patch.object(m, 'arm', side_effect=fail), self.assertRaises(ValueError):
            m.derive(self.c, None, self.h, self.identity)
        report = json.loads((self.work / 'timesync-receipt.json').read_text())
        self.assertFalse(report['success'])
        self.assertEqual(report['commands'][0]['status'], 1)
        self.assertFalse((self.work / 'timesync-package-state.json').exists())

    def test_account_change_prevents_success(self):
        self.identity.compare_accounts.return_value = False
        with patch.object(m, 'arm', side_effect=self.run_arm), self.assertRaises(ValueError):
            m.derive(self.c, None, self.h, self.identity)
        self.assertFalse(json.loads((self.work / 'timesync-receipt.json').read_text())['success'])

    def preflight_fixture(self):
        contract = json.loads((PROJECT / m.CONTRACT).read_text())
        old = {p: v for p, v in self.expected.items() if p != 'p220'}
        hashes = {p: ('1', ('0' * 64, 'debs/' + p + '.deb')) for p in old}
        base_status = '\n\n'.join(f'Package: {p}\nVersion: 1\nArchitecture: arm64\nStatus: install ok installed' for p in old).encode()
        for name in ('dev/pts', 'dev/shm', 'proc', 'sys'):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        self.h.DEVICES = {}
        self.h.ALIASES = {}
        self.h.placeholder = Mock()
        self.h.check_aliases = Mock()
        self.h.PROFILE = self.work / 'profile.json'
        self.h.mounts = lambda: []
        self.h.under = lambda *a: False
        manifest = {'packages': [dict(Package=p, Version='1', Architecture='arm64', SHA256='0' * 64) for p in old]}
        self.h.authenticated = lambda path, *a, **k: (manifest if path.name == 'manifest.json' else self.facts['profile'], 'hash')
        self.c.validate_profile = lambda *a: self.facts['ns']
        self.c.QEMU = self.work / 'qemu'
        self.c.QEMU_SHA256 = hashlib.sha256(b'qemu').hexdigest()
        self.c.clone_directory = Mock()
        self.c.read_checked = lambda path, *a: b'qemu' if path == self.c.QEMU else base_status
        source = dict(contract['source'], binary_packages=[p for p in contract['source']['binary_packages'] if p != 'systemd-timesyncd'])
        def pinned(path, pin):
            if path.name == Path(m.CONTRACT).name:
                return json.dumps(contract).encode()
            if path.name == 'host-timesync-sysusers.conf':
                return b'policy'
            if path.name == 'time-sync-package-input-20261106.json':
                return json.dumps({'package': {'SHA256': contract['addition']['sha256']}, 'package_bytes': contract['addition']['bytes']}).encode()
            if path.name == 'host-userspace-source-archives.lock.json':
                return json.dumps({'sources': [source]}).encode()
            return b'{}' if path == m.BOM else b'deb'
        control = b'Package: systemd-timesyncd\nVersion: 252.39-1~deb12u2\nArchitecture: arm64\nSource: systemd\n'
        e = types.SimpleNamespace(disk_backed=Mock(), scan=lambda root: ({}, {}), canonicalize=lambda records: records)
        patches = [patch.object(m, 'work_guard'), patch.object(m, 'physical_directory'), patch.object(m, 'readonly'),
                   patch.object(m, 'pinned', side_effect=pinned), patch.object(m, 'validate_locks', return_value=(old, self.expected, hashes)),
                   patch.object(m.shutil, 'disk_usage', return_value=types.SimpleNamespace(free=m.MIN_FREE)),
                   patch.object(m.subprocess, 'run', return_value=types.SimpleNamespace(stdout=control))]
        return e, patches

    def test_repository_becomes_readonly_after_helper(self):
        e, patches = self.preflight_fixture()
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        facts = PREFLIGHT(self.c, e, self.h, self.identity)
        self.assertEqual(facts['expected'], self.expected)
        self.h.check_repository.assert_called_once_with('x', path=m.REPO, owner=0)
        self.h.check_repository.reset_mock()
        self.c.read_checked = lambda path, *a: path.read_bytes()
        with patch.object(m, 'arm', side_effect=self.run_arm):
            m.derive(self.c, e, self.h, self.identity)
        self.h.check_repository.assert_called_once_with('x', path=m.REPO, readonly=True, owner=0)

    def test_capacity_floor_rejects_before_mutation(self):
        e, patches = self.preflight_fixture()
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        with patch.object(m.shutil, 'disk_usage', return_value=types.SimpleNamespace(free=m.MIN_FREE - 1)):
            with self.assertRaisesRegex(ValueError, '4 GiB'):
                PREFLIGHT(self.c, e, self.h, self.identity)
        self.assertEqual(self.events, [])
        self.assertFalse((self.work / 'timesync-receipt.json').exists())

    def test_bad_bom_and_overmount_reject_before_mutation(self):
        e, patches = self.preflight_fixture()
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        for label in ('bom', 'mount'):
            e.scan = lambda root: ({'unexpected': {}}, {}) if label == 'bom' else ({}, {})
            self.h.under = lambda *a: label == 'mount'
            with self.subTest(label=label), self.assertRaises(ValueError):
                PREFLIGHT(self.c, e, self.h, self.identity)
            self.assertEqual(self.events, [])
            self.assertFalse((self.work / 'timesync-receipt.json').exists())

    def test_unsafe_native_restrictions_reject(self):
        e, patches = self.preflight_fixture()
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.c.restrictions = Mock(side_effect=ValueError('CAP_SYS_TIME remains'))
        # Exercise environment itself rather than setUp's outer mock.
        with patch.object(m, 'environment', ENVIRONMENT), patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, 'CAP_SYS_TIME'):
                PREFLIGHT(self.c, e, self.h, self.identity)
        self.assertEqual(self.events, [])

    def test_final_audit_failure_never_publishes_success(self):
        def arm(h, report, args, name):
            self.run_arm(h, report, args, name)
            return 'broken package' if name == 'timesync-audit' else ''
        with patch.object(m, 'arm', side_effect=arm), self.assertRaisesRegex(ValueError, 'audit'):
            m.derive(self.c, None, self.h, self.identity)
        self.assertFalse(json.loads((self.work / 'timesync-receipt.json').read_text())['success'])
        self.assertNotIn('enable-service', self.events)

    def test_clock_state_prevents_success(self):
        def arm(h, report, args, name):
            self.run_arm(h, report, args, name)
            if name == 'timesync-check':
                (self.root / 'var/lib/systemd/timesync').mkdir(parents=True)
            return ''
        with patch.object(m, 'arm', side_effect=arm), self.assertRaisesRegex(ValueError, 'clock'):
            m.derive(self.c, None, self.h, self.identity)
        self.assertFalse(json.loads((self.work / 'timesync-receipt.json').read_text())['success'])

    def test_final_package_status_must_match(self):
        def arm(h, report, args, name):
            self.run_arm(h, report, args, name)
            if name == 'timesync-check':
                (self.root / 'var/lib/dpkg/status').write_bytes(self.status.replace(b'ok installed', b'ok unpacked', 1))
            return ''
        with patch.object(m, 'arm', side_effect=arm), self.assertRaisesRegex(ValueError, 'unconfigured'):
            m.derive(self.c, None, self.h, self.identity)
        self.assertFalse((self.work / 'timesync-package-state.json').exists())

    def test_extra_process_prevents_success(self):
        with patch.object(m, 'arm', side_effect=self.run_arm), \
                patch.object(Path, 'iterdir', return_value=iter([Path('/proc/1'), Path('/proc/987654')])), \
                self.assertRaisesRegex(ValueError, 'remains'):
            m.derive(self.c, None, self.h, self.identity)
        self.assertFalse(json.loads((self.work / 'timesync-receipt.json').read_text())['success'])

    def test_restriction_drift_prevents_success(self):
        with patch.object(m, 'arm', side_effect=self.run_arm), \
                patch.object(m, 'environment', return_value={'changed': True}), \
                self.assertRaisesRegex(ValueError, 'restrictions'):
            m.derive(self.c, None, self.h, self.identity)
        self.assertFalse(json.loads((self.work / 'timesync-receipt.json').read_text())['success'])

    def service_fixture(self):
        (self.root / 'usr/lib/systemd/system').mkdir(parents=True)
        (self.root / 'lib').symlink_to('usr/lib')
        path = self.root / 'usr/lib/systemd/system/systemd-timesyncd.service'
        path.write_bytes(b'unit')
        path.chmod(0o644)
        link = 'etc/systemd/system/sysinit.target.wants/systemd-timesyncd.service'
        (self.root / link).parent.mkdir(parents=True)
        (self.root / link).symlink_to('/lib/systemd/system/systemd-timesyncd.service')
        contract = {'service': {'enablement': 'required', 'links': {link: '/lib/systemd/system/systemd-timesyncd.service'},
                               'installed_files': {'usr/lib/systemd/system/systemd-timesyncd.service': {
                                   'bytes': 4, 'sha256': hashlib.sha256(b'unit').hexdigest(), 'mode': '0644'}}}}
        def lstat(path):
            value = list(os.lstat(path))
            value[4] = value[5] = 0  # Model mapped-root ownership without privileged chown.
            return os.stat_result(value)
        patcher = patch.object(Path, 'lstat', lstat)
        patcher.start()
        self.addCleanup(patcher.stop)
        return contract, self.root / link, path

    def test_service_gate_valid_and_wrong_targets(self):
        contract, link, unit = self.service_fixture()
        self.assertEqual(VALIDATE_SERVICE(contract, self.c)['policy'], 'required')
        for target in ('/dev/null', '/lib/systemd/system/other.service'):
            link.unlink()
            link.symlink_to(target)
            with self.subTest(target=target), self.assertRaises(ValueError):
                VALIDATE_SERVICE(contract, self.c)
        link.unlink()
        with self.assertRaises(FileNotFoundError):
            VALIDATE_SERVICE(contract, self.c)

    def test_service_gate_rejects_payload_changes(self):
        contract, link, unit = self.service_fixture()
        unit.write_bytes(b'evil')
        with self.assertRaisesRegex(ValueError, 'bytes'):
            VALIDATE_SERVICE(contract, self.c)
        unit.write_bytes(b'unit')
        unit.chmod(0o755)
        with self.assertRaisesRegex(ValueError, 'metadata'):
            VALIDATE_SERVICE(contract, self.c)

    def test_service_gate_rejects_override_and_lib_alias(self):
        contract, link, unit = self.service_fixture()
        override = self.root / 'etc/systemd/system/systemd-timesyncd.service'
        override.symlink_to('/dev/null')
        with self.assertRaisesRegex(ValueError, 'mask'):
            VALIDATE_SERVICE(contract, self.c)
        override.unlink()
        (self.root / 'lib').unlink()
        (self.root / 'lib').symlink_to('bad')
        with self.assertRaisesRegex(ValueError, 'alias'):
            VALIDATE_SERVICE(contract, self.c)

    def test_service_failure_cannot_publish_success(self):
        with patch.object(m, 'arm', side_effect=self.run_arm), \
                patch.object(m, 'validate_service', side_effect=ValueError('service link differs')), \
                self.assertRaises(ValueError):
            m.derive(self.c, None, self.h, self.identity)
        self.assertFalse(json.loads((self.work / 'timesync-receipt.json').read_text())['success'])
        self.assertFalse((self.work / 'timesync-package-state.json').exists())

    def test_runner_restricts_names(self):
        with self.assertRaises(ValueError):
            m.arm(self.h, {'commands': []}, ['/bin/true'], 'apt-install')

    def test_runner_failure_keeps_logs(self):
        with patch.object(m.subprocess, 'run', return_value=types.SimpleNamespace(returncode=9)), self.assertRaises(ValueError):
            m.arm(self.h, {'commands': []}, ['/usr/bin/dpkg', '--install', '/run/a733-timesync.deb'], 'timesync-install')
        self.assertTrue((self.work / 'timesync-install.stdout').exists())
        self.assertTrue((self.work / 'timesync-install.stderr').exists())


if __name__ == '__main__':
    unittest.main()
