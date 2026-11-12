#!/usr/bin/python3
"""Linux direct-child and QMP peer witness, without lifecycle authority.

The caller must be the root supervisor in the initial PID/user/mount namespaces,
with trusted permit provenance and exclusive child wait ownership. Namespace
comparisons below establish equality with that supervisor, not initiality.
This helper neither launches nor signals anything. Clean raw exit alone never
approves shutdown/checkpoint: correlated QMP, fixture and durable intent evidence
remain external requirements. /proc and the pinned executable path must be in
the trusted supervisor mount namespace. No descriptor is transferred to callers.
"""
import hashlib
import importlib.util
import os
from pathlib import Path
import re
import socket
import stat
import struct

_SPEC = importlib.util.spec_from_file_location(
    'process_run_permit', Path(__file__).resolve().parents[1] / 'configs/guest_run_permit.py')
PERMIT = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(PERMIT)
PROC_LIMIT = 65536
HASH_CHUNK = 1024 * 1024


class WitnessError(ValueError):
    """Missing, conflicting or stale process evidence; fail closed."""


def require(condition, message):
    if not condition:
        raise WitnessError(message)


def _integer(value, label):
    require(type(value) is int and 0 < value <= (1 << 63) - 1, 'invalid ' + label)
    return value


def _decimal(text):
    require(bool(re.fullmatch(rb'[0-9]{1,20}', text)), 'invalid proc integer')
    return int(text)


def parse_stat(data):
    """Parse stat despite arbitrary comm whitespace, parentheses and newlines."""
    require(type(data) is bytes and 0 < len(data) <= PROC_LIMIT, 'invalid stat size')
    opening, closing = data.find(b' ('), data.rfind(b') ')
    require(opening > 0 and closing > opening, 'invalid stat framing')
    pid = _decimal(data[:opening])
    fields = data[closing + 2:].split()
    require(len(fields) >= 20 and len(fields[0]) == 1, 'truncated stat')
    return {'pid': pid, 'state': fields[0], 'ppid': _decimal(fields[1]),
            'start_ticks': _decimal(fields[19])}


def parse_status(data):
    require(type(data) is bytes and 0 < len(data) <= PROC_LIMIT, 'invalid status size')
    wanted = {b'PPid', b'Uid', b'Gid', b'Groups', b'CapInh', b'CapPrm',
              b'CapEff', b'CapBnd', b'CapAmb', b'NoNewPrivs'}
    result = {}
    for line in data.splitlines():
        key, colon, value = line.partition(b':')
        if key not in wanted:
            continue
        require(colon and key not in result, 'duplicate status field')
        values = value.split()
        if key.startswith(b'Cap'):
            require(len(values) == 1 and bool(re.fullmatch(rb'[0-9a-fA-F]{1,16}', values[0])),
                    'invalid capability field')
            result[key] = int(values[0], 16)
        else:
            expected = 4 if key in (b'Uid', b'Gid') else None if key == b'Groups' else 1
            require(expected is None or len(values) == expected, 'invalid credential field')
            result[key] = tuple(_decimal(item) for item in values)
    require(set(result) == wanted, 'missing status field')
    return result


def _read(pid, leaf):
    # Only internal literal leaves and validated integer PIDs reach procfs.
    _integer(pid, 'PID')
    require(leaf in ('stat', 'status'), 'invalid proc leaf')
    with open(f'/proc/{pid}/{leaf}', 'rb', buffering=0) as stream:
        data = stream.read(PROC_LIMIT + 1)
    require(len(data) <= PROC_LIMIT, 'proc size limit exceeded')
    return data


def _identity(pid):
    result = parse_stat(_read(pid, 'stat'))
    require(result['pid'] == pid, 'proc PID mismatch')
    return result


def _metadata(info):
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == 0
            and not info.st_mode & 0o022, 'unsafe QEMU executable metadata')
    return (info.st_dev, info.st_ino, info.st_size, info.st_mode, info.st_uid,
            info.st_gid, info.st_nlink, info.st_mtime_ns, info.st_ctime_ns)


