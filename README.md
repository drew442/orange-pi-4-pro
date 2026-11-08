# Orange Pi 4 Pro hypervisor

A minimal Linux KVM/QEMU host for the **Orange Pi 4 Pro A733**, using the actual
2 GB SD card and nominal 250 GB NVMe. Prefer SPI-to-NVMe boot; the fallback uses
immutable SD boot files with zero routine SD writes.

Start with [implementation status](docs/implementation-status.md) and the
[current images](docs/current-images.md). The corrected installation has booted,
synchronized time and run a real KVM guest through restoration, clean shutdown,
checkpoint and restart. The 60-second endurance smoke passed; the 24-hour attempt
lost SSH management and awaits serial diagnosis. **Release acceptance is incomplete.**

The [build/deployment plan](docs/hypervisor-plan.md),
[Ubuntu build-host assessment](docs/build-host-assessment.md),
[serial guide](docs/diagnostic-image.md) and
[hardware acceptance record](docs/hardware-validation.md) define the work and
remaining physical gates. Build recipes and guarded installation, guest,
update/rollback and recovery tools live under `build/`, `scripts/` and `docs/`.
Generated images, downloaded sources, private keys and raw logs stay outside Git.

The [agent routing policy](docs/agent-routing.md) and `.codex/` configuration define
model tiers, focused handoffs and independent review requirements.
