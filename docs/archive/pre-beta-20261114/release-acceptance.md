> **Historical record — not active instructions or release gates.**
> Superseded by the [manual beta scope](../../beta-scope.md) and
> [current status](../../implementation-status.md). Original observations retain
> their recorded configuration and limitations.

# Release acceptance checklist

Reviewed 2026-11-11 against the [plan](../../hypervisor-plan.md). **Release acceptance
passes for this recorded A733 using the immutable SD fallback.** Ten warm
reboots, ten operator-performed cold boots, the eight-hour workload gate and
independently reproduced deployment all pass. The complete SD matches its
retained boundary hash with zero Linux write/discard/flush counters.
[Cold-series result](../../evidence/final-cold-boots-20261111.json) and
[verified off-device custody](../../evidence/final-cold-custody-20261111.json).

The [current status](../../implementation-status.md) identifies the running host and
exact media. The [coordinator audit](../../evidence/release-acceptance-20261111.json)
records requirement-level conclusions, artifact hashes and source evidence.
Completed tests remain accepted under the operator's eight-hour policy.

The subsequent [SPI operational goal](../../spi-sd-boot-20261111.md) also passes,
with SD removed, cold/warm host boots and actual KVM persistence through 7→8→9.
The ten-cycle records below retain their original protected-SD scope.

## Required hardware evidence

| Plan requirement | Evidence and present conclusion |
| --- | --- |
| Fresh build and installation using actual 2 GB SD and nominal 250 GB NVMe | [Construction index](../../build-reproduction-status.md), [full NVMe stream comparison](../../evidence/normalized-full-nvme-image-20261109.json), [isolated composition](../../evidence/isolated-nvme-composition-20261109.json) and [actual installation](../../evidence/normalized-host-install-20261110.json) pass. SD is 1977614336 bytes; NVMe is 256060514304 bytes. No larger card or expanded image staging is required. |
| Ten warm reboots and ten cold boots on recorded firmware/SSD | [Ten warm reboots pass](../../evidence/final-warm-boots-20261110.json), with distinct guarded UUIDs and matching serial sequences. [Ten physical cold boots pass](../../evidence/final-cold-boots-20261111.json), with per-cycle operator power-removal attestations and matching clean-powerdown/SD/Linux/EL2 serial records. |
| Actual accelerated guest, CPU/disk/network, shutdown/persistence/restart | [Fresh normalized A restoration and real KVM 5→6→7](../../evidence/normalized-guest-restore-20261110.json) pass, with live VM/vCPU/VGIC descriptors, nonroot processes, guest-requested shutdown/raw zero exits and durable checkpoints. KVM API probes alone are insufficient. |
| Eight-hour workload with thermal/memory/NVMe observations | [Retained qualifying data passes](../../evidence/eight-hour-endurance-result-20261109.json). Original >20-hour duration and operator termination remain historical facts. No guest-generated completion or clean guest poweroff is inferred; separate functional runs establish shutdown. No endurance rerun is required. All future planned tests longer than eight hours are capped at eight hours. |
| SPI with SD removed, or unchanged SD across boot/workload/shutdown with counters and firmware audit | The protected-SD fallback is selected. [Workload comparison](../../evidence/eight-hour-endurance-result-20261109.json), [warm comparison](../../evidence/final-warm-boots-20261110.json), [cold comparison](../../evidence/final-cold-boots-20261111.json) and [bounded firmware audit](../../sd-firmware-write-policy.md) pass. The accepted route uses SD boot files and NVMe root/state/guests. SPI remains unqualified. Hash equality does not detect identical-content writes; retain Linux counters and firmware-audit limitations. |
| Interrupted update and rollback without touching guest disks | [Changed inactive-root retry, selector interruptions/cancellation and healthy old-A rollback](../../physical-host-update-rollback-20261110.md) pass with actual KVM. Interruption was handled SIGTERM, not physical power loss. Automatic rollback is not advertised. |
| Missing NVMe and failed-root/management recovery | [NVMe-driver fault recovery](../../missing-nvme-recovery-test.md) and [controlled SSH/offline-root selection](../../controlled-ssh-recovery-20261110.md) pass with actual KVM afterward. Physical SSD removal and recovery from a fully locked kernel are not claimed. |
| Host/configuration and guest backup restoration | [Paired host restoration and interrupted retry](../../paired-host-restore-20261110.md), [guest restoration](../../evidence/normalized-guest-restore-20261110.json) and [dirty STATE preservation/export/replay](../../dirty-state-recovery-trial.md) pass. Original private archives remain off-device. |
| Unique identity, limited guest privileges and deliberate service exposure | [Same-board original identity/configuration restoration](../../evidence/normalized-host-install-20261110.json) and [live QEMU privilege checks](../../evidence/normalized-guest-restore-20261110.json) pass. Root password is locked; both supplied administration keys and the requested any-subnet policy are retained. Resolver policy passes physically on B and fresh A. |
| Another operator reproduces deployment from a clean environment | [Independent operator reproduction from clean RAM installer boot](../../independent-deployment-20261110.md) passes, with reviewed concrete storage plans and coordinator-observed real KVM. This does not claim a separately provisioned build machine. |

