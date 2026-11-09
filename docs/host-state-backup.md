# Preserve installed STATE and both roots before reinstalling

**UNEXECUTED procedure; independent preparation review passes.**
[Review evidence](evidence/offline-preservation-preparation-20261109.json). This is preparation for
same-board preservation, not a tested restore. The
[November 9 workload result](evidence/eight-hour-endurance-result-20261109.json)
passes the 8-hour criterion; both phases of final preservation are complete and
reviewed. These guest/STATE/root backups and subsequent restoration remain
unperformed. The former 02:11/02:16 timing was only a planned 24-hour endpoint
and is no longer a prerequisite.

The A733 uses a 2 GB SD and nominal 250 GB NVMe; keep the minimal host and zero SD writes. These backups read only NVMe and stream directly to private
fixed builder storage: codex has only about 425 MB free. No full raw staging,
SD staging, filesystem mount, journal replay or filesystem repair is permitted.
Preserve raw dirty STATE before any journal replay, including read-only mounts.

## Retain application data and evidence first

Before leaving the installed host, follow [stopped guest backup](guest-backups.md)
for the current counter-5 `firstkvm` disk: verified shutdown, checkpoint, trusted
current manifest pin, authenticated sender receipt and independent receiver
readback. The [counter-3 archive](evidence/corrected-host-guest-restore-20261108.json)
was physically restored and advanced through 4 and 5; that archive omits later
data. The [fresh counter-5 backup](evidence/current-counter5-backup-20261109.json)
now passes source authentication and independent durable receiver readback on the
builder. Its manifest pin is `0b4b439236715ff308c9efab823159ed97fe5dfe010ed4a366091161dc1647fd`.
Recheck that the guest is still stopped and unchanged before relying on it;
a later run/checkpoint requires a matching fresh backup.

Retain the completed/current and `.endurance-failed-retained-20261108` guest
directories, including mutable disks, enrollment metadata and checkpoint records.
Use the guest protocol where valid enrollment/checkpoints permit it. Otherwise retain a labeled
stopped forensic copy with paths, ownership, sizes and hashes; never manufacture a clean checkpoint. The [three endurance-directory copies](guest-preservation-20261109.md)
now pass source stability and independent stored-archive verification on builder;
all four current guest directories are accounted for. Their original enrollment
metadata is preserved verbatim. Keep originals until this inventory is complete; p5 is outside this backup.

Copy native UART relay and bridge logs, full collector telemetry, journals,
observer/CPU exits and `/run/corrected-kvm` output off-board before RAM loss.
Review boot/run IDs, errors and truncation. The outcome preserver captures
observations, **not guest disks**. Keep its snapshots and native sources. Retain the existing BOOT backup, protected SD baseline/artifact,
trusted root manifests, source closure and private identity archive described in
[deployment](deploy-protected-20261109.md); keep secrets outside Git/delivery.

## Admit only the known offline source

