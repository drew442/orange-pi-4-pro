#!/usr/bin/env python3
"""Produce only a bounded endurance initramfs, never launch a VM or touch a disk."""
import argparse
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import stat

REPO = Path(__file__).resolve().parents[1]
EPOCH = 1793750400
MAX_RAW = 64 * 1024**2
BASE = {'bytes': 975394, 'sha256': '10b45e928ab1726e818f78ced73ab6015f0fbabef4f18403d574091fde0adaa2'}
KERNEL = {'bytes': 5251080, 'sha256': '1294959bcaa5a5ad3791913e33cffb8f0893708396aac8be9837c03a0e1f9377'}
DISK = {'bytes': 134217728, 'sha256': 'ff595fac0b9a7d990f099edc0dd0f9d4859fb9942e185d2f2377088ce32bd577',
        'uuid': 'd67f33ba-e201-46bc-980b-61901bbd1156', 'label': 'kvm-fixture',
        'feature_bytes': '28000000c20200006b040000'}
DEPENDENCIES = ('build/endurance-guest.py', 'build/repack-guest-initramfs.py',
                'build/assemble-host.py', 'configs/endurance-guest-init',
                'configs/endurance-guest-status.cgi', 'configs/guest-kernel.fragment')


def load_helper():
    spec = importlib.util.spec_from_file_location('endurance_repack', REPO / 'build/repack-guest-initramfs.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    helper.MAX_RAW = MAX_RAW
    return helper


HELPER = load_helper()
HOST = HELPER.HOST


def duration_mode(smoke_seconds=None):
    if smoke_seconds is None:
        return 86400, 'release'
    if type(smoke_seconds) is not int or not 30 <= smoke_seconds <= 300:
        raise ValueError('smoke seconds must be an integer from 30 through 300')
    return smoke_seconds, 'smoke'


def zero_hash(size):
    digest = hashlib.sha256()
    for _ in range(size // 1024):
        digest.update(bytes(1024))
    return digest.hexdigest()


def record(name, mode, data):
    """Encode one deterministic newc record (framing matches host-initramfs.newc)."""
    encoded = name.encode() + b'\0'
    fields = [0, mode, 0, 0, 1, EPOCH, len(data), 0, 0, 0, 0, len(encoded), 0]
    result = b'070701' + ''.join(f'{v:08x}' for v in fields).encode() + encoded
    result += b'\0' * (-len(result) % 4)
    result += data
    return result + b'\0' * (-len(result) % 4)


def repack(raw, replacements):
    original = HELPER.entries(raw)
    names = {item['name'] for item in original}
    if set(replacements) & names != {'init'}:
        raise ValueError('only init may replace an original record')
    output = bytearray()
    preserved = []
    for item in original:
        if item['name'] == 'TRAILER!!!':
            for name in sorted(set(replacements) - names):
                mode, data = replacements[name]
                output.extend(record(name, mode, data))
        if item['name'] in replacements:
            mode, data = replacements[item['name']]
            output.extend(record(item['name'], mode, data))
        else:
            output.extend(raw[item['start']:item['end']])
            if item['name'] != 'TRAILER!!!':
                preserved.append({'name': item['name'], **HOST.record(raw[item['start']:item['end']])})
    if len(output) > MAX_RAW:
        raise ValueError('new archive exceeds 64 MiB budget')
    parsed = HELPER.entries(bytes(output))
    actual = {item['name']: item for item in parsed}
    if set(actual) != names | set(replacements):
        raise ValueError('archive change closure differs')
    for item in original:
        if item['name'] in replacements or item['name'] == 'TRAILER!!!':
            continue
        other = actual[item['name']]
        if raw[item['start']:item['end']] != output[other['start']:other['end']]:
            raise ValueError('original record changed')
    return bytes(output), preserved


def build(source, output, smoke_seconds=None):
    seconds, mode = duration_mode(smoke_seconds)
    source = HOST.safe_path(source)
    encoded = HOST.read_regular(source, BASE['bytes'], BASE)
    with gzip.GzipFile(fileobj=io.BytesIO(encoded)) as stream:
        raw = stream.read(MAX_RAW + 1)
    if len(raw) > MAX_RAW:
        raise ValueError('decompressed archive exceeds 64 MiB budget')
    initial = HELPER.entries(raw)
    authenticated_init = next(item['data'] for item in initial if item['name'] == 'init')
    init = HOST.read_regular(REPO / 'configs/endurance-guest-init', 65536)
    # This exact identity sequence is pinned by the authenticated original init.
    for identity in (b'1080 2 53ef', b'1116 12 ' + DISK['feature_bytes'].encode(),
                     b'1128 16 d67f33bae20146bc980b61901bbd1156',
                     b'1144 16 6b766d2d666978747572650000000000'):
        if identity not in authenticated_init or identity not in init:
            raise ValueError('fixture identity differs from authenticated base')
    ram_hash, disk_hash = zero_hash(1024**2), zero_hash(16 * 1024**2)
    config = f'DURATION={seconds}\nMODE={mode}\nRAM_SHA256={ram_hash}\nDISK_SHA256={disk_hash}\n'.encode()
    additions = {'init': (stat.S_IFREG | 0o755, init),
                 'etc/endurance.conf': (stat.S_IFREG | 0o644, config),
                 'etc/endurance-status.cgi': (stat.S_IFREG | 0o755,
                    HOST.read_regular(REPO / 'configs/endurance-guest-status.cgi', 65536))}
    updated, preserved = repack(raw, additions)
    packed = io.BytesIO()
    with gzip.GzipFile(fileobj=packed, mode='wb', filename='', mtime=0, compresslevel=9) as stream:
        stream.write(updated)
    output = HOST.safe_path(output)
    if output.parent != REPO / 'out' or not re.fullmatch(r'endurance-guest-[A-Za-z0-9][A-Za-z0-9._-]*', output.name):
        raise ValueError('output must be a new direct repo/out/endurance-guest-NAME directory')
    if output.exists() or output.is_symlink():
        raise ValueError('output already exists')
    manifest = {'schema': 1, 'profile': 'a733-kvm-endurance-v1', 'mode': mode,
                'required_seconds': seconds, 'hardware_acceptance': False,
                'epoch': EPOCH, 'source_initramfs': BASE, 'kernel_expected': KERNEL,
                'seed_disk_expected': DISK, 'initramfs': HOST.record(packed.getvalue()),
                'ram_payload': {'bytes': 1024**2, 'sha256': ram_hash},
                'disk_scratch': {'bytes': 16 * 1024**2, 'sha256': disk_hash},
                'guest': {'vcpus': 4, 'memory_mib': 256, 'memory_max_mib': 512},
                'changed_records': sorted(additions), 'preserved_records': preserved,
                'dependencies': {name: HOST.record(HOST.read_regular(REPO / name, 1024**2))
                                 for name in DEPENDENCIES}}
    output.mkdir(mode=0o700)
    for name, data in [('initramfs.cpio.gz', packed.getvalue()),
                       ('manifest.json', (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode())]:
        fd = os.open(output / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
        os.utime(output / name, (EPOCH, EPOCH))
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('authenticated_base', type=Path)
    parser.add_argument('new_output', type=Path)
    parser.add_argument('--smoke-seconds', type=int, metavar='30..300')
    args = parser.parse_args()
    try:
        result = build(args.authenticated_base, args.new_output, args.smoke_seconds)
    except (ValueError, OSError, EOFError) as error:
        parser.exit(1, f'{error}\n')
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
