#!/usr/bin/env python3
"""Compose a backup-preserving A733 SPI candidate using regular files only.

This tool grants no deployment permission and provides no hardware evidence.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]
SIZE = 16777216
ERASE = 65536
TARGET = 'nvme-candidate-20261107-ram02'
REGISTRY = 'configs/uboot-nvme-candidate-targets.lock.json'
REGISTRY_PIN = {'bytes': 4035, 'sha256': 'abb6f3136f6df22c3da0b7a205b7d2b968e3faad7a3589a3fb69c1c2689117c5'}
LAYOUT = (('artifacts/boot0_spinor_a733.fex', 0, 237568),
          ('artifacts/boot_package.fex', 262144, 1392640))
FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


def require(condition, message):
    if not condition:
        raise ValueError(message)


def pin(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def object_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate JSON key')
        result[key] = value
    return result


def decode(data):
    require(len(data) <= 1024 * 1024, 'JSON exceeds bound')
    return json.loads(data, object_pairs_hook=object_pairs)


def canonical(path):
    path = str(path)
    value = Path(path)
    require(value.is_absolute() and str(value) == path and
            all(p not in ('.', '..') for p in value.parts) and
            value.parts[1:2] not in [('proc',), ('sys',), ('dev',)],
            'require canonical absolute path outside pseudo/device trees')
    return value


def signature(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class Inputs:
    """Hold directory chains and files; verify pathname and content continuity."""
    def __init__(self):
        self.directories = {}
        self.files = []

    def directory(self, path):
        path = canonical(path)
        current = Path('/')
        if current not in self.directories:
            self.directories[current] = os.open('/', FLAGS | os.O_DIRECTORY)
        for part in path.parts[1:]:
            parent = current
            current = current / part
            if current not in self.directories:
                self.directories[current] = os.open(
                    part, FLAGS | os.O_DIRECTORY, dir_fd=self.directories[parent])
        return self.directories[path]

    def read(self, path, maximum, expected=None):
        path = canonical(path)
        parent = self.directory(path.parent)
        fd = os.open(path.name, FLAGS, dir_fd=parent)
        try:
            before = os.fstat(fd)
            require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and
                    before.st_size <= maximum, 'input must be bounded single-link regular file')
            data = os.pread(fd, maximum + 1, 0)
            require(len(data) == before.st_size and signature(os.fstat(fd)) == signature(before),
                    'input changed during read')
            if expected is not None:
                require(pin(data) == expected, f'input pin mismatch: {path}')
            self.files.append((path, fd, signature(before), pin(data)))
            return data
        except BaseException:
            os.close(fd)
            raise

    def recheck(self):
        for path, fd in self.directories.items():
            if path == Path('/'):
                continue
            info = os.stat(path.name, dir_fd=self.directories[path.parent], follow_symlinks=False)
            held = os.fstat(fd)
            require(stat.S_ISDIR(info.st_mode) and
                    (info.st_dev, info.st_ino) == (held.st_dev, held.st_ino),
                    'input/output ancestor replaced')
        for path, fd, before, expected in self.files:
            info = os.stat(path.name, dir_fd=self.directories[path.parent], follow_symlinks=False)
            require(signature(info) == before == signature(os.fstat(fd)), 'input replaced or mutated')
            require(pin(os.pread(fd, expected['bytes'] + 1, 0)) == expected and
                    signature(os.fstat(fd)) == before, 'input bytes changed')

    def close(self):
        for _, fd, _, _ in self.files:
            os.close(fd)
        for fd in reversed(list(self.directories.values())):
            os.close(fd)


def git(*args):
    return subprocess.check_output(['git', '--no-optional-locks', '-C', str(REPO), *args],
                                   env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C',
                                        'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null'})


def authenticate_sources(inputs):
    registry_bytes = inputs.read(REPO / REGISTRY, 1024 * 1024, REGISTRY_PIN)
    require(git('show', f'HEAD:{REGISTRY}') == registry_bytes, 'registry differs from committed Git')
    registry = decode(registry_bytes)
    require(registry['schema'] == 1 and set(registry['targets']) == {TARGET}, 'unknown target registry')
    target = registry['targets'][TARGET]
    evidence = target['evidence']
    require(evidence['path'] == 'docs/evidence/uboot-nvme-candidate-20261107.json', 'unknown evidence')
    raw = inputs.read(REPO / evidence['path'], 1024 * 1024,
                      {k: evidence[k] for k in ('bytes', 'sha256')})
    require(git('show', f"HEAD:{evidence['path']}") == raw, 'evidence differs from committed Git')
    record = decode(raw)
    require(record['success'] is True and record['hardware_acceptance'] is False and
            record['deployment_authorized'] is False and
            record['source_revision'] == target['parent_source_revision'] and
            record['components'] == target['required_artifacts'], 'evidence bindings differ')
    return target


def backup_identity(manifest):
    require(set(manifest) == {'status', 'bytes', 'sha256', 'source', 'source_reads', 'image',
                             'off_device_retention_verified', 'recovery_proven'}, 'unknown backup schema')
    require(manifest['status'] == 'verified-local-ram-backup' and
            type(manifest['source_reads']) is int and manifest['source_reads'] == 2 and
            type(manifest['bytes']) is int and manifest['bytes'] == SIZE and
            manifest['image'] == 'spi.bin' and
            type(manifest['off_device_retention_verified']) is bool and
            type(manifest['recovery_proven']) is bool and
            re.fullmatch('[0-9a-f]{64}', manifest['sha256']), 'invalid backup result')
    source = manifest['source']
    require(set(source) == {'device', 'dev', 'sysfs', 'spi', 'partname', 'jedec_id',
                            'sysfs_geometry', 'proc_mtd', 'ioctl'}, 'incomplete source identity')
    require(re.fullmatch(r'/dev/mtd(?:0|[1-9][0-9]*)', source['device']) is not None,
            'invalid source device identity')
    name = source['device'].split('/')[-1]
    require(re.fullmatch(r'[0-9]+:[0-9]+', source['dev']) is not None and
            source['partname'] == 'XM25QU128C' and source['jedec_id'] == '204118' and
            source['sysfs_geometry'] == dict(name='spi0.0', type='nor', size=str(SIZE),
                                             erasesize=str(ERASE), writesize='1') and
            source['proc_mtd'].splitlines() == ['dev:    size   erasesize  name',
                                               f'{name}: 01000000 00010000 "spi0.0"'],
            'unexpected complete SPI identity/geometry')
    spi, sysfs = Path(source['spi']), Path(source['sysfs'])
    require(all(str(path) == value and '..' not in path.parts
                for path, value in ((spi, source['spi']), (sysfs, source['sysfs']))),
            'noncanonical recorded sysfs topology')
    require(source['spi'].startswith('/sys/devices/') and spi.name == 'spi0.0' and
            spi in sysfs.parents and sysfs.name == name and
            sysfs.parent.name == 'mtd', 'invalid recorded sysfs topology')
    info = source['ioctl']
    require(set(info) == {'type', 'flags', 'size', 'erasesize', 'writesize', 'oobsize'} and
            all(type(value) is int for value in info.values()) and
            0 <= info['flags'] <= 0xffffffff and
            {k: v for k, v in info.items() if k != 'flags'} ==
            dict(type=3, size=SIZE, erasesize=ERASE, writesize=1, oobsize=0),
            'invalid recorded MEMGETINFO')
    return source


def progress(message):
    print(message, file=sys.stderr, flush=True)


def output_parent(inputs, output, backup, components):
    output = canonical(output)
    require(output.parent == REPO / 'out' and
            re.fullmatch(r'spi-nvme-[A-Za-z0-9][A-Za-z0-9._-]*', output.name),
            'output must be a new repo/out/spi-nvme-NAME directory')
    require(all(output != root and root not in output.parents for root in (backup, components)),
            'output overlaps input directories')
    parent = inputs.directory(output.parent)
    try:
        os.stat(output.name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return parent
    raise ValueError('output already exists')


def write_file(directory, name, data):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                 0o600, dir_fd=directory)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def publish(inputs, parent, output, candidate, result):
    inputs.recheck()
    os.mkdir(output.name, 0o700, dir_fd=parent)
    directory = os.open(output.name, FLAGS | os.O_DIRECTORY, dir_fd=parent)
    try:
        require(stat.S_IMODE(os.fstat(directory).st_mode) == 0o700, 'unsafe output mode')
        write_file(directory, 'a733-spi-nvme.bin', candidate)
        inputs.read(output / 'a733-spi-nvme.bin', SIZE, pin(candidate))
        image_info = os.stat('a733-spi-nvme.bin', dir_fd=directory, follow_symlinks=False)
        require(stat.S_IMODE(image_info.st_mode) == 0o600 and image_info.st_uid == os.geteuid(),
                'unsafe candidate permissions/owner')
        inputs.recheck()
        held = os.fstat(directory)
        current = os.stat(output.name, dir_fd=parent, follow_symlinks=False)
        require((held.st_dev, held.st_ino) == (current.st_dev, current.st_ino), 'output replaced')
        manifest_bytes = (json.dumps(result, indent=2, sort_keys=True) + '\n').encode()
        write_file(directory, 'manifest.json.partial', manifest_bytes)
        inputs.recheck()
        os.link('manifest.json.partial', 'manifest.json', src_dir_fd=directory,
                dst_dir_fd=directory, follow_symlinks=False)
        os.unlink('manifest.json.partial', dir_fd=directory)
        inputs.read(output / 'manifest.json', 1024 * 1024, pin(manifest_bytes))
        manifest_info = os.stat('manifest.json', dir_fd=directory, follow_symlinks=False)
        require(stat.S_IMODE(manifest_info.st_mode) == 0o600 and manifest_info.st_uid == os.geteuid(),
                'unsafe manifest permissions/owner')
        os.fsync(directory)
        os.fsync(parent)
        inputs.recheck()
        current = os.stat(output.name, dir_fd=parent, follow_symlinks=False)
        require((held.st_dev, held.st_ino) == (current.st_dev, current.st_ino), 'output replaced')
    except BaseException:
        try:
            os.unlink('manifest.json', dir_fd=directory)
        except FileNotFoundError:
            pass
        raise
    finally:
        os.close(directory)


def assemble(backup, components, output, plan=False):
    backup, components, output = map(canonical, (backup, components, output))
    inputs = Inputs()
    try:
        parent = output_parent(inputs, output, backup, components)
        progress('Validating committed candidate sources and backup files')
        target = authenticate_sources(inputs)
        raw_manifest = inputs.read(backup / 'manifest.json', 1024 * 1024)
        manifest = decode(raw_manifest)
        source = backup_identity(manifest)
        baseline = inputs.read(backup / 'spi.bin', SIZE, {k: manifest[k] for k in ('bytes', 'sha256')})
        raw_parent = inputs.read(components / 'candidate-manifest.json', 1024 * 1024, target['manifest'])
        component_manifest = decode(raw_parent)
        require(component_manifest['schema'] == 1 and component_manifest['hardware_acceptance'] is False,
                'invalid parent component manifest')
        progress('Composing two component ranges and hashing 256 erase units')
        candidate = bytearray(baseline)
        ranges = []
        for name, offset, length in LAYOUT:
            expected = target['required_artifacts'][name]
            require(expected['bytes'] == length and component_manifest['files'][name] == expected,
                    'component manifest binding mismatch')
            data = inputs.read(components / name, length, expected)
            candidate[offset:offset + length] = data
            ranges.append(dict(component=name, offset=offset, end=offset + length, **pin(data)))
        candidate = bytes(candidate)
        preserved = [dict(offset=start, end=end, **pin(baseline[start:end])) for start, end in
                     ((237568, 262144), (1654784, SIZE))]
        units = [dict(index=i, offset=i * ERASE, end=(i + 1) * ERASE,
                      original_sha256=pin(baseline[i * ERASE:(i + 1) * ERASE])['sha256'],
                      candidate_sha256=pin(candidate[i * ERASE:(i + 1) * ERASE])['sha256'],
                      changed=baseline[i * ERASE:(i + 1) * ERASE] != candidate[i * ERASE:(i + 1) * ERASE])
                 for i in range(SIZE // ERASE)]
        result = dict(schema=1, stage='validated-file-only-plan' if plan else 'assembled-file-only',
                      success=True, target_id=TARGET, spi_identity=source, baseline=pin(baseline),
                      candidate=pin(candidate), replacement_ranges=ranges, preserved_ranges=preserved,
                      erase_size=ERASE, affected_erase_units=list(range(26)), affected_erase_end=1703936,
                      erase_units=units, erase_plan_policy='Preserve every byte outside replacement ranges, including partial erase units; no programming authorization.',
                      backup_manifest=pin(raw_manifest), parent_manifest=pin(raw_parent),
                      registry=REGISTRY_PIN, tool_source=pin(inputs.read(Path(__file__).absolute(), 1024 * 1024)),
                      tool_revision=git('rev-parse', 'HEAD').decode().strip(),
                      deployment_authorized=False, hardware_acceptance=False,
                      physical_writes=False, recovery_proven=False)
        inputs.recheck()
        if not plan:
            progress('Publishing private regular-file candidate and manifest')
            publish(inputs, parent, output, candidate, result)
        return result
    finally:
        inputs.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backup-dir', required=True)
    parser.add_argument('--components', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--plan', action='store_true', help='validate and report without creating output')
    args = parser.parse_args()
    print(json.dumps(assemble(args.backup_dir, args.components, args.output, args.plan), indent=2, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f'SPI file assembly refused/failed: {error}', file=sys.stderr)
        sys.exit(1)
