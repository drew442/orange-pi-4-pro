#!/usr/bin/env python3
"""Finalize a configured throwaway ARM tree and export native, verified PAX bytes.

This runs only in the frozen builder's restricted fresh user namespace. It runs
no target executables, creates no devices, and establishes no hardware acceptance.
"""
import argparse
import base64
import ctypes
from decimal import Decimal
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import stat
import tarfile
import tempfile

WORK = Path('/a733-export-output')
ROOT = WORK / 'arm-root'
RECEIPT = WORK / 'configure-receipt.json'
LOCK = Path(__file__).resolve().parent.parent / 'configs/host-userspace-packages.lock.tsv'
LOCK_SHA256 = 'f2abf03d0d6e7fd506560689093220514cbf1acc678f7560562026d6ca6564a6'
LOCK_BYTES = 6671
PACKAGE_COUNT = 220
QEMU_SHA256 = '92c4aeb8dd898de7152c184370ab22f09ae7c5b7279fd300081c76cee3f78a80'
DROP_MASK = 0xa631000
ROOT_OWNER = (0, 0)
DEVICES = {'dev/null': (1, 3), 'dev/zero': (1, 5), 'dev/full': (1, 7),
           'dev/random': (1, 8), 'dev/urandom': (1, 9), 'dev/tty': (5, 0),
           'dev/console': (5, 1), 'dev/ptmx': (5, 2)}
SOURCES = (b'deb [signed-by=/usr/share/keyrings/debian-archive-keyring.gpg] '
           b'https://snapshot.debian.org/archive/debian/20261001T000000Z/ bookworm main\n')
MAX_UNPACKED = 2 * 1024**3
MAX_COMPRESSED = 512 * 1024**2
MIN_FREE = 3 * 1024**3
ARCHIVE_EPOCH = 1790812800  # Pinned snapshot source: 2026-10-01T00:00:00Z.
GENERATED_FILES = ('etc/nvme/hostid', 'etc/nvme/hostnqn',
                   'var/cache/apt/pkgcache.bin', 'var/cache/apt/srcpkgcache.bin',
                   'var/cache/ldconfig/aux-cache')


def require(value, message):
    if not value:
        raise ValueError(message)


def identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def physical(path):
    path = Path(path)
    require(path.is_absolute() and str(path) == os.path.abspath(path), 'absolute physical path required')
    require(not any(path == p or p in path.parents for p in map(Path, ('/proc', '/sys', '/dev'))),
            'pseudo filesystem path forbidden')
    for part in (path, *path.parents):
        require(not part.is_symlink(), 'symlink ancestor forbidden: ' + str(part))
    return path


def held(path):
    """Return an open regular file only after comparing its pathname identity."""
    physical(path.parent)
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode), 'regular file required: ' + str(path))
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    stream = os.fdopen(fd, 'rb')
    try:
        require(identity(os.fstat(fd)) == identity(before), 'file changed at open: ' + str(path))
    except BaseException:
        stream.close()
        raise
    return stream, before


def read_checked(path, limit=1024**2):
    stream, before = held(path)
    with stream:
        require(before.st_size <= limit, 'input too large: ' + str(path))
        data = stream.read(limit + 1)
        require(len(data) == before.st_size and identity(before) == identity(os.fstat(stream.fileno()))
                == identity(path.lstat()), 'input changed while held: ' + str(path))
    return data


def mounts():
    result = []
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        left, right = line.split(' - ', 1)
        fields = left.split()
        decode = lambda text: re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), text)
        result.append((Path(decode(fields[4])), right.split()[0]))
    return result


def no_mounts(root):
    for path, _ in mounts():
        require(path != root and root not in path.parents, 'active mount in source: ' + str(path))


def disk_backed(path):
    candidates = [(p, kind) for p, kind in mounts() if p == path or p in path.parents]
    require(candidates, 'no backing mount')
    _, kind = max(candidates, key=lambda pair: len(pair[0].parts))
    require(kind in {'ext2', 'ext3', 'ext4', 'xfs', 'btrfs', 'zfs', 'f2fs'},
            'physical disk filesystem required')


