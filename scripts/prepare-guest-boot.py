#!/usr/bin/python3
"""One-process closed-bootstrap preparation. Never start or queue a guest.

Opening admission commits only prepared RAM metadata. Unit/cgroup inspection,
automatic dispatch and storage teardown are separate, still required gates.
"""
import argparse
import copy
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import secrets
import stat
import sys
import threading
from types import SimpleNamespace

BASE = Path(__file__).resolve().parents[1]


def module(name, relative):
    spec = importlib.util.spec_from_file_location(name, BASE / relative)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


STATE = module('prepare_boot_state', 'scripts/guest-boot-state.py')
CP, LIFECYCLE = STATE.CP, STATE.LIFECYCLE
CONTEXT, ADMISSION = LIFECYCLE.CONTEXT, LIFECYCLE.ADMISSION
STORE, REGISTRY, HEAD, POLICY = LIFECYCLE.STORE, LIFECYCLE.REGISTRY, LIFECYCLE.HEAD, LIFECYCLE.POLICY
ACCOUNTS = module('prepare_boot_accounts', 'scripts/guest-boot-accounts.py')
BOOT_POLICY = module('prepare_boot_policy', 'scripts/guest-boot-policy.py')
ACTIVATE = module('prepare_boot_activate', 'scripts/activate-guest-storage.py')
MANUAL = module('prepare_boot_manual', 'scripts/guest-launch-admission.py')
NORMAL = MANUAL.NORMAL
MAINTENANCE = module('prepare_boot_census', 'scripts/guest-host-maintenance.py')
UNITS = module('prepare_boot_units', 'configs/guest_boot_units.py')
RESTORE = module('prepare_boot_restore', 'scripts/managed-guest-restore-transaction.py')
require, digest = ADMISSION.require, LIFECYCLE.digest
PROFILE = 'a733-guest-boot-prepared-v1'
RECORD = 'prepared.json'
RECORD_LIMIT = 128 * 1024
UNIT_LIMIT = 16384
NAME_LIMIT = 256
METADATA_LIMIT = 8 * 1024 * 1024
UNIT_DIRECTORY = '/run/systemd/system'
PREPARED_DIRECTORY = '/run/a733-guest-boot'


class CleanupError(RuntimeError):
    """Preparation resource cleanup failed; admission must stay closed."""
    def __init__(self, message, *, primary=None, cleanup=None):
        self.primary, self.cleanup = primary, cleanup
        self.receipt = {}
        if primary is not None:
            self.receipt['primary_error'] = failure_detail(primary)
        if cleanup is not None:
            self.receipt['cleanup_error'] = failure_detail(cleanup)
        super().__init__(message)


class Resources(ExitStack):
    def __exit__(self, error_type, error, traceback):
        try:
            return super().__exit__(error_type, error, traceback)
        except BaseException as failure:
            if failure is error:
                raise
            raise CleanupError('preparation resource cleanup failed: ' + str(failure),
                               primary=error, cleanup=failure) from failure


class StepError(RuntimeError):
    def __init__(self, label, receipt):
        self.receipt = copy.deepcopy(receipt)
        super().__init__(label)


def completed(receipt, label):
    if receipt.get('success') is not True or receipt.get('cleanup_errors'):
        raise StepError(label, receipt)


def failure_detail(error):
    """Keep primary publication/rollback evidence without copying cohort payloads."""
    def receipt(value, depth=0):
        if depth > 6:
            return {'detail_unavailable': 'nested receipt depth exceeded'}
        detail = {}
        for key, item in value.items():
            if type(item) in (bool, int, type(None)):
                detail[key] = item
            elif type(item) is str:
                detail[key] = item[:512]
            elif key in ('publication_error', 'marker_error', 'nested_error',
                         'primary_error', 'cleanup_error', 'receipt') and type(item) is dict:
                detail[key] = receipt(item, depth+1)
            elif key in ('publications', 'synchronizations') and type(item) is list:
                detail[key + '_count'] = len(item)
                if item:
                    detail['last_' + key] = receipt(item[-1], depth+1)
            elif key in ('cleanup_errors', 'errors') and type(item) is list:
                detail[key] = [receipt(entry, depth+1) if type(entry) is dict else str(entry)[:512]
                               for entry in item[:16]]
        return detail
    detail = {'type': type(error).__name__, 'error': str(error)[:1024]}
    if hasattr(error, 'receipt'):
        detail['receipt'] = receipt(error.receipt)
    if isinstance(error, ADMISSION.PublicationError):
        detail.update(stage=error.stage, uncertain=error.uncertain)
    if hasattr(error, 'rollback_error'):
        detail['rollback_error'] = str(error.rollback_error)[:512]
    return detail


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode('ascii')


