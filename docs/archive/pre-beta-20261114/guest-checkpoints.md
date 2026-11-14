> **Historical record — not active instructions or release gates.**
> Superseded by the [manual beta scope](../../beta-scope.md) and
> [current status](../../implementation-status.md). Original observations retain
> their recorded configuration and limitations.

# Stopped guest disk checkpoints

`scripts/checkpoint-guest.py` records the current hash of an enrolled, stopped
guest disk in its protected manifest. It never resizes or copies the disk,
starts/stops a service, or claims filesystem cleanliness. A checkpoint is not a
backup, snapshot or successful guest boot. Use the separate
[backup artifact workflow](../../guest-backups.md) to preserve disk contents off-device.
Actual [guest poweroff, two durable checkpoints and restart](../../kvm-vgic-results-20261108.md)
now pass on the diagnostic VGIC host; off-device restore and final corrected-host
validation remain open.

## Establish a trusted starting record

After verified [enrollment](../../guest-enrollment.md), save `manifest_sha256` from a
receipt with `success`, `published` and `durable` all true in an independently
trusted installation record. Earlier receipts lacked this field: their initial
manifest pin must be established from the verified enrollment before the first
launch. Do not adopt an unexplained current hash just to bypass a mismatch.

The checkpoint controller requires that exact old manifest pin before parsing
its JSON. The config, file records, ownership and UUID must agree. Root-owned
kernel/initramfs files remain fully authenticated; a checkpoint updates only
the disk hash and checkpoint history fields. Disk resizing is refused.

## Stop and verify first

Use the guest's own verified shutdown procedure. Record guest shutdown output,
QEMU exit status and host service state. The current preparation unit sends
SIGTERM when stopped; `systemctl stop` alone does not establish a clean guest
shutdown. The fixture guest requests its own shutdown after syncing and
unmounting its disk; that behavior is now observed in the linked physical results.

The controller requires the fixed service name `a733-guest-NAME.service` to be
`inactive/dead` with `MainPID=0` and `ControlPID=0`. A genuinely absent unit is
permitted for an otherwise verified manual-launch workflow. Failed, active,
transitioning or unreadable service states are refused. No service state is
changed by the controller.

A read-only disk descriptor holds an exclusive nonblocking flock throughout
hashing and publication. Other cooperating launchers cannot start on that
inode. A bounded `/proc` census also refuses other processes with an open
reference, including read-only readers. Namespace/permission/visibility errors
fail closed. This census is a snapshot: trusted root and cooperating writers
must still respect the lock. It cannot exclude every in-place writer or prove
that a filesystem was cleanly unmounted. Stop backup/read jobs before this
operation as well.

All payload reads follow selected-host NVMe placement checks. The controller
verifies the guest p5 UUID, locked identity, directory mode/ownership and all
held file names/inodes. Guest disks remain guest-owned `0600`; the directory
is root:guest-group `0710`, so the guest cannot replace its manifest or assets.

## Prepare and apply

The controller prepare/apply sequence has now run on the board through the
reviewed RAM account closure, after verified guest poweroff. The generic
commands below still require the caller's actual matched installation, manifest
pin and observed shutdown evidence. In a root shell, set
values from the trusted records and use the complete installed tree at
`/usr/lib/a733-tools` on the new immutable A/B root or a trusted complete
portable tool tree on NVMe or RAM. The older recovery SD image has not been
refreshed; see [guest tools availability](../../guest-tools-package.md). The diagnostic hardware result does not establish final corrected-host release acceptance:

```sh
: "${A733_GUEST_TOOLS:?Set absolute trusted complete installed or portable tool tree}"
: "${A733_GUEST_NAME:?Set enrolled guest name}"
: "${A733_GUEST_MANIFEST_SHA256:?Set independently trusted current manifest pin}"
umask 077
set -C
/usr/bin/python3 -I -B "$A733_GUEST_TOOLS/scripts/checkpoint-guest.py" \
  prepare --name "$A733_GUEST_NAME" \
  --manifest-sha256 "$A733_GUEST_MANIFEST_SHA256" \
  > /run/guest-checkpoint-plan.json
chmod 0400 /run/guest-checkpoint-plan.json
cat /run/guest-checkpoint-plan.json
```

Use a fresh plan filename if it exists. Preparation hashes the entire stopped
disk and briefly reserves it against cooperating launches, but writes no
storage. Review the old/proposed manifest hashes, observed disk hash, unchanged
identity/kernel/resource fields and current storage context. Apply separately:

```sh
/usr/bin/python3 -I -B "$A733_GUEST_TOOLS/scripts/checkpoint-guest.py" \
  apply --name "$A733_GUEST_NAME" \
  --manifest-sha256 "$A733_GUEST_MANIFEST_SHA256" \
  --plan /run/guest-checkpoint-plan.json
```

