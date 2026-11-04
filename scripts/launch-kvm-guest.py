#!/usr/bin/env python3
"""Prepare or launch an explicit KVM-only AArch64 guest; no acceptance claim.

Supply trusted SHA-256 values for all three inputs on every invocation. The raw
disk is mutable: after a clean guest shutdown, record and review its new hash as
the expected checkpoint for the next launch. Never rehash a running guest disk.
Keep these files on NVMe and prevent concurrent modification during verification
and execution. No fixture download, disk creation, or hardware validation occurs
here. Dry-run verifies inputs without requiring QEMU or /dev/kvm.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys


def positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    for name in ("kernel", "initrd", "disk"):
        result.add_argument(f"--{name}", required=True, type=Path)
        result.add_argument(f"--{name}-sha256", required=True)
    result.add_argument("--memory-mib", required=True, type=positive_int)
    result.add_argument("--cpus", required=True, type=positive_int)
    result.add_argument("--dry-run", action="store_true")
    return result


def verify_file(path, expected):
    if not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
        raise ValueError(f"Invalid SHA-256 for {path}")
    # Check before opening so special files (including FIFOs) cannot block us.
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"Input must be a regular file, not a symlink/device: {path}")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or (info.st_dev, info.st_ino) != (
            opened.st_dev, opened.st_ino
        ):
            raise ValueError(f"Input changed during verification: {path}")
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != expected.lower():
        raise ValueError(f"SHA-256 mismatch: {path}")
    if not info.st_size:
        raise ValueError(f"Input is empty: {path}")
    return {"path": str(path.resolve()), "sha256": digest.hexdigest(),
            "size_bytes": info.st_size}


def prepare(args):
    inputs = {name: verify_file(getattr(args, name), getattr(args, f"{name}_sha256"))
              for name in ("kernel", "initrd", "disk")}
    if any(os.path.samefile(args.disk, path) for path in (args.kernel, args.initrd)):
        raise ValueError("Mutable disk must be separate from kernel and initrd")
    block = {"driver": "raw", "node-name": "guest-disk",
             "file": {"driver": "file", "filename": inputs["disk"]["path"]}}
    argv = [shutil.which("qemu-system-aarch64") or "qemu-system-aarch64",
            "-machine", "virt,gic-version=host", "-accel", "kvm", "-cpu", "host",
            "-m", str(args.memory_mib), "-smp", str(args.cpus),
            "-nodefaults", "-nographic", "-serial", "stdio", "-monitor", "none",
            "-kernel", inputs["kernel"]["path"], "-initrd", inputs["initrd"]["path"],
            "-append", "console=ttyAMA0 rdinit=/init",
            "-blockdev", json.dumps(block, separators=(",", ":")),
            "-device", "virtio-blk-pci,drive=guest-disk",
            "-netdev", "user,id=guestnet,restrict=on",
            "-device", "virtio-net-pci,netdev=guestnet"]
    return {"operation": "dry-run" if args.dry_run else "launch",
            "hardware_acceptance": "pending", "verified_inputs": inputs, "argv": argv}


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
        result = prepare(args)
        if args.dry_run:
            print(json.dumps(result, indent=2))
        else:
            require_launch_access(args)
            os.execv(result["argv"][0], result["argv"])
    except (OSError, ValueError) as error:
        print(f"launch-kvm-guest: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
