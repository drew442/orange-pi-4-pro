# Matched NVMe-first firmware trial SD

This **unbooted** 256 MiB image prepares the NVMe-first U-Boot trial with the
current protected host boot files. Two constructions produce identical raw and
gzip bytes; independent complete artifact review passes. No physical trial or
SPI write has occurred. Leave the active endurance workload undisturbed;
no operator flashing is currently requested.

The older [November 7 transports](nvme-firmware-test-sd.md) select obsolete STATE
identities. This derivative instead preserves STATE UUID
`360dfb39-f49f-487e-b307-0bb887c854c5`, the working VGIC DTB, kernel, protected
initramfs and SD fallback script. It replaces only the registered NVMe-first
U-Boot package. The compatible **SD boot0 stays unchanged**; SPI boot0 is never
copied into this SD image.

## Artifacts and preservation

The retained codex directory is
`out/matched-nvme-trial-20261109-01/retained/` under this repository. It contains
`a733-host-sd.img.gz` and both original manifests. Files are private, read-only
transport copies; raw outputs remain on the builder.

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| Raw SD | 268435456 | `8470ff59290016bb2f99c051f371ab20ac431e75fd17dec4e0e0de5c665613d9` |
| Gzip SD | 17273151 | `5abb6ac92112a3b87a17328eab2f193aa49833effc6b4aa44557fe7271c2a0c8` |

The raw image fits the actual **1,977,614,336-byte** SU02G card. This file is a
whole SD image, not a whole-NVMe image, installer or SPI programming payload.
The normal [current host/recovery images](current-images.md) remain separate.

The producer authenticates the complete protected SD raw (`ab5e…`) and complete
backup-derived SPI candidate (`83ed…`). Only its package slice
`[262144,1654784)` enters SD `[16793600,18186240)`, retaining the registered
1,392,640-byte package hash `bdfe070a2c358de9dd0061b39609f604c0c12e9c25140903505f88799cc343be`.
All **267,042,816 bytes outside that range** compare directly with the base.
The complete 234,881,024-byte BOOT partition remains unchanged, SHA-256
`dca66b00a1287f860759685e2aeeb011f935a57a628f02449a71456542009003`.
MBR geometry, SD boot0 and all gaps remain unchanged. No filesystem is rewritten.

## Reproduce regular-file construction

The actual builder checkout is
`/var/lib/a733-matched-nvme-trial-20261109-01`, source baseline `c8a65e1` plus the
new producer/tests. Each output below already exists; choose fresh names for
another run. From its trusted root-owned checkout on Ubuntu 22.04:

```sh
python3 -I -B build/matched-nvme-trial-sd.py \
  --base-sd /var/lib/a733-maintenance-validation-20261108-01/project/out/protected-state-bootstrap-20261109-01/a733-host-sd.img \
  --spi-candidate /var/lib/a733-matched-nvme-trial-inputs-20261109-01/a733-spi-nvme.bin \
  --output /var/lib/a733-matched-nvme-trial-20261109-01/out/matched-nvme-trial-sd-FRESH
```

The original runs used `matched-nvme-trial-sd-01` and `-02`, with protected base
builds `-01` and `-02` respectively. Sources must be absolute regular files with
safe nonsymlink ancestry and no hardlinks, in a trusted, quiescent root/Drew
environment. There are no input-pin overrides, mounts, subprocesses or device
routes. Outputs must be fresh `out/matched-nvme-trial-sd-TAG` directories.

Admission requires **3,000,000,000 free bytes plus 1 GiB scratch**, and
**1,000,000,000 available memory bytes**. Runtime samples enforce those floors
around major phases. The two builds retain more than 22.63 GB free disk and
7.52 GB available memory at recorded phases; these are sampled values, not
continuous minima. Full-NVMe admission is unchanged. Raw/gzip files and the final
manifest/publication directories are synced. Require successful job completion;
publication-error cleanup removes the manifest where cleanup succeeds and otherwise
leaves uncertain private partial output. Gzip CRC, complete expansion/hash and outside-range
preservation are checked before publication.

These are two constructions on the same qualified builder, not two independent
clean builder environments. [Evidence](evidence/matched-nvme-trial-sd-20261109.json)
records exact sources, manifests, independent review, the fresh-checkout fixture
correction and successful 2,125-test privileged Jammy validation.

## Physical trial still required

After retaining the workload outcome, prepare and independently review the exact
SD maintenance write and boot procedure for this new raw hash. The existing
protected-SD writer pins `ab5e…` and cannot write this trial; do not bypass its
pin or relabel this image as that protected baseline. Keep the verified protected
SD and private RAM recovery image available for restoration. Known-board storage
authorization persists; another permission request is not needed for that scope.

Capture serial U-Boot version/environment and the read-only NVMe scan/file-load
checks in the [firmware investigation](spi-boot-investigation.md#resolved-source-policy-and-remaining-candidate-work).
Then separately demonstrate default NVMe-file boot and manual SD fallback,
checking management, exact root/STATE, EL2/KVM and SD writes on each path.
Source behavior alone proves none of those results. The matched NVMe BOOT is
already installed, but firmware loading it remains unperformed.

An SD firmware trial does not establish independent ROM recovery, SPI programming,
SD-removed boot, rollback or complete release acceptance. Those remain explicit
[hardware gates](hardware-validation.md); a real KVM guest is still required.
