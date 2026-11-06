#!/usr/bin/env python3
"""Fetch pinned Debian source archives only; never extract or execute them.

Run from a clean committed checkout. --resume requires the original checkout HEAD
and input hashes. A receipt proves acquisition, not license closure or hardware use.
"""
import argparse
import concurrent.futures
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import ssl
import stat
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request

REPO = Path(__file__).resolve().parents[1]
LOCK = 'configs/host-userspace-source-archives.lock.json'
MAP = 'docs/evidence/host-userspace-source-map-20261106.json'
VERSION_LOCK = 'configs/host-userspace-packages.lock.tsv'
HASH_LOCK = 'configs/host-userspace-package-hashes.lock.tsv'
RECIPE = 'build/fetch-userspace-sources.py'
ORIGINS = ['https://deb.debian.org/debian/',
           'https://snapshot.debian.org/archive/debian/20261001T000000Z/']
SHA = re.compile(r'[0-9a-f]{64}')
COMPONENT = re.compile(r'[A-Za-z0-9][A-Za-z0-9+._~-]*')
PACKAGE = re.compile(r'[a-z0-9][a-z0-9+.-]*')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def object_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate JSON key: ' + key)
        result[key] = value
    return result


def read_json(path, max_bytes=None):
    if max_bytes is None:
        data = path.read_bytes()
    else:
        # O_NONBLOCK prevents a substituted FIFO from blocking before fstat.
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, 'rb') as stream:
            info = os.fstat(stream.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_size <= max_bytes,
                    'unsafe or oversized JSON file: ' + str(path))
            data = stream.read(max_bytes + 1)
            require(len(data) <= max_bytes, 'oversized JSON file: ' + str(path))
    return json.loads(data, object_pairs_hook=object_pairs)


def facts(value):
    require(isinstance(value, dict) and set(value) == {'bytes', 'sha256'},
            'invalid size/hash record')
    require(type(value['bytes']) is int and value['bytes'] > 0, 'invalid byte count')
    require(isinstance(value['sha256'], str) and SHA.fullmatch(value['sha256']),
            'invalid SHA256')


def validate_lock(lock, source_map):
    require(set(lock) == {'schema', 'classification', 'snapshot', 'origins',
                         'source_map', 'authenticated_metadata', 'sources'}, 'invalid lock keys')
    require(type(lock['schema']) is int and lock['schema'] == 1, 'invalid schema')
    require(lock['origins'] == ORIGINS and lock['snapshot'] == '20261001T000000Z',
            'untrusted origins or snapshot')
    require(isinstance(lock['classification'], str), 'invalid classification')
    require(set(lock['source_map']) == {'path', 'bytes', 'sha256'} and
            lock['source_map']['path'] == MAP, 'invalid source map path')
    facts({k: lock['source_map'][k] for k in ('bytes', 'sha256')})
    require(set(lock['authenticated_metadata']) == {'InRelease', 'keyring.deb',
                                                   'Sources.xz', 'Sources'}, 'invalid metadata')
    for value in lock['authenticated_metadata'].values():
        facts(value)
    require(isinstance(lock['sources'], list) and len(lock['sources']) == 149,
            'expected 149 sources')
    records, pairs, binaries = {}, {}, set()
    for source in lock['sources']:
        require(set(source) == {'package', 'version', 'directory', 'binary_packages', 'archives'},
                'invalid source keys')
        require(isinstance(source['package'], str) and PACKAGE.fullmatch(source['package']),
                'invalid package')
        require(isinstance(source['version'], str) and source['version'] and
                not any(c.isspace() or ord(c) < 32 for c in source['version']), 'invalid version')
        pair = (source['package'], source['version'])
        require(pair not in pairs, 'duplicate source pair')
        require(isinstance(source['directory'], str), 'invalid directory')
        components = source['directory'].split('/')
        require(len(components) == 4 and components[:2] == ['pool', 'main'] and
                all(COMPONENT.fullmatch(c) and c not in ('.', '..') for c in components),
                'unsafe directory')
        names = source['binary_packages']
        require(isinstance(names, list) and names and all(isinstance(n, str) and
                PACKAGE.fullmatch(n) for n in names), 'invalid binary packages')
        require(len(set(names)) == len(names) and not binaries.intersection(names),
                'duplicate binary coverage')
        binaries.update(names)
        pairs[pair] = set(names)
        require(isinstance(source['archives'], list) and source['archives'], 'missing archives')
        for archive in source['archives']:
            require(set(archive) == {'filename', 'bytes', 'sha256'}, 'invalid archive keys')
            name = archive['filename']
            require(isinstance(name, str) and COMPONENT.fullmatch(name) and name not in ('.', '..'),
                    'unsafe archive filename')
            record = {k: archive[k] for k in ('bytes', 'sha256')}
            facts(record)
            path = source['directory'] + '/' + name
            require(path not in records, 'duplicate archive path')
            records[path] = record
    require(len(records) == 479 and sum(r['bytes'] for r in records.values()) == 570785529,
            'unexpected archive accounting')
    require(len(binaries) == 220, 'expected 220 binary packages')
    require(source_map['schema'] == 1 and source_map['snapshot'] == lock['snapshot'],
            'source map schema/snapshot mismatch')
    mapped = {}
    require(len(source_map['packages']) == 220, 'source map record count')
    for binary in source_map['packages']:
        source = binary['source_effective']
        pair = (source['package'], source['version'])
        name = binary['binary_package']
        require(name not in mapped, 'duplicate map binary')
        mapped[name] = pair
    require(set(mapped) == binaries and all(mapped[n] == pair for pair, names in pairs.items()
                                           for n in names), 'source map coverage mismatch')
    return records


