# Deploy the normalized image on the recorded A733

**Prepared procedure; fresh installation and independent physical reproduction
are unperformed. Do not run while the November 9 endurance workload is active.**
Use [current images](current-images.md) rather than the older November 6/7
payloads or delivery endpoints. The selected normalized distribution now passes
paired independent full-stream verification; physical installation is unperformed.
This procedure destroys the entire recorded
NVMe, including STATE and guests. The operator's [known storage authorization](evidence/storage-write-authorization-20261108.json)
persists. The installer displays its exact target and asks one yes/no question.
SPI is untouched; preferred SPI boot and its recovery prerequisites remain open.

## Inputs and admission

The target is `/dev/nvme0n1`, **SPCC M.2 PCIe SSD**, serial
`134807190FC700149277`, **256060514304 bytes**, 512/512-byte sectors. The known
SD is SU02G CID `035344535530324780716f216e00a8f5`, **1977614336 bytes**.
Stop if either identity differs; these are same-board instructions.

| Input on codex, under `/home/drew/orange-pi-4-pro` | SHA-256 |
| --- | --- |
| `out/normalized-nvme-candidate-20261109/nvme.img.gz` | `3bc02f46d272dcac591d435fff2b1bc98d4fb048569fb2120746145929a8ad64` |
| Adjacent `manifest.json` | `3cbe28d6154d1bdeb8d364bd0f8bf82b61fd40af7be27fbd49695771c4fad08f` |
| `scripts/personalize-known-host-state.py` | `7a8ba7c36102ac5c72e6f7efa961d2f713bf50f0d2d6c0806ae8d7e2c140a2ac` |
| `scripts/write-known-bootstrap-sd.py` | `4769001ab4a4861ff8ffe26ad6aa427f9abe5430b48bb49918d6c1206b96c52a` |

The gzip is **545581074 bytes**; its single expansion is **256060514304 bytes**,
SHA-256 `1fa9144d7a1269066c91a94bfb105a9ac4e4083d4707dd6791e8ac2f26902cd9`.
The 118399-byte manifest binds STATE UUID
`360dfb39-f49f-487e-b307-0bb887c854c5`. Two constructions and complete independent
[artifact comparison](evidence/normalized-full-nvme-image-20261109.json) pass.
This distribution contains no copy of the board's installed private identity.

Before leaving the installed host, retain and review the endurance outcome,
observer/CPU exits, full UART capture and post-workload SD measurements off-board.
The [automatic preserver](endurance-run-20261109.md) does not grant acceptance.
Retain the authenticated [counter-3 guest backup](evidence/corrected-host-guest-restore-20261108.json)
and its indexes/manifests. Also take fresh backups of current guests, including
the counter-5 state, plus completed/failed endurance evidence; counter-3 does not
contain later data. A full reinstall erases the retained failed-run guest directories.
Complete the [changed inactive-slot interruption and rollback cases](normalized-maintenance-20261109.md)
first, preserving their evidence. Use [offline STATE/root preservation](host-state-backup.md) for the paired host
backup; its execution and a guarded raw STATE restore remain unproven. Keep
private host identity separately. No preservation of
unrelated original user data is claimed or required by the discard authorization.

The original same-board identity archive is root-private on the builder:
`/var/lib/a733-original-host-identity-20261108-01/identity.tar`, **10240 bytes**,
SHA-256 `ae3930cec481912a164361e11c926ca02d81561a49c0fc49b70514dd19d3d06e`.
Transfer it through authenticated SSH into private mode-0700 staging; keep the
file mode 0600. Never print its contents or place it in Git or a delivery server.
Use it only on this board; [identity restoration](known-host-state-personalization.md)
explains the three intended file replacements.

## Enter the existing private RAM recovery environment

After evidence retention, the coordinator can use the reviewed
[known SD writer](known-bootstrap-sd-writer.md) from the quiescent installed host
with `--profile ssh-ram-recovery-20261108`, the private recovery raw image and
that installed session's verified boot UUID. Run its read-only plan, then the
preauthorized progress-enabled write; retain the successful receipt off-board
before a clean reboot. Source staging requires sufficient private `/run` tmpfs
capacity. The historical November 8 one-off writer is unnecessary.

If remote maintenance is unavailable, power off and flash the **SSH RAM recovery**
SD from [current images](current-images.md), using that page's exact-card
identification, source verification and progress-enabled write procedure.
Capture serial at 115200 8N1. The host bootstrap SD is a different image.

Boot recovery and identify its actual DHCP address through the lease/serial log.
Use strict SSH host checking and `HostKeyAlias=192.168.1.116`, with the existing
key and pinned fingerprint `SHA256:Epfegy9GsJ6ng4/0EH3dZmir7beQj5uYTnIbE4JPfEI`.
Do not assume `.115` or the installed host's `.116` address.
All following board commands are in this RAM recovery root shell.

