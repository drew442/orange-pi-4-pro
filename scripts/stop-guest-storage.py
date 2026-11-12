#!/usr/bin/python3
"""Installed native-host shutdown of the policy-bound guest mount.

One normal unmount attempt; never reopen admission, retry, force or remove data.
The process census and mount observation cover the native PID1 namespace only;
foreign mount namespace clones are outside this receipt's acceptance scope.
"""
import argparse
import copy
import errno
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

BASE = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location('stop_storage_prepare', BASE / 'scripts/prepare-guest-boot.py')
BOOT = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(BOOT)
ADMISSION, ACTIVATE, CONTEXT = BOOT.ADMISSION, BOOT.ACTIVATE, BOOT.CONTEXT
POLICY, M, NORMAL = BOOT.BOOT_POLICY, BOOT.MAINTENANCE, BOOT.NORMAL
require = ADMISSION.require
MOUNTPOINT = '/run/a733-guests'
PID_LIMIT, FD_LIMIT, MAP_LIMIT = 65536, 262144, 16 * 1024 * 1024


def target_consumers(device):
    """Refuse visible task references to p5, including mmap and raw block FDs."""
    deadline = time.monotonic() + 10
    tasks = descriptors = processes = 0
    def bounded():
        require(time.monotonic() <= deadline, 'guest reference census timeout')
    def gone(error):
        if error.errno not in (errno.ENOENT, errno.ESRCH):
            raise error
    def inspect(path):
        bounded()
        info = os.stat(path)
        require(not (info.st_dev == device or
                stat.S_ISBLK(info.st_mode) and info.st_rdev == device),
                'guest filesystem process reference remains')
    with os.scandir('/proc') as entries:
        for process in entries:
            bounded()
            if not process.name.isdecimal():
                continue
            processes += 1
            require(processes <= PID_LIMIT, 'process census limit exceeded')
            try:
                with os.scandir(process.path + '/task') as threads:
                    for task in threads:
                        bounded()
                        require(task.name.isdecimal(), 'invalid task census entry')
                        tasks += 1
                        require(tasks <= PID_LIMIT, 'task census limit exceeded')
                        try:
                            for name in ('cwd', 'root', 'exe'):
                                try:
                                    inspect(task.path + '/' + name)
                                except OSError as error:
                                    gone(error)
                            with os.scandir(task.path + '/fd') as files:
                                for entry in files:
                                    bounded()
                                    descriptors += 1
                                    require(descriptors <= FD_LIMIT, 'descriptor census limit exceeded')
                                    require(entry.name.isdecimal(), 'invalid descriptor census entry')
                                    try:
                                        inspect(entry.path)
                                    except OSError as error:
                                        gone(error)
                            with open(task.path + '/maps', 'rb') as stream:
                                data = stream.read(MAP_LIMIT + 1)
                            bounded()
                            require(len(data) <= MAP_LIMIT, 'task maps limit exceeded')
                            for line in data.splitlines():
                                bounded()
                                fields = line.split(None, 5)
                                require(len(fields) >= 5, 'invalid task maps')
                                parts = fields[3].split(b':')
                                require(len(parts) == 2, 'invalid task map device')
                                mapped = os.makedev(int(parts[0], 16), int(parts[1], 16))
                                require(mapped != device, 'guest filesystem mapping remains')
                        except OSError as error:
                            gone(error)
            except OSError as error:
                gone(error)
    return dict(processes=processes, tasks=tasks, descriptors=descriptors)


def mount_pin():
    """No directory FD is retained across the census or unmount."""
    rows = ACTIVATE.fixed_entries()
    require(len(rows) == 1, 'exact guest mount required')
    info = os.stat(MOUNTPOINT, follow_symlinks=False)
    require(stat.S_ISDIR(info.st_mode), 'guest mountpoint must be a directory')
    return copy.deepcopy(rows), CONTEXT.physical_identity(info)


def observe_mount():
    try:
        ACTIVATE.host_namespace()
        return 'present' if ACTIVATE.fixed_entries() else 'absent'
    except BaseException:
        return 'unknown'


