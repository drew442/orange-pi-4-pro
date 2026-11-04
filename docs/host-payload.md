# Immutable A/B host payload prototype

The builder produced two raw 4 GiB ext4 root-slot images from the authenticated
[interim ARM64 userspace](host-userspace.md) and matching A733 kernel modules.
They include management runtime profile `a733-management-v1`: the management
helper, its configuration module, and systemd SSH service. Marker and manifest
records hash all three files. The images remain prototypes: they are not
installed or hardware-booted, and establish no KVM or SD-write acceptance.

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
management-runtime hashes, tools and provenance. A/B UUIDs are fixed for these
images; a future installer must assign unique GPT PARTUUIDs. No GPT layout or
physical storage identifiers are written by this recipe.

## Build and verification

On the prepared Ubuntu 22.04 builder, using its pinned inputs:

```sh
mkdir -p out
sudo python3 build/host-payload.py \
  /home/drew/orange-pi-4-pro-build/recipe/out/host-userspace-bookworm-20261001-01/rootfs.tar.gz \
  /home/drew/orange-pi-4-pro-build/artifacts \
  "$(pwd -P)/out/host-payload-20261104-02"
```

The command passed. Use a new output name to repeat it. It requires root,
refuses existing/symlink outputs and nonregular inputs, checks conservative
free-space requirements, and processes slots sequentially. No network, mounts
or physical-device operations occur.

Builder output:
`/home/drew/orange-pi-4-pro-build/management-work-20261104/out/host-payload-20261104-02/`.
See [manifest](evidence/managed-payload-20261104.json) and
[verification](evidence/managed-payload-verify-20261104.json).

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `root-A.ext4` | 4,294,967,296 | `9ae1b5daabff5f7468be5d30f526ead3c4d3e042953480f5b289bead454d3ff3` |
| `root-A.ext4.gz` | 173,053,400 | `0943c929d591db53b64557625e3dd1abe5462b83defdf83f9b270da19f3870c0` |
| `root-B.ext4` | 4,294,967,296 | `70b401c51e5363c182d5916b2b67e29fa2ca1e402f2589dced08e9345ff380a1` |
| `root-B.ext4.gz` | 173,053,267 | `f2747502ca3d52f467f57080b520a02e8c83f88602b17ac4507de47e3789e372` |

Both complete hashes and compressed expansions passed; management files match
the manifest. These are regular-file/content checks. Prior loop checks relate to
the original unconfigured prototype and do not establish loop behavior for
these managed images. Keep payloads on fixed builder storage or transfer them
over authenticated networking/USB; never stage them on SD or in RAM. The
diagnostic SD remains the first-boot candidate. No image authorizes storage
erasure or replaces the pending hardware gates.
