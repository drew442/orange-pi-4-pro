# Ethernet bring-up after the EL2 boot

The operator's second boot log shows BL31 SPSR `0x3c9`, all eight CPUs at EL2,
KVM VHE initialization and `/dev/kvm`. No guest has run. Ethernet negotiates
1 Gbps/full duplex but DHCP gets no lease; the operator reports the switch's
native VLAN is correct and the DHCP server sees no requests from
`10:f4:ae:90:5e:57`.

The diagnostic image has a confirmed module-helper packaging defect: the kernel
requests `/sbin/modprobe`, while BusyBox applets are installed under `/bin` and
`/sbin/modprobe` was not supplied. The network log reports **Generic PHY**.
`CONFIG_MOTORCOMM_PHY=m` is enabled. If the actual PHY is YT8531 (`0x4f51e91b`),
its pinned vendor driver configures a 125 MHz output that the MAC's external
transmit-clock mode needs. Autonegotiated link alone does not prove that MAC
frames leave the board. The PHY ID and live result must confirm this hypothesis.
Do not change VLANs or device-tree delays based on it alone.

## Latest physical result

The [next capture](evidence/ethernet-lease-20261105.json) confirms PHY ID
`0x4f51e91b` and binding to `YT8531 Gigabit Ethernet`. Its first DHCP attempt
still failed. After the operator moved the cable to another device and back,
an explicit `udhcpc` obtained `192.168.1.115/24` for 7,200 seconds. This proves
one successful exchange, with no cold-boot repeatability or route/DNS/TLS proof.
It does not isolate PHY/MAC link-reset state from upstream behavior.

The capture includes a MAC VLAN-filter timeout during interface shutdown, plus
an unexpected `}` and later `phy_test` calls that print only `BEFORE`. Do not
reuse that session's partially defined function. The explicit module, binding
and final DHCP outputs remain useful independent evidence. The RX drop counters
are cumulative and cannot be assigned to DHCP without a bounded capture.

While the successful session is still running, paste these short commands one
line at a time at the `/ #` prompt; they do not reset the interface or write media:

```sh
cat /sys/class/net/eth0/phydev/phy_id
readlink /sys/class/net/eth0/phydev/driver
ip addr show eth0
ip route
cat /etc/resolv.conf
cat /proc/net/dev
```

