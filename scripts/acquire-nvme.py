#!/usr/bin/env python3
"""Acquire authenticated encoded payloads into explicitly selected off-SD storage.

This guard does not identify the installation target or distinguish a USB SD
reader from other USB disks. The writer independently checks target overlap.
No mounting, extraction to disk, or block-device I/O is performed here.
"""
import argparse
import contextlib
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import ssl
import stat
import time
import threading
import urllib.parse
import urllib.request


def adjacent(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


WRITER = adjacent('acquire_nvme_writer', 'install-nvme.py')
LAYOUT = WRITER.LAYOUT
MIB = 1024**2
CHUNK = 64 * 1024


def require(condition, message):
    if not condition:
        raise ValueError(message)


def https_url(value):
    require(isinstance(value, str) and not any(c.isspace() or ord(c) < 32 or ord(c) == 127
                                             for c in value), 'invalid HTTPS URL')
    try:
        parsed = urllib.parse.urlsplit(value)
        valid = (parsed.scheme == 'https' and parsed.hostname and not parsed.username
                 and not parsed.password and '@' not in parsed.netloc and '#' not in value)
        parsed.port
    except ValueError:
        valid = False
    require(valid, 'invalid HTTPS URL')
    return value


def public_url(value):
    parsed = urllib.parse.urlsplit(https_url(value))
    # Queries may contain credentials; receipts only retain origin and path.
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, '', ''))


@contextlib.contextmanager
def directory(path):
    """Walk absolute ancestors using held descriptors and O_NOFOLLOW."""
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts, 'absolute path without .. required')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        yield fd
    finally:
        os.close(fd)


def storage_guard(parent_fd, kind, mountinfo=None, sysroot=Path('/sys/dev/block')):
    require(kind in ('ram', 'external'), 'explicit storage class required')
    device = os.fstat(parent_fd).st_dev
    major_minor = f'{os.major(device)}:{os.minor(device)}'
    text = Path('/proc/self/mountinfo').read_text() if mountinfo is None else mountinfo
    matches = []
    for line in text.splitlines():
        before, after = line.split(' - ', 1)
        if before.split()[2] == major_minor:
            matches.append(after.split()[0])
    require(matches and len(set(matches)) == 1, 'unknown or ambiguous storage filesystem')
    filesystem = matches[0]
    if kind == 'ram':
        require(filesystem in ('tmpfs', 'ramfs'), 'RAM storage requires tmpfs or ramfs')
        return
    require(filesystem in ('ext2', 'ext3', 'ext4', 'xfs', 'vfat', 'exfat', 'ntfs3'),
            'external storage requires a supported local filesystem')
    dependencies = WRITER.block_dependencies(major_minor, sysroot=sysroot)
    for dependency in dependencies:
        node = (sysroot / dependency).resolve(strict=True)
        require(not node.name.startswith('mmcblk'), 'SD/MMC backing storage is forbidden')


def tls_context(ca_file=None):
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    if ca_file:
        path = Path(ca_file)
        with directory(path.absolute().parent) as parent:
            before = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            require(stat.S_ISREG(before.st_mode), 'CA file must be regular')
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            with os.fdopen(fd, 'rb') as source:
                initial = WRITER.file_identity(source.fileno())
                require((initial['device'], initial['inode']) == (before.st_dev, before.st_ino),
                        'CA file changed before opening')
                data = source.read(4*MIB + 1)
                require(len(data) <= 4*MIB, 'CA file exceeds size limit')
                require(WRITER.file_identity(source.fileno()) == initial, 'CA file changed')
        context.load_verify_locations(cadata=data.decode('ascii'))
    return context


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('HTTPS redirects are forbidden')


def remaining(deadline):
    value = deadline - time.monotonic()
    require(value > 0, 'acquisition deadline exceeded')
    return min(30, value)


@contextlib.contextmanager
def deadline_guard(seconds):
    """Bound Python network parsing too; preserve inactive caller alarm state.

    Linux signals interrupt Python socket/header processing. They cannot promise
    to interrupt an uninterruptible kernel operation or every libc DNS resolver.
    The caller must run in the main thread with SIGALRM unblocked and no active
    alarm; refusing otherwise avoids silently losing the overall time bound.
    """
    require(threading.current_thread() is threading.main_thread(),
            'acquisition requires the main thread for its deadline guard')
    require(hasattr(signal, 'setitimer') and hasattr(signal, 'pthread_sigmask'),
            'Linux interval timer support required')
    blocked = signal.pthread_sigmask(signal.SIG_BLOCK, [])
    require(signal.SIGALRM not in blocked and signal.SIGALRM not in signal.sigpending(),
            'SIGALRM must be unblocked and not pending')
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    require(previous_timer == (0.0, 0.0), 'existing real-time alarm prevents acquisition')
    previous_handler = signal.getsignal(signal.SIGALRM)
    def expired(signum, frame):
        raise TimeoutError('acquisition deadline exceeded')
    deadline = time.monotonic() + seconds
    try:
        signal.signal(signal.SIGALRM, expired)
        signal.setitimer(signal.ITIMER_REAL, seconds)
        yield deadline
    finally:
        try:
            signal.setitimer(signal.ITIMER_REAL, 0)
        finally:
            signal.signal(signal.SIGALRM, previous_handler)


