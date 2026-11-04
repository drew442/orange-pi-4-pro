#!/usr/bin/env bash
# Guard and mount ownership tests; never runs debootstrap or mounts a filesystem.
set -euo pipefail
repository=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
source "$repository/build/host-userspace.sh"
fixture=$(mktemp -d)
trap 'rm -rf -- "$fixture"' EXIT
mkdir -p "$fixture/repo/out" "$fixture/elsewhere"
base=$fixture/repo
validate_workspace "$base/out/host-userspace-test" "$base"
for candidate in / "$base" "$base/out" "$base/out/other" "$base/out/host-userspace-" "$base/out/../out/host-userspace-test"; do
    if validate_workspace "$candidate" "$base" 2>/dev/null; then
        printf 'Unsafe path accepted: %s\n' "$candidate" >&2; exit 1
    fi
done
mkdir "$base/out/host-userspace-existing"
if validate_workspace "$base/out/host-userspace-existing" "$base" 2>/dev/null; then exit 1; fi
ln -s "$fixture/elsewhere" "$base/out/host-userspace-link"
if validate_workspace "$base/out/host-userspace-link" "$base" 2>/dev/null; then exit 1; fi
rm "$base/out/host-userspace-link"
ln -s "$fixture/elsewhere" "$base/alias"
if validate_workspace "$base/alias/host-userspace-test" "$base/alias/.." 2>/dev/null; then exit 1; fi

