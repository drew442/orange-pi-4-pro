#!/bin/sh
# Read-only NVMe GPT resolver. CLI: state STATE_UUID, or
# root STATE_DEVICE STATE_UUID SLOT ROOT_UUID. Source for the corresponding
# hs_resolve_state / hs_resolve_root functions. Parse output as data, never eval.
# Only p4 state candidates and the selected p2/p3 root are filesystem-probed.
# Every eligible p4 must be recognizable; unrelated/unrecognized p4 fails closed.
# Observations do not lock devices; run before other storage services.
# Backend primitives are replaceable only by sourced unprivileged fixtures.

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

hs_probe_table() { /sbin/blkid -p -o export -s PTTYPE "$1"; }
hs_table() {
    local data line device type seen_device seen_type
    data=$(hs_probe_table "/dev/$1") || return 1
    device=; type=; seen_device=0; seen_type=0
    while IFS= read -r line; do
        case "$line" in
            DEVNAME=*) [ "$seen_device" = 0 ] || return 1; seen_device=1; device=${line#DEVNAME=} ;;
            PTTYPE=*) [ "$seen_type" = 0 ] || return 1; seen_type=1; type=${line#PTTYPE=} ;;
            *) return 1 ;;
        esac
    done <<END_TABLE
$data
END_TABLE
    [ "$device" = "/dev/$1" ] && [ "$type" = gpt ]
}
hs_no_swap() {
    local swaps
    swaps=$(hs_read /proc/swaps) || return 1
    printf '%s\n' "$swaps" | /bin/busybox awk '
        NR == 1 && NF == 5 && $1 == "Filename" && $2 == "Type" &&
        $3 == "Size" && $4 == "Used" && $5 == "Priority" { ok=1; next }
        { ok=0; exit } END { exit ok ? 0 : 1 }'
}
hs_state_candidates() {
    local names name
    names=$(hs_candidates) || return 1
    for name in $names; do
        hs_match "$name" '^nvme[0-9]+n[0-9]+p4$' && printf '%s\n' "$name"
    done
    return 0
}
hs_resolve_state() (
    [ "$#" = 1 ] && hs_uuid "$1" || { hs_error 'expected canonical STATE_UUID'; exit 1; }
    state_uuid=$1
    hs_no_swap || { hs_error 'active swap or malformed swap inventory'; exit 1; }
    candidates=$(hs_state_candidates) || { hs_error 'cannot enumerate state partitions'; exit 1; }
    state=; parent=; id=
    for name in $candidates; do
        hs_identity "$name" && hs_filesystem "$name" || {
            hs_error "invalid/unrecognized state candidate: $name"; exit 1;
        }
        if [ "$HS_UUID" = "$state_uuid" ]; then
            [ -z "$state" ] && [ "$HS_TYPE" = ext4 ] || { hs_error 'duplicate state UUID or non-ext4 state'; exit 1; }
            state=$name; parent=$HS_PARENT; id=$HS_ID
        fi
    done
    [ -n "$state" ] || { hs_error 'state missing'; exit 1; }
    current=$(hs_state_candidates) && [ "$current" = "$candidates" ] &&
    hs_identity "$state" && [ "$HS_PARENT" = "$parent" ] && [ "$HS_ID" = "$id" ] &&
    hs_filesystem "$state" && [ "$HS_UUID" = "$state_uuid" ] && [ "$HS_TYPE" = ext4 ] &&
    hs_idle "$state" "$id" writable && hs_table "$parent" &&
    parent_id=$(hs_read "/sys/class/block/$parent/dev") && hs_idle "$parent" "$parent_id" writable || {
        hs_error 'state changed, busy, read-only or not GPT'; exit 1;
    }
    printf 'STATE_DEVICE=/dev/%s\n' "$state"
)

# State may already be mounted RW for journal recovery when selecting the root.
# Revalidate its identity/UUID and write eligibility without requiring idle state.
hs_resolve_root() (
    [ "$#" = 4 ] || { hs_error 'expected STATE_DEVICE STATE_UUID SLOT ROOT_UUID'; exit 1; }
    state=${1#/dev/}; state_uuid=$2; slot=$3; root_uuid=$4
    hs_match "$1" '^/dev/nvme[0-9]+n[0-9]+p4$' && hs_uuid "$state_uuid" || {
        hs_error 'invalid state identity'; exit 1;
    }
    case "$slot:$root_uuid" in
        A:a7330000-0000-4000-8000-00000000000a) number=2 ;;
        B:a7330000-0000-4000-8000-00000000000b) number=3 ;;
        *) hs_error 'invalid slot/root UUID pair'; exit 1 ;;
    esac
    [ "$state_uuid" != "$root_uuid" ] && hs_no_swap && hs_identity "$state" || { hs_error 'invalid state or swap'; exit 1; }
    parent=$HS_PARENT; state_id=$HS_ID; root=${parent}p${number}
    hs_filesystem "$state" && [ "$HS_UUID" = "$state_uuid" ] && [ "$HS_TYPE" = ext4 ] &&
    hs_holders_empty "/sys/class/block/$state" && [ "$(hs_read "/sys/class/block/$state/ro")" = 0 ] &&
    hs_table "$parent" && hs_identity "$root" && [ "$HS_PARENT" = "$parent" ] &&
    [ "$HS_ID" != "$state_id" ] || { hs_error 'invalid state/GPT/root identity'; exit 1; }
    root_id=$HS_ID
    hs_filesystem "$root" && [ "$HS_UUID" = "$root_uuid" ] && [ "$HS_TYPE" = ext4 ] &&
    hs_idle "$root" "$root_id" readonly &&
    hs_identity "$state" && [ "$HS_PARENT" = "$parent" ] && [ "$HS_ID" = "$state_id" ] &&
    hs_filesystem "$state" && [ "$HS_UUID" = "$state_uuid" ] && [ "$HS_TYPE" = ext4 ] &&
    hs_identity "$root" && [ "$HS_PARENT" = "$parent" ] && [ "$HS_ID" = "$root_id" ] &&
    hs_filesystem "$root" && [ "$HS_UUID" = "$root_uuid" ] && [ "$HS_TYPE" = ext4 ] &&
    hs_idle "$root" "$root_id" readonly && hs_table "$parent" &&
    parent_id=$(hs_read "/sys/class/block/$parent/dev") && hs_idle "$parent" "$parent_id" writable || {
        hs_error 'selected root/state changed or root/namespace busy'; exit 1;
    }
    printf 'ROOT_DEVICE=/dev/%s\n' "$root"
)

case "$0" in
    */host-storage-resolver.sh|host-storage-resolver.sh)
        command=${1:-}; [ "$#" = 0 ] || shift
        case "$command" in
            state) hs_resolve_state "$@" ;;
            root) hs_resolve_root "$@" ;;
            *) hs_error 'expected state or root subcommand' ;;
        esac ;;
esac
