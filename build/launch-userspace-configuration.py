#!/usr/bin/env python3
"""Root-host launcher for the fixed, isolated Ubuntu userspace builder.

This configures a retained foreign-stage clone. It does not install a host,
access physical storage, or establish A733/KVM hardware acceptance.
"""

import argparse
import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import stat
import subprocess
import tempfile
import time

BASE = Path('/var/lib/a733-clean-replay-20261106')
FOREIGN = BASE / 'userspace-foreign-01/work'
ROOT = Path('/var/lib/a733-frozen-builders/freeze-20261106-01/restored-rootfs')
SEED = Path('/var/lib/a733-clean-builder/seed-probe-20261106-05')
HELPER = Path('/var/lib/a733-builder-freeze-project-20261106/build/clean-builder.py')
HELPER_SHA = '172d7ddd62d685f07e3ad55828eafc1bde4e86fa3d181819d9919fe6f51a1d84'
QEMU_SHA = '92c4aeb8dd898de7152c184370ab22f09ae7c5b7279fd300081c76cee3f78a80'
UID_BASE = 1048576
UID_COUNT = 65536
NAMES = ('mnt', 'pid', 'net', 'user', 'uts', 'ipc', 'cgroup')
PROJECT_FILES = ('build/launch-userspace-configuration.py',
                 'configs/builder-replay-packages-20261106.lock.tsv',
                 'build/configure-host-userspace.py', 'build/userspace-bootstrap-api.py',
                 'configs/host-userspace-packages.lock.tsv',
                 'configs/host-userspace-package-hashes.lock.tsv')
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/nonexistent',
       'LC_ALL': 'C', 'TZ': 'UTC'}
INVENTORY = ['/usr/bin/dpkg-query', '-W',
             '-f=${binary:Package}\t${Version}\t${Architecture}\t${db:Status-Status}\n']
RUNNER = b'''import importlib.util
spec = importlib.util.spec_from_file_location("configure", "/a733-sources/project/build/configure-host-userspace.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.main()
'''


def require(condition, message):
    if not condition:
        raise ValueError(message)


def tag_path(tag, base=BASE):
    require(isinstance(tag, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', tag),
            'tag must be a safe 1-64 character name')
    return base / ('userspace-configure-' + tag)


def safe_directory(path, trusted=False):
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts, 'absolute canonical path required')
    for ancestor in (*reversed(path.parents), path):
        item = ancestor.lstat()
        require(stat.S_ISDIR(item.st_mode), 'symlink/non-directory ancestor: ' + str(ancestor))
        if trusted:
            require(item.st_uid == item.st_gid == 0 and not item.st_mode & 0o022,
                    'untrusted infrastructure directory: ' + str(ancestor))
    return path


def frozen_root_directory(path):
    """Protect initial-root ancestors and the fixed mapped-root container tree."""
    path = Path(path)
    safe_directory(path.parent, trusted=True)
    item = path.lstat()
    require(stat.S_ISDIR(item.st_mode), 'symlink/non-directory frozen root')
    require(item.st_uid == item.st_gid == UID_BASE and
            stat.S_IMODE(item.st_mode) in (0o700, 0o755),
            'unexpected frozen root owner/mode')
    return path


def regular_bytes(path, owner=None, mode=None, exclusive=True, limit=None):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and (not exclusive or before.st_nlink == 1),
                'unsafe regular file: ' + str(path))
        require(owner is None or before.st_uid == before.st_gid == owner, 'wrong file owner')
        require(mode is None or stat.S_IMODE(before.st_mode) == mode, 'wrong file mode')
        require(limit is None or before.st_size <= limit, 'oversized protocol file')
        data = stream.read() if limit is None else stream.read(limit + 1)
        after = os.fstat(stream.fileno())
        require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns),
                'file changed while read')
        require(limit is None or len(data) <= limit, 'oversized protocol file')
        return data


def sha(path):
    return hashlib.sha256(regular_bytes(path, exclusive=False)).hexdigest()


def read_json(path, owner=None):
    return json.loads(regular_bytes(path, owner=owner, mode=0o444, limit=1048576))


def atomic_json(path, value):
    """Publish a root-owned immutable protocol file without replacing anything."""
    fd, temporary = tempfile.mkstemp(prefix='.api-publish-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fchown(stream.fileno(), 0, 0)
            os.fchmod(stream.fileno(), 0o444)
            os.fsync(stream.fileno())
        libc = ctypes.CDLL(None, use_errno=True)
        rename = libc.renameat2
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                           ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        if rename(-100, os.fsencode(temporary), -100, os.fsencode(path), 1) != 0:
            number = ctypes.get_errno()
            raise OSError(number, os.strerror(number), str(path))
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)


