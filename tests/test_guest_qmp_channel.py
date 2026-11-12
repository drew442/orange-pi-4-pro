"""Socketpair transport contracts only; no physical KVM/child identity claim."""
import importlib.util
import fcntl
import json
import os
from pathlib import Path
import socket
import threading
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    'guest_qmp_channel', Path(__file__).resolve().parents[1] / 'scripts/guest-qmp-channel.py')
CHANNEL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHANNEL)
RUN = '655f5a94-fb19-4198-863d-4162f99fc660'
GREETING = {'QMP': {'version': {'qemu': {'major': 7, 'minor': 2, 'micro': 22},
                               'package': 'Debian 1:7.2+dfsg-7+deb12u18'},
                    'capabilities': ['oob']}}
SHUTDOWN = {'event': 'SHUTDOWN', 'data': {'guest': True, 'reason': 'guest-shutdown'}}


def wire(*messages):
    return b''.join(json.dumps(m, separators=(',', ':')).encode() + b'\n' for m in messages)


class Witness:
    def __init__(self):
        self.checked = False

    def verify_qmp_peer(self, connection):
        self.checked = True
        return {'fixture': True}


class FragmentSocket(socket.socket):
    """Real selectable socket with deterministic partial writes."""
    def send(self, data, *args):
        return super().send(data[:3], *args)


class StalledSocket(socket.socket):
    def send(self, data, *args):
        if getattr(self, 'started', False):
            raise BlockingIOError()
        self.started = True
        return super().send(data[:3], *args)


