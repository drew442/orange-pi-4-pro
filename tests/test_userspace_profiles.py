"""Closed production profile proof validation using ordinary file fixtures."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('userspace_profiles', REPO / 'build/userspace-profiles.py')
profiles = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(profiles)


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / 'repo'
        paths = ('build/userspace-profiles.py', profiles.LOCK) + profiles.SOURCES
        paths += tuple(path for group in profiles.EVIDENCE.values() for path in group)
        for relative in paths:
            target = self.repo / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO / relative, target)
        self.lock = json.loads((self.repo / profiles.LOCK).read_text())
        self.name = profiles.PROFILES[1]

    def write_lock(self):
        data = (json.dumps(self.lock, indent=2) + '\n').encode()
        (self.repo / profiles.LOCK).write_bytes(data)
        return patch.object(profiles, 'LOCK_SHA256', hashlib.sha256(data).hexdigest())

    def mutate_proof(self, relative, mutate):
        path = self.repo / relative
        proof = json.loads(path.read_text())
        mutate(proof)
        path.write_text(json.dumps(proof))
        self.lock['profiles'][self.name]['evidence'][relative] = profiles.fingerprint(path.read_bytes())

    def test_real_profiles_and_json_binding(self):
        for name in profiles.PROFILES:
            result = profiles.resolve(name, self.repo)
            self.assertEqual(result['binding']['profile'], name)
            self.assertIsInstance(result['evidence'], Path)
            self.assertIn(self.repo / profiles.LOCK, result['provenance'])
            self.assertIn(self.repo / 'build/userspace-profiles.py', result['provenance'])
            self.assertEqual(set(result['provenance_records']), set(map(str, result['provenance'])))
            for path in result['provenance']:
                self.assertEqual(result['provenance_records'][str(path)], profiles.fingerprint(path.read_bytes()))
            json.dumps(result['binding'])
        normalized = profiles.resolve(self.name, self.repo)
        self.assertEqual(normalized['archive']['bytes'], 138025729)
        self.assertIn(self.repo / 'build/prepare-host-userspace.py', normalized['provenance'])
        self.assertEqual(profiles.resolve(repo=self.repo)['archive']['bytes'], 169852145)

    def test_unknown_profile_rejected_before_reads(self):
        for value in ('normalized', '../proof', '', None, [], True):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'unknown'):
                profiles.resolve(value, self.repo / 'absent')

    def test_lock_pin_cannot_be_reconfigured(self):
        self.lock['profiles'][self.name]['archive']['bytes'] += 1
        (self.repo / profiles.LOCK).write_text(json.dumps(self.lock))
        with self.assertRaisesRegex(ValueError, 'lock hash'):
            profiles.resolve(self.name, self.repo)

    def test_proof_bytes_and_size_are_pinned(self):
        path = self.repo / profiles.EVIDENCE[self.name][0]
        data = path.read_bytes()
        for changed in (data + b' ', data.replace(b'138025729', b'138025728')):
            path.write_bytes(changed)
            with self.assertRaisesRegex(ValueError, 'evidence size/hash'):
                profiles.resolve(self.name, self.repo)

    def test_semantic_proof_checks_survive_repinning(self):
        relative = profiles.EVIDENCE[self.name][0]
        original_lock = copy.deepcopy(self.lock)
        original_data = (self.repo / relative).read_bytes()
        mutations = [
            lambda d: d.update(success=False),
            lambda d: d['export']['archive'].update(bytes=138025728),
            lambda d: d['export']['archive'].update(sha256='0' * 64),
            lambda d: d['export']['raw_tar'].update(bytes=468398081),
            lambda d: d['export'].update(members=15877),
            lambda d: d['export']['timestamps'].update(epoch=1790812801),
            lambda d: d['export']['inventory'].update(packages=219),
            lambda d: d['export']['inventory'].update(lock_sha256='0' * 64),
            lambda d: d['independent_review'].update(success=False),
            lambda d: d['export'].update(success=1),
            lambda d: d['configuration'].update(dpkg_audit_clean=1),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.lock = copy.deepcopy(original_lock)
                (self.repo / relative).write_bytes(original_data)
                self.mutate_proof(relative, mutate)
                with self.write_lock(), self.assertRaises(ValueError):
                    profiles.resolve(self.name, self.repo)

    def test_canonical_disagreement_rejected(self):
        relative = profiles.EVIDENCE[self.name][1]
        self.mutate_proof(relative, lambda d: d['runs'][1]['export']['raw_tar'].update(sha256='0' * 64))
        with self.write_lock(), self.assertRaises(ValueError):
            profiles.resolve(self.name, self.repo)

    def test_malformed_lock_types(self):
        original = copy.deepcopy(self.lock)
        for mutate in (lambda d: d.update(schema=True),
                       lambda d: d.update(profiles=[]),
                       lambda d: d['profiles'].update(extra={}),
                       lambda d: d['profiles'][self.name]['archive'].update(bytes=True),
                       lambda d: d['profiles'][self.name]['archive'].update(sha256='x'),
                       lambda d: d['profiles'][self.name].update(epoch=True)):
            self.lock = copy.deepcopy(original)
            mutate(self.lock)
            with self.write_lock(), self.assertRaises(ValueError):
                profiles.resolve(self.name, self.repo)

    def test_no_evidence_path_aliases(self):
        evidence = self.lock['profiles'][self.name]['evidence']
        relative = next(iter(evidence))
        evidence[relative.replace('docs/', 'docs/../docs/')] = evidence.pop(relative)
        with self.write_lock(), self.assertRaisesRegex(ValueError, 'evidence paths'):
            profiles.resolve(self.name, self.repo)

    def test_duplicate_json_keys_rejected(self):
        path = self.repo / profiles.LOCK
        data = path.read_bytes().replace(b'"schema": 1,', b'"schema": 1, "schema": 1,', 1)
        path.write_bytes(data)
        with patch.object(profiles, 'LOCK_SHA256', hashlib.sha256(data).hexdigest()):
            with self.assertRaisesRegex(ValueError, 'duplicate'):
                profiles.resolve(self.name, self.repo)

    def test_symlink_file_parent_and_repo_rejected(self):
        relative = profiles.EVIDENCE[self.name][0]
        path = self.repo / relative
        saved = path.with_suffix('.saved')
        path.rename(saved)
        path.symlink_to(saved.name)
        with self.assertRaises((ValueError, OSError)):
            profiles.resolve(self.name, self.repo)
        path.unlink()
        saved.rename(path)
        docs = self.repo / 'docs'
        saved_docs = self.repo / 'real-docs'
        docs.rename(saved_docs)
        docs.symlink_to(saved_docs.name, target_is_directory=True)
        with self.assertRaises((ValueError, OSError)):
            profiles.resolve(self.name, self.repo)
        alias = self.repo.parent / 'alias'
        alias.symlink_to(self.repo, target_is_directory=True)
        with self.assertRaises((ValueError, OSError)):
            profiles.resolve(self.name, alias)

    def test_baseline_archive_disagreement_rejected(self):
        self.lock['profiles'][profiles.DEFAULT]['archive']['sha256'] = '0' * 64
        with self.write_lock(), self.assertRaisesRegex(ValueError, 'baseline evidence'):
            profiles.resolve(repo=self.repo)

    def test_duplicate_proof_keys_rejected(self):
        relative = profiles.EVIDENCE[self.name][0]
        path = self.repo / relative
        data = path.read_bytes().replace(b'"success": true,', b'"success": true, "success": true,', 1)
        path.write_bytes(data)
        self.lock['profiles'][self.name]['evidence'][relative] = profiles.fingerprint(data)
        with self.write_lock(), self.assertRaisesRegex(ValueError, 'duplicate'):
            profiles.resolve(self.name, self.repo)

    def test_source_mutation_rejected(self):
        path = self.repo / profiles.SOURCES[0]
        path.write_bytes(path.read_bytes() + b'\n')
        with self.assertRaisesRegex(ValueError, 'source size/hash'):
            profiles.resolve(self.name, self.repo)

    def test_nonregular_and_oversized_inputs(self):
        path = self.repo / profiles.EVIDENCE[self.name][0]
        path.unlink()
        path.mkdir()
        with self.assertRaises(ValueError):
            profiles.resolve(self.name, self.repo)
        path.rmdir()
        with path.open('wb') as stream:
            stream.truncate(profiles.MAX_BYTES + 1)
        with self.assertRaises(ValueError):
            profiles.resolve(self.name, self.repo)


if __name__ == '__main__':
    unittest.main()
