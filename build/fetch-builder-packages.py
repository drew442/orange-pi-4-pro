#!/usr/bin/env python3
"""Acquire exact builder .deb archives with pinned aria2; never install or extract.

Requires the recorded Ubuntu builder, a clean committed checkout and independently
verified index evidence. A complete receipt establishes acquisition only.
"""
import argparse
from contextlib import contextmanager
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]
LOCK = 'configs/builder-package-archives-20261106.lock.json'
INVENTORY = 'configs/builder-replay-packages-20261106.lock.tsv'
EVIDENCE = 'docs/evidence/builder-index-signatures-20261106.json'
RECIPE = 'build/fetch-builder-packages.py'
FILES = (LOCK, INVENTORY, EVIDENCE, RECIPE, 'build/fetch-userspace-sources.py',
         'build/uboot-replay.py', 'configs/builder-packages.lock.tsv')
ORIGINS = ['https://archive.ubuntu.com/ubuntu/', 'https://security.ubuntu.com/ubuntu/']
SNAPSHOT = 'https://snapshot.ubuntu.com/ubuntu/20261106T000000Z/'
XFS_ORIGIN = 'https://snapshot.ubuntu.com/ubuntu/20261001T000000Z/'
ENV = {'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'LC_ALL': 'C', 'TZ': 'UTC',
       'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_OPTIONAL_LOCKS': '0'}


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


safe = load('builder_fetch_safe', 'fetch-userspace-sources.py')
helper = load('builder_inventory', 'uboot-replay.py')
require, digest = safe.require, safe.digest


def run(command):
    return subprocess.check_output(command, env=ENV, stderr=subprocess.PIPE)


# check_builder uses only read-only release/inventory checks; no compiler/cache checks.
helper.run = run


def binding(repo):
    def git(*args):
        return run(['/usr/bin/git', '-c', 'core.hooksPath=/dev/null', '-C', str(repo), *args])
    require(Path(git('rev-parse', '--show-toplevel').decode().strip()) == repo,
            'Git repository root mismatch')
    require(not git('status', '--porcelain', '--untracked-files=normal').strip(),
            'checkout must be clean and committed')
    head = git('rev-parse', 'HEAD').decode().strip()
    files = {}
    for path in FILES:
        data = (repo / path).read_bytes()
        require(data == git('show', head + ':' + path), 'input differs from HEAD: ' + path)
        files[path] = {'bytes': len(data), 'sha256': digest(data)}
    return {'head': head, 'files': files}


def inputs(repo):
    lock = safe.read_json(repo / LOCK)
    inventory = (repo / INVENTORY).read_bytes()
    require(lock['schema'] == 1 and lock['live_origins'] == ORIGINS, 'untrusted lock')
    require(lock['inventory_lock'] == {'path': INVENTORY, 'sha256': digest(inventory)},
            'inventory binding mismatch')
    expected = {tuple(line.split('\t')) for line in inventory.decode().splitlines()}
    records, tuples = {}, set()
    for package in lock['packages']:
        path = package['filename']
        require(isinstance(path, str), 'invalid filename')
        parts = path.split('/')
        require(len(parts) == 5 and parts[0] == 'pool' and parts[1] in
                ('main', 'universe', 'restricted', 'multiverse') and
                all(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9+._~-]*', p) and
                    p not in ('.', '..') for p in parts) and path.endswith('.deb'),
                'unsafe archive path')
        require(path not in records, 'duplicate archive')
        record = {k: package[k] for k in ('bytes', 'sha256')}
        safe.facts(record)
        triple = tuple(package[k] for k in ('package', 'version', 'architecture'))
        require(triple not in tuples, 'duplicate package tuple')
        tuples.add(triple)
        if package['package'] == 'xfsprogs':
            require(package.get('snapshot_origin') == XFS_ORIGIN and
                    record == {'bytes': 870292, 'sha256':
                    'efe2119fec8c0cc7010afbf036b3b6aa60fa2d546e3adb5abe881ff3dac63634'} and
                    triple == ('xfsprogs', '5.13.0-1ubuntu2.1', 'amd64'), 'xfs pin mismatch')
            origins = [XFS_ORIGIN]
        else:
            require(package['metadata_kind'] ==
                    'cached-apt-index; signature audit and download still required',
                    'unknown metadata provenance')
            origins = ORIGINS + [SNAPSHOT]
        records[path] = {**record, 'package': triple[0], 'version': triple[1],
                         'architecture': triple[2], 'origins': origins}
    require(len(expected) == len(records) == 842 and tuples == expected and
            sum(r['bytes'] for r in records.values()) == 983768778, 'archive coverage mismatch')
    evidence = safe.read_json(repo / EVIDENCE)
    require(evidence['schema'] == 1 and evidence['archive_pin_lock_sha256'] ==
            digest((repo / LOCK).read_bytes()) and evidence['verified_live_index_packages'] == 841
            and evidence['snapshot_package'] == 'xfsprogs', 'index authentication evidence mismatch')
    return records


