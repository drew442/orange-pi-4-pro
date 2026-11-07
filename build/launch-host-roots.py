#!/usr/bin/env python3
"""Build only regular-file 4 GiB A/B roots in the frozen private builder.

No physical storage operation or hardware acceptance is performed. Invoke -I -B.
All attempts and partial outputs are retained; a tag is never reused.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import struct
import uuid
import subprocess
import sys
import tarfile
import time
import types

SELF = 'build/launch-host-roots.py'
SHARED = 'build/launch-userspace-configuration.py'
CANDIDATE = 'build/launch-uboot-nvme-candidate.py'
PROFILE = 'timesync-20261106'
METADATA = 'fakeroot-raw-xattrs-v1'
LOCK = 'configs/host-userspace-profiles.lock.json'
DIAGNOSTIC = 'docs/evidence/diagnostic-20261104.json'
HOST_LOCK = 'configs/builder-replay-packages-20261106.lock.tsv'
BASE = Path('/var/lib/a733-clean-replay-20261106')
SLOT_BYTES = 4 * 1024**3
SLOT_UUIDS = {'A': 'a7330000-0000-4000-8000-00000000000a',
              'B': 'a7330000-0000-4000-8000-00000000000b'}
BOOT_FILES = ('Image', 'board.dtb', 'kernel.config', 'initramfs.cpio.gz')
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/nonexistent', 'LC_ALL': 'C', 'TZ': 'UTC'}
MAX_SOURCE = 16 * 1024**2
MAX_CLOSURE = 128 * 1024**2
TIMEOUT = 3600
PROBE_SHA = 'ce565b23ac0b927e54fbfc057bee69ae95d12ba930a5c5421699538e9da14fea'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def pin(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def module(name, data):
    result = types.ModuleType(name.replace('/', '_'))
    result.__file__ = name
    exec(compile(data, name, 'exec'), result.__dict__)
    return result


def physical(path):
    path = Path(path)
    require(path.is_absolute() and str(path) == os.path.abspath(path), 'canonical absolute input required')
    require(not any(path == p or p in path.parents for p in map(Path, ('/dev', '/proc', '/sys'))),
            'pseudo input forbidden')
    for parent in path.parents:
        require(stat.S_ISDIR(parent.lstat().st_mode), 'physical parent required')
    return path


def read_source(path):
    path = physical(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= MAX_SOURCE,
                'bounded single regular source required')
        data = stream.read(MAX_SOURCE + 1)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_nlink, s.st_uid, s.st_gid,
                              s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        require(len(data) == before.st_size and identity(before) == identity(os.fstat(fd))
                == identity(path.lstat()), 'source changed during read')
        return data


def git(project, *args):
    return subprocess.check_output(['/usr/bin/git', '-C', str(project), *args], env=ENV)


def committed_sources(project):
    project = physical(project)
    require(stat.S_ISDIR(project.lstat().st_mode), 'physical project required')
    revision = git(project, 'rev-parse', '--verify', 'HEAD').decode().strip()
    require(re.fullmatch('[0-9a-f]{40}', revision), 'invalid revision')
    require(not git(project, 'status', '--porcelain', '--untracked-files=no'), 'tracked source tree is dirty')
    records, contents, total = {}, {}, 0
    for row in git(project, 'ls-tree', '-rz', '--full-tree', revision).split(b'\0'):
        if not row:
            continue
        fields, raw_name = row.split(b'\t', 1)
        mode, kind, blob = fields.decode().split()
        name = raw_name.decode('utf-8')
        require(mode in ('100644', '100755') and kind == 'blob', 'tracked nonregular source forbidden')
        require(str(Path(name)) == name and '..' not in Path(name).parts and not name.startswith('/'),
                'unsafe Git member')
        data = read_source(project / name)
        total += len(data)
        require(total <= MAX_CLOSURE, 'source closure exceeds bound')
        require(git(project, 'cat-file', 'blob', blob) == data, 'source differs from Git blob: ' + name)
        require(bool((project / name).lstat().st_mode & 0o111) == (mode == '100755'), 'source mode differs')
        records[name], contents[name] = dict(pin(data), git_blob=blob, mode=mode), data
    require(records and git(project, 'rev-parse', 'HEAD').decode().strip() == revision, 'source revision changed')
    return {'revision': revision, 'files': records}, contents


def phase_path(tag):
    require(isinstance(tag, str) and re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,63}', tag), 'unsafe tag')
    return BASE / ('host-roots-' + tag)


def contract(project, contents):
    resolver = module(str(project / 'build/userspace-profiles.py'), contents['build/userspace-profiles.py'])
    selected = resolver.resolve(PROFILE, repo=project)
    lock = json.loads(contents[LOCK])
    require(lock['schema'] == 1 and selected['archive'] == lock['profiles'][PROFILE]['archive'], 'profile mismatch')
    recipe = contents['build/host-payload.py'].decode()
    require('SLOT_BYTES = 4 * 1024**3' in recipe, 'production slot default changed')
    diag = json.loads(contents[DIAGNOSTIC])
    require(diag['schema'] == 1, 'diagnostic schema differs')
    pins = {name: diag['files'][name] for name in BOOT_FILES}
    for expected in (*pins.values(), selected['archive']):
        resolver.pin(expected)
    clock = {'schema': 1, 'identity': 996, 'directory': 'var/lib/systemd/timesync',
             'helper': pin(contents['build/timesync-state.py'])}
    return {'archive': selected['archive'], 'diagnostic': pins, 'binding': selected['binding'], 'clock': clock}


def plan(tag, manifest, policy, diagnostic, userspace):
    physical_args = (diagnostic, userspace)
    # Syntax only: --plan must not inspect physical input files or builder paths.
    require(all(p.is_absolute() and str(p) == os.path.abspath(p) for p in physical_args), 'absolute inputs required')
    return {'schema': 1, 'phase': str(phase_path(tag)), 'sources': manifest,
            'diagnostic_input': str(diagnostic), 'userspace_input': str(userspace), 'policy': policy,
            'userspace_profile': PROFILE, 'metadata_profile': METADATA, 'slot_bytes': SLOT_BYTES,
            'output': 'work/project/out/host-payload-' + tag, 'work_mode': 'disk',
            'initial_required_bytes': '4*SLOT_BYTES + actual unpacked regular bytes + 1 GiB + capsule + Git checkout bound',
            'unpacked_size': 'deferred until authenticated execution preflight',
            'minimum_initial_available_memory_bytes': 2 * 1024**3,
            'runtime_minimum_free_bytes': 3_000_000_000,
            'runtime_minimum_available_memory_bytes': 1_000_000_000, 'timeout_seconds': TIMEOUT,
            'hardware_acceptance': False, 'reproducibility': 'not established',
            'full_filesystem_readback': False}


def authenticated(candidate, path, expected=None, **kwargs):
    physical(path)
    if expected:
        require(expected['bytes'] <= SLOT_BYTES * 2, 'oversized file pin')
    return candidate.authenticated_file(path, expected, **kwargs)


def initial_budget(candidate, userspace, diagnostic, policy, manifest, project):
    authenticated(candidate, userspace, policy['archive'])
    for name in BOOT_FILES:
        authenticated(candidate, diagnostic / name, policy['diagnostic'][name])
    fd = os.open(userspace, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode), 'regular archive required')
        with tarfile.open(fileobj=stream, mode='r:gz') as archive:
            unpacked = sum(m.size for m in archive if m.isfile())
        after = os.fstat(fd)
        require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns), 'archive changed')
    authenticated(candidate, userspace, policy['archive'])
    require(unpacked <= SLOT_BYTES * 3 // 4, 'archive exceeds slot budget')
    # Full reachable Git objects bound bundle and clone object stores; checkout is
    # independently bounded by authenticated tracked bytes. Include metadata slack.
    object_bytes = sum(int(row.split()[2]) for row in git(project, 'cat-file', '--batch-all-objects',
                       '--batch-check=%(objectname) %(objecttype) %(objectsize)').decode().splitlines())
    checkout = sum(row['bytes'] + 4096 for row in manifest['files'].values())
    capsule = policy['archive']['bytes'] + sum(p['bytes'] for p in policy['diagnostic'].values())
    require(object_bytes <= 512 * 1024**2, 'Git object closure exceeds bound')
    return {'unpacked_bytes': unpacked, 'root_recipe_bytes': 4 * SLOT_BYTES + unpacked + 1024**3,
            'capsule_bytes': capsule, 'git_staging_bound_bytes': 3 * object_bytes + checkout + 64 * 1024**2,
            'required_free_bytes': 4 * SLOT_BYTES + unpacked + 1024**3 + capsule +
                                   3 * object_bytes + checkout + 64 * 1024**2}


def check_admission(sample, budget):
    require(sample['free_bytes'] >= budget['required_free_bytes'], 'insufficient initial disk budget')
    require(sample['available_memory_bytes'] >= 2 * 1024**3, 'need 2 GiB MemAvailable')


def check_resources(sample, elapsed):
    require(sample['free_bytes'] >= 3_000_000_000 and sample['available_memory_bytes'] >= 1_000_000_000
            and elapsed <= TIMEOUT, 'runtime resource floor or timeout exceeded')


def inner_program(revision, tag):
    phase_path(tag)
    require(re.fullmatch('[0-9a-f]{40}', revision), 'invalid inner revision')
    return ('''import hashlib,json,os,pathlib,subprocess
os.umask(0o077)
p=pathlib.Path('/a733-export-output/project')
def git(*a):return subprocess.check_output(['git','-C',str(p),*a])
subprocess.run(['git','clone','--no-checkout','/a733-debs/project.bundle',str(p)],check=True)
subprocess.run(['git','-C',str(p),'checkout','--detach',REVISION],check=True)
assert git('rev-parse','HEAD').decode().strip()==REVISION
assert not git('status','--porcelain','--untracked-files=all')
m=json.loads(pathlib.Path('/a733-debs/manifest.json').read_bytes())
for name,entry in m['files'].items():
 data=(p/name).read_bytes()
 assert len(data)==entry['bytes'] and hashlib.sha256(data).hexdigest()==entry['sha256']
 assert git('cat-file','blob',entry['git_blob'])==data
(p/'out').mkdir()
subprocess.run(['fakeroot','--','/usr/bin/python3','-I','-B','/a733-debs/payload.py'],check=True)
'''.replace('REVISION', repr(revision)).replace('OUTPUT', repr('out/host-payload-' + tag))).encode()


def snapshot_program():
    """Expected-tree capture shared with the per-installation state producer."""
    return r'''def snapshot(root):
 root=pathlib.Path(root)
 members={};inodes={}
 def visit(path):
  relative=path.relative_to(root).as_posix()
  assert not any(ord(c)<32 or ord(c)==127 for c in relative)
  info=path.lstat()
  entry={'mode':stat.S_IMODE(info.st_mode),'uid':info.st_uid,'gid':info.st_gid,
         'mtime_ns':info.st_mtime_ns,'xattrs':{}}
  for name in sorted(os.listxattr(path,follow_symlinks=False)):
   entry['xattrs'][name]=base64.b64encode(os.getxattr(path,name,follow_symlinks=False)).decode()
  if stat.S_ISDIR(info.st_mode):entry['type']='dir'
  elif stat.S_ISREG(info.st_mode):
   digest=hashlib.sha256();size=0
   fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
   with os.fdopen(fd,'rb') as stream:
    opened=os.fstat(fd)
    assert (opened.st_dev,opened.st_ino)==(info.st_dev,info.st_ino)
    for chunk in iter(lambda:stream.read(1024**2),b''):
     digest.update(chunk);size+=len(chunk)
    after=os.fstat(fd)
   assert size==info.st_size and (info.st_mode,info.st_uid,info.st_gid,info.st_size,info.st_mtime_ns)==(after.st_mode,after.st_uid,after.st_gid,after.st_size,after.st_mtime_ns)
   entry.update(type='file',data={'bytes':size,'sha256':digest.hexdigest()})
   identity=(info.st_dev,info.st_ino)
   if identity in inodes:entry.update(type='hardlink',link=inodes[identity])
   else:inodes[identity]=relative
  elif stat.S_ISLNK(info.st_mode):entry.update(type='symlink',link=os.readlink(path))
  elif stat.S_ISCHR(info.st_mode):entry.update(type='char',device=[os.major(info.st_rdev),os.minor(info.st_rdev)])
  else:raise ValueError('unsupported expected tree entry '+relative)
  members[relative]=entry
  if stat.S_ISDIR(info.st_mode):
   for child in sorted(path.iterdir()):visit(child)
 visit(root)
 return members
'''


def payload_program(tag):
    phase_path(tag)
    return (r'''import base64,hashlib,importlib.util,json,os,pathlib,stat
p=pathlib.Path('/a733-export-output/project')
spec=importlib.util.spec_from_file_location('host_payload',p/'build/host-payload.py')
payload=importlib.util.module_from_spec(spec)
spec.loader.exec_module(payload)
readback=pathlib.Path('/a733-export-output/readback')
readback.mkdir(mode=0o700)
''' + snapshot_program() + r'''original=payload.make_image
def capture(root,output,slot,size=payload.SLOT_BYTES):
 assert size==4294967296 and slot in ('A','B')
 before=snapshot(root)
 expected=readback/('slot-'+slot+'.expected.json')
 with expected.open('x') as stream:
  json.dump({'schema':1,'slot':slot,'classification':'producer expected tree; independent image readback pending','members':before},stream,sort_keys=True)
  stream.write('\n');stream.flush();os.fsync(stream.fileno())
 original(root,output,slot,size)
 assert snapshot(root)==before,'creator input changed during image construction'
 with (readback/('slot-'+slot+'.stable.json')).open('x') as stream:
  json.dump({'schema':1,'slot':slot,'creator_input_stable':True},stream,sort_keys=True)
  stream.write('\n');stream.flush();os.fsync(stream.fileno())
payload.make_image=capture
payload.build(pathlib.Path('/a733-debs/rootfs.tar.gz'),pathlib.Path('/a733-debs/diagnostic'),p/OUTPUT,
 metadata_profile='fakeroot-raw-xattrs-v1',userspace_profile='timesync-20261106')
'''.replace('OUTPUT',repr('out/host-payload-'+tag))).encode()


def command(shared, builder, phase):
    args = builder.nspawn_command(shared.ROOT, shared.SEED / 'probe.sh')[:-2]
    require(args.count('--private-users-ownership=chown') == 1, 'unexpected namespace command')
    args[args.index('--private-users-ownership=chown')] = '--private-users-ownership=off'
    args += ['--read-only', '--bind=' + str(phase / 'work') + ':/a733-export-output',
             '--bind-ro=' + str(phase / 'inputs') + ':/a733-debs',
             '--bind-ro=' + str(phase / 'inputs/run.py') + ':/a733-install.sh']
    return args + ['/bin/sh', '-c', '/bin/sh /a733-probe.sh && exec /usr/bin/python3 -I -B /a733-install.sh']


def preflight(shared, contents, phase, budget):
    require(os.getuid() == os.geteuid() == os.getgid() == os.getegid() == 0, 'initial-root required')
    require(Path('/proc/self/uid_map').read_text().split() == ['0', '0', '4294967295'], 'initial namespace required')
    release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    require(release.get('ID', '').strip('"') == 'ubuntu' and release.get('VERSION_ID', '').strip('"') == '22.04'
            and os.uname().machine == 'x86_64', 'Ubuntu22.04AMD64 required')
    shared.safe_directory(BASE, trusted=True)
    require(not os.path.lexists(phase), 'phase already exists; choose fresh tag')
    check_admission(shared.resource_sample(), budget)
    shared.frozen_root_directory(shared.ROOT)
    require(not shared.mapped_processes() and not shared.phase_mounts(phase), 'active process or frozen/phase mount')
    helper = shared.regular_bytes(shared.HELPER, owner=0)
    require(pin(helper)['sha256'] == shared.HELPER_SHA, 'builder helper differs')
    shared.safe_directory(shared.SEED, trusted=True)
    require(pin(shared.regular_bytes(shared.SEED / 'probe.sh', owner=0))['sha256'] == PROBE_SHA, 'probe differs')
    for name, directory in (('a733-export-output', True), ('a733-debs', True), ('a733-install.sh', False)):
        target = shared.ROOT / name
        info = target.lstat()
        require(info.st_uid == info.st_gid == shared.UID_BASE, 'anchor owner differs')
        require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode), 'anchor type differs')
        require(not list(target.iterdir()) if directory else info.st_size == 0, 'anchor is not empty')
    before = subprocess.check_output(shared.INVENTORY, env=ENV)
    shared.validate_host_inventory(before, contents[HOST_LOCK])
    require(len(before.splitlines()) == 842, 'host inventory package count differs')
    return module(str(shared.HELPER), helper), before


def stage_inputs(candidate, shared, project, phase, manifest, policy, userspace, diagnostic, tag):
    inputs = phase / 'inputs'
    inputs.mkdir(mode=0o700)
    (inputs / 'diagnostic').mkdir(mode=0o700)
    authenticated(candidate, userspace, policy['archive'], destination=inputs / 'rootfs.tar.gz')
    for name in BOOT_FILES:
        authenticated(candidate, diagnostic / name, policy['diagnostic'][name], destination=inputs / 'diagnostic' / name)
    subprocess.run(['/usr/bin/git', '-C', str(project), 'bundle', 'create', str(inputs / 'project.bundle'),
                    'HEAD'], env=ENV, check=True, timeout=120)
    # Re-authenticate source closure after bundle creation as well.
    require(committed_sources(project)[0] == manifest, 'source changed during staging')
    shared.atomic_json(inputs / 'manifest.json', manifest)
    (inputs / 'run.py').write_bytes(inner_program(manifest['revision'], tag))
    (inputs / 'payload.py').write_bytes(payload_program(tag))
    for item in inputs.rglob('*'):
        os.chown(item, 0, 0)
        os.chmod(item, 0o555 if item.is_dir() else 0o444)
    os.chmod(inputs, 0o555)
    authenticated(candidate, inputs / 'rootfs.tar.gz', policy['archive'])
    for name in BOOT_FILES:
        authenticated(candidate, inputs / 'diagnostic' / name, policy['diagnostic'][name])
    return authenticated(candidate, inputs / 'project.bundle')[0]


def inspect_tree(root):
    allocated, devices = 0, []
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(directory) / name
            info = path.lstat()
            allocated += info.st_blocks * 512
            if stat.S_ISBLK(info.st_mode) or stat.S_ISCHR(info.st_mode):
                devices.append(str(path))
    require(not devices, 'actual block/character device outside fakeroot')
    return {'allocated_bytes': allocated, 'actual_block_character_devices': devices}


def verify_output(candidate, output, policy):
    manifest_pin, raw = authenticated(candidate, output / 'manifest.json', retain=True)
    result = json.loads(raw)
    require(result.get('schema') == 1 and result.get('slot_bytes') == SLOT_BYTES
            and result.get('slot_uuids') == SLOT_UUIDS, 'slot manifest differs')
    sources = result['sources']
    require(sources.get('rootfs.tar.gz') == policy['archive'] and sources.get('userspace_profile') == policy['binding']
            and sources.get('timesync_state') == policy['clock']
            and sources.get('rootfs_metadata', {}).get('profile') == METADATA, 'profile/metadata/time binding differs')
    for name in BOOT_FILES:
        require(sources.get(name) == policy['diagnostic'][name], 'diagnostic binding differs')
    actual = {str(p.relative_to(output)) for p in candidate.regular_tree_files(output)} - {'manifest.json'}
    require(actual == set(result['files']), 'manifest file coverage differs')
    for relative, expected in result['files'].items():
        candidate.safe_member(relative)
        authenticated(candidate, output / relative, expected)
    for slot in SLOT_UUIDS:
        require(result['files'].get('root-' + slot + '.ext4', {}).get('bytes') == SLOT_BYTES
                and 'root-' + slot + '.ext4.gz' in result['files'], 'root image size/output differs')
        image = output / ('root-' + slot + '.ext4')
        fd = os.open(image, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            require(stat.S_ISREG(info.st_mode) and info.st_size == SLOT_BYTES, 'actual image differs')
            block = os.pread(fd, 1024, 1024)
            require(len(block) == 1024 and block[56:58] == b'\x53\xef'
                    and str(uuid.UUID(bytes=block[104:120])) == SLOT_UUIDS[slot]
                    and not struct.unpack_from('<I', block, 92)[0] & 4
                    and struct.unpack_from('<I', block, 100)[0] & 0x1000,
                    'actual image UUID/immutable no-journal superblock differs')
        finally:
            os.close(fd)
    return {'manifest': manifest_pin, 'files_verified': len(actual),
            'image_allocated_bytes': {slot: (output / ('root-' + slot + '.ext4')).stat().st_blocks * 512
                                      for slot in SLOT_UUIDS}, 'full_filesystem_readback': False}


def verify_expectations(candidate, root):
    records = {}
    require({p.name for p in candidate.regular_tree_files(root)} ==
            {'slot-' + slot + suffix for slot in SLOT_UUIDS for suffix in ('.expected.json', '.stable.json')},
            'readback producer files differ')
    for slot in SLOT_UUIDS:
        expected = root / ('slot-' + slot + '.expected.json')
        record, raw = authenticated(candidate, expected, retain=True)
        value = json.loads(raw)
        require(value.get('schema') == 1 and value.get('slot') == slot and '.' in value.get('members', {}),
                'readback expected tree differs')
        stable_pin, stable = authenticated(candidate, root / ('slot-' + slot + '.stable.json'), retain=True)
        require(json.loads(stable) == {'schema': 1, 'slot': slot, 'creator_input_stable': True},
                'creator stability proof differs')
        records[slot] = {'expected': record, 'stability': stable_pin, 'members': len(value['members']),
                         'independent_image_readback': False}
    return records


def execute(shared, candidate, builder, project, phase, manifest, policy, before, report, userspace, diagnostic, tag,
            *, output_name=None, stage=None, namespace_command=None, verify=None, expectations=None, final_check=None, resource_check=None):
    # Defaults preserve the root producer API; the STATE wrapper supplies explicit hooks.
    stage = stage_inputs if stage is None else stage
    namespace_command = command if namespace_command is None else namespace_command
    verify = verify_output if verify is None else verify
    expectations = verify_expectations if expectations is None else expectations
    resource_check = check_resources if resource_check is None else resource_check
    output_name = ('host-payload-' + tag) if output_name is None else output_name
    require(re.fullmatch(r'(?:host-(?:payload|state|sd)|nvme-image|fel-version)-[A-Za-z0-9][A-Za-z0-9_-]{0,63}', output_name),
            'unsafe producer output name')
    shared.safe_directory(phase.parent, trusted=True)
    phase.mkdir(mode=0o700)
    report.update(success=False, state='staging', resources=[])
    handlers, proc = {}, None
    try:
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            handlers[sig] = signal.signal(sig, shared.interrupted)
        work = phase / 'work'
        work.mkdir(mode=0o700)
        os.chown(work, shared.UID_BASE, shared.UID_BASE)
        report['bundle'] = stage(candidate, shared, project, phase, manifest, policy, userspace, diagnostic, tag)
        shared.atomic_json(phase / 'source-manifest.json', manifest)
        shared.atomic_json(phase / 'staged.json', report)
        args = namespace_command(shared, builder, phase)
        report.update(state='running', command=args)
        with (phase / 'runtime.log').open('xb') as stream:
            proc = subprocess.Popen(args, stdout=stream, stderr=subprocess.STDOUT, env=ENV, start_new_session=True)
            start = time.monotonic()
            while True:
                sample = shared.resource_sample()
                report['resources'].append(sample)
                resource_check(sample, time.monotonic() - start)
                if proc.poll() is not None:
                    break
                time.sleep(1)
            report['returncode'] = proc.wait(timeout=15)
            stream.flush()
            os.fsync(stream.fileno())
        require(report['returncode'] == 0, 'root payload failed; outputs retained')
        report['probe'] = builder.validate_probe((phase / 'runtime.log').read_bytes())
        report['verification'] = verify(candidate, work / 'project/out' / output_name, policy)
        report['readback_expectations'] = expectations(candidate, work / 'readback')
        report['success'] = True
    except BaseException as error:
        report['error'] = repr(error)
        raise
    finally:
        # Drop repeated handled signals while stopping the exact child session,
        # checking isolation and publishing its retained receipt. Block during
        # handler transitions so a different handled signal cannot interrupt one.
        cleanup_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set(handlers))
        for sig in handlers:
            signal.signal(sig, signal.SIG_IGN)
        signal.pthread_sigmask(signal.SIG_SETMASK, cleanup_mask)
        try:
            shared.stop_process(proc)
            report['mapped_processes_final'] = shared.mapped_processes()
            report['phase_mounts_final'] = shared.phase_mounts(phase)
            after = subprocess.check_output(shared.INVENTORY, env=ENV)
            require(len(after.splitlines()) == 842, 'final inventory count differs')
            report['host_inventory_unchanged'] = before == after
            report['final_resources'] = shared.resource_sample()
            report['retained_tree'] = inspect_tree(phase / 'work')
            if final_check is not None:
                final_check()
            report['success'] = (report['success'] and before == after and not report['mapped_processes_final']
                                 and not report['phase_mounts_final']
                                 and report['final_resources']['free_bytes'] >= 3_000_000_000
                                 and report['final_resources']['available_memory_bytes'] >= 1_000_000_000)
        except BaseException as error:
            report.update(success=False, cleanup_error=repr(error))
        report['state'] = 'complete' if report['success'] else 'failed'
        try:
            shared.atomic_json(phase / 'receipt.json', report)
        finally:
            signal.pthread_sigmask(signal.SIG_BLOCK, set(handlers))
            try:
                for sig, handler in handlers.items():
                    signal.signal(sig, handler)
            finally:
                signal.pthread_sigmask(signal.SIG_SETMASK, cleanup_mask)
    require(report['success'], 'root build final checks failed; retained phase requires review')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--diagnostic', type=Path, required=True)
    parser.add_argument('--userspace', type=Path, required=True)
    parser.add_argument('--plan', action='store_true')
    options = parser.parse_args(argv)
    require(sys.flags.isolated and sys.flags.dont_write_bytecode, 'invoke Python with -I -B')
    manifest, contents = committed_sources(options.project)
    require(read_source(Path(__file__).absolute()) == contents[SELF], 'executing launcher differs from committed source')
    policy = contract(options.project, contents)
    report = plan(options.tag, manifest, policy, options.diagnostic, options.userspace)
    if options.plan:
        print(json.dumps(report, sort_keys=True))
        return
    shared, candidate = module(SHARED, contents[SHARED]), module(CANDIDATE, contents[CANDIDATE])
    budget = initial_budget(candidate, options.userspace, options.diagnostic, policy, manifest, options.project)
    report['initial_budget'] = budget
    phase = phase_path(options.tag)
    builder, before = preflight(shared, contents, phase, budget)
    execute(shared, candidate, builder, options.project, phase, manifest, policy, before, report,
            options.userspace, options.diagnostic, options.tag)
    print(json.dumps(report, sort_keys=True))


if __name__ == '__main__':
    main()