def environment():
    release = platform.freedesktop_os_release()
    require(release.get('ID') == 'ubuntu' and release.get('VERSION_ID') == '22.04'
            and platform.machine() == 'x86_64', 'frozen Ubuntu 22.04 amd64 required')
    require(os.getuid() == os.geteuid() == os.getgid() == os.getegid() == 0, 'namespace UID/GID 0 required')
    for name in ('uid_map', 'gid_map'):
        require(Path('/proc/self/' + name).read_text().split() == ['0', '1048576', '65536'],
                'exact private UID/GID map required')
    fields = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines())
    require(fields['NoNewPrivs'].strip() == '1', 'NoNewPrivs required')
    for name in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb'):
        require(int(fields[name], 16) & DROP_MASK == 0, 'seven dangerous capabilities must be dropped')
    require(not os.environ.get('FAKEROOTKEY') and 'libfakeroot' not in os.environ.get('LD_PRELOAD', ''),
            'native metadata required')


def inventory():
    lock = read_checked(LOCK)
    require(len(lock) == LOCK_BYTES and hashlib.sha256(lock).hexdigest() == LOCK_SHA256, 'package lock pin differs')
    expected = sorted(tuple(line.split('\t')) for line in lock.decode().splitlines())
    require(len(expected) == PACKAGE_COUNT and all(len(item) == 3 for item in expected), 'invalid package lock')
    actual = []
    text = read_checked(ROOT / 'var/lib/dpkg/status', 16 * 1024**2).decode()
    for paragraph in re.split(r'\n\s*\n', text):
        if not paragraph.strip():
            continue
        fields = {}
        for line in paragraph.splitlines():
            if line.startswith((' ', '\t')):
                continue
            key, sep, value = line.partition(':')
            require(sep and key not in fields, 'invalid dpkg status stanza')
            fields[key] = value.strip()
        require(fields.get('Status') == 'install ok installed', 'package not fully installed')
        actual.append(tuple(fields[key] for key in ('Package', 'Version', 'Architecture')))
    require(sorted(actual) == expected, 'configured inventory differs from exact lock')
    return {'packages': len(actual), 'lock_sha256': LOCK_SHA256}


def authenticate_receipt(expected):
    require(isinstance(expected, str) and re.fullmatch('[0-9a-f]{64}', expected), 'receipt SHA256 required')
    data = read_checked(RECEIPT)
    require(hashlib.sha256(data).hexdigest() == expected, 'configure receipt authentication failed')
    require(RECEIPT.stat().st_nlink == 1, 'receipt hardlink forbidden')
    receipt = json.loads(data)
    require(receipt.get('success') is True and receipt.get('configured_packages') == PACKAGE_COUNT
            and all(receipt.get(name) is True for name in ('inventory_match', 'dpkg_audit_clean', 'apt_check_passed')),
            'configure receipt did not pass')
    return data


def attributes(path):
    attrs = {}
    names = os.listxattr(path, follow_symlinks=False)
    require(sum(len(os.fsencode(name)) + 1 for name in names) < 1024, 'xattr name list too large')
    for name in names:
        value = os.getxattr(path, name, follow_symlinks=False)
        require(len(os.fsencode(name)) + 1 + len(value) < 1024, 'raw xattr too large')
        attrs[name] = base64.b64encode(value).decode('ascii')
    return attrs


def digest(stream):
    value, size = hashlib.sha256(), 0
    for chunk in iter(lambda: stream.read(1024**2), b''):
        value.update(chunk)
        size += len(chunk)
    return {'bytes': size, 'sha256': value.hexdigest()}


