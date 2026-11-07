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

The [completed kernel replay retention](kernel-run-retention.md) now replaces
the expanded `kernel-run-01` builder tree with fully verified archives on codex
and the builder. Follow its exact restoration map before historical commands.
The later measured builder free space was 6.84 GB; full production guards remain
unchanged and no LVM growth occurred.

The [2026-11-07 single-archive retention](evidence/builder-deb-retention-20261107.json)
retires the 316,373,280-byte Ubuntu `linux-firmware` DEB from the historical
builder package acquisition directory. Its original codex acquisition copy and
an additional root-owned mode0600 copy at
`/var/lib/a733-builder-deb-retention-20261107/linux-firmware.deb` both pass full
hashes before and after retirement. Independent Sol/high review and 28 disposable
tests pass; the durable builder journal confirms the one removal. Builder free
space after descriptors closed was 5,656,981,504 bytes. This is an Ubuntu builder
dependency, separate from A733 boot firmware. Before replaying a builder
construction that needs its original cache path, restore either retained copy
to the evidence's exact `builder_path`, refuse an existing destination and fully
verify the recorded size/hash. The other 841 acquired package paths are unchanged.
No storage resize, build guard reduction or physical-device operation occurred.

The later [historical root image retention](historical-root-retention.md)
preserves twelve fixed root-image files in two protected codex copies and
recovers about 3.53 GB on the builder. Its exact old/new path map supersedes
those twelve historical artifact locations. Current deployment files are excluded.

The [2026-11-06 relocation](evidence/builder-relocation-20261106.json) moves six
optional archives and two old nondeployable raw NVMe fixtures to
`codex.drewnet.online:/home/drew/orange-pi-4-pro/out/builder-retained-20261106/`.
Every logical byte and hash was verified before removing its builder copy.
At relocation time, builder free space was 26,116,837,376 bytes, above the 22 GiB
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

## Replay and prototype output retention

The [2026-11-06 output-tree record](evidence/build-output-retention-20261106.json)
identifies six generated trees retained on **codex**, under the root-private
`/var/lib/a733-builder-retained-20261106/build-output-trees/` directory.
Each archive is named `TAG.tar.gz`; the evidence records its exact length/hash.
After all archive descriptors closed, the builder had **25,061,138,432 bytes
free**, meeting the 25 GB construction planning allowance. Recheck before each
stage; full-kernel and NVMe assembler guards remain unchanged. These archives
replace the following expanded **builder** paths:

| Tag | Original builder output directory |
| --- | --- |
| `kernel-replay` | `/var/lib/a733-kernel-replay-work-20261106/out` |
| `uboot-replay` | `/var/lib/a733-uboot-replay-work-20261106/out` |
| `management` | `/home/drew/orange-pi-4-pro-build/management-work-20261104/out` |
| `package-state` | `/home/drew/orange-pi-4-pro-build/package-state-work-20261104/out` |
| `package-state-clean` | `/home/drew/orange-pi-4-pro-build/package-state-clean-20261104/out` |
| `project` | `/home/drew/orange-pi-4-pro-build/project/out` |

Historical manifests keep their original paths and bytes. Before rerunning a
historical command using a retired path, copy its archive into a fresh private
ordinary-file staging directory on the builder and authenticate its complete
size/hash against the record. Stop all users of the target and require its
`out` directory to be absent. As root, extract into the exact original parent
using GNU tar with `--keep-old-files --same-owner --acls --xattrs
--xattrs-include='*'`; preserve sparse files and refuse an existing destination.
Run GNU tar comparison against the restored tree before use. Do not overlay a
newer build. Allow at least the recorded original allocation plus staging and
operating headroom. Full restoration of these actual trees has not been
exercised; disposable restoration tests cover sparse files, links, ACLs, xattrs,
ownership and device metadata.

Each live tree passed exact archive-member-set and GNU tar comparisons. The
coordinator fully hashed and fsynced a root-owned off-host copy, held its file
descriptor through retirement, and repeated the hash afterward. Retirement
moved the selected tree into private quarantine, checked its identity/facts
and contents again, then removed it through a pinned directory descriptor.
Only afterward was the redundant builder archive unlinked. All 43 raw builder
records also have verified off-host copies under `builder-records/`. Reports
remain at `/var/lib/a733-clean-builder-retention-20261106/`; native before/after
receipts and the reviewed tool/test files remain beside the retained copies.
Independent Sol high review and actual Jammy/Python 3.10 disposable tests pass.
The 3 GiB sampled cancellation threshold is not a guaranteed continuous floor.

Current deployment work under `/var/lib/a733-deployment-work-20261106`, operator
inputs, package archives and original pinned source caches were excluded.
The current installer/host SD images and personalized NVMe bundle are unchanged.
This archival does not establish physical boot, installation or recovery.

## Failed full-kernel source retention

The [failed replay working tree](evidence/failed-kernel-source-retention-20261106.json)
was archived in full before its expanded `kernel-source/` directory was retired.
All 118,420 entry names match the archive; GNU tar compared every member against
the live tree. The 1,576,567,651-byte archive has SHA-256
`ed2f6e10fa9e16130381f29d273a9c6a7b380a8b0debb1f5828db4dd3bca84d7`
and remains in both locations:

- Historical builder path (subsequently retired after verified codex retention):
  `/var/lib/a733-kernel-source-retention-20261106/kernel-source.tar.gz`.
- Codex: `out/builder-retained-20261106/failed-kernel-source/kernel-source.tar.gz`.

