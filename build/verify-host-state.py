#!/usr/bin/env python3
"""Read-only complete production state ext4 verification; no hardware acceptance.

Uses the SHA-pinned sibling root verifier's generic inode reader. No mount,
repair, physical device access, package execution, or image writes are used.
"""
import argparse
import base64
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import sys
import types
import uuid

READER_SHA256 = '80e843d499bcc2f6d299d6022dc7edf83d73842405e95b38b0f32eee72dcda30'
STATE_BYTES = 8 * 1024**3
PHASE_PARENT = Path('/var/lib/a733-clean-replay-20261106')
FIELDS = ('SCHEMA', 'SLOT', 'ROOT_UUID', 'ROOT_MARKER_SHA256', 'KERNEL_RELEASE')


def load_reader():
    """Execute only authenticated bytes of our physical sibling, never sys.path."""
    path = Path(os.path.abspath(__file__)).parent / 'verify-host-roots.py'
    held = []
    try:
        directory = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        held.append(directory)
        for name in path.parts[1:-1]:
            directory = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            held.append(directory)
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        held.append(fd)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > 1024**2:
            raise ValueError('reader must be a bounded single-link regular sibling')
        data = os.read(fd, 1024**2 + 1)
        if hashlib.sha256(data).hexdigest() != READER_SHA256:
            raise ValueError('trusted sibling reader SHA256 differs')
        module = types.ModuleType('state_trusted_reader')
        module.__file__ = str(path)
        exec(compile(data, str(path), 'exec'), module.__dict__)
        if module.signature(before) != module.signature(os.fstat(fd)):
            raise ValueError('reader changed during loading')
        with module.Held(path, {'bytes': len(data), 'sha256': READER_SHA256}):
            pass
        return module
    finally:
        for fd in reversed(held):
            os.close(fd)


R = load_reader()
require = R.require


def pin(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def state_uuid(value):
    require(type(value) is str and str(uuid.UUID(value)) == value and uuid.UUID(value).int != 0
            and value not in R.SLOT_UUIDS.values(), 'invalid state UUID')
    return value


def new_output(output, receipt):
    output, receipt = R.physical(output), R.physical(receipt)
    require(not output.is_relative_to(receipt.parent), 'output must be outside producer phase')
    # A sticky shared ancestor is allowed only above a private root-owned barrier.
    private = False
    for parent in output.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0, 'root-owned physical output ancestry required')
        if info.st_mode & 0o022:
            require(private and info.st_mode & stat.S_ISVTX, 'unsafe shared output ancestor')
        else:
            private = private or stat.S_IMODE(info.st_mode) == 0o700
    return R.new_output(output)


