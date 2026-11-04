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
    printf '%s\n' "$name" >> "$fixture/probed"
    if [ "$name" = nvme0n1p2 ]; then
        [ ! -f "$fixture/mutate-root" ] || printf '259:99\n' > "$fixture/dev/nvme0n1p2.identity"
        [ ! -f "$fixture/mutate-state-on-root" ] || printf '259:99\n' > "$fixture/dev/nvme0n1p4.identity"
    fi
    if [ "$name" = nvme0n1p4 ] && [ -f "$fixture/mutate-state" ]; then
        printf '259:99\n' > "$fixture/dev/nvme0n1p4.identity"
    fi
    cat "$fixture/probe/$name"
}
hs_probe_table() { cat "$fixture/table/${1##*/}"; }
namespace() {
    local name=$1 id=$2
    mkdir -p "$fixture/table"
    printf 'DEVNAME=/dev/%s\nPTTYPE=gpt\n' "$name" > "$fixture/table/$name"
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
    partition nvme0n1 3 259:3 a7330000-0000-4000-8000-00000000000b
    partition nvme0n1 4 259:4 "$STATE_UUID"
}
pass_count=0
success() {
    local output slot=${2:-A} uuid=$ROOT_UUID number=2
    [ "$slot" = A ] || { uuid=a7330000-0000-4000-8000-00000000000b; number=3; }
    output=$(hs_resolve_state "$STATE_UUID") || { echo "FAIL state: $1" >&2; exit 1; }
    [ "$output" = STATE_DEVICE=/dev/nvme0n1p4 ] || exit 1
    output=$(hs_resolve_root /dev/nvme0n1p4 "$STATE_UUID" "$slot" "$uuid") || { echo "FAIL root: $1" >&2; exit 1; }
    [ "$output" = ROOT_DEVICE=/dev/nvme0n1p$number ] || exit 1
    pass_count=$((pass_count+1))
}
failure() {
    local label=$1; shift
    if "$@" > "$fixture/result" 2> "$fixture/error"; then
        echo "FAIL accepted: $label" >&2; exit 1
    fi
    [ ! -s "$fixture/result" ] && [ -s "$fixture/error" ] || { echo "FAIL diagnostic: $label" >&2; exit 1; }
    pass_count=$((pass_count+1))
}
reject() { failure "$1" hs_resolve_state "$STATE_UUID"; }
reject_root() { failure "$1" hs_resolve_root /dev/nvme0n1p4 "$STATE_UUID" A "$ROOT_UUID"; }
reset_fixture; success normal
reset_fixture; touch "$fixture/probe/nvme0n1p2.fail"; success 'rollback B ignores damaged A' B
! grep -q '^nvme0n1p2$' "$fixture/probed"
reset_fixture; touch "$fixture/probe/nvme0n1p3.fail"; success 'A ignores damaged B'
reset_fixture; partition nvme0n1 5 259:5 "$STATE_UUID"; success 'ignore UUID outside role p4'
reset_fixture; partition nvme0n1 1 259:1 AB12-CD34 vfat; touch "$fixture/probe/nvme0n1p1.fail"; success 'ignore unreadable boot'
reset_fixture; printf '1\n' > "$fixture/sys/devices/nvme0n1/nvme0n1p2/ro"; success 'block RO root'
reset_fixture
for uuid in invalid '' A7330000-0000-4000-8000-00000000000a; do
    failure 'invalid UUID' hs_resolve_state "$uuid"
