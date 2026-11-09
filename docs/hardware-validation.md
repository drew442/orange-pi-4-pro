# Hardware acceptance record

The first [physical diagnostic boot](board-diagnostic-20261104.md) reached the
RAM shell and enumerated NVMe. The [second boot](evidence/board-el2-20261104.json)
confirms all CPUs at EL2 and KVM VHE initialization. A subsequent
[physical VGIC KVM guest](kvm-vgic-results-20261108.md) passes CPU/disk/network,
guest poweroff, checkpoints and restart with counter progression 1 → 2 → 3.
[Installed SSH repair](installed-host-repair-results-20261108.md) also passes.
The [corrected host](evidence/corrected-host-boot-20261108.json) now passes automatic
NTP and [restored KVM boots 3 → 4 → 5](evidence/corrected-host-guest-restore-20261108.json).
Release acceptance remains incomplete. Host-only checks are recorded separately in
[implementation status](implementation-status.md) and the diagnostic guide.
Never infer a passed board test from a build, model review or QEMU emulation.

## Evidence to collect after the diagnostic SD boot

Follow [diagnostic console and inventory](diagnostic-image.md#serial-console-and-first-boot).
Record diagnostic RAM, accessible board labels, SSD model/serial/actual byte count, power/cooling,
SD identity/capacity, image SHA-256, kernel/U-Boot configs, firmware hashes and
full boot console. Leave attached heatsinks in place; chip markings are optional.
Preserve raw logs outside routine agent context (`out/` or
an off-device evidence archive), and add durable summaries/links here.

The minimal diagnostic shell has BusyBox rather than the full host utilities.
Its sysfs block `size` fields count **512-byte units**, even on drives with a
different logical sector size. `stat` fields include sectors written; preserve
the complete line and kernel version. Capture initial and final values after
each workload. Linux counters start after firmware execution and cannot account
for boot0/U-Boot writes.

| Gate | Required observation | Status |
| --- | --- | --- |
| Diagnostic SD | Actual 2 GB card, serial boot through Linux shell | Observed: 1,977,614,336-byte card; RAM shell |
| Inventory | Board/RAM/SSD/power/cooling and exact capacities | RAM/CPU/SD/SSD and 67 W USB-PD/copper heatsink description recorded; operating power/thermal tests pending |
| NVMe | PCIe/NVMe enumeration, errors and stable repeated access | Enumerates/live; repeated access and warning investigation pending |
| KVM | EL2 messages plus guest execution with KVM explicitly required | [Actual nonroot KVM CPU/disk/network, clean poweroff/checkpoints and restart](kvm-vgic-results-20261108.md) pass on the diagnostic VGIC host; corrected-host restoration and KVM restart also pass; full release remains open |
| Ethernet | Vendor binding, DHCP, route/DNS/TLS and repeatable cold boot | Preparation DHCP/DNS/TLS observed; [installed DHCP and both SSH keys pass after repair](installed-host-repair-results-20261108.md); corrected-host automatic NTP passes; repeatable cold boot remains open |
| Installation | Fresh deployment on actual card and nominal 250 GB SSD | [November 7 write/readback and installed root A boot observed](evidence/installed-host-first-boot-20261107.json); [management repair passes](installed-host-repair-results-20261108.md); independent deployment reproduction remains open |
| Boot reliability | 10 cold boots and 10 warm reboots, matched firmware/SSD | Pending |
| Workload | 24 hours: CPU/disk/network, temperatures/memory/NVMe errors | [Fresh protected-host run is active](endurance-run-20261109.md); previous failed run remains separate, full duration pending |
| SD policy | SD removed, or full-card hashes plus write counters/firmware audit | No mounted SD; continuous zero-write samples and [whole-media baseline/prefix match](evidence/protected-sd-baseline-20261109.json) pass; firmware audit and later comparison pending |
| Update/recovery | Interrupted update, rollback, missing NVMe, disposable data | Pending |
| Backup | Off-device backup restored and checked | [Authenticated physical restoration, KVM restart and checkpoints pass](evidence/corrected-host-guest-restore-20261108.json) |
| Security | Unique keys, no shared secrets, limited guest privileges/services | Pending |
| Reproduction | Independent operator follows clean build/deployment guide | Pending |

## KVM gate

The current host has physically demonstrated EL2 and actual guest KVM CPU
execution. Configuration, `/dev/kvm` and capability ioctls alone remain
insufficient evidence. The old installed DT lacked the GIC maintenance interrupt
(`KVM_CAP_IRQCHIP=0`); the [initial partial run](first-kvm-results-20261108.md)
used explicit GICv2. The [VGIC SD now physically boots](vgic-live-boot-20261108.md)
with `KVM_CAP_IRQCHIP=1`, and the [follow-up normal-GIC tests](kvm-vgic-results-20261108.md)
pass through clean restart. Do not run the original first-guest
sequence unchanged against the existing modified fixture state.

The final matched host must run the pinned ARM64 guest with explicit KVM, using
the verified interrupt-controller configuration. The intended command shape is:

```text
qemu-system-aarch64 -machine virt,gic-version=host -accel kvm -cpu host ...
```

The fixture must supply explicit hashed kernel and disk inputs, record guest
console output for CPU/disk/network checks, shut down cleanly, retain a disk
marker and restart. Never add `tcg` or an accelerator fallback. See
[QEMU Arm virtual machines](https://www.qemu.org/docs/master/system/target-arm.html)
and [Linux arm64 KVM](https://www.kernel.org/doc/html/latest/virt/kvm/arm/index.html).
The QEMU `virt` machine is the guest platform; tests must execute through KVM on
the physical A733. It is not a board emulation substitute.

`scripts/launch-kvm-guest.py` prepares this KVM-only command with explicit
kernel/initramfs/disk SHA-256 checks, measured memory/CPU budgets and a non-root
launch requirement. `--dry-run` verifies inputs and prints JSON without requiring
KVM or QEMU; it is preparation only. The guest disk must be a regular NVMe-backed
file, never a physical drive. It is mutable: after a clean shutdown, preserve its
new reviewed checkpoint hash for the next launch. The [base guest fixture](guest-fixture.md) is built; the separately corrected
diagnostic fixture now passes CPU/disk/network and clean restart on the VGIC
host. This does not establish final corrected-host release acceptance. Restricted user-mode networking has no inbound
listeners by default; the [HTTP fixture and explicit loopback profile](guest-networking.md)
define the pending actual guest network test.

The helper selects the host's GIC version for KVM, as supported by
[QEMU 7.2's virt machine](https://gitlab.com/qemu-project/qemu/-/blob/v7.2.0/docs/system/arm/virt.rst).
The guest fixture needs a separate kernel configuration with PL011 console,
generic PCI host, virtio PCI/block/network, initramfs and devtmpfs support.
The pinned A733 host configuration omits these guest drivers. QEMU supplies the
virtual machine's DTB; the board DTB belongs to the physical host boot chain.

## SD write checks

Before and after a defined boot/workload/shutdown cycle, take the complete card
offline on the provisioning computer, unmount all partitions and read a full-card
SHA-256 using its authorized exact device path. Preserve the byte count and hash
together. Compare Linux write counters and source/serial firmware behavior too:
an identical-content write can leave the hash unchanged. Do not run a filesystem
repair or auto-mount the card read-write as part of measurement.

The first diagnostic candidate configures no disk mounts and no persistent
environment, but opaque firmware still needs observation. Record image-region
readback separately from full-card hashing: the image is 256 MiB and may be
smaller than the actual card, whereas this gate covers every card byte.

## Storage authorization

`scripts/plan-nvme.py --target /dev/disk/by-id/EXACT_NVME` produces a read-only
identity and layout report on a Linux environment with Python and util-linux.
It uses actual bytes, requires model/serial and rejects known busy indicators.
The diagnostic BusyBox image does not yet include Python. A plan is neither a
lock nor authorization to install; raw-device users and other mount namespaces
remain limitations. Revalidate just before any future installer action.

Before erasing physical storage or programming SPI, identify the exact device,
publish its concrete write plan, preserve/read-check required backups and recovery
inputs, and obtain explicit authorization. The user has authorized discarding the
NVMe with serial `134807190FC700149277`, model `SPCC M.2 PCIe SSD`, size
256,060,514,304 bytes; do not request that permission again if identity matches.
Revalidate identity and idle state immediately before any future write. The
[November 7 operator log](evidence/installed-host-first-boot-20261107.json) records
the authorized NVMe installation and readback. SPI remains untouched until its
actual chip, backup and recovery route are verified. Automatic rollback is not established; do not promise it before
the proven boot architecture supports it.
