#!/usr/bin/env python3
"""Create only a per-installation STATE regular image in the frozen disk builder.

Invoke with Python -I -B. Inputs and failed phases are retained. No installer,
physical storage write, network access or hardware acceptance is performed.
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
import subprocess
import sys
import tarfile
import types
import uuid

SELF = 'build/launch-host-state.py'
ROOTS = 'build/launch-host-roots.py'
VERIFIER = 'build/verify-host-roots.py'
OPERATOR = 'docs/evidence/operator-inputs-20261105.json'
PRODUCTION = 'docs/evidence/production-host-roots-20261107.json'
BASE = Path('/var/lib/a733-clean-replay-20261106')
PRODUCER_PHASE = BASE / 'host-roots-timesync-01'
PRODUCER_OUTPUT = PRODUCER_PHASE / 'work/project/out/host-payload-timesync-01'
STATE_BYTES = 8 * 1024**3
OLD_STATE_UUID = '00fd936c-49fb-4fea-87c8-928b9c1599e0'
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/nonexistent', 'LC_ALL': 'C', 'TZ': 'UTC'}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def module(name, data):
    result = types.ModuleType(name.replace('/', '_'))
    result.__file__ = name
    exec(compile(data, name, 'exec'), result.__dict__)
    return result


def phase_path(tag):
    require(isinstance(tag, str) and re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,63}', tag), 'unsafe tag')
    return BASE / ('host-state-' + tag)


def state_uuid(value):
    require(isinstance(value, str), 'explicit fresh state UUID required')
    parsed = uuid.UUID(value)
    require(str(parsed) == value and parsed.int != 0 and
            value not in ('a7330000-0000-4000-8000-00000000000a',
                          'a7330000-0000-4000-8000-00000000000b') and
            value != OLD_STATE_UUID, 'state UUID must be canonical, nonzero and fresh')
    return value


def syntax_path(path):
    require(path.is_absolute() and str(path) == os.path.abspath(path) and
            not any(path == p or p in path.parents for p in map(Path, ('/dev', '/proc', '/sys'))),
            'canonical absolute non-pseudo input required')


def contract(roots, project, contents):
    policy = roots.contract(project, contents)
    operator = json.loads(contents[OPERATOR])
    producer = json.loads(contents[PRODUCTION])
    require(operator['schema'] == producer['schema'] == 1 and producer['profile'] == roots.PROFILE
            and producer['metadata_profile'] == roots.METADATA and producer['policy'] == policy
            and producer['slot_bytes'] == roots.SLOT_BYTES and producer['slot_uuids'] == roots.SLOT_UUIDS,
            'committed production policy differs')
    require(producer['producer_revision'] == '6f2477be759b28829041f70e514dbdf678bb1a13',
            'unexpected root producer')
    return dict(policy, operator=operator, producer=producer,
                metadata_helper=roots.pin(contents['build/rootfs-metadata.py']))


def plan(options, manifest, policy):
    for path in (options.userspace, options.config, options.admin_keys, options.roots_receipt):
        syntax_path(path)
    require(options.roots_receipt == PRODUCER_PHASE / 'receipt.json', 'unexpected root receipt path')
    require(re.fullmatch('[0-9a-f]{64}', options.roots_receipt_sha256) and
            options.roots_receipt_sha256 == policy['producer']['producer_records']['receipt.json']['sha256'],
            'root receipt SHA differs from committed evidence')
    return {'schema': 1, 'phase': str(phase_path(options.tag)), 'sources': manifest,
            'policy': policy, 'userspace_input': str(options.userspace),
            'operator_config_input': str(options.config), 'admin_keys_input': str(options.admin_keys),
            'roots_receipt': str(options.roots_receipt), 'roots_receipt_sha256': options.roots_receipt_sha256,
            'state_uuid': state_uuid(options.state_uuid), 'state_bytes': STATE_BYTES,
            'userspace_profile': 'timesync-20261106', 'metadata_profile': 'fakeroot-raw-xattrs-v1',
            'output': 'work/project/out/host-state-' + options.tag, 'work_mode': 'disk',
            'initial_required_bytes': 'max(5 GiB selector peak, state recipe peak) + capsule + Git staging bound',
            'runtime_minimum_free_bytes': 3_000_000_000,
            'runtime_minimum_available_memory_bytes': 1_000_000_000,
            'minimum_initial_available_memory_bytes': 2 * 1024**3, 'timeout_seconds': 3600,
            'hardware_acceptance': False, 'full_filesystem_readback': False,
            'reproducibility': 'fresh machine identity intentionally differs on each build'}


def held_class(verifier):
    class InputHeld(verifier.Held):
        """Hold parent identities; allow sibling phase creation in trusted ancestry."""
        def check(self):
            require(verifier.signature(self.before) == verifier.signature(os.fstat(self.fd)) ==
                    verifier.signature(os.stat(self.path.name, dir_fd=self.parents[-1][0], follow_symlinks=False)),
                    'input identity changed')
            def identity(info):
                return info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid
            for descriptor, parent, name, before in self.parents:
                require(identity(before) == identity(os.fstat(descriptor)), 'input parent changed')
                if parent is not None:
                    require(identity(before) == identity(os.stat(name, dir_fd=parent, follow_symlinks=False)),
                            'input parent replaced')
    return InputHeld


def json_held(verifier, held):
    return verifier.json_fd(held)


def authenticate_roots(verifier, held, policy):
    receipt = json_held(verifier, held['receipt'])
    producer = policy['producer']
    require(receipt.get('success') is True and receipt.get('state') == 'complete'
            and receipt['sources']['revision'] == producer['producer_revision']
            and receipt['userspace_profile'] == 'timesync-20261106'
            and receipt['metadata_profile'] == 'fakeroot-raw-xattrs-v1'
            and receipt['slot_bytes'] == 4 * 1024**3
            and receipt['policy'] == {k: policy[k] for k in ('archive', 'binding', 'clock', 'diagnostic')}
            and receipt['verification']['manifest'] == held['manifest'].pin,
            'root producer receipt bindings differ')
    manifest = json_held(verifier, held['manifest'])
    require(manifest['slot_bytes'] == 4 * 1024**3 and manifest['slot_uuids'] == producer['slot_uuids']
            and manifest['files']['root-A.ext4'] == held['root'].pin,
            'selected root manifest differs')
    sources = manifest['sources']
    require(sources.get('rootfs.tar.gz') == policy['archive'] and
            sources.get('userspace_profile') == policy['binding'] and
            sources.get('timesync_state') == policy['clock'] and
            sources.get('rootfs_metadata') == {'profile': 'fakeroot-raw-xattrs-v1',
                                              'helper': policy['metadata_helper']},
            'selected root profile bindings differ')
    for name, expected in policy['diagnostic'].items():
        require(sources.get(name) == expected, 'selected root diagnostic binding differs')
    return manifest


def operator_inputs(roots, candidate, contents, options, policy, destination=None):
    common = module('configs/host_config.py', contents['configs/host_config.py'])
    result = {}
    for name, path in (('management.json', options.config), ('admin.pub', options.admin_keys)):
        roots.physical(path)
        info = path.lstat()
        require(info.st_uid == info.st_gid == 1000 and stat.S_IMODE(info.st_mode) == 0o600
                and info.st_nlink == 1 and stat.S_ISREG(info.st_mode), 'private operator input metadata differs')
        expected = policy['operator']['input_records'][name]
        _, data = candidate.authenticated_file(path, expected, retain=True,
                        destination=(destination / name) if destination is not None else None)
        after = path.lstat()
        require((info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid, info.st_nlink,
                 info.st_size, info.st_mtime_ns, info.st_ctime_ns) ==
                (after.st_dev, after.st_ino, after.st_mode, after.st_uid, after.st_gid, after.st_nlink,
                 after.st_size, after.st_mtime_ns, after.st_ctime_ns), 'operator input changed during validation')
        result[name] = data
    require(common.validate_config(result['management.json']) == policy['operator']['management'],
            'management policy differs')
    canonical, fingerprints = common.validate_public_keys(result['admin.pub'])
    require(fingerprints == [key['fingerprint'] for key in policy['operator']['keys']],
            'administration fingerprints/order differ')
    return canonical


def budget(roots, candidate, userspace, policy, manifest, project):
    candidate.authenticated_file(userspace, policy['archive'])
    with userspace.open('rb') as stream, tarfile.open(fileobj=stream, mode='r:gz') as archive:
        unpacked = sum(member.size for member in archive if member.isfile())
    candidate.authenticated_file(userspace, policy['archive'])
    require(unpacked == 440908294, 'authenticated archive unpacked size differs')
    objects = sum(int(row.split()[2]) for row in roots.git(project, 'cat-file', '--batch-all-objects',
                  '--batch-check=%(objectname) %(objecttype) %(objectsize)').decode().splitlines())
    require(objects <= 512 * 1024**2, 'Git closure exceeds bound')
    checkout = sum(entry['bytes'] + 4096 for entry in manifest['files'].values())
    capsule = policy['archive']['bytes'] + sum(p['bytes'] for p in policy['operator']['input_records'].values())
    git_bound = 3 * objects + checkout + 64 * 1024**2
    state_peak = 2 * STATE_BYTES + unpacked + policy['archive']['bytes'] + 1024**3
    return {'unpacked_bytes': unpacked, 'state_recipe_bytes': state_peak, 'selector_peak_bytes': 5 * 1024**3,
            'capsule_bytes': capsule, 'git_staging_bound_bytes': git_bound,
            'required_free_bytes': max(state_peak, 5 * 1024**3) + capsule + git_bound}


def inner_program(roots, revision, tag):
    # The verified checkout and bundle setup use the root producer's unchanged code.
    source = roots.inner_program(revision, tag).decode()
    invocation = "subprocess.run(['fakeroot','--','/usr/bin/python3','-I','-B','/a733-debs/payload.py'],check=True)"
    require(source.count(invocation) == 1, 'shared inner program differs')
    selector = """import importlib.util
