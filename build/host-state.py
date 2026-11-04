#!/usr/bin/env python3
"""Create a per-installation 8 GiB NVMe state payload in a regular file.

No mounts, network, package execution, devices, or hardware acceptance. Requires
root and the pinned builder. The result contains an operator public key only;
SSH host private keys must be generated on the installed board.
"""
import argparse
import base64
import contextlib
import gzip
import hashlib
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import struct
import subprocess
import tarfile
import tempfile
import uuid

REPO = Path(__file__).resolve().parents[1]
STATE_BYTES = 8 * 1024**3
SYS_DEV_BLOCK = Path('/sys/dev/block')
CLASSIFICATION = 'per-installation state; not a portable template; no hardware acceptance'
spec = importlib.util.spec_from_file_location('host_payload_state', REPO / 'build/host-payload.py')
PAYLOAD = importlib.util.module_from_spec(spec)
spec.loader.exec_module(PAYLOAD)


def digest(stream):
    result = hashlib.sha256()
    size = 0
    for data in iter(lambda: stream.read(1024**2), b''):
        result.update(data)
        size += len(data)
    return {'bytes': size, 'sha256': result.hexdigest()}


@contextlib.contextmanager
def regular_input(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError(f'input must be a regular non-symlink file: {path}')
        yield stream


def bounded_input(path, limit):
    with regular_input(path) as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f'input too large: {path}')
    return data


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'duplicate JSON field: {key}')
        result[key] = value
    return result


def validate_config(data):
    value = json.loads(data, object_pairs_hook=unique_pairs)
    required = {'schema', 'hostname', 'management_mac', 'management_cidr', 'dhcp'}
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError('management config must contain exactly schema, hostname, management_mac, management_cidr, dhcp')
    if type(value['schema']) is not int or value['schema'] != 1 or value['dhcp'] is not True:
        raise ValueError('schema 1 and dhcp=true required')
    hostname = value['hostname']
    if not isinstance(hostname, str) or not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', hostname):
        raise ValueError('hostname must be one lowercase DNS label')
    mac = value['management_mac']
    if (not isinstance(mac, str) or not re.fullmatch(r'(?:[0-9a-f]{2}:){5}[0-9a-f]{2}', mac)
            or int(mac[:2], 16) & 1 or mac == '00:00:00:00:00:00'):
        raise ValueError('management_mac must be canonical lowercase nonzero unicast MAC')
    cidr = value['management_cidr']
    if not isinstance(cidr, str):
        raise ValueError('management_cidr must be canonical IPv4 CIDR')
    network = ipaddress.IPv4Network(cidr, strict=True)
    if str(network) != cidr or network.prefixlen == 0:
        raise ValueError('management_cidr must be canonical IPv4 CIDR with prefix 1..32')
    return value


def validate_public_key(data):
    try:
        text = data.decode('ascii')
    except UnicodeDecodeError as error:
        raise ValueError('public key must be ASCII') from error
    if '\r' in text or '\x00' in text or len(text.splitlines()) != 1:
        raise ValueError('public key must be one line')
    fields = text.strip().split()
    if len(fields) < 2 or fields[0] != 'ssh-ed25519':
        raise ValueError('a plain ssh-ed25519 public key without options is required')
    try:
        blob = base64.b64decode(fields[1], validate=True)
    except ValueError as error:
        raise ValueError('invalid public key base64') from error
    algorithm = b'ssh-ed25519'
    expected = struct.pack('>I', len(algorithm)) + algorithm + struct.pack('>I', 32)
    if (len(blob) != len(expected) + 32 or not blob.startswith(expected)
            or base64.b64encode(blob).decode() != fields[1]):
        raise ValueError('invalid RFC4253 ed25519 public key')
    canonical = f'ssh-ed25519 {fields[1]}\n'.encode()
    fingerprint = 'SHA256:' + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip('=')
    return canonical, fingerprint


def validate_uuid(value):
    if (not isinstance(value, str) or str(uuid.UUID(value)) != value or
            uuid.UUID(value).int == 0 or value in PAYLOAD.SLOT_UUIDS.values()):
        raise ValueError('state UUID must be canonical, nonzero and distinct from root slots')
    return value


