#!/usr/bin/python3
"""Authenticate bounded HTTPS chunks before decoding a guest backup.

No archive staging, extraction, retries or destination writes occur here. Callers
must discard private output if a scan or callback fails before publication.
"""
import hashlib
import importlib.util
import os
from pathlib import Path
import re
import ssl
import stat
import urllib.error
import urllib.request
import zlib

BASE = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, BASE / path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


SOURCE = module('https_restore_source', 'scripts/guest-restore-source.py')
CHUNKS = module('https_restore_chunks', 'configs/guest_backup_chunks.py')
ACQ = module('https_restore_acquire', 'scripts/acquire-nvme.py')
require = SOURCE.require
CHUNK = CHUNKS.CHUNK
HEADER = b'\x1f\x8b\x08\x00\x00\x00\x00\x00\x02\xff'


def https_url(value):
    require(type(value) is str and len(value) <= 4096 and '?' not in value,
            'invalid restore HTTPS URL')
    ACQ.https_url(value)
    # urlsplit normalizes scheme casing; retain only the exact supported scheme.
    require(value.startswith('https://'), 'invalid restore HTTPS URL')
    return value


def response_headers(response, cap, expected=None):
    require(response.status == 200, 'HTTPS status must be 200')
    require(not response.headers.get_all('Transfer-Encoding', []),
            'Transfer-Encoding is forbidden')
    encodings = response.headers.get_all('Content-Encoding', [])
    require(not encodings or encodings == ['identity'], 'unsupported Content-Encoding')
    lengths = response.headers.get_all('Content-Length', [])
    require(len(lengths) == 1 and re.fullmatch('[0-9]{1,12}', lengths[0]) is not None,
            'exactly one numeric Content-Length required')
    declared = int(lengths[0])
    require(0 < declared <= cap and (expected is None or declared == expected),
            'Content-Length exceeds or differs from authenticated size')
    # HTTPResponse otherwise hides a suffix behind Content-Length. With an
    # explicit Connection: close request, EOF also authenticates actual framing.
    response.length = None
    return declared


def read_response(response, size, deadline):
    timeout = ACQ.remaining(deadline)
    try:
        if response.fp is not None:
            response.fp.raw._sock.settimeout(timeout)
        data = response.read1(size)
    except Exception:
        raise ValueError('HTTPS body read failed') from None
    ACQ.remaining(deadline)
    require(type(data) is bytes and len(data) <= size, 'invalid bounded HTTPS read')
    return data


class NoRedirect(ACQ.NoRedirect):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        fp.close()
        raise ValueError('HTTPS redirects are forbidden')


class ChunkReader:
    """Release bytes only after their complete encoded chunk is authenticated."""
    def __init__(self, response, index, deadline):
        self.response, self.index, self.deadline = response, index, deadline
        self.pending = b''
        self.count = self.chunks = 0
        self.digest = hashlib.sha256()
        self.done = False

    def read(self, size):
        require(type(size) is int and 0 <= size <= CHUNK, 'invalid chunk read size')
        if not size:
            return b''
        if not self.pending and not self.done:
            expected = min(CHUNK, self.index['archive']['bytes'] - self.count)
            value = bytearray()
            while len(value) < expected:
                part = read_response(self.response, expected - len(value), self.deadline)
                require(bool(part), 'truncated HTTPS archive chunk')
                value.extend(part)
            require(hashlib.sha256(value).hexdigest() == self.index['chunks'][self.chunks],
                    'encoded chunk SHA-256 mismatch')
            self.digest.update(value)
            self.count += len(value)
            self.chunks += 1
            if self.count == self.index['archive']['bytes']:
                require(not read_response(self.response, 1, self.deadline),
                        'extra encoded archive bytes')
                require(self.digest.hexdigest() == self.index['archive']['sha256'],
                        'whole encoded archive SHA-256 mismatch')
                self.done = True
            self.pending = bytes(value)
        result, self.pending = self.pending[:size], self.pending[size:]
        return result


class GzipReader:
    """Bounded single-member gzip decoder over already authenticated bytes."""
    def __init__(self, stream):
        self.stream = stream
        header = SOURCE.FORMAT.read_exact(stream, 10)
        require(header == HEADER, 'noncanonical gzip header')
        self.decoder = zlib.decompressobj(31)
        self.pending = header
        self.done = False

    def read(self, size):
        require(type(size) is int and 0 <= size <= max(CHUNK, SOURCE.FORMAT.HISTORY_LIMIT),
                'invalid decoded read size')
        result = bytearray()
        while len(result) < size and not self.done:
            if not self.pending:
                self.pending = self.stream.read(CHUNK)
                require(bool(self.pending), 'truncated gzip stream')
            part = self.decoder.decompress(self.pending, min(CHUNK, size - len(result)))
            result.extend(part)
            self.pending = self.decoder.unconsumed_tail
            if self.decoder.eof:
                require(not self.decoder.unused_data and not self.stream.read(1),
                        'extra encoded gzip suffix/member')
                self.done = True
        return bytes(result)


