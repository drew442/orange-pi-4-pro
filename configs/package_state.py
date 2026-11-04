"""Immutable, selected-root package metadata format and host build helpers.

Helpers operate only on private, extracted regular-file trees. No mounts or
package tools are invoked. Files are relocated with rename to retain metadata.
"""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat

PROFILE = 'a733-package-state-v1'
ROOT = 'usr/lib/orangepi-hypervisor/package-state'
FORMAT = b'a733-package-state-v1\n'
COMPONENTS = {
    'dpkg': ('var/lib/dpkg', 'directory'),
    'apt': ('var/lib/apt', 'directory'),
    'debconf': ('var/cache/debconf', 'directory'),
    'ucf': ('var/lib/ucf', 'directory'),
    'pam': ('var/lib/pam', 'directory'),
    'shells': ('var/lib/shells.state', 'file'),
    'systemd-helper': ('var/lib/systemd/deb-systemd-helper-enabled', 'directory'),
    'systemd-catalog': ('var/lib/systemd/catalog', 'directory'),
}
MANIFEST_LIMIT = 1024**2
CACHE_FILES = ('var/cache/apt/pkgcache.bin', 'var/cache/apt/srcpkgcache.bin',
               'var/cache/ldconfig/aux-cache')


def validate_profile(value):
    if (not isinstance(value, dict) or set(value) != {'profile', 'manifest'} or
            value['profile'] != PROFILE):
        raise ValueError('incompatible package-state profile')
    record = value['manifest']
    if (not isinstance(record, dict) or set(record) != {'bytes', 'sha256'} or
            type(record['bytes']) is not int or not 0 < record['bytes'] <= MANIFEST_LIMIT or
            not isinstance(record['sha256'], str) or
            not re.fullmatch('[0-9a-f]{64}', record['sha256'])):
        raise ValueError('invalid package-state manifest record')
    return value


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate package-state manifest field')
        result[key] = value
    return result


def _relative_path(value):
    return (isinstance(value, str) and value != '' and '\x00' not in value and
            not value.startswith('/') and '..' not in PurePosixPath(value).parts and
            str(PurePosixPath(value)) == value)


def validate_manifest(data, profile):
    """Authenticate bounded descriptor bytes before parsing their fixed schema."""
    validate_profile(profile)
    if (not isinstance(data, bytes) or len(data) != profile['manifest']['bytes'] or
            _record(data) != profile['manifest']):
        raise ValueError('package-state manifest bytes/hash mismatch')
    try:
        value = json.loads(data, object_pairs_hook=_unique_fields)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError('invalid package-state manifest JSON') from error
    if (not isinstance(value, dict) or set(value) != {'schema', 'profile', 'components'} or
            type(value['schema']) is not int or value['schema'] != 1 or
            value['profile'] != PROFILE or not isinstance(value['components'], dict) or
            set(value['components']) != set(COMPONENTS)):
        raise ValueError('invalid package-state manifest schema')
    for name, (target, kind) in COMPONENTS.items():
        component = value['components'][name]
        if (not isinstance(component, dict) or set(component) != {'target', 'kind', 'entries'} or
                component['target'] != target or component['kind'] != kind or
                not isinstance(component['entries'], list) or
                not 0 < len(component['entries']) <= 16384):
            raise ValueError('invalid package-state component descriptor')
        paths = {}
        for entry in component['entries']:
            if not isinstance(entry, dict):
                raise ValueError('invalid package-state entry')
            entry_type = entry.get('type')
            extra = {'file': {'bytes', 'sha256'}, 'directory': set(),
                     'symlink': {'link_target'}}.get(entry_type) if isinstance(entry_type, str) else None
            if (extra is None or set(entry) != {'path', 'type', 'mode', 'uid', 'gid'} | extra or
                    not _relative_path(entry['path']) or entry['path'] in paths or
                    not isinstance(entry['mode'], str) or not re.fullmatch('[0-7]{4}', entry['mode']) or
                    any(type(entry[field]) is not int or not 0 <= entry[field] <= 2**32 - 1
                        for field in ('uid', 'gid'))):
                raise ValueError('invalid package-state entry fields')
            if entry_type == 'file':
                if (type(entry['bytes']) is not int or entry['bytes'] < 0 or
                        not isinstance(entry['sha256'], str) or
                        not re.fullmatch('[0-9a-f]{64}', entry['sha256'])):
                    raise ValueError('invalid package-state file record')
            elif entry_type == 'symlink':
                link = entry['link_target']
                if (not isinstance(link, str) or not link or '\x00' in link or link.startswith('/')):
                    raise ValueError('invalid package-state link target')
                depth = len(PurePosixPath(entry['path']).parent.parts)
                for part in PurePosixPath(link).parts:
                    if part == '..':
                        depth -= 1
                        if depth < 0:
                            raise ValueError('escaping package-state link target')
                    elif part != '.':
                        depth += 1
            paths[entry['path']] = entry_type
        if paths.get('.') != kind:
            raise ValueError('package-state component root kind mismatch')
        for path in paths:
            if path != '.' and paths.get(str(PurePosixPath(path).parent)) != 'directory':
                raise ValueError('package-state entry lacks a directory parent')
    return value


def _record(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _safe_directory(path):
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or
            info.st_mode & 0o7022):
        raise ValueError(f'unsafe real package-state directory: {path}')
    return info


