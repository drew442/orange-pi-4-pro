"""Loopback TLS and bounded authenticated stream tests; no hardware evidence."""
import base64
from email.message import Message
import http.server
import importlib.util
import io
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, BASE / path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


H = load('restore_https_test', 'scripts/guest-restore-https.py')
T = load('restore_https_fixtures', 'tests/test_guest_backup_receiver.py')
F = H.SOURCE.FORMAT


def index_bytes(encoded, manifest):
    return H.CHUNKS.canonical({'schema': 1, 'profile': H.CHUNKS.PROFILE,
        'archive': {'bytes': len(encoded), 'sha256': T.sha(encoded)},
        'manifest_sha256': T.sha(manifest), 'chunk_bytes': H.CHUNK,
        'chunks': [T.sha(encoded[i:i + H.CHUNK]) for i in range(0, len(encoded), H.CHUNK)]})


class Response:
    def __init__(self, body, short=47):
        self.body, self.short, self.fp = io.BytesIO(body), short, None
        self.reads = []

    def read1(self, size):
        self.reads.append(size)
        return self.body.read(min(size, self.short))


class ChunkTests(unittest.TestCase):
    def test_short_reads_full_hash_final_chunk_and_bounded_requests(self):
        encoded = b'A' * H.CHUNK + b'end'
        index = H.CHUNKS.validate(index_bytes(encoded, b'x'))
        response = Response(encoded, 17111)
        reader = H.ChunkReader(response, index, time.monotonic() + 30)
        parts = []
        while True:
            part = reader.read(999999)
            if not part:
                break
            parts.append(part)
        self.assertEqual(b''.join(parts), encoded)
        self.assertTrue(reader.done)
        self.assertEqual(reader.chunks, 2)
        self.assertLessEqual(max(response.reads), H.CHUNK)
        for size in (-1, H.CHUNK + 1):
            with self.assertRaises(ValueError):
                reader.read(size)

    def test_bad_chunk_never_released_and_final_hash_eof_before_release(self):
        encoded = b'A' * H.CHUNK + b'end'
        index = H.CHUNKS.validate(index_bytes(encoded, b'x'))
        for body in (b'B' + encoded[1:], encoded[:-1]):
            with self.subTest(body_bytes=len(body)):
                reader = H.ChunkReader(Response(body, H.CHUNK), index, time.monotonic() + 30)
                if body[:1] == b'A':
                    self.assertEqual(reader.read(H.CHUNK), encoded[:H.CHUNK])
                with self.assertRaises(ValueError):
                    reader.read(1)
        for altered in ('hash', 'suffix'):
            copy = {**index, 'archive': dict(index['archive'])}
            body = encoded
            if altered == 'hash':
                copy['archive']['sha256'] = '0' * 64
            else:
                body += b'extra'
            reader = H.ChunkReader(Response(body, H.CHUNK), copy, time.monotonic() + 30)
            self.assertEqual(reader.read(H.CHUNK), encoded[:H.CHUNK])
            with self.assertRaises(ValueError):
                reader.read(1)
            self.assertEqual(reader.pending, b'')

    def test_strict_url_and_response_headers(self):
        for url in ('http://example/a', 'https://u@example/a', 'https://example/a?',
                    'https://example/a#', 'https://example/a\n', 'https://example/' + 'a' * 4096):
            with self.subTest(url=url[:90]), self.assertRaises(ValueError):
                H.https_url(url)
        for headers in ([], [('Content-Length', '2'), ('Content-Length', '2')],
                        [('Content-Length', '+2')], [('Content-Length', '3')],
                        [('Content-Length', '2'), ('Transfer-Encoding', 'chunked')],
                        [('Content-Length', '2'), ('Content-Encoding', 'gzip')]):
            response = Response(b'ab')
            response.status, response.headers = 200, Message()
            for key, value in headers:
                response.headers[key] = value
            with self.subTest(headers=headers), self.assertRaises(ValueError):
                H.response_headers(response, 2, 2)


@unittest.skipUnless(shutil.which('openssl') and os.geteuid() == 0,
                     'protected CA and actual loopback TLS tests require root and openssl')
