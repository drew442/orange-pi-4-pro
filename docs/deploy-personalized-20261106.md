# Personalized NVMe deployment

The artifacts and guarded procedure passed software inspection and independent
review. Actual installation, host boot, SSH and KVM execution remain unperformed.
Capture the complete serial output so those hardware results can be assessed.

This installation is for the recorded **SPCC M.2 PCIe SSD**, serial
**134807190FC700149277**, **256,060,514,304 bytes**, logical/physical sectors
**512/512**. The user authorized discarding its existing data. That authorization
excludes another drive and SPI. The guarded writer rechecks identity, idle state
and image hashes; SPI is untouched.

This pre-release deployment must still prove actual host boot, SSH with both
keys, KVM guest execution/persistence and the remaining release gates. It uses
the immutable SD fallback while SPI recovery/boot work remains open.

## Two SD images, in this order

Both files are already staged on codex; each is 268,435,456 bytes and fits the
identified SU02G SD (1,977,614,336 bytes, CID
`035344535530324780716f216e00a8f5`). Use the same authorized physical card.

| When | Exact local image | SHA-256 |
| --- | --- | --- |
| First: installation RAM shell | `/home/drew/orange-pi-4-pro/out/installer-deployment-20261106/a733-installer-prep.img` | `2e16dea5e2941823bf8b40f1687ac417a67c91cecd87173b109fed23ca932499` |
| Only after successful NVMe write/readback: host bootstrap | `/home/drew/orange-pi-4-pro/out/host-deployment-20261106/a733-host-sd.img` | `2d22d84ada37a5b5d3696cc913caddcb31c0e681e65185005ea56ca0b8396329` |

If the card reader is on another computer, copy these exact image files there
and verify their raw hashes. Run the flashing checks where the reader is
attached, adjusting only `IMAGE` to the downloaded path. Device paths such as
`CARD` are local to that computer. The delivery endpoint remains on codex.

The first image has no SSH server and does not write NVMe automatically. The
second expects installed NVMe state UUID
`00fd936c-49fb-4fea-87c8-928b9c1599e0`; it cannot provide the host before installation.
The NVMe bundle is never written to SD.

## Flash the installer SD

Power the board off before removing the card. On the flashing computer, inspect
`lsblk -b -o PATH,TYPE,TRAN,RM,SIZE,MODEL,SERIAL,MOUNTPOINTS` and
`ls -l /dev/disk/by-id/`. Identify the same physical SU02G card/reader; USB readers
may show their own serial rather than the card CID. Select its whole-device
by-id path, not a partition or an example `/dev/sdX`. Unmount that card's exact
partition paths if listed. If identity is uncertain, stop before writing.

Set only `CARD` below to its actual authorized whole-card path, then run from a
Bash shell on codex. These commands overwrite only its first 256 MiB.

```bash
set -euo pipefail
CARD=/dev/disk/by-id/REPLACE_WITH_IDENTIFIED_WHOLE_SD
IMAGE=/home/drew/orange-pi-4-pro/out/installer-deployment-20261106/a733-installer-prep.img
printf '%s  %s\n' 2e16dea5e2941823bf8b40f1687ac417a67c91cecd87173b109fed23ca932499 "$IMAGE" | sha256sum -c -
lsblk -b -o PATH,TYPE,TRAN,RM,SIZE,MODEL,SERIAL,MOUNTPOINTS "$CARD"
test -b "$CARD"
test "$(lsblk -dn -o TYPE "$CARD")" = disk
test "$(sudo blockdev --getsize64 "$CARD")" -eq 1977614336
test -z "$(lsblk -nr -o MOUNTPOINTS "$CARD" | sed '/^[[:space:]]*$/d')"
sudo dd if="$IMAGE" of="$CARD" bs=4M conv=fsync status=progress
sudo blockdev --flushbufs "$CARD"
sudo head -c 268435456 "$CARD" | sha256sum
sudo sha256sum "$CARD"
```

The prefix readback must equal `2e16dea5...932499` in full. Save the full-card
hash too. Safely remove the card, insert it with board power off, leave Ethernet
connected, and capture serial from before power-on: existing 3.3 V TTL wiring,
115200 baud, 8N1, no flow control, adapter VCC disconnected.

## Acquire the authenticated compressed bundle

