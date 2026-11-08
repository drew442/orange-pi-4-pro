#!/usr/bin/env python3
"""Package the pinned PRIVATE same-board SSH RAM installer; regular files only."""
import argparse
from contextlib import ExitStack
import gzip
import hashlib
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
import time
import zlib

REPO = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, REPO / path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


BASE = module('ssh_sd_base', 'build/vgic-runtime-bootstrap.py')
SSH = module('ssh_sd_archive', 'build/derive-ssh-installer.py')
VGIC, HOST = BASE.VGIC, BASE.HOST
IMAGE = {'bytes': 268435456, 'sha256': 'f02c74b4c001f1608d0005b6ff23fe82bffa8c02f71e19e5b6573819125ae383'}
PARENT_MANIFEST = {'bytes': 17471, 'sha256': '47df42be022d9e378a034df25d31865a24337c9309b4e4fdc2710ea9c2462993'}
ARCHIVE = {'bytes': 28946324, 'sha256': '209257f1c240d6662831ce27f14e41947f859992d2ceffc11efd39b5bd94ea36'}
SSH_MANIFEST = {'bytes': 664617, 'sha256': 'cc2f5f467d9dd1ab13fd7a1eaea21119dc29b171d8737b208e2d1f37528dc8a6'}
PREFIX = {'bytes': 33554432, 'sha256': 'da4a8db3aba8255a78ed2b60a251c9835dfa0b5f471f1f0590fe09abb722b556'}
PINS = {
    'boot0_sdcard.fex': {'bytes': 245760, 'sha256': '9213f1c56ef932930d43b5751e8937bea0e9ae7460ec16187cc1b0dffdb75345'},
    'boot_package.fex': {'bytes': 1392640, 'sha256': 'b20c3d1133aee6474416bc9846b0bf94d5439aa12d7f7555b44607105ddab3b6'},
    'uImage': {'bytes': 27283528, 'sha256': '6f9269755c4c2bc196c094899fa1ff20292f4d762c4000f77fcbb0887d5f154a'},
    'board.dtb': BASE.PATCHED_DTB_RECORD,
}
EPOCH = 1793750400
FREE_FLOOR = 256 * 1024**2
SCRATCH_BUDGET = 1536 * 1024**2  # Includes assembler's duplicate partition/disk.
MAX_EXPANDED = 256 * 1024**2
RECIPES = ('build/assemble-ssh-installer.py', 'build/vgic-runtime-bootstrap.py',
           'build/vgic-host-sd.py', 'build/assemble-host.py',
           'build/assemble-diagnostic.sh', 'build/derive-ssh-installer.py',
           'build/repack-guest-initramfs.py', 'build/host-initramfs.py',
           'configs/installer-boot.cmd', 'configs/builder-packages.lock.tsv')


def resources(path, admission=False):
    memory = re.search(r'^MemAvailable:\s+(\d+) kB$', Path('/proc/meminfo').read_text(), re.M)
    minimum = FREE_FLOOR + (SCRATCH_BUDGET if admission else 0)
    if (shutil.disk_usage(path).free < minimum or not memory or
            int(memory[1]) * 1024 < 1_000_000_000):
        raise ValueError('insufficient disk/memory: preserve 256 MiB disk and 1 GB available memory')


def run(command, log, workspace, env=None, timeout=120):
    """Bound external work and check runtime resources once per second."""
    with subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=env,
                          start_new_session=True) as process:
        deadline = time.monotonic() + timeout
        next_progress = time.monotonic() + 30
        BASE.progress('Running ' + Path(command[0]).name + ' (details in private assembly.log)')
        try:
            while process.poll() is None:
                resources(workspace)
                if time.monotonic() >= next_progress:
                    BASE.progress('Still running ' + Path(command[0]).name)
                    next_progress = time.monotonic() + 30
                if time.monotonic() >= deadline:
                    raise ValueError('subprocess exceeded timeout')
                time.sleep(1)
            if process.returncode:
                raise subprocess.CalledProcessError(process.returncode, command)
        except BaseException:
            import signal
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            raise


