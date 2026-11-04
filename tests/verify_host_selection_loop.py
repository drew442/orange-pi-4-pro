#!/usr/bin/env python3
"""Selection publication on private root loops and journaled ext4 state only.

No target argument is accepted. Live NVMe policy and TTY authorization are
explicit fixtures. This does not boot or switch roots or establish recovery.
"""
import argparse
import contextlib
import copy
import ctypes
import errno
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest import mock
import uuid

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('selection_loop_controller', REPO / 'scripts/select-host-root.py')
CONTROL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONTROL)
SIZE = 4 * 1024**3
STATE_SIZE = 64 * 1024**2


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest_file(path):
    value, size = hashlib.sha256(), 0
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode), 'regular file required')
        for data in iter(lambda: source.read(4 * 1024**2), b''):
            size += len(data)
            require(size <= SIZE, 'file exceeds fixture bound')
            value.update(data)
        after = os.fstat(fd)
        require((before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns) ==
                (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns),
                'file changed while hashed')
    return {'bytes':size,'sha256':value.hexdigest()}


def owned_loop(loop, backing, size):
    name = Path(loop).name
    require(name.startswith('loop') and name[4:].isdigit() and loop == '/dev/' + name,
            'unexpected private loop name')
    info = backing.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
            and info.st_size == size, 'private backing metadata differs')
    node = Path('/sys/class/block') / name
    reported = (node / 'loop/backing_file').read_text().strip()
    require(Path('/' + reported.lstrip('/')).resolve(strict=True) == backing,
            'private backing path differs')
    return node


def settle():
    subprocess.run(['udevadm','settle','--timeout=10'], check=True,
                   capture_output=True, timeout=15)


