#!/usr/bin/env python3
"""Derive a regular-file NVMe image by changing only its BOOT DTB."""
import argparse
from contextlib import ExitStack
import copy
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import zlib

REPO = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, REPO / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RUNTIME = load('nvme_runtime', 'build/vgic-runtime-bootstrap.py')
VGIC = RUNTIME.VGIC
INSPECT = load('nvme_inspection', 'tests/verify_nvme_image.py')
READER = load('nvme_namespace', 'build/verify-host-roots.py')
CHUNK = 1024**2
BOOT_START, BOOT_BYTES = 1048576, 536870912
RAW = {'bytes': 256060514304, 'sha256': '0eda2415406e370ca1870f22ce7b2fd1e59620b52cf6c59894b5a2fbce005754'}
WIRE = {'bytes': 545632565, 'sha256': '7c44516aefb25b36db6342dc7cd9666e73b370e59b30fb3dc698bec4d93413c8'}
MANIFEST = {'bytes': 9393, 'sha256': '8d40099bb8a670d67bc62a9c6623edb1201903aef16e8c17c0d424233c4ce8a8'}
SD = {'bytes': 268435456, 'sha256': 'f02c74b4c001f1608d0005b6ff23fe82bffa8c02f71e19e5b6573819125ae383'}
SD_MANIFEST = {'bytes': 17471, 'sha256': '47df42be022d9e378a034df25d31865a24337c9309b4e4fdc2710ea9c2462993'}
DTB_PATH = 'boot/dtb/allwinner/sun60i-a733-orangepi-4-pro.dtb'
BOOT_FILES = {
    'boot/uImage': {'bytes': 27283528, 'sha256': '6f9269755c4c2bc196c094899fa1ff20292f4d762c4000f77fcbb0887d5f154a'},
    'boot/uInitrd': {'bytes': 4762436, 'sha256': 'b10bdad8671a32b4157bf6f7ff553618024fc928e59caf691b529c8658b12d16'},
    'boot/boot.cmd': {'bytes': 1315, 'sha256': 'c9f0db913fb49bbc5c54c5287049b8c56aaf17406fcdccf1b9fe0847ccce6c26'},
    'boot/boot.scr': {'bytes': 1387, 'sha256': 'd82fd8c465f12aa539146ee0843e1badb3eb983f243dc241c04c63b0db56c938'},
    DTB_PATH: RUNTIME.DTB_RECORD,
}
DIRECTORIES = {'.', 'lost+found', 'boot', 'boot/dtb', 'boot/dtb/allwinner'}
ADMISSION = 23760999827
DISK_FLOOR, MEMORY_FLOOR = 3000000000, 1000000000
NATIVE_FLOOR = 268435456
TIMEOUT = 10800
RECIPES = ('build/vgic-nvme-image.py', 'build/vgic-runtime-bootstrap.py',
           'build/vgic-host-sd.py', 'build/assemble-host.py', 'build/verify-host-roots.py',
           'tests/verify_nvme_image.py', 'configs/builder-packages.lock.tsv')


def require(condition, message):
    if not condition:
        raise ValueError(message)


class Monitor:
    def __init__(self, directory):
        self.directory = directory
        self.started = time.monotonic()
        self.last = 0
        self.minimum_free = self.minimum_memory = None

    def check(self, phase, count=0, force=False):
        now = time.monotonic()
        require(now - self.started < TIMEOUT, 'derivation exceeded 10800 second timeout')
        if force or now - self.last >= 15:
            free = shutil.disk_usage(self.directory).free
            memory = re.search(r'^MemAvailable:\s+(\d+) kB$', Path('/proc/meminfo').read_text(), re.M)
            require(memory is not None, 'MemAvailable unavailable')
            available = int(memory[1]) * 1024
            self.minimum_free = free if self.minimum_free is None else min(free, self.minimum_free)
            self.minimum_memory = available if self.minimum_memory is None else min(available, self.minimum_memory)
            require(free >= DISK_FLOOR and available >= MEMORY_FLOOR, 'runtime disk/memory floor violated')
            RUNTIME.progress(f'{phase}: {count} bytes; free={free}, MemAvailable={available}')
            self.last = now


def outside_update(digest, data, offset):
    end = offset + len(data)
    if offset < BOOT_START:
        digest.update(data[:min(len(data), BOOT_START - offset)])
    if end > BOOT_START + BOOT_BYTES:
        digest.update(data[max(0, BOOT_START + BOOT_BYTES - offset):])


