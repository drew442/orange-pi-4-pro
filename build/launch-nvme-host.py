#!/usr/bin/env python3
"""Compose authenticated production components into a regular-file NVMe image.

Invoke Python -I -B. No device writes, installation or hardware acceptance.
Failed phases and partial files are retained; tags cannot be reused.
"""
import argparse
import contextlib
import json
import os
from pathlib import Path
import re
import resource
import stat
import subprocess
import sys
import types

SELF = 'build/launch-nvme-host.py'
ROOTS = 'build/launch-host-roots.py'
STATE = 'build/launch-host-state.py'
BOOTSTRAP = 'build/launch-host-bootstrap.py'
VERIFIER = 'build/verify-host-roots.py'
EVIDENCE = {name: 'docs/evidence/production-host-' + name + '-20261107.json'
            for name in ('roots', 'state', 'bootstrap')}
TARGET = 'docs/evidence/installed-host-first-boot-20261107.json'
BASE = Path('/var/lib/a733-clean-replay-20261106')
PHASES = {name: BASE / value for name, value in (
    ('roots', 'host-roots-timesync-01'), ('state', 'host-state-timesync-02'),
    ('sd', 'host-bootstrap-timesync-01'))}
INPUTS = {name: PHASES[name] / 'work/project/out' / value for name, value in (
    ('roots', 'host-payload-timesync-01'), ('state', 'host-state-timesync-02'),
    ('sd', 'host-sd-timesync-01'))}
INPUTS['diagnostic'] = Path('/var/lib/a733-bootstrap-inputs-20261107')
STATE_UUID = 'fc8bbef6-9385-4ddb-90ba-e41eaccc035b'
SIZE = 256060514304
TIMEOUT = 10800
MINIMUM_NOFILE = 4096
GUARD = 22 * 1024**3
READER_SHA = '80e843d499bcc2f6d299d6022dc7edf83d73842405e95b38b0f32eee72dcda30'
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/nonexistent', 'LC_ALL': 'C', 'TZ': 'UTC'}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def phase_path(tag):
    require(isinstance(tag, str) and re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,63}', tag), 'unsafe tag')
    return BASE / ('nvme-' + tag)


def contract(roots, project, contents):
    bootstrap = roots.module(BOOTSTRAP, contents[BOOTSTRAP])
    parent = bootstrap.contract(roots, project, contents)
    producers = {name: json.loads(contents[path]) for name, path in EVIDENCE.items()}
    target = json.loads(contents[TARGET])['installation']['target']
    require(target == {'model': 'SPCC M.2 PCIe SSD', 'serial': '134807190FC700149277',
                      'bytes': SIZE, 'logical_sector_bytes': 512, 'physical_sector_bytes': 512},
            'committed target geometry differs')
    require(roots.pin(contents[VERIFIER])['sha256'] == READER_SHA, 'generic reader differs')
    r, s, b = (producers[name] for name in ('roots', 'state', 'bootstrap'))
    require(r['phase'] == str(PHASES['roots']) and r['slot_bytes'] == 4 * 1024**3 and
            r['construction_success'] is True and r['full_filesystem_readback_success'] is True and
            s == parent['state_producer'] and s['state_uuid'] == b['state_uuid'] == STATE_UUID and
            b['phase'] == str(PHASES['sd']) and b['output'] == 'work/project/out/host-sd-timesync-01' and
            b['state_receipt_sha256'] == s['producer_records']['receipt.json']['sha256'] and
            b['boot_profile'] == parent['boot_profile'], 'production component chain differs')
    nvme = roots.module(str(project / 'build/nvme-image.py'), contents['build/nvme-image.py'])
    _, command_profile = nvme.boot_command('spi-nvme-v1', 'experimental-el2')
    _, _, profile_recipes = nvme.HOST.load_boot_profile('experimental-el2')
    recipe_names = set(nvme.RECIPES) | set(profile_recipes) | {'configs/spi-nvme-host-boot.cmd'}
    return {'spi_wrapper_helper': {'path': 'tests/verify_host_sd.py', **roots.pin(contents['tests/verify_host_sd.py'])},
            'recipes': {name: roots.pin(contents[name]) for name in recipe_names},
            'nvme_boot_command_profile': command_profile, 'target_geometry': target, 'producers': producers, 'parent': parent,
            'state_uuid': STATE_UUID, 'boot_profile': parent['boot_profile'],
            'nvme_boot_command_source': {'path': 'configs/spi-nvme-host-boot.cmd',
                                         **roots.pin(contents['configs/spi-nvme-host-boot.cmd'])}}


