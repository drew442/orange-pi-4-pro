"""Replay hook regressions; no full builder or physical acceptance claims."""
import importlib.util
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

REPO = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, REPO / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReplayExportTests(unittest.TestCase):
    def setUp(self):
        self.a = load('replay_export_test', 'build/export-account-dated-userspace.py')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.a.ROOT = self.work / 'arm-root'
        (self.a.ROOT / 'etc').mkdir(parents=True)
        for n in ('shadow', 'shadow-'):
            (self.a.ROOT / 'etc' / n).write_bytes(b'locked-before\n')
        self.stage = self.work / 'stage'
        self.stage.mkdir()
        self.output = self.work / 'export-test'
        self.events = []
        self.records = {self.a.SELF: {'bytes': 1, 'sha256': 'a' * 64},
                        self.a.HELPER: {'bytes': 2, 'sha256': 'b' * 64}}
        self.manifest = {'bytes': 3, 'sha256': 'c' * 64}
        self.receipt_sha = 'd' * 64
        self.bom = {'etc/shadow': {'data': 'fixture'}}
        self.bom_bytes = (json.dumps(self.bom) + '\n').encode()
        self.archive = b'compressed fixture'
        self.raw = {'bytes': 42, 'sha256': 'f' * 64}
        self.expected = {'archive': self.a.pin(self.archive), 'raw_tar': self.raw,
                         'bom': self.a.pin(self.bom_bytes), 'members': 1,
                         'inventory': {'packages': 220, 'lock_sha256': 'e' * 64}, 'epoch': 123}
        self.contract = {'components': {self.a.BACKEND: {'sha256': '0' * 64}},
                         'profiles': {'baseline': self.expected, 'time': self.expected}}
        self.e = types.SimpleNamespace(cleanup=self.cleanup, publish=self.publish,
            held=lambda p: (p.open('rb'), None), digest=lambda f: self.a.pin(f.read()),
            verify_archive=lambda p, b: self.raw, read_checked=lambda p: p.read_bytes())
        self.helper = types.SimpleNamespace(normalize=self.normalize)
        self.source_patch = patch.object(self.a, 'sources', return_value=(self.records, self.manifest))
        self.source_patch.start()
        self.addCleanup(self.source_patch.stop)
        self.native_patch = patch.object(self.a, 'read_native', side_effect=lambda p, *a, **k: p.read_bytes())
        self.native_patch.start()
        self.addCleanup(self.native_patch.stop)
        self.write_stage()

    def cleanup(self):
        self.events.append('original-cleanup')
        return {'unchanged': True}

    def normalize(self, root, profile):
        self.events.append('normalize-' + profile)
        self.assertEqual(root, self.a.ROOT)
        for n in ('shadow', 'shadow-'):
            (root / 'etc' / n).write_bytes(b'locked-after\n')
        return ('shadow', 'shadow-')

    def publish(self, staging, output):
        self.events.append('publish')
        staging.rename(output)

    def write_stage(self, **changes):
        self.report = dict(schema=1, phase='final-userspace-export', success=True,
            configure_receipt_sha256=self.receipt_sha, exporter_sha256='0' * 64,
            archive=self.expected['archive'], raw_tar=self.raw, members=1,
            inventory=self.expected['inventory'], timestamps={'epoch': 123})
        self.report.update(changes)
        (self.stage / 'receipt.json').write_text(json.dumps(self.report))
        (self.stage / 'rootfs-bom.json').write_bytes(self.bom_bytes)
        (self.stage / 'rootfs.tar.gz').write_bytes(self.archive)

    def hooks(self, profile='baseline'):
        return self.a.install_hooks(self.e, self.helper, self.contract, self.records,
                                    self.manifest, self.receipt_sha, profile)

    def test_both_profiles_compose_original_hooks_and_truthful_provenance(self):
        for profile in ('baseline', 'time'):
            with self.subTest(profile=profile):
                self.events.clear()
                self.e.cleanup, self.e.publish = self.cleanup, self.publish
                if self.output.exists():
                    self.output.rename(self.stage)
                captured = self.hooks(profile)
                self.assertEqual(self.e.cleanup(), {'unchanged': True})
                self.e.publish(self.stage, self.output)
                self.assertEqual(self.events, ['original-cleanup', 'normalize-' + profile, 'publish'])
                result = json.loads((self.output / 'receipt.json').read_text())
                self.assertEqual(result, captured)
                replay = result['account_date_replay']
                self.assertEqual(replay['profile'], profile)
                self.assertEqual(replay['adapter'], self.records[self.a.SELF])
                self.assertEqual(replay['source_manifest'], self.manifest)
                self.assertEqual(replay['changed_basenames'], ['shadow', 'shadow-'])
                self.assertNotIn('locked-', json.dumps(replay))
                self.assertEqual(result['exporter_sha256'], '0' * 64)
                self.assertEqual(result['bom'], self.expected['bom'])
                self.assertEqual(result['phase'], self.report['phase'])

    def test_wrong_archive_raw_bom_and_inventory_never_publish(self):
        for key in ('archive', 'raw_tar', 'bom', 'members', 'inventory'):
            with self.subTest(key=key):
                self.e.cleanup, self.e.publish = self.cleanup, self.publish
                expected = dict(self.expected)
                expected[key] = None
                self.contract['profiles']['baseline'] = expected
                self.hooks()
                self.e.cleanup()
                with self.assertRaisesRegex(ValueError, 'historical replay bytes differ'):
                    self.e.publish(self.stage, self.output)
                self.assertFalse(self.output.exists())

    def test_archive_readback_is_required(self):
        self.hooks()
        self.e.cleanup()
        self.e.verify_archive = Mock(side_effect=ValueError('readback fails'))
        with self.assertRaisesRegex(ValueError, 'readback fails'):
            self.e.publish(self.stage, self.output)
        self.assertFalse(self.output.exists())

    def test_cleanup_or_normalization_failure_never_publish(self):
        for which in ('cleanup', 'normalize'):
            self.e.cleanup, self.e.publish = self.cleanup, self.publish
            self.helper.normalize = self.normalize
            if which == 'cleanup':
                self.e.cleanup = Mock(side_effect=ValueError('unsafe clone'))
                self.helper.normalize = Mock()
            else:
                self.helper.normalize = Mock(side_effect=OSError('partial update; discard'))
            self.hooks()
            with self.assertRaises((ValueError, OSError)):
                self.e.cleanup()
            with self.assertRaisesRegex(ValueError, 'normalization hook did not complete'):
                self.e.publish(self.stage, self.output)
            self.assertFalse(self.output.exists())
            if which == 'cleanup':
                self.helper.normalize.assert_not_called()

    def test_sources_revalidated_before_publication(self):
        self.hooks()
        self.e.cleanup()
        self.a.sources.return_value = ({}, self.manifest)
        with self.assertRaisesRegex(ValueError, 'sources changed'):
            self.e.publish(self.stage, self.output)
        self.assertFalse(self.output.exists())

    def test_environment_failure_precedes_hook_installation(self):
        self.a.WORK = self.work
        self.e.environment = Mock(side_effect=ValueError('wrong environment'))
        with patch.object(self.a, 'bootstrap', return_value=(self.e, self.helper, self.contract,
                self.records, self.manifest, None, None)), patch.object(self.a, 'install_hooks') as hooks:
            with self.assertRaisesRegex(ValueError, 'wrong environment'):
                self.a.export(self.output, self.receipt_sha)
            hooks.assert_not_called()

    def test_original_core_preflight_rejection_precedes_normalization(self):
        e = load('original_backend_for_replay_test', 'build/export-host-userspace.py')
        e.ROOT, e.WORK = self.a.ROOT, self.work
        e.MIN_FREE = 0
        for failure in ('authenticate_receipt', 'inventory', 'scan'):
            with self.subTest(failure=failure):
                helper = types.SimpleNamespace(normalize=Mock())
                e.cleanup, e.publish = self.cleanup, self.publish
                self.a.install_hooks(e, helper, self.contract, self.records,
                                     self.manifest, self.receipt_sha, 'baseline')
                with patch.multiple(e, environment=Mock(), disk_backed=Mock(), no_mounts=Mock(),
                        read_checked=Mock(return_value=b'code'), authenticate_receipt=Mock(return_value=b'receipt'),
                        inventory=Mock(return_value={}), scan=Mock(return_value=({}, {}))):
                    setattr(e, failure, Mock(side_effect=ValueError('original validation fails')))
                    with self.assertRaisesRegex(ValueError, 'original validation fails'):
                        e.export(self.output, self.receipt_sha)
                helper.normalize.assert_not_called()

    def test_time_adapt_failure_precedes_normalization(self):
        self.a.WORK = self.work
        self.e.environment = Mock()
        time_adapter = types.SimpleNamespace(adapt=Mock(side_effect=ValueError('identity differs')))
        args = (object(), object(), object(), self.records)
        with patch.object(self.a, 'bootstrap', return_value=(self.e, self.helper, self.contract,
                self.records, self.manifest, time_adapter, args)), patch.object(self.a, 'install_hooks') as hooks:
            with self.assertRaisesRegex(ValueError, 'identity differs'):
                self.a.export(self.output, self.receipt_sha, 'time')
            hooks.assert_not_called()

    def test_time_hooks_run_inside_replay_publication(self):
        self.a.WORK = self.work
        self.e.environment = Mock()
        final = {}
        def adapt(d, e, c, identity, records, expected_sha):
            self.events.append('time-prevalidate')
            original = e.publish
            def publish(staging, output):
                self.events.append('time-final-validate')
                report = json.loads((staging / 'receipt.json').read_text())
                report['profile'] = 'timesync-20261106'
                report['provenance'] = {'original': 'retained'}
                final.update(report)
                (staging / 'receipt.json').write_text(json.dumps(report))
                original(staging, output)
            e.publish = publish
            return final
        def core_export(output, receipt):
            self.events.append('core-preflight')
            self.e.cleanup()
            self.events.append('final-scan-bom')
            self.e.publish(self.stage, output)
        self.e.export = core_export
        time_adapter = types.SimpleNamespace(adapt=adapt)
        args = (object(), object(), object(), self.records)
        with patch.object(self.a, 'bootstrap', return_value=(self.e, self.helper, self.contract,
                self.records, self.manifest, time_adapter, args)):
            result = self.a.export(self.output, self.receipt_sha, 'time')
        self.assertEqual(self.events, ['time-prevalidate', 'core-preflight', 'original-cleanup',
            'normalize-time', 'final-scan-bom', 'time-final-validate', 'publish'])
        self.assertEqual(result, json.loads((self.output / 'receipt.json').read_text()))
        self.assertEqual(result['account_date_replay']['profile'], 'time')
        self.assertEqual(result['provenance'], {'original': 'retained'})

    def test_contract_and_original_pins_are_fixed(self):
        data = (REPO / self.a.CONTRACT).read_bytes()
        contract = self.a.validate_contract(data)
        for path, expected in contract['components'].items():
            self.assertEqual(self.a.pin((REPO / path).read_bytes()), expected)
        with self.assertRaisesRegex(ValueError, 'contract pin differs'):
            self.a.validate_contract(data + b' ')


if __name__ == '__main__':
    unittest.main()
