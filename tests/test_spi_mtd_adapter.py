"""In-process Linux syscall/NOR models only; never access an MTD device."""
import ctypes
import errno
import importlib.util
import os
from pathlib import Path
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mtd = load('spi_mtd_adapter', 'spi-mtd-adapter.py')
engine = load('spi_adapter_engine', 'spi-unit-transaction.py')
FD = 177


class Syscalls:
    def __init__(self):
        self.info = SimpleNamespace(st_dev=5, st_ino=10, st_rdev=os.makedev(90, 0),
                                    st_mode=stat.S_IFCHR | 0o600, st_uid=0,
                                    st_gid=0, st_nlink=1, st_ctime_ns=100)
        self.flags = os.O_RDWR
        self.geometry = [3, 0xc00, mtd.SIZE, mtd.ERASE, 1, 0, 0]
        self.calls = []
        self.image = None
        self.limit = mtd.CHUNK
        self.interrupt = False

    def fstat(self, fd):
        assert fd == FD
        self.calls.append(('stat',))
        return self.info

    def fcntl(self, fd, command):
        assert fd == FD and command == mtd.fcntl.F_GETFL
        self.calls.append(('flags',))
        return self.flags

    def ioctl(self, fd, command, data, mutate):
        assert fd == FD and command == 0x80204d01 and mutate is True
        assert type(data) is bytearray and len(data) == 32
        self.calls.append(('info',))
        data[:] = mtd.MTD_INFO.pack(*self.geometry)
        return 0

    def pread(self, fd, size, offset):
        assert fd == FD
        self.calls.append(('read', offset, size))
        return bytes(self.image[offset:offset + size]) if self.image is not None else b'\xff' * size

    def erase(self, fd, payload):
        assert fd == FD and type(payload) is bytes and len(payload) == 16
        offset, size = mtd.ERASE_INFO.unpack(payload)
        self.calls.append(('erase', offset, size))
        if self.image is not None:
            self.image[offset:offset + size] = b'\xff' * size

    def write(self, fd, data, offset):
        assert fd == FD
        self.calls.append(('write', offset, len(data)))
        count = min(self.limit, len(data))
        if self.image is not None:
            assert all(old & new == new for old, new in
                       zip(self.image[offset:offset + count], data[:count]))
            self.image[offset:offset + count] = data[:count]
        if self.interrupt:
            raise KeyboardInterrupt('after possible programming')
        return count


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.sys = Syscalls()
        for target, replacement in (
                ('os.fstat', self.sys.fstat), ('fcntl.fcntl', self.sys.fcntl),
                ('fcntl.ioctl', self.sys.ioctl), ('os.pread', self.sys.pread),
                ('_erase_ioctl', self.sys.erase), ('_pwrite', self.sys.write)):
            parts = target.split('.')
            owner = getattr(mtd, parts[0]) if len(parts) == 2 else mtd
            patcher = patch.object(owner, parts[-1], side_effect=replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.guard = Mock()
        self.identity = Mock()
        self.emit = Mock(return_value=None)
        self.adapter = self.bind()
        self.sys.calls.clear()

    def bind(self, **kwargs):
        return mtd.MtdAdapter(FD, guard=kwargs.get('guard', self.guard),
                              identity_check=kwargs.get('identity_check', self.identity),
                              emit=kwargs.get('emit', self.emit))

    def mutations(self):
        return [call for call in self.sys.calls if call[0] in ('erase', 'write')]

    def test_import_has_no_device_or_libc_activity(self):
        with patch.object(os, 'open', side_effect=AssertionError('open')), \
                patch.object(ctypes, 'CDLL', side_effect=AssertionError('libc')), \
                patch.object(mtd.fcntl, 'ioctl', side_effect=AssertionError('ioctl')):
            load('spi_import_only', 'spi-mtd-adapter.py')

    def test_explicit_callbacks_and_fd_required(self):
        for fd in (True, False, -1, '177', 1 << 32):
            with self.subTest(fd=fd), self.assertRaises(ValueError):
                mtd.MtdAdapter(fd, guard=self.guard, identity_check=self.identity,
                               emit=self.emit)
        for key in ('guard', 'identity_check', 'emit'):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.bind(**{key: None})

    def test_fixed_abi_and_exact_boundaries(self):
        self.assertEqual(mtd.MTD_INFO.size, 32)
        self.assertEqual(mtd.ERASE_INFO.pack(25 * 65536, 65536),
                         (25 * 65536).to_bytes(8, 'little') + (65536).to_bytes(8, 'little'))
        self.assertEqual(self.adapter.read(mtd.SIZE - 1, 1), b'\xff')
        self.adapter.erase(25 * mtd.ERASE, mtd.ERASE)
        self.assertEqual(self.adapter.write(mtd.MUTABLE_END - 1, b'a'), 1)
        self.assertEqual(self.mutations(), [('erase', 25 * mtd.ERASE, mtd.ERASE),
                                            ('write', mtd.MUTABLE_END - 1, 1)])

    def test_fresh_info_directly_precedes_each_mutation(self):
        for operation in (lambda: self.adapter.erase(0, mtd.ERASE),
                          lambda: self.adapter.write(0, b'x')):
            self.sys.calls.clear()
            operation()
            self.assertEqual([call[0] for call in self.sys.calls][-4:],
                             ['info', 'stat', 'flags', self.sys.calls[-1][0]])
            self.assertEqual(sum(call[0] == 'info' for call in self.sys.calls), 1)

    def test_identity_flags_permissions_and_geometry_fail_before_mutation(self):
        changes = [lambda: setattr(self.sys.info, 'st_mode', stat.S_IFREG | 0o600),
                   lambda: setattr(self.sys.info, 'st_rdev', os.makedev(91, 0)),
                   lambda: setattr(self.sys.info, 'st_rdev', os.makedev(90, 1)),
                   lambda: setattr(self.sys.info, 'st_ino', 11),
                   lambda: setattr(self.sys.info, 'st_mode', stat.S_IFCHR | 0o666),
                   lambda: setattr(self.sys.info, 'st_ctime_ns', 101),
                   lambda: setattr(self.sys, 'flags', os.O_RDONLY),
                   lambda: setattr(self.sys, 'flags', os.O_WRONLY),
                   lambda: setattr(self.sys, 'flags', os.O_RDWR | os.O_APPEND),
                   lambda: setattr(self.sys, 'flags', os.O_PATH),
                   lambda: setattr(self.sys, 'flags', os.O_RDWR | os.O_NONBLOCK)]
        for change in changes:
            with self.subTest(change=change):
                self.sys.__init__()
                adapter = self.bind()
                change()
                with self.assertRaises(ValueError):
                    adapter.erase(0, mtd.ERASE)
                self.assertEqual(self.mutations(), [])
        for index, value in enumerate((4, 0x800, mtd.SIZE - 1, 4096, 2, 1, 1)):
            with self.subTest(field=index):
                self.sys.__init__()
                adapter = self.bind()
                self.sys.geometry[index] = value
                with self.assertRaises(ValueError):
                    adapter.write(0, b'x')
                self.assertEqual(self.mutations(), [])
        self.sys.__init__()
        adapter = self.bind()
        self.sys.geometry[1] |= 0x1000  # Even valid-looking flags must stay bound.
        with self.assertRaisesRegex(ValueError, 'geometry changed'):
            adapter.erase(0, mtd.ERASE)
        self.assertEqual(self.mutations(), [])

    def test_initial_bad_geometry_or_device_is_rejected(self):
        for index, value in enumerate((4, 0x800, mtd.SIZE - 1, 4096, 2, 1, 1)):
            self.sys.__init__()
            self.sys.geometry[index] = value
            with self.subTest(field=index), self.assertRaises(ValueError):
                self.bind()
        for flags in (os.O_RDONLY, os.O_WRONLY, os.O_RDWR | os.O_APPEND, os.O_PATH):
            self.sys.__init__()
            self.sys.flags = flags
            with self.subTest(flags=flags), self.assertRaises(ValueError):
                self.bind()

    def test_guards_every_read_erase_write_and_after_callbacks(self):
        self.guard.reset_mock()
        self.identity.reset_mock()
        self.adapter.read(0, 1)
        self.adapter.erase(0, mtd.ERASE)
        self.adapter.write(0, b'a')
        self.assertEqual(self.guard.call_count, 3)
        self.assertEqual(self.identity.call_args_list, [((FD,),)] * 3)
        self.identity.side_effect = lambda fd: setattr(self.sys.info, 'st_ino', 99)
        with self.assertRaises(ValueError):
            self.adapter.erase(0, mtd.ERASE)
        self.assertEqual(len(self.mutations()), 2)

    def test_external_guard_failure_never_mutates_and_invalidates(self):
        for callback in (self.guard, self.identity):
            callback.side_effect = RuntimeError('guard refused')
            adapter = self.adapter
            with self.assertRaises(RuntimeError):
                adapter.write(0, b'a')
            callback.side_effect = None
            with self.assertRaisesRegex(ValueError, 'invalidated'):
                adapter.write(0, b'a')
            self.assertEqual(self.mutations(), [])
            self.adapter = self.bind()

    def test_validation_rejects_ranges_types_alignment_and_crossings(self):
        reads = [(True, 1), (0, True), (-1, 1), (0, 0), (mtd.SIZE, 1),
                 (mtd.SIZE - 1, 2), (0.0, 1), (0, '1')]
        erases = [(True, mtd.ERASE), (0, True), (1, mtd.ERASE),
                  (0, mtd.ERASE - 1), (26 * mtd.ERASE, mtd.ERASE),
                  (25 * mtd.ERASE, 2 * mtd.ERASE), (-mtd.ERASE, mtd.ERASE)]
        writes = [(True, b'a'), (-1, b'a'), (0, b''), (0, bytearray(b'a')),
                  (0, memoryview(b'a')), (0, b'a' * 4097), (4095, b'ab'),
                  (mtd.MUTABLE_END, b'a'), (1.0, b'a')]
        for name, cases in (('read', reads), ('erase', erases), ('write', writes)):
            for args in cases:
                adapter = self.bind()
                self.sys.calls.clear()
                with self.subTest(name=name, args=str(args)[:40]), self.assertRaises(ValueError):
                    getattr(adapter, name)(*args)
                self.assertEqual(self.sys.calls, [])

    def test_short_positive_write_is_returned_once(self):
        self.sys.limit = 2
        self.assertEqual(self.adapter.write(4092, b'abcd'), 2)
        self.assertEqual(self.mutations(), [('write', 4092, 4)])

    def test_bad_read_and_write_results_stop_without_retry(self):
        for value in (b'', b'xx', bytearray(b'x'), None):
            with patch.object(mtd.os, 'pread', return_value=value) as call:
                adapter = self.bind()
                with self.subTest(read=value), self.assertRaises(ValueError):
                    adapter.read(0, 1)
                call.assert_called_once_with(FD, 1, 0)
        for value in (0, -1, True, None, 2, 1.0):
            adapter = self.bind()
            with patch.object(mtd, '_pwrite', return_value=value) as call:
                with self.subTest(write=value), self.assertRaises(ValueError):
                    adapter.write(0, b'a')
                call.assert_called_once_with(FD, b'a', 0)
                with self.assertRaisesRegex(ValueError, 'invalidated'):
                    adapter.write(0, b'a')
                self.assertEqual(call.call_count, 1)

    def test_syscall_exceptions_propagate_once_and_invalidate(self):
        cases = [(mtd, '_erase_ioctl', 'erase', (0, mtd.ERASE)),
                 (mtd, '_pwrite', 'write', (0, b'a')),
                 (mtd.os, 'pread', 'read', (0, 1)),
                 (mtd.os, 'fstat', 'erase', (0, mtd.ERASE)),
                 (mtd.fcntl, 'fcntl', 'write', (0, b'a')),
                 (mtd.fcntl, 'ioctl', 'erase', (0, mtd.ERASE))]
        for owner, target, method, args in cases:
            adapter = self.bind()
            error = InterruptedError(errno.EINTR, 'uncertain syscall')
            with patch.object(owner, target, side_effect=error) as call:
                with self.subTest(target=target), self.assertRaises(InterruptedError) as caught:
                    getattr(adapter, method)(*args)
                self.assertIs(caught.exception, error)
                self.assertEqual(call.call_count, 1)
            with self.assertRaisesRegex(ValueError, 'invalidated'):
                adapter.guard()

    def test_emit_is_forwarded_without_return_value_requirement(self):
        event = dict(event='test', hardware_acceptance=False)
        self.assertIsNone(self.adapter.emit(event))
        self.emit.assert_called_once_with(event)
        self.emit.side_effect = OSError('off-board unavailable')
        with self.assertRaises(OSError):
            self.adapter.emit(event)
        with self.assertRaisesRegex(ValueError, 'invalidated'):
            self.adapter.guard()
        self.assertEqual(self.mutations(), [])

    def test_invalidation_never_closes_descriptor(self):
        with patch.object(mtd.os, 'close', side_effect=AssertionError('close')):
            self.adapter.invalidate()
            for method, args in (('guard', ()), ('read', (0, 1)),
                                 ('erase', (0, mtd.ERASE)), ('write', (0, b'a'))):
                with self.assertRaisesRegex(ValueError, 'invalidated'):
                    getattr(self.adapter, method)(*args)
        self.assertEqual(self.sys.calls, [])

    def test_engine_integration_short_writes_preserves_all_bytes(self):
        original = b'\xa5' * mtd.SIZE
        changed = bytearray(original)
        changed[4 * mtd.ERASE + 17] = 0x30
        changed[237567] = 0x30  # Partial-unit preservation boundary.
        candidate = bytes(changed)
        self.sys.image = bytearray(original)
        self.sys.limit = 997
        with patch.multiple(engine, ORIGINAL_SHA256=engine.sha(original),
                            CANDIDATE_SHA256=engine.sha(candidate)):
            plan = engine.build_plan(original, candidate, original, 'install', engine.sha(original))
            result = engine.execute(plan, original, candidate,
                                    read=self.adapter.read, erase=self.adapter.erase,
                                    write=self.adapter.write, guard=self.adapter.guard,
                                    emit=self.adapter.emit)
        self.assertEqual(bytes(self.sys.image), candidate)
        self.assertTrue(result['final_verified'])
        self.assertFalse(result['hardware_acceptance'])
        self.assertEqual(result['completed_units'], [4, 3])
        self.assertGreater(sum(c[0] == 'write' for c in self.sys.calls), 32)
        self.assertTrue(all(call.args[0]['hardware_acceptance'] is False
                            for call in self.emit.call_args_list))

    def test_engine_interruption_has_conservative_partial_receipt(self):
        original = b'\xa5' * mtd.SIZE
        candidate = original[:4 * mtd.ERASE] + b'\x30' + original[4 * mtd.ERASE + 1:]
        self.sys.image = bytearray(original)
        self.sys.interrupt = True
        with patch.multiple(engine, ORIGINAL_SHA256=engine.sha(original),
                            CANDIDATE_SHA256=engine.sha(candidate)):
            plan = engine.build_plan(original, candidate, original, 'install', engine.sha(original))
            with self.assertRaises(engine.TransactionError) as caught:
                engine.execute(plan, original, candidate,
                               read=self.adapter.read, erase=self.adapter.erase,
                               write=self.adapter.write, guard=self.adapter.guard,
                               emit=self.adapter.emit)
        receipt = caught.exception.receipt
        self.assertEqual(receipt['stage'], 'write')
        self.assertEqual(receipt['attempted_units'], [4])
        self.assertEqual(receipt['completed_units'], [])
        self.assertEqual(receipt['write_confirmed_bytes'], 0)
        self.assertEqual(receipt['write_attempted_bytes'], 4096)
        self.assertTrue(receipt['mutation_possible'])
        self.assertFalse(receipt['hardware_acceptance'])
        self.assertEqual(len(self.mutations()), 2)


class LibcTests(unittest.TestCase):
    def test_mutation_abi_and_no_eintr_restart(self):
        payload = mtd.ERASE_INFO.pack(65536, 65536)
        for name in ('_erase_ioctl', '_pwrite'):
            calls = []

            def invoke(*args):
                calls.append(args)
                ctypes.set_errno(errno.EINTR)
                return -1

            function = Mock(side_effect=invoke)
            libc = SimpleNamespace(ioctl=function, pwrite=function)
            with patch.object(mtd, '_libc', return_value=libc):
                with self.subTest(name=name), self.assertRaises(InterruptedError):
                    if name == '_erase_ioctl':
                        mtd._erase_ioctl(FD, payload)
                    else:
                        mtd._pwrite(FD, b'abc', 4096)
            self.assertEqual(len(calls), 1)
            args = calls[0]
            self.assertEqual(args[0], FD)
            if name == '_erase_ioctl':
                self.assertEqual(args[1], 0x40104d14)
                self.assertEqual(ctypes.string_at(args[2], 16), payload)
                self.assertEqual(function.argtypes, (ctypes.c_int, ctypes.c_ulong, ctypes.c_void_p))
            else:
                self.assertEqual(ctypes.string_at(args[1], 3), b'abc')
                self.assertEqual(args[2:], (3, 4096))
                self.assertEqual(function.restype, ctypes.c_ssize_t)

    def test_success_and_invalid_erase_result(self):
        for result in (0, 1, True, None):
            function = Mock(return_value=result)
            with patch.object(mtd, '_libc', return_value=SimpleNamespace(ioctl=function)):
                if type(result) is int and result == 0:
                    self.assertIsNone(mtd._erase_ioctl(FD, mtd.ERASE_INFO.pack(0, mtd.ERASE)))
                else:
                    with self.assertRaises(ValueError):
                        mtd._erase_ioctl(FD, mtd.ERASE_INFO.pack(0, mtd.ERASE))
                self.assertEqual(function.call_count, 1)


if __name__ == '__main__':
    unittest.main()
