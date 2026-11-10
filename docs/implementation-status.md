# Implementation status

Updated 2026-11-10. Physical release acceptance and independent deployment
reproduction remain incomplete. The protected boot pair demonstrated a real KVM
workload with independent UART capture; the qualifying run is now stopped. The previous SSH failure remains
unexplained; it is not reclassified as a pass.

## Current installation

- **Target:** Orange Pi 4 Pro A733, observed 12 GiB RAM/eight CPUs; actual SD
  **1,977,614,336 bytes**, SU02G CID `035344535530324780716f216e00a8f5`;
  NVMe **256,060,514,304 bytes**, SPCC M.2 PCIe SSD, serial
  `134807190FC700149277`, firmware EDFM20.0, 512/512-byte sectors.
  Leave both attached heatsinks in place. Diagnostic inventory is sufficient.
- **Current maintenance session:** private SSH RAM recovery at `root@192.168.1.115`,
  using `HostKeyAlias=192.168.1.116` and the original pinned key. Boot
  `732ffcf6-d815-4ec0-80d5-02e9ecb69c96`; RAM root mode 0755, no swap or mounted
  NVMe filesystems. Complete 40-file recovery tools authenticate under
  `/run/a733-tools`. Namespace and p1–p5 are block-RO with idle/consumer checks.
  [Physical RAM entry](evidence/offline-preservation-ram-entry-20261109.json).
- **Preserved installation:** the prior protected host at `.116`, boot
  `dd8d6f67-d297-4f3b-bc9a-8a41c9742051`, used read-only NVMe p2 root and STATE
  UUID `360dfb39-f49f-487e-b307-0bb887c854c5`. Management, automatic NTP and both
  public SSH keys passed. Its roots, STATE and GUESTS remain intact offline.
- **Current SD:** the approved first-256-MiB write of private recovery image
  `d3a022…6bc3f6` completed with fsync/flush and booted physically. No installation
  readback was requested. Protected normal-host SD `ab5e8c…ad207` remains retained
  for restoration. NVMe BOOT `cc2ad9…26c313` and SPI are unchanged; firmware
  loading NVMe BOOT remains untested. [Images](current-images.md).
- **STATE protection:** Legacy serial captured orphan cleanup despite `ro,noload`.
  Reviewed boot/export changes enforce block RO before probing; four real ext4
  fixtures, including legacy orphans, preserve all source bytes/write counters.
  Protected host boot passes; physical dirty-journal recovery is still pending.
  [Evidence](evidence/state-block-protection-20261109.json).
  The [dirty-STATE trial design](dirty-state-recovery-trial.md) now passes
  [independent review](evidence/dirty-state-trial-design-20261109.json); live command bindings, verified backups, an actual crash
  journal and physical execution remain pending.
- **Serial:** `orangepi@192.168.1.117`, `ttyS3-capture.service`,
  `/var/log/ttyS3.log` (volatile). Native codex relay
  `a733-uart-relay-20261109-01.service` preserves output off-board.
  UART help, RAM-shell recovery and controlled RAM-installer reset pass.
  No GPIO wiring was added; a fully locked kernel remains outside SysRq proof.
  [Control procedure](remote-serial-control.md).

## Hardware workload and acceptance

The operator revised endurance to **eight hours (28,800 seconds)** and accepts
already collected qualifying data without rerunning. The [November 9 run](endurance-run-20261109.md)
began at 02:10:42 UTC with actual four-vCPU nonroot KVM, four additional nonroot
host workers, disk cycles and physical network transfers. Its 2,423 complete
telemetry samples reach guest elapsed **72,669 seconds** (20 h 11 min), cycle
2,422; original guest run UUID is `59af604a-cf2e-4c8f-9fd5-e26b8283d14c`.
The legacy guest remains historically configured for 86,400 seconds. The
coordinator deliberately stopped collector, guest and host workers after the
operator's change. No guest-generated `ENDURANCE_PASS` or clean guest filesystem
shutdown is claimed for this operator stop; separate functional guest shutdown,
checkpoint and restart evidence remains valid.

