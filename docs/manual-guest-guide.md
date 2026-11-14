# Manual guest operations

This is the supported beta workflow after installing a `manual-beta` root. Use
its complete `/usr/lib/a733-tools` tree. The old installed image is not changed
by a source checkout; see [current status](implementation-status.md).

A started systemd guest survives SSH disconnection. Guest startup after host
reboot is explicit; automatic crash restart and managed checkpointing are not
promised. QEMU uses KVM, an unprivileged account, the existing manifest admission
checks and a raw disk on NVMe. The current launcher accepts a kernel/initramfs
pair and uses `virt,gic-version=host`, `-cpu host`, `-accel kvm` and
`console=ttyAMA0 rdinit=/init`. Supply a compatible ARM64 Linux guest; the small
test fixture establishes integration, not a qualification matrix of guest OSes.

## 1. Check host and prepare storage

Work in a root shell on the installed host. Confirm the selected root, correct
NVMe/STATE and expected profile before guest work:

```sh
findmnt / /run/a733-state /run/a733-guests
python3 -I -B - <<'PY'
import json
from pathlib import Path
p = json.loads(Path('/usr/lib/orangepi-hypervisor/payload.json').read_text())
assert p.get('guest_management_profile') == 'manual-beta'
print('manual-beta root descriptor present')
PY
systemctl status a733-guest-storage.service --no-pager
systemctl is-enabled a733-guest-prepare.service
```

The dispatch-only `a733-guest-prepare.service` should report `masked`; its
nonzero `is-enabled` exit is expected. The storage service remains enabled for
preparation and teardown. Its name does not mean it starts guests.

A new installation needs the protected guest-storage policy. Before any admission
runtime has been initialized, publish it using values from that unit's installation
record, then start storage preparation:

```sh
: "${A733_NVME_MODEL:?Set exact installed NVMe model}"
: "${A733_NVME_SERIAL:?Set exact installed NVMe serial}"
: "${A733_NVME_BYTES:?Set exact installed capacity}"
: "${A733_STATE_UUID:?Set installed STATE filesystem UUID}"
: "${A733_GUEST_UUID:?Set installed GUESTS filesystem UUID}"
python3 -I -B /usr/lib/a733-tools/scripts/configure-guest-storage.py   --target /dev/nvme0n1 --expected-model "$A733_NVME_MODEL"   --expected-serial "$A733_NVME_SERIAL" --expected-bytes "$A733_NVME_BYTES"   --state-uuid "$A733_STATE_UUID" --guest-uuid "$A733_GUEST_UUID"
systemctl start a733-guest-storage.service
```

Existing identical policy is retained. Never delete policy/admission files to
force initial setup. A closed shutdown gate requires the supported fresh boot;
restarting the service or editing RAM metadata is not recovery. Preparation
retains shared storage, account and admission protections even though automatic
dispatch is disabled. Inspect an actual failed service receipt once before retrying.

## 2. Enroll or restore

For a new guest, use the exact account and seed-file schemas and prepare/apply
commands in [storage/accounts](guest-activation.md) and [enrollment](guest-enrollment.md).
For an existing guest, use [off-device restoration](guest-restoration.md).
Keep stable UID/GID assignments and the trusted manifest hash from the successful
durable receipt. Newly enrolled guests require their account before launch;
boot preparation recreates accounts for existing protected enrollments.

The manifest is root-owned and its disk pin changes after guest writes. Do not
calculate a new pin from unexplained disk bytes simply to make a launch succeed.

## 3. Render and install the manual service

The following generalizes the existing working renderer without introducing a
replacement management stack. Set the enrolled name and independently trusted
manifest hash. Keep the guest stopped while replacing its unit.

