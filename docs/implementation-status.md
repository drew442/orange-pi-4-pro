# Implementation status

Updated 2026-11-04. Replace stale entries; keep this file short.

## Current milestone

The small headless diagnostic image is built. Collect live A733 inventory and
prove NVMe and KVM operation. Vendor images exceed the actual 2 GB SD capacity;
no larger card or live inventory was required to produce the diagnostic image.

## Decisions and evidence

- Target: Orange Pi 4 Pro **A733**, 2 GB **SD**, nominal 250 GB NVMe. Board RAM,
  revision, SSD identity, power and cooling await actual hardware evidence.
- Prefer SPI-to-NVMe with SD removed; immutable SD fallback, minimal host and
  real KVM guest remain mandatory. Nothing has passed physical acceptance.
- [Pinned diagnostic inputs](../configs/diagnostic-sources.lock.json): vendor
  build `a95129bf`, kernel `2ac08e8c` (6.6.98), U-Boot `b791be84` (2018.07).
  Full commits, toolchain hashes and 26 opaque input hashes are retained.
  Vendor KVM was disabled; diagnostic configuration enables it. EL2 is unproven.
- [Jammy builder](build-host-assessment.md): actual kernel/U-Boot builds,
  ARM64 chroot and file-backed loop checks passed. Two compilation jobs;
  GCC 11 kernel / Linaro 7.4.1 U-Boot. Latest idle sample has
  over 7.3 GB available RAM and 18 GB free disk; 4 GiB swap is configured.
- **Diagnostic candidate built and independently verified, not board-booted:**
  268,435,456 bytes, SHA-256
  `b05086afaab3d2f9da6c33b8e457f3552cce2899c08824d371615008bf66d9fd`;
  compressed 16,188,652 bytes. [Manifest](evidence/diagnostic-20261104.json),
  [download/flashing/serial guide](diagnostic-image.md). Local files are under
  ignored `out/diagnostic-20261104/`; builder `.../orange-pi-4-pro-build/artifacts/`.
- [Interim ARM64 userspace](host-userspace.md): QEMU 7.2.22, OpenSSH and storage
  tools; 220 version/hash-locked cached packages, approximately 531 MiB unpacked
  / 169,852,145-byte tar. Buildroot/release-base decision follows board baseline.
- [Managed A/B roots](host-payload.md): 4 GiB each, about 143 MB compressed,
  matching modules, read-only ext4 feature/no journal. Full hashes, contents
  and gzip expansions passed. New-root writable-loop checks deny read-write
  remounts and writes; no systemd or board boot.
- [Package-state isolation](host-package-state.md): eight version-owned views
  bind read-only from the selected root; 77.3 MB of caches pruned in private
  staging. Native A→B→A tests and ARM64 binfmt queries on both actual roots
  match all 220 package rows and 1,122 BOM entries; no physical update/rollback.
- [State](host-state.md), [NVMe selector](nvme-root-selection.md) and
  [handoff](host-handoff.md): 8 GiB journaled fixture with fresh installation
  identities; generic bootstrap fixes state UUID and ABI while root choice
  lives on recovered NVMe state. Selected-root-only resolution, ownership,
  GPT-role and cleanup fixtures pass. Physical selection/rollback remain open.
- [Root updates](host-updates.md): authenticated A/B file preparation and guarded
  inactive-slot staging CLI. Private loop interruption at 8 MiB and complete
  4 GiB retry/readback pass; selected/boot/guest sentinels stay unchanged.
  [Explicit selection](host-root-selection.md) adds durable journals, activation
  and cancellation; real ext4 interruption/readback checks pass. Live NVMe/TTY
  policy uses fixtures. [RAM recovery](host-recovery.md) archives malformed
  selectors and selects a retained root; native 250 GB GPT/8 GiB state checks
  pass. The portable [maintenance package](maintenance-tools.md) is included in
  the refreshed preparation SD. ARM imports/CLI, full 4 GiB authentication,
  debugfs and queued-SIGINT publication checks pass. Crash-journal replay, booted rollback and all
  physical update/recovery tests remain open.
- [Management preparation](host-management.md): exact Ethernet MAC DHCPv4,
  selected-IP key-only SSH, NVMe host-key creation and RAM configuration.
  ARM key/sshd/PAM-account/unit checks pass; no SSH connection or systemd boot.
  Test credentials/network settings make the current state nondeployable.
