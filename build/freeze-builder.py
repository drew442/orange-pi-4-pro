#!/usr/bin/env python3
"""Freeze and restore a proven offline AMD64 builder; no hardware acceptance.

Outputs and failed scratch are retained. No packages are installed and no
source/deployment artifact is rebuilt by this phase.
"""
import argparse
import base64
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import signal
import time
import stat
import subprocess
import sys
import tarfile

REPO = Path(__file__).absolute().parents[1]
RECIPE = 'build/freeze-builder.py'
WORKSPACE = Path('/var/lib/a733-frozen-builders')
EPOCH = 1793750400
ANCHORS = {'a733-probe.sh': False, 'a733-install.sh': False,
           'a733-packages.list': False, 'a733-debs': True, 'a733-sources': True,
           'a733-export.sh': False, 'a733-export-files.list': False,
           'a733-export-output': True}
NEW_ANCHORS = {'a733-export.sh', 'a733-export-files.list', 'a733-export-output'}
API_TREES = {'dev', 'proc', 'sys', 'run', 'tmp', 'var/tmp'}
EMPTY_TREES = {'dev', 'proc', 'sys', 'run', 'tmp', 'var/tmp', 'etc/ssl/private',
               'root/.ssh', 'root/.gnupg', 'nonexistent/.gnupg',
               'etc/credstore', 'etc/credstore.encrypted'}


def bootstrap_guard(script):
    """Check stdlib-only trust before importing either executable sibling."""
    for target in (script, script.with_name('clean-builder.py'),
                   script.with_name('install-builder-packages.py')):
        for path in (target, *target.parents):
            info = path.lstat()
            if (info.st_uid != 0 or info.st_mode & 0o022 or
                    not (stat.S_ISREG(info.st_mode) if path == target else stat.S_ISDIR(info.st_mode))):
                raise ValueError('Root-owned physical non-writable bootstrap required: ' + str(path))


def load_producer():
    if os.geteuid() == 0:
        bootstrap_guard(Path(__file__).absolute())
    spec = importlib.util.spec_from_file_location('freeze_producer', REPO / 'build/install-builder-packages.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


producer = load_producer()
seed = producer.seed
require = seed.require
FILES = (*producer.FILES, RECIPE)


def binding(repo):
    previous = seed.FILES
    try:
        seed.FILES = FILES
        return seed.binding(repo)
    finally:
        seed.FILES = previous


def excluded(name):
    """Private directories retain headers; never read excluded file bytes."""
    if name in ANCHORS:
        return True
    if any(name.startswith(prefix + '/') for prefix in EMPTY_TREES):
        return True
    if name.startswith('etc/ssh/') and PurePosixPath(name).name.startswith('ssh_host_'):
        return True
    return name.startswith('var/lib/shim-signed/mok/') and name.endswith('.priv')


def private_leaf(name):
    return (name.startswith('etc/ssh/') and PurePosixPath(name).name.startswith('ssh_host_') or
            name.startswith('var/lib/shim-signed/mok/') and name.endswith('.priv'))


def private_name(name):
    return (any(name.startswith(prefix + '/') for prefix in EMPTY_TREES - API_TREES) or
            private_leaf(name))


def protected_directories(root):
    # Refuse aliases before walking: a private tree symlink could otherwise make
    # its target look like an ordinary included path elsewhere in the root.
    for name in sorted(EMPTY_TREES | {'etc/ssh', 'var/lib/shim-signed/mok'}):
        current = root
        for part in PurePosixPath(name).parts:
            current /= part
            try:
                info = current.lstat()
            except FileNotFoundError:
                break
            require(stat.S_ISDIR(info.st_mode),
                    'Protected directory or ancestor is not physical: ' + name)


def scaffold(root, create=False):
    result = {}
    for name, directory in ANCHORS.items():
        path = root / name
        if not os.path.lexists(path):
            require(create and name in NEW_ANCHORS, 'Missing required runtime anchor: ' + name)
            if directory:
                path.mkdir(mode=0o755)
                path.chmod(0o755)
            else:
                producer.write_private(path, b'', 0o644)
        info = path.lstat()
        require((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)) and
                (info.st_uid, info.st_gid) in ({(0, 0), (seed.UID_BASE, seed.UID_BASE)} if name in NEW_ANCHORS else {(0, 0)}) and
                stat.S_IMODE(info.st_mode) == (0o755 if directory else 0o644) and
                (not list(path.iterdir()) if directory else info.st_size == 0 and info.st_nlink == 1),
                'Unsafe/nonempty runtime anchor: ' + name)
        require(not os.listxattr(path, follow_symlinks=False), 'Runtime anchor has xattrs: ' + name)
        result[name] = {'directory': directory, 'mode': stat.S_IMODE(info.st_mode),
                        'uid': info.st_uid, 'gid': info.st_gid}
    return result


