# Per-installation NVMe state payload

The builder created an **8 GiB journaled ext4 state fixture**, compressed to
39,828,313 bytes. It passes independent hash, filesystem and selected-content
checks. It has not been written to NVMe or booted. The fixture uses documentation
network values and a public key whose temporary private key was deleted; it is
unsuitable for deployment. Actual administration credentials and board Ethernet
identity remain operator inputs.

The recipe creates a fresh filesystem UUID and machine-id for every installation,
copies `/var` from the authenticated userspace, and seeds the
[handoff state contract](host-handoff.md). `/root/.ssh/authorized_keys` contains
only the required operator public key. `identity/ssh` is empty: host private keys
must be generated on the installed board and retained on NVMe. Never clone a
prepared state image across hosts. Restoring a backup to the same installation
is a separate recovery operation.

## Inputs and creation

`build/host-state.py ROOTFS_TAR CONFIG_JSON ADMIN_PUBLIC_KEY NEW_OUTPUT_DIR`
requires root and the pinned builder tools. Optional `--state-uuid` is reserved
for an explicitly selected installation/recovery identity; normal creation
generates a UUID4. No network, mounts, package scripts or physical device writes
occur. The output must be a new absolute `repo/out/host-state-NAME` directory.
The recipe holds and authenticates the userspace descriptor, reauthenticates a
private snapshot, validates its archive namespace, and preserves `/var` ownership,
permissions and xattrs through GNU extraction. It refuses existing/symlink/device
inputs or outputs and reserves space for raw/compressed files, extraction and
margin before building.

The management JSON has exactly these fields; shown values are the tested fixture:

```json
{
  "schema": 1,
  "hostname": "a733-state-fixture",
  "management_mac": "02:73:30:00:00:01",
  "management_cidr": "192.0.2.0/24",
  "dhcp": true
}
```

Hostname is one lowercase DNS label. MAC must be canonical nonzero unicast and
must ultimately identify the chosen physical wired interface. The CIDR is the
allowed IPv4 **SSH client source** network, not a required subnet for the host's
DHCP address. Only DHCPv4 configuration is represented initially. The key file
must contain one plain Ed25519 public key without authorization options; an
optional comment is discarded. Other key algorithms and static networking need
explicit future support.

These fields are persistent data under `config/management.json`; they do not
currently enable DHCP or SSH. Runtime loading, SSH host-key generation, service
activation and management reachability remain implementation and hardware gates.

The actual builder command passed from its prepared checkout:

```sh
sudo python3 build/host-state.py \
  /home/drew/orange-pi-4-pro-build/recipe/out/host-userspace-bookworm-20261001-01/rootfs.tar.gz \
  /home/drew/orange-pi-4-pro-build/state-fixture-inputs-20261104/management.json \
  /home/drew/orange-pi-4-pro-build/state-fixture-inputs-20261104/admin.pub \
  "$(pwd -P)/out/host-state-20261104-01"
sudo python3 tests/verify_host_state_image.py \
  out/host-state-20261104-01/state.ext4 \
  out/host-state-20261104-01/manifest.json
```

Use a new output name for another run. The input fixture files exist only on the
builder. Deployment requires actual operator inputs and exact-target approval;
these commands do not install an image. Keep payloads on known fixed builder
storage or transfer them over authenticated networking/USB. Never stage them on
SD or in RAM. The backing-storage guard rejects MMC, loop, RAM and removable
storage, including mapped backing devices; Linux `sdX/removable=0` alone cannot
identify every USB SD reader, so known builder storage remains an assumption.

## Output and evidence

Builder output:
`/home/drew/orange-pi-4-pro-build/host-handoff-clean-20261104/out/host-state-20261104-01/`.

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `state.ext4` | 8,589,934,592 | `a98cb0ab820b0ef6b9d1c03012a5086df5e4d355538eba3a40088bad5c799c04` |
| `state.ext4.gz` | 39,828,313 | `cd6d4bf9b0bccbcecaaefc189e8682e83b609ff2d87ada42c72f5f6d0b922572` |

The [fixture build report](evidence/host-state-fixture-20261104.json) wraps the
original manifest with its nondeployment context. `staging-contents.json` records
the source tree before `mke2fs`; it is not an exact filesystem BOM. The filesystem
root is 0755 and generated `lost+found` is 0700, both root-owned and checked after
creation. The manifest records this distinction, source/package/tool pins and
fresh-identity policy. Gzip uses a fixed header and streaming I/O; whole images
intentionally differ between installations because of identities and filesystem
creation metadata. No full byte-reproducibility claim is made.

[Independent readback](evidence/host-state-verify-20261104.json) passed for the
actual 8 GiB file: full hash, journal/UUID, selected ownership/modes, exact marker
and machine-id, management JSON, operator-key fingerprint, empty host-key
directory and package database versus the hash-checked staging report. The raw
file stayed unchanged; compressed hash and bounded-memory expansion matched it.
The verifier uses read-only `debugfs` through a pinned regular-file descriptor
and trusts the supplied build manifest; it is not signature verification.

All 14 recipe regressions passed on the pinned Jammy builder, including ownership/
xattr preservation and a disposable 32 MiB journaled filesystem. Independent Sol
high review cleared staging-metadata and device-guard coverage findings. Latest
builder checks showed about 33 GiB free disk and 6.9 GiB available RAM. Actual
handoff, dirty-journal recovery, NVMe installation, SSH, KVM and SD-write acceptance
remain pending.
