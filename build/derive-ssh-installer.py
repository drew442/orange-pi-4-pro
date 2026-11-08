#!/usr/bin/env python3
"""Derive a private same-board SSH RAM initramfs; never execute target programs."""
import argparse
import base64
import gzip
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import tarfile

REPO = Path(__file__).resolve().parents[1]


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, REPO / filename)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


RUNTIME = module('ssh_installer_runtime', 'build/installer-runtime.py')
INIT = module('ssh_installer_newc', 'build/host-initramfs.py')
CPIO = module('ssh_installer_cpio', 'build/repack-guest-initramfs.py')
TOOLS = module('ssh_installer_tools', 'build/maintenance-tools.py')
COMMON = module('ssh_installer_keys', 'configs/host_config.py')
CPIO.MAX_RAW = 256 * 1024**2
BASE = {'bytes': 26977583, 'sha256': '55cfb5ed7b7e7f423ad1ef528e2d926caf29159730ff6b696e0750a8e906f2fd'}
ROOTFS = {'bytes': 169852145, 'sha256': '1967e76c0c831c1c0bab432e7116457d4372c15b413090c054f8958b72dcb392'}
IDENTITY = {'bytes': 10240, 'sha256': 'ae3930cec481912a164361e11c926ca02d81561a49c0fc49b70514dd19d3d06e'}
IDENTITY_MEMBER = 'identity/ssh/ssh_host_ed25519_key'
HOST_FINGERPRINT = 'SHA256:Epfegy9GsJ6ng4/0EH3dZmir7beQj5uYTnIbE4JPfEI'
OPERATOR_FINGERPRINTS = {'SHA256:WVU8wsBgfy5mGFNAZMdrvf+a3cTYo85B8llJlHIhUKA',
                         'SHA256:Dw9WPpqQ741skiDShdwAP3qXQtpgxgbUSlCp2hmod7k'}
MAX_RAW = 256 * 1024**2
MAX_PACKED = 64 * 1024**2 - 64
MUTATIONS = {'init', 'etc/passwd', 'etc/group'}
PROVENANCE = 'usr/share/a733-ssh-installer/provenance/'
RECIPE_FILES = ('build/derive-ssh-installer.py', 'build/installer-runtime.py',
                'build/repack-guest-initramfs.py', 'build/host-initramfs.py',
                'build/host-state.py', 'build/assemble-host.py', 'build/host-payload.py',
                'build/root-selection.py', 'build/host-storage-tools.py',
                'configs/package_state.py', 'configs/host_config.py', 'build/maintenance-tools.py',
                'configs/installer-ssh-init', 'configs/installer-sshd_config',
                'configs/installer-sshd.pam', 'configs/builder-packages.lock.tsv',
                'configs/host-userspace-packages.lock.tsv',
                'configs/host-userspace-package-hashes.lock.tsv',
                'docs/evidence/host-userspace-20261104.json')


def read_input(path, limit, expected=None):
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path or '..' in path.parts:
        raise ValueError('input must be an absolute canonical nonsymlink path')
    data = RUNTIME.STATE.bounded_input(path, limit)
    if expected is not None and RUNTIME.record(data) != expected:
        raise ValueError('input differs from pinned bytes/hash')
    return data


def unpack(data, limit=MAX_RAW):
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('expanded archive exceeds budget')
    return raw


