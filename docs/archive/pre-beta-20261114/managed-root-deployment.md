> **Historical record — not active instructions or release gates.**
> Superseded by the [manual beta scope](../../beta-scope.md) and
> [current status](../../implementation-status.md). Original observations retain
> their recorded configuration and limitations.

# Install managed B while retaining normalized A

This records the previous `2996365` deployment and its original artifact pins.
For the repaired `4334a22` roots use [the new build and artifact record](../../managed-systemd-root-build-20261113.md)
and [current status](../../implementation-status.md). Do not mix the two cohorts' pins.


This initial-install procedure was executed for candidate
`2996365a11cb07deb20eb4325c90841de8ab820e` on 2026-11-13. SPI recovery,
post-reinstall SD-removed boot/manual KVM, checkpoint preservation, B staging,
durable selection and the first authenticated B boot pass.
[Physical receipts](../../evidence/post-reinstall-SPI-managed-B-20261113.json).
The commands below describe that initial A→B migration and require fresh native
identity/plan review for another installation; do not replay against running B.

This procedure ends at initial B selection and the separately reviewed first
boot handoff. It does not authorize a forced reboot, automatic retry, overwrite
of A or a new endurance run. Keep UART capture running throughout execution.

## Boundaries and fresh live inputs

Preserve working normalized A p2, NVMe BOOT p1, GPT, existing STATE p4 contents,
the stopped manual `firstkvm` cohort and p5. Only candidate source/plan/journal
files are added on STATE; staging writes inactive B p3. Selection writes STATE
boot records. There is no SPI/SD/BOOT write in this migration. Keep the known
2 GB recovery SD off-device during ordinary SPI boots, all original/calibrated
SPI backups and the exact normalized A rollback component off-device.

For the recorded execution the A SPI boot was
`ae6ef902-51ce-45cf-88a9-62f7f390eded`, `.118` (strict host alias `.116`).
For any future initial installation, obtain and independently review: current boot ID, pinned SSH identity, actual NVMe whole-device pathname,
partition geometry/UUIDs, A root marker, current selector bytes/hash, stopped
guest checkpoint/disk/history bindings, no guest/KVM consumers, current BOOT/GPT
bindings, STATE free space and UART capture. Expected disk identity is SPCC M.2
PCIe SSD / serial `134807190FC700149277` / 256060514304 bytes; expected STATE UUID
is `360dfb39-f49f-487e-b307-0bb887c854c5`. Expected A filesystem UUID is
`a7330000-0000-4000-8000-00000000000a`; B ends `000b`.

Live paths, boot ID, old B bytes, fresh plan hashes, exact STAGE/SELECT phrases,
free capacity and deployment launcher must be resolved for each execution.
No precomputed device plan is valid. The known-target authorization persists;
independent review of each concrete live plan remains required. A target mismatch
is a stop, not permission to substitute a different disk.

## Which code runs

The original normalized payload manifest is 33481 bytes, SHA-256
`c1bcc7d23d22bd487534ba17ea447055881a044350829608360aa92ca24283f2`.
It authenticates the entire installed 40-file `/usr/lib/a733-tools` closure.
`docs/evidence/normalized-maintenance-20261109.json:6` records source
`81f907481b12e9639ad23752966e05166074f96f`. Its exact source pins are:

| Relative installed script | SHA-256 |
| --- | --- |
| scripts/prepare-host-update.py | `464d44d7c8e868fd72f85f3ac5d6095d7d18d205528b0f55c379c9b2b30c3b4b` |
| scripts/update-host.py | `7e244244e36eabf6ef64b2640029cb05e77b43a8aed121257254dd3a6908433d` |
| scripts/select-host-root.py | `646b608cf5dc3fbd3f1411a49ace41192ee0dec4472199240f87cc1c1073fd89` |

I reproduced these hashes from the complete historical checkout revision
`50bc9e70698d4a09c3705594dcc61c64e1715cb9` with `git show REV:PATH | sha256sum`.
Thus the tested historical controller sources match the normalized sources;
the two revision names themselves are not interchangeable. The whole-reader
artifact review at `docs/evidence/managed-root-artifact-review-20261112.json:14`
uses that historical checkout and actual new roots. This establishes regular-file
reader interoperability, not physical staging or boot.