def scan(root):
    """Capture complete metadata and bytes; reject unsupported nodes and aliases."""
    physical(root)
    no_mounts(root)
    records, identities, groups = {}, {}, {}
    total = 0
    def visit(path):
        nonlocal total
        info = path.lstat()
        name = str(path.relative_to(root))
        entry = {'mode': stat.S_IMODE(info.st_mode), 'uid': info.st_uid, 'gid': info.st_gid,
                 'mtime_ns': info.st_mtime_ns, 'xattrs': attributes(path)}
        if stat.S_ISDIR(info.st_mode):
            entry['type'] = 'dir'
        elif stat.S_ISREG(info.st_mode):
            key = (info.st_dev, info.st_ino)
            groups.setdefault(key, []).append(name)
            require(total + info.st_size <= MAX_UNPACKED, 'unpacked tree exceeds 2 GiB')
            stream, opened = held(path)
            with stream:
                require(identity(info) == identity(opened), 'file changed before read')
                entry['data'] = digest(stream)
                require(identity(info) == identity(os.fstat(stream.fileno())), 'file changed during read')
            total += info.st_size
            require(total <= MAX_UNPACKED, 'unpacked tree exceeds 2 GiB')
            entry['type'] = 'file'
        elif stat.S_ISLNK(info.st_mode):
            entry.update(type='symlink', link=os.readlink(path))
        else:
            raise ValueError('special source node forbidden: ' + name)
        require(identity(info) == identity(path.lstat()), 'source changed during metadata read')
        records[name] = entry
        identities[name] = identity(info)
        if entry['type'] == 'dir':
            for child in sorted(path.iterdir(), key=lambda p: os.fsencode(p.name)):
                visit(child)
            require(identity(info) == identity(path.lstat()), 'directory changed during scan')
    visit(root)
    for group in groups.values():
        first = group[0]
        require(identities[first][5] == len(group), 'regular hardlink escapes source tree')
        for name in group[1:]:
            records[name].update(type='hardlink', link=first)
    for name, device in DEVICES.items():
        require(name in records, 'missing device placeholder: ' + name)
        item = records[name]
        require(item['type'] == 'file' and item['data']['bytes'] == 0 and
                item['mode'] == 0o666 and (item['uid'], item['gid']) == ROOT_OWNER and
                identities[name][5] == 1 and not item['xattrs'], 'unsafe device placeholder: ' + name)
        item.update(type='char', uid=0, gid=0, device=list(device))
        del item['data']
    return records, identities


def physical_directory(path):
    physical(path)
    require(stat.S_ISDIR(path.lstat().st_mode), 'physical directory required: ' + str(path))


def generated_file(path):
    """Validate optional generated files and every physical in-tree parent."""
    physical(path.parent)
    for parent in path.parents:
        if parent == ROOT:
            break
        if os.path.lexists(parent):
            info = parent.lstat()
            require(stat.S_ISDIR(info.st_mode) and (info.st_uid, info.st_gid) == ROOT_OWNER,
                    'unsafe generated file parent: ' + str(parent.relative_to(ROOT)))
    if os.path.lexists(path):
        info = path.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                and (info.st_uid, info.st_gid) == ROOT_OWNER,
                'unsafe generated file: ' + str(path.relative_to(ROOT)))


def canonicalize(records):
    """Copy native scan records, clamping only archive/BOM modification times."""
    return {name: dict(item, mtime_ns=min(item['mtime_ns'], ARCHIVE_EPOCH * 10**9))
            for name, item in records.items()}


def atomic_replace(path, data):
    physical(path.parent)
    if os.path.lexists(path):
        stream, info = held(path)
        stream.close()
        require(info.st_nlink == 1, 'cleanup cannot modify hardlinked file')
    else:
        info = None
    fd, temporary = tempfile.mkstemp(prefix='.a733-clean-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            os.fchmod(stream.fileno(), stat.S_IMODE(info.st_mode) if info else 0o644)
            if info:
                os.fchown(stream.fileno(), info.st_uid, info.st_gid)
            stream.flush()
            os.fsync(stream.fileno())
        if info:
            require(identity(path.lstat()) == identity(info), 'cleanup file changed')
        os.replace(temporary, path)
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)


