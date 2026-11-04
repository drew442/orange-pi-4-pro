#!/usr/bin/env python3
"""Opt-in root test of pinned ARM64 ash validation functions in a private chroot.

No mount, switch_root or physical storage operations. Kernel/identity/mount I/O is
fixture data. Usage: sudo python3 tests/verify_host_init_ash.py PINNED_BUSYBOX
"""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]
ROOT_UUID = 'a7330000-0000-4000-8000-00000000000a'
STATE_UUID = 'e7330000-0000-4000-8000-000000000001'


def verify(busybox):
    if os.geteuid() != 0:
        raise ValueError('root required for private chroot')
    descriptor = os.open(busybox, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('BusyBox must be regular')
        data = stream.read(8 * 1024**2 + 1)
    expected = json.loads((REPO / 'configs/diagnostic-sources.lock.json').read_text())['busybox']['binary_sha256']
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError('BusyBox hash mismatch')
    with tempfile.TemporaryDirectory(prefix='a733-host-init-ash-', dir='/var/tmp') as temporary:
        root = Path(temporary)

        def put(name, contents, mode=0o644):
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(contents)
            target.chmod(mode)

        put('bin/busybox', data, 0o755)
        for directory in ('run', 'tmp', 'dev', 'proc', 'sys', 'var', 'home', 'root', 'etc'):
            (root / 'newroot' / directory).mkdir(parents=True, exist_ok=True)
        for directory in ('var', 'home', 'root', 'identity'):
            (root / 'newroot/run/a733-state' / directory).mkdir(parents=True, exist_ok=True)
        put('newroot/run/a733-state/.a733-state', f'a733-state-v1\n{STATE_UUID}\n'.encode())
        put('newroot/run/a733-state/identity/machine-id', b'0123456789abcdef0123456789abcdef\n')
        (root / 'newroot/run/a733-state/boot').mkdir(mode=0o700)
        put('newroot/etc/machine-id', b'')
        marker = b'ash marker fixture\n'
        put('newroot/usr/lib/orangepi-hypervisor/payload.json', marker)
        put('newroot/usr/lib/systemd/systemd', b'fixture, never executed', 0o755)
        put('newroot/usr/lib/aarch64-linux-gnu/ld-linux-aarch64.so.1', b'fixture, never executed', 0o755)
        (root / 'newroot/sbin').symlink_to('usr/sbin')
        (root / 'newroot/lib').symlink_to('usr/lib')
        (root / 'newroot/usr/sbin').mkdir()
        (root / 'newroot/usr/sbin/init').symlink_to('/lib/systemd/systemd')
        (root / 'newroot/usr/lib/ld-linux-aarch64.so.1').symlink_to('aarch64-linux-gnu/ld-linux-aarch64.so.1')
        put('proc/self/mountinfo', b'21 1 259:2 / /newroot ro,nodev - ext4 /dev/nvme0n1p2 ro\n')
        put('etc/a733-boot.conf', (f'STATE_UUID={STATE_UUID}\nKERNEL_RELEASE=6.6.98-a733-diag\n').encode())
        put('newroot/run/a733-state/boot/selected.conf',
            (f'SCHEMA=1\nSLOT=A\nROOT_UUID={ROOT_UUID}\nROOT_MARKER_SHA256={hashlib.sha256(marker).hexdigest()}\n'
             'KERNEL_RELEASE=6.6.98-a733-diag\n').encode(), 0o600)
        scripts = {}
        for source, target in (('configs/host-init', 'host-init'),
                               ('configs/host-storage-resolver.sh', 'etc/host-storage-resolver.sh')):
            contents = (REPO / source).read_bytes()
            put(target, contents)
            scripts[source] = {'bytes': len(contents), 'sha256': hashlib.sha256(contents).hexdigest()}
        # Stub only uname and mount. All parser/file/hash/permission operations
        # below execute the pinned ARM64 BusyBox in the private fixture tree.
        put('check.sh', b'''. /etc/host-storage-resolver.sh
. /host-init
hi_bb() {
    if [ "$1" = uname ]; then printf '6.6.98-a733-diag\n'; else /bin/busybox "$@"; fi
}
hi_mount() { [ "$1" = /newroot ]; }
hs_resolve_root() { case "$3" in A) printf 'ROOT_DEVICE=/dev/nvme0n1p2\\n' ;; B) printf 'ROOT_DEVICE=/dev/nvme0n1p3\\n' ;; esac; }
set -eu
hi_config
HI_STATE_MOUNT=/newroot/run/a733-state
STATE_DEVICE=/dev/nvme0n1p4
STATE_PARENT=nvme0n1
HI_MACHINE_ID=
hi_state_valid
hi_selection
hi_root
ROOT_MARKER_SHA256_SAVED=$ROOT_MARKER_SHA256
printf 'SCHEMA=1\\nSLOT=B\\nROOT_UUID=a7330000-0000-4000-8000-00000000000b\\nROOT_MARKER_SHA256=%s\\nKERNEL_RELEASE=6.6.98-a733-diag\\n' "$ROOT_MARKER_SHA256_SAVED" > /newroot/run/a733-state/boot/selected.conf
hi_selection
[ "$ROOT_DEVICE" = /dev/nvme0n1p3 ]
printf 'ROOT_UUID=bad\n' >> /etc/a733-boot.conf
if hi_config; then exit 1; fi
STATE_UUID=e7330000-0000-4000-8000-000000000001
printf 'wrong\n' > /newroot/run/a733-state/.a733-state
if hi_state_valid; then exit 1; fi
printf 'a733-state-v1\n%s\n' "$STATE_UUID" > /newroot/run/a733-state/.a733-state
printf '00000000000000000000000000000000\n' > /newroot/run/a733-state/identity/machine-id
if hi_state_valid; then exit 1; fi
printf '0123456789abcdef0123456789abcdef\\0' > /newroot/run/a733-state/identity/machine-id
if hi_state_valid; then exit 1; fi
printf '0123456789abcdef0123456789abcdef\n' > /newroot/run/a733-state/identity/machine-id
/bin/busybox chmod 666 /newroot/run/a733-state/identity/machine-id
if hi_state_valid; then exit 1; fi
printf '10 pinned ARM64 ash validation checks passed; both slots use unchanged bootstrap config\n'
''')
        environment = {'PATH': '/bin', 'LC_ALL': 'C'}
        # Check dependencies without invoking mount/switch_root.
        applets = subprocess.check_output(['/usr/sbin/chroot', str(root), '/bin/busybox', '--list'],
                                          env=environment, text=True).splitlines()
        required = {'cmp', 'stat', 'readlink', 'sha256sum', 'awk', 'grep', 'mount', 'umount', 'switch_root',
                    'test', 'cat', 'head', 'mkdir', 'sed', 'sleep', 'sh', 'ash', 'chmod', 'uname'}
        if not required <= set(applets):
            raise ValueError(f'BusyBox missing: {required - set(applets)}')
        result = subprocess.run(['/usr/sbin/chroot', str(root), '/bin/busybox', 'ash', '/check.sh'],
                                env=environment, capture_output=True, text=True)
        if result.returncode:
            raise ValueError(f'ash fixture failed ({result.returncode}): {result.stderr}')
        return {'classification': 'pinned ARM64 ash/chroot function fixtures; no real mounts or hardware acceptance',
                'busybox_sha256': expected, 'required_applets': sorted(required),
                'result': result.stdout.strip(), 'scripts': scripts}


if __name__ == '__main__':
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    print(json.dumps(verify(sys.argv[1]), indent=2))
