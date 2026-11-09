#!/usr/bin/env python3
"""Regular-file-only, init-only derivative of the pinned corrected VGIC SD."""
import argparse
from contextlib import ExitStack
import copy
import gzip
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import tempfile
import zlib

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location('protected_sd_helpers', REPO / 'build/vgic-nvme-image.py')
HELP = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(HELP)
RUNTIME, VGIC = HELP.RUNTIME, HELP.VGIC
record, require = VGIC.HOST.record, HELP.require
BASE = HELP.SD
INITRD = HELP.BOOT_FILES['boot/uInitrd']
OLD_INIT_SHA = '82515ff0fb49d0d3399f3cc3546a768742bd50db6500452bafa6ae1d105c3964'
BUSYBOX_SHA = '0a41864f87a0737804f0f4a68a1cfa1ed22374f4f75a1be3eb376e2659ce1316'
SD_COMMANDS = {
    'boot/boot.cmd': {'bytes': 1115, 'sha256': 'd65f4dc811150fed3ab9b67722e6b327341d388b0263907380c9bf59497fd93f'},
    'boot/boot.scr': {'bytes': 1187, 'sha256': 'f1ba10868782026d6fa4392b5e5f60db7953e8a340539f750ad710fb2b2032f1'},
}
INIT_SHA = '2cf52e1cee8d4f2050fe3a90eca0a0a8c50b6f30718d210667f7273f9f08ab12'
PREFIX_SHA = 'da4a8db3aba8255a78ed2b60a251c9835dfa0b5f471f1f0590fe09abb722b556'
MAX_ARCHIVE = 64 * 1024**2
MAX_INIT = 128 * 1024
# At peak: raw + worst-case gzip (257 MiB), partition (224 MiB),
# namespace/extracted boot files (<64 MiB), initrd/archive files (<80 MiB),
# logs/commands/manifest (<16 MiB). 1 GiB covers these plus headroom.
SCRATCH_BUDGET = 1024**3
DISK_FLOOR, MEMORY_FLOOR = 3000000000, 1000000000
EPOCH = 1793750400
RECIPES = ('build/protected-state-bootstrap.py', 'build/vgic-nvme-image.py',
           'build/vgic-runtime-bootstrap.py', 'build/vgic-host-sd.py',
           'build/assemble-host.py', 'build/verify-host-roots.py',
           'tests/verify_nvme_image.py', 'configs/host-init')


def output_path(path):
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts, 'absolute output path required')
    require(path.parent == REPO / 'out' and re.fullmatch(
        r'protected-state-bootstrap-[A-Za-z0-9][A-Za-z0-9_-]{0,63}', path.name),
        'output must be fresh out/protected-state-bootstrap-TAG')
    RUNTIME.safe_regular_path(path)
    require(not path.exists() and not path.is_symlink(), 'output already exists')
    return path


def resources(directory, admission=False):
    free = shutil.disk_usage(directory).free
    match = re.search(r'^MemAvailable:\s+(\d+) kB$', Path('/proc/meminfo').read_text(), re.M)
    require(match is not None, 'MemAvailable unavailable')
    memory = int(match[1]) * 1024
    require(free >= DISK_FLOOR + (SCRATCH_BUDGET if admission else 0), 'disk floor/scratch budget violated')
    require(memory >= MEMORY_FLOOR, 'available memory floor violated')
    return {'free_bytes': free, 'available_memory_bytes': memory}


def unwrap(data):
    require(len(data) >= 64, 'truncated legacy uInitrd')
    header = bytearray(data[:64])
    fields = struct.unpack('>7I4B32s', header)
    magic, hcrc, _, size, _, _, dcrc, _, _, kind, comp, _ = fields
    require(magic == 0x27051956 and kind == 3 and comp == 1, 'wrong legacy ramdisk header')
    header[4:8] = bytes(4)
    require(zlib.crc32(header) == hcrc, 'legacy header CRC mismatch')
    payload = data[64:]
    require(len(payload) == size and zlib.crc32(payload) == dcrc, 'legacy data size/CRC mismatch')
    decoder = zlib.decompressobj(31)
    archive = decoder.decompress(payload, MAX_ARCHIVE + 1)
    require(len(archive) <= MAX_ARCHIVE and decoder.eof and not decoder.unused_data
            and not decoder.unconsumed_tail, 'unbounded/truncated/multiple gzip member')
    return data[:64], archive


