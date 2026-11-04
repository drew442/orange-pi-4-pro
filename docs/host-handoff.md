# NVMe root/state handoff prototype

The prepared host initramfs is **4,761,822 bytes**, SHA-256
`544bedbf1ef6793c21a72006fd82419ae3738e568afc8069a3ec783ad381096a`. Its
immutable `/etc/a733-boot.conf` contains exactly `STATE_UUID` and
`KERNEL_RELEASE=6.6.98-a733-diag`; it does not embed a root choice. The active
schema-1 selector is `boot/selected.conf` on NVMe state. The original diagnostic
image is unchanged. No physical boot, SD write, NVMe installation or KVM guest
has been demonstrated.

## Boot behavior and state contract

`configs/host-init` reads the immutable configuration as bounded data, never
shell syntax. It resolves the requested state UUID, mounts state read-only with
`noload`, and validates the v1 marker, layout, ownership and machine-id before
any write. It then unmounts and rechecks identity, UUID and GPT, remounts
read-write (allowing journal recovery), revalidates and reads the selector.
The immutable configuration read is bounded to 2048 bytes and the selector
read to 4096 bytes; marker and identity checks require exact values.
Unknown, duplicate or malformed fields fail. There is no automatic fallback,
attempt counter or boot-time selector write.

The [resolver contract](host-storage-boot.md) scans only eligible NVMe p4 state
partitions. Duplicate UUIDs and unrecognized eligible p4 partitions fail
closed. Fixed GPT roles are p1 boot, p2 root A, p3 root B, p4 state and p5
guests. Selector choice validates the selected namespace/GPT and same-parent
root/state roles; it does not probe the unselected root filesystem, so damage
to an unused slot does not prevent a manual selector rollback.

The selected root mounts ext4 read-only and its marker and expected init paths
are checked. Runtime marker verification does not authenticate every filesystem
byte; the root-selection preparer authenticates the pinned complete image and
installation must verify written payloads separately. State marker is a
root-owned regular `.a733-state` file containing exactly
`a733-state-v1\nSTATE_UUID\n`. Required root-owned real directories are `var`,
`root`, `home` and `identity`, with no group/world writes or special mode bits;
the state root has the same restrictions. `identity/machine-id` is exactly 32
lowercase hex digits and newline, nonzero, root-owned, and mode 0400, 0444,
0600 or 0644.

State supplies `/var`, `/root`, `/home` and `/etc/machine-id`. The existing
`/run` is a 64 MiB tmpfs; nested state is moved to the new root before
bindings. `/etc` uses an ephemeral overlay and `/tmp` is a 64 MiB tmpfs. These
budgets need board memory/workload validation. Persistent management activation
and SSH host-key generation remain open. The final `switch_root`/init boundary
and physical recovery remain hardware gates. See [overlayfs requirements](https://www.kernel.org/doc/html/v6.6/filesystems/overlayfs.html).

## Verified preparation

Prepare an initial root selector from a pinned full 4 GiB image; it checks the
image, slot marker and boot stack, then creates only a new regular output:

```sh
sudo python3 build/root-selection.py \
  /home/drew/orange-pi-4-pro-build/project/out/host-payload-20261104-01/root-A.ext4 \
  "$(pwd -P)/out/root-selection-A.conf" --slot A
```

State creation now requires that selector and seeds boot mode 0700 and
`boot/selected.conf` mode 0600 using an atomic helper:

```sh
sudo python3 build/host-state.py \
  /home/drew/orange-pi-4-pro-build/recipe/out/host-userspace-bookworm-20261001-01/rootfs.tar.gz \
  /home/drew/orange-pi-4-pro-build/state-fixture-inputs-20261104/management.json \
  /home/drew/orange-pi-4-pro-build/state-fixture-inputs-20261104/admin.pub \
  "$(pwd -P)/out/host-state-20261104-03" \
  --boot-selection "$(pwd -P)/out/root-selection-A.conf"
```

The generic initramfs command no longer takes a root image or slot:

```sh
sudo python3 build/host-initramfs.py \
  /home/drew/orange-pi-4-pro-build/recipe/out/host-userspace-bookworm-20261001-01/rootfs.tar.gz \
  /home/drew/orange-pi-4-pro-build/artifacts \
  /home/drew/orange-pi-4-pro-build/busybox/bin/busybox \
  dfad484b-9958-4ce4-b92c-725734f515fd \
  "$(pwd -P)/out/host-initramfs-20261104-03"
```

Current builder outputs are
`/home/drew/orange-pi-4-pro-build/host-state-clean-20261104/out/host-state-20261104-03`
and
`/home/drew/orange-pi-4-pro-build/host-state-clean-20261104/out/host-initramfs-20261104-03`. State UUID is
`dfad484b-9958-4ce4-b92c-725734f515fd`; the raw 8 GiB state image SHA-256 is
`faa22756adb5275cabdc27be88b813d533f41175daf3ee1d905daa3884535421`, and its
gzip is 39,828,609 bytes, SHA-256
`6018fd1d25c06a6f6329dea8fdc5183d19b470931b5ab8d6220e0275ba0cd2a0`.
The [selector guide](nvme-root-selection.md) describes format and limitations.
The `atomic_store` helper requires its caller to authorize and quiesce the
directory. Fault fixtures confirm the old record before rename and report a
post-rename directory-sync failure as published with durability unknown. This
is not a complete updater or automatic rollback mechanism.

Evidence: [initramfs](evidence/nvme-selection-initramfs-20261104.json),
[archive](evidence/nvme-selection-archive-20261104.json),
[ARM ash](evidence/nvme-selection-ash-20261104.json),
[state verification](evidence/nvme-selection-state-20261104.json), and
[state build context](evidence/nvme-selection-state-build-20261104.json).
Archive checks verified 345 regular files and 424 entries. Ten pinned ARM ash
checks validated both slots; resolver fixtures passed 41 Bash and exact ARM ash
cases. Host-init had 84 Bash fixtures and Python had 84 cases (including two
private-state cases on Jammy). A GPT probe passed on a 128 MiB regular file;
there were no block devices involved. These checks do not establish board boot.

A [clean Git checkout repeat](evidence/nvme-selection-rebuild-20261104.json)
of `b9b3791` produced a byte-identical generic archive using the same pinned
inputs and state UUID. This is packaging reproduction on the prepared builder,
not whole-stack or hardware reproduction.

The state fixture uses test MAC/CIDR and a public key whose temporary private
key was deleted; it is not deployment state. Actual SSH key, management
activation, complete SD host image, installer/updater/target activation,
health checks, KVM and all physical acceptance remain open. Root-only selector
changes do not repack SD; kernel, DTB or initramfs changes require explicit boot
maintenance. The SD must remain read-only with zero routine writes.