No new controller is copied into old A. No package install or replacement is
needed when its installed closure authenticates. `docs/matched-host-updates-20261109.md:81`
documents this installed-tree path. If a deployment environment uses a convenience
wrapper, authenticate its bytes and inspect its argv/TTY behavior separately;
no named wrapper or wrapper hash is specified by the repository contract. The
commands below use the installed tree directly through pinned SSH for transfer; apply needs a real interactive PTY. SSH is the
documented existing deployment transport (`docs/deploy-protected-20261109.md:138`).
Stop if that installed tree differs; do not resolve a mismatch by installing
the new managed controllers into old A.

## Authenticate transport inputs; keep preliminary work in RAM

Prepared metadata was produced by the actual preparer and accepted by the old
reader on the builder. Its native directory is
`out/managed-root-candidate-20261112-02/prepared-B-metadata`; the builder retains
`/var/lib/a733-candidate-artifact-review-20261112-01/attempt-02/prepared-0-B`.
[Preparation evidence](../../evidence/managed-candidate-deployment-preparation-20261112.json)
binds these files. `candidate.json` is informational: old `Source.NAMES` reads
only `manifest.json`, `root.ext4`, `selected.conf`.

| Input | Bytes | SHA-256 |
| --- | ---: | --- |
| B gzip | 143661932 | `380ec5c9c20e35f8480e5ff6d40ddc1ab258924f1074252668f84010b08dfbe0` |
| B raw | 4294967296 | `fe842ff05584205b4fb9f930b3620bac04155c9f751488a8fb20a0d798dbd87c` |
| Prepared manifest | 51618 | `65f69d6a2f0b6346c0ca2f0257daa26836dd791918a4af795e70de730f8f424a` |
| Prepared selector | 179 | `39958be7dffc35100dc9b036bed20bcf091cb5fb4d9c8e8adcc6f04546bcf763` |
| Informational candidate record | 9332 | `a088e6dc6ca66963e6647c330b8a425c8f6ab478d1ed834024f4ccf80aea1569` |

On codex, verify the files below, including their table byte lengths. Set the
fresh address/boot ID from the independently authenticated census. The host-key
alias deliberately retains the original pinned host identity.

```bash
set -euo pipefail
cd /home/drew/orange-pi-4-pro
: "${A733_HOST_IP:?Set authenticated current address}"
: "${A733_EXPECTED_BOOT_ID:?Set fresh reviewed boot ID}"
A733_SSH=(ssh -T -i /home/drew/.ssh/id_ed25519 -o IdentitiesOnly=yes \
  -o BatchMode=yes -o StrictHostKeyChecking=yes -o HostKeyAlias=192.168.1.116 \
  -o ConnectTimeout=5 "root@$A733_HOST_IP")
sha256sum -c - <<'PINS'
380ec5c9c20e35f8480e5ff6d40ddc1ab258924f1074252668f84010b08dfbe0  out/managed-root-candidate-20261112-02/artifacts/root-B.ext4.gz
65f69d6a2f0b6346c0ca2f0257daa26836dd791918a4af795e70de730f8f424a  out/managed-root-candidate-20261112-02/prepared-B-metadata/manifest.json
39958be7dffc35100dc9b036bed20bcf091cb5fb4d9c8e8adcc6f04546bcf763  out/managed-root-candidate-20261112-02/prepared-B-metadata/selected.conf
c1bcc7d23d22bd487534ba17ea447055881a044350829608360aa92ca24283f2  out/normalized-a-compatibility-20261112/manifest.json
3c76b96c328b4508ff407c8594352e04389664e97ced6f0a09284cade5bd027b  out/normalized-a-compatibility-20261112/selected.conf
PINS
"${A733_SSH[@]}" 'umask 077; test ! -e /run/managed-B-install; mkdir -m 0700 /run/managed-B-install'
"${A733_SSH[@]}" 'set -euC; umask 077; cat > /run/managed-B-install/old-manifest.json; chmod 0400 /run/managed-B-install/old-manifest.json' \
  < out/normalized-a-compatibility-20261112/manifest.json
```

