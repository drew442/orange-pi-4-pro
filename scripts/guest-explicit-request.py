#!/usr/bin/python3
"""Protected one-use RAM requests for the fixed managed service.

This API never queues services, reopens admission, repairs records or authenticates
archives. Callers retain admission/guest/disk/registry custody throughout.
"""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import threading
import uuid

BASE = Path(__file__).resolve().parents[1]


def module(name, relative):
    spec = importlib.util.spec_from_file_location(name, BASE / relative)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


CONTEXT = module('explicit_request_context', 'scripts/guest-lifecycle-context.py')
ADMISSION = CONTEXT.ADMISSION  # Shared source-authenticated canonical capabilities.
PREP = module('explicit_request_prepare', 'scripts/prepare-guest-boot.py')
STORE, HEAD, REGISTRY, UNITS = PREP.STORE, PREP.HEAD, PREP.REGISTRY, PREP.UNITS
require = ADMISSION.require
PROFILE = 'a733-guest-explicit-request-v1'
LIMIT = 4096
MAX_RECORDS = 4096
FIELDS = {'schema', 'profile', 'request_id', 'boot_id', 'storage', 'name',
          'manifest_sha256', 'ready_head_sha256', 'unit_sha256'}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return PREP.encoded(value)


def validate(data):
    require(type(data) is bytes and 0 < len(data) <= LIMIT, 'invalid explicit request size')
    value = json.loads(data, object_pairs_hook=ADMISSION._pairs)
    require(type(value) is dict and set(value) == FIELDS, 'invalid explicit request fields')
    require(type(value['schema']) is int and value['schema'] == 1 and
            value['profile'] == PROFILE, 'invalid explicit request profile')
    for key in ('request_id', 'boot_id'):
        ADMISSION._uuid(value[key])
    ADMISSION._guest_name(value['name'])
    require(value['name'] != 'firstkvm', 'firstkvm must remain manual')
    CONTEXT.validate_storage(value['storage'])
    for key in ('manifest_sha256', 'ready_head_sha256', 'unit_sha256'):
        REGISTRY._digest(value[key])
    require(encoded(value) == data, 'explicit request must be canonical')
    return value


def authority(controller, name, guest_lock, guard):
    """Bind every observation/mutation to this process and actual readonly flocks."""
    creator = (os.getpid(), threading.get_ident())

    def check():
        require((os.getpid(), threading.get_ident()) == creator and
                threading.current_thread() is threading.main_thread(),
                'explicit request requires its creator main thread')
        require(os.getresuid() == (0, 0, 0), 'actual root explicit controller required')
        ADMISSION._guest_name(name)
        require(name != 'firstkvm', 'firstkvm must remain manual')
        require(callable(guard), 'caller placement/registry guard required')
        PREP.installed_guard(controller.boot_id)
        controller.context.guard()
        admission, lease = controller.admission, controller.lease
        require(isinstance(admission, ADMISSION.Admission) and
                isinstance(lease, ADMISSION.Lease) and not lease.exclusive and
                lease.admission is admission and controller.context.lease is lease,
                'canonical shared admission lease required')
        admission._check_guest(name, lease, guest_lock)
        ADMISSION._held_lock(lease.fd, 'READ', os.getpid())
        ADMISSION._held_lock(guest_lock.fd, 'WRITE', os.getpid())
        require(admission.boot_id == controller.boot_id,
                'explicit admission boot differs')
        require(admission.is_managed(name, lease, guest_lock), 'managed marker missing')
        guard()
    check()
    return check


def bindings(controller, name, pin, head_data, inspection, entry, check):
    check()
    REGISTRY._digest(pin)
    head = HEAD.validate_head(head_data)
    require(head['status'] == 'ready' and head['name'] == name and
            head['manifest_sha256'] == pin and head['storage'] == controller.context.storage,
            'ready head binding differs')
    inspection.recheck()
    config = inspection.manifest['config']
    require(digest(inspection.manifest_data) == pin and
            inspection.manifest_record['sha256'] == pin,
            'explicit manifest pin differs')
    require(config['name'] == name and inspection.manifest['guest_uuid'] ==
            controller.context.storage['guest_uuid'], 'explicit manifest identity differs')
    require(all(head[key] == config[key] == entry[key] for key in ('name', 'uid', 'gid')) and
            entry['manifest_sha256'] == pin and entry['enabled'] is True,
            'enabled explicit registry identity differs')
    require(inspection.disk_record == config['disk'], 'explicit ready disk differs')
    unit_data = UNITS.render_managed(config).encode('ascii')
    with PREP.Resources() as stack:
        units = stack.enter_context(PREP.RamDirectory(PREP.UNIT_DIRECTORY, 0o755, check))
        source = stack.enter_context(PREP.Source(
            units.fd, 'a733-managed-' + name + '.service', 0o444, PREP.UNIT_LIMIT, units.check))
        require(source.data == unit_data, 'fixed managed unit differs from current config')
        source.check()
        check()
    return dict(schema=1, profile=PROFILE, boot_id=controller.boot_id,
                storage=controller.context.storage, name=name, manifest_sha256=pin,
                ready_head_sha256=digest(head_data), unit_sha256=digest(unit_data))


def _pending(name):
    return 'pending-' + name + '.json'


def _same(value, expected):
    require({key: item for key, item in value.items() if key != 'request_id'} == expected,
            'pending explicit request binding differs')


def _read(directory, name, limit, parser=None):
    """Preserve validation and close failures for protected request metadata."""
    with PREP.Resources() as stack:
        source = stack.enter_context(PREP.Source(
            directory.fd, name, 0o400, limit, directory.check))
        value = source.data if parser is None else parser(source.data)
        source.check()
        return value


