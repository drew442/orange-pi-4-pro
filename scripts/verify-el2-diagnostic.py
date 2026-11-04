#!/usr/bin/env python3
"""Inspect experimental regular-file artifacts; never open a physical device.

The baseline verifier checks MBR, boot blobs, legacy ext4 and script CRCs. This
profile adds pinned baseline comparison, gzip readback and packed TOC entries.
"""
import argparse
import gzip
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import tempfile

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('baseline_verify', REPO / 'scripts/verify-diagnostic.py')
BASE = importlib.util.module_from_spec(spec)
spec.loader.exec_module(BASE)


def check(condition, message):
    if not condition:
        raise ValueError(message)


def read_manifest(path):
    check(stat.S_ISREG(path.lstat().st_mode), 'Manifest must be a regular file')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'r') as stream:
        check(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), 'Manifest type changed')
        return json.load(stream)


def verify(root, baseline):
    root, baseline = root.resolve(), baseline.resolve()
    manifest = read_manifest(root / 'artifact-manifest.json')
    pins = json.loads((REPO / 'docs/evidence/diagnostic-20261104.json').read_text())['files']
    immutable = {'Image', 'uImage', 'uInitrd', 'initramfs.cpio.gz', 'board.dtb',
                 'kernel.config', 'boot0_sdcard.fex'}
    required = immutable | {'a733-el2-diagnostic.img', 'a733-el2-diagnostic.img.gz',
                            'boot_package.fex', 'build-recipe.tar.gz',
                            'pack/u-boot.fex', 'pack/monitor.fex', 'pack/scp.fex'}
    check(manifest.get('schema') == 1 and required <= manifest['files'].keys(),
          'Incomplete experimental manifest')
    check(set(manifest['immutable_base_files']) == immutable, 'Unexpected immutable payload set')
    for name, facts in manifest['files'].items():
        relative = Path(name)
        check(not relative.is_absolute() and '..' not in relative.parts, 'Unsafe artifact name')
        path = root / relative
        check(not any(p.is_symlink() for p in [path, *path.parents]), 'Symlink artifact')
        check(stat.S_ISREG(path.stat().st_mode), 'Not a regular artifact')
        check(path.stat().st_size == facts['bytes'] and BASE.digest(path) == facts['sha256'],
              f'Artifact size/hash mismatch: {name}')
    for name in ['a733-diagnostic.img', *manifest['immutable_base_files']]:
        path = baseline / name
        check(stat.S_ISREG(path.lstat().st_mode), 'Baseline must be regular')
        check(path.stat().st_size == pins[name]['bytes'] and BASE.digest(path) == pins[name]['sha256'],
              f'Baseline pin mismatch: {name}')
        if name != 'a733-diagnostic.img':
            check(manifest['files'][name] == pins[name], f'Changed base payload: {name}')
    image = root / 'a733-el2-diagnostic.img'
    size = image.stat().st_size
    check(size == 268435456 and size < 1977614336, 'Image capacity mismatch')
    package = (root / 'boot_package.fex').read_bytes()
    start, end = 16793600, 16793600 + len(package)
    check(len(package) == pins['boot_package.fex']['bytes'], 'Packed region size changed')
    with image.open('rb') as new, (baseline / 'a733-diagnostic.img').open('rb') as old:
        for offset in range(0, size, 1024**2):
            a, b = new.read(1024**2), old.read(1024**2)
            for left, right in [(offset, min(offset + len(a), start)),
                                (max(offset, end), offset + len(a))]:
                if left < right:
                    check(a[left-offset:right-offset] == b[left-offset:right-offset],
                          f'Changed bytes outside boot package at {left}')
    with gzip.open(root / 'a733-el2-diagnostic.img.gz', 'rb') as encoded, image.open('rb') as raw:
        while True:
            block = raw.read(1024**2)
            check(encoded.read(len(block) or 1) == block, 'Compressed expansion differs')
            if not block:
                break
    # Pinned U-Boot include/private_toc.h: 64-byte TOC1 header, 368-byte items.
    check(package[:16].rstrip(b'\0') == b'sunxi-package', 'Wrong TOC name')
    check(struct.unpack_from('<I', package, 32)[0] == 3, 'Wrong TOC count')
    check(struct.unpack_from('<I', package, 36)[0] == len(package), 'Wrong TOC length')
    packed = {}
    for index in range(3):
        position = 64 + index * 368
        name = package[position:position+64].split(b'\0', 1)[0].decode('ascii')
        offset, length, encrypted, kind = struct.unpack_from('<4I', package, position+64)
        check(name in ('u-boot', 'monitor', 'scp') and name not in packed, 'Unexpected TOC entry')
        check(offset >= 2048 and offset % 512 == 0 and offset+length <= len(package)
              and encrypted == 0 and kind == 3, 'Invalid packed entry bounds/type')
        expected = root / 'pack' / f'{name}.fex'
        check(package[offset:offset+length] == expected.read_bytes(), f'Packed {name} differs')
        packed[name] = {'offset': offset, 'bytes': length, 'sha256': BASE.digest(expected)}
    check(set(packed) == {'u-boot', 'monitor', 'scp'}, 'Missing TOC component')
    vendor_pins = dict(line.split(maxsplit=1)[::-1] for line in
                       (REPO / 'configs/vendor-boot-inputs.sha256').read_text().splitlines())
    for name in ('monitor', 'scp'):
        check(packed[name]['sha256'] == vendor_pins[f'external/packages/pack-uboot/sun60iw2/bin/{name}.fex'],
              f'Changed opaque {name}')
    with tempfile.TemporaryDirectory(prefix='verify-a733-el2-') as temporary:
        stage = Path(temporary)
        selected = {}
        for name in ('a733-diagnostic.img', 'a733-diagnostic.img.gz', 'uImage', 'uInitrd',
                     'board.dtb', 'boot0_sdcard.fex', 'boot_package.fex', 'build-recipe.tar.gz'):
            source = name.replace('a733-diagnostic.', 'a733-el2-diagnostic.')
            shutil.copyfile(root / source, stage / name)
            selected[name] = manifest['files'][source]
        (stage / 'artifact-manifest.json').write_text(json.dumps({'schema': 1, 'files': selected}))
        result = BASE.verify(stage)
    result.update({'packed_components': packed, 'outside_boot_package': 'byte-identical to pinned baseline',
                   'gzip_expansion': 'byte-identical to raw image', 'hardware_acceptance': 'UNTESTED'})
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('artifacts', type=Path)
    parser.add_argument('baseline', type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.artifacts, args.baseline), indent=2))
