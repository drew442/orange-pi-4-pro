"""Internal build-only account-date policy; deliberately no mutating CLI.

Call only on an authenticated, private generic build clone, before its BOM is
recorded. The caller must enforce the frozen-builder namespace and exclude a
running/installed OS; directory checks here do not establish that provenance.
Both files are validated before writing. Writes use held descriptors, preserve
metadata, and detect observed changes, but are NOT a two-file transaction: an
I/O failure or concurrent privileged writer may leave a partially updated clone.
Discard that clone on any failure. No password fields are returned or logged.
"""
import fcntl
import os
from pathlib import Path
import re
import stat
from contextlib import ExitStack

# From retained baseline 98d6a57b... and time 0ead... archives (2026-11-07).
# Dates are the existing authenticated profile dates, not the archive mtime epoch.
BASE_NAMES = tuple(('root daemon bin sys sync games man lp mail news uucp proxy '
                    'www-data backup list irc _apt nobody systemd-network uuidd '
                    'messagebus systemd-resolve sshd').split())
BASE_DAY = b'20763'  # 1793923200 / 86400
TIME_DAY = b'20764'  # 1794009600 / 86400
TIME_PASSWD = b'systemd-timesync:x:996:996:systemd Time Synchronization:/:/usr/sbin/nologin'
LIMIT = 8192
MARKERS = frozenset((b'!', b'*', b'!!', b'!*'))


def _require(value, message):
    if not value:
        raise ValueError(message)


def _policy(profile, name):
    _require(profile in ('baseline', 'time'), 'unknown account-date profile')
    _require(name in ('shadow', 'shadow-'), 'unknown account file')
    names = BASE_NAMES if name == 'shadow' or profile == 'time' else BASE_NAMES[:-1]
    result = {n.encode(): BASE_DAY for n in names}
    if profile == 'time' and name == 'shadow':
        result[b'systemd-timesync'] = TIME_DAY
    return result


def _normalize_bytes(data, profile, name):
    policy = _policy(profile, name)
    _require(0 < len(data) <= LIMIT and data.endswith(b'\n'), 'invalid account file bounds/termination')
    seen, output = set(), []
    for row in data[:-1].split(b'\n'):
        fields = row.split(b':')
        _require(len(fields) == 9 and all(32 <= c < 127 for c in row), 'malformed account row')
        user = fields[0]
        _require(user in policy and user not in seen, 'unknown or duplicate account')
        seen.add(user)
        _require(fields[1] in MARKERS, 'public literal locked password marker required')
        _require(re.fullmatch(rb'(0|[1-9][0-9]{0,9})', fields[2]) is not None,
                 'invalid account date')
        _require(all(re.fullmatch(rb'[0-9]*', f) is not None for f in fields[3:]),
                 'invalid account aging fields')
        fields[2] = policy[user]
        output.append(b':'.join(fields) + b'\n')
    _require(seen == set(policy), 'incomplete account set')
    return b''.join(output)


def _identity(s):
    return (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_gid, s.st_nlink,
            s.st_size, s.st_mtime_ns, s.st_ctime_ns)


def _xattrs(fd):
    return {n: os.getxattr(fd, n) for n in os.listxattr(fd)}


def _read(fd, info):
    data = os.pread(fd, LIMIT + 1, 0)
    _require(len(data) == info.st_size and _identity(os.fstat(fd)) == _identity(info),
             'account file changed during read')
    return data


