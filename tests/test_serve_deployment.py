"""Local HTTPS delivery fixtures; no physical boot or installation acceptance."""
import contextlib
import hashlib
import http.client
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('serve_deployment', REPO / 'build/serve-deployment.py')
SERVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SERVER)


@unittest.skipUnless(shutil.which('openssl'), 'requires openssl for disposable localhost certificate')
class DeliveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.credentials = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.credentials.cleanup)
        cls.cert = Path(cls.credentials.name) / 'cert.pem'
        cls.key = Path(cls.credentials.name) / 'key.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                        '-keyout', str(cls.key), '-out', str(cls.cert), '-days', '1',
                        '-subj', '/CN=localhost', '-addext', 'subjectAltName=IP:127.0.0.1',
                        '-addext', 'basicConstraints=critical,CA:TRUE'],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.directory = self.root / 'public'
        self.directory.mkdir()
        self.image = bytes(range(256)) * 4096 + b'last-block'
        (self.directory / 'nvme.img.gz').write_bytes(self.image)
        self.manifest = {'files': {'nvme.img.gz': {'bytes': len(self.image),
                                                 'sha256': hashlib.sha256(self.image).hexdigest()}}}
        self.write_manifest()
        (self.directory / 'delivery-ca.pem').write_bytes(self.cert.read_bytes())
        (self.directory / 'host-private-key').write_bytes(b'PRIVATE SECRET NEVER SERVE')

    def write_manifest(self):
        (self.directory / 'manifest.json').write_text(json.dumps(self.manifest))

    @contextlib.contextmanager
    def running(self):
        artifacts = SERVER.Artifacts(self.directory)
        self.addCleanup(artifacts.close)
        context = SERVER.tls_context(artifacts, self.cert, self.key)
        server = SERVER.DeliveryServer(('127.0.0.1', 0), artifacts, context)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01})
        thread.start()
        try:
            yield server, artifacts
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)
            self.assertFalse(thread.is_alive())

    def request(self, server, method, path):
        context = ssl.create_default_context(cafile=str(self.cert))
        connection = http.client.HTTPSConnection('127.0.0.1', server.server_port,
                                                context=context, timeout=3)
        self.addCleanup(connection.close)
        connection.request(method, path)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()

    def test_https_get_head_exact_bytes_and_public_allowlist(self):
        with self.running() as (server, _):
            for name in ('nvme.img.gz', 'manifest.json', 'delivery-ca.pem'):
                expected = (self.directory / name).read_bytes()
                status, headers, data = self.request(server, 'GET', '/' + name)
                self.assertEqual(status, 200)
                self.assertEqual(data, expected)
                self.assertEqual(headers['Content-Length'], str(len(expected)))
                status, headers, data = self.request(server, 'HEAD', '/' + name)
                self.assertEqual(status, 200)
                self.assertEqual(data, b'')
                self.assertEqual(headers['Content-Length'], str(len(expected)))
            for path in ('/', '/host-private-key', '/key.pem', '/manifest.sha256', '/manifest.json?x',
                         '/manifest.json#x', '/manifest.json/extra', '/%6danifest.json',
                         '/%2e%2e/key.pem', '/../key.pem', '//manifest.json',
                         'https://localhost/manifest.json'):
                with self.subTest(path=path):
                    status, _, data = self.request(server, 'GET', path)
                    self.assertEqual(status, 404)
                    self.assertNotIn(b'PRIVATE SECRET', data)
            for method in ('POST', 'PUT', 'DELETE', 'OPTIONS', 'PATCH'):
                with self.subTest(method=method):
                    self.assertEqual(self.request(server, method, '/manifest.json')[0], 405)

    def test_untrusted_tls_client_rejected_and_idle_handshake_bounded(self):
        with patch.object(SERVER, 'TIMEOUT', 0.1), self.running() as (server, _):
            connection = http.client.HTTPSConnection('127.0.0.1', server.server_port,
                                                    context=ssl.create_default_context(), timeout=3)
            with self.assertRaises(ssl.SSLCertVerificationError):
                connection.request('GET', '/manifest.json')
            connection.close()
            idle = socket.create_connection(('127.0.0.1', server.server_port), timeout=3)
            self.addCleanup(idle.close)
            idle.settimeout(3)
            self.assertEqual(idle.recv(1), b'')
            self.assertEqual(self.request(server, 'GET', '/manifest.json')[0], 200)

    def test_all_symlink_components_refused(self):
        for name in ('nvme.img.gz', 'manifest.json', 'delivery-ca.pem'):
            source = self.directory / name
            saved = self.root / name
            source.rename(saved)
            source.symlink_to(saved)
            with self.subTest(name=name), self.assertRaises(OSError):
                SERVER.Artifacts(self.directory)
            source.unlink()
            saved.rename(source)
        alias = self.root / 'alias'
        alias.symlink_to(self.directory, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            SERVER.Artifacts(alias)
        ancestor = self.root / 'ancestor'
        ancestor.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            SERVER.Artifacts(ancestor / 'public')
        with self.assertRaises(OSError):
            SERVER.open_regular(ancestor / 'public/manifest.json')

    def test_manifest_size_hash_and_snapshot_bounds(self):
        for field, value in [('bytes', len(self.image) + 1), ('bytes', True),
                             ('bytes', SERVER.MAX_IMAGE + 1), ('sha256', '0' * 64)]:
            original = self.manifest['files']['nvme.img.gz'][field]
            self.manifest['files']['nvme.img.gz'][field] = value
            self.write_manifest()
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                SERVER.Artifacts(self.directory)
            self.manifest['files']['nvme.img.gz'][field] = original
        self.write_manifest()
        with (self.directory / 'manifest.json').open('r+b') as stream:
            stream.truncate(8 * SERVER.CHUNK + 1)
        with self.assertRaisesRegex(ValueError, 'size limit'):
            SERVER.Artifacts(self.directory)

    def test_snapshot_does_not_follow_replacements(self):
        with self.running() as (server, artifacts):
            expected = artifacts.snapshots['/manifest.json']
            (self.directory / 'manifest.json').unlink()
            (self.directory / 'manifest.json').symlink_to(self.key)
            status, _, data = self.request(server, 'GET', '/manifest.json')
            self.assertEqual((status, data), (200, expected))
            image_path = self.directory / 'nvme.img.gz'
            image_path.rename(self.root / 'old-image')
            image_path.symlink_to(self.key)
            self.assertEqual(artifacts.image.read(8), self.image[:8])
            # Renaming changes ctime on Linux; reject drift rather than reopen the name.
            self.assertEqual(self.request(server, 'GET', '/nvme.img.gz')[0], 503)

    def test_image_inplace_metadata_drift_refused(self):
        with self.running() as (server, _):
            with (self.directory / 'nvme.img.gz').open('r+b') as stream:
                stream.write(b'changed')
                stream.flush()
                os.fsync(stream.fileno())
            self.assertEqual(self.request(server, 'GET', '/nvme.img.gz')[0], 503)
            self.assertEqual(self.request(server, 'HEAD', '/nvme.img.gz')[0], 503)

    def test_tls_material_privacy_links_and_minimum_version(self):
        with contextlib.closing(SERVER.Artifacts(self.directory)) as artifacts:
            context = SERVER.tls_context(artifacts, self.cert, self.key)
            self.assertEqual(context.minimum_version, ssl.TLSVersion.TLSv1_2)
            self.assertEqual(context.verify_mode, ssl.CERT_NONE)
            inside_key = self.directory / 'private.pem'
            inside_key.write_bytes(self.key.read_bytes())
            with self.assertRaisesRegex(ValueError, 'outside'):
                SERVER.tls_context(artifacts, self.cert, inside_key)
            key_alias = self.root / 'key-link.pem'
            key_alias.symlink_to(self.key)
            with self.assertRaises(OSError):
                SERVER.tls_context(artifacts, self.cert, key_alias)
            ca_alias = self.root / 'ca-hardlink.pem'
            os.link(self.directory / 'delivery-ca.pem', ca_alias)
            with self.assertRaisesRegex(ValueError, 'alias'):
                SERVER.tls_context(artifacts, ca_alias, self.key)

    def test_ca_private_key_and_nonregular_artifacts_refused(self):
        (self.directory / 'delivery-ca.pem').write_bytes(self.cert.read_bytes() + self.key.read_bytes())
        with self.assertRaisesRegex(ValueError, 'public certificates'):
            SERVER.Artifacts(self.directory)
        (self.directory / 'delivery-ca.pem').write_bytes(self.cert.read_bytes())
        (self.directory / 'nvme.img.gz').unlink()
        os.mkfifo(self.directory / 'nvme.img.gz')
        with self.assertRaisesRegex(ValueError, 'regular'):
            SERVER.Artifacts(self.directory)

    def test_explicit_address_and_unprivileged_port(self):
        for bind, port in [('0.0.0.0', '8443'), ('224.1.2.3', '8443'), ('127.0.0.1', '443')]:
            with self.subTest(bind=bind, port=port):
                self.assertEqual(SERVER.main(['--directory', str(self.directory),
                                             '--certificate', str(self.cert), '--key', str(self.key),
                                             '--bind', bind, '--port', port]), 1)


if __name__ == '__main__':
    unittest.main()
