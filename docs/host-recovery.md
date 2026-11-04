# Offline root-selection recovery

`scripts/recover-host-root.py` selects a retained, fully authenticated root from
the RAM preparation environment when the installed host cannot run its live
maintenance tools. It requires the exact NVMe identity, expected state UUID and
an independently trusted release manifest/pin. Root images, GPT, boot files and
guest partitions are not rewritten. Recovery changes regular files on NVMe
state; mounting that filesystem can also replay its journal.

The [maintenance package](maintenance-tools.md) and preparation initramfs now
include the controller and its source closure. No physical failed-boot recovery
has been demonstrated; networking, recovery boot and the procedure still require
board validation. Keep the [diagnostic serial guide](diagnostic-image.md)
available; do not change SPI before proving board recovery.

## Preparation and authorization

Boot the pinned preparation environment, which includes the trusted tools at
`/usr/lib/a733-tools`. Stage the retained release manifest and a review-plan
directory on RAM or identified external storage. The kernel must be AArch64 `6.6.98-a733-diag`, `/` must be RAM,
`/run` must be writable tmpfs, and swap must be inactive. The target namespace
and all five partitions must be unmounted, idle and match the fixed GPT layout.
Source and plan files cannot reside anywhere on that target or on SD/MMC.
Identify USB storage carefully: the software cannot distinguish every USB SD
reader from another USB disk.

