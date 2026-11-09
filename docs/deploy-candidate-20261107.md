# Future matched second-candidate deployment

**Historical second-candidate procedure.** Its `feaa…` STATE and payloads are
not the current protected `360d…` candidate. Use the [current image map](current-images.md)
and [prepared protected deployment guide](deploy-protected-20261109.md).
Leave the active endurance workload undisturbed; no reinstallation is requested.
The commands below are retained as historical construction/rehearsal context.


The future target is **SPCC M.2 PCIe SSD**, serial **134807190FC700149277**, **256,060,514,304 bytes**, **512/512-byte logical/physical sectors**. Existing discard authorization persists for this exact drive. Revalidate identity and idle state immediately before writing. No backup is created. SPI programming is outside this procedure.

## Match and verify the retained files

Paths below are relative to `/home/drew/orange-pi-4-pro`. Keep these artifacts outside Git. Verify copies on whichever computer holds the SD reader; never write the NVMe payload to SD.

| Role and exact retained path | Bytes | SHA-256 |
| --- | ---: | --- |
| Installer: `out/installer-refresh-20261107/a733-installer-prep.img` | 268,435,456 | `265752f020212e73998a869e6098a1bc850a4d2ade4092a72ea3338e64157794` |
| Normal immutable fallback: `out/host-bootstrap-timesync-repeat-01-20261107/work/project/out/host-sd-timesync-repeat-01/a733-host-sd.img` | 268,435,456 | `64f029e319881c50ae6bc0dc0739f310e6476dec7fa4bac2e2d74c66565d5401` |
| Delivery manifest: `out/nvme-repeat-candidate-retained-20261107/manifest.json` | 10,481 | `c0f036dcc5d300a24785ebaac4c438bb0aef7cf1b8b073dc97a2d15a7896f563` |
| NVMe payload: `out/nvme-repeat-candidate-retained-20261107/nvme.img.gz` | 545,633,737 | `4b6cbe6eb16cc3d3cd236b0d8701908408d38e5993d5aca5ffb4882708766df0` |
| Optional later transport: `out/nvme-repeat-firmware-sd-20261107/work/project/out/host-sd-nvme-firmware-repeat-01/a733-host-sd.img.gz` | 17,272,072 | `17da6486bd6669b61959e7ae5b32f6cdd58177856b1cec1bc2211f24d617a2ae` |

The NVMe expands to 256,060,514,304 bytes, SHA-256 `d8f842f806c8ea4257b6efaf45d080b95e42a1f06bbde0a35f07d67c7416c7d9`. Both host SD variants require state UUID **`feaaee34-d782-4c18-bd04-1029540ab26a`**. Never combine them with deployed `00fd…` or first-candidate `fc8…` state. See [NVMe construction](production-nvme-repeat.md), [normal bootstrap](production-host-bootstrap-repeat.md), [installer refresh](installer-refresh-20261107.md) and [transport construction](nvme-repeat-firmware-sd.md).

Local file checks are non-destructive:

```bash
cd /home/drew/orange-pi-4-pro
sha256sum -c /dev/stdin <<'HASHES'
265752f020212e73998a869e6098a1bc850a4d2ade4092a72ea3338e64157794  out/installer-refresh-20261107/a733-installer-prep.img
64f029e319881c50ae6bc0dc0739f310e6476dec7fa4bac2e2d74c66565d5401  out/host-bootstrap-timesync-repeat-01-20261107/work/project/out/host-sd-timesync-repeat-01/a733-host-sd.img
c0f036dcc5d300a24785ebaac4c438bb0aef7cf1b8b073dc97a2d15a7896f563  out/nvme-repeat-candidate-retained-20261107/manifest.json
4b6cbe6eb16cc3d3cd236b0d8701908408d38e5993d5aca5ffb4882708766df0  out/nvme-repeat-candidate-retained-20261107/nvme.img.gz
HASHES
```

A [full-payload acquisition rehearsal](evidence/matched-deployment-preparation-20261107.json)
passes using these exact retained manifest/gzip bytes over loopback HTTPS into
native tmpfs. All four client source files match the refreshed installer recipe
pins. This is source-level acquisition evidence; it neither executes the ARM
installer nor proves board preparation, writing, boot or deployment reproduction.
The temporary test CA is not a deployment trust input.