def cleanup():
    """Only approved build-generated paths; records contain names/counts only."""
    removed = []
    no_mounts(ROOT)
    for name in ('proc', 'sys', 'dev/pts', 'dev/shm'):
        path = ROOT / name
        physical_directory(path)
        require(not any(path.iterdir()), 'pseudo filesystem directory not empty: ' + name)
    # Validate all guards before the first destructive operation.
    root_info = ROOT.lstat()
    require(stat.S_ISDIR(root_info.st_mode) and (root_info.st_uid, root_info.st_gid) == ROOT_OWNER,
            'source root must be namespace-root owned physical directory')
    require(stat.S_IMODE(root_info.st_mode) in (0o700, 0o755), 'unexpected source root mode')
    for name in GENERATED_FILES:
        generated_file(ROOT / name)
    for name in ('etc/apt/sources.list', 'etc/machine-id'):
        path = ROOT / name
        physical(path.parent)
        if os.path.lexists(path):
            stream, info = held(path)
            stream.close()
            require(info.st_nlink == 1, 'cleanup cannot modify hardlinked file')
    keyring = read_checked(ROOT / 'usr/share/keyrings/debian-archive-keyring.gpg')
    require(bool(keyring), 'archive public keyring missing')
    require(read_checked(ROOT / 'etc/systemd/system-preset/00-prototype.preset') == b'disable *\n',
            'disable-all preset missing')
    apt = read_checked(ROOT / 'etc/apt/apt.conf.d/99-prototype')
    for setting in (b'Acquire::Check-Valid-Until "false";', b'APT::Install-Recommends "false";',
                    b'APT::Install-Suggests "false";'):
        require(setting in apt, 'required APT policy missing')
    policy = ROOT / 'usr/sbin/policy-rc.d'
    if os.path.lexists(policy):
        require(read_checked(policy) == b'#!/bin/sh\nexit 101\n', 'unexpected policy-rc.d')
    qemu = ROOT / 'usr/bin/qemu-aarch64-static'
    if os.path.lexists(qemu):
        require(hashlib.sha256(read_checked(qemu, 64 * 1024**2)).hexdigest() == QEMU_SHA256,
                'injected qemu pin differs')
    shadow_path = ROOT / 'etc/shadow'
    shadow = read_checked(shadow_path).decode()
    lines = shadow.splitlines(keepends=True)
    roots = [i for i, line in enumerate(lines) if line.startswith('root:')]
    require(len(roots) == 1, 'one root shadow entry required')
    fields = lines[roots[0]].split(':')
    require(len(fields) == 9, 'invalid root shadow entry')
    if not fields[1].startswith(('!', '*')):
        fields[1] = '!' + fields[1]
        lines[roots[0]] = ':'.join(fields)
    caches = ('var/lib/apt/lists', 'var/cache/apt/archives', 'var/log', 'run', 'tmp')
    for name in caches:
        physical_directory(ROOT / name)
    repo = ROOT / 'a733-repo'
    if os.path.lexists(repo):
        physical_directory(repo)
        require(not any(repo.iterdir()), 'build mirror directory not empty')
    bootstrap = ROOT / 'debootstrap'
    if os.path.lexists(bootstrap):
        physical_directory(bootstrap)
        # Stock debootstrap's narrowly defined build-only state; unknown/user files fail closed.
        allowed = {'arch', 'base', 'debian-common', 'debootstrap', 'debootstrap.log', 'debpaths',
                   'deburis', 'functions', 'required', 'suite', 'suite-script', 'variant',
                   'mirror', 'extra-suites', 'include', 'exclude', 'foreign', 'second-stage',
                   'second-stage.log', 'bootstrap.log', 'apt.conf', 'stable', 'bookworm'}
        for path in bootstrap.iterdir():
            require(path.name in allowed and stat.S_ISREG(path.lstat().st_mode),
                    'unexpected debootstrap state')
    candidates = [ROOT / name for name in ('var/lib/dbus/machine-id', 'var/lib/systemd/random-seed',
                                           'var/lib/urandom/random-seed')]
    ssh = ROOT / 'etc/ssh'
    if os.path.lexists(ssh):
        physical_directory(ssh)
        candidates.extend(sorted(ssh.glob('ssh_host_*')))
    for path in candidates:
        if os.path.lexists(path):
            physical(path.parent)
            require(stat.S_ISREG(path.lstat().st_mode) and path.lstat().st_nlink == 1,
                    'unsafe generated secret path: ' + str(path.relative_to(ROOT)))
    require(identity(ROOT.lstat()) == identity(root_info), 'source root changed before cleanup')
    os.chmod(ROOT, 0o755)
    atomic_replace(ROOT / 'etc/apt/sources.list', SOURCES)
    if ''.join(lines) != shadow:
        atomic_replace(shadow_path, ''.join(lines).encode())
    for path in candidates + [ROOT / name for name in GENERATED_FILES] + [policy, qemu]:
        if os.path.lexists(path):
            path.unlink()
            removed.append(str(path.relative_to(ROOT)))
    atomic_replace(ROOT / 'etc/machine-id', b'')
    counts = {}
    for name in caches:
        directory = ROOT / name
        children = list(directory.iterdir())
        counts[name] = len(children)
        for child in children:
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
    for path in (repo, bootstrap):
        if os.path.lexists(path):
            shutil.rmtree(path)
            removed.append(str(path.relative_to(ROOT)))
    return {'root_mode': {'before': stat.S_IMODE(root_info.st_mode), 'after': 0o755},
            'removed_paths': sorted(removed), 'pruned_child_counts': counts,
            'generic_base_generated_paths': list(GENERATED_FILES),
            'machine_id_emptied': True, 'root_password_locked': True}


