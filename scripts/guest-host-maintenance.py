"""Installed-host maintenance custody. No CLI, recovery mode or implicit reopen.

Borrow controller/context only while this object retains global exclusion. Call
recheck before each mutation and finish_success only after durable work finishes.
RAM recovery deliberately needs a separate offline authority.
"""
import copy
from contextlib import ExitStack
import errno
import importlib.util
import os
from pathlib import Path
import stat
import subprocess
import threading
import time

BASE = Path(__file__).resolve().parents[1]


def module(name, relative):
    spec = importlib.util.spec_from_file_location(name, BASE / relative)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


CP = module('host_maintenance_checkpoint', 'scripts/managed-guest-checkpoint.py')
LIFECYCLE = CP.LIFECYCLE
CONTEXT = LIFECYCLE.CONTEXT
ADMISSION = LIFECYCLE.ADMISSION
NORMAL = module('host_maintenance_normal', 'scripts/guest-operation-admission.py')
require = ADMISSION.require
PID_LIMIT = 65536
FD_LIMIT = 262144
ENVIRONMENT = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'}


class MaintenanceError(RuntimeError):
    def __init__(self, receipt, cause):
        self.receipt = copy.deepcopy(receipt)
        self.cause = cause
        if hasattr(cause, 'receipt'):
            self.receipt['nested_error'] = copy.deepcopy(cause.receipt)
        if isinstance(cause, ADMISSION.PublicationError):
            self.receipt['publication_error'] = dict(stage=cause.stage, uncertain=cause.uncertain)
        super().__init__(f"{receipt['phase']}: {type(cause).__name__}: {cause}")


def stop_managed():
    """PID1 stops only managed supervisors; output never accumulates in RAM."""
    result = subprocess.run(
        ['/usr/bin/systemctl', 'stop', 'a733-managed-*.service'],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=ENVIRONMENT.copy(), timeout=420, check=False)
    require(result.returncode == 0, 'managed supervisor stop failed')


def consumer_check():
    """Bounded initial-namespace task/FD census; permission errors refuse.

    Task tables include threads with distinct FD tables. KVM names follow the
    pinned managed fixture census; character-device aliases also refuse. Only a
    disappearing process/descriptor (ENOENT/ESRCH) is a permissible scan race.
    """
    tasks = descriptors = 0
    deadline = time.monotonic() + 10
    def bounded():
        require(time.monotonic() <= deadline, "KVM consumer census timeout")
    def gone(error):
        if error.errno not in (errno.ENOENT, errno.ESRCH):
            raise error
    with os.scandir('/proc') as processes:
        count = 0
        for process in processes:
            bounded()
            if not process.name.isdecimal():
                continue
            count += 1
            require(count <= PID_LIMIT, 'process census limit exceeded')
            try:
                with os.scandir(process.path + '/task') as threads:
                    for thread in threads:
                        bounded()
                        require(thread.name.isdecimal(), 'invalid task census entry')
                        tasks += 1
                        require(tasks <= PID_LIMIT, 'task census limit exceeded')
                        try:
                            with os.scandir(thread.path + '/fd') as files:
                                for entry in files:
                                    bounded()
                                    descriptors += 1
                                    require(descriptors <= FD_LIMIT, 'descriptor census limit exceeded')
                                    require(entry.name.isdecimal(), 'invalid descriptor census entry')
                                    try:
                                        target = os.readlink(entry.path)
                                        require(not (target.startswith('/dev/kvm') or
                                                (target.startswith('anon_inode:') and 'kvm' in target)),
                                                'actual KVM consumer remains')
                                        info = os.stat(entry.path)
                                        require(not (stat.S_ISCHR(info.st_mode) and
                                                info.st_rdev == os.makedev(10, 232)),
                                                'actual KVM device consumer remains')
                                    except OSError as error:
                                        gone(error)
                        except OSError as error:
                            gone(error)
            except OSError as error:
                gone(error)