def source_policy(receipt):
    require(type(receipt.get('schema')) is int and receipt['schema'] == 1
            and receipt.get('success') is True and receipt.get('state') == 'complete'
            and receipt.get('hardware_acceptance') is False and receipt.get('state_bytes') == STATE_BYTES
            and receipt.get('userspace_profile') == R.PROFILE and receipt.get('metadata_profile') == R.METADATA,
            'producer did not complete closed state profile')
    state_uuid(receipt['state_uuid'])
    for name, record in receipt['sources']['files'].items():
        R.safe_relative(name)
        R.validate_pin({key: record[key] for key in ('bytes', 'sha256')}, allow_empty=True)
        require(re.fullmatch('[0-9a-f]{40}', record.get('git_blob', '')), 'source Git blob missing')
    require(receipt.get('host_inventory_unchanged') is True and receipt.get('mapped_processes_final') == []
            and receipt.get('phase_mounts_final') == [], 'producer isolation postconditions differ')
    policy = receipt['policy']
    require(set(policy) == {'archive', 'binding', 'clock', 'diagnostic', 'metadata_helper', 'operator', 'producer'},
            'state policy closure differs')
    # Reuse closed root/profile/source checks without treating state as a root.
    adapter = dict(receipt, slot_bytes=R.SLOT_BYTES,
                   policy={key: policy[key] for key in ('archive', 'binding', 'clock', 'diagnostic')})
    root_policy = R.source_policy(adapter)
    require(policy['metadata_helper'] == root_policy['metadata_helper'], 'metadata helper binding differs')
    operator, producer = policy['operator'], policy['producer']
    for value, expected in ((operator, '2ea90bd5b462320675b062c0b1c1230224913f459cdaf890cded4dfb434323c7'),
                            (producer, '08cb041263fd4d24770cd80615745c46b9136940737eb6acbb07c1db39bcec8c')):
        require(hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
                == expected, 'committed evidence policy differs')
    require(operator.get('management') == {'schema': 1, 'hostname': 'a733-host',
            'management_mac': '10:f4:ae:90:5e:57', 'management_cidr': '0.0.0.0/0', 'dhcp': True},
            'operator management differs')
    require(operator.get('input_records') == {
        'admin.pub': {'bytes': 663, 'sha256': 'ded86d3be3f910e5b33d7938d21245a149e2b19dc517f0ac4f582b5b0c52f68b'},
        'management.json': {'bytes': 136, 'sha256': '382ccdfb6273614a23f99034dd269740166c2786d65a001ec51deecbe4683491'}},
        'operator input pins differ')
    require([item.get('fingerprint') for item in operator['keys']] == [
        'SHA256:WVU8wsBgfy5mGFNAZMdrvf+a3cTYo85B8llJlHIhUKA',
        'SHA256:Dw9WPpqQ741skiDShdwAP3qXQtpgxgbUSlCp2hmod7k'], 'operator fingerprints differ')
    require(producer.get('schema') == 1 and producer.get('slot_uuids') == R.SLOT_UUIDS
            and producer.get('slot_bytes') == R.SLOT_BYTES and producer.get('profile') == R.PROFILE
            and producer.get('policy') == {key: policy[key] for key in ('archive', 'binding', 'clock', 'diagnostic')},
            'production root policy differs')
    for name, evidence in (('docs/evidence/operator-inputs-20261105.json', operator),
                           ('docs/evidence/production-host-roots-20261107.json', producer)):
        record = receipt['sources']['files'][name]
        R.validate_pin({key: record[key] for key in ('bytes', 'sha256')})
        require(re.fullmatch('[0-9a-f]{40}', record.get('git_blob', '')), 'evidence Git blob missing')
        # Git source closure authenticates original JSON bytes; the policy embeds
        # its complete parsed value, cross-checked against held provenance below.
    return policy


