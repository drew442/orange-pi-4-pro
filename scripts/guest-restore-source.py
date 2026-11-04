#!/usr/bin/python3
"""Read-only authenticated guest-backup source for guarded restoration.

The caller derives allowed devices from its validated destination context. The
protected selected-NVMe file, retained descriptors, metadata checks and shared
flock assume cooperative writers; they do not defend against a root attacker.
No capture, extraction, guest operation or hardware acceptance occurs here.
"""
import fcntl
import hashlib
import importlib.util
import os
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, BASE / path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


ENROLL = module('restore_source_enroll', 'scripts/enroll-guest.py')
FORMAT = module('restore_source_format', 'configs/guest_backup.py')
RECEIVER = module('restore_source_receiver', 'scripts/verify-guest-backup.py')
require = FORMAT.require
CHUNK = 1024 * 1024
MAX_ARCHIVE = 251 * 1024**3


def scan_backup(stream, manifest_sha256, emit=None, initial=None):
    """Validate the bounded decoded profile from an authenticated byte source.

    Local sources authenticate their complete encoded file before decoding;
    remote sources authenticate every encoded chunk before releasing it. The
    caller must also finish its encoded stream verification before publication.
    """
    size = FORMAT.read_header(stream, FORMAT.NAMES[0], FORMAT.MANIFEST.LIMIT)
    manifest_data = FORMAT.read_exact(stream, size)
    require(hashlib.sha256(manifest_data).hexdigest() == manifest_sha256,
            'manifest digest mismatch')
    manifest = FORMAT.MANIFEST.validate_manifest(manifest_data)
    RECEIVER.padding(stream, size)
    size = FORMAT.read_header(stream, FORMAT.NAMES[1], FORMAT.HISTORY_LIMIT)
    history_data = FORMAT.read_exact(stream, size)
    history = FORMAT.validate_history(history_data, manifest_data)
    RECEIVER.padding(stream, size)
    if initial is not None:
        require((manifest_data, history_data) == initial, 'restore source manifest/history differs')
    for name in FORMAT.NAMES[2:]:
        expected = manifest['files'][name]
        size = FORMAT.read_header(stream, name, expected['bytes'])
        require(size == expected['bytes'], 'backup payload size mismatch')
        digest = hashlib.sha256()
        remaining = size
        while remaining:
            chunk = FORMAT.read_exact(stream, min(CHUNK, remaining))
            digest.update(chunk)
            if emit is not None:
                emit(name, chunk)
            remaining -= len(chunk)
        require(digest.hexdigest() == expected['sha256'], 'backup payload digest mismatch')
        RECEIVER.padding(stream, size)
    tail = stream.read(10241)
    require(1024 <= len(tail) <= 10240 and len(tail) % 512 == 0 and
            tail == bytes(len(tail)), 'invalid tar end or extra decoded data')
    return manifest_data, manifest, history_data, history


class Source:
    """Authenticate once, then stream verified payloads into caller-owned files."""
    def __init__(self, path, archive_sha256, manifest_sha256, allowed_devices):
        self.held = None
        self._locked = False
        self._initial = None
        try:
            FORMAT.MANIFEST.digest(archive_sha256)
            FORMAT.MANIFEST.digest(manifest_sha256)
            self.manifest_sha256 = manifest_sha256
            self.held = ENROLL.HeldPath(path)
            ENROLL.regular(self.held.info, 0o400)
            require(self.held.info.st_dev in allowed_devices,
                    'restore archive must reside on selected NVMe')
            require(0 < self.held.info.st_size <= MAX_ARCHIVE, 'invalid archive size')
            fcntl.flock(self.held.fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            self._locked = True
            self.archive_record = {'bytes': self.held.info.st_size, 'sha256': archive_sha256}
            self.recheck()
            # Authenticate every encoded byte before constructing any decoder or
            # invoking tar/JSON parsing. No writable copy of the archive exists.
            ENROLL.hash_source(self.held, self.archive_record)
            self.recheck()
            self.scan()
        except BaseException:
            self.close()
            raise

    def recheck(self):
        require(self.held is not None and self._locked, 'restore source is closed')
        self.held.recheck()
        # Retain a shared nonblocking lock for the complete source lifetime.
        fcntl.flock(self.held.fd, fcntl.LOCK_SH | fcntl.LOCK_NB)

    def scan(self, emit=None):
        """Validate all five members; emit only bounded chunks of three payloads.

        A callback may receive bytes before the final digest/footer checks pass.
        The caller must discard its unpublished files on any exception.
        """
        self.recheck()
        try:
            fd = self.held.fd
            os.lseek(fd, 0, os.SEEK_SET)
            require(os.pread(fd, 10, 0) == b'\x1f\x8b\x08\x00\x00\x00\x00\x00\x02\xff',
                    'noncanonical gzip header')
            stream = RECEIVER.GzipReader(fd)
            manifest_data, manifest, history_data, history = scan_backup(
                stream, self.manifest_sha256, emit, self._initial)
            self.recheck()
            if self._initial is None:
                self.manifest_data = manifest_data
                self.manifest = manifest
                self.history_data = history_data
                self.history = history
                self._initial = (manifest_data, history_data)
        finally:
            self.recheck()

    @property
    def description(self):
        self.recheck()
        return {'path': self.held.path, 'identity': ENROLL.identity(self.held.info),
                **self.archive_record}

    def close(self):
        held, self.held = self.held, None
        self._locked = False
        if held is not None:
            held.close()  # Closing the descriptor also releases its shared flock.

    def __enter__(self):
        self.recheck()
        return self

    def __exit__(self, _kind, _value, _traceback):
        self.close()
