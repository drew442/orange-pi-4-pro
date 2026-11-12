#!/usr/bin/env python3
"""Opt-in ARM enrollment and unit checks; no NVMe, systemd boot or KVM proof."""
import argparse
import contextlib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]
NAMES = {'kernel': 'Image', 'initrd': 'initramfs.cpio.gz', 'disk': 'guest-disk.raw'}
FIXTURE_PROFILES = ('legacy', 'fixed-seed-http')


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, REPO / path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


# Keep the disposable chroot's trusted import closure aligned with packaging.
SOURCES = module('enrollment_arm_tools', 'build/maintenance-tools.py').SOURCES


def load_fixture_profile(fixture_profile='legacy'):
    """Select only repository-pinned evidence, independently of input paths."""
    if type(fixture_profile) is not str or fixture_profile not in FIXTURE_PROFILES:
        raise ValueError('unsupported guest fixture profile')
    legacy_path = 'docs/evidence/guest-fixture-20261104.json'
    selected_path = (legacy_path if fixture_profile == 'legacy' else
                     'docs/evidence/guest-seed-fixture-20261104.json')

    def read_evidence(path):
        data = (REPO / path).read_bytes()
        value = json.loads(data)
        if type(value) is not dict or type(value.get('schema')) is not int or value['schema'] != 1:
            raise ValueError('invalid fixture evidence schema')
        return data, value

    def pins_from(manifest):
        if (type(manifest) is not dict or type(manifest.get('schema')) is not int or
                manifest['schema'] != 1 or type(manifest.get('files')) is not dict):
            raise ValueError('invalid fixture manifest schema')
        pins = {}
        for key, filename in NAMES.items():
            record = manifest['files'].get(filename)
            if type(record) is not dict or set(record) != {'bytes', 'sha256'}:
                raise ValueError('invalid fixture artifact record')
            size, digest = record['bytes'], record['sha256']
            maximum = {'kernel': 8 * 1024**2, 'initrd': 64 * 1024**2,
                       'disk': 128 * 1024**2}[key]
            if (type(size) is not int or not 0 < size <= maximum or
                    (key == 'disk' and size != 128 * 1024**2) or type(digest) is not str or
                    re.fullmatch('[0-9a-f]{64}', digest) is None):
                raise ValueError('invalid fixture artifact size/hash')
            pins[filename] = dict(record)
        return pins

    data, legacy = read_evidence(legacy_path)
    pins = pins_from(legacy)
    provenance = {'profile': fixture_profile, 'evidence_path': selected_path}
    if fixture_profile == 'fixed-seed-http':
        data, evidence = read_evidence(selected_path)
        manifest = evidence.get('artifact_manifest')
        selected = pins_from(manifest)
        if selected['Image'] != pins['Image']:
            raise ValueError('fixture kernel differs from legacy pin')
        seed = manifest.get('seed_builder')
        if (type(seed) is not dict or type(seed.get('schema')) is not int or seed['schema'] != 1 or
                type(seed.get('bytes')) is not int or seed['bytes'] != 134217728 or
                {key: seed.get(key) for key in ('bytes', 'sha256')} != selected['guest-disk.raw'] or
                seed.get('profile') != 'a733-disposable-guest-seed-v1' or
                seed.get('label') != 'kvm-fixture' or
                legacy.get('disk_uuid') != 'd67f33ba-e201-46bc-980b-61901bbd1156' or
                legacy.get('disk_fixture_id') != 'a733-kvm-guest-fixture-v1' or
                seed.get('uuid') != legacy.get('disk_uuid') or
                manifest.get('disk_uuid') != legacy.get('disk_uuid') or
                manifest.get('disk_fixture_id') != legacy.get('disk_fixture_id') or
                type(manifest.get('initial_counter')) is not int or manifest['initial_counter'] != 0):
            raise ValueError('invalid fixed seed identity/profile')
        pins = selected
        provenance['seed_builder'] = dict(seed)
    return pins, {**provenance, 'evidence_sha256': hashlib.sha256(data).hexdigest()}


