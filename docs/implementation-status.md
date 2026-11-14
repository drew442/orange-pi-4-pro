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

- The board runs repaired **manual-beta B** from SPI/NVMe, SD absent, cold boot
  `a97b2d62-52d1-4dbd-8d34-fc6f1d629058`. Its 85+4+2 runtime files authenticate,
  dispatch is masked, admission opened through boot preparation, and the manual
  `firstkvm` lock was created automatically. No RAM admission edits or account
  preparation workaround was used on this boot.
- Normalized A remains intact and was booted read-only during cutover.
  Recovery SD, original/calibrated SPI backups, failed cohorts and the off-device
  firstkvm CP5 backup remain preserved.
- `firstkvm` is stopped, MainPID0/result success, at durable **CP8**; manifest
  `17f21eeefef9eea79cad1410ff657803ebe55943074a28c216b6f14dade5b123`.
  Its RAM unit is regenerated for CP8 and not enabled for boot. Normal UID60001
  KVM CPU/disk/HTTP, persistence12→13, guest-owned shutdown and checkpoint passed.
- The old `2675847` validation was retained. Final-byte CI and native validation
  of source `7325069` both passed: 221 stages, 3,828 native tests, no native
  exclusions. [Software receipt](evidence/manual-beta-software-validation-20261114.json).
  All construction and validation jobs are terminal and successful. Matching
  STATE/NVMe composition `a733-manual-beta-composition-20261114-02.service`
  passed full stream/component verification from the same built source `8f311c4`.
  Its initial preflight refusal of cached test bytecode is retained; no actual
  construction ran in that refused attempt.
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
| Deployment | PASS: repaired B written/read back, selected durably and physically cold-booted; normalized A, BOOT, SPI and guests preserved |
| Manual lifecycle | PASS with applicability reuse: complete CP5→CP6→restart/CP7 lifecycle retained; final repaired cold boot automatically created the lock and normally restarted CP7 to counter13, then cleanly checkpointed CP8. Only bootstrap lock creation/package metadata changed; launch/checkpoint/configuration/QEMU/storage dependencies match |
| SPI boots | PASS: three cold and three warm in total, with compatible prior credit and repaired manual profile. BOOT matches the retained physical proof; protected-SD observations excluded |
| Recovery | Applicable SPI recovery, host rollback and guest backup/restore evidence retained; no new fault campaign or soak |
| Whole-NVMe/STATE and delivery | Whole image constructed/verified; acceptance records ready. Versioned packaging, private delivery and eligible public prerelease are pending |

## Delivery next action

Finite runtime/construction gates are accepted; see
[hardware acceptance/reuse](evidence/manual-beta-hardware-acceptance-20261114.json)
and [v0.1.0-beta.1 notes](releases/v0.1.0-beta.1.md). Assemble the accepted private
package through the implemented packager, deliver through the existing authorized
SSH channel, and publish eligible public notes/checksums as a GitHub prerelease.
Images remain private because of personalization and unresolved vendor grants.
Do not rebuild, rerun the successful gates, restart jobs or grow release scope.
