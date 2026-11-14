# Manual SPI/NVMe beta scope

## Product promise

The first release is a minimal Linux KVM/QEMU host for **Orange Pi 4 Pro only**.
SPI boot to NVMe with the SD removed is mandatory. A 2 GB SD remains a recovery
medium; protected-SD-only boot cannot satisfy beta acceptance. Preserve zero
routine SD writes, the minimal host, actual hardware acceleration and the
existing storage/ownership/recovery protections.

The same model with different serial numbers, MAC addresses, SSH identity or
storage UUIDs is an intended per-unit deployment, not another board family.
Generate/personalize its identity and derive a fresh target plan; do not clone
this board's STATE or use its pinned known-device writer blindly. A materially
changed PCB revision, flash, NVMe or firmware requires a focused compatibility
check. No other Orange Pi series/model or generic A733 board is in scope, and no
additional-unit or revision matrix is a release prerequisite.

## Management boundary

`manual-beta` is the default immutable-root profile. Keep working storage/account
preparation, manual enrollment/launch, stopped-disk checkpointing, backup/restore,
host maintenance and standard Linux/systemd/QEMU administration. A manually
started service continues after SSH disconnects; guest lifetime is not capped by
the test duration.

A checkpoint updates the stopped disk's protected hash/history. It is not a disk
copy, saved guest RAM, suspend/resume snapshot, or off-device backup. The operator
must perform a guest-owned clean shutdown, checkpoint and unit regeneration
before restarting a changed disk, and stop/checkpoint guests before host reboot.

Automatic boot dispatch and the complete managed lifecycle are deferred. That
includes automatic shutdown proof/checkpoint publication, registry reconciliation,
quarantine recovery automation and managed-specific backup/restore qualification.
Source and shared imports remain present. Preparation/admission/teardown remain
active; unsupported managed mutations are refused by the manual profile. Existing
status/disable/stop and supported restore/custody procedures remain available for
safe recovery. `managed-experimental` is an explicit development build choice,
not a supported beta feature or a requirement to complete before beta.

## Finite gates

| Gate | Acceptance and reuse rule |
| --- | --- |
| Candidate identity | Frozen source and exact artifact manifest identify this model, SPI/NVMe route and manual-beta profile |
| Software | One beta-relevant deterministic validation for changed dependencies; required unavailable checks are explicitly blocked, not passed |
| Construction | One successful final construction and artifact verification; reuse unchanged kernel/toolchain/component evidence; no second independent construction requirement |
| SPI boots | Three cold and three warm successful boots in total for applicable dependencies; credit prior SPI records and run only the shortfall; final manual profile must be observed at least once |
| Guest lifecycle | Unprivileged real KVM, representative CPU/disk/network, guest-owned shutdown, stopped-disk checkpoint, restart and persistence; one complete applicable lifecycle is sufficient |
| Recovery/preservation | Applicable SPI recovery, rollback, backup/restore evidence; repeat only when relevant behavior changes |
| Delivery | Coherent guides, known issues, checksums, provenance, asset eligibility and truthful gate status |

These are modest integration checks, not a statistical reliability claim. Do not
run the complete guest lifecycle on every boot. Do not relabel protected-SD
observations as SPI boot evidence. There is **no new endurance gate**. Existing
long-run data remain accepted. If a concrete material change creates a new risk,
use the smallest targeted check, normally no more than a 30-minute representative
smoke; increasing duration requires a stated unanswered question and stop point.

Use upstream validation of unchanged Linux/KVM/QEMU instead of recreating general
instruction, device, migration or virtualization conformance suites. This project
still validates its vendor-kernel/EL2/device-tree integration and custom writers,
permissions, manifests and supported workflows. See [validation](validation-policy.md).

## Release-blocking defects

Block on failed normal supported operations, data corruption, wrong-device writes,
serious reachable privilege failures, or loss of documented recovery. Record minor
edge cases and unsupported configurations as known issues. A defect's upstream
origin neither excuses a serious supported-path failure nor makes an unrelated
upstream issue a local beta prerequisite. Shared failures remain relevant even
when the test/module name contains `managed`.

Future work is admitted by [the roadmap policy](roadmap.md), not by historical
TODOs. A beta label does not turn missing physical evidence into a pass.