def validate_source_tree(path):
    """Allow native rootfs symlinks and internal hardlinks, never external aliases."""
    safe_directory(path)
    inodes = {}
    for directory, dirs, files in os.walk(path, followlinks=False):
        for name in dirs + files:
            current = Path(directory) / name
            item = current.lstat()
            require(stat.S_ISREG(item.st_mode) or stat.S_ISDIR(item.st_mode) or
                    stat.S_ISLNK(item.st_mode), 'special file in foreign source')
            if stat.S_ISLNK(item.st_mode):
                require(item.st_nlink == 1, 'external symlink hardlink alias')
            if stat.S_ISREG(item.st_mode):
                key = item.st_dev, item.st_ino
                count, links = inodes.get(key, (0, item.st_nlink))
                inodes[key] = count + 1, links
    require(all(count == links for count, links in inodes.values()),
            'foreign source has external hardlink aliases')


def committed_sources(project):
    """Read only explicit committed blobs, preserving their exact bytes."""
    safe_directory(project)
    revision = subprocess.check_output(['/usr/bin/git', '-C', str(project),
                                        'rev-parse', '--verify', 'HEAD'], env=ENV).decode().strip()
    require(re.fullmatch('[0-9a-f]{40,64}', revision), 'invalid project revision')
    records, contents = {}, {}
    for name in PROJECT_FILES:
        source = project / name
        safe_directory(source.parent)
        data = regular_bytes(source)
        record = subprocess.check_output(['/usr/bin/git', '-C', str(project), 'ls-tree',
                                          revision, '--', name], env=ENV).decode().strip()
        fields = record.split()
        require(len(fields) == 4 and fields[0] in ('100644', '100755') and
                fields[1] == 'blob' and fields[3] == name, 'uncommitted/unsafe project source: ' + name)
        committed = subprocess.check_output(['/usr/bin/git', '-C', str(project),
                                            'cat-file', 'blob', fields[2]], env=ENV)
        require(data == committed, 'project bytes differ from committed blob: ' + name)
        contents[name] = data
        records[name] = {'git_blob': fields[2], 'sha256': hashlib.sha256(data).hexdigest(),
                         'bytes': len(data)}
    return {'revision': revision, 'files': records}, contents


def validate_host_inventory(inventory, lock):
    def parse(data, installed):
        records = {}
        for line in data.decode().splitlines():
            fields = line.split('\t')
            require(len(fields) == (4 if installed else 3), 'malformed host inventory')
            name, version, architecture = fields[:3]
            require(name and version and architecture and name not in records,
                    'duplicate/empty host inventory tuple')
            require(not installed or fields[3] == 'installed', 'host package not installed')
            records[name] = version, architecture
        require(records, 'empty host inventory')
        return records
    require(parse(inventory, True) == parse(lock, False), 'host package inventory differs from lock')


def project_snapshot(destination, contents):
    for name, data in contents.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        with target.open('xb') as stream:
            stream.write(data)
        os.chmod(target, 0o444)
        os.chown(target, 0, 0)
    for directory, _, _ in os.walk(destination):
        os.chown(directory, 0, 0)
        os.chmod(directory, 0o555)


def mapped_processes(proc_root=Path('/proc')):
    found = []
    for status in proc_root.glob('[0-9]*/status'):
        try:
            fields = status_fields(status)
            if any(UID_BASE <= int(uid) < UID_BASE + UID_COUNT for uid in fields['Uid'].split()):
                found.append(int(status.parent.name))
        except (FileNotFoundError, ProcessLookupError):
            pass
    return sorted(found)


def status_fields(path):
    return {key: value.strip() for line in path.read_text().splitlines()
            if ':' in line for key, value in [line.split(':', 1)]}


def namespaces(proc=Path('/proc/self')):
    return {name: (proc / 'ns' / name).stat().st_ino for name in NAMES}


def phase_mounts(phase, text=None):
    text = Path('/proc/self/mountinfo').read_text() if text is None else text
    paths = (str(phase), str(FOREIGN), str(ROOT), str(SEED))
    # Existing frozen-root/foreign mounts are disallowed before launch, too.
    return [line for line in text.splitlines() if any(path in line for path in paths)]


