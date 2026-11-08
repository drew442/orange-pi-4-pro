# Corrected full NVMe construction

The [completed producer receipt](evidence/production-nvme-runtime-fixes-20261108.json)
combines the runtime-corrected [roots](production-host-roots-runtime-fixes.md),
[state](production-host-state-runtime-fixes.md) and matching
[bootstrap](production-host-bootstrap-runtime-fixes.md). Construction, producer
readback, final environment checks, independent complete stream verification
and independent artifact review pass. The latter checks 778 metadata/provenance
assertions and finds no issues.
This is an unbooted file artifact, with no physical installation acceptance.

The producer is the unchanged committed source
`6c34629c424a1bb414780b26461e06ccd2854ae9`. Its completed single-use invocation was:

```sh
sudo python3 -I -B \
  /var/lib/a733-nvme-runtime-fixes-project-20261108-01/project/build/launch-nvme-host-runtime-fixes.py \
  --project /var/lib/a733-nvme-runtime-fixes-project-20261108-01/project \
  --tag timesync-runtime-fixes-01
```

The [registered controller](nvme-runtime-fixes-controller.md) authenticates the
accepted component evidence and all 144 input files. Only generated regular
files were written in the frozen Ubuntu builder. The original producer receipt
remains unchanged, including its pending-independent-expansion field. A later
successful independent report supplies that separate evidence.

Producer inspection checks the complete logical image, GPT/geometry,
root/state partition hashes, BOOT/GUESTS filesystem namespaces, five boot files
and SPI boot-script wrapper. Final receipt flags show host inventory unchanged,
no mapped processes and no phase mounts. The earlier builder-postchecks record
is preconstruction; it does not establish these final conditions.

The image is **256,060,514,304 bytes**, SHA-256
`0eda2415406e370ca1870f22ce7b2fd1e59620b52cf6c59894b5a2fbce005754`.
Its deterministic gzip is **545,632,565 bytes**, SHA-256
`7c44516aefb25b36db6342dc7cd9666e73b370e59b30fb3dc698bec4d93413c8`.
The producer receipt SHA-256 is
`372cf8da55d6347421260924025a26debe35761f3afa94a0fae8d86359698ba3`.
The original 9,393-byte manifest SHA-256 is
`8d40099bb8a670d67bc62a9c6623edb1201903aef16e8c17c0d424233c4ce8a8`.

The [subsequent capacity retention](coherent-build-capacity-20261108.md) now
retires only the generated raw NVMe image; both gzip copies remain. The original
producer/evidence retention fields describe construction time.

Builder retained artifacts remain under
`/var/lib/a733-clean-replay-20261106/nvme-timesync-runtime-fixes-01/work/project/out/nvme-image-timesync-runtime-fixes-01`.
The retained codex gzip is
`out/nvme-runtime-fixes-20261108/payload-compressed/nvme.img.gz`;
small receipts/readback metadata are in its sibling `generated/` directory.
The derived 10,477-byte delivery manifest SHA-256 is
`308d8a15cd749fbd5019e6527d8a6b18d846d40fdb9e53edb078ea97b0839a13`.
It preserves every original manifest field and adds only gzip/distribution
provenance. The original receipt/manifest remain unchanged. No delivery service
was changed and no expanded native image was created.

The [independent reader](nvme-runtime-fixes-stream-reader.md) checks compressed
wire/CRC/single-member integrity, the complete expanded hash, GPT geometry,
filesystem superblocks and every root/state payload byte. Its 5,343-byte report
SHA-256 is `4652e997b3a4960fbd3930c4d58a472b6a176a84a3aba7ae459adbc58c792efd`.
BOOT/GUESTS namespace and SPI-wrapper inspection remain producer-attributed.
Both retained gzip copies match the producer pin; no recompression occurred.
 Builder minimum observed free space was
**20,742,758,400 bytes**, minimum available memory **7,509,852,160 bytes**;
final builder free was **22,278,135,808 bytes**. The unchanged 3,000,000,000-byte
disk and 1,000,000,000-byte available-memory runtime floors held. Codex retained
its separate 268,435,456-byte free-space floor. Remeasure before further work.

The new state UUID is `360dfb39-f49f-487e-b307-0bb887c854c5`. Keep the existing
repaired `00fd…` installation and its operator images. This candidate retains
the original DTB; the separate VGIC change and matched NVMe-first transport
still need integration and physical validation. Do not flash its bootstrap alone
against the existing installation. Installed automatic time, identity behavior,
guest networking/restart, SPI recovery and the full hardware release gates remain
open. This evidence does not authorize a storage or SPI write.
