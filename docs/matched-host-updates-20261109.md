# Current A/B maintenance payloads

The matched update candidates and portable maintenance package now pass
independent **regular-file artifact review**. No physical root update,
activation, interrupted-update recovery or rollback has been performed.
Leave the active November 9 endurance run undisturbed. These artifacts prepare
later physical tests; they do not authorize starting those tests now.

## Use the root manifest, not the whole-drive manifest

The current immutable roots use `a733-management-v2-timesync`, package-state v1
and kernel ABI `6.6.98-a733-diag`. Their **root-payload manifest** is 32613 bytes,
SHA-256 `4f28d75a14323c52aa976b00f957b56913b75472a07a717672abd33d439b97b9`.
Use that pin with update, selection and offline root recovery controllers.
The whole-NVMe installer manifest `f19b…` has a different schema and is not a
substitute. The historical November 4 `51f8…` candidates use management v1;
they are not this installation's current update candidates.

Both roots match the partition hashes in the protected full-NVMe distribution.
They are the **same userspace version** in two slots. This establishes matched
maintenance inputs, not a cross-version migration or state-compatibility test.

| Slot | Expanded bytes | Expanded root SHA-256 |
| --- | ---: | --- |
| A | 4294967296 | `d868571e89216762ce9d8d2b8209884b958c53b7be97a215d8c4ddbc96ab7940` |
| B | 4294967296 | `3369a8b4c4b0f7dd9ed33740db3bd86b0d868154bc8195c66806ee0e73f3bdab` |

The root markers retain their original diagnostic boot-input lineage. Those
old DTB hashes do not describe the current external VGIC/protected BOOT files.
Root maintenance must preserve the [current boot stack](current-images.md);
never replace the working DTB or bootstrap with the historical diagnostic files
listed in root provenance. These candidates contain no boot-firmware update.

## Retained files

On the Ubuntu builder, the ready **raw** candidates are:

```text
/var/lib/a733-matched-updates-20261109-01/candidate-A/
/var/lib/a733-matched-updates-20261109-01/candidate-B/
```

Each private mode-0700 directory contains root-owned mode-0400 `root.ext4`,
`manifest.json`, `selected.conf` and `candidate.json`. The manifest is an exact
copy of the authenticated producer manifest. The five-field selector is
independently reconstructed from that slot's actual filesystem marker. A
candidate receipt is informational; it is not a target authorization token.

Codex retains smaller transport copies under
`/home/drew/orange-pi-4-pro/out/matched-updates-20261109-01/`:

| Relative artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `candidate-A/root.ext4.gz` | 143469395 | `e74f7ee18b5c2f0fb364870ff208c1a46ea140db5c7e32208d00fc4c2affc161` |
| `candidate-B/root.ext4.gz` | 143469243 | `0327ef98eac45d1c7e973d8231d1a8afd020d8640ca5a22178335a1559085bef` |
| `maintenance/tools.tar.gz` | 124128 | `bbd617d6765323baf2d885d93451d1ebdd6600b8c6ad9aa3aaf7499838b23934` |
| `maintenance/manifest.json` | 16312 | `df70eed2e91561a75c270916822765f83dcc7c65c5fcee717584ab35bd70a1d6` |

Each native candidate directory also contains its small raw-candidate metadata
and selector. `payload-manifest.json` is the same trusted `4f28…` manifest.
`SHA256SUMS` covers retained files; authenticate that record through the trusted
repository/evidence rather than treating an untrusted adjacent checksum as trust.
Both gzip copies pass complete expansion/CRC/4 GiB hash checks without staging
raw roots on codex.

The codex transport directories are **not ready raw candidates**: the staging
CLI requires `root.ext4`, not gzip. For later use, authenticate the selected
compressed input, expand only that slot into a fresh private directory on RAM
or NVMe, and stage its matching manifest/selector there. Set files root:root
0400 and the directory root:root 0700. Keep temporary compressed downloads
separate from the raw candidate directory. Do not expand both roots into a
small RAM filesystem or use SD for staging.

