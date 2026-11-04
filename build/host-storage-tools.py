#!/usr/bin/env python3
"""Extract a pinned ARM64 blkid runtime into a new regular-file staging tree.

No mounts, device access, package execution, network or boot-image modification.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import tarfile

REPO = Path(__file__).resolve().parent.parent


def digest(stream):
    value = hashlib.sha256()
    size = 0
    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
        size += len(chunk)
        value.update(chunk)
    return {'bytes': size, 'sha256': value.hexdigest()}


def relative(name):
    path = PurePosixPath(name)
    if not name or path.is_absolute() or '..' in path.parts or path == PurePosixPath('.'):
        raise ValueError(f'unsafe relative path: {name!r}')
    return str(path)


def selected_files(archive, lock):
    """Read only named regular entries; never use tar extraction or follow links."""
    wanted = {relative(record['source']) for record in lock['files'].values()}
    wanted.update(relative(name) for name in lock['notices'].values())
    entries = {}
    for member in archive.getmembers():
        name = str(PurePosixPath(member.name))
        if name not in wanted:
            continue
        if name in entries or not member.isfile() or member.size > 8 * 1024**2:
            raise ValueError(f'duplicate, nonregular or oversized selected member: {name}')
        stream = archive.extractfile(member)
        with stream:
            entries[name] = stream.read()
    if set(entries) != wanted:
        raise ValueError(f'missing selected members: {sorted(wanted - set(entries))}')
    for target, record in lock['files'].items():
        relative(target)
        data = entries[relative(record['source'])]
        actual = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
        if actual != {key: record[key] for key in ('bytes', 'sha256')}:
            raise ValueError(f'pinned binary mismatch: {target}')
        # ELF64 little endian, AArch64. Dynamic interpreter/NEEDED closure was
        # inspected separately against these exact pinned bytes with readelf.
        if data[:6] != b'\x7fELF\x02\x01' or data[18:20] != b'\xb7\x00':
            raise ValueError(f'not an ELF64 little-endian AArch64 binary: {target}')
    return entries


def package(source, output):
    lock = json.loads((REPO / 'configs/host-storage-tools.lock.json').read_text())
    evidence = json.loads((REPO / lock['source']).read_text())
    expected = evidence['files']['rootfs.tar.gz']
    # Pin the same descriptor through authentication and archive reading.
    descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('source must be a regular non-symlink file')
        if digest(stream) != expected:
            raise ValueError('userspace archive hash/size mismatch')
        stream.seek(0)
        with tarfile.open(fileobj=stream, mode='r:gz') as archive:
            entries = selected_files(archive, lock)
    output = Path(output)
    # Keep outputs under the ignored workspace; prohibit symlink traversal.
    absolute = Path(os.path.abspath(output))
    if not absolute.is_relative_to(REPO / 'out') or absolute == REPO / 'out':
        raise ValueError('output must be a new directory below repository out/')
    for parent in reversed(absolute.parents):
        if parent.is_symlink():
            raise ValueError(f'symlink output ancestor: {parent}')
    absolute.parent.mkdir(parents=True, exist_ok=True)
    absolute.mkdir(mode=0o700)  # Existing outputs are never overwritten.
    records = {}
    mapping = {target: item['source'] for target, item in lock['files'].items()}
    mapping.update(lock['notices'])
    for target, name in mapping.items():
        path = absolute / relative(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = entries[relative(name)]
        with path.open('xb') as destination:
            destination.write(data)
        path.chmod(0o755 if target in lock['files'] else 0o644)
        records[target] = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    manifest = {'schema': 1, 'classification': 'early-boot storage tool staging; no boot or hardware acceptance',
                'source': expected, 'packages': lock['packages'], 'files': records}
    (absolute / 'storage-tools-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('userspace_tar')
    parser.add_argument('new_output')
    args = parser.parse_args()
    try:
        result = package(args.userspace_tar, args.new_output)
    except (OSError, ValueError, tarfile.TarError) as error:
        parser.exit(1, f'{error}\n')
    print(json.dumps(result, indent=2))
