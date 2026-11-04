# Implementation status

Updated 2026-11-04. Replace stale entries; keep this file short.

## Current milestone

Build a headless diagnostic SD candidate, then collect live A733 inventory and
prove NVMe and KVM operation. Vendor images exceed the actual 2 GB SD capacity;
neither a larger card nor live inventory is a prerequisite to the first image.

## Decisions and evidence

- Target: Orange Pi 4 Pro A733, 2 GB **SD**, nominal 250 GB NVMe. Board RAM,
  revision, SSD identity, power and cooling await first hardware evidence.
- Preferred release boot: SPI to NVMe with SD removed; immutable SD fallback,
  minimal host and real KVM-only guest remain mandatory. Nothing physical passed.
- [Diagnostic inputs](../configs/diagnostic-sources.lock.json): vendor build
  `a95129bf`, kernel `2ac08e8c` (6.6.98), U-Boot `b791be84` (2018.07), all full
  commits locked. A733 boot0/monitor/SCP/pack tools have 26 checked input hashes.
- Vendor KVM was disabled; diagnostic configuration enables it. Preserve the
  vendor monitor/kernel/DTB stack and legacy image wrapping; EL2 remains unproven.
- [Jammy builder](build-host-assessment.md): real loop mount, ARM64 chroot,
  Linux 6.6.98 and patched U-Boot builds passed, two compilation jobs at a time.
  GCC 11 kernel / Linaro 7.4.1 U-Boot. Samples retained over 6 GiB available RAM,
  about 41 GiB disk free and negligible swap use; no resource-limit claim inferred.
- **Diagnostic candidate built and independently verified**, not hardware booted:
  268,435,456-byte image, SHA-256 `b05086afaab3d2f9da6c33b8e457f3552cce2899c08824d371615008bf66d9fd`.
  Compressed: 16,188,652 bytes. [Manifest](evidence/diagnostic-20261104.json),
  [download/build/serial guide](diagnostic-image.md). Local files under ignored
  `out/diagnostic-20261104/`; builder `.../orange-pi-4-pro-build/artifacts/`.
- 35 Python host tests plus initramfs regression pass locally/Jammy. Actual image
  hashes, partition/filesystem contents, boot script CRC and wrapper payloads
  verified independently. NVMe planner and KVM-only launcher are preparation only.
- Independent Sol high boot/disk-selection reviews completed; reported size,
  fallback, toolchain, tree-discovery and geometry issues fixed with checks.
  Details and routing calibration: [implementation evidence](implementation-evidence.md).
- [Interim ARM64 userspace](host-userspace.md) built: QEMU 7.2.22, OpenSSH and
  storage tools, 220 version/hash-locked cached packages. Approximately 531 MiB
  unpacked / 169,852,145-byte tar. Identities scrubbed, services disabled; no boot,
  read-only root/state integration, installation or guest execution established.

## Open gates and next actions

1. Identify the actual SD on the provisioning computer, verify capacity/checksum,
   and obtain explicit exact-target authorization before flashing. SPI/NVMe have
   not been written; no destructive operation is authorized for an unknown target.
2. Capture full serial boot and inventory using the guide; investigate EL2/NVMe.
   Integrate the userspace prototype and build a separate virt guest fixture while
   hardware awaits. The A733 host config lacks the required guest virtio/PL011 drivers.
3. Full reproducibility remains open: U-Boot generates a random 128-byte debug
   challenge; two fresh U-Boot builds differ there plus checksums. Preserve that
   generated source next time; review its security role before any deterministic
   change. Clean-builder/package acquisition and opaque firmware license audit remain.
4. Deliver the minimal host, actual NVMe installer, guest fixture, update/rollback,
   backup/recovery and operational guides. The current NVMe tool is plan-only.

## Completion rule

[Hardware acceptance](hardware-validation.md) remains entirely pending: actual
KVM guest, installed NVMe boot, 10 cold/10 warm cycles, 24-hour workload, zero SD
writes, recovery/rollback/restore and independently reproduced deployment.
Build success, model review and emulation cannot satisfy these release gates.
