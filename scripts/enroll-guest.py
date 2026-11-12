#!/usr/bin/python3
"""Prepare/apply bounded new guest enrollment; never starts a guest or service.

Production guards require the selected A733 NVMe namespace. File-engine fixtures
are software checks only and cannot establish physical boot or KVM acceptance.
"""
import argparse
import ctypes
import errno
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import select
import signal
import stat
import subprocess
import sys
import time
import uuid

BASE = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, BASE / path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


CONFIG = module('enrollment_config', 'configs/guest_config.py')
ACCOUNTS = module('enrollment_accounts', 'scripts/guest-accounts.py')
MANAGEMENT = ACCOUNTS.MANAGEMENT
STORAGE = module('enrollment_storage', 'scripts/guest-storage.py')
require = CONFIG.require
ROOT = '/run/a733-guests'
LOCK = '/run/a733-guest-enrollment.lock'
CHUNK = 1024 * 1024
RESERVE = 256 * 1024 * 1024
FILENAMES = {'kernel': 'Image', 'initrd': 'initramfs.cpio.gz', 'disk': 'disk.raw'}
PROFILE = 'a733-guest-enrollment-v1'
OPERATION = None


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def identity(info):
    return {key: getattr(info, 'st_' + key) for key in
            ('dev', 'ino', 'mode', 'uid', 'gid', 'nlink', 'size', 'mtime_ns', 'ctime_ns')}


def inode(info):
    return info.st_dev, info.st_ino


def trusted_directory(info):
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == info.st_gid == 0 and
            not info.st_mode & 0o7022, 'unsafe ancestor directory')


class HeldPath:
    """No-follow walk with retained ancestors and repeated named identity checks."""
    def __init__(self, path, *, directory=False):
        self.path = str(path)
        parts = Path(path).parts
        require(parts and parts[0] == '/' and str(Path(path)) == self.path and
                all(part not in ('.', '..') for part in parts), 'canonical absolute path required')
        self.fds = []
        self.names = []
        try:
            fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            self.fds.append(fd)
            trusted_directory(os.fstat(fd))
            for index, part in enumerate(parts[1:]):
                last = index == len(parts) - 2
                flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                if not last or directory:
                    flags |= os.O_DIRECTORY
                following = os.open(part, flags, dir_fd=fd)
                self.fds.append(following)
                self.names.append(part)
                fd = following
                if not last:
                    trusted_directory(os.fstat(fd))
            self.fd = fd
            self.info = os.fstat(fd)
            self.recheck()
        except BaseException:
            self.close()
            raise

    def recheck(self):
        for index, name in enumerate(self.names):
            parent = self.fds[index]
            trusted_directory(os.fstat(parent))
            require(inode(os.stat(name, dir_fd=parent, follow_symlinks=False)) ==
                    inode(os.fstat(self.fds[index + 1])), 'named path changed')
        require(identity(os.fstat(self.fd)) == identity(self.info), 'held input changed')

    def close(self):
        error = None
        for fd in reversed(self.fds):
            try:
                os.close(fd)
            except OSError as caught:
                error = caught
        self.fds.clear()
        if error:
            raise error


def regular(info, mode=None):
    require(stat.S_ISREG(info.st_mode) and info.st_uid == info.st_gid == 0 and
            info.st_nlink == 1 and not info.st_mode & 0o7022 and
            (mode is None or stat.S_IMODE(info.st_mode) == mode), 'unsafe source metadata')


def read_small(held, devices):
    regular(held.info, 0o400)
    require(held.info.st_dev in devices, 'review input must reside on RAM or selected state')
    require(0 < held.info.st_size <= CONFIG.LIMIT, 'invalid review input size')
    os.lseek(held.fd, 0, os.SEEK_SET)
    data = os.read(held.fd, CONFIG.LIMIT + 1)
    held.recheck()
    require(len(data) == held.info.st_size, 'review input size changed')
    return data


def hash_source(held, expected):
    require(held.info.st_size == expected['bytes'], 'source size differs')
    os.lseek(held.fd, 0, os.SEEK_SET)
    digest, count = hashlib.sha256(), 0
    while True:
        data = os.read(held.fd, min(CHUNK, expected['bytes'] - count + 1))
        if not data:
            break
        count += len(data)
        require(count <= expected['bytes'], 'source grew')
        digest.update(data)
    held.recheck()
    require(count == expected['bytes'] and digest.hexdigest() == expected['sha256'],
            'source size/SHA-256 differs')


