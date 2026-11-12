# Orange Pi 4 Pro hypervisor

A minimal Linux KVM/QEMU host for the **Orange Pi 4 Pro A733**, using the actual
nominal 250 GB NVMe. **It now boots from SPI and operates with SD removed.**
The 2 GB SD card is retained for recovery. [SPI/NVMe results](docs/spi-sd-boot-20261111.md).

Start with [implementation status](docs/implementation-status.md) and the
[current images](docs/current-images.md). The fresh normalized installation and independent deployment reproduction pass,
including original guest restoration, actual nonroot KVM, clean shutdown,
checkpoint and restart. The eight-hour workload gate passes using retained
longer-run data, without another endurance run. Physical host restoration,
changed-root update, selection interruption/cancellation and healthy manual
rollback also pass with independent UART capture/control. The earlier SSH failure
remains unexplained. **The earlier protected-SD release qualification passed**,
including ten warm and ten physical cold boots with unchanged complete SD hashes.
The subsequent SPI operational checks passed a cold boot, warm restart and real
KVM guest persistence through counters 7→8→9.
See the [release checklist](docs/release-acceptance.md).
Production qualification and unattended guest operation follow the
[production-readiness plan](docs/spi-production-readiness-plan.md).

The [build/deployment plan](docs/hypervisor-plan.md),
[selected architecture](docs/architecture.md),
[Ubuntu build-host assessment](docs/build-host-assessment.md),
[serial guide](docs/diagnostic-image.md) and
[hardware acceptance record](docs/hardware-validation.md) define the work and
recorded hardware scope. Build recipes and guarded installation, guest,
update/rollback and recovery tools live under `build/`, `scripts/` and `docs/`.
Generated images, downloaded sources, private keys and raw logs stay outside Git.

The [agent routing policy](docs/agent-routing.md) and `.codex/` configuration define
model tiers, focused handoffs and independent review requirements.
