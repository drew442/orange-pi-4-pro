# Hardware acceptance record

The first [physical diagnostic boot](board-diagnostic-20261104.md) reached the
RAM shell and enumerated NVMe; **EL1 handoff blocks KVM**. Release gates remain
open. Host-only checks are recorded separately in
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
| Inventory | Board/RAM/SSD/power/cooling and exact capacities | RAM/CPU/SD/SSD recorded; power/cooling operating tests pending |
| NVMe | PCIe/NVMe enumeration, errors and stable repeated access | Enumerates/live; repeated access and warning investigation pending |
| KVM | EL2 messages plus guest execution with KVM explicitly required | Failed prerequisite: EL1; HYP unavailable; no guest execution |
| Installation | Fresh deployment on actual card and nominal 250 GB SSD | Pending |
| Boot reliability | 10 cold boots and 10 warm reboots, matched firmware/SSD | Pending |
| Workload | 24 hours: CPU/disk/network, temperatures/memory/NVMe errors | Pending |
| SD policy | SD removed, or full-card hashes plus write counters/firmware audit | No mounted SD; one zero-write snapshot; full gate pending |
| Update/recovery | Interrupted update, rollback, missing NVMe, disposable data | Pending |
| Backup | Off-device backup restored and checked | Pending |
| Security | Unique keys, no shared secrets, limited guest privileges/services | Pending |
| Reproduction | Independent operator follows clean build/deployment guide | Pending |

## KVM gate

The kernel configuration enables arm64 KVM, but firmware handoff may still
prevent EL2 use. `/dev/kvm` and successful capability ioctls alone do not prove
guest CPU execution. The pinned ARM64 guest fixture is prepared and must boot using:

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
new reviewed checkpoint hash for the next launch. A [guest fixture](guest-fixture.md)
is built but has not booted. Restricted user-mode networking has no inbound
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
Revalidate identity and idle state immediately before writing. No such device
operation has been performed. SPI remains untouched until its actual chip, backup and recovery route
are verified. Automatic rollback is not established; do not promise it before
the proven boot architecture supports it.
