#!/usr/bin/env python3
"""Replay the two deployment SD packages inside the frozen, offline Jammy builder.

This is the inner recipe: the trusted outer launcher supplies read-only inputs,
read-only OS, private UID-0 namespace and writable disk-backed /var/tmp. It never
compiles a kernel, mounts filesystems, accesses block devices or publishes images.
"""
import argparse
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile

REPO = Path(__file__).resolve().parents[1]
PROFILE = 'replay-20261106'
ENVIRONMENT = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/nonexistent',
               'LANG': 'C', 'LC_ALL': 'C', 'TZ': 'UTC',
               'SOURCE_DATE_EPOCH': '1793750400', 'GIT_CONFIG_NOSYSTEM': '1',
               'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_OPTIONAL_LOCKS': '0'}
LEGACY_HASHES = {
    'build/diagnostic-initramfs.sh': '5d1f4bb176709ae7117646401c423b7a6dfc766db2ba1a53bdf0c38565230329',
    'configs/diagnostic-init': '1202d293c25ac3b8aa14ca712586dc004a02135fbff49cd34928d79d5ff457aa'}
LEGACY_FILES = {*LEGACY_HASHES, 'licenses/GPL-2.0.txt', 'licenses/busybox-copyright.txt'}


def module(name, relative):
    spec = importlib.util.spec_from_file_location(name, REPO / relative)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


HELPER = module('sd_replay_uboot', 'build/uboot-replay.py')
STATE = module('sd_replay_state', 'build/host-state.py')
require = HELPER.require
safe_path = HELPER.safe_path
facts = HELPER.facts


@contextmanager
def private_environment(mask=0o022):
    previous = dict(os.environ)
    previous_mask = os.umask(mask)
    os.environ.clear()
    os.environ.update(ENVIRONMENT)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(previous)
        os.umask(previous_mask)


@contextmanager
def umask(mask):
    previous = os.umask(mask)
    try:
        yield
    finally:
        os.umask(previous)


def authenticate(path, expected):
    actual = facts(path)
    require(actual == expected, f'Untrusted input: {path}')
    return actual


def project_source():
    project = HELPER.project_source(REPO)
    command = ['git', '--no-optional-locks', '-C', REPO]
    # Check every committed regular file, including this recipe and its complete
    # transitive recipe/config/evidence inputs. This also defeats index flags.
    names = subprocess.check_output(command + ['ls-tree', '-rz', '--full-tree', 'HEAD'])
    inputs = {}
    for item in names.split(b'\0'):
        if not item:
            continue
        metadata, raw_name = item.split(b'\t', 1)
        mode, kind, object_id = metadata.split()
        name = raw_name.decode()
        require(kind == b'blob' and mode in (b'100644', b'100755'),
                f'Project requires committed regular files: {name}')
        committed = subprocess.check_output(command + ['cat-file', 'blob', object_id.decode()])
        expected = {'bytes': len(committed), 'sha256': hashlib.sha256(committed).hexdigest()}
        authenticate(REPO / name, expected)
        inputs[name] = expected
    require('build/replay-sd-images.py' in inputs, 'Replay recipe must be committed in HEAD')
    project['inputs'] = inputs
    return project


