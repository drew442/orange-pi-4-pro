#!/usr/bin/env python3
"""ARM HTTPS guest file restore on loopback; no board network, VM or KVM proof."""
import argparse
import contextlib
import hashlib
import http.server
import importlib.util
import json
from pathlib import Path
import ssl
import subprocess
import threading

REPO = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, REPO / path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


TAIL = r'''
spec=importlib.util.spec_from_file_location('chunks','/source/configs/guest_backup_chunks.py')
K=importlib.util.module_from_spec(spec);spec.loader.exec_module(K)
encoded=stream_path.read_bytes()
index=K.make(backup['archive'],new_pin,[hashlib.sha256(encoded[n:n+K.CHUNK]).hexdigest() for n in range(0,len(encoded),K.CHUNK)])
index_path=pathlib.Path('/var/lib/guest-backup-chunks.json');index_path.write_bytes(index);index_path.chmod(0o400)
params=json.loads(pathlib.Path('/run/https-parameters.json').read_bytes())
url=params['url'];index_pin=hashlib.sha256(index).hexdigest()
remote_root=pathlib.Path('/run/a733-https-guests');remote_root.mkdir(mode=0o711);remote_root.chmod(0o711)
T.ENROLL.ROOT=str(remote_root)
remote_args=[None,backup['archive']['sha256'],new_pin,destination_uuid]
options={'archive_url':url+'/archive.gz','chunk_manifest_url':url+'/chunks.json','chunk_manifest_sha256':index_pin,'ca_file':'/run/backup-ca.pem','network_timeout_seconds':30}
remote_plan=T.prepare(*remote_args,**options)
assert not list(remote_root.iterdir()) and 'path' not in remote_plan['archive']
remote_plan_path=pathlib.Path('/run/https-restore-plan.json');remote_plan_path.write_bytes(T.canonical(remote_plan));remote_plan_path.chmod(0o400)
remote=T.apply(*remote_args,str(remote_plan_path),**options)
assert remote['success'] and remote['published'] and remote['durable'],remote
remote_target=remote_root/'fixture'
for name in ('Image','initramfs.cpio.gz','disk.raw','enrollment.json'):
    assert (remote_target/name).read_bytes()==(target/name).read_bytes()
assert {p.name:p.read_bytes() for p in (remote_target/'.checkpoints').iterdir()}==source_history
assert remote['manifest_sha256']==restored_pin
assert not list((remote_root/'.enrollment').iterdir())
assert set(p.name for p in remote_target.iterdir())=={'Image','initramfs.cpio.gz','disk.raw','enrollment.json','.checkpoints'}
bad_options=dict(options,chunk_manifest_sha256='0'*64)
try:T.prepare(*remote_args,**bad_options)
except ValueError:pass
else:raise AssertionError('bad index pin accepted')
print(json.dumps({'architecture':platform.machine(),'source_archive':backup['archive'],'chunk_index':{'bytes':len(index),'sha256':index_pin,'chunk_count':len(K.validate(index)['chunks'])},'local_restore':restored,'https_restore':remote,'prepare_no_destination_writes':True,'remote_payloads_history_and_manifest_match_local_restore':True,'wrong_index_pin_refused':True,'private_staging_empty':True,'local_archive_staging_by_remote_reader':False,'TLS':'real loopback TLS with verified certificate/hostname and custom protected CA; HTTP transport not mocked','live_boundaries':'NVMe/namespace/UUID/service/UID census/approval substituted only inside disposable fixture','guest_execution':'not performed','board_network':'not tested'}))
'''


