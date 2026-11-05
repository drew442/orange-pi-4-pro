#!/usr/bin/env python3
"""Read-only verifier for a regular-file state payload and trusted manifest.

Rejects block-device inputs and never mounts or repairs. The manifest must come from the
trusted build; this is an integrity/content check, not signature verification.
"""
import base64
import hashlib
import gzip
import json
import os
from pathlib import Path
import re
import stat
import struct
import subprocess
import sys
import uuid


def administration_fingerprints(data):
    # Independent RFC4253 readback parser: do not trust the build's key parser.
    if not data or len(data) > 16384 or not data.endswith(b'\n'):
        raise ValueError('unexpected authorized keys size or terminator')
    lines = data[:-1].split(b'\n')
    if not 1 <= len(lines) <= 16:
        raise ValueError('unexpected authorized keys count')
    fingerprints = []
    for line in lines:
        fields = line.split(b' ')
        if len(fields) != 2 or fields[0] not in (b'ssh-ed25519', b'ssh-rsa'):
            raise ValueError('unexpected authorized keys format')
        blob = base64.b64decode(fields[1], validate=True)
        if base64.b64encode(blob) != fields[1]:
            raise ValueError('noncanonical public key encoding')
        parts = []
        remaining = blob
        while remaining:
            if len(remaining) < 4:
                raise ValueError('truncated key field header')
            size = int.from_bytes(remaining[:4], 'big')
            if size > len(remaining) - 4:
                raise ValueError('truncated key field')
            parts.append(remaining[4:4+size])
            remaining = remaining[4+size:]
        if not parts or parts[0] != fields[0]:
            raise ValueError('public key algorithm mismatch')
        if fields[0] == b'ssh-ed25519':
            if len(parts) != 2 or len(parts[1]) != 32:
                raise ValueError('unexpected Ed25519 key')
        else:
            if len(parts) != 3:
                raise ValueError('unexpected RSA key')
            for value in parts[1:]:
                if (not value or value[0] >= 128 or
                        (value[0] == 0 and (len(value) < 2 or value[1] < 128))):
                    raise ValueError('noncanonical RSA mpint')
            exponent, modulus = (int.from_bytes(value, 'big') for value in parts[1:])
            if (not 2048 <= modulus.bit_length() <= 16384 or not modulus % 2 or
                    not 3 <= exponent <= 0xffffffff or not exponent % 2 or exponent >= modulus):
                raise ValueError('unexpected RSA parameters')
        fingerprints.append('SHA256:' + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip('='))
    if len(fingerprints) != len(set(fingerprints)):
        raise ValueError('duplicate administrator key')
    return fingerprints


def digest(descriptor):
    value = hashlib.sha256()
    offset = 0
    while True:
        data = os.pread(descriptor, 1024**2, offset)
        if not data:
            return {'bytes': offset, 'sha256': value.hexdigest()}
        value.update(data)
        offset += len(data)


