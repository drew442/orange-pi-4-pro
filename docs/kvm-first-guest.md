# First physical KVM guest for a matched installed profile

This prospective procedure has **not been run on the board**. The current
board action remains the [existing-install machine-id repair](installed-host-repair-20261107.md),
then booting the same deployed host SD and verifying trusted SSH with **both**
authorized keys. Do not reinstall to repair management. The current installation
uses `installed-20261106`; the later `candidate-repeat-20261107` profile is only
for a completed [matched second-candidate deployment](deploy-candidate-20261107.md)
with its own successful management checks.

Select exactly one profile below, then use the common runtime, activation,
enrollment and KVM proof sequence. Profiles bind complete tool closures (39
installed files for the original deployment, 40 for the second candidate),
package state, state/p5 identities and retained recovery artifacts. Do not mix
profiles or override individual UUIDs, hashes or tool counts. This changes no
installed runtime or image. The coordinator retains hardware acceptance;
one guest and its restart establish neither release acceptance nor SD-write
endurance.

Stop on any failed command, unexpected role, hash, account, mount or receipt.
Do not substitute an older root/tool package, TCG, root QEMU, RAM/SD payload
storage, another block device, or a physical disk as the guest disk. There is
no automatic guest activation/account/unit boot integration. The manual unit
below lives in RAM and must be reviewed again after a host reboot.

## Select and authenticate the closed profile before touching NVMe

On codex, use Bash from `/home/drew/orange-pi-4-pro`. The native preparation
helper accepts only the two named profiles and authenticates their pinned local
source records. It does not accept an arbitrary profile environment, UUID or
pin. Keep the generated files outside Git. Its absolute output path must be
new and directly under this checkout’s `out/`; a preexisting output directory is a stop. Preserve its printed hashes in
the off-device operator record. These commands do not rebuild or repin artifacts.

```bash
set -euo pipefail
umask 077
cd /home/drew/orange-pi-4-pro
PROFILE_OUT=/home/drew/orange-pi-4-pro/out/first-kvm-installed-20261106
python3 scripts/prepare-first-kvm.py --profile installed-20261106 --output "$PROFILE_OUT"
```

For a later, already deployed matching second candidate, replace those last two
lines with this selection; never run both in one shell:

```bash
PROFILE_OUT=/home/drew/orange-pi-4-pro/out/first-kvm-candidate-repeat-20261107
python3 scripts/prepare-first-kvm.py --profile candidate-repeat-20261107 --output "$PROFILE_OUT"
```

Inspect `profile.json` and retain it with the printed native hashes. Verify the
generated checklist, then source only this freshly generated `profile.env`;
its fixed values are exported and read-only. Keep this native Bash shell for
all later codex commands so `PROFILE_OUT`, `E` and the selected pins persist.

```bash
(cd "$PROFILE_OUT" && sha256sum -c SHA256SUMS)
cat "$PROFILE_OUT/profile.json"
. "$PROFILE_OUT/profile.env"
PROFILE_SUMS_SHA=$(sha256sum "$PROFILE_OUT/SHA256SUMS" | cut -d ' ' -f 1)
export PROFILE_OUT PROFILE_SUMS_SHA
(cd out/guest-fixture-seed-20261104 && sha256sum -c /dev/stdin <<'HASHES'
1294959bcaa5a5ad3791913e33cffb8f0893708396aac8be9837c03a0e1f9377  Image
2f3360a8f6365b84c352cae99949a5ae92a81001efe28dbae221f334805f854b  initramfs.cpio.gz
ff595fac0b9a7d990f099edc0dd0f9d4859fb9942e185d2f2377088ce32bd577  guest-disk.raw
HASHES
)
```

Set `BOARD_IP` to the installed host's actual address. Compare the first SSH
host-key prompt with the physical serial-console `A733 SSH host key` fingerprint
before accepting it. Do not use `ssh-keyscan` as independent authentication or
disable checking. The custom sshd has no SFTP subsystem: transfers require
`scp -O`. Create a fresh private **RAM** profile directory, transfer the generated
records over this authenticated connection, and verify the transferred checksum
file against its native hash before any NVMe evidence-directory creation.

```bash
BOARD_IP=192.168.1.115   # Replace if the installed host has another address.
ssh -i /home/drew/.ssh/id_ed25519 -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=ask root@"$BOARD_IP" \
  'umask 077; test ! -e /run/first-kvm-profile && mkdir -m 0700 /run/first-kvm-profile'
scp -O -i /home/drew/.ssh/id_ed25519 -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=yes "$PROFILE_OUT/profile.env" \
  "$PROFILE_OUT/profile.json" "$PROFILE_OUT/installed-tools.sha256" \
  "$PROFILE_OUT/recovery-bundle.sha256" "$PROFILE_OUT/SHA256SUMS" root@"$BOARD_IP":/run/first-kvm-profile/
ssh -i /home/drew/.ssh/id_ed25519 -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=yes root@"$BOARD_IP" \
  "cd /run/first-kvm-profile && printf '%s  SHA256SUMS\n' '$PROFILE_SUMS_SHA' | sha256sum -c - && sha256sum -c SHA256SUMS && chmod 0400 ./*"
ssh -i /home/drew/.ssh/id_ed25519 -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=yes root@"$BOARD_IP"
```