def probe_uuid(context, expected):
    """Bound blkid output while checking the canonical device on both sides."""
    path = context['guest_source']
    before = os.lstat(path)
    require(stat.S_ISBLK(before.st_mode) and before.st_uid == 0 and
            [os.major(before.st_rdev), os.minor(before.st_rdev)] == context['guest_device'],
            'guest block node differs')
    process = subprocess.Popen(['/sbin/blkid', '-p', '-o', 'export', path],
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               stdin=subprocess.DEVNULL,
                               env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'})
    data, deadline = b'', time.monotonic() + 10
    try:
        while True:
            remaining = deadline - time.monotonic()
            require(remaining > 0, 'blkid timeout')
            require(select.select([process.stdout], [], [], remaining)[0], 'blkid timeout')
            piece = os.read(process.stdout.fileno(), 4097 - len(data))
            if not piece:
                break
            data += piece
            require(len(data) <= 4096, 'oversized blkid output')
        require(process.wait(timeout=max(0.01, deadline - time.monotonic())) == 0, 'blkid failed')
    finally:
        if process.poll() is None:
            process.kill()
        try:
            process.wait(timeout=1)
        finally:
            process.stdout.close()
    require(identity(os.lstat(path)) == identity(before), 'guest block node changed')
    fields = dict(CONFIG.IDENTITY.unique_pairs(line.split('=', 1) for line in data.decode('ascii').splitlines()))
    require(fields.get('UUID') == expected and fields.get('TYPE') == 'ext4', 'guest UUID/type differs')


def budget(config):
    require(os.cpu_count() is not None and config['cpus'] <= os.cpu_count(), 'CPU budget unavailable/exceeded')
    with open('/proc/meminfo', 'rb') as stream:
        data = stream.read(65537)
    require(len(data) <= 65536, 'oversized memory inventory')
    values = [line.split() for line in data.decode('ascii').splitlines() if line.startswith('MemAvailable:')]
    require(len(values) == 1 and len(values[0]) == 3 and values[0][2] == 'kB' and
            values[0][1].isdigit(), 'unmeasured MemAvailable budget refused')
    require(config['memory_max_mib'] + 128 <= int(values[0][1]) // 1024, 'insufficient available RAM')


def absent(parent, name):
    try:
        os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return
    raise ValueError('guest target already exists')


class Review:
    def __init__(self, config_path, kernel, initrd, disk, guest_uuid, plan_path=None):
        self.held = []
        try:
            MANAGEMENT.runtime_guard()
            require(str(uuid.UUID(guest_uuid)) == guest_uuid and uuid.UUID(guest_uuid).int != 0,
                    'canonical nonzero guest UUID required')
            self.root = self.open(ROOT, directory=True)
            info = self.root.info
            require(info.st_uid == info.st_gid == 0 and stat.S_IMODE(info.st_mode) == 0o711,
                    'guest root must be root:root 0711')
            self.context = STORAGE.verify_guest_directory(self.root.fd)
            probe_uuid(self.context, guest_uuid)
            small_devices = {os.stat('/run').st_dev, os.stat(MANAGEMENT.STATE).st_dev}
            self.config_file = self.open(config_path)
            config = CONFIG.validate_config(read_small(self.config_file, small_devices))
            self.config = config
            self.plan_file = self.open(plan_path) if plan_path else None
            try:
                self.supplied = (json.loads(read_small(self.plan_file, small_devices),
                                           object_pairs_hook=CONFIG.IDENTITY.unique_pairs)
                                 if self.plan_file else None)
            except RecursionError as error:
                raise ValueError('excessively nested plan') from error
            ACCOUNTS.validate_accounts({'schema': 1, 'guests': [
                {key: config[key] for key in ('name', 'uid', 'gid')}]}, ACCOUNTS.snapshot(), complete=True)
            budget(config)
            absent(self.root.fd, config['name'])
            required = sum(config[key]['bytes'] for key in FILENAMES) + RESERVE
            space = os.fstatvfs(self.root.fd)
            require(space.f_bavail * space.f_frsize >= required, 'insufficient guest free space')
            allowed = {os.stat('/').st_dev, os.stat(MANAGEMENT.STATE).st_dev, info.st_dev}
            self.sources = {key: self.open(path) for key, path in
                            (('kernel', kernel), ('initrd', initrd), ('disk', disk))}
            # All metadata and storage checks precede every payload read/hash.
            for source in self.sources.values():
                regular(source.info)
                require(source.info.st_dev in allowed, 'payload must reside on selected NVMe storage')
            for key, source in self.sources.items():
                hash_source(source, config[key])
            self.plan = {'schema': 1, 'operation': PROFILE, 'config': config,
                         'config_source': {'path': self.config_file.path, 'identity': identity(self.config_file.info)},
                         'guest_uuid': guest_uuid, 'storage': self.context,
                         'sources': {key: {'path': source.path, 'identity': identity(source.info), **config[key]}
                                     for key, source in self.sources.items()},
                         'required_free_bytes': required}
            self.plan['plan_sha256'] = hashlib.sha256(canonical(self.plan)).hexdigest()
            if self.plan_file:
                require(canonical(self.supplied) == canonical(self.plan), 'stale or mismatched enrollment plan')
        except BaseException:
            self.close()
            raise

    def open(self, path, **kwargs):
        result = HeldPath(path, **kwargs)
        self.held.append(result)
        return result

    def recheck(self):
        MANAGEMENT.runtime_guard()
        # Directory timestamps change during staging; only its inode/metadata policy matters.
        root_info = os.fstat(self.root.fd)
        require(inode(root_info) == inode(self.root.info) and root_info.st_uid == root_info.st_gid == 0 and
                stat.S_IMODE(root_info.st_mode) == 0o711, 'guest root changed')
        self.root.info = root_info
        for held in self.held:
            held.recheck()
        require(STORAGE.verify_guest_directory(self.root.fd) == self.context, 'storage namespace changed')
        probe_uuid(self.context, self.plan['guest_uuid'])
        ACCOUNTS.validate_accounts({'schema': 1, 'guests': [
            {key: self.config[key] for key in ('name', 'uid', 'gid')}]}, ACCOUNTS.snapshot(), complete=True)
        budget(self.config)
        absent(self.root.fd, self.config['name'])

    def close(self):
        error = None
        for held in reversed(self.held):
            try:
                held.close()
            except OSError as caught:
                error = caught
        self.held.clear()
        if error:
            raise error


def operation_module():
    # The helper imports lifecycle context -> policy -> this module. Loading it
    # only after enrollment initialization avoids an import cycle.
    global OPERATION
    if OPERATION is None:
        OPERATION = module('enrollment_operation', 'scripts/guest-operation-admission.py')
    return OPERATION


def discover_config(path):
    """Pin only the protected small config before selecting its guest lock."""
    require(os.getresuid() == (0, 0, 0), 'actual root required for enrollment')
    MANAGEMENT.runtime_guard()
    held = HeldPath(path)
    try:
        devices = {os.stat('/run').st_dev, os.stat(MANAGEMENT.STATE).st_dev}
        data = read_small(held, devices)
        config = CONFIG.validate_config(data)
        return held, data, config
    except BaseException:
        held.close()
        raise


def bind_config(held, data, config, review, operation):
    operation.recheck()
    devices = {os.stat('/run').st_dev, os.stat(MANAGEMENT.STATE).st_dev}
    require(read_small(held, devices) == data and review.config == config and
            operation.name == config['name'] and
            review.plan['config_source'] == {'path': held.path, 'identity': identity(held.info)},
            'enrollment config differs from admitted guest')
    review.config_file.recheck()


def prepare(config, kernel, initrd, disk, guest_uuid):
    discovered = operation = review = None
    try:
        discovered, data, selected = discover_config(config)
        operation = operation_module().ManualGuestOperation(selected['name'], create=True)
        review = Review(config, kernel, initrd, disk, guest_uuid)
        bind_config(discovered, data, selected, review, operation)
        return review.plan
    finally:
        error = None
        for resource in (review, discovered, operation):
            if resource is not None:
                try:
                    resource.close()
                except BaseException as caught:
                    error = caught
        if error:
            raise error


def rename_new(old_parent, old, new_parent, new):
    library = ctypes.CDLL(None, use_errno=True)
    try:
        rename = library.renameat2
    except AttributeError as error:
        raise OSError(errno.ENOSYS, 'atomic no-replace publication unavailable') from error
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(old_parent, os.fsencode(old), new_parent, os.fsencode(new), 1):
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number))


