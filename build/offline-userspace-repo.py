#!/usr/bin/env python3
"""Build a bounded, signature-authenticated, incomplete offline Debian mirror.

Only the 220 locked binaries are copied. The signed full index is unmodified:
APT must fail offline if a dependency outside this inventory is requested.
No package scripts, network client, or rootfs archive are used.
"""
import argparse
import hashlib
import json
import lzma
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess

ROOT = Path(__file__).resolve().parents[1]
VERSION_LOCK = ROOT / 'configs/host-userspace-packages.lock.tsv'
HASH_LOCK = ROOT / 'configs/host-userspace-package-hashes.lock.tsv'
PINS = {
    'InRelease': (151075, '77737fa4b34f2693e982cc9ee35736816c35a7778fc2d326cc1bbf5b301fe1aa'),
    'debian-archive-keyring.gpg': (55918, '506b815cbb32d9b6066b4a2aa524071e071761e7e7f68c3ac74f3061ba852017'),
    'Packages.xz': (8689464, '2ddb1737692e8c45c53e8d57c0ce4cd21c78c5703b830c3226b1423566a06c00'),
    'Packages': (49434864, 'c7c883a61f348283050d3a754e7c8119117c4019bf65da804c78fdae315866f1'),
    'version-lock': (6671, 'f2abf03d0d6e7fd506560689093220514cbf1acc678f7560562026d6ca6564a6'),
    'hash-lock': (28638, 'f2f613e79d9c1abc1689e19cfe81b624d9d2ac3beeb322fd5788980d919f7d86'),
}
PACKAGE_COUNT = 220
LIMIT = 512 * 1024 * 1024
PSEUDO_FS = {'proc', 'sysfs', 'devtmpfs', 'devpts', 'tmpfs', 'cgroup', 'cgroup2',
             'securityfs', 'debugfs', 'tracefs', 'configfs', 'fusectl', 'mqueue',
             'hugetlbfs', 'rpc_pipefs', 'autofs', 'binfmt_misc'}


def digest(path):
    h = hashlib.sha256()
    size = 0
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
            size += len(chunk)
    return size, h.hexdigest()


def physical(path):
    """Reject ambiguous spellings and every existing symlink ancestor."""
    path = Path(path)
    if '..' in path.parts or not path.is_absolute():
        raise ValueError('paths must be absolute and contain no parent traversal')
    current = Path('/')
    for part in path.parts[1:]:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            break
        if stat.S_ISLNK(info.st_mode):
            raise ValueError(f'symlink path: {current}')
    if filesystem_location(path)[2] in PSEUDO_FS:
        raise ValueError(f'pseudo filesystem: {path}')
    return path


def filesystem_location(path):
    """Map bind-mount aliases to a device and filesystem-relative pathname."""
    mounts = []
    def unescape(value):
        return re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), value)
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        left, right = line.split(' - ', 1)
        fields = left.split()
        mount = Path(unescape(fields[4]))
        if path == mount or mount in path.parents:
            location = Path(unescape(fields[3])) / path.relative_to(mount)
            mounts.append((len(mount.parts), fields[2], location, right.split()[0]))
    if not mounts:
        raise ValueError(f'unknown filesystem: {path}')
    _, device, location, filesystem = max(mounts)
    return device, location, filesystem


def open_regular(path):
    """Walk directory descriptors so no ancestor or leaf symlink is followed."""
    path = physical(path)
    descriptor = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            new = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                          dir_fd=descriptor)
            os.close(descriptor)
            descriptor = new
        result = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=descriptor)
    finally:
        os.close(descriptor)
    if not stat.S_ISREG(os.fstat(result).st_mode):
        os.close(result)
        raise ValueError(f'not a regular input: {path}')
    return result


def identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def safe_filename(value, prefix='pool'):
    parts = PurePosixPath(value).parts
    if (not value or value.startswith('/') or '\\' in value or
            any(ord(c) < 32 or ord(c) == 127 for c in value) or
            any(p in ('', '.', '..') for p in value.split('/')) or
            not parts or parts[0] != prefix or len(parts) < 2):
        raise ValueError(f'unsafe {prefix} filename: {value!r}')
    return value


