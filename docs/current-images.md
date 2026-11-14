# Current images

**Accepted repaired manual-beta runtime and image; versioned delivery pending.**
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

Repaired B passed guarded physical staging/full target readback and the final
SD-absent SPI/NVMe cold boot. Preserved normalized A remains intact for rollback.
Automatic manual-lock preparation and normal nonroot KVM launch, persistence,
guest-owned shutdown and CP8 passed. [Acceptance](evidence/manual-beta-hardware-acceptance-20261114.json).

Matching whole-NVMe output:
`out/beta-release-20261114/nvme-assets/nvme.img.gz`, **546,020,115 bytes**, SHA-256
`3ac417ca772c0a2a11d4b67f3357faf783437198e8f66ec560f1df7cd0f71bc6`.
Producer manifest: 139,964 bytes, SHA-256
`4f379ef74e13db8bb4a022da138f1cc39fc4b2ef5055f7d68c75e995d5aaf9da`.
Expanded image: 256,060,514,304 bytes, SHA-256
`1c8d06b8ab3720da9c94ab41322dd9fe0a18f7dcdd238975a988e7f7b76fe667`.
It contains matching A/B roots and newly generated, per-unit STATE. Existing-unit
physical acceptance used the component update; no destructive whole-image
reinstallation is claimed for this run. Versioned packaging/publication is pending.

## Preserved recovery inputs

| Purpose | Reference |
| --- | --- |
| Preceding B trials and exact recovery/SPI hashes | [Preserved inventory](archive/pre-beta-20261114/current-images.md) |
| Normalized A rollback custody | [Verified custody](evidence/normalized-a-compatibility-custody-20261112.json) |
| Original firstkvm CP5 off-device backup | [Resumption receipt](evidence/manual-beta-resumption-20261114.json) |
| SPI candidate and calibration handling | [SPI policy](spi-calibration-policy.md) |

The original recovery SD contains this unit's private SSH host key. Keep it
private and per unit. Original/calibrated SPI snapshots remain retained; future
writes require fresh live identity and metadata-aware planning. No SD write or host SPI programming
was performed during this resumption; vendor calibration metadata may change at boot. Follow [build and release](build-and-release.md)
for eventual packaging and [installation/recovery](install-and-recovery.md).
