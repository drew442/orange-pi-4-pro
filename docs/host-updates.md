# Host updates

Use the matching installed tree, preserved compatible A/B inputs and
[maintenance guide](maintenance-guide.md). The current status identifies actual
artifacts; archived trial payloads are not automatically beta assets. Shared
admission and stopped-guest protections remain required in manual-beta.

## Prepare a compatible root candidate

Use the trusted checkout staged under the root-owned `0700` directory
`/var/lib/a733-update-final-20261104` on the Jammy builder. Existing build-workspace
ancestors include a group-writable directory and are deliberately unsuitable
for this preparer’s private output. Obtain the payload-manifest
SHA-256 through the trusted release/build record; a hash supplied alongside an
untrusted download provides no authentication. The initial preparer accepts a
raw regular root image, not gzip or a block device. It requires at least 5 GiB
free in the output filesystem even when sparse capture uses less disk.

```sh
sudo -i
cd /var/lib/a733-update-final-20261104
payload=/home/drew/orange-pi-4-pro-build/package-state-work-20261104/out/host-payload-package-state-20261104-01
manifest_sha256=51f8dfa7f0f66b005b9bfd08dc5f29fcf25f0791e5e844ab6af22de94aa9a44c
/usr/bin/python3.10 -I -B scripts/prepare-host-update.py \
  "$payload/root-B.ext4" \
  /var/lib/a733-update-final-20261104/candidate-B \
  --slot B --payload-manifest "$payload/manifest.json" \
  --manifest-sha256 "$manifest_sha256" --storage-kind external
```

The commands above describe the recorded B preparation. Choose a new output
name for a repeat; existing directories are deliberately refused. The same
command with `root-A.ext4`, `--slot A` and `candidate-A` prepares A.

The new private directory contains the captured `root.ext4`, the exact trusted
`manifest.json`, a proposed `selected.conf`, and a `candidate.json` receipt.
The manifest pin authenticates bytes before JSON parsing. The complete captured
root is authenticated before filesystem metadata is parsed. Its slot UUID,
read-only ext4 format, kernel ABI, managed-host profile, package-state descriptor
and pinned diagnostic boot inputs must match. A slot A image cannot substitute
for slot B. See [package-state isolation](host-package-state.md) and
[root selection](nvme-root-selection.md).

The output parent must have safe ownership and permissions and reside on
explicitly selected RAM or supported local storage. SD/MMC backing, including
through device mapper, is rejected. A USB reader cannot be distinguished from
another USB disk using this check; identify external storage before selecting
it. No physical target is selected or authorized by candidate preparation.

