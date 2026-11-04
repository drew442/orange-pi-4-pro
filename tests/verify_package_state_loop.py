#!/usr/bin/env python3
"""Opt-in root-only package-state VFS probe using new private loop images.

Usage: sudo python3 tests/verify_package_state_loop.py PINNED_BUSYBOX

The sole argument is a trusted, hash-pinned static ARM BusyBox regular file.
No device, payload, output or mountpoint arguments are accepted. Requires an
ARM64 host or working binfmt_misc ARM support, including inside a chroot.
The operator must know /var/tmp is on fixed builder storage; automatic storage
classification cannot identify every USB SD reader.
Run only after independent review. This establishes no hardware acceptance.
"""
import errno
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
import uuid

REPO = Path(__file__).resolve().parents[1]
BUSYBOX_SHA256 = '0a41864f87a0737804f0f4a68a1cfa1ed22374f4f75a1be3eb376e2659ce1316'
SLOT_SIZES = {'A': 128 * 1024**2, 'B': 64 * 1024**2}
SLOT_UUIDS = {'A': 'a7330000-0000-4000-8000-00000000000a',
              'B': 'a7330000-0000-4000-8000-00000000000b'}


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PACKAGE = load_module('package_loop_format', REPO / 'configs/package_state.py')
STATE = load_module('package_loop_storage', REPO / 'build/host-state.py')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def run(*args):
    result = subprocess.run([str(arg) for arg in args], check=False,
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, close_fds=True)
    # Commands and their diagnostics are kept in the caller's raw log file.
    print(json.dumps({'command': [str(arg) for arg in args],
                      'stdout': result.stdout, 'stderr': result.stderr,
                      'returncode': result.returncode}), file=sys.stderr)
    result.check_returncode()
    return result.stdout.strip()


def version(*args):
    result = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True,
                            text=True, close_fds=True, check=True)
    return (result.stdout + result.stderr).strip()


def record(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for data in iter(lambda: stream.read(4 * 1024**2), b''):
            digest.update(data)
    return {'bytes': path.stat().st_size, 'sha256': digest.hexdigest()}


def regular(path):
    require(path.is_absolute() and path.resolve(strict=True) == path,
            'regular input must have no symlink ancestors')
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode), 'regular-file input required')
    require(not info.st_mode & 0o022, 'writable-by-other input refused')
    return info


def busybox_input(value):
    path = Path(value).absolute()
    require(not (path == Path('/dev') or Path('/dev') in path.parents),
            'device paths are never accepted')
    regular(path)
    require(record(path)['sha256'] == BUSYBOX_SHA256, 'pinned BusyBox hash differs')
    return path


def make_file(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)


def seed(root, version):
    root.mkdir(mode=0o755)
    (root / 'usr/lib/orangepi-hypervisor').mkdir(parents=True)
    for name, (target, kind) in PACKAGE.COMPONENTS.items():
        path = root / target
        if kind == 'directory':
            path.mkdir(parents=True, exist_ok=True)
            make_file(path / 'fixture', f'{name} version {version}\n'.encode())
        else:
            make_file(path, f'{name} version {version}\n'.encode())
    make_file(root / 'var/lib/dpkg/status', (
        'Package: a733-fixture\nStatus: install ok installed\nPriority: optional\n'
        'Section: misc\nInstalled-Size: 1\nMaintainer: Fixture <fixture@example.invalid>\n'
        f'Architecture: arm64\nVersion: {version}\nDescription: loop test fixture\n\n').encode())
    make_file(root / 'var/lib/dpkg/alternatives/a733-fixture', (
        f'auto\n/usr/bin/a733-fixture\n\n/usr/share/a733-fixture/value-{version}\n10\n').encode())
    make_file(root / f'usr/share/a733-fixture/value-{version}', f'slot-version-{version}\n'.encode())
    (root / 'etc/alternatives').mkdir(parents=True)
    (root / 'etc/alternatives/a733-fixture').symlink_to(f'/usr/share/a733-fixture/value-{version}')
    (root / 'usr/bin').mkdir(parents=True)
    (root / 'usr/bin/a733-fixture').symlink_to('/etc/alternatives/a733-fixture')
    return PACKAGE.snapshot(root)


