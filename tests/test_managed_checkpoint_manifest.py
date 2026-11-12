"""Pure managed provenance/history validation; no runtime shutdown authorization."""
import base64
import copy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('managed_checkpoint_backup', ROOT / 'configs/guest_backup.py')
B = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(B)
M = B.MANIFEST


def initial():
    config = {'schema': 1, 'name': 'one', 'uid': 60001, 'gid': 60001,
              'memory_mib': 64, 'memory_max_mib': 128, 'cpus': 1}
    files = {}
    for key, name in M.FILENAMES.items():
        config[key] = {'bytes': 512, 'sha256': 'a' * 64}
        files[name] = {**config[key], 'uid': 60001 if key == 'disk' else 0,
                       'gid': 60001 if key == 'disk' else 0,
                       'mode': '0600' if key == 'disk' else '0444'}
    return {'schema': 1, 'profile': M.PROFILE, 'config': config, 'files': files,
            'guest_uuid': '7811c43d-01e4-4635-83b4-25d276172ac0'}


def provenance():
    return {'profile': 'a733-managed-shutdown-v1',
            'run_id': 'c9f44972-20fb-4b84-b9ce-23c24ca66daf',
            'boot_id': '7811c43d-01e4-4635-83b4-25d276172ac0',
            'shutdown_profile': 'qmp-guest-shutdown-v1',
            'fixture_profile': 'managed-power-key-v1', 'evidence_sha256': 'f' * 64}


def managed(old, disk='b', old_raw=None):
    return M.proposed_managed_manifest(old, B.sha(old_raw or M.canonical(old)),
                                       {'bytes': 512, 'sha256': disk * 64}, provenance())


def history_record(old, new, old_raw=None, new_raw=None, schema=2):
    old_raw = old_raw or M.canonical(old)
    new_raw = new_raw or M.canonical(new)
    old_sha, new_sha = B.sha(old_raw), B.sha(new_raw)
    value = {'schema': schema,
             'operation': 'a733-managed-guest-checkpoint-v2' if schema == 2 else 'a733-guest-checkpoint-v1',
             'old_manifest': old, 'new_manifest': new,
             'old_manifest_sha256': old_sha, 'new_manifest_sha256': new_sha,
             'old_manifest_base64': base64.b64encode(old_raw).decode(),
             'new_manifest_base64': base64.b64encode(new_raw).decode()}
    return old_sha + '-' + new_sha + '.json', B.canonical(value)