def inventory_paths(root):
    """Inspect excluded inode identities before any included regular file hash."""
    protected_directories(root)
    included, private_inodes = {'.'}, set()
    for directory, dirs, files in os.walk(root, followlinks=False):
        for leaf in sorted(dirs + files):
            path = Path(directory) / leaf
            name = path.relative_to(root).as_posix()
            info = path.lstat()
            if excluded(name):
                require(not private_name(name) or not stat.S_ISLNK(info.st_mode),
                        'Private symlink could alias included bytes: ' + name)
                require(not private_leaf(name) or stat.S_ISREG(info.st_mode),
                        'Private key leaf must be regular: ' + name)
                if stat.S_ISREG(info.st_mode):
                    private_inodes.add((info.st_dev, info.st_ino))
            else:
                included.add(name)
    for name in sorted(included):
        path = root if name == '.' else root / name
        info = path.lstat()
        require(stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or stat.S_ISREG(info.st_mode),
                'Unexpected special node: ' + name)
        require(seed.in_range(info.st_uid) and seed.in_range(info.st_gid),
                'Unmapped real-source owner: ' + name)
        require((info.st_dev, info.st_ino) not in private_inodes,
                'Included hardlink aliases excluded private inode: ' + name)
    return sorted(included)


# Executed in the proven namespace as well as in fixture tests. Binary xattrs
# include kernel-translated POSIX ACL IDs and security.capability rootid.
MANIFEST_PY = r'''
import os, stat, hashlib, json, base64
from pathlib import Path

def metadata(root, names):
    result, links = {}, {}
    for name in names:
        path = Path(root) if name == '.' else Path(root) / name
        info = path.lstat()
        record = {'mode': stat.S_IMODE(info.st_mode), 'uid': info.st_uid,
                  'gid': info.st_gid, 'mtime_ns': info.st_mtime_ns,
                  'xattrs': {key: base64.b64encode(os.getxattr(path, key, follow_symlinks=False)).decode('ascii')
                             for key in sorted(os.listxattr(path, follow_symlinks=False))}}
        if stat.S_ISREG(info.st_mode):
            record['type'] = 'file'
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as stream:
                before = os.fstat(stream.fileno())
                if (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino):
                    raise ValueError('File changed before hash: ' + name)
                digest = hashlib.sha256()
                for chunk in iter(lambda: stream.read(1048576), b''):
                    digest.update(chunk)
                after = os.fstat(stream.fileno())
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ValueError('File changed during hash: ' + name)
            record.update(size=info.st_size, sha256=digest.hexdigest())
            links.setdefault((info.st_dev, info.st_ino), []).append(name)
        elif stat.S_ISDIR(info.st_mode):
            record['type'] = 'directory'
        elif stat.S_ISLNK(info.st_mode):
            record.update(type='symlink', target=os.readlink(path))
        else:
            raise ValueError('Special manifest path: ' + name)
        result[name] = record
    for group in links.values():
        for name in group:
            result[name]['hardlinks'] = sorted(group)
    return result

def encoded_manifest(root, names):
    return (json.dumps(metadata(root, names), sort_keys=True, separators=(',', ':'), ensure_ascii=True) + '\n').encode('ascii')
'''
exec(MANIFEST_PY)


def snapshot(root):
    names = inventory_paths(root)
    return names, encoded_manifest(root, names)