def fetch(opener, url, destination, cap, deadline, expected=None):
    """Bound encoded reads even without a truthful Content-Length."""
    https_url(url)
    try:
        response = opener.open(urllib.request.Request(url, headers={'Accept-Encoding': 'identity'}),
                               timeout=remaining(deadline))
    except Exception:
        raise ValueError('HTTPS request failed') from None
    with response:
        require(response.status == 200, 'HTTPS status must be 200')
        encodings = response.headers.get_all('Content-Encoding', [])
        require(not encodings or encodings == ['identity'], 'unsupported Content-Encoding')
        lengths = response.headers.get_all('Content-Length', [])
        declared = None
        if lengths:
            require(len(lengths) == 1 and re.fullmatch('[0-9]+', lengths[0]),
                    'invalid Content-Length')
            declared = int(lengths[0])
            require(declared <= cap and (expected is None or declared == expected['bytes']),
                    'Content-Length exceeds or differs from authenticated size')
        count, digest = 0, hashlib.sha256()
        while True:
            timeout = remaining(deadline)
            try:
                # urllib's HTTPResponse uses this socket; update for the total deadline.
                if response.fp is not None:
                    response.fp.raw._sock.settimeout(timeout)
                chunk = response.read1(min(CHUNK, cap - count + 1))
            except Exception:
                raise ValueError('HTTPS body read failed') from None
            remaining(deadline)
            if not chunk:
                break
            count += len(chunk)
            require(count <= cap, 'HTTPS body exceeds size limit')
            destination.write(chunk)
            digest.update(chunk)
        require(declared is None or count == declared, 'HTTPS body length mismatch')
        record = {'bytes': count, 'sha256': digest.hexdigest()}
        require(expected is None or record == expected, 'encoded size or SHA-256 mismatch')
        return record


def validate_manifest(data, pin):
    require(re.fullmatch('[0-9a-f]{64}', pin or '') and hashlib.sha256(data).hexdigest() == pin,
            'trusted manifest SHA-256 mismatch')
    value = json.loads(data, object_pairs_hook=WRITER.unique_pairs)
    require(isinstance(value, dict), 'manifest object required')
    require(type(value.get('schema')) is int and value['schema'] == 1, 'unsupported manifest schema')
    require(value.get('kernel_release') == '6.6.98-a733-diag', 'unsupported kernel profile')
    raw = WRITER.record_valid(value['files']['nvme.img'])
    encoded = WRITER.record_valid(value['files']['nvme.img.gz'])
    geometry = LAYOUT.layout(raw['bytes'], value['logical_sector_bytes'], value['physical_sector_bytes'])
    require(value['layout'] == geometry, 'manifest differs from fixed host layout')
    if 'deployment_intent' in value:
        intent = value['deployment_intent']
        require(isinstance(intent, dict) and type(intent.get('for_installation')) is bool,
                'invalid deployment intent')
        if intent['for_installation']:
            WRITER.validate_manifest(value)
    return value, raw, encoded


def verify_stream(source, expected, deadline):
    count, digest = 0, hashlib.sha256()
    while True:
        remaining(deadline)
        data = source.read(min(CHUNK, expected['bytes'] - count + 1))
        remaining(deadline)
        if not data:
            break
        count += len(data)
        require(count <= expected['bytes'], 'stream exceeds authenticated size')
        digest.update(data)
    require(count == expected['bytes'] and digest.hexdigest() == expected['sha256'],
            'stream size or SHA-256 mismatch')


def remove_owned(fd, name, identity):
    """Only remove a name still referring to the file this invocation created."""
    with contextlib.suppress(FileNotFoundError):
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if (info.st_dev, info.st_ino) == identity:
            os.unlink(name, dir_fd=fd)


def new_file(fd, name):
    descriptor = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=fd)
    try:
        os.fchmod(descriptor, 0o600)
        require(stat.S_IMODE(os.fstat(descriptor).st_mode) == 0o600,
                'storage cannot enforce private file permissions')
        return os.fdopen(descriptor, 'w+b')
    except BaseException:
        info = os.fstat(descriptor)
        remove_owned(fd, name, (info.st_dev, info.st_ino))
        os.close(descriptor)
        raise


def check_directory(fd, output):
    with directory(output) as current:
        require((os.fstat(current).st_dev, os.fstat(current).st_ino) ==
                (os.fstat(fd).st_dev, os.fstat(fd).st_ino), 'output directory changed')


def check_image_name(fd, identity):
    info = os.stat('nvme.img.gz', dir_fd=fd, follow_symlinks=False)
    require(stat.S_ISREG(info.st_mode) and
            {'device': info.st_dev, 'inode': info.st_ino, 'bytes': info.st_size,
             'mtime_ns': info.st_mtime_ns, 'ctime_ns': info.st_ctime_ns} == identity,
            'image name changed')


