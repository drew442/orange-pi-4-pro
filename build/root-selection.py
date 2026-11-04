#!/usr/bin/env python3
"""Prepare a root selector from a pinned regular image; no installed-state CLI.

The atomic publication helper is for trusted staging/integration callers. It
does not identify or authorize a mounted NVMe target or implement an updater.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import tempfile

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('selection_initramfs', REPO / 'build/host-initramfs.py')
INIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INIT)
FIELDS = ('SCHEMA', 'SLOT', 'ROOT_UUID', 'ROOT_MARKER_SHA256', 'KERNEL_RELEASE')


def parse(data):
    if not isinstance(data, bytes) or not 1 <= len(data) <= 4096 or not data.endswith(b'\n'):
        raise ValueError('selector must be bounded newline-terminated bytes')
    if b'\0' in data or b'\r' in data:
        raise ValueError('selector contains forbidden bytes')
    values = {}
    for line in data[:-1].decode('ascii').split('\n'):
        key, separator, value = line.partition('=')
        if not separator or key not in FIELDS or key in values:
            raise ValueError('unknown, malformed or duplicate selector field')
        values[key] = value
    if (set(values) != set(FIELDS) or values['SCHEMA'] != '1' or
            values['SLOT'] not in INIT.PAYLOAD.SLOT_UUIDS or
            values['ROOT_UUID'] != INIT.PAYLOAD.SLOT_UUIDS[values['SLOT']] or
            not re.fullmatch('[0-9a-f]{64}', values['ROOT_MARKER_SHA256']) or
            values['KERNEL_RELEASE'] != INIT.PAYLOAD.KERNEL_RELEASE):
        raise ValueError('incompatible selector slot, UUID, marker or kernel')
    return values


def serialize(values):
    if set(values) != set(FIELDS):
        raise ValueError('selector must contain exactly its defined fields')
    data = ''.join(f'{key}={values[key]}\n' for key in FIELDS).encode('ascii')
    parse(data)
    return data


class PublicationError(OSError):
    def __init__(self, error, published):
        super().__init__(f'selector publication failed; published={published}: {error}')
        self.published = published


def atomic_store(directory, data):
    """Publish a complete selector with fsync/rename/fsync, without target policy.

    Caller must validate/authorize the directory and serialize update writers.
    A post-rename failure means the new record is visible but durability unknown.
    """
    parse(data)
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    temporary = '.selected-' + secrets.token_hex(12)
    published = False
    created = False
    try:
        info = os.fstat(descriptor)
        if info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise ValueError('selector directory must be owned by caller without group/world writes')
        target = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600,
                         dir_fd=descriptor)
        created = True
        with os.fdopen(target, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, 'selected.conf', src_dir_fd=descriptor, dst_dir_fd=descriptor)
        published = True
        os.fsync(descriptor)
    except OSError as error:
        raise PublicationError(error, published) from error
    finally:
        if created and not published:
            os.unlink(temporary, dir_fd=descriptor)
        os.close(descriptor)


def capture_root(image, expected, destination):
    """Capture and authenticate the bytes later parsed, rather than a live inode."""
    source_fd = os.open(image, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(source_fd, 'rb') as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise ValueError('root image must be a regular file')
        if os.fstat(source.fileno()).st_size != expected['bytes']:
            raise ValueError('root image size mismatch')
        size, hashed = 0, hashlib.sha256()
        with destination.open('xb') as target:
            for data in iter(lambda: source.read(1024**2), b''):
                size += len(data)
                if size > expected['bytes']:
                    raise ValueError('root image grew during capture')
                hashed.update(data)
                if data.strip(b'\0'):
                    target.write(data)
                else:
                    target.seek(len(data), os.SEEK_CUR)
            target.truncate(size)
            target.flush()
            os.fsync(target.fileno())
    if {'bytes': size, 'sha256': hashed.hexdigest()} != expected:
        raise ValueError('captured root hash/size differs from pinned image')
    destination.chmod(0o400)


def prepare(image, slot, payload_manifest):
    descriptor = os.open(payload_manifest, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise ValueError('trusted payload manifest must be a regular file')
        data = source.read(1024**2 + 1)
    if len(data) > 1024**2:
        raise ValueError('payload manifest exceeds size bound')
    roots = json.loads(data)
    profile = roots.get('management_runtime')
    if (roots['slot_bytes'] != INIT.PAYLOAD.SLOT_BYTES or
            roots['slot_uuids'] != INIT.PAYLOAD.SLOT_UUIDS or
            roots['kernel_release'] != INIT.PAYLOAD.KERNEL_RELEASE or
            not isinstance(profile, dict) or profile.get('profile') != 'a733-management-v1' or
            set(profile.get('files', {})) != {destination for destination, _ in INIT.PAYLOAD.MANAGEMENT_FILES.values()}):
        raise ValueError('payload manifest lacks compatible managed-host profile')
    diag = json.loads((REPO / 'docs/evidence/diagnostic-20261104.json').read_text())['files']
    expected = roots['files'][f'root-{slot}.ext4']
    if expected['bytes'] != INIT.PAYLOAD.SLOT_BYTES:
        raise ValueError('root image must fill the fixed slot byte budget')
    parent = REPO / 'out'
    if parent.is_symlink():
        raise ValueError('symlink staging parent refused')
    parent.mkdir(exist_ok=True)
    if shutil.disk_usage(parent).free < expected['bytes'] + 1024**3:
        raise ValueError('insufficient fixed builder disk for private root capture')
    with tempfile.TemporaryDirectory(prefix='.root-selection-', dir=parent) as temporary:
        snapshot = Path(temporary) / 'root.ext4'
        capture_root(image, expected, snapshot)
        with INIT.read_pinned(snapshot, expected) as stream:
            marker, metadata = INIT.root_marker(stream, slot)
    for name in ('Image', 'board.dtb', 'kernel.config', 'initramfs.cpio.gz'):
        if metadata['sources'][name] != diag[name]:
            raise ValueError('root and immutable boot stack differ')
    if metadata.get('management_runtime') != roots['management_runtime']:
        raise ValueError('root marker management runtime differs from trusted manifest')
    return serialize({'SCHEMA': '1', 'SLOT': slot, 'ROOT_UUID': INIT.PAYLOAD.SLOT_UUIDS[slot],
                      'ROOT_MARKER_SHA256': hashlib.sha256(marker).hexdigest(),
                      'KERNEL_RELEASE': INIT.PAYLOAD.KERNEL_RELEASE})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root_image', type=Path)
    parser.add_argument('new_output', type=Path)
    parser.add_argument('--slot', choices=('A', 'B'), required=True)
    parser.add_argument('--payload-manifest', type=Path, required=True,
                        help='trusted release/build manifest containing the full root image hash')
    args = parser.parse_args()
    try:
        output = Path(os.path.abspath(args.new_output))
        if output.parent != REPO / 'out' or output.parent.is_symlink():
            raise ValueError('output must be a new regular file directly below repo/out/')
        data = prepare(args.root_image, args.slot, args.payload_manifest)
        output.parent.mkdir(exist_ok=True)
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
        print(json.dumps({'output': str(output), 'selector': parse(data),
                          'sha256': hashlib.sha256(data).hexdigest()}, indent=2))
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'root selection: {error}\n')
