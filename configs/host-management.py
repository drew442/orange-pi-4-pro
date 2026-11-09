#!/usr/bin/python3
"""Fail-closed A733 management activation. No configurable runtime paths.

Network and journal directives target systemd 252; SSH directives OpenSSH 9.2.
References: https://man.openbsd.org/sshd_config
https://www.freedesktop.org/software/systemd/man/252/systemd.network.html
https://www.freedesktop.org/software/systemd/man/252/journald.conf.html
"""
import ctypes
import errno
import fcntl
import hashlib
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
import types
import uuid

_spec = importlib.util.spec_from_file_location('a733_host_config', Path(__file__).with_name('host_config.py'))
CONFIG = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(CONFIG)
_spec = importlib.util.spec_from_file_location('a733_package_state', Path(__file__).with_name('package_state.py'))
PACKAGE = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(PACKAGE)
STATE = Path('/run/a733-state')
RUNTIME = Path('/run/a733-management')
KEY_NAME = 'ssh_host_ed25519_key'


class AddressUnavailable(ValueError):
    """A fixed, public diagnostic; never carries config or command output."""


def require(condition, message):
    if not condition:
        raise ValueError(message)


def secure_path(path, mode=None, directory=False):
    """Reject symlink parents and unsafe metadata before opening state or RAM."""
    path = Path(path)
    for parent in reversed(path.parents):
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o7022,
                f'unsafe parent: {parent}')
    info = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    require(expected(info.st_mode) and info.st_uid == 0 and info.st_gid == 0 and
            not info.st_mode & 0o7022, f'unsafe metadata: {path}')
    if not directory:
        require(info.st_nlink == 1, f'hard-linked file refused: {path}')
    if mode is not None:
        require(stat.S_IMODE(info.st_mode) == mode, f'wrong mode: {path}')
    return info


def read_secure(path, limit, mode=None):
    before = secure_path(path, mode)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        require((info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid, info.st_nlink) ==
                (before.st_dev, before.st_ino, before.st_mode, before.st_uid, before.st_gid, before.st_nlink),
                'file changed during open')
        value = stream.read(limit + 1)
    require(0 < len(value) <= limit, f'empty or oversized file: {path}')
    return value


def timesync_policy(metadata):
    return CONFIG.timesync_policy(metadata)


def timesync_guard(metadata, expected_uuid, *, boot=False):
    """Check only directory bindings live; audit quiescent clock state at boot."""
    policy = timesync_policy(metadata)
    if policy is None:
        return None
    own = Path(__file__)
    helper = (own.parent.parent / 'build/timesync-state.py' if own.parent.name == 'configs'
              else Path('/usr/lib/a733-tools/build/timesync-state.py'))
    data = read_secure(helper, 1024 * 1024)
    if boot:
        require(len(data) == policy['helper']['bytes'] and
                hashlib.sha256(data).hexdigest() == policy['helper']['sha256'],
                'timesync helper differs from selected root')
    module = types.ModuleType('a733_timesync_state')
    module.__file__ = str(helper)
    exec(compile(data, str(helper), 'exec'), module.__dict__)
    result = module.directory_ready(STATE, expected_uuid)
    if boot:
        audit = module.audit(STATE)
        require(audit.get('state_uuid') == expected_uuid and
                'timesync' in audit.get('metadata', {}), 'timesync boot state differs')
    return result


def prepare_clock_state():
    payload = json.loads(read_secure('/usr/lib/orangepi-hypervisor/payload.json', 1024 * 1024),
                         object_pairs_hook=CONFIG.unique_pairs)
    if timesync_policy(payload) is None:
        return None
    marker = read_secure(STATE / '.a733-state', 51)
    try:
        tag, identity, trailing = marker.decode('ascii').split('\n')
        parsed = uuid.UUID(identity)
    except (UnicodeError, ValueError, AttributeError) as error:
        raise ValueError('invalid state marker') from error
    require(tag == 'a733-state-v1' and not trailing and parsed.int != 0 and
            str(parsed) == identity and len(marker) == 51, 'invalid state marker')
    return timesync_guard(payload, identity, boot=True)


