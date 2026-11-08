# Build reproduction status

Updated 2026-11-08. This index records build evidence; physical release acceptance
remains in [hardware validation](hardware-validation.md). Published installation
artifacts remain bound to [the deployment guide](deploy-personalized-20261106.md).

| Stage | Verified evidence | Remaining limit |
| --- | --- | --- |
| Prepared Jammy host | [Assessment](build-host-assessment.md), pinned package inventories, two compilation jobs | Prepared-host builds alone are insufficient |
| Builder inputs | [842 package archives](evidence/builder-packages-acquired-20261106.json), [signed AMD64 seed](../configs/builder-base-20261106.lock.json) | Source recompilation is separate |
| Fresh builder installation | [Isolation](clean-builder.md), [offline installation](evidence/builder-offline-install-20261106.json): configured inventory/audits, unchanged host/cache | One installed construction |
| Frozen builder | [Two identical exports and restoration](evidence/builder-frozen-20261106.json), independent complete codex readback | Two exports are not two OS constructions |
| Fresh sources | [Acquisition](evidence/rebuild-sources-acquired-20261106.json), [import](evidence/frozen-source-import-20261106.json): exact vendor/kernel/U-Boot commits, Linaro toolchain and 26 boot inputs | Firmware licensing remains open |
| U-Boot | [Two frozen-builder replays](evidence/frozen-uboot-replay-20261106.json): unpacked binary and packed boot package match | Historical challenge replay is explicit; fresh randomness remains default |
| Separate NVMe-first U-Boot candidate | [Actual construction](evidence/uboot-nvme-candidate-20261107.json) and [registered exact replay](evidence/uboot-nvme-replay-20261107.json): binary/package/configuration/challenge/SPI boot0 match; all 77 replay manifest entries verified through complete reference map | SPI backup/recovery, flash assembly/programming and hardware boot remain open |
| Retained NVMe U-Boot workspace | [Actual full temporary-path restoration](evidence/uboot-nvme-restoration-20261107.json): all 27,105 entries compared, exact path equality, 72 manifest hashes, unchanged archive and clean unmount | Restored-source rebuild and restoration at original absolute paths remain untested; no hardware acceptance |
| SPI regular-file composition | [Synthetic-baseline assembly](evidence/spi-file-assembly-20261107.json): real pinned components, byte-identical 16 MiB file and manifest on Jammy/codex, exact gap/tail preservation, all 256 unit hashes | Synthetic fixtures must never be flashed. [Original SPI backup and actual-backup candidate](spi-original-backup-20261108.md) now pass independent retention/file inspection; recovery, confirmation, programming and boot remain open |
| November 8 component retention | [Actual first-root encoded retirement and full seed recovery rehearsal](component-retention-20261108.md): both gzip copies/full expansions pass; 24 Ubuntu retirement fixtures and independent reviews pass; reconstructed seed matches all 91,578 paths | First raw roots retired; their A restoration rehearsed. Actual archive-based seed recovery also passes; reviewed seed retirement is running, with terminal/postchecks pending. No full NVMe composition or hardware acceptance |
| Corrected-chain NVMe readiness | [Registered controller](nvme-runtime-fixes-controller.md): independent source review, committed planning, complete held authentication of all 144 actual inputs and 1,746 privileged tests pass | No composition started; exact 23,760,999,827-byte admission remains capacity-blocked; physical acceptance unchanged |
| Corrected host roots | [Fresh runtime-fix components](production-host-roots-runtime-fixes.md): source `f40c151`, 221 packages, account/ROM fixes and machine-id mask, full 16,368-path readback per slot and independent artifact review | [Matched state](production-host-state-runtime-fixes.md) and [bootstrap](production-host-bootstrap-runtime-fixes.md) now pass construction/readback/review; full-NVMe chain and physical boot pending; no byte-identical root reproduction claim |
| Kernel | [Fresh frozen-builder replay](evidence/frozen-kernel-replay-20261106.json): Image/DTB/config and all 332 regular modules match; independent 2,556-entry manifest rehash | Original debug paths and umask are deliberate replay inputs |
| Deployment SD images | [Tracked clean-checkout recipe](sd-image-reproduction.md): both 256 MiB raw/gzip images match; regenerated runtime and independent inspections pass | Physical boot of refreshed images remains pending |
| Sparse SD packaging and repeat capacity | [Verified retention](repeat-build-retention.md): eleven historical expanded leaves archived on both hosts; independently audited full contents/metadata. Sparse SD recipe passes frozen construction with identical prior raw/gzip bytes and 36,704,256-byte raw allocation | Retention is not a production-chain repeat or physical deployment; full restoration of the eleven actual archives is untested |
| Installer refresh | [Prepared-builder packaging and independent artifact/ARM review](evidence/installer-refresh-20261107.json): source `151a532`, 256 MiB image, 40 maintenance files, simpler confirmation and default no-readback fixture | New physical boot pending; current installed-state repair still required; not a full clean production reconstruction |
| ARM userspace inputs | [220 authenticated binary packages](userspace-rebuild-preparation.md), [479 source archives](evidence/userspace-sources-acquired-20261106.json) | Source archives are acquired, not recompiled |
| Foreign userspace stage | [Authenticated mirror and pinned adapter](offline-userspace-reconstruction.md), [actual records](evidence/userspace-foreign-20261106.json): all 669 mirror files checked, 99 packages cached, 96 extracted, empty target status | Native data stage only |
| ARM userspace configuration/export | [Actual configuration/export](evidence/userspace-configured-export-20261106.json): 220 exact configured packages, clean dpkg/APT audits, identity cleanup; all 15,881 archive members independently verified | [Tracked configuration replay](userspace-configuration-replay.md), [normalized pair](userspace-export-reproducibility.md) and [fresh raw-package pipeline](userspace-fresh-pipeline.md) match compressed/raw/BOM bytes; production composition now passes below; independent deployment remains open |
| Tracked userspace orchestration | [Actual launcher execution](evidence/userspace-tracked-execution-20261106.json): fresh raw inputs, all three phases, 220 packages, exact normalized archive, unchanged host inventory and clean process/mount exit | Production components now pass as recorded below; independent deployment remains open |
| Device metadata | [Fixture](evidence/fakeroot-device-metadata-20261106.json), [two-phase candidate](evidence/fakeroot-metadata-candidate-20261106.json) | Virtual archive/image devices do not prove hardware access |
| Complete historical root archive | [Full staging comparison](evidence/full-root-metadata-fixture-20261106.json), [complete diagnostic image readback](evidence/full-root-image-readback-20261106.json): 15,892 paths, 518,081,083 regular-file bytes, links/devices/attributes | Diagnostic 1 GiB image; production 4 GiB slots now pass as recorded below |
| Authenticated userspace profiles | [Explicit selection and actual staging](userspace-profiles.md): normalized/default pins, 12 provenance files, complete 15,876-entry extraction, 220 packages, eight package-state components and closed runtime; `timesync-20261106` is now an explicit opt-in profile while baseline default and normalized selection remain unchanged | Production roots/state now pass as recorded below; matching bootstrap and NVMe now pass below; physical acceptance remains open |
| Root/state recipe integration | [Optional metadata backend](evidence/root-metadata-backend-20261106.json), [preparation](root-reconstruction-preparation.md), and [time integration](evidence/time-profile-integration-20261107.json): source-wired profile provenance, 221-package metadata, private clock initializer, boot audit and update directory guard; frozen-builder component passed 15,909-entry extraction, 221 tuples, eight per-root package components, four management files, 40 tool files, and a 256 MiB non-deployable clock-state fixture | Production roots now pass complete readback; 8 GiB state now passes below; bootstrap and NVMe now pass below; published baseline artifacts are unchanged |
| Production A/B roots | [First frozen construction](evidence/production-host-roots-20261107.json) and [second construction/full readback](production-host-roots-repeat.md): actual 4 GiB slots, 221 packages/332 modules, 86 file hashes and 16,367 paths per slot. Repeat contents/metadata match except 463 mtimes per slot; ext4 construction metadata also varies | Semantic repeat; raw/gzip hashes differ. State/bootstrap/NVMe repeats now pass; physical deployment remains open |
| Production personalized state | [First construction](evidence/production-host-state-20261107.json) and [second construction/full readback](production-host-state-repeat.md): actual 8 GiB journaled state, two keys/config inputs, 38 artifact hashes and 1,172 paths; corrected machine-id mode and empty clock/host-key directories. Independent review passes both | Fresh UUID/machine-id intentionally differ; matching bootstrap/NVMe repeats now pass; physical deployment remains open |
| Matching immutable bootstrap | [First construction](evidence/production-host-bootstrap-20261107.json) and [second matching construction](production-host-bootstrap-repeat.md): 256 MiB, correct distinct production-state UUID, locked EL2 boot stack, 424 newc entries and complete SD/gzip inspection; independent reviews pass | Identity-bound initramfs/hashes differ. Full NVMe repeat now passes; physical deployment remains open |
| Full production NVMe | [Frozen construction and independent wire verification](evidence/production-nvme-20261107.json): actual 256,060,514,304-byte geometry, matched root/state bytes, complete BOOT/GUESTS readback, SPI-ready BOOT script, 545,633,126-byte gzip independently decoded and checked; independent artifact review passes | [Second full construction and encoded retention](production-nvme-repeat.md) also pass; matched delivery, hardware SPI boot and reproduced deployment remain open |
| Encoded production candidate retention | [Verified off-builder copy and derived manifest](evidence/nvme-candidate-retention-20261107.json): same 545,633,126-byte gzip, encoded readback and source acquisition/installer manifest validation; 710 MB reclaimed from three generated SD copies without changing bytes | Second encoded candidate also retained; active endpoint unchanged. Tested matched delivery and physical installation remain open |
| NVMe-first firmware transport SD | [Frozen construction and boot-file inspection](evidence/nvme-firmware-test-sd-20261107.json): 256 MiB with registered NVMe-first U-Boot package, compatible SD boot0/kernel/DTB, exact production initramfs and complete SD reader pass; 1,596 unittests | [Second matching transport](nvme-repeat-firmware-sd.md) also passes construction, independent image comparison and 1,676 tests. Both remain unbooted; SPI boot and actual KVM remain physical gates |
| Host FEL version-only probe | [Frozen compilation/source and ELF audit](evidence/fel-version-probe-20261107.json): pinned upstream, all 610 project blobs/87 upstream files/97 outputs/five live capsule files, request1 only, 1,614 tests | No board USB access; independent ROM entry, RAM recovery and flash restoration remain unproven |
| Guest fixture | [Fixed seed fixture](evidence/guest-seed-fixture-20261104.json): root/drew byte-identical Jammy builds | [Actual KVM execution](first-kvm-results-20261108.md) now passes CPU/disk after a reviewed initramfs correction; guest networking and clean restart remain open |
| Time-service identity | [Fixed identity gate and ARM fixture](timesync-identity.md): stable996, old tuples unchanged, repeat/collision checks and independent review | Built archive preserves all old accounts and uniquely adds UID/GID 996; production state/A-B now pass below; NTP/hardware unproven |
| Clock-state preparation | [Audit, private initializer and state fixtures](timesync-state.md), plus [integration evidence](evidence/time-profile-integration-20261107.json): preserved future mtime, exact 996 ownership/modes, UUID guards; actual 32 MiB and 256 MiB non-deployable ext4 readbacks and independent review | Time-enabled production state now passes below; installed rollback and NTP remain unproven |
| Time-service package step | [Offline derivation](timesync-userspace.md): exact 221 input locks, complete base-BOM gate, fixed identity/service policy and independent source/input review | Actual complete 221-package configuration and boot-link checks pass; installed startup and physical behavior remain open |
| Time-profile export | [Authenticated adapter](timesync-export.md): strict source/receipt/state chain, immutable backend, prepublication bindings and independent review; native builder process cleanup regressions | Complete readback of all 15,909 archive/BOM members passes; production roots/state now pass below; bootstrap now passes below; matching NVMe now passes above; repeat and physical acceptance remain open |
| Time-profile outer orchestration | [Tracked launcher and actual prelude](timesync-outer-launcher.md): base/source/namespace chain, private-propagation handshake, common manifest, guarded clone and measured output contracts; actual early 5 GiB refusal | Actual complete 221-package configuration/export and independent artifact readback pass; the [dated repeat](evidence/account-date-time-execution-20261107.json) now passes with the strict new baseline receipt contract; production roots/state/bootstrap/NVMe now pass above; clean repeats and hardware acceptance remain open |
| Distribution inputs | [26-input license inventory](evidence/vendor-boot-license-inventory-20261106.json) | Attribution/grants unresolved; no public redistribution clearance claimed |

