# Installer preparation environment

The [EL2 diagnostic](el2-diagnostic.md) has physically initialized KVM. The
[next preparation image](#next-physical-boot) includes the module-helper fix and
retains the proven EL2 boot package. One live RAM-session DHCP lease is recorded;
cold-boot network reliability and actual KVM guest execution remain open.


The A733 preparation image now provides Python, storage tools and an HTTPS
client before NVMe is mounted. It fits the 2 GB SD budget and starts a physical
root console with a read-only NVMe planner and an explicit, guarded full-disk
writer. The [verified payload acquisition tool](nvme-distribution.md) and complete
[maintenance/recovery package](maintenance-tools.md) are included.
The [writer procedure](nvme-installation.md) requires a real installation
candidate and exact-target authorization. This image has not installed an NVMe
or booted on the board. The
[diagnostic candidate](diagnostic-image.md) remains the first hardware baseline.

## Current artifacts and behavior

Built on the prepared Ubuntu 22.04 builder from authenticated interim userspace,
pinned vendor kernel/modules, DTB and firmware. No live hardware inventory was
needed to produce it. Actual SD capacity and NVMe identity are recorded; revalidate before writing.

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `initramfs.cpio.gz` | 26,909,515 | `b1e2f9c19c97224fe5d1eff612cbd271cba34eef6eea9d4ad060a3463c66a98d` |
| `a733-installer-prep.img` | 268,435,456 | `0a44681cb8ab19060f8f620999812067fa2460582046a7808154d9b9afafa329` |
| `a733-installer-prep.img.gz` | 39,204,084 | `e60e318e83217906e347120c4169f61c77e755b9caeea0f1092fc7f54060c461` |

The compressed initramfs plus its 64-byte wrapper fits the existing 64 MiB
load-region bound. Expanded archive size is 79,298,728 bytes; board RAM and boot
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

From a clean checkout at `d5c71f5` on the prepared builder, using its authenticated
cached runtime and diagnostic inputs:

```sh
sudo -i
cd /var/lib/a733-maintenance-work-20261104
/usr/bin/python3.10 -I -B build/installer-runtime.py \
  /home/drew/orange-pi-4-pro-build/recipe/out/host-userspace-bookworm-20261001-01/rootfs.tar.gz \
  "$PWD/out/installer-runtime-maintenance-20261104-01"
/usr/bin/python3.10 -I -B build/installer-initramfs.py \
  /var/lib/a733-maintenance-work-20261104/out/installer-runtime-maintenance-20261104-01 \
  /home/drew/orange-pi-4-pro-build/artifacts \
  /home/drew/orange-pi-4-pro-build/busybox/bin/busybox \
  "$(pwd -P)/out/installer-initramfs-maintenance-20261104-01"
/usr/bin/python3.10 -I -B tests/verify_installer_runtime_arm.py \
  "$(pwd -P)/out/installer-initramfs-maintenance-20261104-01"
/usr/bin/python3.10 -I -B tests/verify_acquisition_https_arm.py \
  "$(pwd -P)/out/installer-initramfs-maintenance-20261104-01"
/usr/bin/python3.10 -I -B build/assemble-installer.py \
  /home/drew/orange-pi-4-pro-build/artifacts \
  "$(pwd -P)/out/installer-initramfs-maintenance-20261104-01" \
  "$(pwd -P)/out/installer-sd-maintenance-20261104-01"
/usr/bin/python3.10 -I -B tests/verify_installer_sd.py \
  "$(pwd -P)/out/installer-sd-maintenance-20261104-01" \
  "$(pwd -P)/out/installer-initramfs-maintenance-20261104-01"
```

All commands passed. A [clean-checkout repeat](evidence/maintenance-rebuild-20261104.json)
reproduced the tools archive, initramfs, raw SD and gzip bytes exactly, and
repeated SD inspection. ARM runtime and TLS probes ran on the first artifact. This repeats preparation recipes using cached authenticated
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
The new package preserves the original four
`/usr/lib/a733-installer` tool paths and adds all seven maintenance entrypoints
under `/usr/lib/a733-tools/scripts`. Both copies use one captured source snapshot.
Evidence:
[initramfs](evidence/maintenance-installer-initramfs-20261104.json),
[ARM probes](evidence/maintenance-installer-arm-20261104.json),
[SD manifest](evidence/maintenance-installer-sd-20261104.json), and
[independent SD inspection](evidence/maintenance-installer-sd-verify-20261104.json).
The full runtime file BOM is embedded at
`/usr/share/a733-installer/runtime-manifest.json` and retained on the builder.

Actual ARM64 binfmt/chroot checks passed for isolated Python imports, CA loading
(142 certificates), localhost NSS, tool versions, planner help, ash syntax and
regular-file mkfs/fsck/blkid/debugfs. The DHCP resolver test replaces exactly
three IP commands with logged fixture stubs. No real Ethernet configuration,
external DNS/TLS connection, kernel boot or KVM guest is established. A separate
[packaged ARM HTTPS probe](evidence/maintenance-installer-https-arm-20261104.json)
passed verified private-CA transport, untrusted-CA and redirect refusal, and
encoded/expanded stream checks against a temporary loopback server. It does not
exercise full acquisition/storage policy on ARM or prove board networking. Chroot
checks execute a trusted packaged artifact; they are not a sandbox for untrusted
programs or proof of every possible dynamic-data load.

## Download and hardware handoff

The compressed image and `installer-manifest.json` are readable on the builder under
`/home/drew/orange-pi-4-pro-build/maintenance-artifacts-20261104/`; a checked local
copy is under ignored `out/maintenance-20261104/`.

```sh
scp drew@build.drewnet.online:/home/drew/orange-pi-4-pro-build/maintenance-artifacts-20261104/a733-installer-prep.img.gz .
printf '%s  %s\n' e60e318e83217906e347120c4169f61c77e755b9caeea0f1092fc7f54060c461 a733-installer-prep.img.gz | sha256sum -c -
gzip -dk a733-installer-prep.img.gz
printf '%s  %s\n' 0a44681cb8ab19060f8f620999812067fa2460582046a7808154d9b9afafa329 a733-installer-prep.img | sha256sum -c -
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

## Next physical boot

Use this current candidate rather than the historical baseline table above.
[Build evidence](evidence/installer-helper-20261105.json) records the pinned EL2
boot package, corrected `/sbin/modprobe`, ARM runtime probe and independent SD
inspection. It is a preparation RAM shell, with no automatic disk installation.
This exact image has not booted on the board.

On `codex.drewnet.online`, the current image is already staged and hash-verified at
`/home/drew/orange-pi-4-pro/out/installer-helper-20261105/a733-installer-prep.img`.
The similarly named `out/installer-20261104/a733-installer-prep.img` is an older
artifact and does not match the current hash. Keep the versioned paths distinct.

On another flashing computer, download and authenticate:

```sh
mkdir a733-installer-helper-test
cd a733-installer-helper-test
scp drew@build.drewnet.online:/home/drew/orange-pi-4-pro-build/installer-helper-20261105/a733-installer-prep.img.gz .
printf '%s  %s\n' bed50bf2aee979f80cc2e273fd16e8fad9e130ba56e71296aa8b8d898452adc6 a733-installer-prep.img.gz | sha256sum -c -
gzip -dk a733-installer-prep.img.gz
printf '%s  %s\n' 0144c6dc8eefd285496923e0b3cf538854a6e444565ef32c4ced7df266009d9b a733-installer-prep.img | sha256sum -c -
```

Gzip size is 39,252,785 bytes; raw size is 268,435,456 bytes. Follow the
[identified-SD flashing procedure](el2-diagnostic.md#reflash-the-identified-diagnostic-sd)
using **`a733-installer-prep.img`** and raw hash
**`0144c6dc8eefd285496923e0b3cf538854a6e444565ef32c4ced7df266009d9b`**
throughout its pre-write and readback checks. Use the same identified, authorized
SU02G card; keep the previous diagnostic image for recovery. Do not write NVMe
or SPI. Retain full-card hashes before and after the boot.

Leave Ethernet connected to the same switch port throughout power-off and boot.
Record serial from before power-on: existing 3.3 V TTL wiring, 115200 baud, 8N1,
no flow control and adapter VCC disconnected. Preserve automatic PHY/DHCP output
before making manual changes. At the shell, enter these **one line at a time**:

```sh
uname -a
cat /sys/devices/system/cpu/online
ls -l /dev/kvm /sbin/modprobe
cat /sys/class/net/eth0/phydev/phy_id
readlink /sys/class/net/eth0/phydev/driver
ip addr show eth0
ip route
cat /etc/resolv.conf
cat /proc/net/dev
cat /sys/class/block/mmcblk1/ro /sys/class/block/mmcblk1/stat
/usr/bin/python3 -I -B /usr/lib/a733-installer/plan-nvme.py --target /dev/nvme0n1
```

The final command is a read-only report, not installation. If no lease arrived,
record the failure, then run this bounded retry with the cable left connected:

```sh
udhcpc -f -n -q -t 5 -T 3 -i eth0 -s /etc/udhcpc.script
ip addr show eth0
ip route
cat /etc/resolv.conf
cat /proc/net/dev
```

If a default route is shown, ping its actual gateway address using
`ping -c 3 ACTUAL_GATEWAY`. Check `date -u` before TLS. If the clock is wrong,
set it in RAM with `date -u -s 'YYYY-MM-DD HH:MM:SS'`, substituting current UTC.
Then test DNS and verified HTTPS without downloading an installation payload:

```sh
/usr/bin/curl --head --fail --connect-timeout 10 --max-time 30 https://deb.debian.org/debian/
```

Retain errors and do not bypass certificate verification. Return the full boot
and command capture, image readback/full-card hashes, and note whether any
cable movement or manual driver repair was required. A successful boot here
advances preparation; it does not establish installed-host or KVM guest acceptance.
