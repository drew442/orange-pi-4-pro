# Implementation status

Updated 2026-11-08. Detailed construction history is indexed in
[build reproduction status](build-reproduction-status.md). Physical release and
independently reproduced deployment remain incomplete.

## Current board action

The [existing-install repair succeeded](installed-host-repair-results-20261108.md).
Both authorized SSH keys were accepted. Trusted live SSH confirms wired DHCP
`192.168.1.116/24` on `end0`, MAC `10:f4:ae:90:5e:57`, with root A read-only
and NVMe state read-write. **Do not reinstall NVMe to fix management.**

[Actual KVM guest execution is now proven partially](first-kvm-results-20261108.md):
QEMU at UID/GID 60001 held VM/vCPU descriptors, and the guest passed CPU and
NVMe-backed disk-write tests. Counter `1` remained after stop. Networking did
not complete; the host stopped QEMU. **Guest clean poweroff, checkpoint,
restart/counter `2` and full guest acceptance remain open.**

The original host DT lacks the GIC maintenance interrupt (`KVM_CAP_IRQCHIP=0`).
The partial run used explicit GICv2 with KVM CPU execution and no PXE ROM,
plus the reviewed [RAM account-controller correction](evidence/account-overlay-correction-20261108.json).
The corrected guest initramfs preserves pinned BusyBox and checks fixed disk
identity without its unavailable `blkid`. Deployed immutable tools remain
unchanged; package the source corrections in a future host.

**Next physical action:** [flash and boot the reviewed VGIC diagnostic SD](next-host-boot-20261108.md).
`out/vgic-host-sd-20261108-trial01/a733-host-sd.img` is **256 MiB**, matched to
the existing `00fd…` NVMe state. Two Ubuntu 22.04 builds are byte-identical;
[independent artifact inspection](evidence/vgic-host-sd-20261108.json) passes.
Only the GIC maintenance-interrupt property changes; firmware, kernel,
initramfs, boot scripts and first 32 MiB remain unchanged. Physical boot is
pending. Return full serial/read-only checks and DHCP address, and leave SSH
available. No NVMe reinstall or SPI write is required.

The captured board clock remains April 27. The original 220-package deployment
has no NTP; separately prepared time-enabled userspace remains unbooted.
The transient machine-id commit unit fails against the intentional persistent
ext4 identity bind; suppress that inapplicable unit in a future image while
preserving identity. No SPI programming has occurred.

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
  and HTTPS after manual UTC correction; installed DHCP/SSH now pass; automatic time remains open.
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
| Existing deployment | [Published guide](deploy-personalized-20261106.md) retains original SD/NVMe images and state UUID `00fd936c-49fb-4fea-87c8-928b9c1599e0`. Physical repair, DHCP and both SSH keys now pass. |
| Installer refresh | [Source `151a532`](installer-refresh-20261107.md): 256 MiB, exact-target yes/no, optional readback/progress; packaged ARM/default-no-readback checks pass. New image unbooted; it does not automatically repair installed state. |
| Time-enabled userspace | [Actual offline build](timesync-outer-launcher.md) configures 221 pinned packages; complete archive readback and [dated repeat](account-date-replay-driver.md) pass. Fixed clock UID/GID 996, package/management ABI integration; installed NTP remains untested. |
| Production components | First [roots](production-host-roots.md), [state](production-host-state.md) and [bootstrap](production-host-bootstrap.md) pass construction/readback/review. [Second roots](production-host-roots-repeat.md), [state](production-host-state-repeat.md) and [bootstrap](production-host-bootstrap-repeat.md) also pass, with timestamp/identity differences documented. Second UUID `feaaee34-d782-4c18-bd04-1029540ab26a`; matching [full NVMe repeat](production-nvme-repeat.md) now passes construction and independent stream verification. |
| Full NVMe candidate | [Complete composition](production-nvme-composition.md) passes BOOT/GUESTS readback, all parent root/state bytes and independent full gzip/raw/GPT verification. [545.6 MB gzip and derived manifest](nvme-candidate-retention.md) retained on codex; [encoded retention](nvme-raw-retention.md) retires only the generated raw file, preserving both gzip copies. [Second composition and retained gzip](production-nvme-repeat.md) also pass. Active endpoint unchanged; matched physical deployment remains open. |
| NVMe-first firmware | [Compiled and exactly replayed U-Boot](uboot-nvme-candidate.md); [first SD transport](nvme-firmware-test-sd.md) and [second matching transport](nvme-repeat-firmware-sd.md) build and pass independent inspection. Each equals its matching bootstrap outside the boot package. Unbooted; paired with `fc8…` and `feaa…` respectively. Do not use with installed `00fd…` state. |
| SPI preparation | [Read-only backup tool](spi-backup.md) and [file assembler](spi-file-candidate.md) pass tests/review; only synthetic baseline assemblies exist. Synthetic files must never be flashed. Actual backup/candidate/programmer/recovery remain open. |
| ROM investigation | [Version-only host FEL probe](rom-recovery-investigation.md) now compiles in the frozen builder; source/archive/ELF/capsule review passes. No RAM/execute/flash command path. No actual USB response or independent ROM recovery demonstrated. |
| Host operations | [Updates](host-updates.md), [selection](nvme-root-selection.md), [RAM recovery](host-recovery.md) and package-state isolation pass software/packaged checks. Physical interrupted update, rollback, dirty-journal and missing-NVMe recovery remain open. |
| Guest operations | [KVM-only first guest](kvm-first-guest.md) now has [two authenticated preparation profiles](evidence/first-kvm-profiles-20261107.json): original installed state/39 tools or second-candidate state/40 tools. Management, networking, enrollment, checkpoints and off-device restore have software fixtures. [Physical GICv2/KVM diagnostics](first-kvm-results-20261108.md) pass CPU/disk only; networking and clean restart remain open. The VGIC test SD is built/reviewed, unbooted. |

