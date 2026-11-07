#!/usr/bin/env python3
"""Prepare metadata for one of two reviewed first-KVM deployments, on codex only.

No device access, image hashing/decoding, guest execution or installed-runtime change.
The recovery checksum file must still be checked before activation.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import stat
import subprocess

REPO = Path(__file__).resolve().parents[1]
RESERVE = 268435456
NATIVE_STORAGE = ['ext4', '/dev/mapper/ubuntu--vg-ubuntu--lv']
OLD_ROOT = 'tmp/final-component-manifests-20261106.json'
NEW_ROOT = 'out/host-roots-timesync-repeat-01-20261107/work/project/out/host-payload-timesync-repeat-01/manifest.json'
PROFILES = {
    'installed-20261106': {
        'root': (OLD_ROOT, 97773, '4d5eddf062a51a946b660a74c0268a6d89b72430e06722860af32e3397e2b103'),
        'root_sha': 'd89bcf206906c6044052c34874ee3e0592c3ccdf2f8a840681d362a55e65db67',
        'count': 39, 'state': '00fd936c-49fb-4fea-87c8-928b9c1599e0',
        'guest': '3dd7373a-c4b2-42e2-867a-bc5f39039285',
        'package': '682f6ae0293cc8ef1a1e4780e30140fd557ede4de893328c8fc0dec5582dba7d',
        'bundle': 'out/deployment-delivery-20261106/public',
        'manifest': (13613, 'bdd3c8f103e08fc1b759ca97e631ee20255d43aefe42abcce5361614a41b041e'),
        'wire': (547077631, '4d1145c5ff8689c1e8ca847e39551eb36f54e703a2843f35fd0ce7ec7574b526'),
        'raw': (256060514304, '74f3c6f9c66df8e125148b7a7d06ebc521d1c0fd5ae8e53de4a90c740e5224fc'),
    },
    'candidate-repeat-20261107': {
        'root': (NEW_ROOT, 32447, 'd15bacd088a9c2de7a83962ad90e2141739cbee1803437c43c4b7fa36c36f1a3'),
        'root_sha': 'd15bacd088a9c2de7a83962ad90e2141739cbee1803437c43c4b7fa36c36f1a3',
        'count': 40, 'state': 'feaaee34-d782-4c18-bd04-1029540ab26a',
        'guest': '5f980211-35f5-4436-98c6-6a750eb1fc3c',
        'package': 'd4ba40ab4a06835d7df062ad4553aa5744efd72db167fe4dc50753969dc2d09d',
        'bundle': 'out/nvme-repeat-candidate-retained-20261107',
        'manifest': (10481, 'c0f036dcc5d300a24785ebaac4c438bb0aef7cf1b8b073dc97a2d15a7896f563'),
        'wire': (545633737, '4b6cbe6eb16cc3d3cd236b0d8701908408d38e5993d5aca5ffb4882708766df0'),
        'raw': (256060514304, 'd8f842f806c8ea4257b6efaf45d080b95e42a1f06bbde0a35f07d67c7416c7d9'),
        'proof': ('out/nvme-timesync-repeat-01-20261107/stream-verification.json', 5322,
                  '683d48fadbbf7481ccc5d0973923e37640af730f95e75179b81dba3babd7e63d'),
    },
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def physical(path):
    require(path.is_absolute() and '..' not in path.parts, 'absolute physical path required')
    for part in reversed((path, *path.parents)):
        mode = part.lstat().st_mode
        require(not stat.S_ISLNK(mode), f'symlink refused: {part}')
    return path


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate JSON key')
        result[key] = value
    return result


def read_record(relative, size, digest):
    path = physical(REPO / relative)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_size == size, f'record type/size: {relative}')
        require(size <= 1048576, 'record exceeds metadata bound')
        raw = stream.read(size + 1)
        after = os.fstat(stream.fileno())
    identity = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    require(identity(before) == identity(after) and len(raw) == size, 'record changed during read')
    require(hashlib.sha256(raw).hexdigest() == digest, f'record hash: {relative}')
    return json.loads(raw, object_pairs_hook=unique)


def record(pair):
    return {'bytes': pair[0], 'sha256': pair[1]}


def validate_tools(files, count):
    require(type(files) is dict and len(files) == count, 'tool count mismatch')
    lines = []
    for name, item in sorted(files.items()):
        require(type(name) is str and re.fullmatch(r'usr/lib/a733-tools/[A-Za-z0-9_./-]+', name), 'unsafe tool name')
        require(all(x not in ('', '.', '..') for x in name.split('/')) and str(PurePosixPath(name)) == name, 'unsafe tool path')
        require(type(item) is dict and type(item.get('bytes')) is int and item['bytes'] > 0, 'tool size')
        require(type(item.get('sha256')) is str and re.fullmatch('[0-9a-f]{64}', item['sha256']), 'tool hash')
        lines.append(f"{item['sha256']}  /{name}\n")
    return ''.join(lines)


def prepare_data(profile):
    require(profile in PROFILES, 'unknown profile')
    p = PROFILES[profile]
    root = read_record(*p['root'])
    if profile == 'installed-20261106':
        component = root['host-payload-deployment-20261106-02']
        require(component['manifest_sha256'] == p['root_sha'], 'root binding mismatch')
        root = component['manifest']
    require(root['package_state']['manifest']['sha256'] == p['package'], 'package profile mismatch')
    tools = validate_tools(root['maintenance_runtime']['files'], p['count'])
    manifest_relative = p['bundle'] + '/manifest.json'
    manifest = read_record(manifest_relative, *p['manifest'])
    require(manifest['state_uuid'] == p['state'], 'state profile mismatch')
    require(manifest['filesystem_uuids']['state'] == p['state'] and manifest['filesystem_uuids']['guests'] == p['guest'], 'filesystem profile mismatch')
    require(manifest['inputs']['roots_manifest']['sha256'] == p['root_sha'], 'root/installation mismatch')
    require(manifest['files']['nvme.img'] == record(p['raw']) and manifest['files']['nvme.img.gz'] == record(p['wire']), 'recovery profile mismatch')
    if 'proof' in p:
        proof = read_record(*p['proof'])
        require(proof['success'] is True and proof['state'] == 'complete' and proof['gzip_expansion_independent_verification'] is True, 'incomplete stream proof')
        require(proof['compressed_wire'] == record(p['wire']) and proof['decoded_image'] == record(p['raw']), 'stream profile mismatch')
        require(manifest['distribution']['verification']['stream_report'] == record(p['proof'][1:]), 'stream binding mismatch')
    else:
        require(manifest['distribution']['verification'] == 'all logical source bytes hashed; fsynced encoded bytes hashed on readback; full bounded gzip expansion hashed', 'missing full-stream proof')
        require(manifest['distribution']['source_image']['record'] == record(p['raw']), 'source-image mismatch')
    env = {
        'PROFILE': profile, 'STATE_UUID': p['state'], 'GUEST_UUID': p['guest'],
        'PACKAGE_STATE_SHA': p['package'], 'TOOL_COUNT': str(p['count']),
        'E': '/root/kvm-first-' + profile, 'REMOTE_E': '/root/kvm-first-' + profile,
        'LOCAL_E': str(REPO / 'out' / ('kvm-first-' + profile)),
        'RECOVERY_DIR': str(REPO / p['bundle']),
        'RECOVERY_MANIFEST': str(REPO / manifest_relative),
        'RECOVERY_MANIFEST_BYTES': str(p['manifest'][0]), 'RECOVERY_MANIFEST_SHA': p['manifest'][1],
        'RECOVERY_WIRE_BYTES': str(p['wire'][0]), 'RECOVERY_WIRE_SHA': p['wire'][1],
        'RECOVERY_RAW_BYTES': str(p['raw'][0]), 'RECOVERY_RAW_SHA': p['raw'][1],
    }
    summary = {'schema': 1, 'profile': profile, 'values': env, 'trusted_root_record': {'path': p['root'][0], **record(p['root'][1:])},
               'recovery_bytes_verified_now': False, 'hardware_acceptance': False}
    if 'proof' in p:
        summary['stream_proof'] = {'path': p['proof'][0], **record(p['proof'][1:])}
    else:
        summary['stream_proof'] = {'path': manifest_relative, 'field': 'distribution.verification', **record(p['manifest'])}
    return {
        'profile.env': ''.join(f'readonly {k}={shlex.quote(v)}\nexport {k}\n' for k, v in env.items()),
        'profile.json': json.dumps(summary, indent=2, sort_keys=True) + '\n',
        'installed-tools.sha256': tools,
        'recovery-bundle.sha256': f"{p['wire'][1]}  nvme.img.gz\n{p['manifest'][1]}  manifest.json\n",
    }


def write_output(output, files):
    require(output.is_absolute() and '..' not in output.parts, 'absolute new output required')
    # Restrict writes to one new directory directly under this checkout's out/.
    require(output.parent == REPO / 'out' and re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]*', output.name), 'output must be a new direct child of repository out/')
    physical(output.parent)
    storage = subprocess.check_output(['findmnt', '-n', '-o', 'FSTYPE,SOURCE', '--target', str(output.parent)], text=True).split()
    require(storage == NATIVE_STORAGE, 'pinned native fixed-disk repository storage required')
    available = os.statvfs(output.parent)
    require(available.f_bavail * available.f_frsize >= RESERVE + 1048576, '256 MiB reserve required')
    output.mkdir(mode=0o700)
    checksums = ''.join(f'{hashlib.sha256(value.encode("ascii")).hexdigest()}  {name}\n' for name, value in files.items())
    for name, value in {**files, 'SHA256SUMS': checksums}.items():
        fd = os.open(output / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(value.encode('ascii'))
            stream.flush()
            os.fsync(stream.fileno())
    fd = os.open(output, os.O_DIRECTORY | os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=tuple(PROFILES), default='installed-20261106')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        write_output(args.output, prepare_data(args.profile))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        parser.exit(1, f'STOP: {error}\n')
    print(f'Prepared {args.profile} metadata at {args.output}; recovery bytes still require checksum verification.')
    for path in sorted(args.output.iterdir()):
        print(f'{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}')


if __name__ == '__main__':
    main()