def write_all(fd, data):
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        require(written > 0, 'short write')
        view = view[written:]


def copy_source(source, target, expected, receipt):
    os.lseek(source.fd, 0, os.SEEK_SET)
    count, digest = 0, hashlib.sha256()
    while True:
        data = os.read(source.fd, min(CHUNK, expected['bytes'] - count + 1))
        if not data:
            break
        count += len(data)
        require(count <= expected['bytes'], 'source grew during staging')
        digest.update(data)
        receipt['bytes_attempted_staged'] += len(data)
        if data.strip(b'\0'):
            write_all(target, data)
        else:
            os.lseek(target, len(data), os.SEEK_CUR)
    source.recheck()
    require(count == expected['bytes'] and digest.hexdigest() == expected['sha256'],
            'staged source size/SHA-256 differs')
    os.ftruncate(target, count)
    # Independent complete readback of the new inode, including sparse holes.
    os.lseek(target, 0, os.SEEK_SET)
    digest, length = hashlib.sha256(), 0
    while True:
        data = os.read(target, min(CHUNK, expected['bytes'] - length + 1))
        if not data:
            break
        length += len(data)
        require(length <= expected['bytes'], 'staged inode grew')
        digest.update(data)
    require(length == count and digest.hexdigest() == expected['sha256'], 'staged readback differs')


