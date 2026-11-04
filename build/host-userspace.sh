#!/usr/bin/env bash
# Prototype Debian userspace only: no boot, disk installation or hardware claims.
set -euo pipefail

SNAPSHOT=https://snapshot.debian.org/archive/debian/20261001T000000Z/
SUITE=bookworm
KEYRING_URL=https://deb.debian.org/debian/pool/main/d/debian-archive-keyring/debian-archive-keyring_2023.3+deb12u2_all.deb
KEYRING_ARCHIVE_SHA256=f699e2f88dca05212f2a452b58475f2993cb6993dfbafb1d0205a3291eb8b4b8
INRELEASE_SHA256=77737fa4b34f2693e982cc9ee35736816c35a7778fc2d326cc1bbf5b301fe1aa
owned_mounts=()

fail() { printf 'host-userspace: %s\n' "$*" >&2; return 1; }

fetch_verified() {
    local url=$1 expected=$2 destination=$3
    curl --fail --location --retry 3 --output "$destination" "$url" || return $?
    printf '%s  %s\n' "$expected" "$destination" | sha256sum --check --status || {
        fail "SHA256 mismatch: $url"
        return 1
    }
}

validate_workspace() {
    local candidate=$1 repository=$2 name
    name=${candidate##*/}
    [[ $candidate == "$repository/out/$name" && $name =~ ^host-userspace-[a-zA-Z0-9][a-zA-Z0-9._-]*$ ]] ||
        { fail 'workspace must be a new absolute out/host-userspace-NAME directory'; return 1; }
    [[ $(realpath -m -- "$candidate") == "$candidate" ]] ||
        { fail 'workspace path must not contain symlinks or traversal'; return 1; }
    [[ ! -e $candidate && ! -L $candidate ]] ||
        { fail 'workspace already exists; use a new name'; return 1; }
}

compare_inventory() {
    local actual=$1 expected=$2
    [[ -f $actual && -s $actual && -r $actual && -f $expected && -s $expected && -r $expected ]] || {
        fail 'missing or empty package inventory lock'; return 1;
    }
    diff -u <(LC_ALL=C sort "$expected") <(LC_ALL=C sort "$actual")
}

verify_package_locks() {
    local workspace=$1 repository=$2
    compare_inventory "$workspace/installed-packages.tsv" "$repository/configs/host-userspace-packages.lock.tsv" || return $?
    compare_inventory "$workspace/package-cache.tsv" "$repository/configs/host-userspace-package-hashes.lock.tsv"
}

cleanup_mounts() {
    local index destination status result=0
    local remaining=()
    for ((index=${#owned_mounts[@]}-1; index>=0; index--)); do
        destination=${owned_mounts[index]}
        if mountpoint -q -- "$destination"; then
            if umount -- "$destination"; then continue; fi
        else
            status=$?
            # util-linux mountpoint returns 32 for a path that is not mounted.
            if [[ $status == 32 ]]; then continue; fi
        fi
        printf 'Unmount failed: %s; retain for EXIT retry/manual cleanup\n' "$destination" >&2
        remaining=("$destination" "${remaining[@]}")
        result=1
    done
    owned_mounts=("${remaining[@]}")
    return "$result"
}

enter_mount_namespace() {
    # All debootstrap stages and our mounts live in this private namespace.
    # No environment variable can bypass this entry path.
    # Positional arguments expand in the child shell, preserving literal paths.
    # shellcheck disable=SC2016
    exec unshare --mount --propagation private --fork bash -c \
        'source "$1"; main "$2"' bash "$1" "$2"
}

entrypoint() {
    [[ $# == 1 ]] || { fail "usage: $0 /absolute/repository/out/host-userspace-NAME"; return 1; }
    [[ $EUID == 0 ]] || { fail 'run as root on the dedicated builder'; return 1; }
    command -v unshare >/dev/null || { fail 'missing command: unshare'; return 1; }
    enter_mount_namespace "$(realpath -- "${BASH_SOURCE[0]}")" "$1"
}

second_stage_and_mounts() {
    local root=$1
    # debootstrap owns temporary /proc, /sys, /dev/pts and /dev/shm mounts.
    # Track none of those until its second-stage exit cleanup has completed.
    chroot "$root" /bin/true || return $?
    chroot "$root" /debootstrap/debootstrap --second-stage || return $?
    mount_owned -t devpts -o newinstance,ptmxmode=0666,nosuid,noexec devpts "$root/dev/pts" || return $?
    mount_owned -t proc -o nosuid,nodev,noexec proc "$root/proc"
}

mount_owned() {
    local destination=${!#}
    mount "$@" || return $?
    owned_mounts+=("$destination")
}

main() {
    [[ $# == 1 ]] || { fail "usage: $0 /absolute/repository/out/host-userspace-NAME"; return 1; }
    [[ $EUID == 0 ]] || { fail 'run as root on the dedicated builder'; return 1; }
    local repository workspace root command packages keyring foreign=false
    repository=$(realpath -- "$(dirname -- "${BASH_SOURCE[0]}")/..")
    workspace=$1
    validate_workspace "$workspace" "$repository"
    for command in debootstrap curl gpgv chroot mount umount mknod tar gzip sha256sum dpkg-deb systemctl mountpoint unshare sort diff; do
        command -v "$command" >/dev/null || { fail "missing command: $command"; return 1; }
    done
    if [[ $(uname -m) != aarch64 ]]; then
        command -v qemu-aarch64-static >/dev/null || { fail 'cross build requires qemu-aarch64-static and registered binfmt'; return 1; }
        foreign=true
    fi
    mkdir -p -- "$repository/out"
    mkdir -- "$workspace"
    root=$workspace/rootfs
    trap 'cleanup_mounts' EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    fetch_verified "$KEYRING_URL" "$KEYRING_ARCHIVE_SHA256" "$workspace/keyring.deb"
    # Extract the authenticated package; never install it into the builder OS.
    dpkg-deb -x "$workspace/keyring.deb" "$workspace/keyring"
    keyring=$workspace/keyring/usr/share/keyrings/debian-archive-keyring.gpg
    [[ -r $keyring ]] || { fail 'bootstrap package has no archive keyring'; return 1; }
    fetch_verified "${SNAPSHOT}dists/$SUITE/InRelease" "$INRELEASE_SHA256" "$workspace/InRelease"
    gpgv --keyring "$keyring" "$workspace/InRelease" 2>"$workspace/signature-verification.txt"
    cp -- "$keyring" "$workspace/debian-archive-keyring.gpg"
    printf '%s\n' "$SNAPSHOT" > "$workspace/source-url.txt"
    {
        printf 'artifact\turl\tsha256\n'
        printf 'keyring.deb\t%s\t%s\n' "$KEYRING_URL" "$KEYRING_ARCHIVE_SHA256"
        printf 'InRelease\t%sdists/%s/InRelease\t%s\n' "$SNAPSHOT" "$SUITE" "$INRELEASE_SHA256"
        printf 'debian-archive-keyring.gpg\t%s#usr/share/keyrings/debian-archive-keyring.gpg\t%s\n' \
            "$KEYRING_URL" "$(sha256sum "$keyring" | cut -d ' ' -f 1)"
    } > "$workspace/source-lock.tsv"
    (cd "$workspace"; sha256sum InRelease debian-archive-keyring.gpg > signed-release.sha256)
    debootstrap --arch=arm64 --variant=minbase --foreign --force-check-gpg --keyring="$keyring" \
        --include=ca-certificates "$SUITE" "$root" "$SNAPSHOT"
    # Do not let debootstrap's HTTPS fetcher or a downloaded package bypass authentication.
    if $foreign; then
        install -m 0755 "$(command -v qemu-aarch64-static)" "$root/usr/bin/qemu-aarch64-static"
    fi
    mkdir -p "$root/etc/apt/apt.conf.d" "$root/etc/systemd/system-preset" "$root/usr/sbin" "$root/usr/share/keyrings"
    cat > "$root/usr/sbin/policy-rc.d" <<'POLICY'
#!/bin/sh
exit 101
POLICY
    chmod 0755 "$root/usr/sbin/policy-rc.d"
    printf 'disable *\n' > "$root/etc/systemd/system-preset/00-prototype.preset"
    # Historical snapshots expire; signature and package hash verification stay enabled.
    cat > "$root/etc/apt/apt.conf.d/99-prototype" <<'APT'
Acquire::Check-Valid-Until "false";
APT::Install-Recommends "false";
APT::Install-Suggests "false";
APT::Keep-Downloaded-Packages "true";
Binary::apt::APT::Keep-Downloaded-Packages "true";
APT
    printf 'deb [signed-by=/usr/share/keyrings/debian-archive-keyring.gpg] %s %s main\n' "$SNAPSHOT" "$SUITE" > "$root/etc/apt/sources.list"
    install -m 0644 "$keyring" "$root/usr/share/keyrings/debian-archive-keyring.gpg"
    mount_owned -t tmpfs -o mode=0755,nosuid tmpfs "$root/dev"
    mknod -m 666 "$root/dev/null" c 1 3
    mknod -m 666 "$root/dev/zero" c 1 5
    mknod -m 666 "$root/dev/random" c 1 8
    mknod -m 666 "$root/dev/urandom" c 1 9
    mkdir -p "$root/dev/pts" "$root/proc"
    ln -s pts/ptmx "$root/dev/ptmx"
    # This also fails early if a cross builder has no working ARM64 binfmt interpreter.
    second_stage_and_mounts "$root"
    # Refresh after second-stage configuration, which can write sources.list.
    printf 'deb [signed-by=/usr/share/keyrings/debian-archive-keyring.gpg] %s %s main\n' "$SNAPSHOT" "$SUITE" > "$root/etc/apt/sources.list"
    chroot "$root" /usr/bin/env DEBIAN_FRONTEND=noninteractive SYSTEMD_OFFLINE=1 apt-get update
    mapfile -t packages < <(sed '/^[[:space:]]*#/d; /^[[:space:]]*$/d' "$repository/configs/host-userspace-packages.txt")
    chroot "$root" /usr/bin/env DEBIAN_FRONTEND=noninteractive SYSTEMD_OFFLINE=1 apt-get --yes --no-install-recommends install "${packages[@]}"
    systemctl --root="$root" preset-all
    chroot "$root" passwd -l root
    # dpkg-query expands these fields, not the shell.
    # shellcheck disable=SC2016
    chroot "$root" dpkg-query -W '-f=${Package}\t${Version}\t${Architecture}\n' > "$workspace/installed-packages.tsv"
    mkdir "$workspace/debs"
    find "$root/var/cache/apt/archives" -maxdepth 1 -type f -name '*.deb' -exec cp -t "$workspace/debs" -- {} +
    # Include debootstrap's cache too, where available (layout varies by version).
    find "$root/var/cache" -type f -name '*.deb' -exec cp -t "$workspace/debs" -- {} +
    local deb
    : > "$workspace/package-cache.tsv"
    for deb in "$workspace"/debs/*.deb; do
        [[ -f $deb ]] || { fail 'no retained deb packages'; return 1; }
        printf '%s\t%s\t' "$(dpkg-deb -f "$deb" Package)" "$(dpkg-deb -f "$deb" Version)" >> "$workspace/package-cache.tsv"
        sha256sum "$deb" | sed "s|$workspace/||" >> "$workspace/package-cache.tsv"
    done
    verify_package_locks "$workspace" "$repository"
    cleanup_mounts
    rm -f "$root"/etc/ssh/ssh_host_* "$root/var/lib/dbus/machine-id" "$root/var/lib/systemd/random-seed" "$root/var/lib/urandom/random-seed"
    : > "$root/etc/machine-id"
    rm -f "$root/usr/sbin/policy-rc.d"
    if $foreign; then rm -f "$root/usr/bin/qemu-aarch64-static"; fi
    rm -rf "$root/var/lib/apt/lists"/* "$root/var/cache/apt/archives"/* "$root/var/log"/*
    cat > "$workspace/README.txt" <<'NOTICE'
Interim Debian ARM64 userspace prototype; no board boot support or installation.
No hardware, runtime, KVM or SD-write acceptance is established by this artifact.
All systemd presets disable services; production state, network and boot integration
remain pending. SSH has no keys or credentials and root password is locked.
Snapshot URLs, signed metadata, package inventory and cached .deb hashes are retained.
Only the base archive snapshot is included. Security archive snapshot selection
and the production security maintenance policy remain release integration work.
This is not a byte-reproducibility claim: builder tooling, generated timestamps,
package scripts and archive ordering have not been compared across clean builds.
NOTICE
    tar --numeric-owner --xattrs --acls -C "$root" -czf "$workspace/rootfs.tar.gz" .
    (cd "$workspace"; sha256sum rootfs.tar.gz > rootfs.tar.gz.sha256)
    printf 'Prototype userspace: %s/rootfs.tar.gz\n' "$workspace"
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then entrypoint "$@"; fi
