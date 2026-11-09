# Known NVMe BOOT maintenance writer

`scripts/write-known-nvme-boot.py` is a narrowly pinned offline component
updater for the preauthorized A733 NVMe, SPCC M.2 PCIe SSD serial
`134807190FC700149277`, 256,060,514,304 bytes, 512/512-byte sectors.
Only `/dev/nvme0n1p1` receives a writable descriptor. Its exact 536,870,912
bytes begin at namespace byte 1,048,576. GPT and p2–p5 are outside that
partition descriptor's address range; the namespace descriptor is read-only.
It does not install a whole image or personalize STATE.

The old BOOT SHA-256 is
`5860889755d7479e619e86b83bc32237e51bacd3a3fd2e2d2ebe976898f44899`;
the protected STATE-probe BOOT SHA-256 is
`cc2ad988fdc57579178766acaf09c71d688aaadec94b722593192cb24426c313`.
These come from `build/protected-state-nvme-boot.py` and the construction
record `out/serial-recovery-20261109/protected-nvme-boot-build-01.stdout`.
All five partition geometries and the five exact primary/backup GPT/MBR
extent hashes come from `out/vgic-nvme-image-20261108/build-02.stdout`.
The exact GPT hashes also bind disk/partition GUIDs, names, types and CRCs.
Generated records remain outside Git. Independent artifact review and actual
hardware boot validation are separate requirements.

## Offline prerequisites

Boot the private maintenance RAM root, retain its observed boot ID as the
caller pin, and leave all NVMe partitions unmounted. The helper requires root,
the pinned `6.6.98-a733-diag` maintenance kernel, initial host PID/user
namespaces and PID 1's mount namespace, unrestricted proc visibility, a RAM
root, and an **existing dedicated `/run` tmpfs**. Never overmount `/run` on a
running SSH maintenance host: it can hide SSH privilege-separation paths.
If capacity needs adjustment, preserve the existing mount and use an approved
remount. Allow space for the old and new 512 MiB BOOT components plus other
maintenance payloads. Root and path ancestors must be root-owned and not
writable by group/others; the immediate payload directory must be mode 0700.
Inputs and receipts must be root-owned mode 0600 single-link regular files.
The helper does not create directories or transfer artifacts.

