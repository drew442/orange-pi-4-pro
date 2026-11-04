#!/usr/bin/env python3
"""Acquire a real builder bundle over temporary loopback HTTPS, files only.

This tests the full native acquisition path and actual image bounds. It is not
an A733 network, physical installation or deployment acceptance test.
"""
import hashlib
import http.server
import importlib.util
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile
import threading
import time


REPO = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, REPO / path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


ACQUIRE = module('full_https_acquisition', 'scripts/acquire-nvme.py')
STATE = module('full_https_storage', 'build/host-state.py')


def verify(source, output):
    source = Path(source)
    ACQUIRE.require(source.is_absolute() and source.resolve() == source and source.is_dir(),
                    'canonical source directory required')
    STATE.validate_workspace_storage(source)
    STATE.validate_workspace_storage(Path('/var/tmp'))
    with ACQUIRE.WRITER.open_regular(source / 'manifest.json') as manifest:
        data, value = ACQUIRE.WRITER.read_json(manifest)
    pin = hashlib.sha256(data).hexdigest()
    _, raw_record, encoded_record = ACQUIRE.validate_manifest(data, pin)
    with ACQUIRE.WRITER.open_regular(source / 'nvme.img.gz') as image:
        ACQUIRE.require(os.fstat(image.fileno()).st_size == encoded_record['bytes'],
                        'encoded source size differs')

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            paths = {'/manifest.json': 'manifest.json', '/nvme.img.gz': 'nvme.img.gz'}
            if self.path not in paths:
                self.send_error(404)
                return
            with ACQUIRE.WRITER.open_regular(source / paths[self.path]) as content:
                self.send_response(200)
                self.send_header('Content-Length', str(os.fstat(content.fileno()).st_size))
                self.end_headers()
                while True:
                    chunk = content.read(1024**2)
                    if not chunk:
                        break
                    self.wfile.write(chunk)

        def log_message(self, *args):
            pass

    with tempfile.TemporaryDirectory(prefix='a733-full-https-', dir='/var/tmp') as temporary:
        certificate, key = Path(temporary) / 'certificate.pem', Path(temporary) / 'key.pem'
        subprocess.run(['/usr/bin/openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                        '-keyout', str(key), '-out', str(certificate), '-days', '1',
                        '-subj', '/CN=A733 native software fixture',
                        '-addext', 'subjectAltName=IP:127.0.0.1',
                        '-addext', 'basicConstraints=critical,CA:TRUE'],
                       check=True, capture_output=True, timeout=30)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(certificate, key)
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        started = time.monotonic()
        try:
            endpoint = f'https://127.0.0.1:{server.server_port}'
            receipt = ACQUIRE.acquire(endpoint + '/manifest.json', endpoint + '/nvme.img.gz',
                                      pin, output, 'external', certificate, max_seconds=3600)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
    ACQUIRE.require(receipt['success'] and receipt['encoded'] == encoded_record
                    and receipt['decoded'] == raw_record, 'acquisition receipt differs')
    ACQUIRE.require((Path(output) / 'manifest.json').read_bytes() == data,
                    'acquired manifest differs from source bytes')
    refused = False
    if not value.get('deployment_intent', {}).get('for_installation'):
        try:
            ACQUIRE.WRITER.validate_manifest(value)
        except ValueError:
            refused = True
        ACQUIRE.require(refused, 'fixture unexpectedly writer-eligible')
    return {'classification': 'native full-size loopback HTTPS acquisition; no board or deployment acceptance',
            'source_manifest': {'bytes': len(data), 'sha256': pin},
            'encoded': encoded_record, 'decoded': raw_record,
            'source_intent_present': 'deployment_intent' in value,
            'fixture_refused_by_writer': refused,
            'encoded_download_readback_full_bounded_expansion': 'passed',
            'elapsed_seconds': round(time.monotonic() - started, 3),
            'recipes': {str(path): ACQUIRE.WRITER.digest((REPO / path).read_bytes()) for path in
                        ('scripts/acquire-nvme.py', 'scripts/install-nvme.py', 'scripts/plan-nvme.py',
                         'scripts/nvme_layout.py', 'tests/verify_nvme_bundle_https.py')},
            'cleanup': 'temporary private certificate/key and loopback listener removed; acquired regular files retained',
            'limits': ['Trusted builder manifest is pinned before transfer; this test creates no release signature.',
                       'No expanded raw file, physical block-device write, mount or external request.',
                       'Native host storage guard exercised; ARM full-size acquisition and A733 networking untested.']}


if __name__ == '__main__':
    if len(sys.argv) != 3:
        sys.exit(f'Usage: {sys.argv[0]} TRUSTED_BUNDLE_DIRECTORY NEW_ABSOLUTE_OUTPUT_DIRECTORY')
    print(json.dumps(verify(sys.argv[1], sys.argv[2]), indent=2))
