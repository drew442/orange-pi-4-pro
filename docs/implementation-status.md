# Implementation status

Updated 2026-11-13. **Goal paused at the operator’s request.**
[Pause handoff](evidence/goal-pause-and-restoration-20261113.json).

**SPI/NVMe boot works with SD removed; managed production
qualification is incomplete.** The shutdown-repaired B is running with SD absent. The preceding
`6c0a7e9` B passed actual managed UID60002 KVM CPU/disk/HTTP, then hit a QMP
shutdown deadline and was durably quarantined. Forced cleanup returned raw exit 0;
this does not prove guest-owned clean shutdown. Supported recovery retained the failed cohort,
committed transaction 4671, left generation21 ready0, and preserved manual CP5.
The independently reviewed timer move after `mark_stopping` is frozen at
`fb29af4`. Focused 32 tests and combined Ubuntu validation pass (3737 unit, five
shell, five diagnostic; 1179 source locks and exact849 package inventory
unchanged). One new shutdown-budget construction and independent artifact verification pass:
133 hashes, two gzip/raw comparisons, both filesystems and five readers. Inactive-B installation/readback and authenticated physical boot pass; actual
guest-owned shutdown and raw exit0 are observed; terminal socket-path validation
then fails and quarantines before checkpoint. Managed checkpoint/restart remain pending.
[Deployment](evidence/shutdown-budget-B-deployment-20261113.json).
[New artifacts](evidence/shutdown-budget-root-trial-and-artifact-review-20261113.json).
External helper pins pass independent data-only review,84 privileged focused
checks and fresh combined validation (3737 unit/five shell/five diagnostic,
zero skips/failures,1184 source locks and exact849 inventory unchanged).
[Helper validation](evidence/shutdown-collector-combined-validation-20261113.json). These four helpers are outside the
packaged 85-file runtime. [Binding](evidence/shutdown-artifact-helper-pin-binding-20261113.json).
Final managed warm/cold counts are **0/0**; release remains pending.
[Combined validation](evidence/shutdown-budget-combined-validation-20261113.json),
[managed KVM trial](evidence/permission-B-managed-KVM-and-shutdown-failure-20261113.json),
[shutdown failure/recovery](evidence/managed-shutdown-failure-recovery-20261113.json).

## Current board and custody

- SSH `root@192.168.1.118`; strict original host-key alias `192.168.1.116`.
  Running boot `14d3fd72-4cbe-4b70-96fe-6ade66b30ace`; corrected B p3 is
  read-only. Complete 85-tool/four-management/two-boot-unit authentication and NTP
  pass. Actual UID 60002 native launcher metadata authentication passes. The
  disposable guest was restored through supported transaction `9d4fd8d5…9f30`:
  generation24 disabled, head ready0, admission shutdown-closed; a fresh boot is
  required before further guest work. Failed cohorts and manual CP5 remain.
  Actual KVM CPU/disk0→1/HTTP and guest-owned shutdown/raw exit0 were observed;
  terminal socket-path validation prevented checkpoint. The reviewed repair is
  committed as `2675847`, but is not built or installed. Full Ubuntu validation
  was still running when paused; leave it to finish and inspect on resume.
- Preserve A, manual `firstkvm` and CP5, all failed cohorts, recovery SD and SPI
  backups. CP5 manifest `88c1ea17…9c946`, disk `2d06d539…5b66`; verified backup
  is on codex and builder. Normalized A custody details are in
  [the recovery record](evidence/managed-shutdown-failure-recovery-20261113.json).
- Hardware: Orange Pi 4 Pro A733; nominal 250 GB NVMe, 2 GB recovery SD
  (SU02G, 1977614336 bytes) physically removed. Leave attached heatsinks in
  place. NVMe identity and UUIDs, UART capture, power, and SPI geometry/calibration
  are recorded in the linked physical evidence. Preserve original, factory,
  calibrated and current SPI backups; the latest full snapshot predates later
  warm boots and is not a fresh programming input. Any programming needs a fresh
  independently reviewed metadata-aware live-flash plan. See
  [SPI policy](spi-calibration-policy.md) and
  [procedure](spi-sd-boot-20261111.md).
- NVMe: SPCC M.2 PCIe SSD, serial `134807190FC700149277`, 256060514304 bytes.
  STATE p4 UUID `360dfb39-f49f-487e-b307-0bb887c854c5`;
  guest p5 UUID `298b281a-d10e-44eb-bdb9-285e27eaafad`.
- SPI: XM25QU128C, JEDEC `204118`, `/dev/mtd0` char90:0, 16777216 bytes,
  erase65536; vendor calibration changes only `[260096,262144)`.
- UART: `orangepi@192.168.1.117`, `ttyS3-capture.service`, `/var/log/ttyS3.log`;
  capped native relay is under `out/serial-recovery-20261109/`.
- Supply: reported67W USB-PD, passive10.4mm six-layer copper cooling;
  negotiated voltage/current is unmeasured. The November8 SSH failure remains
  unexplained; retain continuous UART during qualification.

## Acceptance evidence and boundaries

