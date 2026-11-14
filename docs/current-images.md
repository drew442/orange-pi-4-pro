# Current images

**Constructed repaired manual-beta candidate; staged and selected; awaiting cold boot; no accepted release or release tag.**
Built source: main `8f311c484d33aab2882472766bc6263d064004c8`, including
merged PR #1 and the reviewed manual boot-lock repair.
Tag used by the builder: `manual-beta-20261114-02` (not a Git release tag).
The reviewed fix `990fd09` is included. See
[current status](implementation-status.md) for deployment and remaining gates.

## Exact candidate artifacts

Qualified-builder directory:
`/var/lib/a733-clean-replay-20261106/host-roots-manual-beta-20261114-02/work/project/out/host-payload-manual-beta-20261114-02/`.
Repaired compressed roots and their producer manifest are retained locally under
`out/beta-release-20261114/root-02-assets/`; full source, construction and readback
records are alongside that directory. Original candidate01 raw/gzip copies remain
preserved under `retained-roots/`. Root payloads are not whole-NVMe or SD images.
Keep candidate assets private pending eligibility.

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `manifest.json` | 51,663 | `3b2ba8f27ca2e9a3d2be33b40f39e4e2984a8ddad428156afccee6defa9599af` |
| `root-A.ext4.gz` | 143,665,284 | `0a500bcb9ad6cd11ae632fee031e2cacbdbc301963b7dbb98a5d0b295306e62d` |
| `root-B.ext4.gz` | 143,665,245 | `b1e10a24ce7aa98e87d36b07f07e9dbe4553dd443ed2a10c17ec1084a2631669` |
| `root-A.ext4` | 4,294,967,296 | `373e1b62343b8477a0fe1f6c78d4fa25c81775a8f739b9b3d0a8f53ba2e4cf6e` |
| `root-B.ext4` | 4,294,967,296 | `116da2be7bd31b1e65a6c702bf4dc57bee98cb76155c79998cfbd13759e82789` |

Repaired B has passed guarded physical staging and full target readback. Preserved
normalized A remains intact for rollback. The board is powered off, repaired B
selected durably, awaiting the operator cold power cycle.
The boot-lock fix still requires fresh-boot/normal-launch observation; no physical
fix acceptance is claimed yet. Matching fresh STATE/NVMe composition is running as
`a733-manual-beta-composition-20261114-02.service` on the qualified builder.
No private package or public prerelease has been published.

## Preserved recovery inputs

| Purpose | Reference |
| --- | --- |
| Preceding B trials and exact recovery/SPI hashes | [Preserved inventory](archive/pre-beta-20261114/current-images.md) |
| Normalized A rollback custody | [Verified custody](evidence/normalized-a-compatibility-custody-20261112.json) |
| Original firstkvm CP5 off-device backup | [Resumption receipt](evidence/manual-beta-resumption-20261114.json) |
| SPI candidate and calibration handling | [SPI policy](spi-calibration-policy.md) |

The original recovery SD contains this unit's private SSH host key. Keep it
private and per unit. Original/calibrated SPI snapshots remain retained; future
writes require fresh live identity and metadata-aware planning. No SD or SPI
write was performed during this resumption. Follow [build and release](build-and-release.md)
for eventual packaging and [installation/recovery](install-and-recovery.md).