`/run` must be the existing tmpfs from the read-only census; this preliminary
operation copies only the old public producer manifest to private RAM. No STATE
mkdir/copy occurs yet. Use a fresh RAM tag if the path already exists; retain
uncertain outputs. Do not create swap or expand the 256 GB carrier.

## Authenticate installed code before importing its live guard

In a native root Bash session set/export the reviewed boot ID and actual
whole-device pathname. Run this block before **any** STATE copy/mkdir. It uses
stdlib until the 40 installed files and exact directories authenticate, then
calls the real old `host-update-target.py:snapshot` read-only API. It does not
mount, initialize runtime or write a selector.

```bash
set -euo pipefail
set -C
umask 077
: "${A733_EXPECTED_BOOT_ID:?Set reviewed current boot ID in this shell}"
: "${A733_NVME_TARGET:?Set reviewed whole-device path}"
export A733_EXPECTED_BOOT_ID A733_NVME_TARGET
/usr/bin/python3 -I -B - <<'PY' > /run/managed-B-install/live-guard.json
import hashlib, importlib.util, json, os, shutil, stat
from pathlib import Path
assert Path('/proc/sys/kernel/random/boot_id').read_text().strip() == os.environ['A733_EXPECTED_BOOT_ID']
assert not list(Path('/sys/class/block').glob('mmcblk*')), 'SD/MMC block device present'
data = Path('/run/managed-B-install/old-manifest.json').read_bytes()
assert hashlib.sha256(data).hexdigest() == 'c1bcc7d23d22bd487534ba17ea447055881a044350829608360aa92ca24283f2'
files = json.loads(data)['maintenance_runtime']['files']
assert len(files) == 40
base = Path('/usr/lib/a733-tools')
expected = {Path('/' + name) for name in files}
directories = {base} | {parent for f in expected for parent in f.parents if parent == base or base in parent.parents}
actual = set(base.rglob('*')) | {base}
assert actual == expected | directories
for d in directories:
    st = d.lstat()
    assert stat.S_ISDIR(st.st_mode) and st.st_uid == st.st_gid == 0 and stat.S_IMODE(st.st_mode) == 0o555
for name, record in files.items():
    f = Path('/' + name); st = f.lstat()
    assert stat.S_ISREG(st.st_mode) and st.st_uid == st.st_gid == 0 and st.st_nlink == 1
    assert stat.S_IMODE(st.st_mode) == record['mode'] and st.st_size == record['bytes']
    content = f.read_bytes()
    assert len(content) == record['bytes'] and hashlib.sha256(content).hexdigest() == record['sha256']
for proc in Path('/proc').glob('[0-9]*/task/[0-9]*'):
    try:
        assert b'qemu-system' not in (proc/'cmdline').read_bytes(), 'QEMU present'
        for fd in (proc/'fd').iterdir():
            try:
                link = os.readlink(fd)
            except FileNotFoundError:
                continue
            assert not any(x in link for x in ('kvm-vm', 'kvm-vcpu', 'kvm-vgic', 'kvm-arm-vgic')), 'KVM consumer present'
    except FileNotFoundError:
        continue
spec = importlib.util.spec_from_file_location('reviewed_old_target', base/'scripts/host-update-target.py')
policy = importlib.util.module_from_spec(spec); spec.loader.exec_module(policy)
context = policy.snapshot(os.environ['A733_NVME_TARGET'], 'SPCC M.2 PCIe SSD',
                          '134807190FC700149277', 256060514304, 'B')
assert context['active']['slot'] == context['state']['selector']['SLOT'] == 'A'
assert context['state']['uuid'] == '360dfb39-f49f-487e-b307-0bb887c854c5'
assert context['state']['selector_sha256'] == '3c76b96c328b4508ff407c8594352e04389664e97ced6f0a09284cade5bd027b'
assert context['target']['partition_number'] == 3
assert shutil.disk_usage('/run/a733-state').free >= 5*1024**3
print(json.dumps({'boot_id': os.environ['A733_EXPECTED_BOOT_ID'], 'context': context}, sort_keys=True))
PY
chmod 0400 /run/managed-B-install/live-guard.json
```

