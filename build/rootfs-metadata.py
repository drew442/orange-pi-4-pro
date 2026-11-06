#!/usr/bin/env python3
"""Optional build-only, fail-closed fakeroot metadata restoration.

Run the caller and all its image tools in the same fakeroot session inside the
frozen builder's existing read-only, private-UID, capability-dropped namespace.
This module neither starts fakeroot nor grants privileges.
"""
import base64
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import stat
import subprocess
import tarfile

PROFILE = 'fakeroot-raw-xattrs-v1'
REPO = Path(__file__).resolve().parent.parent
LOCK = REPO / 'configs/builder-replay-packages-20261106.lock.tsv'
DEVICES = {'dev/null': (1, 3), 'dev/console': (5, 1), 'dev/urandom': (1, 9),
           'dev/tty': (5, 0), 'dev/ptmx': (5, 2), 'dev/full': (1, 7),
           'dev/zero': (1, 5), 'dev/random': (1, 8)}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(stream):
    result = hashlib.sha256()
    size = 0
    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
        result.update(chunk)
        size += len(chunk)
    return {'bytes': size, 'sha256': result.hexdigest()}


def validate_profile():
    require(os.environ.get('FAKEROOTKEY') and
            'libfakeroot' in os.environ.get('LD_PRELOAD', '') and
            os.getuid() == os.geteuid() == 0, 'profile requires a shared fakeroot UID 0 session')
    release = platform.freedesktop_os_release()
    require(release.get('ID') == 'ubuntu' and release.get('VERSION_ID') == '22.04'
            and platform.machine() == 'x86_64', 'profile requires frozen Ubuntu 22.04 amd64')
    selected = {'fakeroot', 'libfakeroot:amd64'}
    expected = {}
    for line in LOCK.read_text().splitlines():
        fields = line.split('\t')
        if len(fields) == 3 and fields[0] in selected:
            expected[fields[0]] = fields[1:]
    require(expected == {name: ['1.28-1ubuntu1', 'amd64'] for name in selected},
            'fakeroot replay lock differs')
    result = subprocess.run(['dpkg-query', '-W', '-f=${binary:Package}\t${Version}\t${Architecture}\t${db:Status-Status}\n',
                             *sorted(selected)], check=True, capture_output=True, text=True)
    actual = {}
    for line in result.stdout.splitlines():
        fields = line.split('\t')
        require(len(fields) == 4 and fields[3] == 'installed' and fields[0] not in actual,
                'invalid fakeroot package status')
        actual[fields[0]] = fields[1:3]
    require(actual == expected, 'fakeroot packages differ from replay lock')
    return {name: {'version': value[0], 'architecture': value[1]}
            for name, value in sorted(actual.items())}


def physical_path(path):
    path = Path(os.path.abspath(path))
    require(not any(path == p or p in path.parents for p in map(Path, ('/dev', '/proc', '/sys'))),
            'pseudo filesystem forbidden')
    for part in (path, *path.parents):
        require(not part.is_symlink(), 'symlink in physical path: ' + str(part))
    return path


def identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def source_records(stream, namespace_validator):
    records = {}
    audit = dict.fromkeys(('members', 'character_nodes', 'raw_attributes', 'members_with_raw_xattrs',
                          'textual_acl', 'maximum_name_list_bytes', 'maximum_name_value_bytes',
                          'unpacked_regular_bytes'), 0)
    stream.seek(0)
    with tarfile.open(fileobj=stream, mode='r:*') as source:
        entries = namespace_validator(source.getmembers())
        require(PurePosixPath('.') in entries, 'explicit archive root required')
        for path, member in entries.items():
            require(all(parent in entries and entries[parent].isdir() for parent in path.parents),
                    'explicit directory ancestors required: ' + str(path))
            name = str(path)
            require(member.isdir() or member.isfile() or member.issym() or member.islnk() or member.ischr(),
                    'forbidden archive type: ' + name)
            if member.ischr():
                require(name in DEVICES and (member.devmajor, member.devminor) == DEVICES[name],
                        'unapproved character node: ' + name)
                require(member.uid == member.gid == 0 and member.mode == 0o666,
                        'unapproved character metadata: ' + name)
                audit['character_nodes'] += 1
            attrs = {key[len('SCHILY.xattr.'):]: value.encode('utf-8', 'surrogateescape')
                     for key, value in member.pax_headers.items() if key.startswith('SCHILY.xattr.')}
            names_size = sum(len(key.encode('utf-8', 'surrogateescape')) + 1 for key in attrs)
            require(names_size < 1024, 'fakeroot xattr name-list limit: ' + name)
            audit['maximum_name_list_bytes'] = max(audit['maximum_name_list_bytes'], names_size)
            for key, value in attrs.items():
                raw_name = key.encode('utf-8', 'surrogateescape')
                require(raw_name and b'\0' not in raw_name, 'invalid xattr name')
                size = len(raw_name) + 1 + len(value)
                require(size < 1024, 'fakeroot xattr value limit: ' + name)
                audit['maximum_name_value_bytes'] = max(audit['maximum_name_value_bytes'], size)
            for key in member.pax_headers:
                if key.startswith('SCHILY.acl.'):
                    audit['textual_acl'] += 1
                    counterpart = {'SCHILY.acl.access': 'system.posix_acl_access',
                                   'SCHILY.acl.default': 'system.posix_acl_default'}.get(key)
                    require(counterpart in attrs, 'textual ACL without raw counterpart: ' + name)
            kind = ('dir' if member.isdir() else 'file' if member.isfile() else
                    'symlink' if member.issym() else 'hardlink' if member.islnk() else 'char')
            ns = Decimal(member.pax_headers.get('mtime', str(member.mtime))) * 10**9
            require(ns.is_finite() and ns == ns.to_integral_value(), 'invalid nanosecond timestamp')
            item = {'type': kind, 'mode': member.mode, 'uid': member.uid, 'gid': member.gid,
                    'mtime_ns': int(ns), 'xattrs': {key: base64.b64encode(value).decode('ascii')
                                                  for key, value in attrs.items()}}
            if member.isfile() or member.islnk():
                with source.extractfile(member) as content:
                    item['data'] = digest(content)
                if member.isfile():
                    audit['unpacked_regular_bytes'] += item['data']['bytes']
                else:
                    item['link'] = str(PurePosixPath(member.linkname))
            elif member.issym():
                item['link'] = member.linkname
            elif member.ischr():
                item['device'] = [member.devmajor, member.devminor]
            records[name] = item
            audit['members'] += 1
            audit['raw_attributes'] += len(attrs)
            audit['members_with_raw_xattrs'] += bool(attrs)
    return records, audit