class ManagedManifest(unittest.TestCase):
    def test_manual_contract_exact_bytes_and_independent_expected_content(self):
        old = initial()
        changed = M.proposed_manifest(old, 'c' * 64, {'bytes': 512, 'sha256': 'b' * 64})
        expected = copy.deepcopy(old)
        expected['config']['disk']['sha256'] = 'b' * 64
        expected['files']['disk.raw']['sha256'] = 'b' * 64
        expected['checkpoint'] = {'number': 1, 'previous_manifest_sha256': 'c' * 64,
                                  'previous_disk_sha256': 'a' * 64, 'clean_shutdown_attested': True}
        self.assertEqual(changed, expected)
        self.assertEqual(M.canonical(changed), B.canonical(expected))
        self.assertEqual(B.sha(M.canonical(changed)),
                         'ddd07e45cc34315fc29f4ba8e919404cb3124c48bc6c4a94d2cd87a237e35db1')
        self.assertEqual(M.proposed_manifest(old, 'c' * 64, old['config']['disk']), old)
        historical = M.proposed_manifest(old, B.sha(M.canonical(old)),
                                          {'bytes': 512, 'sha256': 'b' * 64})
        name, raw = history_record(old, historical, schema=1)
        self.assertEqual(B.validate_record(name, raw)[:2], (old, historical))

    def test_upgrade_and_subsequent_checkpoint_bind_provenance(self):
        old = initial()
        manual = M.proposed_manifest(old, 'c' * 64, {'bytes': 512, 'sha256': 'd' * 64})
        changed = managed(manual)
        self.assertEqual((changed['schema'], changed['profile']), (2, M.MANAGED_PROFILE))
        self.assertEqual(changed['checkpoint'], {
            'number': 2, 'previous_manifest_sha256': B.sha(M.canonical(manual)),
            'previous_disk_sha256': 'd' * 64, 'shutdown_provenance': provenance()})
        again = managed(changed, 'e')
        self.assertEqual(again['checkpoint']['number'], 3)
        self.assertEqual(again['checkpoint']['previous_manifest_sha256'], B.sha(M.canonical(changed)))
        for key in ('guest_uuid',):
            self.assertEqual(again[key], old[key])
        for key in ('kernel', 'initrd', 'uid', 'gid', 'cpus', 'memory_mib', 'memory_max_mib'):
            self.assertEqual(again['config'][key], old['config'][key])
        self.assertEqual(manual['schema'], 1)
        self.assertNotIn('checkpoint', old)

    def test_noop_retains_both_versions_and_does_not_upgrade(self):
        for old in (initial(), managed(initial())):
            before = copy.deepcopy(old)
            result = M.proposed_managed_manifest(old, 'a' * 64, old['config']['disk'], provenance())
            self.assertEqual(result, before)
            self.assertEqual(M.canonical(result), M.canonical(before))
            self.assertEqual(old, before)
        for disk in ({'bytes': True, 'sha256': 'b' * 64},
                     {'bytes': 1024, 'sha256': 'b' * 64},
                     {'bytes': 512, 'sha256': True}, {'bytes': 512},
                     {'bytes': 512, 'sha256': 'b' * 64, 'extra': 0}):
            with self.subTest(disk=disk), self.assertRaises(ValueError):
                M.proposed_managed_manifest(initial(), 'a' * 64, disk, provenance())
        with self.assertRaises(ValueError):
            M.proposed_managed_manifest(initial(), 'A' * 64, initial()['config']['disk'], provenance())
        exhausted = managed(initial())
        exhausted['checkpoint']['number'] = 2147483647
        with self.assertRaises(ValueError):
            managed(exhausted, 'e')

    def test_manual_refuses_managed_even_noop(self):
        old = managed(initial())
        for disk in (old['config']['disk'], {'bytes': 512, 'sha256': 'e' * 64}):
            with self.subTest(disk=disk), self.assertRaises(ValueError):
                M.proposed_manifest(old, 'a' * 64, disk)

    def test_invalid_provenance_fields_types_uuids_profiles_and_digest(self):
        replacements = [('profile', 'wrong'), ('profile', True), ('shutdown_profile', 'quit'),
                        ('shutdown_profile', []), ('fixture_profile', 'poweroff'),
                        ('fixture_profile', False), ('evidence_sha256', 'F' * 64),
                        ('evidence_sha256', True), ('evidence_sha256', 'f' * 65),
                        ('evidence_sha256', float('inf'))]
        for key in ('run_id', 'boot_id'):
            replacements += [(key, x) for x in (False, [], None, '', 'a' * 100000,
                '00000000-0000-0000-0000-000000000000', provenance()[key].upper(),
                provenance()[key].replace('-', ''), 'z' * 36)]
        for key, value in replacements:
            evidence = provenance()
            evidence[key] = value
            with self.subTest(key=key, value=str(value)[:80]), self.assertRaises(ValueError):
                M.proposed_managed_manifest(initial(), 'a' * 64,
                    {'bytes': 512, 'sha256': 'b' * 64}, evidence)
        for field in provenance():
            evidence = provenance()
            del evidence[field]
            with self.subTest(missing=field), self.assertRaises(ValueError):
                M.validate_shutdown_provenance(evidence)
        for extra in ('clean_shutdown_attested', 'intent_sha256', 'manifest_sha256'):
            evidence = provenance()
            evidence[extra] = True
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                M.validate_shutdown_provenance(evidence)
        for invalid in (None, [], True):
            with self.assertRaises(ValueError):
                M.validate_shutdown_provenance(invalid)

    def test_structural_validation_cannot_authorize_shutdown(self):
        # Any well-formed evidence digest parses; the external supervisor must authenticate it.
        evidence = provenance()
        evidence['fixture_profile'] = 'none'
        evidence['evidence_sha256'] = '0' * 64
        value = M.proposed_managed_manifest(initial(), 'a' * 64,
                                            {'bytes': 512, 'sha256': 'b' * 64}, evidence)
        self.assertEqual(M.validate_manifest(M.canonical(value)), value)
        evidence['evidence_sha256'] = '1' * 64
        self.assertEqual(value['checkpoint']['shutdown_provenance']['evidence_sha256'], '0' * 64)

    def test_schema_checkpoint_fields_strict_and_json_fail_closed(self):
        value = managed(initial())
        changes = [(('schema',), True), (('schema',), 1), (('profile',), M.PROFILE),
                   (('checkpoint', 'number'), True), (('checkpoint', 'number'), 0),
                   (('checkpoint', 'number'), 2147483648),
                   (('checkpoint', 'previous_manifest_sha256'), False),
                   (('checkpoint', 'previous_disk_sha256'), 'A' * 64),
                   (('checkpoint', 'clean_shutdown_attested'), True),
                   (('checkpoint', 'shutdown_provenance'), {'clean_shutdown_attested': True})]
        for path, replacement in changes:
            changed = copy.deepcopy(value)
            target = changed
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = replacement
            with self.subTest(path=path, value=replacement), self.assertRaises(ValueError):
                M.validate_manifest(json.dumps(changed))
        for field in value['checkpoint']:
            changed = copy.deepcopy(value)
            del changed['checkpoint'][field]
            with self.subTest(missing=field), self.assertRaises(ValueError):
                M.canonical(changed)
        raw = M.canonical(value)
        for invalid in (raw.replace(b'"run_id":', b'"run_id":null,"run_id":'),
                        raw.replace(b'"number":1', b'"number":NaN'),
                        raw.replace(b'"number":1', b'"number":Infinity'),
                        b' ' * (M.LIMIT + 1)):
            with self.assertRaises(ValueError):
                M.validate_manifest(invalid)
        legacy = initial()
        legacy['checkpoint'] = value['checkpoint']
        with self.assertRaises(ValueError):
            M.canonical(legacy)

    def test_restore_v2_rebases_and_next_managed_checkpoint_preserves_restoration(self):
        old = managed(initial())
        restored = M.restored_manifest(old, 'c' * 64, 'd' * 64,
                                       'c9f44972-20fb-4b84-b9ce-23c24ca66daf')
        self.assertEqual((restored['schema'], restored['profile']), (2, M.MANAGED_PROFILE))
        self.assertNotIn('checkpoint', restored)
        self.assertEqual(B.validate_entries({}, M.canonical(restored)), {})
        changed = managed(restored, 'e')
        self.assertEqual(changed['checkpoint']['number'], 1)
        self.assertEqual(changed['restoration'], restored['restoration'])
        self.assertEqual(changed['restoration']['manifest_sha256'], 'd' * 64)
        name, raw = history_record(restored, changed)
        B.validate_entries({name: raw}, M.canonical(changed))
        restored_v1 = M.restored_manifest(initial(), 'c' * 64, 'd' * 64,
                                          'c9f44972-20fb-4b84-b9ce-23c24ca66daf')
        upgraded = managed(restored_v1)
        self.assertEqual(upgraded['restoration'], restored_v1['restoration'])


