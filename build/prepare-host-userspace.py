#!/usr/bin/env python3
"""Authenticate offline binaries and extract foreign ARM data in the fixed builder.

No ARM maintainer scripts run. This provides no A733 boot, KVM, NVMe or
SD-write acceptance; the original rootfs archive is never a construction seed.
"""
import argparse
import ctypes
from contextlib import ExitStack
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
PROJECT = Path('/a733-sources/project')
CACHE = Path('/a733-debs')
INDEX = Path('/a733-sources/Packages.xz')
SOURCE_MANIFEST = Path('/a733-sources/manifest.json')
SOURCE_FILES = ('build/prepare-host-userspace.py', 'build/offline-userspace-repo.py',
                'build/debootstrap-adapter.py', 'configs/host-userspace-packages.lock.tsv',
                'configs/host-userspace-package-hashes.lock.tsv')
NAMESPACE_OWNER = 0
DROP_MASK = 0xa631000
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/nonexistent',
       'LC_ALL': 'C', 'TZ': 'UTC'}
PSEUDO = {'proc', 'sysfs', 'devtmpfs', 'devpts', 'tmpfs', 'ramfs', 'cgroup',
          'cgroup2', 'securityfs', 'debugfs', 'tracefs', 'configfs', 'mqueue',
          'hugetlbfs', 'rpc_pipefs', 'autofs', 'binfmt_misc', 'fusectl'}


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
            and (directory or stat.S_IMODE(info.st_mode) == 0o444),
            'immutable source owner/mode required')
    require(os.statvfs(path).f_flag & os.ST_RDONLY, 'source mount must be read-only')


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


def restrictions():
    release = platform.freedesktop_os_release()
    require(release.get('ID') == 'ubuntu' and release.get('VERSION_ID') == '22.04'
            and platform.machine() == 'x86_64', 'frozen Ubuntu 22.04 amd64 required')
    require(os.getuid() == os.geteuid() == os.getgid() == os.getegid() == 0, 'namespace UID/GID 0 required')
    for name in ('uid_map', 'gid_map'):
        require(Path('/proc/self/' + name).read_text().split() == ['0', '1048576', '65536'], 'exact private UID/GID map required')
    fields = {}
    for line in Path('/proc/self/status').read_text().splitlines():
        key, value = line.split(':', 1)
        require(key not in fields, 'duplicate process status field')
        fields[key] = value.strip()
    require(fields['Uid'].split() == fields['Gid'].split() == ['0'] * 4,
            'all namespace UID/GID identities must be zero')
    require(fields['NoNewPrivs'] == '1', 'NoNewPrivs required')
    for name in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb'):
        require(re.fullmatch('[0-9a-fA-F]{16}', fields[name])
                and int(fields[name], 16) & DROP_MASK == 0, 'seven dangerous capabilities must be dropped')
    require(not any(key.startswith(('FAKEROOT', 'FAKECHROOT', 'LD_', 'DEBOOTSTRAP'))
                    or key in ('PYTHONPATH', 'PYTHONHOME') for key in os.environ),
            'loader/fakeroot/Python environment forbidden')
    return {key: fields[key] for key in ('Uid', 'Gid', 'NoNewPrivs', 'CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb', 'NSpid')}


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


def preflight():
    initial = restrictions()
    require(ROOT == WORK / 'arm-root', 'fixed root relationship required')
    physical_directory(WORK, NAMESPACE_OWNER)
    require(not any(WORK.iterdir()), 'work directory must be empty')
    topology = mounts()
    device, where, filesystem = location(WORK, topology)
    require(filesystem not in PSEUDO and not os.statvfs(WORK).f_flag & os.ST_RDONLY, 'disk-backed writable work required')
    require(not any(WORK in mount.parents for mount, *_ in topology), 'nested work mount forbidden')
    for path in (CACHE, INDEX, PROJECT, Path('/usr/share/debootstrap'), Path('/usr/sbin/debootstrap')):
        source_device, source_where, _ = location(path, topology)
        require(device != source_device or (where != source_where and where not in source_where.parents
                and source_where not in where.parents), 'work aliases protected source')
    readonly(CACHE, directory=True)
    readonly(INDEX)
    code = sources()
    return initial, code


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def publish(temporary, destination):
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.renameat2(-100, os.fsencode(temporary), -100, os.fsencode(destination), 1):
        raise OSError(ctypes.get_errno(), 'exclusive publication failed', str(destination))
    fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_new(path, value):
    physical_directory(path.parent, NAMESPACE_OWNER)
    fd, temporary = tempfile.mkstemp(prefix='.foreign-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        publish(temporary, path)
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)


