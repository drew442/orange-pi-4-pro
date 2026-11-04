# Mutable root choice on NVMe

The bootstrap now identifies state, not an active root. Recovered NVMe state
contains `boot/selected.conf`, with exactly five newline-terminated fields:

```text
SCHEMA=1
SLOT=A
ROOT_UUID=a7330000-0000-4000-8000-00000000000a
ROOT_MARKER_SHA256=650e7550efb731044f901468603c2720eed985a0c6437e2624f6a3fa8704df2f
KERNEL_RELEASE=6.6.98-a733-diag
```

That example is the actual pinned A payload. B uses its own fixed slot UUID and
marker. Both retain the same compatible boot stack. Root choice and marker are
absent from immutable bootstrap configuration; fixed role UUIDs for both slots
remain part of the resolver. The [handoff](host-handoff.md) recovers state before
reading selection and probes only the selected root filesystem.

## Verified preparation commands

From the prepared builder checkout:

```sh
sudo python3 build/root-selection.py \
  /home/drew/orange-pi-4-pro-build/project/out/host-payload-20261104-01/root-A.ext4 \
  "$(pwd -P)/out/root-selection-A.conf" --slot A
sudo python3 build/root-selection.py \
  /home/drew/orange-pi-4-pro-build/project/out/host-payload-20261104-01/root-B.ext4 \
  "$(pwd -P)/out/root-selection-B.conf" --slot B
```

Both commands passed; choose new output names when repeating. The preparer
captures the regular root image into a private sparse temporary file on builder
disk, authenticates the captured bytes against committed payload evidence, and
reads its marker through that snapshot. It verifies slot, read-only/no-journal
format, UUID, ABI and compatible boot inputs. It then writes a new regular
selector file under `out/`. No installed partition is read or changed.

The state recipe now requires `--boot-selection out/root-selection-A.conf` and
seeds a private boot directory and canonical selector. Current fixture build and
both prepared records are in
[evidence](evidence/nvme-selection-state-build-20261104.json); the
[state-image readback](evidence/nvme-selection-state-20261104.json) checked the
actual seeded file, full image and compressed expansion. Fixture credentials and
network values remain unsuitable for deployment.

## Publication helper and remaining update work

`atomic_store()` in `build/root-selection.py` implements file fsync, atomic rename
and directory fsync. Its caller must identify and authorize the target directory
and serialize writers. It is used for fresh staging; there is **no installed
activation CLI**, inactive-slot writer or complete update/rollback controller yet.
The directory API alone does not establish that a target is NVMe.

Six regression tests cover strict schema, private capture, successful replacement,
unsafe directories and publication faults. A failure before rename retains the
old record and removes the temporary file. A directory-sync failure after rename
reports `published=True`: the new record is visible but durability is uncertain.
These are controlled file/fault fixtures, not power-cut or hardware evidence.

Before activation is delivered, verify the written inactive image, preserve a
usable previous record, quiesce slot writers, and check management plus actual
KVM health. Test interrupted publication and manual rollback on disposable data.
Automatic fallback/rollback needs separate proven policy; none is claimed.
Kernel/DTB/initramfs maintenance remains separate from routine root updates.
