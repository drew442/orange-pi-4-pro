#!/bin/bash
# Unprivileged filesystem/backend fixtures: never mounts or accesses real disks.
set -euo pipefail
repo=$(cd "$(dirname "$0")/.." && pwd)
fixture=$(mktemp -d)
trap 'rm -rf "$fixture"' EXIT
source "$repo/configs/host-storage-resolver.sh"
source "$repo/configs/host-init"
root_uuid=a7330000-0000-4000-8000-00000000000a
state_uuid=73a8a9bc-fc5d-40b2-a14e-fda8654d559d
state=$fixture/newroot/run/a733-state
passes=0

hi_resolver() { :; }
hi_console() { printf 'console\n' >> "$fixture/log"; }
hs_resolve() {
    printf 'resolve\n' >> "$fixture/log"
    [ ! -e "$fixture/no-device" ] || return 1
    if [ -e "$fixture/bad-resolver" ]; then cat "$fixture/bad-resolver"; return; fi
    printf 'ROOT_DEVICE=/dev/nvme0n1p2\nSTATE_DEVICE=/dev/nvme0n1p4\n'
}
hs_identity() {
    printf 'identity %s\n' "$1" >> "$fixture/log"
    HS_ID=259:4; HS_PARENT=nvme0n1
    [ ! -e "$fixture/changed-device" ] || HS_ID=259:9
}
hs_filesystem() {
    printf 'probe %s\n' "$1" >> "$fixture/log"
    HS_UUID=$state_uuid; HS_TYPE=ext4
    [ ! -e "$fixture/changed-uuid" ] || HS_UUID=$root_uuid
}
hi_switch() { printf 'switch_root /newroot /sbin/init\n' >> "$fixture/log"; }

