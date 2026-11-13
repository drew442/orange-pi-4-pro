# Implementation status

Updated 2026-11-13. **SPI/NVMe boot works with SD removed; the new managed guest
workflow is not production-qualified.** Managed B is running, but its first
managed start refused before queueing on systemd dependencies. The guest is
disabled and has never run. The reviewed fix passes full validation; new root
construction and deployment remain next. No operator action is needed during this preparation.

## Current board

- SSH: `root@192.168.1.118`, original strict host-key alias `192.168.1.116`.
  Boot `1095a827-6f92-486d-9ad3-2463a95c2d5e`; read-only NVMe root B p3.
  Candidate `2996365` authenticates 85 tool files, four management files and two
  guest boot units. SSH, NTP, eight CPUs and EL2/VHE pass.
- SD is physically removed. Preserve the SU02G recovery card, 1977614336 bytes,
  CID `035344535530324780716f216e00a8f5`; attached heatsinks stay in place.
- NVMe: SPCC M.2 PCIe SSD, serial `134807190FC700149277`, 256060514304 bytes,
  firmware EDFM20.0. STATE p4 UUID `360dfb39-f49f-487e-b307-0bb887c854c5`;
  guests p5 UUID `298b281a-d10e-44eb-bdb9-285e27eaafad`.
- Normalized A p2 and NVMe BOOT remain preserved. BOOT SHA `cc2ad9…26c313` is
  unchanged across B installation. No BOOT/kernel/DTB replacement is needed.
- UART bridge: `orangepi@192.168.1.117`, `ttyS3-capture.service`,
  `/var/log/ttyS3.log`; native relay retains output under
  `out/serial-recovery-20261109/`. Keep capture and its storage cap monitored.
- Reported power is a 67 W USB-PD adapter; cooling is passive 10.4 mm six-layer
  whole-board copper. Negotiated voltage/current is unmeasured.

## Physical evidence and retained recovery

| Gate | Result and boundary |
| --- | --- |
| Earlier protected-SD release | Independent installation reproduction, update/rollback/recovery, ten cold/ten warm boots and unchanged SD evidence pass. This covers that earlier route/configuration. [Release audit](evidence/release-acceptance-20261111.json). |
| Endurance | Retained >20-hour workload satisfies the eight-hour gate. No qualifying rerun; cap every future planned test at eight hours. Production guest lifetime remains unlimited. |
| SPI operation | Actual SD-removed cold/warm boot and nonroot KVM CPU/disk/HTTP, clean shutdown and checkpoint/restart pass. [Operational evidence](evidence/spi-operational-20261111.json), [off-device custody](evidence/spi-operational-custody-20261111.json). |
| Revised SPI recovery | Metadata-aware original blank restoration, SD recovery and firmware reinstallation pass full readback/close. [Restoration](evidence/spi-blank-restoration-and-sd-recovery-20261113.json), [reinstallation](evidence/spi-reinstall-and-poweroff-20261113.json). |
| Post-reinstall boot/KVM | Confirmed SD-removed SPI boot `ae6ef902-51ce-45cf-88a9-62f7f390eded`. Actual UID/GID60001 QEMU PID2111 advanced `firstkvm` counter 9→10, passed CPU/disk/HTTP and guest-owned raw exit 0. Checkpoint5 is durable with nine history records and verified copies on codex and builder. A subsequent CP5 restart is not claimed. [Physical receipts](evidence/post-reinstall-SPI-managed-B-20261113.json). |
| Managed B deployment | Complete B staging/readback, durable selection and first authenticated B boot pass. Initial policy/storage preparation produced an empty registry, open admission and no dispatch; all 13 retained `firstkvm` files stayed unchanged. Same physical receipt above. |
| Managed guest execution | **Pending.** The first start was rejected before queueing; no KVM run or automatic-boot credit exists for this managed cohort. |

The first November 13 removal attempt still had SD installed and receives no SPI
credit. The operator clarified, removed it and booted the confirmed SPI route.
The earlier unexplained November 8 SSH failure remains unresolved.

SPI is XM25QU128C, JEDEC `204118`, `/dev/mtd0`, char90:0, 16777216 bytes with
65536-byte erase units. Vendor boot changes only its validated 2 KiB calibration
area `[260096,262144)`. Use a fresh reviewed metadata-aware live-flash plan for
future writes; initial factory plans become stale after boot.
The latest retained full post-reinstall snapshot is `2d9e2a…92386`, captured on
A before the later B warm boot. Its firmware matches factory outside valid
calibration metadata. Original blank, factory and calibrated copies remain
preserved off-device. [Policy](spi-calibration-policy.md), [procedure](spi-sd-boot-20261111.md).

`firstkvm` remains manual and stopped: CP5 manifest `88c1ea17…9c946`, disk
`2d06d539…5b66`. Its complete backup archive is `5736eafe…c6d926`, verified under
`/home/drew/a733-checkpoint 5-backup-20261113-01` on codex and
`/var/lib/a733-checkpoint 5-backup-20261113-01` on the builder. Preserve these and
the exact [normalized A rollback input](evidence/normalized-a-compatibility-custody-20261112.json).

## Managed deployment findings

Initial account/payload enrollment succeeded, but independent pre-launch review
caught guest port 80 where the pinned fixture requires 8080. Before registration,
the entire unused cohort was moved atomically under private p5 `.enrollment`
quarantine using installed maintenance EX, guest/disk locks and no-replace
publication. It was retained intact; admission reopening was acknowledged.
Corrected enrollment durably published the independent 1024-byte manifest
`a46ae94c…5d8701`. [Correction, regression and receipts](evidence/managed-fixture-port-correction-20261113.json).

