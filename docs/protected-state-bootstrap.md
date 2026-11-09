# Protected STATE bootstrap producer

`build/protected-state-bootstrap.py` prepares a regular-file SD derivative for
Orange Pi 4 Pro A733. It changes the initial STATE probe to use the reviewed
block read-only protection in `configs/host-init`. It preserves the 256 MiB SD
layout, corrected VGIC DTB, compatible kernel, firmware and SD boot commands.
The 2 GB SD, nominal 250 GB NVMe, minimal host and zero routine SD write
requirements remain physical acceptance gates. A real KVM guest is required.

Two fresh output constructions on the qualified Ubuntu builder produce identical
raw/gzip SD images and initramfs artifacts. See [construction evidence](evidence/protected-state-bootstrap-20261109.json).
This is software construction evidence; physical boot and hardware acceptance
remain pending.
NVMe BOOT derivation and any physical installation are separate work.

From a trusted, quiescent checkout on the qualified builder, use a fresh output:

```sh
python3 build/protected-state-bootstrap.py \
  --base-sd /absolute/retained/a733-host-sd.img \
  --output "$PWD/out/protected-state-bootstrap-FRESH"
```

There are no input-pin overrides, arbitrary init arguments, mounts or device
writes. Inputs must be absolute regular files without symlink ancestors,
hardlinks or parent traversal; `/dev`, `/proc` and `/sys` input paths are refused.
Inputs remain open and are reauthenticated before the manifest is published.
Existing output paths are refused, including incomplete failed outputs.

The base raw is exactly 268,435,456 bytes, SHA-256
`f02c74b4c001f1608d0005b6ff23fe82bffa8c02f71e19e5b6573819125ae383`.
Its construction provenance is the corrected VGIC bootstrap recorded in
[the existing recipe evidence](vgic-runtime-bootstrap.md), with retained manifest
SHA-256 `47df42be022d9e378a034df25d31865a24337c9309b4e4fdc2710ea9c2462993`.
Authentication uses the whole raw pin before filesystem parsing; a separate
manifest is unnecessary input.

The embedded 4,762,436-byte uInitrd is pinned to
`b10bdad8671a32b4157bf6f7ff553618024fc928e59caf691b529c8658b12d16`.
The old init is `82515ff0fb49d0d3399f3cc3546a768742bd50db6500452bafa6ae1d105c3964`;
the reviewed replacement is
`2cf52e1cee8d4f2050fe3a90eca0a0a8c50b6f30718d210667f7273f9f08ab12`.
The source review is retained in the coordinator's
`tmp/boot-block-ro-review-20261109.json`; that source review establishes no
artifact or hardware acceptance. The producer refuses a different source hash.

The producer validates the legacy 64-byte header, payload size and both CRCs,
then decodes one size-bounded gzip member and a bounded newc archive. It replaces
only init data and its size field, retaining init metadata and every other
encoded record byte for byte. The ARM BusyBox hash
`0a41864f87a0737804f0f4a68a1cfa1ed22374f4f75a1be3eb376e2659ce1316`
and exact STATE UUID `360dfb39-f49f-487e-b307-0bb887c854c5` configuration are
checked. Only legacy data size, data CRC and header CRC fields change.

BOOT begins at 33,554,432 bytes and occupies 234,881,024 bytes. A private regular
ext4 copy is patched using `debugfs`; inode uid, gid, mode and all four timestamps
are restored. The complete allowed namespace, content, xattr absence and metadata
are compared, allowing only uInitrd size/data to differ. Ext4 allocation and
bookkeeping bytes can change. The completed raw's BOOT is extracted again and
compared. Every byte before BOOT must match the base and the pinned prefix hash
`da4a8db3aba8255a78ed2b60a251c9835dfa0b5f471f1f0590fe09abb722b556`.
Kernel SHA-256 `6f9269755c4c2bc196c094899fa1ff20292f4d762c4000f77fcbb0887d5f154a`,
VGIC DTB SHA-256 `1ba8f5926c0d76d12479bafcfcfce66be4a934ccfb6a5bcbcc7f7aa86c07f9f4`
and both SD command/script hashes are checked and preserved.

Outputs are raw SD, deterministic gzip SD, uInitrd, its gzip initramfs payload,
patch log and manifest. Gzip uses mtime zero, empty filename and level nine;
complete SD expansion is hashed against the raw. The manifest records the input,
source closure, archive record preservation, before/after filesystem namespace,
header, layout, output hashes, tool versions and resource samples. These are
software construction checks only.

Admission requires a 3,000,000,000-byte disk floor plus 1 GiB scratch budget and
1,000,000,000 available memory bytes. The peak bound covers raw plus worst-case
gzip (513 MiB together), one BOOT copy (224 MiB), boot inspection (64 MiB),
archive/payload allowance (80 MiB), and logs/manifest (16 MiB), with remaining
headroom. Runtime samples enforce the disk/memory floors around major phases and
after scratch cleanup. This bounded SD admission does not alter the full NVMe
producer's 23,760,999,827-byte admission guard.

Run the focused tests with:

```sh
python3 -m unittest discover -s tests -p test_protected_state_bootstrap.py
```

Fixtures cover header CRC/length, bounded gzip, malformed archives, exact
record preservation, unsafe paths and input mutation, output scope, resource
floors, excess namespace changes and a 16 MiB regular ext4 replacement. They do
not execute the full producer or substitute for dirty-STATE hardware boot,
SD-write, KVM or NVMe acceptance.
