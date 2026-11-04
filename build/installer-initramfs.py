#!/usr/bin/env python3
"""Package the pinned ARM64 installer preparation runtime; no physical writes."""
import argparse
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys

REPO = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, REPO / path)
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


HOST = module('installer_archive_host', 'build/assemble-host.py')
INIT = module('installer_archive_init', 'build/host-initramfs.py')
STATE = module('installer_archive_state', 'build/host-state.py')
SOURCES = {'init': ('configs/installer-init', 0o755),
           'etc/udhcpc.script': ('configs/installer-udhcpc.script', 0o755),
           'usr/lib/a733-installer/install-nvme.py': ('scripts/install-nvme.py', 0o644),
           'usr/lib/a733-installer/acquire-nvme.py': ('scripts/acquire-nvme.py', 0o644),
           'usr/lib/a733-installer/plan-nvme.py': ('scripts/plan-nvme.py', 0o644),
           'usr/lib/a733-installer/nvme_layout.py': ('scripts/nvme_layout.py', 0o644),
           'usr/share/licenses/GPL-2.0.txt': ('licenses/GPL-2.0.txt', 0o644),
           'usr/share/licenses/busybox-copyright.txt': ('licenses/busybox-copyright.txt', 0o644)}


def relative(name):
    path = PurePosixPath(name)
    if not name or path.is_absolute() or '..' in path.parts or str(path) != name or name == '.':
        raise ValueError('unsafe or noncanonical runtime path')
    return path


def runtime_entries(directory):
    directory = HOST.safe_path(directory)
    data = HOST.read_regular(directory / 'runtime-manifest.json', 4 * 1024**2)
    manifest = HOST.json_object(data)
    source = json.loads((REPO / 'docs/evidence/host-userspace-20261104.json').read_text())['files']['rootfs.tar.gz']
    policy = HOST.record((REPO / 'configs/installer-runtime-policy.json').read_bytes())
    if manifest.get('schema') != 1 or manifest.get('source') != source or manifest.get('policy') != policy:
        raise ValueError('runtime manifest source/policy differs')
    required_provenance = {'build/installer-runtime.py', 'build/host-state.py', 'configs/host_config.py', 'configs/package_state.py',
                           'configs/installer-runtime-policy.json', 'docs/evidence/host-userspace-20261104.json',
                           'configs/host-userspace-packages.lock.tsv', 'configs/host-userspace-package-hashes.lock.tsv',
                           'configs/builder-packages.lock.tsv'}
    if not required_provenance <= set(manifest.get('provenance', {})):
        raise ValueError('runtime provenance is incomplete')
    for name, expected in manifest['provenance'].items():
        relative(name)
        if HOST.file_record(REPO / name) != expected:
            raise ValueError('runtime provenance differs from current source')
    root = directory / 'root'
    if not root.is_dir() or root.is_symlink():
        raise ValueError('runtime root must be a real directory')
    actual = {str(p.relative_to(root)) for p in root.rglob('*') if p.is_symlink() or not p.is_dir()}
    if actual != set(manifest['files']):
        raise ValueError('runtime namespace differs from manifest')
    entries = {}
    for name, expected in manifest['files'].items():
        relative(name)
        path = HOST.safe_path(root / name) if expected['type'] != 'symlink' else root / name
        mode = expected['mode']
        if type(mode) is not int or mode & ~0o777:
            raise ValueError('runtime mode must contain permission bits only')
        info = path.lstat()
        if stat.S_IMODE(info.st_mode) != mode:
            raise ValueError('runtime file mode differs')
        if expected['type'] == 'file':
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError('runtime requires regular single-link files')
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, 'rb') as source:
                opened = os.fstat(source.fileno())
                if not stat.S_ISREG(opened.st_mode) or opened.st_size > 64 * 1024**2:
                    raise ValueError('runtime input must be a bounded regular file')
                value = source.read(64 * 1024**2 + 1)
            if HOST.record(value) != {k: expected[k] for k in ('bytes', 'sha256')}:
                raise ValueError('runtime file hash/size differs')
            entries[name] = (stat.S_IFREG | mode, value)
        elif expected['type'] == 'symlink':
            if not stat.S_ISLNK(info.st_mode) or os.readlink(path) != expected['target']:
                raise ValueError('runtime alias differs')
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(root) or not resolved.is_file():
                raise ValueError('runtime alias escapes or targets a nonregular object')
            entries[name] = (stat.S_IFLNK | mode, expected['target'].encode())
        else:
            raise ValueError('unsupported runtime object type')
    return entries, manifest, data