def mount_table(data):
    result = {}
    for line in data.splitlines():
        fields = line.split()
        require(fields.count('-') == 1, 'malformed mountinfo')
        split = fields.index('-')
        require(split >= 6 and len(fields) == split + 4, 'malformed mountinfo fields')
        target = fields[4]
        require('\\' not in target, 'escaped mount paths refused')
        result.setdefault(target, []).append({'id': fields[2], 'root': fields[3],
            'flags': set(fields[5].split(',')), 'type': fields[split + 1],
            'source': fields[split + 2], 'super': set(fields[split + 3].split(','))})
    return result


def validate_mounts(table):
    def one(target, fs, flags):
        rows = table.get(target, [])
        require(len(rows) == 1, f'missing/ambiguous mount: {target}')
        row = rows[0]
        require(row['type'] == fs and flags <= row['flags'], f'unsafe mount: {target}')
        return row
    root = one('/', 'ext4', {'ro'})
    state = one('/run/a733-state', 'ext4', {'rw', 'nodev', 'nosuid'})
    one('/run', 'tmpfs', {'rw', 'nodev', 'nosuid'})
    overlay = one('/etc', 'overlay', {'rw'})
    options = {}
    for field in overlay['super']:
        if '=' in field:
            key, value = field.split('=', 1)
            require(key not in options, 'duplicate overlay option')
            options[key] = value
    require((options.get('upperdir'), options.get('workdir')) in (
        ('/run/etc-upper', '/run/etc-work'),
        ('/newroot/run/etc-upper', '/newroot/run/etc-work')), 'overlay must use fixed RAM upper/work')
    protected = ('/etc', '/run/a733-state', '/run/a733-management', '/run/etc-upper',
                 '/run/etc-work', '/root/.ssh')
    for target in table:
        if target == '/etc/machine-id':
            continue
        require(target not in protected[2:] and not any(target.startswith(parent + '/') for parent in protected),
                'unexpected nested management mount')
        require(target != '/usr' and not target.startswith('/usr/'), 'unexpected immutable userspace mount')
    require('rw' not in root['flags'] and root['root'] == '/' and state['root'] == '/', 'wrong mount roots')
    match = re.fullmatch(r'/dev/(nvme[0-9]+n[0-9]+)p([23])', root['source'])
    require(match is not None, 'root must be selected NVMe p2/p3')
    require(state['source'] == f'/dev/{match[1]}p4' and state['id'] != root['id'],
            'state must be p4 on same NVMe namespace')
    for name in ('var', 'root', 'home'):
        row = one('/' + name, 'ext4', {'rw', 'nodev', 'nosuid'})
        require(row['id'] == state['id'] and row['root'] == '/' + name, 'wrong state bind')
    package_targets = {'/' + target for target, _ in PACKAGE.COMPONENTS.values()}
    for target in table:
        # /var itself is expected. Package view ancestors and descendants must
        # not hide the selected root or redirect writable state elsewhere.
        if target != '/var' and target not in package_targets:
            require(not any(target.startswith(path + '/') or path.startswith(target.rstrip('/') + '/')
                            for path in package_targets) or target == '/', 'foreign package-state mount')
    for name, (target, kind) in PACKAGE.COMPONENTS.items():
        row = one('/' + target, 'ext4', {'ro', 'nodev', 'nosuid'})
        require('rw' not in row['flags'] and row['id'] == root['id'] and
                row['source'] == root['source'] and row['root'] == '/' + PACKAGE.ROOT + '/' + name,
                'wrong immutable package-state bind')
    identity = one('/etc/machine-id', 'ext4', {'rw', 'nodev', 'nosuid'})
    require(identity['id'] == state['id'] and identity['root'] == '/identity/machine-id',
            'wrong machine identity bind')
    return root, state