Stop for independent review of this RAM receipt with fresh UART, pinned SSH,
physical SD-removal/SPI-boot evidence, BOOT/GPT hashes and the stopped manual
`firstkvm` checkpoint/disk/history custody. The historical API validates read-only
root, writable safe STATE, root/STATE pairing, selected A, exact disk identity,
geometry/GPT and idle inactive p3. It does not certify SPI route or guest custody.
Confirm no VM/vCPU/VGIC consumers, enough free space and a root-owned safe STATE
parent. A mismatch is a blocker; never substitute a target or install new tools
into A. Keep the host quiescent; after a reboot, guest start, mount/selection or
identity change repeat authentication/census/review before continuing.

## Create one private candidate after the live review

Repeat the preceding Python guard immediately before this step, changing its
output redirect to `/run/managed-B-install/live-guard-recheck.json`. Do not
replace the reviewed receipt. Require identical bytes before any STATE mkdir:

```bash
chmod 0400 /run/managed-B-install/live-guard-recheck.json
cmp /run/managed-B-install/live-guard.json /run/managed-B-install/live-guard-recheck.json
```

On the board, then create only the private STATE destination:

```bash
export A733_MIGRATION=/run/a733-state/managed-B-install-20261113-01
export A733_TOOLS=/usr/lib/a733-tools
test ! -e "$A733_MIGRATION"
mkdir -m 0700 "$A733_MIGRATION"
mkdir -m 0700 "$A733_MIGRATION/candidate-B"
```

From codex copy the gzip and the two small authenticated prepared files. These
commands are permitted only after the just-completed live guard/review. Do not
reuse this tag after a partial/uncertain transfer.

```bash
"${A733_SSH[@]}" 'set -euC; umask 077; cat > /run/a733-state/managed-B-install-20261113-01/root-B.ext4.gz; chmod 0400 /run/a733-state/managed-B-install-20261113-01/root-B.ext4.gz' \
  < out/managed-root-candidate-20261112-02/artifacts/root-B.ext4.gz
for name in manifest.json selected.conf; do
  "${A733_SSH[@]}" "set -euC; umask 077; cat > /run/a733-state/managed-B-install-20261113-01/candidate-B/$name; chmod 0400 /run/a733-state/managed-B-install-20261113-01/candidate-B/$name" \
    < "out/managed-root-candidate-20261112-02/prepared-B-metadata/$name"
done
```

In the board root shell, decode once, directly to the final sparse source. Do
**not** run the onboard preparer: it would create another 4 GiB copy/hash. The
historical updater will still perform its own complete source authentication.

```bash
/usr/bin/python3 -I -B - <<'PY'
import gzip, hashlib, os, stat, time
from pathlib import Path
p = Path(os.environ['A733_MIGRATION'])
for name, size, pin in (
    ('root-B.ext4.gz', 143661932, '380ec5c9c20e35f8480e5ff6d40ddc1ab258924f1074252668f84010b08dfbe0'),
    ('candidate-B/manifest.json', 51618, '65f69d6a2f0b6346c0ca2f0257daa26836dd791918a4af795e70de730f8f424a'),
    ('candidate-B/selected.conf', 179, '39958be7dffc35100dc9b036bed20bcf091cb5fb4d9c8e8adcc6f04546bcf763')):
    f = p/name; st = f.lstat()
    assert stat.S_ISREG(st.st_mode) and st.st_uid == st.st_gid == 0 and st.st_nlink == 1 and stat.S_IMODE(st.st_mode) == 0o400
    assert st.st_size == size and hashlib.sha256(f.read_bytes()).hexdigest() == pin
h = hashlib.sha256(); n = 0; last = time.monotonic()
with gzip.open(p/'root-B.ext4.gz', 'rb') as source, (p/'candidate-B/root.ext4').open('xb') as out:
    os.fchmod(out.fileno(), 0o400)
    while True:
        b = source.read(1024**2)
        if not b: break
        n += len(b); assert n <= 4294967296; h.update(b)
        if b.strip(b'\0'): out.write(b)
        else: out.seek(len(b), os.SEEK_CUR)
        if time.monotonic() - last >= 5:
            print(f'decoded {n}/4294967296 bytes', flush=True); last = time.monotonic()
    assert n == 4294967296 and h.hexdigest() == 'fe842ff05584205b4fb9f930b3620bac04155c9f751488a8fb20a0d798dbd87c'
    out.truncate(n); out.flush(); os.fsync(out.fileno())
for f in (p/'candidate-B/manifest.json', p/'candidate-B/selected.conf'):
    with f.open('rb') as stream: os.fsync(stream.fileno())
for d in (p/'candidate-B', p, p.parent):
    fd = os.open(d, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)
print('4294967296 bytes decoded, raw B hash matched, candidate synced; no partition written')
PY
```

