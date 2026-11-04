# Per-installation NVMe state payload

The current state image is an 8 GiB journaled ext4 per-installation fixture. It
has not been written to NVMe or booted. Its test MAC/CIDR and operator public
key are unsuitable for deployment; the corresponding temporary private key was
deleted. Supply the real board Ethernet identity and operator public key for an
installation. Never clone prepared state across hosts.

The recipe copies `/var` from authenticated userspace and seeds the state
contract, management configuration, machine-id, filesystem UUID, and
`boot/selected.conf`. Selection is bound to a full root payload using the
manifest-checked preparer; see [root selection](nvme-root-selection.md).

## Inputs and creation

`build/host-state.py ROOTFS_TAR CONFIG_JSON ADMIN_PUBLIC_KEY NEW_OUTPUT_DIR`
requires root, pinned builder tools, and mandatory `--boot-selection` naming a
prepared selector. Optional `--state-uuid` is for an explicitly selected
installation/recovery identity; ordinary creation generates UUID4. It performs
no network, mounts, package scripts, or physical-device writes. Output must be
a new absolute repository `out/host-state-*` directory.

```sh
sudo /usr/bin/python3.10 -I -B build/host-state.py \
  /home/drew/orange-pi-4-pro-build/recipe/out/host-userspace-bookworm-20261001-01/rootfs.tar.gz \
  /home/drew/orange-pi-4-pro-build/state-fixture-inputs-20261104/management.json \
  /home/drew/orange-pi-4-pro-build/state-fixture-inputs-20261104/admin.pub \
  "$(pwd -P)/out/host-state-package-state-20261104-01" \
  --boot-selection "$(pwd -P)/out/package-selection-A.conf"
```

Fixture management settings use DHCP, MAC `02:73:30:00:00:01`, hostname
`a733-state-fixture`, and client CIDR `192.0.2.0/24`. The public key fingerprint
is `SHA256:zJDXpqimTuhgMi8BhG83V7SeYsDurn/2Dmt9g/R3Fzc`. These values are fixture
data only; they do not themselves activate DHCP or SSH. Keep generated payloads
on known fixed builder storage or transfer by authenticated networking/USB,
never on SD or in RAM.

## Current evidence and limits

Builder output is
`/home/drew/orange-pi-4-pro-build/package-state-work-20261104/out/host-state-package-state-20261104-01`.
State UUID is `cbc5029f-e3e3-45b6-a4ed-5a5cad32f3db`; raw image size is
8,589,934,592 bytes and SHA-256 is
`786dfbae96ed85b8a6f4b59b3ac308d406d2994a0e9e9c7402790db9c4d96163`.
See [fixture manifest](evidence/package-state-manifest-20261104.json) and
[verification](evidence/package-state-verify-20261104.json). Readback confirmed
UUID/journal, management config, selector, identity, metadata, package database,
public-key fingerprint, empty SSH host-key directory, and compressed expansion.
This is regular-file readback, not target installation or boot.

Three expendable caches were pruned only from private staging (APT caches and
ldconfig aux-cache; 77,294,968 bytes total); shared `/var` package metadata
copies are retained but nonauthoritative. The root-owned package-state ABI is
`a733-package-state-v1` and must match root and bootstrap. State selector schema, identity and ownership checks are specified in
[the handoff document](host-handoff.md). On-device management activation, actual
operator-key login, installation-specific state/bootstrap pairing, installer,
inactive-slot writer,
activation/rollback, backup, health checks, KVM and all physical acceptance
remain open. [Native loop checks](evidence/package-state-loop-20261104.json) and
ARM ash fixture checks do not establish physical NVMe operation. Preserve SPI-to-NVMe preference and zero routine SD writes;
explicit kernel/DTB/initramfs maintenance may update boot files.