def _ancestors(root, relative, missing_ok=False):
    if root.resolve() != root:
        raise ValueError('symlink package-state root ancestor')
    _safe_directory(root)
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        try:
            _safe_directory(current)
        except FileNotFoundError:
            if missing_ok:
                return False
            raise
    return True


def _entries(source):
    """Inventory without following links; reject links escaping this component."""
    records = []
    def visit(path, relative):
        info = path.lstat()
        item = {'path': relative, 'mode': f'{stat.S_IMODE(info.st_mode):04o}',
                'uid': info.st_uid, 'gid': info.st_gid}
        if stat.S_ISDIR(info.st_mode):
            item['type'] = 'directory'
            records.append(item)
            for child in sorted(path.iterdir()):
                visit(child, child.name if relative == '.' else relative + '/' + child.name)
        elif stat.S_ISREG(info.st_mode):
            digest = hashlib.sha256()
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as stream:
                if _identity(os.fstat(stream.fileno())) != _identity(info):
                    raise ValueError(f'package-state file changed during inventory: {path}')
                for data in iter(lambda: stream.read(1024**2), b''):
                    digest.update(data)
            item.update(type='file', bytes=info.st_size, sha256=digest.hexdigest())
            records.append(item)
        elif stat.S_ISLNK(info.st_mode):
            target = os.readlink(path)
            # Relocation changes parent paths, so only component-local links
            # preserve their meaning and remain confined to the immutable tree.
            if not target or target.startswith('/'):
                raise ValueError(f'escaping package-state symlink: {path}')
            depth = len(PurePosixPath(relative).parent.parts)
            for part in PurePosixPath(target).parts:
                if part == '..':
                    depth -= 1
                    if depth < 0:
                        raise ValueError(f'escaping package-state symlink: {path}')
                elif part != '.':
                    depth += 1
            item.update(type='symlink', link_target=target)
            records.append(item)
        else:
            raise ValueError(f'unsupported package-state object: {path}')
    visit(source, '.')
    # resolve() also detects chained links that escape or form loops. It is
    # used for validation only; never for choosing a write destination.
    for item in records:
        if item['type'] == 'symlink':
            try:
                (source / item['path']).resolve().relative_to(source.resolve())
            except (ValueError, RuntimeError) as error:
                raise ValueError('escaping or cyclic package-state symlink') from error
    return records


def _write_new(path, data, mode):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        os.fchmod(stream.fileno(), mode)


def snapshot(root):
    """Preflight all components, then relocate them into a new private tree.

    On I/O failure the staging root is unusable and must be discarded. A failed
    or completed output cannot be overwritten or used for a second snapshot.
    """
    root = Path(root).absolute()
    _safe_directory(root)
    _ancestors(root, str(PurePosixPath(ROOT).parent))
    destination = root / ROOT
    if os.path.lexists(destination):
        raise ValueError('package-state destination already exists')
    components = {}
    originals = {}
    for name, (target, kind) in COMPONENTS.items():
        _ancestors(root, str(PurePosixPath(target).parent))
        source = root / target
        info = source.lstat()
        expected = stat.S_ISDIR if kind == 'directory' else stat.S_ISREG
        if (not expected(info.st_mode) or info.st_uid != os.geteuid() or
                info.st_mode & 0o7022):
            raise ValueError(f'unsafe package-state source: {source}')
        if info.st_dev != destination.parent.stat().st_dev:
            raise ValueError('package-state rename requires the same filesystem')
        originals[name] = info
        components[name] = {'target': target, 'kind': kind, 'entries': _entries(source)}
    data = (json.dumps({'schema': 1, 'profile': PROFILE, 'components': components},
                       sort_keys=True, separators=(',', ':')) + '\n').encode()
    if len(data) > MANIFEST_LIMIT:
        raise ValueError('package-state manifest exceeds limit')
    profile = {'profile': PROFILE, 'manifest': _record(data)}
    validate_manifest(data, profile)
    destination.mkdir(mode=0o700)
    for name, (target, kind) in COMPONENTS.items():
        _ancestors(root, ROOT)
        if os.path.lexists(destination / name):
            raise ValueError('package-state component destination already exists')
        _ancestors(root, str(PurePosixPath(target).parent))
        source = root / target
        if _identity(source.lstat()) != _identity(originals[name]) or _entries(source) != components[name]['entries']:
            raise ValueError('package-state source changed after preflight')
        source.rename(destination / name)
        if kind == 'directory':
            source.mkdir(mode=0o755)
            source.chmod(0o755)
        else:
            _write_new(source, b'', 0o644)
    _ancestors(root, ROOT)
    _write_new(destination / 'FORMAT', FORMAT, 0o444)
    _write_new(destination / 'manifest.json', data, 0o444)
    destination.chmod(0o755)
    return profile


def prune_caches(root):
    """Remove only known regenerable caches, after checking every candidate."""
    root = Path(root).absolute()
    candidates = []
    for relative in CACHE_FILES:
        if not _ancestors(root, str(PurePosixPath(relative).parent), missing_ok=True):
            continue
        path = root / relative
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f'cache must be a regular non-symlink file: {path}')
        candidates.append((relative, info, _entries(path)[0]))
    removed = []
    for relative, info, record in candidates:
        _ancestors(root, str(PurePosixPath(relative).parent))
        path = root / relative
        if _identity(path.lstat()) != _identity(info):
            raise ValueError('cache changed after preflight')
        path.unlink()
        removed.append({'path': relative, 'bytes': record['bytes'], 'sha256': record['sha256']})
    return removed
