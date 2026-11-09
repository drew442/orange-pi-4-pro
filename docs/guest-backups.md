# Stopped guest backup artifacts

The sender streams an enrolled guest's manifest, checkpoint history, kernel,
initramfs and disk to another machine. The receiver authenticates the compressed
archive before decoding it, verifies every member and independently reads back
the new files. The [counter-3 guest backup](evidence/vgic-counter3-backup-20261108.json)
passes off-device transfer and receiver readback on builder and codex, including
both checkpoint records. Its [physical corrected-host restoration](evidence/corrected-host-guest-restore-20261108.json)
passes authenticated restore, actual nonroot KVM runs advancing 3 → 4 → 5,
clean shutdown and checkpoints. This establishes that specific restore; a fresh
backup of the current counter-5 disk and normalized deployment remain unperformed.

Use the complete installed tree at `/usr/lib/a733-tools` on the new immutable
A/B root or a trusted complete portable tool tree on RAM or NVMe; do not copy a
single script without its imported modules. The older published recovery SD
image has not been refreshed. See [guest tools availability](guest-tools-package.md).
The [diagnostic host KVM guest](kvm-vgic-results-20261108.md) passes core tests;
the corrected installed host also passes the recorded restore. Full release
acceptance and independent normalized-image deployment remain open.

## Prepare a consistent source

Follow [verified shutdown and checkpointing](guest-checkpoints.md), retaining the
new manifest pin in your trusted installation record. `systemctl stop` alone
does not prove a clean guest shutdown. The sender refuses a disk whose current
hash differs from that manifest: it does not silently create a checkpoint.

The source uses the checkpoint inspector's selected-NVMe checks, locked account,
stopped service, consumer census and exclusive disk flock. The lock remains held
through final file/history checks and stream flushing. Cooperative launchers
must honor it; the consumer census cannot exclude an uncooperative trusted-root
writer after its snapshot. No guest is stopped or started by the sender.

The manifest's exact bytes and every valid checkpoint record are preserved,
including orphan intents from interrupted publication. The current chain must
be complete. Limits are 1,024 records, 512 KiB per record and a 16 MiB encoded
history envelope; exceeding a limit fails rather than dropping history. Keep
older verified backups before planning any history-retention change.

## Transfer to a separate machine

The following is a reusable installed-board procedure. The linked counter-3
evidence records one physical transfer and restore; new backups need their own receipts.
Install the complete current tool tree on the board at `/usr/lib/a733-tools`.
Do not copy only the sender script to match the remote path. An older root can
use a complete trusted portable tool tree and a reviewed adapted remote absolute
path; this guide's command is for the new root.
Confirm the board's SSH host key through the trusted management procedure.
On the **separate backup machine**, use a root shell with known fixed storage,
the trusted complete portable tool tree and appropriate SSH key. Fill in these values from
trusted records:

```bash
set -euo pipefail
umask 077
set -C
: "${A733_BOARD:?Set root@verified-board-address}"
: "${A733_GUEST_NAME:?Set enrolled guest name}"
: "${A733_GUEST_MANIFEST_SHA256:?Set trusted current manifest SHA-256}"
: "${A733_BACKUP_TOOLS:?Set absolute trusted complete portable tool tree on backup machine}"
: "${A733_BACKUP_INBOX:?Set new absolute private directory on fixed backup storage}"
: "${A733_BACKUP_OUTPUT:?Set different new absolute verified output directory}"
[[ "$A733_GUEST_NAME" =~ ^[a-z]([a-z0-9-]{0,14}[a-z0-9])?$ ]]
[[ "$A733_GUEST_MANIFEST_SHA256" =~ ^[0-9a-f]{64}$ ]]
mkdir -m 0700 -- "$A733_BACKUP_INBOX"
ssh -T -o BatchMode=yes -o StrictHostKeyChecking=yes "$A733_BOARD" \
  "/usr/bin/python3 -I -B /usr/lib/a733-tools/scripts/backup-guest.py '$A733_GUEST_NAME' --manifest-sha256 '$A733_GUEST_MANIFEST_SHA256'" \
  > "$A733_BACKUP_INBOX/archive.tar.gz" \
  2> "$A733_BACKUP_INBOX/source-receipt.json"
```

The remote stdout is a pipe; the sender refuses a regular-file or terminal
stdout to prevent accidental board-local redirection. Source files are read
only and no archive is staged on SD. The backup machine writes the archive and
receipt. If SSH or the sender fails, preserve diagnostics and discard the
partial archive; do not invent a trusted hash from it. The destination must be
off-device by operator verification; filesystem checks cannot establish that.

After a successful transfer, inspect the source receipt and verify it reports
`success: true`, the expected name and your trusted manifest pin. Obtain its
archive pin only from that authenticated successful receipt:

```bash
cat "$A733_BACKUP_INBOX/source-receipt.json"
A733_BACKUP_ARCHIVE_SHA256=$(/usr/bin/python3 -I -B - \
  "$A733_BACKUP_INBOX/source-receipt.json" \
  "$A733_GUEST_NAME" "$A733_GUEST_MANIFEST_SHA256" <<'PY'
import json, re, sys
with open(sys.argv[1], 'rb') as stream:
    receipt = json.load(stream)
assert receipt['success'] is True
assert receipt['name'] == sys.argv[2]
assert receipt['manifest_sha256'] == sys.argv[3]
pin = receipt['archive']['sha256']
assert type(pin) is str and re.fullmatch('[0-9a-f]{64}', pin)
print(pin)
PY
)
/usr/bin/python3 -I -B "$A733_BACKUP_TOOLS/scripts/verify-guest-backup.py" \
  "$A733_BACKUP_INBOX/archive.tar.gz" \
  --archive-sha256 "$A733_BACKUP_ARCHIVE_SHA256" \
  --manifest-sha256 "$A733_GUEST_MANIFEST_SHA256" \
  --new-output "$A733_BACKUP_OUTPUT" \
  > "$A733_BACKUP_INBOX/receiver-receipt.json"
cat "$A733_BACKUP_INBOX/receiver-receipt.json"
```

Require final receiver `success`, `published` and `durable` all true. Record both
receipts, pins, board/SSD identity and the separate machine/storage identity in
the trusted backup inventory. Protect the archive: it contains guest data and
is compressed, not encrypted. SSH protects transport; storage access and any
at-rest encryption follow the backup machine's existing policy.

## Receiver behavior and failure handling

The receiver accepts a regular, single-link archive and requires protected
ancestors for the new output. It rejects symlinks, shared-write ancestors,
recognizable SD/removable/RAM/loop backing storage and every existing output.
Known fixed storage remains an operator requirement: a USB reader's device
name alone cannot prove what medium it contains.

It privately captures and authenticates the whole compressed archive before
gzip, tar or JSON parsing. Only five canonical regular GNU-tar members in fixed
order are accepted. Links, device nodes, alternate metadata, extra members,
multiple gzip members and encoded suffixes are refused. GNU size fields support
the guest disk's range beyond traditional ustar limits; general tar extraction
is not used. See Python's [tar formats](https://docs.python.org/3.11/library/tarfile.html#supported-tar-formats)
and [gzip stream API](https://docs.python.org/3.11/library/gzip.html#gzip.GzipFile).

The receiver reserves space for an encoded private capture plus the full
logical payload and a 256 MiB margin at each phase. Sparse zero runs reduce
actual writes but do not reduce the free-space guard. Allow space for the inbox
copy too. The verified directory retains `archive.tar.gz`, all five members,
decoded `.checkpoints` records and `backup-verification.json`. Directories are
private and files are `0400`, owned by the receiver's identity. These are backup
permissions, not a ready-to-run guest directory.

All staged files receive independent full readback before atomic no-replace
publication. Failure before publication removes only owned private staging
entries. A failure after publication preserves the visible output and reports
`published` and `durable` separately; inspect that output rather than overwriting
it. `backup-verification.json` records artifact checks, while the final receipt
records publication/durability. Neither establishes power-loss survival or a
successful restored guest.

## Restoration scope and remaining acceptance

Do not copy a backup directory over an enrolled guest. The
[fresh-destination restore controller](guest-restoration.md) validates the
destination NVMe/account, rebinds installation identity, preserves source
provenance and refuses existing files. [Pinned HTTPS delivery](guest-restore-delivery.md)
avoids board-local archive staging. The counter-3 physical restore above passed;
new archives and destinations require their own restoration checks.
[Host STATE and root preservation](host-state-backup.md), boot firmware and
pre-installation physical-media backups are separate requirements; this guest
archive does not replace them.

Acceptance must restore disposable data from the separate machine, verify its
contents and boot the restored guest using actual KVM. Record the shutdown,
transfer, readback, restoration and workload evidence together. The following
fixture tests exercise file preservation on the builder; the linked November 8
evidence separately records physical transfer and restore.

The actual pinned fixture probe runs from a protected complete checkout on the
prepared Jammy builder, using its existing authenticated input paths:

```bash
sudo /usr/bin/python3.10 -I -B tests/verify_guest_backup_arm.py \
  /home/drew/orange-pi-4-pro-build/recipe/out/host-userspace-bookworm-20261001-01/rootfs.tar.gz \
  /home/drew/orange-pi-4-pro-build/recipe/out/guest-fixture-20261104-03
```

It requires 3 GiB free fixed storage and leaves no guest or mount behind. Its
source mount/UUID/service/consumer boundaries are explicitly substituted inside
the disposable ARM fixture; the native receiver runs the actual builder storage
check. [Recorded evidence](evidence/guest-backup-arm-20261104.json) includes the
source hashes, compressed archive pin, full readback and precise limits.