```sh
set -eu
awk '$1 == "rootfs" && $2 == "/" && $3 == "rootfs" {ok=1} END {exit !ok}' /proc/mounts
chmod 0755 /
awk '$2 == "/run" && $3 == "tmpfs" {ok=1} END {exit !ok}' /proc/mounts
mount -o remount,size=2G /run
awk '/^MemAvailable:/{ok=($2 >= 3145728)} END {exit !ok}' /proc/meminfo
umask 077
test ! -e /run/a733-payload
mkdir -m 700 /run/a733-payload
cat /proc/sys/kernel/random/boot_id
```

Enlarge the existing `/run`; mounting another filesystem over it hides SSH
runtime directories. Confirm at least 3 GiB MemAvailable, no swap and unused
NVMe before staging. Record the intended RAM boot UUID off-board and set
`A733_RAM_BOOT_ID` to that exact value in the following session. A UUID observed
on an unexpected host is not a valid identity check.

The RAM image's authenticated [source manifest](evidence/ssh-ram-installer-20261108.json)
records these installed tool pins. Verify them on the board before using them:

```sh
sha256sum -c - <<'PINS'
6f356eb05ea891981966c1d08b7174fe331823da3cd118971cfd5b2c37110b12  /usr/lib/a733-installer/install-nvme.py
8ed9075de6ab4425457aa3f309847f2e66b134691f0fe80ac5eb7c43f1746e31  /usr/lib/a733-installer/plan-nvme.py
742a71575d94c9be29e01af3f50dc0d0639b38800c3e5a86ded49ac8906e518f  /usr/lib/a733-installer/nvme_layout.py
PINS
```

## Stage authenticated inputs in RAM

Run this Bash block on codex after the RAM preparation above. Set
`A733_RAM_IP` to the verified recovery address and `A733_DEPLOY_EVIDENCE` to a
fresh absolute directory below this repository's ignored `out/`. The SSH image
does not include scp/SFTP; transfers use authenticated SSH standard input.

```bash
set -euo pipefail
cd /home/drew/orange-pi-4-pro
: "${A733_RAM_IP:?Set the verified RAM recovery IPv4 address}"
: "${A733_DEPLOY_EVIDENCE:?Set a fresh private absolute evidence directory under out}"
case "$A733_DEPLOY_EVIDENCE" in "$PWD"/out/*) ;; *) exit 1 ;; esac
umask 077
test ! -e "$A733_DEPLOY_EVIDENCE"
mkdir -m 700 "$A733_DEPLOY_EVIDENCE"
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes drew@build.drewnet.online \
  'sudo -n cat /var/lib/a733-original-host-identity-20261108-01/identity.tar' \
  > "$A733_DEPLOY_EVIDENCE/identity.tar"
printf '%s  %s\n' ae3930cec481912a164361e11c926ca02d81561a49c0fc49b70514dd19d3d06e \
  "$A733_DEPLOY_EVIDENCE/identity.tar" | sha256sum -c -
A733_RAM_SSH=(ssh -T -i /home/drew/.ssh/id_ed25519 -o IdentitiesOnly=yes \
  -o BatchMode=yes -o StrictHostKeyChecking=yes -o HostKeyAlias=192.168.1.116 \
  -o ConnectTimeout=5 "root@$A733_RAM_IP")
a733_send_file() {
  "${A733_RAM_SSH[@]}" "umask 077; set -C; cat > /run/a733-payload/$2" < "$1"
}
a733_send_file out/normalized-nvme-candidate-20261109/nvme.img.gz nvme.img.gz
a733_send_file out/normalized-nvme-candidate-20261109/manifest.json manifest.json
a733_send_file scripts/personalize-known-host-state.py personalize-known-host-state.py
a733_send_file scripts/write-known-bootstrap-sd.py write-known-bootstrap-sd.py
a733_send_file "$A733_DEPLOY_EVIDENCE/identity.tar" identity.tar
gzip -dc out/serial-recovery-20261109/protected-sd-retained-01/a733-host-sd.img.gz |
  "${A733_RAM_SSH[@]}" 'umask 077; set -C; cat > /run/a733-payload/a733-host-sd.img'
```

There is no 256 GB raw staging file or extra raw SD allocation on codex.
The private identity archive stays in this restricted staging directory;
publish only nonsecret receipts. Keep this shell's SSH array for later receipt
retention, but use a separate interactive terminal for installation.

Require root:root ownership, mode 0600 and one link for each staged input,
and 0700 for the directory. Verify manifest/helper/archive hashes against the
trusted values above; let installer `prepare` authenticate the complete NVMe
encoded image. The SD writer authenticates its fixed raw image itself. On the board:

```sh
sha256sum -c - <<'PINS'
3cbe28d6154d1bdeb8d364bd0f8bf82b61fd40af7be27fbd49695771c4fad08f  /run/a733-payload/manifest.json
7a8ba7c36102ac5c72e6f7efa961d2f713bf50f0d2d6c0806ae8d7e2c140a2ac  /run/a733-payload/personalize-known-host-state.py
4769001ab4a4861ff8ffe26ad6aa427f9abe5430b48bb49918d6c1206b96c52a  /run/a733-payload/write-known-bootstrap-sd.py
ae3930cec481912a164361e11c926ca02d81561a49c0fc49b70514dd19d3d06e  /run/a733-payload/identity.tar
PINS
```

