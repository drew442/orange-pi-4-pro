import ctypes as C
import unittest
from unittest.mock import patch
import importlib.util
from pathlib import Path

_OBSERVER_PATH = Path(__file__).resolve().parents[1] / 'scripts' / 'observe-first-kvm-exit.py'
_SPEC = importlib.util.spec_from_file_location('first_kvm_exit_observer', _OBSERVER_PATH)
observe = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(observe)


class Function:
    def __init__(self, callback):
        self.callback = callback
    def __call__(self, *args):
        return self.callback(*args)


class Library:
    def __init__(self, fail=None):
        self.calls, self.fail, self.buffers = [], fail, []
    def __getattr__(self, name):
        fn = Function(lambda *args: self.call(name, args))
        setattr(self, name, fn)
        return fn
    def call(self, name, args):
        self.calls.append((name, args))
        if name == 'sd_bus_open_system':
            C.cast(args[0], C.POINTER(C.c_void_p))[0] = 12
        if name == 'sd_bus_call_method':
            member = args[4]
            C.cast(args[6], C.POINTER(C.c_void_p))[0] = 13
            if member == self.fail:
                return -5
        if name == 'sd_bus_message_read':
            buf = C.create_string_buffer(b'/org/freedesktop/systemd1/unit/fixed')
            self.buffers.append(buf)
            C.cast(args[2], C.POINTER(C.c_void_p))[0] = C.addressof(buf)
            return 1
        if name == 'sd_bus_message_unref' and self.buffers:
            self.buffers[-1][0] = b'X'
        if name == 'sd_bus_get_property_string':
            buf = C.create_string_buffer(b'inactive')
            self.buffers.append(buf)
            C.cast(args[6], C.POINTER(C.c_void_p))[0] = C.addressof(buf)
        if name == 'sd_bus_get_property_trivial':
            kind = args[6]
            typ = {b'i': C.c_int32, b'u': C.c_uint32, b't': C.c_uint64}[kind]
            C.cast(args[7], C.POINTER(typ))[0] = -3 if kind == b'i' else 123
        return 0


class AbiTests(unittest.TestCase):
    def create(self, fail=None):
        lib, libc = Library(fail), Library()
        return lib, libc, patch.object(observe.C, 'CDLL', side_effect=[lib, libc])

    def test_error_free_has_exact_void_signature(self):
        lib, _, patched = self.create()
        with patched:
            observe.Bus()
        self.assertEqual(lib.sd_bus_error_free.argtypes, [C.POINTER(observe.Error)])
        self.assertIsNone(lib.sd_bus_error_free.restype)

    def test_copy_path_free_strings_and_release_reference(self):
        lib, libc, patched = self.create()
        with patched, observe.Bus() as bus:
            self.assertTrue(bus.path.startswith(b'/org/'))
            self.assertEqual(bus.get('ActiveState'), 'inactive')
            self.assertEqual(bus.get('ExecMainCode', 'i', True), -3)
            self.assertEqual(bus.get('MainPID', 'u', True), 123)
            self.assertEqual(bus.get('ExecMainStartTimestampMonotonic', 't', True), 123)
        methods = [args[4] for name, args in lib.calls if name == 'sd_bus_call_method']
        self.assertEqual(methods, [b'RefUnit', b'GetUnit', b'UnrefUnit'])
        self.assertEqual(len([1 for name, _ in libc.calls if name == 'free']), 1)
        self.assertEqual(len([1 for name, _ in lib.calls if name == 'sd_bus_message_unref']), 3)
        self.assertEqual(len([1 for name, _ in lib.calls if name == 'sd_bus_error_free']), 7)
        self.assertEqual(lib.calls[-1][0], 'sd_bus_flush_close_unref')

    def test_failed_getunit_still_unrefs_and_closes(self):
        lib, _, patched = self.create(b'GetUnit')
        with patched, self.assertRaises(RuntimeError):
            with observe.Bus():
                self.fail('must not enter')
        methods = [args[4] for name, args in lib.calls if name == 'sd_bus_call_method']
        self.assertEqual(methods, [b'RefUnit', b'GetUnit', b'UnrefUnit'])
        self.assertEqual(lib.calls[-1][0], 'sd_bus_flush_close_unref')

    def test_failed_unref_still_closes(self):
        lib, _, patched = self.create(b'UnrefUnit')
        with patched, self.assertRaises(RuntimeError):
            with observe.Bus():
                pass
        self.assertEqual(lib.calls[-1][0], 'sd_bus_flush_close_unref')

    def test_failed_ref_does_not_unref(self):
        lib, _, patched = self.create(b'RefUnit')
        with patched, self.assertRaises(RuntimeError):
            with observe.Bus():
                pass
        methods = [args[4] for name, args in lib.calls if name == 'sd_bus_call_method']
        self.assertEqual(methods, [b'RefUnit'])
        self.assertEqual(lib.calls[-1][0], 'sd_bus_flush_close_unref')


if __name__ == '__main__':
    unittest.main()
