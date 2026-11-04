# Mutable root choice on NVMe

The bootstrap identifies state, not the active root. Recovered state contains
`boot/selected.conf`, exactly five newline-terminated fields:

```text
SCHEMA=1
SLOT=A
ROOT_UUID=a7330000-0000-4000-8000-00000000000a
ROOT_MARKER_SHA256=f03ac5ff3e51820d25c470a77e6f74488b7aad27c4e4040c30af741cd03ea842
KERNEL_RELEASE=6.6.98-a733-diag
```

That record selects the current managed A payload. B uses its own fixed UUID
and marker. Root choice and marker are absent from immutable bootstrap config;
fixed role UUIDs for both slots remain in the resolver. The
[handoff](host-handoff.md) recovers state before selection and probes only the
selected root filesystem.

## Verified preparation

The preparer requires an explicit trusted manifest. It snapshots a regular
payload, authenticates full bytes, checks slot, marker, filesystem format, UUID,
ABI and compatible boot inputs, then creates only a new selector file. No
installed partition is read or changed.

```sh
sudo python3 build/root-selection.py \
  /home/drew/orange-pi-4-pro-build/management-work-20261104/out/host-payload-20261104-02/root-A.ext4 \
  "$(pwd -P)/out/managed-selection-A.conf" --slot A \
  --payload-manifest /home/drew/orange-pi-4-pro-build/management-work-20261104/out/host-payload-20261104-02/manifest.json
sudo python3 build/root-selection.py \
  /home/drew/orange-pi-4-pro-build/management-work-20261104/out/host-payload-20261104-02/root-B.ext4 \
  "$(pwd -P)/out/managed-selection-B.conf" --slot B \
  --payload-manifest /home/drew/orange-pi-4-pro-build/management-work-20261104/out/host-payload-20261104-02/manifest.json
```

For the current prepared A selection, state creation uses
`--boot-selection "$(pwd -P)/out/managed-selection-A.conf"`; see
[state guide](host-state.md). The prepared selector is
`$(pwd -P)/out/managed-selection-A.conf` when run from this checkout. The new
state fixture and both managed root images are prototypes, not installed or
hardware-booted.

## Publication and remaining update work

`atomic_store()` in `build/root-selection.py` implements file fsync, atomic
rename and directory fsync. Its caller must authorize the target directory and
serialize writers. It is used for fresh staging; there is no installed
activation CLI, inactive-slot writer or complete update/rollback controller.
Fault fixtures show pre-rename failure retains the old record; post-rename
directory-sync failure reports the new record published with durability
uncertain. These are file/fault fixtures, not power-cut or hardware evidence.

Before activation, verify the written inactive image, preserve a usable prior
record, quiesce writers, and check management plus actual KVM health. Interrupted
publication and manual rollback need testing on disposable data. Automatic
fallback/rollback requires separately proven policy. Kernel/DTB/initramfs
maintenance is separate from routine root updates. SPI-to-NVMe preference,
zero routine SD writes, installer safety, backup and physical acceptance remain
open.