Latest privileged Ubuntu discovery: **1,708 tests, no skips/errors/failures**;
five shell suites, five diagnostic assembly cases and Bash/TOML checks pass.
These results establish no physical boot, guest, SD-write or recovery acceptance.

## Capacity and reproducibility

The [authorized 20 GiB builder growth](build-host-capacity-plan.md) completed;
LV size is 88,017,469,440 bytes. [Further verified retention](repeat-build-retention.md)
preserves eleven superseded expanded leaves on both hosts and recovers about
5.17 GB. [Encoded retention](nvme-raw-retention.md) then recovers another 975 MB
without changing either gzip copy. The second bootstrap construction ends with
**23,760,064,512 bytes** free, before new full-NVMe project/capsule staging.
The full NVMe repeat now passes. New regular-file SD packaging stays
sparse and matches the prior SD raw/gzip bytes in the frozen builder. Recheck
staging overhead and the unchanged full-NVMe **22 GiB** initial guard before any
composition; **no guard reduction or additional LV growth is authorized**.
See [host assessment](build-host-assessment.md).

The [registered state repeat adapters](state-repeat-controller.md) pass independent
source review, committed-source planning, full privileged tests, actual state
construction/full readback and independent artifact review. The matching
bootstrap and full NVMe construction/stream validation also pass.

The [registered NVMe repeat](nvme-repeat-controller.md) passes source review,
committed planning and the full suite. Construction passed at source `339eef1`, tag `timesync-repeat-01`, with unchanged
host inventory and clean process/mount exit. Its [independent full stream check
and encoded retention](production-nvme-repeat.md) and independent artifact review pass.
[Capacity preparation](candidate-repeat-capacity.md) preserves all sparse image
hashes and retains the retired fixture in gzip on both hosts. After the new encoded copy, native free is **855,748,608 bytes**; remeasure before transfers.

[Verified retention](composition-retention.md) preserves retired work trees on
both hosts. The second matching NVMe-first transport ends with **21,926,354,944 bytes** free
on the builder, below a new full-NVMe initial admission. Native sparse conversion
preserves generated image bytes and leaves
the separate **256 MiB** free-space minimum intact. Keep generated/downloaded
artifacts outside Git. Recheck disk/RAM before each operation; start compilation
with two jobs.

[Clean-build evidence](build-reproduction-status.md) includes frozen-builder
restoration, source kernel/U-Boot replays, SD reproductions, normalized 220/221
package exports and separate dated repeats. Second production roots/state/bootstrap/NVMe constructions now pass with documented
timestamp/identity differences; independent deployment remains unfinished. All 479 userspace source archives
are acquired, not compiled. [Licensing evidence for the 26 vendor boot inputs](evidence/vendor-boot-license-inventory-20261106.json)
remains incomplete. [Compiler-source inventory](toolchain-sources.md) now pins
four acquired manifest-listed archives/eight notices and four exact Git
snapshots/35 notice paths. GCC and DejaGNU source inputs remain unacquired. [Userspace notices](userspace-notices.md) resolve for all 221
packages; notice presence does not establish legal sufficiency. No public
redistribution clearance is claimed.

## Next actions and completion gates

1. Boot the [VGIC diagnostic SD](next-host-boot-20261108.md), return its serial/capability checks and current DHCP address, then finish guest networking, guest-initiated shutdown/restart and counter progression over trusted SSH. The original first-guest sequence needs the documented controller/fixture corrections; do not run it unchanged.
2. Package the reviewed account and ROM corrections and suppress the inapplicable machine-id commit unit in a future host; validate prepared automatic time on hardware.
3. The [matched second-candidate procedure](deploy-candidate-20261107.md) and
   actual full-payload acquisition rehearsal pass independent review. The [dedicated LAN endpoint](candidate-delivery-20261107.md) now serves it on
   port 8444 with full builder acquisition/TLS proof and trusted CA instructions;
   original port 8443 remains unchanged. Independently reproduce deployment on
   hardware after the current management/KVM gates. The candidate/transport SD
   must not replace the current repair images alone.
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