def parse(raw):
    items = CPIO.entries(raw)
    entries = {item['name']: (item['mode'], item['data']) for item in items[:-1]}
    for item in items[:-1]:
        name, (mode, data) = item['name'], entries[item['name']]
        header = raw[item['start']:item['start'] + 110]
        fields = [int(header[i:i + 8], 16) for i in range(6, 110, 8)]
        if fields[2:5] != [0, 0, 1] or fields[7:11] != [0, 0, 0, 0] or fields[12]:
            raise ValueError('require root-owned newc without hardlinks/devices/checksum')
        if stat.S_IFMT(mode) not in (stat.S_IFDIR, stat.S_IFREG, stat.S_IFLNK):
            raise ValueError('unsupported CPIO object')
        if stat.S_ISDIR(mode) and data:
            raise ValueError('directory contains payload')
        if stat.S_ISLNK(mode):
            RUNTIME.link_target(name.rpartition('/')[0], data.decode())
        for parent in Path(name).parents:
            if str(parent) in entries and not stat.S_ISDIR(entries[str(parent)][0]):
                raise ValueError('non-directory CPIO ancestor')
    return items, entries


def base_manifest(data, entries):
    manifest = json.loads(data, object_pairs_hook=COMMON.unique_pairs)
    actual = {name: {'mode': mode, **RUNTIME.record(value)} for name, (mode, value) in entries.items()}
    if (manifest.get('schema') != 1 or manifest.get('files', {}).get('initramfs.cpio.gz') != BASE or
            manifest.get('entries') != actual):
        raise ValueError('base manifest does not describe authenticated archive')
    required = {'usr/lib/a733-tools/' + name for name in (*TOOLS.SOURCES, 'package.json')}
    if len(TOOLS.SOURCES) != 39 or not required <= entries.keys():
        raise ValueError('base maintenance package incomplete')
    if entries.get('etc/shadow') != (stat.S_IFREG | 0o600, b'root:!:1:0:99999:7:::\n'):
        raise ValueError('base root must remain locked without account expiry')
    return manifest, sorted(required)


def operator_keys(data):
    canonical, fingerprints = COMMON.validate_public_keys(data)
    if len(fingerprints) != 2 or set(fingerprints) != OPERATOR_FINGERPRINTS:
        raise ValueError('both pinned operator public keys required')
    return canonical, fingerprints


def field(data, offset):
    if len(data) - offset < 4:
        raise ValueError('truncated OpenSSH field')
    size = struct.unpack_from('>I', data, offset)[0]
    offset += 4
    if size > len(data) - offset:
        raise ValueError('truncated OpenSSH field')
    return data[offset:offset + size], offset + size


def host_public(private):
    """Check unencrypted OpenSSH framing and public copies; ARM ssh-keygen is a gate."""
    lines = private.splitlines()
    if len(lines) < 3 or lines[0] != b'-----BEGIN OPENSSH PRIVATE KEY-----' or lines[-1] != b'-----END OPENSSH PRIVATE KEY-----':
        raise ValueError('require OpenSSH private key')
    try:
        data = base64.b64decode(b''.join(lines[1:-1]), validate=True)
    except ValueError as error:
        raise ValueError('invalid OpenSSH encoding') from error
    magic = b'openssh-key-v1\0'
    if not data.startswith(magic):
        raise ValueError('invalid OpenSSH magic')
    offset = len(magic)
    cipher, offset = field(data, offset)
    kdf, offset = field(data, offset)
    options, offset = field(data, offset)
    if cipher != b'none' or kdf != b'none' or options or data[offset:offset + 4] != b'\0\0\0\1':
        raise ValueError('require one unencrypted host key')
    public, offset = field(data, offset + 4)
    secret, offset = field(data, offset)
    if offset != len(data) or len(secret) < 8 or secret[:4] != secret[4:8]:
        raise ValueError('invalid OpenSSH private block')
    algorithm, cursor = field(secret, 8)
    pub, cursor = field(secret, cursor)
    key, cursor = field(secret, cursor)
    _, cursor = field(secret, cursor)  # comment stays private
    if algorithm != b'ssh-ed25519' or len(pub) != 32 or len(key) != 64 or key[32:] != pub:
        raise ValueError('require coherent ed25519 key fields')
    if secret[cursor:] != bytes(range(1, len(secret) - cursor + 1)):
        raise ValueError('invalid OpenSSH padding')
    expected_public = struct.pack('>I', len(algorithm)) + algorithm + struct.pack('>I', 32) + pub
    if public != expected_public:
        raise ValueError('OpenSSH public copies differ')
    canonical, fingerprint = COMMON.validate_public_key(b'ssh-ed25519 ' + base64.b64encode(public))
    if fingerprint != HOST_FINGERPRINT:
        raise ValueError('host key public fingerprint differs from retained trust')
    return canonical


