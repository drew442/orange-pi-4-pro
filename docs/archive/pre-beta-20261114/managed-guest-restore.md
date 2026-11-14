> **Historical record — not active instructions or release gates.**
> Superseded by the [manual beta scope](../../beta-scope.md) and
> [current status](../../implementation-status.md). Original observations retain
> their recorded configuration and limitations.

# Restore a managed guest

This command is installed on F349 and the earlier `4334a22` host. The first supported restoration
of the disposable quarantined fixture committed successfully and retained the old
payload/lifecycle; fresh-boot recovery and later managed execution remain separate
gates. Use the matched root and deployment guide. [Status](../../implementation-status.md)
distinguishes source checks, actual restoration and guest hardware acceptance.

## Select the cohort

Use the successful [managed backup sender receipt](../../guest-maintenance-admission.md#managed-backup-boundary)
and an independently trusted manifest pin. The six-member managed archive has
the disk, immutable inputs, manifest, checkpoint history and lifecycle evidence.
Artifact verification alone does not authorize a destination restore. The legacy
manual guest archive/restore procedure cannot restore this managed profile.

The destination must already register this guest on the same physical STATE/p5
pair, with matching name, UID/GID and profiles. The current protected storage
policy must match the actual NVMe model, serial, size and filesystem UUIDs.
`firstkvm` remains manual. Storage-identity migration and new-name enrollment are
not performed by this command.

Read the selected policy and check the intended guest name before proceeding:

```sh
cat /run/a733-state/config/guest-storage.json
```

Place the archive in a private directory on NVMe STATE or guest storage, outside
the guest's own payload directory and restore transaction namespaces. Require
root:root ownership, mode 0400, a regular file with one link and no symlink path
components. Keep a verified off-device copy and its sender receipt. Do not
derive trust merely by hashing an arbitrary uploaded file.

## Execute once

Stop and checkpoint any manually managed VM using its existing procedure.
Restore itself stops managed supervisors and obtains shutdown exclusion; an
active manual/shared holder causes refusal. Managed stopping can checkpoint
legitimate guest changes before the requested older archive is restored.

Run in the foreground or use a helper unit outside `a733-managed-*.service`.
That namespace is reserved for guest supervisors: maintenance stops every matching
unit, including a mistakenly named restore helper. For example, use
`a733-trace-fixture-restore-20261113-02.service` for a diagnostic helper.
A disappeared unit or default `Result=success` with `LoadState=not-found` does
not prove a restore; require its final data-commit receipt.

Run as root, replacing the path and both trusted hashes:

```sh
python3 -I -B /usr/lib/a733-tools/scripts/restore-managed-guest.py autokvm \
  --archive /run/a733-state/backups/autokvm.tar.gz \
  --archive-sha256 TRUSTED-ARCHIVE-SHA256 \
  --manifest-sha256 TRUSTED-MANIFEST-SHA256
```

The command verifies the existing policy, closes admission for shutdown and
borrows an actual readonly exclusive lease. It activates the exact p5 if needed
before opening the archive. That activation can replay the journal or adjust
traversal permissions; its separate `activation` report remains visible even
if archive authentication later fails. It never initializes/adopts/reopens the
admission runtime. A missing/unsafe runtime requires preparation/recovery
diagnosis, not deletion or reconstruction of runtime files.

Encoded authentication, decoded file writes and file verification show progress.
Byte totals alone are not authentication or commit acknowledgements. Record the
transaction UUID printed before publication and retain the final structured
receipt, including every failure or uncertain outcome.

An actual new restoration completes only with `success: true` and
`data_committed: true`. It preserves the exact historical cohort, the current
enabled flag and all unrelated registry entries. Old payload/lifecycle trees
remain protected under `/run/a733-guests/.a733-restores/TRANSACTION_UUID/`.
STATE provenance is under `/run/a733-state/guest-restores/TRANSACTION_UUID/`.
Do not edit, delete or manually move these objects to force a retry.

After a newly committed restore, reboot normally. Fresh bootstrap recreates
accounts/markers, validates the restored ready cohort and opens admission only
after preparation succeeds. Enabled guests may then start automatically. An
unavailable guest or failed preparation must leave management reachable; inspect
the receipt and service journal. Actual nonroot KVM, persisted counter, guest-owned
shutdown, checkpoint and restart are separate physical qualification checks.

## Interrupted or uncertain outcome

Do not repeat a new request automatically. Keep the archive, old data, stages,
provenance and failed receipts. Admission remains shutdown-closed. A boot with
an incomplete intent refuses preparation before registry/marker/account changes
or guest launches. Normal failed-service teardown can unmount p5; restore's
guarded shutdown activation recovers that mount for an explicit resume.
The stop hook also runs after failed startup under
[systemd 252's service semantics](https://www.freedesktop.org/software/systemd/man/252/systemd.service.html#ExecStopPost=).

Inspect the failing service without treating missing status as a guest failure:

```sh
journalctl -b -u a733-guest-storage.service -u a733-guest-prepare.service --no-pager
```

The boot census failure receipt identifies the transaction it was examining.
Use that UUID or the saved restore receipt, not a "latest" directory heuristic.
Resume explicitly with the same name, archive and pins:

```sh
python3 -I -B /usr/lib/a733-tools/scripts/restore-managed-guest.py autokvm \
  --archive /run/a733-state/backups/autokvm.tar.gz \
  --archive-sha256 TRUSTED-ARCHIVE-SHA256 \
  --manifest-sha256 TRUSTED-MANIFEST-SHA256 \
  --resume TRANSACTION_UUID
```

Resume reauthenticates the source, physical destination, actual locks and exact
reachable filesystem phase. It re-establishes intent/phase durability before
another canonical mutation. Changed disk/head bytes, conflicting names/inodes,
registry conflicts, unexpected mounts or unknown publications refuse; they are
not repaired by weakening checks. A harmless pre-intent stage grants no resume
authority: an explicit new request may use a new UUID, leaving the old private
stage retained. Never replace old STATE alone over a newer managed disk.

A data commit can be acknowledged even when later descriptor cleanup fails.
Such a command reports failure while retaining `data_committed: true` and cleanup
uncertainty. Do not blindly restore again. An explicit completed resume observes
the immutable historical commit before shutdown, mount or archive access, and
does not stop a guest that has since run. `historical: true` is not a current
guest-readiness claim; the nested fresh-boot requirement describes the original
restore boundary and does not by itself require another reboot.

Retained old trees consume NVMe space. Keep them through qualification and
off-device backup custody. There is no automatic pruning or destructive cleanup
in this procedure. Preserve the recovery SD and original/calibrated SPI backups.
The [transaction contract](../../managed-restore-design.md) defines publication,
durability, interruption and readonly census boundaries.
Linux documents the separate directory flush required by
[fsync(2)](https://man7.org/linux/man-pages/man2/fsync.2.html) and the
[renameat2 no-replace operation](https://man7.org/linux/man-pages/man2/rename.2.html)
used by that contract. These syscall guarantees do not replace target hardware
validation or establish a guest-owned shutdown.