def validate_ready(ready, profile, host_ns):
    require(set(ready) == {'nonce', 'pid', 'namespace_inodes', 'uid', 'status', 'bootstrap_root'},
            'unexpected ready fields')
    require(ready['nonce'] == profile['nonce'] and ready['uid'] == 0 and
            type(ready['pid']) is int and ready['pid'] > 1 and
            ready['bootstrap_root'] == '/a733-export-output/arm-root', 'invalid ready identity')
    identities = ready['namespace_inodes']
    require(isinstance(identities, dict) and set(identities) == set(NAMES) and
            all(type(v) is int and v > 0 for v in identities.values()), 'invalid ready namespaces')
    require(all(identities[name] != host_ns[name] for name in NAMES),
            'payload namespaces are not private')
    require(profile['host_userns_inode'] == host_ns['user'], 'wrong host namespace profile')
    return ready


def select_payload(ready, profile, host_ns, proc_root=Path('/proc')):
    validate_ready(ready, profile, host_ns)
    candidates = []
    for status in proc_root.glob('[0-9]*/status'):
        try:
            fields = status_fields(status)
            if fields.get('Uid', '').split() != [str(UID_BASE)] * 4:
                continue
            if fields.get('Gid', '').split() != [str(UID_BASE)] * 4:
                continue
            if int(fields.get('NSpid', '0').split()[-1]) != ready['pid']:
                continue
            if fields.get('NoNewPrivs') != '1':
                continue
            if any(int(fields.get(key, 'ffffffff'), 16) & 0xa631000
                   for key in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb')):
                continue
            if (status.parent / 'uid_map').read_text().split() != ['0', str(UID_BASE), str(UID_COUNT)]:
                continue
            if (status.parent / 'gid_map').read_text().split() != ['0', str(UID_BASE), str(UID_COUNT)]:
                continue
            if namespaces(status.parent) != ready['namespace_inodes']:
                continue
            cmdline = (status.parent / 'cmdline').read_bytes().split(b'\0')
            if cmdline[:-1] != [b'/usr/bin/python3', b'-I', b'-B', b'/a733-sources/run.py']:
                continue
            candidates.append(int(status.parent.name))
        except (FileNotFoundError, ProcessLookupError):
            pass
    require(len(candidates) == 1, 'ambiguous or absent waiting API payload')
    return candidates[0]


def helper_command(pid, request_sha):
    require(type(pid) is int and pid > 1 and re.fullmatch('[0-9a-f]{64}', request_sha),
            'invalid helper command inputs')
    return ['/usr/bin/nsenter', '--target', str(pid), '--mount', '--pid', '--net',
            '--uts', '--ipc', '--cgroup', '--root', '--wd', '--', '/usr/bin/python3',
            '-I', '-B', '/a733-sources/project/build/userspace-bootstrap-api.py',
            '/a733-export-output/api-setup-request.json', request_sha]


def payload_command(builder, phase):
    args = builder.nspawn_command(ROOT, SEED / 'probe.sh')[:-2]
    require(args.count('--private-users-ownership=chown') == 1, 'unexpected pinned builder command')
    args[args.index('--private-users-ownership=chown')] = '--private-users-ownership=off'
    return args + ['--read-only', '--bind=' + str(phase / 'work') + ':/a733-export-output',
                   '--bind=' + str(phase / 'scratch') + ':/var/tmp',
                   '--bind-ro=' + str(phase / 'inputs') + ':/a733-sources',
                   '/bin/sh', '-c', '/bin/sh /a733-probe.sh && exec /usr/bin/python3 -I -B /a733-sources/run.py']


def stop_process(proc):
    if proc is None:
        return
    # Terminate the session's process group even when its leader has exited.
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait(timeout=15)


def resource_sample():
    available = next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines()
                     if line.startswith('MemAvailable:'))
    return {'time': time.time(), 'free_bytes': shutil.disk_usage(BASE).free,
            'available_memory_bytes': int(available) * 1024}


def bootstrap_api(phase, ready, profile, host_ns, report):
    payload = select_payload(ready, profile, host_ns)
    request = {name: ready[name] for name in ('nonce', 'namespace_inodes', 'bootstrap_root')}
    request.update({name: profile[name] for name in
                    ('host_userns_inode', 'host_driver_pid', 'repo_manifest_sha256')})
    request_path = phase / 'work/api-setup-request.json'
    atomic_json(request_path, request)
    command = helper_command(payload, sha(request_path))
    report.update(api_ready=ready, host_payload_pid=payload, helper_command=command)
    helper = None
    try:
        with (phase / 'helper.stdout').open('xb') as out, (phase / 'helper.stderr').open('xb') as err:
            helper = subprocess.Popen(command, stdout=out, stderr=err,
                                      start_new_session=True, env=ENV)
            report['helper_returncode'] = helper.wait(timeout=30)
        require(helper.returncode == 0, 'API setup helper failed')
        receipt_path = phase / 'work/api-mount-receipt.json'
        receipt = read_json(receipt_path, owner=0)
        require(receipt['success'] is True and receipt['request'] == request,
                'invalid API mount receipt')
        require(select_payload(ready, profile, host_ns) == payload, 'payload changed during API setup')
        continuation = {'nonce': profile['nonce'], 'helper_status': 0,
                        'receipt_sha256': sha(receipt_path)}
        atomic_json(phase / 'work/api-continue.json', continuation)
    finally:
        stop_process(helper)


