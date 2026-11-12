#!/usr/bin/env python3
"""Durably capture known SPI programmer events on a native off-board host.

Creates a new private log, fsyncs each event and its directory before acknowledging
it, and never grants hardware acceptance. SSH invokes only the constructed known
programmer command; staging remains a separate coordinator task. No shell command
or target override is accepted. Capture failure closes the channel and stops SSH.
"""
import argparse
import base64
import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import shlex
import stat
import subprocess
import sys
import time
import uuid

MAX_LINE = 65536
MAX_RECORDS = 4096
MAX_TOTAL = 8 * 1048576
FREE_FLOOR = 256 * 1048576
TIMEOUT = 45
FINGERPRINT = 'SHA256:Epfegy9GsJ6ng4/0EH3dZmir7beQj5uYTnIbE4JPfEI'
HOST = '192.168.1.116'
PROGRAMMER_PIN = '635581fd3290a5baf574b0f8e77d87e9a8ea28a67c38380591b05e67b947dbe1'
PROGRAMMER_SIZE = 27218

# The remote command executes this fixed bootstrap, never the supplied pathname.
# Fixed pin/size arguments are embedded by ssh_argv, never read from CLI/env.
BOOTSTRAP = r'''
import ctypes, hashlib, os, re, stat, sys

def require(ok, why):
    if not ok:
        raise ValueError(why)

def source_stamp(info, leaf=True):
    fields = ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid')
    if leaf:
        fields += ('st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
    return tuple(getattr(info, key) for key in fields)

def no_swap():
    with open('/proc/swaps', encoding='ascii') as stream:
        rows = [line.split() for line in stream]
    require(rows == [['Filename', 'Type', 'Size', 'Used', 'Priority']],
            'authenticated programmer requires no swap')

def ram_filesystem(fd):
    result = (ctypes.c_long * 32)()
    require(ctypes.CDLL(None, use_errno=True).fstatfs(fd, ctypes.byref(result)) == 0
            and result[0] == 0x01021994, 'programmer source must be tmpfs')

def run_verified(path, expected_pin, expected_size, arguments):
    require(os.geteuid() == 0, 'root programmer bootstrap required')
    require(isinstance(path, str) and
            re.fullmatch(r'/run/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+', path) and
            all(part not in ('.', '..') for part in path.split('/')) and
            os.path.normpath(path) == path, 'invalid canonical programmer path')
    require(re.fullmatch('[0-9a-f]{64}', expected_pin) and
            type(expected_size) is int and 0 < expected_size <= 1048576,
            'invalid fixed programmer source pin/size')
    no_swap()
    fds, links = [], []
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    old_argv = sys.argv
    try:
        parent = os.open('/', flags | os.O_DIRECTORY)
        fds.append(parent)
        links.append((None, '/', parent, source_stamp(os.fstat(parent), False)))
        parts = path.split('/')[1:]
        for part in parts[:-1]:
            child = os.open(part, flags | os.O_DIRECTORY, dir_fd=parent)
            fds.append(child)
            links.append((parent, part, child, source_stamp(os.fstat(child), False)))
            parent = child
        for _, _, fd, _ in links:
            info = os.fstat(fd)
            require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and
                    not info.st_mode & 0o022, 'unsafe programmer ancestor')
        require(stat.S_IMODE(os.fstat(parent).st_mode) == 0o700,
                'programmer immediate parent must be root 0700')
        ram_filesystem(parent)
        source_fd = os.open(parts[-1], flags, dir_fd=parent)
        fds.append(source_fd)
        info = os.fstat(source_fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and
                stat.S_IMODE(info.st_mode) == 0o400 and info.st_nlink == 1 and
                info.st_size == expected_size and info.st_dev == os.fstat(parent).st_dev,
                'programmer must be root 0400 single-link exact-size regular tmpfs source')
        ram_filesystem(source_fd)
        initial = source_stamp(info)
        def check():
            no_swap()
            for ancestor, name, fd, snapshot in links:
                require(source_stamp(os.fstat(fd), False) == snapshot and
                        source_stamp(os.stat(name, dir_fd=ancestor, follow_symlinks=False), False) == snapshot,
                        'programmer ancestor identity/ownership changed')
            require(source_stamp(os.fstat(source_fd)) == initial and
                    source_stamp(os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)) == initial,
                    'programmer held source identity/content metadata changed')
        check()
        data = os.pread(source_fd, expected_size + 1, 0)
        require(type(data) is bytes and len(data) == expected_size and
                hashlib.sha256(data).hexdigest() == expected_pin, 'programmer source pin mismatch')
        check()
        namespace = {'__name__': '__main__', '__file__': path, '__package__': None,
                     '_AUTHENTICATED_BOOTSTRAP': {'pin': expected_pin, 'path': path,
                                                 'fd': source_fd, 'check': check}}
        sys.argv = [path] + list(arguments)
        exec(compile(data, path, 'exec'), namespace)
        return namespace
    finally:
        sys.argv = old_argv
        failures = []
        for fd in reversed(fds):
            try:
                os.close(fd)
            except OSError as error:
                failures.append(error)
        if failures:
            raise failures[0]
'''