def _reject_consumed_head(directory, name, head_pin):
    """Retained consumed records close the lost-ack gap before HEAD publication."""
    names = os.listdir(directory.fd)
    require(len(names) <= MAX_RECORDS, 'explicit request record limit reached')
    for filename in names:
        if not filename.startswith('consumed-'):
            continue
        require(filename.endswith('.json'), 'invalid consumed record name')
        value = _read(directory, filename, LIMIT, validate)
        require(filename == 'consumed-' + value['request_id'] + '.json',
                'consumed request filename differs')
        require(not (value['name'] == name and value['ready_head_sha256'] == head_pin),
                'explicit request for this ready head was already consumed')
    directory.check()
    return names


def issue(controller, name, manifest_sha256, head_data, unit_sha256, guest_lock, guard,
          *, inspection, registry_entry):
    """Create once while the caller retains ready inspection and final registry lock.

    Reobserving an identical pending request is a no-op. A consumed request for
    this head is never requeued, even when a previous acknowledgement was lost.
    """
    result = dict(operation='issue-explicit-request', name=name, stage='validate',
                  success=False, hardware_acceptance=False, no_op=False)
    try:
        check = authority(controller, name, guest_lock, guard)
        expected = bindings(controller, name, manifest_sha256, head_data,
                            inspection, registry_entry, check)
        require(expected['unit_sha256'] == unit_sha256, 'caller unit pin differs')
        with PREP.Resources() as stack:
            head_store = controller._head_store(stack, name, check)
            require(_read(head_store, 'head.json', HEAD.LIMIT) == head_data,
                    'fresh protected ready head differs')
            _, _, registry = controller._registry(stack, check)
            require(registry is not None and registry_entry in registry['guests'],
                    'fresh registry entry differs')
            ram = stack.enter_context(PREP.RamDirectory(
                PREP.PREPARED_DIRECTORY, 0o700, check))
            directory = stack.enter_context(STORE.RootDirectory(
                ram.fd, 'explicit', guard=ram.check, create=True))
            names = _reject_consumed_head(directory, name, expected['ready_head_sha256'])
            try:
                value = _read(directory, _pending(name), LIMIT, validate)
            except FileNotFoundError as error:
                if error.filename != _pending(name):
                    raise
                require(len(names) < MAX_RECORDS, 'explicit request record limit reached')
                value = dict(expected, request_id=str(uuid.uuid4()))
                result['stage'] = 'publish'
                result['publication'] = directory.publish(_pending(name), encoded(value))
            else:
                _same(value, expected)
                result['no_op'] = True
            result.update(request_id=value['request_id'], ready_head_sha256=value['ready_head_sha256'])
            directory.check()
        check()
    except BaseException as error:
        if hasattr(error, 'receipt'):
            result['nested_error'] = copy.deepcopy(error.receipt)
        raise STORE.PublicationError(result, error) from error
    result.update(stage='complete', success=True)
    return result


def consume(controller, name, pin, head_data, guest_lock, inspection, registry_entry, guard):
    """Return exact automatic flag; valid pending request is irreversibly consumed.

    Missing requests are read-only automatic observations unless this exact ready
    head already has a consumed receipt. Unsafe/stale records fail closed, and
    cleanup completes before live run admission.
    """
    result = dict(operation='consume-explicit-request', name=name, stage='validate',
                  success=False, consumed=False, uncertain=False, hardware_acceptance=False)
    try:
        check = authority(controller, name, guest_lock, guard)

        def observe():
            with PREP.Resources() as stack:
                try:
                    ram = stack.enter_context(PREP.RamDirectory(
                        PREP.PREPARED_DIRECTORY, 0o700, check))
                except FileNotFoundError as error:
                    if error.filename != Path(PREP.PREPARED_DIRECTORY).name:
                        raise
                    check()
                    return True
                try:
                    directory = stack.enter_context(STORE.RootDirectory(
                        ram.fd, 'explicit', guard=ram.check))
                except FileNotFoundError as error:
                    if error.filename != 'explicit':
                        raise
                    check()
                    return True
                try:
                    source = stack.enter_context(PREP.Source(
                        directory.fd, _pending(name), 0o400, LIMIT, directory.check))
                except FileNotFoundError as error:
                    if error.filename != _pending(name):
                        raise
                    # A rename can succeed before its acknowledgement or live
                    # head publication. Never reinterpret that same head as an
                    # automatic launch, including the initial enrolled head.
                    _reject_consumed_head(directory, name, digest(head_data))
                    check()
                    return True
                value = validate(source.data)
                expected = bindings(controller, name, pin, head_data, inspection, registry_entry, check)
                _same(value, expected)
                target = 'consumed-' + value['request_id'] + '.json'
                result.update(request_id=value['request_id'], stage='rename', uncertain=True)
                source.check()
                directory.check()
                identity = STORE._inode(os.fstat(source.fd))
                CONTEXT.ENROLL.rename_new(directory.fd, source.name, directory.fd, target)
                result['consumed'] = True
                directory.check()
                result['stage'] = 'directory-fsync'
                os.fsync(directory.fd)
                result['directory_fsynced'] = True
                # Rename changes ctime: reopen the new name, retaining physical inode.
                final = stack.enter_context(PREP.Source(
                    directory.fd, target, 0o400, LIMIT, directory.check))
                require(STORE._inode(os.fstat(final.fd)) == identity and final.data == source.data,
                        'consumed request source changed')
                final.check()
                directory.check()
            return False

        automatic = observe()
        check()
    except BaseException as error:
        if hasattr(error, 'receipt'):
            result['nested_error'] = copy.deepcopy(error.receipt)
        raise STORE.PublicationError(result, error) from error
    result.update(stage='complete', success=True, uncertain=False)
    return automatic
