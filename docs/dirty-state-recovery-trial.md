# Physical dirty-STATE recovery trial

**November 10 physical crash, protected export, normal recovery and KVM pass.**
[Execution evidence](evidence/dirty-state-physical-20261110.json) pins 177 records;
[verified private custody](evidence/dirty-state-physical-custody-20261110.json)
preserves their second copy. The full dirty STATE image and journal artifacts
are separately retained privately on the builder. The existing [block-protection evidence](evidence/state-block-protection-20261109.json)
uses software loop fixtures and synthetic orphan metadata. It does not prove
recovery from an actual interrupted journal on this board. This trial addresses
the existing physical recovery finding; it is separate from normal installation
and its optional readback policy.

The [November 9 workload result](evidence/eight-hour-endurance-result-20261109.json)
passes the current 8-hour criterion. Post-workload NVMe health and full-card SD
comparison are recorded there. First preserve current guests, identity, failure
evidence and the complete compatible
[ROOT-A/ROOT-B/STATE cohort](host-state-backup.md). Keep that original cohort
immutable off-device. The known storage authorization persists; these are
preconditions for execution, not another permission request.

## Bound the disposable target

Use the known SSH RAM installer and continuous independent UART capture. The
only filesystem deliberately dirtied is the restored disposable copy on
`/dev/nvme0n1p4`: 8,589,934,592 bytes at offset 9,127,854,080, UUID
`360dfb39-f49f-487e-b307-0bb887c854c5`. Its parent must be the SPCC M.2 PCIe SSD,
serial `134807190FC700149277`, exactly 256,060,514,304 bytes. Preserve BOOT, GPT,
both matching root slots and GUESTS. Do not use another disk or a loop fixture
as physical acceptance evidence.

Start from the independently verified [paired restore](known-host-state-restore.md),
including its successful terminal receipt. Bind the actual RAM boot ID, source
cohort/index hash, selected root marker, current tool hashes and live device
identity to a fresh execution record. Recheck RAM root, no swap, all visible
mount namespaces, holders, raw-device consumers and idle I/O. Existing
`host-recovery-target.py` rejects block-RO flags: complete its unchanged
preflight with all flags zero before protecting the partitions that this trial
must not write. Do not weaken that policy to accommodate a mounted test target.

Before the crash, revalidate p4 immediately before mounting it writable. Keep
p1/p2/p3/p5 block read-only; the parent namespace and p4 must permit the intended
p4 writes. No other NVMe filesystem may be mounted. Record all flags, mounts
and SD counters. These flags constrain accidental partition access; they do not
exclude privileged raw writes through the whole namespace. Exclude concurrent
administration throughout.

## Create and preserve a real interrupted journal

1. Mount only the disposable p4 copy `rw,nodev,nosuid,noexec` at a fresh private
   RAM mountpoint. Validate STATE identity and the selector/root pairing before
   writing. Create a fresh trial marker under its existing `var` tree using
   exclusive creation, a recorded nonce and bounded content. Fsync the marker
   and its parent directory; retain its expected content hash off-device.