def block_identity(row):
    path = Path(row['source'])
    info = path.lstat()
    require(stat.S_ISBLK(info.st_mode) and info.st_uid == 0, 'not a real root-owned block node')
    identity = f'{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}'
    require(identity == row['id'], 'mount/device identity mismatch')
    sysnode = (Path('/sys/dev/block') / identity).resolve(strict=True)
    match = re.fullmatch(r'(nvme[0-9]+n[0-9]+)p([234])', path.name)
    require(match is not None and sysnode.name == path.name and sysnode.parent.name == match[1],
            'unexpected sysfs NVMe parent')
    require((sysnode / 'partition').read_text().strip() == match[2] and
            (sysnode / 'dev').read_text().strip() == identity, 'unexpected sysfs partition')
    require(not any((parent / 'removable').exists() and
                    (parent / 'removable').read_text().strip() != '0'
                    for parent in (sysnode, sysnode.parent)), 'removable device refused')
    return identity


def runtime_guard():
    require(os.geteuid() == 0, 'root required')
    root, state = validate_mounts(mount_table(Path('/proc/self/mountinfo').read_text()))
    block_identity(root)
    block_identity(state)
    for path, row in (('/', root), (STATE, state)):
        info = os.stat(path)
        require(f'{os.major(info.st_dev)}:{os.minor(info.st_dev)}' == row['id'], 'mounted filesystem identity differs')
    run_device = os.stat('/run').st_dev
    for directory in ('/run/etc-upper', '/run/etc-work'):
        info = secure_path(directory, directory=True)
        require(info.st_dev == run_device, 'overlay upper/work is not RAM backed')
    secure_path(STATE, directory=True)
    for name in ('var', 'root', 'home'):
        secure_path(STATE / name, directory=True)
    secure_path(STATE / 'identity', 0o700, True)
    for name in ('var', 'root', 'home'):
        left, right = os.stat('/' + name), os.stat(STATE / name)
        require((left.st_dev, left.st_ino) == (right.st_dev, right.st_ino), 'bind identity differs')
    marker = read_secure(STATE / '.a733-state', 128)
    lines = marker.decode('ascii').splitlines()
    require(len(lines) == 2 and lines[0] == 'a733-state-v1', 'wrong state marker')
    state_uuid = lines[1]
    require(str(uuid.UUID(state_uuid)) == state_uuid and uuid.UUID(state_uuid).int != 0 and
            marker == f'a733-state-v1\n{state_uuid}\n'.encode(), 'invalid state UUID')
    probe = subprocess.run(['/sbin/blkid', '-p', '-o', 'export', state['source']],
                           check=True, capture_output=True, timeout=10).stdout.decode('ascii')
    fields = {}
    for line in probe.splitlines():
        key, value = line.split('=', 1)
        require(key not in fields, 'duplicate blkid field')
        fields[key] = value
    require(fields.get('UUID') == state_uuid and fields.get('TYPE') == 'ext4', 'state UUID/type differs')
    machine_id = read_secure(STATE / 'identity/machine-id', 33)
    require(re.fullmatch(b'[0-9a-f]{32}\n', machine_id) is not None and machine_id != b'0' * 32 + b'\n',
            'invalid machine identity')
    require(read_secure('/etc/machine-id', 33) == machine_id, 'machine identity differs')
    identity_info, bound_info = os.stat(STATE / 'identity/machine-id'), os.stat('/etc/machine-id')
    require((identity_info.st_dev, identity_info.st_ino) == (bound_info.st_dev, bound_info.st_ino),
            'machine identity bind differs')
    payload = json.loads(read_secure('/usr/lib/orangepi-hypervisor/payload.json', 1024 * 1024),
                         object_pairs_hook=CONFIG.unique_pairs)
    require(payload.get('schema') == 1 and payload.get('kernel_release') == os.uname().release == '6.6.98-a733-diag',
            'incompatible kernel ABI')
    slot = 'A' if root['source'].endswith('p2') else 'B'
    require(payload.get('slot') == slot and payload.get('filesystem_uuid') ==
            f'a7330000-0000-4000-8000-00000000000{slot.lower()}', 'wrong root payload slot')
    package = PACKAGE.validate_profile(payload.get('package_state'))
    base = Path('/') / PACKAGE.ROOT
    require(read_secure(base / 'FORMAT', 128) == PACKAGE.FORMAT, 'wrong package-state format')
    PACKAGE.validate_manifest(read_secure(base / 'manifest.json', PACKAGE.MANIFEST_LIMIT), package)
    for name, (target, kind) in PACKAGE.COMPONENTS.items():
        source = secure_path(base / name, directory=kind == 'directory')
        bound = secure_path('/' + target, directory=kind == 'directory')
        require((source.st_dev, source.st_ino) == (bound.st_dev, bound.st_ino) and
                f'{os.major(source.st_dev)}:{os.minor(source.st_dev)}' == root['id'],
                'package-state source/bind identity differs')
    secure_path(STATE / 'config', 0o700, True)
    secure_path(STATE / 'identity/ssh', 0o700, True)
    secure_path('/root', 0o700, True)
    secure_path('/root/.ssh', 0o700, True)
    CONFIG.validate_public_keys(read_secure('/root/.ssh/authorized_keys', 16384, 0o600))
    return CONFIG.validate_config(read_secure(STATE / 'config/management.json', 16384, 0o600))