def same_entry(parent, name, expected):
    try:
        return inode(os.stat(name, dir_fd=parent, follow_symlinks=False)) == expected
    except FileNotFoundError:
        return False


def stage_and_publish(review, receipt):
    root, config = review.root.fd, review.config
    try:
        os.mkdir('.enrollment', 0o700, dir_fd=root)
    except FileExistsError:
        pass
    private = os.open('.enrollment', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
    stage = None
    owned = {}
    temporary = secrets.token_hex(16)
    stage_inode = None
    try:
        info = os.fstat(private)
        require(info.st_uid == info.st_gid == 0 and stat.S_IMODE(info.st_mode) == 0o700 and
                info.st_dev == os.fstat(root).st_dev, 'unsafe enrollment staging parent')
        private_inode = inode(info)
        require(same_entry(root, '.enrollment', private_inode), 'staging parent changed')
        os.mkdir(temporary, 0o700, dir_fd=private)
        receipt['staging_retained'] = True
        stage = os.open(temporary, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=private)
        stage_inode = inode(os.fstat(stage))

        def create(name, callback, uid, gid, mode):
            fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=stage)
            owned[name] = inode(os.fstat(fd))
            try:
                callback(fd)
                os.fchown(fd, uid, gid)
                os.fchmod(fd, mode)
                os.fsync(fd)
            finally:
                os.close(fd)

        records = {}
        for key, name in FILENAMES.items():
            uid, gid, mode = (config['uid'], config['gid'], 0o600) if key == 'disk' else (0, 0, 0o444)
            create(name, lambda fd, key=key: copy_source(review.sources[key], fd, config[key], receipt),
                   uid, gid, mode)
            records[name] = {**config[key], 'uid': uid, 'gid': gid, 'mode': format(mode, '04o')}
        manifest = {'schema': 1, 'profile': PROFILE, 'config': config,
                    'guest_uuid': review.plan['guest_uuid'], 'files': records}
        manifest_data = canonical(manifest)
        receipt['manifest_sha256'] = hashlib.sha256(manifest_data).hexdigest()
        create('enrollment.json', lambda fd: write_all(fd, manifest_data), 0, 0, 0o400)
        os.fchown(stage, 0, config['gid'])
        os.fchmod(stage, 0o710)
        os.fsync(stage)
        review.recheck()
        require(same_entry(root, '.enrollment', private_inode) and
                same_entry(private, temporary, stage_inode), 'staging namespace changed')
        current_private = os.fstat(private)
        require(current_private.st_uid == current_private.st_gid == 0 and
                stat.S_IMODE(current_private.st_mode) == 0o700, 'private staging metadata changed')
        stage_info = os.fstat(stage)
        require(stage_info.st_uid == 0 and stage_info.st_gid == config['gid'] and
                stat.S_IMODE(stage_info.st_mode) == 0o710, 'staging directory metadata changed')
        require(set(os.listdir(stage)) == set(owned), 'unexpected staged entry')
        for name, expected in owned.items():
            require(same_entry(stage, name, expected), 'staged file replaced')
            file_info = os.stat(name, dir_fd=stage, follow_symlinks=False)
            expected_metadata = records.get(name, {'uid': 0, 'gid': 0, 'mode': '0400',
                                                    'bytes': len(canonical(manifest))})
            require(stat.S_ISREG(file_info.st_mode) and file_info.st_nlink == 1 and
                    file_info.st_uid == expected_metadata['uid'] and
                    file_info.st_gid == expected_metadata['gid'] and
                    stat.S_IMODE(file_info.st_mode) == int(expected_metadata['mode'], 8) and
                    file_info.st_size == expected_metadata['bytes'], 'staged file metadata changed')
        review.lock_check()
        previous = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM})
        try:
            receipt['directory_may_be_published'] = True
            try:
                rename_new(private, temporary, root, config['name'])
                receipt['published'] = True
                receipt['staging_retained'] = False
                os.fsync(root)
                os.fsync(private)
                receipt['durable'] = True
            finally:
                if same_entry(root, config['name'], stage_inode):
                    receipt['published'] = True
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous)
        receipt['success'] = True
    finally:
        try:
            # Reconcile before cleanup even if a publish helper returned by interruption.
            if stage_inode and same_entry(root, config['name'], stage_inode):
                receipt['published'] = True
                receipt['staging_retained'] = False
            if (stage is not None and not receipt['published'] and
                    same_entry(private, temporary, stage_inode)):
                for name, expected in owned.items():
                    if same_entry(stage, name, expected):
                        os.unlink(name, dir_fd=stage)
                if same_entry(private, temporary, stage_inode):
                    try:
                        os.rmdir(temporary, dir_fd=private)
                        receipt['staging_retained'] = False
                    except OSError:
                        receipt['staging_retained'] = True
        finally:
            close_error = None
            for fd in (stage, private):
                if fd is not None:
                    try:
                        os.close(fd)
                    except (OSError, KeyboardInterrupt) as error:
                        close_error = error
            if close_error:
                raise close_error


