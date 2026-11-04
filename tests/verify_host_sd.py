#!/usr/bin/env python3
"""Opt-in regular-file host SD inspection; no mounts or physical targets."""
import argparse
import importlib.util
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import zlib

REPO = Path(__file__).resolve().parents[1]
IMAGE_BYTES = 256 * 1024**2
PARTITION_OFFSET = 32 * 1024**2


SPEC = importlib.util.spec_from_file_location('verify_sd_boot_profiles', REPO / 'build/assemble-host.py')
HOST = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HOST)


def verify_boot_profile(manifest, boot_profile):
    pinned, expected, recipes = HOST.load_boot_profile(boot_profile)
    historical = 'boot_profile' not in manifest and boot_profile == 'baseline-el1'
    if not historical and manifest.get('boot_profile') != expected:
        raise ValueError('boot profile metadata differs from explicit trusted selection')
    if manifest.get('boot_inputs') != {name: pinned[name] for name in HOST.BLOBS}:
        raise ValueError('boot profile input provenance differs')
    for name, data in recipes.items():
        if manifest.get('recipes', {}).get(name) != record(data):
            raise ValueError('boot profile evidence recipe provenance differs')
    return pinned


def record(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def regular(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    stream = os.fdopen(fd, 'rb')
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        stream.close()
        raise ValueError('regular files required')
    return stream


def stream_record(source):
    size = 0
    digest = hashlib.sha256()
    for chunk in iter(lambda: source.read(1024**2), b''):
        size += len(chunk)
        digest.update(chunk)
    return {'bytes': size, 'sha256': digest.hexdigest()}


def wrapper(data, kind, compression):
    if len(data) < 64:
        raise ValueError('short legacy image')
    header = bytearray(data[:64])
    header[4:8] = bytes(4)
    if (struct.unpack_from('>I', data)[0] != 0x27051956 or
            struct.unpack_from('>I', data, 4)[0] != zlib.crc32(header) or
            struct.unpack_from('>I', data, 12)[0] != len(data) - 64 or
            struct.unpack_from('>I', data, 24)[0] != zlib.crc32(data[64:]) or
            data[28:32] != bytes((5, 2, kind, compression))):
        raise ValueError('legacy image header/type/CRC mismatch')
    return data[64:]


def inspect_newc(compressed, state_uuid, manifest):
    # Cap expansion independently of the manifest and never extract archive paths.
    import io
    with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as archive:
        data = archive.read(256 * 1024**2 + 1)
    if len(data) > 256 * 1024**2:
        raise ValueError('initramfs expansion exceeds inspection budget')
    pos = 0
    entries = {}
    while True:
        if data[pos:pos + 6] != b'070701' or len(data) - pos < 110:
            raise ValueError('invalid newc header')
        fields = [int(data[pos + 6 + i * 8:pos + 14 + i * 8], 16) for i in range(13)]
        _, mode, uid, gid, nlink, mtime, size, _, _, _, _, namesize, _ = fields
        if uid or gid or mtime or nlink != 1 or not 1 <= namesize <= 4096:
            raise ValueError('unexpected newc metadata')
        pos += 110
        encoded = data[pos:pos + namesize]
        if len(encoded) != namesize or encoded[-1:] != b'\0':
            raise ValueError('invalid newc name')
        name = encoded[:-1].decode()
        pos = (pos + namesize + 3) & ~3
        content = data[pos:pos + size]
        if len(content) != size:
            raise ValueError('truncated newc payload')
        pos = (pos + size + 3) & ~3
        if name == 'TRAILER!!!':
            if size or any(data[pos:]):
                raise ValueError('unexpected newc trailer or trailing content')
            break
        if name in entries or PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts:
            raise ValueError('duplicate/unsafe newc path')
        entries[name] = (mode, content)
    records = {name: dict(mode=mode, **record(content)) for name, (mode, content) in entries.items()
               if stat.S_ISREG(mode)}
    if records != manifest['entries']:
        raise ValueError('initramfs regular entries differ from trusted manifest')
    config = f'STATE_UUID={state_uuid}\nKERNEL_RELEASE=6.6.98-a733-diag\n'.encode()
    if entries['etc/a733-boot.conf'][1] != config:
        raise ValueError('initramfs state/ABI configuration differs')
    for name, source in (('init', 'configs/host-init'), ('etc/host-storage-resolver.sh', 'configs/host-storage-resolver.sh')):
        if entries[name][1] != (REPO / source).read_bytes():
            raise ValueError('initramfs differs from current reviewed source')
    return len(entries)


def verify(directory, initramfs_directory, boot_profile='baseline-el1'):
    directory, initramfs_directory = Path(directory), Path(initramfs_directory)
    with regular(directory / 'manifest.json') as source:
        manifest = json.load(source)
    with regular(initramfs_directory / 'manifest.json') as source:
        host_data = source.read(4 * 1024**2 + 1)
    if len(host_data) > 4 * 1024**2:
        raise ValueError('trusted host manifest exceeds budget')
    host = json.loads(host_data)
    for field in ('state_uuid', 'kernel_release'):
        if manifest[field] != host[field]:
            raise ValueError('packaging and trusted host metadata differ')
    for field, expected in (('trusted_host_manifest', record(host_data)),
                            ('host_initramfs', host['files']['initramfs.cpio.gz'])):
        if any(manifest[field][name] != value for name, value in expected.items()):
            raise ValueError('packaging input record differs from trusted host input')
    pinned = verify_boot_profile(manifest, boot_profile)
    image_name = 'a733-host-sd.img'
    image_record = manifest['files'][image_name]
    with regular(directory / image_name) as image, tempfile.TemporaryDirectory(prefix='a733-host-sd-', dir='/var/tmp') as temporary:
        if stream_record(image) != image_record or image_record['bytes'] != IMAGE_BYTES:
            raise ValueError('host SD image size/hash differs')
        image.seek(0)
        mbr = image.read(512)
        if (mbr[510:] != b'\x55\xaa' or struct.unpack_from('<I', mbr, 440)[0] != 0xa7330001 or
                mbr[446] != 0x80 or mbr[450] != 0x83 or
                struct.unpack_from('<II', mbr, 454) != (65536, 458752) or any(mbr[462:510])):
            raise ValueError('host SD MBR differs from reviewed layout')
        for name, offset in (('boot0_sdcard.fex', 8192), ('boot_package.fex', 16793600)):
            image.seek(offset)
            if record(image.read(pinned[name]['bytes'])) != pinned[name]:
                raise ValueError('raw vendor boot blob differs')
        work = Path(temporary)
        partition = work / 'partition.ext4'
        image.seek(PARTITION_OFFSET)
        with partition.open('xb') as output:
            shutil.copyfileobj(image, output)
        subprocess.run(['e2fsck', '-fn', str(partition)], check=True, capture_output=True)
        details = subprocess.check_output(['dumpe2fs', '-h', str(partition)], stderr=subprocess.DEVNULL, text=True)
        features = next(line.split(':', 1)[1].split() for line in details.splitlines() if line.startswith('Filesystem features:'))
        if set(features) & {'has_journal', '64bit', 'metadata_csum', 'metadata_csum_seed'}:
            raise ValueError('legacy U-Boot filesystem feature mismatch')
        def extract(path, name):
            target = work / name
            subprocess.run(['debugfs', '-R', f'dump {path} {target}', str(partition)], check=True, capture_output=True)
            return target.read_bytes()
        for name, path in (('uImage', '/boot/uImage'), ('board.dtb', '/boot/dtb/allwinner/sun60i-a733-orangepi-4-pro.dtb')):
            if record(extract(path, name)) != pinned[name]:
                raise ValueError('filesystem boot stack differs')
        command = (REPO / 'configs/host-boot.cmd').read_bytes()
        if extract('/boot/boot.cmd', 'boot.cmd') != command:
            raise ValueError('host boot command differs')
        script = wrapper(extract('/boot/boot.scr', 'boot.scr'), 6, 0)
        if script != struct.pack('>II', len(command), 0) + command:
            raise ValueError('boot script contents differ')
        initrd = extract('/boot/uInitrd', 'uInitrd')
        if record(initrd) != manifest['files']['uInitrd']:
            raise ValueError('embedded uInitrd differs from artifact record')
        payload = wrapper(initrd, 3, 1)
        if record(payload) != host['files']['initramfs.cpio.gz']:
            raise ValueError('uInitrd payload differs from trusted host initramfs')
        entry_count = inspect_newc(payload, host['state_uuid'], host)
        image.seek(0)
        if stream_record(image) != image_record:
            raise ValueError('image changed during inspection')
    with regular(directory / (image_name + '.gz')) as compressed:
        if stream_record(compressed) != manifest['files'][image_name + '.gz']:
            raise ValueError('compressed distribution differs')
        compressed.seek(0)
        with gzip.GzipFile(fileobj=compressed) as expanded:
            if stream_record(expanded) != image_record:
                raise ValueError('distribution expansion differs')
    return {'classification': 'independent regular-file host SD checks; hardware acceptance pending',
            'image': image_record, 'state_uuid': host['state_uuid'], 'mbr_and_raw_blobs': 'passed',
            'legacy_ext4_features': features, 'pinned_kernel_and_dtb': 'passed',
            'host_script_crc_and_contents': 'passed', 'initrd_wrapper_and_payload': 'passed',
            'initramfs_entries_checked': entry_count, 'gzip_expansion': 'passed',
            'physical_storage_writes': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory')
    parser.add_argument('initramfs_directory')
    parser.add_argument('--boot-profile', choices=HOST.BOOT_PROFILES, default='baseline-el1')
    args = parser.parse_args()
    print(json.dumps(verify(args.directory, args.initramfs_directory, args.boot_profile), indent=2))
