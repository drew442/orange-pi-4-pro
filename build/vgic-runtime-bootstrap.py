#!/usr/bin/env python3
"""Derive a regular-file, DT-only VGIC bootstrap for corrected state360d."""
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

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location('runtime_vgic_helpers', REPO / 'build/vgic-host-sd.py')
VGIC = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(VGIC)
HOST = VGIC.HOST
IMAGE_RECORD = {'bytes': 268435456, 'sha256': '1063c591c3955e1e8531c8b22960bc09eb080024a6e79d331846572c3e0d2f76'}
MANIFEST_RECORD = {'bytes': 8794, 'sha256': '23d80141db14245d3f832440416131e818fba7b73f1a64a3147365a60a8007c2'}
DTB_RECORD = {'bytes': 209320, 'sha256': '4d7d964a54668cf5dc12cd4bf7e72a625c79883541272aeba1a91adbbedb86ed'}
PATCHED_DTB_RECORD = {'bytes': 209344, 'sha256': '1ba8f5926c0d76d12479bafcfcfce66be4a934ccfb6a5bcbcc7f7aa86c07f9f4'}
STATE_UUID = '360dfb39-f49f-487e-b307-0bb887c854c5'
PROFILE = 'experimental-el2-vgic-v1'
BASE_REVISION = '0a3d2a1ff78108e445d1c832362b2667897a9c9e'
SD_BYTES = 1977614336
FREE_FLOOR = 256 * 1024**2
# Includes assembly's partition+disk, extraction, payloads, gzip and final output.
SCRATCH_BUDGET = 1024**3
RECIPES = ('build/vgic-runtime-bootstrap.py', 'build/vgic-host-sd.py',
           'build/assemble-host.py', 'build/assemble-diagnostic.sh',
           'configs/builder-packages.lock.tsv')


def progress(message):
    print(message, file=sys.stderr, flush=True)


def safe_regular_path(path):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('input path must be absolute without parent traversal')
    if any(path == Path(root) or Path(root) in path.parents for root in ('/dev', '/proc', '/sys')):
        raise ValueError('device/proc/sys paths forbidden')
    return HOST.safe_path(path)


def identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class HeldInput:
    """Hold and reauthenticate inputs in a trusted, quiescent checkout."""
    def __init__(self, path, maximum, expected=None):
        self.path = safe_regular_path(path)
        self.fd = os.open(self.path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        try:
            info = os.fstat(self.fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError('input must be a regular file without hardlinks')
            if not 0 < info.st_size <= maximum:
                raise ValueError('input exceeds size bound')
            self.initial_identity = identity(info)
            self.record = self.hash()
            if expected is not None and self.record != expected:
                raise ValueError(f'input hash/size mismatch: {self.path}')
            self.verify()
        except BaseException:
            os.close(self.fd)
            raise

    def hash(self):
        digest, size = hashlib.sha256(), 0
        os.lseek(self.fd, 0, os.SEEK_SET)
        while True:
            data = os.read(self.fd, 1024**2)
            if not data:
                break
            size += len(data)
            if size > self.initial_identity[4]:
                raise ValueError('input grew while hashing')
            digest.update(data)
        return {'bytes': size, 'sha256': digest.hexdigest()}

    def read(self):
        os.lseek(self.fd, 0, os.SEEK_SET)
        data = os.read(self.fd, self.record['bytes'] + 1)
        if HOST.record(data) != self.record:
            raise ValueError('input changed while reading')
        return data

    def verify(self):
        safe_regular_path(self.path)
        if (identity(os.fstat(self.fd)) != self.initial_identity or
                identity(self.path.lstat()) != self.initial_identity or self.hash() != self.record or
                identity(os.fstat(self.fd)) != self.initial_identity or
                identity(self.path.lstat()) != self.initial_identity):
            raise ValueError(f'input changed: {self.path}')

    def __enter__(self):
        return self

    def __exit__(self, *args):
        os.close(self.fd)


def output_path(name):
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}', name):
        raise ValueError('name must be 1..64 letters/digits/underscores/hyphens')
    path = HOST.safe_path(REPO / 'out' / ('host-sd-vgic-runtime-' + name))
    if path.exists() or path.is_symlink():
        raise ValueError('output must not exist; failed tags cannot be reused')
    return path


def validate_base(manifest):
    if (manifest.get('state_uuid') != STATE_UUID or
            manifest.get('boot_profile', {}).get('name') != 'experimental-el2' or
            manifest.get('files', {}).get('a733-host-sd.img') != IMAGE_RECORD or
            manifest.get('boot_inputs', {}).get('board.dtb') != DTB_RECORD):
        raise ValueError('incorrect corrected bootstrap base/profile/state')


def compare_prefix(before, after, length=VGIC.PARTITION_OFFSET):
    with open(before, 'rb') as source, open(after, 'rb') as candidate:
        digest = hashlib.sha256()
        remaining = length
        while remaining:
            data = source.read(min(1024**2, remaining))
            if not data or candidate.read(len(data)) != data:
                raise ValueError('complete first 32 MiB changed')
            digest.update(data)
            remaining -= len(data)
    return {'bytes': length, 'sha256': digest.hexdigest()}


def deterministic_gzip(source, target):
    with open(source, 'rb') as src, open(target, 'xb') as raw:
        with gzip.GzipFile(filename='', mode='wb', compresslevel=9, mtime=0, fileobj=raw) as dst:
            shutil.copyfileobj(src, dst, 1024**2)
    digest, size = hashlib.sha256(), 0
    with gzip.open(target, 'rb') as stream:
        for data in iter(lambda: stream.read(1024**2), b''):
            digest.update(data)
            size += len(data)
    if {'bytes': size, 'sha256': digest.hexdigest()} != VGIC.file_record(source):
        raise ValueError('gzip expansion differs from image')


def tool_versions():
    versions = {}
    for command in (('fdtput', '-V'), ('debugfs', '-V'), ('mkimage', '-V'),
                    ('mke2fs', '-V'), ('sfdisk', '--version'), ('bash', '--version')):
        completed = subprocess.run(command, check=True, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True)
        versions[command[0]] = completed.stdout.strip()
    versions['python'] = sys.version
    return versions


def result_manifest(base, original_data, recipes, packages, outputs, preserved, prefix):
    # Only current payload descriptors remain at top level. Historical producer
    # paths/recipes and root source DTB pins stay intact inside base_bootstrap.
    result = {key: copy.deepcopy(base[key]) for key in (
        'schema', 'board', 'state_uuid', 'kernel_release', 'package_state_profile',
        'root_selection', 'source_date_epoch', 'boot_command')}
    result.update({
        'classification': 'Corrected state360d DT-only VGIC derivation; UNBOOTED',
        'boot_profile': {'name': PROFILE, 'target_service': '0x8000ff40',
                         'expected_spsr': '0x3c9', 'physical_acceptance_pending': True,
                         'dtb': PATCHED_DTB_RECORD},
        'boot_inputs': {key: copy.deepcopy(base['boot_inputs'][key]) for key in
                        ('boot0_sdcard.fex', 'boot_package.fex', 'uImage')},
        'base_bootstrap': {'source_revision': BASE_REVISION,
                           'image': IMAGE_RECORD, 'manifest': MANIFEST_RECORD,
                           'boot_profile': copy.deepcopy(base['boot_profile']),
                           'original_manifest': copy.deepcopy(base),
                           'original_manifest_file': {'path': 'base-bootstrap-manifest.json',
                                                      **HOST.record(original_data)}},
        'recipes': recipes, 'builder_packages': packages, 'files': outputs,
        'hardware_acceptance': False, 'physical_storage_written': False,
        'derivation': {'semantic_change': {'node': VGIC.NODE, 'property': 'interrupts', 'cells': [1, 9, 4]},
                       'dtb_before': DTB_RECORD, 'dtb_after': PATCHED_DTB_RECORD,
                       'semantic_proof': 'all FDT nodes/properties/reservations/boot CPU compared; only interrupts added',
                       'preserved_files': preserved, 'identical_complete_prefix': prefix,
                       'output_readback': 'layout, complete prefix, extracted boot files, DT semantics and gzip expansion passed',
                       'base_inputs_held_and_reauthenticated': True,
                       'source_patch_url': VGIC.PATCH_URL,
                       'root_embedded_dtb': 'unchanged original source pin; roots not modified',
                       'gzip': {'mtime': 0, 'filename': '', 'level': 9},
                       'image_fits_actual_sd': outputs['a733-host-sd.img']['bytes'] <= SD_BYTES}})
    result['boot_inputs']['board.dtb'] = dict(PATCHED_DTB_RECORD)
    return result


def build(image, manifest_path, name):
    output = output_path(name)
    with ExitStack() as stack:
        held = []

        def hold(path, maximum, expected=None):
            item = stack.enter_context(HeldInput(path, maximum, expected))
            held.append(item)
            return item

        progress('Authenticate corrected bootstrap and local source closure')
        source = hold(image, IMAGE_RECORD['bytes'], IMAGE_RECORD)
        manifest_input = hold(manifest_path, MANIFEST_RECORD['bytes'], MANIFEST_RECORD)
        original_data = manifest_input.read()
        base = HOST.json_object(original_data)
        validate_base(base)
        VGIC.verify_layout(source.path)
        recipes = {path: hold(REPO / path, 1024**2).record for path in RECIPES}
        if recipes['configs/builder-packages.lock.tsv'] != base['recipes']['configs/builder-packages.lock.tsv']:
            raise ValueError('builder lock differs from original bootstrap')
        packages = VGIC.builder_packages()
        versions = tool_versions()
        output.parent.mkdir(exist_ok=True)
        if shutil.disk_usage(output.parent).free < FREE_FLOOR + SCRATCH_BUDGET:
            raise ValueError('need 1 GiB scratch plus retained 256 MiB free-space floor')
        output.mkdir(mode=0o700)
        with tempfile.TemporaryDirectory(prefix='scratch-', dir=output) as scratch_name:
            scratch = Path(scratch_name)
            original = scratch / 'original'
            progress('Extract base boot files and authenticate payloads')
            VGIC.extract(source.path, original)
            for blob in ('uImage', 'board.dtb'):
                VGIC.require_record(original / blob, base['boot_inputs'][blob])
            VGIC.require_record(original / 'uInitrd', base['files']['uInitrd'])
            VGIC.require_record(original / 'boot.cmd', base['boot_command'])
            inputs = scratch / 'inputs'
            inputs.mkdir()
            for blob, offset in (('boot0_sdcard.fex', 8192), ('boot_package.fex', 16793600)):
                pin = base['boot_inputs'][blob]
                VGIC.copy_region(source.path, inputs / blob, offset, pin['bytes'])
                VGIC.require_record(inputs / blob, pin)
            for blob in ('uImage', 'uInitrd', 'board.dtb'):
                shutil.copyfile(original / blob, inputs / blob)
            progress('Patch only the GIC maintenance interrupt and prove semantic delta')
            subprocess.run(['fdtput', '-t', 'i', str(inputs / 'board.dtb'), VGIC.NODE,
                            'interrupts', '1', '9', '4'], check=True)
            VGIC.require_record(inputs / 'board.dtb', PATCHED_DTB_RECORD)
            VGIC.verify_delta((original / 'board.dtb').read_bytes(), (inputs / 'board.dtb').read_bytes())
            candidate = output / 'a733-host-sd.img'
            progress('Assemble new regular-file SD (packaging output in assembly.log)')
            with open(output / 'assembly.log', 'xb') as log:
                subprocess.run(['bash', str(REPO / 'build/assemble-diagnostic.sh'), str(inputs),
                                str(candidate), str(original / 'boot.cmd')],
                               env=dict(os.environ, TMPDIR=str(scratch), LC_ALL='C', TZ='UTC'),
                               stdout=log, stderr=subprocess.STDOUT, check=True)
            progress('Read back layout, complete prefix, boot files and DTB')
            VGIC.verify_layout(candidate)
            prefix = compare_prefix(source.path, candidate)
            rebuilt = scratch / 'rebuilt'
            VGIC.extract(candidate, rebuilt)
            VGIC.verify_boot_files(original, rebuilt)
            VGIC.require_record(rebuilt / 'board.dtb', PATCHED_DTB_RECORD)
            shutil.copyfile(rebuilt / 'uInitrd', output / 'uInitrd')
            preserved = {blob: VGIC.file_record(rebuilt / blob) for blob in VGIC.FILES if blob != 'board.dtb'}
            progress('Write deterministic gzip and verify complete expansion')
            deterministic_gzip(candidate, output / 'a733-host-sd.img.gz')
            with open(output / 'base-bootstrap-manifest.json', 'xb') as stream:
                stream.write(original_data)
            outputs = {blob: VGIC.file_record(output / blob) for blob in
                       ('a733-host-sd.img', 'a733-host-sd.img.gz', 'uInitrd',
                        'base-bootstrap-manifest.json', 'assembly.log')}
            for item in held:
                item.verify()
            result = result_manifest(base, original_data, recipes, packages, outputs, preserved, prefix)
            result['tool_versions'] = versions
            result['derivation']['inputs'] = {str(item.path): item.record for item in held[:2]}
            with open(output / 'manifest.json', 'x') as stream:
                json.dump(result, stream, indent=2)
                stream.write('\n')
        progress('Completed regular-file derivation; corrected state360d artifact remains UNBOOTED')
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--name', required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(build(args.image, args.manifest, args.name), indent=2))
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'VGIC runtime bootstrap failed: {error}\n')


if __name__ == '__main__':
    main()