class ManagedHistory(unittest.TestCase):
    def test_mixed_chain_and_exact_old_bytes(self):
        base = initial()
        base_raw = json.dumps(base, indent=2).encode()
        manual = M.proposed_manifest(base, B.sha(base_raw), {'bytes': 512, 'sha256': 'd' * 64})
        first = managed(manual)
        second = managed(first, 'e')
        entries = dict([history_record(base, manual, old_raw=base_raw, schema=1),
                        history_record(manual, first), history_record(first, second)])
        payload = B.history_payload(entries, M.canonical(second))
        self.assertEqual(B.validate_history(payload, M.canonical(second)), entries)
        name, raw = history_record(base, manual, old_raw=base_raw, schema=1)
        self.assertEqual(B.validate_record(name, raw)[2], base_raw)
        # Managed upgrades also retain independently pinned noncanonical v1 bytes.
        upgraded = managed(base, old_raw=base_raw)
        name, raw = history_record(base, upgraded, old_raw=base_raw)
        self.assertEqual(B.validate_record(name, raw), (base, upgraded, base_raw))
        for missing in entries:
            changed = {name: raw for name, raw in entries.items() if name != missing}
            with self.subTest(missing=missing), self.assertRaises(ValueError):
                B.validate_entries(changed, M.canonical(second))

    def test_version_operation_mismatch_and_downgrade_refused(self):
        old = initial()
        new = managed(old)
        name, raw = history_record(old, new)
        for schema, operation in ((True, 'a733-managed-guest-checkpoint-v2'),
                (1, 'a733-managed-guest-checkpoint-v2'), (2, 'a733-guest-checkpoint-v1'),
                (3, 'a733-managed-guest-checkpoint-v2'), (2, False)):
            changed = json.loads(raw)
            changed.update(schema=schema, operation=operation)
            with self.subTest(schema=schema, operation=operation), self.assertRaises(ValueError):
                B.validate_record(name, B.canonical(changed))
        # A v1 operation cannot create v2, and a v2 operation cannot create v1.
        for schema, previous, following in ((1, old, new), (2, old,
                M.proposed_manifest(old, B.sha(M.canonical(old)), {'bytes': 512, 'sha256': 'b' * 64})),
                (1, new, old), (2, new, old), (2, new, new)):
            name, raw = history_record(previous, following, schema=schema)
            with self.subTest(schema=schema, old=previous['schema'], new=following['schema']), \
                    self.assertRaises(ValueError):
                B.validate_record(name, raw)

    def test_exact_content_hash_and_transition_refuse_forgery(self):
        old = initial()
        new = managed(old)
        name, raw = history_record(old, new)
        for key in ('old_manifest_sha256', 'new_manifest_sha256', 'old_manifest_base64',
                    'new_manifest_base64'):
            changed = json.loads(raw)
            changed[key] = 'e' * 64
            with self.subTest(key=key), self.assertRaises(ValueError):
                B.validate_record(name, B.canonical(changed))
        changed = json.loads(raw)
        changed['old_manifest']['config']['cpus'] = 2
        with self.assertRaises(ValueError):
            B.validate_record(name, B.canonical(changed))
        for mutate in ('number', 'previous_manifest_sha256', 'previous_disk_sha256', 'cpus'):
            forged = copy.deepcopy(new)
            if mutate == 'cpus':
                forged['config']['cpus'] = 2
            else:
                forged['checkpoint'][mutate] = 2 if mutate == 'number' else 'e' * 64
            bad_name, bad_raw = history_record(old, forged)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                B.validate_record(bad_name, bad_raw)
        noncanonical = json.dumps(new, indent=2).encode()
        name, raw = history_record(old, new, new_raw=noncanonical)
        with self.assertRaises(ValueError):
            B.validate_record(name, raw)
        with self.assertRaises(ValueError):
            B.validate_record('e' * 64 + '-' + 'f' * 64 + '.json', raw)
        with self.assertRaises(ValueError):
            B.validate_record(name, raw.replace(b'"schema":2', b'"schema":2,"schema":2'))


if __name__ == '__main__':
    unittest.main()