def require(ok, message):
    if not ok:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode('ascii')


def sha(value):
    return hashlib.sha256(value).hexdigest()


class BoundedLines:
    def __init__(self, stream):
        self.fd, self.buffer = stream.fileno(), b''

    def __call__(self, limit, timeout):
        deadline = time.monotonic() + timeout
        with selectors.DefaultSelector() as selector:
            selector.register(self.fd, selectors.EVENT_READ)
            while b'\n' not in self.buffer:
                require(len(self.buffer) < limit, 'oversized/truncated event')
                remaining = deadline - time.monotonic()
                require(remaining > 0 and selector.select(remaining), 'event timeout')
                data = os.read(self.fd, min(4096, limit + 1 - len(self.buffer)))
                require(bool(data), 'event EOF/truncated line')
                self.buffer += data
            line, self.buffer = self.buffer.split(b'\n', 1)
            require(len(line) + 1 <= limit, 'oversized event')
            return line + b'\n'


    def require_eof(self, timeout):
        require(not self.buffer, 'trailing output after final outcome')
        with selectors.DefaultSelector() as selector:
            selector.register(self.fd, selectors.EVENT_READ)
            require(selector.select(timeout), 'remote EOF timeout after final outcome')
            require(os.read(self.fd, 1) == b'', 'trailing output after final outcome')


def native_context():
    # Pin initial PID/user namespaces and share PID1 mount namespace. This tool
    # must run natively on the separate x86-64 capture machine, never the A733.
    require(os.uname().machine == 'x86_64', 'native off-board x86-64 host required')
    for name, kind, inode in (('pid', 0x20000000, 0xEFFFFFFC),
                              ('user', 0x10000000, 0xEFFFFFFD),
                              ('mnt', 0x20000, None)):
        fd = os.open('/proc/self/ns/' + name, os.O_RDONLY | os.O_CLOEXEC)
        try:
            mine, init = os.fstat(fd), os.stat('/proc/1/ns/' + name)
            require((mine.st_dev, mine.st_ino) == (init.st_dev, init.st_ino) and
                    (inode is None or mine.st_ino == inode) and
                    fcntl.ioctl(fd, 0xB703) == kind, 'initial native namespace required')
        finally:
            os.close(fd)


def directory_stamp(info):
    return info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid


class NativeLog:
    """New nofollow log in a newly created 0700 directory; native disk only."""
    def __init__(self, path):
        self.fds, self.links = [], []
        self.fd = None
        self.closed = False
        try:
            path = os.fspath(path)
            require(os.path.isabs(path) and os.path.normpath(path) == path,
                    'capture output must be canonical absolute path')
            parts = Path(path).parts
            require(len(parts) >= 3, 'capture output needs private parent')
            parent = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            self.fds.append(parent)
            self.links.append((None, '/', parent, directory_stamp(os.fstat(parent))))
            for part in parts[1:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                dir_fd=parent)
                self.fds.append(child)
                self.links.append((parent, part, child, directory_stamp(os.fstat(child))))
                parent = child
            for _, _, fd, _ in self.links:
                info = os.fstat(fd)
                require(info.st_uid in (0, os.geteuid()) and not info.st_mode & 0o022,
                        'capture ancestors must be owned and protected')
            result = (ctypes.c_long * 32)()
            require(ctypes.CDLL(None, use_errno=True).fstatfs(parent, ctypes.byref(result)) == 0
                    and result[0] in (0xEF53, 0x58465342, 0x9123683E),
                    'capture requires native ext4/XFS/Btrfs disk, not RAM/network storage')
            space = os.fstatvfs(parent)
            require(space.f_bavail * space.f_frsize >= FREE_FLOOR + MAX_TOTAL,
                    'capture requires 256 MiB free floor plus total capture budget')
            os.mkdir(parts[-1], 0o700, dir_fd=parent)
            directory = os.open(parts[-1], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                dir_fd=parent)
            self.fds.append(directory)
            self.links.append((parent, parts[-1], directory, directory_stamp(os.fstat(directory))))
            self.directory = directory
            os.fsync(parent)  # Persist the new capture directory before SSH starts.
            self.fd = os.open('events.jsonl', os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                              os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=directory)
            self.initial = self.file_stamp()
            os.fsync(self.fd)
            os.fsync(directory)
        except BaseException:
            self.close()
            raise

    def file_stamp(self):
        info = os.fstat(self.fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and
                stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1,
                'unsafe capture log')
        return info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid, info.st_nlink

    def append(self, raw):
        require(not self.closed and self.file_stamp() == self.initial, 'capture log identity changed')
        link = os.stat('events.jsonl', dir_fd=self.directory, follow_symlinks=False)
        require((link.st_dev, link.st_ino) == self.initial[:2], 'capture log path replaced')
        for parent, name, fd, initial in self.links:
            require(directory_stamp(os.fstat(fd)) == initial and
                    directory_stamp(os.stat(name, dir_fd=parent, follow_symlinks=False)) == initial,
                    'capture ancestor changed')
        space = os.fstatvfs(self.directory)
        require(space.f_bavail * space.f_frsize >= FREE_FLOOR + len(raw),
                'capture free floor exhausted')
        view = memoryview(raw)
        while view:
            count = os.write(self.fd, view)
            require(0 < count <= len(view), 'capture write failed')
            view = view[count:]
        os.fsync(self.fd)
        os.fsync(self.directory)

    def close(self):
        if self.closed:
            return
        self.closed = True
        errors = []
        for fd in ([self.fd] if self.fd is not None else []) + list(reversed(self.fds)):
            try:
                os.close(fd)
            except OSError as error:
                errors.append(error)
        if errors:
            raise errors[0]


