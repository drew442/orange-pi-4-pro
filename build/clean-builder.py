#!/usr/bin/env python3
"""Extract the pinned AMD64 seed and prove isolation; no package installation.

A passing receipt covers only seed extraction and the namespace probe. It does
not establish a complete clean builder, reproducibility, or A733 acceptance.
"""
import argparse
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import shutil
import signal
import stat
import subprocess
import sys
import tarfile

REPO = Path(__file__).resolve().parents[1]
WORKSPACE = Path('/var/lib/a733-clean-builder')
LOCK = 'configs/builder-base-20261106.lock.json'
FILES = (LOCK, 'build/clean-builder.py', 'build/uboot-replay.py',
         'configs/builder-packages.lock.tsv', 'configs/builder-replay-packages-20261106.lock.tsv')
SEED = {'bytes': 29832285, 'sha256': '242cd8898b33ea806ef5f13b1076ed7c76f9f989d18384452f7166692438ff1a'}
UID_BASE, UID_COUNT = 1048576, 65536
CAPABILITIES = ('CAP_SYS_ADMIN', 'CAP_SYS_MODULE', 'CAP_SYS_RAWIO', 'CAP_SYS_BOOT',
                'CAP_SYS_TIME', 'CAP_MKNOD', 'CAP_NET_ADMIN')
PROHIBITED = sum(1 << bit for bit in (21, 16, 17, 22, 25, 27, 12))
ENV = {'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'LC_ALL': 'C', 'TZ': 'UTC',
       'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_OPTIONAL_LOCKS': '0'}
PROBE = b'''#!/bin/sh
set -eu
emit() { printf 'A733_BEGIN %s\\n' "$1"; cat "$2"; printf 'A733_END %s\\n' "$1"; }
emit uid_map /proc/self/uid_map
emit gid_map /proc/self/gid_map
emit status /proc/self/status
emit interfaces /proc/net/dev
emit routes /proc/net/route
emit routes6 /proc/net/ipv6_route
printf 'A733_BEGIN devices\\n'
find /dev -type b -print
for p in /dev/mem /dev/kmem /dev/port /dev/kvm /dev/nvme*; do
    if [ -e "$p" ] || [ -L "$p" ]; then printf '%s\\n' "$p"; fi
done
printf 'A733_END devices\\n'
printf 'A733_BEGIN firmware\\n'
[ -d /sys/firmware ]
find /sys/firmware -mindepth 1 -print
printf 'A733_END firmware\\n'
printf 'A733_BEGIN firmware_mount\\n'
awk '$5 == "/sys/firmware" { print $6 }' /proc/self/mountinfo
printf 'A733_END firmware_mount\\n'
command -v mknod >/dev/null || { echo 'mknod binary missing' >&2; exit 1; }
mknod --version >/dev/null
[ ! -e /tmp/a733-probe-block ] && [ ! -L /tmp/a733-probe-block ]
if mknod /tmp/a733-probe-block b 240 0; then
    echo 'block mknod unexpectedly succeeded' >&2
    exit 1
fi
printf 'A733_BEGIN mknod\\ndenied\\nA733_END mknod\\n'
printf 'a733 ordinary private sentinel\\n' > /tmp/a733-probe-sentinel
[ "$(cat /tmp/a733-probe-sentinel)" = 'a733 ordinary private sentinel' ]
printf 'A733_BEGIN sentinel\\npassed\\nA733_END sentinel\\n'
'''


def load_helper():
    spec = importlib.util.spec_from_file_location('clean_builder_inventory', REPO / 'build/uboot-replay.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    helper.run = lambda command: run(command)
    return helper


def require(condition, message):
    if not condition:
        raise ValueError(message)


def run(command):
    return subprocess.check_output(list(map(str, command)), env=ENV, stderr=subprocess.PIPE)


def safe_path(path):
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts, 'Absolute physical path required')
    require(not any(p == path or p in path.parents for p in map(Path, ('/dev', '/proc', '/sys'))),
            'Pseudo-filesystem path forbidden')
    for component in (path, *path.parents):
        require(not component.is_symlink(), 'Symlink path forbidden: ' + str(component))
    return path


def regular(path):
    safe_path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise ValueError('Regular file required: ' + str(path))
    return os.fdopen(fd, 'rb')


