#!/usr/bin/env python3
"""Stage a pinned ARM64 installer runtime; never execute target programs or extractall."""
import argparse
import hashlib
import gzip
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tarfile

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('installer_state_guards', REPO / 'build/host-state.py')
STATE = importlib.util.module_from_spec(spec)
spec.loader.exec_module(STATE)


def record(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def relative(name):
    path = PurePosixPath(name)
    if not name or path.is_absolute() or '..' in path.parts or str(path) == '.':
        raise ValueError(f'unsafe archive path: {name!r}')
    return str(path)


def link_target(parent, target):
    if not target or '\x00' in target:
        raise ValueError('empty or invalid symlink')
    parts = [] if target.startswith('/') else parent.split('/') if parent else []
    for part in target.split('/'):
        if part in ('', '.'):
            continue
        if part == '..':
            if not parts:
                raise ValueError('symlink escapes archive')
            parts.pop()
        else:
            parts.append(part)
    return '/'.join(parts)


class Namespace:
    def __init__(self, archive):
        self.archive = archive
        self.entries = {}
        for member in archive.getmembers():
            if member.name in ('.', './') and member.isdir():
                continue
            name = relative(member.name)
            if name in self.entries:
                raise ValueError(f'duplicate archive member: {name}')
            if member.islnk():
                relative(member.linkname)  # Tar hardlinks are archive-root relative.
            if member.issym():
                link_target(name.rsplit('/', 1)[0] if '/' in name else '', member.linkname)
            self.entries[name] = member
        # An archive containing both an alias and children below it is ambiguous.
        for name in self.entries:
            for parent in PurePosixPath(name).parents:
                entry = self.entries.get(str(parent))
                if entry and not entry.isdir():
                    raise ValueError(f'non-directory archive ancestor: {parent}')
        for name, member in self.entries.items():
            if member.issym() or member.islnk():
                self.exists(name)  # Dangling aliases are permitted; cycles are not.

    def resolve(self, name):
        name = relative(name)
        seen = set()
        traversals = 0
        followed_hardlink = False
        while True:
            if name in seen:
                raise ValueError(f'symlink cycle: {name}')
            seen.add(name)
            parts = name.split('/')
            changed = False
            for count in range(1, len(parts) + 1):
                prefix = '/'.join(parts[:count])
                member = self.entries.get(prefix)
                if member and (member.issym() or member.islnk()):
                    traversals += 1
                    if traversals > 40:
                        raise ValueError(f'alias traversal limit/cycle: {prefix}')
                    if member.islnk():
                        if count != len(parts):
                            raise ValueError('hardlink is not a directory')
                        target = relative(member.linkname)
                        followed_hardlink = True
                    else:
                        base = prefix.rsplit('/', 1)[0] if '/' in prefix else ''
                        target = link_target(base, member.linkname)
                    name = '/'.join([target, *parts[count:]])
                    changed = True
                    break
            if not changed:
                if name not in self.entries:
                    raise ValueError(f'missing archive member: {name}')
                if followed_hardlink and not self.entries[name].isfile():
                    raise ValueError(f'hardlink target is not regular: {name}')
                return name

    def exists(self, name):
        try:
            self.resolve(name)
        except ValueError as error:
            if str(error).startswith('missing archive member:'):
                return False
            raise
        return True

    def read(self, name):
        source = self.resolve(name)
        member = self.entries[source]
        if not member.isfile() or member.size > 64 * 1024**2:
            raise ValueError(f'nonregular or oversized selected file: {source}')
        with self.archive.extractfile(member) as stream:
            data = stream.read()
        if len(data) != member.size:
            raise ValueError(f'truncated selected member: {source}')
        return source, data, member.mode

    def tree(self, name):
        source = self.resolve(name)
        if not self.entries[source].isdir():
            raise ValueError(f'selected tree is not a directory: {name}')
        found = []
        for path, member in sorted(self.entries.items()):
            if path.startswith(source + '/') and not member.isdir():
                found.append(name + path[len(source):])
        return found


def elf_dependencies(data, scratch):
    if data[:6] != b'\x7fELF\x02\x01' or len(data) < 64 or data[18:20] != b'\xb7\x00':
        raise ValueError('not ELF64 little-endian AArch64')
    scratch.write_bytes(data)
    try:
        result = subprocess.run(['/usr/bin/readelf', '-l', '-d', '--wide', str(scratch)],
                                check=True, text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env={**os.environ, 'LC_ALL': 'C'})
    finally:
        scratch.unlink()
    text = result.stdout
    needed = re.findall(r'\(NEEDED\).*?\[([^\]]+)\]', text)
    if any('/' in value or not re.fullmatch(r'[A-Za-z0-9_.+\-]+', value) for value in needed):
        raise ValueError('unsafe DT_NEEDED name')
    if re.search(r'\((?:RPATH|RUNPATH)\)', text):
        raise ValueError('unsupported ELF search path override')
    interpreters = re.findall(r'Requesting program interpreter:\s*([^\]]+)\]', text)
    if any(value != '/lib/ld-linux-aarch64.so.1' for value in interpreters):
        raise ValueError('unexpected ELF interpreter')
    return needed, interpreters


def validate_output(output):
    output = Path(output)
    parent = REPO / 'out'
    if (not output.is_absolute() or output.parent != parent or
            not output.name.startswith('installer-runtime-') or output.name == 'installer-runtime-' or
            output.exists() or output.is_symlink()):
        raise ValueError('output must be a new absolute repo/out/installer-runtime-NAME')
    if parent.resolve() != parent or parent.is_symlink():
        raise ValueError('symlink output parent refused')
    return output


def builder_tools():
    rows = [line.split('\t') for line in (REPO / 'configs/builder-packages.lock.tsv').read_text().splitlines()]
    expected = [row for row in rows if row[0] == 'binutils']
    if len(expected) != 1:
        raise ValueError('missing/ambiguous builder binutils lock')
    actual = subprocess.check_output(['dpkg-query', '-W', '-f=${Package}\t${Version}\t${Architecture}\n',
                                      'binutils'], text=True).strip().split('\t')
    if actual != expected[0]:
        raise ValueError('builder binutils version differs from lock')
    executable = Path('/usr/bin/readelf').resolve(strict=True)
    owner = subprocess.check_output(['dpkg-query', '-S', str(executable)], text=True).strip()
    # readelf is supplied by the architecture-specific binutils package.
    package = owner.split(': /', 1)[0]
    locked = [row for row in rows if row[0] == package]
    actual_owner = subprocess.check_output(['dpkg-query', '-W', '-f=${Package}\t${Version}\t${Architecture}\n',
                                            package], text=True).strip().split('\t')
    if len(locked) != 1 or actual_owner != locked[0]:
        raise ValueError('readelf provider differs from builder lock')
    return {'packages': [actual, actual_owner], 'readelf': str(executable),
            'readelf_record': record(executable.read_bytes()),
            'method': 'pinned native readelf parses target ELF without target execution'}


def stage(namespace, output, policy):
    files = {}
    queue = []
    closure = {}
    def install(target, source, executable=False):
        target = relative(target)
        resolved, data, mode = namespace.read(source)
        permissions = 0o755 if executable or mode & 0o111 else 0o644
        info = {'type': 'file', 'source': source, 'resolved_source': resolved,
                'mode': permissions, **record(data)}
        if target in files:
            if files[target] != info:
                raise ValueError(f'conflicting output: {target}')
            return
        path = output / target
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream:
            stream.write(data)
        path.chmod(permissions)
        files[target] = info
        if (executable or data.startswith(b'\x7fELF') or
                ('/lib-dynload/' in target and target.endswith('.so')) or
                ('/ossl-modules/' in target and target.endswith('.so'))):
            queue.append((target, data))
    def library(name, optional=False):
        if not re.fullmatch(r'[A-Za-z0-9_.+\-]+', name):
            raise ValueError('unsafe library name')
        candidates = [prefix + '/' + name for prefix in policy['library_directories']
                      if namespace.exists(prefix + '/' + name)]
        if not candidates:
            if optional:
                return
            raise ValueError(f'missing ELF dependency: {name}')
        if len({record(namespace.read(path)[1])['sha256'] for path in candidates}) != 1:
            raise ValueError(f'ambiguous ELF dependency: {name}')
        target = policy['loader'] if name == 'ld-linux-aarch64.so.1' else 'lib/aarch64-linux-gnu/' + name
        # Canonicalize source so merged-/usr aliases produce identical records.
        install(target, namespace.resolve(candidates[0]))
    for name in policy['executables']:
        install(name, name, executable=True)
    for tree in policy['required_trees'] + policy['optional_trees']:
        if tree in policy['optional_trees'] and not namespace.exists(tree):
            continue
        for name in namespace.tree(tree):
            install(name, name)
    for name in policy['required_data'] + policy['optional_data']:
        if name in policy['optional_data'] and not namespace.exists(name):
            continue
        install(name, name)
    for name in policy['optional_libraries']:
        library(name, optional=True)
    # Copy every package notice, including /usr/share/doc package-directory aliases.
    packages = set()
    for name in namespace.entries:
        parts = name.split('/')
        if len(parts) >= 4 and parts[:3] == ['usr', 'share', 'doc']:
            packages.add(parts[3])
    for package in sorted(packages):
        name = 'usr/share/doc/' + package + '/copyright'
        if namespace.exists(name):
            install(name, name)
    if namespace.exists('usr/share/common-licenses'):
        for name in namespace.tree('usr/share/common-licenses'):
            install(name, name)
    while queue:
        target, data = queue.pop()
        needed, interpreters = elf_dependencies(data, output / '.readelf-input')
        closure[target] = {'needed': needed, 'interpreters': interpreters}
        for name in needed:
            library(name)
        if interpreters:
            library('ld-linux-aarch64.so.1')
    for target, link in policy['symlinks'].items():
        target = relative(target)
        resolved = link_target(target.rsplit('/', 1)[0], link)
        if resolved not in files or target in files:
            raise ValueError('invalid output alias')
        (output / target).symlink_to(link)
        files[target] = {'type': 'symlink', 'target': link, 'mode': 0o777, 'source': target,
                         'source_target': namespace.entries[target].linkname if target in namespace.entries else None}
    return files, closure


def private_output(path):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    return os.fdopen(descriptor, 'wb')


def capture_archive(source, target, expected):
    """Hash the exact private snapshot bytes before opening any archive parser."""
    digest = hashlib.sha256()
    size = 0
    with private_output(target) as destination:
        while True:
            data = source.read(min(1024**2, expected['bytes'] - size + 1))
            if not data:
                break
            size += len(data)
            if size > expected['bytes']:
                raise ValueError('userspace archive exceeds pinned size')
            destination.write(data)
            digest.update(data)
    if {'bytes': size, 'sha256': digest.hexdigest()} != expected:
        raise ValueError('userspace archive hash/size mismatch')


def decompress_archive(source, target, limit=1024**3):
    """Decompress once for seekable member reads; refuse gzip expansion bombs."""
    size = 0
    with gzip.open(source, 'rb') as stream, private_output(target) as destination:
        while True:
            data = stream.read(min(1024**2, limit - size + 1))
            if not data:
                break
            size += len(data)
            if size > limit:
                raise ValueError('userspace tar exceeds 1 GiB expansion cap')
            destination.write(data)
    return size


def package(source, output):
    policy_path = REPO / 'configs/installer-runtime-policy.json'
    policy = json.loads(policy_path.read_text())
    expected = json.loads((REPO / policy['source_evidence']).read_text())['files']['rootfs.tar.gz']
    if policy['archive'] != expected:
        raise ValueError('policy archive differs from source evidence')
    output = validate_output(output)
    tools = builder_tools()
    # All writes, including the private authenticated snapshot, require fixed storage.
    with STATE.regular_input(source) as stream:
        output.parent.mkdir(exist_ok=True)
        storage = STATE.validate_workspace_storage(output.parent)
        if shutil.disk_usage(output.parent).free < 2 * 1024**3:
            raise ValueError('insufficient staging space: require 2 GiB')
        output.mkdir(mode=0o700)
        captured = output / '.userspace-input.tar.gz'
        capture_archive(stream, captured, expected)
    unpacked = output / '.userspace-input.tar'
    decompress_archive(captured, unpacked)
    with unpacked.open('rb') as stream, tarfile.open(fileobj=stream, mode='r:') as archive:
        namespace = Namespace(archive)
        root = output / 'root'
        root.mkdir(mode=0o755)
        files, closure = stage(namespace, root, policy)
    captured.unlink()
    unpacked.unlink()
    provenance = {}
    for path in (policy_path, Path(__file__).resolve(), REPO / 'build/host-state.py',
                 REPO / 'configs/host_config.py', REPO / policy['source_evidence'],
                 REPO / 'configs/host-userspace-packages.lock.tsv',
                 REPO / 'configs/host-userspace-package-hashes.lock.tsv',
                 REPO / 'configs/builder-packages.lock.tsv'):
        data = path.read_bytes()
        name = 'usr/share/installer-runtime/provenance/' + path.name
        destination = output / 'root' / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open('xb') as stream:
            stream.write(data)
        destination.chmod(0o644)
        files[name] = {'type': 'file', 'mode': 0o644, 'source': str(path.relative_to(REPO)), **record(data)}
        provenance[str(path.relative_to(REPO))] = record(data)
    result = {'schema': 1, 'classification': policy['acceptance'], 'source': expected,
              'policy': record(policy_path.read_bytes()), 'provenance': provenance,
              'input_capture': 'authenticated private compressed snapshot; single bounded 1 GiB decompression',
              'builder_tools': tools, 'staging_storage': storage, 'elf_closure': closure,
              'packages': [line.split('\t') for line in (REPO / 'configs/host-userspace-packages.lock.tsv').read_text().splitlines()],
              'files': files, 'regular_file_count': sum(v['type'] == 'file' for v in files.values()),
              'total_uncompressed_bytes': sum(v.get('bytes', 0) for v in files.values()),
              'totals_scope': 'installed regular files including provenance; excludes this self-referential manifest'}
    with (output / 'runtime-manifest.json').open('x') as stream:
        json.dump(result, stream, indent=2, sort_keys=True)
        stream.write('\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('userspace_tar')
    parser.add_argument('new_output', type=Path)
    args = parser.parse_args()
    try:
        result = package(args.userspace_tar, args.new_output)
    except (OSError, EOFError, ValueError, tarfile.TarError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'installer runtime: {error}\n')
    print(json.dumps({key: result[key] for key in ('regular_file_count', 'total_uncompressed_bytes')}))


if __name__ == '__main__':
    main()
