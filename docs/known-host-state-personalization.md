# Preserve this board's original host identity

This narrowly scoped recovery tool restores the original identity of the **same
physical Orange Pi 4 Pro A733** after the corrected full NVMe installation and
before its first boot. It never provisions another machine with these keys.
Storage authorization is recorded in
[evidence/storage-write-authorization-20261108.json](evidence/storage-write-authorization-20261108.json).
Independent device-safety review is required before physical use. Host tests do
not establish hardware acceptance.

The immutable root slots, package-state profile, configuration, boot selector,
user state and timesync daemon ownership remain unchanged. The only intended
file replacements are the exact paths established by
[host-state.py](../build/host-state.py) and consumed by
[host-management.py](../configs/host-management.py):

| STATE path | UID:GID | Mode |
| --- | --- | --- |
| `identity/machine-id` | 0:0 | 0644 |
| `identity/ssh/ssh_host_ed25519_key` | 0:0 | 0600 |
| `identity/ssh/ssh_host_ed25519_key.pub` | 0:0 | 0644 |

Mode 0644 on machine-id is necessary for unprivileged services including
networkd. The original pinned identity archive has five members including two
0700 root-owned directories. Timestamp-only PAX fields (`mtime`, `atime`,
`ctime`) are permitted and ignored for destination metadata; path, size, link
and xattr extensions are refused. Both directory entries are required. Its exact 10,240 bytes have SHA256
`ae3930cec481912a164361e11c926ca02d81561a49c0fc49b70514dd19d3d06e`.
The original private builder copy is
`/var/lib/a733-original-host-identity-20261108-01/identity.tar`.
The public key fingerprint must be
`SHA256:Epfegy9GsJ6ng4/0EH3dZmir7beQj5uYTnIbE4JPfEI`.
Do not display machine-id or private-key contents.

The target is fixed to `/dev/nvme0n1p4`, ext4 STATE UUID
`360dfb39-f49f-487e-b307-0bb887c854c5`, on the 256,060,514,304-byte
SPCC M.2 PCIe SSD serial `134807190FC700149277`, with 512/512 sectors.
Partition 4 must start at sector 17,827,840 and be 8 GiB. There is no alternate
target argument. The existing fresh machine-id is replaced only if its bytes
match SHA256
`27e9d2bf78a55ad7cbfc2dfce5511aebb7adcd44b24b474b5123c4ce41c39e2a`,
bound by the corrected STATE producer receipt in
[evidence/production-host-state-runtime-fixes-20261108.json](evidence/production-host-state-runtime-fixes-20261108.json).
Absent/empty files and exact idempotent copies are permitted; other nonempty
files fail before any identity file changes.

Stage the already authenticated archive as root:root mode 0600 at
`/run/a733-payload/identity.tar`; its parent must be a real root:root 0700
directory on dedicated `/run` tmpfs. Use the root RAM installer's initial
mount/PID/user namespaces, with NVMe unmounted and unused. The caller must pin
the **current** `/proc/sys/kernel/random/boot_id`; the tool has no default boot
pin. Invoke with Python 3.11 or later:

```sh
python3 -B /run/a733-payload/personalize-known-host-state.py \
  --expected-boot-id CURRENT-VERIFIED-BOOT-UUID \
  --source-archive /run/a733-payload/identity.tar
```

The default plan performs no mount or physical write. It validates the entire
source archive, serial/model/geometry, block ioctls, raw p4 superblock UUID/label,
4 KiB block size and exact 8 GiB filesystem block count (including its high word),
all NVMe holders, mounts in process namespaces, swaps, open process files and
memory mappings. Before any mount, the raw superblock must have `has_journal`
(compat `0x4`), no incompatibility bits outside `0x2c6` (which excludes
`JOURNAL_DEV`, `0x8`), an all-zero journal UUID, internal journal inode 8 and
`journal_dev` zero. These checks contain journal writes to the selected STATE
filesystem rather than another device. Destination metadata is inspected only
during apply. Repeat the same invocation with `--apply` after reviewing the
plan and completing the full image installation. Apply repeats source and
target guards, mounts only
p4 as `rw,nosuid,nodev,noexec`, verifies mountinfo and device identity, checks
STATE's root-owned marker and 996:996 0755 timesync directory, then checks every
destination before mutation. It creates only absent `identity` and
`identity/ssh` directories, writes same-directory temporary files, fsyncs each
file, renames each separately and fsyncs its parent directory. It reads back
the exact three files, rechecks STATE metadata, unmounts and removes its RAM
mountpoint. It never erases, partitions, opens a root slot for writing, reboots,
or accesses SD or SPI.

This is a trusted, quiescent RAM recovery procedure. Device inspection and
mount are separate operations: no whole-disk exclusive FD is held across
mount, because a kernel claim could prevent the mount. Concurrent root device
or namespace changes are outside its protection; keep other storage tools
stopped until completion. Ext4 mount/unmount can update filesystem metadata,
in addition to the three intended file replacements.
Dirty journal recovery and legacy or file-orphan cleanup remain permitted by
this explicitly authorized writable apply procedure. These conditions do not
make a filesystem eligible for an unprotected read-only probe or export.

The 2026-11-09 journal-containment change is source and host-fixture validation
only; it has not been staged or exercised on the board. The completed physical
identity restoration in the
[2026-11-08 install receipt](evidence/corrected-host-install-20261108.json)
remains historical evidence for the source used then. Future staging must pin
the changed helper's source SHA256 separately; that receipt and existing image
hashes do not validate this version.

JSON output contains public file hashes, names and modes, change progress,
completion/durability and `hardware_acceptance: false`. No source contents are
printed. Renames are atomic individually; the three-file update is not one
transaction. A failure may leave a partial result. Failed cleanup may leave
the tool's mountpoint mounted; inspect its exact mount identity and unmount it
before further storage work. **Do not reboot or write the final SD after any
failure.** Resolve the failure and rerun; exact existing copies are idempotent.
Only exit 0 with `completed: true`, `files_fsynced: true` and `unmounted: true`
on apply establishes completion of this procedure.

Preserve the image installer's pre-personalization write/hash receipt as a
historical record. After this operation the full image's byte hash is no
longer the device's byte hash; record these three file differences separately.
Actual corrected-host SSH trust, machine-id service readability, DHCP,
timesync, zero routine SD writes and a real KVM guest still require hardware
validation.
