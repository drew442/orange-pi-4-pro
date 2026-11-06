# Current managed host root components

Fresh immutable A/B roots now contain the current complete maintenance and guest
management tree, including optional isolated loopback networking. They preserve
the pinned A733 kernel, DTB and modules. **Neither root has booted on the board;
no KVM guest has executed.** They are components for a future matched installation,
not an update to the already published diagnostic image.

[Evidence](evidence/current-roots-20261104.json) records raw/compressed hashes,
source pins, filesystem inspection, package-state ARM checks and packaged tool
workflows. Each raw root is 4,294,967,296 bytes, read-only ext4 without a journal.
It belongs in its NVMe root slot; it is not an SD image.

| Download | Bytes | SHA-256 |
| --- | ---: | --- |
| `root-A.ext4.gz` | 143,415,593 | `c80955d80719d4bba239158356645db87be7afc0948b5542207c9c42b208e92c` |
| `root-B.ext4.gz` | 143,415,652 | `8a21d898de41a3b29d16bba313321d0d3b7afa662e85844fe940be64a385a89c` |

Builder input directory:
`/var/lib/a733-current-roots-work-20261104/out/host-payload-current-20261104-01/`.
Compressed artifacts and the complete root manifest are readable at
`/home/drew/orange-pi-4-pro-build/current-roots-20261104/`.
Keep each manifest with its exact files. Never substitute a component's hash from
an older root build or write a root component directly to the whole NVMe device.

## Verified build and inspection

On the pinned Jammy builder, from the protected checkout
`/var/lib/a733-current-roots-work-20261104`, the following ran successfully as root.
Choose a fresh output name for another build; existing outputs are refused.

```sh
python3 -I -B build/host-payload.py \
  /home/drew/orange-pi-4-pro-build/recipe/out/host-userspace-bookworm-20261001-01/rootfs.tar.gz \
  /home/drew/orange-pi-4-pro-build/artifacts \
  "$(pwd -P)/out/host-payload-current-20261104-01"
python3 -I -B tests/verify_managed_payload.py \
  "$(pwd -P)/out/host-payload-current-20261104-01"
python3 -I -B tests/verify_package_payload_arm.py \
  "$(pwd -P)/out/host-payload-current-20261104-01" \
  /home/drew/orange-pi-4-pro-build/busybox/bin/busybox
```

The build took 141.42 seconds and reported 68,076 KiB maximum child RSS. No kernel
compilation or package download was needed. Full raw hashes, gzip expansion,
read-only/no-journal features and every installed tool's contents, ownership and
mode pass readback. Both actual roots expose the matching eight immutable package
views; ARM queries return all 220 locked package rows. The 1,122-entry metadata
BOM, writable state logs and private mount/loop cleanup pass. These are native VFS
and ARM userspace checks, not an A733 system boot.

## Current guest fixture in packaged ARM workflows

The ARM enrollment/workflow inspector now has an explicit
`--fixture-profile fixed-seed-http` option. It selects only the repository's
locked [fixed-seed fixture record](evidence/guest-seed-fixture-20261104.json),
checks the shared kernel, seed UUID/label/identity and exact 128 MiB disk, then
captures all three inputs through the existing descriptor/hash/identity checks.
The default remains `legacy`; arbitrary caller manifests and path-based guessing
are not accepted. Wrong/missing labels and malformed/oversized records are refused.

The fresh portable tools archive and manifest match the earlier current network
package byte for byte. At `/usr/lib/a733-tools`, the actual ARM workflow passed:

```sh
python3 -I -B tests/verify_guest_tools_arm.py \
  "$(pwd -P)/out/maintenance-tools-current-20261104-01" \
  --package-manifest-sha256 60731890107e504dfce9e9d7297032daac46b320b7cee528e99d40fcd9fae5e1 \
  --loopback-network --fixture-profile fixed-seed-http \
  /home/drew/orange-pi-4-pro-build/recipe/out/host-userspace-bookworm-20261001-01/rootfs.tar.gz \
  /var/lib/a733-el2-checks-20261104/out/guest-fixture-seed-20261104-01
```

This exercises sysusers, enrollment, checkpoints, backup, local/HTTPS restoration,
file readback and unit parsing with explicit live-policy fixture substitutions.
Loopback network configuration survives those operations; the launcher command
is checked in dry-run only. No guest or service starts. All original legacy
artifacts still pass capture; the new HTTP initramfs is refused under that profile.

A new operator-specific state image and full NVMe image remain unbuilt. Do not
pair these roots blindly with earlier fixture state/selectors or full-disk images.
Use the real administration key, management settings and measured sector geometry
to create fresh matched state/bootstrap/NVMe artifacts after the
[EL2 diagnostic result](el2-diagnostic.md). Physical KVM, installation, SD policy,
repeated boots, workload, update/rollback/recovery and deployment reproduction
remain mandatory [release gates](hardware-validation.md).

## Personalized management refresh

[New preparation evidence](evidence/operator-build-20261105.json) supersedes the
historical root artifacts above for the real installation. Fresh A/B roots at
`/var/lib/a733-operator-work-20261105/out/host-payload-operator-20261105-01`
include the multi-key management validator and installer support. Independent
root contents, modes, full raw hashes and compressed expansion pass. Actual ARM
[management-policy checks](evidence/operator-management-arm-20261105.json) pass;
installed-host boot and SSH login remain untested. The separately built
[personalized state](host-state.md#personalized-preparation) holds both real keys;
keys are not embedded into the immutable roots.

## Installation-ready candidate

The [final personalized candidate](evidence/personalized-nvme-20261106.json)
supersedes the earlier preparation above. Source `18affa1` includes explicit
discard policy and trusted serial SSH fingerprint output. Root/state/bootstrap
bindings and the complete full-disk GPT/filesystems/selection pass independent
software inspection. The encoded bundle and authenticated LAN delivery also
pass; no physical installation or guest execution is claimed. Use the
[complete installation procedure](deploy-personalized-20261106.md), then the
[first physical KVM guest procedure](kvm-first-guest.md). The guest fixture is
staged and hash-verified on codex for that later step.