Return the output. The next cold-boot test should use the corrected preparation
image with the cable left connected, avoiding a second diagnostic-only reflash.
See [preparation boot](installer-preparation.md#next-physical-boot).

## RAM-only live test

Run this on the **currently booted diagnostic RAM shell**, recording the complete
serial output. Wait for the `/ #` prompt before pasting. It changes runtime interface/module state only; no SD/NVMe/SPI
write or formatting command is involved. The brief interface reset interrupts
Ethernet. Keep the existing serial connection.

```sh
# Reduce asynchronous console noise without clearing the kernel log.
dmesg -n 3
phy_test() {
echo '=== BEFORE ==='
cat /proc/sys/kernel/modprobe
ls -l /sbin/modprobe /bin/modprobe
cat /sys/class/net/eth0/phydev/phy_id
readlink /sys/class/net/eth0/phydev/driver
lsmod
cat /proc/net/dev

# Restore the kernel's expected helper in RAM, then release Generic PHY before
# registering the vendor module. Loading the module while up does not rebind it.
mkdir -p /sbin
if [ ! -e /sbin/modprobe ]; then ln -s /bin/busybox /sbin/modprobe; fi
ip link set eth0 down || return 1
if [ -d /sys/module/motorcomm ]; then
    if ! modprobe -r motorcomm; then
        echo 'Cannot remove motorcomm; retaining failure and stopping this test.'
        ip link set eth0 up
        return 1
    fi
fi
if ! modprobe motorcomm; then
    echo 'Cannot load motorcomm; retaining failure and stopping this test.'
    ip link set eth0 up
    return 1
fi
ip link set eth0 up || return 1
sleep 5

echo '=== DRIVER AND LINK ==='
cat /sys/class/net/eth0/phydev/phy_id
readlink /sys/class/net/eth0/phydev/driver
lsmod
cat /sys/class/net/eth0/carrier /sys/class/net/eth0/speed /sys/class/net/eth0/duplex
dmesg | grep -Ei 'eth0|stmmac|dwmac|motorcomm|yt85|phy|clock' | tail -60

echo '=== DHCP ==='
udhcpc -f -n -q -t 5 -T 3 -i eth0 -s /etc/udhcpc.script

echo '=== AFTER ==='
cat /proc/net/dev
ip addr show eth0
ip route
}
phy_test
```

If Motorcomm was already loaded, the test removes it only after the interface
is down, then reloads it to trigger driver matching. A removal/load failure
stops the test without forcing an unbind. The pinned BusyBox lacks `ip -s`;
`/proc/net/dev` supplies the before/after counters.

Return the PHY ID, bound driver name, `modprobe`/DHCP output and before/after
counters. A missing file or failed command is evidence; retain it. Do not load
an unrelated PHY driver or write PHY registers if this test fails. The permanent
image fix and its hardware result will be recorded separately.

During the DHCP window, useful switch evidence is whether it learns
`10:f4:ae:90:5e:57` on this port and whether its received-frame/error counters
increase. If a mirror capture is available, filter for that source MAC and
UDP ports 67/68. The DHCP server's absence of logged requests alone cannot locate
a drop between the MAC, switch, relay and server. Board TX counters alone also
do not prove that a valid frame reached the wire.

If it still fails, collect these read-only counters as well:

```sh
for f in /sys/class/net/eth0/statistics/*; do
    echo "$f"; cat "$f"
done
cat /proc/interrupts
```

After capture, `dmesg -n 7` restores the boot command line's console verbosity.
The DHCP hook changes only RAM-backed addresses/routes; it stores no lease.

## Pinned source basis

Kernel commit `2ac08e8c7cdc28abbdc5c9a9dd812f887ae9c79f` supplies the
[PHY initialization](https://github.com/orangepi-xunlong/linux-orangepi/blob/2ac08e8c7cdc28abbdc5c9a9dd812f887ae9c79f/drivers/net/phy/motorcomm.c#L102),
[PHY clock setup](https://github.com/orangepi-xunlong/linux-orangepi/blob/2ac08e8c7cdc28abbdc5c9a9dd812f887ae9c79f/drivers/net/phy/motorcomm.c#L406)
and [external MAC clock selection](https://github.com/orangepi-xunlong/linux-orangepi/blob/2ac08e8c7cdc28abbdc5c9a9dd812f887ae9c79f/bsp/drivers/stmmac/dwmac-sunxi.c#L63).
Its PHY core ignores a missing module-helper error before Generic PHY fallback.
The extracted board DTB retains TX delay 12/RX delay 10; the driver's optional
`delay-maps` notice does not mean these settings are missing. No delay or DTB
change is justified by the present evidence.

## Corrected image, if a fresh boot is needed

The RAM test above can diagnose the existing boot without reflashing. A corrected
image is also prepared: [artifact evidence](evidence/network-diagnostic-20261105.json).
Two fresh Jammy builds produced identical image/gzip bytes. Independent review,
archive/module comparisons and standalone image inspection passed. The kernel,
DTB, boot0 and EL2 boot package are unchanged. Physical boot and DHCP with this
image remain untested.

On the flashing computer, in a new directory:

```sh
scp drew@build.drewnet.online:/home/drew/orange-pi-4-pro-build/network-diagnostic-20261105/a733-diagnostic.img.gz .
printf '%s  %s\n' 9ecd929b1f6ea302659b4b19c6837ba747131c8e7dc091a1a541793fddcf7339 a733-diagnostic.img.gz | sha256sum -c -
gzip -dk a733-diagnostic.img.gz
printf '%s  %s\n' eea4ca991ff62c80d5f158a97c2510cee13f421cbfe9b7bad32b922e2bf26fcd a733-diagnostic.img | sha256sum -c -
```

The gzip is 16,188,752 bytes; the raw image is 268,435,456 bytes. Follow the
[identified-SD flashing and serial procedure](el2-diagnostic.md#reflash-the-identified-diagnostic-sd),
substituting **`a733-diagnostic.img`** and raw hash
**`eea4ca991ff62c80d5f158a97c2510cee13f421cbfe9b7bad32b922e2bf26fcd`**
where that procedure names the previous EL2 image/hash, including its pre-write
check and post-write readback. Keep the original image for recovery. Use the
same authorized SU02G SD; never select the NVMe or SPI. Capture the complete
115200 8N1 serial boot, then the driver/link/DHCP outputs above.

To rebuild on the prepared Jammy builder, from a matching repository snapshot
on validated fixed storage (fresh output directory required):

```sh
sudo python3 -I -B build/network-diagnostic.py \
  /var/lib/a733-el2-work-20261104/out/el2-diagnostic-20261104-03/artifacts \
  /home/drew/orange-pi-4-pro-build/busybox/bin/busybox \
  "$PWD/out/network-diagnostic-NEW"
```

The recipe authenticates the original EL2 artifacts and pinned BusyBox, retains
module contents/modes, checks builder packages and places temporary staging on
validated storage. It does not download, compile or open physical targets.
