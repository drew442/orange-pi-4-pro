# Exercise missing-NVMe recovery on the physical board

**Prepared trial; independent procedure review passes. Not hardware evidence.**
[Preparation evidence](evidence/missing-nvme-trial-preparation-20261109.json).
The [November 9 workload result](evidence/eight-hour-endurance-result-20261109.json)
passes the 8-hour (28,800-second) criterion. Run only after current guest/STATE
backups are complete and exact target/recovery prerequisites are satisfied.
Keep heatsinks attached. This is a Linux driver fault injection on the actual
board, not physical SSD removal or proof of every electrical/controller fault.

The pinned kernel builds the PCI NVMe driver into the image. Its
[`nvme_init`](https://github.com/orangepi-xunlong/linux-orangepi/blob/2ac08e8c7cdc28abbdc5c9a9dd812f887ae9c79f/drivers/nvme/host/pci.c#L3608)
registers that driver, while its
[initcall blacklist](https://github.com/orangepi-xunlong/linux-orangepi/blob/2ac08e8c7cdc28abbdc5c9a9dd812f887ae9c79f/init/main.c#L1110)
can skip a named initialization function. The authenticated kernel config
`8a29d9c36e52db7e8a7d6ff757dc69e660f4273c99e5f00c5dbc5a9b7e060675`
has `CONFIG_KALLSYMS=y`, `CONFIG_NVME_CORE=y` and `CONFIG_BLK_DEV_NVME=y`.
`modprobe.blacklist` would not be the appropriate control for this built-in driver.
Source support does not prove that this compiled image accepts the injection.

## One boot from the retained protected SD

Use [remote serial control](remote-serial-control.md) and preserve the complete
console interval off-board. Require the matched protected SD bootstrap
(`ab5e8c…` prefix) and current VGIC-corrected kernel/DTB/protected initramfs.
This test uses SD boot files because Linux must reach recovery without NVMe.
It is not an NVMe-file boot or SPI test, and is not a release reliability boot.

After an orderly shutdown/reboot, interrupt U-Boot's countdown. Confirm the
console is the recorded board and `mmc 0:1` is its known SD BOOT partition.
Load the same three paths used by the protected script. Stop if any load fails;
do not issue the final boot command using stale RAM contents. The fixed load
addresses are those observed in the successful protected serial boot.

```text
mmc dev 0
load mmc 0:1 0x41000000 /boot/uImage
load mmc 0:1 0x43300000 /boot/uInitrd
load mmc 0:1 0x43000000 /boot/dtb/allwinner/sun60i-a733-orangepi-4-pro.dtb
fdt addr 0x43000000
fdt resize 65536
setenv bootargs console=ttyS0,115200 earlyprintk=sunxi-uart,0x02500000 initcall_debug=0 loglevel=7 clk_ignore_unused swiotlb=65536 rdinit=/init ro initcall_blacklist=nvme_init
bootm 0x41000000 0x43300000 0x43000000
```

These are U-Boot commands, not Linux shell commands. Do not run `saveenv`,
`env save`, boot-counter writes or a script that replaces the injected bootargs.
The bootarg exists only in the current RAM environment; do not edit boot files.
Preserve U-Boot version, device/partition confirmation, every load result,
final bootargs and the whole kernel/initramfs console output.

## Required observation and recovery

`configs/host-init` retries STATE resolution ten times, with nine one-second
sleeps, then its failure path should retain a physical serial root shell with
no automatic reboot. This is an expectation to verify. Debug-level blacklist
messages may be absent; do not require an invented success marker.

At the recovery prompt, record:

```sh
/bin/busybox cat /proc/cmdline
/bin/busybox cat /proc/sys/kernel/random/boot_id
/bin/busybox cat /proc/mounts
/bin/busybox ls -ld /sys/bus/pci/drivers/nvme /sys/class/block/nvme0n1 /dev/nvme0n1
```

The last command should report absent entries. Require the injected bootarg,
no Linux NVMe namespace/driver, no NVMe root/STATE/GUESTS mount, the expected
handoff failure and a usable serial shell. If the driver/namespace exists or
normal host boot occurs, injection failed: retain the result and do not count
it as missing-NVMe recovery. Do not mount, format or repair anything in this test.

From the recovery shell, use `/bin/busybox reboot -f` after retaining evidence. Interrupt
the next countdown and confirm no blacklist remains, or allow the ordinary
protected boot script to replace bootargs. Do not save environment changes.
Verify normal NVMe detection, expected root/STATE selection, pinned SSH identity
and management, then execute an actual KVM guest. Preserve both faulted and
normal boot IDs and console records. This closes only the fault-injected
Linux missing-storage path when actually demonstrated. It does not establish
physically disconnected-SSD behavior. SPI recovery, dirty-journal recovery and
other acceptance gates remain separate. No result has been claimed by this
preparation.