Both fixed outcome-preservation phases were explicitly captured immediately.
The obsolete November 10 02:16 timer is stopped and disabled. UART capture/relay remain live;
the A733 subsequently made one explicitly recorded RAM-maintenance reboot.
[Whole-data audit and post-workload checks](evidence/eight-hour-endurance-result-20261109.json)
now pass the revised eight-hour workload gate. Before the explicit maintenance
write, the post-workload complete SD hash matched its protected baseline and all
recorded workload write fields were zero. Final SMART had zero critical warnings/media errors,
with the existing 153 error-log entries and 77 unsafe shutdowns unchanged.
The historical [12-hour audit](evidence/endurance-prefix-12h-20261109.json) and
[preflight repair](evidence/endurance-journal-selector-repair-20261109.json) remain
retained with their original metadata. No new eight-hour test is required. Release acceptance remains incomplete.

| Gate | Evidence/status |
| --- | --- |
| Diagnostic image fits actual 2 GB SD; EL2 on all eight CPUs | Passed; vendor images were not used |
| Actual KVM CPU/disk/network, clean shutdown/checkpoint/restart | [Passed](kvm-vgic-results-20261108.md); corrected-host restoration advances 3 → 4 → 5 |
| Protected host boot, management, time and identity | Passed; current full release acceptance remains open |
| Eight-hour workload | [Passed using retained >20-hour data](evidence/eight-hour-endurance-result-20261109.json); no rerun |
| 10 cold and 10 warm boots with matched firmware/SSD | Pending; accidental user restarts and the controlled RAM reset are not counted |
| SPI-to-NVMe with SD removed | Unprogrammed; registered independent recovery and physical SPI/SD-removed boot pending |
| Immutable SD fallback | Qualifying workload writes were zero and its pre-maintenance full-card hash matched; [whole-card baseline](evidence/protected-sd-baseline-20261109.json) and exact written prefix match pass; [bounded firmware source audit](sd-firmware-write-policy.md) complete; final shutdown/reboot comparison and serial reconciliation pending |
| Interrupted update, rollback, missing NVMe, dirty journal | Software preparation/review pass; required physical recovery cases pending |
| Off-device guest backup/restoration | [Counter-3 physical restoration](evidence/corrected-host-guest-restore-20261108.json) passes; [current counter-5 off-device backup](evidence/current-counter5-backup-20261109.json) now passes durable receiver readback; new archive restore pending |
| Coherent normalized full image and independent deployment reproduction | [Two selected full streams match and pass independent artifact review](evidence/normalized-full-nvme-image-20261109.json); fresh installation and reproduced deployment pending |

The [previous release run](endurance-run-20261108.md) lost SSH at collector
elapsed 300.005 seconds, last valid guest elapsed 357/cycle 12. Later state was
unobserved before user restarts. Its guest directory is retained separately;
no clean shutdown or completed endurance is claimed. Reported power/cooling is a
67 W USB-PD adapter and 10.4 mm six-layer whole-board copper heatsink; negotiated
power is not measured. Thermal workload evidence is retained for review.

## Resolver policy finding

A [read-only listener audit](evidence/management-runtime-exposure-20261109.json)
found wildcard TCP/UDP 5355 listeners. The intended resolver drop-in disables
LLMNR/mDNS, but its newly created directory is mode 0700 under the boot umask,
so `systemd-resolve` cannot read it. The selected Ethernet link disables these
protocols; global policy is not applied. Root password is locked, SSH is key-only
and bound to the selected address, and the observed guest was UID/GID 60002 with
empty capabilities; these observations do not waive the resolver finding.
The creation-mode fix is now in [normalized roots](deterministic-host-roots.md)
with complete filesystem and raw/gzip repeat proof. The matched STATE selector
and [two complete full-image streams](evidence/normalized-full-nvme-image-20261109.json)
pass independent artifact review. The selected native candidate is
`out/normalized-nvme-candidate-20261109/`; it is unbooted. Physical policy/listener
verification remains required after installation; the installed host is
unchanged. Deployment review found the old identity helper recognized only its
predecessor's factory machine ID. A narrow two-factory fix passes independent
privileged focused review; full-suite revalidation and final guide review pass. Image bytes and root/STATE pairing are unchanged by this external helper.

