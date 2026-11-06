#!/usr/bin/env python3
"""Export the separately derived timesync profile in a restricted namespace.

Source-only adapter: no ARM execution, clock setting or hardware acceptance.
Cleanup is destructive only to the launcher's disposable private root clone.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import types

WORK = Path('/a733-export-output')
ROOT = WORK / 'arm-root'
PROJECT = Path('/a733-sources/project')
MANIFEST = Path('/a733-sources/manifest.json')
SELF = 'build/export-timesync-userspace.py'
DERIVER = 'build/configure-timesync-userspace.py'
BACKEND = 'build/export-host-userspace.py'
DERIVER_PIN = {'bytes': 24229, 'sha256': 'fb1ebc6512317658982022df22cd0f349791b9d5145e6ad48694c97d775264a9'}
BACKEND_PIN = {'bytes': 28217, 'sha256': '1cd49563c43fe7ddd78d309ef2d9fe48b2ad1d1d29ee6d7050374db12be118ed'}
RECEIPT = WORK / 'timesync-receipt.json'
STATE = WORK / 'timesync-package-state.json'


def require(value, message):
    if not value:
        raise ValueError(message)


def signature(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def read_native(path, owner, limit=1048576, readonly=False):
    """Read a bounded, held native single-link file through physical parents."""
    require(path.is_absolute() and '..' not in path.parts, 'absolute path required')
    for parent in path.parents:
        require(stat.S_ISDIR(parent.lstat().st_mode), 'physical parents required')
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
            and before.st_uid == before.st_gid == owner and before.st_size <= limit,
            'native bounded single-link input required')
    if readonly:
        require(stat.S_IMODE(before.st_mode) == 0o444
                and os.statvfs(path).f_flag & os.ST_RDONLY, 'immutable source required')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        require(signature(before) == signature(os.fstat(fd)), 'input changed before read')
        data = stream.read(limit + 1)
        require(len(data) == before.st_size and signature(before) == signature(os.fstat(fd))
                == signature(path.lstat()), 'input changed during read')
    return data


def pin(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def private_module(name, path, data):
    """Compile authenticated bytes directly; never consult or create pycache."""
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(data, str(path), 'exec'), module.__dict__)
    return module


def bootstrap():
    require(os.sys.flags.isolated and os.sys.dont_write_bytecode, '-I -B required')
    require(Path(__file__).absolute() == PROJECT / SELF, 'fixed adapter source required')
    own = read_native(PROJECT / SELF, 65534, readonly=True)
    source = read_native(PROJECT / DERIVER, 65534, readonly=True)
    require(pin(source) == DERIVER_PIN, 'deriver source pin differs')
    d = private_module('timesync_deriver_private', PROJECT / DERIVER, source)
    records = d.sources()  # Closed project authentication precedes dependency imports.
    require(records.get(SELF) == pin(own), 'adapter absent from manifest or changed')
    require(read_native(PROJECT / SELF, 65534, readonly=True) == own, 'adapter source changed')
    modules = []
    for name, filename in (('backend', BACKEND), ('configure', 'build/configure-host-userspace.py'),
                           ('identity', 'build/timesync-identity.py')):
        data = d.read_checked(PROJECT / filename)
        require(pin(data) == records[filename], 'dependency source changed')
        if filename == BACKEND:
            require(pin(data) == BACKEND_PIN, 'backend source pin differs')
        modules.append(private_module('timesync_' + name + '_private', PROJECT / filename, data))
    return d, *modules, records


def authenticate_build_deb(contract):
    addition = contract['addition']
    require(pin(read_native(ROOT / 'run/a733-timesync.deb', 0))
            == {key: addition[key] for key in ('bytes', 'sha256')}, 'build DEB differs')


def live_pids():
    return {int(p.name) for p in Path('/proc').iterdir() if p.name.isdigit()}


def validate(d, c, identity, contract, expected_sha, initial=False):
    """Authenticate success and live stable state; safe both sides of cleanup."""
    require(isinstance(expected_sha, str) and re.fullmatch('[0-9a-f]{64}', expected_sha),
            'derivation receipt SHA256 required')
    data = read_native(RECEIPT, 0)
    require(pin(data)['sha256'] == expected_sha, 'derivation receipt authentication failed')
    receipt = json.loads(data)
    require(receipt.get('success') is True and type(receipt.get('configured_packages')) is int
            and receipt['configured_packages'] == 221 and all(receipt.get(key) is True for key in
            ('dpkg_audit_clean', 'apt_check_passed', 'accounts_preserved')), 'derivation receipt did not pass')
    require(receipt.get('inputs') == contract, 'derivation contract differs')
    require(receipt.get('source_manifest') == d.record(MANIFEST), 'derivation source manifest differs')
    _, expected, _ = d.validate_locks(contract)
    inventory = {name: list(value) for name, value in expected.items()}
    require(receipt.get('inventory') == inventory, 'derivation inventory differs')
    status = read_native(ROOT / 'var/lib/dpkg/status', 0, 16 * 1024**2)
    require(d.status_inventory(status) == expected, 'live package inventory differs')
    state_bytes = read_native(STATE, 0)
    require(pin(state_bytes) == receipt.get('package_state'), 'package state pin differs')
    state = json.loads(state_bytes)
    require(type(state.get('packages')) is int and state['packages'] == 221
            and state.get('inventory') == inventory and state.get('dpkg_status') == pin(status),
            'package state differs from live status')
    policy = contract['identity']['policy']
    policy_data = d.pinned(PROJECT / policy['path'], {k: policy[k] for k in ('bytes', 'sha256')})
    require(read_native(ROOT / 'etc/sysusers.d/systemd-timesync.conf', 0) == policy_data,
            'installed identity policy differs')
    require(identity.audit(ROOT, 'after') == receipt.get('identity_after')
            and identity.compare_accounts(WORK / 'identity-before', ROOT), 'identity changed')
    require(identity.audit(WORK / 'identity-before', 'before') == receipt.get('identity_before'),
            'identity baseline differs')
    require(d.validate_service(contract, c) == receipt.get('service_enablement'), 'service policy differs')
    require(not os.path.lexists(ROOT / 'var/lib/systemd/timesync'), 'unexpected clock state')
    live = live_pids()
    require(live <= {1, os.getpid()}, 'daemon/helper remains')
    if initial:
        authenticate_build_deb(contract)
        require(read_native(ROOT / 'usr/sbin/policy-rc.d', 0) == c.POLICY
                and read_native(ROOT / 'etc/systemd/system-preset/00-prototype.preset', 0) == c.PRESET,
                'build suppression changed')
    return data


def adapt(d, e, c, identity, records, expected_sha):
    """Only the declared profile constants and two backend hooks are replaced."""
    e.environment()
    require(not any(name.startswith(('LD_', 'PYTHON', 'FAKEROOT', 'FAKECHROOT', 'DEBOOTSTRAP'))
                    for name in os.environ), 'loader/Python/bootstrap overrides forbidden')
    contract = json.loads(d.pinned(PROJECT / d.CONTRACT, d.CONTRACT_PIN))
    original_manifest = d.record(MANIFEST)
    original_sources = records.copy()
    receipt_bytes = validate(d, c, identity, contract, expected_sha, initial=True)
    e.no_mounts(ROOT)
    lock_name = 'configs/host-timesync-packages.lock.tsv'
    e.LOCK = PROJECT / lock_name
    e.LOCK_SHA256 = contract['locks'][lock_name]['sha256']
    e.LOCK_BYTES = contract['locks'][lock_name]['bytes']
    e.PACKAGE_COUNT = 221
    e.RECEIPT = RECEIPT
    original_publish = e.publish
    captured = {}

    def authenticate(expected):
        require(expected == expected_sha, 'receipt argument changed')
        current = validate(d, c, identity, contract, expected_sha, initial=True)
        require(current == receipt_bytes, 'derivation receipt changed')
        return current

    def publish(staging, output):
        # Everything which can reject enrichment happens while output is private.
        require(validate(d, c, identity, contract, expected_sha) == receipt_bytes,
                'derivation receipt changed')
        require(d.sources() == original_sources and d.record(MANIFEST) == original_manifest,
                'authenticated sources changed')
        e.no_mounts(ROOT)
        report = json.loads(read_native(staging / 'receipt.json', 0))
        require(report.get('success') is True and report.get('configure_receipt_sha256') == expected_sha
                and report.get('inventory') == {'packages': 221, 'lock_sha256': e.LOCK_SHA256}
                and report.get('exporter_sha256') == BACKEND_PIN['sha256'], 'backend receipt differs')
        bom_bytes = read_native(staging / 'rootfs-bom.json', 0, 16 * 1024**2)
        bom = json.loads(bom_bytes)
        archive = staging / 'rootfs.tar.gz'
        with e.held(archive)[0] as stream:
            compressed = e.digest(stream)
        require(compressed == report['archive'] and e.verify_archive(archive, bom) == report['raw_tar']
                and len(bom) == report['members'], 'staged archive bindings differ')
        report.update(phase='final-timesync-userspace-export', profile='timesync-20261106',
                      derivation_receipt_sha256=expected_sha, bom=pin(bom_bytes),
                      provenance={'adapter': original_sources[SELF], 'backend': BACKEND_PIN,
                                  'deriver': DERIVER_PIN, 'contract': d.CONTRACT_PIN,
                                  'source_manifest': original_manifest})
        rewritten = (json.dumps(report, indent=2) + '\n').encode()
        # The staging receipt is already checked as a native single-link file.
        fd = os.open(staging / 'receipt.json', os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(rewritten)
            stream.flush()
            os.fsync(stream.fileno())
        fd = os.open(staging, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        captured.update(report)
        original_publish(staging, output)  # Last operation: no published mutation/readback.

    e.authenticate_receipt = authenticate
    e.publish = publish
    return captured


def export(output, expected_sha):
    require(output.parent == WORK and re.fullmatch(r'export-timesync-[A-Za-z0-9][A-Za-z0-9._-]*', output.name),
            'new WORK/export-timesync-NAME output required')
    require(not os.path.lexists(output), 'output already exists')
    d, e, c, identity, records = bootstrap()
    captured = adapt(d, e, c, identity, records, expected_sha)
    e.export(output, expected_sha)
    return captured


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--derivation-receipt-sha256', required=True)
    args = parser.parse_args()
    try:
        result = export(args.output, args.derivation_receipt_sha256)
    except (ValueError, OSError, KeyError, EOFError) as error:
        parser.exit(1, 'export-timesync-userspace: ' + str(error) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
