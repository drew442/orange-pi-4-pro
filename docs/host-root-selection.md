# Explicit activation and pending-selection cancellation

`scripts/select-host-root.py` verifies a complete installed root against an
externally pinned release manifest, then changes the **next boot selection** on
NVMe state. It never switches the running root, reboots, migrates shared state,
or declares management/KVM health. This is a checkout-only prototype; it is not
yet included in the packaged host or recovery SD. No physical activation or
rollback has been performed.

`activate` selects the idle opposite slot while selection still matches the
running root. After a successful boot, the same operation can select a retained
old root using its old trusted release manifest. `cancel-pending` selects the
currently running read-only root when another slot is pending and no reboot
has occurred. It cannot run when the installed host failed to boot; use the
separate [RAM recovery controller](host-recovery.md). Packaged recovery and
hardware rollback validation remain open.

## Preconditions and commands

Keep verified off-device backups, both old and new release manifests/pins,
serial access and a recovery card before maintenance. Shared state must remain
backward compatible or have a tested restore path. The package-state ABI alone
does not establish application/state compatibility. Preserve the compatible
kernel/DTB/initramfs stack; routine selection never writes SD, SPI, root blocks
or the boot partition.

Run the complete trusted checkout from NVMe or RAM on the installed host.
Release manifests must be root-owned, mode `0400`, with one link and safe
non-symlink ancestors. The tool checks source storage, actual running mounts,
NVMe model/serial/capacity, GPT, partition roles, current selector and kernel ABI.
An inactive target is held read-only with an exclusive block claim. Cancellation
uses a nonexclusive read-only descriptor because the verified running root is
mounted read-only. Both paths hash all 4 GiB before parsing bounded filesystem
metadata and hold the descriptor through publication.

The following is prototype CLI usage. Enter a root shell with `sudo -i`, then
set every `A733_*` value from actual host inventory and a trusted release
record; there is no default target. The tools must be staged at the stated root-owned checkout path before use. These
commands have not been reproduced on the board.

```sh
: "${A733_NVME_TARGET:?Set the exact whole-NVMe stable path}"
: "${A733_NVME_MODEL:?Set the exact model}"
: "${A733_NVME_SERIAL:?Set the exact serial}"
: "${A733_NVME_BYTES:?Set the exact byte capacity}"
: "${A733_SELECT_SLOT:?Set the intended slot A or B}"
: "${A733_RELEASE_MANIFEST:?Set the absolute trusted manifest path}"
: "${A733_MANIFEST_SHA256:?Set its independently trusted SHA-256}"
set -- --intent activate --slot "$A733_SELECT_SLOT" \
  --target "$A733_NVME_TARGET" --expected-model "$A733_NVME_MODEL" \
  --expected-serial "$A733_NVME_SERIAL" --expected-bytes "$A733_NVME_BYTES" \
  --payload-manifest "$A733_RELEASE_MANIFEST" \
  --manifest-sha256 "$A733_MANIFEST_SHA256"
/usr/bin/python3 -I -B /run/a733-state/update-tools/scripts/select-host-root.py \
  prepare "$@" > /run/a733-state/updates/selection-plan.json
chmod 0400 /run/a733-state/updates/selection-plan.json
/usr/bin/python3 -I -B /run/a733-state/update-tools/scripts/select-host-root.py \
  apply "$@" --plan /run/a733-state/updates/selection-plan.json
```

Create the maintenance checkout, release and plan directories with root
ownership and mode `0700`; they are not preinstalled by current images. Review
the complete plan before `apply`. It requires an interactive terminal,
`BACKUP VERIFIED`, and the exact displayed `SELECT` phrase binding intent, slot,
serial, partition, byte count and plan hash. There is no force/unattended switch.
For cancellation, use `--intent cancel-pending`, the currently running slot,
and its retained trusted manifest/pin, then prepare a new plan. For rollback
after a booted update, use `activate` with the old inactive root and old pin.