At the installer `/ #` prompt, first record:

```sh
uname -a
ls -l /dev/kvm
ip addr show eth0
ip route
cat /etc/resolv.conf
cat /proc/meminfo
/usr/bin/python3 -I -B /usr/lib/a733-installer/plan-nvme.py --target /dev/nvme0n1
```

Retain the report; model, serial, capacity and 512/512 sectors must match the
opening identity, with no busy indicators. Correct UTC before TLS: read current
`date -u` on codex, then use `date -u -s 'YYYY-MM-DD HH:MM:SS'` on the board with
that actual current value. The delivery certificate is valid from Nov 6
07:42:33 through Nov 20 07:42:33 UTC, 2026. If expired, request renewed pinned
instructions; do not bypass verification.

The bundle is **547077631 bytes compressed**, SHA-256
`4d1145c5ff8689c1e8ca847e39551eb36f54e703a2843f35fd0ce7ec7574b526`. Its complete expanded image is 256,060,514,304 bytes,
SHA-256 `74f3c6f9c66df8e125148b7a7d06ebc521d1c0fd5ae8e53de4a90c740e5224fc`.
Only the bounded compressed file is cached in a dedicated 1 GiB tmpfs; the large
expanded payload is streamed during verification/write and never staged in RAM
or on SD. Require at least 3 GiB MemAvailable, leaving at least 2 GiB beyond the
staging cap. The tool additionally requires encoded bytes plus 64 MiB free.

Paste this non-destructive acquisition block at a clean prompt, retaining its
output. It fails if the staging directory already exists; do not reuse partial
output blindly. The public certificate comes from these trusted instructions,
not an unverified endpoint download.

```sh
sh -eu <<'A733_ACQUIRE'
dmesg -n 3
A733_AVAILABLE_KIB=$(awk '/^MemAvailable:/{print $2}' /proc/meminfo)
test "$A733_AVAILABLE_KIB" -ge 3145728
test ! -e /run/a733-payload
mkdir -m 700 /run/a733-payload
mount -t tmpfs -o size=1G,mode=0700,nodev,nosuid,noexec tmpfs /run/a733-payload
cat > /run/a733-delivery-ca.pem <<'A733_CA'
-----BEGIN CERTIFICATE-----
MIIB0jCCAXmgAwIBAgIUcZ55TSJ2jXOIUzroT0uIMcS6C3IwCgYIKoZIzj0EAwIw
IzEhMB8GA1UEAwwYQTczMyBkZXBsb3ltZW50IDIwMjYxMTA2MB4XDTI2MTEwNjA3
NDIzM1oXDTI2MTEyMDA3NDIzM1owIzEhMB8GA1UEAwwYQTczMyBkZXBsb3ltZW50
IDIwMjYxMTA2MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEIz2l+bxw6F8Z0MNi
T2y6lHSQr5cxeZZncbnjVb96A0+u3BHDeeJVOZV/Zc8TQcAmxhxtNZt0KiKiOms/
TAKX0qOBijCBhzAdBgNVHQ4EFgQUP5vOvjSI0v6fa8OgSdS007nko5QwHwYDVR0j
BBgwFoAUP5vOvjSI0v6fa8OgSdS007nko5QwDwYDVR0RBAgwBocEwKgBhDAPBgNV
HRMBAf8EBTADAQH/MA4GA1UdDwEB/wQEAwIChDATBgNVHSUEDDAKBggrBgEFBQcD
ATAKBggqhkjOPQQDAgNHADBEAiA6J7WegEBiDD/jPBfj5/iIQKf3BZLUL7/bUClE
/BKw7QIgaDpR25bUGmY/J0x4RTeHgH9FJn63Vn4B15xYkuVlhDs=
-----END CERTIFICATE-----
A733_CA
printf '%s  %s\n' 4c71c2528ab650b2f9b8dc2a4554ed6248d77461abb7191bb58dd030a9eb9b56 /run/a733-delivery-ca.pem | sha256sum -c -
/usr/bin/python3 -I -B /usr/lib/a733-installer/acquire-nvme.py \
  --manifest-url https://192.168.1.132:8443/manifest.json \
  --image-url https://192.168.1.132:8443/nvme.img.gz \
  --manifest-sha256 bdd3c8f103e08fc1b759ca97e631ee20255d43aefe42abcce5361614a41b041e \
  --ca-file /run/a733-delivery-ca.pem \
  --storage-kind ram --output /run/a733-payload/download --max-seconds 7200
cat /run/a733-payload/download/acquisition.json
A733_ACQUIRE
```

