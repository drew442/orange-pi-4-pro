#!/usr/bin/env bash
# Run on the recorded Ubuntu 22.04 builder. Two compilation jobs, sequential trees.
set -euo pipefail
export LC_ALL=C TZ=UTC SOURCE_DATE_EPOCH=1793750400
[[ $# == 1 ]] || { echo "Usage: $0 DEDICATED_WORKSPACE" >&2; exit 2; }
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
workspace=$(realpath -m "$1")
mkdir -p "$workspace"
[[ ! -e $workspace/artifacts/a733-diagnostic.img ]] || {
    echo 'Completed image already exists; preserve/move artifacts before a new run' >&2; exit 1;
}
exec 9>"$workspace/.diagnostic-build.lock"
flock -n 9 || { echo 'A diagnostic build already owns this workspace' >&2; exit 1; }
[[ $(. /etc/os-release; echo "$VERSION_ID") == 22.04 && $(uname -m) == x86_64 ]]
[[ $(aarch64-linux-gnu-gcc -dumpfullversion) == 11.4.0 ]]
dpkg-query -W -f='${binary:Package}\t${Version}\t${Architecture}\n' > "$workspace/current-packages.tsv"
diff -u "$repo/configs/builder-packages.lock.tsv" "$workspace/current-packages.tsv"
(( $(df --output=avail -B1 "$workspace" | tail -1) > 15000000000 )) || {
    echo 'Need at least 15 GB free before this build (measured planning guard)' >&2; exit 1;
}
python3 "$repo/build/fetch-diagnostic.py" "$workspace"
cd "$workspace"
mkdir -p logs artifacts
monitor_resources() {
    while :; do
        date -u
        free -b
        df -B1 .
        sleep 30
    done
}
monitor_resources > logs/resource-samples.txt 2>&1 &
monitor_pid=$!
toolchain_root=''
uboot_tree=''
cleanup() {
    kill "$monitor_pid" 2>/dev/null || :
    wait "$monitor_pid" 2>/dev/null || :
    [[ -z $toolchain_root ]] || rm -rf "$toolchain_root"
    [[ -z $uboot_tree ]] || git -C "$workspace/u-boot" worktree remove --force "$uboot_tree"
}
trap cleanup EXIT
date -u > logs/build-start.txt
free -h > logs/resources-start.txt
df -h . /tmp >> logs/resources-start.txt
toolchain=gcc-linaro-7.4.1-2019.02-x86_64_arm-linux-gnueabi
toolchain_root=$(mktemp -d "$workspace/toolchain.XXXXXX")
tar -xJf "downloads/$toolchain.tar.xz" -C "$toolchain_root"
uboot_cross=$toolchain_root/$toolchain/bin/arm-linux-gnueabi-
mkdir -p busybox
dpkg-deb -x downloads/busybox-static_1.30.1-7ubuntu3.1_arm64.deb busybox
"$repo/build/preflight.sh" "$workspace/busybox/bin/busybox" > logs/preflight.log 2>&1
export KBUILD_BUILD_TIMESTAMP='2026-11-04 00:00:00 UTC'
export KBUILD_BUILD_USER=builder KBUILD_BUILD_HOST=jammy KBUILD_BUILD_VERSION=1
cp vendor/external/config/kernel/linux-sun60iw2-current-a733.config kernel/.config
kernel/scripts/config --file kernel/.config --enable VIRTUALIZATION --enable KVM \
    --enable IKCONFIG --enable IKCONFIG_PROC --set-str SYSTEM_TRUSTED_KEYS '' --set-str SYSTEM_REVOCATION_KEYS ''
make -C kernel ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- olddefconfig > logs/kernel-config.log 2>&1
grep -qx CONFIG_KVM=y kernel/.config
make -C kernel -j2 ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- LOCALVERSION=-a733-diag \
    Image modules dtbs > logs/kernel-build.log 2>&1
# Keep compiler output from the old bootloader separate; never reuse other GCC objects.
uboot_tree=$(mktemp -d "$workspace/uboot-work.XXXXXX")
git -C u-boot worktree add --detach "$uboot_tree" HEAD > logs/uboot-build.log 2>&1
git -C "$uboot_tree" apply "$repo/build/patches/uboot-env-nowhere-size.patch"
# This vendor Makefile uses exported PWD for DTS_PATH; make -C alone leaves
# the caller's PWD and fails late in device-tree generation.
mkdir -p "$uboot_tree/vendor-export/bin"
uboot_make() {
    (cd "$uboot_tree"; make CROSS_COMPILE="$uboot_cross" \
        LICHEE_CHIP_CONFIG_DIR="$uboot_tree/vendor-export" LICHEE_BUSSINESS='' \
        LICHEE_PLAT_OUT="$uboot_tree/vendor-export" "$@")
}
uboot_make -j2 sun60iw2p1_t736_defconfig >> logs/uboot-build.log 2>&1
kernel/scripts/config --file "$uboot_tree/.config" --disable ENV_IS_IN_EXT4 --enable ENV_IS_NOWHERE \
    --disable CMD_SAVEENV --enable BOOTCMD_SKIP_MISC --disable SUNXI_SWITCH_SYSTEM --disable BOOTCOUNT_LIMIT \
    --set-str BOOTCOMMAND 'setenv boot_targets mmc0; run distro_bootcmd' --disable SUNXI_OTA_TURNNING
uboot_make olddefconfig >> logs/uboot-build.log 2>&1
grep -qx CONFIG_ENV_IS_NOWHERE=y "$uboot_tree/.config"
grep -qx CONFIG_ENV_SIZE=0x20000 "$uboot_tree/.config"
grep -qx CONFIG_BOOTCMD_SKIP_MISC=y "$uboot_tree/.config"
grep -Fxq 'CONFIG_BOOTCOMMAND="setenv boot_targets mmc0; run distro_bootcmd"' "$uboot_tree/.config"
! grep -qx CONFIG_SUNXI_SWITCH_SYSTEM=y "$uboot_tree/.config"
! grep -qx CONFIG_BOOTCOUNT_LIMIT=y "$uboot_tree/.config"
! grep -qx CONFIG_ENV_IS_IN_EXT4=y "$uboot_tree/.config"
! grep -qx CONFIG_CMD_SAVEENV=y "$uboot_tree/.config"
! grep -qx CONFIG_SUNXI_OTA_TURNNING=y "$uboot_tree/.config"
uboot_make -j2 >> logs/uboot-build.log 2>&1
pack=$workspace/artifacts/pack
mkdir -p "$pack"
cp -R vendor/external/packages/pack-uboot/sun60iw2/bin/. "$pack/"
cp "$pack/boot0_sdcard_a733.fex" "$pack/boot0_sdcard.fex"
cp "$uboot_tree/u-boot.bin" "$pack/u-boot.fex"
cp "$pack/sys_config/sys_config.fex" "$pack/sys_config.fex"
cp "$pack/dts/u-boot-current.dts" "$pack/dts/orangepi4pro-u-boot.dts"
export PATH="$workspace/vendor/external/packages/pack-uboot/tools:$PATH"
(
    cd "$pack"
    dtc -p 2048 -W no-unit_address_vs_reg -@ -O dtb -o orangepi4pro-u-boot.dtb -b 0 dts/u-boot-current.dts
    busybox unix2dos sys_config.fex
    script sys_config.fex
    cp orangepi4pro-u-boot.dtb sunxi.fex
    update_dtb sunxi.fex 4096
    update_uboot -no_merge u-boot.fex sys_config.bin
    busybox unix2dos boot_package.cfg
    dragonsecboot -pack boot_package.cfg
) > logs/boot-pack.log 2>&1
mkdir -p artifacts/modules
make -C kernel -j2 ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- LOCALVERSION=-a733-diag \
    INSTALL_MOD_PATH="$workspace/artifacts/modules" INSTALL_MOD_STRIP=1 modules_install > logs/modules-install.log 2>&1
find artifacts/modules/lib/modules -type l \( -name build -o -name source \) -delete
"$repo/build/diagnostic-initramfs.sh" "$workspace/busybox/bin/busybox" \
    "$workspace/artifacts/initramfs.cpio.gz" "$workspace/artifacts/modules/lib/modules"
cp kernel/arch/arm64/boot/Image artifacts/Image
cp kernel/arch/arm64/boot/dts/allwinner/sun60i-a733-orangepi-4-pro.dtb artifacts/board.dtb
cp kernel/.config artifacts/kernel.config
cp "$uboot_tree/.config" artifacts/uboot.config
cp artifacts/pack/boot0_sdcard.fex artifacts/pack/boot_package.fex artifacts/
mkimage -A arm -O linux -T kernel -C none -a 0x41000000 -e 0x41000000 -n 'Linux kernel' -d artifacts/Image artifacts/uImage
mkimage -A arm -O linux -T ramdisk -C gzip -n uInitrd -d artifacts/initramfs.cpio.gz artifacts/uInitrd
"$repo/build/assemble-diagnostic.sh" "$workspace/artifacts" "$workspace/artifacts/a733-diagnostic.img"
gzip -n -c artifacts/a733-diagnostic.img > artifacts/a733-diagnostic.img.gz
( cd artifacts; sha256sum a733-diagnostic.img a733-diagnostic.img.gz Image board.dtb \
    initramfs.cpio.gz uImage uInitrd boot0_sdcard.fex boot_package.fex kernel.config uboot.config > SHA256SUMS )
cp "$repo/configs/diagnostic-sources.lock.json" "$repo/configs/builder-packages.lock.tsv" artifacts/
cp "$repo/build/patches/uboot-env-nowhere-size.patch" artifacts/
( cd artifacts; sha256sum uboot-env-nowhere-size.patch >> SHA256SUMS )
mkdir -p artifacts/notices artifacts/busybox-source
cp -R "$repo/licenses/." artifacts/notices/
cp kernel/COPYING artifacts/notices/kernel-COPYING
cp kernel/LICENSES/exceptions/Linux-syscall-note artifacts/notices/
cp u-boot/Licenses/README artifacts/notices/uboot-README
cp downloads/busybox_1.30.1* artifacts/busybox-source/
( cd artifacts/busybox-source; sha256sum busybox_*.dsc busybox_*.tar.* > SHA256SUMS )
tar --sort=name --mtime="@$SOURCE_DATE_EPOCH" --owner=0 --group=0 --numeric-owner \
    --exclude=__pycache__ -C "$repo" -cf - build configs licenses | gzip -n > artifacts/build-recipe.tar.gz
( cd artifacts; sha256sum build-recipe.tar.gz diagnostic-sources.lock.json builder-packages.lock.tsv >> SHA256SUMS )
python3 - "$workspace/artifacts" <<'PY'
import hashlib
import json
from pathlib import Path
import sys

directory = Path(sys.argv[1])
files = {}
for line in (directory / 'SHA256SUMS').read_text().splitlines():
    checksum, name = line.split(maxsplit=1)
    files[name] = {'bytes': (directory / name).stat().st_size, 'sha256': checksum}
(directory / 'artifact-manifest.json').write_text(json.dumps({
    'schema': 1, 'board': 'Orange Pi 4 Pro A733',
    'classification': 'diagnostic candidate, hardware untested', 'files': files,
}, indent=2) + '\n')
PY
free -h > logs/resources-end.txt
df -h . /tmp >> logs/resources-end.txt
date -u > logs/build-end.txt
echo "Diagnostic candidate ready in $workspace/artifacts; physical boot remains untested."
