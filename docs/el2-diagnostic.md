# Experimental EL2 diagnostic boot

The [first physical boot](board-diagnostic-20261104.md) entered EL1, leaving KVM
unavailable. This separate SD image changes normal U-Boot `bootm` to use the
pinned monitor's existing hypervisor service `0x8000ff40`. **It has not booted on
the board.** Its expected SPSR is `0x3c9`; this is a test expectation.

Leave both heatsinks attached. This image mounts no disk filesystem, erases no
NVMe and programs no SPI. It runs the same RAM diagnostics as the first image.
There is no QEMU in this diagnostic; actual guest execution remains a later gate.

## Download and verify

The complete raw image is **268,435,456 bytes**, well within the measured
1,977,614,336-byte SD. Gzip size is **16,188,653 bytes**.
[Build evidence and hashes](evidence/el2-diagnostic-20261104.json) distinguish
host checks from pending hardware acceptance.

On codex.drewnet.online, the files already exist at:

```text
/home/drew/orange-pi-4-pro/out/el2-diagnostic-20261104/a733-el2-diagnostic.img
/home/drew/orange-pi-4-pro/out/el2-diagnostic-20261104/a733-el2-diagnostic.img.gz
```

On your Linux SD-flashing computer, download into a new working directory:

```sh
mkdir a733-el2-test
cd a733-el2-test
scp drew@build.drewnet.online:/home/drew/orange-pi-4-pro-build/el2-diagnostic-20261104/a733-el2-diagnostic.img.gz .
printf '%s  %s\n' 3e07a434284097314000c00f0245581eee5c4bdc000f434cbf7c071bdb0fe96e a733-el2-diagnostic.img.gz | sha256sum -c -
gzip -dk a733-el2-diagnostic.img.gz
printf '%s  %s\n' 08fff0a88c509ee484c2450c58d42629fd90eedb8cd4c57513f98f4f78611b58 a733-el2-diagnostic.img | sha256sum -c -
```

Preserve the earlier baseline image as recovery input. The experimental build
leaves it untouched. A different compiled U-Boot can have a different vendor
random challenge; use the hashes of this specific candidate.

## Reflash the identified diagnostic SD

Power off the board before removing its card. Use the **same physical SD already
selected and authorized for diagnostics**, not the NVMe. Its board-reported CID
is `035344535530324780716f216e00a8f5`, name SU02G, capacity 1,977,614,336 bytes.
A USB reader may expose the reader serial instead of the card CID. Confirm the
reader/card physically and inspect its current path on the flashing computer:

```sh
lsblk -b -o PATH,TYPE,TRAN,RM,SIZE,MODEL,SERIAL,MOUNTPOINTS
ls -l /dev/disk/by-id/
```

Set `CARD` to the previously authorized whole-card identifier, substituting its
actual current by-id path. Do not paste an example `/dev/sdX` or select by size
alone. If this is a different card/reader target whose identity is uncertain,
return that report before writing. Existing authorization persists for the same
identified target. The NVMe erase authorization is separate.

Unmount any mounted partitions of that card using their exact paths from
`lsblk`, for example `sudo umount -- /dev/SELECTED_CARD_PARTITION`. Then run:

```sh
set -euo pipefail
CARD=/dev/disk/by-id/REPLACE_WITH_AUTHORIZED_WHOLE_SD
lsblk -b -o PATH,TYPE,TRAN,RM,SIZE,MODEL,SERIAL,MOUNTPOINTS "$CARD"
test -b "$CARD"
test "$(lsblk -dn -o TYPE "$CARD")" = disk
test "$(sudo blockdev --getsize64 "$CARD")" -eq 1977614336
test -z "$(lsblk -nr -o MOUNTPOINTS "$CARD" | sed '/^[[:space:]]*$/d')"
printf '%s  %s\n' 08fff0a88c509ee484c2450c58d42629fd90eedb8cd4c57513f98f4f78611b58 a733-el2-diagnostic.img | sha256sum -c -
sudo dd if=a733-el2-diagnostic.img of="$CARD" bs=4M conv=fsync status=progress
sudo blockdev --flushbufs "$CARD"
sudo head -c 268435456 "$CARD" | sha256sum
```

The final readback must be
`08fff0a88c509ee484c2450c58d42629fd90eedb8cd4c57513f98f4f78611b58`.
These commands overwrite the selected card's first 256 MiB; its remaining bytes
are unchanged. Before ejecting, capture `sudo sha256sum "$CARD"` for a full-card
baseline too. Preserve the capacity and hash together. Do not auto-mount its
filesystem read-write. Safely remove the card after reads finish.

## Boot and return evidence

