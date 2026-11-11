# Qualify SPI-to-NVMe boot on the recorded A733

The operator requested operation with the SD physically removed and explicitly
authorized SPI programming on November 11. **The system now operates without SD.** SPI cold/warm boots and two actual KVM
runs pass independent review. [Operational result](evidence/spi-operational-20261111.json)
and [verified custody](evidence/spi-operational-custody-20261111.json).
The prior protected-SD release evidence remains retained.

## Recovery route

The A733 TRM V1.00 places SMHC0 first in every normal GPADC sequence in Table 3-2
and every non-skip eFuse sequence in Table 3-4, including Try mode. These are on
printed pages 13–14, PDF pages 34–35. The retained document identity is
75,246,396 bytes, SHA-256
`cdd77e5779d3c69d41b5f237f3db3db51c67fdf0f3aca9a2eedc77d9420f248d`.
[Source document](https://gitlab.com/tina5.0_aiot/product/docs/-/blob/d0dc4d615be00b76e3a0818d0818eff8f69ffa15/A733/Hardware%E7%A1%AC%E4%BB%B6%E7%B1%BB%E6%96%87%E6%A1%A3/%E8%8A%AF%E7%89%87%E6%89%8B%E5%86%8C/A733_User%20Manual_V1.00.pdf).

The coordinator selected SD recovery using that ROM priority and the physically
exercised protected-host/private RAM recovery images. The earlier USB-FEL-specific
programmer prerequisite was a project precaution. The registered policy now
records ROM source verification and physical SD recovery separately.
[Exact registered policy](evidence/spi-sd-recovery-policy-20261111.json).

Keep the known SU02G recovery card available and the UART bridge powered. If SPI
or NVMe boot fails, power off, insert the known SD, and boot through SMHC0.
The protected card provides the host when NVMe is healthy. The private RAM
recovery image provides maintenance access when the installed host cannot boot.
Original SPI and the backup-derived candidate are retained on codex and the
Ubuntu builder. A separately reviewed restore plan can return changed flash
units to the original blank image. Preserve any failed programming receipts
before attempting recovery.

## Observed NVMe boot before programming

The coordinator intercepted U-Boot through the independent UART, scanned NVMe,
selected device 0 and identified SPCC serial `134807190FC700149277`, firmware
EDFM20.0. Listing partition 1 `/boot/` and loading its 1,387-byte `boot.scr`
succeeded. `run bootcmd_nvme` loaded the script, kernel, initramfs and VGIC DTB
from NVMe and started Linux. Host boot
`a55b0c1c-91fc-4fee-80b7-3a963a20d72e` passed the installed release/device guards,
SSH, automatic NTP, eight CPUs at EL2, KVM API 12 and IRQCHIP capability 1.
Raw command responses, console output and the ready guard are under
`out/spi-nvme-hardware-20261111-01/`.

## Target and reviewed write

The target is `/dev/mtd0`, character `90:0`, XM25QU128C, JEDEC `204118`,
16,777,216 bytes with 65,536-byte erase units. A fresh complete read matches
original SHA-256
`dffab0dd410657cb30c7b2fd7f2586a4792e8472e58882b3532581f8111a646d`.
The initial programmed backup-derived SPI image was
`83edcd7f448e8353163666016a6bae3e9b70d041a5e4d8454f48931839205e90`.
The initial programming plan digest was
`1ae1c742c7b2547e27555e25a3bee43600881853b8958486d36c570fea5f5286`.

The completed transaction reconstructed erase units 0–25, preserving bytes outside boot0
`[0,237568)` and boot package `[262144,1654784)`. Units 26–255 are untouched.
Its order is units 4–25, then 1, 2, 3 and header unit 0 last. The existing guarded
programmer and off-device durable event capture enforce exact source, image,
device, namespace and idle-state checks. Readback and the final captured outcome
must pass before reboot. Source changes and the concrete plan receive independent
review before execution.

## Physical completion checks

After programming, first reboot with the known SD inserted and verify that ROM
still selects its boot0 and returns to the protected host. Then request clean
poweroff. Tell the operator when the UART confirms powerdown so they can remove
SD and restore power. Retain the complete SPI boot console, flash identity,
U-Boot NVMe reads, actual Linux root/STATE, SSH and automatic NTP. Confirm the SD
is absent from Linux and run a real KVM guest with its CPU/disk/network,
shutdown/checkpoint and restart checks. Reboot with SD absent and verify the
same route. Record results before claiming completion.

## Programming and SD recovery result

The physical write completed with exit 0. All 26 changed units and the complete
16 MiB flash match the reviewed candidate. The descriptor closed successfully,
and 56 canonical events are retained off-device with a verified builder copy.
The subsequent reboot with SD inserted selected SD boot0, reported storage
type 1 and the SD host bootstrap, then passed the installed release guard on
boot `96e14dbb-496b-4484-9e7d-c9eaf3773dfa`. Clean UART poweroff is confirmed
for operator SD removal. [Programming evidence](evidence/spi-programming-20261111.json).

The first native capture invocation lacked access to PID 1 namespace descriptors
and stopped before starting SSH. The unchanged controller then ran as root with
private root-owned native staging. A local post-run parser initially expected
the wrong final event tag, then accepted the actual `programmer_outcome` receipt.
Programming was executed once.

## First SD-free boot and SPI calibration

The operator removed SD and restored power. The captured cold boot selected
SPI NOR (storage type 3), identified the XM25QU128C, and loaded Linux from
NVMe partition 1. Boot `461104ad-de37-4fbe-8ecc-c81c10203b9a` passed the
SD-absence, original identity, NVMe A/STATE, SSH, NTP, eight-CPU EL2/VHE and
KVM readiness checks. Real nonroot QEMU PID 2066 subsequently passed CPU,
NVMe disk counter 7→8 and HTTP checks, then guest-requested poweroff with
raw exit status 0. Checkpoint 3 is durable.

Vendor U-Boot initializes its 2 KiB boot-parameter/calibration area at
`[260096,262144)` during SPI startup. Its unaligned-write warning continues
through a real write path. The first boot changed 2,046 bytes entirely within
that area. Boot0 and the complete U-Boot/firmware package remain byte-identical
to the programmed input. The initialized 16 MiB flash SHA-256 is
`812038f8291faffc0a404dc06afa2d1f7b52f2404ead566b79b44eef4bca1be8`.
A verified, fsynced snapshot is retained on the builder at
`/var/lib/a733-spi-operation-20261111-01/postboot-spi.bin`.

The original strict-gap programming plan is stale after this vendor metadata
initialization. Future installation or restoration requires a fresh complete
flash read and an independently reviewed plan accounting for these bytes. Keep
the known SD recovery path and both original/initialized backups. Identical
post-boot hashes cannot establish absence of repeated identical-content writes.
Source findings and the measured difference are retained in the final evidence.

## Completed SD-free restart and guest persistence

Warm SPI/NVMe boot `5858af7a-977e-4a9b-971e-6d71ae7858d3` passed with SD
still absent. The guest resumed from checkpoint 3, ran as nonroot QEMU PID 2044
with real VM/vCPU/VGIC descriptors, advanced disk counter 8→9 and passed HTTP.
It requested clean poweroff with raw exit status 0. Checkpoint 4 is durable,
manifest `c47520b6d87cae6db6032827f58442d35ba389576712cb1be5e020a19e2bc60f`,
disk `fb46ba776d5468d250dd56d018fd50f939e5f897c6f5b3676257dc0413ce6efa`.
All eight history records verify. The guest is stopped and its current RAM
service is rendered for the next launch. Accounts/units need explicit recreation
after future host reboots as described in the guest operations guide.

The post-warm SPI hash is
`e829cf7b08fa9753866df7de727b2d1b92bb5c525f3bbf4cd94315d528dc275a`.
Its 66 changed bytes relative to the first initialized snapshot remain within
the same vendor parameter area. Every boot0/package byte still matches the
factory candidate. Preserve both calibrated snapshots. The operational checks
cover one cold and one warm SPI boot. The previous ten-cycle release records
cover the earlier protected-SD configuration.
