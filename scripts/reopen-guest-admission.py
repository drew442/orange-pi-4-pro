#!/usr/bin/env python3
"""Explicit installed-host recovery of one failed maintenance closure.

This operation authenticates guest readiness and reopens admission. It never
retries the host transaction that failed, initializes runtime, or adopts a
bootstrap/shutdown closure. Physical deployment acceptance remains separate.
"""
import argparse
import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import threading


SPEC = importlib.util.spec_from_file_location(
    'reopen_host_maintenance', Path(__file__).with_name('guest-host-maintenance.py'))
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
A = M.ADMISSION


class ReopenMaintenance(M.InstalledMaintenance):
    """Retain the exact existing closure and native exclusion until finish.

    Only construction changes from installed maintenance: the protected existing
    record must match an explicit nonce before any stop request. The inherited
    controller, quiescence, final census, publication and cleanup rules apply.
    """
    def __init__(self, expected_nonce):
        self._pid, self._thread = os.getpid(), threading.get_ident()
        self._closed = self._finished = False
        self.admission = self.owner = self.lease = self.controller = None
        self.receipt = dict(phase='validate', success=False, hardware_acceptance=False,
                            nonce=None, open_attempted=False, open_acknowledged=False,
                            gate_state='unobserved', close_errors=[])
        try:
            self._creator()
            nonce = A._uuid(expected_nonce)
            storage = M.CONTEXT.validate_storage(M.registered_storage())
            boot = M.CONTEXT.read_system('/proc/sys/kernel/random/boot_id', 64).decode('ascii')
            A.require(boot.endswith('\n'), 'invalid host boot UUID')
            self.boot_id = A._uuid(boot[:-1])
            self._native()
            A.require(M.NORMAL.manager_state() in ('starting', 'running', 'degraded'),
                      'PID1 does not permit maintenance recovery')
            self.receipt['phase'] = 'open-existing'
            self.admission = A.Admission.open_existing(
                self.boot_id, M.NORMAL.local_kvm_gid(), M.NORMAL.manager_state)
            self.receipt['phase'] = 'match-closure'
            with self.admission._gate():
                record = self.admission._read()
                A.require(record['state'] == 'closed' and record['reason'] == 'maintenance'
                          and record['owner_nonce'] == nonce,
                          'exact closed maintenance nonce required')
                self.owner = A.ClosureOwner(self.admission, record)
            self.receipt.update(nonce=nonce, gate_state='closed')
            self.receipt['phase'] = 'stop-managed'
            M.stop_managed()
            self.receipt['phase'] = 'exclusive-lease'
            self.lease = self.owner.exclusive(timeout=0)
            # exclusive() checks the protected nonce again after acquiring EX.
            self.receipt['phase'] = 'physical-state'
            self.controller = M.LIFECYCLE.StateController(storage, self.lease, boot_id=self.boot_id)
            self.recheck()
            self.receipt['phase'] = 'entry-consumers'
            M.consumer_check()
            self.receipt['phase'] = 'entry-quiescence'
            self._quiescent()
            self.receipt['phase'] = 'maintenance'
        except BaseException as error:
            self._fail(error)


def reopen(expected_nonce):
    """Reopen only after installed readiness proofs and report cleanup errors."""
    with ReopenMaintenance(expected_nonce) as operation:
        receipt = operation.finish_success()
    return copy.deepcopy(receipt)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-nonce', required=True,
                        help='UUID of the existing closed maintenance record')
    args = parser.parse_args(argv)
    try:
        receipt = reopen(args.expected_nonce)
    except M.MaintenanceError as error:
        # Command completion includes cleanup. Preserve the transaction's
        # acknowledged open independently when a later close reports failure.
        failure = dict(error.receipt, success=False, maintenance=error.receipt,
                       error=str(error))
        print(json.dumps(failure, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
