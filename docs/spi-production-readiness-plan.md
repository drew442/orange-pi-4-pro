# SPI/NVMe production-readiness plan

Execution started 2026-11-12. SPI/NVMe boot with SD removed, metadata-aware SPI
restore/reinstall and actual manual nonroot KVM/checkpoint pass. The managed
workflow remains unqualified. Its prior launch failed before QEMU; supported
quarantine restoration and fresh-boot admission recovery now pass.

Frozen F349 passes 3717 privileged tests, one trial construction and independent
complete artifact/filesystem/readers. Inactive-B installation and authenticated
SD-removed boot now pass; normalized A remains preserved. After a working hardware trial, an independent fresh-checkout build
will compare all artifacts and reproduce the recipe, giving two constructions.
[Current status](implementation-status.md),
[trial evidence](evidence/managed-permit-root-trial-20261113.json).

Retain the accepted eight-hour workload result from existing >20-hour data.
No qualifying endurance rerun is required.

## Delivery order and current checkpoints

### Next execution steps

Use frozen F349 and its exact artifact pins for the next deployment. Preserve
older `2996365`/`4334a22` artifacts and normalized A as separate cohorts. The
bound supervisor stop and permit budget preserve normal guest shutdown; the
collector isolates completion data from the power helper's fixed two-file packet.
Artifact-derived helper pins pass independent review and 100 focused checks;
combined external validation and actual execution remain pending.
[Completion procedure](managed-fixture-completion.md).

The disposable guest is disabled at generation 8 after durable quarantine.
F349 completed permit publication, then its child exited1 before QMP binding.
Retain [the failure evidence](evidence/managed-permit-launch-failure-20261113.json).
Do not re-enroll it or overwrite retained failed cohorts. Its first successful
managed KVM execution, checkpoint/restart and automatic boot still need proof.

| Order | Work and owner | Required result before proceeding |
| --- | --- | --- |
| 1 | Coordinator prepares deployment and SPI boot collectors; Sol implements bounded changes and a separate Sol reviews them | Artifact-bound inactive-B and rollback plans; collectors support SD absence, current firmware identity and authenticated managed-guest outcomes. Preserve uncertain-reboot safeguards; never issue another power request just because observation timed out. |
| 2 | Operator boots the retained recovery SD; coordinator revalidates targets and obtains independent live-plan review | Fresh boot ID, SD CID, NVMe/STATE identity and live SPI backup; readable original/calibrated backups retained off-device. Existing known-target write authorization applies. |
| 3 | Coordinator demonstrates SPI restoration and reinstallation, with operator card/power transitions | Restore original blank SPI with required readback, boot via SD, reinstall reviewed SPI, then prove SD-removed NVMe boot and actual KVM. Retain receipts before each transition. |
| 4 | Coordinator stages the reviewed candidate into inactive B and boots it | Preserve working normalized A, BOOT/kernel/DTB/EL2, STATE, SSH identity and `firstkvm`. Confirm management, read-only root, expected listeners and SD absence before enabling automation. |
| 5 | Coordinator deploys the separate disposable managed guest | Publish explicit storage policy before initialization; prepare locked account and enrolled payload, then register and enable. Prove nonroot KVM autostart, CPU/disk/network, guest-owned shutdown, durable checkpoint/restart, coherent backup/restore and disable. Confirm actual systemd mount/shutdown order and failure isolation. |
| 6 | Coordinator exercises changed installed maintenance and rollback under independent review | Review a separate concrete plan before temporarily replacing inactive A. Demonstrate applicable interruption/retry and selector publication/cancellation with disposable data. Restore the authenticated normalized A rollback root before selecting it; prove management and preserved guest cohort on A, then return to B and prove managed operation. Default bulk writer readback is optional; full selector authentication remains required and reports progress. |
| 7 | Coordinator freezes final configuration and completes SPI boot qualification | Reach ten accepted cold and ten warm boots. Credit applicable existing and demonstration boots explicitly; collect only the shortfall. Record final-build managed behavior and actual KVM outcomes. Retain continuous UART and investigate any new management failure. |
| 8 | Independent agent/operator reproduces the released procedure; coordinator reconciles acceptance | Execute build/deployment, SPI recovery and guest operations from the declared clean environment, retain discrepancies and resolutions, and distinguish retained-input construction from independently provisioned-builder reproduction. Update runbooks, evidence/status and release matrix; commit and push validated changes. |