The namespace guard opens and holds each proc namespace descriptor, compares
its inode/device against the path and PID 1, and verifies `NS_GET_NSTYPE`. For
this pinned vendor kernel, initial PID/user namespace inodes must respectively
be `4026531836` / `4026531837`, as defined by
[`PROC_PID_INIT_INO` / `PROC_USER_INIT_INO` in the pinned vendor source](https://raw.githubusercontent.com/orangepi-xunlong/linux-orangepi/2ac08e8c7cdc28abbdc5c9a9dd812f887ae9c79f/include/linux/proc_ns.h).
This rejects a nested PID namespace even when its private proc mount presents
one `NSpid` field and its own PID 1. A one-field `NSpid` and a full UID map are
secondary checks; neither alone proves the initial PID namespace. The helper
makes no generic namespace-proof claim for other kernels and does not use
`NS_GET_PARENT` permission errors as proof.

For each operation the helper checks exact device identities and geometry,
all namespace/p1–p5 mounts, swaps, holders, process descriptors, cwd/root/exe
and mapped files. It inspects every visible task's mount namespace and file
descriptors and fails on unreadable census data. Target identity is checked
before and after `O_EXCL` plus nonblocking exclusive `flock`, and after the
operation. GPT is checked before and after; a fresh check also immediately
precedes a write. This is a trusted-root maintenance procedure; it cannot
stop another privileged process deliberately changing storage after a census.

## Export and retain the live BOOT backup

Use a new root 0700 directory under `/run` for all paths below. Replace the
sample boot ID with the independently observed live RAM boot ID. Output
paths must be fresh. Export only opens p1 read-only and does not write NVMe:

```sh
python3 /run/maintenance/write-known-nvme-boot.py export-backup \
  /run/maintenance/live-boot.ext4 \
  --expected-boot-id OBSERVED-BOOT-ID \
  --report /run/maintenance/export.json
```

The export copies all 512 MiB with progress, fsyncs its RAM output and fully
hashes the backup against the old BOOT pin. A failed export is not a usable
backup. Transfer the successful backup and export report over the established
authenticated channel to retained off-device storage. A compressed backup is
acceptable if the receiving trusted host independently decompresses the
complete stream and verifies **expanded** length and SHA-256. Do not infer
success from compressed size, transfer exit status alone, or a sender hash.

After that verification, the trusted control host creates this receipt,
substituting the actual host/path and current boot ID, and supplies the SHA-256
of the exact receipt bytes through the authenticated control channel:

```json
{
  "schema": 1,
  "operation": "known-nvme-boot-offdevice-backup",
  "serial": "134807190FC700149277",
  "bytes": 536870912,
  "sha256": "5860889755d7479e619e86b83bc32237e51bacd3a3fd2e2d2ebe976898f44899",
  "export_boot_id": "OBSERVED-BOOT-ID",
  "authenticated_transfer_verified": true,
  "remote_host": "trusted-retention-host",
  "remote_path": "/private/retained/live-boot.ext4.gz"
}
```

The receipt is an authenticated caller assertion: the helper does not contact
the remote host. Transfer it back as root0600 and pin its exact file SHA-256.
Additional evidence fields, such as compressed hash, are permitted. The
current boot binding prevents silently reusing another RAM session's receipt.
Do not fabricate the receipt before the receiver verifies the saved bytes.

## Apply or reverse the component

Stage the fully expanded protected BOOT file under the protected RAM directory.
The helper authenticates the entire held source descriptor before opening
p1 for writing, verifies the current live p1 matches the old pin, then writes
exactly 512 MiB including zeros, with progress, fsync and `BLKFLSBUF`:

```sh
python3 /run/maintenance/write-known-nvme-boot.py apply \
  /run/maintenance/protected-boot.ext4 \
  --expected-boot-id OBSERVED-BOOT-ID \
  --backup-receipt /run/maintenance/backup-receipt.json \
  --backup-receipt-sha256 RECEIPT-SHA256 \
  --report /run/maintenance/apply.json
```

Readback is optional: add `--readback` to explicitly request a complete p1
SHA-256 read after flushing. The mandatory predecessor hash is an admission
check, not installation readback. The helper never hashes the whole namespace.
Save reports off-device before leaving RAM maintenance. Persisted authorization
is [the known storage record](evidence/storage-write-authorization-20261108.json);
there is no additional operator confirmation prompt.

Rollback uses the same command with `rollback` and the expanded old BOOT
backup as payload. It authenticates the old source pin and requires current
live p1 to match the **new** pin. For rollback in a later RAM boot, independently
reverify the retained old backup and issue a newly boot-bound receipt. Rollback
retains all identity, idle, GPT, bounded-write, flush and optional-readback guards.
A partially written p1 matches neither predecessor pin and is refused by both
modes. Use the explicit inspect/recover procedure below only after its independent
source review passes.

A flush or postcheck failure can leave p1 partially or fully changed. Failure
reports use unknown bytes-written once application begins; do not interpret a
failed operation as zero writes or automatic rollback. No image, successful
host test, or helper receipt establishes physical BOOT or KVM acceptance.

## Inspect and recover an interrupted write

This extension has **not performed physical partial-write recovery**. The original
writer SHA-256 `6401a4153a6bcb49c13e0fe0959383f4d2e6384c504e983d1bbe54ec3f1db975`
was independently reviewed and used for the successful physical p1 application;
that historical result does not validate this extension. Independent source review
and physical interrupted-recovery acceptance are separate gates.

Remain in the private maintenance RAM root, or boot it again if required. Preserve
the existing off-device old BOOT backup. Independently reverify its expanded
536,870,912-byte length and old SHA-256 on the trusted retention host; for a new
maintenance boot, create a newly authenticated receipt bound to its observed boot
ID as described above. Stage the verified expanded old backup and receipt in the
protected RAM directory. Do not attempt to export a partially written p1 as the
old backup: `export-backup` requires the complete old hash.

First inspect the exact fixed target; this operation takes **no payload** and opens
both namespace and p1 read-only. All existing offline, namespace, idle, identity,
geometry, exclusive-claim and GPT guards apply. Its fresh JSON report records
`current_sha256`, validated boot ID, model/serial, device-node identities, complete
partition geometry and the verified GPT extent pins. It hashes only p1:

```sh
python3 /run/maintenance/write-known-nvme-boot.py inspect \
  --expected-boot-id OBSERVED-BOOT-ID \
  --report /run/maintenance/inspect.json
```

Retain and review the successful report through the authenticated control channel.
Use its exact `current_sha256` as the caller pin in the same maintenance boot:

```sh
python3 /run/maintenance/write-known-nvme-boot.py recover-backup \
  /run/maintenance/live-boot.ext4 \
  --expected-boot-id OBSERVED-BOOT-ID \
  --expected-current-sha256 CURRENT-SHA256-FROM-FRESH-INSPECT \
  --backup-receipt /run/maintenance/backup-receipt.json \
  --backup-receipt-sha256 RECEIPT-SHA256 \
  --report /run/maintenance/recover.json
```

`--expected-current-sha256` binds only the observed current target bytes; it never
changes the allowed source. Recovery always authenticates the fixed old backup
hash `5860889755d7479e619e86b83bc32237e51bacd3a3fd2e2d2ebe976898f44899`
and requires the same pinned off-device old-backup receipt. No device or source-hash
override exists. The helper checks the caller pin after claiming p1 and again
immediately before writing, under that same held claim, after the fresh context,
identity, idle, GPT and source-descriptor checks. A changed current hash is refused
without copying. If it changes, investigate the cause and obtain a fresh inspection;
do not blindly substitute hashes until a write is accepted.

Recovery writes exactly the 512 MiB p1 descriptor, with progress, fsync and
`BLKFLSBUF`; GPT and p2–p5 remain outside its writable range. Add `--readback`
only when an explicit complete p1 readback is wanted. Save both reports off-device.
A successful recovery report establishes helper completion, not physical boot or
KVM acceptance. A failure after admission may have changed p1 and never triggers
automatic rollback; retain the reports and reinspect before any further operation.

## Host regression evidence

`sudo python3 -m unittest discover -s tests -p test_known_nvme_boot_writer.py`
exercises bounded actual regular-file copies with zeros and short writes,
prefix/suffix preservation, EOF/zero-write refusal, GPT corruption, source
metadata/hash checks, receipt binding and both forward/reverse predecessor
pins. Additional fixtures inspect without a source, interrupt a real regular-file
copy then restore the fixed old source while preserving bytes outside the emulated
partition window, reject changed or missing current-target pins, and require the
boot-bound receipt. Fixtures never open physical storage. Root is required for root-owned
source/receipt fixtures. Independent review and hardware evidence remain the
coordinator's responsibility.
