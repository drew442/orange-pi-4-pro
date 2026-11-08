# Current images for this board

The corrected NVMe installation and host SD already booted, synchronized time,
and ran the restored KVM guest through counters 3 → 4 → 5. Its endurance attempt
subsequently lost SSH; [serial diagnosis is pending](endurance-run-20261108.md).
Capture that console output before changing media. Installation and release
acceptance are separate; this is not a public release.

Paths below are relative to `/home/drew/orange-pi-4-pro` on codex.

| Purpose | Existing artifact |
| --- | --- |
| SSH RAM recovery, same board | `out/ssh-ram-installer-20261108/a733-ssh-installer.img.gz` |
| Normal corrected host SD bootstrap | `out/vgic-runtime-bootstrap-20261108/retained-01/a733-host-sd.img.gz` |
| Corrected NVMe installation | `out/vgic-nvme-image-20261108/retained-02/nvme.img.gz` |

The two SD artifacts each expand to **268,435,456 bytes**, fitting the actual
**1,977,614,336-byte** SU02G card, CID `035344535530324780716f216e00a8f5`.
The recovery artifact starts an independent RAM environment with SSH and no
automatic NVMe installation. It contains this board's private SSH host key;
keep it private and use it only for this board.

The normal host bootstrap selects STATE UUID
`360dfb39-f49f-487e-b307-0bb887c854c5` on the corrected NVMe. Earlier host SD
images selecting `00fd…`, including the November 6 deployment and `0bb7…` VGIC
trial, are incompatible with this new state. The independent RAM recovery image
works without selecting that installed state. SPI remains unprogrammed.

## Authenticate the selected download

Check the compressed source once on the flashing computer:

| Artifact | Compressed bytes | Compressed SHA-256 |
| --- | ---: | --- |
| SSH RAM recovery SD | 41,229,263 | `2f9cdd006e6d5c2c59e5ed1aba68f3ed7dd267a7f61b3672e590d926468c4eec` |
| Corrected host SD | 17,271,954 | `fbfa9f4eae5913a61768e90fb62b2a3f48df9a2d38aed5b7380b83873f5307b6` |
| Corrected NVMe | 545,632,496 | `3e560264bb64a9bc23b342febdff2146955ca97cb6e0279e5240c29d4e6d85bc` |

Expanded recovery SD SHA-256 is
`d3a02224efdb2c2eeccc7115d84cd6c4b2d75c7d06580b8f7812d12a546bc3f6`;
expanded host SD SHA-256 is
`f02c74b4c001f1608d0005b6ff23fe82bffa8c02f71e19e5b6573819125ae383`.
The NVMe stream expands to **256,060,514,304 bytes**, SHA-256
`835031b81d0320d6f9f35e74b9be67b494006c26a25fa9070f3e0a8f434ebc11`.
Its producer manifest is retained at `out/vgic-nvme-image-20261108/build-02.stdout`
(35,023 bytes, SHA-256 `addfcafe239a2aa075435383cc4742869d256d98beecd2526806da206d98f4a2`).
Same-board identity was restored after installation, so the personalized whole
NVMe no longer has the original stream hash. No NVMe reinstall is requested.

## Flash an SD after diagnosis calls for it

Power down before removing the card. On a Linux flashing computer, identify the
inserted whole SD by its appearance in `lsblk -b -d -o PATH,SIZE,MODEL,SERIAL,TRAN`.
Set `A733_SD_DEVICE` to that exact whole device and `A733_SD_IMAGE` to the copied
recovery or host gzip selected above. The existing authorization covers this
known card; another physical target needs its own identification/authorization.
Unmount all of that card's partitions before continuing.

```bash
set -euo pipefail
: "${A733_SD_DEVICE:?Set the identified whole SD device}"
: "${A733_SD_IMAGE:?Set the selected copied .img.gz path}"
: "${A733_SD_GZIP_SHA256:?Set its compressed SHA-256 from the table}"
lsblk -b -o PATH,TYPE,SIZE,MODEL,SERIAL,MOUNTPOINT "$A733_SD_DEVICE"
a733_sd_type=$(lsblk -dn -o TYPE "$A733_SD_DEVICE")
a733_sd_bytes=$(sudo blockdev --getsize64 "$A733_SD_DEVICE")
a733_sd_mounts=$(lsblk -nr -o MOUNTPOINT "$A733_SD_DEVICE")
[[ "$a733_sd_type" == disk ]]
[[ "$a733_sd_bytes" == 1977614336 ]]
[[ -z ${a733_sd_mounts//[[:space:]]/} ]]
printf '%s  %s\n' "$A733_SD_GZIP_SHA256" "$A733_SD_IMAGE" | sha256sum -c -
gzip -dc -- "$A733_SD_IMAGE" |
  sudo dd of="$A733_SD_DEVICE" bs=4M iflag=fullblock conv=fsync status=progress
sync
```

This streams the 256 MiB image once with progress and flushing. Target readback
is optional troubleshooting. The separate full-card hash comparisons required
for release SD-write acceptance are not installation steps.

## Serial and SSH

Connect a **3.3 V TTL** serial adapter with TX/RX crossed and common ground;
leave adapter VCC disconnected. Use **115200 baud, 8N1, no flow control** and
record from power-on. Leave both heatsinks attached.

The recorded MAC is `10:f4:ae:90:5e:57`. The RAM image previously leased
`192.168.1.115`; the installed host leased `.116`. Check the actual DHCP lease
rather than assuming those addresses. On codex, an alternate address for this
same board can reuse the pinned host identity:

```sh
ssh -i /home/drew/.ssh/id_ed25519 -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=yes -o HostKeyAlias=192.168.1.116 root@ACTUAL-BOARD-IP
```

Expected host fingerprint is
`SHA256:Epfegy9GsJ6ng4/0EH3dZmir7beQj5uYTnIbE4JPfEI`.
Both requested public keys are present; RSA private-key login has not been
repeated by the coordinator on the corrected boot. The normal host's locked
password prevents password login at the serial getty; visible error text can
still be captured. The RAM recovery image supplies a serial root shell.
