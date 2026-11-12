#!/usr/bin/python3
"""Execute a paused KVM-only child using a supervisor's protected run permit.

No operator launch/attestation bypass: this process must be the exact nonroot
child bound by the root supervisor, with its inherited lease and guest lock.
The root supervisor authenticates manifest/UUIDs and durable admitted state;
this child authenticates accessible placement, serial, release and payload bytes.
Root must still bind the actual QEMU/QMP peer before cont. No clean-shutdown or
checkpoint authority, service installation or hardware acceptance is provided.
"""
from contextlib import ExitStack
import argparse
import grp
import hashlib
import importlib.util
import os
from pathlib import Path
import re
import stat
import sys
from types import SimpleNamespace

BASE = Path(__file__).resolve().parents[1]
PAYLOAD = '/usr/lib/orangepi-hypervisor/payload.json'
PAYLOAD_LIMIT = 1024 * 1024
KERNEL_RELEASE = '6.6.98-a733-diag'
QEMU_PATH = '/usr/bin/qemu-system-aarch64'


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, BASE / path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


CONTEXT = module('managed_launch_context', 'scripts/guest-lifecycle-context.py')
ADMISSION = CONTEXT.ADMISSION
CONFIG = module('managed_launch_config', 'configs/guest_config.py')
LAUNCH = module('managed_launch_inputs', 'scripts/launch-kvm-guest.py')
CGROUP = module('managed_launch_cgroup', 'scripts/guest-cgroup-budget.py')
require = CONFIG.require


def close_fd(fd):
    # ExitStack calls each registered close once, including on later errors.
    os.close(fd)


def regular(info, uid, gid, mode, size):
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and
            (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode), info.st_size) ==
            (uid, gid, mode, size), 'managed input metadata differs')


