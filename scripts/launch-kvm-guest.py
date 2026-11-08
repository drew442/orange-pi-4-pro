#!/usr/bin/env python3
"""Prepare or launch an explicit KVM-only AArch64 guest; no acceptance claim.

Supply trusted SHA-256 values for all three inputs on every invocation. The raw
disk is mutable: after a clean guest shutdown, record and review its new hash as
the expected checkpoint for the next launch. Never rehash a running guest disk.
Keep these files on NVMe and prevent concurrent in-place modification during
verification and execution. Held descriptors prevent pathname replacement from
redirecting QEMU; an exclusive disk lock coordinates cooperating launchers only.
No fixture download, disk creation, or hardware validation occurs
here. Dry-run verifies inputs without requiring QEMU or /dev/kvm.
"""

import argparse
import fcntl
import errno
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys


_spec = importlib.util.spec_from_file_location('a733_guest_storage', Path(__file__).with_name('guest-storage.py'))
STORAGE = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(STORAGE)


def positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def tcp_port(value):
    if not re.fullmatch(r'[0-9]+', value):
        raise argparse.ArgumentTypeError('must be a decimal TCP port')
    return int(value)


def network_config(args):
    """Validate the one supported forward before touching any guest input."""
    host = getattr(args, 'host_tcp_port', None)
    guest = getattr(args, 'guest_tcp_port', None)
    if host is None and guest is None:
        return None
    if type(host) is not int or not 1024 <= host <= 65535:
        raise ValueError('host_tcp_port must be an integer from 1024 through 65535')
    if type(guest) is not int or not 1 <= guest <= 65535:
        raise ValueError('guest_tcp_port must be an integer from 1 through 65535')
    return {'mode': 'isolated', 'host_tcp_port': host, 'guest_tcp_port': guest}


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    for name in ("kernel", "initrd", "disk"):
        result.add_argument(f"--{name}", required=True, type=Path)
        result.add_argument(f"--{name}-sha256", required=True)
    result.add_argument("--memory-mib", required=True, type=positive_int)
    result.add_argument("--cpus", required=True, type=positive_int)
    result.add_argument('--host-tcp-port', type=tcp_port,
                        help='paired loopback-only TCP listener, from 1024 through 65535')
    result.add_argument('--guest-tcp-port', type=tcp_port,
                        help='paired TCP destination at fixed guest address 10.0.2.15')
    result.add_argument("--dry-run", action="store_true")
    return result


