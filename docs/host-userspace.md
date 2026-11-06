# Interim ARM64 userspace prototype

A minimal Debian userspace prototype built successfully on the prepared Jammy
builder on 2026-11-04. It contains QEMU, OpenSSH, Python, systemd/network tools
and NVMe/filesystem tools. This document describes the base userspace archive.
[Integrated roots](current-host-roots.md), mutable state, management and installation
are built separately; follow the [current deployment guide](deploy-personalized-20261106.md)
for the prepared physical installation. Installed-host boot and actual KVM guest
execution remain unproven. The release base still needs the plan's Buildroot/interim
comparison after a working hardware baseline.

## Source and package locks

The recipe uses the Debian Bookworm snapshot at
[`20261001T000000Z`](https://snapshot.debian.org/archive/debian/20261001T000000Z/),
with an expected InRelease SHA-256 checked before signature verification. The
builder's old Debian keyring lacked current archive keys. The recipe instead
downloads and hash-checks the official
[`debian-archive-keyring_2023.3+deb12u2_all.deb`](https://deb.debian.org/debian/pool/main/d/debian-archive-keyring/debian-archive-keyring_2023.3+deb12u2_all.deb),
extracts it inside the workspace, and uses it for `gpgv`, debootstrap and APT.
It does not install Debian packages into Ubuntu. Primary key fingerprints were
checked against the [Debian archive key records](https://ftp-master.debian.org/keys.html).
All three InRelease signatures verified successfully.

Historical snapshot expiration checks are disabled for APT; signatures and package
hash checks remain enabled. `--force-check-gpg` is used for debootstrap. The
prototype includes only the base snapshot; a pinned security archive and release
maintenance policy remain integration work.

[`host-userspace-packages.txt`](../configs/host-userspace-packages.txt) is the
requested package set. The build resolved 220 packages, all retained in its
external `.deb` cache. Every installed package/version has a matching archive,
and all archive hashes passed a separate post-build audit. Future builds compare
their results with the [version lock](../configs/host-userspace-packages.lock.tsv)
and [archive hash lock](../configs/host-userspace-package-hashes.lock.tsv).
Those additional comparison checks passed against the actual first build.

## Build and checks

On the Ubuntu builder, from the repository root:

```sh
mkdir -p out
sudo bash build/host-userspace.sh "$(pwd -P)/out/host-userspace-bookworm-20261001-01"
```

Use a new `out/host-userspace-NAME` direct child on each run. Existing paths,
symlinks, traversal and unrelated destinations are refused. The entire build
executes in a private mount namespace, including both debootstrap stages.
Only its own mounts are cleaned up. `policy-rc.d` prevents package service starts;
systemd presets disable services pending production configuration.

Independent review found and fixed a debootstrap mount-lifecycle interaction:
second stage manages proc/devpts itself, so recipe-owned instances are mounted
after it exits. Cleanup retains failed entries for retry. Tests use mocks; the
actual build then completed cleanup and tar creation successfully. No host mounts
or proc/sys contents appeared in the resulting archive. The Ubuntu package
inventory still matches the original builder lock.

```sh
bash tests/test-host-userspace.sh
bash -n build/host-userspace.sh tests/test-host-userspace.sh
```

First build: 11 minutes 5 seconds, approximately 531 MiB unpacked; GNU time
reported 119,528 KiB maximum process RSS. Resource samples retained about 7 GiB
available RAM and about 41 GiB free disk. These are builder measurements, not
board idle RAM or aggregate peak-memory acceptance results.

## Artifact and actual validation

[Manifest](evidence/host-userspace-20261104.json):

- `rootfs.tar.gz`: **169,852,145 bytes**.
- SHA-256: `1967e76c0c831c1c0bab432e7116457d4372c15b413090c054f8958b72dcb392`.
- Builder location: `/home/drew/orange-pi-4-pro-build/recipe/out/host-userspace-bookworm-20261001-01/`.
- Logs: builder `/home/drew/orange-pi-4-pro-build/host-userspace-build.log` and
  `host-userspace-resources.log`.

ARM64 binfmt/chroot checks ran the real installed binaries: QEMU 7.2.22 (Debian
`1:7.2+dfsg-7+deb12u18+b3`) and OpenSSH 9.2/OpenSSL 3.0.20. QEMU lists compiled
KVM support and its `virt` machine accepts the documented `gic-version=host`
property. These checks execute userspace through builder emulation; **no KVM
guest or board test occurred**.

Generated SSH host keys, machine ID and random seeds were removed; root is locked.
The tar archive was checked for those secret/state files and mounted proc/sys
content. Runtime unique keys, management authorization/network policy and machine
identity must be provisioned before enabling services. Component copyright files
remain in the filesystem. The [source mapping and archive lock](userspace-sources.md)
now account for all 220 packages and their 149 source versions. All 479 selected
source archives have been acquired and independently rehashed; the
corresponding-source/license audit remains a separate release task.

The tarball is not a bootable image. Separate integrated recipes provide NVMe state
bindings, immutable roots, guest supervision, updates/rollback and installation;
their host checks do not establish physical deployment acceptance.
The payload must be streamed from network/USB during provisioning rather than
staged on SD. Timestamps and package-generated data have not been compared across
clean builders; no byte-reproducibility claim is made.