def plan(tag, manifest, policy):
    return {'schema': 1, 'phase': str(phase_path(tag)), 'sources': manifest, 'policy': policy,
            'inputs': {k: str(v) for k, v in INPUTS.items()},
            'output': 'work/project/out/nvme-image-' + tag, 'work_mode': 'disk',
            'initial_required_bytes': '22 GiB + metadata capsule + Git staging bound',
            'minimum_initial_available_memory_bytes': 3 * 1024**3,
            'minimum_process_open_file_soft_limit': MINIMUM_NOFILE,
            'runtime_minimum_free_bytes': 3_000_000_000,
            'runtime_minimum_available_memory_bytes': 1_000_000_000, 'timeout_seconds': TIMEOUT,
            'candidate_for_installation': True, 'hardware_acceptance': False,
            'physical_storage_written': False, 'physical_write_authorization': False,
            'reproducibility': 'not established'}


def admit_descriptor_limit():
    """Raise only this producer's soft limit before opening held input ancestry."""
    before, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    expected = before
    if before != resource.RLIM_INFINITY and before < MINIMUM_NOFILE:
        require(hard == resource.RLIM_INFINITY or hard >= MINIMUM_NOFILE,
                'process open-file hard limit is below required 4096')
        expected = MINIMUM_NOFILE
        resource.setrlimit(resource.RLIMIT_NOFILE, (expected, hard))
    after, final_hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    require(after == expected and final_hard == hard, 'process open-file limit differs after admission')
    return {'required_soft_limit': MINIMUM_NOFILE, 'soft_before': before, 'soft_after': after,
            'hard': hard, 'scope': 'current producer process only'}


def records(policy):
    p = policy['producers']
    result = {}
    for name, evidence in (('roots', p['roots']), ('state', p['state']), ('sd', p['bootstrap'])):
        result[name + '-receipt'] = (PHASES[name] / 'receipt.json', evidence['producer_records']['receipt.json'])
        result[name + '-manifest'] = (INPUTS[name] / 'manifest.json', evidence['verification']['manifest'])
    result['roots-readback'] = (Path(p['roots']['readback']['builder_output']) / 'receipt.json', p['roots']['readback']['receipt'])
    result['state-readback.json'] = (Path(p['state']['readback']['output']) / 'receipt.json', p['state']['readback']['receipt'])
    result['sd-readback'] = (PHASES['sd'] / 'work/readback/bootstrap.json', p['bootstrap']['readback_expectations']['inspection'])
    return result


