#!/usr/bin/env python3
"""Exercise QEMU user networking with synthetic Ethernet, without a guest CPU.

This is a software network-backend fixture, never KVM or hardware acceptance.
Only loopback TCP listeners and an inherited private socketpair are used.
"""
import argparse
import hashlib
import json
import os
import selectors
import socket
import struct
import subprocess
import time

MAC = bytes.fromhex('525400123456')
GUEST = socket.inet_aton('10.0.2.15')
GATEWAY = socket.inet_aton('10.0.2.2')
MAX_FRAME = 65536


def checksum(data):
    if len(data) % 2:
        data += b'\0'
    total = sum(struct.unpack('!%dH' % (len(data) // 2), data))
    while total >> 16:
        total = (total & 65535) + (total >> 16)
    return (~total) & 65535


def tcp_frame(dst_mac, src_ip, dst_ip, sport, dport, seq, ack, flags, data=b''):
    tcp = struct.pack('!HHIIBBHHH', sport, dport, seq, ack, 80, flags, 65535, 0, 0) + data
    pseudo = src_ip + dst_ip + struct.pack('!BBH', 0, 6, len(tcp))
    tcp = tcp[:16] + struct.pack('!H', checksum(pseudo + tcp)) + tcp[18:]
    ip = struct.pack('!BBHHHBBH4s4s', 69, 0, 20 + len(tcp), 1, 0, 64, 6, 0, src_ip, dst_ip)
    ip = ip[:10] + struct.pack('!H', checksum(ip)) + ip[12:]
    return dst_mac + MAC + b'\x08\x00' + ip + tcp


def parse_tcp(frame):
    if len(frame) < 34 or frame[12:14] != b'\x08\x00':
        return None
    ip = frame[14:]
    ihl = (ip[0] & 15) * 4
    length = int.from_bytes(ip[2:4], 'big')
    if ip[0] >> 4 != 4 or ihl < 20 or length < ihl + 20 or length > len(ip):
        raise ValueError('malformed IPv4 length')
    if ip[9] != 6 or int.from_bytes(ip[6:8], 'big') & 0x3fff:
        return None
    if checksum(ip[:ihl]):
        raise ValueError('invalid IPv4 checksum')
    tcp = ip[ihl:length]
    offset = (tcp[12] >> 4) * 4
    if offset < 20 or offset > len(tcp):
        raise ValueError('malformed TCP offset')
    if checksum(ip[12:20] + struct.pack('!BBH', 0, 6, len(tcp)) + tcp):
        raise ValueError('invalid TCP checksum')
    sport, dport, seq, ack = struct.unpack('!HHII', tcp[:12])
    return dict(src=ip[12:16], dst=ip[16:20], sport=sport, dport=dport,
                seq=seq, ack=ack, flags=tcp[13], data=tcp[offset:], mac=frame[6:12])


class Frames:
    def __init__(self):
        self.buffer = bytearray()
        self.count = 0

    def feed(self, data):
        self.buffer.extend(data)
        frames = []
        while len(self.buffer) >= 4:
            length = int.from_bytes(self.buffer[:4], 'big')
            if length < 14 or length > MAX_FRAME:
                raise ValueError('invalid socket-netdev frame length')
            if len(self.buffer) < length + 4:
                break
            self.count += 1
            if self.count > 4096:
                raise ValueError('socket-netdev frame count exceeded')
            frames.append(bytes(self.buffer[4:4 + length]))
            del self.buffer[:4 + length]
        return frames


def loopback_bind_evidence(port):
    """Read the kernel IPv4 table; reject any non-loopback bind on this port."""
    rows = []
    with open('/proc/net/tcp', encoding='ascii') as table:
        for line in list(table)[1:]:
            fields = line.split()
            address, value = fields[1].split(':')
            if int(value, 16) == port and fields[3] == '0A':
                rows.append(address)
    if rows != ['0100007F']:
        raise RuntimeError('hostfwd lacks exclusive IPv4 loopback listener: %r' % rows)
    # An IPv6 wildcard listener would also expose the port on some hosts.
    with open('/proc/net/tcp6', encoding='ascii') as table:
        for line in list(table)[1:]:
            fields = line.split()
            if int(fields[1].split(':')[1], 16) == port and fields[3] == '0A':
                raise RuntimeError('unexpected IPv6 listener on hostfwd port')
    return {'source': '/proc/net/tcp and /proc/net/tcp6', 'address': '127.0.0.1',
            'port': port, 'wildcard_listener': False}


def probe(qemu_path, timeout=20):
    if not 1 <= timeout <= 120:
        raise ValueError('timeout must be between 1 and 120 seconds')
    deadline = time.monotonic() + timeout
    selector = selectors.DefaultSelector()
    owned = []
    process = None
    stderr = bytearray()
    qmp_data = bytearray()
    replies = []
    framed = Frames()
    wire_output = bytearray()
    frames = []
    payload = b'a733-network-backend-fixture:' + os.urandom(24)
    client_received = bytearray()
    client = None
    try:
        wire, inherited = socket.socketpair()
        owned.extend([wire, inherited])
        reserve = socket.socket()
        owned.append(reserve)
        reserve.bind(('127.0.0.1', 0))
        forward_port = reserve.getsockname()[1]
        reserve.close()
        listener = socket.socket()
        owned.append(listener)
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        listener.setblocking(False)
        blocked_port = listener.getsockname()[1]
        args = [os.fspath(qemu_path), '-machine', 'none', '-nodefaults', '-display', 'none',
                '-serial', 'none', '-qmp', 'stdio', '-netdev',
                'user,id=u,restrict=on,ipv6=off,hostfwd=tcp:127.0.0.1:%d-10.0.2.15:8080' % forward_port,
                '-netdev', 'socket,id=wire,fd=%d' % inherited.fileno(),
                '-netdev', 'hubport,id=h0,hubid=0,netdev=u',
                '-netdev', 'hubport,id=h1,hubid=0,netdev=wire']
        process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, pass_fds=(inherited.fileno(),))
        inherited.close()
        for stream, tag in [(wire, 'wire'), (process.stdout, 'qmp'), (process.stderr, 'stderr')]:
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, tag)

        def tick(until=None):
            now = time.monotonic()
            if now >= deadline:
                raise TimeoutError('network fixture overall deadline exceeded')
            if process.poll() is not None:
                try:
                    stderr.extend(os.read(process.stderr.fileno(), 16384 - len(stderr)))
                except BlockingIOError:
                    pass
                raise RuntimeError('QEMU exited: ' + stderr.decode(errors='replace'))
            if wire_output:
                try:
                    sent = wire.send(wire_output)
                    del wire_output[:sent]
                except BlockingIOError:
                    pass
            for key, _ in selector.select(min(.02, deadline - now, max(0, until - now) if until else .02)):
                try:
                    data = os.read(key.fd, 65536)
                except BlockingIOError:
                    continue
                if not data:
                    selector.unregister(key.fileobj)
                    if key.data in ('wire', 'qmp'):
                        raise RuntimeError('QEMU closed ' + key.data + ': ' + stderr.decode(errors='replace'))
                    continue
                if key.data == 'stderr':
                    stderr.extend(data[:max(0, 16384 - len(stderr))])
                elif key.data == 'wire':
                    frames.extend(framed.feed(data))
                else:
                    qmp_data.extend(data)
                    if len(qmp_data) > MAX_FRAME:
                        raise ValueError('QMP response exceeds limit')
                    while b'\n' in qmp_data:
                        line, _, rest = qmp_data.partition(b'\n')
                        qmp_data[:] = rest
                        replies.append(json.loads(line))
                    if len(replies) > 128:
                        raise ValueError('too many QMP responses')

        def command(name):
            process.stdin.write((json.dumps({'execute': name, 'id': name}) + '\n').encode())
            process.stdin.flush()
            while True:
                tick()
                for reply in replies:
                    if reply.get('id') == name:
                        if 'error' in reply:
                            raise RuntimeError('QMP error: %r' % reply)
                        return reply['return']

        while not any('QMP' in item for item in replies):
            tick()
        command('qmp_capabilities')
        cpus = command('query-cpus-fast')
        if cpus != []:
            raise RuntimeError('fixture unexpectedly has CPUs')
        bind = loopback_bind_evidence(forward_port)
        client = socket.socket()
        owned.append(client)
        client.settimeout(max(.001, deadline - time.monotonic()))
        client.connect(('127.0.0.1', forward_port))
        client.sendall(payload)
        client.setblocking(False)
        peer = None
        next_seq = 1001
        guest_data = bytearray()
        guest_start = None
        gateway_mac = None

        def send(frame):
            if len(wire_output) + len(frame) + 4 > 4 * MAX_FRAME:
                raise ValueError('outgoing wire buffer exceeded')
            wire_output.extend(struct.pack('!I', len(frame)) + frame)

        def service():
            nonlocal peer, next_seq, gateway_mac, guest_start
            while frames:
                frame = frames.pop(0)
                if frame[12:14] == b'\x08\x06' and len(frame) >= 42:
                    arp = frame[14:42]
                    if arp[:6] != b'\x00\x01\x08\x00\x06\x04':
                        continue
                    if arp[14:18] == GATEWAY:
                        gateway_mac = arp[8:14]
                    if arp[6:8] == b'\x00\x01' and arp[24:28] == GUEST:
                        send(frame[6:12] + MAC + b'\x08\x06' + arp[:6] + b'\x00\x02' +
                             MAC + GUEST + arp[8:18])
                    continue
                tcp = parse_tcp(frame)
                if not tcp or tcp['dst'] != GUEST or tcp['dport'] != 8080:
                    continue
                gateway_mac = tcp['mac']
                peer = tcp
                flags = tcp['flags']
                ack = (tcp['seq'] + len(tcp['data']) + bool(flags & 2) + bool(flags & 1)) & 0xffffffff
                if flags & 2:
                    guest_start = (tcp['seq'] + 1) & 0xffffffff
                    send(tcp_frame(tcp['mac'], GUEST, tcp['src'], 8080, tcp['sport'], 1000, ack, 0x12))
                elif tcp['data']:
                    if guest_start is None:
                        raise RuntimeError('TCP data arrived before SYN')
                    expected = (guest_start + len(guest_data)) & 0xffffffff
                    if tcp['seq'] == expected:
                        guest_data.extend(tcp['data'])
                        if not payload.startswith(guest_data):
                            raise RuntimeError('unexpected hostfwd payload')
                        if bytes(guest_data) == payload and next_seq == 1001:
                            send(tcp_frame(tcp['mac'], GUEST, tcp['src'], 8080,
                                           tcp['sport'], next_seq, ack, 0x18, payload))
                            next_seq += len(payload)
                        else:
                            send(tcp_frame(tcp['mac'], GUEST, tcp['src'], 8080,
                                           tcp['sport'], next_seq, ack, 0x10))
                    else:
                        send(tcp_frame(tcp['mac'], GUEST, tcp['src'], 8080, tcp['sport'],
                                       next_seq, expected, 0x10))
                elif flags & 1:
                    send(tcp_frame(tcp['mac'], GUEST, tcp['src'], 8080, tcp['sport'], next_seq, ack, 0x10))

        while bytes(client_received) != payload:
            tick()
            service()
            try:
                received = client.recv(4096)
                if not received:
                    raise RuntimeError('hostfwd client closed before echo')
                client_received.extend(received)
                if len(client_received) > len(payload):
                    raise RuntimeError('excess hostfwd echo bytes')
            except BlockingIOError:
                pass
        if not peer or not gateway_mac:
            raise RuntimeError('missing guest endpoint evidence')
        send(tcp_frame(gateway_mac, GUEST, GATEWAY, 40000, blocked_port, 5000, 0, 2))
        end = min(deadline, time.monotonic() + .75)
        if end + .01 >= deadline:
            raise TimeoutError('insufficient deadline for isolation observation')
        while time.monotonic() < end:
            tick(end)
            service()
            try:
                accepted, _ = listener.accept()
            except BlockingIOError:
                continue
            accepted.close()
            raise RuntimeError('restrict=on allowed guest access to host listener')
        return {'classification': 'network-backends-only', 'qmp_cpus': cpus,
                'payload_hex': payload.hex(), 'payload_sha256': hashlib.sha256(payload).hexdigest(),
                'roundtrip_bytes': len(client_received), 'loopback_bind': bind,
                'blocked_host_attempt': {'destination': '10.0.2.2', 'port': blocked_port,
                                         'observation_seconds': .75, 'host_accepts': 0},
                'frames_received': framed.count, 'process_status': 'running-before-cleanup'}
    except Exception as error:
        raise RuntimeError('%s; QEMU stderr: %s' % (error, stderr.decode(errors='replace'))) from error
    finally:
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream:
                    stream.close()
        selector.close()
        for item in owned:
            item.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', required=True, help='trusted QEMU executable path')
    parser.add_argument('--timeout', type=float, default=20)
    options = parser.parse_args()
    print(json.dumps(probe(options.qemu, options.timeout), sort_keys=True))
