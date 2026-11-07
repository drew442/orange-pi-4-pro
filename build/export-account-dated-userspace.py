#!/usr/bin/env python3
"""Build-only historical replay; discard the private clone on any failure.

Authenticated original producers retain their safety gates and core attribution.
This adapter establishes exact userspace bytes, never hardware acceptance.
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
SELF = 'build/export-account-dated-userspace.py'
BACKEND = 'build/export-host-userspace.py'
TIME_ADAPTER = 'build/export-timesync-userspace.py'
HELPER = 'build/account-dates.py'
CONTRACT = 'configs/host-account-dates-replay.lock.json'
CONTRACT_PIN = {'bytes': 2655, 'sha256': '5859c325c938168b6c08612d5071be059d032c520ed017410f3a3941571190bb'}
REQUIRED = {SELF, CONTRACT, HELPER, BACKEND, 'configs/host-userspace-packages.lock.tsv',
            'configs/host-userspace-profiles.lock.json', 'build/launch-userspace-pipeline.py',
            'build/launch-account-dated-userspace.py'}


def require(value, message):
    if not value:
        raise ValueError(message)


def pin(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def signature(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def read_native(path, owner, limit=16 * 1024**2, readonly=False):
    require(path.is_absolute() and '..' not in path.parts, 'absolute physical path required')
    for parent in path.parents:
        require(stat.S_ISDIR(parent.lstat().st_mode), 'physical parents required')
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
            and before.st_uid == before.st_gid == owner and before.st_size <= limit,
            'native bounded single-link input required')
    if readonly:
        require(stat.S_IMODE(before.st_mode) == 0o444 and os.statvfs(path).f_flag & os.ST_RDONLY,
                'immutable source required')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        require(signature(before) == signature(os.fstat(fd)), 'input changed before read')
        data = stream.read(limit + 1)
        require(len(data) == before.st_size and signature(before) == signature(os.fstat(fd))
                == signature(path.lstat()), 'input changed during read')
    return data


def sources():
    """Authenticate the trusted Git driver's immutable, closed project snapshot."""
    data = read_native(MANIFEST, 65534, readonly=True)
    manifest = json.loads(data)
    require(set(manifest) == {'revision', 'files'} and isinstance(manifest['revision'], str)
            and re.fullmatch('[0-9a-f]{40}', manifest['revision'])
            and isinstance(manifest['files'], dict), 'invalid source manifest')
    require(REQUIRED <= manifest['files'].keys(), 'required sources missing')
    records, directories = {}, set()
    for name, item in manifest['files'].items():
        require(isinstance(name, str) and name and not name.startswith('/')
                and all(p not in ('', '.', '..', '__pycache__') for p in name.split('/'))
                and '\\' not in name and not name.endswith(('.pyc', '.pyo'))
                and not any(ord(c) < 32 or ord(c) == 127 for c in name), 'unsafe source name')
        require(isinstance(item, dict) and set(item) == {'bytes', 'sha256', 'git_blob'}
                and type(item['bytes']) is int and item['bytes'] >= 0
                and isinstance(item['sha256'], str) and re.fullmatch('[0-9a-f]{64}', item['sha256'])
                and isinstance(item['git_blob'], str) and re.fullmatch('[0-9a-f]{40}', item['git_blob']),
                'invalid source record')
        actual = read_native(PROJECT / name, 65534, readonly=True)
        require(pin(actual) == {k: item[k] for k in ('bytes', 'sha256')}, 'source pin differs')
        require(hashlib.sha1(b'blob ' + str(len(actual)).encode() + b'\0' + actual).hexdigest()
                == item['git_blob'], 'Git source blob differs')
        records[name] = pin(actual)
        directories.update(str(p) for p in Path(name).parents if p != Path('.'))
    actual_files, actual_dirs = set(), set()
    def walk(directory):
        info = directory.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == info.st_gid == 65534
                and not stat.S_IMODE(info.st_mode) & 0o222
                and os.statvfs(directory).f_flag & os.ST_RDONLY, 'immutable source directory required')
        for path in directory.iterdir():
            name = str(path.relative_to(PROJECT))
            if stat.S_ISDIR(path.lstat().st_mode):
                actual_dirs.add(name)
                walk(path)
            else:
                actual_files.add(name)
    walk(PROJECT)
    require(actual_files == set(records) and actual_dirs == directories, 'unlisted source nodes')
    require(read_native(MANIFEST, 65534, readonly=True) == data, 'source manifest changed')
    return records, pin(data)


def private_module(name, filename, records):
    data = read_native(PROJECT / filename, 65534, readonly=True)
    require(pin(data) == records[filename], 'module changed')
    module = types.ModuleType(name)
    module.__file__ = str(PROJECT / filename)
    exec(compile(data, module.__file__, 'exec'), module.__dict__)
    return module


def validate_contract(data):
    require(pin(data) == CONTRACT_PIN, 'replay contract pin differs')
    return json.loads(data)


