#!/bin/sh
# Read-only early-boot resolver. Source for hs_resolve ROOT_UUID STATE_UUID;
# execute as host-storage-resolver.sh ROOT_UUID STATE_UUID. No eval is needed:
# success prints ROOT_DEVICE=/dev/nvme... and STATE_DEVICE=/dev/nvme....
# Requires devtmpfs, sysfs, procfs, /bin/busybox and util-linux /sbin/blkid.
# Every discovered NVMe partition must probe successfully; an unformatted or
# unrecognized partition therefore prevents selection in this preparation stage.
# Observation is not a lock. Run before storage services or mounting either fs.
# Backend functions below are separated for sourced, unprivileged fixture tests;
# the CLI has no environment/path overrides and never accepts arbitrary devices.

hs_error() { printf '%s\n' "storage resolver: $*" >&2; return 1; }
hs_read() { /bin/busybox cat "$1"; }
hs_realpath() { /bin/busybox readlink -f "$1"; }
hs_has() { [ -e "$1" ] || [ -L "$1" ]; }
hs_block() { [ -b "$1" ] && [ ! -L "$1" ]; }
hs_device_id() {
    local id major minor
    id=$(/bin/busybox stat -c '%t:%T' "$1") || return 1
    major=${id%:*}; minor=${id#*:}
    printf '%d:%d\n' "0x$major" "0x$minor"
}
hs_candidates() {
    local path
    for path in /sys/class/block/nvme*; do
        hs_has "$path" || continue
        printf '%s\n' "${path##*/}"
    done
}
hs_holders_empty() {
    local path
    [ -d "$1/holders" ] && [ -r "$1/holders" ] && [ -x "$1/holders" ] || return 1
    for path in "$1"/holders/*; do
        hs_has "$path" && return 1
    done
    return 0
}
hs_probe() { /sbin/blkid -p -o export -s UUID -s TYPE "$1"; }
hs_match() {
    case "$1" in *'
'*) return 1 ;; esac
    printf '%s\n' "$1" | /bin/busybox grep -Eq "$2"
}
hs_uuid() {
    hs_match "$1" '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
}

# Validate the block node, sysfs major:minor, partition number and real parent.
# Sets HS_PARENT and HS_ID only on success.
hs_identity() {
    local name sys resolved parent number id actual parent_path parent_id
    name=$1; sys=/sys/class/block/$name
    hs_match "$name" '^nvme[0-9]+n[0-9]+p[1-9][0-9]*$' || return 1
    hs_block "/dev/$name" || return 1
    actual=$(hs_realpath "/dev/$name") || return 1
    [ "$actual" = "/dev/$name" ] || return 1
    resolved=$(hs_realpath "$sys") || return 1
    [ "${resolved##*/}" = "$name" ] || return 1
    parent_path=${resolved%/*}; parent=${parent_path##*/}
    hs_match "$parent" '^nvme[0-9]+n[0-9]+$' || return 1
    number=$(hs_read "$sys/partition") || return 1
    hs_match "$number" '^[1-9][0-9]*$' || return 1
    [ "$name" = "${parent}p${number}" ] || return 1
    actual=$(hs_realpath "/sys/class/block/$parent") || return 1
    [ "$actual" = "$parent_path" ] || return 1
    # A namespace must itself be a real block node, not another partition.
    hs_has "/sys/class/block/$parent/partition" && return 1
    hs_block "/dev/$parent" || return 1
    actual=$(hs_realpath "/dev/$parent") || return 1
    [ "$actual" = "/dev/$parent" ] || return 1
    parent_id=$(hs_read "/sys/class/block/$parent/dev") || return 1
    actual=$(hs_device_id "/dev/$parent") || return 1
    [ "$parent_id" = "$actual" ] || return 1
    id=$(hs_read "$sys/dev") || return 1
    hs_match "$id" '^[0-9]+:[0-9]+$' || return 1
    actual=$(hs_device_id "/dev/$name") || return 1
    [ "$id" = "$actual" ] && [ "$id" != "$parent_id" ] || return 1
    HS_PARENT=$parent; HS_ID=$id
}

# Busy indicators are checked for the selected partitions and parent namespace.
hs_idle() {
    local name id ro mounts
    name=$1; id=$2
    hs_holders_empty "/sys/class/block/$name" || return 1
    ro=$(hs_read "/sys/class/block/$name/ro") || return 1
    case "$ro" in 0|1) ;; *) return 1 ;; esac
    # Root may intentionally be block-read-only; state and namespace must write.
    [ "$3" != writable ] || [ "$ro" = 0 ] || return 1
    mounts=$(hs_read /proc/self/mountinfo) || return 1
    # Reject malformed inventory rather than treating it as an idle device.
    printf '%s\n' "$mounts" | /bin/busybox awk -v id="$id" '
        NF < 10 || $1 !~ /^[0-9]+$/ || $2 !~ /^[0-9]+$/ ||
        $3 !~ /^[0-9]+:[0-9]+$/ { bad=1 }
        $3 == id { busy=1 }
        END { exit (bad || busy) ? 1 : 0 }' || return 1
}

