# Implementation status

Updated 2026-11-13. **SPI/NVMe boot works with SD removed; managed production
qualification remains incomplete.** Preserved normalized A is running while the
verified F349 trial is prepared for inactive B. Its permit-barrier and bound-stop
changes pass combined validation. No managed KVM execution or final boot-series
credit is claimed. No operator action is currently needed.

## Current board and retained recovery

- SSH: `root@192.168.1.118`, strict original host-key alias `192.168.1.116`.
  Boot `03cfd8ac-1efd-4eed-a758-3ec874aa4154`; normalized A p2 is read-only.
  Its complete 40-tool/four-management-file cohort authenticates. NTP passes;
  p5 is unmounted and admission absent, as expected for this older root.
- NVMe: SPCC M.2 PCIe SSD, serial `134807190FC700149277`, 256060514304 bytes,
  firmware EDFM20.0. STATE p4 UUID `360dfb39-f49f-487e-b307-0bb887c854c5`;
  guest p5 UUID `298b281a-d10e-44eb-bdb9-285e27eaafad`.
- A raw root `a9715b…97decdd` and protected BOOT remain preserved. Compatible
  bootloader, kernel, device tree and EL2 configuration are not replaced.
- SD is physically removed. Preserve SU02G recovery card, 1977614336 bytes,
  CID `035344535530324780716f216e00a8f5`. Leave attached heatsinks in place.
- UART bridge: `orangepi@192.168.1.117`, `ttyS3-capture.service`,
  `/var/log/ttyS3.log`; capped native relay under `out/serial-recovery-20261109/`.
- Supply: reported 67 W USB-PD adapter; passive 10.4 mm six-layer whole-board
  copper cooling. Negotiated voltage/current remains unmeasured.

SPI is XM25QU128C, JEDEC `204118`, `/dev/mtd0`, char90:0, 16777216 bytes,
65536-byte erase units. Vendor boot changes the validated 2 KiB calibration area
`[260096,262144)`. Future programming requires a fresh independently reviewed
metadata-aware live-flash plan. Original blank, factory and calibrated backups
remain off-device. Latest retained post-reinstall full snapshot `2d9e2a…92386`
predates subsequent warm boots and is not a fresh programming input.
[SPI policy](spi-calibration-policy.md), [procedure](spi-sd-boot-20261111.md).

## Physical evidence

| Gate | Result and boundary |
| --- | --- |
| Earlier protected-SD release | Installation, update/rollback/recovery, independent deployment reproduction, ten cold/ten warm boots and unchanged SD pass for that configuration. [Audit](evidence/release-acceptance-20261111.json). |
| Endurance | Retained >20-hour workload satisfies the eight-hour criterion. No qualifying rerun; future planned tests are capped at eight hours. Production guest lifetime remains unlimited. |
| SPI operation | SD-removed cold/warm boot and actual nonroot manual KVM CPU/disk/HTTP, shutdown and checkpoint/restart pass. [Operational record](evidence/spi-operational-20261111.json). |
| Revised SPI recovery | Original blank restoration, SD recovery and metadata-aware reinstallation pass full readback/close. [Restoration](evidence/spi-blank-restoration-and-sd-recovery-20261113.json), [reinstallation](evidence/spi-reinstall-and-poweroff-20261113.json). |
| Post-reinstall manual KVM | Boot `ae6ef902…0eded`; UID/GID60001 QEMU advanced `firstkvm` 9→10, passed CPU/disk/HTTP and guest-owned raw exit 0. CP5 is durable with nine history records and verified copies on codex and builder. CP5 subsequent restart is not claimed. [Receipts](evidence/post-reinstall-SPI-managed-B-20261113.json). |
| Managed root deployments | Original B and repaired `4334a22` B staged, selected and booted with authenticated runtimes. Normalized A remains the rollback root. [Repaired B](evidence/repaired-managed-B-deployment-20261113.json). |
| Managed launch failures | Original B refused before queueing on systemd dependency display/mount edges. Repaired B queued but child exited125 before QEMU during permit publication; durable quarantine succeeded. No managed execution credit. [Dependency diagnosis](evidence/managed-systemd-dependencies-20261113.json), [permit failure](evidence/managed-permit-barrier-failure-20261113.json). |
| Quarantine/admission recovery | Supported initial-baseline restoration committed, retained old payload/lifecycle and CP5, and kept `autokvm` disabled at generation 6. Fresh B boot `fad6b1f4…afd85` validated ready sequence 0/open admission and completed restore census. This reconstructed baseline is not a successful managed-backup export. [Recovery](evidence/managed-initial-cohort-recovery-20261113.json). |
| Final managed qualification | **Pending:** actual managed KVM, shutdown/checkpoint/restart, automation and remaining maintenance/backup/reproduction gates. Final warm/cold counts remain **0/0**. |

The first November 13 removal attempt still contained SD and receives no SPI
credit. The operator subsequently removed it. The unexplained November 8 SSH
failure remains unresolved; retain continuous UART during qualification.