## Future SD flashing, only at the selected deployment

Power off before card removal/insertion. Identify the actual SU02G card, **1,977,614,336 bytes**, CID `035344535530324780716f216e00a8f5`, using `lsblk -b -o PATH,TYPE,TRAN,RM,SIZE,MODEL,SERIAL,MOUNTPOINTS` and `ls -l /dev/disk/by-id/`. A reader can expose its own serial; resolve uncertainty before writing. Unmount the exact card partitions. Choose its whole-device by-id link, never a partition.

Define this guarded Bash function on the flashing computer. Each invocation displays the target and asks a fresh simple yes/no; it performs no write until answered. Set `IMAGE`/`IMAGE_SHA` first to the installer row. After successful NVMe installation, repeat with the normal fallback row and reidentify the same card.

```bash
set -euo pipefail
CARD=/dev/disk/by-id/REPLACE_WITH_IDENTIFIED_WHOLE_SD
flash_selected_sd() {
  case "$CARD" in /dev/disk/by-id/*) ;; *) return 1 ;; esac
  case "$CARD" in *REPLACE*|*-part[0-9]*) return 1 ;; esac
  printf '%s  %s\n' "$IMAGE_SHA" "$IMAGE" | sha256sum -c -
  test "$(stat -c %s "$IMAGE")" -eq 268435456
  lsblk -b -o PATH,TYPE,TRAN,RM,SIZE,MODEL,SERIAL,MOUNTPOINTS "$CARD"
  test -b "$CARD"
  test "$(lsblk -dn -o TYPE "$CARD")" = disk
  test "$(sudo blockdev --getsize64 "$CARD")" -eq 1977614336
  test -z "$(lsblk -nr -o MOUNTPOINTS "$CARD" | sed '/^[[:space:]]*$/d')"
  read -r -p "Overwrite the first 256 MiB of $CARD with $IMAGE? [y/N] " A733_REPLY
  case "$A733_REPLY" in y|Y|yes|YES) ;; *) return 1 ;; esac
  sudo dd if="$IMAGE" of="$CARD" bs=4M conv=fsync status=progress
  sudo blockdev --flushbufs "$CARD"
}
IMAGE=/home/drew/orange-pi-4-pro/out/installer-refresh-20261107/a733-installer-prep.img
IMAGE_SHA=265752f020212e73998a869e6098a1bc850a4d2ade4092a72ea3338e64157794
flash_selected_sd
```

Normal flashing requires no readback. For suspected corruption only, optionally hash the first 268,435,456 bytes and compare the selected raw hash. Keep full-card release SD-write measurements separate and collect them before reflashing. Boot the installer with Ethernet and NVMe connected; leave heatsinks attached. Capture serial before power-on: **3.3 V TTL, 115200, 8N1, no flow control, adapter VCC disconnected**. Its root RAM shell has no SSH and installs nothing automatically.

## Future authenticated acquisition and read-only preparation

The [dedicated LAN delivery endpoint](candidate-delivery-20261107.md) is **`https://192.168.1.132:8444`**. An actual full acquisition from the Ubuntu builder passes TLS and encoded-byte authentication. Port **8443** continues to serve the original `00fd…` deployment; do not substitute it. The existing IP-SAN CA is deliberately reused after hostname/time verification, with PEM SHA-256 **`4c71c2528ab650b2f9b8dc2a4554ed6248d77461abb7191bb58dd030a9eb9b56`**. It is valid from **2026-11-06 07:42:33 UTC through 2026-11-20 07:42:33 UTC**. After expiry, renew the certificate and trusted instructions; never bypass TLS. Correct the board clock using current trusted `date -u` if needed. The public CA below comes from these trusted instructions; downloading it from an endpoint cannot itself establish trust. No public redistribution clearance is claimed.

At the installer RAM shell, record `uname -a`, `ip addr show eth0`, `ip route`, `date -u`, and `/usr/bin/python3 -I -B /usr/lib/a733-installer/plan-nvme.py --target /dev/nvme0n1`. Require the exact opening drive identity, 512/512 sectors and no busy state. The following acquisition stages only the compressed payload in a dedicated 1 GiB tmpfs, requiring at least 3 GiB MemAvailable. It writes no target device; fresh output paths are mandatory.

