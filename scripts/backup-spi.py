#!/usr/bin/python3
"""Read the known A733 SPI NOR to private tmpfs storage; never program flash.

A successful local backup still requires an off-device copy/hash verification and
an independently demonstrated recovery route before any SPI programming.
"""
import argparse
import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import sys

SIZE = 16 * 1024 * 1024
ERASE = 65536
RESERVE = 8 * 1024 * 1024
CHUNK = 1024 * 1024
# Linux UAPI include/uapi/mtd/mtd-abi.h: struct mtd_info_user, MEMGETINFO.
# Fixed 32-byte layout on Linux arm64 and x86_64, including the u64 padding.
MTD_INFO = struct.Struct('=B3xIIIIIQ')
MEMGETINFO = 0x80204D01
READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


def require(condition, message):
    if not condition:
        raise ValueError(message)


def mtd_info(fd):
    data = bytearray(MTD_INFO.size)
    fcntl.ioctl(fd, MEMGETINFO, data, True)
    kind, flags, size, erase, write, oob, _ = MTD_INFO.unpack(data)
    require((kind, size, erase, write, oob) == (3, SIZE, ERASE, 1, 0),
            'MEMGETINFO is not the complete expected NOR geometry')
    return dict(type=kind, flags=flags, size=size, erasesize=erase,
                writesize=write, oobsize=oob)


def canonical_device(device):
    require(re.fullmatch(r'/dev/mtd(?:0|[1-9][0-9]*)', device) is not None,
            'source must be a canonical /dev/mtdN character device')
    return device.rsplit('/', 1)[1]


def require_no_swap():
    """tmpfs may spill to disk when swap is active; fail closed on uncertainty."""
    lines = Path('/proc/swaps').read_text(encoding='ascii').splitlines()
    require(len(lines) == 1 and lines[0].split() ==
            ['Filename', 'Type', 'Size', 'Used', 'Priority'],
            'RAM-only backup requires a valid /proc/swaps header and no active swap')


def identity(fd, device):
    require_no_swap()
    name = canonical_device(device)
    info = os.fstat(fd)
    require(stat.S_ISCHR(info.st_mode), 'source is not a character device')
    root = Path('/sys/class/mtd') / name
    read = lambda path: path.read_text(encoding='ascii').strip()
    expected = dict(name='spi0.0', type='nor', size=str(SIZE),
                    erasesize=str(ERASE), writesize='1')
    observed = {key: read(root / key) for key in expected}
    require(observed == expected, 'unexpected SPI sysfs identity/geometry')
    dev = read(root / 'dev')
    require(dev == f'{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}',
            'source major/minor does not match sysfs')
    spi = Path('/sys/bus/spi/devices/spi0.0').resolve(strict=True)
    target = root.resolve(strict=True)
    require(spi in target.parents and
            Path('/sys/dev/char', dev).resolve(strict=True) == target,
            'MTD device does not belong to the expected SPI node')
    part = read(spi / 'spi-nor/partname')
    jedec = read(spi / 'spi-nor/jedec_id')
    require((part, jedec) == ('XM25QU128C', '204118'), 'unexpected SPI part/JEDEC ID')
    partition_map = Path('/proc/mtd').read_text(encoding='ascii')
    lines = partition_map.splitlines()
    require(lines == ['dev:    size   erasesize  name',
                      f'{name}: 01000000 00010000 "spi0.0"'],
            'unexpected or ambiguous complete MTD partition map')
    require(sorted(p.name for p in Path('/sys/class/mtd').iterdir()
                   if re.fullmatch(r'mtd[0-9]+', p.name)) == [name],
            'additional MTD devices or partitions present')
    return dict(device=device, dev=dev, sysfs=str(target), spi=str(spi),
                partname=part, jedec_id=jedec, sysfs_geometry=observed,
                proc_mtd=partition_map, ioctl=mtd_info(fd))


def open_source(device):
    canonical_device(device)
    fd = os.open(device, READ_FLAGS)
    try:
        snapshot = identity(fd, device)
        return fd, snapshot
    except BaseException:
        os.close(fd)
        raise


def ram_filesystem(fd):
    # fstatfs reads the kernel filesystem type of the retained directory FD;
    # mount-table text and a pathname prefix alone cannot establish RAM storage.
    libc = ctypes.CDLL(None, use_errno=True)
    result = (ctypes.c_long * 32)()
    require(libc.fstatfs(fd, ctypes.byref(result)) == 0,
            'cannot inspect output filesystem')
    require(result[0] == 0x01021994, 'output is not tmpfs')


def safe_directory(fd, private=False):
    info = os.fstat(fd)
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and
            not info.st_mode & 0o022 and
            (not private or stat.S_IMODE(info.st_mode) == 0o700),
            'output ancestors must be root owned, nonsymlink and not writable by others')