def authenticate(verifier, state, bootstrap, held, policy):
    parent = policy['parent']
    # Reuse the reviewed component producer contracts without reopening inputs.
    roots_manifest = state.authenticate_roots(verifier, {
        'receipt': held['roots-receipt'], 'manifest': held['roots-manifest'],
        'root': held['roots/root-A.ext4']}, parent['state_policy'])
    bootstrap.authenticate_state(verifier, {
        'state-receipt.json': held['state-receipt'], 'state-manifest.json': held['state-manifest'],
        'state-readback.json': held['state-readback.json']}, parent)
    p = policy['producers']; r = verifier.json_fd(held['roots-readback'])
    require(r['success'] is True and r['state'] == 'complete' and
            r['full_namespace_metadata_content_readback'] is True and
            r['producer_receipt'] == held['roots-receipt'].pin and
            r['root_manifest'] == held['roots-manifest'].pin and r['slots'] == p['roots']['readback']['slots'],
            'root full readback differs')
    for slot in ('A', 'B'):
        require(r['slots'][slot]['image'] == held['roots/root-' + slot + '.ext4'].pin ==
                roots_manifest['files']['root-' + slot + '.ext4'], 'root payload/readback differs')
    receipt = verifier.json_fd(held['sd-receipt']); sd = verifier.json_fd(held['sd-manifest'])
    require(receipt['success'] is True and receipt['state'] == 'complete' and
            receipt['phase'] == str(PHASES['sd']) and receipt['output'] == p['bootstrap']['output'] and
            receipt['sources']['revision'] == p['bootstrap']['source_revision'] and
            receipt['policy'] == parent and receipt['verification'] == p['bootstrap']['verification'] and
            receipt['readback_expectations'] == p['bootstrap']['readback_expectations'] and
            sd['state_uuid'] == STATE_UUID and sd['boot_profile'] == parent['boot_profile'] and
            sd['boot_inputs'] == p['bootstrap']['boot_inputs'] and
            verifier.json_fd(held['sd-readback']) == p['bootstrap']['readback_expectations']['checks'],
            'bootstrap provenance/readback differs')


def budget(roots, project, manifest, payload, run):
    objects = sum(int(row.split()[2]) for row in roots.git(project, 'cat-file', '--batch-all-objects',
                  '--batch-check=%(objectname) %(objecttype) %(objectsize)').decode().splitlines())
    require(objects <= 512 * 1024**2, 'Git closure exceeds bound')
    checkout = sum(item['bytes'] + 4096 for item in manifest['files'].values())
    # Bundle is covered by the three-object-store bound; originals remain bound RO.
    capsule = len(payload) + len(run) + len(json.dumps(manifest).encode()) + 65536
    git_bound = 3 * objects + checkout + 64 * 1024**2
    return {'recipe_bytes': GUARD, 'capsule_bytes': capsule, 'git_staging_bound_bytes': git_bound,
            'required_free_bytes': GUARD + capsule + git_bound}


def check_resources(sample, elapsed):
    require(sample['free_bytes'] >= 3_000_000_000 and sample['available_memory_bytes'] >= 1_000_000_000
            and elapsed <= TIMEOUT, 'NVMe runtime resource floor or timeout exceeded')


def command(roots, shared, builder, phase):
    args = roots.command(shared, builder, phase)
    for name, path in INPUTS.items():
        args.insert(-3, '--bind-ro=' + str(path) + ':/a733-debs/' + name)
    args.insert(-3, '--bind=' + str(phase / 'work/private-scratch') + ':/var/tmp')
    return args


def private_artifact_program():
    """Leaf-only fences for generated private files; no parent timestamp fence."""
    return r'''def validate_spi_script(data,command):
 assert sdinspection.wrapper(data,6,0)==struct.pack('>II',len(command),0)+command,'SPI script body differs'
 return True
def check_private(fd,path,before):
 assert reader.signature(os.fstat(fd))==reader.signature(before)==reader.signature(path.lstat()),'private artifact identity changed'
def open_private(path,expected_bytes=None):
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
 try:
  before=os.fstat(fd)
  assert stat.S_ISREG(before.st_mode) and before.st_nlink==1,'single regular private artifact required'
  assert expected_bytes is None or before.st_size==expected_bytes,'private artifact size differs'
  check_private(fd,path,before)
  return fd,before
 except BaseException:
  os.close(fd);raise
def compress_private(raw,target,expected):
 fd,before=open_private(raw,expected['bytes'])
 try:
  digest=hashlib.sha256();size=0
  with target.open('xb') as compressed:
   with gzip.GzipFile(filename='',mode='wb',fileobj=compressed,compresslevel=9,mtime=0) as stream:
    while chunk:=os.read(fd,4*1024**2):
     digest.update(chunk);size+=len(chunk);stream.write(chunk)
   compressed.flush();os.fsync(compressed.fileno())
  assert {'bytes':size,'sha256':digest.hexdigest()}==expected,'compressed raw bytes differ'
  check_private(fd,raw,before)
 finally:os.close(fd)
 compressed_fd,compressed_before=open_private(target)
 try:
  gzip_pin=reader.digest_fd(compressed_fd)
  check_private(compressed_fd,target,compressed_before)
 finally:os.close(compressed_fd)
 return {'schema':1,'raw':expected,'gzip':gzip_pin,'filename':'','mtime':0,'level':9,'input_identity_unchanged':True,'gzip_expansion_independent_verification':'pending'}
'''