def apply(config, kernel, initrd, disk, guest_uuid, plan):
    receipt = {'schema': 1, 'operation': PROFILE, 'success': False,
               'directory_may_be_published': False, 'published': False, 'durable': False,
               'bytes_attempted_staged': 0}
    discovered = operation = lock = review = None
    try:
        discovered, data, selected = discover_config(config)
        operation = operation_module().ManualGuestOperation(selected['name'], create=True)
        lock = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        info = os.fstat(lock)
        regular(info, 0o600)
        require(info.st_dev == os.stat('/run').st_dev and identity(os.lstat(LOCK)) == identity(info),
                'unsafe enrollment RAM lock')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        review = Review(config, kernel, initrd, disk, guest_uuid, plan)
        def lock_check():
            bind_config(discovered, data, selected, review, operation)
            require(identity(os.fstat(lock)) == identity(info) and
                    identity(os.lstat(LOCK)) == identity(info), 'enrollment lock changed')
        review.lock_check = lock_check
        lock_check()
        receipt['plan_sha256'] = review.plan['plan_sha256']
        stage_and_publish(review, receipt)
        operation.recheck()
    except BaseException as error:
        receipt['success'] = False
        receipt['error'] = type(error).__name__
    finally:
        for resource in (review, lock, discovered, operation):
            if resource is not None:
                try:
                    os.close(resource) if isinstance(resource, int) else resource.close()
                except BaseException as error:
                    receipt['success'] = False
                    receipt['cleanup_error'] = type(error).__name__
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for action in ('prepare', 'apply'):
        command = commands.add_parser(action)
        for name in ('config', 'kernel', 'initrd', 'disk', 'guest-uuid'):
            command.add_argument('--' + name, required=True)
        if action == 'apply':
            command.add_argument('--plan', required=True)
    args = parser.parse_args(argv)
    os.umask(0o077)
    def interrupted(_signum, _frame):
        raise KeyboardInterrupt
    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        arguments = (args.config, args.kernel, args.initrd, args.disk, args.guest_uuid)
        result = apply(*arguments, args.plan) if args.command == 'apply' else prepare(*arguments)
        print(canonical(result).decode(), end='')
        return 0 if result.get('success', True) else 1
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, UnicodeError, subprocess.SubprocessError, KeyboardInterrupt,
            RuntimeError) as error:
        print(json.dumps({'success': False, 'error': type(error).__name__}), file=sys.stderr)
        sys.exit(1)
