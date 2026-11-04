#!/usr/bin/env python3
"""Opt-in authenticated ARM64 maintenance probes; never physical acceptance.

The private worker protocol executes this trusted verifier, not package input.
It is an internal orchestration protocol, not a security boundary. No block
nodes, devtmpfs, host /dev bind, network requests or target arguments are used.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import time

REPO = Path(__file__).resolve().parents[1]
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'}
LIMIT = 4 * 1024**2


def module(name, relative):
    spec = importlib.util.spec_from_file_location(name, REPO / relative)
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result


def record(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def regular(path, limit=LIMIT):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        before = os.fstat(source.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and
                0 <= before.st_size <= limit, 'bounded single-link regular file required')
        data = source.read(limit + 1)
        require(len(data) <= limit and identity(before) == identity(os.fstat(source.fileno())), 'file changed or exceeds bound')
    return data


def authenticate(path, pin):
    require(isinstance(pin, str) and re.fullmatch('[0-9a-f]{64}', pin), 'explicit lowercase SHA-256 required')
    data = regular(path)
    require(record(data)['sha256'] == pin, 'manifest SHA-256 mismatch before parsing')
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate JSON key')
            result[key] = value
        return result
    value = json.loads(data, object_pairs_hook=pairs)
    require(isinstance(value, dict), 'manifest object required')
    return data, value


def validate_tree(root, expected):
    """Compare with trusted repository bytes, never self-declared package hashes."""
    require(root.is_dir() and not root.is_symlink() and stat.S_IMODE(root.stat().st_mode) == 0o555, 'real mode-0555 package tree required')
    allowed_dirs = set()
    for name in expected:
        path = PurePosixPath(name)
        require(str(path) == name and not path.is_absolute() and '..' not in path.parts and name != '.', 'unsafe package path')
        allowed_dirs.update(str(p) for p in path.parents if str(p) != '.')
    found = set()
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            info = path.lstat()
            if stat.S_ISDIR(info.st_mode):
                require(relative in allowed_dirs and stat.S_IMODE(info.st_mode) == 0o555, 'unexpected package directory or mode')
            else:
                require(relative in expected and stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'unexpected package object')
                mode, data = expected[relative]
                require(stat.S_IMODE(info.st_mode) == mode and regular(path) == data, 'package bytes or mode differ from trusted checkout')
                found.add(relative)
    require(found == set(expected), 'package tree incomplete')


def stage_entries(root, entries):
    """Stage validated leaves without following runtime aliases."""
    for name, (mode, data) in sorted(entries.items()):
        path = PurePosixPath(name)
        require(str(path) == name and not path.is_absolute() and '..' not in path.parts, 'unsafe runtime path')
        target = root / name
        for ancestor in reversed(target.parents):
            if ancestor == root or ancestor.is_relative_to(root):
                if ancestor.exists() or ancestor.is_symlink():
                    require(ancestor.is_dir() and not ancestor.is_symlink(), 'runtime symlink parent refused')
                else:
                    ancestor.mkdir(mode=0o755)
        if stat.S_ISREG(mode):
            with target.open('xb') as output:
                output.write(data)
            target.chmod(stat.S_IMODE(mode))
        elif stat.S_ISLNK(mode):
            alias = PurePosixPath(data.decode())
            require(not alias.is_absolute(), 'absolute runtime alias refused')
            target.symlink_to(str(alias))
        else:
            raise ValueError('runtime special object refused')


PROBE = r'''
import hashlib,importlib.util,json,os,pathlib,platform,signal,struct,subprocess,sys,tempfile
assert sys.flags.isolated and sys.dont_write_bytecode and platform.machine()=='aarch64'
def configure_probe_sigint():
    # unshare/sudo may inherit SIG_IGN or a blocked mask. This owned PID1
    # fixture needs normal Python interruption for the publication probe.
    signal.signal(signal.SIGINT,signal.default_int_handler)
    signal.pthread_sigmask(signal.SIG_UNBLOCK,{signal.SIGINT})
    assert signal.getsignal(signal.SIGINT) is signal.default_int_handler
    assert signal.SIGINT not in signal.pthread_sigmask(signal.SIG_BLOCK,set())
configure_probe_sigint()
BASE=pathlib.Path('/usr/lib/a733-tools')
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,BASE/path)
    value=importlib.util.module_from_spec(spec);sys.modules[name]=value;spec.loader.exec_module(value);return value
package=json.loads((BASE/'package.json').read_bytes())
# Import every Python source, including policy dependencies otherwise loaded lazily.
imports=[]
for path in sorted(BASE.rglob('*.py')):
    load('maintenance_probe_'+str(len(imports)),str(path.relative_to(BASE)));imports.append(str(path.relative_to(BASE)))
U=load('maintenance_update','scripts/update-host.py')
R=load('maintenance_recovery','scripts/host-recovery-target.py')
S=U.PREP.SELECT
clis=['scripts/prepare-host-update.py','scripts/update-host.py','scripts/select-host-root.py','scripts/recover-host-root.py','scripts/plan-nvme.py','scripts/install-nvme.py','scripts/acquire-nvme.py']
for path in clis:
    result=subprocess.run(['/usr/bin/python3','-I','-B',str(BASE/path),'--help'],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,(path,result.stderr)
root_mounts=[r.split(' - ')[0].split() for r in pathlib.Path('/proc/self/mountinfo').read_text().splitlines() if r.split(' - ')[0].split()[4]=='/']
candidate_device=os.stat('/fixture/candidate-A').st_dev
assert len(root_mounts)==1 and root_mounts[0][2]==str(os.major(candidate_device))+':'+str(os.minor(candidate_device)),root_mounts
source=U.Source('/fixture/candidate-A',pathlib.Path('/payload-pin').read_text(),'A')
try:
    source.check();review=source.review
    selector=source.files['selected.conf'];selector.seek(0);data=selector.read()
    parsed=S.parse(data);assert S.serialize(parsed)==data
finally:source.close()
with tempfile.TemporaryDirectory(dir='/tmp') as tmp:
    p=pathlib.Path(tmp);p.chmod(0o700)
    S.atomic_store(p,data);assert (p/'selected.conf').read_bytes()==data
    # Queue SIGINT in the actual masked rename region; verify visible publication.
    replace=S.os.replace
    def interrupted(*args,**kwargs):
        replace(*args,**kwargs);os.kill(os.getpid(),signal.SIGINT)
    S.os.replace=interrupted
    try:
        try:S.atomic_store(p,data)
        except S.PublicationError as error:assert error.published
        else:raise AssertionError('queued SIGINT not delivered')
    finally:S.os.replace=replace
    assert (p/'selected.conf').read_bytes()==data
    assert [q.name for q in p.iterdir()]==['selected.conf']
    encoded=R.LAYOUT.encode_gpt(250000000000,512,4096)
    extents=encoded['extents']
    def reader(fd,length,offset):
        for item in extents:
            start=item['offset_bytes'];raw=item['data']
            if start<=offset and offset+length<=start+len(raw):return raw[offset-start:offset-start+length]
        return b''
    context=R.LIVE.gpt_context(-1,{'size_bytes':250000000000,'logical_sector_bytes':512,'physical_sector_bytes':4096},reader)
    assert context['disk_guid']==encoded['disk_guid']
    geometry=encoded['layout'];sysroot=p/'sys';sysroot.mkdir();disk=p/'nvme0n1';disk.mkdir()
    nodes=[]
    for index in range(6):
        base=disk if index==0 else disk/('nvme0n1p'+str(index))
        base.mkdir(exist_ok=True);identity='259:'+str(index)
        (base/'dev').write_text(identity);(base/'removable').write_text('0')
        if index:
            part=geometry['partitions'][index-1]
            (base/'partition').write_text(str(index));(base/'start').write_text(str(part['start_bytes']//512));(base/'size').write_text(str(part['size_bytes']//512))
        (sysroot/identity).symlink_to(base)
        nodes.append({'maj:min':identity,'kname':base.name})
    R.LIVE.sysfs_geometry(nodes,geometry,sysroot)
refusals={}
for name,call in [('recovery',R.environment_guard),('live',R.LIVE.MANAGEMENT.runtime_guard)]:
    try:call()
    except (ValueError,OSError) as error:refusals[name]=str(error)
    else:raise AssertionError(name+' unexpectedly accepted disposable chroot')
assert os.getpid()==1
assert pathlib.Path('/proc/1/root').resolve()==pathlib.Path('/')
assert sorted(p.name for p in pathlib.Path('/dev').iterdir())==['null']
for target in ('/proc','/sys'):
    rows=[r.split(' - ')[0].split() for r in pathlib.Path('/proc/self/mountinfo').read_text().splitlines() if r.split(' - ')[0].split()[4]==target]
    assert len(rows)==1 and {'ro','nosuid','nodev','noexec'}<=set(rows[0][5].split(',')),rows
print(json.dumps({'architecture':platform.machine(),'python':sys.version,'isolated_python':True,'imported_modules':imports,'CLI_help':clis,'authenticated_candidate':review,'selector_roundtrip_atomic_store_queued_SIGINT':'passed','GPT_roundtrip_sysfs_fixture':'passed','production_guard_refusals':refusals,'private_PID_namespace':True,'physical_block_nodes':'absent','source_inspection':'full logical root hash and real ARM debugfs descriptor/selector inspection'}))
'''


def worker(root, pin):
    """Only called under unshare; mount namespace disappears when PID 1 exits."""
    require(os.getpid() == 1 and record(regular(Path(__file__), 1024**2))['sha256'] == pin, 'trusted private PID1 worker required')
    def run(args):
        subprocess.run(args, check=True, env=ENV, capture_output=True, timeout=20)
    run(['/usr/bin/mount', '--make-rprivate', '/'])
    # Give the chroot a visible root mount so the real candidate storage guard
    # can resolve its backing filesystem. Nonrecursive bind keeps host child
    # mounts out; create only our restricted proc/sys mounts afterwards.
    run(['/usr/bin/mount', '--bind', str(root), str(root)])
    run(['/usr/bin/mount', '-t', 'proc', '-o', 'ro,nosuid,nodev,noexec', 'proc', str(root / 'proc')])
    run(['/usr/bin/mount', '--bind', '/sys', str(root / 'sys')])
    run(['/usr/bin/mount', '-o', 'remount,bind,ro,nosuid,nodev,noexec', str(root / 'sys')])
    os.chroot(root);os.chdir('/')
    os.execve('/usr/bin/python3', ['/usr/bin/python3', '-I', '-B', '/probe.py'], ENV)


def binfmt_check(pin):
    registration = regular(Path('/proc/sys/fs/binfmt_misc/qemu-aarch64'), 16384).decode('ascii')
    rows = registration.splitlines()
    require(rows and rows[0] == 'enabled', 'enabled qemu-aarch64 binfmt registration required')
    fields = dict(line.split(' ', 1) for line in rows[1:] if ' ' in line)
    require('F' in fields.get('flags:', ''), 'binfmt F flag required for private chroot')
    require(isinstance(pin, str) and re.fullmatch('[0-9a-f]{64}', pin), 'explicit QEMU interpreter SHA-256 required')
    interpreter = Path(fields.get('interpreter', ''))
    require(interpreter.is_absolute(), 'absolute registered interpreter required')
    # Distribution binfmt aliases are resolved only for this explicitly pinned
    # host tool. No package or runtime input is executed during authentication.
    canonical = interpreter.resolve(strict=True)
    data = regular(canonical, 64 * 1024**2)
    require(record(data)['sha256'] == pin and data.startswith(b'\x7fELF'), 'registered QEMU interpreter bytes differ')
    return {'registration': registration, 'interpreter': str(interpreter),
            'resolved_interpreter': str(canonical), **record(data),
            'limit': 'Hashes configured interpreter pathname; kernel-held binfmt interpreter FD is not independently inspectable.'}


def verify(package_directory, runtime_directory, candidate_directory, package_pin, payload_pin, qemu_pin):
    require(os.geteuid() == 0, 'root required for private ARM64 chroot')
    binfmt = binfmt_check(qemu_pin)
    builder = module('maintenance_runtime', 'build/installer-initramfs.py')
    tools = module('maintenance_packager', 'build/maintenance-tools.py')
    prep = module('maintenance_prepare', 'scripts/prepare-host-update.py')
    package_directory=builder.HOST.safe_path(package_directory)
    package_data, package_manifest = authenticate(package_directory / 'manifest.json', package_pin)
    expected = tools.entries(REPO)
    validate_tree(package_directory / 'tree', expected)
    expected_records = {name: {'mode': mode, **record(data)} for name, (mode, data) in expected.items()}
    require(package_manifest.get('files') == expected_records, 'outer manifest file records differ')
    metadata = json.loads(expected['package.json'][1])
    for key in ('schema', 'profile', 'kernel_release', 'entrypoints', 'uid', 'gid', 'source_records'):
        require(package_manifest.get(key) == metadata[key], 'outer package metadata differs')
    packed = regular(package_directory / 'tools.tar.gz', 8 * 1024**2)
    require(package_manifest.get('artifacts') == {'tools.tar.gz': record(packed)} and
            packed == tools.archive(expected), 'package archive differs from trusted entries')
    runtime, runtime_manifest, runtime_data = builder.runtime_entries(runtime_directory)
    for name, (mode, data) in runtime.items():
        if stat.S_ISREG(mode) and data.startswith(b'\x7fELF'):
            require(len(data) >= 64 and data[4:6] == b'\x02\x01' and struct.unpack_from('<H', data, 18)[0] == 183, 'runtime ELF must be ARM64 little endian')
    require('usr/bin/python3.11' in runtime and 'usr/sbin/debugfs' in runtime, 'required native ARM tools absent')
    candidate_directory=builder.HOST.safe_path(candidate_directory)
    payload_data, payload = authenticate(candidate_directory / 'manifest.json', payload_pin)
    root_record = prep.validate_manifest(payload, 'A')
    base=Path('/var/lib')
    with prep.safe_parent(base) as fd:
        prep.ACQUIRE.storage_guard(fd, 'external')
        space=os.fstatvfs(fd)
        require(space.f_bavail*space.f_frsize >= 6*1024**3, 'private fixture needs at least 6 GiB free')
    root=Path(tempfile.mkdtemp(prefix='a733-maintenance-arm-',dir=base))
    identity=(root.stat().st_dev,root.stat().st_ino)
    try:
        stage_entries(root,runtime)
        stage_entries(root / 'usr/lib/a733-tools',{k:(stat.S_IFREG|mode,data) for k,(mode,data) in expected.items()})
        for name in ('dev','proc','sys','tmp','fixture'):
            target=root/name;target.mkdir(exist_ok=True);target.chmod(0o700 if name=='fixture' else 0o755)
        os.mknod(root/'dev/null',stat.S_IFCHR|0o666,os.makedev(1,3))
        captured=root/'fixture/candidate-A';captured.mkdir(mode=0o700)
        with (captured/'root.ext4').open('x+b') as output:
            prep.capture_root(candidate_directory/'root.ext4',root_record,output)
            selector, metadata=prep.compatibility(output,'A',payload)
        require(regular(candidate_directory/'selected.conf',4096)==selector,'candidate selector differs')
        for name,data in (('manifest.json',payload_data),('selected.conf',selector)):
            (captured/name).write_bytes(data);(captured/name).chmod(0o400)
        (root/'payload-pin').write_text(payload_pin)
        (root/'probe.py').write_text(PROBE)
        verifier=regular(Path(__file__),1024**2)
        worker_path=root/'worker.py';worker_path.write_bytes(verifier);worker_path.chmod(0o400)
        args=['/usr/bin/unshare','--mount','--pid','--fork','--kill-child=SIGKILL','/usr/bin/python3','-I','-B',str(worker_path),'--worker',str(root),record(verifier)['sha256']]
        process=subprocess.Popen(args,env=ENV,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
        try:
            deadline = time.monotonic() + 600
            while True:
                try:
                    stdout, stderr = process.communicate(timeout=min(30, max(0.1, deadline-time.monotonic())))
                    break
                except subprocess.TimeoutExpired:
                    require(time.monotonic() < deadline, 'ARM probe exceeded 600-second deadline')
                    print('ARM maintenance probe running; full candidate hash/debugfs inspection may take minutes', file=sys.stderr, flush=True)
        except BaseException:
            os.killpg(process.pid,signal.SIGKILL);process.wait();raise
        require(process.returncode==0,'ARM maintenance probe failed: '+stderr[-16000:])
        report=json.loads(stdout)
        return {'classification':'ARM64 binfmt/chroot software checks; no board, boot, KVM or recovery acceptance','verifier_source':record(verifier),'binfmt':binfmt,'package_manifest':record(package_data),'package_entries':len(expected),'runtime_manifest':record(runtime_data),'runtime_entries':len(runtime),'payload_manifest':record(payload_data),'native_compatibility':metadata,'ARM_checks':report,'private_fixture':'deleted after namespace exit; no block nodes or physical target arguments','limits':['Native builder kernel and ARM64 binfmt are not A733 hardware.','No update apply, real NVMe, dirty-journal recovery, SD-write or actual KVM guest validation.']}
    finally:
        # A failed teardown retains the owned root; never recurse across mounts.
        require((root.stat().st_dev,root.stat().st_ino)==identity,'private root identity changed; retained '+str(root))
        mounts=[line.split(' - ')[0].split()[4] for line in Path('/proc/self/mountinfo').read_text().splitlines()]
        require(not any(p==str(root) or p.startswith(str(root)+'/') for p in mounts),'mount remains; private root retained '+str(root))
        shutil.rmtree(root)


def main():
    if len(sys.argv)==4 and sys.argv[1]=='--worker':
        worker(Path(sys.argv[2]),sys.argv[3]);return
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('tool_package_directory');parser.add_argument('runtime_directory');parser.add_argument('candidate_a_directory')
    parser.add_argument('--package-manifest-sha256',required=True);parser.add_argument('--payload-manifest-sha256',required=True)
    parser.add_argument('--qemu-interpreter-sha256',required=True)
    args=parser.parse_args()
    result=verify(args.tool_package_directory,args.runtime_directory,args.candidate_a_directory,args.package_manifest_sha256,args.payload_manifest_sha256,args.qemu_interpreter_sha256)
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
