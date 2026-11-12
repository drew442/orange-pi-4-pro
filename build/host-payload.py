#!/usr/bin/env python3
"""Package authenticated interim userspace into immutable regular-file A/B payloads.

No installer, mounts, network access, chroot execution or hardware acceptance.
Run as root: host-payload.py ROOTFS_TAR DIAGNOSTIC_ARTIFACT_DIR NEW_OUTPUT_DIR
"""
import argparse
import gzip
import hashlib
import importlib.util
import json
import os
import re
from pathlib import Path, PurePosixPath
import shutil
import stat
import struct
import subprocess
import tarfile
import tempfile

REPO = Path(__file__).resolve().parent.parent
package_spec = importlib.util.spec_from_file_location('payload_package_state', REPO / 'configs/package_state.py')
PACKAGE = importlib.util.module_from_spec(package_spec)
package_spec.loader.exec_module(PACKAGE)
config_spec = importlib.util.spec_from_file_location('payload_host_config', REPO / 'configs/host_config.py')
CONFIG = importlib.util.module_from_spec(config_spec)
config_spec.loader.exec_module(CONFIG)

# These shared modules are also packaged for runtime readers. Profile policy is
# source-only and loaded only when a production build explicitly resolves it.
DEFAULT_USERSPACE_PROFILE = 'baseline-20261104'
USERSPACE_PROFILES = (DEFAULT_USERSPACE_PROFILE, 'normalized-20261106', 'timesync-20261106')
TIMESYNC_PROFILE = 'timesync-20261106'

SLOT_BYTES = 4 * 1024**3
KERNEL_RELEASE = '6.6.98-a733-diag'
SLOT_UUIDS = {'A': 'a7330000-0000-4000-8000-00000000000a',
              'B': 'a7330000-0000-4000-8000-00000000000b'}
CLASSIFICATION = 'managed host root prototype; installation and hardware acceptance pending'
BOOT_FILES = ('Image', 'board.dtb', 'kernel.config', 'initramfs.cpio.gz')
MANAGEMENT_FILES = {
    'configs/package_state.py': ('usr/lib/orangepi-hypervisor/package_state.py', 0o644),
    'configs/host-management.py': ('usr/lib/orangepi-hypervisor/host-management.py', 0o755),
    'configs/host_config.py': ('usr/lib/orangepi-hypervisor/host_config.py', 0o644),
    'configs/a733-management-ssh.service': ('usr/lib/systemd/system/a733-management-ssh.service', 0o644),
    'configs/a733-guest-storage.service': ('usr/lib/systemd/system/a733-guest-storage.service', 0o644),
    'configs/a733-guest-prepare.service': ('usr/lib/systemd/system/a733-guest-prepare.service', 0o644),
}