## Prepare and independently review p3 staging, then apply

Use the whole-device path verified live (previously `/dev/nvme0n1`); controllers
derive p3. Do not pass a p3 path as the whole-device target. Run these in the same
root Bash session and retain plans/receipts off-device before later reboots.

```bash
: "${A733_NVME_TARGET:?Set reviewed actual whole-NVMe device path}"
identity=(--target "$A733_NVME_TARGET" --expected-model 'SPCC M.2 PCIe SSD' \
  --expected-serial 134807190FC700149277 --expected-bytes 256060514304)
update=(--slot B --candidate "$A733_MIGRATION/candidate-B" \
  --manifest-sha256 65f69d6a2f0b6346c0ca2f0257daa26836dd791918a4af795e70de730f8f424a \
  "${identity[@]}")
set -C
/usr/bin/python3 -I -B "$A733_TOOLS/scripts/update-host.py" prepare \
  "${update[@]}" > "$A733_MIGRATION/update-B-plan.json"
chmod 0400 "$A733_MIGRATION/update-B-plan.json"
```

Stop at this boundary for independent plan review. Require running+selected A,
expected STATE UUID and disk geometry, exact B raw hash above, only p3 target,
safe source/plan locations and unchanged prior root/BOOT/GPT/guest bindings.
After review, use a coordinator-controlled interactive PTY (native console or pinned `ssh -tt`);
do not redirect stderr or pipe input. The coordinator supplies the two legacy
phrases under persisted known-target authorization; this is not a new operator
approval request:

```bash
/usr/bin/python3 -I -B "$A733_TOOLS/scripts/update-host.py" apply \
  "${update[@]}" --plan "$A733_MIGRATION/update-B-plan.json" \
  > "$A733_MIGRATION/update-B-receipt.json"
chmod 0400 "$A733_MIGRATION/update-B-receipt.json"
```

Old source `50bc9e7:scripts/update-host.py:182` requires two exact input lines:
`BACKUP VERIFIED`, then the displayed
`STAGE B SERIAL CANONICAL_PARTITION_PATH PARTITION_BYTES PLAN_SHA256`.
`PARTITION_BYTES` is the 4 GiB root size, not the whole-drive byte count. Values
come from the actual reviewed plan/display; do not precompute the phrase here.
The old source hardcodes `verify_readback=True` (lines 273–279). It has no
`--verify-readback`, yes/no or force flag. Staging must complete with successful
flush, 4294967296 bytes written and verified, and `activation_ready=true`.
Selection must still be A. If interrupted/failing, retain receipts and actual
state and prepare a fresh plan; do not activate incomplete B or reuse a stale
staging plan. The old hash passes may be silent; copy/readback have progress.
Do not benchmark storage to explain duration.

## Prepare and review selector publication separately

