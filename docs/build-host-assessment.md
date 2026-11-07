# Build host assessment

## Active remote builder

Prepared 2026-11-04: `drew@build.drewnet.online`, Ubuntu 22.04.5 LTS x86_64. SSH and passwordless sudo verified. Four CPUs, 7.8 GiB RAM, 4 GiB swap, approximately 49 GiB available after package installation. Workspace: `/home/drew/orange-pi-4-pro-build`. Use this host for vendor builds; the local Noble assessment below is retained only to explain why local builds are unsuitable.

Installed and verified all 86 packages in [`configs/build-host-packages.txt`](../configs/build-host-packages.txt): pinned vendor base list, amd64 additions, Jammy Python packages and initial bootstrap utilities. Use `NO_APT_CACHER=yes`; the optional caching service was excluded. These are package names, not a version lock. Component-specific build dependencies may still be discovered.

Validation passed: `dpkg --audit` (no findings), `apt-get check`, all manifest packages installed, `qemu-aarch64` binfmt enabled, `en_US.utf8` available, and an actual file-backed ext4 loop mount plus static ARM64 chroot execution. `build/preflight.sh` reproduces the mount/chroot checks without accepting a physical target. Vendor GitHub access and pinned source fetch/hash checks succeeded. Start with two compilation jobs due to the smaller RAM budget; the diagnostic recipe samples memory and disk every 30 seconds.

Added Jammy `gcc-aarch64-linux-gnu` and `gcc-arm-linux-gnueabi`; observed GCC 11.4.0. The former builds the kernel. GCC 11 failed the old U-Boot's conflicting function attributes under `-Werror`; the diagnostic recipe instead uses the vendor-listed Linaro 7.4.1 toolchain, hash pinned in `configs/diagnostic-sources.lock.json`. Its original Linaro URL returned HTML and the attempted Arm URL failed with an HTTP error; a hash-checked BFSU Armbian mirror download succeeded. No warning suppression patch was applied. The exact prepared package inventory is `configs/builder-packages.lock.tsv`; it is not yet an immutable clean-builder image or APT snapshot.

Host regressions for initramfs, SD image assembly and NVMe planning pass on this Jammy host. These are generated-file/fixture tests, not physical-device tests. Build and hardware milestone evidence remains in [implementation status](implementation-status.md).

Commands used from this repository:

```sh
scp configs/build-host-packages.txt drew@build.drewnet.online:/tmp/orangepi-build-packages.txt
ssh drew@build.drewnet.online
sudo apt-get update
sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends $(cat /tmp/orangepi-build-packages.txt)
sudo dpkg --audit
sudo apt-get check
sudo update-binfmts --display qemu-aarch64
sudo losetup -f
```

Package logs remain on the builder at `/tmp/orangepi-apt-update.log` and `/tmp/orangepi-apt-install.log`; temporary logs are not release evidence archives. No disk resizing or OS replacement was performed during initial preparation;
the later authorized LV expansion is recorded below.

## Current cache capacity

Latest [production-repeat retention](repeat-build-retention.md) preserves eleven
historical expanded leaves as complete, independently verified archives on build
and codex. Retirement recovers about **5.17 GB**, leaving **24,798,740,480 bytes**
free before the fresh root repeat. Seven native SD files also retain identical
bytes while recovering **1.54 GB** through sparse allocation; codex still keeps
its separate 256 MiB reserve. The new regular-file SD recipe produces identical
raw/gzip bytes with only 36,704,256 allocated bytes per new raw copy. Recheck
resources before subsequent staging; full-NVMe guards remain unchanged, and no
further LV expansion is authorized. Earlier capacity samples below are historical.

The [operator-approved 20 GiB root-LV growth](evidence/build-host-capacity-execution-20261107.json)
now completes successfully, including online ext4 enlargement. The LV is
88,017,469,440 bytes; free space immediately after growth was **24.28 GB**, above
the unchanged 22 GiB staging guard by about 663 MB. The subsequent
[production roots](production-host-roots.md) and [state](production-host-state.md)
pass complete readback, leaving 22.53 GB before retention. Their work trees
allocate 1.23 GB and 104 MB respectively. [Verified workspace retention](composition-retention.md)
then archives three older terminal work trees on build and codex and retires
only their generated `work` subtrees. It recovers **2.48 GB**, leaving
**24,148,152,320 bytes** before the matching bootstrap. Its completed
[construction and byte-preserving sparsification](production-host-bootstrap.md)
left **23,830,810,624 bytes**, admitting the subsequent
[full NVMe composition](production-nvme-composition.md). It completed with
20,710,158,336 bytes minimum free and 7,413,485,568 bytes minimum available RAM.
Final NVMe free was **22,245,568,512 bytes**. The subsequent
[NVMe-first firmware transport SD](nvme-firmware-test-sd.md) passes its separate
4 GiB recipe budget plus staging and leaves **21,618,569,216 bytes**. The
[version-only FEL probe](rom-recovery-investigation.md) subsequently passes
frozen compilation under its own 4 GiB recipe budget and leaves
**21,557,407,744 bytes**, with unchanged host inventory and clean exit. This remains
below the next full-NVMe initial 22 GiB guard before staging overhead. Verified
retention is needed for that construction; keep every guard. No additional LV growth is authorized.
The LV UUID, root source and filesystem type are unchanged. No Orange Pi storage
was touched. Codex has a separate 256 MiB free-space minimum for retention copies.
[Native capacity recovery and candidate retention](nvme-candidate-retention.md)
now preserve three generated image hashes while reclaiming 710 MB, then retain
the 545.6 MB gzip with 565 MB free at copy completion. This does not increase
builder capacity.