calls=$fixture/mount-calls
mount() { printf 'mount %s\n' "$*" >> "$calls"; [[ $* != *FAIL* ]]; }
mountpoint() { return 0; }
umount() { printf 'umount %s\n' "$*" >> "$calls"; }
mount_owned -t tmpfs tmpfs "$fixture/root/dev"
mount_owned -t proc proc "$fixture/root/proc"
if mount_owned -t proc proc "$fixture/FAIL"; then exit 1; fi
[[ ${#owned_mounts[@]} == 2 ]]
cleanup_mounts
[[ ${#owned_mounts[@]} == 0 ]]
mapfile -t lines < "$calls"
[[ ${lines[3]} == "umount -- $fixture/root/proc" ]]
[[ ${lines[4]} == "umount -- $fixture/root/dev" ]]
[[ ${#lines[@]} == 5 ]]
# Empty cleanup must never try to unmount existing host paths.
cleanup_mounts
[[ $(wc -l < "$calls") == 5 ]]
# Partial failures retain their original order; a later EXIT cleanup can retry.
owned_mounts=("$fixture/root/dev" "$fixture/root/dev/pts" "$fixture/root/proc")
umount() {
    printf 'umount %s\n' "$*" >> "$calls"
    [[ $2 == "$fixture/root/proc" ]]
}
if cleanup_mounts 2>/dev/null; then
    printf 'Unmount failure was ignored\n' >&2; exit 1
fi
[[ ${#owned_mounts[@]} == 2 ]]
[[ ${owned_mounts[0]} == "$fixture/root/dev" && ${owned_mounts[1]} == "$fixture/root/dev/pts" ]]
umount() { printf 'retry %s\n' "$*" >> "$calls"; }
cleanup_mounts
[[ ${#owned_mounts[@]} == 0 ]]
# A stale record is discarded without attempting an unmount.
owned_mounts=("$fixture/root/stale")
mountpoint() { return 32; }
umount() { printf 'Stale mount unmount attempted\n' >&2; return 99; }
cleanup_mounts
[[ ${#owned_mounts[@]} == 0 ]]
# An inspection error must retain ownership rather than silently discard it.
owned_mounts=("$fixture/root/unknown")
mountpoint() { return 1; }
if cleanup_mounts 2>/dev/null; then exit 1; fi
[[ ${owned_mounts[0]} == "$fixture/root/unknown" ]]
owned_mounts=()

# During second-stage only /dev is ours; proc/pts belong to debootstrap.
mountpoint() { return 0; }
umount() { :; }
mount_owned -t tmpfs tmpfs "$fixture/root/dev"
chroot() {
    [[ ${#owned_mounts[@]} == 1 && ${owned_mounts[0]} == "$fixture/root/dev" ]] || return 97
    printf 'chroot %s\n' "$*" >> "$calls"
}
second_stage_and_mounts "$fixture/root"
[[ ${#owned_mounts[@]} == 3 ]]
[[ ${owned_mounts[1]} == "$fixture/root/dev/pts" && ${owned_mounts[2]} == "$fixture/root/proc" ]]
cleanup_mounts
# Failed second-stage must never create our proc/pts mounts.
mount_owned -t tmpfs tmpfs "$fixture/root/dev"
chroot() { [[ $2 == /bin/true ]]; }
if second_stage_and_mounts "$fixture/root"; then exit 1; fi
[[ ${#owned_mounts[@]} == 1 ]]
cleanup_mounts

# Exercise the actual exec wrapper against a fake executable, without unshare/root.
mkdir "$fixture/bin"
cat > "$fixture/bin/unshare" <<'MOCK'
#!/usr/bin/env bash
set -eu
[[ $# == 10 ]]
[[ $1 == --mount && $2 == --propagation && $3 == private && $4 == --fork ]]
[[ $5 == bash && $6 == -c && $7 == 'source "$1"; main "$2"' && $8 == bash ]]
[[ $9 == '/fixture/script path.sh' && ${10} == '/fixture/workspace path' ]]
MOCK
chmod +x "$fixture/bin/unshare"
(PATH="$fixture/bin:$PATH"; enter_mount_namespace '/fixture/script path.sh' '/fixture/workspace path')
# Invalid invocation fails without invoking build commands or creating output.
if main "$base/out/host-userspace-test" extra 2>/dev/null; then exit 1; fi
if entrypoint "$base/out/host-userspace-test" extra 2>/dev/null; then exit 1; fi
[[ ! -e $base/out/host-userspace-test ]]
# Downloads must succeed and match their pinned content before extraction/use.
curl() {
    [[ $1 == --fail && $2 == --location && $3 == --retry && $4 == 3 && $5 == --output ]] || return 98
    [[ $7 == https://example.invalid/pinned ]] || return 99
    printf 'authenticated fixture\n' > "$6"
}
expected=$(printf 'authenticated fixture\n' | sha256sum | cut -d ' ' -f 1)
fetch_verified https://example.invalid/pinned "$expected" "$fixture/download"
if fetch_verified https://example.invalid/pinned "$(printf '%064d' 0)" "$fixture/bad" 2>/dev/null; then
    printf 'Mismatched source hash accepted\n' >&2; exit 1
fi
curl() { return 22; }
if fetch_verified https://example.invalid/pinned "$expected" "$fixture/absent" 2>/dev/null; then
    printf 'Failed download accepted\n' >&2; exit 1
fi
[[ ! -e $fixture/absent ]]
# A signed snapshot must also reproduce the recorded versions and archive hashes.
mkdir -p "$base/configs" "$fixture/inventories"
printf 'a\t1\nb\t2\n' > "$base/configs/host-userspace-packages.lock.tsv"
printf 'b\t2\na\t1\n' > "$fixture/inventories/installed-packages.tsv"
printf 'a\t1\t0123  debs/a.deb\n' > "$base/configs/host-userspace-package-hashes.lock.tsv"
cp "$base/configs/host-userspace-package-hashes.lock.tsv" "$fixture/inventories/package-cache.tsv"
verify_package_locks "$fixture/inventories" "$base"
printf 'a\t3\nb\t2\n' > "$fixture/inventories/installed-packages.tsv"
if verify_package_locks "$fixture/inventories" "$base" >/dev/null 2>&1; then exit 1; fi
cp "$base/configs/host-userspace-packages.lock.tsv" "$fixture/inventories/installed-packages.tsv"
printf 'a\t1\t9999  debs/a.deb\n' > "$fixture/inventories/package-cache.tsv"
if verify_package_locks "$fixture/inventories" "$base" >/dev/null 2>&1; then exit 1; fi
rm "$fixture/inventories/package-cache.tsv"
if verify_package_locks "$fixture/inventories" "$base" >/dev/null 2>&1; then exit 1; fi
printf 'host-userspace guard, namespace, mount lifecycle and source verification tests passed\n'
