# Implementation status

Updated 2026-11-09. See [build reproduction status](build-reproduction-status.md)
for construction history and evidence links. Physical release acceptance and
independent deployment reproduction remain incomplete. SSH has recovered and an
independent UART capture/control host is available; goal work has resumed.
The original endurance failure remains unexplained. A captured boot exposed
unsafe filesystem-only read-only STATE probing; block-level protection is reviewed
and tested, with a replacement bootstrap being prepared.

## Current state

- **Board and storage:** Orange Pi 4 Pro A733; actual SD size
  **1,977,614,336 bytes** (SU02G); NVMe size **256,060,514,304 bytes**,
  serial **134807190FC700149277**, SPCC M.2 PCIe SSD, firmware EDFM20.0,
  512/512-byte sectors. SD CID `035344535530324780716f216e00a8f5`.
  Minimal Linux/KVM/QEMU is the target.
  Preserve zero routine SD writes; prefer SPI-to-NVMe with SD removed, otherwise
  use immutable SD boot files. Leave attached heatsinks in place.
- **Running now:** Corrected VGIC host at **192.168.1.116**, boot ID
  `2e32858c-1ef8-4ae5-860f-9959cd38a1e0`; pinned SSH, read-only NVMe p2 root,
  STATE `360d…`, complete 40-file tool closure and automatic NTP pass again.
  No failed units or management restarts were observed. A read-only slot-B
  preparation passed with console guard monitoring; no selection was applied.
  The user reported accidental restarts of both boards; these are not classified
  cold/warm acceptance cycles. [Current serial evidence](evidence/serial-control-20261109.json).
- **Out-of-band console:** `orangepi@192.168.1.117` captures A733 UART via
  `ttyS3-capture.service` to `/var/log/ttyS3.log`. That directory is RAM-backed;
  a bounded independent relay now preserves output on codex. UART BREAK + SysRq
  help physically passes; UART reboot is untested. No GPIO wiring was added.
- **STATE probe fix:** Captured legacy boot performed orphan cleanup despite
  `ro,noload`. Reviewed boot/export changes enforce kernel block RO before probe;
  [four real ext4 fixtures](evidence/state-block-protection-20261109.json), including
  synthetic legacy orphans, preserve complete source bytes and all write counters.
  The fixed bootstrap is not yet built/deployed; physical dirty recovery remains open.
- **Previous NVMe host:** The earlier `00fd…` host was repaired before replacement.
  Trusted live SSH verified DHCP `192.168.1.116/24` on `end0`, MAC
  `10:f4:ae:90:5e:57`, and acceptance of both authorized keys. SSH remains
  public-key-only from any source/subnet. That repair did not require reinstallation. See
  [repair results](installed-host-repair-results-20261108.md).
- **Actual KVM guest core tests pass:** [Physical CPU/disk/network, clean
  poweroff/checkpoint/restart](kvm-vgic-results-20261108.md) advance the disk
  **1 → 2 → 3** under nonroot KVM/GICv3. Both durable checkpoints pass.
  Counter 3 retains exact exit **1/0**; counter 2's garbage-collected exit fields
  remain an explicit observation gap. The earlier counter-1 host stop is not
  reclassified as clean. Final corrected-host deployment/endurance remain open.
- **VGIC diagnostic:** The [approved SD write and physical warm boot pass](vgic-live-boot-20261108.md).
  Boot ID `7f1f07c4-5a4f-469f-97a7-612406d915a2` has the maintenance property
  `<1 9 4>`, **KVM_CAP_IRQCHIP=1**, GIC system-register CPU interface and
  `vgic interrupt IRQ9`. DHCP/SSH returned on the existing installation.
  The counter-1 guest disk and original enrollment survived unchanged;
  off-device guest backup and guarded p5 activation pass. [Normal-GIC guest tests](kvm-vgic-results-20261108.md) now pass; this remains
  a diagnostic host, not final release acceptance.
- **Guest backup and restoration:** The stopped counter-3 guest and both checkpoint
  records have an [authenticated backup on builder and codex](evidence/vgic-counter3-backup-20261108.json).
  [Physical restoration and KVM boots 3 → 4 → 5](evidence/corrected-host-guest-restore-20261108.json)
  now pass on the corrected host; both new checkpoints are durable.
