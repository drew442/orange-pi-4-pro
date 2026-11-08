# Capacity for the coherent VGIC host build

[Actual retention evidence](evidence/coherent-build-capacity-20261108.json)
records retirement of three generated regular files on the Ubuntu builder:
the superseded second-repeat `root-A.ext4` and `root-B.ext4` under
`host-roots-timesync-repeat-01/work/project/out/host-payload-timesync-repeat-01`,
and `nvme.img` under
`nvme-timesync-runtime-fixes-01/work/project/out/nvme-image-timesync-runtime-fixes-01`.
All other files and the current corrected root/state/bootstrap inputs remain.

The operation releases **1,863,880,704 allocated bytes** and leaves
**24,076,783,616 bytes free** at completion, above the unchanged old full-NVMe
admission of **23,760,999,827 bytes**. Future source/capsule changes still require
their own fresh admission. No LV growth or guard reduction occurred.

Individual root gzip files remain on the builder; their full expansions and
both raw root hashes were checked before retirement. The same root payloads
also remain in the independently verified repeat NVMe gzip on codex and builder.
Both complete NVMe gzip copies were reauthenticated against their original
full-stream proofs. The 256 GB raw image was not decoded/hashed again merely to
retire its generated cache copy. Its original producer record describes
construction-time retention; this later record describes the current state.

Historical consumers needing a raw file must restore it first from its retained
individual gzip using the [exclusive sparse regular-file recovery procedure](component-retention-20261108.md#restore-a-historical-raw-root),
substituting the exact path and expected raw hash in this evidence. The same
exclusive sparse `dd` pattern restores `nvme.img` from its own gzip; verify its
256,060,514,304-byte size and recorded raw SHA-256 before consumption. These
are developer cache operations, separate from physical NVMe installation.

A separate private root-only archive on the builder retains only the running
board's machine ID and SSH host identity, for continuity during a later approved
replacement on this same board. Private key bytes were never printed or added
to Git. This backup does not restore old boot selection or the old state UUID.
No physical SD, NVMe or SPI write occurred during this capacity preparation.