def installed_guard(boot_id):
    require(BASE == Path('/usr/lib/a733-tools'), 'installed maintenance source required')
    CONTEXT.host_identity(boot_id)
    CONTEXT.MANAGEMENT.runtime_guard()
    root = CONTEXT.MANAGEMENT.mount_table(Path('/proc/self/mountinfo').read_text())['/'][0]
    require('ro' in root['flags'] and 'rw' not in root['flags'], 'read-only installed source required')
    for path in (BASE, BASE / 'scripts', BASE / 'configs', Path(__file__)):
        info = os.lstat(path)
        require(info.st_uid == info.st_gid == 0 and not info.st_mode & 0o022 and
                not stat.S_ISLNK(info.st_mode) and info.st_dev == os.stat('/').st_dev,
                'unsafe installed source')


def physical_memory():
    raw = CONTEXT.read_system('/proc/meminfo', 65536).decode('ascii')
    rows = [line.split() for line in raw.splitlines() if line.startswith('MemTotal:')]
    require(len(rows) == 1 and len(rows[0]) == 3 and rows[0][1].isdecimal() and
            rows[0][2] == 'kB', 'invalid physical memory observation')
    return int(rows[0][1]) * 1024


class RamDirectory:
    """Fixed /run descendants, retaining physical identities and mount IDs."""
    def __init__(self, path, mode, guard, *, create=False):
        require(path in (UNIT_DIRECTORY, PREPARED_DIRECTORY), 'invalid publication path')
        self.chain, self.guard, self.fd = [], guard, None
        try:
            fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            self.chain.append((None, '', fd, CONTEXT.physical_identity(os.fstat(fd))))
            parts = path.strip('/').split('/')
            run_mount = None
            for index, name in enumerate(parts):
                parent = fd
                if create and index > 0:
                    guard()
                    try:
                        os.mkdir(name, mode if index == len(parts)-1 else 0o755, dir_fd=parent)
                    except FileExistsError:
                        pass
                    else:
                        os.fsync(parent)
                    guard()
                fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                             dir_fd=parent)
                info = os.fstat(fd)
                self.chain.append((parent, name, fd, CONTEXT.physical_identity(info)))
                ADMISSION._tmpfs(fd)
                mount = self._mount(fd)
                if run_mount is None:
                    run_mount = mount
                require(mount == run_mount, 'foreign RAM publication mount')
                require(info.st_uid == info.st_gid == 0 and
                        stat.S_IMODE(info.st_mode) == (mode if index == len(parts)-1 else 0o755),
                        'unsafe RAM directory metadata')
            self.fd, self.mount = fd, run_mount
            self.check()
        except BaseException as error:
            try:
                self.close()
            except BaseException as cleanup:
                raise CleanupError('RAM directory construction cleanup failed',
                                   primary=error, cleanup=cleanup) from error
            raise

    @staticmethod
    def _mount(fd):
        return CONTEXT.STORAGE.fd_mount_id(CONTEXT.STORAGE.read_bounded(
            '/proc/self/fdinfo/' + str(fd), CONTEXT.STORAGE.FDINFO_LIMIT))

    def check(self):
        self.guard()
        require(self.fd is not None, 'RAM directory closed')
        for parent, name, fd, pin in self.chain:
            info = os.fstat(fd)
            require(CONTEXT.physical_identity(info) == pin, 'RAM directory changed')
            require(info.st_uid == info.st_gid == 0 and not info.st_mode & 0o022,
                    'unsafe RAM ancestor')
            if parent is not None:
                require(CONTEXT.physical_identity(os.stat(name, dir_fd=parent,
                    follow_symlinks=False)) == pin, 'RAM directory named identity changed')
                ADMISSION._tmpfs(fd)
                require(self._mount(fd) == self.mount, 'RAM publication mount changed')
        ADMISSION._readonly(self.fd)

    def close(self):
        chain, self.chain, self.fd = self.chain, [], None
        errors = []
        for _, _, fd, _ in reversed(chain):
            try:
                os.close(fd)
            except BaseException as error:
                errors.append(error)
        if errors:
            raise errors[0]

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.close()


