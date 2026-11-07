# Frozen-builder root reconstruction preparation

Full NVMe/root/state reconstruction remains open. The published SD and NVMe
installation candidates are unchanged; use the current deployment guide.

The authenticated ARM64 userspace archive contains eight character entries:
`dev/null`, `console`, `urandom`, `tty`, `ptmx`, `full`, `zero` and `random`.
The frozen namespace intentionally denies real device creation. Its pinned
`fakeroot` and `libfakeroot` packages are already version `1.28-1ubuntu1`.

The [device fixture](evidence/fakeroot-device-metadata-20261106.json) passes
inside the existing isolation boundary: eight ordinary host files produce eight
correct character-device records in a regular ext4 image, with expected device
numbers, mode and ownership. No actual host block/character node was created.
This validates that small metadata path, not a complete root filesystem build.

The [two-phase metadata candidate](evidence/fakeroot-metadata-candidate-20261106.json)
now passes independent postchecks. Ordinary source archive creation preserves
real ACL/capability metadata; extraction under fakeroot disables tar's ACL/xattr
restoration, then applies validated raw PAX xattrs after inode types exist.
Source/restored snapshots match, and image attributes match an ordinary
`mke2fs` reference. Named/default ACL semantics match the source; basic ACL IDs
are normalized by e2fsprogs during export. Capabilities and binary user xattrs
match exactly. Production root/state image verification remains pending.

The [complete archive diagnostic](evidence/full-root-metadata-fixture-20261106.json)
now restores and validates all 15,892 members: 12,302 regular files, three
hardlinks, 1,737 directories, 1,842 symlinks and eight character nodes. The
staging comparison covers namespace, types, modes, UID/GID, nanosecond mtimes,
file lengths/hashes/bytes, hardlink topology, link targets, xattrs and device
identities. A regular 1 GiB diagnostic image retains all eight device records,
nonroot group ownership and the exact capability value; e2fsck passes and an
outside-fakeroot scan finds no real host devices. Independent review confirms
these execution/probe records and the complete image hash.

This is not a production root slot or full NVMe test. The staging tree was not
copied to codex; its complete comparison is supported by execution records and
reviewed code. Its original image checks were selected metadata probes and
consistency. A subsequent [complete diagnostic-image readback](evidence/full-root-image-readback-20261106.json)
now checks every expected path and all 518,081,083 regular-file bytes against
the authenticated source record. It verifies 12,302 unique regular inodes,
1,842 symlink targets (including 99 stored outside the inode), all three hardlink
groups, eight device identities and the raw capability. The only additional path
is the expected empty, root-owned `0700` lost+found directory. Image hashes
before and after remain identical. Production 4 GiB A/B slots and their existing
staging guards remain unchanged and unexercised.

Metadata comparisons account explicitly for the pinned creator: mke2fs 1.46.5
copies child modification times at whole-second resolution and leaves the root
at the fixed filesystem creation epoch. The source has 2,774 fractional-second
modification times, including the root; this is not nanosecond preservation.
Access/change/creation times and image directory sizes are generated metadata
outside this comparison. The [pinned population source](https://github.com/tytso/e2fsprogs/blob/v1.46.5/misc/create_inode.c#L111)
explains the time conversion. Independent code, Unicode-fixture and execution
reviews pass. Verification code and raw records remain under the ignored
`out/full-root-image-readback-20261106/` directory with recorded code hashes.

The optional `fakeroot-raw-xattrs-v1` backend is now integrated into both root
and state recipes; their default remains `native`. It requires the pinned
Ubuntu 22.04 amd64 fakeroot session and authenticates a held archive descriptor
before and after extraction. It preflights the complete namespace and raw
attribute limits, then checks all staged content, metadata and hardlink groups.
Selected-profile manifests retain the receipt, helper and package-lock provenance.
The helper is build-only; the closed maintenance runtime remains unchanged.

The [backend integration probe](evidence/root-metadata-backend-20261106.json)
executes `host-payload.extract_rootfs` on the entire authenticated archive inside
the frozen namespace. All 15,892 members pass, including eight virtual devices
and the raw capability; the outside-fakeroot actual-device scan is empty. Eight
resource samples remain above 6.04 GB free disk and 7.42 GB available RAM.
Independent software and execution-evidence reviews and 24 focused tests pass.
Complete payload/state builder paths have unit coverage; their production image
runs remain pending.
This probe creates no filesystem image. Existing published images are unchanged;
replay their historical source revision as specified in
[SD image reproduction](sd-image-reproduction.md). New recipe source/provenance
will produce a distinct future artifact profile.

Preserved failures explain the method: the first fixture used an invalid empty
xattr baseline; direct tar xattr restoration exposed a tar/fakeroot zero-type
interaction; the checker needed to distinguish Linux/debugfs ACL export formats.
The full archive's first launcher also stopped before extraction because Bash
was invoked as sh; a new Bash-launched workspace completed successfully. No
failed result was accepted or overwritten. The actual archive contains one raw
capability xattr, no missing raw ACL counterpart and no attribute exceeding the
pinned fakeroot IPC limits.

The [approved 20 GiB builder growth](build-host-capacity-plan.md) has now been
executed. Online ext4 enlargement leaves 24.28 GB free, passing the unchanged
22 GiB staging guard by about 663 MB. Production A/B roots can be constructed
next; their allocated data and compressed copies will consume that headroom.
Measure allocations and retain verified outputs safely before state/final NVMe
composition. No production image or deployment success follows from expansion.

No fixture or storage preparation establishes installation, actual KVM guest
execution, SD-write/endurance behavior, SPI boot or release acceptance.