Obtain `A733_STATE_UUID` from the installation/immutable bootstrap record.
Do not guess a UUID from another prototype. The state superblock must match
that UUID and the 8 GiB journaled ext4 role. Only the recipe's internal journal
inode 8 is supported: external journal device/UUID references and journal-device
filesystems are rejected before any writable mount. The
[pinned vendor journal loader](https://github.com/orangepi-xunlong/linux-orangepi/blob/2ac08e8c7cdc28abbdc5c9a9dd812f887ae9c79f/fs/ext4/super.c#L5638)
can otherwise open a separate device, outside the selected state partition.

Enter a root shell, set the required values from actual inventory and trusted
release records, and use the following prototype CLI form. The directories
must have root ownership and mode `0700`; manifests and
review plans must be root-owned regular files with one link and mode `0400`.
These commands have not been reproduced on the board.

```sh
: "${A733_NVME_TARGET:?Set exact whole-NVMe stable path}"
: "${A733_NVME_MODEL:?Set exact model}"
: "${A733_NVME_SERIAL:?Set exact serial}"
: "${A733_NVME_BYTES:?Set exact byte capacity}"
: "${A733_STATE_UUID:?Set expected installation state UUID}"
: "${A733_RECOVER_SLOT:?Set retained slot A or B}"
: "${A733_RELEASE_MANIFEST:?Set retained trusted manifest path}"
: "${A733_MANIFEST_SHA256:?Set independently trusted manifest SHA-256}"
set -- --target "$A733_NVME_TARGET" --expected-model "$A733_NVME_MODEL" \
  --expected-serial "$A733_NVME_SERIAL" --expected-bytes "$A733_NVME_BYTES" \
  --state-uuid "$A733_STATE_UUID" --slot "$A733_RECOVER_SLOT" \
  --payload-manifest "$A733_RELEASE_MANIFEST" \
  --manifest-sha256 "$A733_MANIFEST_SHA256"
/usr/bin/python3 -I -B /usr/lib/a733-tools/scripts/recover-host-root.py \
  prepare "$@" > /run/recovery-plan.json
chmod 0400 /run/recovery-plan.json
/usr/bin/python3 -I -B /usr/lib/a733-tools/scripts/recover-host-root.py \
  apply "$@" --plan /run/recovery-plan.json
```

`prepare` mounts nothing. It authenticates all 4 GiB of the retained root before
parsing filesystem metadata and hashes all 8 GiB of state to bind the review
plan to its pre-recovery contents. Review the complete plan. Before `apply`,
make and independently verify off-device backups of existing roots and state,
including the original state bytes if journal recovery is pending. The tool
does not create or restore that backup.

`apply` requires `BACKUP VERIFIED` and an exact interactive `RECOVER` phrase
binding the slot, serial, root partition, state UUID and plan hash. There are
no force/unattended switches. It rechecks full hashes after authorization,
holds a RAM recovery lock and whole-device advisory lock, and keeps a read-only
exclusive claim on the chosen root. The state claim is released only when the
kernel must claim that filesystem for mounting. Privileged, noncooperating raw
device users are outside this maintenance protocol.

## State recovery and publication

Before invoking a writable mount, the controller emits a conservative receipt:
`state_write_attempted=true` and `state_may_be_modified=true`. A failed mount can
already have replayed journal transactions. The tool mounts only the identified
state partition at private `/run/a733-recovery-state`, verifies the resulting
mount and `.a733-state` marker, and takes the shared NVMe update lock.

A missing or malformed `boot/selected.conf` is supported. Unsafe ownership,
links, oversized records, mismatched markers and filesystem failures are
refused. No filesystem repair or formatting is attempted. The actual recovered
old selector bytes (or absence), proposed choice, root/manifest and pre-recovery
state hash are durably archived in `boot/recovery-PLAN_SHA256.json` before
publication. Shared state must still be compatible with the retained release
or restored through a separately tested backup procedure.

The chosen root is rehashed before the shared
[signal-safe publisher](host-root-selection.md#publication-and-interruption-handling)
updates next-boot selection. Visibility and durability have separate receipt
fields. A later failure never implies that the old choice remains installed.
The controller attempts to unmount its own state mount on all paths and reports
cleanup failures without discarding publication information. Inspect actual
selection and archive after uncertain outcomes; prepare a fresh plan before
retrying because journal replay or earlier recovery may have changed state.

No automatic reboot or running-root switch occurs. After an explicit boot,
verify the selected marker, NVMe-backed state, management reachability and actual
KVM guest execution. Missing NVMe, both roots failing authentication, corrupt
state requiring repair, backup restoration and interrupted SPI recovery remain
separate required recovery paths. All physical release gates remain open.

## Native software evidence

[Recovery evidence](evidence/host-recovery-loop-20261104.json) records a newly
created 250,000,000,000-byte sparse GPT loop disk with five real kernel
partitions, an authenticated 4 GiB A root and actual 8 GiB journaled ext4 state.
The state began clean and unmounted with a malformed selector. A full independent
state hash before and after preparation matched. Recovery archived the exact
bad bytes, published verified A and unmounted state. Offline filesystem readback
confirmed the selector and journal; the root hash, GPT, boot/guest sentinels and
unrelated state sentinel remained unchanged.

The test explicitly replaces NVMe/RAM entry policy and TTY authorization. Real
GPT bytes, partition sysfs geometry, internal-journal superblock checks, mount
ownership, read-only claims, full hashes and archive/publication execute.
Cleanup removed its private mounts, loop and backing file, independently checked
from the builder afterward. The run took 1:28.36 with 36,784 KiB peak process RSS.
It does not exercise dirty-journal transactions after a crash, physical failed
boot, backup restore, or KVM. The new tool remains untested on ARM and unpackaged.

Verified command on the Jammy builder:

```sh
sudo /usr/bin/python3.10 -I -B \
  /var/lib/a733-recovery-work-20261104/tests/verify_host_recovery_loop.py \
  /var/lib/a733-update-final-20261104/candidate-A \
  --manifest-sha256 51f8dfa7f0f66b005b9bfd08dc5f29fcf25f0791e5e844ab6af22de94aa9a44c
```

The harness accepts no device argument and requires 12 GiB free on supported
fixed builder storage. Formatting is confined to its newly created and
identity/geometry-checked loop p4. It uses a private mount namespace and private
`/run`, retains unresolved cleanup resources, and does not alter builder mounts.
The pinned diagnostic configuration hash was rechecked with
`CONFIG_NAMESPACES=y`; that is configuration evidence, not an A733 boot result.
Separate tests inject actual SIGINT after publication and verify conservative
receipts plus owned-state cleanup. All 397 discovery cases pass on Jammy with
no skips, plus four diagnostic cases and the shell regressions.