```bash
selection=(--intent activate --slot B "${identity[@]}" \
  --payload-manifest "$A733_MIGRATION/candidate-B/manifest.json" \
  --manifest-sha256 65f69d6a2f0b6346c0ca2f0257daa26836dd791918a4af795e70de730f8f424a)
/usr/bin/python3 -I -B "$A733_TOOLS/scripts/select-host-root.py" prepare \
  "${selection[@]}" > "$A733_MIGRATION/select-B-plan.json"
chmod 0400 "$A733_MIGRATION/select-B-plan.json"
```

Independently review that fresh plan, then in the interactive terminal:

```bash
/usr/bin/python3 -I -B "$A733_TOOLS/scripts/select-host-root.py" apply \
  "${selection[@]}" --plan "$A733_MIGRATION/select-B-plan.json" \
  > "$A733_MIGRATION/select-B-receipt.json"
chmod 0400 "$A733_MIGRATION/select-B-receipt.json"
```

Historical `scripts/select-host-root.py:156` requires `BACKUP VERIFIED`, then
the displayed `SELECT activate B SERIAL CANONICAL_PARTITION_PATH PARTITION_BYTES PLAN_SHA256`.
The old selector independently hashes the full installed root; it does not trust
the staging receipt. Its CLI is at lines 345–356. It journals and publishes B
on STATE without switching the running A or rebooting. Retain selector/journal,
receipt and exact exit status off-device. If publication fails/interruption is
reported, inspect actual `boot/selected.conf` and journal before any retry.

Before a separately guarded explicit reboot, verify B visible/durable,
A still running read-only, no guest running, backups and UART capture available.
If cancelling pending B, prepare a new selector transaction with
`--intent cancel-pending --slot A --payload-manifest "/run/managed-B-install/old-manifest.json"`
and old manifest pin `c1bcc7d23d22bd487534ba17ea447055881a044350829608360aa92ca24283f2`;
review and apply that fresh plan. Do not manually copy a selector over the live
file. Healthy booted-B rollback also uses that old A manifest with intent
`activate`, through B's authenticated installed controller and its new admission
prerequisites. That is a distinct later reviewed transaction.

## Postboot gates and retained rollback

The exact normalized A raw root is
`a9715b819348a8220ec8b3b1f41ef27db677baaa96014f5d1dfb7a22597decdd`.
Its retained gzip is 143482835 bytes, SHA
`87b097024dd772d6752dfb6a605618cfb8a1ce208722d056fb1e91f8c0a6f2fa`,
original manifest pin `c1bcc7…83f2`, selector pin `3c76b9…027b`.
Native custody is `out/normalized-a-compatibility-20261112`, builder custody
`/var/lib/a733-managed-root-candidate-20261112-01/retained-normalized-A`;
see `docs/evidence/normalized-a-compatibility-custody-20261112.json:19`.
Keep these unchanged; the early historical `e1cc…` old A is a different root.

After a separately reviewed explicit normal boot, authenticate p3's B marker
`bdfde6c304edc9709bda5e4de8cb9adef9707118920a759630b01f6468362460`,
all B installed runtime bytes, original identity, NTP, fixed BOOT/kernel/DTB/EL2,
STATE/p5 and preserved manual `firstkvm` cohort. Root provenance's diagnostic DTB
must never be copied to BOOT. The recorded B boot passes these checks;
candidate A has not booted.

Continue with [managed fixture deployment](../../managed-fixture-deployment.md), which
covers explicit storage policy before runtime initialization and opt-in setup.
B's authenticated controllers add yes/no confirmation and optional writer readback;
the initial A controllers above retain mandatory readback and legacy phrases.
Actual nonroot KVM, unattended operation, guest shutdown/checkpoint/restart,
coherent backup/restore, installed maintenance interruption/retry, rollback/return,
mount/shutdown ordering and independent physical reproduction remain gates in the
[production plan](../../spi-production-readiness-plan.md). Preserve A until B works and
a fresh independent review approves any later inactive-A trial. Do not temporarily
overwrite A merely to test the new updater. Current readers accept retained A;
a separate physical old-A rollback/managed-B return trial remains pending. Reuse
accepted endurance data. Artifact checks and emulation do not establish hardware
acceptance.
