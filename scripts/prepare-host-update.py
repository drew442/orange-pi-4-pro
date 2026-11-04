#!/usr/bin/env python3
"""Authenticate and stage a file-only A733 host update candidate.

No device selection, installed selector publication, mounts, downloads, update
application or authorization. The immutable boot stack cannot change here.
"""
import argparse
import contextlib
import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess

REPO = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


SELECT = module('update_selection', REPO / 'build/root-selection.py')
INIT = SELECT.INIT
ACQUIRE = module('update_acquire', REPO / 'scripts/acquire-nvme.py')
WRITER = ACQUIRE.WRITER
LIMIT = 1024**2
RESERVE = 1024**3
CHUNK = 1024**2


def require(condition, message):
    if not condition:
        raise ValueError(message)


def record(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def authenticated_manifest(path, pin):
    """Never pass untrusted bytes to the JSON parser, even to inspect schema."""
    require(isinstance(pin, str) and re.fullmatch('[0-9a-f]{64}', pin),
            'explicit lowercase manifest SHA-256 required')
    with WRITER.open_regular(path) as source:
        identity = WRITER.file_identity(source.fileno())
        require(0 < identity['bytes'] <= LIMIT, 'manifest exceeds size bound or is empty')
        data = source.read(LIMIT + 1)
        require(len(data) <= LIMIT and record(data)['sha256'] == pin,
                'trusted manifest SHA-256 mismatch')
        require(WRITER.file_identity(source.fileno()) == identity, 'manifest changed during read')
    value = json.loads(data, object_pairs_hook=WRITER.unique_pairs)
    require(isinstance(value, dict), 'manifest object required')
    return data, value, identity


def validate_manifest(value, slot, slot_bytes=None):
    slot_bytes = INIT.PAYLOAD.SLOT_BYTES if slot_bytes is None else slot_bytes
    require(slot in INIT.PAYLOAD.SLOT_UUIDS, 'unsupported root slot')
    require(type(value.get('schema')) is int and value['schema'] == 1
            and value.get('board') == 'Orange Pi 4 Pro A733', 'incompatible manifest schema/board')
    require(type(value.get('slot_bytes')) is int and value['slot_bytes'] == slot_bytes
            and value.get('slot_uuids') == INIT.PAYLOAD.SLOT_UUIDS
            and value.get('kernel_release') == INIT.PAYLOAD.KERNEL_RELEASE,
            'incompatible slot size, UUIDs or kernel')
    profile = value.get('management_runtime')
    require(isinstance(profile, dict) and set(profile) == {'profile', 'files'}
            and profile.get('profile') == 'a733-management-v1'
            and isinstance(profile.get('files'), dict)
            and set(profile['files']) == {v[0] for v in INIT.PAYLOAD.MANAGEMENT_FILES.values()},
            'incompatible management profile')
    for item in profile['files'].values():
        WRITER.record_valid(item)
    INIT.PAYLOAD.PACKAGE.validate_profile(value.get('package_state'))
    expected = WRITER.record_valid(value['files'][f'root-{slot}.ext4'])
    require(expected['bytes'] == slot_bytes, 'root must fill fixed slot byte budget')
    return expected


@contextlib.contextmanager
def safe_parent(path):
    """Hold a no-symlink walk; every ancestor must exclude untrusted writers."""
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts, 'absolute parent without .. required')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in ('', *path.parts[1:]):
            if part:
                following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = following
            info = os.fstat(fd)
            require(info.st_uid in (0, os.geteuid()) and not info.st_mode & 0o022,
                    'staging ancestors must have trusted ownership and no group/world writes')
        yield fd
    finally:
        os.close(fd)


def capture_root(path, expected, target, after_chunk=lambda: None):
    """Sparse copy, complete logical hash and stable held input identity."""
    require(str(path).endswith('.ext4'), 'only raw .ext4 roots supported; gzip is unsupported')
    with WRITER.open_regular(path) as source:
        identity = WRITER.file_identity(source.fileno())
        require(identity['bytes'] == expected['bytes'], 'root source size mismatch')
        length, digest = 0, hashlib.sha256()
        while True:
            data = source.read(min(CHUNK, expected['bytes'] - length + 1))
            if not data:
                break
            length += len(data)
            require(length <= expected['bytes'], 'root source grew during capture')
            digest.update(data)
            if data.strip(b'\0'):
                target.write(data)
            else:
                target.seek(len(data), os.SEEK_CUR)
            after_chunk()
        require(WRITER.file_identity(source.fileno()) == identity, 'root source changed during capture')
        require({'bytes': length, 'sha256': digest.hexdigest()} == expected,
                'captured root size/SHA-256 mismatch')
        target.truncate(length)
        target.flush()
        os.fchmod(target.fileno(), 0o400)
        os.fsync(target.fileno())
    # Authenticate the private captured inode independently before filesystem parsing.
    target.seek(0)
    WRITER.hash_stream(target, expected)
    target.seek(0)
    return identity


def compatibility(stream, slot, manifest, diagnostic=None):
    marker, metadata = INIT.root_marker(stream, slot)
    diagnostic = (json.loads((REPO / 'docs/evidence/diagnostic-20261104.json').read_bytes())['files']
                  if diagnostic is None else diagnostic)
    for name in INIT.PAYLOAD.BOOT_FILES:
        require(metadata['sources'][name] == diagnostic[name]
                and manifest['sources'][name] == diagnostic[name],
                'root/manifest immutable boot stack differs from pinned diagnostic')
    for name in ('management_runtime', 'package_state'):
        require(metadata.get(name) == manifest[name], f'root {name} differs from authenticated manifest')
    selector = SELECT.serialize({'SCHEMA': '1', 'SLOT': slot,
                                 'ROOT_UUID': INIT.PAYLOAD.SLOT_UUIDS[slot],
                                 'ROOT_MARKER_SHA256': hashlib.sha256(marker).hexdigest(),
                                 'KERNEL_RELEASE': INIT.PAYLOAD.KERNEL_RELEASE})
    return selector, {name: metadata[name] for name in
                      ('sources', 'management_runtime', 'package_state')}


