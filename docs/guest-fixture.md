# Pinned KVM guest fixture

The guest artifacts have been built and checked as files. **No guest has booted**
and no hardware KVM result is established. The physical first action remains
the [diagnostic SD boot](diagnostic-image.md), after exact-card authorization.

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

The entrypoint passed end-to-end. Choose a new output name for another build;
existing destinations are refused. It checks the clean source Git commit,
BusyBox hash and Ubuntu cross-compiler packages, then archives only tracked
source into the new output. It never cleans or modifies the host kernel tree.
Compilation uses two jobs and fixed build metadata. Full source and license
notices are retained beside artifacts.

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

## Guest behavior and safeguards

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
chosen writable test location on NVMe before actual validation; the first
launch uses the seed hash above. Capture `findmnt -T` for that location to prove
where storage resides. Never put guest state on SD or pass a physical disk.

`scripts/launch-kvm-guest.py` requires kernel, initramfs and disk checkpoint
hashes, an explicit measured memory budget and CPU count. It enforces a non-root
process, KVM access, `-machine virt,gic-version=host -accel kvm -cpu host`, and no
accelerator fallback. It exposes no monitor or forwarded network listener.
Protected input files must remain unchanged between verification and launch.
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

Actual board launch awaits measured RAM, an unprivileged account/KVM permissions,
and installed or temporary authenticated userspace. Record the exact actual
QEMU command, host identity, KVM device/vCPU evidence where available, full guest
console and QEMU exit status. Console markers alone cannot prove acceleration
or clean host-side exit. On a second launch, verify the reviewed disk checkpoint
hash after clean shutdown and require a persisted counter increment. A RAM-backed
bring-up test must be labeled as such; NVMe storage/durability and the full
[release acceptance](hardware-validation.md) remain separate mandatory gates.