def payload_program(roots, tag, policy):
    phase_path(tag)
    code = r'''import base64,gzip,hashlib,importlib.util,json,os,pathlib,stat,struct
os.umask(0o022)
os.environ.update(SOURCE_DATE_EPOCH='1793750400',TMPDIR='/var/tmp',LC_ALL='C',TZ='UTC')
p=pathlib.Path('/a733-export-output/project')
def module(name,path):
 spec=importlib.util.spec_from_file_location(name,path)
 value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value
def publish(path,value):
 with path.open('x') as stream:
  json.dump(value,stream,sort_keys=True);stream.write('\n');stream.flush();os.fsync(stream.fileno())
def progress(value):print(value,flush=True)
# Each component bind must itself be a read-only mount, beneath the RO capsule.
mounts={}
for line in pathlib.Path('/proc/self/mountinfo').read_text().splitlines():
 fields=line.split();mounts[fields[4]]=set(fields[5].split(','))
for path in ('/a733-debs','/a733-debs/roots','/a733-debs/state','/a733-debs/sd','/a733-debs/diagnostic'):
 assert 'ro' in mounts.get(path,set()),'component mount is not read-only: '+path
nvme=module('nvme',p/'build/nvme-image.py')
inspection=module('inspection',p/'tests/verify_nvme_image.py')
sdinspection=module('sdinspection',p/'tests/verify_host_sd.py')
reader=module('reader',p/'build/verify-host-roots.py')
assert hashlib.sha256((p/'build/verify-host-roots.py').read_bytes()).hexdigest()==READER_SHA
readback=pathlib.Path('/a733-export-output/readback');readback.mkdir(mode=0o700)
'''.replace('READER_SHA', repr(READER_SHA)) + private_artifact_program() + roots.snapshot_program() + r'''
expected={};namespace_proofs={};script_proof={}
makefs=nvme.make_filesystem
def capture(image,size,label,identity,seed,legacy=False):
 role={'A733BOOT':'boot','A733GUESTS':'guests'}[label]
 before=snapshot(seed)
 allowed={'.'} if role=='guests' else {'.','boot','boot/dtb','boot/dtb/allwinner','boot/uImage','boot/uInitrd','boot/boot.cmd','boot/boot.scr','boot/dtb/allwinner/sun60i-a733-orangepi-4-pro.dtb'}
 assert set(before)==allowed
 assert before['.']['type']=='dir' and before['.']['uid']==before['.']['gid']==0
 for name,item in before.items():
  assert item['uid']==item['gid']==0 and not item['xattrs']
  assert item['type']==('dir' if name in ('.','boot','boot/dtb','boot/dtb/allwinner') else 'file')
 if role=='boot':
  command=(p/'configs/spi-nvme-host-boot.cmd').read_bytes()
  assert before['boot/boot.cmd']['data']==nvme.HOST.record(command)
  script_proof.update(header_type_crc_body=validate_spi_script((seed/'boot/boot.scr').read_bytes(),command),helper={'path':'tests/verify_host_sd.py',**nvme.HOST.file_record(p/'tests/verify_host_sd.py')})
 makefs(image,size,label,identity,seed,legacy)
 assert snapshot(seed)==before,'filesystem seed changed'
 before['.']['mode']=0o755 if legacy else 0o700
 expected[role]=before
 publish(readback/(role+'.expected.json'),{'schema':1,'members':before,'creator_input_stable':True})
nvme.make_filesystem=capture
progress('NVMe construction starting')
output=p/OUTPUT
manifest=nvme.build(pathlib.Path('/a733-debs/roots'),pathlib.Path('/a733-debs/state'),pathlib.Path('/a733-debs/sd'),pathlib.Path('/a733-debs/diagnostic'),output,256060514304,logical_sector_bytes=512,physical_sector_bytes=512,for_installation=True,boot_profile='experimental-el2',boot_command_profile='spi-nvme-v1')
original_capture=inspection.sparse_capture
def inspect_capture(fd,start,size,target):
 original_capture(fd,start,size,target)
 role=target.stem
 if role in ('boot','guests'):
  work=pathlib.Path('/var/tmp')/(role+'-inspection');work.mkdir(mode=0o700)
  captured,before=open_private(target,size)
  try:
   root_time=struct.unpack('<I',os.pread(captured,4,1024+264))[0]
   namespace_proofs[role]=reader.verify(captured,expected[role],work,root_time=root_time)
   check_private(captured,target,before)
  finally:os.close(captured)
inspection.sparse_capture=inspect_capture
progress('Complete logical image and partition inspection starting')
proof=inspection.verify(output)
assert set(namespace_proofs)=={'boot','guests'}
proof['complete_new_filesystem_readback']=namespace_proofs
proof['spi_script_wrapper']=script_proof
publish(readback/'nvme.json',proof)
progress('Deterministic gzip compression starting')
raw=output/'nvme.img'
compression=compress_private(raw,output/'nvme.img.gz',manifest['files']['nvme.img'])
publish(readback/'compression.json',compression)
progress('NVMe file-only construction and inspection complete')
'''
    return code.replace('OUTPUT', repr('out/nvme-image-' + tag)).encode()