def manifest_policy(manifest, receipt, policy):
    require(type(manifest.get('schema')) is int and manifest['schema'] == 1
            and manifest.get('board') == 'Orange Pi 4 Pro A733'
            and manifest.get('state_bytes') == STATE_BYTES
            and manifest.get('state_uuid') == receipt['state_uuid'], 'state manifest identity differs')
    sources = manifest['sources']
    for key, expected in (('rootfs.tar.gz', policy['archive']), ('userspace_profile', policy['binding']),
                          ('timesync_state', policy['clock']),
                          ('rootfs_metadata', {'profile': R.METADATA, 'helper': policy['metadata_helper']}),
                          ('operator_config', policy['operator']['input_records']['management.json']),
                          ('operator_public_key', policy['operator']['input_records']['admin.pub']),
                          ('boot_selection_input', receipt['verification']['boot_selection']['pin'])):
        require(sources.get(key) == expected, 'manifest source binding differs: ' + key)
    require(manifest.get('management') == policy['operator']['management']
            and manifest.get('admin_public_key_fingerprints') == [x['fingerprint'] for x in policy['operator']['keys']]
            and manifest.get('boot_selection') == receipt['verification']['boot_selection']['selector'],
            'manifest operator/selector semantics differ')
    require(type(manifest.get('machine_id')) is str and re.fullmatch('[0-9a-f]{32}', manifest['machine_id'])
            and manifest['machine_id'] != '0' * 32, 'invalid per-install machine-id')
    require(manifest.get('filesystem_directories') == {
        '/': {'type': 'directory', 'mode': '0755', 'uid': 0, 'gid': 0},
        '/lost+found': {'type': 'directory', 'mode': '0700', 'uid': 0, 'gid': 0}},
        'state mkfs directory metadata differs')
    files = manifest['files']
    require(type(files) is dict and 0 < len(files) <= R.MAX_MEMBERS, 'invalid artifact closure')
    for name, expected in files.items():
        R.safe_relative(name)
        R.validate_pin(expected, allow_empty=True)
        require(expected['bytes'] <= 2 * STATE_BYTES, 'oversized state artifact')
    require(files.get('state.ext4', {}).get('bytes') == STATE_BYTES
            and 'state.ext4.gz' in files and 'staging-contents.json' in files, 'missing state artifacts')
    for name, expected in (('timesync-state.py', policy['clock']['helper']),
                            ('rootfs-metadata.py', policy['metadata_helper']),
                            ('userspace-profiles.py', policy['binding']['resolver']),
                            ('host-userspace-profiles.lock.json', policy['binding']['profile_lock'])):
        require(files.get('provenance/' + name) == expected, 'state provenance differs')
    for name, expected in files.items():
        if name.startswith('provenance/'):
            candidates = [record for source, record in receipt['sources']['files'].items()
                          if Path(source).name == Path(name).name]
            require(any({k: record[k] for k in ('bytes', 'sha256')} == expected for record in candidates),
                    'provenance outside full source closure: ' + name)
    for source in ('build/host-state.py', 'build/host-payload.py', 'build/root-selection.py',
                   'build/host-initramfs.py', 'build/host-storage-tools.py', 'configs/host_config.py',
                   'configs/package_state.py', 'build/host-userspace.sh', 'configs/builder-packages.lock.tsv',
                   'configs/host-userspace-packages.lock.tsv', 'configs/host-userspace-package-hashes.lock.tsv'):
        record = receipt['sources']['files'][source]
        require(files.get('provenance/' + Path(source).name) ==
                {key: record[key] for key in ('bytes', 'sha256')}, 'required state recipe provenance differs')



def artifact_namespace(directory_fd, files):
    """Reject unlisted outputs and all nonregular/non-directory entries."""
    expected = set(files) | {'manifest.json'}
    directories = {'.'}
    for name in expected:
        directories.update(str(parent) for parent in Path(name).parents)
    found, found_dirs = set(), {'.'}

    def scan(fd, prefix=''):
        before = R.signature(os.fstat(fd))
        with os.scandir(fd) as entries:
            for entry in entries:
                name = prefix + entry.name
                require(len(found) + len(found_dirs) <= R.MAX_MEMBERS, 'excessive artifact namespace')
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    found_dirs.add(name)
                    child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    try:
                        scan(child, name + '/')
                    finally:
                        os.close(child)
                else:
                    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'special/aliased artifact refused')
                    found.add(name)
        require(before == R.signature(os.fstat(fd)), 'artifact directory changed')

    scan(directory_fd)
    require(found == expected and found_dirs == directories, 'artifact namespace closure differs')


