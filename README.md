# Orange Pi 4 Pro hypervisor

A minimal Linux KVM/QEMU host for **Orange Pi 4 Pro**, booting from **SPI to NVMe
with the SD removed**. The 2 GB SD is a recovery medium. This project supports
this model only; it is not a general A733 distribution.

The first release targets a **manual-management beta**: start an unprivileged
KVM guest, leave it running, and use the working guest, backup/restore and host
maintenance tools. Automatic guest startup and the unfinished managed lifecycle
are experimental, disabled by the beta profile, and deferred.

**Status:** SPI boot and manual KVM operation have existing hardware evidence.
The final manual beta image and its remaining applicable checks are not yet
claimed complete. The hardware development goal is paused.
[Current state and remaining gates](docs/implementation-status.md).

## Use and build

- [Beta scope and acceptance](docs/beta-scope.md)
- [Install, personalize and recover SPI boot](docs/install-and-recovery.md)
- [Operate guests manually](docs/manual-guest-guide.md)
- [Back up, restore, update and roll back](docs/maintenance-guide.md)
- [Build and package a release](docs/build-and-release.md)
- [Known issues](docs/known-issues.md) and [future work policy](docs/roadmap.md)
- [Documentation index](docs/README.md)

## Development

Run affected checks through [the validation runner](docs/validation-policy.md).
Reuse accepted evidence for unchanged dependencies; scripted successes need one
compact result. The [agent policy](docs/agent-routing.md) defaults to Sol 6.1 low
and escalates when the work requires it. [AGENTS.md](AGENTS.md) contains stable
project rules. Historical investigations are outside routine working context.

Generated images, downloaded sources, private identity and raw logs stay outside
Git. A repository checkout or passing software checks do not constitute a
physical beta release; packaging records the exact artifact and evidence scope.
