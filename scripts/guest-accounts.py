#!/usr/bin/python3
"""Explicit prepare/apply of locked guest accounts in the guarded RAM /etc.

Targets systemd 252 --inline, with no implicit sysusers configuration search:
https://www.freedesktop.org/software/systemd/man/252/systemd-sysusers.html
https://www.freedesktop.org/software/systemd/man/252/sysusers.d.html
No guest is launched and no KVM access is granted by this tool.
"""
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys

BASE = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, BASE / 'configs' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MANAGEMENT = load('a733_accounts_management', 'host-management.py')
IDENTITY = load('a733_guest_identity', 'guest_identity.py')
require = IDENTITY.require
DATABASES = ('passwd', 'shadow', 'group', 'gshadow')
LOCK = '/run/a733-guest-accounts.lock'
DB_LIMIT = 1024 * 1024


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def metadata(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def read_input(path):
    """Check storage on the held FD *before* reading (including atime writes)."""
    path = Path(path)
    require(path.is_absolute(), 'absolute input path required')
    before = MANAGEMENT.secure_path(path, 0o400)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        require(metadata(info) == metadata(before), 'input changed during open')
        require(info.st_dev in {os.stat('/run').st_dev, os.stat(MANAGEMENT.STATE).st_dev},
                'input must reside on RAM or guarded NVMe state')
        data = os.read(fd, IDENTITY.LIMIT + 1)
        require(0 < len(data) <= IDENTITY.LIMIT, 'empty or oversized input')
        require(metadata(os.fstat(fd)) == metadata(info) and
                metadata(path.lstat()) == metadata(info), 'input changed during read')
        return data
    finally:
        os.close(fd)


def validate_database_metadata(info, etc_device, name, shadow_gid=0):
    sensitive = name in ('shadow', 'gshadow')
    require(name in DATABASES and stat.S_ISREG(info.st_mode) and info.st_uid == 0 and
            info.st_nlink == 1 and info.st_dev == etc_device and
            stat.S_IMODE(info.st_mode) in ({0o600, 0o640} if sensitive else {0o600, 0o644}) and
            info.st_gid in ({0, shadow_gid} if sensitive else {0}),
            'unsafe account database metadata')


def read_database(name, etc_device, shadow_gid=0):
    """Bounded held-FD reads, allowing Debian's root:shadow 0640 convention."""
    path = Path('/etc') / name
    MANAGEMENT.secure_path(path.parent, directory=True)
    before = path.lstat()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        require(metadata(info) == metadata(before), 'account database changed during open')
        validate_database_metadata(info, etc_device, name, shadow_gid)
        data = os.read(fd, DB_LIMIT + 1)
        require(0 < len(data) <= DB_LIMIT, 'empty or oversized account database')
        require(metadata(os.fstat(fd)) == metadata(info) and
                metadata(path.lstat()) == metadata(info), 'account database changed during read')
        return data
    finally:
        os.close(fd)


def snapshot():
    """Runtime guard proves /etc is a fixed RAM-upper overlay first.

    A root:root group database establishes the trusted local shadow group ID.
    No shadow/gshadow bytes are copied to plans or receipts.
    """
    etc_device = os.stat('/etc').st_dev
    group = read_database('group', etc_device)
    groups = parse_database(group, 4, 2)
    shadow_gid = int(groups['shadow'][2]) if 'shadow' in groups else 0
    return {'group': group, 'passwd': read_database('passwd', etc_device),
            'shadow': read_database('shadow', etc_device, shadow_gid),
            'gshadow': read_database('gshadow', etc_device, shadow_gid)}


def parse_database(data, count, numeric=None):
    require(isinstance(data, bytes) and 0 < len(data) <= DB_LIMIT, 'invalid account database size')
    text = data.decode('utf-8')
    require(text.endswith('\n') and '\x00' not in text, 'malformed account database')
    rows = {}
    for line in text.splitlines():
        fields = line.split(':')
        require(len(fields) == count and fields[0] and fields[0] not in rows,
                'malformed or duplicate account row')
        for index in (() if numeric is None else (numeric,) if type(numeric) is int else numeric):
            require(len(fields[index]) <= 10 and
                    re.fullmatch(r'(?:0|[1-9][0-9]*)', fields[index]) is not None and
                    int(fields[index]) <= 4294967294, 'invalid account number')
        rows[fields[0]] = fields
    return rows


def validate_accounts(config, data, complete=False):
    """Reject local name/ID collisions and every supplementary membership.

    sysusers 252 accesses these local databases directly. This policy parses
    the same bounded files rather than using mutable NSS configuration.
    """
    users = parse_database(data['passwd'], 7, (2, 3))
    shadows = parse_database(data['shadow'], 9)
    groups = parse_database(data['group'], 4, 2)
    gshadows = parse_database(data['gshadow'], 4)
    for guest in config['guests']:
        name = 'a733-vm-' + guest['name']
        uid, gid = str(guest['uid']), str(guest['gid'])
        present = [name in rows for rows in (users, shadows, groups, gshadows)]
        require(all(present) or not any(present), 'partial existing guest identity refused')
        require(not complete or all(present), 'guest identity missing after sysusers')
        require(all(row[0] == name or row[2] != uid for row in users.values()), 'UID collision')
        require(all(row[0] == name or row[2] != gid for row in groups.values()), 'GID collision')
        # Another user's primary group must never give it guest disk access.
        require(all(row[0] == name or row[3] != gid for row in users.values()),
                'another account uses guest primary GID')
        for row in groups.values():
            require(name not in row[3].split(','), 'supplementary membership refused')
        for row in gshadows.values():
            require(name not in row[2].split(',') and name not in row[3].split(','),
                    'supplementary group administration/membership refused')
        if all(present):
            require(users[name] == [name, 'x', uid, gid, 'A733 guest ' + guest['name'],
                                    '/nonexistent', '/usr/sbin/nologin'],
                    'existing guest account differs')
            require(re.fullmatch(r'[!*]+', shadows[name][1]) is not None,
                    'guest password must be locked without a stored hash')
            require(groups[name] == [name, 'x', gid, ''] and
                    re.fullmatch(r'[!*]+', gshadows[name][1]) is not None and
                    gshadows[name][2:] == ['', ''], 'guest group must be locked and empty')
    return all('a733-vm-' + guest['name'] in users for guest in config['guests'])


def validate_preserved(config, before, after):
    """sysusers may change requested rows only; protect root and all others."""
    managed = {'a733-vm-' + guest['name'] for guest in config['guests']}
    for name, count, numeric in (('passwd', 7, (2, 3)), ('shadow', 9, None),
                                 ('group', 4, 2), ('gshadow', 4, None)):
        old = parse_database(before[name], count, numeric)
        new = parse_database(after[name], count, numeric)
        require({key: row for key, row in old.items() if key not in managed} ==
                {key: row for key, row in new.items() if key not in managed},
                'unrelated account database rows changed')


def make_plan(config_data, data):
    config = IDENTITY.validate_config(config_data)
    validate_accounts(config, data)
    return {'schema': 1, 'operation': 'a733-guest-accounts', 'config_sha256': digest(config_data),
            'database_sha256': {name: digest(data[name]) for name in DATABASES},
            'sysusers_lines': IDENTITY.render(config)}


def prepare(config_path):
    MANAGEMENT.runtime_guard()
    return make_plan(read_input(config_path), snapshot())


def validate_lock(info, path_info, run_device):
    require(stat.S_ISREG(info.st_mode) and info.st_uid == info.st_gid == 0 and
            stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1 and
            info.st_dev == run_device, 'unsafe account activation lock')
    require(metadata(path_info) == metadata(info), 'account lock changed')


def apply(config_path, plan_path):
    receipt = {'schema': 1, 'operation': 'a733-guest-accounts',
               'success': False, 'accounts_may_be_modified': False}
    fd = None
    try:
        MANAGEMENT.runtime_guard()
        # Lock metadata and placement are validated on the held descriptor.
        fd = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        info = os.fstat(fd)
        validate_lock(info, os.lstat(LOCK), os.stat('/run').st_dev)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        config_data, plan_data = read_input(config_path), read_input(plan_path)
        supplied = json.loads(plan_data, object_pairs_hook=IDENTITY.unique_pairs)
        current = make_plan(config_data, snapshot())
        require(canonical(supplied) == canonical(current), 'stale or mismatched account plan')
        receipt['plan_sha256'] = digest(canonical(current))
        MANAGEMENT.runtime_guard()
        require(make_plan(read_input(config_path), snapshot()) == current,
                'account state changed before apply')
        config = IDENTITY.validate_config(config_data)
        before = snapshot()
        require(make_plan(config_data, before) == current, 'account state changed before sysusers')
        if not validate_accounts(config, before, complete=False):
            receipt['accounts_may_be_modified'] = True
            sys.stderr.write(canonical(receipt).decode())
            sys.stderr.flush()  # Intent remains visible even if later killed.
            subprocess.run(['/usr/bin/systemd-sysusers', '--inline', *current['sysusers_lines']],
                           check=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=30,
                           env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'})
        MANAGEMENT.runtime_guard()
        after = snapshot()
        validate_accounts(config, after, complete=True)
        validate_preserved(config, before, after)
        receipt['success'] = True
    except (OSError, ValueError, UnicodeError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        receipt['error'] = type(error).__name__
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except (OSError, KeyboardInterrupt) as error:
                receipt['success'] = False
                receipt['cleanup_error'] = type(error).__name__
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('prepare').add_argument('config')
    applying = commands.add_parser('apply')
    applying.add_argument('config')
    applying.add_argument('--plan', required=True)
    args = parser.parse_args()
    os.umask(0o077)
    if args.command == 'apply':
        def interrupted(_signum, _frame):
            raise KeyboardInterrupt
        previous = signal.signal(signal.SIGTERM, interrupted)
        try:
            result = apply(args.config, args.plan)
        finally:
            signal.signal(signal.SIGTERM, previous)
        print(canonical(result).decode(), end='')
        return 0 if result['success'] else 1
    print(canonical(prepare(args.config)).decode(), end='')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, UnicodeError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        print(json.dumps({'success': False, 'accounts_may_be_modified': True,
                          'error': type(error).__name__, 'receipt': 'unavailable; inspect preceding intent'}), file=sys.stderr)
        sys.exit(1)
