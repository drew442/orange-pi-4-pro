#!/usr/bin/python3
"""Read-only authenticated managed artifact source; never destination authority.

Root callers MUST already own their outer admission before constructing Source,
including before archive path access. This module borrows no admission and grants
no guest/storage/registry authority. Allowed devices come from the caller's
validated destination context. Shared flock and metadata checks assume cooperative
writers, not a hostile root. Payload callbacks must discard unpublished output on
ANY failure, including final footer, metadata, or callback failures.
"""
import fcntl
import hashlib
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


# One generic helper family; never substitute its legacy FORMAT global.
GENERIC = module('managed_source_generic', 'scripts/verify-guest-backup.py')
ENROLL = module('managed_source_enroll', 'scripts/enroll-guest.py')
DECODER = module('managed_source_decoder', 'scripts/managed-guest-archive.py')
FORMAT = DECODER.FORMAT
require = FORMAT.require
CHUNK = GENERIC.CHUNK
MAX_ARCHIVE = GENERIC.MAX_ARCHIVE


class SourceError(RuntimeError):
    """Bounded failure receipt retaining the original operation and cleanup errors."""
    def __init__(self, primary, cleanup):
        prior = []
        if isinstance(primary, SourceError):
            prior, primary = list(primary.cleanup), primary.primary
        self.primary = primary
        errors = prior
        for error in cleanup:
            if isinstance(error, SourceError):
                errors.extend((error.primary, *error.cleanup))
            else:
                errors.append(error)
        self.cleanup = tuple(errors[:8])

        def record(error):
            return dict(error=type(error).__name__[:128], message=str(error)[:512])

        self.receipt = dict(primary_error=record(primary),
                            cleanup_errors=[record(error) for error in self.cleanup],
                            cleanup_error_count=len(errors))
        super().__init__('managed source operation and cleanup failed')


def scan_backup(stream, manifest_sha256, emit=None, initial=None, metadata_ready=None):
    """Scan exact six-member profile from a fully authenticated encoded source.

    Caller authenticates ALL encoded bytes before constructing the decoder.
    metadata_ready is artifact extraction plumbing, never an authority callback.
    emit(name, chunk) runs only after full legal historical cohort validation;
    its output still requires final payload hashes, padding, gzip/footer and EOF.
    """
    FORMAT.MANIFEST.digest(manifest_sha256)
    raw = []
    for name in FORMAT.NAMES[:3]:
        size = FORMAT.read_header(stream, name)
        data = FORMAT.read_exact(stream, size)
        FORMAT.read_padding(stream, size)
        raw.append(data)
    manifest_data, history_data, lifecycle_data = raw
    require(hashlib.sha256(manifest_data).hexdigest() == manifest_sha256,
            'manifest digest mismatch')
    manifest = FORMAT.MANIFEST.validate_manifest(manifest_data)
    history = FORMAT.BACKUP.validate_history(history_data, manifest_data)
    cohort = DECODER.validate_cohort(manifest_data, lifecycle_data)
    exact = tuple(raw)
    require(initial is None or exact == initial, 'managed source metadata differs')
    if metadata_ready is not None:
        metadata_ready(exact, manifest, history, cohort)
    for name in FORMAT.NAMES[3:]:
        expected = manifest['files'][name]
        size = FORMAT.read_header(stream, name, expected['bytes'])
        require(size == expected['bytes'], 'managed payload size mismatch')
        digest = hashlib.sha256()
        remaining = size
        while remaining:
            chunk = FORMAT.read_exact(stream, min(CHUNK, remaining))
            digest.update(chunk)
            if emit is not None:
                emit(name, chunk)
            remaining -= len(chunk)
        require(digest.hexdigest() == expected['sha256'], 'managed payload digest mismatch')
        FORMAT.read_padding(stream, size)
    FORMAT.read_trailer(stream)
    return exact, manifest, history, cohort


def scan_fd(fd, manifest_sha256, emit=None, initial=None, metadata_ready=None):
    """Decode an already fully authenticated FD; leaves caller owning the FD."""
    os.lseek(fd, 0, os.SEEK_SET)
    require(os.pread(fd, 10, 0) == b'\x1f\x8b\x08\x00\x00\x00\x00\x00\x02\xff',
            'noncanonical managed gzip header')
    return scan_backup(GENERIC.GzipReader(fd), manifest_sha256, emit, initial, metadata_ready)


