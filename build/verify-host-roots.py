#!/usr/bin/env python3
"""Read-only full ext4 readback bound to an authenticated production receipt.

The namespace reader derives from the reviewed November 6 diagnostic verifier.
Creator semantics: e2fsprogs v1.46.5 misc/create_inode.c:set_inode_extra copies
source mtime seconds; the root inode uses the actual superblock mkfs time.
https://github.com/tytso/e2fsprogs/blob/v1.46.5/misc/create_inode.c
No mounts, device access, filesystem repair or hardware acceptance.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import shutil
import struct
import subprocess
import sys
import uuid

SLOT_BYTES = 4 * 1024**3
SLOT_UUIDS = {'A': 'a7330000-0000-4000-8000-00000000000a',
              'B': 'a7330000-0000-4000-8000-00000000000b'}
PROFILE = 'timesync-20261106'
METADATA = 'fakeroot-raw-xattrs-v1'
ARCHIVE = {'bytes': 138093095, 'sha256': '0ead62c982344bfa3a796027e2f07ce0d8402e7033122707e01a9a3f8860f248'}
MAX_JSON = 16 * 1024**2
MAX_MEMBERS = 100000
DUMP_RESERVE = 64 * 1024**2
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/nonexistent', 'LC_ALL': 'C', 'TZ': 'UTC'}
KINDS = {'dir': 'directory', 'file': 'regular', 'hardlink': 'regular', 'symlink': 'symlink', 'char': 'character special'}
BITS = {'directory': stat.S_IFDIR, 'regular': stat.S_IFREG, 'symlink': stat.S_IFLNK, 'character special': stat.S_IFCHR}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def digest_fd(fd):
    os.lseek(fd, 0, os.SEEK_SET)
    h, size = hashlib.sha256(), 0
    while chunk := os.read(fd, 1024 * 1024):
        h.update(chunk)
        size += len(chunk)
    return {'bytes': size, 'sha256': h.hexdigest()}


def open_regular(path):
    path = physical(path)
    for parent in path.parents:
        require(stat.S_ISDIR(parent.lstat().st_mode), 'physical input parent required')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        require(stat.S_ISREG(os.fstat(fd).st_mode) and os.fstat(fd).st_nlink == 1, 'input must be a single regular file')
    except BaseException:
        os.close(fd)
        raise
    return fd


def valid_name_text(name):
    # ls -p returns raw UTF-8 names; names never enter debugfs commands.
    try:
        name.encode('utf-8', 'strict')
    except UnicodeEncodeError:
        return False
    return all(ord(c) >= 32 and ord(c) != 127 for c in name)


def validate_members(members):
    require(type(members) is dict and '.' in members and len(members) <= MAX_MEMBERS, 'missing root or excessive members')
    for name, item in members.items():
        require(isinstance(name, str) and name and (name == '.' or all(p not in ('', '.', '..') for p in name.split('/'))), 'unsafe source name')
        require(valid_name_text(name) and not name.startswith('/'), 'unsupported source name encoding/control')
        require(type(item) is dict and type(item.get('xattrs')) is dict, 'invalid member object')
        require(item['type'] in KINDS, 'unknown source type: ' + name)
        require(all(type(item.get(k)) is int for k in ('mode', 'uid', 'gid', 'mtime_ns'))
                and 0 <= item['mode'] <= 0o7777 and 0 <= item['uid'] < 2**32
                and 0 <= item['gid'] < 2**32, 'invalid source metadata')
        if item['type'] in ('file', 'hardlink'):
            validate_pin(item.get('data'), allow_empty=True)
        if item['type'] == 'symlink':
            require(type(item.get('link')) is str and '\x00' not in item['link'], 'invalid symlink target')
        if item['type'] == 'char':
            require(type(item.get('device')) is list and len(item['device']) == 2
                    and all(type(v) is int and 0 <= v < 2**32 for v in item['device']), 'invalid device metadata')
        for key, value in item['xattrs'].items():
            require(type(key) is str and re.fullmatch(r'[A-Za-z0-9_.-]+', key)
                    and len(key) <= 255 and type(value) is str and len(value) <= 131072, 'unsafe xattr name')
            base64.b64decode(value, validate=True)
        if name != '.':
            parent = name.rsplit('/', 1)[0] if '/' in name else '.'
            require(parent in members and members[parent]['type'] == 'dir', 'source parent missing')
        if item['type'] == 'hardlink':
            require(item['link'] in members and members[item['link']]['type'] == 'file', 'unsupported hardlink target')
            require(item['data'] == members[item['link']]['data'], 'hardlink source data mismatch')
    require(members['.']['type'] == 'dir' and 'lost+found' not in members, 'unexpected source root/lost+found')


class Reader:
    def __init__(self, fd, work):
        self.fd, self.work, self.sequence = fd, Path(work), 0
        require(re.fullmatch(r'[A-Za-z0-9_./-]+', str(self.work)), 'unsafe debugfs scratch path')

    def batch(self, commands):
        require(commands and all('\n' not in c and '\r' not in c for c in commands), 'invalid command batch')
        script = self.work / 'commands.txt'
        script.write_text('\n'.join(commands) + '\n', encoding='ascii')
        result = subprocess.run(['debugfs', '-f', str(script), '/proc/self/fd/' + str(self.fd)],
                                pass_fds=(self.fd,), capture_output=True, env=ENV, timeout=120)
        self.sequence += 1
        (self.work / ('debugfs-%05d.stdout' % self.sequence)).write_bytes(result.stdout)
        (self.work / ('debugfs-%05d.stderr' % self.sequence)).write_bytes(result.stderr)
        require(result.returncode == 0, 'debugfs command failed')
        require(re.fullmatch(rb'debugfs [^\r\n]+\n', result.stderr), 'debugfs diagnostic/error: ' + repr(result.stderr[:1000]))
        output = result.stdout.decode('utf-8', 'strict')
        pieces = re.split(r'^debugfs: (.*)\n', output, flags=re.M)
        require(pieces[0] == '' and pieces[1::2] == commands, 'unexpected debugfs command framing')
        return pieces[2::2]


def parse_listing(text):
    rows = {}
    for line in text.splitlines():
        if not line:
            continue
        # debugfs ls -p emits an exact zero record for unused directory slots.
        if line == '/0/000000/0/0//0/':
            continue
        match = re.fullmatch(r'/(\d+)/([0-7]{6})/(\d+)/(\d+)/([^/]+)/([0-9]*)/', line)
        require(match, 'unparsed directory entry: ' + repr(line))
        ino, mode, uid, gid, name, size = match.groups()
        require(int(ino) > 0 and name not in rows and valid_name_text(name), 'invalid/duplicate directory entry')
        rows[name] = {'ino': int(ino), 'mode': int(mode, 8), 'uid': int(uid), 'gid': int(gid), 'size': int(size) if size else None}
    require('.' in rows and '..' in rows, 'missing directory dot entries')
    return rows


def parse_stat(text, ino):
    def one(pattern):
        matches = re.findall(pattern, text, flags=re.M)
        require(len(matches) == 1, 'ambiguous/missing stat field: ' + pattern)
        return matches[0]
    number, kind, mode = one(r'^Inode: (\d+)\s+Type: (.*?)\s+Mode:\s+([0-7]+)\s+Flags:.*$')
    require(int(number) == ino and kind in BITS, 'unexpected inode/type')
    uid, gid, size = one(r'^User:\s+(\d+)\s+Group:\s+(\d+)\s+Project:\s+\d+\s+Size:\s+(\d+)$')
    seconds, extra = one(r'^ mtime: 0x([0-9a-f]+):([0-9a-f]+) -- .*$')
    extra = int(extra, 16)
    raw_seconds = int(seconds, 16)
    seconds = (raw_seconds if raw_seconds < 2**31 else raw_seconds - 2**32) + ((extra & 3) << 32)
    result = dict(kind=kind, mode=int(mode, 8), uid=int(uid), gid=int(gid), size=int(size), mtime_ns=seconds * 10**9 + (extra >> 2), links=int(one(r'^Links: (\d+)\s+Blockcount:.*$')))
    fast = re.findall(r'^Fast link dest: "(.*)"$', text, flags=re.M)
    require(len(fast) <= 1, 'duplicate fastlink')
    if fast:
        result['fastlink'] = fast[0].encode('utf-8')
    if kind == 'character special':
        major, minor = one(r'^Device major/minor number:\s*(\d+):(\d+).*$')
        result['device'] = [int(major), int(minor)]
    return result


def parse_xattrs(text):
    if not text.strip():
        return {}
    lines = text.splitlines()
    require(lines[0] == 'Extended attributes:', 'unexpected xattr header')
    attrs = {}
    for line in lines[1:]:
        match = re.fullmatch(r'  ([A-Za-z0-9_.-]+) \((\d+)\)(?: = .*)?', line)
        require(match and match[1] not in attrs, 'unparsed/duplicate xattr: ' + repr(line))
        attrs[match[1]] = int(match[2])
    require(attrs, 'empty xattr header')
    return attrs


def verify(fd, members, work, root_time=None, progress=True):
    require(type(root_time) is int and root_time > 0, 'actual root creation time required')
    validate_members(members)
    reader = Reader(fd, work)
    found = {'.': {'ino': 2}}
    pending, dirs = [('.', 2, 2)], set()
    while pending:
        batch, pending = pending[:128], pending[128:]
        outputs = reader.batch(['ls -p <%d>' % ino for _, ino, _ in batch])
        for (path, ino, parent), output in zip(batch, outputs):
            require(ino not in dirs, 'directory inode alias/cycle')
            dirs.add(ino)
            rows = parse_listing(output)
            require(rows['.']['ino'] == ino and rows['..']['ino'] == parent, 'directory topology mismatch')
            for name, row in rows.items():
                if name in ('.', '..'):
                    continue
                child = name if path == '.' else path + '/' + name
                require(child not in found, 'duplicate namespace member')
                found[child] = row
                if stat.S_ISDIR(row['mode']):
                    pending.append((child, row['ino'], ino))
    require(set(found) == set(members) | {'lost+found'}, 'namespace mismatch: ' + repr(sorted(set(found) ^ (set(members) | {'lost+found'}))[:30]))
    require(len([p for p in found if p.startswith('lost+found/')]) == 0, 'lost+found must be empty')
    inode_paths = {}
    for path, row in found.items():
        inode_paths.setdefault(row['ino'], []).append(path)
    inodes = sorted(inode_paths)
    stats, attrs = {}, {}
    for start in range(0, len(inodes), 256):
        chunk = inodes[start:start + 256]
        outputs = reader.batch([c for ino in chunk for c in ('stat <%d>' % ino, 'ea_list <%d>' % ino)])
        for index, ino in enumerate(chunk):
            stats[ino] = parse_stat(outputs[2 * index], ino)
            attrs[ino] = parse_xattrs(outputs[2 * index + 1])
    groups, image_groups = {}, []
    regular, symlinks, xattr_gets = {}, {}, []
    for ino, paths in inode_paths.items():
        actual = stats[ino]
        for path in paths:
            if path == 'lost+found':
                require((actual['kind'], actual['mode'], actual['uid'], actual['gid'], attrs[ino]) == ('directory', 0o700, 0, 0, {}), 'unexpected lost+found metadata')
                continue
            item = members[path]
            require((actual['kind'], actual['mode'], actual['uid'], actual['gid']) == (KINDS[item['type']], item['mode'], item['uid'], item['gid']), 'metadata differs: ' + path)
            expected_time = (root_time if path == '.' else item['mtime_ns'] // 10**9) * 10**9
            require(actual['mtime_ns'] == expected_time, 'mtime differs: ' + path)
            if path != '.':
                row = found[path]
                require((row['mode'], row['uid'], row['gid']) == (BITS[actual['kind']] | actual['mode'], actual['uid'], actual['gid']), 'listing/stat differ: ' + path)
                require(row['size'] == (None if actual['kind'] == 'directory' else actual['size']), 'listing/stat size differs: ' + path)
            expected_attrs = {k: base64.b64decode(v, validate=True) for k, v in item['xattrs'].items()}
            require(attrs[ino] == {k: len(v) for k, v in expected_attrs.items()}, 'xattr namespace/size differs: ' + path)
            for key, value in expected_attrs.items():
                xattr_gets.append((ino, key, value))
            if item['type'] in ('file', 'hardlink'):
                require(actual['size'] == item['data']['bytes'], 'file size differs: ' + path)
                require(ino not in regular or regular[ino] == item['data'], 'shared inode content mismatch')
                regular[ino] = item['data']
                groups.setdefault(item.get('link', path), []).append(path)
            elif item['type'] == 'symlink':
                target = item['link'].encode('utf-8', 'surrogateescape')
                require(actual['size'] == len(target), 'symlink length differs: ' + path)
                symlinks[ino] = target
            elif item['type'] == 'char':
                require(actual['device'] == item['device'], 'device differs: ' + path)
        if actual['kind'] == 'regular':
            image_groups.append(sorted(paths))
            require(actual['links'] == len(paths), 'regular link count differs')
        elif actual['kind'] == 'directory':
            path = paths[0]
            count = 2 + sum(1 for p, row in found.items() if p != '.' and (p.rsplit('/', 1)[0] if '/' in p else '.') == path and stat.S_ISDIR(row['mode']))
            require(actual['links'] == count, 'directory link count differs: ' + path)
        else:
            require(len(paths) == actual['links'] == 1, 'unexpected nonregular inode alias')
    require(sorted(sorted(v) for v in groups.values()) == sorted(image_groups), 'hardlink topology differs')
    xattr_gets = sorted(set(xattr_gets))
    for ino, key, value in xattr_gets:
        require(shutil.disk_usage(work).free >= len(value) + DUMP_RESERVE, 'insufficient xattr scratch budget')
        output = Path(work) / 'attribute.bin'
        require(not output.exists(), 'stale xattr output')
        response = reader.batch(['ea_get -f %s <%d> %s' % (output, ino, key)])[0]
        require(not response.strip() and output.is_file() and not output.is_symlink() and output.read_bytes() == value, 'raw xattr differs')
        output.unlink()
    dumps = [(ino, 'regular', data) for ino, data in regular.items()]
    for ino, target in symlinks.items():
        if 'fastlink' in stats[ino]:
            require(stats[ino]['fastlink'] == target, 'fast symlink target differs')
        else:
            dumps.append((ino, 'symlink', target))
    total = 0
    for start in range(0, len(dumps), 128):
        chunk = dumps[start:start + 128]
        batch_bytes = sum(expected['bytes'] if kind == 'regular' else len(expected)
                          for _, kind, expected in chunk)
        require(shutil.disk_usage(work).free >= batch_bytes + DUMP_RESERVE, 'insufficient inode dump scratch budget')
        paths = [Path(work) / ('inode-%d.bin' % ino) for ino, _, _ in chunk]
        require(all(not p.exists() for p in paths), 'stale dump output')
        responses = reader.batch(['dump <%d> %s' % (entry[0], path) for entry, path in zip(chunk, paths)])
        for (ino, kind, expected), path, response in zip(chunk, paths, responses):
            require(not response.strip(), 'unexpected dump output')
            dumped = open_regular(path)
            try:
                actual = digest_fd(dumped)
            finally:
                os.close(dumped)
            if kind == 'regular':
                require(actual == expected, 'regular contents differ: inode ' + str(ino))
                total += actual['bytes']
            else:
                require(path.read_bytes() == expected, 'long symlink target differs')
            path.unlink()
        if progress and (start == 0 or start % 1024 == 0):
            print('readback checked %d/%d unique dumped inodes' % (min(start + 128, len(dumps)), len(dumps)), file=sys.stderr, flush=True)
    return {'namespace_members': len(members), 'allowed_extra_empty_directory': 'lost+found', 'unique_regular_inodes': len(regular), 'regular_bytes_read': total, 'symlinks': len(symlinks), 'fast_symlinks': sum('fastlink' in stats[ino] for ino in symlinks), 'long_symlinks_dumped': sum('fastlink' not in stats[ino] for ino in symlinks), 'character_devices': sum(x['type'] == 'char' for x in members.values()), 'raw_xattrs': len(xattr_gets), 'hardlink_groups': sorted(sorted(v) for v in groups.values() if len(v) > 1), 'source_fractional_mtime_members': sum(x['mtime_ns'] % 10**9 != 0 for x in members.values()), 'image_mtime_policy': 'source whole seconds; root from actual superblock s_mkfs_time', 'root_creation_time_seconds': root_time, 'out_of_scope_metadata': ['atime', 'ctime', 'crtime'], 'source_nanosecond_metadata_preserved': False, 'hardware_acceptance': False, 'production_build_acceptance': False}


def validate_pin(value, allow_empty=False):
    require(type(value) is dict and set(value) == {'bytes', 'sha256'}
            and type(value['bytes']) is int and value['bytes'] >= (0 if allow_empty else 1)
            and type(value['sha256']) is str and re.fullmatch('[0-9a-f]{64}', value['sha256']),
            'invalid size/hash pin')


def signature(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def physical(path):
    path = Path(path)
    require(path.is_absolute() and str(path) == os.path.abspath(path), 'canonical absolute path required')
    require(not any(path == p or p in path.parents for p in map(Path, ('/dev', '/proc', '/sys'))),
            'pseudo filesystem path forbidden')
    return path


class Held:
    """Hold physical parent chain and one nofollow single-link regular inode."""
    def __init__(self, path, expected=None, max_bytes=None):
        self.path, self.parents, self.fd = physical(path), [], None
        try:
            directory = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            self.parents.append((directory, None, None, os.fstat(directory)))
            for name in self.path.parts[1:-1]:
                following = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                self.parents.append((following, directory, name, os.fstat(following)))
                directory = following
            self.fd = os.open(self.path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            self.before = os.fstat(self.fd)
            require(stat.S_ISREG(self.before.st_mode) and self.before.st_nlink == 1,
                    'single-link regular input required')
            require(max_bytes is None or self.before.st_size <= max_bytes, 'input exceeds bound')
            if expected is not None:
                validate_pin(expected, allow_empty=True)
                require(self.before.st_size == expected['bytes'], 'input size pin differs')
            self.pin = digest_fd(self.fd)
            if expected is not None:
                require(self.pin == expected, 'input hash pin differs')
            self.check()
        except BaseException:
            self.close()
            raise

    def check(self):
        require(signature(self.before) == signature(os.fstat(self.fd)) == signature(os.stat(
            self.path.name, dir_fd=self.parents[-1][0], follow_symlinks=False)), 'input identity changed')
        for held, parent, name, before in self.parents:
            require(signature(before) == signature(os.fstat(held)), 'input parent changed')
            if parent is not None:
                require(signature(before) == signature(os.stat(name, dir_fd=parent, follow_symlinks=False)),
                        'input parent replaced')

    def unchanged(self):
        self.check()
        require(digest_fd(self.fd) == self.pin, 'input bytes changed during readback')
        self.check()

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        for held, _, _, _ in reversed(self.parents):
            os.close(held)
        self.parents = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        try:
            self.unchanged()
        finally:
            self.close()


def json_fd(held):
    require(held.before.st_size <= MAX_JSON, 'JSON exceeds bound')
    os.lseek(held.fd, 0, os.SEEK_SET)
    raw = os.read(held.fd, MAX_JSON + 1)
    require(len(raw) == held.before.st_size and len(raw) <= MAX_JSON, 'JSON changed or oversized')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'duplicate JSON key')
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=unique,
                       parse_constant=lambda value: require(False, 'nonfinite JSON'))
    require(type(value) is dict, 'JSON object required')
    held.check()
    return value


def safe_relative(value):
    require(type(value) is str and value and not value.startswith('/')
            and str(Path(value)) == value and '..' not in Path(value).parts and value != '.'
            and valid_name_text(value), 'unsafe relative member')
    return Path(value)


def source_policy(receipt):
    require(type(receipt.get('schema')) is int and receipt.get('schema') == 1 and receipt.get('success') is True and receipt.get('state') == 'complete'
            and receipt.get('hardware_acceptance') is False and receipt.get('slot_bytes') == SLOT_BYTES
            and receipt.get('userspace_profile') == PROFILE and receipt.get('metadata_profile') == METADATA,
            'producer receipt did not complete closed root build')
    require(receipt.get('host_inventory_unchanged') is True and receipt.get('mapped_processes_final') == []
            and receipt.get('phase_mounts_final') == [], 'producer final isolation/inventory checks differ')
    policy = receipt['policy']
    require(type(policy) is dict and set(policy) == {'archive', 'binding', 'diagnostic', 'clock'},
            'producer policy fields differ')
    validate_pin(policy['archive'])
    require(policy['archive'] == ARCHIVE, 'unregistered production archive')
    require(policy['binding'].get('profile') == PROFILE and policy['binding'].get('archive') == policy['archive'],
            'profile archive binding differs')
    require(set(policy['diagnostic']) == {'Image', 'board.dtb', 'kernel.config', 'initramfs.cpio.gz'},
            'diagnostic source closure differs')
    for expected in policy['diagnostic'].values():
        validate_pin(expected)
    clock = policy['clock']
    require(type(clock) is dict and set(clock) == {'schema', 'identity', 'directory', 'helper'}
            and type(clock['schema']) is int and clock['schema'] == 1 and clock['identity'] == 996
            and clock['directory'] == 'var/lib/systemd/timesync', 'time state policy differs')
    validate_pin(clock['helper'])
    sources = receipt['sources']
    require(type(sources) is dict and re.fullmatch('[0-9a-f]{40}', sources.get('revision', ''))
            and type(sources.get('files')) is dict, 'source revision/closure missing')
    for name in ('build/host-payload.py', 'build/launch-host-roots.py', 'build/userspace-profiles.py',
                 'build/timesync-state.py', 'build/rootfs-metadata.py', 'configs/host-userspace-profiles.lock.json',
                 'docs/evidence/diagnostic-20261104.json'):
        entry = sources['files'][name]
        validate_pin({key: entry[key] for key in ('bytes', 'sha256')})
        require(re.fullmatch('[0-9a-f]{40}', entry.get('git_blob', '')), 'source blob binding missing')
    require({key: sources['files']['build/timesync-state.py'][key] for key in ('bytes', 'sha256')}
            == clock['helper'], 'clock source closure differs')
    for binding_key, source in (('profile_lock', 'configs/host-userspace-profiles.lock.json'),
                                ('resolver', 'build/userspace-profiles.py')):
        require(policy['binding'].get(binding_key) ==
                {key: sources['files'][source][key] for key in ('bytes', 'sha256')},
                'profile source binding differs')
    policy = dict(policy, metadata_helper={key: sources['files']['build/rootfs-metadata.py'][key]
                                          for key in ('bytes', 'sha256')})
    return policy


def manifest_policy(manifest, policy):
    require(type(manifest.get('schema')) is int and manifest.get('schema') == 1 and manifest.get('board') == 'Orange Pi 4 Pro A733'
            and manifest.get('slot_bytes') == SLOT_BYTES and manifest.get('slot_uuids') == SLOT_UUIDS,
            'root manifest identity differs')
    sources = manifest['sources']
    require(sources.get('rootfs.tar.gz') == policy['archive']
            and sources.get('userspace_profile') == policy['binding']
            and sources.get('timesync_state') == policy['clock']
            and sources.get('rootfs_metadata', {}).get('profile') == METADATA
            and sources.get('rootfs_metadata', {}).get('helper') == policy['metadata_helper'],
            'root manifest profile/time/metadata binding differs')
    for name, expected in policy['diagnostic'].items():
        require(sources.get(name) == expected, 'root manifest diagnostic binding differs')
    files = manifest['files']
    require(type(files) is dict and 0 < len(files) <= MAX_MEMBERS, 'invalid manifest closure')
    for name, expected in files.items():
        safe_relative(name)
        validate_pin(expected, allow_empty=True)
        require(expected['bytes'] <= 2 * SLOT_BYTES, 'oversized artifact')
    for slot in SLOT_UUIDS:
        require(files.get('root-' + slot + '.ext4', {}).get('bytes') == SLOT_BYTES
                and 'root-' + slot + '.ext4.gz' in files, 'root image outputs missing')
    # Bind the actual time helper included in the deployable provenance closure.
    require(files.get('provenance/timesync-state.py') == policy['clock']['helper'], 'time helper provenance differs')
    for filename, expected in (('host-userspace-profiles.lock.json', policy['binding']['profile_lock']),
                               ('userspace-profiles.py', policy['binding']['resolver']),
                               ('rootfs-metadata.py', policy['metadata_helper'])):
        require(files.get('provenance/' + filename) == expected, 'profile/metadata provenance differs')


def superblock(fd, slot):
    block = os.pread(fd, 1024, 1024)
    require(len(block) == 1024 and block[56:58] == b'\x53\xef', 'invalid ext4 superblock')
    require(str(uuid.UUID(bytes=block[104:120])) == SLOT_UUIDS[slot], 'actual filesystem UUID differs')
    require(not struct.unpack_from('<I', block, 92)[0] & 4
            and struct.unpack_from('<I', block, 100)[0] & 0x1000, 'filesystem must be read-only without journal')
    root_time = struct.unpack_from('<I', block, 264)[0]
    require(946684800 <= root_time < 2**32, 'unsupported filesystem creation time')
    return root_time


def new_output(path):
    path = physical(path)
    require(re.fullmatch(r'[A-Za-z0-9_./-]+', str(path)), 'unsafe output/scratch path')
    for parent in path.parents:
        require(stat.S_ISDIR(parent.lstat().st_mode), 'output needs physical directory ancestors')
    require(not os.path.lexists(path), 'output must be new')
    path.mkdir(mode=0o700)
    return path


def publish(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def readback(receipt_path, receipt_sha256, output):
    require(type(receipt_sha256) is str and re.fullmatch('[0-9a-f]{64}', receipt_sha256), 'invalid receipt SHA256')
    receipt_path = physical(receipt_path)
    output = physical(output)
    require(not output.is_relative_to(receipt_path.parent), 'readback output must be outside producer phase')
    output = new_output(output)
    report = {'schema': 1, 'success': False, 'state': 'verifying', 'hardware_acceptance': False,
              'classification': 'Independent physical ext4 readback against producer-captured stage; no hardware acceptance',
              'independent_expected_stage_correctness': False, 'slots': {}}
    try:
        with Held(Path(__file__).absolute(), max_bytes=1024**2) as controller:
            os.lseek(controller.fd, 0, os.SEEK_SET)
            (output / 'controller.py').write_bytes(os.read(controller.fd, 1024**2))
            report['controller'] = controller.pin
        with Held(receipt_path, max_bytes=MAX_JSON) as receipt_file:
            require(receipt_file.pin['sha256'] == receipt_sha256, 'producer receipt hash pin differs')
            receipt = json_fd(receipt_file)
            report['producer_receipt'] = receipt_file.pin
            policy = source_policy(receipt)
            phase = physical(receipt['phase'])
            require(re.fullmatch(r'host-roots-[A-Za-z0-9][A-Za-z0-9_-]{0,63}', phase.name)
                    and phase.parent == Path('/var/lib/a733-clean-replay-20261106'), 'unexpected producer phase')
            require(Path(receipt_path) == phase / 'receipt.json', 'receipt location differs from bound phase')
            require(not output.is_relative_to(phase), 'readback output must be outside producer phase')
            production = phase / safe_relative(receipt['output'])
            require(production.parent == phase / 'work/project/out'
                    and production.name == 'host-payload-' + phase.name.removeprefix('host-roots-'),
                    'production output location differs')
            manifest_expected = receipt['verification']['manifest']
            validate_pin(manifest_expected)
            with Held(production / 'manifest.json', manifest_expected, MAX_JSON) as manifest_file:
                manifest = json_fd(manifest_file)
                manifest_policy(manifest, policy)
                report['root_manifest'] = manifest_file.pin
                # Authenticate the complete emitted artifact/provenance closure.
                for name, expected in manifest['files'].items():
                    with Held(production / safe_relative(name), expected):
                        pass
                report['artifact_files_authenticated'] = len(manifest['files'])
                for slot in SLOT_UUIDS:
                    print('full root readback starting slot ' + slot, file=sys.stderr, flush=True)
                    pins = receipt['readback_expectations'][slot]
                    work = output / ('slot-' + slot)
                    work.mkdir(mode=0o700)
                    with Held(phase / 'work/readback' / ('slot-' + slot + '.expected.json'), pins['expected'], MAX_JSON) as expected_file, \
                         Held(phase / 'work/readback' / ('slot-' + slot + '.stable.json'), pins['stability'], MAX_JSON) as stable_file:
                        expected, stable = json_fd(expected_file), json_fd(stable_file)
                        require(type(expected.get('schema')) is int and expected.get('schema') == 1 and expected.get('slot') == slot
                                and type(expected.get('members')) is dict and len(expected['members']) == pins['members'],
                                'expected namespace proof differs')
                        require(type(stable.get('schema')) is int and stable.get('creator_input_stable') is True
                                and stable == {'schema': 1, 'slot': slot, 'creator_input_stable': True}, 'creator stability proof differs')
                        validate_members(expected['members'])
                        require(sum(item['type'] == 'char' for item in expected['members'].values()) == 8,
                                'production character device count differs')
                        image = production / ('root-' + slot + '.ext4')
                        with Held(image, manifest['files'][image.name], SLOT_BYTES) as image_file:
                            require(image_file.before.st_size == SLOT_BYTES, 'production image size differs')
                            root_time = superblock(image_file.fd, slot)
                            result = verify(image_file.fd, expected['members'], work, root_time=root_time)
                            image_file.unchanged()
                            result.update(image=image_file.pin, expected=expected_file.pin,
                                          stability=stable_file.pin, image_path=str(image), input_identity_unchanged=True)
                            report['slots'][slot] = result
                    publish(output / ('slot-' + slot + '.json'), report['slots'][slot])
                    with (output / 'progress.jsonl').open('a') as progress:
                        progress.write(json.dumps({'slot': slot, 'full_readback_passed': True}) + '\n')
                        progress.flush()
                        os.fsync(progress.fileno())
            report.update(success=True, state='complete', full_namespace_metadata_content_readback=True)
    except BaseException as error:
        report.update(success=False, state='failed', error=repr(error))
        raise
    finally:
        publish(output / 'receipt.json', report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('--receipt-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    require(sys.flags.isolated and sys.flags.dont_write_bytecode, 'invoke Python with -I -B')
    print(json.dumps(readback(args.receipt, args.receipt_sha256, args.output), sort_keys=True))


if __name__ == '__main__':
    main()
