# NVMe root/state handoff prototype

Historical generated paths below may have been archived. Consult the
[retention map](build-cache-retention.md#replay-and-prototype-output-retention)
before reusing them; use the [personalized deployment](deploy-personalized-20261106.md)
for the current installation.

The current generic host initramfs is 4,762,380 bytes, SHA-256
`e310593ef5a5cfa433be7a7b46b86231c134a3008694bfb86f8a6e14b920c0ef`. Its
immutable `/etc/a733-boot.conf` identifies state UUID
`cbc5029f-e3e3-45b6-a4ed-5a5cad32f3db` and kernel
`6.6.98-a733-diag`; root A/B are not embedded. Selection comes from
`boot/selected.conf` on NVMe state. No physical boot, NVMe installation, KVM
guest or SD-write acceptance has been demonstrated.

## Boot behavior and state contract

`configs/host-init` reads bounded configuration as data, not shell syntax. It
resolves the requested state UUID, mounts state read-only with `noload`, and
validates marker, layout, ownership and machine-id before writing. It then
unmounts and rechecks identity, UUID and GPT, remounts read-write for journal
recovery, revalidates, and reads the selector. State remains read-write for
persistent state; only the eight package metadata binds are read-only. Configuration is limited to
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
`/etc` uses an ephemeral overlay and `/tmp` is 64 MiB tmpfs. State remains
read-write for journal recovery and persistent state. Eight package metadata
views from the immutable root are separately bound read-only after the state
`/var` bind; the metadata ABI is `a733-package-state-v1`, required by the matching
root/bootstrap/selector set. These memory
budgets need board workload validation. Management preparation runs after
proc/sys/dev moves and before final exec. The helper requires root Python with
`-I -B`, verifies read-only root and read-write NVMe state roles, UUID,
identity, binds and RAM-backed `/etc`, then writes only RAM `/etc` config/unit
links and masks native SSH. Failure cleanup is serial. Network policy is DHCPv4
on the exact physical Ethernet MAC, IPv6 RA/link-local off, public-key-only root
SSH from one configured IPv4 client CIDR, supervised renewal, and 128 MiB
persistent plus 16 MiB volatile journals. Hardware memory/network behavior is
unverified. See the [management guide](host-management.md).

A [complete 256 MiB SD bootstrap](host-sd-bootstrap.md) now packages this handoff
with the pinned vendor boot stack. It references fixture state and remains
uninstalled and unbooted.

## Prepared artifacts

Root choice requires an explicit trusted manifest; there is no hardcoded
manifest default:

```sh
sudo /usr/bin/python3.10 -I -B build/root-selection.py \
  /home/drew/orange-pi-4-pro-build/package-state-work-20261104/out/host-payload-package-state-20261104-01/root-A.ext4 \
  "$(pwd -P)/out/package-selection-A.conf" --slot A \
  --payload-manifest /home/drew/orange-pi-4-pro-build/package-state-work-20261104/out/host-payload-package-state-20261104-01/manifest.json
```

The preparer authenticates the complete regular image against the supplied
manifest, checks slot marker and compatible boot stack, and creates only a new
regular selector output. State creation then consumes this selector; see
[host state](host-state.md). Generic initramfs creation takes no root image or
slot:

```sh
sudo /usr/bin/python3.10 -I -B build/host-initramfs.py \
  /home/drew/orange-pi-4-pro-build/recipe/out/host-userspace-bookworm-20261001-01/rootfs.tar.gz \
  /home/drew/orange-pi-4-pro-build/artifacts \
  /home/drew/orange-pi-4-pro-build/busybox/bin/busybox \
  cbc5029f-e3e3-45b6-a4ed-5a5cad32f3db \
  "$(pwd -P)/out/host-initramfs-package-state-20261104-01"
```

Builder outputs are under
`/home/drew/orange-pi-4-pro-build/package-state-work-20261104/out/`:
`host-payload-package-state-20261104-01`, `host-state-package-state-20261104-01`, and
`host-initramfs-package-state-20261104-01`. The state fixture contains test credentials and
is not deployable. Evidence: [initramfs](evidence/package-host-initramfs-20261104.json),
[ARM ash](evidence/package-host-init-ash-20261104.json),
[package ARM checks](evidence/package-payload-arm-20261104.json),
[management ARM checks](evidence/package-management-arm-20261104.json),
[state VFS checks](evidence/package-state-loop-20261104.json), and payload records
linked from their guides. ARM chroot and fixture tests do not establish
systemd boot, a listener, SSH connection or physical operation. Actual native
loop/VFS mounts were tested on disposable regular-file fixtures; no board or
physical NVMe mounts were tested. Configuration-file preparation mocked the mount-provenance guard only inside
a disposable chroot. Real ARM key generation exercised directory metadata
checks and atomic publication; no installed-NVMe activation was performed.

A [clean-checkout repeat](evidence/package-rebuild-20261104.json) from `f2428b9`
reproduced the generic bootstrap byte-identically with the same fixture state
UUID and authenticated cached inputs; it does not reproduce physical deployment.

The `atomic_store` helper requires an authorized, quiesced directory. It is a
publication primitive, not an updater or automatic rollback controller.
Actual key login, SSH discovery, installer, inactive-slot writer,
activation/rollback, backup, KVM and physical recovery remain open. Preserve
SPI-to-NVMe preference and zero routine SD writes.