Use the [reviewed RAM entry procedure](deploy-protected-20261109.md#enter-the-existing-private-ram-recovery-environment)
after guest/evidence retention. Verify recovery DHCP address through UART/lease
and pinned recovery SSH key (`HostKeyAlias=192.168.1.116`, fingerprint
`SHA256:Epfegy9GsJ6ng4/0EH3dZmir7beQj5uYTnIbE4JPfEI`). Never learn a replacement
key from the network. Record the RAM boot UUID. Require AArch64
`6.6.98-a733-diag`, RAM `/`, tmpfs `/run`, no swap, stopped guest processes,
no mounted NVMe partition, no holders/consumers and no pending I/O. Disable any
maintenance automounter and exclude concurrent root/device users throughout.

Exact source: `/dev/nvme0n1`, **SPCC M.2 PCIe SSD**, serial
`134807190FC700149277`, **256060514304 bytes**, 512/512 sectors.
Fixed geometry from `scripts/nvme_layout.py`:

| Source | Offset bytes | Exact raw bytes |
| --- | ---: | ---: |
| p2 ROOT-A | 537919488 | 4294967296 |
| p3 ROOT-B | 4832886784 | 4294967296 |
| p4 STATE | 9127854080 | 8589934592 |

STATE UUID is `360dfb39-f49f-487e-b307-0bb887c854c5`; all five GPT partitions
must match the existing policy. Reuse the trusted complete recovery source tree
at `/run/a733-tools` (stage/authenticate its full closure first, not one script).
In the **RAM board root shell**, invoke the existing read-only policy:

```sh
umask 077
python3 -I -B - > /run/a733-preservation-source.json <<'PY'
import importlib.util, json
p = '/run/a733-tools/scripts/host-recovery-target.py'
s = importlib.util.spec_from_file_location('preservation_policy', p)
m = importlib.util.module_from_spec(s)
s.loader.exec_module(m)
print(json.dumps(m.snapshot('/dev/nvme0n1', 'SPCC M.2 PCIe SSD',
    '134807190FC700149277', 256060514304,
    '360dfb39-f49f-487e-b307-0bb887c854c5', 'A'), sort_keys=True))
PY
```

Require exit zero and review GPT identities. This checks RAM/no-swap, device
identity, partition geometry, mounts/holders and STATE superblock without mounts.
It does not exclude raw consumers or establish an exclusive maintenance session. Independently inspect `/proc/*/fd`,
`/proc/*/mountinfo`, process/service inventory and namespace/partition `inflight`
counters; require no device consumers and zero in-flight I/O before proceeding.
Missing evidence is a stop. Retain those observations and the boot UUID off-board.

Then make the namespace **and all five partitions** block read-only using
`blockdev --setro /dev/nvme0n1` and each `/dev/nvme0n1p1` through `p5`.
Require `blockdev --getro` to return `1` for every node. These ioctls change
kernel protection, not media bytes. Keep them read-only; the recovery policy subsequently refuses these block flags.
Never run recovery `apply`, mount or fsck before preserving STATE. Privileged
noncooperating writers can defeat protection; exclude concurrent administration.

## Stream directly to the builder and verify

Run this in a **codex Bash shell**. Use the existing local private key only on
codex; builder needs no board credential. Verify the builder destination is on
known fixed storage with at least 17 GiB free plus separate guest-backup space.
Do not use RAM/removable/SD storage. Both SSH host keys must already be pinned.
`sudo -n` must work on builder; no package installation is required.

```bash
set -euo pipefail
: "${A733_RAM_IP:?Set verified recovery address}"
A733_SAVE=/var/lib/a733-pre-reinstall-preservation-20261110-01
A733_SSH=(ssh -T -i /home/drew/.ssh/id_ed25519 -o IdentitiesOnly=yes
  -o BatchMode=yes -o StrictHostKeyChecking=yes
  -o HostKeyAlias=192.168.1.116 -o ConnectTimeout=5 "root@$A733_RAM_IP")
A733_BUILDER=(ssh -T -o BatchMode=yes -o StrictHostKeyChecking=yes
  drew@build.drewnet.online)
"${A733_BUILDER[@]}" "sudo -n mkdir -m 0700 '$A733_SAVE'"
"${A733_SSH[@]}" 'cat /run/a733-preservation-source.json' |
  "${A733_BUILDER[@]}" "sudo -n dd of='$A733_SAVE/source.json' conv=excl,fsync status=none"
"${A733_SSH[@]}" 'cat /proc/sys/kernel/random/boot_id' |
  "${A733_BUILDER[@]}" "sudo -n dd of='$A733_SAVE/ram-boot-id' conv=excl,fsync status=none"
for part in 2 3 4; do
  [[ $("${A733_SSH[@]}" "blockdev --getro /dev/nvme0n1p$part") == 1 ]]
  # Redirect stdin so gzip never treats a block-device pathname as a file operand.
  "${A733_SSH[@]}" "gzip -1 -c < /dev/nvme0n1p$part" |
    "${A733_BUILDER[@]}" "sudo -n dd of='$A733_SAVE/p$part.img.gz' bs=4M conv=excl,fsync status=progress"
  "${A733_BUILDER[@]}" "sudo -n python3 -I -B - '$part' '$A733_SAVE'" <<'PYVERIFY'
import gzip, hashlib, json, os, pathlib, sys
part = int(sys.argv[1])
assert part in (2, 3, 4)
expected = 8589934592 if part == 4 else 4294967296
base = pathlib.Path(sys.argv[2])
archive = base / f'p{part}.img.gz'
encoded, raw = hashlib.sha256(), hashlib.sha256()
encoded_bytes, raw_bytes = archive.stat().st_size, 0
with archive.open('rb') as stream:
    while data := stream.read(4 * 1024**2):
        encoded.update(data)
print(f'Verifying {archive.name}: {expected} raw bytes', file=sys.stderr, flush=True)
with gzip.open(archive, 'rb') as stream:
    while data := stream.read(4 * 1024**2):
        raw_bytes += len(data)
        assert raw_bytes <= expected
        raw.update(data)
        if raw_bytes % (512 * 1024**2) == 0 or raw_bytes == expected:
            print(f'Verified {raw_bytes}/{expected} raw bytes', file=sys.stderr, flush=True)
assert raw_bytes == expected
receipt = dict(partition=part, expected_source_bytes=expected,
    archive_bytes=encoded_bytes, archive_sha256=encoded.hexdigest(),
    decoded_bytes=raw_bytes, decoded_sha256=raw.hexdigest(),
    raw_hash_origin='receiver-derived; no independent source hash', success=True)
with (base / f'p{part}.receipt.json').open('x') as stream:
    os.chmod(stream.fileno(), 0o600)
    json.dump(receipt, stream, sort_keys=True)
    stream.write('\n')
    stream.flush()
    os.fsync(stream.fileno())
print(json.dumps(receipt, sort_keys=True))
PYVERIFY
done
"${A733_BUILDER[@]}" 'sudo -n sync'
```

Require both SSH pipeline exits and receiver verification to pass. Builder stderr
shows compressed-byte receive progress; no full raw file touches codex or builder.
This makes one source read and one full receiver decode per partition. The raw hash is receiver-derived, not an independently measured source hash. Gzip CRC,
EOF and exact expanded size validate the successful authenticated stream.
Any SSH/read/gzip/space failure leaves an incomplete set: retain diagnostics,
mark failed and use a fresh private directory for retry. Do not erase the source.
Recheck boot ID, block RO, mounts, consumers and idle state after transfer;
retain these observations and an index hashing all reports. Record builder
filesystem/device identity, free space, UTC times and receipt sizes/hashes in
the private inventory. The full archive readback above validates stored bytes;
keep another verified off-device copy where practical. Compression is not encryption.

## Restore pairing and the remaining implementation gap

These partition artifacts exclude BOOT, GPT bytes, SD/SPI and p5. Restore STATE with the matching old p2/p3
roots and compatible boot files/identifiers: its selector authenticates an old
root and may change after journal replay. **Never blindly restore old STATE over
the normalized root pair.** Keep the original dirty STATE archive immutable;
perform replay only on a restored copy after preserving its original bytes.

`recover-host-root.py` authenticates a retained root and changes selection; it
neither restores partition images nor backs them up. The separate
[fixed paired restore controller](known-host-state-restore.md) now passes
independent code/procedure review and the full privileged Ubuntu suite. It
binds all three archives to an off-device verified index, claims all targets
before writing roots then STATE, and reports partial attempts conservatively.
Routine readback is optional; use it for physical restoration acceptance.

No backup or host restoration has been performed by this preparation. Preserve
the complete set, test interrupted retry and verify same-board boot,
configuration/management and an actual KVM guest before claiming host restore.
