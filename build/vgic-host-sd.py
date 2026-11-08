#!/usr/bin/env python3
"""Build an experimental DT-only VGIC SD candidate from the installed 00fd image.

No device access, source compilation, NVMe modification or hardware acceptance.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location('assemble_host', REPO / 'build/assemble-host.py')
HOST = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(HOST)
IMAGE_RECORD = {'bytes': 268435456,
                'sha256': '2d22d84ada37a5b5d3696cc913caddcb31c0e681e65185005ea56ca0b8396329'}
MANIFEST_RECORD = {'bytes': 9116,
                   'sha256': 'b5af23b9f1a1604e4635bcce98c819328797104e2efe46b447343fe1124edcc0'}
STATE_UUID = '00fd936c-49fb-4fea-87c8-928b9c1599e0'
PARTITION_OFFSET = 32 * 1024**2
PARTITION_BYTES = 224 * 1024**2
NODE = '/interrupt-controller@3400000'
PROPERTY = (NODE, 'interrupts')
VALUE = struct.pack('>III', 1, 9, 4)
PATCH_URL = 'https://lkml.iu.edu/hypermail/linux/kernel/2610.3/16416.html'
FILES = {'uImage': '/boot/uImage', 'uInitrd': '/boot/uInitrd',
         'board.dtb': '/boot/dtb/allwinner/sun60i-a733-orangepi-4-pro.dtb',
         'boot.cmd': '/boot/boot.cmd', 'boot.scr': '/boot/boot.scr'}


def file_record(path):
    path = HOST.safe_path(path)
    digest = hashlib.sha256()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        import stat
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError(f'not a regular file: {path}')
        size = 0
        for data in iter(lambda: stream.read(1024**2), b''):
            digest.update(data)
            size += len(data)
    return {'bytes': size, 'sha256': digest.hexdigest()}


def require_record(path, expected):
    if file_record(path) != expected:
        raise ValueError(f'hash/size mismatch: {path}')


def fdt_semantics(data):
    """Read raw FDT nodes/properties, avoiding textual formatting/order ambiguity."""
    if len(data) < 40:
        raise ValueError('short FDT')
    magic, total, off_struct, off_strings, off_reserve, version, compatible, cpu, strings_size, struct_size = struct.unpack_from('>10I', data)
    if (magic != 0xd00dfeed or total != len(data) or version != 17 or compatible > 17 or
            off_struct + struct_size > total or off_strings + strings_size > total):
        raise ValueError('unsupported/invalid FDT header')
    reserve_end = off_reserve
    while True:
        if reserve_end + 16 > total:
            raise ValueError('invalid FDT reservations')
        pair = struct.unpack_from('>QQ', data, reserve_end)
        reserve_end += 16
        if pair == (0, 0):
            break
    strings = data[off_strings:off_strings + strings_size]
    block = data[off_struct:off_struct + struct_size]
    position, stack, nodes, props = 0, [], set(), {}

    def take(size):
        nonlocal position
        if position + size > len(block):
            raise ValueError('truncated FDT structure')
        result = block[position:position + size]
        position += size
        return result

    while position < len(block):
        token = struct.unpack('>I', take(4))[0]
        if token == 1:
            end = block.find(b'\0', position)
            if end < 0:
                raise ValueError('unterminated FDT node')
            name = take(end - position + 1)[:-1].decode('ascii')
            position = (position + 3) & ~3
            stack.append(name)
            path = '/' + '/'.join(stack[1:])
            if path in nodes or (len(stack) == 1 and name):
                raise ValueError('duplicate/invalid FDT node')
            nodes.add(path)
        elif token == 2:
            if not stack:
                raise ValueError('unbalanced FDT nodes')
            stack.pop()
        elif token == 3:
            length, nameoff = struct.unpack('>II', take(8))
            end = strings.find(b'\0', nameoff)
            if not stack or end < 0:
                raise ValueError('invalid FDT property')
            key = ('/' + '/'.join(stack[1:]), strings[nameoff:end].decode('ascii'))
            if key in props:
                raise ValueError('duplicate FDT property')
            props[key] = take(length)
            position = (position + 3) & ~3
        elif token == 4:
            continue
        elif token == 9:
            if stack:
                raise ValueError('unclosed FDT node')
            return nodes, props, (compatible, cpu, data[off_reserve:reserve_end])
        else:
            raise ValueError('invalid FDT token')
    raise ValueError('missing FDT end')


def verify_delta(original, candidate):
    old_nodes, old_props, old_meta = fdt_semantics(original)
    nodes, props, meta = fdt_semantics(candidate)
    if NODE not in old_nodes or PROPERTY in old_props:
        raise ValueError('baseline GIC missing or interrupts already present')
    expected = dict(old_props)
    expected[PROPERTY] = VALUE
    if old_nodes != nodes or old_meta != meta or props != expected:
        raise ValueError('DT semantic delta exceeds the single GIC interrupts property')


def copy_region(source, output, offset, length):
    """Sparse regular-file copy; caller creates outputs in its private new directory."""
    with open(source, 'rb') as src, open(output, 'xb') as dst:
        src.seek(offset)
        remaining = length
        while remaining:
            chunk = src.read(min(1024**2, remaining))
            if not chunk:
                raise ValueError('short image region')
            if chunk.count(0) == len(chunk):
                dst.seek(len(chunk), 1)
            else:
                dst.write(chunk)
            remaining -= len(chunk)
        dst.truncate(length)


def verify_layout(image):
    if file_record(image)['bytes'] != IMAGE_RECORD['bytes']:
        raise ValueError('wrong SD image capacity')
    with open(image, 'rb') as stream:
        mbr = stream.read(512)
    entry = mbr[446:462]
    if (mbr[510:] != b'\x55\xaa' or mbr[440:444] != struct.pack('<I', 0xa7330001) or
            entry[0] != 0x80 or entry[4] != 0x83 or
            struct.unpack_from('<II', entry, 8) != (65536, 458752) or
            mbr[462:510] != bytes(48)):
        raise ValueError('unexpected original MBR/partition layout')


def extract(image, destination):
    destination.mkdir()
    partition = destination / 'partition.ext4'
    copy_region(image, partition, PARTITION_OFFSET, PARTITION_BYTES)
    for name, fs_path in FILES.items():
        target = destination / name
        # All strings are recipe constants or generated safe directory names.
        subprocess.run(['debugfs', '-R', f'dump {fs_path} {target}', str(partition)],
                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if not target.is_file() or target.stat().st_size == 0:
            raise ValueError(f'debugfs failed to extract {fs_path}')
    partition.unlink()


def verify_boot_files(original, rebuilt):
    for blob in FILES:
        if blob != 'board.dtb' and file_record(original / blob) != file_record(rebuilt / blob):
            raise ValueError(f'preserved boot file changed: {blob}')
    verify_delta((original / 'board.dtb').read_bytes(), (rebuilt / 'board.dtb').read_bytes())


def builder_packages():
    lock = REPO / 'configs/builder-packages.lock.tsv'
    result = HOST.validate_builder_packages(lock)
    rows = [line.split('\t') for line in HOST.read_regular(lock, 1024**2).decode().splitlines()
            if line.split('\t')[0] == 'device-tree-compiler']
    if rows != [['device-tree-compiler', '1.6.1-1', 'amd64']]:
        raise ValueError('unexpected device-tree-compiler lock')
    actual = subprocess.check_output(
        ['dpkg-query', '-W', '-f=${Version}\t${Architecture}', 'device-tree-compiler:amd64'], text=True)
    if actual != '1.6.1-1\tamd64':
        raise ValueError('device-tree-compiler version/architecture differs from lock')
    result['device-tree-compiler'] = {'version': '1.6.1-1', 'architecture': 'amd64'}
    return result


def output_path(name):
    if not re.fullmatch('[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}', name):
        raise ValueError('name must be 1..64 letters/digits/underscores/hyphens')
    output = HOST.safe_path(REPO / 'out' / ('vgic-host-sd-' + name))
    if output.exists() or output.is_symlink():
        raise ValueError('output must not exist')
    return output


def build(image, manifest_path, name):
    output = output_path(name)
    image = HOST.safe_path(image)
    manifest_data = HOST.read_regular(manifest_path, 1024**2, MANIFEST_RECORD)
    manifest = HOST.json_object(manifest_data)
    if (manifest['state_uuid'] != STATE_UUID or
            manifest['files']['a733-host-sd.img'] != IMAGE_RECORD):
        raise ValueError('original deployment profile differs')
    require_record(image, IMAGE_RECORD)
    verify_layout(image)
    require_record(REPO / 'configs/builder-packages.lock.tsv',
                   manifest['recipes']['configs/builder-packages.lock.tsv'])
    packages = builder_packages()
    output.parent.mkdir(exist_ok=True)
    if shutil.disk_usage(output.parent).free < 256 * 1024**2:
        raise ValueError('need at least 256 MiB free before sparse SD packaging')
    output.mkdir(mode=0o700)  # Atomic no-overwrite reservation; retain failures for inspection.
    with tempfile.TemporaryDirectory(prefix='scratch-', dir=output) as scratch_name:
        scratch = Path(scratch_name)
        original = scratch / 'original'
        extract(image, original)
        for blob in ('uImage', 'board.dtb'):
            require_record(original / blob, manifest['boot_inputs'][blob])
        require_record(original / 'uInitrd', manifest['files']['uInitrd'])
        require_record(original / 'boot.cmd', manifest['boot_command'])
        inputs = scratch / 'inputs'
        inputs.mkdir()
        for blob, offset in (('boot0_sdcard.fex', 8192), ('boot_package.fex', 16793600)):
            pin = manifest['boot_inputs'][blob]
            copy_region(image, inputs / blob, offset, pin['bytes'])
            require_record(inputs / blob, pin)
        for blob in ('uImage', 'uInitrd', 'board.dtb'):
            shutil.copyfile(original / blob, inputs / blob)
        subprocess.run(['fdtput', '-t', 'i', str(inputs / 'board.dtb'), NODE,
                        'interrupts', '1', '9', '4'], check=True)
        verify_delta((original / 'board.dtb').read_bytes(), (inputs / 'board.dtb').read_bytes())
        candidate = output / 'a733-host-sd.img'
        env = dict(os.environ, TMPDIR=str(scratch), LC_ALL='C', TZ='UTC')
        with open(output / 'assembly.log', 'xb') as log:
            subprocess.run(['bash', str(REPO / 'build/assemble-diagnostic.sh'), str(inputs),
                            str(candidate), str(original / 'boot.cmd')], env=env,
                           stdout=log, stderr=subprocess.STDOUT, check=True)
        verify_layout(candidate)
        with open(image, 'rb') as before, open(candidate, 'rb') as after:
            if before.read(PARTITION_OFFSET) != after.read(PARTITION_OFFSET):
                raise ValueError('raw boot firmware/MBR region changed')
        rebuilt = scratch / 'rebuilt'
        extract(candidate, rebuilt)
        verify_boot_files(original, rebuilt)
        require_record(image, IMAGE_RECORD)
        result = {'schema': 1, 'classification': 'experimental VGIC DT candidate; UNBOOTED',
                  'state_uuid': STATE_UUID, 'input_image': IMAGE_RECORD,
                  'input_manifest': MANIFEST_RECORD, 'builder_packages': packages,
                  'source_patch_url': PATCH_URL,
                  'source_patch_status': 'AI-assisted proposal; no Reviewed-by/Tested-by acceptance',
                  'semantic_change': {'node': NODE, 'property': 'interrupts', 'cells': [1, 9, 4]},
                  'preserved_files': {blob: file_record(original / blob) for blob in FILES if blob != 'board.dtb'},
                  'firmware': {blob: file_record(inputs / blob) for blob in ('boot0_sdcard.fex', 'boot_package.fex')},
                  'dtb_before': file_record(original / 'board.dtb'),
                  'dtb_after': file_record(rebuilt / 'board.dtb'),
                  'verification': 'single semantic DT property; all other boot file bytes and first 32MiB identical',
                  'recipes': {path: file_record(REPO / path) for path in
                              ('build/vgic-host-sd.py', 'build/assemble-diagnostic.sh',
                               'build/assemble-host.py', 'configs/builder-packages.lock.tsv')},
                  'files': {'a733-host-sd.img': file_record(candidate),
                            'assembly.log': file_record(output / 'assembly.log')},
                  'hardware_acceptance': 'PENDING: reboot, live DT/VGIC, actual KVM guest and release gates'}
        with open(output / 'manifest.json', 'x') as stream:
            json.dump(result, stream, indent=2)
            stream.write('\n')
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--name', required=True)
    parser.add_argument('--image', type=Path, default=REPO / 'out/host-deployment-20261106/a733-host-sd.img')
    parser.add_argument('--manifest', type=Path, default=REPO / 'out/host-deployment-20261106/manifest.json')
    args = parser.parse_args()
    try:
        print(build(args.image, args.manifest, args.name))
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'VGIC candidate failed: {error}\n')


if __name__ == '__main__':
    main()
