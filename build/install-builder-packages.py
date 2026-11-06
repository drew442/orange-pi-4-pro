#!/usr/bin/env python3
"""Install all pinned packages offline in an already proven private seed.

A successful receipt covers this mutable AMD64 root only. Immutable export,
source rebuilds and Orange Pi hardware acceptance remain separate gates.
"""
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[1]
LOCK = 'configs/builder-package-archives-20261106.lock.json'
INVENTORY = 'configs/builder-replay-packages-20261106.lock.tsv'
RECIPE = 'build/install-builder-packages.py'


def bootstrap_guard(script):
    """Trust no sibling Python before checking the root execution path."""
    for target in (script, script.with_name('clean-builder.py')):
        for component in (target, *target.parents):
            info = component.lstat()
            if (stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022 or
                    not (stat.S_ISREG(info.st_mode) if component == target else
                         stat.S_ISDIR(info.st_mode))):
                raise ValueError('Root-owned physical non-writable bootstrap required: ' + str(component))


def load_seed():
    if os.geteuid() == 0:
        bootstrap_guard(Path(__file__).absolute())
    spec = importlib.util.spec_from_file_location('install_seed', REPO / 'build/clean-builder.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


seed = load_seed()
require = seed.require
FILES = tuple(dict.fromkeys((*seed.FILES, LOCK, INVENTORY, RECIPE)))
POLICY = b'#!/bin/sh\nexit 101\n'
APT = ('apt-get', '-o', 'Dir::Etc::sourcelist=/dev/null', '-o',
       'Dir::Etc::sourceparts=-', '-o', 'APT::Get::List-Cleanup=0')


def read(path):
    with seed.regular(path) as stream:
        return stream.read()


def binding(repo):
    original = seed.FILES
    try:
        seed.FILES = FILES
        return seed.binding(repo)
    finally:
        seed.FILES = original


def inputs(repo):
    inventory = read(repo / INVENTORY)
    expected = set()
    for line in inventory.decode().splitlines():
        fields = tuple(line.split('\t'))
        require(len(fields) == 3 and all(fields) and fields not in expected,
                'Malformed/duplicate inventory tuple')
        expected.add(fields)
    lock = json.loads(read(repo / LOCK))
    require(lock['schema'] == 1 and lock['inventory_lock'] ==
            {'path': INVENTORY, 'sha256': hashlib.sha256(inventory).hexdigest()},
            'Archive lock inventory binding mismatch')
    records, tuples, basenames = {}, set(), set()
    for item in lock['packages']:
        name = item['filename']
        require(isinstance(name, str), 'Invalid archive filename')
        parts = name.split('/')
        require(len(parts) == 5 and parts[0] == 'pool' and parts[1] in
                ('main', 'universe', 'restricted', 'multiverse') and
                all(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9+._~-]*', p) and
                    p not in ('.', '..') for p in parts) and name.endswith('.deb'),
                'Unsafe archive filename')
        triple = tuple(item[k] for k in ('package', 'version', 'architecture'))
        require(all(isinstance(v, str) and v and not any(c.isspace() for c in v)
                    for v in triple), 'Invalid package tuple')
        require(name not in records and triple not in tuples and parts[-1] not in basenames,
                'Duplicate package/archive/basename')
        require(type(item['bytes']) is int and item['bytes'] > 0 and
                isinstance(item['sha256'], str) and re.fullmatch('[0-9a-f]{64}', item['sha256']),
                'Invalid archive hash/size')
        records[name] = {k: item[k] for k in ('bytes', 'sha256')}
        tuples.add(triple)
        basenames.add(parts[-1])
    require(len(records) == len(expected) == 842 and tuples == expected and
            sum(r['bytes'] for r in records.values()) == 983768778,
            'Expected exact 842-package archive coverage')
    return records, expected, inventory


def private_directory(path):
    seed.safe_path(path)
    info = path.stat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and
            stat.S_IMODE(info.st_mode) == 0o700, 'Root-owned private 0700 directory required')


