#!/usr/bin/env python3
"""Serve three public deployment artifacts over bounded, single-client HTTPS.

The operator must distribute the delivery CA and manifest SHA-256 out of band.
This validates delivery bytes only; it does not establish installation acceptance.
No files are created or modified. Requires Linux descriptor paths for TLS loading.
"""
import argparse
import contextlib
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import ipaddress
import json
import os
from pathlib import Path
import re
import ssl
import stat
import sys

CHUNK = 1024 * 1024
MAX_IMAGE = 8 * 1024**3
TIMEOUT = 10
NAMES = {'/nvme.img.gz', '/manifest.json', '/delivery-ca.pem'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def identity(fd):
    info = os.fstat(fd)
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns, info.st_nlink)


def open_regular(path):
    """Open each path component without following links, including ancestors."""
    path = Path(os.path.abspath(path))
    directory = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.parts[1:-1]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=directory)
        try:
            require(stat.S_ISREG(os.fstat(fd).st_mode), 'artifact must be a regular file')
            return os.fdopen(fd, 'rb')
        except BaseException:
            os.close(fd)
            raise
    finally:
        os.close(directory)


def snapshot(stream, limit):
    before = identity(stream.fileno())
    require(before[2] <= limit, 'snapshot exceeds size limit')
    data = stream.read(limit + 1)
    require(len(data) == before[2] and len(data) <= limit
            and before == identity(stream.fileno()), 'snapshot changed while reading')
    return data


class Artifacts:
    def __init__(self, directory):
        self.stack = contextlib.ExitStack()
        try:
            self.directory = Path(os.path.abspath(directory))
            require(self.directory.is_dir() and self.directory.resolve() == self.directory,
                    'artifact directory must exist without symlink ancestors')
            self.image = self.stack.enter_context(open_regular(self.directory / 'nvme.img.gz'))
            manifest = self.stack.enter_context(open_regular(self.directory / 'manifest.json'))
            ca = self.stack.enter_context(open_regular(self.directory / 'delivery-ca.pem'))
            self.snapshots = {'/manifest.json': snapshot(manifest, 8 * CHUNK),
                              '/delivery-ca.pem': snapshot(ca, CHUNK)}
            ca_data = self.snapshots['/delivery-ca.pem'].decode('ascii')
            public_pem = r'(?:\s*-----BEGIN CERTIFICATE-----\s+[A-Za-z0-9+/=\s]+-----END CERTIFICATE-----\s*)+'
            require(re.fullmatch(public_pem, ca_data) is not None,
                    'delivery CA must contain only public certificates')
            ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT).load_verify_locations(cadata=ca_data)
            expected = json.loads(self.snapshots['/manifest.json'])['files']['nvme.img.gz']
            require(type(expected['bytes']) is int and 0 < expected['bytes'] <= MAX_IMAGE,
                    'invalid encoded image size')
            self.image_identity = identity(self.image.fileno())
            require(self.image_identity[2] == expected['bytes'], 'encoded image size differs from manifest')
            digest = hashlib.sha256()
            remaining = expected['bytes']
            while remaining:
                block = self.image.read(min(CHUNK, remaining))
                require(block, 'encoded image truncated')
                remaining -= len(block)
                digest.update(block)
            self.check_image()
            require(digest.hexdigest() == expected['sha256'], 'encoded image SHA-256 differs from manifest')
            self.image.seek(0)
            self.source_ids = {identity(stream.fileno())[:2] for stream in (self.image, manifest, ca)}
        except BaseException:
            self.close()
            raise

    def check_image(self):
        require(identity(self.image.fileno()) == self.image_identity, 'encoded image changed while held')

    def close(self):
        self.stack.close()


