"""Acquisition tests use loopback TLS and tiny regular files; no device I/O."""
import gzip
import hashlib
import http.server
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import ssl
import subprocess
import tempfile
import time
import sys
import threading
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('acquire_nvme_tests',
    Path(__file__).resolve().parents[1] / 'scripts/acquire-nvme.py')
ACQUIRE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ACQUIRE)


def record(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def fixture(raw=b'tiny authenticated payload'):
    encoded = gzip.compress(raw)
    value = {'schema': 1, 'kernel_release': '6.6.98-a733-diag',
             'logical_sector_bytes': 512, 'physical_sector_bytes': 4096,
             'layout': {'test_only': True},
             'files': {'nvme.img': record(raw), 'nvme.img.gz': record(encoded)},
             'deployment_intent': {'for_installation': False}}
    return value, encoded


class PolicyTests(unittest.TestCase):
    def test_url_rejects_nonhttps_credentials_and_controls(self):
        for url in ('http://host/x', 'https://u:p@host/x', 'https://@host/x',
                    'https://host/x#fragment', 'https://host/x#', 'https://host/\nx', 'https://host/ x',
                    'https://host:bad/x'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                ACQUIRE.https_url(url)
        self.assertEqual(ACQUIRE.public_url('https://host/file?token=secret'), 'https://host/file')

    def test_manifest_pin_precedes_parse_and_rejects_duplicates(self):
        for data, pin in ((b'{', '0'*64), (b'{"schema":1,"schema":1}', None)):
            with self.assertRaises(ValueError):
                ACQUIRE.validate_manifest(data, pin or record(data)['sha256'])

    def test_real_layout_and_malformed_deployment_intent(self):
        value, _ = fixture()
        value['files']['nvme.img']['bytes'] = 250_000_000_000
        value['layout'] = ACQUIRE.LAYOUT.layout(250_000_000_000, 512, 4096)
        for intent in (None, False, {}, {'for_installation': 1}, {'for_installation': True}):
            value['deployment_intent'] = intent
            data = json.dumps(value).encode()
            with self.subTest(intent=intent), self.assertRaises((ValueError, KeyError)):
                ACQUIRE.validate_manifest(data, record(data)['sha256'])
        del value['deployment_intent']
        data = json.dumps(value).encode()
        self.assertNotIn('deployment_intent', ACQUIRE.validate_manifest(data, record(data)['sha256'])[0])

    def test_deadline_guard_preserves_handler_and_rejects_existing_alarm_or_blocked_signal(self):
        previous_handler = signal.getsignal(signal.SIGALRM)
        previous_timer = signal.getitimer(signal.ITIMER_REAL)
        self.assertEqual(previous_timer, (0.0, 0.0))
        def handler(signum, frame):
            pass
        signal.signal(signal.SIGALRM, handler)
        try:
            with self.assertRaises(TimeoutError), ACQUIRE.deadline_guard(.01):
                time.sleep(.1)
            self.assertIs(signal.getsignal(signal.SIGALRM), handler)
            self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))
            signal.setitimer(signal.ITIMER_REAL, 60, 60)
            with self.assertRaises(ValueError), ACQUIRE.deadline_guard(1):
                self.fail('active alarm must prevent entry')
            delay, interval = signal.getitimer(signal.ITIMER_REAL)
            self.assertTrue(59 < delay <= 60)
            self.assertEqual(interval, 60)
            self.assertIs(signal.getsignal(signal.SIGALRM), handler)
            signal.setitimer(signal.ITIMER_REAL, 0)
            previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, [signal.SIGALRM])
            try:
                with self.assertRaises(ValueError), ACQUIRE.deadline_guard(1):
                    self.fail('blocked alarm must prevent entry')
                self.assertIn(signal.SIGALRM, signal.pthread_sigmask(signal.SIG_BLOCK, []))
            finally:
                signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
        finally:
            signal.setitimer(signal.ITIMER_REAL, *previous_timer)
            signal.signal(signal.SIGALRM, previous_handler)

    def test_deadline_guard_restores_alarm_state_after_baseexception(self):
        previous = signal.getsignal(signal.SIGALRM)
        with self.assertRaises(KeyboardInterrupt), ACQUIRE.deadline_guard(1):
            raise KeyboardInterrupt()
        self.assertEqual(signal.getsignal(signal.SIGALRM), previous)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))

    def test_private_permissions_fail_closed_if_filesystem_ignores_requested_mode(self):
        actual = os.fchmod
        with tempfile.TemporaryDirectory() as temp, ACQUIRE.directory(temp) as fd:
            with patch.object(ACQUIRE.os, 'fchmod', side_effect=lambda descriptor, mode:
                    actual(descriptor, 0o644)):
                with self.assertRaises(ValueError):
                    ACQUIRE.new_file(fd, 'private')
            self.assertFalse((Path(temp) / 'private').exists())

    def test_acquisition_rejects_nonmain_thread_before_creating_output(self):
        errors = []
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'new'
            def run():
                try:
                    ACQUIRE.acquire('https://host/manifest', 'https://host/image', 'a'*64,
                                    output, 'ram')
                except ValueError as error:
                    errors.append(str(error))
            thread = threading.Thread(target=run)
            thread.start()
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(len(errors), 1)
            self.assertFalse(output.exists())

    def test_ca_rejects_symlink_fifo_directory_and_symlink_ancestor(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'regular').write_text('invalid CA')
            (root / 'link').symlink_to(root / 'regular')
            (root / 'ancestor').symlink_to(root, target_is_directory=True)
            os.mkfifo(root / 'fifo')
            for path in (root / 'link', root / 'fifo', root, root / 'ancestor/regular'):
                with self.subTest(path=path), self.assertRaises((ValueError, OSError)):
                    ACQUIRE.tls_context(path)

    def test_storage_classes_and_real_recursive_topologies(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sysroot = root / 'sys'
            sysroot.mkdir()
            with ACQUIRE.directory(root) as fd:
                dev = os.fstat(fd).st_dev
                identity = f'{os.major(dev)}:{os.minor(dev)}'
                def mount(fs):
                    return f'1 0 {identity} / / rw - {fs} ignored rw\n'
                ACQUIRE.storage_guard(fd, 'ram', mount('tmpfs'))
                for kind, fs in (('ram', 'ext4'), ('external', 'tmpfs'), ('external', 'nfs')):
                    with self.assertRaises(ValueError):
                        ACQUIRE.storage_guard(fd, kind, mount(fs), sysroot)
                # Real synthetic sysfs nodes exercise the writer's recursive resolver.
                whole = root / 'sda'
                whole.mkdir()
                (whole / 'dev').write_text('8:0')
                (whole / 'slaves').mkdir()
                part = whole / 'sda1'
                part.mkdir()
                (part / 'dev').write_text(identity)
                (part / 'partition').write_text('1')
                (sysroot / identity).symlink_to(part)
                (sysroot / '8:0').symlink_to(whole)
                ACQUIRE.storage_guard(fd, 'external', mount('ext4'), sysroot)
                (sysroot / identity).unlink()
                for name in ('mmcblk0', 'loop0', 'ram0', 'unknown0'):
                    node = root / name
                    node.mkdir()
                    (node / 'dev').write_text(identity)
                    (node / 'slaves').mkdir()
                    (sysroot / identity).symlink_to(node)
                    with self.subTest(name=name), self.assertRaises(ValueError):
                        ACQUIRE.storage_guard(fd, 'external', mount('ext4'), sysroot)
                    (sysroot / identity).unlink()
                mmc = root / 'mmcblk1'
                mmc.mkdir()
                (mmc / 'dev').write_text('179:0')
                (mmc / 'slaves').mkdir()
                (sysroot / '179:0').symlink_to(mmc)
                mapper = root / 'dm-0'
                mapper.mkdir()
                (mapper / 'dev').write_text(identity)
                (mapper / 'slaves').mkdir()
                (mapper / 'slaves/mmcblk1').symlink_to(mmc)
                (sysroot / identity).symlink_to(mapper)
                with self.assertRaises(ValueError):
                    ACQUIRE.storage_guard(fd, 'external', mount('ext4'), sysroot)


@unittest.skipUnless(shutil.which('openssl'), 'loopback TLS tests require openssl')
class TLSTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.cert, cls.key = cls.root / 'cert.pem', cls.root / 'key.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
            '-keyout', str(cls.key), '-out', str(cls.cert), '-days', '1', '-subj', '/CN=localhost',
            '-addext', 'subjectAltName=DNS:localhost'], check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.routes = {}
        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.0'
            def do_GET(self):
                if self.path == '/drip':
                    try:
                        self.wfile.write(b'HTTP/1.0 200 OK\r\n')
                        self.wfile.flush()
                        for index in range(12):
                            time.sleep(.04)
                            self.wfile.write(b'X-Pad: value\r\n')
                            self.wfile.flush()
                        self.wfile.write(b'Content-Length: 0\r\n\r\n')
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
                        pass
                    return
                status, headers, body = cls.routes[self.path.split('?')[0]]
                self.send_response(status)
                for key, value in headers:
                    self.send_header(key, value)
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
                    pass
            def log_message(self, *args):
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
        self.value, self.encoded = fixture()
        self.data = json.dumps(self.value, indent=2).encode() + b'\n'
        self.routes['/manifest'] = (200, [], self.data)
        self.routes['/image'] = (200, [], self.encoded)

    def acquire(self, output, **changes):
        args = dict(manifest_url=self.url + '/manifest?secret=TOKEN',
                    image_url=self.url + '/image?secret=TOKEN',
                    manifest_sha256=record(self.data)['sha256'], output=output,
                    storage_kind='ram', ca_file=self.cert, max_seconds=30)
        args.update(changes)
        # Tiny payloads explicitly mock only geometry, plus environment storage policy.
        with patch.object(ACQUIRE, 'storage_guard'), patch.object(ACQUIRE.LAYOUT, 'layout',
                return_value={'test_only': True}):
            return ACQUIRE.acquire(**args)

    def test_verified_success_preserves_manifest_fixture_and_private_modes(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'new'
            with patch.dict(os.environ, {'HTTPS_PROXY': 'http://127.0.0.1:1'}):
                result = self.acquire(output)
            self.assertFalse(result['deployment_intent']['for_installation'])
            self.assertTrue(result['encoded_verified'])
            self.assertFalse(result['decoded_verified'])
            self.assertEqual((output / 'manifest.json').read_bytes(), self.data)
            self.assertEqual((output / 'nvme.img.gz').read_bytes(), self.encoded)
            self.assertNotIn('TOKEN', (output / 'acquisition.json').read_text())
            self.assertEqual(output.stat().st_mode & 0o777, 0o700)
            for path in output.iterdir():
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                self.acquire(output)

    def test_truthful_content_lengths(self):
        self.routes['/manifest'] = (200, [('Content-Length', str(len(self.data)))], self.data)
        self.routes['/image'] = (200, [('Content-Length', str(len(self.encoded)))], self.encoded)
        with tempfile.TemporaryDirectory() as temp:
            self.assertTrue(self.acquire(Path(temp) / 'new')['success'])

    def test_tls_redirect_hash_length_encoding_and_truncation_fail_closed(self):
        failures = [
            ({'ca_file': None}, None),
            ({'manifest_sha256': '0'*64}, None),
            ({}, (302, [('Location', self.url + '/manifest')], b'')),
            ({}, (200, [('Content-Encoding', 'gzip')], self.encoded)),
            ({}, (200, [('Content-Length', 'invalid')], self.encoded)),
            ({}, (200, [('Content-Length', str(len(self.encoded) + 1))], self.encoded)),
            ({}, (200, [], self.encoded[:-1])),
            ({}, (200, [], self.encoded + b'overflow')),
            ({}, (200, [], b'x'*len(self.encoded))),
        ]
        for args, route in failures:
            with self.subTest(args=args, route=route), tempfile.TemporaryDirectory() as temp:
                self.routes['/image'] = route or (200, [], self.encoded)
                output = Path(temp) / 'new'
                with self.assertRaises((ValueError, OSError)):
                    self.acquire(output, **args)
                self.assertFalse((output / 'acquisition.json').exists())
                self.assertFalse((output / 'manifest.json').exists())

    def test_authenticated_expanded_payload_is_deferred_to_writer(self):
        for raw in (b'x' * 100000, b'wrong raw bytes'):
            encoded = gzip.compress(raw)
            self.value['files']['nvme.img.gz'] = record(encoded)
            self.data = json.dumps(self.value).encode()
            self.routes['/manifest'] = (200, [], self.data)
            self.routes['/image'] = (200, [], encoded)
            with tempfile.TemporaryDirectory() as temp:
                output = Path(temp) / 'new'
                result = self.acquire(output)
                self.assertTrue(result['success'])
                self.assertFalse(result['decoded_verified'])

    def test_output_symlink_ancestor_and_storage_guard_before_creation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'link').symlink_to(root, target_is_directory=True)
            with self.assertRaises(OSError):
                self.acquire(root / 'link/new')
            with patch.object(ACQUIRE, 'storage_guard', side_effect=ValueError('SD forbidden')):
                with self.assertRaises(ValueError):
                    ACQUIRE.acquire(self.url + '/manifest', self.url + '/image',
                        record(self.data)['sha256'], root / 'new', 'external', self.cert)
            self.assertFalse((root / 'new').exists())

    def test_free_space_deadline_and_exclusive_publication(self):
        from types import SimpleNamespace
        for failure in ('space', 'deadline', 'publication'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temp:
                output = Path(temp) / 'new'
                original = ACQUIRE.new_file
                def new_file(fd, name):
                    if failure == 'publication' and name == 'manifest.json':
                        (output / name).write_bytes(b'preexisting')
                    return original(fd, name)
                with patch.object(ACQUIRE, 'new_file', side_effect=new_file):
                    if failure == 'space':
                        with patch.object(ACQUIRE.os, 'fstatvfs', return_value=
                                SimpleNamespace(f_bavail=1, f_frsize=1)):
                            with self.assertRaises(ValueError):
                                self.acquire(output)
                    elif failure == 'deadline':
                        with self.assertRaises((ValueError, TimeoutError)):
                            self.acquire(output, max_seconds=0.000001)
                    else:
                        with self.assertRaises(FileExistsError):
                            self.acquire(output)
                        self.assertEqual((output / 'manifest.json').read_bytes(), b'preexisting')
                self.assertFalse((output / 'acquisition.json').exists())

    def test_authenticated_bad_gzip_crc_is_deferred_to_writer(self):
        encoded = self.encoded[:-8] + bytes(8)
        self.value['files']['nvme.img.gz'] = record(encoded)
        self.data = json.dumps(self.value).encode()
        self.routes['/manifest'] = (200, [], self.data)
        self.routes['/image'] = (200, [], encoded)
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'new'
            result = self.acquire(output)
            self.assertTrue(result['success'])
            self.assertFalse(result['decoded_verified'])

    def test_owned_alarm_is_disarmed_before_success_or_failure_leaves_cleanup_scope(self):
        actual = ACQUIRE._acquire
        actual_remove = ACQUIRE.remove_owned
        def completed(*args, **kwargs):
            result = actual(*args, **kwargs)
            self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))
            return result
        with tempfile.TemporaryDirectory() as temp, patch.object(ACQUIRE, '_acquire',
                side_effect=completed):
            self.assertTrue(self.acquire(Path(temp) / 'new')['success'])
        def remove(fd, name, identity):
            self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))
            actual_remove(fd, name, identity)
        actual_check = ACQUIRE.check_directory
        calls = 0
        def fail_final(fd, path):
            nonlocal calls
            calls += 1
            actual_check(fd, path)
            if calls == 2:
                raise ValueError('forced failure with an active deadline')
        with tempfile.TemporaryDirectory() as temp, patch.object(ACQUIRE, 'remove_owned',
                side_effect=remove), patch.object(ACQUIRE, 'check_directory', side_effect=fail_final):
            output = Path(temp) / 'new'
            with self.assertRaises(ValueError):
                self.acquire(output)
            self.assertFalse((output / 'manifest.json').exists())
            self.assertFalse((output / 'acquisition.json').exists())

    def test_cleanup_keeps_foreign_replacement_and_removes_owned_receipt(self):
        original = ACQUIRE.check_directory
        for replace in (False, True):
            with self.subTest(replace=replace), tempfile.TemporaryDirectory() as temp:
                output = Path(temp) / 'new'
                calls = 0
                def check(fd, path):
                    nonlocal calls
                    calls += 1
                    original(fd, path)
                    if calls == 2:
                        if replace:
                            (output / 'acquisition.json').unlink()
                            (output / 'acquisition.json').write_bytes(b'foreign replacement')
                        raise ValueError('forced final check failure')
                with patch.object(ACQUIRE, 'check_directory', side_effect=check):
                    with self.assertRaises(ValueError):
                        self.acquire(output)
                self.assertFalse((output / 'manifest.json').exists())
                if replace:
                    self.assertEqual((output / 'acquisition.json').read_bytes(), b'foreign replacement')
                else:
                    self.assertFalse((output / 'acquisition.json').exists())

    def test_slow_dripped_headers_obey_overall_deadline_in_subprocess(self):
        # A subprocess bounds a regression even if the production timer is broken.
        script = """
import importlib.util, json, signal, sys, time
from pathlib import Path
spec = importlib.util.spec_from_file_location('probe_acquire', sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
m.storage_guard = lambda *args: None
previous = signal.getsignal(signal.SIGALRM)
started = time.monotonic()
try:
    m.acquire(sys.argv[2] + '/drip', sys.argv[2] + '/image', 'a'*64,
              Path(sys.argv[4]), 'ram', sys.argv[3], max_seconds=.12)
except (ValueError, TimeoutError):
    pass
else:
    raise SystemExit('unexpected acquisition success')
print(json.dumps({'elapsed': time.monotonic() - started,
                  'handler_restored': signal.getsignal(signal.SIGALRM) == previous,
                  'timer': signal.getitimer(signal.ITIMER_REAL)}))
"""
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'new'
            result = subprocess.run([sys.executable, '-c', script, str(SPEC.origin),
                self.url, str(self.cert), str(output)], capture_output=True, text=True,
                timeout=4, check=True)
            value = json.loads(result.stdout)
            self.assertLess(value['elapsed'], .35)
            self.assertTrue(value['handler_restored'])
            self.assertEqual(value['timer'], [0.0, 0.0])
            self.assertFalse((output / 'acquisition.json').exists())
            self.assertFalse((output / 'manifest.json').exists())

    def test_manifest_cap_and_missing_content_length(self):
        self.routes['/manifest'] = (200, [], b'x' * (4*ACQUIRE.MIB + 1))
        with tempfile.TemporaryDirectory() as temp, self.assertRaises(ValueError):
            self.acquire(Path(temp) / 'new')


if __name__ == '__main__':
    unittest.main()