def new_regular(path):
    return os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)


def write_all(fd, data, offset):
    while data:
        written = os.pwrite(fd, data, offset)
        require(written > 0, 'short write')
        offset += written
        data = data[written:]


def decode(source_fd, target_fd, expected, monitor):
    """Bounded single gzip member, CRC/EOF and full logical raw authentication."""
    os.lseek(source_fd, 0, os.SEEK_SET)
    decoder = zlib.decompressobj(31)
    whole, outside, total = hashlib.sha256(), hashlib.sha256(), 0
    while True:
        encoded = os.read(source_fd, CHUNK)
        if not encoded:
            break
        require(not decoder.eof, 'trailing gzip member/data')
        while encoded:
            data = decoder.decompress(encoded, CHUNK)
            encoded = decoder.unconsumed_tail
            require(not decoder.unused_data, 'trailing gzip member/data')
            require(total + len(data) <= expected['bytes'], 'expanded stream exceeds raw bound')
            whole.update(data)
            outside_update(outside, data, total)
            if target_fd is not None and data and data.count(0) != len(data):
                write_all(target_fd, data, total)
            total += len(data)
            monitor.check('decode', total)
    require(decoder.eof, 'truncated gzip/CRC trailer')
    require({'bytes': total, 'sha256': whole.hexdigest()} == expected, 'raw hash/size mismatch')
    if target_fd is not None:
        os.ftruncate(target_fd, total)
    return {'bytes': total - BOOT_BYTES, 'sha256': outside.hexdigest()}


def compress(source, target, monitor, expected_outside=None):
    whole, outside, total = hashlib.sha256(), hashlib.sha256(), 0
    with open(source, 'rb') as src, open(target, 'xb') as wire:
        with gzip.GzipFile(filename='', mode='wb', compresslevel=9, mtime=0, fileobj=wire) as dst:
            while data := src.read(CHUNK):
                whole.update(data)
                outside_update(outside, data, total)
                dst.write(data)
                total += len(data)
                monitor.check('compress', total)
    actual_outside = {'bytes': total - BOOT_BYTES, 'sha256': outside.hexdigest()}
    if expected_outside is not None:
        require(actual_outside == expected_outside, 'bytes outside BOOT changed')
    return {'bytes': total, 'sha256': whole.hexdigest()}


def namespace(image, work):
    """Read the complete fixed BOOT namespace, all content, xattrs and metadata."""
    work.mkdir()
    fd = os.open(image, os.O_RDONLY | os.O_NOFOLLOW)
    result = {}
    try:
        reader = READER.Reader(fd, work)
        pending = [('.', 2)]
        seen_inodes = set()
        while pending:
            name, inode = pending.pop()
            require(name not in result and inode not in seen_inodes, 'duplicate inode/path or hardlink')
            seen_inodes.add(inode)
            path = '/' if name == '.' else '/' + name
            details, xattrs = reader.batch([f'stat <{inode}>', f'ea_list <{inode}>'])
            meta = READER.parse_stat(details, inode)
            require(not READER.parse_xattrs(xattrs), 'unexpected BOOT xattrs')
            kind = 'directory' if name in DIRECTORIES else 'regular'
            require(name in DIRECTORIES | set(BOOT_FILES) and meta['kind'] == kind,
                    'unexpected BOOT namespace member/type: ' + name)
            times = {}
            for field in ('atime', 'mtime', 'ctime', 'crtime'):
                match = re.search(r'^\s*' + field + r': 0x([0-9a-f]+):([0-9a-f]+) --', details, re.M)
                require(match is not None, 'missing inode timestamp')
                times[field] = [int(match[1], 16), int(match[2], 16)]
            item = {'metadata': meta, 'times': times}
            if kind == 'directory':
                rows = READER.parse_listing(reader.batch([f'ls -p <{inode}>'])[0])
                require(rows['.']['ino'] == inode, 'incorrect dot entry')
                for child, info in rows.items():
                    if child in ('.', '..'):
                        continue
                    child_name = child if name == '.' else name + '/' + child
                    require(child_name in DIRECTORIES | set(BOOT_FILES), 'extra BOOT entry')
                    pending.append((child_name, info['ino']))
            else:
                require(meta['links'] == 1 and meta['size'] <= 128 * CHUNK, 'BOOT file bounds/links')
                item['data'] = VGIC.HOST.record(INSPECT.debugfs(image, 'cat ' + path, max(CHUNK, meta['size'])))
            result[name] = item
        require(set(result) == DIRECTORIES | set(BOOT_FILES), 'incomplete BOOT namespace')
        return result
    finally:
        os.close(fd)