Use the already working serial wiring: **3.3 V TTL, 115200 baud, 8N1, no flow
control, adapter VCC disconnected**. The [serial guide](diagnostic-image.md#serial-console-and-first-boot)
contains header orientation and Linux capture commands. Start capture before
power-on and preserve the complete boot0/BL31/U-Boot/Linux log, even on failure.
Insert the reflashed SD with power off, leave NVMe connected and power on normally.
DHCP failure must not prevent the RAM shell.

At the shell, run these read-only commands:

```sh
uname -a
cat /proc/cmdline /proc/meminfo /proc/mounts
cat /sys/devices/system/cpu/online
ls -l /dev/kvm
dmesg | grep -Ei 'kvm|hyp|el1|el2|CPU\(s\)|nvme|pcie|error|fail'
cat /sys/class/block/mmcblk1/ro /sys/class/block/mmcblk1/stat
cat /sys/class/nvme/nvme0/model /sys/class/nvme/nvme0/serial /sys/class/nvme/nvme0/firmware_rev
cat /sys/class/block/nvme0n1/size
cat /sys/class/block/nvme0n1/queue/logical_block_size /sys/class/block/nvme0n1/queue/physical_block_size
ip addr
ip route
```

Return the full serial log plus the flashing image readback hash and before-boot
full-card hash/capacity. The desired new observations are:

- BL31 `Next image spsr = 0x3c9`;
- Linux `All CPU(s) started at EL2`, with online CPUs `0-7`;
- KVM initialization instead of `HYP mode not available`, and `/dev/kvm`.

Before any recovery reflash, preserve the experimental image's post-boot SD
evidence. If the RAM shell is available, capture the final SD `stat`, run
`poweroff -f`, wait for console output to stop and remove board power. If no
shell is available, preserve the serial capture through the last message before
removing power; record that no final Linux counter was obtainable. Read the whole
SD offline with `sudo sha256sum "$CARD"` on the flashing computer, keeping all
partitions unmounted. Compare with its pre-boot full-card hash and retain both
hashes and the card capacity **before overwriting anything for recovery**.

Identical hashes and Linux counters are evidence toward the SD policy, not proof
against identical-content firmware writes. Full firmware audit, repeated cycles,
installed NVMe boot and actual KVM guest remain required.

If it stalls, return the full capture ending at the last message. Do not erase
NVMe or program SPI to try to fix a failed EL2 test. To recover, power off and
reflash the preserved baseline diagnostic image to the same authorized card.
The [baseline guide](diagnostic-image.md#current-verified-artifact) supplies the
exact download and decompression commands if it is not already available.
Reidentify `CARD` on the flashing computer as above; the same physical target
authorization persists. With its partitions unmounted, run:

```sh
set -euo pipefail
test -b "$CARD"
test "$(lsblk -dn -o TYPE "$CARD")" = disk
test "$(sudo blockdev --getsize64 "$CARD")" -eq 1977614336
test -z "$(lsblk -nr -o MOUNTPOINTS "$CARD" | sed '/^[[:space:]]*$/d')"
printf '%s  %s\n' b05086afaab3d2f9da6c33b8e457f3552cce2899c08824d371615008bf66d9fd a733-diagnostic.img | sha256sum -c -
sudo dd if=a733-diagnostic.img of="$CARD" bs=4M conv=fsync status=progress
sudo blockdev --flushbufs "$CARD"
sudo head -c 268435456 "$CARD" | sha256sum
```

Require readback `b05086afaab3d2f9da6c33b8e457f3552cce2899c08824d371615008bf66d9fd`,
then safely remove and boot the card normally. No persistent firmware
modification is involved.


## Rebuild and inspect

On the pinned Jammy builder, from a fresh protected checkout with existing `out/`:

```sh
bash build/el2-diagnostic.sh /home/drew/orange-pi-4-pro-build /home/drew/orange-pi-4-pro-build/artifacts "$(pwd -P)/out/el2-diagnostic-new"
python3 scripts/verify-el2-diagnostic.py "$(pwd -P)/out/el2-diagnostic-new/artifacts" /home/drew/orange-pi-4-pro-build/artifacts
```

This ran end to end with two compilation jobs. It performs no source/package
fetch. It checks full builder package inventory, pinned source commits, compiler
archive, 26 vendor binary/tool inputs and immutable baseline artifacts. It builds
private U-Boot sources with the existing environment-nowhere configuration and
retains its random challenge, generated source, ELF, map, object and disassembly.
The known-booted version suffix and compiled timestamp are retained.

The raw image is a literal baseline copy with only its equal-size 1,392,640-byte
boot package replaced at offset 16,793,600. All outside bytes compare identical,
including boot0, partition table, boot script and complete ext4 filesystem.
Packed monitor/SCP match their source pins. Kernel, DTB and initramfs match the
booted baseline. Independent review plus host checks establish this narrow
change; they do not establish physical EL2 behavior or KVM guest execution.
