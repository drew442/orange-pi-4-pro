# Implementation status

Updated 2026-11-13. Current physical state: managed root B from SPI/NVMe,
boot `1095a827-6f92-486d-9ad3-2463a95c2d5e`, SD physically removed. Management is
`root@192.168.1.118`, authenticating the original host key with alias
`192.168.1.116`. Candidate `2996365` authenticates all 85 tools, four management
files and two guest boot units; root is read-only, NTP synchronized. Normalized
root A and BOOT remain preserved. Initial policy/storage preparation passes:
empty registry, admission open, no dispatched guest and unchanged retained
`firstkvm` checkpoint. Managed account/payload deployment is in progress;
automatic guest operation and production qualification remain pending.

The post-reinstall SPI boot on normalized A (`ae6ef902-51ce-45cf-88a9-62f7f390eded`)
passed actual nonroot KVM CPU/disk/HTTP, counter 9→10 and guest-owned exit zero.
Checkpoint 5 is durable and independently verified on codex and builder;
a subsequent restart of checkpoint 5 is not claimed. Fresh SPI backup
`2d9e2a…92386` matches factory firmware outside valid calibration metadata and
is synced off-device. [Physical evidence](evidence/post-reinstall-SPI-managed-B-20261113.json).

The earlier attempt still had the SU02G card installed and booted through SD;
it receives no SPI credit. The operator clarified the misunderstanding and
removed it before this confirmed SPI boot. Reviewed original-blank restoration,
SD recovery and reinstallation retain full readback/close evidence:
[recovery](evidence/spi-blank-restoration-and-sd-recovery-20261113.json) and
[reinstallation](evidence/spi-reinstall-and-poweroff-20261113.json).

**The validated system operates from SPI and NVMe with SD
physically removed.** SPI cold boot, warm restart, original SSH identity,
automatic NTP and actual nonroot KVM CPU/disk/HTTP tests pass. The guest persisted
counter 7→8→9 across checkpointing and host restart. Independent review passes.
[Operational evidence](evidence/spi-operational-20261111.json) and
[verified off-device custody](evidence/spi-operational-custody-20261111.json).

The earlier protected-SD release and independently reproduced deployment remain
accepted, including ten warm/ten cold boots and the eight-hour workload from
retained >20-hour data. Those ten-cycle records cover that earlier boot route.
The earlier November 8 SSH failure remains unexplained.

## Current SPI result

The identified XM25QU128C received the reviewed firmware with complete readback.
SD recovery after programming passed. The operator then removed SD and restored
power. SPI/NVMe cold boot and warm restart both passed, followed by real KVM
runs on each host boot. Vendor U-Boot updates its 2 KiB calibration metadata
while preserving boot0 and the firmware package. Current calibrated flash and
the original blank backup are retained off-device. Future SPI writes require
a fresh metadata-aware reviewed plan. [Procedure](spi-sd-boot-20261111.md).

## Production qualification plan

