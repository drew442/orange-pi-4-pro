#!/usr/bin/python3
"""Disposable managed fixture acceptance; evidence bytes grant no publication authority.

Construct all lifecycle/supervisor objects through SUPERVISOR exported here.
Production guests do not use this reader or its additional disk hashing.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import select
import socket
import stat
import subprocess
import threading
import time
import importlib.util

BASE = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location('fixture_supervisor', BASE / 'scripts/managed-guest-supervisor.py')
SUPERVISOR = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(SUPERVISOR)
POLICY = SUPERVISOR.LIFECYCLE.POLICY
ENROLL = SUPERVISOR.ENROLL
require = SUPERVISOR.require
PROFILE = 'managed-power-key-v1'
EVIDENCE_LIMIT = 256 * 1024
LOCK_PATH = BASE / 'configs/managed-guest-profile.lock.json'
KERNEL = dict(bytes=5253128, sha256='29ba9385454cfbde890cabd24ced727838658d939368ab52dfbf6d770433e959')
INITRD = dict(bytes=975144, sha256='315f6ff4398e477bf1e040cca3c60483dca88c68e99a9a3cda0ecd545b5d0da3')
CPU = '5ab247be0ba27004a2adee73bc50d4a1dfcd98f16ef5fa32c024e5f1c5c29f1c'
MARKER = 'a733-kvm-guest-fixture-v1'
LOCK = dict(schema=1, profile=PROFILE, kernel=KERNEL, initrd=INITRD, disk_bytes=134217728,
            fixture_uuid='d67f33ba-e201-46bc-980b-61901bbd1156', label='kvm-fixture',
            marker=MARKER, cpu_sha256=CPU,
            source='docs/evidence/managed-fixture-build-20261112.json')
FIELDS = ((1080, bytes.fromhex('53ef')), (1116, bytes.fromhex('28000000c20200006b040000')),
          (1128, bytes.fromhex('d67f33bae20146bc980b61901bbd1156')),
          (1144, bytes.fromhex('6b766d2d666978747572650000000000')))


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate profile key')
        result[key] = value
    return result


def validate_lock(data):
    require(type(data) is bytes and 0 < len(data) <= 4096, 'profile size differs')
    value = json.loads(data, object_pairs_hook=unique)
    require(canonical(value) == canonical(LOCK), 'reviewed fixture profile differs')
    return value


def counter(data):
    require(type(data) is bytes and re.fullmatch(rb'(0|[1-9][0-9]{0,7})\n', data),
            'noncanonical fixture counter')
    return int(data)


def superblock(fd):
    require(os.fstat(fd).st_size == LOCK['disk_bytes'], 'fixture disk size differs')
    for offset, expected in FIELDS:
        require(os.pread(fd, len(expected), offset) == expected, 'fixture ext4 identity differs')


def debugfs(fd, leaf, uid, gid):
    """Read fixed ext4 paths as the guest, with no shell or filesystem writes."""
    require(leaf in ('/counter', '/fixture-id'), 'unsupported fixture path')
    superblock(fd)
    args = ['/usr/bin/setpriv', '--reuid=' + str(uid), '--regid=' + str(gid),
            '--clear-groups', '--inh-caps=-all', '--ambient-caps=-all',
            '--bounding-set=-all', '--no-new-privs', '/usr/sbin/debugfs',
            '-R', 'cat ' + leaf, '/proc/self/fd/' + str(fd)]
    process = subprocess.Popen(args, pass_fds=(fd,), stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'})
    output = {stream: bytearray() for stream in (process.stdout, process.stderr)}
    deadline = time.monotonic() + 10
    try:
        active = list(output)
        while active:
            remaining = deadline - time.monotonic()
            require(remaining > 0, 'debugfs deadline exceeded')
            ready = select.select(active, [], [], remaining)[0]
            require(ready, 'debugfs deadline exceeded')
            for stream in ready:
                chunk = os.read(stream.fileno(), 4097)
                if not chunk:
                    active.remove(stream)
                else:
                    output[stream].extend(chunk)
                    require(sum(map(len, output.values())) <= 4096, 'debugfs output limit exceeded')
        require(process.wait(timeout=max(0.001, deadline - time.monotonic())) == 0,
                'debugfs failed')
        return bytes(output[process.stdout])
    finally:
        try:
            try:
                if process.poll() is None:
                    process.kill()
            finally:
                process.wait(timeout=1)
        finally:
            try:
                process.stdout.close()
            finally:
                process.stderr.close()


def http(port, expected, *, confirm=False, before_send=None, timeout=5):
    """One connection, no proxy, redirect, reconnect or retry, bounded total I/O."""
    require(type(port) is int and 1024 <= port <= 65535, 'invalid fixture host port')
    deadline = time.monotonic() + timeout
    path = '/cgi-bin/probe' + ('?confirm=' + str(expected) if confirm else '')
    request = ('GET ' + path + ' HTTP/1.0\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n').encode()
    body = ('a733-kvm-guest-network-v1 counter=' + str(expected) + '\n').encode()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.settimeout(max(0.001, deadline - time.monotonic()))
        connection.connect(('127.0.0.1', port))
        if before_send is not None:
            before_send()
        connection.settimeout(max(0.001, deadline - time.monotonic()))
        connection.sendall(request)
        data = bytearray()
        while True:
            require(time.monotonic() < deadline, 'HTTP deadline exceeded')
            connection.settimeout(max(0.001, deadline - time.monotonic()))
            chunk = connection.recv(4097)
            if not chunk:
                break
            data.extend(chunk)
            require(len(data) <= 4096, 'HTTP size limit exceeded')
    header, separator, received = bytes(data).partition(b'\r\n\r\n')
    lines = header.split(b'\r\n')
    require(separator and re.fullmatch(rb'HTTP/1\.[01] 200(?: [\x20-\x7e]*)?', lines[0]),
            'HTTP status/framing differs')
    fields = {}
    for line in lines[1:]:
        key, colon, value = line.partition(b':')
        require(colon and re.fullmatch(rb'[A-Za-z0-9-]+', key) and
                key.lower() not in fields and all(32 <= byte <= 126 or byte == 9 for byte in value),
                'HTTP header differs')
        fields[key.lower()] = value.strip()
    require(b'transfer-encoding' not in fields and b'location' not in fields and
            (b'content-length' not in fields or fields[b'content-length'] == str(len(body)).encode()) and
            received == body, 'HTTP body differs')
    return dict(status=200, body_sha256=hashlib.sha256(received).hexdigest(), bytes=len(data))


def census(pid, cpus, port):
    """Read trusted procfs; basename lookalikes are never KVM descriptors.

    Linux 2ac08e8c7cdc28abbdc5c9a9dd812f887ae9c79f:
    virt/kvm/kvm_main.c:create_vcpu_fd uses kvm-vcpu:%d;
    fs/anon_inodes.c:anon_inodefs_dname emits anon_inode:%s.
    Each configured index must occur once, with no extra vCPU descriptors.
    """
    result, sockets, vcpus = [], set(), set()
    entries = list(os.scandir(f'/proc/{pid}/fd'))
    require(len(entries) <= 4096, 'child descriptor limit exceeded')
    for entry in entries:
        require(re.fullmatch('[0-9]+', entry.name), 'invalid descriptor entry')
        target = os.readlink(entry.path)
        if target == '/dev/kvm':
            info = os.stat(entry.path)
            require(stat.S_ISCHR(info.st_mode) and os.major(info.st_rdev) == 10 and
                    os.minor(info.st_rdev) == 232, 'actual KVM character device required')
            result.append(dict(fd=int(entry.name), target=target))
        if target == 'anon_inode:kvm-vm' or target in (
                'anon_inode:kvm-arm-vgic-v2', 'anon_inode:kvm-arm-vgic-v3'):
            result.append(dict(fd=int(entry.name), target=target))
        match = re.fullmatch(r'anon_inode:kvm-vcpu:(0|[1-9][0-9]*)', target)
        require(not target.startswith('anon_inode:kvm-vcpu:') or match is not None,
                'invalid KVM vCPU descriptor name')
        if match:
            index = int(match[1])
            require(index not in vcpus, 'duplicate KVM vCPU descriptor')
            vcpus.add(index)
            result.append(dict(fd=int(entry.name), target=target))
        match = re.fullmatch(r'socket:\[([0-9]+)\]', target)
        if match:
            sockets.add(match[1])
    require(any(item['target'] == '/dev/kvm' for item in result) and
            sum(item['target'] == 'anon_inode:kvm-vm' for item in result) == 1 and
            len(vcpus) == cpus and set(range(cpus)) == vcpus and
            any(item['target'].startswith('anon_inode:kvm-arm-vgic-') for item in result),
            'actual KVM VM/vCPU/VGIC descriptors required')
    with open('/proc/net/tcp', 'rb') as stream:
        data = stream.read(1024 * 1024 + 1)
    require(len(data) <= 1024 * 1024, 'TCP census limit exceeded')
    listeners = []
    for line in data.splitlines()[1:]:
        fields = line.split()
        require(len(fields) >= 10, 'invalid TCP census')
        if fields[1] == f'0100007F:{port:04X}'.encode() and fields[3] == b'0A' and fields[9].decode() in sockets:
            listeners.append(fields[9].decode())
    require(len(listeners) == 1, 'QEMU-owned loopback listener required')
    return dict(kvm_fds=sorted(result, key=lambda item: item['fd']), listen_inode=listeners[0], port=port)


class Fixture:
    def __init__(self, supervisor):
        require(type(supervisor) is SUPERVISOR.Supervisor, 'actual Supervisor required')
        supervisor._owner()
        require(os.getuid() == os.geteuid() == 0 and not supervisor._started,
                'root fixture must attach before start')
        require(not hasattr(supervisor, '_managed_fixture'), 'fixture already attached')
        self.supervisor, self.run = supervisor, supervisor.run
        self._pid, self._thread = os.getpid(), threading.get_ident()
        self._attempted = self._verified = self._attested = False
        head = self.run.recheck()
        require(head['status'] == 'admitted' and self.run._record['fixture_profile'] == PROFILE and
                self.run.config['name'] != 'firstkvm', 'managed disposable fixture required')
        self.config = self.run.config
        require(self.config['kernel'] == KERNEL and self.config['initrd'] == INITRD and
                self.config['disk']['bytes'] == LOCK['disk_bytes'], 'reviewed fixture inputs required')
        network = self.config.get('network', {})
        require(network.get('mode') == 'isolated' and network.get('guest_tcp_port') == 8080 and
                type(network.get('host_tcp_port')) is int and 1024 <= network['host_tcp_port'] <= 65535,
                'fixture network differs')
        supervisor._managed_fixture = self  # Claim once, including failed preparations.
        self._lock = ENROLL.HeldPath(str(LOCK_PATH))
        supervisor._stack.callback(self._lock.close)
        ENROLL.regular(self._lock.info)
        require(not self._lock.info.st_mode & 0o222 and 0 < self._lock.info.st_size <= 4096,
                'profile must be bounded root-owned read-only')
        self._lock_data = os.pread(self._lock.fd, 4097, 0)
        validate_lock(self._lock_data)
        self._lock.recheck()
        self.old_pin = self.run._record['manifest_sha256']
        with POLICY.Inspection(self.config['name'], self.old_pin) as inspected:
            self._inspection(inspected)
            require(inspected.disk_record == self.config['disk'], 'old fixture disk differs')
            self.old = self._counter(inspected)
            require(self.old < 99999999, 'fixture counter exhausted')
            self._disk_inode = ENROLL.inode(os.fstat(inspected.disk_fd))
        self.expected = self.old + 1
        self.evidence = dict(schema=1, profile='a733-managed-fixture-acceptance-v1',
                             fixture_profile=PROFILE, storage=copy.deepcopy(self.run._record['storage']),
                             name=self.config['name'], run_id=self.run.run_id, boot_id=self.run.boot_id,
                             admission_sha256=self.run.admission_sha256, old_manifest_sha256=self.old_pin,
                             profile_sha256=hashlib.sha256(self._lock_data).hexdigest(),
                             previous_counter=self.old, counter=self.expected,
                             hardware_acceptance=False, checkpoint_authorized=False)

    def _owner(self):
        require(os.getpid() == self._pid and threading.get_ident() == self._thread,
                'fixture process/thread changed')
        self.supervisor._owner()
        require(self.supervisor._managed_fixture is self, 'fixture attachment differs')
        self._lock.recheck()
        require(os.pread(self._lock.fd, 4097, 0) == self._lock_data, 'fixture profile changed')
        self.run.recheck()
        self.run.guest_lock.recheck()
        require(self.run.config == self.config and self.run._record['manifest_sha256'] == self.old_pin,
                'fixture run inputs changed')

    def _inspection(self, inspected):
        require(type(inspected) is POLICY.Inspection, 'actual stopped Inspection required')
        self.run.guest_lock.recheck()
        inspected.recheck()
        require(inspected.name == self.config['name'] and inspected.manifest == self.run.manifest and
                inspected.manifest_record['sha256'] == self.old_pin and
                inspected.manifest['guest_uuid'] == self.run._record['storage']['guest_uuid'] and
                POLICY.semantic(inspected.context) == POLICY.semantic(
                    POLICY.STORAGE.verify_guest_directory(self.run._controller.context.guest_fd)),
                'fixture inspection authority differs')
        require(inspected._hash(inspected.fds['kernel'], KERNEL['bytes']) == KERNEL and
                inspected._hash(inspected.fds['initrd'], INITRD['bytes']) == INITRD,
                'fixture pinned boot inputs differ')

    def _counter(self, inspected):
        uid, gid = self.config['uid'], self.config['gid']
        require(debugfs(inspected.disk_fd, '/fixture-id', uid, gid) == (MARKER + '\n').encode(),
                'fixture marker differs')
        value = counter(debugfs(inspected.disk_fd, '/counter', uid, gid))
        inspected.recheck()
        return value

    def _console(self, *, stopped=False):
        data = self.supervisor.console_snapshot()
        require(len(data) <= SUPERVISOR.CONSOLE_LIMIT, 'fixture console limit exceeded')
        require(b'FAIL' not in data and b'Kernel panic' not in data and b'not syncing' not in data,
                'fixture guest failure')
        channel = self.supervisor.channel
        require(channel is not None and not any(event.get('message', {}).get('event') in ('RESET', 'GUEST_PANICKED')
                for event in channel.transcript if isinstance(event, dict)), 'guest reset observed')
        *lines, tail = data.split(b'\n')
        tail = tail.removesuffix(b'\r')
        lines = [line.removesuffix(b'\r') for line in lines]
        patterns = {
            b'PASS cpu': ('PASS cpu sha256=' + CPU).encode(),
            b'PASS disk': f'PASS disk previous={self.old} counter={self.expected}'.encode(),
            b'READY network': f'READY network tcp=8080 counter={self.expected}; host must verify and confirm HTTP response'.encode(),
            b'PASS network': f'PASS network loopback-http counter={self.expected}'.encode(),
            b'PASS fixture': f'PASS fixture counter={self.expected}; READY managed-power-key-v1'.encode(),
            b'PASS managed-power-key-sync': b'PASS managed-power-key-sync'}
        found = set()
        for prefix, exact in patterns.items():
            matches = [line for line in lines if line.startswith(prefix)]
            require(len(matches) <= 1 and all(line == exact for line in matches),
                    'duplicate or incorrect fixture console evidence')
            if matches:
                found.add(prefix)
        # An active pipe can end at any byte. Never count its final fragment;
        # wait if it could be structured evidence, including an unfinished prefix.
        # At EOF such a fragment cannot finish and must fail closed. Complete
        # duplicates cannot hide behind a missing final newline.
        structured = (b'PASS ', b'READY ', b'FAIL')
        pending = bool(tail) and any(tail.startswith(prefix) or prefix.startswith(tail)
                                     for prefix in structured)
        for prefix in patterns:
            require(not (tail.startswith(prefix) and prefix in found),
                    'duplicate unterminated fixture console evidence')
        require(not (stopped and pending), 'unterminated fixture console evidence at EOF')
        positions = [lines.index(exact) for prefix, exact in patterns.items() if prefix in found]
        require(positions == sorted(positions), 'fixture console order differs')
        if not stopped:
            require(b'PASS managed-power-key-sync' not in found and channel.shutdown_message is None,
                    'fixture stopped before stop request')
        return data, set() if pending else found

    def _live(self):
        self._owner()
        owner = self.supervisor
        require(owner._started and not owner._stopped and not owner._failed and
                owner.poll() is None and self.run.recheck()['status'] == 'running', 'live fixture required')
        require(type(owner.witness) is SUPERVISOR.PROCESS.ChildWitness and
                owner.witness.pid == owner.pid and owner.witness.start_ticks == owner.start_ticks,
                'actual child witness required')
        owner.witness.validate_child()
        owner.permit.recheck()
        return census(owner.pid, self.config['cpus'], self.config['network']['host_tcp_port'])

    def verify_and_confirm(self, timeout=120):
        self._owner()
        require(type(timeout) in (int, float) and 0 < timeout <= 120 and
                not self._attempted and not self._verified, 'fixture verification is one-shot')
        deadline = time.monotonic() + timeout
        try:
            def wait_console(required, phase, *, before_confirmation=False):
                while True:
                    self._live()
                    _, found = self._console()
                    if before_confirmation:
                        require(not {b'PASS network', b'PASS fixture'} & found,
                                'fixture confirmed before host request')
                    if required <= found:
                        return
                    require(time.monotonic() < deadline, 'fixture ' + phase + ' deadline exceeded')
                    time.sleep(min(0.02, max(0, deadline - time.monotonic())))
            ready = {b'PASS cpu', b'PASS disk', b'READY network'}
            confirmed = ready | {b'PASS network', b'PASS fixture'}
            wait_console(ready, 'readiness', before_confirmation=True)
            self.evidence['live'] = self._live()
            port = self.config['network']['host_tcp_port']
            def request(confirm=False):
                require(time.monotonic() < deadline, 'fixture HTTP deadline exceeded')
                return http(port, self.expected, confirm=confirm,
                            before_send=self._mark_attempted if confirm else None,
                            timeout=min(5, deadline - time.monotonic()))
            self.evidence['http_probe'] = request()
            wait_console(ready, 'preconfirmation', before_confirmation=True)
            self.evidence['http_confirmation'] = request(True)
            wait_console(confirmed, 'confirmation')
            self.evidence['http_post_confirmation'] = request()
            wait_console(confirmed, 'postconfirmation')
            self.evidence['permit_sha256'] = hashlib.sha256(self.supervisor.permit.data).hexdigest()
            require(time.monotonic() <= deadline, 'fixture verification deadline exceeded')
            self._verified = True
            return copy.deepcopy(self.evidence)
        except BaseException:
            self._attempted = True  # Any uncertainty consumes this validation attempt.
            raise

    def _mark_attempted(self):
        self._owner()
        require(not self._attempted, 'confirmation already attempted')
        self._attempted = True  # Before sendall: a lost response can never trigger retry.

    def attest_stopped(self, outcome, inspection):
        self._owner()
        require(self._verified and not self._attested and type(outcome) is SUPERVISOR.StoppedRun and
                outcome._supervisor is self.supervisor and outcome is self.supervisor._outcome,
                'actual same-supervisor stopped outcome required')
        stopped = outcome.recheck()
        self._inspection(inspection)
        require(ENROLL.inode(os.fstat(inspection.disk_fd)) == self._disk_inode,
                'fixture stopped disk inode differs')
        data, found = self._console(stopped=True)
        require({b'PASS cpu', b'PASS disk', b'READY network', b'PASS network', b'PASS fixture',
                 b'PASS managed-power-key-sync'} <= found, 'fixture sync evidence required')
        require(self._counter(inspection) == self.expected, 'persisted fixture counter differs')
        outcome.recheck()
        self.evidence.update(outcome=stopped, stopped_disk=copy.deepcopy(inspection.disk_record),
                             console_bytes=len(data), console_sha256=hashlib.sha256(data).hexdigest(),
                             kernel=KERNEL, initrd=INITRD)
        result = canonical(self.evidence)
        require(len(result) <= EVIDENCE_LIMIT, 'fixture evidence limit exceeded')
        self._attested = True
        return result
