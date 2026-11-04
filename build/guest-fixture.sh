#!/usr/bin/env bash
# Preparation only: a separate virt guest kernel and disposable regular-file disk.
set -euo pipefail
export LC_ALL=C TZ=UTC
umask 022
[[ $# == 3 ]] || { echo "Usage: $0 GIT_KERNEL_TREE STATIC_AARCH64_BUSYBOX NEW_OUTPUT_DIRECTORY" >&2; exit 2; }
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
output=$(realpath -m -- "$3")
[[ $(dirname -- "$output") == "$repo/out" && $(basename -- "$output") =~ ^guest-fixture-[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || {
    echo 'Output must be a new direct repo/out/guest-fixture-NAME directory' >&2; exit 1;
}
[[ ! -e $output && ! -L $output ]] || { echo 'Output already exists; preserve it and choose a new name' >&2; exit 1; }
# Resolve inputs before creating outputs; source files are never changed or built in place.
kernel=$(realpath -e -- "$1")
busybox=$(realpath -e -- "$2")
[[ -d $kernel && -f $busybox && -x $busybox ]] || { echo 'Invalid kernel tree or BusyBox executable' >&2; exit 1; }
for tool in git python3 aarch64-linux-gnu-gcc make readelf cpio gzip tar mkfs.ext4 sha256sum dpkg-query; do
    command -v "$tool" >/dev/null || { echo "Missing host tool: $tool" >&2; exit 1; }
done
readarray -t pins < <(python3 - "$repo/configs/diagnostic-sources.lock.json" <<'PY'
import json, sys
lock = json.load(open(sys.argv[1]))
for value in (lock['kernel']['commit'], lock['busybox']['binary_sha256'],
              lock['kernel_compiler']['version'], lock['source_date_epoch']):
    print(value)
PY
)
[[ ${#pins[@]} == 4 ]] || { echo 'Invalid source lock' >&2; exit 1; }
[[ $(git -C "$kernel" rev-parse --show-toplevel) == "$kernel" && $(git -C "$kernel" rev-parse HEAD) == "${pins[0]}" ]] || {
    echo 'Kernel must be the pinned Git tree root' >&2; exit 1;
}
[[ -z $(git -C "$kernel" status --porcelain --untracked-files=no) ]] || { echo 'Kernel tracked files must be clean' >&2; exit 1; }
[[ $(sha256sum -- "$busybox" | cut -d ' ' -f1) == "${pins[1]}" ]] || { echo 'BusyBox hash does not match lock' >&2; exit 1; }
[[ $(aarch64-linux-gnu-gcc -dumpfullversion) == "${pins[2]}" ]] || { echo 'Use the pinned Ubuntu GCC 11.4.0 cross compiler' >&2; exit 1; }
[[ $(realpath -- "$(command -v aarch64-linux-gnu-gcc)") == /usr/bin/aarch64-linux-gnu-gcc-11 ]] || {
    echo 'Compiler must resolve to the Ubuntu-packaged GCC 11 executable' >&2; exit 1;
}
python3 - "$repo/configs/builder-packages.lock.tsv" <<'PY_PACKAGES'
from pathlib import Path
import subprocess
import sys
locked = {line.split('\t')[0]: line for line in Path(sys.argv[1]).read_text().splitlines()}
for package in ('gcc-11-aarch64-linux-gnu', 'binutils-aarch64-linux-gnu'):
    current = subprocess.check_output(['dpkg-query', '-W', '-f=${binary:Package}\t${Version}\t${Architecture}', package], text=True)
    if current != locked[package]:
        raise SystemExit(f'Pinned builder package mismatch: {package}')
PY_PACKAGES
header=$(readelf -h -- "$busybox")
[[ $header == *ELF64* && $header == *AArch64* && $header == *'little endian'* ]] || { echo 'BusyBox is not ARM64 ELF' >&2; exit 1; }
[[ $(readelf -l -- "$busybox") != *INTERP* && $(readelf -d -- "$busybox") != *'(NEEDED)'* ]] || { echo 'BusyBox must be static' >&2; exit 1; }
export SOURCE_DATE_EPOCH=${pins[3]}
export KBUILD_BUILD_TIMESTAMP='2026-11-04 00:00:00 UTC'
export KBUILD_BUILD_USER=builder KBUILD_BUILD_HOST=jammy KBUILD_BUILD_VERSION=1
# Atomic mkdir refuses collisions. Everything generated stays under this new output.
mkdir -p -- "$repo/out"
mkdir -- "$output"
mkdir -- "$output/src" "$output/logs" "$output/seed" "$output/initramfs-root"
cp -- "$repo/configs/diagnostic-sources.lock.json" "$repo/configs/builder-packages.lock.tsv" "$output/"
git -C "$kernel" archive HEAD | tar -x -C "$output/src"
# Empty .scmversion prevents setlocalversion discovering a surrounding Git checkout.
: > "$output/src/.scmversion"
cp -- "$repo/configs/guest-kernel.fragment" "$output/guest-kernel.fragment"
kmake() { make -C "$output/src" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- "$@"; }
kmake tinyconfig > "$output/logs/kernel-config.log" 2>&1
(cd "$output/src"; scripts/kconfig/merge_config.sh -m .config "$output/guest-kernel.fragment") >> "$output/logs/kernel-config.log" 2>&1
kmake olddefconfig >> "$output/logs/kernel-config.log" 2>&1
required=(ARCH_VEXPRESS ARM_PSCI_FW SMP ARM_GIC ARM_GIC_V3 ARM_GIC_V3_ITS PCI PCI_MSI PCI_HOST_GENERIC
    VIRTIO_MENU VIRTIO VIRTIO_PCI VIRTIO_BLK VIRTIO_NET BLOCK BLK_DEV EXT4_FS NET INET PACKET UNIX NETDEVICES NET_CORE BLK_DEV_INITRD
    RD_GZIP DEVTMPFS PROC_FS SYSFS TMPFS BINFMT_ELF BINFMT_SCRIPT PRINTK TTY SERIAL_AMBA_PL011 SERIAL_AMBA_PL011_CONSOLE POSIX_TIMERS FUTEX)
for symbol in "${required[@]}"; do
    grep -qx "CONFIG_${symbol}=y" "$output/src/.config" || { echo "Required guest symbol missing: $symbol" >&2; exit 1; }
done
grep -qx '# CONFIG_MODULES is not set' "$output/src/.config"
grep -qx '# CONFIG_WLAN is not set' "$output/src/.config"
! grep -Eq '^CONFIG_BCMDHD=[ym]' "$output/src/.config"
grep -qx '# CONFIG_LOCALVERSION_AUTO is not set' "$output/src/.config"
kmake -j2 Image > "$output/logs/kernel-build.log" 2>&1
cp -- "$output/src/arch/arm64/boot/Image" "$output/Image"
cp -- "$output/src/.config" "$output/kernel.config"
root=$output/initramfs-root
mkdir -p -- "$root"/{bin,dev,proc,sys,tmp,run,mnt,etc,usr/share/licenses}
cp -- "$busybox" "$root/bin/busybox"
ln -s busybox "$root/bin/sh"
cp -- "$repo/configs/guest-init" "$root/init"
cp -- "$repo/licenses/GPL-2.0.txt" "$repo/licenses/busybox-copyright.txt" "$root/usr/share/licenses/"
chmod 0755 "$root/init" "$root/bin/busybox"
cat > "$root/etc/udhcpc.script" <<'HOOK'
#!/bin/sh
case "$1" in
    bound|renew)
        ip addr flush dev "$interface" || exit 1
        ip addr add "$ip/${mask:-${subnet:-255.255.255.0}}" dev "$interface" || exit 1
        for gateway in $router; do
            ip route replace default via "$gateway" dev "$interface" || exit 1
            break
        done
        ;;
esac
HOOK
chmod 0755 "$root/etc/udhcpc.script"
find "$root" -type d -exec chmod 0755 {} +
find "$root" -exec touch -h -d "@$SOURCE_DATE_EPOCH" {} +
(cd "$root"; find . -print0 | sort -z | cpio --null -o --format=newc --owner=0:0 --reproducible --quiet) | gzip -n -9 > "$output/initramfs.cpio.gz"
printf 'a733-kvm-guest-fixture-v1\n' > "$output/seed/fixture-id"
printf '0\n' > "$output/seed/counter"
find "$output/seed" -exec touch -d "@$SOURCE_DATE_EPOCH" {} +
# This fresh private directory contains only a newly-created regular file, no device paths.
( set -o noclobber; : > "$output/guest-disk.raw" )
[[ -f $output/guest-disk.raw && ! -L $output/guest-disk.raw ]]
truncate -s 134217728 "$output/guest-disk.raw"
E2FSPROGS_FAKE_TIME=$SOURCE_DATE_EPOCH mkfs.ext4 -q -F -b 4096 -O ^has_journal \
    -E lazy_itable_init=0,lazy_journal_init=0,hash_seed=d67f33ba-e201-46bc-980b-61901bbd1156 -U d67f33ba-e201-46bc-980b-61901bbd1156 \
    -L kvm-fixture -d "$output/seed" "$output/guest-disk.raw" > "$output/logs/disk-build.log" 2>&1
cp -- "$repo/build/guest-fixture.sh" "$output/guest-fixture.sh"
cp -- "$repo/configs/guest-init" "$output/guest-init"
cp -R -- "$repo/licenses" "$output/licenses"
# src/LICENSES and src/COPYING are retained alongside the full pinned source.
(cd "$output"; sha256sum Image kernel.config initramfs.cpio.gz guest-disk.raw \
    guest-kernel.fragment guest-init guest-fixture.sh diagnostic-sources.lock.json builder-packages.lock.tsv > SHA256SUMS)
python3 - "$output" "${pins[0]}" "${pins[1]}" "${pins[2]}" <<'PY'
import hashlib, json, pathlib, sys
root = pathlib.Path(sys.argv[1])
files = {}
for line in (root / 'SHA256SUMS').read_text().splitlines():
    digest, name = line.split(maxsplit=1)
    files[name] = {'sha256': digest, 'bytes': (root / name).stat().st_size}
(root / 'artifact-manifest.json').write_text(json.dumps({
    'schema': 1, 'purpose': 'ARM64 QEMU virt KVM guest fixture; preparation only',
    'hardware_acceptance': 'pending', 'kernel_commit': sys.argv[2],
    'busybox_binary_sha256': sys.argv[3], 'compiler_version': sys.argv[4],
    'source_date_epoch': 1793750400, 'compilation_jobs': 2,
    'disk_fixture_id': 'a733-kvm-guest-fixture-v1', 'initial_counter': 0,
    'disk_uuid': 'd67f33ba-e201-46bc-980b-61901bbd1156',
    'network_probe': 'DHCP and ICMP to 10.0.2.2; restricted usernet behavior unproven',
    'files': files,
}, indent=2) + '\n')
PY
printf 'Prepared guest fixture: %s (hardware KVM acceptance remains pending)\n' "$output"