- [Complete SD host bootstrap](host-sd-bootstrap.md): 256 MiB raw /
  17,271,977 bytes compressed. Independent inspection and byte-identical
  clean-checkout assembly passed. It references fixture NVMe state.
- [Integrated NVMe preparation](nvme-provisioning.md): 250,000,000,000-byte
  sparse disk file combines GPT, boot, A/B, state and journaled guest storage.
  Full logical hash and independent readback passed. Clean-checkout assembly
  and independent readback passed with fresh disk/PARTUUID/boot/guest identities.
  Published full-disk payloads remain frozen pre-package-ABI fixtures. New
  component sets are verified separately; a new full-disk per-installation
  candidate and physical installation await real inputs.
- [Installer preparation](installer-preparation.md): pinned ARM Python/storage/TLS
  client runtime and 256 MiB SD candidate built; 26.9 MB compressed initramfs.
  Private ARM file-tool/CA/NSS probes, SD inspection and byte-identical clean
  repeat pass. Real board networking and installation remain open.
- [Guarded NVMe writer](nvme-installation.md): authenticated prepare/apply,
  exact-target TTY authorization, exclusive access, complete write/readback and
  conservative interruption reports implemented. Private native loop and ARM
  software checks pass; no production NVMe apply or physical acceptance.
- [Compressed delivery](nvme-distribution.md): verified 630,156,553-byte gzip
  expands to the full 250 GB fixture. Deterministic gzip recipe
  preserves fixture eligibility; verified HTTPS acquisition checks encoded and
  full expanded contents without raw staging. Updated installer and packaged
  ARM loopback TLS probes pass; clean-checkout packaging is byte-identical.
  Full-size native HTTPS acquisition/readback/expansion passed without raw
  staging; the fixture remains writer-ineligible.
  Board networking and real per-installation delivery remain untested.
- [Guest fixture](guest-fixture.md): separate virt-profile kernel/initramfs and
  disposable disk built and hashed; launcher requires KVM with no TCG fallback.
  [Guest launch guards](guest-operations.md) retain authenticated descriptors,
  lock disks and enforce same-NVMe guest placement before hashing. Real ARM
  QEMU file-backend checks pass. [Explicit activation](guest-activation.md) adds
  guarded p5 mounting and locked RAM accounts; actual private-loop interruption/
  retry/idempotence and ARM sysusers checks pass. **No guest boot or KVM execution.**
  Packaging/boot integration, enrollment, supervision and guest networking remain open.
- 501 Python cases across both suites and all shell regressions pass; the 497-case
  discovery suite passes on Jammy with no skips (two privileged skips locally). Actual
  files/build checks are distinct from hardware gates. Independent Sol high
  reviews and routing outcomes: [implementation evidence](implementation-evidence.md).

## Open gates and next actions

1. Identify the actual SD, verify capacity/checksum, and obtain exact-target
   authorization before flashing. SPI/NVMe have not been written; no destructive
   operation is authorized for an unknown target. Use the diagnostic guide and
   return full serial/inventory evidence, including EL2/NVMe observations.
2. Supply the administration public key and actual wired MAC. Validate real
   management activation and trusted host-key verification. Deliver the
   actual per-installation candidate and validate board payload acquisition. Validate
   the writer/locking/readback/interruption protocol on authorized hardware. The preparation runtime
   has exercised software probes; board boot/DHCP/DNS/TLS remain unproven.
3. Exercise the packaged maintenance/recovery tools on the board.
   Validate dirty-journal recovery, serial/manual rollback and backup restoration;
   cross-version updates/state compatibility remain unproven. Finish
   guest storage/networking, backup/restore and operational procedures.
4. Full clean-build reproducibility remains open: U-Boot emits a random
   128-byte debug challenge; builds differ there plus checksums. Exact generated
   source/binary/log evidence passed review and a
   [validation build](evidence/diagnostic-provenance-20261104.json). Review its
   role before deterministic changes. Clean builder/package acquisition and
   opaque-firmware/source/license audits remain.

## Completion rule

[Hardware acceptance](hardware-validation.md) remains entirely pending: actual
KVM guest, installed NVMe boot, 10 cold/10 warm cycles, 24-hour workload, zero SD
writes, interruption/rollback/recovery/restore and independently reproduced
deployment. Build success, model review and emulation cannot satisfy these gates.