def network_config(config):
    return ('[Match]\nType=ether\nMACAddress=' + config['management_mac'] +
            '\n\n[Network]\nDHCP=ipv4\nIPv6AcceptRA=no\nLinkLocalAddressing=no\nLLMNR=no\nMulticastDNS=no\n')


def ssh_config(address, config):
    ip = usable_address(address)
    require(ip is not None, 'invalid management IPv4')
    # Validate at this pure rendering boundary too; no text can inject directives.
    config = CONFIG.validate_config(json.dumps(config))
    return (f'AddressFamily inet\nListenAddress {ip}\nPort 22\n'
            f'HostKey {STATE}/identity/ssh/{KEY_NAME}\n'
            'AuthorizedKeysFile /root/.ssh/authorized_keys\nPubkeyAuthentication yes\n'
            'AuthenticationMethods publickey\nPasswordAuthentication no\n'
            'KbdInteractiveAuthentication no\nUsePAM yes\nPermitRootLogin prohibit-password\n'
            f'AllowUsers root@{config["management_cidr"]}\nStrictModes yes\n'
            'DisableForwarding yes\nAllowTcpForwarding no\nAllowAgentForwarding no\n'
            'AllowStreamLocalForwarding no\nX11Forwarding no\nPermitTunnel no\n'
            'SyslogFacility AUTH\nLogLevel INFO\nPrintMotd no\nPrintLastLog no\n'
            'PidFile /run/a733-management/sshd.pid\n')