def output_paths(tag):
    require(isinstance(tag, str) and len(tag) <= 64 and
            re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', tag), 'Invalid replay tag')
    names = {'group': 'sd-replay-', 'runtime': 'installer-runtime-',
             'host_init': 'host-initramfs-', 'installer_init': 'installer-initramfs-',
             'host': 'host-sd-', 'installer': 'installer-sd-'}
    return {key: safe_path(REPO / 'out' / (prefix + tag)) for key, prefix in names.items()}


def preflight(inputs, kernel, vendor, tag):
    directories = [safe_path(path) for path in (inputs, kernel, vendor)]
    for path in directories:
        require(path.is_dir(), f'Input directory required: {path}')
    outputs = output_paths(tag)
    for output in outputs.values():
        require(not output.exists(), f'Existing output refused: {output}')
        for source in directories:
            require(output != source and output not in source.parents and source not in output.parents,
                    f'Input/output overlap: {source}, {output}')
    # Match the authenticated legacy recipe: accept only physical directories
    # and regular files, refusing module links before reserving any output.
    modules = safe_path(kernel / 'modules/lib/modules')
    require(modules.is_dir(), 'Kernel module directory required')
    for parent, dirs, files in os.walk(modules, followlinks=False):
        for name in dirs:
            require(not (Path(parent) / name).is_symlink(), 'Symlink module directory refused')
        for name in files:
            path = Path(parent) / name
            require(stat.S_ISREG(path.lstat().st_mode),
                    f'Special module input refused: {path}')
    return outputs


def resources(outputs):
    parent = outputs['group'].parent
    staging = parent if parent.exists() else REPO
    storage = {'output': STATE.validate_workspace_storage(staging),
               'inspection': STATE.validate_workspace_storage(safe_path(Path('/var/tmp')))}
    # Fail before building if the outer namespace omitted the writable disk bind.
    with tempfile.TemporaryDirectory(prefix='a733-sd-replay-preflight-', dir='/var/tmp') as temporary:
        (Path(temporary) / 'write-probe').write_bytes(b'probe')
    free = {str(path): shutil.disk_usage(path).free for path in (staging, Path('/var/tmp'))}
    require(all(value >= 4 * 1024**3 for value in free.values()), 'SD replay requires 4 GiB free disk')
    memory = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
    available = int(memory['MemAvailable'].split()[0]) * 1024
    require(available >= 3 * 1024**3, 'SD replay requires 3 GiB available RAM')
    return {'storage': storage, 'free_disk_bytes': free, 'available_ram_bytes': available}


def anchors():
    def evidence(name):
        return json.loads(HELPER.read_regular(REPO / 'docs/evidence' / (name + '.json')))
    diagnostic = evidence('diagnostic-20261104')['files']
    el2 = evidence('el2-diagnostic-20261104')['files']
    personalized = evidence('personalized-nvme-20261106')
    lock = json.loads(HELPER.read_regular(REPO / 'configs/diagnostic-sources.lock.json'))
    return {'diagnostic': diagnostic, 'el2': el2,
            'rootfs': evidence('host-userspace-20261104')['files']['rootfs.tar.gz'],
            'busybox': {'bytes': 1794520, 'sha256': lock['busybox']['binary_sha256']},
            'state_uuid': personalized['state_uuid'],
            'expected': {'host': personalized['component_images']['host-sd-deployment-20261106-02']['a733-host-sd.img'],
                         'installer': evidence('deployment-installer-20261106')['files']['a733-installer-prep.img']}}


def input_records(inputs, kernel, vendor, pins):
    diag = pins['diagnostic']
    files = {inputs / 'rootfs.tar.gz': pins['rootfs'], inputs / 'busybox': pins['busybox'],
             inputs / 'baseline-build-recipe.tar.gz': diag['build-recipe.tar.gz'],
             inputs / 'boot_package.fex': pins['el2']['boot_package.fex'],
             vendor / 'external/packages/pack-uboot/sun60iw2/bin/boot0_sdcard_a733.fex': diag['boot0_sdcard.fex']}
    files.update({kernel / name: diag[name] for name in ('Image', 'board.dtb', 'kernel.config')})
    return {str(path): authenticate(path, expected) for path, expected in files.items()}


def extract_legacy(archive_path, destination):
    with tarfile.open(archive_path, 'r:gz') as archive:
        members = {}
        for item in archive.getmembers():
            path = PurePosixPath(item.name)
            normalized = str(path)
            require(item.name and not path.is_absolute() and '..' not in path.parts and
                    normalized not in ('', '.') and normalized not in members and
                    (item.isdir() or item.isfile()), f'Unsafe/duplicate archive member: {item.name}')
            members[normalized] = item
        require(LEGACY_FILES <= members.keys(), 'Missing legacy recipe files')
        for name in LEGACY_FILES:
            require(members[name].isfile(), f'Legacy regular file required: {name}')
            # Validate all content and names before creating any extracted file.
        content = {name: archive.extractfile(members[name]).read() for name in LEGACY_FILES}
        for name, expected in LEGACY_HASHES.items():
            require(hashlib.sha256(content[name]).hexdigest() == expected, f'Legacy source checksum mismatch: {name}')
        destination.mkdir()
        for name, data in content.items():
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(0o755 if name.endswith('.sh') else 0o644)
    return {name: facts(destination / name) for name in sorted(LEGACY_FILES)}


class Commands:
    def __init__(self, directory):
        self.directory = directory
        self.records = []

    def run(self, *command):
        index = len(self.records) + 1
        log = self.directory / f'{index:02d}.log'
        record = {'command': list(map(str, command)), 'log': str(log), 'returncode': None}
        self.records.append(record)
        try:
            with log.open('xb') as stream:
                completed = subprocess.run(record['command'], env=dict(ENVIRONMENT), cwd=REPO,
                                           stdout=stream, stderr=subprocess.STDOUT, check=False)
            record['returncode'] = completed.returncode
            if completed.returncode:
                raise subprocess.CalledProcessError(completed.returncode, record['command'])
            return log
        except OSError as error:
            record['error'] = str(error)
            raise
        finally:
            (self.directory / 'commands.json').write_text(json.dumps(self.records, indent=2) + '\n')


def replay(inputs, kernel, vendor, tag):
    with private_environment():
        require(os.geteuid() == 0, 'Requires private UID-0 builder namespace')
        outputs = preflight(inputs, kernel, vendor, tag)
        project = project_source()
        before = HELPER.check_builder(REPO, PROFILE)
        capacity = resources(outputs)
        pins = anchors()
        sources = input_records(inputs, kernel, vendor, pins)
        outputs['group'].parent.mkdir(exist_ok=True)
        outputs['group'].mkdir(mode=0o700)  # Atomic reservation; never delete failed work.
        group = outputs['group']
        logs = group / 'logs'
        logs.mkdir()
        commands = Commands(logs)
        result = {'schema': 1, 'classification': 'SD software packaging replay only; no fresh kernel compilation, full userspace rebuild or hardware acceptance',
                  'status': 'failed', 'project_source': project, 'core_inputs': sources,
                  'builder_profile_before': before, 'resources_before': capacity,
                  'boot_profile': 'experimental-el2', 'state_uuid': pins['state_uuid'],
                  'physical_storage_writes': False, 'hardware_acceptance': False,
                  'outputs': {key: str(path) for key, path in outputs.items()}}
        try:
            legacy = group / 'legacy'
            result['legacy_sources'] = extract_legacy(inputs / 'baseline-build-recipe.tar.gz', legacy)
            boot = group / 'boot'
            boot.mkdir()
            for name in ('Image', 'board.dtb', 'kernel.config'):
                shutil.copyfile(kernel / name, boot / name)
                authenticate(boot / name, pins['diagnostic'][name])
            for source, name, expected in (
                    (vendor / 'external/packages/pack-uboot/sun60iw2/bin/boot0_sdcard_a733.fex', 'boot0_sdcard.fex', pins['diagnostic']['boot0_sdcard.fex']),
                    (inputs / 'boot_package.fex', 'boot_package.fex', pins['el2']['boot_package.fex'])):
                shutil.copyfile(source, boot / name)
                authenticate(boot / name, expected)
            with umask(0o002):
                commands.run('bash', legacy / 'build/diagnostic-initramfs.sh', inputs / 'busybox',
                             boot / 'initramfs.cpio.gz', kernel / 'modules/lib/modules')
            result['legacy_module_carrier_rebuilt'] = authenticate(boot / 'initramfs.cpio.gz', pins['diagnostic']['initramfs.cpio.gz'])
            commands.run('mkimage', '-A', 'arm', '-O', 'linux', '-T', 'kernel', '-C', 'none',
                         '-a', '0x41000000', '-e', '0x41000000', '-n', 'Linux kernel',
                         '-d', boot / 'Image', boot / 'uImage')
            result['fresh_uImage'] = authenticate(boot / 'uImage', pins['diagnostic']['uImage'])
            python = ['/usr/bin/python3', '-I', '-B']
            commands.run(*python, REPO / 'build/installer-runtime.py', inputs / 'rootfs.tar.gz', outputs['runtime'])
            commands.run(*python, REPO / 'build/host-initramfs.py', inputs / 'rootfs.tar.gz', boot,
                         inputs / 'busybox', pins['state_uuid'], outputs['host_init'])
            commands.run(*python, REPO / 'build/installer-initramfs.py', outputs['runtime'], boot,
                         inputs / 'busybox', outputs['installer_init'])
            products = {}
            for kind, name in (('host', 'a733-host-sd.img'), ('installer', 'a733-installer-prep.img')):
                commands.run(*python, REPO / f'build/assemble-{kind}.py', boot, outputs[kind + '_init'],
                             outputs[kind], '--boot-profile', 'experimental-el2')
                inspection = commands.run(*python, REPO / f'tests/verify_{kind}_sd.py', outputs[kind],
                                          outputs[kind + '_init'], '--boot-profile', 'experimental-el2')
                shutil.copyfile(inspection, outputs[kind] / 'independent-inspection.json')
                products[kind] = {'raw': facts(outputs[kind] / name),
                                  'gzip': facts(outputs[kind] / (name + '.gz'))}
            result.update(products=products, expected=pins['expected'],
                          comparisons={kind: value['raw'] == pins['expected'][kind] for kind, value in products.items()})
            require(all(result['comparisons'].values()), 'SD images differ from committed targets; preserve outputs and investigate')
            require(input_records(inputs, kernel, vendor, pins) == sources, 'Core inputs changed during replay')
            result['builder_profile_after'] = HELPER.check_builder(REPO, PROFILE)
            require(result['builder_profile_after'] == before, 'Builder profile changed during replay')
            result['builder_profile_before_after_equal'] = True
            result['status'] = 'passed'
        except Exception as error:
            result['error'] = f'{type(error).__name__}: {error}'
            # Preserve the original failure while still recording a postflight
            # package inventory when a child command or hash assertion failed.
            try:
                result['builder_profile_after'] = HELPER.check_builder(REPO, PROFILE)
                result['builder_profile_before_after_equal'] = result['builder_profile_after'] == before
            except Exception as profile_error:
                result['builder_profile_after_error'] = f'{type(profile_error).__name__}: {profile_error}'
            raise
        finally:
            result['commands'] = commands.records
            (group / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('input_directory', 'kernel_artifacts', 'vendor_checkout'):
        parser.add_argument(name, type=Path)
    parser.add_argument('tag')
    args = parser.parse_args()
    try:
        result = replay(args.input_directory, args.kernel_artifacts, args.vendor_checkout, args.tag)
        print(json.dumps(result))
    except (ValueError, OSError, tarfile.TarError, subprocess.CalledProcessError) as error:
        print(f'SD replay refused/failed: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