# Only primitive I/O is replaced. Configuration parsing, validation, ordering,
# mount arguments and failure cleanup execute unchanged from production.
hi_bb() {
    local command=$1 arg path target count value
    shift
    case "$command" in
        mount)
            printf 'mount %s\n' "$*" >> "$fixture/log"
            target=${!#}
            if [ -f "$fixture/fail-mount" ] && [ "$target" = "$(cat "$fixture/fail-mount")" ]; then return 1; fi
            case "$*" in
                *'rw,nodev,nosuid'*)
                    [ ! -f "$fixture/mutate-marker" ] || printf 'wrong\n' > "$state/.a733-state"
                    [ ! -f "$fixture/mutate-id" ] || printf 'fedcba9876543210fedcba9876543210\n' > "$state/identity/machine-id" ;;
            esac
            return 0 ;;
        umount)
            printf 'umount %s\n' "$*" >> "$fixture/log"
            if [ "$1" = /newroot/run/a733-state ] && [ -f "$fixture/replace-on-unmount" ]; then touch "$fixture/changed-device"; fi
            [ ! -f "$fixture/fail-umount" ] || [ "$1" != "$(cat "$fixture/fail-umount")" ] ;;
        sleep) printf 'sleep %s\n' "$*" >> "$fixture/log"; return 0 ;;
        uname)
            if [ -e "$fixture/kernel" ]; then cat "$fixture/kernel"; else printf '6.6.98-a733-diag\n'; fi ;;
        stat)
            if [ "$2" = %u ]; then
                if [ -f "$fixture/bad-owner" ] && [ "$3" = "$(cat "$fixture/bad-owner")" ]; then printf '1000\n'; else printf '0\n'; fi
                return
            fi
            ;;
    esac
    case "$command" in mount|umount|sleep|uname) return $? ;; esac
    local -a arguments=()
    for arg in "$@"; do
        case "$arg" in /newroot|/newroot/*|/etc/a733-boot.conf|/proc/self/mountinfo) arg=$fixture$arg ;; esac
        arguments+=("$arg")
    done
    # State content is preseeded to represent the mocked mount, so its mountpoint
    # already exists in this backend. Real init creates it on freshly mounted RAM.
    if [ "$command" = mkdir ] && [ "${arguments[-1]}" = "$state" ]; then return 0; fi
    /bin/busybox "$command" "${arguments[@]}"
}
reset_fixture() {
    rm -rf "$fixture"/*
    mkdir -p "$fixture/etc" "$fixture/proc/self" "$fixture/newroot"/{run,tmp,etc,var,root,home,proc,sys,dev,usr/sbin,usr/lib/orangepi-hypervisor,usr/lib/systemd,usr/lib/aarch64-linux-gnu}
    chmod 755 "$fixture/newroot/etc"
    ln -s usr/sbin "$fixture/newroot/sbin"
    ln -s usr/lib "$fixture/newroot/lib"
    ln -s /lib/systemd/systemd "$fixture/newroot/usr/sbin/init"
    ln -s aarch64-linux-gnu/ld-linux-aarch64.so.1 "$fixture/newroot/usr/lib/ld-linux-aarch64.so.1"
    mkdir -p "$state"/{var,root,home,identity}
    chmod 755 "$state" "$state"/{var,root,home,identity}
    printf 'immutable payload marker\n' > "$fixture/newroot/usr/lib/orangepi-hypervisor/payload.json"
    : > "$fixture/newroot/etc/machine-id"
    : > "$fixture/newroot/usr/lib/systemd/systemd"
    : > "$fixture/newroot/usr/lib/aarch64-linux-gnu/ld-linux-aarch64.so.1"
    chmod 755 "$fixture/newroot/usr/lib/systemd/systemd" "$fixture/newroot/usr/lib/aarch64-linux-gnu/ld-linux-aarch64.so.1"
    printf 'a733-state-v1\n%s\n' "$state_uuid" > "$state/.a733-state"
    printf '0123456789abcdef0123456789abcdef\n' > "$state/identity/machine-id"
    chmod 644 "$state/.a733-state" "$state/identity/machine-id"
    hash=$(sha256sum "$fixture/newroot/usr/lib/orangepi-hypervisor/payload.json"); hash=${hash%% *}
    printf 'ROOT_UUID=%s\nSTATE_UUID=%s\nROOT_MARKER_SHA256=%s\nKERNEL_RELEASE=6.6.98-a733-diag\n' "$root_uuid" "$state_uuid" "$hash" > "$fixture/etc/a733-boot.conf"
    printf '21 1 259:2 / /newroot ro,nodev - ext4 /dev/nvme0n1p2 ro\n' > "$fixture/proc/self/mountinfo"
    : > "$fixture/log"
}
fail() { echo "FAIL: $*" >&2; cat "$fixture/log" >&2; exit 1; }
success() {
    hi_handoff || fail "$1"
    grep -q '^switch_root ' "$fixture/log" || fail "missing handoff: $1"
    passes=$((passes + 1))
}
reject() {
    local label=$1 writable=${2:-no}
    if hi_handoff > "$fixture/stdout" 2> "$fixture/stderr"; then fail "accepted $label"; fi
    hi_cleanup >> "$fixture/stdout" 2>> "$fixture/stderr" || fail "cleanup failed: $label"
    ! grep -q '^switch_root ' "$fixture/log" || fail "switched on $label"
    if [ "$writable" = no ]; then
        ! grep -q 'rw,nodev,nosuid' "$fixture/log" || fail "wrote before validation: $label"
    fi
    passes=$((passes + 1))
}

reset_fixture
success 'provisioned root and state'
python3 - "$fixture/log" <<'PY'
import sys
lines = open(sys.argv[1]).read().splitlines()
def index(fragment):
    return next(i for i, line in enumerate(lines) if fragment in line)
assert index('ro,noload,nodev /dev/nvme0n1p2 /newroot') < index('ro,noload,nodev,nosuid')
assert index('devtmpfs /dev') < index('console') < index('resolve')
assert index('ro,noload,nodev,nosuid') < index('umount /newroot/run/a733-state')
assert index('umount /newroot/run/a733-state') < index('probe nvme0n1p4') < index('rw,nodev,nosuid')
assert index('rw,nodev,nosuid') < index('--bind /newroot/run/a733-state/var') < index('-t overlay')
assert index('-t overlay') < index('--bind /newroot/run/a733-state/identity/machine-id')
assert index('size=64M,mode=1777,nodev,nosuid tmpfs /newroot/tmp') < index('--move /proc')
assert index('size=64M,mode=1777,nodev,nosuid tmpfs /newroot/tmp') < index('umount /tmp') < index('umount /run') < index('--move /proc')
assert lines[-1] == 'switch_root /newroot /sbin/init'
assert sum('size=64M' in line for line in lines) == 4
assert all('/dev/mmc' not in line for line in lines)
PY
[ "$(stat -c %a "$fixture/newroot/run/etc-upper")" = 755 ] || fail 'overlay /etc root mode'
[ "$(stat -c %a "$fixture/newroot/run/etc-work")" = 700 ] || fail 'overlay work mode'

reset_fixture; printf 'ROOT_UUID=%s\n' "$root_uuid" >> "$fixture/etc/a733-boot.conf"; reject 'duplicate config'
reset_fixture; printf 'UNKNOWN=yes\n' >> "$fixture/etc/a733-boot.conf"; reject 'unknown config'
reset_fixture; sed -i 's/ROOT_UUID=/ROOT_UUID =/' "$fixture/etc/a733-boot.conf"; reject 'malformed config'
reset_fixture; sed -i '/STATE_UUID=/d' "$fixture/etc/a733-boot.conf"; reject 'missing config'
reset_fixture; sed -i "s/$state_uuid/$root_uuid/" "$fixture/etc/a733-boot.conf"; reject 'same UUIDs'
reset_fixture; printf '\n' >> "$fixture/etc/a733-boot.conf"; reject 'extra config newline'
reset_fixture; truncate -s -1 "$fixture/etc/a733-boot.conf"; reject 'missing config newline'
reset_fixture; printf '\0' >> "$fixture/etc/a733-boot.conf"; reject 'config NUL'
reset_fixture; truncate -s -1 "$fixture/etc/a733-boot.conf"; printf '\0' >> "$fixture/etc/a733-boot.conf"; reject 'NUL replaces config newline'
reset_fixture; printf 'KERNEL_RELEASE=$(touch /unreachable)\n' > "$fixture/etc/a733-boot.conf"; reject 'shell code as config'
reset_fixture; printf '6.6.98-other\n' > "$fixture/kernel"; reject 'kernel mismatch'
reset_fixture; touch "$fixture/no-device"; reject 'retry exhaustion'
[ "$(grep -c '^resolve$' "$fixture/log")" = 10 ] && [ "$(grep -c '^sleep 1$' "$fixture/log")" = 9 ] || fail 'retry bound'
reset_fixture; printf 'ROOT_DEVICE=/dev/mmcblk0p2\nSTATE_DEVICE=/dev/nvme0n1p4\n' > "$fixture/bad-resolver"; reject 'SD path'
reset_fixture; printf 'ROOT_DEVICE=/dev/nvme0n1p2\nROOT_DEVICE=/dev/nvme0n1p2\nSTATE_DEVICE=/dev/nvme0n1p4\n' > "$fixture/bad-resolver"; reject 'duplicate resolver result'
reset_fixture; printf 'modified\n' >> "$fixture/newroot/usr/lib/orangepi-hypervisor/payload.json"; reject 'root hash'
reset_fixture; mv "$fixture/newroot/usr/lib/orangepi-hypervisor/payload.json" "$fixture/marker"; ln -s "$fixture/marker" "$fixture/newroot/usr/lib/orangepi-hypervisor/payload.json"; reject 'root marker symlink'
reset_fixture; mv "$fixture/newroot/usr/lib/orangepi-hypervisor" "$fixture/payload"; ln -s "$fixture/payload" "$fixture/newroot/usr/lib/orangepi-hypervisor"; reject 'root marker ancestor symlink'
reset_fixture; sed -i 's/ro,nodev/rw,nodev/' "$fixture/proc/self/mountinfo"; reject 'root mounted writable'
reset_fixture; : > "$fixture/proc/self/mountinfo"; reject 'missing root mount'
reset_fixture; chmod -x "$fixture/newroot/usr/lib/systemd/systemd"; reject 'init not executable'
reset_fixture; chmod 700 "$fixture/newroot/etc"; reject 'base etc mode differs from overlay root'
reset_fixture; rm "$fixture/newroot/usr/sbin/init"; ln -s /other/init "$fixture/newroot/usr/sbin/init"; reject 'wrong init target'
reset_fixture; rm "$fixture/newroot/usr/lib/aarch64-linux-gnu/ld-linux-aarch64.so.1"; reject 'missing ELF interpreter'
reset_fixture; printf 'wrong\n' > "$state/.a733-state"; reject 'state marker'
reset_fixture; truncate -s -1 "$state/.a733-state"; reject 'state marker no newline'
reset_fixture; printf '\n' >> "$state/.a733-state"; reject 'state marker extra newline'
reset_fixture; truncate -s -1 "$state/.a733-state"; printf '\0' >> "$state/.a733-state"; reject 'NUL replaces marker newline'
reset_fixture; mv "$state/.a733-state" "$fixture/marker"; ln -s "$fixture/marker" "$state/.a733-state"; reject 'state marker symlink'
reset_fixture; rmdir "$state/var"; reject 'missing state directory'
reset_fixture; rmdir "$state/home"; ln -s "$fixture" "$state/home"; reject 'state directory symlink'
reset_fixture; chmod 777 "$state/var"; reject 'writable state directory'
reset_fixture; printf '/newroot/run/a733-state/root\n' > "$fixture/bad-owner"; reject 'wrong state owner'
reset_fixture; printf '00000000000000000000000000000000\n' > "$state/identity/machine-id"; reject 'zero identity'
reset_fixture; printf '0123456789ABCDEF0123456789abcdef\n' > "$state/identity/machine-id"; reject 'uppercase identity'
reset_fixture; truncate -s -1 "$state/identity/machine-id"; reject 'identity missing newline'
reset_fixture; truncate -s -1 "$state/identity/machine-id"; printf '\0' >> "$state/identity/machine-id"; reject 'NUL replaces identity newline'
reset_fixture; chmod 666 "$state/identity/machine-id"; reject 'identity mode'
reset_fixture; mv "$state/identity/machine-id" "$fixture/id"; ln -s "$fixture/id" "$state/identity/machine-id"; reject 'identity symlink'
reset_fixture; touch "$fixture/changed-uuid"; reject 'state UUID changed after RO unmount'
reset_fixture; touch "$fixture/replace-on-unmount"; reject 'state block identity replaced after RO unmount'
reset_fixture; touch "$fixture/mutate-marker"; reject 'state marker changed on RW mount' yes
grep -q '^umount /newroot/run/a733-state$' "$fixture/log" || fail 'RW cleanup'
reset_fixture; touch "$fixture/mutate-id"; reject 'machine identity changed on RW mount' yes
reset_fixture; printf '/newroot/etc/machine-id\n' > "$fixture/fail-mount"; reject 'late bind failure' yes
python3 - "$fixture/log" <<'PY'
import sys
lines = open(sys.argv[1]).read().splitlines()
cleanup = [line for line in lines if line.startswith('umount ')][1:]
assert cleanup == ['umount ' + p for p in (
    '/newroot/etc', '/newroot/home', '/newroot/root', '/newroot/var',
    '/newroot/run/a733-state', '/newroot/run', '/newroot')], cleanup
PY
reset_fixture; printf '/newroot/sys\n' > "$fixture/fail-mount"; reject 'partial pseudo filesystem move' yes
grep -q '^mount --move /newroot/proc /proc$' "$fixture/log" || fail 'restore moved proc'
reset_fixture; printf '/newroot/dev\n' > "$fixture/fail-mount"; reject 'device move fails after proc and sys moved' yes
python3 - "$fixture/log" <<'PY'
import sys
lines = open(sys.argv[1]).read().splitlines()
assert lines.index('mount --move /newroot/sys /sys') < lines.index('mount --move /newroot/proc /proc') < lines.index('umount /newroot')
PY
reset_fixture; printf '/run\n' > "$fixture/fail-umount"; reject 'old RAM unmount failure' yes
! grep -q '^mount --move ' "$fixture/log" || fail 'pseudo filesystems moved after RAM unmount failed'
reset_fixture; printf '/newroot/etc/machine-id\n' > "$fixture/fail-mount"; printf '/newroot/etc\n' > "$fixture/fail-umount"; reject 'cleanup continues after unmount failure' yes
grep -q '^umount /newroot/run/a733-state$' "$fixture/log" || fail 'state cleanup skipped'
grep -q 'cleanup could not unmount /newroot/etc' "$fixture/stderr" || fail 'cleanup failure not reported'

# Running a copy named init outside PID 1 must remain inert, even with hostile env.
cp "$repo/configs/host-init" "$fixture/init"
output=$(PATH=/missing /bin/sh "$fixture/init")
[ -z "$output" ] || fail 'non-PID1 execution'
passes=$((passes + 1))
printf 'host init: %s fixtures passed (no real mounts or hardware checks)\n' "$passes"
