#!/usr/bin/env bash
# Build only RAM-based diagnostic userspace; this does not create a bootable SD.
set -euo pipefail
export LC_ALL=C
export TZ=UTC
umask 022
usage() {
    echo "Usage: $0 STATIC_AARCH64_BUSYBOX OUTPUT_CPIO_GZ [MODULES_DIR]" >&2
    echo 'MODULES_DIR contains release directories as installed below /lib/modules.' >&2
    exit 2
}
[[ $# == 2 || $# == 3 ]] || usage
for tool in readelf cpio gzip find sort touch mktemp cp realpath; do
    command -v "$tool" >/dev/null || { echo "Missing host tool: $tool" >&2; exit 1; }
done
busybox=$(realpath -- "$1")
output=$(realpath -m -- "$2")
[[ -f $busybox && -x $busybox ]] || { echo 'BusyBox must be an executable regular file' >&2; exit 1; }
header=$(readelf -h -- "$busybox")
[[ $header == *'Class:'*'ELF64'* && $header == *'Machine:'*'AArch64'* &&
   $header == *'little endian'* && ( $header == *'Type:'*'EXEC'* || $header == *'Type:'*'DYN'* ) ]] || {
    echo 'BusyBox must be an ELF64 AArch64 executable' >&2; exit 1;
}
program_headers=$(readelf -l -- "$busybox")
dynamic_entries=$(readelf -d -- "$busybox")
if [[ $program_headers == *INTERP* || $dynamic_entries == *'(NEEDED)'* ]]; then
    echo 'BusyBox must be statically linked (no interpreter or needed shared libraries)' >&2
    exit 1
fi
[[ $output != "$busybox" ]] || { echo 'Output must differ from BusyBox input' >&2; exit 1; }
[[ ! -e $output || -f $output ]] || { echo 'Output must be a regular file path' >&2; exit 1; }
recipe_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
work=$(mktemp -d)
archive=''
trap 'rm -rf -- "$work"; [[ -z $archive ]] || rm -f -- "$archive"' EXIT
root=$work/root
mkdir -p "$root"/{bin,sbin,dev,proc,sys,run,tmp,etc,lib/modules}
mkdir -p "$root/usr/share/licenses"
cp -- "$recipe_dir/../licenses/GPL-2.0.txt" "$recipe_dir/../licenses/busybox-copyright.txt" "$root/usr/share/licenses/"
cp -- "$busybox" "$root/bin/busybox"
chmod 0755 "$root/bin/busybox"
ln -s busybox "$root/bin/sh"
# CONFIG_MODPROBE_PATH=/sbin/modprobe: kernel PHY autoload happens while the
# platform module creates MDIO devices, before our one-time modalias snapshot.
ln -s ../bin/busybox "$root/sbin/modprobe"
cp -- "$recipe_dir/../configs/diagnostic-init" "$root/init"
chmod 0755 "$root/init"
# The DHCP hook changes RAM-backed network state only. No lease file or daemon.
cat > "$root/etc/udhcpc.script" <<'HOOK'
#!/bin/sh
case "$1" in
    bound|renew)
        ip addr flush dev "$interface"
        ip addr add "$ip/${mask:-${subnet:-255.255.255.0}}" dev "$interface"
        for gateway in $router; do
            ip route replace default via "$gateway" dev "$interface"
            break
        done
        ;;
esac
HOOK
chmod 0755 "$root/etc/udhcpc.script"
if [[ $# == 3 ]]; then
    [[ -d $3 ]] || { echo 'MODULES_DIR must be a directory' >&2; exit 1; }
    # Stage only files and directories: dangling vendor build/source symlinks
    # must be removed by the caller, and cannot escape the archived tree.
    if [[ -n $(find "$3" -mindepth 1 ! -type d ! -type f -print -quit) ]]; then
        echo 'MODULES_DIR may contain only regular files and directories' >&2
        exit 1
    fi
    cp --preserve=mode -R -- "$3/." "$root/lib/modules/"
fi
# Fixed metadata, ordering and gzip header make identical inputs reproducible.
find "$root" -type d -exec chmod 0755 {} +
find "$root" -exec touch -h -d '@0' {} +
mkdir -p -- "$(dirname -- "$output")"
archive=$(mktemp "${output}.tmp.XXXXXX")
(
    cd -- "$root"
    find . -print0 | sort -z | cpio --null -o --format=newc --owner=0:0 --reproducible --quiet
) | gzip -n -9 > "$archive"
mv -f -- "$archive" "$output"
archive=''
echo "Created diagnostic initramfs: $output"
