#!/usr/bin/python3
"""Explicit initial storage-policy publication; never initialize or launch guests.

Only a host with no admission runtime is eligible. Replacing storage after runtime
initialization needs a separately reviewed offline workflow. Live observations
are read-only software checks, not hardware qualification.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
import stat
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

BASE = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location('configure_storage_prepare', BASE / 'scripts/prepare-guest-boot.py')
PREP = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(PREP)
ADMISSION, STORE, CONTEXT = PREP.ADMISSION, PREP.STORE, PREP.CONTEXT
POLICY, ACTIVATE, REGISTRY = PREP.BOOT_POLICY, PREP.ACTIVATE, PREP.REGISTRY
SCHEMA, require = POLICY.SCHEMA, PREP.require


def installed_source(boot_id):
    """Authenticate this entrypoint as part of PREP's read-only installed tree."""
    PREP.installed_guard(boot_id)
    require(BASE == PREP.BASE == Path('/usr/lib/a733-tools'), 'installed publisher required')
    info = os.lstat(__file__)
    require(stat.S_ISREG(info.st_mode) and info.st_uid == info.st_gid == 0 and
            not info.st_mode & 0o022 and info.st_dev == os.stat('/').st_dev and
            info.st_nlink == 1, 'unsafe installed publisher source')
    return STORE._metadata(info)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def progress(phase):
    print('storage policy: ' + phase, file=sys.stderr, flush=True)


class MetadataSource(PREP.Source):
    """Keep the existing source checks and preserve failed-constructor cleanup."""
    def close(self):
        primary = sys.exc_info()[1]
        try:
            super().close()
        except BaseException as error:
            raise PREP.CleanupError('metadata source cleanup uncertain',
                                    primary=primary, cleanup=error) from error


class MetadataDirectory(STORE.RootDirectory):
    """Read-only RootDirectory construction with classified descriptor cleanup.

    The shared store constructor's optional mkdir path is intentionally absent:
    the installed STATE layout must already provide config. Publication and sync
    remain the unchanged shared store methods.
    """
    def __init__(self, parent_fd, name, *, guard):
        STORE._name(name)
        STORE._require(callable(guard), 'guard must authenticate placement and locks')
        self.guard, self._fds, self._edges, self._closed = guard, [], [], False
        try:
            parent = os.dup(parent_fd)
            self._fds.append(parent)
            self._base_identity = STORE._inode(os.fstat(parent))
            STORE._directory(os.fstat(parent), False)
            self._check_ancestors()
            guard()
            before = os.stat(name, dir_fd=parent, follow_symlinks=False)
            STORE._directory(before, True)
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW |
                            os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
            self._fds.append(child)
            self.fd = child
            self._edges.append((parent, name, child, STORE._inode(before)))
            self.check()
        except BaseException:
            self.close()
            raise

    def close(self):
        primary = sys.exc_info()[1]
        try:
            super().close()
        except BaseException as error:
            raise PREP.CleanupError('metadata directory cleanup uncertain',
                                    primary=primary, cleanup=error) from error


class StateRoot:
    """Read-only no-follow STATE walk, retaining physical and mount identity."""
    def __init__(self, guard):
        self.guard, self.chain = guard, []
        try:
            parts = Path(POLICY.STATE).parts
            require(parts and parts[0] == '/' and str(Path(POLICY.STATE)) == POLICY.STATE,
                    'canonical STATE path required')
            parent = None
            for name in ('/',) + parts[1:]:
                guard()
                fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW |
                             os.O_CLOEXEC, dir_fd=parent)
                self.chain.append((parent, name, fd, CONTEXT.physical_identity(os.fstat(fd))))
                parent = fd
            self.fd = parent
            self.mount = self._mount()
            self.check()
        except BaseException:
            self.close()
            raise

    def _mount(self):
        storage = CONTEXT.STORAGE
        rows = storage.mount_table(storage.read_bounded('/proc/self/mountinfo', storage.MOUNTINFO_LIMIT))
        require('/' in rows and POLICY.STATE in rows, 'selected root/STATE mount missing')
        row = rows[POLICY.STATE]
        info = os.fstat(self.fd)
        require((os.major(info.st_dev), os.minor(info.st_dev)) == row['device'] and
                storage.fd_mount_id(storage.read_bounded('/proc/self/fdinfo/' + str(self.fd),
                    storage.FDINFO_LIMIT)) == row['mount_id'], 'held STATE mount differs')
        return copy.deepcopy({path: rows[path] for path in ('/', POLICY.STATE)})

    def check(self):
        self.guard()
        for parent, name, fd, identity in self.chain:
            info = os.fstat(fd)
            CONTEXT.ENROLL.trusted_directory(info)
            ADMISSION._readonly(fd)
            require(CONTEXT.physical_identity(info) == identity, 'STATE directory changed')
            if parent is not None:
                require(CONTEXT.physical_identity(os.stat(name, dir_fd=parent,
                    follow_symlinks=False)) == identity, 'STATE named directory changed')
        require(self._mount() == self.mount, 'root/STATE mount changed')

    def close(self):
        chain, self.chain = self.chain, []
        errors = []
        for _, _, fd, _ in reversed(chain):
            try:
                os.close(fd)
            except BaseException as error:
                errors.append(error)
        if errors:
            raise PREP.CleanupError('STATE descriptor cleanup uncertain',
                                    primary=sys.exc_info()[1], cleanup=errors[0])