def entries(data):
    """Parse bounded newc; keep each original encoded record for closure proof."""
    require(len(data) <= MAX_ARCHIVE, 'archive size bound')
    result, offset = {}, 0
    while offset < len(data):
        start = offset
        header = data[offset:offset + 110]
        require(len(header) == 110 and header[:6] == b'070701'
                and re.fullmatch(rb'[0-9a-fA-F]{104}', header[6:]), 'invalid newc header')
        values = [int(header[i:i + 8], 16) for i in range(6, 110, 8)]
        size, namesize = values[6], values[11]
        require(1 < namesize <= 4096 and values[12] == 0, 'newc name/checksum bound')
        name_end = offset + 110 + namesize
        encoded = data[offset + 110:name_end]
        require(len(encoded) == namesize and encoded[-1:] == b'\0' and b'\0' not in encoded[:-1], 'newc name terminator')
        name = encoded[:-1].decode('ascii')
        require(name and not name.startswith('/') and all(p not in ('', '.', '..') for p in name.split('/'))
                and all(32 <= ord(c) < 127 for c in name), 'unsafe newc name')
        body = (name_end + 3) & ~3
        end = (body + size + 3) & ~3
        require(end <= len(data) and not any(data[name_end:body]) and not any(data[body + size:end]), 'newc truncation/padding')
        require(name not in result and len(result) < 4096, 'duplicate/excessive newc entries')
        result[name] = {'raw': data[start:end], 'header': header, 'prefix': data[start:body],
                        'data': data[body:body + size], 'fields': values}
        offset = end
        if name == 'TRAILER!!!':
            require(size == 0 and not any(data[offset:]), 'invalid newc trailer')
            return result, data[offset:]
    raise ValueError('missing newc trailer')


def replace_init(data, new_init):
    require(0 < len(new_init) <= MAX_INIT, 'init size bound')
    before, tail = entries(data)
    require('init' in before and stat.S_ISREG(before['init']['fields'][1])
            and before['init']['fields'][4] == 1, 'init must be regular and unlinked')
    old = before['init']
    prefix = bytearray(old['prefix'])
    prefix[54:62] = f'{len(new_init):08x}'.encode()
    replacement = bytes(prefix) + new_init + bytes(-len(new_init) % 4)
    candidate = b''.join(replacement if name == 'init' else item['raw']
                         for name, item in before.items()) + tail
    after, after_tail = entries(candidate)
    require(list(before) == list(after) and tail == after_tail, 'newc closure changed')
    for name in before:
        if name != 'init':
            require(before[name]['raw'] == after[name]['raw'], 'non-init record changed')
    expected = list(old['fields'])
    expected[6] = len(new_init)
    require(after['init']['fields'] == expected and after['init']['data'] == new_init, 'init metadata changed')
    return candidate, {'before_init': record(old['data']), 'after_init': record(new_init),
                       'preserved_records': {n: record(v['raw']) for n, v in before.items() if n != 'init'}}


def rewrap(header, archive):
    payload = gzip.compress(archive, compresslevel=9, mtime=0)
    header = bytearray(header)
    struct.pack_into('>I', header, 12, len(payload))
    struct.pack_into('>I', header, 24, zlib.crc32(payload))
    header[4:8] = bytes(4)
    struct.pack_into('>I', header, 4, zlib.crc32(header))
    result = bytes(header) + payload
    require(unwrap(result)[1] == archive, 'uInitrd readback failed')
    return result


