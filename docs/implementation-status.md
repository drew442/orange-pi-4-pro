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
  GCC 11 kernel / Linaro 7.4.1 U-Boot. Latest check: about 7 GiB available RAM,
  34 GiB disk free and negligible swap use; no resource-limit claim inferred.
- **Diagnostic candidate built and independently verified**, not hardware booted:
  268,435,456-byte image, SHA-256 `b05086afaab3d2f9da6c33b8e457f3552cce2899c08824d371615008bf66d9fd`.
  Compressed: 16,188,652 bytes. [Manifest](evidence/diagnostic-20261104.json),
  [download/build/serial guide](diagnostic-image.md). Local files under ignored
  `out/diagnostic-20261104/`; builder `.../orange-pi-4-pro-build/artifacts/`.
- 78 Python test cases pass, with privileged state cases run on Jammy; shell
  regressions pass. Actual image
  hashes, partition/filesystem contents, boot script CRC and wrapper payloads
  verified independently. NVMe planner and KVM-only launcher are preparation only.
- Independent Sol high boot/disk-selection reviews completed; reported size,
  fallback, toolchain, tree-discovery and geometry issues fixed with checks.
  Details and routing calibration: [implementation evidence](implementation-evidence.md).
- [Interim ARM64 userspace](host-userspace.md) built: QEMU 7.2.22, OpenSSH and
  storage tools, 220 version/hash-locked cached packages. Approximately 531 MiB
  unpacked / 169,852,145-byte tar. Identities scrubbed, disable-service presets;
  no host boot, installation or guest execution established.
- [Guest fixture](guest-fixture.md) built from a separate virt profile: 5.25 MB
  kernel, 0.97 MB initramfs, disposable 128 MiB disk. Hashes, seed and dry-run pass;
  **no guest boot or KVM execution**. Script execution/WLAN issues fixed after review.
- [A/B root payloads](host-payload.md) built: 4 GiB each, about 173 MB compressed,
  matched kernel modules, ext4 read-only feature/no journal. Actual file-loop
  remount/write denial and content/hash checks passed. Provisioned state, management,
  physical boot, installer and update integration remain open.
- [NVMe boot resolver](host-storage-boot.md): authenticated ARM64 `blkid` runtime
  and read-only UUID/identity selection implemented; 39 fixtures pass with the exact
  diagnostic BusyBox. Private chroot/file probes passed; no mounts or host boot.
  Independent review's duplicate-field parser finding was fixed and rechecked.
- [Host handoff prototype](host-handoff.md) packaged: 4,761,354-byte initramfs,
  authenticated compatible inputs, RO root/state validation then NVMe state RW,
  bounded tmpfs and ephemeral `/etc`. 49 handoff fixtures, pinned ARM64 ash checks
  and actual builder overlay test passed; clean Git checkout packaging repeated
  byte-identically. Cleanup/permission findings fixed after review; no complete
  host boot image or board handoff yet.
- [State payload recipe](host-state.md) built an 8 GiB journaled regular-file
  fixture (39.8 MB compressed), fresh identities, authenticated `/var` and operator
  public key/config. Independent readback/gzip checks and 14 builder regressions
  passed; clean-checkout repeat produced distinct installation identities and
  passed readback. Test credentials/network values prevent deployment; runtime
  management activation and a physical NVMe writer remain open.

## Open gates and next actions

1. Identify the actual SD on the provisioning computer, verify capacity/checksum,
   and obtain explicit exact-target authorization before flashing. SPI/NVMe have
   not been written; no destructive operation is authorized for an unknown target.
2. Capture full serial boot and inventory using the guide; investigate EL2/NVMe.
   Integrate userspace and the guest fixture while hardware awaits; guest drivers
   use a separate profile. Supply the administration public key; configure and
   activate persistent management. Move A/B boot selection to NVMe before routine
   updates; current initramfs fixes one root. Physical installer remains open.
3. Full reproducibility remains open: U-Boot generates a random 128-byte debug
   challenge; fresh U-Boot builds differ there plus checksums. Exact generated
   source/binary/log collection passed independent review and an end-to-end
   [validation build](evidence/diagnostic-provenance-20261104.json); original download
   unchanged. Review its role before deterministic changes. Clean-builder/package
   acquisition and opaque firmware license audit remain.
4. Deliver the minimal host, actual NVMe installer, guest fixture, update/rollback,
   backup/recovery and operational guides. The current NVMe tool is plan-only.

## Completion rule

[Hardware acceptance](hardware-validation.md) remains entirely pending: actual
KVM guest, installed NVMe boot, 10 cold/10 warm cycles, 24-hour workload, zero SD
writes, recovery/rollback/restore and independently reproduced deployment.
Build success, model review and emulation cannot satisfy these release gates.