Registration disabled, then enablement succeeded. The single explicit start
returned `AdmissionError: loaded storage dependencies differ` with
`queue_attempted=false`, `queue_acknowledged=false`, `queue_uncertain=false`.
No explicit request or run exists. Supported disable succeeded; registry is now
generation 3, sole `autokvm` entry disabled, head ready at sequence 0. Its disk is
the unchanged seed; no guest execution is claimed.

Two actual systemd 252 behaviors caused the refusal: displayed dependency unit
names containing backslashes are quoted/escaped, and `RequiresMountsFor` omits
`Requires` for these manually mounted fragmentless mount units. The reviewed
source fix adds explicit mount `After`/`Requires` and strictly decodes the
bounded display without weakening the contract. A never-started native PID1
probe confirms all required edges and clean removal. The installed root has
not been patched. [Diagnosis/source/native-probe evidence](evidence/managed-systemd-dependencies-20261113.json).

## Software and build evidence

The frozen `2996365` candidate passed **3589 privileged Ubuntu tests**, five
shell suites and five diagnostic cases. It contains the reviewed admission,
managed lifecycle/checkpoint, coherent backup/restore, maintenance, RAM recovery
and four-plus-two management/guest-unit composition. Those software fixtures
remain distinct from physical acceptance.
[Combined validation](evidence/candidate-integration-validation-20261112.json),
[managed restoration](evidence/managed-restore-validation-20261112.json),
[RAM recovery](host-recovery.md), [lifecycle design](guest-autostart-design.md).

Three independent constructions of that candidate reproduce all 133 artifacts,
manifest, raw/gzip bytes and filesystem checks using the declared retained
inputs. Independent build-procedure reproduction passes; fresh outer-OS
provisioning and final physical procedure reproduction are separate scopes.
[Root construction](managed-root-candidate-20261112.md),
[independent reproduction](evidence/independent-managed-root-reproduction-20261113.json).

The external collector endpoint/atime changes passed **3659 privileged tests**,
five shell suites and five diagnostic cases with no skips/errors/failures.
Strict historical SSH identity is preserved when selecting `.118`. The earlier
single failure and atime attribution as inference are retained.
[Collector validation](evidence/spi-collector-address-validation-20261113.json).
This neither changes installed board code nor grants boot credit.

The systemd repair passes independent source review, 121 focused privileged
checks and the native no-start graph probe. The outer-builder adapter passes
independent review and ten focused checks. Their combined privileged validation
passes **3664 unit tests**, five shell suites and five diagnostic cases with
zero skips/errors/failures. Source `11012f6`, tree `2b8a4e7`, 1143 locked inputs,
terminal exited/status0 and verified receipt copies on both hosts are retained.
Minimum free disk was 20,414,304,256 bytes and MemAvailable 6,885,322,752 bytes.
[Integrated evidence](evidence/managed-systemd-outer849-validation-20261113.json).
No new root image is claimed yet.

The Ubuntu 22.04 builder is `drew@build.drewnet.online`. Unattended upgrades
advanced its outer inventory to 849; the exact new lock and seventeen-package
signed supplement have verified copies and independent review. The frozen
842-package replay builder and historical locks remain unchanged. Old images'
842-package outer-build evidence is not a claim about the current host.
[Inventory/provenance](evidence/outer-builder-849-20261113.json),
[host assessment](build-host-assessment.md). The remaining approved VG space was allocated after exact-target/source review
and off-device metadata backup; LV/ext4 now span 133085265920 bytes, VG free0.
Available space was 24,447,950,848 bytes afterward; remeasure before construction.
[Capacity receipt](evidence/build-host-capacity-20261113.json). Begin compilation
with two jobs.

## Next actions and acceptance

Follow [the production plan](spi-production-readiness-plan.md). Final managed
warm/cold qualification counts remain **0/0**; retain the [conditional older SPI
ledger](evidence/spi-boot-credit-ledger-20261112.json) without treating earlier
protected-SD cycles as this configuration's credit.

1. Freeze the validated source, construct matching repaired roots
   and independently verify artifacts/procedure and updated helper pins.
2. Prepare and independently review the exact repair deployment. Preserve
   normalized A, BOOT, STATE, both guest cohorts and all backups. Use supported
   inactive-slot updates; never patch or overwrite the running root.
3. Prove real nonroot managed KVM CPU/disk/network, power-key shutdown,
   checkpoint/restart and separate unattended startup on the repaired root.
4. Exercise coherent backup/restore including interrupted/resumed restoration,
   admission recovery, disable, installed maintenance interruption/retry and
   physical old-A rollback/managed-B return. Preserve manual `firstkvm`.
5. Freeze the final configuration, complete ten qualifying warm/ten cold SPI
   boots with automatic guest evidence, then independently reproduce the final
   physical procedure and reconcile [release acceptance](release-acceptance.md).
   Cold cycles need actual operator USB-C removal attestations.

Known-board SD/NVMe/SPI writes remain authorized by
[the persisted record](evidence/storage-write-authorization-20261108.json).
Only changed targets/scope need a new yes/no request. All free builder capacity
is approved for use/expansion after exact-target revalidation and independent
review; native codex LVM is outside that approval. Retain resource guards.
Installation shows progress and keeps bulk readback optional for troubleshooting;
mandatory SPI verification remains. Do not benchmark the board for installation
bottlenecks, remove heatsinks or repeat accepted endurance testing.

Exact source/license inventory remains incomplete for 26 vendor boot inputs and
two Linaro source revisions. Keep these limitations explicit; local production
qualification does not establish public binary redistribution clearance.
[Source inventory](toolchain-sources.md). Use [current images](current-images.md)
and the linked operation/recovery guides; do not install an unqualified new
candidate merely because host tests passed.
