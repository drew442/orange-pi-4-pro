# Builder cache retention

The Jammy builder's generated work had reduced free space to 9.3 GB. Verified
working-tree archival and byte-preserving image sparsification increased it to
**23.9 GB (about 22.25 GiB)**. This passes the full NVMe assembler's **22 GiB**
staging guard at the recorded measurement, with only about 260 MiB above that
threshold. Recheck disk and memory before every payload/NVMe build; later output
can consume that headroom. Compilation remains limited to two jobs initially.

[Retention evidence](evidence/build-cache-retention-20261104.json) records exact
archive hashes, raw journal pins, allocation changes, source checks and limits.
Raw records and the one-off scripts remain outside Git on the builder at
`/var/lib/a733-cache-retention-20261104/`. Readable copies of the records are at
`/home/drew/orange-pi-4-pro-build/cache-retention-evidence-20261104/`.

## Current off-builder retention

The [2026-11-06 relocation](evidence/builder-relocation-20261106.json) moves six
optional archives and two old nondeployable raw NVMe fixtures to
`codex.drewnet.online:/home/drew/orange-pi-4-pro/out/builder-retained-20261106/`.
Every logical byte and hash was verified before removing its builder copy.
Newly observed builder free space is 26,116,837,376 bytes, above the 22 GiB
assembly guard; later builds consume this headroom. Original manifests remain
on the builder, but the eight original file paths in older records are now
absent. Follow the exact old/new path mapping in the new evidence record.

Restore one retained file to its original builder path before using a historical
command that names it. Authenticate its size/hash against the record, refuse an
existing destination, preserve sparse holes for the two raw images, and verify
the restored bytes before use. The two raw fixtures retain their original
250,000,000,000-byte sizes; they are not the actual-geometry installation image.
Restoring raw fixtures requires sparse transfer and sufficient free allocation.

The existing recovery commands below describe the historical builder locations.
For example, the fixed-seed archive is now retained under `seed-source/` and the
compiler-object archive under `kernel-object-cache/` within the codex directory.
Transfer and authenticate them at their original paths before those commands.
The reviewed relocation script and durable verification/acknowledgment records
are retained beside the copies. No physical NVMe or SPI operation occurred.

## Retained source and images

Five guest kernel `src/` working trees, including two failed-build trees, were
compressed in full to sibling `src-working-tree.tar.gz` files. GNU tar compared
each complete archive against the live tree before its expanded copy was removed.
Images, configurations, logs, licenses, recipe archives and source snapshots were
retained. Manifest pins before and after archival match. The original pinned
kernel checkout, configuration, built Image and modules remain in place.

Forty-one manifest-listed generated images were made sparse by replacing
allocated zero blocks with holes. Their logical bytes, lengths, paths, inodes
and modes are unchanged; full manifest hashes pass before and after each
operation. Physical allocation and file timestamps may change. The procedure
used held regular-file descriptors, refused symlinks/devices, excluded loop/open/
mapped image users and required an idle builder. Independent review corrected
its evidence-output guards before execution. The exclusion is not protection
against a later noncooperating writer; keep the builder quiescent during such
maintenance.

The diagnostic and experimental image hashes are unchanged. Independent EL2
artifact inspection passed again, including packed firmware, boot/filesystem
contents and full gzip readback. This does not establish a new physical boot.

A subsequent [compiler-object retention](evidence/kernel-object-retention-20261104.json)
archived 11,149 generated, untracked kernel cache files before removing their
expanded copies. All tracked source plus 323 protected configuration/Image/map/
module files retain their hashes. The compared archive remains at
`/var/lib/a733-cache-retention-20261104/kernel-object-cache.tar.gz`. Restore it
only against matching source/configuration with no newer objects to overwrite;
otherwise let the next two-job kernel build regenerate its cache. This creates
headroom for refreshed roots without discarding compiled evidence.

## Recover an expanded guest build tree

For the latest fixed-seed fixture, the following recovery was actually exercised
in a root shell on the builder. All restored members passed GNU tar comparison;
its Image and configuration also matched the published fixture. Its expanded
copy was then removed again to retain space. The full archive remains.

```sh
cd /var/lib/a733-el2-checks-20261104/out/guest-fixture-seed-20261104-01
printf '%s  %s\n' 534c0ff2b1e210a92284df6cc30868983bd46a00effb6de06907561f7c0800ff src-working-tree.tar.gz | sha256sum -c -
test ! -e src
test ! -L src
tar -xzf src-working-tree.tar.gz
tar -dzf src-working-tree.tar.gz
```

Use the retained hash and exact parent from the evidence record to restore any
other tree. Refuse an existing `src` rather than overlaying newer work. Recovery
needs roughly 2.1 GB of temporary space; restore only when no concurrent build or
image operation is using the directory. Leave the verified archive intact.

These are builder source-cache recovery checks. They do not replace SPI backup,
physical host rollback, off-device guest restoration or reproduced deployment.
No board storage was erased, and no SPI programming occurred.