SPI/NVMe operation is verified. Production qualification and unattended guest
operation are planned in [the production-readiness plan](spi-production-readiness-plan.md).
It covers metadata-aware SPI update/restore, a physical recovery demonstration,
opt-in guest lifecycle automation, remaining boot counts and independent signoff.
Its [remaining implementation sequence](spi-production-readiness-plan.md#remaining-implementation-sequence-2026-11-12)
orders maintenance, coherent backup/restore, RAM recovery, bootstrap/services,
reproducible packaging and physical deployment, with explicit completion gates.
Existing endurance data remains accepted. Execution has begun: live identities,
UART/relay, builder resources and original/calibrated SPI custody were rechecked.
A fresh SPI backup matches the retained post-warm image. The stopped checkpoint-4
guest cohort has verified manifest files and a synced second copy on the builder.
[Baseline evidence](evidence/production-baseline-20261112.json). Metadata-aware
SPI engine/programmer/assembler changes now preserve validated live calibration
and restore registered exact backups. Independent source and concrete live-plan
review passes. The privileged Ubuntu suite passes **2346 tests**, five shell
suites and five diagnostic cases, with no skips/errors/failures after correcting
validation staging permissions. Read-only live install/calibrated-restore plans
are no-ops; original blank restoration affects exactly units 0–25.
[Software evidence](evidence/spi-metadata-validation-20261112.json) and
[metadata policy](spi-calibration-policy.md). Original blank restoration and
subsequent SD recovery now pass [physical checks](evidence/spi-blank-restoration-and-sd-recovery-20261113.json).
Reinstallation also passes [full readback and close](evidence/spi-reinstall-and-poweroff-20261113.json).
The post-reinstall SD-removed boot/KVM demonstration passes. Unattended guest
operation and final qualification remain pending.

The [guest lifecycle design](guest-autostart-design.md) passes independent
architecture review. The following software components are reviewed and tested;
**the pinned B runtime is installed and authenticated; managed guest hardware
qualification remains pending**.

| Component | Evidence and current boundary |
| --- | --- |
| Admission, registry, protected stores and bootstrap accounts | [Foundation](evidence/guest-foundation-validation-20261112.json), [state](evidence/guest-state-validation-20261112.json), [account batches](evidence/account-admission-validation-20261112.json). Actual native permissions/flocks; one lease for sorted guest locks. |
| Managed launcher, supervisor, fixture and checkpoint | [Runtime](evidence/managed-runtime-validation-20261112.json), [launch](evidence/managed-launch-validation-20261112.json), [supervision](evidence/managed-supervisor-validation-20261112.json), [checkpoint foundation](evidence/managed-checkpoint-foundation-validation-20261112.json) and [integration](evidence/managed-checkpoint-runtime-validation-20261112.json). Native producer composition and strict recovery pass with explicit A733/KVM substitutions. Parsing alone grants no authority. |
| Legacy backup, checkpoint, enrollment and restore guards | [Admission](evidence/legacy-admission-validation-20261112.json), [publication](evidence/legacy-publication-validation-20261112.json). Managed names refuse; explicit restore name precedes archive access. ARM file-engine local/HTTPS/TLS probes pass with runtime/account placement substituted. |
| Nonroot manual launch | [Evidence](evidence/manual-launch-admission-validation-20261112.json). Protected root namespace pins and exactly three input/two readonly lock FDs survive exec; no extra child capabilities. Candidate bootstrap producer is composed; installed service hook remains pending. |
| Installed maintenance and managed backup artifact format | [Foundation evidence](evidence/maintenance-backup-foundation-validation-20261112.json). **2888** privileged Ubuntu tests, five shell suites and five diagnostic cases pass. Maintenance core is installed-only; the six-member codec is artifact validation, not backup/restore authority. |
| Installed update/selection admission | [Integration evidence](evidence/installed-maintenance-admission-validation-20261112.json). **2905** privileged Ubuntu tests plus actual private-loop write/retry and ext4 selector interruption/cancellation pass. Independent source/fixture reviews pass; platform/admission substitutions and the corrected wrapper TOML failure are retained. |
| Managed source export and explicit admission reopening | [Evidence](evidence/managed-sender-reopen-validation-20261112.json). **2956** privileged Ubuntu tests, shell/diagnostic suites and independent reviews pass. Successful fixture export and pre-output proof rejection are covered. Cleanup reporting correction preserves acknowledged reopening while reporting command failure. [Candidate procedure](guest-maintenance-admission.md). |
| Managed historical decoder, artifact receiver and local archive reader | [Evidence](evidence/managed-archive-validation-20261112.json). **3012** privileged Ubuntu tests and shell/diagnostic suites pass. Independent reviews resolve partial-construction and final-observation cleanup faults; native sender integration passes with explicit platform/shutdown substitutions. Artifact publication supplies no destination restore authority. |
| Initialization exclusion and bootstrap registry/markers | [Evidence](evidence/bootstrap-admission-validation-20261112.json). **3060** privileged Ubuntu tests and shell/diagnostic suites pass. Actual readonly EX interlock, native failure probes and original-owner preparation pass review after GID-boundary and reserved-firstkvm corrections. Candidate boot composition now uses both sides; marker observations grant no launch permission. |
| Single managed run controller | [Evidence](evidence/managed-service-validation-20261112.json). **3077** privileged Ubuntu tests and shell/diagnostic suites pass. Independent native shutdown/cleanup probes pass; healthy polling no longer spawns PID1 queries. Actual retained locks, run, fork/QMP surrogate and checkpoint compose with explicit platform/credential substitutions. Unit now installed; hardware qualification pending. |
| Pre-p5 maintenance and normal activation | [Evidence](evidence/guest-activation-admission-validation-20261112.json). **3120** privileged Ubuntu tests and shell/diagnostic suites pass. Independent helper/wrapper reviews and actual private-loop interruption/retry/idempotency, preservation and cleanup pass. Protected registry must match reviewed target; binding and reopening follow all target cleanup. Bootstrap borrowing remains separate. |

Current packaging has **84 sources, 85 runtime files, 22 entrypoints**, four
management runtime files and two separately bound guest boot units. The historical
exact 39-source recovery boundary remains intact. **These controllers are now installed in B.** Their native/surrogate results
remain software evidence; installation alone does not establish acceptance.

[Coherent managed restoration](evidence/managed-restore-validation-20261112.json)
now passes independent implementation review and **3577 privileged Ubuntu
22.04 tests**, five shell suites and five diagnostic cases without skips,
errors or failures. Actual canonical shutdown EX, stopped disk custody and
protected source/cohort checks precede private staging. Durable intent and
ordered no-replace publication retain originals; registry CAS preserves current
enable flags and unrelated guests. Explicit resume verifies the exact reachable
phase, rehashes current objects once and re-establishes intent/phase durability.
Missing whole shared lifecycle parents use pre-pinned private stages. Historical
completion is readonly before admission/mount/archive access and survives cleanup
uncertainty without claiming current readiness. [Candidate guide](managed-guest-restore.md).

Original bootstrap EX performs the readonly restore census before state,
markers, accounts or admission opening. Incomplete publication refuses; unsafe
mounts/metadata never become absence. Failed preparation's ordinary teardown
still unmounts p5. Restore can recover that exact mount under typed shutdown EX
without initialization, nonce adoption or reopening. Journal/traversal effects
are reported separately, including after later archive refusal. Actual disposable
GPT/ext4 activation interruption/retry/idempotency, locks, permissions and cleanup
pass; platform/STATE/NVMe identity substitutions remain explicit. Encoded/decode
progress adds no scans, and retained inode/metadata checks avoid repeated full
disk hashes. A733 service ordering and actual managed KVM remain physical gates.

The initial 3577-test full run's eleven dispatcher fixture setup errors are
retained. Its closed helper list omitted the new restore census; the corrected
fixture passes all thirteen root dispatcher cases and the full rerun passes.
Independent review retains six resolved findings: historical cleanup receipts,
resumed intent durability, private metadata modes, mounted-record refusal,
recovery activation after failed-service unmount and public activation reporting.
Both terminal runs, source locks, raw reviews and verified second copies are
linked in the evidence. The prior [architecture contract](managed-restore-design.md)
and [59-test custody foundation](evidence/managed-restore-custody-validation-20261112.json)
remain historical subsets of this integrated result.

Earlier software evidence remains linked for
[explicit storage-policy schema](evidence/storage-policy-schema-validation-20261112.json),
[portable recovery/accounts](evidence/portable-recovery-validation-20261112.json),
[bootstrap/budgets](evidence/boot-prepare-validation-20261112.json),
[dispatch/teardown](evidence/guest-service-validation-20261112.json),
[policy publication](evidence/initial-policy-validation-20261112.json) and
[explicit guest controls](evidence/guest-controls-validation-20261112.json).
Root-only controls preserve firstkvm, exact RAM units, resource budgets and
locked accounts; requests are consumed once and unknown acknowledgements never
silently queue another run. Manual unit rendering remains explicit. Candidate
construction/repeat now pass; inactive-root deployment and physical qualification
are next.
No new endurance run is planned.

Candidate construction refused before image creation because seven outer Ubuntu
package versions had advanced through November 11 unattended upgrades. A separate
exact 842-package outer-host lock now passes independent review and the corrected
**3579-test** full Ubuntu run, shell/diagnostic suites and sixteen independent
inventory probes. The frozen replay/archive lock remains unchanged. The initial
0077-umask harness failure and successful unchanged-source 0022 rerun are retained.
[Builder adapter evidence](evidence/outer-builder-inventory-validation-20261112.json).

Deployment review found that extending the management descriptor from four files
to six broke old updater/new candidate and new selector/old rollback compatibility.
The reviewed replacement retains four management files and records the two guest
boot units in separate optional versioned metadata. Implementation passes 181
focused/consumer tests and independent review, including real ext4 fixtures;
full historical readers against production artifacts now pass; physical rollback
remains pending. [Compatibility evidence](evidence/guest-boot-metadata-compatibility-20261112.json)
retains the strict four-plus-two contract and fixture limits. Current source has
four management records and two guest boot records; the earlier six-record
uninstalled representation is retired.

Single yes/no target confirmation, plan-bound optional writer readback and hash
progress now pass independent review and **94 privileged focused checks** with
no skips/errors/failures. Default staging flushes but reports zero verified bytes;
selection retains full root authentication. The verified private-loop fixture
explicitly requests readback. [Operator-flow evidence](evidence/maintenance-confirmation-progress-20261112.json).
These changes are installed in B; physical maintenance trials remain pending. The combined frozen source `2996365` now passes
**3589 privileged Ubuntu tests**, five shell suites and five diagnostic cases
without skips/errors/failures. All independent source reviews remain pinned;
terminal unit and receipt copies are verified off-device.
[Combined evidence](evidence/candidate-integration-validation-20261112.json).
Two fresh A/B root constructions now match all corresponding raw/gzip bytes and
pass independent full filesystem/package/timestamp checks and whole historical/
current reader interoperability. Encoded artifacts and review receipts have
verified copies on both hosts. [Candidate artifacts](managed-root-candidate-20261112.md)
and [independent review](evidence/managed-root-artifact-review-20261112.json).
Candidate B now boots on hardware; candidate A remains an unbooted artifact.
Physical rollback/return and maintenance trials remain pending.

The exact retained normalized A gzip, original producer manifest and selector
are available on codex and the builder for reverse-compatibility checks. A bounded
range recovery reproduced the historical gzip hash from the authenticated retained
full image, without expanding the whole256GB carrier or touching physical storage.
[Recovered input custody](evidence/normalized-a-compatibility-custody-20261112.json).
Production artifact/actual-reader checks now pass; hardware acceptance remains
required.

Two fresh managed power-key guest builds match byte for byte with verified
off-device copies. [Build evidence](evidence/managed-fixture-build-20261112.json).
Its shutdown path remains hardware-unproven.

## Live board and constraints

- **Current boot:** managed B, `1095a827-6f92-486d-9ad3-2463a95c2d5e`,
  `root@192.168.1.118` with original strict host-key alias `192.168.1.116`.
  SD is physically removed. Read-only p3 root and pinned runtime pass; p5 is
  mounted `rw,nosuid,nodev,noexec`. Initial preparation dispatched no guest.
  Manual `firstkvm` is stopped at checkpoint **5**, manifest `88c1ea17…9c946`,
  disk `2d06d539…5b66`, nine history records. Its cohort remains unchanged
  after B storage preparation; verified backups exist on both hosts.
- **Target:** A733, observed 12 GiB/eight CPUs. SD is SU02G,
  **1977614336 bytes**, CID `035344535530324780716f216e00a8f5`.
  NVMe is SPCC M.2 PCIe SSD, **256060514304 bytes**, serial
  `134807190FC700149277`, firmware EDFM20.0, 512/512 sectors. Diagnostic inventory
  is sufficient; leave heatsinks attached. Reported supply is 67 W USB-PD with
  passive 10.4 mm six-layer whole-board copper cooling; negotiated power is unmeasured.
- **Boot route:** SPI boot0/EL2 firmware/U-Boot → NVMe p1 `/boot/` →
  selected read-only NVMe B and writable STATE. NVMe BOOT remains
  `cc2ad9…26c313`. Original blank restoration, SD recovery and factory SPI
  reinstallation passed; fresh post-cold flash `2d9e2a…92386` matches factory
  outside validated 2 KiB vendor calibration metadata. Preserve original and
  calibrated backups and the off-device 2 GB recovery card.
  [Current images](current-images.md).
- **Serial:** bridge `orangepi@192.168.1.117`, `ttyS3-capture.service`, volatile
  `/var/log/ttyS3.log`; native relay `a733-uart-relay-20261109-01.service` retains
  output off-board. UART help, RAM shell and controlled SysRq recovery pass;
  a fully locked kernel and hardware reset are outside that proof. AP-RESET is
  not exposed on the reviewed 40-pin header; no wiring was requested.
  [Procedure](remote-serial-control.md).
- Known board SD/NVMe/SPI writes remain authorized. Revalidate exact targets and
  retain independent review; ask yes/no only for unapproved targets or scope.
  SPI-to-NVMe with SD removed now operates on the recorded hardware.
  The registered source-backed SD recovery policy replaces the earlier
  USB-FEL-specific prerequisite. Physical SPI programming and SD-priority recovery now pass.
  [Programming evidence](evidence/spi-programming-20261111.json). [Programmer](known-spi-programmer.md).

## Acceptance evidence

| Gate | Current evidence |
| --- | --- |
| Diagnostic fits actual 2 GB SD; all eight CPUs at EL2 | Passed; oversized vendor images were not used |
| Actual nonroot KVM CPU/disk/network, poweroff/checkpoint/restart | [SPI-host counters 8/9 and checkpoint/restart pass](evidence/spi-operational-20261111.json). [Initial passes](kvm-vgic-results-20261108.md), [restored host counter 6](paired-host-restore-20261110.md), and normalized B/rolled-back A counters 7/8 with retained zero exits |
| Management and identity | Installed DHCP, NTP, pinned SSH and both authorized keys pass; any-subnet configuration retained through update/rollback |
| Eight-hour workload | [Passed from retained >20-hour data](evidence/eight-hour-endurance-result-20261109.json); 2423 samples reach 72669 guest seconds/cycle 2422, with temperatures/memory/disk/network/KVM and post-run health/SD checks. Original 86400-second metadata remains historical. Operator stop is not guest-generated ENDURANCE_PASS or clean guest poweroff. Old timer is stopped/disabled; no rerun |
| Original host backup/restoration | [Immutable offline cohort](evidence/offline-host-preservation-20261110.json) and [physical interrupted restore/full retry/boot/KVM](paired-host-restore-20261110.md) pass |
| Changed inactive update and manual rollback | [Physical p3 interruption, A recovery boot, complete B retry, selector interruption before/after publication, cancellation, B boot/KVM and old-A rollback/KVM pass](physical-host-update-rollback-20261110.md); handled SIGTERM, not power cuts |
| Guest backup/restoration | [Counter-3 archive restored and advanced 4/5](evidence/corrected-host-guest-restore-20261108.json). [Counter-5 archive](evidence/current-counter5-backup-20261109.json) and [forensic copies](guest-preservation-20261109.md) remain preserved; [fresh archive restoration and actual KVM 5→6→7/checkpoint/restart now pass](evidence/normalized-guest-restore-20261110.json) |
| Failed health/offline root selection, missing NVMe, dirty journal | [Controlled SSH/offline-root recovery/KVM 9 passes](controlled-ssh-recovery-20261110.md). [NVMe-driver fault → serial shell → ordinary B → KVM 10 passes](missing-nvme-recovery-test.md); physical disconnection is not claimed. [Actual dirty crash → protected export → ext4 replay/marker → KVM 11 passes](dirty-state-recovery-trial.md), with verified custody. Initial raw inspector counter refusal remains retained; no broader claim |
| Fresh normalized full installation and deployment reproduction | [Matching full streams](evidence/normalized-full-nvme-image-20261109.json) and [isolated composition](evidence/isolated-nvme-composition-20261109.json) pass. B alone has booted through the updater; [full installation](evidence/normalized-host-install-20261110.json), [fresh restored KVM](evidence/normalized-guest-restore-20261110.json) and [independent procedure reproduction](independent-deployment-20261110.md) pass |
| Protected-SD final 10 warm and 10 cold boots | [Ten guarded warm reboots](evidence/final-warm-boots-20261110.json) and [ten operator-performed cold boots](evidence/final-cold-boots-20261111.json) pass independent serial/device/release review. Each cold cycle has clean UART powerdown, physical USB-C removal attestation and a distinct fully ready boot |
| SPI removed-SD boot or immutable SD | Immutable-SD fallback passes. [Final cold-series comparison](evidence/final-cold-boots-20261111.json) matches complete-card SHA `401df0…64be5` and prefix `ab5e8c…ad207`, with Linux write/discard/flush fields zero. [Firmware audit](sd-firmware-write-policy.md) passes within its stated limits. [Cold metadata custody](evidence/final-cold-custody-20261111.json) is verified off-device. [SPI/NVMe operational cold/warm boot and KVM now pass](evidence/spi-operational-20261111.json) |

The original host cohort and counter-5/forensic archives remain private and
immutable on the Ubuntu builder. Later counter 6–11 changes are disposable
functional-test data with retained evidence/checkpoints. The fresh normalized installation has returned from RAM to the host. Completed cases have private raw receipts in their linked `out/` directories
and verified second copies on the builder.
Do not restore old STATE alone over a different root pair.

[Controlled recovery details](controlled-ssh-recovery-20261110.md) preserve the
UART parsing anomaly, ext4 warning, evidence-path limitation and exact scope.
The earlier uncontrolled SSH failure remains unexplained.

## Findings and release inputs

The prior old A exposed wildcard 5355 because its resolver directory was 0700.
The normalized fix now passes physically on **both B and fresh A**: directory
0755, global LLMNR/mDNS disabled and no 5355/5353 listeners. Root password stays
locked and SSH key-only. [Prior policy/update result](physical-host-update-rollback-20261110.md).
The final host passes release/device guards after warm and cold qualification.
The first cold invocation used a stale UUID and refused before poweroff; the
[cold guide](cold-boot-validation.md) now reads the current UUID at launch.
Installer RAM serial retained three CMA allocation failures whose cause remains
unexplained. The successful install/flush/hash receipts and completed hardware
qualification are retained with that limitation.

Legacy serial showed orphan cleanup despite `ro,noload`. Protected bootstrap
now enforces block RO before STATE probing; real ext4 software fixtures and
physical protected boot pass. [Actual dirty-journal recovery](dirty-state-recovery-trial.md)
now passes with original dirty-source custody, protected exporter counter equality,
kernel replay, exact marker and KVM. The raw debugfs inspector refused three
zero-sector flushes; its refusal and source-backed interpretation are retained.
[Protection](evidence/state-block-protection-20261109.json).

Before reinstall, dirty-trial recovery SMART showed critical warnings/media errors 0, unsafe shutdowns 80
and admin errors 162. Prior invalid-field entries had baseline status/parameter;
the exact causes of increments are not proven. That older return console also reported
an original persistent journal corrupted/unclean and renamed/replaced; byte export
does not prove application-journal integrity or identify its cause.

The selected full distribution is `out/normalized-nvme-candidate-20261109/`:
545581074-byte gzip SHA-256
`3bc02f46d272dcac591d435fff2b1bc98d4fb048569fb2120746145929a8ad64`,
manifest `3cbe28…ad08f`. It is a factory distribution requiring the reviewed
same-board identity helper. The full installation, original identity preservation and first protected-SD
normalized A boot and restored guest KVM/checkpoint/restart now pass.
[Deployment guide](deploy-protected-20261109.md). Preserve the compatible kernel,
DTB and firmware; root provenance's old diagnostic DTB is not the current boot DTB.
Licensing/source inventory remains incomplete for 26 vendor boot inputs and two
exact Linaro source revisions; nearby GNU releases are not substitutes. No public
binary redistribution clearance is claimed. [Source inventory](toolchain-sources.md).

## Resources and next actions

Latest privileged Ubuntu 22.04 suite: **3659 tests**, zero skips/errors/failures,
five shell suites and five diagnostic cases. Collector endpoint/atime changes,
source `d7ae1e5`, tree `c2f03d4`, 1139 locked inputs and retained first-run
failure are recorded in [validation evidence](evidence/spi-collector-address-validation-20261113.json).
Minima were 21,046,808,576 bytes free and 6,749,331,456 bytes MemAvailable.
The outer builder now has 849 packages; it does not match the qualified
842-package image-build lock. Candidate roots remain frozen at `2996365`;
these software checks establish no physical acceptance.

The [seven updated outer-builder package archives](outer-builder-package-supplement.md)
and complete signed indexes now have verified copies on both hosts. Independent
signature/hash/tuple [review passes](evidence/builder-outer-supplement-independent-review-20261113.json).
Those archives cover the historical 842-package inventory; the current outer
host has since advanced to 849 packages. Historical locks remain unchanged;
old 835 archive bytes were not rehashed. Fresh outer-host provisioning remains open.

Production root construction/repetition and artifact/reader verification pass.
An [independent operator reproduced the documented root build](evidence/independent-managed-root-reproduction-20261113.json)
from a fresh checkout/worktree using the declared retained inputs. All 133 files,
manifest and complete gzip expansions match; full filesystem readback passes.
This completes the build-procedure portion of independent reproduction on the
qualified outer/frozen-inner environment. Physical procedure reproduction and
fresh outer-OS provisioning are separate scopes.

The [initial managed-B deployment procedure](managed-root-deployment.md) now
replaces the research transfer draft. It authenticates all 40 installed legacy
runtime files before importing the historical target guard, checks live identity
before STATE writes, and expands B once directly into its final sparse candidate
with progress, exact length/hash and fsync. Prepared metadata is reused; the
duplicate onboard preparer pass is omitted. Ten Bash snippets and two Python
blocks parse, and historical CLI/runtime and artifact pins pass
[independent review](evidence/managed-root-deployment-recipe-review-20261113.json).
The reviewed live staging, selection and first B boot now pass. BOOT hashes
match before/after; staging read back the complete B root and selection/journal
publication is durable. [Physical receipts](evidence/post-reinstall-SPI-managed-B-20261113.json).
[Prepared B metadata](evidence/managed-candidate-deployment-preparation-20261112.json)
has verified custody for direct sparse expansion, avoiding an extra onboard
preparer copy/hash. The [external qualification helper](managed-boot-qualification.md)
authenticates a completed current-boot automatic cohort and retains thirteen
bound records. The separate [power helper](spi-power-request.md) passes fifteen
focused checks and independent native EX/cohort/intent probes with explicit
platform substitutions. [Review and custody](evidence/spi-power-request-validation-20261112.json).
Its shutdown gate stays closed through uncertainty; EX cleanup precedes the
single power command. No real power command has been issued by these helpers.
The [native SPI collector](spi-boot-qualification.md) passes the integrated
suite and independent review, including cross-tag exclusion, request uncertainty,
readonly closed-gate observation, bounded transport/progress and ordered UART
with complete-tail checks. No real power command or target collection has run.
The [artifact-bound managed fixture setup](managed-fixture-deployment.md) has
validated inputs, an independent initial-manifest reference and reviewed CLI
ordering. Guarded STATE copying and initial policy/storage preparation now pass;
the registry is empty and no managed guest has been dispatched.
The [boot ledger](evidence/spi-boot-credit-ledger-20261112.json) records conditional
existing route credit; final managed-build credit remains zero.
Recovery-card boot, original blank restoration and subsequent SD recovery pass.
SPI reinstallation, SD-removed boot/manual KVM and initial B deployment pass.
Actual managed KVM, maintenance/recovery exercises, final boot counts and
independent physical procedure reproduction remain required.

The external collector now supports the changed DHCP endpoint while preserving
strict historical SSH identity. Independent reviews and **3659 privileged Ubuntu
tests**, five shell suites and five diagnostic cases pass. The first run’s
atime-related failure and current 849-package builder inventory are retained
explicitly; this is not a new root build or hardware boot credit.
[Collector evidence](evidence/spi-collector-address-validation-20261113.json).

Codex has more than 22 GB available at this validation. Keep bulk sources, images and backups on the builder. Its LV is 128819658752 bytes
with 4265607168 bytes VG free. All free builder capacity is approved for use and
expansion after exact-target revalidation and independent review. Native codex
LVM is outside that approval. Retain resource guards and begin compilation with
two jobs. Installation uses one progress-enabled streamed write with optional
troubleshooting readback. The eight-hour workload remains accepted from existing
data and requires no rerun.

## Operation after acceptance

Use [current images](current-images.md), the [reproduced installation guide](deploy-protected-20261109.md),
[guest operations](guest-operations.md), [maintenance/rollback](normalized-maintenance-20261109.md)
and [backup/recovery](host-state-backup.md). Preserve original private host,
guest and forensic archives off-device. Qualifying firmware, kernel/DTB and
storage identities are recorded in the evidence. Changes to that combination
need appropriate validation before extending the acceptance claim.

SPI programming, SD-priority recovery and SD-removed operation now pass.
Retain the original and calibrated SPI snapshots. The strict initial programming
plan is stale after vendor metadata initialization. Keep source/license inventory
limits explicit. The [completed release audit](evidence/release-acceptance-20261111.json)
and [construction index](build-reproduction-status.md) identify the evidence and
precise reproduction scope.
