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
                el2=True, kernel='6.6.98-a733-diag')
    data.update(changes)
    return dict(returncode=0, timed_out=False, truncated=False,
                stdout_b64=base64.b64encode(json.dumps(data).encode()).decode())


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

    def test_no_preservation_never_probes(self):
        def refuse(): raise ValueError('not retained')
        with self.assertRaises(ValueError):
            w.collect(self.evidence, START, lambda *a, **k: self.fail('probe'), refuse)
        self.assertEqual(self.events()[-1]['completed'], 0)

    def test_invalid_admission_never_requests_reboot(self):
        for changes in ({'kvm_api': 11}, {'irqchip': 0}, {'el2': False}, {'ntp': 'no'}, {'cpus': '0-6'}, {'sd_stat': [0]*4+[1]+[0]*12}):
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
    def run_remote(self, *, fd_target='socket:[1]', unit_pid='0', model='SPCC M.2 PCIe SSD', inode=None, reboot=False, ntp='yes', log='All CPU(s) started at EL2\nkvm: VHE mode initialized successfully\n', api=12, irq=1, sd_field=None):
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
        if sd_field is not None:
            counters = [0]*17
            counters[sd_field] = 1
            files['/sys/class/block/mmcblk1/stat'] = ' '.join(map(str, counters))
        class FakePath(PurePosixPath):
            def lstat(self):
                import stat
                return types.SimpleNamespace(st_mode=(stat.S_IFREG|0o644 if self.suffix == '.py' else stat.S_IFDIR|0o755), st_uid=0, st_nlink=1)
            def exists(self): return False
            def iterdir(self):
                return iter([FakePath(p) for p in {
                    '/proc': ['/proc/1'], '/proc/1/task': ['/proc/1/task/1'],
                    '/proc/1/task/1/fd': ['/proc/1/task/1/fd/3']
                }.get(str(self), [])])
        def read(path, mode): return io.BytesIO(files[str(path)].encode())
        def nsstat(path):
            name = str(path).rsplit('/', 1)[-1]
            return types.SimpleNamespace(st_dev=1, st_ino=inode or {'pid':0xeffffffc, 'user':0xeffffffd, 'mnt':3}[name])
        descriptors = {}
        def opened(path, flags):
            descriptors[len(descriptors)+4] = str(path)
            return len(descriptors)+3
        def fstat(fd): return nsstat(descriptors[fd])
        def ioctl(fd, op, arg=None):
            if op == 0xae00: return api
            if op == 0xae03: return irq
            return {'pid':0x20000000, 'user':0x10000000, 'mnt':0x20000}[descriptors[fd].rsplit('/', 1)[-1]]
        def command(argv, timeout):
            if argv[0] == 'timedatectl': return ntp.encode()
            if argv[0] == 'dmesg': return log.encode()
            if argv[1] == 'list-units': return b'[{"unit":"a733-endurance-host-cpu-release-20261109.service"}]'
            return ('ActiveState=active\nSubState=exited\nMainPID='+unit_pid+'\n').encode()
        management = types.SimpleNamespace(runtime_guard=lambda: {}, current_address=lambda config: '192.168.1.116')
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
            exec(w.REMOTE, {'EXPECTED': START, 'REBOOT': reboot})
            return output.getvalue(), run.call_args_list

    def test_remote_idle_active_exited_is_allowed_and_clean_command(self):
        raw, calls = self.run_remote(reboot=True)
        self.assertTrue(json.loads(raw)['guards_passed'])
        self.assertEqual(calls[0].args[0], ['systemctl', 'reboot'])

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
