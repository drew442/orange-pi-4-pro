# Offline installed-host journal export

Use this diagnostic fallback only if serial cannot restore installed-host SSH.
The [initial preparation record](evidence/offline-journal-preparation-20261108.json)
predates the mandatory block protection added after the dirty-ext4 source review.
The revised helper has **not been run on the board**. It does not establish
hardware acceptance, fix SSH, or authorize a new installation.

Capture the requested live serial-console text first. Power off before changing
the card. Boot the already proven private RAM installer
D3 described in [physical SSH RAM installer boot](ssh-installer-remote-boot-20261108.md),
using its documented artifact hashes, guarded SD procedure, and trusted SSH
identity. Its recovery address was `192.168.1.115`; verify the board's recorded
MAC and strict host identity again. Keep the private image and exported journals
out of Git. Journals can contain private operational data even though the helper
never opens keys, identity files, shadow, root slots, or guest disks.

The helper is Python 3.11 standard library only. Neither `mount` nor `journalctl`
is needed in the RAM installer. It is exclusively pinned to SPCC M.2 PCIe SSD
serial `134807190FC700149277`, 256060514304 bytes, and 8 GiB ext4 STATE partition
`/dev/nvme0n1p4`, UUID `360dfb39-f49f-487e-b307-0bb887c854c5`, label `a733-state`.
Its checks require an internal journal at inode 8, no external journal, and the
known partition geometry. Unsupported ext4 features, including orphan-file
compatibility and orphan-present flags, cause refusal. Legacy orphan-list state
and a dirty journal may be inspected only under the block protection below;
skipping replay can expose stale or inconsistent filesystem contents.

Before export, inspect `/proc/self/mountinfo`, `/proc/swaps`, boot ID and RAM
root permissions over the authenticated connection. All swap must be disabled;
root must be RAM rootfs/ramfs/tmpfs, `/run` a dedicated tmpfs, and proc mounted.
The proven RAM installer initially creates `/` mode 1777. After verifying it is
this RAM boot, normalize **RAM root only** to mode 0755 (`chmod 0755 /`). The
helper deliberately refuses mode 1777. Never apply this preparation command to
an installed filesystem or remount physical storage to satisfy a guard.

Stage the reviewed script under a private root-owned mode 0700 directory in
`/run`, with script mode 0600. Verify its SHA-256 against the coordinator's
reviewed local file. Read and record the current actual
`/proc/sys/kernel/random/boot_id`; supply that full canonical UUID as the required
`--expected-boot-id`. A boot ID from an earlier document is not a valid pin for a
new boot. Preparation, transfer and export must use the same verified RAM boot.

On the coordinator, create a private output directory (mode 0700), set `umask
077`, then use a command of this form, replacing the boot UUID and using the
already staged path. This is an operator recipe, not recorded hardware evidence:

```sh
umask 077
ssh -i ~/.ssh/id_ed25519 -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=yes -o HostKeyAlias=192.168.1.116 \
  root@192.168.1.115 \
  'python3 -I -B /run/journal-export/export-known-host-journal.py --expected-boot-id ACTUAL-RAM-BOOT-UUID' \
  > /private/coordinator/path/installed-journal.tar.gz.partial
```

Capture SSH's exit status immediately. Keep stderr separate from the gzip stream;
it contains progress and refusal details. Promote the `.partial` file to its final
name only after status zero and successful gzip/tar validation. A failed SSH
connection or export can leave partial output; discard it. Never print journal
contents into routine logs. If cleanup reports a failed unmount, inspect the
remaining private `/run/a733-journal-*` mount before rebooting; the helper retains
its mountpoint, leaves STATE block read-only and refuses a successful archive.
A failed protection, revalidation or flag restoration also refuses an archive.
Inspect the kernel block read-only flag as well as mounts after any failure; do
not automatically clear it or retry with a writable mount.

The helper checks every NVMe partition is unmounted across visible process mount
namespaces, has no holders, active swap, block-device FDs, mapped files or process
references. It records the original STATE `BLKROGET` flag (0 or 1), claims
only the validated fixed partition exclusively, and sets `BLKROSET=1` when
needed. It verifies both the ioctl and sysfs read-only flags, retains a checked
read-only partition descriptor, releases the exclusive claim before the kernel
mount, and rechecks identity, geometry, internal journal and idle state. It uses
libc `mount` with `MS_RDONLY|MS_NOSUID|MS_NODEV|MS_NOEXEC`
and ext4 `noload`, then verifies mount identity, read-only flags and kernel-reported
journal replay suppression. Filesystem `ro,noload` alone is insufficient:
the pinned ext4 kernel can clean legacy orphans or update error metadata on a
writable block device. Its orphan cleanup checks block-device read-only state
and skips cleanup under this protection. The exporter rejects unsupported
orphan-file formats and retains all external-journal exclusions.

