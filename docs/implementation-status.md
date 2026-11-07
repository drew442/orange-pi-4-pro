# Implementation status

Updated 2026-11-07. Detailed construction history is indexed in
[build reproduction status](build-reproduction-status.md). Physical release and
independently reproduced deployment remain incomplete.

## Current board action

Apply the [existing-install machine-id repair](installed-host-repair-20261107.md),
then boot the **same deployed host SD**. Return the complete serial log, DHCP
address and SSH results with both authorized keys. **Do not reinstall NVMe to
fix management.**

[Physical logs](evidence/installed-host-first-boot-20261107.json) prove successful
NVMe installation/full readback and installed root A reaching systemd. All eight
CPUs enter EL2 and KVM initializes. Ethernet links at 1 Gbps, but installed
DHCP/SSH fails; the serial root account has no usable password. The deployed
machine-id mode `0600` blocks unprivileged networkd's DHCP identity access.
[Actual pinned ARM-library checks](evidence/installed-host-fix-20261107.json)
reproduce the defect; the repair changes that one file to `0644`.
Physical repair, SSH and an actual KVM guest are still unproven.

## Fixed requirements and authorization

- A733, actual **1,977,614,336-byte SD**, nominal 250 GB NVMe, minimal Linux
  KVM/QEMU and **zero routine SD writes**. Prefer SPI-to-NVMe with SD removed;
  otherwise immutable SD bootstrap. Emulation/containers cannot satisfy KVM.
- [Inventory](board-diagnostic-20261104.md): 12 GiB LPDDR5; SU02G SD CID
  `035344535530324780716f216e00a8f5`; NVMe `SPCC M.2 PCIe SSD`, serial
  `134807190FC700149277`, firmware `EDFM20.0`, **256,060,514,304 bytes, 512/512**.
  Exact-NVMe erase authorization persists; revalidate identity and idle state
  immediately before any future concrete write. The operator completed installation.
- [Power/cooling](evidence/power-cooling-20261106.json): reported 67 W USB-PD
  adapter, 10.4 mm six-layer whole-board copper heatsink. Leave heatsinks attached;
  diagnostic identities suffice. Negotiated power, fan and thermal endurance are unproven.
- [Management inputs](evidence/operator-inputs-20261105.json): both RSA3072 and
  Ed25519 keys, hostname `a733-host`, public-key-only SSH from any source/subnet.
  Wired MAC `10:f4:ae:90:5e:57`. Preparation boot obtained DHCP `192.168.1.115/24`
  and HTTPS after manual UTC correction; installed automatic networking/time remain open.
- SPI: **XM25QU128C**, JEDEC `204118`, 16 MiB, erase 65,536/write 1 byte.
  Preparation is authorized; present the exact live target/write plan and request
  simple **yes/no** before programming. No SPI write has occurred. Original backup,
  off-device retention and independent recovery must precede a programming proposal.
- Installation uses one expanded write with progress; readback is optional.
  Do not benchmark the board to diagnose installation duration. Builder artifact
  validation does not add mandatory onboard installation passes.

## Validated software and retained candidates

| Area | Evidence and current limit |
| --- | --- |
| Compatible board stack | [Locked sources](../configs/diagnostic-sources.lock.json): vendor `a95129bf`, kernel `2ac08e8c`/6.6.98, U-Boot `b791be84`, pinned toolchains/boot inputs. [EL2 diagnostics](el2-diagnostic.md) physically initialize KVM; the separate modalias fix remains unbooted. |
| Existing deployment | [Published guide](deploy-personalized-20261106.md) retains original SD/NVMe images and state UUID `00fd936c-49fb-4fea-87c8-928b9c1599e0`. Management repair is next. |
| Installer refresh | [Source `151a532`](installer-refresh-20261107.md): 256 MiB, exact-target yes/no, optional readback/progress; packaged ARM/default-no-readback checks pass. New image unbooted; it does not automatically repair installed state. |
| Time-enabled userspace | [Actual offline build](timesync-outer-launcher.md) configures 221 pinned packages; complete archive readback and [dated repeat](account-date-replay-driver.md) pass. Fixed clock UID/GID 996, package/management ABI integration; installed NTP remains untested. |
| Production components | First [roots](production-host-roots.md), [state](production-host-state.md) and [bootstrap](production-host-bootstrap.md) pass construction/readback/review. [Second roots](production-host-roots-repeat.md), [state](production-host-state-repeat.md) and [bootstrap](production-host-bootstrap-repeat.md) also pass, with timestamp/identity differences documented. Second UUID `feaaee34-d782-4c18-bd04-1029540ab26a`; matching full NVMe repeat remains open. |
| Full NVMe candidate | [Complete composition](production-nvme-composition.md) passes BOOT/GUESTS readback, all parent root/state bytes and independent full gzip/raw/GPT verification. [545.6 MB gzip and derived manifest](nvme-candidate-retention.md) retained on codex; [encoded retention](nvme-raw-retention.md) retires only the generated raw file, preserving both gzip copies. Active endpoint unchanged; clean repeat and matched deployment remain open. |
| NVMe-first firmware | [Compiled and exactly replayed U-Boot](uboot-nvme-candidate.md); [SD transport](nvme-firmware-test-sd.md) builds and passes independent inspection. Entire SD is identical to production bootstrap outside the boot package. Unbooted, paired only with new `fc8…` state; do not use with installed `00fd…` state. |
| SPI preparation | [Read-only backup tool](spi-backup.md) and [file assembler](spi-file-candidate.md) pass tests/review; only synthetic baseline assemblies exist. Synthetic files must never be flashed. Actual backup/candidate/programmer/recovery remain open. |
| ROM investigation | [Version-only host FEL probe](rom-recovery-investigation.md) now compiles in the frozen builder; source/archive/ELF/capsule review passes. No RAM/execute/flash command path. No actual USB response or independent ROM recovery demonstrated. |
| Host operations | [Updates](host-updates.md), [selection](nvme-root-selection.md), [RAM recovery](host-recovery.md) and package-state isolation pass software/packaged checks. Physical interrupted update, rollback, dirty-journal and missing-NVMe recovery remain open. |
| Guest operations | [KVM-only first guest](kvm-first-guest.md), [management](guest-operations.md), networking, enrollment, checkpoints and off-device backup/restore have software fixtures. **No guest has booted.** |

