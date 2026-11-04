#!/usr/bin/env python3
"""Actual ARM QEMU network backends and BusyBox CGI; no guest CPU or KVM."""
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
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'}


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, REPO / path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


PROBE = r'''
import hashlib,importlib.util,json,os,pathlib,platform,socket,subprocess,time,urllib.request,urllib.error
assert platform.machine()=='aarch64' and os.getpid()==1
spec=importlib.util.spec_from_file_location('packets','/packet-probe.py')
P=importlib.util.module_from_spec(spec);spec.loader.exec_module(P)
network=P.probe('/usr/bin/qemu-system-aarch64',timeout=30)
reservation=socket.socket();reservation.bind(('127.0.0.1',0));port=reservation.getsockname()[1];reservation.close()
def cgi_chroot():os.chroot('/guest-cgi-root');os.chdir('/')
server=subprocess.Popen(['/bin/busybox','httpd','-f','-p','127.0.0.1:'+str(port),'-h','/www'],preexec_fn=cgi_chroot,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
base='http://127.0.0.1:'+str(port)+'/cgi-bin/probe'
expected=b'a733-kvm-guest-network-v1 counter=1\n'
marker=pathlib.Path('/guest-cgi-root/run/a733-guest-network-confirmed')
try:
    deadline=time.monotonic()+5
    while True:
        try:
            with opener.open(base,timeout=1) as response:first=response.read(1024)
            break
        except urllib.error.URLError:
            assert time.monotonic()<deadline and server.poll() is None
            time.sleep(.05)
    assert first==expected and not marker.exists(),first
    try:opener.open(base+'?confirm=2',timeout=2)
    except urllib.error.HTTPError as error:assert error.code==400;error.close()
    else:raise AssertionError('incorrect confirmation accepted')
    assert not marker.exists()
    with opener.open(base+'?confirm=1',timeout=2) as response:confirmed=response.read(1024)
    assert confirmed==expected
    deadline=time.monotonic()+2
    while not marker.exists():
        assert time.monotonic()<deadline;time.sleep(.01)
    assert marker.read_bytes()==b'1\n'
finally:
    server.terminate()
    try:server.wait(timeout=2)
    except subprocess.TimeoutExpired:server.kill();server.wait(timeout=2)
    stdout,stderr=server.communicate(timeout=2)
    assert len(stdout)+len(stderr)<16384
print(json.dumps({'architecture':platform.machine(),'network_backend':network,'BusyBox_HTTP_CGI':{'response_sha256':hashlib.sha256(first).hexdigest(),'read_without_confirmation':True,'wrong_confirmation_refused':True,'exact_confirmation_recorded':True,'interpreter':'pinned static ARM BusyBox ash/httpd, no guest kernel'},'guest_execution':'none; QMP must report zero CPUs','classification':'software network/CGI checks; not board network, VM or KVM acceptance'}))
'''


def worker(root, pin):
    if os.geteuid() != 0 or os.getpid() != 1:
        raise ValueError('worker requires root in owned PID namespace')
    for namespace in ('mnt', 'net', 'pid'):
        if os.stat('/proc/self/ns/' + namespace).st_ino == os.stat('/proc/1/ns/' + namespace).st_ino:
            raise ValueError('worker requires private namespace: ' + namespace)
    root = Path(root)
    if (root.name != 'root' or not root.parent.name.startswith('a733-guest-network-arm-') or
            root.resolve() != root or root.parent.stat().st_uid != 0 or
            stat.S_IMODE(root.parent.stat().st_mode) != 0o700 or
            hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != pin):
        raise ValueError('invalid private worker source/root')
    subprocess.run(['mount', '--make-rprivate', '/'], check=True, env=ENV, timeout=10)
    subprocess.run(['ip', 'link', 'set', 'lo', 'up'], check=True, env=ENV, timeout=10)
    subprocess.run(['mount', '-t', 'proc', '-o', 'ro,nosuid,nodev,noexec', 'proc', str(root / 'proc')],
                   check=True, env=ENV, timeout=10)
    os.chroot(root)
    os.chdir('/')
    os.execve('/usr/bin/python3', ['/usr/bin/python3', '-I', '-B', '-c', PROBE], ENV)