def owned_loop(loop, backing, size):
    require(re.fullmatch(r'/dev/loop[0-9]+', loop) is not None, 'unexpected loop device')
    attribute = Path('/sys/class/block') / Path(loop).name / 'loop/backing_file'
    actual = Path('/' + attribute.read_text().strip().lstrip('/')).resolve(strict=True)
    require(actual == backing, 'loop backing-file path differs')
    info = backing.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
            and info.st_size == size and stat.S_IMODE(info.st_mode) == 0o600,
            'loop backing-file ownership/type/size/mode differs')
    require((attribute.parent.parent / 'ro').read_text().strip() == '1',
            'loop device is not read-only')


def mount_table():
    rows = []
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        before, after = line.split(' - ', 1)
        fields = before.split()
        tail = after.split()
        rows.append({'id': fields[0], 'device': fields[2], 'root': fields[3],
                     'target': fields[4], 'flags': fields[5].split(','),
                     'filesystem': tail[0], 'super_flags': tail[2].split(',')})
    return rows


def mounted(path):
    return [row for row in mount_table() if row['target'] == str(path)]


def unmount_owned(path):
    rows = mounted(path)
    require(len(rows) <= 1, f'unexpected stacked mounts at {path}')
    if rows:
        run('umount', path)
    require(not mounted(path), f'mount survived explicit cleanup: {path}')


def detach_owned(loop, backing, size):
    owned_loop(loop, backing, size)
    run('losetup', '--detach', loop)
    attribute = Path('/sys/class/block') / Path(loop).name / 'loop/backing_file'
    for _ in range(50):
        if not attribute.exists():
            return
        # A reused loop device is not ours and must never be detached again.
        reported = Path('/' + attribute.read_text().strip().lstrip('/'))
        require(reported == backing, 'loop was reused before detach confirmation')
        time.sleep(0.1)
    raise ValueError('owned loop detach did not complete')


def state_hashes(statevar):
    return {str(path.relative_to(statevar)): record(path)
            for path in sorted(statevar.rglob('*')) if path.is_file() and
            not path.is_symlink() and not path.is_relative_to(statevar / 'log')}


