# Packaged userspace notice coverage

The [November 7 coverage evidence](evidence/userspace-notice-coverage-20261107.json)
accounts for all **221** locked packages in the verified time-enabled root tree.
Each `/usr/share/doc/PACKAGE/copyright` resolves to a regular file. Seven
packages use shared documentation-directory symlinks; the evidence records the
complete resolution and exact terminal content size/hash for each package.
There are 149 distinct notice content hashes and 14 regular common-license
files under `/usr/share/common-licenses`.

The inventory uses the complete expected tree previously checked by the
[second root construction's independent full readback](production-host-roots-repeat.md).
The coordinator independently resolved every path and compared package count,
lock hashes, terminal file records and source inputs. This does not rerun image
readback or determine the legal sufficiency of the notices' contents.

`systemd-timesyncd` adds a binary row beyond the original 220-package
[binary-to-source map](evidence/host-userspace-source-map-20261106.json). Its
62,172-byte Debian package matches the pinned SHA-256; direct control metadata
reports source `systemd`. The time-profile input contract records source version
`252.39-1~deb12u2`, already represented by seven binary rows in the original map.
The three corresponding source archives were rehashed against their exact pins.
No new source/version pair is needed for this added binary. The original map is
unchanged; the evidence distinguishes binary-row absence from source coverage.

Package notices and common-license files remain inside the root filesystem.
The [source archives](userspace-sources.md), signed-index receipts and exact
build recipes remain separate retained provenance. Source acquisition, notice
presence and hashes do not establish that every corresponding-source obligation
has been satisfied. Opaque vendor firmware and proprietary packing tools retain
[separate unresolved evidence](evidence/vendor-boot-license-inventory-20261106.json).
No blanket repository license or public firmware redistribution clearance is
claimed. No physical boot, KVM, SD-write or deployment result follows from this
inventory.
