#!/usr/bin/env python3
"""Inspect packaged bytes and run ARM64 software probes in a disposable chroot.

No mounts, listeners, external requests, physical block targets or board tests.
"""
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import stat
import struct
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]


def record(value):
    return {'bytes': len(value), 'sha256': hashlib.sha256(value).hexdigest()}


def regular(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 <= info.st_size <= limit:
            raise ValueError('bounded regular file required')
        data = source.read(limit + 1)
    if len(data) > limit:
        raise ValueError('file exceeds bound')
    return data


def decode(data):
    entries, position = {}, 0
    while True:
        if len(data) - position < 110 or data[position:position + 6] != b'070701':
            raise ValueError('invalid newc header')
        fields = [int(data[position + 6 + i * 8:position + 14 + i * 8], 16) for i in range(13)]
        _, mode, uid, gid, links, timestamp, length, _, _, _, _, name_length, check = fields
        if uid or gid or timestamp or check or links != 1 or not 1 <= name_length <= 4096:
            raise ValueError('unexpected newc metadata')
        position += 110
        raw_name = data[position:position + name_length]
        if len(raw_name) != name_length or raw_name[-1:] != b'\0' or b'\0' in raw_name[:-1]:
            raise ValueError('invalid newc name')
        name = raw_name[:-1].decode()
        position = (position + name_length + 3) & ~3
        content = data[position:position + length]
        if len(content) != length:
            raise ValueError('truncated archive member')
        position = (position + length + 3) & ~3
        if position > len(data):
            raise ValueError('truncated newc padding')
        if name == 'TRAILER!!!':
            if length or any(data[position:]):
                raise ValueError('invalid newc trailer')
            break
        path = PurePosixPath(name)
        if name in entries or path.is_absolute() or '..' in path.parts or str(path) != name or name == '.':
            raise ValueError('duplicate/unsafe archive name')
        if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode) or stat.S_ISLNK(mode)):
            raise ValueError('special archive object refused')
        entries[name] = (mode, content)
    for name in entries:
        for parent in PurePosixPath(name).parents:
            if str(parent) != '.' and (str(parent) not in entries or not stat.S_ISDIR(entries[str(parent)][0])):
                raise ValueError('archive parent must be a real directory')
    return entries


