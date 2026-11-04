#!/usr/bin/env python3
"""Offline selector recovery on one fresh sparse GPT loop disk, never hardware.

Only an authenticated candidate directory is accepted. NVMe/RAM policy entry
and interactive authorization are explicit fixtures; actual GPT/partitions,
readonly claims, hashes, state mount, journal and publication execute natively.
"""
import argparse
import copy
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
import time
from types import SimpleNamespace
from unittest import mock
import uuid

REPO=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('recovery_loop_controller',REPO/'scripts/recover-host-root.py')
REC=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REC)
POLICY=REC.adjacent('recovery_loop_policy','host-recovery-target.py')
SIZE=250_000_000_000
ROOT_BYTES=4*1024**3
require=REC.require


def digest_file(path):
    with Path(path).open('rb') as source:
        digest,size=hashlib.sha256(),0
        for data in iter(lambda:source.read(4*1024**2),b''):
            size+=len(data)
            require(size<=ROOT_BYTES,'file exceeds harness bound')
            digest.update(data)
    return {'bytes':size,'sha256':digest.hexdigest()}


def region(path,offset,length):
    digest=hashlib.sha256()
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        done=0
        while done<length:
            data=os.pread(fd,min(4*1024**2,length-done),offset+done)
            require(data,'short private region read')
            digest.update(data)
            done+=len(data)
        return {'bytes':done,'sha256':digest.hexdigest()}
    finally:
        os.close(fd)


def owned(loop,backing):
    name=Path(loop).name
    require(name.startswith('loop') and name[4:].isdigit() and loop=='/dev/'+name,
            'unexpected loop name')
    info=backing.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid==0 and info.st_nlink==1 and info.st_size==SIZE,
            'private backing metadata differs')
    base=Path('/sys/class/block')/name
    value=(base/'loop/backing_file').read_text().strip()
    require(Path('/'+value.lstrip('/')).resolve(strict=True)==backing,'private backing differs')
    return base


def partition(loop,backing,part):
    parent=owned(loop,backing)
    path=Path(loop+'p'+str(part['number']))
    info=path.lstat()
    node=(Path('/sys/dev/block')/f'{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}').resolve(strict=True)
    require(stat.S_ISBLK(info.st_mode) and info.st_uid==0 and node.parent==parent.resolve()
            and int((node/'partition').read_text())==part['number']
            and int((node/'start').read_text())*512==part['start_bytes']
            and int((node/'size').read_text())*512==part['size_bytes'],
            'private partition geometry differs')
    return path


