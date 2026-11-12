#!/usr/bin/env python3
"""Opt-in native p5 activation check on a newly owned sparse GPT loop only.

Run as root with no arguments; redirect stdout to retain the JSON evidence.
NVMe/running-host classification, installed maintenance/admission and TTY
authorization are explicit fixtures with no authority. Actual
mounts, exclusive claims, locks, review storage, permissions and cleanup execute
natively. This is no evidence of A733 boot, physical NVMe policy or KVM.
"""
import argparse
import copy
import ctypes
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest import mock
import uuid

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('guest_loop_controller', REPO / 'scripts/activate-guest-storage.py')
CONTROL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONTROL)
POLICY = CONTROL.POLICY
require = CONTROL.require
SIZE = 250_000_000_000


def fixture_maintenance(storage, guard, unlocked):
    """Test-only API custody; replaces platform/admission, grants no authority."""
    instances = []

    class FixtureStorageMaintenance:
        def __init__(self, expected):
            require(expected == storage, 'fixture storage tuple differs')
            require(not instances or instances[-1].closed, 'fixture custody overlaps')
            self.creator = os.getpid(), threading.get_ident()
            self.closed = self.finished = self.binding_started = self.bound = False
            self.checks = 0
            self.events = ['create', 'unbound']
            self.receipt = dict(fixture='platform and admission replaced; no authority',
                                hardware_acceptance=False, success=False, storage_bound=False,
                                open_attempted=False, open_acknowledged=False)
            instances.append(self)
            self.recheck()

        def creator_check(self):
            require((os.getpid(), threading.get_ident()) == self.creator,
                    'fixture custody changed process/thread')

        def recheck(self):
            self.creator_check()
            require(not self.closed and not self.finished, 'fixture custody ended')
            guard()
            self.checks += 1

        def mutation(self):
            self.recheck()
            require(not self.binding_started and self.checks > 2,
                    'mutation outside unbound fixture custody')

        def bind_storage(self):
            self.recheck()
            require(not self.binding_started, 'fixture storage binding already attempted')
            self.binding_started = True
            unlocked()
            self.bound = True
            self.events.append('bind')
            self.receipt['storage_bound'] = True

        def finish_success(self):
            self.recheck()
            require(self.bound, 'fixture storage not bound')
            unlocked()
            self.finished = True
            self.events.append('finish')
            self.receipt.update(success=True, open_attempted=True, open_acknowledged=True)
            return copy.deepcopy(self.receipt)

        def close(self):
            self.creator_check()
            if self.closed:
                return
            unlocked()
            self.closed = True
            self.events.append('close')
            self.receipt.update(events=list(self.events), rechecks=self.checks)

    helper = SimpleNamespace(StorageMaintenance=FixtureStorageMaintenance,
                             M=SimpleNamespace(registered_storage=lambda: copy.deepcopy(storage)))
    return helper, instances


def assert_custody(instances, successful):
    require(len(instances) == len(successful), 'fixture custody acquisition count differs')
    for held, success in zip(instances, successful):
        require(held.closed and held.bound == held.finished == success,
                'fixture custody outcome differs')
        require(held.events == (['create', 'unbound', 'bind', 'finish', 'close'] if success else
                                ['create', 'unbound', 'close']), 'fixture custody ordering differs')
        require(held.checks > 2, 'transaction did not recheck fixture custody')


def descriptor_snapshot():
    descriptors = {}
    for entry in Path('/proc/self/fd').iterdir():
        try:
            info = os.fstat(int(entry.name))
            descriptors[int(entry.name)] = (info.st_dev, info.st_ino, os.readlink(entry))
        except FileNotFoundError:
            pass  # The directory iterator's own descriptor may already be closed.
        except OSError as error:
            if error.errno != 9:
                raise
    return descriptors