def builder_tools(lock_path):
    """Use the existing Jammy package policy; record tools and bounded versions."""
    release = Path('/etc/os-release').read_text()
    if not re.search(r'^ID=ubuntu$', release, re.M) or not re.search(r'^VERSION_ID="22.04"$', release, re.M):
        raise ValueError('requires pinned Ubuntu 22.04 builder')
    # Shared helper's dpkg-query remains cooperative on the trusted builder.
    result = {'packages': HOST.validate_builder_packages(lock_path),
              'python': __import__('sys').version, 'executables': {}, 'versions': {}}
    for name in ('bash', 'debugfs', 'mkimage', 'mkfs.ext4', 'e2fsck', 'sfdisk',
                 'dd', 'cp', 'find', 'touch', 'truncate', 'realpath', 'stat',
                 'mktemp', 'mkdir', 'rm', 'sort', 'sha256sum'):
        path = shutil.which(name, path='/usr/sbin:/usr/bin:/sbin:/bin')
        if not path:
            raise ValueError('missing builder tool')
        result['executables'][name] = {'path': path, **VGIC.file_record(Path(path).resolve())}
    for command in (('bash', '--version'), ('debugfs', '-V'), ('mkimage', '-V'),
                    ('mkfs.ext4', '-V'), ('e2fsck', '-V'), ('sfdisk', '--version')):
        completed = subprocess.run(command, check=True, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, timeout=30)
        result['versions'][command[0]] = completed.stdout.strip()
    return result


def output_path(name):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', name):
        raise ValueError('invalid output tag')
    path = BASE.safe_regular_path(REPO / 'out' / ('ssh-installer-sd-' + name))
    if path.exists() or path.is_symlink():
        raise ValueError('fresh output required; failed tags cannot be reused')
    return path


def copy_region(held, target, offset, size):
    with target.open('xb') as destination:
        os.chmod(target, 0o600)
        for position in range(0, size, 1024**2):
            data = os.pread(held.fd, min(1024**2, size - position), offset + position)
            if len(data) != min(1024**2, size - position):
                raise ValueError('short input region')
            destination.write(data)
    held.verify()


def extract(image, destination, names, log, workspace, held=None):
    destination.mkdir(mode=0o700)
    partition = destination / 'partition.ext4'
    if held:
        copy_region(held, partition, VGIC.PARTITION_OFFSET, VGIC.PARTITION_BYTES)
    else:
        VGIC.copy_region(image, partition, VGIC.PARTITION_OFFSET, VGIC.PARTITION_BYTES)
    try:
        for name in names:
            target = destination / name
            # debugfs interprets its own command language. Generated paths only.
            if not re.fullmatch(r'[A-Za-z0-9_./-]+', str(target)):
                raise ValueError('debugfs target path is not command-safe')
            run(['debugfs', '-R', f'dump {VGIC.FILES[name]} {target}', str(partition)], log, workspace)
            with BASE.HeldInput(target, 64 * 1024**2):
                pass
    finally:
        partition.unlink(missing_ok=True)


def validate_archive(data, manifest):
    if HOST.record(data) != ARCHIVE:
        raise ValueError('SSH archive authentication failed before decompression')
    if (manifest.get('schema') != 1 or manifest.get('profile') != 'a733-private-ssh-ram-installer-v1' or
            manifest.get('files', {}).get('initramfs.cpio.gz') != ARCHIVE):
        raise ValueError('incorrect SSH installer profile/archive')
    raw = SSH.unpack(data, MAX_EXPANDED)
    _, entries = SSH.parse(raw)
    actual = {name: {'mode': mode, **HOST.record(value)} for name, (mode, value) in entries.items()}
    if manifest.get('entries') != actual or manifest.get('expanded_bytes') != len(raw):
        raise ValueError('SSH manifest does not describe archive entries')
    maintenance = {'usr/lib/a733-tools/' + name for name in (*SSH.TOOLS.SOURCES, 'package.json')}
    if len(maintenance) != 40 or set(manifest.get('maintenance_preserved', [])) != maintenance:
        raise ValueError('40 preserved maintenance entries required')
    receipt = manifest.get('base_manifest_path')
    if receipt not in entries or HOST.record(entries[receipt][1]) != manifest.get('base_manifest'):
        raise ValueError('missing authenticated historical base receipt')
    original = HOST.json_object(entries[receipt][1])
    for name in maintenance:
        if name not in actual or original.get('entries', {}).get(name) != actual[name]:
            raise ValueError('historical maintenance entry changed')
    for name, record in manifest.get('recipes', {}).items():
        embedded = SSH.PROVENANCE + name
        if embedded not in entries or HOST.record(entries[embedded][1]) != record:
            raise ValueError('embedded SSH producer provenance changed')
    return len(raw)


