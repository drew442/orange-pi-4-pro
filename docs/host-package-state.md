# Package metadata across A/B roots

The interim Debian host now keeps version-owned package metadata with each
immutable root. Software fixtures and artifact checks pass; no installed update,
real rollback, systemd boot or hardware acceptance has been demonstrated.
Both built root slots still contain the same 220 package versions.

Previously, binding shared state `/var` hid each selected root's package
database. The [authenticated archive inventory](evidence/package-metadata-inventory-20261104.json)
and [broader inventory](evidence/package-extra-metadata-inventory-20261104.json)
identified the metadata and caches that needed explicit treatment.

## Format and state classification

`a733-package-state-v1` relocates these complete components into
`/usr/lib/orangepi-hypervisor/package-state/NAME` during private builder staging:

| Name | Runtime view | Treatment |
| --- | --- | --- |
| `dpkg` | `/var/lib/dpkg` | Read-only package database, including alternatives |
| `apt` | `/var/lib/apt` | Read-only package indexes/state |
| `debconf` | `/var/cache/debconf` | Read-only built configuration metadata |
| `ucf` | `/var/lib/ucf` | Read-only built configuration registry/cache |
| `pam` | `/var/lib/pam` | Read-only PAM configuration-generation metadata |
| `shells` | `/var/lib/shells.state` | Read-only registration file |
| `systemd-helper` | `/var/lib/systemd/deb-systemd-helper-enabled` | Read-only package service-helper records |
| `systemd-catalog` | `/var/lib/systemd/catalog` | Read-only built message catalog |

The snapshot preserves contents, links, modes and ownership, leaves real empty
mountpoints, and records all 1,122 entries in a bounded descriptor. Its
189,468-byte manifest has SHA-256
`682f6ae0293cc8ef1a1e4780e30140fd557ede4de893328c8fc0dec5582dba7d`.
The root marker and trusted payload manifest bind this descriptor's size/hash.
`/etc/alternatives` stays in the selected root's lower `/etc`; the writable
upper layer is fresh RAM each boot. In-place APT/dpkg maintenance is unsupported.

Logs, coredumps, pstore, identity and host-generated runtime state remain on
writable NVMe state. Empty Python package-compilation markers and maintenance
registries are not evidence that arbitrary future package state is compatible.
New packages, state migrations and root-version combinations require a separate
compatibility audit and rollback/restore tests. The release-base choice still
follows the board baseline; this is the interim Debian contract.

Both root and fresh-state builders remove only three known regenerable files:
APT `pkgcache.bin` and `srcpkgcache.bin` (77,282,380 bytes together), plus
ldconfig `aux-cache` (12,588 bytes). The original authenticated 169,852,145-byte
userspace archive is unchanged. Runtime caches may be regenerated on NVMe;
these checks establish artifact contents, not a cache-free running system.

## Handoff and compatibility

After the writable state `/var` bind, early boot validates real, safely owned
source/target ancestors, creates eight package binds and separately remounts
each `ro,nodev,nosuid`. It checks actual mount flags and tracks each successful
bind for cleanup even if its remount fails. State itself remains read-write.
This follows the separate bind/remount behavior in [Linux mount(2)](https://man7.org/linux/man-pages/man2/mount.2.html).

Management preparation requires exact selected-root device identities and
mount-root paths, no foreign ancestor/descendant or immutable `/usr` overmounts,
matching source/bound inodes and the authenticated descriptor. It masks native
SSH aliases and package-maintenance APT, dpkg-backup and catalog-update units
in RAM. Unit masking uses the [systemd 252 unit search/mask semantics](https://raw.githubusercontent.com/systemd/systemd/v252/man/systemd.unit.xml).

Root selection, bootstrap packaging and integrated-image assembly reject an
unsupported or mismatched package-state ABI. New selectors bind regenerated
root-marker hashes. Old frozen prototype roots lack this format and cannot be
mixed with the new bootstrap; this is not a legacy migration or rollback path.
Existing state database copies remain covered and nonauthoritative; no installed
state is deleted or migrated. Runtime package queries must use the selected
views, not the covered `/run/a733-state/var` copies. Routine compatible root
updates must leave SD unchanged; kernel/bootstrap maintenance remains explicit.

## Executed checks and reproducible commands

Recipe bytes match commit `f2428b9`. On the prepared Jammy builder:

```sh
sudo /usr/bin/python3.10 -I -B tests/verify_package_state_loop.py \
  /home/drew/orange-pi-4-pro-build/busybox/bin/busybox
sudo /usr/bin/python3.10 -I -B tests/verify_package_payload_arm.py \
  /home/drew/orange-pi-4-pro-build/package-state-work-20261104/out/host-payload-package-state-20261104-01 \
  /home/drew/orange-pi-4-pro-build/busybox/bin/busybox
```

Both passed. The first uses newly created tiny files, a private mount namespace,
real production ARM BusyBox mount functions, and native queries on synthetic
version 1/2 databases. A→B→A selected the matching alternatives and database;
24 root write attempts returned `EROFS`, state logs stayed writable, covered
state databases and source images stayed unchanged, and owned loops/mounts were
explicitly removed. [Tiny VFS evidence](evidence/package-state-loop-20261104.json)
does not implement or test an installed updater.

The second independently inspected both actual 4 GiB root files, compared every
metadata entry and executed their ARM64 `dpkg-query` binaries through the default
`/var/lib/dpkg` view under binfmt/chroot. Both returned the exact 220-row package
lock. The worker uses a fixed environment so `DPKG_ADMINDIR`/`DPKG_ROOT` cannot
redirect the query; the [Bookworm dpkg-query manual](https://manpages.debian.org/bookworm/dpkg/dpkg-query.1.en.html)
documents database selection and architecture-qualified package names.
[Artifact/ARM evidence](evidence/package-payload-arm-20261104.json) records
read-only binds, writable logs, unchanged source/covered state and cleanup.
Separate [A](evidence/package-root-loop-A-20261104.json) and
[B](evidence/package-root-loop-B-20261104.json) checks used writable private loop
copies and confirmed the ext4 feature denies read-write remounts and writes.

A [clean-checkout repeat](evidence/package-rebuild-20261104.json) reproduced root
markers/package descriptors and byte-identical host/installer bootstrap files.
Raw root byte identity is not claimed because filesystem metadata changes.
Existing authenticated inputs and the prepared builder were reused.
Hardware boot, real cross-version updates, interrupted-update recovery, rollback,
backup restoration and actual KVM remain mandatory [release gates](hardware-validation.md).
