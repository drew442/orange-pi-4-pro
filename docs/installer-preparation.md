# Installer preparation environment

The A733 preparation image now provides Python, storage tools and an HTTPS
client before NVMe is mounted. It fits the 2 GB SD budget and starts a physical
root console with a read-only NVMe planner and an explicit, guarded full-disk
writer. The [verified payload acquisition tool](nvme-distribution.md) is included.
The [writer procedure](nvme-installation.md) requires a real installation
candidate and exact-target authorization. This image has not installed an NVMe
or booted on the board. The
[diagnostic candidate](diagnostic-image.md) remains the first hardware baseline.

## Current artifacts and behavior

Built on the prepared Ubuntu 22.04 builder from authenticated interim userspace,
pinned vendor kernel/modules, DTB and firmware. No live hardware inventory was
needed to produce it. Actual SD capacity and target identity still need checking.

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `initramfs.cpio.gz` | 26,853,830 | `de514547f1b04cbdf4ed83116ea44a4204ee7e06c66fc25ef1b549b67a1c6295` |
| `a733-installer-prep.img` | 268,435,456 | `bd33504f9a0b4c5a87239c95c71353f1d518588e6247f43ce9396172e1c85e2a` |
| `a733-installer-prep.img.gz` | 39,141,036 | `95cb997cc005854dfbfd92b8688407f45cec2a64f633102f830fe1c3303e27c6` |

The compressed initramfs plus its 64-byte wrapper fits the existing 64 MiB
load-region bound. Expanded archive size is 79,055,868 bytes; board RAM and boot
memory reservations remain hardware checks. The source runtime stages 1,461
regular files totaling 66,713,692 bytes. It includes Python 3.11, util-linux 2.38.1,
e2fsprogs 1.47.0, curl 7.88.1, their selected libraries, CA/config data and notices.
It omits an SSH server and launches no listeners.

Boot follows the same pinned legacy vendor protocol and 256 MiB disk layout as
the diagnostic image. Linux mounts proc, sysfs, devtmpfs and bounded RAM `/run`
and `/tmp`; no disk filesystem or swap is mounted. Kernel modules and wired
DHCP are attempted, with resolver changes in the RAM root. No provisioning or
SPI operation starts automatically. The unrestricted root shell is for physical
console bring-up; a locked password record is not console access control.

```mermaid
flowchart LR
  SD[SD vendor boot stack] --> K[Pinned kernel and DTB]
  K --> I[Installer preparation initramfs]
  I --> R[RAM console and selected ARM tools]
  R --> P[Read-only NVMe identity and layout report]
```

The installed host's persistent SSH management remains separate; see
[management preparation](host-management.md). This environment contains no
administrator credentials or pre-generated SSH host keys.

## Verified build and inspection

From a clean checkout at `f2428b9` on the prepared builder, using its authenticated
cached runtime and diagnostic inputs:

```sh
sudo /usr/bin/python3.10 -I -B build/installer-initramfs.py \
  /home/drew/orange-pi-4-pro-build/package-state-work-20261104/out/installer-runtime-package-state-20261104-01 \
  /home/drew/orange-pi-4-pro-build/artifacts \
  /home/drew/orange-pi-4-pro-build/busybox/bin/busybox \
  "$(pwd -P)/out/installer-initramfs-repeat"
sudo /usr/bin/python3.10 -I -B tests/verify_installer_runtime_arm.py \
  "$(pwd -P)/out/installer-initramfs-repeat"
sudo /usr/bin/python3.10 -I -B tests/verify_acquisition_https_arm.py \
  "$(pwd -P)/out/installer-initramfs-repeat"
sudo /usr/bin/python3.10 -I -B build/assemble-installer.py \
  /home/drew/orange-pi-4-pro-build/artifacts \
  "$(pwd -P)/out/installer-initramfs-repeat" \
  "$(pwd -P)/out/installer-sd-repeat"
sudo /usr/bin/python3.10 -I -B tests/verify_installer_sd.py \
  "$(pwd -P)/out/installer-sd-repeat" \
  "$(pwd -P)/out/installer-initramfs-repeat"
```

All commands passed. A [clean-checkout repeat](evidence/package-rebuild-20261104.json)
reproduced initramfs, raw SD and gzip bytes exactly, and repeated both ARM and
SD inspections. This repeats preparation recipes using cached authenticated
inputs, not a complete clean vendor build or physical deployment.

