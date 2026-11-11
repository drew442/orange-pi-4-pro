# Hardware acceptance record

The first [physical diagnostic boot](board-diagnostic-20261104.md) reached the
RAM shell and enumerated NVMe. The [second boot](evidence/board-el2-20261104.json)
confirms all CPUs at EL2 and KVM VHE initialization. A subsequent
[physical VGIC KVM guest](kvm-vgic-results-20261108.md) passes CPU/disk/network,
guest poweroff, checkpoints and restart with counter progression 1 → 2 → 3.
[Installed SSH repair](installed-host-repair-results-20261108.md) also passes.
The [corrected host](evidence/corrected-host-boot-20261108.json) now passes automatic
NTP and [restored KVM boots 3 → 4 → 5](evidence/corrected-host-guest-restore-20261108.json).
The [physical changed-B update and old-A rollback](physical-host-update-rollback-20261110.md)
also pass with actual KVM counters 7/8 and retained clean exits.
[Fresh normalized deployment and independent reproduction](independent-deployment-20261110.md)
also pass with restored KVM 5→6→7. Ten [warm reboots](warm-boot-validation.md)
and their complete SD comparison pass; ten [cold boots](cold-boot-validation.md)
and the final SD comparison now pass. See the [release checklist](release-acceptance.md).
Host-only checks are recorded separately in
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
| Inventory | Board/RAM/SSD/power/cooling and exact capacities | RAM/CPU/SD/SSD and 67 W USB-PD/copper heatsink description recorded; qualifying workload thermal observations retained; negotiated operating power is unmeasured |
| NVMe | PCIe/NVMe enumeration, errors and stable repeated access | Enumeration, qualifying workload and physical update/restore readbacks pass; admin invalid-field/power-cycle correlation is recorded with origin unproved |
| KVM | EL2 messages plus guest execution with KVM explicitly required | [Actual nonroot KVM CPU/disk/network, clean poweroff/checkpoints and restart](kvm-vgic-results-20261108.md) pass on the diagnostic VGIC host; [fresh normalized-host restoration and KVM restart](evidence/normalized-guest-restore-20261110.json) also pass; [release acceptance](release-acceptance.md) passes for the recorded SD fallback |
| Ethernet | Vendor binding, DHCP, route/DNS/TLS and repeatable cold boot | Preparation DHCP/DNS/TLS observed; [installed DHCP and both SSH keys pass after repair](installed-host-repair-results-20261108.md); corrected-host automatic NTP passes; [ten final cold boots with DHCP/NTP readiness](evidence/final-cold-boots-20261111.json) pass |
| Installation | Fresh deployment on actual card and nominal 250 GB SSD | [Fresh normalized full installation](evidence/normalized-host-install-20261110.json) passes with streamed source hash, exit 0 and no optional readback; [independent physical deployment reproduction](independent-deployment-20261110.md) passes |
| Boot reliability | 10 cold boots and 10 warm reboots, matched firmware/SSD | [Ten warm boots pass](evidence/final-warm-boots-20261110.json); [ten physical cold boots pass](evidence/final-cold-boots-20261111.json) |
| Workload | Eight hours: CPU/disk/network, temperatures/memory/NVMe errors | [Legacy run exceeded 20 hours](endurance-run-20261109.md) and was operator-stopped; [data/health/SD checks pass](evidence/eight-hour-endurance-result-20261109.json), no eight-hour rerun required; previous failed run remains separate |
| SD policy | SD removed, or full-card hashes plus write counters/firmware audit | No mounted SD; continuous zero-write samples and [whole-media baseline/prefix match](evidence/protected-sd-baseline-20261109.json) pass; [bounded firmware audit](sd-firmware-write-policy.md) complete; [warm shutdown/reboot comparison passes](evidence/final-warm-boots-20261110.json); [final cold-series comparison passes](evidence/final-cold-boots-20261111.json) |
| Update/recovery | Interrupted update, rollback, missing NVMe, disposable data | [Changed-B interruption/retry, publication interruptions/cancellation and healthy old-A rollback pass](physical-host-update-rollback-20261110.md). [Controlled SSH/offline-root recovery](controlled-ssh-recovery-20261110.md), [NVMe-driver fault recovery](missing-nvme-recovery-test.md) and [actual dirty-STATE crash/export/replay](dirty-state-recovery-trial.md) pass with subsequent KVM execution; physical SSD removal and power cuts are not claimed |
| Backup | Off-device guest/configuration backup restored and checked | [Guest restoration, KVM restart and checkpoints pass](evidence/corrected-host-guest-restore-20261108.json); [paired host restoration, interrupted retry, restored configuration and actual KVM pass](paired-host-restore-20261110.md) |
| Security | Unique keys, no shared secrets, limited guest privileges/services | [Original identity/configuration restored](evidence/normalized-host-install-20261110.json), root password locked, both supplied keys retained; [real KVM UID/GID/capability/NoNewPrivileges checks](evidence/normalized-guest-restore-20261110.json) pass; resolver policy passes on final A/B |
| Reproduction | Independent operator follows clean build/deployment guide | [Physical deployment from clean RAM environment passes](independent-deployment-20261110.md); [build repetition has the documented fixed-input scope](build-reproduction-status.md) |

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
define the loopback network profile used in the linked physical guest checks.

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

The [current firmware source audit](sd-firmware-write-policy.md) binds the
protected SD firmware and selected startup path. It identifies disabled ordinary
write paths and remaining conditional/opaque behavior; it does not establish
hardware acceptance. Opaque firmware still needs the planned observations. Record image-region
readback separately from full-card hashing: the image is 256 MiB and may be
smaller than the actual card, whereas this gate covers every card byte.

## Storage authorization

`scripts/plan-nvme.py --target /dev/disk/by-id/EXACT_NVME` produces a read-only
identity and layout report on a Linux environment with Python and util-linux.
It uses actual bytes, requires model/serial and rejects known busy indicators.
The original minimal diagnostic BusyBox image lacks Python; the current
[private SSH RAM recovery image](current-images.md) supplies the installer runtime.
A plan is neither a
lock nor authorization to install; raw-device users and other mount namespaces
remain limitations. Revalidate just before any future installer action.

Before erasing physical storage or programming SPI, identify the exact device,
publish its concrete write plan, preserve/read-check required backups and recovery
inputs, and revalidate identity and idle state. The operator's [standing storage
authorization](evidence/storage-write-authorization-20261108.json) covers the known
board's SD, NVMe and SPI; do not ask again within that scope. Request a simple
yes/no only for a new target or unapproved scope. Known NVMe is serial
`134807190FC700149277`, model `SPCC M.2 PCIe SSD`, 256,060,514,304 bytes;
known SD is the SU02G with CID `035344535530324780716f216e00a8f5`.
The [November 7 operator log](evidence/installed-host-first-boot-20261107.json)
records an earlier authorized NVMe installation and readback. Normal installation
readback is optional and transfer/write progress must remain visible.
SPI remains unprogrammed until its exact chip, backup and independent recovery
route are verified. Persisted write authority does not supply recovery evidence.
Automatic rollback is not established; do not promise it before the proven boot
architecture supports it.
