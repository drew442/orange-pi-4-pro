"""Frozen bootstrap admission and source checks; no build or hardware claims."""
import argparse
import ast
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import types
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]


def load(path):
    spec = importlib.util.spec_from_file_location(path.replace('/', '_'), REPO / path)
    result = importlib.util.module_from_spec(spec); spec.loader.exec_module(result)
    return result


m = load('build/launch-host-bootstrap.py')
r = load('build/launch-host-roots.py')
s = load('build/launch-host-state.py')
v = load('build/verify-host-roots.py')
c = load('build/launch-uboot-nvme-candidate.py')
CONTENTS = {name: (REPO / name).read_bytes() for name in
            __import__('subprocess').check_output(['git', '-C', str(REPO), 'ls-files']).decode().splitlines()}
POLICY = m.contract(r, REPO, CONTENTS)


def options():
    return argparse.Namespace(project=REPO, tag='test', userspace=Path('/missing/rootfs.tar.gz'),
        diagnostic=Path('/missing/diagnostic'), busybox=Path('/missing/busybox'),
        state_receipt=m.STATE_PHASE / 'receipt.json', state_receipt_sha256=m.STATE_RECEIPT_SHA)


class BootstrapLauncherTests(unittest.TestCase):
    def test_original_root_payload_bytes_are_unchanged(self):
        data = r.payload_program('test')
        self.assertEqual(hashlib.sha256(data).hexdigest(),
                         'eec3a0c554579d649787114979f1ad126ba3884e85e595aad594b355c993f834')
        self.assertEqual(len(data), 3116)
        self.assertEqual(m.payload_program('test', m.STATE_UUID).count(b'init.build('), 1)

    def test_plan_never_opens_runtime_inputs_or_builder(self):
        with mock.patch.object(Path, 'lstat', side_effect=AssertionError('runtime access')), \
             mock.patch.object(Path, 'open', side_effect=AssertionError('runtime access')), \
             mock.patch.object(Path, 'mkdir', side_effect=AssertionError('phase reservation')):
            result = m.plan(options(), {'revision': 'a' * 40}, POLICY)
        self.assertEqual(result['phase'], str(m.BASE / 'host-bootstrap-test'))
        self.assertEqual(result['state_uuid'], m.STATE_UUID)
        self.assertEqual(result['output'], 'work/project/out/host-sd-test')
        self.assertEqual(result['initramfs_output'], 'work/project/out/host-initramfs-test')
        self.assertEqual(result['image_bytes'], 268435456)
        self.assertLess(result['image_bytes'], result['actual_sd_bytes'])
        self.assertFalse(result['hardware_acceptance'])
        self.assertFalse(result['physical_storage_written'])

    def test_plan_rejects_unsafe_paths_tags_and_receipts(self):
        cases = [('tag', t) for t in ('', '../other', 'two words', '/bad', 'x' * 65)]
        cases += [('userspace', Path('relative')), ('diagnostic', Path('/dev/diag')),
                  ('busybox', Path('/proc/busybox')), ('state_receipt', Path('/wrong/receipt.json')),
                  ('state_receipt_sha256', '0' * 64), ('state_receipt_sha256', m.STATE_RECEIPT_SHA.upper())]
        for field, value in cases:
            opts = options(); setattr(opts, field, value)
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                m.plan(opts, {}, POLICY)

    def test_baseline_storage_archive_is_preserved(self):
        self.assertEqual(POLICY['archive'], json.loads(CONTENTS[m.BASELINE])['files']['rootfs.tar.gz'])
        self.assertNotEqual(POLICY['archive'], POLICY['state_policy']['archive'])
        self.assertEqual(POLICY['diagnostic']['initramfs.cpio.gz'],
                         json.loads(CONTENTS[r.DIAGNOSTIC])['files']['initramfs.cpio.gz'])
        self.assertEqual(POLICY['busybox']['sha256'],
                         json.loads(CONTENTS['configs/diagnostic-sources.lock.json'])['busybox']['binary_sha256'])

    def test_contract_rejects_producer_or_el2_changes(self):
        cases = [(m.PRODUCTION, ('state_uuid',), '00fd936c-49fb-4fea-87c8-928b9c1599e0'),
                 (m.PRODUCTION, ('phase',), '/wrong'),
                 (m.PRODUCTION, ('source_revision',), 'a' * 40),
                 (m.PRODUCTION, ('producer_records', 'receipt.json', 'sha256'), 'a' * 64),
                 (m.PRODUCTION, ('readback', 'filesystem', 'full_namespace_metadata_content_readback'), False),
                 (m.EL2, ('handoff', 'expected_service'), '0x8000ff04'),
                 (m.EL2, ('handoff', 'expected_spsr'), '0x3c5'),
                 (m.EL2, ('files', 'Image', 'sha256'), 'a' * 64)]
        for path, keys, value in cases:
            contents = dict(CONTENTS); document = json.loads(contents[path]); current = document
            for key in keys[:-1]: current = current[key]
            current[keys[-1]] = value; contents[path] = json.dumps(document).encode()
            with self.subTest(keys=keys), self.assertRaises(ValueError): m.contract(r, REPO, contents)

    def test_all_boot_inputs_and_proofs_are_held_without_raw_images(self):
        records = m.input_records(options(), POLICY)
        self.assertEqual(set(records), {'rootfs.tar.gz', 'busybox', 'state-receipt.json',
                         'state-manifest.json', 'state-readback.json'} | {'diagnostic/' + x for x in m.BOOT_FILES})
        self.assertFalse(any(path.name.endswith(('.ext4', '.img')) for path, _ in records.values()))
        self.assertEqual(records['state-readback.json'][1], POLICY['state_producer']['readback']['receipt'])

    def test_inner_uses_capsule_only_and_current_blob_validation(self):
        source = r.inner_program('a' * 40, 'test').decode(); ast.parse(source)
        self.assertIn("git('cat-file','blob',entry['git_blob'])==data", source)
        self.assertIn("'/a733-debs/project.bundle'", source)
        payload = m.payload_program('test', m.STATE_UUID).decode(); ast.parse(payload)
        self.assertIn("os.umask(0o022)", payload)
        self.assertIn("SOURCE_DATE_EPOCH='1793750400'", payload)
        self.assertEqual(payload.count("boot_profile='experimental-el2'"), 2)
        self.assertIn("inspection.verify(", payload)
        self.assertNotIn('userspace_profile=', payload)
        self.assertNotIn('state.ext4', payload)
        self.assertNotIn('mount', payload)
        for value in ('00000000-0000-0000-0000-000000000000', r.SLOT_UUIDS['A'], m.STATE_UUID.upper()):
            with self.assertRaises(ValueError): m.payload_program('test', value)

    def test_namespace_reuses_isolation_and_private_disk_scratch(self):
        shared = types.SimpleNamespace(ROOT=Path('/frozen'), SEED=Path('/seed'))
        builder = types.SimpleNamespace(nspawn_command=lambda root, probe:
            ['nspawn', '--private-users-ownership=chown', '--private-network', 'shell', 'probe'])
        actual = m.command(r, shared, builder, Path('/phase'))
        self.assertIn('--private-network', actual)
        self.assertIn('--read-only', actual)
        self.assertIn('--private-users-ownership=off', actual)
        self.assertIn('--bind=/phase/work/private-scratch:/var/tmp', actual)
        self.assertIn('--bind-ro=/phase/inputs:/a733-debs', actual)
        self.assertEqual(actual[-3:-1], ['/bin/sh', '-c'])

    def test_initial_budget_includes_capsule_and_git_bound(self):
        held = {'archive': types.SimpleNamespace(pin={'bytes': 123})}
        with mock.patch.object(r, 'git', return_value=b'a blob 10\n'):
            budget = m.budget(r, {'files': {'source': {'bytes': 42}}}, REPO, held)
        self.assertEqual(budget['required_free_bytes'], 4 * 1024**3 + 123 + 30 + 42 + 4096 + 64 * 1024**2)
        with mock.patch.object(r, 'git', return_value=b'a blob 999999999\n'), self.assertRaises(ValueError):
            m.budget(r, {'files': {}}, REPO, held)

    def test_preflight_rejects_low_ram_before_root_preflight(self):
        shared = types.SimpleNamespace(resource_sample=lambda: {'available_memory_bytes': 3 * 1024**3 - 1})
        with mock.patch.object(r, 'preflight', side_effect=AssertionError('should refuse first')), self.assertRaises(ValueError):
            m.preflight(r, shared, CONTENTS, Path('/phase'), {})

    def test_scratch_anchor_requires_empty_real_mapped_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); (root / 'var/tmp').mkdir(parents=True); (root / 'var/tmp').chmod(0o1777)
            shared = types.SimpleNamespace(ROOT=root, UID_BASE=os.getuid())
            m.scratch_anchor(shared)
            (root / 'var/tmp/unexpected').write_text('x')
            with self.assertRaises(ValueError): m.scratch_anchor(shared)
            (root / 'var/tmp/unexpected').unlink(); (root / 'var/tmp').chmod(0o755)
            with self.assertRaises(ValueError): m.scratch_anchor(shared)

    def test_held_leaf_changes_and_parent_replacement_are_rejected(self):
        Held = s.held_class(v)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); parent = root / 'parent'; parent.mkdir(); path = parent / 'input'
            path.write_bytes(b'first')
            item = Held(path)
            try:
                (parent / 'sibling').write_bytes(b'x'); item.unchanged()
                path.write_bytes(b'other'); os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1000000000))
                with self.assertRaises(ValueError): item.unchanged()
            finally: item.close()
            item = Held(path)
            try:
                parent.rename(root / 'old'); parent.mkdir(); (parent / 'input').write_bytes(b'other')
                with self.assertRaises(ValueError): item.unchanged()
            finally: item.close()

    def test_held_rejects_symlink_hardlink_and_fifo_inputs(self):
        Held = s.held_class(v)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); path = root / 'input'; path.write_bytes(b'x')
            (root / 'link').symlink_to(path); os.link(path, root / 'hard'); os.mkfifo(root / 'fifo')
            for target in (root / 'link', root / 'hard', root / 'fifo'):
                with self.subTest(target=target), self.assertRaises((ValueError, OSError)): Held(target)

    def test_manifest_rejects_missing_or_untracked_files_and_changed_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary); raw = b'expected'; (output / 'image').write_bytes(raw)
            manifest = {'schema': 1, 'files': {'image': r.pin(raw)}}
            (output / 'manifest.json').write_text(json.dumps(manifest)); m.verify_manifest(c, output)
            (output / 'other').write_bytes(b'extra')
            with self.assertRaises(ValueError): m.verify_manifest(c, output)
            (output / 'other').unlink(); (output / 'image').write_bytes(b'wrong')
            with self.assertRaises(ValueError): m.verify_manifest(c, output)

    def state_fixture(self):
        producer = POLICY['state_producer']
        receipt = {'success': True, 'state': 'complete', 'phase': str(m.STATE_PHASE),
                   'output': producer['output'], 'state_uuid': m.STATE_UUID, 'state_bytes': 8 * 1024**3,
                   'sources': {'revision': producer['source_revision']}, 'userspace_profile': producer['profile'],
                   'metadata_profile': producer['metadata_profile'], 'policy': POLICY['state_policy'],
                   'verification': producer['verification']}
        manifest = {'schema': 1, 'state_uuid': m.STATE_UUID, 'state_bytes': 8 * 1024**3,
                    'boot_selection': producer['boot_selection'], 'files': {'state.ext4': producer['state_image']},
                    'sources': {'rootfs.tar.gz': POLICY['state_policy']['archive'],
                                'userspace_profile': POLICY['state_policy']['binding'],
                                'timesync_state': POLICY['state_policy']['clock']}}
        readback = {'schema': 1, 'success': True, 'state': 'complete',
                    'full_namespace_metadata_content_readback': True, 'input_identity_unchanged': True,
                    'producer_receipt': producer['producer_records']['receipt.json'],
                    'state_manifest': producer['verification']['manifest'], 'source_policy': POLICY['state_policy'],
                    'parent_root_marker': producer['parent_roots']['readback_expected'],
                    'filesystem': producer['readback']['filesystem'], 'image': producer['state_image']}
        return {'state-receipt.json': types.SimpleNamespace(value=receipt, pin=producer['producer_records']['receipt.json']),
                'state-manifest.json': types.SimpleNamespace(value=manifest, pin=producer['verification']['manifest']),
                'state-readback.json': types.SimpleNamespace(value=readback, pin=producer['readback']['receipt'])}

    def test_state_receipt_manifest_and_readback_are_all_bound(self):
        verifier = types.SimpleNamespace(json_fd=lambda item: item.value)
        held = self.state_fixture(); m.authenticate_state(verifier, held, POLICY)
        changes = [('state-receipt.json', 'success', False), ('state-receipt.json', 'state_uuid', r.SLOT_UUIDS['A']),
                   ('state-receipt.json', 'sources', {'revision': 'a' * 40}),
                   ('state-receipt.json', 'policy', {}), ('state-manifest.json', 'state_uuid', r.SLOT_UUIDS['B']),
                   ('state-manifest.json', 'boot_selection', {}), ('state-readback.json', 'success', False),
                   ('state-readback.json', 'producer_receipt', r.pin(b'wrong')),
                   ('state-readback.json', 'state_manifest', r.pin(b'wrong')),
                   ('state-readback.json', 'parent_root_marker', {}),
                   ('state-readback.json', 'input_identity_unchanged', False),
                   ('state-readback.json', 'full_namespace_metadata_content_readback', False)]
        for name, field, value in changes:
            held = copy.deepcopy(self.state_fixture()); held[name].value[field] = value
            with self.subTest(name=name, field=field), self.assertRaises(ValueError):
                m.authenticate_state(verifier, held, POLICY)

    def output_fixture(self):
        init_output = Path('/phase/work/project/out/host-initramfs-test')
        output = init_output.parent / 'host-sd-test'
        init_files = {'initramfs.cpio.gz': r.pin(b'cpio')}
        common = {'schema': 1, 'state_uuid': m.STATE_UUID, 'kernel_release': '6.6.98-a733-diag',
                  'package_state_profile': 'a733-package-state-v1',
                  'root_selection': 'NVMe state boot/selected.conf; root A/B not embedded'}
        init = dict(common, files=init_files, boot_inputs=json.loads(CONTENTS[r.DIAGNOSTIC])['files'],
                    recipes=POLICY['initramfs_recipes'], entries={name: dict(mode=stat.S_IFREG | 0o755, **r.pin(CONTENTS[source]))
                        for name, source in (('init', 'configs/host-init'),
                                            ('etc/host-storage-resolver.sh', 'configs/host-storage-resolver.sh'))})
        pin = r.pin(json.dumps(init).encode())
        sd = dict(common, files={'a733-host-sd.img': {'bytes': m.IMAGE_BYTES, 'sha256': 'a' * 64},
                                 'a733-host-sd.img.gz': r.pin(b'gz'), 'uInitrd': r.pin(b'wrapper'),
                                 'assembly.log': r.pin(b'log')}, recipes=POLICY['sd_recipes'],
                  source_date_epoch=1793750400, board='Orange Pi 4 Pro A733', boot_profile=POLICY['boot_profile'],
                  boot_inputs={name: POLICY['diagnostic'][name] for name in m.BOOT_FILES if name != 'initramfs.cpio.gz'},
                  source_bom=json.loads(CONTENTS['configs/diagnostic-sources.lock.json']),
                  boot_command=r.pin(CONTENTS['configs/host-boot.cmd']),
                  host_initramfs=dict(path='/a733-export-output/project/out/host-initramfs-test/initramfs.cpio.gz', **init_files['initramfs.cpio.gz']),
                  trusted_host_manifest=dict(path='/a733-export-output/project/out/host-initramfs-test/manifest.json', **pin))
        sd['sources'] = {name: dict(path='/a733-debs/diagnostic/' + name, **record)
                         for name, record in sd['boot_inputs'].items()}
        return output, init_output, sd, init

    def test_output_binds_both_manifests_and_complete_source_closure(self):
        output, init_output, sd, init = self.output_fixture()
        def verify(actual_candidate, actual_output):
            value = init if actual_output == init_output else sd
            return r.pin(json.dumps(value).encode()), value
        with mock.patch.object(m, 'verify_manifest', side_effect=verify):
            result = m.verify_output(r, c, output, init_output, POLICY, CONTENTS)
            self.assertTrue(result['fits_actual_sd'])
            for manifest, field, value in [(sd, 'state_uuid', r.SLOT_UUIDS['A']),
                    (sd, 'boot_profile', dict(POLICY['boot_profile'], expected_spsr='0x3c5')),
                    (sd, 'recipes', {}), (init, 'recipes', {}), (init, 'state_uuid', r.SLOT_UUIDS['B']),
                    (sd, 'root_selection', 'embedded'), (sd, 'sources', {}),
                    (sd, 'host_initramfs', dict(sd['host_initramfs'], sha256='a' * 64)),
                    (init, 'entries', {})]:
                original = manifest[field]; manifest[field] = value
                try:
                    with self.subTest(field=field), self.assertRaises((ValueError, KeyError)):
                        m.verify_output(r, c, output, init_output, POLICY, CONTENTS)
                finally: manifest[field] = original

    def test_raw_inspection_requires_every_result_and_matching_uuid_image(self):
        proof = {'image': r.pin(b'image'), 'state_uuid': m.STATE_UUID, 'physical_storage_writes': False,
                 'initramfs_entries_checked': 2, 'legacy_ext4_features': ['ext_attr']}
        for name in ('mbr_and_raw_blobs', 'pinned_kernel_and_dtb', 'host_script_crc_and_contents',
                     'initrd_wrapper_and_payload', 'gzip_expansion'): proof[name] = 'passed'
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); path = root / 'bootstrap.json'
            path.write_text(json.dumps(proof)); result = m.verify_expectations(c, root, proof['image'], m.STATE_UUID)
            self.assertFalse(result['hardware_acceptance'])
            for field, value in [('state_uuid', r.SLOT_UUIDS['A']), ('image', r.pin(b'other')),
                                 ('physical_storage_writes', True), ('initrd_wrapper_and_payload', 'failed'),
                                 ('legacy_ext4_features', ['has_journal']), ('initramfs_entries_checked', 0)]:
                changed = dict(proof); changed[field] = value; path.write_text(json.dumps(changed))
                with self.subTest(field=field), self.assertRaises(ValueError):
                    m.verify_expectations(c, root, proof['image'], m.STATE_UUID)


if __name__ == '__main__':
    unittest.main()
