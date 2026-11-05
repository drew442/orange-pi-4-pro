# Explicit boot profiles for prepared host and installer images

The original board boot reached Linux at EL1 and could not initialize KVM.
The [separate EL2 diagnostic](el2-diagnostic.md) has physically entered EL2 on all eight CPUs with KVM VHE initialization; actual guest execution remains pending.
Host/installer/NVMe packaging now accepts an **explicit** locked profile so
preparation can retain that handoff with the same pinned components. Selecting a profile
never establishes hardware acceptance or authorizes a storage write.

| Profile | Monitor service | Expected SPSR | Status |
| --- | --- | --- | --- |
| `baseline-el1` (default) | `0x8000ff04` | `0x3c5` | Original diagnostic booted; KVM unavailable |
| `experimental-el2` | `0x8000ff40` | `0x3c9` | Physical EL2/VHE initialization observed; guest execution pending |

The selector reads fixed repository evidence paths, not caller-supplied pin
manifests. The experimental profile substitutes only the exact locked
1,392,640-byte boot package. Boot0, Image/uImage, board DTB and kernel configuration
must match the original diagnostic. Initramfs/root builders retain their existing
kernel/module pins because those inputs are unchanged. Profile metadata, target
service, expected SPSR, pending acceptance and evidence hashes accompany outputs.
A change in file bytes cannot be accepted by relabeling an artifact.

## Actual Jammy preparation

The protected builder checkout is
`/var/lib/a733-boot-profile-work-20261104`. In a root shell on the builder, from
that directory, the following packaging and independent inspection commands
passed. Existing output names are refused; use fresh names for another run.
These commands create regular files only, not physical-device writes.

```sh
python3 -I -B build/assemble-host.py \
  /var/lib/a733-el2-work-20261104/out/el2-diagnostic-20261104-03/artifacts \
  /home/drew/orange-pi-4-pro-build/package-state-work-20261104/out/host-initramfs-package-state-20261104-01 \
  "$(pwd -P)/out/host-sd-el2-20261104-01" --boot-profile experimental-el2
python3 -I -B tests/verify_host_sd.py \
  "$(pwd -P)/out/host-sd-el2-20261104-01" \
  /home/drew/orange-pi-4-pro-build/package-state-work-20261104/out/host-initramfs-package-state-20261104-01 \
  --boot-profile experimental-el2
python3 -I -B build/installer-initramfs.py \
  /var/lib/a733-maintenance-work-20261104/out/installer-runtime-maintenance-20261104-01 \
  /var/lib/a733-el2-work-20261104/out/el2-diagnostic-20261104-03/artifacts \
  /home/drew/orange-pi-4-pro-build/busybox/bin/busybox \
  "$(pwd -P)/out/installer-initramfs-el2-20261104-01"
python3 -I -B build/assemble-installer.py \
  /var/lib/a733-el2-work-20261104/out/el2-diagnostic-20261104-03/artifacts \
  "$(pwd -P)/out/installer-initramfs-el2-20261104-01" \
  "$(pwd -P)/out/installer-sd-el2-20261104-01" --boot-profile experimental-el2
python3 -I -B tests/verify_installer_sd.py \
  "$(pwd -P)/out/installer-sd-el2-20261104-01" \
  "$(pwd -P)/out/installer-initramfs-el2-20261104-01" --boot-profile experimental-el2
```

The installer initramfs was regenerated: its source-provenance checks include
`assemble-host.py`, so an older archive cannot silently stand in after this
change. The independent inspectors check raw firmware offsets, full image hashes,
MBR, legacy ext4 features, kernel/DTB, boot script and initrd wrappers/CRCs,
authenticated initramfs contents and full compressed expansion.

[Evidence](evidence/el2-boot-profiles-20261104.json) records full artifact pins.
A fresh protected checkout of commit `cae284a` rebuilt the host raw/gzip/initrd,
installer initramfs and installer raw/gzip/initrd byte for byte. Both independent
SD inspections passed again. This repeats packaging with authenticated cached
inputs; it is not a clean-builder acquisition or physical deployment reproduction.

Both raw SD images are 268,435,456 bytes and fit the actual 1,977,614,336-byte card.
**The host bootstrap is a fixture**, referencing state UUID
`cbc5029f-e3e3-45b6-a4ed-5a5cad32f3db` with test management inputs. Neither image
has booted on the board. Keep using the diagnostic test instructions until EL2
is observed; do not substitute these preparation images for a real installation.

## NVMe pairing and compatibility

`build/nvme-image.py` also accepts `--boot-profile experimental-el2`. It requires
the supplied SD manifest's complete profile/evidence metadata and all locked boot
blob records to match that explicit choice before creating output or staging
payloads. A missing profile is allowed only for a historical baseline manifest;
explicit null/malformed values are refused. Installation intent and physical
writer authorization/locking/readback rules are unchanged.

The default verifiers still accept actual historical baseline host/installer
images with absent profile metadata, provided all boot pins and baseline evidence
provenance match. An EL2 image cannot pass the default baseline verification.
The explicit flag is required for both construction and inspection of EL2 images.

A new complete NVMe image with this profile has not been assembled. It awaits
real administrator/network configuration and measured logical/physical sector
geometry, then adequate staging space and complete image/readback validation.
Published 250,000,000,000-byte disk fixtures remain nondeployable. Profile tests
and file inspection do not establish board networking, NVMe installation, SPI
boot, zero routine SD writes or actual KVM guest execution.
