#!/usr/bin/env python3
"""Opt-in ARM stopped-disk checkpoint fixtures; no VM or clean-shutdown proof."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, REPO / path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


TAIL = r'''
import base64,fcntl,signal,stat
initial_enrollment=dict(receipt)
initial_unit=dict(returncode=verified.returncode,diagnostics=verified.stderr.strip())
old_data=pathlib.Path(paths['enrollment']).read_bytes();old_pin=hashlib.sha256(old_data).hexdigest()
assert old_pin==receipt['manifest_sha256']
spec=importlib.util.spec_from_file_location('checkpoint','/source/scripts/checkpoint-guest.py')
C=importlib.util.module_from_spec(spec);spec.loader.exec_module(C)
P=C.POLICY
unsupported=subprocess.run(['/usr/bin/python3','-I','-B','/source/scripts/checkpoint-guest.py','prepare','--name','fixture','--manifest-sha256',old_pin],capture_output=True,text=True,timeout=30)
assert unsupported.returncode!=0 and pathlib.Path(paths['enrollment']).read_bytes()==old_data and not (parent/'.checkpoints').exists()
P.MANAGEMENT.runtime_guard=lambda:None
P.host_namespace=lambda:None
P.STORAGE.verify_guest_directory=lambda fd:dict(context)
P.STORAGE.verify_guest_storage=lambda *a,**kw:dict(context,boot_inputs_checked=2)
P.ENROLL.probe_uuid=lambda observed,expected:None
P.service_state=lambda name:{'LoadState':'loaded','ActiveState':'inactive','SubState':'dead','MainPID':'0','ControlPID':'0'}
P.disk_consumers=lambda fd:[]
C.authorize=lambda plan:None
current_disk=hashlib.sha256(pathlib.Path(paths['disk']).read_bytes()).hexdigest()
assert current_disk!=config['disk']['sha256']
def dryrun(cfg):
    args=['/usr/bin/python3','-I','-B','/source/scripts/launch-kvm-guest.py','--dry-run','--memory-mib',str(cfg['memory_mib']),'--cpus',str(cfg['cpus'])]
    for key in ('kernel','initrd','disk'):args.extend(['--'+key,paths[key],'--'+key+'-sha256',cfg[key]['sha256']])
    return subprocess.run(args,capture_output=True,text=True,timeout=60)
old_launch=dryrun(config);assert old_launch.returncode!=0 and 'SHA-256 mismatch' in old_launch.stderr
# Actual separate-process flock must refuse even with fixture live boundaries.
busy_code="import fcntl,sys;f=open('/run/a733-guests/fixture/disk.raw','rb');fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB);print('locked',flush=True);sys.stdin.read(1)"
busy=subprocess.Popen(['/usr/bin/python3','-I','-B','-c',busy_code],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
try:
    assert busy.stdout.readline().strip()=='locked'
    try:C.prepare('fixture',old_pin)
    except BlockingIOError:pass
    else:raise AssertionError('busy disk checkpoint accepted')
finally:
    busy.communicate('x',timeout=30)
assert busy.returncode==0
plan=C.prepare('fixture',old_pin)
assert plan['requires_update'] and not (parent/'.checkpoints').exists()
review_path=pathlib.Path('/run/checkpoint-plan.json');review_path.write_bytes(C.canonical(plan));review_path.chmod(0o400)
# Queue real SIGTERM inside the actual replace region. Its receipt must retain
# visibility and durability, even though interruption makes success false.
def interrupted(signum,frame):raise KeyboardInterrupt
old_handler=signal.signal(signal.SIGTERM,interrupted)
old_mask=signal.pthread_sigmask(signal.SIG_UNBLOCK,{signal.SIGTERM})
real_replace=C.os.replace
fired=False
def replace_and_signal(*args,**kwargs):
    global fired
    real_replace(*args,**kwargs)
    if not fired:fired=True;os.kill(os.getpid(),signal.SIGTERM)
C.os.replace=replace_and_signal
try:checkpoint=C.apply('fixture',old_pin,review_path)
finally:
    C.os.replace=real_replace
    signal.signal(signal.SIGTERM,old_handler)
    signal.pthread_sigmask(signal.SIG_SETMASK,old_mask)
assert fired and not checkpoint['success'] and checkpoint['published'] and checkpoint['durable'] and checkpoint['history_durable'],checkpoint
new_data=pathlib.Path(paths['enrollment']).read_bytes();new_pin=hashlib.sha256(new_data).hexdigest()
assert new_pin==checkpoint['new_manifest_sha256']
new_manifest=C.MANIFEST.validate_manifest(new_data)
assert new_manifest['config']['disk']['sha256']==current_disk and new_manifest['checkpoint']['number']==1
assert new_manifest['checkpoint']['previous_manifest_sha256']==old_pin
assert new_manifest['checkpoint']['previous_disk_sha256']==config['disk']['sha256']
archives=list((parent/'.checkpoints').iterdir());assert len(archives)==1
archive=json.loads(archives[0].read_bytes())
assert base64.b64decode(archive['old_manifest_base64'],validate=True)==old_data
assert base64.b64decode(archive['new_manifest_base64'],validate=True)==new_data
for path,mode in [(parent/'.checkpoints',0o700),(archives[0],0o400),(pathlib.Path(paths['enrollment']),0o400)]:
    info=path.stat();assert (info.st_uid,info.st_gid,info.st_mode&0o7777)==(0,0,mode)
assert hashlib.sha256(pathlib.Path(paths['disk']).read_bytes()).hexdigest()==current_disk
try:C.prepare('fixture',old_pin)
except ValueError:pass
else:raise AssertionError('obsolete manifest pin accepted')
noop_plan=C.prepare('fixture',new_pin);assert not noop_plan['requires_update']
review_path.write_bytes(C.canonical(noop_plan));review_path.chmod(0o400)
noop=C.apply('fixture',new_pin,review_path)
assert noop['success'] and noop['unchanged'] and not noop['published'] and not noop['disk_flush_attempted'],noop
assert len(list((parent/'.checkpoints').iterdir()))==1
assert pathlib.Path(paths['enrollment']).read_bytes()==new_data
new_launch=dryrun(new_manifest['config']);assert new_launch.returncode==0,new_launch.stderr
assert json.loads(new_launch.stdout)['hardware_acceptance']=='pending'
unit=E.CONFIG.render_unit(new_manifest['config'],'/source/scripts/launch-kvm-guest.py',kvm_gid)
unit_path.write_text(unit)
unit_check=subprocess.run(['/usr/bin/systemd-analyze','verify',str(unit_path)],capture_output=True,text=True,timeout=30)
assert unit_check.returncode==0,unit_check.stderr
print(json.dumps({'architecture':platform.machine(),'initial_enrollment':initial_enrollment,'initial_unit':initial_unit,'real_checkpoint_CLI_refusal_before_writes':True,'old_launch_pin_refusal':True,'real_cross_process_disk_lock_refusal':True,'checkpoint_after_queued_SIGTERM':checkpoint,'history_exact_before_after_bytes':True,'disk_bytes_unchanged_by_checkpoint':True,'old_manifest_pin_refused':True,'idempotent_checkpoint':noop,'new_launch_file_checks':'passed; dry-run only','updated_unit_verify':{'returncode':unit_check.returncode,'diagnostics':unit_check.stderr.strip()},'manifest_record':{'old_sha256':old_pin,'new_sha256':new_pin,'new_disk_sha256':current_disk},'live_boundaries':'mount/UUID/namespace/service/consumer scan and operator approval mocked only inside authenticated disposable fixture','guest_execution':'not performed','clean_shutdown':'fixture attestation only, not verified'}))
'''


def verify(rootfs, guest_directory):
    enrollment = module('checkpoint_arm_enrollment', 'tests/verify_guest_enrollment_arm.py')
    marker = "print(json.dumps({'architecture':platform.machine(),'actual_fixture_inputs'"
    if enrollment.PROBE.count(marker) != 1:
        raise ValueError('trusted enrollment probe interface changed')
    prefix = enrollment.PROBE.split(marker, 1)[0]
    probe = prefix + TAIL
    compile(probe, '/checkpoint-probe.py', 'exec')
    sources = enrollment.SOURCES + ('configs/guest_manifest.py',
                                    'scripts/guest-checkpoint-policy.py', 'scripts/checkpoint-guest.py')
    result = enrollment.verify(rootfs, guest_directory, probe=probe, sources=sources)
    result['classification'] = 'ARM enrollment/checkpoint file checks; no physical guest shutdown, VM or KVM proof'
    result['checkpoint_verifier_source'] = {'bytes': Path(__file__).stat().st_size,
        'sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    result['combined_probe'] = {'bytes': len(probe.encode()), 'sha256': hashlib.sha256(probe.encode()).hexdigest()}
    result['limits'] = ['Live storage, namespace, stopped service, consumer census and TTY boundaries mocked only in disposable fixture.',
                        'Separate subprocess flock, actual full file hashes, ownership, fsync, archive and atomic replacement execute under ARM userspace.',
                        'Dry-run and unit parsing are not guest execution; no filesystem cleanliness, backup, physical NVMe or KVM acceptance.',
                        'Configured binfmt interpreter pathname/hash checked; kernel-held F descriptor not independently inspected.']
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('rootfs_tar', type=Path)
    parser.add_argument('guest_directory', type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.rootfs_tar, args.guest_directory), indent=2))