def facts(path):
    with regular(path) as stream:
        before = os.fstat(stream.fileno())
        digest = hashlib.sha256()
        count = 0
        for chunk in iter(lambda: stream.read(1048576), b''):
            digest.update(chunk)
            count += len(chunk)
        after = os.fstat(stream.fileno())
    require((before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
            (after.st_size, after.st_mtime_ns, after.st_ctime_ns) and count == before.st_size,
            'Input changed while hashing')
    return {'bytes': count, 'sha256': digest.hexdigest()}


def root_owned_path(path):
    safe_path(path)
    for component in (path, *path.parents):
        info = component.stat()
        require(info.st_uid == 0 and not info.st_mode & 0o022,
                'Root-owned non-writable path required: ' + str(component))


def repository_guard(repo):
    # Run no Git command until local configuration/index/object paths are trusted.
    root_owned_path(repo)
    metadata = repo / '.git'
    root_owned_path(metadata)
    require(metadata.is_dir(), 'Local .git directory required; no worktree indirection')
    for path in metadata.rglob('*'):
        root_owned_path(path)
        require(path.is_dir() or path.is_file(), 'Special Git metadata forbidden')
    with regular(metadata / 'config') as stream:
        config = stream.read().decode('utf-8')
    require(not re.search(r'^\s*\[\s*include(?:if)?(?:\s|\])', config, re.IGNORECASE | re.MULTILINE),
            'Git config includes forbidden')
    require(not (metadata / 'objects/info/alternates').exists(), 'Borrowed Git objects forbidden')


def binding(repo):
    repository_guard(repo)
    def git(*args):
        return run(['/usr/bin/git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false',
                    '-C', repo, *args])
    require(Path(git('rev-parse', '--show-toplevel').decode().strip()) == repo, 'Wrong project root')
    require(not git('status', '--porcelain', '--untracked-files=all').strip(), 'Clean committed project required')
    head = git('rev-parse', 'HEAD').decode().strip()
    require(re.fullmatch('[0-9a-f]{40}', head), 'Invalid project commit')
    records = {}
    for relative in FILES:
        root_owned_path(repo / relative)
        with regular(repo / relative) as stream:
            data = stream.read()
        require(data == git('show', head + ':' + relative), 'Project input differs from commit: ' + relative)
        records[relative] = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    return {'head': head, 'files': records}


def mounts_under(output, mountinfo):
    mounts = []
    for line in mountinfo.splitlines():
        fields = line.split()
        require(len(fields) >= 6, 'Malformed mountinfo')
        name = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), fields[4])
        point = Path(name)
        if point == output or output in point.parents:
            mounts.append(name)
    return mounts


def check_no_mounts(output):
    require(not mounts_under(output, Path('/proc/self/mountinfo').read_text()), 'Output has active mounts')


def validate_output(output, seed, repo):
    for path in (output, seed, repo, WORKSPACE):
        safe_path(path)
    require(output.parent == WORKSPACE and
            re.fullmatch(r'seed-probe-[A-Za-z0-9][A-Za-z0-9._-]*', output.name),
            'Output must be /var/lib/a733-clean-builder/seed-probe-NAME')
    require(not os.path.lexists(output), 'Output already exists; failed outputs must be retained')
    for source in (seed, repo):
        require(source != output and source not in output.parents and output not in source.parents,
                'Input/output overlap forbidden')
    for parent in WORKSPACE.parents:
        info = parent.stat()
        require(info.st_uid == 0 and not info.st_mode & 0o022, 'Workspace ancestor must be root-owned and non-writable')
    if WORKSPACE.exists():
        info = WORKSPACE.stat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o700,
                'Dedicated workspace must be root-owned 0700')
    check_no_mounts(output)
    capacity_path = WORKSPACE if WORKSPACE.exists() else WORKSPACE.parent
    sample = resources(capacity_path)
    require(sample['free_bytes'] > 24_000_000_000, 'Need more than 24 GB free (25 GB planning target)')
    require(sample['available_memory_bytes'] >= 3 * 1024**3, 'Need at least 3 GiB available RAM')
    return sample


def resources(path):
    memory = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
    return {'free_bytes': shutil.disk_usage(path).free,
            'available_memory_bytes': int(memory['MemAvailable'].split()[0]) * 1024}


