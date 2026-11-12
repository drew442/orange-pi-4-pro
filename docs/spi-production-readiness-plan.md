# SPI/NVMe production-readiness plan

Execution started 2026-11-12. The recoverable baseline and metadata-aware SPI
software review/checks pass; physical recovery and later phases remain pending.
[Software evidence](evidence/spi-metadata-validation-20261112.json). The system currently operates with SD
removed. This plan completes production qualification and adds opt-in unattended
guest operation. Physical restore/reinstall, unattended deployment and final qualification remain
future work.

## Starting evidence and scope

| Item | Accepted starting point | Work remaining |
| --- | --- | --- |
| SPI/NVMe boot | One physical cold boot and one warm reboot with SD absent | Reach ten successful boots of each kind for the recorded firmware/SSD combination |
| KVM | Actual nonroot CPU/disk/HTTP, clean shutdown and checkpoint/restart through counters 7→8→9 | Exercise the new unattended lifecycle on real KVM |
| Endurance | Retained >20-hour data meets the revised eight-hour criterion | Reuse accepted data |
| SPI preservation | Boot0/package unchanged, vendor changes confined to `[260096,262144)` | Support mutable calibration in update and exact-restore planning |
| Recovery | Known SD boot after SPI programming, original and calibrated backups retained | Demonstrate the revised SPI restore/reinstall procedure |
| Guests | Manual recreation of RAM accounts/units after reboot | Persist trusted desired state and regenerate runtime state at boot |
| Management | SSH/NTP pass, earlier November 8 outage remains unexplained | Retain continuous diagnostics during planned qualification and investigate any recurrence |

