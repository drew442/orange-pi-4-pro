# Implementation status

Updated 2026-11-12. Last boot/KVM validation was 2026-11-11.
Current physical state: cleanly powered off for the operator to insert the known
SD recovery card and restore power. UART confirms complete unmount/powerdown.
[Preparation evidence](evidence/spi-recovery-preparation-20261112.json).

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
[metadata policy](spi-calibration-policy.md). Physical SD recovery/restore/reinstall,
unattended guest deployment and final qualification remain pending.

The [guest lifecycle design](guest-autostart-design.md) passes independent
architecture review. The following software components are reviewed and tested;
**all remain uninstalled**.

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
| Single managed run controller | [Evidence](evidence/managed-service-validation-20261112.json). **3077** privileged Ubuntu tests and shell/diagnostic suites pass. Independent native shutdown/cleanup probes pass; healthy polling no longer spawns PID1 queries. Actual retained locks, run, fork/QMP surrogate and checkpoint compose with explicit platform/credential substitutions. No installed unit or hardware qualification. |
| Pre-p5 maintenance and normal activation | [Evidence](evidence/guest-activation-admission-validation-20261112.json). **3120** privileged Ubuntu tests and shell/diagnostic suites pass. Independent helper/wrapper reviews and actual private-loop interruption/retry/idempotency, preservation and cleanup pass. Protected registry must match reviewed target; binding and reopening follow all target cleanup. Bootstrap borrowing remains separate. |

Current packaging has 77 sources, 78 runtime files, 20 entrypoints and the historical exact
39-source boundary. Reviews retain resolved findings, preliminary failures and
verified off-device receipts. Native surrogate/ARM user-mode results ran no
QEMU guest or KVM and do not establish deployment acceptance.
[Portable recovery and boot account preparation](evidence/portable-recovery-validation-20261112.json)
now pass independent review and **3210** privileged Ubuntu tests, five shell
suites and five diagnostic cases without skips/errors/failures. Actual disposable
kernel loops prove public RAM selector publication/cleanup and protected p5
clean-absence/dangling-marker refusal with unchanged bytes; explicit platform
substitutions remain. BOOT/paired restore/RAM selection retain offline exclusion
through cleanup; paired restoration refuses managed metadata before writable
roots/STATE. The p5 fixture lazy-detach cleanup finding is resolved and retained.
Boot accounts reserve all published identities before sorted batches and are
composed by the candidate preparer. Coherent managed restoration,
boot services, inactive-root deployment and physical acceptance remain pending.
The outer service must finish/check supervisor/run/controller/lease cleanup;
bootstrap must publish manual namespace pins before opening admission.

A separate [explicit storage-policy schema](evidence/storage-policy-schema-validation-20261112.json)
passes eight native tests and 34 independent parser probes. It records the
caller-selected NVMe/model/size and STATE/p5/serial tuple as data only. Protected
STATE policy reader and one-process preparation are now packaged as source;
installer publication and installed boot services remain pending. Schema parsing
itself supplies no device or launch authority.

[Boot preparation and runtime budget evidence](evidence/boot-prepare-validation-20261112.json)
now records **3292** privileged Ubuntu tests, shell/diagnostic suites and independent
reviews passing without skips/errors/failures. One original bootstrap EX spans
activation, state/accounts, same-family readiness, protected RAM units and manual
namespace pins; all preparation cleanup precedes opening. Repeated preparation
is a verified no-op, even with an active SH lease. Checkpoint cleanup and combined
publication/close receipt findings are resolved. The actual disposable Ubuntu
systemd/cgroup probe passes as UID/GID 60002 with zero capabilities: kernel
memory/swap/task limits match, all 15 guard FDs close, and owned runtime fragments
and cgroups are removed. This ran no VM. Initial policy publication, coherent
managed restore, candidate build and physical acceptance remain pending. Manual unit rendering remains explicit; capability sufficiency
and A733 mount/shutdown ordering are hardware gates.

[Guest service integration](evidence/guest-service-validation-20261112.json) passes
independent review and **3348** privileged Ubuntu tests plus shell/diagnostic
suites. Root recipes include both conditional boot services and six management
runtime files. Dispatch retains fresh metadata/SH custody and a durable one-shot
intent before nonblocking queueing. Teardown uses monotonic shutdown closure,
readonly immediate EX, exact target and visible-process census, then at most one
normal native unmount. Gate-exit lease leakage, duplicate stop hooks and empty
Exec-array display findings are resolved. The first full run's four historical
NVMe fixture failures are retained; only fixture metadata changed, and current
roots still require both new service records. Private systemd255 graph/display
checks do not establish target systemd252 or physical mount behavior. All new
services remain uninstalled. Policy publication packaging, coherent managed
restore and explicit guest-management commands are next, followed by candidate
construction/deployment and physical qualification.

Two fresh managed power-key guest builds match byte for byte with verified
off-device copies. [Build evidence](evidence/managed-fixture-build-20261112.json).
Its shutdown path remains hardware-unproven.

## Live board and constraints

- **Last running state (now powered off):** normalized A at `root@192.168.1.116`, original pinned identity,
  SPI boot `5858af7a-977e-4a9b-971e-6d71ae7858d3`. SD is absent. Final release,
  NVMe A/STATE, SSH/NTP, eight-CPU EL2/VHE and KVM readiness guards pass.
  The SPI-host guest tests ran QEMU PIDs 2066/2044 as locked UID/GID 60001 with
  actual VM/vCPU/VGIC descriptors. Both guests requested clean poweroff and
  exited zero. Current checkpoint **4**, manifest `c47520b6…bc60f`, disk
  `fb46ba77…e6efa`, has eight verified history records. Guest is stopped and its
  current RAM unit is rendered for the next launch. Recreate accounts/units
  explicitly after host reboot. [Guest operations](guest-operations.md).
- **Target:** A733, observed 12 GiB/eight CPUs. SD is SU02G,
  **1977614336 bytes**, CID `035344535530324780716f216e00a8f5`.
  NVMe is SPCC M.2 PCIe SSD, **256060514304 bytes**, serial
  `134807190FC700149277`, firmware EDFM20.0, 512/512 sectors. Diagnostic inventory
  is sufficient; leave heatsinks attached. Reported supply is 67 W USB-PD with
  passive 10.4 mm six-layer whole-board copper cooling; negotiated power is unmeasured.
- **Boot route:** SPI boot0/EL2 firmware/U-Boot → NVMe p1 `/boot/` → selected
  read-only NVMe root and writable STATE. NVMe BOOT remains `cc2ad9…26c313`.
  Programmed factory SPI hash is `83edcd…05e90`. The current post-warm snapshot
  is `e829cf…275a`; all differences are vendor metadata at `[260096,262144)`.
  The known 2 GB SD stays available off-device for recovery.
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

Latest privileged Ubuntu 22.04 suite: **3348 tests**, zero skips/errors/failures,
five shell suites and five diagnostic cases; all five TOMLs parse natively.
[Guest service evidence](evidence/guest-service-validation-20261112.json) retains
terminal units, source locks, independent reviews, initial failed run and verified
receipts on codex and the builder. Sample minima are 30,480,363,520 bytes free
and 6,628,093,952 bytes MemAvailable. These are software checks, not physical
recovery or unattended KVM acceptance. Recovery-card boot awaits the operator;
UART capture remains active with the unchanged clean-powerdown tail. Continue
policy-publication packaging, coherent restore and guest control while physical
recovery is pending, then construct/deploy the reviewed inactive root.

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