The November 7 [cross-day failure](userspace-account-date-replay.md) exposed
45 date fields in two locked-account files; other 15,874 entries matched. A
separate authenticated adapter now preserves the historical dates. Its
[fresh baseline execution](evidence/account-date-baseline-execution-20261107.json)
passes all 220 packages and unchanged compressed/raw/BOM pins. Independent
complete 15,876-entry readback and source review pass. The failed attempt remains
recorded. [Time-profile repetition](evidence/account-date-time-execution-20261107.json)
now also passes with unchanged compressed/raw/BOM pins and complete independent
15,909-entry readback. Both staged source snapshots match all 27 recorded Git
blobs; live cleanup and unchanged inventory pass. Production roots/state now pass
below; integrated installation images remain open.

The pinned mke2fs creator copies child modification times at whole-second
resolution and sets the filesystem root to its creation epoch; diagnostic image
comparison explicitly accounts for this transformation. Nanosecond archive
preservation is not nanosecond image preservation.

Capacity recovery is documented in [retention](build-cache-retention.md),
[relocation](evidence/builder-relocation-20261106.json),
[output retention](evidence/build-output-retention-20261106.json),
[scratch retirement](evidence/builder-scratch-retirement-20261106.json) and
[kernel retention](evidence/kernel-space-retention-20261106.json). Retired bytes
remain authenticated on codex; restore paths using their maps. [Verified userspace retention](userspace-rebuild-capacity.md) subsequently recovered
approximately 1.12 GB without losing the five selected artifacts. Recheck actual
free disk/RAM before every run. The reviewed [20 GiB LVM growth](build-host-capacity-plan.md)
was authorized and executed with a metadata backup and successful online ext4
growth. The LV is now 88,017,469,440 bytes; production capacity guards remain mandatory.

