# Orange Pi 4 Pro hypervisor

A minimal Linux KVM/QEMU host for the **Orange Pi 4 Pro A733**, using the actual
2 GB SD card and nominal 250 GB NVMe. Prefer SPI-to-NVMe boot; the fallback uses
immutable SD boot files with zero routine SD writes.

Start with [implementation status](docs/implementation-status.md) and the
[current images](docs/current-images.md). The fresh normalized installation and independent deployment reproduction pass,
including original guest restoration, actual nonroot KVM, clean shutdown,
checkpoint and restart. The eight-hour workload gate passes using retained
longer-run data, without another endurance run. Physical host restoration,
changed-root update, selection interruption/cancellation and healthy manual
rollback also pass with independent UART capture/control. The earlier SSH failure
remains unexplained. **Release acceptance is incomplete:** ten physical cold boots and their final
SD comparison remain. See the [release checklist](docs/release-acceptance.md).

The [build/deployment plan](docs/hypervisor-plan.md),
[selected architecture](docs/architecture.md),
[Ubuntu build-host assessment](docs/build-host-assessment.md),
[serial guide](docs/diagnostic-image.md) and
[hardware acceptance record](docs/hardware-validation.md) define the work and
remaining physical gates. Build recipes and guarded installation, guest,
update/rollback and recovery tools live under `build/`, `scripts/` and `docs/`.
Generated images, downloaded sources, private keys and raw logs stay outside Git.

The [agent routing policy](docs/agent-routing.md) and `.codex/` configuration define
model tiers, focused handoffs and independent review requirements.
