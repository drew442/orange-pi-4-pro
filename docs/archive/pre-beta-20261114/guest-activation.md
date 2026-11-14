> **Historical record — not active instructions or release gates.**
> Superseded by the [manual beta scope](../../beta-scope.md) and
> [current status](../../implementation-status.md). Original observations retain
> their recorded configuration and limitations.

# Guest storage and account activation

Explicit controllers prepare and apply guest-storage mounting and locked
service identities. The current installed host includes the authenticated
40-file maintenance tree (including `package.json`) at `/usr/lib/a733-tools`.
[Physical guest results](../../kvm-vgic-results-20261108.md) establish storage/account
activation and actual nonroot KVM execution; the
[corrected-host guest restoration](../../evidence/corrected-host-guest-restore-20261108.json)
advanced the disk counter through 4 and 5. These observations do not establish
acceptance of the unbooted normalized distribution. See
[implementation status](../../implementation-status.md) for the current maintenance
session and remaining gates.

Activation and guest startup remain explicit. Enrollment publishes private
per-guest directories and protected assets; recreate locked RAM service accounts
and review the unit before starting a guest after a host reboot.

## Mount the installation's guest filesystem

`scripts/activate-guest-storage.py` requires the exact whole NVMe target, model,
serial, byte capacity and guest filesystem UUID from the installation record.
It derives p5 only after validating the selected read-only p2/p3 root, p4 state,
fixed GPT roles, kernel geometry and live ownership. A pending A/B selection is
permitted; the current running root still establishes the namespace.

Preparation opens block devices read-only. It rejects busy, swapped, mapped,
read-only, aliased or foreign-mounted p5 storage. An existing guest mount must
be the sole full-filesystem ext4 mount at `/run/a733-guests`, with
`rw,nodev,nosuid,noexec`. The ext4 UUID and byte size must match; only the
recipe's internal journal inode 8 is allowed. External journal device/UUID
references and the journal-device feature are refused before mounting.

The following shows the controller interface. Physical activation is recorded
in the linked evidence; a fresh installation needs its own verified target and
receipts. Enter a root shell and set every value from the actual installation:

```sh
: "${A733_GUEST_TOOLS:?Set absolute trusted complete installed or portable tool tree}"
: "${A733_NVME_TARGET:?Set exact whole NVMe stable path}"
: "${A733_NVME_MODEL:?Set exact model}"
: "${A733_NVME_SERIAL:?Set exact serial}"
: "${A733_NVME_BYTES:?Set exact byte capacity}"
: "${A733_GUEST_UUID:?Set guest filesystem UUID from installation record}"
set -- --target "$A733_NVME_TARGET" --expected-model "$A733_NVME_MODEL" \
  --expected-serial "$A733_NVME_SERIAL" --expected-bytes "$A733_NVME_BYTES" \
  --guest-uuid "$A733_GUEST_UUID"
umask 077
set -C
/usr/bin/python3 -I -B "$A733_GUEST_TOOLS/scripts/activate-guest-storage.py" \
  prepare "$@" > /run/guest-mount-plan.json
chmod 0400 /run/guest-mount-plan.json
cat /run/guest-mount-plan.json
```

Use a fresh plan name if it exists. Review the full plan and independently
verify off-device backups before the separate apply step:

```sh
/usr/bin/python3 -I -B "$A733_GUEST_TOOLS/scripts/activate-guest-storage.py" \
  apply "$@" --plan /run/guest-mount-plan.json
```

Apply requires a real TTY, `BACKUP VERIFIED` and the displayed `ACTIVATE`
phrase binding serial, p5 path, UUID and plan hash. A RAM lock and held
read-only whole-device lock serialize cooperating changes; p5 has an exclusive
read-only claim before the kernel mount. The claim is released for the kernel
filesystem claim. Revalidation cannot protect against a privileged process
ignoring locks.

A conservative receipt is flushed **before** the mount command: ext4 journal
replay may write even if mounting fails. After verifying the mounted device,
the controller changes only its root directory from root-owned `0700` to `0711`
through a held directory descriptor. This permits traversal; private guest
subdirectories still need their own ownership and `0700` permissions. No guest
files, partition tables, boot selection, SD or SPI are written by the controller.

Successful activation leaves the mount present. An already-owned mount with
`0711` root is idempotent. On failure, cleanup removes only a newly created
mount whose ID/device still match. It preserves foreign and preexisting mounts;
cleanup errors and possible modification remain in the receipt. Do not treat
failed apply as proof that no journal replay occurred. Inspect the receipt and
current mounts before preparing another plan. There is no force/lazy unmount,
formatting or filesystem repair fallback.

## Create locked service identities in RAM