def validate_seed(output, project):
    seed.safe_path(output)
    require(output.parent == seed.WORKSPACE and
            re.fullmatch(r'seed-probe-[A-Za-z0-9][A-Za-z0-9._-]*', output.name),
            'Seed output must be directly under the fixed workspace')
    seed.root_owned_path(seed.WORKSPACE)
    private_directory(seed.WORKSPACE)
    private_directory(output)
    rootfs = output / 'rootfs'
    seed.safe_path(rootfs)
    require(rootfs.is_dir(), 'Missing seed rootfs')
    seed.shifted_root_owner(rootfs)
    receipt = json.loads(read(output / 'receipt.json'))
    require(receipt['schema'] == 1 and receipt['phase'] == 'seed-and-isolation-probe' and
            receipt['success'] is True and receipt['post_checks']['success'] is True and
            receipt['post_checks']['errors'] == [] and receipt['seed'] == seed.SEED,
            'Original seed receipt did not pass')
    original = receipt['project']['files']
    require(set(original) == set(seed.FILES) and
            all(original[name] == project['files'][name] for name in seed.FILES),
            'Original seed source binding differs from current committed inputs')
    require(seed.facts(output / 'source-lock.json') == project['files'][seed.LOCK] and
            seed.facts(output / 'host-inventory-before.tsv') == project['files'][INVENTORY],
            'Seed staged lock/inventory mismatch')
    require(read(output / 'probe.sh') == seed.PROBE and
            seed.facts(output / 'probe.sh') == receipt['probe_payload'] and
            seed.facts(output / 'probe.log') == receipt['probe_log'], 'Original seed probe mismatch')
    seed.validate_probe(read(output / 'probe.log'))
    require(receipt['command'] == seed.nspawn_command(rootfs, output / 'probe.sh'),
            'Original seed isolation command mismatch')
    firmware = output / 'firmware-empty'
    seed.root_owned_path(firmware)
    require(firmware.is_dir() and stat.S_IMODE(firmware.stat().st_mode) == 0o555 and
            not list(firmware.iterdir()), 'Original firmware mask changed')
    seed.check_no_mounts(output)
    seed.check_range_unused()
    return {name: seed.facts(output / name) for name in
            ('receipt.json', 'source-lock.json', 'host-inventory-before.tsv', 'probe.sh', 'probe.log')}


def cache_facts(cache, records):
    seed.safe_path(cache)
    require(cache.is_dir(), 'Existing package cache required')
    result = {}
    for name, expected in records.items():
        result[name] = seed.facts(cache / name)
        require(result[name] == expected, 'Archive hash/size mismatch: ' + name)
    return result


