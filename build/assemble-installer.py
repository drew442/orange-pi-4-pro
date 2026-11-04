#!/usr/bin/env python3
"""Package an A733 installer preparation SD image in regular files only."""
import argparse
import gzip
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile

REPO = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, REPO / path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


HOST = module('installer_sd_host', 'build/assemble-host.py')
STATE = module('installer_sd_state', 'build/host-state.py')
KERNEL_RELEASE = HOST.KERNEL_RELEASE
MAX_INITRAMFS = HOST.MAX_INITRAMFS
BLOBS = HOST.BLOBS
ENTRIES = {'init': ('configs/installer-init', 0o100755),
           'etc/udhcpc.script': ('configs/installer-udhcpc.script', 0o100755),
           'usr/lib/a733-installer/install-nvme.py': ('scripts/install-nvme.py', 0o100644),
           'usr/lib/a733-installer/acquire-nvme.py': ('scripts/acquire-nvme.py', 0o100644),
           'usr/lib/a733-installer/plan-nvme.py': ('scripts/plan-nvme.py', 0o100644),
           'usr/lib/a733-installer/nvme_layout.py': ('scripts/nvme_layout.py', 0o100644)}
INITRAMFS_RECIPES = {'build/installer-initramfs.py', 'build/host-initramfs.py',
                    'build/host-payload.py', 'build/installer-runtime.py',
                    'build/assemble-host.py', 'build/host-state.py', 'build/root-selection.py',
                    'build/host-storage-tools.py', 'configs/host_config.py', 'configs/package_state.py',
                    'configs/installer-runtime-policy.json',
                    *(source for source, _ in ENTRIES.values()),
                    'licenses/GPL-2.0.txt', 'licenses/busybox-copyright.txt'}
RECIPES = ('build/assemble-installer.py', 'build/assemble-diagnostic.sh',
           'configs/installer-boot.cmd', 'configs/diagnostic-sources.lock.json',
           'configs/builder-packages.lock.tsv', 'docs/evidence/diagnostic-20261104.json')
CLASSIFICATION = 'installer preparation prototype; physical writer/boot/KVM acceptance pending'


def validate_manifest(manifest, diagnostic):
    if type(manifest.get('schema')) is not int or manifest['schema'] != 1:
        raise ValueError('installer manifest must use schema 1')
    if manifest.get('kernel_release') != KERNEL_RELEASE:
        raise ValueError('incompatible installer kernel ABI')
    for name in ('Image', 'board.dtb', 'kernel.config'):
        if manifest.get('boot_inputs', {}).get(name) != diagnostic[name]:
            raise ValueError(f'incompatible installer boot input: {name}')
    for name, (source, mode) in ENTRIES.items():
        expected = {'mode': mode, **HOST.record(HOST.read_regular(REPO / source, 1024**2))}
        if manifest.get('entries', {}).get(name) != expected:
            raise ValueError(f'missing or changed current repository initramfs entry: {name}')
    recipes = manifest.get('recipes', {})
    if not isinstance(recipes, dict) or not INITRAMFS_RECIPES <= set(recipes):
        raise ValueError('installer recipe provenance is incomplete')
    for name, expected in recipes.items():
        path = PurePosixPath(name)
        if not name or path.is_absolute() or '..' in path.parts or str(path) != name or name == '.':
            raise ValueError('unsafe or noncanonical recipe path')
        if HOST.record(HOST.read_regular(REPO / name, 4 * 1024**2)) != expected:
            raise ValueError(f'installer recipe provenance differs from current source: {name}')
    item = manifest.get('files', {}).get('initramfs.cpio.gz', {})
    if (type(item.get('bytes')) is not int or not 0 < item['bytes'] <= MAX_INITRAMFS or
            not isinstance(item.get('sha256'), str) or
            not re.fullmatch('[0-9a-f]{64}', item['sha256'])):
        raise ValueError('invalid initramfs hash/size record')
    return item


