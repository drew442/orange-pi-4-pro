# Production NVMe composition

The [completed construction and independent checks](evidence/production-nvme-20261107.json)
establish one full NVMe candidate from the production time-enabled roots, state
and matching immutable SD bootstrap. The image uses the actual
**256,060,514,304-byte, 512/512-sector** drive geometry. Software checks pass;
physical boot, real KVM guest execution and a reproduced deployment remain open.
Existing published installation artifacts are unchanged.

**Keep the current installation.** Its next step remains the
[one-file machine-id repair](installed-host-repair-20261107.md), using the existing
installer RAM shell, followed by booting the same deployed host SD. This new
candidate's state UUID is `fc8bbef6-9385-4ddb-90ba-e41eaccc035b`; the installed
state is `00fd936c-49fb-4fea-87c8-928b9c1599e0`. Do not flash the new bootstrap
alone or reinstall to repair DHCP. No new physical flashing procedure is released here.

## Construction and provenance

The successful frozen-builder command, from immutable producer revision
`6f46e080b9557dd6d720bb0b07d17b19044a7946`, was:

```sh
sudo python3 -I -B \
  /var/lib/a733-nvme-host-project-20261107-03/project/build/launch-nvme-host.py \
  --project /var/lib/a733-nvme-host-project-20261107-03/project \
  --tag timesync-03
```

The command with `--plan` passed before execution. Tags are single-use; this
completed tag must not be reused. The launcher authenticates **594 source
records**, a Git bundle/checkout, the original production component manifests,
receipts and full-readback evidence, and every consumed leaf. Inputs are held
open with identity fences and mounted read-only into the namespace; no extra
16 GiB root/state copy is staged. The private frozen Ubuntu 22.04 builder has
isolated UID/network, capability drops, masked firmware access and a read-only
root. Only generated regular files are written; no physical disk is selected.
All 842 host packages remain unchanged, and final process/mount/device checks
are clean.

The [production roots](production-host-roots.md) supply 4 GiB slots A/B, 221
packages, compatible modules and maintenance tools. The
[8 GiB personalized state](production-host-state.md) supplies both administrator
keys, DHCP configuration, machine-id mode `0644`, private SSH state, clock
ownership `996:996` and selected root A. The
[256 MiB bootstrap](production-host-bootstrap.md) supplies the matching initrd
and original compatible kernel/DTB/EL2 stack. It fits the actual 2 GB SD card.

The core NVMe recipe's default SD boot profile remains unchanged. This candidate
explicitly selects `spi-nvme-v1` for **NVMe BOOT only**, using
[`spi-nvme-host-boot.cmd`](../configs/spi-nvme-host-boot.cmd). Its payload loads
from `nvme 0:1` under `/boot/`; the separate matching SD bootstrap remains the
original `experimental-el2` variant. The
[NVMe-first U-Boot candidate](uboot-nvme-candidate.md) is compiled and separately
replayed, but has not been programmed or booted. SPI-ready filesystem contents
do not prove the firmware handoff.

The [source and linked-environment inspection](evidence/nvme-firmware-search-policy-20261107.json)
finds EFI disabled in this candidate. Its broader distro scan still searches
extlinux and script paths. All five GPT entries have Linux filesystem GUIDs and
zero attributes; the pinned empty bootable-list logic falls back to partition 1.
The complete BOOT namespace is therefore checked separately. Do not describe the
whole firmware search policy as restricted to the script's one payload path.

## Layout and artifacts

| Partition | Start byte | Size bytes | Purpose |
| --- | ---: | ---: | --- |
| 1 | 1,048,576 | 536,870,912 | Immutable compatible boot files |
| 2 | 537,919,488 | 4,294,967,296 | Read-only root A |
| 3 | 4,832,886,784 | 4,294,967,296 | Read-only root B |
| 4 | 9,127,854,080 | 8,589,934,592 | Writable identity, configuration and host state |
| 5 | 17,717,788,672 | 238,342,373,376 | Guest storage |

Builder directory:
`/var/lib/a733-clean-replay-20261106/nvme-timesync-03/work/project/out/nvme-image-timesync-03`.

