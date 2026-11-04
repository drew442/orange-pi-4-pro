"""Exercise the trusted guest CGI with private runtime paths; no VM execution."""
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'configs/guest-network-cgi'


class NetworkConfirmation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.counter = root / 'counter'
        self.confirmed = root / 'confirmed'
        # Substitute only hard-coded guest /run paths inside this private test.
        self.script = SOURCE.read_text().replace('/run/a733-guest-network-counter', str(self.counter)).replace(
            '/run/a733-guest-network-confirmed', str(self.confirmed))

    def run_cgi(self, query='', method='GET'):
        return subprocess.run(['/bin/sh', '-c', self.script], env={**os.environ,
            'REQUEST_METHOD': method, 'QUERY_STRING': query}, capture_output=True, timeout=5)

    def test_read_then_exact_confirmation(self):
        self.counter.write_bytes(b'7\n')
        first = self.run_cgi()
        self.assertEqual(first.returncode, 0)
        self.assertIn(b'a733-kvm-guest-network-v1 counter=7\n', first.stdout)
        self.assertFalse(self.confirmed.exists())
        wrong = self.run_cgi('confirm=8')
        self.assertIn(b'Status: 400', wrong.stdout)
        self.assertFalse(self.confirmed.exists())
        good = self.run_cgi('confirm=7')
        self.assertEqual(good.returncode, 0)
        self.assertIn(b'a733-kvm-guest-network-v1 counter=7\n', good.stdout)
        self.assertEqual(self.confirmed.read_bytes(), b'7\n')
        self.assertEqual(self.counter.read_bytes(), b'7\n')

    def test_invalid_runtime_and_method_cannot_confirm(self):
        for value in (None, b'', b'bad\n', b'1;touch nope\n', b'1' * 10):
            with self.subTest(value=value):
                if value is not None:
                    self.counter.write_bytes(value)
                result = self.run_cgi('confirm=7')
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.confirmed.exists())
        self.counter.write_bytes(b'7\n')
        self.assertNotEqual(self.run_cgi('confirm=7', 'POST').returncode, 0)
        self.assertFalse(self.confirmed.exists())

    def test_confirmation_is_never_visible_with_partial_contents(self):
        self.counter.write_bytes(b'7\n')
        # Simulate preemption after redirection creates the output but before
        # printf writes. The old direct-marker write exposes an empty file here.
        delayed = "printf() { case \"$1\" in '%s\\n') sleep 0.1 ;; esac; command printf \"$@\"; }\n" + self.script
        process = subprocess.Popen(['/bin/sh', '-c', delayed], env={**os.environ,
            'REQUEST_METHOD': 'GET', 'QUERY_STRING': 'confirm=7'}, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 3
            while process.poll() is None:
                self.assertLess(time.monotonic(), deadline)
                if self.confirmed.exists():
                    self.assertEqual(self.confirmed.read_bytes(), b'7\n')
                time.sleep(0.002)
            stdout, stderr = process.communicate(timeout=1)
            self.assertEqual(process.returncode, 0, stderr)
            self.assertIn(b'counter=7', stdout)
            self.assertEqual(self.confirmed.read_bytes(), b'7\n')
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=1)


if __name__ == '__main__':
    unittest.main()