def copy_cache(cache, destination, records):
    destination.mkdir(mode=0o700)
    for name, expected in records.items():
        target = destination / Path(name).name
        digest, count = hashlib.sha256(), 0
        with seed.regular(cache / name) as source, target.open('xb') as stream:
            before = os.fstat(source.fileno())
            for chunk in iter(lambda: source.read(1048576), b''):
                stream.write(chunk)
                digest.update(chunk)
                count += len(chunk)
            after = os.fstat(source.fileno())
            stream.flush()
            os.fsync(stream.fileno())
        require((before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
                (after.st_size, after.st_mtime_ns, after.st_ctime_ns) and
                {'bytes': count, 'sha256': digest.hexdigest()} == expected and
                seed.facts(target) == expected, 'Copied archive mismatch: ' + name)
        target.chmod(0o444)
    destination.chmod(0o555)


def host_state():
    """Hash relevant host configuration/state without printing its contents."""
    result = {}
    roots = (Path('/etc/apt'), Path('/var/lib/apt'), Path('/var/cache/apt'),
             Path('/etc/passwd'), Path('/etc/group'), Path('/etc/subuid'), Path('/etc/subgid'),
             Path('/var/lib/dpkg'), Path('/usr/lib/os-release'))
    for root in roots:
        if not os.path.lexists(root):
            result[str(root)] = None
            continue
        paths = [root]
        if root.is_dir() and not root.is_symlink():
            paths.extend(sorted(root.rglob('*')))
        for path in paths:
            info = path.lstat()
            value = {'mode': info.st_mode, 'uid': info.st_uid, 'gid': info.st_gid}
            if stat.S_ISLNK(info.st_mode):
                value['link'] = os.readlink(path)
            elif stat.S_ISREG(info.st_mode):
                value.update(seed.facts(path))
            else:
                require(stat.S_ISDIR(info.st_mode), 'Unexpected special host input: ' + str(path))
            result[str(path)] = value
    return result


def install_script():
    # All checks precede debconf and maintainer scripts; no Python in the seed.
    boundary = r'''
awk 'NF != 3 || $1 != 0 || $2 != 1048576 || $3 != 65536 { exit 1 }; END { if (NR != 1) exit 1 }' /proc/self/uid_map
awk 'NF != 3 || $1 != 0 || $2 != 1048576 || $3 != 65536 { exit 1 }; END { if (NR != 1) exit 1 }' /proc/self/gid_map
for key in CapEff CapPrm CapBnd; do
    value=$(awk -v key="$key:" '$1 == key { print $2 }' /proc/self/status)
    [ -n "$value" ] && [ "$((0x$value & 0x''' + format(seed.PROHIBITED, 'x') + r'''))" -eq 0 ]
done
[ "$(awk '$1 == "NoNewPrivs:" { print $2 }' /proc/self/status)" = 1 ]
[ "$(awk -F: 'NF == 2 { gsub(/ /,"",$1); print $1 }' /proc/net/dev)" = lo ]
[ "$(wc -l < /proc/net/route)" -eq 1 ]
awk 'NF != 10 || $10 != "lo" || ($1 != "00000000000000000000000000000000" && $1 != "00000000000000000000000000000001") || $5 != "00000000000000000000000000000000" || ($2 != "00" && $2 != "80") { exit 1 }' /proc/net/ipv6_route
[ -z "$(find /dev -type b -print)" ]
for p in /dev/mem /dev/kmem /dev/port /dev/kvm /dev/nvme*; do
    if [ -e "$p" ] || [ -L "$p" ]; then exit 1; fi
done
[ -z "$(find /sys/firmware -mindepth 1 -print)" ]
awk '$5 == "/sys/firmware" { count++; if ($6 !~ /(^|,)ro(,|$)/) exit 1 }; END { if (count != 1) exit 1 }' /proc/self/mountinfo
printf 'A733_BOUNDARY_PASSED\n'
printf '%s\n' 'grub-efi-amd64 grub2/update_nvram boolean false' 'grub-pc grub-pc/install_devices_empty boolean true' | debconf-set-selections
set --
while IFS= read -r archive; do
    case "$archive" in /a733-debs/*.deb) ;; *) exit 1 ;; esac
    [ -f "$archive" ] && [ ! -L "$archive" ]
    set -- "$@" "$archive"
done < /a733-packages.list
[ "$#" -eq 842 ]
'''
    apt = ' '.join(APT)
    tail = '\n' + apt + ' --no-download --no-install-recommends --no-remove --allow-downgrades --reinstall --yes install "$@"\n'
    tail += apt + ' clean\n'
    tail += "audit=$(dpkg --audit)\n[ -z \"$audit\" ] || { printf '%s\\n' \"$audit\"; exit 1; }\n"
    tail += apt + ' --no-download check\n'
    tail += "printf 'A733_BEGIN installed\\n'\ndpkg-query -W -f='${binary:Package}\\t${Version}\\t${Architecture}\\t${Status}\\n'\nprintf 'A733_END installed\\n'\n"
    return seed.PROBE + boundary.encode() + tail.encode()


def command(output, phase):
    args = seed.nspawn_command(output / 'rootfs', output / 'probe.sh')[:-2]
    args += ['--bind-ro=' + str(phase / 'debs') + ':/a733-debs',
             '--bind-ro=' + str(phase / 'packages.list') + ':/a733-packages.list',
             '--bind-ro=' + str(phase / 'install.sh') + ':/a733-install.sh',
             '--setenv=DEBIAN_FRONTEND=noninteractive', '--setenv=DEBCONF_NONINTERACTIVE_SEEN=true',
             '--setenv=LC_ALL=C', '--setenv=TZ=UTC']
    return args + ['/bin/sh', '/a733-install.sh']


def validate_installed(log, expected):
    seed.validate_probe(log)
    text = log.decode()
    require(text.count('\nA733_BOUNDARY_PASSED\n') == 1, 'Missing/duplicate pre-install boundary success')
    blocks = re.findall(r'^A733_BEGIN installed\n(.*?)^A733_END installed\n', text, re.M | re.S)
    require(len(blocks) == 1, 'Missing/duplicate installed inventory')
    actual = set()
    for line in blocks[0].splitlines():
        fields = line.split('\t')
        require(len(fields) == 4 and fields[3] == 'install ok installed',
                'Malformed or not fully configured package')
        triple = tuple(fields[:3])
        require(triple not in actual, 'Duplicate installed tuple')
        actual.add(triple)
    require(len(actual) == 842 and actual == expected, 'Installed inventory differs from exact 842 tuples')
    return blocks[0].encode()


def write_private(path, data, mode=0o600):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(mode)


def policy(rootfs):
    parent = rootfs / 'usr/sbin'
    seed.safe_path(parent)
    require(parent.is_dir(), 'Missing physical /usr/sbin')
    target = parent / 'policy-rc.d'
    require(not os.path.lexists(target), 'Existing policy-rc.d must not be overwritten')
    write_private(target, POLICY, 0o755)
    os.chown(target, seed.UID_BASE, seed.UID_BASE, follow_symlinks=False)
    return seed.facts(target)


def capacity(path, extra_bytes=0):
    sample = seed.resources(path)
    require(sample['free_bytes'] >= 3 * 1024**3 + extra_bytes,
            'Disk cancellation threshold: 3 GiB plus staging headroom')
    require(sample['available_memory_bytes'] >= 512 * 1024**2,
            'RAM cancellation threshold: 512 MiB')
    return sample


def monitored(command_args, log, phase, report):
    # A sampled watchdog supplements, rather than replaces, launch preflight.
    report['resources_before_launch'] = capacity(phase)
    process = subprocess.Popen(command_args, env=seed.ENV, stdout=log,
                               stderr=subprocess.STDOUT, start_new_session=True)
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
                result = process.wait(timeout=2)
                report['install_exit'] = result
                require(result == 0, 'Maintainer/installer failed with exit ' + str(result))
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
            report['install_exit'] = process.returncode
            log.flush()
            os.fsync(log.fileno())
        finally:
            for number, handler in previous.items():
                signal.signal(number, handler)


def capture_inventory(repo):
    actual = seed.run(['/usr/bin/dpkg-query', '-W',
                       '-f=${binary:Package}\\t${Version}\\t${Architecture}\\n'])
    require(actual == read(repo / INVENTORY), 'Host package inventory bytes changed')
    return actual


def install(output, cache, repo=REPO):
    require(os.geteuid() == 0, 'Root required on the prepared AMD64 Ubuntu builder')
    # Trust the private project before the first Git command.
    project = binding(repo)
    original = validate_seed(output, project)
    records, expected, inventory_bytes = inputs(repo)
    seed.safe_path(cache)
    require(cache != output and cache not in output.parents and output not in cache.parents,
            'Cache/seed overlap forbidden')
    phase = output / 'package-install'
    require(not os.path.lexists(phase), 'Installation phase exists; no resume; retain failed output')
    fd = os.open(output / '.package-install.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'r+') as guard, seed.interruption_signals():
        info = os.fstat(guard.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1 and
                stat.S_IMODE(info.st_mode) == 0o600, 'Unsafe installation lock')
        fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(not os.path.lexists(phase), 'Installation phase exists; no resume')
        seed.check_no_mounts(output)
        seed.check_range_unused()
        phase.mkdir(mode=0o700)
        phase.chmod(0o700)
        report = {'schema': 1, 'phase': 'offline-package-install', 'success': False,
                  'project': project, 'seed_records': original, 'resource_samples': [],
                  'complete_clean_builder': False, 'hardware_acceptance': False,
                  'immutable_export': False, 'install_exit': None}
        helper = seed.load_helper()
        host_before = cache_before = inventory_before = None
        pending = None
        try:
            host_before = host_state()
            inventory_before = helper.check_builder(repo, 'replay-20261106')
            report['host_inventory_before'] = inventory_before
            report['host_state_before'] = host_before
            actual_before = capture_inventory(repo)
            require(actual_before == inventory_bytes, 'Host inventory differs from validated lock')
            write_private(phase / 'host-inventory-before.tsv', actual_before)
            cache_before = cache_facts(cache, records)
            report['source_cache'] = {'path': str(cache), 'files': cache_before}
            args = command(output, phase)
            report['command'] = args
            smoke = args[:-2] + ['--help']
            report['option_probe_command'] = smoke
            write_private(phase / 'nspawn-help.txt', seed.run(['/usr/bin/systemd-nspawn', '--help']))
            write_private(phase / 'nspawn-option-probe.txt', seed.run(smoke))
            report['resources_before_staging'] = capacity(phase, sum(r['bytes'] for r in records.values()))
            copy_cache(cache, phase / 'debs', records)
            package_list = ''.join('/a733-debs/' + Path(name).name + '\n' for name in records).encode()
            # Linux exec argument limit is at least 128 KiB. Leave room for apt/env.
            require(len(package_list) < 96 * 1024, 'Package arguments exceed conservative exec budget')
            write_private(phase / 'packages.list', package_list, 0o444)
            write_private(phase / 'install.sh', install_script(), 0o444)
            report['policy_rc_d'] = policy(output / 'rootfs')
            report['staged'] = {name: seed.facts(phase / name) for name in
                                ('packages.list', 'install.sh', 'nspawn-help.txt',
                                 'nspawn-option-probe.txt', 'host-inventory-before.tsv')}
            report['private_archives'] = {Path(name).name: seed.facts(phase / 'debs' / Path(name).name)
                                          for name in records}
            seed.write_json(phase / 'provenance.json', report)
            seed.check_range_unused()
            with (phase / 'install.log').open('xb') as log:
                monitored(args, log, phase, report)
            installed = validate_installed(read(phase / 'install.log'), expected)
            write_private(phase / 'installed.tsv', installed)
            report['installed_inventory'] = seed.facts(phase / 'installed.tsv')
            report['success'] = True
        except BaseException as exc:
            report['success'] = False
            report['error'] = type(exc).__name__ + ': ' + str(exc)
            pending = exc
        finally:
            errors = []
            checks = [('mounts', lambda: seed.check_no_mounts(output)),
                      ('process_range', seed.check_process_ranges),
                      ('project', lambda: require(binding(repo) == project, 'Project changed')),
                      ('seed_records', lambda: require(all(seed.facts(output / name) == value
                             for name, value in original.items()), 'Original seed records changed'))]
            if inventory_before is not None:
                checks.append(('host_inventory', lambda: require(
                    helper.check_builder(repo, 'replay-20261106') == inventory_before, 'Host inventory changed')))
            if inventory_before is not None:
                def record_after():
                    actual_after = capture_inventory(repo)
                    require(actual_after == inventory_bytes, 'Host inventory changed')
                    write_private(phase / 'host-inventory-after.tsv', actual_after)
                    report['host_inventory_after'] = seed.facts(phase / 'host-inventory-after.tsv')
                checks.append(('host_inventory_bytes', record_after))
            if host_before is not None:
                checks.append(('host_state', lambda: require(host_state() == host_before, 'Host APT/profile changed')))
            if cache_before is not None:
                checks.append(('source_cache', lambda: require(cache_facts(cache, records) == cache_before,
                                                              'Source cache changed')))
            if 'private_archives' in report:
                checks.append(('private_cache', lambda: require(all(
                    seed.facts(phase / 'debs' / name) == value for name, value in
                    report['private_archives'].items()), 'Private archives changed')))
            if 'staged' in report:
                checks.append(('staged', lambda: require(all(seed.facts(phase / name) == value
                    for name, value in report['staged'].items()), 'Staged records changed')))
            for label, check in checks:
                try:
                    check()
                except BaseException as exc:
                    errors.append(label + ': ' + str(exc))
            report['post_checks'] = {'success': not errors, 'errors': errors}
            report['success'] = report['success'] and pending is None and not errors
            if (phase / 'install.log').exists():
                report['install_log'] = seed.facts(phase / 'install.log')
            seed.write_json(phase / 'receipt.json', report)
        if pending is not None:
            raise pending
        require(report['success'], 'Postchecks failed; retain phase and inspect receipt')
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed-output', type=Path, required=True)
    parser.add_argument('--package-cache', type=Path, required=True)
    args = parser.parse_args()
    try:
        install(args.seed_output, args.package_cache)
    except (Exception, KeyboardInterrupt) as exc:
        print('Offline builder installation refused or failed: ' + str(exc), file=sys.stderr)
        return 1
    print('Offline 842-package root installed; immutable export/build/hardware gates remain open')
    return 0


if __name__ == '__main__':
    sys.exit(main())
