"""Prepare protected desired state and RAM legacy exclusions under bootstrap EX.

No gate opening, guest repair, disk inspection, accounts, units or launch authority.
The caller retains the original bootstrap owner and lease throughout this call.
"""
import copy
from contextlib import ExitStack
import importlib.util
import os
from pathlib import Path
import threading

BASE = Path(__file__).resolve().parents[1]


def module(name, relative):
    spec = importlib.util.spec_from_file_location(name, BASE / relative)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


CP = module('boot_state_checkpoint', 'scripts/managed-guest-checkpoint.py')
LIFECYCLE = CP.LIFECYCLE
CONTEXT = LIFECYCLE.CONTEXT
ADMISSION = LIFECYCLE.ADMISSION
STORE, REGISTRY, HEAD = LIFECYCLE.STORE, LIFECYCLE.REGISTRY, LIFECYCLE.HEAD
require, digest = ADMISSION.require, LIFECYCLE.digest
NAME_LIMIT = 256


class PreparationError(RuntimeError):
    def __init__(self, receipt, cause):
        self.receipt, self.cause = copy.deepcopy(receipt), cause
        if hasattr(cause, 'receipt'):
            self.receipt['nested_error'] = copy.deepcopy(cause.receipt)
        if isinstance(cause, ADMISSION.PublicationError):
            self.receipt['marker_error'] = dict(stage=cause.stage, uncertain=cause.uncertain)
        super().__init__(f"{receipt['stage']}: {type(cause).__name__}: {cause}")