## Publication and interruption handling

All staging and selection operations share NVMe `boot/update.lock`. Under that
lock the controller rechecks the plan, full target bytes, compatibility and
current selection. It first durably creates
`boot/selection-PLAN_SHA256.json`, containing exact before/after selector bytes,
target/context bindings and the manifest pin. Existing identical journals are
revalidated and synced before reuse; different or unsafe records are refused.
An archived selector is history, not proof that its slot was healthy or booted.

Only after the journal is durable does the controller publish `selected.conf`
using file fsync, atomic rename and directory fsync, bound to a held directory
descriptor. It reads the selected file back and checks the expected pending or
cancelled context. See [rename](https://man7.org/linux/man-pages/man2/rename.2.html)
and [fsync](https://man7.org/linux/man-pages/man2/fsync.2.html) for the underlying
semantics. The original five-field [selector format](nvme-root-selection.md)
and immutable bootstrap remain compatible.

Handled SIGINT/SIGTERM are deferred through publication and receipt accounting;
see [Python signal masks](https://docs.python.org/3.10/library/signal.html#signal.pthread_sigmask).
A failure before rename reports selection unpublished. A failure or interruption
after rename reports the new selection visible, with durability uncertain when
directory sync did not complete. An interruption after a completed publisher
can report visible and durable with `success=false` because later checks did
not finish. `root_switched` stays false; state files are written but raw root
blocks are not. Receipts are not health results or future activation tokens.

After any uncertain result, inspect the actual
`/run/a733-state/boot/selected.conf` and the named journal. Do not infer the
installed choice from the error alone. SIGKILL and power loss can leave no
receipt. Do not blindly retry a stale plan: prepare against the observed state,
or explicitly cancel a pending selection using the verified running root.

After an explicit reboot, verify the actual selected marker, NVMe state/package
views, management reachability and an actual KVM guest. No automatic reboot or
rollback is implemented. Interrupted publication, failed-boot recovery,
cross-version state restoration and all physical release tests remain required.

## Verified software checks

[Private-loop evidence](evidence/host-root-selection-loop-20261104.json) records
actual authenticated 4 GiB A/B copies and 64 MiB journaled ext4 state in a
private mount namespace. The running-root fixture A was mounted read-only and
refused an exclusive block claim. Activation read idle B with exclusive claims;
cancellation read mounted A nonexclusively. All controller block opens were
read-only; both root images remained byte-identical.

An injected pre-rename failure retained A and a durable journal. Reusing that
journal with a post-rename interruption reported B visible and durability
uncertain. Explicit cancellation then published A durably. Offline readback of
the unmounted state filesystem confirmed A. Unrelated sentinels were unchanged;
all private mounts/loops/files were removed. The run took 1:53.89 with 37,500 KiB
peak process RSS. These are file/loop and fault-injection checks, not power-cut
or booted rollback results. The live NVMe context and TTY authorization were
explicitly replaced by fixtures.

Verified command on the Jammy builder:

```sh
sudo /usr/bin/python3.10 -I -B \
  /var/lib/a733-selection-work-20261104/tests/verify_host_selection_loop.py \
  /var/lib/a733-update-final-20261104 \
  --manifest-sha256 51f8dfa7f0f66b005b9bfd08dc5f29fcf25f0791e5e844ab6af22de94aa9a44c
```

The harness accepts no target argument and requires 12 GiB free on supported
fixed builder storage. It creates its own loops, retains the workspace if
cleanup fails, and imports no untrusted executable fixture. Separate native
signal tests queue actual SIGINT and handled SIGTERM around publication,
including the helper-return boundary; independent review verified conservative
receipts and restored masks. The 367-case suite passes on Jammy without skips,
plus four separate diagnostic cases and shell regressions. ARM execution of
this new controller, actual host health, packaged recovery and all physical
acceptance remain open. The later offline controller has separate
[software recovery evidence](host-recovery.md#native-software-evidence).