The operational record pins the chip, firmware, NVMe, boot IDs, KVM runs and
backups. Ten-cycle protected-SD results remain evidence for that earlier route.
[Operational evidence](evidence/spi-operational-20261111.json).
[Release criteria](hypervisor-plan.md#release-acceptance-and-documentation).

Keep the A733, nominal 250 GB NVMe, 2 GB recovery card, minimal read-only host,
original SSH identity and supplied keys. Retain the compatible kernel/DTB/EL2
firmware and the requested any-subnet management policy. The recovery card stays
off-device during normal operation. All planned tests have an eight-hour maximum.
Installation keeps progress reporting and optional troubleshooting readback.

## 1. Prepare a recoverable baseline

Revalidate the live boot ID, exact NVMe identity, root/STATE pairing, SPI identity
and available builder disk/RAM. Confirm UART capture and off-device retention.
Retain a fresh SPI snapshot, its full hash, code-component hashes and decoded
calibration boundaries. Verify original and calibrated backups on codex and the
Ubuntu builder. Preserve current guest manifests/checkpoint history and trusted
backup references before introducing disposable test guests.

The SPI target remains `/dev/mtd0`, XM25QU128C, JEDEC `204118`, 16,777,216 bytes,
with Linux MTD erase units of 65,536 bytes. The NVMe remains SPCC serial
`134807190FC700149277`, 256,060,514,304 bytes. Known-target authorization persists.
Any changed target or expanded authorization scope needs a concise yes/no request.

**Exit evidence:** exact live inventory, readable verified backups, working
serial capture, recovery-card instructions and a current baseline manifest.

## 2. Make SPI maintenance aware of calibration metadata

Own changes in the SPI assembler, transaction engine, guarded programmer,
capture source pins, associated locks/manifests and focused tests. Reuse the
existing descriptor, identity, quiescence and durable-event mechanisms.

- Define the fixed board layout from pinned vendor sources. Boot0 occupies
  `[0,237568)`, vendor parameters `[260096,262144)`, and the boot package
  `[262144,1654784)`. Account for complete Linux erase units and preservation
  of neighboring bytes, including the vendor's smaller erase operations.
- Authenticate immutable firmware components and preserved areas separately
  from the bounded vendor parameter data. Define supported parameter formats
  and the erased state. Reject unknown layouts and changes outside declared
  regions. Differences discovered on a device cannot widen the allowed ranges.
- Bind every plan to the complete live snapshot and its hash. Show old/new
  component hashes, metadata handling, exact erase units and preservation.
  Revalidate the unchanged preimage before mutation.
- For firmware updates, preserve same-board calibration when the registered
  firmware format supports it. For exact restoration, reproduce the selected
  trusted full backup, including its metadata. Restoring the original blank
  image deliberately removes SPI boot support and runs from SD recovery.
- Keep install/update header-last ordering and review the existing restoration
  ordering. Retain readback, final close checks and off-device acknowledged
  events. Refresh the capture's programmer pin when its source changes.
- Investigate repeated vendor calibration writes. Preserve compatible boot
  behavior as the initial policy. Record affected bytes and erase/write paths.
  Introduce a firmware change only if source analysis establishes a correctness
  or material wear problem, then independently review and requalify that change.

Test captured preboot, initialized and subsequent-boot snapshots, whole-unit
reconstruction, corrupted metadata, unknown outside-region changes, stale plans,
short writes, erase failures, interruption and lost capture acknowledgements.
The current strict-gap plan remains unusable after metadata initialization.
[Current transaction engine](../scripts/spi-unit-transaction.py).
[Measured calibration behavior](spi-sd-boot-20261111.md#first-sd-free-boot-and-spi-calibration).

**Exit evidence:** independently reviewed source and concrete plans, focused
regressions and required full repository checks passing on the Ubuntu builder.

## 3. Prove the revised SPI recovery procedure

[Operator and coordinator procedure](spi-recovery-qualification.md).

A fresh operator follows the reviewed guide for this demonstration and records
discrepancies, supplying the reproduction evidence used in phase 6. Complete
software review and all non-destructive preparation before writing.
Use the known SD recovery environment with guests stopped and verified backups
retained off-device. Inspect the exact current target and fresh restore plan.

1. Restore the selected original full SPI backup with the revised tool. Verify
   the intended complete bytes before reboot and retain the final receipt.
2. Boot through the known SD and confirm management and NVMe state access.
3. Reinstall the reviewed SPI firmware with the declared metadata policy and
   readback. Retain each receipt before requesting another power transition.
4. Boot with SD removed, confirm SPI-to-NVMe loading and unchanged firmware code
   outside the allowed parameter area, then run the actual KVM smoke fixture.

A failed or uncertain write stops the sequence for inspection. Preserve partial
receipts and read the live state before planning another operation. Use fake NOR
and file fixtures for failure injection, then perform the bounded physical
restore/reinstall demonstration. Recovery from loss of management uses the
already tested serial/SD path.
[Recovery policy](evidence/spi-sd-recovery-policy-20261111.json).

**Exit evidence:** actual restore/readback, SD recovery, reinstall/readback,
SD-removed boot and KVM receipts, with verified off-device copies.

## 4. Add opt-in unattended guest lifecycle

Use a versioned root-owned registry on NVMe STATE, proposed at
`/run/a733-state/guests/autostart.json`. Each enabled entry binds an enrolled guest,
its stable UID/GID, trusted current manifest and boot policy. Publish registry
updates atomically under the existing maintenance/guest locks. Validate schemas,
ownership, sizes and symlinks. Include this state in backup/restoration.

At host boot, validate the selected root and STATE, activate the exact guest
filesystem, recreate locked accounts in RAM, render restricted units and start
enabled guests after `/dev/kvm` is available. Reuse current activation, manifest,
configuration and launch logic. Make repeated preparation idempotent and protect
against concurrent enrollment, checkpointing and host maintenance. Keep management
available when a guest fails. A/B rollback preserves the current guest disks
and their trusted manifest/history pairing. A compatible older root reads the
registry; an incompatible older root leaves autostart disabled and keeps
management available. Restoring guest metadata requires its matching disk backup.
An old STATE registry or enrollment pin must never overwrite the trusted pairing
for a newer guest disk.

Default to opt-in autostart. Keep the existing `firstkvm` fixture manually managed
and use a separately enrolled disposable guest for automation tests. Confirm
available UID/GID assignments through the account planner. The HTTP-confirmed
acceptance fixture gets a dedicated validator that checks actual KVM, CPU, disk
and network evidence before its one confirmation request. Production guests use
their configured normal lifecycle.

Automatic checkpointing needs a reviewed evidence contract. Add a protected local
QMP shutdown channel or a supported guest shutdown mechanism, observe guest-owned
poweroff and raw QEMU exit, flush the stopped disk, checkpoint durably, publish the
new trusted manifest reference, and re-render before another launch. Use a durable
transition record binding the old/new manifest hashes, verified shutdown evidence
and intended registry update. On boot, reconcile an interrupted transition using
that record and authenticated checkpoint history. Resume only a provable known
state; otherwise quarantine the guest. Test crashes before and after each manifest,
history and registry publication so a partial update cannot bless an unexplained
disk hash. Preserve the manual checkpoint command's operator-attestation contract. Host SIGTERM or exit
status zero alone cannot establish clean guest shutdown. Unclean exits, stale
hashes or incomplete checkpoint publication quarantine the guest for recovery.
Bound retries and prevent reboot/crash loops.

Run from STATE/RAM with root read-only. Package the new services/controllers in
reproducible inactive-root builds and retain the working rollback slot. Test
clean boot/start/stop/checkpoint/restart, disabled guests, malformed registry,
account collisions, missing storage/KVM, guest failure, unclean exit, publication
interruption and host shutdown ordering. Physically validate unattended launch,
clean shutdown and restart on KVM before final boot qualification.
[Current lifecycle](guest-operations.md).
[Checkpoint contract](guest-checkpoints.md).

**Exit evidence:** reviewed source, passing host checks, actual unattended KVM
runs and a tested rollback/disable path that preserves management access.

## 5. Complete SPI boot qualification on the final configuration

Freeze the firmware/SSD identity and final host automation before the remaining
series. Identify firmware through boot0/package hashes and the declared metadata
policy, recording complete flash snapshots separately. Normal vendor parameter
updates within the declared area retain the code identity. Credit the existing
one cold and one warm SPI boot while their firmware and SSD identities remain
applicable. Run **nine additional cold boots and nine
additional warm reboots**. If firmware code changes, explicitly reassess the
applicable earlier records and affected checks before setting the remaining count.
Automatic guest behavior must be exercised on the final host build. Credit
qualifying recovery and automation demo boots toward those additional counts
when their recorded firmware/SSD identities match. Collect only the remaining
shortfall, keeping the count and supporting evidence explicit.

Adapt the collectors for SPI operation and SD absence. During readiness they may
observe the registered autostart test guest. Before the next host reboot or
poweroff, require verified guest shutdown, completed checkpoint publication and
no active VM consumers. Keep safeguards that refuse uncertain reboot requests.
After an observation timeout or uncertain acknowledgement, restrict retries to
read-only observation. Establish the authoritative outcome before admitting
another reboot or poweroff request.

Every counted boot needs a distinct boot ID, UART SPI storage selection and NVMe
file loading, expected NVMe root/STATE, original SSH identity, management address,
automatic NTP, eight CPUs/EL2 and actual KVM guest checks. Use a 300-second boot
readiness deadline with progress reporting. Count only completed cases. Cold
cycles require clean UART powerdown and operator USB-C power removal for at least
ten seconds, with SD left out and an explicit per-cycle attestation. Preserve
failed/partial attempts separately in the evidence.

Keep continuous UART and management diagnostics through the series. Investigate
any unexpected SSH loss from retained console, network/service state and kernel
logs before accepting the affected case. The historical outage stays documented
if its cause cannot be reproduced. Qualification requires all new cases to meet
their readiness and management checks and all observed failures to be resolved.

Use compact per-boot checks and full SPI snapshots at defined maintenance/series
boundaries. Compare code regions and bounded metadata changes. Preserve progress
on long reads. Reuse the accepted endurance data and earlier applicable
update/rollback/backup cases. Run targeted regressions when code changes affect
them, with the same eight-hour maximum.

**Exit evidence:** ten accepted cold and ten warm SPI boots, final-build
unattended guest evidence, intact code components, measured metadata behavior,
management results and verified off-device custody.

## 6. Close production acceptance and publish the operating procedure

Use the independent operator's phase-3 recovery report and an independent
walkthrough of the updated autostart guide. Retain discrepancies and their
resolutions. Reuse completed qualifying demonstrations during signoff.
Reconcile the requirement matrix, artifacts, hashes, source versions, physical
results, rollback and backup procedures. Update status, current images,
architecture and AGENTS.md. Keep raw logs, images and private keys outside Git.
Commit coherent validated changes and push to main.

Production signoff requires completed evidence from every phase, readable backups,
reviewed metadata-aware SPI maintenance, verified SD recovery, opt-in unattended
operation and the completed SPI boot counts. Record historical reliability and
vendor source/license limitations alongside that signoff. Planning and passing
host tests alone leave these hardware gates pending.

## Execution ownership and checkpoints

| Work package | Owner | Review/checkpoint |
| --- | --- | --- |
| Architecture, metadata and guest lifecycle policy | Coordinator | Concrete design and acceptance criteria |
| SPI engine/programmer and tests | Sol medium implementer | Independent Sol high review before physical writes |
| Registry/services/checkpoint automation | Sol medium implementer | Independent Sol high review before deployment |
| Focused log/source extraction | Luna low | Coordinator verifies extracted facts |
| Hardware operation, boot counts and final acceptance | Coordinator with operator for cable/card steps | Physical receipts and independent evidence review |

Keep at most two active children with exclusive file ownership and fresh focused
handoffs. Children cannot delegate. Finish prerequisites before dependent work.
The builder remains `drew@build.drewnet.online`, Ubuntu 22.04, starting compilation
with two jobs and fresh resource checks. Existing target authorizations persist.
Operator availability is needed for recovery-card transitions and the remaining
cold power cycles, initially nine before crediting qualifying demonstrations.
The physical recovery demonstration may supply additional qualifying boot records
when the final firmware/configuration matches.
