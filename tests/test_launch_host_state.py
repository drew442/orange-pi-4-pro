"""STATE producer checks: small regular fixtures; no build or hardware claims."""
import argparse
import ast
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock
import uuid

REPO = Path(__file__).resolve().parents[1]


def load(path):
    spec = importlib.util.spec_from_file_location(path.replace('/', '_'), REPO / path)
    result = importlib.util.module_from_spec(spec); spec.loader.exec_module(result)
    return result


m = load('build/launch-host-state.py')
r = load('build/launch-host-roots.py')
c = load('build/launch-uboot-nvme-candidate.py')
v = load('build/verify-host-roots.py')
NEW_UUID = 'fc8bbef6-9385-4ddb-90ba-e41eaccc035b'
OPERATOR = json.loads((REPO / m.OPERATOR).read_bytes())
PRODUCER = json.loads((REPO / m.PRODUCTION).read_bytes())
POLICY = dict(PRODUCER['policy'], operator=OPERATOR, producer=PRODUCER,
              metadata_helper=r.pin((REPO / 'build/rootfs-metadata.py').read_bytes()))


def options():
    return argparse.Namespace(userspace=Path('/missing/rootfs.tar.gz'), config=Path('/missing/management.json'),
        admin_keys=Path('/missing/admin.pub'), roots_receipt=m.PRODUCER_PHASE / 'receipt.json',
        roots_receipt_sha256=PRODUCER['producer_records']['receipt.json']['sha256'],
        state_uuid=NEW_UUID, tag='test')


