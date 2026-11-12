#!/usr/bin/python3
"""Restore one registered managed guest; leave admission closed until fresh boot.

A completed resume is a readonly historical observation. Other operations stop
managed guests, authenticate the archive and execute the retained transaction.
Receipts do not establish boot readiness or hardware acceptance.
"""
import argparse
import copy
import importlib.util
import os
from pathlib import Path
import sys
import threading
import time

BASE = Path(__file__).resolve().parents[1]


class CleanupError(RuntimeError):
    def __init__(self, primary, errors):
        self.primary = primary
        self.receipt = dict(cleanup_uncertain=True, cleanup_errors=[
            dict(resource=label, error=type(error).__name__, message=str(error)[:1024],
                 **({'receipt': copy.deepcopy(error.receipt)} if hasattr(error, 'receipt') else {}))
            for label, error in errors])
        if primary is not None:
            self.receipt['primary_error'] = dict(error=type(primary).__name__, message=str(primary)[:1024])
            if hasattr(primary, 'receipt'):
                self.receipt['primary_error']['receipt'] = copy.deepcopy(primary.receipt)
        super().__init__('restore CLI cleanup failed')


class Resources:
    """Cleanup only; retaining an object here grants no destination authority."""
    def __init__(self):
        self.items = []

    def retain(self, value, name):
        self.items.append((name, value))
        return value

    def __enter__(self):
        return self

    def __exit__(self, kind, primary, traceback):
        items, self.items = self.items, []
        errors = []
        for label, value in reversed(items):
            try:
                value.close()
            except BaseException as error:
                errors.append((label, error))
        if errors:
            raise CleanupError(primary, errors) from primary or errors[0][1]


def engine():
    spec = importlib.util.spec_from_file_location(
        'restore_cli_transaction', BASE / 'scripts/managed-guest-restore-transaction.py')
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


class Progress:
    """Print phase/file changes and bounded periodic byte updates, on main thread."""
    def __init__(self):
        self.previous = None
        self.last = 0

    def __call__(self, value):
        phase = value.get('phase', 'verify')
        name = value.get('file', value.get('name', 'restore'))
        key = phase, name
        now = time.monotonic()
        count, total = value.get('bytes'), value.get('total')
        if key != self.previous or now - self.last >= 15 or count == total:
            suffix = f': {count}/{total} bytes' if type(count) is type(total) is int else ''
            print(f'Restore {phase} {name}{suffix}', file=sys.stderr, flush=True)
            self.previous, self.last = key, now


