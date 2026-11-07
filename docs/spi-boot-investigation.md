# SPI-to-NVMe boot investigation

SPI-to-NVMe with the SD removed remains the preferred release mode. The first
physical diagnostic log identifies SPI as **XM25QU128C, 16 MiB**. No SPI backup,
programming or SD-removed NVMe boot has been performed. The user authorizes SPI
work with exact-target/proposed-write details and yes/no confirmation before
programming. No concrete SPI write has been confirmed. Leave attached heatsinks
in place; software identity is sufficient.

## Pinned source findings

The vendor build commit is `a95129bf30ea11886cc35f08ebb3e69695a26656`; U-Boot is
`b791be842935b27268ae3d00e943a9075495f30a`. The board uses the vendor kernel's
`CONFIG_AW_MTD_SPI_NOR_6_1` driver. Its
[XMC table](https://github.com/orangepi-xunlong/linux-orangepi/blob/2ac08e8c7cdc28abbdc5c9a9dd812f887ae9c79f/bsp/drivers/mtd/spi-nor-6.1/xmc.c#L19)
associates this name with JEDEC ID `0x204118`. The [second physical capture](evidence/board-el2-20261104.json) directly confirms
JEDEC `204118`, 16,777,216 bytes, erase size 65,536 bytes and write size 1 byte. No physical marking is required.

The vendor
[family SPI writer](https://github.com/orangepi-xunlong/orangepi-build/blob/a95129bf30ea11886cc35f08ebb3e69695a26656/external/config/sources/families/sun60iw2.conf#L230)
uses this layout:

| Component | Start byte | Pinned/candidate length | End byte, exclusive |
| --- | ---: | ---: | ---: |
| `boot0_spinor_a733.fex` | 0 | 237,568 | 237,568 |
| `boot_package.fex` | 262,144 | 1,392,640 | 1,654,784 |

The SPI boot0 SHA-256 is
`7f0babc242c1e4614c7243bd2c45a980e4385727ffbb544a7c8f2d324cf8dc07`, already
covered by the [26-input lock](../configs/vendor-boot-inputs.sha256). Boot0 for SD
is a different binary. The SD image puts its package at byte **16,793,600**,
beyond the entire 16,777,216-byte SPI capacity. **Never write a diagnostic or
host SD image onto SPI.** This table is source evidence, not a programming plan.

The vendor function erases **50 MTD erase blocks**, then writes these two files.
With the observed 65,536-byte erase size this spans 3,276,800 bytes and covers
both components. This resolves the earlier unknown erase geometry; it does not
establish that the entire erased range is disposable. A future reviewed
programmer must preserve all bytes outside explicitly authorized ranges.

The vendor
[`nand-sata-install`](https://github.com/orangepi-xunlong/orangepi-build/blob/a95129bf30ea11886cc35f08ebb3e69695a26656/external/packages/bsp/common/usr/sbin/nand-sata-install#L743)
menu combines disk formatting/system installation with a later SPI-write offer.
Do not use this stock workflow for our A/B layout. The manual's section 3.36
supports investigating SPI plus NVMe but does not prove the user's SPCC SSD works.

## U-Boot and remaining preparation

The pinned
[A733 U-Boot configuration](https://github.com/orangepi-xunlong/u-boot-orangepi/blob/b791be842935b27268ae3d00e943a9075495f30a/configs/sun60iw2p1_t736_defconfig#L127)
enables `CMD_NVME`, `NVME`, PCI and the Allwinner PCIe root complex. The [preparation boot](evidence/installer-prep-physical-20261106.json) now shows
U-Boot enumerating the exact SPCC serial/capacity at Gen3. It still loads boot
files from SD; NVMe file reads and SPI-to-NVMe boot remain unproven.

The reviewed diagnostic U-Boot explicitly restricts `boot_targets` to `mmc0`.
Copying its boot package into SPI would not by itself implement NVMe-first boot.
A separately reviewed SPI/NVMe boot policy must preserve the compatible firmware,
read-only environment and EL2 handoff while selecting NVMe boot files. The [EL2 diagnostic](el2-diagnostic.md) has physically initialized KVM, but
SPI boot and guest execution remain untested.

Before proposing any SPI write:

1. Revalidate the recorded software identity and geometry immediately before
   any programming plan; capture the complete MTD partition map.
2. Read the entire exact SPI into RAM, transfer it off-device after management
   networking works, repeat the read and verify byte count/hash and retained
   backup. Do not store a backup on SD or the NVMe that will be reprovisioned.
3. Establish and reproduce an actual recovery route with the original flash
   retained. A working SD diagnostic alone does not prove recovery after a
   malformed SPI bootloader changes ROM boot selection.
4. Demonstrate U-Boot NVMe scan/read and a compatible boot-file layout. Prepare
   and independently inspect a new regular-file SPI candidate and its exact
   byte/erase-unit plan; keep it distinct from SD images.
5. Present the exact physical target and proposed write, then request yes/no
   confirmation before programming. The user authorized preparing SPI deployment
   using this confirmation style; no concrete SPI write has occurred.
   NVMe erase permission does not authorize this operation.

After authorized programming, physically prove SD-removed cold boot, actual KVM
guest execution, rollback/recovery and the full release cycle/workload tests.
Until then, the immutable SD-to-NVMe host path is a fallback under development,
not a reason to declare SPI unsupported or reduce release acceptance.