def tls_context(artifacts, certificate, key):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    with contextlib.ExitStack() as stack:
        streams = []
        for path in (certificate, key):
            absolute = Path(os.path.abspath(path))
            require(absolute != artifacts.directory and artifacts.directory not in absolute.parents,
                    'TLS certificate and key must be outside the artifact directory')
            stream = stack.enter_context(open_regular(absolute))
            require(identity(stream.fileno())[:2] not in artifacts.source_ids,
                    'TLS material must not alias served artifacts')
            require(identity(stream.fileno())[2] <= CHUNK, 'TLS material exceeds size limit')
            streams.append(stream)
        before = [identity(stream.fileno()) for stream in streams]
        context.load_cert_chain(*(f'/proc/self/fd/{stream.fileno()}' for stream in streams))
        require(before == [identity(stream.fileno()) for stream in streams], 'TLS material changed while loading')
    return context


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'
    server_version = 'DeploymentHTTPS'
    sys_version = ''

    def log_message(self, *args):
        pass

    def send_error(self, code, message=None, explain=None):
        if code == 501:
            code, message = 405, 'Method not allowed'
        super().send_error(code, message, explain)

    def do_HEAD(self):
        self.deliver(False)

    def do_GET(self):
        self.deliver(True)

    def deliver(self, body):
        self.close_connection = True
        if self.path not in NAMES or self.requestline.split()[1] != self.path:
            self.send_error(404)
            return
        artifacts = self.server.artifacts
        is_image = self.path == '/nvme.img.gz'
        try:
            if is_image:
                artifacts.check_image()
                size = artifacts.image_identity[2]
                artifacts.image.seek(0)
            else:
                data = artifacts.snapshots[self.path]
                size = len(data)
        except ValueError:
            self.send_error(503, 'Artifact changed; restart after verification')
            return
        self.send_response(200)
        self.send_header('Content-Length', str(size))
        self.send_header('Content-Type', {'/manifest.json': 'application/json',
                                        '/delivery-ca.pem': 'application/x-pem-file',
                                        '/nvme.img.gz': 'application/gzip'}[self.path])
        self.send_header('Connection', 'close')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        if not body:
            return
        try:
            if not is_image:
                self.wfile.write(data)
                return
            remaining = size
            while remaining:
                artifacts.check_image()
                block = artifacts.image.read(min(CHUNK, remaining))
                require(block, 'encoded image truncated')
                artifacts.check_image()
                self.wfile.write(block)
                remaining -= len(block)
            artifacts.check_image()
        except (OSError, ValueError):
            # Headers are already sent: close early so Content-Length detects truncation.
            self.close_connection = True


class DeliveryServer(HTTPServer):
    request_queue_size = 4

    def __init__(self, address, artifacts, context):
        self.artifacts = artifacts
        self.context = context
        super().__init__(address, Handler)

    def get_request(self):
        connection, address = self.socket.accept()
        connection.settimeout(TIMEOUT)
        try:
            return self.context.wrap_socket(connection, server_side=True), address
        except BaseException:
            connection.close()
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True, type=Path)
    parser.add_argument('--certificate', required=True, type=Path)
    parser.add_argument('--key', required=True, type=Path)
    parser.add_argument('--bind', required=True, help='explicit IPv4 address of the serving interface')
    parser.add_argument('--port', required=True, type=int)
    args = parser.parse_args(argv)
    try:
        address = ipaddress.IPv4Address(args.bind)
        require(not address.is_unspecified and not address.is_multicast, 'bind requires a specific unicast IPv4 address')
        require(1024 <= args.port <= 65535, 'port must be unprivileged (1024..65535)')
        with contextlib.closing(Artifacts(args.directory)) as artifacts:
            context = tls_context(artifacts, args.certificate, args.key)
            with DeliveryServer((str(address), args.port), artifacts, context) as server:
                digest = hashlib.sha256(artifacts.snapshots['/manifest.json']).hexdigest()
                print(f'Serving https://{address}:{args.port}; manifest SHA-256 {digest}', flush=True)
                server.serve_forever(poll_interval=0.5)
        return 0
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f'deployment delivery: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
