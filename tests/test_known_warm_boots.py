import base64
import contextlib
import importlib.util
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import tempfile
import types
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('warm', ROOT / 'scripts/collect-known-warm-boots.py')
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)
START = str(uuid.UUID(int=1))


def response(boot=START, **changes):
    data = dict(boot_id=boot, guards_passed=True, hardware_acceptance=False,
                kvm_api=12, irqchip=1, ntp='yes', cpus='0-7', sd_stat=[0]*17,
                el2=True, kernel='6.6.98-a733-diag',
                release_binding=w.RELEASE_BINDING.copy(), file_hashes=w.RELEASE_FILES.copy(),
                root_device='/dev/nvme0n1p2', root_id='259:2')
    data.update(changes)
    return dict(returncode=0, timed_out=False, truncated=False,
                stdout_b64=base64.b64encode(json.dumps(data).encode()).decode())

# Exact retained first warm-request SSH response (2026-11-10); no device access.
CLOSED_REQUEST = {
    'argv': ['ssh', '-T', '-i', '/home/drew/.ssh/id_ed25519', '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'HostKeyAlias=192.168.1.116', '-o', 'ConnectTimeout=5', 'root@192.168.1.116', 'python3 -I -B -'],
    'returncode': 255,
    'timed_out': False,
    'truncated': False,
    'stdout_b64': (
        'eyJyZWxlYXNlX2JpbmRpbmciOiB7Im1hbmlmZXN0X3NoYTI1NiI6ICIzY2JlMjhkNjE1NGQxYmRlYjhkMzY0YmQwZjhiZjgy'
        'YjYxZmQ0MGFmN2JlMjdmYmQ0OTY5NTc3MWM0ZmFkMDhmIiwgImd6aXBfc2hhMjU2IjogIjNiYzAyZjQ2ZDI3MmRjYWM1OTFk'
        'NDM1ZmZmMmIxYmM5OGQ0ZmIwNDg1NjlmYjIxMjA3NDYxNDU5MjlhOGFkNjQiLCAicmF3X3NoYTI1NiI6ICIxZmE5MTQ0ZDdh'
        'MTI2OTA2NmM5MWE5NGJmYjEwNWE5YWM0ZTQwODNkNDcwN2RkNjc5MWU4YWMyZjI2OTAyY2Q5IiwgInNsb3QiOiAiQSIsICJy'
        'b290X2RldmljZSI6ICIvZGV2L252bWUwbjFwMiIsICJyb290X2lkIjogIjI1OToyIiwgImJvb3Rfcm91dGUiOiAia25vd24t'
        'U1UwMkctU0Q7IGluZGVwZW5kZW50IGZpcm13YXJlIGF1ZGl0IGFuZCBzZXJpYWwgcmV2aWV3IHJlcXVpcmVkIn0sICJmaWxl'
        'X2hhc2hlcyI6IHsiL3Vzci9saWIvb3JhbmdlcGktaHlwZXJ2aXNvci9ob3N0LW1hbmFnZW1lbnQucHkiOiAiNjZmYmVhZjhh'
        'OTM5YWQwNzNmMTgzYjg4YTM5MTBiZmQ2Mjc5NGZmYTljNDg1ZjJjMWYxYWRhYzVhYjVmODcyOCIsICIvdXNyL2xpYi9vcmFu'
        'Z2VwaS1oeXBlcnZpc29yL3BheWxvYWQuanNvbiI6ICIyODY0OTQwNzNjZTNhMGI2OTQ0M2Y5NjM4OWRiYmNlMTFhYjAxNTcw'
        'NWY3ZTlmMDVjN2E3Y2YwNjUwM2RhODY2IiwgIi9ydW4vYTczMy1zdGF0ZS9ib290L3NlbGVjdGVkLmNvbmYiOiAiM2M3NmI5'
        'NmMzMjhiNDUwOGZmNDA3Yzg1OTQzNTJlMDQzODk2NjRlOTdjZWQ2ZjBhMDkyODRjYWRlNWJkMDI3YiJ9LCAicm9vdF9kZXZp'
        'Y2UiOiAiL2Rldi9udm1lMG4xcDIiLCAicm9vdF9pZCI6ICIyNTk6MiIsICJib290X2lkIjogImQ5ZTk0ZDU0LTc5YmItNDlj'
        'My1hZDcyLTI2NzJhOWM3MWM2YSIsICJndWFyZHNfcGFzc2VkIjogdHJ1ZSwgImhhcmR3YXJlX2FjY2VwdGFuY2UiOiBmYWxz'
        'ZSwgImt2bV9hcGkiOiAxMiwgImlycWNoaXAiOiAxLCAibnRwIjogInllcyIsICJjcHVzIjogIjAtNyIsICJzZF9zdGF0Ijog'
        'WzE5NjQsIDAsIDM4NjY3MTIsIDMxMjY4NCwgMCwgMCwgMCwgMCwgMCwgMTY1MDMyLCAzMTI2ODQsIDAsIDAsIDAsIDAsIDAs'
        'IDBdLCAidW5pdHMiOiBbXSwgImVsMiI6IHRydWUsICJrZXJuZWwiOiAiNi42Ljk4LWE3MzMtZGlhZyIsICJuYW1lc3BhY2Vz'
        'IjogeyJwaWQiOiB7ImRldiI6IDQsICJpbm9kZSI6IDQwMjY1MzE4MzZ9LCAidXNlciI6IHsiZGV2IjogNCwgImlub2RlIjog'
        'NDAyNjUzMTgzN30sICJtbnQiOiB7ImRldiI6IDQsICJpbm9kZSI6IDQwMjY1MzE4NDF9fSwgIm1hbmFnZW1lbnRfYWRkcmVz'
        'cyI6ICIxOTIuMTY4LjEuMTE2IiwgInN0YXRlX21hcmtlciI6ICJhNzMzLXN0YXRlLXYxXG4zNjBkZmIzOS1mNDlmLTQ4N2Ut'
        'YjMwNy0wYmI4ODdjODU0YzUiLCAibnZtZSI6IHsic2VyaWFsIjogIjEzNDgwNzE5MEZDNzAwMTQ5Mjc3IiwgIm1vZGVsIjog'
        'IlNQQ0MgTS4yIFBDSWUgU1NEIiwgImJ5dGVzIjogMjU2MDYwNTE0MzA0fSwgInNkIjogeyJjaWQiOiAiMDM1MzQ0NTM1NTMw'
        'MzI0NzgwNzE2ZjIxNmUwMGE4ZjUiLCAibW9kZWwiOiAiU1UwMkciLCAiYnl0ZXMiOiAxOTc3NjE0MzM2fSwgImluaXRpYWxp'
        'emF0aW9uX2xvZyI6IFsiWyAgICAwLjMwNTY2OV0gQ1BVOiBBbGwgQ1BVKHMpIHN0YXJ0ZWQgYXQgRUwyIiwgIlsgICAgMS43'
        'OTY2NzNdIGt2bSBbMV06IFZIRSBtb2RlIGluaXRpYWxpemVkIHN1Y2Nlc3NmdWxseSJdfQo='
    ),
    'stderr_b64': 'Q29ubmVjdGlvbiB0byAxOTIuMTY4LjEuMTE2IGNsb3NlZCBieSByZW1vdGUgaG9zdC4NCg==',
}
CLOSED_BOOT = json.loads(base64.b64decode(CLOSED_REQUEST['stdout_b64']))['boot_id']


class WarmTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'out').mkdir()
        self.repopatch = patch.object(w, 'REPO', self.root)
        self.repopatch.start()
        self.evidence = w.Evidence('test')

    def tearDown(self):
        self.evidence.close()
        self.repopatch.stop()
        self.temp.cleanup()

    def events(self):
        return [json.loads(line) for line in (self.evidence.path / 'events.jsonl').read_text().splitlines()]

    def test_plan_has_no_transport_or_reboot(self):
        with patch.object(w, 'transport', side_effect=AssertionError('transport')), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(w.main([]), 0)

    def test_explicit_execution_requires_uuid(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(w.main(['--execute']), 1)
            self.assertEqual(w.main(['--execute', '--expected-start-boot-id', START.upper() + 'x']), 1)

    def test_ten_distinct_sequences(self):
        calls = []
        current = [1]
        def probe(expected=None, reboot=False, timeout=30):
            calls.append((expected, reboot))
            if reboot:
                old = current[0]
                current[0] += 1
                return response(str(uuid.UUID(int=old)))
            return response(str(uuid.UUID(int=current[0])))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(w.collect(self.evidence, START, probe, lambda: 'retained'), 10)
        self.assertEqual(sum(reboot for _, reboot in calls), 10)
        self.assertEqual(len([x for x in self.events() if x['event'] == 'observed']), 10)
        self.assertTrue(all(x['hardware_acceptance'] is False for x in self.events()))

    def test_exact_closed_request_is_admission_only_then_one_observed_boot(self):
        original = json.dumps(CLOSED_REQUEST, sort_keys=True)
        records = iter([response(CLOSED_BOOT), CLOSED_REQUEST, response(START),
                        response(START, release_binding={})])
        calls = []
        def probe(expected=None, reboot=False, timeout=30):
            calls.append(reboot)
            return next(records)
        with self.assertRaisesRegex(ValueError, 'release binding'), contextlib.redirect_stdout(io.StringIO()):
            w.collect(self.evidence, CLOSED_BOOT, probe, lambda: 'retained')
        events = self.events()
        self.assertEqual(calls, [False, True, False, False])
        self.assertEqual(events[-1]['completed'], 1)
        self.assertEqual(json.dumps(CLOSED_REQUEST, sort_keys=True), original)
        self.assertEqual(next(e['transport'] for e in events if e['event'] == 'request-response'), CLOSED_REQUEST)
        closed = next(e for e in events if e['event'] == 'reboot-admission-transport-closed')
        self.assertEqual(closed['command_exit'], 'UNKNOWN')
        self.assertEqual(closed['completed'], 0)
        self.assertEqual([e['boot_id'] for e in events if e['event'] == 'observed'], [START])

    def test_ten_closed_request_sequences(self):
        current, requests = [1], []
        def probe(expected=None, reboot=False, timeout=30):
            old = str(uuid.UUID(int=current[0]))
            if not reboot:
                return response(old)
            requests.append(expected)
            data = json.loads(base64.b64decode(CLOSED_REQUEST['stdout_b64']))
            data['boot_id'] = old
            current[0] += 1
            return {**CLOSED_REQUEST, 'stdout_b64': base64.b64encode(json.dumps(data).encode()).decode()}
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(w.collect(self.evidence, START, probe, lambda: 'retained'), 10)
        self.assertEqual(len(requests), 10)
        self.assertEqual(len([e for e in self.events() if e['event'] == 'observed']), 10)
        self.assertEqual(len([e for e in self.events() if e['event'] == 'reboot-admission-transport-closed']), 10)

    def test_closed_request_failures_stop_before_polling_or_more_requests(self):
        invalid = [{**CLOSED_REQUEST, **changes} for changes in (
            {'timed_out': True}, {'truncated': True}, {'returncode': 1},
            {'stdout_b64': ''}, {'stdout_b64': 'e30='}, {'stdout_b64': '!'},
            {'stderr_b64': ''}, {'stderr_b64': '!'},
            {'stderr_b64': base64.b64encode(b'Connection reset by peer\n').decode()})]
        data = json.loads(base64.b64decode(CLOSED_REQUEST['stdout_b64']))
        for key in data:
            missing = {k: v for k, v in data.items() if k != key}
            invalid.append({**CLOSED_REQUEST, 'stdout_b64': base64.b64encode(json.dumps(missing).encode()).decode()})
        for changes in ({'release_binding': {}}, {'boot_id': START}, {'guards_passed': False},
                        {'namespaces': {}}, {'units': [{'unit': 'a733-guest', 'MainPID': '9', 'ActiveState': 'active', 'SubState': 'running'}]},
                        {'state_marker': 'wrong'}, {'initialization_log': []}):
            invalid.append({**CLOSED_REQUEST, 'stdout_b64': base64.b64encode(json.dumps({**data, **changes}).encode()).decode()})
        for raw in invalid:
            with self.subTest(raw=raw):
                records = iter([response(CLOSED_BOOT), raw])
                calls = []
                def probe(expected=None, reboot=False, timeout=30):
                    calls.append(reboot)
                    return next(records)
                with self.assertRaises((ValueError, KeyError, TypeError)), contextlib.redirect_stdout(io.StringIO()):
                    w.collect(self.evidence, CLOSED_BOOT, probe, lambda: 'retained')
                self.assertEqual(calls, [False, True])
                self.assertEqual(self.events()[-1]['completed'], 0)

    def test_closed_request_is_not_ordinary_probe_success(self):
        with self.assertRaisesRegex(ValueError, 'SSH probe failed'):
            w.decode(CLOSED_REQUEST)

    def test_closed_request_old_boot_hits_deadline_without_reissue_or_count(self):
        ticks, requests = [0], []
        def probe(expected=None, reboot=False, timeout=30):
            if reboot:
                requests.append(expected)
                return CLOSED_REQUEST
            return response(CLOSED_BOOT)
        def sleep(seconds): ticks[0] += seconds
        with self.assertRaisesRegex(ValueError, 'deadline'), contextlib.redirect_stdout(io.StringIO()):
            w.collect(self.evidence, CLOSED_BOOT, probe, lambda: 'retained', lambda: ticks[0], sleep)
        self.assertEqual(requests, [CLOSED_BOOT])
        self.assertEqual(ticks[0], 300)
        self.assertEqual(self.events()[-1]['completed'], 0)
        self.assertFalse(any(e['event'] == 'observed' for e in self.events()))

    def test_selected_bytes_use_canonical_root_selection_format(self):
        spec = importlib.util.spec_from_file_location('warm_test_selection', ROOT / 'build/root-selection.py')
        selection = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(selection)
        values = selection.parse(w.SELECTED_BYTES)
        self.assertEqual(selection.serialize(values), w.SELECTED_BYTES)
        self.assertEqual(values['SLOT'], 'A')
        self.assertEqual(values['ROOT_MARKER_SHA256'],
                         w.RELEASE_FILES['/usr/lib/orangepi-hypervisor/payload.json'])
        self.assertEqual(hashlib.sha256(w.SELECTED_BYTES).hexdigest(),
                         w.RELEASE_FILES['/run/a733-state/boot/selected.conf'])

    def test_release_binding_and_each_file_hash_are_required(self):
        for changes in ({'release_binding': {}}, {'root_device': '/dev/nvme0n1p3'},
                        {'root_id': '259:3'}, {'file_hashes': {}}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, 'release binding'):
                w.decode(response(**changes))
        for path in w.RELEASE_FILES:
            hashes = w.RELEASE_FILES.copy()
            hashes[path] = '0'*64
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'release binding'):
                w.decode(response(file_hashes=hashes))

    def test_post_reboot_release_mismatch_stops_series_without_more_reboots(self):
        calls = []
        def probe(expected=None, reboot=False, timeout=30):
            calls.append(reboot)
            if len(calls) < 3:
                return response()
            return response(str(uuid.UUID(int=2)), release_binding={})
        with self.assertRaisesRegex(ValueError, 'release binding'), contextlib.redirect_stdout(io.StringIO()):
            w.collect(self.evidence, START, probe, lambda: 'retained')
        self.assertEqual(calls, [False, True, False])
        self.assertEqual(self.events()[-1]['completed'], 0)

    def test_no_preservation_never_probes(self):
        def refuse(): raise ValueError('not retained')
        with self.assertRaises(ValueError):
            w.collect(self.evidence, START, lambda *a, **k: self.fail('probe'), refuse)
        self.assertEqual(self.events()[-1]['completed'], 0)

    def test_invalid_admission_never_requests_reboot(self):
        for changes in ({'kvm_api': 11}, {'irqchip': 0}, {'el2': False}, {'ntp': 'no'}, {'cpus': '0-6'}, {'release_binding': {}}, {'file_hashes': {}}, {'sd_stat': [0]*4+[1]+[0]*12}):
            calls = []
            def probe(*a, **k):
                calls.append(k)
                return response(**changes)
            with self.assertRaises(ValueError):
                w.collect(self.evidence, START, probe, lambda: 'retained')
            self.assertFalse(any(x.get('reboot') for x in calls))

    def test_same_boot_times_out_and_retains_raw(self):
        ticks = [0]
        calls = []
        def probe(*args, **kwargs):
            calls.append(kwargs)
            return response()
        def sleep(seconds): ticks[0] += seconds
        with self.assertRaisesRegex(ValueError, 'deadline'), contextlib.redirect_stdout(io.StringIO()):
            w.collect(self.evidence, START, probe, lambda: 'retained', lambda: ticks[0], sleep)
        self.assertEqual(sum(c.get('reboot', False) for c in calls), 1)
        self.assertEqual(self.events()[-1]['completed'], 0)
        self.assertTrue(any(x['event'] == 'poll' and 'transport' in x for x in self.events()))

    def test_reused_prior_boot_stops_with_partial_log(self):
        sequence = iter([response(), response(), response(str(uuid.UUID(int=2))), response(str(uuid.UUID(int=2))), response(str(uuid.UUID(int=2))), response()])
        with self.assertRaisesRegex(ValueError, 'reused'), contextlib.redirect_stdout(io.StringIO()):
            w.collect(self.evidence, START, lambda *a, **k: next(sequence), lambda: 'retained')
        self.assertEqual(self.events()[-1]['completed'], 1)
        self.assertEqual(sum(x['event'] == 'requested' for x in self.events()), 2)

    def test_failed_transport_retains_partial_response(self):
        failed = dict(returncode=255, timed_out=True, truncated=False, stdout_b64='e30=')
        with self.assertRaises(ValueError):
            w.collect(self.evidence, START, lambda *a, **k: failed, lambda: 'retained')
        self.assertEqual(self.events()[1]['transport'], failed)

    def test_bounded_response_rejected(self):
        for key in ('timed_out', 'truncated'):
            raw = response()
            raw[key] = True
            with self.assertRaises(ValueError): w.decode(raw)

    def test_retention_requires_snapshot_not_directory(self):
        (self.root / 'out/endurance-outcome-20261109-01').mkdir()
        with self.assertRaises(ValueError): w.retained_outcome()


class PreservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.directory = self.root / 'out/endurance-outcome-20261109-01'
        self.directory.mkdir(parents=True)
        self.stack = contextlib.ExitStack()
        self.stack.enter_context(patch.object(w, 'REPO', self.root))
        self.stack.enter_context(patch.object(w.TRANSPORT, 'UART', self.root / 'uart-full.log'))
        self.snapshot = self.fixture()
        self.save()

    def tearDown(self):
        self.stack.close()
        self.temp.cleanup()

    @staticmethod
    def capture(raw, journal=False):
        result = dict(returncode=0, timed_out=False, truncated=False, stderr_b64='',
                      stdout_b64=base64.b64encode(raw).decode())
        if journal:
            result.update(possible_truncation=False, records_captured=raw.count(b'\n'), record_limit=2000)
        return result

    @staticmethod
    def tail(path, raw, offset=0):
        return dict(path=str(path), size_at_open=offset+len(raw), offset=offset,
                    truncated=offset>0, data_b64=base64.b64encode(raw).decode())

    def fixture(self):
        units = '\n\n'.join('Id='+name+'\nLoadState=loaded\nActiveState=failed\nSubState=failed\nMainPID=0\nExecMainCode=1\nExecMainStatus=1' for name in w.TRANSPORT.UNITS)
        observer = (json.dumps(dict(event='ready', unit=w.TRANSPORT.UNITS[0]))+'\n').encode()
        contents = dict.fromkeys(w.TRANSPORT.FILES, b'')
        contents.update({'release-exit-observer.jsonl': observer,
                         'release-exit-observer.stderr': b'observer failed: unit failed (failed)\n',
                         'management-watch.jsonl': b'{"event":"sample"}\n'})
        journal = (json.dumps({'_BOOT_ID': w.BOOT.replace('-', ''), 'MESSAGE':'Main process exited, status=1/FAILURE'})+'\n').encode()
        return dict(expected_boot_id=w.BOOT, actual_boot_id=w.BOOT, guest_run_id=w.RUN,
                    boot_matches=True, units=self.capture(units.encode()),
                    files=[self.tail('/run/corrected-kvm/'+name, contents[name]) for name in w.TRANSPORT.FILES],
                    guest_journal=self.capture(journal, True), kernel_journal=self.capture(journal, True),
                    manager_journal=self.capture(journal, True), dmesg=self.capture(b'kernel log\n'))

    def save(self, uart=True):
        records = {
            'summary.json':dict(expected_boot_id=w.BOOT, guest_run_id=w.RUN, phase='collector-exit'),
            'collector.json':self.capture(b'LoadState=loaded\nActiveState=failed\nMainPID=0\nExecMainStatus=1\n'),
            'remote-transport.json':self.capture(json.dumps(self.snapshot).encode())}
        if uart:
            records['uart-tail.json'] = self.tail(w.TRANSPORT.UART, b'console evidence\n', offset=100)
        for name, record in records.items():
            (self.directory / name).write_text(json.dumps(record))
        if not uart:
            (self.directory / 'uart-tail.json').unlink(missing_ok=True)

    def assert_refused_before_probe(self):
        evidence = w.Evidence('refused')
        try:
            with self.assertRaises(ValueError):
                w.collect(evidence, START, lambda *a, **k:self.fail('probe before preservation'), w.retained_outcome)
        finally:
            evidence.close()

    def test_complete_failed_endurance_is_retained_and_admitted(self):
        self.assertEqual(w.retained_outcome(), str(self.directory))

    def test_reviewer_nested_failed_no_uart_reproduction_refuses(self):
        for key in ('units', 'guest_journal', 'kernel_journal', 'dmesg', 'manager_journal'):
            self.snapshot[key]['returncode'] = 1
        self.snapshot['files'][0] = {'path':'/run/corrected-kvm/release-exit-observer.jsonl', 'error':'missing'}
        self.save(uart=False)
        self.assert_refused_before_probe()

    def test_missing_uart_refuses_before_probe(self):
        self.save(uart=False)
        self.assert_refused_before_probe()

    def test_readable_full_uart_archive_can_supply_missing_tail(self):
        self.save(uart=False)
        w.TRANSPORT.UART.write_bytes(b'full retained console\n')
        self.assertEqual(w.retained_outcome(), str(self.directory))

    def test_missing_observer_or_cpu_file_refuses(self):
        for filename in ('release-exit-observer.stderr', 'host-cpu-release.stdout', 'host-cpu-release.stderr'):
            with self.subTest(filename=filename):
                self.snapshot = self.fixture()
                self.snapshot['files'] = [f for f in self.snapshot['files'] if not f['path'].endswith('/'+filename)]
                self.save()
                with self.assertRaises(ValueError): w.retained_outcome()

    def test_nested_capture_failure_timeout_and_truncation_refuse(self):
        for key, change in (('units', {'returncode':1}), ('guest_journal', {'timed_out':True}),
                            ('kernel_journal', {'possible_truncation':True}),
                            ('manager_journal', {'truncated':True}), ('dmesg', {'error':'read failed'}),
                            ('dmesg', {'extra':{'error':'hidden failure'}})):
            with self.subTest(key=key):
                self.snapshot = self.fixture()
                self.snapshot[key].update(change)
                self.save()
                with self.assertRaises(ValueError): w.retained_outcome()

    def test_tail_watch_and_uart_are_allowed_but_not_cpu(self):
        watch = next(f for f in self.snapshot['files'] if f['path'].endswith('/management-watch.jsonl'))
        watch.update(self.tail(watch['path'], b'partial\n{"event":"sample"}\n', offset=100))
        self.save()
        self.assertEqual(w.retained_outcome(), str(self.directory))
        cpu = next(f for f in self.snapshot['files'] if f['path'].endswith('/host-cpu-release.stdout'))
        cpu.update(self.tail(cpu['path'], b'retained suffix', offset=10))
        self.save()
        with self.assertRaises(ValueError): w.retained_outcome()

    def test_malformed_base64_and_uart_extent_refuse(self):
        self.snapshot['dmesg']['stdout_b64'] = '@bad'
        self.save()
        with self.assertRaises(ValueError): w.retained_outcome()
        self.snapshot = self.fixture()
        self.save()
        path = self.directory / 'uart-tail.json'
        record = json.loads(path.read_text())
        record['size_at_open'] += 1
        path.write_text(json.dumps(record))
        with self.assertRaises(ValueError): w.retained_outcome()