| Area | Verified result and boundary |
| --- | --- |
| Earlier protected-SD release | Installation, update/rollback/recovery, independent deployment reproduction, ten cold/ten warm boots and unchanged SD passed for that configuration. This is not managed SPI release. [Audit](evidence/release-acceptance-20261111.json). |
| Endurance | Retained >20-hour workload satisfies the eight-hour criterion. Do not rerun; cap future planned tests at eight hours. Production guest lifetime remains unlimited. |
| SPI and recovery | SD-removed cold/warm boot; manual nonroot KVM CPU/disk/HTTP, shutdown and checkpoint/restart; blank restoration, SD recovery and metadata-aware reinstall/readback/close passed. [Operations](evidence/spi-operational-20261111.json), [restoration](evidence/spi-blank-restoration-and-sd-recovery-20261113.json), [reinstall](evidence/spi-reinstall-and-poweroff-20261113.json). |
| Manual CP5 | Post-reinstall boot `ae6ef902…0eded`; UID/GID60001 `firstkvm` 9→10 passed CPU/disk/HTTP and guest-owned raw exit0. CP5 is durable with nine history records and verified codex/builder copies. Subsequent CP5 restart is not claimed. [Receipts](evidence/post-reinstall-SPI-managed-B-20261113.json). |
| Managed deployments and failures | Original B and repaired `4334a22` staged, selected and booted; A remains rollback root. Earlier launch failures and permission repair are documented. The preceding corrected `6c0a7e9` managed execution passed KVM CPU/disk/HTTP, but shutdown failed and quarantine followed; no clean managed shutdown credit. [Deployment](evidence/repaired-managed-B-deployment-20261113.json), [failure/recovery](evidence/managed-shutdown-failure-recovery-20261113.json). |
| Current managed shutdown repair | `fb29af4` timer fix independently reviewed and frozen. Host validation is green, but the new root construction/artifact review and actual hardware shutdown, durable checkpoint, explicit restart and automatic startup remain pending. No final managed boot-series credit. |

## Retained evidence and implementation state

The corrected managed trial exposed the QMP deadline, retained its failed
cohort, then used supported recovery without changing manual CP5. Keep the
unused original port80 cohort and failed port8080 cohort. Earlier failures
include pre-queue systemd dependency edges; child exit125 before QEMU during
permit publication; four F349 exit1/74-byte console failures before QMP bind
(`Permission denied: 'orangepi-hypervisor'`); and an earlier supervisor cleanup
error. Their retained records remain authoritative; none earns execution credit.

The permission fix publishes public metadata directory/marker as 0755/0644
before both snapshots. Earlier collector mtime fixture correction and F349
permit-publication/child-barrier and native bounded-stop work are frozen history,
not current hardware acceptance. F349 and test-fixture chronology, per-trial
hashes, artifact counts, disk measurements and earlier validation totals are
preserved in the linked records.
[F349 source review](evidence/managed-bound-stop-and-fixture-completion-20261113.json),
[collector correction](evidence/collector-mutation-fixture-correction-20261113.json),
[older combined validation](evidence/permission-root-combined-validation-20261113.json).

Eight old producer raws were retired only after review; verified native raw and
gzip copies and all builder gzips remain. Last fresh preparation admission had
19,588,169,728 bytes free versus 19,084,036,631 required, and
7,493,877,760 bytes MemAvailable. Recheck guards for every construction. Preserve
all new roots, A and backups. [Retirement and retention](evidence/old-producer-raw-retirement-20261113.json).

Builder is Ubuntu 22.04 with exact849 package lock and signed 17-package
supplement; historical 842-package replay inputs remain unchanged. Reviewed
scratch retirement and current capacity are documented in
[builder assessment](build-host-assessment.md). Preserve source/license limits:
exact inventory remains incomplete for 26 vendor boot inputs and two Linaro
revisions; local qualification does not establish public redistribution
clearance. [Source inventory](toolchain-sources.md),
[current images](current-images.md).

## Next actions and managed SPI release gates

Follow the [production readiness plan](spi-production-readiness-plan.md):

1. After the operator resumes, inspect the running validation’s actual result.
   Complete reviewed capacity/custody preparation, fresh root construction and
   artifact checks, then deploy and prove stopped outcome/checkpoint/restart.
   Restore07 requires a reviewed fresh boot first; do not reopen RAM admission.
2. On hardware, prove guest-owned power-key shutdown, durable checkpoint,
   explicit restart, and separately automatic startup using an actual KVM guest.
3. Exercise coherent completed-cohort backup/export/restore,
   interrupted/resumed restoration, disable, installed maintenance
   interruption/retry, restored old-A rollback and managed-B return. Preserve
   `firstkvm`, CP5 and all failure evidence.
4. Independently reproduce frozen construction and compare all artifacts; freeze
   final collectors and setup after the new trial.
5. Pass ten qualifying warm and ten cold SPI boots with automatic guest
   outcomes, ordered UART and current firmware/storage identity. Cold transitions
   need real operator USB-C removal attestations; credit only applicable boots.
6. Independently reproduce the final physical procedure, reconcile
   [release acceptance](release-acceptance.md), then commit and push validated
   changes. Managed warm/cold credit remains 0/0 until earned.

Known-board SD/NVMe/SPI writes are authorized by
[the persisted record](evidence/storage-write-authorization-20261108.json).
Revalidate exact targets and use independent review. Free builder capacity may
be used/expanded after exact-target revalidation; native codex LVM is outside
that approval. Keep resource guards. Installation shows progress and bulk
readback stays optional; older installed A updater still requires legacy
readback, and selector authentication is mandatory. Do not benchmark
installation, remove heatsinks or rerun accepted endurance testing.