def compare_namespace(before, after, initrd_record):
    expected = copy.deepcopy(before)
    expected['boot/uInitrd']['data'] = initrd_record
    expected['boot/uInitrd']['metadata']['size'] = initrd_record['bytes']
    require(after == expected, 'BOOT change exceeds uInitrd data/size')


def patch_boot(image, initrd, before, log):
    require(re.fullmatch(r'[A-Za-z0-9_./-]+', str(initrd)), 'unsafe scratch path')
    commands = ['rm /boot/uInitrd', f'write {initrd} /boot/uInitrd']
    for name, item in sorted(before.items()):
        path = '/' if name == '.' else '/' + name
        for field in ('uid', 'gid', 'mode'):
            value = item['metadata'][field]
            if field == 'mode':
                value |= stat.S_IFDIR if name in HELP.DIRECTORIES else stat.S_IFREG
            commands.append(f'set_inode_field {path} {field} {value}')
        for field, (seconds, extra) in item['times'].items():
            commands.extend((f'set_inode_field {path} {field} 0x{seconds:x}',
                             f'set_inode_field {path} {field}_extra 0x{extra:x}'))
    script = image.parent / 'patch.commands'
    script.write_text('\n'.join(commands) + '\n')
    with log.open('xb') as stream:
        subprocess.run(['debugfs', '-w', '-f', str(script), str(image)], check=True,
                       stdout=stream, stderr=subprocess.STDOUT, timeout=120,
                       env=dict(os.environ, LC_ALL='C', TZ='UTC', E2FSPROGS_FAKE_TIME=str(EPOCH)))
    subprocess.run(['e2fsck', '-nf', str(image)], check=True, capture_output=True, timeout=120,
                   env=dict(os.environ, LC_ALL='C'))