def inspect_view(newroot, loop, version):
    major_minor = os.stat(loop).st_rdev
    device = f'{os.major(major_minor)}:{os.minor(major_minor)}'
    rows = mounted(newroot)
    require(len(rows) == 1 and rows[0]['device'] == device and
            rows[0]['root'] == '/' and 'ro' in rows[0]['flags'], 'selected root mount differs')
    bindings = {}
    write_errors = {}
    expected = {str(newroot / 'var')}
    for name, (target, kind) in PACKAGE.COMPONENTS.items():
        path = newroot / target
        expected.add(str(path))
        rows = mounted(path)
        require(len(rows) == 1, f'missing or stacked package mount: {name}')
        row = rows[0]
        require(row['device'] == device and row['root'] == '/' + PACKAGE.ROOT + '/' + name
                and row['filesystem'] == 'ext4' and
                {'ro', 'nodev', 'nosuid'} <= set(row['flags']) and 'rw' not in row['flags'],
                f'package mount origin/options differ: {name}')
        bindings[name] = row
        victim = path / 'fixture' if kind == 'directory' else path
        try:
            fd = os.open(victim, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
        except OSError as error:
            require(error.errno == errno.EROFS, f'write denied for wrong reason: {name}: {error}')
            write_errors[name] = {'errno': error.errno, 'name': errno.errorcode[error.errno]}
        else:
            os.close(fd)
            raise ValueError(f'root write unexpectedly allowed: {name}')
    actual = {row['target'] for row in mount_table() if row['target'].startswith(str(newroot) + '/')}
    require(actual == expected, 'unexpected or missing nested root mounts')
    var = mounted(newroot / 'var')
    require(len(var) == 1 and 'rw' in var[0]['flags'], 'state /var is not writable')
    with (newroot / 'var/log/probe.log').open('ab') as stream:
        stream.write(f'selected version {version}\n'.encode())
        stream.flush()
        os.fsync(stream.fileno())
    query = run('dpkg-query', '--admindir=' + str(newroot / 'var/lib/dpkg'),
                '--show', '--showformat=${Package} ${Version} ${Architecture} ${Status}\n', 'a733-fixture')
    require(query == f'a733-fixture {version} arm64 install ok installed', 'wrong selected package view')
    alternatives = newroot / 'etc/alternatives/a733-fixture'
    target = os.readlink(alternatives)
    require(target == f'/usr/share/a733-fixture/value-{version}', 'alternatives link differs')
    require(os.readlink(newroot / 'usr/bin/a733-fixture') == '/etc/alternatives/a733-fixture',
            'alternatives entry link differs')
    contents = (newroot / target.lstrip('/')).read_text()
    require(contents == f'slot-version-{version}\n', 'alternatives target differs')
    database = (newroot / 'var/lib/dpkg/alternatives/a733-fixture').read_text()
    require(target + '\n10\n' in database, 'alternatives database/link mismatch')
    return {'native_dpkg_query_synthetic_database': query, 'package_mounts': bindings,
            'state_var_mount': var[0], 'root_write_failures': write_errors,
            'alternatives': {'target': target, 'contents': contents, 'database': database},
            'state_log_writable': True}


def worker(busybox, workspace):
    require(os.readlink('/proc/self/ns/mnt') != os.environ['A733_PACKAGE_PARENT_NS'],
            'private mount namespace was not created')
    require(workspace.parent == Path('/var/tmp') and workspace.name.startswith('a733-package-loop-')
            and workspace.resolve(strict=True) == workspace, 'invalid private workspace')
    info = workspace.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and
            stat.S_IMODE(info.st_mode) == 0o700, 'unsafe private workspace')
    # Inspect propagation fields directly; mount_table intentionally records
    # just the fields used to validate package database bind origins.
    require(all(not any(field.startswith(('shared:', 'master:')) for field in
                        line.split(' - ', 1)[0].split()[6:])
                for line in Path('/proc/self/mountinfo').read_text().splitlines()),
            'mount namespace propagation is not private')
    chroot = workspace / 'chroot'
    (chroot / 'bin').mkdir(parents=True)
    (chroot / 'proc').mkdir()
    (chroot / 'newroot').mkdir()
    shutil.copyfile(busybox, chroot / 'bin/busybox')
    (chroot / 'bin/busybox').chmod(0o755)
    require(record(chroot / 'bin/busybox')['sha256'] == BUSYBOX_SHA256, 'BusyBox copy differs')
    shutil.copyfile(REPO / 'configs/host-init', chroot / 'host-init')
    require(record(chroot / 'host-init') == record(REPO / 'configs/host-init'), 'host-init copy differs')
    run('chroot', chroot, '/bin/busybox', 'true')
    statevar = workspace / 'state-var'
    statevar.mkdir()
    # Deliberately retain different shared databases; binds must only cover them.
    for name, (target, kind) in PACKAGE.COMPONENTS.items():
        path = statevar / Path(target).relative_to('var')
        if kind == 'directory':
            path.mkdir(parents=True, exist_ok=True)
            make_file(path / 'covered-original', f'state database {name}\n'.encode())
        else:
            make_file(path, f'state database {name}\n'.encode())
    make_file(statevar / 'lib/dpkg/status', b'covered shared status: never authoritative\n')
    (statevar / 'log').mkdir()
    original_state = state_hashes(statevar)
    loops = []
    images = {}
    profiles = {}
    cases = []
    newroot = chroot / 'newroot'
    targets = [newroot / path for path, _ in PACKAGE.COMPONENTS.values()]
    cleanup_errors = []
    try:
        for slot, size in SLOT_SIZES.items():
            tree = workspace / ('seed-' + slot)
            profiles[slot] = seed(tree, '1' if slot == 'A' else '2')
            backing = workspace / ('root-' + slot + '.ext4')
            fd = os.open(backing, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.truncate(size)
            run('mkfs.ext4', '-q', '-F', '-U', SLOT_UUIDS[slot], '-O', '^has_journal',
                '-E', 'lazy_itable_init=0', '-d', tree, backing)
            run('tune2fs', '-O', 'read-only', backing)
            with backing.open('rb') as stream:
                stream.seek(1024)
                superblock = stream.read(1024)
            require(struct.unpack_from('<H', superblock, 56)[0] == 0xef53 and
                    not struct.unpack_from('<I', superblock, 92)[0] & 4 and
                    struct.unpack_from('<I', superblock, 100)[0] & 0x1000 and
                    str(uuid.UUID(bytes=superblock[104:120])) == SLOT_UUIDS[slot],
                    'ext4 journal/read-only feature/UUID differs')
            images[slot] = {'path': backing, 'before': record(backing), 'size': size}
            loop = run('losetup', '--find', '--show', '--read-only', backing)
            # Register immediately, before any validation that could raise.
            loops.append((loop, backing, size))
            owned_loop(loop, backing, size)
            images[slot]['loop'] = loop
        run('mount', '-t', 'proc', '-o', 'nosuid,nodev,noexec', 'proc', chroot / 'proc')
        for slot in ('A', 'B', 'A'):
            image = images[slot]
            owned_loop(image['loop'], image['path'], image['size'])
            run('mount', '-t', 'ext4', '-o', 'ro,noload,nodev,nosuid', image['loop'], newroot)
            try:
                run('mount', '--bind', statevar, newroot / 'var')
                # Sourcing is production's supported definitions-only interface.
                # No mount or read-only callbacks are replaced by the fixture.
                script = ('. /host-init\nHI_MOUNTS=\n'
                          'trap \'code=$?; if [ "$code" -ne 0 ]; then hi_cleanup; fi\' EXIT\n'
                          'hi_package_mounts || exit 1\n')
                run('chroot', chroot, '/bin/busybox', 'sh', '-c', script)
                cases.append({'slot': slot, **inspect_view(newroot, image['loop'],
                                                         '1' if slot == 'A' else '2')})
            finally:
                for target in reversed(targets):
                    unmount_owned(target)
                unmount_owned(newroot / 'var')
                unmount_owned(newroot)
            require(state_hashes(statevar) == original_state, 'covered shared state database changed')
        require((statevar / 'log/probe.log').read_text() ==
                'selected version 1\nselected version 2\nselected version 1\n', 'state logs differ')
    finally:
        # Try every explicit owned cleanup even when a previous one fails.
        # Keep backing files on failure for inspection; namespace exit is extra
        # containment and is never counted as successful unmount/detach evidence.
        for target in [*reversed(targets), newroot / 'var', newroot, chroot / 'proc']:
            try:
                unmount_owned(target)
            except Exception as error:
                cleanup_errors.append(str(error))
        for loop, backing, size in reversed(loops):
            try:
                require(not any(row['device'] ==
                                f'{os.major(os.stat(loop).st_rdev)}:{os.minor(os.stat(loop).st_rdev)}'
                                for row in mount_table()), 'owned loop remains mounted')
                detach_owned(loop, backing, size)
            except Exception as error:
                cleanup_errors.append(str(error))
        require(not cleanup_errors, 'explicit cleanup failed: ' + '; '.join(cleanup_errors))
    for image in images.values():
        require(record(image['path']) == image['before'], 'read-only source image changed')
    return {'classification': 'private native-kernel VFS and ARM BusyBox production-function checks only',
            'sources': {str(path.relative_to(REPO)): record(path) for path in
                        (Path(__file__), REPO / 'configs/host-init', REPO / 'configs/package_state.py',
                         REPO / 'build/host-state.py')},
            'busybox': record(chroot / 'bin/busybox'),
            'tools': {'native_dpkg_query': run('dpkg-query', '--version').splitlines()[0],
                      'mkfs_ext4': version('mkfs.ext4', '-V'),
                      'busybox_arm': version('chroot', str(chroot), '/bin/busybox', '--help').splitlines()[0]},
            'descriptor_policy': 'close_fds=True; stdin=/dev/null; only stdout/stderr retained; no /dev, /sys, /proc bind mounts; new procfs only',
            'fixtures': {slot: {'image': image['before'], 'uuid': SLOT_UUIDS[slot],
                                'package_profile': profiles[slot], 'source_unchanged': True,
                                'ext4_no_journal': True, 'ext4_readonly_feature': True}
                         for slot, image in images.items()},
            'cases': cases, 'covered_state_original_hashes': original_state,
            'covered_state_unchanged': True,
            'cleanup': {'explicit_unmounts': True, 'all_owned_loops_detached': True},
            'limitations': ['Native dpkg-query reads synthetic ARM64 package databases; packaged ARM dpkg-query is not tested.',
                            'Tiny fixtures do not authenticate or inspect the 220-package production root payload.',
                            'No physical boot, KVM guest, updater, power-cut or rollback acceptance.']}


def main():
    require(len(sys.argv) == 2, 'Only PINNED_BUSYBOX regular-file argument accepted; no device arguments')
    require(os.geteuid() == 0, 'root required for private file-backed loop tests')
    os.umask(0o022)
    busybox = busybox_input(sys.argv[1])
    if 'A733_PACKAGE_LOOP_WORKSPACE' in os.environ:
        print(json.dumps(worker(busybox, Path(os.environ['A733_PACKAGE_LOOP_WORKSPACE'])), indent=2))
        return
    storage = STATE.validate_workspace_storage('/var/tmp')
    require(shutil.disk_usage('/var/tmp').free >= 1024**3, '1 GiB fixed storage free required')
    for command in ('unshare', 'losetup', 'mount', 'umount', 'mkfs.ext4', 'tune2fs', 'chroot', 'dpkg-query'):
        require(shutil.which(command) is not None, f'required builder tool missing: {command}')
    # Fail before allocating loops if mount namespaces are unavailable.
    run('unshare', '--mount', '--propagation', 'private', 'true')
    logdir = REPO / 'tmp'
    logdir.mkdir(exist_ok=True)
    require(logdir.resolve() == logdir and stat.S_ISDIR(logdir.lstat().st_mode), 'unsafe raw-log directory')
    logfd, logname = tempfile.mkstemp(prefix='package-state-loop-', suffix='.log', dir=logdir)
    workspace = Path(tempfile.mkdtemp(prefix='a733-package-loop-', dir='/var/tmp'))
    environment = dict(os.environ, A733_PACKAGE_LOOP_WORKSPACE=str(workspace),
                       A733_PACKAGE_PARENT_NS=os.readlink('/proc/self/ns/mnt'))
    with os.fdopen(logfd, 'w') as log:
        result = subprocess.run(['unshare', '--mount', '--propagation', 'private', sys.executable,
                                 str(Path(__file__).resolve()), str(busybox)],
                                env=environment, stdout=subprocess.PIPE, stderr=log, text=True,
                                stdin=subprocess.DEVNULL, close_fds=True)
    require(result.returncode == 0,
            f'private VFS probe failed (exit {result.returncode}); raw log {logname}; retained {workspace}')
    report = json.loads(result.stdout)
    require(report['cleanup']['all_owned_loops_detached'] and report['cleanup']['explicit_unmounts'],
            f'cleanup incomplete; retained {workspace}')
    shutil.rmtree(workspace)
    report.update(storage=storage, raw_log=logname, private_files_deleted=True)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
