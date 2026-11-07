# Second production root construction

The [November 7 repeat evidence](evidence/production-host-roots-repeat-20261107.json)
records a fresh construction of both production **4 GiB** root slots in the
frozen Ubuntu 22.04 builder. The separate full filesystem reader passes for both
images. The first construction remains documented in
[production roots](production-host-roots.md); its evidence is unchanged.

This repeat uses source `626e3222d9d98587179722a925208fdca34127ee`, the same
138,093,095-byte `0ead62c9…` time-enabled userspace archive and the same locked
kernel, modules, DTB and configuration. It keeps the fixed root-slot UUIDs,
no-journal/read-only format, 221 packages and closed maintenance runtime.
Every non-root-image payload file has the same manifest pin. Build infrastructure
has changed between producer revisions; the injected runtime payload is unchanged.

## Executed commands

A successful `--plan` preceded the construction:

```sh
sudo python3 -I -B \
  /var/lib/a733-sparse-sd-project-20261107/project/build/launch-host-roots.py \
  --project /var/lib/a733-sparse-sd-project-20261107/project \
  --tag timesync-repeat-01 \
  --userspace /var/lib/a733-clean-replay-20261106/userspace-timesync-account-dates-01/work/export-timesync-result/rootfs.tar.gz \
  --diagnostic /var/lib/a733-bootstrap-inputs-20261107
```

The `sparse-sd` project directory is the committed source checkout already used
for the [sparse SD construction](repeat-build-retention.md). The root launcher
performs its own complete source authentication; it clones that revision into a
new root-build workspace. It does not reuse the earlier staged root directories.

Producer phase:
`/var/lib/a733-clean-replay-20261106/host-roots-timesync-repeat-01`.
Payload:
`work/project/out/host-payload-timesync-repeat-01`.
The tag is consumed and cannot be reused.

```sh
sudo python3 -I -B \
  /var/lib/a733-root-readback-controller-20261107/verify-host-roots.py \
  --receipt /var/lib/a733-clean-replay-20261106/host-roots-timesync-repeat-01/receipt.json \
  --receipt-sha256 64e413bbdd158da4e998a193e32d40c8a62c65d3051c6cb2f6aa4b955ab2efbc \
  --output /var/lib/a733-host-roots-repeat-readback-20261107-01
```

Both commands completed successfully. The unchanged reader opens regular files
read-only and uses numeric-inode `debugfs` requests; it never mounts or repairs
the images. Each slot passes its complete 16,367-path expected tree, file bytes,
links, device metadata, ownership, permissions and raw capability attributes.

## Comparison and limits

The two constructions have the same complete staged namespaces, regular-file
contents, owners, modes, links, device IDs and attributes in each slot. Exactly
**463 modification times per slot differ**. Injected runtime/module files, package-state reset directories and their parents
retain their new staging times. Filesystem creation
also runs at the new wall-clock time. Independent superblock inspection confirms
different creation/check/write times and directory hash seeds, with identical
UUIDs, geometry and immutable/no-journal features. The current recipe does not
fix those construction fields. Raw/gzip image hashes differ: this is a successful
semantic repeat, **not byte-identical root-image reproduction**. The evidence
records the detailed timestamp comparison and passing independent review. The
review rehashes both raw/gzip images and fully expands both gzip streams on the
builder. Complete file/xattr comparison is evidence from the reviewed reader
execution; its atime, ctime and crtime exclusions remain explicit.

The builder's 842-package inventory is unchanged, with no mapped processes or
phase mounts left. Sampled minima are **22,909,526,016 bytes free disk** and
**7,282,962,432 bytes available RAM**. Construction ends with
23,399,813,120 bytes free, already below the unchanged full-NVMe 22 GiB initial
guard before additional staging. Recheck capacity and retain superseded work
before later composition; do not lower guards or expand the LV again.

Native metadata, expected trees and all full-readback logs are in
`out/host-roots-timesync-repeat-01-20261107`. New raw/gzip roots remain on the
builder; they have not been copied to codex. Matching unchanged non-image
payload files can be referenced through the original retained payload, using
exact manifest pins. Current published operator images remain unchanged.

A repeat personalized state needs fresh identity and explicit bindings to these
new roots, followed by matching bootstrap and full NVMe composition. Historical
state/bootstrap/NVMe controllers still bind the first construction; their
records cannot be relabeled as a new chain. This root repeat establishes no
physical boot, SSH, automatic time, KVM guest, SPI recovery or SD-write acceptance.
The [existing-install management repair](installed-host-repair-20261107.md)
remains the next board action.