def validate_output(output):
    output = Path(output)
    parent = REPO / 'out'
    if (not output.is_absolute() or output.parent != parent or
            not output.name.startswith('host-state-') or output.name == 'host-state-' or
            output.exists() or output.is_symlink()):
        raise ValueError('output must be a new absolute repo/out/host-state-NAME')
    if parent.is_symlink() or parent.resolve() != parent:
        raise ValueError('symlink output parent refused')
    return output


def validate_workspace_storage(path):
    """Reject recognizable SD, removable and RAM storage, including DM slaves.

    Kernel sdX/removable=0 does not prove that a USB reader contains no SD card.
    The operator must still locate this builder checkout on known fixed storage.
    """
    value = json.loads(subprocess.check_output(
        ['findmnt', '--json', '--target', str(path), '--output', 'FSTYPE,MAJ:MIN'], text=True))
    entries = value.get('filesystems', [])
    if len(entries) != 1 or entries[0].get('fstype') not in ('ext4', 'xfs', 'btrfs'):
        raise ValueError('output staging requires a supported local disk filesystem')
    device = SYS_DEV_BLOCK / entries[0]['maj:min']
    if not device.exists():
        raise ValueError('cannot identify output backing block storage')
    def inspect(node):
        node = node.resolve()
        # USB SD readers can use sdX names; removable media are refused too.
        for ancestor in (node, *node.parents):
            removable = ancestor / 'removable'
            if removable.is_file() and removable.read_text().strip() != '0':
                raise ValueError('removable output backing storage refused')
        slaves = list((node / 'slaves').glob('*'))
        if slaves:
            for slave in slaves:
                inspect(slave)
        elif not re.fullmatch(r'(?:nvme\d+n\d+(?:p\d+)?|sd[a-z]+\d*|vd[a-z]+\d*|xvd[a-z]+\d*)', node.name):
            raise ValueError(f'unsupported output backing device (SD/RAM/loop refused): {node.name}')
    inspect(device)
    return entries[0]


def write_private(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)


def seed_state(extracted, state, state_uuid, config, public_key):
    source_var = extracted / 'var'
    info = source_var.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o7022:
        raise ValueError('userspace var must be a root-owned real directory without unsafe modes')
    state.mkdir(mode=0o700)
    source_var.rename(state / 'var')
    for name, mode in (('root', 0o700), ('home', 0o755), ('identity', 0o700),
                       ('identity/ssh', 0o700), ('config', 0o700), ('root/.ssh', 0o700)):
        (state / name).mkdir(mode=mode)
        (state / name).chmod(mode)
    machine_id = secrets.token_hex(16)
    while machine_id == '0' * 32:
        machine_id = secrets.token_hex(16)
    write_private(state / '.a733-state', f'a733-state-v1\n{state_uuid}\n'.encode())
    write_private(state / 'identity/machine-id', (machine_id + '\n').encode())
    write_private(state / 'root/.ssh/authorized_keys', public_key)
    write_private(state / 'config/management.json', (json.dumps(config, sort_keys=True, separators=(',', ':')) + '\n').encode())
    return machine_id


def contents(root):
    records = {}
    for path in [root, *sorted(root.rglob('*'))]:
        info = path.lstat()
        record = {'mode': f'{stat.S_IMODE(info.st_mode):04o}', 'uid': info.st_uid, 'gid': info.st_gid}
        if stat.S_ISREG(info.st_mode):
            record.update(type='file', **PAYLOAD.file_record(path))
        elif stat.S_ISDIR(info.st_mode):
            record['type'] = 'directory'
        elif stat.S_ISLNK(info.st_mode):
            record.update(type='symlink', target=os.readlink(path))
        else:
            raise ValueError(f'unsupported state file type: {path}')
        record['xattrs'] = {name: hashlib.sha256(os.getxattr(path, name, follow_symlinks=False)).hexdigest()
                            for name in sorted(os.listxattr(path, follow_symlinks=False))}
        records[str(path.relative_to(root))] = record
    return records