def regular(path):
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.geteuid(),
            'unsafe regular file: ' + str(path))
    return info


def prepare(output, records, resume):
    """Only a bound aria2 control file authorizes an incomplete destination."""
    missing = []
    permitted = {'.fetch.lock', 'fetch-state.json', 'fetch-report.json', 'receipt.json',
                 'aria2-input.txt', 'aria2.log'}
    permitted.update(records)
    permitted.update(path + '.aria2' for path in records)
    directories = {str(Path(path).parent) for path in records}
    directories |= {str(parent) for path in records for parent in Path(path).parents if str(parent) != '.'}
    for current in output.rglob('*'):
        relative = current.relative_to(output).as_posix()
        if relative in directories:
            require(not current.is_symlink() and current.is_dir(), 'unsafe pool directory')
        else:
            require(relative in permitted, 'unexpected output file: ' + relative)
            regular(current)
    for path, record in records.items():
        destination, control = output / path, output / (path + '.aria2')
        safe.safe_directory(destination.parent)
        if control.exists():
            regular(control)
            require(resume and destination.exists(), 'unbound or orphan aria2 partial')
            require(regular(destination).st_size <= record['bytes'], 'oversized aria2 partial')
            missing.append(path)
        elif destination.exists():
            regular(destination)
            safe.verify(destination, {k: record[k] for k in ('bytes', 'sha256')})
        else:
            missing.append(path)
    return missing


def aria_input(output, missing, records):
    lines = []
    for path in missing:
        record = records[path]
        lines.extend(['\t'.join(origin + path for origin in record['origins']),
                      '  dir=' + str((output / path).parent),
                      '  out=' + Path(path).name, '  checksum=sha-256=' + record['sha256']])
    return '\n'.join(lines) + ('\n' if lines else '')


def aria_command(output):
    return ['/usr/bin/aria2c', '--no-conf=true', '--input-file=' + str(output / 'aria2-input.txt'),
            '--max-concurrent-downloads=2', '--split=1', '--max-connection-per-server=1',
            '--check-certificate=true', '--follow-torrent=false', '--follow-metalink=false',
            '--check-integrity=true',
            '--continue=true', '--auto-file-renaming=false', '--allow-overwrite=false',
            '--file-allocation=none', '--max-tries=2', '--retry-wait=2',
            '--connect-timeout=30', '--timeout=60', '--summary-interval=0',
            '--console-log-level=notice', '--download-result=full']


@contextmanager
def interruption_signals():
    def interrupted(number, frame):
        raise InterruptedError('Acquisition interrupted by signal ' + str(number))
    previous = {number: signal.signal(number, interrupted)
                for number in (signal.SIGTERM, signal.SIGHUP)}
    try:
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def download_command(command, log, report):
    """Own the entire private process group until every download is stopped."""
    process = subprocess.Popen(command, env=ENV, stdout=log, stderr=subprocess.STDOUT,
                               start_new_session=True)
    try:
        return process.wait()
    finally:
        # A second interrupt must not skip cleanup or release the directory lock.
        previous = {number: signal.signal(number, signal.SIG_IGN)
                    for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
        try:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
            # The leader can exit while a descendant ignores TERM.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            report['aria2_exit'] = process.returncode
            log.flush()
            os.fsync(log.fileno())
        finally:
            for number, handler in previous.items():
                signal.signal(number, handler)


def write_text(path, value):
    # Inputs/logs are recipe-owned regular files, validated before replacement.
    if path.exists() or path.is_symlink():
        regular(path)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())


