# Normalized host maintenance candidates

The normalized A/B update candidates now pass independent actual-artifact review.
They have not been staged, activated or rolled back on the Orange Pi. [Exact
pins and offline compatibility evidence](evidence/normalized-maintenance-20261109.json)
separate these regular-file results from physical acceptance.

Builder candidates are
`/var/lib/a733-normalized-maintenance-20261109-01/candidate-A` and `candidate-B`.
Each contains a sparse 4 GiB `root.ext4`, `manifest.json`, `selected.conf` and
`candidate.json`, in a private root-owned directory with single-link mode-0400
files. Native metadata is retained under `out/normalized-maintenance-20261109-01`;
raw roots remain on the Ubuntu builder.

| Slot | Raw root SHA-256 | Selector SHA-256 (179 bytes) |
| --- | --- | --- |
| A | `a9715b819348a8220ec8b3b1f41ef27db677baaa96014f5d1dfb7a22597decdd` | `3c76b96c328b4508ff407c8594352e04389664e97ced6f0a09284cade5bd027b` |
| B | `42fb2becf66c25a4cbb7844a6ab84ba12ab3f53c0d5d283acc8d42e63b124ab2` | `27ea5d897aa590de9efbe42800894b6a0645ec855ef68012897684f87efe2971` |

The 33,481-byte root manifest is
`c1bcc7d23d22bd487534ba17ea447055881a044350829608360aa92ca24283f2`.
Both complete source/copy byte comparisons and raw hashes pass; direct extraction
matches the 40 maintenance files and four management files per root to their
reviewed source/portable closure. Kernel ABI, UUID, markers and selector bindings
pass. Preparation used unchanged reviewed code from source
`81f907481b12e9639ad23752966e05166074f96f`.

The portable export is under the same source checkout's
`out/maintenance-tools-normalized-20261109-01`. Its archive, manifest and complete
tree match the [previous verified package](public-config-maintenance-20261109.md)
byte for byte: archive `872797…`, manifest `1ce0e5…`. The review checks 40 files,
39 source comparisons, 40 recipe pins and 46 archive members. These tools remain
separate from the SPI caller/capture preparation. The first private review harness
used the archive root name for the published tree path and failed; the corrected
harness passes, with the initial failure retained and no artifact change.

## Physical test order after endurance

First retain and review the full endurance outcome, exit records, UART capture,
NVMe health and post-workload SD measurements. Preserve current STATE/identity, guests
and prior failure evidence before maintenance or a whole-drive reinstall. Known
storage authority persists; it does not replace fresh target and guard checks.

For interruption testing, verify that the actual inactive B is the earlier
payload before staging normalized B. The recorded old B is `3369…`; new B is
`42fb…`. Testing a changed payload before the fresh full-image install avoids
using an identical-image rewrite as the sole interruption test. Keep old active
A and its `e1cc…` selector intact until staging completes and a separately validated activation plan selects B. Then test corrected B and manual rollback to old A using
[update guards](host-updates.md) and [root selection](host-root-selection.md).
Retain each actual plan, interrupted/complete result, selector and boot record.

Offline execution of the authenticated old updater's complete `Source` reader
accepts new B, and its selection-manifest reader accepts `c1…`. Corrected tooling
accepts retained old A's metadata/selector. Actual-marker and synthetic-mount
fixtures preserve old A plus STATE UUID and refuse staging while B is pending.
These are compatibility checks; real block-target, mounted-root, timesync and
runtime guards have not been exercised by this review. Stop on any live mismatch.

Fresh installation and independent deployment reproduction follow these cases,
using the final independently verified distribution. Select the final boot route
before counting ten warm and ten cold release boots. SPI recovery and SD-removed
boot, or complete immutable-SD proof, remain physical acceptance gates.
