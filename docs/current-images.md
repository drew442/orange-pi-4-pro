# Current images for this board

The board last ran **normalized A at 192.168.1.116 from SPI/NVMe with SD removed**,
with its original pinned SSH identity. Its last observed state is cleanly powered
off, awaiting the retained recovery card and power restoration. Last boot ID is
`5858af7a-977e-4a9b-971e-6d71ae7858d3`. SPI cold/warm boot, SSH/NTP and real
nonroot KVM CPU/disk/HTTP, clean shutdown, checkpoints and restart pass.
The retained test guest is stopped at checkpoint 4 with eight history records;
RAM accounts/units must be prepared again after boot. [Operational evidence](evidence/spi-operational-20261111.json).

The earlier full installation and independent deployment reproduction pass.
The earlier protected-SD route also passed ten warm/ten cold boots and complete
SD comparisons. The eight-hour workload criterion remains accepted from retained
longer-run data. Original guests/forensics and the compatible host cohort remain
preserved off-device. [Release record](release-acceptance.md).

The factory SPI candidate is
`out/spi-nvme-original-20261108/a733-spi-nvme.bin`, 16777216 bytes, SHA-256
`83edcd7f448e8353163666016a6bae3e9b70d041a5e4d8454f48931839205e90`.
Vendor calibration changes its 2 KiB parameter area during boot. The latest
private running-flash snapshot is
`out/spi-nvme-hardware-20261111-01/postwarm-spi.bin`, SHA-256
`e829cf7b08fa9753866df7de727b2d1b92bb5c525f3bbf4cd94315d528dc275a`.
Future SPI writes require a fresh reviewed plan accounting for that metadata.
Keep both original and calibrated backups. [SPI procedure](spi-sd-boot-20261111.md).

Paths below are relative to `/home/drew/orange-pi-4-pro` on codex.

## New uninstalled managed candidate

The [managed root candidate](managed-root-candidate-20261112.md) has matching
production constructions, independent complete filesystem/package checks and
whole historical/current reader compatibility. It remains unbooted and
unqualified on hardware. Its 4 GiB root components are for NVMe; keep the retained
2 GB recovery card unchanged. Fresh inactive-B deployment and actual managed KVM,
SPI recovery and final qualification remain pending.

## Existing installed release and recovery inputs

For this existing normalized release, use the authenticated
[normalized A/B update kit](normalized-maintenance-20261109.md) and the exact
[current normalized A rollback input](evidence/normalized-a-compatibility-custody-20261112.json).
Keep the [earlier pre-normalization pair](matched-host-updates-20261109.md) as
historical recovery evidence. The root-payload manifest differs from the whole-NVMe
installer manifest below. The changed-B
update, interruption, selection and healthy live rollback cases now pass physically. Failed-health/offline recovery now passes; final normalized full
installation and first A boot pass; restored KVM and independent reproduction also pass.
The separately retained [matched NVMe-first trial SD](matched-nvme-trial-sd-20261109.md)
remains an optional unbooted artifact. Direct NVMe loading and SPI operation
now have their own physical evidence. The trial image is separate from the
normal recovery bootstrap in the table.

| Purpose | Existing artifact |
| --- | --- |
| SSH RAM recovery, same board | `out/ssh-ram-installer-20261108/a733-ssh-installer.img.gz` |
| Retained protected host SD bootstrap | `out/serial-recovery-20261109/protected-sd-retained-01/a733-host-sd.img.gz` |
| Current protected NVMe BOOT component | `out/serial-recovery-20261109/protected-nvme-retained-01/boot.ext4.gz` |
| Normalized full NVMe distribution, installed and personalized on this board | `out/normalized-nvme-candidate-20261109/nvme.img.gz` |
| Historical full NVMe base, requiring the protected BOOT update | `out/vgic-nvme-image-20261108/retained-02/nvme.img.gz` |

The two SD artifacts each expand to **268,435,456 bytes**, fitting the actual
**1,977,614,336-byte** SU02G card, CID `035344535530324780716f216e00a8f5`.
The recovery artifact starts an independent RAM environment with SSH and no
automatic NVMe installation. It contains this board's private SSH host key;
keep it private and use it only for this board.

The normal host bootstrap selects STATE UUID
`360dfb39-f49f-487e-b307-0bb887c854c5` on the corrected NVMe. Earlier host SD
images selecting `00fd…`, including the November 6 deployment and `0bb7…` VGIC
trial, are incompatible with this new state. The independent RAM recovery image
works without selecting that installed state. SPI is programmed; future writes
require a fresh metadata-aware reviewed plan.

## Authenticate the selected download

Check the compressed source once on the flashing computer:

| Artifact | Compressed bytes | Compressed SHA-256 |
| --- | ---: | --- |
| SSH RAM recovery SD | 41,229,263 | `2f9cdd006e6d5c2c59e5ed1aba68f3ed7dd267a7f61b3672e590d926468c4eec` |
| Protected host SD | 17,273,154 | `450e30f0b8169da7471396dbc97d44185426c3da61cc83d8425acab74ccef18c` |
| Protected NVMe BOOT | 16,659,543 | `6a2725e53e3f2e89b7a796969df05af19f9778db301152495699fc0d9351e041` |
| Normalized full NVMe candidate | 545,581,074 | `3bc02f46d272dcac591d435fff2b1bc98d4fb048569fb2120746145929a8ad64` |
| Historical NVMe base | 545,632,496 | `3e560264bb64a9bc23b342febdff2146955ca97cb6e0279e5240c29d4e6d85bc` |

Expanded recovery SD SHA-256 is
`d3a02224efdb2c2eeccc7115d84cd6c4b2d75c7d06580b8f7812d12a546bc3f6`;
expanded host SD SHA-256 is
`ab5e8c2c846cbbd51b61bef6831d7e4d1498c29fefbf47d74301b1f3bb0ad207`.
The BOOT component expands to **536,870,912 bytes**, SHA-256
`cc2ad988fdc57579178766acaf09c71d688aaadec94b722593192cb24426c313`.
It is a p1-only maintenance payload, not a whole-drive image; use the
[reviewed offline updater](known-nvme-boot-writer.md), which has already applied
it to this board. The host roots, STATE and GUESTS were retained. The matched full candidate is
now retained above.
The normalized full candidate expands to **256,060,514,304 bytes**, SHA-256
`1fa9144d7a1269066c91a94bfb105a9ac4e4083d4707dd6791e8ac2f26902cd9`.
Its `manifest.json` (118,399 bytes, SHA-256
`3cbe28d6154d1bdeb8d364bd0f8bf82b61fd40af7be27fbd49695771c4fad08f`) and
`verification.json` are alongside the gzip. Two constructions and independent complete
artifact inspection pass. [Fresh installation](evidence/normalized-host-install-20261110.json)
and [hardware release acceptance](release-acceptance.md) also pass.

The historical NVMe base stream expands to **256,060,514,304 bytes**, SHA-256
`835031b81d0320d6f9f35e74b9be67b494006c26a25fa9070f3e0a8f434ebc11`.
Its producer manifest is retained at `out/vgic-nvme-image-20261108/build-02.stdout`
(35,023 bytes, SHA-256 `addfcafe239a2aa075435383cc4742869d256d98beecd2526806da206d98f4a2`).
Same-board identity was restored after installation and BOOT was subsequently
updated, so that historical unpersonalized stream does not describe the later installed state.
Use the selected normalized candidate above for the authorized fresh installation
and independent reproduction in the [deployment guide](deploy-protected-20261109.md).
The historical base is retained for provenance and recovery.

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