def foreign(report):
    tree, repo = WORK / 'adapter', WORK / 'repo'
    command = ['/bin/sh', str(tree / 'debootstrap'), '--arch=arm64', '--variant=minbase',
               '--foreign', '--force-check-gpg', '--keyring=' + str(repo / 'verified-keyring.gpg'),
               '--include=ca-certificates', 'bookworm', str(ROOT), 'file://' + str(repo)]
    env = dict(ENV, DEBOOTSTRAP_DIR=str(tree), DEBIAN_FRONTEND='noninteractive', SYSTEMD_OFFLINE='1')
    entry = {'argv': command, 'environment': env}
    report['commands'].append(entry)
    temporary = []
    try:
        with ExitStack() as stack:
            streams = []
            for suffix in ('stdout', 'stderr'):
                target = WORK / ('foreign.' + suffix)
                require(not os.path.lexists(target), 'command log already exists')
                fd, name = tempfile.mkstemp(prefix='.foreign-log-', dir=WORK)
                temporary.append((name, target))
                streams.append(stack.enter_context(os.fdopen(fd, 'wb')))
            try:
                result = subprocess.run(command, stdout=streams[0], stderr=streams[1], env=env, timeout=600)
                entry['status'] = result.returncode
            finally:
                for stream in streams:
                    stream.flush()
                    os.fsync(stream.fileno())
            require(result.returncode == 0, 'foreign debootstrap failed')
    finally:
        for name, target in temporary:
            try:
                publish(name, target)
            finally:
                if os.path.lexists(name):
                    os.unlink(name)


def validate_output(adapter, manifest):
    tree, repo = WORK / 'adapter', WORK / 'repo'
    require(record(ROOT / 'debootstrap/functions') == record(tree / 'functions'), 'foreign functions differ')
    require(read_checked(ROOT / 'debootstrap/arch').strip() == b'arm64'
            and read_checked(ROOT / 'debootstrap/suite').strip() == b'bookworm', 'wrong foreign architecture/suite')
    require(read_checked(ROOT / 'var/lib/dpkg/status') == b'', 'foreign stage unexpectedly configured packages')
    devices = {}
    for name, numbers in adapter.DEVICES.items():
        path = ROOT / 'dev' / name
        record(path)
        info = path.lstat()
        require((info.st_uid, info.st_gid, info.st_size, stat.S_IMODE(info.st_mode), info.st_nlink)
                == (NAMESPACE_OWNER, NAMESPACE_OWNER, 0, 0o666, 1), 'invalid device placeholder: ' + name)
        devices[name] = {'placeholder_type': 'ordinary regular file', 'major': numbers[0],
                         'minor': numbers[1], 'mode': stat.S_IMODE(info.st_mode)}
    aliases = {'fd': '/proc/self/fd', 'stdin': '/proc/self/fd/0',
               'stdout': '/proc/self/fd/1', 'stderr': '/proc/self/fd/2'}
    require({path.name for path in (ROOT / 'dev').iterdir()}
            == set(adapter.DEVICES) | set(aliases) | {'pts', 'shm'}, 'unexpected target device paths')
    for name, destination in aliases.items():
        path = ROOT / 'dev' / name
        require(stat.S_ISLNK(path.lstat().st_mode) and os.readlink(path) == destination,
                'unexpected target device alias')
    for name in ('pts', 'shm'):
        physical_directory(ROOT / 'dev' / name, NAMESPACE_OWNER)
        require(not any((ROOT / 'dev' / name).iterdir()), 'target device API directory not empty')
    topology = mounts()
    require(not any(mount == ROOT or ROOT in mount.parents for mount, *_ in topology), 'target mounts forbidden')
    for name in ('proc', 'sys'):
        physical_directory(ROOT / name, NAMESPACE_OWNER)
        require(not any((ROOT / name).iterdir()), 'target API directory not empty')
    names = {line.split('\t')[0] for line in read_checked(PROJECT / 'configs/host-userspace-packages.lock.tsv').decode().splitlines()}
    sets = {name: read_checked(ROOT / 'debootstrap' / name).decode().split() for name in ('required', 'base')}
    require(all(values and len(values) == len(set(values)) and set(values) <= names for values in sets.values()), 'bootstrap package sets outside lock')
    expected = {p['SHA256']: p['Size'] for p in manifest['packages']}
    cache = ROOT / 'var/cache/apt/archives'
    physical_directory(cache, NAMESPACE_OWNER)
    archives = sorted(cache.glob('*.deb'))
    require(archives, 'no cached bootstrap packages')
    hashes = []
    for path in archives:
        actual = record(path)
        require(expected.get(actual['sha256']) == actual['bytes'], 'cached deb differs from lock')
        hashes.append(actual['sha256'])
    require(len(hashes) == len(set(hashes)), 'duplicate cached package')
    return devices, sets, len(archives)