class ManualStopTests(unittest.TestCase):
    """Data-shaped GC captures require an independently pinned proof chain."""
    capture = staticmethod(PreservationTests.capture)
    tail = staticmethod(PreservationTests.tail)
    fixture = PreservationTests.fixture
    save = PreservationTests.save

    def setUp(self):
        PreservationTests.setUp(self)
        self.proof_files = {}
        self.collector_props = dict(Id=w.COLLECTOR, LoadState='not-found',
            ActiveState='inactive', SubState='dead', MainPID='0', Result='success',
            ExecMainCode='0', ExecMainStatus='0', InvocationID='')
        self.write_collector()
        self.native = dict(Id=w.COLLECTOR, LoadState='loaded', ActiveState='active',
            SubState='running', MainPID='3869075', InvocationID='e14e2bae19e04a9baa203eb1b2480627',
            ExecStart='python3 collector --boot-id '+w.BOOT)
        self.events = [dict(operation=op, unit=unit) for unit in
            ('a733-endurance-outcome-final-20261109.timer', w.COLLECTOR)
            for op in ('stop-attempt', 'stop-result')]
        self.events[-1].update(returncode=0, stderr='', state=dict(MainPID='0', ActiveState='inactive', SubState='dead'))
        self.board = []
        for unit, pid, status in ((w.TRANSPORT.UNITS[0], '2153', '0'),
                                  (w.TRANSPORT.UNITS[1], '2169', '2')):
            self.board.extend([dict(event='operator-stop-attempt', unit=unit,
                before=dict(LoadState='loaded', MainPID=pid)),
                dict(event='operator-stop-result', unit=unit, returncode=0, stderr='',
                     after=dict(MainPID='0', ExecMainStatus=status))])
            if pid == '2153':
                self.board.append(dict(event='guest-raw-exit-before-observer-release',
                    state=self.board[-1]['after'].copy(), guest_requested_shutdown=False))
        self.board.extend([dict(event='observer-after-guest-exit', state=dict(MainPID='0')),
            dict(event='management-watch-state'), dict(event='management-watch-stop', returncode=0),
            dict(event='all-terminal', units=[dict(MainPID='0') for _ in w.TRANSPORT.UNITS])])
        self.bind_proof()

    def write_collector(self):
        data = ('\n'.join(k+'='+v for k, v in self.collector_props.items())+'\n').encode()
        (self.directory / 'collector.json').write_text(json.dumps(self.capture(data)))

    def bind_proof(self):
        def lines(rows): return ''.join(json.dumps(row)+'\n' for row in rows).encode()
        self.proof_files.update({
            w.STOP_BASE+'policy-change.json': json.dumps(dict(hardware_acceptance=False,
                termination_classification='planned operator stop; guest-requested clean shutdown not established')).encode(),
            w.STOP_BASE+'native-stop-events.jsonl': lines(self.events),
            w.STOP_BASE+'board-prestop-03.json': json.dumps(dict(boot_id=w.BOOT)).encode(),
            w.STOP_BASE+'board-stop-events.jsonl': lines(self.board)})
        for name in ('summary.json', 'collector.json', 'remote-transport.json', 'uart-tail.json'):
            self.proof_files[str(self.directory.relative_to(self.root) / name)] = (self.directory / name).read_bytes()
        for relative, raw in self.proof_files.items():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        native = ('\n'.join(k+'='+v for k, v in self.native.items())+'\n').encode()
        (self.root / w.STOP_BASE / 'native-prestop-units.txt').write_bytes(native)
        result = dict(host_boot_id=w.BOOT, guest_run_id=w.RUN, operator_stop=True,
            records=[dict(path=p, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
                     for p, raw in self.proof_files.items()])
        raw = json.dumps(result).encode()
        path = self.root / w.STOP_RESULT
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        # Fixture trust anchors replace only the production digest, never validation.
        w.STOP_RESULT_SHA256 = hashlib.sha256(raw).hexdigest()
        w.PRESTOP_PIN = (len(native), hashlib.sha256(native).hexdigest())

    def tearDown(self):
        PreservationTests.tearDown(self)
        w.STOP_RESULT_SHA256 = '7168f8d1604c4aa375c862d045c07fd6b37be599c73196743b6ef63e0feaf679'
        w.PRESTOP_PIN = (17939, '07fbbd203dbabf2858bc9807f1e3fab2c103045c081fe11e212493818995c7a1')

    def test_authenticated_manual_stop_admits_gc(self):
        self.assertEqual(w.retained_outcome(), str(self.directory))

    def test_each_missing_changed_or_truncated_proof_refuses(self):
        paths = [self.root / p for p in self.proof_files] + [self.root / w.STOP_RESULT,
            self.root / w.STOP_BASE / 'native-prestop-units.txt']
        for path in paths:
            original = path.read_bytes()
            for replacement in (None, original+b' ', original[:len(original)//2]):
                with self.subTest(path=path.name, replacement=replacement is None):
                    if replacement is None: path.unlink()
                    else: path.write_bytes(replacement)
                    with self.assertRaises(ValueError): w.retained_outcome()
                    path.write_bytes(original)

    def test_proof_semantics_refuse_even_with_fixture_hashes_rebound(self):
        cases = [('native', 'MainPID', '42'), ('native', 'LoadState', 'not-found'),
                 ('native', 'ExecStart', '--boot-id '+START), ('events', 'returncode', 1),
                 ('events', 'returncode', True), ('events', 'state', dict(MainPID='42'))]
        for target, key, value in cases:
            record = self.native if target == 'native' else self.events[-1]
            previous = record[key]
            record[key] = value
            self.bind_proof()
            with self.assertRaises(ValueError): w.retained_outcome()
            record[key] = previous
        self.bind_proof()
        self.board[-1]['units'][0]['MainPID'] = '2153'
        self.bind_proof()
        with self.assertRaises(ValueError): w.retained_outcome()

    def test_unsupported_not_found_state_refuses(self):
        for key, value in [('ActiveState', 'active'), ('SubState', 'failed'),
                           ('MainPID', '42'), ('ExecMainStatus', '1'), ('Id', 'other')]:
            previous = self.collector_props[key]
            self.collector_props[key] = value
            self.write_collector()
            self.bind_proof()
            with self.assertRaises(ValueError): w.retained_outcome()
            self.collector_props[key] = previous
        self.write_collector()

    def test_wrong_retained_run_boot_or_incomplete_capture_refuses(self):
        for key, value in [('guest_run_id', START), ('actual_boot_id', START),
                           ('expected_boot_id', START)]:
            original = self.snapshot[key]
            self.snapshot[key] = value
            self.save()
            self.write_collector()
            self.bind_proof()
            with self.assertRaises(ValueError): w.retained_outcome()
            self.snapshot[key] = original
        self.snapshot = self.fixture()
        for filename in w.TRANSPORT.FILES:
            original = self.snapshot['files']
            self.snapshot['files'] = [r for r in original if r['path'].rsplit('/', 1)[-1] != filename]
            self.save()
            self.write_collector()
            self.bind_proof()
            with self.assertRaises(ValueError): w.retained_outcome()
            self.snapshot['files'] = original

    def test_symlink_proof_refuses(self):
        path = self.root / w.STOP_BASE / 'native-stop-events.jsonl'
        real = path.with_suffix('.saved')
        path.rename(real)
        path.symlink_to(real)
        with self.assertRaises(ValueError): w.retained_outcome()



class RemoteGuardTests(unittest.TestCase):
    """Run the actual remote source with a fixed fake kernel/systemd transport."""
    def run_remote(self, *, fd_target='socket:[1]', unit_pid='0', model='SPCC M.2 PCIe SSD', inode=None, reboot=False, ntp='yes', log='All CPU(s) started at EL2\nkvm: VHE mode initialized successfully\n', api=12, irq=1, sd_field=None, release_change=None, root_source='/dev/nvme0n1p2', transport_record=False, metadata_change=None):
        files = {
            '/proc/sys/kernel/random/boot_id': START,
            '/run/a733-state/.a733-state': 'a733-state-v1\n360dfb39-f49f-487e-b307-0bb887c854c5',
            '/sys/class/block/nvme0n1/device/serial': '134807190FC700149277',
            '/sys/class/block/nvme0n1/device/model': model,
            '/sys/class/block/nvme0n1/size': str(256060514304//512),
            '/sys/class/block/mmcblk1/device/cid': '035344535530324780716f216e00a8f5',
            '/sys/class/block/mmcblk1/device/name': 'SU02G',
            '/sys/class/block/mmcblk1/size': str(1977614336//512),
            '/sys/class/block/mmcblk1/dev': '179:0',
            '/sys/class/block/mmcblk1/stat': ' '.join(['0']*17),
            '/sys/devices/system/cpu/online': '0-7',
            '/proc/1/task/1/mountinfo': '1 0 259:2 / / ro - ext4 /dev/nvme0n1p2 ro',
        }
        # Fixture source executes into the module only after its captured hash passes.
        module_bytes = b'fixture_module_loaded = True\n'
        payload_bytes = b'{"slot":"A","fixture":"normalized-release"}\n'
        files.update({
            '/usr/lib/orangepi-hypervisor/host-management.py': module_bytes.decode(),
            '/usr/lib/orangepi-hypervisor/payload.json': payload_bytes.decode(),
            '/run/a733-state/boot/selected.conf': w.SELECTED_BYTES.decode(),
            '/proc/self/mountinfo': 'fixture mount table',
        })
        fixture_hashes = {path: hashlib.sha256(files[path].encode()).hexdigest() for path in w.RELEASE_FILES}
        if release_change:
            path, contents = release_change
            if contents is None: del files[path]
            else: files[path] = contents
        if sd_field is not None:
            counters = [0]*17
            counters[sd_field] = 1
            files['/sys/class/block/mmcblk1/stat'] = ' '.join(map(str, counters))
        class FakePath(PurePosixPath):
            def lstat(self):
                import stat
                if str(self) in w.RELEASE_FILES and str(self) not in files:
                    raise FileNotFoundError(str(self))
                result = types.SimpleNamespace(st_mode=(stat.S_IFREG|0o644 if str(self) in files and str(self) != '/proc/self/mountinfo' else stat.S_IFDIR|0o755), st_uid=0, st_gid=0, st_nlink=1, st_dev=1, st_ino=2, st_size=len(files.get(str(self), '')), st_mtime_ns=1, st_ctime_ns=1)
                if metadata_change and str(self) == metadata_change[0]:
                    for key, value in metadata_change[1].items(): setattr(result, key, value)
                return result
            def exists(self): return False
            def iterdir(self):
                return iter([FakePath(p) for p in {
                    '/proc': ['/proc/1'], '/proc/1/task': ['/proc/1/task/1'],
                    '/proc/1/task/1/fd': ['/proc/1/task/1/fd/3']
                }.get(str(self), [])])
        class File(io.BytesIO):
            def fileno(self): return -1
        def read(path, mode):
            if str(path) not in files: raise FileNotFoundError(str(path))
            stream = File(files[str(path)].encode())
            stream.info = FakePath(path).lstat()
            opened_stream[0] = stream
            return stream
        opened_stream = [None]
        def nsstat(path):
            name = str(path).rsplit('/', 1)[-1]
            return types.SimpleNamespace(st_dev=1, st_ino=inode or {'pid':0xeffffffc, 'user':0xeffffffd, 'mnt':3}[name])
        descriptors = {}
        def opened(path, flags):
            descriptors[len(descriptors)+4] = str(path)
            return len(descriptors)+3
        def fstat(fd): return opened_stream[0].info if fd == -1 else nsstat(descriptors[fd])
        def ioctl(fd, op, arg=None):
            if op == 0xae00: return api
            if op == 0xae03: return irq
            return {'pid':0x20000000, 'user':0x10000000, 'mnt':0x20000}[descriptors[fd].rsplit('/', 1)[-1]]
        def command(argv, timeout):
            if argv[0] == 'timedatectl': return ntp.encode()
            if argv[0] == 'dmesg': return log.encode()
            if argv[1] == 'list-units': return b'[{"unit":"a733-endurance-host-cpu-release-20261109.service"}]'
            return ('ActiveState=active\nSubState=exited\nMainPID='+unit_pid+'\n').encode()
        management = types.SimpleNamespace(runtime_guard=lambda: {}, current_address=lambda config: '192.168.1.116',
            mount_table=lambda data: {'/': [dict(source=root_source, id='259:2', root='/', flags={'ro'})]})
        loader = types.SimpleNamespace(exec_module=lambda module: None)
        with contextlib.ExitStack() as stack:
            for target, value in {
                'pathlib.Path':FakePath, 'builtins.open':read, 'os.geteuid':lambda:0,
                'os.stat':nsstat, 'os.fstat':fstat, 'os.open':opened, 'os.close':lambda fd:None,
                'os.readlink':lambda path:fd_target, 'os.uname':lambda:types.SimpleNamespace(release='6.6.98-a733-diag'),
                'fcntl.ioctl':ioctl, 'subprocess.check_output':command,
                'importlib.util.spec_from_file_location':lambda *a:types.SimpleNamespace(loader=loader),
                'importlib.util.module_from_spec':lambda spec:management,
            }.items(): stack.enter_context(patch(target, value))
            run = stack.enter_context(patch('subprocess.run'))
            output = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            errors = stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
            source = w.REMOTE.replace('RELEASE_FILES = ' + repr(w.RELEASE_FILES),
                                      'RELEASE_FILES = ' + repr(fixture_hashes), 1)
            code = 0
            try:
                exec(source, {'EXPECTED': START, 'REBOOT': reboot})
            except SystemExit as error:
                if not transport_record: raise
                code = error.code
            except (ValueError, OSError):
                if not transport_record: raise
                code = 1
            if transport_record:
                return dict(returncode=code, timed_out=False, truncated=False,
                            stdout_b64=base64.b64encode(output.getvalue().encode()).decode(),
                            stderr_b64=base64.b64encode(errors.getvalue().encode()).decode()), run.call_args_list
            return output.getvalue(), run.call_args_list

    def test_remote_idle_active_exited_is_allowed_and_clean_command(self):
        raw, calls = self.run_remote(reboot=True)
        self.assertTrue(json.loads(raw)['guards_passed'])
        self.assertEqual(calls[0].args[0], ['systemctl', 'reboot'])

    def test_remote_wrong_marker_selector_and_module_refuse_before_reboot(self):
        for path in w.RELEASE_FILES:
            with self.subTest(path=path), self.assertRaises(SystemExit) as error:
                self.run_remote(reboot=True, release_change=(path, 'changed-release\n'))
            self.assertEqual(error.exception.code, 78)

    def test_remote_other_slot_root_refuses(self):
        with self.assertRaises(SystemExit) as error:
            self.run_remote(reboot=True, root_source='/dev/nvme0n1p3')
        self.assertEqual(error.exception.code, 78)

    def test_real_remote_release_refusals_stop_polling_without_further_reboots(self):
        cases = [dict(release_change=(path, 'changed-release\n')) for path in w.RELEASE_FILES]
        cases += [dict(root_source='/dev/nvme0n1p3')]
        cases += [dict(release_change=(path, None)) for path in w.RELEASE_FILES]
        cases += [dict(metadata_change=(path, {'st_nlink': 2})) for path in w.RELEASE_FILES]
        for changes in cases:
            with self.subTest(changes=changes):
                refusal, remote_calls = self.run_remote(transport_record=True, reboot=True, **changes)
                self.assertEqual(refusal['returncode'], w.RELEASE_REFUSAL_CODE)
                self.assertEqual(remote_calls, [])
                self.assertIn(b'Final release refused:', base64.b64decode(refusal['stderr_b64']))
                records = iter([response(), response(), refusal])
                calls = []
                def probe(expected=None, reboot=False, timeout=30):
                    calls.append(reboot)
                    return next(records)
                with tempfile.TemporaryDirectory() as directory, patch.object(w, 'REPO', Path(directory)):
                    (Path(directory) / 'out').mkdir()
                    evidence = w.Evidence('terminal')
                    try:
                        with self.assertRaises(w.TerminalReleaseRefusal), contextlib.redirect_stdout(io.StringIO()):
                            w.collect(evidence, START, probe, lambda: 'retained')
                    finally:
                        evidence.close()
                    events = [json.loads(line) for line in (evidence.path / 'events.jsonl').read_text().splitlines()]
                self.assertEqual(calls, [False, True, False])
                self.assertEqual(events[-1]['completed'], 0)
                self.assertEqual(events[-2]['transport'], refusal)

    def test_generic_remote_startup_failure_still_retries(self):
        startup, remote_calls = self.run_remote(transport_record=True, ntp='no')
        self.assertEqual(startup['returncode'], 1)
        self.assertEqual(remote_calls, [])
        boot2 = str(uuid.UUID(int=2))
        records = iter([response(), response(), startup, response(boot2),
                        response(boot2, release_binding={})])
        calls = []
        def probe(expected=None, reboot=False, timeout=30):
            calls.append(reboot)
            return next(records)
        with tempfile.TemporaryDirectory() as directory, patch.object(w, 'REPO', Path(directory)):
            (Path(directory) / 'out').mkdir()
            evidence = w.Evidence('retry')
            try:
                with self.assertRaisesRegex(ValueError, 'release binding'), contextlib.redirect_stdout(io.StringIO()):
                    w.collect(evidence, START, probe, lambda: 'retained', sleep=lambda seconds: None)
            finally:
                evidence.close()
            events = [json.loads(line) for line in (evidence.path / 'events.jsonl').read_text().splitlines()]
        self.assertEqual(calls, [False, True, False, False, False])
        self.assertEqual(events[-1]['completed'], 1)

    def test_remote_vm_and_vcpu_refuse(self):
        for target in ('anon_inode:kvm-vm', 'anon_inode:kvm-vcpu:0'):
            with self.assertRaisesRegex(ValueError, 'KVM descriptor'): self.run_remote(fd_target=target)

    def test_remote_running_cpu_refuses(self):
        with self.assertRaisesRegex(ValueError, 'workload unit'): self.run_remote(unit_pid='44')

    def test_remote_bad_identity_refuses(self):
        with self.assertRaisesRegex(ValueError, 'NVMe differs'): self.run_remote(model='other')

    def test_remote_api_and_irqchip_are_required(self):
        for changes in ({'api': 11}, {'irq': 0}):
            with self.assertRaisesRegex(ValueError, 'KVM API'): self.run_remote(**changes)

    def test_remote_ntp_and_el2_are_required(self):
        for changes in ({'ntp': 'no'}, {'log': 'HYP mode not available'}):
            with self.assertRaises(ValueError): self.run_remote(**changes)

    def test_remote_sd_write_discard_flush_each_refuse(self):
        for index in (4, 6, 11, 13, 15, 16):
            with self.assertRaisesRegex(ValueError, 'SD writes'): self.run_remote(sd_field=index)

    def test_remote_namespace_proof_requires_initial_inode(self):
        with self.assertRaisesRegex(ValueError, 'initial namespace'): self.run_remote(inode=123)


if __name__ == '__main__': unittest.main()
