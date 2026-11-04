#!/usr/bin/env python3
"""Verify generated diagnostic files and filesystem contents; never open a disk."""
import hashlib
import json
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import zlib


def digest(path):
    checksum = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(chunk)
    return checksum.hexdigest()


def run(*args):
    return subprocess.check_output(args, stderr=subprocess.STDOUT, text=True)


def verify(directory):
    manifest = json.loads((directory / 'artifact-manifest.json').read_text())
    required = {'a733-diagnostic.img', 'a733-diagnostic.img.gz', 'uImage', 'uInitrd',
                'board.dtb', 'boot0_sdcard.fex', 'boot_package.fex', 'build-recipe.tar.gz'}
    if manifest.get('schema') != 1 or not required <= manifest['files'].keys():
        raise ValueError('Incomplete artifact manifest')
    for name, record in manifest['files'].items():
        if Path(name).name != name or not stat.S_ISREG((directory / name).lstat().st_mode):
            raise ValueError(f'Not a top-level regular artifact: {name}')
        path = directory / name
        if path.stat().st_size != record['bytes'] or digest(path) != record['sha256']:
            raise ValueError(f'Artifact size/hash mismatch: {name}')
    image = directory / 'a733-diagnostic.img'
    if image.stat().st_size != 268435456:
        raise ValueError('Expected exact 256 MiB image')
    with tempfile.TemporaryDirectory(prefix='verify-a733-') as temporary:
        work = Path(temporary)
        partition = work / 'partition.ext4'
        with image.open('rb') as stream:
            mbr = stream.read(512)
            if mbr[510:] != b'\x55\xaa' or mbr[446] != 0x80 or mbr[450] != 0x83:
                raise ValueError('Unexpected DOS partition table')
            if struct.unpack_from('<II', mbr, 454) != (65536, 458752) or any(mbr[462:510]):
                raise ValueError('Unexpected partition bounds/count')
            for name, offset in [('boot0_sdcard.fex', 8192), ('boot_package.fex', 16793600)]:
                expected = (directory / name).read_bytes()
                stream.seek(offset)
                if stream.read(len(expected)) != expected:
                    raise ValueError(f'Raw boot blob differs: {name}')
            stream.seek(33554432)
            with partition.open('wb') as destination:
                shutil.copyfileobj(stream, destination)
        run('e2fsck', '-fn', str(partition))
        details = run('dumpe2fs', '-h', str(partition))
        features = next(line for line in details.splitlines()
                        if line.startswith('Filesystem features:')).split(':', 1)[1].split()
        if set(features) & {'has_journal', '64bit', 'metadata_csum', 'metadata_csum_seed'}:
            raise ValueError('Legacy-reader filesystem feature mismatch')
        for name, source in [('uImage', '/boot/uImage'), ('uInitrd', '/boot/uInitrd'),
                             ('board.dtb', '/boot/dtb/allwinner/sun60i-a733-orangepi-4-pro.dtb')]:
            output = work / name
            run('debugfs', '-R', f'dump {source} {output}', str(partition))
            if digest(output) != digest(directory / name):
                raise ValueError(f'Filesystem payload differs: {name}')
        with tarfile.open(directory / 'build-recipe.tar.gz', 'r:gz') as archive:
            command = archive.extractfile('configs/diagnostic-boot.cmd').read()
        boot = work / 'boot.scr'
        run('debugfs', '-R', f'dump /boot/boot.scr {boot}', str(partition))
        script = boot.read_bytes()
        header = bytearray(script[:64])
        header[4:8] = bytes(4)
        if (struct.unpack_from('>I', script)[0] != 0x27051956
                or zlib.crc32(header) != struct.unpack_from('>I', script, 4)[0]
                or zlib.crc32(script[64:]) != struct.unpack_from('>I', script, 24)[0]
                or script[64:72] != struct.pack('>II', len(command), 0)
                or script[72:] != command):
            raise ValueError('Legacy boot script payload/CRC mismatch')
        for name in ('uImage', 'uInitrd'):
            run('mkimage', '-l', str(directory / name))
    return {'host_artifact_verification': 'passed', 'image_bytes': image.stat().st_size,
            'image_sha256': digest(image), 'hardware_acceptance': 'pending'}


if __name__ == '__main__':
    if len(sys.argv) != 2:
        sys.exit(f'Usage: {sys.argv[0]} ARTIFACT_DIRECTORY')
    try:
        print(json.dumps(verify(Path(sys.argv[1]).resolve()), indent=2))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, tarfile.TarError) as error:
        sys.exit(f'Diagnostic verification failed: {error}')
