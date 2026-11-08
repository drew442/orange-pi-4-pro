# Next physical boot: VGIC diagnostic SD

Use the same NVMe installation and repaired identity. **Do not reinstall NVMe.**
The new image is:

`/home/drew/orange-pi-4-pro/out/vgic-host-sd-20261108-trial01/a733-host-sd.img`

It is **268,435,456 bytes (256 MiB)** and fits the actual 1,977,614,336-byte SD.
SHA-256: `0bb7ff051f8c79110e4bad15624f5026b21b3bf09593f476dead02a0793c56ac`.
It preserves the original firmware, kernel, initramfs, boot commands and state UUID;
only the device tree gains the GIC maintenance interrupt. The
[construction recipe](vgic-host-sd.md) explains the experimental source basis.
This image has not booted on the board. It does not package the RAM account or
launcher corrections used during the coordinator's guest diagnostics.

## Prepared remote write over SSH

The coordinator has also completed [non-destructive remote preparation](evidence/vgic-remote-sd-preparation-20261108.json)
for the same image: RAM-only staging, exact SU02G identity/capacity, idle-state
checks, image hash and independent review. The identified target is
`/dev/mmcblk1`, CID `035344535530324780716f216e00a8f5`. Exact-target operator
yes/no for overwrite and reboot is pending. No remote SD write has occurred.
After approval, the guarded single-write procedure repeats the identity and
idle checks, displays progress and skips optional readback. Failed boot may
still require the physical recovery procedure below. NVMe and SPI are outside
this write plan.

## Flash the same SD

Save any console session first. Shut down the running host normally with
`systemctl poweroff` over SSH, wait for shutdown, and remove board power before
removing SD. Keep the original host image available for recovery:
`out/host-deployment-20261106/a733-host-sd.img`.

On codex, inspect `lsblk -b -o PATH,TYPE,TRAN,RM,SIZE,MODEL,SERIAL,MOUNTPOINTS`
and `/dev/disk/by-id/`. Identify the same SU02G card/reader and unmount its exact
listed partitions. Select its whole-card by-id path below. The displayed
identity and capacity must describe that SD; stop if uncertain.

This performs one 256 MiB write with progress. There is no mandatory readback.
The prompt authorizes only the exact displayed SD target, not NVMe or SPI.

```bash
set -euo pipefail
CARD=/dev/disk/by-id/REPLACE_WITH_IDENTIFIED_WHOLE_SD
IMAGE=/home/drew/orange-pi-4-pro/out/vgic-host-sd-20261108-trial01/a733-host-sd.img
printf '%s  %s\n' 0bb7ff051f8c79110e4bad15624f5026b21b3bf09593f476dead02a0793c56ac "$IMAGE" | sha256sum -c -
test -b "$CARD"
test "$(lsblk -dn -o TYPE "$CARD")" = disk
test "$(sudo blockdev --getsize64 "$CARD")" -eq 1977614336
test -z "$(lsblk -nr -o MOUNTPOINTS "$CARD" | sed '/^[[:space:]]*$/d')"
lsblk -b -o PATH,TYPE,TRAN,RM,SIZE,MODEL,SERIAL,MOUNTPOINTS "$CARD"
printf 'Overwrite the first 256 MiB of this exact SD (%s)? [yes/no] ' "$CARD"
read -r answer
case "$answer" in yes|YES|Yes) ;; *) exit 1 ;; esac
sudo dd if="$IMAGE" of="$CARD" bs=4M conv=fsync status=progress
sudo blockdev --flushbufs "$CARD"
```

Readback remains optional troubleshooting if you suspect corruption. Do not run
full-NVMe hashes or benchmarks for this test.

## Boot and return evidence

Safely remove SD, insert it with board power off, and leave NVMe/Ethernet attached.
Capture the complete serial session from before power-on using the existing
**3.3 V TTL, 115200 8N1, no flow control** wiring. Do not connect adapter VCC.
The host should retain its SSH identity and both authorized keys. Its DHCP
address may change; compare the printed SSH fingerprint with the retained
trusted value before connecting:
`SHA256:Epfegy9GsJ6ng4/0EH3dZmir7beQj5uYTnIbE4JPfEI`.

After SSH is available, run these read-only checks as root and save their output
with the boot log:

```sh
uname -a
cat /proc/sys/kernel/random/boot_id
findmnt -T /
findmnt -T /run/a733-state
networkctl status end0 --no-pager
dmesg | grep -iE 'kvm|vgic|GICv3|hyp'
od -An -v -tx1 /sys/firmware/devicetree/base/interrupt-controller@3400000/interrupts
python3 -I -B - <<'PY'
import fcntl, os
fd = os.open('/dev/kvm', os.O_RDWR | os.O_CLOEXEC)
try:
    print('KVM_CAP_IRQCHIP:', fcntl.ioctl(fd, 0xae03, 0))
    print('KVM_CAP_ARM_USER_IRQ:', fcntl.ioctl(fd, 0xae03, 144))
finally:
    os.close(fd)
PY
```

The new property should be `00 00 00 01 00 00 00 09 00 00 00 04`.
A positive `KVM_CAP_IRQCHIP` and VGIC initialization are expected test results,
not assumed successes. Return the saved log path and current DHCP address.
Leave the host running so the coordinator can run the actual guest checks over
trusted SSH. `/dev/kvm` presence alone is insufficient.

If boot fails, preserve the serial log, power off, and restore the original
256 MiB host SD image using the same capacity, identity, mount and yes/no guards
above, with its original SHA-256 from
[the deployment guide](deploy-personalized-20261106.md#switch-the-same-sd-to-the-matching-host-bootstrap).
This restores SD boot files only; it cannot undo NVMe guest/state changes.
Neither this test nor its recovery writes SPI.
