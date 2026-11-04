# Orange Pi 4 Pro hypervisor

Build a minimal KVM/QEMU host for an A733 Orange Pi 4 Pro with a 2 GB SD card and a nominal 250 GB NVMe drive.

Start with [implementation status](docs/implementation-status.md), then the [build and deployment plan](docs/hypervisor-plan.md). Diagnostic recipes, host tests and a read-only NVMe layout planner are implemented. Host preparation also includes [A/B root payloads](docs/host-payload.md), a [root/state handoff](docs/host-handoff.md), [per-installation state](docs/host-state.md) and a [KVM guest fixture](docs/guest-fixture.md). These have not been installed or passed hardware acceptance.

Preferred outcome: Linux KVM with QEMU, a minimal host on NVMe, and SPI boot where validated. A read-only SD bootstrap is the fallback. Hardware acceleration and boot compatibility must be proven before release.

The [agent routing policy](docs/agent-routing.md) and project `.codex/` configuration define model tiers, context budgets, review gates, and cost calibration for implementation work.

See the [diagnostic image and serial guide](docs/diagnostic-image.md), [build host assessment](docs/build-host-assessment.md) and [hardware acceptance record](docs/hardware-validation.md). The first image must fit the available 2 GB SD card; live hardware inventory follows its first boot.