def semantic_members(members, manifest, receipt, staging):
    R.validate_members(members)
    require(all(x['type'] not in ('char', 'block') for x in members.values()), 'state special node forbidden')
    require(set(staging) == set(members), 'staging namespace differs')
    require(not any(Path(name).name.startswith('ssh_host_') for name in members), 'preseeded SSH host key refused')
    for name, item in members.items():
        record = staging[name]
        kind = {'dir': 'directory', 'file': 'file', 'hardlink': 'file', 'symlink': 'symlink'}[item['type']]
        require((record.get('type'), record.get('mode'), record.get('uid'), record.get('gid')) ==
                (kind, '%04o' % item['mode'], item['uid'], item['gid']), 'staging metadata differs: ' + name)
        require(record.get('xattrs') == {k: hashlib.sha256(base64.b64decode(v, validate=True)).hexdigest()
                                        for k, v in item['xattrs'].items()}, 'staging raw xattr hash differs')
        if kind == 'file':
            require({k: record.get(k) for k in ('bytes', 'sha256')} == item['data'], 'staging file pin differs')
        elif kind == 'symlink':
            require(record.get('target') == item['link'], 'staging symlink differs')

    def entry(name, kind, mode, uid=0, gid=0, data=None, empty=False):
        item = members.get(name, {})
        require((item.get('type'), item.get('mode'), item.get('uid'), item.get('gid')) ==
                (kind, mode, uid, gid), 'critical state metadata differs: ' + name)
        if data is not None:
            require(item.get('data') == pin(data), 'critical state bytes differ: ' + name)
        if empty:
            require(not any(p.startswith(name + '/') for p in members), 'critical state directory not empty: ' + name)

    entry('.', 'dir', 0o700)
    entry('.a733-state', 'file', 0o600, data=('a733-state-v1\n' + manifest['state_uuid'] + '\n').encode())
    entry('identity', 'dir', 0o700)
    entry('identity/ssh', 'dir', 0o700, empty=True)
    entry('identity/machine-id', 'file', 0o644, data=(manifest['machine_id'] + '\n').encode())
    entry('root', 'dir', 0o700)
    entry('root/.ssh', 'dir', 0o700)
    entry('root/.ssh/authorized_keys', 'file', 0o600)
    entry('config', 'dir', 0o700)
    entry('config/management.json', 'file', 0o600, data=(json.dumps(manifest['management'], sort_keys=True, separators=(',', ':')) + '\n').encode())
    entry('boot', 'dir', 0o700)
    selection = receipt['verification']['boot_selection']
    values = selection['selector']
    require(set(values) == set(FIELDS) and values['SCHEMA'] == '1' and values['SLOT'] == 'A'
            and values['ROOT_UUID'] == R.SLOT_UUIDS['A'] and values['KERNEL_RELEASE'] == '6.6.98-a733-diag'
            and re.fullmatch('[0-9a-f]{64}', values['ROOT_MARKER_SHA256']), 'incompatible initial selector')
    data = ''.join(key + '=' + values[key] + '\n' for key in FIELDS).encode('ascii')
    require(pin(data) == selection['pin'], 'selector serialized pin differs')
    entry('boot/selected.conf', 'file', 0o600, data=data)
    entry('var/lib/systemd/timesync', 'dir', 0o755, 996, 996, empty=True)



def parent_marker_binding(held, policy, selector):
    """Bind selector to the already independently read-back parent root tree."""
    producer = policy['producer']
    expected = producer['expected_trees']['A']
    require(expected['expected'] == producer['readback']['slots']['A']['expected']
            and held.pin == expected['expected'], 'parent root proof pin differs')
    proof = R.json_fd(held)
    require(type(proof.get('schema')) is int and proof['schema'] == 1 and proof.get('slot') == 'A'
            and type(proof.get('members')) is dict and len(proof['members']) == expected['members'],
            'parent root proof identity differs')
    R.validate_members(proof['members'])
    marker = proof['members']['usr/lib/orangepi-hypervisor/payload.json']
    require(marker['type'] == 'file' and marker['uid'] == marker['gid'] == 0,
            'parent root marker metadata differs')
    R.validate_pin(marker['data'])
    require(selector['ROOT_MARKER_SHA256'] == marker['data']['sha256'],
            'selected marker differs from independent parent root proof')
    return marker['data']