def validate_wrapper(data, payload, kind):
    if len(data) != len(payload) + 64 or len(data) >= 64 * 1024**2:
        raise ValueError('incorrect legacy wrapper length')
    header = bytearray(data[:64])
    magic, crc, epoch, size, load, entry, checksum = struct.unpack_from('>7I', header)
    header[4:8] = bytes(4)
    if (magic != 0x27051956 or zlib.crc32(header) != crc or epoch != EPOCH or
            size != len(payload) or load or entry or checksum != zlib.crc32(payload) or
            data[64:] != payload or data[28:32] != bytes((5, 2, kind, 1 if kind == 3 else 0)) or
            any(data[32:64])):
        raise ValueError('incorrect legacy header/CRC/payload')


def verify_prefix(source, candidate):
    with candidate.open('rb') as stream:
        data = stream.read(PREFIX['bytes'])
    if HOST.record(data) != PREFIX or data != os.pread(source.fd, PREFIX['bytes'], 0):
        raise ValueError('complete firmware/MBR prefix changed')
    source.verify()
    return PREFIX


def compress_image(source, target, workspace):
    expected = VGIC.file_record(source)
    with source.open('rb') as reader, target.open('xb') as raw:
        with gzip.GzipFile(filename='', mode='wb', compresslevel=9, mtime=0, fileobj=raw) as writer:
            for data in iter(lambda: reader.read(1024**2), b''):
                resources(workspace)
                writer.write(data)
    digest, size = hashlib.sha256(), 0
    with gzip.open(target, 'rb') as reader:
        for data in iter(lambda: reader.read(1024**2), b''):
            resources(workspace)
            size += len(data)
            if size > IMAGE['bytes']:
                raise ValueError('compressed image exceeds expansion bound')
            digest.update(data)
    if {'bytes': size, 'sha256': digest.hexdigest()} != expected:
        raise ValueError('compressed image expansion differs')


