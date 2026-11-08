#!/usr/bin/env python3
"""Reference and observe one fixed guest service; never start or change it."""
import ctypes as C
import json
import sys
import time

UNIT = b'a733-guest-firstkvm.service'
DEST = b'org.freedesktop.systemd1'
MANAGER = b'/org/freedesktop/systemd1'
TIMEOUT = 300.0


class Error(C.Structure):
    _fields_ = [('name', C.c_char_p), ('message', C.c_char_p), ('need_free', C.c_int)]


class Bus:
    def __init__(self):
        self.lib = C.CDLL('libsystemd.so.0')
        self.libc = C.CDLL(None)
        self.libc.free.argtypes = [C.c_void_p]
        self.libc.free.restype = None
        p, s, ep = C.c_void_p, C.c_char_p, C.POINTER(Error)
        signatures = {
            'sd_bus_open_system': ([C.POINTER(p)], C.c_int),
            'sd_bus_set_method_call_timeout': ([p, C.c_uint64], C.c_int),
            'sd_bus_call_method': ([p, s, s, s, s, ep, C.POINTER(p), s], C.c_int),
            'sd_bus_message_read': ([p, s], C.c_int),
            'sd_bus_get_property_string': ([p, s, s, s, s, ep, C.POINTER(p)], C.c_int),
            'sd_bus_get_property_trivial': ([p, s, s, s, s, ep, C.c_char, p], C.c_int),
            'sd_bus_message_unref': ([p], p),
            'sd_bus_error_free': ([ep], None),
            'sd_bus_flush_close_unref': ([p], p),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(self.lib, name)
            fn.argtypes, fn.restype = args, result
        self.bus, self.path, self.referenced = p(), None, False
        self.deadline = time.monotonic() + TIMEOUT

    @staticmethod
    def check(result, error=None):
        if result < 0:
            detail = error.message.decode(errors='replace') if error and error.message else ''
            raise RuntimeError(f'sd-bus errno {-result}: {detail}')

    def budget(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('300-second observation deadline expired')
        self.check(self.lib.sd_bus_set_method_call_timeout(
            self.bus, max(1, int(remaining * 1000000))))

    def method(self, member):
        if member not in (b'GetUnit', b'RefUnit', b'UnrefUnit'):
            raise ValueError('unsupported method')
        error, reply = Error(), C.c_void_p()
        try:
            self.budget()
            self.check(self.lib.sd_bus_call_method(
                self.bus, DEST, MANAGER, DEST + b'.Manager', member,
                C.byref(error), C.byref(reply), b's', C.c_char_p(UNIT)), error)
            if member == b'GetUnit':
                value = C.c_char_p()
                result = self.lib.sd_bus_message_read(reply, b'o', C.byref(value))
                self.check(result)
                if result == 0 or not value.value:
                    raise RuntimeError('GetUnit returned no object path')
                return bytes(value.value)  # Copy before releasing the reply.
        finally:
            self.lib.sd_bus_message_unref(reply)
            self.lib.sd_bus_error_free(C.byref(error))

    def __enter__(self):
        try:
            self.check(self.lib.sd_bus_open_system(C.byref(self.bus)))
            self.method(b'RefUnit')
            self.referenced = True
            self.path = self.method(b'GetUnit')
            return self
        except BaseException:
            self.close()
            raise

    def close(self):
        try:
            if self.referenced:
                # Allow cleanup even after the observation deadline.
                self.deadline = time.monotonic() + 2
                self.method(b'UnrefUnit')
        finally:
            self.referenced = False
            self.lib.sd_bus_flush_close_unref(self.bus)
            self.bus = C.c_void_p()

    def __exit__(self, *_):
        self.close()

    def get(self, name, kind='s', service=False):
        error, value = Error(), C.c_void_p()
        interface = DEST + (b'.Service' if service else b'.Unit')
        scalar = {'i': C.c_int32, 'u': C.c_uint32, 't': C.c_uint64}
        try:
            self.budget()
            if kind == 's':
                self.check(self.lib.sd_bus_get_property_string(
                    self.bus, DEST, self.path, interface, name.encode(),
                    C.byref(error), C.byref(value)), error)
                if not value.value:
                    raise RuntimeError(f'{name}: null string')
                return C.string_at(value).decode()
            value = scalar[kind]()
            self.check(self.lib.sd_bus_get_property_trivial(
                self.bus, DEST, self.path, interface, name.encode(),
                C.byref(error), kind.encode(), C.byref(value)), error)
            return value.value
        finally:
            if kind == 's':
                self.libc.free(value)
            self.lib.sd_bus_error_free(C.byref(error))

    def snapshot(self):
        data = {name: self.get(name) for name in ('LoadState', 'ActiveState', 'SubState')}
        for name, kind in [('Result', 's'), ('MainPID', 'u'), ('ControlPID', 'u'),
                           ('ExecMainPID', 'u'), ('ExecMainCode', 'i'), ('ExecMainStatus', 'i'),
                           ('ExecMainStartTimestampMonotonic', 't'),
                           ('ExecMainExitTimestampMonotonic', 't')]:
            data[name] = self.get(name, kind, service=True)
        return data


def observe(bus, emit, clock=time.monotonic, sleep=time.sleep):
    deadline = clock() + TIMEOUT
    initial = bus.get('ActiveState')
    if initial != 'inactive':
        raise RuntimeError(f'expected inactive unit before readiness, got {initial}')
    emit({'event': 'ready', 'unit': UNIT.decode(), 'ActiveState': initial})
    seen_active = False
    while clock() < deadline:
        active, sub = bus.get('ActiveState'), bus.get('SubState')
        if active == 'failed':
            raise RuntimeError(f'unit failed ({sub})')
        seen_active |= active == 'active'
        if seen_active and active == 'inactive':
            data = bus.snapshot()
            if not (data['LoadState'] == 'loaded' and data['ActiveState'] == 'inactive'
                    and data['SubState'] == 'dead' and data['Result'] == 'success'
                    and data['MainPID'] == data['ControlPID'] == 0
                    and data['ExecMainPID'] > 0
                    and data['ExecMainCode'] == 1 and data['ExecMainStatus'] == 0
                    and 0 < data['ExecMainStartTimestampMonotonic']
                    <= data['ExecMainExitTimestampMonotonic']):
                raise RuntimeError(f'unclean or incomplete final fields: {json.dumps(data)}')
            return {'event': 'final', 'unit': UNIT.decode(), **data}
        sleep(min(0.2, max(0, deadline - clock())))
    raise TimeoutError('300-second observation deadline expired without active-to-inactive run')


def emit(record):
    print(json.dumps(record, sort_keys=True), flush=True)


def main():
    try:
        if len(sys.argv) != 1:
            raise RuntimeError('no arguments accepted; unit is fixed')
        with Bus() as bus:
            result = observe(bus, emit)
        # Cleanup must also succeed before emitting final evidence.
        emit(result)
        return 0
    except Exception as exc:
        print(f'observer failed: {exc}', file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