PROBE = r"""
import errno,grp,hashlib,importlib.util,json,os,pathlib,platform,subprocess,sys
assert platform.machine()=='aarch64' and sys.flags.isolated and sys.dont_write_bytecode
spec=importlib.util.spec_from_file_location('enroll','/source/scripts/enroll-guest.py')
E=importlib.util.module_from_spec(spec);spec.loader.exec_module(E)
records=json.loads(pathlib.Path('/seed/records.json').read_bytes())
config={'schema':1,'name':'fixture','uid':60001,'gid':60001,'memory_mib':128,'memory_max_mib':256,'cpus':1,**records}
config_path=pathlib.Path('/run/enrollment-config.json');config_path.write_bytes(E.CONFIG.canonical(config));config_path.chmod(0o400)
root=pathlib.Path(E.ROOT);root.mkdir(mode=0o711);root.chmod(0o711)
guest_uuid='a7330000-1111-4000-8000-000000000005'
inputs=[str(config_path),'/seed/Image','/seed/initramfs.cpio.gz','/seed/guest-disk.raw',guest_uuid]
# The production live guard must reject the unsupported chroot without creating a guest.
refusal=subprocess.run(['/usr/bin/python3','-I','-B','/source/scripts/enroll-guest.py','prepare','--config',inputs[0],'--kernel',inputs[1],'--initrd',inputs[2],'--disk',inputs[3],'--guest-uuid',guest_uuid],capture_output=True,text=True,timeout=30)
assert refusal.returncode!=0 and not list(root.iterdir())
# This file-engine substitute grants no admission authority. Real runtime
# admission is verified separately; the unsupported production CLI ran above.
class FixtureAdmission:
    def __init__(self,name,*,create=False):self.name=name
    def recheck(self):pass
    def close(self):pass
    def __enter__(self):return self
    def __exit__(self,*unused):self.close()
E.operation_module().ManualGuestOperation=FixtureAdmission
# Real pinned sysusers creates the fixture identity in this disposable chroot.
subprocess.run(['/usr/bin/systemd-sysusers','--inline','g a733-vm-fixture 60001','u a733-vm-fixture 60001:60001 "A733 guest fixture" /nonexistent /usr/sbin/nologin'],check=True,capture_output=True,timeout=30)
E.MANAGEMENT.runtime_guard=lambda:None
# Real sysusers/database bytes remain exercised. This disposable chroot has no
# production RAM-overlay mount; substitute only snapshot placement authority.
def fixture_account_snapshot():
    return {name:pathlib.Path('/etc',name).read_bytes() for name in E.ACCOUNTS.DATABASES}
E.ACCOUNTS.snapshot=fixture_account_snapshot
context={'namespace':'fixture-no-NVMe','guest_source':'fixture-no-block-device','root_source':'fixture','root_device':[0,1],'guest_device':[0,2],'guest_mount_id':1,'boot_inputs_checked':0}
E.STORAGE.verify_guest_directory=lambda fd:dict(context)
E.probe_uuid=lambda observed,expected:None
before={name:hashlib.sha256(pathlib.Path('/seed',filename).read_bytes()).hexdigest() for name,filename in [('kernel','Image'),('initrd','initramfs.cpio.gz'),('disk','guest-disk.raw')]}
plan=E.prepare(*inputs)
assert not list(root.iterdir())
plan_path=pathlib.Path('/run/enrollment-plan.json');plan_path.write_bytes(E.canonical(plan));plan_path.chmod(0o400)
receipt=E.apply(*inputs,plan_path)
assert receipt['success'],receipt
paths=E.CONFIG.paths(config)
expected={'kernel':(0,0,0o444),'initrd':(0,0,0o444),'disk':(60001,60001,0o600),'enrollment':(0,0,0o400)}
for key,path in paths.items():
    info=pathlib.Path(path).stat();assert (info.st_uid,info.st_gid,info.st_mode&0o7777)==expected[key],(key,info)
    if key!='enrollment':assert hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()==records[key]['sha256']
parent=pathlib.Path(paths['disk']).parent;info=parent.stat();assert (info.st_uid,info.st_gid,info.st_mode&0o7777)==(0,60001,0o710)
manifest=json.loads(pathlib.Path(paths['enrollment']).read_bytes());assert manifest['config']==config and manifest['guest_uuid']==guest_uuid
# Actual UID/GID changes test ordinary Linux access, not a guest VM.
child=r'''
import os,pathlib,sys
base=pathlib.Path('/run/a733-guests/fixture')
def refused(call):
    try:call()
    except PermissionError:pass
    else:raise AssertionError('unexpected permission')
assert os.getuid()==60001 and os.getgroups()==[]
assert (base/'Image').read_bytes()
refused(lambda:(base/'Image').open('r+b'))
refused(lambda:(base/'enrollment.json').read_bytes())
refused(lambda:(base/'unexpected').write_bytes(b'x'))
refused(lambda:(base/'Image').rename(base/'other'))
with (base/'disk.raw').open('r+b') as disk:disk.seek(-1,2);disk.write(b'!');disk.flush();os.fsync(disk.fileno())
'''
def as_guest():os.setgroups([]);os.setgid(60001);os.setuid(60001)
subprocess.run(['/usr/bin/python3','-I','-B','-c',child],preexec_fn=as_guest,check=True,capture_output=True,timeout=30)
assert pathlib.Path(paths['disk']).read_bytes()[-1:]==b'!'
def as_other():os.setgroups([]);os.setgid(60002);os.setuid(60002)
other=subprocess.run(['/usr/bin/python3','-I','-B','-c',"open('/run/a733-guests/fixture/disk.raw','rb')"],preexec_fn=as_other,capture_output=True,text=True,timeout=30)
assert other.returncode!=0
try:E.prepare(*inputs)
except ValueError:pass
else:raise AssertionError('existing guest accepted')
assert not (parent/'unexpected').exists() and (parent/'Image').exists()
for key,filename in [('kernel','Image'),('initrd','initramfs.cpio.gz'),('disk','guest-disk.raw')]:assert hashlib.sha256(pathlib.Path('/seed',filename).read_bytes()).hexdigest()==before[key]
try:kvm_gid=grp.getgrnam('kvm').gr_gid
except KeyError:
    subprocess.run(['/usr/bin/systemd-sysusers','--inline','g kvm 104'],check=True,capture_output=True,timeout=30);kvm_gid=grp.getgrnam('kvm').gr_gid
unit=E.CONFIG.render_unit(config,'/source/scripts/launch-kvm-guest.py',kvm_gid)
unit_path=pathlib.Path('/etc/systemd/system/a733-guest-fixture.service');unit_path.write_text(unit)
verified=subprocess.run(['/usr/bin/systemd-analyze','verify',str(unit_path)],capture_output=True,text=True,timeout=30)
assert verified.returncode==0,verified.stderr
print(json.dumps({'architecture':platform.machine(),'actual_fixture_inputs':records,'prepare_no_output':True,'enrollment':receipt,'published_ownership_and_full_hashes':'passed','guest_uid_disk_write':'passed','guest_uid_kernel_manifest_and_directory_refusals':'passed','different_uid_refused':'passed','existing_guest_refused':'passed','source_inputs_unchanged':'passed','systemd_unit_verify':{'returncode':verified.returncode,'diagnostics':verified.stderr.strip(),'unit':unit},'live_policy':'admission/RAM/NVMe/mounted UUID boundaries mocked only inside disposable file fixture','guest_execution':'not performed'}))
"""