def registered_storage():
    """Discover only the protected storage tuple; no lease or guest authority.

    InstalledMaintenance authenticates this tuple against physical STATE/p5
    after exclusion, and reads the latest registry itself. Never retain guest
    entries from this read-only observation across maintenance acquisition.
    """
    require(os.getresuid() == (0, 0, 0), 'actual root required for storage discovery')
    boot = CONTEXT.read_system('/proc/sys/kernel/random/boot_id', 64).decode('ascii')
    require(boot.endswith('\n'), 'invalid host boot UUID')
    boot_id = ADMISSION._uuid(boot[:-1])
    CONTEXT.host_identity(boot_id)
    CONTEXT.MANAGEMENT.runtime_guard()
    held = CONTEXT.ENROLL.HeldPath('/run/a733-state/guests/autostart.json')
    try:
        info = held.info
        CONTEXT.ENROLL.regular(info, 0o400)
        require(info.st_dev == os.stat('/run/a733-state').st_dev,
                'managed registry is not on selected STATE')
        require(0 < info.st_size <= CONTEXT.REGISTRY.LIMIT, 'invalid registry size')
        data = os.read(held.fd, CONTEXT.REGISTRY.LIMIT + 1)
        held.recheck()
        require(len(data) == info.st_size, 'managed registry size changed')
        value = CONTEXT.REGISTRY.validate_registry(data)
        require(CONTEXT.REGISTRY.canonical(value) == data, 'noncanonical managed registry')
        CONTEXT.host_identity(boot_id)
        CONTEXT.MANAGEMENT.runtime_guard()
        held.recheck()
        require(info.st_dev == os.stat('/run/a733-state').st_dev,
                'managed registry STATE placement changed')
        return copy.deepcopy(value['storage'])
    finally:
        held.close()