class HashWriter:
    def __init__(self, stream):
        self.stream, self.hasher, self.size = stream, hashlib.sha256(), 0

    def write(self, data):
        self.hasher.update(data)
        self.size += len(data)
        return self.stream.write(data)

    def flush(self):
        self.stream.flush()


def write_archive(path, records, identities):
    with path.open('xb') as raw:
        with gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as compressed:
            writer = HashWriter(compressed)
            with tarfile.open(fileobj=writer, mode='w|', format=tarfile.PAX_FORMAT,
                              encoding='utf-8', errors='surrogateescape') as archive:
                for name, item in records.items():
                    source = ROOT / name
                    require(identity(source.lstat()) == identities[name], 'source changed before archive: ' + name)
                    member = tarfile.TarInfo(name)
                    member.mode, member.uid, member.gid = item['mode'], item['uid'], item['gid']
                    member.uname = member.gname = ''
                    member.mtime = item['mtime_ns'] // 10**9
                    member.pax_headers['mtime'] = str(Decimal(item['mtime_ns']) / Decimal(10**9))
                    for key, value in item['xattrs'].items():
                        member.pax_headers['SCHILY.xattr.' + key] = base64.b64decode(value).decode('utf-8', 'surrogateescape')
                    kind = item['type']
                    member.type = {'dir': tarfile.DIRTYPE, 'file': tarfile.REGTYPE,
                                   'hardlink': tarfile.LNKTYPE, 'symlink': tarfile.SYMTYPE,
                                   'char': tarfile.CHRTYPE}[kind]
                    if kind in ('hardlink', 'symlink'):
                        member.linkname = item['link']
                    if kind == 'char':
                        member.devmajor, member.devminor = item['device']
                    if kind == 'file':
                        member.size = item['data']['bytes']
                        stream, before = held(source)
                        with stream:
                            require(identity(before) == identities[name], 'file changed before archive read')
                            archive.addfile(member, stream)
                            require(identity(os.fstat(stream.fileno())) == identities[name], 'file changed during archive read')
                    else:
                        archive.addfile(member)
            raw_facts = {'bytes': writer.size, 'sha256': writer.hasher.hexdigest()}
        raw.flush()
        os.fsync(raw.fileno())
    require(path.stat().st_size < MAX_COMPRESSED, 'compressed archive exceeds 512 MiB')
    return raw_facts


def verify_archive(path, expected):
    """Independent full-content readback, including every hardlink resolution."""
    actual, seen = {}, set()
    with tarfile.open(path, 'r:gz', encoding='utf-8', errors='surrogateescape') as archive:
        for member in archive:
            name = member.name
            pure = PurePosixPath(name)
            require(name == str(pure) and not pure.is_absolute() and '..' not in pure.parts
                    and name not in seen, 'unsafe/duplicate archive namespace')
            require(name == '.' if not seen else name != '.', 'explicit root first required')
            require(all(str(parent) in seen and actual[str(parent)]['type'] == 'dir' for parent in pure.parents),
                    'archive directory ancestors missing')
            seen.add(name)
            kind = ('dir' if member.isdir() else 'file' if member.isfile() else 'hardlink' if member.islnk()
                    else 'symlink' if member.issym() else 'char' if member.ischr() else None)
            require(kind is not None, 'unsupported archive member')
            require(kind == 'file' or member.size == 0, 'non-file archive data forbidden')
            require(all(key in {'mtime', 'path', 'linkpath', 'hdrcharset'} or
                        key.startswith('SCHILY.xattr.') for key in member.pax_headers),
                    'unexpected archive metadata extension')
            ns = Decimal(member.pax_headers.get('mtime', str(member.mtime))) * 10**9
            require(ns.is_finite() and ns == ns.to_integral_value(), 'invalid archive mtime')
            attrs = {key[len('SCHILY.xattr.'):]: base64.b64encode(value.encode('utf-8', 'surrogateescape')).decode('ascii')
                     for key, value in member.pax_headers.items() if key.startswith('SCHILY.xattr.')}
            require(not any(key.startswith('SCHILY.acl.') for key in member.pax_headers), 'textual ACL forbidden')
            item = {'type': kind, 'mode': member.mode, 'uid': member.uid, 'gid': member.gid,
                    'mtime_ns': int(ns), 'xattrs': attrs}
            if kind in ('file', 'hardlink'):
                if kind == 'hardlink':
                    require(member.linkname in actual and actual[member.linkname]['type'] == 'file',
                            'hardlink must reference prior first regular file')
                    item['link'] = member.linkname
                with archive.extractfile(member) as stream:
                    item['data'] = digest(stream)
            elif kind == 'symlink':
                item['link'] = member.linkname
            elif kind == 'char':
                require(member.size == 0 and name in DEVICES, 'unexpected character header')
                item['device'] = [member.devmajor, member.devminor]
            actual[name] = item
    require(actual == expected and list(actual) == list(expected), 'archive BOM/content/topology differs')
    # Consume gzip to EOF separately, validating trailer and the complete raw tar hash.
    with gzip.open(path, 'rb') as stream:
        return digest(stream)