```sh
sh -eu <<'A733_ACQUIRE'
MANIFEST_URL='https://192.168.1.132:8444/manifest.json'
IMAGE_URL='https://192.168.1.132:8444/nvme.img.gz'
CA_FILE='/run/a733-candidate-delivery-ca.pem'
CA_SHA='4c71c2528ab650b2f9b8dc2a4554ed6248d77461abb7191bb58dd030a9eb9b56'
cat > "$CA_FILE" <<'A733_CANDIDATE_CA'
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
A733_CANDIDATE_CA
printf '%s  %s\n' "$CA_SHA" "$CA_FILE" | sha256sum -c -
test "$(awk '/^MemAvailable:/{print $2}' /proc/meminfo)" -ge 3145728
test ! -e /run/a733-payload
mkdir -m 700 /run/a733-payload
mount -t tmpfs -o size=1G,mode=0700,nodev,nosuid,noexec tmpfs /run/a733-payload
/usr/bin/python3 -I -B /usr/lib/a733-installer/acquire-nvme.py \
  --manifest-url "$MANIFEST_URL" --image-url "$IMAGE_URL" \
  --manifest-sha256 c0f036dcc5d300a24785ebaac4c438bb0aef7cf1b8b073dc97a2d15a7896f563 \
  --ca-file "$CA_FILE" --storage-kind ram \
  --output /run/a733-payload/download --max-seconds 7200
cat /run/a733-payload/download/acquisition.json
A733_ACQUIRE
```

Require successful authenticated acquisition with the table's sizes/hashes and `decoded_verified=false`: this refreshed installer checks the expansion during writing, without a preliminary full decode. Stop on failure and retain output. Read-only preparation:

```sh
/usr/bin/python3 -I -B /usr/lib/a733-installer/install-nvme.py prepare \
  --image /run/a733-payload/download/nvme.img.gz \
  --manifest /run/a733-payload/download/manifest.json \
  --manifest-sha256 c0f036dcc5d300a24785ebaac4c438bb0aef7cf1b8b073dc97a2d15a7896f563 \
  --target /dev/nvme0n1 --expected-model 'SPCC M.2 PCIe SSD' \
  --expected-serial 134807190FC700149277 --expected-bytes 256060514304 \
  --existing-data-policy discard-authorized > /run/a733-install-plan.json
A733_PREPARE_STATUS=$?
cat /run/a733-install-plan.json
printf 'prepare exit: %s\n' "$A733_PREPARE_STATUS"
```

Require exit 0; inspect the complete plan, exact drive/geometry/idle state, discard policy, installation intent and ordered key fingerprints `SHA256:WVU8wsBgfy5mGFNAZMdrvf+a3cTYo85B8llJlHIhUKA`, `SHA256:Dw9WPpqQ741skiDShdwAP3qXQtpgxgbUSlCp2hmod7k`. GPT roles are boot/root-a/root-b/state/guests; guests capacity is 238,342,373,376 bytes. The plan binds the authenticated manifest hash but does not print its state UUID. Check that UUID in the downloaded manifest separately:

```sh
/usr/bin/python3 -I -B - <<'PY'
import json
with open('/run/a733-payload/download/manifest.json') as stream:
    manifest = json.load(stream)
assert manifest['state_uuid'] == 'feaaee34-d782-4c18-bd04-1029540ab26a'
print('Authenticated manifest state UUID:', manifest['state_uuid'])
PY
```

Do not mount target partitions or change inputs between prepare/apply.

## Future full NVMe write and normal host boot

This command erases the entire exact NVMe. Run directly in its serial TTY with stable power and capture; never pipe `yes` or confirmation input. The tool displays identity and asks **Erase and install? [y/N]**; answer `yes` only for the reviewed exact target. No typed serial or authorization phrase is required.