def create_output(path):
    require_no_swap()
    parts = Path(path).parts
    require(len(parts) >= 3 and parts[:2] == ('/', 'run') and
            str(Path(path)) == path and all(p not in ('.', '..') for p in parts),
            'output must be a new directory strictly under /run')
    fd = os.open('/', READ_FLAGS | os.O_DIRECTORY)
    try:
        safe_directory(fd)
        for component in parts[1:-1]:
            next_fd = os.open(component, READ_FLAGS | os.O_DIRECTORY, dir_fd=fd)
            os.close(fd)
            fd = next_fd
            safe_directory(fd)
            ram_filesystem(fd)
        space = os.fstatvfs(fd)
        require(space.f_bavail * space.f_frsize >= SIZE + RESERVE,
                'insufficient tmpfs capacity (16 MiB plus 8 MiB reserve)')
        require_no_swap()
        os.mkdir(parts[-1], 0o700, dir_fd=fd)
        next_fd = os.open(parts[-1], READ_FLAGS | os.O_DIRECTORY, dir_fd=fd)
        os.close(fd)
        fd = next_fd
        safe_directory(fd, private=True)
        ram_filesystem(fd)
        return fd
    except BaseException:
        os.close(fd)
        raise


def hash_read(fd, sink=None, progress=None, reader=os.pread):
    digest = hashlib.sha256()
    offset = 0
    while offset < SIZE:
        data = reader(fd, min(CHUNK, SIZE - offset), offset)
        require(0 < len(data) <= min(CHUNK, SIZE - offset), 'short/invalid SPI read')
        digest.update(data)
        if sink is not None:
            view = memoryview(data)
            while view:
                written = os.write(sink, view)
                require(0 < written <= len(view), 'invalid backup write')
                view = view[written:]
        offset += len(data)
        if progress:
            progress(offset)
    require(reader(fd, 1, SIZE) == b'', 'source exceeds expected size')
    return digest.hexdigest()


def backup(fd, snapshot, directory, guard, progress=None, reader=os.pread):
    require(guard() == snapshot, 'SPI identity changed before backup')
    out = os.open('spi.bin', os.O_RDWR | os.O_CREAT | os.O_EXCL |
                  os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=directory)
    try:
        info = os.fstat(out)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and
                stat.S_IMODE(info.st_mode) == 0o600, 'unsafe backup file')
        first = hash_read(fd, out, progress, reader)
        os.fsync(out)
        require(guard() == snapshot, 'SPI identity changed after first read')
        second = hash_read(fd, progress=progress, reader=reader)
        require(guard() == snapshot, 'SPI identity changed after second read')
        require(first == second, 'SPI changed between complete reads')
        require(os.fstat(out).st_size == SIZE and hash_read(out) == first,
                'backup file bytes/hash do not match SPI')
        require(guard() == snapshot, 'SPI identity changed during output verification')
        manifest = dict(status='verified-local-ram-backup', bytes=SIZE, sha256=first,
                        source=snapshot, source_reads=2, image='spi.bin',
                        off_device_retention_verified=False, recovery_proven=False)
        manifest_fd = os.open('manifest.json.partial', os.O_WRONLY | os.O_CREAT |
                              os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600,
                              dir_fd=directory)
        with os.fdopen(manifest_fd, 'w', encoding='ascii') as stream:
            json.dump(manifest, stream, indent=2, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.rename('manifest.json.partial', 'manifest.json',
                  src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
        return manifest
    finally:
        os.close(out)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('plan', 'backup'))
    parser.add_argument('--device', default='/dev/mtd0')
    parser.add_argument('--output', help='new private directory strictly under /run (backup only)')
    args = parser.parse_args()
    if args.operation == 'backup' and not args.output:
        parser.error('backup requires --output')
    if args.operation == 'plan' and args.output:
        parser.error('plan does not accept --output')
    fd, snapshot = open_source(args.device)
    try:
        if args.operation == 'plan':
            require(identity(fd, args.device) == snapshot, 'SPI identity changed during inspection')
            print(json.dumps(dict(operation='read-only-plan', source=snapshot,
                                  backup_bytes=SIZE, writes_to_flash=False), indent=2))
        else:
            directory = create_output(args.output)
            try:
                def progress(count):
                    print(f'SPI read: {count}/{SIZE} bytes', file=sys.stderr, flush=True)
                result = backup(fd, snapshot, directory,
                                lambda: identity(fd, args.device), progress)
                print(json.dumps(result, indent=2, sort_keys=True))
            finally:
                os.close(directory)
    finally:
        os.close(fd)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as error:
        print(f'SPI backup refused/failed: {error}', file=sys.stderr)
        sys.exit(1)
