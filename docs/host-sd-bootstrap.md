# SD bootstrap for the NVMe host

Historical generated paths below may have been archived. Consult the
[retention map](build-cache-retention.md#replay-and-prototype-output-retention)
before reusing them; use the [personalized deployment](deploy-personalized-20261106.md)
for the current installation.

The [explicit EL2 packaging profile](boot-profiles.md) prepares the locked
experimental handoff without changing the baseline artifacts below. Physical
EL2/KVM initialization is now observed; the current installation uses the
personalized deployment above. Guest execution and deployment acceptance remain open.


A complete 256 MiB host SD candidate has been assembled and inspected as an
ordinary file on the Ubuntu 22.04 builder. It combines the pinned diagnostic
vendor boot stack with the [NVMe host handoff](host-handoff.md). It has not
booted on the board. It is a fallback/bootstrap component, not an NVMe installer
or evidence of a working hypervisor. SPI-to-NVMe with SD removed remains the
preferred release mode.

The current candidate references fixture state UUID
`cbc5029f-e3e3-45b6-a4ed-5a5cad32f3db`. That state contains a deleted test
credential's public key and documentation-only network settings. It is not an
operator installation. Use the [diagnostic candidate](diagnostic-image.md) for
first board boot; supply real Ethernet identity and administration public key
before preparing installation state and its matching bootstrap.

## Artifact and boot behavior

Builder output is under
`/home/drew/orange-pi-4-pro-build/package-state-work-20261104/out/host-sd-package-state-20261104-01/`.
The [manifest](evidence/package-host-sd-20261104.json) records input, recipe and package
versions; [independent inspection](evidence/package-host-sd-verify-20261104.json) records
content checks.

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `a733-host-sd.img` | 268,435,456 | `a0eec1415503c7a69eb697218ef259df439f772e70c55cbae2774896c9561157` |
| `a733-host-sd.img.gz` | 17,271,977 | `aa64c35cb9f570e748350edacd6ad546e762a3d7ee2ac49b63a4b07c6034d8a7` |
| `uInitrd` | 4,762,444 | `a28b70548a18ff0fb8dfe66e76106aae162e3f6f81e65ab47f025507b9add693` |

The image fits below the 2 GB card budget. Actual card capacity, identity and
explicit target authorization are still required before flashing anything.
Its DOS partition layout, raw firmware offsets and legacy ext4 features match
the [diagnostic layout](diagnostic-image.md#byte-layout-and-write-policy).
The boot partition retains the historical `A733DIAG` label; identify artifacts
by manifest/hash, not that shared label.

The host boot command preserves the vendor load addresses and legacy ARM image
wrappers around the ARM64 payload. It loads kernel, DTB and wrapped host
initramfs from SD. Linux selects the configured NVMe state and its mutable root
selector, then starts the selected immutable root. Linux does not mount SD.
Missing/incompatible state stops in serial recovery; no automatic rollback or
NVMe provisioning happens. Firmware write behavior and recovery are untested.

```mermaid
flowchart LR
  SD[SD vendor boot0 and boot package] --> B[SD kernel, DTB and host initramfs]
  B --> S[NVMe state UUID and selected.conf]
  S --> R[NVMe immutable root A or B]
  R --> M[RAM configuration and NVMe persistent state]
```

## Recipe and validation

On the prepared Jammy builder, from the repository:

```sh
sudo /usr/bin/python3.10 -I -B build/assemble-host.py \
  /home/drew/orange-pi-4-pro-build/artifacts \
  /home/drew/orange-pi-4-pro-build/package-state-work-20261104/out/host-initramfs-package-state-20261104-01 \
  "$(pwd -P)/out/host-sd-package-state-20261104-01"
sudo /usr/bin/python3.10 -I -B tests/verify_host_sd.py \
  "$(pwd -P)/out/host-sd-package-state-20261104-01" \
  /home/drew/orange-pi-4-pro-build/package-state-work-20261104/out/host-initramfs-package-state-20261104-01
```

These commands passed. Choose a new output name to repeat. Root is needed to
read the current private input directories; the packager itself performs no
mounts, device operations or package installation. It refuses existing outputs,
symlink paths, special files, RAM staging and outputs outside a new repository
`out/host-sd-*` directory. Failed builds retain their output/log and require a
new name for retry. Keep images on known fixed builder storage or transfer by
authenticated networking/USB, never stage on SD or RAM.

The diagnostic kernel, DTB and firmware are captured and checked against the
committed diagnostic hashes. The caller supplies a trusted host build manifest;
its compressed initramfs hash, ABI, boot inputs and current init/resolver source
records are checked. This is a trust boundary: the supplied manifest is not a
release signature. Preserve the manifest and its provenance with the artifact.
Exact selected Jammy packaging package versions/architectures are enforced from
`configs/builder-packages.lock.tsv`. The overall clean-builder image, tool
binary integrity and firmware licensing audits remain open.

Independent inspection checked the full disk hash, MBR, raw boot-blob offsets,
ext4 features, pinned kernel/DTB, legacy script and initrd header/data CRCs,
script contents, compressed distribution expansion and all 424 newc entries.
It matched the actual state UUID and init/resolver bytes to the supplied
manifest/current source. Packaging and inspection did not use physical targets.
A [clean-checkout repeat](evidence/package-rebuild-20261104.json) from `f2428b9`
reproduced the host initramfs, raw SD and compressed SD byte-identically. It
reused authenticated cached inputs, not a clean immutable builder or hardware.

Default diagnostic assembly regressions also passed after adding the optional
explicit boot-command input to the shared assembler.

The current fixture contains no host private keys.

## Hardware handoff still required

The [diagnostic guide](diagnostic-image.md) provides exact target identification,
authorized flashing, 3.3 V serial wiring and 115200 8N1 capture instructions.
Do not flash this fixture bootstrap as an installed host. Once real NVMe state
is installed, build its matching bootstrap, then apply the same exact-target
approval and readback procedure using that artifact's own hashes.

Return full serial logs for boot and failure paths, selected NVMe identities and
mounts, management/key verification and the separate real KVM guest results.
The [acceptance record](hardware-validation.md) also requires repeated cold/warm
boots, workload, zero SD writes, rollback, backup/restore and reproduced
deployment. None is satisfied by this file build.

Vendor protocol sources and upstream kernel packaging references are linked in
the [diagnostic boot-chain explanation](diagnostic-image.md#inputs-and-boot-chain).
