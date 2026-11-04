#!/usr/bin/env python3
"""Opt-in root test of a regular ext4 image; never accepts a physical device."""
import errno
import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tempfile


def sha256_fd(descriptor):
    result = hashlib.sha256()
    offset = 0
    while True:
        block = os.pread(descriptor, 1024 * 1024, offset)
        if not block:
            break
        result.update(block)
        offset += len(block)
    return result.hexdigest()


def inspect_image(descriptor, info):
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('Only a regular image file is accepted; no symlinks or devices')
    superblock = os.pread(descriptor, 1024, 1024)
    if len(superblock) != 1024 or struct.unpack_from('<H', superblock, 56)[0] != 0xEF53:
        raise ValueError('Expected ext4 superblock')
    compatible, _, ro_compatible = struct.unpack_from('<III', superblock, 92)
    if compatible & 0x4 or not ro_compatible & 0x1000:
        raise ValueError('Image must have READONLY feature and no journal')
    blocks = struct.unpack_from('<I', superblock, 4)[0]
    blocks |= struct.unpack_from('<I', superblock, 0x150)[0] << 32
    block_size = 1024 << struct.unpack_from('<I', superblock, 24)[0]
    if info.st_size != blocks * block_size or not 16 * 1024**2 <= info.st_size <= 4 * 1024**3:
        raise ValueError('Unsupported or inconsistent image byte count')


def copy_sparse_image(descriptor, destination, size):
    """Copy the validated object, preserving zero ranges as holes."""
    with destination.open('xb') as target:
        offset = 0
        while offset < size:
            block = os.pread(descriptor, min(1024 * 1024, size - offset), offset)
            if not block:
                raise ValueError('Image became shorter during staging')
            if block.strip(b'\0'):
                target.write(block)
            else:
                target.seek(len(block), os.SEEK_CUR)
            offset += len(block)
        target.truncate(size)


def verify(filename):
    if os.geteuid() != 0:
        raise ValueError('Requires root in a private mount namespace')
    path = Path(filename).absolute()
    original = path.lstat()
    if not stat.S_ISREG(original.st_mode):
        raise ValueError('Only a regular non-symlink image file is accepted')
    descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if (info.st_dev, info.st_ino) != (original.st_dev, original.st_ino):
            raise ValueError('Image identity changed before opening')
        inspect_image(descriptor, info)
        verify_open_image(path, descriptor, info)
    finally:
        os.close(descriptor)


def verify_open_image(path, descriptor, info):
    before = sha256_fd(descriptor)
    loop = None
    mounted = False
    # losetup canonicalizes /proc/self/fd paths before opening them. Attach a
    # matching private copy under a root-controlled sticky directory instead.
    parent = Path('/var/tmp')
    parent_info = parent.lstat()
    if not stat.S_ISDIR(parent_info.st_mode) or parent_info.st_uid != 0 or (
            parent_info.st_mode & 0o022 and not parent_info.st_mode & stat.S_ISVTX):
        raise ValueError('Private staging parent must be root-controlled')
    with tempfile.TemporaryDirectory(prefix='host-root-loop-', dir=parent) as temporary:
        staged = Path(temporary) / 'root.img'
        copy_sparse_image(descriptor, staged, info.st_size)
        with staged.open('rb') as staged_source:
            if sha256_fd(staged_source.fileno()) != before:
                raise ValueError('Private staging copy does not match validated image')
        mountpoint = Path(temporary) / 'root'
        mountpoint.mkdir()
        try:
            # The loop is writable so filesystem behavior, not a block-device
            # read-only flag, must reject the remount and write probe.
            loop = subprocess.check_output(['losetup', '--find', '--show', str(staged)], text=True).strip()
            read_only = subprocess.check_output(['blockdev', '--getro', loop], text=True).strip()
            if read_only != '0':
                raise RuntimeError('Loop is not writable; filesystem denial would be unproven')
            subprocess.run(['mount', '-t', 'ext4', '-o', 'ro,noload', loop, str(mountpoint)], check=True)
            mounted = True
            remount = subprocess.run(['mount', '-o', 'remount,rw', str(mountpoint)],
                                     text=True, capture_output=True)
            if remount.returncode == 0:
                raise RuntimeError('Read-write remount unexpectedly succeeded')
            options = subprocess.check_output(['findmnt', '-rn', '-o', 'OPTIONS',
                                               '--target', str(mountpoint)], text=True).strip()
            if 'ro' not in options.split(',') or 'rw' in options.split(','):
                raise RuntimeError('Filesystem is not mounted read-only')
            try:
                with (mountpoint / 'host-write-probe').open('xb'):
                    pass
            except OSError as error:
                if error.errno != errno.EROFS:
                    raise
            else:
                raise RuntimeError('Write probe unexpectedly succeeded')
        finally:
            try:
                if mounted:
                    subprocess.run(['umount', str(mountpoint)], check=True)
            finally:
                if loop:
                    subprocess.run(['losetup', '-d', loop], check=True)
        with staged.open('rb') as staged_source:
            if sha256_fd(staged_source.fileno()) != before:
                raise RuntimeError('Staged image changed across mount/write-denial test')
    if sha256_fd(descriptor) != before:
        raise RuntimeError('Image bytes changed across the mount/write-denial test')
    after = path.lstat()
    if not stat.S_ISREG(after.st_mode) or (after.st_dev, after.st_ino) != (info.st_dev, info.st_ino):
        raise RuntimeError('Image path identity changed during test')
    print(json.dumps({'host_file_loop_test': 'passed', 'kernel': os.uname().release,
                      'image_bytes': info.st_size, 'sha256': before,
                      'loop_block_read_only': False,
                      'attachment': 'byte-identical private staged regular file',
                      'staged_image_unchanged': True,
                      'read_write_remount': 'rejected', 'write_probe': 'EROFS',
                      'image_unchanged': True, 'physical_board_acceptance': 'pending'}, indent=2))


if __name__ == '__main__':
    if len(sys.argv) != 2 or os.geteuid() != 0:
        sys.exit(f'Usage: sudo python3 {sys.argv[0]} REGULAR_EXT4_READONLY_IMAGE')
    # Loading as a module in the child avoids an environment-variable bypass.
    os.execvp('unshare', ['unshare', '--mount', '--propagation', 'private', '--fork',
                         sys.executable, '-c',
                         "import runpy,sys; runpy.run_path(sys.argv[1])['verify'](sys.argv[2])",
                         str(Path(__file__).resolve()), sys.argv[1]])
