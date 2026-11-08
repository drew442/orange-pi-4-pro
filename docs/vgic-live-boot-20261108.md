# Physical VGIC diagnostic boot — 2026-11-08

The reviewed 256 MiB VGIC SD image was written to the identified SU02G card
using the [guarded remote procedure](next-host-boot-20261108.md). The operator
[authorized writes to the known SD, NVMe and SPI](evidence/storage-write-authorization-20261108.json).
The single SD write completed and flushed in 34.0951 seconds; optional readback
was omitted. A normal warm reboot then returned trusted SSH at `192.168.1.116`.
The original repaired NVMe installation and its host key remain in use.

The new boot ID is `7f1f07c4-5a4f-469f-97a7-612406d915a2`.
The live maintenance property is `<1 9 4>` and **KVM_CAP_IRQCHIP is now 1**,
compared with 0 before this change. The kernel reports the GIC system-register
CPU interface, `vgic interrupt IRQ9` and successful VHE initialization.
Root A remains read-only on NVMe p2; state remains the original `00fd…` UUID
on p4. Wired DHCP returned on `end0` at 1 Gbps full duplex and the management
SSH service is active. Full kernel/read-only captures and pinned write logs
are indexed in the [evidence record](evidence/vgic-live-boot-20261108.json).
No external serial-console capture was available for this remote warm boot.

The existing first guest disk and enrollment survived the host reboot with
unchanged recorded hashes and counter 1. A current off-device file backup was
verified before p5 was activated read-write. The [follow-up guest tests](kvm-vgic-results-20261108.md) resumed that disk
with the normal host GIC, empty NIC ROM setting and account correction, passing
CPU/disk/network, guest poweroff, checkpoints and restart to counter 3. The prior host-terminated guest stop remains explicitly unclean;
no checkpoint or clean-shutdown claim is inferred from its persistence.

This establishes one physical diagnostic warm boot and the VGIC capability
change. Final-candidate guest/time/identity behavior,
SPI recovery/boot, SD write policy, endurance and reproduced deployment remain
open. Neither the new NVMe candidate nor SPI firmware was installed in this trial.
