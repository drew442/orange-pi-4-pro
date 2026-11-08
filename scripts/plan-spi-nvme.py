#!/usr/bin/python3
"""Compare the pinned A733 SPI candidate with two fresh read-only device reads."""
import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import types

SIZE = 16777216
ERASE = 65536
MANIFEST_LIMIT = 128 * 1024
LIB_SHA = 'b160e3fe8e421e18e35aad34aff909e10b4dfca027178d80a4b202c711e2147d'
MANIFEST_SHA = 'd1444bb4f0c16d9495e125d3db4b7e7e130af294106f356c850d0e0b8fb31ff0'
CANDIDATE_SHA = '83edcd7f448e8353163666016a6bae3e9b70d041a5e4d8454f48931839205e90'
BASELINE_SHA = 'dffab0dd410657cb30c7b2fd7f2586a4792e8472e58882b3532581f8111a646d'
RANGES = [(0, 237568), (262144, 1654784)]
READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def stamp(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class HeldFile:
    """Keep every nofollow path component open and detect later link drift."""
    def __init__(self, path, limit, exact=None):
        self.fds = []
        self.links = []
        try:
            parts = path.split('/')
            require(parts[0] == '' and len(parts) > 2 and
                    all(p not in ('', '.', '..') for p in parts[1:]),
                    'input requires an absolute canonical path')
            directory = os.open('/', READ_FLAGS | os.O_DIRECTORY)
            self.fds.append(directory)
            for index, name in enumerate(parts[1:]):
                info = os.fstat(directory)
                require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and
                        not info.st_mode & 0o022, 'unsafe root-owned ancestry')
                last = index == len(parts) - 2
                if last:
                    require(stat.S_IMODE(info.st_mode) == 0o700,
                            'input parent must be root-private mode 0700')
                fd = os.open(name, READ_FLAGS | (0 if last else os.O_DIRECTORY),
                             dir_fd=directory)
                self.fds.append(fd)
                self.links.append((directory, name, fd, stamp(os.fstat(fd))))
                directory = fd
            self.fd = directory
            info = os.fstat(self.fd)
            require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and
                    stat.S_IMODE(info.st_mode) == 0o400 and info.st_nlink == 1,
                    'input must be single-link root-owned regular mode 0400')
            require(0 < info.st_size <= limit and
                    (exact is None or info.st_size == exact), 'invalid input size')
            self.size = info.st_size
            self.data = self.read()
            self.check()
        except BaseException:
            self.close()
            raise

    def read(self):
        data = os.pread(self.fd, self.size + 1, 0)
        require(len(data) == self.size, 'input short read or size drift')
        return data

    def check(self):
        for parent, name, fd, initial in self.links:
            require(stamp(os.fstat(fd)) == initial and
                    stamp(os.stat(name, dir_fd=parent, follow_symlinks=False)) == initial,
                    'input path/identity changed')
        require(self.read() == self.data, 'input content changed')

    def close(self):
        for fd in reversed(self.fds):
            os.close(fd)
        self.fds = []


def load_library(held):
    require(sha(held.data) == LIB_SHA, 'backup library source pin mismatch')
    module = types.ModuleType('authenticated_spi_backup')
    # Compile exactly the authenticated bytes; no loader or pycache pathname race.
    exec(compile(held.data, '<authenticated backup-spi.py>', 'exec'), module.__dict__)
    return module


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate manifest key')
        result[key] = value
    return result