def check_host(phase):
    require(os.getuid() == os.geteuid() == 0, 'root host required')
    release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines()
                   if '=' in line)
    require(release.get('ID', '').strip('"') == 'ubuntu' and
            release.get('VERSION_ID', '').strip('"') == '22.04' and
            os.uname().machine == 'x86_64', 'Ubuntu 22.04 AMD64 host required')
    require(Path('/proc/self/uid_map').read_text().split() == ['0', '0', '4294967295'],
            'initial host user namespace required')
    for path in (BASE, FOREIGN, SEED, HELPER.parent):
        safe_directory(path, trusted=path != FOREIGN)
    frozen_root_directory(ROOT)
    require(not os.path.lexists(phase), 'phase already exists; use a fresh tag')
    require(not mapped_processes(), 'existing mapped-UID process')
    require(not phase_mounts(phase), 'existing builder/source mount')
    require(shutil.disk_usage(BASE).free > 4_000_000_000, 'need more than 4 GB free before cloning')
    helper_metadata = HELPER.lstat()
    require(helper_metadata.st_uid == helper_metadata.st_gid == 0 and
            not helper_metadata.st_mode & 0o022, 'untrusted pinned helper metadata')
    require(sha(HELPER) == HELPER_SHA, 'pinned clean-builder helper mismatch')
    require(SEED.joinpath('probe.sh').is_file(), 'missing fixed seed probe')
    require(SEED.joinpath('firmware-empty').is_dir(), 'missing firmware mask')
    paths = [Path('/usr/bin/qemu-aarch64-static'), ROOT / 'usr/bin/qemu-aarch64-static',
             Path('/usr/libexec/qemu-binfmt/aarch64-binfmt-P').resolve(strict=True)]
    qemu = {str(path): sha(path) for path in paths}
    require(all(value == QEMU_SHA for value in qemu.values()), 'pinned QEMU mismatch')
    binfmt = Path('/proc/sys/fs/binfmt_misc/qemu-aarch64').read_text()
    require('enabled\n' in binfmt and 'flags: PF\n' in binfmt and
            'interpreter /usr/libexec/qemu-binfmt/aarch64-binfmt-P\n' in binfmt,
            'unexpected QEMU binfmt registration')
    return qemu, binfmt


def stage(phase, contents):
    validate_source_tree(FOREIGN)
    for name in ('arm-root', 'repo', 'adapter'):
        safe_directory(FOREIGN / name)
    for name in ('proc', 'sys', 'dev', 'dev/pts', 'dev/shm', 'usr', 'usr/bin', 'var/lib/dpkg'):
        safe_directory(FOREIGN / 'arm-root' / name)
    receipt_data = regular_bytes(FOREIGN / 'foreign-receipt.json')
    receipt = json.loads(receipt_data)
    require(receipt['success'] is True and receipt['dpkg_status_bytes'] == 0,
            'foreign extraction not successful')
    repo_sha = sha(FOREIGN / 'repo/manifest.json')
    adapter_sha = sha(FOREIGN / 'adapter/manifest.json')
    require(repo_sha == receipt['repository']['manifest']['sha256'] and
            adapter_sha == receipt['adapter_manifest']['sha256'], 'foreign receipt binding mismatch')
    phase.mkdir(mode=0o700)
    for name in ('work', 'scratch'):
        (phase / name).mkdir(mode=0o700)
        os.chown(phase / name, UID_BASE, UID_BASE)
    inputs = phase / 'inputs'
    inputs.mkdir(mode=0o755)
    project_snapshot(inputs / 'project', contents)
    for name in ('arm-root', 'repo', 'adapter', 'foreign-receipt.json'):
        subprocess.run(['/bin/cp', '-a', '--reflink=auto', '--', str(FOREIGN / name),
                        str(phase / 'work' / name)], check=True, env=ENV)
    require(sha(phase / 'work/foreign-receipt.json') == hashlib.sha256(receipt_data).hexdigest() and
            sha(phase / 'work/repo/manifest.json') == repo_sha and
            sha(phase / 'work/adapter/manifest.json') == adapter_sha, 'clone binding mismatch')
    # Check copied hardlinks remain internal before any namespace may mutate them.
    validate_source_tree(phase / 'work')
    profile = {'nonce': secrets.token_hex(32), 'host_userns_inode': namespaces()['user'],
               'host_driver_pid': os.getpid(), 'repo_manifest_sha256': repo_sha}
    contract = {'foreign_receipt_sha256': hashlib.sha256(receipt_data).hexdigest(),
                'adapter_manifest_sha256': adapter_sha}
    atomic_json(inputs / 'api-profile.json', profile)
    atomic_json(inputs / 'configure-inputs.json', contract)
    with (inputs / 'run.py').open('xb') as stream:
        stream.write(RUNNER)
    os.chmod(inputs / 'run.py', 0o444)
    os.chmod(inputs, 0o555)
    return profile, contract