The protected review plan must be root:root `0400`, single-link, and stored on
RAM or selected NVMe state. Apply repeats inspection under a RAM lock and asks
for `CLEAN SHUTDOWN VERIFIED`, followed by the displayed `CHECKPOINT` phrase
binding the name, new disk hash and plan hash. This records operator attestation;
it is not an automatic shutdown-verification mechanism.

Before committing a changed checkpoint, the controller explicitly synchronizes
the held disk descriptor. This may flush earlier guest data to NVMe, although
the controller writes no guest payload bytes. It then persists a root-only
`.checkpoints/` history record before replacing `enrollment.json` atomically.
History records retain exact base64-encoded before/after manifest bytes and
their hashes, plus parsed content. History files and manifests are root:root
`0400`; the history directory is `0700`.

## Publication, interruption and restart

Receipts distinguish possible metadata changes, disk flush attempts, history
publication/durability, manifest replacement and manifest durability. SIGINT,
SIGTERM or a cleanup error can make `success=false` after publication occurred.
Use the receipt's publication fields and independently inspect the protected
manifest; do not blindly retry with the old pin. A visible new manifest causes
that old pin to be refused. An identical checkpoint using the trusted current
pin is a no-op without another archive or disk flush.

Failure after publication preserves the new manifest and history. There is no
automatic rollback of a checkpoint or disk bytes. Never restore an old manifest
and assume it restores the disk: matching disk data must come from a separately
verified backup. Conflicting or foreign history files are refused, not replaced.

After successful durable publication, save `new_manifest_sha256` as the next
trusted pin. Regenerate the manually prepared unit from the new protected
manifest's `config`; the unit still embeds the old disk hash until regenerated.
Review and reload the unit before a separate start. Packaging and a guarded
unit installation/start controller are still required. A successful dry-run or
unit parser check does not prove guest restart, KVM acceleration or clean stop.

Upstream: [Linux flock semantics](https://man7.org/linux/man-pages/man2/flock.2.html),
[proc process information](https://www.kernel.org/doc/Documentation/filesystems/proc.txt),
[systemd 252 service state](https://www.freedesktop.org/software/systemd/man/252/systemctl.html),
and [systemd 252 lifecycle](https://www.freedesktop.org/software/systemd/man/252/systemd.service.html).

## Verified software evidence

Source `6c62d03` passed 559 discovery cases on Jammy without skips, four separate
diagnostic cases and shell regressions. Independent Sol high review accepted
the policy and transaction. Native tests include real disk locks and readers,
stale pins, history collisions, exact noncanonical history, fsync/close failures
and queued SIGINT/SIGTERM around actual file replacement.

The [ARM checkpoint probe](../../evidence/guest-checkpoint-arm-20261104.json) enrolled
the actual pinned fixture, changed its working disk, refused the old launch
pin and a separately locked disk, then published a checkpoint with queued
SIGTERM. Its receipt correctly reported interruption while retaining publication
and durability. Exact history bytes, unchanged disk content, obsolete-pin
refusal, no-op behavior, new-pin file checks and updated unit parsing passed.
Elapsed 50.99 seconds; maximum RSS 193,384 KiB.

Those positive file checks mocked mounted-NVMe, namespace, service/consumer and
TTY boundaries only inside an authenticated disposable fixture. Real CLI entry
refused the unsupported environment before writes. No VM, systemd service or
KVM guest was started; clean shutdown was not performed. Configured binfmt
interpreter pathname/hash was checked; the kernel-held F descriptor was not
independently inspected.

A separate [actual builder proc scan](../../evidence/guest-checkpoint-consumers-20261104.json)
detected a real UID-60001 read-only consumer and returned empty after its exit.
It used only a private owned regular file, not guest or physical storage. This
characterizes the builder's visibility, not the board's.

Exact executed ARM integration command from the private builder checkout:

```sh
sudo -i
cd /var/lib/a733-guest-checkpoint-work-20261104
/usr/bin/python3.10 -I -B tests/verify_guest_checkpoint_arm.py \
  /home/drew/orange-pi-4-pro-build/recipe/out/host-userspace-bookworm-20261001-01/rootfs.tar.gz \
  /home/drew/orange-pi-4-pro-build/recipe/out/guest-fixture-20261104-03
```

The harness requires 3 GiB free fixed storage and removes its private files at
exit. Separate inspection found no leftover fixture or host-visible guest mount.
Raw logs remain under the builder's `guest-checkpoint-artifacts-20261104/logs/`.
Every [physical release gate](../../hardware-validation.md) remains open.