The latest [candidate build](evidence/uboot-nvme-candidate-20261107.json) used
RAM for its temporary workspace and retained a verified full archive plus review
artifacts on disk. It passes with a 4.05 GB host disk minimum and 6.08 GB memory
minimum. A [registered replay](evidence/uboot-nvme-replay-20261107.json) subsequently
retains changed files and verified original references without a second full
archive, matching all five required artifacts. Before the approved growth, disk free was
**4.01 GB** after a separate [16 MiB synthetic SPI assembly fixture](evidence/spi-file-assembly-20261107.json),
below production staging and another complete retained build's budget. That
fixture uses real components and matches a codex repeat; it is not a physical
backup or board firmware. Those earlier runs did not resize the LV. Before that run,
[three generated SD copies](evidence/candidate-sd-sparsification-20261107.json)
were converted to sparse allocation, recovering 663,117,824 bytes while preserving
their full hashes and logical sizes. Physical media and operator images on codex
were untouched.

Earlier November 7 evidence: [completed kernel replay retention](kernel-run-retention.md)
and a subsequent fresh userspace attempt leave about **6.84 GB** on the builder.
That attempt failed the exact archive pin because account dates changed across
days. The later [corrected fresh baseline](account-date-replay-driver.md) passed and
left about **6.04 GB** free. The subsequent
[time repeat](evidence/account-date-time-execution-20261107.json) passes and leaves
**5,249,044,480 bytes** free. Production 22 GiB staging
was capacity-blocked at that point. The subsequently approved growth above now
clears the initial staging guard.

[Verified cache retention](build-cache-retention.md) recovered working space
without changing generated image bytes. Five complete guest working trees are
retained as compared archives; all 41 sparsified images retain their manifest
pins. [Verified relocation](evidence/builder-relocation-20261106.json) of optional
archives and historical nondeployable fixtures to codex recovered a measured
26.12 GB (24.32 GiB) free, above the 22 GiB full NVMe staging guard. Exact
retained paths and hashes are recorded; later builds consume this headroom. Recheck before each build and keep the
initial two-job compilation limit. Physical acceptance is independent of these checks.

[Builder package acquisition](evidence/builder-packages-acquired-20261106.json)
now retains all 842 exact archives on the builder and codex. The latest measured
free space after compilation/acquisition fell to about 9.0 GB. Subsequent
[verified output-tree retention](evidence/build-output-retention-20261106.json)
left 25,061,138,432 bytes free, meeting the 25 GB construction planning target.
Six complete generated trees and their raw records are retained on codex.
Current deployment artifacts and original source caches remain available;
historical output paths require restoration using the retention map. Recheck
capacity as new seed/root files consume space; existing build guards remain mandatory.

The [actual clean seed isolation probe](clean-builder.md) now passes on Jammy,
with no host inventory drift or remaining mapped processes/mounts. It ended
with 24.53 GB free after seed staging and retained failed attempts. The subsequent [offline installation](evidence/builder-offline-install-20261106.json)
now configures all 842 exact versions in a fresh seed, with package audits and
unchanged host/cache postchecks. [Builder freeze and restoration](evidence/builder-frozen-20261106.json) now pass,
including complete metadata and restored 842-package/isolation checks. The freeze
left approximately 5.3 GB free. [Verified duplicate retirement](evidence/builder-scratch-retirement-20261106.json)
recovered 11.25 GB of logical files; [fresh source checkout/import](evidence/frozen-source-import-20261106.json)
then passed in the restored builder. [Two U-Boot replays](evidence/frozen-uboot-replay-20261106.json)
now match the retained binary and boot package. [Further verified retention](evidence/kernel-space-retention-20261106.json)
raised free space from about 9.38 GB to 15.48 GB before launch. The
[fresh kernel replay](evidence/frozen-kernel-replay-20261106.json) now matches
Image/DTB/configuration and all 332 regular module files. Independent review
verifies all 2,556 manifest entries and postchecks. Two-job compilation sampled
minima of 10.13 GB disk free and 6.48 GB RAM available. The old kernel cache is
retained on codex, where full restoration, archive comparison and Git object
checks pass. [Both full SD images](sd-image-reproduction.md) now reproduce
inside the frozen builder, including standalone inspections. The final formal
run stayed above 7.57 GB disk free and 7.15 GB RAM available. Subsequent
[verified userspace retention](userspace-rebuild-capacity.md) recovered about
1.12 GB. The [fresh userspace pipeline](userspace-fresh-pipeline.md) now passes
from raw pinned packages with a byte-identical normalized result; it recorded
minima of 3.76 GB free disk and 7.12 GB available RAM. Production root/state/NVMe
reconstruction remains open; recheck all guards before further staging.