class OwnedDisk:
    """Identity checks never accept caller supplied targets or backing files."""
    def __init__(self, workspace, encoded, geometry):
        self.workspace = workspace
        self.workspace_id = self.identity(workspace)
        self.backing = workspace / 'disk.img'
        self.loop = None
        self.parts = geometry['partitions']
        with self.backing.open('x+b') as stream:
            stream.truncate(SIZE)
            for extent in encoded['extents']:
                stream.seek(extent['offset_bytes'])
                stream.write(extent['data'])
            self.sentinels = {}
            for part in self.parts[:4]:
                data = ('owned p%d preservation sentinel\n' % part['number']).encode().ljust(4096, b'!')
                stream.seek(part['start_bytes'])
                stream.write(data)
                self.sentinels[part['number']] = hashlib.sha256(data).hexdigest()
            stream.flush()
            os.fsync(stream.fileno())
        self.backing.chmod(0o600)
        self.backing_id = self.identity(self.backing)

    @staticmethod
    def identity(path):
        info = path.lstat()
        return info.st_dev, info.st_ino

    def check(self):
        require(self.identity(self.workspace) == self.workspace_id, 'workspace replaced')
        info = self.workspace.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == info.st_gid == 0
                and stat.S_IMODE(info.st_mode) == 0o700, 'unsafe private workspace')
        info = self.backing.lstat()
        require(self.identity(self.backing) == self.backing_id and stat.S_ISREG(info.st_mode)
                and info.st_uid == info.st_gid == 0 and info.st_nlink == 1
                and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size == SIZE,
                'owned backing identity differs')
        if self.loop is None:
            return None
        name = Path(self.loop).name
        require(name.startswith('loop') and name[4:].isdigit() and self.loop == '/dev/' + name,
                'unexpected loop path')
        base = Path('/sys/class/block') / name
        backing = Path('/' + (base / 'loop/backing_file').read_text().strip().lstrip('/'))
        require(backing.resolve(strict=True) == self.backing and self.identity(backing) == self.backing_id,
                'loop backing inode differs')
        require(POLICY.PLANNER.identify(self.loop)[1] == (base / 'dev').read_text().strip(),
                'loop sysfs identity differs')
        require(int((base / 'size').read_text()) * 512 == SIZE, 'loop size differs')
        return base

    def partition(self, number):
        parent = self.check()
        require(parent is not None and 1 <= number <= 5, 'owned loop partition required')
        path = Path(self.loop + 'p' + str(number))
        info = path.lstat()
        require(stat.S_ISBLK(info.st_mode) and info.st_uid == 0, 'partition is not root owned block device')
        node = (Path('/sys/dev/block') / f'{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}').resolve(strict=True)
        part = self.parts[number - 1]
        require(node.parent == parent.resolve(strict=True)
                and int((node / 'partition').read_text()) == number
                and int((node / 'start').read_text()) * 512 == part['start_bytes']
                and int((node / 'size').read_text()) * 512 == part['size_bytes'],
                'owned partition geometry differs')
        return path

    def preserved(self):
        self.check()
        with self.backing.open('rb') as stream:
            for part in self.parts[:4]:
                stream.seek(part['start_bytes'])
                require(hashlib.sha256(stream.read(4096)).hexdigest() == self.sentinels[part['number']],
                        'non-guest partition sentinel changed')


def private_namespace():
    library = ctypes.CDLL(None, use_errno=True)
    if library.unshare(0x00020000) != 0:  # CLONE_NEWNS
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    subprocess.run(['mount', '--make-rprivate', '/'], check=True)
    own, init = os.stat('/proc/self/ns/mnt'), os.stat('/proc/1/ns/mnt')
    require((own.st_dev, own.st_ino) != (init.st_dev, init.st_ino), 'private mount namespace required')