def capture_seed(source, target, expected):
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream, target.open('xb') as output:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != expected['bytes']:
            raise ValueError('seed metadata differs')
        digest, size = hashlib.sha256(), 0
        while size < expected['bytes']:
            data = stream.read(min(1024**2, expected['bytes'] - size))
            if not data: raise ValueError('short seed capture')
            digest.update(data); size += len(data)
            if data.strip(b'\0'): output.write(data)
            else: output.seek(len(data), os.SEEK_CUR)
        output.truncate(size)
        after = os.fstat(stream.fileno())
        if digest.hexdigest() != expected['sha256'] or any(getattr(after, key) != getattr(info, key)
            for key in ('st_dev','st_ino','st_mode','st_nlink','st_size','st_mtime_ns','st_ctime_ns')):
            raise ValueError('seed capture size/hash/identity differs')
    target.chmod(0o400)


def verify(rootfs, guest_directory, *, probe=PROBE, sources=SOURCES, after_probe=None,
           probe_context=None, source_entries=None, source_prefix='/source', fixture_profile='legacy'):
    pins, profile = load_fixture_profile(fixture_profile)
    if os.geteuid() != 0: raise ValueError('root required for private ARM fixture')
    fixture = module('enrollment_arm_fixture','tests/verify_guest_file_backend_arm.py')
    maintenance = module('enrollment_arm_binfmt','tests/verify_maintenance_arm.py')
    payload = module('enrollment_arm_payload','build/host-payload.py')
    state = module('enrollment_arm_state','build/host-state.py')
    binfmt = maintenance.binfmt_check(fixture.QEMU_PIN)
    storage = state.validate_workspace_storage(Path('/var/lib'))
    if shutil.disk_usage('/var/lib').free < 3 * 1024**3: raise ValueError('3 GiB free fixed storage required')
    with tempfile.TemporaryDirectory(prefix='a733-guest-enrollment-arm-',dir='/var/lib') as directory:
        work=Path(directory); captured=work/'rootfs.tar.gz'
        fixture.capture(rootfs,captured); payload.authenticate(captured,fixture.ROOTFS)
        root=work/'root';root.mkdir(mode=0o700);payload.extract_rootfs(captured,root);fixture.sanitize(root)
        root.chmod(0o755)
        (root/'run/a733-state').mkdir(mode=0o700,exist_ok=True)
        source_records={}
        if source_prefix not in ('/source', '/usr/lib/a733-tools'):
            raise ValueError('unsupported trusted source staging prefix')
        captured_sources = ({name: (0o444, fixture.regular(REPO/name)) for name in sources}
                            if source_entries is None else source_entries)
        for name, (mode, data) in captured_sources.items():
            if not name or Path(name).is_absolute() or '..' in Path(name).parts or mode != 0o444:
                raise ValueError('invalid trusted source entry')
            target=root/source_prefix.lstrip('/')/name
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data);target.chmod(0o444)
            source_records[name]=fixture.record(data)
        if source_prefix == '/usr/lib/a733-tools':
            prefix = root / source_prefix.lstrip('/')
            for directory in sorted((p for p in prefix.rglob('*') if p.is_dir()), reverse=True):
                directory.chmod(0o555)
            prefix.chmod(0o555)
        seed=root/'seed';seed.mkdir(mode=0o700);records={}
        for name,filename in NAMES.items():
            capture_seed(guest_directory/filename,seed/filename,pins[filename]);records[name]=pins[filename]
        (seed/'records.json').write_text(json.dumps(records))
        # Resource checks use a captured builder sample, never invented board RAM.
        (root/'proc/meminfo').write_bytes(Path('/proc/meminfo').read_bytes())
        with (probe_context(root) if probe_context else contextlib.nullcontext()):
            process=subprocess.Popen(['chroot',str(root),'/usr/bin/python3','-I','-B','-c',probe],
                                     env=fixture.ENV,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
            try:
                stdout,stderr=process.communicate(timeout=180)
            except BaseException:
                os.killpg(process.pid,signal.SIGKILL);process.wait();raise
            if process.returncode:raise ValueError('ARM enrollment probe failed: '+stderr[-16000:])
            checks=json.loads(stdout)
            if after_probe is not None:
                checks['native_followup'] = after_probe(root, checks)
    return {'classification':'ARM enrollment and unit file checks; no physical NVMe, systemd boot or KVM proof',
            'rootfs':fixture.ROOTFS,'binfmt':binfmt,'builder_staging_storage':storage,
            'fixture_profile':profile,
            'verifier_source':fixture.record(Path(__file__).read_bytes()),'trusted_sources':source_records,
            'ARM_checks':checks,'limits':['Admission, RAM/NVMe mount and UUID policy boundaries mocked only inside disposable file fixture.',
            'Admission uses a no-authority file-engine substitute; actual runtime admission is verified separately.',
            'Memory sample and CPU count belong to native builder, not A733 hardware.',
            'Unit parsed but not started; stop sends SIGTERM and does not prove guest graceful shutdown.',
            'No guest VM, KVM, board boot, production enrollment or hardware acceptance.']}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('rootfs_tar',type=Path);parser.add_argument('guest_directory',type=Path)
    parser.add_argument('--fixture-profile', choices=FIXTURE_PROFILES, default='legacy')
    args=parser.parse_args();print(json.dumps(verify(args.rootfs_tar,args.guest_directory,fixture_profile=args.fixture_profile),indent=2))
