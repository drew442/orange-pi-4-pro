#!/usr/bin/python3
"""Stream a root-authenticated managed ready cohort to a remote receiver.

Discard every partial stream. This exports historical source policy; it grants
no destination admission, restore, filesystem cleanliness or hardware authority.
"""
import argparse
from contextlib import ExitStack
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import stat
import sys
import threading

BASE = Path(__file__).resolve().parents[1]


def module(name, relative):
    spec = importlib.util.spec_from_file_location(name, BASE / relative)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


MAINT = module('managed_sender_maintenance', 'scripts/guest-host-maintenance.py')
CP = MAINT.CP
LIFECYCLE, POLICY = CP.LIFECYCLE, CP.POLICY
CONTEXT, ADMISSION = LIFECYCLE.CONTEXT, LIFECYCLE.ADMISSION
HEAD, REGISTRY, ENROLL = CP.HEAD, CP.REGISTRY, CP.ENROLL
FORMAT = module('managed_sender_format', 'configs/guest_managed_backup.py')
LEGACY = module('managed_sender_stream_helpers', 'scripts/backup-guest.py')
require = CP.require
CHUNK = LEGACY.CHUNK


class ProtectedRecords:
    """Retain exact named inodes using independent physical-only store guards."""
    def __init__(self, stack):
        self.stack, self.files = stack, []

    def read(self, store, name, limit):
        store.check()
        initial = os.stat(name, dir_fd=store.fd, follow_symlinks=False)
        CP.STORE._regular(initial, os.fstat(store.fd).st_dev, limit)
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                     dir_fd=store.fd)
        self.stack.callback(os.close, fd)
        raw = store._read_retained(fd, name, initial, limit, None)
        self.files.append((store, name, fd, initial, limit, raw))
        return raw

    def recheck(self):
        for store, name, fd, initial, limit, raw in self.files:
            require(store._read_retained(fd, name, initial, limit, CP.digest(raw)) == raw,
                    'retained lifecycle record changed')


def write_entry(stream, name, data):
    stream.write(FORMAT.make_header(name, len(data)))
    stream.write(data)
    stream.write(bytes(-len(data) % 512))


def stream_file(stream, inspection, key, name):
    fd = inspection.fds[key]
    expected = inspection.manifest['config'][key]
    before = os.fstat(fd)
    inspection._held(fd)
    stream.write(FORMAT.make_header(name, expected['bytes']))
    os.lseek(fd, 0, os.SEEK_SET)
    remaining, digest = expected['bytes'], hashlib.sha256()
    while remaining:
        data = os.read(fd, min(CHUNK, remaining))
        require(bool(data), 'guest input truncated')
        remaining -= len(data)
        digest.update(data)
        stream.write(data)
    require(not os.read(fd, 1), 'guest input grew')
    require(digest.hexdigest() == expected['sha256'], name + ' SHA-256 differs during backup')
    require(ENROLL.identity(before) == ENROLL.identity(os.fstat(fd)), 'guest input metadata changed')
    inspection._held(fd)
    stream.write(bytes(-expected['bytes'] % 512))