## Available volume-group capacity

Read-only LVM inventory on 2026-11-06 identifies 66,542,632,960 bytes of unused
extents in `ubuntu-vg`, equal to the current root LV allocation. Current filesystem
free space was about 7.68 GB after the earlier replay builds; the latest sample
after the fresh userspace pipeline was about 3.79 GB; subsequent
[normalized profile staging](userspace-profiles.md) left about 3.29 GB. A subsequent
[small ARM identity fixture](timesync-identity.md) left 3,247,067,136 bytes
free. The subsequent [private state-view fixture](timesync-state.md) left
3,246,751,744 bytes. The [outer time-profile preparation](timesync-outer-launcher.md)
then passed the actual propagation prelude and early 5 GiB capacity refusal.
After retaining only the 22 required committed source files and diagnostic
records, free space was 3,240,124,416 bytes, above the small-probe 3 GiB reserve
but below full-build guards. The
[prepared 20 GiB growth proposal](build-host-capacity-plan.md) has an exact target,
metadata backup, successful allocation test and independently reviewed guards.
The operator subsequently approved the host root-volume change, and the
[execution evidence](evidence/build-host-capacity-execution-20261107.json) now
records successful growth. It does not involve Orange Pi storage or change its
existing NVMe erase/SPI authorization boundaries.

Subsequent [historical root retention](historical-root-retention.md) preserves
twelve old generated files in two protected codex copies. Complete hashes and
independent review pass; after retirement the builder had **6,765,948,928 bytes
free**. This permits the tracked userspace pipeline to start, while production
image assembly retains its larger capacity guards. No LVM growth occurred.

The later [time-profile component run](evidence/time-profile-integration-20261107.json)
passed in the frozen builder with minima of **4,345,155,584 bytes free disk** and
**7,260,938,240 bytes available RAM**. This staged the complete 221-package archive
and a 256 MiB nondeployable state fixture, not production images. Host inventory
remained unchanged and mapped processes/mounts were cleared. Full root/state/NVMe
capacity guards are unchanged; approved growth now clears the initial guard.
Recheck current resources before further staging.

The [November 7 installer refresh](evidence/installer-refresh-20261107.json)
restaged runtime and packaged initramfs/SD with all guards intact. Six stages,
packaged ARM checks and independent artifact review pass. Sampled minima were
3,889,745,920 bytes free disk and 7,205,289,984 bytes available RAM; all 842 host
packages were unchanged. Full production reconstruction retains its larger
capacity requirements; this refresh preceded the approved LV growth.

## Local host assessment

Checked 2026-11-04 against vendor build commit `a95129bf30ea11886cc35f08ebb3e69695a26656`. **Local host only: suitable for source investigation, not an unmodified vendor image build.** No local dependencies were installed; remote provisioning is described above.

## Requirements compared with this host

| Item | Vendor evidence | Observed host and conclusion |
| --- | --- | --- |
| Architecture | Manual section 4.1 specifies x64 | `uname -m`: x86_64; matches |
| OS | README and manual specify Ubuntu 22.04 | Ubuntu 24.04.4 LTS; differs from documented environment |
| CPU | No numeric Linux-build minimum found in reviewed requirements | Four available processors; start with conservative build parallelism |
| RAM | No numeric Linux-build minimum found in reviewed requirements | 16,765,571,072 bytes total, about 15.6 GiB; about 14.7 GiB available during check; 4 GiB swap |
| Disk | Build script warns below 10 GiB free | About 48 GiB available on repository filesystem; exceeds warning threshold, not a guarantee of sufficient full-build capacity |
| Packages | Base list plus amd64, Noble/Jammy Python and default cache additions | 83 package names checked; 43 installed, 40 missing |
| Builder isolation | A matching Ubuntu environment avoids host-version mismatch | Neither Docker nor Podman found on PATH; no matching isolated builder validated |
| Downloads | Vendor fetches sources and toolchains separately | GitHub vendor clone succeeded; toolchain and other artifact endpoints not yet validated |