class TLSRestore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(dir='/run', prefix='restore-https-test-')
        cls.root = Path(cls.temp.name)
        cls.cert, cls.key = cls.root / 'cert.pem', cls.root / 'key.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                        '-keyout', str(cls.key), '-out', str(cls.cert), '-days', '1',
                        '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost'],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.cert.chmod(0o400)
        cls.routes, cls.requests, cls.drip = {}, [], False
        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.0'
            def do_GET(self):
                cls.requests.append(self.path)
                status, headers, body = cls.routes[self.path]
                self.send_response(status)
                for key, value in headers:
                    self.send_header(key, value)
                self.end_headers()
                try:
                    if self.path == '/archive' and cls.drip:
                        for byte in body:
                            self.wfile.write(bytes([byte]))
                            self.wfile.flush()
                            time.sleep(.02)
                    else:
                        self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
                    pass
            def log_message(self, *_args):
                pass
        cls.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cls.cert, cls.key)
        cls.server.socket = ctx.wrap_socket(cls.server.socket, server_side=True)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'https://localhost:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.temp.cleanup()

    def setUp(self):
        self.manifest, self.payload = T.fixture()
        self.set_archive(T.bundle(self.manifest, self.payload))
        self.requests.clear()

    def route(self, path, body, headers=None, status=200):
        self.routes[path] = status, headers if headers is not None else [
            ('Content-Length', str(len(body)))], body

    def set_archive(self, encoded):
        self.encoded = encoded
        self.index = index_bytes(encoded, self.manifest)
        self.route('/index', self.index)
        self.route('/archive', encoded)

    def source(self, **kwargs):
        options = {'ca_file': str(self.cert), 'timeout_seconds': 10, **kwargs}
        return H.RemoteSource(self.url + '/archive', self.url + '/index', T.sha(self.index),
                              T.sha(self.encoded), T.sha(self.manifest), **options)

    def test_real_tls_repeated_scan_exact_payloads_and_no_writes(self):
        original_open = os.open
        def read_only(path, flags, *args, **kwargs):
            self.assertFalse(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC))
            return original_open(path, flags, *args, **kwargs)
        with patch.object(H.os, 'open', side_effect=read_only):
            with self.source() as source:
                self.assertEqual(source.history, {})
                self.assertEqual(source.manifest_data, self.manifest)
                self.assertEqual(source.archive_record, T.R.record(self.encoded))
                self.assertEqual(source.description['ca']['sha256'], T.sha(self.cert.read_bytes()))
                self.assertEqual(source.context.minimum_version, ssl.TLSVersion.TLSv1_2)
                for _ in range(2):
                    received = {name: bytearray() for name in self.payload}
                    source.scan(lambda name, data: received[name].extend(data))
                    self.assertEqual(received, self.payload)
                source.recheck()
        self.assertEqual(self.requests, ['/index', '/archive', '/archive', '/archive'])
        with self.assertRaises(ValueError):
            source.scan()

    def test_ssl_keylog_environment_cannot_create_local_file(self):
        keylog = self.cert.parent / 'unexpected-keylog'
        with patch.dict(H.os.environ, {'SSLKEYLOGFILE': str(keylog)}):
            with self.source() as source:
                self.assertIsNone(source.context.keylog_filename)
        self.assertFalse(keylog.exists())

    def test_tls_certificate_hostname_and_redirect_refusals(self):
        with self.assertRaisesRegex(ValueError, 'HTTPS request failed'):
            self.source(ca_file=None)
        with self.assertRaisesRegex(ValueError, 'HTTPS request failed'):
            H.RemoteSource(self.url.replace('localhost', '127.0.0.1') + '/archive',
                self.url.replace('localhost', '127.0.0.1') + '/index', T.sha(self.index),
                T.sha(self.encoded), T.sha(self.manifest), ca_file=str(self.cert))
        self.route('/index', b'', [('Location', self.url + '/archive'), ('Content-Length', '0')], 302)
        self.requests.clear()
        with self.assertRaisesRegex(ValueError, 'HTTPS request failed'):
            self.source()
        self.assertEqual(self.requests, ['/index'])

    def test_changed_stream_chunk_refused_before_decoder(self):
        with self.source() as source:
            self.route('/archive', b'X' + self.encoded[1:])
            with patch.object(H.zlib, 'decompressobj', side_effect=AssertionError('decoder')):
                with self.assertRaisesRegex(ValueError, 'chunk SHA-256'):
                    source.scan()

    def test_bad_sidecar_before_json_or_archive_request(self):
        self.route('/index', b'X' + self.index[1:])
        with patch.object(H.CHUNKS.json, 'loads', side_effect=AssertionError('JSON')):
            with self.assertRaises(ValueError):
                self.source()
        self.assertEqual(self.requests, ['/index'])

    def test_truncation_suffix_and_duplicate_headers(self):
        cases = [(self.encoded[:-1], [('Content-Length', str(len(self.encoded)))]),
                 (self.encoded + b'suffix', [('Content-Length', str(len(self.encoded)))]),
                 (self.encoded, [('Content-Length', str(len(self.encoded))),
                                 ('Content-Length', str(len(self.encoded)))]),
                 (self.encoded, [('Content-Length', str(len(self.encoded))),
                                 ('Transfer-Encoding', 'chunked')])]
        for body, headers in cases:
            self.route('/archive', body, headers)
            with self.subTest(headers=headers), self.assertRaises(ValueError):
                self.source()

    def test_authenticated_gzip_footer_member_and_callback_failures(self):
        for encoded in (self.encoded[:-8] + bytes(8), self.encoded + self.encoded):
            self.set_archive(encoded)
            with self.assertRaises((ValueError, H.zlib.error)):
                self.source()
        self.set_archive(T.bundle(self.manifest, self.payload))
        with self.source() as source:
            def fail(_name, _data):
                raise RuntimeError('private output failed')
            with self.assertRaisesRegex(RuntimeError, 'private output failed'):
                source.scan(fail)
            source.scan()

    def test_protected_ca_metadata_and_lifetime(self):
        with self.source() as source:
            self.cert.chmod(0o444)
            try:
                with self.assertRaisesRegex(ValueError, 'held input changed'):
                    source.recheck()
            finally:
                self.cert.chmod(0o400)
        self.cert.chmod(0o600)
        try:
            with self.assertRaisesRegex(ValueError, 'CA file mode'):
                self.source()
        finally:
            self.cert.chmod(0o400)

    def test_total_deadline_bounds_dripping_body_and_restores_alarm(self):
        type(self).drip = True
        try:
            started = time.monotonic()
            with self.assertRaises((ValueError, TimeoutError)):
                self.source(timeout_seconds=1)
            self.assertLess(time.monotonic() - started, 3)
        finally:
            type(self).drip = False
        # A successful later pass demonstrates that the prior alarm was cleared.
        with self.source():
            pass

    def test_sidecar_external_archive_and_manifest_pins_must_match(self):
        for archive_sha, manifest_sha in [('0' * 64, T.sha(self.manifest)),
                                          (T.sha(self.encoded), '0' * 64)]:
            with self.subTest(archive_sha=archive_sha), self.assertRaisesRegex(
                    ValueError, 'trusted archive/manifest pins'):
                H.RemoteSource(self.url + '/archive', self.url + '/index', T.sha(self.index),
                               archive_sha, manifest_sha, ca_file=str(self.cert))
        self.assertNotIn('/archive', self.requests)

    def test_noncanonical_gzip_header_is_rejected_after_authentication(self):
        encoded = bytearray(self.encoded)
        encoded[4] = 1
        self.set_archive(bytes(encoded))
        with self.assertRaisesRegex(ValueError, 'noncanonical gzip header'):
            self.source()

    def test_large_archive_second_chunk_changes_are_refused_on_rescan(self):
        self.payload['disk.raw'] = os.urandom(3 * H.CHUNK)
        manifest = F.MANIFEST.validate_manifest(self.manifest)
        record = T.R.record(self.payload['disk.raw'])
        manifest['config']['disk'] = record
        manifest['files']['disk.raw'].update(record)
        self.manifest = F.MANIFEST.canonical(manifest)
        self.set_archive(T.bundle(self.manifest, self.payload))
        with self.source() as source:
            received = {name: bytearray() for name in self.payload}
            source.scan(lambda name, data: received[name].extend(data))
            self.assertEqual(received, self.payload)
            changed = bytearray(self.encoded)
            changed[H.CHUNK + 7] ^= 1
            self.route('/archive', bytes(changed))
            with self.assertRaisesRegex(ValueError, 'chunk SHA-256 mismatch'):
                source.scan(lambda _name, _data: None)

    def test_complete_noncanonical_history_preserved(self):
        old_raw = F.canonical(F.MANIFEST.validate_manifest(self.manifest))
        old = F.MANIFEST.validate_manifest(old_raw)
        new = F.MANIFEST.proposed_manifest(old, T.sha(old_raw), T.R.record(b'A' * 512))
        new_raw = F.MANIFEST.canonical(new)
        entry = F.canonical({'schema': 1, 'operation': 'a733-guest-checkpoint-v1',
            'old_manifest': old, 'new_manifest': new,
            'old_manifest_sha256': T.sha(old_raw), 'new_manifest_sha256': T.sha(new_raw),
            'old_manifest_base64': base64.b64encode(old_raw).decode(),
            'new_manifest_base64': base64.b64encode(new_raw).decode()})
        history = {T.sha(old_raw) + '-' + T.sha(new_raw) + '.json': entry}
        self.manifest, self.payload['disk.raw'] = new_raw, b'A' * 512
        self.set_archive(T.bundle(new_raw, self.payload, history))
        with self.source() as source:
            self.assertEqual(source.history, history)
            self.assertEqual(source.history_data, F.history_payload(history, new_raw))
            source.scan()


if __name__ == '__main__':
    unittest.main()