class Source:
    """Own SH admission, guest EX, stopped disk EX and protected source readers."""
    def __init__(self, name, pin):
        self.stack = ExitStack()
        self.pid, self.thread = os.getpid(), threading.get_ident()
        self.closed = False
        self.name, self.pin = name, pin
        try:
            require(os.getresuid() == (0, 0, 0), 'actual root required for managed backup')
            ADMISSION._guest_name(name)
            POLICY.MANIFEST.digest(pin)
            boot = CONTEXT.read_system('/proc/sys/kernel/random/boot_id', 64).decode('ascii')
            require(boot.endswith('\n'), 'invalid host boot UUID')
            self.boot_id = ADMISSION._uuid(boot[:-1])
            CONTEXT.host_identity(self.boot_id)
            CONTEXT.MANAGEMENT.runtime_guard()
            storage = MAINT.registered_storage()
            self.admission = ADMISSION.Admission.open_existing(
                self.boot_id, MAINT.NORMAL.local_kvm_gid(), MAINT.NORMAL.manager_state)
            self.stack.callback(self.admission.close)
            self.lease = self.admission.admit()
            self.stack.callback(self.lease.close)
            self.lock = self.stack.enter_context(self.admission.guest_lock(name, self.lease, create=False))
            self.controller = self.stack.enter_context(LIFECYCLE.StateController(
                storage, self.lease, boot_id=self.boot_id))
            self.inspection = self.stack.enter_context(POLICY.Inspection(name, pin))
            self.physical()
            self.identity = self.controller._inspection(self.inspection, name, pin)
            self.store = self.controller._head_store(self.stack, name, self.physical)
            self.records = ProtectedRecords(self.stack)
            self.head_raw = self.records.read(self.store, 'head.json', HEAD.LIMIT)
            self.head = HEAD.validate_head(self.head_raw)
            self.controller._storage(self.head)
            require(HEAD.canonical(self.head) == self.head_raw and self.head['status'] == 'ready' and
                    self.head['manifest_sha256'] == pin and
                    all(self.head[k] == v for k, v in self.identity.items()), 'ready head differs')
            self.registry_raw, self.registry, self.entry = self.registry_snapshot()
            self.payload_records = {}
            if self.head['sequence'] > 0:
                runs = self.stack.enter_context(self.store.child('runs'))
                run_id = self.head['run_id']
                limits = {'.json': HEAD.LIMIT, '.console': CP.RECORDS.CONSOLE_LIMIT,
                          '.outcome.json': CP.RECORDS.OUTCOME_LIMIT,
                          '.intent.json': CP.RECORDS.INTENT_LIMIT,
                          '.completed.json': CP.RECORDS.COMPLETION_LIMIT}
                if self.entry['fixture_profile'] != 'none':
                    limits['.fixture.json'] = CP.RECORDS.FIXTURE_LIMIT
                raw = {}
                for suffix, limit in limits.items():
                    raw[suffix] = self.records.read(runs, run_id + suffix, limit)
                    self.payload_records['runs/' + run_id + suffix] = CP.reference(raw[suffix], embedded=True)
                admission = LIFECYCLE.validate_run(raw['.json'])
                intent = CP.RECORDS.validate_intent(raw['.intent.json'])
                heads = CP._heads(admission, raw['.intent.json'], intent)
                transitions = self.stack.enter_context(self.store.child('transitions'))
                for previous, new in zip(heads, heads[1:]):
                    old_raw, new_raw = HEAD.canonical(previous), HEAD.canonical(new)
                    filename = CP.digest(old_raw) + '-' + CP.digest(new_raw) + '.json'
                    edge = self.records.read(transitions, filename, LIFECYCLE.TRANSITION_LIMIT)
                    require(edge == LIFECYCLE.head_transition(old_raw, new_raw), 'illegal head edge')
                    self.payload_records['transitions/' + filename] = CP.reference(edge, embedded=True)
            self.history = LEGACY.History(self.inspection)
            self.stack.callback(self.history.close)
            # This codec validates framing only. Actual CP authentication precedes it.
            self.recheck()
            self.lifecycle = FORMAT.canonical(dict(schema=1, profile=FORMAT.PROFILE,
                registry=CP.reference(self.registry_raw, embedded=True),
                head=CP.reference(self.head_raw, embedded=True), records=self.payload_records),
                self.inspection.manifest_data)
        except BaseException:
            self.close()
            raise

    def physical(self):
        require(not self.closed and (os.getpid(), threading.get_ident()) == (self.pid, self.thread),
                'managed backup source unavailable')
        require(os.getresuid() == (0, 0, 0), 'actual root required for managed backup')
        CONTEXT.host_identity(self.boot_id)
        CONTEXT.MANAGEMENT.runtime_guard()
        require(type(self.lease) is ADMISSION.Lease and not self.lease.exclusive and
                type(self.lock) is ADMISSION.GuestLock, 'actual shared lease and guest lock required')
        self.lease.recheck()
        self.lock.recheck()
        ADMISSION._held_lock(self.lease.fd, 'READ', self.pid)
        ADMISSION._held_lock(self.lock.fd, 'WRITE', self.pid)
        self.controller.context.guard()
        require(self.admission.is_managed(self.name, self.lease, self.lock), 'managed marker missing')

    def registry_snapshot(self):
        with ExitStack() as stack:
            lock = stack.enter_context(self.admission.registry_lock(self.lease))
            def guard():
                self.physical()
                lock.recheck()
            store, raw, registry = self.controller._registry(stack, guard)
            require(registry is not None and REGISTRY.canonical(registry) == raw,
                    'canonical registered guest required')
            matches = [entry for entry in registry['guests'] if entry['name'] == self.name]
            require(len(matches) == 1, 'registered guest missing')
            entry = matches[0]
            require(entry['shutdown_profile'] == 'qmp-guest-shutdown-v1' and
                    entry['fixture_profile'] in ('none', CP.FIXTURE.PROFILE),
                    'unsupported managed source profiles')
            require(entry['manifest_sha256'] == self.pin and
                    all(entry[k] == v for k, v in self.identity.items()), 'registered identity/pin differs')
            if hasattr(self, 'entry'):
                require({k: v for k, v in entry.items() if k != 'enabled'} ==
                        {k: v for k, v in self.entry.items() if k != 'enabled'},
                        'registered source configuration changed')
            guard()
        return raw, registry, entry

    def recheck(self):
        self.physical()
        self.controller._inspection(self.inspection, self.name, self.pin)
        self.records.recheck()
        self.history.recheck()
        for key, fd in self.inspection.fds.items():
            expected = self.inspection.manifest['config'][key]
            require(self.inspection._hash(fd, expected['bytes']) == expected,
                    'source file hash changed')
        self.registry_snapshot()
        # CP hashes/authenticates the retained disk before taking its own short
        # registry EX; never hold that lock over disk IO or archive streaming.
        if self.head['sequence'] > 0:
            CP.authenticate_ready_cohort(self.controller, self.name, guest_lock=self.lock,
                inspection=self.inspection, head_store=self.store, head_data=self.head_raw)
        self.physical()

    def close(self):
        require((os.getpid(), threading.get_ident()) == (self.pid, self.thread),
                'managed backup source belongs to another process or thread')
        if not self.closed:
            # All callbacks run even if a close acknowledgement fails.
            try:
                self.stack.close()
            finally:
                self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.close()


