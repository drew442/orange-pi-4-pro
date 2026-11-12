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
FDINFO_LIMIT = 16384


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


def fd_mount_id(data):
    require(isinstance(data, str) and 0 < len(data) <= FDINFO_LIMIT, 'invalid fdinfo size')
    values = []
    for line in data.splitlines():
        require(':' in line, 'malformed fdinfo')
        key, value = line.split(':', 1)
        if key == 'mnt_id':
            value = value.strip()
            require(re.fullmatch(r'[1-9][0-9]{0,19}', value) is not None,
                    'invalid fd mount identity')
            values.append(int(value))
    require(len(values) == 1, 'missing or ambiguous fd mount identity')
    return values[0]


def held_mount_id(fd):
    with open(f'/proc/self/fdinfo/{fd}', 'r', encoding='ascii') as stream:
        return fd_mount_id(stream.read(FDINFO_LIMIT + 1))


def etc_mount_id(data):
    """Locate the same overlay mount already authenticated by runtime_guard."""
    MANAGEMENT.validate_mounts(MANAGEMENT.mount_table(data))
    rows = [line.split() for line in data.splitlines() if line.split()[4] == '/etc']
    require(len(rows) == 1 and re.fullmatch(r'[1-9][0-9]{0,19}', rows[0][0]) is not None,
            'missing or ambiguous etc mount identity')
    return int(rows[0][0])


def validate_database_metadata(info, name, shadow_gid=0):
    sensitive = name in ('shadow', 'gshadow')
    require(name in DATABASES and stat.S_ISREG(info.st_mode) and info.st_uid == 0 and
            info.st_nlink == 1 and
            stat.S_IMODE(info.st_mode) in ({0o600, 0o640} if sensitive else {0o600, 0o644}) and
            info.st_gid in ({0, shadow_gid} if sensitive else {0}),
            'unsafe account database metadata')


def validate_etc(fd, info, mount_id):
    require(metadata(os.fstat(fd)) == metadata(info) and
            metadata(MANAGEMENT.secure_path('/etc', directory=True)) == metadata(info),
            'account database parent changed')
    require(held_mount_id(fd) == mount_id and
            etc_mount_id(Path('/proc/self/mountinfo').read_text()) == mount_id,
            'account database parent mount changed')


def read_database(name, etc_fd, etc_info, mount_id, shadow_gid=0):
    """Read relative to held /etc; OverlayFS file st_dev may differ from /etc.

    A held file must belong to the authenticated overlay mount before any bytes
    are read. A bind over a file has a different mount ID even on the same fs.
    """
    require(name in DATABASES, 'unknown account database')
    validate_etc(etc_fd, etc_info, mount_id)
    before = os.stat(name, dir_fd=etc_fd, follow_symlinks=False)
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=etc_fd)
    try:
        info = os.fstat(fd)
        require(metadata(info) == metadata(before), 'account database changed during open')
        validate_database_metadata(info, name, shadow_gid)
        require(held_mount_id(fd) == mount_id, 'foreign account database mount')
        validate_etc(etc_fd, etc_info, mount_id)
        require(0 < info.st_size <= DB_LIMIT, 'empty or oversized account database')
        data = os.read(fd, DB_LIMIT + 1)
        require(0 < len(data) <= DB_LIMIT, 'empty or oversized account database')
        require(metadata(os.fstat(fd)) == metadata(info) and
                metadata(os.stat(name, dir_fd=etc_fd, follow_symlinks=False)) == metadata(info),
                'account database changed during read')
        require(held_mount_id(fd) == mount_id, 'account database mount changed')
        validate_etc(etc_fd, etc_info, mount_id)
        return data
    finally:
        os.close(fd)


