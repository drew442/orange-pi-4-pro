# Build-host root-volume capacity proposal

Status: prepared and dry-run only; execution requires operator approval.
No logical-volume or filesystem resize has been performed.

The Ubuntu 22.04 builder at `build.drewnet.online` has a 133,085,265,920-byte
`ubuntu-vg` volume group, with 66,542,632,960 bytes unallocated. Its root ext4
filesystem currently uses `/dev/ubuntu-vg/ubuntu-lv`, size 66,542,632,960 bytes,
LV UUID `Wacdne-hf6p-osvg-5IiF-iVD0-Dlgt-6iflHh`. `findmnt /` confirms ext4 on
`/dev/mapper/ubuntu--vg-ubuntu--lv`, major/minor `252:0`. The VG is on the build
VM's existing `/dev/sda3`; the Orange Pi's NVMe is a different machine/target.

## Proposed change

Allocate 20 GiB (21,474,836,480 bytes) from that existing VG to the root LV,
then grow its mounted ext4 filesystem. The resulting LV size is exactly
88,017,469,440 bytes (81.97 GiB); 45,067,796,480 bytes remain unallocated in the
VG. At preparation, filesystem free space was about 7.68 GB; the full archive
diagnostic later consumed about 1 GiB. Growth should add 20 GiB less filesystem
metadata overhead; verify actual free space after applying it. Recheck all build guards.
This keeps the existing partitions and volume/filesystem identities.

[Ubuntu's lvextend manual](https://manpages.ubuntu.com/manpages/jammy/man8/lvextend.8.html)
documents allocation from VG free extents. The
[resize2fs manual](https://manpages.ubuntu.com/manpages/jammy/man8/resize2fs.8.html)
documents online ext4 enlargement on supported kernels. These commands grow the
volume and filesystem; they are a host-wide storage change, not ordinary image
file staging. The existing package-install authorization is not being treated
as approval for this proposal.

## Completed preparation

Read-only VG/LV/device and root-mount inventories identify the exact target.
Both relative `+20G` and exact-size LVM test invocations succeed. LVM test mode
explicitly disables metadata updates; a subsequent LV/VG query confirms the
original sizes and free extents remain unchanged. Raw records are retained at
`out/build-host-capacity-preparation-20261106/` on codex.

A root-private metadata backup is retained on the builder:
`/var/lib/a733-builder-lvm-preparation-20261106/ubuntu-vg.before.vg`, SHA-256
`2b21a23134c124fab4bfbc9c7d2cf3d28e825df83e7a6c9337f16b0eeaf6815f`.
This backs up LVM metadata, not filesystem data. It must not be blindly restored
with the old LV size after the filesystem has grown. Existing build/source/image
archives remain available on codex.

## Concrete application sequence after approval

Before applying, stop compilation/packaging jobs and revalidate hostname,
LV UUID, root mount, ext4 type, original LV size and at least 20 GiB free in the
same VG. Stop on any mismatch; this plan is for the recorded volume only.
Then run on `build.drewnet.online`:

```bash
set -euo pipefail
LV=/dev/ubuntu-vg/ubuntu-lv
test "$(hostname -s)" = build
test "$(sudo -n lvs --noheadings -o lv_uuid "$LV" | xargs)" = Wacdne-hf6p-osvg-5IiF-iVD0-Dlgt-6iflHh
test "$(sudo -n lvs --noheadings --units b --nosuffix -o lv_size "$LV" | xargs)" = 66542632960
test "$(findmnt -n --target / --output SOURCE)" = /dev/mapper/ubuntu--vg-ubuntu--lv
test "$(findmnt -n --target / --output FSTYPE)" = ext4
test "$(sudo -n vgs --noheadings --units b --nosuffix -o vg_free ubuntu-vg | xargs)" -ge 21474836480
sudo -n lvextend --size 88017469440B "$LV"
test "$(sudo -n lvs --noheadings --units b --nosuffix -o lv_size "$LV" | xargs)" = 88017469440
sudo -n resize2fs "$LV"
sudo -n lvs --units b --nosuffix -o lv_path,lv_uuid,lv_size
sudo -n vgs --units b --nosuffix -o vg_name,vg_free
findmnt --target / --output SOURCE,FSTYPE,MAJ:MIN,OPTIONS
df -B1 /
```

Verify the LV reaches the exact target size, the same root filesystem remains
mounted read/write and free space clears the unchanged 22 GiB NVMe staging
threshold. If LV growth succeeds but filesystem growth fails, retain the larger
LV and diagnose the error; do not reduce it or restore old volume metadata.
This proposal authorizes no Orange Pi NVMe write or SPI programming. Hardware
installation, actual KVM execution and release acceptance remain pending.