def superblock(fd, selected_uuid, size=STATE_BYTES):
    block = os.pread(fd, 1024, 1024)
    require(len(block) == 1024 and block[56:58] == b'\x53\xef', 'invalid state ext4 superblock')
    require(str(uuid.UUID(bytes=block[104:120])) == state_uuid(selected_uuid), 'state filesystem UUID differs')
    require(struct.unpack_from('<I', block, 92)[0] & 4
            and not struct.unpack_from('<I', block, 100)[0] & 0x1000, 'state requires journal and writable filesystem')
    blocks = struct.unpack_from('<I', block, 4)[0]
    if struct.unpack_from('<I', block, 96)[0] & 0x80:
        blocks |= struct.unpack_from('<I', block, 336)[0] << 32
    require((1024 << struct.unpack_from('<I', block, 24)[0]) * blocks == size
            and os.fstat(fd).st_size == size, 'state filesystem/image size differs')
    created = struct.unpack_from('<I', block, 264)[0]
    require(946684800 <= created < 2**32, 'unsupported state mkfs time')
    return created


def readback(receipt_path, receipt_sha256, output):
    require(re.fullmatch('[0-9a-f]{64}', receipt_sha256 or ''), 'invalid receipt SHA256')
    receipt_path = R.physical(receipt_path)
    output = new_output(output, receipt_path)
    report = {'schema': 1, 'success': False, 'state': 'verifying', 'hardware_acceptance': False,
              'classification': 'component state filesystem readback; no hardware acceptance',
              'independent_expected_stage_correctness': False, 'reader_sha256': READER_SHA256}
    try:
        with contextlib.ExitStack() as stack:
            hold = lambda path, expected=None, bound=None: stack.enter_context(R.Held(path, expected, bound))
            controller = hold(Path(os.path.abspath(__file__)), bound=1024**2)
            report['controller'] = controller.pin
            helper = hold(Path(os.path.abspath(__file__)).parent / 'verify-host-roots.py',
                          {'bytes': 33471, 'sha256': READER_SHA256})
            report['reader'] = helper.pin
            receipt_file = hold(receipt_path, bound=R.MAX_JSON)
            require(receipt_file.pin['sha256'] == receipt_sha256, 'receipt SHA256 differs')
            receipt = R.json_fd(receipt_file)
            policy = source_policy(receipt)
            for name, held in (('build/verify-host-state.py', controller), ('build/verify-host-roots.py', helper)):
                require({key: receipt['sources']['files'][name][key] for key in ('bytes', 'sha256')}
                        == held.pin, 'controller/reader source closure differs')
            phase = R.physical(receipt['phase'])
            require(phase.parent == PHASE_PARENT and re.fullmatch(r'host-state-[A-Za-z0-9][A-Za-z0-9_-]{0,63}', phase.name)
                    and receipt_path == phase / 'receipt.json', 'unexpected producer phase')
            production = phase / R.safe_relative(receipt['output'])
            require(production == phase / 'work/project/out' / phase.name, 'state output location differs')
            manifest_file = hold(production / 'manifest.json', receipt['verification']['manifest'], R.MAX_JSON)
            manifest = R.json_fd(manifest_file)
            manifest_policy(manifest, receipt, policy)
            artifact_namespace(manifest_file.parents[-1][0], manifest['files'])
            artifacts = {name: hold(production / R.safe_relative(name), expected)
                         for name, expected in manifest['files'].items()}
            require(receipt['verification']['files_verified'] == len(artifacts)
                    and receipt['verification']['full_filesystem_readback'] is False, 'producer verification fields differ')
            expectations = receipt['readback_expectations']['state']
            require(expectations['independent_image_readback'] is False and expectations['character_devices'] == 0,
                    'producer readback expectations differ')
            expected_file = hold(phase / 'work/readback/state.expected.json', expectations['expected'], R.MAX_JSON)
            stable_file = hold(phase / 'work/readback/state.stable.json', expectations['stability'], R.MAX_JSON)
            expected, stable = R.json_fd(expected_file), R.json_fd(stable_file)
            require(expected.get('schema') == 1 and expected.get('state_uuid') == receipt['state_uuid']
                    and len(expected['members']) == expectations['members'], 'expected tree identity differs')
            require(stable == {'schema': 1, 'state_uuid': receipt['state_uuid'], 'creator_input_stable': True},
                    'producer staging stability differs')
            parent_phase = R.physical(policy['producer']['phase'])
            require(parent_phase == PHASE_PARENT / 'host-roots-timesync-01', 'unexpected parent root phase')
            parent_expected = hold(parent_phase / 'work/readback/slot-A.expected.json',
                                   policy['producer']['expected_trees']['A']['expected'], R.MAX_JSON)
            marker = parent_marker_binding(parent_expected, policy, receipt['verification']['boot_selection']['selector'])
            report['parent_root_marker'] = {'proof': parent_expected.pin, 'marker': marker,
                                          'scope': 'bound parent expected tree previously checked by independent full root readback'}
            semantic_members(expected['members'], manifest, receipt, R.json_fd(artifacts['staging-contents.json']))
            # Validate keys using public blob fingerprints, without logging key text.
            key_pin = expected['members']['root/.ssh/authorized_keys']['data']
            # Original operator input is held independently; comments are removed
            # by the producer's canonical public-key serialization.
            keys = hold(phase / 'inputs/admin.pub', policy['operator']['input_records']['admin.pub'], 16384)
            config_input = hold(phase / 'inputs/management.json', policy['operator']['input_records']['management.json'], 16384)
            require(R.json_fd(config_input) == manifest['management'], 'original operator management differs')
            selector_input = hold(phase / 'work/project/out/selection-A.conf', receipt['verification']['boot_selection']['pin'], 4096)
            archive_input = hold(phase / 'inputs/rootfs.tar.gz', policy['archive'])
            os.lseek(keys.fd, 0, os.SEEK_SET)
            lines = os.read(keys.fd, 16385).decode('ascii').splitlines()
            canonical, fingerprints = [], []
            for line in lines:
                parts = line.split()
                require(len(parts) >= 2 and parts[0] in ('ssh-rsa', 'ssh-ed25519'), 'invalid operator key')
                blob = base64.b64decode(parts[1], validate=True)
                canonical.append(parts[0] + ' ' + parts[1] + '\n')
                fingerprints.append('SHA256:' + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip('='))
            require(fingerprints == manifest['admin_public_key_fingerprints']
                    and pin(''.join(canonical).encode()) == key_pin, 'canonical authorized keys differ')
            work = output / 'filesystem'
            work.mkdir(mode=0o700)
            image = artifacts['state.ext4']
            created = superblock(image.fd, manifest['state_uuid'])
            # mke2fs initializes root permissions independently of -d source.
            # Preserve source/staging semantics above and enforce the closed
            # actual directory policy here; only root metadata differs.
            image_members = dict(expected['members'])
            image_members['.'] = dict(image_members['.'], mode=0o755, uid=0, gid=0)
            result = R.verify(image.fd, image_members, work, root_time=created)
            result.update(image=image.pin, expected=expected_file.pin, stability=stable_file.pin,
                          full_namespace_metadata_content_readback=True)
            result['root_metadata_policy'] = 'mkfs root 0755 root:root; source/staging root 0700 root:root'
            result['source_root_metadata'] = {key: expected['members']['.'][key] for key in ('mode', 'uid', 'gid')}
            result['filesystem_root_metadata'] = manifest['filesystem_directories']['/']
            report.update(filesystem=result, image=image.pin, state_manifest=manifest_file.pin,
                          producer_receipt=receipt_file.pin, expected=expected_file.pin, stability=stable_file.pin,
                          artifact_files_authenticated=len(artifacts), source_policy=policy,
                          sources=receipt['sources'])
        result['input_identity_unchanged'] = True
        report.update(success=True, state='complete', full_namespace_metadata_content_readback=True, input_identity_unchanged=True)
    except BaseException as error:
        report.update(success=False, state='failed', error=repr(error))
        raise
    finally:
        R.publish(output / 'receipt.json', report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('--receipt-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    require(sys.flags.isolated and sys.flags.dont_write_bytecode, 'invoke Python with -I -B')
    print(json.dumps(readback(args.receipt, args.receipt_sha256, args.output), sort_keys=True))


if __name__ == '__main__':
    main()
