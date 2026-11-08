# Registered runtime-corrected NVMe stream reader

`build/verify-nvme-runtime-fixes-stream.py` independently reads the single
registered corrected-chain full-NVMe gzip artifact from binary stdin. It is
separate from the producer's reviewed source/project; adding this reader does
not require rebuilding that producer. The [actual completed corrected-chain
image](production-nvme-runtime-fixes.md) now passes this reader over its complete
compressed wire and 256,060,514,304-byte expansion. Independent source review and 31 focused
no-fork tests pass. Full privileged Ubuntu discovery passes **1,777 tests**
without skips, errors or failures. These software checks do not establish
physical boot or deployment acceptance.

The reader holds the physical sibling `build/verify-nvme-stream.py` with SHA256
`02b04bce52597d9a4ca00f19f4c520fe357e3ba437de43aa1bef38978474684d`.
Exactly seven counted AST string literals change, each occurring once:

| Registered field | Corrected-chain value |
| --- | --- |
| Producer source revision | `6c34629c424a1bb414780b26461e06ccd2854ae9` |
| Producer phase | `/var/lib/a733-clean-replay-20261106/nvme-timesync-runtime-fixes-01` |
| Producer output | `work/project/out/nvme-image-timesync-runtime-fixes-01` |
| State UUID | `360dfb39-f49f-487e-b307-0bb887c854c5` |
| Roots canonical evidence SHA256 | `2c04a75d5239224b80a721442e88ea872ccd63d18aaae841539d007697302971` |
| State canonical evidence SHA256 | `4d3b694fd55fc1d40b73b1de8c3b0d2e9f9dce9ca2fb8c1dda058dfb77a1e6c1` |
| Bootstrap canonical evidence SHA256 | `60d40b557637f1b7da3785e45fde52db37b692e36d30d8156616f48c9e8f9176` |

The producer runs the existing project at
`/var/lib/a733-nvme-runtime-fixes-project-20261108-01/project` with tag
`timesync-runtime-fixes-01`. Its original component records are
`docs/evidence/production-host-{roots,state,bootstrap}-runtime-fixes-20261108.json`;
the canonical hashes above authenticate their complete parsed contents. See
[the producer controller](nvme-runtime-fixes-controller.md).
The producer's `records()` wrapper adds addressing keys only to a private policy
copy. The published root producer evidence retains its original shape and hash.
The regression exercises the actual nested producer contract and checks this
private-copy behavior, without inventing addressing fields in the evidence.

All historical authentication predicates, gzip header/CRC/single-member checks,
wire and expanded SHA256/size checks, GPT primary/backup and geometry inspection,
filesystem superblock checks and root/state partition hashes remain unchanged.
The unchanged inspection helper `tests/verify_nvme_image.py` is pinned to
15,781 bytes, SHA256
`d9e9342136af125df2eb9dfe1d832183244f13cd28c7ac3e2a4d7f1ca976b8fb`.
The unchanged SPI command source is pinned to 1,315 bytes, SHA256
`c9f0db913fb49bbc5c54c5287049b8c56aaf17406fcdccf1b9fe0847ccce6c26`.
BOOT/GUESTS namespace readback, boot-file hashes and SPI wrapper inspection
remain producer-attributed through the authenticated receipt; this reader does
not independently traverse those complete filesystems or extract the wrapper.

Only bounded, single-link regular metadata and stdin data are read. There are
no subprocesses, mounts, image/partition opens or output/scratch file creation.
Metadata paths reject symlinks, parent traversal and device/pseudo-filesystem
paths; checked inode data is reopened through its held `/proc/self/fd` descriptor.
The template's physical ancestry, inode identity and bytes are held and checked
alongside the historical metadata checks at stream fences (including each
compressed MiB), and before/after verification. A successful report adds the
separate `runtime_fixes_stream_reader` provenance key.

For an actual completed construction, obtain the successful producer receipt
SHA256 through a trusted channel. No receipt SHA256 is registered in advance. Run the reader
from its checkout, keeping the pinned template and helper at their sibling paths:

```sh
python3 -I -B build/verify-nvme-runtime-fixes-stream.py \
  --receipt /trusted/receipt.json \
  --receipt-sha256 "$trusted_receipt_sha256" \
  --manifest /trusted/manifest.json < /trusted/nvme.img.gz
```

These are placeholders for the eventual authenticated artifact paths. A caller
pin alone cannot authorize an arbitrary receipt: source, phase, output, original
canonical producer records, geometry, UUID, manifest and all retained historical
predicates must also match. Errors produce no success report.

The focused synthetic suite is
`python3 -I -B -m unittest discover -s tests -p test_verify_nvme_runtime_fixes_stream.py`.
It uses no subprocesses, namespaces or mapped UIDs. Raw local results are stored
under ignored `tmp/nvme-runtime-fixes-stream-unit.log`. Fixtures exercise real
small gzip streams and metadata races; they establish no production stream pass.
Hardware KVM networking/restart, physical boot, SD-write validation and SPI
programming/recovery gates are unchanged.