def stanzas(path):
    fields = {}
    last = None
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            line = line.rstrip('\n')
            if not line:
                if fields:
                    yield fields
                fields, last = {}, None
            elif line.startswith((' ', '\t')):
                if last is None:
                    raise ValueError('orphan index continuation')
                fields[last] += '\n' + line
            else:
                key, sep, value = line.partition(':')
                if not sep or key in fields:
                    raise ValueError('malformed or duplicate index field')
                fields[key] = value.strip()
                last = key
    if fields:
        yield fields


class Builder:
    def __init__(self, output):
        self.output = output
        self.consumed = 0
        self.sources = []

    def charge(self, amount):
        self.consumed += amount
        if self.consumed > LIMIT:
            raise ValueError('unexpected staging total exceeds 512 MiB')

    def hold(self, source, target, expected=None, expected_sha=None):
        descriptor = open_regular(source)
        try:
            before = os.fstat(descriptor)
            if before.st_size > LIMIT - self.consumed:
                raise ValueError('unexpected input size exceeds staging bound')
            if expected and before.st_size != expected[0]:
                raise ValueError(f'input size mismatch: {source}')
            target.parent.mkdir(parents=True, exist_ok=True)
            h = hashlib.sha256()
            size = 0
            with os.fdopen(os.dup(descriptor), 'rb') as stream, target.open('xb') as out:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    self.charge(len(chunk))
                    size += len(chunk)
                    h.update(chunk)
                    out.write(chunk)
            result = (size, h.hexdigest())
            if identity(before) != identity(os.fstat(descriptor)):
                raise ValueError(f'input changed while copying: {source}')
            if expected and result != expected:
                raise ValueError(f'input hash/size mismatch: {source}')
            if expected_sha and result[1] != expected_sha:
                raise ValueError(f'package hash mismatch: {source}')
            if digest(target) != result:
                raise ValueError('private copy changed')
            target.chmod(0o444)
            self.sources.append((source, identity(before), result))
            return result
        finally:
            os.close(descriptor)

    def recheck(self):
        for source, initial, expected in self.sources:
            descriptor = open_regular(source)
            try:
                if identity(os.fstat(descriptor)) != initial:
                    raise ValueError(f'original input changed: {source}')
                h, size = hashlib.sha256(), 0
                with os.fdopen(os.dup(descriptor), 'rb') as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        h.update(chunk)
                        size += len(chunk)
                if (size, h.hexdigest()) != expected or identity(os.fstat(descriptor)) != initial:
                    raise ValueError(f'original input changed: {source}')
            finally:
                os.close(descriptor)

    def command(self, name, arguments):
        result = subprocess.run(arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                check=False, env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'})
        for suffix, data in [('stdout', result.stdout), ('stderr', result.stderr)]:
            self.charge(len(data))
            (self.output / 'verification' / f'{name}.{suffix}').write_bytes(data)
        if result.returncode:
            raise ValueError(f'{name} failed with status {result.returncode}; see verification logs')
        return result.stdout


def locks(version_path, hash_path):
    rows = []
    hashes = {}
    for line in hash_path.read_text().splitlines():
        name, version, rest = line.split('\t')
        sha, filename = rest.split('  ')
        safe_filename(filename, 'debs')
        if not re.fullmatch('[0-9a-f]{64}', sha) or (name, version) in hashes:
            raise ValueError('invalid or duplicate package hash lock')
        hashes[name, version] = (sha, filename)
    names = set()
    for line in version_path.read_text().splitlines():
        name, version, arch = line.split('\t')
        if name in names or not name or not version or arch not in ('all', 'arm64'):
            raise ValueError('invalid or duplicate package tuple')
        names.add(name)
        sha, filename = hashes.pop((name, version))
        rows.append({'Package': name, 'Version': version, 'Architecture': arch,
                     'SHA256': sha, 'cache_filename': filename})
    if hashes or len(rows) != PACKAGE_COUNT:
        raise ValueError('package locks do not specify exactly 220 matching records')
    return rows


def release_index(release):
    fields = list(stanzas(release))
    if len(fields) != 1 or fields[0].get('Codename') != 'bookworm':
        raise ValueError('unexpected authenticated Release codename')
    result = {}
    for line in fields[0].get('SHA256', '').splitlines():
        if not line.strip():
            continue
        sha, size, filename = line.split()
        if filename in result:
            raise ValueError('duplicate Release SHA256 entry')
        result[filename] = (int(size), sha)
    for name in ('Packages', 'Packages.xz'):
        if result.get(f'main/binary-arm64/{name}') != PINS[name]:
            raise ValueError(f'authenticated Release does not advertise pinned {name}')


def match_index(path, inventory):
    wanted = {(r['Package'], r['Version'], r['Architecture']): r for r in inventory}
    found = set()
    for entry in stanzas(path):
        key = tuple(entry.get(field) for field in ('Package', 'Version', 'Architecture'))
        if key not in wanted:
            continue
        if key in found:
            raise ValueError(f'duplicate/conflicting locked index stanza: {key}')
        found.add(key)
        record = wanted[key]
        filename = safe_filename(entry['Filename'])
        if entry['SHA256'] != record['SHA256'] or not re.fullmatch('[0-9]+', entry['Size']):
            raise ValueError(f'locked index hash/size mismatch: {key}')
        size = int(entry['Size'])
        if size <= 0 or size > LIMIT:
            raise ValueError('invalid binary package size')
        record.update({'Filename': filename, 'Size': size})
    if found != wanted.keys():
        raise ValueError('signed index is missing a locked tuple/version/architecture')
    if len({r['Filename'] for r in inventory}) != len(inventory):
        raise ValueError('conflicting pool filenames')


def construct(cache: Path, packages_xz: Path, output: Path) -> dict:
    cache, packages_xz, output = map(physical, (cache, packages_xz, output))
    if not cache.is_dir() or not output.parent.is_dir():
        raise ValueError('cache and output parent must be existing physical directories')
    inputs = (cache, packages_xz, VERSION_LOCK, HASH_LOCK)
    output_device, output_location, _ = filesystem_location(output)
    for source in inputs:
        source_device, source_location, _ = filesystem_location(source)
        if (source == output or source in output.parents or output in source.parents or
                (source_device == output_device and
                 (source_location == output_location or source_location in output_location.parents or
                  output_location in source_location.parents))):
            raise ValueError('output overlaps or aliases inputs')
    cache_id = (cache.stat().st_dev, cache.stat().st_ino)
    if any((p.stat().st_dev, p.stat().st_ino) == cache_id
           for p in (output.parent, *output.parent.parents)):
        raise ValueError('output parent aliases or lies within cache')
    if output.exists() or output.is_symlink():
        raise ValueError('output must be new')
    if shutil.disk_usage(output.parent).free < LIMIT:
        raise ValueError('at least 512 MiB staging free space required')
    output.mkdir(mode=0o755)
    builder = Builder(output)
    try:
        verify = output / 'verification'
        verify.mkdir()
        dists = output / 'dists/bookworm'
        binary = dists / 'main/binary-arm64'
        binary.mkdir(parents=True)
        builder.hold(cache / 'InRelease', dists / 'InRelease', PINS['InRelease'])
        builder.hold(cache / 'debian-archive-keyring.gpg', output / 'verified-keyring.gpg',
                     PINS['debian-archive-keyring.gpg'])
        builder.hold(packages_xz, binary / 'Packages.xz', PINS['Packages.xz'])
        builder.hold(VERSION_LOCK, verify / 'versions.tsv', PINS['version-lock'])
        builder.hold(HASH_LOCK, verify / 'hashes.tsv', PINS['hash-lock'])
        # A private empty home prevents ambient trust/configuration from contributing.
        home = verify / 'gnupg'
        home.mkdir(mode=0o700)
        builder.command('gpgv', ['gpgv', '--homedir', str(home), '--keyring',
                               str(output / 'verified-keyring.gpg'), '--output',
                               str(dists / 'Release'), str(dists / 'InRelease')])
        builder.charge((dists / 'Release').stat().st_size)
        release_pin = digest(dists / 'Release')
        (dists / 'Release').chmod(0o444)
        release_index(dists / 'Release')
        h, size = hashlib.sha256(), 0
        with lzma.open(binary / 'Packages.xz', 'rb') as source, (binary / 'Packages').open('xb') as target:
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                builder.charge(len(chunk))
                size += len(chunk)
                if size > PINS['Packages'][0]:
                    raise ValueError('expanded index exceeds pinned size')
                h.update(chunk)
                target.write(chunk)
        if (size, h.hexdigest()) != PINS['Packages']:
            raise ValueError('expanded index hash/size mismatch')
        (binary / 'Packages').chmod(0o444)
        inventory = locks(verify / 'versions.tsv', verify / 'hashes.tsv')
        match_index(binary / 'Packages', inventory)
        for number, record in enumerate(inventory):
            destination = output / record['Filename']
            held = verify / 'package-inputs' / f'{number:03d}.deb'
            builder.hold(cache / record['cache_filename'], held,
                         (record['Size'], record['SHA256']))
            control = builder.command(f'dpkg-deb-{number:03d}', ['dpkg-deb', '--show',
                                      '--showformat=${Package}\t${Version}\t${Architecture}\n',
                                      str(held)]).decode().strip().split('\t')
            if control != [record[k] for k in ('Package', 'Version', 'Architecture')]:
                raise ValueError('actual deb control tuple does not match locks')
            if digest(held) != (record['Size'], record['SHA256']):
                raise ValueError('deb changed during metadata verification')
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.rename(held, destination)
        builder.recheck()
        for record in inventory:
            if digest(output / record['Filename']) != (record['Size'], record['SHA256']):
                raise ValueError('private pool package changed')
        # Recheck every trust-bearing private input after its consumers finish.
        for path, pin in ((dists / 'InRelease', PINS['InRelease']),
                          (output / 'verified-keyring.gpg', PINS['debian-archive-keyring.gpg']),
                          (binary / 'Packages.xz', PINS['Packages.xz']),
                          (binary / 'Packages', PINS['Packages']),
                          (dists / 'Release', release_pin),
                          (verify / 'versions.tsv', PINS['version-lock']),
                          (verify / 'hashes.tsv', PINS['hash-lock'])):
            if digest(path) != pin:
                raise ValueError('private authentication input changed')
        bom = []
        for path in sorted(output.rglob('*')):
            if path.is_symlink():
                raise ValueError('unexpected output symlink')
            if path.is_file():
                size, sha = digest(path)
                bom.append({'path': str(path.relative_to(output)), 'bytes': size, 'sha256': sha})
                path.chmod(0o444)
            elif path.is_dir():
                path.chmod(0o755)
            else:
                raise ValueError('unexpected output special file')
        manifest = {'schema': 1, 'status': 'verified', 'snapshot': '20261001T000000Z',
                    'suite': 'bookworm', 'source_pins': {k: {'bytes': v[0], 'sha256': v[1]}
                                                        for k, v in PINS.items()},
                    'packages': inventory, 'package_count': len(inventory), 'files': bom,
                    'bom_sha256': hashlib.sha256(json.dumps(bom, sort_keys=True,
                                                           separators=(',', ':')).encode()).hexdigest(),
                    'bom_scope': 'all regular repository and verification files, excluding manifest.json',
                    'staged_bytes': builder.consumed,
                    'classification': 'offline package inputs; no userspace execution or hardware acceptance'}
        # A success receipt appears atomically only after every validation passes.
        temporary = output / '.manifest.tmp'
        temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
        temporary.chmod(0o444)
        os.replace(temporary, output / 'manifest.json')
        output.chmod(0o755)
        return manifest
    except Exception as error:
        (output / 'failure.json').write_text(json.dumps({'status': 'failed', 'error': str(error)}, indent=2) + '\n')
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', required=True, type=Path)
    parser.add_argument('--packages-xz', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    manifest = construct(args.cache.absolute(), args.packages_xz.absolute(), args.output.absolute())
    print(json.dumps({'status': manifest['status'], 'package_count': manifest['package_count'],
                      'bom_sha256': manifest['bom_sha256']}))


if __name__ == '__main__':
    main()
