#!/usr/bin/env python3
"""Trusted host-root setup in restricted payload namespaces (never its userns)."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import time

WORK = Path('/a733-export-output')
ROOT = WORK / 'arm-root'
REPO = WORK / 'repo'
MAPPED_ROOT = 1048576
DEV = Path('/dev')
DEVICES = {'null': (1, 3), 'zero': (1, 5), 'full': (1, 7), 'random': (1, 8),
           'urandom': (1, 9), 'tty': (5, 0), 'console': (5, 1), 'ptmx': (5, 2)}
ALIASES = {'fd': '/proc/self/fd', 'stdin': '/proc/self/fd/0',
           'stdout': '/proc/self/fd/1', 'stderr': '/proc/self/fd/2'}
REQUEST = WORK / 'api-setup-request.json'
RECEIPT = WORK / 'api-mount-receipt.json'
PROFILE = Path('/a733-sources/api-profile.json')
NAMES = ('mnt', 'pid', 'net', 'user', 'uts', 'ipc', 'cgroup')
MASKS = ('/proc/kcore', '/proc/keys', '/proc/kallsyms', '/proc/sysrq-trigger', '/proc/timer_list', '/sys/firmware')
INACCESSIBLE = WORK / 'api-inaccessible-file'
FLAGS = {'ro', 'nosuid', 'nodev', 'noexec'}
REPORT = {'classification': 'trusted host-root API setup only; no package or hardware acceptance', 'commands': []}


def require(value, message):
    if not value:
        raise ValueError(message)


def authenticated(path, expected=None, owner=None):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    before = os.fstat(fd)
    if not (stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size < 1048576):
        os.close(fd)
        raise ValueError('unsafe JSON file')
    with os.fdopen(fd, 'rb') as stream:
        if owner is not None:
            require(before.st_uid == before.st_gid == owner and stat.S_IMODE(before.st_mode) == 0o444, 'unsafe JSON owner/mode')
        data = stream.read(1048576)
        after = os.fstat(stream.fileno())
        require((before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
                (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns), 'JSON changed while held')
    digest = hashlib.sha256(data).hexdigest()
    require(expected is None or re.fullmatch('[0-9a-f]{64}', expected) and digest == expected, 'JSON authentication failed')
    return json.loads(data), digest


def write_new(path, value):
    path = Path(path)
    fd, staging = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fchmod(stream.fileno(), 0o444)
            os.fsync(stream.fileno())
        libc = ctypes.CDLL(None, use_errno=True)
        result = libc.renameat2(ctypes.c_int(-100), os.fsencode(staging), ctypes.c_int(-100),
                                os.fsencode(path), ctypes.c_uint(1))
        if result != 0:
            raise OSError(ctypes.get_errno(), 'atomic no-replace JSON publication failed', str(path))
    finally:
        if os.path.lexists(staging):
            os.unlink(staging)


def namespaces():
    return {name: os.stat('/proc/self/ns/' + name).st_ino for name in NAMES}


def status():
    return {key: value.strip() for line in Path('/proc/self/status').read_text().splitlines()
            for key, value in [line.split(':', 1)] if key.startswith('Cap') or key in ('Uid', 'Gid', 'NoNewPrivs', 'NSpid')}


def mounts():
    result = []
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        left, right = line.split(' - ', 1)
        fields, fs = left.split(), right.split()
        unescape = lambda text: re.sub(r'\\([0-7]{3})', lambda match: chr(int(match[1], 8)), text)
        result.append({'id': int(fields[0]), 'parent': int(fields[1]), 'root': unescape(fields[3]),
                       'path': unescape(fields[4]), 'options': sorted(fields[5].split(',')),
                       'optional': fields[6:], 'type': fs[0], 'source': unescape(fs[1]), 'super': sorted(fs[2].split(','))})
    return result


def under(records, path):
    return [r for r in records if r['path'] == str(path) or r['path'].startswith(str(path) + '/')]


def identity(path):
    item = os.stat(path)
    return [item.st_dev, item.st_ino, stat.S_IFMT(item.st_mode)]


def require_absent(path):
    try:
        os.lstat(path)
    except FileNotFoundError:
        return
    raise ValueError('host driver PID visible')


def proc_proof(prefix='', authenticated_anchor=None):
    current_ns = os.stat('/proc/self/ns/pid').st_ino
    own_pid = os.getpid()
    require(os.readlink(prefix + '/proc/self') == str(own_pid), 'proc self link PID mismatch')
    fields = [line.split(':', 1)[1].strip() for line in Path(prefix + '/proc/self/status').read_text().splitlines() if line.startswith('Pid:')]
    require(len(fields) == 1 and fields[0] == str(own_pid), 'proc self status PID mismatch')
    require(os.stat(prefix + '/proc/self/ns/pid').st_ino == current_ns, 'API self namespace mismatch')
    root_identity = identity(prefix + '/proc')
    require(root_identity[2] == stat.S_IFDIR, 'proc root not directory')
    if authenticated_anchor is None:
        require(os.stat(prefix + '/proc/1/ns/pid').st_ino == current_ns, 'API proc PID namespace mismatch')
        pid1_check = 'verified_by_trusted_helper'
    else:
        require(authenticated_anchor['pid1_namespace_check'] == 'verified_by_trusted_helper' and
                authenticated_anchor['private_pid_inode'] == current_ns and
                authenticated_anchor['proc_root_identity'] == root_identity, 'authenticated proc anchor mismatch')
        pid1_check = 'not_queried_by_restricted_payload'
    return dict(private_pid_inode=current_ns, proc_root_identity=root_identity,
                pid1_namespace_check=pid1_check, self_pid=own_pid)


def check_api(prefix='', readonly=False, absent=(), defer_proc_masks=False, authenticated_anchor=None):
    require(set(absent) <= set(MASKS[:-1]), 'invalid absent proc masks')
    records = mounts()
    out = dict(mounts=[], masks={}, **proc_proof(prefix, authenticated_anchor))
    for leaf, kind in (('/proc', 'proc'), ('/sys', 'tmpfs')):
        exact = [r for r in records if r['path'] == prefix + leaf]
        require(len(exact) == 1 and exact[0]['type'] == kind, 'wrong API filesystem type: ' + leaf)
        selected = under(records, prefix + leaf)
        if leaf == '/sys':
            expected = {prefix + '/sys/' + name for name in ('block', 'bus', 'class', 'dev', 'devices', 'kernel')}
            sysfs = [item['path'] for item in selected if item['type'] == 'sysfs']
            require(len(sysfs) == 6 and set(sysfs) == expected, 'wrong filtered sysfs layout')
        if readonly:
            for item in selected:
                require(FLAGS <= set(item['options']), 'API mount missing restrictive flags: ' + item['path'])
                require(not any(x.startswith(('shared:', 'master:', 'propagate_from:')) for x in item['optional']), 'API propagation not private')
                required = os.ST_RDONLY | os.ST_NOSUID | os.ST_NODEV | os.ST_NOEXEC
                require(os.statvfs(item['path']).f_flag & required == required, 'API statvfs flags mismatch')
        out['mounts'] += selected
    require(set(os.listdir(prefix + '/sys/class/net')) == {'lo'}, 'unexpected API network devices')
    require(not os.listdir(prefix + '/sys/firmware'), 'firmware is visible')
    for path in MASKS:
        if defer_proc_masks and path != '/sys/firmware':
            continue
        try:
            meta = os.lstat(prefix + path)
        except FileNotFoundError:
            require(path in absent, 'unexpected absent API mask: ' + path)
            out['masks'][path] = {'absent': True, 'identity': None}
            continue
        require(path not in absent, 'absent API path became visible: ' + path)
        item = [r for r in records if r['path'] == prefix + path]
        require(len(item) == 1 and 'ro' in item[0]['options'], 'missing read-only API mask: ' + path)
        if path != '/sys/firmware':
            require(FLAGS <= set(item[0]['options']), 'proc mask flags incomplete: ' + path)
            known = os.lstat(INACCESSIBLE)
            require(stat.S_ISREG(known.st_mode) and stat.S_IMODE(known.st_mode) == 0 and known.st_size == 0 and known.st_nlink == 1, 'unsafe known-empty mask file')
            require(stat.S_ISREG(meta.st_mode) and stat.S_IMODE(meta.st_mode) == 0 and meta.st_size == 0 and
                    identity(prefix + path) == identity(INACCESSIBLE), 'proc mask is not known-empty file: ' + path)
        out['masks'][path] = {'absent': False, 'identity': identity(prefix + path), 'mount': item[0]}
    if readonly:
        require(len(out['mounts']) == 26 - len(absent), 'unexpected API mount count')
    return out


def validate_request(request):
    require(set(request) == {'nonce', 'namespace_inodes', 'host_userns_inode', 'bootstrap_root', 'host_driver_pid', 'repo_manifest_sha256'}, 'unexpected request fields')
    require(isinstance(request['repo_manifest_sha256'], str) and re.fullmatch('[0-9a-f]{64}', request['repo_manifest_sha256']), 'invalid repository digest')
    require(isinstance(request['nonce'], str) and re.fullmatch('[0-9a-f]{64}', request['nonce']), 'invalid nonce')
    require(request['bootstrap_root'] == str(ROOT), 'unexpected bootstrap root')
    expected = request['namespace_inodes']
    require(set(expected) == set(NAMES) and all(type(v) is int and v > 0 for v in expected.values()), 'invalid namespace identities')
    require(type(request['host_userns_inode']) is int and request['host_userns_inode'] > 0 and
            type(request['host_driver_pid']) is int and request['host_driver_pid'] > 1, 'invalid host identities')
    current = namespaces()
    require(os.getuid() == os.geteuid() == 0, 'host root required')
    require(current['user'] == request['host_userns_inode'] != expected['user'], 'wrong helper user namespace')
    require(all(current[n] == expected[n] for n in NAMES if n != 'user'), 'wrong entered namespaces')
    return current


def check_targets():
    records = mounts()
    for path in (ROOT, ROOT / 'proc', ROOT / 'sys', ROOT / 'dev', ROOT / 'dev/pts', ROOT / 'dev/shm', ROOT / 'a733-repo'):
        for ancestor in (path, *path.parents):
            require(stat.S_ISDIR(ancestor.lstat().st_mode), 'symlink/non-directory target ancestor')
        meta = path.lstat()
        require(meta.st_uid == meta.st_gid == MAPPED_ROOT, 'target is not mapped-root owned')
        if path not in (ROOT, ROOT / 'dev'):
            require(not os.listdir(path) and not under(records, path), 'API target not empty/unmounted')
    require(not under(records, ROOT / 'dev'), 'device target already mounted')
    require(set(os.listdir(ROOT / 'dev')) == set(DEVICES) | set(ALIASES) | {'pts', 'shm'}, 'unexpected device target entry')
    for name in DEVICES:
        placeholder(ROOT / 'dev' / name, MAPPED_ROOT)
    check_aliases(ROOT / 'dev')


def real_directory(path, owner):
    for ancestor in (path, *path.parents):
        require(stat.S_ISDIR(ancestor.lstat().st_mode), 'symlink/non-directory ancestor')
    meta = path.lstat()
    require(meta.st_uid == meta.st_gid == owner, 'directory owner mismatch')


def placeholder(path, owner):
    meta = path.lstat()
    require(stat.S_ISREG(meta.st_mode) and stat.S_IMODE(meta.st_mode) == 0o666 and
            meta.st_uid == meta.st_gid == owner and meta.st_nlink == 1 and meta.st_size == 0,
            'unsafe device placeholder: ' + str(path))


def check_aliases(directory):
    for name, destination in ALIASES.items():
        require(stat.S_ISLNK((directory / name).lstat().st_mode) and
                os.readlink(directory / name) == destination, 'unsafe device alias')


def walk_error(error):
    raise error


def mount_policy(records, path, attr_set, recursive=False):
    selected = under(records, path)
    exact = [r for r in selected if r['path'] == str(path)]
    require(len(exact) == 1 and (recursive or len(selected) == 1), 'unexpected mount topology: ' + str(path))
    required = {name for bit, name in ((1, 'ro'), (2, 'nosuid'), (4, 'nodev'), (8, 'noexec')) if attr_set & bit}
    for record in selected:
        options = set(record['options'])
        require(required <= options and ('ro' in options) == bool(attr_set & 1) and
                ('nodev' in options) == bool(attr_set & 4), 'mount policy mismatch: ' + str(path))
        require(not any(x.startswith(('shared:', 'master:', 'propagate_from:')) for x in record['optional']), 'mount is not private')
        flags = os.statvfs(record['path']).f_flag
        expected = sum(value for bit, value in ((1, os.ST_RDONLY), (2, os.ST_NOSUID), (4, os.ST_NODEV), (8, os.ST_NOEXEC)) if attr_set & bit)
        require(flags & (os.ST_RDONLY | os.ST_NOSUID | os.ST_NODEV | os.ST_NOEXEC) == expected, 'statvfs policy mismatch')
    return selected


def check_repository(expected_sha, path=None, readonly=False, owner=MAPPED_ROOT):
    path = REPO if path is None else Path(path)
    require(path in (REPO, ROOT / 'a733-repo'), 'unreviewed repository path')
    real_directory(path, owner)
    manifest, digest = authenticated(path / 'manifest.json', expected_sha, owner=owner)
    require(manifest.get('schema') == 1 and manifest.get('status') == 'verified' and
            manifest.get('package_count') == 220, 'unverified repository manifest')
    bom = manifest.get('files')
    require(isinstance(bom, list) and len(bom) == 669, 'unexpected repository BOM count')
    require(hashlib.sha256(json.dumps(bom, sort_keys=True, separators=(',', ':')).encode()).hexdigest() == manifest.get('bom_sha256'), 'BOM digest mismatch')
    expected = {'manifest.json'}
    for entry in bom:
        require(isinstance(entry, dict) and set(entry) == {'path', 'bytes', 'sha256'}, 'unsafe BOM entry')
        name = entry['path']
        require(isinstance(name, str) and name and not name.startswith('/') and
                all(part not in ('', '.', '..') for part in name.split('/')) and name not in expected,
                'unsafe or duplicate BOM path')
        require(type(entry['bytes']) is int and entry['bytes'] >= 0 and isinstance(entry['sha256'], str) and re.fullmatch('[0-9a-f]{64}', entry['sha256']), 'unsafe BOM digest/size')
        expected.add(name)
        file = path / name
        for ancestor in file.parents:
            if ancestor == path:
                break
            real_directory(ancestor, owner)
        fd = os.open(file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            before = os.fstat(stream.fileno())
            require(stat.S_ISREG(before.st_mode) and before.st_uid == before.st_gid == owner and
                    before.st_nlink == 1 and stat.S_IMODE(before.st_mode) == 0o444 and before.st_size == entry['bytes'], 'unsafe repository file')
            sha = hashlib.sha256()
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                sha.update(block)
            after = os.fstat(stream.fileno())
            stable = lambda item: (item.st_dev, item.st_ino, item.st_mode, item.st_uid, item.st_gid,
                                   item.st_nlink, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
            require(stable(after) == stable(before) and sha.hexdigest() == entry['sha256'], 'repository file changed/hash mismatch')
    actual = set()
    for directory, dirs, files in os.walk(path, followlinks=False, onerror=walk_error):
        for name in dirs + files:
            node = Path(directory) / name
            meta = node.lstat()
            require(meta.st_uid == meta.st_gid == owner, 'repository node owner mismatch')
            if stat.S_ISDIR(meta.st_mode):
                continue
            require(stat.S_ISREG(meta.st_mode), 'repository symlink/special file')
            actual.add(str(node.relative_to(path)))
    require(actual == expected, 'unexpected/missing repository files')
    records = mounts()
    selected = mount_policy(records, path, 15, recursive=True) if readonly else under(records, path)
    if not readonly:
        require(not selected, 'repository initially mounted')
        enclosing = [r for r in records if str(path) == r['path'] or str(path).startswith(r['path'].rstrip('/') + '/')]
        require(enclosing and max(enclosing, key=lambda r: len(r['path']))['type'] in ('ext4', 'xfs', 'btrfs'), 'repository source is not a physical filesystem')
    return dict(manifest_sha256=digest, bom_sha256=manifest['bom_sha256'], file_count=len(actual), mounts=selected,
                identity=identity(path), scope='fixed source repository self-bind and target mirror; all BOM files plus manifest')


def check_devices(prefix='', authenticated_anchor=None):
    directory = Path(prefix) / 'dev' if prefix else DEV
    records = mounts()
    result = dict(mounts=under(records, directory), devices={}, filesystems={})
    if not prefix:
        real_directory(directory, MAPPED_ROOT)
        for leaf, kind in (('', 'tmpfs'), ('pts', 'devpts'), ('shm', 'tmpfs')):
            path = directory / leaf
            exact = [r for r in records if r['path'] == str(path)]
            require(len(exact) == 1 and exact[0]['type'] == kind and exact[0]['source'] == kind and exact[0]['root'] == '/', 'wrong source device filesystem')
            require('rw' in exact[0]['options'] and 'nosuid' in exact[0]['options'], 'unsafe source device flags')
            supers = set(exact[0]['super'])
            expected = {'gid=1048581', 'mode=620', 'ptmxmode=666'} if leaf == 'pts' else {'uid=1048576', 'gid=1048576'}
            require(expected <= supers, 'wrong source device mount ownership/options')
        for base, dirs, files in os.walk(directory, followlinks=False, onerror=walk_error):
            for name in dirs + files:
                node = Path(base) / name
                meta = node.lstat()
                require(not stat.S_ISBLK(meta.st_mode) and not (stat.S_ISCHR(meta.st_mode) and os.major(meta.st_rdev) == 10 and os.minor(meta.st_rdev) == 232), 'block/KVM device in source namespace')
                require(name != 'kvm', 'KVM alias in source namespace')
    else:
        owner = 0 if authenticated_anchor is not None else MAPPED_ROOT
        real_directory(directory, owner)
        placeholder(directory / 'console', owner)
        check_aliases(directory)
        require(set(os.listdir(directory)) == set(DEVICES) | set(ALIASES) | {'pts', 'shm'}, 'unexpected target device entry')
        allowed = {str(directory / name) for name in DEVICES if name != 'console'} | {str(directory / 'pts'), str(directory / 'shm')}
        require(all(r['path'] in allowed or r['path'].startswith((str(directory / 'pts') + '/', str(directory / 'shm') + '/'))
                    for r in result['mounts']), 'unexpected target device mount')
    for name, numbers in DEVICES.items():
        if name == 'console':
            continue
        path = directory / ('pts/ptmx' if name == 'ptmx' and not prefix else name)
        meta = path.lstat()
        require(stat.S_ISCHR(meta.st_mode) and (os.major(meta.st_rdev), os.minor(meta.st_rdev)) == numbers, 'wrong device type/numbers: ' + name)
        result['devices'][name] = dict(numbers=list(numbers), identity=identity(path))
        if prefix:
            mount_policy(records, path, 11)
    for name, kind, attributes in (('pts', 'devpts', 10), ('shm', 'tmpfs', 14)):
        path = directory / name
        require(stat.S_ISDIR(path.lstat().st_mode), 'unsafe device directory')
        exact = [r for r in records if r['path'] == str(path)]
        require(len(exact) == 1 and exact[0]['type'] == kind and exact[0]['root'] == '/', 'wrong device subtree filesystem')
        result['filesystems'][name] = dict(identity=identity(path), mount=exact[0])
        if prefix:
            mount_policy(records, path, attributes, recursive=True)
    if authenticated_anchor is not None:
        require(result == authenticated_anchor, 'authenticated device anchor mismatch')
    return result


def setup_repository(libc=None):
    libc = api_libc(libc)
    bind_restrict(libc, str(REPO), str(REPO), True, 15)
    bind_restrict(libc, str(REPO), str(ROOT / 'a733-repo'), True, 15)


def setup_devices(libc=None):
    libc = api_libc(libc)
    for name in DEVICES:
        if name != 'console':
            source = DEV / ('pts/ptmx' if name == 'ptmx' else name)
            bind_restrict(libc, str(source), str(ROOT / 'dev' / name), False, 11)
    for name, attributes in (('pts', 10), ('shm', 14)):
        bind_restrict(libc, str(DEV / name), str(ROOT / 'dev' / name), True, attributes)


class MountAttr(ctypes.Structure):
    _fields_ = [('attr_set', ctypes.c_uint64), ('attr_clr', ctypes.c_uint64),
                ('propagation', ctypes.c_uint64), ('userns_fd', ctypes.c_uint64)]


def syscall_checked(libc, operation, *args):
    result = operation(*args)
    require(result == 0, 'mount syscall failed errno=' + str(ctypes.get_errno()))


def api_libc(libc=None):
    require(os.uname().machine == 'x86_64', 'mount_setattr syscall ABI requires x86_64')
    return libc or ctypes.CDLL(None, use_errno=True)


def bind_restrict(libc, source, target, recursive, attr_set=15):
    require(type(attr_set) is int and attr_set in (15, 11, 10, 14), 'unreviewed mount attributes')
    encoded = os.fsencode(target)
    recurse = 16384 if recursive else 0
    for description, src, flags in (('bind', os.fsencode(source), 4096 | recurse), ('private', None, 1 << 18 | recurse)):
        REPORT['commands'].append(dict(syscall='mount', operation=description, source=source if src else None, target=target, flags=flags))
        syscall_checked(libc, libc.mount, src, encoded, None, ctypes.c_ulong(flags), None)
    attr_flags = 0x8000 if recursive else 0
    REPORT['commands'].append(dict(syscall='mount_setattr', nr=442, target=target, flags=attr_flags, attr_set=attr_set))
    attr = MountAttr(attr_set, 0, 0, 0)
    syscall_checked(libc, libc.syscall, ctypes.c_long(442), ctypes.c_int(-100), encoded,
                    ctypes.c_uint(attr_flags), ctypes.byref(attr), ctypes.c_size_t(ctypes.sizeof(attr)))


def proc_baseline():
    result = {}
    for path in MASKS[:-1]:
        try:
            meta = os.lstat(path)
        except FileNotFoundError:
            result[path] = {'absent': True}
        else:
            result[path] = dict(absent=False, mode=meta.st_mode, uid=meta.st_uid, gid=meta.st_gid,
                                size=meta.st_size, device=meta.st_dev, inode=meta.st_ino)
    return result


def setup_masks(baseline, libc=None):
    libc = api_libc(libc)
    require(set(baseline) == set(MASKS[:-1]), 'invalid proc baseline paths')
    require(proc_baseline() == baseline, 'proc mask targets changed since baseline')
    require(all(item['absent'] or stat.S_ISREG(item['mode']) for item in baseline.values()), 'proc mask target not real regular file')
    fd = os.open(INACCESSIBLE, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0)
    with os.fdopen(fd, 'wb') as stream:
        os.fchmod(stream.fileno(), 0)
        os.fsync(stream.fileno())
        meta = os.fstat(stream.fileno())
        require(meta.st_uid == meta.st_gid == 0 and meta.st_size == 0 and stat.S_ISREG(meta.st_mode), 'unsafe mask owner/type/size')
    absent = [path for path, item in baseline.items() if item['absent']]
    for path in MASKS[:-1]:
        if path not in absent:
            bind_restrict(libc, str(INACCESSIBLE), path, recursive=False)
    return absent


def setup_api(libc=None):
    libc = api_libc(libc)
    for source in ('/proc', '/sys'):
        bind_restrict(libc, source, str(ROOT) + source, recursive=True)


def main():
    require(len(sys.argv) == 3 and sys.argv[1] == str(REQUEST), 'fixed request path and SHA256 required')
    request, sha = authenticated(REQUEST, sys.argv[2], owner=0)
    REPORT.update(request=request, request_sha256=sha, started=time.time(), helper_status=status())
    REPORT['helper_namespaces'] = validate_request(request)
    require(os.getcwd() == '/', 'helper cwd must be namespace root')
    helper = Path(__file__).lstat()
    require(stat.S_ISREG(helper.st_mode) and helper.st_uid == helper.st_gid == 0 and
            stat.S_IMODE(helper.st_mode) == 0o444 and helper.st_nlink == 1, 'unsafe trusted helper file')
    profile, _ = authenticated(PROFILE)
    require(os.statvfs(PROFILE).f_flag & os.ST_RDONLY, 'API profile must be read-only')
    fields = ('nonce', 'host_userns_inode', 'host_driver_pid', 'repo_manifest_sha256')
    require(set(profile) == set(fields) and all(request[k] == profile[k] for k in fields), 'request/profile mismatch')
    check_targets()
    expected_sha = request['repo_manifest_sha256']
    REPORT['repository_before'] = check_repository(expected_sha)
    REPORT['devices_before'] = check_devices()
    REPORT['console_identity'] = identity(ROOT / 'dev/console')
    REPORT['baseline_api'] = check_api(defer_proc_masks=True)
    REPORT['baseline_mounts'] = mounts()
    REPORT['baseline_proc_paths'] = proc_baseline()
    setup_repository()
    REPORT['repository_source'] = check_repository(expected_sha, readonly=True)
    REPORT['repository_destination'] = check_repository(expected_sha, ROOT / 'a733-repo', readonly=True)
    setup_devices()
    REPORT['devices'] = check_devices(str(ROOT))
    require(identity(ROOT / 'dev/console') == REPORT['console_identity'], 'console placeholder changed')
    for name in REPORT['devices_before']['devices']:
        require(REPORT['devices_before']['devices'][name] == REPORT['devices']['devices'][name], 'copied device identity mismatch')
    for name in ('pts', 'shm'):
        require(REPORT['devices_before']['filesystems'][name]['identity'] == REPORT['devices']['filesystems'][name]['identity'], 'copied device filesystem identity mismatch')
    absent = setup_masks(REPORT['baseline_proc_paths'])
    REPORT['proc_absent'] = absent
    source = check_api(absent=absent)
    REPORT['source_before_copy'] = source
    setup_api()
    target = check_api(str(ROOT), readonly=True, absent=absent)
    REPORT.update(source_after=check_api(absent=absent), destination=target)
    REPORT['devices_after'] = check_devices()
    require(REPORT['devices_before'] == REPORT['devices_after'], 'source devices changed')
    require(REPORT['repository_source'] == check_repository(expected_sha, readonly=True), 'source repository changed')
    require(REPORT['repository_destination'] == check_repository(expected_sha, ROOT / 'a733-repo', readonly=True), 'target repository changed')
    require(REPORT['repository_before']['identity'] == REPORT['repository_source']['identity'], 'source repository identity changed')
    require(REPORT['source_after'] == source, 'source API flags/records changed')
    require(all(source['masks'][p]['identity'] == target['masks'][p]['identity'] for p in MASKS), 'recursive copy lost API masks')


if __name__ == '__main__':
    try:
        main()
        REPORT['success'] = True
    except Exception as error:
        REPORT.update(success=False, error=repr(error))
        raise
    finally:
        REPORT['finished'] = time.time()
        write_new(RECEIPT, REPORT)
