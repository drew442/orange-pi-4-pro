# Pinned KVM guest fixture

Actual nonroot KVM CPU/disk/HTTP, clean shutdown and checkpoint/restart now pass
on the SPI/NVMe host. See [current implementation status](implementation-status.md)
and [operational evidence](evidence/spi-operational-20261111.json). The original
November 4 artifact records below describe file-only checks; they do not establish
hardware results for those particular bytes.
The current [HTTP network fixture](guest-networking.md) replaces gateway ping
with a host request/confirmation. Its new artifact pins and build are documented
there. The records below retain the earlier fixture used by file-only probes;
do not combine its old initramfs pin with the new expected HTTP behavior.

## Managed power-key fixture

The separate `managed-power-key-v1` profile builds a virtual GPIO power-button
driver and keeps the HTTP-confirmed guest running until a power-key event. It
syncs before requesting guest poweroff. The legacy three-argument build keeps
its existing init and kernel configuration. Keep `firstkvm` manually managed and
use a separately enrolled disposable guest for automation tests.

Two fresh Ubuntu 22.04 builds match every artifact-manifest file byte for byte.
[Build evidence and complete hashes](evidence/managed-fixture-build-20261112.json)
record the pinned kernel/compiler/BusyBox, source lock, resources and off-device
artifact copies. Kernel `Image` is 5,253,128 bytes; initramfs is 975,144 bytes;
the disposable disk is 134,217,728 bytes. This profile's actual KVM power-key and
QMP shutdown path remains untested on the board.

To reproduce on the prepared builder, using the exact pinned clean kernel tree
and static ARM64 BusyBox:

```sh
bash build/guest-fixture.sh \
  /var/lib/a733-managed-fixture-inputs-20261112-01/kernel-tree \
  /var/lib/a733-managed-fixture-inputs-20261112-01/busybox-arm64 \
  "$(pwd -P)/out/guest-fixture-managed-REBUILD" managed-power-key-v1
```

Choose a new output name. The recipe first verifies pinned packages and the
ARM BusyBox parser's exact shared-FD input-event reads. That userspace check is
not KVM acceptance. Final acceptance requires the real nonroot guest's CPU,
disk/counter and HTTP evidence, continued execution after confirmation,
authenticated QMP powerdown, guest sync, guest-owned SHUTDOWN, normal raw exit
zero, durable checkpoint and restart.

## Build and provenance

The guest uses the same pinned kernel source commit as the diagnostic host,
with a separate tiny `virt` configuration. Generic PCI, virtio block/network,
PL011 console, GIC/PSCI, ext4 and initramfs/script execution are built in.
The physical host keeps its compatible A733 kernel/device-tree stack.

On the prepared builder, from the repository root, with the diagnostic source
workspace already fetched:

```sh
mkdir -p out
build/guest-fixture.sh /home/drew/orange-pi-4-pro-build/kernel /home/drew/orange-pi-4-pro-build/busybox/bin/busybox "$(pwd -P)/out/guest-fixture-20261104-03"
```

The current entrypoint passed end-to-end on Jammy, including the fixed seed and
complete recipe archive. Choose a new output name for another build;
existing destinations are refused. It checks the clean source Git commit,
BusyBox hash and Ubuntu cross-compiler packages, then archives only tracked
source into the new output. It never cleans or modifies the host kernel tree.
Compilation uses two jobs and fixed build metadata. Full source and license
notices are retained beside artifacts. Expanded guest source trees now live in
verified `src-working-tree.tar.gz` archives after [cache retention](build-cache-retention.md);
restore a tree before inspecting its expanded `src/` directory.

Independent review caught a missing `BINFMT_SCRIPT` setting, needed by `/init`;
the fragment and required-symbol checks now enforce it. A subsequent compile
found the vendor's default Broadcom Wi-Fi driver pulled into the tiny profile.
Wireless is explicitly disabled for this virtual Ethernet fixture; the physical
host configuration is unchanged. The final clean build passed.

[Manifest](evidence/guest-fixture-20261104.json):

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `Image` | 5,251,080 | `1294959bcaa5a5ad3791913e33cffb8f0893708396aac8be9837c03a0e1f9377` |
| `initramfs.cpio.gz` | 973,641 | `62a07e1a5a7c38c5677a4c3750fc479c2636fe9e611aed67c89d3b9623a2c725` |
| `guest-disk.raw` | 134,217,728 | `0f78f07743e41fec6655b44792284980536f5cd62d08fdcaea0025bb222c6278` |

Builder output: `/home/drew/orange-pi-4-pro-build/recipe/out/guest-fixture-20261104-03/`.
Build/resource logs: builder `guest-fixture-build-03.log` and
`guest-fixture-resources-03.log` under `/home/drew/orange-pi-4-pro-build/`.
All artifact hashes, seed ID, zero counter and `e2fsck -fn` passed. Packaging and
input guards were tested locally; no emulator or physical guest run was used
for these results. Cross-builder binary comparison is pending.

## Deterministic seed preparation

