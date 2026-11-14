# Install, personalize and recover

Only **Orange Pi 4 Pro** is supported. The normal route is SPI firmware → NVMe
BOOT → selected read-only root A/B with persistent STATE/GUESTS. The 2 GB SD is
recovery media; SD-only boot is not beta completion. [Architecture](architecture.md).

## Select the actual package and unit

Use [build and release](build-and-release.md) to select a coherent package and
verify its release manifest/checksums through a trusted channel. Match component
roles: a root partition image is not a whole NVMe image, and an SPI binary is not
an SD image. The new manual-beta profile must be explicit in the root manifest;
old trial filenames are not evidence of that profile.

The same Orange Pi 4 Pro model with another MAC/serial is an intended deployment,
but identity is per unit. Obtain the actual board/flash/NVMe inventory, generate a
unique machine ID and SSH host key, select its management MAC/hostname/admin
public keys, and use compatible STATE/GPT UUIDs. The retained-BOOT compositor keeps the
   recorded STATE UUID because the initramfs resolves it; it still requires fresh
   per-unit machine identity and administrator configuration. Do not change that
   UUID without rebuilding matching BOOT inputs. Existing state builders accept
management JSON and administrator public keys and generate per-installation
identity. Never clone the original private recovery SD or STATE. The
`known-*` helpers contain original-unit pins; use the generic reviewed installer
interface and a fresh target plan for another unit, not edited pins guessed from
an example. A changed component or PCB revision gets a focused compatibility
check; no other-board qualification campaign is required.

## Installation sequence

1. Preserve current guest/host data off-device and retain working recovery media.
   Identify exact physical SD/NVMe/SPI devices from current inventory.
2. Prepare recovery SD appropriate to the unit, using the authenticated image and
   the [SD/recovery procedure](ssh-ram-installer.md). The existing original-unit
   recovery image is private. Capture UART from power-on; SSH is supplemental.
3. Acquire the authenticated compressed NVMe image on suitable external storage;
   the [acquisition procedure](nvme-distribution.md) and
   [guarded installer](nvme-installation.md) stream it without staging 250 GB raw
   data on the recovery card. Run prepare, inspect exact target identity, then
   apply with existing target authorization. Installation readback is optional;
   no repeated readback campaign is implied.
4. A root-component update preserves the existing STATE identity and guests.
   The complete NVMe image contains freshly generated machine/SSH identity and
   the specified unit's management configuration. Verify its SSH fingerprint on
   the trusted UART console before updating a known-host entry. The historical
   [same-unit personalizer](known-host-state-personalization.md) accepts only its
   fixed legacy factory-identity pins; do not bypass its refusal on newly
   generated STATE. Keep ROOT/STATE/BOOT compatibility and original guest backups
   intact. Another unit needs its own provisioning inputs.
5. For SPI, retain original and calibrated snapshots, establish the existing
   [recovery route](spi-recovery-qualification.md), and follow the guarded
   [programmer](known-spi-programmer.md) and [calibration policy](spi-calibration-policy.md).
   Use fresh live geometry/identity/readback inputs. Do not reuse a stale flash
   plan, or reinterpret permission for one device as permission for another.
6. Remove SD and boot via SPI/NVMe. Observe the selected root, management access,
   storage preparation, explicit manual profile and masked dispatch. Continue
   with the [manual guest guide](manual-guest-guide.md).

The existing known-board SD/NVMe/SPI authorization remains valid within its scope;
[the receipt](evidence/storage-write-authorization-20261108.json) avoids repeated
permission requests. Fresh identity/idle checks remain necessary because live
devices and mounted state can change.

## Cutover from the paused experiment

The last recorded state is in [current status](implementation-status.md), backed
by the unchanged [pause receipt](evidence/goal-pause-and-restoration-20261113.json).
This PR does not itself resume hardware work.

When resumed, inspect live state once and reconcile the old validation service's
terminal result if needed. The supported restore already completed at generation24,
disabled/ready0; admission is shutdown-closed. Preserve normalized A, manual CP5,
failed cohorts, recovery SD and SPI backups. Do not repeat restore simply because
a session resumed; do not clear quarantine or reopen the gate by editing files.
Use the supported fresh boot, then the normal inactive-root update/selection
procedure to deploy the manual-beta image. Stop/checkpoint any actual running
manual guest first; disabled registry state alone is not evidence a process ended.

The new profile retains shared preparation, accounts, admission and teardown,
while masking automatic dispatch. Keep all shared packaged files and their
matching provenance; do not copy individual old/new scripts into the immutable
installation. Existing experimental data remain recoverable through supported
status/disable/stop and restore/custody commands.

Verify the final profile and manual lifecycle once, reuse unchanged platform and
recovery evidence, and fill only remaining SPI boot-count gaps. The archived
managed checkpoint/autostart/ten-cycle resume sequence is not the beta plan.

## Recovery

Keep the known-good root and recovery SD until replacement boot succeeds.
For SSH loss use UART and the documented [host recovery](host-recovery.md);
select a compatible retained root through the guarded selector, not raw edits to
STATE. Use [backup/restore maintenance](maintenance-guide.md) for guest data.
SPI recovery uses retained original/calibrated bytes and fresh metadata-aware
planning. A source-only PR cannot establish physical recovery on new hardware.