def raw_estimate(root, names):
    # Account conservatively for PAX headers/xattrs and duplicate hardlink data.
    size = 10240
    for name in names:
        path = root if name == '.' else root / name
        info = path.lstat()
        size += 8192 + (min(info.st_size, info.st_blocks * 512) if stat.S_ISREG(info.st_mode) else 0)
        size += 2 * sum(len(key.encode()) + len(os.getxattr(path, key, follow_symlinks=False))
                        for key in os.listxattr(path, follow_symlinks=False))
    return size


def output_guard(output, source, repo):
    seed.safe_path(output)
    require(output.parent == WORKSPACE and
            re.fullmatch(r'freeze-[A-Za-z0-9][A-Za-z0-9._-]*', output.name),
            'Output must be /var/lib/a733-frozen-builders/freeze-NAME')
    require(not os.path.lexists(output), 'Output exists; no resume/overwrite')
    for other in (source, repo):
        require(output != other and output not in other.parents and other not in output.parents,
                'Input/output overlap forbidden')
    seed.root_owned_path(WORKSPACE.parent)
    if WORKSPACE.exists():
        producer.private_directory(WORKSPACE)
    seed.check_no_mounts(output)


def validate_producer(output, project, records, expected):
    original = producer.validate_seed(output, project)
    phase = output / 'package-install'
    producer.private_directory(phase)
    report = json.loads(producer.read(phase / 'receipt.json'))
    require(report['schema'] == 1 and report['phase'] == 'offline-package-install' and
            report['success'] is True and report['install_exit'] == 0 and
            report['post_checks'] == {'success': True, 'errors': []}, 'Producer did not pass')
    require(set(report['project']['files']) == set(producer.FILES) and all(
        report['project']['files'][name] == project['files'][name] for name in producer.FILES),
        'Producer code/lock binding changed')
    require(report['seed_records'] == original, 'Producer seed binding changed')
    require(report['command'] == producer.command(output, phase), 'Producer isolation command changed')
    require(producer.read(phase / 'install.sh') == producer.install_script(), 'Producer payload changed')
    require(seed.facts(phase / 'install.log') == report['install_log'], 'Producer log changed')
    installed = producer.validate_installed(producer.read(phase / 'install.log'), expected)
    require(producer.read(phase / 'installed.tsv') == installed and
            seed.facts(phase / 'installed.tsv') == report['installed_inventory'], 'Producer inventory changed')
    require(all(seed.facts(phase / name) == value for name, value in report['staged'].items()),
            'Producer staged input changed')
    require(report['source_cache']['files'] == records and producer.cache_facts(
        Path(report['source_cache']['path']), records) == records, 'Producer original cache changed')
    require(report['private_archives'] == {Path(name).name: value for name, value in records.items()} and
            all(seed.facts(phase / 'debs' / name) == value for name, value in report['private_archives'].items()),
            'Producer private cache changed')
    require(producer.source_facts(phase / 'sources') == report['sources'], 'Producer source profile changed')
    require(report['host_inventory_after'] == project['files'][producer.INVENTORY] and
            seed.facts(phase / 'host-inventory-after.tsv') == report['host_inventory_after'],
            'Producer after-inventory changed')
    require(seed.facts(output / 'rootfs/usr/sbin/policy-rc.d') == report['policy_rc_d'], 'Producer policy changed')
    captured = dict(original)
    for name in ('receipt.json', 'install.log', 'installed.tsv', 'host-inventory-after.tsv', *report['staged']):
        captured['package-install/' + name] = seed.facts(phase / name)
    return captured


def boundary():
    payload = producer.install_script()
    anchor = b"printf 'A733_BOUNDARY_PASSED\\n'\n"
    require(payload.startswith(seed.PROBE) and payload.count(anchor) == 1,
            'Producer boundary extraction anchor changed')
    prefix, tail = payload.split(anchor)
    require(tail.startswith(b"printf '%s\\n' 'grub-efi-amd64 "), 'Producer boundary terminator changed')
    return prefix + anchor


TAR_METADATA = '--numeric-owner --acls --xattrs --xattrs-include=* --sparse'
TAR_LIST = '--no-recursion --null --verbatim-files-from --files-from=/a733-export-files.list'


