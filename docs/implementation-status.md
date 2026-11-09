# Implementation status

Updated 2026-11-09. Physical release acceptance and independent deployment
reproduction remain incomplete. The protected boot pair now runs a real KVM
workload with independent UART capture. The previous SSH failure remains
unexplained; it is not reclassified as a pass.

## Current installation

- **Target:** Orange Pi 4 Pro A733, observed 12 GiB RAM/eight CPUs; actual SD
  **1,977,614,336 bytes**, SU02G CID `035344535530324780716f216e00a8f5`;
  NVMe **256,060,514,304 bytes**, SPCC M.2 PCIe SSD, serial
  `134807190FC700149277`, firmware EDFM20.0, 512/512-byte sectors.
  Leave both attached heatsinks in place. Diagnostic inventory is sufficient.
- **Host:** `root@192.168.1.116`, boot
  `dd8d6f67-d297-4f3b-bc9a-8a41c9742051`; pinned Ed25519 SSH, read-only NVMe p2
  root, STATE UUID `360dfb39-f49f-487e-b307-0bb887c854c5`, authenticated 40-file
  tool closure and automatic NTP pass. Both public keys are retained, accessible
  from any subnet; fresh RSA login is not repeated.
- **Boot update:** Reviewed 256 MiB protected SD and 512 MiB NVMe p1-only writes
  pass; roots, STATE and GUESTS were retained. Live original BOOT backup is
  authenticated off-device. No installation readback was requested.
  Firmware currently loads kernel/DTB/initramfs from SD and root from NVMe.
  Matching NVMe BOOT files are installed; firmware loading them is untested.
  [Physical receipts](evidence/protected-state-maintenance-20261109.json),
  [current images](current-images.md).
- **STATE protection:** Legacy serial captured orphan cleanup despite `ro,noload`.
  Reviewed boot/export changes enforce block RO before probing; four real ext4
  fixtures, including legacy orphans, preserve all source bytes/write counters.
  Protected host boot passes; physical dirty-journal recovery is still pending.
  [Evidence](evidence/state-block-protection-20261109.json).
- **Serial:** `orangepi@192.168.1.117`, `ttyS3-capture.service`,
  `/var/log/ttyS3.log` (volatile). Native codex relay
  `a733-uart-relay-20261109-01.service` preserves output off-board.
  UART help, RAM-shell recovery and controlled RAM-installer reset pass.
  No GPIO wiring was added; a fully locked kernel remains outside SysRq proof.
  [Control procedure](remote-serial-control.md).

## Hardware workload and acceptance

The [fresh 24-hour run](endurance-run-20261109.md) began **2026-11-09 02:10:42 UTC**:
actual four-vCPU nonroot KVM, four additional nonroot host workers, disk cycles,
physical network transfers, temperatures, memory, NVMe kernel errors and exact
SD write counters. Guest run UUID is `59af604a-cf2e-4c8f-9fd5-e26b8283d14c`.
Completion is pending, no earlier than approximately **November 10 02:11 UTC**.
The endpoint was recorded at elapsed zero; first full telemetry is elapsed
10 seconds. Do not reboot the A733, UART bridge or codex during this test.

Native collector `a733-offboard-endurance-collector-20261109.service` and the
UART relay are independent of this chat. Reviewed read-only outcome preservation
is armed on collector exit and at **November 10 02:16 UTC**, with separate private
snapshots. It never grants acceptance; coordinator review of console, exact exit
records, duration, health and telemetry remains mandatory.

| Gate | Evidence/status |
| --- | --- |
| Diagnostic image fits actual 2 GB SD; EL2 on all eight CPUs | Passed; vendor images were not used |
| Actual KVM CPU/disk/network, clean shutdown/checkpoint/restart | [Passed](kvm-vgic-results-20261108.md); corrected-host restoration advances 3 → 4 → 5 |
| Protected host boot, management, time and identity | Passed; current full release acceptance remains open |
| 24-hour workload | Running; no final pass claimed |
| 10 cold and 10 warm boots with matched firmware/SSD | Pending; accidental user restarts and the controlled RAM reset are not counted |
| SPI-to-NVMe with SD removed | Unprogrammed; independent recovery and reviewed programmer prerequisites pending |
| Immutable SD fallback | Linux writes remain zero; [whole-card baseline](evidence/protected-sd-baseline-20261109.json) and exact written prefix match pass; post-workload/shutdown/reboot comparison and firmware audit pending |
| Interrupted update, rollback, missing NVMe, dirty journal | Software preparation/review pass; required physical recovery cases pending |
| Off-device guest backup/restoration | [Counter-3 backup and physical restoration](evidence/corrected-host-guest-restore-20261108.json) pass |
| Coherent protected full image and independent deployment reproduction | [Two full streams match and pass independent artifact review](evidence/protected-full-nvme-image-20261109.json); fresh installation and reproduced deployment pending |

