# Per-installation NVMe state payload

Historical generated paths below may have been archived. Consult the
[retention map](build-cache-retention.md#replay-and-prototype-output-retention)
before reusing them; use the [personalized deployment](deploy-personalized-20261106.md)
for the current installation.

The historical state image below is an 8 GiB journaled ext4 per-installation
fixture. It has not been written to NVMe or booted. Its test MAC/CIDR and operator public
key are unsuitable for deployment; the corresponding temporary private key was
deleted. The [real operator inputs](evidence/operator-inputs-20261105.json) now supply
the Ethernet identity, two keys and any-source SSH policy. The personalized
deployment supplies the current installation artifacts. Never clone prepared
state across hosts.

The recipe copies `/var` from authenticated userspace and seeds the state
contract, management configuration, machine-id, filesystem UUID, and
`boot/selected.conf`. Selection is bound to a full root payload using the
manifest-checked preparer; see [root selection](nvme-root-selection.md).

The optional `--userspace-profile normalized-20261106` selects the authenticated
normalized archive; omission preserves the baseline. See [profile selection and
actual staging evidence](userspace-profiles.md). This does not replace the
published installation artifacts.

The internal `seed_state(..., timesync=True)` API now supports guarded creation
of an empty clock directory owned by 996:996 with mode 0755; default seeding does not load that helper.
[Private initializer and ext4 fixture checks](timesync-state.md) pass independent
review. This option is not exposed by the production CLI or current profiles.
Time-profile selection, helper provenance and installed boot/update checks remain
required before a production time-enabled state image can be built.

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

## Multiple administration keys

`ADMIN_PUBLIC_KEY` may contain up to 16 distinct plain Ed25519/RSA keys within
16,384 bytes, one per line. Options, blank lines, duplicate keys, malformed wire
fields and RSA keys below 2048 bits are rejected. Optional comments are stripped
from canonical `authorized_keys`; order is preserved. State manifests record
`admin_public_key_fingerprints` for the whole bundle. A one-key state also keeps
the legacy singular fingerprint field; readers reject contradictory metadata.

The operator supplied RSA3072 and Ed25519 keys; both are required for the real
installation. SSH host identity remains a separately generated single Ed25519
key, created on NVMe at first managed boot. No host private key is embedded in
this state input. Management CIDR `0.0.0.0/0` intentionally allows any IPv4 source;
password authentication remains disabled.

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

## Personalized preparation

[Current build evidence](evidence/operator-build-20261105.json) records actual
RSA3072/Ed25519 fingerprints, DHCP, MAC `10:f4:ae:90:5e:57` and any-source IPv4
access. Fresh A/B roots and an 8 GiB state image were built on Jammy. Independent
state readback confirms both canonical keys, private file/directory modes,
management configuration, selected A root, unique machine/filesystem identity,
empty host-key directory and matching gzip expansion. The NVMe assembler's
actual state-identity and installation-intent checks also pass.

Protected builder outputs are under
`/var/lib/a733-operator-work-20261105/out/`, with names
`host-payload-operator-20261105-01` and `host-state-operator-20261105-01`.
This state UUID is `64504c34-85b4-4035-9618-d86b298f8349`; it is specific to this
installation. These are component images, not whole-disk installation images.
Do not flash them individually onto the namespace. Full-disk assembly, delivery,
physical identity revalidation and actual SSH with both keys remain pending.