def payload(action):
    require(action in ('export', 'restore', 'compare', 'inventory'), 'Unknown namespace action')
    script = boundary()
    script += b"awk '$5 == \"/\" { count++; if ($6 !~ /(^|,)ro(,|$)/) exit 1 }; END { if (count != 1) exit 1 }' /proc/self/mountinfo\n"
    if action == 'export':
        script += ("tar --create " + TAR_METADATA +
                   " --format=pax --pax-option=exthdr.name=%d/PaxHeaders/%f,delete=atime,delete=ctime --sort=name " +
                   " --directory=/a733-debs " + TAR_LIST + " --file=/a733-export-output/builder-root.tar\n" +
                   "tar --compare " + TAR_METADATA + ' --directory=/a733-debs ' + TAR_LIST +
                   " --file=/a733-export-output/builder-root.tar\n").encode()
    elif action == 'restore':
        script += ('tar --extract ' + TAR_METADATA +
                   ' --same-owner --same-permissions --delay-directory-restore' +
                   ' --directory=/a733-export-output --file=/a733-packages.list\n').encode()
    elif action == 'compare':
        script += ('tar --compare ' + TAR_METADATA +
                   ' --directory=/a733-export-output --file=/a733-packages.list\n').encode()
    else:
        script += b'audit=$(dpkg --audit)\n[ -z "$audit" ]\n'
        script += b"printf 'A733_BEGIN installed\\n'\ndpkg-query -W -f='${binary:Package}\\t${Version}\\t${Architecture}\\t${Status}\\n'\nprintf 'A733_END installed\\n'\n"
    if action in ('export', 'compare'):
        root = '/a733-debs' if action == 'export' else '/a733-export-output'
        script += b"/usr/bin/python3 -I -B - <<'A733_PY'\n" + MANIFEST_PY.encode()
        script += ("\nnames = [p.decode('utf-8', 'surrogateescape')[2:] for p in Path('/a733-export-files.list').read_bytes().split(b'\\0') if p]\n" +
                   "data = encoded_manifest('" + root + "', names)\n").encode()
        if action == 'compare':
            script += ("synthetics = metadata('" + root + "', " + repr(sorted(ANCHORS)) + ")\n" +
                       "for name, directory in " + repr(ANCHORS) + ".items():\n" +
                       "    item = synthetics[name]\n" +
                       "    expected = {'type': 'directory' if directory else 'file', 'mode': 493 if directory else 420, 'uid': 0, 'gid': 0, 'mtime_ns': " + str(EPOCH * 10**9) + ", 'xattrs': {}}\n" +
                       "    if not directory: expected.update(size=0, sha256=hashlib.sha256(b'').hexdigest(), hardlinks=[name])\n" +
                       "    if item != expected: raise ValueError('Restored synthetic anchor differs: ' + name)\n").encode()
        # Compare is read-only: put the manifest in its retained stdout record.
        script += b"print('A733_BEGIN manifest')\nprint(base64.b64encode(data).decode('ascii'))\nprint('A733_END manifest')\nA733_PY\n"
    return script


def command(output, phase, script, destination, action, restored_root=None):
    root = restored_root or output / 'rootfs'
    seed.shifted_root_owner(root)
    args = seed.nspawn_command(root, output / 'probe.sh')[:-2]
    # systemd 249 rejects readonly + ownership=chown; these roots are already shifted.
    args[args.index('--private-users-ownership=chown')] = '--private-users-ownership=off'
    args += ['--read-only']
    args += ['--bind-ro=' + str(script) + ':/a733-export.sh',
             '--bind-ro=' + str(phase / 'files.list') + ':/a733-export-files.list']
    if action == 'export':
        args += ['--bind-ro=' + str(output / 'rootfs') + ':/a733-debs:norbind']
    if action in ('restore', 'compare'):
        args += ['--bind-ro=' + str(phase / 'run1/builder-root.tar') + ':/a733-packages.list']
    if destination is not None:
        flag = '--bind-ro=' if action == 'compare' else '--bind='
        args += [flag + str(destination) + ':/a733-export-output']
    return args + ['/bin/sh', '/a733-export.sh']


