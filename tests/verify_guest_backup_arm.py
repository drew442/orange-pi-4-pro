#!/usr/bin/env python3
"""Opt-in ARM backup stream/native receiver fixture; no guest restore or KVM."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, REPO / path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


TAIL = r'''
spec=importlib.util.spec_from_file_location('backup','/source/scripts/backup-guest.py')
B=importlib.util.module_from_spec(spec);spec.loader.exec_module(B)
unsupported=subprocess.run(['/usr/bin/python3','-I','-B','/source/scripts/backup-guest.py','fixture','--manifest-sha256',new_pin],capture_output=True,timeout=30)
assert unsupported.returncode!=0 and unsupported.stdout==b''
# Reuse only the disposable fixture's explicit live-boundary substitutions.
B.POLICY=P;B.ENROLL=P.ENROLL
stream_path=pathlib.Path('/var/lib/guest-backup-stream.tar.gz')
with stream_path.open('xb') as output:
    backup=B.send('fixture',new_pin,output)
    output.flush();os.fsync(output.fileno())
stream_path.chmod(0o400)
assert backup['success'] and backup['history_count']==1
assert backup['archive']['bytes']==stream_path.stat().st_size
assert backup['archive']['sha256']==hashlib.sha256(stream_path.read_bytes()).hexdigest()
assert pathlib.Path(paths['enrollment']).read_bytes()==new_data
assert hashlib.sha256(pathlib.Path(paths['disk']).read_bytes()).hexdigest()==current_disk
assert len(list((parent/'.checkpoints').iterdir()))==1
print(json.dumps({'architecture':platform.machine(),'backup':backup,'real_sender_CLI_refusal_before_stream':True,'source_manifest_disk_history_preserved':True,'checkpoint':checkpoint,'live_boundaries':'mount/UUID/namespace/service/consumer and checkpoint approval mocked only inside disposable fixture','guest_execution':'not performed','clean_shutdown':'fixture attestation only, not verified'}))
'''


def followup(root, checks):
    receiver = module('backup_arm_native_receiver', 'scripts/verify-guest-backup.py')
    archive = root / 'var/lib/guest-backup-stream.tar.gz'
    pin = checks['backup']['archive']['sha256']
    manifest_pin = checks['backup']['manifest_sha256']
    bad_output = root.parent / 'bad-receive'
    # Authentication must precede every gzip, tar, manifest and history parser.
    with patch.object(receiver, 'extract', side_effect=AssertionError('parser reached')) as parser:
        bad = receiver.verify(archive, '0' * 64, manifest_pin, str(bad_output))
        assert not bad['success'] and not bad['published'] and not parser.called, bad
    assert not bad_output.exists()
    output = root.parent / 'received'
    result = receiver.verify(archive, pin, manifest_pin, str(output))
    assert result['success'] and result['published'] and result['durable'], result
    source = root / 'run/a733-guests/fixture'
    for name in ('Image', 'initramfs.cpio.gz', 'disk.raw', 'enrollment.json'):
        assert hashlib.sha256((source / name).read_bytes()).digest() == hashlib.sha256((output / name).read_bytes()).digest()
    history = {p.name: p.read_bytes() for p in (source / '.checkpoints').iterdir()}
    assert history == {p.name: p.read_bytes() for p in (output / '.checkpoints').iterdir()}
    assert result['source_history_records'] == 1
    for path in (output / 'Image', output / 'initramfs.cpio.gz', output / 'disk.raw', output / 'enrollment.json'):
        info = path.stat()
        assert info.st_uid == os.geteuid() and info.st_nlink == 1 and info.st_mode & 0o7777 == 0o400
    again = receiver.verify(archive, pin, manifest_pin, str(output))
    assert not again['success'] and not again['published']
    assert not list(root.parent.glob('.guest-backup-*'))
    return {'receipt': result, 'wrong_encoded_pin_refused_before_parsing': True,
            'full_source_and_received_payloads_match': True, 'exact_history_bytes_match': True,
            'existing_output_refused': True, 'private_stages_cleaned': True,
            'storage_boundary': 'actual native builder fixed-storage check; no substitution',
            'classification': 'same-builder artifact verification; not off-device backup or guest restoration'}


def verify(rootfs, guest_directory):
    enrollment = module('backup_arm_enrollment', 'tests/verify_guest_enrollment_arm.py')
    checkpoint = module('backup_arm_checkpoint', 'tests/verify_guest_checkpoint_arm.py')
    enrollment_marker = "print(json.dumps({'architecture':platform.machine(),'actual_fixture_inputs'"
    checkpoint_marker = "print(json.dumps({'architecture':platform.machine(),'initial_enrollment'"
    if enrollment.PROBE.count(enrollment_marker) != 1 or checkpoint.TAIL.count(checkpoint_marker) != 1:
        raise ValueError('trusted probe interface changed')
    probe = enrollment.PROBE.split(enrollment_marker, 1)[0] + checkpoint.TAIL.split(checkpoint_marker, 1)[0] + TAIL
    compile(probe, '/backup-probe.py', 'exec')
    sources = enrollment.SOURCES + ('configs/guest_manifest.py', 'scripts/guest-checkpoint-policy.py',
        'scripts/checkpoint-guest.py', 'configs/guest_backup.py', 'scripts/backup-guest.py')
    result = enrollment.verify(rootfs, guest_directory, probe=probe, sources=sources, after_probe=followup)
    result['classification'] = 'ARM sender/native receiver full-file backup verification; no restored VM or KVM'
    result['backup_verifier_source'] = {'bytes': Path(__file__).stat().st_size,
        'sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    result['receiver_sources'] = {name: {'bytes': (REPO / name).stat().st_size,
        'sha256': hashlib.sha256((REPO / name).read_bytes()).hexdigest()}
        for name in ('scripts/verify-guest-backup.py', 'configs/guest_backup.py')}
    result['combined_probe'] = {'bytes': len(probe.encode()), 'sha256': hashlib.sha256(probe.encode()).hexdigest()}
    result['limits'] = [
        'Source live mount/UUID/namespace/stopped-service/consumer/approval boundaries mocked in disposable fixture.',
        'Actual ARM source hashes, checkpoint, compression, native capture, encoded authentication and full sparse-file readback execute.',
        'Both ends use the same builder; no off-device storage, clean guest shutdown, restoration, KVM or physical acceptance.',
        'Configured binfmt interpreter pathname/hash checked; kernel-held F descriptor not independently inspected.']
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('rootfs_tar', type=Path)
    parser.add_argument('guest_directory', type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.rootfs_tar, args.guest_directory), indent=2))
