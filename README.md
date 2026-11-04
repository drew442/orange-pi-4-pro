# Orange Pi 4 Pro hypervisor

Development planning for an A733 Orange Pi 4 Pro with a 2 GB SD card and a nominal 250 GB NVMe drive.

Start with the [build and deployment plan](docs/hypervisor-plan.md). It defines the proposed architecture, agent work packages, installation method, and release acceptance gates. This repository currently contains planning documentation, not a buildable or hardware-validated image.

Preferred outcome: Linux KVM with QEMU, a minimal host on NVMe, and SPI boot where validated. A read-only SD bootstrap is the fallback. Hardware acceleration and boot compatibility must be proven before release.

The [agent routing policy](docs/agent-routing.md) and project `.codex/` configuration define model tiers, context budgets, review gates, and cost calibration for implementation work.

See [implementation status](docs/implementation-status.md) for the next milestone and [build host assessment](docs/build-host-assessment.md) for environment gaps. The first image must fit the available 2 GB SD card; live hardware inventory follows its first boot.