def parsed_manifest(log):
    blocks = re.findall(rb'^A733_BEGIN manifest\n(.*?)^A733_END manifest\n', log, re.M | re.S)
    require(len(blocks) == 1, 'Missing/duplicate metadata manifest')
    data = base64.b64decode(blocks[0].strip(), validate=True)
    require(isinstance(json.loads(data), dict), 'Invalid metadata manifest')
    return data


def validate_namespace_source(host_data, namespace_data):
    host, child = json.loads(host_data), json.loads(namespace_data)
    require(set(host) == set(child), 'Namespace source path set differs')
    translated = {'system.posix_acl_access', 'system.posix_acl_default', 'security.capability'}
    for name, original in host.items():
        current = child[name]
        require(set(original) == set(current), 'Namespace source metadata fields differ: ' + name)
        for key, value in original.items():
            if key in ('uid', 'gid'):
                require(current[key] == value - seed.UID_BASE, 'Namespace source owner differs: ' + name)
            elif key == 'xattrs':
                require(set(current[key]) == set(value) and all(
                    current[key][attribute] == data for attribute, data in value.items()
                    if attribute not in translated), 'Namespace source xattrs differ: ' + name)
            else:
                require(current[key] == value, 'Namespace source ' + key + ' differs: ' + name)


def compare_manifests(expected, actual):
    if expected == actual:
        return
    before, after = json.loads(expected), json.loads(actual)
    changed = sorted(name for name in set(before) | set(after) if before.get(name) != after.get(name))
    raise ValueError('Restored metadata/content/ACL/xattr mismatch: ' + ', '.join(changed[:8]))


def namespace_run(args, phase, label, report):
    require('--read-only' in args, 'Source OS must be read-only')
    report.setdefault('commands', {})[label] = args
    producer.write_private(phase / (label + '-help.txt'), seed.run(args[:-2] + ['--help']))
    try:
        with (phase / (label + '.log')).open('xb') as log:
            monitor(args, log, phase, report)
    finally:
        if (phase / (label + '.log')).exists():
            try:
                report.setdefault('probes', {})[label] = seed.validate_probe(
                    producer.read(phase / (label + '.log')))
            except BaseException as exc:
                report.setdefault('probe_errors', {})[label] = type(exc).__name__ + ': ' + str(exc)
    data = producer.read(phase / (label + '.log'))
    report.setdefault('probes', {})[label] = seed.validate_probe(data)
    require(data.count(b'\nA733_BOUNDARY_PASSED\n') == 1, 'Namespace boundary assertion missing')
    seed.check_no_mounts(phase)
    seed.check_process_ranges()
    return data


def canonical_append(path):
    with seed.regular(path) as held:
        before = os.fstat(held.fileno())
        require(before.st_nlink == 1, 'Archive hardlink forbidden')
        fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'r+b') as stream:
            current = os.fstat(stream.fileno())
            require((current.st_dev, current.st_ino) == (before.st_dev, before.st_ino), 'Archive replaced')
            with tarfile.open(fileobj=stream, mode='a', format=tarfile.PAX_FORMAT) as archive:
                for name, directory in sorted(ANCHORS.items()):
                    member = tarfile.TarInfo('./' + name)
                    member.type = tarfile.DIRTYPE if directory else tarfile.REGTYPE
                    member.uid = member.gid = 0
                    member.uname = member.gname = ''
                    member.mode = 0o755 if directory else 0o644
                    member.mtime = EPOCH
                    archive.addfile(member)
            stream.flush()
            os.fsync(stream.fileno())


