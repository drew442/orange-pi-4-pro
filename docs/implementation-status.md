# Implementation status

Updated 2026-11-04. Replace stale entries; detailed evidence lives in linked files.

## Current milestone

The 256 MiB diagnostic booted to its RAM shell on the A733. Its BL31 handoff
enters **EL1**, and KVM reports HYP unavailable. A separately built and reviewed
[EL2 diagnostic](el2-diagnostic.md) is ready for the next physical boot; **its
hardware behavior is untested**. NVMe installation cannot resolve EL1 entry.

## Decisions and physical evidence

- Preserve A733, actual 2 GB SD and nominal 250 GB NVMe requirements. Prefer
  SPI-to-NVMe with SD removed; otherwise immutable SD with zero routine writes.
  Actual KVM guest execution remains mandatory.
- [Physical logs](board-diagnostic-20261104.md): 12 GiB LPDDR5, eight CPUs;
  SU02G SD 1,977,614,336 bytes, CID `035344535530324780716f216e00a8f5`;
  NVMe 256,060,514,304 bytes, model `SPCC M.2 PCIe SSD`, serial
  `134807190FC700149277`, firmware `EDFM20.0`. Use actual NVMe geometry.
- Heatsinks remain attached. Diagnostic outputs supply component identities;
  chip markings are unnecessary. Power rating/thermal endurance remain open.
- User explicitly authorized discarding that exact NVMe's data. Revalidate its
  identity/idle state immediately before the concrete installation; do not ask
  again for matching-device erase permission. No coordinator NVMe erase or SPI
  programming has occurred. SPI programming remains unauthorized.
- SD is unmounted in Linux; one write-counter snapshot is zero, but `ro=0`.
  Firmware audit, offline full-card hash comparisons and cycle/workload evidence
  remain necessary. A single Linux snapshot does not prove zero routine writes.
- Wired MAC `10:f4:ae:90:5e:57`, 1 Gbps link; DHCP has no lease/IPv4 route.
  Administration public key and DHCP/static management choice requested.

## Builds and host-only verification

- [Source locks](../configs/diagnostic-sources.lock.json): vendor build
  `a95129bf`, kernel `2ac08e8c` (6.6.98), U-Boot `b791be84` (2018.07), pinned
  toolchains/packages and 26 opaque firmware/tool inputs. Compatible boot0,
  monitor, SCP, kernel and DTB remain together.
- [Booted baseline](diagnostic-image.md): 268,435,456-byte raw image, SHA-256
  `b05086afaab3d2f9da6c33b8e457f3552cce2899c08824d371615008bf66d9fd`.
  Vendor images do not fit; no larger card or prior live inventory was required.
- [EL2 candidate evidence](evidence/el2-diagnostic-20261104.json): 268,435,456
  bytes, SHA-256 `08fff0a88c509ee484c2450c58d42629fd90eedb8cd4c57513f98f4f78611b58`;
  gzip 16,188,653 bytes. Only the equal-size boot package is replaced; all outside
  bytes match the authenticated baseline, including the complete filesystem.
  Emitted monitor call is `0x8000ff40`; expected SPSR is `0x3c9`. Packed monitor/
  SCP and copied kernel/DTB/initramfs/boot0 hashes pass; physical EL2 is unproven.
- [Jammy builder](build-host-assessment.md): actual builds with two compilation
  jobs, GCC 11 kernel/Linaro 7.4.1 U-Boot; 4 GiB swap. Latest sample during guest
  fixture rebuild: over 10 GB free disk and 7.4 GB available RAM.
- [Minimal interim ARM64 userspace](host-userspace.md): 220 version/hash-locked
  packages, QEMU 7.2.22, OpenSSH/storage tools; approximately 531 MiB unpacked.
  Final Buildroot/release-base decision follows working hardware KVM baseline.
- [Read-only A/B roots](host-payload.md), [package-state isolation](host-package-state.md),
  [NVMe state](host-state.md), [selection](nvme-root-selection.md) and
  [handoff](host-handoff.md) pass file-backed and packaged ARM checks. Current
  [complete maintenance/guest tree](guest-tools-package.md) has 39 files at
  `/usr/lib/a733-tools`; newer networking package/fixture are separate artifacts.
  [Management](host-management.md) has ARM sshd/key/unit checks; no real SSH login.
- [Updates](host-updates.md), [selection journals](host-root-selection.md) and
  [RAM recovery](host-recovery.md) pass private-loop interruption/readback tests.
  Physical update, dirty-journal recovery and rollback remain untested.
- [Host SD bootstrap](host-sd-bootstrap.md), [preparation runtime](installer-preparation.md),
  [NVMe assembly](nvme-provisioning.md), [guarded writer](nvme-installation.md) and
  [authenticated compressed delivery](nvme-distribution.md) have actual file/ARM/
  loop/HTTPS checks. Published full-disk images remain **nondeployable fixtures**
  with earlier package ABI/test identities and 250,000,000,000-byte geometry.
  Build a fresh per-installation image using actual capacity/key/network inputs.
- [Guest fixture](guest-fixture.md), [KVM-only launch](guest-operations.md),
  [activation](guest-activation.md), [enrollment](guest-enrollment.md),
  [checkpoints](guest-checkpoints.md), [backup](guest-backups.md),
  [restore](guest-restoration.md), [HTTPS restore](guest-restore-delivery.md) and
  [isolated networking](guest-networking.md) pass host/packaged ARM checks.
  **No guest has booted.** No acceleration fallback is permitted. The new fixed
  seed passes actual Jammy root/drew byte-identical builds; the
  [complete fixture rebuild](evidence/guest-seed-fixture-20261104.json) passes
  with matching kernel/initramfs and new pinned seed. Earlier artifacts remain unchanged.
- Jammy: 719 discovery cases without skips, four diagnostic assembly cases and
  all five shell regressions pass. Independent Sol high reviews cover boot,
  installation/selection and rollback changes. [Detailed implementation evidence](implementation-evidence.md)
  separates builds, software probes and physical results.

## Next actions and open gates

1. Boot the [reviewed EL2 candidate](el2-diagnostic.md); return full serial capture,
   image readback and before/after whole-SD hashes. Require SPSR `0x3c9`, eight
   CPUs at EL2, KVM initialization and `/dev/kvm`, then actual KVM guest execution.
2. Resolve management addressing and administration key; produce actual-geometry
   installation inputs and reviewed recovery artifacts. Validate real board
   boot/DHCP or static addressing/DNS/TLS, guarded NVMe write/readback and boot.
3. Prove installed NVMe operation, SD policy, repeated boots/workload, interrupted
   updates, manual rollback, dirty-journal recovery and off-device restore. SPI
   needs chip identification, backup and proven recovery before authorization.
4. Full clean-build reproducibility remains open: vendor U-Boot generates a
   random 128-byte challenge, retained with exact provenance. Clean package/source
   acquisition and opaque firmware/source/license audit remain release gates.

## Completion rule

[Release acceptance](hardware-validation.md) remains incomplete: actual KVM guest,
installed NVMe boot, 10 cold/10 warm cycles, 24-hour workload, zero routine SD
writes, interruption/rollback/recovery/restore and independently reproduced
deployment. Build success, model review and emulation cannot satisfy these gates.
