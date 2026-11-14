> **Historical record — not active instructions or release gates.**
> Superseded by the [manual beta scope](../../beta-scope.md) and
> [current status](../../implementation-status.md). Original observations retain
> their recorded configuration and limitations.

# Resolver-readable host maintenance candidates

New A/B update candidates and portable tools now match the
[corrected root construction 01](../../public-config-roots-20261109.md).
Independent actual-artifact review passes. They have **not** been applied to
the Orange Pi, and the installed host remains unchanged.
[Evidence and exact pins](../../evidence/public-config-maintenance-20261109.json)
separate file preparation from physical update, activation and rollback.

Builder-ready candidates are:

```text
/var/lib/a733-public-config-maintenance-20261109-01/candidate-A/
/var/lib/a733-public-config-maintenance-20261109-01/candidate-B/
```

Each contains `root.ext4`, `manifest.json`, `selected.conf` and `candidate.json`.
The 4 GiB raw images match construction 01 hashes `299b1f2f…` (A) and
`48b9876f…` (B); full hashes are in the linked evidence. Root files are private,
root-owned, mode 0400, and sparse. The root-payload manifest is 32,613 bytes,
SHA-256 `1f82b9e85c5fbcbac65af695b699ee0048fb397faeba2e3eeb652eeb584adb6c`.
Do not substitute the earlier `4f28…` root manifest or a whole-drive manifest.

The selectors are reconstructed from the actual root markers:

| Slot | Selector bytes | Selector SHA-256 |
| --- | ---: | --- |
| A | 179 | `3c76b96c328b4508ff407c8594352e04389664e97ced6f0a09284cade5bd027b` |
| B | 179 | `27ea5d897aa590de9efbe42800894b6a0645ec855ef68012897684f87efe2971` |

Preparation uses the existing reviewed `scripts/prepare-host-update.py` with
`--payload-manifest` pointing to construction 01, the manifest hash above and
`--storage-kind external`. Outputs must be fresh absolute directories in a
trusted private parent. The producer requires 5 GiB free per candidate, retains
source identity and full hash checks, and changes no installed selector or disk.

## Portable package

Builder package:
`/var/lib/a733-public-config-roots-20261109-01/project/out/maintenance-tools-public-dirs-20261109-01`.
Native transport copy: `out/public-config-maintenance-20261109-01/portable`.
The native candidate directories contain metadata only; their raw root files
remain on the builder.

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `tools.tar.gz` | 124,193 | `87279790979079ef707a817931fc958e440e44204239e7b5897a86de231056bf` |
| `manifest.json` | 16,312 | `1ce0e5daaf760a8407914986dc61314c13091239be68db5948a2765de0c21d97` |

All 40 published files, 39 source/root-provenance comparisons, 40 recipe pins
and 46 archive members pass independent inspection. The private output container
is mode 0700; its portable source tree directories are 0555 and files 0444.
The preparer and package code are from source
`4f40843365566ae4ada9b7377b75bba3f40c3360`.

The first aggregate job completed both candidates, then failed because the
portable output path was outside its required checkout `out` directory. A
separate successful job packaged only the tools at the correct fresh path and
fsynced all 50 published files and their publication directories (12 directory-sync
calls, including repeated parents). The failure and corrected
completion remain in the evidence. No source change or candidate rerun was
needed.

## Physical use remains pending

Retain endurance outcome evidence and current mutable STATE/identity/guest
backups before maintenance. Revalidate exact NVMe identity, current root and
selector; stage only the inactive slot with [host update guards](../../host-updates.md).
Use [explicit activation and manual rollback](../../host-root-selection.md) and
[offline recovery](../../host-recovery.md), preserving the external protected BOOT.
Known storage authority persists; this preparation does not add a permission
request. Physical interruption/rollback and resolver-policy verification remain
required. A coherent new full installation image also needs a matching STATE
selector; these candidates do not update the older whole-drive distribution.