def directory(fd, uid, gid, mode):
    info = os.fstat(fd)
    require(stat.S_ISDIR(info.st_mode) and
            (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (uid, gid, mode),
            'managed directory metadata differs')
    return info.st_dev, info.st_ino


def child_platform():
    require(os.getuid() != 0 and os.geteuid() != 0, 'nonroot managed child required')
    require(os.uname().machine == 'aarch64' and os.uname().release == KERNEL_RELEASE,
            'managed host ABI differs')
    require(CONTEXT.read_system('/proc/device-tree/compatible', 128) == CONTEXT.COMPATIBLE,
            'managed board compatible differs')
    # ManagedLocks checks self against the root-authenticated namespace pins.
    # Nonroot cannot dereference the root parent/PID1 namespace symlinks.
    info = os.lstat('/dev/kvm')
    gid = grp.getgrnam('kvm').gr_gid
    require(stat.S_ISCHR(info.st_mode) and info.st_uid == 0 and info.st_gid == gid and
            (os.major(info.st_rdev), os.minor(info.st_rdev)) == (10, 232) and
            stat.S_IMODE(info.st_mode) == 0o660 and os.access('/dev/kvm', os.R_OK | os.W_OK),
            'managed KVM node/access differs')
    return gid


def memory_budget(config):
    require(os.cpu_count() is not None and config['cpus'] <= os.cpu_count(),
            'managed CPU budget unavailable')
    data = CONTEXT.read_system('/proc/meminfo', 65536).decode('ascii')
    values = [line.split() for line in data.splitlines() if line.startswith('MemAvailable:')]
    require(len(values) == 1 and len(values[0]) == 3 and values[0][2] == 'kB' and
            re.fullmatch(r'[0-9]{1,20}', values[0][1]) is not None,
            'managed MemAvailable unavailable')
    require(int(values[0][1]) // 1024 >= config['memory_max_mib'] + 1024,
            'managed launch requires MemoryMax plus 1 GiB available')


def hash_root_file(held, expected, limit):
    CONTEXT.ENROLL.regular(held.info)
    require(0 < held.info.st_size <= limit, 'managed root file size differs')
    os.lseek(held.fd, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    remaining = held.info.st_size
    while remaining:
        chunk = os.read(held.fd, min(1024 * 1024, remaining))
        require(bool(chunk), 'managed root file truncated')
        digest.update(chunk)
        remaining -= len(chunk)
    held.recheck()
    require(digest.hexdigest() == expected, 'managed root file SHA-256 differs')


class Inputs(LAUNCH.PreparedGuest):
    def close(self):
        # Detach before closing; a reported close failure cannot justify reuse.
        held, self.fds = self.fds, {}
        error = None
        for fd in held.values():
            try:
                os.close(fd)
            except OSError as caught:
                error = caught
        if error is not None:
            raise error


def inherit(guest, locks):
    keep = set(guest.fds.values()) | set(locks.fdtuple)
    require(len(keep) == 5 and all(fd > 2 for fd in keep), 'managed descriptor collision')
    for entry in os.listdir('/proc/self/fd'):
        fd = int(entry)
        if fd > 2 and fd not in keep:
            try:
                os.set_inheritable(fd, False)
            except OSError as error:
                if error.errno != 9:  # listdir's transient FD may have closed.
                    raise
    for fd in keep:
        os.set_inheritable(fd, True)


class ManagedLaunch:
    """Own input/permit guards; inherited lock descriptors remain borrowed."""
    def __init__(self, run_id):
        CONTEXT.REGISTRY._uuid(run_id)
        self.run_id = run_id
        self.stack = ExitStack()
        self.edges = []
        self._entered = False
        self._executed = False
        self._closed = False

    def __enter__(self):
        require(not self._entered and not self._closed, 'managed launch is one-shot')
        self._entered = True
        try:
            gid = child_platform()
            boot = CONTEXT.read_system('/proc/sys/kernel/random/boot_id', 64).decode('ascii').rstrip('\n')
            self.admission = ADMISSION.Admission.open_runtime(boot, gid)
            self.stack.callback(self.admission.close)
            self.permit = self.admission.open_permit(self.run_id)
            self.stack.callback(self.permit.close)
            self.value = self.permit.value
            require(self.value['run_id'] == self.run_id, 'managed run differs')
            self.locks = self.admission.validate_inherited(self.permit)
            self.config = self.value['config']
            self.cgroup = CGROUP.CgroupBudget(self.config)
            self.stack.callback(self.cgroup.close)
            self.paths = CONFIG.paths(self.config)
            self._input_directories()
            args = SimpleNamespace(memory_mib=self.config['memory_mib'], cpus=self.config['cpus'],
                                   dry_run=False)
            for key in ('kernel', 'initrd', 'disk'):
                setattr(args, key, Path(self.paths[key]))
                setattr(args, key + '_sha256', self.config[key]['sha256'])
            if 'network' in self.config:
                args.host_tcp_port = self.config['network']['host_tcp_port']
                args.guest_tcp_port = self.config['network']['guest_tcp_port']
            self.guest = Inputs(args)
            self.stack.callback(self.guest.close)
            for key in ('kernel', 'initrd', 'disk'):
                self.guest.open_file(key)
                info = self.guest.snapshots[key]
                mutable = key == 'disk'
                regular(info, self.config['uid'] if mutable else 0,
                        self.config['gid'] if mutable else 0, 0o600 if mutable else 0o444,
                        self.config[key]['bytes'])
            require(len({(v.st_dev, v.st_ino) for v in self.guest.snapshots.values()}) == 3,
                    'managed artifacts must be separate inodes')
            # Authenticate NVMe placement before reading any artifact or root file.
            self.guest.verify_storage()
            self._serial()
            self.payload = CONTEXT.ENROLL.HeldPath(PAYLOAD)
            self.stack.callback(self.payload.close)
            hash_root_file(self.payload, self.value['host_payload_sha256'], PAYLOAD_LIMIT)
            self.qemu = CONTEXT.ENROLL.HeldPath(str(Path(QEMU_PATH).resolve(strict=True)))
            self.stack.callback(self.qemu.close)
            require(self.qemu.info.st_size == self.value['qemu']['bytes'] and
                    self.value['qemu']['path'] == QEMU_PATH and
                    self.qemu.info.st_mode & 0o111, 'managed QEMU identity differs')
            hash_root_file(self.qemu, self.value['qemu']['sha256'], self.value['qemu']['bytes'])
            require(os.pread(self.qemu.fd, 4, 0) == b'\x7fELF', 'managed QEMU must be ELF')
            self._qmp_directories()
            memory_budget(self.config)
            for key in ('kernel', 'initrd', 'disk'):
                self.guest.verify_file(key)
            self.recheck()
            return self
        except BaseException:
            self.close()
            raise

    def _open_directory(self, parent, name, uid, gid, mode):
        fd = os.open(name, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                     dir_fd=parent)
        self.stack.callback(close_fd, fd)
        identity = directory(fd, uid, gid, mode)
        self.edges.append((parent, name, fd, identity, uid, gid, mode))
        return fd

    def _input_directories(self):
        parent = os.open('/run', os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        self.stack.callback(close_fd, parent)
        directory(parent, 0, 0, 0o755)
        guest_root = self._open_directory(parent, 'a733-guests', 0, 0, 0o711)
        self._open_directory(guest_root, self.config['name'], 0, self.config['gid'], 0o710)

    def _qmp_directories(self):
        root = self._open_directory(self.admission.directory, 'qmp', 0,
                                    self.value['kvm_gid'], 0o710)
        self.qmp_dir = self._open_directory(root, self.run_id, 0, self.config['gid'], 0o730)
        ADMISSION._tmpfs(self.qmp_dir)
        self.qmp_path = '/run/a733-admission/qmp/' + self.run_id + '/qmp.sock'

    def _serial(self):
        namespace = self.guest.storage['namespace']
        serial = CONTEXT.read_system('/sys/class/block/' + namespace + '/device/serial', 128)
        require(serial.decode('ascii').strip() == self.value['storage']['nvme_serial'],
                'managed NVMe serial differs')

    def recheck(self):
        require(self._entered and not self._closed, 'managed launch closed')
        self.locks.recheck()
        self.permit.recheck()
        for parent, name, fd, identity, uid, gid, mode in self.edges:
            require(directory(fd, uid, gid, mode) == identity ==
                    CONTEXT.ENROLL.inode(os.stat(name, dir_fd=parent, follow_symlinks=False)),
                    'managed directory replaced')
        self.guest.verify_storage()
        self._serial()
        self.guest.validate_inputs()
        for key in ('kernel', 'initrd', 'disk'):
            info = self.guest.snapshots[key]
            require(CONTEXT.ENROLL.inode(os.stat(self.paths[key], follow_symlinks=False)) ==
                    (info.st_dev, info.st_ino), 'managed input path replaced')
        self.payload.recheck()
        self.qemu.recheck()
        require(CONTEXT.ENROLL.inode(os.stat(QEMU_PATH)) ==
                CONTEXT.ENROLL.inode(os.fstat(self.qemu.fd)), 'named managed QEMU replaced')
        try:
            os.stat('qmp.sock', dir_fd=self.qmp_dir, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ValueError('managed QMP socket destination already exists')
        self.cgroup.check()

    def argv(self):
        result = self.guest.argv(descriptor_paths=True)
        result[0] = QEMU_PATH
        return result + ['-S', '-qmp', 'unix:' + self.qmp_path + ',server=on,wait=off']

    def execute(self):
        require(not self._executed, 'managed exec already attempted')
        self._executed = True
        self.recheck()
        inherit(self.guest, self.locks)
        # fexecve binds the authenticated ELF descriptor, even if its name changes.
        os.execve(self.qemu.fd, self.argv(), {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'TZ': 'UTC'})
        raise RuntimeError('managed exec unexpectedly returned')

    def close(self):
        if not self._closed:
            self._closed = True
            self.stack.close()

    def __exit__(self, *unused):
        self.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args(argv)
    try:
        with ManagedLaunch(args.run_id) as prepared:
            prepared.execute()
    except (OSError, ValueError, ADMISSION.AdmissionError) as error:
        print('launch-managed-guest: ' + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
