# Immutable A/B host payload prototype

The builder produced two **4 GiB ext4 root-slot payloads** from the authenticated
[interim ARM64 userspace](host-userspace.md) and matching A733 kernel modules.
These files are unconfigured prototypes. Boot selection, persistent state,
management access and an installer are still required before deployment.
They have not booted on the board and establish no KVM or SD-write acceptance.

## Format and provenance

Each image has a distinct slot UUID, no journal and the ext4 `read-only` feature.
The pinned kernel supports ext4 ACLs/security xattrs. Its
[`fs/ext4/super.c`](https://github.com/orangepi-xunlong/linux-orangepi/blob/2ac08e8c7cdc28abbdc5c9a9dd812f887ae9c79f/fs/ext4/super.c)
forces a read-only mount for this feature and rejects read-write remounts.
See also the [upstream superblock definition](https://www.kernel.org/doc/html/latest/filesystems/ext4/super.html).

`mke2fs` cannot set this feature during creation with the selected tools. The
recipe creates/populates a journal-free filesystem, then uses `tune2fs -O read-only`
and verifies its superblock and `e2fsck -fn` result. Filesystem creation receives
only newly created regular files, never storage-device arguments.

The builder checks the exact userspace tar, kernel Image, DTB, kernel config and
diagnostic initramfs against committed evidence hashes/sizes. It validates the
entire tar namespace before GNU extraction, preserving ownership, devices,
ACLs and xattrs. Only regular module files/directories for `6.6.98-a733-diag`
are imported from the authenticated initramfs. The resulting module BOM contains
332 files. It executes no package scripts or chroot commands.

Each root contains `/usr/lib/orangepi-hypervisor/payload.json`, identifying its
slot, kernel release, source hashes and prototype status. The output also retains
compatible diagnostic inputs, recipe/source evidence, builder package locks and
selected filesystem/archive tool versions. The fixed filesystem UUIDs distinguish
A/B images; the future installer must assign unique GPT PARTUUIDs for each target.
No GPT layout or physical storage identifiers are written by this recipe.

## Build and verification

On the prepared Ubuntu 22.04 builder, from the repository root:

```sh
mkdir -p out
sudo python3 build/host-payload.py \
  /home/drew/orange-pi-4-pro-build/recipe/out/host-userspace-bookworm-20261001-01/rootfs.tar.gz \
  /home/drew/orange-pi-4-pro-build/artifacts \
  "$(pwd -P)/out/host-payload-20261104-01"
```

The actual command passed. Use a new output name on another run. The recipe
requires root to preserve authenticated metadata and refuses existing/symlink
outputs or nonregular input files. It checks available space conservatively for
two images, compressed copies, staging and reserve, and processes slots sequentially.
No network, mounts or physical-device operations occur during packaging.

Builder output: `/home/drew/orange-pi-4-pro-build/project/out/host-payload-20261104-01/`.
Logs: builder `/home/drew/orange-pi-4-pro-build/host-payload-build.log` and
`host-payload-resources.log`. Latest free disk was about 34 GiB; available memory
about 7 GiB. Full cross-builder byte reproducibility remains unproven.

[Manifest](evidence/host-payload-20261104.json):

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `root-A.ext4` | 4,294,967,296 | `09702dfbce62849aa6722a2121f8fcdb7e3491c187c951d5a01ac5dd9c4c4589` |
| `root-A.ext4.gz` | 173,047,158 | `98ad36698bcc633eb54e94e0eec579eed95582d56820f3d18484454e24680fd0` |
| `root-B.ext4` | 4,294,967,296 | `b724b4f6e36222e777103b2f185714c81130ba40afbdb0fa68fd7afd981cc22f` |
| `root-B.ext4.gz` | 173,046,547 | `92be4186bf3d5ba96856c1f8635b1c6be3b694e9b5ec2ccff4114a310acac60e` |

All manifest hashes and compressed expansions passed. Independent content checks
confirmed both slot markers, root ownership, `/dev/null` device identity, empty
machine-id, absent SSH host keys, preserved package `security.capability`, and
Ethernet module bytes/vermagic. These are file-content checks, not board operation.

## Opt-in file-loop test

Host regressions require no root or mounts:

```sh
python3 -m unittest discover -s tests -p 'test_host_payload.py'
python3 -m unittest discover -s tests -p 'test_host_root_loop.py'
```

The separate privileged verifier accepts only regular ext4 image files with the
read-only feature and no journal. It runs in a private mount namespace, pins the
validated input descriptor and creates a hash-matching private sparse copy under
root-controlled `/var/tmp`. It attaches that copy to a writable loop device,
checks the block read-only flag is zero, and requires both read-write-remount
rejection and an `EROFS` write result. It then unmounts/detaches its own loop and
checks original/staged bytes remain unchanged.

```sh
sudo python3 tests/verify_host_root_loop.py "$PAYLOAD/root-A.ext4"
sudo python3 tests/verify_host_root_loop.py "$PAYLOAD/root-B.ext4"
```

Here `PAYLOAD` is the builder output directory above. Both commands passed on
builder kernel `6.8.0-138-generic`; reports are retained for
[A](evidence/host-payload-loop-A-20261104.json) and
[B](evidence/host-payload-loop-B-20261104.json). This proves the tested host
filesystem behavior. Physical A733 boot, NVMe reliability and all release
[hardware gates](hardware-validation.md) remain pending.

These payloads belong on NVMe, transferred over authenticated networking or USB.
Do not stage them on the SD card or in RAM. The small diagnostic SD remains the
first-boot image; these payloads do not replace it or authorize storage erasure.