2. Retain the exact write/crash recipe and arm the independent UART observer
   before executing it. With this sole disposable filesystem still mounted,
   use a single SysRq `b` to reset without a clean unmount. Do not send `s` or
   `u` first for this deliberate fault injection. Ordinary recovery and boot
   tests continue to use their documented clean shutdown paths. Linux documents
   the different [SysRq semantics](https://docs.kernel.org/admin-guide/sysrq.html).
3. Boot back into the same private **RAM installer**, which must not mount p4.
   Record its new boot ID and inspect the raw ext4 superblock and internal
   journal before any mount. Capture the actual journal bytes and their mapping
   from inode 8 using authenticated tools. Require evidence of a genuinely dirty
   journal, including RECOVER state and its nonzero journal start; a flag set
   by hand or synthetic orphan entry is insufficient. If no pending committed
   transaction is established, report that limit rather than claiming replay
   of transaction contents. The pinned [JBD2 recovery implementation](https://github.com/orangepi-xunlong/linux-orangepi/blob/2ac08e8c7cdc28abbdc5c9a9dd812f887ae9c79f/fs/jbd2/recovery.c#L268)
   distinguishes an empty log from its scan/revoke/replay path.
4. Protect the exact namespace and partitions block-RO, and preserve the raw
   dirty p4 stream directly off-device using the existing backup protocol.
   Authenticate its archive and decoded byte count/hash at the receiver. This
   is a labeled trial-state archive, never a replacement for the original
   paired backup or an index falsely claiming a new three-partition export.
   Keep the dirty archive immutable even after successful replay.

## Exercise protected inspection and normal boot

After verifying the immutable dirty archive, revalidate the idle disk identity
and clear **only the parent namespace** block-RO flag. Verify the parent is RO=0
and each of p1/p2/p3/p4/p5 is still RO=1. The unchanged exporter requires parent
RO=0 and accepts p4's original RO=1; leaving the parent protected would refuse
before its mount lifecycle. Do not weaken that preflight or clear p4 protection.
Run the unchanged [offline journal exporter](offline-host-journal.md) against
the preserved dirty source with its actual RAM boot ID and authenticated script.
Retain its stderr, exit status and archive validation, block/sysfs RO transitions,
mount options, unchanged Linux NVMe/SD write counters and final cleanup state.
It may refuse stale/inconsistent journal-directory contents: preserve that
refusal and remain in RAM; never retry by removing RO protection or using a
writable mount. An archive refusal alone is not a failed protection claim.

After preserving the dirty source and confirming no mounts/consumers remain,
restore the known protected SD profile through the reviewed writer. Clear only
the known kernel block flags needed for the subsequent boot after revalidation;
do not repair or mount STATE writable in RAM. Cleanly restart the RAM system
into the retained compatible host pair, capturing the entire console.

The production sequence is `configs/host-init:283`: block RO, `ro,noload` probe,
STATE validation, normal unmount, same-device/internal-journal/idle revalidation,
restoration of original write eligibility, then authorized RW mount. Retain
console evidence of the physical recovery, selected root, STATE/configuration,
pinned SSH identity and the trial marker. The pinned [ext4 mount implementation](https://github.com/orangepi-xunlong/linux-orangepi/blob/2ac08e8c7cdc28abbdc5c9a9dd812f887ae9c79f/fs/ext4/super.c#L5291)
reports recovery completion; a message alone does not establish restored content.
Run an actual KVM guest and retain its CPU/disk/network and clean-exit evidence.
Reconcile SD counters with the intentional maintenance writes; this trial is
not one of the final ten clean warm or ten cold boots.

## Execution bindings for future trials

The dated plan/helper below are retained evidence, not reusable authorization for
another boot. Bind a fresh actual RAM boot, exact compatible cohort, source hashes
and target before independent review. Never run a guessed boot ID or placeholder
backup pin. Failures require retaining RAM evidence and restoring the complete
original cohort, not splicing old STATE onto different roots.

## November 10 physical result

The original 4/4/8 GiB ROOT-A/ROOT-B/STATE cohort was restored and flushed from
verified streams in RAM boot `597391e8-1ad5-4a04-a27d-f21e243fa960`. Optional
readback was omitted; no raw readback hash match is claimed. GPT/BOOT/p5/SD/SPI
were preserved. The unchanged target preflight passed with all RO flags zero;
STATE initially had RECOVER false.

Attempt 01 refused before mount/marker/GO because its UART guard assumed 4:64.
Actual `ttyS0` is 241:0 on `2500000.uart`, driver `uart-ng`. The refusal and unchanged
STATE were retained. After independently reviewed correction and resetting only
the four preparation RO flags, attempt 02 emitted a fresh nonce-bound UART ARM.
The coordinator atomically published GO. The worker mounted only disposable p4,
created the exclusive marker, fsynced it and its parent, then directly requested
one SysRq `b` without intervening sync/unmount. UART records mount and reset.

Postcrash RAM boot `ed6c34cf-b7a0-48ed-b512-2cba29878b3a` mounted no NVMe filesystem.
After unchanged RO0 preflight, all six nodes were protected RO1. STATE now had
RECOVER true; the extracted journal began at block 429, sequence 4037. Offline
parsing of the preserved regular file found descriptor 429 and matching commit
437, then end 438. The marker filename occurs in journal data block 435.
Independent offline transaction checksums were not validated.

The one-off raw inspector **refused** its final counter-equality check: three
zero-sector writes/flushes appeared, with written sectors/discards unchanged.
Its dump and all diagnostics are retained; no successful-inspector claim is made.
[e2fsprogs 1.47.0](https://github.com/tytso/e2fsprogs/blob/v1.47.0/lib/ext2fs/unix_io.c)
calls fsync when opening an I/O channel even read-only. The pinned kernel
[constructs zero-sector flush writes](https://github.com/orangepi-xunlong/linux-orangepi/blob/2ac08e8c7cdc28abbdc5c9a9dd812f887ae9c79f/block/blk-flush.c#L449)
and [allows them on RO devices](https://github.com/orangepi-xunlong/linux-orangepi/blob/2ac08e8c7cdc28abbdc5c9a9dd812f887ae9c79f/block/blk-core.c#L473).
This fits the three debugfs opens; exact historical attribution remains inference.
An initial off-device `logdump -S -f` without filesystem context also faulted due
to the tool's [NULL-context path](https://github.com/tytso/e2fsprogs/blob/v1.47.0/debugfs/logdump.c).
The corrected journal-only `logdump -f` succeeded without board device access.

Before replay, the complete 8 GiB dirty p4, exact 64 MiB journal/diagnostics and
later exported persistent journals were verified and retained mode 0400 under
`/var/lib/a733-dirty-state-preservation-20261110-01/` on the builder. Original
matched-cohort backups remain separate and immutable. Only parent RO was then
cleared; p1–p5 stayed RO1. The **unchanged protected exporter** passed its separate
counter scope, keeping p4 RO1 during `ro,noload`, normally unmounting, and exporting
six journal files with byte hashes and counters unchanged from the three-flush
baseline. This success does not erase the raw inspector's refusal.

The reviewed protected SD prefix was restored and flushed, then a mount-free RAM
reboot produced old-A boot `487a3021-fb8f-4300-924f-31d17aed18b9`. UART shows the
protected probe/unmount followed by actual ext4 recovery complete and RW mount.
The recovered marker matches SHA-256 `ae50b931…15e074`; original identity/configuration,
SSH and NTP pass. Actual nonroot KVM CPU, disk counter 10 → 11 and loopback HTTP
pass, followed by guest-requested poweroff and independently retained raw zero
exit. Counter 11 is durably checkpointed in enrollment `3193cb24…483f7d`
(checkpoint ordinal 8), and the inactive RAM unit uses the new disk pin. Linux
SD write/discard/flush fields remain zero in the installed boot.

The return console reports original `system.journal` corrupted or uncleanly shut
down and renamed/replaced. Its cause is unproven; byte export does not establish
application-journal integrity. Post-recovery SMART has critical warnings/media
errors 0, unsafe shutdowns 80 and admin errors 162; increases are not attributed
to a particular command. Old A retains the known resolver-policy defect; final
normalized installation remains pending. No physical power-cut, SPI recovery,
final immutable-SD, final boot-cycle or overall release acceptance is claimed.
