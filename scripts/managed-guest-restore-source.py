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
    """Retain root 0400 archive and shared flock, without borrowing admission."""
    def __init__(self, path, archive_sha256, manifest_sha256, allowed_devices):
        self._creator = (os.getpid(), threading.get_ident())
        self.held = None
        self._locked = False
        self._initial = None
        try:
            require(os.geteuid() == 0, 'root managed source caller required')
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
            ENROLL.hash_source(self.held, self.archive_record)
            self.recheck()
            self.scan()
        except BaseException:
            self.close()
            raise

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
        try:
            exact, manifest, history, cohort = scan_fd(
                self.held.fd, self.manifest_sha256, emit, self._initial)
            self.recheck()
            if self._initial is None:
                self._initial = exact
                self.manifest_data, self.history_data, self.lifecycle_data = exact
                self.manifest, self.history, self.cohort = manifest, history, cohort
        finally:
            self.recheck()

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
            held.close()

    def __enter__(self):
        self.recheck()
        return self

    def __exit__(self, *_unused):
        self.close()
