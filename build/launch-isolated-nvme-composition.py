#!/usr/bin/env python3
"""Replay unchanged full NVMe composition in the frozen Ubuntu namespace.

Default/--plan only prints metadata. --execute retains a fresh private phase,
including failures. Held components are reused; no compiler rebuild or hardware
acceptance is established. Invoke the trusted launcher using Python -I -B.
"""
import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import time
import types

SOURCE = '81f907481b12e9639ad23752966e05166074f96f'
BASE = Path('/var/lib/a733-clean-replay-20261106')
ROOT = Path('/var/lib/a733-frozen-builders/freeze-20261106-01/restored-rootfs')
SEED = Path('/var/lib/a733-clean-builder/seed-probe-20261106-05')
HELPER = Path('/var/lib/a733-builder-freeze-project-20261106/build/clean-builder.py')
HELPER_SHA = '172d7ddd62d685f07e3ad55828eafc1bde4e86fa3d181819d9919fe6f51a1d84'
ADMISSION, MARGIN, CHUNK = 23760999827, 1024**3, 1024**2
UID = 1048576
HOST_LOCK = 'configs/builder-replay-packages-20261106.lock.tsv'
PROFILE = 'deterministic-20261109'
TARGET = {
    'nvme.img': {'bytes': 256060514304, 'sha256': '1fa9144d7a1269066c91a94bfb105a9ac4e4083d4707dd6791e8ac2f26902cd9'},
    'nvme.img.gz': {'bytes': 545581074, 'sha256': '3bc02f46d272dcac591d435fff2b1bc98d4fb048569fb2120746145929a8ad64'}}
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/nonexistent',
       'LC_ALL': 'C', 'TZ': 'UTC', 'GIT_CONFIG_NOSYSTEM': '1',
       'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_OPTIONAL_LOCKS': '0'}