def capture(read_event, ack_stream, log):
    nonce, used = None, 0
    verified, outcome_seen = False, False
    for sequence in range(1, MAX_RECORDS + 1):
        raw = read_event(MAX_LINE, TIMEOUT)
        require(type(raw) is bytes and raw.endswith(b'\n') and len(raw) <= MAX_LINE,
                'invalid/truncated event')
        used += len(raw)
        require(used <= MAX_TOTAL, 'total capture budget exceeded')
        envelope = json.loads(raw)
        require(raw == canonical(envelope) + b'\n', 'noncanonical event')
        require(set(envelope) == {'schema', 'nonce', 'sequence', 'event_sha256', 'event'} and
                type(envelope['schema']) is int and envelope['schema'] == 1 and
                type(envelope['sequence']) is int and envelope['sequence'] == sequence and
                isinstance(envelope['nonce'], str) and re.fullmatch('[0-9a-f]{32}', envelope['nonce']),
                'invalid event session/sequence')
        nonce = nonce or envelope['nonce']
        require(envelope['nonce'] == nonce, 'event session changed')
        event = envelope['event']
        require(isinstance(event, dict) and event.get('hardware_acceptance') is False and
                envelope['event_sha256'] == sha(canonical(event)), 'event digest/acceptance mismatch')
        require(not outcome_seen, 'event after final outcome')
        if sequence == 1:
            require(event.get('event') == 'capture_ready', 'capture readiness event required first')
        if event.get('event') == 'transaction_verified':
            verified = True
        if event.get('event') == 'programmer_outcome':
            outcome_seen = True
            require(type(event.get('success')) is bool and type(event.get('close_success')) is bool,
                    'invalid final outcome')
            if event['success']:
                receipt = event.get('receipt', {})
                require(verified and event['close_success'] and receipt.get('success') is True and
                        receipt.get('final_verified') is True and receipt.get('hardware_acceptance') is False,
                        'success requires verified engine event and successful close')
        # Durable local evidence MUST precede ack, including capture_ready.
        log.append(raw)
        ack_stream.write(canonical(dict(nonce=nonce, sequence=sequence,
                                        event_sha256=envelope['event_sha256'])) + b'\n')
        ack_stream.flush()
        if outcome_seen:
            return event
    raise ValueError('capture record bound exhausted before final outcome')


def known_hosts_data(path):
    # Validate the exact held known_hosts bytes. The SSH child receives a private
    # FD path, not a pathname which could be exchanged after validation.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid in (0, os.geteuid()) and
                not info.st_mode & 0o022 and info.st_nlink == 1 and info.st_size <= 65536,
                'unsafe known_hosts file')
        raw = os.pread(fd, 65537, 0)
        require(len(raw) == info.st_size, 'short known_hosts read')
        matches = []
        for line in raw.decode('ascii').splitlines():
            fields = line.split()
            if not fields or fields[0].startswith('#'):
                continue
            # Dedicated single clear host key only; no wildcard/hash/CA fallback.
            require(len(fields) in (3, 4) and fields[0] == HOST and fields[1] == 'ssh-ed25519',
                    'known_hosts must contain only the exact known Ed25519 host')
            key = base64.b64decode(fields[2], validate=True)
            fingerprint = 'SHA256:' + base64.b64encode(hashlib.sha256(key).digest()).decode().rstrip('=')
            require(fingerprint == FINGERPRINT, 'A733 SSH fingerprint mismatch')
            matches.append(line)
        require(len(matches) == 1, 'exactly one known host key required')
        return (matches[0] + '\n').encode('ascii')
    finally:
        os.close(fd)


