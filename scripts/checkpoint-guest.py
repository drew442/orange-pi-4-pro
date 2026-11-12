#!/usr/bin/python3
"""Explicit stopped-guest manifest checkpoint; never writes guest payload bytes.

A checkpoint attests the operator's clean shutdown verification. It does not
inspect filesystem cleanliness, create a backup, or establish hardware acceptance.
"""
import argparse
import base64
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import sys

BASE = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, BASE / path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


POLICY = module('checkpoint_policy', 'scripts/guest-checkpoint-policy.py')
TRANSACTION = module('checkpoint_transaction', 'scripts/guest-checkpoint-transaction.py')
OPERATION = module('checkpoint_operation', 'scripts/guest-operation-admission.py')
BACKUP = module('checkpoint_backup', 'configs/guest_backup.py')
ENROLL = POLICY.ENROLL
MANIFEST = POLICY.MANIFEST
require = ENROLL.require
canonical = ENROLL.canonical
PROFILE = 'a733-guest-checkpoint-v1'
LOCK = '/run/a733-guest-checkpoint.lock'


def record(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def make_plan(inspection):
    old = inspection.manifest_record
    disk = {key: inspection.disk_record[key] for key in ('bytes', 'sha256')}
    proposed = MANIFEST.proposed_manifest(inspection.manifest, old['sha256'], disk)
    changed = disk != inspection.manifest['config']['disk']
    encoded = MANIFEST.canonical(proposed) if changed else inspection.manifest_data
    result = {'schema': 1, 'operation': PROFILE, 'name': inspection.name,
              'context': inspection.context, 'disk': inspection.disk_record,
              'old_manifest': {**old, 'value': inspection.manifest},
              'proposed_manifest': {**record(encoded), 'value': proposed},
              'requires_update': changed}
    result['plan_sha256'] = hashlib.sha256(canonical(result)).hexdigest()
    return result


def prepare(name, manifest_sha256):
    with OPERATION.ManualGuestOperation(name) as operation, \
            POLICY.Inspection(name, manifest_sha256) as inspection:
        result = make_plan(inspection)
        inspection.recheck()
        operation.recheck()
        return result


def authorize(plan):
    require(sys.stdin.isatty() and sys.stderr.isatty(), 'apply requires an interactive TTY')
    phrase = f"CHECKPOINT {plan['name']} {plan['disk']['sha256']} {plan['plan_sha256']}"
    for prompt, expected in (('Confirm independently verified clean guest shutdown',
                              'CLEAN SHUTDOWN VERIFIED'),
                             ('Authorize manifest checkpoint', phrase)):
        sys.stderr.write(prompt + ': ' + expected + '\n> ')
        sys.stderr.flush()
        require(sys.stdin.readline().rstrip('\n') == expected, 'authorization phrase differs')


def transaction(inspection, plan, lock_check, review, receipt):
    """Manual adapter: preserve operator authorization and schema-1 history bytes."""
    archive = None
    if plan['requires_update']:
        old, new = plan['old_manifest'], plan['proposed_manifest']
        archive = canonical({'schema': 1, 'operation': PROFILE,
                             'old_manifest': old['value'], 'new_manifest': new['value'],
                             'old_manifest_sha256': old['sha256'],
                             'new_manifest_sha256': new['sha256'],
                             'old_manifest_base64': base64.b64encode(inspection.manifest_data).decode('ascii'),
                             'new_manifest_base64': base64.b64encode(MANIFEST.canonical(new['value'])).decode('ascii')})
    return TRANSACTION.transaction(
        inspection, plan, lock_check, review, receipt, enroll=ENROLL,
        manifest=MANIFEST, require=require, canonical=canonical,
        history_data=archive, history_schema=1, validate_record=BACKUP.validate_record)


def apply(name, manifest_sha256, plan_path):
    receipt = {'schema': 1, 'operation': PROFILE, 'success': False,
               'published': False, 'durable': False, 'history_published': False,
               'history_durable': False, 'disk_flush_attempted': False,
               'metadata_may_be_modified': False, 'manifest_replace_attempted': False}
    operation = lock = inspection = review = None
    try:
        operation = OPERATION.ManualGuestOperation(name)
        lock = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        info = os.fstat(lock)
        ENROLL.regular(info, 0o600)
        require(info.st_dev == os.stat('/run').st_dev, 'checkpoint lock must reside on RAM')
        def lock_check():
            operation.recheck()
            require(ENROLL.identity(os.fstat(lock)) == ENROLL.identity(info) and
                    ENROLL.identity(os.lstat(LOCK)) == ENROLL.identity(info), 'checkpoint lock changed')
        lock_check()
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        inspection = POLICY.Inspection(name, manifest_sha256)
        plan = make_plan(inspection)
        review = ENROLL.HeldPath(plan_path)
        devices = {os.stat('/run').st_dev, os.stat(ENROLL.MANAGEMENT.STATE).st_dev}
        supplied = json.loads(ENROLL.read_small(review, devices),
                              object_pairs_hook=ENROLL.CONFIG.IDENTITY.unique_pairs,
                              parse_constant=ENROLL.CONFIG._reject_constant)
        require(canonical(supplied) == canonical(plan), 'stale or mismatched checkpoint plan')
        receipt.update({'name': name, 'plan_sha256': plan['plan_sha256'],
                        'old_manifest_sha256': plan['old_manifest']['sha256'],
                        'new_manifest_sha256': plan['proposed_manifest']['sha256'],
                        'new_disk_sha256': plan['disk']['sha256']})
        authorize(plan)
        transaction(inspection, plan, lock_check, review, receipt)
        operation.recheck()
    except BaseException as error:
        receipt['success'] = False
        receipt['error'] = type(error).__name__
    finally:
        for resource in (review, inspection, lock, operation):
            if resource is not None:
                try:
                    os.close(resource) if isinstance(resource, int) else resource.close()
                except BaseException as error:
                    receipt['success'] = False
                    receipt['cleanup_error'] = type(error).__name__
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for action in ('prepare', 'apply'):
        command = commands.add_parser(action)
        command.add_argument('--name', required=True)
        command.add_argument('--manifest-sha256', required=True)
        if action == 'apply':
            command.add_argument('--plan', required=True)
    args = parser.parse_args(argv)
    os.umask(0o077)
    def interrupted(_signum, _frame):
        raise KeyboardInterrupt
    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        result = (apply(args.name, args.manifest_sha256, args.plan) if args.command == 'apply'
                  else prepare(args.name, args.manifest_sha256))
        print(canonical(result).decode(), end='')
        return 0 if result.get('success', True) else 1
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, UnicodeError, KeyboardInterrupt,
            OPERATION.ADMISSION.AdmissionError) as error:
        print(json.dumps({'success': False, 'error': type(error).__name__}), file=sys.stderr)
        sys.exit(1)
