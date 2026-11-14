> **Historical record — not active instructions or release gates.**
> Superseded by the [manual beta scope](../../beta-scope.md) and
> [current status](../../implementation-status.md). Original observations retain
> their recorded configuration and limitations.

# Matching production-state EL2 bootstrap

The [build and inspection evidence](../../evidence/production-host-bootstrap-20261107.json)
records a **256 MiB** immutable SD bootstrap bound to the new
[production state component](../../production-host-state.md). It fits the actual
1,977,614,336-byte SD card. Construction and software boot-file inspection pass;
this image has not booted on the board. Published deployment images remain
unchanged. The matching [full NVMe composition](../../production-nvme-composition.md)
now passes software inspection; clean reconstruction repeats remain required.
The [second matching bootstrap](../../production-host-bootstrap-repeat.md) now passes
frozen construction and independent artifact review for the second state UUID
`feaa…`; its [full NVMe repeat](../../production-nvme-repeat.md) also passes.

**Do not flash this component over the current host SD.** Its state UUID is
`fc8bbef6-9385-4ddb-90ba-e41eaccc035b`; the installed NVMe state UUID is
`00fd936c-49fb-4fea-87c8-928b9c1599e0`. These do not pair. The existing-install repair now passes. The next operator
action is the [VGIC diagnostic SD trial](../../next-host-boot-20261108.md), matched to
the installed state; retain the existing NVMe installation.

## Construction

Source revision `885b2441caf35d6c7f96058468f4db85f7d10405` authenticates all
**587** tracked source records, a fresh bundle/checkout, original input leaves,
read-only capsule and the production state's successful receipt/manifest/full
readback proof. It derives the UUID from that authenticated lineage; no root or
state image is read to build this bootstrap.

The existing `host-initramfs.py` and `assemble-host.py` recipes run unchanged in
the frozen Ubuntu 22.04 namespace. The original baseline archive intentionally
supplies only the locked storage-tool binaries; production roots/state retain
their separately pinned time-enabled archive. Kernel, DTB and module carrier
remain the original compatible diagnostic inputs. The boot package is the
locked `experimental-el2` package, using service `0x8000ff40` and SPSR `0x3c9`.
No NVMe-first U-Boot or SPI candidate is substituted into this SD component.

The executed builder command was:

```sh
sudo python3 -I -B \
  /var/lib/a733-host-bootstrap-project-20261107-02/project/build/launch-host-bootstrap.py \
  --project /var/lib/a733-host-bootstrap-project-20261107-02/project \
  --tag timesync-01 \
  --userspace /var/lib/a733-sd-replay-inputs-20261106/rootfs.tar.gz \
  --diagnostic /var/lib/a733-bootstrap-inputs-20261107 \
  --busybox /var/lib/a733-sd-replay-inputs-20261106/busybox \
  --state-receipt /var/lib/a733-clean-replay-20261106/host-state-timesync-02/receipt.json \
  --state-receipt-sha256 3abeb7ba014eae05acebfd7af3b75dab7c4c58999b96d8c752163bb96a2ccd33
```

The same command with `--plan` passed first. Plans read committed source/policy
without runtime builder or raw-input inspection. A used tag refuses reuse;
start a repeat with a fresh tag and committed inputs. Failed attempts remain
for diagnosis. The launcher preserves private UID/network, read-only builder,
capability drops, firmware mask, probe and interrupt-safe cleanup. A private
disk scratch directory is bound onto `/var/tmp`; no frozen root is modified.
All 842 host packages remain unchanged, no mapped process or phase mount remains,
and retained work contains no real device node. No physical storage is selected.

## Outputs and checks

Builder phase:
`/var/lib/a733-clean-replay-20261106/host-bootstrap-timesync-01`.
Outputs under `work/project/out/` are `host-initramfs-timesync-01` and
`host-sd-timesync-01`. The raw SD image is 268,435,456 bytes, SHA-256
`beb4ae47ea536e2b7601f29f3ddc89e14d8bb15670b3e52d56271a76f50136e7`.
Its gzip is 17,271,918 bytes; exact compressed and manifest pins are in the
evidence. These remain builder components, with no new installation instructions.

The producer authenticates five artifact files and both complete recipe sets.
The separately implemented `tests/verify_host_sd.py` also runs inside the frozen
namespace: MBR/raw firmware placement, legacy ext4 features, kernel/DTB bytes,
boot script CRC/content, initrd wrapper/payload, **424** newc entries, all 345 regular entry hashes/modes against their manifest,
and complete gzip expansion pass. Root choice remains on NVMe state; the SD
contains immutable boot files. Real zero-write behavior still requires physical
counters, full-card hashes and firmware/cycle/workload observations.

Initial admission requires 4,652,936,823 bytes including capsule/Git staging and
3 GiB available RAM. Actual runtime minimums are 23.25 GB disk and 7.41 GB
available RAM. The completed raw image initially allocated its full 256 MiB.
After independent review, a fixed-file held-FD conversion removes only allocated
zero extents, reducing allocation to **32,591,872 bytes**. Before/after full-image
hashes match, and inode, ownership, mode, size and mtime are preserved. This
changes builder allocation only. The reviewed code, readiness, intent and result
are retained in `out/bootstrap-sparse-20261107`; five real fallocate fixtures pass.

After conversion, builder free is **23,830,810,624 bytes**, only **208,490,496 bytes**
above the unchanged 22 GiB composition guard. Future admission must account for
all additional staging. No guard reduction or further LV growth is authorized.
See the [workspace retention map](../../composition-retention.md) for earlier archives.

Native records, compressed image, initramfs and inspection proof are retained in
`out/host-bootstrap-timesync-01-20261107`. The raw image remains on the builder.
A transfer exclusion typo briefly created a dense native duplicate and crossed
the native 256 MiB reserve; the matching duplicate was verified and removed,
restoring the reserve. The builder original and gzip were unaffected. The
correction is recorded in the evidence; no continuous native-reserve claim is made.

Ubuntu discovery passes **1,530 tests, no skips or failures**. An earlier whole-suite
`-I` invocation incorrectly removed local package imports; the standard test
invocation fixed that. A same-length marker-mutation fixture now forces an
observable mtime change; runtime guards are unchanged. Independent source and
artifact reviews pass. The artifact reviewer streams the complete gzip and
checks raw headers/firmware regions, wrappers and all newc entries in memory.
Complete SD filesystem inspection is attributed to the reviewed producer run;
it was not repeated with a dense native partition copy.

The matching [full NVMe candidate](../../production-nvme-composition.md) now passes
construction and independent wire verification. Next repeat from clean inputs
and validate physical deployment under the unchanged capacity guards. Actual SSH,
NTP, KVM guest execution, endurance, SD writes, recovery/rollback and reproduced
deployment remain physical release gates.
