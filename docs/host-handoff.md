# NVMe root/state handoff prototype

The prepared builder produced a **4,761,354-byte host initramfs**, SHA-256
`2806c4cad490a2eda76dbbbf3803efc02a34144b783ed9d2a722f5bc5f3397b3`.
It contains the pinned diagnostic BusyBox, authenticated ARM64 storage tools,
matching A733 modules and the new host handoff script. The original diagnostic
SD image is unchanged. This initramfs has not booted on the board and is not a
complete host installation or an SD image.

## Boot behavior and state contract

`configs/host-init` executes automatically only as `/init` at PID 1. It reads
four data-only configuration fields: root/state filesystem UUIDs, the expected
root payload marker hash, and kernel release. Unknown/duplicate fields and
malformed bytes fail validation. The selected root/state must satisfy the
[NVMe resolver contract](host-storage-boot.md).

Root mounts ext4 `ro,noload,nodev`; its read-only mount, payload marker hash,
expected Debian init/loader paths and compatible kernel release are checked.
Runtime marker verification does not authenticate every filesystem byte; the
packager authenticates the complete input image, and installation must verify
the written payload separately.

State first mounts `ro,noload,nodev,nosuid`. Before any state journal replay or
write, it must contain:

- A root-owned regular `.a733-state` file with exactly
  `a733-state-v1\nSTATE_UUID\n`, substituting the configured UUID.
- Root-owned real directories `var`, `root`, `home` and `identity`, without
  group/world writes or special mode bits. State's filesystem root has the same
  ownership/mode restrictions.
- A root-owned regular `identity/machine-id`, exactly 32 lowercase hex digits plus
  newline, excluding the all-zero value; mode 0400, 0444, 0600 or 0644.

The future provisioner must seed `var` from the matching userspace, preserve its
package ownership/modes, prepare home/admin configuration and generate a unique
machine-id and state UUID for each installation. Boot never formats or populates
missing state. State format/migrations must remain compatible with both root
slots or have a tested restore path.

After read-only validation, state is unmounted, its block identity/UUID is
rechecked, and it mounts read-write on NVMe. Journal recovery may occur at this
point. The marker/layout/machine-id are checked again. State supplies `/var`,
`/root`, `/home` and `/etc/machine-id` through bind mounts. An ephemeral `/etc`
overlay keeps the root image unchanged; upper/work share bounded `/run` tmpfs.
Upper and base `/etc` are 0755 so unprivileged services can read configuration;
work is 0700. The pinned kernel has overlayfs and tmpfs xattrs built in, satisfying
the [overlayfs requirements](https://www.kernel.org/doc/html/v6.6/filesystems/overlayfs.html).
Each final `/run` and `/tmp` has a 64 MiB tmpfs limit. These budgets require board
memory/workload validation. Persistent configuration beyond machine-id remains
integration work; ephemeral `/etc` edits do not survive reboot.

The handoff drops unused initial RAM mounts, moves proc/sys/dev into the new
root and transfers PID 1 to `/sbin/init`. Earlier validation failures attempt
reverse cleanup and a physical serial recovery shell without automatic reboot
or SD fallback. The final
[`switch_root` exec](https://github.com/mirror/busybox/blob/1_30_1/util-linux/switch_root.c)
is an irreversible boundary: failure inside that applet or the new dynamic init
cannot return to the script's recovery shell. Hardware recovery remains required.

## Verified preparation

From the prepared builder repository checkout:

```sh
sudo python3 build/host-initramfs.py \
  /home/drew/orange-pi-4-pro-build/recipe/out/host-userspace-bookworm-20261001-01/rootfs.tar.gz \
  /home/drew/orange-pi-4-pro-build/artifacts \
  /home/drew/orange-pi-4-pro-build/busybox/bin/busybox \
  /home/drew/orange-pi-4-pro-build/project/out/host-payload-20261104-01/root-A.ext4 \
  e7330000-0000-4000-8000-000000000001 \
  "$(pwd -P)/out/host-initramfs-20261104-01" --slot A
sudo python3 tests/verify_host_init_ash.py \
  /home/drew/orange-pi-4-pro-build/busybox/bin/busybox
sudo python3 tests/verify_host_etc_overlay.py
```

These commands passed. Use a new output name on another run. The UUID above is
a **fixture configuration**, not an identified or provisioned physical state
partition. This prototype does not authorize any physical write. Root is needed
to read the existing private payload directory and run isolated chroot/mount
tests; packaging itself performs no mounts, network access or device writes.

The packager pins the full root image, userspace archive, BusyBox and compatible
kernel/DTB/config/module archive against committed evidence. It requires built-in
NVMe/ext4/overlayfs/tmpfs/tmpfs-xattr/devtmpfs support and emits a deterministic
root-owned newc/gzip archive plus file BOM and recipe hashes. Root marker reads
use `debugfs` through the already authenticated descriptor. Full clean-builder
reproducibility remains open.

Evidence:

- [Build inputs/output](evidence/host-initramfs-20261104.json) and
  [independent archive checks](evidence/host-initramfs-archive-20261104.json): all
  345 regular-file hashes, 424 archive entries, paths, ownership, timestamps,
  symlink, configuration and current source scripts checked.
- [Pinned ARM64 ash](evidence/host-init-ash-20261104.json): required applets and
  eight function fixtures passed through binfmt/chroot, with mocked kernel/mount
  input. Full handoff has 49 unprivileged Bash fixtures covering ordering/failures.
- [Actual builder overlay test](evidence/host-etc-overlay-20261104.json): private
  directory/tmpfs mounts on Linux 6.8.0-138-generic preserved the read-only lower,
  allowed an unprivileged read, confined writes to RAM and enforced 64 MiB.

The A733 kernel has not performed these mounts. Dirty-state journal recovery,
systemd startup, provisioned identities, management access, NVMe boot, KVM guest
execution and zero physical SD writes remain unproven. Next work is state
provisioning, persistent management configuration and packaging a complete host
boot selection while retaining the diagnostic recovery image.