def prepare(owner, expected_storage):
    """Return detached observations after durable registry/marker preparation.

    Existing canonical registry bytes, generations and pins are preserved exactly.
    Unavailable heads are observations only; this function never repairs them.
    Errors leave admission closed and retain publication uncertainty in receipts.
    Guards bind this call's PID/thread. ClosureOwner does not record its creation
    thread; trusted root callers remain responsible for any prior thread handoff.
    """
    result = dict(operation='bootstrap-state', stage='validate', success=False,
                  hardware_acceptance=False, publications=[], synchronizations=[],
                  marked_names=[], orphan_names=[], heads={}, cleanup_errors=[])
    controller = None
    creator = os.getpid(), threading.get_ident()
    admission = lease = None
    nonce = None

    def creator_only():
        require((os.getpid(), threading.get_ident()) == creator,
                'bootstrap preparation belongs to another process or thread')

    class ClosingStack(ExitStack):
        def __exit__(self, error_type, error, traceback):
            creator_only()
            try:
                return super().__exit__(error_type, error, traceback)
            except BaseException as close_error:
                if error is None:
                    raise
                result['cleanup_errors'].append(dict(type=type(close_error).__name__,
                                                     error=str(close_error)))
                return False  # Preserve the primary publication receipt.

    def authority():
        creator_only()
        require(os.getresuid() == (0, 0, 0), 'actual root required')
        require(CONTEXT.module('boot_state_admission', 'scripts/guest-admission.py') is ADMISSION,
                'admission source differs')
        require(type(owner) is ADMISSION.ClosureOwner, 'canonical bootstrap owner required')
        require(type(admission) is ADMISSION.Admission and
                admission.bootstrap_owner is owner and owner.admission is admission,
                'original bootstrap owner required')
        require(owner.reason == 'bootstrap' and owner.nonce == nonce,
                'bootstrap ownership differs')
        require(owner.lease is lease and type(lease) is ADMISSION.Lease and
                lease.admission is admission and lease.exclusive,
                'original exclusive bootstrap lease required')
        lease.recheck()
        ADMISSION._held_lock(lease.fd, 'WRITE', creator[0])
        require(admission.manager_state() in ('starting', 'running', 'degraded'),
                'PID1 not accepting bootstrap preparation')
        owner._matching()

    def guard():
        authority()
        controller.context.guard()
        authority()

    try:
        require(type(owner) is ADMISSION.ClosureOwner, 'canonical bootstrap owner required')
        admission, lease, nonce = owner.admission, owner.lease, owner.nonce
        authority()
        storage = CONTEXT.validate_storage(expected_storage)
        result['stage'] = 'physical-state'
        controller = LIFECYCLE.StateController(storage, lease, boot_id=admission.boot_id)
        guard()
        with ClosingStack() as stores:
            _, observed_raw, observed = controller._registry(stores, guard)
            if observed is not None:
                require(REGISTRY.canonical(observed) == observed_raw, 'noncanonical registry')
            else:
                observed = dict(schema=1, profile=REGISTRY.PROFILE, generation=0,
                                storage=storage, guests=[])
            registered = {entry['name']: entry for entry in observed['guests']}
            result['stage'] = 'lifecycle-names'
            try:
                root = stores.enter_context(STORE.RootDirectory(
                    controller.context.guest_fd, '.a733-lifecycle', guard=guard))
            except FileNotFoundError as error:
                if error.filename != '.a733-lifecycle':
                    raise
                root = None
                guard()
            children = set()
            if root is not None:
                root.check()
                with os.scandir(root.fd) as entries:
                    for entry in entries:
                        require(len(children) < NAME_LIMIT, 'lifecycle name limit exceeded')
                        ADMISSION._guest_name(entry.name)
                        children.add(entry.name)
                root.check()
            orphans = children - registered.keys()
            result['orphan_names'] = sorted(orphans)
            names = sorted(children | registered.keys())
            require('firstkvm' not in names, 'unexpected manual firstkvm in managed names')
            require(len(names) <= NAME_LIMIT, 'managed name limit exceeded')
            result['stage'] = 'managed-directory'
            guard()
            admission.prepare_managed(lease)
            guard()
            for name in names:
                result['stage'] = 'managed-marker'
                with admission.guest_lock(name, lease, create=True) as lock:
                    def guest_guard():
                        guard()
                        lock.recheck()
                    guest_guard()
                    admission.mark_managed(name, lease, lock, guard=guest_guard)
                    result['marked_names'].append(name)
                    result['stage'] = 'head-observation'
                    try:
                        require(root is not None, 'lifecycle parent absent')
                        with root.child(name) as head_store:
                            raw = head_store.read('head.json', HEAD.LIMIT)
                            head = HEAD.validate_head(raw)
                            controller._storage(head)
                            require(head['name'] == name, 'head name differs')
                            if name in registered:
                                require(all(head[key] == registered[name][key] for key in
                                            ('name', 'uid', 'gid', 'manifest_sha256')),
                                        'registry/head identity differs')
                            result['heads'][name] = dict(head_sha256=digest(raw), head=head,
                                                       run_permission=False)
                    except (ValueError, OSError, ADMISSION.AdmissionError) as error:
                        result['heads'][name] = dict(unavailable=True, error=str(error),
                                                   run_permission=False)
                    guest_guard()
            # Registry lock is last; no guest or disk lock is awaited under it.
            result['stage'] = 'registry-lock'
            with admission.registry_lock(lease) as registry_lock:
                def registry_guard():
                    guard()
                    registry_lock.recheck()
                registry_guard()
                with ClosingStack() as final_stores:
                    store, raw, value = controller._registry(final_stores, registry_guard)
                    require(raw == observed_raw, 'registry changed during preparation')
                    if store is None:
                        store = final_stores.enter_context(STORE.RootDirectory(
                            controller.context.state_fd, 'guests', guard=registry_guard, create=True))
                        result['publications'].append(copy.deepcopy(store.creation_receipt))
                    if raw is None:
                        raw = REGISTRY.canonical(observed)
                        result['stage'] = 'registry-publication'
                        result['publications'].append(store.publish('autostart.json', raw))
                    result['stage'] = 'registry-synchronization'
                    result['synchronizations'].append(store.sync(
                        'autostart.json', pin=digest(raw), limit=REGISTRY.LIMIT))
                    value = REGISTRY.validate_registry(raw)
                    controller._storage(value)
                    require(REGISTRY.canonical(value) == raw, 'noncanonical registry')
                    result.update(registry=copy.deepcopy(value), registry_raw=raw,
                                  registry_sha256=digest(raw))
                    registry_guard()
        guard()
    except BaseException as error:
        failure = error
    else:
        failure = None
    finally:
        if controller is not None:
            try:
                creator_only()
                controller.close()
            except BaseException as error:
                result['cleanup_errors'].append(dict(type=type(error).__name__, error=str(error)))
                if failure is None:
                    failure = error
    if failure is not None:
        raise PreparationError(result, failure) from failure
    result.update(stage='complete', success=True)
    return copy.deepcopy(result)
