# Physical dirty-STATE recovery trial

**Design preparation only; no crash, journal export or recovery is performed by
this document.** The existing [block-protection evidence](evidence/state-block-protection-20261109.json)
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

## Execution bindings still required

No executable crash command is authorized by a guessed boot ID or placeholder
backup pin. Before the physical trial, independently review the exact live-bound
mount, marker/fsync, reset and raw-journal-inspection commands, their tool
availability and the full recovery route. The dirty-journal input and executable
crash recipe do not exist yet; cohort restoration alone is not a reviewed crash
plan. If any
stage fails, retain evidence in RAM and restore the complete authenticated
original cohort using its unchanged procedure; do not splice an old selector
onto normalized roots or continue after an ambiguous partial restore.

## November 10 live preparation

Actual private RAM boot `597391e8-1ad5-4a04-a27d-f21e243fa960` at `.115` passes
pinned SSH, rootfs/tmpfs, no swap, no NVMe filesystem mounts and exact target
checks. Independent review approved the authenticated 256 MiB RAM SD prefix
write and clean transition. SD maintenance is separate from routine-write proof.

The unchanged paired-restoration controller applied reviewed plan
`dfdca654301ed55104b0a2a7150e7f8c68b3cdf91d2bf932ccbfcd6bf9ac9876`
with original index `44e8fc1f…64372e`. All 4/4/8 GiB decoded stream hashes/lengths,
accepted writes and flushes pass, with zero exit and matching terminal/progress
receipts. Optional readback was not requested; no raw readback hash match is
claimed. GPT/BOOT/p5/SD/SPI were preserved by this operation. The originals remain
immutable on the builder; the newly restored cohort is disposable.

The full trusted 40-file closure is staged in RAM. The unchanged
`host-recovery-target.py` snapshot passes with all six RO flags zero, and the
restored STATE superblock has RECOVER false. Actual ARM environment/process/SD
guards pass. The pinned debugfs is version 1.47.0, SHA-256 `7aca7f…bfe51`.
The one-off inspector `tmp/inspect-dirty-state-journal-20261110.py`, SHA-256
`177fb5dd…c9717`, passes host fixtures and independent source review; its complete
postcrash inspection remains unperformed. It requires all six block-RO flags set
and a fresh actual postcrash boot binding; dirty indicators alone do not prove
committed transactions.

Raw preparation receipts are under ignored `out/dirty-state-physical-20261110-01/`.
No marker creation, dirty reset, dirty-image preservation, protected journal
export or physical replay has been performed. The exact live-bound crash recipe
still needs independent review before execution. Completed controlled SSH and
NVMe-driver recovery cases remain accepted without repetition.
