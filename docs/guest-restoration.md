# Restore a guest into a fresh destination

`scripts/restore-guest.py` authenticates a compressed guest backup, copies its
assets into a new selected-NVMe guest directory and verifies the new files.
It refuses every existing destination, including symlinks and empty directories.
It does not erase a disk, replace a guest, create an account, mount storage,
start a service or boot a VM. Physical restoration and a restored KVM workload
remain required release tests.

These examples target the **uninstalled production-readiness candidate** and
require its initialized admission runtime. This candidate is not physically
qualified. The accepted 20261109 release helper omits the new `--name` flag; its
commands and historical physical results remain in the
[deployment guide](deploy-protected-20261109.md) and
[restoration record](evidence/normalized-guest-restore-20261110.json).

## Source and destination prerequisites

First complete the [off-device backup workflow](guest-backups.md). Retain its
successful source and receiver receipts and both externally trusted pins. Do
not calculate a replacement pin from an unexplained current archive.

On the installed destination, complete [guest storage and account activation](guest-activation.md).
Use the same guest name, UID and GID recorded in the backup. The destination
guest filesystem UUID may differ. Verify the real destination namespace and
UUID from the trusted installation record; do not infer them from an example.
The explicit target name must exactly match the authenticated backup; account
remapping and guest renaming are not implemented. The candidate takes admission
and the guest lock before reading the archive and refuses managed guest names.

Keep all guests using that identity stopped. The controller requires the fixed
guest service to be inactive/dead with zero process IDs, or genuinely absent
under the documented manual-launch workflow. It also refuses any process whose
real, effective, saved or filesystem UID equals the guest UID, including an
unreaped process. This catches an old manually launched guest that still holds
an unlinked disk. Namespace, process visibility and census errors fail closed.
The census is bounded and remains a snapshot; trusted root must exclude new
uncooperative processes throughout restoration.

Use the complete installed tree at `/usr/lib/a733-tools` on the new immutable
A/B root or a trusted complete portable tool tree on RAM or NVMe. The older
published recovery SD image has not been refreshed. See
[guest tools availability](guest-tools-package.md). The
[diagnostic KVM guest](kvm-vgic-results-20261108.md) and its
[counter-3 off-device backup](evidence/vgic-counter3-backup-20261108.json) now pass;
[physical counter-3 restoration and KVM advancement through 4/5](evidence/corrected-host-guest-restore-20261108.json)
also pass. [The separately preserved counter-5 archive now also restores on normalized A
and runs KVM through 6, checkpoint and restart to 7](evidence/normalized-guest-restore-20261110.json). Choose [direct HTTPS delivery](guest-restore-delivery.md)
to avoid local encoded staging. For the local-file mode below, copy the verified archive from the separate backup
machine to a **protected directory on the selected NVMe**, outside every enrolled
guest directory. Its ancestors must be root-owned without shared write access;
the archive must be a root:root, single-link regular file with mode `0400`.
Use a private directory on state or guest storage, not SD or RAM. Verify the
staged encoded hash against the successful authenticated source receipt.

Local-file mode requires archive staging. Allow room for that
archive plus the full logical kernel/initramfs/disk, history and a 256 MiB margin
for the new guest. Sparse zero runs do not relax the free-space guard. A large
backup may not fit alongside its restored disk on the nominal 250 GB NVMe;
use pinned HTTPS delivery for that case. Full-capacity resource checks and the
actual deployment/recovery procedure still require board validation.

Never delete an existing guest to make this command pass. Replacement of a
physical installation or disposal of old guest data needs a separately reviewed
plan, verified backups and any required exact-target authorization.

## Prepare, review and apply

The following is the controller interface; the linked physical record retains
the earlier actual invocation. A fresh destination still requires its own
receipts. In a destination root shell, set these variables from trusted records:

```bash
set -euo pipefail
umask 077
set -C
: "${A733_GUEST_TOOLS:?Set absolute trusted complete installed or portable tool tree}"
: "${A733_RESTORE_GUEST_NAME:?Set guest name matching the authenticated backup}"
: "${A733_RESTORE_ARCHIVE:?Set absolute protected selected-NVMe archive path}"
: "${A733_BACKUP_ARCHIVE_SHA256:?Set authenticated source receipt archive pin}"
: "${A733_BACKUP_MANIFEST_SHA256:?Set trusted backed-up manifest pin}"
: "${A733_DESTINATION_GUEST_UUID:?Set verified destination p5 UUID}"
/usr/bin/python3 -I -B "$A733_GUEST_TOOLS/scripts/restore-guest.py" prepare \
  --name "$A733_RESTORE_GUEST_NAME" \
  --archive "$A733_RESTORE_ARCHIVE" \
  --archive-sha256 "$A733_BACKUP_ARCHIVE_SHA256" \
  --manifest-sha256 "$A733_BACKUP_MANIFEST_SHA256" \
  --guest-uuid "$A733_DESTINATION_GUEST_UUID" \
  > /run/guest-restore-plan.json
chmod 0400 /run/guest-restore-plan.json
cat /run/guest-restore-plan.json
```

