#!/usr/bin/env python3
"""Opt-in pinned ARM qemu-img held-file probe; no guest boot or KVM evidence.

Run on the known-fixed-storage builder as root with the pinned rootfs archive.
Only a private disposable chroot and synthetic regular files are used. The
internal worker executes this trusted verifier, never rootfs-provided orchestration.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import struct
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'}
ROOTFS = {'bytes': 169852145,
          'sha256': '1967e76c0c831c1c0bab432e7116457d4372c15b413090c054f8958b72dcb392'}
QEMU_PIN = '92c4aeb8dd898de7152c184370ab22f09ae7c5b7279fd300081c76cee3f78a80'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def record(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def regular(path, limit=1024**2):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and
                0 <= before.st_size <= limit, 'bounded single-link regular file required')
        data = stream.read(limit + 1)
        require(len(data) <= limit and identity(before) == identity(os.fstat(stream.fileno())),
                'input changed or exceeds bound')
    return data


def module(name, relative):
    spec = importlib.util.spec_from_file_location(name, REPO / relative)
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


PROBE = r'''
import fcntl,hashlib,importlib.util,json,os,pathlib,platform,stat,subprocess,sys
assert sys.flags.isolated and sys.dont_write_bytecode and platform.machine()=='aarch64'
assert os.getpid()==1 and pathlib.Path('/proc/1/root').resolve()==pathlib.Path('/')
assert sorted(p.name for p in pathlib.Path('/dev').iterdir())==['null']
rows=[r.split(' - ')[0].split() for r in pathlib.Path('/proc/self/mountinfo').read_text().splitlines()]
proc=[r for r in rows if r[4]=='/proc']
assert len(proc)==1 and {'ro','nosuid','nodev','noexec'}<=set(proc[0][5].split(',')),proc
assert not any(r[4]=='/sys' for r in rows)
spec=importlib.util.spec_from_file_location('guest_launcher','/probe-src/launch-kvm-guest.py')
L=importlib.util.module_from_spec(spec);sys.modules[spec.name]=L;spec.loader.exec_module(L)
def rec(data):return {'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
def run(argv,fds=()):
    return subprocess.run(argv,pass_fds=fds,check=True,capture_output=True,text=True,timeout=60)
base=pathlib.Path('/fixture');argv=['--dry-run','--memory-mib','64','--cpus','1'];originals={}
for index,name in enumerate(('kernel','initrd','disk')):
    data=bytes([65+index])*1024**2;path=base/name;path.write_bytes(data)
    originals[name]=rec(data);argv.extend(['--'+name,str(path),'--'+name+'-sha256',originals[name]['sha256']])
results={}
with L.prepare(L.parser().parse_args(argv)) as guest:
    for name,fd in guest.fds.items():
        assert fcntl.fcntl(fd,fcntl.F_GETFL)&os.O_ACCMODE==os.O_RDONLY
        path=base/name;path.rename(base/(name+'.original'))
        replacement=b'replaced-'+name.encode();path.write_bytes(replacement)
        assert rec(path.read_bytes())!=originals[name]
        held='/proc/self/fd/'+str(fd)
        info=json.loads(run(['/usr/bin/qemu-img','info','-f','raw','--output=json',held],(fd,)).stdout)
        assert info['format']=='raw' and info['virtual-size']==originals[name]['bytes'],info
        copied=base/'copied.raw'
        run(['/usr/bin/qemu-img','dd','-f','raw','-O','raw','if='+held,'of='+str(copied)],(fd,))
        actual=rec(copied.read_bytes());assert actual==originals[name],(name,actual)
        copied.unlink()
        results[name]={'original':originals[name],'replacement':rec(replacement),'copied':actual,
                       'qemu_img_info':info,'descriptor_access':'read-only'}
version=run(['/usr/bin/qemu-img','--version']).stdout.strip()
assert version.startswith('qemu-img version 7.2.22 '),version
print(json.dumps({'architecture':platform.machine(),'python':sys.version,'qemu_img_version':version,
                  'private_PID_namespace':True,'private_proc':'ro,nosuid,nodev,noexec',
                  'physical_block_nodes':'absent','held_descriptor_checks':results}))
'''


def worker(root, pin):
    require(os.getpid() == 1 and record(regular(Path(__file__)))['sha256'] == pin,
            'trusted private PID1 worker required')
    for args in (['/usr/bin/mount', '--make-rprivate', '/'],
                 ['/usr/bin/mount', '--bind', str(root), str(root)],
                 ['/usr/bin/mount', '-t', 'proc', '-o', 'ro,nosuid,nodev,noexec',
                  'proc', str(root / 'proc')]):
        subprocess.run(args, check=True, env=ENV, capture_output=True, timeout=20)
    os.chroot(root)
    os.chdir('/')
    os.execve('/usr/bin/python3', ['/usr/bin/python3', '-I', '-B', '/probe.py'], ENV)


def capture(source, target):
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream, target.open('xb') as output:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and
                before.st_size == ROOTFS['bytes'], 'pinned regular rootfs archive required')
        remaining = ROOTFS['bytes']
        while remaining:
            data = stream.read(min(1024**2, remaining))
            require(data, 'rootfs archive shortened')
            output.write(data)
            remaining -= len(data)
        require(identity(before) == identity(os.fstat(stream.fileno())), 'rootfs changed during capture')
    target.chmod(0o400)


def sanitize(root):
    # Authentication preceded extraction. Still remove every special object and
    # discard all supplied /dev entries before creating our one harmless node.
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(directory) / name
            info = path.lstat()
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)):
                require(stat.S_ISCHR(info.st_mode) or stat.S_ISFIFO(info.st_mode),
                        'unexpected special rootfs object')
                path.unlink()
    for name in ('dev', 'proc', 'sys', 'tmp'):
        path = root / name
        require(path.is_dir() and not path.is_symlink(), 'real runtime directory required: ' + name)
        require(not os.path.ismount(path), 'unexpected staging mount')
    for child in (root / 'dev').iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    os.mknod(root / 'dev/null', stat.S_IFCHR | 0o666, os.makedev(1, 3))


def verify(rootfs):
    require(os.geteuid() == 0, 'root required for private ARM chroot')
    maintenance = module('file_backend_runtime', 'tests/verify_maintenance_arm.py')
    binfmt = maintenance.binfmt_check(QEMU_PIN)
    payload = module('file_backend_payload', 'build/host-payload.py')
    state = module('file_backend_state', 'build/host-state.py')
    evidence = regular(REPO / 'docs/evidence/host-userspace-20261104.json')
    require(json.loads(evidence)['files']['rootfs.tar.gz'] == ROOTFS, 'trusted rootfs pin differs')
    base = Path('/var/lib')
    require(base.resolve(strict=True) == base and base.is_dir(), 'real fixed staging parent required')
    storage = state.validate_workspace_storage(base)
    require(shutil.disk_usage(base).free >= 2 * 1024**3, 'private fixture needs 2 GiB free')
    owned = Path(tempfile.mkdtemp(prefix='a733-guest-file-arm-', dir=base))
    owned_identity = (owned.stat().st_dev, owned.stat().st_ino)
    try:
        archive = owned / 'rootfs.tar.gz'
        capture(rootfs, archive)
        payload.authenticate(archive, ROOTFS)  # Authenticate private snapshot BEFORE tar inspection.
        root = owned / 'root'
        root.mkdir(mode=0o700)
        payload.extract_rootfs(archive, root)
        root.chmod(0o700)
        sanitize(root)
        runtime = {}
        for name in ('usr/bin/python3.11', 'usr/bin/qemu-img'):
            data = regular(root / name, 64 * 1024**2)
            require(len(data) >= 64 and data[:6] == b'\x7fELF\x02\x01' and
                    struct.unpack_from('<H', data, 18)[0] == 183, 'ARM64 little-endian ELF required')
            runtime[name] = {**record(data), 'ELF_machine': 183}
        sources = {}
        source_dir = root / 'probe-src'
        source_dir.mkdir(mode=0o700)
        for name in ('launch-kvm-guest.py', 'guest-storage.py'):
            data = regular(REPO / 'scripts' / name)
            target = source_dir / name
            with target.open('xb') as output:
                output.write(data)
            target.chmod(0o400)
            sources['scripts/' + name] = record(data)
        (root / 'fixture').mkdir(mode=0o700)
        with (root / 'probe.py').open('x') as output:
            output.write(PROBE)
        verifier = regular(Path(__file__))
        worker_path = owned / 'worker.py'
        with worker_path.open('xb') as output:
            output.write(verifier)
        worker_path.chmod(0o400)
        args = ['/usr/bin/unshare', '--mount', '--pid', '--fork', '--kill-child=SIGKILL',
                '/usr/bin/python3', '-I', '-B', str(worker_path), '--worker',
                str(root), record(verifier)['sha256']]
        process = subprocess.Popen(args, env=ENV, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, start_new_session=True)
        try:
            stdout, stderr = process.communicate(timeout=120)
        except BaseException:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise
        require(process.returncode == 0, 'ARM file-backend probe failed: ' + stderr[-16000:])
        checks = json.loads(stdout)
        return {'classification': 'ARM64 QEMU file-backend software probe; no guest boot or KVM execution',
                'rootfs': ROOTFS, 'userspace_evidence': record(evidence), 'binfmt': binfmt,
                'verifier_source': record(verifier), 'trusted_sources': sources,
                'runtime_ELF': runtime, 'builder_staging_storage': storage, 'ARM_checks': checks,
                'private_fixture': 'deleted after namespace exit; no physical target arguments',
                'limits': ['Native builder kernel and ARM binfmt do not validate A733 hardware.',
                           'Dry-run uses read-only descriptors and does not exercise the storage guard, writable disk or launcher flock.',
                           'qemu-img file backend only; QEMU system kernel/initrd loaders, VM, KVM, boot and networking untested.',
                           'Configured interpreter pathname hash checked; inherited kernel binfmt F interpreter FD is not independently inspectable.']}
    finally:
        require((owned.stat().st_dev, owned.stat().st_ino) == owned_identity,
                'private root identity changed; retained ' + str(owned))
        mounts = [line.split(' - ')[0].split()[4]
                  for line in Path('/proc/self/mountinfo').read_text().splitlines()]
        require(not any(p == str(owned) or p.startswith(str(owned) + '/') for p in mounts),
                'mount remains; private root retained ' + str(owned))
        shutil.rmtree(owned)


def main():
    if len(sys.argv) == 4 and sys.argv[1] == '--worker':
        worker(Path(sys.argv[2]), sys.argv[3])
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('rootfs_tar', type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.rootfs_tar), indent=2))


if __name__ == '__main__':
    main()