def filesystem_directories(image):
    """Read actual post-mke2fs directory metadata; staging is a separate BOM."""
    records = {}
    for name in ('/', '/lost+found'):
        result = subprocess.run(['debugfs', '-R', f'stat {name}', str(image)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, check=True, env={**os.environ, 'LC_ALL': 'C'})
        mode = re.search(r'Type:\s+directory\s+Mode:\s+([0-7]{4})\b', result.stdout)
        owner = re.search(r'User:\s+(\d+)\s+Group:\s+(\d+)\b', result.stdout)
        if not mode or not owner:
            raise ValueError(f'cannot read filesystem directory metadata: {name}')
        permissions = int(mode[1], 8)
        uid, gid = int(owner[1]), int(owner[2])
        if uid != 0 or gid != 0 or permissions & 0o7022:
            raise ValueError(f'unsafe filesystem directory metadata: {name}')
        records[name] = {'type': 'directory', 'mode': mode[1], 'uid': uid, 'gid': gid}
    return records


def make_image(root, output, state_uuid, size=STATE_BYTES):
    # Internal size override exists only for small regular-file regression fixtures.
    state_uuid = validate_uuid(state_uuid)
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.truncate(size)
    subprocess.run(['mke2fs', '-q', '-t', 'ext4', '-F', '-b', '4096', '-O', 'has_journal',
                    '-E', 'lazy_itable_init=0,lazy_journal_init=0', '-m', '0', '-U', state_uuid,
                    '-L', 'a733-state', '-d', str(root), str(output)], check=True)
    with output.open('rb') as stream:
        stream.seek(1024)
        block = stream.read(1024)
    if (block[56:58] != b'\x53\xef' or not struct.unpack_from('<I', block, 92)[0] & 4 or
            struct.unpack_from('<I', block, 100)[0] & 0x1000 or
            str(uuid.UUID(bytes=block[104:120])) != state_uuid or output.stat().st_size != size):
        raise ValueError('state must be writable ext4 with a journal and the selected UUID/size')
    subprocess.run(['e2fsck', '-fn', str(output)], check=True)
    return filesystem_directories(output)



def compress_image(image, output):
    """Stream the regular image into gzip with a reproducible empty header."""
    with regular_input(image) as source, output.open('xb') as target:
        with gzip.GzipFile(filename='', mode='wb', fileobj=target, mtime=0) as compressed:
            shutil.copyfileobj(source, compressed, length=1024**2)


def build(rootfs, config_path, public_key_path, output, state_uuid=None):
    if os.geteuid() != 0:
        raise ValueError('root required to preserve userspace metadata')
    os.umask(0o077)
    for tool in ('mke2fs', 'e2fsck', 'debugfs', 'tar', 'findmnt'):
        if not shutil.which(tool):
            raise ValueError(f'required tool missing: {tool}')
    packages = PAYLOAD.validate_builder_packages(REPO / 'configs/builder-packages.lock.tsv')
    output = validate_output(output)
    config_data = bounded_input(config_path, 16384)
    key_data = bounded_input(public_key_path, 16384)
    config = validate_config(config_data)
    public_key, fingerprint = validate_public_key(key_data)
    state_uuid = validate_uuid(state_uuid or str(uuid.uuid4()))
    evidence = REPO / 'docs/evidence/host-userspace-20261104.json'
    evidence_data = bounded_input(evidence, 1024**2)
    expected = json.loads(evidence_data)['files']['rootfs.tar.gz']
    # Hold the authenticated source descriptor; copy and reauthenticate its bytes
    # into the private output to use the existing GNU metadata extraction helper.
    with regular_input(rootfs) as source:
        if digest(source) != expected:
            raise ValueError('rootfs archive hash/size differs from userspace evidence')
        source.seek(0)
        with tarfile.open(fileobj=source, mode='r:*') as archive:
            members = archive.getmembers()
            PAYLOAD.validate_tar(members)
            unpacked = sum(m.size for m in members if m.isfile())
        if unpacked > STATE_BYTES * 3 // 4:
            raise ValueError('userspace exceeds conservative state budget')
        source.seek(0)
        output.parent.mkdir(exist_ok=True)
        storage = validate_workspace_storage(output.parent)
        # Reserve image plus worst-case compressed copy, extraction, input and margin.
        required = 2 * STATE_BYTES + unpacked + expected['bytes'] + 1024**3
        if shutil.disk_usage(output.parent).free < required:
            raise ValueError(f'insufficient space: require {required} bytes')
        output.mkdir(mode=0o700)
        snapshot = output / '.rootfs-input.tar.gz'
        with snapshot.open('xb') as target:
            shutil.copyfileobj(source, target)
        PAYLOAD.authenticate(snapshot, expected)
    provenance = output / 'provenance'
    provenance.mkdir(mode=0o700)
    for path in (Path(__file__).resolve(), REPO / 'build/host-payload.py',
                 REPO / 'configs/builder-packages.lock.tsv',
                 REPO / 'configs/host-userspace-packages.lock.tsv',
                 REPO / 'configs/host-userspace-package-hashes.lock.tsv',
                 REPO / 'build/host-userspace.sh'):
        write_private(provenance / path.name, bounded_input(path, 8 * 1024**2))
    write_private(provenance / evidence.name, evidence_data)
    with tempfile.TemporaryDirectory(prefix='.staging-', dir=output) as temporary:
        stage = Path(temporary)
        extracted = stage / 'userspace'
        extracted.mkdir()
        PAYLOAD.extract_rootfs(snapshot, extracted)
        state = stage / 'state'
        machine_id = seed_state(extracted, state, state_uuid, config, public_key)
        PAYLOAD.write_json(output / 'staging-contents.json', contents(state))
        filesystem_metadata = make_image(state, output / 'state.ext4', state_uuid)
    snapshot.unlink()
    compress_image(output / 'state.ext4', output / 'state.ext4.gz')
    files = {str(p.relative_to(output)): PAYLOAD.file_record(p)
             for p in sorted(output.rglob('*')) if p.is_file()}
    PAYLOAD.write_json(output / 'manifest.json', {
        'schema': 1, 'board': 'Orange Pi 4 Pro A733', 'classification': CLASSIFICATION,
        'state_bytes': STATE_BYTES, 'state_uuid': state_uuid, 'machine_id': machine_id,
        'identity_policy': 'fresh per installation; never clone this image across hosts; fresh builds intentionally differ',
        'compression': 'streamed gzip; empty filename and mtime=0; identical image bytes produce identical gzip bytes on the pinned builder',
        'contents_scope': 'staging-contents.json records the pre-mke2fs source tree only; not an exact filesystem BOM',
        'filesystem_directories': filesystem_metadata,
        'staging_storage_guard_limit': 'kernel sdX/removable=0 cannot identify every USB SD reader; operator must use known fixed builder storage',
        'sources': {'rootfs.tar.gz': expected,
                    'operator_config': {'bytes': len(config_data), 'sha256': hashlib.sha256(config_data).hexdigest()},
                    'operator_public_key': {'bytes': len(key_data), 'sha256': hashlib.sha256(key_data).hexdigest()}},
        'management': config, 'admin_public_key_fingerprint': fingerprint,
        'builder_packages': packages, 'staging_storage': storage, 'files': files,
        'tools': {tool: PAYLOAD.tool_version(tool) for tool in ('mke2fs', 'e2fsck', 'debugfs')},
        'hardware_acceptance': 'pending; no physical boot, NVMe installation, KVM or SD-write evidence'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('rootfs_tar', type=Path)
    parser.add_argument('config_json', type=Path)
    parser.add_argument('admin_public_key', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--state-uuid', help='explicit per-installation UUID for recovery/fixtures; defaults to fresh UUID4')
    args = parser.parse_args()
    try:
        build(args.rootfs_tar, args.config_json, args.admin_public_key, args.output, args.state_uuid)
    except (ValueError, OSError, tarfile.TarError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'host state: {error}\n')


if __name__ == '__main__':
    main()