def atomic_write(path, data, mode=0o600):
    """Write only below caller-validated root-owned directories, then persist."""
    path = Path(path)
    secure_path(path.parent, directory=True)
    fd, temporary = tempfile.mkstemp(prefix='.a733-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(data if isinstance(data, bytes) else data.encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)


def fsync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def ram_directory(path):
    path = Path(path)
    if not path.exists():
        ram_directory(path.parent)
        path.mkdir(mode=0o755)
        secure_path(path, directory=True)
        # main's private umask must not hide public RAM configuration from
        # unprivileged systemd services. Preserve modes of existing directories.
        path.chmod(0o755)
    secure_path(path, directory=True)


def ram_link(path, target):
    path = Path(path)
    ram_directory(path.parent)
    if os.path.lexists(path):
        require(not path.is_dir() or path.is_symlink(), 'refuse directory replacement')
        path.unlink()
    path.symlink_to(target)


def prepare():
    config = runtime_guard()  # No write is permitted before all checks succeed.
    prepare_clock_state()
    # Upgrade older state images whose private machine-id prevents networkd's
    # unprivileged DHCP client from deriving its default DUID. The guard verifies
    # that this is the bound NVMe identity, not a symlink or another filesystem.
    os.chmod('/etc/machine-id', 0o644)
    files = {
        '/etc/hostname': config['hostname'] + '\n',
        '/etc/hosts': f'127.0.0.1 localhost\n127.0.1.1 {config["hostname"]}\n',
        '/etc/systemd/network/00-a733-management.network': network_config(config),
        '/etc/systemd/resolved.conf.d/90-a733-management.conf':
            '[Resolve]\nLLMNR=no\nMulticastDNS=no\nDNSStubListener=yes\n',
        '/etc/systemd/journald.conf.d/90-a733-state.conf':
            '[Journal]\nStorage=persistent\nSystemMaxUse=128M\nRuntimeMaxUse=16M\n'
            'SystemKeepFree=256M\nMaxRetentionSec=7day\n',
    }
    for path, contents in files.items():
        ram_directory(Path(path).parent)
        atomic_write(path, contents, 0o644)
    ram_link('/etc/resolv.conf', '/run/systemd/resolve/stub-resolv.conf')
    for name in ('ssh.service', 'sshd.service', 'ssh.socket', 'sshd.socket',
                 'apt-daily.service', 'apt-daily.timer', 'apt-daily-upgrade.service',
                 'apt-daily-upgrade.timer', 'dpkg-db-backup.service', 'dpkg-db-backup.timer',
                 'systemd-journal-catalog-update.service'):
        ram_link('/etc/systemd/system/' + name, '/dev/null')
    for name in ('systemd-networkd.service', 'systemd-resolved.service', 'a733-management-ssh.service'):
        ram_link('/etc/systemd/system/multi-user.target.wants/' + name, '/lib/systemd/system/' + name)


def publish_private_key(source, destination):
    """Linux atomic no-replace rename: an identity is always one inode link.

    No hard-link or replacing-rename fallback is safe here. Unsupported libc,
    kernel or filesystem implementations fail closed before publication.
    """
    library = ctypes.CDLL('libc.so.6', use_errno=True)
    try:
        rename = library.renameat2
    except AttributeError as error:
        raise OSError(errno.ENOSYS, 'atomic no-replace publication unavailable') from error
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    # AT_FDCWD=-100 and RENAME_NOREPLACE=1 are Linux UAPI constants.
    result = rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1)
    if result != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))
    fsync_directory(Path(source).parent)
    fsync_directory(Path(destination).parent)