def publish(staging, output):
    libc = ctypes.CDLL(None, use_errno=True)
    result = libc.renameat2(-100, os.fsencode(staging), -100, os.fsencode(output), 1)
    if result:
        raise OSError(ctypes.get_errno(), 'atomic exclusive export publication failed')
    fd = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def export(output: Path, configure_receipt_sha256: str) -> dict:
    environment()
    root, output = physical(ROOT), physical(output)
    require(root.parent == WORK and root.name == 'arm-root' and root != Path('/'), 'fixed source required')
    physical_directory(root)
    physical_directory(WORK)
    require(output.parent == WORK and re.fullmatch(r'export-[A-Za-z0-9][A-Za-z0-9._-]*', output.name),
            'output must be a new WORK/export-NAME sibling')
    require(not os.path.lexists(output), 'output already exists')
    disk_backed(root)
    disk_backed(output.parent)
    require(shutil.disk_usage(WORK).free >= MIN_FREE, '3 GiB free disk floor required')
    no_mounts(root)
    code_path = Path(__file__).absolute()
    code_bytes = read_checked(code_path)
    receipt_bytes = authenticate_receipt(configure_receipt_sha256)
    package_facts = inventory()
    scan(root)  # Reject special files/external hardlinks before any cleanup.
    cleanup_facts = cleanup()
    native_records, identities = scan(root)
    records = canonicalize(native_records)
    staging = Path(tempfile.mkdtemp(prefix='.export-staging-', dir=WORK))
    try:
        archive = staging / 'rootfs.tar.gz'
        raw_facts = write_archive(archive, records, identities)
        require(verify_archive(archive, records) == raw_facts, 'raw tar readback digest differs')
        current, current_ids = scan(root)
        require(current == native_records and current_ids == identities, 'source tree changed during export')
        require(read_checked(RECEIPT) == receipt_bytes, 'configure receipt changed')
        require(read_checked(code_path) == code_bytes, 'exporter code changed')
        require(inventory() == package_facts, 'inventory changed')
        with archive.open('rb') as stream:
            compressed_facts = digest(stream)
        (staging / 'rootfs-bom.json').write_text(json.dumps(records, indent=2, ensure_ascii=True) + '\n')
        report = {'schema': 1, 'phase': 'final-userspace-export', 'success': True,
                  'configure_receipt_sha256': configure_receipt_sha256,
                  'exporter_sha256': hashlib.sha256(code_bytes).hexdigest(), 'inventory': package_facts,
                  'cleanup': cleanup_facts, 'archive': compressed_facts, 'raw_tar': raw_facts,
                  'members': len(records), 'character_headers': len(DEVICES),
                  'timestamps': {'policy': 'clamp archive/BOM mtime_ns to min(native, epoch); preserve earlier nanoseconds',
                                 'epoch': ARCHIVE_EPOCH,
                                 'clamped_entries': sum(records[name]['mtime_ns'] != item['mtime_ns']
                                                        for name, item in native_records.items()),
                                 'native_source_metadata_verified': True},
                  'classification': 'interim userspace only; no physical boot, KVM, NVMe or SD-write acceptance',
                  'metadata': 'normalized archive timestamps; native same-UID-map xattrs; no original-rootfs binary-equivalence claim'}
        for name, value in (('receipt.json', report),):
            (staging / name).write_text(json.dumps(value, indent=2) + '\n')
        for path in staging.iterdir():
            with path.open('rb') as stream:
                os.fsync(stream.fileno())
        directory_fd = os.open(staging, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        publish(staging, output)
        return report
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--configure-receipt-sha256', required=True)
    args = parser.parse_args()
    try:
        result = export(args.output, args.configure_receipt_sha256)
    except (ValueError, OSError, tarfile.TarError, EOFError) as error:
        parser.exit(1, 'export-host-userspace: ' + str(error) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
