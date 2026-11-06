# Userspace source archives

All 220 retained binary packages passed their existing archive hashes before
control metadata was read. The [binary-to-source map](evidence/host-userspace-source-map-20261106.json)
accounts for every locked package and resolves 149 source package/version pairs.
It preserves explicit source versions, including differences caused by binary
rebuilds, using [Debian's Source-field rules](https://www.debian.org/doc/debian-policy/ch-controlfields.html#source).
Neither `Built-Using` nor `Static-Built-Using` was present in these control records;
that observation does not establish a complete license audit.

The [source archive lock](../configs/host-userspace-source-archives.lock.json)
selects all 149 versions from the authenticated Bookworm source index for the
existing `20261001T000000Z` snapshot. It pins 479 files totaling 570,785,529 bytes,
including source-control descriptions and their referenced source archives.
The [index receipt](evidence/userspace-source-index-20261106.json) records the
release/keyring pins, three verified signatures and compressed/expanded index
hashes. The source index was retrieved from Debian's live by-hash location after
snapshot gateway errors; its bytes exactly match the original signed snapshot
release. The target snapshot and versions were not changed.

Archive acquisition is being prepared separately. A source lock or a successful
index download does not establish that all source archives have been retained,
that package source obligations have been satisfied, or that a release is ready.
Archive downloads must match the complete expected length and SHA-256 before
publication. Keep all downloaded packages and source trees outside Git.

This inventory covers the interim Debian userspace. Vendor kernel/U-Boot,
BusyBox and opaque firmware/tool inputs have separate provenance and license
records. Source extraction, build instructions, notices and any additional
corresponding-source requirements remain part of the release audit. Physical
boot, actual KVM execution and reproduced deployment remain independent gates.
