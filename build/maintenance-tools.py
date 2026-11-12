#!/usr/bin/env python3
"""Package the closed A733 maintenance source tree; never install or run it."""
import argparse
import ctypes
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import secrets
import stat
import tarfile

REPO = Path(__file__).resolve().parents[1]
PROFILE = 'a733-maintenance-tools-v1'
KERNEL_RELEASE = '6.6.98-a733-diag'
LEGACY_SOURCES = tuple(sorted((
    'scripts/prepare-host-update.py', 'scripts/update-host.py', 'scripts/select-host-root.py',
    'scripts/recover-host-root.py', 'scripts/host-update-target.py', 'scripts/host-recovery-target.py',
    'scripts/install-nvme.py', 'scripts/acquire-nvme.py', 'scripts/plan-nvme.py', 'scripts/nvme_layout.py',
    'build/root-selection.py', 'build/host-initramfs.py', 'build/host-payload.py', 'build/timesync-state.py',
    'build/host-storage-tools.py', 'configs/package_state.py', 'configs/host-management.py',
    'configs/host_config.py', 'docs/evidence/diagnostic-20261104.json',
    'configs/guest_config.py', 'configs/guest_identity.py', 'configs/guest_manifest.py',
    'configs/guest_backup.py', 'configs/guest_backup_chunks.py', 'build/host-state.py',
    'scripts/activate-guest-storage.py', 'scripts/guest-mount-target.py',
    'scripts/guest-accounts.py', 'scripts/guest-storage.py', 'scripts/enroll-guest.py',
    'scripts/launch-kvm-guest.py', 'scripts/guest-checkpoint-policy.py',
    'scripts/checkpoint-guest.py', 'scripts/backup-guest.py', 'scripts/verify-guest-backup.py',
    'scripts/index-guest-backup.py', 'scripts/guest-restore-source.py',
    'scripts/guest-restore-https.py', 'scripts/restore-guest.py')))
# Historical private SSH recovery archives preserve these exact 39 names.
# Extend current packaging without redefining their authenticated boundary.
SOURCES = tuple(sorted(LEGACY_SOURCES + (
    'configs/guest_registry.py', 'configs/guest_lifecycle.py', 'configs/guest_qmp.py',
    'scripts/guest-admission.py', 'scripts/guest-lifecycle-context.py',
    'scripts/guest-lifecycle-store.py', 'scripts/guest-lifecycle.py',
    'configs/guest_run_permit.py', 'scripts/guest-process-evidence.py',
    'scripts/guest-qmp-channel.py', 'scripts/guest-checkpoint-transaction.py',
    'scripts/launch-managed-guest.py',
    'scripts/managed-guest-supervisor.py',
    'scripts/managed-guest-fixture.py', 'configs/managed-guest-profile.lock.json',
    'configs/guest_checkpoint_records.py', 'scripts/managed-guest-checkpoint.py',
    'scripts/guest-operation-admission.py', 'scripts/guest-launch-admission.py',
    'scripts/guest-host-maintenance.py', 'configs/guest_managed_backup.py',
    'scripts/backup-managed-guest.py',
    'scripts/reopen-guest-admission.py',
    'scripts/managed-guest-archive.py', 'scripts/managed-guest-restore-source.py',
    'scripts/verify-managed-guest-backup.py',
    'scripts/guest-boot-state.py',
)))
ENTRYPOINTS = {name: f'scripts/{name}.py' for name in (
    'install-nvme', 'acquire-nvme', 'plan-nvme', 'prepare-host-update',
    'update-host', 'select-host-root', 'recover-host-root',
    'activate-guest-storage', 'guest-accounts', 'enroll-guest', 'launch-kvm-guest',
    'checkpoint-guest', 'backup-guest', 'verify-guest-backup', 'index-guest-backup',
    'restore-guest', 'backup-managed-guest', 'reopen-guest-admission',
    'verify-managed-guest-backup')}
MAX_SOURCE = 2 * 1024**2
MAX_PAYLOAD = 4 * 1024**2


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True) + '\n').encode('ascii')