def compare_namespace(before, after):
    expected = copy.deepcopy(before)
    expected[DTB_PATH]['data'] = dict(RUNTIME.PATCHED_DTB_RECORD)
    expected[DTB_PATH]['metadata']['size'] = RUNTIME.PATCHED_DTB_RECORD['bytes']
    require(after == expected, 'BOOT namespace/content/metadata delta exceeds DTB')


def patch_boot(image, dtb, before, epoch, log):
    require(re.fullmatch(r'[A-Za-z0-9_./-]+', str(dtb)), 'unsafe scratch DTB path')
    commands = ['rm /' + DTB_PATH, f'write {dtb} /{DTB_PATH}']
    for name, item in sorted(before.items()):
        path = '/' if name == '.' else '/' + name
        metadata = item['metadata']
        for field in ('uid', 'gid', 'mode'):
            value = metadata[field]
            if field == 'mode':
                value = (stat.S_IFDIR if name in DIRECTORIES else stat.S_IFREG) | value
            commands.append(f'set_inode_field {path} {field} {value}')
        for field, (seconds, extra) in item['times'].items():
            commands.extend((f'set_inode_field {path} {field} 0x{seconds:x}',
                             f'set_inode_field {path} {field}_extra 0x{extra:x}'))
    script = image.parent / 'patch.commands'
    script.write_text('\n'.join(commands) + '\n')
    with open(log, 'xb') as stream:
        completed = subprocess.run(['debugfs', '-w', '-f', str(script), str(image)],
                                   stdout=stream, stderr=subprocess.STDOUT, timeout=120,
                                   env=dict(os.environ, LC_ALL='C', TZ='UTC', E2FSPROGS_FAKE_TIME=str(epoch)))
    require(completed.returncode == 0, 'BOOT patch failed')
    subprocess.run(['e2fsck', '-nf', str(image)], check=True, stdout=subprocess.PIPE,
                   stderr=subprocess.STDOUT, timeout=120, env=dict(os.environ, LC_ALL='C'))


def replace_boot(raw_fd, image):
    require(stat.S_ISREG(os.fstat(raw_fd).st_mode) and os.fstat(raw_fd).st_nlink == 1,
            'writeback target must be private regular file')
    require(0 <= BOOT_START and 0 < BOOT_BYTES and BOOT_START + BOOT_BYTES <= os.fstat(raw_fd).st_size,
            'BOOT writeback bounds')
    require(image.stat().st_size == BOOT_BYTES and stat.S_ISREG(image.lstat().st_mode), 'BOOT scratch size/type')
    # Punch before skipping zero chunks: otherwise stale original blocks survive.
    subprocess.run(['fallocate', '--punch-hole', '--keep-size', '--offset', str(BOOT_START),
                    '--length', str(BOOT_BYTES), '/proc/self/fd/' + str(raw_fd)],
                   pass_fds=(raw_fd,), check=True, timeout=120)
    with open(image, 'rb') as source:
        offset = 0
        while data := source.read(CHUNK):
            if data.count(0) != len(data):
                write_all(raw_fd, data, BOOT_START + offset)
            offset += len(data)
        require(offset == BOOT_BYTES, 'short BOOT writeback')


def validate_manifests(base, paired):
    require(base['state_uuid'] == paired['state_uuid'] == RUNTIME.STATE_UUID, 'state mismatch')
    require(base['files']['nvme.img'] == RAW and base['boot_files'] == BOOT_FILES, 'base raw/boot pins differ')
    require(base['inputs']['sd_manifest'] == RUNTIME.MANIFEST_RECORD, 'base SD provenance differs')
    require(paired['base_bootstrap']['manifest'] == RUNTIME.MANIFEST_RECORD, 'paired SD parent differs')
    require(paired['files']['a733-host-sd.img'] == SD and
            paired['boot_inputs']['board.dtb'] == RUNTIME.PATCHED_DTB_RECORD and
            paired['boot_profile']['name'] == RUNTIME.PROFILE, 'paired runtime profile/DTB differs')
    require(base['boot_profile']['name'] == 'experimental-el2', 'unexpected original runtime profile')