Success must report the exact encoded/expanded hashes above. Verification reads
all expanded bytes; it can take many minutes. No target is written during this
step. On error, retain all output and stop; do not continue to apply.

## Prepare and inspect the exact-device plan

Run this read-only preparation; use this new installer, since older preparation
images lack the discard-policy and multiple-key support.

```sh
/usr/bin/python3 -I -B /usr/lib/a733-installer/install-nvme.py prepare \
  --image /run/a733-payload/download/nvme.img.gz \
  --manifest /run/a733-payload/download/manifest.json \
  --manifest-sha256 bdd3c8f103e08fc1b759ca97e631ee20255d43aefe42abcce5361614a41b041e \
  --target /dev/nvme0n1 --expected-model 'SPCC M.2 PCIe SSD' \
  --expected-serial 134807190FC700149277 --expected-bytes 256060514304 \
  --existing-data-policy discard-authorized > /run/a733-install-plan.json
A733_PREPARE_STATUS=$?
cat /run/a733-install-plan.json
printf 'prepare exit: %s\n' "$A733_PREPARE_STATUS"
```

Require exit status 0 and inspect the full plan. It must bind the exact known
NVMe, 512/512 sectors, `discard-authorized`, installation intent true, and these
ordered administration fingerprints:

```text
SHA256:WVU8wsBgfy5mGFNAZMdrvf+a3cTYo85B8llJlHIhUKA
SHA256:Dw9WPpqQ741skiDShdwAP3qXQtpgxgbUSlCp2hmod7k
```

The five GPT roles are boot, root-a, root-b, state and guests. Guest capacity is
238,342,373,376 bytes. State UUID is `00fd936c-49fb-4fea-87c8-928b9c1599e0`.
Do not mount target partitions or change network/source files between plan and
apply. The canonical namespace path is used because the RAM environment may
not create udev by-id links; exact serial/model/geometry checks remain mandatory.

## Apply the already authorized NVMe installation

This overwrites the entire recorded NVMe, including all old partitions/data.
Keep stable power and the serial capture running. Run directly in the serial
TTY; do not pipe confirmation input. The report alone is redirected to RAM.

```sh
/usr/bin/python3 -I -B /usr/lib/a733-installer/install-nvme.py apply \
  --image /run/a733-payload/download/nvme.img.gz \
  --manifest /run/a733-payload/download/manifest.json \
  --manifest-sha256 bdd3c8f103e08fc1b759ca97e631ee20255d43aefe42abcce5361614a41b041e \
  --target /dev/nvme0n1 --expected-model 'SPCC M.2 PCIe SSD' \
  --expected-serial 134807190FC700149277 --expected-bytes 256060514304 \
  --existing-data-policy discard-authorized --plan /run/a733-install-plan.json \
  > /run/a733-install-result.json
A733_APPLY_STATUS=$?
cat /run/a733-install-result.json
printf 'apply exit: %s\n' "$A733_APPLY_STATUS"
```

When prompted, type `DISCARD EXISTING DATA`, then
`ERASE 134807190FC700149277 256060514304 PLAN_SHA256`, substituting the exact
reviewed `plan_sha256` from the plan. No backup is claimed. The writer repeats
all checks, writes every byte including zeros, synchronizes and performs full
target readback before refreshing the partition table. Allow it to finish;
source authentication and target readback can take a long time.

Require `A733_APPLY_STATUS=0`, `success=true`, `bytes_written`/`bytes_verified` each
256,060,514,304, and `readback_sha256` equal to the expanded image hash above.
Any other result means stop and retain the log/report, even if some data was
written. Do not boot an unverified partial installation.

Print and save these off-device in the serial capture before RAM is lost:

```sh
cat /run/a733-payload/download/acquisition.json
cat /run/a733-install-plan.json
cat /run/a733-install-result.json
lsblk -b -o NAME,SIZE,MODEL,SERIAL,TYPE,FSTYPE,MOUNTPOINTS
cat /sys/class/block/mmcblk1/ro /sys/class/block/mmcblk1/stat
```

