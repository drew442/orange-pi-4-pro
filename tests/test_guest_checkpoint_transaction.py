"""Real-file internal transaction checks, with ownership mocked when unprivileged.

These tests exercise publication/fsync/inode mechanics, not stopped-guest policy,
shutdown evidence authentication, actual root ownership, or hardware acceptance.
The unchanged manual integration suite separately requires a root fixture.
"""
import base64
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

BASE = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('transaction_manual', BASE / 'scripts/checkpoint-guest.py')
C = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(C)
T = C.TRANSACTION


class CheckpointTransaction(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='checkpoint-core-')
        self.addCleanup(self.tmp.cleanup)
        self.guest = Path(self.tmp.name)
        config = {'schema': 1, 'name': 'one', 'uid': 60001, 'gid': 60001,
                  'memory_mib': 64, 'memory_max_mib': 128, 'cpus': 1}
        files = {}
        for key, filename, data in [('kernel', 'Image', b'kernel'),
                                    ('initrd', 'initramfs.cpio.gz', b'initrd'),
                                    ('disk', 'disk.raw', b'A' * 512)]:
            config[key] = C.record(data)
            files[filename] = {**config[key], 'uid': 60001 if key == 'disk' else 0,
                               'gid': 60001 if key == 'disk' else 0,
                               'mode': '0600' if key == 'disk' else '0444'}
        old = {'schema': 1, 'profile': C.MANIFEST.PROFILE, 'config': config,
               'guest_uuid': '7811c43d-01e4-4635-83b4-25d276172ac0', 'files': files}
        # A real first managed transition must retain even noncanonical old bytes.
        self.old_raw = json.dumps(old, indent=2).encode()
        self.manifest_path = self.guest / 'enrollment.json'
        self.manifest_path.write_bytes(self.old_raw)
        self.manifest_path.chmod(0o400)
        self.disk_path = self.guest / 'disk.raw'
        self.disk_raw = b'B' * 512
        self.disk_path.write_bytes(self.disk_raw)
        self.disk_path.chmod(0o600)
        guest_fd = os.open(self.guest, os.O_RDONLY | os.O_DIRECTORY)
        disk_fd = os.open(self.disk_path, os.O_RDONLY)
        self.addCleanup(os.close, guest_fd)
        self.addCleanup(os.close, disk_fd)
        self.inspection = SimpleNamespace(
            name='one', guest_fd=guest_fd, disk_fd=disk_fd, manifest=old,
            manifest_data=self.old_raw, manifest_record=C.record(self.old_raw),
            disk_record=C.record(self.disk_raw), context={'fixture': True}, recheck=Mock())
        self.authorization = SimpleNamespace(recheck=Mock())
        self.lock_check = Mock()
        self.plan = C.make_plan(self.inspection)
        provenance = {'profile': 'a733-managed-shutdown-v1',
                      'run_id': '7811c43d-01e4-4635-83b4-25d276172ac1',
                      'boot_id': '7811c43d-01e4-4635-83b4-25d276172ac2',
                      'shutdown_profile': 'qmp-guest-shutdown-v1',
                      'fixture_profile': 'managed-power-key-v1', 'evidence_sha256': 'e' * 64}
        new = C.MANIFEST.proposed_managed_manifest(
            old, C.record(self.old_raw)['sha256'], C.record(self.disk_raw), provenance)
        new_raw = C.MANIFEST.canonical(new)
        self.plan['proposed_manifest'] = {**C.record(new_raw), 'value': new}
        self.history_value = {
            'schema': 2, 'operation': 'a733-managed-guest-checkpoint-v2',
            'old_manifest': old, 'new_manifest': new,
            'old_manifest_sha256': self.plan['old_manifest']['sha256'],
            'new_manifest_sha256': self.plan['proposed_manifest']['sha256'],
            'old_manifest_base64': base64.b64encode(self.old_raw).decode('ascii'),
            'new_manifest_base64': base64.b64encode(new_raw).decode('ascii')}
        self.history = C.canonical(self.history_value)
        self.receipt = {key: False for key in (
            'success', 'published', 'durable', 'history_published', 'history_durable',
            'disk_flush_attempted', 'metadata_may_be_modified', 'manifest_replace_attempted')}
        self.receipt.update(schema=2, operation='fixture-only')
        mocks = [patch.object(T.sys, 'stderr', io.StringIO())]
        if os.geteuid() != 0:
            real_fstat = os.fstat
            def root_owned(fd):
                info = real_fstat(fd)
                return SimpleNamespace(**{key: (0 if key in ('st_uid', 'st_gid') else getattr(info, key))
                                          for key in dir(info) if key.startswith('st_')})
            mocks += [patch.object(T.os, 'fstat', side_effect=root_owned),
                      patch.object(T.os, 'fchown')]
        for item in mocks:
            item.start()
            self.addCleanup(item.stop)

    def run_transaction(self, history=None, schema=2):
        T.transaction(self.inspection, self.plan, self.lock_check, self.authorization,
                      self.receipt, enroll=C.ENROLL, manifest=C.MANIFEST,
                      require=C.require, canonical=C.canonical,
                      history_data=self.history if history is None else history,
                      history_schema=schema, validate_record=C.BACKUP.validate_record)

    def test_managed_history_exact_bytes_and_durable_publication(self):
        self.run_transaction()
        self.assertTrue(self.receipt['success'])
        self.assertTrue(self.receipt['durable'])
        self.assertTrue(self.receipt['history_durable'])
        archive = next((self.guest / '.checkpoints').iterdir())
        self.assertEqual(archive.read_bytes(), self.history)
        self.assertEqual(self.manifest_path.read_bytes(),
                         C.MANIFEST.canonical(self.plan['proposed_manifest']['value']))
        self.assertEqual(self.disk_path.read_bytes(), self.disk_raw)
        self.assertGreaterEqual(self.authorization.recheck.call_count, 4)
        self.assertEqual(self.authorization.recheck.call_count, self.lock_check.call_count)

    def test_authorization_invalidation_before_first_write(self):
        self.authorization.recheck.side_effect = ValueError('retained evidence invalidated')
        with patch.object(T.os, 'fsync', side_effect=AssertionError('unexpected flush')):
            with self.assertRaisesRegex(ValueError, 'invalidated'):
                self.run_transaction()
        self.assertFalse(self.receipt['disk_flush_attempted'])
        self.assertFalse((self.guest / '.checkpoints').exists())
        self.assertEqual(self.manifest_path.read_bytes(), self.old_raw)

    def test_valid_history_cannot_bless_other_disk_than_retained_inspection(self):
        provenance = self.history_value['new_manifest']['checkpoint']['shutdown_provenance']
        new = C.MANIFEST.proposed_managed_manifest(
            self.inspection.manifest, C.record(self.old_raw)['sha256'],
            C.record(b'C' * 512), provenance)
        new_raw = C.MANIFEST.canonical(new)
        self.plan['proposed_manifest'] = {**C.record(new_raw), 'value': new}
        history = {**self.history_value, 'new_manifest': new,
                   'new_manifest_sha256': C.record(new_raw)['sha256'],
                   'new_manifest_base64': base64.b64encode(new_raw).decode('ascii')}
        with patch.object(T.os, 'fsync', side_effect=AssertionError('unexpected flush')):
            with self.assertRaisesRegex(ValueError, 'retained inspection'):
                self.run_transaction(C.canonical(history))
        self.assertFalse(self.receipt['disk_flush_attempted'])
        self.assertFalse((self.guest / '.checkpoints').exists())
        self.assertEqual(self.manifest_path.read_bytes(), self.old_raw)

    def test_changed_disk_cannot_be_classified_as_unchanged(self):
        self.plan['requires_update'] = False
        with self.assertRaisesRegex(ValueError, 'classification'):
            self.run_transaction(b'')
        self.assertFalse(self.receipt['success'])
        self.assertFalse(self.receipt['disk_flush_attempted'])

    def test_plan_identity_and_observed_disk_cannot_diverge(self):
        original = copy.deepcopy(self.plan)
        for key, wrong in (('name', 'other'), ('context', {'different': True}),
                           ('disk', C.record(b'C' * 512))):
            self.plan = {**original, key: wrong}
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'retained inspection'):
                self.run_transaction()
            self.assertFalse(self.receipt['disk_flush_attempted'])

    def test_malformed_or_mismatched_history_refused_before_write(self):
        cases = [b'{}', b'not JSON', self.history + b' ',
                 C.canonical({**self.history_value, 'schema': True}),
                 C.canonical({**self.history_value, 'new_manifest_sha256': '0' * 64})]
        for history in cases:
            with self.subTest(history=history[:50]):
                with self.assertRaises(ValueError):
                    self.run_transaction(history)
                self.assertFalse(self.receipt['disk_flush_attempted'])
                self.assertFalse((self.guest / '.checkpoints').exists())
        with self.assertRaises(ValueError):
            self.run_transaction(schema=1)
        self.assertFalse(self.receipt['disk_flush_attempted'])

    def test_structurally_valid_other_transition_refused(self):
        other = copy.deepcopy(self.history_value)
        other['new_manifest']['checkpoint']['shutdown_provenance']['evidence_sha256'] = 'f' * 64
        new_raw = C.MANIFEST.canonical(other['new_manifest'])
        other['new_manifest_sha256'] = C.record(new_raw)['sha256']
        other['new_manifest_base64'] = base64.b64encode(new_raw).decode('ascii')
        with self.assertRaises(ValueError):
            self.run_transaction(C.canonical(other))
        self.assertFalse(self.receipt['disk_flush_attempted'])

    def test_interrupted_manifest_publication_preserves_receipt(self):
        real = T.os.replace
        def interrupted(*args, **kwargs):
            real(*args, **kwargs)
            raise KeyboardInterrupt
        with patch.object(T.os, 'replace', side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                self.run_transaction()
        self.assertTrue(self.receipt['published'])
        self.assertTrue(self.receipt['history_durable'])
        self.assertFalse(self.receipt['durable'])
        self.assertFalse(self.receipt['success'])
        self.assertEqual(list(self.guest.glob('.checkpoint-*')), [])

    def test_interrupted_history_publication_preserves_receipt(self):
        real = C.ENROLL.rename_new
        def interrupted(*args):
            real(*args)
            raise KeyboardInterrupt
        with patch.object(C.ENROLL, 'rename_new', side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                self.run_transaction()
        self.assertTrue(self.receipt['history_published'])
        self.assertFalse(self.receipt['history_durable'])
        self.assertFalse(self.receipt['published'])
        self.assertEqual(self.manifest_path.read_bytes(), self.old_raw)
        self.assertEqual(list(self.guest.glob('.checkpoint-*')), [])

    def test_unchanged_needs_no_history_flush_or_manifest_rewrite(self):
        self.disk_path.write_bytes(b'A' * 512)
        self.inspection.disk_record = C.record(b'A' * 512)
        self.plan = C.make_plan(self.inspection)
        self.assertFalse(self.plan['requires_update'])
        with patch.object(T.os, 'fsync', side_effect=AssertionError('unexpected flush')):
            self.run_transaction(b'')
        self.assertTrue(self.receipt['success'])
        self.assertTrue(self.receipt['unchanged'])
        self.assertFalse(self.receipt['published'])
        self.assertFalse((self.guest / '.checkpoints').exists())
        self.assertEqual(self.manifest_path.read_bytes(), self.old_raw)
        self.authorization.recheck.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
