# Userspace rebuild capacity retention

[Verified retention](evidence/userspace-space-retention-20261106.json) moved five
unused generated builder copies to codex while preserving their complete bytes.
This recovered approximately **1.12 GB of allocated space**. The first postcheck
showed **4,575,662,080 bytes free**; subsequent builds consume that space.
No filesystem resize occurred, and production image capacity guards are unchanged.

The selected artifacts are the 1 GiB diagnostic metadata filesystem and four
historical/normalized generic userspace archives. They are regular build files;
the deployment SD images and personalized NVMe bundle were excluded. Original
package caches, source trees, configured roots, foreign data and build receipts
remain available on the builder.

## Retained locations

The evidence maps every exact builder path to its size, SHA-256, original codex
copy and protected second copy. Later [candidate capacity preparation](candidate-repeat-capacity.md)
archives the ordinary diagnostic raw image on both hosts and retires only that
ordinary file; its protected raw second copy remains available, now sparse.
The other ordinary codex originals remain at:

| Artifact | Original codex file |
| --- | --- |
| Diagnostic filesystem | Historical raw path retired; use the [current recovery map](candidate-repeat-capacity.md) |
| First configured export | `out/userspace-export-20261106/rootfs.tar.gz` |
| Repeated configured export | `out/userspace-export-recipe-20261106/rootfs.tar.gz` |
| First normalized export | `out/userspace-export-canonical-01-20261106/rootfs.tar.gz` |
| Second normalized export | `out/userspace-export-canonical-02-20261106/rootfs.tar.gz` |

Separate protected copies are at
`/var/lib/a733-userspace-space-retention-20261106/retained-0.ext4` and
`retained-1.tar.gz` through `retained-4.tar.gz`, respectively, on codex. Their
parent is initial-root owned mode `0700`, with each copy mode `0600`. All originals
and protected copies match their published hashes. The five logical lengths total
1,681,167,255 bytes; sparse diagnostic image allocation explains the smaller
recovered amount.

If a future replay requires a retired builder file, restore its exact bytes from
the mapped codex copy to that evidence-listed builder path, verify the full size
and SHA-256, and check the destination is an unused regular file. Preserve the
retention ledger and refuse an unexpected existing destination. The current
installation procedure uses separate files and needs no restoration.

## Verification and removal

The coordinator ran the independently reviewed helper only from a protected
root-owned `0400` snapshot after checking its complete SHA-256. Before remote
removal, it verified and fsynced every native original and protected copy and
kept their file descriptors held. It rechecked them after SSH, including the
failure path. Independent postreview rehashed every copy and the coordinator.

The remote helper checks all five complete file lengths/hashes before the first
removal, rejects nonregular files, hardlinks and symlink ancestors, and checks
open descriptors, mappings, mounts in process namespaces and loop backing files.
It revalidates identity and content before each exact file unlink. Other paths
cannot be selected through arguments.

An exclusive append-only journal records a durable intent before each unlink,
then fsyncs the parent directory and records completion. Writes loop until all
bytes are written; earlier records survive a failed or interrupted later write.
The actual journal has 12 records: start, five ordered intent/completion pairs
and complete. Its final summary and absence postchecks match all five targets.
This is a selected-file audit, not a whole-host mutation audit.

All 13 disposable safety fixtures pass, including short writes, interrupted
later journaling, alias/replacement rejection and active-use checks. Source and
actual records passed independent Sol high review. Raw code, tests, journals and
postchecks remain in `out/userspace-space-retention-20261106/`, with hashes in
the evidence. No physical SD/NVMe erase, SPI programming or new hardware test
was performed.
