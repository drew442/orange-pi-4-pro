#!/usr/bin/env bash
# Experimental A733-only EL2 handoff. Never changes the known-booted recipe.
set -euo pipefail

# Sourceable guard for fixture tests; no writes, package queries or compilation.
el2_validate_inputs() {
    python3 - "$@" <<'PY'
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

repo, workspace, base, output = map(Path, sys.argv[1:])
# Git status must not refresh the shared source index.
os.environ["GIT_OPTIONAL_LOCKS"] = "0"
def require(ok, message):
    if not ok:
        raise SystemExit(message)
def regular(path):
    require(path.is_file() and not path.is_symlink(), f'Expected regular input: {path}')
def digest(path):
    regular(path)
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1048576), b''):
            h.update(block)
    return h.hexdigest()
# Reject aliases before canonicalization; output parent must already exist.
for path in (repo, workspace, base, output):
    require(path.is_absolute(), 'Paths must be absolute')
    require('..' not in path.parts, 'Parent traversal forbidden')
    for part in [path, *path.parents]:
        require(not part.is_symlink(), f'Symlink path forbidden: {part}')
    require(not str(path).startswith(('/dev/', '/proc/', '/sys/')), 'Device/pseudo-filesystem paths forbidden')
require(workspace.is_dir() and base.is_dir(), 'Workspace and base must exist')
require(output.parent == repo / 'out' and output.parent.is_dir(), 'Output must be directly under repository out/')
require(re.fullmatch(r'el2-diagnostic-[A-Za-z0-9][A-Za-z0-9._-]*', output.name), 'Use el2-diagnostic-NAME output')
require(not os.path.lexists(output), 'Output must not exist')
require(workspace != base and workspace not in output.parents and base not in output.parents, 'Output overlaps inputs')
lock = json.loads((repo / 'configs/diagnostic-sources.lock.json').read_text())
for name, tree in [('vendor', 'vendor'), ('uboot', 'u-boot'), ('kernel', 'kernel')]:
    directory = workspace / tree
    head = subprocess.check_output(['git', '-C', str(directory), 'rev-parse', 'HEAD'], text=True).strip()
    require(head == lock[name]['commit'], f'Unpinned {tree} HEAD')
    # Generated ignored compiler outputs are permitted; tracked changes are not.
    status = subprocess.check_output(['git', '-C', str(directory), 'status', '--porcelain', '--untracked-files=no'], text=True)
    require(not status, f'Tracked source changes in {tree}')
lines = (repo / 'configs/vendor-boot-inputs.sha256').read_text().splitlines()
require(len(lines) == 26, 'Expected 26 pinned firmware/tool inputs')
for line in lines:
    expected, relative = line.split(maxsplit=1)
    require(digest(workspace / 'vendor' / relative) == expected, f'Untrusted vendor input: {relative}')
toolchain = lock['uboot_toolchain']
require(digest(workspace / 'downloads' / toolchain['filename']) == toolchain['sha256'], 'Untrusted toolchain archive')
for patch in lock['patches']:
    require(digest(repo / patch['path']) == patch['sha256'], 'Untrusted baseline patch')
manifest = json.loads((repo / 'docs/evidence/diagnostic-20261104.json').read_text())
for name, facts in manifest['files'].items():
    path = base / name
    require(path.stat().st_size == facts['bytes'] and digest(path) == facts['sha256'], f'Untrusted base artifact: {name}')
for directory in ('notices', 'busybox-source'):
    require((base / directory).is_dir() and not (base / directory).is_symlink(), f'Missing base {directory}')
    for path in (base / directory).rglob('*'):
        require(not path.is_symlink() and (path.is_dir() or path.is_file()), f'Unsafe base notice/source: {path}')
# Fixed capacity planning guard on both source and output filesystems.
for directory in (workspace, output.parent):
    require(os.statvfs(directory).f_bavail * os.statvfs(directory).f_frsize > 4000000000, f'Need 4 GB free: {directory}')
PY
}