class ChannelTests(unittest.TestCase):
    def test_hard_handshake_ack_and_finish_deadline_caps(self):
        for method in ('greeting', 'command', 'finish'):
            with self.subTest(method=method):
                channel = CHANNEL.Channel(self.left, Witness(), RUN)
                args = ('qmp_capabilities',) if method == 'command' else ()
                with self.assertRaisesRegex(CHANNEL.ChannelError, 'within'):
                    getattr(channel, method)(*args, timeout=10.01)
                self.assertTrue(channel._failed)

    def test_actual_kernel_nonblocking_flag_is_required(self):
        flags = fcntl.fcntl(self.left, fcntl.F_GETFL)
        fcntl.fcntl(self.left, fcntl.F_SETFL, flags & ~os.O_NONBLOCK)
        self.assertEqual(self.left.gettimeout(), 0.0)
        with self.assertRaises(CHANNEL.ChannelError):
            CHANNEL.Channel(self.left, Witness(), RUN)

    def setUp(self):
        self.left, self.peer = socket.socketpair()
        self.left.setblocking(False)
        self.peer.settimeout(1)
        self.addCleanup(self.left.close)
        self.addCleanup(self.peer.close)
        self.witness = Witness()
        self.channel = CHANNEL.Channel(self.left, self.witness, RUN)

    def greet(self):
        self.peer.sendall(wire(GREETING))
        self.assertEqual(self.channel.greeting(.2), GREETING)

    def command(self, name, before=(), after=(), close=False):
        failures = []

        def peer():
            try:
                data = bytearray()
                while not data.endswith(b'\n'):
                    data.extend(self.peer.recv(4096))
                message = json.loads(data)
                self.assertTrue(self.witness.checked)
                self.assertEqual(message, {'execute': name, 'id': name + ':' + RUN})
                self.peer.sendall(wire(*before, {'return': {}, 'id': message['id']}, *after))
                if close:
                    self.peer.shutdown(socket.SHUT_WR)
            except BaseException as exc:
                failures.append(exc)

        thread = threading.Thread(target=peer)
        thread.start()
        try:
            result = self.channel.command(name, .5)
        finally:
            thread.join(2)
        self.assertFalse(thread.is_alive())
        if failures:
            raise failures[0]
        return result

    def running(self):
        self.greet()
        self.command('qmp_capabilities')
        self.command('cont')

    def assert_poisoned(self):
        self.assertFalse(self.channel.clean_shutdown)
        for operation in [lambda: self.channel.command('cont', .01),
                          lambda: self.channel.receive(.01),
                          lambda: self.channel.finish(.01)]:
            with self.assertRaises(CHANNEL.ChannelError):
                operation()

    def test_shutdown_before_ack_requires_eof_and_retains_async(self):
        self.running()
        custom = {'event': 'CUSTOM', 'data': {'text': 'é中'}}
        self.command('system_powerdown', before=(custom, SHUTDOWN))
        self.assertFalse(self.channel.clean_shutdown)
        self.assertEqual(self.channel.pump(.1), SHUTDOWN)
        self.peer.shutdown(socket.SHUT_WR)
        self.assertTrue(self.channel.finish(.2))
        transcript = self.channel.transcript
        self.assertIn(custom, [entry['message'] for entry in transcript])
        transcript[0]['message'].clear()
        self.assertEqual(self.channel.transcript[0]['message'], GREETING)
        self.assertEqual(self.left.gettimeout(), 0.0)
        self.assertGreaterEqual(self.left.fileno(), 0)

    def test_pipelined_trailing_shutdown_reset_and_duplicate_are_checked(self):
        for trailing in [{'event': 'RESET'}, SHUTDOWN]:
            with self.subTest(trailing=trailing):
                if self.channel.clean_shutdown or self.channel._finished:
                    self.setUp()
                self.running()
                self.command('system_powerdown', before=(SHUTDOWN,), after=(trailing,), close=True)
                self.assertFalse(self.channel.finish(.2))
                self.assertFalse(self.channel.clean_shutdown)

    def test_self_shutdown_before_cont_ack(self):
        self.greet()
        self.command('qmp_capabilities')
        self.command('cont', before=(SHUTDOWN,), close=True)
        self.assertTrue(self.channel.finish(.2))

    def test_ack_without_shutdown_is_not_clean(self):
        self.running()
        self.command('system_powerdown', close=True)
        self.assertFalse(self.channel.finish(.2))

    def test_partial_writes_send_one_command(self):
        replacement = FragmentSocket(fileno=self.left.detach())
        replacement.setblocking(False)
        self.left = replacement
        self.addCleanup(replacement.close)
        self.channel = CHANNEL.Channel(replacement, self.witness, RUN)
        self.greet()
        self.command('qmp_capabilities')
        self.command('cont')
        with self.assertRaises(CHANNEL.ChannelError):
            self.channel.command('cont', .1)
        with self.assertRaises(socket.timeout):
            self.peer.recv(4096)
        self.assert_poisoned()

    def test_timeout_no_ack_or_only_async_poison_and_no_replay(self):
        self.greet()
        self.peer.sendall(wire({'event': 'CUSTOM'}))
        with self.assertRaises(CHANNEL.ChannelError):
            self.channel.command('qmp_capabilities', .02)
        first = self.peer.recv(4096)
        self.assertEqual(json.loads(first)['execute'], 'qmp_capabilities')
        self.assert_poisoned()
        self.peer.settimeout(.02)
        with self.assertRaises(socket.timeout):
            self.peer.recv(4096)

    def test_partial_send_timeout_is_terminal_without_replay(self):
        replacement = StalledSocket(fileno=self.left.detach())
        replacement.setblocking(False)
        self.left = replacement
        self.addCleanup(replacement.close)
        self.channel = CHANNEL.Channel(replacement, self.witness, RUN)
        self.greet()
        with self.assertRaises(CHANNEL.ChannelError):
            self.channel.command('qmp_capabilities', .02)
        self.assertEqual(self.peer.recv(4096), b'{"e')
        self.assert_poisoned()
        self.peer.settimeout(.02)
        with self.assertRaises(socket.timeout):
            self.peer.recv(4096)

    def test_eof_before_ack_and_truncated_eof_poison(self):
        for payload in [b'', b'{"event":']:
            with self.subTest(payload=payload):
                if self.channel._failed:
                    self.setUp()
                self.greet()
                self.peer.sendall(payload)
                self.peer.shutdown(socket.SHUT_WR)
                with self.assertRaises(CHANNEL.ChannelError):
                    self.channel.command('qmp_capabilities', .2)
                self.assert_poisoned()

    def test_finish_truncated_or_malformed_trailing_frames_poison(self):
        for tail in [b'{', b'[]\n', b'{"event":"X","event":"Y"}\n']:
            with self.subTest(tail=tail):
                if self.channel._failed:
                    self.setUp()
                self.running()
                self.peer.sendall(wire(SHUTDOWN) + tail)
                self.peer.shutdown(socket.SHUT_WR)
                with self.assertRaises(CHANNEL.ChannelError):
                    self.channel.finish(.2)
                self.assert_poisoned()

    def test_limits_raw_bytes_message_count_and_message_size(self):
        for bound, value, payload in [
                ('MAX_TOTAL_BYTES', 300, b' ' * 301),
                ('MAX_MESSAGES', 3, wire(*[{'event': 'X'}] * 4)),
                ('MAX_MESSAGE_BYTES', 256, b'x' * 258)]:
            with self.subTest(bound=bound):
                if self.channel._failed:
                    self.setUp()
                self.greet()
                self.peer.sendall(payload)
                with patch.object(CHANNEL.QMP, bound, value):
                    with self.assertRaises(CHANNEL.ChannelError):
                        self.channel.finish(.2)
                self.assert_poisoned()

    def test_greeting_is_first_exact_version_and_consumed_once(self):
        self.peer.sendall(wire(GREETING, {'event': 'CUSTOM'}))
        self.assertEqual(self.channel.greeting(.2), GREETING)
        self.assertEqual(len(self.channel.transcript), 1)
        self.assertEqual(self.channel.receive(.2), {'event': 'CUSTOM'})
        with self.assertRaises(CHANNEL.ChannelError):
            self.channel.greeting(.2)
        self.assert_poisoned()

    def test_peer_witness_failure_does_not_consume_or_send(self):
        self.peer.sendall(wire(GREETING))
        with patch.object(self.witness, 'verify_qmp_peer', side_effect=ValueError('untrusted')):
            with self.assertRaises(CHANNEL.ChannelError):
                CHANNEL.Channel(self.left, self.witness, RUN)
        self.assertEqual(self.left.recv(4096), wire(GREETING))

    def test_wrong_version_and_io_errors_poison(self):
        greeting = json.loads(json.dumps(GREETING))
        greeting['QMP']['version']['qemu']['micro'] = 23
        self.peer.sendall(wire(greeting))
        with self.assertRaises(CHANNEL.ChannelError):
            self.channel.greeting(.2)
        self.assert_poisoned()
        self.setUp()
        self.left.close()
        with self.assertRaises(CHANNEL.ChannelError):
            self.channel.greeting(.2)
        self.assert_poisoned()

    def test_combined_wire_budget_includes_outgoing_commands(self):
        self.greet()
        raw = self.channel._raw_total
        with patch.object(CHANNEL.QMP, 'MAX_TOTAL_BYTES', raw + 10):
            with self.assertRaises(CHANNEL.ChannelError):
                self.channel.command('qmp_capabilities', .2)
        self.assert_poisoned()

    def test_finish_eof_deadline_is_required(self):
        self.running()
        self.peer.sendall(wire(SHUTDOWN))
        self.channel.pump(.2)
        with self.assertRaises(CHANNEL.ChannelError):
            self.channel.finish(.02)
        self.assert_poisoned()

    def test_requires_nonblocking_connected_unix_stream(self):
        self.left.setblocking(True)
        with self.assertRaises(CHANNEL.ChannelError):
            CHANNEL.Channel(self.left, self.witness, RUN)
        self.assertIsNone(self.left.gettimeout())
        for family, kind in [(socket.AF_UNIX, socket.SOCK_STREAM),
                             (socket.AF_UNIX, socket.SOCK_DGRAM),
                             (socket.AF_INET, socket.SOCK_STREAM)]:
            with socket.socket(family, kind) as connection:
                connection.setblocking(False)
                with self.assertRaises(CHANNEL.ChannelError):
                    CHANNEL.Channel(connection, self.witness, RUN)

    def test_timeout_validation_and_wait_failures_are_terminal(self):
        for timeout in [0, -1, 61, float('nan'), float('inf'), True, '1']:
            with self.subTest(timeout=timeout):
                if self.channel._failed:
                    self.setUp()
                with self.assertRaises(CHANNEL.ChannelError):
                    self.channel.greeting(timeout)
                self.assert_poisoned()

    def test_graceful_wait_and_finish_timeout_poison(self):
        self.running()
        with self.assertRaises(CHANNEL.ChannelError):
            self.channel.pump(.02)
        self.assert_poisoned()


if __name__ == '__main__':
    unittest.main()