class StateLauncherTests(unittest.TestCase):
    def test_shared_payload_is_byte_identical_to_producer(self):
        actual = r.payload_program('test')
        self.assertEqual(len(actual), 3116)
        self.assertEqual(hashlib.sha256(actual).hexdigest(),
                         'eec3a0c554579d649787114979f1ad126ba3884e85e595aad594b355c993f834')
        self.assertIn(r.snapshot_program(), m.payload_program(r, 'test', NEW_UUID).decode())

    def test_plan_is_pure_no_input_inode_or_output(self):
        with mock.patch.object(Path, 'lstat', side_effect=AssertionError('external access')), \
             mock.patch.object(Path, 'mkdir', side_effect=AssertionError('staging')):
            result = m.plan(options(), {'revision': 'a' * 40}, POLICY)
        self.assertEqual(result['state_uuid'], NEW_UUID)
        self.assertEqual(result['state_bytes'], 8589934592)
        self.assertFalse(result['hardware_acceptance'])
        self.assertFalse(result['full_filesystem_readback'])
        self.assertEqual(result['output'], 'work/project/out/host-state-test')
        self.assertIn('max(', result['initial_required_bytes'])

    def test_uuid_and_path_and_receipt_pin_guards(self):
        for value in (m.OLD_STATE_UUID, '0' * 32, str(uuid.UUID(int=0)), NEW_UUID.upper(), r.SLOT_UUIDS['A']):
            with self.subTest(value=value), self.assertRaises(ValueError): m.state_uuid(value)
        for tag in ('../x', '', 'a b', '/old', 'x' * 65):
            with self.assertRaises(ValueError): m.phase_path(tag)
        for field, value in (('roots_receipt_sha256', 'a' * 64), ('roots_receipt', Path('/wrong/receipt.json')),
                             ('config', Path('relative')), ('admin_keys', Path('/proc/key'))):
            opts = options(); setattr(opts, field, value)
            with self.subTest(field=field), self.assertRaises(ValueError): m.plan(opts, {}, POLICY)

    def test_inner_selector_precedes_fakeroot_state_capture(self):
        source = m.inner_program(r, 'a' * 40, 'test').decode()
        ast.parse(source)
        self.assertLess(source.index('selection.prepare('), source.index("['fakeroot'"))
        self.assertIn("pathlib.Path('/a733-sources/root-A.ext4')", source)
        self.assertIn("pathlib.Path('/a733-sources/manifest.json')", source)
        payload = m.payload_program(r, 'test', NEW_UUID).decode(); ast.parse(payload)
        self.assertIn('size==8589934592', payload)
        self.assertIn('snapshot(root)==before', payload)
        self.assertNotIn('host-payload.py', payload)
        self.assertNotIn('selection.prepare(', payload)
        self.assertIn("'fakeroot-raw-xattrs-v1'", payload)

    def test_original_payload_bound_readonly_to_existing_anchor(self):
        shared = types.SimpleNamespace(ROOT=Path('/frozen'), SEED=Path('/seed'))
        builder = types.SimpleNamespace(nspawn_command=lambda *a: [
            'systemd-nspawn', '--private-users-ownership=chown', '/bin/sh', '/probe'])
        args = m.command(r, shared, builder, Path('/phase'))
        self.assertIn('--private-users-ownership=off', args)
        self.assertIn('--read-only', args)
        self.assertIn('--bind-ro=' + str(m.PRODUCER_OUTPUT) + ':/a733-sources', args)
        self.assertNotIn('--private-users-ownership=chown', args)
        self.assertEqual(args[-3:], ['/bin/sh', '-c',
            '/bin/sh /a733-probe.sh && exec /usr/bin/python3 -I -B /a733-install.sh'])

    def test_source_parent_sibling_creation_permitted_but_input_changes_fail(self):
        Held = m.held_class(v)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); path = root / 'input'; path.write_bytes(b'initial')
            with Held(path, r.pin(b'initial')) as held:
                (root / 'fresh-phase').mkdir()
                held.unchanged()
            with Held(path, r.pin(b'initial')) as held:
                path.write_bytes(b'changed')
                with self.assertRaises(ValueError): held.unchanged()
                # Do not ask the context manager to verify this intentional mutation.
                held.before = path.stat(); held.pin = r.pin(b'changed')
            link = root / 'link'; link.symlink_to(path)
            with self.assertRaises(OSError): Held(link)
            fifo = root / 'fifo'; os.mkfifo(fifo)
            with self.assertRaises(ValueError): Held(fifo)

    def test_capsule_rejects_bytes_metadata_special_and_namespace_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); path = root / 'config'; path.write_bytes(b'private fixture')
            path.chmod(0o444)
            real_lstat = Path.lstat
            def root_metadata(value):
                info = real_lstat(value)
                fields = list(info); fields[4] = fields[5] = 0
                return os.stat_result(fields)
            with mock.patch.object(Path, 'lstat', root_metadata):
                pins = {'config': r.pin(path.read_bytes())}
                m.verify_capsule(c, root, pins)
                path.chmod(0o644)
                with self.assertRaisesRegex(ValueError, 'metadata'): m.verify_capsule(c, root, pins)
                path.write_bytes(b'changed fixture'); path.chmod(0o444)
                with self.assertRaises(ValueError): m.verify_capsule(c, root, pins)
                path.unlink(); os.mkfifo(path)
                with self.assertRaises(ValueError): m.verify_capsule(c, root, pins)

    def test_root_receipt_and_selected_root_binding(self):
        receipt = {'success': True, 'state': 'complete', 'sources': {'revision': PRODUCER['producer_revision']},
                   'userspace_profile': r.PROFILE, 'metadata_profile': r.METADATA,
                   'slot_bytes': r.SLOT_BYTES, 'slot_uuids': r.SLOT_UUIDS, 'policy': PRODUCER['policy'],
                   'verification': {'manifest': PRODUCER['verification']['manifest']}}
        manifest = {'slot_bytes': r.SLOT_BYTES, 'slot_uuids': r.SLOT_UUIDS,
                    'files': {'root-A.ext4': PRODUCER['readback']['slots']['A']['image']},
                    'sources': {'rootfs.tar.gz': POLICY['archive'], 'userspace_profile': POLICY['binding'],
                                'timesync_state': POLICY['clock'],
                                'rootfs_metadata': {'profile': r.METADATA, 'helper': POLICY['metadata_helper']},
                                **POLICY['diagnostic']}}
        held = {'receipt': object(), 'manifest': types.SimpleNamespace(pin=PRODUCER['verification']['manifest']),
                'root': types.SimpleNamespace(pin=PRODUCER['readback']['slots']['A']['image'])}
        def run():
            with mock.patch.object(m, 'json_held', side_effect=[receipt, manifest]):
                return m.authenticate_roots(v, held, POLICY)
        self.assertEqual(run(), manifest)
        for key, bad in (('success', False), ('state', 'running'), ('slot_bytes', 123),
                         ('userspace_profile', 'baseline-20261104')):
            old = receipt[key]; receipt[key] = bad
            with self.subTest(key=key), self.assertRaises(ValueError): run()
            receipt[key] = old
        manifest['sources']['timesync_state'] = {}
        with self.assertRaises(ValueError): run()

    def test_budget_uses_maximum_not_sum_of_selector_and_state_peaks(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'archive'; source.write_bytes(b'fixture')
            fake = types.SimpleNamespace(authenticated_file=mock.Mock())
            archive = mock.MagicMock(); archive.__enter__.return_value = [types.SimpleNamespace(
                size=440908294, isfile=lambda: True)]
            with mock.patch.object(m.tarfile, 'open', return_value=archive), \
                 mock.patch.object(r, 'git', return_value=b'object blob 1000\n'):
                result = m.budget(r, fake, source, POLICY, {'files': {'a': {'bytes': 7}}}, REPO)
            self.assertEqual(result['state_recipe_bytes'], 18832612397)
            self.assertEqual(result['required_free_bytes'], result['state_recipe_bytes'] +
                             result['capsule_bytes'] + result['git_staging_bound_bytes'])
            self.assertEqual(fake.authenticated_file.call_count, 2)

    def test_operator_policy_and_ordered_fingerprints_and_private_metadata(self):
        common = types.SimpleNamespace(validate_config=mock.Mock(return_value=OPERATOR['management']),
            validate_public_keys=mock.Mock(return_value=(b'canonical fixture', [key['fingerprint'] for key in OPERATOR['keys']])))
        candidate = types.SimpleNamespace(authenticated_file=mock.Mock(return_value=({}, b'private fixture')))
        info = types.SimpleNamespace(st_uid=1000, st_gid=1000, st_mode=stat.S_IFREG | 0o600, st_nlink=1,
                                     st_dev=1, st_ino=2, st_size=3, st_mtime_ns=4, st_ctime_ns=5)
        with mock.patch.object(m, 'module', return_value=common), \
             mock.patch.object(r, 'physical'), mock.patch.object(Path, 'lstat', return_value=info):
            self.assertEqual(m.operator_inputs(r, candidate, {'configs/host_config.py': b'fixture'}, options(), POLICY), b'canonical fixture')
            self.assertEqual(candidate.authenticated_file.call_args_list[0].args[1],
                             OPERATOR['input_records']['management.json'])
            self.assertEqual(candidate.authenticated_file.call_args_list[1].args[1],
                             OPERATOR['input_records']['admin.pub'])
            common.validate_public_keys.return_value = (b'fixture', list(reversed([key['fingerprint'] for key in OPERATOR['keys']])))
            with self.assertRaisesRegex(ValueError, 'fingerprints'): m.operator_inputs(r, candidate, {'configs/host_config.py': b'fixture'}, options(), POLICY)
            common.validate_public_keys.return_value = (b'fixture', [key['fingerprint'] for key in OPERATOR['keys']])
            common.validate_config.return_value = dict(OPERATOR['management'], hostname='other')
            with self.assertRaisesRegex(ValueError, 'management'): m.operator_inputs(r, candidate, {'configs/host_config.py': b'fixture'}, options(), POLICY)
            info.st_mode = stat.S_IFREG | 0o644
            with self.assertRaisesRegex(ValueError, 'metadata'): m.operator_inputs(r, candidate, {'configs/host_config.py': b'fixture'}, options(), POLICY)

    def expectation_fixture(self, root):
        output = root / 'output'; output.mkdir(); readback = root / 'readback'; readback.mkdir()
        members = {}
        def directory(name, mode=0o755, owner=0):
            members[name] = dict(type='dir', mode=mode, uid=owner, gid=owner, mtime_ns=123000000000, xattrs={})
        for path in ('.', 'var', 'var/lib', 'var/lib/systemd', 'boot'):
            directory(path)
        for path in ('identity', 'identity/ssh', 'root', 'root/.ssh', 'config'):
            directory(path, 0o700)
        directory('var/lib/systemd/timesync', 0o755, 996)
        selector = {'SCHEMA': '1', 'SLOT': 'A', 'ROOT_UUID': r.SLOT_UUIDS['A'],
                    'ROOT_MARKER_SHA256': 'a' * 64, 'KERNEL_RELEASE': 'fixture'}
        selector_raw = ''.join(key + '=' + value + '\n' for key, value in selector.items()).encode()
        config = (json.dumps(OPERATOR['management'], sort_keys=True, separators=(',', ':')) + '\n').encode()
        for path, data, mode in (('identity/machine-id', b'1' * 32 + b'\n', 0o644),
                                 ('root/.ssh/authorized_keys', b'canonical fixture', 0o600),
                                 ('config/management.json', config, 0o600),
                                 ('boot/selected.conf', selector_raw, 0o600)):
            members[path] = dict(type='file', mode=mode, uid=0, gid=0, mtime_ns=123000000000, xattrs={}, data=r.pin(data))
        manifest = {'machine_id': '1' * 32, 'boot_selection': selector,
                    'sources': {'boot_selection_input': r.pin(selector_raw)},
                    'filesystem_directories': {name: dict(mode='0700', uid=0, gid=0) for name in ('/', '/lost+found')}}
        staging = {name: {'type': 'directory' if item['type'] == 'dir' else 'file',
                           'uid': item['uid'], 'gid': item['gid'], 'mode': format(item['mode'], '04o'),
                           'xattrs': {}, **item.get('data', {})} for name, item in members.items()}
        def save():
            (output / 'manifest.json').write_text(json.dumps(manifest))
            (output / 'staging-contents.json').write_text(json.dumps(staging))
            (readback / 'state.expected.json').write_text(json.dumps(dict(schema=1, state_uuid=NEW_UUID, members=members)))
            (readback / 'state.stable.json').write_text(json.dumps(dict(schema=1, state_uuid=NEW_UUID, creator_input_stable=True)))
        save()
        return output, readback, members, staging, save

    def test_expected_identity_clock_selector_and_staging_are_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            output, readback, members, staging, save = self.expectation_fixture(Path(directory))
            def verify():
                return m.verify_expectations(c, v, readback, output, NEW_UUID, b'canonical fixture', POLICY)
            self.assertEqual(verify()['state']['character_devices'], 0)
            for path, field, bad in (('identity/machine-id', 'mode', 0o600),
                                      ('identity', 'mode', 0o755),
                                      ('var/lib/systemd/timesync', 'uid', 0),
                                      ('boot/selected.conf', 'data', r.pin(b'wrong'))):
                old = members[path][field]; members[path][field] = bad; save()
                with self.subTest(path=path), self.assertRaises(ValueError): verify()
                members[path][field] = old; save()
            staging['root/.ssh/authorized_keys']['xattrs'] = {'user.unexpected': 'a' * 64}; save()
            with self.assertRaisesRegex(ValueError, 'xattrs'): verify()
            staging['root/.ssh/authorized_keys']['xattrs'] = {}; save()
            members['identity/ssh/host_key'] = copy.deepcopy(members['identity/machine-id']); save()
            with self.assertRaisesRegex(ValueError, 'not empty'): verify()

    def test_actual_sparse_superblock_guards(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / 'state.ext4'
            block = bytearray(1024); block[56:58] = b'\x53\xef'; block[104:120] = uuid.UUID(NEW_UUID).bytes
            struct.pack_into('<I', block, 4, m.STATE_BYTES // 4096)
            struct.pack_into('<I', block, 24, 2); struct.pack_into('<I', block, 92, 4)
            def write():
                with image.open('wb') as stream:
                    stream.truncate(m.STATE_BYTES); stream.seek(1024); stream.write(block)
            write(); m.superblock(image, NEW_UUID)
            for offset, bad in ((92, 0), (100, 0x1000), (4, 8), (24, 1)):
                original = block[offset:offset + 4]; struct.pack_into('<I', block, offset, bad); write()
                with self.subTest(offset=offset), self.assertRaises(ValueError): m.superblock(image, NEW_UUID)
                block[offset:offset + 4] = original
            write()
            with self.assertRaises(ValueError): m.superblock(image, str(uuid.uuid4()))

    def test_manifest_full_hash_coverage_profile_and_selected_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); output = root / 'host-state-test'; output.mkdir()
            (output / 'provenance').mkdir()
            (output / 'provenance/rootfs-metadata.py').write_bytes(b'helper')
            (output / 'state.ext4').write_bytes(b'smallimg')
            (output / 'state.ext4.gz').write_bytes(b'compressed')
            (output / 'staging-contents.json').write_bytes(b'{}')
            selector = {'SCHEMA': '1', 'SLOT': 'A', 'ROOT_UUID': r.SLOT_UUIDS['A'],
                        'ROOT_MARKER_SHA256': 'a' * 64, 'KERNEL_RELEASE': '6.6.98-a733-diag'}
            selector_path = root / 'selection-A.conf'
            selector_path.write_text(''.join(key + '=' + value + '\n' for key, value in selector.items()))
            policy = dict(POLICY, metadata_helper=r.pin(b'helper'))
            value = {'schema': 1, 'state_bytes': 8, 'state_uuid': NEW_UUID, 'boot_selection': selector,
                     'management': OPERATOR['management'],
                     'admin_public_key_fingerprints': [key['fingerprint'] for key in OPERATOR['keys']],
                     'sources': {'rootfs.tar.gz': policy['archive'], 'userspace_profile': policy['binding'],
                                 'timesync_state': policy['clock'],
                                 'rootfs_metadata': {'profile': r.METADATA, 'helper': policy['metadata_helper']},
                                 'operator_config': OPERATOR['input_records']['management.json'],
                                 'operator_public_key': OPERATOR['input_records']['admin.pub'],
                                 'boot_selection_input': r.pin(selector_path.read_bytes())},
                     'files': {str(path.relative_to(output)): r.pin(path.read_bytes()) for path in
                               c.regular_tree_files(output)}}
            def run():
                (output / 'manifest.json').write_text(json.dumps(value))
                with mock.patch.object(m, 'STATE_BYTES', 8), mock.patch.object(m, 'superblock') as check:
                    result = m.verify_output(c, output, policy, NEW_UUID, selector_path, b'fixture')
                    check.assert_called_once_with(output / 'state.ext4', NEW_UUID)
                    return result
            self.assertEqual(run()['boot_selection']['selector'], selector)
            value['boot_selection']['SLOT'] = 'B'
            with self.assertRaisesRegex(ValueError, 'selected root'): run()
            value['boot_selection']['SLOT'] = 'A'
            value['sources']['timesync_state'] = {}
            with self.assertRaisesRegex(ValueError, 'source bindings'): run()
            value['sources']['timesync_state'] = policy['clock']
            (output / 'state.ext4.gz').write_bytes(b'tampered')
            with self.assertRaises(ValueError): run()
            (output / 'state.ext4.gz').write_bytes(b'compressed')
            (output / 'unexpected').write_bytes(b'bad')
            with self.assertRaisesRegex(ValueError, 'file coverage'): run()

    def test_state_output_hooks_and_final_input_failure_retained(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); phase = root / 'phase'
            shared = types.SimpleNamespace(safe_directory=lambda *a, **kw: None, UID_BASE=os.getuid(),
                interrupted=lambda *a: None, stop_process=mock.Mock(), mapped_processes=lambda: [],
                phase_mounts=lambda p: [], INVENTORY=['inventory'],
                resource_sample=lambda: {'free_bytes': 20_000_000_000, 'available_memory_bytes': 3 * 1024**3},
                atomic_json=lambda p, value: p.write_text(json.dumps(value)))
            builder = types.SimpleNamespace(validate_probe=lambda log: {})
            verifier = mock.Mock(return_value={'verified': True})
            def stage(*a):
                (phase / 'work/partial').write_bytes(b'retain state'); return {}
            with mock.patch.object(r.os, 'chown'), \
                 mock.patch.object(r.subprocess, 'check_output', return_value=b'package\n' * 842):
                with self.assertRaisesRegex(ValueError, 'final checks failed'):
                    r.execute(shared, c, builder, root, phase, {}, {}, b'package\n' * 842, {},
                              Path('/rootfs'), None, 'test', output_name='host-state-test', stage=stage,
                              namespace_command=lambda *a: [sys.executable, '-c', 'print("probe")'],
                              verify=verifier, expectations=lambda *a: {'state': {}},
                              final_check=mock.Mock(side_effect=ValueError('root changed')))
                self.assertEqual(verifier.call_args.args[1], phase / 'work/project/out/host-state-test')
                receipt = json.loads((phase / 'receipt.json').read_bytes())
                self.assertFalse(receipt['success']); self.assertEqual(receipt['state'], 'failed')
                self.assertIn('root changed', receipt['cleanup_error'])
                self.assertEqual((phase / 'work/partial').read_bytes(), b'retain state')
                with self.assertRaises(FileExistsError):
                    r.execute(shared, c, builder, root, phase, {}, {}, b'', {}, Path('/r'), None, 'test')


if __name__ == '__main__':
    unittest.main()