The [tracked base now passes](userspace-pipeline-launcher.md) after
[historical root retention](historical-root-retention.md) recovered about 3.53 GB.
The [complete time-profile build](evidence/timesync-execution-20261107.json) now
passes after [one more verified cache retirement](evidence/builder-deb-retention-20261107.json).
Source integration is recorded at commit `bb411e3`; its detailed evidence is
[here](evidence/time-profile-integration-20261107.json). The source now binds the
time opt-in profile, package state, and clock-state seed/boot/update guards, but
is component evidence; subsequent production roots/state are recorded below. The [dated baseline and time constructions](account-date-replay-driver.md) now
pass with preserved closure checks and unchanged historical output pins. Time-enabled
4 GiB roots and 8 GiB personalized state now pass construction/full readback;
matching bootstrap and [full NVMe composition](production-nvme-composition.md)
now also pass, including [second constructions](production-nvme-repeat.md) with documented timestamp/identity differences. Physical deployment remains open.
Published baseline artifacts remain unchanged.
The [approved 20 GiB builder growth](evidence/build-host-capacity-execution-20261107.json)
now succeeds and leaves 24.28 GB free, above the unchanged 22 GiB staging guard.
[Verified workspace retention](composition-retention.md) subsequently recovers
2.48 GB before bootstrap. [Bootstrap construction and sparsification](production-host-bootstrap.md)
left 23.83 GB, admitting the completed full NVMe run. Its final free is
22,245,568,512 bytes; the subsequent firmware transport SD leaves
21,618,569,216 bytes under its own smaller budget. The later FEL probe
compilation leaves 21,557,407,744 bytes under a separate 4 GiB recipe budget. Both are below the next
full-NVMe initial 22 GiB guard before staging overhead.
Further construction requires verified retention. Recheck every admission
without lowering guards. A second operator must
reproduce deployment from the guide; build proofs cannot satisfy physical release gates.
