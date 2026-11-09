"""Scaled regular-file distribution tests; no physical acceptance claims."""
from contextlib import ExitStack
import copy
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zlib

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('public_image', REPO / 'build/public-config-nvme-image.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def record(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


class Monitor:
    minimum_free, minimum_memory = 30_000_000_000, 2_000_000_000

    def check(self, *args, **kwargs):
        pass


class StreamTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.raw = bytes(range(256)) * 200
        self.source = self.root / 'base.gz'
        self.source.write_bytes(gzip.compress(self.raw, mtime=0))
        self.parts = {'root-a': (1007, 1391), 'root-b': (2398, 1391), 'state': (3789, 2782)}
        self.ranges = {'prefix': (0, 1007), 'suffix': (6571, len(self.raw) - 6571)}
        self.old, self.new, self.paths = {}, {}, {}
        expected = bytearray(self.raw)
        for index, (name, (start, length)) in enumerate(self.parts.items()):
            data = bytes([65 + index]) * length
            self.old[name] = record(self.raw[start:start + length])
            self.new[name] = record(data)
            self.paths[name] = self.root / name
            self.paths[name].write_bytes(data)
            self.ranges['partition:' + name] = (start, length)
            expected[start:start + length] = data
        self.expected = bytes(expected)

    def derive(self, name='new.gz', old=None, new=None, expected=None, bound=100000):
        with ExitStack() as stack:
            source = stack.enter_context(self.source.open('rb'))
            replacements = {n: (stack.enter_context(self.paths[n].open('rb')).fileno(), *p) for n, p in self.parts.items()}
            stack.enter_context(patch.object(M.BASE, 'CHUNK', 733))
            return M.derive(source.fileno(), replacements, self.root / name, expected or record(self.raw),
                            old or self.old, new or self.new, self.ranges, Monitor(), bound)

    def test_three_cross_chunk_replacements_and_independent_reader(self):
        raw, before, after, wire = self.derive()
        self.assertEqual(raw, record(self.expected))
        self.assertEqual(wire, record((self.root / 'new.gz').read_bytes()))
        self.assertEqual(gzip.decompress((self.root / 'new.gz').read_bytes()), self.expected)
        self.assertEqual(M.BASE.verify_output(self.root / 'new.gz', raw, self.ranges, Monitor()).records(), after)
        M.unchanged(before, after)
        self.derive('second.gz')
        self.assertEqual((self.root / 'new.gz').read_bytes(), (self.root / 'second.gz').read_bytes())

    def test_bad_source_members_crc_short_and_overlong(self):
        original = self.source.read_bytes()
        bad = (original + gzip.compress(b'X'), original + b'X', original[:-1],
               original[:-8] + bytes([original[-8] ^ 1]) + original[-7:],
               gzip.compress(self.raw[:-1]), gzip.compress(self.raw + b'X'),
               gzip.compress(b'X' + self.raw[1:]))
        for index, data in enumerate(bad):
            self.source.write_bytes(data)
            with self.subTest(index=index), self.assertRaises((ValueError, zlib.error)):
                self.derive(str(index) + '.gz')

    def test_wrong_old_new_pins_and_short_replacement(self):
        for index, name in enumerate(self.parts):
            for label, kw in (('old', 'old'), ('new', 'new')):
                pins = copy.deepcopy(self.old if label == 'old' else self.new)
                pins[name]['sha256'] = '0' * 64
                with self.assertRaisesRegex(ValueError, 'extent pin mismatch'):
                    self.derive(f'{index}-{label}.gz', **{kw: pins})
        self.paths['state'].write_bytes(b'Q')
        with self.assertRaisesRegex(ValueError, 'short replacement'):
            self.derive('short.gz')

    def test_bound_outside_and_output_crc_hash_size(self):
        with self.assertRaisesRegex(ValueError, 'scratch bound'):
            self.derive(bound=20)
        self.derive('valid.gz')
        target = self.root / 'valid.gz'
        for expected in (record(self.expected[:-1]), record(b'X' + self.expected[1:])):
            with self.assertRaises(ValueError):
                M.BASE.verify_output(target, expected, self.ranges, Monitor())
        target.write_bytes(target.read_bytes()[:-1])
        with self.assertRaises((EOFError, OSError)):
            M.BASE.verify_output(target, record(self.expected), self.ranges, Monitor())
        with self.assertRaisesRegex(ValueError, 'outside replacement'):
            M.unchanged({'prefix': record(b'a')}, {'prefix': record(b'b')})

    def test_input_paths_pins_alias_and_mutation(self):
        with self.assertRaises(ValueError):
            M.RUNTIME.HeldInput(self.source, 100000, record(b'wrong'))
        with M.RUNTIME.HeldInput(self.source, 100000) as held:
            self.source.write_bytes(gzip.compress(self.raw + b'X'))
            with self.assertRaisesRegex(ValueError, 'changed'):
                held.verify()
        link = self.root / 'link'
        link.symlink_to(self.source)
        hard = self.root / 'hard'
        os.link(self.source, hard)
        for path in (link, hard, Path('/dev/null'), Path('relative')):
            with self.assertRaises((ValueError, OSError)):
                M.RUNTIME.HeldInput(path, 100000)

    def test_resources_and_output_path(self):
        with patch.object(M.BASE.BOOT.SD, 'resources', return_value={'free_bytes': M.ADMISSION - 1}):
            with self.assertRaisesRegex(ValueError, '23760999827'):
                M.BASE.admission(self.root)
        for free, memory in ((M.HELP.DISK_FLOOR - 1, 2_000_000), (30_000_000_000, 1)):
            with patch.object(M.HELP.shutil, 'disk_usage', return_value=SimpleNamespace(free=free)), \
                    patch.object(Path, 'read_text', return_value=f'MemAvailable: {memory} kB\n'):
                with self.assertRaisesRegex(ValueError, 'runtime disk/memory floor'):
                    M.HELP.Monitor(self.root).check('fixture', force=True)
        with patch.object(M, 'REPO', self.root):
            (self.root / 'out').mkdir()
            output = self.root / 'out/public-config-nvme-image-test'
            self.assertEqual(M.output_path(output), output)
            output.mkdir()
            with self.assertRaisesRegex(ValueError, 'already exists'):
                M.output_path(output)
            for path in (Path('out/public-config-nvme-image-a'), self.root / 'public-config-nvme-image-a'):
                with self.assertRaises(ValueError):
                    M.output_path(path)

    def test_publication_failure_leaves_no_completion_manifest(self):
        output = self.root / 'publish'
        output.mkdir()
        partial = output / 'nvme.img.gz.partial'
        partial.write_bytes(b'wire')
        with patch.object(M.os, 'fsync', side_effect=OSError('fixture fsync')):
            with self.assertRaises(OSError):
                M.publish(output, partial, {'schema': 1})
        self.assertFalse((output / 'manifest.json').exists())
        self.assertTrue((output / 'nvme.img.gz').exists())

    def test_closure_held_before_parse_and_reauthenticated_before_publish(self):
        # Exercise the actual build control flow with bounded fixture streams.
        out = self.root / 'out'
        out.mkdir()
        recipe = self.root / 'recipe.py'
        recipe.write_text('# source closure\n')
        base = {'files': {}, 'partition_payloads': self.old,
                'gpt_extents': [], 'derivation': {'after_ranges': {'partition:guests': record(b'G')}}}
        manifests = [self.root / name for name in ('base.json', 'roots.json', 'state.json')]
        for path, value in zip(manifests, (base, {}, {'boot_selection': M.SELECTION})):
            path.write_text(json.dumps(value))
        patches = {'REPO': self.root, 'WIRE': record(self.source.read_bytes()), 'RAW': record(self.raw),
                   'MANIFEST': record(manifests[0].read_bytes()), 'ROOTS_MANIFEST': record(manifests[1].read_bytes()),
                   'STATE_MANIFEST': record(manifests[2].read_bytes()), 'NEW': self.new, 'RECIPES': ('recipe.py',)}
        before = {**{'partition:' + n: p for n, p in self.old.items()},
                  'partition:boot': M.BASE.NEW_BOOT, 'partition:guests': record(b'G')}
        after = {**before, **{'partition:' + n: p for n, p in self.new.items()}}
        real_derive = M.derive
        real_verify = M.BASE.verify_output
        def derived(*args):
            result = real_derive(*args)
            return result[0], before, after, result[3]
        mutate = True
        def verified(*args):
            if mutate:
                recipe.write_text('# source edit\n')
            return SimpleNamespace(records=lambda: after, gpt={})
        with ExitStack() as stack:
            for name, value in patches.items():
                stack.enter_context(patch.object(M, name, value))
            for obj, name, value in ((M.BASE, 'admission', {}), (M.HELP, 'Monitor', Monitor()),
                                     (M, 'validate_metadata', None), (M, 'replacement_extents', self.parts),
                                     (M, 'extents', self.ranges), (M.BASE, 'validate_gpt', None)):
                stack.enter_context(patch.object(obj, name, return_value=value))
            stack.enter_context(patch.object(M, 'derive', side_effect=derived))
            stack.enter_context(patch.object(M.BASE, 'verify_output', side_effect=verified))
            output = out / 'public-config-nvme-image-mutation'
            with self.assertRaisesRegex(ValueError, 'input changed'):
                M.build(self.source, manifests[0], manifests[1], self.paths['root-a'], self.paths['root-b'],
                        manifests[2], self.paths['state'], output)
            self.assertFalse((output / 'manifest.json').exists())
            self.assertFalse((output / 'nvme.img.gz').exists())
            mutate = False
            complete = out / 'public-config-nvme-image-success'
            result = M.build(self.source, manifests[0], manifests[1], self.paths['root-a'], self.paths['root-b'],
                             manifests[2], self.paths['state'], complete)
            self.assertEqual(result['files']['nvme.img'], record(self.expected))
            self.assertEqual(result['boot_selection'], M.SELECTION)
            self.assertEqual(result['partition_payloads'], self.new)
            self.assertEqual(result['derivation']['replaced_extent_bytes'], 5564)
            self.assertFalse(result['hardware_acceptance'])
            self.assertFalse(result['physical_storage_written'])
            self.assertTrue((complete / 'manifest.json').exists())
            self.assertFalse((complete / 'nvme.img').exists())
            self.assertEqual(result['source_manifest'], base)
            # The named normalized profile must route both authority and raw
            # pins through held-input authentication and output derivation.
            # Deliberately poison legacy pins so a default-profile fallback fails.
            invalid_legacy = copy.deepcopy(self.new)
            invalid_legacy['root-a'] = record(b'wrong legacy root')
            with patch.object(M, 'ROOTS_MANIFEST', record(b'wrong legacy manifest')), \
                    patch.object(M, 'NEW', invalid_legacy), \
                    patch.object(M, 'DETERMINISTIC_ROOTS_MANIFEST', record(manifests[1].read_bytes())), \
                    patch.object(M, 'DETERMINISTIC_ROOTS', {k: v for k, v in self.new.items() if k != 'state'}):
                normalized = M.build(self.source, manifests[0], manifests[1], self.paths['root-a'],
                                     self.paths['root-b'], manifests[2], self.paths['state'],
                                     out / 'public-config-nvme-image-normalized', 'deterministic-20261109')
            self.assertEqual(normalized['root_profile'], 'deterministic-20261109')
            self.assertEqual(normalized['roots_manifest'], record(manifests[1].read_bytes()))
            self.assertEqual(normalized['partition_payloads'], self.new)
            self.assertEqual(normalized['files']['nvme.img'], record(self.expected))
            self.assertEqual(normalized['files']['nvme.img.gz'], result['files']['nvme.img.gz'])
            for label in ('member', 'header'):
                tampered = out / ('public-config-nvme-image-' + label)
                def tamper(*args):
                    proof = verified(*args)
                    path = args[0]
                    data = path.read_bytes()
                    if label == 'member':
                        path.write_bytes(data + gzip.compress(b'', mtime=0))
                    else:
                        changed = bytearray(data)
                        changed[4] ^= 1  # MTIME does not affect the decoded payload.
                        path.write_bytes(changed)
                    # The original independent reader accepts both mutations.
                    real_verify(path, record(self.expected), self.ranges, Monitor())
                    return proof
                # The enclosing mock returns fixture GPT/range records; use the
                # real reader to demonstrate raw/range equality survives tampering.
                with patch.object(M.BASE, 'verify_output', side_effect=tamper):
                    with self.assertRaisesRegex(ValueError, 'generated single-member wire'):
                        M.build(self.source, manifests[0], manifests[1], self.paths['root-a'], self.paths['root-b'],
                                manifests[2], self.paths['state'], tampered)
                self.assertFalse((tampered / 'manifest.json').exists())

    def test_all_fixed_inputs_authenticated_before_json_parse(self):
        (self.root / 'out').mkdir()
        manifests = [self.root / name for name in ('base.json', 'roots.json', 'state.json')]
        for path in manifests:
            path.write_bytes(b'invalid JSON')
        self.paths['state'].write_bytes(b'wrong state')
        patches = {'REPO': self.root, 'WIRE': record(self.source.read_bytes()),
                   'MANIFEST': record(manifests[0].read_bytes()), 'ROOTS_MANIFEST': record(manifests[1].read_bytes()),
                   'STATE_MANIFEST': record(manifests[2].read_bytes()), 'NEW': self.new, 'RECIPES': ()}
        with ExitStack() as stack:
            for name, value in patches.items():
                stack.enter_context(patch.object(M, name, value))
            stack.enter_context(patch.object(M.BASE, 'admission', return_value={}))
            stack.enter_context(patch.object(M.HELP, 'Monitor', return_value=Monitor()))
            parsed = stack.enter_context(patch.object(M.json, 'loads'))
            with self.assertRaisesRegex(ValueError, 'hash/size mismatch'):
                M.build(self.source, manifests[0], manifests[1], self.paths['root-a'], self.paths['root-b'],
                        manifests[2], self.paths['state'], self.root / 'out/public-config-nvme-image-invalid')
            parsed.assert_not_called()

    def test_late_publication_error_removes_manifest(self):
        output = self.root / 'late-publication'
        output.mkdir()
        partial = output / 'nvme.img.gz.partial'
        partial.write_bytes(b'wire')
        with patch.object(M, 'fsync_directory', side_effect=[None, None, OSError('late'), None]):
            with self.assertRaisesRegex(OSError, 'late'):
                M.publish(output, partial, {'schema': 1})
        self.assertFalse((output / 'manifest.json').exists())
        self.assertTrue((output / 'nvme.img.gz').exists())


class PinnedMetadataTests(unittest.TestCase):
    def fixture(self):
        userspace = record(b'pinned userspace archive')
        profile = {'profile': 'fixture-profile', 'archive': userspace}
        uuids = {'root-a': M.SELECTION['ROOT_UUID'],
                 'root-b': 'a7330000-0000-4000-8000-00000000000b'}
        fingerprints = ['SHA256:fixture-first', 'SHA256:fixture-second']
        management = {'schema': 1, 'dhcp': True, 'hostname': 'a733-host',
                      'management_mac': '10:f4:ae:90:5e:57', 'management_cidr': '0.0.0.0/0'}
        parts, offset = [], 1048576
        for index, (name, size) in enumerate((('boot', M.BASE.NEW_BOOT['bytes']),
                                             ('root-a', M.NEW['root-a']['bytes']),
                                             ('root-b', M.NEW['root-b']['bytes']),
                                             ('state', M.NEW['state']['bytes']), ('guests', 1048576)), 1):
            parts.append({'number': index, 'name': name, 'start_bytes': offset, 'size_bytes': size})
            offset += size
        base = {'files': {'nvme.img.gz': M.WIRE, 'nvme.img': M.RAW},
                'protected_boot': M.BASE.NEW_BOOT, 'state_uuid': M.RUNTIME.STATE_UUID,
                'kernel_release': M.SELECTION['KERNEL_RELEASE'], 'filesystem_uuids': uuids,
                'layout': {'partitions': parts},
                'deployment_intent': {'administration_public_key_fingerprints': fingerprints,
                                      'management_mac': management['management_mac'],
                                      'management_cidr': management['management_cidr']}}
        roots = {'kernel_release': M.SELECTION['KERNEL_RELEASE'],
                 'slot_uuids': {'A': uuids['root-a'], 'B': uuids['root-b']},
                 'sources': {'rootfs.tar.gz': userspace, 'userspace_profile': profile},
                 'files': {'root-A.ext4': M.NEW['root-a'], 'root-B.ext4': M.NEW['root-b']}}
        state = {'state_uuid': M.RUNTIME.STATE_UUID, 'boot_selection': M.SELECTION,
                 'sources': {'rootfs.tar.gz': userspace, 'userspace_profile': profile, **M.OPERATOR_INPUTS},
                 'files': {'state.ext4': M.NEW['state']}, 'management': management,
                 'admin_public_key_fingerprints': fingerprints}
        return tuple(copy.deepcopy(item) for item in (base, roots, state))

    def test_registered_profiles_preserve_state_and_reject_cross_profile_roots(self):
        original_manifest, original = M.root_inputs('public-dirs-20261109')
        normalized_manifest, normalized = M.root_inputs('deterministic-20261109')
        self.assertNotEqual(original_manifest, normalized_manifest)
        self.assertEqual(normalized['state'], original['state'])
        for name in ('root-a', 'root-b'):
            self.assertEqual(normalized[name]['bytes'], original[name]['bytes'])
            self.assertNotEqual(normalized[name]['sha256'], original[name]['sha256'])
        base, roots, state = self.fixture()
        roots['files'].update({'root-A.ext4': normalized['root-a'], 'root-B.ext4': normalized['root-b']})
        M.validate_metadata(base, roots, state, normalized)
        with self.assertRaisesRegex(ValueError, 'component manifest'):
            M.validate_metadata(base, roots, state)
        roots['files']['root-B.ext4'] = original['root-b']
        with self.assertRaisesRegex(ValueError, 'component manifest'):
            M.validate_metadata(base, roots, state, normalized)
        normalized['state']['sha256'] = '0' * 64
        self.assertEqual(M.root_inputs('deterministic-20261109')[1]['state'], original['state'])

    def test_unknown_profile_refused_before_admission_or_output_access(self):
        for profile in ('unknown', '', None, {'root-a': 'override'}):
            with self.subTest(profile=profile), patch.object(M, 'output_path') as output, \
                    patch.object(M.BASE, 'admission') as admission:
                with self.assertRaisesRegex(ValueError, 'unregistered root profile'):
                    M.build(*([Path('/missing')] * 8), root_profile=profile)
                output.assert_not_called()
                admission.assert_not_called()

    def test_matched_metadata_and_contiguous_extents(self):
        base, roots, state = self.fixture()
        M.validate_metadata(base, roots, state)
        parts = M.replacement_extents(base)
        self.assertEqual(sum(length for _, length in parts.values()), 16 * 1024**3)
        invalid = copy.deepcopy(base)
        invalid['layout']['partitions'][2]['start_bytes'] += 512
        with self.assertRaisesRegex(ValueError, 'contiguous'):
            M.replacement_extents(invalid)

    def test_incompatible_metadata_rejected(self):
        cases = (
            ('state UUID', 2, ('state_uuid',), 'wrong', 'STATE UUID'),
            ('kernel', 1, ('kernel_release',), 'wrong', 'kernel mismatch'),
            ('selector', 2, ('boot_selection', 'ROOT_MARKER_SHA256'), '0' * 64, 'boot selection'),
            ('slot UUID', 1, ('slot_uuids', 'B'), 'wrong', 'root UUID'),
            ('source userspace', 1, ('sources', 'rootfs.tar.gz'), record(b'wrong'), 'source userspace'),
            ('userspace profile', 2, ('sources', 'userspace_profile', 'profile'), 'wrong', 'userspace profile'),
            ('operator config', 2, ('sources', 'operator_config'), record(b'wrong'), 'operator/selection input'),
            ('operator key', 2, ('sources', 'operator_public_key'), record(b'wrong'), 'operator/selection input'),
            ('selector source', 2, ('sources', 'boot_selection_input'), record(b'wrong'), 'operator/selection input'),
            ('fingerprints', 2, ('admin_public_key_fingerprints',), ['SHA256:wrong'], 'operator keys'),
            ('management', 2, ('management', 'dhcp'), False, 'management profile'),
            ('root A pin', 1, ('files', 'root-A.ext4'), record(b'wrong'), 'component manifest'),
            ('root B pin', 1, ('files', 'root-B.ext4'), record(b'wrong'), 'component manifest'),
            ('STATE pin', 2, ('files', 'state.ext4'), record(b'wrong'), 'component manifest'),
            ('BOOT pin', 0, ('protected_boot',), record(b'wrong'), 'protected BOOT'),
            ('base wire', 0, ('files', 'nvme.img.gz'), record(b'wrong'), 'base authority'),
        )
        for label, index, path, value, error in cases:
            metadata = self.fixture()
            target = metadata[index]
            for name in path[:-1]:
                target = target[name]
            target[path[-1]] = value
            with self.subTest(label=label), self.assertRaisesRegex(ValueError, error):
                M.validate_metadata(*metadata)


if __name__ == '__main__':
    unittest.main()