def stop_storage():
    result = dict(operation='stop-guest-storage', success=False, stage='validate',
                  hardware_acceptance=False, scope='native-pid1-mount', noop=False,
                  shutdown_closed=None, shutdown_close_attempted=False, umount_attempted=False, umount_acknowledged=False,
                  mount_after='unknown', cleanup_errors=[])
    policy = admission = lease = None
    creator = os.getpid(), threading.get_ident()
    boot_id = record = None
    primary = None
    def guard():
        require((os.getpid(), threading.get_ident()) == creator and
                threading.current_thread() is threading.main_thread(), 'original main thread required')
        require(os.getresuid() == (0, 0, 0), 'actual root required')
        require(ADMISSION._boot_id() == boot_id, 'host boot changed')
        BOOT.installed_guard(boot_id)
        ACTIVATE.host_namespace()
        if policy is not None:
            policy.check()
    def authority():
        guard()
        require(type(lease) is ADMISSION.Lease and lease.exclusive and
                lease.admission is admission, 'retained shutdown EX required')
        lease.recheck()
        ADMISSION._held_lock(lease.fd, 'WRITE', creator[0])
        require(not os.get_inheritable(lease.fd), 'noninheritable shutdown lease required')
        with admission._gate():
            require(admission._read() == record and record['state'] == 'closed' and
                    record['reason'] == 'shutdown', 'shutdown closure changed')
    try:
        boot_id = ADMISSION._boot_id()
        guard()
        require(all(component.ADMISSION is ADMISSION for component in (ACTIVATE, M, POLICY.CONTEXT)),
                'canonical admission family required')
        result['stage'] = 'storage-policy'
        policy = POLICY.Policy(boot_id)
        value = policy.value
        guard()
        # Absence does not create runtime or close admission. Policy is mandatory.
        if not ACTIVATE.fixed_entries():
            result.update(noop=True, mount_after='absent')
            guard()
        else:
            args = SimpleNamespace(target=value['target'], expected_model=value['expected_model'],
                expected_serial=value['storage']['nvme_serial'], expected_bytes=value['expected_bytes'],
                guest_uuid=value['storage']['guest_uuid'])
            result['stage'] = 'close-admission'
            admission = ADMISSION.Admission.open_existing(boot_id, NORMAL.local_kvm_gid(), NORMAL.manager_state)
            result['shutdown_close_attempted'] = True
            admission.close_gate('shutdown')
            with admission._gate():
                record = admission._read()
                require(record['state'] == 'closed' and record['reason'] == 'shutdown',
                        'shutdown closure required')
            result['shutdown_closed'] = True
            result['stage'] = 'stop-managed'
            M.stop_managed()
            guard()
            lease = admission.shutdown_lease()
            authority()
            result['stage'] = 'validate-target'
            before = ACTIVATE.snapshot(args, True)
            require(before['state']['uuid'] == value['storage']['state_uuid'], 'selected STATE differs')
            require(before['guest']['canonical_path'] == '/dev/nvme0n1p5' and
                    before['guest']['uuid'] == value['storage']['guest_uuid'], 'exact policy p5 required')
            major, minor = map(int, before['guest']['major_minor'].split(':'))
            device = os.makedev(major, minor)
            pin = mount_pin()
            require(pin[1][0] == device, 'mountpoint device differs')
            with BOOT.Resources() as locks:
                locks.enter_context(ACTIVATE.activation_lock(before, check=authority))
                authority()
                require(ACTIVATE.snapshot(args, True) == before and mount_pin() == pin,
                        'guest target changed before census')
                result['stage'] = 'consumer-census'
                M.consumer_check()
                result['census'] = target_consumers(device)
                authority()
                require(ACTIVATE.snapshot(args, True) == before and mount_pin() == pin,
                        'guest mount changed before unmount')
                command = ACTIVATE.checked_command('umount')
                require(command == '/usr/bin/umount', 'fixed normal unmount required')
                result['stage'] = 'unmount'
                result['umount_attempted'] = True
                try:
                    completed = subprocess.run([command, MOUNTPOINT], stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        env=M.ENVIRONMENT.copy(), timeout=30, check=False)
                    result['umount_acknowledged'] = completed.returncode == 0
                    require(completed.returncode == 0, 'normal guest unmount failed')
                finally:
                    result['mount_after'] = observe_mount()
                require(result['mount_after'] == 'absent', 'native guest mount absence unproved')
                result['stage'] = 'verify-unmounted'
                authority()
                after = ACTIVATE.snapshot(args, False)
                # Snapshot records contain only immutable identity/layout fields,
                # not mutable lsblk mountpoint/flags rows.
                require(after == before, 'storage identity changed after unmount')
                authority()
    except BaseException as error:
        primary = error
        result['error'] = BOOT.failure_detail(error)
        if admission is not None:
            try:
                with admission._gate():
                    observed = admission._read()
                    result['shutdown_closed'] = (observed['state'] == 'closed' and
                                                 observed['reason'] == 'shutdown')
            except BaseException as observation_error:
                result['closure_observation_error'] = BOOT.failure_detail(observation_error)
    finally:
        for name, resource in (('lease', lease), ('admission', admission), ('policy', policy)):
            if resource is not None:
                try:
                    resource.close()
                except BaseException as error:
                    result['cleanup_errors'].append(dict(resource=name, **BOOT.failure_detail(error)))
        if primary is None and not result['cleanup_errors']:
            result.update(success=True, stage='complete')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    receipt = stop_storage()
    print(json.dumps(receipt, sort_keys=True))
    return 0 if receipt['success'] else 1


if __name__ == '__main__':
    sys.exit(main())
