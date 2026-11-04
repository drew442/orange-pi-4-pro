#!/bin/bash
# Unprivileged fixture backend: fake regular files never become real devices.
set -euo pipefail
repo=$(cd "$(dirname "$0")/.." && pwd)
fixture=$(mktemp -d)
trap 'rm -rf "$fixture"' EXIT
source "$repo/configs/host-storage-resolver.sh"
ROOT_UUID=a7330000-0000-4000-8000-00000000000a
STATE_UUID=73a8a9bc-fc5d-40b2-a14e-fda8654d559d

# Override only source-level I/O primitives. The production CLI cannot select this
# backend through environment variables or arguments. Core identity and selection
# code still runs unchanged, including real sysfs symlink-parent relationships.
hs_read() { /bin/busybox cat "$fixture$1"; }
hs_has() { [ -e "$fixture$1" ] || [ -L "$fixture$1" ]; }
hs_realpath() {
    local result
    result=$(/bin/busybox readlink -f "$fixture$1") || return 1
    case "$result" in "$fixture"/*) printf '%s\n' "${result#"$fixture"}" ;; *) return 1 ;; esac
}
hs_block() { [ -f "$fixture$1.identity" ] && [ ! -L "$fixture$1" ]; }
hs_device_id() { cat "$fixture$1.identity"; }
hs_candidates() {
    local path
    if [ -e "$fixture/change-inventory" ]; then
        if [ -e "$fixture/enumerated" ]; then return 0; fi
        touch "$fixture/enumerated"
    fi
    for path in "$fixture"/sys/class/block/nvme*; do
        [ -e "$path" ] || [ -L "$path" ] || continue
        printf '%s\n' "${path##*/}"
    done
}
hs_holders_empty() {
    local path
    [ -d "$fixture$1/holders" ] || return 1
    for path in "$fixture$1"/holders/*; do
        [ -e "$path" ] || [ -L "$path" ] || continue
        return 1
    done
}
hs_probe() {
    local name=${1##*/}
    [ ! -e "$fixture/probe/$name.fail" ] || return 8
    cat "$fixture/probe/$name"
}
namespace() {
    local name=$1 id=$2
    mkdir -p "$fixture/sys/devices/$name/holders" "$fixture/sys/class/block" "$fixture/dev" "$fixture/probe"
    ln -s "../../devices/$name" "$fixture/sys/class/block/$name"
    touch "$fixture/dev/$name"
    printf '%s\n' "$id" > "$fixture/dev/$name.identity"
    printf '%s\n' "$id" > "$fixture/sys/devices/$name/dev"
    printf '0\n' > "$fixture/sys/devices/$name/ro"
}
partition() {
    local parent=$1 number=$2 id=$3 uuid=$4 type=${5:-ext4} name=$1'p'$2
    mkdir -p "$fixture/sys/devices/$parent/$name/holders"
    ln -s "../../devices/$parent/$name" "$fixture/sys/class/block/$name"
    touch "$fixture/dev/$name"
    printf '%s\n' "$id" > "$fixture/dev/$name.identity"
    printf '%s\n' "$id" > "$fixture/sys/devices/$parent/$name/dev"
    printf '%s\n' "$number" > "$fixture/sys/devices/$parent/$name/partition"
    printf '0\n' > "$fixture/sys/devices/$parent/$name/ro"
    printf 'DEVNAME=/dev/%s\nUUID=%s\nTYPE=%s\n' "$name" "$uuid" "$type" > "$fixture/probe/$name"
}
reset_fixture() {
    rm -rf "$fixture"/*
    mkdir -p "$fixture/proc/self"
    printf 'Filename\tType\tSize\tUsed\tPriority\n' > "$fixture/proc/swaps"
    printf '1 0 0:1 / / rw - rootfs rootfs rw\n' > "$fixture/proc/self/mountinfo"
    namespace nvme0n1 259:0
    partition nvme0n1 2 259:2 "$ROOT_UUID"
    partition nvme0n1 4 259:4 "$STATE_UUID"
}
pass_count=0
success() {
    local output
    output=$(hs_resolve "$ROOT_UUID" "$STATE_UUID") || { echo "FAIL: $1" >&2; exit 1; }
    [ "$output" = $'ROOT_DEVICE=/dev/nvme0n1p2\nSTATE_DEVICE=/dev/nvme0n1p4' ] || { echo "FAIL output: $1" >&2; exit 1; }
    pass_count=$((pass_count+1))
}
failure() {
    local label=$1; shift
    if hs_resolve "$@" > "$fixture/result" 2> "$fixture/error"; then
        echo "FAIL accepted: $label" >&2; exit 1
    fi
    [ ! -s "$fixture/result" ] && [ -s "$fixture/error" ] || { echo "FAIL diagnostic: $label" >&2; exit 1; }
    pass_count=$((pass_count+1))
}
reject() { failure "$1" "$ROOT_UUID" "$STATE_UUID"; }

reset_fixture; success 'normal NVMe'
printf '1\n' > "$fixture/sys/devices/nvme0n1/nvme0n1p2/ro"
success 'block read-only root'
partition nvme0n1 1 259:1 AB12-CD34 vfat
success 'unrelated non-ext4 boot partition'
reset_fixture
failure 'malformed root UUID' invalid "$STATE_UUID"
failure 'multiline UUID' "$ROOT_UUID
$ROOT_UUID" "$STATE_UUID"
failure 'empty UUID' '' "$STATE_UUID"
failure 'uppercase UUID' A7330000-0000-4000-8000-00000000000a "$STATE_UUID"
failure 'same root/state UUID' "$ROOT_UUID" "$ROOT_UUID"
reset_fixture
sed -i "s/$STATE_UUID/$ROOT_UUID/" "$fixture/probe/nvme0n1p4"
reject 'duplicate root UUID'
reset_fixture
partition nvme0n1 5 259:5 "$STATE_UUID"
reject 'duplicate state UUID'
reset_fixture
namespace nvme1n1 260:0
partition nvme1n1 4 260:4 "$STATE_UUID"
rm "$fixture/sys/class/block/nvme0n1p4"
reject 'different namespaces'
reset_fixture
mv "$fixture/sys/class/block/nvme0n1p4" "$fixture/sys/class/block/mmcblk0p4"
reject 'state UUID only on SD'
reset_fixture
printf '259:2\n' > "$fixture/dev/nvme0n1p4.identity"
reject 'root/state same device identity'
reset_fixture
touch "$fixture/probe/nvme0n1p4.fail"
reject 'blkid probe failure'
reset_fixture
: > "$fixture/probe/nvme0n1p4"
reject 'empty probe result'
reset_fixture
printf '1\n' > "$fixture/sys/devices/nvme0n1/nvme0n1p4/ro"
reject 'read-only state'
reset_fixture
printf '1\n' > "$fixture/sys/devices/nvme0n1/ro"
reject 'read-only namespace'
reset_fixture
touch "$fixture/sys/devices/nvme0n1/nvme0n1p4/holders/dm-0"
reject 'state holder'
reset_fixture
touch "$fixture/sys/devices/nvme0n1/holders/dm-0"
reject 'namespace holder'
reset_fixture
printf '2 1 259:2 / /root ro - ext4 /dev/nvme0n1p2 ro\n' >> "$fixture/proc/self/mountinfo"
reject 'root already mounted'
reset_fixture
printf '2 1 259:4 / /state rw - ext4 /dev/nvme0n1p4 rw\n' >> "$fixture/proc/self/mountinfo"
reject 'state already mounted'
reset_fixture
printf '/swapfile file 100 0 -2\n' >> "$fixture/proc/swaps"
reject 'active swap file'
reset_fixture
printf 'bad inventory\n' > "$fixture/proc/swaps"
reject 'malformed swap inventory'
reset_fixture
rm "$fixture/proc/self/mountinfo"
reject 'missing mount inventory'
reset_fixture
printf 'broken\n' > "$fixture/proc/self/mountinfo"
reject 'malformed mount inventory'
reset_fixture
rm "$fixture/dev/nvme0n1p4"
ln -s nvme0n1p2 "$fixture/dev/nvme0n1p4"
reject 'devtmpfs symlink'
reset_fixture
printf '259:8\n' > "$fixture/dev/nvme0n1p4.identity"
reject 'device sysfs identity mismatch'
reset_fixture
printf '259:8\n' > "$fixture/dev/nvme0n1.identity"
reject 'namespace sysfs identity mismatch'
reset_fixture
printf '3\n' > "$fixture/sys/devices/nvme0n1/nvme0n1p4/partition"
reject 'partition number mismatch'
reset_fixture
mkdir -p "$fixture/sys/devices/other/nvme0n1p4"
rm "$fixture/sys/class/block/nvme0n1p4"
ln -s ../../devices/other/nvme0n1p4 "$fixture/sys/class/block/nvme0n1p4"
reject 'sysfs parent mismatch'
reset_fixture
sed -i 's/TYPE=ext4/TYPE=vfat/' "$fixture/probe/nvme0n1p4"
reject 'selected state not ext4'
reset_fixture
printf 'UUID=%s\n' "$STATE_UUID" >> "$fixture/probe/nvme0n1p4"
reject 'duplicate probe field'
# An empty first value is still a field occurrence and must not permit a
# second value to replace it. Keep a complete otherwise-valid probe after it.
for field in DEVNAME UUID TYPE; do
    reset_fixture
    { printf '%s=\n' "$field"; cat "$fixture/probe/nvme0n1p4"; } > "$fixture/duplicate-probe"
    mv "$fixture/duplicate-probe" "$fixture/probe/nvme0n1p4"
    reject "empty duplicate $field field"
done
reset_fixture
printf 'DEVNAME=/dev/mmcblk0p4\nUUID=%s\nTYPE=ext4\n' "$STATE_UUID" > "$fixture/probe/nvme0n1p4"
reject 'blkid device identity mismatch'
reset_fixture
printf 'TYPE=$(touch /tmp/forbidden)\n' >> "$fixture/probe/nvme0n1p4"
reject 'probe shell metacharacters stay data'
reset_fixture
touch "$fixture/change-inventory"
reject 'inventory disappears after initial scan'
reset_fixture
rm "$fixture/dev/nvme0n1p4.identity"
reject 'partition is not a block node'
# Sourcing must produce no output or execute a probe, including in BusyBox ash.
[ -z "$(/bin/busybox ash -c '. "$1"' ash "$repo/configs/host-storage-resolver.sh")" ]
/bin/busybox ash -n "$repo/configs/host-storage-resolver.sh"
printf 'host storage resolver: %s fixture checks passed (%s)\n' "$pass_count" "${1:-bash}"
if [ "${1:-}" != ash ]; then
    /bin/busybox ash "$0" ash
fi
