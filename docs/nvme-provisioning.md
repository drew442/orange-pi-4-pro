# NVMe provisioning preparation

The provisioning pipeline now assembles the proposed disk layout into a new
sparse regular file. This joins the previously separate roots, state and boot
artifacts before implementing physical writes. It does not identify, erase,
partition or install a physical NVMe, and implies no target authorization.
The [device planner](../scripts/plan-nvme.py) and
[guarded full-disk writer](nvme-installation.md) provide read-only planning and
explicit installation. Production whole-NVMe apply and physical acceptance
remain unperformed.

The fixture uses nominal 250 GB capacity, 512-byte logical sectors and 4096-byte
physical alignment. Actual SSD capacity and sector sizes must come from the
identified hardware. The writer rejects a mismatch; this fixture must
not be copied to an unknown target.

The published full-disk fixtures below were built at `c368563` before
`a733-package-state-v1`. They remain frozen and nondeployable. Current
[root/state/bootstrap components](host-package-state.md) use the new ABI;
new assembly rejects older incompatible inputs. Do not mix their state UUIDs
or selectors. No new full-disk package-state fixture is claimed here.

## Integrated layout

| Role | Start byte | Bytes | Filesystem |
| --- | ---: | ---: | --- |
| Boot | 1,048,576 | 536,870,912 | Legacy-reader ext4, no journal |
| Root A | 537,919,488 | 4,294,967,296 | Read-only ext4, no journal |
| Root B | 4,832,886,784 | 4,294,967,296 | Read-only ext4, no journal |
| State | 9,127,854,080 | 8,589,934,592 | Journaled ext4 |
| Guests | 17,717,788,672 | 232,281,604,096 | Journaled ext4 |

The first MiB and 607,232 trailing bytes reserve GPT/alignment space. The
shared layout engine retains the planner's geometry for 512-byte and 4096-byte
logical sectors; the latter conservatively reserves 33 logical sectors at the
tail. Primary and backup GPT have 128 entries, CRCs and five Linux filesystem
data partition types. Disk GUID and all five partition GUIDs are fresh and
distinct on each assembly.

Root filesystem UUIDs retain the selected A/B role contract. State UUID and
machine identity come from per-installation state. Boot and guest filesystem
UUIDs are newly generated. Never clone an installation's state or treat fixed
root-role UUIDs as a disk identity. The current state is explicitly a fixture
with test MAC/CIDR and an unusable operator credential; it cannot provide real
operator access.

The boot filesystem contains the pinned kernel/DTB and the matching wrapped
host initramfs and boot script. It uses no journal, 64-bit or metadata checksum
features, matching the old bootloader reader's constraints. Its root is owned
by root with mode 0755. Guest storage uses the `largefile4` ext4 profile for VM
disk files; its root is root-owned mode 0700. The
[explicit activation controller](guest-activation.md) validates and mounts p5,
then grants root-directory traversal. Per-guest directory enrollment and
automatic boot integration remain work.

No NVMe boot0 or SPI firmware is written or synthesized here. The prepared boot
partition does not prove that the current firmware can discover or boot it.
Prefer SPI-to-NVMe after hardware validation; until then the
[SD host bootstrap](host-sd-bootstrap.md) supplies the compatible kernel/DTB and
handoff, with no routine Linux SD writes. Firmware write behavior remains a
separate hardware gate.

## Assembly contract

`build/nvme-image.py` accepts trusted root, state and SD-bootstrap build
manifests plus pinned diagnostic components. It checks state/bootstrap UUID and
ABI, both immutable root markers and boot-stack hashes, the selected root
marker/selector, and state ext4 identity/format. Both full root images and state
are captured and authenticated privately before parsing or placement.

The recipe creates only a new absolute repository `out/nvme-image-*` directory.
It rejects symlink paths and recognizable MMC, removable, RAM or unsupported
backing storage, including device-mapper slaves. At least 22 GiB fixed builder
storage must be free before staging. USB readers with indistinguishable fixed
sdX identities remain an operator responsibility: locate the checkout on known
fixed builder storage. Selected Jammy tool versions/architectures are checked
against the committed builder package lock.

Sparse placement is valid only for the newly created, zero-filled regular
output. It must never be reused as a physical writer: skipping zero ranges on
an existing disk would preserve old bytes and violate the image hash. The
recipe explicitly rejects nonregular destinations. Its output hash covers all
logical bytes, including holes, rather than just allocated data.

Fresh disk/partition/filesystem identities and filesystem timestamps mean
byte-identical output is not claimed. A failed build leaves an incomplete
private output; a successful manifest is written only after placement, file
synchronization and complete logical hashing. Retry with a new output name.
Do not distribute or deploy an incomplete directory.

