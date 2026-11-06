#!/usr/bin/env python3
"""Read-only identity gate for a future, separately pinned timesync profile.

This does not install packages, run sysusers, enable a daemon, or set a clock.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

NAME = 'systemd-timesync'
IDENTITY = 996
POLICY_SHA256 = 'd3873e161fa2ba8cee53de2a675b1ed8af9145c7151ba7a2775634c480766079'
POLICY = Path(__file__).absolute().parent.parent / 'configs/host-timesync-sysusers.conf'
MAX_BYTES = 1024 * 1024
MAX_LINE_BYTES = 4096
MAX_RECORDS = 16384
USER = (NAME, 'x', '996', '996', 'systemd Time Synchronization', '/', '/usr/sbin/nologin')
GROUP = (NAME, 'x', '996', '')
_NAME = re.compile(r'[a-zA-Z_][a-zA-Z0-9_-]*\$?\Z')
_ID = re.compile(r'(?:0|[1-9][0-9]{0,9})\Z')


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns, info.st_nlink)


def _directory(path):
    """Open every physical component relative to a held directory descriptor."""
    path = Path(path)
    if '..' in path.parts:
        raise ValueError('parent traversal is prohibited')
    path = path.absolute()
    descriptor = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in path.parts[1:]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _read_at(parent, name, limit=MAX_BYTES):
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=parent)
    try:
        initial = os.fstat(descriptor)
        if not stat.S_ISREG(initial.st_mode) or not 0 < initial.st_size <= limit:
            raise ValueError('input must be a nonempty bounded regular file')
        chunks = []
        remaining = limit + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b''.join(chunks)
        final = os.fstat(descriptor)
        linked = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (len(data) != initial.st_size or len(data) > limit
                or _signature(initial) != _signature(final)
                or _signature(initial) != _signature(linked)):
            raise ValueError('input changed during audit')
        return data, _signature(initial)
    finally:
        os.close(descriptor)


def _policy(path):
    path = Path(path)
    parent = _directory(path.parent)
    try:
        data, _ = _read_at(parent, path.name, 4096)
    finally:
        os.close(parent)
    if hashlib.sha256(data).hexdigest() != POLICY_SHA256:
        raise ValueError('timesync policy authentication failed')


def _number(field):
    if (not _ID.fullmatch(field) or int(field) > 4294967294
            or int(field) == 65535):
        raise ValueError('invalid account numeric identity')
    return int(field)


def _records(data, kind):
    try:
        text = data.decode('ascii')
    except UnicodeDecodeError as error:
        raise ValueError('account file must be ASCII') from error
    if not text.endswith('\n') or '\r' in text or '\x00' in text:
        raise ValueError('invalid account file framing')
    lines = text[:-1].split('\n')
    if len(lines) > MAX_RECORDS:
        raise ValueError('too many account records')
    result = {}
    identities = set()
    for line in lines:
        fields = tuple(line.split(':'))
        if (len(line) > MAX_LINE_BYTES or len(fields) != (7 if kind == 'passwd' else 4)
                or not _NAME.fullmatch(fields[0])):
            raise ValueError('invalid account record')
        number = _number(fields[2])
        if fields[0] in result or number in identities:
            raise ValueError('duplicate account name or numeric identity')
        if any(ord(character) < 32 or ord(character) == 127 for character in line):
            raise ValueError('invalid account record characters')
        if kind == 'passwd':
            _number(fields[3])
            if not fields[5].startswith('/') or not fields[6].startswith('/'):
                raise ValueError('invalid account home or shell')
        elif fields[3] and any(not _NAME.fullmatch(member) for member in fields[3].split(',')):
            raise ValueError('invalid group membership')
        result[fields[0]] = fields
        identities.add(number)
    return result


def _snapshot(root):
    root_fd = _directory(root)
    try:
        etc = os.open('etc', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
    finally:
        os.close(root_fd)
    try:
        passwd, passwd_sig = _read_at(etc, 'passwd')
        group, group_sig = _read_at(etc, 'group')
        users = _records(passwd, 'passwd')
        groups = _records(group, 'group')
        for name, signature in (('passwd', passwd_sig), ('group', group_sig)):
            if _signature(os.stat(name, dir_fd=etc, follow_symlinks=False)) != signature:
                raise ValueError('account files changed during audit')
    finally:
        os.close(etc)
    return users, groups, {'passwd': hashlib.sha256(passwd).hexdigest(),
                           'group': hashlib.sha256(group).hexdigest()}


def _validate(users, groups, phase):
    if phase not in ('before', 'after'):
        raise ValueError('phase must be before or after')
    if phase == 'after' and (users.get(NAME) != USER or groups.get(NAME) != GROUP):
        raise ValueError('required systemd-timesync identity is absent or inexact')
    for name, fields in users.items():
        if phase == 'after' and name == NAME:
            continue
        if name == NAME or IDENTITY in (_number(fields[2]), _number(fields[3])):
            raise ValueError('systemd-timesync name or identity 996 is already reserved')
    for name, fields in groups.items():
        if phase == 'after' and name == NAME:
            continue
        if name == NAME or _number(fields[2]) == IDENTITY:
            raise ValueError('systemd-timesync name or identity 996 is already reserved')
        if NAME in fields[3].split(','):
            raise ValueError('systemd-timesync has unexpected supplementary membership')


def audit(root, phase, policy_path=POLICY):
    """Validate an offline root without modifying it; return nonsecret evidence."""
    _policy(policy_path)
    users, groups, hashes = _snapshot(root)
    _validate(users, groups, phase)
    return {'phase': phase, 'account': NAME, 'uid': IDENTITY, 'gid': IDENTITY,
            'reservation': 'unused' if phase == 'before' else 'exact',
            'sha256': {**hashes, 'sysusers_policy': POLICY_SHA256}}


def compare_accounts(before_root, after_root, policy_path=POLICY):
    """Require exactly the reserved addition and unchanged preexisting tuples."""
    _policy(policy_path)
    before_users, before_groups, _ = _snapshot(before_root)
    after_users, after_groups, _ = _snapshot(after_root)
    _validate(before_users, before_groups, 'before')
    _validate(after_users, after_groups, 'after')
    if (before_users != {name: fields for name, fields in after_users.items() if name != NAME}
            or before_groups != {name: fields for name, fields in after_groups.items() if name != NAME}):
        raise ValueError('preexisting account tuples changed')
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--phase', choices=('before', 'after'), required=True)
    args = parser.parse_args()
    try:
        result = audit(args.root, args.phase)
    except (OSError, ValueError):
        # Never echo paths, account fields, or exception details into build logs.
        print('timesync identity audit failed', file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
