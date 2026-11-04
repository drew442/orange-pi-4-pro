#!/usr/bin/env python3
"""Opt-in native block-I/O tests using only freshly created private loop files.

No physical NVMe, SD or SPI target can be supplied. This does not exercise the
production CLI's whole-NVMe authorization policy or establish hardware acceptance.
"""
import errno
import fcntl
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
import sys
import tempfile
import time

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('nvme_writer_loop_engine', REPO / 'scripts/install-nvme.py')
WRITER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WRITER)
STATE_SPEC = importlib.util.spec_from_file_location('nvme_loop_storage', REPO / 'build/host-state.py')
STATE = importlib.util.module_from_spec(STATE_SPEC)
STATE_SPEC.loader.exec_module(STATE)
SIZE = 128 * 1024**2


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(fd, size):
    value = hashlib.sha256()
    offset = 0
    while offset < size:
        data = os.pread(fd, min(4 * 1024**2, size - offset), offset)
        require(data, 'short read')
        value.update(data)
        offset += len(data)
    return {'bytes': offset, 'sha256': value.hexdigest()}


def owned_loop(loop, backing):
    require(re.fullmatch(r'/dev/loop[0-9]+', loop) is not None, 'unexpected loop device')
    reported = (Path('/sys/class/block') / Path(loop).name / 'loop/backing_file').read_text().strip()
    actual = Path('/' + reported.lstrip('/')).resolve(strict=True)
    require(actual == backing and actual.stat().st_uid == 0, 'loop backing identity differs')
    source = backing.lstat()
    require(stat.S_ISREG(source.st_mode) and source.st_nlink == 1 and source.st_size == SIZE,
            'private backing file metadata differs')