def ensure_host_key(directory):
    """Create once, never overwrite a private key; recover its public half."""
    directory = Path(directory)
    secure_path(directory, 0o700, True)
    private = directory / KEY_NAME
    if not os.path.lexists(private):
        with tempfile.TemporaryDirectory(prefix='.key-', dir=directory) as temporary:
            candidate = Path(temporary) / KEY_NAME
            subprocess.run(['/usr/bin/ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(candidate)],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30)
            read_secure(candidate, 16384, 0o600)
            fd = os.open(candidate, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            publish_private_key(candidate, private)
    read_secure(private, 16384, 0o600)
    derived = subprocess.run(['/usr/bin/ssh-keygen', '-y', '-f', str(private)],
                             check=True, capture_output=True, timeout=10).stdout
    public, _ = CONFIG.validate_public_key(derived)
    require(public.startswith(b'ssh-ed25519 '), 'host identity must be Ed25519')
    pubpath = private.with_name(private.name + '.pub')
    if os.path.lexists(pubpath):
        actual, _ = CONFIG.validate_public_key(read_secure(pubpath, 16384))
        require(actual == public, 'public/private host key mismatch')
    else:
        atomic_write(pubpath, public, 0o644)
    return private


def log_host_identity(address, private):
    """Expose only the verified host identity and selected address before SSH starts."""
    address = usable_address(address)
    require(address is not None, 'invalid management IPv4')
    private = Path(private)
    public, fingerprint = CONFIG.validate_public_key(
        read_secure(private.with_name(private.name + '.pub'), 16384))
    require(public.startswith(b'ssh-ed25519 '), 'host identity must be Ed25519')
    print(f'A733 SSH host key for {address}: ED25519 {fingerprint}', flush=True)


def usable_address(value):
    try:
        ip = ipaddress.IPv4Address(value)
    except (ValueError, TypeError):
        return None
    if ip.is_unspecified or ip.is_loopback or ip.is_multicast or ip.is_link_local or ip.is_reserved:
        return None
    return str(ip)


def discover_interface(mac):
    matches = []
    for path in Path('/sys/class/net').iterdir():
        if not re.fullmatch(r'[a-zA-Z0-9_.:-]{1,15}', path.name):
            continue
        try:
            if ((path / 'device').exists() and not (path / 'wireless').exists() and
                    not (path / 'phy80211').exists() and (path / 'type').read_text().strip() == '1' and
                    (path / 'address').read_text().strip() == mac):
                matches.append(path.name)
        except FileNotFoundError:
            continue
    require(len(matches) <= 1, 'ambiguous management Ethernet')
    return matches[0] if matches else None


def current_address(config):
    name = discover_interface(config['management_mac'])
    if name is None:
        return None
    result = subprocess.run(['/usr/sbin/ip', '-j', '-4', 'addr', 'show', 'dev', name],
                            check=True, capture_output=True, timeout=5)
    rows = json.loads(result.stdout, object_pairs_hook=CONFIG.unique_pairs)
    require(isinstance(rows, list) and len(rows) <= 1, 'ambiguous interface address response')
    if not rows:
        return None
    require(rows[0].get('ifname') == name, 'wrong interface address response')
    addresses = []
    for info in rows[0].get('addr_info', []):
        if info.get('family') != 'inet' or info.get('scope') != 'global':
            continue
        flags = info.get('flags', [])
        require(isinstance(flags, list), 'malformed address flags')
        if (info.get('tentative') or info.get('dadfailed') or info.get('deprecated') or
                {'tentative', 'dadfailed', 'deprecated'} & set(flags) or
                info.get('valid_life_time') == 0 or info.get('preferred_life_time') == 0):
            continue
        address = usable_address(info.get('local'))
        if address is not None:
            addresses.append(address)
    require(len(addresses) <= 1, 'ambiguous management IPv4')
    return addresses[0] if addresses else None


def stop_child(child):
    if child is not None and child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)


def supervise(config, stopped):
    """Every loss/change stops the old listener before any replacement starts."""
    child = None
    active = None
    deadline = time.monotonic() + 60
    try:
        while not stopped():
            # Guard failures also terminate the listener through the finally block.
            require(runtime_guard() == config, 'management config changed; restart required')
            address = current_address(config)
            if child is not None and child.poll() is not None:
                raise ValueError('sshd exited unexpectedly')
            if address != active:
                stop_child(child)
                child = None
                if active is not None:
                    deadline = time.monotonic() + 60
                active = None
            if address is None:
                if time.monotonic() >= deadline:
                    raise AddressUnavailable('management IPv4 unavailable after 60 seconds')
            elif child is None:
                private = ensure_host_key(STATE / 'identity/ssh')
                atomic_write(RUNTIME / 'sshd_config', ssh_config(address, config))
                log_host_identity(address, private)
                child = subprocess.Popen(['/usr/sbin/sshd', '-D', '-e', '-f', str(RUNTIME / 'sshd_config')])
                active = address
            time.sleep(5)
    finally:
        stop_child(child)


def serve():
    config = runtime_guard()
    ram_directory(RUNTIME)
    lock = RUNTIME / 'lock'
    fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        secure_path(lock, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        stopped = [False]
        def stop(_signum, _frame):
            stopped[0] = True
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        supervise(config, lambda: stopped[0])
    finally:
        os.close(fd)


def main():
    require(len(sys.argv) == 2 and sys.argv[1] in ('prepare', 'serve'), 'usage: host-management.py prepare|serve')
    os.umask(0o077)
    {'prepare': prepare, 'serve': serve}[sys.argv[1]]()


def report_failure(error):
    # Only this locally constructed exception has public text. Other errors can
    # contain private config, key material, command arguments or captured output.
    detail = '; management IPv4 unavailable after 60 seconds' if type(error) is AddressUnavailable else ''
    print(f'A733 management: {type(error).__name__}{detail}; activation failed', file=sys.stderr, flush=True)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        # Commands containing private material are never printed.
        report_failure(error)
        sys.exit(1)
