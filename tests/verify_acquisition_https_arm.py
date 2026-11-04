#!/usr/bin/env python3
"""Exercise packaged ARM HTTPS code against a temporary loopback TLS fixture.

No mounts, physical devices, external requests or board-network acceptance.
"""
import gzip
import hashlib
import http.server
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import ssl
import stat
import subprocess
import sys
import tempfile
import threading


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


REPO = Path(__file__).resolve().parents[1]
ARCHIVE = module('https_arm_archive', Path(__file__).with_name('verify_installer_runtime_arm.py'))
STATE = module('https_arm_storage', REPO / 'build/host-state.py')


def verify(directory):
    if os.geteuid() != 0:
        raise ValueError('root required for private ARM chroot')
    STATE.validate_workspace_storage(Path('/var/tmp'))
    space = os.statvfs('/var/tmp')
    if space.f_bavail * space.f_frsize < 1024**3:
        raise ValueError('at least 1 GiB free fixed storage required')
    directory = Path(directory)
    manifest = json.loads(ARCHIVE.regular(directory / 'manifest.json', 4 * 1024**2))
    packed = ARCHIVE.regular(directory / 'initramfs.cpio.gz', 64 * 1024**2)
    if ARCHIVE.record(packed) != manifest['files']['initramfs.cpio.gz']:
        raise ValueError('archive differs from trusted manifest')
    import io
    with gzip.GzipFile(fileobj=io.BytesIO(packed)) as source:
        raw = source.read(256 * 1024**2 + 1)
    if len(raw) > 256 * 1024**2 or len(raw) != manifest['expanded_bytes']:
        raise ValueError('expanded archive budget differs')
    entries = ARCHIVE.decode(raw)
    if {name: dict(mode=mode, **ARCHIVE.record(data)) for name, (mode, data) in entries.items()} != manifest['entries']:
        raise ValueError('archive entries differ from trusted manifest')
    for name in ('acquire-nvme.py', 'install-nvme.py', 'plan-nvme.py', 'nvme_layout.py'):
        if entries['usr/lib/a733-installer/' + name][1] != (REPO / 'scripts' / name).read_bytes():
            raise ValueError('packaged source differs')
    payload = bytes(range(256)) * 257
    encoded = gzip.compress(payload, mtime=0)
    payload_record, encoded_record = ARCHIVE.record(payload), ARCHIVE.record(encoded)

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/redirect':
                self.send_response(302)
                self.send_header('Location', '/payload')
                self.end_headers()
                return
            self.send_response(200)
            self.send_header('Content-Length', str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *args):
            pass

    with tempfile.TemporaryDirectory(prefix='a733-acquisition-arm-', dir='/var/tmp') as temporary:
        temporary = Path(temporary)
        root = temporary / 'root'
        root.mkdir(mode=0o700)
        for name, (mode, content) in sorted(entries.items(), key=lambda item: (len(PurePosixPath(item[0]).parts), item[0])):
            target = root / name
            if stat.S_ISDIR(mode):
                target.mkdir(exist_ok=True)
                target.chmod(stat.S_IMODE(mode))
            elif stat.S_ISREG(mode):
                with target.open('xb') as output:
                    output.write(content)
                target.chmod(stat.S_IMODE(mode))
            else:
                target.symlink_to(content.decode())
        for name, minor in (('null', 3), ('zero', 5), ('urandom', 9)):
            os.mknod(root / 'dev' / name, stat.S_IFCHR | 0o666, os.makedev(1, minor))
        certificate, key = temporary / 'certificate.pem', temporary / 'key.pem'
        subprocess.run(['/usr/bin/openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                        '-keyout', str(key), '-out', str(certificate), '-days', '1',
                        '-subj', '/CN=A733 software fixture',
                        '-addext', 'subjectAltName=IP:127.0.0.1',
                        '-addext', 'basicConstraints=critical,CA:TRUE'],
                       check=True, capture_output=True, timeout=30)
        (root / 'fixture-ca.pem').write_bytes(certificate.read_bytes())
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(certificate, key)
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            parameters = {'url': f'https://127.0.0.1:{server.server_port}',
                          'encoded': encoded_record, 'decoded': payload_record}
            (root / 'parameters.json').write_text(json.dumps(parameters))
            program = r'''import gzip,importlib.util,io,json,pathlib,platform,ssl,time,urllib.request
assert platform.machine()=='aarch64'
spec=importlib.util.spec_from_file_location('arm_acquisition','/usr/lib/a733-installer/acquire-nvme.py')
acq=importlib.util.module_from_spec(spec);spec.loader.exec_module(acq)
params=json.loads(pathlib.Path('/parameters.json').read_text())
def opener(context):
    return urllib.request.build_opener(urllib.request.ProxyHandler({}),acq.NoRedirect(),urllib.request.HTTPSHandler(context=context))
context=acq.tls_context('/fixture-ca.pem')
assert context.verify_mode==ssl.CERT_REQUIRED and context.check_hostname
assert context.minimum_version>=ssl.TLSVersion.TLSv1_2
transport=opener(context)
encoded=io.BytesIO()
record=acq.fetch(transport,params['url']+'/payload',encoded,params['encoded']['bytes'],time.monotonic()+30,params['encoded'])
assert record==params['encoded']
encoded.seek(0);acq.verify_stream(encoded,params['encoded'],time.monotonic()+30)
encoded.seek(0)
with gzip.GzipFile(fileobj=encoded,mode='rb') as stream:
    acq.verify_stream(stream,params['decoded'],time.monotonic()+30)
for client,path in ((opener(acq.tls_context()),'/payload'),(transport,'/redirect')):
    try:acq.fetch(client,params['url']+path,io.BytesIO(),params['encoded']['bytes'],time.monotonic()+30)
    except ValueError:pass
    else:raise AssertionError('untrusted certificate or redirect accepted')
print(json.dumps({'architecture':platform.machine(),'HTTPS_Content_Length_fetch':'passed','custom_CA_hostname_verification':'passed','untrusted_CA_refusal':'passed','redirect_refusal':'passed','encoded_and_expanded_verification':'passed','bytes':params,'scope':'production transport and stream verifier; no full acquisition/storage policy or device operation'}))
'''
            (root / 'probe.py').write_text(program)
            result = subprocess.run(['chroot', str(root), '/usr/bin/python3', '-I', '-B', '/probe.py'],
                                    capture_output=True, text=True, timeout=120,
                                    env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'})
            if result.returncode:
                raise ValueError('private ARM HTTPS probe failed: ' + result.stderr)
            report = json.loads(result.stdout)
            # Retain byte records without an incidental ephemeral endpoint.
            report['bytes'].pop('url')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
    return {'classification': 'ARM64 software TLS loopback probe; no board networking or hardware acceptance',
            'verifier_source': ARCHIVE.record(Path(__file__).read_bytes()),
            'archive': ARCHIVE.record(packed), 'ARM_checks': report,
            'cleanup': 'temporary chroot, certificate/private key and loopback listener removed; no mounts or external requests',
            'limits': ['No real DNS, DHCP, Ethernet, production endpoint or A733 TLS connection.',
                       'Full acquisition/storage guards have native mocked/loopback tests; this ARM probe exercises transport and stream checks.',
                       'No SD/NVMe/SPI access or actual KVM guest.']}


if __name__ == '__main__':
    if len(sys.argv) != 2:
        sys.exit(f'Usage: sudo {sys.argv[0]} TRUSTED_INSTALLER_INITRAMFS_DIRECTORY')
    print(json.dumps(verify(sys.argv[1]), indent=2))
