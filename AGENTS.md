# Repository Guidelines

## Purpose and Current State

Build a minimal KVM/QEMU host for the **Orange Pi 4 Pro A733**, using a 2 GB SD card and nominal 250 GB NVMe. Read `docs/implementation-status.md` first, then relevant sections of `docs/hypervisor-plan.md`. Diagnostic/preparation SD boots enter EL2 on all eight CPUs; KVM initializes, but guest execution is pending. Preparation DHCP and verified HTTPS work; read the status for installation gates.

Vendor images exceed the available SD capacity. Use diagnostic outputs for inventory; leave attached heatsinks in place. Prefer SPI-to-NVMe boot; otherwise keep SD boot files read-only with zero routine writes. Require an actual KVM guest for hardware acceptance.

## Structure and Commands

- `.codex/config.toml` and `.codex/agents/`: coordinator defaults and worker roles.
- `docs/`: implementation plan, current status, host assessment and routing policy.
- `configs/build-host-packages.txt`: vendor-derived Ubuntu 22.04 builder dependencies.
- `build/`: pinned diagnostic and host bootstrap/root/state recipes; see `docs/`.
- `scripts/`: guarded NVMe maintenance, guest activation and KVM-only launch.
- `tests/`: host packaging and planner regressions; no hardware acceptance claims.
- `tmp/` and `out/`: ignored research downloads and generated artifacts.

Run all `tests/test-*.sh` with Bash, `python3 tests/test-assemble-diagnostic.py`, and `python3 -m unittest discover -s tests -p 'test_*.py'`. Run `git diff --check`, parse modified TOML with Python `tomllib`, and use `bash -n` for Bash syntax checks. Record end-to-end build evidence separately; host checks do not establish physical boot.

## Agent Routing

Delegate bounded work according to `docs/agent-routing.md`: Luna for extraction, Sol for implementation/review, Astra only for justified escalation. Use explicit model and effort with focused fresh-context handoffs. Limit active children to two, prohibit nested delegation, and assign one writer per file. Keep architecture, integration and acceptance with the coordinator.

## Changes and Validation

Use descriptive names, consistent language-native formatting, and focused patches. Pin vendor revisions and cite source paths for hardware claims. Add meaningful regression tests with behavior changes. Independently review boot, disk selection and rollback logic; record hardware evidence separately from host checks.

Use imperative commit subjects. Commit coherent changes with validation results; pull requests should explain purpose, risks and linked issues where applicable. Keep generated images, downloaded sources and secrets out of Git.

## Documentation Hygiene

Keep this guide concise and current. Replace obsolete instructions instead of appending history. Store evolving decisions and blockers in the status file, detailed evidence in linked documents, and raw logs outside routine agent context. Never describe an untested image or unavailable hardware check as successful.