def verify(rootfs, busybox):
    if os.geteuid() != 0:
        raise ValueError('root required for private ARM/network namespace fixture')
    fixture = module('network_arm_inputs', 'tests/verify_guest_file_backend_arm.py')
    payload = module('network_arm_payload', 'build/host-payload.py')
    maintenance = module('network_arm_binfmt', 'tests/verify_maintenance_arm.py')
    state = module('network_arm_storage', 'build/host-state.py')
    binfmt = maintenance.binfmt_check(fixture.QEMU_PIN)
    storage = state.validate_workspace_storage(Path('/var/lib'))
    if shutil.disk_usage('/var/lib').free < 2 * 1024**3:
        raise ValueError('2 GiB free fixed storage required')
    binary = fixture.regular(busybox, 4 * 1024**2)
    lock = json.loads((REPO / 'configs/diagnostic-sources.lock.json').read_bytes())
    if hashlib.sha256(binary).hexdigest() != lock['busybox']['binary_sha256']:
        raise ValueError('BusyBox must match pinned static guest binary')
    packets = fixture.regular(REPO / 'tests/guest_network_packets.py')
    cgi = fixture.regular(REPO / 'configs/guest-network-cgi')
    source = fixture.regular(Path(__file__))
    with tempfile.TemporaryDirectory(prefix='a733-guest-network-arm-', dir='/var/lib') as temporary:
        work = Path(temporary);root = work / 'root';root.mkdir(mode=0o700)
        captured = work / 'rootfs.tar.gz'
        fixture.capture(rootfs, captured);payload.authenticate(captured, fixture.ROOTFS)
        payload.extract_rootfs(captured, root);fixture.sanitize(root)
        (root / 'packet-probe.py').write_bytes(packets)
        guest = root / 'guest-cgi-root'
        for directory in ('bin', 'dev', 'run', 'www/cgi-bin'):
            (guest / directory).mkdir(parents=True, exist_ok=True)
        (guest / 'bin/busybox').write_bytes(binary);(guest / 'bin/busybox').chmod(0o755)
        for applet in ('sh', 'cat', 'mv', 'rm'):
            (guest / 'bin' / applet).symlink_to('busybox')
        os.mknod(guest / 'dev/null', stat.S_IFCHR | 0o666, os.makedev(1, 3))
        (guest / 'www/cgi-bin/probe').write_bytes(cgi);(guest / 'www/cgi-bin/probe').chmod(0o755)
        (guest / 'run/a733-guest-network-counter').write_bytes(b'1\n')
        qemu = payload.file_record(root / 'usr/bin/qemu-system-aarch64')
        worker_path = work / 'worker.py';worker_path.write_bytes(source);worker_path.chmod(0o400)
        process = subprocess.Popen(['unshare', '--mount', '--net', '--pid', '--fork', '--kill-child=SIGKILL',
            '/usr/bin/python3', '-I', '-B', str(worker_path), '--worker', str(root), fixture.record(source)['sha256']],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=ENV, start_new_session=True)
        try:
            stdout, stderr = process.communicate(timeout=70)
        except BaseException:
            os.killpg(process.pid, signal.SIGKILL);process.wait();raise
        if process.returncode:
            raise ValueError('ARM network probe failed: ' + stderr.decode(errors='replace')[-12000:])
        checks = json.loads(stdout)
    return {'classification': 'ARM QEMU network backends and static BusyBox CGI; no guest CPU/KVM',
        'rootfs': fixture.ROOTFS, 'binfmt': binfmt, 'builder_storage': storage,
        'QEMU_binary': qemu, 'BusyBox_binary': fixture.record(binary), 'packet_source': fixture.record(packets),
        'CGI_source': fixture.record(cgi), 'verifier_source': fixture.record(source), 'ARM_checks': checks,
        'limits': ['No guest kernel runs; QEMU machine none exposes zero CPUs. Synthetic Ethernet is a test endpoint.',
                   'Private network/PID/mount namespace and loopback only; no host management/firewall/bridge changes.',
                   'BusyBox CGI runs under static ARM ash/httpd, without guest init; no DHCP, physical board, VM or KVM evidence.',
                   'Configured binfmt interpreter pathname/hash checked; kernel-held F descriptor not independently inspected.']}


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == '--worker':
        worker(sys.argv[2], sys.argv[3])
    else:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument('rootfs_tar', type=Path)
        parser.add_argument('busybox', type=Path)
        args = parser.parse_args()
        print(json.dumps(verify(args.rootfs_tar, args.busybox), indent=2))
