# Corrected NVMe BOOT DTB derivation

`build/vgic-nvme-image.py` derives the accepted corrected full NVMe image for
state `360dfb39-f49f-487e-b307-0bb887c854c5`. It uses the independently inspected
matched VGIC bootstrap as its DTB source. The new runtime profile is explicitly
`experimental-el2-vgic-v1`. This is a regular-file preparation recipe;
**the derived image remains UNBOOTED, with hardware acceptance false**.

The [original accepted NVMe artifact](production-nvme-runtime-fixes.md) is
expanded from its pinned 545,632,565-byte gzip to a new sparse regular file.
Compressed size/hash, single-member gzip CRC/EOF and complete 256,060,514,304-byte
raw size/hash must pass before any patch. The original 9,393-byte manifest is
authenticated separately. Historical receipts remain original producer evidence;
this recipe does not claim to reread or validate those receipt files.

The only filesystem payload change is the DTB at
`boot/dtb/allwinner/sun60i-a733-orangepi-4-pro.dtb` in BOOT (offset 1,048,576,
length 536,870,912 bytes). The binary semantic check permits only
`interrupts = <1 9 4>` at `/interrupt-controller@3400000`. The four other BOOT
files, including the **NVMe SPI boot command**, retain their exact original
pins. The SD boot command is never copied. Complete BOOT namespace, types,
content hashes, ownership, modes, link counts and all four inode timestamps
are checked before and after. The empty ext4 `lost+found` directory is
preserved, including its metadata; any child in it is refused. Unexpected
entries, hardlinks and xattrs fail.
Only the private BOOT scratch filesystem receives `debugfs -w`; `e2fsck -nf`
performs read-only checking. No mounts, repair, downloads or physical writes occur.

A streaming SHA-256 covers every byte outside BOOT during original expansion
and new-image compression. Equality proves that GPT, both roots, state, GUESTS
and all gaps remain byte identical to the authenticated base. Both GPT tables
are also parsed before and after. BOOT writeback first punches the entire
bounded region in the new regular file, then writes sparse nonzero chunks;
this avoids retaining stale original bytes beneath skipped zeros. Readback
compares the complete written BOOT partition. The output gzip uses level 9,
mtime zero and an empty filename; its full expansion is verified again.

The final manifest describes the new operation, recipes, selected Jammy tools,
actual invoked versions, input paths, progress resource minima, new raw/gzip
pins and readback. All historical producer records remain intact under
`base_nvme.original_manifest`, with byte-exact original and paired SD manifests
retained beside the result. Root embedded source DTB provenance remains the
original `4d7d…` pin; runtime DTB provenance separately records `1ba8…`.
Original root/state partition hashes are retained and supported by the actual
outside-BOOT byte proof. The complete original BOOT filesystem is retained as
`base-boot.ext4.gz` with both compressed and expanded pins for reversal.
The authenticated installation intent, management MAC, subnet policy and
administrator key fingerprints are copied unchanged for the existing installer.

Independent source review and all **10** small-fixture tests pass. [Actual full construction and complete independent artifact review](evidence/vgic-nvme-image-20261108.json)
now pass. Physical corrected-host boot remains pending.

The 256060514304-byte raw image has SHA-256
`835031b81d0320d6f9f35e74b9be67b494006c26a25fa9070f3e0a8f434ebc11`;
its 545632496-byte gzip has SHA-256
`3e560264bb64a9bc23b342febdff2146955ca97cb6e0279e5240c29d4e6d85bc`.
Independent review authenticates both complete gzip streams and directly compares
all **255523643392 bytes outside BOOT**, including GUESTS and gaps. Root/state
payloads, all BOOT metadata, sole DTB semantic change and full reverse capsule pass.

Builder output is
`/var/lib/a733-vgic-nvme-project-20261108-02/project/out/nvme-vgic-corrected-02`.
Codex retains the verified gzip under
`out/vgic-nvme-image-20261108/retained-02/nvme.img.gz`. No native raw expansion
was created. Minimum builder free was 20933390336 bytes and available memory
6928285696 bytes; remeasure before subsequent jobs.

Run only in the prepared Ubuntu 22.04 builder after independent source review,
using fresh measured resources and a trusted quiescent checkout:

```sh
python3 -I -B build/vgic-nvme-image.py --name corrected-01 \
  --gzip /ABSOLUTE/BASE/nvme.img.gz \
  --manifest /ABSOLUTE/BASE/manifest.json \
  --sd-image /ABSOLUTE/PAIRED/a733-host-sd.img \
  --sd-manifest /ABSOLUTE/PAIRED/manifest.json
```

All four inputs are mandatory absolute, nonaliased, single-link regular files
with no symlink ancestors or parent traversal. `/dev`, `/proc` and `/sys`
inputs are refused. Fixed production pins have no CLI override. Descriptors
remain held and input identity/hash are rechecked before manifest publication.
This detects ordinary mutation; it assumes a quiescent trusted checkout.

Admission requires at least **23,760,999,827 free bytes**; runtime floors are
**3,000,000,000 free bytes** and **1,000,000,000 MemAvailable bytes**, with a
10,800-second cooperative runtime limit and periodic byte progress. Version
queries have 30-second deadlines and filesystem tools have 120-second deadlines.
The existing shared package validator has an unbounded `dpkg-query` call; it
assumes a responsive prepared builder. A running command or file I/O can delay
a cooperative check; this is not an external watchdog.
Keep the native checkout's
268,435,456-byte floor; do not run a full build or raw hash there. Outputs are
reserved atomically under fresh `out/nvme-vgic-NAME/` tags. A failed construction
retains its consumed tag/logs without a success manifest. Generated raw/gzip
images and logs stay outside Git. Identical source bytes produce deterministic
gzip; complete filesystem image reproducibility is a separate evidence gate.

Small tests exercise gzip bounds/CRC/trailing streams, complete namespace and
metadata, preservation/rejection of non-DTB changes, semantic excess changes,
input stability and unsafe paths, resource guards, sparse writeback bounds and
zero-block replacement. They provide host evidence only. The Orange Pi 4 Pro
A733, 2 GB SD, nominal 250 GB NVMe, minimal host and zero routine SD writes
remain requirements; final hardware validation requires the corrected host and
an actual KVM guest.