def build(diagnostic_directory, installer_directory, output_directory, boot_profile='baseline-el1'):
    pinned, profile, profile_recipes = HOST.load_boot_profile(boot_profile)
    raw_output = Path(output_directory)
    output = HOST.safe_path(raw_output)
    if (not raw_output.is_absolute() or raw_output != output or
            output.parent != REPO / 'out' or not output.name.startswith('installer-sd-') or
            output.name == 'installer-sd-'):
        raise ValueError('output must be a new absolute repo/out/installer-sd-NAME directory')
    if output.exists():
        raise ValueError('existing output refused')
    storage = STATE.validate_workspace_storage(output.parent if output.parent.exists() else REPO)
    diagnostic_directory = HOST.safe_path(diagnostic_directory)
    installer_directory = HOST.safe_path(installer_directory)
    for directory in (diagnostic_directory, installer_directory):
        if not directory.is_dir():
            raise ValueError(f'input must be a directory: {directory}')
    manifest_data = HOST.read_regular(installer_directory / 'manifest.json', 4 * 1024**2)
    installer = HOST.json_object(manifest_data)
    initramfs_record = validate_manifest(installer, pinned)
    recipe_data = {name: HOST.read_regular(REPO / name, 4 * 1024**2) for name in RECIPES}
    recipe_data.update(profile_recipes)
    recipes = {name: HOST.record(data) for name, data in recipe_data.items()}
    builder_packages = HOST.validate_builder_packages(REPO / 'configs/builder-packages.lock.tsv')
    output.parent.mkdir(exist_ok=True)
    output.mkdir(mode=0o700)
    with tempfile.TemporaryDirectory(prefix='.staging-', dir=output) as temporary:
        stage = Path(temporary)
        inputs = stage / 'inputs'
        inputs.mkdir(mode=0o700)
        sources = {}
        for name in BLOBS:
            value = HOST.read_regular(diagnostic_directory / name, pinned[name]['bytes'], pinned[name])
            (inputs / name).write_bytes(value)
            sources[name] = {'path': str(diagnostic_directory / name), **HOST.record(value)}
        archive = HOST.read_regular(installer_directory / 'initramfs.cpio.gz', MAX_INITRAMFS, initramfs_record)
        (inputs / 'initramfs.cpio.gz').write_bytes(archive)
        command = recipe_data['configs/installer-boot.cmd']
        (inputs / 'boot.cmd').write_bytes(command)
        # Execute the captured assembler whose bytes are recorded, with all of
        # its temporary files confined to the private fixed-storage directory.
        assembler = stage / 'assemble-diagnostic.sh'
        assembler.write_bytes(recipe_data['build/assemble-diagnostic.sh'])
        environment = dict(os.environ, SOURCE_DATE_EPOCH=HOST.EPOCH, TMPDIR=str(stage))
        image = output / 'a733-installer-prep.img'
        with (output / 'assembly.log').open('xb') as log:
            subprocess.run(['mkimage', '-A', 'arm', '-O', 'linux', '-T', 'ramdisk',
                            '-C', 'gzip', '-d', str(inputs / 'initramfs.cpio.gz'),
                            str(inputs / 'uInitrd')], env=environment, stdout=log,
                           stderr=subprocess.STDOUT, check=True)
            subprocess.run(['bash', str(assembler), str(inputs), str(image), str(inputs / 'boot.cmd')],
                           env=environment, stdout=log, stderr=subprocess.STDOUT, check=True)
        shutil.copyfile(inputs / 'uInitrd', output / 'uInitrd')
        if image.stat().st_size != 256 * 1024**2:
            raise ValueError('assembler returned incorrect image size')
        with image.open('rb') as source, (output / 'a733-installer-prep.img.gz').open('xb') as destination:
            with gzip.GzipFile(filename='', fileobj=destination, mode='wb', compresslevel=9, mtime=0) as compressed:
                shutil.copyfileobj(source, compressed)
        result = {'schema': 1, 'board': 'Orange Pi 4 Pro A733', 'classification': CLASSIFICATION,
                  'boot_profile': profile,
                  'kernel_release': KERNEL_RELEASE, 'source_date_epoch': int(HOST.EPOCH),
                  'boot_inputs': {name: pinned[name] for name in BLOBS}, 'sources': sources,
                  'installer_initramfs': {'path': str(installer_directory / 'initramfs.cpio.gz'), **HOST.record(archive)},
                  'trusted_installer_manifest': {'path': str(installer_directory / 'manifest.json'), **HOST.record(manifest_data)},
                  'source_bom': HOST.json_object(recipe_data['configs/diagnostic-sources.lock.json']),
                  'builder_packages': builder_packages, 'recipes': recipes,
                  'initramfs_recipes': installer['recipes'], 'boot_command': HOST.record(command),
                  'staging_storage': storage,
                  'staging_storage_guard_limit': 'kernel sdX/removable=0 cannot identify every USB SD reader; use known fixed builder storage',
                  'files': {name: HOST.file_record(output / name) for name in
                            ('a733-installer-prep.img', 'a733-installer-prep.img.gz', 'uInitrd', 'assembly.log')}}
        (output / 'manifest.json').write_text(json.dumps(result, indent=2) + '\n')
        return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('diagnostic_directory', 'installer_initramfs_directory', 'new_output_directory'):
        parser.add_argument(name)
    parser.add_argument('--boot-profile', choices=HOST.BOOT_PROFILES, default='baseline-el1')
    args = parser.parse_args()
    try:
        result = build(args.diagnostic_directory, args.installer_initramfs_directory, args.new_output_directory, boot_profile=args.boot_profile)
    except (ValueError, OSError, KeyError, TypeError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'installer SD packaging: {error}\n')
    print(json.dumps(result, indent=2))
