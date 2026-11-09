#!/usr/bin/env python3
"""Opt-in real ext4 journal-export check, using only newly created 64 MiB loops.

Run as root, with no arguments, on the prepared builder after independent review.
PART, PART_SIZE and inspect_target are adapted to each owned loop. Production
protected_journal and its real block-RO and filesystem helpers run unchanged.
Fixed A733/RAM-entry and physical-device/global-idle policy is bypassed.
This validates export, not physical A733 recovery, SD/NVMe identity, boot,
or hardware acceptance.
JSON evidence goes to stdout; commands and retention diagnostics go to stderr.
"""
import argparse
from contextlib import ExitStack
import ctypes
import hashlib
import importlib.util
import io
import json
import os
import re
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / 'scripts/export-known-host-journal.py'
REVIEWED_SOURCE_SHA256 = '3a54ca3536f0df7599f6a8722d14f465cbe9cfb774865623d9a5e622ad7bbb1c'
STATE_UUID = '360dfb39-f49f-487e-b307-0bb887c854c5'
SIZE = 64 * 1024**2
MAX_SPACE = 256 * 1024**2
RAM_RESERVE = 1_000_000_000
DISK_RESERVE = 3_000_000_000
JOURNAL = 'var/log/journal/' + '1234567890abcdef' * 2 + '/system.journal'
PAYLOAD = b'Synthetic journal-export fixture; not a systemd journal.\n\x00\xff\n'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def run(command, **kwargs):
    print('+ ' + ' '.join(map(str, command)), file=sys.stderr, flush=True)
    return subprocess.run(command, check=True, stdout=sys.stderr, stderr=sys.stderr,
                          timeout=60, **kwargs)


def identity(info):
    return info.st_dev, info.st_ino


def resources():
    values = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
    memory = int(values['MemAvailable'].split()[0]) * 1024
    free = shutil.disk_usage('/var/tmp').free
    require(memory >= RAM_RESERVE, '1 GB available RAM reserve required')
    require(free >= DISK_RESERVE + MAX_SPACE, '3 GB disk reserve plus 256 MiB fixture budget required')
    return {'disk_free_bytes': free, 'memory_available_bytes': memory}


def private_namespace():
    library = ctypes.CDLL(None, use_errno=True)
    library.unshare.argtypes = [ctypes.c_int]
    library.unshare.restype = ctypes.c_int
    before = os.readlink('/proc/self/ns/mnt')
    if library.unshare(0x00020000) != 0:  # CLONE_NEWNS; no user namespace.
        raise OSError(ctypes.get_errno(), 'private mount namespace unavailable')
    run(['mount', '--make-rprivate', '/'])
    require(os.readlink('/proc/self/ns/mnt') != before, 'mount namespace did not change')
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        optional = line.split(' - ', 1)[0].split()[6:]
        require(not any(x.startswith(('shared:', 'master:', 'propagate_from:')) for x in optional),
                'mount propagation is not private')


