# Deterministic root filesystem preparation

A build-only creator and registered launcher are designed to normalize root
filesystem metadata while retaining the installed maintenance sources and
payload-marker contents. [Independent source reviews and 2,178 privileged Ubuntu tests](evidence/deterministic-roots-preparation-20261109.json)
pass. Actual 4 GiB construction, complete readback and repeat-byte comparison
remain pending; no hardware acceptance is claimed.

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

The creator and launcher are outside the installed 39-source maintenance tree.
The unchanged installed generator remains available for historical builds; it
is not silently relabeled as deterministic. The normalized roots should retain
the existing marker/STATE pairing, but actual artifacts must prove that before
integration. They preserve the current kernel, modules and external protected
BOOT, including its VGIC DTB.

## Validation and next construction

Native e2fsprogs 1.47.0 and pinned Ubuntu 22.04/e2fsprogs 1.46.5 fixture checks
pass. Fresh 32 MiB fixture filesystems match complete raw bytes across different source
creation orders and wall times. Separate root fixtures check character devices,
capabilities, hardlinks, ownership, root xattrs and timestamp fields. These
fixtures establish creator behavior, not the full production result.

After committing both modules in the producer source closure, invoke the new
launcher using the same `--project`, `--userspace`, `--diagnostic`, `--tag` and
`--plan` interface as the [existing root construction](public-config-roots-20261109.md).
Use separate fresh tags `timesync-deterministic-01` and
`timesync-deterministic-02`; reused phases are refused. Keep the existing pinned
userspace and diagnostic inputs. Fully read back all four resulting filesystems,
independently inspect the artifacts, compare complete raw and gzip bytes, and
check actual payload markers and package profiles before any full-image update.
Physical installation, KVM/endurance and deployment reproduction remain separate
gates. The actual 2 GB SD and nominal 250 GB NVMe requirements are unchanged.
