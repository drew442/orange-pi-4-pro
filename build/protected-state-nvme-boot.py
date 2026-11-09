#!/usr/bin/env python3
"""Derive a regular NVMe BOOT component from the pinned corrected wire image."""
import argparse
from contextlib import ExitStack
import gzip
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import zlib

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location('protected_nvme_sd', REPO / 'build/protected-state-bootstrap.py')
SD = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(SD)
HELP, RUNTIME, VGIC = SD.HELP, SD.RUNTIME, SD.VGIC
require, record = SD.require, SD.record
WIRE = {'bytes': 545632496, 'sha256': '3e560264bb64a9bc23b342febdff2146955ca97cb6e0279e5240c29d4e6d85bc'}
MANIFEST = {'bytes': 35023, 'sha256': 'addfcafe239a2aa075435383cc4742869d256d98beecd2526806da206d98f4a2'}
RAW = {'bytes': 256060514304, 'sha256': '835031b81d0320d6f9f35e74b9be67b494006c26a25fa9070f3e0a8f434ebc11'}
BASE_BOOT = {'bytes': 536870912, 'sha256': '5860889755d7479e619e86b83bc32237e51bacd3a3fd2e2d2ebe976898f44899'}
NEW_INITRD = {'bytes': 4763312, 'sha256': '2149513ae5cf90f1edb6047e6ebf8ba903dc86c55b4b43754a5a2cfd3368c786'}
BOOT_OFFSET, BOOT_BYTES = 1048576, 536870912
# One raw BOOT plus two worst-case gzip BOOT capsules (1537 MiB), bounded
# namespace/archive work (<160 MiB), with headroom within two GiB.
SCRATCH_BUDGET = 2 * 1024**3
RECIPES = ('build/protected-state-nvme-boot.py',) + SD.RECIPES


def output_path(path):
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts, 'absolute output path required')
    require(path.parent == REPO / 'out' and re.fullmatch(
        r'protected-state-nvme-boot-[A-Za-z0-9][A-Za-z0-9_-]{0,63}', path.name),
        'output must be fresh out/protected-state-nvme-boot-TAG')
    RUNTIME.safe_regular_path(path)
    require(not path.exists() and not path.is_symlink(), 'output already exists')
    return path


def resources(directory, admission=False):
    sample = SD.resources(directory)
    require(sample['free_bytes'] >= SD.DISK_FLOOR + (SCRATCH_BUDGET if admission else 0),
            'NVMe BOOT disk floor/scratch budget violated')
    return sample


def extract_boot(source, destination, offset=BOOT_OFFSET, length=BOOT_BYTES):
    """Decode only the BOOT prefix of an already authenticated complete gzip."""
    require(offset >= 0 and length > 0, 'invalid extraction extent')
    # dup shares position; reset because HeldInput hashing leaves it at EOF.
    os.lseek(source.fd, 0, os.SEEK_SET)
    with os.fdopen(os.dup(source.fd), 'rb') as wire:
        with gzip.GzipFile(fileobj=wire, mode='rb') as decoded, destination.open('xb') as target:
            for remaining, capture in ((offset, False), (length, True)):
                while remaining:
                    block = decoded.read(min(1024**2, remaining))
                    require(block, 'truncated gzip before BOOT end')
                    if capture:
                        target.write(block)
                    remaining -= len(block)
    require(destination.stat().st_size == length, 'incorrect BOOT extent size')


def validate_base(manifest, namespace):
    require(manifest['files']['nvme.img.gz'] == WIRE and manifest['files']['nvme.img'] == RAW,
            'incorrect corrected NVMe provenance')
    require(manifest['state_uuid'] == RUNTIME.STATE_UUID, 'STATE UUID mismatch')
    p1 = manifest['layout']['partitions'][0]
    require(p1['number'] == 1 and p1['name'] == 'boot' and
            p1['start_bytes'] == BOOT_OFFSET and p1['size_bytes'] == BOOT_BYTES,
            'NVMe BOOT extent mismatch')
    require(namespace == manifest['derivation']['complete_boot_namespace_after'],
            'complete BOOT namespace differs from pinned corrected construction')
    for name, pin in manifest['boot_files'].items():
        require(namespace[name]['data'] == pin, 'NVMe boot file pin mismatch')
    require(namespace[HELP.DTB_PATH]['data'] == RUNTIME.PATCHED_DTB_RECORD,
            'corrected VGIC DTB mismatch')


def derive_initrd(data, init):
    require(record(data) == SD.INITRD, 'base uInitrd pin mismatch')
    require(record(init)['sha256'] == SD.INIT_SHA, 'reviewed init source hash mismatch')
    header, archive = SD.unwrap(data)
    members, _ = SD.entries(archive)
    require(record(members['init']['data'])['sha256'] == SD.OLD_INIT_SHA, 'old init pin mismatch')
    require(record(members['bin/busybox']['data'])['sha256'] == SD.BUSYBOX_SHA, 'BusyBox pin mismatch')
    require(members['etc/a733-boot.conf']['data'] == (
        'STATE_UUID=' + RUNTIME.STATE_UUID + '\nKERNEL_RELEASE=6.6.98-a733-diag\n').encode(),
        'STATE configuration mismatch')
    changed, proof = SD.replace_init(archive, init)
    result = SD.rewrap(header, changed)
    allowed = set(range(4, 8)) | set(range(12, 16)) | set(range(24, 28))
    require(all(header[i] == result[i] for i in range(64) if i not in allowed), 'legacy header fields changed')
    return result, proof


