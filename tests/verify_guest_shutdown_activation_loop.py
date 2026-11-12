#!/usr/bin/env python3
"""Opt-in native shutdown activation on a newly generated owned GPT loop.

Run as root without arguments. Reuses the pinned existing loop harness's fixed
/var/lib workspace, 2 GiB guard, actual GPT/ext4, identity checks and conservative
cleanup. Its NVMe/source/STATE metadata remain explicit substitutes. Admission
here is actual canonical /run tmpfs, readonly EX flock and shutdown closure.
No physical NVMe, A733 boot, VM queueing or KVM acceptance is exercised.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest import mock

SOURCE = Path(__file__).with_name('verify_guest_mount_loop.py')
SOURCE_SHA256 = '21ceed53c6a72c1e5e5c37b05746ac5b16381060853f2cc5d166e7df73baf366'
if hashlib.sha256(SOURCE.read_bytes()).hexdigest() != SOURCE_SHA256:
    raise RuntimeError('reviewed loop helper source changed; review adapter before running')
SPEC = importlib.util.spec_from_file_location('shutdown_owned_loop', SOURCE)
LOOP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LOOP)
C, A = LOOP.CONTROL, LOOP.CONTROL.ADMISSION


def verify():
    observer = None
    original_run = subprocess.run
    baseline = LOOP.descriptor_snapshot()

    class ShutdownObserver:
        """Observe actual authority; never substitutes admission or its flocks."""
        def __init__(self, guard, unlocked):
            self.guard, self.unlocked = guard, unlocked
            self.admission = self.lease = None
            self.checks = self.mutations = self.applications = 0
            self.closed = False
            try:
                self.admission = A.initialize(A._boot_id(), os.getgid(), lambda: 'running')
                # Fixture preparation completes before the shutdown-only operation.
                self.admission.bootstrap_owner.open()
                self.admission.bootstrap_owner.lease.close()
                self.admission.close_gate('shutdown')
                self.lease = self.admission.shutdown_lease()
                self.record = self.admission._read()
                self.check()
            except BaseException:
                self.close()
                raise

        def check(self):
            LOOP.require(not self.closed, 'shutdown observer already closed')
            self.guard()
            C.shutdown_check(self.lease)
            LOOP.require(self.admission._read() == self.record,
                         'shutdown closure changed or reopened')
            self.checks += 1

        def mutation(self):
            self.check()
            self.mutations += 1

        def close(self):
            if not self.closed:
                self.closed = True
                try:
                    if self.lease is not None:
                        self.lease.close()
                finally:
                    if self.admission is not None:
                        self.admission.close()

    def actual_shutdown(storage, guard, unlocked):
        nonlocal observer
        LOOP.require(observer is None, 'shutdown fixture initialized twice')
        observer = ShutdownObserver(guard, unlocked)
        # The imported harness uses this list solely to assert authority at
        # actual mount/umount/chmod calls. Its fake maintenance is never built.
        return SimpleNamespace(), [observer]

    def apply(args, plan, report=None, *, plan_check):
        observer.check()
        with mock.patch.object(C, 'authorize', side_effect=AssertionError('manual authorization forbidden')), \
             mock.patch.object(A, 'initialize', side_effect=AssertionError('initialization forbidden')), \
             mock.patch.object(A.ClosureOwner, 'open', side_effect=AssertionError('reopening forbidden')), \
             mock.patch.object(observer.admission, '_gate', side_effect=AssertionError('deep gate lock forbidden')):
            result = C.apply_shutdown(args, plan, observer.lease, report, plan_check=plan_check)
        observer.check()
        observer.unlocked()  # Actual activation lock/disk flock and FD census.
        observer.applications += 1
        return result

    def assert_shutdown(instances, successful):
        LOOP.require(instances == [observer] and successful == [False, True, True],
                     'actual shutdown fixture sequencing differs')
        observer.check()
        LOOP.require(observer.applications == 3 and observer.mutations == 4,
                     'interruption/retry/idempotency mutation count differs')
        observer.close()

    def run(command, *args, **kwargs):
        # Release every owned authority descriptor before inherited cleanup
        # unmounts private /run, including unexpected failures before final checks.
        if command == ['umount', '/run'] and observer is not None:
            observer.close()
        return original_run(command, *args, **kwargs)

    try:
        # Only host/namespace classification is substituted for the deliberately
        # private mount namespace. Actual boot ID, protected metadata and flocks
        # remain native; the original harness checks actual loop identities.
        with mock.patch.object(LOOP, 'fixture_maintenance', new=actual_shutdown), \
             mock.patch.object(LOOP, 'assert_custody', new=assert_shutdown), \
             mock.patch.object(C, 'apply', new=apply), \
             mock.patch.object(C.CONTEXT, 'host_identity', new=lambda boot: None), \
             mock.patch.object(subprocess, 'run', new=run):
            result = LOOP.verify()
    finally:
        if observer is not None:
            observer.close()
    LOOP.require(LOOP.descriptor_snapshot() == baseline, 'shutdown harness retained owned descriptors')
    # Replace inherited fixture claims with the actual shutdown evidence.
    result.pop('public_apply_fixture_custody_ordering_checked')
    result.pop('core_descriptors_and_locks_released_before_binding')
    result.update(classification=__doc__.strip(), helper_source_sha256=SOURCE_SHA256,
                  actual_canonical_shutdown_readonly_ex=True,
                  same_closed_shutdown_lease_borrowed_for_all_three_applications=True,
                  closure_never_reopened=True, deep_guard_does_not_take_gate=True,
                  core_descriptors_and_locks_released_after_each_application=True,
                  all_owned_descriptors_released=True,
                  shutdown_checks=observer.checks, actual_mutation_authority_checks=observer.mutations)
    return result


if __name__ == '__main__':
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(json.dumps(verify(), indent=2, sort_keys=True))