def rename_new(parent, old, new):
    """Linux atomic directory publication which cannot overwrite any object."""
    library = ctypes.CDLL(None, use_errno=True)
    rename = library.renameat2
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(parent, os.fsencode(old), parent, os.fsencode(new), 1) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


class PublicationError(OSError):
    def __init__(self, error, published):
        self.published = published
        super().__init__(f'candidate publication failed; published={published}; '
                         f'durability={"uncertain" if published else "not published"}: {error}')


def prepare(image, output, slot, manifest_path, manifest_sha256, storage_kind, *,
            slot_bytes=None, parent_opener=safe_parent, storage_guard=ACQUIRE.storage_guard,
            inspect=compatibility, publish=rename_new, reserve=RESERVE):
    """Injectable file-only core. CLI always uses fixed production slot policy."""
    data, manifest, manifest_identity = authenticated_manifest(manifest_path, manifest_sha256)
    expected = validate_manifest(manifest, slot, slot_bytes)
    output = Path(output)
    require(output.is_absolute() and '..' not in output.parts and output.name,
            'new absolute output directory required')
    with parent_opener(output.parent) as parent:
        storage_guard(parent, storage_kind)
        space = os.fstatvfs(parent)
        required = expected['bytes'] + reserve
        require(space.f_bavail * space.f_frsize >= required,
                'insufficient staging space: fixed root slot plus 1 GiB reserve required')
        try:
            os.stat(output.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ValueError('output already exists (including symlinks)')
        temporary = '.host-update-' + secrets.token_hex(16)
        os.mkdir(temporary, 0o700, dir_fd=parent)
        stage = os.open(temporary, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        stage_info = os.fstat(stage)
        owned = {}
        published = False
        def new_file(name):
            fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=stage)
            info = os.fstat(fd)
            owned[name] = (info.st_dev, info.st_ino)
            return os.fdopen(fd, 'w+b')
        def save(name, content):
            with new_file(name) as target:
                target.write(content)
                target.flush()
                os.fchmod(target.fileno(), 0o400)
                os.fsync(target.fileno())
        try:
            require(stat.S_IMODE(stage_info.st_mode) == 0o700, 'private staging permissions required')
            with new_file('root.ext4') as root:
                identity = capture_root(image, expected, root)
                selector, compatible = inspect(root, slot, manifest)
            save('manifest.json', data)
            save('selected.conf', selector)
            candidate = {'schema': 1, 'operation': 'file-only-host-update-candidate',
                         'classification': 'preparation only; no installed updater or hardware acceptance',
                         'slot': slot, 'root_uuid': INIT.PAYLOAD.SLOT_UUIDS[slot],
                         'kernel_release': INIT.PAYLOAD.KERNEL_RELEASE,
                         'root': expected, 'selector': record(selector),
                         'manifest': {'path': os.path.abspath(manifest_path), **record(data),
                                      'identity': manifest_identity},
                         'source': {'path': os.path.abspath(image), 'identity': identity},
                         'compatibility': compatible,
                         'staging': {'storage_kind': storage_kind, 'required_free_bytes': required,
                                     'reserve_bytes': reserve}}
            save('candidate.json', (json.dumps(candidate, sort_keys=True, indent=2) + '\n').encode())
            os.fsync(stage)
            # A held parent prevents path replacement from redirecting publication.
            info = os.stat(temporary, dir_fd=parent, follow_symlinks=False)
            require((info.st_dev, info.st_ino) == (stage_info.st_dev, stage_info.st_ino),
                    'staging directory name changed')
            publish(parent, temporary, output.name)
            published = True
            os.fsync(parent)
            return candidate
        except OSError as error:
            raise PublicationError(error, published) from error
        finally:
            try:
                if not published:
                    # Never recurse through a pathname another process could swap.
                    # Unexpected objects remain for inspection; preserve the original
                    # failure even when cleanup cannot remove such a directory.
                    for name, identity in owned.items():
                        with contextlib.suppress(OSError):
                            ACQUIRE.remove_owned(stage, name, identity)
                    with contextlib.suppress(OSError):
                        info = os.stat(temporary, dir_fd=parent, follow_symlinks=False)
                        if (info.st_dev, info.st_ino) == (stage_info.st_dev, stage_info.st_ino):
                            os.rmdir(temporary, dir_fd=parent)
            finally:
                os.close(stage)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root_image', type=Path)
    parser.add_argument('new_output', type=Path)
    parser.add_argument('--slot', choices=('A', 'B'), required=True)
    parser.add_argument('--payload-manifest', type=Path, required=True)
    parser.add_argument('--manifest-sha256', required=True,
                        help='SHA-256 obtained through an independent trusted channel')
    parser.add_argument('--storage-kind', choices=('ram', 'external'), required=True)
    args = parser.parse_args(argv)
    try:
        candidate = prepare(args.root_image, args.new_output, args.slot, args.payload_manifest,
                            args.manifest_sha256, args.storage_kind)
    except (ValueError, OSError, KeyError, TypeError, AttributeError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'host update preparation: {error}\n')
    print(json.dumps(candidate, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
