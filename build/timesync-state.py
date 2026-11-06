#!/usr/bin/env python3
"""Read-only NVMe clock-state compatibility audit for a future timesync profile.

This guard neither initializes state nor reads account databases. It never sets a
clock. Run against private, quiescent build/recovery state, not a live daemon.
"""

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import uuid

IDENTITY = 996
MARKER_BYTES = 51  # a733-state-v1\n + canonical UUID + \n
MAX_COMPONENTS = 128


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_size, info.st_nlink, info.st_atime_ns, info.st_mtime_ns, info.st_ctime_ns)


def _metadata(info):
    return {'uid': info.st_uid, 'gid': info.st_gid,
            'mode': format(stat.S_IMODE(info.st_mode), '04o'),
            'size': info.st_size, 'nlink': info.st_nlink,
            'device': info.st_dev, 'inode': info.st_ino}


def _owned(info, owner, mode=None, directory=True):
    valid_type = stat.S_ISDIR if directory else stat.S_ISREG
    if (not valid_type(info.st_mode) or (info.st_uid, info.st_gid) != (owner, owner)
            or info.st_mode & 0o7022
            or (mode is not None and stat.S_IMODE(info.st_mode) != mode)):
        raise ValueError('unsafe state ownership, type or mode')
    if not directory and info.st_nlink != 1:
        raise ValueError('aliased state file')


def _readable_pin(pinned, expected):
    """Reopen a verified regular inode through Linux's own procfs fd link.

    Reopening the original pathname, even with O_NOFOLLOW, could race with a
    replacement device. Verify procfs before following its kernel fd link; no
    untrusted state pathname participates in the readable open.
    """
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        fstatfs = libc.fstatfs
    except AttributeError as error:
        raise ValueError('procfs verification is unavailable') from error
    fstatfs.argtypes = [ctypes.c_int, ctypes.c_void_p]
    fstatfs.restype = ctypes.c_int
    descriptors = []
    try:
        parent = None
        for component in ('/proc', str(os.getpid()), 'fd'):
            descriptor = os.open(component, os.O_PATH | os.O_DIRECTORY |
                                 os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
            descriptors.append(descriptor)
            # Linux struct statfs begins with a native signed long f_type. The
            # oversized aligned buffer accommodates either supported ABI.
            buffer = (ctypes.c_long * 128)()
            if fstatfs(descriptor, ctypes.byref(buffer)) != 0:
                raise OSError(ctypes.get_errno(), 'cannot verify procfs')
            if buffer[0] != 0x9fa0:  # PROC_SUPER_MAGIC, linux/magic.h
                raise ValueError('verified procfs is required')
            if parent is None and os.readlink('self', dir_fd=descriptor) != str(os.getpid()):
                raise ValueError('procfs PID namespace does not match')
            parent = descriptor
        readable = os.open(str(pinned), os.O_RDONLY | os.O_NONBLOCK |
                           os.O_NOATIME | os.O_CLOEXEC, dir_fd=parent)
        try:
            if _signature(os.fstat(readable)) != _signature(expected):
                raise ValueError('pinned marker changed during audit')
        except BaseException:
            os.close(readable)
            raise
        return readable
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def audit(state_root, require_clock=False):
    """Validate physical state without writes; return only nonsecret evidence.

    O_NOATIME is mandatory for data/directory reads. Insufficient ownership or
    capabilities fail closed rather than allowing a read to update access time.
    Every opened component remains held and is checked again before returning.
    """
    path = Path(state_root)
    if '..' in path.parts:
        raise ValueError('parent traversal is prohibited')
    path = path.absolute()
    if len(path.parts) > MAX_COMPONENTS:
        raise ValueError('state path is too deep')
    held = []
    absent = []

    def open_at(parent, name, flags):
        fd = os.open(name, flags | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
        held.append((fd, parent, name, os.fstat(fd)))
        return fd

    try:
        current = open_at(None, '/', os.O_PATH | os.O_DIRECTORY)
        for component in path.parts[1:]:
            current = open_at(current, component, os.O_PATH | os.O_DIRECTORY)
        root = current
        _owned(os.fstat(root), 0)
        if stat.S_IMODE(os.fstat(root).st_mode) & 0o700 != 0o700:
            raise ValueError('state root requires owner access')
        marker = open_at(root, '.a733-state', os.O_PATH)
        marker_info = os.fstat(marker)
        _owned(marker_info, 0, directory=False)
        if marker_info.st_mode & 0o111 or marker_info.st_size != MARKER_BYTES:
            raise ValueError('invalid state marker metadata')
        readable = _readable_pin(marker, marker_info)
        held.append((readable, root, '.a733-state', marker_info))
        data = os.read(readable, MARKER_BYTES + 1)
        try:
            tag, identity, trailing = data.decode('ascii').split('\n')
            parsed = uuid.UUID(identity)
        except (UnicodeError, ValueError) as error:
            raise ValueError('invalid state marker') from error
        if (tag != 'a733-state-v1' or trailing or not parsed.int
                or str(parsed) != identity or len(data) != MARKER_BYTES):
            raise ValueError('invalid state marker')
        for component in ('var', 'lib', 'systemd'):
            current = open_at(current, component, os.O_PATH | os.O_DIRECTORY)
            _owned(os.fstat(current), 0)
        result = {'state_uuid': identity,
                  'marker_sha256': hashlib.sha256(data).hexdigest(),
                  'status': 'uninitialized', 'clock_mtime_ns': None,
                  'metadata': {'state_root': _metadata(os.fstat(root)),
                               'marker': _metadata(marker_info)}}
        try:
            timesync = open_at(current, 'timesync', os.O_RDONLY | os.O_DIRECTORY | os.O_NOATIME)
        except FileNotFoundError:
            absent.append((current, 'timesync'))
            if require_clock:
                raise ValueError('required timesync directory is absent')
        else:
            _owned(os.fstat(timesync), IDENTITY, 0o755)
            result['metadata']['timesync'] = _metadata(os.fstat(timesync))
            # Stop immediately on an unexpected child; never enumerate unbounded input.
            with os.scandir(timesync) as entries:
                for entry in entries:
                    if entry.name != 'clock':
                        raise ValueError('unexpected timesync state child')
            try:
                clock = open_at(timesync, 'clock', os.O_PATH)
            except FileNotFoundError:
                absent.append((timesync, 'clock'))
                if require_clock:
                    raise ValueError('required saved clock is absent')
            else:
                info = os.fstat(clock)
                # v252 timesyncd-manager.c:1097 calls touch_file(); fs-util.c:346
                # creates a new empty file with 0644. Its mtime is the saved time.
                _owned(info, IDENTITY, 0o644, directory=False)
                if info.st_size != 0 or info.st_mtime_ns < 0:
                    raise ValueError('invalid saved clock metadata')
                result['status'] = 'initialized'
                result['clock_mtime_ns'] = info.st_mtime_ns
                result['metadata']['clock'] = _metadata(info)
        for parent, name in absent:
            try:
                os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise ValueError('state changed during audit')
        for fd, parent, name, initial in reversed(held):
            if (_signature(initial) != _signature(os.fstat(fd))
                    or _signature(initial) != _signature(os.stat(
                        name, dir_fd=parent, follow_symlinks=False))):
                raise ValueError('state changed during audit')
        return result
    finally:
        for fd, _, _, _ in reversed(held):
            os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('state_root', type=Path)
    parser.add_argument('--require-clock', action='store_true')
    args = parser.parse_args()
    try:
        result = audit(args.state_root, args.require_clock)
    except (OSError, ValueError):
        print('timesync state audit failed', file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