@contextlib.contextmanager
def transport(root):
    certificate, key = root.parent / 'server.pem', root.parent / 'server-key.pem'
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
        '-keyout', str(key), '-out', str(certificate), '-days', '1', '-subj', '/CN=fixture',
        '-addext', 'subjectAltName=IP:127.0.0.1', '-addext', 'basicConstraints=critical,CA:TRUE'],
        check=True, capture_output=True, timeout=30)
    ca = root / 'run/backup-ca.pem'
    ca.write_bytes(certificate.read_bytes())
    ca.chmod(0o400)

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            paths = {'/archive.gz': 'guest-backup-stream.tar.gz', '/chunks.json': 'guest-backup-chunks.json'}
            if self.path not in paths:
                self.send_error(404)
                return
            data = (root / 'var/lib' / paths[self.path]).read_bytes()
            self.send_response(200)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
                pass

        def log_message(self, *args):
            pass

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate, key)
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    (root / 'run/https-parameters.json').write_text(json.dumps({'url': f'https://127.0.0.1:{server.server_port}'}))
    try:
        yield
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def native_index(root, checks):
    indexer = module('https_arm_native_indexer', 'scripts/index-guest-backup.py')
    source = root / 'var/lib/guest-backup-stream.tar.gz'
    output = root.parent / 'native-chunks.json'
    result = indexer.index(str(source), checks['source_archive']['sha256'],
        checks['https_restore']['source_manifest_sha256'], str(output))
    assert result['success'] and result['published'] and result['durable'], result
    assert output.read_bytes() == (root / 'var/lib/guest-backup-chunks.json').read_bytes()
    return {'receipt': result, 'matches_ARM_index': True,
            'storage_checks': 'actual native fixed storage, no substitutions',
            'classification': 'chunk index publication only, no VM or off-device retention'}


def verify(rootfs, guest_directory):
    enrollment = module('https_arm_enrollment', 'tests/verify_guest_enrollment_arm.py')
    checkpoint = module('https_arm_checkpoint', 'tests/verify_guest_checkpoint_arm.py')
    backup = module('https_arm_backup', 'tests/verify_guest_backup_arm.py')
    restore = module('https_arm_restore', 'tests/verify_guest_restore_arm.py')
    markers = (
        (enrollment.PROBE, "print(json.dumps({'architecture':platform.machine(),'actual_fixture_inputs'"),
        (checkpoint.TAIL, "print(json.dumps({'architecture':platform.machine(),'initial_enrollment'"),
        (backup.TAIL, "print(json.dumps({'architecture':platform.machine(),'backup'"),
        (restore.TAIL, "print(json.dumps({'architecture':platform.machine(),'source_backup'"))
    if any(source.count(marker) != 1 for source, marker in markers):
        raise ValueError('trusted probe interface changed')
    probe = ''.join(source.split(marker, 1)[0] for source, marker in markers) + TAIL
    compile(probe, '/https-restore-probe.py', 'exec')
    # Complete runtime import closure, including existing HTTPS deadline helpers.
    sources = enrollment.SOURCES + ('configs/guest_manifest.py', 'scripts/guest-checkpoint-policy.py',
        'scripts/checkpoint-guest.py', 'configs/guest_backup.py', 'scripts/backup-guest.py',
        'scripts/guest-restore-source.py', 'scripts/restore-guest.py', 'scripts/verify-guest-backup.py',
        'build/host-state.py', 'build/host-payload.py', 'build/root-selection.py',
        'build/host-initramfs.py', 'build/host-storage-tools.py', 'configs/guest_backup_chunks.py',
        'scripts/guest-restore-https.py', 'scripts/acquire-nvme.py', 'scripts/install-nvme.py',
        'scripts/plan-nvme.py', 'scripts/nvme_layout.py')
    result = enrollment.verify(rootfs, guest_directory, probe=probe, sources=sources,
                               probe_context=transport, after_probe=native_index)
    result['classification'] = 'ARM HTTPS guest file restore; same-builder loopback, no restored VM or KVM'
    result['https_restore_verifier_source'] = {'bytes': Path(__file__).stat().st_size,
        'sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    result['combined_probe'] = {'bytes': len(probe.encode()), 'sha256': hashlib.sha256(probe.encode()).hexdigest()}
    result['native_indexer_source'] = {'bytes': (REPO / 'scripts/index-guest-backup.py').stat().st_size,
        'sha256': hashlib.sha256((REPO / 'scripts/index-guest-backup.py').read_bytes()).hexdigest()}
    result['limits'] = [
        'Actual pinned ARM userspace, archive generation, HTTPS, TLS certificate/hostname checks, chunk verification and full copy/readback execute.',
        'NVMe topology/UUID/namespace/service/UID census/TTY boundaries substituted only inside disposable fixture.',
        'Fixture server reads a backup generated on the same builder; remote reader does not stage an archive, but off-device retention and board networking are untested.',
        'No guest VM/KVM/board boot/physical restoration; configured binfmt interpreter hash checked, kernel-held F descriptor not independently inspected.']
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('rootfs_tar', type=Path)
    parser.add_argument('guest_directory', type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.rootfs_tar, args.guest_directory), indent=2))
