#!/usr/bin/env python3
"""Opt-in root check of ARM64 runtime closure using private regular-file fixtures.

Requires ARM64 binfmt, mke2fs and sfdisk. No mount or block devices.
Usage: sudo python3 tests/verify_storage_tools_runtime.py STAGED_TOOLS [PINNED_BUSYBOX]
"""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile

UUID = 'e7330000-0000-4000-8000-000000000001'
REPO = Path(__file__).resolve().parents[1]


def record(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def verify(source):
    if os.geteuid() != 0:
        raise ValueError('requires root for a private staging-tree chroot')
    source = Path(source)
    manifest_path = source / 'storage-tools-manifest.json'
    if source.is_symlink() or not source.is_dir() or manifest_path.is_symlink():
        raise ValueError('expected a regular staging directory and manifest')
    manifest = json.loads(manifest_path.read_text())
    # Load the committed binary hashes independently of the supplied manifest.
    lock = json.loads((REPO / 'configs/host-storage-tools.lock.json').read_text())
    with tempfile.TemporaryDirectory(prefix='a733-storage-runtime-', dir='/var/tmp') as temporary:
        root = Path(temporary)
        for name, expected in lock['files'].items():
            path = source / name
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, 'rb') as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError(f'not a regular file: {name}')
                data = stream.read(8 * 1024**2 + 1)
            actual = record(data)
            if actual != {key: expected[key] for key in ('bytes', 'sha256')} or actual != manifest['files'][name]:
                raise ValueError(f'pinned runtime mismatch: {name}')
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(0o755)
        fixture = root / 'fixture.ext4'
        blank = root / 'blank.img'
        for image in (fixture, blank):
            with image.open('xb') as stream:
                stream.truncate(32 * 1024**2)
        subprocess.run(['mke2fs', '-q', '-F', '-t', 'ext4', '-U', UUID,
                        '-O', '^has_journal', str(fixture)], check=True)
        gpt = root / 'gpt.img'
        with gpt.open('xb') as stream:
            stream.truncate(128 * 1024**2)
        subprocess.run(['sfdisk', '--no-reread', '--no-tell-kernel', str(gpt)],
                       input='label: gpt\nunit: sectors\n\n' + 'size=32768\n' * 5,
                       text=True, check=True, stdout=subprocess.DEVNULL)
        before = {image.name: record(image.read_bytes()) for image in (fixture, blank, gpt)}
        environment = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'}
        version = subprocess.run(['chroot', str(root), '/sbin/blkid', '--version'],
                                 env=environment, check=True, capture_output=True, text=True).stdout.strip()
        result = subprocess.run(['chroot', str(root), '/sbin/blkid', '-p', '-o', 'export',
                                 '-s', 'UUID', '-s', 'TYPE', '/fixture.ext4'],
                                env=environment, check=True, capture_output=True, text=True)
        if set(result.stdout.splitlines()) != {'DEVNAME=/fixture.ext4', f'UUID={UUID}', 'TYPE=ext4'}:
            raise ValueError(f'unexpected probe output: {result.stdout!r}')
        failure = subprocess.run(['chroot', str(root), '/sbin/blkid', '-p', '-o', 'export',
                                  '-s', 'UUID', '-s', 'TYPE', '/blank.img'],
                                 env=environment, capture_output=True, text=True)
        if failure.returncode != 2 or failure.stdout:
            raise ValueError('blank file must produce no identifiers and status 2')
        table = subprocess.run(['chroot', str(root), '/sbin/blkid', '-p', '-o', 'export',
                                '-s', 'PTTYPE', '/gpt.img'], env=environment, check=True,
                               capture_output=True, text=True)
        if set(table.stdout.splitlines()) != {'DEVNAME=/gpt.img', 'PTTYPE=gpt'}:
            raise ValueError(f'unexpected GPT probe output: {table.stdout!r}')
        after = {image.name: record(image.read_bytes()) for image in (fixture, blank, gpt)}
        if before != after:
            raise ValueError('probe modified regular-file input')
        return {'classification': 'ARM64 userspace binfmt/chroot file test; no boot or KVM acceptance',
                'version': version, 'probe': result.stdout.splitlines(), 'blank_status': failure.returncode,
                'gpt_probe': table.stdout.splitlines(),
                'fixture_bytes_unchanged': True, 'runtime_files': lock['files']}


def verify_ash(busybox):
    """Run the same fixture backend under the exact diagnostic ARM64 BusyBox."""
    descriptor = os.open(busybox, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('BusyBox must be a regular file')
        data = stream.read(8 * 1024**2 + 1)
    expected = json.loads((REPO / 'configs/diagnostic-sources.lock.json').read_text())['busybox']['binary_sha256']
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError('diagnostic BusyBox hash mismatch')
    with tempfile.TemporaryDirectory(prefix='a733-resolver-ash-', dir='/var/tmp') as temporary:
        root = Path(temporary)
        (root / 'bin').mkdir()
        (root / 'tmp').mkdir()
        (root / 'bin/busybox').write_bytes(data)
        (root / 'bin/busybox').chmod(0o755)
        for applet in ('sh', 'dirname', 'mktemp', 'rm', 'mkdir', 'ln', 'touch', 'cat', 'sed', 'mv'):
            (root / 'bin' / applet).symlink_to('busybox')
        scripts = {}
        for name in ('configs/host-storage-resolver.sh', 'tests/test-host-storage-resolver.sh'):
            contents = (REPO / name).read_bytes()
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(contents)
            scripts[name] = record(contents)
        environment = {'PATH': '/bin', 'LC_ALL': 'C'}
        # All executed commands are copied or symlinked inside the private root.
        result = subprocess.run(['/usr/sbin/chroot', str(root), '/bin/busybox', 'ash',
                                 '/tests/test-host-storage-resolver.sh', 'ash'],
                                env=environment, check=True, capture_output=True, text=True)
        subprocess.run(['/usr/sbin/chroot', str(root), '/bin/busybox', 'ash', '-c',
                        '[ "$(/bin/busybox stat -c \'%t:%T\' /bin/busybox)" = 0:0 ] && '
                        '[ "$(printf \'%d:%d\' 0x103 0x10)" = 259:16 ]'],
                       env=environment, check=True, capture_output=True, text=True)
        return {'busybox_sha256': expected, 'fixture_result': result.stdout.strip(), 'scripts': scripts,
                'stat_format_and_hex_printf': 'passed'}


if __name__ == '__main__':
    if len(sys.argv) not in (2, 3):
        sys.exit(__doc__)
    report = verify(sys.argv[1])
    if len(sys.argv) == 3:
        report['diagnostic_busybox_ash'] = verify_ash(sys.argv[2])
    print(json.dumps(report, indent=2))
