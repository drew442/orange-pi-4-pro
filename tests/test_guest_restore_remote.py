"""Real TLS to guarded private-directory restoration; no board or VM evidence."""
import hashlib
import http.server
import importlib.util
import os
from pathlib import Path
import ssl
import subprocess
import threading
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, BASE / path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


F = load('restore_remote_fixture', 'tests/test_guest_restore.py')
C = load('restore_remote_chunks', 'configs/guest_backup_chunks.py')
R = F.R


@unittest.skipUnless(os.geteuid() == 0, 'guarded destination ownership requires root')
class RemoteRestore(unittest.TestCase):
    def setUp(self):
        self.fixture = F.RestoreFiles()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.gets = 0
        self.corrupt_copy = False
        # More than two encoded chunks exercises failure after private writes.
        self.fixture.payload['disk.raw'] = os.urandom(2 * C.CHUNK)
        manifest = F.T.F.MANIFEST.validate_manifest(self.fixture.manifest)
        disk = R.record(self.fixture.payload['disk.raw'])
        manifest['config']['disk'] = disk
        manifest['files']['disk.raw'].update(disk)
        self.fixture.manifest = F.T.F.MANIFEST.canonical(manifest)
        self.fixture.write_archive()
        self.chunks = C.make(R.record(self.fixture.encoded), F.T.sha(self.fixture.manifest),
            [F.T.sha(self.fixture.encoded[n:n + C.CHUNK])
             for n in range(0, len(self.fixture.encoded), C.CHUNK)])
        self.cert = self.fixture.base / 'ca.pem'
        key = self.fixture.base / 'key.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
            '-keyout', str(key), '-out', str(self.cert), '-days', '1', '-subj', '/CN=fixture',
            '-addext', 'subjectAltName=IP:127.0.0.1', '-addext', 'basicConstraints=critical,CA:TRUE'],
            check=True, capture_output=True, timeout=30)
        self.cert.chmod(0o400)
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == '/chunks.json':
                    data = owner.chunks
                elif self.path == '/archive.gz':
                    owner.gets += 1
                    data = owner.fixture.encoded
                    if owner.corrupt_copy and owner.gets == 3:
                        data = data[:-1] + bytes([data[-1] ^ 1])
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
                    pass

            def log_message(self, *args):
                pass

        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.cert, key)
        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        url = f'https://127.0.0.1:{self.server.server_port}'
        self.options = {'archive_url': url + '/archive.gz', 'chunk_manifest_url': url + '/chunks.json',
            'chunk_manifest_sha256': F.T.sha(self.chunks), 'ca_file': str(self.cert),
            'network_timeout_seconds': 10}
        self.args = (None, F.T.sha(self.fixture.encoded), F.T.sha(self.fixture.manifest), F.UUID)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def prepare(self):
        plan = R.prepare(*self.args, name='one', **self.options)
        self.fixture.plan_path.write_bytes(R.canonical(plan))
        self.fixture.plan_path.chmod(0o400)
        return plan

    def apply(self):
        return R.apply(*self.args, str(self.fixture.plan_path), name='one', **self.options)

    def test_full_tls_restore_without_local_archive(self):
        # Serving bytes is independent of the on-board local archive pathname.
        self.fixture.archive.unlink()
        plan = self.prepare()
        self.assertNotIn('path', plan['archive'])
        self.assertEqual(plan['archive']['chunk_manifest_sha256'], self.options['chunk_manifest_sha256'])
        self.assertEqual(list(self.fixture.root.iterdir()), [])
        result = self.apply()
        self.assertTrue(result['success'], result)
        self.assertTrue(result['published'] and result['durable'])
        target = self.fixture.root / 'one'
        for name, expected in self.fixture.payload.items():
            self.assertEqual(hashlib.sha256((target / name).read_bytes()).digest(), hashlib.sha256(expected).digest())
        self.assertEqual(self.gets, 3)
        self.assertEqual(set(p.name for p in target.iterdir()),
                         {'Image', 'initramfs.cpio.gz', 'disk.raw', 'enrollment.json'})

    def test_changed_final_chunk_during_copy_never_publishes(self):
        self.prepare()
        self.corrupt_copy = True
        result = self.apply()
        self.assertFalse(result['success'], result)
        self.assertFalse(result['published'])
        self.assertGreater(result['bytes_attempted_staged'], 0)
        self.assertFalse((self.fixture.root / 'one').exists())
        self.assertEqual(list((self.fixture.root / '.enrollment').iterdir()), [])

    def test_changed_sidecar_pin_refused_and_host_guard_precedes_network(self):
        self.options['chunk_manifest_sha256'] = '0' * 64
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertEqual(self.gets, 0)
        with patch.object(R.ENROLL.MANAGEMENT, 'runtime_guard', side_effect=ValueError('unsupported')):
            with patch.object(R, 'module') as network, self.assertRaises(ValueError):
                self.prepare()
        network.assert_not_called()
        self.assertEqual(list(self.fixture.root.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
