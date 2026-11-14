> **Historical record — not active instructions or release gates.**
> Superseded by the [manual beta scope](../../beta-scope.md) and
> [current status](../../implementation-status.md). Original observations retain
> their recorded configuration and limitations.

# Additional build-host capacity proposal

Status: **authorized and executed** on November 9. The operator also approved
all available free space on the Ubuntu build host for future use/expansion
without further capacity permission requests. Continue exact-target checks and
independent review. The previous 20 GiB growth remains historical.

On `build.drewnet.online`, allocate **6 GiB (6,442,450,944 bytes)** from the existing
`ubuntu-vg` to `/dev/ubuntu-vg/ubuntu-lv`, then enlarge its mounted ext4 filesystem.
The LV grew from **88,017,469,440** to **94,459,920,384 bytes**. Its UUID is
`Wacdne-hf6p-osvg-5IiF-iVD0-Dlgt-6iflHh`, and `/` is ext4 on device `252:0`.
The VG had **45,067,796,480 bytes** unallocated; **38,625,345,536 bytes**
remain. This concerns the Ubuntu builder, separate from the Orange Pi's
already authorized SD/NVMe/SPI.

Before growth, builder free space was **19,218,972,672 bytes**, below the unchanged full-NVMe
admission of **23,760,999,827 bytes**. Growth leaves **25,303,740,416 bytes free**, passing the guard and allowing the coherent protected-image
construction and replay. Recheck actual free space and all guards before each build.
The limited surveyed generated-output candidates do not cover the shortfall;
current sources, frozen build environment and retained backups are preserved.

The exact-size `lvextend --test --size 94459920384B` succeeds with metadata
updates explicitly disabled. Post-test queries still show the original LV size
and VG free space. Raw results are retained in
`out/serial-recovery-20261109/builder-growth-preparation.{txt,stderr}`.
A root-private LVM metadata backup is at
`/var/lib/a733-builder-lvm-preparation-20261109/ubuntu-vg.before.vg`, SHA-256
`ac982fbae17b73bfb6fd262716c24ec8df97c2d481ec60acedf97abd2c2afd67`.
This is not a filesystem-data backup. Never restore its old LV size after
filesystem growth.

The prepared application script revalidates hostname, exact LV UUID/old size,
root mount/type/device, free VG extents and metadata backup before growth. It
uses the exact final LV size, then `resize2fs`, and records final metadata and
capacity. A failed filesystem resize must be diagnosed with the actual enlarged
LV size before resuming; automatic shrink/metadata rollback is not attempted.

The [Ubuntu lvextend manual](https://manpages.ubuntu.com/manpages/jammy/man8/lvextend.8.html)
and [resize2fs manual](https://manpages.ubuntu.com/manpages/jammy/man8/resize2fs.8.html)
describe these operations. [Independent review](../../evidence/build-host-capacity-addition-20261109.json) passes.
The original attempt stopped before changing storage because formatted
`findmnt` output padded the device number. The corrected raw-output guard
passed a complete read-only `--check`, then execution passed. Reviewed script
SHA-256 is `4ce6988213ab5002d2f4bfb90816bec9306f28393b01e94ae9009abf8385b250`.

## Applied guarded sequence

This exact sequence completed after authorization. Its old-size guard prevents
reuse now; retain the target checks when preparing any later approved expansion.

```bash
#!/bin/bash
# Proposal only: execute after specific authorization for this additional 6 GiB.
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C TZ=UTC
[[ $# == 1 && ( $1 == --apply || $1 == --check ) ]]
A733_BUILD_LV=/dev/ubuntu-vg/ubuntu-lv
[[ $(hostname -s) == build ]]
[[ $(sudo -n lvs --noheadings -o lv_uuid "$A733_BUILD_LV" | xargs) == Wacdne-hf6p-osvg-5IiF-iVD0-Dlgt-6iflHh ]]
[[ $(sudo -n lvs --noheadings --units b --nosuffix -o lv_size "$A733_BUILD_LV" | xargs) == 88017469440 ]]
[[ $(findmnt -nr --target / --output SOURCE) == /dev/mapper/ubuntu--vg-ubuntu--lv ]]
[[ $(findmnt -nr --target / --output FSTYPE) == ext4 ]]
[[ $(findmnt -nr --target / --output MAJ:MIN) == 252:0 ]]
[[ $(sudo -n vgs --noheadings --units b --nosuffix -o vg_free ubuntu-vg | xargs) -ge 6442450944 ]]
printf '%s  %s\n' ac982fbae17b73bfb6fd262716c24ec8df97c2d481ec60acedf97abd2c2afd67 /var/lib/a733-builder-lvm-preparation-20261109/ubuntu-vg.before.vg | sudo -n sha256sum -c -
sudo -n test ! -e /var/lib/a733-builder-lvm-preparation-20261109/ubuntu-vg.after.vg
if [[ $1 == --check ]]; then
    printf 'Prepared target guards pass; no LV or filesystem change performed.\n'
    exit 0
fi
# Exact final size prevents accidental repeated additive growth.
sudo -n lvextend --size 94459920384B "$A733_BUILD_LV"
[[ $(sudo -n lvs --noheadings --units b --nosuffix -o lv_size "$A733_BUILD_LV" | xargs) == 94459920384 ]]
# If this fails, stop with the LV enlarged and filesystem unchanged/unknown.
# Revalidate before resuming resize2fs; do not shrink or restore old VG metadata.
sudo -n resize2fs "$A733_BUILD_LV"
sudo -n vgcfgbackup -f /var/lib/a733-builder-lvm-preparation-20261109/ubuntu-vg.after.vg ubuntu-vg
sudo -n sha256sum /var/lib/a733-builder-lvm-preparation-20261109/ubuntu-vg.after.vg
sudo -n lvs --reportformat json --units b --nosuffix -o lv_path,lv_uuid,lv_size "$A733_BUILD_LV"
sudo -n vgs --reportformat json --units b --nosuffix -o vg_name,vg_free ubuntu-vg
findmnt -nr --target / --output SOURCE,FSTYPE,MAJ:MIN
df -B1 /
```