## Construction, resources and authorization

- Protected SD and NVMe BOOT components each have two matching constructions
  and independent artifact review; their actual protected host boot passes.
  [Physical evidence](evidence/protected-state-maintenance-20261109.json).
- The selected normalized full NVMe candidate has two matching fresh-checkout
  raw/gzip constructions and complete independent GPT/hash/component checks.
  [Current evidence](evidence/normalized-full-nvme-image-20261109.json). Same-builder
  repetition is established; independent clean-builder/deployment reproduction
  and physical installation remain open.
- [Actual isolated NVMe composition](evidence/isolated-nvme-composition-20261109.json)
  now passes independent artifact/environment review: the complete 545,581,074-byte
  gzip matches the canonical target. Producer `81f907…` used the same held
  components in a fresh frozen-Ubuntu namespace. Frozen/source/host invariants
  and cleanup pass. The [initial bind-target repair](evidence/frozen-builder-anchor-repair-20261109.json)
  remains recorded separately. This is not a new OS/compiler rebuild or deployment.
- Latest complete privileged Ubuntu suite: **2,274 tests**, zero skips/errors/
  failures; five shell suites and five diagnostic assembly cases also pass.
  All five TOML files parse separately on codex. These are software checks.
- [Normalized maintenance candidates](normalized-maintenance-20261109.md) use
  root manifest `c1bc…` and pass independent artifact review plus offline old/new
  API checks. The separate [old A rollback pair](matched-host-updates-20261109.md)
  uses manifest `4f28…`, selector hash `bd27…` and selected old-A root marker
  `e1cc…`. A read-only audit matches the 40
  installed old maintenance files. Live staging/activation/rollback remain
  unperformed; preserve current guests and host state before maintenance.
- Native free space is now **513,011,712 bytes** after [byte-exact builder
  retention of ten validation source transfer archives](evidence/native-validation-source-retention-20261109.json)
  and retirement of their native copies (108,267,520 bytes). The unchanged
  256 MiB floor and planned 384 MiB outcome/scratch reserve remain. Canonical
  images, manifests and hardware evidence were retained; all 24 historical
  canonical raw/gzip files remain. Avoid another large native copy and keep
  bulk backups on the builder. Earlier [duplicate-transfer retirement](evidence/native-loose-transfer-retirement-20261109.json)
  remains recorded separately.
- Builder LV now **128,819,658,752 bytes**, VG free **4,265,607,168 bytes**;
  filesystem available after the reviewed additional 20 GiB growth is
  **43,622,555,648 bytes**. [Latest exact-target growth](evidence/isolated-composition-capacity-20261109.json)
  retains the LV/VG/filesystem UUIDs, ordinary linear LV and ext4 root mapping.
  It admits conservative component staging plus unchanged full-composer guards.
  All free capacity on this builder remains authorized for use/expansion without
  another permission request; revalidate exact targets and retain independent review.
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