def preserved_metadata(base):
    """Carry the unchanged layout and installation identity into the derivation."""
    return {key: copy.deepcopy(base[key]) for key in
            ('schema', 'state_uuid', 'kernel_release', 'deployment_intent',
             'logical_sector_bytes', 'physical_sector_bytes', 'disk_guid', 'partition_guids',
             'filesystem_uuids', 'layout', 'gpt_extents', 'boot_selection', 'partition_payloads')}


def extract_sd_dtb(image, destination):
    destination.mkdir()
    partition = destination / 'partition.ext4'
    VGIC.copy_region(image, partition, VGIC.PARTITION_OFFSET, VGIC.PARTITION_BYTES)
    target = destination / 'board.dtb'
    # The SD is authenticated in full; extract only the required DTB, bounded in time.
    target.write_bytes(INSPECT.debugfs(partition, 'cat ' + VGIC.FILES['board.dtb'],
                                     RUNTIME.PATCHED_DTB_RECORD['bytes']))
    partition.unlink()
    return target


def build(gzip_path, manifest_path, sd_path, sd_manifest_path, name):
    require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', name), 'invalid fresh output tag')
    output = VGIC.HOST.safe_path(REPO / 'out' / ('nvme-vgic-' + name))
    require(not output.exists() and not output.is_symlink(), 'output tag already reserved')
    output.parent.mkdir(exist_ok=True)
    require(shutil.disk_usage(output.parent).free >= ADMISSION, 'initial admission requires 23760999827 free bytes')
    monitor = Monitor(output.parent)
    monitor.check('admission', force=True)
    with ExitStack() as stack:
        held = []
        def hold(path, pin=None):
            item = stack.enter_context(RUNTIME.HeldInput(path, pin['bytes'] if pin else CHUNK, pin))
            require(all(item.initial_identity[:2] != prior.initial_identity[:2] for prior in held), 'aliased inputs')
            held.append(item)
            return item
        source = hold(gzip_path, WIRE)
        original = hold(manifest_path, MANIFEST)
        sd = hold(sd_path, SD)
        sd_manifest = hold(sd_manifest_path, SD_MANIFEST)
        base, paired = json.loads(original.read()), json.loads(sd_manifest.read())
        validate_manifests(base, paired)
        recipes = {path: hold(REPO / path).record for path in RECIPES}
        require(recipes['configs/builder-packages.lock.tsv'] == base['recipes']['configs/builder-packages.lock.tsv'], 'original builder lock differs')
        packages = VGIC.HOST.validate_builder_packages(REPO / 'configs/builder-packages.lock.tsv')
        versions = {tool: subprocess.check_output([tool, flag], stderr=subprocess.STDOUT, text=True, timeout=30).strip()
                    for tool, flag in (('debugfs', '-V'), ('e2fsck', '-V'), ('fallocate', '--version'))}
        versions['python'] = sys.version
        versions['zlib'] = zlib.ZLIB_RUNTIME_VERSION
        output.mkdir(mode=0o700)
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix='scratch-', dir=output) as scratch_name:
            scratch = Path(scratch_name)
            raw = output / 'nvme.img'
            raw_fd = new_regular(raw)
            try:
                outside = decode(source.fd, raw_fd, RAW, monitor)
                INSPECT.inspect_gpt(raw_fd, base, RAW['bytes'])
                boot = scratch / 'boot.ext4'
                INSPECT.sparse_capture(raw_fd, BOOT_START, BOOT_BYTES, boot)
                before = namespace(boot, scratch / 'before')
                require({key: before[key]['data'] for key in BOOT_FILES} == BOOT_FILES, 'actual BOOT files differ')
                # Retain the complete original BOOT as the exact reverse patch.
                original_boot_record = compress(boot, output / 'base-boot.ext4.gz', monitor)
                sd_extract = scratch / 'sd'
                VGIC.verify_layout(sd.path)
                new_dtb = extract_sd_dtb(sd.path, sd_extract)
                VGIC.require_record(new_dtb, RUNTIME.PATCHED_DTB_RECORD)
                old_dtb = INSPECT.debugfs(boot, 'cat /' + DTB_PATH, RUNTIME.DTB_RECORD['bytes'])
                VGIC.verify_delta(old_dtb, new_dtb.read_bytes())
                patch_boot(boot, new_dtb, before, paired['source_date_epoch'], output / 'patch.log')
                after = namespace(boot, scratch / 'after')
                compare_namespace(before, after)
                replace_boot(raw_fd, boot)
                INSPECT.inspect_gpt(raw_fd, base, RAW['bytes'])
                # Read back the actual written region, not just the patch scratch.
                readback = scratch / 'readback.ext4'
                INSPECT.sparse_capture(raw_fd, BOOT_START, BOOT_BYTES, readback)
                require(VGIC.file_record(readback) == VGIC.file_record(boot), 'written BOOT differs')
                new_raw = compress(raw, output / 'nvme.img.gz', monitor, outside)
                with open(output / 'nvme.img.gz', 'rb') as stream:
                    require(decode(stream.fileno(), None, new_raw, monitor) == outside, 'gzip readback outside proof differs')
                for item in held:
                    monitor.check('reauthenticate', force=True)
                    item.verify()
                for filename, data in (('base-nvme-manifest.json', original.read()), ('paired-sd-manifest.json', sd_manifest.read())):
                    with open(output / filename, 'xb') as stream:
                        stream.write(data)
                files = {name: VGIC.file_record(output / name) for name in
                         ('nvme.img.gz', 'base-boot.ext4.gz', 'base-nvme-manifest.json', 'paired-sd-manifest.json', 'patch.log')}
                files['nvme.img'] = new_raw
                monitor.check('complete', force=True)
                result = preserved_metadata(base)
                result.update(board='Orange Pi 4 Pro A733', source_date_epoch=paired['source_date_epoch'],
                              classification='BOOT DTB-only NVMe derivation; UNBOOTED',
                              boot_profile=copy.deepcopy(paired['boot_profile']),
                              runtime_profile=RUNTIME.PROFILE,
                              nvme_boot_command_profile={**copy.deepcopy(base['nvme_boot_command_profile']),
                                                         'permitted_runtime_profile': RUNTIME.PROFILE,
                                                         'source_unchanged': True},
                              boot_command_policy={'family': 'spi-nvme-v1', 'source_unchanged': True,
                                                   'permitted_runtime_profile': RUNTIME.PROFILE},
                              boot_files={**BOOT_FILES, DTB_PATH: RUNTIME.PATCHED_DTB_RECORD},
                              base_nvme={'gzip': WIRE, 'raw': RAW, 'manifest': MANIFEST,
                                         'original_manifest': base},
                              inputs={'gzip': {'path': str(source.path), **WIRE},
                                      'manifest': {'path': str(original.path), **MANIFEST},
                                      'sd_image': {'path': str(sd.path), **SD},
                                      'sd_manifest': {'path': str(sd_manifest.path), **SD_MANIFEST}},
                              recipes=recipes, builder_packages=packages, tool_versions=versions,
                              files=files, build_seconds=time.monotonic() - started,
                              allocated_bytes=raw.stat().st_blocks * 512,
                              hardware_acceptance=False, physical_storage_written=False,
                              derivation={'outside_boot': outside, 'outside_boot_identical': True,
                                          'gpt_unchanged': True, 'base_crc_eof_raw_hash_verified': True,
                                          'output_gzip_full_expansion_verified': True,
                                          'complete_boot_namespace_before': before,
                                          'complete_boot_namespace_after': after,
                                          'root_embedded_dtb_source': RUNTIME.DTB_RECORD,
                                          'runtime_dtb': RUNTIME.PATCHED_DTB_RECORD,
                                          'all_inputs_held_reauthenticated': True,
                                          'reverse_patch': {'path': 'base-boot.ext4.gz', 'expanded': original_boot_record},
                                          'receipt_policy': 'fixed wire/raw/original-manifest authority; historical receipt not reread',
                                          'gzip': {'level': 9, 'mtime': 0, 'filename': ''},
                                          'minimum_free_bytes': monitor.minimum_free,
                                          'minimum_memavailable_bytes': monitor.minimum_memory})
                with open(output / 'manifest.json', 'x') as stream:
                    json.dump(result, stream, indent=2)
                    stream.write('\n')
                return result
            finally:
                os.close(raw_fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('gzip', 'manifest', 'sd-image', 'sd-manifest'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--name', required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(build(args.gzip, args.manifest, args.sd_image, args.sd_manifest, args.name), indent=2))
    except (ValueError, OSError, KeyError, TypeError, zlib.error, subprocess.SubprocessError) as error:
        parser.exit(1, f'VGIC NVMe derivation failed: {error}\n')


if __name__ == '__main__':
    main()