def build(runtime_directory, diagnostic_directory, busybox, output):
    supplied_output = Path(output)
    output = HOST.safe_path(output)
    if (not supplied_output.is_absolute() or output.parent != REPO / 'out' or
            not output.name.startswith('installer-initramfs-') or output.name == 'installer-initramfs-' or output.exists()):
        raise ValueError('new absolute repo/out/installer-initramfs-* output required')
    STATE.validate_workspace_storage(output.parent if output.parent.exists() else REPO)
    runtime, manifest, runtime_data = runtime_entries(runtime_directory)
    diagnostic_directory = HOST.safe_path(diagnostic_directory)
    diagnostic = json.loads(HOST.read_regular(HOST.DIAGNOSTIC_RECORD, 1024**2))['files']
    for name in ('Image', 'board.dtb', 'kernel.config'):
        value = HOST.read_regular(diagnostic_directory / name, diagnostic[name]['bytes'], diagnostic[name])
        if name == 'kernel.config':
            INIT.validate_kernel(value)
    diagnostic_init = HOST.read_regular(diagnostic_directory / 'initramfs.cpio.gz', diagnostic['initramfs.cpio.gz']['bytes'], diagnostic['initramfs.cpio.gz'])
    modules = INIT.PAYLOAD.module_entries(gzip.decompress(diagnostic_init))
    busybox_record = {'bytes': 1794520, 'sha256': '0a41864f87a0737804f0f4a68a1cfa1ed22374f4f75a1be3eb376e2659ce1316'}
    binary = HOST.read_regular(busybox, busybox_record['bytes'], busybox_record)
    entries = {}
    for name in ('bin', 'sbin', 'usr/bin', 'usr/sbin', 'dev', 'proc', 'sys', 'run', 'tmp', 'root', 'etc'):
        mode = 0o1777 if name == 'tmp' else 0o700 if name == 'root' else 0o755
        INIT.add(entries, name, stat.S_IFDIR | mode, b'')
    INIT.add(entries, 'bin/busybox', stat.S_IFREG | 0o755, binary)
    INIT.add(entries, 'bin/sh', stat.S_IFLNK | 0o777, b'busybox')
    for name, (mode, value) in sorted(runtime.items()):
        INIT.add(entries, name, mode, value)
    for name, (source, mode) in SOURCES.items():
        INIT.add(entries, name, stat.S_IFREG | mode, (REPO / source).read_bytes())
    generated = {'etc/hosts': (0o644, b'127.0.0.1 localhost\n::1 localhost\n'),
                 'etc/resolv.conf': (0o644, b''),
                 'etc/passwd': (0o644, b'root:x:0:0:root:/root:/bin/sh\n'),
                 'etc/group': (0o644, b'root:x:0:\n'),
                 'etc/shadow': (0o600, b'root:!:1:0:99999:7:::\n'),
                 'usr/share/a733-installer/runtime-manifest.json': (0o644, runtime_data)}
    for name, (mode, value) in generated.items():
        INIT.add(entries, name, stat.S_IFREG | mode, value)
    for path, (mode, value) in modules.items():
        if stat.S_ISDIR(mode) and str(path) in entries:
            continue
        INIT.add(entries, str(path), mode, value)
    archive = INIT.newc(entries)
    if len(archive) > 256 * 1024**2:
        raise ValueError('installer archive exceeds expanded memory budget')
    compressed = gzip.compress(archive, compresslevel=9, mtime=0)
    if len(compressed) + 64 >= 64 * 1024**2:
        raise ValueError('installer initramfs exceeds existing boot load-region budget')
    output.parent.mkdir(exist_ok=True)
    output.mkdir(mode=0o700)
    (output / 'initramfs.cpio.gz').write_bytes(compressed)
    recipes = {'build/installer-initramfs.py', 'build/host-initramfs.py', 'build/host-payload.py',
               'build/installer-runtime.py', 'build/assemble-host.py', 'build/host-state.py',
               'build/root-selection.py', 'build/host-storage-tools.py', 'configs/host_config.py', 'configs/package_state.py',
               'configs/installer-runtime-policy.json', *(s for s, _ in SOURCES.values())}
    result = {'schema': 1, 'classification': 'installer preparation initramfs; no physical installation or hardware acceptance',
              'kernel_release': '6.6.98-a733-diag', 'boot_inputs': diagnostic,
              'runtime_manifest': HOST.record(runtime_data), 'runtime_source': manifest['source'],
              'busybox': busybox_record, 'expanded_bytes': len(archive),
              'files': {'initramfs.cpio.gz': HOST.record(compressed)},
              'recipes': {name: HOST.file_record(REPO / name) for name in sorted(recipes)},
              'entries': {name: {'mode': mode, **HOST.record(value)} for name, (mode, value) in sorted(entries.items())},
              'limitations': ['Physical writer included; physical installation and board acceptance untested.',
                              'Actual board boot, Ethernet/DNS/TLS, SD writes and KVM execution are untested.']}
    (output / 'manifest.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('runtime_directory', 'diagnostic_directory', 'busybox', 'new_output_directory'):
        parser.add_argument(name)
    args = parser.parse_args()
    try:
        result = build(args.runtime_directory, args.diagnostic_directory, args.busybox, args.new_output_directory)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f'installer initramfs: {error}\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'entries'}, indent=2))