def build(base_gzip, base_manifest, output):
    output = output_path(output)
    require(output.parent.is_dir(), 'repository out directory required')
    with ExitStack() as stack:
        RUNTIME.progress('Authenticate complete corrected gzip, historical manifest and source closure')
        source = stack.enter_context(RUNTIME.HeldInput(base_gzip, WIRE['bytes'], WIRE))
        authority = stack.enter_context(RUNTIME.HeldInput(base_manifest, MANIFEST['bytes'], MANIFEST))
        held = {name: stack.enter_context(RUNTIME.HeldInput(REPO / name, 1024**2)) for name in RECIPES}
        manifest = json.loads(authority.read())
        init = held['configs/host-init'].read()
        require(record(init)['sha256'] == SD.INIT_SHA, 'reviewed init source hash mismatch')
        admission = resources(output.parent, admission=True)
        output.mkdir(mode=0o700)
        with tempfile.TemporaryDirectory(prefix='scratch-', dir=output) as temp:
            scratch = Path(temp)
            boot = output / 'boot.ext4'
            RUNTIME.progress('Decode only authenticated NVMe BOOT prefix into regular file')
            extract_boot(source, boot)
            base_boot_record = VGIC.file_record(boot)
            require(base_boot_record == BASE_BOOT, 'corrected BOOT extent pin mismatch')
            before = HELP.namespace(boot, scratch / 'namespace-before')
            validate_base(manifest, before)
            RUNTIME.deterministic_gzip(boot, output / 'reverse-boot.ext4.gz')
            resources(output)
            data = HELP.INSPECT.debugfs(boot, 'cat /boot/uInitrd', SD.INITRD['bytes'])
            new_initrd, proof = derive_initrd(data, init)
            require(record(new_initrd) == NEW_INITRD, 'protected SD/NVMe uInitrd pair mismatch')
            (output / 'uInitrd').write_bytes(new_initrd)
            (output / 'initramfs.cpio.gz').write_bytes(new_initrd[64:])
            RUNTIME.progress('Replace only reviewed initrd; preserve NVMe commands and complete namespace')
            SD.patch_boot(boot, output / 'uInitrd', before, output / 'patch.log')
            after = HELP.namespace(boot, scratch / 'namespace-after')
            SD.compare_namespace(before, after, record(new_initrd))
            require(boot.stat().st_size == BOOT_BYTES, 'BOOT size changed')
            resources(output)
            RUNTIME.deterministic_gzip(boot, output / 'boot.ext4.gz')
            source.verify()
            authority.verify()
            for item in held.values():
                item.verify()
            result = {
                'schema': 1, 'classification': 'protected STATE NVMe BOOT component; UNBOOTED',
                'hardware_acceptance': False, 'physical_storage_written': False,
                'board': 'Orange Pi 4 Pro A733', 'state_uuid': RUNTIME.STATE_UUID,
                'base_gzip': source.record, 'base_manifest': authority.record,
                'historical_full_raw': RAW,
                'authentication': 'complete fixed compressed wire hash; bounded prefix decode; historical full-raw construction authority',
                'fresh_full_raw_validation': False,
                'source_manifest': manifest,
                'sources': {n: i.record for n, i in held.items()},
                'component_extent': {'offset_bytes': BOOT_OFFSET, 'bytes': BOOT_BYTES, 'partition': 1},
                'outside_boot': {'written': False, 'copied': False, 'freshly_verified': False},
                'reverse_boot_expanded': base_boot_record,
                'initramfs_change': proof,
                'legacy_header': {'before': data[:64].hex(), 'after': new_initrd[:64].hex()},
                'namespace_before': before, 'namespace_after': after,
                'permitted_change': 'init data/size in newc; uInitrd data/size in BOOT; ext4 allocation bookkeeping',
                'deployment': 'component only; separate reviewed offline updater and live backup required; never replace personalized full NVMe',
                'gzip': {'mtime': 0, 'level': 9, 'filename': ''},
                'resources': {'before': admission, 'after': resources(output), 'scratch_budget': SCRATCH_BUDGET,
                              'disk_floor': SD.DISK_FLOOR, 'memory_floor': SD.MEMORY_FLOOR},
                'files': {p.name: VGIC.file_record(p) for p in output.iterdir() if p.is_file()},
                'tool_versions': {'python': os.sys.version, 'debugfs': subprocess.run(
                    ['debugfs', '-V'], check=True, capture_output=True, text=True).stderr.strip()},
            }
        result['resources']['after_scratch_cleanup'] = resources(output)
        (output / 'manifest.json').write_text(json.dumps(result, indent=2) + '\n')
        RUNTIME.progress('NVMe BOOT component complete; independent review and physical acceptance pending')
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-gzip', required=True, type=Path)
    parser.add_argument('--base-manifest', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(build(args.base_gzip, args.base_manifest, args.output), indent=2))
    except (ValueError, OSError, KeyError, UnicodeError, zlib.error, subprocess.SubprocessError) as error:
        parser.exit(1, f'protected STATE NVMe BOOT failed: {error}\n')


if __name__ == '__main__':
    main()