def verify():
    require(os.geteuid() == 0, 'root required for private file-backed loop tests')
    require(shutil.which('udevadm') is not None, 'builder udevadm required to settle new loop probes')
    storage = STATE.validate_workspace_storage('/var/tmp')
    require(shutil.disk_usage('/var/tmp').free >= 1024**3, '1 GiB fixed storage free required')
    results = {}
    with tempfile.TemporaryDirectory(prefix='a733-writer-loop-', dir='/var/tmp') as temporary:
        root = Path(temporary)
        source_path = root / 'source.img'
        with source_path.open('xb') as source:
            source.truncate(SIZE)
            mbr = bytearray(512)
            struct.pack_into('<B3sB3sII', mbr, 446, 0, b'\0\2\0', 0x83, b'\xff\xff\xff', 2048, (SIZE-1024**2)//512)
            mbr[510:512] = b'\x55\xaa'
            source.seek(0)
            source.write(mbr)
            source.seek(4 * 1024**2 + 4096)
            source.write(b'private loop source marker\n')
            source.seek(SIZE - 4096)
            source.write(b'last private block\n')
        source_path.chmod(0o600)
        with source_path.open('rb') as source:
            expected = digest(source.fileno(), SIZE)
            for case in ('complete', 'interrupted_then_rewrite', 'corrupted_readback'):
                backing = root / (case + '.img')
                with backing.open('xb') as target:
                    for _ in range(SIZE // 1024**2):
                        target.write(b'\xa5' * 1024**2)
                backing.chmod(0o600)
                loop = None
                fd = None
                try:
                    loop = subprocess.check_output(['losetup', '--find', '--show', '--partscan', str(backing)], text=True).strip()
                    owned_loop(loop, backing)
                    # The builder's udev probe can briefly hold a shared flock
                    # on a newly attached loop. Settle events before acquiring
                    # the same exclusive lock required by production; never
                    # bypass or weaken that lock on contention.
                    subprocess.run(['udevadm', 'settle', '--timeout=10'], check=True, capture_output=True, timeout=15)
                    fd = os.open(loop, os.O_RDWR | os.O_EXCL | os.O_NOFOLLOW | os.O_NONBLOCK)
                    require(stat.S_ISBLK(os.fstat(fd).st_mode), 'loop descriptor must be block')
                    require(os.fstat(fd).st_rdev == os.stat(loop).st_rdev, 'loop identity changed')
                    size_buffer = bytearray(8)
                    fcntl.ioctl(fd, WRITER.BLKGETSIZE64, size_buffer, True)
                    require(struct.unpack('=Q', size_buffer)[0] == SIZE, 'loop byte size differs')
                    queue = Path('/sys/class/block') / Path(loop).name / 'queue'
                    device = Path(loop).stat().st_rdev
                    WRITER.validate_fd(fd, {'major_minor': f'{os.major(device)}:{os.minor(device)}',
                                           'size_bytes': SIZE,
                                           'logical_sector_bytes': int((queue / 'logical_block_size').read_text()),
                                           'physical_sector_bytes': int((queue / 'physical_block_size').read_text())})
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    try:
                        other = os.open(loop, os.O_RDWR | os.O_EXCL | os.O_NOFOLLOW)
                    except OSError as error:
                        require(error.errno == errno.EBUSY, 'unexpected exclusive-open refusal')
                    else:
                        os.close(other)
                        raise ValueError('second exclusive block claim unexpectedly succeeded')
                    calls = []
                    def source_check():
                        owned_loop(loop, backing)
                        require(source_path.stat().st_size == SIZE, 'private source changed')
                    def flush(held):
                        calls.append('flush')
                        fcntl.ioctl(held, WRITER.BLKFLSBUF)
                        if case == 'corrupted_readback':
                            os.pwrite(held, b'X', 8 * 1024**2)
                            os.fsync(held)
                            fcntl.ioctl(held, WRITER.BLKFLSBUF)
                    def refresh(held):
                        calls.append('refresh')
                        fcntl.ioctl(held, WRITER.BLKRRPART)
                    def progress(done, total):
                        if case == 'interrupted_then_rewrite':
                            raise KeyboardInterrupt('intentional private-loop interruption')
                    report = WRITER.stream_copy_verify(source, fd, expected,
                                flush_callback=flush, refresh_callback=refresh,
                                source_check=source_check, progress=progress)
                    if case == 'complete':
                        require(report['success'] and report['target_modified'], 'complete copy failed')
                        require(report['bytes_verified'] == SIZE and calls == ['flush', 'refresh'], 'verification/refresh incomplete')
                        # The kernel must recognize the MBR from the written file.
                        part = Path('/sys/class/block') / (Path(loop).name + 'p1')
                        require((part / 'start').read_text().strip() == '2048', 'kernel partition start differs')
                        require(int((part / 'size').read_text()) == (SIZE-1024**2)//512, 'kernel partition size differs')
                        with backing.open('rb') as raw:
                            require(digest(raw.fileno(), SIZE) == expected, 'backing-file readback differs')
                        results[case] = {'report': report, 'exclusive_second_open_refused': True,
                                         'kernel_partition_readback': True, 'production_descriptor_geometry_ioctls': True, 'nonzero_old_bytes_and_zero_ranges_replaced': True}
                    elif case == 'interrupted_then_rewrite':
                        require(not report['success'] and report['target_modified'] and report['bytes_verified'] == 0,
                                'interruption report is not conservative')
                        require(0 < report['bytes_written'] < SIZE and not calls, 'interruption did not stop before flush')
                        require(os.pread(fd, 4096, 16 * 1024**2) == b'\xa5' * 4096, 'unwritten tail unexpectedly changed')
                        rewritten = WRITER.stream_copy_verify(source, fd, expected,
                                       flush_callback=flush, refresh_callback=refresh, source_check=source_check)
                        require(rewritten['success'] and digest(fd, SIZE) == expected, 'full rewrite failed')
                        results[case] = {'interrupted_report': report, 'unwritten_old_tail_preserved': True,
                                         'explicit_full_rewrite_report': rewritten}
                    else:
                        require(not report['success'] and report['target_modified'] and report['bytes_verified'] == SIZE,
                                'corruption did not fail full readback')
                        require('SHA-256 mismatch' in report.get('error', '') and calls == ['flush'],
                                'corruption unexpectedly refreshed partitions or escaped detection')
                        results[case] = {'report': report, 'partition_refresh_skipped': True}
                finally:
                    try:
                        if fd is not None:
                            os.close(fd)
                    finally:
                        if loop is not None:
                            owned_loop(loop, backing)
                            subprocess.run(['losetup', '--detach', loop], check=True)
                            attribute = Path('/sys/class/block') / Path(loop).name / 'loop/backing_file'
                            for _ in range(50):
                                if not attribute.exists():
                                    break
                                time.sleep(0.1)
                            else:
                                raise ValueError('owned loop detach did not complete')
            require(digest(source.fileno(), SIZE) == expected, 'private source changed')
    return {'classification': 'native kernel loop-device byte-engine checks only; no physical NVMe or hardware acceptance',
            'writer_source': {'bytes': (REPO / 'scripts/install-nvme.py').stat().st_size,
                              'sha256': hashlib.sha256((REPO / 'scripts/install-nvme.py').read_bytes()).hexdigest()},
            'harness_source': {'bytes': Path(__file__).stat().st_size, 'sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
            'storage': storage, 'fixture_bytes': SIZE, 'cases': results,
            'cleanup': 'all owned loops detached and private files deleted',
            'limitations': ['Production whole-NVMe prepare/apply/authorization paths use separate mocked policy tests.',
                           'Loop interruptions are not physical power-cut, NVMe firmware or rollback validation.',
                           'No SD, NVMe or SPI target erased; actual KVM guest acceptance remains pending.']}


if __name__ == '__main__':
    if len(sys.argv) != 1:
        sys.exit('No device arguments accepted; uses private file-backed loops only')
    print(json.dumps(verify(), indent=2))