def fetch(repo, output, resume=False):
    require(output.is_absolute() and output.parent == repo / 'out' and
            re.fullmatch(r'builder-packages-[A-Za-z0-9][A-Za-z0-9._-]*', output.name),
            'output must be absolute REPO/out/builder-packages-NAME')
    # No whitespace/control characters can enter aria2's line-oriented input.
    require(not any(c.isspace() or ord(c) < 32 for c in str(output)), 'unsafe output characters')
    initial, records = binding(repo), inputs(repo)
    before = helper.check_builder(repo, 'replay-20261106')
    version = run(['/usr/bin/aria2c', '--no-conf=true', '--version']).decode()
    require(version.startswith('aria2 version 1.36.0\n'), 'aria2 version mismatch')
    require(not os.path.lexists(output) if not resume else output.is_dir(),
            'output exists or resume output missing')
    safe.safe_directory(output.parent)
    if not resume:
        output.mkdir(mode=0o700)
    safe.safe_directory(output)
    info = output.stat()
    require(info.st_uid == os.geteuid() and info.st_mode & 0o077 == 0, 'output must be private and owned')
    fd = os.open(output / '.fetch.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    with os.fdopen(fd, 'r+') as guard, interruption_signals():
        regular(output / '.fetch.lock')
        fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = output / 'fetch-state.json'
        if resume:
            regular(state)
            require(safe.read_json(state, 128 * 1024) == initial, 'resume binding mismatch')
        else:
            safe.write_json(state, initial)
        report = {'schema': 1, 'status': 'failed', 'binding': initial, 'builder_before': before,
                  'aria2_version': version, 'jobs': 2, 'aria2_exit': None,
                  'redirect_policy': 'Native aria2 1.36.0 redirects; no redirect restriction. '
                      'Fixed initial HTTPS origins, certificate checks and immutable final SHA256.',
                  'archives': [{'path': path, **record, 'status': 'pending'}
                               for path, record in records.items()]}
        interruption = None
        try:
            if os.path.lexists(output / 'receipt.json'):
                regular(output / 'receipt.json')
                (output / 'receipt.json').unlink()
                safe.fsync_directory(output)
            missing = prepare(output, records, resume)
            require(shutil.disk_usage(output).free >= sum(records[p]['bytes'] for p in missing)
                    + 64 * 1024 * 1024, 'insufficient free space')
            value = aria_input(output, missing, records)
            write_text(output / 'aria2-input.txt', value)
            report['aria2_input_sha256'] = digest(value.encode())
            command = aria_command(output)
            report['command'] = command
            # --help parses every supplied option and exits without starting transfers.
            smoke_command = command + ['--help=#basic']
            report['aria2_option_smoke'] = {'command': smoke_command, 'exit': None}
            try:
                smoke = run(smoke_command)
            except subprocess.CalledProcessError as exc:
                report['aria2_option_smoke'].update(exit=exc.returncode,
                    stderr=(exc.stderr or b'').decode(errors='replace')[:4096])
                raise
            report['aria2_option_smoke'].update(exit=0, stdout_sha256=digest(smoke))
            if missing:
                write_text(output / 'aria2.log', '')
                with (output / 'aria2.log').open('ab') as log:
                    report['aria2_exit'] = download_command(command, log, report)
            for item in report['archives']:
                path = item['path']
                record = records[path]
                item['status'] = 'failed'
                try:
                    require(not os.path.lexists(output / (path + '.aria2')), 'incomplete aria2 transfer')
                    regular(output / path)
                    safe.verify(output / path, {k: record[k] for k in ('bytes', 'sha256')})
                    with (output / path).open('rb') as archive:
                        os.fsync(archive.fileno())
                    safe.fsync_directory((output / path).parent)
                    item['status'] = 'verified'
                except Exception as exc:
                    item['error'] = str(exc)
            require(all(item['status'] == 'verified' for item in report['archives']), 'archive verification failed')
            require(report['aria2_exit'] in (None, 0), 'aria2 failed')
            require(binding(repo) == initial, 'checkout changed during acquisition')
            inputs(repo)
            report.update(status='complete', archive_count=842, total_bytes=983768778,
                          classification='Builder binary acquisition only; clean builder remains unconstructed')
        except BaseException as exc:
            # Ctrl-C must retain failure evidence and perform the final inventory check.
            report.update(status='failed', error=str(exc) or type(exc).__name__)
            if not isinstance(exc, Exception):
                interruption = exc
        try:
            report['builder_after'] = helper.check_builder(repo, 'replay-20261106')
            require(report['builder_after'] == before, 'builder changed during acquisition')
        except Exception as exc:
            report.update(status='failed', error=str(exc))
        if os.path.lexists(output / 'fetch-report.json'):
            regular(output / 'fetch-report.json')
        safe.write_json(output / 'fetch-report.json', report)
        if interruption is not None:
            raise interruption
        require(report['status'] == 'complete', report.get('error', 'acquisition failed'))
        safe.write_json(output / 'receipt.json', report)
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    try:
        report = fetch(REPO, args.output, args.resume)
        print(json.dumps({k: report[k] for k in ('status', 'archive_count', 'total_bytes')}))
        return 0
    except Exception as exc:
        print('Builder archive acquisition failed: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
