"""Native collector policy/transport simulations; no physical acceptance evidence."""
import base64
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('spi_collector', ROOT / 'scripts/collect-spi-boots.py')
C = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(C)
OLD = '11111111-1111-4111-8111-111111111111'
NEW = '22222222-2222-4222-8222-222222222222'
THIRD = '33333333-3333-4333-8333-333333333333'
GOOD = b'HELLO! BOOT0 is starting!\n' + b'\n'.join(C.MARKERS)
WARM = b'reboot: Restarting system\n' + GOOD
COLD = b'reboot: Power down\n'


class Clock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now

    def sleep(self, amount):
        self.now += amount


class UART:
    def __init__(self, chunks):
        self.chunks = iter(chunks)
        self.closed = False

    def mark(self):
        return dict(inode=1, device=2, offset=3)

    def read(self):
        return next(self.chunks, b'')

    def close(self):
        self.closed = True


class Native:
    def __init__(self, observations, chunks=(WARM,), *, deadline=28800):
        self.deadline = self.phase_deadline = deadline
        self.observations = iter(observations)
        self.last = dict(state='transport-unknown')
        self.actions = []
        self.u = UART(chunks)
        self.response = dict(returncode=None, timed_out=True, stdout_b64='cGFydGlhbA==', stderr_b64='')

    def observe(self):
        self.actions.append(('observe',))
        self.last = next(self.observations, self.last)
        return self.last

    def components(self, boot):
        self.actions.append(('components', boot))
        return dict(state='components', boot_id=boot)

    def stage(self, boot):
        self.actions.append(('stage', boot))

    def qualify(self, boot):
        self.actions.append(('qualify', boot))
        return dict(head='a' * 64, registry='b' * 64)

    def request(self, intent):
        self.actions.append(('request', intent['old_boot_id']))
        return self.response

    def uart(self):
        return self.u

    def attest(self, deadline):
        self.actions.append(('attest',))
        return True


