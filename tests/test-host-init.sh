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
hs_resolve_state() {
    printf 'resolve\n' >> "$fixture/log"
    [ ! -e "$fixture/no-device" ] || return 1
    if [ -e "$fixture/bad-resolver" ]; then cat "$fixture/bad-resolver"; return; fi
    printf 'STATE_DEVICE=/dev/nvme0n1p4\n'
}
hs_resolve_root() {
    printf 'resolve-root %s %s\n' "$3" "$4" >> "$fixture/log"
    [ ! -f "$fixture/bad-root-resolver" ] || { cat "$fixture/bad-root-resolver"; return; }
    case "$3" in A) printf 'ROOT_DEVICE=/dev/nvme0n1p2\n' ;; B) printf 'ROOT_DEVICE=/dev/nvme0n1p3\n' ;; esac
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
hs_idle() {
    [ ! -f "$fixture/became-busy" ] && [ ! -f "$fixture/state-mounted" ] || return 1
    [ "$3" != writable ] || [ "$(cat "$fixture/block-ro")" = 0 ]
}
hs_table() { [ ! -f "$fixture/changed-table" ]; }
hi_switch() { printf 'switch_root /newroot /sbin/init\n' >> "$fixture/log"; }

# Only primitive I/O is replaced. Configuration parsing, validation, ordering,
# mount arguments and failure cleanup execute unchanged from production.
hi_bb() {
    local command=$1 arg path target count value
    shift
    case "$command" in
        blockdev)
            printf 'blockdev %s\n' "$*" >> "$fixture/log"
            [ "$2" = /dev/nvme0n1p4 ] || return 1
            case "$1" in
                --getro) cat "$fixture/block-ro" ;;
                --setro) [ -f "$fixture/ignore-setro" ] || printf '1\n' > "$fixture/block-ro"
                    [ ! -f "$fixture/change-on-setro" ] || touch "$fixture/changed-device" ;;
                --setrw) [ ! -f "$fixture/state-mounted" ] || return 1
                    [ -f "$fixture/ignore-setrw" ] || printf '0\n' > "$fixture/block-ro" ;;
                *) return 1 ;;
            esac
            return ;;
        od)
            printf 'superblock %s\n' "$*" >> "$fixture/log"
            [ "${!#}" = /dev/nvme0n1p4 ] || return 1
            /bin/busybox od -An -v -tu1 -j 1024 -N 236 "$fixture/state-device"
            return ;;
        chroot)
            printf 'management %s\n' "$*" >> "$fixture/log"
            [ ! -f "$fixture/fail-management" ]; return ;;
        head)
            printf 'bounded-read %s\n' "$*" >> "$fixture/log"
            case "${!#}" in */boot/selected.conf) printf 'read-selector\n' >> "$fixture/log" ;; esac
            if [ -f "$fixture/grow-selector" ] && [ "${!#}" = /run/a733-state/boot/selected.conf ]; then
                /bin/busybox head -c 1048576 /dev/zero >> "$state/boot/selected.conf"
            fi
            ;;
        cat)
            case "$1" in */boot/selected.conf) printf 'read-selector\n' >> "$fixture/log" ;; esac ;;
        mount)
            if [[ "$*" == *remount,bind,ro* ]] && [ -f "$fixture/fail-package-remount" ] && [ "${!#}" = "$(cat "$fixture/fail-package-remount")" ]; then return 1; fi
            printf 'mount %s\n' "$*" >> "$fixture/log"
            target=${!#}
            if [ -f "$fixture/fail-mount" ] && [ "$target" = "$(cat "$fixture/fail-mount")" ]; then return 1; fi
            if [ "$target" = /run/a733-state ]; then
                touch "$fixture/state-mounted"
                if [[ "$*" == *ro,noload* ]] && [ -f "$fixture/orphan" ]; then
                    if [ "$(cat "$fixture/block-ro")" = 1 ]; then
                        printf 'orphan write blocked by block RO\n' >> "$fixture/log"
                    else
                        printf 'orphan cleanup wrote disk\n' >> "$fixture/log"
                    fi
                fi
            fi
            case "$*" in
                *'rw,nodev,nosuid'*)
                    [ ! -f "$fixture/mutate-marker" ] || printf 'wrong\n' > "$state/.a733-state"
                    [ ! -f "$fixture/recovered-selector" ] || cp "$fixture/recovered-selector" "$state/boot/selected.conf"
                    [ ! -f "$fixture/mutate-id" ] || printf 'fedcba9876543210fedcba9876543210\n' > "$state/identity/machine-id" ;;
            esac
            if [[ "$*" == *remount,bind,ro* ]]; then
                value=ro; [ ! -f "$fixture/ignore-package-ro" ] || value=rw
                printf '40 20 259:2 /package %s %s,nodev,nosuid - ext4 /dev/nvme0n1p2 ro\n' "$target" "$value" >> "$fixture/proc/self/mountinfo"
            fi
            return 0 ;;
        umount)
            printf 'umount %s\n' "$*" >> "$fixture/log"
            if [ "$1" = /run/a733-state ] && [ -f "$fixture/replace-on-unmount" ]; then touch "$fixture/changed-device"; fi
            [ ! -f "$fixture/fail-umount" ] || [ "$1" != "$(cat "$fixture/fail-umount")" ] || return 1
            case "$1" in /run/a733-state|/newroot/run/a733-state)
                rm -f "$fixture/state-mounted"
                if [ -f "$fixture/external-on-unmount" ]; then
                    printf '\001' | dd of="$fixture/state-device" bs=1 seek=1232 conv=notrunc status=none
                fi ;;
            esac
            return 0 ;;
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
        case "$arg" in /run/a733-state|/run/a733-state/*) arg=/newroot$arg ;; esac
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
    mkdir -p "$fixture/etc" "$fixture/proc/self" "$fixture/newroot"/{run,tmp,etc,var,root,home,proc,sys,dev,usr/bin,usr/sbin,usr/lib/orangepi-hypervisor,usr/lib/systemd,usr/lib/aarch64-linux-gnu}
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
    : > "$fixture/newroot/usr/lib/orangepi-hypervisor/host-management.py"
    : > "$fixture/newroot/usr/lib/orangepi-hypervisor/host_config.py"
    : > "$fixture/newroot/usr/bin/python3"
    chmod 755 "$fixture/newroot/usr/bin/python3"
    chmod 755 "$fixture/newroot/usr/lib/systemd/systemd" "$fixture/newroot/usr/lib/aarch64-linux-gnu/ld-linux-aarch64.so.1"
    printf 'a733-state-v1\n%s\n' "$state_uuid" > "$state/.a733-state"
    printf '0123456789abcdef0123456789abcdef\n' > "$state/identity/machine-id"
    chmod 644 "$state/.a733-state" "$state/identity/machine-id"
    hash=$(sha256sum "$fixture/newroot/usr/lib/orangepi-hypervisor/payload.json"); hash=${hash%% *}
    printf 'STATE_UUID=%s\nKERNEL_RELEASE=6.6.98-a733-diag\n' "$state_uuid" > "$fixture/etc/a733-boot.conf"
    mkdir -m 700 "$state/boot"
    printf 'SCHEMA=1\nSLOT=A\nROOT_UUID=%s\nROOT_MARKER_SHA256=%s\nKERNEL_RELEASE=6.6.98-a733-diag\n' "$root_uuid" "$hash" > "$state/boot/selected.conf"
    chmod 600 "$state/boot/selected.conf"
    printf '21 1 259:2 / /newroot ro,nodev - ext4 /dev/nvme0n1p2 ro\n' > "$fixture/proc/self/mountinfo"
    local name path kind base
    base=$fixture/newroot/usr/lib/orangepi-hypervisor/package-state
    mkdir -p "$base"
    printf 'a733-package-state-v1\n' > "$base/FORMAT"
    printf '{}\n' > "$base/manifest.json"
    while read -r name path kind; do
        mkdir -p "$fixture/newroot/${path%/*}" "$state/${path%/*}"
        if [ "$kind" = directory ]; then
            mkdir -p "$base/$name" "$fixture/newroot/$path" "$state/$path"
        else
            : > "$base/$name"; : > "$fixture/newroot/$path"; : > "$state/$path"
        fi
    done <<END_ITEMS
$(hi_package_items)
END_ITEMS
    find "$fixture/newroot" -type d -exec chmod go-w {} +
    find "$fixture/newroot" -type f -exec chmod go-w {} +
    truncate -s 2048 "$fixture/state-device"
    printf '\123\357' | dd of="$fixture/state-device" bs=1 seek=1080 conv=notrunc status=none
    printf '\004' | dd of="$fixture/state-device" bs=1 seek=1116 conv=notrunc status=none
    printf '\010' | dd of="$fixture/state-device" bs=1 seek=1248 conv=notrunc status=none
    printf '0\n' > "$fixture/block-ro"
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
selection_before=$(sha256sum "$state/boot/selected.conf")
success 'provisioned root and state'
[ "$(sha256sum "$state/boot/selected.conf")" = "$selection_before" ] || fail 'boot changed selection'
[ "$(cat "$fixture/block-ro")" = 0 ] || fail 'successful probe did not restore original writable state'
python3 - "$fixture/log" <<'PY'
import sys
lines = open(sys.argv[1]).read().splitlines()
def index(fragment):
    return next(i for i, line in enumerate(lines) if fragment in line)
assert index('rw,nodev,nosuid') < index('read-selector') < index('resolve-root') < index('ro,noload,nodev /dev/nvme0n1p2 /newroot') < index('--move /run /newroot/run')
assert index('devtmpfs /dev') < index('console') < index('resolve')
assert index('--move /dev /newroot/dev') < index('management /newroot /usr/bin/python3 -I -B') < index('switch_root ')
assert index('ro,noload,nodev,nosuid') < index('umount /run/a733-state')
assert index('blockdev --setro') < index('ro,noload,nodev,nosuid')
assert index('umount /run/a733-state') < index('blockdev --setrw') < index('rw,nodev,nosuid')
assert [line for line in lines if line.startswith('blockdev ')] == [
    'blockdev --getro /dev/nvme0n1p4', 'blockdev --setro /dev/nvme0n1p4',
    'blockdev --getro /dev/nvme0n1p4', 'blockdev --getro /dev/nvme0n1p4',
    'blockdev --setrw /dev/nvme0n1p4', 'blockdev --getro /dev/nvme0n1p4']
assert index('rw,nodev,nosuid') < index('--bind /newroot/run/a733-state/var') < index('-t overlay')
assert index('-t overlay') < index('--bind /newroot/run/a733-state/identity/machine-id')
assert index('size=64M,mode=1777,nodev,nosuid tmpfs /newroot/tmp') < index('--move /proc')
assert index('--move /run /newroot/run') < index('size=64M,mode=1777,nodev,nosuid tmpfs /newroot/tmp') < index('umount /tmp') < index('--move /proc')
assert 'umount /run' not in lines
assert lines[-1] == 'switch_root /newroot /sbin/init'
assert sum('size=64M' in line for line in lines) == 3
assert all('/dev/mmc' not in line for line in lines)
PY
[ "$(stat -c %a "$fixture/newroot/run/etc-upper")" = 755 ] || fail 'overlay /etc root mode'
[ "$(stat -c %a "$fixture/newroot/run/etc-work")" = 700 ] || fail 'overlay work mode'



reset_fixture; printf '1\n' > "$fixture/block-ro"; reject 'preexisting RO is never owned or cleared'
! grep -q 'blockdev --set' "$fixture/log" || fail 'changed preexisting block RO'
reset_fixture; touch "$fixture/ignore-setrw"; reject 'kernel writable restoration verification failed'
[ "$(cat "$fixture/block-ro")" = 1 ] || fail 'failed restoration lost protection'
reset_fixture; touch "$fixture/ignore-setro"; reject 'kernel block RO verification failed'
! grep -q 'ro,noload,nodev,nosuid' "$fixture/log" || fail 'mounted after failed RO verification'
! grep -q 'blockdev --setrw' "$fixture/log" || fail 'restored without confirmed owned RO'
reset_fixture; touch "$fixture/change-on-setro"; reject 'target changed while setting block RO'
! grep -q 'blockdev --setrw' "$fixture/log" || fail 'restored substituted target'
reset_fixture; touch "$fixture/orphan"; success 'orphan writes blocked during initial probe'
grep -q '^orphan write blocked by block RO$' "$fixture/log" || fail 'orphan protection not exercised'
! grep -q '^orphan cleanup wrote disk$' "$fixture/log" || fail 'probe allowed orphan write'
reset_fixture; printf '/run/a733-state\n' > "$fixture/fail-umount"; reject 'failed probe unmount retains block RO'
[ "$(cat "$fixture/block-ro")" = 1 ] || fail 'RO lost after unmount failure'
! grep -q 'blockdev --setrw' "$fixture/log" || fail 'restored RW while mounted'
reset_fixture; touch "$fixture/external-on-unmount"; reject 'external journal introduced after probe'
! grep -q 'blockdev --setrw' "$fixture/log" || fail 'restored external journal source'
for external in uuid inode device journal-device no-journal truncated; do
    reset_fixture
    case "$external" in
        uuid) offset=1232; byte='\001' ;;
        inode) offset=1248; byte='\011' ;;
        device) offset=1252; byte='\001' ;;
        journal-device) offset=1120; byte='\010' ;;
        no-journal) offset=1116; byte='\000' ;;
        truncated) truncate -s 1200 "$fixture/state-device" ;;
    esac
    if [ "$external" != truncated ]; then
        printf '%b' "$byte" | dd of="$fixture/state-device" bs=1 seek="$offset" conv=notrunc status=none
    fi
    reject "raw journal guard $external"
    ! grep -q 'blockdev --setro' "$fixture/log" || fail 'invalid journal reached probe'
done

reset_fixture; printf 'wrong\n' > "$fixture/newroot/usr/lib/orangepi-hypervisor/package-state/FORMAT"; reject 'package ABI' yes
reset_fixture; touch "$fixture/ignore-package-ro"; reject 'package bind remount ignored' yes
while read -r name path kind; do
    reset_fixture; printf '/newroot/%s\n' "$path" > "$fixture/fail-mount"; reject "package bind failure $name" yes
    reset_fixture; printf '/newroot/%s\n' "$path" > "$fixture/fail-package-remount"; reject "package remount failure $name" yes
    grep -q "^umount /newroot/$path$" "$fixture/log" || fail 'failed remount bind not cleaned'
    reset_fixture; rm -rf "$fixture/newroot/usr/lib/orangepi-hypervisor/package-state/$name"
    ln -s /tmp "$fixture/newroot/usr/lib/orangepi-hypervisor/package-state/$name"
    reject "package source symlink $name" yes
done <<END_ITEMS
$(hi_package_items)
END_ITEMS
reset_fixture; rmdir "$fixture/newroot/var/cache/debconf"; rmdir "$fixture/newroot/var/cache"
ln -s /tmp "$fixture/newroot/var/cache"; reject 'package target symlink ancestor' yes

reset_fixture; printf 'ROOT_UUID=%s\n' "$root_uuid" >> "$fixture/etc/a733-boot.conf"; reject 'duplicate config'
reset_fixture; printf 'UNKNOWN=yes\n' >> "$fixture/etc/a733-boot.conf"; reject 'unknown config'
reset_fixture; sed -i 's/STATE_UUID=/STATE_UUID =/' "$fixture/etc/a733-boot.conf"; reject 'malformed config'
reset_fixture; sed -i '/STATE_UUID=/d' "$fixture/etc/a733-boot.conf"; reject 'missing config'
reset_fixture; printf '\n' >> "$fixture/etc/a733-boot.conf"; reject 'extra config newline'
reset_fixture; truncate -s -1 "$fixture/etc/a733-boot.conf"; reject 'missing config newline'
reset_fixture; printf '\0' >> "$fixture/etc/a733-boot.conf"; reject 'config NUL'
reset_fixture; truncate -s -1 "$fixture/etc/a733-boot.conf"; printf '\0' >> "$fixture/etc/a733-boot.conf"; reject 'NUL replaces config newline'
reset_fixture; printf 'KERNEL_RELEASE=$(touch /unreachable)\n' > "$fixture/etc/a733-boot.conf"; reject 'shell code as config'
reset_fixture; printf '6.6.98-other\n' > "$fixture/kernel"; reject 'kernel mismatch'
reset_fixture; touch "$fixture/no-device"; reject 'retry exhaustion'
[ "$(grep -c '^resolve$' "$fixture/log")" = 10 ] && [ "$(grep -c '^sleep 1$' "$fixture/log")" = 9 ] || fail 'retry bound'
reset_fixture; printf 'STATE_DEVICE=/dev/mmcblk0p4\n' > "$fixture/bad-resolver"; reject 'SD path'
reset_fixture; printf 'STATE_DEVICE=/dev/nvme0n1p4\nSTATE_DEVICE=/dev/nvme0n1p4\n' > "$fixture/bad-resolver"; reject 'duplicate resolver result'
reset_fixture; printf 'modified\n' >> "$fixture/newroot/usr/lib/orangepi-hypervisor/payload.json"; reject 'root hash' yes
reset_fixture; mv "$fixture/newroot/usr/lib/orangepi-hypervisor/payload.json" "$fixture/marker"; ln -s "$fixture/marker" "$fixture/newroot/usr/lib/orangepi-hypervisor/payload.json"; reject 'root marker symlink' yes
reset_fixture; mv "$fixture/newroot/usr/lib/orangepi-hypervisor" "$fixture/payload"; ln -s "$fixture/payload" "$fixture/newroot/usr/lib/orangepi-hypervisor"; reject 'root marker ancestor symlink' yes
reset_fixture; sed -i 's/ro,nodev/rw,nodev/' "$fixture/proc/self/mountinfo"; reject 'root mounted writable' yes
reset_fixture; : > "$fixture/proc/self/mountinfo"; reject 'missing root mount' yes
reset_fixture; chmod -x "$fixture/newroot/usr/lib/systemd/systemd"; reject 'init not executable' yes
reset_fixture; rm "$fixture/newroot/usr/lib/orangepi-hypervisor/host-management.py"; reject 'management code missing' yes
reset_fixture; touch "$fixture/fail-management"; reject 'management preparation failure' yes
grep -q '^mount --move /newroot/dev /dev$' "$fixture/log" || fail 'management failure did not restore dev'
grep -q '^umount /newroot/run/a733-state$' "$fixture/log" || fail 'management failure did not unmount writable state'
reset_fixture; chmod 700 "$fixture/newroot/etc"; reject 'base etc mode differs from overlay root' yes
reset_fixture; rm "$fixture/newroot/usr/sbin/init"; ln -s /other/init "$fixture/newroot/usr/sbin/init"; reject 'wrong init target' yes
reset_fixture; rm "$fixture/newroot/usr/lib/aarch64-linux-gnu/ld-linux-aarch64.so.1"; reject 'missing ELF interpreter' yes
reset_fixture; printf 'wrong\n' > "$state/.a733-state"; reject 'state marker'
reset_fixture; truncate -s -1 "$state/.a733-state"; reject 'state marker no newline'
reset_fixture; printf '\n' >> "$state/.a733-state"; reject 'state marker extra newline'
reset_fixture; truncate -s -1 "$state/.a733-state"; printf '\0' >> "$state/.a733-state"; reject 'NUL replaces marker newline'
reset_fixture; mv "$state/.a733-state" "$fixture/marker"; ln -s "$fixture/marker" "$state/.a733-state"; reject 'state marker symlink'
reset_fixture; rm -rf "$state/var"; reject 'missing state directory'
reset_fixture; rmdir "$state/home"; ln -s "$fixture" "$state/home"; reject 'state directory symlink'
reset_fixture; chmod 777 "$state/var"; reject 'writable state directory'
reset_fixture; printf '/run/a733-state/root\n' > "$fixture/bad-owner"; reject 'wrong state owner'
reset_fixture; printf '00000000000000000000000000000000\n' > "$state/identity/machine-id"; reject 'zero identity'
reset_fixture; printf '0123456789ABCDEF0123456789abcdef\n' > "$state/identity/machine-id"; reject 'uppercase identity'
reset_fixture; truncate -s -1 "$state/identity/machine-id"; reject 'identity missing newline'
reset_fixture; truncate -s -1 "$state/identity/machine-id"; printf '\0' >> "$state/identity/machine-id"; reject 'NUL replaces identity newline'
reset_fixture; chmod 666 "$state/identity/machine-id"; reject 'identity mode'
reset_fixture; mv "$state/identity/machine-id" "$fixture/id"; ln -s "$fixture/id" "$state/identity/machine-id"; reject 'identity symlink'
reset_fixture; touch "$fixture/changed-uuid"; reject 'state UUID changed after RO unmount'
reset_fixture; touch "$fixture/replace-on-unmount"; reject 'state block identity replaced after RO unmount'
! grep -q 'blockdev --setrw' "$fixture/log" || fail 'restored replaced target after unmount'
reset_fixture; touch "$fixture/became-busy"; reject 'state became busy before RW'
reset_fixture; touch "$fixture/changed-table"; reject 'GPT changed before RW'
reset_fixture; touch "$fixture/mutate-marker"; reject 'state marker changed on RW mount' yes
grep -q '^umount /run/a733-state$' "$fixture/log" || fail 'RW cleanup'
reset_fixture; touch "$fixture/mutate-id"; reject 'machine identity changed on RW mount' yes
reset_fixture; printf '/newroot/etc/machine-id\n' > "$fixture/fail-mount"; reject 'late bind failure' yes
python3 - "$fixture/log" <<'PY'
import sys
lines = open(sys.argv[1]).read().splitlines()
cleanup = [line for line in lines if line.startswith('umount ')][1:]
assert cleanup == ['umount ' + p for p in (
    '/newroot/etc', '/newroot/var/lib/systemd/catalog', '/newroot/var/lib/systemd/deb-systemd-helper-enabled',
    '/newroot/var/lib/shells.state', '/newroot/var/lib/pam', '/newroot/var/lib/ucf',
    '/newroot/var/cache/debconf', '/newroot/var/lib/apt', '/newroot/var/lib/dpkg',
    '/newroot/home', '/newroot/root', '/newroot/var',
    '/newroot/run/a733-state', '/newroot')], cleanup
assert lines.index('umount /newroot/run/a733-state') < lines.index('mount --move /newroot/run /run') < lines.index('umount /newroot')
PY
reset_fixture; printf '/newroot/sys\n' > "$fixture/fail-mount"; reject 'partial pseudo filesystem move' yes
grep -q '^mount --move /newroot/proc /proc$' "$fixture/log" || fail 'restore moved proc'
reset_fixture; printf '/newroot/dev\n' > "$fixture/fail-mount"; reject 'device move fails after proc and sys moved' yes
python3 - "$fixture/log" <<'PY'
import sys
lines = open(sys.argv[1]).read().splitlines()
assert lines.index('mount --move /newroot/sys /sys') < lines.index('mount --move /newroot/proc /proc') < lines.index('umount /newroot')
PY
reset_fixture; printf '/tmp\n' > "$fixture/fail-umount"; reject 'old RAM unmount failure' yes
! grep -q '^mount --move /proc' "$fixture/log" || fail 'pseudo filesystems moved after RAM unmount failed'
reset_fixture; printf '/newroot/etc/machine-id\n' > "$fixture/fail-mount"; printf '/newroot/etc\n' > "$fixture/fail-umount"; reject 'cleanup continues after unmount failure' yes
grep -q '^umount /newroot/run/a733-state$' "$fixture/log" || fail 'state cleanup skipped'
grep -q 'cleanup could not unmount /newroot/etc' "$fixture/stderr" || fail 'cleanup failure not reported'

reset_fixture
cp "$state/boot/selected.conf" "$fixture/recovered-selector"
printf 'dirty metadata exposes stale/invalid record\n' > "$state/boot/selected.conf"
success 'journal recovery exposes valid selector before read'

# Oversized metadata is rejected before any content capture. A selector that
# grows after stat is still read through a fixed 4097-byte cap and rejected.
for metadata in config selector marker identity; do
    reset_fixture
    case "$metadata" in
        config) oversized=/etc/a733-boot.conf; file=$fixture$oversized; writable=no ;;
        selector) oversized=/run/a733-state/boot/selected.conf; file=$state/boot/selected.conf; writable=yes ;;
        marker) oversized=/run/a733-state/.a733-state; file=$state/.a733-state; writable=no ;;
        identity) oversized=/run/a733-state/identity/machine-id; file=$state/identity/machine-id; writable=no ;;
    esac
    /bin/busybox head -c 8192 /dev/zero > "$file"
    reject "oversized $metadata" "$writable"
    ! grep -Fq -- " $oversized" "$fixture/log" || fail "read oversized $metadata"
done
reset_fixture; touch "$fixture/grow-selector"; reject 'selector grows after size check' yes
grep -q '^bounded-read -c 4097 /run/a733-state/boot/selected.conf$' "$fixture/log" || fail 'selector read cap'

# Mutable selector is parsed only after journal recovery and is never sourced.
reset_fixture
sed -i 's/SLOT=A/SLOT=B/; s/00000000000a/00000000000b/' "$state/boot/selected.conf"
success 'slot B'
grep -q 'ro,noload,nodev /dev/nvme0n1p3 /newroot' "$fixture/log" || fail 'B root path'
for field in SCHEMA SLOT ROOT_UUID ROOT_MARKER_SHA256 KERNEL_RELEASE; do
    reset_fixture; printf '%s=\n' "$field" >> "$state/boot/selected.conf"; reject "duplicate selector $field" yes
    reset_fixture; sed -i "/^$field=/d" "$state/boot/selected.conf"; reject "missing selector $field" yes
done
reset_fixture; printf 'UNKNOWN=yes\n' >> "$state/boot/selected.conf"; reject 'unknown selector' yes
reset_fixture; printf '\n' >> "$state/boot/selected.conf"; reject 'extra selector newline' yes
reset_fixture; truncate -s -1 "$state/boot/selected.conf"; reject 'missing selector newline' yes
reset_fixture; printf '\0' >> "$state/boot/selected.conf"; reject 'selector NUL' yes
reset_fixture; truncate -s -1 "$state/boot/selected.conf"; printf '\0' >> "$state/boot/selected.conf"; reject 'NUL replaces selector newline' yes
reset_fixture; sed -i 's/SLOT=A/SLOT=B/' "$state/boot/selected.conf"; reject 'stale slot UUID' yes
reset_fixture; sed -i 's/6.6.98-a733-diag/6.6.98-other/' "$state/boot/selected.conf"; reject 'selector ABI mismatch' yes
reset_fixture; sed -i 's/SCHEMA=1/SCHEMA=2/' "$state/boot/selected.conf"; reject 'unsupported selector schema' yes
reset_fixture; sed -i 's/ROOT_MARKER_SHA256=.*/ROOT_MARKER_SHA256=ABC/' "$state/boot/selected.conf"; reject 'invalid selector hash' yes
reset_fixture; sed -i 's/SLOT=A/SLOT=$(touch \/unreachable)/' "$state/boot/selected.conf"; reject 'selector shell code stays data' yes
reset_fixture; chmod 666 "$state/boot/selected.conf"; reject 'writable selector' yes
reset_fixture; chmod 755 "$state/boot"; reject 'nonprivate boot directory' yes
reset_fixture; printf '/run/a733-state/boot/selected.conf\n' > "$fixture/bad-owner"; reject 'selector owner' yes
reset_fixture; mv "$state/boot/selected.conf" "$fixture/selector"; ln -s "$fixture/selector" "$state/boot/selected.conf"; reject 'selector symlink' yes
reset_fixture; mv "$state/boot" "$fixture/boot"; ln -s "$fixture/boot" "$state/boot"; reject 'boot symlink' yes
reset_fixture; printf 'ROOT_DEVICE=/dev/mmcblk0p2\n' > "$fixture/bad-root-resolver"; reject 'root resolver SD' yes
reset_fixture; printf '/newroot/run\n' > "$fixture/fail-mount"; reject 'run move failure' yes
# Failure before /run move must clean the still-original state location.
grep -q '^umount /run/a733-state$' "$fixture/log" || fail 'original state cleanup'

# Running a copy named init outside PID 1 must remain inert, even with hostile env.
cp "$repo/configs/host-init" "$fixture/init"
output=$(PATH=/missing /bin/sh "$fixture/init")
[ -z "$output" ] || fail 'non-PID1 execution'
passes=$((passes + 1))
printf 'host init: %s fixtures passed (no real mounts or hardware checks)\n' "$passes"
