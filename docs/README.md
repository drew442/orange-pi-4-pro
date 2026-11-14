# Documentation

## Current instructions

| Need | Start here |
| --- | --- |
| Release notes and exact artifacts | [v0.1.0-beta.1](releases/v0.1.0-beta.1.md), [current images](current-images.md) |
| What is supported and what remains? | [Beta scope](beta-scope.md), [current status](implementation-status.md) |
| Install a unit or recover SPI/NVMe | [Installation and recovery](install-and-recovery.md) |
| Start and manage a guest | [Manual guest guide](manual-guest-guide.md) |
| Backup, restore, update, rollback | [Maintenance guide](maintenance-guide.md) |
| Build and package artifacts | [Build and release](build-and-release.md) |
| Run checks and reuse evidence | [Validation policy](validation-policy.md) |
| Use agents economically | [Agent routing](agent-routing.md) |
| Limits and future proposals | [Known issues](known-issues.md), [roadmap](roadmap.md) |

## Detailed references

[Architecture](architecture.md), [host userspace](host-userspace.md),
[guest storage/accounts](guest-activation.md), [enrollment](guest-enrollment.md),
[checkpoints](guest-checkpoints.md), [backup](guest-backups.md),
[restore](guest-restoration.md), [networking](guest-networking.md),
[SPI calibration](spi-calibration-policy.md), [guarded SPI programming](known-spi-programmer.md),
[host recovery](host-recovery.md), [source/notices](userspace-notices.md).
Use the current status for acceptance; historical successes apply only to their
recorded configuration.

## Historical evidence

[Investigation archive](archive/pre-beta-20261114/README.md) preserves obsolete
plans, trial narratives and completed work. The small original-path pages are
redirects so links remain useful. `docs/evidence/` retains machine records at
unchanged paths because some are hashed build inputs. Neither archive is a
routine agent reading list or active task queue. Retrieve a specific receipt
when its scope matters; default `rg` excludes these directories.
