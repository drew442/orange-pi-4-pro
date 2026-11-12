"""Root-only normal guest operation; no bootstrap or managed authority.

Own one shared admission lease and stable guest exclusion through the operation.
Runtime preparation must already have completed. This authenticates admission,
not guest storage, manifest pins, clean shutdown or hardware acceptance.
"""
import contextlib
import importlib.util
import os
from pathlib import Path
import select
import subprocess
import threading
import time

BASE = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    'manual_operation_context', BASE / 'scripts/guest-lifecycle-context.py')
CONTEXT = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(CONTEXT)
ADMISSION = CONTEXT.ADMISSION
ACCOUNTS = CONTEXT.POLICY.ACCOUNTS
require = ADMISSION.require
# Legacy callers import this helper under distinct names. Share the nesting
# boundary through their authenticated canonical admission module.
_ACTIVE = ADMISSION.__dict__.setdefault('_manual_guest_operation_local', threading.local())


def manager_state():
    """Query native PID1 with fixed argv/environment, bounded bytes and deadline."""
    process = subprocess.Popen(
        ['/usr/bin/systemctl', 'show', '--property=SystemState', '--value'],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'})
    data, deadline = b'', time.monotonic() + 10
    try:
        while True:
            remaining = deadline - time.monotonic()
            require(remaining > 0 and select.select([process.stdout], [], [], remaining)[0],
                    'PID1 SystemState query timeout')
            piece = os.read(process.stdout.fileno(), 65 - len(data))
            if not piece:
                break
            data += piece
            require(len(data) <= 64, 'oversized PID1 SystemState')
        code = process.wait(timeout=max(0.01, deadline - time.monotonic()))
        require(code == 0 and data.endswith(b'\n') and data.count(b'\n') == 1,
                'invalid PID1 SystemState response')
        return data[:-1].decode('ascii')
    finally:
        try:
            if process.poll() is None:
                process.kill()
        finally:
            try:
                process.wait(timeout=1)
            finally:
                process.stdout.close()


def local_kvm_gid():
    # snapshot authenticates bounded protected local files on the guarded /etc
    # overlay. Never consult NSS (which may be mutable or remote).
    groups = ACCOUNTS.parse_database(ACCOUNTS.snapshot()['group'], 4, 2)
    require('kvm' in groups and int(groups['kvm'][2]) > 0, 'local KVM group required')
    return int(groups['kvm'][2])


class _ManualGuests:
    """Retain root-only admission and guest EX; exclude every managed name.

    Borrowed lease/guest_lock objects belong to this operation and must not be
    closed, unlocked or converted by callers. No nested lease acquisition.
    """
    def _initialize(self, names, *, create=False):
        self._stack = contextlib.ExitStack()
        self._closed = False
        self._pid, self._thread = os.getpid(), threading.get_ident()
        self._lease = None
        self._guest_locks = ()
        self._active = False
        try:
            self._root()
            require(type(names) in (list, tuple), 'guest names must be a list or tuple')
            names = tuple(names)
            require(1 <= len(names) <= 16, 'one to sixteen guest names required')
            for name in names:
                ADMISSION._guest_name(name)
            require(len(set(names)) == len(names), 'duplicate guest name')
            self._names = tuple(sorted(names))
            require(type(create) is bool, 'create must be boolean')
            require(not getattr(_ACTIVE, 'operation', None), 'nested guest operation refused')
            _ACTIVE.operation = self
            self._active = True
            boot = CONTEXT.read_system('/proc/sys/kernel/random/boot_id', 64).decode('ascii')
            require(boot.endswith('\n'), 'invalid host boot UUID')
            self.boot_id = ADMISSION._uuid(boot[:-1])
            CONTEXT.host_identity(self.boot_id)
            CONTEXT.MANAGEMENT.runtime_guard()
            self.admission = ADMISSION.Admission.open_existing(
                self.boot_id, local_kvm_gid(), manager_state)
            self._stack.callback(self.admission.close)
            self._lease = self.admission.admit()
            self._stack.callback(self._lease.close)
            for name in self._names:
                lock = self.admission.guest_lock(name, self._lease, create=create)
                self._stack.callback(lock.close)
                self._guest_locks += (lock,)
            self.recheck()
        except BaseException:
            self.close()
            raise

    def _root(self):
        require(os.getresuid() == (0, 0, 0), 'actual root required for manual guest operation')
        require((os.getpid(), threading.get_ident()) == (self._pid, self._thread),
                'guest operation belongs to another process or thread')

    @property
    def lease(self):
        self.recheck()
        return self._lease

    @property
    def names(self):
        return self._names

    @property
    def guest_locks(self):
        self.recheck()
        return self._guest_locks

    def recheck(self):
        require(not self._closed, 'guest operation closed')
        self._root()
        CONTEXT.host_identity(self.boot_id)
        CONTEXT.MANAGEMENT.runtime_guard()
        self._lease.recheck()
        require(type(self._lease) is ADMISSION.Lease and not self._lease.exclusive,
                'normal shared lease required')
        ADMISSION._held_lock(self._lease.fd, 'READ', self._pid)
        for name, lock in zip(self._names, self._guest_locks):
            lock.recheck()
            ADMISSION._held_lock(lock.fd, 'WRITE', self._pid)
            require(not self.admission.is_managed(name, self._lease, lock),
                    'managed guest requires lifecycle operation')
        # Deliberately do not reread gate state or query SystemState: callers
        # admitted before maintenance/shutdown closure must be able to finish.

    def close(self):
        if self._closed:
            return
        self._root()
        self._closed = True
        try:
            self._stack.close()  # All callbacks run; failed close numbers never retry.
        finally:
            if self._active:
                _ACTIVE.operation = None
                self._active = False

    def __enter__(self):
        try:
            self.recheck()
            return self
        except BaseException:
            self.close()
            raise

    def __exit__(self, *unused):
        self.close()


class ManualGuestOperation(_ManualGuests):
    """Own one normal lease and guest lock; preserve the single-guest API."""
    def __init__(self, name, *, create=False):
        self.name = name
        self._initialize((name,), create=create)

    @property
    def _guest_lock(self):
        return self._guest_locks[0]

    @property
    def guest_lock(self):
        self.recheck()
        return self._guest_lock


class ManualGuestBatch(_ManualGuests):
    """Own one normal lease and distinct guest locks in canonical sorted order."""
    def __init__(self, names, *, create=False):
        self._initialize(names, create=create)