def verify():
    require(os.geteuid() == 0, 'root required for opt-in private loop test')
    # Run before namespace/workspace/device setup and never use a caller path.
    with CONTROL.PREP.safe_parent(Path('/var/lib')) as parent:
        CONTROL.UPDATE.storage_guard(parent)
    require(shutil.disk_usage('/var/lib').free >= 2 * 1024**3, '2 GiB fixed storage free required')
    private_namespace()
    workspace = Path(tempfile.mkdtemp(prefix='a733-guest-mount-loop-', dir='/var/lib'))
    workspace.chmod(0o700)
    owned = None
    setup = None
    setup_identity = None
    private_run = False
    run_identity = None
    result = {}
    real_run = subprocess.run
    previous_int = signal.signal(signal.SIGINT, signal.default_int_handler)
    previous_term = signal.signal(signal.SIGTERM, CONTROL.WRITER.interrupted)
    previous_mask = signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGINT, signal.SIGTERM})
    try:
        encoded = POLICY.LIVE.LAYOUT.encode_gpt(SIZE, 512, 512)
        geometry = POLICY.LIVE.LAYOUT.layout(SIZE, 512, 512)
        owned = OwnedDisk(workspace, encoded, geometry)
        owned.check()
        owned.loop = subprocess.check_output(['losetup', '--find', '--show', '--partscan', str(owned.backing)], text=True).strip()
        real_run(['udevadm', 'settle', '--timeout=10'], check=True, capture_output=True, timeout=15)
        paths = {n: owned.partition(n) for n in range(1, 6)}
        guest_uuid = str(uuid.uuid4())
        owned.partition(5)
        real_run(['mke2fs', '-q', '-F', '-t', 'ext4', '-b', '4096', '-T', 'largefile4',
                  '-m', '0', '-U', guest_uuid, '-E', 'lazy_itable_init=0,lazy_journal_init=0,nodiscard',
                  str(paths[5])], check=True, capture_output=True)
        owned.partition(5)
        real_run(['debugfs', '-w', '-R', 'set_inode_field <2> mode 040700', str(paths[5])],
                 check=True, capture_output=True)
        setup = workspace / 'guest-setup'
        setup.mkdir(mode=0o700)
        setup_identity = OwnedDisk.identity(setup)
        owned.partition(5)
        real_run(['mount', '-t', 'ext4', '-o', 'rw,nodev,nosuid,noexec', str(paths[5]), str(setup)], check=True)
        sentinel = b'preserve existing guest payload\n'
        (setup / 'preservation-proof').write_bytes(sentinel)
        with (setup / 'preservation-proof').open('rb') as stream:
            os.fsync(stream.fileno())
        real_run(['umount', str(setup)], check=True)
        require(OwnedDisk.identity(setup) == setup_identity, 'setup directory replaced')
        setup.rmdir()
        setup = None
        real_run(['mount', '-t', 'tmpfs', '-o', 'mode=0755,nodev,nosuid,size=64m', 'tmpfs', '/run'], check=True)
        private_run = True
        run_identity = OwnedDisk.identity(Path('/run'))
        base = owned.check()
        disk = dict(requested_path=owned.loop, canonical_path=owned.loop,
                    major_minor=POLICY.PLANNER.identify(owned.loop)[1], model='PRIVATE GPT LOOP',
                    serial='NO-PHYSICAL-TARGET', size_bytes=SIZE,
                    logical_sector_bytes=int((base / 'queue/logical_block_size').read_text()),
                    physical_sector_bytes=int((base / 'queue/physical_block_size').read_text()))
        nodes = [{'kname': Path(owned.loop).name, 'maj:min': disk['major_minor']}]
        for number in range(1, 6):
            nodes.append({'kname': paths[number].name, 'maj:min': POLICY.PLANNER.identify(paths[number])[1]})
        guest = dict(disk, requested_path=str(paths[5]), canonical_path=str(paths[5]),
                     major_minor=nodes[5]['maj:min'], size_bytes=geometry['partitions'][4]['size_bytes'],
                     partition_number=5, uuid=guest_uuid, mountpoint=str(CONTROL.MOUNTPOINT),
                     partition_uuid=encoded['partition_guids'][4])
        # These are explicit placeholders, never evidence of running NVMe state.
        context = dict(disk=disk, guest=guest, active={'slot': 'A', 'fixture': True},
                       state={'major_minor': '0:999', 'uuid': str(uuid.uuid4()), 'fixture': True})
        args = SimpleNamespace(target=owned.loop, expected_model=disk['model'],
                               expected_serial=disk['serial'], expected_bytes=SIZE, guest_uuid=guest_uuid)

        def snapshot(arguments, mounted):
            require(arguments is args, 'foreign activation arguments')
            for number in range(1, 6):
                owned.partition(number)
            POLICY.LIVE.sysfs_geometry(nodes, geometry)
            identities = [POLICY.PLANNER.identify('/dev/' + node['kname'])[1] for node in nodes]
            require(len(set(identities)) == 6 and identities == [node['maj:min'] for node in nodes],
                    'duplicate or changed block identity')
            table = POLICY.MANAGEMENT.mount_table(Path('/proc/self/mountinfo').read_text())
            POLICY.mounted_guest_policy(table, nodes[5], mounted)
            flags = POLICY.PLANNER.read_safety(nodes)
            for index, node in enumerate(nodes):
                actual = flags[node['maj:min']]
                require(not actual['holders'] and not actual['read_only'] and not actual['swap']
                        and actual['mounted'] == (index == 5 and mounted), 'unexpected device safety flags')
                rows = [row for items in table.values() for row in items if row['id'] == node['maj:min']]
                require(len(rows) == (1 if index == 5 and mounted else 0), 'unexpected device mount alias')
            fd = POLICY.LIVE.open_checked(disk)
            try:
                gpt = POLICY.LIVE.gpt_context(fd, disk)
            finally:
                os.close(fd)
            require(gpt['disk_guid'] == encoded['disk_guid'], 'GPT identity changed')
            fd = POLICY.LIVE.open_checked(guest)
            try:
                POLICY.guest_superblock(fd, guest_uuid, guest['size_bytes'])
            finally:
                os.close(fd)
            return dict(copy.deepcopy(context), gpt=gpt)

        counts = {'mount': 0, 'chmod': 0}
        interrupt = False
        baseline_descriptors = None

        def unlocked():
            owned.partition(5)
            require(descriptor_snapshot() == baseline_descriptors, 'activation retained core descriptors')
            # Independent opens probe the actual locks. O_EXCL is inappropriate
            # once the mounted filesystem owns the kernel's guest block claim.
            for path in (CONTROL.LOCK, Path(owned.loop)):
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                finally:
                    os.close(fd)

        storage = dict(state_uuid=context['state']['uuid'], guest_uuid=guest_uuid,
                       nvme_serial=disk['serial'])
        maintenance, custody = fixture_maintenance(storage, lambda: owned.partition(5), unlocked)
        real_adjacent = CONTROL.adjacent

        def fixture_helper(name, filename):
            if (name, filename) == ('guest_activation_maintenance', 'guest-storage-maintenance.py'):
                return maintenance
            return real_adjacent(name, filename)

        real_chmod = os.fchmod

        def observed_run(command, *positional, **keywords):
            nonlocal interrupt
            if Path(command[0]).name in ('mount', 'umount'):
                require(bool(custody), 'mount without fixture custody')
                custody[-1].mutation()
                owned.partition(5)
            value = real_run(command, *positional, **keywords)
            if Path(command[0]).name == 'mount':
                counts['mount'] += 1
                if interrupt:
                    interrupt = False
                    os.kill(os.getpid(), signal.SIGINT)
            return value

        def observed_chmod(fd, mode):
            require(bool(custody), 'chmod without fixture custody')
            custody[-1].mutation()
            require(f'{os.major(os.fstat(fd).st_dev)}:{os.minor(os.fstat(fd).st_dev)}'
                    == guest['major_minor'], 'chmod target differs from owned guest')
            counts['chmod'] += 1
            return real_chmod(fd, mode)

        def reviewed_apply(plan):
            nonlocal baseline_descriptors
            path = Path('/run/review.json')
            path.write_bytes(CONTROL.WRITER.canonical_json(plan))
            path.chmod(0o400)
            review = CONTROL.Review(path, plan['context'])
            try:
                require(review.value == plan, 'held reviewed plan differs')
                baseline_descriptors = descriptor_snapshot()
                return CONTROL.apply(args, plan, plan_check=review.check)
            finally:
                review.close()
                path.unlink()

        with mock.patch.object(CONTROL, 'snapshot', new=snapshot), \
             mock.patch.object(CONTROL, 'host_namespace', new=lambda: None), \
             mock.patch.object(CONTROL, 'authorize', new=lambda unused: None), \
             mock.patch.object(CONTROL, 'adjacent', new=fixture_helper), \
             mock.patch.object(CONTROL.subprocess, 'run', new=observed_run), \
             mock.patch.object(CONTROL.os, 'fchmod', new=observed_chmod):
            plan = CONTROL.make_plan(args)
            require(not CONTROL.fixed_entries() and not plan['mounted_before'] and counts == {'mount': 0, 'chmod': 0},
                    'prepare mounted or changed permissions')
            interrupt = True
            interrupted = reviewed_apply(plan)
            require(not interrupted['success'] and interrupted['guest_may_be_modified']
                    and interrupted['mount_attempted'] and not interrupted['mount_retained']
                    and not interrupted['cleanup_errors'] and not CONTROL.fixed_entries()
                    and not CONTROL.MOUNTPOINT.exists(), 'interruption cleanup failed: ' + json.dumps(interrupted))
            require(counts == {'mount': 1, 'chmod': 0}, 'interrupt did not occur immediately after actual mount')
            retry_plan = CONTROL.make_plan(args)
            require(retry_plan == plan, 'unmounted retry context changed')
            success = reviewed_apply(retry_plan)
            require(success['success'] and success['mount_retained'] and success['permissions_may_be_modified'],
                    'retry failed: ' + json.dumps(success))
            info = CONTROL.MOUNTPOINT.stat()
            require(stat.S_IMODE(info.st_mode) == 0o711 and info.st_uid == info.st_gid == 0,
                    'mounted guest root permissions differ')
            require((CONTROL.MOUNTPOINT / 'preservation-proof').read_bytes() == sentinel, 'guest sentinel changed')
            before = dict(counts)
            mounted_plan = CONTROL.make_plan(args)
            require(mounted_plan['context'] == plan['context'] and mounted_plan['mounted_before'], 'mounted context differs')
            idempotent = reviewed_apply(mounted_plan)
            require(idempotent['success'] and idempotent['mounted_before'] and idempotent['mount_retained']
                    and not idempotent['mount_attempted'] and not idempotent['guest_may_be_modified']
                    and not idempotent['permissions_may_be_modified'] and counts == before,
                    'idempotent apply changed guest: ' + json.dumps(idempotent))
            assert_custody(custody, [False, True, True])
            owned.preserved()
            require(snapshot(args, True) == plan['context'], 'final actual GPT/context differs')
        result = dict(classification='private sparse-GPT native mount only; running-host/NVMe classification, active/state, installed maintenance/admission and TTY are explicit fixtures with no authority; no physical NVMe, boot or KVM acceptance',
                      logical_disk_bytes=SIZE, guest_bytes=guest['size_bytes'], guest_uuid=guest_uuid,
                      reports={'interrupted': interrupted, 'retry': success, 'idempotent': idempotent},
                      resources={'workspace': str(workspace), 'backing_inode': owned.backing_id,
                                 'loop': owned.loop, 'guest': str(paths[5]), 'namespace': os.readlink('/proc/self/ns/mnt')},
                      counters=counts, public_apply_fixture_custody_ordering_checked=True,
                      core_descriptors_and_locks_released_before_binding=True,
                      hardware_acceptance=False, actual_prepare_unmounted=True, internal_journal_inode=8,
                      preservation_sentinel_unchanged=True, other_partition_4k_sentinels_unchanged=True)
    finally:
        errors = []
        # Never peel away a foreign mount or detach a reassigned loop on error.
        def unmount_owned(path, expected_device):
            owned.partition(5)
            rows = [row for row in CONTROL.mount_entries() if row[4] == str(path)]
            if rows:
                require(len(rows) == 1 and rows[0][2] == expected_device, 'foreign cleanup mount retained')
                real_run(['umount', str(path)], check=True)
                require(not any(row[4] == str(path) for row in CONTROL.mount_entries()), 'cleanup mount remains')
        if owned is not None and owned.loop is not None:
            for path in ((CONTROL.MOUNTPOINT if private_run else None), setup):
                if path is not None:
                    try:
                        device = os.stat(owned.partition(5)).st_rdev
                        unmount_owned(path, f'{os.major(device)}:{os.minor(device)}')
                    except BaseException as error:
                        errors.append(str(error))
        if setup is not None and not errors:
            try:
                require(OwnedDisk.identity(setup) == setup_identity and not list(setup.iterdir()), 'setup directory changed')
                setup.rmdir()
                setup = None
            except BaseException as error:
                errors.append(str(error))
        if private_run and not errors:
            try:
                require(OwnedDisk.identity(Path('/run')) == run_identity, 'private /run replaced')
                require(not CONTROL.fixed_entries(), 'guest mount remains')
                if CONTROL.MOUNTPOINT.exists():
                    info = CONTROL.MOUNTPOINT.lstat()
                    require(stat.S_ISDIR(info.st_mode) and info.st_uid == info.st_gid == 0
                            and stat.S_IMODE(info.st_mode) == 0o700 and not list(CONTROL.MOUNTPOINT.iterdir()),
                            'unexpected mount directory retained')
                    CONTROL.MOUNTPOINT.rmdir()
                real_run(['umount', '/run'], check=True)
                private_run = False
            except BaseException as error:
                errors.append(str(error))
        if owned is not None and owned.loop is not None and not errors:
            try:
                for number in range(1, 6):
                    owned.partition(number)
                owned.preserved()
                owned.check()
                real_run(['losetup', '--detach', owned.loop], check=True)
                attribute = Path('/sys/class/block') / Path(owned.loop).name / 'loop/backing_file'
                for unused in range(50):
                    if not attribute.exists():
                        break
                    time.sleep(0.1)
                else:
                    raise ValueError('owned loop detach did not complete')
                owned.loop = None
            except BaseException as error:
                errors.append(str(error))
        if not errors and not private_run and setup is None and (owned is None or owned.loop is None):
            try:
                if owned is not None:
                    owned.check()
                    require(set(workspace.iterdir()) == {owned.backing}, 'foreign workspace objects retained')
                    owned.backing.unlink()
                require(not list(workspace.iterdir()), 'workspace not empty')
                workspace.rmdir()
            except BaseException as error:
                errors.append(str(error))
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)
        signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
        if errors:
            print(json.dumps({'cleanup_errors': errors, 'retained_workspace': str(workspace),
                              'loop': owned.loop if owned else None, 'private_run': private_run}), file=sys.stderr)
            raise RuntimeError('private cleanup failed; owned workspace retained: ' + '; '.join(errors))
    result['cleanup'] = 'owned guest/setup/private-run mounts removed; owned loop detached; workspace deleted; private namespace ends at process exit'
    return result


if __name__ == '__main__':
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(json.dumps(verify(), indent=2, sort_keys=True))
