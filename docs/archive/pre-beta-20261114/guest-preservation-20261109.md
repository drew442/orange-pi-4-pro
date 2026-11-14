> **Historical record — not active instructions or release gates.**
> Superseded by the [manual beta scope](../../beta-scope.md) and
> [current status](../../implementation-status.md). Original observations retain
> their recorded configuration and limitations.

# Guest preservation before physical maintenance

This is the November 9 preservation checkpoint. The original source files were
later erased during the authorized normalized installation; the off-device
archives remain intact. [Fresh counter-5 restoration and KVM restart now pass](../../evidence/normalized-guest-restore-20261110.json).

The current guest data is retained on the separate Ubuntu builder before any
new NVMe erasure. This checkpoint completes guest preservation, not host STATE
backup, new-archive restoration or release acceptance. Original source files
remain on the A733 and no partition or firmware was overwritten.

| Data | Encoded bytes | SHA-256 | Verified builder location |
| --- | ---: | --- | --- |
| Clean counter-5 `firstkvm` backup | 3,394,269 | `ba85b804690a50bd12b4e001b11cfe404896c78e70e4d87855c1523145b297e2` | `/var/lib/a733-counter5-guest-backup-20261109-01/inbox/archive.tar.gz` |
| Three stopped endurance directories, forensic copy | 10,159,229 | `fc4532873c176b7efc499b1708437cc541e2195bbb285801291ec433f644f0aa` | `/var/lib/a733-endurance-forensic-backup-20261109-01/inbox/archive.tar.gz` |

The [counter-5 record](../../evidence/current-counter5-backup-20261109.json) authenticates
source manifest `0b4b439236715ff308c9efab823159ed97fe5dfe010ed4a366091161dc1647fd`
and disk `d3faf8742f80fb83091d84464ff367d0e3f31ab8fb72ea20066ff5f8a5f83217`,
including all four checkpoint records. Both SSH transfers exited zero; the
reviewed receiver authenticated the archive, verified all members and fully read
back the published files. Its success/publication/durability flags are true.
The `verified` sibling directory retains the independent receiver output.
Actual restoration of this new archive belongs to fresh-host validation; the
older counter-3 physical restoration is separate evidence.

The [forensic record](../../evidence/endurance-forensic-preservation-20261109.json)
covers `endurance`, `.endurance-failed-retained-20261108` and
`.endurance-smoke-retained-20261108`. It preserves their original `Image`,
`initramfs.cpio.gz`, `disk.raw` and `enrollment.json`, including mismatches between
mutable disks and historical enrollment hashes. No clean checkpoint or guest
filesystem consistency is claimed. Do not repair or edit these original
archives; investigate a separate copy.

Independent review and 20 software fixtures preceded this one-off operation.
The sender authenticated the fixed A733 boot/device/guest mount and installed
storage helper, retained all three exclusive disk locks, excluded process
consumers and compared complete source names, metadata and hashes before/after
streaming. Separate kernel loop/NBD censuses were empty. The receiver first
authenticated encoded bytes, then independently decoded the stored single gzip
member and checked all 15 USTAR entries, metadata and hashes across 421,355,520
expanded bytes. It extracted nothing. Files, receipts and new destination
ancestors were fsynced; reviewed helper sources and receipts are retained beside
the builder archive. Serialized trusted-root administration remains a scope
condition.

Both backup destinations are root-private on the known fixed builder ext4 root,
UUID `bb90e190-be63-4324-a020-c80583437401`. SSH protected transport; compression
provides no at-rest encryption. The board's guest filesystem has no additional
guest directories; its global enrollment and `lost+found` directories are empty.
Complete UART, outcome, observer and telemetry preservation is recorded in the
[eight-hour workload result](../../evidence/eight-hour-endurance-result-20261109.json).

Next enter the reviewed private RAM recovery environment and preserve the
[offline compatible ROOT-A/ROOT-B/STATE cohort](../../host-state-backup.md) before
update, rollback and fresh installation. Keep the separate original host identity
archive private. Guest and host backups have different schemas and purposes.