In that board root Bash shell, source only the just-authenticated generated
profile. Verify its read-only declarations, complete installed tool closure,
package pin, state marker and runtime guard **before** creating `E` on NVMe.
The guard checks selected NVMe roles, kernel ABI, RAM `/etc` overlay, state binds
and all eight immutable package-state views. It must pass for the selected
profile; the original deployment's ABI/tool guard remains mandatory.

```bash
set -euo pipefail
umask 077
set -C
cd /run/first-kvm-profile
sha256sum -c SHA256SUMS
. ./profile.env
declare -p PROFILE STATE_UUID GUEST_UUID PACKAGE_STATE_SHA TOOL_COUNT E
[[ $(wc -l < installed-tools.sha256) -eq "$TOOL_COUNT" ]]
sha256sum -c installed-tools.sha256 | tee /run/first-kvm-installed-tools-check.txt
printf '%s  %s\n' "$PACKAGE_STATE_SHA" \
  /usr/lib/orangepi-hypervisor/package-state/manifest.json | sha256sum -c -
/usr/bin/python3 -I -B - <<'PYGUARD'
import importlib.util, os
from pathlib import Path
assert Path('/run/a733-state/.a733-state').read_bytes() == (
    'a733-state-v1\n' + os.environ['STATE_UUID'] + '\n').encode('ascii')
s = importlib.util.spec_from_file_location('first_guest_management', '/usr/lib/a733-tools/configs/host-management.py')
m = importlib.util.module_from_spec(s)
s.loader.exec_module(m)
print(m.runtime_guard())
PYGUARD
findmnt -T /
findmnt -T /root
findmnt -T /run/a733-state
cat /run/a733-state/.a733-state
cat /usr/lib/orangepi-hypervisor/payload.json
```

Require read-only NVMe p2 (A) or p3 (B), with root UUID
`a7330000-0000-4000-8000-00000000000a` or `...000b`; p4 must be the same
namespace's writable state, with the selected `STATE_UUID`. `/root` must bind
state `/root`. Verify the declarations above carry Bash's read-only and export
attributes (`-rx`). No other storage role is accepted. Keep this board shell
for all later board commands. A preexisting evidence directory is a stop,
not permission to reuse prior evidence.

```bash
mkdir -m 0700 "$E"
T=/usr/lib/a733-tools
export T
cp /run/first-kvm-profile/{profile.env,profile.json,installed-tools.sha256,recovery-bundle.sha256,SHA256SUMS} "$E/"
cp /run/first-kvm-installed-tools-check.txt "$E/installed-tools-check.txt"
uname -a | tee "$E/host-uname.txt"
cat /proc/sys/kernel/random/boot_id | tee "$E/host-boot-id.txt"
cat /proc/cmdline | tee "$E/host-cmdline.txt"
findmnt --real | tee "$E/host-mounts.txt"
```

## Activate the exact p5 and locked identity

The selected closed profile supplies the exact p5 UUID in `GUEST_UUID`. Both
layouts have p5 size 238,342,373,376 bytes,
start LBA 34,605,056 and end LBA 500,117,503. The whole NVMe is
`SPCC M.2 PCIe SSD`, serial `134807190FC700149277`, 256,060,514,304 bytes,
512-byte logical and physical sectors. Derive its pathname from the guarded
running root, rather than choosing the first enumerated disk.

```bash
ROOT_SOURCE=$(findmnt -n -o SOURCE --target /)
case "$ROOT_SOURCE" in
  /dev/nvme[0-9]*n[0-9]*p2|/dev/nvme[0-9]*n[0-9]*p3) ;;
  *) printf '%s\n' 'STOP: unknown running-root role' >&2; exit 1 ;;
esac
NVME=${ROOT_SOURCE%p[23]}
mount_args=(--target "$NVME" --expected-model 'SPCC M.2 PCIe SSD' \
  --expected-serial 134807190FC700149277 --expected-bytes 256060514304 \
  --guest-uuid "$GUEST_UUID")
/usr/bin/python3 -I -B "$T/scripts/activate-guest-storage.py" prepare \
  "${mount_args[@]}" > /run/first-kvm-mount-plan.json
chmod 0400 /run/first-kvm-mount-plan.json
cat /run/first-kvm-mount-plan.json
```

