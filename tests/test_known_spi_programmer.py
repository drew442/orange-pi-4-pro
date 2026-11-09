"""Host-only programmer/capture integration; no MTD, SSH or board operations."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


def load(filename):
    spec = importlib.util.spec_from_file_location(filename.replace('-', '_'), ROOT / 'scripts' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


P = load('program-known-spi.py')
C = load('capture-known-spi-events.py')


class Nor:
    def __init__(self, original):
        self.data = bytearray(original)
        self.erases, self.writes = [], []
        self.fail_erase = False
        self.fail_write = False
        self.short = False

    def read(self, fd, count, offset):
        return bytes(self.data[offset:offset + count])

    def erase(self, fd, raw):
        offset, size = self.backend.ERASE_INFO.unpack(raw)
        self.erases.append(offset)
        self.data[offset:offset + size] = b'\xff' * size
        if self.fail_erase:
            raise OSError('uncertain erase')

    def write(self, fd, data, offset):
        self.writes.append(offset)
        count = min(1000, len(data)) if self.short else len(data)
        for index, value in enumerate(data[:count]):
            self.data[offset + index] &= value
        if self.fail_write:
            raise OSError('uncertain write')
        return count


class Integration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = b'\xa5' * P.SIZE
        candidate = bytearray(cls.original)
        candidate[10:20] = b'\x51' * 10
        candidate[262150:262160] = b'\x40' * 10
        cls.candidate = bytes(candidate)

    def setUp(self):
        self.engine = load('spi-unit-transaction.py')
        self.engine.ORIGINAL_SHA256 = P.sha(self.original)
        self.engine.CANDIDATE_SHA256 = P.sha(self.candidate)
        self.backend = load('spi-mtd-adapter.py')
        self.nor = Nor(self.original)
        self.nor.backend = self.backend
        self.backend.os = types.SimpleNamespace(pread=self.nor.read)
        self.backend._erase_ioctl = self.nor.erase
        self.backend._pwrite = self.nor.write
        self.patches = [patch.object(self.backend.MtdAdapter, '_descriptor', return_value=('fd-stamp', 2)),
                        patch.object(self.backend.MtdAdapter, '_info', return_value=b'geometry')]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.events, self.closed, self.opened = [], [], []
        self.guard = Mock()

    def plan(self, direction='install'):
        current = bytes(self.nor.data)
        return self.engine.build_plan(self.original, self.candidate, current,
                                      direction, P.sha(current))

    def run_plan(self, plan=None, emit=None, close=None):
        with contextlib.redirect_stderr(io.StringIO()):
            return P.transact(self.engine, self.backend, plan or self.plan(),
                              self.original, self.candidate, guard=self.guard,
                              identity=lambda fd: None, emit=emit or self.events.append,
                              open_rw=lambda: self.opened.append(9) or 9,
                              close=close or self.closed.append)

    def test_install_header_last_short_writes_preserve_all_other_bytes(self):
        self.nor.short = True
        outcome = self.run_plan()
        self.assertTrue(outcome['success'])
        self.assertFalse(outcome['hardware_acceptance'])
        self.assertEqual(self.nor.erases, [4 * 65536, 0])
        self.assertEqual(bytes(self.nor.data), self.candidate)
        self.assertEqual(bytes(self.nor.data[237568:262144]), self.original[237568:262144])
        self.assertEqual(bytes(self.nor.data[1654784:]), self.original[1654784:])
        self.assertEqual(self.closed, [9])
        self.assertEqual(self.events[0]['event'], 'capture_ready')
        self.assertEqual(self.events[-2]['event'], 'transaction_verified')
        self.assertEqual(self.events[-1]['event'], 'programmer_outcome')
        self.assertFalse(self.events[-1]['hardware_acceptance'])

    def test_uncertain_erase_stops_then_fresh_partial_restore(self):
        self.nor.fail_erase = True
        with self.assertRaises(ValueError):
            self.run_plan()
        self.assertEqual(len(self.nor.erases), 1)
        self.assertEqual(self.nor.writes, [])
        partial = self.events[-1]['receipt']
        self.assertTrue(partial['mutation_possible'])
        self.assertEqual(partial['attempted_units'], [4])
        self.assertEqual(partial['completed_units'], [])
        self.assertEqual(self.closed, [9])
        self.nor.fail_erase = False
        self.run_plan(self.plan('restore'))
        self.assertEqual(bytes(self.nor.data), self.original)
        self.assertEqual(self.closed, [9, 9])

    def test_uncertain_write_never_retries_and_can_fresh_restore(self):
        self.nor.fail_write = True
        with self.assertRaises(ValueError):
            self.run_plan()
        self.assertEqual(len(self.nor.writes), 1)
        self.assertEqual(len(self.nor.erases), 1)
        self.assertEqual(self.events[-1]['receipt']['write_confirmed_bytes'], 0)
        self.nor.fail_write = False
        self.run_plan(self.plan('restore'))
        self.assertEqual(bytes(self.nor.data), self.original)

    def test_close_error_prevents_success_and_close_is_not_retried(self):
        close = Mock(side_effect=OSError('close uncertain'))
        with self.assertRaises(ValueError):
            self.run_plan(close=close)
        close.assert_called_once_with(9)
        self.assertFalse(self.events[-1]['success'])
        self.assertFalse(self.events[-1]['close_success'])
        self.assertTrue(self.events[-1]['receipt']['final_verified'])

    def test_capture_readiness_failure_precedes_open(self):
        with self.assertRaises(OSError):
            self.run_plan(emit=Mock(side_effect=OSError('offboard failure')))
        self.assertEqual(self.opened, [])
        self.assertEqual(self.nor.erases, [])

    def test_erase_intent_ack_failure_prevents_erase(self):
        def emit(event):
            self.events.append(event)
            if event['event'] == 'erase_attempt':
                raise OSError('offboard disk failure')
        with self.assertRaises(ValueError):
            self.run_plan(emit=emit)
        self.assertEqual(self.nor.erases, [])
        self.assertEqual(self.closed, [9])
        self.assertFalse(self.events[-1]['receipt']['mutation_possible'])

    def test_guard_failure_precedes_open(self):
        self.guard.side_effect = ValueError('workload active')
        with self.assertRaises(ValueError):
            self.run_plan()
        self.assertEqual(self.opened, [])

    def test_changed_plan_and_current_refuse_review(self):
        plan = self.plan()
        pin = plan['plan_sha256']
        args = (self.engine, self.engine.canonical(plan), pin,
                self.original, self.candidate, bytes(self.nor.data), 'install')
        self.assertEqual(P.reviewed_plan(*args), plan)
        plan['execution_order'].reverse()
        with self.assertRaises(ValueError):
            P.reviewed_plan(self.engine, self.engine.canonical(plan), pin,
                            self.original, self.candidate, bytes(self.nor.data), 'install')
        self.nor.data[0] ^= 1
        with self.assertRaises(ValueError):
            P.reviewed_plan(*args[:5], bytes(self.nor.data), 'install')
        self.assertEqual(self.nor.erases, [])


class Safety(unittest.TestCase):
    def test_unregistered_gate_refuses_before_any_open(self):
        with patch.object(P.os, 'open', side_effect=AssertionError('must not open')):
            with self.assertRaisesRegex(ValueError, 'unregistered'):
                P.main(['write', '--expected-boot-id', 'dd8d6f67-d297-4f3b-bc9a-8a41c9742051',
                        '--original', '/run/private/original', '--candidate', '/run/private/candidate',
                        '--helpers', '/run/private/helpers'])
            with self.assertRaisesRegex(ValueError, 'unregistered'):
                P.recovery_gate(types.SimpleNamespace(data=b'{"recovery":true}'))

    def test_arbitrary_proof_boolean_cannot_bypass_registered_hash(self):
        proof = types.SimpleNamespace(data=b'{"recovery":true}', check=Mock())
        with patch.object(P, 'RECOVERY_PROOF_PIN', '0' * 64):
            with self.assertRaisesRegex(ValueError, 'pin mismatch'):
                P.recovery_gate(proof)

    def test_helper_source_mismatch_never_executes(self):
        with self.assertRaisesRegex(ValueError, 'pin mismatch'):
            P.module_from_bytes(b'raise RuntimeError("executed")', 'unsafe', '0' * 64)

    def test_fixed_source_pins_match_held_helper_bytes(self):
        for name, pin in P.HELPERS.items():
            data = (ROOT / 'scripts' / name).read_bytes()
            self.assertEqual(P.sha(data), pin)
            P.module_from_bytes(data, 'verified_' + name, pin)

    def test_alias_and_kvm_consumers_refuse(self):
        for mode, major, minor, link in ((stat.S_IFCHR, 90, 0, ''),
                                         (stat.S_IFCHR, 90, 1, ''),
                                         (stat.S_IFBLK, 31, 0, ''),
                                         (stat.S_IFREG, 0, 0, 'anon_inode:kvm-vm'),
                                         (stat.S_IFREG, 0, 0, 'anon_inode:kvm-vcpu'),
                                         (stat.S_IFCHR, 10, 232, '/dev/kvm')):
            with self.subTest(link=link, device=(major, minor)):
                with self.assertRaises(ValueError):
                    P.reject_consumer(types.SimpleNamespace(st_mode=mode,
                                                            st_rdev=os.makedev(major, minor)), link)

    def test_exact_target_refusal_before_identity_ioctl(self):
        backup = Mock()
        with patch.object(P.os, 'fstat', return_value=types.SimpleNamespace(st_mode=stat.S_IFCHR,
                                                                          st_rdev=os.makedev(90, 1))):
            with self.assertRaises(ValueError):
                P.target_identity(9, backup)
        backup.identity.assert_not_called()

    def test_board_pin_uses_nvme_but_not_sd(self):
        host = Mock()
        class FakePath:
            def __init__(self, value):
                self.value = str(value)
            def __truediv__(self, value):
                return FakePath(self.value + '/' + value)
            def read_bytes(self):
                return P.COMPATIBLE
            def read_text(self):
                return {'/sys/class/block/nvme0n1/device/serial': P.NVME[0],
                        '/sys/class/block/nvme0n1/device/model': P.NVME[1],
                        '/sys/class/block/nvme0n1/size': str(P.NVME[2] // 512)}[self.value]
        with patch.object(P, 'Path', FakePath):
            P.board_guard('boot', host)
        host.host_context.assert_called_once_with('boot')

    def test_directory_stamp_ignores_unrelated_mtime(self):
        first = types.SimpleNamespace(st_dev=1, st_ino=2, st_mode=0o40700, st_uid=0,
                                      st_gid=0, st_mtime_ns=1, st_ctime_ns=1)
        second = types.SimpleNamespace(**{**vars(first), 'st_mtime_ns': 500, 'st_ctime_ns': 500})
        self.assertEqual(P.stamp(first, False), P.stamp(second, False))


class HeldAndCensus(unittest.TestCase):
    def info(self, mode, size=0):
        return types.SimpleNamespace(st_dev=1, st_ino=mode + size, st_mode=mode,
                                     st_uid=0, st_gid=0, st_nlink=1, st_size=size,
                                     st_mtime_ns=1, st_ctime_ns=1)

    def held_environment(self, leaf_mode=stat.S_IFREG | 0o600):
        infos = {100: self.info(stat.S_IFDIR | 0o755),
                 101: self.info(stat.S_IFDIR | 0o755),
                 102: self.info(stat.S_IFDIR | 0o700),
                 103: self.info(leaf_mode, 4)}
        links = {(None, '/'): 100, (100, 'run'): 101,
                 (101, 'private'): 102, (102, 'source'): 103}
        def opener(name, flags, dir_fd=None):
            self.assertTrue(flags & os.O_NOFOLLOW)
            return links[dir_fd, name]
        backup = Mock()
        return infos, links, opener, backup

    def test_held_leaf_content_metadata_and_ancestor_ownership_guards(self):
        infos, links, opener, backup = self.held_environment()
        closed = []
        with patch.object(P.os, 'open', side_effect=opener), \
             patch.object(P.os, 'fstat', side_effect=lambda fd: infos[fd]), \
             patch.object(P.os, 'stat', side_effect=lambda name, dir_fd=None, **kw: infos[links[dir_fd, name]]), \
             patch.object(P.os, 'pread', return_value=b'data'), \
             patch.object(P.os, 'close', side_effect=closed.append):
            held = P.HeldFile('/run/private/source', backup, pin=P.sha(b'data'))
            self.assertEqual(held.data, b'data')
            infos[101].st_mtime_ns += 5
            held.check()  # Unrelated /run child activity is permitted.
            infos[103].st_mtime_ns += 1
            with self.assertRaisesRegex(ValueError, 'held input'):
                held.check()
            infos[103].st_mtime_ns -= 1
            infos[102].st_uid = 1
            with self.assertRaisesRegex(ValueError, 'ancestor'):
                held.check()
            held.close()
            held.close()
        self.assertEqual(closed, [103, 102, 101, 100])
        self.assertIn(unittest.mock.call(103), backup.ram_filesystem.call_args_list)

    def test_source_pin_or_nonregular_leaf_refuses_and_closes(self):
        for mode, pin in ((stat.S_IFREG | 0o600, '0' * 64), (stat.S_IFLNK | 0o600, P.sha(b'data'))):
            infos, links, opener, backup = self.held_environment(mode)
            closed = []
            with patch.object(P.os, 'open', side_effect=opener), \
                 patch.object(P.os, 'fstat', side_effect=lambda fd: infos[fd]), \
                 patch.object(P.os, 'stat', side_effect=lambda name, dir_fd=None, **kw: infos[links[dir_fd, name]]), \
                 patch.object(P.os, 'pread', return_value=b'data'), \
                 patch.object(P.os, 'close', side_effect=closed.append):
                with self.assertRaises(ValueError):
                    P.HeldFile('/run/private/source', backup, pin=pin)
            self.assertEqual(closed, [103, 102, 101, 100])

    def test_census_rejects_mount_fd_map_and_incomplete_visibility(self):
        tree = {'/proc': ['/proc/1'], '/proc/1/task': ['/proc/1/task/1'],
                '/proc/1/task/1/fd': ['/proc/1/task/1/fd/7']}
        class FakePath:
            def __init__(self, value):
                self.value = str(value)
                self.name = self.value.rsplit('/', 1)[-1]
            def __truediv__(self, value):
                return FakePath(self.value + '/' + value)
            def __str__(self):
                return self.value
            def iterdir(self):
                return iter(FakePath(v) for v in tree[self.value])
            def exists(self):
                return False
        mounted = '1 0 31:0 / /mnt rw - jffs2 /dev/mtdblock0 rw'
        ordinary = '1 0 8:1 / / rw - ext4 /dev/root rw'
        mapped = '1000-2000 rw-s 00000000 00:01 1 /dev/mtdblock0'
        for kind in ('mount', 'fd', 'map', 'unscannable'):
            host = Mock()
            host.read_text.side_effect = lambda path: (mounted if kind == 'mount' else ordinary) \
                if str(path).endswith('/mountinfo') else mapped if kind == 'map' else ''
            host.ordinary_exit.return_value = False
            node = types.SimpleNamespace(st_mode=stat.S_IFCHR,
                                         st_rdev=os.makedev(90, 1) if kind == 'fd' else os.makedev(1, 3))
            def stat_node(path):
                if kind == 'unscannable':
                    raise PermissionError('hidden live task')
                if '/map_files/' in str(path):
                    return types.SimpleNamespace(st_mode=stat.S_IFBLK, st_rdev=os.makedev(31, 0))
                return node
            with self.subTest(kind=kind), patch.object(P, 'Path', FakePath), \
                 patch.object(P.os, 'stat', side_effect=stat_node), \
                 patch.object(P.os, 'readlink', return_value='/dev/null'):
                with self.assertRaises((ValueError, PermissionError)):
                    P.quiescence(host)


class Bootstrap(unittest.TestCase):
    def setUp(self):
        self.namespace = {'__name__': 'offline_bootstrap_test'}
        exec(C.BOOTSTRAP, self.namespace)
        self.data = b'import sys\nRESULT = (__name__, __file__, list(sys.argv))\n'
        self.infos = {}
        for fd, mode in ((100, stat.S_IFDIR | 0o755), (101, stat.S_IFDIR | 0o755),
                         (102, stat.S_IFDIR | 0o700), (103, stat.S_IFREG | 0o400)):
            self.infos[fd] = types.SimpleNamespace(st_dev=1, st_ino=fd, st_mode=mode, st_uid=0,
                                                  st_gid=0, st_nlink=1, st_size=0,
                                                  st_mtime_ns=1, st_ctime_ns=1)
        self.links = {(None, '/'): 100, (100, 'run'): 101,
                      (101, 'private'): 102, (102, 'programmer.py'): 103,
                      (102, 'arbitrary.py'): 103}
        self.closed, self.opened = [], []
        def opener(name, flags, dir_fd=None):
            self.assertTrue(flags & os.O_NOFOLLOW)
            self.opened.append(name)
            return self.links[dir_fd, name]
        fake_os = types.SimpleNamespace(path=os.path, O_RDONLY=os.O_RDONLY,
                                        O_NOFOLLOW=os.O_NOFOLLOW, O_CLOEXEC=os.O_CLOEXEC,
                                        O_DIRECTORY=os.O_DIRECTORY, geteuid=lambda: 0,
                                        open=opener, fstat=lambda fd: self.infos[fd],
                                        stat=lambda name, dir_fd=None, **kw: self.infos[self.links[dir_fd, name]],
                                        pread=lambda fd, count, offset: self.data[offset:offset + count],
                                        close=self.closed.append)
        self.namespace['os'] = fake_os
        self.namespace['no_swap'] = Mock()
        self.namespace['ram_filesystem'] = Mock()

    def run_source(self, *, path='/run/private/programmer.py', pin=None, size=None):
        self.infos[103].st_size = len(self.data)
        return self.namespace['run_verified'](path, pin or P.sha(self.data),
                                               size or len(self.data), ['write', '--direction', 'install'])

    def test_bootstrap_compiles_held_bytes_preserving_entrypoint(self):
        result = self.run_source()
        self.assertEqual(result['RESULT'], ('__main__', '/run/private/programmer.py',
                                           ['/run/private/programmer.py', 'write', '--direction', 'install']))
        self.assertEqual(result['_AUTHENTICATED_BOOTSTRAP']['pin'], P.sha(self.data))
        self.assertEqual(self.closed, [103, 102, 101, 100])

    def test_arbitrary_script_never_executes(self):
        self.data = b'raise AssertionError("arbitrary script executed")\n'
        with self.assertRaisesRegex(ValueError, 'source pin mismatch'):
            self.run_source(path='/run/private/arbitrary.py', pin=C.PROGRAMMER_PIN)
        self.assertEqual(self.closed, [103, 102, 101, 100])

    def test_changed_source_path_and_metadata_fail_before_exec(self):
        for changed in ('path', 'content', 'ancestor'):
            self.setUp()
            def read(fd, count, offset):
                if changed == 'path':
                    replacement = types.SimpleNamespace(**vars(self.infos[103]))
                    replacement.st_ino += 1
                    self.infos[104] = replacement
                    self.links[102, 'programmer.py'] = 104
                elif changed == 'content':
                    self.infos[103].st_mtime_ns += 1
                else:
                    self.infos[102].st_uid = 1
                return self.data
            self.namespace['os'].pread = read
            with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, 'changed'):
                self.run_source()
            self.assertEqual(self.closed, [103, 102, 101, 100])

    def test_direct_write_without_bootstrap_refuses_before_open(self):
        with patch.object(P, 'RECOVERY_PROOF_PIN', 'a' * 64), \
             patch.dict(P.__dict__, {'_AUTHENTICATED_BOOTSTRAP': None}), \
             patch.object(P.os, 'open', side_effect=AssertionError('target opened')):
            with self.assertRaisesRegex(ValueError, 'authenticated capture bootstrap'):
                P.main(['write', '--expected-boot-id', 'dd8d6f67-d297-4f3b-bc9a-8a41c9742051',
                        '--original', '/run/private/original', '--candidate', '/run/private/candidate',
                        '--helpers', '/run/private/helpers', '--plan', '/run/private/plan',
                        '--plan-sha256', 'a' * 64, '--recovery-proof', '/run/private/proof'])

    def test_bound_own_source_identity_is_held_and_guarded(self):
        self.infos[103].st_size = len(self.data)
        binding = dict(pin=P.sha(self.data), path='/run/private/programmer.py', fd=103,
                       check=Mock())
        source = types.SimpleNamespace(initial=P.stamp(self.infos[103]), close=Mock())
        with patch.dict(P.__dict__, {'_AUTHENTICATED_BOOTSTRAP': binding}), \
             patch.object(P, '__file__', binding['path']), \
             patch.object(P, 'HeldFile', return_value=source) as held, \
             patch.object(P.os, 'fstat', side_effect=lambda fd: self.infos[fd]):
            self.assertIs(P.authenticated_binding(True), binding)
            self.assertIs(P.hold_programmer(Mock(), binding), source)
            held.assert_called_once_with(binding['path'], unittest.mock.ANY,
                                          pin=binding['pin'], limit=1048576, source_mode=0o400)
            self.infos[103].st_mtime_ns += 1
            with self.assertRaisesRegex(ValueError, 'held source identity changed'):
                P.hold_programmer(Mock(), binding)
            source.close.assert_called_once()

    def test_bootstrap_bound_model_programmer_transaction(self):
        # A test-only pinned source bridges verified execution to the real caller
        # and reviewed engine/backend with injected NOR syscalls. No device opens.
        Integration.setUpClass()
        fixture = Integration('test_guard_failure_precedes_open')
        fixture.setUp()
        module = types.ModuleType('known_spi_bootstrap_model')
        def run(binding, path):
            with patch.dict(P.__dict__, {'_AUTHENTICATED_BOOTSTRAP': binding}), \
                 patch.object(P, '__file__', path):
                checked = P.authenticated_binding(True)
                self.assertEqual(checked['pin'], P.sha(self.data))
                fixture.guard = checked['check']
                return fixture.run_plan()
        module.run = run
        self.data = (b'import known_spi_bootstrap_model as model\n'
                     b'RESULT = model.run(_AUTHENTICATED_BOOTSTRAP, __file__)\n')
        try:
            with patch.dict(sys.modules, {'known_spi_bootstrap_model': module}):
                result = self.run_source()
            self.assertTrue(result['RESULT']['success'])
            self.assertFalse(result['RESULT']['hardware_acceptance'])
            self.assertEqual(bytes(fixture.nor.data), fixture.candidate)
        finally:
            fixture.doCleanups()

    def test_capture_pins_actual_programmer_and_uses_fixed_bootstrap(self):
        data = (ROOT / 'scripts/program-known-spi.py').read_bytes()
        self.assertEqual(C.PROGRAMMER_PIN, P.sha(data))
        self.assertEqual(C.PROGRAMMER_SIZE, len(data))


class Protocol(unittest.TestCase):
    def envelope(self, event, sequence=1, nonce='a' * 32):
        return C.canonical(dict(schema=1, nonce=nonce, sequence=sequence,
                                event_sha256=C.sha(C.canonical(event)), event=event)) + b'\n'

    def ready(self):
        return dict(event='capture_ready', hardware_acceptance=False)

    def outcome(self, success=False):
        return dict(event='programmer_outcome', hardware_acceptance=False, success=success,
                    close_success=True, receipt=dict(hardware_acceptance=False,
                                                     success=success, final_verified=success))

    def test_ack_happens_only_after_durable_append(self):
        order = []
        class Sink(io.BytesIO):
            def write(self, raw):
                order.append('ack')
                return super().write(raw)
            def flush(self):
                order.append('flush')
        lines = iter([self.envelope(self.ready()), self.envelope(self.outcome(), 2)])
        result = C.capture(lambda *args: next(lines), Sink(),
                           types.SimpleNamespace(append=lambda raw: order.append('durable')))
        self.assertFalse(result['success'])
        self.assertEqual(order, ['durable', 'ack', 'flush'] * 2)

    def test_fsync_failure_produces_no_ack(self):
        ack = io.BytesIO()
        with self.assertRaises(OSError):
            C.capture(lambda *args: self.envelope(self.ready()), ack,
                      types.SimpleNamespace(append=Mock(side_effect=OSError('fsync failed'))))
        self.assertEqual(ack.getvalue(), b'')

    def test_success_requires_engine_verified_and_close(self):
        for verified, closed in ((False, True), (True, False)):
            events = [self.ready()]
            if verified:
                events.append(dict(event='transaction_verified', hardware_acceptance=False))
            outcome = self.outcome(True)
            outcome['close_success'] = closed
            events.append(outcome)
            lines = iter(self.envelope(event, i + 1) for i, event in enumerate(events))
            log = Mock()
            with self.assertRaises(ValueError):
                C.capture(lambda *args: next(lines), io.BytesIO(), log)
            self.assertEqual(log.append.call_count, len(events) - 1)

    def test_valid_final_success_is_captured(self):
        events = [self.ready(), dict(event='transaction_verified', hardware_acceptance=False),
                  self.outcome(True)]
        lines = iter(self.envelope(event, i + 1) for i, event in enumerate(events))
        self.assertTrue(C.capture(lambda *args: next(lines), io.BytesIO(), Mock())['success'])

    def test_corrupt_sequence_digest_and_truncation_refuse_no_ack(self):
        good = self.envelope(self.ready())
        variants = [good[:-1], good.replace(b'"sequence":1', b'"sequence":2'),
                    good.replace(b'capture_ready', b'capture_XXXXX'), b'{}\n', b'x' * (C.MAX_LINE + 1)]
        for raw in variants:
            ack = io.BytesIO()
            with self.subTest(raw=raw[:50]):
                with self.assertRaises((ValueError, KeyError)):
                    C.capture(lambda *args: raw, ack, Mock())
                self.assertEqual(ack.getvalue(), b'')

    def test_total_and_record_limits(self):
        with patch.object(C, 'MAX_TOTAL', 4):
            with self.assertRaisesRegex(ValueError, 'budget'):
                C.capture(lambda *args: self.envelope(self.ready()), io.BytesIO(), Mock())
        with patch.object(C, 'MAX_RECORDS', 1):
            with self.assertRaisesRegex(ValueError, 'record bound'):
                C.capture(lambda *args: self.envelope(self.ready()), io.BytesIO(), Mock())

    def test_programmer_exact_ack_and_failure(self):
        out = io.BytesIO()
        def ack(*args):
            value = json.loads(out.getvalue().splitlines()[-1])
            return P.canonical({key: value[key] for key in ('nonce', 'sequence', 'event_sha256')}) + b'\n'
        emit = P.AcknowledgedEvents(out, ack, nonce='a' * 32)
        emit(self.ready())
        emit(self.outcome())
        self.assertEqual(emit.sequence, 2)
        for reply in (b'', b'{}\n'):
            with self.assertRaises(ValueError):
                P.AcknowledgedEvents(io.BytesIO(), lambda *args: reply)(self.ready())
        with self.assertRaises(EOFError):
            P.AcknowledgedEvents(io.BytesIO(), Mock(side_effect=EOFError))(self.ready())

    def test_native_fsync_file_and_directory_before_return(self):
        log = object.__new__(C.NativeLog)
        log.fd, log.directory, log.closed, log.links = 10, 11, False, []
        log.initial = (1, 2, stat.S_IFREG | 0o600, 0, 0, 1)
        log.file_stamp = Mock(return_value=log.initial)
        calls = []
        with patch.object(C.os, 'stat', return_value=types.SimpleNamespace(st_dev=1, st_ino=2)), \
             patch.object(C.os, 'fstatvfs', return_value=types.SimpleNamespace(f_bavail=C.FREE_FLOOR + 100, f_frsize=1)), \
             patch.object(C.os, 'write', side_effect=lambda fd, raw: calls.append(('write', fd)) or len(raw)), \
             patch.object(C.os, 'fsync', side_effect=lambda fd: calls.append(('sync', fd))):
            log.append(b'event\n')
        self.assertEqual(calls, [('write', 10), ('sync', 10), ('sync', 11)])

    def test_bounded_raw_line_eof_truncation_limit_timeout(self):
        for data, limit in ((b'partial', 20), (b'oversized\n', 4), (b'', 20)):
            read_fd, write_fd = os.pipe()
            try:
                os.write(write_fd, data)
                os.close(write_fd)
                write_fd = None
                with os.fdopen(read_fd, 'rb', buffering=0) as stream:
                    read_fd = None
                    with self.assertRaises(ValueError):
                        P.BoundedLines(stream)(limit, 0.1)
            finally:
                if read_fd is not None:
                    os.close(read_fd)
                if write_fd is not None:
                    os.close(write_fd)
        read_fd, write_fd = os.pipe()
        try:
            with os.fdopen(read_fd, 'rb', buffering=0) as stream:
                with self.assertRaisesRegex(ValueError, 'timeout'):
                    C.BoundedLines(stream)(20, 0.01)
        finally:
            os.close(write_fd)

    def test_capture_final_eof_refuses_trailing_bytes(self):
        read_fd, write_fd = os.pipe()
        try:
            os.write(write_fd, b'first\ntrailing')
            os.close(write_fd)
            write_fd = None
            with os.fdopen(read_fd, 'rb', buffering=0) as stream:
                reader = C.BoundedLines(stream)
                self.assertEqual(reader(100, 0.1), b'first\n')
                with self.assertRaisesRegex(ValueError, 'trailing'):
                    reader.require_eof(0.1)
        finally:
            if write_fd is not None:
                os.close(write_fd)

    def test_parent_held_sealed_key_survives_child_close_fds(self):
        # Offline subprocess only: OpenSSH may close all inherited extra FDs.
        # The child reads the controller's descriptor through the parent path.
        fd = os.memfd_create('spi-test-key', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
        try:
            raw = b'public known host key snapshot\n'
            os.write(fd, raw)
            C.fcntl.fcntl(fd, C.fcntl.F_ADD_SEALS, C.fcntl.F_SEAL_WRITE |
                          C.fcntl.F_SEAL_GROW | C.fcntl.F_SEAL_SHRINK | C.fcntl.F_SEAL_SEAL)
            path = f'/proc/{os.getpid()}/fd/{fd}'
            result = C.subprocess.run([C.sys.executable, '-I', '-B', '-c',
                                       'import sys; sys.stdout.buffer.write(open(sys.argv[1], "rb").read())',
                                       path], stdout=C.subprocess.PIPE, stderr=C.subprocess.PIPE,
                                      close_fds=True, timeout=5, check=True)
            self.assertEqual(result.stdout, raw)
            with self.assertRaises(OSError):
                os.write(fd, b'changed')
        finally:
            os.close(fd)

    def test_constructed_ssh_has_no_unchecked_command(self):
        args = types.SimpleNamespace(programmer='/run/private/program-known-spi.py', direction='restore',
                                     expected_boot_id='dd8d6f67-d297-4f3b-bc9a-8a41c9742051',
                                     helpers='/run/private/helpers', original='/run/private/original',
                                     candidate='/run/private/candidate', plan='/run/private/plan',
                                     plan_sha256='a' * 64, recovery_proof='/run/private/proof')
        argv = C.ssh_argv(args, 42)
        self.assertIn('StrictHostKeyChecking=yes', argv)
        self.assertIn(f'UserKnownHostsFile=/proc/{os.getpid()}/fd/42', argv)
        self.assertIn('root@192.168.1.116', argv)
        remote = C.shlex.split(argv[-1])
        self.assertEqual(remote[:4], ['python3', '-I', '-B', '-c'])
        self.assertIn(C.BOOTSTRAP, remote[4])
        self.assertIn(repr(C.PROGRAMMER_PIN), remote[4])
        self.assertIn(repr(C.PROGRAMMER_SIZE), remote[4])
        self.assertEqual(remote[5], args.programmer)
        args.programmer = '/run/private/x;touch /tmp/bad'
        with self.assertRaises(ValueError):
            C.ssh_argv(args, 42)


if __name__ == '__main__':
    unittest.main()
