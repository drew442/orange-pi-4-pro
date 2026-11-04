"""Packet correctness and failure cleanup for the zero-CPU QEMU fixture."""
import importlib.util
import os
from pathlib import Path
import socket
import struct
import subprocess
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('guest_network_packets', Path(__file__).with_name('guest_network_packets.py'))
packets = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(packets)


class PacketTests(unittest.TestCase):
    def test_checksum_known_vector_and_odd_length(self):
        # RFC 1071's arithmetic example: 0001 f203 f4f5 f6f7.
        self.assertEqual(packets.checksum(bytes.fromhex('0001f203f4f5f6f7')), 0x220d)
        self.assertEqual(packets.checksum(b'\x01'), 0xfeff)

    def test_packet_has_independently_valid_wire_checksums(self):
        frame = packets.tcp_frame(bytes.fromhex('52550a000202'), packets.GUEST,
                                  packets.GATEWAY, 8080, 42000, 1001, 99, 0x18, b'odd')
        # Explicitly fold the on-wire words, including supplied checksum.
        def fold(data):
            if len(data) % 2:
                data += b'\0'
            value = sum(int.from_bytes(data[n:n + 2], 'big') for n in range(0, len(data), 2))
            while value > 65535:
                value = (value & 65535) + (value >> 16)
            return value
        self.assertEqual(fold(frame[14:34]), 65535)
        pseudo = packets.GUEST + packets.GATEWAY + b'\0\x06' + struct.pack('!H', len(frame) - 34)
        self.assertEqual(fold(pseudo + frame[34:]), 65535)
        parsed = packets.parse_tcp(frame)
        self.assertEqual((parsed['sport'], parsed['dport'], parsed['seq'], parsed['ack'], parsed['data']),
                         (8080, 42000, 1001, 99, b'odd'))

    def test_corruption_and_invalid_lengths_rejected(self):
        good = packets.tcp_frame(packets.MAC, packets.GUEST, packets.GATEWAY, 1, 2, 3, 4, 0x10, b'test')
        for index in (16, 24, len(good) - 1):
            broken = bytearray(good)
            broken[index] ^= 1
            with self.assertRaises(ValueError):
                packets.parse_tcp(broken)
        self.assertIsNone(packets.parse_tcp(b'\0' * 13))
        with self.assertRaises(ValueError):
            packets.parse_tcp(good[:-1])

    def test_stream_fragmentation_and_caps(self):
        stream = packets.Frames()
        first, second = b'a' * 14, b'b' * 1500
        encoded = struct.pack('!I', len(first)) + first + struct.pack('!I', len(second)) + second
        frames = []
        for byte in encoded:
            frames.extend(stream.feed(bytes([byte])))
        self.assertEqual(frames, [first, second])
        for size in (0, 13, packets.MAX_FRAME + 1):
            with self.assertRaises(ValueError):
                packets.Frames().feed(struct.pack('!I', size))
        stream.count = 4096
        with self.assertRaises(ValueError):
            stream.feed(struct.pack('!I', 14) + first)

    def test_non_tcp_ignored(self):
        frame = bytearray(packets.tcp_frame(packets.MAC, packets.GUEST, packets.GATEWAY, 1, 2, 3, 4, 2))
        frame[23] = 17
        self.assertIsNone(packets.parse_tcp(frame))


class CleanupTests(unittest.TestCase):
    def run_fake(self, body, timeout=2):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / 'fake-qemu'
            executable.write_text('#!/usr/bin/env python3\n' + body)
            executable.chmod(0o700)
            children = []
            real_popen = subprocess.Popen
            def capture(*args, **kwargs):
                child = real_popen(*args, **kwargs)
                children.append(child)
                return child
            before = len(os.listdir('/proc/self/fd'))
            with mock.patch.object(packets.subprocess, 'Popen', side_effect=capture):
                with self.assertRaises(RuntimeError):
                    packets.probe(executable, timeout)
            self.assertEqual(len(children), 1)
            self.assertIsNotNone(children[0].poll())
            self.assertTrue(all(stream.closed for stream in
                                (children[0].stdin, children[0].stdout, children[0].stderr)))
            self.assertEqual(len(os.listdir('/proc/self/fd')), before)

    def test_qmp_malformed_response_reaps_child_and_closes_fds(self):
        self.run_fake("import time\nprint('invalid-json', flush=True)\ntime.sleep(60)\n")

    def test_deadline_kills_unresponsive_child_and_closes_fds(self):
        self.run_fake("import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(60)\n", 1)

    def test_unexpected_cpus_rejected_and_child_reaped(self):
        self.run_fake('''import json, sys, time
print(json.dumps({'QMP': {}}), flush=True)
for line in sys.stdin:
    request = json.loads(line)
    result = [{}] if request['execute'] == 'query-cpus-fast' else {}
    print(json.dumps({'id': request['id'], 'return': result}), flush=True)
''')


if __name__ == '__main__':
    unittest.main()