spec=importlib.util.spec_from_file_location('selection',p/'build/root-selection.py')
selection=importlib.util.module_from_spec(spec);spec.loader.exec_module(selection)
data=selection.prepare(pathlib.Path('/a733-sources/root-A.ext4'),'A',pathlib.Path('/a733-sources/manifest.json'))
with (p/'out/selection-A.conf').open('xb') as stream:
 stream.write(data);stream.flush();os.fsync(stream.fileno())
"""
    return source.replace(invocation, selector + invocation).encode()


def payload_program(roots, tag, selected_uuid):
    phase_path(tag)
    state_uuid(selected_uuid)
    program = r'''import base64,hashlib,importlib.util,json,os,pathlib,stat
p=pathlib.Path('/a733-export-output/project')
spec=importlib.util.spec_from_file_location('state',p/'build/host-state.py')
state=importlib.util.module_from_spec(spec);spec.loader.exec_module(state)
readback=pathlib.Path('/a733-export-output/readback');readback.mkdir(mode=0o700)
'''+ roots.snapshot_program() + r'''original=state.make_image
def capture(root,output,selected,size=state.STATE_BYTES):
 assert size==8589934592 and selected==UUID
 before=snapshot(root)
 assert not any(item['type']=='char' for item in before.values())
 with (readback/'state.expected.json').open('x') as stream:
  json.dump({'schema':1,'state_uuid':selected,'classification':'producer expected tree; independent image readback pending','members':before},stream,sort_keys=True)
  stream.write('\n');stream.flush();os.fsync(stream.fileno())
 result=original(root,output,selected,size)
 assert snapshot(root)==before,'creator input changed during image construction'
 with (readback/'state.stable.json').open('x') as stream:
  json.dump({'schema':1,'state_uuid':selected,'creator_input_stable':True},stream,sort_keys=True)
  stream.write('\n');stream.flush();os.fsync(stream.fileno())
 return result
state.make_image=capture
state.build(pathlib.Path('/a733-debs/rootfs.tar.gz'),pathlib.Path('/a733-debs/management.json'),
 pathlib.Path('/a733-debs/admin.pub'),p/OUTPUT,p/'out/selection-A.conf',UUID,
 metadata_profile='fakeroot-raw-xattrs-v1',userspace_profile='timesync-20261106')
'''
    return program.replace('UUID', repr(selected_uuid)).replace('OUTPUT', repr('out/host-state-' + tag)).encode()


def command(roots, shared, builder, phase):
    args = roots.command(shared, builder, phase)
    args.insert(-3, '--bind-ro=' + str(PRODUCER_OUTPUT) + ':/a733-sources')
    return args


def preflight(roots, shared, contents, phase, required):
    builder, before = roots.preflight(shared, contents, phase, required)
    anchor = shared.ROOT / 'a733-sources'
    info = anchor.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == info.st_gid == shared.UID_BASE
            and stat.S_IMODE(info.st_mode) == 0o755 and not list(anchor.iterdir()),
            'original payload read-only anchor differs')
    return builder, before


def stage_inputs(roots, candidate, shared, project, phase, manifest, policy, userspace, options, held, contents):
    for item in held.values():
        item.unchanged()
    inputs = phase / 'inputs'; inputs.mkdir(mode=0o700)
    candidate.authenticated_file(userspace, policy['archive'], destination=inputs / 'rootfs.tar.gz')
    operator_inputs(roots, candidate, contents, options, policy, inputs)
    subprocess.run(['/usr/bin/git', '-C', str(project), 'bundle', 'create', str(inputs / 'project.bundle'), 'HEAD'],
                   env=ENV, check=True, timeout=120)
    require(roots.committed_sources(project)[0] == manifest, 'source changed during staging')
    shared.atomic_json(inputs / 'manifest.json', manifest)
    (inputs / 'run.py').write_bytes(inner_program(roots, manifest['revision'], options.tag))
    (inputs / 'payload.py').write_bytes(payload_program(roots, options.tag, options.state_uuid))
    for item in inputs.rglob('*'):
        os.chown(item, 0, 0); os.chmod(item, 0o555 if item.is_dir() else 0o444)
    os.chmod(inputs, 0o555)
    for name in ('management.json', 'admin.pub'):
        candidate.authenticated_file(inputs / name, policy['operator']['input_records'][name])
    candidate.authenticated_file(inputs / 'rootfs.tar.gz', policy['archive'])
    for item in held.values():
        item.unchanged()
    return candidate.authenticated_file(inputs / 'project.bundle')[0]


def verify_capsule(candidate, inputs, pins):
    require({str(path.relative_to(inputs)) for path in candidate.regular_tree_files(inputs)} == set(pins),
            'private capsule namespace changed')
    for name, expected in pins.items():
        path = inputs / name
        info = path.lstat()
        require(info.st_uid == info.st_gid == 0 and stat.S_IMODE(info.st_mode) == 0o444,
                'private capsule metadata changed')
        candidate.authenticated_file(path, expected)


def superblock(path, selected_uuid):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd); block = os.pread(fd, 1024, 1024)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size == STATE_BYTES
                and len(block) == 1024 and block[56:58] == b'\x53\xef'
                and str(uuid.UUID(bytes=block[104:120])) == selected_uuid
                and struct.unpack_from('<I', block, 92)[0] & 4
                and not struct.unpack_from('<I', block, 100)[0] & 0x1000,
                'actual state superblock UUID/size/journal/writable policy differs')
        blocks = struct.unpack_from('<I', block, 4)[0] | (struct.unpack_from('<I', block, 336)[0] << 32)
        require(blocks * (1024 << struct.unpack_from('<I', block, 24)[0]) == STATE_BYTES,
                'actual filesystem geometry differs')
    finally:
        os.close(fd)


def verify_output(candidate, output, policy, selected_uuid, selector_path, canonical_keys):
    manifest_pin, raw = candidate.authenticated_file(output / 'manifest.json', retain=True)
    value = json.loads(raw)
    require(value['schema'] == 1 and value['state_bytes'] == STATE_BYTES and value['state_uuid'] == selected_uuid,
            'state manifest identity differs')
    sources = value['sources']
    require(sources['rootfs.tar.gz'] == policy['archive'] and sources['userspace_profile'] == policy['binding']
            and sources['timesync_state'] == policy['clock']
            and sources['rootfs_metadata'] == {'profile': 'fakeroot-raw-xattrs-v1', 'helper': policy['metadata_helper']}
            and sources['rootfs_metadata']['helper'] == value['files']['provenance/rootfs-metadata.py']
            and sources['operator_config'] == policy['operator']['input_records']['management.json']
            and sources['operator_public_key'] == policy['operator']['input_records']['admin.pub'],
            'state source bindings differ')
    require(value['management'] == policy['operator']['management'] and
            value['admin_public_key_fingerprints'] == [key['fingerprint'] for key in policy['operator']['keys']],
            'state management policy differs')
    actual = {str(path.relative_to(output)) for path in candidate.regular_tree_files(output)} - {'manifest.json'}
    require(actual == set(value['files']), 'state manifest file coverage differs')
    for name, expected in value['files'].items():
        candidate.safe_member(name); candidate.authenticated_file(output / name, expected)
    require(value['files']['state.ext4']['bytes'] == STATE_BYTES and 'state.ext4.gz' in value['files'],
            'state outputs differ')
    selector_pin, selector_raw = candidate.authenticated_file(selector_path, retain=True)
    require(sources['boot_selection_input'] == selector_pin, 'state selector pin differs')
    selection = value['boot_selection']
    require(set(selection) == {'SCHEMA', 'SLOT', 'ROOT_UUID', 'ROOT_MARKER_SHA256', 'KERNEL_RELEASE'}
            and selection['SCHEMA'] == '1' and selection['SLOT'] == 'A'
            and selection['ROOT_UUID'] == 'a7330000-0000-4000-8000-00000000000a'
            and re.fullmatch('[0-9a-f]{64}', selection['ROOT_MARKER_SHA256'])
            and selection['KERNEL_RELEASE'] == '6.6.98-a733-diag', 'state selected root differs')
    require(selector_raw == (''.join(key + '=' + value['boot_selection'][key] + '\n' for key in
            ('SCHEMA', 'SLOT', 'ROOT_UUID', 'ROOT_MARKER_SHA256', 'KERNEL_RELEASE'))).encode(),
            'state selector contents differ')
    superblock(output / 'state.ext4', selected_uuid)
    return {'manifest': manifest_pin, 'files_verified': len(actual),
            'image_allocated_bytes': (output / 'state.ext4').stat().st_blocks * 512,
            'full_filesystem_readback': False,
            'boot_selection': {'pin': selector_pin, 'selector': value['boot_selection']}}


def verify_expectations(candidate, verifier, root, output, selected_uuid, canonical_keys, policy):
    require({str(path.relative_to(root)) for path in candidate.regular_tree_files(root)} ==
            {'state.expected.json', 'state.stable.json'}, 'state readback files differ')
    expected_pin, data = candidate.authenticated_file(root / 'state.expected.json', retain=True)
    expected = json.loads(data)
    stable_pin, data = candidate.authenticated_file(root / 'state.stable.json', retain=True)
    require(json.loads(data) == {'schema': 1, 'state_uuid': selected_uuid, 'creator_input_stable': True},
            'state creator stability proof differs')
    require(expected['schema'] == 1 and expected['state_uuid'] == selected_uuid, 'expected state identity differs')
    members = expected['members']; verifier.validate_members(members)
    require(not any(item['type'] == 'char' for item in members.values()), 'state expected tree has devices')
    for path, mode, owner in (('identity', 0o700, 0), ('identity/ssh', 0o700, 0),
                              ('root', 0o700, 0), ('root/.ssh', 0o700, 0), ('config', 0o700, 0),
                              ('var/lib/systemd/timesync', 0o755, 996)):
        item = members[path]
        require(item['type'] == 'dir' and item['mode'] == mode and item['uid'] == item['gid'] == owner,
                'state private/clock directory metadata differs')
    require(not any(path.startswith(('identity/ssh/', 'var/lib/systemd/timesync/')) for path in members),
            'state host-key/clock directory is not empty')
    require(not any(Path(path).name.startswith('ssh_host_') for path in members),
            'state must not contain seeded SSH host keys')
    manifest = json.loads((output / 'manifest.json').read_bytes())
    machine_id = manifest['machine_id']
    require(re.fullmatch('[0-9a-f]{32}', machine_id) and int(machine_id, 16) != 0, 'invalid fresh machine identity')
    for path, raw, mode in (('identity/machine-id', (machine_id + '\n').encode(), 0o644),
                            ('root/.ssh/authorized_keys', canonical_keys, 0o600),
                            ('config/management.json', (json.dumps(policy['operator']['management'], sort_keys=True,
                                separators=(',', ':')) + '\n').encode(), 0o600)):
        item = members[path]
        require(item['type'] == 'file' and item['mode'] == mode and item['uid'] == item['gid'] == 0
                and item['data'] == {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()},
                'state identity/management metadata differs')
    selector = ''.join(key + '=' + manifest['boot_selection'][key] + '\n' for key in
                       ('SCHEMA', 'SLOT', 'ROOT_UUID', 'ROOT_MARKER_SHA256', 'KERNEL_RELEASE')).encode()
    require(members['boot/selected.conf']['data'] ==
            {'bytes': len(selector), 'sha256': hashlib.sha256(selector).hexdigest()} ==
            manifest['sources']['boot_selection_input'], 'expected tree selector binding differs')
    staging = json.loads((output / 'staging-contents.json').read_bytes())
    require(set(staging) == set(members), 'state staging namespace differs')
    for path, item in members.items():
        recorded = staging[path]
        require(recorded['mode'] == format(item['mode'], '04o') and
                (recorded['uid'], recorded['gid']) == (item['uid'], item['gid']), 'state staging metadata differs')
        if item['type'] in ('file', 'hardlink'):
            require({k: recorded[k] for k in ('bytes', 'sha256')} == item['data'], 'state staging data differs')
        require(recorded['type'] == {'dir': 'directory', 'file': 'file', 'hardlink': 'file',
                                     'symlink': 'symlink'}[item['type']], 'state staging type differs')
        require(recorded['xattrs'] == {key: hashlib.sha256(base64.b64decode(raw, validate=True)).hexdigest()
                                      for key, raw in item['xattrs'].items()}, 'state staging xattrs differ')
        if item['type'] == 'symlink':
            require(recorded['target'] == item['link'], 'state staging link differs')
    require(all(manifest['filesystem_directories'][name]['uid'] ==
                manifest['filesystem_directories'][name]['gid'] == 0 and
                not int(manifest['filesystem_directories'][name]['mode'], 8) & 0o7022
                for name in ('/', '/lost+found')), 'filesystem directory metadata differs')
    return {'state': {'expected': expected_pin, 'stability': stable_pin, 'members': len(members),
                      'independent_image_readback': False, 'character_devices': 0}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('project', 'userspace', 'config', 'admin-keys', 'roots-receipt'):
        parser.add_argument('--' + name, type=Path, required=True)
    for name in ('tag', 'roots-receipt-sha256', 'state-uuid'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--plan', action='store_true')
    options = parser.parse_args(argv)
    require(sys.flags.isolated and sys.flags.dont_write_bytecode, 'invoke Python with -I -B')
    # Bootstrap only the coordinator-reviewed root launcher, then authenticate all
    # tracked source bytes and execute subsequent helpers from that Git closure.
    syntax_path(options.project)
    root_source = subprocess.check_output(['/usr/bin/git', '-C', str(options.project),
                                           'show', 'HEAD:' + ROOTS], env=ENV)
    roots = module(str(options.project / ROOTS), root_source)
    manifest, contents = roots.committed_sources(options.project)
    require(roots.read_source(Path(__file__).absolute()) == contents[SELF], 'executing launcher differs from committed source')
    require(root_source == contents[ROOTS], 'root launcher differs from closure')
    policy = contract(roots, options.project, contents)
    report = plan(options, manifest, policy)
    if options.plan:
        print(json.dumps(report, sort_keys=True)); return
    verifier = module(VERIFIER, contents[VERIFIER]); Held = held_class(verifier)
    shared = module(roots.SHARED, contents[roots.SHARED])
    candidate = module(roots.CANDIDATE, contents[roots.CANDIDATE])
    with contextlib.ExitStack() as stack:
        held = {}
        for name, path, expected in (
            ('receipt', options.roots_receipt, policy['producer']['producer_records']['receipt.json']),
            ('manifest', PRODUCER_OUTPUT / 'manifest.json', policy['producer']['verification']['manifest']),
            ('root', PRODUCER_OUTPUT / 'root-A.ext4', policy['producer']['readback']['slots']['A']['image'])):
            held[name] = stack.enter_context(Held(path, expected))
        authenticate_roots(verifier, held, policy)
        keys = operator_inputs(roots, candidate, contents, options, policy)
        required = budget(roots, candidate, options.userspace, policy, manifest, options.project)
        report['initial_budget'] = required
        phase = phase_path(options.tag)
        builder, before = preflight(roots, shared, contents, phase, required)
        capsule = {}
        def stage(c, s, p, ph, m, pol, u, unused, tag):
            bundle = stage_inputs(roots, c, s, p, ph, m, pol, u, options, held, contents)
            capsule.update({str(path.relative_to(ph / 'inputs')): c.authenticated_file(path)[0]
                            for path in c.regular_tree_files(ph / 'inputs')})
            return bundle
        output = phase / 'work/project/out' / ('host-state-' + options.tag)
        def verify(c, actual_output, pol):
            require(actual_output == output, 'state verification output differs')
            verify_capsule(c, phase / 'inputs', capsule)
            for item in held.values(): item.unchanged()
            authenticate_roots(verifier, held, policy)
            operator_inputs(roots, candidate, contents, options, policy)
            for relative, expected in json.loads((output / 'manifest.json').read_bytes())['files'].items():
                if relative.startswith('provenance/'):
                    candidates = [data for name, data in contents.items()
                                  if Path(name).name == Path(relative).name]
                    require(len(candidates) == 1 and roots.pin(candidates[0]) == expected,
                            'state provenance source differs from committed closure')
            return verify_output(c, output, pol, options.state_uuid,
                                 phase / 'work/project/out/selection-A.conf', keys)
        def final_check():
            for item in held.values(): item.unchanged()
            authenticate_roots(verifier, held, policy)
            operator_inputs(roots, candidate, contents, options, policy)
            verify_capsule(candidate, phase / 'inputs', capsule)
        roots.execute(shared, candidate, builder, options.project, phase, manifest, policy,
                      before, report, options.userspace, None, options.tag,
                      output_name='host-state-' + options.tag, stage=stage,
                      namespace_command=lambda s, b, ph: command(roots, s, b, ph), verify=verify,
                      expectations=lambda c, root: verify_expectations(
                          c, verifier, root, output, options.state_uuid, keys, policy), final_check=final_check)
    print(json.dumps(report, sort_keys=True))


if __name__ == '__main__':
    main()
