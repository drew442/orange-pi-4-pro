# SPDX-License-Identifier: GPL-2.0-only
# Derived from the pinned vendor boot-sun60iw2.cmd load protocol.
# NVMe host bootstrap prototype; Linux mounts root/state from NVMe, never SD.
# Addresses and legacy arm image wrappers follow the pinned vendor boot script
# and kernel scripts/package/builddeb; this is an arm64 kernel payload.
echo "A733 NVMe host bootstrap from ${devtype} ${devnum} ${prefix}"
setenv boot_targets mmc0
setenv bootargs "console=ttyS0,115200 earlyprintk=sunxi-uart,0x02500000 initcall_debug=0 loglevel=7 clk_ignore_unused swiotlb=65536 rdinit=/init ro"
if load ${devtype} ${devnum} ${kernel_addr_r} ${prefix}uImage; then
    if load ${devtype} ${devnum} ${ramdisk_addr_r} ${prefix}uInitrd; then
        if load ${devtype} ${devnum} ${fdt_addr_r} ${prefix}dtb/allwinner/sun60i-a733-orangepi-4-pro.dtb; then
            fdt addr ${fdt_addr_r}
            fdt resize 65536
            bootm ${kernel_addr_r} ${ramdisk_addr_r} ${fdt_addr_r}
        fi
    fi
fi
echo "NVMe host bootstrap load/boot failed; capture serial output. No persistent environment is saved."
while true; do sleep 1; done