def acquire(manifest_url, image_url, manifest_sha256, output, storage_kind,
            ca_file=None, max_seconds=3600):
    require(type(max_seconds) in (int, float) and 0 < max_seconds <= 86400,
            'max seconds must be positive and at most 86400')
    with deadline_guard(max_seconds) as deadline:
        return _acquire(manifest_url, image_url, manifest_sha256, output, storage_kind,
                        ca_file, deadline)


def _acquire(manifest_url, image_url, manifest_sha256, output, storage_kind, ca_file, deadline):
    https_url(manifest_url)
    https_url(image_url)
    require(re.fullmatch('[0-9a-f]{64}', manifest_sha256 or ''), 'invalid manifest SHA-256 pin')
    context = tls_context(ca_file)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
                                        urllib.request.HTTPSHandler(context=context))
    output = Path(output)
    require(output.is_absolute() and '..' not in output.parts and output.name,
            'new absolute output directory required')
    with directory(output.parent) as parent:
        storage_guard(parent, storage_kind)
        os.mkdir(output.name, 0o700, dir_fd=parent)
        fd = os.open(output.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        published = {}
        try:
            require(stat.S_IMODE(os.fstat(fd).st_mode) == 0o700, 'output must be private')
            with new_file(fd, '.manifest.partial') as manifest_file:
                manifest_record = fetch(opener, manifest_url, manifest_file, 4*MIB, deadline)
                manifest_file.flush()
                os.fsync(manifest_file.fileno())
                manifest_file.seek(0)
                manifest_bytes = manifest_file.read(4*MIB + 1)
                manifest, raw, encoded = validate_manifest(manifest_bytes, manifest_sha256)
                manifest_identity = WRITER.file_identity(manifest_file.fileno())
                space = os.fstatvfs(fd)
                require(space.f_bavail * space.f_frsize >= encoded['bytes'] + 64*MIB,
                        'insufficient free storage for payload and 64 MiB reserve')
                with new_file(fd, 'nvme.img.gz') as image:
                    fetch(opener, image_url, image, encoded['bytes'], deadline, encoded)
                    image.flush()
                    os.fsync(image.fileno())
                    identity = WRITER.file_identity(image.fileno())
                    image.seek(0)
                    verify_stream(image, encoded, deadline)
                    image.seek(0)
                    with gzip.GzipFile(fileobj=image, mode='rb') as decoded:
                        verify_stream(decoded, raw, deadline)
                    require(WRITER.file_identity(image.fileno()) == identity, 'image changed')
                    require(WRITER.file_identity(manifest_file.fileno()) == manifest_identity,
                            'manifest changed')
                    check_image_name(fd, identity)
                    check_directory(fd, output)
                    receipt = {'schema': 1, 'success': True, 'storage_kind': storage_kind,
                               'manifest_url': public_url(manifest_url), 'image_url': public_url(image_url),
                               'manifest': manifest_record, 'encoded': encoded, 'decoded': raw,
                               'deployment_intent': manifest.get('deployment_intent')}
                    # Exclusive regular files also work on FAT/exFAT (no hardlinks).
                    # A failure removes our published names, retaining private partials.
                    for name, data in (('manifest.json', manifest_bytes),
                                       ('acquisition.json', (json.dumps(receipt, sort_keys=True) + '\n').encode())):
                        with new_file(fd, name) as result:
                            info = os.fstat(result.fileno())
                            published[name] = ((info.st_dev, info.st_ino), os.dup(result.fileno()))
                            result.write(data)
                            result.flush()
                            os.fsync(result.fileno())
                    os.unlink('.manifest.partial', dir_fd=fd)
                    os.fsync(fd)
                    check_image_name(fd, identity)
                    check_directory(fd, output)
                    # Finish while cleanup still owns the published names. No owned
                    # alarm may fire after success has escaped this cleanup scope.
                    signal.setitimer(signal.ITIMER_REAL, 0)
                    remaining(deadline)
                    return receipt
        except BaseException:
            # A different failure near the deadline must not interrupt cleanup.
            signal.setitimer(signal.ITIMER_REAL, 0)
            for name, (identity, held_fd) in published.items():
                remove_owned(fd, name, identity)
            raise
        finally:
            for identity, held_fd in published.values():
                os.close(held_fd)
            os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest-url', required=True)
    parser.add_argument('--image-url', required=True)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--storage-kind', choices=('ram', 'external'), required=True)
    parser.add_argument('--ca-file')
    parser.add_argument('--max-seconds', type=int, default=3600)
    args = parser.parse_args()
    try:
        acquire(**vars(args))
    except Exception:
        # Network/library exception strings can contain credential-bearing URLs.
        parser.exit(1, 'acquisition failed; no successful receipt published\n')
    print('verified payload acquisition complete')


if __name__ == '__main__':
    main()
