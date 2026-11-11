# Selected A733 host architecture

The selected distribution combines pinned minimal Debian arm64 userspace with
the compatible vendor A733 kernel, device tree and firmware. It uses KVM/QEMU
and supervised unprivileged guests. There is no desktop or libvirt dependency.
[Current images](current-images.md) identify exact artifacts;
[implementation status](implementation-status.md) separates physical evidence
from remaining release gates. The final normalized host has booted and passed restored-guest KVM/checkpoint/
restart. Ten final warm and ten physical cold boots and their complete SD
comparisons pass through the [release checklist](release-acceptance.md).

The plan's minimal Debian option is retained for this deployment: its first
normalized-A observation reports **437968896 bytes used** on the 4 GiB root
filesystem, with MemTotal 12161560 KiB and MemAvailable 11798000 KiB before any
guest launch. These are point observations, not a memory benchmark. The pinned
221-package userspace is maintained by building/reviewing an inactive root;
on-board APT mutation is not the update mechanism. Final boot reliability is
still a release gate. The [installation record](evidence/normalized-host-install-20261110.json)
pins the measurements and preserves the compatible board stack.

## Boot chain and recovery

The working fallback loads Linux boot files from the actual 2 GB SD, then uses
NVMe for the selected read-only host root and writable state. Routine operation
does not mount SD. The compatible protected bootstrap has passed physical boot,
recovery and KVM cases. Final normalized boot-cycle acceptance passes.

```mermaid
flowchart TD
  ROM[A733 boot ROM] --> SD[SD boot0 and compatible EL2 firmware package]
  SD --> UB[Vendor U-Boot]
  UB --> FILES[SD kernel, VGIC DTB and protected host initramfs]
  FILES --> SELECT[NVMe STATE: block-RO probe of selected.conf]
  SELECT --> ROOT[Selected NVMe root A or B, mounted read-only]
  ROOT --> HOST[systemd host: RAM configuration and NVMe state]
  SELECT -->|Missing or invalid storage| RESCUE[Serial RAM recovery shell]
  ROM -.->|Preferred, unproven| SPI[SPI firmware with SD removed]
  SPI -.-> NVBOOT[NVMe kernel, DTB and initramfs]
  NVBOOT -.-> SELECT
```

The [protected initramfs](../configs/host-init) resolves STATE by UUID, validates
the selector and matching root marker, and refuses incompatible combinations.
Its initial STATE probe enforces block read-only protection before `ro,noload`
mounting. Normal host handoff subsequently mounts STATE writable, where journal
replay is allowed. These compatibility checks are not a cryptographic secure-boot
chain. The [firmware write audit](sd-firmware-write-policy.md) records opaque and
conditional firmware limits; full-card comparisons and serial observations at
defined boot-series boundaries remain required.

[SPI preparation](known-spi-programmer.md) and the separate
[SD-to-NVMe firmware trial](matched-nvme-trial-sd-20261109.md) are not physical
boot proofs. SPI programming requires independent recovery first. If SPI cannot
be qualified, the SD fallback remains within the plan, with zero routine writes.
Kernel/DTB/initramfs replacement in that fallback is explicit SD maintenance;
ordinary host root updates stay on NVMe.

## Storage and mutable state

The nominal 250 GB drive actually has **256060514304 bytes**. The selected GPT
uses 512-byte logical sectors and these byte offsets and lengths:

| Partition | Offset | Length | Role |
| --- | ---: | ---: | --- |
| p1 BOOT | 1048576 | 536870912 | Prepared matched Linux boot files for NVMe firmware loading |
| p2 ROOT-A | 537919488 | 4294967296 | Immutable host A, selected by factory distribution |
| p3 ROOT-B | 4832886784 | 4294967296 | Compatible inactive host or rollback root |
| p4 STATE | 9127854080 | 8589934592 | Identity, management configuration, selector and persistent host files |
| p5 GUESTS | 17717788672 | 238342373376 | Guest assets, writable disks and checkpoint history |

The front reservation is 1048576 bytes and the tail reservation is 352256 bytes,
including backup GPT space. The SD bootstrap image is 268435456 bytes and fits
the measured **1977614336-byte** SU02G card. The installer checks exact physical
identity and capacity; a partition component is never a whole-drive image.

```mermaid
flowchart LR
  ROOT[Read-only NVMe root] --> ETC[RAM overlay for /etc]
  RAM[Bounded tmpfs /run and /tmp] --> ETC
  STATE[NVMe STATE] --> CONFIG[Persistent configuration and unique host identity]
  STATE --> BIND[Writable /var, /root and /home]
  GUESTS[NVMe GUESTS] --> DISKS[Protected per-guest assets and mutable disks]
  QEMU[Unprivileged supervised QEMU] --> DISKS
  QEMU --> KVM[Physical A733 KVM]
```

The p5 mountpoint `/run/a733-guests` is a RAM directory, but the mounted
filesystem and its contents reside on NVMe. Guest accounts and service units are
recreated explicitly in RAM after reboot; [activation](guest-activation.md) and
[operations](guest-operations.md) describe the steps. No disk swap is configured.
The selected host uses unique same-board identity restoration before its first
boot; factory distribution hashes describe inputs before that operation.

## Management, guests and maintenance

Management uses wired DHCP, key-only SSH and automatic NTP. This installation
accepts both supplied administration keys from any host/subnet, as requested.
Guest QEMU identities have no effective capabilities and use NoNewPrivileges.
The launcher requires `-accel kvm -cpu host` and host GIC support, with no
software-emulation fallback. Actual guest execution is recorded separately from
KVM API probes. See [physical KVM results](kvm-vgic-results-20261108.md).

[Guest networking](guest-networking.md) is restricted user networking, with an
optional explicit loopback TCP forward for the test service. A bridge or
outward NAT policy is not installed. [Checkpoints](guest-checkpoints.md) bind a
stopped, cleanly shut-down guest disk to its next launch; they are not off-device
backups. [Backup/restoration](guest-backups.md) authenticates an off-device copy.

Host updates write the inactive root and explicitly publish a compatible NVMe
selector. [Physical interruption and manual rollback](physical-host-update-rollback-20261110.md)
have passed; unattended automatic rollback is not promised. Preserve compatible
ROOT-A/ROOT-B/STATE together for [host recovery](host-state-backup.md), and retain
original dirty STATE separately before repair. [Offline selection](host-recovery.md)
and [serial access](remote-serial-control.md) cover loss of management.

Upstream interfaces: [Linux arm64 KVM](https://www.kernel.org/doc/html/latest/virt/kvm/arm/index.html),
[QEMU 7.2 Arm virt](https://gitlab.com/qemu-project/qemu/-/blob/v7.2.0/docs/system/arm/virt.rst),
and [Linux overlayfs](https://www.kernel.org/doc/html/latest/filesystems/overlayfs.html).