Never substitute downloaded pins for these trusted values. Retain transport errors
and stop on failed transfer. Staging here requires no temporary HTTPS server,
new CA certificate or certificate-verification exception.

## Prepare, then install the exact NVMe

Use this same argument set for both operations. `prepare` is read-only and
checks target identity, geometry, busy state, manifest and encoded source bytes.
Do not mount target partitions or change inputs between operations.

```sh
set -- --image /run/a733-payload/nvme.img.gz \
  --manifest /run/a733-payload/manifest.json \
  --manifest-sha256 3cbe28d6154d1bdeb8d364bd0f8bf82b61fd40af7be27fbd49695771c4fad08f \
  --target /dev/nvme0n1 --expected-model 'SPCC M.2 PCIe SSD' \
  --expected-serial 134807190FC700149277 --expected-bytes 256060514304 \
  --existing-data-policy discard-authorized
python3 -I -B /usr/lib/a733-installer/install-nvme.py prepare "$@" \
  > /run/a733-payload/install-plan.json
cat /run/a733-payload/install-plan.json
```

Require exit 0 and inspect all five roles: BOOT, ROOT-A, ROOT-B, STATE, GUESTS.
Apply from an actual serial terminal or `ssh -t` session, keeping stderr attached
to that terminal so progress and the one **Erase and install? [y/N]** question
remain visible:

```sh
python3 -I -B /usr/lib/a733-installer/install-nvme.py apply "$@" \
  --plan /run/a733-payload/install-plan.json \
  > /run/a733-payload/install-result.json
cat /run/a733-payload/install-result.json
```

The source is decoded/hash-checked while writing; no preliminary full 256 GB
decode or installation readback is requested. Progress covers source
authentication, writes and flushing. Expect `success=true`,
`bytes_written=256060514304`, `bytes_verified=0`, `readback_requested=false`, and
exit 0. Optional `--verify-readback` is only for suspected installation integrity
problems; add it to `apply` only when that extra full-drive read is wanted.
After any error, inspect/preserve the exact receipt before another write.

## Restore identity, write host SD, then preserve reports

Do not reboot until both identity restoration and host SD writing complete.
Use the intended RAM boot UUID recorded earlier:

```sh
: "${A733_RAM_BOOT_ID:?Set the independently identified RAM boot UUID}"
python3 -I -B /run/a733-payload/personalize-known-host-state.py \
  --expected-boot-id "$A733_RAM_BOOT_ID" --source-archive /run/a733-payload/identity.tar \
  > /run/a733-payload/identity-plan.json
cat /run/a733-payload/identity-plan.json
python3 -I -B /run/a733-payload/personalize-known-host-state.py \
  --expected-boot-id "$A733_RAM_BOOT_ID" --source-archive /run/a733-payload/identity.tar \
  --apply > /run/a733-payload/identity-result.json
cat /run/a733-payload/identity-result.json
```

Require exit 0, `completed=true`, `files_fsynced=true`, `unmounted=true` and the
pinned host fingerprint. Failure may leave a partial identity or mounted STATE;
resolve it before proceeding. Personalization changes the full-device hash, so
the distribution raw hash is a pre-personalization record only.

Use the [known SD writer](known-bootstrap-sd-writer.md) with the same boot UUID,
explicitly selecting `--profile protected-state-20261109`, first its read-only
invocation, then `--write --report
/run/a733-payload/sd-write-result.json`. It writes only the first 268435456 bytes
of the exact card with progress, fsync and flush; raw SHA-256 is
`ab5e8c2c846cbbd51b61bef6831d7e4d1498c29fefbf47d74301b1f3bb0ad207`.
It performs no readback and never selects NVMe or SPI.

Copy plans/results, serial capture and relevant error output to a fresh private
off-board evidence directory before reboot. Fsync retained evidence there.
The RAM copy disappears on poweroff. Do not copy private identity material into
the public evidence directory. After successful retention, sync and request a
clean reboot. The firmware should load protected host files from SD and use the
read-only NVMe root; this does not exercise U-Boot NVMe file loading.

## Physical result and independent reproduction

Record the actual new boot ID, root/state identities, 40-file installed tool
closure, DHCP/address, host fingerprint, SSH login, automatic NTP and SD counters.
Restore the authenticated guest backup using [guest restoration](guest-restoration.md)
and demonstrate actual KVM execution/checkpoint/restart on the fresh installation.
Reconcile final firmware/SSD identity with the qualifying eight-hour workload data and boot-cycle records;
count no accidental restart as a measured cycle. Full SD comparisons, firmware
write audit, cold/warm cycles, interrupted update, rollback, missing NVMe and
SPI recovery remain [physical acceptance gates](hardware-validation.md).

A second agent/operator must reproduce the procedure from a clean environment
and retain discrepancies plus the complete physical evidence. Source review,
manifest acceptance and fixtures alone cannot satisfy that requirement.