Latest privileged Ubuntu discovery: **1,669 tests, no skips/errors/failures**;
five shell suites, five diagnostic assembly cases and Bash/TOML checks pass.
These results establish no physical boot, guest, SD-write or recovery acceptance.

## Capacity and reproducibility

The [authorized 20 GiB builder growth](build-host-capacity-plan.md) completed;
LV size is 88,017,469,440 bytes. [Further verified retention](repeat-build-retention.md)
preserves eleven superseded expanded leaves on both hosts and recovers about
5.17 GB. [Encoded retention](nvme-raw-retention.md) then recovers another 975 MB
without changing either gzip copy. The second bootstrap construction ends with
**23,760,064,512 bytes** free, before new full-NVMe project/capsule staging.
The full NVMe repeat remains open. New regular-file SD packaging stays
sparse and matches the prior SD raw/gzip bytes in the frozen builder. Recheck
staging overhead and the unchanged full-NVMe **22 GiB** initial guard before any
composition; **no guard reduction or additional LV growth is authorized**.
See [host assessment](build-host-assessment.md).

The [registered state repeat adapters](state-repeat-controller.md) pass independent
source review, committed-source planning, full privileged tests, actual state
construction/full readback and independent artifact review. The matching
bootstrap also passes; full NVMe construction/stream validation remain open.

The [registered NVMe repeat](nvme-repeat-controller.md) passes source review,
committed planning and the full suite. Construction passed at source `339eef1`, tag `timesync-repeat-01`, with unchanged
host inventory and clean process/mount exit. Its **independent stream check is
running**; stream pass and final artifact review remain pending.
[Capacity preparation](candidate-repeat-capacity.md) preserves all sparse image
hashes and retains the retired fixture in gzip on both hosts. Native free space
after the final conversion is **1,410,519,040 bytes**; remeasure before transfers.

[Verified retention](composition-retention.md) preserves retired work trees on
both hosts. Native sparse conversion preserves generated image bytes and leaves
the separate **256 MiB** free-space minimum intact. Keep generated/downloaded
artifacts outside Git. Recheck disk/RAM before each operation; start compilation
with two jobs.

[Clean-build evidence](build-reproduction-status.md) includes frozen-builder
restoration, source kernel/U-Boot replays, SD reproductions, normalized 220/221
package exports and separate dated repeats. Full new production component repeats
and independent deployment are unfinished. All 479 userspace source archives
are acquired, not compiled. [Licensing evidence for the 26 vendor boot inputs](evidence/vendor-boot-license-inventory-20261106.json)
remains incomplete. [Userspace notices](userspace-notices.md) resolve for all 221
packages; notice presence does not establish legal sufficiency. No public
redistribution clearance is claimed.

## Next actions and completion gates

1. Complete the existing-install repair and verify SSH with **both** keys.
2. Run the [first actual KVM guest](kvm-first-guest.md): CPU/disk/network, clean
   shutdown/restart and persisted disk marker. `/dev/kvm` alone is insufficient.
3. Continue clean production repeats and prepare a tested matched deployment
   procedure. The new candidate/transport SD must not replace the current repair
   images without an explicit matched deployment plan.
4. After management works, retain the [original SPI backup](spi-backup.md)
   off-device and demonstrate independent recovery. The manual's ADB/button
   descriptions and FEL version identification alone do not prove this. Capture
   U-Boot NVMe file reads; later test the matched NVMe-first transport/default and
   manual SD fallback. Independently inspect the actual SPI candidate before
   exact-target yes/no programming confirmation.
5. Prove automatic time/state transitions, 10 cold/10 warm boots, 24-hour CPU/
   disk/network/thermal workload, SD-removed SPI boot or zero routine fallback SD
   writes (full-card hashes, Linux counters and firmware audit), interrupted
   update/manual rollback, dirty-journal/missing-NVMe recovery and off-device
   guest restoration. Reproduce the deployment from the guide independently.

Finish only when [release acceptance](hardware-validation.md) and the
[plan](hypervisor-plan.md) are satisfied. Builds, reviews and emulation cannot
waive any physical requirement.