def interrupted(signum, _frame):
    raise RuntimeError('launcher interrupted by signal ' + str(signum))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--project', type=Path, default=Path(__file__).resolve().parent.parent)
    options = parser.parse_args(argv)
    phase = tag_path(options.tag)
    qemu, binfmt = check_host(phase)
    manifest, contents = committed_sources(options.project)
    inventory_before = subprocess.check_output(INVENTORY, env=ENV)
    validate_host_inventory(inventory_before, contents['configs/builder-replay-packages-20261106.lock.tsv'])
    spec = importlib.util.spec_from_file_location('pinned_builder', HELPER)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    require(regular_bytes(SEED / 'probe.sh', owner=0) == builder.PROBE, 'seed probe differs from pinned helper')
    safe_directory(SEED / 'firmware-empty', trusted=True)
    require(not any((SEED / 'firmware-empty').iterdir()), 'firmware mask is not empty')
    report = {'schema': 1, 'classification': 'isolated ARM userspace configuration; no hardware acceptance',
              'success': False, 'qemu': qemu, 'binfmt': binfmt, 'resources': []}
    proc = None
    handlers = {sig: signal.signal(sig, interrupted) for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}
    try:
        profile, contract = stage(phase, contents)
        atomic_json(phase / 'inputs/manifest.json', manifest)
        report.update(api_profile=profile, inputs=manifest, configure_inputs=contract)
        (phase / 'host-packages.before.tsv').write_bytes(inventory_before)
        args = payload_command(builder, phase)
        report['command'] = args
        host_ns = namespaces()
        with (phase / 'configure.log').open('xb') as stream:
            proc = subprocess.Popen(args, stdout=stream, stderr=subprocess.STDOUT,
                                    start_new_session=True, env=ENV)
            setup_done = False
            start = time.monotonic()
            while proc.poll() is None:
                sample = resource_sample()
                report['resources'].append(sample)
                require(sample['free_bytes'] >= 3_000_000_000 and
                        sample['available_memory_bytes'] >= 1_000_000_000 and
                        time.monotonic() - start <= 2700, 'resource floor or timeout exceeded')
                ready_path = phase / 'work/api-ready.json'
                if not setup_done and os.path.lexists(ready_path):
                    bootstrap_api(phase, read_json(ready_path, owner=UID_BASE), profile, host_ns, report)
                    setup_done = True
                time.sleep(1)
            report['returncode'] = proc.wait(timeout=15)
            stream.flush()
            os.fsync(stream.fileno())
        require(setup_done and proc.returncode == 0, 'configuration payload failed')
        report['probe'] = builder.validate_probe((phase / 'configure.log').read_bytes())
        configured = read_json(phase / 'work/configure-receipt.json', owner=UID_BASE)
        require(configured['success'] is True, 'configuration receipt not successful')
        report['configure_receipt_sha256'] = sha(phase / 'work/configure-receipt.json')
        report['success'] = True
    except BaseException as error:
        report['error'] = repr(error)
        raise
    finally:
        # Ignore repeated termination while reaping both process groups and writing evidence.
        for sig in handlers:
            signal.signal(sig, signal.SIG_IGN)
        try:
            stop_process(proc)
            report['mapped_processes_final'] = mapped_processes()
            report['output_mounts_final'] = phase_mounts(phase)
            inventory_after = subprocess.check_output(INVENTORY, env=ENV)
            report['host_package_inventory_unchanged'] = inventory_after == inventory_before
            report['success'] = (report['success'] and inventory_after == inventory_before and
                                 not report['mapped_processes_final'] and not report['output_mounts_final'])
            if phase.exists():
                (phase / 'host-packages.after.tsv').write_bytes(inventory_after)
                atomic_json(phase / 'receipt.json', report)
        finally:
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
    require(report['success'], 'configuration or cleanup checks failed; inspect retained receipt')
    print(json.dumps({'success': True, 'phase': str(phase)}))


if __name__ == '__main__':
    main()
