# Immutable A/B host payload prototype

Historical generated paths below may have been archived. Consult the
[retention map](build-cache-retention.md#replay-and-prototype-output-retention)
before reusing them; use the [personalized deployment](deploy-personalized-20261106.md)
for the current installation.

The builder produced two raw 4 GiB ext4 root-slot images from the authenticated
[interim ARM64 userspace](host-userspace.md) and matching A733 kernel modules.
They include management runtime profile `a733-management-v1`: the management
helper, configuration and package-state modules, and systemd SSH service.
Marker and manifest records hash all four files. The
[current root build](guest-tools-package.md) also includes the complete closed
maintenance/guest tree at `/usr/lib/a733-tools`, with its own exact BOM and
immutable modes. The artifact records below retain the earlier package-state
components; they have not been silently replaced. The images remain prototypes: they are not
installed or hardware-booted, and establish no KVM or SD-write acceptance.

The optional `--userspace-profile normalized-20261106` selects the authenticated
normalized archive; omission preserves the baseline. See [profile selection and
actual staging evidence](userspace-profiles.md). This does not replace the
published installation artifacts.

## Format and provenance

Each image has a distinct slot UUID, no journal and ext4 `read-only` feature.
The pinned kernel forces read-only mounting for that feature. The recipe creates
and populates a journal-free filesystem, applies `tune2fs -O read-only`, then
checks the superblock and `e2fsck -fn`. Filesystem tools receive new regular
files, never storage-device arguments.

The builder checks source archives, kernel Image, DTB, kernel config and
diagnostic initramfs against committed evidence. It validates the complete tar
namespace before extraction, preserving ownership, devices, ACLs and xattrs.
Only authenticated module files/directories for `6.6.98-a733-diag` are imported;
no package scripts or chroot commands run. Root passwords are locked,
machine-id is empty, and SSH host keys are absent. The later installed-NVMe
management helper generates host keys there; test keys existed only in a
disposable ARM chroot.

The build manifest records slot, source hashes, prototype classification,
management-runtime hashes, tools and provenance. The new package metadata ABI is
`a733-package-state-v1`; roots require the matching state/bootstrap pairing.
Eight version-owned metadata views are stored under
`/usr/lib/orangepi-hypervisor/package-state` and are bound read-only at runtime.
Shared-state copies of these metadata databases remain present but
nonauthoritative. A/B UUIDs are fixed for these
images; the integrated assembler assigns fresh GPT identities. No GPT layout or
physical storage identifiers are written by this recipe.

## Build and verification

On the prepared Ubuntu 22.04 builder, using its pinned inputs:

```sh
mkdir -p out
sudo /usr/bin/python3.10 -I -B build/host-payload.py \
  /home/drew/orange-pi-4-pro-build/recipe/out/host-userspace-bookworm-20261001-01/rootfs.tar.gz \
  /home/drew/orange-pi-4-pro-build/artifacts \
  "$(pwd -P)/out/host-payload-package-state-20261104-01"
```

The command passed. Use a new output name to repeat it. It requires root,
refuses existing/symlink outputs and nonregular inputs, checks conservative
free-space requirements, and processes slots sequentially. No network, mounts
or physical-device operations occur.

Builder output:
`/home/drew/orange-pi-4-pro-build/package-state-work-20261104/out/host-payload-package-state-20261104-01/`.
See [manifest](evidence/package-payload-20261104.json) and
[verification](evidence/package-payload-verify-20261104.json).

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `root-A.ext4` | 4,294,967,296 | `6157897213b918c82241a6ff74920c2d5b92eb45cb30bc0333d8d97a58be733e` |
| `root-A.ext4.gz` | 143,285,038 | `0ee3eca8df37f38ac9a1683ea41e0ed40609e8edad166090fbcc13b8079194d4` |
| `root-B.ext4` | 4,294,967,296 | `a7c98b1cff05ccb9a77857d4d7b3a4bfd36fd6734a05866d911d5d2adbaa1e27` |
| `root-B.ext4.gz` | 143,285,222 | `a7dfe4f6d46bd1c41f272177c019ce35975a39ad0655c5b71e05957cde575d8a` |

Both complete hashes and compressed expansions passed; management and package-state files match
the manifest. ARM package-query and managed-root loop evidence is recorded in
[package ARM checks](evidence/package-payload-arm-20261104.json) and
[state loop checks](evidence/package-state-loop-20261104.json). The tiny A→B→A VFS cycle used distinct synthetic package versions. A separate
probe mounted both actual managed roots and queried their packages under
ARM64 binfmt/chroot; neither is physical NVMe/board evidence. Three caches were pruned only from private builder
staging (77,294,968 bytes per extracted userspace tree; both roots and fresh state omit them). The eight
metadata database copies under shared `/var` remain but are nonauthoritative. Keep payloads on fixed builder storage or transfer them
over authenticated networking/USB; never stage them on SD or in RAM. A [clean repeat](evidence/package-rebuild-20261104.json) reproduced root markers
and package descriptors; raw filesystem byte identity is not claimed. The
diagnostic SD remains the first-boot candidate. No image authorizes storage
erasure or replaces the pending hardware gates.