def file_identity(info):
    """Fields that must remain stable while an input is authenticated."""
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class PreparedGuest:
    """Own verified input descriptors until exec or a refused launch.

    Held descriptors defeat pathname replacement. The disk flock coordinates
    cooperating launches; it cannot stop a same-UID writer ignoring that lock.
    Kernel/initrd must remain protected against concurrent in-place writes.
    """

    def __init__(self, args):
        self.args = args
        self.network = network_config(args)
        self.fds = {}
        self.snapshots = {}
        self.inputs = {}

    def close(self):
        for fd in self.fds.values():
            os.close(fd)
        self.fds.clear()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def open_file(self, name):
        path = getattr(self.args, name)
        expected = getattr(self.args, f"{name}_sha256")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
            raise ValueError(f"Invalid SHA-256 for {path}")
        # lstat plus NONBLOCK avoids waiting even if a FIFO replaces the leaf.
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ValueError(f"Input must be a single-link regular file: {path}")
        if not before.st_size:
            raise ValueError(f"Input is empty: {path}")
        writable = name == "disk" and not self.args.dry_run
        flags = os.O_RDWR if writable else os.O_RDONLY
        fd = os.open(path, flags | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        if fd < 3:
            try:
                duplicate = fcntl.fcntl(fd, fcntl.F_DUPFD_CLOEXEC, 3)
            finally:
                os.close(fd)
            fd = duplicate
        self.fds[name] = fd
        opened = os.fstat(fd)
        if file_identity(before) != file_identity(opened):
            raise ValueError(f"Input changed during open: {path}")
        if writable:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ValueError(f"Guest disk is already locked: {path}") from error
        self.snapshots[name] = opened

    def verify_file(self, name):
        path = getattr(self.args, name)
        expected = getattr(self.args, f"{name}_sha256")
        fd = self.fds[name]
        opened = self.snapshots[name]
        digest = hashlib.sha256()
        remaining = opened.st_size
        # Never chase a writer extending the file. Authenticate exactly the
        # captured size and then reject growth or any other metadata change.
        while remaining:
            chunk = os.read(fd, min(1024 * 1024, remaining))
            if not chunk:
                raise ValueError(f"Input shortened during verification: {path}")
            digest.update(chunk)
            remaining -= len(chunk)
        self.validate_input(name)
        if digest.hexdigest() != expected.lower():
            raise ValueError(f"SHA-256 mismatch: {path}")
        self.inputs[name] = {"path": str(path.absolute()),
                             "sha256": digest.hexdigest(),
                             "size_bytes": opened.st_size}

    def validate_input(self, name):
        current = os.fstat(self.fds[name])
        if file_identity(current) != file_identity(self.snapshots[name]):
            raise ValueError(f"Input changed during verification: {getattr(self.args, name)}")

    def validate_inputs(self):
        for name in self.fds:
            self.validate_input(name)

    def verify_storage(self):
        self.storage = STORAGE.verify_guest_storage(
            self.fds['disk'], boot_fds=[self.fds['kernel'], self.fds['initrd']])

    def argv(self, descriptor_paths=False):
        paths = {name: f"/proc/self/fd/{self.fds[name]}" if descriptor_paths
                 else info["path"] for name, info in self.inputs.items()}
        block = {"driver": "raw", "node-name": "guest-disk",
                 "file": {"driver": "file", "filename": paths["disk"]}}
        netdev = 'user,id=guestnet,restrict=on'
        if self.network is not None:
            netdev += (',ipv6=off,hostfwd=tcp:127.0.0.1:' +
                       str(self.network['host_tcp_port']) + '-10.0.2.15:' +
                       str(self.network['guest_tcp_port']))
        return [shutil.which("qemu-system-aarch64") or "qemu-system-aarch64",
                "-machine", "virt,gic-version=host", "-accel", "kvm", "-cpu", "host",
                "-m", str(self.args.memory_mib), "-smp", str(self.args.cpus),
                "-nodefaults", "-nographic", "-serial", "stdio", "-monitor", "none",
                "-kernel", paths["kernel"], "-initrd", paths["initrd"],
                "-append", "console=ttyAMA0 rdinit=/init",
                "-blockdev", json.dumps(block, separators=(",", ":")),
                "-device", "virtio-blk-pci,drive=guest-disk",
                "-netdev", netdev,
                "-device", "virtio-net-pci,netdev=guestnet,romfile="]

    def report(self):
        return {"operation": "dry-run" if self.args.dry_run else "launch",
                "hardware_acceptance": "pending", "verified_inputs": self.inputs,
                "argv": self.argv(), "storage_policy": self.storage or "not checked in file-only dry-run"}

    def inherit_inputs(self):
        """Pass only input descriptors beyond stdio into the QEMU process."""
        held = set(self.fds.values())
        for entry in os.listdir("/proc/self/fd"):
            fd = int(entry)
            if fd > 2 and fd not in held:
                try:
                    os.set_inheritable(fd, False)
                except OSError as error:
                    # listdir's own descriptor may already have disappeared.
                    if error.errno != errno.EBADF:
                        raise
        for fd in held:
            os.set_inheritable(fd, True)


def prepare(args):
    guest = PreparedGuest(args)
    try:
        for name in ("kernel", "initrd", "disk"):
            guest.open_file(name)
        disk = guest.snapshots["disk"]
        if any((disk.st_dev, disk.st_ino) == (guest.snapshots[name].st_dev,
                                            guest.snapshots[name].st_ino)
               for name in ("kernel", "initrd")):
            raise ValueError("Mutable disk must be separate from kernel and initrd")
        # Validate storage before reading payload bytes: even a hash read can
        # cause atime writes on an incorrectly mounted SD filesystem.
        guest.storage = None
        if not args.dry_run:
            guest.verify_storage()
        for name in ('kernel', 'initrd', 'disk'):
            guest.verify_file(name)
        guest.validate_inputs()
        return guest
    except BaseException:
        guest.close()
        raise


def require_launch_access(args):
    if os.getuid() == 0 or os.geteuid() == 0:
        raise ValueError("Launch requires a non-root process with /dev/kvm access")
    if not shutil.which("qemu-system-aarch64"):
        raise ValueError("qemu-system-aarch64 is not installed")
    if not stat.S_ISCHR(os.stat("/dev/kvm").st_mode):
        raise ValueError("/dev/kvm must be a character device")
    if not os.access("/dev/kvm", os.R_OK | os.W_OK):
        raise ValueError("Read/write access to /dev/kvm is required")
    if not os.access(args.disk, os.R_OK | os.W_OK):
        raise ValueError("Read/write access to the guest disk is required")


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        network_config(args)
        if not args.dry_run:
            require_launch_access(args)
        with prepare(args) as guest:
            if args.dry_run:
                print(json.dumps(guest.report(), indent=2))
            else:
                guest.verify_storage()
                guest.validate_inputs()
                argv = guest.argv(descriptor_paths=True)
                guest.inherit_inputs()
                os.execv(argv[0], argv)
    except (OSError, ValueError) as error:
        print(f"launch-kvm-guest: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
