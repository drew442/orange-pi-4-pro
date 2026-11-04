# Implementation status

Updated 2026-11-04. Replace stale entries; keep this file short.

## Current milestone

Build a headless diagnostic SD image that fits the actual 2 GB card, then collect live board inventory and prove NVMe and KVM operation. Vendor images are too large for the available SD; booting one or obtaining a larger card is not a prerequisite.

## Established facts and decisions

- Target: Orange Pi 4 Pro A733, 2 GB SD, nominal 250 GB NVMe. RAM, board revision and SSD identity remain unverified; live inventory awaits first boot.
- Desired host: minimal Linux KVM/QEMU with state and guests on NVMe; SPI boot preferred, immutable SD fallback. Hardware acceptance remains pending.
- Vendor build source inspected: `orangepi-build` branch `next`, commit `a95129bf30ea11886cc35f08ebb3e69695a26656`. This is an inspection baseline, not a complete reproducible source lock.
- Board config: `external/config/boards/orangepi4pro.conf`; family `sun60iw2`. At this revision `current` uses Linux 6.6 and `legacy` 5.15; boot branch is `v2018.05-sun60iw2`. Resolve component commits and firmware hashes before building.
- Agent configuration is present; explicit Luna extraction was exercised. Fresh-session named-role activation still needs verification.

## Readiness and evidence

- No image, build recipe, installer, or hardware test result exists yet.
- [Remote builder](build-host-assessment.md): `drew@build.drewnet.online`, Ubuntu 22.04.5 x86_64, four CPUs, 7.8 GiB RAM, 4 GiB swap and about 49 GiB free. All 86 selected dependencies installed; package consistency, ARM binfmt registration, locale and free loop device verified. Workspace: `/home/drew/orange-pi-4-pro-build`. Use `NO_APT_CACHER=yes`; start with two build jobs.
- Source and manuals are cached under ignored `tmp/`; durable references are in the assessment and [plan](hypervisor-plan.md). Do not depend on those caches surviving a clone.

## Next actions

1. Use the prepared remote builder; validate actual loop mounts and ARM chroot execution during image-assembly preflight, and monitor disk use.
2. Pin the chosen kernel, bootloader, firmware and toolchain; inspect raw boot layout and KVM prerequisites.
3. Produce a small SD bootstrap with serial console and minimal rootfs. Check actual card capacity at provisioning; 1.8 GB decimal is a packaging target, not the card's measured limit.
4. Boot hardware, collect inventory, and test NVMe and KVM. Continue image/installer work against the plan's gates.

## Completion rule

Build success is not hardware acceptance. Track concrete evidence for each release gate in the plan; no physical boot, zero-SD-write, recovery or KVM claim is currently proven.
