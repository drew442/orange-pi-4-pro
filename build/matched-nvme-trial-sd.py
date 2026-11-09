#!/usr/bin/env python3
"""Produce an UNBOOTED matched NVMe-first trial SD using regular files only.

Run from a trusted, quiescent root/Drew-owned checkout. No mounts, subprocesses,
filesystem edits, physical device routes, or caller-selected pins are supported.
"""
import argparse
from contextlib import ExitStack
import gzip
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import stat
import sys
import time

REPO = Path(__file__).absolute().parents[1]
BASE = (268435456, 'ab5e8c2c846cbbd51b61bef6831d7e4d1498c29fefbf47d74301b1f3bb0ad207')
SPI = (16777216, '83edcd7f448e8353163666016a6bae3e9b70d041a5e4d8454f48931839205e90')
PACKAGE = (1392640, 'bdfe070a2c358de9dd0061b39609f604c0c12e9c25140903505f88799cc343be')
SPI_START = 262144
SD_START = 16793600
BOOT_START = 33554432
STATE_UUID = '360dfb39-f49f-487e-b307-0bb887c854c5'
TARGET = 'nvme-candidate-20261107-ram02'
CLOSURE = {
    'configs/uboot-nvme-candidate-targets.lock.json': (4035, 'abb6f3136f6df22c3da0b7a205b7d2b968e3faad7a3589a3fb69c1c2689117c5'),
    'docs/evidence/uboot-nvme-candidate-20261107.json': (16835, 'da2f319c342beaf692e4f392ef622ab307748d9c586709c186d9f04a1d7ca01c'),
    'docs/evidence/protected-state-bootstrap-20261109.json': (111012, '6782997ba86f7a7fa8eef3302d11f6afa8359b58af6c8cbd86ac53539d79feee'),
    'docs/evidence/spi-original-backup-20261108.json': (4794, 'f91282dbe6156138bc2d1e86a5612c1de477e41a624fbf02fe0edcdf1396bd99'),
}
CHUNK = 1024 * 1024
DISK_FLOOR = 3000000000
SCRATCH = 1024**3
MEMORY_FLOOR = 1000000000
TRUSTED_UIDS = {0, pwd.getpwnam('drew').pw_uid}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def record(size, digest):
    return {'bytes': size, 'sha256': digest}


def identity(st):
    return st.st_dev, st.st_ino, st.st_uid, st.st_mode


def fingerprint(st):
    return identity(st) + (st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_nlink)


def trusted(st, directory=False):
    require(st.st_uid in TRUSTED_UIDS and not st.st_mode & 0o002, 'untrusted owner/world-writable path')
    require(stat.S_ISDIR(st.st_mode) if directory else stat.S_ISREG(st.st_mode), 'expected directory' if directory else 'expected regular file')
    if not directory:
        require(st.st_nlink == 1, 'hardlinked file refused')


def absolute(path):
    path = os.fspath(path)
    require(path.startswith('/') and all(p not in ('', '.', '..') for p in path.split('/')[1:]), 'absolute canonical path required')
    require(path.split('/')[1] not in ('dev', 'proc', 'sys'), 'device/pseudo-filesystem path refused')
    return Path(path)


class HeldPath:
    """Open each path component with no-follow and retain its parent descriptor."""
    def __init__(self, path, directory=False):
        self.path = absolute(path)
        self.fds = []
        self.links = []
        try:
            fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            self.fds.append(fd)
            trusted(os.fstat(fd), True)
            parts = self.path.parts[1:]
            for index, name in enumerate(parts):
                isdir = directory or index < len(parts) - 1
                child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | (os.O_DIRECTORY if isdir else 0), dir_fd=fd)
                self.fds.append(child)
                st = os.fstat(child)
                trusted(st, isdir)
                self.links.append((fd, name, child, identity(st)))
                fd = child
            self.fd = fd
            self.directory = directory
            self.initial = fingerprint(os.fstat(fd))
            self.check()
        except BaseException:
            self.close()
            raise

    def check(self):
        for parent, name, child, expected in self.links:
            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
            require(identity(current) == expected == identity(os.fstat(child)), 'path/ancestor drift')
            trusted(current, stat.S_ISDIR(current.st_mode))
        if not self.directory:
            require(fingerprint(os.fstat(self.fd)) == self.initial, 'source mutation')

    def close(self):
        for fd in reversed(self.fds):
            os.close(fd)
        self.fds = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def chunks(fd, start=0, length=None):
    if length is None:
        length = os.fstat(fd).st_size - start
    require(length >= 0, 'invalid range')
    while length:
        data = os.pread(fd, min(CHUNK, length), start)
        require(data, 'truncated input')
        yield data
        start += len(data)
        length -= len(data)


def digest(fd, start=0, length=None):
    h = hashlib.sha256()
    size = 0
    for data in chunks(fd, start, length):
        h.update(data)
        size += len(data)
    return size, h.hexdigest()


