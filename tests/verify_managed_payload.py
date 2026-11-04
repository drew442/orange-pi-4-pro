#!/usr/bin/env python3
"""Inspect trusted managed-root regular files without mounting or repairing."""
import gzip
import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys


def digest(stream):
    value = hashlib.sha256()
    size = 0
    for chunk in iter(lambda: stream.read(1024**2), b''):
        value.update(chunk)
        size += len(chunk)
    return {'bytes': size, 'sha256': value.hexdigest()}


def regular(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    stream = os.fdopen(descriptor, 'rb')
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        stream.close()
        raise ValueError('regular files only; devices and symlinks refused')
    return stream


def verify(directory):
    directory = Path(directory)
    with regular(directory / 'manifest.json') as source:
        manifest = json.load(source)
    profile = manifest['management_runtime']
    if profile['profile'] != 'a733-management-v1':
        raise ValueError('unexpected root profile')
    reports = {}
    for slot in ('A', 'B'):
        name = f'root-{slot}.ext4'
        with regular(directory / name) as image:
            expected = manifest['files'][name]
            if digest(image) != expected:
                raise ValueError('raw root image hash/size differs')
            image.seek(1024)
            superblock = image.read(1024)
            if (superblock[56:58] != b'\x53\xef' or struct.unpack_from('<I', superblock, 92)[0] & 4 or
                    not struct.unpack_from('<I', superblock, 100)[0] & 0x1000):
                raise ValueError('root must be read-only ext4 without journal')
            def inspect(command):
                return subprocess.run(['debugfs', '-R', command, f'/proc/self/fd/{image.fileno()}'],
                                      pass_fds=(image.fileno(),), check=True,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout
            marker = json.loads(inspect('cat /usr/lib/orangepi-hypervisor/payload.json'))
            if marker['slot'] != slot or marker['management_runtime'] != profile:
                raise ValueError('root marker/profile differs')
            for path, record in profile['files'].items():
                contents = inspect('cat /' + path)
                if {'bytes': len(contents), 'sha256': hashlib.sha256(contents).hexdigest()} != record:
                    raise ValueError(f'installed runtime differs: {path}')
            shadow = inspect('cat /etc/shadow').decode()
            root_password = next(line.split(':')[1] for line in shadow.splitlines() if line.startswith('root:'))
            if not root_password.startswith(('!', '*')):
                raise ValueError('root password is not locked')
            machine_metadata = inspect('stat /etc/machine-id').decode()
            if 'Type: regular' not in machine_metadata:
                raise ValueError('root machine-id must exist as a regular file')
            if inspect('cat /etc/machine-id'):
                raise ValueError('root image embeds machine identity')
            listing = inspect('ls -p /etc/ssh').decode()
            if 'ssh_host_' in listing:
                raise ValueError('root image embeds SSH host keys')
        with regular(directory / (name + '.gz')) as compressed:
            compressed_record = digest(compressed)
            if compressed_record != manifest['files'][name + '.gz']:
                raise ValueError('compressed root hash/size differs')
            compressed.seek(0)
            with gzip.GzipFile(fileobj=compressed) as expanded:
                if digest(expanded) != expected:
                    raise ValueError('compressed expansion differs from raw root')
        reports[slot] = {'raw': expected, 'compressed': compressed_record,
                         'runtime_files_match': True, 'read_only_no_journal': True,
                         'root_password_locked': True, 'machine_id_empty': True,
                         'host_keys_absent': True, 'compressed_expansion_matches': True}
    return {'classification': 'managed regular-file root checks; no board boot or KVM acceptance',
            'management_runtime': profile, 'slots': reports}


if __name__ == '__main__':
    if len(sys.argv) != 2:
        sys.exit(f'Usage: {sys.argv[0]} TRUSTED_MANAGED_PAYLOAD_DIRECTORY')
    print(json.dumps(verify(sys.argv[1]), indent=2))