def identity(data):
    member = IDENTITY_MEMBER
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:') as archive:
        namespace = RUNTIME.Namespace(archive)
        member = RUNTIME.relative(member)
        if member not in namespace.entries:
            raise ValueError('selected host identity member absent')
        info = namespace.entries[member]
        if not info.isfile() or info.uid != 0 or info.gid != 0 or info.mode != 0o600 or info.size > 16384:
            raise ValueError('host key must be a bounded root-owned 0600 regular member')
        resolved, private, _ = namespace.read(member)
        if resolved != member:
            raise ValueError('host identity alias refused')
        _, public, _ = namespace.read(member + '.pub')
        canonical, fingerprint = COMMON.validate_public_key(public)
        if fingerprint != HOST_FINGERPRINT or canonical != host_public(private):
            raise ValueError('retained public host key differs from private public fields')
    return private, canonical


def policy():
    return {'executables': ['usr/sbin/sshd', 'usr/bin/ssh-keygen'],
            'required_trees': [], 'optional_trees': [],
            'required_data': ['usr/lib/aarch64-linux-gnu/security/' + name for name in
                              ('pam_deny.so', 'pam_unix.so', 'pam_permit.so')] +
                             ['lib/aarch64-linux-gnu/libnss_files.so.2',
                              'lib/aarch64-linux-gnu/libnss_dns.so.2'],
            'optional_data': [], 'optional_libraries': [],
            'library_directories': ['lib/aarch64-linux-gnu', 'usr/lib/aarch64-linux-gnu'],
            'loader': 'lib/ld-linux-aarch64.so.1', 'symlinks': {}}


def merge(raw, additions):
    """Retain unchanged CPIO records byte-for-byte, including their metadata."""
    items, original = parse(raw)
    final = dict(original)
    mutations = {}
    added = {}
    for name, value in additions.items():
        RUNTIME.relative(name)
        if name in final and value != final[name]:
            if name not in MUTATIONS:
                raise ValueError(f'addition would mutate preserved base entry: {name}')
            mutations[name] = {'before': {'mode': final[name][0], **RUNTIME.record(final[name][1])},
                               'after': {'mode': value[0], **RUNTIME.record(value[1])}}
        elif name not in final:
            added[name] = value
        final[name] = value
    # Add parents without following archive aliases, then revalidate the complete namespace.
    for name in list(final):
        for parent in reversed(Path(name).parents):
            key = str(parent)
            if key != '.' and key not in final:
                final[key] = added[key] = (stat.S_IFDIR | 0o755, b'')
    chunks = []
    preserved = []
    for item in items[:-1]:
        name = item['name']
        if name in mutations:
            chunks.append(single_record(name, final[name]))
        else:
            chunks.append(raw[item['start']:item['end']])
            preserved.append(name)
    chunks.extend(single_record(name, value) for name, value in sorted(added.items()))
    trailer = INIT.newc({})
    updated = b''.join(chunks) + trailer
    parse(updated)
    if len(updated) > MAX_RAW:
        raise ValueError('expanded archive exceeds 256 MiB')
    return updated, final, mutations, preserved


def single_record(name, value):
    packed = INIT.newc({name: value})
    # newc's deterministic first record length, independent of any init requirement.
    header_size = (110 + len(name.encode()) + 1 + 3) & ~3
    data_size = (len(value[1]) + 3) & ~3
    return packed[:header_size + data_size]