def record(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def read_source(path):
    """Walk held directory descriptors, deny symlinks and detect source mutation."""
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('absolute canonical source path required')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:-1]:
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = following
        before = os.stat(path.name, dir_fd=fd, follow_symlinks=False)
        source_fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(source_fd, 'rb') as stream:
            opened = os.fstat(stream.fileno())
            if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or
                    not 0 < opened.st_size <= MAX_SOURCE or identity(before) != identity(opened)):
                raise ValueError('source must be a stable bounded single-link regular file')
            data = stream.read(MAX_SOURCE + 1)
            after = os.fstat(stream.fileno())
        current = os.stat(path.name, dir_fd=fd, follow_symlinks=False)
        if (identity(opened) != identity(after) or identity(opened) != identity(current) or
                len(data) != opened.st_size):
            raise ValueError('source changed while reading')
        return data
    finally:
        os.close(fd)


def entries(repo=None):
    root = REPO if repo is None else Path(repo)
    result = {name: (0o444, read_source(root / name)) for name in SOURCES}
    if sum(len(data) for _, data in result.values()) > MAX_PAYLOAD:
        raise ValueError('maintenance payload exceeds budget')
    records = {name: {'mode': mode, **record(data)} for name, (mode, data) in result.items()}
    metadata = {'schema': 1, 'profile': PROFILE, 'kernel_release': KERNEL_RELEASE,
                'entrypoints': ENTRYPOINTS, 'uid': 0, 'gid': 0,
                'source_records': records, 'files': records}
    result['package.json'] = (0o444, canonical(metadata))
    if sum(len(data) for _, data in result.values()) > MAX_PAYLOAD:
        raise ValueError('maintenance payload including metadata exceeds budget')
    return result


def archive(entries):
    objects = {'a733-tools': None}
    for name, value in entries.items():
        path = Path('a733-tools') / name
        for parent in path.parents:
            if str(parent) != '.':
                objects[str(parent)] = None
        objects[str(path)] = value
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode='w', format=tarfile.USTAR_FORMAT) as tar:
        for name, value in sorted(objects.items()):
            info = tarfile.TarInfo(name)
            info.uid = info.gid = info.mtime = 0
            info.uname = info.gname = ''
            info.mode = 0o555 if value is None else 0o444
            info.type = tarfile.DIRTYPE if value is None else tarfile.REGTYPE
            info.size = 0 if value is None else len(value[1])
            tar.addfile(info, None if value is None else io.BytesIO(value[1]))
    return gzip.compress(raw.getvalue(), compresslevel=9, mtime=0)


def check_directory(info):
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.geteuid()) or
            info.st_mode & 0o022):
        raise ValueError('all output ancestors must be trusted caller/root-owned directories without shared writes')


def open_trusted_directory(path):
    """Keep the walk on directory FDs and check every absolute ancestor."""
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('absolute canonical directory required')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        check_directory(os.fstat(fd))
        for name in path.parts[1:]:
            following = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = following
            check_directory(os.fstat(fd))
        return fd
    except BaseException:
        os.close(fd)
        raise


def inode(info):
    return (info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode))


def validate_storage(fd):
    spec = importlib.util.spec_from_file_location('maintenance_builder_state', REPO / 'build/host-state.py')
    state = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(state)
    # findmnt follows the held object, even if its original name is replaced.
    state.validate_workspace_storage(Path('/proc') / str(os.getpid()) / 'fd' / str(fd))


def validate_output(output):
    output = Path(output)
    if (not output.is_absolute() or '..' in output.parts or output.parent != REPO / 'out' or
            not output.name.startswith('maintenance-tools-') or output.name == 'maintenance-tools-'):
        raise ValueError('new absolute repo/out/maintenance-tools-NAME output required')
    return output


def open_output_parent(output):
    repo_fd = open_trusted_directory(REPO)
    parent_fd = None
    try:
        validate_storage(repo_fd)
        try:
            parent_fd = os.open('out', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=repo_fd)
        except FileNotFoundError:
            os.mkdir('out', mode=0o700, dir_fd=repo_fd)
            parent_fd = os.open('out', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=repo_fd)
        check_directory(os.fstat(parent_fd))
        validate_storage(parent_fd)
        verify_parent(output, parent_fd)
        try:
            os.stat(output.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ValueError('output must not exist')
        return parent_fd
    except BaseException:
        if parent_fd is not None:
            os.close(parent_fd)
        raise
    finally:
        os.close(repo_fd)


def verify_parent(output, parent_fd):
    named_fd = open_trusted_directory(output.parent)
    try:
        if inode(os.fstat(named_fd)) != inode(os.fstat(parent_fd)):
            raise ValueError('output parent changed')
    finally:
        os.close(named_fd)


def publish(parent_fd, source, destination):
    libc = ctypes.CDLL(None, use_errno=True)
    rename = libc.renameat2
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(parent_fd, os.fsencode(source), parent_fd, os.fsencode(destination), 1):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), destination)