def ready(boot):
    return dict(state='ready', boot_id=boot)


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.out = Path(self.temp.name)
        self.clock = Clock()

    def tearDown(self):
        self.temp.cleanup()

    def setup_collector(self, native, tag='case'):
        evidence = C.Evidence(self.out, tag)
        intents = C.Intents(self.out)
        collector = C.Collector(native, evidence, intents, clock=self.clock, sleep=self.clock.sleep)
        return collector, evidence, intents

    def test_default_plan_has_no_operational_io(self):
        with patch.object(C, 'source_bytes', side_effect=AssertionError), \
             patch.object(C, 'Evidence', side_effect=AssertionError), \
             patch.object(C, 'Native', side_effect=AssertionError):
            self.assertEqual(C.main([]), 0)

    def test_invalid_execution_rejected_before_io(self):
        cases = [[], ['--kind', 'warm', '--count', '0'], ['--kind', 'warm', '--count', '11'],
                 ['--kind', 'warm', '--count', '1', '--expected-start-boot-id', 'bad'],
                 ['--kind', 'warm', '--count', '1', '--expected-start-boot-id', OLD, '--tag', '../bad']]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(SystemExit):
                C.parse_args(['--execute', *case])

    def test_cold_requires_tty_and_observe_has_minimal_interface(self):
        with patch.object(C.sys.stdin, 'isatty', return_value=False), self.assertRaises(SystemExit):
            C.parse_args(['--execute', '--kind', 'cold', '--count', '1',
                          '--expected-start-boot-id', OLD, '--tag', 'case'])
        with self.assertRaises(SystemExit):
            C.parse_args(['--observe-only', '--tag', 'case', '--count', '1'])

    def test_source_pins_frozen_and_corruption_refuses(self):
        captured = C.source_bytes()
        self.assertEqual(set(captured), set(C.PINS))
        for name, raw in captured.items():
            self.assertEqual((len(raw), C.digest(raw)), C.PINS[name])
        with patch.object(C.os, 'read', return_value=b'x' * 15424), self.assertRaises(ValueError):
            C.source_bytes()
        with self.assertRaises(ValueError):
            C.captured_modules({k: base64.b64encode(b'x').decode() for k in C.PINS})

    def test_exclusive_publication_and_fsync_failure_remain_blocking(self):
        evidence = C.Evidence(self.out, 'publish')
        target = evidence.path / 'one.json'
        with patch.object(C.os, 'fsync', side_effect=OSError('fsync failed')):
            with self.assertRaises(OSError):
                C.publish(target, b'one')
        self.assertTrue(target.exists())
        with self.assertRaises(FileExistsError):
            C.publish(target, b'two')
        with self.assertRaises(FileExistsError):
            C.Evidence(self.out, 'publish')

    def test_global_intent_durable_before_one_request(self):
        native = Native([ready(OLD), ready(OLD), ready(NEW)])
        collector, evidence, intents = self.setup_collector(native)
        original_request = native.request
        def request(value):
            retained = json.loads(C.read_private(intents.path / (OLD + '.json'), 262144))
            self.assertEqual(retained['request_id'], value['request_id'])
            self.assertEqual(len(list(evidence.path.glob('*-requested.json'))), 1)
            return original_request(value)
        native.request = request
        result = collector.run('warm', 1, OLD)
        self.assertEqual(result['completed_count'], 1)
        self.assertEqual([x for x in native.actions if x[0] == 'request'], [('request', OLD)])
        self.assertEqual([x for x in native.actions if x[0] == 'qualify'],
                         [('qualify', OLD), ('qualify', NEW)])
        saved = json.loads(next(evidence.path.glob('*-request-result.json')).read_bytes())
        self.assertTrue(saved['raw']['timed_out'])
        self.assertEqual(saved['raw']['stdout_b64'], 'cGFydGlhbA==')
        self.assertFalse(result['hardware_acceptance'])

    def test_fresh_tag_cannot_sync_or_request_old_boot(self):
        native = Native([ready(OLD)])
        collector, _, intents = self.setup_collector(native)
        intents.create(dict(old_boot_id=OLD, request_id=NEW))
        fresh = Native([ready(OLD)])
        c2, _, _ = self.setup_collector(fresh, 'fresh')
        with self.assertRaisesRegex(ValueError, 'existing global intent'):
            c2.run('warm', 1, OLD)
        self.assertEqual(fresh.actions, [('observe',)])

    def test_unknown_request_polls_only_old_boot_until_deadline(self):
        native = Native([ready(OLD)], deadline=22)
        collector, _, _ = self.setup_collector(native)
        with self.assertRaisesRegex(ValueError, 'deadline'):
            collector.run('warm', 1, OLD)
        self.assertEqual([x for x in native.actions if x[0] == 'request'], [('request', OLD)])
        self.assertEqual([x for x in native.actions if x[0] == 'qualify'], [('qualify', OLD)])
        self.assertTrue(native.u.closed)
        self.assertLessEqual(self.clock.now, 22)

    def test_publication_fsync_failure_prevents_request(self):
        native = Native([ready(OLD)])
        collector, _, intents = self.setup_collector(native)
        original = intents.create
        def broken(value):
            original(value)
            raise OSError('directory fsync unknown')
        intents.create = broken
        with self.assertRaises(OSError):
            collector.run('warm', 1, OLD)
        self.assertFalse(any(x[0] == 'request' for x in native.actions))
        with self.assertRaises(ValueError):
            intents.check(OLD)

    def test_unsafe_partial_global_namespace_refuses(self):
        collector, _, intents = self.setup_collector(Native([]))
        C.private_directory(intents.path, create=True)
        C.publish(intents.path / (OLD + '.json'), b'{')
        with self.assertRaises(ValueError):
            intents.check(NEW)

    def test_seen_boot_rejected_without_sync(self):
        native = Native([ready(OLD), ready(NEW)])
        collector, _, _ = self.setup_collector(native)
        collector.seen.add(NEW)
        with self.assertRaisesRegex(ValueError, 'reused boot'):
            collector.run('warm', 1, OLD)
        self.assertNotIn(('qualify', NEW), native.actions)

    def test_baseline_identity_change_and_guard_refusal_fatal(self):
        for value in (ready(NEW), dict(state='refused', boot_id=OLD, error='root mismatch')):
            native = Native([value])
            collector, _, _ = self.setup_collector(native, str(len(list(self.out.iterdir()))))
            with self.assertRaises(ValueError):
                collector.run('warm', 1, OLD)
            self.assertFalse(any(x[0] == 'stage' for x in native.actions))

    def test_series_cap_and_new_boot_deadline(self):
        self.assertEqual(C.SERIES_SECONDS, 28800)
        native = Native([ready(OLD)])
        collector, _, _ = self.setup_collector(native)
        with self.assertRaisesRegex(ValueError, 'deadline'):
            collector.run('warm', 1, OLD)
        self.assertEqual(self.clock.now, 300)

    def test_cold_attestation_follows_powerdown_then_new_uart(self):
        native = Native([ready(OLD), ready(NEW)], chunks=(COLD, b'', GOOD))
        collector, evidence, _ = self.setup_collector(native)
        result = collector.run('cold', 1, OLD)
        self.assertEqual(result['completed_count'], 1)
        self.assertIn(('attest',), native.actions)
        event = json.loads(next(evidence.path.glob('*-physical-attestation.json')).read_bytes())
        self.assertEqual(event['uart_offset'], len(COLD))

    def test_observe_only_no_staging_qualification_power_or_intent(self):
        native = Native([ready(OLD), ready(NEW)])
        collector, evidence, intents = self.setup_collector(native)
        collector.run('warm', 1, OLD)
        before = {p: p.read_bytes() for p in evidence.path.iterdir()}
        global_before = {p: p.read_bytes() for p in intents.path.iterdir()}
        observer = Native([ready(THIRD)])
        observation = C.Evidence(self.out, 'case', observe=True)
        result = C.observe_only(observer, observation)
        self.assertEqual(observer.actions, [('observe',)])
        self.assertEqual(result['outcome'], 'distinct boot observed')
        self.assertFalse(result['credit'])
        self.assertEqual(before, {p: p.read_bytes() for p in evidence.path.iterdir()})
        self.assertEqual(global_before, {p: p.read_bytes() for p in intents.path.iterdir()})

    def test_uart_order_stale_multi_boot_and_panic_rejected(self):
        self.assertTrue(C.uart_evidence(WARM, 'warm'))
        self.assertFalse(C.uart_evidence(GOOD + b'reboot: Restarting system', 'warm'))
        self.assertFalse(C.uart_evidence(b'reboot: Restarting system' + GOOD[::-1], 'warm'))
        with self.assertRaises(ValueError):
            C.uart_evidence(WARM + GOOD, 'warm')
        with self.assertRaises(ValueError):
            C.uart_evidence(b'reboot: Restarting system\n' + WARM, 'warm')
        with self.assertRaises(ValueError):
            C.uart_evidence(WARM + b'Kernel panic', 'warm')
        self.assertFalse(C.uart_evidence(COLD + GOOD, 'cold'))
        self.assertTrue(C.uart_evidence(COLD + GOOD, 'cold', len(COLD)))

    def test_execute_lock_excludes_other_tags_and_is_noninheritable(self):
        lock = C.ExecutionLock(self.out)
        try:
            self.assertFalse(os.get_inheritable(lock.fd))
            with self.assertRaises(BlockingIOError):
                C.ExecutionLock(self.out)
        finally:
            lock.close()
        again = C.ExecutionLock(self.out)
        again.close()

    def test_intent_race_after_components_prevents_staging(self):
        native = Native([ready(OLD)])
        collector, _, intents = self.setup_collector(native)
        component = native.components
        def race(boot):
            value = component(boot)
            intents.create(dict(old_boot_id=boot, request_id=NEW))
            return value
        native.components = race
        with self.assertRaisesRegex(ValueError, 'global intent'):
            collector.run('warm', 1, OLD)
        self.assertFalse(any(x[0] in ('stage', 'qualify', 'request') for x in native.actions))

    def test_phase_expiry_after_components_prevents_staging_and_sync(self):
        native = Native([ready(OLD)])
        collector, _, _ = self.setup_collector(native)
        component = native.components
        def expire(boot):
            value = component(boot)
            self.clock.now = 301
            return value
        native.components = expire
        with self.assertRaisesRegex(ValueError, 'deadline'):
            collector.run('warm', 1, OLD)
        self.assertFalse(any(x[0] in ('stage', 'qualify', 'request') for x in native.actions))

    def test_uart_tail_panic_and_second_boot_prevent_credit(self):
        for tail in (b'Kernel panic', GOOD):
            tag = 'tail' + str(len(list(self.out.iterdir())))
            native = Native([ready(OLD), ready(NEW)], chunks=(WARM, b'x' * 65536, tail))
            collector, _, _ = self.setup_collector(native, tag)
            with self.assertRaises(ValueError):
                collector.run('warm', 1, OLD)
            self.assertFalse(collector.completed)
            self.assertNotIn(('qualify', NEW), native.actions)

    def test_uart_new_error_during_qualification_prevents_credit(self):
        native = Native([ready(OLD), ready(NEW)])
        collector, _, _ = self.setup_collector(native)
        qualify = native.qualify
        def changed(boot):
            value = qualify(boot)
            if boot == NEW:
                native.u.chunks = iter((b'Kernel panic',))
            return value
        native.qualify = changed
        with self.assertRaisesRegex(ValueError, 'panic'):
            collector.run('warm', 1, OLD)
        self.assertFalse(collector.completed)

    def test_observe_refused_foreign_root_never_labels_distinct(self):
        original = C.Evidence(self.out, 'observe')
        original.save('requested', old_boot_id=OLD)
        observer = Native([dict(state='refused', boot_id=NEW, error='foreign root')])
        evidence = C.Evidence(self.out, 'observe', observe=True)
        value = C.observe_only(observer, evidence)
        self.assertEqual(value['outcome'], 'uncertain')
        self.assertEqual(value['observed']['error'], 'foreign root')
        self.assertFalse(value['credit'])

    def test_native_rpc_refusal_and_captured_templates(self):
        evidence = C.Evidence(self.out, 'rpc')
        native = C.Native(C.source_bytes(), evidence, 300, clock=self.clock)
        refusal = dict(state='refused', boot_id=OLD, error='root differs')
        native.call = Mock(return_value=dict(returncode=1, timed_out=True, truncated=False,
                            stdout_b64=base64.b64encode(json.dumps(refusal).encode()).decode()))
        with patch.object(C.inspect, 'getsource', side_effect=AssertionError('must be captured')):
            self.assertEqual(native.observe(), refusal)
        native.call.return_value['stdout_b64'] = base64.b64encode(json.dumps(ready(OLD)).encode()).decode()
        self.assertEqual(native.observe()['state'], 'transport-unknown')

    def test_native_transport_timeout_clamped_and_partial_capture_preserved(self):
        evidence = C.Evidence(self.out, 'transport')
        native = C.Native(C.source_bytes(), evidence, 28800, clock=self.clock)
        native.phase_deadline = 17
        partial = dict(returncode=None, timed_out=True, truncated=False,
                       stdout_b64='cGFydGlhbA==', stderr_b64='ZXJyb3I=')
        native.transport.bounded_command = Mock(return_value=partial)
        self.assertEqual(native.call('test', b'pass', timeout=120), partial)
        self.assertEqual(native.transport.bounded_command.call_args.kwargs['timeout'], 17)
        retained = json.loads(next(evidence.path.glob('*-transport.json')).read_bytes())
        self.assertEqual(retained['raw'], partial)

    def test_native_uart_rotation_and_truncation(self):
        legacy = C.module(ROOT / 'scripts/collect-known-cold-boots.py', 'test_spi_cold')
        path = self.out / 'uart.log'
        path.write_bytes(b'old')
        uart = legacy.UART(path)
        try:
            path.write_bytes(b'')
            with self.assertRaises(ValueError):
                uart.read()
        finally:
            uart.close()
        path.write_bytes(b'old')
        uart = legacy.UART(path)
        try:
            path.rename(self.out / 'old.log')
            path.write_bytes(b'new')
            with self.assertRaises(ValueError):
                uart.read()
        finally:
            uart.close()