def environment(output):
    if (not output.is_absolute() or output.parent != REPO / 'out' or
            not re.fullmatch(r'ssh-installer-[A-Za-z0-9][A-Za-z0-9._-]*', output.name) or
            output.exists() or output.is_symlink() or output.parent.resolve() != output.parent):
        raise ValueError('require fresh absolute repo/out/ssh-installer-NAME')
    release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    if release.get('ID', '').strip('"') != 'ubuntu' or release.get('VERSION_ID', '').strip('"') != '22.04':
        raise ValueError('requires prepared Ubuntu 22.04 builder')
    if os.geteuid() != 0:
        raise ValueError('requires root on prepared builder')
    storage = RUNTIME.STATE.validate_workspace_storage(output.parent)
    memory = re.search(r'^MemAvailable:\s+(\d+) kB$', Path('/proc/meminfo').read_text(), re.M)
    if shutil.disk_usage(output.parent).free < 3 * 1024**3 or not memory or int(memory[1]) * 1024 < 1024**3:
        raise ValueError('requires 3 GiB free fixed storage and 1 GiB MemAvailable')
    return storage, RUNTIME.builder_tools()


def derive(base, manifest_path, rootfs, authorized_keys, identity_path, output):
    output = Path(output)
    storage, builder = environment(output)
    os.umask(0o077)
    base_data = read_input(base, BASE['bytes'], BASE)
    receipt = read_input(manifest_path, 16 * 1024**2)
    raw = unpack(base_data)
    _, entries = parse(raw)
    original_manifest, maintenance = base_manifest(receipt, entries)
    keys_data = read_input(authorized_keys, 16384)
    keys, fingerprints = operator_keys(keys_data)
    identity_data = read_input(identity_path, IDENTITY['bytes'], IDENTITY)
    private, public = identity(identity_data)
    additions = {'init': (stat.S_IFREG | 0o755, (REPO / 'configs/installer-ssh-init').read_bytes()),
                 'etc/passwd': (stat.S_IFREG | 0o644, b'root:x:0:0:root:/root:/bin/sh\nsshd:x:100:65534::/var/empty:/bin/false\n'),
                 'etc/group': (stat.S_IFREG | 0o644, b'root:x:0:\nsshd:x:100:\nnogroup:x:65534:\n'),
                 'root/.ssh': (stat.S_IFDIR | 0o700, b''),
                 'root/.ssh/authorized_keys': (stat.S_IFREG | 0o600, keys),
                 'etc/ssh/ssh_host_ed25519_key': (stat.S_IFREG | 0o600, private),
                 'etc/ssh/ssh_host_ed25519_key.pub': (stat.S_IFREG | 0o644, public),
                 'etc/ssh/sshd_config': (stat.S_IFREG | 0o644, (REPO / 'configs/installer-sshd_config').read_bytes()),
                 'etc/pam.d/sshd': (stat.S_IFREG | 0o644, (REPO / 'configs/installer-sshd.pam').read_bytes()),
                 'var/empty': (stat.S_IFDIR | 0o755, b''),
                 'var/log/lastlog': (stat.S_IFREG | 0o644, b''),
                 'run/sshd': (stat.S_IFDIR | 0o755, b''),
                 'dev/pts': (stat.S_IFDIR | 0o755, b''),
                 PROVENANCE + 'base-manifest.json': (stat.S_IFREG | 0o644, receipt)}
    recipes = {}
    for name in RECIPE_FILES:
        value = (REPO / name).read_bytes()
        recipes[name] = RUNTIME.record(value)
        additions[PROVENANCE + name] = (stat.S_IFREG | 0o644, value)
    output.mkdir(mode=0o700)
    captured, unpacked, stage = output / '.rootfs.gz', output / '.rootfs.tar', output / '.stage'
    try:
        # Authenticate an immutable private compressed snapshot before tar parsing.
        with RUNTIME.STATE.regular_input(Path(rootfs)) as source:
            if not Path(rootfs).is_absolute() or Path(rootfs).resolve() != Path(rootfs):
                raise ValueError('rootfs requires absolute canonical nonsymlink path')
            RUNTIME.capture_archive(source, captured, ROOTFS)
        RUNTIME.decompress_archive(captured, unpacked)
        stage.mkdir(mode=0o700)
        with tarfile.open(unpacked, 'r:') as archive:
            files, closure = RUNTIME.stage(RUNTIME.Namespace(archive), stage, policy())
        for name, info in files.items():
            if info['type'] != 'file':
                raise ValueError('unexpected staged alias')
            additions[name] = (stat.S_IFREG | info['mode'], (stage / name).read_bytes())
        updated, final, mutations, preserved = merge(raw, additions)
        for name in maintenance:
            if final[name] != entries[name]:
                raise ValueError('maintenance source changed')
        compressed = gzip.compress(updated, compresslevel=9, mtime=0)
        if len(compressed) >= MAX_PACKED:
            raise ValueError('compressed archive plus wrapper must be below 64 MiB')
        manifest = {'schema': 1, 'profile': 'a733-private-ssh-ram-installer-v1',
                    'classification': 'PRIVATE personalized same-board image; never public distribution',
                    'hardware_acceptance': False, 'base_initramfs': BASE,
                    'base_manifest': RUNTIME.record(receipt), 'base_manifest_path': PROVENANCE + 'base-manifest.json',
                    'base_boot_inputs': original_manifest.get('boot_inputs'),
                    'source_rootfs': ROOTFS, 'ssh_policy': policy(), 'ssh_source_files': files,
                    'elf_closure': closure, 'operator_keys': {'input': RUNTIME.record(keys_data), 'fingerprints': fingerprints},
                    'host_identity': {'archive': RUNTIME.record(identity_data), 'member': IDENTITY_MEMBER,
                                      'public_fingerprint': HOST_FINGERPRINT,
                                      'verification': 'OpenSSH public fields only; ARM ssh-keygen/private signing gate pending'},
                    'management_mac': '10:f4:ae:90:5e:57', 'management_ipv4_sources': 'any',
                    'recipes': recipes, 'builder_tools': builder, 'staging_storage': storage,
                    'preserved_base_entries': preserved, 'mutations': mutations,
                    'added_entries': sorted(set(final) - set(entries)), 'maintenance_preserved': maintenance,
                    'expanded_bytes': len(updated), 'files': {'initramfs.cpio.gz': RUNTIME.record(compressed)},
                    'entries': {name: {'mode': mode, **RUNTIME.record(value)} for name, (mode, value) in sorted(final.items())},
                    'pending_gates': ['Independent archive/ELF/source preservation review',
                                      'Actual ARM sshd/PAM command and PTY, bad-key/password/forwarding rejection',
                                      'Matched SD assembly and independent review',
                                      'Physical RAM boot, both operator logins/host trust, NVMe idle and zero SD writes',
                                      'Actual KVM guest acceptance remains separate']}
        for name, value in [('initramfs.cpio.gz', compressed), ('manifest.json', (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode())]:
            with RUNTIME.private_output(output / name) as stream:
                stream.write(value)
        return manifest
    finally:
        captured.unlink(missing_ok=True)
        unpacked.unlink(missing_ok=True)
        if stage.exists():
            shutil.rmtree(stage)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('base_initramfs', 'base_manifest', 'rootfs', 'authorized_keys', 'identity_archive', 'new_output'):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    try:
        result = derive(args.base_initramfs, args.base_manifest, args.rootfs, args.authorized_keys,
                        args.identity_archive, args.new_output)
    except (OSError, ValueError, EOFError, tarfile.TarError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'SSH installer derivation: {error}\n')
    print(json.dumps({'profile': result['profile'], 'files': result['files'], 'hardware_acceptance': False}))


if __name__ == '__main__':
    main()