def compare_tree(root, members):
    found = {'.'}
    def visit(directory):
        with os.scandir(directory) as children:
            for child in children:
                found.add(str(Path(child.path).relative_to(root)))
                if child.is_dir(follow_symlinks=False):
                    visit(child.path)
    visit(root)
    require(found == set(members), 'restored namespace differs')
    expected_groups, actual_groups = {}, {}
    kinds = {'dir': stat.S_ISDIR, 'file': stat.S_ISREG, 'hardlink': stat.S_ISREG,
             'symlink': stat.S_ISLNK, 'char': stat.S_ISCHR}
    for name, item in members.items():
        path = root / name
        actual = path.lstat()
        require(kinds[item['type']](actual.st_mode), 'restored type differs: ' + name)
        require((stat.S_IMODE(actual.st_mode), actual.st_uid, actual.st_gid, actual.st_mtime_ns) ==
                (item['mode'], item['uid'], item['gid'], item['mtime_ns']), 'restored metadata differs: ' + name)
        attrs = {key: base64.b64encode(os.getxattr(path, key, follow_symlinks=False)).decode('ascii')
                 for key in os.listxattr(path, follow_symlinks=False)}
        require(attrs == item['xattrs'], 'restored xattrs differ: ' + name)
        if item['type'] in ('file', 'hardlink'):
            expected_groups.setdefault(item.get('link', name), []).append(name)
            actual_groups.setdefault((actual.st_dev, actual.st_ino), []).append(name)
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, 'rb') as content:
                require(digest(content) == item['data'], 'restored file data differs: ' + name)
        elif item['type'] == 'symlink':
            require(os.readlink(path) == item['link'], 'restored symlink differs: ' + name)
        elif item['type'] == 'char':
            require([os.major(actual.st_rdev), os.minor(actual.st_rdev)] == item['device'],
                    'restored device differs: ' + name)
    require(sorted(sorted(group) for group in expected_groups.values()) ==
            sorted(sorted(group) for group in actual_groups.values()), 'restored hardlink topology differs')


def extract(archive, root, expected, namespace_validator):
    packages = validate_profile()
    require(isinstance(expected, dict) and set(expected) == {'bytes', 'sha256'},
            'authenticated producer archive record required')
    archive, root = physical_path(archive), physical_path(root)
    require(archive != root and root not in archive.parents and archive not in root.parents,
            'archive and extraction root overlap')
    require(stat.S_ISDIR(root.lstat().st_mode) and not any(root.iterdir()),
            'extraction root must be an empty existing physical directory')
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        root_identity = (os.fstat(root_fd).st_dev, os.fstat(root_fd).st_ino)
        def stable_root():
            physical_path(root)
            require((root.lstat().st_dev, root.lstat().st_ino) == root_identity,
                    'extraction root replaced')
        fd = os.open(archive, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            before = os.fstat(fd)
            require(stat.S_ISREG(before.st_mode), 'archive must be regular')
            def stable_source():
                physical_path(archive)
                require(identity(os.fstat(fd)) == identity(before) == identity(archive.lstat()),
                        'archive changed during extraction')
                stream.seek(0)
                require(digest(stream) == expected, 'archive hash or size differs from producer')
            stable_source()
            records, audit = source_records(stream, namespace_validator)
            stable_source()
            stable_root()
            require(not any(root.iterdir()), 'extraction root no longer empty')
            stream.seek(0)
            subprocess.run(['tar', '--extract', '--file', '/proc/self/fd/' + str(fd),
                            '--directory', '/proc/self/fd/' + str(root_fd), '--numeric-owner',
                            '--same-owner', '--same-permissions', '--no-acls', '--no-xattrs'],
                           pass_fds=(fd, root_fd), check=True)
            stable_root()
            stable_source()
            for name, item in records.items():
                for key, value in item['xattrs'].items():
                    os.setxattr(root / name, key, base64.b64decode(value), follow_symlinks=False)
            compare_tree(root, records)
            stable_root()
            stable_source()
    finally:
        os.close(root_fd)
    canonical = json.dumps(records, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()
    with Path(__file__).open('rb') as helper:
        helper_record = digest(helper)
    return {'profile': PROFILE, 'archive': dict(expected), 'audit': audit,
            'expected_metadata_sha256': hashlib.sha256(canonical).hexdigest(),
            'helper': helper_record, 'tool_packages': packages,
            'validation': {'passed': True, 'members': len(records),
                           'all_regular_and_hardlink_bytes_sha256_checked': True}}
