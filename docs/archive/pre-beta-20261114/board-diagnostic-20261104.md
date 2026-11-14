> **Historical record — not active instructions or release gates.**
> Superseded by the [manual beta scope](../../beta-scope.md) and
> [current status](../../implementation-status.md). Original observations retain
> their recorded configuration and limitations.

# First physical diagnostic boot

The operator's full boot and read-only checks reached the diagnostic RAM shell
on the A733. The major blocker is confirmed: BL31 entered Linux at **EL1**,
Linux reported `kvm: HYP mode not available`, and `/dev/kvm` was absent.
No KVM guest has executed. Installing onto NVMe cannot fix this handoff.

The [evidence record](../../evidence/board-diagnostic-20261104.json) retains raw-log
names, lengths and SHA-256 values. Raw logs remain under ignored `tmp/` and
should also be retained off-device. Filename timestamps are operator labels;
their timezone/date has not been independently established. The operator
reported using the [baseline diagnostic](../../diagnostic-image.md); its offline
flashed-image readback hash is not included in these logs.

## Observed hardware

| Item | Physical observation | Remaining limits |
| --- | --- | --- |
| Memory/CPU | boot0 reports 12,288 MiB LPDDR5; Linux MemTotal 12,153,464 KiB; eight CPUs | No stress/thermal test |
| SD | SU02G; CID `035344535530324780716f216e00a8f5`; 1,977,614,336 bytes | Linux `ro=0`; single write-counter snapshot is zero |
| NVMe namespace | `/dev/nvme0n1`, 256,060,514,304 bytes; 500,118,192 × 512-byte reporting units | SMART and logical/physical block sizes not captured |
| NVMe identity | `SPCC M.2 PCIe SSD`; serial `134807190FC700149277`; firmware `EDFM20.0` | Existing GPT/data are disposable by operator authorization |
| PCIe | `0000:01:00.0`, 8 GT/s ×1; controller live | Invalid/missing SUBNQN warning; stable repeated I/O untested |
| Ethernet | MAC `10:f4:ae:90:5e:57`; 1 Gbps full duplex | DHCP obtained no lease; no IPv4 address/route |
| SPI | Linux reports `XM25QU128C`, 16 MiB `spi0.0` | Direct JEDEC capture/backup/recovery pending; programming unauthorized |
| Root/write policy | RAM root; `/run` and `/tmp` tmpfs; no SD filesystem mount | Firmware writes/full-card offline hashes and cycle tests unproven |

`/dev/nvme0` is the controller character device; `/dev/nvme0n1` is its block
namespace. BusyBox `fdisk`'s 2047G protective-GPT entry is not the drive capacity.
Use the namespace byte count and later real GPT tooling. The nominal 250 GB
project target is satisfied by this actual 256 GB device; installation must use
its actual geometry, not the earlier 250,000,000,000-byte fixture.

The user recalls memory marking `CA12`, but no chip marking is required.
Leave board and SSD heatsinks attached. RAM enumeration, NVMe identify/sysfs,
and subsequent operating tests supply the relevant evidence. Power-supply rating
and sustained cooling performance remain to be recorded without disassembly.

## SPI identity without removing heatsinks

Boot log line 654 reports `spi-nor spi0.0: XM25QU128C (16384 Kbytes)`.
The pinned kernel configuration selects `CONFIG_AW_MTD_SPI_NOR_6_1=y`; the
active vendor driver is under `bsp/drivers/mtd/spi-nor-6.1`, rather than the
upstream-shaped `drivers/mtd/spi-nor` directory. Its
[pinned XMC table](https://github.com/orangepi-xunlong/linux-orangepi/blob/2ac08e8c7cdc28abbdc5c9a9dd812f887ae9c79f/bsp/drivers/mtd/spi-nor-6.1/xmc.c#L19)
contains `XM25QU128C`, JEDEC ID `0x204118`, 256 × 64 KiB. This is the expected
ID from source, not a separately captured raw-ID read. Direct software identity,
backup/readback and recovery remain to be recorded before any programming. No
physical chip marking is required. See the [SPI boot investigation](../../spi-boot-investigation.md)
for the distinct firmware layout and remaining recovery/erase-geometry gates.

## Firmware handoff diagnosis

Full boot log lines 147–148 report entry `0x41000000` and SPSR `0x3c5` (EL1h).
Line 253 reports all CPUs started at EL1; line 379 reports HYP unavailable.
The [pinned U-Boot normal boot path](https://github.com/orangepi-xunlong/u-boot-orangepi/blob/b791be842935b27268ae3d00e943a9075495f30a/arch/arm/lib/bootm.c#L418)
invokes monitor service `0x8000ff04`. Its SUNXI branch bypasses the generic
`armv8_switch_to_el2()` branch. The existing vendor
[hypervisor service](https://github.com/orangepi-xunlong/u-boot-orangepi/blob/b791be842935b27268ae3d00e943a9075495f30a/cmd/sunxi_hyper.c#L167)
uses `0x8000ff40` with the same entry, DTB and AArch64 arguments.

Independent source/binary investigation found both services in the actual
locked A733 `monitor.fex` (SHA-256
`8af16b86ae8e2eff634a3ebca76a8cdf3c10b3540387573d46c3d785545b2632`).
Offsets below are file offsets with disassembly VMA `0x48000000`:

| Offsets | Observed binary behavior |
| --- | --- |
| `0x236c–0x2378`, `0x24a4` | Normal service selects internal flag 0 |
| `0x2394–0x23c4` | Hypervisor service selects flag 1 |
| `0x24a8–0x24ac` | Both call common entry routine `0x89c4` |
| `0x8a80–0x8aa4` | AArch64 entry uses SPSR `0x3c5` or `0x3c9` according to flag |
| `0x4a60–0x4a98` | EL2 context sets `SCR_EL3.HCE` |
| `0xa4cc–0xa55c` | PSCI endpoint derives EL2/EL1 entry from HCE |

This supports a narrow experiment, not a successful hardware claim. Keep the
normal `bootm` kernel/DTB/initramfs preparation and change its monitor service
only in a separate diagnostic build. Preserve boot0, monitor, SCP, kernel and
DTB. Do not invoke the unrelated `boot_hyper` partition loader or patch opaque
firmware. Retain emitted call-site disassembly and independent review.
The secondary-entry behavior is consistent with upstream
[TF-A v2.5 PSCI entry derivation](https://github.com/ARM-software/arm-trusted-firmware/blob/v2.5/lib/psci/psci_common.c#L570).

The next physical boot must capture BL31 SPSR `0x3c9`, eight CPUs entering EL2,
KVM initialization and `/dev/kvm`. These are prerequisites; actual KVM-only
guest execution, clean shutdown/restart and the remaining release gates still
follow. No NVMe erase or SPI programming is needed for this experiment.

## Authorization and preparation

The user explicitly permits discarding the data on the NVMe identified above.
This authorization applies to that exact model/serial/capacity, not an arbitrary
`nvme0` device. Before installation, revalidate the namespace identity, geometry,
idle state and concrete write plan using the guarded installer. Complete payload,
recovery and independent review first. No further authorization is needed for
the already authorized matching NVMe; a different physical target requires it.
No coordinator action has erased this drive or programmed SPI.