class Fixture:
    """Hold each created regular file and verify loop identity before device use."""
    def __init__(self, workspace, name):
        self.workspace = workspace
        self.workspace_id = identity(workspace.lstat())
        self.directory = workspace / name
        self.directory.mkdir(mode=0o700)
        self.directory_id = identity(self.directory.lstat())
        self.image = self.directory / 'owned.img'
        self.fd = os.open(self.image, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.ftruncate(self.fd, SIZE)
        self.image_id = identity(os.fstat(self.fd))
        self.loop = None
        self.loop_device = None
        self.allocation_may_exist = False
        self.export_assertions_passed = False
        self.point = self.directory / 'mount'
        self.point_id = None

    def check(self):
        for path, expected in ((self.workspace, self.workspace_id),
                               (self.directory, self.directory_id)):
            info = path.lstat()
            require(identity(info) == expected and stat.S_ISDIR(info.st_mode)
                    and info.st_uid == info.st_gid == 0 and stat.S_IMODE(info.st_mode) == 0o700,
                    'private fixture directory changed')
        info = self.image.lstat()
        require(identity(info) == self.image_id == identity(os.fstat(self.fd))
                and stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                and info.st_uid == info.st_gid == 0 and stat.S_IMODE(info.st_mode) == 0o600
                and info.st_size == SIZE, 'held regular image changed')
        if self.loop is None:
            return None
        require(self.loop.parent == Path('/dev') and self.loop.name.startswith('loop')
                and self.loop.name[4:].isdigit(), 'unexpected newly allocated loop path')
        node = self.loop.lstat()
        require(stat.S_ISBLK(node.st_mode) and node.st_uid == 0, 'loop node differs')
        device = f'{os.major(node.st_rdev)}:{os.minor(node.st_rdev)}'
        require(self.loop_device in (None, device), 'loop device reassigned')
        base = Path('/sys/dev/block') / device
        require(base.resolve(strict=True).name == self.loop.name, 'sysfs loop identity differs')
        require((base / 'dev').read_text().strip() == device, 'loop sysfs number differs')
        backing = Path((base / 'loop/backing_file').read_text().strip())
        require(backing == self.image and identity(backing.stat()) == self.image_id,
                'loop backing file differs')
        require(int((base / 'loop/offset').read_text()) == 0
                and int((base / 'loop/sizelimit').read_text()) == 0
                and int((base / 'size').read_text()) * 512 == SIZE
                and not list((base / 'holders').iterdir()), 'loop geometry or holders differ')
        self.loop_device = device
        return base

    def prepare(self, unexpected, legacy_orphan=False):
        seed = self.directory / 'seed'
        seed.mkdir(mode=0o2755)
        current = seed
        for name in Path(JOURNAL).parts[:-1]:
            current /= name
            current.mkdir(mode=0o2755)
        (seed / JOURNAL).write_bytes(PAYLOAD)
        (seed / JOURNAL).chmod(0o640)
        if unexpected:
            (current / 'unexpected.txt').write_bytes(b'refuse this file\n')
            (current / 'unexpected.txt').chmod(0o640)
        # Explicit chmod also defeats inherited umask and setgid propagation.
        for directory, children, files in os.walk(seed):
            os.chmod(directory, 0o2755)
            require(os.stat(directory).st_uid == os.stat(directory).st_gid == 0,
                    'seed directory owner differs')
            for name in files:
                os.chmod(Path(directory) / name, 0o640)
                require(os.stat(Path(directory) / name).st_uid ==
                        os.stat(Path(directory) / name).st_gid == 0, 'seed file owner differs')
        self.check()
        # mke2fs receives the held regular image FD, never a caller-supplied path/device.
        run(['mke2fs', '-q', '-F', '-t', 'ext4', '-b', '4096', '-m', '0',
             '-U', STATE_UUID, '-L', 'a733-state', '-O', 'has_journal',
             '-E', 'lazy_itable_init=0,lazy_journal_init=0,nodiscard',
             '-d', str(seed), '/proc/self/fd/' + str(self.fd)], pass_fds=(self.fd,))
        self.check()
        os.fsync(self.fd)
        sb = os.pread(self.fd, 1024, 1024)
        require(sb[56:58] == b'\x53\xef' and int.from_bytes(sb[92:96], 'little') & 4
                and sb[208:224] == bytes(16) and int.from_bytes(sb[224:228], 'little') == 8
                and int.from_bytes(sb[228:232], 'little') == 0, 'internal journal inode 8 required')
        self.legacy_orphan_inode = 0
        if legacy_orphan:
            # Synthetic legacy s_last_orphan only: this is not a real crash or
            # dirty-journal fixture. debugfs can write only our held regular file,
            # before any block device is attached. Use the known seed file inode.
            self.check()
            require(self.loop is None and not self.allocation_may_exist,
                    'legacy orphan preparation requires an unattached owned image')
            target = '/proc/self/fd/' + str(self.fd)
            command = ['debugfs', '-R', 'stat /' + JOURNAL, target]
            print('+ ' + ' '.join(command), file=sys.stderr, flush=True)
            listing = subprocess.check_output(command, text=True, timeout=30,
                                              pass_fds=(self.fd,))
            match = re.search(r'\bInode:\s+([0-9]+)', listing)
            require(match is not None, 'seed journal inode not found')
            self.legacy_orphan_inode = int(match.group(1))
            require(11 <= self.legacy_orphan_inode <= int.from_bytes(sb[:4], 'little'),
                    'seed journal inode outside fixture inode table')
            self.check()
            run(['debugfs', '-w', '-R', 'set_super_value last_orphan ' +
                 str(self.legacy_orphan_inode), target], pass_fds=(self.fd,))
            self.check()
            os.fsync(self.fd)
        sb = os.pread(self.fd, 1024, 1024)
        require(int.from_bytes(sb[232:236], 'little') == self.legacy_orphan_inode,
                'synthetic legacy orphan superblock differs')
        self.allocate_loop()
        # Loop attachment emits uevents; drain host probes before baseline sampling.
        run(['udevadm', 'settle', '--timeout=15'])
        self.check()
        self.point.mkdir(mode=0o700)
        self.point_id = identity(self.point.lstat())

    def allocate_loop(self):
        self.check()
        command = ['losetup', '--find', '--show', str(self.image)]
        print('+ ' + ' '.join(command), file=sys.stderr, flush=True)
        # Allocation may succeed before stdout capture, decoding, or wait fails.
        # Keep this uncertainty until a positively identified loop is detached.
        self.allocation_may_exist = True
        value = subprocess.check_output(command, text=True, timeout=30).strip()
        self.loop = Path(value)
        self.check()

    def fingerprint(self):
        base = self.check()
        require(base is not None, 'owned loop required')
        digest = hashlib.sha256()
        for offset in range(0, SIZE, 1024**2):
            data = os.pread(self.fd, min(1024**2, SIZE - offset), offset)
            require(len(data) == min(1024**2, SIZE - offset), 'short image read')
            digest.update(data)
        fields = (base / 'stat').read_text().split()
        require(len(fields) >= 17 and all(x.isdecimal() for x in fields),
                'Linux write/discard/flush counters unavailable')
        counters = [int(x) for x in fields[4:8] + fields[11:17]]
        return {'sha256': digest.hexdigest(), 'write_discard_flush_counters': counters}

    def cleanup(self, control):
        require(not self.allocation_may_exist or
                (self.loop is not None and self.loop_device is not None),
                'uncertain loop allocation; retain image/workspace; inspect losetup backing-file inventory before any manual cleanup')
        self.check()
        if self.loop is not None:
            rows = [e for e in control.mounts() if e['dev'] == self.loop_device
                    or e['path'] == str(self.point)]
            # mounted_journal owns its ordinary unmount. A remaining mount means
            # it failed: do not retry, detach, delete paths, or hide that failure.
            require(not rows, 'mount remains after production cleanup; retain fixture and loop')
            self.check()
            if self.export_assertions_passed:
                # The RO1 preservation assertion and all byte/counter checks
                # have finished. Do not leave our temporary kernel flag set on
                # a loop minor that the next fixture or another caller may reuse.
                normalize_fixture_ro(control, self)
                self.check()
            run(['losetup', '--detach', str(self.loop)])
            backing = Path('/sys/class/block') / self.loop.name / 'loop/backing_file'
            for _ in range(50):
                if not backing.exists():
                    break
                time.sleep(0.1)
            else:
                raise ValueError('loop detach incomplete; retain fixture')
            self.loop = None
            self.allocation_may_exist = False
        if self.point.exists():
            require(identity(self.point.lstat()) == self.point_id
                    and not list(self.point.iterdir()), 'mountpoint replaced or populated')
            self.point.rmdir()
        self.check()
        os.close(self.fd)
        self.fd = -1
        # No recursive deletion until successful unmount and detach.
        shutil.rmtree(self.directory / 'seed')
        self.image.unlink()
        self.directory.rmdir()



def allocation_retention_probe():
    """Nonprivileged mock-only regression; importing/calling this opens no devices.

    This callable deliberately has no command-line switch. The opt-in executable
    interface remains root-only and accepts no device or other arguments.
    """
    failures = [subprocess.TimeoutExpired(['losetup'], 30),
                subprocess.CalledProcessError(1, ['losetup']),
                UnicodeDecodeError('utf-8', b'\xff', 0, 1, 'synthetic decode failure')]
    for error in failures:
        fixture = object.__new__(Fixture)
        fixture.image = Path('/nonexistent/mock-only-owned.img')
        fixture.loop = fixture.loop_device = None
        fixture.allocation_may_exist = False
        fixture.check = mock.Mock()
        control = mock.Mock()
        with mock.patch.object(subprocess, 'check_output', side_effect=error):
            try:
                fixture.allocate_loop()
            except type(error):
                pass
            else:
                raise AssertionError('mock allocation failure did not propagate')
        require(fixture.allocation_may_exist, 'allocation uncertainty was lost')
        try:
            fixture.cleanup(control)
        except ValueError as refusal:
            require(str(refusal).startswith('uncertain loop allocation;'),
                    'cleanup failed for an unexpected reason')
        else:
            raise AssertionError('uncertain allocation cleanup was allowed')
        fixture.check.assert_called_once_with()  # No cleanup filesystem check/mutation.
        require(not control.mock_calls, 'uncertain cleanup inspected or changed mounts')
    return {'mock_allocation_failures_retained': len(failures), 'device_operations': 0}


def fixture_inspector(control, fixture, expected_state_ro=None, held_fd=None):
    """Replace only physical target/global-idle policy, never production FD checks.

    The private fixture identity, backing file, geometry and holders are checked
    by Fixture.check(). This adapter checks this namespace's loop mounts; the
    production whole-host process/swap/namespace idle scan is deliberately not
    exercised. The production held FD is validated directly, rather than treated
    as an external user. No physical target or entry-guard acceptance is implied.
    """
    fixture.check()
    require(not any(e['dev'] == fixture.loop_device or e['path'] == str(fixture.point)
                    for e in control.mounts()), 'owned fixture remains mounted')
    if held_fd is None:
        fd = os.open(fixture.loop, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            control.validate_state_fd(fd, fixture.loop_device, expected_state_ro)
        finally:
            os.close(fd)
    else:
        control.validate_state_fd(held_fd, fixture.loop_device, expected_state_ro)
    fixture.check()
    return {fixture.loop.name: fixture.loop_device}


def fixture_ro(control, fixture):
    fixture.check()
    fd = os.open(fixture.loop, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        # Actual production BLKROGET, sysfs RO, held identity, geometry and SB.
        return control.validate_state_fd(fd, fixture.loop_device)
    finally:
        os.close(fd)


def normalize_fixture_ro(control, fixture):
    """Reset only our unmounted owned loop's temporary kernel RO metadata.

    A detached loop minor can retain BLKROSET across backing-file replacements.
    This setup/teardown operation is outside the export measurements. The
    exclusive block claim also rejects a mount outside our private namespace;
    all production identity/geometry/SB/BLKROGET/sysfs checks remain active.
    """
    fixture.check()
    require(fixture.loop is not None and fixture.loop_device is not None,
            'validated owned loop required for RO normalization')
    require(not any(e['dev'] == fixture.loop_device or e['path'] == str(fixture.point)
                    for e in control.mounts()),
            'owned fixture mounted; refusing RO normalization')
    with mock.patch.object(control, 'PART', fixture.loop), \
            mock.patch.object(control, 'PART_SIZE', SIZE):
        fd = os.open(fixture.loop, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_EXCL)
        try:
            fixture.check()
            before = control.validate_state_fd(fd, fixture.loop_device)
            if before == 1:
                control.set_state_ro(fd, fixture.loop_device, 0)
            control.validate_state_fd(fd, fixture.loop_device, 0)
            fixture.check()
        finally:
            os.close(fd)
    row = {'stage': 'owned-loop-ro-normalization', 'block_ro_before': before,
           'block_ro_after': 0, 'outside_export_measurements': True}
    print(json.dumps(row, sort_keys=True), file=sys.stderr, flush=True)
    return before


def exercise(control, fixture, name, original_ro=0, unexpected=False, legacy_orphan=False):
    fixture.prepare(unexpected, legacy_orphan)
    prepared_resources = resources()
    allocated = sum(path.lstat().st_blocks * 512 for path in fixture.directory.rglob('*'))
    require(allocated <= MAX_SPACE, 'allocated fixture exceeds 256 MiB budget')
    stages = []

    def trace(label):
        base = fixture.check()
        fields = (base / 'stat').read_text().split()
        row = {'stage': label, 'stat': [int(x) for x in fields],
               'block_ro': fixture_ro(control, fixture),
               'sysfs_ro': int(control.node_ro(fixture.loop).read_text())}
        stages.append(row)
        print(json.dumps(row, sort_keys=True), file=sys.stderr, flush=True)

    def inspect(expected_state_ro=None, held_fd=None):
        return fixture_inspector(control, fixture, expected_state_ro, held_fd)

    # Explicit fixture-only adaptations. All production protection, restore,
    # mount/verify/collect/ordinary-unmount functions call through unchanged.
    with ExitStack() as patches:
        patches.enter_context(mock.patch.object(control, 'PART', fixture.loop))
        patches.enter_context(mock.patch.object(control, 'PART_SIZE', SIZE))
        patches.enter_context(mock.patch.object(control, 'inspect_target', inspect))
        attachment_ro = normalize_fixture_ro(control, fixture)
        require(fixture_ro(control, fixture) == 0, 'owned loop RO normalization failed')
        if original_ro == 1:
            fd = os.open(fixture.loop, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                control.set_state_ro(fd, fixture.loop_device, 1)
            finally:
                os.close(fd)
        require(fixture_ro(control, fixture) == original_ro, 'original fixture RO differs')
        # prepare() has already settled attachment uevents; sample only after
        # original-flag setup. Flag changes never relax hash/counter equality.
        before = fixture.fingerprint()
        receipt = {'hardware_acceptance': False, 'fixture': True,
                   'source_sha256': before['sha256']}
        identities = inspect(expected_state_ro=original_ro)
        trace('before-protected_journal')
        for method in ('mount_readonly', 'verify_mount', 'collect_journal', 'unmount'):
            original = getattr(control, method)

            def traced(*args, _original=original, _name=method, **kwargs):
                try:
                    return _original(*args, **kwargs)
                finally:
                    trace('after-' + _name)

            patches.enter_context(mock.patch.object(control, method, traced))
        if unexpected:
            try:
                control.protected_journal(fixture.point, identities)
            except ValueError as error:
                require(str(error) == 'unselected file in journal subtree',
                        'negative fixture failed for an unexpected reason: ' + str(error))
            else:
                raise ValueError('unexpected nonjournal file was accepted')
        else:
            files, observed_original_ro = control.protected_journal(fixture.point, identities)
            require(observed_original_ro == original_ro, 'reported original RO differs')
            require(len(files) == 1 and files[0][0] == JOURNAL and files[0][2] == PAYLOAD,
                    'synthetic journal bytes differ')
            info = files[0][1]
            require(info.st_uid == info.st_gid == 0 and stat.S_IMODE(info.st_mode) == 0o640,
                    'journal metadata differs')
            output = io.BytesIO()
            control.emit_archive(files, receipt, output)
            with tarfile.open(fileobj=io.BytesIO(output.getvalue()), mode='r:gz') as archive:
                require(archive.getnames() == [JOURNAL, 'export-receipt.json'], 'archive members differ')
                require(archive.extractfile(JOURNAL).read() == PAYLOAD, 'gzip/tar payload differs')
                member = archive.getmember(JOURNAL)
                require(member.uid == member.gid == 0 and member.mode == 0o640,
                        'archive metadata differs')
                require(json.load(archive.extractfile('export-receipt.json')) == receipt,
                        'gzip/tar receipt differs')
        trace('after-protected_journal')
        require(fixture_ro(control, fixture) == original_ro, 'original block RO was not restored')
        inspect(expected_state_ro=original_ro)
        require([row['stage'] for row in stages] ==
                ['before-protected_journal', 'after-mount_readonly', 'after-verify_mount',
                 'after-collect_journal', 'after-unmount', 'after-protected_journal'],
                'production stage trace differs')
        require(all(row['block_ro'] == row['sysfs_ro'] == 1 for row in stages[1:-1])
                and stages[0]['block_ro'] == stages[-1]['block_ro'] == original_ro,
                'production mount lacked block RO or original flag was not restored')
    require(not fixture.point.exists() and not any(e['dev'] == fixture.loop_device
            for e in control.mounts()), 'production helper did not clean its owned mount')
    after = fixture.fingerprint()  # Must precede detach; hashing covers every logical byte.
    require(before == after, 'source image SHA or loop write/discard/flush counters changed: ' +
            json.dumps({'before': before, 'after': after, 'stages': stages}, sort_keys=True))
    require(int.from_bytes(os.pread(fixture.fd, 4, 1024 + 232), 'little') ==
            fixture.legacy_orphan_inode, 'synthetic legacy orphan field changed')
    exported_resources = resources()
    fixture.export_assertions_passed = True
    return {'check': name, 'source_before': before, 'source_after': after,
            'attachment_block_ro_before_normalization': attachment_ro,
            'image_bytes': SIZE, 'block_ro_original': original_ro,
            'block_ro_restored': stages[-1]['block_ro'], 'stages': stages,
            'verified_mount_flags': ['ro', 'noload', 'noexec', 'nodev', 'nosuid'],
            'owned_mount_cleaned': True, 'internal_journal_inode': 8,
            'synthetic_legacy_last_orphan': fixture.legacy_orphan_inode,
            'orphan_fixture_is_actual_crash_journal': False,
            'allocated_fixture_bytes': allocated, 'resources_prepared': prepared_resources,
            'resources_exported': exported_resources}


def verify():
    require(os.geteuid() == 0, 'root required')
    require(Path('/proc/self/uid_map').read_text().split() == ['0', '0', '4294967295'],
            'initial user namespace required')
    require(hashlib.sha256(SOURCE.read_bytes()).hexdigest() == REVIEWED_SOURCE_SHA256,
            'production helper differs from independently reviewed SHA256')
    for tool in ('mke2fs', 'debugfs', 'losetup', 'mount', 'udevadm'):
        require(shutil.which(tool) is not None, tool + ' required')
    before = resources()
    private_namespace()
    spec = importlib.util.spec_from_file_location('journal_loop_control', SOURCE)
    control = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(control)
    workspace = Path(tempfile.mkdtemp(prefix='a733-journal-loop-', dir='/var/tmp'))
    workspace.chmod(0o700)
    current = None
    result = []
    def interrupted(number, frame):
        raise KeyboardInterrupt('signal ' + str(number))

    old_term = signal.signal(signal.SIGTERM, interrupted)
    try:
        cases = [('original-ro-zero', 0, False, False),
                 ('original-ro-one', 1, False, False),
                 ('synthetic-legacy-orphan', 0, False, True),
                 ('unexpected-file-refused', 0, True, False)]
        for name, original_ro, unexpected, legacy_orphan in cases:
            resources()
            current = Fixture(workspace, name)
            try:
                result.append(exercise(control, current, name, original_ro, unexpected, legacy_orphan))
            finally:
                current.cleanup(control)
            current = None
        require(not list(workspace.iterdir()), 'unexpected workspace contents retained')
        workspace.rmdir()
    except BaseException:
        print(json.dumps({'retained_workspace': str(workspace),
                          'retained_loop': str(current.loop) if current and current.loop else None,
                          'allocation_may_exist': current.allocation_may_exist if current else False,
                          'validated_loop_device': current.loop_device if current else None,
                          'backing_image': str(current.image) if current else None,
                          'mountpoint': str(current.point) if current else None,
                          'namespace': os.readlink('/proc/self/ns/mnt'),
                          'action': 'Inspect paths and loop backing identity; use ordinary umount then losetup --detach only for the owned fixture. Never delete an active mountpoint.'}),
              file=sys.stderr, flush=True)
        raise
    finally:
        signal.signal(signal.SIGTERM, old_term)
    after = resources()
    require(before['disk_free_bytes'] - after['disk_free_bytes'] <= MAX_SPACE,
            'disk consumption exceeded 256 MiB budget')
    return {'schema': 2, 'hardware_acceptance': False,
            'classification': ('new private synthetic ext4 loop fixtures only; production '
                               'protected_journal uses real block RO; fixed A733/RAM-entry, '
                               'physical-device and whole-host idle policy bypassed by explicit '
                               'fixture adapter; synthetic legacy orphan is not an actual crash journal'),
            'fixture_patches': ['PART=owned newly allocated loop', 'PART_SIZE=64 MiB',
                                'inspect_target=fixture identity and production validate_state_fd'],
            'source_sha256': hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
            'checks': result, 'resources_before': before, 'resources_after': after,
            'cleanup': 'owned mounts removed, loops detached, fixture paths deleted'}


if __name__ == '__main__':
    argparse.ArgumentParser(description=__doc__).parse_args()
    try:
        print(json.dumps(verify(), sort_keys=True))
    except (OSError, ValueError, subprocess.SubprocessError, tarfile.TarError, KeyboardInterrupt) as error:
        print('Journal loop validation failed: ' + str(error), file=sys.stderr)
        sys.exit(1)
