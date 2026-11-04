# Host updates and manual rollback

Routine updates must replace the inactive 4 GiB NVMe root, verify its complete
contents, and then explicitly publish its selector on NVMe state. The current
implementation prepares authenticated **files on the builder**. It does not yet
write an installed root partition, activate a candidate, or perform rollback.
Do not copy a prepared selector into live state by hand. Physical update and
interruption testing remain release gates.

## Prepare a compatible root candidate

Use the trusted checkout staged under the root-owned `0700` directory
`/var/lib/a733-update-work-20261104` on the Jammy builder. Existing build-workspace
ancestors include a group-writable directory and are deliberately unsuitable
for this preparer’s private output. Obtain the payload-manifest
SHA-256 through the trusted release/build record; a hash supplied alongside an
untrusted download provides no authentication. The initial preparer accepts a
raw regular root image, not gzip or a block device. It requires at least 5 GiB
free in the output filesystem even when sparse capture uses less disk.

```sh
sudo -i
cd /var/lib/a733-update-work-20261104
payload=/home/drew/orange-pi-4-pro-build/package-state-work-20261104/out/host-payload-package-state-20261104-01
manifest_sha256=51f8dfa7f0f66b005b9bfd08dc5f29fcf25f0791e5e844ab6af22de94aa9a44c
/usr/bin/python3.10 -I -B scripts/prepare-host-update.py \
  "$payload/root-B.ext4" \
  /var/lib/a733-update-work-20261104/candidate-B \
  --slot B --payload-manifest "$payload/manifest.json" \
  --manifest-sha256 "$manifest_sha256" --storage-kind external
```

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

## Required installation and activation protocol

The remaining controller must identify the actual NVMe model, serial, capacity,
sector geometry and GPT roles, and prove the selected root and state belong to
that drive. It must reject the active slot, mounted/in-use inactive partitions,
source overlap and changed identities. Before physical writes, display the
exact target and obtain operator authorization. Hold an exclusive inactive-slot
descriptor and serialize all update/activation writers using NVMe state.

Write every authenticated root byte, flush, and independently read back the
entire slot before offering activation. Preserve the previous verified selector
and a verified off-device state backup. A partial write must leave the current
selection intact. Recheck the current selection under the update lock, then use
the existing fsync/rename/fsync publication helper. A publication error after
rename means the visible selection must be inspected before proceeding.

Activation must not automatically declare success. After an explicit reboot,
verify the selected marker, NVMe-backed state and package views, management
reachability, and actual KVM guest startup. These checks have not run on the
board. The controller must not write the boot partition, SD or SPI during a
routine root update. Kernel/DTB/initramfs changes require a separate recovery
and maintenance procedure.

## Manual rollback and recovery gates

Keep serial access and the diagnostic/recovery SD available before activation.
If the new root fails health checks, recovery must verify the prior root and
restore its selector under the same locking, identity and publication rules.
Guest storage must remain untouched. Shared-state changes must be backward
compatible or have a tested restore path; package-state isolation alone does
not establish application/state compatibility across releases.

There is no installed rollback command or automatic fallback policy yet. Do
not advertise unattended updates. Required disposable-data tests include
interruption during inactive-slot writes, before and after selector publication,
manual A→B→A rollback, failed health checks, missing NVMe, and state-backup
restoration. File fixtures and builder loop tests cannot establish power-loss
behavior or board recovery. The complete criteria remain in the
[hypervisor plan](hypervisor-plan.md#release-acceptance-and-documentation).
