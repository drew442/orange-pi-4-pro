# SPDX-License-Identifier: GPL-2.0-only
# Separate candidate for the pinned A733 distro NVMe target; never save env.
# This script restricts its own payload reads. The firmware retains the pinned
# distro search, so the boot partition must contain only the reviewed layout.
echo "A733 SPI/NVMe host candidate from ${devtype} ${devnum}:${distro_bootpart} ${prefix}"
if test "${devtype}" = nvme && test "${devnum}" = 0 && test "${distro_bootpart}" = 1 && test "${prefix}" = /boot/; then
    setenv bootargs "console=ttyS0,115200 earlyprintk=sunxi-uart,0x02500000 initcall_debug=0 loglevel=7 clk_ignore_unused swiotlb=65536 rdinit=/init ro"
    if load ${devtype} ${devnum}:${distro_bootpart} ${kernel_addr_r} ${prefix}uImage; then
        if load ${devtype} ${devnum}:${distro_bootpart} ${ramdisk_addr_r} ${prefix}uInitrd; then
            if load ${devtype} ${devnum}:${distro_bootpart} ${fdt_addr_r} ${prefix}dtb/allwinner/sun60i-a733-orangepi-4-pro.dtb; then
                if fdt addr ${fdt_addr_r} && fdt resize 65536; then
                    bootm ${kernel_addr_r} ${ramdisk_addr_r} ${fdt_addr_r}
                fi
            fi
        fi
    fi
else
    echo "Rejected SPI/NVMe candidate boot context"
fi
echo "SPI/NVMe host boot failed; capture serial output. Manual SD recovery: run bootcmd_mmc0"
false