def remote_path(path):
    require(re.fullmatch(r'/run/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+', path) and
            all(part not in ('.', '..') for part in path.split('/')), 'invalid staged /run path')
    return path


def ssh_argv(args, known_fd):
    bootstrap = BOOTSTRAP + '\nrun_verified(sys.argv[1], ' + repr(PROGRAMMER_PIN) + ', ' + repr(PROGRAMMER_SIZE) + ', sys.argv[2:])\n'
    values = ['python3', '-I', '-B', '-c', bootstrap, remote_path(args.programmer), 'write',
              '--direction', args.direction, '--expected-boot-id', str(uuid.UUID(args.expected_boot_id)),
              '--helpers', remote_path(args.helpers), '--original', remote_path(args.original),
              '--candidate', remote_path(args.candidate), '--plan', remote_path(args.plan),
              '--plan-sha256', args.plan_sha256, '--recovery-proof', remote_path(args.recovery_proof)]
    restore_id = getattr(args, 'restore_id', None)
    restore_image = getattr(args, 'restore_image', None)
    require(args.direction == 'restore' or (restore_id is None and restore_image is None),
            'restore options are invalid for install')
    require(restore_id in (None, 'original', 'postboot-20261111', 'postwarm-20261111'),
            'unknown restore ID')
    require(restore_id in (None, 'original') or restore_image is not None,
            'calibrated restore requires held restore image')
    if restore_id is not None:
        values.extend(['--restore-id', restore_id])
    if restore_image is not None:
        values.extend(['--restore-image', remote_path(restore_image)])
    require(re.fullmatch('[0-9a-f]{64}', args.plan_sha256), 'invalid reviewed plan digest')
    return ['ssh', '-F', '/dev/null', '-T', '-i', '/home/drew/.ssh/id_ed25519',
            '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'HostKeyAlias=' + HOST,
            '-o', f'UserKnownHostsFile=/proc/{os.getpid()}/fd/{known_fd}',
            '-o', 'GlobalKnownHostsFile=/dev/null', '-o', 'UpdateHostKeys=no',
            '-o', 'ConnectTimeout=5', '-o', 'ServerAliveInterval=5', '-o', 'ServerAliveCountMax=2',
            'root@' + HOST, shlex.join(values)]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('output', 'known-hosts', 'programmer', 'helpers', 'original', 'candidate',
                 'plan', 'plan-sha256', 'recovery-proof', 'expected-boot-id'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--direction', choices=('install', 'restore'), default='install')
    parser.add_argument('--restore-id', choices=('original', 'postboot-20261111', 'postwarm-20261111'))
    parser.add_argument('--restore-image')
    args = parser.parse_args(argv)
    native_context()
    # Keep a sealed snapshot in this controller. SSH may close inherited FDs;
    # /proc/<controller>/fd names the held parent descriptor throughout capture.
    key_data = known_hosts_data(args.known_hosts)
    known_fd = os.memfd_create('known-a733-ssh-host', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
    log, child = None, None
    try:
        require(os.write(known_fd, key_data) == len(key_data), 'short known_hosts staging write')
        fcntl.fcntl(known_fd, fcntl.F_ADD_SEALS, fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW |
                    fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
        argv = ssh_argv(args, known_fd)
        log = NativeLog(args.output)
        child = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=None, bufsize=0)
        lines = BoundedLines(child.stdout)
        result = capture(lines, child.stdin, log)
        child.stdin.close()
        lines.require_eof(TIMEOUT)
        require(child.wait(timeout=TIMEOUT) == 0 and result['success'],
                'remote programmer failed or final success absent')
        print('Captured final outcome; hardware acceptance remains false.', flush=True)
    finally:
        if child is not None and child.poll() is None:
            # Closing stdin removes ACK authority; engine must stop before its
            # next mutation. Terminate SSH, never send a remote kill/reboot.
            child.stdin.close()
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
        if log is not None:
            log.close()
        os.close(known_fd)


if __name__ == '__main__':
    try:
        main()
    except (Exception, KeyboardInterrupt) as error:
        print(f'SPI capture refused/failed: {type(error).__name__}: {error}',
              file=sys.stderr, flush=True)
        sys.exit(1)
