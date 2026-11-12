#!/usr/bin/env python3
"""Opt-in ARM fresh-guest file restoration; no restored VM/KVM acceptance."""
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
spec=importlib.util.spec_from_file_location('restore','/source/scripts/restore-guest.py')
T=importlib.util.module_from_spec(spec);spec.loader.exec_module(T)
restore_root=pathlib.Path('/run/a733-restored-guests');restore_root.mkdir(mode=0o711);restore_root.chmod(0o711)
destination_uuid='a7330000-2222-4000-8000-000000000005'
restore_args=[str(stream_path),backup['archive']['sha256'],new_pin,destination_uuid]
refusal=subprocess.run(['/usr/bin/python3','-I','-B','/source/scripts/restore-guest.py','prepare','--name','fixture','--archive',restore_args[0],'--archive-sha256',restore_args[1],'--manifest-sha256',restore_args[2],'--guest-uuid',restore_args[3]],capture_output=True,timeout=30)
assert refusal.returncode!=0 and not list(restore_root.iterdir())
T.operation_module().ManualGuestOperation=FixtureAdmission
# Only live hardware and operator boundaries are substituted in the fixture.
T.ENROLL.ROOT=str(restore_root)
T.ENROLL.MANAGEMENT.runtime_guard=lambda:None
T.ENROLL.ACCOUNTS.snapshot=fixture_account_snapshot
T.POLICY.host_namespace=lambda:None
T.ENROLL.STORAGE.verify_guest_directory=lambda fd:dict(context)
T.ENROLL.probe_uuid=lambda observed,expected:None
T.POLICY.service_state=lambda name:{'LoadState':'loaded','ActiveState':'inactive','SubState':'dead','MainPID':'0','ControlPID':'0'}
T.uid_consumers=lambda uid:[]
T.authorize=lambda plan:None
restore_plan=T.prepare(*restore_args,name='fixture')
assert not list(restore_root.iterdir())
restore_plan_path=pathlib.Path('/run/restore-plan.json');restore_plan_path.write_bytes(T.canonical(restore_plan));restore_plan_path.chmod(0o400)
restored=T.apply(*restore_args,str(restore_plan_path),name='fixture')
assert restored['success'] and restored['published'] and restored['durable'],restored
target=restore_root/'fixture';restored_raw=(target/'enrollment.json').read_bytes()
restored_pin=hashlib.sha256(restored_raw).hexdigest();assert restored_pin==restored['manifest_sha256']
restored_manifest=T.MANIFEST.validate_manifest(restored_raw)
assert restored_manifest['config']==new_manifest['config'] and restored_manifest['files']==new_manifest['files']
assert restored_manifest['guest_uuid']==destination_uuid and 'checkpoint' not in restored_manifest
assert restored_manifest['restoration']=={'archive_sha256':backup['archive']['sha256'],'manifest_sha256':new_pin,'guest_uuid':guest_uuid}
for name in ('Image','initramfs.cpio.gz','disk.raw'):
    assert hashlib.sha256((target/name).read_bytes()).digest()==hashlib.sha256((parent/name).read_bytes()).digest()
source_history={p.name:p.read_bytes() for p in (parent/'.checkpoints').iterdir()}
assert source_history=={p.name:p.read_bytes() for p in (target/'.checkpoints').iterdir()}
for name,uid,gid,mode in [('Image',0,0,0o444),('initramfs.cpio.gz',0,0,0o444),('disk.raw',60001,60001,0o600),('enrollment.json',0,0,0o400)]:
    info=(target/name).stat();assert (info.st_uid,info.st_gid,info.st_mode&0o7777)==(uid,gid,mode)