class InstalledMaintenance:
    """Own admission, nonce, EX lease and physical STATE controller, in that order.

    Failure and ordinary close leave the closure in place. A successful explicit
    finish opens admission and permanently prohibits further use for mutation.
    """
    def __init__(self, expected_storage):
        self._pid, self._thread = os.getpid(), threading.get_ident()
        self._closed = self._finished = False
        self.admission = self.owner = self.lease = self.controller = None
        self.receipt = dict(phase='validate', success=False, hardware_acceptance=False,
                            nonce=None, open_attempted=False, open_acknowledged=False,
                            gate_state='unobserved', close_errors=[])
        try:
            self._creator()
            storage = CONTEXT.validate_storage(expected_storage)
            boot = CONTEXT.read_system('/proc/sys/kernel/random/boot_id', 64).decode('ascii')
            require(boot.endswith('\n'), 'invalid host boot UUID')
            self.boot_id = ADMISSION._uuid(boot[:-1])
            self._native()
            require(NORMAL.manager_state() in ('starting', 'running', 'degraded'),
                    'PID1 does not permit maintenance')
            self.receipt['phase'] = 'open-existing'
            self.admission = ADMISSION.Admission.open_existing(
                self.boot_id, NORMAL.local_kvm_gid(), NORMAL.manager_state)
            self.receipt['phase'] = 'close-gate'
            self.owner = self.admission.close_gate('maintenance')
            require(type(self.owner) is ADMISSION.ClosureOwner, 'already closed gate requires recovery')
            self.receipt.update(nonce=self.owner.nonce, gate_state='closed')
            self.receipt['phase'] = 'stop-managed'
            stop_managed()
            self.receipt['phase'] = 'exclusive-lease'
            self.lease = self.owner.exclusive(timeout=0)
            self.receipt['phase'] = 'physical-state'
            self.controller = LIFECYCLE.StateController(storage, self.lease, boot_id=self.boot_id)
            self.recheck()
            self.receipt['phase'] = 'entry-consumers'
            consumer_check()
            self.receipt['phase'] = 'entry-quiescence'
            self._quiescent()
            self.receipt['phase'] = 'maintenance'
        except BaseException as error:
            self._fail(error)

    @property
    def context(self):
        self.recheck()
        return self.controller.context

    def _creator(self):
        require((os.getpid(), threading.get_ident()) == (self._pid, self._thread),
                'maintenance belongs to another process or thread')
        require(os.getresuid() == (0, 0, 0), 'actual root required for maintenance')

    def _native(self):
        CONTEXT.host_identity(self.boot_id)
        NORMAL.CONTEXT.MANAGEMENT.runtime_guard()

    def recheck(self):
        self._creator()
        require(not self._closed and not self._finished, 'maintenance closed or finished')
        self._native()
        require(type(self.owner) is ADMISSION.ClosureOwner and
                self.owner.nonce == self.receipt['nonce'] and self.owner.reason == 'maintenance',
                'maintenance ownership differs')
        # Atomic protected gate bytes suffice for observation. Do not acquire
        # gate EX from a per-guest/disk/registry guard deeper in the lock order.
        self.owner._matching()
        require(type(self.lease) is ADMISSION.Lease and self.lease.exclusive and
                self.owner.lease is self.lease, 'actual exclusive maintenance lease required')
        self.lease.recheck()
        ADMISSION._held_lock(self.lease.fd, 'WRITE', self._pid)
        require(type(self.controller) is LIFECYCLE.StateController,
                'actual same-family state controller required')
        self.controller.context.guard()

    def _registry(self):
        with ExitStack() as stack:
            lock = stack.enter_context(self.admission.registry_lock(self.lease))
            def guard():
                self.recheck()
                lock.recheck()
            _, _, registry = self.controller._registry(stack, guard)
            require(registry is not None, 'prepared managed registry required')
            return registry

    def _head(self, name):
        with ExitStack() as stack:
            lock = stack.enter_context(self.admission.guest_lock(name, self.lease, create=False))
            def guard():
                self.recheck()
                lock.recheck()
            store = self.controller._head_store(stack, name, guard)
            head = LIFECYCLE.HEAD.validate_head(store.read('head.json', LIFECYCLE.HEAD.LIMIT))
            self.controller._storage(head)
            return head

    def _ready(self, name):
        # The fresh registry pin is read again after guest and stopped-disk EX.
        registry = self._registry()
        entries = [e for e in registry['guests'] if e['name'] == name]
        require(len(entries) == 1, 'registered guest disappeared')
        pin = entries[0]['manifest_sha256']
        with ExitStack() as stack:
            lock = stack.enter_context(self.admission.guest_lock(name, self.lease, create=False))
            inspection = stack.enter_context(LIFECYCLE.POLICY.Inspection(name, pin))
            def guard():
                self.recheck()
                lock.recheck()
                self.controller._inspection(inspection, name, pin)
                require(self.admission.is_managed(name, self.lease, lock), 'managed marker missing')
            guard()
            identity = self.controller._inspection(inspection, name, pin)
            store = self.controller._head_store(stack, name, guard)
            raw = store.read('head.json', LIFECYCLE.HEAD.LIMIT)
            head = LIFECYCLE.HEAD.validate_head(raw)
            self.controller._storage(head)
            require(head['status'] == 'ready' and head['manifest_sha256'] == pin and
                    all(head[k] == v for k, v in identity.items()), 'ready head identity differs')
            registry_lock = stack.enter_context(self.admission.registry_lock(self.lease))
            def registry_guard():
                guard()
                registry_lock.recheck()
            registry_store, registry_raw, current = self.controller._registry(stack, registry_guard)
            require(current is not None, 'registry disappeared')
            fresh = [e for e in current['guests'] if e['name'] == name]
            require(len(fresh) == 1 and fresh[0]['manifest_sha256'] == pin and
                    all(fresh[0][k] == v for k, v in identity.items()), 'registry identity differs')
            if head['sequence'] > 0:
                CP.authenticate_ready_cohort(self.controller, name, guest_lock=lock,
                    inspection=inspection, head_store=store, head_data=raw,
                    registry=(registry_store, registry_raw, current))
            registry_guard()

    def _quiescent(self):
        self.recheck()
        names = sorted(e['name'] for e in self._registry()['guests'])
        for name in names:
            head = self._head(name)
            require(head['status'] != 'quarantined', 'quarantined guest requires recovery')
            if head['status'] in ('admitted', 'running', 'stopping', 'checkpoint'):
                CP.reconcile(self.controller, name)
            else:
                require(head['status'] == 'ready', 'unknown guest lifecycle state')
            self._ready(name)
        require(sorted(e['name'] for e in self._registry()['guests']) == names,
                'registered guest set changed')
        self.recheck()

    def _fail(self, error):
        if self.receipt['open_attempted']:
            self.receipt['gate_state'] = 'uncertain'
        try:
            self.close()
        except BaseException as cleanup:
            self.receipt['cleanup_error'] = repr(cleanup)
        raise MaintenanceError(self.receipt, error) from error

    def finish_success(self):
        self._creator()
        if self._finished:
            # Preserve the completed transaction's acknowledged receipt. A
            # repeated API call must not relabel it or run failure cleanup.
            raise MaintenanceError(self.receipt, ADMISSION.AdmissionError('maintenance already finished'))
        try:
            self.recheck()
            self.receipt['phase'] = 'final-quiescence'
            self._quiescent()
            self.receipt['phase'] = 'final-consumers'
            consumer_check()
            self.recheck()
            self.receipt.update(phase='open-gate', open_attempted=True)
            # No persistent mutation may follow this commit boundary.
            self.owner.open()
            self._finished = True
            self.receipt.update(phase='finished', success=True,
                                open_acknowledged=True, gate_state='open')
            return copy.deepcopy(self.receipt)
        except BaseException as error:
            self._fail(error)

    def close(self):
        self._creator()
        if self._closed:
            return
        self._closed = True
        errors = []
        for field in ('controller', 'lease', 'admission'):
            item = getattr(self, field)
            setattr(self, field, None)
            if item is not None:
                try:
                    item.close()
                except BaseException as error:
                    errors.append(error)
                    self.receipt['close_errors'].append(dict(resource=field, error=repr(error)))
        if errors:
            raise MaintenanceError(self.receipt, errors[0]) from errors[0]

    def __enter__(self):
        self._creator()
        try:
            self.recheck()
            return self
        except BaseException as error:
            self._fail(error)

    def __exit__(self, *unused):
        self.close()