Steps 1 and other non-destructive preparation continue while step 2 awaits
physical access. Independent review is required for boot, target selection,
installation and rollback changes before mutation. Hardware records must bind
boot ID, exact storage/firmware identities, UART and actual guest outcomes;
software fixtures cannot close these gates. Keep verified evidence off-device.

No endurance rerun is planned: the retained longer run satisfies the eight-hour
criterion. Cap every future planned test at eight hours. Use progress-enabled
installation writes, preserve the known 2 GB recovery SD without reflashing,
and leave attached heatsinks in place. Preserve source/license limitations;
local production qualification does not establish redistribution clearance.

The recovery-card insertion/removal transitions and SPI reinstall are complete.
Keep the SD removed. Further operator involvement is limited to the required card/power transitions and cold
cycle attestations. Software preparation continues while those steps are pending.

| Phase | Current checkpoint | Completion condition |
| --- | --- | --- |
| 1. Recoverable baseline | Recorded; revalidate after each hardware transition | Verified original/calibrated SPI and stopped guest backups retained off-device |
| 2. Metadata-aware SPI maintenance | Software checks and independent review pass | Reviewed fresh live plans and preserved calibration policy |
| 3. Physical restore/reinstall | Blank restoration, SD recovery, reinstall and SD-removed manual KVM pass | Original SPI restore, SD recovery, reinstall, SD-removed boot and actual KVM pass |
| 4. Unattended guests | F349 build/artifact/readers and physical B boot pass; managed child exit unresolved | Reproducible packaged services, graceful shutdown/checkpoint/reconciliation, rollback and real KVM acceptance |
| 5. Final SPI boot series | One cold and one warm operational boot evidenced | Ten accepted cold and ten warm boots with applicable identity and final automation checks |
| 6. Independent reproduction and signoff | Earlier protected-SD reproduction retained | Updated SPI recovery and guest procedures reproduced, evidence reconciled and changes pushed |

Continue managed deployment and collector preparation. Complete both before the final configuration
is frozen for phase 5. Credit qualifying demonstration boots and existing
endurance evidence rather than repeating completed tests. Phase 6 closes only
after the physical gates pass; an API implementation or software test suite
does not close a deployment checkpoint.

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
The original strict-gap programming plan is stale after metadata initialization.
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
Back up the new registry, trusted lifecycle heads and checkpoint history together
with their matching disposable guest disk. Restore that coherent cohort and prove
actual KVM autostart, the expected persisted counter, clean shutdown and
checkpoint/restart. Test rejection of stale or mismatched metadata in the focused
host checks. Combine the successful physical restore with the planned automation
demonstration; earlier manual-guest backup results do not qualify this new state.
[Current lifecycle](guest-operations.md).
[Checkpoint contract](guest-checkpoints.md).

**Exit evidence:** reviewed source, passing host checks, actual unattended KVM
runs and a tested rollback/disable path that preserves management access.

## 5. Complete SPI boot qualification on the final configuration

The [boot-credit ledger](evidence/spi-boot-credit-ledger-20261112.json) binds the
existing cold/warm boot UUIDs, immutable SPI component hashes, SSD identity and
actual KVM counters 8/9. These are conditional route credits after identity
comparison with the reinstalled firmware. The new managed build has no physical
boot or autostart credit. Add qualifying demonstration records before computing
the remaining shortfall; the protected-SD series is excluded.

Freeze the firmware/SSD identity and final host automation before the remaining
series. Identify firmware through boot0/package hashes and the declared metadata
policy, recording complete flash snapshots separately. Normal vendor parameter
updates within the declared area retain the code identity. Credit the existing
one cold and one warm SPI boot while their firmware and SSD identities remain
applicable. Maintain a per-boot evidence ledger and collect only the shortfall to
**ten accepted cold boots and ten accepted warm reboots**. Each credited record
binds firmware/SSD identity, host build and guest evidence; older route evidence
does not establish final-build unattended behavior. If firmware code changes, explicitly reassess the
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

