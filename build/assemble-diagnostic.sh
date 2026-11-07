#!/usr/bin/env bash
# Assemble only a new regular image file. This script never opens physical disks.
set -euo pipefail
export LC_ALL=C TZ=UTC
[[ $# == 2 || $# == 3 ]] || { echo "Usage: $0 INPUT_DIRECTORY NEW_OUTPUT_IMG [BOOT_COMMAND]" >&2; exit 2; }
inputs=$(realpath "$1")
output=$(realpath -m "$2")
[[ ! -e $output && ! -L $output ]] || { echo 'Output must not exist' >&2; exit 1; }
[[ $output != /dev/* ]] || { echo 'Device paths forbidden' >&2; exit 1; }
for name in boot0_sdcard.fex boot_package.fex uImage uInitrd board.dtb; do
    [[ -s $inputs/$name && -f $inputs/$name ]] || { echo "Missing $name" >&2; exit 1; }
done
# Pinned vendor layout: boot0 at 8 KiB; package at 2050 * 8 KiB;
# partition 1 starts at 32 MiB. Never allow blobs to cross these boundaries.
(( $(stat -c %s "$inputs/boot0_sdcard.fex") <= 16793600 - 8192 ))
(( $(stat -c %s "$inputs/boot_package.fex") <= 33554432 - 16793600 ))
# Pinned 32-bit U-Boot default load regions: kernel 0x41000000,
# DTB 0x43000000, active script 0x43100000, initrd 0x43300000.
# A larger image requires an explicitly reviewed memory map, not silent overlap.
(( $(stat -c %s "$inputs/uImage") < 33554432 )) || {
    echo 'uImage overlaps the vendor DTB load address' >&2; exit 1;
}
(( $(stat -c %s "$inputs/board.dtb") + 65536 < 1048576 )) || {
    echo 'DTB resize overlaps the active boot script' >&2; exit 1;
}
# Conservative 64 MiB initrd packaging bound; physical RAM/reservations still
# require serial validation before this candidate can be called bootable.
(( $(stat -c %s "$inputs/uInitrd") < 67108864 )) || {
    echo 'Initrd exceeds diagnostic load-region budget' >&2; exit 1;
}
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
boot_command=$repo/configs/diagnostic-boot.cmd
if [[ $# == 3 ]]; then
    boot_command=$3
    [[ -f $boot_command && ! -L $boot_command && -s $boot_command ]] || {
        echo 'Boot command must be a nonempty regular file, not a symlink' >&2; exit 1;
    }
fi
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/fs/boot/dtb/allwinner" "$(dirname "$output")"
cp "$inputs/uImage" "$inputs/uInitrd" "$work/fs/boot/"
cp "$inputs/board.dtb" "$work/fs/boot/dtb/allwinner/sun60i-a733-orangepi-4-pro.dtb"
cp "$boot_command" "$work/fs/boot/boot.cmd"
export SOURCE_DATE_EPOCH=1793750400
mkimage -C none -A arm -T script -d "$work/fs/boot/boot.cmd" "$work/fs/boot/boot.scr"
find "$work/fs" -exec touch -h -d "@$SOURCE_DATE_EPOCH" {} +
truncate -s 224M "$work/partition.ext4"
# Legacy U-Boot ext4 reader: omit journal, metadata checksum and 64-bit features.
# Kernel never mounts this filesystem. Image reproducibility still needs two builds.
E2FSPROGS_FAKE_TIME=$SOURCE_DATE_EPOCH mkfs.ext4 -q -F -b 4096 -L A733DIAG \
    -U a7330000-0000-4000-8000-000000000001 \
    -O ^has_journal,^metadata_csum,^64bit \
    -E lazy_itable_init=0,hash_seed=a7330000-0000-4000-8000-000000000001 \
    -d "$work/fs" "$work/partition.ext4"
# Import copies source ctime, which touch cannot set. Normalize it using the
# filesystem editor on the generated partition file (never on a physical disk).
{
    printf 'set_inode_field / ctime %s\n' "$SOURCE_DATE_EPOCH"
    while IFS= read -r name; do
        printf 'set_inode_field /%s ctime %s\n' "$name" "$SOURCE_DATE_EPOCH"
        printf 'set_inode_field /%s uid 0\nset_inode_field /%s gid 0\n' "$name" "$name"
    done < <(cd "$work/fs"; find boot -print | sort)
} > "$work/normalize.debugfs"
E2FSPROGS_FAKE_TIME=$SOURCE_DATE_EPOCH debugfs -w -f "$work/normalize.debugfs" "$work/partition.ext4"
e2fsck -fn "$work/partition.ext4"
truncate -s 256M "$work/disk.img"
printf 'label: dos\nlabel-id: 0xa7330001\nunit: sectors\n\nstart=65536, size=458752, type=83, bootable\n' | sfdisk "$work/disk.img"
dd if="$inputs/boot0_sdcard.fex" of="$work/disk.img" bs=8192 seek=1 conv=notrunc status=none
dd if="$inputs/boot_package.fex" of="$work/disk.img" bs=8192 seek=2050 conv=notrunc status=none
dd if="$work/partition.ext4" of="$work/disk.img" bs=1M seek=32 conv=notrunc,sparse status=none
(( $(stat -c %s "$work/disk.img") < 1800000000 ))
# noclobber prevents overwriting an existing output even if it appeared mid-build.
# Keep the newly reserved regular file sparse; never use this copy rule for
# physical flashing, where zero bytes must replace any previous contents.
( set -o noclobber; dd if="$work/disk.img" bs=1M conv=sparse status=none > "$output" )
sha256sum "$output"
stat -c 'Image bytes: %s' "$output"
