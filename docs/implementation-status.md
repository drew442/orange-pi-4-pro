# Current status

Updated 14 November 2026 (UTC). **Manual-beta is not released.**
The operator resumed the hardware goal from merged PR #1 `e4d3943`.
Source fix `990fd09` is pushed to main; it is not built or deployed.
Validation timeout correction `7325069` is pushed to main. Final-byte CI passed
221 stages (3,011 executed; 817 declared exclusions); native checks are running.
The previous coordinator is inactive. Standing known-target authorization remains
valid; no permission renewal is needed for those targets.

## Current hardware and custody

- The board runs the constructed `e4d3943` **manual-beta B**, SPI/NVMe with SD
  absent, boot `73c57537-bc17-4230-85f0-87561ae0499a`. All 85 maintenance,
  four management and two boot-unit files match the root manifest. Dispatch is
  masked; storage preparation opened admission normally after the supported
  fresh boot. No RAM admission metadata was manually edited.
- Normalized A remains intact and was booted read-only during cutover.
  Recovery SD, original/calibrated SPI backups, failed cohorts and the off-device
  firstkvm CP5 backup remain preserved.
- `firstkvm` is stopped, MainPID0/result success, at durable **CP7**; manifest
  `e8cef8118bc3e192c3d03ef60c2a04d6f38ca67e39b81b2a65b75215b2faaf8e`.
  Its RAM unit is regenerated for CP7 and not enabled for boot.
- The old `2675847` builder validation finished successfully and was retained,
  not restarted. Current construction/readback jobs finished successfully;
  the first native validation job finished with failures described below.
  Corrective native validation is running as
  `a733-manual-beta-native-20261114-02.service` on the qualified builder, with
  final source `7325069`, output `out/validation/manual-beta-20261114-02`.
  Retain this job rather than restarting on session change.

[Resumption and hardware receipt](evidence/manual-beta-resumption-20261114.json).
[Current artifacts](current-images.md). The unchanged
[pause receipt](evidence/goal-pause-and-restoration-20261113.json) remains the
historical restore/custody input; its managed resume list is superseded.

## Finite gates

| Gate | Actual result and remaining action |
| --- | --- |
| Source | PR #1 merged; reviewed boot-lock fix `990fd09` pushed, pending construction |
| Software | Merged-source CI passed 221 stages/3,007 executed tests. Native candidate validation did **not** pass: restore-transaction suite timed out at 240 seconds; SPI retained-fixture test was unavailable. The interrupted restore case separately passed in 19.361 seconds. The runner now grants only that complete suite a bounded 1200 seconds; all other stages retain 240. Four retained SPI fixtures were transferred and hash-verified. Final-byte CI passed 221 stages (3,011 executed; 817 declared exclusions); native validation is running, with no corrected native pass claimed yet. Lock fix: 25 native fixtures and independent review passed; CI passed with recorded early-stage fingerprint limitation |
| Construction | One `manual-beta-20261114-01` A/B construction passed, plus separate full filesystem readback. A second construction is justified solely by the observed boot-lock defect; it has not started |
| Deployment | B was written/read back through the installed updater and selected/booted through the supported selector; A, BOOT, SPI and guest partitions were preserved |
| Manual lifecycle | Real UID60001 KVM CPU/disk/HTTP, guest-owned shutdown, CP6, restart/persistence counter11→12 and clean shutdown/CP7 passed using the existing guarded account-preparation workaround. Final repaired startup must create the manual lock automatically; assess lifecycle reuse by dependencies |
| SPI boots | Prior SPI observations retained; this run added three warm SPI/NVMe boots, including the manual profile. A provisional pinned ledger supports two cold and three warm observations; one cold boot and observation of the repaired final profile remain. Final applicability awaits repaired runtime/composed BOOT; protected-SD boots remain excluded |
| Recovery | Applicable SPI recovery, host rollback and guest backup/restore evidence retained; no new fault campaign or soak |
| Whole-NVMe/STATE and delivery | **BLOCKED on builder capacity**; not constructed, packaged, tagged, privately delivered or publicly published |

## Exact blocker and next action

Qualified builder `build.drewnet.online` has **18,219,798,528 bytes free** and
zero free LVM extents. The supported fresh-STATE compositor requires **more than
44,845,873,778 bytes**, before additional root-manifest/configuration/key bytes.
The independently reviewed exact eight-file duplicate-raw retirement is complete,
with protected, verified off-builder raw/gzip custody. No further deletion is
selected; preservation and admission guards were not weakened.

**Operator action: expand this builder's disk, partition, LV and filesystem by
40 GB**, or provide an existing authorized expansion method. Recheck live free
space afterward. Retain the current jobs/results and construct from the reviewed
fix; do not reconstruct the successful unchanged kernel/toolchain.

Then close only these gaps: final-byte native validation (provide retained SPI
fixtures and a justified finite allowance for the slow restore suite), repaired
root construction and fresh-boot lock/normal-launch observation, matching STATE/
NVMe composition, boot-ledger shortfall, acceptance records and versioned private
package/public eligible prerelease. Reuse applicable successful evidence. Do not
resume automatic managed lifecycle, another soak or the archived ten-cycle plan.