def verify(candidate,pin):
    require(os.geteuid()==0,'root required for private loop test')
    with REC.PREP.safe_parent(Path('/var/lib')) as parent:
        REC.UPDATE.storage_guard(parent)
    require(shutil.disk_usage('/var/lib').free>=12*1024**3,'12 GiB fixed storage free required')
    REC.private_namespace()
    workspace=Path(tempfile.mkdtemp(prefix='a733-recovery-loop-',dir='/var/lib'))
    backing=workspace/'disk.img'
    loop=None
    setup_mount=None
    private_run=False
    manifest=None
    result={}
    try:
        candidate=Path(candidate)
        manifest=REC.Manifest(candidate/'manifest.json',pin,'A')
        require(manifest.expected['bytes']==ROOT_BYTES,'fixed A root required')
        encoded=POLICY.LAYOUT.encode_gpt(SIZE,512,512)
        geometry=POLICY.LAYOUT.layout(SIZE,512,512)
        parts=geometry['partitions']
        with backing.open('x+b') as image:
            image.truncate(SIZE)
            for extent in encoded['extents']:
                image.seek(extent['offset_bytes'])
                image.write(extent['data'])
            source_path=candidate/'root.ext4'
            with REC.WRITER.open_regular(source_path) as source:
                identity=REC.WRITER.file_identity(source.fileno())
                digest,length=hashlib.sha256(),0
                while True:
                    data=source.read(1024**2)
                    if not data:
                        break
                    length+=len(data)
                    require(length<=ROOT_BYTES,'root source exceeds budget')
                    digest.update(data)
                    if data.strip(b'\0'):
                        image.seek(parts[1]['start_bytes']+length-len(data))
                        image.write(data)
                require({'bytes':length,'sha256':digest.hexdigest()}==manifest.expected
                        and REC.WRITER.file_identity(source.fileno())==identity,
                        'root source authentication failed')
            sentinels={}
            for role,index in (('boot',0),('guests',4)):
                data=(role+' private preservation sentinel\n').encode().ljust(4096,b'!')
                image.seek(parts[index]['start_bytes'])
                image.write(data)
                sentinels[role]={'offset':parts[index]['start_bytes'],'data':data}
            image.flush()
            os.fsync(image.fileno())
        backing.chmod(0o600)
        loop=subprocess.check_output(['losetup','--find','--show','--partscan',str(backing)],text=True).strip()
        subprocess.run(['udevadm','settle','--timeout=10'],check=True,capture_output=True,timeout=15)
        paths={part['number']:partition(loop,backing,part) for part in parts}
        state_uuid=str(uuid.uuid4())
        # Formatting is confined to the freshly created, ownership-checked p4.
        subprocess.run(['mke2fs','-q','-F','-t','ext4','-m','0','-U',state_uuid,
                        '-E','lazy_itable_init=0,lazy_journal_init=0,nodiscard',str(paths[4])],
                       check=True,capture_output=True)
        setup_mount=workspace/'state-setup'
        setup_mount.mkdir(mode=0o700)
        subprocess.run(['mount','-t','ext4','-o','rw,nodev,nosuid',str(paths[4]),str(setup_mount)],check=True)
        (setup_mount/'.a733-state').write_bytes(f'a733-state-v1\n{state_uuid}\n'.encode())
        (setup_mount/'.a733-state').chmod(0o444)
        (setup_mount/'boot').mkdir(mode=0o700)
        broken=b'broken selector\x00\xff\n'
        (setup_mount/'boot/selected.conf').write_bytes(broken)
        (setup_mount/'boot/selected.conf').chmod(0o600)
        state_sentinel=b'preserve existing state contents\n'
        (setup_mount/'state-sentinel').write_bytes(state_sentinel)
        subprocess.run(['umount',str(setup_mount)],check=True)
        setup_mount=None
        subprocess.run(['udevadm','settle','--timeout=10'],check=True,capture_output=True,timeout=15)
        # Isolate fixed recovery paths and the global RAM lock from the builder.
        subprocess.run(['mount','-t','tmpfs','-o','mode=0755,nodev,nosuid,size=64m','tmpfs','/run'],check=True)
        private_run=True
        node=owned(loop,backing)
        dev=os.stat(loop).st_rdev
        disk={'requested_path':loop,'canonical_path':loop,
              'major_minor':f'{os.major(dev)}:{os.minor(dev)}','model':'PRIVATE GPT LOOP',
              'serial':'NO-PHYSICAL-TARGET','size_bytes':SIZE,
              'logical_sector_bytes':int((node/'queue/logical_block_size').read_text()),
              'physical_sector_bytes':int((node/'queue/physical_block_size').read_text())}
        records={}
        nodes=[{'kname':Path(loop).name,'maj:min':disk['major_minor']}]
        for part in parts:
            number=part['number']
            device=os.stat(paths[number]).st_rdev
            records[number]=dict(disk,requested_path=str(paths[number]),canonical_path=str(paths[number]),
                major_minor=f'{os.major(device)}:{os.minor(device)}',size_bytes=part['size_bytes'],
                partition_number=number,partition_uuid=encoded['partition_guids'][number-1])
            nodes.append({'kname':paths[number].name,'maj:min':records[number]['major_minor']})
        state=dict(records[4],uuid=state_uuid,mountpoint=str(REC.MOUNTPOINT))
        context={'disk':disk,'target':records[2],'state':state,'gpt':None}
        real_open=os.open
        def snapshot(arguments,mounted_state=False):
            owned(loop,backing)
            POLICY.LIVE.sysfs_geometry(nodes,geometry)
            table=POLICY.MANAGEMENT.mount_table(Path('/proc/self/mountinfo').read_text())
            POLICY.mounted_state_policy(table,nodes[4],mounted_state)
            fd=real_open(loop,os.O_RDONLY|os.O_NOFOLLOW)
            try:
                gpt=POLICY.LIVE.gpt_context(fd,disk)
            finally:
                os.close(fd)
            require(gpt['disk_guid']==encoded['disk_guid'],'GPT disk identity changed')
            fd=real_open(str(paths[4]),os.O_RDONLY|os.O_NOFOLLOW)
            try:
                POLICY.state_superblock(fd,state_uuid)
            finally:
                os.close(fd)
            for row in nodes:
                actual=[m for rows in table.values() for m in rows if m['id']==row['maj:min']]
                require(len(actual)==(1 if mounted_state and row is nodes[4] else 0),
                        'unexpected private target mount')
            return dict(copy.deepcopy(context),gpt=gpt)
        args=SimpleNamespace(target=loop,expected_model=disk['model'],expected_serial=disk['serial'],
                             expected_bytes=SIZE,state_uuid=state_uuid,slot='A')
        allowed={os.stat(loop).st_rdev}|{os.stat(p).st_rdev for p in paths.values()}
        raw_opens=[]
        real_ioctl=REC.fcntl.ioctl
        def open_audit(path,flags,*positional,**keywords):
            fd=real_open(path,flags,*positional,**keywords)
            try:
                info=os.fstat(fd)
                if stat.S_ISBLK(info.st_mode):
                    require(info.st_rdev in allowed and flags&os.O_ACCMODE==os.O_RDONLY,
                            'controller opened a foreign or writable raw device')
                    raw_opens.append({'major_minor':f'{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}',
                                      'exclusive':bool(flags&os.O_EXCL)})
                return fd
            except BaseException:
                os.close(fd)
                raise
        def no_write(*unused,**keywords):
            raise AssertionError('controller attempted raw block writes')
        def ioctl(fd,command,*arguments):
            require(command not in (REC.WRITER.BLKFLSBUF,REC.WRITER.BLKRRPART),
                    'controller attempted raw block maintenance')
            return real_ioctl(fd,command,*arguments)
        before_state=region(backing,parts[3]['start_bytes'],parts[3]['size_bytes'])
        with mock.patch.object(REC,'snapshot',new=snapshot), \
             mock.patch.object(REC,'authorize',new=lambda unused:None), \
             mock.patch.object(REC.os,'open',new=open_audit), \
             mock.patch.object(REC.os,'pwrite',new=no_write), \
             mock.patch.object(REC.fcntl,'ioctl',new=ioctl):
            plan=REC.make_plan(args,manifest)
            require(plan['state_image_before']==before_state,'read-only prepare changed state')
            require(region(backing,parts[3]['start_bytes'],parts[3]['size_bytes'])==before_state,
                    'state changed during read-only preparation')
            report=REC.apply(args,manifest,plan)
        require(report['success'] and report['selection_published'] and report['publication_durable']
                and report['state_may_be_modified'] and report['state_unmounted'],
                'offline recovery failed: '+json.dumps(report))
        require(not report['before_selector_valid'] and bytes.fromhex(report['before_selector_hex'])==broken,
                'broken selection was not preserved')
        require(not report['root_switched'] and not report['block_writes_performed'],
                'controller claimed a root switch/raw write')
        require(region(backing,parts[1]['start_bytes'],ROOT_BYTES)==manifest.expected,
                'retained root bytes changed')
        fd=real_open(loop,os.O_RDONLY|os.O_NOFOLLOW)
        try:
            require(POLICY.LIVE.gpt_context(fd,disk)==plan['context']['gpt'],'GPT changed')
            for value in sentinels.values():
                require(os.pread(fd,4096,value['offset'])==value['data'],'boot/guest sentinel changed')
        finally:
            os.close(fd)
        def offline(path,limit):
            return REC.PREP.INIT.bounded_output(['debugfs','-R','cat '+path,str(paths[4])],limit)
        selected=offline('/boot/selected.conf',4096)
        require(REC.SELECT.parse(selected)==plan['proposed_selector'],'offline selector readback differs')
        journal=json.loads(offline('/boot/'+report['journal_name'],REC.PREP.LIMIT))
        require(bytes.fromhex(journal['before_selector_hex'])==broken and not journal['before_selector_valid']
                and bytes.fromhex(journal['after_selector_hex'])==selected,'offline journal differs')
        require(offline('/state-sentinel',4096)==state_sentinel,'unrelated state changed')
        manifest.check()
        result={'classification':'fresh sparse-GPT loop recovery only; NVMe/RAM entry policy and TTY are fixtures; no boot or physical failed-host recovery proof',
            'report':report,'logical_disk_bytes':SIZE,'root_bytes':ROOT_BYTES,
            'state_bytes':parts[3]['size_bytes'],'full_state_prepare_unchanged':True,
            'retained_root_unchanged':True,'gpt_and_boot_guest_sentinels_unchanged':True,
            'unrelated_state_sentinel_unchanged':True,'offline_selected_slot':'A',
            'broken_selector_preserved_in_durable_journal':True,'raw_block_opens':raw_opens,
            'sources':{name:digest_file(REPO/name) for name in ('scripts/recover-host-root.py',
                'scripts/host-recovery-target.py','scripts/select-host-root.py','tests/verify_host_recovery_loop.py')}}
    finally:
        errors=[]
        if setup_mount is not None:
            try:
                subprocess.run(['umount',str(setup_mount)],check=True)
                setup_mount=None
            except Exception as error:
                errors.append(str(error))
        if REC.MOUNTPOINT.exists() and loop is not None:
            rows=[r for r in REC.mount_entries() if r[4]==str(REC.MOUNTPOINT)]
            if rows:
                try:
                    require(len(rows)==1 and rows[0][2]==f'{os.major(os.stat(loop+"p4").st_rdev)}:{os.minor(os.stat(loop+"p4").st_rdev)}',
                            'foreign mount at recovery path')
                    subprocess.run(['umount',str(REC.MOUNTPOINT)],check=True)
                except Exception as error:
                    errors.append(str(error))
        if private_run:
            try:
                subprocess.run(['umount','/run'],check=True)
                private_run=False
            except Exception as error:
                errors.append(str(error))
        if loop is not None:
            try:
                owned(loop,backing)
                subprocess.run(['losetup','--detach',loop],check=True)
                attribute=Path('/sys/class/block')/Path(loop).name/'loop/backing_file'
                for unused in range(50):
                    if not attribute.exists():
                        break
                    time.sleep(0.1)
                else:
                    raise ValueError('owned loop detach did not complete')
                loop=None
            except Exception as error:
                errors.append(str(error))
        if manifest is not None:
            manifest.close()
        if loop is None and setup_mount is None and not private_run:
            shutil.rmtree(workspace)
        else:
            print(f'Retained recovery workspace {workspace}; loop={loop}; private_run={private_run}',file=sys.stderr)
        if errors:
            raise RuntimeError('private cleanup failed: '+'; '.join(errors))
    result['cleanup']='owned state/private-run mounts unmounted, loop detached and workspace deleted'
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate_directory',type=Path)
    parser.add_argument('--manifest-sha256',required=True)
    arguments=parser.parse_args()
    print(json.dumps(verify(arguments.candidate_directory,arguments.manifest_sha256),indent=2))