class Source:
    """Retained root-owned single-link immutable metadata, opened readonly."""
    def __init__(self, directory, name, mode, limit, guard):
        STORE._name(name)
        self.directory, self.name, self.guard = directory, name, guard
        self.mode, self.limit, self.fd = mode, limit, None
        try:
            guard()
            self.fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                              dir_fd=directory)
            self.pin = STORE._metadata(os.fstat(self.fd))
            self.data = self.read()
        except BaseException as error:
            try:
                self.close()
            except BaseException as cleanup:
                raise CleanupError('metadata source construction cleanup failed',
                                   primary=error, cleanup=cleanup) from error
            raise

    def read(self):
        self.guard()
        require(self.fd is not None, 'metadata source closed')
        ADMISSION._readonly(self.fd)
        info = os.fstat(self.fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == info.st_gid == 0 and
                stat.S_IMODE(info.st_mode) == self.mode and info.st_nlink == 1 and
                info.st_dev == os.fstat(self.directory).st_dev and 0 < info.st_size <= self.limit,
                'unsafe protected metadata')
        require(STORE._metadata(info) == self.pin == STORE._metadata(os.stat(
            self.name, dir_fd=self.directory, follow_symlinks=False)), 'protected metadata changed')
        data = os.pread(self.fd, self.limit+1, 0)
        require(len(data) == info.st_size and STORE._metadata(os.fstat(self.fd)) == self.pin,
                'protected metadata changed during read')
        require(STORE._metadata(os.stat(self.name, dir_fd=self.directory,
                    follow_symlinks=False)) == self.pin, 'protected named metadata changed')
        return data

    def check(self):
        require(self.read() == self.data, 'protected metadata bytes changed')

    def close(self):
        fd, self.fd = self.fd, None
        if fd is not None:
            os.close(fd)

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.close()


def publish(directory, name, data, mode, limit):
    """Create once, preserving exact partial publication and cleanup evidence."""
    receipt = dict(operation='publish-boot-ram', name=name, stage='validate', success=False,
                   mutation_possible=False, rename_attempted=False, published=False,
                   file_fsynced=False, directory_fsynced=False, durable=False,
                   uncertain=False, cleanup_errors=[])
    temporary, fd, source = None, None, None
    failure = None
    try:
        require(type(data) is bytes and 0 < len(data) <= limit, 'invalid publication size')
        directory.check()
        receipt['stage'] = 'existing'
        try:
            source = Source(directory.fd, name, mode, limit, directory.check)
        except FileNotFoundError as error:
            if error.filename != name:
                raise
        if source is not None:
            require(source.data == data, 'existing publication differs')
            source.check()
            receipt['stage'] = 'file-fsync'
            os.fsync(source.fd)
            receipt['file_fsynced'] = True
            directory.check()
            receipt['stage'] = 'directory-fsync'
            os.fsync(directory.fd)
            receipt.update(directory_fsynced=True, durable=True)
            source.check()
        else:
            temporary = '.prepare-' + secrets.token_hex(16)
            receipt.update(stage='create', mutation_possible=True)
            directory.check()
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW |
                         os.O_CLOEXEC, 0o600, dir_fd=directory.fd)
            directory.check()
            os.fchown(fd, 0, 0)
            receipt['stage'] = 'write'
            for offset in range(0, len(data), 4096):
                directory.check()
                CONTEXT.ENROLL.write_all(fd, data[offset:offset+4096])
                directory.check()
            os.fchmod(fd, mode)
            directory.check()
            receipt['stage'] = 'file-fsync'
            os.fsync(fd)
            receipt['file_fsynced'] = True
            number, fd = fd, None
            os.close(number)
            directory.check()
            receipt.update(stage='rename', rename_attempted=True, uncertain=True)
            CONTEXT.ENROLL.rename_new(directory.fd, temporary, directory.fd, name)
            receipt['published'] = True
            directory.check()
            receipt['stage'] = 'directory-fsync'
            os.fsync(directory.fd)
            receipt.update(directory_fsynced=True, durable=True, uncertain=False)
            directory.check()
            receipt['stage'] = 'verification'
            source = Source(directory.fd, name, mode, limit, directory.check)
            require(source.data == data, 'published bytes differ')
            source.check()
    except BaseException as error:
        failure = error
    finally:
        for item in ([source] if source is not None else []):
            try:
                item.close()
            except BaseException as error:
                receipt['cleanup_errors'].append(str(error)[:512])
                failure = failure or error
        if fd is not None:
            number, fd = fd, None
            try:
                os.close(number)
            except BaseException as error:
                receipt['cleanup_errors'].append(str(error)[:512])
                failure = failure or error
        if temporary is not None:
            try:
                os.unlink(temporary, dir_fd=directory.fd)
            except FileNotFoundError:
                pass
            except BaseException as error:
                receipt['cleanup_errors'].append(str(error)[:512])
                failure = failure or error
    if failure is not None:
        raise STORE.PublicationError(receipt, failure) from failure
    return digest(data)


