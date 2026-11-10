# Orange Pi 4 Pro hypervisor development plan

## Outcome and scope

Develop a reproducible, minimal Linux KVM host for the **Orange Pi 4 Pro A733**, with a 2 GB SD card and a nominal 250 GB NVMe drive. Guests, host state, logs, and updates belong on NVMe. Prefer SPI boot with the SD removed; otherwise use SD only for immutable boot files. Deliver an installer, host image, source manifest, operational documentation, and hardware validation results.

This is the implementation plan. A custom 256 MiB diagnostic candidate has booted to its RAM shell on the board; the revised firmware enters EL2 on all eight CPUs and KVM initializes with VHE. A real nonroot KVM guest now passes CPU, NVMe-backed disk, networking, clean shutdown, checkpoint and restart tests on the corrected VGIC host; the eight-hour workload gate passes using retained longer-run data, while final release acceptance remains open. Installed NVMe root A boots; the permission repair now restores DHCP and both authorized SSH keys. See [implementation status](implementation-status.md) and the [diagnostic guide](diagnostic-image.md). Vendor images exceed the available SD capacity. [Recorded diagnostic inventory](board-diagnostic-20261104.md) identifies 12 GiB RAM and the actual NVMe. Diagnostic outputs suffice for component identity; attached heatsinks remain in place. Record power supply and intended guest workload when available. Guest limits depend on measured available RAM; do not confuse SD capacity with memory.

## Evidence and decisions