class ChildWitness:
    """One directly forked child; caller exclusively owns its raw wait status."""
    def __init__(self, child_pid, start_ticks, permit):
        _integer(child_pid, 'child PID')
        _integer(start_ticks, 'start ticks')
        # Revalidate and detach even an allegedly parsed caller-owned dict.
        self._permit = PERMIT.validate_permit(PERMIT.canonical(permit))
        self.pid, self.start_ticks = child_pid, start_ticks
        self._parent = os.getpid()
        self._parent_ticks = _identity(self._parent)['start_ticks']
        require(self._permit['parent'] == {'pid': self._parent, 'start_ticks': self._parent_ticks},
                'permit parent mismatch')
        require(self._permit['child'] == {'pid': child_pid, 'start_ticks': start_ticks},
                'permit child mismatch')
        self._status = None
        self._wait_failed = False

    def _active(self):
        require(self._status is None and not self._wait_failed, 'child witness closed')
        require(os.getpid() == self._parent and
                _identity(self._parent)['start_ticks'] == self._parent_ticks,
                'supervisor identity changed')

    def _child(self):
        self._active()
        identity = _identity(self.pid)
        require(identity['start_ticks'] == self.start_ticks and
                identity['ppid'] == self._parent and identity['state'] not in (b'Z', b'X', b'x'),
                'child identity changed or exited')
        return identity

    def _credentials(self):
        values = parse_status(_read(self.pid, 'status'))
        config = self._permit['config']
        require(values[b'PPid'] == (self._parent,), 'child parent changed')
        require(values[b'Uid'] == (config['uid'],) * 4 and
                values[b'Gid'] == (config['gid'],) * 4, 'child credentials mismatch')
        groups = values[b'Groups']
        require(groups == (self._permit['kvm_gid'],),
                'child groups mismatch')
        require(all(values[key] == 0 for key in
                    (b'CapInh', b'CapPrm', b'CapEff', b'CapBnd', b'CapAmb')),
                'child retains capabilities')
        require(values[b'NoNewPrivs'] == (1,), 'child lacks no-new-privileges')
        for name in ('pid', 'user', 'mnt'):
            supervisor = os.stat(f'/proc/{self._parent}/ns/{name}')
            child = os.stat(f'/proc/{self.pid}/ns/{name}')
            require((supervisor.st_dev, supervisor.st_ino) == (child.st_dev, child.st_ino),
                    'child namespace mismatch')

    def validate_child(self):
        """Authenticate live child, credentials, namespaces and pinned exec bytes.

        Hold the actual exe and named binary FDs throughout hashing. Debian's
        versioned QEMU symlink is permitted: its resolved inode must be identical
        to the child's executable and satisfy safe regular-file metadata.
        """
        before = self._child()
        self._credentials()
        flags = os.O_RDONLY | os.O_CLOEXEC
        with os.fdopen(os.open(f'/proc/{self.pid}/exe', flags), 'rb', buffering=0) as executable:
            with os.fdopen(os.open(PERMIT.QEMU_PATH, flags), 'rb', buffering=0) as named:
                info = _metadata(os.fstat(executable.fileno()))
                require(info == _metadata(os.fstat(named.fileno())), 'named QEMU inode mismatch')
                pin = self._permit['qemu']
                require(info[2] == pin['bytes'], 'QEMU size mismatch')
                digest = hashlib.sha256()
                remaining = pin['bytes']
                while remaining:
                    chunk = executable.read(min(HASH_CHUNK, remaining))
                    require(bool(chunk), 'QEMU executable truncated')
                    digest.update(chunk)
                    remaining -= len(chunk)
                require(not executable.read(1), 'QEMU executable grew')
                require(digest.hexdigest() == pin['sha256'], 'QEMU digest mismatch')
                require(info == _metadata(os.fstat(executable.fileno())) ==
                        _metadata(os.fstat(named.fileno())) == _metadata(os.stat(PERMIT.QEMU_PATH)) ==
                        _metadata(os.stat(f'/proc/{self.pid}/exe')), 'QEMU executable changed')
                self._credentials()
                after = self._child()
                require(all(after[key] == before[key] for key in ('pid', 'ppid', 'start_ticks')),
                        'child identity changed during verification')
        return {'pid': self.pid, 'start_ticks': self.start_ticks,
                'qemu_sha256': pin['sha256'], 'qemu_bytes': pin['bytes']}

    def verify_qmp_peer(self, connection):
        """Bind a connected UNIX stream peer before QMP parsing/admission."""
        self._active()
        require(isinstance(connection, socket.socket) and connection.family == socket.AF_UNIX,
                'QMP requires UNIX socket')
        require(connection.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) == socket.SOCK_STREAM,
                'QMP requires stream socket')
        connection.getpeername()  # Fails for unconnected/listening sockets.
        peer = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED,
                                                        struct.calcsize('3i')))
        config = self._permit['config']
        require(peer == (self.pid, config['uid'], config['gid']), 'QMP peer mismatch')
        return self.validate_child()

    def reap(self, nonblocking=True):
        """Return exact raw direct-child wait status, or None if still running.

        No shell/subprocess return-code translation; ECHILD is evidence loss.
        An interrupted wait is left to the caller; never retry recycled FDs/PIDs.
        """
        require(type(nonblocking) is bool, 'nonblocking must be boolean')
        self._active()
        try:
            pid, status = os.waitpid(self.pid, os.WNOHANG if nonblocking else 0)
        except ChildProcessError as exc:
            self._wait_failed = True
            raise WitnessError('direct child wait evidence unavailable') from exc
        if pid == 0:
            return None
        require(pid == self.pid and (os.WIFEXITED(status) or os.WIFSIGNALED(status)),
                'unexpected direct-child wait result')
        self._status = status
        return status

    @property
    def raw_status(self):
        return self._status

    @property
    def normal_zero(self):
        return (self._status is not None and os.WIFEXITED(self._status)
                and os.WEXITSTATUS(self._status) == 0)
