> **Historical record — not active instructions or release gates.**
> Superseded by the [manual beta scope](../../beta-scope.md) and
> [current status](../../implementation-status.md). Original observations retain
> their recorded configuration and limitations.

# Component retention and seed recovery preparation

[Evidence and exact receipts](../../evidence/component-retention-20261108.json)
record the operations below.

The first-generation raw root files have been retired from the builder after
complete encoded-copy verification and independent review. Their two gzip files
remain on both hosts. This releases **888,848,384 allocated bytes**; final builder
free space is **19,735,375,872 bytes**. The corrected components, current operator
images, installed NVMe, SD and SPI are unchanged. Full corrected NVMe admission
still needs **23,760,999,827 bytes**, plus any later source/staging changes.

The sole retired paths are beneath
`/var/lib/a733-clean-replay-20261106/host-roots-timesync-01/work/project/out/host-payload-timesync-01/`:
`root-A.ext4` and `root-B.ext4`. Each is logically 4 GiB. Actual complete gzip
expansions on both hosts and both original raw hashes passed before retirement.
The native controller held and verified both encoded copies before and after
remote execution. The builder retained and reverified both gzip files. Its
original producer receipt and manifest are unchanged.

The first source review found a process-scan race and incomplete postchecks.
Both were corrected; the revised script passed independent review and **24
fixtures on Ubuntu 22.04**, including the reproduced failure cases. The actual
plan pinned the exact four payload files and native identities. A separate
explicit apply consumed that plan. Durable per-unlink intents/results and the
completion receipt remain under
`/var/lib/a733-first-roots-retirement-20261108-01` on the builder; raw records and
reviews are in `out/component-retention-20261108` and the linked evidence.
No automatic retry is allowed for this consumed tag. Host inventory still
matches all 842 replay packages, package audit is empty, and no mapped processes
or phase mounts remain. No hardware acceptance follows from cache retention.

## Restore a historical raw root

This is developer cache recovery on the Ubuntu builder, separate from physical
NVMe installation. Keep native writers and builder jobs quiescent. The fixed
source directory is protected by its root-owned phase ancestor. Retained gzip
pins and expected raw pins are in
[the original evidence](../../evidence/production-host-roots-20261107.json).
Restore before invoking a historical consumer that needs these raw paths.

Use one selected slot, `A` or `B`, per invocation. The output must be absent.
GNU dd's exclusive creation and nofollow flags refuse an existing output or
symlink. Sparse writing avoids allocating the zero-filled remainder. This
reconstructs a regular file; it selects no block device. No NVMe or SD readback
is involved.

```bash
set -euo pipefail
SLOT=A
case "$SLOT" in
  A) RAW_SHA=4f9379aeca0044c6222fb4aa5b60dc65a32b744a4d9562061031a0b742e756d4 ;;
  B) RAW_SHA=d8921d95b96fff500cb9da236c7d2d3071d48326f8120c8dd26bb6a271ec97fd ;;
  *) exit 1 ;;
esac
BASE=/var/lib/a733-clean-replay-20261106/host-roots-timesync-01/work/project/out/host-payload-timesync-01
RAW="$BASE/root-$SLOT.ext4"
sudo test ! -e "$RAW"
sudo test ! -L "$RAW"
sudo gzip -dc "$RAW.gz" | sudo dd of="$RAW" bs=4M conv=excl,sparse,fsync oflag=nofollow status=progress
test "$(sudo stat -c %s "$RAW")" -eq 4294967296
printf '%s  %s\n' "$RAW_SHA" "$RAW" | sudo sha256sum -c -
sudo chown 1048576:1048576 "$RAW"
sudo chmod 0600 "$RAW"
```

A failed reconstruction leaves its new file for inspection; do not blindly
retry or overwrite it. Consumers must authenticate the complete raw hash. Its
host inode and creation timestamps intentionally differ from the retired file;
image bytes and ext4 metadata are recovered exactly.

## Raw-root recovery check

A fresh private-path rehearsal on Ubuntu 22.04 reconstructed root A using GNU
coreutils 8.32 with the exact flags above. Its full 4,294,967,296 bytes matched
the original raw SHA-256, with sparse allocation 444,665,856 bytes and final
UID/GID 1048576, mode 0600. Existing-file and dangling-symlink cases were refused
without changing the sentinel or creating the symlink destination. The gzip
identity remained unchanged. Only the newly created rehearsal files/directory
were removed; neither retired old raw path was recreated. Root B's compressed
stream is verified, but a new complete B-file reconstruction was not run.

## Complete old-seed recovery rehearsal

Before retirement, the old seed leaf was at
`/var/lib/a733-clean-builder/seed-probe-20261106-05/rootfs`. The coordinator found
two gaps in its existing freeze backup:
the active builder has one changed directory timestamp and four additional empty
directories; the freeze archive intentionally excludes 16 paths, including six
private files totaling 4,325 bytes. A source-list comparison alone cannot prove
complete namespace equality. Its failed comparison is retained separately.

A new recovery copy was built from the active restored builder, with only the
exact extra empty chain removed from that **copy**, the closed exclusion overlay
applied, and original directory timestamps restored to the copy. All **91,578**
paths then matched the complete original source, including contents, ownership,
modes, xattrs, hardlinks and modification times. Atime, ctime and creation time
are outside this comparison. Both protected roots remained unchanged. Only the
new rehearsal tree was removed.

Private overlay and compressed metadata are verified and retained under
`/var/lib/a733-seed-retention-20261108` on both hosts, root-owned 0700 with files
0600. No private contents are in Git. Only newly generated raw metadata duplicates
were retired after both compressed copies and their full expansions verified.
Independent review passes for the actual rehearsal and retained correspondence.
The [archive-based Method B](../../evidence/seed-archive-recovery-20261108.json) has now
also executed: pinned archive expansion, original UID/ACL/capability namespace
extraction, the actual retained finish helper and an independent full 91,578-path
comparison all pass. Its eight new provenance records are verified on both hosts;
existing Method A artifacts remain unchanged. The standalone Method A copy branch
has source review but has not been executed directly as another full copy.
Original freeze archive pins and dependency limits remain explicit.

The separately reviewed old-seed retirement completed successfully under
`/var/lib/a733-seed-leaf-retirement-20261108-01` on the builder. It removed only
that 91,578-entry source leaf through its closed sibling quarantine; every unlink
was journaled and all seven protected postchecks pass. The exact journal SHA-256
is `a266abf07ee59e206ecf0268c38030b8ec221154fedbaebbcfee57039a2a228a`; the
prepared-record SHA-256 is `c3c4aa5699a813189e6593abafcdf79eca461d2c41c07569f26fd1f36088da04`.
The full journal and per-path records remain private on the builder. The actual
reconstruction from the retained active-builder material and the separate
archive-based Method B both have complete 91,578-path proofs; Method B's
independent review passes. See the [retirement receipt](../../evidence/seed-leaf-retirement-20261108.json)
and [archive recovery evidence](../../evidence/seed-archive-recovery-20261108.json).
The probe, firmware mask, parent records, active builder and backups remain
protected. At the recorded postcheck sample, builder free space is **23,848,976,384
bytes**, exceeding the unchanged **23,760,999,827-byte** initial admission by
**87,976,557 bytes** before later validation staging. Recheck capacity before
composition. No further LV growth or admission reduction is authorized.
Native storage retains its separate 256 MiB free-space floor; another full NVMe
gzip transfer now has [verified native archive capacity](../../native-archive-retention-20261108.md);
remeasure against the retained 256 MiB floor before copying.
