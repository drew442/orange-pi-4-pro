#!/usr/bin/env python3
"""Reviewed, opt-in privileged VFS/ARM probe of trusted packaged A/B roots.

Usage: sudo python3 tests/verify_package_payload_arm.py TRUSTED_PAYLOAD_DIRECTORY PINNED_BUSYBOX

Only canonical regular-file payload inputs are accepted; no device arguments.
/var/tmp must be on operator-known fixed builder storage with 5 GiB free.
No flashing, systemd boot, updater, networking or hardware acceptance occurs.
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

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('package_payload_loop_helpers',
                                            REPO / 'tests/verify_package_state_loop.py')
LOOP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LOOP)
PACKAGE = LOOP.PACKAGE
SELECT = LOOP.load_module('package_payload_selection', REPO / 'build/root-selection.py')
require = LOOP.require
LIMIT = 4 * 1024**2
SIZE = 4 * 1024**3
WORKER_ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'}


def identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def bounded(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and 0 < before.st_size <= limit,
                f'nonregular or oversized metadata: {path}')
        data = stream.read(limit + 1)
        require(len(data) == before.st_size and identity(before) == identity(os.fstat(stream.fileno())),
                f'metadata changed while reading: {path}')
        return data


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate JSON field')
        result[key] = value
    return result


def trusted_payload(value):
    path = Path(value)
    require(path.is_absolute() and path.resolve(strict=True) == path and
            not (path == Path('/dev') or Path('/dev') in path.parents),
            'trusted payload directory must be canonical, absolute and outside /dev')
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and not info.st_mode & 0o022,
            'trusted payload directory must be real and not writable by others')
    LOOP.regular(path / 'manifest.json')
    data = bounded(path / 'manifest.json', LIMIT)
    manifest = json.loads(data, object_pairs_hook=unique)
    require(manifest.get('schema') == 1 and manifest.get('slot_bytes') == SIZE and
            manifest.get('slot_uuids') == LOOP.SLOT_UUIDS, 'incompatible root payload manifest')
    PACKAGE.validate_profile(manifest.get('package_state'))
    for slot in ('A', 'B'):
        source = path / f'root-{slot}.ext4'
        info = LOOP.regular(source)
        expected = manifest['files'][source.name]
        require(set(expected) == {'bytes', 'sha256'} and expected['bytes'] == SIZE and
                isinstance(expected['sha256'], str) and re.fullmatch('[0-9a-f]{64}', expected['sha256'])
                and info.st_size == SIZE, 'missing full 4 GiB root image hash')
    return path, manifest, {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def real_parents(root, path):
    current = path.parent
    while current != root:
        require(current.is_relative_to(root) and stat.S_ISDIR(current.lstat().st_mode),
                f'unsafe payload parent: {current}')
        current = current.parent


def inventory(component):
    """Independent lstat/scandir reader; never follow component symlinks."""
    records = {}

    def visit(path, relative):
        info = path.lstat()
        entry = {'path': relative, 'mode': f'{stat.S_IMODE(info.st_mode):04o}',
                 'uid': info.st_uid, 'gid': info.st_gid}
        if stat.S_ISDIR(info.st_mode):
            entry['type'] = 'directory'
            records[relative] = entry
            with os.scandir(path) as children:
                names = sorted(child.name for child in children)
            for name in names:
                visit(path / name, name if relative == '.' else relative + '/' + name)
            return
        if stat.S_ISREG(info.st_mode):
            entry.update(type='file')
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            digest = hashlib.sha256()
            size = 0
            with os.fdopen(fd, 'rb') as stream:
                require(identity(info) == identity(os.fstat(stream.fileno())), 'BOM inode changed')
                for data in iter(lambda: stream.read(1024**2), b''):
                    digest.update(data)
                    size += len(data)
                require(identity(info) == identity(os.fstat(stream.fileno())), 'BOM file changed')
            entry.update(bytes=size, sha256=digest.hexdigest())
        elif stat.S_ISLNK(info.st_mode):
            entry.update(type='symlink', link_target=os.readlink(path))
        else:
            raise ValueError(f'unsupported BOM object: {path}')
        records[relative] = entry

    visit(component, '.')
    return records


def check_bom(newroot, profile):
    base = newroot / PACKAGE.ROOT
    real_parents(newroot, base / 'FORMAT')
    require(bounded(base / 'FORMAT', 128) == PACKAGE.FORMAT, 'package FORMAT differs')
    data = bounded(base / 'manifest.json', PACKAGE.MANIFEST_LIMIT)
    descriptor = PACKAGE.validate_manifest(data, profile)  # Authenticate bytes before JSON.
    counts = {}
    for name, component in descriptor['components'].items():
        actual = inventory(base / name)
        expected = {entry['path']: entry for entry in component['entries']}
        require(actual == expected, f'independent package BOM mismatch: {name}')
        counts[name] = len(actual)
    return descriptor, counts


def query_rows(output):
    rows = []
    for line in output.splitlines():
        fields = line.split('\t')
        require(len(fields) == 3, 'malformed ARM dpkg-query output')
        name, version, arch = fields
        if ':' in name:
            name, suffix = name.rsplit(':', 1)
            require(suffix == arch, 'binary:Package architecture suffix differs')
        rows.append((name, version, arch))
    require(len(rows) == len(set(rows)) == 220, 'ARM package inventory must contain 220 unique rows')
    return sorted(rows)


def check_mounts(newroot, loop, descriptor):
    device_id = os.stat(loop).st_rdev
    device = f'{os.major(device_id)}:{os.minor(device_id)}'
    root_rows = LOOP.mounted(newroot)
    require(len(root_rows) == 1 and root_rows[0]['device'] == device and root_rows[0]['root'] == '/'
            and {'ro', 'nodev', 'nosuid'} <= set(root_rows[0]['flags']), 'root mount differs')
    expected_targets = {str(newroot / 'var')}
    result = {}
    for name, (target, kind) in PACKAGE.COMPONENTS.items():
        path = newroot / target
        expected_targets.add(str(path))
        rows = LOOP.mounted(path)
        require(len(rows) == 1 and rows[0]['device'] == device and
                rows[0]['root'] == '/' + PACKAGE.ROOT + '/' + name and
                rows[0]['filesystem'] == 'ext4' and
                {'ro', 'nodev', 'nosuid'} <= set(rows[0]['flags']) and 'rw' not in rows[0]['flags'],
                f'wrong package bind origin/options: {name}')
        files = [entry['path'] for entry in descriptor['components'][name]['entries']
                 if entry['type'] == 'file']
        victim = path / files[0] if files and kind == 'directory' else path
        flags = os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW
        if kind == 'directory' and not files:
            victim = path / 'a733-readonly-probe'
            flags |= os.O_CREAT | os.O_EXCL
        try:
            fd = os.open(victim, flags, 0o600)
        except OSError as error:
            require(error.errno == errno.EROFS, f'wrong write-failure errno: {name}: {error}')
        else:
            os.close(fd)
            raise ValueError(f'root write permitted: {name}')
        result[name] = {'mount': rows[0], 'root_write_errno': errno.EROFS}
    actual = {row['target'] for row in LOOP.mount_table()
              if row['target'].startswith(str(newroot) + '/')}
    require(actual == expected_targets, 'unexpected nested mount under selected root')
    var_rows = LOOP.mounted(newroot / 'var')
    require(len(var_rows) == 1 and 'rw' in var_rows[0]['flags'], 'state var is not writable')
    return result


def worker(payload_value, busybox_value, workspace_value, parent_namespace):
    require(os.geteuid() == 0, 'root required')
    require(dict(os.environ) == WORKER_ENV, 'worker requires the fixed environment without database redirects')
    os.umask(0o022)
    require(os.readlink('/proc/self/ns/mnt') != parent_namespace, 'mount namespace was not isolated')
    require(all(not any(field.startswith(('shared:', 'master:')) for field in
                        line.split(' - ', 1)[0].split()[6:])
                for line in Path('/proc/self/mountinfo').read_text().splitlines()),
            'mount propagation is not private')
    workspace = Path(workspace_value)
    require(workspace.parent == Path('/var/tmp') and workspace.name.startswith('a733-package-payload-')
            and workspace.resolve(strict=True) == workspace, 'unsafe private workspace path')
    info = workspace.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o700,
            'workspace must be root-owned and private')
    payload, manifest, manifest_record = trusted_payload(payload_value)
    busybox = LOOP.busybox_input(busybox_value)
    chroot = workspace / 'chroot'
    (chroot / 'bin').mkdir(parents=True)
    (chroot / 'proc').mkdir()
    (chroot / 'newroot').mkdir()
    shutil.copyfile(busybox, chroot / 'bin/busybox')
    (chroot / 'bin/busybox').chmod(0o755)
    require(LOOP.record(chroot / 'bin/busybox')['sha256'] == LOOP.BUSYBOX_SHA256, 'BusyBox copy differs')
    shutil.copyfile(REPO / 'configs/host-init', chroot / 'host-init')
    init_record = LOOP.record(chroot / 'host-init')
    require(init_record == LOOP.record(REPO / 'configs/host-init'), 'host-init copy differs')
    LOOP.run('chroot', chroot, '/bin/busybox', 'true')
    statevar = workspace / 'state-var'
    statevar.mkdir()
    for name, (target, kind) in PACKAGE.COMPONENTS.items():
        path = statevar / Path(target).relative_to('var')
        if kind == 'directory':
            path.mkdir(parents=True, exist_ok=True)
            LOOP.make_file(path / 'covered-original', f'covered state {name}\n'.encode())
        else:
            LOOP.make_file(path, f'covered state {name}\n'.encode())
    (statevar / 'log').mkdir()
    original_state = LOOP.state_hashes(statevar)
    lock_path = REPO / 'configs/host-userspace-packages.lock.tsv'
    lock = sorted(tuple(line.split('\t')) for line in bounded(lock_path, LIMIT).decode().splitlines())
    require(len(lock) == len(set(lock)) == 220 and all(len(row) == 3 for row in lock), 'invalid package lock')
    newroot = chroot / 'newroot'
    targets = [newroot / target for target, _ in PACKAGE.COMPONENTS.values()]
    cases = {}
    first_descriptor = None
    try:
        LOOP.run('mount', '-t', 'proc', '-o', 'nosuid,nodev,noexec', 'proc', chroot / 'proc')
        for slot in ('A', 'B'):
            source = payload / f'root-{slot}.ext4'
            expected = manifest['files'][source.name]
            private = workspace / f'root-{slot}.ext4'
            loop = None
            with source.open('rb') as held_source:
                source_identity = identity(os.fstat(held_source.fileno()))
                SELECT.capture_root(source, expected, private)
                private.chmod(0o600)
                try:
                    loop = LOOP.run('losetup', '--find', '--show', '--read-only', private)
                    LOOP.owned_loop(loop, private, SIZE)
                    LOOP.run('mount', '-t', 'ext4', '-o', 'ro,noload,nodev,nosuid', loop, newroot)
                    marker_path = newroot / 'usr/lib/orangepi-hypervisor/payload.json'
                    real_parents(newroot, marker_path)
                    marker = json.loads(bounded(marker_path, LIMIT), object_pairs_hook=unique)
                    require(marker.get('package_state') == manifest['package_state'] and
                            marker.get('slot') == slot and marker.get('filesystem_uuid') == LOOP.SLOT_UUIDS[slot],
                            'root marker/profile differs from trusted manifest')
                    descriptor, counts = check_bom(newroot, manifest['package_state'])
                    if first_descriptor is None:
                        first_descriptor = descriptor
                    require(descriptor == first_descriptor, 'A/B complete package BOMs differ')
                    LOOP.run('mount', '--bind', statevar, newroot / 'var')
                    script = ('. /host-init\nHI_MOUNTS=\n'
                              'trap \'code=$?; if [ "$code" -ne 0 ]; then hi_cleanup; fi\' EXIT\n'
                              'hi_package_mounts || exit 1\n')
                    LOOP.run('chroot', chroot, '/bin/busybox', 'sh', '-c', script)
                    mount_report = check_mounts(newroot, loop, descriptor)
                    with (newroot / 'var/log/package-payload.log').open('ab') as log:
                        log.write(f'selected {slot}\n'.encode())
                        log.flush()
                        os.fsync(log.fileno())
                    executable = newroot / 'usr/bin/dpkg-query'
                    real_parents(newroot, executable)
                    fd = os.open(executable, os.O_RDONLY | os.O_NOFOLLOW)
                    with os.fdopen(fd, 'rb') as stream:
                        require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), 'dpkg-query must be regular')
                        elf = stream.read(64)
                    require(elf[:6] == b'\x7fELF\x02\x01' and struct.unpack_from('<H', elf, 18)[0] == 183,
                            'dpkg-query is not an ARM64 ELF')
                    output = LOOP.run('chroot', newroot, '/usr/bin/dpkg-query', '-W',
                                      '-f=${binary:Package}\t${Version}\t${Architecture}\n')
                    require(query_rows(output) == lock, 'actual ARM default database query differs from package lock')
                    machine = LOOP.run('chroot', newroot, '/usr/bin/python3', '-I', '-B', '-c',
                                       'import platform; print(platform.machine())')
                    require(machine == 'aarch64', 'packaged ARM Python machine differs')
                    cases[slot] = {'independent_bom_entries': counts, 'mounts': mount_report,
                                   'actual_arm_query': {'rows': 220, 'lock_exact_after_arch_suffix_normalization': True,
                                                        'output_sha256': hashlib.sha256(output.encode()).hexdigest()},
                                   'dpkg_query_elf_machine': 183, 'actual_arm_python_machine': machine,
                                   'state_log_writable': True}
                finally:
                    errors = []
                    for target in [*reversed(targets), newroot / 'var', newroot]:
                        try:
                            LOOP.unmount_owned(target)
                        except Exception as error:
                            errors.append(str(error))
                    if loop is not None:
                        try:
                            LOOP.owned_loop(loop, private, SIZE)
                            device = os.stat(loop).st_rdev
                            require(not any(row['device'] == f'{os.major(device)}:{os.minor(device)}'
                                            for row in LOOP.mount_table()), 'owned loop remains mounted')
                            LOOP.detach_owned(loop, private, SIZE)
                        except Exception as error:
                            errors.append(str(error))
                    require(not errors, 'explicit root/loop cleanup failed: ' + '; '.join(errors))
                require(LOOP.record(private) == expected, 'private root image changed')
                require(identity(os.fstat(held_source.fileno())) == source_identity and
                        identity(source.lstat()) == source_identity and LOOP.record(source) == expected,
                        'original root image changed')
            require(LOOP.state_hashes(statevar) == original_state, 'covered shared state changed')
            cases[slot].update(source_and_private_hash_unchanged=expected, explicit_unmount_and_detach=True)
            private.unlink()  # Sequential slots keep the fixed workspace below 5 GiB.
    finally:
        LOOP.unmount_owned(chroot / 'proc')
    require((statevar / 'log/package-payload.log').read_text() == 'selected A\nselected B\n', 'state log differs')
    return {'classification': 'native kernel VFS, actual ARM 220-package default database queries and independent BOM readback',
            'payload_manifest': manifest_record, 'cases': cases, 'busybox': LOOP.record(chroot / 'bin/busybox'),
            'sources': {str(path.relative_to(REPO)): LOOP.record(path) for path in
                        (Path(__file__), REPO / 'tests/verify_package_state_loop.py',
                         REPO / 'configs/package_state.py', REPO / 'build/root-selection.py', lock_path)},
            'production_host_init': init_record, 'covered_state_unchanged': True,
            'descriptor_policy': 'close_fds=True; stdin=/dev/null; stdout/stderr only; no host /dev, /proc or /sys binds',
            'worker_environment': WORKER_ENV,
            'cleanup': {'explicit_unmounts': True, 'all_owned_loops_detached': True},
            'limitations': ['No actual systemd boot, installed updates, power-cut or rollback test.',
                            'Emulation and file-backed loops do not establish board boot, KVM or hardware acceptance.']}


def main():
    require(len(sys.argv) == 3, 'Only TRUSTED_PAYLOAD_DIRECTORY PINNED_BUSYBOX accepted; no device arguments')
    require(os.geteuid() == 0, 'root required')
    os.umask(0o022)
    payload, _, _ = trusted_payload(sys.argv[1])
    busybox = LOOP.busybox_input(sys.argv[2])
    storage = LOOP.STATE.validate_workspace_storage('/var/tmp')
    require(shutil.disk_usage('/var/tmp').free >= SIZE + 1024**3, '5 GiB fixed storage free required')
    for command in ('unshare', 'losetup', 'mount', 'umount', 'chroot'):
        require(shutil.which(command) is not None, f'missing builder command: {command}')
    LOOP.run('unshare', '--mount', '--propagation', 'private', 'true')
    logdir = REPO / 'tmp'
    logdir.mkdir(exist_ok=True)
    require(logdir.resolve() == logdir and stat.S_ISDIR(logdir.lstat().st_mode), 'unsafe log directory')
    logfd, logname = tempfile.mkstemp(prefix='package-payload-arm-', suffix='.log', dir=logdir)
    workspace = Path(tempfile.mkdtemp(prefix='a733-package-payload-', dir='/var/tmp'))
    # Worker dispatch is fixed code and explicit arguments; ambient environment
    # variables cannot select an alternate worker or workspace.
    code = ('import importlib.util,json,sys; '
            's=importlib.util.spec_from_file_location("payload_probe_worker",sys.argv[1]); '
            'm=importlib.util.module_from_spec(s); s.loader.exec_module(m); '
            'print(json.dumps(m.worker(*sys.argv[2:])))')
    with os.fdopen(logfd, 'w') as log:
        result = subprocess.run(['unshare', '--mount', '--propagation', 'private', sys.executable,
                                 '-I', '-B', '-c', code, str(Path(__file__).resolve()), str(payload), str(busybox),
                                 str(workspace), os.readlink('/proc/self/ns/mnt')],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=log,
                                text=True, close_fds=True, env=WORKER_ENV)
    require(result.returncode == 0,
            f'ARM payload probe failed (exit {result.returncode}); raw log {logname}; retained {workspace}')
    report = json.loads(result.stdout)
    require(all(report['cleanup'].values()), f'cleanup incomplete; retained {workspace}')
    shutil.rmtree(workspace)
    report.update(storage=storage, raw_log=logname, private_files_deleted=True)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