1. Current counter-5 guest backup and [all three stopped endurance-directory
   forensic copies](guest-preservation-20261109.md) are verified off-device.
   The [offline compatible ROOT-A/ROOT-B/STATE cohort](evidence/offline-host-preservation-20261110.json)
   now passes full receiver verification and independent custody review. Keep its
   original archives immutable; physical paired restoration remains pending. The
   [warm-reboot collector](warm-boot-validation.md) is independently reviewed
   and prepared for the SD route; it remains unexecuted and refuses active
   workloads or missing retained outcome evidence. The operator-stop snapshot was
   captured after the transient collector unit was garbage-collected. A narrow
   [authenticated stop-receipt exception](evidence/warm-stop-admission-20261110.json)
   now passes independent review and the full Ubuntu suite (2,274 tests); live
   root/SD/idle/reboot guards remain unchanged. Bind the final root and boot route
   before execution. Count the final ten warm and
   ten cold boots only after choosing and installing the final release/boot route.
   An SD-removed SPI route needs corresponding collector adaptation. [Preparation evidence](evidence/warm-boot-preparation-20261109.json).
   Preserve kernel/control evidence on recurrence. Audit final guest/CPU exits, duration and post-run
   health; pending collector exit 2 alone is not acceptance.
2. After retaining the workload outcome and backups, run the
   [changed inactive-slot interruption and manual rollback cases](normalized-maintenance-20261109.md)
   on the current old host. Both normalized candidates now pass independent
   actual-artifact review, including offline old/new API compatibility. Verify
   live guards and the actual inactive marker first; physical execution is pending.
   Then bind the [prepared full-deployment procedure](deploy-protected-20261109.md)
   to the final verified normalized distribution and reproduce its fresh install.
   The earlier protected-image preflight remains historical; do not substitute
   its older image for the final corrected distribution. Preserve same-board
   identity, current guests and prior failure evidence before whole-drive erasure.
   [Offline STATE/root preservation](host-state-backup.md) is prepared; physical
   backup now passes, while [paired STATE/root restoration](known-host-state-restore.md) remains
   a physical gate; the external controller now passes independent review and
   the full Ubuntu suite without changing installed image bytes.
3. Independently review the final normalized deployment bindings and preflight
   before physical installation; both full streams now pass complete artifact
   verification. Actual fresh installation and deployment reproduction are open.
   Preserve current BOOT; verify the policy physically after retaining endurance
   evidence. The [temporary missing-NVMe driver trial](missing-nvme-recovery-test.md)
   now passes independent procedure review; faulted serial recovery, normal
   recovery boot and a KVM guest remain unperformed for that case.
4. Prepare SPI recovery/programming and matched NVMe-first firmware trials.
   The [matched trial SD](matched-nvme-trial-sd-20261109.md) now has two identical
   constructions and independent complete artifact review; it preserves current
   protected BOOT and SD boot0. Its finite-profile
   [SD writer](known-bootstrap-sd-writer.md) now passes independent review,
   the complete Jammy suite and live read-only target/census checks. Physical
   maintenance and NVMe firmware handoff remain pending until workload evidence
   is retained. [Writer evidence](evidence/known-sd-trial-writer-preparation-20261109.json).
   The same writer now has an explicitly selected private
   [SSH RAM recovery profile](evidence/known-sd-recovery-profile-20261109.json)
   for the later full-install transition; it does not change the protected default.
   The [transaction API](spi-unit-transaction.md) passes independent review and
   actual-input NOR-model rehearsals; it supplies no device-opening route.
   [Preparation evidence](evidence/spi-unit-transaction-20261109.json).
   A [borrowed-FD Linux MTD backend](spi-mtd-adapter.md) now passes independent
   review and the full privileged Jammy suite. It has no CLI or device opening.
   The [trusted programmer/capture preparation](known-spi-programmer.md) now passes
   independent review and 2,218 Ubuntu tests; actual writes remain disabled until
   independent physical recovery evidence is registered.
   Otherwise finish complete immutable-SD proof. The blank XM25QU128C SPI backup
   and successful SD boot do not alone prove recovery after broken SPI firmware.
5. Complete the remaining physical gates in [hardware validation](hardware-validation.md)
   and the [plan](hypervisor-plan.md). Finish only after acceptance and reproduced
   deployment, preserving the earlier observation gaps.

Detailed history is indexed by [build reproduction status](build-reproduction-status.md),
[host assessment](build-host-assessment.md) and the linked evidence records.