Publication uses a new private directory and a no-replace rename. Existing
outputs are refused. Files and directories are synced; a failure after rename
reports that publication occurred but durability is uncertain. These semantics
follow [renameat2](https://man7.org/linux/man-pages/man2/rename.2.html) and
[fsync](https://man7.org/linux/man-pages/man2/fsync.2.html). A receipt is evidence
of preparation, not an authorization token or a substitute for rechecking bytes
before writing.

## Verified builder checks

Both actual 4 GiB package-state roots were captured from producer commit
`8cd3947f575681fab59268b59f8175fbd194bd84`. Each took about 18 seconds and
30 MiB peak memory. An independent file verifier matched the complete source
and captured hashes, original selectors, exact trusted manifests, receipt
bindings and private permissions. The roots represent the same userspace
version; this does not test cross-version updates or state compatibility.
[Evidence and hashes](evidence/host-update-candidates-20261104.json) distinguish
these checks from hardware acceptance.

Run from the trusted checkout as root, with the preceding `payload` and
`manifest_sha256` variables set:

```sh
/usr/bin/python3.10 -I -B tests/verify_host_update_candidates.py \
  /var/lib/a733-update-final-20261104 "$payload" \
  /home/drew/orange-pi-4-pro-build/package-state-work-20261104/out \
  --manifest-sha256 "$manifest_sha256"
```

The verifier expects `candidate-A` and `candidate-B`, plus the independently
prepared `package-selection-A.conf` and `package-selection-B.conf` in the
reference directory. It imports no producer code and performs regular-file
reads only. Run without Python optimization; `-O` is refused. The shared
filesystem metadata reader now bounds output while reading, caps the root
marker at 64 KiB, and limits each extraction to 30 seconds plus up to five
seconds for child cleanup. This preparation-code change does not rebuild or
deploy previously recorded bootstrap images.

## Stage the inactive slot

`scripts/update-host.py` requires the complete authenticated
[maintenance package](maintenance-tools.md) on NVMe or RAM, root privileges and
the expected live host mounts/configuration. The current installed host includes the matching controller at
`/usr/lib/a733-tools/scripts/update-host.py`. Revalidate its authenticated tree;
only hosts or recovery runtimes lacking that tree need portable-package staging.
The candidate directory and its three authoritative files must be root-owned with modes `0700` and `0400`.
Candidate receipts are informational; the writer authenticates the manifest,
entire root and proposed selector again. Every held source file is checked for
SD/MMC backing and inactive-target overlap, including individual bind mounts.

The read-only `prepare` command requires explicit `--target`, `--expected-model`,
`--expected-serial`, `--expected-bytes`, `--slot`, `--candidate` and
`--manifest-sha256` arguments. These must come from the actual host inventory and
trusted release record. The known target is recorded in
[implementation status](implementation-status.md); revalidate that live identity
before preparing any device plan. [Physical changed-B staging and interruption/retry now pass](physical-host-update-rollback-20261110.md).
`--slot` must be opposite the
running slot; a pending selection change requires a reboot before staging.

Save the resulting JSON plan on NVMe or RAM. Invoke `apply` with the same
arguments and `--plan /absolute/path/to/reviewed-plan.json` from an interactive
terminal. The source candidate displays the exact slot, partition, model,
serial, byte count, root hash and plan hash, then asks one `[yes/no]` question.
Only `yes` accepts; blank/no/EOF/non-TTY refuses. Verify the off-device backup
beforehand; the answer is not backup evidence. There is no force switch.

Full writer readback defaults off. For troubleshooting, include
`--verify-readback` on both `prepare` and `apply`; the Boolean is bound into the
plan, so changing it requires a fresh plan. Existing source authentication and
selection checks show byte progress and remain required. No added scan or
hardware benchmark is performed.

These new controls remain uninstalled. The retained legacy controller used for
initial migration keeps its version-specific prompts and mandatory readback;
follow its pinned procedure rather than supplying unsupported candidate flags.
The coordinator uses persisted known-target authorization for that transition.

The controller compares the plan before and after authorization, locks
`boot/update.lock` on NVMe state, and opens only the inactive partition with
`O_EXCL`. It checks full primary/backup GPT bytes, all five kernel partition
geometries, live root/state/selector identities, and inactive busy indicators.
Active root, state and guest mounts remain permitted. All 4 GiB, including zero
ranges, are written and flushed. Optional readback runs only when the reviewed
flag requests it. It never refreshes the partition table or publishes a selector.
A successful receipt binds the source, target and reviewed plan. Without readback,
`bytes_verified` stays zero and `verification_performed` is false;
`activation_ready` reports successful staging/flush, not verified storage bytes,
activation authorization or health. Selection still authenticates the full
installed root with progress instead of trusting a saved staging receipt.

On failure, assume the inactive slot may be incomplete whenever a write was
attempted. The report preserves attempted, written and verified byte counts.
Selection is never written by this command. A full explicit retry needs a new
plan after any changed slot bytes. SIGKILL or power loss may leave no report;
the recorded handled-SIGTERM physical case passes; power-cut behavior remains
unproved. Advisory locks require
cooperating maintenance tools; privileged raw-device users are not a supported
concurrency model.

## Staging software evidence

The [private-loop report](evidence/host-update-stage-loop-20261104.json) records
an injected write error after 8 MiB, conservative incomplete-slot reporting,
and a separate full 4 GiB retry with flush and complete SHA-256 readback.
Selection and unrelated boot/guest sentinels remained unchanged. Second
exclusive claims were refused; no partition refresh occurred. Independent
checks found the owned loop detached and all private workspaces removed.
The final run took 28.71 seconds with 34,212 KiB peak process RSS.

The test explicitly replaces the live NVMe context and TTY authorization with
fixtures. Production source authentication, block open/locks, geometry ioctls,
copy, flush and readback execute unchanged. This is not a live host update,
activation, rollback, physical interruption or board boot result. The source
root is the same-version B fixture. Separate policy fixtures cover exact
identity, active-slot refusal, GPT/4Kn geometry, busy targets and context races.

Verified opt-in command on the Jammy builder:

```sh
sudo /usr/bin/python3.10 -I -B \
  /var/lib/a733-update-stage-20261104/tests/verify_host_update_loop.py \
  /var/lib/a733-update-final-20261104/candidate-B \
  --manifest-sha256 51f8dfa7f0f66b005b9bfd08dc5f29fcf25f0791e5e844ab6af22de94aa9a44c
```

It accepts no target argument, creates only its own file-backed loop, requires
6 GiB free on supported fixed builder storage, and retains its workspace if
detach fails. It settles device events before each separate transaction and
keeps production nonblocking locks. The first retry was safely refused until
device events were settled; the lock owner was not identified. The test uses
direct hook replacements to avoid retaining copy buffers in mock call history.
For the underlying semantics, see [block-device O_EXCL](https://man7.org/linux/man-pages/man2/open.2.html)
and [advisory flock](https://man7.org/linux/man-pages/man2/flock.2.html).

## Explicit activation

Use the separate [root-selection controller](host-root-selection.md) after
staging. It does not trust a saved staging receipt: it reauthenticates the
complete installed root, shares the NVMe update lock, journals the original and
proposed selector, and explicitly publishes next-boot selection. The current
running root stays unchanged. Publication/interruption receipts distinguish
visible selection from confirmed durability; no automatic reboot or health
claim is made. The selector format and compatible immutable bootstrap remain
unchanged.

## Manual rollback and recovery gates

Keep serial access and the diagnostic/recovery SD available before activation.
If the new root fails health checks, recovery must verify the prior root and
restore its selector under the same locking, identity and publication rules.
Guest storage must remain untouched. Shared-state changes must be backward
compatible or have a tested restore path; package-state isolation alone does
not establish application/state compatibility across releases.

A healthy running host can select a retained old slot with its old trusted
manifest, and can cancel a pending selection before reboot. These paths do not
provide offline failed-boot recovery. Use the separate [RAM recovery
controller](host-recovery.md) when the installed host cannot run. Its native
software checks are distinct from physical recovery acceptance. There is no
automatic fallback policy; do not advertise unattended updates. Historical disposable-data checks covered
interruption during inactive-slot writes, before and after selector publication,
manual A→B→A rollback, failed health checks, missing NVMe, and state-backup
restoration. File fixtures and builder loop tests cannot establish power-loss
behavior or board recovery. The complete criteria remain in the
[hypervisor plan](hypervisor-plan.md#release-acceptance-and-documentation).