Use the independent operator's phase-3 recovery report. A second agent or
operator must execute the released build/deployment and autostart procedures
from a clean environment, recording artifact identity, commands, physical
results and discrepancies. Retain discrepancies and their resolutions. Reuse
completed qualifying demonstrations where they cover the released procedure.
Reconcile the requirement matrix, artifacts, hashes, source versions, physical
results, rollback and backup procedures. Update status, current images,
architecture and AGENTS.md. Keep raw logs, images and private keys outside Git.
Commit coherent validated changes and push to main.

The reproduced operating runbook must cover quarantine and closed-admission
diagnosis, checkpoint/backup failures, disabling autostart, management recovery,
backup custody and future host/SPI maintenance. State the implemented shutdown
deadline and the safe outcome when shutdown or checkpoint cannot complete.
Exercise these commands during reproduction rather than documenting them alone.

Production signoff requires completed evidence from every phase, readable backups,
reviewed metadata-aware SPI maintenance, verified SD recovery, opt-in unattended
operation and the completed SPI boot counts. Record historical reliability and
vendor source/license limitations alongside that signoff. Planning and passing
host tests alone leave these hardware gates pending.

## Decision gates and release boundary

Proceed from each milestone only after its evidence is durable and independently
reviewed where boot, storage, installation or rollback changes. A hardware failure
or uncertain mutation stops dependent operations: preserve receipts, inspect the
actual state and prepare a fresh plan before another write. Continue independent
software/documentation work while physical access is pending.