Both copies passed full hashes and durability checks. Retirement held the verified
off-host descriptor, excluded active/open/mapped users and mounts, and rechecked
all 356 final artifact/log hashes. Only the fixed failed generated source tree
was removed, recovering 5,274,927,104 bytes and leaving 15,358,078,976 bytes free
at that moment. Later builds consume this headroom. The full-kernel 15 GB guard
and NVMe assembly's separate 22 GiB guard remain unchanged.

Direct archive inspection corrects the earlier claim about borrowed Git objects:
there is no alternates member, and a local 316,118,371-byte Git pack is retained.
Restoring only archived Git metadata on codex resolves the pinned shallow commit
and passes `git fsck --full --no-reflogs`, without the original builder path.
The original kernel source cache is now retained off-host as recorded below;
restore it before running a legacy recipe directly on the host. Full working-tree
restoration and rebuilding were not exercised by this metadata check. This is
independent of physical recovery.

## Retained source and images

Five guest kernel `src/` working trees, including two failed-build trees, were
compressed in full to sibling `src-working-tree.tar.gz` files. GNU tar compared
each complete archive against the live tree before its expanded copy was removed.
Images, configurations, logs, licenses, recipe archives and source snapshots were
retained. Manifest pins before and after archival match. The original pinned kernel checkout and its cached files are now retained
off-host as recorded below; comparison artifacts remain on the builder.

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
module files retain their hashes. The archive originally lived at
`/var/lib/a733-cache-retention-20261104/kernel-object-cache.tar.gz`; its current
verified copy is under `kernel-object-cache/` in the codex retention directory. Restore it
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

## Failed builder installation cache

The first offline builder installation stopped at APT's local-acquisition error.
Its rootfs, logs, provenance and receipts remain on the builder. Only the
verified duplicate `package-install/debs/` cache was archived and retired to
recover space for a fresh seed. The
[retention record](evidence/builder-install-cache-retention-20261106.json) pins
all 842 original inputs, exact archive size/hash and before/after checks.

The original builder path was
`/var/lib/a733-clean-builder/seed-probe-20261106-04/package-install/debs`.
Its 982,972,646-byte archive is retained on codex at
`/var/lib/a733-builder-retained-20261106/build-output-trees/builder-install-cache-01.tar.gz`,
SHA-256 `c6e7994a519af6401dce5dda4a8a601652d4cee2194e38eef1d6f879ae9a35c6`.
Restore it into the exact original parent before using historical commands that
name that cache. Apply the same absent-destination, size/hash and full tar
comparison checks described above. The original acquisition cache and native
842-package pool remain available separately. No board storage was touched.

## Frozen-builder duplicate archives

The [verified retirement](evidence/builder-scratch-retirement-20261106.json)
removed both raw export tars, the builder-side frozen gzip and its duplicate
failed-kernel source gzip, recovering 11,247,031,929 logical bytes. The frozen
image is retained on codex at
`/var/lib/a733-frozen-builder-retention-20261106/builder-root.tar.gz`;
expand it to recover either raw export. The failed source archive remains at
`out/builder-retained-20261106/failed-kernel-source/kernel-source.tar.gz`
and now also at `/var/lib/a733-scratch-retirement-20261106/kernel-source.tar.gz`
on codex. Earlier builder archive paths are historical and require restoration.
Current deployment artifacts, source checkouts and both builder rootfs trees
remain untouched. This operation establishes no hardware acceptance.

## Kernel-build capacity recovery

[Verified retention](evidence/kernel-space-retention-20261106.json) moved three
complete trees to root-private codex archives under
`/var/lib/a733-builder-retained-20261106/build-output-trees/`:

| Archive | Historical builder tree | SHA-256 |
| --- | --- | --- |
| `fresh-uboot.tar.gz` | `/var/lib/a733-clean-replay-20261106/uboot-run-02/work/project/out` | `284982b46d5c7c1b53a536f575bc5359371d6718fd8bf0c4cd494785818fb165` |
| `builder-debs.tar.gz` | `/var/lib/a733-clean-builder/seed-probe-20261106-05/package-install/debs` | `2be30c38a2649e6300f3a34aa68f9224ccb2ad8bd4c3995d0385ae057f74a4ec` |
| `old-kernel-source.tar.gz` | `/home/drew/orange-pi-4-pro-build/kernel` | `7ba3277e51e7a02c56e8dbccf2c6598f90fb398e965ecdca6f4e2f0f67b6346d` |

All archives were compared before removal, transferred, fully hashed/fsynced
and held open through retirement. Independent review and 30 root fixtures on
both hosts pass. Both installed builder rootfs trees, fresh upstream checkouts
and current deployment artifacts remain on the builder. Baseline artifacts
remain at `/home/drew/orange-pi-4-pro-build/artifacts`. The fresh source tree
is `/var/lib/a733-clean-replay-20261106/home/drew/orange-pi-4-pro-build/kernel`
and is mounted at the canonical workspace path inside the frozen environment.

The old source archive was actually restored on codex at
`/var/lib/a733-kernel-source-restoration-20261106/kernel`; full GNU tar
comparison, exact pinned HEAD and standalone `git fsck --full --no-reflogs`
pass. To recover a historical builder tree, transfer its pinned archive and
extract into that tree’s parent only while the target is absent and workloads
are stopped, preserving numeric ownership, permissions, ACLs and xattrs.
The archive roots are respectively `out`, `debs` and `kernel`. Recheck capacity
before restoration; the new kernel build is consuming the recovered space.
