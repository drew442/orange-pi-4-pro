#!/usr/bin/env python3
"""Configure a cloned foreign ARM tree inside the frozen restricted builder.

QEMU-user is a build tool only. This establishes no physical boot, KVM, NVMe
or SD-write acceptance. All paths are fixed; never run against a deployed host.
"""
import argparse
import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import stat
import subprocess
import tempfile
import time

WORK = Path('/a733-export-output')
ROOT = WORK / 'arm-root'
REPO = WORK / 'repo'
PROJECT = Path('/a733-sources/project')
HELPER = PROJECT / 'build/userspace-bootstrap-api.py'
INPUTS = Path('/a733-sources/configure-inputs.json')
LOCK = PROJECT / 'configs/host-userspace-packages.lock.tsv'
HASH_LOCK = PROJECT / 'configs/host-userspace-package-hashes.lock.tsv'
LOCK_PIN = (6671, 'f2abf03d0d6e7fd506560689093220514cbf1acc678f7560562026d6ca6564a6')
HASH_LOCK_PIN = (28638, 'f2f613e79d9c1abc1689e19cfe81b624d9d2ac3beeb322fd5788980d919f7d86')
QEMU = Path('/usr/bin/qemu-aarch64-static')
QEMU_SHA256 = '92c4aeb8dd898de7152c184370ab22f09ae7c5b7279fd300081c76cee3f78a80'
DROP_MASK = 0xa631000
CLONE_OWNER = 0
POLICY = b'#!/bin/sh\nexit 101\n'
PRESET = b'disable *\n'
APTCONF = (b'Acquire::Check-Valid-Until "false";\nAPT::Install-Recommends "false";\n'
           b'APT::Install-Suggests "false";\nAcquire::Languages "none";\n'
           b'APT::Keep-Downloaded-Packages "true";\n'
           b'Binary::apt::APT::Keep-Downloaded-Packages "true";\n')
SOURCES = (b'deb [signed-by=/usr/share/keyrings/debian-archive-keyring.gpg] '
           b'file:/a733-repo bookworm main\n')
COMMAND_NAMES = ('second-stage', 'base-inventory', 'apt-update', 'apt-install',
                 'full-inventory', 'dpkg-audit', 'apt-check')
DIGEST = re.compile('[0-9a-f]{64}')


def require(value, message):
    if not value:
        raise ValueError(message)


def identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def physical_directory(path, owner=None):
    path = Path(path)
    require(path.is_absolute() and str(path) == os.path.abspath(path), 'physical absolute path required')
    for ancestor in (path, *path.parents):
        require(stat.S_ISDIR(ancestor.lstat().st_mode), 'symlink/non-directory ancestor: ' + str(ancestor))
    info = path.lstat()
    require(owner is None or info.st_uid == info.st_gid == owner, 'directory owner mismatch')
    return info


def clone_directory(path, create=False):
    """Validate every existing parent before creating approved clone directories."""
    require(path == ROOT or ROOT in path.parents, 'directory outside fixed clone forbidden')
    missing = []
    current = path
    while current != ROOT.parent:
        if os.path.lexists(current):
            physical_directory(current, CLONE_OWNER)
        else:
            missing.append(current)
        current = current.parent
    if create:
        for directory in reversed(missing):
            directory.mkdir(mode=0o755)
    return missing


def read_checked(path, limit=64 * 1024**2):
    physical_directory(path.parent)
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1, 'single regular file required: ' + str(path))
    require(before.st_size <= limit, 'file exceeds bound')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        require(identity(before) == identity(os.fstat(fd)), 'file changed before read')
        data = stream.read(limit + 1)
        require(len(data) == before.st_size and identity(before) == identity(os.fstat(fd))
                == identity(path.lstat()), 'file changed during read')
    return data


def sha(path):
    return hashlib.sha256(read_checked(path)).hexdigest()


def readonly_input(path):
    physical_directory(path.parent)
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == info.st_gid == 65534
            and stat.S_IMODE(info.st_mode) == 0o444, 'root-owned immutable source input required')
    require(os.statvfs(path).f_flag & os.ST_RDONLY, 'source input mount must be read-only')