def check_repository(repo, manifest, initial):
    require(record(repo / 'manifest.json') == initial, 'repository manifest changed')
    expected = {'manifest.json'}
    for item in manifest['files']:
        name = item['path']
        require(isinstance(name, str) and name and not name.startswith('/') and '..' not in name.split('/')
                and '.' not in name.split('/') and '' not in name.split('/'), 'invalid repository BOM path')
        require(name not in expected, 'duplicate repository BOM path')
        expected.add(name)
        require(record(repo / name) == {key: item[key] for key in ('bytes', 'sha256')}, 'repository file changed')
    actual = set()
    for directory, dirs, files in os.walk(repo, followlinks=False):
        for name in dirs:
            physical_directory(Path(directory) / name, NAMESPACE_OWNER)
        actual.update(str((Path(directory) / name).relative_to(repo)) for name in files)
    require(actual == expected, 'repository file set changed')
    require(hashlib.sha256(json.dumps(manifest['files'], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            == manifest['bom_sha256'], 'repository BOM digest differs')


def prepare():
    # No failure receipt or other writes until every namespace/source/work guard passes.
    initial, code = preflight()
    report = {'schema': 1, 'classification': 'Authenticated offline mirror and native foreign DATA extraction only; no maintainer scripts, configured userspace or hardware acceptance',
              'success': False, 'commands': [], 'restrictions_before': initial, 'code': code}
    try:
        mirror = load('offline_repo', PROJECT / 'build/offline-userspace-repo.py')
        adapter = load('bootstrap_adapter', PROJECT / 'build/debootstrap-adapter.py')
        repo, tree = WORK / 'repo', WORK / 'adapter'
        manifest = mirror.construct(CACHE, INDEX, repo)
        require(manifest['schema'] == 1 and manifest['status'] == 'verified'
                and manifest['package_count'] == len(manifest['packages']) == 220, 'incomplete authenticated repository')
        report['repository'] = {'manifest': record(repo / 'manifest.json'), 'package_count': manifest['package_count'], 'bom_sha256': manifest['bom_sha256']}
        check_repository(repo, manifest, report['repository']['manifest'])
        adapter.prepare(tree)
        report['adapter_manifest'] = record(tree / 'manifest.json')
        ROOT.mkdir(mode=0o700)
        foreign(report)
        devices, sets, count = validate_output(adapter, manifest)
        report.update(dpkg_status_bytes=0, device_placeholders=devices,
                      bootstrap_package_sets=sets, bootstrap_cached_debs=count)
        check_repository(repo, manifest, report['repository']['manifest'])
        require(record(tree / 'manifest.json') == report['adapter_manifest'], 'adapter manifest changed')
        require(sources() == code, 'project sources changed')
        report['restrictions_after'] = restrictions()
        require(report['restrictions_after'] == initial, 'payload restrictions changed')
        report['success'] = True
    except BaseException as error:
        report['error'] = repr(error)
        raise
    finally:
        report['finished'] = time.time()
        write_new(WORK / 'foreign-receipt.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    try:
        result = prepare()
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
        parser.exit(1, 'prepare-host-userspace: ' + str(error) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