def send(name, pin, writer):
    encoded = LEGACY.EncodedWriter(writer)
    with Source(name, pin) as source:
        # Source construction authenticates the complete cohort before gzip emits bytes.
        with gzip.GzipFile(filename='', mode='wb', compresslevel=9, mtime=0,
                           fileobj=encoded) as archive:
            write_entry(archive, 'enrollment.json', source.inspection.manifest_data)
            write_entry(archive, 'checkpoint-history.json', source.history.payload)
            write_entry(archive, 'lifecycle.json', source.lifecycle)
            for key, filename in ENROLL.FILENAMES.items():
                stream_file(archive, source.inspection, key, filename)
            archive.write(bytes(1024))
        encoded.flush()
        source.recheck()
        result = dict(schema=1, operation=FORMAT.PROFILE, success=True, name=name,
            archive=dict(bytes=encoded.count, sha256=encoded.digest.hexdigest()),
            manifest_sha256=pin, registry_sha256=CP.digest(source.registry_raw),
            registry_generation=source.registry['generation'], head_sha256=CP.digest(source.head_raw),
            lifecycle_sha256=CP.digest(source.lifecycle), history_count=len(source.history.entries),
            scope='authenticated-source-cohort-only', restore_verified=False,
            filesystem_consistency_verified=False, hardware_acceptance=False)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('name')
    parser.add_argument('--manifest-sha256', required=True)
    args = parser.parse_args(argv)
    LEGACY.stdout_guard(sys.stdout.buffer)
    def interrupted(_signum, _frame):
        raise KeyboardInterrupt
    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        result = send(args.name, args.manifest_sha256, sys.stdout.buffer)
        sys.stderr.write(json.dumps(result, sort_keys=True) + '\n')
        sys.stderr.flush()
        return 0
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, UnicodeError, KeyboardInterrupt,
            ADMISSION.AdmissionError, LIFECYCLE.OperationError) as error:
        print(json.dumps(dict(success=False, error=type(error).__name__,
                              partial_stream_must_be_discarded=True)), file=sys.stderr)
        sys.exit(1)
