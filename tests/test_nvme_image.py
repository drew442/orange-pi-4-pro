"""Integration contracts and regular-file-only sparse placement regressions."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('nvme_image_tests', REPO / 'build/nvme-image.py')
IMAGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(IMAGE)


class NVMeImageTests(unittest.TestCase):
    def setUp(self):
        self.roots = json.loads((REPO / 'docs/evidence/managed-payload-20261104.json').read_text())
        self.state = json.loads((REPO / 'docs/evidence/managed-state-fixture-20261104.json').read_text())['manifest']
        self.sd = json.loads((REPO / 'docs/evidence/host-sd-20261104.json').read_text())
        self.roots['package_state'] = {'profile': 'a733-package-state-v1', 'manifest': {'bytes':1,'sha256':'a'*64}}
        self.roots['management_runtime']['files']['usr/lib/orangepi-hypervisor/package_state.py'] = {'bytes':1,'sha256':'a'*64}
        self.sd['package_state_profile'] = 'a733-package-state-v1'
        self.diag = json.loads((REPO / 'docs/evidence/diagnostic-20261104.json').read_text())['files']

    def test_fixture_cannot_be_marked_for_installation(self):
        self.assertFalse(IMAGE.installation_intent(self.state)['for_installation'])
        with self.assertRaisesRegex(ValueError, 'fixture'):
            IMAGE.installation_intent(self.state, True)
        candidate = copy.deepcopy(self.state)
        candidate['management']['management_mac'] = '02:00:00:73:33:12'
        candidate['management']['management_cidr'] = '192.168.50.0/24'
        candidate['admin_public_key_fingerprint'] = 'SHA256:' + 'A' * 43
        self.assertTrue(IMAGE.installation_intent(candidate, True)['for_installation'])
        for cidr in ('192.0.0.0/16', '192.0.2.0/24', '198.51.100.0/24', '203.0.113.0/24', '127.0.0.0/8', '224.0.0.0/3'):
            candidate['management']['management_cidr'] = cidr
            with self.subTest(cidr=cidr), self.assertRaises(ValueError):
                IMAGE.installation_intent(candidate, True)

    def test_plural_manifest_compatibility_crosschecks_and_fixture_rejection(self):
        candidate = copy.deepcopy(self.state)
        legacy = candidate['admin_public_key_fingerprint']
        self.assertEqual(IMAGE.admin_fingerprints(candidate), [legacy])
        candidate['admin_public_key_fingerprints'] = [legacy]
        self.assertEqual(IMAGE.admin_fingerprints(candidate), [legacy])
        candidate['management']['management_mac'] = '02:00:00:73:33:12'
        candidate['management']['management_cidr'] = '0.0.0.0/0'
        candidate.pop('admin_public_key_fingerprint')
        first, second = 'SHA256:' + 'A' * 43, 'SHA256:' + 'B' * 43
        candidate['admin_public_key_fingerprints'] = [first, second]
        intent = IMAGE.installation_intent(candidate, True)
        self.assertEqual(intent['administration_public_key_fingerprints'], [first, second])
        self.assertNotIn('administration_public_key_fingerprint', intent)
        for values in ([], None, 'invalid', [first, first], [first, 1], [first] * 17):
            candidate['admin_public_key_fingerprints'] = values
            with self.subTest(values=values), self.assertRaises(ValueError):
                IMAGE.admin_fingerprints(candidate)
        candidate['admin_public_key_fingerprints'] = [first, legacy]
        with self.assertRaisesRegex(ValueError, 'fixture'):
            IMAGE.installation_intent(candidate, True)
        candidate['admin_public_key_fingerprints'] = [first, second]
        candidate['admin_public_key_fingerprint'] = first
        with self.assertRaisesRegex(ValueError, 'legacy'):
            IMAGE.admin_fingerprints(candidate)

    def test_candidate_eligibility_matches_writer(self):
        spec = importlib.util.spec_from_file_location('image_writer_policy', REPO / 'scripts/install-nvme.py')
        writer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(writer)
        candidate = copy.deepcopy(self.state)
        candidate['management']['management_cidr'] = '192.168.50.0/24'
        candidate['admin_public_key_fingerprint'] = 'SHA256:' + 'A' * 43
        for mac in ('02:73:30:00:00:01', '02:11:22:33:44:55', '02:00:00:73:33:12'):
            candidate['management']['management_mac'] = mac
            if mac.startswith('02:00:'):
                from scripts.nvme_layout import layout
                intent = IMAGE.installation_intent(candidate, True)
                manifest = {'schema': 1, 'kernel_release': '6.6.98-a733-diag',
                            'files': {'nvme.img': {'bytes': 250_000_000_000, 'sha256': '0' * 64}},
                            'logical_sector_bytes': 512, 'physical_sector_bytes': 4096,
                            'layout': layout(250_000_000_000, 512, 4096), 'deployment_intent': intent}
                writer.validate_manifest(manifest)
            else:
                with self.assertRaises(ValueError):
                    IMAGE.installation_intent(candidate, True)

    def test_state_identity_readback_checks_actual_key_mode_and_host_keys(self):
        import base64
        import struct
        key = b'ssh-ed25519 ' + base64.b64encode(struct.pack('>I', 11) + b'ssh-ed25519' + struct.pack('>I', 32) + bytes(range(32))) + b'\n'
        state = copy.deepcopy(self.state)
        state['admin_public_key_fingerprint'] = IMAGE.STATE.validate_public_key(key)[1]
        responses = {'cat /config/management.json': json.dumps(state['management']).encode(),
                     'cat /root/.ssh/authorized_keys': key,
                     'cat /identity/machine-id': (state['machine_id'] + '\n').encode(),
                     'ls -p /identity/ssh': b'/2/040700/0/0/./4096/\n/1/040700/0/0/../4096/\n'}
        for path in ('/config/management.json', '/root/.ssh/authorized_keys', '/identity/machine-id'):
            responses['stat ' + path] = b'Inode: 12 Type: regular Mode: 0600\nUser: 0 Group: 0\n'
        responses['stat /identity/ssh'] = b'Inode: 13 Type: directory Mode: 0700\nUser: 0 Group: 0\n'
        with patch.object(IMAGE, 'debugfs', side_effect=lambda image, command: responses[command]):
            IMAGE.validate_state_identity(Path('fixture'), state)
        readable = dict(responses)
        readable['stat /identity/machine-id'] = b'Type: regular Mode: 0644\nUser: 0 Group: 0\n'
        with patch.object(IMAGE, 'debugfs', side_effect=lambda image, command: readable[command]):
            IMAGE.validate_state_identity(Path('fixture'), state)
        for mode in ('0666', '0664', '0674'):
            changed = dict(responses)
            changed['stat /identity/machine-id'] = ('Type: regular Mode: ' + mode + '\nUser: 0 Group: 0\n').encode()
            with self.subTest(mode=mode), patch.object(IMAGE, 'debugfs', side_effect=lambda image, command: changed[command]):
                with self.assertRaises(ValueError):
                    IMAGE.validate_state_identity(Path('fixture'), state)
        for command, value in (('cat /identity/machine-id', b'f'*32 + b'\n'),
                                ('stat /root/.ssh/authorized_keys', b'Type: regular Mode: 0644\nUser: 0 Group: 0\n'),
                                ('ls -p /identity/ssh', responses['ls -p /identity/ssh'] + b'/14/0100600/0/0/ssh_host_ed25519_key/100/\n')):
            changed = dict(responses); changed[command] = value
            with self.subTest(command=command), patch.object(IMAGE, 'debugfs', side_effect=lambda image, name: changed[name]):
                with self.assertRaises(ValueError):
                    IMAGE.validate_state_identity(Path('fixture'), state)
        wrong = dict(state); wrong['admin_public_key_fingerprint'] = 'SHA256:' + 'A'*43
        with patch.object(IMAGE, 'debugfs', side_effect=lambda image, name: responses[name]):
            with self.assertRaises(ValueError):
                IMAGE.validate_state_identity(Path('fixture'), wrong)

    def test_multikey_state_readback_detects_missing_extra_and_reordered_keys(self):
        import base64
        import struct
        def key(number):
            return b'ssh-ed25519 ' + base64.b64encode(struct.pack('>I', 11) + b'ssh-ed25519' +
                    struct.pack('>I', 32) + bytes([number]) * 32) + b'\n'
        first, second, extra = key(1), key(2), key(3)
        state = copy.deepcopy(self.state)
        state.pop('admin_public_key_fingerprint')
        state['admin_public_key_fingerprints'] = IMAGE.STATE.validate_public_keys(first + second)[1]
        responses = {'cat /config/management.json': json.dumps(state['management']).encode(),
                     'cat /root/.ssh/authorized_keys': first + second,
                     'cat /identity/machine-id': (state['machine_id'] + '\n').encode(),
                     'ls -p /identity/ssh': b'/2/040700/0/0/./4096/\n/1/040700/0/0/../4096/\n',
                     'stat /identity/ssh': b'Type: directory Mode: 0700\nUser: 0 Group: 0\n'}
        for path in ('/config/management.json', '/root/.ssh/authorized_keys', '/identity/machine-id'):
            responses['stat ' + path] = b'Type: regular Mode: 0600\nUser: 0 Group: 0\n'
        with patch.object(IMAGE, 'debugfs', side_effect=lambda image, command: responses[command]):
            IMAGE.validate_state_identity(Path('fixture'), state)
            for data in (first, second + first, first + second + extra, first + first,
                         first.rstrip(b'\n') + b' comment\n' + second):
                responses['cat /root/.ssh/authorized_keys'] = data
                with self.subTest(data=data[:70]), self.assertRaises(ValueError):
                    IMAGE.validate_state_identity(Path('fixture'), state)

    def test_state_readback_cannot_substitute_declared_identity(self):
        from unittest.mock import patch
        with patch.object(IMAGE, 'debugfs', return_value=b'{}'):
            with self.assertRaises(ValueError):
                IMAGE.validate_state_identity(Path('unused'), self.state)

    def test_pinned_payload_state_bootstrap_pairing(self):
        self.assertEqual(IMAGE.validate_pairing(self.roots, self.state, self.sd, self.diag), self.state['state_uuid'])

    def time_sources(self):
        payload = IMAGE.SELECT.INIT.PAYLOAD
        selected = payload.userspace_binding('timesync-20261106')
        return {'rootfs.tar.gz': selected['archive'], 'userspace_profile': selected['binding'],
                'timesync_state': payload.clock_state_policy('timesync-20261106')}

    def test_time_pair_requires_matching_archive_and_clock_policy(self):
        self.roots['sources'].update(self.time_sources())
        self.roots['management_runtime']['profile'] = 'a733-management-v2-timesync'
        with self.assertRaises(ValueError):
            IMAGE.validate_pairing(self.roots, self.state, self.sd, self.diag)
        self.state['sources'].update(copy.deepcopy(self.time_sources()))
        self.assertEqual(IMAGE.validate_pairing(self.roots, self.state, self.sd, self.diag), self.state['state_uuid'])
        for field in ('rootfs.tar.gz', 'userspace_profile', 'timesync_state'):
            changed = copy.deepcopy(self.state)
            changed['sources'][field] = None
            with self.subTest(field=field), self.assertRaises(ValueError):
                IMAGE.validate_pairing(self.roots, changed, self.sd, self.diag)

    def test_captured_time_root_cannot_change_declared_sources(self):
        self.roots['sources'].update(self.time_sources())
        self.roots['management_runtime']['profile'] = 'a733-management-v2-timesync'
        metadata = {name: copy.deepcopy(self.roots[name]) for name in
                    ('sources', 'management_runtime', 'package_state')}
        with tempfile.TemporaryDirectory() as temporary:
            image = Path(temporary) / 'root.ext4'
            image.write_bytes(b'parser fixture')
            with patch.object(IMAGE.SELECT.INIT, 'root_marker', return_value=(b'marker', metadata)):
                self.assertEqual(IMAGE.root_selection(image, 'A', self.roots, self.diag)['SLOT'], 'A')
            for field in ('rootfs.tar.gz', 'userspace_profile', 'timesync_state'):
                changed = copy.deepcopy(metadata)
                if field == 'timesync_state':
                    changed['sources'][field]['helper']['sha256'] = 'e' * 64
                else:
                    changed['sources'].pop(field)
                with self.subTest(field=field), patch.object(IMAGE.SELECT.INIT, 'root_marker', return_value=(b'marker', changed)):
                    with self.assertRaises(ValueError):
                        IMAGE.root_selection(image, 'A', self.roots, self.diag)

    def test_fresh_time_state_requires_empty_owned_clock_directory(self):
        state = {'sources': self.time_sources()}
        responses = {'stat ' + path: f'Type: directory Mode: 0755\nUser: {owner} Group: {owner}\n'.encode()
                     for path, owner in (('/var', 0), ('/var/lib', 0), ('/var/lib/systemd', 0),
                                         ('/var/lib/systemd/timesync', 996))}
        command = 'ls -p /var/lib/systemd/timesync'
        responses[command] = b'/2/040755/996/996/./4096/\n/1/040755/0/0/../4096/\n'
        with patch.object(IMAGE, 'debugfs', side_effect=lambda image, name: responses[name]):
            IMAGE.validate_clock_directory(Path('fixture'), state)
        for name in responses:
            changed = dict(responses)
            changed[name] = b'' if name != command else responses[name] + b'/3/0100644/996/996/clock/0/\n'
            with self.subTest(name=name), patch.object(IMAGE, 'debugfs', side_effect=lambda image, key: changed[key]):
                with self.assertRaises(ValueError):
                    IMAGE.validate_clock_directory(Path('fixture'), state)

    def test_explicit_boot_profiles_require_matching_locked_sd_provenance(self):
        for name in IMAGE.HOST.BOOT_PROFILES:
            pinned, profile, _ = IMAGE.HOST.load_boot_profile(name)
            sd = copy.deepcopy(self.sd)
            sd['boot_inputs'] = pinned
            sd['boot_profile'] = profile
            self.assertEqual(IMAGE.validate_pairing(self.roots, self.state, sd, pinned, name),
                             self.state['state_uuid'])
            other = 'experimental-el2' if name == 'baseline-el1' else 'baseline-el1'
            with self.subTest(profile=name), self.assertRaisesRegex(ValueError, 'boot profile'):
                IMAGE.validate_pairing(self.roots, self.state, sd, pinned, other)
            changed = copy.deepcopy(sd)
            changed['boot_profile']['evidence']['sha256'] = '0' * 64
            with self.assertRaisesRegex(ValueError, 'provenance'):
                IMAGE.validate_pairing(self.roots, self.state, changed, pinned, name)
            changed = copy.deepcopy(sd)
            changed['boot_inputs']['boot_package.fex']['sha256'] = '0' * 64
            with self.assertRaisesRegex(ValueError, 'locked inputs'):
                IMAGE.validate_pairing(self.roots, self.state, changed, pinned, name)
        for name in IMAGE.HOST.BOOT_PROFILES:
            invalid = copy.deepcopy(self.sd)
            invalid['boot_profile'] = None
            with self.subTest(profile=name), self.assertRaisesRegex(ValueError, 'boot profile'):
                IMAGE.validate_pairing(self.roots, self.state, invalid, self.diag, name)
        historical_el2 = copy.deepcopy(self.sd)
        historical_el2['boot_inputs']['boot_package.fex'] = IMAGE.HOST.load_boot_profile('experimental-el2')[0]['boot_package.fex']
        with self.assertRaisesRegex(ValueError, 'locked inputs'):
            IMAGE.validate_pairing(self.roots, self.state, historical_el2, self.diag)
        self.assertEqual(IMAGE.HOST.load_boot_profile('experimental-el2')[0]['initramfs.cpio.gz'],
                         self.diag['initramfs.cpio.gz'])
        with self.assertRaisesRegex(ValueError, 'boot profile'):
            IMAGE.validate_pairing(self.roots, self.state, self.sd, self.diag, 'experimental-el2')
        with self.assertRaisesRegex(ValueError, 'unknown boot profile'):
            IMAGE.validate_pairing(self.roots, self.state, self.sd, self.diag, 'unknown')

    def test_build_profile_mismatch_refused_before_output_or_storage_work(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directories = []
            for name, manifest in (('roots', self.roots), ('state', self.state), ('sd', self.sd)):
                directory = root / name
                directory.mkdir()
                (directory / 'manifest.json').write_text(json.dumps(manifest))
                directories.append(directory)
            output = root / 'out/nvme-image-fixture'
            with patch.object(IMAGE.os, 'geteuid', return_value=0), patch.object(IMAGE.STATE, 'validate_workspace_storage') as storage, patch.object(IMAGE, 'make_filesystem') as filesystem:
                with self.assertRaisesRegex(ValueError, 'boot profile'):
                    IMAGE.build(*directories, root, output, 256060514304,
                                boot_profile='experimental-el2')
                storage.assert_not_called()
                filesystem.assert_not_called()
            self.assertFalse(output.parent.exists())

    def test_reject_mismatched_state_bootstrap_and_root_profile(self):
        for where, field, value in (('sd', 'state_uuid', 'd' * 36), ('sd', 'kernel_release', 'other'),
                                    ('roots', 'management_runtime', {}), ('roots', 'package_state', {}), ('sd', 'package_state_profile', 'wrong'), ('roots', 'slot_bytes', 1)):
            values = {'roots': copy.deepcopy(self.roots), 'state': copy.deepcopy(self.state), 'sd': copy.deepcopy(self.sd)}
            values[where][field] = value
            with self.subTest(where=where, field=field), self.assertRaises(ValueError):
                IMAGE.validate_pairing(values['roots'], values['state'], values['sd'], self.diag)
        changed = copy.deepcopy(self.sd)
        changed['boot_inputs']['board.dtb']['sha256'] = 'f' * 64
        with self.assertRaises(ValueError):
            IMAGE.validate_pairing(self.roots, self.state, changed, self.diag)

    def test_sparse_placement_preserves_source_and_zero_padding(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, target = root / 'source', root / 'target'
            data = b'header' + bytes(2 * 1024**2) + b'tail'
            source.write_bytes(data)
            fd = os.open(target, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.ftruncate(fd, len(data) + 8192)
                IMAGE.place_sparse(source, fd, 4096, len(data))
            finally:
                os.close(fd)
            self.assertEqual(target.read_bytes(), bytes(4096) + data + bytes(4096))
            self.assertEqual(source.read_bytes(), data)

    def test_sparse_placement_rejects_devices_symlinks_and_bounds(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source'
            source.write_bytes(b'test')
            alias = root / 'alias'
            alias.symlink_to(source)
            fd = os.open(root / 'target', os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.ftruncate(fd, 8)
                for path, offset, size in ((alias, 0, 4), (source, 7, 4), (source, 0, 5), (source, -1, 4)):
                    with self.subTest(path=path, offset=offset, size=size), self.assertRaises(ValueError):
                        IMAGE.place_sparse(path, fd, offset, size)
            finally:
                os.close(fd)
            fd = os.open('/dev/null', os.O_RDONLY)
            try:
                with self.assertRaises(ValueError):
                    IMAGE.place_sparse(source, fd, 0, 4)
            finally:
                os.close(fd)

    def test_output_storage_rejection_precedes_staging(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directories = []
            for name, manifest in (('roots', self.roots), ('state', self.state), ('sd', self.sd)):
                directory = root / name
                directory.mkdir()
                (directory / 'manifest.json').write_text(json.dumps(manifest))
                directories.append(directory)
            (root / 'configs').mkdir()
            shutil.copyfile(REPO / 'configs/host-boot.cmd', root / 'configs/host-boot.cmd')
            output = root / 'out/nvme-image-fixture'
            with patch.object(IMAGE.os, 'geteuid', return_value=0), patch.object(IMAGE, 'REPO', root), patch.object(IMAGE.HOST, 'validate_builder_packages', return_value={}), patch.object(IMAGE.STATE, 'validate_workspace_storage', side_effect=ValueError('SD/RAM refused')) as guard:
                with self.assertRaisesRegex(ValueError, 'SD/RAM refused'):
                    IMAGE.build(*directories, root, output, 250_000_000_000)
                guard.assert_called_once_with(root)
            self.assertFalse(output.parent.exists())

    def test_write_all_rejects_no_progress(self):
        with patch.object(IMAGE.os, 'pwrite', return_value=0):
            with self.assertRaises(OSError):
                IMAGE.write_all(99, b'content', 0)

    @unittest.skipUnless(all(shutil.which(tool) for tool in ('mke2fs', 'e2fsck', 'debugfs')), 'filesystem tools required')
    def test_guests_filesystem_journal_identity_and_private_root(self):
        import struct
        import uuid
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            seed = root / 'seed'
            seed.mkdir(mode=0o700)
            image = root / 'guests.ext4'
            identity = str(uuid.uuid4())
            IMAGE.make_filesystem(image, 32 * 1024**2, 'A733GUESTS', identity, seed)
            with image.open('rb') as source:
                source.seek(1024)
                sb = source.read(1024)
            self.assertEqual(str(uuid.UUID(bytes=sb[104:120])), identity)
            self.assertTrue(struct.unpack_from('<I', sb, 92)[0] & 4)
            info = IMAGE.debugfs(image, 'stat /').decode()
            self.assertRegex(info, r'Mode:\s+0700')


if __name__ == '__main__':
    unittest.main()