again=T.apply(*restore_args,str(restore_plan_path),name='fixture');assert not again['success'] and not again['published']
assert (target/'enrollment.json').read_bytes()==restored_raw
# The restored base is valid for file launch checks and a later new checkpoint.
cfg=restored_manifest['config'];target_paths={k:str(target/n) for k,n in T.ENROLL.FILENAMES.items()}
launch_args=['/usr/bin/python3','-I','-B','/source/scripts/launch-kvm-guest.py','--dry-run','--memory-mib',str(cfg['memory_mib']),'--cpus',str(cfg['cpus'])]
for key in ('kernel','initrd','disk'):launch_args.extend(['--'+key,target_paths[key],'--'+key+'-sha256',cfg[key]['sha256']])
launch=subprocess.run(launch_args,capture_output=True,text=True,timeout=60);assert launch.returncode==0,launch.stderr
assert json.loads(launch.stdout)['hardware_acceptance']=='pending'
restored_unit=T.ENROLL.CONFIG.render_unit(cfg,'/source/scripts/launch-kvm-guest.py',kvm_gid)
unit_path.write_text(restored_unit)
unit_check=subprocess.run(['/usr/bin/systemd-analyze','verify',str(unit_path)],capture_output=True,text=True,timeout=30)
assert unit_check.returncode==0,unit_check.stderr
P.ENROLL.ROOT=str(restore_root)
next_plan=C.prepare('fixture',restored_pin)
assert not next_plan['requires_update'] and next_plan['old_manifest']['value']['restoration']==restored_manifest['restoration']
assert not list((restore_root/'.enrollment').iterdir())
assert pathlib.Path(paths['enrollment']).read_bytes()==new_data
assert hashlib.sha256(pathlib.Path(paths['disk']).read_bytes()).hexdigest()==current_disk
print(json.dumps({'architecture':platform.machine(),'source_backup':backup,'restore':restored,'restored_manifest_sha256':restored_pin,'source_CLI_refused_before_writes':True,'prepare_no_destination_writes':True,'full_payloads_and_history_match':True,'ownership_and_rebased_provenance':'passed','existing_guest_refused':True,'source_guest_unchanged':True,'restored_launch_file_checks':'passed; dry-run only','restored_unit_verify':{'returncode':unit_check.returncode,'diagnostics':unit_check.stderr.strip()},'restored_checkpoint_inspection':'unchanged disk and new base provenance accepted','private_staging_empty':True,'live_boundaries':'admission/account-placement/mount/UUID/namespace/service/UID census and approval mocked only inside disposable fixture','guest_execution':'not performed','clean_shutdown':'fixture attestation only, not verified'}))
'''


def verify(rootfs, guest_directory):
    enrollment = module('restore_arm_enrollment', 'tests/verify_guest_enrollment_arm.py')
    checkpoint = module('restore_arm_checkpoint', 'tests/verify_guest_checkpoint_arm.py')
    backup = module('restore_arm_backup', 'tests/verify_guest_backup_arm.py')
    markers = (
        (enrollment.PROBE, "print(json.dumps({'architecture':platform.machine(),'actual_fixture_inputs'"),
        (checkpoint.TAIL, "print(json.dumps({'architecture':platform.machine(),'initial_enrollment'"),
        (backup.TAIL, "print(json.dumps({'architecture':platform.machine(),'backup'"))
    if any(source.count(marker) != 1 for source, marker in markers):
        raise ValueError('trusted probe interface changed')
    probe = ''.join(source.split(marker, 1)[0] for source, marker in markers) + TAIL
    compile(probe, '/restore-probe.py', 'exec')
    sources = enrollment.SOURCES + ('configs/guest_manifest.py', 'scripts/guest-checkpoint-policy.py',
        'scripts/checkpoint-guest.py', 'configs/guest_backup.py', 'scripts/backup-guest.py',
        'scripts/guest-restore-source.py', 'scripts/restore-guest.py', 'scripts/verify-guest-backup.py',
        'build/host-state.py', 'build/host-payload.py', 'build/root-selection.py',
        'build/host-initramfs.py', 'build/host-storage-tools.py')
    result = enrollment.verify(rootfs, guest_directory, probe=probe, sources=sources)
    result['classification'] = 'ARM fresh guest file restoration; no restored VM, off-device retention or KVM'
    result['restore_verifier_source'] = {'bytes': Path(__file__).stat().st_size,
        'sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    result['combined_probe'] = {'bytes': len(probe.encode()), 'sha256': hashlib.sha256(probe.encode()).hexdigest()}
    result['limits'] = [
        'Admission uses a no-authority file-engine substitute; actual runtime admission is verified separately.',
        'Admission/account placement, live NVMe topology, namespace, UUID, stopped service, guest UID census and TTY approval substituted only in disposable fixture.',
        'Actual ARM sysusers, archive authentication, full copy/readback, ownership, history, fsync, no-replace publication and file checks execute.',
        'Source and destination use the same builder; no off-device retention, clean shutdown, restored VM, KVM or physical acceptance.',
        'Configured binfmt interpreter pathname/hash checked; kernel-held F descriptor not independently inspected.']
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('rootfs_tar', type=Path)
    parser.add_argument('guest_directory', type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.rootfs_tar, args.guest_directory), indent=2))
