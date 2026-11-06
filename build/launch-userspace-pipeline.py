#!/usr/bin/env python3
"""Launch a fresh three-phase interim userspace build on the pinned builder.

No historical rootfs seeds this build. No physical boot/KVM/NVMe/SD-write
acceptance is established. --plan validates Git sources without staging or root.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import time
import types

BASE = Path('/var/lib/a733-clean-replay-20261106')
CACHE = Path('/home/drew/orange-pi-4-pro-build/recipe/out/host-userspace-bookworm-20261001-01')
INDEX = Path('/var/lib/a733-userspace-foreign-recipe-inputs-20261106/Packages.xz')
INDEX_PIN = (8689464, '2ddb1737692e8c45c53e8d57c0ce4cd21c78c5703b830c3226b1423566a06c00')
PROBE_SHA = 'ce565b23ac0b927e54fbfc057bee69ae95d12ba930a5c5421699538e9da14fea'
SELF = 'build/launch-userspace-pipeline.py'
SHARED = 'build/launch-userspace-configuration.py'
PROFILE_LOCK = 'configs/host-userspace-profiles.lock.json'
HOST_LOCK = 'configs/builder-replay-packages-20261106.lock.tsv'
LOCK = 'configs/host-userspace-packages.lock.tsv'
HASH_LOCK = 'configs/host-userspace-package-hashes.lock.tsv'
PHASE_FILES = {
    'foreign': ('build/prepare-host-userspace.py', 'build/offline-userspace-repo.py',
                'build/debootstrap-adapter.py', LOCK, HASH_LOCK, HOST_LOCK),
    'configure': ('build/configure-host-userspace.py', 'build/userspace-bootstrap-api.py', LOCK, HASH_LOCK),
    'export': ('build/export-host-userspace.py', LOCK),
}
START_FLOORS = {'foreign': 4_000_000_000, 'configure': 4_000_000_000, 'export': 3_500_000_000}
TIMEOUTS = {'foreign': 900, 'configure': 2700, 'export': 900}
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/nonexistent', 'LC_ALL': 'C', 'TZ': 'UTC'}


def require(value, message):
    if not value:
        raise ValueError(message)


def tag_path(tag):
    require(isinstance(tag, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', tag),
            'tag must be a safe 1-64 character name')
    return BASE / ('userspace-pipeline-' + tag)


def source_bytes(path):
    """Reject aliases and special files before reading any project code."""
    require(path.is_absolute() and '..' not in path.parts, 'absolute project path required')
    for directory in path.parents:
        require(stat.S_ISDIR(directory.lstat().st_mode), 'symlink/non-directory project ancestor')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1, 'single regular project file required')
        data = stream.read()
        identity = lambda item: (item.st_dev, item.st_ino, item.st_mode, item.st_uid, item.st_gid,
                                 item.st_nlink, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
        require(identity(before) == identity(os.fstat(stream.fileno())) == identity(path.lstat()),
                'project source changed while read')
        return data


def committed_sources(project):
    names = sorted({SELF, SHARED, HOST_LOCK, PROFILE_LOCK}.union(*map(set, PHASE_FILES.values())))
    revision = subprocess.check_output(['/usr/bin/git', '-C', str(project), 'rev-parse', '--verify', 'HEAD'], env=ENV).decode().strip()
    require(re.fullmatch('[0-9a-f]{40}', revision), 'invalid Git revision')
    records, contents = {}, {}
    for name in names:
        data = source_bytes(project / name)
        fields = subprocess.check_output(['/usr/bin/git', '-C', str(project), 'ls-tree', revision, '--', name], env=ENV).decode().split()
        require(len(fields) == 4 and fields[0] in ('100644', '100755') and fields[1] == 'blob' and fields[3] == name,
                'uncommitted/unsafe source: ' + name)
        blob = subprocess.check_output(['/usr/bin/git', '-C', str(project), 'cat-file', 'blob', fields[2]], env=ENV)
        require(blob == data, 'project bytes differ from committed blob: ' + name)
        records[name] = {'git_blob': fields[2], 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
        contents[name] = data
    return {'revision': revision, 'files': records}, contents


def shared_module(contents):
    """Execute authenticated source bytes, bypassing importlib bytecode caches."""
    module = types.ModuleType('pipeline_shared')
    module.__file__ = SHARED
    exec(compile(contents[SHARED], SHARED, 'exec'), module.__dict__)
    return module


def plan(tag, manifest):
    phase = tag_path(tag)
    return {'schema': 1, 'classification': 'interim userspace only; no hardware acceptance',
            'phase': str(phase), 'work': str(phase / 'work'), 'inputs': manifest,
            'cache': str(CACHE), 'index': str(INDEX), 'index_pin': INDEX_PIN,
            'phases': [{'name': name, 'source_files': list(files),
                        'start_free_bytes_exclusive': START_FLOORS[name],
                        'runtime_free_bytes_minimum': 3_000_000_000,
                        'available_memory_bytes_minimum': 1_000_000_000,
                        'timeout_seconds': TIMEOUTS[name]}
                       for name, files in PHASE_FILES.items()]}


def mounts(shared, phase):
    return shared.phase_mounts(phase) + [line for line in Path('/proc/self/mountinfo').read_text().splitlines()
                                       if str(CACHE) in line or str(INDEX) in line]


def preflight(shared, phase, contents):
    # The existing check_host requires a historical FOREIGN directory. Validate
    # the same host properties here without making any historical tree an input.
    require(os.getuid() == os.geteuid() == os.getgid() == os.getegid() == 0, 'initial-root host required')
    release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    require(release.get('ID', '').strip('"') == 'ubuntu' and release.get('VERSION_ID', '').strip('"') == '22.04'
            and os.uname().machine == 'x86_64', 'Ubuntu 22.04 AMD64 host required')
    require(Path('/proc/self/uid_map').read_text().split() == ['0', '0', '4294967295'], 'initial host user namespace required')
    for path in (BASE, shared.SEED, shared.HELPER.parent, INDEX.parent):
        shared.safe_directory(path, trusted=True)
    shared.frozen_root_directory(shared.ROOT)
    shared.safe_directory(CACHE)  # User-owned ancestors; signed recipe checks authenticate every consumed byte.
    require(not os.path.lexists(phase), 'pipeline already exists; use a fresh tag')
    require(not shared.mapped_processes() and not mounts(shared, phase), 'existing builder process/mount')
    require(shared.resource_sample()['free_bytes'] > START_FLOORS['foreign'], 'need more than 4 GB free before staging')
    metadata = shared.HELPER.lstat()
    require(metadata.st_uid == metadata.st_gid == 0 and not metadata.st_mode & 0o022, 'untrusted helper metadata')
    require(shared.sha(shared.HELPER) == shared.HELPER_SHA, 'pinned clean-builder helper mismatch')
    probe = shared.regular_bytes(shared.SEED / 'probe.sh', owner=0)
    require(hashlib.sha256(probe).hexdigest() == PROBE_SHA, 'pinned probe mismatch')
    shared.safe_directory(shared.SEED / 'firmware-empty', trusted=True)
    require(not any((shared.SEED / 'firmware-empty').iterdir()), 'firmware mask is not empty')
    index = shared.regular_bytes(INDEX, owner=0, mode=0o444)
    require((len(index), hashlib.sha256(index).hexdigest()) == INDEX_PIN, 'pinned package index mismatch')
    qemu = {str(path): shared.sha(path) for path in (Path('/usr/bin/qemu-aarch64-static'),
            shared.ROOT / 'usr/bin/qemu-aarch64-static', Path('/usr/libexec/qemu-binfmt/aarch64-binfmt-P').resolve(strict=True))}
    require(all(value == shared.QEMU_SHA for value in qemu.values()), 'pinned QEMU mismatch')
    binfmt = Path('/proc/sys/fs/binfmt_misc/qemu-aarch64').read_text()
    require('enabled\n' in binfmt and 'flags: PF\n' in binfmt and
            'interpreter /usr/libexec/qemu-binfmt/aarch64-binfmt-P\n' in binfmt, 'unexpected QEMU binfmt registration')
    before = subprocess.check_output(shared.INVENTORY, env=ENV)
    shared.validate_host_inventory(before, contents[HOST_LOCK])
    builder = types.ModuleType('pinned_builder')
    builder.__file__ = str(shared.HELPER)
    exec(compile(shared.regular_bytes(shared.HELPER, owner=0), str(shared.HELPER), 'exec'), builder.__dict__)
    require(probe == builder.PROBE, 'probe differs from pinned helper')
    return builder, before, index, {'qemu': qemu, 'binfmt': binfmt}


def immutable_file(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o444)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fchown(stream.fileno(), 0, 0)
        os.fchmod(stream.fileno(), 0o444)
        os.fsync(stream.fileno())


def stage_inputs(shared, phase, name, manifest, contents, index):
    slot = phase / name
    slot.mkdir(mode=0o700)
    (slot / 'scratch').mkdir(mode=0o700)
    os.chown(slot / 'scratch', shared.UID_BASE, shared.UID_BASE)
    inputs = slot / 'inputs'
    inputs.mkdir(mode=0o755)
    selected = PHASE_FILES[name]
    shared.project_snapshot(inputs / 'project', {key: contents[key] for key in selected})
    shared.atomic_json(inputs / 'manifest.json', {'revision': manifest['revision'],
                       'files': {key: manifest['files'][key] for key in selected}})
    profile = None
    if name == 'foreign':
        immutable_file(inputs / 'Packages.xz', index)
        program = 'prepare-host-userspace.py'
    elif name == 'configure':
        work = phase / 'work'
        receipt = json.loads(shared.regular_bytes(work / 'foreign-receipt.json', owner=shared.UID_BASE))
        require(receipt['success'] is True and receipt['dpkg_status_bytes'] == 0, 'foreign extraction not successful')
        repo_sha = shared.sha(work / 'repo/manifest.json')
        adapter_sha = shared.sha(work / 'adapter/manifest.json')
        require(repo_sha == receipt['repository']['manifest']['sha256'] and
                adapter_sha == receipt['adapter_manifest']['sha256'], 'foreign receipt binding mismatch')
        profile = {'nonce': os.urandom(32).hex(), 'host_userns_inode': shared.namespaces()['user'],
                   'host_driver_pid': os.getpid(), 'repo_manifest_sha256': repo_sha}
        shared.atomic_json(inputs / 'api-profile.json', profile)
        shared.atomic_json(inputs / 'configure-inputs.json', {
            'foreign_receipt_sha256': shared.sha(work / 'foreign-receipt.json'), 'adapter_manifest_sha256': adapter_sha})
        program = 'configure-host-userspace.py'
    else:
        configured = shared.read_json(phase / 'work/configure-receipt.json', owner=shared.UID_BASE)
        require(configured['success'] is True, 'configuration receipt not successful')
        program = 'export-host-userspace.py'
    runner = ('import importlib.util,json,pathlib\np="/a733-sources/project/build/' + program + '"\n'
              's=importlib.util.spec_from_file_location("recipe",p)\nm=importlib.util.module_from_spec(s)\ns.loader.exec_module(m)\n')
    if name == 'export':
        runner += 'print(json.dumps(m.export(pathlib.Path("/a733-export-output/export-result"),' + repr(shared.sha(phase / 'work/configure-receipt.json')) + '),sort_keys=True))\n'
    else:
        runner += 'm.main()\n'
    immutable_file(inputs / 'run.py', runner.encode())
    os.chmod(inputs, 0o555)
    return slot, profile


def command(shared, builder, phase, slot, name):
    args = builder.nspawn_command(shared.ROOT, shared.SEED / 'probe.sh')[:-2]
    require(args.count('--private-users-ownership=chown') == 1, 'unexpected pinned builder command')
    args[args.index('--private-users-ownership=chown')] = '--private-users-ownership=off'
    args += ['--read-only', '--bind=' + str(phase / 'work') + ':/a733-export-output',
             '--bind=' + str(slot / 'scratch') + ':/var/tmp', '--bind-ro=' + str(slot / 'inputs') + ':/a733-sources']
    if name == 'foreign':
        args += ['--bind-ro=' + str(CACHE) + ':/a733-debs']
    return args + ['/bin/sh', '-c', '/bin/sh /a733-probe.sh && exec /usr/bin/python3 -I -B /a733-sources/run.py']


def check_resources(sample, name, elapsed):
    require(sample['free_bytes'] >= 3_000_000_000 and sample['available_memory_bytes'] >= 1_000_000_000
            and elapsed <= TIMEOUTS[name], 'resource floor or timeout exceeded')


def run_phase(shared, builder, phase, slot, name, profile, step):
    require(shared.resource_sample()['free_bytes'] > START_FLOORS[name], 'phase start disk floor exceeded: ' + name)
    require(not shared.mapped_processes() and not mounts(shared, phase), 'unclean phase boundary')
    args = command(shared, builder, phase, slot, name)
    step.update(command=args, resources=[], success=False)
    proc = None
    try:
        host_ns = shared.namespaces()
        setup_done = False
        with (slot / 'runtime.log').open('xb') as stream:
            proc = subprocess.Popen(args, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True, env=ENV)
            start = time.monotonic()
            while True:
                sample = shared.resource_sample()
                step['resources'].append(sample)
                check_resources(sample, name, time.monotonic() - start)
                ready = phase / 'work/api-ready.json'
                if name == 'configure' and not setup_done and os.path.lexists(ready):
                    # Existing reviewed handshake expects phase/work. The slot
                    # supplies helper logs; the adapter supplies shared work only.
                    shared.bootstrap_api(WorkSlot(slot, phase / 'work'), shared.read_json(ready, owner=shared.UID_BASE),
                                         profile, host_ns, step)
                    setup_done = True
                if proc.poll() is not None:
                    break
                time.sleep(1)
            step['returncode'] = proc.wait(timeout=15)
            stream.flush()
            os.fsync(stream.fileno())
        require(proc.returncode == 0 and (name != 'configure' or setup_done), 'payload failed: ' + name)
        step['probe'] = builder.validate_probe((slot / 'runtime.log').read_bytes())
        receipt_path = phase / 'work' / ({'foreign': 'foreign-receipt.json', 'configure': 'configure-receipt.json',
                                         'export': 'export-result/receipt.json'}[name])
        receipt = json.loads(shared.regular_bytes(receipt_path, owner=shared.UID_BASE))
        require(receipt['success'] is True, 'unsuccessful recipe receipt')
        step['recipe_receipt_sha256'] = shared.sha(receipt_path)
        step['success'] = True
    finally:
        shared.stop_process(proc)
        step['mapped_processes_final'] = shared.mapped_processes()
        step['output_mounts_final'] = mounts(shared, phase)
        step['success'] = step['success'] and not step['mapped_processes_final'] and not step['output_mounts_final']
    require(step['success'], 'phase cleanup failed')


class WorkSlot:
    """Path adapter for the unchanged reviewed API handshake."""
    def __init__(self, slot, work):
        self.slot, self.work = slot, work

    def __truediv__(self, name):
        relative = Path(name)
        return self.work.joinpath(*relative.parts[1:]) if relative.parts[0] == 'work' else self.slot / relative



def verify_export(shared, phase, contents):
    expected = json.loads(contents[PROFILE_LOCK])['profiles']['normalized-20261106']['archive']
    require(set(expected) == {'bytes', 'sha256'} and type(expected['bytes']) is int and expected['bytes'] > 0
            and isinstance(expected['sha256'], str) and re.fullmatch('[0-9a-f]{64}', expected['sha256']),
            'invalid normalized profile archive pin')
    output = phase / 'work/export-result'
    shared.safe_directory(output)
    receipt = json.loads(shared.regular_bytes(output / 'receipt.json', owner=shared.UID_BASE))
    require(receipt['success'] is True and receipt['archive'] == expected, 'export receipt differs from normalized profile')
    archive = output / 'rootfs.tar.gz'
    fd = os.open(archive, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_uid == before.st_gid == shared.UID_BASE,
                'unsafe export archive')
        digest, size = hashlib.sha256(), 0
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
            size += len(block)
        identity = lambda item: (item.st_dev, item.st_ino, item.st_mode, item.st_uid, item.st_gid,
                                 item.st_nlink, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
        require(identity(before) == identity(os.fstat(stream.fileno())) == identity(archive.lstat()),
                'export archive changed during read')
    actual = {'bytes': size, 'sha256': digest.hexdigest()}
    require(actual == expected, 'export archive differs from normalized profile')
    return actual


def execute(shared, builder, phase, manifest, contents, index, before, report):
    phase.mkdir(mode=0o700)
    report.update(success=False, phases=[])
    handlers = {sig: signal.signal(sig, shared.interrupted) for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}
    try:
        (phase / 'work').mkdir(mode=0o700)
        os.chown(phase / 'work', shared.UID_BASE, shared.UID_BASE)
        immutable_file(phase / 'host-packages.before.tsv', before)
        shared.atomic_json(phase / 'source-manifest.json', manifest)
        for name in PHASE_FILES:
            slot, profile = stage_inputs(shared, phase, name, manifest, contents, index)
            step = {'name': name}
            report['phases'].append(step)
            if profile is not None:
                step['api_profile'] = profile
            run_phase(shared, builder, phase, slot, name, profile, step)
            after = subprocess.check_output(shared.INVENTORY, env=ENV)
            shared.validate_host_inventory(after, contents[HOST_LOCK])
            require(after == before, 'host package inventory changed')
        report['final_archive'] = verify_export(shared, phase, contents)
        report['success'] = True
    except BaseException as error:
        report['error'] = repr(error)
        raise
    finally:
        for sig in handlers:
            signal.signal(sig, signal.SIG_IGN)
        try:
            report['mapped_processes_final'] = shared.mapped_processes()
            report['output_mounts_final'] = mounts(shared, phase)
            after = subprocess.check_output(shared.INVENTORY, env=ENV)
            report['host_package_inventory_unchanged'] = after == before
            report['success'] = (report['success'] and after == before and not report['mapped_processes_final']
                                 and not report['output_mounts_final'])
            immutable_file(phase / 'host-packages.after.tsv', after)
            shared.atomic_json(phase / 'receipt.json', report)
        finally:
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
    require(report['success'], 'pipeline cleanup checks failed; inspect retained receipt')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--project', type=Path, default=Path(__file__).absolute().parent.parent)
    parser.add_argument('--plan', action='store_true', help='validate committed sources and print read-only plan')
    options = parser.parse_args(argv)
    phase = tag_path(options.tag)
    manifest, contents = committed_sources(options.project)
    require(source_bytes(Path(__file__).absolute()) == contents[SELF], 'executing launcher differs from committed source')
    report = plan(options.tag, manifest)
    if options.plan:
        print(json.dumps(report, sort_keys=True))
        return
    shared = shared_module(contents)
    builder, before, index, facts = preflight(shared, phase, contents)
    report.update(facts)
    execute(shared, builder, phase, manifest, contents, index, before, report)
    print(json.dumps({'success': True, 'phase': str(phase), 'export': str(phase / 'work/export-result')}))


if __name__ == '__main__':
    main()
