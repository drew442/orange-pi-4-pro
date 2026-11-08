#!/usr/bin/env bash
# Host-only guards and packaging. No kernel compilation, mounts, QEMU or KVM evidence.
set -euo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT
bash -n "$repo/build/guest-fixture.sh"
sh -n "$repo/configs/guest-init"
sh -n "$repo/configs/guest-network-cgi"
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
# Run only the actual identity function, never guest PID 1 on the host.
identity = init[init.index('fixture_disk_identity() {'):init.index('fixture_disk_identity /dev/vda')]
probe = work / 'identity.sh'
probe.write_text(identity + '\nfixture_disk_identity "$1"\n')
fields = [(1080, bytes.fromhex('53ef')),
          (1116, bytes.fromhex('28000000c20200006b040000')),
          (1128, bytes.fromhex('d67f33bae20146bc980b61901bbd1156')),
          (1144, bytes.fromhex('6b766d2d666978747572650000000000'))]
valid = bytearray(1160)
for offset, value in fields:
    valid[offset:offset + len(value)] = value
sample = work / 'identity.raw'
def accepts(data):
    sample.write_bytes(data)
    return subprocess.run(['sh', str(probe), str(sample)], capture_output=True).returncode == 0
assert accepts(valid)
for offset, value in fields:
    corrupt = valid.copy()
    corrupt[offset] ^= 1
    assert not accepts(corrupt), offset
    assert not accepts(valid[:offset + len(value) - 1]), offset
assert subprocess.run(['sh', str(probe), str(work / 'missing')], capture_output=True).returncode != 0
assert subprocess.run(['sh', str(probe), str(work)], capture_output=True).returncode != 0
assert init.index('"$$" -eq 1') < init.index('mount -t proc')
assert init.index('ro,noload /dev/vda') < init.index('cat /mnt/fixture-id') < init.index('rw /dev/vda')
assert init.index('umount /mnt || fail disk-unmount\nprintf') < init.index('PASS disk')
assert 'ping -I eth0' not in init
assert init.index('udhcpc -f') < init.index('httpd -f') < init.index('PASS network') < init.index('poweroff -f')
assert init.index('umount /mnt || fail disk-unmount\nprintf') < init.index('httpd -f')
assert 'tries" -le 120' in init
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
output.mkdir(mode=0o700)
(output / 'initramfs-root').mkdir()
(output / 'seed').mkdir()
(output / 'logs').mkdir()
start = recipe.index('root=$output/initramfs-root\n')
end = recipe.index('cp -- "$repo/build/guest-fixture.sh"', start)
commands = recipe[start:end]
# Only this host-only fixture bypasses the known-fixed-storage/Jammy policies.
# Production exposes no override: the actual builder runs the exact CLI.
shim = work / 'seed-shim.py'
shim.write_text('''import importlib.util, json, os, pathlib, sys
spec=importlib.util.spec_from_file_location('seed_test', pathlib.Path(sys.argv[1])/'build/guest-seed.py')
S=importlib.util.module_from_spec(spec);spec.loader.exec_module(S)
check=S.TRUST.check_directory
temporary=pathlib.Path('/tmp').stat()
def fixture_directory(info):
    if (info.st_dev,info.st_ino)!=(temporary.st_dev,temporary.st_ino):check(info)
S.TRUST.check_directory=fixture_directory
S.STATE.validate_workspace_storage=lambda path:None
S.check_versions=lambda:None
print(json.dumps(S.build(pathlib.Path(sys.argv[2]))))
''')
invocation = 'python3 -I -B "$repo/build/guest-seed.py" "$output/guest-disk.raw"'
assert commands.count(invocation) == 1
commands = commands.replace(invocation, 'python3 "$seed_shim" "$repo" "$output/guest-disk.raw"')
env = dict(os.environ, repo=str(repo), output=str(output),
           busybox=shutil.which('true'), seed_shim=str(shim), SOURCE_DATE_EPOCH='1793750400', LC_ALL='C')
packaging = subprocess.run(['bash', '-euo', 'pipefail', '-c', commands], env=env,
                           capture_output=True, text=True)
assert packaging.returncode == 0, (packaging.stderr +
    (output / 'logs/disk-build.log').read_text())[-8000:]
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
             './usr/share/licenses/busybox-copyright.txt', './etc/udhcpc.script', './www/cgi-bin/probe'):
    assert path.removeprefix('./') in [item.removeprefix('./') for item in listing], path
assert (output / 'initramfs-root/init').read_bytes() == (repo / 'configs/guest-init').read_bytes()
assert (output / 'initramfs-root/www/cgi-bin/probe').read_bytes() == (repo / 'configs/guest-network-cgi').read_bytes()
hook = (output / 'initramfs-root/etc/udhcpc.script').read_text()
assert 'ip addr add' in hook and 'ip route replace' in hook
assert 'resolv.conf' not in hook and '/mnt' not in hook
print('PASS host-only guest guards, exact packaging sections and disposable ext4 seed; KVM untested')
PY