def validate_manifest(data, candidate):
    require(sha(data) == MANIFEST_SHA, 'candidate manifest pin mismatch')
    m = json.loads(data, object_pairs_hook=unique_object)
    require(m['schema'] == 1 and m['stage'] == 'assembled-file-only' and
            m['success'] is True and m['erase_size'] == ERASE,
            'invalid candidate manifest metadata')
    require(m['candidate'] == dict(bytes=SIZE, sha256=CANDIDATE_SHA) and
            m['baseline'] == dict(bytes=SIZE, sha256=BASELINE_SHA) and
            len(candidate) == SIZE and sha(candidate) == CANDIDATE_SHA,
            'candidate/baseline pin mismatch')
    require(all(m[k] is False for k in ('physical_writes', 'recovery_proven',
            'hardware_acceptance', 'deployment_authorized')), 'unsafe manifest gates')
    require(m['spi_identity']['device'] == '/dev/mtd0' and
            m['spi_identity']['dev'] == '90:0', 'unexpected pinned device')
    require([(r['offset'], r['end']) for r in m['replacement_ranges']] == RANGES and
            m['affected_erase_units'] == list(range(26)) and
            m['affected_erase_end'] == 26 * ERASE, 'unexpected replacement envelope')
    units = m['erase_units']
    require(len(units) == SIZE // ERASE, 'incomplete erase map')
    for index, unit in enumerate(units):
        require(type(unit['index']) is int and unit['index'] == index and
                type(unit['offset']) is int and unit['offset'] == index * ERASE and
                type(unit['end']) is int and unit['end'] == (index + 1) * ERASE,
                'invalid erase unit position')
        for key in ('original_sha256', 'candidate_sha256'):
            require(isinstance(unit[key], str) and
                    re.fullmatch('[0-9a-f]{64}', unit[key]), 'invalid erase unit hash')
        require(unit['changed'] is (index < 26) and
                (unit['original_sha256'] != unit['candidate_sha256']) == unit['changed'],
                'invalid changed-unit map')
        require(sha(candidate[index * ERASE:(index + 1) * ERASE]) ==
                unit['candidate_sha256'], 'candidate erase unit mismatch')
    for r in m['replacement_ranges'] + m['preserved_ranges']:
        require(r['bytes'] == r['end'] - r['offset'] and
                sha(candidate[r['offset']:r['end']]) == r['sha256'],
                'candidate range mismatch')
    require([(r['offset'], r['end']) for r in m['preserved_ranges']] ==
            [(237568, 262144), (1654784, SIZE)], 'incomplete preservation map')
    return m


def boot_id():
    value = Path('/proc/sys/kernel/random/boot_id').read_text(encoding='ascii').strip()
    require(re.fullmatch('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', value),
            'invalid boot ID')
    return value


def read_live(fd, m, candidate, pass_number, reader=os.pread):
    digest = hashlib.sha256()
    units = []
    for index, unit in enumerate(m['erase_units']):
        offset = index * ERASE
        data = reader(fd, ERASE, offset)
        require(len(data) == ERASE, 'short/invalid live SPI read')
        unit_hash = sha(data)
        require(unit_hash == unit['original_sha256'], 'live SPI unit differs from baseline')
        digest.update(data)
        units.append(unit_hash)
        # Compare preserved bytes directly, including the gap and partial-unit tail.
        for start, end in ((237568, 262144), (1654784, SIZE)):
            lo, hi = max(start, offset), min(end, offset + ERASE)
            if lo < hi:
                require(data[lo-offset:hi-offset] == candidate[lo:hi],
                        'preserved live bytes differ from candidate')
        if (index + 1) % 16 == 0:
            print(f'SPI read {pass_number}: {(index + 1) * ERASE}/{SIZE} bytes',
                  file=sys.stderr, flush=True)
    require(reader(fd, 1, SIZE) == b'', 'live SPI exceeds expected size')
    require(digest.hexdigest() == BASELINE_SHA, 'live SPI full baseline mismatch')
    return digest.hexdigest(), units


def compare_live(library, device, m, candidate):
    require(device == '/dev/mtd0', 'only pinned /dev/mtd0 is supported')
    before_boot = boot_id()
    fd, snapshot = library.open_source(device)
    try:
        initial = stamp(os.fstat(fd))
        def guard():
            require(stamp(os.fstat(fd)) == initial, 'device FD identity changed')
            link = os.stat(device, follow_symlinks=False)
            require(stat.S_ISCHR(link.st_mode) and
                    (link.st_dev, link.st_ino, link.st_rdev) ==
                    (os.fstat(fd).st_dev, os.fstat(fd).st_ino, os.fstat(fd).st_rdev),
                    'device path no longer binds to open FD')
            require(library.identity(fd, device) == snapshot == m['spi_identity'],
                    'live SPI identity differs from pinned identity')
        guard()
        first = read_live(fd, m, candidate, 1)
        guard()
        second = read_live(fd, m, candidate, 2)
        guard()
        require(first == second, 'live SPI changed between reads')
        require(boot_id() == before_boot, 'host boot ID changed')
        return dict(boot_id=before_boot, live_identity=snapshot,
                    live_sha256=first[0], source_reads=2)
    finally:
        os.close(fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', required=True)
    parser.add_argument('--candidate', required=True)
    parser.add_argument('--manifest', required=True)
    args = parser.parse_args(argv)
    require(os.geteuid() == 0, 'live planning requires root')
    with ExitStack() as stack:
        def hold(path, limit, exact=None):
            held = HeldFile(path, limit, exact)
            stack.callback(held.close)
            return held
        # Use the physical sibling, with no resolve() that could follow aliases.
        library_file = hold(str(Path(os.path.abspath(__file__)).parent / 'backup-spi.py'),
                            MANIFEST_LIMIT)
        library = load_library(library_file)
        manifest = hold(args.manifest, MANIFEST_LIMIT)
        candidate = hold(args.candidate, SIZE, SIZE)
        require((os.fstat(manifest.fd).st_dev, os.fstat(manifest.fd).st_ino) !=
                (os.fstat(candidate.fd).st_dev, os.fstat(candidate.fd).st_ino),
                'candidate and manifest alias')
        m = validate_manifest(manifest.data, candidate.data)
        result = compare_live(library, args.device, m, candidate.data)
        for held in (library_file, manifest, candidate):
            held.check()
        result.update(operation='read-only-live-comparison', success=True,
                      manifest_sha256=MANIFEST_SHA, backup_library_sha256=LIB_SHA,
                      candidate=m['candidate'], baseline=m['baseline'],
                      replacement_ranges=m['replacement_ranges'],
                      preserved_ranges=m['preserved_ranges'],
                      preservation_verified=True, erase_units=m['erase_units'],
                      planned_full_unit_bytes=26 * ERASE,
                      changed_units=list(range(26)), skipped_units=list(range(26, 256)),
                      untouched_units=list(range(26, 256)),
                      affected_erase_end=26 * ERASE,
                      physical_writes=False, recovery_proven=False,
                      deployment_authorized=False, hardware_acceptance=False,
                      programmer_available=False)
        print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f'SPI live plan refused/failed: {error}', file=sys.stderr)
        sys.exit(1)