def prepared_record(data):
    require(type(data) is bytes and 0 < len(data) <= RECORD_LIMIT, 'invalid prepared receipt size')
    value = json.loads(data, object_pairs_hook=ADMISSION._pairs)
    require(type(value) is dict and set(value) == {'schema', 'profile', 'boot_id',
            'policy_sha256', 'storage', 'units', 'eligible', 'budget'}, 'invalid prepared receipt fields')
    require(type(value['schema']) is int and value['schema'] == 1 and value['profile'] == PROFILE,
            'invalid prepared receipt profile')
    ADMISSION._uuid(value['boot_id'])
    CONTEXT.validate_storage(value['storage'])
    REGISTRY._digest(value['policy_sha256'])
    names = value['eligible']
    require(type(names) is list and len(names) <= NAME_LIMIT and names == sorted(set(names)),
            'invalid prepared eligible names')
    for name in names:
        ADMISSION._guest_name(name)
        require(name != 'firstkvm', 'firstkvm must remain manual')
    require(type(value['units']) is dict and set(value['units']) == {UNITS.SLICE_NAME} |
            {'a733-managed-' + name + '.service' for name in names}, 'invalid prepared unit set')
    for pin in value['units'].values():
        REGISTRY._digest(pin)
    budget = value['budget']
    keys = {'guest_count', 'physical_memory_bytes', 'host_reserve_bytes', 'budget_bytes',
            'requested_bytes', 'remaining_bytes'}
    require(type(budget) is dict and set(budget) == keys and all(type(v) is int and v >= 0
            for v in budget.values()), 'invalid prepared budget')
    require(budget['guest_count'] <= NAME_LIMIT and budget['guest_count'] >= len(names) and
            budget['host_reserve_bytes'] == UNITS.HOST_RESERVE_BYTES and
            budget['budget_bytes'] == (budget['physical_memory_bytes']//UNITS.MIB)*UNITS.MIB -
                UNITS.HOST_RESERVE_BYTES and budget['budget_bytes'] > 0 and
            budget['requested_bytes'] + budget['remaining_bytes'] == budget['budget_bytes'],
            'invalid prepared budget arithmetic')
    require(encoded(value) == data, 'noncanonical prepared receipt')
    return value


def _registry(controller, guard):
    with Resources() as stack:
        _, raw, value = controller._registry(stack, guard)
        require(value is not None and REGISTRY.canonical(value) == raw, 'prepared registry required')
        require(len(value['guests']) <= NAME_LIMIT and all(e['name'] != 'firstkvm' for e in value['guests']),
                'invalid managed registry names')
        return value


def restore_census(storage, lease, boot_id, guard):
    """Observe restore provenance before any bootstrap state/account mutations."""
    guard()
    with Resources() as resources:
        controller = resources.enter_context(RESTORE.CUSTODY.LIFECYCLE.StateController(
            storage, lease, boot_id=boot_id))
        def checked():
            guard()
            controller.context.guard()
            guard()
        checked()
        result = RESTORE.census(controller, checked)
        completed(result, 'restore census failed')
        require(not result.get('close_errors') and not result.get('cleanup_uncertain'),
                'restore census cleanup uncertain')
        checked()
    guard()
    return result


def _observe_head(controller, entry, guard):
    name = entry['name']
    with Resources() as stack:
        lock = stack.enter_context(controller.admission.guest_lock(name, controller.lease, create=False))
        def checked():
            guard()
            lock.recheck()
        store = controller._head_store(stack, name, checked)
        head = HEAD.validate_head(store.read('head.json', HEAD.LIMIT))
        controller._storage(head)
        require(all(head[k] == entry[k] for k in ('name', 'uid', 'gid')), 'head identity differs')
        checked()
        return head


def _ready(controller, entry, guard):
    name, pin = entry['name'], entry['manifest_sha256']
    with Resources() as stack:
        lock = stack.enter_context(controller.admission.guest_lock(name, controller.lease, create=False))
        inspection = stack.enter_context(POLICY.Inspection(name, pin))
        def checked():
            guard()
            lock.recheck()
            controller._inspection(inspection, name, pin)
            require(controller.admission.is_managed(name, controller.lease, lock), 'managed marker missing')
        checked()
        identity = controller._inspection(inspection, name, pin)
        store = controller._head_store(stack, name, checked)
        raw = store.read('head.json', HEAD.LIMIT)
        head = HEAD.validate_head(raw)
        controller._storage(head)
        require(head['status'] == 'ready' and head['manifest_sha256'] == pin and
                all(head[k] == v for k, v in identity.items()), 'ready head identity differs')
        registry_lock = stack.enter_context(controller.admission.registry_lock(controller.lease))
        def registry_guard():
            checked()
            registry_lock.recheck()
        reg_store, reg_raw, current = controller._registry(stack, registry_guard)
        require(current is not None and REGISTRY.canonical(current) == reg_raw, 'prepared registry missing')
        entries = [e for e in current['guests'] if e['name'] == name]
        require(entries == [entry], 'registry entry changed')
        LIFECYCLE._verify_ready(controller, name, guest_lock=lock, inspection=inspection,
            head_store=store, head_data=raw, registry=(reg_store, reg_raw, current))
        registry_guard()


def _configs(controller, entries, guard):
    """Authenticate metadata for every enabled entry, including unavailable heads."""
    configs, retained = [], 0
    with Resources() as stack:
        sources, directories = [], []
        for entry in entries:
            if not entry['enabled']:
                continue
            name = entry['name']
            info = os.stat(name, dir_fd=controller.context.guest_fd, follow_symlinks=False)
            require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and info.st_gid == entry['gid'] and
                    stat.S_IMODE(info.st_mode) == 0o710 and
                    info.st_dev == os.fstat(controller.context.guest_fd).st_dev, 'unsafe enrollment directory')
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                         dir_fd=controller.context.guest_fd)
            stack.callback(os.close, fd)
            pin = CONTEXT.physical_identity(info)
            def directory_guard(fd=fd, name=name, pin=pin):
                guard()
                require(CONTEXT.physical_identity(os.fstat(fd)) == pin == CONTEXT.physical_identity(
                    os.stat(name, dir_fd=controller.context.guest_fd, follow_symlinks=False)),
                    'enrollment directory changed')
                require(RamDirectory._mount(fd) == RamDirectory._mount(controller.context.guest_fd),
                        'foreign enrollment mount')
            directory_guard()
            directories.append(directory_guard)
            source = stack.enter_context(Source(fd, 'enrollment.json', 0o400, POLICY.MANIFEST.LIMIT,
                                                directory_guard))
            retained += len(source.data)
            require(retained <= METADATA_LIMIT, 'enabled metadata limit exceeded')
            manifest = POLICY.MANIFEST.validate_manifest(source.data)
            config = manifest['config']
            require(digest(source.data) == entry['manifest_sha256'] and
                    manifest['guest_uuid'] == controller.context.storage['guest_uuid'] and
                    all(config[k] == entry[k] for k in ('name', 'uid', 'gid')), 'enabled enrollment differs')
            configs.append(copy.deepcopy(config))
            sources.append(source)
        for check in directories:
            check()
        for source in sources:
            source.check()
    guard()
    return configs


