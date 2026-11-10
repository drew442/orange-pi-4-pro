"""Host-only cold collector checks; no physical power or acceptance evidence."""
import base64
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('cold', ROOT / 'scripts/collect-known-cold-boots.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)
w = c.warm_module()
START = str(uuid.UUID(int=1))


def response(boot=START, **changes):
    data = dict(boot_id=boot, guards_passed=True, hardware_acceptance=False,
                kvm_api=12, irqchip=1, ntp='yes', cpus='0-7', sd_stat=[0]*17,
                el2=True, kernel='6.6.98-a733-diag', release_binding=w.RELEASE_BINDING,
                file_hashes=w.RELEASE_FILES, root_device='/dev/nvme0n1p2', root_id='259:2',
                management_address='192.168.1.116',
                state_marker='a733-state-v1\n360dfb39-f49f-487e-b307-0bb887c854c5',
                nvme=dict(serial='134807190FC700149277', model='SPCC M.2 PCIe SSD', bytes=256060514304),
                sd=dict(cid='035344535530324780716f216e00a8f5', model='SU02G', bytes=1977614336),
                namespaces={'pid': {'dev': 4, 'inode': 0xeffffffc},
                            'user': {'dev': 4, 'inode': 0xeffffffd}, 'mnt': {'dev': 4, 'inode': 3}},
                units=[], initialization_log=['All CPU(s) started at EL2', 'VHE mode initialized successfully'])
    data.update(changes)
    return dict(returncode=0, timed_out=False, truncated=False,
                stdout_b64=base64.b64encode(json.dumps(data).encode()).decode(), stderr_b64='')


class FakeUART:
    def __init__(self, marker=True):
        self.offset = 100
        self.marker = marker
        self.closed = False

    def mark(self):
        return dict(device=1, inode=2, offset=self.offset)

    def read(self):
        data = b'[ 25.0] reboot: Power down\r\n' if self.marker else b''
        self.marker = False
        self.offset += len(data)
        return data

    def close(self):
        self.closed = True


class ColdTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'out').mkdir()
        self.repo = patch.object(c, 'REPO', self.root)
        self.repo.start()
        self.evidence = c.Evidence('fake')
        self.now = 0
        self.current = 1
        self.requests = []
        self.acks = 0
        self.uarts = []

    def tearDown(self):
        self.evidence.close()
        self.repo.stop()
        self.temp.cleanup()

    def sleep(self, seconds):
        self.now += seconds

    def probe(self, expected=None, timeout=30):
        return response(str(uuid.UUID(int=self.current)))

    def request(self, expected, timeout):
        self.requests.append(expected)
        return response(expected)

    def operator(self, deadline):
        self.acks += 1
        self.sleep(10)
        self.current += 1
        return True

    def uart(self):
        result = FakeUART()
        self.uarts.append(result)
        return result

    def collect(self, **changes):
        options = dict(probe=self.probe, request=self.request, preserved=lambda: 'native-retained',
                       operator=self.operator, uart_factory=self.uart, clock=lambda: self.now,
                       sleep=self.sleep, tty=lambda: True)
        options.update(changes)
        with contextlib.redirect_stdout(io.StringIO()):
            return c.collect(self.evidence, START, w, **options)

    def events(self):
        return [json.loads(line) for line in (self.evidence.path / 'events.jsonl').read_text().splitlines()]

    def test_ten_operator_cycles_unique_and_never_reboot(self):
        self.assertEqual(self.collect(), 10)
        self.assertEqual(len(self.requests), 10)
        self.assertEqual(self.acks, 10)
        events = self.events()
        self.assertEqual(len([e for e in events if e['event'] == 'operator-attestation']), 10)
        self.assertTrue(all(e['hardware_acceptance'] is False for e in events))
        self.assertEqual(events[-1]['completed'], 10)
        self.assertTrue(all(u.closed for u in self.uarts))

    def test_no_tty_refuses_before_probe(self):
        with self.assertRaisesRegex(ValueError, 'TTY'):
            self.collect(tty=lambda: False, probe=lambda *a, **k: self.fail('network'))
        self.assertEqual(self.requests, [])

    def test_no_ack_or_too_early_no_second_poweroff(self):
        for answer in (False, True):
            with self.subTest(answer=answer), self.assertRaisesRegex(ValueError, 'attestation|interval'):
                self.collect(operator=lambda deadline: answer)
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(self.events()[-1]['completed'], 0)

    def test_unknown_request_stops_without_retry_or_ack(self):
        raw = dict(returncode=255, timed_out=False, truncated=False,
                   stdout_b64=response()['stdout_b64'], stderr_b64=base64.b64encode(b'other failure').decode())
        calls = []
        def request(*args):
            calls.append(args)
            return raw
        with self.assertRaises(ValueError):
            self.collect(request=request)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.acks, 0)
        self.assertEqual(self.events()[-2]['transport'], raw)

    def test_exact_255_close_retains_unknown_command_exit(self):
        raw = response()
        raw.update(returncode=255, stderr_b64=base64.b64encode(
            b'Connection to 192.168.1.116 closed by remote host.\r\n').decode())
        with self.assertRaisesRegex(ValueError, 'attestation'):
            self.collect(request=lambda *args: raw, operator=lambda deadline: False)
        events = self.events()
        admission = next(e for e in events if e['event'] == 'poweroff-admission')
        self.assertEqual(admission['command_exit'], 'UNKNOWN')
        self.assertEqual(next(e for e in events if e['event'] == 'request-response')['transport']['returncode'], 255)
        self.assertEqual(events[-1]['completed'], 0)

    def test_failed_guards_prevent_poweroff(self):
        for changes in ({'guards_passed': False}, {'file_hashes': {}}, {'sd_stat': [1]*17}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.collect(probe=lambda *a, **k: response(**changes))
        self.assertEqual(self.requests, [])

    def test_missing_powerdown_stops_before_operator(self):
        with self.assertRaisesRegex(ValueError, 'Power down'):
            self.collect(uart_factory=lambda: FakeUART(False))
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.acks, 0)
        self.assertEqual(self.now, 60)

    def test_same_boot_times_out_without_duplicate_request(self):
        def ack(deadline):
            self.sleep(10)
            return True
        with self.assertRaisesRegex(ValueError, '300-second'):
            self.collect(operator=ack)
        self.assertEqual(len(self.requests), 1)

    def test_reused_uuid_stops_after_two_requests(self):
        def ack(deadline):
            self.sleep(10)
            self.current = 2 if self.current == 1 else 1
            return True
        with self.assertRaisesRegex(ValueError, 'reused'):
            self.collect(operator=ack)
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(self.events()[-1]['completed'], 1)

    def test_terminal_release_refusal_during_ready_poll_never_retries_poweroff(self):
        calls = []
        def probe(expected=None, timeout=30):
            calls.append(expected)
            return response() if expected else dict(returncode=78)
        with self.assertRaises(w.TerminalReleaseRefusal):
            self.collect(probe=probe)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(calls, [START, None])

    def test_probe_time_consumes_ready_deadline(self):
        def probe(expected=None, timeout=30):
            if expected:
                return response()
            self.sleep(timeout)
            return dict(returncode=255, timed_out=True, truncated=False)
        with self.assertRaisesRegex(ValueError, 'deadline'):
            self.collect(probe=probe)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.events()[-1]['completed'], 0)

    def test_warm_source_mismatch_refuses_before_import(self):
        (self.root / 'scripts').mkdir()
        (self.root / 'scripts/collect-known-warm-boots.py').write_text('raise AssertionError("executed")')
        with self.assertRaisesRegex(ValueError, 'source pin'):
            c.warm_module()

    def test_operator_wait_counts_toward_eight_hour_cap(self):
        def ack(deadline):
            self.sleep(28800)
            return True
        with self.assertRaisesRegex(ValueError, 'eight-hour'):
            self.collect(operator=ack)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.events()[-1]['completed'], 0)

    def test_plan_network_free_without_loading_warm(self):
        with patch.object(c, 'warm_module', side_effect=AssertionError('loaded')), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(c.main([]), 0)

    def test_execute_needs_tty_tag_and_canonical_uuid(self):
        with contextlib.redirect_stderr(io.StringIO()), patch('sys.stdin.isatty', return_value=False):
            self.assertEqual(c.main(['--execute', '--tag', 't', '--expected-start-boot-id', START]), 1)
        with contextlib.redirect_stderr(io.StringIO()), patch('sys.stdin.isatty', return_value=True), patch('sys.stdout.isatty', return_value=True):
            self.assertEqual(c.main(['--execute']), 1)

    def test_poweroff_source_disables_warm_reboot_and_fresh_guard(self):
        source = c.poweroff_source(w, START).decode()
        self.assertTrue(source.startswith('EXPECTED = ' + repr(START) + '\nREBOOT = False\n'))
        self.assertTrue(source.endswith("subprocess.run(['systemctl', 'poweroff'], check=True, timeout=20)\n"))
        self.assertIn('boot changed before poweroff', source)

    def test_private_no_overwrite_symlink_and_capacity(self):
        self.assertEqual(self.evidence.path.stat().st_mode & 0o777, 0o700)
        with self.assertRaises(FileExistsError):
            c.Evidence('fake')
        with patch('shutil.disk_usage', return_value=type('Space', (), {'free': 256*c.MIB})()):
            with self.assertRaisesRegex(ValueError, 'reserve'):
                c.Evidence('small')
        self.evidence.size = c.BUDGET - 65536
        with self.assertRaisesRegex(ValueError, 'budget'):
            self.evidence.save('poll', extra='x')
        self.evidence.save('failed-or-refused', error='cap')
        (self.root / 'out' / 'cold-boots-link').symlink_to(self.evidence.path)
        with self.assertRaises(FileExistsError):
            c.Evidence('link')

    def test_uart_fresh_offset_and_replacement_refusal(self):
        path = self.root / 'uart.log'
        path.write_bytes(b'reboot: Power down\n')
        uart = c.UART(path)
        try:
            self.assertEqual(uart.read(), b'')
            with path.open('ab') as stream:
                stream.write(b'new bytes')
            self.assertEqual(uart.read(), b'new bytes')
            path.unlink()
            path.write_bytes(b'replacement')
            with self.assertRaisesRegex(ValueError, 'replaced'):
                uart.read()
        finally:
            uart.close()


if __name__ == '__main__':
    unittest.main()