def verify(directory):
    if os.geteuid() != 0:
        raise ValueError('root required for private ARM chroot')
    directory = Path(directory)
    manifest = json.loads(regular(directory / 'manifest.json', 4 * 1024**2))
    packed = regular(directory / 'initramfs.cpio.gz', 64 * 1024**2)
    if record(packed) != manifest['files']['initramfs.cpio.gz'] or len(packed) + 64 >= 64 * 1024**2:
        raise ValueError('compressed archive hash/size or load budget differs')
    with gzip.GzipFile(fileobj=io.BytesIO(packed)) as source:
        data = source.read(256 * 1024**2 + 1)
    if len(data) > 256 * 1024**2 or len(data) != manifest['expanded_bytes']:
        raise ValueError('expanded archive budget differs')
    entries = decode(data)
    if {name: dict(mode=mode, **record(content)) for name, (mode, content) in entries.items()} != manifest['entries']:
        raise ValueError('archive contents differ from trusted manifest')
    for name, source in (('init', 'configs/installer-init'), ('etc/udhcpc.script', 'configs/installer-udhcpc.script'),
                         ('usr/lib/a733-installer/install-nvme.py', 'scripts/install-nvme.py'),
                         ('usr/lib/a733-installer/acquire-nvme.py', 'scripts/acquire-nvme.py'),
                         ('usr/lib/a733-installer/plan-nvme.py', 'scripts/plan-nvme.py'),
                         ('usr/lib/a733-installer/nvme_layout.py', 'scripts/nvme_layout.py')):
        if entries[name][1] != (REPO / source).read_bytes():
            raise ValueError('packaged preparation source differs')
    if entries['etc/shadow'][1] != b'root:!:1:0:99999:7:::\n':
        raise ValueError('unexpected password state')
    with tempfile.TemporaryDirectory(prefix='a733-installer-arm-', dir='/var/tmp') as temporary:
        root = Path(temporary)
        for name, (mode, content) in sorted(entries.items(), key=lambda item: (len(PurePosixPath(item[0]).parts), item[0])):
            target = root / name
            if stat.S_ISDIR(mode):
                target.mkdir(exist_ok=True)
                target.chmod(stat.S_IMODE(mode))
            elif stat.S_ISREG(mode):
                with target.open('xb') as output:
                    output.write(content)
                target.chmod(stat.S_IMODE(mode))
            else:
                # No member has a symlink parent; aliases are leaves. Absolute
                # links remain inside the chroot when executed, not dereferenced here.
                target.symlink_to(content.decode())
        for name, minor in (('null', 3), ('zero', 5), ('urandom', 9)):
            os.mknod(root / 'dev' / name, stat.S_IFCHR | 0o666, os.makedev(1, minor))
        program = r'''import argparse,base64,bz2,ctypes,fcntl,gzip,hashlib,ipaddress,json,lzma,mmap,os,pathlib,platform,re,resource,shutil,signal,socket,sqlite3,ssl,struct,subprocess,sys,tarfile,tempfile,uuid,zlib
assert sys.flags.isolated and sys.dont_write_bytecode
assert platform.machine()=='aarch64'
ctypes.CDLL('libc.so.6')
context=ssl.create_default_context()
paths=ssl.get_default_verify_paths()
assert paths.cafile and pathlib.Path(paths.cafile).is_file()
assert len(context.get_ca_certs())>0
assert any(row[4][0]=='127.0.0.1' for row in socket.getaddrinfo('localhost',22,socket.AF_INET))
versions={}
for name,args in {'python':['/usr/bin/python3','--version'],'lsblk':['/usr/bin/lsblk','--version'],'blkid':['/usr/sbin/blkid','-V'],'mke2fs':['/usr/sbin/mke2fs','-V'],'tune2fs':['/usr/sbin/tune2fs','-V'],'e2fsck':['/usr/sbin/e2fsck','-V'],'debugfs':['/usr/sbin/debugfs','-V'],'curl':['/usr/bin/curl','--version']}.items():
    result=subprocess.run(args,capture_output=True,text=True,timeout=20)
    # tune2fs prints its version with usage exit 1; accept only its version text.
    assert result.returncode==0 or name=='tune2fs' and result.returncode==1,(name,result.stderr)
    versions[name]=(result.stdout+' '+result.stderr).strip().splitlines()[0]
planner=subprocess.run(['/usr/bin/python3','-I','-B','/usr/lib/a733-installer/plan-nvme.py','--help'],capture_output=True,text=True,timeout=20)
assert planner.returncode==0 and '--target' in planner.stdout,planner.stderr
subprocess.run(['/bin/busybox','ash','-n','/init'],check=True,capture_output=True)
seed=pathlib.Path('/fixture-seed');seed.mkdir();(seed/'probe.txt').write_text('ARM installer regular-file test\n')
image=pathlib.Path('/fixture.ext4')
with image.open('xb') as target:target.truncate(32*1024**2)
subprocess.run(['/usr/sbin/mke2fs','-q','-F','-t','ext4','-b','4096','-U','d2733000-0000-4000-8000-000000000001','-d',str(seed),str(image)],check=True,capture_output=True,timeout=60)
subprocess.run(['/usr/sbin/e2fsck','-fn',str(image)],check=True,capture_output=True,timeout=60)
probe=subprocess.run(['/usr/sbin/blkid','-p','-o','export',str(image)],check=True,capture_output=True,text=True,timeout=20)
assert 'UUID=d2733000-0000-4000-8000-000000000001' in probe.stdout and 'TYPE=ext4' in probe.stdout
readback=subprocess.run(['/usr/sbin/debugfs','-R','cat /probe.txt',str(image)],check=True,capture_output=True,timeout=20)
assert readback.stdout==(seed/'probe.txt').read_bytes()
import importlib.util
spec=importlib.util.spec_from_file_location('installer_writer_probe','/usr/lib/a733-installer/install-nvme.py')
writer=importlib.util.module_from_spec(spec);spec.loader.exec_module(writer)
writer_help=subprocess.run(['/usr/bin/python3','-I','-B','/usr/lib/a733-installer/install-nvme.py','--help'],capture_output=True,text=True,timeout=20)
assert writer_help.returncode==0 and 'prepare' in writer_help.stdout and 'apply' in writer_help.stdout
acquire_help=subprocess.run(['/usr/bin/python3','-I','-B','/usr/lib/a733-installer/acquire-nvme.py','--help'],capture_output=True,text=True,timeout=20)
assert acquire_help.returncode==0 and '--manifest-sha256' in acquire_help.stdout and '--storage-kind' in acquire_help.stdout
fixture_manifest={'schema':1,'kernel_release':'6.6.98-a733-diag','files':{'nvme.img':{'bytes':250000000000,'sha256':'0'*64}},'logical_sector_bytes':512,'physical_sector_bytes':4096,'layout':writer.LAYOUT.layout(250000000000,512,4096),'deployment_intent':{'for_installation':False}}
manifest_path=pathlib.Path('/fixture-manifest.json');manifest_path.write_text(json.dumps(fixture_manifest))
refusal=subprocess.run(['/usr/bin/python3','-I','-B','/usr/lib/a733-installer/install-nvme.py','prepare','--image','/missing/nvme.img','--manifest',str(manifest_path),'--manifest-sha256',hashlib.sha256(manifest_path.read_bytes()).hexdigest(),'--target','/dev/no-target','--expected-model','NO-TARGET','--expected-serial','NO-TARGET','--expected-bytes','250000000000'],capture_output=True,text=True,timeout=20)
assert refusal.returncode==1 and 'fixture' in json.loads(refusal.stdout)['error']
assert json.loads(refusal.stdout)['target_modified'] is False
discard_refusal=subprocess.run(refusal.args+['--existing-data-policy','discard-authorized'],capture_output=True,text=True,timeout=20)
assert discard_refusal.returncode==1 and 'fixture' in json.loads(discard_refusal.stdout)['error']
assert json.loads(discard_refusal.stdout)['target_modified'] is False
assert json.loads(discard_refusal.stdout)['existing_data_policy']=='discard-authorized'
copy=pathlib.Path('/fixture-copy.img')
with copy.open('xb') as target:
    for _ in range(32):target.write(b'X'*1024**2)
expected={'bytes':image.stat().st_size,'sha256':hashlib.sha256(image.read_bytes()).hexdigest()}
fd=os.open(copy,os.O_RDWR)
try:
    with image.open('rb') as source:
        copied=writer.stream_copy_verify(source,fd,expected,flush_callback=lambda held:None,refresh_callback=lambda held:None)
finally:os.close(fd)
assert copied['success'] and copied['target_modified'] and copied['bytes_verified']==expected['bytes'],copied
assert hashlib.sha256(copy.read_bytes()).hexdigest()==expected['sha256']
binpath=pathlib.Path('/fixture-bin');binpath.mkdir()
stub=binpath/'ip';stub.write_text('#!/bin/sh\nprintf fixture-call >> /run/fixture-ip.log\nexit 0\n');stub.chmod(0o755)
env=dict(os.environ,PATH='/fixture-bin:/usr/bin:/bin',interface='fixture0',ip='192.0.2.10',subnet='255.255.255.0',router='192.0.2.1',dns='192.0.2.53 invalid options;bad')
hook=pathlib.Path('/etc/udhcpc.script').read_text()
hook,count=re.subn(r'(?m)^(\s*)ip ',r'\1/fixture-bin/ip ',hook)
assert count==3,count
pathlib.Path('/fixture-hook.sh').write_text(hook)
subprocess.run(['/bin/sh','/fixture-hook.sh','bound'],env=env,check=True,capture_output=True,timeout=20)
assert pathlib.Path('/run/fixture-ip.log').read_text()=='fixture-call'*3
assert pathlib.Path('/etc/resolv.conf').read_text()=='nameserver 192.0.2.53\n'
print(json.dumps({'architecture':platform.machine(),'isolated_python':True,'core_modules':'imported','ca_file':paths.cafile,'ca_certificates':len(context.get_ca_certs()),'localhost_NSS':'passed','versions':versions,'planner_isolated_help':'passed','writer_isolated_help':'passed','acquisition_isolated_help':'passed','writer_fixture_refusal':'passed before target access','writer_regular_file_engine':'passed; block flush and partition callbacks not exercised','init_ash_syntax':'passed','regular_file_mkfs_fsck_blkid_debugfs':'passed','dhcp_ram_resolver_hook':'passed; three IP commands replaced by logged fixture stub','DNS_TLS_connection':'not performed','physical_block_targets':'not opened'}))
'''
        (root / 'probe.py').write_text(program)
        result = subprocess.run(['chroot', str(root), '/usr/bin/python3', '-I', '-B', '/probe.py'],
                                env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'},
                                capture_output=True, text=True, timeout=240)
        if result.returncode:
            raise ValueError('private ARM probe failed: ' + result.stderr)
        report = json.loads(result.stdout)
    return {'classification': 'packaged ARM64 chroot software checks; no board boot, physical installation or KVM acceptance',
            'verifier_source': record(Path(__file__).read_bytes()),
            'archive': record(packed), 'expanded_bytes': len(data), 'entries_checked': len(entries),
            'all_archive_hashes_modes_paths': 'passed', 'ARM_checks': report,
            'private_fixture': 'deleted after check; no mounts, listeners or external requests',
            'limits': ['Runtime tests cover the exercised commands/modules, not every possible dynamic load.',
                       'IP command was stubbed for DHCP hook; board DHCP/DNS/TLS untested.',
                       'No SD/NVMe/SPI write or actual KVM guest performed.']}


if __name__ == '__main__':
    if len(sys.argv) != 2:
        sys.exit(f'Usage: sudo {sys.argv[0]} TRUSTED_INSTALLER_INITRAMFS_DIRECTORY')
    print(json.dumps(verify(sys.argv[1]), indent=2))