def read_json(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('metadata must be a regular non-symlink file')
        data = stream.read(16 * 1024**2 + 1)
    if len(data) > 16 * 1024**2:
        raise ValueError('metadata exceeds bound')
    return json.loads(data), {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def verify(image, manifest_path):
    manifest, _ = read_json(manifest_path)
    initial = os.lstat(image)
    if not stat.S_ISREG(initial.st_mode):
        raise ValueError('image must be a regular non-symlink file')
    descriptor = os.open(image, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(descriptor)
        if (not stat.S_ISREG(opened.st_mode) or
                (opened.st_dev, opened.st_ino) != (initial.st_dev, initial.st_ino)):
            raise ValueError('image must be a regular non-symlink file')
        if not 16 * 1024**2 <= os.fstat(descriptor).st_size <= 8 * 1024**3:
            raise ValueError('unsupported state image size')
        before = digest(descriptor)
        if before != manifest['files']['state.ext4']:
            raise ValueError('state image hash/size mismatch')
        superblock = os.pread(descriptor, 1024, 1024)
        if (len(superblock) != 1024 or superblock[56:58] != b'\x53\xef' or
                not struct.unpack_from('<I', superblock, 92)[0] & 4 or
                struct.unpack_from('<I', superblock, 100)[0] & 0x1000):
            raise ValueError('expected writable journaled ext4 state')
        state_uuid = str(uuid.UUID(bytes=superblock[104:120]))
        if state_uuid != manifest['state_uuid']:
            raise ValueError('superblock UUID differs from manifest')

        def debugfs(command):
            result = subprocess.run(['debugfs', '-R', command, f'/proc/self/fd/{descriptor}'],
                                    pass_fds=(descriptor,), check=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            return result.stdout
        if 'cache_pruning' in manifest:
            for cache in ('/var/cache/apt/pkgcache.bin', '/var/cache/apt/srcpkgcache.bin',
                          '/var/cache/ldconfig/aux-cache'):
                if b'Inode:' in debugfs('stat ' + cache):
                    raise ValueError('disposable cache remains in state image')

        def metadata(path, kind, mode):
            value = debugfs('stat ' + path).decode()
            attributes = re.search(r'Type:\s+(\S+)\s+Mode:\s+0?([0-7]{3,4})\b', value)
            owner = re.search(r'User:\s+(\d+)\s+Group:\s+(\d+)', value)
            if (not attributes or not owner or attributes.group(1) != kind or
                    int(attributes.group(2), 8) != mode or owner.groups() != ('0', '0')):
                raise ValueError(f'unexpected filesystem metadata: {path}')

        for path, mode in (('/', 0o755), ('/lost+found', 0o700), ('/var', 0o755),
                           ('/root', 0o700), ('/root/.ssh', 0o700), ('/home', 0o755),
                           ('/identity', 0o700), ('/identity/ssh', 0o700), ('/config', 0o700), ('/boot', 0o700)):
            metadata(path, 'directory', mode)
        for path in ('/.a733-state', '/identity/machine-id', '/config/management.json', '/root/.ssh/authorized_keys', '/boot/selected.conf'):
            metadata(path, 'regular', 0o600)
        if debugfs('cat /.a733-state') != f'a733-state-v1\n{state_uuid}\n'.encode():
            raise ValueError('state marker mismatch')
        identity = debugfs('cat /identity/machine-id')
        if (identity != (manifest['machine_id'] + '\n').encode() or
                not re.fullmatch(rb'[0-9a-f]{32}\n', identity) or identity == b'0' * 32 + b'\n'):
            raise ValueError('machine-id mismatch')
        if json.loads(debugfs('cat /config/management.json')) != manifest['management']:
            raise ValueError('management configuration mismatch')
        selection = debugfs('cat /boot/selected.conf')
        selection_fields = ('SCHEMA', 'SLOT', 'ROOT_UUID', 'ROOT_MARKER_SHA256', 'KERNEL_RELEASE')
        expected_selection = ''.join(f'{key}={manifest["boot_selection"][key]}\n' for key in selection_fields).encode()
        if selection != expected_selection or len(manifest['boot_selection']) != 5:
            raise ValueError('boot selection differs from manifest')
        key = debugfs('cat /root/.ssh/authorized_keys')
        fingerprints = administration_fingerprints(key)
        expected = manifest.get('admin_public_key_fingerprints', [manifest.get('admin_public_key_fingerprint')])
        if (fingerprints != expected or ('admin_public_key_fingerprint' in manifest and
                fingerprints != [manifest['admin_public_key_fingerprint']])):
            raise ValueError('operator public keys differ from build')
        listing = debugfs('ls -p /identity/ssh').decode()
        names = []
        for line in listing.splitlines():
            if line.strip():
                parts = line.split('/')
                if len(parts) < 7:
                    raise ValueError('unexpected debugfs directory output')
                names.append(parts[5])
        if sorted(names) != ['.', '..']:
            raise ValueError('state image must not embed host private keys')
        # Verify a substantial package database file against the staging BOM,
        # independently of the whole-image digest and identity/config checks.
        bom, bom_record = read_json(Path(manifest_path).parent / 'staging-contents.json')
        if bom_record != manifest['files']['staging-contents.json']:
            raise ValueError('staging BOM hash/size mismatch')
        expected = bom['var/lib/dpkg/status']
        package_status = debugfs('cat /var/lib/dpkg/status')
        if {'bytes': len(package_status), 'sha256': hashlib.sha256(package_status).hexdigest()} != {
                name: expected[name] for name in ('bytes', 'sha256')}:
            raise ValueError('package database differs from authenticated staging')
        if digest(descriptor) != before:
            raise ValueError('image changed during read-only verification')
        compressed_path = Path(manifest_path).parent / 'state.ext4.gz'
        compressed_fd = os.open(compressed_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(compressed_fd, 'rb') as compressed:
            if not stat.S_ISREG(os.fstat(compressed.fileno()).st_mode):
                raise ValueError('compressed image must be regular')
            if digest(compressed.fileno()) != manifest['files']['state.ext4.gz']:
                raise ValueError('compressed image hash/size mismatch')
            expanded_size, expanded_hash = 0, hashlib.sha256()
            with gzip.GzipFile(fileobj=compressed, mode='rb') as expanded:
                for data in iter(lambda: expanded.read(1024**2), b''):
                    expanded_size += len(data)
                    if expanded_size > before['bytes']:
                        raise ValueError('compressed expansion exceeds raw image size')
                    expanded_hash.update(data)
            if {'bytes': expanded_size, 'sha256': expanded_hash.hexdigest()} != before:
                raise ValueError('compressed expansion differs from raw image')
        return {'classification': 'regular-file state verification; no hardware acceptance',
                'image': before, 'state_uuid': state_uuid, 'machine_id': manifest['machine_id'],
                'journal': True, 'filesystem_read_only_feature': False,
                'filesystem_metadata': 'checked root/lost+found and identity/config/key directories/files',
                'management_config_matches': True, 'boot_selection_matches': True,
                'operator_public_key_fingerprints': fingerprints,
                **({'operator_public_key_fingerprint': fingerprints[0]} if len(fingerprints) == 1 else {}),
                'host_key_directory_empty': True, 'package_database_matches_staging': True,
                'compressed_hash_and_expansion_match': True, 'image_unchanged': True}
    finally:
        os.close(descriptor)


if __name__ == '__main__':
    if len(sys.argv) != 3:
        sys.exit(f'Usage: {sys.argv[0]} STATE_IMAGE TRUSTED_BUILD_MANIFEST')
    print(json.dumps(verify(sys.argv[1], sys.argv[2]), indent=2))