def authenticate(held, pin):
    held.check()
    require(os.fstat(held.fd).st_size == pin[0], 'input size pin mismatch')
    require(digest(held.fd) == pin, 'input SHA-256 pin mismatch')
    held.check()


def resources(fd, phase, admission=False):
    fs = os.fstatvfs(fd)
    free = fs.f_bavail * fs.f_frsize
    with open('/proc/meminfo', encoding='ascii') as stream:
        memory = next(int(line.split()[1]) * 1024 for line in stream if line.startswith('MemAvailable:'))
    require(free >= DISK_FLOOR + (SCRATCH if admission else 0), 'disk admission/floor')
    require(memory >= MEMORY_FLOOR, 'MemAvailable floor')
    print(f'{phase}: free={free} MemAvailable={memory}', file=sys.stderr, flush=True)
    return {'phase': phase, 'free_bytes': free, 'available_memory_bytes': memory}


def write_all(fd, data):
    view = memoryview(data)
    while view:
        count = os.write(fd, view)
        require(count > 0, 'short output write')
        view = view[count:]


def compare(base_fd, raw_fd, spi_fd):
    """Compare every byte, independently of construction; hash excluded ranges."""
    outside = hashlib.sha256()
    for start, end in ((0, SD_START), (SD_START + PACKAGE[0], BASE[0])):
        for offset in range(start, end, CHUNK):
            length = min(CHUNK, end - offset)
            before = os.pread(base_fd, length, offset)
            after = os.pread(raw_fd, length, offset)
            require(len(before) == length and before == after, 'outside-package byte mismatch')
            outside.update(after)
    for offset in range(0, PACKAGE[0], CHUNK):
        length = min(CHUNK, PACKAGE[0] - offset)
        require(os.pread(raw_fd, length, SD_START + offset) == os.pread(spi_fd, length, SPI_START + offset), 'package byte mismatch')
    require(os.fstat(raw_fd).st_size == BASE[0], 'raw length mismatch')
    return {'ranges': [[0, SD_START], [SD_START + PACKAGE[0], BASE[0]]],
            'concatenated_sha256': outside.hexdigest(), 'byte_for_byte_equal': True,
            'boot_partition': record(*digest(raw_fd, BOOT_START, BASE[0] - BOOT_START))}


def verify_gzip(fd, raw_pin):
    require(os.fstat(fd).st_size <= BASE[0] + 1024**2, 'gzip size bound')
    h = hashlib.sha256()
    size = 0
    with os.fdopen(os.dup(fd), 'rb') as compressed:
        compressed.seek(0)
        with gzip.GzipFile(fileobj=compressed, mode='rb') as stream:
            while True:
                data = stream.read(min(CHUNK, raw_pin[0] + 1 - size))
                if not data:
                    break
                size += len(data)
                require(size <= raw_pin[0], 'gzip expansion bound')
                h.update(data)
    require((size, h.hexdigest()) == raw_pin, 'gzip/raw mismatch')