INPUTS = {'base-gzip': {'path': '/var/lib/a733-maintenance-validation-20261108-01/project/out/protected-state-nvme-image-20261109-01/nvme.img.gz', 'bytes': 545636009, 'sha256': '584a958c63fb01f62c2162c733aeb004082088952c6e428d6406087492e315e2'}, 'base-manifest': {'path': '/var/lib/a733-maintenance-validation-20261108-01/project/out/protected-state-nvme-image-20261109-01/manifest.json', 'bytes': 50191, 'sha256': 'f19b5b58c12030422b36c203471026209731fa413ea1ce8d9196121861cf8eb5'}, 'roots-manifest': {'path': '/var/lib/a733-clean-replay-20261106/host-roots-timesync-deterministic-02/work/project/out/host-payload-timesync-deterministic-02/manifest.json', 'bytes': 33481, 'sha256': 'c1bcc7d23d22bd487534ba17ea447055881a044350829608360aa92ca24283f2'}, 'root-a': {'path': '/var/lib/a733-clean-replay-20261106/host-roots-timesync-deterministic-02/work/project/out/host-payload-timesync-deterministic-02/root-A.ext4', 'bytes': 4294967296, 'sha256': 'a9715b819348a8220ec8b3b1f41ef27db677baaa96014f5d1dfb7a22597decdd'}, 'root-b': {'path': '/var/lib/a733-clean-replay-20261106/host-roots-timesync-deterministic-02/work/project/out/host-payload-timesync-deterministic-02/root-B.ext4', 'bytes': 4294967296, 'sha256': '42fb2becf66c25a4cbb7844a6ab84ba12ab3f53c0d5d283acc8d42e63b124ab2'}, 'state-manifest': {'path': '/var/lib/a733-clean-replay-20261106/host-state-timesync-public-dirs-01/work/project/out/host-state-timesync-public-dirs-01/manifest.json', 'bytes': 15468, 'sha256': '0c6a76a38743924cf5919ac0ba9f5cf2a4eae818b0b312a01c2d4c590283ae03'}, 'state': {'path': '/var/lib/a733-clean-replay-20261106/host-state-timesync-public-dirs-01/work/project/out/host-state-timesync-public-dirs-01/state.ext4', 'bytes': 8589934592, 'sha256': '2e9d3dcf4dcabfaead65e125e7778c0aed092eeb665e67f3559719d08e3564ec'}}
STAGED = {'base-gzip': 'base/nvme.img.gz', 'base-manifest': 'base/manifest.json', 'roots-manifest': 'roots/manifest.json', 'root-a': 'roots/root-A.ext4', 'root-b': 'roots/root-B.ext4', 'state-manifest': 'state/manifest.json', 'state': 'state/state.ext4'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def pin(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def physical(path):
    path = Path(path)
    require(path.is_absolute() and str(path) == os.path.abspath(path), 'canonical absolute path required')
    require(not any(p == path or p in path.parents for p in map(Path, ('/dev', '/proc', '/sys'))),
            'pseudo/device path forbidden')
    for parent in path.parents:
        require(stat.S_ISDIR(parent.lstat().st_mode), 'physical parent required')
    return path


def identity(item):
    return (item.st_dev, item.st_ino, item.st_mode, item.st_nlink, item.st_uid,
            item.st_gid, item.st_size, item.st_mtime_ns, item.st_ctime_ns)


class Held:
    """Keep the original inode open, reject aliases, and stream authentication."""
    def __init__(self, path, expected):
        self.path, self.expected = physical(path), expected
        self.fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            item = os.fstat(self.fd)
            require(stat.S_ISREG(item.st_mode) and item.st_nlink == 1 and item.st_size == expected['bytes'],
                    'single regular input with exact size required: ' + str(path))
            self.initial = identity(item)
            self.stable()
        except BaseException:
            os.close(self.fd)
            raise

    def __enter__(self):
        return self

    def __exit__(self, *_):
        os.close(self.fd)

    def stable(self):
        require(self.initial == identity(os.fstat(self.fd)) == identity(self.path.lstat()),
                'held input identity changed: ' + str(self.path))

    def authenticate(self, destination=None):
        self.stable()
        digest, count = hashlib.sha256(), 0
        os.lseek(self.fd, 0, os.SEEK_SET)
        output = destination.open('xb') if destination is not None else None
        try:
            for data in iter(lambda: os.read(self.fd, CHUNK), b''):
                digest.update(data)
                count += len(data)
                if output is not None:
                    if data == bytes(len(data)):
                        output.seek(len(data), os.SEEK_CUR)
                    else:
                        require(output.write(data) == len(data), 'short sparse write')
            if output is not None:
                output.truncate(count)
                output.flush()
                os.fsync(output.fileno())
            self.stable()
            result = {'bytes': count, 'sha256': digest.hexdigest()}
            require(result == self.expected, 'held input hash mismatch: ' + str(self.path))
            return result
        finally:
            if output is not None:
                output.close()


def module(path, authenticated_bytes):
    """Execute exactly the authenticated source, without reopening or pyc lookup."""
    value = types.ModuleType(path.stem.replace('-', '_'))
    value.__file__ = str(path)
    exec(compile(authenticated_bytes, str(path), 'exec'), value.__dict__)
    return value


def trusted_bytes(path):
    """Read root-owned source from one held regular inode before any import."""
    path = physical(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and
                before.st_uid == before.st_gid == 0 and not before.st_mode & 0o022 and
                before.st_size <= 16 * CHUNK, 'untrusted bootstrap source')
        data = stream.read(16 * CHUNK + 1)
        require(len(data) == before.st_size and identity(before) == identity(os.fstat(fd)) ==
                identity(path.lstat()), 'bootstrap source changed while read')
        return data


def bytecode_free(project, builder):
    # freeze-builder's existing nested imports use SourceFileLoader. They may
    # read bytecode even under -B, so refuse ignored caches before any host API.
    for name in ('build', 'configs'):
        directory = project / name
        builder.root_owned_path(directory)
        require(not any(path.name == '__pycache__' or path.suffix == '.pyc'
                        for path in directory.rglob('*')), 'fresh bytecode-free project required')


def project_apis(project, builder):
    roots_path = project / 'build/launch-host-roots.py'
    builder.root_owned_path(roots_path)
    bytecode_free(project, builder)
    roots_data = trusted_bytes(roots_path)
    require(roots_data == git(project, 'show', SOURCE + ':build/launch-host-roots.py'),
            'source reader differs from pinned commit')
    roots = module(roots_path, roots_data)
    manifest = sources(project, roots.read_source)
    for name in manifest['files']:
        builder.root_owned_path(project / name)
    authenticated = {}
    for name in ('build/launch-userspace-configuration.py', 'build/freeze-builder.py',
                 'build/install-builder-packages.py', 'build/clean-builder.py',
                 'configs/builder-image-20261106.lock.json'):
        data = trusted_bytes(project / name)
        require(data == git(project, 'show', SOURCE + ':' + name),
                'authenticated builder API/lock changed; integration review required: ' + name)
        authenticated[name] = data
    shared_path, freeze_path = 'build/launch-userspace-configuration.py', 'build/freeze-builder.py'
    shared = module(project / shared_path, authenticated[shared_path])
    freeze = module(project / freeze_path, authenticated[freeze_path])
    return roots, manifest, shared, freeze


def git(project, *args):
    return subprocess.check_output(['/usr/bin/git', '-c', 'core.hooksPath=/dev/null',
        '-c', 'core.fsmonitor=false', '-c', 'safe.directory=' + str(project),
        '-C', str(project), *args], env=ENV)


def sources(project, reader):
    revision = git(project, 'rev-parse', 'HEAD').decode().strip()
    require(re.fullmatch('[0-9a-f]{40}', revision), 'invalid project commit')
    require(not git(project, 'status', '--porcelain', '--untracked-files=all').strip(),
            'clean committed checkout required')
    records, total = {}, 0
    for row in git(project, 'ls-tree', '-rz', '--full-tree', revision).split(b'\0'):
        if not row:
            continue
        fields, raw = row.split(b'\t', 1)
        mode, kind, blob = fields.decode().split()
        name = raw.decode()
        require(mode in ('100644', '100755') and kind == 'blob' and
                not name.startswith('/') and '..' not in Path(name).parts, 'unsafe Git source')
        data = reader(project / name)
        require(bool((project / name).lstat().st_mode & 0o111) == (mode == '100755'),
                'source executable mode differs from Git')
        total += len(data)
        require(total <= 128 * 1024**2, 'source closure exceeds bound')
        require(data == git(project, 'cat-file', 'blob', blob), 'source differs from committed blob')
        records[name] = dict(pin(data), git_blob=blob, mode=mode)
    require(records, 'empty source closure')
    return {'revision': revision, 'files': records}


def phase_path(tag):
    require(isinstance(tag, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,54}', tag), 'unsafe tag')
    return BASE / ('isolated-nvme-' + tag)


def output_name(tag):
    phase_path(tag)
    return 'public-config-nvme-image-isolated-' + tag


def command(builder, phase, tag):
    args = builder.nspawn_command(ROOT, SEED / 'probe.sh')[:-2]
    require(args.count('--private-users-ownership=chown') == 1, 'unexpected builder command')
    args[args.index('--private-users-ownership=chown')] = '--private-users-ownership=off'
    # Bind the project first, then overlay its ignored out directory, then its
    # private inputs read-only. No writable source alias enters the namespace.
    args += ['--read-only', '--bind-ro=' + str(phase / 'project') + ':/a733-sources',
             '--bind=' + str(phase / 'output') + ':/a733-sources/out',
             '--bind-ro=' + str(phase / 'inputs') + ':/a733-sources/out/.inputs']
    payload = ['/usr/bin/python3', '-I', '-B', '/a733-sources/build/public-config-nvme-image.py',
               '--root-profile', PROFILE]
    for name, relative in STAGED.items():
        payload += ['--' + name, '/a733-sources/out/.inputs/' + relative]
    payload += ['--output', '/a733-sources/out/' + output_name(tag)]
    # The arguments contain only fixed paths and a strictly validated tag.
    return args + ['/bin/sh', '-c', '/bin/sh /a733-probe.sh && exec ' + ' '.join(payload)]


def resource_guard(sample, initial=False):
    floor = ADMISSION + sum(v['bytes'] for v in INPUTS.values()) + MARGIN if initial else ADMISSION
    require(sample['free_bytes'] > floor and sample['available_memory_bytes'] >= 1024**3,
            'composition admission disk/memory floor violated')


def plan(project, tag):
    phase = phase_path(tag)
    physical(project)
    return {'schema': 1, 'phase': str(phase), 'project': str(project), 'source_revision': SOURCE,
            'builder_root': str(ROOT), 'root_profile': PROFILE, 'inputs': INPUTS,
            'staged_inputs': STAGED, 'target': TARGET, 'output': 'output/' + output_name(tag),
            'initial_required_free_bytes_exclusive': ADMISSION + sum(v['bytes'] for v in INPUTS.values()) + MARGIN,
            'core_admission_bytes': ADMISSION, 'runtime_disk_floor': 3000000000,
            'runtime_memory_floor': 1000000000, 'hardware_acceptance': False,
            'physical_storage_written': False, 'compilers_rebuilt': False,
            'classification': 'Frozen Ubuntu environment replay of unchanged composer and same held components; UNBOOTED'}


def stage(phase, project, held, reader):
    subprocess.run(['/usr/bin/git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false',
                    'clone', '--no-local', '--no-hardlinks', '--no-checkout', '--', str(project),
                    str(phase / 'project')], env=ENV, check=True, timeout=120)
    git(phase / 'project', 'checkout', '--detach', SOURCE)
    producer_manifest = sources(phase / 'project', reader)
    require(producer_manifest['revision'] == SOURCE, 'exact producer commit required')
    for name in ('inputs', 'output', 'output/.inputs', 'project/out'):
        (phase / name).mkdir(mode=0o700, parents=True, exist_ok=True)
    for name, item in held.items():
        target = phase / 'inputs' / STAGED[name]
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        item.authenticate(target)
        with Held(target, item.expected) as copied:
            copied.authenticate()
    for root in (phase / 'inputs', phase / 'output', phase / 'project'):
        for directory, dirs, files in os.walk(root):
            for path in [Path(directory)] + [Path(directory) / name for name in files + dirs]:
                info = path.lstat()
                require(stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode), 'stage alias/special node')
                require(not stat.S_ISREG(info.st_mode) or info.st_nlink == 1, 'stage hardlink alias')
                os.chown(path, UID, UID)
                if root.name == 'project':
                    os.chmod(path, 0o755 if path.is_dir() else stat.S_IMODE(info.st_mode) & ~0o022)
                else:
                    os.chmod(path, 0o700 if path.is_dir() else 0o400)
    return producer_manifest


def verify_target(path, shared, manifest):
    result = json.loads(shared.regular_bytes(path / 'manifest.json', owner=UID, limit=1024**2))
    require(result['files'] == TARGET and result['root_profile'] == PROFILE and
            result['hardware_acceptance'] is False and result['physical_storage_written'] is False,
            'target raw/encoded pins or provenance mismatch')
    require(result['sources'] and all(value == {key: manifest['files'][name][key] for key in ('bytes', 'sha256')}
            for name, value in result['sources'].items()), 'composer recipe provenance mismatch')
    require(all(result['derivation'][name] is True for name in
                ('base_crc_eof_raw_hash_verified', 'both_gpt_tables_verified',
                 'output_gzip_full_expansion_verified', 'generated_single_member_wire_verified',
                 'all_inputs_held_reauthenticated')), 'composer verification incomplete')
    with Held(path / 'nvme.img.gz', TARGET['nvme.img.gz']) as wire:
        wire.authenticate()
    require(not (path / 'nvme.img').exists(), 'full raw staging forbidden')
    return {'files': TARGET, 'manifest': pin(shared.regular_bytes(path / 'manifest.json', owner=UID)),
            'tool_versions': result['tool_versions'], 'root_profile': result['root_profile'],
            'same_held_components': True, 'full_raw_staged': False}


def preflight(project, phase):
    require(os.getuid() == os.geteuid() == os.getgid() == os.getegid() == 0, 'initial host root required')
    require(sys.flags.isolated and sys.dont_write_bytecode, 'invoke launcher with Python -I -B')
    physical(HELPER)
    for path in (HELPER, *HELPER.parents):
        info = path.lstat()
        require(info.st_uid == info.st_gid == 0 and not info.st_mode & 0o022, 'untrusted pinned helper path')
    helper_data = trusted_bytes(HELPER)
    require(pin(helper_data)['sha256'] == HELPER_SHA, 'builder helper hash mismatch')
    builder = module(HELPER, helper_data)
    builder.repository_guard(project)
    builder.binding(project)
    roots, manifest, shared, freeze = project_apis(project, builder)
    release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    require(release.get('ID', '').strip('"') == 'ubuntu' and
            release.get('VERSION_ID', '').strip('"') == '22.04' and os.uname().machine == 'x86_64',
            'Ubuntu 22.04 AMD64 required')
    require(Path('/proc/self/uid_map').read_text().split() == ['0', '0', '4294967295'], 'initial namespace required')
    shared.safe_directory(BASE, trusted=True)
    shared.frozen_root_directory(ROOT)
    shared.safe_directory(SEED, trusted=True)
    require(not os.path.lexists(phase), 'phase exists; failed tags cannot be reused')
    require(not shared.mapped_processes() and not shared.phase_mounts(phase), 'existing mapped processes/mounts')
    resource_guard(shared.resource_sample(), initial=True)
    require(shared.regular_bytes(SEED / 'probe.sh', owner=0) == builder.PROBE, 'probe differs')
    shared.safe_directory(SEED / 'firmware-empty', trusted=True)
    require(not list((SEED / 'firmware-empty').iterdir()), 'firmware mask not empty')
    runtime_anchors()
    return builder, roots, shared, freeze, manifest


def runtime_anchors():
    records = {}
    for name, directory in (('a733-sources', True), ('a733-probe.sh', False)):
        path = ROOT / name
        info = path.lstat()
        require(info.st_uid == info.st_gid == UID and
                (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)) and
                stat.S_IMODE(info.st_mode) == (0o755 if directory else 0o644) and
                (not list(path.iterdir()) if directory else info.st_size == 0 and info.st_nlink == 1) and
                not os.listxattr(path, follow_symlinks=False), 'unsafe/nonempty runtime anchor: ' + name)
        records[name] = identity(info)
    return records


def reauthenticate(held, phase, report):
    errors = []
    for name, item in held.items():
        try:
            item.authenticate()
            target = phase / 'inputs' / STAGED[name]
            if os.path.lexists(target):
                with Held(target, item.expected) as copied:
                    copied.authenticate()
            else:
                raise ValueError('staged input absent: ' + name)
        except BaseException as error:
            errors.append(str(error))
    report['held_and_staged_inputs_reauthenticated'] = not errors
    require(not errors, 'input postchecks failed: ' + '; '.join(errors))


def execute(project, tag):
    phase = phase_path(tag)
    builder, roots, shared, freeze, manifest = preflight(project, phase)
    before = subprocess.check_output(shared.INVENTORY, env=ENV)
    shared.validate_host_inventory(before, roots.read_source(project / HOST_LOCK))
    frozen_before = freeze.snapshot(ROOT)[1]
    anchors_before = runtime_anchors()
    frozen_lock = json.loads(roots.read_source(project / 'configs/builder-image-20261106.lock.json'))
    historical = shared.regular_bytes(ROOT.parent / 'source-namespace-manifest.json', owner=0, limit=32 * 1024**2)
    require(pin(historical) == frozen_lock['source_namespace_manifest'], 'authenticated frozen manifest differs')
    freeze.validate_namespace_source(frozen_before, historical)
    host_before = freeze.producer.host_state()
    report = dict(plan(project, tag), success=False, resources=[], sources=manifest,
                  frozen_lock=frozen_lock, frozen_manifest_authenticated=True,
                  frozen_identity_limit='Historical comparison checks translated ACL/capability xattr names, not translated values; full pre/post xattr bytes must match')
    proc, handlers = None, {}
    phase.mkdir(mode=0o700)
    try:
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            handlers[sig] = signal.signal(sig, shared.interrupted)
        with ExitStack() as stack:
            held = {name: stack.enter_context(Held(Path(item['path']), {k: item[k] for k in ('bytes', 'sha256')}))
                    for name, item in INPUTS.items()}
            stack.callback(reauthenticate, held, phase, report)
            stack.callback(lambda: shared.stop_process(proc))
            require(len({item.initial[:2] for item in held.values()}) == len(held), 'aliased held inputs')
            producer_manifest = stage(phase, project, held, roots.read_source)
            report['producer_sources'] = producer_manifest
            require(sources(project, roots.read_source) == manifest, 'original sources changed during staging')
            sample = shared.resource_sample()
            report['resources'].append(sample)
            resource_guard(sample)
            args = command(builder, phase, tag)
            report['command'] = args
            shared.atomic_json(phase / 'staged.json', report)
            with (phase / 'runtime.log').open('xb') as stream:
                proc = subprocess.Popen(args, stdout=stream, stderr=subprocess.STDOUT, env=ENV, start_new_session=True)
                started = time.monotonic()
                while proc.poll() is None:
                    sample = shared.resource_sample()
                    report['resources'].append(sample)
                    require(sample['free_bytes'] >= 3000000000 and sample['available_memory_bytes'] >= 1000000000
                            and time.monotonic() - started <= 10800, 'runtime resource floor/timeout')
                    time.sleep(5)
                report['returncode'] = proc.wait(timeout=15)
                stream.flush()
                os.fsync(stream.fileno())
            require(proc.returncode == 0, 'composer failed; phase retained')
            report['probe'] = builder.validate_probe((phase / 'runtime.log').read_bytes())
            report['verification'] = verify_target(phase / 'output' / output_name(tag), shared, producer_manifest)
            report['success'] = True
    except BaseException as error:
        report.update(success=False, error=repr(error))
        raise
    finally:
        for sig in handlers:
            signal.signal(sig, signal.SIG_IGN)
        try:
            shared.stop_process(proc)
            report['mapped_processes_final'] = shared.mapped_processes()
            report['phase_mounts_final'] = shared.phase_mounts(phase)
            after = subprocess.check_output(shared.INVENTORY, env=ENV)
            report['host_inventory_unchanged'] = after == before
            report['host_configuration_unchanged'] = freeze.producer.host_state() == host_before
            frozen_after = freeze.snapshot(ROOT)[1]
            report['frozen_before'], report['frozen_after'] = pin(frozen_before), pin(frozen_after)
            report['frozen_builder_unchanged'] = frozen_after == frozen_before
            report['runtime_anchors_unchanged'] = runtime_anchors() == anchors_before
            report['host_configuration_before'] = pin(json.dumps(host_before, sort_keys=True).encode())
            report['sources_unchanged'] = sources(project, roots.read_source) == manifest
            report['staged_sources_unchanged'] = sources(phase / 'project', roots.read_source) == report.get('producer_sources')
            sample = shared.resource_sample()
            report['resources'].append(sample)
            require(sample['free_bytes'] >= 3000000000 and sample['available_memory_bytes'] >= 1000000000,
                    'final disk/memory floor violated')
            report['success'] = report['success'] and all(report[key] for key in
                ('host_inventory_unchanged', 'host_configuration_unchanged', 'frozen_builder_unchanged',
                 'runtime_anchors_unchanged', 'sources_unchanged', 'staged_sources_unchanged')) and not (
                    report['mapped_processes_final'] or report['phase_mounts_final'])
        except BaseException as error:
            report.update(success=False, final_checks_error=repr(error))
        try:
            shared.atomic_json(phase / 'receipt.json', report)
        finally:
            for sig, previous in handlers.items():
                signal.signal(sig, previous)
    require(report['success'], 'final checks failed; inspect retained phase')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--tag', required=True)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--execute', action='store_true')
    modes.add_argument('--plan', action='store_true')
    options = parser.parse_args(argv)
    result = execute(options.project, options.tag) if options.execute else plan(options.project, options.tag)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