def in_range(value):
    return UID_BASE <= int(value) < UID_BASE + UID_COUNT


def shifted_root_owner(rootfs):
    owner = rootfs.stat()
    require(owner.st_uid == UID_BASE and owner.st_gid == UID_BASE,
            'Persistent root ownership shift not observed')
    return [owner.st_uid, owner.st_gid]


def validate_identity_tables(passwd, groups, subuid='', subgid=''):
    for text, index in ((passwd, 2), (passwd, 3), (groups, 2)):
        for line in text.splitlines():
            require(not in_range(line.split(':')[index]), 'Private UID/GID range already assigned')
    for text in (subuid, subgid):
        for line in text.splitlines():
            if not line.strip():
                continue
            _, start, count = line.split(':')
            require(int(start) + int(count) <= UID_BASE or int(start) >= UID_BASE + UID_COUNT,
                    'Private UID/GID range overlaps subordinate allocation')


def check_process_ranges(proc=Path('/proc')):
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            data = (entry / 'status').read_text()
        except FileNotFoundError:
            continue
        for line in data.splitlines():
            if line.startswith(('Uid:', 'Gid:', 'Groups:')):
                require(not any(in_range(v) for v in line.split()[1:]),
                        'Process uses reserved UID/GID range: ' + entry.name)


def check_range_unused():
    subids = [p.read_text() if p.exists() else '' for p in (Path('/etc/subuid'), Path('/etc/subgid'))]
    validate_identity_tables(run(['/usr/bin/getent', 'passwd']).decode(),
                             run(['/usr/bin/getent', 'group']).decode(), *subids)
    check_process_ranges()


def member_name(name):
    require(isinstance(name, str) and name and not name.startswith('/') and '\x00' not in name,
            'Absolute/empty archive path')
    parts = PurePosixPath(name).parts
    require('..' not in parts, 'Archive traversal')
    return str(PurePosixPath(name))


def audit_archive(path):
    """Reject symlink-parent writes irrespective of archive ordering, before GNU tar."""
    with tarfile.open(path, 'r:gz') as archive:
        members = archive.getmembers()
    names = {}
    for member in members:
        name = member_name(member.name)
        require(name not in names, 'Duplicate archive path')
        require(member.isdir() or member.isfile() or member.issym() or member.islnk(),
                'Special archive member forbidden (including device nodes)')
        require(0 <= member.uid < UID_COUNT and 0 <= member.gid < UID_COUNT, 'Archive owner outside namespace')
        names[name] = member
    for name, member in names.items():
        for parent in PurePosixPath(name).parents:
            if str(parent) in names:
                require(names[str(parent)].isdir(), 'Archive writes through non-directory parent')
        if member.issym():
            target = member.linkname
            require(target and '\x00' not in target, 'Empty/invalid symlink target')
            logical = posixpath.normpath(target.lstrip('/') if target.startswith('/') else
                                         posixpath.join(posixpath.dirname(name), target))
            require(logical != '..' and not logical.startswith('../'), 'Symlink escapes root')
        if member.islnk():
            target = member_name(member.linkname)
            require(target in names and names[target].isfile(), 'Hardlink must target a regular archive member')
    return {'members': len(members), 'special_nodes': 0, 'symlink_parent_writes': 0}


def nspawn_command(rootfs, probe):
    command = ['/usr/bin/systemd-nspawn', '--directory=' + str(rootfs), '--settings=no',
               '--as-pid2', '--register=no', '--keep-unit', '--console=pipe',
               '--private-users=1048576:65536', '--private-users-ownership=chown',
               '--private-network', '--no-new-privileges=yes', '--drop-capability=' + ','.join(CAPABILITIES),
               '--resolv-conf=off', '--timezone=off', '--link-journal=no',
               '--bind-ro=' + str(probe) + ':/a733-probe.sh',
               '--bind-ro=' + str(probe.parent / 'firmware-empty') + ':/sys/firmware']
    return command + ['/bin/sh', '/a733-probe.sh']