| File | Logical bytes | SHA-256 |
| --- | ---: | --- |
| `nvme.img` | 256,060,514,304 | `2c7e69cf5f0370b818b47c4d36a6c1eec7cd7258773794ad06de7cd5f5ab499f` |
| `nvme.img.gz` | 545,633,126 | `9e8be89dbcabe30799744201ea94de93ce343134d0a8e034fa44281c8dd13955` |
| `manifest.json` | 9,393 | `fa01243f5884bfaf3dae92f5974a2899dd93836b7c6c23d3d0a509d7c572e90c` |

The raw sparse image allocates 975,032,320 bytes. Raw and gzip remain on the
builder. Native `out/nvme-timesync-03-20261107` retains small receipts, expected
trees, complete filesystem inspection transcripts, stream proof and independent
review; images and downloaded sources remain outside Git.

## Validation and limits

The producer checks primary/backup GPT, all five filesystem superblocks and
geometry, complete root/state partition hashes, selected-root metadata and all
BOOT file hashes. A separate generic filesystem reader checks the whole newly
created BOOT/GUESTS namespace: **9 paths/5 files** and **1 path/0 files**,
respectively, with only empty `lost+found` allowed in addition. Ownership, modes,
links and raw attributes are inspected. Child mtimes preserve whole seconds;
root mtime uses actual filesystem creation time. Nanosecond preservation and
atime/ctime/crtime are outside that policy. The legacy boot script header, CRC,
size table and exact source body also pass.

A separately implemented [stream verifier](../build/verify-nvme-stream.py)
then reads the actual gzip over SSH, verifies all 545,633,126 compressed bytes
and all 256,060,514,304 expanded bytes in bounded memory, and independently
checks GPT, superblocks and every root/state partition byte. It stages no image
or decoded partition. Filesystem namespace inspection remains attributed to the
reviewed producer; the stream reader does not claim to repeat it. The producer's
immutable compression record says independent expansion was pending at its
completion; the later successful stream report supplies that evidence without
editing the original record. Independent Sol/high artifact review finds no issues.

The refreshed installer's unchanged source also accepts this manifest; this is
source compatibility evidence, not a physical installation test. Privileged
Ubuntu discovery passes **1,585 tests without skips, errors or failures**. Five
shell suites, four diagnostic assembly cases, Bash syntax and TOML parsing pass.
Expensive hashes here validate builder artifacts. Operator installation retains
one expanded write with progress and **optional** readback.

Two earlier attempts are recorded rather than treated as successes. The first
exhausted the process's 1,024-descriptor soft limit before creating a phase. The
launcher now admits 4,096 descriptors without changing the hard limit. The second
could not read diagnostic inputs through the mapped namespace and produced no
NVMe image. Preflight now checks effective search/read permissions. Seven
hash-verified public boot inputs were made read-only/readable (`0444`, directory
`0555`), preserving their bytes and owners; private operator/state files were
unchanged. An actual namespace probe passes.

Admission required 23,749,797,311 free bytes, including the unchanged **22 GiB**
recipe guard plus metadata/Git staging. Runtime minima were 20,710,158,336 free
bytes and 7,413,485,568 available RAM, above their separate 3 GB/1 GB guards.
The completed run leaves **22,245,568,512 bytes**, below the next initial 22 GiB
guard by 1,376,751,616 bytes before staging overhead. Further construction needs
verified retention, not guard reduction or additional LV growth.

Routine root updates and rollback affect inactive ROOT and STATE selection;
they do not overwrite NVMe BOOT or SPI. Root rollback therefore does not establish
bootloader/BOOT rollback. Matched boot-stack backup, maintenance and recovery
still need their own procedure and physical evidence. Before SPI programming,
retain and verify the actual original-flash backup off-device, prepare recovery,
identify the exact target/write plan, and request the agreed yes/no confirmation.

One construction does not establish clean reproduction or deployment acceptance.
Actual SSH with both keys, automatic time, KVM guest CPU/disk/network and restart
persistence, cold/warm cycles, endurance, zero routine SD writes, interrupted
update, rollback and off-device recovery remain required by
[hardware validation](hardware-validation.md).
