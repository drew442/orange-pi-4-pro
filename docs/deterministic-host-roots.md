# Deterministic host root components

Two fresh complete A/B root constructions now match **all raw and gzip bytes**
and pass independent artifact review and full filesystem readback. The build-only
creator preserves installed maintenance sources, payload markers and the existing
STATE pairing. [Exact production evidence](evidence/deterministic-host-roots-20261109.json)
binds source `50bc9e70698d4a09c3705594dcc61c64e1715cb9` and all 869 committed
files. This is repeat proof on one qualified Ubuntu builder; independent clean
builder/deployment reproduction and hardware acceptance remain open.

The earlier [two resolver-readable roots](public-config-roots-20261109.md)
match contents and non-mtime metadata, but differ in 465 expected mtimes per
slot and filesystem creation/check/write times, hash seeds and checksums. Those
historical images remain unchanged.

## Creator policy

`build/deterministic-root-image.py` explicitly prepares a trusted private
staging tree before its expected-tree snapshot. It sets access/modification
times to epoch **1793750400**, without following symlinks or changing bytes,
ownership, permissions, xattrs, hardlink relationships or device metadata.
Creation then requires those prepared mtimes and never silently prepares input.

The creator holds a new exclusive regular-file output descriptor through all
filesystem commands. It uses the existing fixed A/B UUIDs and labels, 4 KiB
blocks, no journal, complete inode-table initialization, a fixed directory hash
seed and `E2FSPROGS_FAKE_TIME`. Numeric-inode `debugfs` commands normalize access,
modification, change and birth times plus their extra fields. Directory/file
names never enter writable editor commands. The read-only feature is enabled
only afterward; independent superblock checks and read-only `e2fsck` must pass.
No physical device, mount or filesystem repair is supported.

The pinned e2fsprogs 1.46.5 importer already uses
[sorted directory traversal](https://github.com/tytso/e2fsprogs/blob/v1.46.5/misc/create_inode.c#L760).
It also [copies source inode times](https://github.com/tytso/e2fsprogs/blob/v1.46.5/misc/create_inode.c#L103),
including ctime, which ordinary `utime` cannot set. This is why source-tree
preparation alone is insufficient. The
[fixed hash-seed option](https://github.com/tytso/e2fsprogs/blob/v1.46.5/misc/mke2fs.c#L807)
and timestamp editing address the observed creator differences. Existing
[diagnostic packaging](../build/assemble-diagnostic.sh) and
[guest seed construction](../build/guest-seed.py) use the same underlying tools.

## Registered launcher

`build/launch-deterministic-host-roots.py` holds and authenticates the unchanged
`build/launch-host-roots.py` template. Its AST changes exactly one `SELF` literal;
the original command interface, policy and isolation/resource checks remain.
The generated payload program authenticates the committed helper, prepares the
tree immediately before the original snapshot, calls the normalized creator,
and retains the original expected-tree/stability writers. Normalization records
are added only to the outer manifest and receipt, not the root payload marker.
The held-template check runs before receipt completion.

The current adapter separately authenticates the outer host's exact 842-package
inventory in `configs/builder-host-packages-20261112.lock.tsv`. Seven recorded
November 11 updates distinguish it from the unchanged historical replay/archive
lock. Both paths and pins, and only those seven version differences, appear in
`builder_package_inventory` on plans/receipts. The frozen builder and userspace
archive retain their original bindings. Missing/tampered locks or any additional
inventory difference refuse. [Validation](evidence/outer-builder-inventory-validation-20261112.json).
No historical artifact hash below is a hash for the new managed candidate.

The creator and launcher are outside the installed 39-source maintenance tree.
The unchanged installed generator remains available for historical builds; it
is not silently relabeled as deterministic. Actual markers remain A `286494…`
and B `521e86…`, preserving the reviewed STATE `2e9d3dcf…` pairing. The roots
preserve the current kernel, modules and external protected
BOOT, including its VGIC DTB.

## Completed production and remaining integration

Native e2fsprogs 1.47.0 and pinned Ubuntu 22.04/e2fsprogs 1.46.5 fixture checks
pass. Fresh 32 MiB fixture filesystems match complete raw bytes across different source
creation orders and wall times. Separate root fixtures check character devices,
capabilities, hardlinks, ownership, root xattrs and timestamp fields. These
fixtures establish creator behavior, not the full production result.

The successful tags are `timesync-deterministic-02` and
`timesync-deterministic-03`. Both completed with exit 0 under a one-CPU quota
and 3 GiB ceiling, with unchanged package inventory and cleared mapped processes
and mounts. Source checkout is
`/var/lib/a733-deterministic-host-roots-20261109-01/project`; the launcher uses
the same pinned userspace/diagnostic inputs and command flags as the
[existing root construction](public-config-roots-20261109.md), with
`build/launch-deterministic-host-roots.py` selected. All consumed tags are
reserved; later constructions must use fresh tags.

| Component | Bytes | SHA-256 |
| --- | ---: | --- |
| Root A | 4,294,967,296 | `a9715b819348a8220ec8b3b1f41ef27db677baaa96014f5d1dfb7a22597decdd` |
| Root A gzip | 143,482,835 | `87b097024dd772d6752dfb6a605618cfb8a1ce208722d056fb1e91f8c0a6f2fa` |
| Root B | 4,294,967,296 | `42fb2becf66c25a4cbb7844a6ab84ba12ab3f53c0d5d283acc8d42e63b124ab2` |
| Root B gzip | 143,482,852 | `7501e9ccd972d934c5eef71994c7a7d372f33e2b0651c818b79255c50d56b4af` |

Both outer manifests are 33,481 bytes, SHA-256
`c1bcc7d23d22bd487534ba17ea447055881a044350829608360aa92ca24283f2`.
Each producer output is under
`/var/lib/a733-clean-replay-20261106/host-roots-TAG/work/project/out/host-payload-TAG`.

Independent review authenticates 86 emitted artifacts per construction, expands
all four gzip files and compares complete raw and compressed pairs byte by byte.
All four full filesystem readbacks verify 16,368 expected paths and 451,240,558
regular bytes per slot, including devices, links and raw xattrs. Separate direct
extraction verifies 46 files per slot, including all 39 maintenance sources and
their package descriptor. Every expected member retains historical non-mtime
contents/metadata. Additional checks verify all four inode timestamp fields and
extra fields across 16,366 reachable inodes per slot, plus fixed superblock
times, UUID/hash seed and read-only/no-journal features.

[Attempt 01](evidence/deterministic-roots-failed-attempt-20261109.json) remains
failed and excluded from this successful pair. Concurrent reviewer directory
creation coincided with its final physical-parent stability check. Serializing
shared ancestor directory creation resolved the issue without weakening guards.

The [full-image profile](public-config-nvme-image.md) now passes independent
source review and 2,180 privileged Ubuntu tests; integrated construction and
actual full-image verification remain required before installation. Physical boot,
resolver policy, KVM/endurance and deployment reproduction remain separate gates.
The actual 2 GB SD and nominal 250 GB NVMe requirements are unchanged.
