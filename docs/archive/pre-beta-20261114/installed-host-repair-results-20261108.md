> **Historical record — not active instructions or release gates.**
> Superseded by the [manual beta scope](../../beta-scope.md) and
> [current status](../../implementation-status.md). Original observations retain
> their recorded configuration and limitations.

# Installed host repair results (2026-11-08)

The existing NVMe host booted and management recovery succeeded after the
state partition's `identity/machine-id` was changed from mode `0600` to `0644`.
The repair preserved the file contents and did not reinstall NVMe. The repair
block reports the guarded target as the SPCC M.2 PCIe SSD, serial
`134807190FC700149277`, with only the machine-id permission changed
([serial transcript](../../../tmp/08112026-1052-host-repair-and-ssh-test.txt#L1266)).

The installed Debian 12 host booted with kernel `6.6.98-a733-diag`. It mounted
the root from NVMe partition 2 read-only and state from NVMe partition 4
read-write; see the [serial transcript](../../../tmp/08112026-1052-host-repair-and-ssh-test.txt#L2393)
and [captured evidence](../../evidence/installed-host-repair-results-20261108.json).
The live capture identifies `end0` as MAC `10:f4:ae:90:5e:57`, DHCP address
`192.168.1.116/24`, gateway `192.168.1.1`, and the same NVMe partitions
([live capture](../../../out/host-repair-20261108/live-read-only.log#L1)).

The host advertised SSH fingerprint `SHA256:Epfegy9GsJ6ng4/0EH3dZmir7beQj5uYTnIbE4JPfEI`.
The server accepted both configured keys: RSA
`SHA256:WVU8wsBgfy5mGFNAZMdrvf+a3cTYo85B8llJlHIhUKA` and Ed25519
`SHA256:Dw9WPpqQ741skiDShdwAP3qXQtpgxgbUSlCp2hmod7k`
([acceptance records](../../../tmp/08112026-1052-host-repair-and-ssh-test.txt#L2611)).

## Remaining gates

- The captured host clock says April 27; this remains a timekeeping defect.
- `systemd-machine-id-commit.service` still fails because `/etc/machine-id` is
  not on a temporary filesystem ([live capture](../../../out/host-repair-20261108/live-read-only.log#L31)).
- NVMe state is persistent ext4 mounted across `/etc/machine-id`, `/home`,
  `/root`, `/var`, and `/run/a733-state`; review the bind/mount arrangement.
- A subsequent [actual KVM guest](../../first-kvm-results-20261108.md) passed CPU and
  disk tests. Full network, guest clean shutdown and restart acceptance remain
  open; kernel KVM initialization alone does not satisfy those gates.
- No SPI programming or zero-routine-SD-write acceptance is established here.

The persistent machine-id bind is intentional. A future image should suppress
the inapplicable transient-ID commit unit; do not unmount or replace the live
identity to make that unit pass. Time-enabled userspace is already prepared
separately, but has not booted here.

The first guest attempt also exposed an [OverlayFS account-controller
compatibility defect](../../evidence/account-overlay-correction-20261108.json).
The reviewed correction passed 1,686 privileged tests and a real OverlayFS
fixture. A closed original-profile controller tree in RAM then created the
locked guest account at UID/GID 60001. The installed root remains unchanged;
this correction still needs packaging into a future image.

The [guest fixture and PXE-ROM corrections](../../evidence/guest-compatibility-fixes-20261108.json)
also pass independent review and 1,708 privileged tests. Next, boot the
[VGIC diagnostic SD](../../next-host-boot-20261108.md), complete the guest checks and
separately validate SPI boot or the read-only SD fallback before claiming
storage/boot acceptance.
