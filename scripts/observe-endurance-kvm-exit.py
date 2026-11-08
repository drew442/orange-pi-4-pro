#!/usr/bin/env python3
"""Observe the fixed endurance unit through the authenticated first-run observer."""
import argparse
import hashlib
import os
from pathlib import Path
import stat
import sys
import types

BASE_NAME = 'observe-first-kvm-exit.py'
BASE_SHA256 = '334f79c44ca9033556f9a7752d837ada7f37c4c34a7c7a51798807e195faefb4'
UNIT = b'a733-guest-endurance.service'
TIMEOUTS = {'smoke': 300.0, 'release': 86700.0}
MAX_SOURCE_BYTES = 65536


def require_protected_directory(fd):
    info = os.fstat(fd)
    if not (stat.S_ISDIR(info.st_mode) and info.st_uid == 0
            and not info.st_mode & 0o022):
        raise RuntimeError('observer source ancestors must be root-owned protected directories')


def read_protected_source(path):
    """Read only a root-owned immutable regular file through held safe ancestors."""
    path = Path(os.path.abspath(path))
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory = os.open('/', flags)
    try:
        require_protected_directory(directory)
        for part in path.parts[1:-1]:
            following = os.open(part, flags, dir_fd=directory)
            os.close(directory)
            directory = following
            require_protected_directory(directory)
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=directory)
        try:
            info = os.fstat(descriptor)
            if not (stat.S_ISREG(info.st_mode) and info.st_uid == 0
                    and not info.st_mode & 0o222):
                raise RuntimeError('observer source must be a root-owned read-only regular file')
            with os.fdopen(descriptor, 'rb', closefd=False) as source:
                data = source.read(MAX_SOURCE_BYTES + 1)
            if len(data) > MAX_SOURCE_BYTES:
                raise RuntimeError('observer source exceeds size bound')
            return data
        finally:
            os.close(descriptor)
    finally:
        os.close(directory)


def load_observer():
    path = Path(__file__).absolute().with_name(BASE_NAME)
    source = read_protected_source(path)
    if hashlib.sha256(source).hexdigest() != BASE_SHA256:
        raise RuntimeError('first-run observer SHA-256 mismatch; refusing execution')
    # Execute exactly the authenticated bytes, without a loader reopening the file
    # or reading/writing a bytecode cache. Its __main__ block must not run here.
    observer = types.ModuleType('_authenticated_first_kvm_exit_observer')
    observer.__file__ = str(path)
    exec(compile(source, str(path), 'exec'), observer.__dict__)
    return observer


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', required=True, choices=tuple(TIMEOUTS))
    args = parser.parse_args(argv)
    try:
        observer = load_observer()
        observer.UNIT = UNIT
        observer.TIMEOUT = TIMEOUTS[args.mode]
        original_argv = sys.argv
        try:
            # The proven entry point accepts no arguments. Do not let wrapper
            # arguments reach it, and restore process state even on exceptions.
            sys.argv = [str(observer.__file__)]
            return observer.main()
        finally:
            sys.argv = original_argv
    except Exception as exc:
        print(f'endurance observer failed: {exc}', file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
