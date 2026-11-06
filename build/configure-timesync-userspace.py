#!/usr/bin/env python3
"""Derive a separate 221-package ARM userspace clone; no hardware acceptance.

Run only through the reviewed frozen-builder namespace launcher with -I -B.
The original 220-package producer, mirror and receipts are never rewritten.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import subprocess
import time

WORK = Path('/a733-export-output')
ROOT = WORK / 'arm-root'
REPO = WORK / 'repo'
PROJECT = Path('/a733-sources/project')
SOURCE_MANIFEST = Path('/a733-sources/manifest.json')
BOM = Path('/a733-sources/rootfs-bom.json')
DEB = Path('/a733-sources/systemd-timesyncd_252.39-1~deb12u2_arm64.deb')
CONTRACT = 'configs/host-timesync-inputs.lock.json'
CONTRACT_PIN = {'bytes': 3837, 'sha256': 'b183b9df1c19241420c6939c4487dcf4894ae60daa3827d73589a6e112918a5f'}
SOURCE_FILES = ('build/configure-timesync-userspace.py', 'build/configure-host-userspace.py',
                'build/export-host-userspace.py', 'build/userspace-bootstrap-api.py',
                'build/timesync-identity.py', CONTRACT,
                'configs/host-userspace-packages.lock.tsv', 'configs/host-userspace-package-hashes.lock.tsv',
                'configs/host-timesync-packages.lock.tsv', 'configs/host-timesync-package-hashes.lock.tsv',
                'configs/host-timesync-sysusers.conf', 'configs/host-userspace-source-archives.lock.json',
                'docs/evidence/time-sync-package-input-20261106.json')
COMMAND_NAMES = ('timesync-install', 'timesync-inventory', 'timesync-audit', 'timesync-check')
QUERY = ['/usr/bin/dpkg-query', '-W', '-f=${Package}\t${Version}\t${Architecture}\t${db:Status-Status}\n']
MIN_FREE = 4 * 1024**3

def require(value, message):
    if not value:
        raise ValueError(message)


def physical_directory(path, owner=None):
    require(path.is_absolute() and '..' not in path.parts, 'canonical absolute path required')
    for ancestor in (path, *path.parents):
        require(stat.S_ISDIR(ancestor.lstat().st_mode), 'symlink/non-directory ancestor: ' + str(ancestor))
    info = path.lstat()
    require(owner is None or info.st_uid == info.st_gid == owner, 'directory owner mismatch')
    return info


def identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def record(path):
    physical_directory(path.parent)
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1, 'single regular file required: ' + str(path))
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    digest, size = hashlib.sha256(), 0
    with os.fdopen(fd, 'rb') as stream:
        require(identity(before) == identity(os.fstat(fd)), 'file changed before read')
        for data in iter(lambda: stream.read(1048576), b''):
            digest.update(data)
            size += len(data)
        require(identity(before) == identity(os.fstat(fd)) == identity(path.lstat())
                and size == before.st_size, 'file changed during read')
    return {'bytes': size, 'sha256': digest.hexdigest()}


def read_checked(path, limit=1048576):
    before = record(path)
    require(before['bytes'] <= limit, 'file exceeds bound')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        data = stream.read(limit + 1)
    require({'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()} == before
            and record(path) == before, 'file changed during read')
    return data


def readonly(path, directory=False):
    physical_directory(path if directory else path.parent)
    info = path.lstat()
    require((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            and info.st_uid == info.st_gid == 65534
            and (directory or (stat.S_IMODE(info.st_mode) == 0o444 and info.st_nlink == 1)),
            'immutable source owner/mode required')
    require(os.statvfs(path).f_flag & os.ST_RDONLY, 'source mount must be read-only')


def sources():
    readonly(SOURCE_MANIFEST)
    manifest = json.loads(read_checked(SOURCE_MANIFEST))
    require(set(manifest) == {'revision', 'files'} and
            isinstance(manifest['revision'], str) and re.fullmatch('[0-9a-f]{40}', manifest['revision'])
            and isinstance(manifest['files'], dict), 'invalid source manifest')
    require(set(SOURCE_FILES) <= manifest['files'].keys(), 'required project sources missing')
    records = {}
    for name, item in manifest['files'].items():
        require(isinstance(name, str) and name and not name.startswith('/')
                and all(part not in ('', '.', '..') for part in name.split('/'))
                and '\\' not in name and not any(ord(char) < 32 or ord(char) == 127 for char in name),
                'unsafe manifest path')
        require(isinstance(item, dict) and set(item) == {'git_blob', 'bytes', 'sha256'}
                and isinstance(item['git_blob'], str) and re.fullmatch('[0-9a-f]{40}', item['git_blob'])
                and isinstance(item['sha256'], str) and re.fullmatch('[0-9a-f]{64}', item['sha256'])
                and type(item['bytes']) is int and item['bytes'] >= 0, 'invalid source record')
        path = PROJECT / name
        readonly(path)
        actual = record(path)
        require(actual == {key: item[key] for key in ('bytes', 'sha256')}, 'source authentication failed: ' + name)
        records[name] = actual
    # importlib may use a stale bytecode cache even with -B. Admit only the
    # committed source paths, before any module import can consult this tree.
    physical_directory(PROJECT)
    expected_files = set(manifest['files'])
    expected_dirs = set()
    for name in expected_files:
        expected_dirs.update(str(parent) for parent in Path(name).parents if parent != Path('.'))
    actual_files, actual_dirs = set(), set()
    for directory, dirs, files in os.walk(PROJECT, followlinks=False):
        for name in dirs:
            path = Path(directory) / name
            physical_directory(path)
            actual_dirs.add(str(path.relative_to(PROJECT)))
        for name in files:
            path = Path(directory) / name
            require(stat.S_ISREG(path.lstat().st_mode), 'non-regular project source')
            actual_files.add(str(path.relative_to(PROJECT)))
    require(actual_files == expected_files and actual_dirs == expected_dirs,
            'undeclared project paths forbidden')
    return records


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def pinned(path, pin):
    readonly(path)
    require(record(path) == pin, 'input pin differs: ' + path.name)
    return read_checked(path, 16 * 1024**2)


def parse_lock(data, hashes=False):
    rows = {}
    for line in data.decode().splitlines():
        fields = line.split('\t')
        require(len(fields) == 3, 'invalid lock fields')
        package, version, value = fields
        require(re.fullmatch('[a-z0-9][a-z0-9+.-]*', package) and package not in rows
                and re.fullmatch(r'[^\s\x00-\x1f]+', version), 'invalid/duplicate package lock')
        if hashes:
            parts = value.split('  ')
            require(len(parts) == 2 and re.fullmatch('[0-9a-f]{64}', parts[0])
                    and re.fullmatch('debs/[A-Za-z0-9+._~%-]+[.]deb', parts[1]), 'invalid hash lock')
            value = tuple(parts)
        else:
            require(value in ('arm64', 'all'), 'invalid lock architecture')
        rows[package] = (version, value)
    return rows


def validate_locks(contract):
    locks = {name: parse_lock(pinned(PROJECT / name, pin), 'hashes' in name)
             for name, pin in contract['locks'].items()}
    old = locks['configs/host-userspace-packages.lock.tsv']
    new = locks['configs/host-timesync-packages.lock.tsv']
    oldhash = locks['configs/host-userspace-package-hashes.lock.tsv']
    newhash = locks['configs/host-timesync-package-hashes.lock.tsv']
    addition = contract['addition']
    name = addition['package']
    require(len(old) == len(oldhash) == 220 and name not in old
            and new == dict(old, **{name: (addition['version'], addition['architecture'])})
            and len(new) == contract['configured_packages'] == 221, 'exact base plus one package required')
    require(newhash == dict(oldhash, **{name: (addition['version'],
            (addition['sha256'], 'debs/' + addition['filename']))}), 'hash lock additions differ')
    for versions, hashes in ((old, oldhash), (new, newhash)):
        require(set(versions) == set(hashes) and all(versions[p][0] == hashes[p][0] for p in versions),
                'version/hash lock mismatch')
    return old, new, oldhash


def status_inventory(data):
    rows = {}
    for paragraph in data.decode().strip().split('\n\n'):
        fields = {}
        for line in paragraph.splitlines():
            if line.startswith((' ', '\t')):
                require(fields, 'orphan status continuation')
                continue
            key, sep, value = line.partition(':')
            require(sep and key not in fields, 'malformed/duplicate status field')
            fields[key] = value.strip()
        require(fields.get('Status') == 'install ok installed', 'unconfigured package status')
        package = fields['Package']
        require(package not in rows, 'duplicate status package')
        rows[package] = (fields['Version'], fields['Architecture'])
    return rows


def mounts():
    result = []
    unescape = lambda s: re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), s)
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        left, right = line.split(' - ', 1)
        fields = left.split()
        result.append((Path(unescape(fields[4])), fields[2], Path(unescape(fields[3])), right.split()[0]))
    return result


def location(path, topology):
    candidates = [(len(m.parts), index, dev, root / path.relative_to(m), fs)
                  for index, (m, dev, root, fs) in enumerate(topology)
                  if m == path or m in path.parents]
    require(candidates, 'unknown filesystem topology')
    return max(candidates)[2:]


def work_guard():
    topology = mounts()
    device, where, _ = location(WORK, topology)
    for path in (PROJECT, SOURCE_MANIFEST, BOM, DEB, Path('/usr')):
        source_device, source_where, _ = location(path, topology)
        require(device != source_device or (where != source_where and where not in source_where.parents
                and source_where not in where.parents), 'work aliases protected source')
    require(not any(WORK in mount.parents for mount, *_ in topology), 'nested work mount forbidden')
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        fields = line.split(' - ', 1)[0].split()
        require(not any(value.startswith(('shared:', 'master:', 'propagate_from:')) for value in fields[6:]),
                'recursively private mount namespace required')
    require(socket.if_nameindex() == [(1, 'lo')], 'private loopback-only network required')


def environment(c, h):
    require(not any(name.startswith(('LD_', 'PYTHON', 'FAKEROOT', 'FAKECHROOT', 'DEBOOTSTRAP'))
                    for name in os.environ), 'loader/Python/bootstrap overrides forbidden')
    fields = c.restrictions(h)
    require(fields['Uid'].split() == fields['Gid'].split() == ['0'] * 4, 'nonzero saved identities')
    for key in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb'):
        require(re.fullmatch('[0-9a-fA-F]{16}', fields[key]), 'malformed capabilities')
    return fields


def output_names():
    return ['timesync-receipt.json', 'timesync-package-state.json', 'identity-before',
            'api-ready.json', 'api-continue.json', 'api-mount-receipt.json',
            'api-setup-request.json', 'api-inaccessible-file'] + [
                name + suffix for name in COMMAND_NAMES for suffix in ('-started.json', '.stdout', '.stderr')]


def preflight(c, e, h, identity):
    initial = environment(c, h)
    work_guard()
    physical_directory(WORK, 0)
    require(stat.S_IMODE(WORK.lstat().st_mode) == 0o700, 'private work mode required')
    e.disk_backed(WORK)
    require(not os.statvfs(WORK).f_flag & os.ST_RDONLY and shutil.disk_usage(WORK).free >= MIN_FREE,
            '4 GiB free writable disk required')
    physical_directory(ROOT, 0)
    require(not h.under(h.mounts(), ROOT), 'root API overmount forbidden')
    for name in output_names():
        require(not os.path.lexists(WORK / name), 'derivation output exists: ' + name)
    contract = json.loads(pinned(PROJECT / CONTRACT, CONTRACT_PIN))
    old, new, oldhash = validate_locks(contract)
    policy = contract['identity']['policy']
    policy_bytes = pinned(PROJECT / policy['path'], {k: policy[k] for k in ('bytes', 'sha256')})
    evidence = contract['package_evidence']
    package_evidence = json.loads(pinned(PROJECT / evidence['path'], {k: evidence[k] for k in ('bytes', 'sha256')}))
    source = contract['source_lock']
    source_lock = json.loads(pinned(PROJECT / source['path'], {k: source[k] for k in ('bytes', 'sha256')}))
    selected = [item for item in source_lock['sources'] if item['package'] == 'systemd']
    source_expected = dict(contract['source'], binary_packages=[p for p in contract['source']['binary_packages']
                                                             if p != 'systemd-timesyncd'])
    require(selected == [source_expected], 'systemd source selection differs')
    addition = contract['addition']
    require(package_evidence['package']['SHA256'] == addition['sha256']
            and package_evidence['package_bytes'] == addition['bytes'], 'package evidence differs')
    deb = pinned(DEB, {k: addition[k] for k in ('bytes', 'sha256')})
    result = subprocess.run(['/usr/bin/dpkg-deb', '--field', str(DEB), 'Package', 'Version', 'Architecture', 'Source'],
                            capture_output=True, check=True, timeout=30,
                            env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'})
    expected_control = ''.join(key + ': ' + addition[key.lower()] + '\n' for key in ('Package', 'Version', 'Architecture')) + 'Source: systemd\n'
    require(result.stdout.decode() == expected_control, 'extra package control differs')
    bom = pinned(BOM, contract['base']['bom'])
    require(e.canonicalize(e.scan(ROOT)[0]) == json.loads(bom), 'complete base BOM differs')
    require(status_inventory(c.read_checked(ROOT / 'var/lib/dpkg/status', 16 * 1024**2)) == old,
            'base installed inventory differs')
    before = identity.audit(ROOT, 'before')
    for name in ('run/a733-timesync.deb', 'etc/sysusers.d/systemd-timesync.conf',
                 'usr/bin/qemu-aarch64-static', 'var/lib/systemd/timesync', 'run/systemd/system'):
        require(not os.path.lexists(ROOT / name), 'unexpected existing derivation target')
    for name in h.DEVICES:
        h.placeholder(ROOT / 'dev' / name, 0)
    h.check_aliases(ROOT / 'dev')
    require(set(os.listdir(ROOT / 'dev')) == set(h.DEVICES) | set(h.ALIASES) | {'pts', 'shm'},
            'unexpected device target')
    for name in ('proc', 'sys', 'dev/pts', 'dev/shm'):
        physical_directory(ROOT / name, 0)
        require(not any((ROOT / name).iterdir()), 'API target not empty')
    if os.path.lexists(ROOT / 'a733-repo'):
        physical_directory(ROOT / 'a733-repo', 0)
        require(not any((ROOT / 'a733-repo').iterdir()), 'repository target not empty')
    readonly(h.PROFILE)
    profile, _ = h.authenticated(h.PROFILE, owner=65534)
    ns = c.validate_profile(profile, h)
    h.check_repository(profile['repo_manifest_sha256'], path=REPO, owner=0)
    manifest, _ = h.authenticated(REPO / 'manifest.json', profile['repo_manifest_sha256'])
    require(len(manifest['packages']) == 220 and
            {p['Package']: (p['Version'], p['Architecture']) for p in manifest['packages']} == old and
            {p['Package']: p['SHA256'] for p in manifest['packages']} == {p: v[1][0] for p, v in oldhash.items()},
            'unchanged 220-package repository required')
    qemu = c.read_checked(c.QEMU)
    require(hashlib.sha256(qemu).hexdigest() == c.QEMU_SHA256, 'QEMU pin differs')
    for name in ('usr/bin', 'usr/sbin', 'etc/systemd/system-preset', 'etc/sysusers.d', 'run'):
        c.clone_directory(ROOT / name)
    return dict(contract=contract, profile=profile, initial=initial, ns=ns, expected=new,
                qemu=qemu, policy=policy_bytes, deb=deb, identity_before=before)


def arm(h, report, arguments, name):
    require(name in COMMAND_NAMES, 'unreviewed ARM command')
    command = ['/usr/sbin/chroot', str(ROOT), '/usr/bin/qemu-aarch64-static', *arguments]
    entry = {'name': name, 'argv': command}
    report['commands'].append(entry)
    h.write_new(WORK / (name + '-started.json'), entry)
    def log(suffix):
        return os.fdopen(os.open(WORK / (name + suffix), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'wb')
    with log('.stdout') as out, log('.stderr') as err:
        result = subprocess.run(command, stdout=out, stderr=err, timeout=1200,
                                env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/root', 'LC_ALL': 'C',
                                     'TZ': 'UTC', 'DEBIAN_FRONTEND': 'noninteractive', 'SYSTEMD_OFFLINE': '1'})
    entry['status'] = result.returncode
    entry['logs'] = {suffix: record(WORK / (name + suffix)) for suffix in ('.stdout', '.stderr')}
    require(result.returncode == 0, 'ARM command failed: ' + name)
    return read_checked(WORK / (name + '.stdout'), 16 * 1024**2).decode()


def enable_service_links(contract):
    """Enable only pinned future-boot links without running systemd or a daemon."""
    physical_directory(ROOT, 0)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    for name, target in contract['service']['links'].items():
        parts = name.split('/')
        require(parts[:3] == ['etc', 'systemd', 'system'] and len(parts) >= 4
                and all(part not in ('', '.', '..') for part in parts), 'unsafe service link path')
        fd = os.open(ROOT, flags)
        try:
            for part in parts[:-1]:
                info = os.fstat(fd)
                require(info.st_uid == info.st_gid == 0 and not info.st_mode & 0o022,
                        'unsafe service parent owner/mode')
                created = False
                try:
                    child = os.open(part, flags, dir_fd=fd)
                except FileNotFoundError:
                    os.mkdir(part, mode=0o755, dir_fd=fd)
                    created = True
                    child = os.open(part, flags, dir_fd=fd)
                os.close(fd)
                fd = child
                if created:
                    info = os.fstat(fd)
                    require(info.st_uid == info.st_gid == 0, 'unsafe new service parent owner')
                    os.fchmod(fd, 0o755)  # The builder may use a restrictive umask.
            info = os.fstat(fd)
            require(info.st_uid == info.st_gid == 0 and not info.st_mode & 0o022,
                    'unsafe service parent owner/mode')
            try:
                info = os.stat(parts[-1], dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                os.symlink(target, parts[-1], dir_fd=fd)
                info = os.stat(parts[-1], dir_fd=fd, follow_symlinks=False)
            require(stat.S_ISLNK(info.st_mode) and info.st_uid == info.st_gid == 0
                    and info.st_nlink == 1 and os.readlink(parts[-1], dir_fd=fd) == target,
                    'conflicting service enablement link')
        finally:
            os.close(fd)


def validate_service(contract, c):
    service = contract['service']
    lib = ROOT / 'lib'
    require(stat.S_ISLNK(lib.lstat().st_mode) and lib.lstat().st_uid == lib.lstat().st_gid == 0
            and os.readlink(lib) == 'usr/lib', 'merged-usr lib alias differs')
    for base in ('etc/systemd/system', 'run/systemd/system'):
        for suffix in ('', '.d'):
            require(not os.path.lexists(ROOT / base / ('systemd-timesyncd.service' + suffix)),
                    'service mask or override forbidden')
    for name, target in service['links'].items():
        path = ROOT / name
        physical_directory(path.parent, 0)
        info = path.lstat()
        require(stat.S_ISLNK(info.st_mode) and info.st_uid == info.st_gid == 0
                and info.st_nlink == 1 and os.readlink(path) == target,
                'required service enablement link differs')
    files = {}
    for name, pin in service['installed_files'].items():
        path = ROOT / name
        physical_directory(path.parent, 0)
        info = path.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_uid == info.st_gid == 0 and info.st_nlink == 1
                and stat.S_IMODE(info.st_mode) == int(pin['mode'], 8), 'installed service file metadata differs')
        data = c.read_checked(path)
        actual = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
        require(actual == {k: pin[k] for k in ('bytes', 'sha256')}, 'installed service bytes differ')
        files[name] = actual
    return dict(policy=service['enablement'], links=service['links'], files=files)


def derive(c, e, h, identity):
    facts = preflight(c, e, h, identity)  # No failure receipt for unsafe inputs.
    report = dict(schema=1, success=False, commands=[], classification='isolated ARM package derivation; no hardware acceptance',
                  inputs=facts['contract'], source_manifest=record(SOURCE_MANIFEST), identity_before=facts['identity_before'])
    try:
        snapshot = WORK / 'identity-before'
        snapshot.mkdir(mode=0o700)
        (snapshot / 'etc').mkdir(mode=0o700)
        for name in ('passwd', 'group'):
            data = c.read_checked(ROOT / 'etc' / name)
            fd = os.open(snapshot / 'etc' / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
        require(identity.audit(snapshot, 'before') == facts['identity_before'], 'identity changed before snapshot')
        if not os.path.lexists(ROOT / 'a733-repo'):
            (ROOT / 'a733-repo').mkdir(mode=0o755)
        c.write_regular(ROOT / 'usr/bin/qemu-aarch64-static', facts['qemu'], 0o755)
        c.suppress_daemons()
        c.write_regular(ROOT / 'etc/sysusers.d/systemd-timesync.conf', facts['policy'])
        c.write_regular(ROOT / 'run/a733-timesync.deb', facts['deb'])
        _, helper_sha = c.handshake(h, facts['profile'], facts['initial'], facts['ns'])
        report['helper_receipt_sha256'] = helper_sha
        arm(h, report, ['/usr/bin/dpkg', '--install', '/run/a733-timesync.deb'], 'timesync-install')
        actual = c.inventory(arm(h, report, QUERY, 'timesync-inventory'))
        require(actual == facts['expected'], 'exact 221-package inventory differs')
        require(not arm(h, report, ['/usr/bin/dpkg', '--audit'], 'timesync-audit').strip(), 'dpkg audit not empty')
        arm(h, report, ['/usr/bin/apt-get', 'check'], 'timesync-check')
        report['identity_after'] = identity.audit(ROOT, 'after')
        require(identity.compare_accounts(snapshot, ROOT), 'old account tuples changed')
        report['accounts_preserved'] = True
        require(c.read_checked(ROOT / 'etc/sysusers.d/systemd-timesync.conf') == facts['policy']
                and c.read_checked(ROOT / 'run/a733-timesync.deb') == facts['deb']
                and c.read_checked(ROOT / 'usr/sbin/policy-rc.d') == c.POLICY
                and c.read_checked(ROOT / 'etc/systemd/system-preset/00-prototype.preset') == c.PRESET,
                'derivation inputs or suppression changed')
        require(environment(c, h) == facts['initial'] and h.namespaces() == facts['ns'], 'restrictions changed')
        live = sorted(int(p.name) for p in Path('/proc').iterdir() if p.name.isdigit())
        require(set(live) <= {1, os.getpid()}, 'daemon/helper remains')
        require(not os.path.lexists(ROOT / 'var/lib/systemd/timesync'), 'unexpected clock state')
        h.check_repository(facts['profile']['repo_manifest_sha256'], path=REPO, readonly=True, owner=0)
        status = c.read_checked(ROOT / 'var/lib/dpkg/status', 16 * 1024**2)
        require(status_inventory(status) == facts['expected'], 'final package state differs')
        package_state = dict(schema=1, packages=221, inventory=actual,
                             dpkg_status={'bytes': len(status), 'sha256': hashlib.sha256(status).hexdigest()})
        enable_service_links(facts['contract'])
        service = validate_service(facts['contract'], c)
        h.write_new(WORK / 'timesync-package-state.json', package_state)
        report.update(service_enablement=service, package_state=record(WORK / 'timesync-package-state.json'), inventory=actual,
                      configured_packages=221, dpkg_audit_clean=True, apt_check_passed=True,
                      live_namespace_pids=live, success=True)
    except BaseException as error:
        report['error'] = repr(error)
        raise
    finally:
        report['finished'] = time.time()
        h.write_new(WORK / 'timesync-receipt.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    try:
        require(os.sys.flags.isolated and os.sys.dont_write_bytecode, '-I -B required')
        sources()  # Authenticate closed tree before importlib can consult any dependency/cache.
        modules = [load(name, PROJECT / ('build/' + filename + '.py')) for name, filename in
                   (('configure', 'configure-host-userspace'), ('export', 'export-host-userspace'),
                    ('bootstrap_api', 'userspace-bootstrap-api'), ('identity', 'timesync-identity'))]
        result = derive(*modules)
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
        parser.exit(1, 'configure-timesync-userspace: ' + str(error) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
