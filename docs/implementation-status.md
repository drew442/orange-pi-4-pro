# Current status

Updated 14 November 2026 (Sydney), incorporating main `6d8fdb7`.
**Target: Orange Pi 4 Pro SPI/NVMe manual-management beta. Not yet released.**
The physical development goal is paused. This repository change does not deploy
an image, resume a hardware job, or authorize bypassing a closed admission gate.

## Last recorded physical state

- SPI/NVMe boot with SD removed works; manual nonroot KVM and checkpoint/restart
  have applicable prior evidence. [SPI operation](evidence/spi-operational-20261111.json).
- Installed root B is `fb29af4`; the reviewed terminal-socket repair `2675847`
  is in source but was not built or installed when paused.
- Supported restore07 completed: registry generation **24**, guest **disabled**,
  head **ready sequence 0**, admission **closed: shutdown**. A fresh boot is
  required before further guest operations. Never reopen RAM admission manually.
- Manual `firstkvm` CP5, normalized A, failed cohorts, recovery SD and original/
  calibrated SPI backups are preserved. CP5 restart is not yet claimed.
- A bounded builder validation job for `2675847` was running at pause. Its result
  is unknown here. Inspect its terminal receipt once if relevant; do not rerun it
  simply because the coordinator session changed.

[Pause/restoration receipt](evidence/goal-pause-and-restoration-20261113.json)
retains exact identifiers, pins and job name. Its old managed-release resume
list is superseded by [beta scope](beta-scope.md).

## Acceptance and remaining work

| Area | State | Next action |
| --- | --- | --- |
| SPI boot, recovery and manual KVM foundation | DONE for recorded configurations | Reuse [SPI](evidence/spi-operational-20261111.json), [recovery](evidence/spi-blank-restoration-and-sd-recovery-20261113.json), [reinstall](evidence/spi-reinstall-and-poweroff-20261113.json) evidence by dependency |
| Long-running workload | DONE; no new endurance gate | Retain [accepted observations](evidence/eight-hour-endurance-result-20261109.json) |
| Host rollback and stopped-guest restoration | DONE for recorded configurations | Recheck only affected paths; see [maintenance](maintenance-guide.md) |
| Manual beta runtime and packaging code | Implemented in the overhaul PR; software results belong in its check receipt | Freeze merged candidate and construct once on the qualified builder |
| Final manual beta image | NOT BUILT / NOT DEPLOYED | Verify actual root profile, units and artifact manifest; preserve A and recovery inputs |
| SPI boot count and manual lifecycle on applicable candidate | NOT YET RECONCILED | Credit valid existing SPI records toward 3 cold + 3 warm; fill only gaps, including final profile and CP5 restart |
| Release assets | NOT PUBLISHED | Stage through the release packager with actual artifact/evidence/eligibility records |
| Automatic managed lifecycle | DEFERRED | No managed checkpoint/autostart/ten-cycle qualification gate for beta |

## Bounded resumption

When the operator resumes hardware work: inspect live state and the existing
job once, preserve recorded custody, perform the required fresh boot through the
supported procedure, then follow [cutover](install-and-recovery.md#cutover-from-the-paused-experiment).
Build/deploy the manual profile and close only the finite gaps above. No automatic
return to the archived managed qualification program.

Known limits: [known issues](known-issues.md). Exact retained images:
[current images](current-images.md). Historical narrative:
[archived status](archive/pre-beta-20261114/implementation-status.md).
