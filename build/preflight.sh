#!/usr/bin/env bash
# Exercise mount/loop/binfmt on allocated files only; never accept a disk target.
set -euo pipefail
[[ $# == 1 ]] || { echo "Usage: $0 STATIC_AARCH64_BUSYBOX" >&2; exit 2; }
busybox=$(realpath "$1")
[[ -x $busybox ]] || exit 1
work=$(mktemp -d)
loop=''
cleanup() {
    mountpoint -q "$work/mount" && sudo -n umount "$work/mount"
    [[ -z $loop ]] || sudo -n losetup -d "$loop"
    rm -rf "$work"
}
trap cleanup EXIT
free -h
df -h . /tmp
sudo -n dpkg --audit
sudo -n apt-get check
truncate -s 32M "$work/preflight.img"
mkfs.ext4 -q -F "$work/preflight.img"
mkdir "$work/mount" "$work/arm-root" "$work/arm-root/bin"
loop=$(sudo -n losetup --find --show "$work/preflight.img")
sudo -n mount "$loop" "$work/mount"
sudo -n sh -c 'echo loop-mount-ok > "$1/probe"' sh "$work/mount"
[[ $(cat "$work/mount/probe") == loop-mount-ok ]]
sudo -n umount "$work/mount"
sudo -n losetup -d "$loop"
loop=''
cp "$busybox" "$work/arm-root/bin/busybox"
[[ $(sudo -n chroot "$work/arm-root" /bin/busybox uname -m) == aarch64 ]]
sudo -n chroot "$work/arm-root" /bin/busybox sh -c 'echo arm-chroot-ok'
echo 'PASS: file-backed loop mount and ARM64 chroot (builder checks only)'