def build(image, parent_manifest, archive, ssh_manifest, name):
    output = output_path(name)
    with ExitStack() as stack:
        held = []
        def hold(path, maximum, pin=None):
            item = stack.enter_context(BASE.HeldInput(path, maximum, pin))
            held.append(item)
            return item
        BASE.progress('Authenticate paired VGIC SD and private SSH runtime')
        source = hold(image, IMAGE['bytes'], IMAGE)
        parent = hold(parent_manifest, PARENT_MANIFEST['bytes'], PARENT_MANIFEST).read()
        receipt = hold(ssh_manifest, SSH_MANIFEST['bytes'], SSH_MANIFEST).read()
        payload = hold(archive, ARCHIVE['bytes'], ARCHIVE).read()
        base, ssh = HOST.json_object(parent), HOST.json_object(receipt)
        if (base.get('boot_profile', {}).get('name') != BASE.PROFILE or
                base.get('files', {}).get('a733-host-sd.img') != IMAGE or
                any(base.get('boot_inputs', {}).get(key) != pin for key, pin in PINS.items())):
            raise ValueError('incorrect paired VGIC bootstrap')
        resources(output.parent, admission=True)
        BASE.progress('Check bounded archive, historical maintenance files and provenance')
        expanded = validate_archive(payload, ssh)
        captured = {name: hold(REPO / name, 4 * 1024**2).read() for name in RECIPES}
        packages = builder_tools(REPO / 'configs/builder-packages.lock.tsv')
        VGIC.verify_layout(source.path)
        output.parent.mkdir(exist_ok=True)
        resources(output.parent, admission=True)
        output.mkdir(mode=0o700)
        previous_umask = os.umask(0o077)
        try:
            with tempfile.TemporaryDirectory(prefix='scratch-', dir=output) as temporary, (output / 'assembly.log').open('xb') as log:
                stage = Path(temporary)
                inputs = stage / 'inputs'
                extract(source.path, inputs, ('uImage', 'board.dtb'), log, output, source)
                for blob, offset in (('boot0_sdcard.fex', 8192), ('boot_package.fex', 16793600)):
                    copy_region(source, inputs / blob, offset, PINS[blob]['bytes'])
                for blob, pin in PINS.items():
                    VGIC.require_record(inputs / blob, pin)
                (inputs / 'initramfs.cpio.gz').write_bytes(payload)
                command = captured['configs/installer-boot.cmd']
                (inputs / 'boot.cmd').write_bytes(command)
                assembler = stage / 'assemble-diagnostic.sh'
                assembler.write_bytes(captured['build/assemble-diagnostic.sh'])
                env = dict(os.environ, PATH='/usr/sbin:/usr/bin:/sbin:/bin', TMPDIR=str(stage),
                           SOURCE_DATE_EPOCH=str(EPOCH), LC_ALL='C', TZ='UTC')
                run(['mkimage', '-A', 'arm', '-O', 'linux', '-T', 'ramdisk', '-C', 'gzip',
                     '-d', str(inputs / 'initramfs.cpio.gz'), str(inputs / 'uInitrd')], log, output, env)
                validate_wrapper((inputs / 'uInitrd').read_bytes(), payload, 3)
                candidate = output / 'a733-ssh-installer.img'
                run(['bash', str(assembler), str(inputs), str(candidate), str(inputs / 'boot.cmd')], log, output, env, 900)
                VGIC.verify_layout(candidate)
                verify_prefix(source, candidate)
                rebuilt = stage / 'rebuilt'
                extract(candidate, rebuilt, tuple(VGIC.FILES), log, output)
                for blob in ('uImage', 'board.dtb', 'uInitrd', 'boot.cmd'):
                    if VGIC.file_record(rebuilt / blob) != VGIC.file_record(inputs / blob):
                        raise ValueError('assembled boot file differs')
                # Script payload is a legacy multi-file framing: length, zero, bytes.
                script_payload = struct.pack('>II', len(command), 0) + command
                validate_wrapper((rebuilt / 'boot.scr').read_bytes(), script_payload, 6)
                shutil.copyfile(rebuilt / 'uInitrd', output / 'uInitrd')
                resources(output)
                BASE.progress('Compress image and verify its complete expansion')
                compress_image(candidate, output / 'a733-ssh-installer.img.gz', output)
                (output / 'parent-bootstrap-manifest.json').write_bytes(parent)
                (output / 'ssh-runtime-manifest.json').write_bytes(receipt)
                for item in held:
                    item.verify()
                log.flush()
                files = {path.name: VGIC.file_record(path) for path in output.iterdir() if path.is_file()}
                result = {'schema': 1, 'board': 'Orange Pi 4 Pro A733',
                          'classification': 'PRIVATE same-board SSH RAM installer; UNBOOTED',
                          'boot_profile': {'name': BASE.PROFILE, 'physical_acceptance_pending': True},
                          'kernel_release': '6.6.98-a733-diag', 'source_date_epoch': EPOCH,
                          'state_uuid': None, 'root_selection': 'RAM only; no NVMe UUID selection',
                          'hardware_acceptance': False, 'physical_storage_written': False,
                          'boot_inputs': PINS, 'boot_command': HOST.record(command),
                          'parent_bootstrap': {'manifest': PARENT_MANIFEST, 'image': IMAGE, 'original_manifest': base},
                          'ssh_runtime': {'manifest': SSH_MANIFEST, 'archive': ARCHIVE, 'original_manifest': ssh},
                          'recipes': {key: HOST.record(value) for key, value in captured.items()},
                          'builder_tools': packages, 'files': files,
                          'verification': {'complete_prefix': PREFIX, 'expanded_initramfs_bytes': expanded,
                                           'legacy_wrappers_and_payloads': True, 'image_fits_actual_sd': IMAGE['bytes'] <= BASE.SD_BYTES,
                                           'gzip_complete_expansion': True, 'inputs_held_and_reauthenticated': True},
                          'pending_gates': ['Independent SD artifact review', 'Physical RAM boot, operator SSH and host identity',
                                            'NVMe idle and zero routine SD writes', 'Actual KVM guest acceptance remains separate']}
                (output / 'manifest.json').write_text(json.dumps(result, indent=2) + '\n')
                resources(output)
                BASE.progress('Private regular-file SD packaging complete; hardware acceptance remains UNBOOTED')
                return result
        finally:
            os.umask(previous_umask)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('image', 'parent-manifest', 'archive', 'ssh-manifest', 'name'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    try:
        result = build(args.image, args.parent_manifest, args.archive, args.ssh_manifest, args.name)
        print(json.dumps({'classification': result['classification'], 'files': result['files']}, indent=2))
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as error:
        parser.exit(1, f'private SSH SD packaging failed: {error}\n')


if __name__ == '__main__':
    main()
