# Current images

**No manual-beta release image has been built or published by this PR.**
The last recorded installation remains `fb29af4` B, SPI/NVMe with SD absent.
The source repair at `2675847` was not installed. Supported restore completed at
generation24 with the guest disabled/ready0 and admission shutdown-closed;
a fresh boot is required. [Current state](implementation-status.md).

## Retained inputs

| Purpose | Reference |
| --- | --- |
| Exact installed B and older trial hashes | [Preserved artifact inventory](archive/pre-beta-20261114/current-images.md) |
| Normalized A rollback custody | [Verified custody](evidence/normalized-a-compatibility-custody-20261112.json) |
| SPI candidate, original and calibrated snapshots | [Artifact inventory](archive/pre-beta-20261114/current-images.md) and [SPI policy](spi-calibration-policy.md) |
| Manual guest CP5 and restored experimental state | [Pause/restoration receipt](evidence/goal-pause-and-restoration-20261113.json) |
| New beta artifacts | Produced by [build and release](build-and-release.md); no historical artifact is silently relabeled |

The old recovery SD contains this board's private SSH host key. Keep it private;
it is not a generic image for another unit or a public GitHub release asset.
The retained full SPI snapshot predates later boots and vendor calibration may
change its parameter area. Use a fresh metadata-aware live plan for any later
programming, never an old snapshot as proof of current flash contents.

Changing documentation does not require rebuilding these retained artifacts.
Keep exact hashes, source receipts and private identity custody; use the new
packaging inventory to distinguish private per-unit images from distributable
assets. [Installation/recovery](install-and-recovery.md).