```sh
/usr/bin/python3 -I -B /usr/lib/a733-installer/install-nvme.py apply \
  --image /run/a733-payload/download/nvme.img.gz \
  --manifest /run/a733-payload/download/manifest.json \
  --manifest-sha256 c0f036dcc5d300a24785ebaac4c438bb0aef7cf1b8b073dc97a2d15a7896f563 \
  --target /dev/nvme0n1 --expected-model 'SPCC M.2 PCIe SSD' \
  --expected-serial 134807190FC700149277 --expected-bytes 256060514304 \
  --existing-data-policy discard-authorized --plan /run/a733-install-plan.json \
  > /run/a733-install-result.json
A733_APPLY_STATUS=$?
cat /run/a733-install-result.json
printf 'apply exit: %s\n' "$A733_APPLY_STATUS"
```

Progress remains visible on stderr. The writer reauthenticates compressed bytes before writing, then performs **one full expanded write**, validating expanded hash/size and gzip CRC. Default target readback is **off**. Require exit 0, `success=true`, `bytes_written=256060514304`, `readback_requested=false`, `bytes_verified=0`, `source_sha256=d8f842f806c8ea4257b6efaf45d080b95e42a1f06bbde0a35f07d67c7416c7d9` and no `readback_sha256`. Optional troubleshooting only: add `--verify-readback` to apply if deliberately requesting an additional complete target read; do not benchmark hardware to explain duration. On any error stop and retain reports; a partially written target is not bootable evidence.

Before RAM is lost, print acquisition, plan and result JSON plus `lsblk -b -o NAME,SIZE,MODEL,SERIAL,TYPE,FSTYPE,MOUNTPOINTS` into the off-device serial capture. Confirm those complete reports are saved off-device **before poweroff**. Then `poweroff -f`, wait for output to stop, remove power and switch the same identified card using the guarded function with:

```bash
IMAGE=/home/drew/orange-pi-4-pro/out/host-bootstrap-timesync-repeat-01-20261107/work/project/out/host-sd-timesync-repeat-01/a733-host-sd.img
IMAGE_SHA=64f029e319881c50ae6bc0dc0739f310e6476dec7fa4bac2e2d74c66565d5401
flash_selected_sd
```

Boot this **normal immutable fallback first**. Capture full serial, DHCP address, root slot A, state UUID `feaaee34-d782-4c18-bd04-1029540ab26a`, read-only root/SD policy and management logs. For SSH host-key trust and separate explicit `IdentitiesOnly=yes` tests with both supplied keys, follow [management verification](deploy-personalized-20261106.md#switch-the-same-sd-to-the-matching-host-bootstrap), using this boot's actual console fingerprint/address. Never disable host checking or infer two-key success from an agent choosing one identity.

## Later separate NVMe-first firmware test and acceptance

Only after this matched normal boot and management work, consider the [matching transport test](nvme-repeat-firmware-sd.md#physical-test-gate). Its raw file remains builder-only; obtain it there or expand the verified retained gzip into a separate regular file on a host with sufficient capacity, then verify raw length 268,435,456 and SHA-256 `e8a0a7a7b1edb3bb86cb485dfc6e83ed5e7c7c971caa031463e18faad9c16717` before using the same guarded SD flash. Preserve the normal fallback image for recovery. Capture `version`, firmware environment, NVMe enumeration/file reads, default NVMe boot and a separate manual `run bootcmd_mmc0` fallback. Do not use `saveenv`, SPI program/erase commands or any persistent firmware writes. This transport test establishes no SPI boot result.

Return artifact pins, actual endpoint/CA fingerprint (no private keys), full serial logs, acquisition/plan/result and exit statuses, live drive geometry, selected root/state identities, DHCP/time/management logs, and separate successful SSH results for both keys. Run [first actual KVM acceptance](kvm-first-guest.md), selecting its closed **`candidate-repeat-20261107`** profile: running KVM VM/vCPU descriptors, CPU/disk/network PASS, clean shutdown/restart and persisted counter. `/dev/kvm` and build checks are insufficient.

Physical repair, SSH/KVM, automatic time, independent recovery/off-device SPI backup, interrupted update/rollback, dirty-journal/missing-NVMe recovery, guest restore, 10 cold/10 warm boots, 24-hour endurance and zero routine SD writes remain required under [hardware validation](hardware-validation.md). Prove zero writes with full-card hashes, Linux counters and firmware audit; prefer eventual SPI-to-NVMe with SD removed only after its separate recovery and exact-target yes/no gates. Record independent deployment reproduction separately; no release acceptance is claimed here.
