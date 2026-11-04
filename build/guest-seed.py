#!/usr/bin/env python3
"""Build the fixed disposable guest seed, using pinned Jammy filesystem tools.

No mounts, device arguments, arbitrary input trees or configurable image metadata.
Reproducibility is scoped to the pinned builder, not other e2fsprogs versions.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import selectors
import stat
import subprocess
import struct
import uuid
import time

REPO = Path(__file__).resolve().parents[1]
SIZE = 128 * 1024**2
EPOCH = 1793750400
UUID = 'd67f33ba-e201-46bc-980b-61901bbd1156'
CONTENTS = {'fixture-id': b'a733-kvm-guest-fixture-v1\n', 'counter': b'0\n'}
FEATURES = ('extent,filetype,flex_bg,sparse_super,large_file,huge_file,'
            'dir_nlink,extra_isize,metadata_csum,64bit,dir_index,ext_attr')
PIN = '1.46.5-2ubuntu1.2'


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


TRUST = module('guest_seed_trust', REPO / 'build/maintenance-tools.py')
STATE = module('guest_seed_state', REPO / 'build/host-state.py')


def run(args, *, fd=None, cwd=None):
    """Bound wall time and captured output, including failed commands."""
    env = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C', 'TZ': 'UTC',
           'E2FSPROGS_FAKE_TIME': str(EPOCH), 'MKE2FS_CONFIG': '/dev/null'}
    with subprocess.Popen(args, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          pass_fds=() if fd is None else (fd,)) as process:
        data = bytearray()
        deadline = time.monotonic() + 60
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ValueError('filesystem tool deadline exceeded')
                    for key, _ in selector.select(min(remaining, 1)):
                        block = os.read(key.fd, 65536)
                        if not block:
                            selector.unregister(key.fd)
                        else:
                            data.extend(block)
                            if len(data) > 1024**2:
                                raise ValueError('filesystem tool output exceeds bound')
            code = process.wait(timeout=max(0.01, deadline - time.monotonic()))
            if code:
                raise ValueError(f'{args[0]} failed ({code}): {data[-4096:].decode("utf-8", "replace")}')
            return bytes(data)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()


def check_versions():
    for package in ('e2fsprogs', 'libext2fs2'):
        actual = run(['dpkg-query', '-W', '-f=${Version}', package]).decode('ascii')
        if actual != PIN:
            raise ValueError(f'{package} must match pinned Jammy version {PIN}, got {actual}')


def verify_name(output, parent_fd, fd):
    TRUST.verify_parent(output, parent_fd)
    opened = os.fstat(fd)
    named = os.stat(output.name, dir_fd=parent_fd, follow_symlinks=False)
    if (TRUST.inode(named) != TRUST.inode(opened) or not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1 or opened.st_size != SIZE):
        raise ValueError('seed output identity changed')


def format_seed(fd, stage):
    cwd = Path(f'/proc/{os.getpid()}/fd/{stage.fd}')
    image = f'/proc/self/fd/{fd}'
    run(['mke2fs', '-q', '-F', '-t', 'ext4', '-b', '4096', '-g', '32768',
         '-G', '16', '-I', '256', '-i', '16384', '-m', '0', '-O', 'none,' + FEATURES,
         '-E', f'lazy_itable_init=0,lazy_journal_init=0,hash_seed={UUID},root_owner=0:0',
         '-U', UUID, '-L', 'kvm-fixture', image, str(SIZE // 4096)], fd=fd)
    commands = [f'set_current_time @{EPOCH}']
    for name, data in CONTENTS.items():
        stage.write(name, data, 0o600)
        commands.append(f'write {name} /{name}')
    for name in ('/', '/lost+found', '/fixture-id', '/counter'):
        mode = '040755' if name == '/' else '040700' if name == '/lost+found' else '0100644'
        for field, value in [('uid', '0'), ('gid', '0'), ('mode', mode),
                             *[(field, '@' + str(EPOCH)) for field in ('atime', 'mtime', 'ctime', 'crtime')],
                             *[(field + '_extra', '0') for field in ('atime', 'mtime', 'ctime', 'crtime')]]:
            commands.append(f'set_inode_field {name} {field} {value}')
    stage.write('commands', ('\n'.join(commands) + '\n').encode('ascii'), 0o600)
    stage.verify()
    result = run(['debugfs', '-w', '-f', 'commands', image], fd=fd, cwd=cwd)
    # debugfs can report failed requests while returning success.
    if b'Command not found' in result or b'while ' in result or b'not found' in result:
        raise ValueError('debugfs seed metadata operation failed')
    run(['e2fsck', '-f', '-n', image], fd=fd)
    # Independent raw superblock decoding complements filesystem-tool checks.
    # Decode documented ext4 fields; never alter raw filesystem bytes.
    superblock = os.pread(fd, 1024, 1024)
    word = lambda offset: struct.unpack_from('<I', superblock, offset)[0]
    blocks = word(4) | (word(336) << 32)
    if (len(superblock) != 1024 or superblock[56:58] != b'\x53\xef' or
            superblock[104:120] != uuid.UUID(UUID).bytes or
            superblock[120:136].rstrip(b'\0') != b'kvm-fixture' or
            word(92) & 0x4 or word(24) != 2 or blocks * 4096 != SIZE or
            word(44) != 0 or word(48) != EPOCH or word(64) != EPOCH or word(264) != EPOCH):
        raise ValueError('seed raw superblock readback mismatch')
    for name, expected in CONTENTS.items():
        result = run(['debugfs', '-R', f'cat /{name}', image], fd=fd)
        # debugfs emits one version banner on stderr (merged by bounded runner).
        if result.split(b'\n', 1)[1] != expected:
            raise ValueError('seed content readback mismatch')
    header = run(['dumpe2fs', '-h', image], fd=fd).decode('ascii')
    fields = dict(line.split(':', 1) for line in header.splitlines() if ':' in line)
    if (fields.get('Filesystem UUID', '').strip() != UUID or
            fields.get('Filesystem volume name', '').strip() != 'kvm-fixture' or
            fields.get('Block size', '').strip() != '4096' or
            fields.get('Block count', '').strip() != str(SIZE // 4096) or
            set(fields.get('Filesystem features', '').split()) != set(FEATURES.split(','))):
        raise ValueError('seed filesystem geometry/features readback mismatch')
    for name in ('/', '/lost+found', '/fixture-id', '/counter'):
        result = run(['debugfs', '-R', f'stat {name}', image], fd=fd).decode('ascii')
        mode = '0755' if name == '/' else '0700' if name == '/lost+found' else '0644'
        if f'Mode:  {mode}' not in result:
            raise ValueError('seed mode readback mismatch')
        if 'User:     0   Group:     0' not in result:
            raise ValueError('seed owner readback mismatch')
        for field in ('ctime', 'atime', 'mtime', 'crtime'):
            if f'{field}: 0x{EPOCH:08x}:00000000' not in result:
                raise ValueError(f'seed {field} readback mismatch')


def build(output):
    output = Path(output)
    if not output.is_absolute() or '..' in output.parts or not output.name:
        raise ValueError('new absolute output path required')
    parent_fd = TRUST.open_trusted_directory(output.parent)
    fd = None
    created = None
    try:
        try:
            os.stat(output.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ValueError('seed output must not exist')
        # Resolve backing storage through the held parent before creating anything.
        STATE.validate_workspace_storage(Path(f'/proc/{os.getpid()}/fd/{parent_fd}'))
        TRUST.verify_parent(output, parent_fd)
        check_versions()
        fd = os.open(output.name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=parent_fd)
        created = TRUST.inode(os.fstat(fd))
        os.ftruncate(fd, SIZE)
        verify_name(output, parent_fd, fd)
        stage = TRUST.Stage(parent_fd)
        try:
            format_seed(fd, stage)
            stage.verify()
        finally:
            try:
                stage.cleanup()
            finally:
                stage.close()
        verify_name(output, parent_fd, fd)
        os.fchmod(fd, 0o644)
        os.fsync(fd)
        digest = hashlib.sha256()
        os.lseek(fd, 0, os.SEEK_SET)
        length = 0
        while block := os.read(fd, 1024**2):
            digest.update(block)
            length += len(block)
        if length != SIZE:
            raise ValueError('seed full readback length mismatch')
        verify_name(output, parent_fd, fd)
        os.fsync(parent_fd)
        return {'schema': 1, 'profile': 'a733-disposable-guest-seed-v1',
                'bytes': length, 'sha256': digest.hexdigest(), 'uuid': UUID,
                'label': 'kvm-fixture', 'source_date_epoch': EPOCH,
                'e2fsprogs_version': PIN, 'hardware_acceptance': 'pending'}
    except BaseException:
        if created is not None:
            try:
                if TRUST.inode(os.stat(output.name, dir_fd=parent_fd,
                                       follow_symlinks=False)) == created:
                    os.unlink(output.name, dir_fd=parent_fd)
                    os.fsync(parent_fd)
            except FileNotFoundError:
                pass
        raise
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent_fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path, metavar='NEW_ABSOLUTE_OUTPUT')
    args = parser.parse_args()
    try:
        print(json.dumps(build(args.output), sort_keys=True))
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, f'{error}\n')


if __name__ == '__main__':
    main()