The final acceptance matrix must explicitly reconcile every
[original release criterion](hypervisor-plan.md#release-acceptance-and-documentation):
actual 2 GB-card installation/recovery, final SPI boot counts and SD absence,
real KVM and accepted endurance, interrupted update/rollback, missing-NVMe
recovery, coherent backup restoration and host access restrictions. For each
changed path, record a new physical result or justify the exact applicability of
retained evidence. Preserve the limited scope of earlier fault injection; do not
turn a simulated missing driver into a claimed physical NVMe disconnection.

Completion means the released procedure has been independently followed, all
applicable physical gates have passed, artifacts and backups are retained,
limitations are documented and coherent validated changes are pushed to main.
Unresolved exact-source/license issues must remain visible; local hardware
qualification cannot be presented as public redistribution clearance. No new
endurance run is required, and no planned test may exceed eight hours.

## Execution ownership and checkpoints

### Remaining implementation sequence (2026-11-12)

The [status](implementation-status.md) distinguishes reviewed software from
installed hardware. The installed-maintenance core and managed archive codec
are validated foundations; neither constitutes a deployed maintenance or backup
workflow. Finish the following packages in dependency order while phase 3 waits
for the operator. Each package includes focused regressions, independent review
where it changes boot/storage/rollback, and a coherent validated commit.

| Order | Work package | Deliverable and acceptance gate |
| --- | --- | --- |
| A | Retain installed maintenance integration | Update/selector admission, actual loop checks and explicit closed-maintenance recovery are evidenced in status. Preserve these guarantees through the remaining integration; rerun affected checks when behavior changes. Guests stop before exclusive maintenance and target mutation ends before reopening. |
| B | Close managed backup/restore integration | Publication/explicit-resume/bootstrap-census integration passes the frozen 3577-test run and independent review. Retain its evidence and demonstrate coherent restoration on the disposable real KVM guest. Preserve unrelated guests; reject unsupported identity changes and stale authority. |
| C | Preserve portable RAM recovery integration | Reviewed offline exclusion, protected p5 probing and disposable kernel-loop evidence are recorded in status. Retain exclusion through cleanup, refuse raw STATE restoration over managed metadata and rerun affected checks only when changes justify it. Confirm the released physical recovery route. |
| D | Deploy bootstrap and guest services | Initial policy publication, explicit controls and the final restore census are packaged in the reviewed candidate. Prove actual unit/mount dependencies, bounded nonroot KVM and shutdown order on the target. Retain SSH/NTP when guest preparation or execution fails. |
| E | Package and build the candidate | Completed: source 2996365 passes 3589 tests, two fresh constructions match and independent artifact/reader checks pass. Retain pinned artifacts and the working root/recovery card; rebuild only if candidate code changes. |
| F | Validate the installed candidate | Deploy to the inactive root and use the separate disposable managed guest. Prove actual nonroot KVM autostart, CPU/disk/network, guest-owned clean shutdown, checkpoint/restart, coherent backup/restore, disable and host rollback. Preserve the manually managed firstkvm cohort. Software fixtures cannot satisfy this gate. |

E/F also retain and verify the installed host restrictions: read-only root,
NVMe/RAM state placement, bounded runtime storage, limited nonroot QEMU authority,
locked accounts, key-only SSH, expected listeners and recorded SD absence. Use
the existing release checks where applicable and retain the actual results.

A–E now pass integrated software and artifact validation. F is the remaining
candidate deployment gate and requires actual installation and physical evidence. Phase 3 can proceed
independently when the retained recovery card is booted. Finish phases 3 and 4
before freezing the configuration for phase 5. Reassess earlier boot credit if
firmware identity changes; record final automation checks separately.

For D, publish the explicit trusted storage policy before initial registry/runtime
preparation. Missing or inconsistent policy must leave management available while
refusing guest launch. Check actual target systemd 252 dependency and shutdown
behavior, including p5 activation, admission closure and checkpoint cleanup; private
PID1/native fixtures do not satisfy this gate.

For B, the reviewed transaction distinguishes historical archive validity from
fresh destination authority. Validate complete disk/manifest/history/lifecycle
publication, registry-generation handling, preserved unrelated guests, explicit
resume and incomplete-transaction boot refusal. The candidate guide is not yet
an installed restoration service. Use the disposable automation guest for physical
restoration and preserve the manual `firstkvm` cohort.
[Architecture review](evidence/managed-restore-design-review-20261112.json).

For E and phase 6, distinguish a fresh checkout using retained inputs from a
fully independently provisioned builder. Reproduce the released procedure with
its declared dependency locks and record any unavailable exact inputs; do not
claim clean-builder reproduction from cached composition alone. Track the
remaining exact toolchain sources and 26 vendor boot-input licensing gaps in
[the source inventory](toolchain-sources.md). Local hardware qualification does
not establish public binary redistribution clearance.

Before deploying the candidate in F, record a change-to-evidence matrix for the frozen candidate:

| Changed area | Required evidence before carrying acceptance forward |
| --- | --- |
| SPI writer or metadata policy | Fresh reviewed plans and the phase-3 physical restore/reinstall demonstration; reassess boot credit if firmware code changes |
| Maintenance, portable restoration or selection | Focused kernel-loop regressions plus applicable physical interruption/retry, selection and rollback on the released tools |
| Managed lifecycle, backup/restore or boot units | Phase-F actual nonroot KVM autostart, coherent restore, checkpoint/restart, disable and host rollback |
| Unchanged workload duration requirement | Retain the accepted endurance result; no qualifying endurance rerun |

Identify reused records and their exact scope in that matrix. Passing historical
tests on the manual or protected-SD release does not qualify changed managed
restore or shutdown paths. Combine applicable physical demonstrations with the
remaining boot series to avoid redundant cycles.

### Operator steps and evidence

The retained SU02G recovery card is now removed. Original blank SPI restoration,
SD recovery, reviewed reinstallation and the subsequent SD-removed SPI boot/manual
KVM check passed on November 13. Managed B is installed and booted; normalized A
and BOOT remain preserved. [Recovery/reinstallation](evidence/spi-reinstall-and-poweroff-20261113.json)
and [post-reinstall/B records](evidence/post-reinstall-SPI-managed-B-20261113.json).
The external managed-fixture template required a port correction before launch;
retain [the correction and unused cohort](evidence/managed-fixture-port-correction-20261113.json).

For future recovery transitions, the coordinator explicitly reports readiness
for SD removal after reinstallation and clean poweroff. The operator then removes
SD and restores power. The recorded November 13 transition is complete.
Subsequent cold qualification requires the per-cycle USB-C removal attestation
described in phase 5. No heatsink removal or hardware benchmark is required.

For every package, record source revision, checks, remaining limitations and raw
receipt location in the status/evidence files. Hardware records include boot ID,
UART, exact storage and firmware identity, guest outcome and verified off-device
custody. Retain failures and uncertain outcomes. Refresh operator instructions
against the actual released commands before asking an independent operator to
reproduce them. Final signoff requires phases 1–6; the accepted endurance data is
reused and no qualifying endurance rerun is planned.

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
cold power cycles, with the remaining count computed from the reviewed ledger.
The physical recovery demonstration may supply additional qualifying boot records
when the final firmware/configuration matches.
