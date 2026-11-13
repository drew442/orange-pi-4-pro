# Implementation status

Updated 2026-11-13. **SPI/NVMe boot works with SD removed; managed production
qualification remains incomplete.** The board runs preserved normalized A while
an inactive-B repair is prepared. RAM tracing identified the nonroot metadata
permission failure. The reviewed packaging correction and deterministic collector
fixture are pushed as `6c0a7e9`; fresh combined Ubuntu validation02 passes. One corrected construction and
independent full artifact/filesystem/reader verification pass; inactive-B writing
and real managed KVM remain pending.
The failed validation01 and all failed guests remain retained. No operator action
is currently needed. No managed KVM or final managed boot-series credit is claimed.

## Current board and retained recovery

- SSH: `root@192.168.1.118`, strict original host-key alias `192.168.1.116`.
  Boot `aa743db3-abae-4991-a467-5357c0a6c780`; normalized A p2 is read-only.
  Its complete 40-tool/four-management-file cohort authenticates. NTP passes,
  registry18 remains disabled and p5 is unmounted. Inactive B awaits the corrected
  artifact. [A boot](evidence/permission-repair-preserved-A-boot-20261113.json).
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
retained. Four F349 launches produced the same exit1/74-byte console fingerprint before
QMP binding; none receives KVM credit. The fourth, diagnostic run captured the
exact error: `Permission denied: 'orangepi-hypervisor'` from the nonroot launcher.
The root-owned metadata directory is 0700 and its marker 0600. The trace proves
successful setpriv/Python exec, retained attachment and clean detachment with no
signals, dropped bytes or capture errors. The reviewed packaging fix sets the public metadata directory/marker to
0755/0644 before both image snapshots. Thirty privileged payload checks and
24 metadata checks pass, including actual UID60002 authentication and six denied
mutations. Full Ubuntu validation and a new build remain pending; no live root
permission bypass is applied.

Supported restore `2d3a95df…2b7c` committed cleanly after the fourth failure,
retained its old payload/lifecycle and kept manual CP5 unchanged. Fresh SD-removed
B boot `26acaf3d…f62d` passed the complete runtime, generation18 disabled/ready0,
open admission and no-queued-guest checks. The earlier supervisor cleanup error
remains retained in its failed-run record; fresh boot cleared stale RAM permits.
[Diagnosis](evidence/managed-launch-permission-diagnosis-20261113.json),
[latest recovery](evidence/managed-permission-cohort-recovery-20261113.json).

## Current source and build evidence

The first full `e296ae0` validation ran 3733 tests with one failure in the external
collector's source-mutation fixture; shell/diagnostic stages did not run and no
construction was released. Native reproduction proves a same-timestamp fixture
assumption while captured bytes remained correctly pinned. The reviewed test-only
mtime baseline fix passes 52 privileged collector tests and 2,000 diagnostic
mutation cases. Fresh `6c0a7e9` combined validation02 now passes **3733 unit
tests, five shell suites and five diagnostic cases**, with zero skips/failures, all
1174 source locks matching before/after and exact849 inventory retained. Minimum
free disk 19,111,034,880 bytes; MemAvailable 6,590,410,752 bytes. Evidence is fsynced
on both hosts; retain failed01 separately.
[Fresh validation](evidence/permission-root-combined-validation-20261113.json).
[Failure and correction](evidence/collector-mutation-fixture-correction-20261113.json).

One `6c0a7e9` construction passes independent verification of 133 artifacts, both
complete gzip expansions, both filesystems and five current/historical reader
results. Both metadata directories/markers are root-owned 0755/0644. B raw
`0cfef33e…d7d11b`, gzip143660160 bytes `6e203430…654f1`, marker
`2ecff261…bf9501`, selector `64eb90f2…35dd0`. External helper pins match this
artifact and pass independent review plus 84 privileged focused tests; their new
combined validation remains pending. No new B hardware/KVM credit yet.
[Corrected trial](evidence/permission-root-trial-and-artifact-review-20261113.json).

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

The collector isolates its completion packet from the power helper's exact
two-file namespace. Artifact-derived qualifier/module/marker/selector pins pass
independent review and **100 focused privileged checks**. Later combined Ubuntu
validation passes **3730 unit tests**, five shell suites and five diagnostic cases,
zero skips/errors/failures, with all 1166 source inputs and 849-package inventory
unchanged. Evidence is fsynced on both hosts; physical invocation remains pending.
[Combined validation](evidence/managed-permit-collector-validation-20261113.json).
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

1. F349 B staging, inactive-p3 write/readback, durable selection and SD-removed
   boot pass. Retain [the deployment record](evidence/managed-permit-B-deployment-20261113.json).
2. Complete full Ubuntu validation and new root construction for reviewed
   the public-metadata correction and deterministic collector fixture; verify
   artifacts and deploy inactive B
   under review. Restore the disabled fixture without losing failed cohorts.
   Prove real nonroot managed KVM CPU/disk/HTTP, guest-owned power-key shutdown,
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