# File-only overlay: authenticate the baseline and preserve every other byte.
# Arguments: trusted repository, baseline raw image, new package, NEW raw image.
el2_overlay_boot_package() {
    python3 - "$@" <<'PYOVERLAY'
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

repo, baseline, package, output = map(Path, sys.argv[1:])
offset = 16793600
image_size = 268435456
package_size = 1392640

def require(ok, message):
    if not ok:
        raise SystemExit(message)

def safe_path(path):
    require(path.is_absolute() and '..' not in path.parts, 'Absolute paths without traversal required')
    require(not str(path).startswith(('/dev/', '/proc/', '/sys/')), 'Device/pseudo-filesystem paths forbidden')
    for part in [path, *path.parents]:
        require(not part.is_symlink(), f'Symlink path forbidden: {part}')

for path in (repo, baseline, package, output):
    safe_path(path)
require(output.parent.is_dir(), 'Output parent must already exist')
require(not os.path.lexists(output), 'Output must not exist')
for path in (baseline, package):
    require(stat.S_ISREG(path.stat().st_mode), f'Expected regular input: {path}')
manifest = json.loads((repo / 'docs/evidence/diagnostic-20261104.json').read_text())['files']
require(manifest['a733-diagnostic.img']['bytes'] == image_size, 'Unexpected baseline image size')
require(manifest['boot_package.fex']['bytes'] == package_size, 'Unexpected pinned package size')
require(image_size <= 1977614336 and image_size < 1800000000, 'Image exceeds SD capacity budget')
require(package.stat().st_size == package_size, 'Replacement package must match baseline package size')
require(offset + package_size < 33554432 < image_size, 'Package crosses pinned partition boundary')

def hash_stream(stream):
    checksum = hashlib.sha256()
    for block in iter(lambda: stream.read(1048576), b''):
        checksum.update(block)
    return checksum.hexdigest()

flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
with os.fdopen(os.open(baseline, flags), 'rb') as source, os.fdopen(os.open(package, flags), 'rb') as replacement:
    require(stat.S_ISREG(os.fstat(source.fileno()).st_mode), 'Baseline must be regular')
    require(stat.S_ISREG(os.fstat(replacement.fileno()).st_mode), 'Package must be regular')
    require(os.fstat(source.fileno()).st_size == image_size, 'Baseline image size mismatch')
    baseline_sha = hash_stream(source)
    require(baseline_sha == manifest['a733-diagnostic.img']['sha256'], 'Untrusted baseline raw image')
    source.seek(offset)
    old_package = source.read(package_size)
    require(hashlib.sha256(old_package).hexdigest() == manifest['boot_package.fex']['sha256'],
            'Baseline package region mismatches pinned package')
    replacement_bytes = replacement.read(package_size + 1)
    require(len(replacement_bytes) == package_size, 'Replacement package size changed')
    # O_EXCL plus O_NOFOLLOW forbids clobbering a file, symlink or device.
    fd = os.open(output, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, 'w+b') as target:
        require(stat.S_ISREG(os.fstat(target.fileno()).st_mode), 'Output must be regular')
        source.seek(0)
        for block in iter(lambda: source.read(1048576), b''):
            target.write(block)
        target.seek(offset)
        target.write(replacement_bytes)
        target.flush()
        os.fsync(target.fileno())
        require(os.fstat(target.fileno()).st_size == image_size, 'Output image size changed')
        # Byte comparisons cover both complete regions outside the package,
        # preserving boot0, partition table, boot script, filesystem and payloads.
        for start, length in ((0, offset), (offset + package_size, image_size - offset - package_size)):
            source.seek(start)
            target.seek(start)
            while length:
                count = min(length, 1048576)
                require(source.read(count) == target.read(count), 'Unexpected change outside boot package')
                length -= count
        target.seek(offset)
        require(target.read(package_size) == replacement_bytes, 'Replacement package readback failed')
        # Detect shared baseline mutation during copying/comparison.
        source.seek(0)
        require(hash_stream(source) == baseline_sha, 'Baseline changed during overlay')
        target.seek(0)
        output_sha = hash_stream(target)
print(json.dumps({
    'method': 'Authenticated baseline raw image copied; boot package region only replaced',
    'baseline_sha256': baseline_sha, 'output_sha256': output_sha,
    'image_bytes': image_size, 'package_offset': offset, 'package_bytes': package_size,
    'package_sha256': hashlib.sha256(replacement_bytes).hexdigest(),
    'outside_package_byte_comparison': 'identical',
    'preserved': 'All bytes outside package, including boot0, boot script and complete filesystem',
}, indent=2))
PYOVERLAY
}