def inputs(repo):
    lock = read_json(repo / LOCK)
    map_data = (repo / MAP).read_bytes()
    require(len(map_data) == lock['source_map']['bytes'] and
            digest(map_data) == lock['source_map']['sha256'], 'source map hash mismatch')
    source_map = read_json(repo / MAP)
    records = validate_lock(lock, source_map)
    provenance = source_map['provenance']
    for label, path in [('binary_version_lock', VERSION_LOCK), ('binary_hash_lock', HASH_LOCK)]:
        require(provenance[label] == path and
                provenance[label + '_sha256'] == digest((repo / path).read_bytes()),
                'binary lock proof mismatch')
    versions = [line.split('\t') for line in (repo / VERSION_LOCK).read_text().splitlines()]
    require(len(versions) == 220 and {tuple(row) for row in versions} ==
            {(b['binary_package'], b['binary_version'], b['architecture'])
             for b in source_map['packages']}, 'binary version coverage mismatch')
    hashes = []
    for line in (repo / HASH_LOCK).read_text().splitlines():
        name, version, rest = line.split('\t')
        sha, filename = rest.split('  ')
        hashes.append((name, version, sha, filename))
    require(len(hashes) == 220 and set(hashes) ==
            {(b['binary_package'], b['binary_version'], b['sha256'], 'debs/' + b['filename'])
             for b in source_map['packages']}, 'binary hash coverage mismatch')
    return lock, records


def git(repo, *args):
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith('GIT_')}
    return subprocess.check_output(['/usr/bin/git', '-C', str(repo), *args],
                                   stderr=subprocess.PIPE, env=environment)


def binding(repo):
    require(Path(git(repo, 'rev-parse', '--show-toplevel').decode().strip()).resolve() ==
            repo.resolve(), 'Git repository root mismatch')
    require(not git(repo, 'status', '--porcelain', '--untracked-files=normal').strip(),
            'checkout must be clean and committed')
    head = git(repo, 'rev-parse', 'HEAD').decode().strip()
    files = {}
    for path in (LOCK, MAP, VERSION_LOCK, HASH_LOCK, RECIPE):
        data = (repo / path).read_bytes()
        require(data == git(repo, 'show', head + ':' + path), 'input differs from HEAD: ' + path)
        files[path] = {'bytes': len(data), 'sha256': digest(data)}
    return {'head': head, 'files': files}


def safe_directory(path):
    """Refuse symlinks in every existing ancestor before creating directories."""
    for parent in reversed((path, *path.parents)):
        if parent.exists() or parent.is_symlink():
            require(not parent.is_symlink() and parent.is_dir(), 'unsafe directory: ' + str(parent))
        else:
            parent.mkdir(mode=0o700)


def verify(path, record):
    require(not path.is_symlink() and path.is_file() and
            stat.S_ISREG(path.stat().st_mode), 'unsafe archive: ' + str(path))
    require(path.stat().st_size == record['bytes'], 'archive length mismatch: ' + str(path))
    hasher = hashlib.sha256()
    with path.open('rb') as stream:
        remaining = record['bytes']
        while remaining:
            chunk = stream.read(min(1024 * 1024, remaining))
            require(chunk, 'truncated archive')
            remaining -= len(chunk)
            hasher.update(chunk)
        require(not stream.read(1), 'oversized archive')
    require(hasher.hexdigest() == record['sha256'], 'archive hash mismatch: ' + str(path))


class DebianRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlsplit(newurl)
        require(parsed.scheme == 'https' and parsed.hostname in
                {'deb.debian.org', 'snapshot.debian.org'} and parsed.port in (None, 443)
                and not parsed.username and not parsed.password, 'untrusted redirect')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(path, record, output, opener):
    destination = output / path
    safe_directory(destination.parent)
    require(not destination.exists() and not destination.is_symlink(), 'destination already exists')
    errors = []
    for origin in ORIGINS:
        temporary = None
        try:
            with opener.open(origin + path, timeout=60) as response:
                require(response.status == 200, 'unexpected HTTP status')
                length = response.headers.get('Content-Length')
                require(length is None or length == str(record['bytes']), 'HTTP length mismatch')
                descriptor, temporary = tempfile.mkstemp(prefix='.partial-', dir=destination.parent)
                with os.fdopen(descriptor, 'wb') as stream:
                    remaining = record['bytes']
                    while remaining:
                        chunk = response.read(min(1024 * 1024, remaining))
                        require(chunk, 'truncated response')
                        stream.write(chunk)
                        remaining -= len(chunk)
                    require(not response.read(1), 'oversized response')
                    stream.flush()
                    os.fsync(stream.fileno())
            verify(Path(temporary), record)
            # link is atomic and refuses to replace even an unexpected new destination.
            os.link(temporary, destination)
            return {'path': path, **record, 'origin': origin, 'status': 'verified',
                    'attempt_errors': errors}
        except Exception as exc:
            errors.append({'origin': origin, 'error': str(exc)})
        finally:
            if temporary is not None:
                os.unlink(temporary)
                fsync_directory(destination.parent)
        require(not destination.exists() and not destination.is_symlink(),
                'destination appeared during fetch')
    return {'path': path, **record, 'status': 'failed', 'attempt_errors': errors}