def verify_capsule(candidate, inputs, held):
    require({str(p.relative_to(inputs)) for p in candidate.regular_tree_files(inputs)} == set(held),
            'capsule file namespace differs')
    require({p.name for p in inputs.iterdir()} == {'roots','state','sd','diagnostic','project.bundle','manifest.json','run.py','payload.py'},
            'capsule namespace differs')
    for path in (inputs, *(inputs / name for name in INPUTS)):
        info = path.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == info.st_gid == 0 and
                stat.S_IMODE(info.st_mode) == 0o555, 'capsule directory metadata differs')
    for name in INPUTS:
        require(not list((inputs / name).iterdir()), 'capsule binding anchor must be empty')
    for item in held.values():
        require(item.before.st_uid == item.before.st_gid == 0 and stat.S_IMODE(item.before.st_mode) == 0o444,
                'capsule leaf metadata differs')
        item.unchanged()


def verify_output(candidate, output, policy, held, contents, roots):
    require({p.name for p in output.iterdir()} == {'manifest.json','nvme.img','nvme.img.gz'} and
            {p.name for p in candidate.regular_tree_files(output)} == {'manifest.json','nvme.img','nvme.img.gz'},
            'NVMe output namespace differs')
    manifest_pin, raw = candidate.authenticated_file(output / 'manifest.json', retain=True)
    value = json.loads(raw)
    require(value['schema'] == 1 and set(value['files']) == {'nvme.img'} and
            value['files']['nvme.img']['bytes'] == SIZE and value['logical_sector_bytes'] == value['physical_sector_bytes'] == 512 and
            value['state_uuid'] == STATE_UUID and value['boot_profile'] == policy['boot_profile'] and
            value['deployment_intent']['for_installation'] is True and
            value['boot_selection'] == policy['producers']['state']['boot_selection'], 'NVMe manifest geometry/profile differs')
    require(value['inputs'] == {name + '_manifest': held[key + '-manifest'].pin for name,key in
                               (('roots','roots'),('state','state'),('sd','sd'))}, 'NVMe component manifests differ')
    require(value['partition_payloads'] == {'root-a': held['roots/root-A.ext4'].pin,
            'root-b': held['roots/root-B.ext4'].pin, 'state': held['state/state.ext4'].pin}, 'NVMe payload hashes differ')
    require(value['nvme_boot_command_profile'] == policy['nvme_boot_command_profile'] and
            value['nvme_boot_command_profile']['source'] == policy['nvme_boot_command_source'] and
            value['boot_files']['boot/boot.cmd'] == roots.pin(contents['configs/spi-nvme-host-boot.cmd']) and
            value['recipes'] == policy['recipes'],
            'NVMe SPI command binding differs')
    candidate.authenticated_file(output / 'nvme.img', value['files']['nvme.img'])
    return {'manifest': manifest_pin, 'image': value['files']['nvme.img'], 'hardware_acceptance': False}