After a normal successful unmount, the helper verifies protection is still 1 and
revalidates the same fixed partition's identity, geometry, internal journal and
unmounted/idle state before restoring an original flag of 0. An original flag of
1 stays 1. A failed unmount or failed validation before restoring 0 cannot
clear the verified protection. If restoration or its readback fails, the helper
revalidates the held descriptor's device identity, geometry and internal journal
before attempting to set RO=1 again and verifying ioctl/sysfs agreement. It
refuses an archive even when protection is successfully reasserted. If that
revalidation, protective ioctl or final verification fails, it explicitly reports
the block read-only state as uncertain; a failed export does not guarantee RO=1.
`BLKROSET` changes only kernel write eligibility; it is not a filesystem
write, repair or replay operation. No physical write call is performed. This is a
trusted quiescent recovery operation; concurrent hostile root is outside its
threat model.

Only `var/log/journal` is traversed using held directory descriptors and
`O_NOFOLLOW`. Directories must be root-owned without group/other write permission
(setgid journal directories are permitted). Files must be regular, root-owned,
single-link, without special bits or group/other write permission; a journal
file's group may be the systemd-journal group and mode 0640 is permitted. Symlinks,
unsafe objects, unexpected non-journal files, changed source metadata and crossed
filesystem boundaries cause failure rather than silent omission. Names must be
safe ASCII components. Export is bounded to 64 `.journal`/`.journal~` files,
64 MiB each, 192 MiB total, 256 directory entries, and four directory levels
below the journal root. Missing/empty persistent journals cause refusal. An
oversized journal needs a separately reviewed approach, not a raised live limit.

Selected bytes are retained in bounded RAM until the owned mount is unmounted.
The helper then verifies the original kernel flags have been preserved/restored,
rechecks idle disk identity and Linux NVMe **and known SD** write,
discard and flush counters; any change refuses output. Only after these checks
are complete does it stream gzip tar to stdout. Archive members retain exact
relative names and file ownership/mode/mtime, with nanosecond source mtime and
SHA-256 recorded in `export-receipt.json`. The receipt records the actual RAM boot
ID, fixed source identity, successful unmount, original/restored block flag,
verified mount-time block protection, counters and
`hardware_acceptance: false`. Counter sampling covers the whole Linux export
interval, including setting and restoring the kernel flag; equality is required
even for discard/flush activity. Counter equality is not the full-card hash, firmware audit or endurance acceptance proof.

On the coordinator, validate the member allowlist and receipt SHA-256 values in a
private directory. Inspect the exported journal with a compatible offline
`journalctl --directory=...` environment; record specific errors and timestamps
without assuming that absence of a final log proves a particular failure. Keep
raw journals and logs in private ignored output storage, and link only a bounded
redacted finding from implementation status. No journal interpretation or
hardware result is claimed by the helper tests.

## Real-filesystem software check

The [Ubuntu loop-fixture record](evidence/offline-journal-loop-20261108.json)
now passes two fresh 64 MiB clean ext4 filesystems: an exact synthetic archive
roundtrip and refusal of an unexpected nonjournal file. Both mounts use the
low-level read-only/noload implementation, bypassing the new mandatory physical
block-protection wrapper. This clean-filesystem record does not validate the
revised protection lifecycle or dirty-orphan behavior. Complete source hashes
and Linux write/discard/flush counters stay unchanged across the export, and independent
postchecks find no remaining fixture loops or directories. Counter fields follow
[Linux I/O statistics](https://docs.kernel.org/admin-guide/iostats.html).

The harness accepts no device argument and formats only its newly created held
regular files in a private mount namespace. Source review precedes execution.
Initial fixture runs detected attachment-time flush activity before export;
waiting for device events before baseline sampling resolved that setup race.
The strict counter checks remain unchanged in the revised exporter.

The synthetic bytes are not real systemd journals. The physical-target/RAM-entry
policy is bypassed solely to call filesystem helpers with the new loop device;
this does not establish actual A733 export, ARM kernel block-protection behavior
or dirty-journal recovery. Those checks remain untested on the board. To repeat
on the prepared builder, run the reviewed `tests/verify_known_host_journal_loop.py`
as root from its protected project tree, with no arguments. No compilation or
package installation is required.
