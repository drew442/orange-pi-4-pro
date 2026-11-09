# SPI-to-NVMe boot investigation

SPI-to-NVMe with the SD removed remains the preferred release mode. The first
physical diagnostic log identifies SPI as **XM25QU128C, 16 MiB**. The
[original backup and backup-derived candidate](spi-original-backup-20261108.md)
are retained and reviewed. Programming and SD-removed boot remain unperformed.
The [known SPI target is authorized](evidence/storage-write-authorization-20261108.json);
independent recovery and programmer review remain prerequisites. Leave attached
heatsinks in place; software identity is sufficient.

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
SPI boot remains untested; [actual KVM guest execution](kvm-vgic-results-20261108.md)
now passes on the SD-booted host.

## Resolved source policy and remaining candidate work

The pinned [sunxi target definition](https://github.com/orangepi-xunlong/u-boot-orangepi/blob/b791be842935b27268ae3d00e943a9075495f30a/include/configs/sunxi-common.h#L357)
registers **`nvme`**, not `nvme0`. Its
[distro command](https://github.com/orangepi-xunlong/u-boot-orangepi/blob/b791be842935b27268ae3d00e943a9075495f30a/include/config_distro_bootcmd.h#L67)
runs PCI enumeration, NVMe scan and device 0 selection, then searches bootable
partitions (default partition 1) and `/` then `/boot/`. SPI itself is not a distro
boot target: it supplies firmware/U-Boot, which then reads Linux files from NVMe.

The NVMe image recipe already creates legacy-compatible `A733BOOT` partition 1
with `/boot/boot.scr`, `uImage`, `uInitrd` and the matching DTB. This is source
compatibility evidence, not an observed U-Boot NVMe file read. A separate candidate
should retain `ENV_IS_NOWHERE=y`, disabled `saveenv`, misc/system switching,
bootcount/OTA writes, both reviewed patches and EL2 service `0x8000ff40`.
Coordinator policy for that candidate is:

```text
setenv boot_targets nvme; run distro_bootcmd
```

Manual SD selection remains `run bootcmd_mmc0` at the interrupted U-Boot prompt.
No distro-macro source patch is needed. Do not alter the registered diagnostic
replay target: its `.config` and binary hashes prove a different, SD-only build.
The separate candidate needs its own configuration provenance, build and review.
The current host script resets `boot_targets` to `mmc0`, uses implicit partition 1
and loops on load failure; its candidate revision must carry the discovered
partition explicitly and document manual recovery. The [separate component recipe](uboot-nvme-candidate.md) now implements this
policy and a guarded NVMe host script without changing existing diagnostic
producers. Independent source review and focused fixtures pass. [Actual compilation and independent artifact inspection](evidence/uboot-nvme-candidate-20261107.json)
now pass, including the linked ARM EL2 call and complete packed payload bindings.
The [SD firmware transport image](nvme-firmware-test-sd.md) now also passes
frozen construction and software boot-file inspection; it remains unbooted.
Its state UUID differs from the installed NVMe, so it is not the next repair image.
No firmware has been programmed. Its full budget
preserves the final >4 GB guard, with unchanged runtime floors.

The [file-only SPI assembler](spi-file-candidate.md) now combines the pinned
components with an authenticated backup while preserving all other bytes,
including gaps and partial erase units. A [synthetic-baseline fixture](evidence/spi-file-assembly-20261107.json)
produces identical files/manifests on the Jammy builder and codex. The
[physical backup-derived candidate](spi-original-backup-20261108.md) now
passes independent file review. An actual programmer remains unprepared; never
flash synthetic fixtures.

After installed management works, a serial-only **read test** can establish the
NVMe file path without programming SPI. Interrupt U-Boot autoboot and capture:

```text
printenv boot_targets scriptaddr
pci enum
nvme scan
nvme dev 0
ls nvme 0:1 /boot/
```

The recorded SD boot loads scripts at `0x43100000`. If `scriptaddr` reports that
same value and the listing contains `/boot/boot.scr`, also capture:

```text
load nvme 0:1 ${scriptaddr} /boot/boot.scr
```

This loads the small script into RAM without executing it. Do not run `source`,
`saveenv`, `sf`, erase or write commands. Return the complete serial transcript;
this is a file-read test, not SPI boot acceptance.

## Backup and recovery gate

The [read-only backup tool and procedure](spi-backup.md) now provide exact-device
inspection and a two-read, verified 16 MiB RAM backup with progress. Only private
tmpfs output is accepted, and active swap is refused. Transfer and verify the
backup off-device before poweroff. The [physical backup and off-device retention](spi-original-backup-20261108.md)
now pass; source fixtures alone would not establish that result.

The [ROM recovery investigation](rom-recovery-investigation.md) now provides a
pinned, version-only host FEL probe with source/ELF inspection and no memory or
flash command path. It has not contacted the board. The manual lists a BOOT button
and ADB instructions but no independent ROM-entry procedure. Neither button
presence, working SD boot nor a later version response proves corrupted-SPI
recovery. That hardware demonstration remains required before programming.

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
5. Present and revalidate the exact physical target and proposed write. The
   [known SPI authorization](evidence/storage-write-authorization-20261108.json)
   persists; request yes/no only for unapproved targets or scope. No concrete SPI
   write has occurred.

After authorized programming, physically prove SD-removed cold boot, actual KVM
guest execution, rollback/recovery and the full release cycle/workload tests.
Until then, the immutable SD-to-NVMe host path is a fallback under development,
not a reason to declare SPI unsupported or reduce release acceptance.