def verify_expectations(candidate, root, output, policy):
    expected_files = {'nvme.json','compression.json','boot.expected.json','guests.expected.json'}
    actual = {str(p.relative_to(root)) for p in candidate.regular_tree_files(root)}
    # Generic readback uses only ephemeral scratch files, cleaned by its reader.
    require(actual == expected_files and {p.name for p in root.iterdir()} == expected_files,
            'readback output namespace differs')
    result = {}
    for name in expected_files:
        pin, raw = candidate.authenticated_file(root / name, retain=True)
        result[name] = {'pin': pin, 'value': json.loads(raw)}
    proof = result['nvme.json']['value']; compression = result['compression.json']['value']
    manifest_pin, raw = candidate.authenticated_file(output / 'manifest.json', retain=True)
    manifest = json.loads(raw)
    require(proof['manifest'] == manifest_pin and proof['image'] == manifest['files']['nvme.img'] and
            proof['partition_payloads'] == manifest['partition_payloads'] and
            proof['boot_file_hashes'] == manifest['boot_files'], 'inspection/output bindings differ')
    require(proof['image'] == compression['raw'] and compression['raw']['bytes'] == SIZE and
            proof['gpt_primary_backup_and_geometry'] is True and proof['root_markers_and_state_selection'] is True and
            proof['image_metadata_unchanged'] is True and
            proof['spi_script_wrapper'] == {'header_type_crc_body': True, 'helper': policy['spi_wrapper_helper']} and
            set(proof['complete_new_filesystem_readback']) == {'boot','guests'} and
            compression['input_identity_unchanged'] is True and compression['mtime'] == 0 and
            compression['level'] == 9 and compression['filename'] == '', 'NVMe readback/compression proof differs')
    candidate.authenticated_file(output / 'nvme.img.gz', compression['gzip'])
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--plan', action='store_true')
    options = parser.parse_args(argv)
    require(sys.flags.isolated and sys.flags.dont_write_bytecode, 'invoke Python with -I -B')
    import subprocess
    source = subprocess.check_output(['/usr/bin/git','-C',str(options.project),'show','HEAD:' + ROOTS],env=ENV)
    roots = types.ModuleType('nvme_roots'); roots.__file__ = str(options.project / ROOTS)
    exec(compile(source, roots.__file__, 'exec'), roots.__dict__)
    manifest, contents = roots.committed_sources(options.project)
    require(roots.read_source(Path(__file__).absolute()) == contents[SELF] and source == contents[ROOTS],
            'executing launcher differs from committed closure')
    policy = contract(roots, options.project, contents)
    report = plan(options.tag, manifest, policy)
    if options.plan:
        print(json.dumps(report, sort_keys=True)); return
    report['process_open_file_limit'] = admit_descriptor_limit()
    state = roots.module(STATE, contents[STATE]); bootstrap = roots.module(BOOTSTRAP, contents[BOOTSTRAP])
    verifier = roots.module(VERIFIER, contents[VERIFIER]); Held = state.held_class(verifier)
    shared = roots.module(roots.SHARED, contents[roots.SHARED]); candidate = roots.module(roots.CANDIDATE, contents[roots.CANDIDATE])
    payload = payload_program(roots, options.tag, policy); run = roots.inner_program(manifest['revision'], options.tag)
    with contextlib.ExitStack() as stack:
        held = {name: stack.enter_context(Held(path, pin)) for name,(path,pin) in records(policy).items()}
        for component in ('roots','state','sd'):
            value = verifier.json_fd(held[component + '-manifest'])
            for name,pin in value['files'].items():
                candidate.safe_member(name)
                held[component + '/' + name] = stack.enter_context(Held(INPUTS[component] / name,pin))
        for name,pin in policy['parent']['diagnostic'].items():
            held['diagnostic/' + name] = stack.enter_context(Held(INPUTS['diagnostic'] / name,pin))
        authenticate(verifier,state,bootstrap,held,policy)
        required = budget(roots,options.project,manifest,payload,run); report['initial_budget'] = required
        require(shared.resource_sample()['available_memory_bytes'] >= 3 * 1024**3, 'need 3 GiB MemAvailable')
        phase = phase_path(options.tag)
        builder,before = roots.preflight(shared,contents,phase,required)
        bootstrap.scratch_anchor(shared)
        capsule = {}
        def stage(c,s,project,ph,m,pol,u,d,tag):
            for item in held.values(): item.unchanged()
            inputs = ph / 'inputs';inputs.mkdir(mode=0o700)
            for name in INPUTS: (inputs/name).mkdir(mode=0o700)
            scratch = ph/'work/private-scratch';scratch.mkdir(mode=0o700);os.chown(scratch,s.UID_BASE,s.UID_BASE)
            subprocess.run(['/usr/bin/git','-C',str(project),'bundle','create',str(inputs/'project.bundle'),'HEAD'],env=ENV,check=True,timeout=120)
            require(roots.committed_sources(project)[0] == m, 'source changed during staging')
            s.atomic_json(inputs/'manifest.json',m)
            (inputs/'run.py').write_bytes(run);(inputs/'payload.py').write_bytes(payload)
            for path in inputs.rglob('*'):
                os.chown(path,0,0);os.chmod(path,0o555 if path.is_dir() else 0o444)
            os.chmod(inputs,0o555)
            for path in c.regular_tree_files(inputs): capsule[str(path.relative_to(inputs))] = stack.enter_context(Held(path))
            verify_capsule(c,inputs,capsule)
            return capsule['project.bundle'].pin
        output = phase/'work/project/out'/('nvme-image-'+options.tag)
        def final_check():
            for item in held.values(): item.unchanged()
            authenticate(verifier,state,bootstrap,held,policy)
            verify_capsule(candidate,phase/'inputs',capsule)
            require(roots.committed_sources(options.project)[0] == manifest, 'source closure changed')
        def verify(c,o,pol):
            require(o == output,'unexpected output path');final_check()
            return verify_output(c,o,pol,held,contents,roots)
        roots.execute(shared,candidate,builder,options.project,phase,manifest,policy,before,report,None,None,options.tag,
                      output_name='nvme-image-'+options.tag,stage=stage,
                      namespace_command=lambda s,b,ph:command(roots,s,b,ph),verify=verify,
                      expectations=lambda c,r:verify_expectations(c,r,output,policy),final_check=final_check,
                      resource_check=check_resources)
    print(json.dumps(report,sort_keys=True))


if __name__ == '__main__':
    main()