class NativeReadinessTests(unittest.TestCase):
    def setUp(self):
        expected = dict(LoadState='loaded', ActiveState='inactive', Result='success',
                        MainPID='0', Job='', ExecMainCode='1', ExecMainStatus='0')
        self.policy = Mock()
        self.M = NS(P=NS(installed_guard=Mock(), NORMAL=NS(manager_state=lambda: 'running'),
                         BOOT_POLICY=NS(Policy=lambda boot: self.policy)),
                    A=NS(_boot_id=lambda: OLD), D=NS(command=Mock(side_effect=self.command),
                                                    properties=Mock(return_value=expected)))
        self.Q = NS(load_installed=lambda: self.M)
        self.R = NS(storage_snapshot=Mock(return_value={'root': 'B'}), sd_absent=Mock())
        self.ntp = 'yes'
        self.log = 'All CPU(s) started at EL2\nVHE mode initialized successfully'
        self.patches = [patch.object(C, 'captured_modules', return_value=(self.Q, self.R)),
                        patch.object(C.os, 'uname', return_value=NS(release='6.6.98-a733-diag')),
                        patch.object(Path, 'read_bytes', return_value=b'0-7\n')]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def command(self, argv, **kwargs):
        return self.ntp if 'timedatectl' in argv[0] else self.log

    def test_ready_without_admission_api(self):
        result = C.native_context({})
        self.assertEqual(result['state'], 'ready')
        self.assertEqual(result['boot_id'], OLD)
        self.policy.close.assert_called_once()

    def test_pid1_starting_is_authenticated_busy(self):
        self.M.P.NORMAL.manager_state = lambda: 'starting'
        result = C.native_context({})
        self.assertEqual(result['state'], 'busy')
        self.assertEqual(result['boot_id'], OLD)
        self.R.storage_snapshot.assert_called_once()

    def test_ntp_and_not_loaded_or_running_units_busy(self):
        self.ntp = 'no'
        self.assertEqual(C.native_context({})['state'], 'busy')
        self.ntp = 'yes'
        for changes in ({'LoadState': 'not-found'}, {'ActiveState': 'active'}, {'Job': '77'}):
            self.M.D.properties.return_value.update(changes)
            self.assertEqual(C.native_context({})['state'], 'busy')

    def test_failed_unit_storage_sd_and_el2_fatal(self):
        self.M.D.properties.return_value['ActiveState'] = 'failed'
        with self.assertRaisesRegex(ValueError, 'unit failed'):
            C.native_context({})
        self.M.D.properties.return_value['ActiveState'] = 'inactive'
        for function in (self.R.storage_snapshot, self.R.sd_absent, self.M.P.installed_guard):
            function.side_effect = ValueError('authenticated mismatch')
            with self.assertRaisesRegex(ValueError, 'authenticated mismatch'):
                C.native_context({})
            function.side_effect = None
        for log in ('missing', self.log + 'HYP mode not available'):
            self.log = log
            with self.assertRaisesRegex(ValueError, 'EL2'):
                C.native_context({})

    def test_rpc_source_is_honest_pinned_and_syntax_valid(self):
        self.patches[0].stop()
        source = C.remote_source(C.source_bytes(), 'observe')
        compile(source, '<rpc>', 'exec')
        self.assertIn(b'<captured-', source)
        self.assertNotIn(b'.admit(', source)
        self.assertNotIn(b'R.execute(', source)
        self.assertNotIn(b'Q.qualify(', source)


