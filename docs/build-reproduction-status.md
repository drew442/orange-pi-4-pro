# Build reproduction status

Updated 2026-11-07. This index records build evidence; physical release acceptance
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
| Kernel | [Fresh frozen-builder replay](evidence/frozen-kernel-replay-20261106.json): Image/DTB/config and all 332 regular modules match; independent 2,556-entry manifest rehash | Original debug paths and umask are deliberate replay inputs |
| Deployment SD images | [Tracked clean-checkout recipe](sd-image-reproduction.md): both 256 MiB raw/gzip images match; regenerated runtime and independent inspections pass | Physical boot of refreshed images remains pending |
| ARM userspace inputs | [220 authenticated binary packages](userspace-rebuild-preparation.md), [479 source archives](evidence/userspace-sources-acquired-20261106.json) | Source archives are acquired, not recompiled |
| Foreign userspace stage | [Authenticated mirror and pinned adapter](offline-userspace-reconstruction.md), [actual records](evidence/userspace-foreign-20261106.json): all 669 mirror files checked, 99 packages cached, 96 extracted, empty target status | Native data stage only |
| ARM userspace configuration/export | [Actual configuration/export](evidence/userspace-configured-export-20261106.json): 220 exact configured packages, clean dpkg/APT audits, identity cleanup; all 15,881 archive members independently verified | [Tracked configuration replay](userspace-configuration-replay.md), [normalized pair](userspace-export-reproducibility.md) and [fresh raw-package pipeline](userspace-fresh-pipeline.md) match compressed/raw/BOM bytes; production composition and independent deployment remain open |
| Tracked userspace orchestration | [Actual launcher execution](evidence/userspace-tracked-execution-20261106.json): fresh raw inputs, all three phases, 220 packages, exact normalized archive, unchanged host inventory and clean process/mount exit | Production root/state/NVMe composition and independent deployment remain open |
| Device metadata | [Fixture](evidence/fakeroot-device-metadata-20261106.json), [two-phase candidate](evidence/fakeroot-metadata-candidate-20261106.json) | Virtual archive/image devices do not prove hardware access |
| Complete historical root archive | [Full staging comparison](evidence/full-root-metadata-fixture-20261106.json), [complete diagnostic image readback](evidence/full-root-image-readback-20261106.json): 15,892 paths, 518,081,083 regular-file bytes, links/devices/attributes | Diagnostic 1 GiB image; production 4 GiB slots remain open |
| Authenticated userspace profiles | [Explicit selection and actual staging](userspace-profiles.md): normalized/default pins, 12 provenance files, complete 15,876-entry extraction, 220 packages, eight package-state components and closed runtime | No production images; source-side resolver only |
| Root/state recipe integration | [Optional metadata backend](evidence/root-metadata-backend-20261106.json), [preparation](root-reconstruction-preparation.md): full extraction through payload interface, no real nodes | Complete root/state/NVMe reconstruction and image readback remain open |
| Guest fixture | [Fixed seed fixture](evidence/guest-seed-fixture-20261104.json): root/drew byte-identical Jammy builds | No guest has booted; real KVM is mandatory |
| Time-service identity | [Fixed identity gate and ARM fixture](timesync-identity.md): stable996, old tuples unchanged, repeat/collision checks and independent review | Built archive preserves all old accounts and uniquely adds UID/GID 996; production state/A-B/NTP/hardware unproven |
| Future clock-state contract | [Guard and private old/new views](timesync-state.md): same inode/996 ownership, zero-capability writer, preserved mtime, six refusal cases and independent review | Tiny file-backed roots; actual boot/updater/rollback/NTP remain unproven |
| Time-service package step | [Offline derivation](timesync-userspace.md): exact221 input locks, complete base-BOM gate, fixed identity/service policy and independent source/input review | Actual complete 221-package configuration and boot-link checks pass; production state integration/boot remain open |
| Time-profile export | [Authenticated adapter](timesync-export.md): strict source/receipt/state chain, immutable backend, prepublication bindings and independent review; native builder process cleanup regressions | Complete readback of all 15,909 archive/BOM members passes; production profile/image binding remains open |
| Time-profile outer orchestration | [Tracked launcher and actual prelude](timesync-outer-launcher.md): base/source/namespace chain, private-propagation handshake, common manifest, guarded clone and measured output contracts; actual early5GiB refusal | Actual complete 221-package configuration/export and independent artifact readback pass; second construction, production images and hardware acceptance remain open |
| Distribution inputs | [26-input license inventory](evidence/vendor-boot-license-inventory-20261106.json) | Attribution/grants unresolved; no public redistribution clearance claimed |

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
has a metadata backup and successful dry run, but awaits authorization. No resize
has occurred; production capacity guards remain mandatory.

The [tracked base now passes](userspace-pipeline-launcher.md) after
[historical root retention](historical-root-retention.md) recovered about 3.53 GB.
The [complete time-profile build](evidence/timesync-execution-20261107.json) now
passes after [one more verified cache retirement](evidence/builder-deb-retention-20261107.json).
Next integrate explicit profile/root package-state and persistent clock-state
seeding/guards, repeat construction, then reconstruct and fully inspect production
roots/state/NVMe after capacity permits. A second agent/operator must reproduce the complete
deployment from the guide; build proofs cannot satisfy physical release gates.
