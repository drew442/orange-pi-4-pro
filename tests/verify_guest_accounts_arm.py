#!/usr/bin/env python3
"""Exercise pinned ARM sysusers in a private rootfs; no host account changes."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, REPO / path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


PROBE = r'''
import hashlib,importlib.util,json,os,pathlib,platform,subprocess,sys
assert platform.machine()=='aarch64' and sys.flags.isolated and sys.dont_write_bytecode
base=pathlib.Path('/account-src')
spec=importlib.util.spec_from_file_location('accounts',base/'scripts/guest-accounts.py')
A=importlib.util.module_from_spec(spec);spec.loader.exec_module(A)
def snapshot():return {n:pathlib.Path('/etc',n).read_bytes() for n in A.DATABASES}
initial=snapshot()
config=pathlib.Path('/run/accounts.json')
config.write_text(json.dumps({'schema':1,'guests':[{'name':'fixture','uid':60001,'gid':60001}]}));config.chmod(0o400)
# Real CLI must refuse before account writes in this unsupported environment.
refusal=subprocess.run(['/usr/bin/python3','-I','-B',str(base/'scripts/guest-accounts.py'),'prepare',str(config)],capture_output=True,text=True,timeout=30)
assert refusal.returncode!=0 and snapshot()==initial
# Runtime mount provenance alone is mocked inside this disposable fixture.
# The real source/DB/lock/render/sysusers/postcondition operations run unchanged.
A.MANAGEMENT.runtime_guard=lambda:None
plan=pathlib.Path('/run/accounts-plan.json')
plan.write_bytes(A.canonical(A.prepare(config)));plan.chmod(0o400)
receipt=A.apply(config,plan)
assert receipt['success'] and receipt['accounts_may_be_modified'],receipt
current=snapshot();validated=A.IDENTITY.validate_config(config.read_bytes())
A.validate_accounts(validated,current,complete=True)
# All preexisting users/groups/credential rows survive byte-for-byte.
for name,data in initial.items():
    before={r.split(b':',1)[0]:r for r in data.splitlines()}
    after={r.split(b':',1)[0]:r for r in current[name].splitlines()}
    assert all(after.get(k)==v for k,v in before.items()),name
plan.write_bytes(A.canonical(A.prepare(config)));plan.chmod(0o400)
second=A.apply(config,plan)
assert second['success'] and not second['accounts_may_be_modified'] and snapshot()==current,second
# Explicit host-account collision must be rejected without mutation.
config.chmod(0o600)
config.write_text(json.dumps({'schema':1,'guests':[{'name':'root','uid':60001,'gid':60001}]}));config.chmod(0o400)
try:A.prepare(config)
except ValueError:pass
else:raise AssertionError('UID collision accepted')
assert snapshot()==current
version=subprocess.run(['/usr/bin/systemd-sysusers','--version'],check=True,capture_output=True,text=True,timeout=30).stdout.splitlines()[0]
assert version.startswith('systemd 252 '),version
print(json.dumps({'architecture':platform.machine(),'sysusers_version':version,'real_CLI_refusal_before_writes':True,'private_fixture_apply':receipt,'idempotent_apply':second,'unrelated_account_rows':'byte-identical','UID_collision_refusal':True,'guest_account':{'name':'a733-vm-fixture','uid':60001,'gid':60001,'password':'locked; no hash','home':'/nonexistent','shell':'/usr/sbin/nologin','supplementary_groups':[]},'mount_provenance':'mocked only in disposable fixture; no actual host activation'}))
'''


def verify(rootfs):
    if os.geteuid() != 0:
        raise ValueError('root required for private authenticated ARM chroot')
    fixture = module('accounts_arm_fixture', 'tests/verify_guest_file_backend_arm.py')
    maintenance = module('accounts_arm_binfmt', 'tests/verify_maintenance_arm.py')
    binfmt = maintenance.binfmt_check(fixture.QEMU_PIN)
    payload = module('accounts_arm_payload', 'build/host-payload.py')
    state = module('accounts_arm_state', 'build/host-state.py')
    storage = state.validate_workspace_storage(Path('/var/lib'))
    if shutil.disk_usage('/var/lib').free < 2 * 1024**3:
        raise ValueError('2 GiB fixed-storage free space required')
    with tempfile.TemporaryDirectory(prefix='a733-guest-accounts-arm-', dir='/var/lib') as directory:
        work = Path(directory)
        captured = work / 'rootfs.tar.gz'
        fixture.capture(rootfs, captured)
        payload.authenticate(captured, fixture.ROOTFS)
        root = work / 'root'; root.mkdir(mode=0o700)
        payload.extract_rootfs(captured, root)
        root.chmod(0o700); fixture.sanitize(root)
        (root / 'run/a733-state').mkdir(mode=0o700, exist_ok=True)
        records = {}
        for name in ('scripts/guest-accounts.py', 'configs/guest_identity.py',
                     'configs/host-management.py', 'configs/host_config.py', 'configs/package_state.py'):
            data = fixture.regular(REPO / name)
            target = root / 'account-src' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data); target.chmod(0o400)
            records[name] = fixture.record(data)
        process = subprocess.Popen(['chroot', str(root), '/usr/bin/python3', '-I', '-B', '-c', PROBE],
                                   env=fixture.ENV, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, start_new_session=True)
        try:
            stdout, stderr = process.communicate(timeout=120)
        except BaseException:
            os.killpg(process.pid, signal.SIGKILL); process.wait(); raise
        if process.returncode:
            raise ValueError('ARM sysusers probe failed: ' + stderr[-16000:])
        checks = json.loads(stdout)
    return {'classification': 'private ARM sysusers file checks; no board boot or account activation',
            'rootfs': fixture.ROOTFS, 'binfmt': binfmt, 'storage': storage,
            'verifier_source': fixture.record(Path(__file__).read_bytes()),
            'trusted_sources': records, 'ARM_checks': checks,
            'limits': ['Runtime mount provenance was mocked only inside the authenticated private chroot.',
                       'No host account DB, physical storage, guest VM, KVM or deployment was changed.',
                       'Configured interpreter pathname/hash checked; kernel-held binfmt F descriptor not independently inspected.']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('rootfs_tar', type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.rootfs_tar), indent=2))