Review every field, including exact GPT roles, selected namespace and p5 UUID.
This first-install procedure requires `mounted_before=false` and a freshly
installed, never-used p5. Before the first mount, establish the backup scope
truthfully: retain the **verified full compressed NVMe installation bundle on
another machine**, with its exact encoded and expanded byte counts/SHA-256
recorded in the installation evidence and complete-stream verification passed.
The generated `profile.json` records the selected bundle's exact paths,
encoded/expanded byte counts and hashes, plus the authenticated source and
complete-stream verification provenance. `recovery-bundle.sha256` pins only that
profile's retained gzip and manifest. For the current installation these are
the original personalized delivery artifacts; for the second candidate they
are the [retained repeat artifacts](production-nvme-repeat.md) used by its
[matched deployment](deploy-candidate-20261107.md). This is off-device artifact
verification, not a physical installation result. Do not rerun the 256 GB
expansion merely to repeat the already recorded complete-stream check.

Before apply, confirm both retained encoded sizes and hashes on codex using
the generated closed recovery checklist:

```bash
(cd "$RECOVERY_DIR" &&
  [[ $(stat -c %s nvme.img.gz) == "$RECOVERY_WIRE_BYTES" ]] &&
  [[ $(stat -c %s manifest.json) == "$RECOVERY_MANIFEST_BYTES" ]] &&
  sha256sum -c "$PROFILE_OUT/recovery-bundle.sha256"
)
printf 'expanded bytes=%s sha256=%s\n' "$RECOVERY_RAW_BYTES" "$RECOVERY_RAW_SHA"
```

That exact bundle contains the pristine empty p5 built by `build/nvme-image.py`
from an empty `guest-seed` directory. It is the recovery copy for the empty
guest filesystem at risk during this activation. If the completed verification
record or retained bytes are unavailable, **stop before apply**.

The initial bundle does not back up newly generated postboot SSH host keys or
current state. This activation authorizes p5 journal replay/root traversal;
the account operation changes RAM identities. Neither operation changes the
host SSH identity. Do not describe the initial image as a current-state backup,
or restore the whole initial image over live host state as a guest recovery
shortcut. Erase/discard authorization is not backup verification.

After the guarded preparation above has established the selected read-only
root/state, exact drive and GPT roles, run these read-only checks before any
p5 mount. The protected plan supplies the p5 pathname; never substitute an SD
partition or select another block device by enumeration order.

```bash
GUEST_DEVICE=$(/usr/bin/python3 -I -B - /run/first-kvm-mount-plan.json <<'PY'
import json, os, sys
p = json.load(open(sys.argv[1]))
g = p['context']['guest']
assert p['mounted_before'] is False
assert g['uuid'] == os.environ['GUEST_UUID']
assert g['size_bytes'] == 238342373376
assert g['partition_number'] == 5
for line in open('/proc/self/mountinfo'):
    f = line.split()
    assert f[2] != g['major_minor']
    assert f[4] != '/run/a733-guests'
print(g['canonical_path'])
PY
)
[[ "$GUEST_DEVICE" == "${NVME}p5" ]]
/sbin/blkid -p -o export "$GUEST_DEVICE" | tee "$E/p5-before-mount.blkid"
[[ $(/sbin/blkid -p -s UUID -o value "$GUEST_DEVICE") == "$GUEST_UUID" ]]
DEBUGFS=$(command -v debugfs)
case "$DEBUGFS" in /sbin/debugfs|/usr/sbin/debugfs) ;; *) exit 1 ;; esac
"$DEBUGFS" -R 'ls -l /' "$GUEST_DEVICE" \
  > "$E/p5-pristine-root.txt" 2> "$E/p5-pristine-root.stderr"
"$DEBUGFS" -R 'ls -l /lost+found' "$GUEST_DEVICE" \
  > "$E/p5-pristine-lost-found.txt" 2> "$E/p5-pristine-lost-found.stderr"
cat "$E/p5-before-mount.blkid" "$E/p5-pristine-root.txt" \
  "$E/p5-pristine-root.stderr" "$E/p5-pristine-lost-found.txt" \
  "$E/p5-pristine-lost-found.stderr"
```

`debugfs` opens read-only by default; never add `-w`, repair, mount or replay
options. Require ext4 with the exact p5 UUID, a root listing containing only
`.`/`..`/`lost+found`, and an empty `lost+found` apart from `.`/`..`. A tool
version banner on stderr is normal; any filesystem/read error is a stop.
Keep these listings with the fresh-install/readback and off-device bundle
verification record. Maintain exclusive administrative control between these
checks and apply; no other process may activate or populate p5.

If p5 is mounted, nonempty, previously used, or its fresh-install provenance is
uncertain, **stop this procedure**. Obtain a real stopped-guest off-device backup
and verify it using [guest backups](guest-backups.md) before a separately
reviewed activation procedure. Do not type `BACKUP VERIFIED` based only on
matching UUIDs, directory listings or the earlier discard authorization.