def validate_probe(data):
    text = data.decode('utf-8')
    sections = {}
    for name in ('uid_map', 'gid_map', 'status', 'interfaces', 'routes', 'routes6',
                 'devices', 'firmware', 'firmware_mount', 'mknod', 'sentinel'):
        pattern = r'^A733_BEGIN ' + name + r'\n(.*?)^A733_END ' + name + r'\n'
        matches = re.findall(pattern, text, re.MULTILINE | re.DOTALL)
        require(len(matches) == 1, 'Missing/duplicate probe section: ' + name)
        sections[name] = matches[0]
    for name in ('uid_map', 'gid_map'):
        require(sections[name].split() == ['0', str(UID_BASE), str(UID_COUNT)], 'Wrong ' + name)
    status = dict(line.split(':', 1) for line in sections['status'].splitlines() if ':' in line)
    for key in ('CapEff', 'CapPrm', 'CapBnd'):
        require(not int(status[key].strip(), 16) & PROHIBITED, 'Prohibited capability: ' + key)
    require(status['NoNewPrivs'].strip() == '1', 'NoNewPrivs must be 1')
    interfaces = [line.split(':')[0].strip() for line in sections['interfaces'].splitlines() if ':' in line]
    require(interfaces == ['lo'], 'Network interface other than lo')
    require(len(sections['routes'].splitlines()) == 1, 'IPv4 route present')
    # Linux may retain kernel IPv6 loopback routes; no external interface/route is allowed.
    for line in sections['routes6'].splitlines():
        fields = line.split()
        require(len(fields) == 10 and fields[-1] == 'lo' and
                fields[0] in ('0' * 32, '0' * 31 + '1') and
                fields[4] == '0' * 32 and fields[1] in ('00', '80'), 'IPv6 external route present')
    require(not sections['devices'].strip(), 'Forbidden device visible')
    require(not sections['firmware'].strip(), 'EFI firmware visible')
    firmware_mount = sections['firmware_mount'].splitlines()
    require(len(firmware_mount) == 1 and 'ro' in firmware_mount[0].split(','),
            'Firmware mask must be one read-only mount')
    require(sections['mknod'].strip() == 'denied', 'Block mknod was not denied')
    require(sections['sentinel'].strip() == 'passed', 'Private ordinary write failed')
    return {'uid_base': UID_BASE, 'uid_count': UID_COUNT, 'network_interfaces': interfaces,
            'ipv6_loopback_routes': sections['routes6'].splitlines(), 'prohibited_capability_mask': hex(PROHIBITED)}


@contextmanager
def interruption_signals():
    def interrupted(number, frame):
        raise InterruptedError('Interrupted by signal ' + str(number))
    previous = {n: signal.signal(n, interrupted) for n in (signal.SIGTERM, signal.SIGHUP)}
    try:
        yield
    finally:
        for n, handler in previous.items():
            signal.signal(n, handler)


def managed_command(command, log):
    process = subprocess.Popen(command, env=ENV, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        result = process.wait()
        require(result == 0, 'Command failed with exit ' + str(result) + '; inspect retained log')
    finally:
        previous = {n: signal.signal(n, signal.SIG_IGN) for n in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
        try:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(process.pid, sig)
                except ProcessLookupError:
                    pass
                if sig == signal.SIGTERM:
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        pass
            process.wait()
            log.flush()
            os.fsync(log.fileno())
        finally:
            for n, handler in previous.items():
                signal.signal(n, handler)


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2) + '\n')


