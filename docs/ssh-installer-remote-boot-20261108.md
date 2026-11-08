# Physical SSH RAM installer boot

The approved SD maintenance write and warm boot pass. The board currently runs
from RAM at **192.168.1.115**. NVMe has no mounted filesystems or swap/holders;
the packaged read-only plan identifies SPCC M.2 PCIe SSD, serial
**134807190FC700149277**, **256060514304 bytes**, 512/512-byte sectors.
This observation is not a write lock; installer apply must revalidate it.

The installed-host address was 192.168.1.116. The RAM DHCP client acquired .115;
ARP responses were filtered to the recorded MAC `10:f4:ae:90:5e:57` before
connecting. Strict host verification reused the existing trusted identity:

```sh
ssh -i ~/.ssh/id_ed25519 -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=yes -o HostKeyAlias=192.168.1.116 root@192.168.1.115
```

The Ed25519 operator login and physical PTY pass. Both authorized public keys
match the reviewed artifact. The RSA private key is not available to the
coordinator, so RSA login has not been repeated on this boot. Host fingerprint:
`SHA256:Epfegy9GsJ6ng4/0EH3dZmir7beQj5uYTnIbE4JPfEI`.

[Evidence](evidence/ssh-ram-installer-physical-20261108.json) records the exact
write guard, independent artifact/write reviews, 78 guard checks and physical
logs. The first 268435456 bytes of `/dev/mmcblk1`, SU02G CID
`035344535530324780716f216e00a8f5`, were written with progress and fsync in
**34.2736 seconds**. No SD readback was requested. The normal reboot changed boot
ID to `f3a37e96-ad2f-4eb5-93a3-193d41083ce3`.

Root is writable `rootfs` in RAM; only proc, sysfs, devtmpfs, bounded run/tmp
tmpfs and devpts are mounted. Link is 1 Gbit/s full duplex. Kernel
`6.6.98-a733-diag` reports all eight CPUs at EL2, VGIC IRQ9/VHE, KVM API12,
IRQCHIP1 and ARM_USER_IRQ1. No guest was launched in this installer. Actual guest
execution belongs to the [earlier diagnostic host tests](kvm-vgic-results-20261108.md).

Linux SD and NVMe write counters are zero in the early sample. This is not the
release full-card-hash/firmware-audit/endurance proof. No external serial capture
was made for this remote warm boot; the retained kernel log was collected over
SSH. RAM initially showed January 1970; later UTC correction was manual and does
not prove final-host NTP.

## Recovery artifact

The private recovery gzip is now retained on codex:

`/home/drew/orange-pi-4-pro/out/ssh-ram-installer-20261108/a733-ssh-installer.img.gz`

It is **41229263 bytes**, SHA-256
`2f9cdd006e6d5c2c59e5ed1aba68f3ed7dd267a7f61b3672e590d926468c4eec`.
Its expanded image is **268435456 bytes**, SHA-256
`d3a02224efdb2c2eeccc7115d84cd6c4b2d75c7d06580b8f7812d12a546bc3f6`.
Builder also retains the raw/gzip under
`/var/lib/a733-ssh-sd-project-20261108-01/project/out/ssh-installer-sd-personalized-01`.
This image contains the existing board's private SSH host key. Keep it private
and use it only for this board; it must never enter Git or public distribution.

For physical recovery, identify and unmount the same whole SD on the flashing
computer, using the [existing guarded flashing procedure](installer-refresh-20261107.md#image-and-flashing).
Use the private image above, expanding it directly into the confirmed target
instead of staging a raw native image; preserve pipeline failure checking and
write progress. Source gzip authentication is required; target readback remains
optional troubleshooting. The image fits the actual 1977614336-byte card.
Power off before card removal/insertion. Serial remains 3.3 V TTL, 115200 baud, 8N1,
no flow control, adapter VCC disconnected; leave heatsinks attached.

The old `0bb7…` VGIC host SD is only recovery for the original `00fd…` state.
After the corrected `360d…` NVMe installation, recover with this RAM installer
or the matched corrected host bootstrap. Never use an old-state host bootstrap
as evidence that the new state failed.

## Next deployment steps

Complete independent full NVMe artifact review before its approved installation.
Retain the [counter3 guest backup](evidence/vgic-counter3-backup-20261108.json).
The reviewed [identity helper](known-host-state-personalization.md) preserves
only this same board's identity after installation. Then use the reviewed
[known SD writer](known-bootstrap-sd-writer.md) for the matching host bootstrap.
Final corrected221-package host boot, automatic time, restored KVM guest,
update/rollback/recovery, boot cycles, endurance and independent deployment
reproduction remain required. SPI is still unprogrammed and requires its
independent recovery prerequisites.
