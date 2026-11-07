"""Small regular ext4 fixtures; no production image or device access."""
import base64
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('state_readback', REPO / 'build/verify-host-state.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
UUID = 'fc8bbef6-9385-4ddb-90ba-e41eaccc035b'
SIZE = 32 * 1024**2


def snapshot(root):
    members, staging, links = {}, {}, {}
    for path in [root, *sorted(root.rglob('*'))]:
        info = path.lstat()
        name = str(path.relative_to(root))
        item = dict(mode=stat.S_IMODE(info.st_mode), uid=info.st_uid, gid=info.st_gid,
                    mtime_ns=info.st_mtime_ns, xattrs={key: base64.b64encode(os.getxattr(path, key, follow_symlinks=False)).decode()
                    for key in os.listxattr(path, follow_symlinks=False)})
        record = dict(mode='%04o' % item['mode'], uid=item['uid'], gid=item['gid'],
                      xattrs={key: __import__('hashlib').sha256(base64.b64decode(value)).hexdigest()
                              for key, value in item['xattrs'].items()})
        if path.is_symlink():
            item.update(type='symlink', link=os.readlink(path))
            record.update(type='symlink', target=item['link'])
        elif path.is_dir():
            item['type'] = 'dir'; record['type'] = 'directory'
        else:
            item.update(type='file', data=m.pin(path.read_bytes()))
            record.update(type='file', **item['data'])
            if info.st_ino in links:
                item.update(type='hardlink', link=links[info.st_ino])
            links[info.st_ino] = name
        members[name], staging[name] = item, record
    return members, staging


class StateVerifierTests(unittest.TestCase):
    def fixture(self, parent):
        root = parent / 'stage'; root.mkdir(mode=0o700); root.chmod(0o700)
        for name, mode in [('identity', 0o700), ('identity/ssh', 0o700), ('root', 0o700),
                            ('root/.ssh', 0o700), ('config', 0o700), ('boot', 0o700),
                            ('var', 0o755), ('var/lib', 0o755), ('var/lib/systemd', 0o755),
                            ('var/lib/systemd/timesync', 0o755)]:
            (root / name).mkdir(mode=mode)
            (root / name).chmod(mode)  # Fixture metadata must not depend on the suite's umask.
        values = dict(SCHEMA='1', SLOT='A', ROOT_UUID=m.R.SLOT_UUIDS['A'],
                      ROOT_MARKER_SHA256='a' * 64, KERNEL_RELEASE='6.6.98-a733-diag')
        selection = ''.join(key + '=' + values[key] + '\n' for key in m.FIELDS).encode()
        management = json.loads((REPO / 'docs/evidence/operator-inputs-20261105.json').read_bytes())['management']
        manifest = dict(machine_id='1' * 32, state_uuid=UUID, management=management)
        receipt = {'verification': {'boot_selection': {'pin': m.pin(selection), 'selector': values}}}
        for name, data, mode in [('.a733-state', ('a733-state-v1\n' + UUID + '\n').encode(), 0o600),
                                 ('identity/machine-id', ('1' * 32 + '\n').encode(), 0o644),
                                 ('root/.ssh/authorized_keys', b'ssh-ed25519 example\n', 0o600),
                                 ('config/management.json', (json.dumps(management, sort_keys=True, separators=(',', ':')) + '\n').encode(), 0o600),
                                 ('boot/selected.conf', selection, 0o600)]:
            (root / name).write_bytes(data); (root / name).chmod(mode)
        members, staging = snapshot(root)
        # Non-root host fixtures model root-produced metadata; actual image tests
        # use noncritical data and test the generic reader separately below.
        for name, item in members.items():
            item['uid'] = item['gid'] = 996 if name == 'var/lib/systemd/timesync' else 0
            staging[name]['uid'] = item['uid']; staging[name]['gid'] = item['gid']
        return root, members, staging, manifest, receipt

    def test_semantic_overlay_and_staging_binding(self):
        with tempfile.TemporaryDirectory(prefix='a733-state-') as directory:
            _, members, staging, manifest, receipt = self.fixture(Path(directory))
            m.semantic_members(members, manifest, receipt, staging)
            for name, field, value in [('identity/machine-id', 'mode', 0o600),
                                        ('.a733-state', 'data', m.pin(b'bad marker')),
                                        ('config/management.json', 'data', m.pin(b'{}\n')),
                                        ('boot/selected.conf', 'data', m.pin(b'bad selector'))]:
                bad, stage = copy.deepcopy(members), copy.deepcopy(staging)
                bad[name][field] = value
                if field == 'mode': stage[name]['mode'] = '%04o' % value
                else: stage[name].update(value)
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'critical state'):
                    m.semantic_members(bad, manifest, receipt, stage)
            bad = copy.deepcopy(staging); bad['identity']['xattrs'] = {'user.fake': '0' * 64}
            with self.assertRaisesRegex(ValueError, 'raw xattr'):
                m.semantic_members(members, manifest, receipt, bad)
            for directory in ('identity/ssh', 'var/lib/systemd/timesync'):
                bad, stage = copy.deepcopy(members), copy.deepcopy(staging)
                name = directory + '/forbidden'
                bad[name] = dict(members['identity/machine-id']); stage[name] = dict(staging['identity/machine-id'])
                with self.assertRaisesRegex(ValueError, 'not empty'):
                    m.semantic_members(bad, manifest, receipt, stage)
            bad = copy.deepcopy(members); bad['device'] = dict(members['identity/machine-id'], type='char', device=[1, 3])
            with self.assertRaisesRegex(ValueError, 'special node'):
                m.semantic_members(bad, manifest, receipt, staging)


    def closed_receipt(self):
        producer = json.loads((REPO / 'docs/evidence/production-host-roots-20261107.json').read_bytes())
        operator = json.loads((REPO / 'docs/evidence/operator-inputs-20261105.json').read_bytes())
        policy = dict(producer['policy'], operator=operator, producer=producer,
                      metadata_helper=m.pin((REPO / 'build/rootfs-metadata.py').read_bytes()))
        names = ('build/host-payload.py', 'build/launch-host-roots.py', 'build/userspace-profiles.py',
                 'build/timesync-state.py', 'build/rootfs-metadata.py', 'configs/host-userspace-profiles.lock.json',
                 'docs/evidence/diagnostic-20261104.json', 'docs/evidence/operator-inputs-20261105.json',
                 'docs/evidence/production-host-roots-20261107.json', 'build/host-state.py', 'build/root-selection.py',
                 'build/host-initramfs.py', 'build/host-storage-tools.py', 'configs/host_config.py',
                 'configs/package_state.py', 'build/host-userspace.sh', 'configs/builder-packages.lock.tsv',
                 'configs/host-userspace-packages.lock.tsv', 'configs/host-userspace-package-hashes.lock.tsv')
        records = {name: dict(m.pin((REPO / name).read_bytes()), git_blob='a' * 40) for name in names}
        return dict(schema=1, success=True, state='complete', hardware_acceptance=False,
                    state_bytes=m.STATE_BYTES, state_uuid=UUID, userspace_profile=m.R.PROFILE,
                    metadata_profile=m.R.METADATA, host_inventory_unchanged=True, mapped_processes_final=[],
                    phase_mounts_final=[], sources={'revision': 'a' * 40, 'files': records}, policy=policy)

    def test_closed_policy_and_manifest_reject_substitution(self):
        receipt = self.closed_receipt()
        policy = m.source_policy(receipt)
        for field, value in [('success', False), ('state_bytes', SIZE), ('schema', True),
                             ('userspace_profile', 'baseline-20261104'), ('phase_mounts_final', ['mounted'])]:
            bad = copy.deepcopy(receipt); bad[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): m.source_policy(bad)
        bad = copy.deepcopy(receipt); bad['policy']['operator']['classification'] = 'substituted'
        with self.assertRaisesRegex(ValueError, 'committed evidence'): m.source_policy(bad)
        receipt['verification'] = {'boot_selection': {'pin': m.pin(b'selector'), 'selector': {}}}
        manifest = dict(schema=1, board='Orange Pi 4 Pro A733', state_bytes=m.STATE_BYTES,
                        state_uuid=UUID, machine_id='1' * 32, management=policy['operator']['management'],
                        admin_public_key_fingerprints=[item['fingerprint'] for item in policy['operator']['keys']],
                        boot_selection={}, filesystem_directories={
                        '/': {'type': 'directory', 'mode': '0755', 'uid': 0, 'gid': 0},
                        '/lost+found': {'type': 'directory', 'mode': '0700', 'uid': 0, 'gid': 0}}, sources={'rootfs.tar.gz': policy['archive'],
                        'userspace_profile': policy['binding'], 'timesync_state': policy['clock'],
                        'rootfs_metadata': {'profile': m.R.METADATA, 'helper': policy['metadata_helper']},
                        'operator_config': policy['operator']['input_records']['management.json'],
                        'operator_public_key': policy['operator']['input_records']['admin.pub'],
                        'boot_selection_input': m.pin(b'selector')},
                        files={'state.ext4': {'bytes': m.STATE_BYTES, 'sha256': 'a' * 64},
                               'state.ext4.gz': m.pin(b'gzip'), 'staging-contents.json': m.pin(b'{}')})
        for filename, expected in [('timesync-state.py', policy['clock']['helper']),
                                   ('rootfs-metadata.py', policy['metadata_helper']),
                                   ('userspace-profiles.py', policy['binding']['resolver']),
                                   ('host-userspace-profiles.lock.json', policy['binding']['profile_lock'])]:
            manifest['files']['provenance/' + filename] = expected
        for source, record in receipt['sources']['files'].items():
            manifest['files']['provenance/' + Path(source).name] = {key: record[key] for key in ('bytes', 'sha256')}
        m.manifest_policy(manifest, receipt, policy)
        for field, value in [('machine_id', '0' * 32), ('admin_public_key_fingerprints', []),
                             ('state_uuid', m.R.SLOT_UUIDS['A']), ('schema', True)]:
            bad = copy.deepcopy(manifest); bad[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): m.manifest_policy(bad, receipt, policy)
        bad = copy.deepcopy(manifest); del bad['files']['state.ext4.gz']
        with self.assertRaisesRegex(ValueError, 'missing state'): m.manifest_policy(bad, receipt, policy)
        bad = copy.deepcopy(manifest); bad['sources']['operator_public_key'] = m.pin(b'substitution')
        with self.assertRaisesRegex(ValueError, 'source binding'): m.manifest_policy(bad, receipt, policy)


    def test_independent_parent_marker_rejects_agreed_wrong_selector(self):
        with tempfile.TemporaryDirectory(prefix='a733-state-') as directory:
            root = Path(directory)
            stage = root / 'stage'; stage.mkdir()
            payload_dir = stage / 'usr/lib/orangepi-hypervisor'; payload_dir.mkdir(parents=True)
            (payload_dir / 'payload.json').write_bytes(b'{"root":"independently verified"}\n')
            members, _ = snapshot(stage)
            for item in members.values(): item['uid'] = item['gid'] = 0
            proof = root / 'expected.json'
            proof.write_text(json.dumps({'schema': 1, 'slot': 'A', 'members': members}))
            proof_pin = m.pin(proof.read_bytes())
            policy = {'producer': {'expected_trees': {'A': {'expected': proof_pin, 'members': len(members)}},
                                   'readback': {'slots': {'A': {'expected': proof_pin}}}}}
            marker_sha = members['usr/lib/orangepi-hypervisor/payload.json']['data']['sha256']
            with m.R.Held(proof, proof_pin) as held:
                self.assertEqual(m.parent_marker_binding(held, policy, {'ROOT_MARKER_SHA256': marker_sha})['sha256'], marker_sha)
                # A self-consistent producer selector and state snapshot still
                # cannot substitute a marker absent from the parent root proof.
                wrong_selector = {'ROOT_MARKER_SHA256': 'b' * 64}
                with self.assertRaisesRegex(ValueError, 'independent parent root proof'):
                    m.parent_marker_binding(held, policy, wrong_selector)
                bad_policy = copy.deepcopy(policy)
                bad_policy['producer']['readback']['slots']['A']['expected']['sha256'] = 'c' * 64
                with self.assertRaisesRegex(ValueError, 'proof pin'):
                    m.parent_marker_binding(held, bad_policy, {'ROOT_MARKER_SHA256': marker_sha})

    def test_uuid_validation(self):
        for value in ('0' * 32, str(__import__('uuid').UUID(int=0)), m.R.SLOT_UUIDS['A'], UUID.upper()):
            with self.assertRaises(ValueError): m.state_uuid(value)
        self.assertEqual(m.state_uuid(UUID), UUID)

    def test_output_rejected_before_creation_and_failure_journal(self):
        with tempfile.TemporaryDirectory(prefix='a733-state-') as directory:
            root = Path(directory); receipt = root / 'receipt.json'; receipt.write_text('{}')
            with self.assertRaisesRegex(ValueError, 'outside producer'):
                m.new_output(root / 'forbidden', receipt)
            self.assertFalse((root / 'forbidden').exists())
            outside = root / 'outside'; outside.mkdir(); output = outside / 'new'
            # The native checkout is user owned: test output creation mechanics
            # via the existing physical-only helper, never weaken CLI policy.
            with patch.object(m, 'new_output', side_effect=lambda p, _: m.R.new_output(p)):
                with self.assertRaisesRegex(ValueError, 'receipt SHA256'):
                    m.readback(receipt, '0' * 64, output)
            self.assertEqual(json.loads((output / 'receipt.json').read_bytes())['state'], 'failed')
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o700)
            with self.assertRaises(ValueError): m.R.new_output(output)


    def test_trusted_output_ancestry_and_artifact_closure(self):
        with tempfile.TemporaryDirectory(prefix='a733-state-') as directory:
            root = Path(directory); root.chmod(0o700)
            production = root / 'production'; production.mkdir()
            (production / 'manifest.json').write_text('{}')
            (production / 'file').write_bytes(b'file')
            fd = os.open(production, os.O_RDONLY | os.O_DIRECTORY)
            try:
                m.artifact_namespace(fd, {'file': m.pin(b'file')})
                (production / 'extra').write_bytes(b'extra')
                with self.assertRaisesRegex(ValueError, 'namespace closure'):
                    m.artifact_namespace(fd, {'file': m.pin(b'file')})
                (production / 'extra').unlink(); (production / 'extra').symlink_to('file')
                with self.assertRaisesRegex(ValueError, 'special/aliased'):
                    m.artifact_namespace(fd, {'file': m.pin(b'file')})
            finally: os.close(fd)
            shared = root / 'shared'; shared.mkdir(); shared.chmod(0o777)
            actual = Path.lstat
            def pretend_root(path, *args, **kwargs):
                info = actual(path, *args, **kwargs)
                fields = list(info); fields[4] = 0
                return os.stat_result(fields)
            # Simulate trusted root ownership only; real physical inode and mode
            # checks remain active, including sticky /tmp above private barrier.
            with patch.object(Path, 'lstat', pretend_root):
                with self.assertRaisesRegex(ValueError, 'unsafe shared'):
                    m.new_output(shared / 'forbidden', production / 'receipt.json')
                self.assertFalse((shared / 'forbidden').exists())
                fresh = m.new_output(root / 'fresh', production / 'receipt.json')
                self.assertEqual(stat.S_IMODE(fresh.stat().st_mode), 0o700)
            if os.geteuid() != 0:
                with self.assertRaisesRegex(ValueError, 'root-owned'):
                    m.new_output(root / 'not-root-owned', production / 'receipt.json')

    def test_sibling_reader_pin_and_no_arbitrary_import(self):
        with patch.object(m, 'READER_SHA256', '0' * 64):
            with self.assertRaisesRegex(ValueError, 'SHA256'): m.load_reader()
        self.assertEqual(m.R.__file__, str(REPO / 'build/verify-host-roots.py'))

    @unittest.skipUnless(shutil.which('mke2fs') and shutil.which('debugfs'), 'ext4 tools required')
    def test_actual_journal_writable_filesystem_full_readback(self):
        with tempfile.TemporaryDirectory(prefix='a733-state-') as directory:
            work = Path(directory); root = work / 'source'; root.mkdir(mode=0o700); root.chmod(0o700)
            (root / 'dir').mkdir(); (root / 'dir/file').write_bytes(b'payload\x00\xff')
            os.setxattr(root / 'dir/file', 'user.binary', b'\x00binary\xff')
            (root / 'hard').hardlink_to(root / 'dir/file')
            (root / 'short').symlink_to('dir/file')
            (root / 'long').symlink_to('dir/' + 'long-target-' * 10)
            members, _ = snapshot(root)
            self.assertEqual(members['.']['mode'], 0o700)
            members['.'].update(uid=0, gid=0, mode=0o755)
            image = work / 'image.ext4'
            with image.open('wb') as stream: stream.truncate(SIZE)
            subprocess.run(['mke2fs', '-q', '-F', '-t', 'ext4', '-b', '4096', '-O', 'has_journal',
                            '-E', 'lazy_itable_init=0,lazy_journal_init=0', '-U', UUID,
                            '-d', str(root), str(image)], check=True)
            scratch_parent = work / 'readbacks'; scratch_parent.mkdir()
            with m.R.Held(image) as held:
                created = m.superblock(held.fd, UUID, SIZE)
                def check(tree):
                    scratch = scratch_parent / ('read-' + str(len(list(scratch_parent.glob('read-*'))))); scratch.mkdir()
                    return m.R.verify(held.fd, tree, scratch, root_time=created, progress=False)
                result = check(members)
                self.assertEqual(result['raw_xattrs'], 1)
                self.assertEqual(result['character_devices'], 0)
                self.assertEqual(result['hardlink_groups'], [['dir/file', 'hard']])
                self.assertFalse(result['hardware_acceptance'])
                bad = copy.deepcopy(members); bad['dir/file']['data'] = bad['hard']['data'] = m.pin(b'incorrect')
                with self.assertRaises(ValueError): check(bad)
                for change in ('missing', 'extra', 'link', 'attrs'):
                    bad = copy.deepcopy(members)
                    if change == 'missing': del bad['long']
                    elif change == 'extra': bad['extra'] = dict(bad['dir/file'])
                    elif change == 'link': bad['short']['link'] = 'bad/file'
                    else:
                        for name in ('dir/file', 'hard'): bad[name]['xattrs']['user.binary'] = base64.b64encode(b'\x00binary\x00').decode()
                    with self.subTest(change=change), self.assertRaises(ValueError): check(bad)
                with self.assertRaisesRegex(ValueError, 'UUID'):
                    m.superblock(held.fd, '12345678-1234-4234-8234-123456789abc', SIZE)
                with self.assertRaisesRegex(ValueError, 'size'):
                    m.superblock(held.fd, UUID, SIZE + 4096)
                original = os.pread(held.fd, 1024, 1024)
                for field, value in ((92, struct.unpack_from('<I', original, 92)[0] & ~4),
                                     (100, struct.unpack_from('<I', original, 100)[0] | 0x1000)):
                    changed = bytearray(original); struct.pack_into('<I', changed, field, value)
                    with patch.object(m.os, 'pread', return_value=bytes(changed)):
                        with self.assertRaisesRegex(ValueError, 'journal and writable'):
                            m.superblock(held.fd, UUID, SIZE)


if __name__ == '__main__': unittest.main()