Only after both the verified retained recovery bundle and pristine p5 checks
are established, apply on a real TTY and enter `BACKUP VERIFIED` and the exact
displayed `ACTIVATE` phrase. Ext4 journal replay can write even on a failed
mount; retain failure receipts and inspect the live mount before any retry.

```bash
/usr/bin/python3 -I -B "$T/scripts/activate-guest-storage.py" apply \
  "${mount_args[@]}" --plan /run/first-kvm-mount-plan.json \
  | tee "$E/mount-receipt.json"
findmnt -T /run/a733-guests | tee "$E/guest-mount.txt"
stat -c '%U:%G %a %n' /run/a733-guests
```

Require the sole full-filesystem p5 mount at `/run/a733-guests`, ext4,
`rw,nodev,nosuid,noexec`, root directory root:root `0711`.
This guide allocates the explicit new guest ID `firstkvm`, UID=GID `60001`.
Stop if already allocated; do not repurpose an existing identity or guest.
The account controller also refuses collisions and supplementary memberships.

```bash
NAME=firstkvm
export NAME
printf '%s\n' '{"schema":1,"guests":[{"name":"firstkvm","uid":60001,"gid":60001}]}' \
  > /run/a733-state/config/first-kvm-identities.json
chmod 0400 /run/a733-state/config/first-kvm-identities.json
/usr/bin/python3 -I -B "$T/scripts/guest-accounts.py" prepare \
  /run/a733-state/config/first-kvm-identities.json > /run/first-kvm-accounts-plan.json
chmod 0400 /run/first-kvm-accounts-plan.json
cat /run/first-kvm-accounts-plan.json
```

Review before the separate apply:

```bash
/usr/bin/python3 -I -B "$T/scripts/guest-accounts.py" apply \
  /run/a733-state/config/first-kvm-identities.json --plan /run/first-kvm-accounts-plan.json \
  | tee "$E/accounts-receipt.json"
getent passwd a733-vm-firstkvm
getent group a733-vm-firstkvm
passwd -S a733-vm-firstkvm
id a733-vm-firstkvm
```

Require locked password, `/nonexistent`, `/usr/sbin/nologin`, UID/GID 60001 and
no supplementary memberships. Accounts live in RAM and need recreation after
host reboot. Do not add the guest to `disk`, administration or other groups.

Check the actual KVM node and existing KVM group, then grant only that group
runtime access. Stop if `kvm` is absent, GID zero, collides with 60001, or the
node has an unexpected type/device/owner. Do not invent another group allocation.

```bash
/usr/bin/python3 -I -B - <<'PY'
import grp, os, stat
k = grp.getgrnam('kvm')
s = os.lstat('/dev/kvm')
assert 0 < k.gr_gid != 60001
assert stat.S_ISCHR(s.st_mode) and s.st_uid == 0
assert (os.major(s.st_rdev), os.minor(s.st_rdev)) == (10, 232)
os.chown('/dev/kvm', 0, k.gr_gid)
os.chmod('/dev/kvm', 0o660)
print('KVM group', k.gr_gid)
PY
stat -c '%F %u:%g %a %t:%T %n' /dev/kvm | tee "$E/kvm-node.txt"
```

Only the unit's explicit `SupplementaryGroups` grants guest KVM access. If
udev changes these node permissions, stop and resolve the actual runtime policy;
these commands do not install a persistent udev rule.

## Stage immutable seeds on NVMe and enroll the private disk

On the board create a new root-only seed directory on the activated p5:

```bash
mkdir -m 0700 /run/a733-guests/.first-kvm-seed-20261104
findmnt -T /run/a733-guests/.first-kvm-seed-20261104
```

On codex transfer exactly the three locally verified files:

```bash
scp -O -i /home/drew/.ssh/id_ed25519 -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=yes \
  out/guest-fixture-seed-20261104/Image \
  out/guest-fixture-seed-20261104/initramfs.cpio.gz \
  out/guest-fixture-seed-20261104/guest-disk.raw \
  root@"$BOARD_IP":/run/a733-guests/.first-kvm-seed-20261104/
```

Back on the board verify byte sizes and hashes before enrollment. The trusted
source disk stays root-owned and immutable by policy; enrollment makes a distinct
private writable copy. Never launch the seed itself.

```bash
SEED=/run/a733-guests/.first-kvm-seed-20261104
export SEED
chown root:root "$SEED/Image" "$SEED/initramfs.cpio.gz" "$SEED/guest-disk.raw"
chmod 0400 "$SEED/Image" "$SEED/initramfs.cpio.gz" "$SEED/guest-disk.raw"
(cd "$SEED" && sha256sum -c /dev/stdin <<'HASHES'
1294959bcaa5a5ad3791913e33cffb8f0893708396aac8be9837c03a0e1f9377  Image
2f3360a8f6365b84c352cae99949a5ae92a81001efe28dbae221f334805f854b  initramfs.cpio.gz
ff595fac0b9a7d990f099edc0dd0f9d4859fb9942e185d2f2377088ce32bd577  guest-disk.raw
HASHES
)
stat -c '%s %u:%g %a %h %n' "$SEED/"* | tee "$E/source-metadata.txt"
sha256sum "$SEED/"* > "$E/source-before.sha256"
```