def produce(base_sd, spi_candidate, output):
    output = absolute(output)
    require(output.parent == REPO / 'out' and re.fullmatch(r'matched-nvme-trial-sd-[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', output.name), 'output must be fresh REPO/out/matched-nvme-trial-sd-TAG')
    started = time.monotonic()
    with ExitStack() as stack:
        repo = stack.enter_context(HeldPath(REPO, True))
        parent = stack.enter_context(HeldPath(output.parent, True))
        samples = [resources(parent.fd, 'admission', True)]
        base = stack.enter_context(HeldPath(base_sd))
        spi = stack.enter_context(HeldPath(spi_candidate))
        authenticate(base, BASE)
        authenticate(spi, SPI)
        require(digest(spi.fd, SPI_START, PACKAGE[0]) == PACKAGE, 'SPI extraction package pin mismatch')
        sources = {}
        closure = []
        for relative, pin in CLOSURE.items():
            held = stack.enter_context(HeldPath(REPO / relative))
            authenticate(held, pin)
            sources[relative] = record(*pin)
            closure.append((held, pin))
        producer = stack.enter_context(HeldPath(Path(__file__).absolute()))
        producer_pin = digest(producer.fd)
        sources['build/matched-nvme-trial-sd.py'] = record(*producer_pin)
        closure.append((producer, producer_pin))
        registry_path = next(iter(CLOSURE))
        registry = json.loads(os.pread(closure[0][0].fd, CLOSURE[registry_path][0], 0))
        registered = registry['targets'][TARGET]['required_artifacts']['artifacts/boot_package.fex']
        require(registered == record(*PACKAGE), 'registered package binding mismatch')
        repo.check()
        parent.check()
        os.mkdir(output.name, 0o700, dir_fd=parent.fd)
        out = stack.enter_context(HeldPath(output, True))
        require(os.fstat(out.fd).st_mode & 0o777 == 0o700, 'private output required')
        os.fsync(parent.fd)
        artifacts = {}
        opened = []
        sealed = {}

        def create(name):
            out.check()
            fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=out.fd)
            stack.callback(os.close, fd)
            opened.append((name, fd, identity(os.fstat(fd))))
            return fd

        def check_paths():
            repo.check()
            parent.check()
            out.check()
            for held, _pin in [(base, BASE), (spi, SPI)] + closure:
                held.check()
            for name, fd, expected in opened:
                st = os.stat(name, dir_fd=out.fd, follow_symlinks=False)
                trusted(st)
                require(identity(st) == expected == identity(os.fstat(fd)), 'output file drift')
                if name in sealed:
                    require(fingerprint(st) == sealed[name], 'output mutation')

        raw = create('a733-host-sd.img')
        samples.append(resources(out.fd, 'copy'))
        for data in chunks(base.fd):
            write_all(raw, data)
        os.lseek(raw, SD_START, os.SEEK_SET)
        for data in chunks(spi.fd, SPI_START, PACKAGE[0]):
            write_all(raw, data)
        os.fsync(raw)
        samples.append(resources(out.fd, 'preservation'))
        preservation = compare(base.fd, raw, spi.fd)
        raw_pin = digest(raw)
        artifacts['a733-host-sd.img'] = record(*raw_pin)
        sealed['a733-host-sd.img'] = fingerprint(os.fstat(raw))
        check_paths()
        gz = create('a733-host-sd.img.gz')
        samples.append(resources(out.fd, 'gzip'))
        with os.fdopen(os.dup(gz), 'wb') as sink:
            with gzip.GzipFile(filename='', mode='wb', compresslevel=9, mtime=0, fileobj=sink) as stream:
                for data in chunks(raw):
                    stream.write(data)
        os.fsync(gz)
        samples.append(resources(out.fd, 'gzip-verification'))
        verify_gzip(gz, raw_pin)
        artifacts['a733-host-sd.img.gz'] = record(*digest(gz))
        sealed['a733-host-sd.img.gz'] = fingerprint(os.fstat(gz))
        for held, pin in [(base, BASE), (spi, SPI)] + closure:
            authenticate(held, pin)
        require(digest(raw) == raw_pin, 'raw output mutation')
        # Recheck preservation after compression and all source reauthentication.
        require(compare(base.fd, raw, spi.fd) == preservation, 'preservation drift')
        samples.append(resources(out.fd, 'final'))
        check_paths()
        manifest = {
            'schema': 1, 'status': 'UNBOOTED', 'hardware_acceptance': False,
            'physical_device_writes': False, 'state_uuid': STATE_UUID,
            'inputs': {'base_sd': {'path': str(base.path), **record(*BASE)},
                       'spi_candidate': {'path': str(spi.path), **record(*SPI)}},
            'registered_target': TARGET, 'package': record(*PACKAGE),
            'preserved_bootstack': {
                'provenance': 'docs/evidence/protected-state-bootstrap-20261109.json',
                'kernel_sha256': '6f9269755c4c2bc196c094899fa1ff20292f4d762c4000f77fcbb0887d5f154a',
                'vgic_dtb_sha256': '1ba8f5926c0d76d12479bafcfcfce66be4a934ccfb6a5bcbcc7f7aa86c07f9f4',
                'protected_init_sha256': '2cf52e1cee8d4f2050fe3a90eca0a0a8c50b6f30718d210667f7273f9f08ab12',
                'authentication': 'fixed complete base raw pin; complete BOOT preserved byte for byte',
            },
            'spi_extraction_range': [SPI_START, SPI_START + PACKAGE[0]],
            'changed_range': [SD_START, SD_START + PACKAGE[0]],
            'preservation': preservation, 'source_closure': sources,
            'outputs': artifacts, 'gzip': {'mtime': 0, 'filename': '', 'level': 9, 'full_crc_and_raw_hash_verified': True},
            'resources': samples, 'elapsed_seconds': time.monotonic() - started,
            'tools': {'python': sys.version},
            'remaining_acceptance': ['physical default NVMe boot and manual SD fallback', 'zero routine SD writes', 'real KVM guest', 'SPI recovery and SD-removed boot', 'remaining project release gates'],
        }
        # The complete receipt is the final publication. Remove it on any failure,
        # including directory durability or final path/resource checks.
        published = False
        try:
            receipt = create('manifest.json')
            published = True
            write_all(receipt, (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode())
            os.fsync(receipt)
            check_paths()
            resources(out.fd, 'publication')
            os.fsync(out.fd)
            os.fsync(parent.fd)
            os.fsync(repo.fd)
            check_paths()
        except BaseException:
            if published:
                os.unlink('manifest.json', dir_fd=out.fd)
                os.fsync(out.fd)
            raise
        return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-sd', required=True)
    parser.add_argument('--spi-candidate', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        produce(args.base_sd, args.spi_candidate, args.output)
    except (OSError, ValueError, KeyError, EOFError) as error:
        parser.exit(1, f'FAILED (private partial output may remain): {error}\n')
    print(f'UNBOOTED regular-file trial SD complete: {args.output}', flush=True)


if __name__ == '__main__':
    main()