class RemoteSource:
    """Retain a pinned sidecar and rescan a fresh authenticated HTTPS stream."""
    def __init__(self, archive_url, chunk_manifest_url, chunk_manifest_sha256,
                 archive_sha256, manifest_sha256, *, ca_file=None, timeout_seconds=86400):
        self.closed = False
        self.ca_held = None
        self._initial = None
        try:
            self.archive_url = https_url(archive_url)
            self.chunk_manifest_url = https_url(chunk_manifest_url)
            for pin in (chunk_manifest_sha256, archive_sha256, manifest_sha256):
                SOURCE.FORMAT.MANIFEST.digest(pin)
            require(type(timeout_seconds) is int and 1 <= timeout_seconds <= 172800,
                    'invalid HTTPS scan timeout')
            self.timeout_seconds = timeout_seconds
            self.manifest_sha256 = manifest_sha256
            # Build the client context explicitly: create_default_context honors
            # SSLKEYLOGFILE and can silently write secrets to a local pathname.
            self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            self.context.load_default_certs()
            self.context.minimum_version = ssl.TLSVersion.TLSv1_2
            self.context.verify_mode = ssl.CERT_REQUIRED
            self.context.check_hostname = True
            ca = {'kind': 'system'}
            if ca_file is not None:
                held = self.ca_held = SOURCE.ENROLL.HeldPath(ca_file)
                SOURCE.ENROLL.regular(held.info)
                require(stat.S_IMODE(held.info.st_mode) in (0o400, 0o444) and
                        0 < held.info.st_size <= 4 * CHUNK, 'unsafe CA file mode/size')
                data = bytearray()
                while len(data) < held.info.st_size:
                    part = os.read(held.fd, min(CHUNK, held.info.st_size - len(data)))
                    require(bool(part), 'truncated CA file')
                    data.extend(part)
                held.recheck()
                self.context.load_verify_locations(cadata=data.decode('ascii'))
                ca = {'kind': 'file', 'path': held.path,
                      'identity': SOURCE.ENROLL.identity(held.info),
                      'sha256': hashlib.sha256(data).hexdigest()}
            self.opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({}), NoRedirect(),
                urllib.request.HTTPSHandler(context=self.context))
            with ACQ.deadline_guard(timeout_seconds) as deadline:
                with self._open(self.chunk_manifest_url, deadline) as response:
                    declared = response_headers(response, CHUNKS.LIMIT)
                    raw = bytearray()
                    while len(raw) < declared:
                        part = read_response(response, min(CHUNK, declared - len(raw)), deadline)
                        require(bool(part), 'truncated HTTPS chunk manifest')
                        raw.extend(part)
                    require(not read_response(response, 1, deadline), 'extra chunk manifest bytes')
                    self.index = CHUNKS.validate(bytes(raw), chunk_manifest_sha256)
            require(self.index['archive']['sha256'] == archive_sha256 and
                    self.index['manifest_sha256'] == manifest_sha256,
                    'chunk manifest differs from trusted archive/manifest pins')
            self.archive_record = dict(self.index['archive'])
            self.description = {'url': self.archive_url,
                                'chunk_manifest_url': self.chunk_manifest_url,
                                'chunk_manifest_sha256': chunk_manifest_sha256,
                                'timeout_seconds': timeout_seconds, 'ca': ca,
                                **self.archive_record}
            self.scan()
        except BaseException:
            self.close()
            raise

    def _open(self, url, deadline):
        try:
            return self.opener.open(urllib.request.Request(
                url, headers={'Accept-Encoding': 'identity', 'Connection': 'close'}),
                timeout=ACQ.remaining(deadline))
        except urllib.error.HTTPError as error:
            error.close()
            raise ValueError('HTTPS request failed') from None
        except Exception:
            raise ValueError('HTTPS request failed') from None

    def recheck(self):
        require(not self.closed, 'restore source is closed')
        if self.ca_held is not None:
            self.ca_held.recheck()

    def scan(self, emit=None):
        self.recheck()
        try:
            with ACQ.deadline_guard(self.timeout_seconds) as deadline:
                with self._open(self.archive_url, deadline) as response:
                    response_headers(response, self.archive_record['bytes'], self.archive_record['bytes'])
                    encoded = ChunkReader(response, self.index, deadline)
                    values = SOURCE.scan_backup(GzipReader(encoded), self.manifest_sha256,
                                                emit=emit, initial=self._initial)
                    require(encoded.done and not encoded.pending,
                            'archive was not completely authenticated')
            if self._initial is None:
                (self.manifest_data, self.manifest,
                 self.history_data, self.history) = values
                self._initial = (self.manifest_data, self.history_data)
        finally:
            self.recheck()

    def close(self):
        self.closed = True
        held, self.ca_held = self.ca_held, None
        if held is not None:
            held.close()

    def __enter__(self):
        self.recheck()
        return self

    def __exit__(self, _kind, _value, _traceback):
        self.close()
