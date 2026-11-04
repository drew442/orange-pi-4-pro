# NVMe boot-device selection preparation

The storage resolver and its authenticated ARM64 probing runtime are implemented
and tested. They do **not** mount a root or state filesystem, start the host,
install storage or change the diagnostic SD image. Physical A733 boot, NVMe
identity and zero SD writes remain untested.

## Selection contract

`configs/host-storage-resolver.sh ROOT_UUID STATE_UUID` expects trusted devtmpfs,
sysfs and procfs, `/bin/busybox`, and util-linux `/sbin/blkid`. It selects distinct
ext4 partitions with the two canonical lowercase filesystem UUIDs on one NVMe
namespace. It checks device-node versus sysfs identity, partition parentage,
duplicate UUIDs, current-namespace mounts, holders, active swap and state/namespace
read-only flags. Root may be block-read-only. Output contains only
`ROOT_DEVICE=/dev/nvme...` and `STATE_DEVICE=/dev/nvme...`; callers must parse
these records without `eval`.

The plan permits filesystem UUIDs. Current root payloads have distinct fixed A/B
UUIDs; cloned NVMe installations can therefore be ambiguous and are rejected.
The future installer must assign unique GPT identifiers and a unique state UUID.
The current resolver does not select by GPT PARTUUID, model or serial and is not
an installer target-authorization mechanism. It never selects an SD partition.

Every discovered NVMe partition must return a successful probe with UUID/type.
Blank, unrecognized or unreadable partitions prevent selection; unrelated
recognized formats, such as a boot VFAT filesystem, may be present. This is a
conservative preparation contract requiring refinement before a general installer.
All active swap is rejected. These observations do not lock devices or detect raw
device users or mounts in other namespaces; use before storage services start.

Probing uses explicit devices and
[`blkid --probe`](https://github.com/util-linux/util-linux/blob/v2.38.1/misc-utils/blkid.8.adoc),
which bypasses the cache and reports ambiguous signatures through its exit status.
All nonzero statuses stop selection. Export fields are parsed as data with
duplicate-field rejection and no assumed order. No formatting, repair, mount,
boot-selection write or fallback to another storage class occurs.

## Runtime packaging and verified commands

The diagnostic BusyBox has no `blkid` or `findfs` applet. The new
`build/host-storage-tools.py` extracts only four pinned ELF files and three
copyright notices from the hash-verified userspace archive. It copies resolved
library bytes to the required runtime paths, without extracting archive links,
running package code or requiring root. The output must be new and below `out/`.
The [file/package lock](../configs/host-storage-tools.lock.json) records the exact
binary bytes, util-linux/libblkid 2.38.1 and glibc 2.36 package versions.

On the prepared Ubuntu 22.04 builder, from its repository checkout:

```sh
python3 build/host-storage-tools.py \
  /home/drew/orange-pi-4-pro-build/recipe/out/host-userspace-bookworm-20261001-01/rootfs.tar.gz \
  "$(pwd -P)/out/host-storage-tools-20261104-01"
sudo python3 tests/verify_storage_tools_runtime.py \
  out/host-storage-tools-20261104-01 \
  /home/drew/orange-pi-4-pro-build/busybox/bin/busybox
```

Both commands passed. The second is an opt-in regular-file test using ARM64
binfmt/chroot on the x86 builder, with no mounts or physical storage targets.
It copies hash-verified runtime files to a new private temporary root, creates
an ext4 file and a blank file, checks probe results and confirms unchanged file
hashes. It also runs all 39 resolver fixtures with the exact diagnostic ARM64
BusyBox 1.30.1, including its `stat` and hexadecimal `printf` behavior.
The [packaging manifest](evidence/host-storage-tools-20261104.json) and
[runtime report](evidence/host-storage-runtime-20261104.json) retain the evidence.
The packaged ARM64 runtime also identified both existing 4 GiB A/B payload files.
None of these checks executes a guest or satisfies hardware acceptance.

Unprivileged regressions:

```sh
python3 -m unittest discover -s tests -p 'test_host_storage_tools.py'
bash tests/test-host-storage-resolver.sh
```

Next integration work must mount the selected root `ro,noload`, validate its
payload marker and compatible boot stack, validate provisioned state before any
write, provide bounded tmpfs and persistent identity/management configuration,
and establish failure/recovery behavior. The existing unconfigured A/B roots and
diagnostic initramfs have not been changed by this preparation.