The [previous release run](endurance-run-20261108.md) lost SSH at collector
elapsed 300.005 seconds, last valid guest elapsed 357/cycle 12. Later state was
unobserved before user restarts. Its guest directory is retained separately;
no clean shutdown or completed endurance is claimed. Reported power/cooling is a
67 W USB-PD adapter and 10.4 mm six-layer whole-board copper heatsink; negotiated
power and full thermal endurance remain unproven.

## Construction, resources and authorization

- Protected SD and NVMe BOOT components each have two byte-matching constructions
  and independent actual-artifact review. Two fresh full NVMe streams now match
  byte for byte; complete independent old/new comparison, GPT/framing/hash
  inspection and unchanged outside-BOOT bytes pass. Native candidate is
  `out/protected-nvme-candidate-20261109/nvme.img.gz`; fresh installation is
  unperformed. [Evidence](evidence/protected-full-nvme-image-20261109.json).
- Latest complete privileged Ubuntu suite: **2,065 tests**, zero skips/errors/
  failures; five shell suites and five diagnostic assembly cases also pass.
  All five TOML files parse separately on codex. These are software checks.
- Builder LV now **94,459,920,384 bytes**, VG free **38,625,345,536 bytes**;
  free immediately after growth **25,303,740,416 bytes**. The operator approves
  **all free capacity on the Ubuntu build host for use/expansion without another
  permission request**. Revalidate exact targets and retain independent review.
  [Growth evidence](evidence/build-host-capacity-addition-20261109.json).
- Full-NVMe admission remains **23,760,999,827 free bytes**; runtime floors remain
  **3,000,000,000 disk bytes / 1,000,000,000 available memory bytes**. Start
  compilation with two jobs; retain generated files and sources outside Git.
- Known board SD/NVMe/SPI writes remain authorized. Revalidate the exact target
  and concrete plan; ask yes/no only for unapproved targets/scope. SPI recovery
  prerequisites remain mandatory. Normal installation uses one write with
  progress; readback is optional. Do not benchmark hardware to diagnose install
  duration or remove heatsinks.
- Licensing/source inventory remains incomplete for 26 vendor boot inputs;
  exact Linaro GCC/DejaGNU sources remain unacquired. No public binary
  redistribution clearance is claimed. [Inventory](toolchain-sources.md).

## Next actions

1. Observe the new 24-hour run and serial capture. The
   [warm-reboot collector](warm-boot-validation.md) is independently reviewed
   and prepared; it remains unexecuted and refuses active workloads or missing
   retained outcome evidence. [Preparation evidence](evidence/warm-boot-preparation-20261109.json).
   Preserve kernel/control evidence on recurrence. Audit final guest/CPU exits, duration and post-run
   health; pending collector exit 2 alone is not acceptance.
2. After the workload, reproduce installation from the verified coherent full
   stream with protected SD, retain same-board identity and required guest
   backups, and complete independent deployment review. Keep the running
   workload undisturbed until its evidence is retained.
3. Prepare SPI recovery/programming and matched NVMe-first firmware trials.
   Otherwise finish complete immutable-SD proof. The blank XM25QU128C SPI backup
   and successful SD boot do not alone prove recovery after broken SPI firmware.
4. Complete the remaining physical gates in [hardware validation](hardware-validation.md)
   and the [plan](hypervisor-plan.md). Finish only after acceptance and reproduced
   deployment, preserving the earlier observation gaps.

Detailed history is indexed by [build reproduction status](build-reproduction-status.md),
[host assessment](build-host-assessment.md) and the linked evidence records.
