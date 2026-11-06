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

[Acquisition and independent readback](evidence/userspace-sources-acquired-20261106.json)
now pass for all 479 files. All came from Debian's primary archive with exact
length/SHA-256 matches; no fallback was used. The complete retained set is at
`out/userspace-sources-20261106/pool/`. Its receipt is 141,049 bytes, SHA-256
`b043ca441f7c79cbf1d296603c6b418720bb7d0a517f609133f3671d6ccee624`.
No source archive was extracted or executed. Keep these generated files outside Git.

The actual acquisition ran from clean project commit
`321005217540d0725ee3728af0d7151b0a2d0883`:

```sh
python3 -I -B build/fetch-userspace-sources.py \
  --output "$PWD/out/userspace-sources-20261106"
```

For another acquisition, use a new `out/userspace-sources-NAME` directory.
`--resume` requires the original clean commit and input hashes, verifies all
existing archives, refuses corrupt files and only fetches missing ones. Two
workers verify each file before publishing it, and the complete cache is hashed
again before a success receipt. A changed checkout requires a separate output.
Archive acquisition does not establish that package source obligations have
been satisfied or that the release is ready.

This inventory covers the interim Debian userspace. Vendor kernel/U-Boot,
BusyBox and opaque firmware/tool inputs have separate provenance and license
records. Source extraction, build instructions, notices and any additional
corresponding-source requirements remain part of the release audit. Physical
boot, actual KVM execution and reproduced deployment remain independent gates.