class Stage:
    """Track only objects we created; cleanup never follows names or symlinks."""
    def __init__(self, parent_fd):
        self.parent_fd = parent_fd
        self.objects = {}
        self.directories = {}
        self.name = '.maintenance-tools-' + secrets.token_hex(16)
        os.mkdir(self.name, mode=0o700, dir_fd=parent_fd)
        self.fd = os.open(self.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        self.directories[''] = self.fd
        self.objects[''] = (parent_fd, self.name, inode(os.fstat(self.fd)))

    def directory(self, name):
        if name in self.directories:
            return self.directories[name]
        path = Path(name)
        parent = self.directory('' if str(path.parent) == '.' else str(path.parent))
        os.mkdir(path.name, mode=0o700, dir_fd=parent)
        fd = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        self.directories[name] = fd
        self.objects[name] = (parent, path.name, inode(os.fstat(fd)))
        return fd

    def write(self, name, data, mode):
        path = Path(name)
        parent = self.directory('' if str(path.parent) == '.' else str(path.parent))
        fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        self.objects[name] = (parent, path.name, inode(os.fstat(fd)))
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            os.fchmod(stream.fileno(), mode)

    def verify(self):
        for parent, basename, expected in self.objects.values():
            if inode(os.stat(basename, dir_fd=parent, follow_symlinks=False)) != expected:
                raise ValueError('staging object replaced')
        for directory, fd in self.directories.items():
            expected = {basename for name, (parent, basename, _) in self.objects.items()
                        if name and parent == fd}
            if set(os.listdir(fd)) != expected:
                raise ValueError('staging namespace changed')

    def cleanup(self):
        # fchmod only held directory objects created by this stage. Names may
        # now be symlinks or foreign objects; those are always left untouched.
        for name, fd in self.directories.items():
            if inode(os.fstat(fd)) == self.objects[name][2]:
                os.fchmod(fd, 0o700)
        for name in sorted(self.objects, key=lambda p: (p.count('/'), len(p)), reverse=True):
            parent, basename, expected = self.objects[name]
            try:
                current = os.stat(basename, dir_fd=parent, follow_symlinks=False)
                if inode(current) != expected:
                    continue
                if stat.S_ISDIR(current.st_mode):
                    os.rmdir(basename, dir_fd=parent)
                else:
                    os.unlink(basename, dir_fd=parent)
            except FileNotFoundError:
                pass
            except OSError as error:
                # Preserve untracked children and their containing directory.
                if error.errno not in (39, 17):  # ENOTEMPTY, EEXIST
                    raise

    def close(self):
        for fd in self.directories.values():
            os.close(fd)


def build(output):
    output = validate_output(output)
    parent_fd = open_output_parent(output)
    stage = None
    published = False
    try:
        payload = entries()
        packed = archive(payload)
        metadata = json.loads(payload['package.json'][1])
        recipes = {name: record(payload[name][1]) for name in SOURCES}
        recipes['build/maintenance-tools.py'] = record(read_source(REPO / 'build/maintenance-tools.py'))
        manifest = {**metadata, 'classification': 'portable maintenance tools; no hardware acceptance',
                    'files': {name: {'mode': mode, **record(data)} for name, (mode, data) in sorted(payload.items())},
                    'artifacts': {'tools.tar.gz': record(packed)}, 'recipes': dict(sorted(recipes.items()))}
        verify_parent(output, parent_fd)
        stage = Stage(parent_fd)
        stage.directory('tree')
        for name, (mode, data) in sorted(payload.items()):
            stage.write('tree/' + name, data, mode)
        for name, fd in stage.directories.items():
            if name:
                os.fchmod(fd, 0o555)
        stage.write('tools.tar.gz', packed, 0o444)
        stage.write('manifest.json', canonical(manifest), 0o444)
        verify_parent(output, parent_fd)
        stage.verify()
        publish(parent_fd, stage.name, output.name)
        published = True
        return manifest
    finally:
        if stage is not None:
            try:
                if not published:
                    stage.cleanup()
            finally:
                stage.close()
        os.close(parent_fd)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('new_output_directory')
    args = parser.parse_args()
    try:
        result = build(args.new_output_directory)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        parser.exit(1, f'maintenance tools: {error}\n')
    print(json.dumps(result, indent=2))
