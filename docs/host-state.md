# Per-installation NVMe state payload

The current fixture is an 8 GiB journaled ext4 image, 39,828,609 bytes when
compressed. It has not been written to NVMe or booted. Its test MAC/CIDR and
public key whose temporary private key was deleted make it unsuitable for
deployment. Actual administration credentials, board Ethernet identity and
operator SSH key remain inputs.

The recipe copies `/var` from authenticated userspace and seeds the handoff
state contract, management configuration, unique machine-id and filesystem
UUID. Never clone a prepared state image across hosts. State includes the
schema-1 `boot/selected.conf` selector. The selector is prepared independently
from a full pinned root image; see [root selection](nvme-root-selection.md).

## Inputs and creation

`build/host-state.py ROOTFS_TAR CONFIG_JSON ADMIN_PUBLIC_KEY NEW_OUTPUT_DIR`
requires root, pinned builder tools and mandatory `--boot-selection` pointing
to the prepared selector. Optional `--state-uuid` is for an explicitly selected
installation/recovery identity; normal creation generates UUID4. No network,
mounts, package scripts or physical device writes occur. Output must be a new
absolute `repo/out/host-state-NAME` directory.

```sh
sudo python3 build/host-state.py \
  /home/drew/orange-pi-4-pro-build/recipe/out/host-userspace-bookworm-20261001-01/rootfs.tar.gz \
  /home/drew/orange-pi-4-pro-build/state-fixture-inputs-20261104/management.json \
  /home/drew/orange-pi-4-pro-build/state-fixture-inputs-20261104/admin.pub \
  "$(pwd -P)/out/host-state-20261104-03" \
  --boot-selection "$(pwd -P)/out/root-selection-A.conf"
```

The management JSON has exactly these fields (fixture values):

```json
{
  "schema": 1,
  "hostname": "a733-state-fixture",
  "management_mac": "02:73:30:00:00:01",
  "management_cidr": "192.0.2.0/24",
  "dhcp": true
}
```

Use new output names when repeating these verified builder commands. The CIDR
is the permitted IPv4 SSH client source network. MAC is explicit canonical
unicast; only plain Ed25519 operator public keys are currently accepted.
These persist as data and do not enable DHCP or SSH. Runtime loading, service
activation, host-key generation and management reachability remain open. Keep
payloads on known fixed builder storage or transfer via authenticated
networking/USB; never stage them on SD or in RAM.

## Current evidence and limits

Builder output is
`/home/drew/orange-pi-4-pro-build/host-state-clean-20261104/out/host-state-20261104-03`.
State UUID is `dfad484b-9958-4ce4-b92c-725734f515fd`. The raw image is
8,589,934,592 bytes, SHA-256
`faa22756adb5275cabdc27be88b813d533f41175daf3ee1d905daa3884535421`; gzip is
39,828,609 bytes, SHA-256
`6018fd1d25c06a6f6329dea8fdc5183d19b470931b5ab8d6220e0275ba0cd2a0`.
The current initramfs is in
`/home/drew/orange-pi-4-pro-build/host-state-clean-20261104/out/host-initramfs-20261104-03`.

[State verification](evidence/nvme-selection-state-20261104.json) confirms
filesystem UUID/journal, selected ownership and modes, marker, machine-id,
management JSON, operator-key fingerprint, empty host-key directory and
package database. It is regular-file readback, not signature verification or
target installation. The selector record is validated against root A and the
compatible `6.6.98-a733-diag` kernel. No fresh rebuild of the new selector
format is claimed here.

Runtime management activation, actual SSH key, complete SD host image,
installer/updater/target activation, health checks, KVM, NVMe boot and all
physical acceptance remain pending. Preserve SPI-to-NVMe preference and the
zero routine SD-write rule; explicit kernel/DTB/initramfs maintenance may
update boot files.