def fsync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_json(path, data):
    descriptor, temporary = tempfile.mkstemp(prefix='.report-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(data, stream, indent=2, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def fetch(repo, output, resume=False):
    require(output.is_absolute() and output.parent == repo / 'out' and
            re.fullmatch(r'userspace-sources-[A-Za-z0-9][A-Za-z0-9._-]*', output.name),
            'output must be an absolute REPO/out/userspace-sources-NAME path')
    initial = binding(repo)
    lock, records = inputs(repo)
    require(resume or not output.exists(), 'output exists; use --resume explicitly')
    require(not resume or output.is_dir(), 'resume output missing')
    safe_directory(output.parent)
    if resume:
        safe_directory(output)
    else:
        output.mkdir(mode=0o700)
    lock_fd = os.open(output / '.fetch.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock_fd, 'w') as guard:
        require(stat.S_ISREG(os.fstat(guard.fileno()).st_mode), 'unsafe fetch lock')
        fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state_path = output / 'fetch-state.json'
        if resume:
            require(not state_path.is_symlink() and read_json(state_path, max_bytes=64 * 1024) == initial,
                    'resume binding mismatch')
        else:
            write_json(state_path, initial)
        receipt_path = output / 'receipt.json'
        report_path = output / 'fetch-report.json'
        # A previously successful receipt cannot survive a failed revalidation.
        if receipt_path.exists() or receipt_path.is_symlink():
            receipt_path.unlink()
        previous = {}
        if resume and report_path.exists():
            require(not report_path.is_symlink(), 'unsafe report')
            previous = {r['path']: r for r in read_json(report_path, max_bytes=4 * 1024 * 1024).get('archives', [])}
        results, missing = [], []
        try:
            for path, record in records.items():
                destination = output / path
                safe_directory(destination.parent)
                if destination.exists() or destination.is_symlink():
                    try:
                        verify(destination, record)
                    except Exception as exc:
                        results.append({'path': path, **record, 'status': 'failed',
                                        'error': str(exc)})
                        raise
                    old = previous.get(path, {})
                    require(old.get('origin') in (None, *ORIGINS), 'untrusted cached origin')
                    results.append({'path': path, **record, 'status': 'verified',
                                    'origin': old.get('origin'), 'revalidated_cache': True})
                else:
                    missing.append(path)
            require(shutil.disk_usage(output).free >=
                    sum(records[p]['bytes'] for p in missing) + 64 * 1024 * 1024,
                    'insufficient free space')
            def worker(path):
                opener = urllib.request.build_opener(
                    urllib.request.HTTPSHandler(context=ssl.create_default_context()), DebianRedirect())
                return download(path, records[path], output, opener)
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                futures = {pool.submit(worker, path): path for path in missing}
                for future in concurrent.futures.as_completed(futures):
                    path = futures[future]
                    try:
                        results.append(future.result())
                    except Exception as exc:
                        results.append({'path': path, **records[path], 'status': 'failed',
                                        'error': str(exc)})
            require(all(r['status'] == 'verified' for r in results), 'one or more downloads failed')
            for path, record in records.items():
                try:
                    safe_directory((output / path).parent)
                    verify(output / path, record)
                except Exception as exc:
                    for result in results:
                        if result['path'] == path:
                            result.update(status='failed', error=str(exc))
                    raise
            require(binding(repo) == initial, 'checkout changed during fetch')
            inputs(repo)
            report = {'schema': 1, 'status': 'complete', 'binding': initial,
                      'classification': 'Source archive acquisition only; license closure remains open',
                      'sources': len(lock['sources']), 'binary_packages': 220,
                      'archive_count': len(records), 'total_bytes': sum(r['bytes'] for r in records.values()),
                      'authenticated_metadata': lock['authenticated_metadata'],
                      'archives': sorted(results, key=lambda r: r['path'])}
            write_json(report_path, report)
            write_json(receipt_path, report)
            return report
        except Exception as exc:
            write_json(report_path, {'schema': 1, 'status': 'failed', 'binding': initial,
                                    'error': str(exc), 'archives': sorted(results, key=lambda r: r['path'])})
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    try:
        report = fetch(REPO, args.output, args.resume)
    except Exception as exc:
        print('Source fetch failed: ' + str(exc), file=sys.stderr)
        return 1
    print(json.dumps({k: report[k] for k in ('status', 'archive_count', 'total_bytes')}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
