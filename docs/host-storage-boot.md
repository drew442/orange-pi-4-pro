# NVMe boot-device selection preparation

The resolver chooses state first, then the root recorded in the validated NVMe
selector. It does not mount filesystems, install storage or change the
diagnostic SD image. Physical A733 boot, NVMe identity and zero SD writes remain
untested. The [handoff document](host-handoff.md) covers validation order and
the current evidence.

## GPT and resolver contract

Each selected namespace has fixed roles: p1 boot, p2 root A, p3 root B, p4
state and p5 guest storage. The resolver CLI is:

```text
state STATE_UUID -> STATE_DEVICE
root STATE_DEVICE STATE_UUID SLOT ROOT_UUID -> ROOT_DEVICE
```

Only eligible NVMe p4 partitions are scanned for state. Duplicate state UUIDs
or an unrecognized eligible p4 fail closed. For root selection it validates
the selected GPT namespace and same-parent role. It does not inspect the
unselected root filesystem, allowing manual rollback when that unused slot is
corrupt. Output is parsed as data, never with `eval`. The resolver identifies
NVMe namespaces and partitions using block-node and sysfs identity, parentage,
GPT, and fresh blkid probes. It never selects SD. Observations do not lock
devices, so use before storage services start.

The immutable initramfs config contains only `STATE_UUID` and
`KERNEL_RELEASE=6.6.98-a733-diag`. State's `boot/selected.conf` is schema 1 and
contains `SLOT`, `ROOT_UUID`, `ROOT_MARKER_SHA256`, and `KERNEL_RELEASE` in
addition to `SCHEMA`. It is data-only, bounded, and validated against the
selected root marker and expected kernel. No root choice or selector write is
embedded in routine boot.

## Runtime and evidence

The diagnostic BusyBox has no `blkid` applet. The package recipe extracts four
hash-pinned ARM64 ELF files and three notices from the authenticated userspace
archive. It uses util-linux/libblkid 2.38.1 and glibc 2.36. See the
[runtime lock](../configs/host-storage-tools.lock.json) and
[runtime evidence](evidence/nvme-selection-blkid-20261104.json). ARM64
binfmt/chroot probing passed for ext4, blank files, and GPT on a 128 MiB regular
file; fixture hashes were unchanged. No block devices or physical targets
were used.

Resolver fixture coverage is 41 cases in Bash and exact pinned ARM64 BusyBox
ash. This is host evidence, not physical boot or KVM acceptance. A full
host boot/installer image, updater and target activation remain open.
Preserve the compatible firmware/kernel/DTB stack and prefer SPI-to-NVMe boot.
If SD boot remains necessary, its boot files stay read-only with zero routine
writes; root-only selection changes do not require SD repacking, while kernel,
DTB or initramfs changes require explicit boot maintenance.