`build/guest-seed.py` replaces `mke2fs -d`, which copied builder UID/GID and
ctime into the earlier disks. The fixed 128 MiB image controls its ext4 features,
geometry, UUID, hash seed, owners, modes and timestamps. It requires pinned
Jammy e2fsprogs/libext2fs2 `1.46.5-2ubuntu1.2`, trusted fixed-storage output
parents and a new regular file. No device or arbitrary seed-tree input is
accepted. Readback checks content, inode metadata, raw superblock fields and
`e2fsck -fn`; helper and direct imports are hashed with future fixture artifacts.

On the actual builder, two unmodified CLI builds as root and drew, with different
umask, working directory and timezone, produced identical 134,217,728-byte files:
`ff595fac0b9a7d990f099edc0dd0f9d4859fb9942e185d2f2377088ce32bd577`.
[Evidence](evidence/guest-seed-20261104.json) records the scope. The [complete rebuild](evidence/guest-seed-fixture-20261104.json) passed in
234.28 seconds with two jobs and 545,912 KiB maximum reported child RSS. Kernel
and HTTP initramfs match the earlier network fixture; the disk matches both
standalone seed builds. Actual cpio source readback, complete recipe/import
closure, manifest pins, e2fsck, ID and counter checks pass. Download directory:
`/home/drew/orange-pi-4-pro-build/guest-fixture-seed-20261104/` on the builder.
Existing fixture artifacts and pins above are retained; use this new complete
manifest rather than combining an old disk pin with the new seed. This is
filesystem reproducibility on the pinned builder, not KVM execution or a
cross-toolchain claim.

## Earlier fixture behavior and safeguards

The guest `/init` refuses execution outside PID 1. It computes a known CPU
checksum, then checks `/dev/vda` for the exact disposable fixture UUID/label.
It mounts read-only with journal replay disabled, verifies the content marker,
then mounts writable to increment a bounded numeric counter. It synchronizes
and unmounts the disk before reporting disk success. It never formats a disk.
The seeded ext4 filesystem has no journal.

The guest then requests DHCP and pings `10.0.2.2` on QEMU's restricted user
network. Gateway ICMP behavior is still unproven; failures enter a local console
and cannot count as network acceptance. After all checks pass it requests a
clean power-off. Expected console markers include:

```text
PASS cpu sha256=5ab247be0ba27004a2adee73bc50d4a1dfcd98f16ef5fa32c024e5f1c5c29f1c
PASS disk previous=0 counter=1
PASS network gateway=10.0.2.2
PASS fixture counter=1; clean shutdown requested
```

## Launch preparation and hardware evidence

Keep the source fixture immutable. Copy its disposable disk to an explicitly
chosen writable test location on the guest NVMe filesystem before actual
validation; the first launch uses the seed hash above. Capture `findmnt -T` for that location to prove
where storage resides. Never put guest state on SD or pass a physical disk.

`scripts/launch-kvm-guest.py` requires kernel, initramfs and disk checkpoint
hashes, an explicit measured memory budget and CPU count. It enforces a non-root
process, KVM access, `-machine virt,gic-version=host -accel kvm -cpu host`, and no
accelerator fallback. It exposes no monitor or forwarded listener by default;
the new explicit [loopback-only network profile](guest-networking.md) is optional.
The launcher retains authenticated descriptors through exec, locks the disk
for cooperating launchers, and checks NVMe placement before payload reads.
Kernel/initramfs must remain protected against concurrent in-place writers.
The [guest runtime contract](guest-operations.md) describes the fixed NVMe mount,
unprivileged account and operational work still required.
See [QEMU's virt machine reference](https://gitlab.com/qemu-project/qemu/-/blob/v7.2.0/docs/system/arm/virt.rst).

The following dry-run passed on the builder with actual artifact files. Here
`FIXTURE` names that output directory; 256 MiB is a dry-run value, not an
unmeasured board allocation recommendation:

```sh
FIXTURE=/home/drew/orange-pi-4-pro-build/recipe/out/guest-fixture-20261104-03
python3 scripts/launch-kvm-guest.py \
  --kernel "$FIXTURE/Image" --kernel-sha256 1294959bcaa5a5ad3791913e33cffb8f0893708396aac8be9837c03a0e1f9377 \
  --initrd "$FIXTURE/initramfs.cpio.gz" --initrd-sha256 62a07e1a5a7c38c5677a4c3750fc479c2636fe9e611aed67c89d3b9623a2c725 \
  --disk "$FIXTURE/guest-disk.raw" --disk-sha256 0f78f07743e41fec6655b44792284980536f5cd62d08fdcaea0025bb222c6278 \
  --memory-mib 256 --cpus 1 --dry-run
```

Actual launch through this installed-host tool awaits measured RAM, an
unprivileged account/KVM permissions, the managed read-only root and activated
NVMe guest storage. Record the exact actual
QEMU command, host identity, KVM device/vCPU evidence where available, full guest
console and QEMU exit status. Console markers alone cannot prove acceleration
or clean host-side exit. On a second launch, verify the reviewed disk checkpoint
hash after clean shutdown and require a persisted counter increment. NVMe storage/durability and the full
[release acceptance](hardware-validation.md) remain separate mandatory gates.
