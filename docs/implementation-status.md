# Current status

Updated 14 November 2026 (UTC). **Manual-beta is not released.**
The operator resumed the hardware goal from merged PR #1 `e4d3943`.
The reviewed boot-lock fix is built in `manual-beta-20261114-02` from main
`8f311c4`; deployment is underway.
Validation timeout correction `7325069` is pushed to main. Final-byte CI passed
221 stages (3,011 executed; 817 declared exclusions); native checks passed all 3,828 tests without exclusions.
The previous coordinator is inactive. Standing known-target authorization remains
valid; no permission renewal is needed for those targets.

## Current hardware and custody

- The board is cleanly **powered off**, with repaired manual-beta B selected
  durably for the next boot. UART confirmed STATE unmount, all filesystems
  unmounted and kernel power down. The operator has been asked to disconnect
  USB-C for at least 10 seconds and reconnect with SD absent for the remaining
  cold observation. Do not issue another poweroff or infer that boot passed.
- Normalized A remains intact and was booted read-only during cutover.
  Recovery SD, original/calibrated SPI backups, failed cohorts and the off-device
  firstkvm CP5 backup remain preserved.
- `firstkvm` is stopped, MainPID0/result success, at durable **CP7**; manifest
  `e8cef8118bc3e192c3d03ef60c2a04d6f38ca67e39b81b2a65b75215b2faaf8e`.
  Its RAM unit is regenerated for CP7 and not enabled for boot.
- The old `2675847` validation was retained. Final-byte CI and native validation
  of source `7325069` both passed: 221 stages, 3,828 native tests, no native
  exclusions. [Software receipt](evidence/manual-beta-software-validation-20261114.json).
  Validation jobs are terminal and retained. The repaired root construction and
  full readback passed. STATE/NVMe composition is running as
  `a733-manual-beta-composition-20261114-02.service`, source `8f311c4`, fresh
  checkout `/var/lib/a733-manual-beta-compose-project-20261114`, phase
  `/var/lib/a733-clean-replay-20261106/isolated-nvme-manual-beta-20261114-02`.
  The first composition attempt refused cached Python bytecode during preflight,
  before construction; its failure is retained. Do not restart the current job.
- The operator enlarged the backing disk. Reviewed guest-only partition/PV/LV/
  ext4 growth used all 33 GiB added capacity; Hyper-V was untouched. Root LV is
  168,518,746,112 bytes, VG free zero, filesystem available about 51.77 GB.
  [Growth receipt](evidence/builder-disk-growth-20261114.json).


[Resumption and hardware receipt](evidence/manual-beta-resumption-20261114.json).
[Current artifacts](current-images.md). The unchanged
[pause receipt](evidence/goal-pause-and-restoration-20261113.json) remains the
historical restore/custody input; its managed resume list is superseded.

## Finite gates

| Gate | Actual result and remaining action |
| --- | --- |
| Source | PR #1 merged; reviewed boot-lock fix included in constructed source `8f311c4` |
| Software | PASS: final-byte CI 221 stages/3,011 executed/817 declared exclusions; native 221 stages/3,828 executed/no exclusions. Original timeout and missing-fixture results retained; bounded allowance and authenticated fixtures resolve them. Boot-lock fix independently reviewed |
| Construction | Repaired `manual-beta-20261114-02` A/B construction and full filesystem readback passed. This replaces the first candidate only for its observed boot-lock defect; no further root rebuild is planned |
| Deployment | Repaired B written/read back and selected durably through the installed tools; A, BOOT, SPI and guests preserved. Board powered off awaiting the operator's cold power cycle |
| Manual lifecycle | Real UID60001 KVM CPU/disk/HTTP, guest-owned shutdown, CP6, restart/persistence counter11→12 and clean shutdown/CP7 passed using the existing guarded account-preparation workaround. Final repaired startup must create the manual lock automatically; assess lifecycle reuse by dependencies |
| SPI boots | Prior SPI observations retained; this run added three warm SPI/NVMe boots, including the manual profile. A provisional pinned ledger supports two cold and three warm observations; one cold boot and observation of the repaired final profile remain. Final applicability awaits repaired runtime/composed BOOT; protected-SD boots remain excluded |
| Recovery | Applicable SPI recovery, host rollback and guest backup/restore evidence retained; no new fault campaign or soak |
| Whole-NVMe/STATE and delivery | Matching STATE/NVMe composition running; not yet packaged, tagged, privately delivered or publicly published |

## Bounded next action

Capacity is resolved with operator-authorized guest growth; no further deletion
or Hyper-V modification is selected. Reuse the successful final-byte software
checks and unchanged frozen builder/kernel/toolchain inputs.

Construct the reviewed boot-lock repair once, then perform the supported inactive-B
update through preserved A and a fresh boot proving automatic manual-lock creation
and normal launch. Compose matching STATE/NVMe data once. Finalize dependency
reuse and the boot ledger (one cold observation remains), then package/deliver the
versioned private beta and publish eligible public artifacts. No new soak,
automatic managed lifecycle or archived ten-cycle campaign.
