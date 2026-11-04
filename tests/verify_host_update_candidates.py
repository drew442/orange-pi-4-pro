"""Independently inspect regular-file candidate pairs; never mount or write."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

if sys.flags.optimize:
    raise SystemExit('verification requires Python assertions enabled')
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('candidate_base', type=Path)
parser.add_argument('payload_directory', type=Path)
parser.add_argument('reference_selector_directory', type=Path)
parser.add_argument('--manifest-sha256', required=True)
args = parser.parse_args()
base = args.candidate_base
payload = args.payload_directory
selection = args.reference_selector_directory
manifest_pin = args.manifest_sha256

def read(path, limit=None, private=False):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        before = os.fstat(fd)
        assert stat.S_ISREG(before.st_mode)
        maximum = limit if limit is not None else 4294967296
        assert before.st_size <= maximum
        if private:
            assert stat.S_IMODE(before.st_mode) == 0o400
            assert before.st_uid == before.st_gid == 0 and before.st_nlink == 1
        digest = hashlib.sha256()
        size = 0
        collected = bytearray() if limit is not None else None
        for data in iter(lambda: source.read(1024 * 1024), b''):
            size += len(data)
            assert size <= maximum
            if limit is not None:
                assert size <= limit
                collected.extend(data)
            digest.update(data)
        after = os.fstat(fd)
        assert (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns) == (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns)
        return {'bytes': size, 'sha256': digest.hexdigest()}, bytes(collected) if collected is not None else None

reports = {}
original_manifest, original_data = read(payload / 'manifest.json', 1024**2)
assert original_manifest['sha256'] == manifest_pin
trusted = json.loads(original_data)
assert trusted['board'] == 'Orange Pi 4 Pro A733' and trusted['schema'] == 1
assert trusted['slot_bytes'] == 4294967296
assert trusted['kernel_release'] == '6.6.98-a733-diag'
for slot in ('A', 'B'):
    directory = base / f'candidate-{slot}'
    info = directory.lstat()
    assert stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o700
    assert info.st_uid == info.st_gid == 0
    assert {p.name for p in directory.iterdir()} == {'candidate.json','manifest.json','root.ext4','selected.conf'}
    records, data = {}, {}
    for name, bound in (('manifest.json',1024**2),('selected.conf',4096),('candidate.json',1024**2),('root.ext4',None)):
        records[name], data[name] = read(directory / name, bound, True)
    assert records['manifest.json'] == original_manifest and data['manifest.json'] == original_data
    manifest = json.loads(data['manifest.json'])
    expected = trusted['files'][f'root-{slot}.ext4']
    assert expected['bytes'] == 4294967296
    assert records['root.ext4'] == expected == manifest['files'][f'root-{slot}.ext4']
    original_record, _ = read(payload / f'root-{slot}.ext4')
    assert original_record == expected
    old_selector, old_data = read(selection / f'package-selection-{slot}.conf', 4096)
    assert records['selected.conf'] == old_selector and data['selected.conf'] == old_data
    values = dict(line.split('=',1) for line in data['selected.conf'].decode('ascii').splitlines())
    assert set(values) == {'SCHEMA','SLOT','ROOT_UUID','ROOT_MARKER_SHA256','KERNEL_RELEASE'}
    assert values['SCHEMA'] == '1' and values['SLOT'] == slot
    assert values['ROOT_UUID'] == f'a7330000-0000-4000-8000-00000000000{slot.lower()}'
    assert values['KERNEL_RELEASE'] == '6.6.98-a733-diag'
    receipt = json.loads(data['candidate.json'])
    assert receipt['schema'] == 1 and receipt['operation'] == 'file-only-host-update-candidate'
    assert receipt['slot'] == slot and receipt['root'] == expected
    assert receipt['root_uuid'] == values['ROOT_UUID'] and receipt['kernel_release'] == values['KERNEL_RELEASE']
    assert receipt['selector'] == records['selected.conf']
    assert {key:receipt['manifest'][key] for key in ('bytes','sha256')} == original_manifest
    for key in ('management_runtime','package_state'):
        assert receipt['compatibility'][key] == manifest[key]
    for key in ('Image','board.dtb','kernel.config','initramfs.cpio.gz'):
        assert receipt['compatibility']['sources'][key] == manifest['sources'][key]
    assert receipt['staging'] == {'storage_kind':'external','required_free_bytes':5368709120,'reserve_bytes':1073741824}
    reports[slot] = {'files': records, 'original_root_unchanged': True,
                     'selector_matches_independently_prepared_package_selector': True,
                     'trusted_manifest_exact_copy': True, 'receipt_bindings_match': True,
                     'directory_mode': '0700', 'all_file_modes': '0400', 'all_owner_uid_gid':[0,0]}
print(json.dumps({'classification':'regular-file A/B candidate preparation checks; no installed update, activation, rollback or hardware acceptance',
                  'builder_directory': str(base), 'trusted_payload_manifest':original_manifest, 'slots':reports},indent=2))