def file_record(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(data)
    return {'bytes': Path(path).stat().st_size, 'sha256': digest.hexdigest()}


def userspace_backend():
    spec = importlib.util.spec_from_file_location('payload_userspace_profiles', REPO / 'build/userspace-profiles.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if module.DEFAULT != DEFAULT_USERSPACE_PROFILE or module.PROFILES != USERSPACE_PROFILES:
        raise ValueError('userspace profile interface differs from production builders')
    return module


def userspace_binding(profile=DEFAULT_USERSPACE_PROFILE, repo=None):
    """Resolve source-only profile policy against the caller's repository."""
    return userspace_backend().resolve(profile, repo=REPO if repo is None else repo)


def clock_state_policy(profile, repo=None):
    """Bind the additive NVMe clock directory to its guarded implementation."""
    if profile not in USERSPACE_PROFILES:
        raise ValueError('unknown userspace profile')
    if profile != TIMESYNC_PROFILE:
        return None
    repository = REPO if repo is None else Path(repo)
    source = require_regular(repository / 'build/timesync-state.py')
    return {'schema': 1, 'identity': 996, 'directory': 'var/lib/systemd/timesync',
            'helper': file_record(source)}


def management_profile(metadata):
    return 'a733-management-v2-timesync' if CONFIG.timesync_policy(metadata) is not None else 'a733-management-v1'


def validate_source_bindings(actual, expected):
    """Bind a captured root's feature and archive provenance to its manifest."""
    if management_profile(actual) != management_profile(expected):
        raise ValueError('root feature policy differs from trusted manifest')
    left, right = actual.get('sources', {}), expected.get('sources', {})
    if type(left) is not dict or type(right) is not dict:
        raise ValueError('root source bindings must be objects')
    for name in ('rootfs.tar.gz', 'userspace_profile', 'timesync_state'):
        left_json = json.dumps(left.get(name), sort_keys=True, allow_nan=False)
        right_json = json.dumps(right.get(name), sort_keys=True, allow_nan=False)
        if (name in left) != (name in right) or left_json != right_json:
            raise ValueError(f'root {name} differs from trusted manifest')


def provenance_paths(paths):
    """Deduplicate identical paths, rejecting ambiguous flattened names."""
    selected = {}
    for path in paths:
        path = Path(path)
        if path.name in selected and selected[path.name] != path:
            raise ValueError(f'provenance basename collision: {path.name}')
        selected[path.name] = path
    return tuple(selected.values())


def require_regular(path):
    path = Path(path)
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError(f'not a regular non-symlink input: {path}')
    return path


def authenticate(path, expected):
    require_regular(path)
    if file_record(path) != expected:
        raise ValueError(f'input hash or size mismatch: {path}')


def archive_path(name):
    if not name or '\x00' in name or name.startswith('/'):
        raise ValueError(f'unsafe archive path: {name!r}')
    path = PurePosixPath(name)
    if '..' in path.parts:
        raise ValueError(f'archive traversal: {name!r}')
    return path


def validate_tar(members):
    """Check complete namespace before extracting anything (including late links)."""
    entries = {}
    for member in members:
        path = archive_path(member.name)
        if path in entries:
            raise ValueError(f'duplicate tar path: {path}')
        entries[path] = member
        if path == PurePosixPath('.') and not member.isdir():
            raise ValueError('archive root must be a directory')
        if not (member.isdir() or member.isfile() or member.issym() or
                member.islnk() or member.ischr() or member.isfifo()):
            raise ValueError(f'unsupported tar entry: {path}')
    for path, member in entries.items():
        for ancestor in path.parents:
            if ancestor in entries and not entries[ancestor].isdir():
                raise ValueError(f'non-directory tar ancestor: {path}')
        if member.issym():
            # Absolute links are rooted at the future filesystem root. Relative
            # links may not walk above it, even when they are leaf entries.
            depth = len(path.parent.parts) if not member.linkname.startswith('/') else 0
            for component in PurePosixPath(member.linkname).parts:
                if component == '..':
                    depth -= 1
                    if depth < 0:
                        raise ValueError(f'escaping tar symlink: {path}')
                elif component not in ('/', '.'):
                    depth += 1
        if member.islnk():
            target = archive_path(member.linkname)
            if target not in entries or not entries[target].isfile():
                raise ValueError(f'unsafe tar hardlink: {path}')
    return entries


METADATA_PROFILES = ('native', 'fakeroot-raw-xattrs-v1')


def metadata_backend(profile):
    if profile not in METADATA_PROFILES:
        raise ValueError('unknown metadata profile: ' + str(profile))
    if profile == 'native':
        return None
    spec = importlib.util.spec_from_file_location('payload_rootfs_metadata', REPO / 'build/rootfs-metadata.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_metadata_profile(profile):
    backend = metadata_backend(profile)
    if backend is not None:
        backend.validate_profile()
    return backend


def extract_rootfs(archive, root, metadata_profile='native', expected=None):
    backend = metadata_backend(metadata_profile)
    if backend is not None:
        return backend.extract(archive, root, expected, validate_tar)
    with tarfile.open(archive, 'r:*') as source:
        members = source.getmembers()
        validate_tar(members)
    # GNU tar preserves the --xattrs/--acls metadata emitted by the userspace
    # recipe; Python tarfile does not restore these PAX fields. Namespace was
    # checked in full above, including links declared after their descendants.
    subprocess.run(['tar', '--extract', '--file', str(archive), '--directory', str(root),
                    '--numeric-owner', '--same-owner', '--same-permissions',
                    '--xattrs', '--xattrs-include=*', '--acls'], check=True)


def module_entries(data):
    """Parse gzip-decoded newc; return ONLY regular modules and directories."""
    offset = 0
    modules = {}
    seen = set()
    trailer = False
    while offset + 110 <= len(data):
        header = data[offset:offset + 110]
        if header[:6] != b'070701':
            raise ValueError('expected newc archive')
        try:
            values = [int(header[n:n + 8], 16) for n in range(6, 110, 8)]
        except ValueError as error:
            raise ValueError('invalid newc header') from error
        mode, nlink, size, namesize = values[1], values[4], values[6], values[11]
        offset += 110
        if namesize < 1 or offset + namesize > len(data):
            raise ValueError('truncated newc name')
        raw_name = data[offset:offset + namesize]
        if raw_name[-1:] != b'\0' or b'\0' in raw_name[:-1]:
            raise ValueError('invalid newc name')
        name = raw_name[:-1].decode('utf-8')
        offset = (offset + namesize + 3) & ~3
        if offset + size > len(data):
            raise ValueError('truncated newc content')
        content = data[offset:offset + size]
        offset = (offset + size + 3) & ~3
        if name == 'TRAILER!!!':
            if size or any(data[offset:]):
                raise ValueError('unexpected newc trailer data')
            trailer = True
            break
        path = archive_path(name)
        if path in seen:
            raise ValueError(f'duplicate newc entry: {path}')
        seen.add(path)
        if path.parts[:2] == ('lib', 'modules'):
            if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                raise ValueError(f'non-regular module archive entry: {path}')
            if stat.S_ISREG(mode) and nlink != 1:
                raise ValueError(f'hardlinked module: {path}')
            if stat.S_ISDIR(mode) and size:
                raise ValueError(f'directory module content: {path}')
            modules[path] = (mode, content)
    if not trailer:
        raise ValueError('missing newc trailer')
    releases = {p.parts[2] for p in modules if len(p.parts) > 2}
    if releases != {KERNEL_RELEASE} or not any(
            p.name.endswith('.ko') and stat.S_ISREG(v[0]) for p, v in modules.items()):
        raise ValueError('missing modules or incompatible kernel module ABI')
    for path in modules:
        for parent in path.parents:
            if parent in modules and not stat.S_ISDIR(modules[parent][0]):
                raise ValueError('non-directory module ancestor')
    return modules


def within_root(root, relative):
    target = root.joinpath(*PurePosixPath(relative).parts).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError(f'staging symlink escapes root: {relative}')
    return target


def import_modules(root, entries):
    destination = within_root(root, 'lib/modules')
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('rootfs already contains modules')
    records = {}
    for path, (mode, content) in sorted(entries.items(), key=lambda item: len(item[0].parts)):
        target = within_root(root, str(path))
        if stat.S_ISDIR(mode):
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as stream:
                stream.write(content)
            records[str(path)] = {'bytes': len(content),
                                  'sha256': hashlib.sha256(content).hexdigest()}
        target.chmod(stat.S_IMODE(mode))
    return records


def validate_kernel(config):
    settings = dict(line.split('=', 1) for line in config.splitlines()
                    if line.startswith('CONFIG_') and '=' in line)
    if settings.get('CONFIG_EXT4_FS') != 'y':
        raise ValueError('CONFIG_EXT4_FS=y required')
    if not re.search(r'^# Linux/arm64 6\.6\.98(?:[- ]).*Kernel Configuration$', config, re.M):
        raise ValueError('incompatible kernel version')


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def install_management(root):
    records = {}
    for source, (destination, mode) in MANAGEMENT_FILES.items():
        target = within_root(root, destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            raise ValueError(f'management destination already exists: {destination}')
        shutil.copyfile(REPO / source, target)
        target.chmod(mode)
        records[destination] = file_record(target)
    return {'profile': 'a733-management-v1', 'files': records}


def install_machine_id_commit_mask(root):
    """Suppress transient-ID commit before PID 1 sees the persistent state bind."""
    relative = 'etc/systemd/system/systemd-machine-id-commit.service'
    parent = root
    for part in PurePosixPath(relative).parts[:-1]:
        parent = parent / part
        if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
            raise ValueError('unsafe machine-id commit mask ancestor')
    target = root / relative
    if target.exists() or target.is_symlink():
        raise ValueError('machine-id commit mask destination already exists')
    target.parent.mkdir(parents=True, exist_ok=True)
    # host-init overlays this packaged /etc before switch_root. Its guarded
    # ext4 identity bind is already persistent, unlike systemd's transient ID.
    target.symlink_to('/dev/null')
    return {relative: {'type': 'symlink', 'target': os.readlink(target),
                       'mode': stat.S_IMODE(target.lstat().st_mode)}}


def maintenance_entries():
    # Build-time only: runtime imports of this module must not import the
    # packager, which itself records this source in its closed runtime set.
    spec = importlib.util.spec_from_file_location('payload_maintenance_tools', REPO / 'build/maintenance-tools.py')
    tools = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tools)
    return tools.entries(REPO)


def install_maintenance(root, entries=None):
    """Install the complete closed tool tree into a new immutable root."""
    entries = maintenance_entries() if entries is None else entries
    parent = root
    for part in ('usr', 'lib'):
        parent = parent / part
        if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
            raise ValueError('unsafe maintenance ancestor')
    destination = root / 'usr/lib/a733-tools'
    if destination.exists() or destination.is_symlink():
        raise ValueError('maintenance destination already exists')
    destination.mkdir(parents=True, mode=0o700)
    directories = {destination}
    files = {}
    for name, (mode, data) in sorted(entries.items()):
        target = within_root(root, 'usr/lib/a733-tools/' + name)
        target.parent.mkdir(parents=True, exist_ok=True)
        directories.update(parent for parent in target.parents
                           if parent == destination or destination in parent.parents)
        with target.open('xb') as output:
            output.write(data)
        target.chmod(mode)
        relative = str(target.relative_to(root))
        files[relative] = {**file_record(target), 'mode': mode}
    for directory in sorted(directories, key=lambda p: len(p.parts), reverse=True):
        directory.chmod(0o555)
    metadata = json.loads(entries['package.json'][1])
    return {'profile': metadata['profile'], 'entrypoints': metadata['entrypoints'],
            'files': files}


def make_image(root, output, slot, size=SLOT_BYTES):
    if output.is_symlink():
        raise ValueError('symlink image output refused')
    # Exclusive regular file creation; mke2fs never receives a device argument.
    with output.open('xb') as stream:
        stream.truncate(size)
    subprocess.run(['mke2fs', '-q', '-t', 'ext4', '-F', '-b', '4096',
                    '-O', '^has_journal', '-E', 'lazy_itable_init=0',
                    '-m', '0', '-U', SLOT_UUIDS[slot], '-L', f'a733-root-{slot}',
                    '-d', str(root), str(output)], check=True)
    # mke2fs's supported creation features exclude READONLY (e2fsprogs
    # v1.47.0 misc/mke2fs.c:1115); tune2fs sets it with valid checksums.
    subprocess.run(['tune2fs', '-O', 'read-only', str(output)], check=True)
    # Inspect disk superblock directly, independently of human-readable tool text.
    with output.open('rb') as stream:
        stream.seek(1024)
        superblock = stream.read(1024)
    compat, ro_compat = struct.unpack_from('<I', superblock, 92)[0], struct.unpack_from('<I', superblock, 100)[0]
    if compat & 4 or not ro_compat & 0x1000 or output.stat().st_size != size:
        raise ValueError('filesystem lacks immutable no-journal format or size differs')
    subprocess.run(['e2fsck', '-fn', str(output)], check=True)


def validate_output(output):
    output = Path(output)
    parent = REPO / 'out'
    if (not output.is_absolute() or output.parent != parent or
            not output.name.startswith('host-payload-') or
            output.name == 'host-payload-' or output.exists() or output.is_symlink()):
        raise ValueError('output must be a new absolute repo/out/host-payload-NAME')
    if parent.is_symlink() or parent.resolve() != parent:
        raise ValueError('symlink output parent refused')
    return output


def tool_version(tool):
    result = subprocess.run([tool, '-V'], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, check=False)
    # tune2fs prints its version followed by usage and exits nonzero for -V.
    return result.stdout.strip()


def validate_builder_packages(lock_path):
    """Require the pinned package versions for archive and filesystem tools."""
    selected = {'e2fsprogs', 'libext2fs2', 'tar', 'gzip'}
    expected = {}
    for line in Path(lock_path).read_text().splitlines():
        name, version, architecture = line.split('\t')
        package = name.split(':', 1)[0]
        if package in selected:
            if package in expected:
                raise ValueError(f'ambiguous builder package lock: {package}')
            expected[package] = (version, architecture)
    if set(expected) != selected:
        raise ValueError('builder package lock lacks required tool packages')
    queries = [f'{name}:{expected[name][1]}' for name in sorted(expected)]
    output = subprocess.check_output(
        ['dpkg-query', '-W', '-f=${binary:Package}\t${Version}\t${Architecture}\n',
         *queries], text=True)
    actual = {}
    for line in output.splitlines():
        name, version, architecture = line.split('\t')
        package = name.split(':', 1)[0]
        if package in actual:
            raise ValueError(f'ambiguous installed builder package: {package}')
        actual[package] = (version, architecture)
    if actual != expected:
        raise ValueError(f'builder tool package versions/architectures differ from lock: {actual!r}')
    return {name: {'version': version, 'architecture': architecture}
            for name, (version, architecture) in sorted(actual.items())}


def build(rootfs, diagnostic, output, metadata_profile='native', userspace_profile=DEFAULT_USERSPACE_PROFILE):
    backend = validate_metadata_profile(metadata_profile)
    userspace = userspace_binding(userspace_profile)
    clock_policy = clock_state_policy(userspace_profile)
    if os.geteuid() != 0:
        raise ValueError('root required to preserve rootfs ownership and device nodes')
    for tool in ('mke2fs', 'tune2fs', 'e2fsck', 'tar'):
        if not shutil.which(tool):
            raise ValueError(f'required tool missing: {tool}')
    builder_packages = validate_builder_packages(REPO / 'configs/builder-packages.lock.tsv')
    output = validate_output(output)
    rootfs = require_regular(rootfs)
    diagnostic = Path(diagnostic)
    if not diagnostic.is_dir() or diagnostic.is_symlink():
        raise ValueError('diagnostic input must be a real directory')
    user_evidence = userspace['evidence']
    diag_evidence = REPO / 'docs/evidence/diagnostic-20261104.json'
    expected = userspace['archive']
    diag_lock = json.loads(diag_evidence.read_text())['files']
    authenticate(rootfs, expected)
    for name in BOOT_FILES:
        authenticate(diagnostic / name, diag_lock[name])
    validate_kernel((diagnostic / 'kernel.config').read_text())
    with gzip.open(diagnostic / 'initramfs.cpio.gz', 'rb') as stream:
        modules = module_entries(stream.read())
    with tarfile.open(rootfs) as source:
        members = source.getmembers()
        validate_tar(members)
        unpacked = sum(m.size for m in members if m.isfile())
    if unpacked > SLOT_BYTES * 3 // 4:
        raise ValueError('rootfs exceeds conservative slot budget')
    paths = provenance_paths((user_evidence, *userspace['provenance'], diag_evidence, Path(__file__).resolve(),
                 REPO / 'configs/diagnostic-sources.lock.json',
                 REPO / 'configs/builder-packages.lock.tsv',
                 REPO / 'build/host-userspace.sh',
                 REPO / 'configs/host-userspace-packages.txt',
                 REPO / 'configs/host-userspace-packages.lock.tsv',
                 REPO / 'configs/host-userspace-package-hashes.lock.tsv',
                 *(REPO / source for source in MANAGEMENT_FILES),
                 *((REPO / 'build/timesync-state.py',) if clock_policy is not None else ()),
                 *((REPO / 'build/rootfs-metadata.py', backend.LOCK) if backend is not None else ())))
    # Two full images, worst-case compressed copies, staging and safety reserve.
    required = 4 * SLOT_BYTES + unpacked + 1024**3
    output.parent.mkdir(exist_ok=True)
    if shutil.disk_usage(output.parent).free < required:
        raise ValueError(f'insufficient free space: require {required} bytes')
    output.mkdir(mode=0o700)
    sources = {'rootfs.tar.gz': expected,
               **{name: diag_lock[name] for name in BOOT_FILES}}
    if userspace_profile != DEFAULT_USERSPACE_PROFILE:
        sources['userspace_profile'] = userspace['binding']
    if clock_policy is not None:
        sources['timesync_state'] = clock_policy
    provenance = output / 'provenance'
    provenance.mkdir()
    for path in paths:
        shutil.copyfile(path, provenance / path.name)
    for path in userspace['provenance']:
        authenticate(provenance / path.name, userspace['provenance_records'][str(path)])
    if clock_policy is not None:
        authenticate(provenance / 'timesync-state.py', clock_policy['helper'])
    if backend is not None:
        helper = REPO / 'build/rootfs-metadata.py'
        sources['rootfs_metadata'] = {'profile': metadata_profile,
                                     'helper': file_record(provenance / helper.name)}
    for name in BOOT_FILES:
        shutil.copyfile(diagnostic / name, output / name)
    with tempfile.TemporaryDirectory(prefix='.staging-', dir=output) as stage:
        root = Path(stage)
        metadata_extraction = None
        if backend is None:
            extract_rootfs(rootfs, root)
        else:
            metadata_extraction = extract_rootfs(rootfs, root, metadata_profile, expected)
            if metadata_extraction['helper'] != sources['rootfs_metadata']['helper']:
                raise ValueError('metadata helper changed during build')
        cache_pruning = PACKAGE.prune_caches(root)
        module_bom = import_modules(root, modules)
        systemd_masks = install_machine_id_commit_mask(root)
        management = install_management(root)
        if clock_policy is not None:
            # Legacy update tools require v1 and must reject time-enabled roots.
            management = {**management, 'profile': management_profile({'sources': sources})}
        tool_entries = maintenance_entries()
        maintenance = install_maintenance(root, tool_entries)
        if clock_policy is not None:
            installed = root / 'usr/lib/a733-tools/build/timesync-state.py'
            authenticate(installed, clock_policy['helper'])
        maintenance_provenance = provenance / 'maintenance'
        maintenance_provenance.mkdir()
        provenance_entries = {**tool_entries,
            'build/maintenance-tools.py': (0o444, (REPO / 'build/maintenance-tools.py').read_bytes())}
        for relative, (_mode, data) in provenance_entries.items():
            target = maintenance_provenance / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        package_state = PACKAGE.snapshot(root)
        write_json(output / 'modules.json', module_bom)
        metadata_dir = within_root(root, 'usr/lib/orangepi-hypervisor')
        metadata_dir.mkdir(parents=True, exist_ok=True)
        for slot in SLOT_UUIDS:
            write_json(metadata_dir / 'payload.json', {
                'schema': 1, 'classification': CLASSIFICATION, 'slot': slot,
                'filesystem_uuid': SLOT_UUIDS[slot], 'kernel_release': KERNEL_RELEASE,
                'sources': sources, 'modules_bom': file_record(output / 'modules.json'),
                'management_runtime': management, 'maintenance_runtime': maintenance,
                'systemd_masks': systemd_masks,
                'package_state': package_state,
                'cache_pruning': cache_pruning})
            image = output / f'root-{slot}.ext4'
            make_image(root, image, slot)
            with image.open('rb') as source, (output / (image.name + '.gz')).open('xb') as target:
                with gzip.GzipFile(filename='', mode='wb', fileobj=target, mtime=0) as compressed:
                    shutil.copyfileobj(source, compressed)
    files = {str(p.relative_to(output)): file_record(p)
             for p in sorted(output.rglob('*')) if p.is_file()}
    write_json(output / 'manifest.json', {
        'schema': 1, 'board': 'Orange Pi 4 Pro A733', 'classification': CLASSIFICATION,
        'kernel_release': KERNEL_RELEASE, 'slot_bytes': SLOT_BYTES,
        'slot_uuids': SLOT_UUIDS, 'sources': sources, 'files': files,
        'management_runtime': management, 'maintenance_runtime': maintenance,
        'systemd_masks': systemd_masks,
        'package_state': package_state,
        'cache_pruning': cache_pruning,
        **({'metadata_extraction': metadata_extraction} if backend is not None else {}),
        'builder_packages': builder_packages,
        'reproducibility': 'not established; filesystem/package-generated metadata may vary',
        'tools': {tool: tool_version(tool) for tool in ('mke2fs', 'tune2fs', 'e2fsck')}})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('rootfs_tar', type=Path)
    parser.add_argument('diagnostic_artifact_dir', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--metadata-profile', choices=METADATA_PROFILES, default='native')
    parser.add_argument('--userspace-profile', choices=USERSPACE_PROFILES, default=DEFAULT_USERSPACE_PROFILE)
    args = parser.parse_args()
    try:
        build(args.rootfs_tar, args.diagnostic_artifact_dir, args.output, args.metadata_profile, args.userspace_profile)
    except (ValueError, OSError, tarfile.TarError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'host payload: {error}\n')


if __name__ == '__main__':
    main()
