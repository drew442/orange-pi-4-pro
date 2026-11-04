# NVMe root/state handoff prototype

The current generic host initramfs is 4,761,902 bytes, SHA-256
`00b8cbe9ae85d3abe7b772981b5b685c4326a924abc13f336ce3f2cb577bcc84`. Its
immutable `/etc/a733-boot.conf` identifies state UUID
`bfe5ba29-3175-47a3-a410-04b87e3de1a6` and kernel
`6.6.98-a733-diag`; root A/B are not embedded. Selection comes from
`boot/selected.conf` on NVMe state. No physical boot, NVMe installation, KVM
guest or SD-write acceptance has been demonstrated.

## Boot behavior and state contract

`configs/host-init` reads bounded configuration as data, not shell syntax. It
resolves the requested state UUID, mounts state read-only with `noload`, and
validates marker, layout, ownership and machine-id before writing. It then
unmounts and rechecks identity, UUID and GPT, remounts read-write for journal
recovery, revalidates, and reads the selector. Configuration is limited to
2048 bytes and selector to 4096 bytes; duplicate, unknown or malformed fields
fail. No automatic fallback, attempt counter or boot-time selector write exists.

The [resolver contract](host-storage-boot.md) scans only eligible NVMe p4 state
partitions. Duplicate UUIDs and unrecognized eligible p4 partitions fail
closed. GPT roles are p1 boot, p2 root A, p3 root B, p4 state and p5 guests.
Selection validates the selected namespace/GPT and same-parent root/state
roles, without probing the unselected root filesystem.

The selected ext4 root mounts read-only and its marker and init paths are
checked. Runtime marker checks do not authenticate every filesystem byte; the
root-selection preparer authenticates the full pinned image, while installation
must verify written payloads separately. State marker is a root-owned regular
`.a733-state` containing exactly `a733-state-v1\nSTATE_UUID\n`. Required
root-owned directories are `var`, `root`, `home`, and `identity`, with no
group/world writes or special mode bits. `identity/machine-id` must be 32
lowercase hex digits plus newline, excluding the all-zero value,, root-owned and mode 0400, 0444, 0600
or 0644.

State supplies `/var`, `/root`, `/home` and `/etc/machine-id`. Existing `/run`
is 64 MiB tmpfs; nested state is moved into the new root before bindings.
`/etc` uses an ephemeral overlay and `/tmp` is 64 MiB tmpfs. These memory
budgets need board workload validation. Management preparation runs after
proc/sys/dev moves and before final exec. The helper requires root Python with
`-I -B`, verifies read-only root and read-write NVMe state roles, UUID,
identity, binds and RAM-backed `/etc`, then writes only RAM `/etc` config/unit
links and masks native SSH. Failure cleanup is serial. Network policy is DHCPv4
on the exact physical Ethernet MAC, IPv6 RA/link-local off, public-key-only root
SSH from one configured IPv4 client CIDR, supervised renewal, and 128 MiB
persistent plus 16 MiB volatile journals. Hardware memory/network behavior is
unverified. See the [management guide](host-management.md).

## Prepared artifacts

Root choice requires an explicit trusted manifest; there is no hardcoded
manifest default:

```sh
sudo python3 build/root-selection.py \
  /home/drew/orange-pi-4-pro-build/management-work-20261104/out/host-payload-20261104-02/root-A.ext4 \
  "$(pwd -P)/out/managed-selection-A.conf" --slot A \
  --payload-manifest /home/drew/orange-pi-4-pro-build/management-work-20261104/out/host-payload-20261104-02/manifest.json
```

The preparer authenticates the complete regular image against the supplied
manifest, checks slot marker and compatible boot stack, and creates only a new
regular selector output. State creation then consumes this selector; see
[host state](host-state.md). Generic initramfs creation takes no root image or
slot:

```sh
sudo python3 build/host-initramfs.py \
  /home/drew/orange-pi-4-pro-build/recipe/out/host-userspace-bookworm-20261001-01/rootfs.tar.gz \
  /home/drew/orange-pi-4-pro-build/artifacts \
  /home/drew/orange-pi-4-pro-build/busybox/bin/busybox \
  bfe5ba29-3175-47a3-a410-04b87e3de1a6 \
  "$(pwd -P)/out/host-initramfs-20261104-05"
```

Builder outputs are under
`/home/drew/orange-pi-4-pro-build/management-work-20261104/out/`:
`host-payload-20261104-02`, `host-state-20261104-04`, and
`host-initramfs-20261104-05`. The state fixture contains test credentials and
is not deployable. Evidence: [initramfs](evidence/managed-initramfs-20261104.json),
[archive inspection](evidence/managed-initramfs-archive-20261104.json),
[ARM ash](evidence/managed-handoff-ash-20261104.json),
[management ARM checks](evidence/management-arm-20261104.json), and state and
payload records linked from their guides. ARM chroot tests do not establish
systemd boot, a listener, SSH connection or physical operation. Configuration-file preparation mocked the mount-provenance guard only inside
a disposable chroot. Real ARM key generation exercised directory metadata
checks and atomic publication; no installed-NVMe activation was performed.

The `atomic_store` helper requires an authorized, quiesced directory. It is a
publication primitive, not an updater or automatic rollback controller.
Actual key login, SSH discovery, installer, inactive-slot writer,
activation/rollback, backup, KVM and physical recovery remain open. Preserve
SPI-to-NVMe preference and zero routine SD writes.
