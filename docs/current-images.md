# Current images

**Constructed/deployed manual-beta candidate; no accepted release or release tag.**
Built source: merged PR #1 `e4d39439623393c470f4d2ca354a9a6afd54f902`.
Tag used by the builder: `manual-beta-20261114-01` (not a Git release tag).
The subsequent reviewed fix `990fd09` has not been built. See
[current status](implementation-status.md) for the capacity blocker and gates.

## Exact candidate artifacts

Qualified-builder directory:
`/var/lib/a733-clean-replay-20261106/host-roots-manual-beta-20261114-01/work/project/out/host-payload-manual-beta-20261114-01/`.
Protected, independently expanded/hashed raw and gzip copies are retained locally
under `out/beta-release-20261114/retained-roots/`; manifest and full provenance/
readback records are alongside that directory. These are root partition payloads,
not whole-NVMe or SD images. Keep candidate assets private pending eligibility.

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `manifest.json` | 51,663 | `35247592dbd53060a54063253ef710b3c7e2bf7786299074305a1aca2f0a908f` |
| `root-A.ext4.gz` | 143,664,060 | `01bb217112a455038512da5fbf249aa9e6d36594b325efa09583a481e5c2696f` |
| `root-B.ext4.gz` | 143,664,214 | `dc9774be176e41dd81238a317fda67b2bd40f3c12b73071c3e6750bd97981c0a` |
| `root-A.ext4` | 4,294,967,296 | `97d01e2d5e173ce4c751b49d0bd413f8852ed843493ba3d637fc0de2fa470b1e` |
| `root-B.ext4` | 4,294,967,296 | `2ea70ca197acdbb2f10851185a7bce4d5ca59d966744268267815bca6bf8ca7d` |

B is installed and booted via SPI/NVMe with SD absent. Normalized A remains
installed for rollback. The boot-lock defect and supported diagnostic workaround
are recorded in [the fix receipt](evidence/manual-boot-lock-fix-20261114.json);
this candidate is not relabeled as containing the fix. No matching fresh STATE
or whole-NVMe image has been constructed. No private package or public prerelease
has been published.

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
