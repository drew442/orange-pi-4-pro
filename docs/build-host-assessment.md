# Build host assessment

## Active remote builder

Prepared 2026-11-04: `drew@build.drewnet.online`, Ubuntu 22.04.5 LTS x86_64. SSH and passwordless sudo verified. Four CPUs, 7.8 GiB RAM, 4 GiB swap, approximately 49 GiB available after package installation. Workspace: `/home/drew/orange-pi-4-pro-build`. Use this host for vendor builds; the local Noble assessment below is retained only to explain why local builds are unsuitable.

Installed and verified all 86 packages in [`configs/build-host-packages.txt`](../configs/build-host-packages.txt): pinned vendor base list, amd64 additions, Jammy Python packages and initial bootstrap utilities. Use `NO_APT_CACHER=yes`; the optional caching service was excluded. These are package names, not a version lock. Component-specific build dependencies may still be discovered.

Validation passed: `dpkg --audit` (no findings), `apt-get check`, all manifest packages installed, `qemu-aarch64` binfmt enabled, `en_US.utf8` available, and `sudo losetup -f` returned a free loop device. Vendor GitHub access succeeded. No kernel/rootfs build, actual loop mount, or target ARM chroot execution has been tested yet. Start with two compilation jobs due to the smaller RAM budget and monitor peak memory and disk usage.

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

Package logs remain on the builder at `/tmp/orangepi-apt-update.log` and `/tmp/orangepi-apt-install.log`; temporary logs are not release evidence archives. No disk resizing or OS replacement was performed.

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
