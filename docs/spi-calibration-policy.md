# SPI calibration and restoration policy

Independent source/live-plan review and the full Ubuntu software suite pass.
[Validation evidence](evidence/spi-metadata-validation-20261112.json). This does not
authorize running an old plan or establish physical restore/reinstall acceptance.

The registered A733 firmware is unchanged. Its vendor parameter area changes
at boot, so full-flash hashes identify snapshots rather than firmware releases.
Boot0 and package hashes identify firmware; the entire current snapshot remains
an exact precondition for each programming transaction.

## Fixed layout and supported parameters

| Region | Byte interval | Policy |
| --- | --- | --- |
| Boot0 | `[0,237568)` | Registered firmware component |
| Gap | `[237568,260096)` | Preserve exact original bytes |
| Parameters | `[260096,262144)` | Validate the fixed supported format |
| Boot package | `[262144,1654784)` | Registered firmware component |
| Remaining flash | `[1654784,16777216)` | Preserve exact original bytes |

The source is U-Boot revision
`b791be842935b27268ae3d00e943a9075495f30a`, retained in the pinned component
source archive. Relevant definitions are `include/boot_param.h:21`,
`drivers/mtd/spi/sf_internal.h:111`,
`drivers/sunxi_flash/spinor/spinor.c:470`, and
`board/sunxi/board_common.c:846`.
[Component registry](../configs/uboot-nvme-candidate-targets.lock.json).
[Measured boot behavior](spi-sd-boot-20261111.md#first-sd-free-boot-and-spi-calibration).

Accepted parameter states are all-FF erased bytes or `vendor-normal-a733-v1`.
The latter requires `bootpara` and `NORPARAM` magic, the registered NOR fields,
zero unused/header/reserved fields and the vendor checksum. For 512 little-endian
32-bit words the checksum is
`(sum(words) - words[3] + 0x5f0a6c39) & 0xffffffff`, equal to `words[3]`.
The 512 DDR bytes are opaque same-board data. The observed header's version,
length, boot0 checksum and transfer flag are zero. The vendor zero-initializes
the structure but also assigns `header.boot0_checksum` from the selected boot0
checksum at `spinor.c:490`; the zero-only check registers the observed profile,
not every state the source can produce. The checksum detects corruption; authenticated
snapshot hashes and exact board identity establish provenance.

Unknown parameter formats, new timing values and differences outside the fixed
boundaries require source review and a new registration. A difference observed
on hardware never expands the allowed region automatically.

## Installation and exact restoration

Installation uses the exact registered factory candidate and copies only the
validated live parameter bytes into the intended image. It preserves same-board
calibration. Exact restoration instead reproduces the selected registered full
backup, including its parameters. Registered sources are the original all-FF
backup and the retained November 11 post-cold and post-warm calibrated backups.
The transaction engine pins their full hashes; a CLI hash cannot register another
restore source.

Linux exposes 64 KiB erase units. Every changed unit is reconstructed completely,
including neighboring code and preserved bytes. Bootable installation and
calibrated restoration write units 4–25, then 1, 2, 3 and finally 0, skipping
unchanged units. Restoring the original blank image invalidates boot0 first and
requires the known SD recovery environment. Both operations retain complete
readback, final descriptor-close checks and acknowledged off-device receipts.

The vendor itself can erase a smaller 4 KiB region `[258048,262144)` and preserve
its lower 2 KiB. That does not broaden the parameter boundary or require the
Linux programmer to use a smaller erase operation.

## Failure and wear limits

Every plan binds the fresh full preimage, selected restore source, metadata policy,
component hashes and exact erase-unit reconstruction. The historical
`plan-spi-nvme.py` is pinned to its original schema-1 candidate manifest; use the
reviewed `program-known-spi.py plan` path for metadata-aware operations. Old
plans and candidate manifests do not become current by changing a hash.
Execution rebuilds the
whole plan before mutation. An uncertain write or acknowledgement stops work;
there is no automatic mutation retry.

Normal admission refuses malformed parameters, including an interrupted metadata
write. Such a failure requires a separate independently reviewed recovery-preimage
registration tied to fresh bytes and the prior durable receipt. That recovery
interface is not implemented by this change. Preserve receipts and backups and
stop for inspection; do not weaken the decoder or reuse a stale plan.

Vendor source paths `drivers/mtd/spi/spi-nor-core.c:4278` and
`drivers/spi/sunxi_spi.c:1581` lead to updates when magic is absent or timing
remains at the default. `spinor.c:542` selects the smaller erase operation.
There is no equality-before-write check in that path. Retained cold/warm snapshots
show unchanged NOR parameters and changed DDR/checksum bytes. Command counts,
wear lifetime, opaque boot0 validation and the reason for default timing remain
unmeasured. Preserve compatible firmware; no speculative wear patch or hardware
benchmark is part of this qualification.
