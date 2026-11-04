#!/usr/bin/env python3
"""Opt-in ARM enrollment and unit checks; no NVMe, systemd boot or KVM proof."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]
SOURCES = ('scripts/enroll-guest.py', 'scripts/guest-accounts.py', 'scripts/guest-storage.py',
           'scripts/launch-kvm-guest.py', 'configs/guest_config.py', 'configs/guest_identity.py',
           'configs/host-management.py', 'configs/host_config.py', 'configs/package_state.py')
NAMES = {'kernel': 'Image', 'initrd': 'initramfs.cpio.gz', 'disk': 'guest-disk.raw'}


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, REPO / path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


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
# Real pinned sysusers creates the fixture identity in this disposable chroot.
subprocess.run(['/usr/bin/systemd-sysusers','--inline','g a733-vm-fixture 60001','u a733-vm-fixture 60001:60001 "A733 guest fixture" /nonexistent /usr/sbin/nologin'],check=True,capture_output=True,timeout=30)
E.MANAGEMENT.runtime_guard=lambda:None
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
print(json.dumps({'architecture':platform.machine(),'actual_fixture_inputs':records,'prepare_no_output':True,'enrollment':receipt,'published_ownership_and_full_hashes':'passed','guest_uid_disk_write':'passed','guest_uid_kernel_manifest_and_directory_refusals':'passed','different_uid_refused':'passed','existing_guest_refused':'passed','source_inputs_unchanged':'passed','systemd_unit_verify':{'returncode':verified.returncode,'diagnostics':verified.stderr.strip(),'unit':unit},'live_policy':'RAM/NVMe/mounted UUID boundaries mocked only inside disposable file fixture','guest_execution':'not performed'}))
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


def verify(rootfs, guest_directory, *, probe=PROBE, sources=SOURCES, after_probe=None):
    if os.geteuid() != 0: raise ValueError('root required for private ARM fixture')
    fixture = module('enrollment_arm_fixture','tests/verify_guest_file_backend_arm.py')
    maintenance = module('enrollment_arm_binfmt','tests/verify_maintenance_arm.py')
    payload = module('enrollment_arm_payload','build/host-payload.py')
    state = module('enrollment_arm_state','build/host-state.py')
    binfmt = maintenance.binfmt_check(fixture.QEMU_PIN)
    storage = state.validate_workspace_storage(Path('/var/lib'))
    if shutil.disk_usage('/var/lib').free < 3 * 1024**3: raise ValueError('3 GiB free fixed storage required')
    pins = json.loads((REPO/'docs/evidence/guest-fixture-20261104.json').read_bytes())['files']
    with tempfile.TemporaryDirectory(prefix='a733-guest-enrollment-arm-',dir='/var/lib') as directory:
        work=Path(directory); captured=work/'rootfs.tar.gz'
        fixture.capture(rootfs,captured); payload.authenticate(captured,fixture.ROOTFS)
        root=work/'root';root.mkdir(mode=0o700);payload.extract_rootfs(captured,root);fixture.sanitize(root)
        root.chmod(0o755)
        (root/'run/a733-state').mkdir(mode=0o700,exist_ok=True)
        source_records={}
        for name in sources:
            data=fixture.regular(REPO/name);target=root/'source'/name
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data);target.chmod(0o444)
            source_records[name]=fixture.record(data)
        seed=root/'seed';seed.mkdir(mode=0o700);records={}
        for name,filename in NAMES.items():
            capture_seed(guest_directory/filename,seed/filename,pins[filename]);records[name]=pins[filename]
        (seed/'records.json').write_text(json.dumps(records))
        # Resource checks use a captured builder sample, never invented board RAM.
        (root/'proc/meminfo').write_bytes(Path('/proc/meminfo').read_bytes())
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
            'verifier_source':fixture.record(Path(__file__).read_bytes()),'trusted_sources':source_records,
            'ARM_checks':checks,'limits':['RAM/NVMe mount and UUID policy boundaries mocked only inside disposable file fixture.',
            'Memory sample and CPU count belong to native builder, not A733 hardware.',
            'Unit parsed but not started; stop sends SIGTERM and does not prove guest graceful shutdown.',
            'No guest VM, KVM, board boot, production enrollment or hardware acceptance.']}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('rootfs_tar',type=Path);parser.add_argument('guest_directory',type=Path)
    args=parser.parse_args();print(json.dumps(verify(args.rootfs_tar,args.guest_directory),indent=2))