The current script permits `noble`, but still asks for `python2`, `libpython2.7-dev`, and `python3-distutils`; none has a candidate in this host's local apt index. Passing its OS guard therefore does not establish package compatibility. The manual's 16 GB RAM / 200 GB disk recommendation belongs to **Android compilation**, not the Linux SDK, and is not used as a Linux requirement here.

The pre-clone disk measurement was 51,977,252,864 available bytes. The shallow vendor checkout consumed approximately 692 MiB afterward; free space changes with other work. Source trees, toolchains, caches, temporary root filesystems and image copies share this disk. Recheck `df -h . /tmp` before downloads and each full build. Prefer at least 80 GiB free for comfortable iterative work as a project planning allowance, not a vendor minimum. Measure actual peak use before setting a release-build requirement.

## Dependency audit

The list below is missing according to `dpkg-query -W -f='${Status}'` (required result: `install ok installed`). It covers literal base dependencies and the amd64, Python and default apt-cache branches in `scripts/general.sh`; optional extensions and future `EXTRA_BUILD_DEPS` can add more. `apt-cacher-ng` is omitted by the vendor when `NO_APT_CACHER=yes`.

```text
apt-cacher-ng
aptly
aria2
ccache
debian-keyring
dialog
distcc
dwarves
f2fs-tools
fatattr
gcc-arm-linux-gnueabihf
gcc-riscv64-linux-gnu
git-lfs
imagemagick
lib32ncurses-dev
lib32stdc++6
libbison-dev
libc6-dev-armhf-cross
libc6-i386
libfdt-dev
libfl-dev
liblz4-tool
libncurses-dev
libpython2.7-dev
libusb-1.0-0-dev
lzop
nfs-kernel-server
ntpdate
p7zip-full
patchutils
pigz
pixz
pkg-config
pv
python2
python3-distutils
scons
systemd-container
unzip
zip
```

Package presence does not prove version compatibility. `apt-cache policy` used existing local indexes, without refreshing them. `debootstrap` and `qemu-aarch64-static` are already available. The vendor bootstrap also has package-install and host-mutation steps; do not source its scripts merely to enumerate dependencies.

## Local host alternative

Use the prepared remote Jammy host. If a local builder is later needed, provision an isolated Ubuntu 22.04 amd64 environment with a dedicated persistent workspace. Validate package resolution there before starting a long build; use a VM or a deliberately configured container with the mount/loop/binfmt facilities required by image assembly. Do not assume an ordinary unprivileged container will work. Pin the environment and record exact setup commands once exercised. Do not replace the host OS, mix Jammy repositories into Noble, or bypass dependency errors.

Then pin the board's actual kernel, bootloader, binary firmware and toolchain inputs. Produce the small headless SD bootstrap first; live inventory and board tests follow that image. No stock-image installation or larger SD card is required.

## Sources and reproduction

- [Vendor manual v1.4](https://fox-exe.ru/Files/OrangePi4Pro/OrangePi_4_Pro_A733_User%20Manual_v1.4.pdf), section 4.1, printed pages 201–203: Linux build environment; section 4.2: branch and toolchain downloads.
- [Pinned README](https://github.com/orangepi-xunlong/orangepi-build/blob/a95129bf30ea11886cc35f08ebb3e69695a26656/README.md): supported host and board list.
- [Pinned host setup](https://github.com/orangepi-xunlong/orangepi-build/blob/a95129bf30ea11886cc35f08ebb3e69695a26656/scripts/general.sh#L1431): dependencies, release guard, and free-space warning near line 1651.
- [Pinned board](https://github.com/orangepi-xunlong/orangepi-build/blob/a95129bf30ea11886cc35f08ebb3e69695a26656/external/config/boards/orangepi4pro.conf) and [family](https://github.com/orangepi-xunlong/orangepi-build/blob/a95129bf30ea11886cc35f08ebb3e69695a26656/external/config/sources/families/sun60iw2.conf): actual current/legacy selections. Current source differs from the older manual's branch mapping.

Read-only host checks: `cat /etc/os-release`, `uname -m`, `nproc`, `free -b`, `df -B1 .`, `command -v docker podman debootstrap qemu-aarch64-static`, `dpkg-query`, and `apt-cache policy`. Source inspected with a shallow `next` clone and `git rev-parse HEAD`; verify the recorded commit when reproducing. This assessment does not claim a successful build or complete network/preflight validation.
