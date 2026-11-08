#!/usr/bin/env python3
"""Private ARM64 SSH/PAM fixture; no board or hardware acceptance."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('derive_ssh_fixture', REPO / 'build/derive-ssh-installer.py')
D = importlib.util.module_from_spec(spec)
spec.loader.exec_module(D)
FLOOR = 256 * 1024**2


def authenticated(archive, manifest, pin):
    """Authenticate receipt bytes before JSON, then gzip before CPIO parsing."""
    if not re.fullmatch('[0-9a-f]{64}', pin):
        raise ValueError('manifest SHA256 must be 64 lowercase hex digits')
    receipt = D.read_input(manifest, 16 * 1024**2)
    if hashlib.sha256(receipt).hexdigest() != pin:
        raise ValueError('manifest SHA256 mismatch')
    value = json.loads(receipt, object_pairs_hook=D.COMMON.unique_pairs)
    if value.get('profile') != 'a733-private-ssh-ram-installer-v1':
        raise ValueError('unexpected profile')
    packed = D.read_input(archive, D.MAX_PACKED, value['files']['initramfs.cpio.gz'])
    raw = D.unpack(packed)
    _, entries = D.parse(raw)
    actual = {name: {'mode': mode, **D.RUNTIME.record(data)} for name, (mode, data) in entries.items()}
    if actual != value['entries'] or len(raw) != value['expanded_bytes']:
        raise ValueError('receipt entry/expansion mismatch')
    if entries.get('etc/shadow') != (stat.S_IFREG | 0o600, b'root:!:1:0:99999:7:::\n'):
        raise ValueError('locked, nonexpired root required')
    if entries.get('etc/ssh/sshd_config', (0, b''))[1] != (REPO / 'configs/installer-sshd_config').read_bytes():
        raise ValueError('unexpected sshd policy')
    if entries.get('etc/pam.d/sshd', (0, b''))[1] != (REPO / 'configs/installer-sshd.pam').read_bytes():
        raise ValueError('unexpected PAM stack')
    public, fingerprint = D.COMMON.validate_public_key(entries['etc/ssh/ssh_host_ed25519_key.pub'][1])
    if fingerprint != D.HOST_FINGERPRINT:
        raise ValueError('unexpected public host identity')
    for name in ('usr/sbin/sshd', 'usr/bin/ssh-keygen', 'bin/busybox'):
        mode, data = entries[name]
        if not stat.S_ISREG(mode) or data[:6] != b'\x7fELF\x02\x01' or data[18:20] != b'\xb7\x00':
            raise ValueError('fixture requires actual little-endian AArch64 ELF: ' + name)
    return entries, public, value['files']['initramfs.cpio.gz']


def extract(entries, root):
    """Validated namespace only: directories first, files next, symlinks last."""
    root.mkdir(mode=0o700)
    for name, (mode, _) in sorted(entries.items(), key=lambda row: (row[0].count('/'), row[0])):
        if name == '.':
            if not stat.S_ISDIR(mode):
                raise ValueError('archive root must be directory')
            continue
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if stat.S_ISDIR(mode):
            path.mkdir(exist_ok=True)
    for name, (mode, data) in entries.items():
        if stat.S_ISREG(mode):
            with D.RUNTIME.private_output(root / name) as stream:
                stream.write(data)
            os.chmod(root / name, stat.S_IMODE(mode))
    for name, (mode, data) in entries.items():
        if stat.S_ISLNK(mode):
            os.symlink(data.decode(), root / name)
    for name, (mode, _) in entries.items():
        if name != '.' and stat.S_ISDIR(mode):
            os.chmod(root / name, stat.S_IMODE(mode))
    # The enclosing 0700 evidence directory protects this tree. Inside chroot,
    # sshd's unprivileged reexecution must be able to traverse its own /.
    os.chmod(root, 0o755)


def run(command, evidence, label, timeout=30, expected=0):
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
                            env={**os.environ, 'LC_ALL': 'C'})
    (evidence / (label + '.stdout')).write_bytes(result.stdout)
    (evidence / (label + '.stderr')).write_bytes(result.stderr)
    if expected is not None and result.returncode != expected:
        raise ValueError(f'{label}: exit {result.returncode}; see private logs')
    return result


def policy_check(data):
    values = dict(line.split(' ', 1) for line in data.decode().splitlines() if ' ' in line)
    expected = {'usepam': 'yes', 'authenticationmethods': 'publickey', 'passwordauthentication': 'no',
                'kbdinteractiveauthentication': 'no', 'permitrootlogin': 'without-password',
                'pubkeyauthentication': 'yes', 'strictmodes': 'yes', 'disableforwarding': 'yes',
                'allowtcpforwarding': 'no', 'allowagentforwarding': 'no', 'x11forwarding': 'no',
                'permittunnel': 'no', 'permituserenvironment': 'no', 'permituserrc': 'no',
                'allowusers': 'root', 'authorizedkeysfile': '.ssh/authorized_keys',
                'hostkey': '/etc/ssh/ssh_host_ed25519_key', 'port': '2222'}
    for key, value in expected.items():
        if values.get(key) != value:
            raise ValueError(f'sshd -T policy mismatch: {key}')


def namespace(work):
    """This process is PID 1 in fresh PID/mount/network namespaces."""
    evidence, root = work / 'logs', work / 'root'
    if os.getpid() != 1:
        raise ValueError('internal runner requires fresh PID namespace')
    run(['mount', '--make-rprivate', '/'], evidence, 'private-mounts')
    run(['mount', '-t', 'proc', '-o', 'nosuid,nodev,noexec', 'proc', '/proc'], evidence, 'namespace-proc')
    run(['ip', 'link', 'set', 'lo', 'up'], evidence, 'loopback')
    for directory in ('dev', 'proc', 'run', 'tmp'):
        path = root / directory
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise ValueError('runtime directory alias refused')
        path.mkdir(exist_ok=True)
    run(['mount', '-t', 'tmpfs', '-o', 'mode=0755,nosuid,size=8m', 'tmpfs', str(root / 'dev')], evidence, 'dev')
    for name, major, minor, mode in [('null', 1, 3, 0o666), ('zero', 1, 5, 0o666),
                                    ('random', 1, 8, 0o666), ('urandom', 1, 9, 0o666), ('tty', 5, 0, 0o666)]:
        os.mknod(root / 'dev' / name, stat.S_IFCHR | mode, os.makedev(major, minor))
        os.chmod(root / 'dev' / name, mode)
    (root / 'dev/pts').mkdir()
    run(['mount', '-t', 'devpts', '-o', 'newinstance,nosuid,noexec,mode=0620,ptmxmode=0666',
         'devpts', str(root / 'dev/pts')], evidence, 'devpts')
    os.symlink('pts/ptmx', root / 'dev/ptmx')
    run(['mount', '-t', 'proc', '-o', 'nosuid,nodev,noexec', 'proc', str(root / 'proc')], evidence, 'chroot-proc')
    for directory, options in [('run', 'mode=0755,nosuid,nodev,size=16m'), ('tmp', 'mode=1777,nosuid,nodev,size=16m')]:
        run(['mount', '-t', 'tmpfs', '-o', options, 'tmpfs', str(root / directory)], evidence, directory)
    (root / 'run/sshd').mkdir(mode=0o755)
    target = ['chroot', str(root)]
    # Install applet symlinks only in disposable copy, as RAM init does.
    run(target + ['/bin/busybox', '--install', '-s', '/bin'], evidence, 'busybox-applets')
    proof = run(target + ['/usr/bin/ssh-keygen', '-y', '-f', '/etc/ssh/ssh_host_ed25519_key'],
                evidence, 'target-private-key-public-proof')
    public, fingerprint = D.COMMON.validate_public_key(proof.stdout)
    retained, _ = D.COMMON.validate_public_key((root / 'etc/ssh/ssh_host_ed25519_key.pub').read_bytes())
    if fingerprint != D.HOST_FINGERPRINT or public != retained:
        raise ValueError('target private host-key proof failed')
    sshd = target + ['/usr/sbin/sshd', '-f', '/etc/ssh/sshd_config', '-p', '2222']
    run(sshd + ['-t'], evidence, 'sshd-t')
    policy_check(run(sshd + ['-T'], evidence, 'sshd-T').stdout)
    known = work / 'known_hosts'
    known.write_bytes(b'[127.0.0.1]:2222 ' + public)
    client = ['/usr/bin/ssh', '-F', '/dev/null', '-p', '2222', '-o', 'BatchMode=yes',
              '-o', 'ConnectTimeout=5', '-o', 'ConnectionAttempts=1', '-o', 'IdentitiesOnly=yes',
              '-o', 'IdentityAgent=none', '-o', 'StrictHostKeyChecking=yes',
              '-o', 'UserKnownHostsFile=' + str(known), '-o', 'GlobalKnownHostsFile=/dev/null',
              '-o', 'HostKeyAlgorithms=ssh-ed25519']
    with (evidence / 'sshd.log').open('wb') as log:
        daemon = subprocess.Popen(sshd + ['-D', '-e'], stdout=log, stderr=log)
        try:
            # Readiness probes are authenticated real sessions, with a finite deadline.
            deadline = time.monotonic() + 20
            while True:
                result = run(client + ['-i', str(work / 'client'), 'root@127.0.0.1',
                                       "printf 'A733_COMMAND_OK\\n'; /bin/busybox id -u"],
                             evidence, 'command', expected=None, timeout=10)
                if result.returncode == 0:
                    break
                if daemon.poll() is not None or time.monotonic() >= deadline:
                    raise ValueError('authenticated command session failed')
                time.sleep(0.2)
            if result.stdout != b'A733_COMMAND_OK\n0\n':
                raise ValueError('command session output differs')
            result = run(client + ['-tt', '-i', str(work / 'client'), 'root@127.0.0.1',
                                   "test -t 0 && printf 'A733_PTY_OK\\n'"], evidence, 'pty')
            if result.stdout.replace(b'\r\n', b'\n') != b'A733_PTY_OK\n':
                raise ValueError('PTY session output differs')
            for label, options in [('wrong-key', ['-i', str(work / 'wrong')]),
                                    ('password-only', ['-o', 'PubkeyAuthentication=no',
                                                       '-o', 'PreferredAuthentications=password',
                                                       '-o', 'PasswordAuthentication=yes'])]:
                result = run(client + options + ['root@127.0.0.1', 'true'], evidence, label, expected=255)
                if b'Permission denied' not in result.stderr:
                    raise ValueError(label + ' did not fail for authentication')
            result = run(client + ['-i', str(work / 'client'), '-o', 'ExitOnForwardFailure=yes',
                                   '-R', '127.0.0.1:2223:127.0.0.1:2224', '-N', 'root@127.0.0.1'],
                         evidence, 'forwarding', expected=255)
            if b'remote port forwarding failed' not in result.stderr:
                raise ValueError('forwarding denial not demonstrated')
        finally:
            daemon.terminate()
            try:
                daemon.wait(timeout=5)
            except subprocess.TimeoutExpired:
                daemon.kill()
                daemon.wait(timeout=5)
    return {'target_private_key_proof': fingerprint, 'command_uid': 0, 'pty': True,
            'wrong_key_rejected': True, 'password_only_rejected': True, 'forwarding_rejected': True,
            'sshd_t_and_T': True, 'locked_root_PAM_session': True}


def verify(args):
    if os.geteuid() != 0:
        raise ValueError('root required on prepared Ubuntu 22.04 builder')
    release = Path('/etc/os-release').read_text()
    if not re.search(r'^ID=ubuntu$', release, re.M) or not re.search(r'^VERSION_ID="22\.04"$', release, re.M):
        raise ValueError('prepared Ubuntu 22.04 builder required')
    output = args.output
    if (not output.is_absolute() or output.parent.resolve() != output.parent or output.exists()
            or output.is_symlink() or not re.fullmatch('ssh-arm-fixture-[A-Za-z0-9._-]+', output.name)):
        raise ValueError('fresh canonical absolute ssh-arm-fixture-NAME output required')
    storage = D.RUNTIME.STATE.validate_workspace_storage(output.parent)
    memory = re.search(r'^MemAvailable:\s+(\d+) kB$', Path('/proc/meminfo').read_text(), re.M)
    if shutil.disk_usage(output.parent).free < 1024**3 + FLOOR or not memory or int(memory[1]) * 1024 < 1024**3:
        raise ValueError('require 1 GiB extra fixed storage plus 256 MiB floor and 1 GiB MemAvailable')
    rows = [line.split('\t') for line in (REPO / 'configs/builder-packages.lock.tsv').read_text().splitlines()]
    expected = [row for row in rows if row[0] == 'openssh-client']
    actual = subprocess.check_output(['dpkg-query', '-W', '-f=${Package}\t${Version}\t${Architecture}\n',
                                      'openssh-client'], text=True).strip().split('\t')
    if expected != [actual]:
        raise ValueError('native openssh-client differs from builder pin')
    registration = Path('/proc/sys/fs/binfmt_misc/qemu-aarch64').read_text()
    flags = re.search(r'^flags: (.*)$', registration, re.M)
    if not registration.startswith('enabled\n') or not flags or 'F' not in flags[1]:
        raise ValueError('enabled persistent AArch64 binfmt required for chroot/reexecution')
    interpreter = Path(re.search(r'^interpreter (.+)$', registration, re.M)[1]).resolve(strict=True)
    binfmt = {'registration': registration, 'configured_interpreter': str(interpreter),
              'interpreter': D.RUNTIME.record(interpreter.read_bytes()),
              'limit': 'Kernel-held F interpreter descriptor is not independently inspectable.'}
    os.umask(0o077)
    entries, _, archive_pin = authenticated(args.archive, args.manifest, args.manifest_sha256)
    output.mkdir(mode=0o700)
    logs, root = output / 'logs', output / 'root'
    logs.mkdir(mode=0o700)
    result = {'schema': 1, 'status': 'FAIL', 'hardware_acceptance': False,
              'classification': 'ARM64 binfmt SSH/PAM software fixture; no physical boot or KVM evidence',
              'source_manifest_sha256': args.manifest_sha256, 'source_archive': archive_pin,
              'storage': storage, 'native_client_package': actual, 'binfmt': binfmt}
    result['native_client_files'] = {name: D.RUNTIME.record(Path('/usr/bin', name).read_bytes())
                                     for name in ('ssh', 'ssh-keygen')}
    try:
        extract(entries, root)
        for name in ('client', 'wrong'):
            run(['/usr/bin/ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(output / name)], logs, name + '-keygen')
        keys = root / 'root/.ssh/authorized_keys'
        if keys.is_symlink() or not keys.is_file():
            raise ValueError('fixture authorized_keys must be regular')
        keys.write_bytes((output / 'client.pub').read_bytes())
        os.chmod(keys, 0o600)
        command = ['unshare', '--mount', '--net', '--pid', '--fork', '--kill-child=SIGKILL',
                   sys.executable, str(Path(__file__).resolve()), '--namespace', str(output)]
        with (logs / 'namespace.stdout').open('wb') as stdout, (logs / 'namespace.stderr').open('wb') as stderr:
            process = subprocess.Popen(command, stdout=stdout, stderr=stderr, start_new_session=True)
            try:
                code = process.wait(timeout=180)
            except BaseException as error:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=10)
                if isinstance(error, subprocess.TimeoutExpired):
                    raise ValueError('namespace overall timeout') from error
                raise
        if code:
            raise ValueError('namespace fixture failed; see private namespace.stderr')
        result['checks'] = json.loads((output / 'namespace-result.json').read_text())
        # Reauthenticate original input after sessions; never edit either input.
        authenticated(args.archive, args.manifest, args.manifest_sha256)
        result['source_unchanged'] = True
        result['status'] = 'PASS'
    except BaseException as error:
        result['error'] = str(error)
        raise
    finally:
        # Namespace mounts disappear when its PID 1 exits; never recursively remove a mounted tree.
        mounts = [re.sub(r'\\([0-7]{3})', lambda match: chr(int(match[1], 8)), line.split()[4])
                  for line in Path('/proc/self/mountinfo').read_text().splitlines()]
        if any(path == str(root) or path.startswith(str(root) + '/') for path in mounts):
            result['status'] = 'FAIL'
            result['cleanup_error'] = 'unexpected host-visible mount: retained private root for manual inspection'
        elif root.exists():
            shutil.rmtree(root)
        for name in ('client', 'client.pub', 'wrong', 'wrong.pub'):
            (output / name).unlink(missing_ok=True)
        (output / 'result.json').write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    if result.get('cleanup_error'):
        raise ValueError(result['cleanup_error'])
    return result


def main():
    def interrupted(number, _frame):
        raise InterruptedError(f'fixture interrupted by signal {number}')
    for number in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(number, interrupted)
    # Internal invocation receives only the private tree; public CLI requires pinned receipt.
    if len(sys.argv) == 3 and sys.argv[1] == '--namespace':
        work = Path(sys.argv[2])
        checks = namespace(work)
        (work / 'namespace-result.json').write_text(json.dumps(checks, indent=2) + '\n')
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('manifest_sha256')
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    try:
        result = verify(args)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(1, f'ARM SSH fixture: {error}\n')
    print(json.dumps({'status': result['status'], 'hardware_acceptance': False, 'evidence': str(args.output / 'result.json')}))


if __name__ == '__main__':
    main()