- **Endurance:** The [60-second physical smoke](evidence/endurance-smoke-physical-20261108.json)
  passes with four KVM vCPUs, four additional nonroot host workers, verified
  network transfers, three disk cycles and independently observed clean exit.
  The [24-hour run](endurance-run-20261108.md) started **2026-11-08 11:06:24 UTC**;
  it **failed management reachability** at collector elapsed 300.005 seconds.
  Last valid guest sample is elapsed 357 seconds/cycle 12; temperatures were below
  29 °C, available memory 11,589,748 KiB, NVMe kernel errors absent and SD writes
  zero. Root cause remains unexplained by retained logs. Subsequent guest/load
  state was not observed before the user restarted the boards. Serial capture
  is now active for reproduction.
  Its initial 86-second telemetry gap is also recorded. The
  [read-only offline journal exporter](offline-host-journal.md) passes independent
  review and [real ext4 loop checks](evidence/offline-journal-loop-20261108.json);
  actual board recovery export is unperformed. See the [current image map](current-images.md).
- **Corrected candidate:** Runtime-fixed roots, matched state and bootstrap
  passed construction, readback/inspection and independent artifact review;
  the corrected VGIC chain now physically boots. The matched full-NVMe producer (source `6c34629`, tag
  `timesync-runtime-fixes-01`) now passes construction, producer readback and
  final environment checks, independent complete stream verification and
  independent artifact review. Hardware acceptance remains open. See
  [construction evidence](production-nvme-runtime-fixes.md).
- **Corrected VGIC bootstrap:** [Two constructions and independent artifact review](vgic-runtime-bootstrap.md) pass from `18af215`; raw/gzip/initramfs bytes match.
  The 256 MiB image fits the actual SD and selects corrected state `360d…`.
  Matched NVMe derivation, approved writes and actual corrected-host boot now pass;
  historical construction records retain their construction-time classification.
- **Private SSH RAM installer:** [Construction, independent review and ARM
  session tests](evidence/ssh-ram-installer-20261108.json) pass. The private
  256 MiB SD candidate preserves firmware/kernel/VGIC and both authorized keys.
  Actual RAM boot, Ed25519 login, PTY and idle-NVMe observations now pass.
  Full VGIC NVMe construction and producer readback pass after preserving empty
  `lost+found`; [independent complete artifact comparison now passes](evidence/vgic-nvme-image-20261108.json).
  The installation preserved same-board identity using the reviewed
  [STATE helper](known-host-state-personalization.md), then used the reviewed
  [known SD writer](known-bootstrap-sd-writer.md) for the matched bootstrap.
  Both maintenance helpers now completed physical writes with durable receipts.
  The full NVMe stream was installed without readback, then identity was restored;
  its original stream hash does not describe the personalized whole device.
- **SPI:** Original backup and a private candidate derived from it are reviewed;
  the XM25QU128C, JEDEC `204118`, 16 MiB backup is blank (`0xff`).
  The [fresh read-only live comparison](spi-live-plan-results-20261108.md)
  matches the retained baseline, candidate and all 256 unit pins. The candidate
  is prepared, not programmed.
  Independent recovery proof and a reviewed programmer remain pending.
  Programming is authorized for this known target once those prerequisites pass.

## Acceptance requirements and evidence

The preparation and diagnostic SD boots enter EL2 on all eight CPUs. They do
not establish final host acceptance: an actual KVM guest is required. Vendor
images exceed the SD capacity. The board inventory and pinned compatible
firmware/kernel inputs are in [diagnostic inventory](board-diagnostic-20261104.md)
and [source locks](../configs/diagnostic-sources.lock.json).

The corrected 221-package host now synchronizes time automatically and masks
machine-id commit. Packaged account creation and the ROM-free launcher now run
on the corrected host; restored guest counter **3 → 4 → 5**, CPU, disk, network
and independently observed clean exits pass. Durable checkpoint 4 passes;
checkpoint 5 also passes. See [restoration evidence](evidence/corrected-host-guest-restore-20261108.json). Reported
power/cooling is a 67 W USB-PD adapter and 10.4 mm six-layer whole-board copper
heatsink; negotiated power, fan and thermal endurance remain unproven.

Physical gates still include: completing
final corrected-host guest validation and proving
automatic time and corrected state behavior; 10 cold and 10 warm boots; a
24-hour CPU/disk/network/thermal workload; SPI-to-NVMe boot with SD removed or
verified zero routine writes on fallback; interrupted-update rollback;
missing-NVMe and dirty-journal recovery; off-device guest restoration; and
independent deployment reproduction. See [hardware validation](hardware-validation.md)
and the [hypervisor plan](hypervisor-plan.md) for gate definitions.

