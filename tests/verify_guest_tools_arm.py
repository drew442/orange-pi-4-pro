#!/usr/bin/env python3
"""Authenticate a portable tool tree, then exercise it at its installed ARM path."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, REPO / path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def verify(package, package_pin, rootfs, guest_directory, *, loopback_network=False, fixture_profile='legacy'):
    tools = module('installed_arm_packager', 'build/maintenance-tools.py')
    maintenance = module('installed_arm_authentication', 'tests/verify_maintenance_arm.py')
    enrollment = module('installed_arm_enrollment', 'tests/verify_guest_enrollment_arm.py')
    checkpoint = module('installed_arm_checkpoint', 'tests/verify_guest_checkpoint_arm.py')
    backup = module('installed_arm_backup', 'tests/verify_guest_backup_arm.py')
    restore = module('installed_arm_restore', 'tests/verify_guest_restore_arm.py')
    https = module('installed_arm_https', 'tests/verify_guest_restore_https_arm.py')
    package_data, manifest = maintenance.authenticate(Path(package) / 'manifest.json', package_pin)
    expected = tools.entries(REPO)
    maintenance.validate_tree(Path(package) / 'tree', expected)
    records = {name: {'mode': mode, **tools.record(data)} for name, (mode, data) in expected.items()}
    if manifest.get('files') != records:
        raise ValueError('package metadata must match complete trusted checkout')
    metadata = json.loads(expected['package.json'][1])
    for key in ('schema', 'profile', 'kernel_release', 'entrypoints', 'uid', 'gid', 'source_records'):
        if manifest.get(key) != metadata[key]:
            raise ValueError('outer package metadata differs from authenticated entries')
    packed = maintenance.regular(Path(package) / 'tools.tar.gz', 8 * 1024**2)
    if packed != tools.archive(expected) or manifest.get('artifacts') != {'tools.tar.gz': tools.record(packed)}:
        raise ValueError('portable archive differs from authenticated exact entries')
    markers = (
        (enrollment.PROBE, "print(json.dumps({'architecture':platform.machine(),'actual_fixture_inputs'"),
        (checkpoint.TAIL, "print(json.dumps({'architecture':platform.machine(),'initial_enrollment'"),
        (backup.TAIL, "print(json.dumps({'architecture':platform.machine(),'backup'"),
        (restore.TAIL, "print(json.dumps({'architecture':platform.machine(),'source_backup'"))
    if any(text.count(marker) != 1 for text, marker in markers):
        raise ValueError('trusted probe interface changed')
    probe = ''.join(text.split(marker, 1)[0] for text, marker in markers) + https.TAIL
    if loopback_network:
        config_marker = "config_path=pathlib.Path('/run/enrollment-config.json')"
        final_marker = "print(json.dumps({'architecture':platform.machine(),'source_archive'"
        if probe.count(config_marker) != 1 or probe.count(final_marker) != 1:
            raise ValueError('trusted network probe interface changed')
        probe = probe.replace(config_marker,
            "config['network']={'mode':'isolated','host_tcp_port':18080,'guest_tcp_port':8080}\n" + config_marker)
        checks = r'''
assert restored_manifest['config']['network']==config['network']
assert '--host-tcp-port 18080 --guest-tcp-port 8080' in restored_unit
forwarded=subprocess.run(launch_args+['--host-tcp-port','18080','--guest-tcp-port','8080'],capture_output=True,text=True,timeout=60)
assert forwarded.returncode==0,forwarded.stderr
report=json.loads(forwarded.stdout)
assert report['argv'][report['argv'].index('-netdev')+1]=='user,id=guestnet,restrict=on,ipv6=off,hostfwd=tcp:127.0.0.1:18080-10.0.2.15:8080'
assert report['hardware_acceptance']=='pending'
'''
        probe = probe.replace(final_marker, checks + final_marker)
    probe = probe.replace('/source/', '/usr/lib/a733-tools/')
    compile(probe, '/installed-tools-probe.py', 'exec')
    # Bytes are read from the authenticated portable tree, rather than a checkout
    # inside the chroot; exact namespace and archive were checked above.
    entries = {name: (mode, maintenance.regular(Path(package) / 'tree' / name))
               for name, (mode, _data) in expected.items()}
    if entries != expected:
        raise ValueError('captured package changed before ARM staging')
    result = enrollment.verify(rootfs, guest_directory, probe=probe, probe_context=https.transport,
                               source_entries=entries, source_prefix='/usr/lib/a733-tools',
                               fixture_profile=fixture_profile)
    result['classification'] = 'portable guest tools at installed ARM path; no systemd boot or KVM proof'
    result['package_manifest'] = tools.record(package_data)
    result['package_archive'] = tools.record(packed)
    result['installed_prefix'] = '/usr/lib/a733-tools'
    result['entrypoints'] = manifest['entrypoints']
    result['loopback_network_profile'] = ('preserved through enrollment/checkpoint/backup/restoration; unit and actual ARM launcher dry-run verified'
                                          if loopback_network else 'not requested')
    result['packaged_verifier_source'] = tools.record(Path(__file__).read_bytes())
    result['combined_probe'] = tools.record(probe.encode())
    result['limits'] = [
        'Private authenticated ARM userspace receives only the closed portable tool tree at the installed path; no repository source tree is staged.',
        'Actual sysusers, enrollment/checkpoint/backup/local and HTTPS restoration, file readback and unit parsing run; live hardware policy boundaries substituted only inside fixture.',
        'No service/guest started, no board networking/NVMe/SD-write or KVM acceptance; configured interpreter path hash checked, kernel-held F descriptor not independently inspected.']
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package_directory', type=Path)
    parser.add_argument('--package-manifest-sha256', required=True)
    parser.add_argument('--loopback-network', action='store_true')
    parser.add_argument('--fixture-profile', choices=('legacy', 'fixed-seed-http'), default='legacy')
    parser.add_argument('rootfs_tar', type=Path)
    parser.add_argument('guest_directory', type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.package_directory, args.package_manifest_sha256,
                           args.rootfs_tar, args.guest_directory,
                           loopback_network=args.loopback_network,
                           fixture_profile=args.fixture_profile), indent=2))