The [vendor build repository](https://github.com/orangepi-xunlong/orangepi-build) lists A733 Orange Pi 4Pro support and Ubuntu 22.04 as its build host. Resolve the actual board configuration and pin its commit before building; do not reuse RK3399 Orange Pi 4 configuration. Use the [vendor organization](https://github.com/orangepi-xunlong) to trace the kernel and bootloader repositories referenced by that configuration.

The [vendor manual v1.4, mirrored by Fox_exe](https://fox-exe.ru/Files/OrangePi4Pro/OrangePi_4_Pro_A733_User%20Manual_v1.4.pdf), section 3.36, printed pages 196–199, documents SPIFlash plus NVMe installation using a TF-booted Linux system and `nand-sata-install`. It describes PCIe 3.0 x1 and warns of SSD compatibility limits. This supports investigating SPI boot, not guaranteeing it on the user's SSD. The stock installation sequence does not establish that its image fits a 2 GB card.

The [board schematic](https://fox-exe.ru/Files/OrangePi4Pro/OPI%204%20PRO%20V1_3_2_20260109.pdf) and [archive index](https://fox-exe.ru/Files/OrangePi4Pro/) are hardware references. Check revision and UART electrical details before attaching a console. Treat mirrored binaries as third-party artifacts: record hashes and provenance rather than assuming they match vendor source.

Choose **Linux KVM and QEMU** provisionally. A CPU specification alone does not prove usable EL2, firmware handoff, interrupt virtualization, or kernel support. The first release gate is an actual accelerated guest. Containers and software emulation do not satisfy this gate. Device passthrough is outside the initial design until IOMMU isolation is demonstrated.

## Minimal image strategy

1. Build a small headless bootstrap image using the vendor board stack to isolate boot, NVMe, and KVM issues. Stock vendor images cannot fit the available SD card and are not a bring-up prerequisite. Keep compatible firmware, device tree, and kernel together, with a minimal rootfs and serial diagnostics. Verify image byte size before asking for live inventory.
2. Build a custom root filesystem with Buildroot after the baseline passes. Use a project `BR2_EXTERNAL` tree and pinned versions; retain required vendor board support rather than presuming mainline support.
3. If Buildroot integration delays a working host, use a minimal Debian arm64 root filesystem with the proven board stack as an interim image. Measure disk use, idle RAM, maintenance burden, and boot reliability before choosing the release base. Debian userspace does not supply board boot support by itself.

Include QEMU's AArch64 system emulator, SSH, network configuration, storage tools, and a small service supervisor. Omit desktop, GPU/NPU services, development toolchains, and unnecessary daemons. Start with supervised QEMU processes; add libvirt only if management requirements justify it. Buildroot customization and licensing workflows are described in its [upstream manual](https://buildroot.org/downloads/manual/manual.html).

## Boot and storage architecture

Priority order:

1. **SPI firmware → NVMe boot files → NVMe root.** SD is removed after provisioning and retained for recovery.
2. **SD bootstrap → NVMe boot files → NVMe root.** Requires proven NVMe support in the selected bootloader.
3. **SD bootloader, kernel, DTB and initramfs → NVMe root.** Acceptable fallback when NVMe is available only after Linux initializes. SD remains unmounted during operation and read-only whenever mounted.

Do not assume generic U-Boot commands, raw flash offsets, or persistent environment layout. Extract them from the pinned vendor sources and validate against serial boot logs. Disable SD environment saves, boot counters, filesystem journal replay, automatic repair, and updater writes. Put mutable boot selection on NVMe where supported. A Linux read-only mount alone does not prevent firmware writes.

Proposed NVMe GPT layout, subject to bootloader filesystem support:

| Area | Initial budget | Purpose |
| --- | --- | --- |
| Boot | 512 MiB | Kernel, DTB, initramfs and recovery selection |
| Root A | 4 GiB | Current host image |
| Root B | 4 GiB | Inactive update image |
| State | 8 GiB | Host configuration, keys and bounded logs |
| Guests | Remaining usable capacity | Guest disks and local snapshots |

Calculate from actual bytes, not a presumed 250 GiB. Use stable PARTUUIDs or filesystem UUIDs. Keep root read-only, `/run` and `/tmp` in bounded tmpfs, and persistent state on NVMe. Disable disk swap initially; evaluate bounded zram after memory measurements. Snapshots are not backups.

SD artifacts must fit the actual card byte count, including bootloader reservations and partition tables. Target an uncompressed installer image below 1.8 GB decimal and fail packaging if it exceeds the device. Stream the NVMe payload over wired networking or read it from USB; never stage the expanded NVMe image on SD or in RAM. The recorded 12 GiB board also permits the selected 545,581,074-byte compressed distribution in private RAM under the deployment guide's 2 GiB tmpfs limit, no-swap check and 3 GiB available-memory admission. Decode directly to NVMe with progress; larger distributions require streamed delivery or identified external storage. No larger temporary SD is available or required by this plan; the first diagnostic image must also fit the available card.

## Agent work packages and dependency gates

Assign independent agents after interfaces are agreed. Each must deliver code, documentation, provenance, and test evidence; an integrating agent owns shared image layout and release decisions.

| Work package | Deliverables | Dependencies and acceptance |
| --- | --- | --- |
| A: Board and boot | Pinned vendor sources, firmware inventory, console capture, boot layout, recovery procedure | Identify accessible board labels and diagnostic SSD identity; demonstrate repeatable NVMe root boot |
| B: Virtualization | Kernel config/patches, guest fixture, KVM probe and launch recipe | A baseline; prove a guest executes with KVM explicitly required |
| C: Image build | Buildroot external tree or interim minimal distro recipe, locked builder, artifact manifest | A and B interfaces; clean build produces boot and root artifacts |
| D: Installer and updates | 2 GB SD installer, NVMe provisioning, dry-run device report, rollback mechanism | A layout and C artifacts; safe target identification and interruption tests |
| E: Operations and verification | Build/install/recovery guides, test matrix, release report | Integrate A–D; independent operator reproduces deployment |

Run A and source-level B investigation in parallel; C can prototype userspace while the hardware gates remain pending. Never declare a release from emulation-only testing. If firmware prevents EL2 access, investigate firmware changes and upstream support before proceeding; do not silently substitute a different virtualization product or claim success.

Proposed repository outputs: `build/` for recipes, `configs/` for board/kernel/rootfs settings, `scripts/` for build and provisioning entrypoints, `tests/` for artifact and hardware checks, `docs/` for procedures, and ignored `out/` for generated images. These are planned paths, not existing commands.

## Build and validation methodology

Pin source commits, firmware hashes, patches, compiler, rootfs packages, and builder image digest in a machine-readable manifest. Record redistribution terms and provide required source and license notices. Separate downloadable caches from output. Document exact commands only after running them successfully from a clean checkout. Produce compressed distribution artifacts, uncompressed sizes, SHA-256 checksums, and a bill of materials. Build twice in clean environments and explain any binary differences before claiming reproducibility.

Kernel review must cover arm64 KVM, interrupt controller support, PCIe/NVMe, board networking, device mapper if used, and guest virtio devices. Ensure root storage drivers are built in or present in initramfs. Check symbol names against the selected kernel version.

On the board, capture these read-only diagnostics where available:

```sh
uname -a
cat /proc/cmdline
lsblk -b -o NAME,SIZE,MODEL,SERIAL,FSTYPE,MOUNTPOINTS
findmnt
ls -l /dev/kvm
dmesg | grep -Ei 'kvm|hyp|el2|nvme|pcie'
```

`/dev/kvm` existence is preliminary evidence only. Boot a pinned AArch64 guest using QEMU with `-machine virt -accel kvm -cpu host`, explicit guest kernel/disk inputs, and recorded guest console output. Reject fallback to TCG. Verify actual CPU execution, guest shutdown, disk persistence, networking, and restart. See [QEMU Arm documentation](https://www.qemu.org/docs/master/system/target-arm.html) and [Linux arm64 KVM documentation](https://www.kernel.org/doc/html/latest/virt/kvm/arm/index.html). The QEMU virtual board is the guest platform, not an emulation test of this physical board.

## Installation and deployment sequence

1. Record diagnostic board/SSD identity, accessible labels, power and cooling where available; do not remove heatsinks for markings. Establish serial access and test recovery before changing SPI. Back up existing flash to another machine and verify the backup can be read; back up disk data unless the operator explicitly authorizes discarding it.
2. Build the selected pinned release; verify checksums and actual SD capacity on the provisioning host. The installer must identify targets by model, serial and size, refuse mounted/in-use targets, display its write plan, and require explicit target selection before erasure.
3. Write only the installer to SD. Boot its read-only filesystem and use RAM for temporary runtime state. Acquire the authenticated NVMe payload over Ethernet or USB and verify it before activation.
4. Provision the NVMe layout, install root and boot artifacts, assign unique identifiers, and write host configuration to the state partition. Never clone pre-generated SSH keys or shared passwords. Configure key-based SSH and restrict management access.
5. Install SPI firmware only after verifying compatibility, backup and recovery. Use the vendor installer as a behavioral reference; its whole-drive partitioning flow must not be applied blindly to the proposed A/B layout.
6. Power down, remove SD for the preferred mode, and cold boot. If unsupported, select a documented SD fallback and verify zero routine SD writes. Confirm root, guest storage, logs and persistent configuration resolve to NVMe.
7. Launch the KVM test guest, then enroll production guests. Start with wired management and an isolated/NAT guest network; introduce a bridge only after testing management connectivity and firewall behavior.

Publish exact device-specific commands in the installer guide after validation. Avoid copying destructive `/dev/nvme0n1` examples into a universal recipe.

## Updates and recovery

Write a new host image to the inactive NVMe root slot, verify it, then switch boot selection atomically where the bootloader permits. Keep each slot's compatible kernel/DTB/initramfs together. Health checks include management reachability, NVMe state mounts and a KVM guest startup. Revert failed updates without touching guest disks. Mutable state migrations must remain backward compatible or include a tested restore path.

Automatic rollback is conditional on proven bootloader support. Otherwise document serial/manual rollback honestly and do not advertise unattended updates. In the SD-kernel fallback, kernel updates need an explicit maintenance operation or replacement recovery card; routine rootfs updates must not rewrite SD. Keep SPI updates separate from routine host upgrades.

Recovery documentation must cover missing NVMe, corrupt root, failed update, forgotten network configuration and interrupted SPI programming. Identify the supported board recovery interface and exact tools from the vendor documentation, then demonstrate recovery on hardware. Store configuration and guest backups off-device and test restoration.

## Release acceptance and documentation

Release only when evidence includes:

The operator caps every planned test longer than eight hours at eight hours
(28,800 seconds), including all former 24-hour tests.
Existing runs already covering at least eight hours qualify from their retained
data without an eight-hour rerun. Preserve historical durations and distinguish
operator termination from guest-generated completion; this changes duration,
not the required hardware observations.

- A fresh build and installation using the actual 2 GB SD and 250 GB NVMe, with no hidden larger-card dependency.
- At least ten successful cold boots and ten warm reboots on the recorded SSD and firmware combination.
- A KVM-only guest completing CPU, disk and network checks; an eight-hour workload run with recorded temperatures, memory use and NVMe errors.
- SD removed in SPI mode, or unchanged full-card hashes across boot/workload/shutdown cycles in fallback mode. Also compare Linux block-write counters and audit firmware writes; hashing alone misses identical-content writes.
- Successful interrupted-update recovery, host rollback, missing-NVMe recovery and backup restoration, tested on disposable data.
- No default shared secrets; guest processes use limited privileges and management services are not exposed unintentionally.

Final documentation must include architecture and boot-chain diagrams, pinned component versions, build commands and host prerequisites, byte-accurate image layouts, installer usage, guest creation and networking, SD write policy, update/rollback procedures, recovery, backup/restore, known SSD limitations, and test logs. Link upstream references beside the relevant instructions. A second agent or operator must follow the guide from a clean environment and report discrepancies.

Use the Ubuntu 22.04 build host at `drew@build.drewnet.online` described in `docs/build-host-assessment.md`. The diagnostic stack and binary inputs are locked, and physical first boot has supplied RAM, SD and NVMe inventory. The reviewed EL2 handoff has now booted physically with KVM VHE initialization. The preparation boot has demonstrated automatic vendor PHY binding, DHCP, routing/DNS and verified HTTPS after manual clock correction; installed DHCP and both SSH keys now pass after repair; the diagnostic host now passes real KVM guest CPU/disk/network, clean shutdown, checkpoint and restart tests; corrected-host validation and repeated-boot management reliability remain open. See [implementation status](implementation-status.md) for the current deployment artifacts and evidence. Continue minimal host and installer work while hardware results are pending. Full clean-build reproducibility and firmware licensing remain open. Implementation completion requires the above hardware results; a diagnostic build does not imply a working hypervisor.