## Builder commands and artifact record

On the prepared Ubuntu 22.04 builder, from a clean checkout at `c368563`:

```sh
sudo python3 build/nvme-image.py \
  /home/drew/orange-pi-4-pro-build/management-work-20261104/out/host-payload-20261104-02 \
  /home/drew/orange-pi-4-pro-build/management-work-20261104/out/host-state-20261104-04 \
  /home/drew/orange-pi-4-pro-build/host-sd-work-20261104/out/host-sd-managed-20261104-01 \
  /home/drew/orange-pi-4-pro-build/artifacts \
  "$(pwd -P)/out/nvme-image-250GB-repeat-20261104" \
  --size-bytes 250000000000
sudo python3 tests/verify_nvme_image.py \
  "$(pwd -P)/out/nvme-image-250GB-repeat-20261104"
```

Both commands passed. Use a new output name for another run. These commands read
private fixture inputs and create only builder files. No new build packages or
compiler jobs were needed. Full logical hashing takes several minutes even
though sparse allocation is small.

The first build is retained under
`/home/drew/orange-pi-4-pro-build/nvme-work-20261104/out/nvme-image-250GB-20261104-01/`.
Its `nvme.img` is exactly 250,000,000,000 bytes, allocates 1,205,710,848 bytes,
and has SHA-256
`1240abe267b9099467c8cdb6d2c4963070620cebd07bf0c43e2f6771adb2bb30`.
The [manifest](evidence/nvme-image-20261104.json),
[independent readback](evidence/nvme-image-verify-20261104.json) and
[builder resource samples](evidence/nvme-build-resources-20261104.json) record
actual results. Raw samples/logs remain outside Git. No full disk image is
stored in Git or staged on SD.

Readback independently decodes both GPTs and their CRCs, checks full-image and
root/state hashes, five filesystem UUIDs/features, boot-file contents,
root-marker/state-selection pairing, and boot/guest root permissions. It
captures partition views from one pinned regular-file descriptor and compares
inode, size and modification/change times afterward. This is an integrity
check against a trusted manifest on quiescent builder storage, not signature
authentication or a power-cut test. Before running it, confirm `/var/tmp` is on
known fixed storage with sufficient free space for sparse captures; this was
verified on the prepared builder.

A [clean-checkout repeat](evidence/nvme-image-rebuild-20261104.json) used the
same cached authenticated inputs and recipe bytes. It generated fresh GPT,
boot and guest identities while preserving the explicit state fixture. The
second full image also passed independent readback. Its SHA-256 is
`6aa51794bf82b3814568a526c9242b5abbe041af603255d2b3b9438847fc4d98`.
This reproduces integration/inspection from committed source, not a complete
clean kernel/firmware/userspace build or physical deployment.

## Remaining installer and acceptance work

The [writer](nvme-installation.md) now implements exact identity, review plan,
interactive confirmations, locking/revalidation, full written-byte verification
and conservative interruption reporting. Its production whole-NVMe path still
needs authorized hardware execution. The [compressed bundle and acquisition tool](nvme-distribution.md) implement
verified delivery, with board Ethernet/USB acquisition still untested. Backup
creation/restore and the complete deployment procedure remain integration work; the planner observation alone authorizes no erasure.

A [builder archive inventory](evidence/installer-runtime-inventory-20261104.json)
confirmed Python 3.11, its 26.6 MB uncompressed standard library, `lsblk`,
ext4 tools, curl and SSH in the authenticated interim userspace. `sfdisk` is
absent; the pure GPT encoder avoids relying on it for table serialization.
The [installer preparation runtime](installer-preparation.md) now packages
selected ELF/Python dependencies and networking data in a 26.8 MB compressed
initramfs; isolated ARM software probes passed. Real board boot, DNS/TLS and the
physical installation remain unproven. Inventory alone is not runtime acceptance.

Inactive-slot updates, serial/manual rollback, backward-compatible state
migrations, package metadata handling, backup/restore and runtime guest storage
activation remain open. SPI programming requires its own compatibility,
backup/recovery checks and explicit exact-target approval. No fixture operation
replaces actual boot, management, KVM, zero SD writes or reproduced deployment.

## Format references

The codec uses the [Linux v6.6 GPT structures](https://raw.githubusercontent.com/torvalds/linux/v6.6/block/partitions/efi.h)
and [GPT validation/CRC implementation](https://raw.githubusercontent.com/torvalds/linux/v6.6/block/partitions/efi.c)
as primary references. Partitioning and filesystem behavior are also checked
against the pinned installed util-linux and e2fsprogs tools. The planned role
budgets and physical release gates remain in the [hypervisor plan](hypervisor-plan.md).