Preparation writes no guest storage. It checks the selected host/namespace and
destination UUID before reading archive payloads. It authenticates every encoded
byte before any gzip, tar or JSON parser, then scans the complete bounded backup
and checks all payload hashes. Review the source archive/manifest pins and
identity, guest name/resources/UID/GID, destination UUID/storage, new manifest
pin and exact history envelope hash/count. The compact envelope record binds
every preserved history entry without overflowing the 64 KiB plan limit.

Apply the same inputs and protected plan from an interactive TTY:

```bash
/usr/bin/python3 -I -B "$A733_GUEST_TOOLS/scripts/restore-guest.py" apply \
  --name "$A733_RESTORE_GUEST_NAME" \
  --archive "$A733_RESTORE_ARCHIVE" \
  --archive-sha256 "$A733_BACKUP_ARCHIVE_SHA256" \
  --manifest-sha256 "$A733_BACKUP_MANIFEST_SHA256" \
  --guest-uuid "$A733_DESTINATION_GUEST_UUID" \
  --plan /run/guest-restore-plan.json
```

The controller repeats preparation and rejects any stale plan. It asks for
`SOURCE BACKUP VERIFIED` and an exact `RESTORE` phrase containing the guest name,
archive pin, destination UUID and plan pin. It shares enrollment's RAM lock to
serialize publication. No service or account state is changed.

The archive remains protected by a shared flock and retained descriptors. During
a second full scan, the controller writes only new private files under the
destination's protected `.enrollment` directory. Each new file receives full
independent readback and fsync. History is copied exactly with bounded descriptor
use; kernel/initramfs become root-owned `0444`, the disk becomes guest-owned
`0600`, history/manifest are root-owned `0400`, and the final directory is
root:guest-group `0710`. Storage, UUID, source, account, process, service and lock
guards are checked again before atomic no-replace publication.

Retain the final receipt only after `success`, `published` and `durable` are all
true. Save its new `manifest_sha256` in the destination's trusted record; this
becomes the pin for later inspection, checkpoints and backups.

## Provenance, interruptions and verification

The new manifest keeps the original configuration and exact payload hashes,
sets the destination guest UUID and adds `restoration` with the source archive
pin, source manifest pin and source UUID. It begins a new checkpoint chain. All
old valid checkpoint records, including interrupted-publication intents, are
preserved as history. Future checkpoints preserve restoration provenance and
link to the new manifest. Keep the source backup off-device: its exact current
manifest remains the authoritative predecessor record.

Failure before publication cleans only owned staging files. Foreign entries
are preserved and `staging_retained` indicates incomplete cleanup. A failure
after rename preserves the visible guest directory and reports `published`
separately from `durable`. Do not retry by deleting or overwriting that directory.
Inspect the receipt, trusted expected pin, directory metadata and complete file
hashes before planning recovery. Fsync and interruption fixtures do not prove
physical power-loss recovery; see Linux [fsync](https://man7.org/linux/man-pages/man2/fsync.2.html)
and [renameat2](https://man7.org/linux/man-pages/man2/rename.2.html) semantics.

Regenerate and review the [guest unit](guest-enrollment.md) from the restored
configuration and use the new manifest pin. Before release acceptance, actually
boot the restored guest with KVM, verify its expected data and networking, shut
it down cleanly, restart it and record the result. Dry-run hashes and unit
parsing cannot establish these outcomes.

The software probe uses the pinned fixture on the prepared Jammy builder:

```bash
sudo /usr/bin/python3.10 -I -B tests/verify_guest_restore_arm.py \
  /home/drew/orange-pi-4-pro-build/recipe/out/host-userspace-bookworm-20261001-01/rootfs.tar.gz \
  /home/drew/orange-pi-4-pro-build/recipe/out/guest-fixture-20261104-03
```

It authenticates the rootfs, creates actual disposable ARM accounts, checkpoints
and backs up the fixture, and restores all assets with actual copy/readback and
ownership checks. Admission/storage/namespace/UUID/service/UID-census/TTY boundaries
are explicitly substituted inside that fixture. The source and destination are
on the same builder. No guest is executed and no hardware acceptance follows.
The [ARM evidence](evidence/guest-restore-arm-20261104.json) records the exact
source hashes and results; a separate [native process census](evidence/guest-restore-consumers-20261104.json)
confirms refusal while a real UID-60001 child exists and acceptance after it exits.
