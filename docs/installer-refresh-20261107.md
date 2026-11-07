# Refreshed installer SD

The refreshed installer includes the simpler yes/no confirmation, optional NVMe
readback and progress feedback. It was built on the prepared Ubuntu 22.04 host
from commit `151a532`. [Build evidence and independent review](evidence/installer-refresh-20261107.json)
cover packaged ARM behavior and all artifact bytes; this image has not booted on
the Orange Pi. It contains a physical root RAM shell and no SSH server.

**For the NVMe already installed on November 7, do not reinstall.** Continue the
[machine-id permission repair](installed-host-repair-20261107.md) and retry the
existing host SD. Either the existing installer or this refreshed installer can
provide the RAM shell for that repair; changing installer images is unnecessary
if the existing one is already available.

## Image and flashing

On codex:

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `/home/drew/orange-pi-4-pro/out/installer-refresh-20261107/a733-installer-prep.img` | 268,435,456 | `265752f020212e73998a869e6098a1bc850a4d2ade4092a72ea3338e64157794` |
| `/home/drew/orange-pi-4-pro/out/installer-refresh-20261107/a733-installer-prep.img.gz` | 39,270,669 | `7c0910a089644f23388d09b1f21ea0e1213069e5de5498bc285401cac47fbadb` |

The raw image fits the actual 1,977,614,336-byte SU02G card. Identify that same
whole SD device/reader using `lsblk -b -o PATH,TYPE,TRAN,RM,SIZE,MODEL,SERIAL,MOUNTPOINTS`
and `/dev/disk/by-id`; unmount its partitions before flashing. Set `CARD` below
to its actual whole-device by-id path on the flashing computer. Use Bash:

```bash
set -euo pipefail
CARD=/dev/disk/by-id/REPLACE_WITH_IDENTIFIED_WHOLE_SD
IMAGE=/home/drew/orange-pi-4-pro/out/installer-refresh-20261107/a733-installer-prep.img
printf '%s  %s\n' 265752f020212e73998a869e6098a1bc850a4d2ade4092a72ea3338e64157794 "$IMAGE" | sha256sum -c -
lsblk -b -o PATH,TYPE,TRAN,RM,SIZE,MODEL,SERIAL,MOUNTPOINTS "$CARD"
test -b "$CARD"
test "$(lsblk -dn -o TYPE "$CARD")" = disk
test "$(sudo blockdev --getsize64 "$CARD")" -eq 1977614336
test -z "$(lsblk -nr -o MOUNTPOINTS "$CARD" | sed '/^[[:space:]]*$/d')"
read -r -p 'Write this installer to the displayed SD card? [y/N] ' A733_FLASH_REPLY
case "$A733_FLASH_REPLY" in y|Y|yes|YES) ;; *) exit 1 ;; esac
sudo dd if="$IMAGE" of="$CARD" bs=4M conv=fsync status=progress
```

No SD readback is required for normal installation. If troubleshooting suspected
corruption, optionally read back the first 268,435,456 bytes and compare their
hash with the raw image hash. Full-card hashes/counters remain part of the
separate [release SD-write validation](hardware-validation.md).

Power off the board before inserting/removing SD. Capture serial from before
power-on: existing **3.3 V TTL**, **115200 baud, 8N1, no flow control**, adapter
VCC disconnected. Leave the heatsinks in place and Ethernet connected. The image
does not automatically install anything or program SPI.

## Future full installation with this image

The existing [acquisition block](deploy-personalized-20261106.md#acquire-the-authenticated-compressed-bundle)
and [prepare command](deploy-personalized-20261106.md#prepare-and-inspect-the-exact-device-plan)
use unchanged arguments. With this refreshed image, acquisition authenticates
compressed bytes while downloading and reports `decoded_verified=false`;
preparation does not decompress the payload. Expanded size/hash and gzip CRC
are checked during the write itself. Current source requires a trusted manifest
and authenticates compressed bytes again before the first target write.

For an intentional full reinstall, use the existing [apply command](deploy-personalized-20261106.md#apply-the-already-authorized-nvme-installation).
The tool displays the target path, model, serial, size and sector sizes, then
asks **Erase and install? [y/N]**. Answer `yes` only for the recorded target and
intended operation. No typed serial, plan hash or authorization phrase is needed.
There is no automatic-confirmation flag. Existing data is discarded; the tool
creates no backup.

Normal success reports `bytes_written=256060514304`, `success=true`,
`readback_requested=false`, `bytes_verified=0` and the hash of the expanded stream
written. It omits `readback_sha256`. Add **`--verify-readback`** to `apply` only
when you want a full target integrity check, such as troubleshooting suspected
storage corruption. That mode reports actual verified bytes and hash. Progress
appears on stderr during download/authentication/write/readback; flushing has
an explicit status message. No new hardware timing or performance result is
claimed.

A malformed authenticated payload can fail during writing and leave a partial
target. A nonzero exit or `success=false` means retain the report and stay in
recovery; do not infer a complete installation from the progress counter.

**The November 6 NVMe bundle still contains the old machine-id permission.**
After a successful new full installation of that bundle, run the same
[permission repair block](installed-host-repair-20261107.md) in the RAM shell
before powering off and switching to the matching host SD. This changes one
NVMe file's permission; it does not repeat the installation or read back the disk.
Newly built state images use the corrected mode, but a refreshed complete NVMe
bundle is still pending. The host SD, state UUID and both authorized SSH keys
are unchanged. Physical SSH and real KVM guest execution remain acceptance gates.