```sh
: "${A733_GUEST_NAME:?Set enrolled name}"
: "${A733_GUEST_MANIFEST_SHA256:?Set trusted current manifest hash}"
umask 077
A733_UNIT_DIR=$(mktemp -d /run/a733-unit.XXXXXX)
python3 -I -B - "$A733_GUEST_NAME" "$A733_GUEST_MANIFEST_SHA256"   > "$A733_UNIT_DIR/guest.service" <<'PY'
from pathlib import Path
import grp, hashlib, importlib.util, re, sys
name, pin = sys.argv[1:]
assert re.fullmatch(r'[a-z]([a-z0-9-]{0,14}[a-z0-9])?', name)
assert re.fullmatch(r'[0-9a-f]{64}', pin)
base = Path('/usr/lib/a733-tools')
def load(name):
    spec = importlib.util.spec_from_file_location(name, base/'configs'/(name+'.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
raw = (Path('/run/a733-guests')/name/'enrollment.json').read_bytes()
assert hashlib.sha256(raw).hexdigest() == pin
manifest = load('guest_manifest').validate_manifest(raw)
assert manifest['config']['name'] == name
print(load('guest_config').render_unit(manifest['config'],
      str(base/'scripts/launch-kvm-guest.py'), grp.getgrnam('kvm').gr_gid), end='')
PY
A733_UNIT="a733-guest-${A733_GUEST_NAME}.service"
mv "$A733_UNIT_DIR/guest.service" "$A733_UNIT_DIR/$A733_UNIT"
systemd-analyze verify "$A733_UNIT_DIR/$A733_UNIT"
cat "$A733_UNIT_DIR/$A733_UNIT"
systemctl show "$A733_UNIT" -p ActiveState -p MainPID -p ControlPID
```

Require an inactive/absent guest and no consumers; review the generated identity,
resource limits and network exposure. Then install the unit in RAM and start it:

```sh
install -o root -g root -m 0644 "$A733_UNIT_DIR/$A733_UNIT" "/run/systemd/system/$A733_UNIT"
systemctl daemon-reload
systemctl start "$A733_UNIT"
systemctl status "$A733_UNIT" --no-pager
journalctl -u "$A733_UNIT" -n 50 --no-pager
```

Do not enable this RAM unit for automatic boot. Use the existing
[networking contract](guest-networking.md) for the optional loopback forward.
For the short acceptance fixture, its documented HTTP confirmation causes its
own shutdown; an ordinary guest uses its configured guest-OS shutdown path.

## 4. Shut down, checkpoint and restart

Use shutdown inside the guest, wait for QEMU to exit, and verify service/process
state. `systemctl stop` sends a host signal and alone does not attest filesystem
cleanliness. Do not back up or checkpoint a running disk.

```sh
systemctl show "$A733_UNIT" -p ActiveState -p SubState -p MainPID -p ControlPID
journalctl -u "$A733_UNIT" -n 50 --no-pager
A733_CHECKPOINT_DIR=$(mktemp -d /run/a733-checkpoint.XXXXXX)
python3 -I -B /usr/lib/a733-tools/scripts/checkpoint-guest.py prepare   --name "$A733_GUEST_NAME" --manifest-sha256 "$A733_GUEST_MANIFEST_SHA256"   > "$A733_CHECKPOINT_DIR/plan.json"
chmod 0400 "$A733_CHECKPOINT_DIR/plan.json"
cat "$A733_CHECKPOINT_DIR/plan.json"
python3 -I -B /usr/lib/a733-tools/scripts/checkpoint-guest.py apply   --name "$A733_GUEST_NAME" --manifest-sha256 "$A733_GUEST_MANIFEST_SHA256"   --plan "$A733_CHECKPOINT_DIR/plan.json"
```

The existing clean-shutdown attestation remains part of this operation. Save the
successful durable receipt's `new_manifest_sha256` as the next trusted pin;
regenerate the unit with that pin before starting again. Inspect publication and
durability fields if interrupted rather than blindly retrying an old plan.
A checkpoint updates metadata/history; use [backup](guest-backups.md) for a copy.

Before a planned host reboot, stop and checkpoint each guest. After reboot,
verify preparation and render/start the wanted units again. There is no beta
promise of automatic safe guest restart or automatic dirty-disk recovery.

## Troubleshooting and acceptance

Use `systemctl`, `journalctl`, `findmnt`, the packaged tools' structured failures,
and [known issues](known-issues.md). Do not bypass a failed admission check by
launching raw QEMU against the enrolled disk. Run only the affected check after a
specific correction. Ordinary operations do not require a new agent review of
all historical evidence. The release smoke adds one observation of real KVM and
data persistence; it is not repeated for every routine start.