`scripts/guest-accounts.py` uses the guarded RAM-backed `/etc` overlay and the
pinned `systemd-sysusers` 252 tool. It does not change the immutable root or
create persistent passwd/shadow databases on state. Keep its identity config
on the installation's NVMe state and recreate the RAM accounts each boot;
automatic invocation is not installed yet.

The exact schema is:

```json
{"schema":1,"guests":[{"name":"fixture","uid":60001,"gid":60001}]}
```

Choose one to sixteen stable, distinct identities. Names use lowercase ASCII
letters, digits and internal hyphens, start with a letter and contain at most
16 characters. UID and GID must be equal and in `60000..60999`; do not reuse an
identity for another guest while its files or backups exist. The example is a
fixture identity, not an allocation for the user's eventual workloads.

The config and review plan must be root:root, single-link regular files with
mode `0400`, safe parents, and storage on RAM or the selected NVMe state. In a
root shell after staging and reviewing the actual identity config:

```sh
: "${A733_GUEST_IDENTITIES:?Set absolute protected identity config path}"
/usr/bin/python3 -I -B "$A733_GUEST_TOOLS/scripts/guest-accounts.py" \
  prepare "$A733_GUEST_IDENTITIES" > /run/guest-accounts-plan.json
chmod 0400 /run/guest-accounts-plan.json
cat /run/guest-accounts-plan.json
/usr/bin/python3 -I -B "$A733_GUEST_TOOLS/scripts/guest-accounts.py" \
  apply "$A733_GUEST_IDENTITIES" --plan /run/guest-accounts-plan.json
```

The controller rejects existing name/UID/GID collisions, partial identities,
noncanonical numeric IDs, other users sharing the primary GID and supplementary
memberships. Accounts have locked passwords without stored hashes, home
`/nonexistent` and shell `/usr/sbin/nologin`. The matching primary groups are
locked and empty. Existing exact accounts are reused without running sysusers;
all unrelated account rows must remain unchanged. Plans expose database hashes
and requested sysusers lines, not password hashes.

A receipt records possible account changes before invoking sysusers. Partial
failure prevents success; it does not advertise an automatic rollback of the
four account files. Review RAM state or reboot into the known root before
retrying. KVM access, disk directory enrollment and service startup are separate;
this helper adds no administration, disk or KVM group membership.

Upstream: [systemd 252 sysusers invocation](https://www.freedesktop.org/software/systemd/man/252/systemd-sysusers.html),
[identity definitions](https://www.freedesktop.org/software/systemd/man/252/sysusers.d.html),
[252 database/backup implementation](https://github.com/systemd/systemd/blob/v252/src/sysusers/sysusers.c),
and the [pinned ext4 journal loader](https://github.com/orangepi-xunlong/linux-orangepi/blob/2ac08e8c7cdc28abbdc5c9a9dd812f887ae9c79f/fs/ext4/super.c).

## Verified software evidence

At source `927a5a2`, the [native private-loop run](../../evidence/guest-mount-loop-20261104.json)
created its own sparse 250,000,000,000-byte GPT disk and journaled p5. It exercised
real prepare, mount, SIGINT immediately after mounting, owned cleanup, retry,
root permission readback and idempotence. Guest data and four other partition
sentinels stayed unchanged. Running-host/NVMe classification and TTY approval
were fixtures. This did not test dirty-journal crash recovery or physical NVMe.
Elapsed 0.63 seconds, maximum RSS 27,416 KiB.

The [ARM account run](../../evidence/guest-accounts-arm-20261104.json) used authenticated
userspace and actual systemd 252.39. It proved account creation, locked identity,
unrelated-row preservation, idempotence and UID-collision refusal inside a
private chroot. Runtime mount provenance was mocked only in that fixture; the
real CLI refused the unsupported environment before account writes. Elapsed
8.17 seconds, maximum RSS 53,908 KiB. No builder host accounts changed.

Exact verified commands on the prepared builder, from its private checkout:

```sh
sudo -i
cd /var/lib/a733-guest-activation-final-20261104
/usr/bin/python3.10 -I -B tests/verify_guest_mount_loop.py
/usr/bin/python3.10 -I -B tests/verify_guest_accounts_arm.py \
  /home/drew/orange-pi-4-pro-build/recipe/out/host-userspace-bookworm-20261001-01/rootfs.tar.gz
```

The loop harness accepts no target argument and refuses foreign ownership. Both
harnesses require at least 2 GiB free fixed builder storage. Separate post-process
checks found no fixture paths, owned loop attachments or host-visible guest
mount. ARM binfmt checks hash the configured interpreter pathname; the
kernel-held F interpreter descriptor is not independently inspected. All
[physical acceptance gates](../../hardware-validation.md) remain open.