def verify(candidate_base, pin):
    require(os.geteuid() == 0, 'root required for private namespace/loop test')
    with CONTROL.PREP.safe_parent(Path('/var/lib')) as parent:
        CONTROL.UPDATE.storage_guard(parent)
    require(shutil.disk_usage('/var/lib').free >= 12 * 1024**3,
            '12 GiB free on fixed builder storage required')
    library = ctypes.CDLL(None, use_errno=True)
    require(library.unshare(0x20000) == 0, 'private mount namespace unavailable')
    subprocess.run(['mount','--make-rprivate','/'], check=True)
    loops, mounts = {}, []
    root = Path(tempfile.mkdtemp(prefix='a733-selection-loop-',dir='/var/lib'))
    manifests = {}
    result = {}
    try:
        for slot in ('A','B'):
            source_dir = Path(candidate_base) / f'candidate-{slot}'
            manifest = CONTROL.Manifest(source_dir / 'manifest.json',pin,slot)
            manifests[slot] = manifest
            require(manifest.expected['bytes'] == SIZE, 'fixed root fixture required')
            backing = root / f'root-{slot}.ext4'
            with backing.open('x+b') as target:
                CONTROL.PREP.capture_root(source_dir / 'root.ext4',manifest.expected,target)
            backing.chmod(0o600)
        state = root / 'state.ext4'
        with state.open('xb') as stream:
            stream.truncate(STATE_SIZE)
        state.chmod(0o600)
        state_uuid = str(uuid.uuid4())
        subprocess.run(['mke2fs','-q','-F','-t','ext4','-m','0','-U',state_uuid,str(state)],
                       check=True,capture_output=True)
        with state.open('rb') as stream:
            stream.seek(1024+92)
            require(struct.unpack('<I',stream.read(4))[0] & 4,
                    'state fixture must have an ext4 journal')
        for slot,size,path in (('A',SIZE,root/'root-A.ext4'),('B',SIZE,root/'root-B.ext4'),
                               ('state',STATE_SIZE,state)):
            loop = subprocess.check_output(['losetup','--find','--show',str(path)],text=True).strip()
            loops[slot] = (loop,path,size)
            owned_loop(loop,path,size)
        settle()
        active_mount, state_mount = root/'active-root',root/'state-mount'
        active_mount.mkdir(mode=0o700)
        state_mount.mkdir(mode=0o700)
        subprocess.run(['mount','-t','ext4','-o','ro,noload,nodev,nosuid',loops['A'][0],str(active_mount)],check=True)
        mounts.append(active_mount)
        subprocess.run(['mount','-t','ext4','-o','rw,nodev,nosuid',loops['state'][0],str(state_mount)],check=True)
        mounts.append(state_mount)
        boot = state_mount/'boot'
        boot.mkdir(mode=0o700)
        selectors = {}
        for slot in ('A','B'):
            with (root/f'root-{slot}.ext4').open('rb') as stream:
                selectors[slot],_ = CONTROL.PREP.compatibility(stream,slot,manifests[slot].value)
        CONTROL.SELECT.atomic_store(boot,selectors['A'])
        selected = boot/'selected.conf'
        sentinels = [state_mount/'guest-sentinel',state_mount/'boot-stack-sentinel']
        for path in sentinels:
            path.write_bytes(b'private fixture; must stay unchanged\n')
        before_sentinels = {path.name:digest_file(path) for path in sentinels}
        try:
            held = os.open(loops['A'][0],os.O_RDONLY | os.O_EXCL | os.O_NOFOLLOW)
        except OSError as error:
            require(error.errno == errno.EBUSY,'unexpected mounted-root exclusive refusal')
        else:
            os.close(held)
            raise ValueError('mounted active root accepted an exclusive block claim')
        running = 'A'
        slot_records = {}
        for slot in ('A','B'):
            loop,path,size = loops[slot]
            node = owned_loop(loop,path,size)
            dev = os.stat(loop).st_rdev
            slot_records[slot] = {'requested_path':loop,'canonical_path':loop,
                'major_minor':f'{os.major(dev)}:{os.minor(dev)}',
                'model':'PRIVATE LOOP FIXTURE','serial':'NO-PHYSICAL-TARGET','size_bytes':SIZE,
                'logical_sector_bytes':int((node/'queue/logical_block_size').read_text()),
                'physical_sector_bytes':int((node/'queue/physical_block_size').read_text()),
                'partition_number':2 if slot=='A' else 3,
                'partition_uuid':f'a7330000-0000-4000-8000-00000000000{2 if slot=="A" else 3}'}
        args = SimpleNamespace(intent='activate',slot='B',target='private-fixture-only',
            expected_model='PRIVATE LOOP FIXTURE',expected_serial='NO-PHYSICAL-TARGET',expected_bytes=SIZE)
        def snapshot(arguments,published=False):
            for loop,path,size in loops.values():
                owned_loop(loop,path,size)
            values = CONTROL.SELECT.parse(selected.read_bytes())
            desired = arguments.slot if arguments.intent == 'activate' and published else running
            if arguments.intent == 'cancel-pending' and not published:
                require(values['SLOT'] != running,'fixture cancellation requires pending selection')
            else:
                require(values['SLOT'] == desired,'fixture publication mode differs')
            if arguments.intent == 'activate':
                require(arguments.slot != running,'fixture activation targets inactive root')
            else:
                require(arguments.slot == running,'fixture cancellation targets running root')
            state_dev = boot.stat().st_dev
            return {'disk':dict(slot_records['B']), 'target':copy.deepcopy(slot_records[arguments.slot]),
                'active':{'slot':running,'root_source':loops['A'][0],
                          'root_major_minor':slot_records['A']['major_minor'],
                          'marker_sha256':CONTROL.SELECT.parse(selectors['A'])['ROOT_MARKER_SHA256']},
                'state':{'source':loops['state'][0],
                         'major_minor':f'{os.major(state_dev)}:{os.minor(state_dev)}',
                         'uuid':state_uuid,'boot_directory':str(boot),'selector':values,
                         'selector_sha256':hashlib.sha256(selected.read_bytes()).hexdigest()},
                'gpt':{'disk_guid':'fixture-only-no-GPT','partition_guids':[],'sha256':'b'*64}}
        block_devs = {os.stat(loops[s][0]).st_rdev:s for s in ('A','B')}
        reads = []
        real_open,real_ioctl = os.open,CONTROL.fcntl.ioctl
        def open_audit(path,flags,*positional,**keywords):
            fd = real_open(path,flags,*positional,**keywords)
            try:
                info = os.fstat(fd)
                if stat.S_ISBLK(info.st_mode):
                    require(info.st_rdev in block_devs,'controller opened a foreign raw block node')
                    require(flags & os.O_ACCMODE == os.O_RDONLY,'controller opened a root block for writes')
                    reads.append({'slot':block_devs[info.st_rdev], 'exclusive':bool(flags & os.O_EXCL)})
                return fd
            except BaseException:
                os.close(fd)
                raise
        def no_write(*unused,**keywords):
            raise AssertionError('controller attempted raw pwrite')
        def ioctl(held,command,*arguments):
            require(command not in (CONTROL.WRITER.BLKFLSBUF,CONTROL.WRITER.BLKRRPART),
                    'selection must not flush or refresh a block device')
            return real_ioctl(held,command,*arguments)
        with mock.patch.object(CONTROL,'snapshot',new=snapshot), \
             mock.patch.object(CONTROL,'authorize',new=lambda unused:None), \
             mock.patch.object(CONTROL.os,'open',new=open_audit), \
             mock.patch.object(CONTROL.os,'pwrite',new=no_write), \
             mock.patch.object(CONTROL.fcntl,'ioctl',new=ioctl):
            plan = CONTROL.make_plan(args,manifests['B'])
            real_replace = os.replace
            def before_rename(source,destination,**keywords):
                if destination == 'selected.conf':
                    raise OSError(errno.EIO,'intentional prepublication rename failure')
                return real_replace(source,destination,**keywords)
            with mock.patch.object(CONTROL.SELECT.os,'replace',new=before_rename):
                pre = CONTROL.apply(args,manifests['B'],plan)
            require(not pre['success'] and pre['journal_visible'] and pre['journal_durable']
                    and not pre['selection_published'] and selected.read_bytes()==selectors['A'],
                    'prepublication failure did not retain A: '+json.dumps(pre))
            journal_name = pre['journal_name']
            journal_before = digest_file(boot/journal_name)
            real_sync = os.fsync
            boot_identity = (boot.stat().st_dev,boot.stat().st_ino)
            def after_rename(fd):
                info = os.fstat(fd)
                if ((info.st_dev,info.st_ino)==boot_identity and selected.read_bytes()==selectors['B']):
                    raise KeyboardInterrupt('intentional postrename interruption before directory sync')
                return real_sync(fd)
            settle()
            with mock.patch.object(CONTROL.SELECT.os,'fsync',new=after_rename):
                post = CONTROL.apply(args,manifests['B'],plan)
            require(not post['success'] and post['selection_published']
                    and not post['publication_durable'] and selected.read_bytes()==selectors['B'],
                    'postrename outcome was not reported conservatively: '+json.dumps(post))
            require(digest_file(boot/journal_name)==journal_before,'existing journal changed on retry')
            args.intent,args.slot = 'cancel-pending','A'
            settle()
            cancellation_plan = CONTROL.make_plan(args,manifests['A'])
            cancelled = CONTROL.apply(args,manifests['A'],cancellation_plan)
            require(cancelled['success'] and cancelled['selection_published']
                    and cancelled['publication_durable'] and selected.read_bytes()==selectors['A'],
                    'cancellation of pending selection failed: '+json.dumps(cancelled))
        require(reads and all(row['exclusive']==(row['slot']=='B') for row in reads),
                'root claim mode did not distinguish idle B from mounted A')
        for receipt in (pre,post,cancelled):
            require(not receipt['root_switched'] and not receipt['block_writes_performed'],
                    'controller claimed a root switch or raw writes')
        require({path.name:digest_file(path) for path in sentinels}==before_sentinels,
                'unrelated sentinel changed')
        journals = {}
        for path in boot.glob('selection-*.json'):
            value = json.loads(path.read_bytes())
            before,after = bytes.fromhex(value['before_selector_hex']),bytes.fromhex(value['after_selector_hex'])
            require((before,after) in ((selectors['A'],selectors['B']),(selectors['B'],selectors['A'])),
                    'journal selector pair differs')
            journals[path.name] = digest_file(path)
        require(len(journals)==2,'expected activation and cancellation journals')
        for mount in reversed(mounts.copy()):
            subprocess.run(['umount',str(mount)],check=True)
            mounts.remove(mount)
        for slot in ('A','B'):
            require(digest_file(root/f'root-{slot}.ext4')==manifests[slot].expected,
                    'private root bytes changed')
            manifests[slot].check()
        command=['debugfs','-R','cat /boot/selected.conf',str(state)]
        offline = CONTROL.PREP.INIT.bounded_output(command,4096)
        require(offline==selectors['A'],'unmounted ext4 state selector readback differs')
        result={'classification':'private-loop selection and real ext4 journal checks; live NVMe policy and TTY mocked; no boot, running-root switch, failed-boot recovery or hardware proof',
            'prepublication_failure':pre,'postrename_interruption':post,'cancel_pending':cancelled,
            'root_block_opens':reads,'raw_root_images_unchanged':True,
            'mounted_readonly_A_refused_exclusive_claim':True,'journaled_state_bytes':STATE_SIZE,
            'journal_records':journals,'offline_state_selector_readback':'A',
            'unrelated_sentinels_unchanged':True,
            'sources':{name:digest_file(REPO/name) for name in ('scripts/select-host-root.py',
                'scripts/host-update-target.py','build/root-selection.py','tests/verify_host_selection_loop.py')}}
    finally:
        cleanup_errors=[]
        for mount in reversed(mounts.copy()):
            try:
                subprocess.run(['umount',str(mount)],check=True)
                mounts.remove(mount)
            except Exception as error:
                cleanup_errors.append(str(error))
        for slot,(loop,path,size) in list(loops.items()):
            try:
                owned_loop(loop,path,size)
                subprocess.run(['losetup','--detach',loop],check=True)
                attribute=Path('/sys/class/block')/Path(loop).name/'loop/backing_file'
                for unused in range(50):
                    if not attribute.exists():
                        break
                    time.sleep(0.1)
                else:
                    raise ValueError('owned loop detach did not complete')
                del loops[slot]
            except Exception as error:
                cleanup_errors.append(str(error))
        for manifest in manifests.values():
            manifest.close()
        if mounts or loops:
            print(f'Retained selection workspace: {root}; mounts={mounts}; loops={loops}',file=sys.stderr)
        else:
            shutil.rmtree(root)
        if cleanup_errors:
            raise RuntimeError('private cleanup failed: '+'; '.join(cleanup_errors))
    result['cleanup']='all owned mounts unmounted, loops detached and private workspace deleted'
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate_base',type=Path)
    parser.add_argument('--manifest-sha256',required=True)
    arguments=parser.parse_args()
    print(json.dumps(verify(arguments.candidate_base,arguments.manifest_sha256),indent=2))