def build(base_sd, output):
    output = output_path(output)
    require(output.parent.is_dir(), 'repository out directory required')
    with ExitStack() as stack:
        RUNTIME.progress('Authenticate pinned regular SD and reviewed source closure')
        source = stack.enter_context(RUNTIME.HeldInput(base_sd, BASE['bytes'], BASE))
        held = {name: stack.enter_context(RUNTIME.HeldInput(REPO / name, 1024**2)) for name in RECIPES}
        init = held['configs/host-init'].read()
        require(record(init)['sha256'] == INIT_SHA, 'reviewed init source hash mismatch')
        before_resources = resources(output.parent, admission=True)
        VGIC.verify_layout(source.path)
        output.mkdir(mode=0o700)
        with tempfile.TemporaryDirectory(prefix='scratch-', dir=output) as temp:
            scratch = Path(temp)
            boot = scratch / 'boot.ext4'
            RUNTIME.progress('Extract regular BOOT filesystem and verify complete namespace')
            VGIC.copy_region(source.path, boot, VGIC.PARTITION_OFFSET, VGIC.PARTITION_BYTES)
            before = HELP.namespace(boot, scratch / 'namespace-before')
            for name, pin in SD_COMMANDS.items():
                require(before[name]['data'] == pin, 'SD command/script pin mismatch')
            for name in ('boot/uImage', HELP.DTB_PATH):
                expected = HELP.BOOT_FILES[name] if name == 'boot/uImage' else RUNTIME.PATCHED_DTB_RECORD
                require(before[name]['data'] == expected, 'kernel/VGIC pin mismatch')
            data = HELP.INSPECT.debugfs(boot, 'cat /boot/uInitrd', INITRD['bytes'])
            require(record(data) == INITRD, 'base uInitrd pin mismatch')
            header, archive = unwrap(data)
            members, _ = entries(archive)
            require(record(members['init']['data'])['sha256'] == OLD_INIT_SHA, 'old init pin mismatch')
            require(record(members['bin/busybox']['data'])['sha256'] == BUSYBOX_SHA, 'BusyBox pin mismatch')
            require(members['etc/a733-boot.conf']['data'] == (
                'STATE_UUID=' + RUNTIME.STATE_UUID + '\nKERNEL_RELEASE=6.6.98-a733-diag\n').encode(), 'STATE configuration mismatch')
            RUNTIME.progress('Replace only reviewed init data and prove CPIO closure')
            changed, proof = replace_init(archive, init)
            new_initrd = rewrap(header, changed)
            for name, payload in (('uInitrd', new_initrd), ('initramfs.cpio.gz', new_initrd[64:])):
                (output / name).write_bytes(payload)
            require(all(header[i] == new_initrd[i] for i in range(64)
                        if i not in set(range(4, 8)) | set(range(12, 16)) | set(range(24, 28))), 'legacy header fields changed')
            resources(output)
            RUNTIME.progress('Patch regular ext4 and preserve namespace metadata')
            patch_boot(boot, output / 'uInitrd', before, output / 'patch.log')
            after = HELP.namespace(boot, scratch / 'namespace-after')
            compare_namespace(before, after, record(new_initrd))
            candidate = output / 'a733-host-sd.img'
            shutil.copyfile(source.path, candidate)
            with candidate.open('r+b') as dst, boot.open('rb') as src:
                dst.seek(VGIC.PARTITION_OFFSET)
                shutil.copyfileobj(src, dst, 1024**2)
            VGIC.verify_layout(candidate)
            prefix = RUNTIME.compare_prefix(source.path, candidate)
            require(prefix['sha256'] == PREFIX_SHA, 'firmware prefix pin mismatch')
            # Read the partition from the completed raw, not just the pre-write scratch.
            boot.unlink()
            VGIC.copy_region(candidate, boot, VGIC.PARTITION_OFFSET, VGIC.PARTITION_BYTES)
            final = HELP.namespace(boot, scratch / 'namespace-final')
            compare_namespace(before, final, record(new_initrd))
            require(candidate.stat().st_size == BASE['bytes'], 'output raw size changed')
            RUNTIME.progress('Compress SD deterministically and verify complete expansion')
            resources(output)
            RUNTIME.deterministic_gzip(candidate, output / 'a733-host-sd.img.gz')
            source.verify()
            for item in held.values():
                item.verify()
            result = {'schema': 1, 'classification': 'software-only protected STATE init derivative; UNBOOTED',
                      'hardware_acceptance': False, 'physical_storage_written': False,
                      'board': 'Orange Pi 4 Pro A733', 'state_uuid': RUNTIME.STATE_UUID,
                      'base_sd': source.record, 'sources': {n: i.record for n, i in held.items()},
                      'layout': {'raw_bytes': BASE['bytes'], 'boot_offset': VGIC.PARTITION_OFFSET,
                                 'boot_bytes': VGIC.PARTITION_BYTES, 'firmware_prefix': prefix},
                      'initramfs_change': proof, 'namespace_before': before, 'namespace_after': final,
                      'permitted_change': 'init data/size in newc; uInitrd data/size in BOOT; ext4 allocation bookkeeping',
                      'legacy_header': {'before': header.hex(), 'after': new_initrd[:64].hex()},
                      'resources': {'before': before_resources, 'after': resources(output),
                                    'scratch_budget': SCRATCH_BUDGET, 'disk_floor': DISK_FLOOR,
                                    'memory_floor': MEMORY_FLOOR},
                      'gzip': {'mtime': 0, 'level': 9, 'filename': ''},
                      'files': {p.name: VGIC.file_record(p) for p in output.iterdir() if p.is_file()},
                      'tool_versions': {'python': os.sys.version,
                                        'debugfs': subprocess.run(['debugfs', '-V'], check=True,
                                                                 capture_output=True, text=True).stderr.strip()}}
        result['resources']['after_scratch_cleanup'] = resources(output)
        (output / 'manifest.json').write_text(json.dumps(result, indent=2) + '\n')
        RUNTIME.progress('Software derivation complete; hardware acceptance remains pending')
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-sd', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(build(args.base_sd, args.output), indent=2))
    except (ValueError, OSError, KeyError, UnicodeError, zlib.error, subprocess.SubprocessError) as error:
        parser.exit(1, f'protected STATE bootstrap failed: {error}\n')


if __name__ == '__main__':
    main()
