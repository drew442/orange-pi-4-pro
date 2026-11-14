# Current status

Updated 14 November 2026 (UTC). **v0.1.0-beta.1 delivered.**
[GitHub prerelease](https://github.com/drew442/orange-pi-4-pro/releases/tag/v0.1.0-beta.1)
contains eligible release notes/checksums; images remain private.

Release tag/source: `v0.1.0-beta.1` →
`60e7ec6c8535a64cbda93a8919c9b4a0a1c9a67d`.
Runtime construction source: `8f311c484d33aab2882472766bc6263d064004c8`, including
merged PR #1, the reviewed manual boot-lock fix and bounded validation correction.
Documentation-only delivery recording does not rebuild the accepted artifacts.

## Actual acceptance

| Gate | Result |
| --- | --- |
| Software | PASS: 221 final CI stages/3,011 executed/817 declared CI exclusions; qualified native 221 stages/3,828 executed/no exclusions |
| Construction | PASS: final repaired A/B roots/full filesystem readback and matching fresh STATE/NVMe full stream/component verification. Initial candidate replaced only for the concrete boot-lock defect; no further construction |
| Deployment | PASS: guarded inactive-B write/readback, supported selection and physical cold boot via SPI/NVMe with SD absent; preserved normalized A, BOOT and guests |
| SPI boots | REUSED/PASS: three cold and three warm total, compatible prior credit plus repaired manual profile; protected-SD boots excluded; composed BOOT matches physical evidence |
| Manual lifecycle | REUSED/PASS: complete CP5→CP6→restart/CP7 lifecycle plus final automatic boot-lock preparation, normal protected CP7 restart/persistence12→13, UID60001 real KVM CPU/disk/HTTP, guest-owned shutdown and durable CP8 |
| Recovery | REUSED: applicable actual SPI/SD recovery, host rollback/paired restore and guest backup/restore evidence; current normalized-A boot and guarded repaired-B update refresh the host path |
| Delivery | PASS: implemented packager accepted all finite gates, copied/hash-verified five assets and retained provenance/source/evidence/guides/notices. Versioned private archive fully read back at receiver; GitHub prerelease/public metadata readback verified |

[Software](evidence/manual-beta-software-validation-20261114.json),
[hardware and reuse](evidence/manual-beta-hardware-acceptance-20261114.json),
[delivery receipt](evidence/manual-beta-delivery-20261114.json),
[release notes](releases/v0.1.0-beta.1.md), [exact images](current-images.md).

## Current board and custody

The board runs read-only manual-beta B, boot
`a97b2d62-52d1-4dbd-8d34-fc6f1d629058`, with SD absent. Its 85 maintenance,
four management and two boot-unit files authenticate; automatic dispatch is
masked, storage/admission preparation works, and manual locks are created at boot.
No manual RAM admission edit or account workaround was used on this final boot.

`firstkvm` is stopped, MainPID0/result success, at **CP8**; manifest
`17f21eeefef9eea79cad1410ff657803ebe55943074a28c216b6f14dade5b123`.
Its RAM unit is regenerated and not enabled for boot. Normalized A, the off-device
CP5 archive, failed cohorts, recovery SD and original/calibrated SPI snapshots
remain preserved. No host SPI programming or SD write was performed in this
resumption; vendor calibration metadata may change during boot.

All build/validation/delivery jobs are terminal. Prior success receipts were
retained, not restarted on session changes. The original timeout/missing fixture
and bytecode-preflight failures remain recorded alongside their bounded fixes.
The operator enlarged the builder's backing disk; reviewed guest-only growth
used all 33 GiB added capacity. No Hyper-V modification was performed by the agent.

## Delivery and limits

Private SSH artifact:
`drew@build.drewnet.online:/home/drew/orange-pi-4-pro-build/releases/v0.1.0-beta.1/orange-pi-4-pro-v0.1.0-beta.1-private.tar`.
Bytes: **917,145,600**; SHA-256:
`534ee8332b70c35ed59ed1f0c7c5948776d18bdeb7ea3f6f0653937a6513e91a`.
It has receiver-side full hash verification and private directory/file modes.

Only Orange Pi 4 Pro is supported. This package contains per-unit data and is not
for cloning to another unit. Automatic managed lifecycle/rollback remain deferred;
checkpointing is not a backup. Payload redistribution limitations are unchanged.
The final run deployed components on existing STATE/GUESTS rather than wiping the
whole drive. Existing long-run and recovery evidence is retained; there is no new
soak, conformance campaign, second construction requirement or ten-cycle gate.

The finite beta goal is fulfilled. Further features follow [roadmap admission](roadmap.md).