## Switch the same SD to the matching host bootstrap

Only after the successful full readback, run `poweroff -f`, wait for console
output to stop and remove board power. Remove the SD and hash the entire card
on codex before reflashing; compare it with the installer full-card baseline.
If the post-installer full-card hash differs from its baseline, stop, preserve
the card and both hashes, and return the complete log before any host reflash
overwrites that evidence. Matching hashes supply one boot's SD evidence, not
the final cycle/workload acceptance.

Select the same identified whole-card `CARD` again, and run in Bash on codex:

```bash
set -euo pipefail
CARD=/dev/disk/by-id/REPLACE_WITH_IDENTIFIED_WHOLE_SD
IMAGE=/home/drew/orange-pi-4-pro/out/host-deployment-20261106/a733-host-sd.img
printf '%s  %s\n' 2d22d84ada37a5b5d3696cc913caddcb31c0e681e65185005ea56ca0b8396329 "$IMAGE" | sha256sum -c -
lsblk -b -o PATH,TYPE,TRAN,RM,SIZE,MODEL,SERIAL,MOUNTPOINTS "$CARD"
test -b "$CARD"
test "$(lsblk -dn -o TYPE "$CARD")" = disk
test "$(sudo blockdev --getsize64 "$CARD")" -eq 1977614336
test -z "$(lsblk -nr -o MOUNTPOINTS "$CARD" | sed '/^[[:space:]]*$/d')"
sudo dd if="$IMAGE" of="$CARD" bs=4M conv=fsync status=progress
sudo blockdev --flushbufs "$CARD"
sudo head -c 268435456 "$CARD" | sha256sum
sudo sha256sum "$CARD"
```

Require the full prefix hash in the table and preserve the new full-card baseline.
Boot with Ethernet and NVMe connected; capture the entire serial output. The
expected host is `a733-host`, DHCP, SSH from any IPv4 source using either supplied
key. The supervisor prints `A733 SSH host key for ADDRESS: ED25519 SHA256:...`
before starting its listener. Compare first-connection host-key prompts against
that physical-console fingerprint; never substitute a test fingerprint or
silently disable host checking. Record the actual address from the console.

Test each supplied identity explicitly, so an SSH agent cannot silently reuse
the same key for both tests. On codex, select the private key whose public
fingerprint is `SHA256:Dw9WPpqQ741skiDShdwAP3qXQtpgxgbUSlCp2hmod7k`:

```sh
ssh -o IdentitiesOnly=yes -i /home/drew/.ssh/id_ed25519 root@ACTUAL_BOARD_IP
```

From BoganEdition, use its RSA private key with public fingerprint
`SHA256:WVU8wsBgfy5mGFNAZMdrvf+a3cTYo85B8llJlHIhUKA`:

```sh
ssh -o IdentitiesOnly=yes -i "/PATH/TO/BOGANEDITION_RSA_PRIVATE_KEY" root@ACTUAL_BOARD_IP
```

Substitute actual private-key paths locally and the serial-reported IPv4;
private keys stay on their originating computers. Compare the host fingerprint
against the serial log at each first prompt. After each login, capture
`id; uname -a; findmnt /; ls -l /dev/kvm`. From an authenticated session, retain
`journalctl -u a733-management-ssh -b --no-pager` so accepted-key fingerprints
can independently establish that both identities worked. Actual KVM guest proof
follows [the first KVM guest procedure](kvm-first-guest.md); SSH and `/dev/kvm` alone do not
satisfy that gate.

## Recovery and evidence to return

If host boot/SSH fails, retain the full serial capture. Power off and reflash
the same SD with the installer image and its guards above. The preparation boot
mounts no disk filesystems and starts no automatic writer; capture `lsblk`, the
read-only planner and kernel errors. Do not rerun apply as a recovery action:
it would erase state and guest data. Selection repair and interrupted-update
recovery require their own verified procedure; see [host recovery](host-recovery.md).
No SPI programming is involved.

Return the flashing prefix/full-card hashes, complete installer boot/acquisition/
plan/apply reports, complete host boot including its public SSH fingerprint,
and results of both SSH-key tests. Installation, SSH, real KVM guest execution,
10 cold/10 warm boots, workload, SD policy, rollback/recovery/backup and reproduced
deployment remain open until their actual evidence is recorded.
