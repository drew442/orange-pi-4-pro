# Repository Guidelines

## Purpose and Current State

Build a minimal KVM/QEMU host for the **Orange Pi 4 Pro A733**, with a nominal 250 GB NVMe and 2 GB SD recovery card. Read `docs/implementation-status.md` first, then relevant sections of `docs/hypervisor-plan.md`. SPI/NVMe operation with SD physically removed is evidenced. Metadata-aware blank restoration, SD recovery and SPI reinstallation pass; post-reinstall actual nonroot KVM advanced counter 9→10, shut down cleanly and published checkpoint 5 with verified off-device backups. Managed root B is installed and booted at 192.168.1.118 (strict host-key alias 192.168.1.116); its pinned runtime and empty-registry storage preparation pass. Managed guest enrollment and hardware qualification remain in progress. Normalized root A, known SD recovery card and original/calibrated SPI backups remain preserved. Vendor SPI calibration metadata changes during boot; use metadata-aware tooling and fresh reviewed live-flash plans. Production qualification follows `docs/spi-production-readiness-plan.md`; APIs alone are not deployment acceptance. The previous protected-SD release, update/rollback/recovery and independent deployment reproduction remain evidenced. The eight-hour workload passes from retained >20-hour data; cap future planned tests at eight hours and avoid qualifying reruns. Keep the known SD recovery card and original/calibrated SPI backups. See status for the earlier unexplained SSH failure and source/license limits.

Vendor images exceed the available SD capacity. Use diagnostic outputs for inventory; leave attached heatsinks in place. Prefer SPI-to-NVMe boot; otherwise keep SD boot files read-only with zero routine writes. Require an actual KVM guest for hardware acceptance.

## Structure and Commands

- `.codex/config.toml` and `.codex/agents/`: coordinator defaults and worker roles.
- `docs/`: implementation plan, current status, host assessment and routing policy.
- `configs/build-host-packages.txt`: vendor-derived Ubuntu 22.04 builder dependencies.
- `build/`: pinned diagnostic and host bootstrap/root/state recipes; see `docs/`.
- `scripts/`: guarded NVMe maintenance, SPI preparation, guest activation and KVM-only launch.
- `tests/`: host packaging and planner regressions; no hardware acceptance claims.
- `tmp/` and `out/`: ignored research downloads and generated artifacts.

Run all `tests/test-*.sh` with Bash, `python3 tests/test-assemble-diagnostic.py`, and `python3 -m unittest discover -s tests -p 'test_*.py'`. Run `git diff --check`, parse modified TOML with Python `tomllib`, and use `bash -n` for Bash syntax checks. Record end-to-end build evidence separately; host checks do not establish physical boot.

## Agent Routing

Delegate bounded work according to `docs/agent-routing.md`: Luna for extraction, Sol for implementation/review, Astra only for justified escalation. Use explicit model and effort with focused fresh-context handoffs. Limit active children to two, prohibit nested delegation, and assign one writer per file. Keep architecture, integration and acceptance with the coordinator.

## Changes and Validation

Use descriptive names, consistent language-native formatting, and focused patches. Pin vendor revisions and cite source paths for hardware claims. Add meaningful regression tests with behavior changes. Independently review boot, disk selection and rollback logic; record hardware evidence separately from host checks. Identify exact erase/SPI targets and honor persisted operator authorization; request yes/no only for unapproved targets or scope. The known board SD, NVMe and SPI writes are authorized; see `docs/evidence/storage-write-authorization-20261108.json`. Make installation readback optional and show progress; do not benchmark hardware to diagnose installation duration. All free capacity on the Ubuntu build host is approved for use/expansion without another permission request; revalidate exact targets and retain independent review.

Use imperative commit subjects. Commit coherent changes with validation results; pull requests should explain purpose, risks and linked issues where applicable. Keep generated images, downloaded sources and secrets out of Git.

## Documentation Hygiene

Keep this guide concise and current. Replace obsolete instructions instead of appending history. Store evolving decisions and blockers in the status file, detailed evidence in linked documents, and raw logs outside routine agent context. Never describe an untested image or unavailable hardware check as successful.