done
failure 'mismatched slot' hs_resolve_root /dev/nvme0n1p4 "$STATE_UUID" B "$ROOT_UUID"
failure 'SD state' hs_resolve_root /dev/mmcblk0p4 "$STATE_UUID" A "$ROOT_UUID"
reset_fixture; namespace nvme1n1 260:0; partition nvme1n1 4 260:4 "$STATE_UUID"; reject 'duplicate eligible state'
reset_fixture; namespace nvme1n1 260:0; partition nvme1n1 4 260:4 other; touch "$fixture/probe/nvme1n1p4.fail"; reject 'unrecognized unrelated p4'
reset_fixture; mv "$fixture/sys/class/block/nvme0n1p4" "$fixture/sys/class/block/mmcblk0p4"; reject 'state only on SD'
for field in DEVNAME UUID TYPE; do
    reset_fixture
    { printf '%s=\n' "$field"; cat "$fixture/probe/nvme0n1p4"; } > "$fixture/duplicate"
    mv "$fixture/duplicate" "$fixture/probe/nvme0n1p4"
    reject "empty duplicate $field"
done
for field in DEVNAME PTTYPE; do
    reset_fixture
    printf '%s=\n' "$field" >> "$fixture/table/nvme0n1"
    reject "duplicate table $field"
done
reset_fixture; printf 'DEVNAME=/dev/nvme0n1\nPTTYPE=dos\n' > "$fixture/table/nvme0n1"; reject 'non-GPT'
reset_fixture; sed -i 's,nvme0n1,nvme1n1,' "$fixture/table/nvme0n1"; reject 'table device mismatch'
reset_fixture; touch "$fixture/probe/nvme0n1p4.fail"; reject 'state probe failure'
reset_fixture; touch "$fixture/probe/nvme0n1p2.fail"; reject_root 'selected root probe failure'
for device in nvme0n1 nvme0n1p4; do
    reset_fixture; printf '1\n' > "$fixture/sys/class/block/$device/ro"; reject 'read-only state/namespace'
    reset_fixture; touch "$fixture/sys/class/block/$device/holders/dm-0"; reject 'holder'
    reset_fixture; printf '259:99\n' > "$fixture/dev/$device.identity"; reject 'identity mismatch'
done
reset_fixture; printf '2 1 259:2 / /root ro - ext4 /dev/nvme0n1p2 ro\n' >> "$fixture/proc/self/mountinfo"; reject_root 'root mounted'
reset_fixture; printf '2 1 259:4 / /run/a733-state rw - ext4 /dev/nvme0n1p4 rw\n' >> "$fixture/proc/self/mountinfo"
reject 'state mounted before selection'
hs_resolve_root /dev/nvme0n1p4 "$STATE_UUID" A "$ROOT_UUID" > "$fixture/result" || exit 1
pass_count=$((pass_count+1))
reset_fixture; printf '/swap file 100 0 -2\n' >> "$fixture/proc/swaps"; reject 'swap'
reset_fixture; printf 'broken\n' > "$fixture/proc/self/mountinfo"; reject 'malformed mount inventory'
reset_fixture; printf '3\n' > "$fixture/sys/class/block/nvme0n1p4/partition"; reject 'partition role mismatch'
reset_fixture; sed -i 's/TYPE=ext4/TYPE=vfat/' "$fixture/probe/nvme0n1p4"; reject 'non-ext4 state'
reset_fixture; sed -i "s/$ROOT_UUID/$STATE_UUID/" "$fixture/probe/nvme0n1p2"; reject_root 'stale root UUID'
reset_fixture; touch "$fixture/change-inventory"; reject 'changed inventory'
reset_fixture; touch "$fixture/mutate-state"; reject 'state replaced during discovery'
reset_fixture; touch "$fixture/mutate-root"; reject_root 'root replaced during probe'
reset_fixture; touch "$fixture/mutate-state-on-root"; reject_root 'state replaced during root probe'
[ -z "$(/bin/busybox ash -c '. "$1"' ash "$repo/configs/host-storage-resolver.sh")" ]
/bin/busybox ash -n "$repo/configs/host-storage-resolver.sh"
printf 'host storage resolver: %s fixture checks passed (%s)\n' "$pass_count" "${1:-bash}"
if [ "${1:-}" != ash ]; then /bin/busybox ash "$0" ash; fi