Physical write rules: installation uses one expanded write with progress;
readback is optional. Do not benchmark the board to diagnose installation
duration. Leave heatsinks attached. Before any destructive operation, show and revalidate
the exact device identity and write plan. Operator authorization now persists
for the known SD, NVMe and SPI; ask yes/no only for a new target or scope.
Existing exact-NVMe erase authorization persists; revalidate its identity and
idle state before any future concrete write. SPI programming still requires its independent recovery and reviewed
programmer/rollback prerequisites. Builder checks and emulation do not
count as physical acceptance.

## Build and host evidence

- The corrected-chain root components, state and bootstrap have passed their
  documented construction and inspection checks. The full NVMe producer is
  complete; independent stream inspection and artifact review pass.
  Corrected VGIC-chain physical boot now passes.
- Actual old-seed retirement removed **91,578** entries; all **7** protected
  postchecks passed. Source/quarantine are absent and the probe, mask, records,
  active builder and backups remain. Free space at completion was
  **23,848,976,384 bytes**, before later validation staging.
- Four duplicate native archives were retired after 25 Ubuntu fixtures and
  independent review, recovering **552,181,760 allocated bytes**. Canonical
  copies remain on both hosts; restore has not been exercised. The unchanged
  initial admission guard is **23,760,999,827 bytes**. Native free at completion was **872,210,432 bytes**; preserve its 256 MiB floor.
  Runtime floors remain **3,000,000,000 free disk bytes** and
  **1,000,000,000 available memory bytes**. Recheck actual resources before
  subsequent jobs. Do not reduce the guard or
  grow the LV without authorization. See [native archive retention](native-archive-retention-20261108.md)
  and [capacity assessment](build-host-assessment.md).
- [Coherent-build capacity retention](coherent-build-capacity-20261108.md) now
  retires three superseded expanded cache files, keeping their verified encoded
  recovery copies and all current corrected inputs. Builder free at completion
  is **24,076,783,616 bytes**; recheck each new source/capsule budget.
- [Historical kernel-cache relocation](native-kernel-cache-relocation-20261108.md)
  preserves a verified 893 MB generated archive on builder and releases native
  capacity for current images. Native free at completion was 1,165,426,688 bytes;
  remeasure above its unchanged 256 MiB floor. Restoration is unperformed.
- Latest privileged Ubuntu suite: **1,960 tests**, no skips, errors or failures;
  five shell suites and five diagnostic assembly cases pass. See the
  [latest record](evidence/state-block-protection-20261109.json). Jammy lacks `tomllib`;
  all five TOML files passed the separate native parser. These are host
  checks only, not hardware acceptance.
- Source/licensing work remains incomplete: licensing for 26 vendor boot inputs
  is unresolved; GCC and DejaGNU sources remain unacquired; notice presence does
  not establish legal sufficiency or public redistribution clearance. See
  [license inventory](evidence/vendor-boot-license-inventory-20261106.json),
  [toolchain sources](toolchain-sources.md) and [userspace notices](userspace-notices.md).

## Next actions

1. Build, independently inspect and deploy the protected STATE bootstrap, then
   reproduce management/endurance with the independent serial monitor. Preserve
   kernel/control evidence on any recurrence. Complete the full 24-hour workload;
   the previous attempt remains failed.
2. The verified `timesync-runtime-fixes-01` image is retained on both hosts;
   recheck resource floors before transfers. Integrate and
   validate matching NVMe-first firmware and VGIC DT separately; physically
   validate automatic time and identity behavior.
3. Demonstrate independent SPI recovery before proposing programming. Capture
   U-Boot NVMe file reads, then test matched NVMe-first boot and manual SD
   fallback. Review rollback and revalidate the authorized exact live write target.
4. Complete the physical gates listed above and reproduce deployment from the
   guide. Do not claim release completion until the [plan](hypervisor-plan.md)
   and [release acceptance](hardware-validation.md) pass.

## Detailed records

The [build reproduction index](build-reproduction-status.md) links component,
installer, update/recovery, candidate delivery, firmware, SPI, ROM and guest
construction records. Current installed-host management evidence is in
[repair results](installed-host-repair-results-20261108.md); retained deployment
instructions are in [the deployment guide](deploy-personalized-20261106.md).