# Parse the restricted export format as data, never shell syntax.
hs_filesystem() {
    local data line uuid type device expected seen_uuid seen_type seen_device
    expected=/dev/$1
    data=$(hs_probe "/dev/$1") || return 1
    uuid=; type=; device=; seen_uuid=0; seen_type=0; seen_device=0
    while IFS= read -r line; do
        case "$line" in
            DEVNAME=*) [ "$seen_device" = 0 ] || return 1; seen_device=1; device=${line#DEVNAME=} ;;
            UUID=*) [ "$seen_uuid" = 0 ] || return 1; seen_uuid=1; uuid=${line#UUID=} ;;
            TYPE=*) [ "$seen_type" = 0 ] || return 1; seen_type=1; type=${line#TYPE=} ;;
            '') ;;
            *) return 1 ;;
        esac
    done <<END_PROBE
$data
END_PROBE
    # Other filesystems (e.g. boot VFAT) may have noncanonical UUID formats.
    [ "$device" = "$expected" ] && [ -n "$uuid" ] && [ -n "$type" ] || return 1
    HS_UUID=$uuid; HS_TYPE=$type
}

hs_resolve() (
    [ "$#" = 2 ] || { hs_error 'expected ROOT_UUID STATE_UUID'; exit 1; }
    hs_uuid "$1" && hs_uuid "$2" && [ "$1" != "$2" ] || {
        hs_error 'UUIDs must be distinct, canonical lowercase UUIDs'; exit 1;
    }
    root_uuid=$1; state_uuid=$2
    # Early boot has no swap. Reject all active swap, including files and aliases.
    swaps=$(hs_read /proc/swaps) || { hs_error 'cannot read swap inventory'; exit 1; }
    # Kernel spacing is not an ABI; accept the exact five header fields.
    printf '%s\n' "$swaps" | /bin/busybox awk '
        NR == 1 && NF == 5 && $1 == "Filename" && $2 == "Type" &&
        $3 == "Size" && $4 == "Used" && $5 == "Priority" { ok=1; next }
        { ok=0; exit } END { exit ok ? 0 : 1 }' || {
            hs_error 'active swap or malformed swap inventory'; exit 1;
        }
    candidates=$(hs_candidates) || { hs_error 'cannot enumerate NVMe'; exit 1; }
    root=; state=; root_parent=; state_parent=; root_id=; state_id=
    for name in $candidates; do
        # Whole namespaces are not filesystem candidates.
        hs_match "$name" '^nvme[0-9]+n[0-9]+$' && continue
        hs_identity "$name" || { hs_error "invalid NVMe partition identity: $name"; exit 1; }
        hs_filesystem "$name" || { hs_error "failed/ambiguous filesystem probe: $name"; exit 1; }
        case "$HS_UUID" in
            "$root_uuid")
                [ -z "$root" ] && [ "$HS_TYPE" = ext4 ] || {
                    hs_error 'duplicate root UUID or root is not ext4'; exit 1;
                }
                root=$name; root_parent=$HS_PARENT; root_id=$HS_ID ;;
            "$state_uuid")
                [ -z "$state" ] && [ "$HS_TYPE" = ext4 ] || {
                    hs_error 'duplicate state UUID or state is not ext4'; exit 1;
                }
                state=$name; state_parent=$HS_PARENT; state_id=$HS_ID ;;
        esac
    done
    [ -n "$root" ] && [ -n "$state" ] && [ "$root" != "$state" ] &&
    [ "$root_id" != "$state_id" ] && [ "$root_parent" = "$state_parent" ] || {
        hs_error 'root/state missing, aliased, or on different NVMe namespaces'; exit 1;
    }
    # Detect removal/replacement during scanning; observations still cannot lock.
    current=$(hs_candidates) || { hs_error 'cannot re-enumerate NVMe'; exit 1; }
    [ "$current" = "$candidates" ] || { hs_error 'NVMe inventory changed'; exit 1; }
    hs_identity "$root" && [ "$HS_PARENT" = "$root_parent" ] && [ "$HS_ID" = "$root_id" ] &&
    hs_filesystem "$root" && [ "$HS_UUID" = "$root_uuid" ] && [ "$HS_TYPE" = ext4 ] &&
    hs_idle "$root" "$root_id" readonly &&
    hs_identity "$state" && [ "$HS_PARENT" = "$state_parent" ] && [ "$HS_ID" = "$state_id" ] &&
    hs_filesystem "$state" && [ "$HS_UUID" = "$state_uuid" ] && [ "$HS_TYPE" = ext4 ] &&
    hs_idle "$state" "$state_id" writable || {
        hs_error 'selected partition changed, is busy, or state is read-only'; exit 1;
    }
    parent_id=$(hs_read "/sys/class/block/$root_parent/dev") &&
    hs_idle "$root_parent" "$parent_id" writable || {
        hs_error 'NVMe namespace is busy or read-only'; exit 1;
    }
    printf 'ROOT_DEVICE=/dev/%s\nSTATE_DEVICE=/dev/%s\n' "$root" "$state"
)

case "$0" in
    */host-storage-resolver.sh|host-storage-resolver.sh) hs_resolve "$@" ;;
esac