def _open(stack, name, flags, parent=None):
    fd = os.open(name, flags | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
    stack.callback(os.close, fd)
    return fd


def normalize(root, profile):
    """Normalize the two closed shadow account sets; return changed basenames.

    Requires namespace UID 0, root-owned physical build root (0700 or 0755),
    physical safe ancestors and etc, and single-link 0:42 mode-0640 files.
    Preserves every non-date byte, row order, uid/gid/mode/mtime and xattrs.
    No-op leaves inode/ctime untouched. No arbitrary epoch or path selection.
    """
    _policy(profile, 'shadow')
    root = os.fspath(root)
    _require(isinstance(root, str) and root.startswith('/') and root == os.path.normpath(root)
             and root != '/' and not root.startswith('//'), 'physical absolute build root required')
    _require(os.geteuid() == 0, 'namespace root required')
    with ExitStack() as stack:
        directory_flags = os.O_RDONLY | os.O_DIRECTORY
        current = _open(stack, '/', directory_flags)
        chain = []
        for part in Path(root).parts[1:] + ('etc',):
            child = _open(stack, part, directory_flags, current)
            info = os.fstat(child)
            _require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0,
                     'root-owned physical ancestors required')
            # A root-owned sticky /tmp is safe to traverse, but not as build root/etc.
            _require(not info.st_mode & 0o022 or info.st_mode & stat.S_ISVTX,
                     'unsafe writable directory')
            chain.append((current, part, child, info))
            current = child
        build_info = os.fstat(chain[-2][2])
        _require((build_info.st_dev, build_info.st_ino) != (os.stat('/').st_dev, os.stat('/').st_ino),
                 'live root forbidden')
        _require(stat.S_IMODE(build_info.st_mode) in (0o700, 0o755)
                 and not os.fstat(current).st_mode & 0o022, 'private build directories required')
        files = []
        for name in ('shadow', 'shadow-'):
            fd = _open(stack, name, os.O_RDWR | os.O_NOATIME, current)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            info = os.fstat(fd)
            _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                     and (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (0, 42, 0o640)
                     and 0 < info.st_size <= LIMIT, 'unexpected account file metadata')
            data = _read(fd, info)
            files.append((name, fd, info, data, _normalize_bytes(data, profile, name), _xattrs(fd)))
        identity_file = None
        if profile == 'time':
            fd = _open(stack, 'passwd', os.O_RDONLY | os.O_NOATIME, current)
            info = os.fstat(fd)
            _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == 0
                     and not info.st_mode & 0o022 and 0 < info.st_size <= LIMIT,
                     'unexpected passwd metadata')
            passwd_data = _read(fd, info)
            identity_file = (fd, info, passwd_data, _xattrs(fd))
            rows = passwd_data.splitlines()
            matches = [r for r in rows if r.split(b':', 1)[0] == b'systemd-timesync']
            _require(matches == [TIME_PASSWD], 'fixed time account identity required')
        def check():
            for parent, part, child, original in chain:
                s = os.stat(part, dir_fd=parent, follow_symlinks=False)
                def directory_identity(value):
                    return (value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid)
                _require(directory_identity(s) == directory_identity(original)
                         == directory_identity(os.fstat(child)), 'build directory changed')
            if identity_file is not None:
                fd, info, data, attrs = identity_file
                _require(_identity(os.stat('passwd', dir_fd=current, follow_symlinks=False))
                         == _identity(info) == _identity(os.fstat(fd))
                         and _read(fd, info) == data and _xattrs(fd) == attrs,
                         'time account identity file changed')
            for name, fd, info, data, _, attrs in files:
                _require(_identity(os.stat(name, dir_fd=current, follow_symlinks=False))
                         == _identity(info) == _identity(os.fstat(fd))
                         and _read(fd, info) == data and _xattrs(fd) == attrs,
                         'account file changed before write')
        check()  # Both files must pass before either file is modified.
        changed = []
        for index, (name, fd, info, data, output, attrs) in enumerate(files):
            check()
            if data == output:
                continue
            offset = 0
            while offset < len(output):
                count = os.pwrite(fd, output[offset:], offset)
                _require(count > 0, 'short account file write')
                offset += count
            os.ftruncate(fd, len(output))
            os.utime(fd, ns=(info.st_atime_ns, info.st_mtime_ns))
            os.fsync(fd)
            updated = os.fstat(fd)
            _require((updated.st_uid, updated.st_gid, updated.st_mode, updated.st_nlink,
                      updated.st_mtime_ns) == (info.st_uid, info.st_gid, info.st_mode,
                                              info.st_nlink, info.st_mtime_ns)
                     and _xattrs(fd) == attrs and _read(fd, updated) == output,
                     'account write verification failed; discard build clone')
            files[index] = (name, fd, updated, output, output, attrs)
            changed.append(name)
        check()
        return tuple(changed)
