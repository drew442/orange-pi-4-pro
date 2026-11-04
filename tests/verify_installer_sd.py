#!/usr/bin/env python3
"""Independent regular-file installer SD content and wrapper inspection."""
import argparse
import gzip
import importlib.util
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('installer_sd_generic_checks', Path(__file__).with_name('verify_host_sd.py'))
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


def verify(directory, archive_directory, boot_profile='baseline-el1'):
    directory, archive_directory = Path(directory), Path(archive_directory)
    with CHECK.regular(directory / 'manifest.json') as source:
        manifest = json.load(source)
    with CHECK.regular(archive_directory / 'manifest.json') as source:
        archive_data = source.read(4 * 1024**2 + 1)
    if len(archive_data) > 4 * 1024**2:
        raise ValueError('archive manifest exceeds bound')
    archive = json.loads(archive_data)
    if manifest['kernel_release'] != archive['kernel_release'] or archive['kernel_release'] != '6.6.98-a733-diag':
        raise ValueError('kernel ABI differs')
    for name, expected in (('trusted_installer_manifest', CHECK.record(archive_data)),
                           ('installer_initramfs', archive['files']['initramfs.cpio.gz'])):
        if any(manifest[name][field] != value for field, value in expected.items()):
            raise ValueError('installer input provenance differs')
    pinned = CHECK.verify_boot_profile(manifest, boot_profile)
    expected_image = manifest['files']['a733-installer-prep.img']
    with CHECK.regular(directory / 'a733-installer-prep.img') as image, tempfile.TemporaryDirectory(prefix='installer-sd-inspect-', dir='/var/tmp') as temporary:
        if CHECK.stream_record(image) != expected_image or expected_image['bytes'] != CHECK.IMAGE_BYTES:
            raise ValueError('image size/hash differs')
        image.seek(0)
        mbr = image.read(512)
        if (mbr[510:] != b'\x55\xaa' or mbr[446] != 0x80 or mbr[450] != 0x83 or
                struct.unpack_from('<II', mbr, 454) != (65536, 458752) or any(mbr[462:510])):
            raise ValueError('MBR layout differs')
        for name, offset in (('boot0_sdcard.fex', 8192), ('boot_package.fex', 16793600)):
            image.seek(offset)
            if CHECK.record(image.read(pinned[name]['bytes'])) != pinned[name]:
                raise ValueError('raw vendor blob differs')
        work = Path(temporary)
        partition = work / 'partition.ext4'
        image.seek(CHECK.PARTITION_OFFSET)
        with partition.open('xb') as output:
            shutil.copyfileobj(image, output)
        subprocess.run(['e2fsck', '-fn', str(partition)], check=True, capture_output=True)
        details = subprocess.check_output(['dumpe2fs', '-h', str(partition)], stderr=subprocess.DEVNULL, text=True)
        features = next(line.split(':', 1)[1].split() for line in details.splitlines() if line.startswith('Filesystem features:'))
        if set(features) & {'has_journal', '64bit', 'metadata_csum', 'metadata_csum_seed'}:
            raise ValueError('legacy ext4 features differ')
        def readback(path, name):
            target = work / name
            subprocess.run(['debugfs', '-R', f'dump {path} {target}', str(partition)], check=True, capture_output=True)
            return target.read_bytes()
        for name, path in (('uImage', '/boot/uImage'), ('board.dtb', '/boot/dtb/allwinner/sun60i-a733-orangepi-4-pro.dtb')):
            if CHECK.record(readback(path, name)) != pinned[name]:
                raise ValueError('kernel/DTB differs from pinned stack')
        command = (REPO / 'configs/installer-boot.cmd').read_bytes()
        if readback('/boot/boot.cmd', 'boot.cmd') != command:
            raise ValueError('installer command differs')
        script = CHECK.wrapper(readback('/boot/boot.scr', 'boot.scr'), 6, 0)
        if script != struct.pack('>II', len(command), 0) + command:
            raise ValueError('script wrapper/content differs')
        initrd = readback('/boot/uInitrd', 'uInitrd')
        if CHECK.record(initrd) != manifest['files']['uInitrd']:
            raise ValueError('embedded initrd differs from output record')
        if CHECK.record(CHECK.wrapper(initrd, 3, 1)) != archive['files']['initramfs.cpio.gz']:
            raise ValueError('embedded payload differs from checked installer archive')
        image.seek(0)
        if CHECK.stream_record(image) != expected_image:
            raise ValueError('image changed during inspection')
    with CHECK.regular(directory / 'a733-installer-prep.img.gz') as compressed:
        if CHECK.stream_record(compressed) != manifest['files']['a733-installer-prep.img.gz']:
            raise ValueError('compressed distribution differs')
        compressed.seek(0)
        with gzip.GzipFile(fileobj=compressed) as expanded:
            if CHECK.stream_record(expanded) != expected_image:
                raise ValueError('compressed expansion differs')
    return {'classification': 'regular-file installer SD inspection; no physical boot or installation acceptance',
            'image': expected_image, 'legacy_ext4_features': features,
            'mbr_and_vendor_blob_offsets': 'passed', 'pinned_kernel_DTB': 'passed',
            'script_and_initrd_wrappers_CRCs': 'passed', 'checked_installer_archive_payload': archive['files']['initramfs.cpio.gz'],
            'compressed_hash_and_expansion': 'passed', 'physical_storage_writes': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory')
    parser.add_argument('archive_directory')
    parser.add_argument('--boot-profile', choices=CHECK.HOST.BOOT_PROFILES, default='baseline-el1')
    args = parser.parse_args()
    print(json.dumps(verify(args.directory, args.archive_directory, args.boot_profile), indent=2))
