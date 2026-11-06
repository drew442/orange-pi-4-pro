# Normalized userspace export

[Two actual normalized exports](evidence/userspace-canonical-export-20261106.json)
now produce identical compressed archives, raw tar streams and BOM bytes from
the [two separately configured ARM trees](userspace-configuration-replay.md).
Independent complete readback passes. Both configurations shared one authenticated
foreign-stage source. A subsequent [fresh raw-package pipeline](userspace-fresh-pipeline.md)
now produces matching bytes. Production image composition and physical deployment
remain open. The published installation images are unchanged.

## Generic-base cleanup

[`export-host-userspace.py`](../build/export-host-userspace.py), committed in
`9964f93`, retains its fixed clone path, restricted frozen-builder environment,
authenticated configure receipt, exact 220-package inventory, source-integrity
checks, exclusive publication and existing size/resource guards. It runs no
target executable and creates only archive headers for devices.

In addition to generated SSH keys, machine identity, random seeds, logs and
build-only inputs, it removes these exact generated files:

| Path | Purpose |
| --- | --- |
| `etc/nvme/hostid` | Exclude build-generated host identifier from generic base |
| `etc/nvme/hostnqn` | Exclude build-generated host NQN from generic base |
| `var/cache/apt/pkgcache.bin` | Remove regenerable binary package cache |
| `var/cache/apt/srcpkgcache.bin` | Remove regenerable source package cache |
| `var/cache/ldconfig/aux-cache` | Remove build-generated linker auxiliary cache |

Before cleanup writes, each existing parent must be a physical, namespace-root
owned directory and each existing target a namespace-root owned, single-link
regular file. Optional missing paths are accepted. Symlinks, external hardlinks
and unexpected ownership fail before mutation. Unrelated configuration survives.
Generated identifier values are neither logged nor placed in receipts.

The earlier comparison found four changing file contents. The source package
cache was identical between those runs, but is also generated and unnecessary
in the exported base; removing it accounts for the fifth removed member.

## Timestamp contract

The fixed archive epoch is **1790812800**, October 1, 2026 at 00:00:00 UTC,
matching the pinned APT snapshot. For each archive/BOM entry:

```text
archive_mtime_ns = min(native_source_mtime_ns, 1790812800 * 1000000000)
```

The clamp follows the approach described in [GNU tar’s reproducibility guidance](https://www.gnu.org/software/tar/manual/html_node/Reproducibility.html); this exporter implements the policy directly in its Python PAX writer.

Earlier package timestamps, including fractional seconds, survive. Later build
timestamps are clamped. There is no environment or CLI epoch override. Clamping
copies scan records; it does not call `utime` on source files. The ordinary
cleanup operations still modify their named source paths and parent directories.
Native post-cleanup snapshots and inode identities are compared before and after
archive creation independently of the normalized archive records. Normalization
cannot hide a concurrent native metadata change.

Both actual exports clamp **2,763 entries**. All 556 Python source files in the
recorded tree already precede the epoch, so their timestamps are preserved.
Content, ownership/modes, xattrs, hardlinks, symlinks and device identities retain
the existing full checks. The gzip header is fixed and archive traversal remains
deterministic for these inputs.

## Actual results

The code and both trusted outer wrapper variants passed independent review
before execution. Each actual run used a fresh restricted namespace with the
same read-only frozen OS and inputs, private network, fixed UID/GID map,
no-new-privileges and seven capability drops. The existing configured trees were
already audited and exported under the preceding policy; this run applies the
new cleanup and timestamp contract to those retained trees.

Both copies are retained on codex. Their unused builder copies were later moved
off the builder by [verified capacity retention](userspace-rebuild-capacity.md):

- `out/userspace-export-canonical-01-20261106/rootfs.tar.gz`
- `out/userspace-export-canonical-02-20261106/rootfs.tar.gz`

Each compressed archive is **138,025,729 bytes**, SHA-256
`98d6a57bbb70ff55bef32b6bd051ebfcb6bc0fad4f6c8b3167e8619b5a9d961e`.
Each raw tar is **468,398,080 bytes**, SHA-256
`a3b1ccfb69c24844d50cf60fb4c4286eb366dc6ba8da0f77ff672c8202b6e730`.
Each contains **15,876 members**: 12,295 regular files, three hardlinks,
1,720 directories, 1,850 symlinks and eight character headers.

Standalone raw tar/PAX readback checks every content hash/byte, timestamp, mode,
owner, link, device and xattr in both complete archives. Each BOM equals its
previously verified historical BOM with only the five approved paths removed
and timestamp clamping applied. Full compressed-byte and BOM-byte comparisons
pass. Both archives retain exactly 220 configured locked package tuples, a locked
root account and empty machine ID, with generated identity/build paths absent.
Distinct configure receipts preserve the two inputs' provenance.

Both host inventories remain unchanged, with no mapped processes or output
mounts left. Each run records 52 resource samples; minima are **3,464,351,744 bytes
free disk** and **7,417,167,872 bytes available RAM** across the pair. Cleanup
recovered enough space to retain both archives during those runs; no retirement
or resize occurred in that export phase. Later retention is tracked separately. Raw logs, input wrappers, readback code and receipts remain
under the two ignored output directories with hashes bound by the evidence.

Native validation passes **1,098 discovery cases** with 96 environment skips,
four assembly cases and all five shell suites. The 30 focused exporter cases
include unsafe-cleanup rejection, unchanged native times, byte equality despite
generated variations and detection of source changes hidden by clamping.

The subsequent [fresh pipeline](userspace-fresh-pipeline.md) completes a new
foreign extraction/configuration/export from raw pinned binary inputs and matches
this pair. Next, consolidate outer orchestration and integrate the selected
userspace profile into production root/state/NVMe reconstruction and full readback. Continue the [physical deployment](deploy-personalized-20261106.md)
and [hardware acceptance](hardware-validation.md) separately. This generic archive
is a build result; the deployment guide still selects the published images.