def construct(seed, output, repo=REPO):
    require(os.geteuid() == 0, 'Requires root on the recorded Ubuntu 22.04 x86_64 builder')
    resources_before = validate_output(output, seed, repo)
    before = binding(repo)
    helper = load_helper()
    inventory = helper.check_builder(repo, 'replay-20261106')
    require(len(helper.package_rows((repo / 'configs/builder-replay-packages-20261106.lock.tsv').read_bytes())) == 842,
            'Expected exact 842-package replay inventory')
    with regular(repo / LOCK) as stream:
        lock_bytes = stream.read()
    lock = json.loads(lock_bytes)
    require(lock['schema'] == 1 and lock['architecture'] == 'amd64' and lock['archive'] == SEED,
            'Seed source lock mismatch')
    require(facts(seed) == SEED, 'Seed archive full hash/size mismatch')
    check_range_unused()
    help_text = run(['/usr/bin/systemd-nspawn', '--help'])
    option_probe_command = nspawn_command(output / 'rootfs', output / 'probe.sh')[:-2] + ['--help']
    option_probe_help = run(option_probe_command)
    if not WORKSPACE.exists():
        WORKSPACE.mkdir(mode=0o700)
    output.mkdir(mode=0o700)
    report = {'schema': 1, 'phase': 'seed-and-isolation-probe', 'success': False,
              'project': before, 'host_inventory': inventory, 'seed': SEED,
              'resources_before': resources_before,
              'ownership_shift': {'uid_base': UID_BASE, 'count': UID_COUNT,
                                  'persistent': True, 'method': 'systemd-nspawn chown including ACLs',
                                  'unshift_or_export': 'not implemented'},
              'complete_clean_builder': False, 'hardware_acceptance': False}
    try:
        (output / 'source-lock.json').write_bytes(lock_bytes)
        (output / 'host-inventory-before.tsv').write_bytes(
            (repo / 'configs/builder-replay-packages-20261106.lock.tsv').read_bytes())
        (output / 'nspawn-help.txt').write_bytes(help_text)
        (output / 'nspawn-option-probe.txt').write_bytes(option_probe_help)
        report['option_probe_command'] = option_probe_command
        private_seed = output / 'seed.tar.gz'
        with regular(seed) as source, private_seed.open('xb') as target:
            shutil.copyfileobj(source, target)
        require(facts(private_seed) == SEED, 'Private seed copy mismatch')
        report['archive_audit'] = audit_archive(private_seed)
        rootfs = output / 'rootfs'
        rootfs.mkdir(mode=0o700)
        require(b'GNU tar' in run(['/usr/bin/tar', '--version']), 'GNU tar required')
        with (output / 'extract.log').open('xb') as log:
            managed_command(['/usr/bin/tar', '--extract', '--gzip', '--file=' + str(private_seed),
                             '--directory=' + str(rootfs), '--numeric-owner', '--same-owner',
                             '--same-permissions', '--keep-old-files', '--delay-directory-restore'], log)
        firmware_empty = output / 'firmware-empty'
        firmware_empty.mkdir(mode=0o555)
        firmware_empty.chmod(0o555)
        probe = output / 'probe.sh'
        probe.write_bytes(PROBE)
        probe.chmod(0o444)
        report['probe_payload'] = facts(probe)
        report['firmware_mask'] = {'method': 'read-only empty directory bind',
                                   'source': str(firmware_empty), 'destination': '/sys/firmware'}
        command = nspawn_command(rootfs, probe)
        report['command'] = command
        write_json(output / 'provenance.json', report)
        check_range_unused()
        with (output / 'probe.log').open('xb') as log:
            managed_command(command, log)
        report['ownership_shift']['observed_rootfs_uid_gid'] = shifted_root_owner(rootfs)
        report['probe'] = validate_probe((output / 'probe.log').read_bytes())
        report['probe_log'] = facts(output / 'probe.log')
        report['success'] = True
    except BaseException as exc:
        report['error'] = type(exc).__name__ + ': ' + str(exc)
        raise
    finally:
        errors = []
        for label, check in (
                ('mounts', lambda: check_no_mounts(output)),
                ('process_range', check_process_ranges),
                ('inventory', lambda: require(helper.check_builder(repo, 'replay-20261106') == inventory,
                                              'Host inventory changed')),
                ('project', lambda: require(binding(repo) == before, 'Project changed during seed probe'))):
            try:
                check()
            except BaseException as exc:
                errors.append(label + ': ' + str(exc))
        try:
            report['resources_after'] = resources(output)
        except OSError as exc:
            errors.append('resources: ' + str(exc))
        report['post_checks'] = {'success': not errors, 'errors': errors}
        report['success'] = report['success'] and not errors
        write_json(output / 'receipt.json', report)
        require(not errors, 'Post-run checks failed; preserve output and inspect receipt: ' + '; '.join(errors))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        with interruption_signals():
            construct(args.seed, args.output)
    except (ValueError, OSError, subprocess.SubprocessError, KeyError, UnicodeError, tarfile.TarError) as exc:
        print('Seed/probe refused or failed: ' + str(exc), file=sys.stderr)
        return 1
    print('Seed and isolation probe passed only; full clean builder remains open: ' + str(args.output))
    return 0


if __name__ == '__main__':
    sys.exit(main())