class Source:
    """Retain root 0400 archive and shared flock, without borrowing admission.

    Optional progress receives phase/file/bytes/total dictionaries on the creator
    thread. Encoded progress counts bytes read, not authentication success; only
    the subsequent decode phase follows complete encoded authentication. Decode
    counters reset per scan and count payload bytes before final archive checks.
    """
    def __init__(self, path, archive_sha256, manifest_sha256, allowed_devices, *, progress=None):
        self._creator = (os.getpid(), threading.get_ident())
        self.held = None
        self._locked = False
        self._initial = None
        self._progress = progress
        try:
            require(os.geteuid() == 0, 'root managed source caller required')
            require(progress is None or callable(progress), 'progress callback required')
            FORMAT.MANIFEST.digest(archive_sha256)
            FORMAT.MANIFEST.digest(manifest_sha256)
            devices = frozenset(allowed_devices)
            require(bool(devices) and all(type(device) is int for device in devices),
                    'explicit allowed devices required')
            self.manifest_sha256 = manifest_sha256
            self.held = ENROLL.HeldPath(path)
            ENROLL.regular(self.held.info, 0o400)
            require(self.held.info.st_dev in devices, 'managed archive device not allowed')
            require(0 < self.held.info.st_size <= MAX_ARCHIVE, 'invalid managed archive size')
            fcntl.flock(self.held.fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            self._locked = True
            self.archive_record = dict(bytes=self.held.info.st_size, sha256=archive_sha256)
            self.recheck()
            # Complete encoded authentication precedes gzip and every schema parser.
            self._hash_source()
            self.recheck()
            self.scan()
        except BaseException as primary:
            try:
                self.close()
            except BaseException as cleanup:
                raise SourceError(primary, [cleanup]) from primary
            raise

    def _notify(self, phase, name, count, total):
        self._owner()
        self._progress(dict(phase=phase, file=name, bytes=count, total=total))

    def _hash_source(self):
        if self._progress is None:
            ENROLL.hash_source(self.held, self.archive_record)
            return
        expected = self.archive_record
        require(self.held.info.st_size == expected['bytes'], 'source size differs')
        os.lseek(self.held.fd, 0, os.SEEK_SET)
        digest, count = hashlib.sha256(), 0
        self._notify('encoded', self.held.path, count, expected['bytes'])
        while True:
            data = os.read(self.held.fd, min(ENROLL.CHUNK, expected['bytes'] - count + 1))
            if not data:
                break
            count += len(data)
            require(count <= expected['bytes'], 'source grew')
            digest.update(data)
            self._notify('encoded', self.held.path, count, expected['bytes'])
        self.held.recheck()
        require(count == expected['bytes'] and digest.hexdigest() == expected['sha256'],
                'source size/SHA-256 differs')

    def _owner(self):
        require(self._creator == (os.getpid(), threading.get_ident()),
                'managed source belongs to another process/thread')

    def recheck(self):
        self._owner()
        require(self.held is not None and self._locked, 'managed source is closed')
        self.held.recheck()
        fcntl.flock(self.held.fd, fcntl.LOCK_SH | fcntl.LOCK_NB)

    def scan(self, emit=None):
        """Emit bounded payload chunks; discard all unpublished bytes on failure."""
        self.recheck()
        primary = None
        try:
            metadata_ready = None
            payload_emit = emit
            if self._progress is not None:
                counts, totals = {}, {}

                def metadata_ready(_exact, manifest, _history, _cohort):
                    for name in FORMAT.NAMES[3:]:
                        counts[name], totals[name] = 0, manifest['files'][name]['bytes']
                        self._notify('decode', name, 0, totals[name])

                def payload_emit(name, chunk):
                    counts[name] += len(chunk)
                    self._notify('decode', name, counts[name], totals[name])
                    if emit is not None:
                        emit(name, chunk)

            exact, manifest, history, cohort = scan_fd(
                self.held.fd, self.manifest_sha256, payload_emit, self._initial, metadata_ready)
            self.recheck()
            if self._initial is None:
                self._initial = exact
                self.manifest_data, self.history_data, self.lifecycle_data = exact
                self.manifest, self.history, self.cohort = manifest, history, cohort
        except BaseException as error:
            primary = error
            raise
        finally:
            try:
                self.recheck()
            except BaseException as cleanup:
                if primary is not None:
                    raise SourceError(primary, [cleanup]) from primary
                raise

    @property
    def description(self):
        self.recheck()
        return dict(path=self.held.path, identity=ENROLL.identity(self.held.info),
                    **self.archive_record)

    def close(self):
        self._owner()
        held, self.held = self.held, None
        self._locked = False
        if held is not None:
            errors = []
            try:
                held.close()
            except BaseException as error:
                errors.append(error)
            # A failed close hook must not strand any remaining owned descriptors.
            while held.fds:
                fd = held.fds.pop()
                try:
                    os.close(fd)
                except BaseException as error:
                    errors.append(error)
            if len(errors) > 1:
                raise SourceError(errors[0], errors[1:]) from errors[0]
            if errors:
                raise errors[0]

    def __enter__(self):
        self.recheck()
        return self

    def __exit__(self, _kind, primary, _traceback):
        try:
            self.close()
        except BaseException as cleanup:
            if primary is not None:
                raise SourceError(primary, [cleanup]) from primary
            raise
