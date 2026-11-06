"""Native staging/readback tests; namespace ownership and service payload mocked."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, PROJECT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


m = load('timesync_export_adapter_tests', 'build/export-timesync-userspace.py')
helpers = load('timesync_export_fixture', 'tests/test_export_host_userspace.py')


class Export(unittest.TestCase):
    def setUp(self):
        self.e = m.private_module('private_backend', PROJECT / m.BACKEND,
                                  (PROJECT / m.BACKEND).read_bytes())
        self.d = load('private_deriver', m.DERIVER)
        self.c = load('private_configure', 'build/configure-host-userspace.py')
        self.i = load('private_identity', 'build/timesync-identity.py')
        self.addCleanup(patch.stopall)
        patch.object(helpers, 'exporter', self.e).start()
        self.fixture = helpers.ExportTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.work, self.root = self.fixture.work, self.fixture.root
        for obj, name, value in ((m, 'WORK', self.work), (m, 'ROOT', self.root),
                                 (m, 'PROJECT', PROJECT), (m, 'RECEIPT', self.work / 'timesync-receipt.json'),
                                 (m, 'STATE', self.work / 'timesync-package-state.json'),
                                 (self.d, 'ROOT', self.root), (self.d, 'PROJECT', PROJECT)):
            patch.object(obj, name, value).start()
        patch.object(self.d, 'readonly').start()
        native = m.read_native
        patch.object(m, 'read_native', side_effect=lambda path, owner, *a, **kw:
                     native(path, os.getuid() if owner == 0 else owner, *a, **kw)).start()
        patch.object(m, 'live_pids', return_value={1, os.getpid()}).start()
        patch.object(self.e, 'environment').start()
        patch.object(self.e, 'disk_backed').start()
        self.contract = json.loads((PROJECT / self.d.CONTRACT).read_bytes())
        _, self.inventory, _ = self.d.validate_locks(self.contract)
        status = ''.join('Package: %s\nVersion: %s\nArchitecture: %s\nStatus: install ok installed\n\n' %
                         (p, *v) for p, v in self.inventory.items()).encode()
        self.fixture.put('var/lib/dpkg/status', status)
        self.fixture.put('etc/sysusers.d/systemd-timesync.conf',
                         (PROJECT / self.contract['identity']['policy']['path']).read_bytes())
        before = self.work / 'identity-before/etc'
        before.mkdir(parents=True)
        passwd = b'root:x:0:0:root:/root:/bin/sh\n'
        group = b'root:x:0:\n'
        (before / 'passwd').write_bytes(passwd)
        (before / 'group').write_bytes(group)
        self.fixture.put('etc/passwd', passwd + (':'.join(self.i.USER) + '\n').encode())
        self.fixture.put('etc/group', group + (':'.join(self.i.GROUP) + '\n').encode())
        self.service = dict(policy=self.contract['service']['enablement'],
                            links=self.contract['service']['links'],
                            files={p: {k: v[k] for k in ('bytes', 'sha256')}
                                   for p, v in self.contract['service']['installed_files'].items()})
        patch.object(self.d, 'validate_service', return_value=self.service).start()
        self.build_deb_validator = m.authenticate_build_deb
        patch.object(m, 'authenticate_build_deb').start()
        state = dict(schema=1, packages=221, inventory=self.inventory, dpkg_status=m.pin(status))
        m.STATE.write_text(json.dumps(state))
        self.receipt = dict(success=True, configured_packages=221, dpkg_audit_clean=True,
                            apt_check_passed=True, accounts_preserved=True, inputs=self.contract,
                            inventory=self.inventory, package_state=m.pin(m.STATE.read_bytes()),
                            identity_after=self.i.audit(self.root, 'after'),
                            identity_before=self.i.audit(self.work / 'identity-before', 'before'),
                            service_enablement=self.service)
        self.records = {m.SELF: m.pin((PROJECT / m.SELF).read_bytes())}
        patch.object(self.d, 'sources', return_value=self.records).start()
        self.manifest = self.work / 'manifest.json'
        self.manifest.write_text('{}')
        patch.object(m, 'MANIFEST', self.manifest).start()
        self.receipt['source_manifest'] = self.d.record(self.manifest)
        self.write_receipt()

    def write_receipt(self):
        m.RECEIPT.write_text(json.dumps(self.receipt))
        self.sha = m.pin(m.RECEIPT.read_bytes())['sha256']

    def validate(self, initial=True):
        return m.validate(self.d, self.c, self.i, self.contract, self.sha, initial)

    def test_real_staging_enriched_before_publication(self):
        output = self.work / 'export-timesync-test'
        original_publish = self.e.publish
        seen = []
        def publication(staging, target):
            receipt = json.loads((staging / 'receipt.json').read_bytes())
            self.assertEqual(receipt['phase'], 'final-timesync-userspace-export')
            self.assertEqual(receipt['derivation_receipt_sha256'], self.sha)
            self.assertEqual(receipt['bom'], m.pin((staging / 'rootfs-bom.json').read_bytes()))
            seen.append((staging / 'receipt.json').read_bytes())
            original_publish(staging, target)
        self.e.publish = publication
        result = m.adapt(self.d, self.e, self.c, self.i, self.records, self.sha)
        self.e.export(output, self.sha)
        self.assertEqual((output / 'receipt.json').read_bytes(), seen[0])
        self.assertEqual(json.loads(seen[0]), result)
        self.assertEqual(result['provenance']['backend'], m.BACKEND_PIN)
        self.assertEqual(helpers.exporter.PACKAGE_COUNT, 221)
        untouched = load('baseline_again', m.BACKEND)
        self.assertEqual(untouched.PACKAGE_COUNT, 220)
        self.assertEqual(m.pin((PROJECT / m.BACKEND).read_bytes()), m.BACKEND_PIN)

    def test_receipt_tampering_rejected_before_cleanup(self):
        for key, value in (('success', 1), ('configured_packages', True), ('configured_packages', 220),
                           ('accounts_preserved', False), ('inventory', {}), ('service_enablement', {}),
                           ('inputs', {}), ('package_state', {}), ('identity_after', {}), ('source_manifest', {})):
            with self.subTest(key=key):
                original = self.receipt[key]
                self.receipt[key] = value
                self.write_receipt()
                with self.assertRaises(ValueError):
                    self.validate()
                self.receipt[key] = original
        self.assertTrue((self.root / 'usr/sbin/policy-rc.d').exists())

    def test_wrong_sha_and_live_status_policy_state_rejected(self):
        with self.assertRaises(ValueError):
            m.validate(self.d, self.c, self.i, self.contract, '0' * 64)
        for path in (self.root / 'var/lib/dpkg/status', self.root / 'etc/sysusers.d/systemd-timesync.conf', m.STATE):
            data = path.read_bytes()
            path.write_bytes(data + b'changed')
            with self.subTest(path=path), self.assertRaises((ValueError, KeyError, json.JSONDecodeError)):
                self.validate()
            path.write_bytes(data)

    def test_native_link_and_bound_guard(self):
        path = self.work / 'native'
        path.write_bytes(b'ab')
        with self.assertRaises(ValueError):
            m.read_native(path, 0, limit=1)
        link = self.work / 'linked'
        os.link(path, link)
        with self.assertRaises(ValueError):
            m.read_native(path, 0)

    def test_build_deb_tamper_rejected(self):
        self.fixture.put('run/a733-timesync.deb', b'wrong package')
        with self.assertRaisesRegex(ValueError, 'build DEB differs'):
            self.build_deb_validator(self.contract)

    def test_clock_state_and_extra_process_rejected(self):
        (self.root / 'var/lib/systemd/timesync').mkdir()
        with self.assertRaises(ValueError):
            self.validate()
        (self.root / 'var/lib/systemd/timesync').rmdir()
        with patch.object(m, 'live_pids', return_value={1, os.getpid(), 999999}), self.assertRaises(ValueError):
            self.validate()

    def test_changed_sources_never_publish_and_staging_removed(self):
        output = self.work / 'export-timesync-test'
        m.adapt(self.d, self.e, self.c, self.i, self.records, self.sha)
        self.d.sources.return_value = {}
        with self.assertRaises(ValueError):
            self.e.export(output, self.sha)
        self.assertFalse(output.exists())
        self.assertFalse(list(self.work.glob('.export-staging-*')))

    def test_failed_cleanup_never_publishes(self):
        output = self.work / 'export-timesync-test'
        m.adapt(self.d, self.e, self.c, self.i, self.records, self.sha)
        with patch.object(self.e, 'cleanup', side_effect=ValueError('cleanup refused')):
            with self.assertRaises(ValueError):
                self.e.export(output, self.sha)
        self.assertFalse(output.exists())
        self.assertFalse(list(self.work.glob('.export-staging-*')))

    def test_bad_staging_bindings_never_publish(self):
        output = self.work / 'export-timesync-test'
        m.adapt(self.d, self.e, self.c, self.i, self.records, self.sha)
        verify = self.e.verify_archive
        calls = []
        def corrupt_at_hook(path, records):
            result = verify(path, records)
            calls.append(True)
            return dict(result, sha256='0' * 64) if len(calls) == 2 else result
        with patch.object(self.e, 'verify_archive', side_effect=corrupt_at_hook):
            with self.assertRaises(ValueError):
                self.e.export(output, self.sha)
        self.assertFalse(output.exists())
        self.assertFalse(list(self.work.glob('.export-staging-*')))

    def test_existing_output_preserved(self):
        output = self.work / 'export-timesync-test'
        output.mkdir()
        (output / 'sentinel').write_bytes(b'keep')
        with self.assertRaises(ValueError):
            m.export(output, self.sha)
        self.assertEqual((output / 'sentinel').read_bytes(), b'keep')

    def test_removed_build_policy_not_required_after_cleanup(self):
        (self.root / 'usr/sbin/policy-rc.d').unlink()
        self.validate(initial=False)
        with self.assertRaises(FileNotFoundError):
            self.validate(initial=True)


class ClosedSources(unittest.TestCase):
    def test_real_closed_validator_rejects_extra_files(self):
        d = load('closed_deriver', m.DERIVER)
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / 'project'
            project.mkdir()
            data = b'# source\n'
            (project / 'declared.py').write_bytes(data)
            manifest = Path(tmp) / 'manifest.json'
            manifest.write_text(json.dumps({'revision': '1' * 40, 'files': {
                'declared.py': dict(git_blob='2' * 40, **m.pin(data))}}))
            with patch.object(d, 'PROJECT', project), patch.object(d, 'SOURCE_MANIFEST', manifest), \
                    patch.object(d, 'SOURCE_FILES', ('declared.py',)), patch.object(d, 'readonly'):
                self.assertEqual(d.sources()['declared.py'], m.pin(data))
                (project / 'extra.py').write_bytes(b'')
                with self.assertRaises(ValueError):
                    d.sources()

    def test_bootstrap_authenticates_before_dependency_compilation(self):
        reads, compiled = [], []
        own = (PROJECT / m.SELF).read_bytes()
        deriver = (PROJECT / m.DERIVER).read_bytes()
        def reader(path, *args, **kwargs):
            reads.append(path)
            return own if path == PROJECT / m.SELF else deriver
        source_gate = types.SimpleNamespace(sources=lambda: {})
        def compile_private(name, path, data):
            compiled.append(path)
            return source_gate
        with patch.object(m, 'PROJECT', PROJECT), patch.object(m, '__file__', str(PROJECT / m.SELF)), \
                patch.object(m.os.sys, 'flags', types.SimpleNamespace(isolated=True)), \
                patch.object(m.os.sys, 'dont_write_bytecode', True), \
                patch.object(m, 'read_native', side_effect=reader), \
                patch.object(m, 'private_module', side_effect=compile_private):
            with self.assertRaisesRegex(ValueError, 'adapter absent'):
                m.bootstrap()
        self.assertEqual(compiled, [PROJECT / m.DERIVER])
        self.assertEqual(reads, [PROJECT / m.SELF, PROJECT / m.DERIVER])

    def test_bootstrap_rejects_deriver_bytes_before_compile(self):
        with patch.object(m, 'PROJECT', PROJECT), patch.object(m, '__file__', str(PROJECT / m.SELF)), \
                patch.object(m.os.sys, 'flags', types.SimpleNamespace(isolated=True)), \
                patch.object(m.os.sys, 'dont_write_bytecode', True), \
                patch.object(m, 'read_native', return_value=b'changed'), \
                patch.object(m, 'private_module') as compile_private:
            with self.assertRaisesRegex(ValueError, 'deriver source pin'):
                m.bootstrap()
            compile_private.assert_not_called()

    def test_dependency_modules_are_private_and_pins_match(self):
        one = m.private_module('one', PROJECT / m.BACKEND, (PROJECT / m.BACKEND).read_bytes())
        two = m.private_module('two', PROJECT / m.BACKEND, (PROJECT / m.BACKEND).read_bytes())
        one.PACKAGE_COUNT = 221
        self.assertEqual(two.PACKAGE_COUNT, 220)
        self.assertEqual(m.pin((PROJECT / m.DERIVER).read_bytes()), m.DERIVER_PIN)


if __name__ == '__main__':
    unittest.main()
