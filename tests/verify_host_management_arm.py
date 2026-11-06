#!/usr/bin/env python3
"""Opt-in root ARM64 management checks in a private authenticated chroot.

No mounts, network configuration, listeners or physical storage targets. Test
private keys exist only in the temporary fixture and are deleted afterward.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('management_payload', REPO / 'build/host-payload.py')
PAYLOAD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PAYLOAD)


def verify(rootfs):
    if os.geteuid() != 0:
        raise ValueError('root required for private ARM64 chroot')
    expected = json.loads((REPO / 'docs/evidence/host-userspace-20261104.json').read_text())['files']['rootfs.tar.gz']
    descriptor = os.open(rootfs, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as source, tempfile.TemporaryDirectory(prefix='a733-management-', dir='/var/tmp') as temporary:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise ValueError('userspace archive must be regular')
        work = Path(temporary)
        captured = work / 'rootfs.tar.gz'
        with captured.open('xb') as target:
            shutil.copyfileobj(source, target)
        PAYLOAD.authenticate(captured, expected)
        root = work / 'root'
        root.mkdir()
        PAYLOAD.extract_rootfs(captured, root)
        runtime = PAYLOAD.install_management(root)
        for path, mode in (('run/a733-state', 0o700), ('run/a733-state/identity', 0o700),
                           ('run/a733-state/identity/ssh', 0o700), ('run/sshd', 0o755),
                           ('run/a733-management', 0o700)):
            target = root / path
            target.mkdir(parents=True, exist_ok=True)
            target.chmod(mode)
        program = r'''import contextlib,ctypes,hashlib,importlib.util,io,json,os,pathlib,subprocess
path=pathlib.Path('/usr/lib/orangepi-hypervisor/host-management.py')
spec=importlib.util.spec_from_file_location('management',path)
M=importlib.util.module_from_spec(spec); spec.loader.exec_module(M)
for name in ('/usr/sbin/ip','/sbin/blkid','/usr/bin/python3','/usr/bin/ssh-keygen','/usr/sbin/sshd'):
    assert pathlib.Path(name).is_file() and os.access(name,os.X_OK),name
cfg={'schema':1,'hostname':'a733-arm-fixture','management_mac':'02:73:30:00:00:01','management_cidr':'0.0.0.0/0','dhcp':True}
key=M.ensure_host_key(M.STATE/'identity/ssh')
first=hashlib.sha256(key.read_bytes()).digest()
with contextlib.redirect_stdout(io.StringIO()) as identity_output:
    M.log_host_identity('192.0.2.10',key)
first_identity_log=identity_output.getvalue()
assert key.stat().st_nlink==1
assert M.ensure_host_key(M.STATE/'identity/ssh')==key
assert hashlib.sha256(key.read_bytes()).digest()==first
with contextlib.redirect_stdout(io.StringIO()) as identity_output:
    M.log_host_identity('192.0.2.10',key)
assert identity_output.getvalue()==first_identity_log
public=key.with_name(key.name+'.pub')
public_bytes=public.read_bytes(); public.unlink()
M.ensure_host_key(M.STATE/'identity/ssh')
assert public.read_bytes()==public_bytes and hashlib.sha256(key.read_bytes()).digest()==first
_,fingerprint=M.CONFIG.validate_public_key(public_bytes)
assert first_identity_log=='A733 SSH host key for 192.0.2.10: ED25519 '+fingerprint+'\n'
assert key.read_bytes().decode() not in first_identity_log and public_bytes.decode().strip() not in first_identity_log
rsa=pathlib.Path('/run/a733-management/test-admin-rsa')
subprocess.run(['/usr/bin/ssh-keygen','-q','-t','rsa','-b','3072','-N','','-f',str(rsa)],check=True,capture_output=True)
rsa_public=rsa.with_name(rsa.name+'.pub')
admin_bundle=rsa_public.read_bytes()+public_bytes
canonical,admin_fingerprints=M.CONFIG.validate_public_keys(admin_bundle)
assert len(admin_fingerprints)==2
for path,expected in zip((rsa_public,public),admin_fingerprints):
    actual=subprocess.run(['/usr/bin/ssh-keygen','-lf',str(path)],check=True,capture_output=True,text=True).stdout.split()[1]
    assert actual==expected
assert M.CONFIG.validate_public_keys(canonical)==(canonical,admin_fingerprints)
rsa.unlink()  # Disposable test identity; never an installed host key.
conf=pathlib.Path('/run/a733-management/sshd_config')
conf.write_text(M.ssh_config('192.0.2.10',cfg)); conf.chmod(0o600)
subprocess.run(['/usr/sbin/sshd','-t','-f',str(conf)],check=True,capture_output=True)
result=subprocess.run(['/usr/sbin/sshd','-T','-f',str(conf),'-C','user=root,addr=192.0.2.20,host=fixture'],check=True,capture_output=True,text=True)
values={}
for line in result.stdout.splitlines():
    name,_,value=line.partition(' '); values.setdefault(name,[]).append(value)
required={'addressfamily':'inet','listenaddress':'192.0.2.10:22','authenticationmethods':'publickey','passwordauthentication':'no','kbdinteractiveauthentication':'no','usepam':'yes','allowusers':'root@0.0.0.0/0','disableforwarding':'yes','authorizedkeysfile':'/root/.ssh/authorized_keys'}
for name,value in required.items(): assert values.get(name)==[value],(name,values.get(name))
assert values['permitrootlogin'][0] in ('without-password','prohibit-password')
assert 'ssh-rsa' not in values['pubkeyacceptedalgorithms'][0].split(',')
assert 'rsa-sha2-256' in values['pubkeyacceptedalgorithms'][0].split(',')
# Account checks are distinct from an SSH authentication/session test.
locked=[line.split(':')[1] for line in pathlib.Path('/etc/shadow').read_text().splitlines() if line.startswith('root:')][0]
assert locked.startswith(('!','*'))
callback_type=ctypes.CFUNCTYPE(ctypes.c_int,ctypes.c_int,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p)
@callback_type
def no_conversation(*args): return 19
class Conversation(ctypes.Structure): _fields_=[('conv',callback_type),('data',ctypes.c_void_p)]
conversation=Conversation(no_conversation,None); handle=ctypes.c_void_p()
pam=ctypes.CDLL('libpam.so.0')
pam.pam_start.argtypes=[ctypes.c_char_p,ctypes.c_char_p,ctypes.POINTER(Conversation),ctypes.POINTER(ctypes.c_void_p)]
pam.pam_set_item.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_void_p]
pam.pam_acct_mgmt.argtypes=[ctypes.c_void_p,ctypes.c_int]
pam.pam_end.argtypes=[ctypes.c_void_p,ctypes.c_int]
assert pam.pam_start(b'sshd',b'root',ctypes.byref(conversation),ctypes.byref(handle))==0
try:
    assert pam.pam_set_item(handle,4,ctypes.cast(ctypes.c_char_p(b'192.0.2.20'),ctypes.c_void_p))==0
    account_status=pam.pam_acct_mgmt(handle,0)
    assert account_status==0,account_status
finally:
    pam.pam_end(handle,0)
before=pathlib.Path('/etc/hostname').read_bytes() if pathlib.Path('/etc/hostname').exists() else None
failure=subprocess.run(['/usr/bin/python3','-I','-B',str(path),'prepare'],capture_output=True)
assert failure.returncode!=0
assert (pathlib.Path('/etc/hostname').read_bytes() if pathlib.Path('/etc/hostname').exists() else None)==before
assert not pathlib.Path('/etc/systemd/network/00-a733-management.network').exists()
# Exercise real target file preparation only inside this disposable chroot.
# Mount provenance itself has independent guard fixtures; it is not mocked in CLI.
guard=M.runtime_guard
M.runtime_guard=lambda:cfg
try:
    M.prepare()
finally:
    M.runtime_guard=guard
assert pathlib.Path('/etc/hostname').read_text()==cfg['hostname']+'\n'
assert 'MACAddress='+cfg['management_mac'] in pathlib.Path('/etc/systemd/network/00-a733-management.network').read_text()
for name in ('ssh.service','sshd.service','ssh.socket','sshd.socket','apt-daily.service','apt-daily.timer','apt-daily-upgrade.service','apt-daily-upgrade.timer','dpkg-db-backup.service','dpkg-db-backup.timer','systemd-journal-catalog-update.service'):
    assert os.readlink('/etc/systemd/system/'+name)=='/dev/null'
assert os.readlink('/etc/systemd/system/multi-user.target.wants/a733-management-ssh.service')=='/lib/systemd/system/a733-management-ssh.service'
assert '\nStandardOutput=journal+console\n' in pathlib.Path('/usr/lib/systemd/system/a733-management-ssh.service').read_text()
assert hashlib.sha256(key.read_bytes()).digest()==first
unit=subprocess.run(['/usr/bin/systemd-analyze','verify','/usr/lib/systemd/system/a733-management-ssh.service'],capture_output=True,text=True)
assert unit.returncode==0,unit.stderr
print(json.dumps({'key_generation_noreplace':'passed','private_key_single_link':True,'private_key_reused':True,'missing_public_key_recovered':True,'test_host_public_key_fingerprint':fingerprint,'test_administration_public_key_fingerprints':admin_fingerprints,'sshd_syntax':'passed','sshd_effective_policy':required,'locked_root_pam_account_check':account_status,'password_locked_root_public_key_login':'not performed','prepare_guard_without_NVMe':'rejected before configuration writes','private_file_preparation':'passed; guard mocked only inside disposable chroot','unit_verify_returncode':unit.returncode,'unit_verify_diagnostics':unit.stderr.strip()},indent=2))
'''
        (root / 'check.py').write_text(program)
        result = subprocess.run(['chroot', str(root), '/usr/bin/python3', '-I', '-B', '/check.py'],
                                env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'},
                                capture_output=True, text=True, timeout=180)
        if result.returncode:
            raise ValueError(f'private ARM64 check failed: {result.stderr}')
        report = json.loads(result.stdout)
        report.update(classification='ARM64 binfmt/chroot software checks; no board boot, SSH connection or KVM acceptance',
                      runtime=runtime, verifier_source=PAYLOAD.file_record(Path(__file__)), private_fixture_keys='deleted with temporary chroot', userspace=expected)
        return report


if __name__ == '__main__':
    if len(sys.argv) != 2:
        sys.exit(f'Usage: sudo python3 {sys.argv[0]} AUTHENTICATED_USERSPACE_TAR')
    print(json.dumps(verify(sys.argv[1]), indent=2))