`firstkvm` remains manual and stopped: CP5 manifest `88c1ea17…9c946`, disk
`2d06d539…5b66`. Backup archive `5736eafe…c6d926` is verified at
`/home/drew/a733-checkpoint5-backup-20261113-01` on codex and
`/var/lib/a733-checkpoint5-backup-20261113-01` on the builder. Preserve these and
[normalized A](evidence/normalized-a-compatibility-custody-20261112.json).
The unused original port80 cohort and the later failed port8080 cohort are both
retained. The active restored `autokvm` is disabled, initial manifest
`a46ae94c…5d8701`, ready sequence 0 and seed counter 0.

## Current source and build evidence

Frozen **F349** (`f3496f5ff1b3629d5333d26725b9f5494ace9d25`, 1156 tracked files)
adds a 120-second permit-publication budget with a 180-second pre-exec child
barrier, preserving authority checks. Native bound stop signals an authenticated
supervisor pidfd once and observes legal checkpoint manifest transitions. The
external fixture helper waits for real KVM/network observation before one bound
stop; production guests remain unlimited.
[Source review](evidence/managed-bound-stop-and-fixture-completion-20261113.json).

Combined privileged Ubuntu validation passes **3717 unit tests**, five shell
suites and five diagnostic cases, zero skips/errors/failures. All 1160 locked
inputs match before/after; outer 849-package inventory is unchanged. Minimum free disk
22,273,024,000 bytes; MemAvailable 6,685,016,064 bytes. Verified evidence copies
exist on both hosts. [Validation](evidence/managed-native-lifecycle-validation-20261113.json).

One F349 trial construction completes; producer receipts correctly say full
filesystem readback false and reproducibility not established. Separate
independent verification passes all 133 artifacts, both complete root gzip
expansions, both filesystem/semantic checks, historical A/B readers and current
reader on normalized A. Its sole temporary legacy expansion was removed; all
producer originals, three prepared roots and metadata remain.
[Trial artifacts](evidence/managed-permit-root-trial-20261113.json).

F349 B: manifest `333c0f69…57be35`; compressed 143659792 bytes
`5dbf65c9…b8a9c25`; raw 4294967296 bytes `fdecc216…d8d5806`; marker
`396be626…8c347ab8`; selector `e907e3d0…f69da93`. These are NVMe components,
not SD images. Independent fresh-checkout construction/comparison follows a
working hardware trial, giving two constructions and documented reproduction.

The collector now isolates its completion packet from the power helper's exact
two-file namespace. Artifact-derived qualifier/module/marker/selector pins pass
independent review and **100 focused privileged checks**. Combined validation of
that later external change and physical invocation remain pending.
[Fixture completion](managed-fixture-completion.md).

The builder remains Ubuntu 22.04 `drew@build.drewnet.online`, exact849 package
lock plus signed seventeen-package supplement; historical 842-package frozen replay
inputs remain unchanged. LV/ext4 is 133085265920 bytes and VG free space zero. Reviewed
retirement removed only twelve redundant verifier scratch copies, retaining all
producer originals/backups; free space afterward was 22,408,658,944 bytes.
Remeasure dynamic disk/memory guards for each construction; begin with two jobs.
[Builder](build-host-assessment.md), [retirement](evidence/redundant-verifier-scratch-retirement-20261113.json).

## Next actions and acceptance

Follow [the production plan](spi-production-readiness-plan.md):

1. Decode the verified F349 B component once into private STATE, review the exact
   inactive-p3 write and selection, and boot it while preserving normalized A.
2. Prove real nonroot managed KVM CPU/disk/HTTP, guest-owned power-key shutdown,
   durable checkpoint and explicit restart; separately prove automatic startup.
3. Exercise coherent completed-cohort backup/export/restore, interrupted/resumed
   restoration, disable, installed maintenance interruption/retry, restored old-A
   rollback and managed-B return. Preserve `firstkvm` and all recovery evidence.
4. Independently reproduce the frozen construction and compare all artifacts;
   complete later external collector combined validation and freeze final setup.
5. Reach ten qualifying warm/ten cold SPI boots with automatic guest outcomes,
   retaining ordered UART and current firmware/storage identity. Cold transitions
   require real operator USB-C removal attestations. Credit only applicable boots.
6. Independently reproduce the final physical procedure, reconcile
   [release acceptance](release-acceptance.md), commit and push validated changes.

Known-board SD/NVMe/SPI writes remain authorized by
[the persisted record](evidence/storage-write-authorization-20261108.json).
Only changed targets/scope need a new yes/no request. All free builder capacity
is approved for use/expansion after exact-target revalidation and independent
review; native codex LVM is outside that expansion approval. Keep resource guards.
Installation shows progress and keeps bulk readback optional; the older installed
A updater still requires legacy readback, and selector authentication is mandatory.
Do not benchmark installation bottlenecks, remove heatsinks or rerun accepted
endurance testing.

Exact source/license inventory remains incomplete for 26 vendor boot inputs and
two Linaro revisions. Local qualification does not establish public binary
redistribution clearance. [Source inventory](toolchain-sources.md),
[current images](current-images.md).
