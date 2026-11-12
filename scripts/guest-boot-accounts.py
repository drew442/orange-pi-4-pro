"""Prepare RAM accounts for protected manual and managed enrollments under EX.

Discovery grants no launch permission. The caller owns the original bootstrap
lease; this helper owns only its physical context and retained input descriptors.
"""
import copy
from contextlib import ExitStack
import importlib.util
import os
from pathlib import Path
import stat
import threading

BASE = Path(__file__).resolve().parents[1]


def module(name, relative):
    spec = importlib.util.spec_from_file_location(name, BASE / relative)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


CP = module('boot_accounts_checkpoint', 'scripts/managed-guest-checkpoint.py')
LIFECYCLE = CP.LIFECYCLE
CONTEXT, ADMISSION = LIFECYCLE.CONTEXT, LIFECYCLE.ADMISSION
STORE, REGISTRY = LIFECYCLE.STORE, LIFECYCLE.REGISTRY
ACCOUNTS, MANIFEST = CONTEXT.POLICY.ACCOUNTS, CONTEXT.POLICY.MANIFEST
require, digest = ADMISSION.require, LIFECYCLE.digest
NAME_LIMIT, METADATA_LIMIT, BATCH_LIMIT = 256, 8 * 1024 * 1024, 16


class PreparationError(RuntimeError):
    def __init__(self, receipt, cause):
        self.receipt, self.cause = copy.deepcopy(receipt), cause
        if hasattr(cause, 'receipt'):
            self.receipt['nested_error'] = copy.deepcopy(cause.receipt)
        if isinstance(cause, ADMISSION.PublicationError):
            self.receipt['publication_error'] = dict(stage=cause.stage, uncertain=cause.uncertain)
        super().__init__(f"{receipt['stage']}: {type(cause).__name__}: {cause}")