def _noop(admission, policy, boot_id, policy_pin, guard):
    """No lease acquisition, state mutation, start or queue on an existing boot."""
    with admission._gate():
        require(admission._read()['state'] == 'open', 'existing closed admission cannot be adopted')
        with Resources() as stack:
            runtime = stack.enter_context(RamDirectory(PREPARED_DIRECTORY, 0o700, guard))
            source = stack.enter_context(Source(runtime.fd, RECORD, 0o400, RECORD_LIMIT, runtime.check))
            value = prepared_record(source.data)
            require(value['boot_id'] == boot_id and value['storage'] == policy.value['storage'] and
                    value['policy_sha256'] == policy_pin, 'prepared boot/storage/policy differs')
            units = stack.enter_context(RamDirectory(UNIT_DIRECTORY, 0o755, guard))
            for name, pin in value['units'].items():
                with Source(units.fd, name, 0o444, UNIT_LIMIT, units.check) as unit:
                    require(digest(unit.data) == pin, 'prepared unit differs')
            attestation = MANUAL.Attestation(admission)
            try:
                attestation.recheck()
            finally:
                attestation.close()
            source.check()
            guard()
            require(admission._read()['state'] == 'open', 'existing admission changed')
    return value


def prepare():
    """Return a bounded receipt; failures preserve acknowledged-open uncertainty."""
    result = dict(operation='prepare-guest-boot', stage='validate', success=False,
                  hardware_acceptance=False, admission_open_attempted=False, admission_open_acknowledged=False,
                  admission_open_uncertain=False, noop=False, eligible=[], unavailable={},
                  units={}, cleanup_errors=[])
    admission = policy = owner = controller = None
    creator = os.getpid(), threading.get_ident()
    boot_id = None
    cause = None
    opened = False

    def creator_guard():
        require((os.getpid(), threading.get_ident()) == creator and
                threading.current_thread() is threading.main_thread(), 'prepare requires its original main thread')
        require(os.getresuid() == (0, 0, 0), 'actual root required')

    def guard():
        creator_guard()
        require(ADMISSION._boot_id() == boot_id, 'boot changed')
        installed_guard(boot_id)
        if policy is not None:
            policy.check()

    def authority():
        guard()
        require(type(owner) is ADMISSION.ClosureOwner and admission.bootstrap_owner is owner and
                owner.admission is admission and owner.reason == 'bootstrap' and owner.nonce == nonce,
                'original bootstrap owner required')
        require(owner.lease is lease and type(lease) is ADMISSION.Lease and lease.exclusive and
                lease.admission is admission, 'original bootstrap EX required')
        lease.recheck()
        ADMISSION._held_lock(lease.fd, 'WRITE', creator[0])
        require(admission.manager_state() in ('starting', 'running', 'degraded'), 'PID1 not accepting preparation')
        owner._matching()

    try:
        creator_guard()
        boot_id = ADMISSION._boot_id()
        guard()
        managed = CONTEXT.MANAGEMENT.guest_management_profile() == 'managed-experimental'
        require(CONTEXT.module('prepare_admission', 'scripts/guest-admission.py') is ADMISSION and
                all(component.ADMISSION is ADMISSION for component in
                    (ACCOUNTS, BOOT_POLICY.CONTEXT, ACTIVATE, MANUAL, MAINTENANCE)),
                'canonical admission family required')
        require(RESTORE.ADMISSION is ADMISSION and RESTORE.CUSTODY.ADMISSION is ADMISSION,
                'canonical restore admission family required')
        result['stage'] = 'storage-policy'
        try:
            policy = BOOT_POLICY.Policy(boot_id)
        except FileNotFoundError as error:
            if error.filename != BOOT_POLICY.NAME:
                raise
            result.update(stage='unconfigured', unavailable_reason='protected storage policy absent')
            return result
        value = policy.value
        policy_pin = digest(BOOT_POLICY.SCHEMA.canonical(value))
        result.update(boot_id=boot_id, policy_sha256=policy_pin, storage=copy.deepcopy(value['storage']))
        guard()
        result['stage'] = 'admission'
        gid = NORMAL.local_kvm_gid()
        try:
            admission = ADMISSION.Admission.open_existing(boot_id, gid, NORMAL.manager_state)
        except FileNotFoundError as error:
            if error.filename != 'a733-admission':
                raise
            guard()
            admission = ADMISSION.initialize(boot_id, gid, NORMAL.manager_state)
        owner = admission.bootstrap_owner
        if owner is None:
            record = _noop(admission, policy, boot_id, policy_pin, guard)
            result.update(success=True, noop=True, admission_open_acknowledged=True,
                          stage='complete', eligible=record['eligible'], units=record['units'], budget=record['budget'])
            opened = True
        else:
            lease, nonce = owner.lease, owner.nonce
            authority()
            result['stage'] = 'activate-storage'
            args = SimpleNamespace(target=value['target'], expected_model=value['expected_model'],
                expected_bytes=value['expected_bytes'], expected_serial=value['storage']['nvme_serial'],
                guest_uuid=value['storage']['guest_uuid'])
            plan = ACTIVATE.make_plan(args)
            require(plan['context']['state']['uuid'] == value['storage']['state_uuid'], 'activation STATE differs')
            activation = ACTIVATE.apply_bootstrap(args, plan, owner, plan_check=authority)
            completed(activation, 'storage activation or cleanup failed')
            authority()
            result['stage'] = 'restore-census'
            result['restore_census'] = restore_census(value['storage'], lease, boot_id, authority)
            authority()
            result['stage'] = 'state'
            prepared = STATE.prepare(owner, value['storage'])
            completed(prepared, 'state preparation failed')
            result['stage'] = 'accounts'
            accounts = ACCOUNTS.prepare(owner, value['storage'])
            completed(accounts, 'accounts preparation failed')
            result['stage'] = 'ready-cohorts'
            controller = LIFECYCLE.StateController(value['storage'], lease, boot_id=boot_id)
            def context_guard():
                authority()
                controller.context.guard()
                authority()
            registry = _registry(controller, context_guard)
            # Manual beta still authenticates shared storage, restore census,
            # accounts, admission and attestation. It never reconciles managed
            # cohorts or publishes their launch units during boot.
            initial_names = [entry['name'] for entry in registry['guests']] if managed else []
            eligible = []
            for name in initial_names:
                context_guard()
                entry = next(e for e in registry['guests'] if e['name'] == name)
                try:
                    head = _observe_head(controller, entry, context_guard)
                    require(head['status'] != 'quarantined', 'head quarantined; explicit recovery required')
                    if head['status'] != 'ready' or head['sequence'] > 0:
                        reconciled = CP.reconcile(controller, name)
                        require(reconciled.get('success') is True and not reconciled.get('close_error'),
                                'checkpoint reconciliation failed')
                    registry = _registry(controller, context_guard)
                    entry = next(e for e in registry['guests'] if e['name'] == name)
                    _ready(controller, entry, context_guard)
                    if entry['enabled']:
                        eligible.append(name)
                except (ValueError, OSError, ADMISSION.AdmissionError, LIFECYCLE.OperationError) as error:
                    if getattr(error, 'receipt', {}).get('close_error'):
                        raise CleanupError('checkpoint cleanup failed', primary=error) from error
                    nested = getattr(error, 'cause', None)
                    if isinstance(nested, OSError) and not isinstance(nested, FileNotFoundError):
                        # Older checkpoint helpers cannot always tag their first
                        # close failure. Refuse global preparation on ambiguous I/O.
                        raise CleanupError('checkpoint I/O or cleanup failed', primary=error) from error
                    # Guest-specific failure grants no inferred durable quarantine.
                    result['unavailable'][name] = str(error)[:192]
                context_guard()  # Global faults cannot become per-guest availability.
                registry = _registry(controller, context_guard)
                require([e['name'] for e in registry['guests']] == initial_names, 'registry names changed')
            result['stage'] = 'unit-format'
            configs = _configs(controller, registry['guests'], context_guard) if managed else []
            memory = physical_memory()
            budget = dict(UNITS.check_budget(configs, memory))
            texts = {UNITS.SLICE_NAME: UNITS.render_slice(memory).encode('ascii')}
            for config in configs:
                if config['name'] in eligible:
                    texts['a733-managed-' + config['name'] + '.service'] = UNITS.render_managed(config).encode('ascii')
            result.update(eligible=sorted(eligible), budget=budget)
            context_guard()
            item, controller = controller, None
            item.close()
            authority()
            result['stage'] = 'manual-namespaces'
            MANUAL.prepare_runtime(owner)
            authority()
            result['stage'] = 'ram-publication'
            with Resources() as publications:
                units = publications.enter_context(RamDirectory(
                    UNIT_DIRECTORY, 0o755, authority, create=True))
                sources = []
                for name, data in sorted(texts.items()):
                    result['units'][name] = publish(units, name, data, 0o444, UNIT_LIMIT)
                    sources.append(publications.enter_context(Source(
                        units.fd, name, 0o444, UNIT_LIMIT, units.check)))
                    require(sources[-1].data == data, 'retained unit differs')
                record = dict(schema=1, profile=PROFILE, boot_id=boot_id, policy_sha256=policy_pin,
                    storage=value['storage'], units=result['units'], eligible=result['eligible'], budget=budget)
                data = encoded(record)
                prepared_record(data)
                directory = publications.enter_context(RamDirectory(
                    PREPARED_DIRECTORY, 0o700, authority, create=True))
                publish(directory, RECORD, data, 0o400, RECORD_LIMIT)
                sources.append(publications.enter_context(Source(
                    directory.fd, RECORD, 0o400, RECORD_LIMIT, directory.check)))
                require(sources[-1].data == data, 'retained prepared receipt differs')
                result['stage'] = 'final-consumers'
                MAINTENANCE.consumer_check()
                authority()
                attestation = MANUAL.Attestation(admission)
                try:
                    attestation.recheck()
                finally:
                    attestation.close()
                for source in sources:
                    source.check()
                authority()
            # Every borrowed source/context closes before the open commit. Only
            # the original admission's own descriptors survive that boundary.
            result['stage'] = 'precommit-cleanup'
            policy.check()
            item, policy = policy, None
            item.close()
            authority()
            result['stage'] = 'open-admission'
            result['admission_open_attempted'] = True
            try:
                owner.open()
            except BaseException as error:
                result['admission_open_uncertain'] = bool(getattr(error, 'uncertain', True))
                raise
            opened = True
            result.update(success=True, admission_open_acknowledged=True, stage='complete')
    except BaseException as error:
        cause = error
        result.update(error=type(error).__name__ + ': ' + str(error)[:1024], failure=failure_detail(error))
        if isinstance(error, CleanupError) and error.cleanup is not None:
            result['cleanup_errors'].append(dict(resource='preparation',
                                                error=str(error.cleanup)[:512]))
        if hasattr(error, 'receipt'):
            result['nested_stage'] = error.receipt.get('stage')
    finally:
        # Do not mutate preparation after open; these closes own FDs only.
        for name in ('controller', 'policy', 'admission'):
            item = {'controller': controller, 'policy': policy, 'admission': admission}[name]
            if item is not None:
                try:
                    creator_guard()
                    item.close()
                except BaseException as error:
                    result['cleanup_errors'].append(dict(resource=name, error=str(error)[:512]))
        if result['cleanup_errors']:
            result['success'] = False
            result['stage'] = 'cleanup-after-open' if opened else 'cleanup'
        if cause is not None:
            result['success'] = False
    require(len(encoded(result)) <= RECORD_LIMIT, 'prepare receipt bound exceeded')
    return copy.deepcopy(result)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    receipt = prepare()
    print(encoded(receipt).decode('ascii'), end='')
    return 0 if receipt['success'] else 1


if __name__ == '__main__':
    sys.exit(main())