def audit_archive(path, names, synthetic=True):
    with seed.regular(path) as stream, tarfile.open(fileobj=stream, mode='r:') as archive:
        members = archive.getmembers()
    by_name = {}
    for member in members:
        name = seed.member_name(member.name)
        require(name not in by_name, 'Duplicate archive member')
        require(member.isdir() or member.isfile() or member.issym() or member.islnk(), 'Special archive member')
        require(0 <= member.uid < seed.UID_COUNT and 0 <= member.gid < seed.UID_COUNT, 'Unmapped canonical owner')
        require(not excluded(name) or synthetic and name in ANCHORS, 'Private/API archive member')
        by_name[name] = member
    require(set(by_name) == set(names) | (set(ANCHORS) if synthetic else set()), 'Archive member set differs')
    for name, member in by_name.items():
        for parent in PurePosixPath(name).parents:
            if str(parent) in by_name:
                require(by_name[str(parent)].isdir(), 'Archive writes through non-directory')
        if member.islnk():
            target = seed.member_name(member.linkname)
            require(target in by_name and by_name[target].isfile() and target not in ANCHORS,
                    'Unsafe archive hardlink')
        if member.issym():
            target = member.linkname
            logical = posixpath.normpath(target.lstrip('/') if target.startswith('/') else
                                        posixpath.join(posixpath.dirname(name), target))
            require(target and '\0' not in target and logical != '..' and not logical.startswith('../'),
                    'Archive symlink escapes root')
        if name in ANCHORS:
            require((member.isdir() if ANCHORS[name] else member.isreg()) and member.size == 0 and
                    member.uid == member.gid == 0 and member.mode == (0o755 if ANCHORS[name] else 0o644) and
                    member.mtime == EPOCH and not member.pax_headers and not member.linkname and
                    not member.uname and not member.gname, 'Noncanonical runtime anchor')
    return {'members': len(by_name), 'special_nodes': 0}


def verify_exports(first, second):
    facts = seed.facts(first)
    require(seed.facts(second) == facts, 'Two exports of the same source differ')
    return facts


