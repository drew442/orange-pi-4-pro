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
match exactly. Full-root integration/verification remains pending.

Preserved failures explain the method: the first fixture used an invalid empty
xattr baseline; direct tar xattr restoration then exposed a tar/fakeroot zero-type
interaction; the final checker also needed to distinguish Linux/debugfs ACL
export formats. The raw evidence records these failures and corrected postchecks.
The actual pinned userspace archive has 15,892 members, eight character nodes
and one raw `security.capability` attribute. No inspected attribute exceeds
fakeroot's 1,024-byte IPC limits, and no textual ACL lacks a raw counterpart.
This header audit does not replace complete archive/image verification.

Capacity is a separate issue. The filesystem has about 7.68 GB free, below the
unchanged root/NVMe build guards, while its VG has 66.54 GB unallocated. The
[reviewed growth proposal](build-host-capacity-plan.md) would allocate 20 GiB to
the builder's existing root LV and enlarge ext4. Inventory, metadata backup,
allocation dry-runs and live precondition checks pass. Operator approval is
pending; no LV or filesystem resize has been performed.

No fixture or storage preparation establishes installation, actual KVM guest
execution, SD-write/endurance behavior, SPI boot or release acceptance.