## Maintenance tools and durability

The installed host already contains `/usr/lib/a733-tools`. A read-only hardware
availability check matched **all 40 files** there against this reviewed portable
package, including the package descriptor and update/selection/recovery code.
It invoked no updater, selector, recovery command or guest. Revalidate the exact
tree before later use; do not infer a successful maintenance operation from
file availability.

The portable archive supplies the same 39 source files plus `package.json`,
with 16 entrypoints. It is retained for recovery environments or hosts lacking
the matching tree. Authenticate the archive and manifest before following the
[package staging procedure](maintenance-tools.md). Current normal-host operation
can use the matching installed tree without copying another wrapper.

The candidate producer syncs candidate files and publication directories.
The portable-package producer does not establish fsync durability by itself.
A separate coordinator postcheck synced all 42 published package files and all
eight relevant directories, including the output parent; native retention also
syncs files/directories. [Evidence](evidence/matched-host-updates-20261109.json)
separates integrity review, publication behavior and this durability check.

## Reproduce file preparation

The original producer payload is retained on the builder at:

```text
/var/lib/a733-clean-replay-20261106/host-roots-timesync-runtime-fixes-01/work/project/out/host-payload-timesync-runtime-fixes-01/
```

The checked preparer source is `scripts/prepare-host-update.py`, SHA-256
`464d44d7c8e868fd72f85f3ac5d6095d7d18d205528b0f55c379c9b2b30c3b4b`.
The actual job used the trusted checkout
`/var/lib/a733-maintenance-validation-20261108-01/project`, a 41-file source lock
matching Git `1e17745`, and new output names. It ran the following interface for
each slot; supply fresh absolute output paths in a trusted private parent:

```sh
: "${A733_UPDATE_PROJECT:?Set the authenticated preparer checkout}"
: "${A733_ROOT_PAYLOAD:?Set the retained producer payload directory above}"
: "${A733_NEW_CANDIDATE:?Set a fresh absolute candidate-B output directory}"
sudo python3 -I -B "$A733_UPDATE_PROJECT/scripts/prepare-host-update.py" \
  "$A733_ROOT_PAYLOAD/root-B.ext4" "$A733_NEW_CANDIDATE" --slot B \
  --payload-manifest "$A733_ROOT_PAYLOAD/manifest.json" \
  --manifest-sha256 4f28d75a14323c52aa976b00f957b56913b75472a07a717672abd33d439b97b9 \
  --storage-kind external
```

Use `root-A.ext4`, `--slot A` and a separate fresh output for A. Existing outputs
are refused. Per-candidate admission remains 5 GiB free, including the 1 GiB
reserve. The pair's actual stage-boundary samples stayed above 23.29 GB free and
7.54 GB available RAM. The builder job never accessed Orange Pi storage; the
separate availability check only read installed tool files.
The trusted builder job source, logs, receipts and exact commands are linked in
the evidence; its already-used paths must not be reused for a new run.

## Before physical update or rollback

After the workload outcome is retained, obtain a fresh live root/selector and
exact NVMe identity. Stage only the inactive slot. Verify off-device backups of
the current roots and mutable STATE before any destructive staging or selector
change; these unpersonalized source images are not a backup of current STATE.
Current STATE/identity/guest checkpoints must not be inferred from this bundle.
The operator's [known storage authority](evidence/storage-write-authorization-20261108.json)
persists; revalidate the concrete target and operation rather than asking for
that authority again.

Use [inactive-slot staging](host-updates.md), [explicit activation/manual rollback](host-root-selection.md)
and [offline recovery](host-recovery.md) with the current root-manifest pin and
matching tool tree. Those controllers retain their own plan, backup and TTY
requirements; the bundle changes none of their behavior. Physical staging,
interruption, booted rollback, dirty-journal recovery and missing-NVMe recovery
still need their own reviewed procedures and hardware evidence. An automatic
rollback mechanism is not claimed.
