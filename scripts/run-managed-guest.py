#!/usr/bin/python3
"""Run one enabled managed guest through retained stop/checkpoint custody.

No bootstrap, restart, forced checkpoint, custom QEMU arguments or hardware
acceptance. Normal service lifetime is unlimited; producer deadlines stay bounded.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import sys
import threading
import time
import uuid

BASE = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    'managed_service_maintenance', BASE / 'scripts/guest-host-maintenance.py')
MAINTENANCE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(MAINTENANCE)
# Use exactly one checkpoint/fixture/supervisor/lifecycle family. The storage
# discovery helper grants no authority and reads no guest entry for admission.
CP = MAINTENANCE.CP
LIFECYCLE, SUPERVISOR = CP.LIFECYCLE, CP.SUPERVISOR
ADMISSION, CONTEXT = LIFECYCLE.ADMISSION, LIFECYCLE.CONTEXT
EXPLICIT = CONTEXT.module('managed_service_explicit', 'scripts/guest-explicit-request.py')
NORMAL = MAINTENANCE.NORMAL
require = ADMISSION.require


class ServiceError(RuntimeError):
    def __init__(self, receipt, cause):
        self.receipt = copy.deepcopy(receipt)
        self.cause = cause
        super().__init__(f"{receipt['stage']}: {type(cause).__name__}: {cause}")


class StopRequest:
    """Signal handlers set only a flag; all authority stays on the main thread."""
    def __init__(self):
        self.requested = False

    def signal(self, number, frame):
        self.requested = True

    def __call__(self):
        return self.requested


def run_service(name, *, stop_requested=None):
    """Own one actual service run. Optional callback is an API test seam only."""
    result = dict(operation='managed-service', name=name, stage='validate',
                  success=False, hardware_acceptance=False, errors=[], cleanup=[])
    admission = lease = controller = run = supervisor = fixture = None
    cause = None
    creator = (os.getpid(), threading.get_ident())

    def owner():
        require((os.getpid(), threading.get_ident()) == creator,
                'service process/thread changed')
        require(threading.current_thread() is threading.main_thread(),
                'service requires main thread')
        require(os.getresuid() == (0, 0, 0), 'actual root service required')

    def stopping():
        owner()
        state = NORMAL.manager_state()
        require(state in ('starting', 'running', 'degraded', 'stopping'),
                'unsupported PID1 SystemState')
        if state == 'stopping':
            # Monotonic closure before any shutdown request. Existing SH custody
            # remains usable; no closure owner is ever used to reopen the gate.
            admission.close_gate('shutdown')
            result['shutdown_gate_closed'] = True
            return True
        return bool(stop_requested()) if stop_requested is not None else False

    try:
        owner()
        ADMISSION._guest_name(name)
        require(name != 'firstkvm', 'firstkvm must remain manual')
        storage = MAINTENANCE.registered_storage()
        boot = CONTEXT.read_system('/proc/sys/kernel/random/boot_id', 64).decode('ascii')
        require(boot.endswith('\n'), 'invalid host boot UUID')
        boot_id = ADMISSION._uuid(boot[:-1])
        CONTEXT.host_identity(boot_id)
        CONTEXT.MANAGEMENT.runtime_guard()
        result['stage'] = 'open-existing'
        admission = ADMISSION.Admission.open_existing(
            boot_id, NORMAL.local_kvm_gid(), NORMAL.manager_state)
        result['stage'] = 'shared-admission'
        lease = admission.admit()
        result['stage'] = 'physical-state'
        controller = LIFECYCLE.StateController(storage, lease, boot_id=boot_id)
        result['stage'] = 'registry'
        observed = controller.registry()
        require(observed['configured'], 'managed registry is unconfigured')
        entries = [item for item in observed['registry']['guests'] if item['name'] == name]
        require(len(entries) == 1 and entries[0]['enabled'], 'enabled registered guest required')
        pin = entries[0]['manifest_sha256']
        result.update(manifest_sha256=pin, run_id=str(uuid.uuid4()))
        result['stage'] = 'admit-run'
        run = controller.admit_run(name, pin, result['run_id'], automatic=True,
                                   admission_policy=EXPLICIT.consume)
        result['stage'] = 'supervisor'
        supervisor = SUPERVISOR.Supervisor(run)
        if run._record['fixture_profile'] == CP.FIXTURE.PROFILE:
            fixture = CP.FIXTURE.Fixture(supervisor)
        else:
            require(run._record['fixture_profile'] == 'none', 'unsupported fixture profile')
        require(not stopping(), 'stop requested before launch')
        result['stage'] = 'start'
        supervisor.start()
        if fixture is not None:
            result['stage'] = 'fixture'
            result['fixture'] = fixture.verify_and_confirm()
            # The fixed fixture API cannot interrupt its bounded verification.
            # A signal during it is ambiguous and must not bless acceptance.
            require(not stopping(), 'stop during fixture verification; proof ambiguous')
        result['stage'] = 'running'
        while True:
            owner()
            # PID1 shutdown signals this service. Keep the unlimited healthy
            # loop to flag/QMP/raw-child observations, with no systemctl spawn.
            if stop_requested is not None and stop_requested():
                break
            status = supervisor.poll()
            if status is not None or supervisor.channel.shutdown_message is not None:
                break
            time.sleep(0.2)
        result['stage'] = 'stop'
        stopping()  # Reobserve PID1 before issuing the shutdown request.
        outcome = supervisor.stop()
        result['stage'] = 'checkpoint'
        result['checkpoint'] = CP.checkpoint(outcome, fixture=fixture)
        require(result['checkpoint']['success'], 'checkpoint did not complete')
    except BaseException as error:
        cause = error
        result['errors'].append(repr(error))
        if hasattr(error, 'receipt'):
            result['nested_error'] = copy.deepcopy(error.receipt)
    finally:
        # Every close is attempted, exactly once, in authority retention order.
        # Supervisor may record cleanup failures without raising.
        for label, resource in (('supervisor', supervisor), ('run', run),
                                ('controller', controller), ('lease', lease),
                                ('admission', admission)):
            if resource is None:
                continue
            try:
                owner()
                resource.close()
                if label == 'supervisor':
                    require(not resource.receipt['errors'], 'supervisor cleanup errors')
                result['cleanup'].append(label)
            except BaseException as error:
                cause = cause or error
                result['errors'].append(label + ' close: ' + repr(error))
                if hasattr(error, 'receipt'):
                    result[label + '_close_error'] = copy.deepcopy(error.receipt)
        if run is not None:
            result['run'] = copy.deepcopy(run.receipt)
        if supervisor is not None:
            result['supervisor'] = copy.deepcopy(supervisor.receipt)
    if cause is not None:
        raise ServiceError(result, cause) from cause
    result.update(stage='complete', success=True)
    return result


def json_receipt(value):
    """Bound CLI output independently of retained producer transcript sizes."""
    budget = [4096]
    def bounded(item, depth=0):
        budget[0] -= 1
        if budget[0] < 0 or depth > 16:
            return {'omitted': True}
        if isinstance(item, bytes):
            return dict(bytes=len(item), sha256=hashlib.sha256(item).hexdigest())
        if isinstance(item, dict):
            result = {str(k)[:128]: bounded(v, depth + 1) for k, v in list(item.items())[:128]}
            if len(item) > 128:
                result['__omitted_entries'] = len(item) - 128
            return result
        if isinstance(item, (list, tuple)):
            result = [bounded(v, depth + 1) for v in item[:64]]
            if len(item) > 64:
                result.append({'omitted_items': len(item) - 64})
            return result
        if isinstance(item, str):
            return item[:2048]
        if item is None or type(item) in (bool, int, float):
            return item
        return repr(item)[:2048]
    raw = json.dumps(bounded(value), sort_keys=True, allow_nan=False)
    if len(raw.encode()) > 262144:
        return json.dumps(dict(success=value.get('success', False), receipt_oversize=True,
                               errors=[str(error)[:2048] for error in value.get('errors', [])[:8]]))
    return raw


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('name')
    args = parser.parse_args(argv)
    request = StopRequest()
    previous = {}
    try:
        require(threading.current_thread() is threading.main_thread(), 'main thread required')
        for number in (signal.SIGTERM, signal.SIGINT):
            previous[number] = signal.signal(number, request.signal)
        try:
            receipt = run_service(args.name, stop_requested=request)
        except ServiceError as error:
            print(json_receipt(error.receipt), file=sys.stderr)
            return 1
        print(json_receipt(receipt))
        return 0
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


if __name__ == '__main__':
    sys.exit(main())