class MtdTests(unittest.TestCase):
    def inventory(self, path):
        return NS(st_mode=0o020600, st_rdev=os.makedev(90, 0), st_dev=1, st_ino=2)

    def read_sys(self, path):
        return {'dev': b'90:0\n', 'size': b'16777216\n', 'erasesize': b'65536\n',
                'name': b'spi0.0\n'}[path.name]

    def test_component_hashes_calibration_and_short_read(self):
        info = self.inventory(None)
        with patch.object(C.os, 'open', return_value=9), patch.object(C.os, 'close'), \
             patch.object(C.os, 'fstat', return_value=info), \
             patch.object(Path, 'lstat', return_value=info), \
             patch.object(Path, 'read_bytes', autospec=True, side_effect=self.read_sys), \
             patch.object(C.os, 'pread', side_effect=lambda fd, amount, offset: b'x' * amount), \
             patch.object(C, 'BOOT0_SHA', C.digest(b'x' * 237568)), \
             patch.object(C, 'PACKAGE_SHA', C.digest(b'x' * (1654784 - 262144))):
            result = C.mtd_components()
            self.assertEqual(len(base64.b64decode(result['calibration']['data_base64'])), 2048)
            with patch.object(C.os, 'pread', return_value=b''), self.assertRaisesRegex(ValueError, 'short MTD'):
                C.mtd_components()
            with patch.object(C, 'BOOT0_SHA', '0' * 64), self.assertRaisesRegex(ValueError, 'hash differs'):
                C.mtd_components()
            info.st_rdev = os.makedev(90, 1)
            with self.assertRaisesRegex(ValueError, 'identity differs'):
                C.mtd_components()


if __name__ == '__main__':
    unittest.main()