def registry_check(state, guard):
    """Missing exact directory/leaf is initial unconfigured state only."""
    with PREP.Resources() as resources:
        try:
            store = MetadataDirectory(state.fd, 'guests', guard=guard)
        except FileNotFoundError as error:
            if error.filename != 'guests':
                raise
            guard()
            return
        resources.callback(store.close)
        try:
            source = MetadataSource(store.fd, 'autostart.json', 0o400, REGISTRY.LIMIT, store.check)
            resources.callback(source.close)
            raw = source.data
            source.check()
        except FileNotFoundError as error:
            if error.filename != 'autostart.json':
                raise
            store.check()
            return
        value = REGISTRY.validate_registry(raw)
        require(REGISTRY.canonical(value) == raw, 'noncanonical managed registry')
        return value['storage']


def configure(value):
    """Publish initial explicit expected identities; return bounded JSON receipt."""
    receipt = dict(schema=1, operation='configure-initial-guest-storage', success=False,
                   stage='validate', published=False, durable=False, uncertain=False,
                   cleanup_uncertain=False, cleanup_errors=[], idempotent=False)
    creator = os.getpid(), threading.get_ident()
    try:
        require(os.getresuid() == (0, 0, 0), 'actual root required')
        require(threading.current_thread() is threading.main_thread(), 'original main thread required')
        data = SCHEMA.canonical(value)
        expected = SCHEMA.decode(data)
        receipt.update(target=expected['target'], expected_model=expected['expected_model'],
                       expected_bytes=expected['expected_bytes'], storage=expected['storage'],
                       policy_sha256=digest(data))
        args = SimpleNamespace(target=expected['target'], expected_model=expected['expected_model'],
            expected_serial=expected['storage']['nvme_serial'], expected_bytes=expected['expected_bytes'],
            guest_uuid=expected['storage']['guest_uuid'])
        boot_id = ADMISSION._boot_id()
        source_pin = None

        def creator_guard():
            require((os.getpid(), threading.get_ident()) == creator and
                    threading.current_thread() is threading.main_thread(), 'creator changed')
            require(os.getresuid() == (0, 0, 0), 'actual root required')
            require(SCHEMA.canonical(value) == data, 'caller policy changed')
            require(installed_source(boot_id) == source_pin, 'installed publisher changed')

        receipt['stage'] = 'installed-host'
        progress('validate installed host and acquire initialization exclusion')
        source_pin = installed_source(boot_id)
        creator_guard()
        with PREP.Resources() as outer:
            lock = ADMISSION.InitializationLock.acquire()
            outer.callback(lock.close)

            def absent_guard():
                creator_guard()
                lock.check()
                # lstat through the authenticated held /run FD: symlinks count.
                try:
                    os.stat('a733-admission', dir_fd=lock.parent, follow_symlinks=False)
                except FileNotFoundError:
                    return
                raise ValueError('existing admission runtime prohibits initial configuration')

            absent_guard()
            receipt['stage'] = 'storage-preflight'
            progress('validate storage and absence of VM consumers')
            PREP.MAINTENANCE.consumer_check()
            initial = ACTIVATE.snapshot(args, mounted=bool(ACTIVATE.fixed_entries()))
            require(initial['state']['uuid'] == expected['storage']['state_uuid'], 'STATE UUID differs')

            def live_guard():
                absent_guard()
                require(ACTIVATE.snapshot(args, mounted=bool(ACTIVATE.fixed_entries())) == initial,
                        'selected storage context changed')

            # Nested resources must finish before the initialization lock closes.
            with PREP.Resources() as resources:
                state = StateRoot(live_guard)
                resources.callback(state.close)
                def guard():
                    state.check()
                registered = registry_check(state, guard)
                require(registered is None or registered == expected['storage'], 'registry storage differs')
                receipt['stage'] = 'policy-directory'
                progress('authenticate policy directory')
                config = MetadataDirectory(state.fd, 'config', guard=guard)
                resources.callback(config.close)
                try:
                    source = MetadataSource(config.fd, POLICY.NAME, 0o400, SCHEMA.LIMIT, config.check)
                except FileNotFoundError as error:
                    if error.filename != POLICY.NAME:
                        raise
                    source = None
                policy = None
                if source is not None:
                    resources.callback(source.close)
                    require(source.data == data, 'existing policy differs')
                    policy = POLICY.Policy(boot_id)
                    resources.callback(policy.close)
                    require(policy.value == expected, 'authenticated existing policy differs')
                    policy.check()
                    receipt['idempotent'] = True
                    receipt['stage'] = 'existing-policy-sync'
                    progress('synchronize identical protected policy')
                    source.check()
                    try:
                        synchronization = config.sync(POLICY.NAME,
                            pin=receipt['policy_sha256'], limit=SCHEMA.LIMIT)
                    except STORE.PublicationError as error:
                        receipt['synchronization'] = copy.deepcopy(error.receipt)
                        receipt.update(durable=error.receipt['durable'], uncertain=True)
                        raise
                    receipt.update(synchronization=synchronization,
                                   durable=synchronization['durable'])
                    source.check()
                else:
                    receipt['stage'] = 'publication'
                    progress('publish immutable policy and synchronize')
                    try:
                        publication = config.publish(POLICY.NAME, data)
                    except STORE.PublicationError as error:
                        receipt['publication'] = copy.deepcopy(error.receipt)
                        receipt.update(published=error.receipt['published'],
                                       durable=error.receipt['durable'], uncertain=True)
                        raise
                    receipt.update(publication=publication, published=publication['published'],
                                   durable=publication['durable'])
                    source = MetadataSource(config.fd, POLICY.NAME, 0o400, SCHEMA.LIMIT, config.check)
                    resources.callback(source.close)
                    require(source.data == data and digest(source.data) == receipt['policy_sha256'],
                            'published policy bytes differ')
                    source.check()
                receipt['stage'] = 'final-authentication'
                progress('authenticate published policy and final storage context')
                if policy is None:
                    policy = POLICY.Policy(boot_id)
                    resources.callback(policy.close)
                require(policy.value == expected, 'authenticated policy differs')
                policy.check()
                config.check()
                PREP.MAINTENANCE.consumer_check()
                registered = registry_check(state, guard)
                require(registered is None or registered == expected['storage'], 'registry storage differs')
                source.check()
                policy.check()
                guard()
                receipt['stage'] = 'cleanup'
        receipt.update(success=True, stage='complete')
    except BaseException as error:
        receipt.update(error=str(error)[:1024], error_type=type(error).__name__)
        receipt['failure'] = PREP.failure_detail(error)
        if isinstance(error, (PREP.CleanupError, POLICY.CleanupError)):
            receipt.update(cleanup_uncertain=True, uncertain=True, durable=False)
        if isinstance(error, STORE.PublicationError):
            key = ('synchronization' if error.receipt.get('operation') == 'sync-existing-record'
                   else 'publication')
            receipt[key] = copy.deepcopy(error.receipt)
            receipt['uncertain'] = True
            publication = error.receipt
            if (publication.get('close_error') or publication.get('cleanup_error') or
                    publication.get('operation') == 'publish' and
                    publication.get('mutation_possible') and
                    not publication.get('temporary_removed')):
                receipt.update(cleanup_uncertain=True, durable=False)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', required=True, choices=[SCHEMA.TARGET])
    parser.add_argument('--expected-model', required=True)
    parser.add_argument('--expected-serial', required=True)
    parser.add_argument('--expected-bytes', required=True, type=int)
    parser.add_argument('--state-uuid', required=True)
    parser.add_argument('--guest-uuid', required=True)
    args = parser.parse_args(argv)
    result = configure(dict(schema=1, profile=SCHEMA.PROFILE, target=args.target,
        expected_model=args.expected_model, expected_bytes=args.expected_bytes,
        storage=dict(state_uuid=args.state_uuid, guest_uuid=args.guest_uuid,
                     nvme_serial=args.expected_serial)))
    print(json.dumps(result, sort_keys=True, separators=(',', ':'), allow_nan=False))
    return 0 if result['success'] else 1


if __name__ == '__main__':
    sys.exit(main())