The final warm [evidence archive](../../evidence/final-warm-custody-20261110.json) has a
verified, fsynced second copy on the Ubuntu builder. Installation and guest
[metadata custody](../../evidence/normalized-deployment-custody-20261110.json) excludes
private identity files. Raw captures remain outside Git.

## Deliverables and operating instructions

| Deliverable | Canonical reference |
| --- | --- |
| Architecture, boot-chain diagrams and byte-accurate layout | [Architecture](../../architecture.md) |
| Exact source/firmware/toolchain pins and package bill of materials | [Diagnostic input lock](../../../configs/diagnostic-sources.lock.json), [component-source lock](../../../configs/linaro-source-inputs.lock.json), [userspace profile](../../userspace-profiles.md) and the selected distribution manifest identified by [current images](../../current-images.md) |
| Build prerequisites, commands and reproduction evidence | [Builder assessment](../../build-host-assessment.md), [construction index](../../build-reproduction-status.md), [frozen builder](../../clean-builder.md), [kernel replay](../../kernel-reproducibility.md), [SD reproduction](../../sd-image-reproduction.md), [full composition](../../isolated-nvme-composition.md) |
| Installer, host distribution, checksums and same-board personalization | [Current images](../../current-images.md) and [reproduced deployment guide](../../deploy-protected-20261109.md); installation uses one streamed write with progress and optional troubleshooting readback |
| Guest creation, lifecycle, networking, backup and restoration | [Enrollment](../../guest-enrollment.md), [operations](../../guest-operations.md), [networking](../../guest-networking.md), [checkpoints](../../guest-checkpoints.md), [backup](../../guest-backups.md), [restoration](../../guest-restoration.md) |
| SD write policy and explicit maintenance | [Firmware audit](../../sd-firmware-write-policy.md), [known SD writer](../../known-bootstrap-sd-writer.md); routine root updates remain on NVMe |
| Updates, rollback, recovery and host backup | [Maintenance kit](../../normalized-maintenance-20261109.md), [rollback](../../host-recovery.md), [serial](../../remote-serial-control.md), [paired backup](../../host-state-backup.md), [dirty-state recovery](../../dirty-state-recovery-trial.md) |
| SSD limitations, inventory and test logs | [Hardware record](../../hardware-validation.md), [diagnostic inventory](../../board-diagnostic-20261104.md), [current acceptance evidence](../../implementation-status.md#acceptance-evidence) |
| SPI recovery and programming status | [ROM recovery investigation](../../rom-recovery-investigation.md) and [guarded programmer](../../known-spi-programmer.md); independent recovery proof is required before any programming |
| Source/license notices and unresolved provenance | [Exact toolchain-source inventory](../../toolchain-sources.md) and [vendor boot-input inventory](../../evidence/vendor-boot-license-inventory-20261106.json) |

Build repetition establishes the documented fixed-input constructions on the
qualified Ubuntu builder, including fresh checkouts and isolated frozen stages.
It does not establish a new machine provisioned independently or a source rebuild
of every compiler component. The binary compiler and firmware bytes are pinned;
exact GCC/DejaGNU source copies and 26 vendor boot-input grants remain unresolved.
Keep these limitations explicit. Public binary redistribution clearance is not
claimed; no new approval requirement is imposed on the authorized private
same-board deployment.

The final cold-series review reconciles all ten physical attestations, distinct
ready boots and continuous UART windows. The complete SD comparison matches
SHA-256 `401df09e666c22db8e8f9adbeecc0f385a910b1f41995e7a1d0bc68be3b64be5`
across the final warm/cold interval. Counters stay zero and the firmware audit
retains its stated opaque-input limitations. The final evidence has a verified,
fsynced second copy on the builder. No required private-deployment acceptance
work remains for the recorded hardware and selected fallback route.