def bootstrap(profile):
    require(os.sys.flags.isolated and os.sys.dont_write_bytecode, '-I -B required')
    require(Path(__file__).absolute() == PROJECT / SELF, 'fixed adapter source required')
    records, manifest = sources()
    contract = validate_contract(read_native(PROJECT / CONTRACT, 65534, readonly=True))
    for name, expected in contract['components'].items():
        # The timesync input contract is optional for the baseline dependency closure.
        if name in records:
            require(records[name] == expected, 'original component pin differs')
    require(records[BACKEND] == contract['components'][BACKEND]
            and records[HELPER] == contract['components'][HELPER], 'core/helper pin differs')
    helper = private_module('account_date_policy_private', HELPER, records)
    e = private_module('account_date_backend_private', BACKEND, records)
    time_adapter = None
    time_arguments = None
    if profile == 'time':
        require(records[TIME_ADAPTER] == contract['components'][TIME_ADAPTER], 'time adapter pin differs')
        time_adapter = private_module('account_date_time_adapter_private', TIME_ADAPTER, records)
        d, e, c, identity, original_records = time_adapter.bootstrap()
        require(original_records == records, 'time source authentication differs')
        time_arguments = (d, c, identity, original_records)
    return e, helper, contract, records, manifest, time_adapter, time_arguments


def install_hooks(e, helper, contract, records, manifest, expected_sha, profile):
    """Wrap cleanup/publication after the original profile hooks are installed."""
    old_cleanup, old_publish = e.cleanup, e.publish
    captured, transformation = {}, {}
    def cleanup():
        facts = old_cleanup()  # Core preflight scan/receipt/inventory already passed.
        before = {n: pin(e.read_checked(ROOT / 'etc' / n)) for n in ('shadow', 'shadow-')}
        changed = helper.normalize(ROOT, profile)
        after = {n: pin(e.read_checked(ROOT / 'etc' / n)) for n in ('shadow', 'shadow-')}
        transformation.update(changed_basenames=list(changed), before=before, after=after)
        return facts
    def publish(staging, output):
        require(transformation, 'normalization hook did not complete')
        current, current_manifest = sources()
        require(current == records and current_manifest == manifest, 'replay sources changed')
        report = json.loads(read_native(staging / 'receipt.json', 0))
        bom_bytes = read_native(staging / 'rootfs-bom.json', 0)
        bom = json.loads(bom_bytes)
        with e.held(staging / 'rootfs.tar.gz')[0] as stream:
            archive = e.digest(stream)
        raw = e.verify_archive(staging / 'rootfs.tar.gz', bom)
        expected = contract['profiles'][profile]
        require(report.get('success') is True and report.get('configure_receipt_sha256') == expected_sha
                and report.get('exporter_sha256') == contract['components'][BACKEND]['sha256'],
                'core receipt attribution differs')
        require(archive == report.get('archive') == expected['archive']
                and raw == report.get('raw_tar') == expected['raw_tar']
                and pin(bom_bytes) == expected['bom']
                and len(bom) == report.get('members') == expected['members']
                and report.get('inventory') == expected['inventory']
                and report.get('timestamps', {}).get('epoch') == expected['epoch'],
                'historical replay bytes differ; discard clone')
        report['bom'] = pin(bom_bytes)
        report['account_date_replay'] = dict(schema=1, profile=profile, adapter=records[SELF],
            helper=records[HELPER], contract=CONTRACT_PIN, source_manifest=manifest,
            input_receipt_sha256=expected_sha, **transformation)
        fd = os.open(staging / 'receipt.json', os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW)
        with os.fdopen(fd, 'wb') as stream:
            stream.write((json.dumps(report, indent=2) + '\n').encode())
            stream.flush()
            os.fsync(stream.fileno())
        captured.update(report)
        old_publish(staging, output)
    e.cleanup, e.publish = cleanup, publish
    return captured


def export(output: Path, expected_sha: str, profile='baseline'):
    require(profile in ('baseline', 'time'), 'unknown replay profile')
    require(output.parent == WORK and re.fullmatch(r'export-[A-Za-z0-9][A-Za-z0-9._-]*', output.name)
            and not os.path.lexists(output), 'new WORK/export-NAME required')
    e, helper, contract, records, manifest, t, arguments = bootstrap(profile)
    e.environment()  # Before any hook capable of normalizing a tree.
    time_report = None
    if t is not None:
        d, c, identity, original_records = arguments
        time_report = t.adapt(d, e, c, identity, original_records, expected_sha)
    captured = install_hooks(e, helper, contract, records, manifest, expected_sha, profile)
    e.export(output, expected_sha)  # Failures propagate; never retry or reuse clone.
    return time_report if time_report is not None else captured


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--configure-receipt-sha256', required=True)
    parser.add_argument('--profile', choices=('baseline', 'time'), default='baseline')
    args = parser.parse_args()
    try:
        print(json.dumps(export(args.output, args.configure_receipt_sha256, args.profile), indent=2))
    except (ValueError, OSError, KeyError, EOFError) as error:
        parser.exit(1, 'export-account-dated-userspace: discard private clone: ' + str(error) + '\n')


if __name__ == '__main__':
    main()