Require sizes 5,251,080, 974,226 and 134,217,728 bytes respectively. Admission
uses 1 vCPU, 256 MiB guest RAM, 512 MiB `MemoryMax`, zero swap and 256 tasks.
Record online CPUs and measured `MemAvailable` now and again before each start;
require at least two online CPUs (one reserved for host work) and 640 MiB
available RAM (512 MiB cap plus the controller's 128 MiB host reserve). These
are bounded initial allocations, not measured workload or thermal safety.

```bash
cat > /run/first-kvm-budget.py <<'PY'
import os
from pathlib import Path
online = Path('/sys/devices/system/cpu/online').read_text().strip()
cpus = set()
for part in online.split(','):
    edge = list(map(int, part.split('-')))
    cpus.update(range(edge[0], edge[-1] + 1))
available = int(next(x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines()
                     if x.startswith('MemAvailable:'))) // 1024
print('online=', online, 'MemAvailable_MiB=', available, 'guest_vcpus=1 RAM_MiB=256 cap_MiB=512')
assert len(cpus) >= 2 and os.cpu_count() >= 2 and available >= 640
PY
/usr/bin/python3 -I -B /run/first-kvm-budget.py | tee "$E/budget-before-enroll.txt"
ss -ltnp | tee "$E/listeners-before.txt"
```

Require TCP port 18080 unused; if occupied stop and choose/review a different
explicit config before enrollment. The allowed profile has only a loopback TCP
forward, with restricted user networking and IPv6 disabled; no outward NAT.

```bash
cat > /run/first-kvm-config.json <<'JSON'
{"schema":1,"name":"firstkvm","uid":60001,"gid":60001,"memory_mib":256,"memory_max_mib":512,"cpus":1,"kernel":{"bytes":5251080,"sha256":"1294959bcaa5a5ad3791913e33cffb8f0893708396aac8be9837c03a0e1f9377"},"initrd":{"bytes":974226,"sha256":"2f3360a8f6365b84c352cae99949a5ae92a81001efe28dbae221f334805f854b"},"disk":{"bytes":134217728,"sha256":"ff595fac0b9a7d990f099edc0dd0f9d4859fb9942e185d2f2377088ce32bd577"},"network":{"mode":"isolated","host_tcp_port":18080,"guest_tcp_port":8080}}
JSON
chmod 0400 /run/first-kvm-config.json
enroll_args=(--config /run/first-kvm-config.json --kernel "$SEED/Image" \
  --initrd "$SEED/initramfs.cpio.gz" --disk "$SEED/guest-disk.raw" --guest-uuid "$GUEST_UUID")
/usr/bin/python3 -I -B "$T/scripts/enroll-guest.py" prepare \
  "${enroll_args[@]}" > /run/first-kvm-enrollment-plan.json
chmod 0400 /run/first-kvm-enrollment-plan.json
cat /run/first-kvm-enrollment-plan.json
```

Review source identities, all hashes, UUID, locked identity, resources and
capacity. Apply separately:

```bash
/usr/bin/python3 -I -B "$T/scripts/enroll-guest.py" apply \
  "${enroll_args[@]}" --plan /run/first-kvm-enrollment-plan.json \
  | tee "$E/enrollment-receipt.json"
PIN=$(/usr/bin/python3 -I -B - "$E/enrollment-receipt.json" <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
assert r['success'] and r['published'] and r['durable']
print(r['manifest_sha256'])
PY
)
printf '%s\n' "$PIN" > "$E/trusted-manifest-0.sha256"
export PIN
findmnt -T /run/a733-guests/firstkvm/disk.raw
stat -c '%u:%g %a %h %n' /run/a733-guests/firstkvm /run/a733-guests/firstkvm/*
```

Require directory root:60001 `0710`, kernel/initrd root:root `0444`, disk
60001:60001 `0600`, manifest root:root `0400`, all regular files single-linked.
Before starting, run on codex to save the trusted initial receipt and pin:

```bash
scp -O -i /home/drew/.ssh/id_ed25519 -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=yes \
  root@"$BOARD_IP":"$E/enrollment-receipt.json" \
  root@"$BOARD_IP":"$E/trusted-manifest-0.sha256" "$PROFILE_OUT/"
```

Existing enrollment is never replaced; inspect partial-publication receipts
before recovery.

## Render a RAM unit and start the KVM-only guest

There is no unit-rendering CLI. Use the actual pure Python module interface;
verify the protected manifest against the trusted receipt pin before rendering.
Use this same command after checkpointing to regenerate the unit from its new
`config`. It refuses an unexpected service name or enrollment contract.

```bash
cat > /run/first-kvm-render.py <<'PY'
import grp, hashlib, importlib.util, os
from pathlib import Path
base = Path('/usr/lib/a733-tools/configs')
def load(name):
    s = importlib.util.spec_from_file_location('first_' + name, base / (name + '.py'))
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m
raw = Path('/run/a733-guests/firstkvm/enrollment.json').read_bytes()
assert hashlib.sha256(raw).hexdigest() == os.environ['PIN']
m = load('guest_manifest').validate_manifest(raw)
c = m['config']
assert c['name'] == 'firstkvm' and c['uid'] == c['gid'] == 60001
unit = load('guest_config').render_unit(c, '/usr/lib/a733-tools/scripts/launch-kvm-guest.py', grp.getgrnam('kvm').gr_gid)
print(unit, end='')
PY
/usr/bin/python3 -I -B /run/first-kvm-render.py > /run/first-kvm-unit.review
cat /run/first-kvm-unit.review
```

Verify using the exact `.service` filename required by systemd:

```bash
cp /run/first-kvm-unit.review /run/a733-guest-firstkvm.service
systemd-analyze verify /run/a733-guest-firstkvm.service
```

Review `User/Group=a733-vm-firstkvm`, only the KVM supplementary group, all pins,
`MemoryMax=512M`, `MemorySwapMax=0`, `TasksMax=256`, `Restart=no`, empty
capabilities and `NoNewPrivileges=yes`. The launcher requires
`-machine virt,gic-version=host -accel kvm -cpu host`, and offers no fallback.
No unit may already exist in `/etc`, `/usr/lib` or `/run` for this name.

```bash
test ! -e /etc/systemd/system/a733-guest-firstkvm.service
test ! -e /usr/lib/systemd/system/a733-guest-firstkvm.service
test ! -e /run/systemd/system/a733-guest-firstkvm.service
install -o root -g root -m 0600 /run/a733-guest-firstkvm.service \
  /run/systemd/system/a733-guest-firstkvm.service
systemctl daemon-reload
systemctl cat a733-guest-firstkvm.service | tee "$E/unit-1.txt"
/usr/bin/python3 -I -B /run/first-kvm-budget.py | tee "$E/budget-before-start-1.txt"
```

Do not `enable` this unit. Prepare the observer before starting, then capture the
running QEMU process and actual KVM VM/vCPU descriptors immediately, before
the HTTP confirmation lets it exit:

```bash
cat > /run/first-kvm-observe.py <<'PY'
import os, subprocess, time
from pathlib import Path
for attempt in range(30):
    pid = int(subprocess.check_output(['systemctl', 'show', '-p', 'MainPID', '--value', 'a733-guest-firstkvm.service']))
    try:
        p = Path('/proc') / str(pid)
        exe = (p / 'exe').resolve().name
        if exe == 'qemu-system-aarch64':
            break
    except FileNotFoundError:
        pass
    time.sleep(1)
else:
    raise SystemExit('STOP: running QEMU not observed')
status = (p / 'status').read_text()
assert next(x for x in status.splitlines() if x.startswith('Uid:')).split()[1:] == ['60001'] * 4
args = (p / 'cmdline').read_bytes().split(b'\0')[:-1]
assert args[args.index(b'-accel') + 1] == b'kvm'
assert args[args.index(b'-cpu') + 1] == b'host'
fds = [(f.name, os.readlink(f)) for f in (p / 'fd').iterdir()]
assert any('kvm-vm' in target for _, target in fds)
assert any('kvm-vcpu' in target for _, target in fds)
print('PID', pid, 'exe', exe)
print(status)
print('argv', [x.decode() for x in args])
print('fds', fds)
PY
RUN=1
systemctl start a733-guest-firstkvm.service
/usr/bin/python3 -I -B /run/first-kvm-observe.py | tee "$E/kvm-process-1.txt"
ss -ltnp | tee "$E/listeners-running-1.txt"
journalctl -u a733-guest-firstkvm.service -b --no-pager -o short-monotonic \
  | tee "$E/console-before-http-1.txt"
```

Require the exact loopback-only listener on `127.0.0.1:18080`, no wildcard/IPv6
listener, and console `PASS cpu sha256=5ab247be0ba27004a2adee73bc50d4a1dfcd98f16ef5fa32c024e5f1c5c29f1c`,
`PASS disk previous=0 counter=1`, then
`READY network tcp=8080 counter=1; host must verify and confirm HTTP response`.
If READY has not arrived yet, follow `journalctl -fu a733-guest-firstkvm.service`
in a second trusted SSH shell. Finish HTTP checks within its 120-second wait.

On codex during that wait, record SSH continuity and refusal of a connection
to the management address's port 18080; an unexpected successful connection is
failure. This does not attempt a guest outward connection.

```bash
ssh -i /home/drew/.ssh/id_ed25519 -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes \
  root@"$BOARD_IP" 'date -u; systemctl is-active a733-management-ssh.service'
if curl --noproxy '*' --silent --show-error --max-time 5 "http://$BOARD_IP:18080/cgi-bin/probe"; then
  printf '%s\n' 'STOP: management-address guest forward unexpectedly reachable' >&2
  exit 1
fi
```

On the board verify the response, then explicitly confirm the exact counter:

```bash
EXPECTED_COUNTER=1
expected="a733-kvm-guest-network-v1 counter=$EXPECTED_COUNTER"
response=$(curl --noproxy '*' --fail --silent --show-error --max-time 10 \
  http://127.0.0.1:18080/cgi-bin/probe)
[[ "$response" == "$expected" ]]
printf '%s\n' "$response" | tee "$E/http-1.txt"
confirmation=$(curl --noproxy '*' --fail --silent --show-error --max-time 10 \
  "http://127.0.0.1:18080/cgi-bin/probe?confirm=$EXPECTED_COUNTER")
[[ "$confirmation" == "$expected" ]]
printf '%s\n' "$confirmation" | tee "$E/http-confirm-1.txt"
```

## Clean exit, checkpoint and restart persistence

Let the fixture power itself off. Do not use `systemctl stop` as evidence of
clean shutdown: that sends SIGTERM to QEMU. Within 60 seconds require
`inactive/dead`, `MainPID=0`, `ControlPID=0`, `Result=success`,
`ExecMainCode=1` (CLD_EXITED), `ExecMainStatus=0`. Capture the complete console
including `PASS network loopback-http counter=1`,
`PASS fixture counter=1; clean shutdown requested` and the kernel power-off.
Any FAIL, timeout, signal exit, OOM or missing evidence leaves acceptance open.

```bash
for attempt in {1..30}; do
  [[ $(systemctl show -p ActiveState --value a733-guest-firstkvm.service) == inactive ]] && break
  sleep 2
done
systemctl show a733-guest-firstkvm.service \
  -p LoadState -p ActiveState -p SubState -p MainPID -p ControlPID \
  -p Result -p ExecMainCode -p ExecMainStatus | tee "$E/exit-1.txt"
journalctl -u a733-guest-firstkvm.service -b --no-pager -o short-monotonic \
  > "$E/console-complete-1.txt"
cat "$E/console-complete-1.txt"
sha256sum -c "$E/source-before.sha256" | tee "$E/source-after-1.txt"
sha256sum /run/a733-guests/firstkvm/disk.raw | tee "$E/stopped-disk-1.sha256"
/usr/bin/python3 -I -B "$T/scripts/checkpoint-guest.py" prepare \
  --name firstkvm --manifest-sha256 "$PIN" > /run/first-kvm-checkpoint-1.json
chmod 0400 /run/first-kvm-checkpoint-1.json
cat /run/first-kvm-checkpoint-1.json
```

Review the new disk hash against the stopped-disk hash above, unchanged seed,
identity, UUID, kernel/initrd, resources and network, old manifest pin and
proposed new pin. No readers/backups may hold the working disk. Apply only
after reviewing the clean console and host exit, using the real TTY prompts
`CLEAN SHUTDOWN VERIFIED` and the exact displayed `CHECKPOINT` phrase.

```bash
/usr/bin/python3 -I -B "$T/scripts/checkpoint-guest.py" apply \
  --name firstkvm --manifest-sha256 "$PIN" --plan /run/first-kvm-checkpoint-1.json \
  | tee "$E/checkpoint-receipt-1.json"
PIN=$(/usr/bin/python3 -I -B - "$E/checkpoint-receipt-1.json" <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
assert r['success'] and r['published'] and r['durable']
print(r['new_manifest_sha256'])
PY
)
export PIN
printf '%s\n' "$PIN" > "$E/trusted-manifest-1.sha256"
```

Before restart, save the new receipt/pin from codex:

```bash
scp -O -i /home/drew/.ssh/id_ed25519 -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=yes \
  root@"$BOARD_IP":"$E/checkpoint-receipt-1.json" \
  root@"$BOARD_IP":"$E/trusted-manifest-1.sha256" "$PROFILE_OUT/"
```

Back on the board, with noclobber still set,
render into a fresh review file, verify and deliberately replace only this
known RAM unit after checking the stopped state:

```bash
/usr/bin/python3 -I -B /run/first-kvm-render.py > /run/first-kvm-unit-2.review
cat /run/first-kvm-unit-2.review
install -o root -g root -m 0600 /run/first-kvm-unit-2.review \
  /run/a733-guest-firstkvm.service
systemd-analyze verify /run/a733-guest-firstkvm.service
install -o root -g root -m 0600 /run/a733-guest-firstkvm.service \
  /run/systemd/system/a733-guest-firstkvm.service
systemctl daemon-reload
systemctl cat a733-guest-firstkvm.service | tee "$E/unit-2.txt"
/usr/bin/python3 -I -B /run/first-kvm-budget.py | tee "$E/budget-before-start-2.txt"
RUN=2
systemctl start a733-guest-firstkvm.service
```

Repeat the running observations immediately and inspect only the new console
entries for the restart. Require `PASS disk previous=1 counter=2` and READY
counter 2. Repeat the codex SSH continuity and management-port refusal checks
above while it runs, then complete the following within the HTTP wait:

```bash
/usr/bin/python3 -I -B /run/first-kvm-observe.py | tee "$E/kvm-process-2.txt"
ss -ltnp | tee "$E/listeners-running-2.txt"
journalctl -u a733-guest-firstkvm.service -b --no-pager -o short-monotonic \
  | tee "$E/console-before-http-2.txt"
EXPECTED_COUNTER=2
expected="a733-kvm-guest-network-v1 counter=$EXPECTED_COUNTER"
response=$(curl --noproxy '*' --fail --silent --show-error --max-time 10 \
  http://127.0.0.1:18080/cgi-bin/probe)
[[ "$response" == "$expected" ]]
printf '%s\n' "$response" | tee "$E/http-2.txt"
confirmation=$(curl --noproxy '*' --fail --silent --show-error --max-time 10 \
  "http://127.0.0.1:18080/cgi-bin/probe?confirm=$EXPECTED_COUNTER")
[[ "$confirmation" == "$expected" ]]
printf '%s\n' "$confirmation" | tee "$E/http-confirm-2.txt"
for attempt in {1..30}; do
  [[ $(systemctl show -p ActiveState --value a733-guest-firstkvm.service) == inactive ]] && break
  sleep 2
done
systemctl show a733-guest-firstkvm.service \
  -p LoadState -p ActiveState -p SubState -p MainPID -p ControlPID \
  -p Result -p ExecMainCode -p ExecMainStatus | tee "$E/exit-2.txt"
journalctl -u a733-guest-firstkvm.service -b --no-pager -o short-monotonic \
  > "$E/console-complete-2.txt"
cat "$E/console-complete-2.txt"
sha256sum -c "$E/source-before.sha256" | tee "$E/source-after-2.txt"
sha256sum /run/a733-guests/firstkvm/disk.raw | tee "$E/stopped-disk-2.sha256"
```

Require another clean zero QEMU exit with the same stopped-state fields,
network/fixture PASS counter 2 and kernel power-off. A changed stopped disk hash
alone does not prove correct contents; the verified persisted counter supplies
that check. Only after reviewing this evidence prepare the second checkpoint:

```bash
/usr/bin/python3 -I -B "$T/scripts/checkpoint-guest.py" prepare \
  --name firstkvm --manifest-sha256 "$PIN" > /run/first-kvm-checkpoint-2.json
chmod 0400 /run/first-kvm-checkpoint-2.json
cat /run/first-kvm-checkpoint-2.json
```

Review all fields as for checkpoint 1, then apply separately with the same real
TTY attestations:

```bash
/usr/bin/python3 -I -B "$T/scripts/checkpoint-guest.py" apply \
  --name firstkvm --manifest-sha256 "$PIN" --plan /run/first-kvm-checkpoint-2.json \
  | tee "$E/checkpoint-receipt-2.json"
PIN=$(/usr/bin/python3 -I -B - "$E/checkpoint-receipt-2.json" <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
assert r['success'] and r['published'] and r['durable']
print(r['new_manifest_sha256'])
PY
)
export PIN
printf '%s\n' "$PIN" > "$E/trusted-manifest-2.sha256"
```

Copy the evidence directory off-device from codex with the same trusted key:

```bash
test ! -e "$PROFILE_OUT/board-evidence"
scp -O -r -i /home/drew/.ssh/id_ed25519 -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=yes root@"$BOARD_IP":"$E" \
  "$PROFILE_OUT/board-evidence"
```

Inspect receipts for success and durability before adopting any new pin.
Interrupted publication can leave a changed manifest even if the operation
reports failure; do not blindly retry or restore an old manifest over new disk
bytes. Keep the initial seed and both stopped disk/checkpoint records distinct.

The evidence packet must bind the actual host/boot ID, selected NVMe roles,
all source pins, measured budgets, locked UID, bounded unit, running
`-accel kvm -cpu host` process and KVM VM/vCPU descriptors, both full guest
consoles, allowed HTTP exchanges, loopback listener policy, clean QEMU exits,
and counter persistence. Record hardware results separately after execution.
Installed-host time policy, repeated boots, power/thermal workload, SD-write
proof, updates/rollback/recovery/restore and the remaining
[hardware acceptance gates](hardware-validation.md) stay open.
