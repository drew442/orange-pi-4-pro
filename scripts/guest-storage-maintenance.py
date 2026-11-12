"""Installed pre-p5 maintenance custody; no mount or target authorization.

The caller supplies protected-registry storage matched to its reviewed activation
plan. It retains this custody while performing the existing guarded activation
transaction, closes every target/subsystem lock, then calls bind_storage().
Binding authenticates actual mounted storage and stopped guests. Only a bound
transaction may explicitly reopen admission. Fresh bootstrap uses its original
owner through activate-guest-storage.apply_bootstrap instead.
"""
import copy
import importlib.util
import os
from pathlib import Path
import threading

_SPEC = importlib.util.spec_from_file_location(
    'storage_maintenance_installed', Path(__file__).with_name('guest-host-maintenance.py'))
M = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(M)
ADMISSION, CONTEXT, LIFECYCLE = M.ADMISSION, M.CONTEXT, M.LIFECYCLE
require = M.require
MaintenanceError = M.MaintenanceError


class StorageMaintenance(M.InstalledMaintenance):
    """Own fresh maintenance closure/EX before p5, bind once after activation.

    No admission runtime is initialized or adopted. Borrowed owner/lease and,
    after binding, controller/context must never be closed or converted by the
    caller. recheck() authenticates custody before each activation mutation.
    """
    def __init__(self, expected_storage):
        # InstalledMaintenance.__init__ intentionally requires p5 at acquisition.
        self._pid, self._thread = os.getpid(), threading.get_ident()
        self._closed = self._finished = self._binding_started = self._bound = False
        self.admission = self.owner = self.lease = self.controller = None
        self.receipt = dict(phase='validate', success=False, hardware_acceptance=False,
                            nonce=None, open_attempted=False, open_acknowledged=False,
                            gate_state='unobserved', close_errors=[], storage_bound=False)
        try:
            self._creator()
            self._storage = copy.deepcopy(CONTEXT.validate_storage(expected_storage))
            boot = CONTEXT.read_system('/proc/sys/kernel/random/boot_id', 64).decode('ascii')
            require(boot.endswith('\n'), 'invalid host boot UUID')
            self.boot_id = ADMISSION._uuid(boot[:-1])
            self._native()
            require(M.NORMAL.manager_state() in ('starting', 'running', 'degraded'),
                    'PID1 does not permit maintenance')
            self.receipt['phase'] = 'open-existing'
            self.admission = ADMISSION.Admission.open_existing(
                self.boot_id, M.NORMAL.local_kvm_gid(), M.NORMAL.manager_state)
            self.receipt['phase'] = 'close-gate'
            self.owner = self.admission.close_gate('maintenance')
            require(type(self.owner) is ADMISSION.ClosureOwner,
                    'already closed gate requires recovery')
            self.receipt.update(nonce=self.owner.nonce, gate_state='closed')
            self.receipt['phase'] = 'stop-managed'
            M.stop_managed()
            self.receipt['phase'] = 'exclusive-lease'
            self.lease = self.owner.exclusive(timeout=0)
            self.recheck()
            self.receipt['phase'] = 'entry-consumers'
            M.consumer_check()
            self.recheck()
            self.receipt['phase'] = 'unbound'
        except BaseException as error:
            self._fail(error)

    def _native(self):
        super()._native()
        # The installed runtime guard authenticates root, STATE, block topology,
        # marker/blkid and management binds without requiring guest storage.
        # Bind that selected STATE to the caller's registry tuple as well.
        management = CONTEXT.MANAGEMENT
        _, state = management.validate_mounts(
            management.mount_table(Path('/proc/self/mountinfo').read_text()))
        identity = management.block_identity(state)
        marker = management.read_secure(management.STATE / '.a733-state', 128)
        require(marker == ('a733-state-v1\n' + self._storage['state_uuid'] + '\n').encode('ascii'),
                'selected STATE UUID differs')
        node = (Path('/sys/dev/block') / identity).resolve(strict=True)
        serial = CONTEXT.read_system(
            '/sys/class/block/' + node.parent.name + '/device/serial', 128)
        require(serial.decode('ascii').strip() == self._storage['nvme_serial'],
                'selected STATE NVMe serial differs')

    def recheck(self):
        self._creator()
        require(not self._closed and not self._finished, 'maintenance closed or finished')
        self._native()
        require(type(self.owner) is ADMISSION.ClosureOwner and
                self.owner.nonce == self.receipt['nonce'] and self.owner.reason == 'maintenance',
                'maintenance ownership differs')
        self.owner._matching()
        require(type(self.lease) is ADMISSION.Lease and self.lease.exclusive and
                self.owner.lease is self.lease, 'actual exclusive maintenance lease required')
        self.lease.recheck()
        ADMISSION._held_lock(self.lease.fd, 'WRITE', self._pid)
        if self._binding_started:
            require(type(self.controller) is LIFECYCLE.StateController,
                    'actual same-family state controller required')
            self.controller.context.guard()
            require(self.controller.context.storage == self._storage,
                    'bound storage differs')
        else:
            require(self.controller is None, 'unexpected unbound controller')

    @property
    def context(self):
        self.recheck()
        require(self._bound, 'storage not bound')
        return self.controller.context

    def bind_storage(self):
        """Bind once after guarded activation and all target lock cleanup.

        This method neither mounts p5 nor authorizes its target. Lifecycle
        reconciliation is the existing installed maintenance proof machinery.
        """
        self._creator()
        if self._finished:
            raise MaintenanceError(self.receipt, ADMISSION.AdmissionError('maintenance already finished'))
        try:
            self.recheck()
            require(not self._binding_started, 'storage binding already attempted')
            self._binding_started = True
            self.receipt['phase'] = 'bind-storage'
            self.controller = LIFECYCLE.StateController(
                self._storage, self.lease, boot_id=self.boot_id)
            self.recheck()
            self.receipt['phase'] = 'bound-quiescence'
            self._quiescent()
            self.receipt['phase'] = 'bound-consumers'
            M.consumer_check()
            self.recheck()
            self._bound = True
            self.receipt.update(phase='maintenance', storage_bound=True)
        except BaseException as error:
            self._fail(error)

    def finish_success(self):
        self._creator()
        if not self._bound:
            self._fail(ADMISSION.AdmissionError('storage not bound'))
        return super().finish_success()