def snapshot():
    """Callers run runtime_guard first to prove the fixed RAM-upper overlay.

    A root:root group database establishes the trusted local shadow group ID.
    No shadow/gshadow bytes are copied to plans or receipts.
    """
    before = MANAGEMENT.secure_path('/etc', directory=True)
    fd = os.open('/etc', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        require(metadata(info) == metadata(before), 'account database parent changed during open')
        mount_id = etc_mount_id(Path('/proc/self/mountinfo').read_text())
        validate_etc(fd, info, mount_id)
        group = read_database('group', fd, info, mount_id)
        groups = parse_database(group, 4, 2)
        shadow_gid = int(groups['shadow'][2]) if 'shadow' in groups else 0
        return {'group': group, 'passwd': read_database('passwd', fd, info, mount_id),
                'shadow': read_database('shadow', fd, info, mount_id, shadow_gid),
                'gshadow': read_database('gshadow', fd, info, mount_id, shadow_gid)}
    finally:
        os.close(fd)


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


_BOOTSTRAP_CONTEXT = None


def _bootstrap_context():
    # Load only after this module is complete: context -> policy -> enrollment
    # imports accounts too. Context authenticates and caches canonical ADMISSION.
    global _BOOTSTRAP_CONTEXT
    if _BOOTSTRAP_CONTEXT is None:
        spec = importlib.util.spec_from_file_location(
            'a733_accounts_context', BASE / 'scripts/guest-lifecycle-context.py')
        context = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(context)
        _BOOTSTRAP_CONTEXT = context
    return _BOOTSTRAP_CONTEXT


def bootstrap_check(owner):
    """Check a borrowed bootstrap capability without acquiring another lease."""
    require(os.geteuid() == 0, 'bootstrap accounts require root')
    context = _bootstrap_context()
    admission = context.ADMISSION
    require(type(owner) is admission.ClosureOwner, 'typed bootstrap closure owner required')
    require(type(owner.admission) is admission.Admission, 'typed admission required')
    require(owner.reason == 'bootstrap', 'bootstrap closure required')
    lease = owner.lease
    require(type(lease) is admission.Lease and lease.admission is owner.admission
            and lease.exclusive is True, 'matching exclusive bootstrap lease required')
    lease.recheck()
    admission._held_lock(lease.fd, 'WRITE', os.getpid())
    context.host_identity(owner.admission.boot_id)
    with owner.admission._gate():
        record = owner._matching()
        require(record['boot_id'] == owner.admission.boot_id, 'bootstrap boot differs')


def apply(config_path, plan_path):
    """Manual CLI account preparation retains authenticated input file checks."""
    return _apply(lambda: (read_input(config_path), read_input(plan_path)), lambda: None)


def apply_bootstrap(config_data, plan, owner):
    """Apply one exact <=16-identity batch under a borrowed bootstrap owner.

    The Python caller supplies exact config bytes and a fresh make_plan result
    against the current databases. Subsequent batches need fresh plans. All other
    account rows remain protected. This never opens admission or closes its lease.
    """
    # Freeze the caller's plan before any mutation; never trust a mutable plan
    # through sysusers. The core still validates it against current live state.
    def inputs():
        require(type(config_data) is bytes, 'exact bootstrap config bytes required')
        return config_data, plan_data
    plan_data = canonical(plan)
    return _apply(inputs, lambda: bootstrap_check(owner))


def _apply(inputs, check):
    receipt = {'schema': 1, 'operation': 'a733-guest-accounts',
               'success': False, 'accounts_may_be_modified': False}
    fd = None
    try:
        check()
        MANAGEMENT.runtime_guard()
        # Lock metadata and placement are validated on the held descriptor.
        fd = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        info = os.fstat(fd)
        validate_lock(info, os.lstat(LOCK), os.stat('/run').st_dev)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        check()
        config_data, plan_data = inputs()
        supplied = json.loads(plan_data, object_pairs_hook=IDENTITY.unique_pairs)
        current = make_plan(config_data, snapshot())
        require(canonical(supplied) == canonical(current), 'stale or mismatched account plan')
        receipt['plan_sha256'] = digest(canonical(current))
        MANAGEMENT.runtime_guard()
        require(make_plan(inputs()[0], snapshot()) == current,
                'account state changed before apply')
        config = IDENTITY.validate_config(config_data)
        before = snapshot()
        require(make_plan(config_data, before) == current, 'account state changed before sysusers')
        check()
        if not validate_accounts(config, before, complete=False):
            receipt['accounts_may_be_modified'] = True
            sys.stderr.write(canonical(receipt).decode())
            sys.stderr.flush()  # Intent remains visible even if later killed.
            check()
            subprocess.run(['/usr/bin/systemd-sysusers', '--inline', *current['sysusers_lines']],
                           check=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=30,
                           env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'})
        check()
        MANAGEMENT.runtime_guard()
        after = snapshot()
        validate_accounts(config, after, complete=True)
        validate_preserved(config, before, after)
        check()
        receipt['success'] = True
    except (OSError, ValueError, RuntimeError, UnicodeError, subprocess.SubprocessError, KeyboardInterrupt) as error:
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