def prepare(owner, expected_storage):
    """Apply fresh sorted <=16 account plans, preserving all other database rows.

    Guards bind this operation's actual PID/thread. ClosureOwner does not record
    its creation thread; trusted root callers must prevent prior thread handoff.
    No disk reads, repairs, marker publications, units or admission opening occur.
    """
    result = dict(operation='bootstrap-accounts', stage='validate', success=False,
                  hardware_acceptance=False, run_permission=False, batches=[],
                  identities=[], manifest_pins={}, cleanup_errors=[])
    creator = os.getpid(), threading.get_ident()
    admission = lease = nonce = controller = None
    failure = None

    def creator_only():
        require((os.getpid(), threading.get_ident()) == creator,
                'bootstrap preparation belongs to another process or thread')

    def authority():
        creator_only()
        require(os.getresuid() == (0, 0, 0), 'actual root required')
        require(CONTEXT.module('boot_accounts_admission', 'scripts/guest-admission.py') is ADMISSION,
                'admission source differs')
        require(type(owner) is ADMISSION.ClosureOwner, 'canonical bootstrap owner required')
        require(type(admission) is ADMISSION.Admission and
                admission.bootstrap_owner is owner and owner.admission is admission,
                'original bootstrap owner required')
        require(owner.reason == 'bootstrap' and owner.nonce == nonce, 'bootstrap ownership differs')
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

    class Resources(ExitStack):
        def __exit__(self, error_type, error, traceback):
            creator_only()
            try:
                return super().__exit__(error_type, error, traceback)
            except BaseException as close_error:
                if error is None:
                    raise
                result['cleanup_errors'].append(dict(type=type(close_error).__name__,
                                                     error=str(close_error)))
                return False

    try:
        require(type(owner) is ADMISSION.ClosureOwner, 'canonical bootstrap owner required')
        admission, lease, nonce = owner.admission, owner.lease, owner.nonce
        authority()
        storage = CONTEXT.validate_storage(expected_storage)
        result['stage'] = 'physical-state'
        controller = LIFECYCLE.StateController(storage, lease, boot_id=admission.boot_id)
        guard()
        with Resources() as resources:
            registry_store, registry_raw, registry = controller._registry(resources, guard)
            require(registry is not None, 'prepared registry required')
            require(REGISTRY.canonical(registry) == registry_raw, 'noncanonical registry')
            result['registry_sha256'] = digest(registry_raw)
            retained_bytes = 0

            def hold(parent, name, limit):
                nonlocal retained_bytes
                before = os.stat(name, dir_fd=parent, follow_symlinks=False)
                STORE._regular(before, os.fstat(parent).st_dev, limit)
                require(0 < before.st_size and retained_bytes + before.st_size <= METADATA_LIMIT,
                        'retained metadata limit exceeded')
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK |
                             os.O_CLOEXEC, dir_fd=parent)
                resources.callback(os.close, fd)
                pinned = STORE._metadata(before)

                def read():
                    guard()
                    require(STORE._metadata(os.fstat(fd)) == pinned == STORE._metadata(
                        os.stat(name, dir_fd=parent, follow_symlinks=False)),
                        'protected metadata changed')
                    os.lseek(fd, 0, os.SEEK_SET)
                    raw = os.read(fd, limit + 1)
                    require(len(raw) == before.st_size, 'protected metadata size changed')
                    require(STORE._metadata(os.fstat(fd)) == pinned == STORE._metadata(
                        os.stat(name, dir_fd=parent, follow_symlinks=False)),
                        'protected metadata changed during read')
                    return raw
                raw = read()
                retained_bytes += len(raw)
                return raw, read

            # Retain the registry descriptor too: same-byte replacement is refused.
            held_registry, registry_read = hold(registry_store.fd, 'autostart.json', REGISTRY.LIMIT)
            require(held_registry == registry_raw, 'registry changed during discovery')
            registered = {entry['name']: entry for entry in registry['guests']}
            require('firstkvm' not in registered, 'manual firstkvm cannot be managed')
            require(len(registered) <= NAME_LIMIT, 'guest name limit exceeded')
            root_fd = controller.context.guest_fd

            def listing():
                guard()
                found = {}
                with os.scandir(root_fd) as entries:
                    for entry in entries:
                        require(len(found) < NAME_LIMIT, 'guest root entry limit exceeded')
                        info = entry.stat(follow_symlinks=False)
                        require(stat.S_ISDIR(info.st_mode) and info.st_dev == os.fstat(root_fd).st_dev,
                                'unsafe guest root child')
                        found[entry.name] = STORE._metadata(info)
                return found

            result['stage'] = 'protected-discovery'
            roots = listing()
            identities, readers, seen_ids = [], [], set()
            for name, meta in sorted(roots.items()):
                info = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
                # Root-private lifecycle, filesystem and partial-work metadata
                # never constitutes an exposed guest identity.
                if info.st_uid == info.st_gid == 0 and stat.S_IMODE(info.st_mode) == 0o700:
                    require(name not in registered, 'registered guest directory is private')
                    continue
                require(not name.startswith('.'), 'hidden exposed guest directory')
                ADMISSION._guest_name(name)
                require(info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o710,
                        'unsafe exposed guest directory')
                fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW |
                             os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=root_fd)
                resources.callback(os.close, fd)
                require(STORE._metadata(os.fstat(fd)) == meta, 'guest directory changed')
                raw, reader = hold(fd, 'enrollment.json', MANIFEST.LIMIT)
                value = MANIFEST.validate_manifest(raw)
                config = value['config']
                identity = {key: config[key] for key in ('name', 'uid', 'gid')}
                require(config['name'] == name and config['uid'] == config['gid'] == info.st_gid,
                        'enrollment directory identity differs')
                require(value['guest_uuid'] == storage['guest_uuid'], 'enrollment guest UUID differs')
                require(config['uid'] not in seen_ids, 'duplicate guest UID/GID')
                seen_ids.add(config['uid'])
                if name in registered:
                    require(all(registered[name][key] == identity[key] for key in identity) and
                            registered[name]['manifest_sha256'] == digest(raw),
                            'registry/enrollment identity or pin differs')
                identities.append(identity)
                readers.append((raw, reader))
                result['manifest_pins'][name] = digest(raw)
            require(registered.keys() <= {item['name'] for item in identities},
                    'registered enrollment missing')
            require(len(identities) <= NAME_LIMIT, 'guest name limit exceeded')
            result['identities'] = copy.deepcopy(identities)
            result['retained_metadata_bytes'] = retained_bytes

            def recheck():
                guard()
                registry_store.check()
                require(registry_read() == registry_raw, 'registry bytes changed')
                require(listing() == roots, 'guest root namespace changed')
                for raw, reader in readers:
                    require(reader() == raw, 'enrollment bytes changed')
                guard()

            recheck()
            for offset in range(0, len(identities), BATCH_LIMIT):
                result['stage'] = 'account-plan'
                recheck()
                data = ACCOUNTS.canonical(dict(schema=1, guests=identities[offset:offset+BATCH_LIMIT]))
                plan = ACCOUNTS.make_plan(data, ACCOUNTS.snapshot())
                recheck()
                result['stage'] = 'account-apply'
                batch = dict(names=[item['name'] for item in identities[offset:offset+BATCH_LIMIT]],
                             config_sha256=digest(data), attempted=True)
                result['batches'].append(batch)
                batch['receipt'] = copy.deepcopy(ACCOUNTS.apply_bootstrap(data, plan, owner))
                require(batch['receipt'].get('success') is True, 'account batch failed')
                recheck()
            result['stage'] = 'final-verification'
            recheck()
        guard()
    except BaseException as error:
        failure = error
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
