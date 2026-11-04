#!/usr/bin/env bash
# Host-only guards and packaging. No kernel compilation, mounts, QEMU or KVM evidence.
set -euo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT
bash -n "$repo/build/guest-fixture.sh"
sh -n "$repo/configs/guest-init"
reject() {
    if "$@" > "$work/rejection.log" 2>&1; then
        echo "Expected rejection: $*" >&2
        exit 1
    fi
}
reject bash "$repo/build/guest-fixture.sh"
reject bash "$repo/build/guest-fixture.sh" /missing /missing /dev/null
reject bash "$repo/build/guest-fixture.sh" /missing /missing "$repo/out/guest-fixture-name/child"
reject bash "$repo/build/guest-fixture.sh" /missing /missing "$repo/out/guest-fixture-../escape"
# The guard must stop accidental host execution before even calling BusyBox.
reject sh "$repo/configs/guest-init"
grep -q 'Guest init requires PID 1' "$work/rejection.log"
python3 - "$repo" "$work" <<'PY'
from pathlib import Path
import os
import shutil
import subprocess
import sys

repo, work = map(Path, sys.argv[1:])
recipe = (repo / 'build/guest-fixture.sh').read_text()
init = (repo / 'configs/guest-init').read_text()
fragment = (repo / 'configs/guest-kernel.fragment').read_text()
assert 'mkfs' not in init
assert init.index('"$$" -eq 1') < init.index('mount -t proc')
assert init.index('ro,noload /dev/vda') < init.index('cat /mnt/fixture-id') < init.index('rw /dev/vda')
assert init.index('umount /mnt || fail disk-unmount\nprintf') < init.index('PASS disk')
assert init.index('ping -I eth0') < init.index('PASS network') < init.index('poweroff -f')
assert 'tcg' not in recipe
for symbol in ('PCI_HOST_GENERIC', 'SERIAL_AMBA_PL011_CONSOLE', 'VIRTIO_PCI', 'ARM_GIC_V3_ITS',
               'BINFMT_ELF', 'BINFMT_SCRIPT'):
    assert f'CONFIG_{symbol}=y' in fragment
    assert symbol in recipe
assert 'git -C "$kernel" archive HEAD' in recipe
assert '--untracked-files=no' in recipe
assert 'binary_sha256' in recipe
assert 'hardware_acceptance\': \'pending' in recipe
assert 'kmake -j2 Image' in recipe
assert 'CONFIG_MODULES is not set' in recipe
assert '# CONFIG_WLAN is not set' in fragment
assert 'src/.scmversion' in recipe
# Execute the actual staging/package and seed sections without kernel compilation.
# The host ELF placeholder is used solely to test archive contents, never executed.
output = work / 'artifact'
output.mkdir()
(output / 'initramfs-root').mkdir()
(output / 'seed').mkdir()
(output / 'logs').mkdir()
start = recipe.index('root=$output/initramfs-root\n')
end = recipe.index('cp -- "$repo/build/guest-fixture.sh"', start)
commands = recipe[start:end]
env = dict(os.environ, repo=str(repo), output=str(output),
           busybox=shutil.which('true'), SOURCE_DATE_EPOCH='1793750400', LC_ALL='C')
subprocess.run(['bash', '-euo', 'pipefail', '-c', commands], env=env, check=True)
disk = output / 'guest-disk.raw'
assert disk.is_file() and not disk.is_symlink()
assert disk.stat().st_size == 134217728
header = subprocess.check_output(['dumpe2fs', '-h', str(disk)], stderr=subprocess.DEVNULL, text=True)
assert 'd67f33ba-e201-46bc-980b-61901bbd1156' in header
assert 'kvm-fixture' in header
features = next(line for line in header.splitlines() if line.startswith('Filesystem features:'))
assert 'has_journal' not in features
for name, expected in [('fixture-id', 'a733-kvm-guest-fixture-v1\n'), ('counter', '0\n')]:
    value = subprocess.check_output(['debugfs', '-R', f'cat /{name}', str(disk)], stderr=subprocess.DEVNULL, text=True)
    assert value == expected, (name, value)
archive = output / 'initramfs.cpio.gz'
listing = subprocess.run(['bash', '-o', 'pipefail', '-c', 'gzip -dc "$1" | cpio -it --quiet', 'sh', str(archive)],
                         check=True, capture_output=True, text=True).stdout.splitlines()
for path in ('./init', './bin/busybox', './bin/sh', './usr/share/licenses/GPL-2.0.txt',
             './usr/share/licenses/busybox-copyright.txt', './etc/udhcpc.script'):
    assert path.removeprefix('./') in [item.removeprefix('./') for item in listing], path
assert (output / 'initramfs-root/init').read_bytes() == (repo / 'configs/guest-init').read_bytes()
hook = (output / 'initramfs-root/etc/udhcpc.script').read_text()
assert 'ip addr add' in hook and 'ip route replace' in hook
assert 'resolv.conf' not in hook and '/mnt' not in hook
print('PASS host-only guest guards, exact packaging sections and disposable ext4 seed; KVM untested')
PY