Use new output names to repeat. Outputs are private new directories below
repository `out/`; stages reject RAM, recognizable SD/removable backing devices,
symlink paths and existing outputs. Keep the checkout on known fixed builder
storage. Failed stages retain partial output without a success manifest;
retry with a new name. No package installation, compilation or network download
occurs in these commands. Root permits private chroot/device-node fixtures and
reading existing private inputs, not physical installation.

The stager authenticates a private archive capture before parsing, caps its
expanded tar, and selects files without general tar extraction. Safe source
hardlinks and symlinks become regular copies; bounded alias traversal rejects
cycles/escapes. Python's standard library and extension modules are selected
with recursive ELF dependencies, explicit dynamic-provider/NSS candidates,
configuration and CA data. Exact source archive, policy, component hashes,
builder readelf/provider packages and package locks are recorded.

The packager authenticates every runtime file/mode and current provenance,
including legitimate empty stdlib files. It emits deterministic root-owned newc
metadata with the pinned BusyBox/modules. The SD wrapper preserves the pinned
kernel/DTB/firmware, checks current preparation source records and uses the
reviewed regular-file assembler. No physical target is accepted by these recipes.

Runtime dependencies remain the same authenticated ARM libraries/tools; staging
was repeated to refresh source provenance, including `package_state.py`.
Evidence:
[initramfs](evidence/package-installer-initramfs-20261104.json),
[ARM probes](evidence/package-installer-arm-20261104.json),
[SD manifest](evidence/package-installer-sd-20261104.json), and
[independent SD inspection](evidence/package-installer-sd-verify-20261104.json).
The full runtime file BOM is embedded at
`/usr/share/a733-installer/runtime-manifest.json` and retained on the builder.

Actual ARM64 binfmt/chroot checks passed for isolated Python imports, CA loading
(142 certificates), localhost NSS, tool versions, planner help, ash syntax and
regular-file mkfs/fsck/blkid/debugfs. The DHCP resolver test replaces exactly
three IP commands with logged fixture stubs. No real Ethernet configuration,
external DNS/TLS connection, kernel boot or KVM guest is established. A separate
[packaged ARM HTTPS probe](evidence/package-installer-https-arm-20261104.json)
passed verified private-CA transport, untrusted-CA and redirect refusal, and
encoded/expanded stream checks against a temporary loopback server. It does not
exercise full acquisition/storage policy on ARM or prove board networking. Chroot
checks execute a trusted packaged artifact; they are not a sandbox for untrusted
programs or proof of every possible dynamic-data load.

## Download and hardware handoff

The compressed image and `installer-manifest.json` are readable on the builder under
`/home/drew/orange-pi-4-pro-build/package-artifacts-20261104/`; a checked local
copy is under ignored `out/package-state-20261104/`.

```sh
scp drew@build.drewnet.online:/home/drew/orange-pi-4-pro-build/package-artifacts-20261104/a733-installer-prep.img.gz .
printf '%s  %s\n' 95cb997cc005854dfbfd92b8688407f45cec2a64f633102f830fe1c3303e27c6 a733-installer-prep.img.gz | sha256sum -c -
gzip -dk a733-installer-prep.img.gz
printf '%s  %s\n' bd33504f9a0b4c5a87239c95c71353f1d518588e6247f43ce9396172e1c85e2a a733-installer-prep.img | sha256sum -c -
```

Downloading/decompressing writes ordinary files only. Follow the
[exact-target approval and serial guide](diagnostic-image.md) before flashing
physical SD. Use 3.3 V TTL serial at 115200 8N1 and retain complete logs. Return
board/SSD identities, RAM, kernel/KVM messages, Ethernet/DHCP results and mount
inventory. The preparation console's read-only planner command is:

```sh
/usr/bin/python3 -I -B /usr/lib/a733-installer/plan-nvme.py --help
```

After an actual target is identified, use its exact path/serial/capacity in the
planner; its output authorizes no erasure. The [provisioning guide](nvme-provisioning.md)
tracks physical writer, source-payload staging, locking, verification and
interruption work. SPI backup/recovery, actual guest execution, zero SD writes
and reproduced deployment remain required.

## Upstream behavior

The planner's adjacent-module import supports
[Python isolated mode](https://docs.python.org/3.11/using/cmdline.html#cmdoption-I),
which removes the script directory from normal import lookup. Bytecode writes
are disabled in preparation commands. The bundled CA data supports
[curl certificate verification](https://curl.se/docs/sslcerts.html); loading that
data in software does not establish a board TLS connection or correct clock.
Vendor boot-protocol references remain in the [diagnostic guide](diagnostic-image.md).