def load_helper():
    readonly_input(HELPER)
    spec = importlib.util.spec_from_file_location('userspace_bootstrap_api', HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def restrictions(h):
    release = platform.freedesktop_os_release()
    require(release.get('ID') == 'ubuntu' and release.get('VERSION_ID') == '22.04'
            and platform.machine() == 'x86_64', 'frozen Ubuntu 22.04 amd64 required')
    require(os.getuid() == os.geteuid() == os.getgid() == os.getegid() == 0, 'namespace UID/GID 0 required')
    for name in ('uid_map', 'gid_map'):
        require(Path('/proc/self/' + name).read_text().split() == ['0', '1048576', '65536'], 'exact private UID/GID map required')
    fields = h.status()
    require(fields['NoNewPrivs'] == '1', 'NoNewPrivs required')
    for name in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb'):
        require(int(fields[name], 16) & DROP_MASK == 0, 'seven dangerous capabilities must be dropped')
    require(not os.environ.get('FAKEROOTKEY') and 'libfakeroot' not in os.environ.get('LD_PRELOAD', ''), 'native metadata required')
    return fields


def locks():
    data = []
    for path, pin in ((LOCK, LOCK_PIN), (HASH_LOCK, HASH_LOCK_PIN)):
        readonly_input(path)
        content = read_checked(path)
        require((len(content), hashlib.sha256(content).hexdigest()) == pin, 'package lock pin differs')
        data.append(content.decode())
    expected, hashes = {}, {}
    for line in data[0].splitlines():
        package, version, architecture = line.split('\t')
        require(package not in expected and re.fullmatch('[a-z0-9][a-z0-9+.-]*', package)
                and version and architecture in ('all', 'arm64'), 'invalid package tuple')
        expected[package] = (version, architecture)
    for line in data[1].splitlines():
        package, version, rest = line.split('\t')
        digest, filename = rest.split('  ')
        require(package not in hashes and DIGEST.fullmatch(digest)
                and filename.startswith('debs/') and len(Path(filename).parts) == 2
                and Path(filename).name not in ('.', '..'), 'invalid package hash tuple')
        require(package in expected and expected[package][0] == version, 'hash/version locks differ')
        hashes[package] = digest
    require(len(expected) == len(hashes) == 220, 'exact 220-package locks required')
    return expected, hashes


def validate_adapter(h, digest, foreign):
    adapter = WORK / 'adapter'
    physical_directory(adapter, CLONE_OWNER)
    manifest, actual = h.authenticated(adapter / 'manifest.json', digest)
    require(actual == foreign['adapter_manifest']['sha256'], 'foreign adapter digest differs')
    sources = {'usr/sbin/debootstrap': 'debootstrap', 'usr/share/debootstrap/functions': 'functions',
               'usr/share/debootstrap/scripts/sid': 'scripts/sid',
               'usr/share/debootstrap/scripts/debian-common': 'scripts/debian-common'}
    require(manifest.get('version') == '1.0.126+nmu1ubuntu0.9' and set(manifest['sources']) == set(sources)
            and manifest.get('bookworm_symlink') == 'sid', 'unexpected adapter manifest')
    expected_files = {'manifest.json', 'scripts/bookworm'} | set(sources.values())
    actual_files = set()
    for directory, dirs, files in os.walk(adapter, followlinks=False):
        for name in dirs + files:
            path = Path(directory) / name
            if path.is_dir() and not path.is_symlink():
                physical_directory(path, CLONE_OWNER)
            else:
                actual_files.add(str(path.relative_to(adapter)))
    require(actual_files == expected_files, 'unexpected adapter file set')
    require((adapter / 'scripts/bookworm').is_symlink()
            and os.readlink(adapter / 'scripts/bookworm') == 'sid', 'unexpected adapter suite link')
    copied = {'debootstrap': 'debootstrap', 'functions': 'functions',
              'scripts/sid': 'suite-script', 'scripts/debian-common': 'debian-common'}
    for source, output in sources.items():
        item = manifest['sources'][source]
        require(item['output'] == output, 'adapter output path differs')
        content = read_checked(adapter / output)
        require(len(content) == item['output_bytes'] and hashlib.sha256(content).hexdigest() == item['output_sha256'], 'adapter output authentication failed')
        require(read_checked(ROOT / 'debootstrap' / copied[output]) == content, 'cloned bootstrap differs from adapter')


def validate_profile(profile, h):
    require(set(profile) == {'nonce', 'host_userns_inode', 'host_driver_pid', 'repo_manifest_sha256'}, 'unexpected profile fields')
    require(all(isinstance(profile[k], str) and DIGEST.fullmatch(profile[k]) for k in ('nonce', 'repo_manifest_sha256')), 'invalid profile digest/nonce')
    require(type(profile['host_userns_inode']) is int and profile['host_userns_inode'] > 0
            and type(profile['host_driver_pid']) is int and profile['host_driver_pid'] > 1, 'invalid host identities')
    ns = h.namespaces()
    require(ns['user'] != profile['host_userns_inode'], 'initial user namespace forbidden')
    return ns


def output_names():
    names = ['configure-receipt.json', 'api-ready.json', 'api-continue.json', 'api-mount-receipt.json',
             'api-setup-request.json', 'api-inaccessible-file']
    names += [name + suffix for name in COMMAND_NAMES for suffix in ('-started.json', '.stdout', '.stderr')]
    return names


def preflight(h):
    initial = restrictions(h)
    physical_directory(WORK, CLONE_OWNER)
    info = physical_directory(ROOT, CLONE_OWNER)
    require(ROOT == WORK / 'arm-root' and stat.S_IMODE(info.st_mode) in (0o700, 0o755), 'fixed foreign clone required')
    require(not h.under(h.mounts(), ROOT), 'cloned root has active mounts')
    for name in output_names():
        require(not os.path.lexists(WORK / name), 'configuration output already exists: ' + name)
    readonly_input(h.PROFILE)
    profile, _ = h.authenticated(h.PROFILE, owner=65534)
    ns = validate_profile(profile, h)
    readonly_input(INPUTS)
    inputs, _ = h.authenticated(INPUTS, owner=65534)
    require(set(inputs) == {'foreign_receipt_sha256', 'adapter_manifest_sha256'} and
            all(isinstance(v, str) and DIGEST.fullmatch(v) for v in inputs.values()), 'invalid configure inputs')
    foreign, _ = h.authenticated(WORK / 'foreign-receipt.json', inputs['foreign_receipt_sha256'])
    require(foreign.get('schema') == 1 and foreign.get('success') is True
            and foreign.get('dpkg_status_bytes') == 0, 'foreign receipt did not pass')
    require(profile['repo_manifest_sha256'] == foreign['repository']['manifest']['sha256'], 'foreign repository differs')
    expected, hashes = locks()
    h.check_repository(profile['repo_manifest_sha256'], path=REPO, owner=0)
    manifest, _ = h.authenticated(REPO / 'manifest.json', profile['repo_manifest_sha256'])
    require({p['Package']: (p['Version'], p['Architecture']) for p in manifest['packages']} == expected
            and len(manifest['packages']) == 220
            and {p['Package']: p['SHA256'] for p in manifest['packages']} == hashes, 'repository package locks differ')
    validate_adapter(h, inputs['adapter_manifest_sha256'], foreign)
    require(read_checked(ROOT / 'var/lib/dpkg/status') == b'', 'foreign tree already configured')
    require(read_checked(ROOT / 'debootstrap/arch').strip() == b'arm64'
            and read_checked(ROOT / 'debootstrap/suite').strip() == b'bookworm', 'wrong foreign architecture/suite')
    for name in h.DEVICES:
        h.placeholder(ROOT / 'dev' / name, 0)
    h.check_aliases(ROOT / 'dev')
    for name in ('proc', 'sys', 'dev/pts', 'dev/shm', 'etc/apt/sources.list.d'):
        physical_directory(ROOT / name, CLONE_OWNER)
        require(not any((ROOT / name).iterdir()), 'foreign API/APT directory not empty: ' + name)
    if os.path.lexists(ROOT / 'a733-repo'):
        physical_directory(ROOT / 'a733-repo', CLONE_OWNER)
        require(not any((ROOT / 'a733-repo').iterdir()), 'mirror target not empty')
    require(not os.path.lexists(ROOT / 'usr/bin/qemu-aarch64-static'), 'build interpreter already exists')
    qemu = read_checked(QEMU)
    require(hashlib.sha256(qemu).hexdigest() == QEMU_SHA256, 'QEMU build-tool pin differs')
    for name in ('usr/bin', 'usr/sbin', 'etc/systemd/system-preset', 'etc/apt/apt.conf.d', 'usr/share/keyrings'):
        clone_directory(ROOT / name)
    for name in ('usr/sbin/policy-rc.d', 'etc/systemd/system-preset/00-prototype.preset',
                 'etc/apt/apt.conf.d/99-prototype', 'etc/apt/sources.list',
                 'usr/share/keyrings/debian-archive-keyring.gpg'):
        path = ROOT / name
        if os.path.lexists(path):
            read_checked(path)
            require(path.lstat().st_uid == path.lstat().st_gid == CLONE_OWNER, 'clone output owner mismatch')
    return profile, initial, ns, expected, qemu


def write_regular(path, data, mode=0o644):
    """Atomically replace an approved build-clone file; never follow links."""
    require(ROOT in path.parents, 'write outside fixed clone forbidden')
    clone_directory(path.parent, create=True)
    before = None
    if os.path.lexists(path):
        read_checked(path)
        require(path.lstat().st_uid == path.lstat().st_gid == CLONE_OWNER, 'clone output owner mismatch')
        before = identity(path.lstat())
    fd, staging = tempfile.mkstemp(prefix='.configure-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            os.fchmod(stream.fileno(), mode)
            stream.flush()
            os.fsync(stream.fileno())
        if before is None:
            libc = ctypes.CDLL(None, use_errno=True)
            result = libc.renameat2(-100, os.fsencode(staging), -100, os.fsencode(path), 1)
            if result:
                raise OSError(ctypes.get_errno(), 'exclusive clone publication failed')
        else:
            require(identity(path.lstat()) == before, 'clone file changed before replacement')
            os.replace(staging, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.lexists(staging):
            os.unlink(staging)


def suppress_daemons():
    write_regular(ROOT / 'usr/sbin/policy-rc.d', POLICY, 0o755)
    write_regular(ROOT / 'etc/systemd/system-preset/00-prototype.preset', PRESET)


def arm(h, report, arguments, name, timeout=1200, env_extra=None):
    require(name in COMMAND_NAMES, 'unreviewed ARM command log name')
    command = ['/usr/sbin/chroot', str(ROOT), '/usr/bin/qemu-aarch64-static', *arguments]
    env = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/root', 'LC_ALL': 'C', 'TZ': 'UTC',
           'DEBIAN_FRONTEND': 'noninteractive', 'SYSTEMD_OFFLINE': '1'}
    if env_extra:
        env.update(env_extra)
    entry = {'name': name, 'argv': command}
    report['commands'].append(entry)
    h.write_new(WORK / (name + '-started.json'), entry)
    def log(path):
        return os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'wb')
    with log(WORK / (name + '.stdout')) as out, log(WORK / (name + '.stderr')) as err:
        result = subprocess.run(command, stdout=out, stderr=err, env=env, timeout=timeout)
    entry['status'] = result.returncode
    require(result.returncode == 0, 'ARM command failed: ' + name)
    return read_checked(WORK / (name + '.stdout'), 16 * 1024**2).decode()


def inventory(text):
    rows = {}
    for line in text.splitlines():
        package, version, architecture, state = line.split('\t')
        require(package not in rows and state == 'installed', 'duplicate/unconfigured package')
        rows[package] = (version, architecture)
    return rows


def handshake(h, profile, initial, ns, timeout=120):
    ready = dict(nonce=profile['nonce'], pid=os.getpid(), namespace_inodes=ns,
                 uid=os.getuid(), status=initial, bootstrap_root=str(ROOT))
    h.write_new(WORK / 'api-ready.json', ready)
    deadline = time.monotonic() + timeout
    while not os.path.lexists(WORK / 'api-continue.json'):
        require(time.monotonic() < deadline, 'helper timeout')
        time.sleep(0.1)
    # Only initial-root can publish this immutable continuation after wait(helper).
    continuation, _ = h.authenticated(WORK / 'api-continue.json', owner=65534)
    require(set(continuation) == {'nonce', 'helper_status', 'receipt_sha256'} and
            continuation['nonce'] == profile['nonce'] and type(continuation['helper_status']) is int
            and continuation['helper_status'] == 0, 'helper continuation mismatch/failure')
    require(isinstance(continuation['receipt_sha256'], str) and DIGEST.fullmatch(continuation['receipt_sha256']), 'invalid helper receipt digest')
    helper, _ = h.authenticated(h.RECEIPT, continuation['receipt_sha256'], owner=65534)
    request = helper['request']
    require(helper.get('success') is True and request == dict(nonce=profile['nonce'], namespace_inodes=ns,
            host_userns_inode=profile['host_userns_inode'], bootstrap_root=str(ROOT),
            host_driver_pid=profile['host_driver_pid'], repo_manifest_sha256=profile['repo_manifest_sha256']), 'helper request identity mismatch')
    h.check_api(str(ROOT), readonly=True, absent=helper['proc_absent'], authenticated_anchor=helper['destination'])
    h.check_devices(str(ROOT), authenticated_anchor=helper['devices'])
    h.check_repository(profile['repo_manifest_sha256'], path=ROOT / 'a733-repo', readonly=True, owner=0)
    h.require_absent(ROOT / 'proc' / str(profile['host_driver_pid']))
    require(restrictions(h) == initial and h.namespaces() == ns, 'payload restrictions/namespaces changed')
    return ready, continuation['receipt_sha256']


def configure():
    h = load_helper()
    # Reject all inputs and preexisting outputs before writing even a failure receipt.
    profile, initial, ns, expected, qemu = preflight(h)
    report = {'schema': 1, 'classification': 'ARM package configuration in isolated frozen builder; no hardware acceptance',
              'success': False, 'commands': []}
    try:
        # Only the authenticated foreign clone reaches this chmod; APT's _apt can traverse it.
        os.chmod(ROOT, 0o755, follow_symlinks=False)
        if not os.path.lexists(ROOT / 'a733-repo'):
            (ROOT / 'a733-repo').mkdir(mode=0o755)
        write_regular(ROOT / 'usr/bin/qemu-aarch64-static', qemu, 0o755)
        require(sha(ROOT / 'usr/bin/qemu-aarch64-static') == QEMU_SHA256, 'injected QEMU differs')
        suppress_daemons()
        ready, helper_sha = handshake(h, profile, initial, ns)
        report.update(ready=ready, helper_receipt_sha256=helper_sha)
        arm(h, report, ['/bin/sh', '/debootstrap/debootstrap', '--second-stage'], 'second-stage',
            env_extra={'DEBOOTSTRAP_DIR': '/debootstrap'})
        query = ['/usr/bin/dpkg-query', '-W', '-f=${Package}\t${Version}\t${Architecture}\t${db:Status-Status}\n']
        base = inventory(arm(h, report, query, 'base-inventory'))
        report['configured_base_count'] = len(base)
        require(len(base) == 99 and all(expected.get(p) == v for p, v in base.items()), 'base inventory differs')
        # Stock debootstrap removes policy-rc.d. Restore it before any offline APT operation.
        suppress_daemons()
        write_regular(ROOT / 'etc/apt/apt.conf.d/99-prototype', APTCONF)
        write_regular(ROOT / 'etc/apt/sources.list', SOURCES)
        write_regular(ROOT / 'usr/share/keyrings/debian-archive-keyring.gpg', read_checked(REPO / 'verified-keyring.gpg'))
        require(not any((ROOT / 'etc/apt/sources.list.d').iterdir()), 'additional APT source forbidden')
        arm(h, report, ['/usr/bin/apt-get', 'update'], 'apt-update')
        specs = [p + '=' + v for p, (v, _) in sorted(expected.items())]
        arm(h, report, ['/usr/bin/apt-get', '--yes', '--no-install-recommends', 'install', *specs], 'apt-install', timeout=1800)
        actual = inventory(arm(h, report, query, 'full-inventory'))
        require(actual == expected and len(actual) == 220, 'full inventory differs')
        require(not arm(h, report, ['/usr/bin/dpkg', '--audit'], 'dpkg-audit').strip(), 'dpkg audit not empty')
        arm(h, report, ['/usr/bin/apt-get', 'check'], 'apt-check')
        require(read_checked(ROOT / 'usr/sbin/policy-rc.d') == POLICY and
                read_checked(ROOT / 'etc/systemd/system-preset/00-prototype.preset') == PRESET, 'daemon suppression changed')
        require(restrictions(h) == initial and h.namespaces() == ns, 'payload restrictions/namespaces changed')
        live = sorted(int(p.name) for p in Path('/proc').iterdir() if p.name.isdigit())
        require(set(live) <= {1, os.getpid()}, 'daemon/helper remains in namespace')
        h.check_repository(profile['repo_manifest_sha256'], path=REPO, readonly=True, owner=0)
        report.update(configured_packages=len(actual), inventory_match=True, dpkg_audit_clean=True,
                      apt_check_passed=True, final_status=restrictions(h), live_namespace_pids=live, success=True)
    except BaseException as error:
        report['error'] = repr(error)
        raise
    finally:
        report['finished'] = time.time()
        h.write_new(WORK / 'configure-receipt.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    try:
        result = configure()
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
        parser.exit(1, 'configure-host-userspace: ' + str(error) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