[[ ${BASH_SOURCE[0]} == "$0" ]] || return 0
[[ $# == 3 ]] || { echo "Usage: $0 PINNED_VENDOR_WORKSPACE BASE_DIAGNOSTIC_ARTIFACTS NEW_OUTPUT_DIRECTORY" >&2; exit 2; }
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# Preserve supplied spelling for the symlink/traversal checks.
workspace=$1 base=$2 output=$3
el2_validate_inputs "$repo" "$workspace" "$base" "$output"
[[ $(. /etc/os-release; echo "$ID:$VERSION_ID") == ubuntu:22.04 && $(uname -m) == x86_64 ]] || {
    echo 'Requires recorded Ubuntu 22.04 x86_64 builder' >&2; exit 1;
}
# Full recorded package inventory pins native pack/assembly tools, including dtc,
# mkimage, make, binutils and filesystem tools. No package installation or fetch.
packages=$(mktemp)
trap 'rm -f "$packages"' EXIT
dpkg-query -W -f='${binary:Package}\t${Version}\t${Architecture}\n' > "$packages"
diff -u "$repo/configs/builder-packages.lock.tsv" "$packages"
rm "$packages"
trap - EXIT
# Atomic mkdir owns a new output; failed builds remain available for inspection.
mkdir "$output"
mkdir "$output/logs" "$output/artifacts"
exec > >(tee "$output/logs/recipe.log") 2>&1
export LC_ALL=C TZ=UTC SOURCE_DATE_EPOCH=1793750400
export KBUILD_BUILD_TIMESTAMP='2026-11-04 00:00:00 UTC'
export KBUILD_BUILD_USER=builder KBUILD_BUILD_HOST=jammy KBUILD_BUILD_VERSION=1
source "$repo/build/diagnostic.sh"
monitor_resources() {
    while :; do date -u; free -b; df -B1 "$workspace" "$output"; sleep 30; done
}
monitor_resources > "$output/logs/resource-samples.txt" 2>&1 &
monitor_pid=$!
cleanup() {
    kill "$monitor_pid" 2>/dev/null || :
    wait "$monitor_pid" 2>/dev/null || :
    # Retain all source/compiled evidence; remove only private extracted compiler.
    rm -rf "$output/toolchain-private"
}
trap cleanup EXIT
date -u > "$output/logs/build-start.txt"
artifacts=$output/artifacts
for name in Image uImage uInitrd initramfs.cpio.gz board.dtb kernel.config boot0_sdcard.fex; do
    cp "$base/$name" "$artifacts/$name"
    cmp "$base/$name" "$artifacts/$name"
done
cp -R "$base/notices" "$base/busybox-source" "$artifacts/"
cp "$repo/docs/evidence/diagnostic-20261104.json" "$artifacts/base-artifact-manifest.json"
cp "$repo/configs/diagnostic-sources.lock.json" "$repo/configs/vendor-boot-inputs.sha256" \
    "$repo/configs/builder-packages.lock.tsv" "$artifacts/"
cp "$repo/build/patches/uboot-env-nowhere-size.patch" "$repo/build/patches/uboot-a733-el2-handoff.patch" "$artifacts/"
# Archive and extract the pinned tracked tree, without git worktree or shared writes.
git --no-optional-locks -C "$workspace/u-boot" archive --format=tar HEAD > "$artifacts/uboot-pinned-source.tar"
uboot_tree=$output/uboot-source
mkdir "$uboot_tree" "$output/toolchain-private"
tar -xf "$artifacts/uboot-pinned-source.tar" -C "$uboot_tree"
git -C "$uboot_tree" init -q
# Archive extraction has no HEAD. Preserve the verified baseline SCM suffix
# explicitly; scripts/setlocalversion reads this before inspecting Git.
# Baseline image and serial log both contain U-Boot 2018.07-gb791be8-dirty.
printf '%s\n' '-gb791be8-dirty' > "$uboot_tree/.scmversion"
cp "$uboot_tree/.scmversion" "$artifacts/uboot.scmversion"
toolchain=gcc-linaro-7.4.1-2019.02-x86_64_arm-linux-gnueabi
tar -xJf "$workspace/downloads/$toolchain.tar.xz" -C "$output/toolchain-private"
uboot_cross=$output/toolchain-private/$toolchain/bin/arm-linux-gnueabi-
[[ $("${uboot_cross}gcc" -dumpfullversion) == 7.4.1 ]]
"${uboot_cross}gcc" --version > "$output/logs/uboot-compiler.txt"
git -C "$uboot_tree" apply "$artifacts/uboot-env-nowhere-size.patch"
git -C "$uboot_tree" apply "$artifacts/uboot-a733-el2-handoff.patch"
mkdir -p "$uboot_tree/vendor-export/bin"
uboot_make() {
    # The vendor Makefile otherwise queries unavailable Git history and can
    # override the exported epoch. Match the baseline's compiled UTC timestamp.
    (cd "$uboot_tree"; make DIRTY=dirty SOURCE_DATE_EPOCH=1772630733 CROSS_COMPILE="$uboot_cross" \
        LICHEE_CHIP_CONFIG_DIR="$uboot_tree/vendor-export" LICHEE_BUSSINESS='' \
        LICHEE_PLAT_OUT="$uboot_tree/vendor-export" "$@")
}
uboot_make -j2 sun60iw2p1_t736_defconfig > "$output/logs/uboot-build.log" 2>&1
"$workspace/kernel/scripts/config" --file "$uboot_tree/.config" --disable ENV_IS_IN_EXT4 --enable ENV_IS_NOWHERE \
    --disable CMD_SAVEENV --enable BOOTCMD_SKIP_MISC --disable SUNXI_SWITCH_SYSTEM --disable BOOTCOUNT_LIMIT \
    --set-str BOOTCOMMAND 'setenv boot_targets mmc0; run distro_bootcmd' --disable SUNXI_OTA_TURNNING
uboot_make olddefconfig >> "$output/logs/uboot-build.log" 2>&1
grep -qx CONFIG_ENV_IS_NOWHERE=y "$uboot_tree/.config"
grep -qx CONFIG_ENV_SIZE=0x20000 "$uboot_tree/.config"
grep -qx CONFIG_BOOTCMD_SKIP_MISC=y "$uboot_tree/.config"
grep -Fxq 'CONFIG_BOOTCOMMAND="setenv boot_targets mmc0; run distro_bootcmd"' "$uboot_tree/.config"
for disabled in SUNXI_SWITCH_SYSTEM BOOTCOUNT_LIMIT ENV_IS_IN_EXT4 CMD_SAVEENV SUNXI_OTA_TURNNING; do
    ! grep -qx "CONFIG_$disabled=y" "$uboot_tree/.config"
done
# Configuration should match the known-booted baseline exactly.
cmp "$base/uboot.config" "$uboot_tree/.config"
uboot_make -j2 >> "$output/logs/uboot-build.log" 2>&1
collect_uboot_provenance "$uboot_tree" "$artifacts" "$output/logs/uboot-build.log"
cp "$uboot_tree/u-boot" "$artifacts/uboot.elf"
cp "$uboot_tree/u-boot.map" "$artifacts/uboot.map"
cp "$uboot_tree/arch/arm/lib/bootm.o" "$artifacts/uboot-bootm.o"
cp "$uboot_tree/arch/arm/lib/bootm.c" "$artifacts/uboot-bootm.c"
"${uboot_cross}objdump" -dr "$uboot_tree/arch/arm/lib/bootm.o" > "$artifacts/uboot-bootm.disassembly.txt"
"${uboot_cross}objdump" -d "$uboot_tree/u-boot" > "$artifacts/uboot.disassembly.txt"
cp -R "$uboot_tree/Licenses" "$artifacts/notices/uboot-Licenses"
pack=$artifacts/pack
mkdir "$pack"
cp -R "$workspace/vendor/external/packages/pack-uboot/sun60iw2/bin/." "$pack/"
cp "$pack/boot0_sdcard_a733.fex" "$pack/boot0_sdcard.fex"
cmp "$pack/boot0_sdcard.fex" "$artifacts/boot0_sdcard.fex"
cp "$uboot_tree/u-boot.bin" "$pack/u-boot.fex"
cp "$pack/sys_config/sys_config.fex" "$pack/sys_config.fex"
cp "$pack/dts/u-boot-current.dts" "$pack/dts/orangepi4pro-u-boot.dts"
# Expose only hash-pinned pack tools, never arbitrary untracked files from
# the shared source directory on PATH.
mkdir "$output/pack-tools"
while read -r checksum relative; do
    if [[ $relative == external/packages/pack-uboot/tools/* ]]; then
        cp "$workspace/vendor/$relative" "$output/pack-tools/"
        printf '%s  %s\n' "$checksum" "$output/pack-tools/${relative##*/}" | sha256sum -c -
    fi
done < "$repo/configs/vendor-boot-inputs.sha256"
export PATH="$output/pack-tools:$PATH"
(
    cd "$pack"
    dtc -p 2048 -W no-unit_address_vs_reg -@ -O dtb -o orangepi4pro-u-boot.dtb -b 0 dts/u-boot-current.dts
    busybox unix2dos sys_config.fex
    script sys_config.fex
    cp orangepi4pro-u-boot.dtb sunxi.fex
    update_dtb sunxi.fex 4096
    update_uboot -no_merge u-boot.fex sys_config.bin
    busybox unix2dos boot_package.cfg
    dragonsecboot -pack boot_package.cfg
) > "$output/logs/boot-pack.log" 2>&1
for name in monitor.fex scp.fex boot0_sdcard_a733.fex; do
    cmp "$pack/$name" "$workspace/vendor/external/packages/pack-uboot/sun60iw2/bin/$name"
done
cp "$pack/boot_package.fex" "$artifacts/"
el2_overlay_boot_package "$repo" "$base/a733-diagnostic.img" "$artifacts/boot_package.fex" \
    "$artifacts/a733-el2-diagnostic.img" > "$artifacts/raw-image-provenance.json"
cp "$artifacts/raw-image-provenance.json" "$output/logs/assembly.log"
(( $(stat -c %s "$artifacts/a733-el2-diagnostic.img") <= 1977614336 ))
(( $(stat -c %s "$artifacts/a733-el2-diagnostic.img") < 1800000000 ))
gzip -n -c "$artifacts/a733-el2-diagnostic.img" > "$artifacts/a733-el2-diagnostic.img.gz"
# Recheck all shared inputs after compilation and packing, before publication.
el2_validate_existing_inputs() {
    # The sourceable validator requires a new output; use an unused sibling name.
    el2_validate_inputs "$repo" "$workspace" "$base" "$output-postcheck"
}
el2_validate_existing_inputs
tar --sort=name --mtime="@$SOURCE_DATE_EPOCH" --owner=0 --group=0 --numeric-owner \
    --exclude=__pycache__ -C "$repo" -cf - build configs licenses | gzip -n > "$artifacts/build-recipe.tar.gz"
date -u > "$output/logs/build-end.txt"
kill "$monitor_pid" 2>/dev/null || :
wait "$monitor_pid" 2>/dev/null || :
cp -R "$output/logs" "$artifacts/build-logs"
python3 - "$artifacts" "$repo" <<'PY'
import hashlib
import json
from pathlib import Path
import sys
root, repo = map(Path, sys.argv[1:])
def checksum(p):
    h = hashlib.sha256()
    with p.open('rb') as f:
        for block in iter(lambda: f.read(1048576), b''):
            h.update(block)
    return h.hexdigest()
files = {str(p.relative_to(root)): {'bytes': p.stat().st_size, 'sha256': checksum(p)}
         for p in sorted(root.rglob('*')) if p.is_file()}
base = json.loads((root / 'base-artifact-manifest.json').read_text())
immutable = ['Image', 'uImage', 'uInitrd', 'initramfs.cpio.gz', 'board.dtb', 'kernel.config', 'boot0_sdcard.fex']
for name in immutable:
    assert files[name] == base['files'][name], name
manifest = {
    'schema': 1, 'board': 'Orange Pi 4 Pro A733',
    'classification': 'experimental EL2 diagnostic; hardware UNTESTED',
    'hardware_acceptance': 'Requires physical boot, all CPUs EL2 and actual KVM guest; none established by this build',
    'handoff': {'baseline_service': '0x8000ff04', 'baseline_spsr': '0x3c5',
                'expected_service': '0x8000ff40', 'expected_spsr': '0x3c9',
                'arguments': 'processed kernel_entry, DTB, 1; normal bootm initrd/cleanup preserved'},
    'source': json.loads((root / 'diagnostic-sources.lock.json').read_text()),
    'immutable_base_files': immutable,
    'raw_image_construction': json.loads((root / 'raw-image-provenance.json').read_text()),
    'firmware_policy': 'Opaque monitor/SCP/boot0 unmodified; only rebuilt U-Boot and packed boot package changed',
    'uboot_version_provenance': {'scm_suffix': '-gb791be8-dirty',
                                'source_date_epoch': 1772630733,
                                'compiled_timestamp_utc': '2026-03-04 13:25:33',
                                'basis': 'matched pinned baseline image version string'},
    'reproducibility': 'Vendor random challenge retained; no bit reproducibility claim',
    'files': files,
}
(root / 'artifact-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
(root / 'SHA256SUMS').write_text(''.join(f"{facts['sha256']}  {name}\n" for name, facts in files.items()))
PY
echo "Experimental candidate: $artifacts; physical EL2/KVM acceptance remains UNTESTED."