def restore(name, archive, archive_sha256, manifest_sha256, *, resume=None, progress=None):
    result = dict(operation='restore-managed-guest', name=name, transaction_id=resume,
                  stage='validate', success=False, data_committed=False,
                  historical=False, hardware_acceptance=False)
    transaction = None
    module = None
    try:
        if os.getresuid() != (0, 0, 0) or threading.current_thread() is not threading.main_thread():
            raise ValueError('actual root main thread required')
        if type(archive) is not str or not Path(archive).is_absolute() or '..' in Path(archive).parts:
            raise ValueError('absolute archive path without parent traversal required')
        module = engine()
        custody_module = module.CUSTODY
        admission = custody_module.ADMISSION
        admission._guest_name(name)
        admission.require(name != 'firstkvm', 'firstkvm must remain manually managed')
        custody_module.REGISTRY._digest(archive_sha256)
        custody_module.REGISTRY._digest(manifest_sha256)
        if resume is not None:
            custody_module.REGISTRY._uuid(resume)
        admission.require(progress is None or callable(progress), 'progress callback must be callable')
        meter = Progress() if progress is None else progress
        with Resources() as resources:
            result['stage'] = 'storage-policy'
            policy = resources.retain(custody_module.BOOT_POLICY.Policy(admission._boot_id()), 'policy')
            policy.check()
            if resume is not None:
                result['stage'] = 'observe-completed'
                historical = module.observe_completed(
                    policy, name, resume, archive_sha256, manifest_sha256)
            else:
                historical = None
            if historical is not None:
                admission.require(historical.get('success') is True and
                                  historical.get('data_committed') is True and
                                  historical.get('historical') is True,
                                  'invalid historical completion observation')
                result.update(transaction=copy.deepcopy(historical), historical=True,
                              data_committed=True, transaction_id=resume)
            else:
                result['stage'] = 'shutdown-custody'
                custody = resources.retain(custody_module.ShutdownRestoreCustody(
                    policy.value, name, progress=meter, activate_missing=True), 'shutdown-custody')
                result['activation'] = copy.deepcopy(custody.receipt['activation'])
                result['storage'] = copy.deepcopy(policy.value)
                result['admission'] = 'shutdown-closed'
                custody.recheck()
                context = custody.context
                devices = {os.fstat(context.state_fd).st_dev, os.fstat(context.guest_fd).st_dev}
                result['stage'] = 'authenticate-archive'
                print('Authenticating the complete managed archive before publication',
                      file=sys.stderr, flush=True)
                source = resources.retain(module.SOURCE.Source(
                    archive, archive_sha256, manifest_sha256, devices, progress=meter), 'archive')
                custody.recheck()
                result['stage'] = 'restore-transaction'
                transaction = resources.retain(module.RestoreTransaction(
                    custody, source, transaction_id=resume, resume=resume is not None), 'transaction')
                result['transaction_id'] = transaction.receipt['transaction_id']
                meter(dict(phase='transaction', file=result['transaction_id']))
                receipt = transaction.execute()
                result['transaction'] = copy.deepcopy(receipt)
                result['transaction_id'] = receipt.get('transaction_id')
                result['data_committed'] = receipt.get('data_committed') is True
                admission.require(receipt.get('success') is True and result['data_committed'],
                                  'restore transaction did not acknowledge data commit')
            result['stage'] = 'cleanup'
        result.update(success=True, stage='complete')
    except BaseException as error:
        result.update(error_type=type(error).__name__, error=str(error)[:1024])
        if hasattr(error, 'receipt'):
            result['failure'] = copy.deepcopy(error.receipt)
        # Only this engine's authenticated observer can preserve a historical
        # commit before a transaction object exists. Arbitrary receipt fields
        # cannot establish that observation.
        observed_error = error
        for _ in range(8):
            if not isinstance(observed_error, CleanupError) or observed_error.primary is None:
                break
            observed_error = observed_error.primary
        if (result['stage'] == 'observe-completed' and module is not None and
                type(observed_error) is module.TransactionError):
            observed = observed_error.receipt
            if (observed.get('data_committed') is True and observed.get('historical') is True and
                    observed.get('transaction_id') == resume):
                result.update(data_committed=True, historical=True,
                              transaction=copy.deepcopy(observed),
                              cleanup_uncertain=observed.get('cleanup_uncertain') is True)
        if transaction is not None:
            result['transaction'] = copy.deepcopy(transaction.receipt)
            result['transaction_id'] = transaction.receipt.get('transaction_id', resume)
            result['data_committed'] = transaction.receipt.get('data_committed') is True
        if result['stage'] == 'cleanup' or isinstance(error, CleanupError):
            result['cleanup_uncertain'] = True
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('name')
    parser.add_argument('--archive', required=True)
    parser.add_argument('--archive-sha256', required=True)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--resume', metavar='TRANSACTION_UUID')
    args = parser.parse_args(argv)
    result = restore(args.name, args.archive, args.archive_sha256, args.manifest_sha256,
                     resume=args.resume)
    # Reuse the existing bounded receipt formatter; importing it grants no authority.
    spec = importlib.util.spec_from_file_location('restore_cli_receipt', BASE / 'scripts/run-managed-guest.py')
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    print(runtime.json_receipt(result))
    return 0 if result['success'] else 1


if __name__ == '__main__':
    sys.exit(main())