def compress(raw, destination, phase, report):
    producer.capacity(phase, raw.stat().st_size + raw.stat().st_size // 1000 + 1048576)
    with seed.regular(raw) as source:
        producer.write_private(destination, b'')
        with destination.open('r+b') as target:
            # gzip receives only held regular FDs; no shell/path interpretation.
            with (phase / 'gzip.log').open('xb') as log:
                args = ['/usr/bin/gzip', '-n', '-9']
                # Compression needs stdin/stdout FDs and the same watchdog.
                monitor(args, log, phase, report, source, target)
            target.flush()
            os.fsync(target.fileno())
    import gzip
    digest, size = hashlib.sha256(), 0
    with seed.regular(destination) as held, gzip.GzipFile(fileobj=held, mode='rb') as stream:
        for chunk in iter(lambda: stream.read(1048576), b''):
            digest.update(chunk)
            size += len(chunk)
    require({'bytes': size, 'sha256': digest.hexdigest()} == seed.facts(raw), 'Gzip roundtrip mismatch')
    return seed.facts(destination)


def monitor(args, log, phase, report, source=None, target=None):
    report['resources_before_launch'] = producer.capacity(phase)
    process = subprocess.Popen(args, env=seed.ENV, stdin=source,
                               stdout=target if target is not None else log,
                               stderr=log if target is not None else subprocess.STDOUT,
                               start_new_session=True)
    try:
        while True:
            sample = {'time_ns': time.time_ns(), **seed.resources(phase)}
            report['resource_samples'].append(sample)
            with (phase / 'resources.jsonl').open('ab') as stream:
                stream.write((json.dumps(sample) + '\n').encode())
            require(sample['free_bytes'] >= 3 * 1024**3, 'Disk cancellation threshold: 3 GiB')
            require(sample['available_memory_bytes'] >= 512 * 1024**2,
                    'RAM cancellation threshold: 512 MiB')
            try:
                status = process.wait(timeout=2)
                require(status == 0, 'Command failed with exit ' + str(status))
                return
            except subprocess.TimeoutExpired:
                continue
    finally:
        previous = {n: signal.signal(n, signal.SIG_IGN)
                    for n in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
        try:
            for number in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(process.pid, number)
                except ProcessLookupError:
                    pass
                if number == signal.SIGTERM:
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        pass
            process.wait()
            log.flush()
            os.fsync(log.fileno())
            if target is not None:
                target.flush()
                os.fsync(target.fileno())
        finally:
            for number, handler in previous.items():
                signal.signal(number, handler)


def checks(report, functions):
    errors = []
    for label, check in functions:
        try:
            check()
        except BaseException as exc:
            errors.append(label + ': ' + type(exc).__name__ + ': ' + str(exc))
    report['post_checks'] = {'success': not errors, 'errors': errors}
    report['success'] = report['success'] and not errors
    report['builder_frozen'] = report['success']


def freeze(source, output, repo=REPO):
    require(os.geteuid() == 0, 'Root required on the prepared AMD64 builder')
    project = binding(repo)
    records, expected, inventory_bytes = producer.inputs(repo)
    # validate_seed reads only external producer records, never rootfs files.
    producer.validate_seed(source, project)
    inventory_paths(source / 'rootfs')
    original = validate_producer(source, project, records, expected)
    output_guard(output, source, repo)
    if not WORKSPACE.exists():
        WORKSPACE.mkdir(mode=0o700)
        WORKSPACE.chmod(0o700)
    output.mkdir(mode=0o700)
    output.chmod(0o700)
    report = {'schema': 1, 'phase': 'freeze-builder', 'success': False, 'builder_frozen': False,
              'artifact_rebuilds': False, 'hardware_acceptance': False, 'project': project,
              'producer_records': original, 'resource_samples': [], 'source': str(source)}
    root = source / 'rootfs'
    require(stat.S_IMODE(root.stat().st_mode) == 0o700, 'Source root must remain 0700')
    before = host = cache = profile = staged = None
    pending = None
    fd = os.open(source / '.package-install.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'r+') as guard, seed.interruption_signals():
        try:
            info = os.fstat(guard.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1 and
                    stat.S_IMODE(info.st_mode) == 0o600, 'Unsafe source lock')
            fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
            seed.check_range_unused()
            seed.check_no_mounts(source)
            actual_inventory = producer.capture_inventory(repo)
            producer.write_private(output / 'host-inventory-before.tsv', actual_inventory)
            report['host_inventory_before'] = seed.facts(output / 'host-inventory-before.tsv')
            profile = seed.load_helper().check_builder(repo, 'replay-20261106')
            host = producer.host_state()
            report['host_configuration_before_sha256'] = hashlib.sha256(
                json.dumps(host, sort_keys=True).encode()).hexdigest()
            cache = records
            report['runtime_anchors'] = scaffold(root, create=True)
            names, before = snapshot(root)
            estimate = raw_estimate(root, names)
            report['resources_before_exports'] = producer.capacity(output, 2 * estimate)
            producer.write_private(output / 'files.list', b''.join(('./' + n).encode('utf-8', 'surrogateescape') + b'\0' for n in names), 0o444)
            producer.write_private(output / 'source-host-manifest.json', before)
            for action in ('export', 'restore', 'compare', 'inventory'):
                producer.write_private(output / (action + '.sh'), payload(action), 0o444)
            staged = {name: seed.facts(output / name) for name in
                      ('files.list', 'export.sh', 'restore.sh', 'compare.sh', 'inventory.sh')}
            report['staged'] = staged
            manifests = []
            for label in ('run1', 'run2'):
                destination = output / label
                destination.mkdir(mode=0o700)
                os.chown(destination, seed.UID_BASE, seed.UID_BASE)
                args = command(source, output, output / 'export.sh', destination, 'export')
                log = namespace_run(args, output, label, report)
                manifests.append(parsed_manifest(log))
                validate_namespace_source(before, manifests[-1])
                archive = destination / 'builder-root.tar'
                audit_archive(archive, names, synthetic=False)
                canonical_append(archive)
                report[label] = audit_archive(archive, names)
            require(manifests[0] == manifests[1], 'Source namespace metadata changed between exports')
            producer.write_private(output / 'source-namespace-manifest.json', manifests[0])
            report['manifest'] = seed.facts(output / 'source-namespace-manifest.json')
            raw = output / 'run1/builder-root.tar'
            report['raw_archive'] = verify_exports(raw, output / 'run2/builder-root.tar')
            report['gzip_binary'] = seed.facts(Path('/usr/bin/gzip'))
            report['compressed_archive'] = compress(raw, output / 'builder-root.tar.gz', output, report)
            producer.capacity(output, estimate)
            restored = output / 'restored-rootfs'
            restored.mkdir(mode=0o700)
            os.chown(restored, seed.UID_BASE, seed.UID_BASE)
            audit_archive(raw, names)
            namespace_run(command(source, output, output / 'restore.sh', restored, 'restore'), output, 'restore', report)
            log = namespace_run(command(source, output, output / 'compare.sh', restored, 'compare'), output, 'compare', report)
            compare_manifests(manifests[0], parsed_manifest(log))
            # Probe uses the restored OS itself and requires its exact installed inventory.
            log = namespace_run(command(source, output, output / 'inventory.sh', None, 'inventory', restored), output, 'restored-inventory', report)
            installed = producer.validate_installed(log, expected)
            producer.write_private(output / 'restored-installed.tsv', installed)
            report['restored_inventory'] = seed.facts(output / 'restored-installed.tsv')
            report['success'] = True
        except BaseException as exc:
            report['success'] = False
            report['error'] = type(exc).__name__ + ': ' + str(exc)
            pending = exc
        finally:
            functions = [('source_mounts', lambda: seed.check_no_mounts(source)),
                         ('output_mounts', lambda: seed.check_no_mounts(output)),
                         ('process_range', seed.check_process_ranges),
                         ('project', lambda: require(binding(repo) == project, 'Project changed')),
                         ('producer_records', lambda: require(all(seed.facts(source / name) == value for name, value in original.items()), 'Producer records changed')),
                         ('host_inventory', lambda: require(producer.capture_inventory(repo) == inventory_bytes, 'Host inventory changed'))]
            if before is not None:
                functions.append(('source_snapshot', lambda: require(snapshot(root) == (names, before), 'Real source changed')))
                functions.append(('anchors', lambda: require(scaffold(root) == report['runtime_anchors'], 'Runtime anchors changed')))
            if host is not None:
                def host_after():
                    observed = producer.host_state()
                    report['host_configuration_after_sha256'] = hashlib.sha256(
                        json.dumps(observed, sort_keys=True).encode()).hexdigest()
                    require(observed == host, 'Host configuration changed')
                functions.append(('host_configuration', host_after))
            def inventory_after():
                observed = producer.capture_inventory(repo)
                producer.write_private(output / 'host-inventory-after.tsv', observed)
                report['host_inventory_after'] = seed.facts(output / 'host-inventory-after.tsv')
                require(observed == inventory_bytes, 'Host inventory changed')
            functions.append(('host_inventory_bytes', inventory_after))
            if profile is not None:
                functions.append(('host_profile', lambda: require(seed.load_helper().check_builder(repo, 'replay-20261106') == profile, 'Host profile changed')))
            if cache is not None:
                functions.append(('producer_inputs', lambda: validate_producer(source, project, records, expected)))
            if staged is not None:
                functions.append(('staged', lambda: require(all(seed.facts(output / name) == value for name, value in staged.items()), 'Staged payload changed')))
            if 'compressed_archive' in report:
                functions.append(('artifacts', lambda: require(
                    seed.facts(output / 'builder-root.tar.gz') == report['compressed_archive'] and
                    seed.facts(output / 'run1/builder-root.tar') == report['raw_archive'] and
                    seed.facts(output / 'run2/builder-root.tar') == report['raw_archive'] and
                    seed.facts(output / 'source-namespace-manifest.json') == report['manifest'], 'Frozen artifact changed')))
            if 'gzip_binary' in report:
                functions.append(('gzip', lambda: require(seed.facts(Path('/usr/bin/gzip')) == report['gzip_binary'], 'gzip changed')))
            checks(report, functions)
            seed.write_json(output / 'receipt.json', report)
        if pending is not None:
            raise pending
        require(report['success'], 'Postchecks failed; inspect retained receipt')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed-output', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        freeze(args.seed_output, args.output)
    except (ValueError, OSError, subprocess.SubprocessError, InterruptedError) as exc:
        print('freeze-builder:', exc, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
